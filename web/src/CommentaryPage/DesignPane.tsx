import React from "react";
import {flushSync} from "react-dom";
import {
    Box,
    Button,
    Menu,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BACKEND_PORT, BASE_URL} from "../env";
import {getWithRetry} from "../PanelShell/requests";
import {copyText} from "../PanelShell/clipboard";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import socketConnection from "../websocketConnection";
import {LayoutEntry, OBS_HOST, UnlistedFolder} from "./SideCards";
import {
    EMPTY_REPORT,
    LayoutCheckPanel,
    LayoutReport,
    READY_TIMEOUT_MS,
    UnlistedDesigns,
    addMessage,
    parseLayoutMessage,
    parseRenderedMessage,
} from "./LayoutCheck";

// The Commentary page's right pane (ADR-005 S4b): the design select and the
// overlay itself as the preview -- /layout/<design>/<entry>, the page OBS
// shows, so the preview is what goes on air. Scaled down from 1920x1080;
// it does not take clicks. (The Qt preview's copy / save-as-image buttons
// are not here: dropped for the Web version, owner's call 2026-10-31.)
// The Top 8 page's too (S4i), with its own heading and empty text. The Board
// page's too (S4s), with what only it has: controls above the preview, a picture
// behind the overlay, a second overlay page on top, a way to load them again and
// a preview that also keeps to the height left for it.
//
// The frames load from the server's preview listener (ADR-005 S-final SF-8a):
// this host at the port /api/v1/health names, another origin than this page's,
// where the server serves only what a layout reads and refuses every write, token
// or not. A layout is anyone's script (a downloaded one too): there it can read
// the state as OBS lets it, but not write through this page's origin nor reach
// into this page. Sandboxed, but with its own (real) origin kept: an opaque one
// broke the layouts (S4af: cssRules, toDataURL and localStorage throw). Without
// a preview port there is no preview -- never one on this page's origin.
//
// What the origin does not stop is a frame taking the keyboard focus: the keys
// typed then go to the layout. A frame found holding the focus -- as this window
// loses it, or on a poll for when the window is not the active one -- is taken
// out with a note, until another design (or a reload) is shown.
//
// The check of a layout (ADR-008 D3 / L3): a layout on autona.js posts its
// errors and "autona:ready" to the page around its frame. The pane takes a
// `message` only from its own frames (the event's source is one of their
// windows: the design's, and on the Board the popup's) and from the preview
// origin, of the one shape autona.js sends (LayoutCheck.parseLayoutMessage),
// and it moves only the check panel: the errors of the document shown, as text,
// emptied when another document is shown (a design, a reload, the sample). A
// layout expected to use autona.js -- its settings.json declares the contract,
// or it said it was ready before in this page -- that is not ready within
// READY_TIMEOUT_MS is told so; one that never said anything (the shipped
// layouts on globals.js) shows no panel unless it posts an error. Around the
// preview: the folders the catalog could not list, the sample data for a layout
// on autona.js (?autonaSample=), a reload and, on the Board, its OBS URL.
//
// The Thumbnail page's live preview (ADR-008 D7 R2): a template on template
// contract 2 framed the same way, at its own size, with no select. The pane
// is given the payload (`renderPayload`, the server's: GET
// /api/v1/thumbnail/payload) and posts it to the design's frame -- exactly
// {type: "autona:render", serial, payload}, to the preview origin -- once the
// frame said it is ready, and again for every new serial; autona.js draws it
// in the template's Autona.onRender and posts what went wrong as above. Each
// new render's errors replace the last one's. The frame's URL names this
// page's origin (?autonaParent=): autona.js takes a render from that origin
// only. "No errors" waits for the frame to say it drew the render posted
// ({type: "autona:rendered", contract: 2, serial, ok}); until then it is
// drawing.

export const HEALTH_PATH = "/api/v1/health";
export const PREVIEW_SANDBOX = "allow-scripts allow-same-origin";
export const QUARANTINE_POLL_MS = 250;
// layout/include/sample/<name>.json: what ?autonaSample= draws (ADR-008 D1).
export const SAMPLE_NAMES = ["singles", "teams", "empty"] as const;

const OVERLAY_WIDTH = 1920;
const OVERLAY_HEIGHT = 1080;
// The preview's border, on both sides: what `fit` leaves out of what it measures.
const FRAME_BORDERS = 2;

function layoutPath(design: LayoutEntry): string {
    // A template package's folder is not its id (a shipped one's).
    const id = (design.folder ?? design.id).split("/")
        .map(encodeURIComponent).join("/");
    return `/layout/${id}/${encodeURIComponent(design.entry)}`;
}

// The overlay's http URL for an OBS browser source on this PC: the board's
// (?scoreboardNumber=) when one is given. Over http, not file://: a layout on
// autona.js shows its images and samples only so (ADR-008 D1).
export function overlayObsUrl(design: LayoutEntry, board?: number): string {
    const query = board ? `?scoreboardNumber=${board}` : "";
    return `http://${OBS_HOST}:${BACKEND_PORT}${layoutPath(design)}${query}`;
}

// What the select picks a design by: `id`, unless the entry has its own key.
function selectKey(design: LayoutEntry): string {
    return design.key ?? design.id;
}

function usePreviewScale(
    frame: React.RefObject<HTMLDivElement | null>,
    shown: boolean,
    fit: boolean,
    width: number,
    height: number,
): number {
    const [scale, setScale] = React.useState(0.25);
    React.useLayoutEffect(() => {
        const element = frame.current;
        if (!element) {
            return undefined;
        }
        const measure = () => {
            if (element.clientWidth > 0) {
                setScale(fit
                    ? Math.min(
                        (element.clientWidth - FRAME_BORDERS) / width,
                        (element.clientHeight - FRAME_BORDERS) / height)
                    : element.clientWidth / width);
            }
        };
        measure();
        if (typeof ResizeObserver === "undefined") {
            return undefined;
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [frame, shown, fit, width, height]);
    return scale;
}

// The preview listener's origin for /api/v1/health's `previewPort`, or null: a
// port that is not one, or this server's own (no preview on this origin).
function previewOrigin(port: unknown): string | null {
    if (typeof port !== "number" || !Number.isInteger(port)
        || port < 1 || port > 65535 || String(port) === String(BACKEND_PORT)) {
        return null;
    }
    return `http://${window.location.hostname}:${port}`;
}

// undefined while the server is first asked, then the origin or null. Asked
// again on every (re)connect of the page's Socket.IO: a restarted app listens
// for the previews on another port, and a health that failed is tried again.
// Only the latest answer counts.
function usePreviewOrigin(): string | null | undefined {
    const [origin, setOrigin] = React.useState<string | null | undefined>(
        undefined);
    React.useEffect(() => {
        let mounted = true;
        let asked = 0;
        const ask = () => {
            const request = ++asked;
            const current = () => mounted && request === asked;
            getWithRetry<{data?: {previewPort?: unknown}}>(
                HEALTH_PATH, current).then(
                (body) => {
                    if (current()) {
                        setOrigin(previewOrigin(body?.data?.previewPort));
                    }
                },
                (error) => {
                    if (current()) {
                        console.error(error);
                        setOrigin(null);
                    }
                });
        };
        const socket = socketConnection.instance();
        socket.on("connect", ask);
        // The socket may have connected before this mounted: ask now too.
        ask();
        return () => {
            mounted = false;
            socket.off("connect", ask);
        };
    }, []);
    return origin;
}

// Whether the frames of `shown` (a design and its reload) were taken out:
// watched while they are shown, by the window's blur and a poll. Until
// another document is shown: coming back to the one taken out (A, B, A)
// shows it again. No document for a while (the preview port lost and found
// again) is not another one.
function useQuarantine(
    container: React.RefObject<HTMLDivElement | null>,
    shown: string | null,
): boolean {
    const [quarantined, setQuarantined] = React.useState<string | null>(null);
    const taken = shown !== null && quarantined === shown;
    React.useEffect(() => {
        if (shown !== null) {
            setQuarantined((out) => out === shown ? out : null);
        }
    }, [shown]);
    React.useEffect(() => {
        if (shown === null || taken) {
            return undefined;
        }
        // What had the focus here last: given it back after.
        const focused = document.activeElement;
        let last = focused instanceof HTMLElement
            && !(focused instanceof HTMLIFrameElement) ? focused : null;
        const onFocusIn = (event: FocusEvent) => {
            if (event.target instanceof HTMLElement
                && !(event.target instanceof HTMLIFrameElement)) {
                last = event.target;
            }
        };
        const check = () => {
            const holder = document.activeElement;
            if (!(holder instanceof HTMLIFrameElement)
                || !container.current?.contains(holder)) {
                return;
            }
            // Out now, not at React's next render: every key until then is
            // the layout's.
            flushSync(() => setQuarantined(shown));
            window.focus();
            if (last?.isConnected) {
                last.focus();
            }
        };
        window.addEventListener("blur", check);
        document.addEventListener("focusin", onFocusIn);
        const poll = window.setInterval(check, QUARANTINE_POLL_MS);
        return () => {
            window.removeEventListener("blur", check);
            document.removeEventListener("focusin", onFocusIn);
            window.clearInterval(poll);
        };
    }, [container, shown, taken]);
    return taken;
}

// The overlay pages lie on one another: one 1920x1080 frame each (a
// template's at its size), scaled.
function frameSx(scale: number, width: number, height: number) {
    return {
        position: "absolute",
        top: 0,
        left: 0,
        width,
        height,
        border: 0,
        transform: `scale(${scale})`,
        transformOrigin: "0 0",
        pointerEvents: "none",
    };
}

type KnownSetter = React.Dispatch<React.SetStateAction<ReadonlySet<string>>>;

// What the pane's frames told it, for the document shown (LayoutCheck), and
// how to start a new render's errors afresh (its readiness kept). A design
// that says it is ready is added to the ones on autona.js (`setKnown`).
function useLayoutCheck(
    origin: string | null | undefined,
    documentKey: string | null,
    designKey: string | null,
    frames: {
        design: React.RefObject<HTMLIFrameElement | null>;
        overlay: React.RefObject<HTMLIFrameElement | null>;
    },
    setKnown: KnownSetter,
): [LayoutReport, () => void] {
    const [state, setState] = React.useState<{
        key: string | null; report: LayoutReport;
    }>({key: null, report: EMPTY_REPORT});
    React.useEffect(() => {
        if (!origin || documentKey === null) {
            return undefined;
        }
        const key = documentKey;
        // A document shown afresh starts with nothing (its key is its own).
        setState({key, report: EMPTY_REPORT});
        const onMessage = (event: MessageEvent) => {
            if (event.origin !== origin || event.source === null) {
                return;
            }
            const designWindow = frames.design.current?.contentWindow ?? null;
            const overlayWindow = frames.overlay.current?.contentWindow ?? null;
            let frame: string;
            if (designWindow !== null && event.source === designWindow) {
                frame = "design";
            } else if (overlayWindow !== null && event.source === overlayWindow) {
                frame = "overlay";
            } else {
                return;
            }
            const message = parseLayoutMessage(event.data);
            if (message === null) {
                // A template's frame said it drew a render (ADR-008 D7 R2).
                const drawn = frame === "design"
                    ? parseRenderedMessage(event.data) : null;
                if (drawn !== null) {
                    setState((current) => {
                        const before = current.key === key
                            ? current.report : EMPTY_REPORT;
                        return before.rendered !== null
                            && before.rendered >= drawn.serial
                            ? current
                            : {key, report: {...before,
                                             rendered: drawn.serial}};
                    });
                }
                return;
            }
            // The popup's "ready" says nothing about the design.
            if (message.type === "autona:ready" && frame !== "design") {
                return;
            }
            setState((current) => {
                const before = current.key === key
                    ? current.report : EMPTY_REPORT;
                const after = addMessage(before, message, frame);
                // Past the caps nothing changes: no render either.
                return after === before && current.key === key
                    ? current : {key, report: after};
            });
            if (message.type === "autona:ready" && designKey !== null) {
                setKnown((known) => known.has(designKey)
                    ? known : new Set(known).add(designKey));
            }
        };
        window.addEventListener("message", onMessage);
        return () => window.removeEventListener("message", onMessage);
    }, [origin, documentKey, designKey, frames, setKnown]);
    const restart = React.useCallback(() => setState((current) =>
        current.key === documentKey
            ? {key: current.key, report: {
                ...EMPTY_REPORT, ready: current.report.ready,
                contract: current.report.contract,
                rendered: current.report.rendered}}
            : current), [documentKey]);
    return [state.key === documentKey ? state.report : EMPTY_REPORT, restart];
}

// Whether `documentKey`, expected to say it is ready, has been shown for
// READY_TIMEOUT_MS without saying so.
function useReadyTimeout(
    documentKey: string | null, watched: boolean, ready: boolean,
): boolean {
    const [late, setLate] = React.useState<string | null>(null);
    React.useEffect(() => {
        if (!watched || documentKey === null) {
            return undefined;
        }
        const key = documentKey;
        setLate(null);
        const timer = window.setTimeout(() => setLate(key), READY_TIMEOUT_MS);
        return () => window.clearTimeout(timer);
    }, [documentKey, watched]);
    return watched && !ready && late === documentKey;
}

// `documentKey` and how many times it has been shown afresh: one key per
// document the frame loads, so coming back to a design (A, B, A) or a sample
// (live, teams, live) is never taken for the one shown before.
function useShownDocument(documentKey: string | null): string | null {
    const [shown, setShown] = React.useState({key: documentKey, serial: 0});
    let current = shown;
    if (shown.key !== documentKey) {
        current = {key: documentKey, serial: shown.serial + 1};
        setShown(current);
    }
    return documentKey === null ? null : `${documentKey}@${current.serial}`;
}

// "Copy the OBS URL": the design's, or one board's of `boards` (a menu).
function ObsUrlButton({design, boards}: {
    design: LayoutEntry;
    boards: number[];
}) {
    const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
    const label = i18n.t("layout_obs_url_copy");
    // In the click itself: the clipboard takes a write only while the click's
    // activation lasts.
    const copy = (board?: number) => {
        setAnchor(null);
        copyText(overlayObsUrl(design, board)).then((copied) => showSnackbar(
            i18n.t(copied ? "layout_obs_url_copied"
                          : "commentary_obs_copy_failed"),
            copied ? "success" : "error"));
    };
    if (boards.length === 0) {
        return (
            <Button variant="outlined" size="small" onClick={() => copy()}>
                {label}
            </Button>
        );
    }
    return (
        <>
            <Button variant="outlined" size="small" aria-haspopup="menu"
                    onClick={(event) => setAnchor(event.currentTarget)}>
                {label}
            </Button>
            <Menu anchorEl={anchor} open={anchor !== null}
                  onClose={() => setAnchor(null)}>
                {boards.map((board) => (
                    <MenuItem key={board} onClick={() => copy(board)}>
                        {i18n.t("layout_obs_url_board", {board})}
                    </MenuItem>
                ))}
            </Menu>
        </>
    );
}

export interface RenderPayload {
    // What autona.js hands the template's Autona.onRender.
    payload: unknown;
    // Above the one before it: a new render.
    serial: number;
}

export default function DesignPane({
    designs, design, onSelect, label, empty, actions, controls, background,
    overlay, reloadKey, fit = false, unlisted, onReload, obsBoards,
    renderPayload, size, selectable = true, frameMaxWidth,
}: {
    designs: LayoutEntry[];
    design: LayoutEntry | null;
    // Called with the design's select key (its `key`, else its `id`).
    onSelect: (key: string) => void;
    // The heading (and the select's name), and what shows without a
    // design: the Commentary page's by default.
    label?: string;
    empty?: string;
    // Right under the select: the layout kit's buttons (ADR-008 L2) on the
    // pages that make designs.
    actions?: React.ReactNode;
    // Between the select and the preview.
    controls?: React.ReactNode;
    // A picture of the server's behind the design (a path): what shows through
    // the overlay's transparent page.
    background?: string;
    // A page of the server's on top of the design (a path), with its frame's
    // name. Its fragment changing is no new document.
    overlay?: {src: string; title: string} | null;
    // A new value loads the frames again: a page the server has changed.
    reloadKey?: number;
    // Scale to the smaller of the width and the height the pane has left (the
    // Qt preview's rule), rather than to the width. For a pane in a column of a
    // set height, from the md breakpoint up; below it the preview is by width.
    fit?: boolean;
    // The folders of the page's kinds the catalog could not list (GET
    // /api/v1/layouts' "unlisted"): shown under the select.
    unlisted?: UnlistedFolder[];
    // Given: a reload button, which calls it (the page reads the catalog
    // again) and loads the frames again.
    onReload?: () => void;
    // Given: a button that copies the design's http URL for OBS -- one
    // board's of these (a menu), or the design's alone when empty.
    obsBoards?: number[];
    // Given (null while there is none yet): the design is a template on
    // template contract 2, drawn with this payload (see the top). No sample
    // select; the design is expected to say it is ready.
    renderPayload?: RenderPayload | null;
    // The design's size: 1920x1080 when not given.
    size?: [number, number];
    // No heading nor select (the page chose the design).
    selectable?: boolean;
    // The preview's frame no wider than this (a CSS length), centred: a tall
    // design kept within a height. The rest of the pane keeps its width. Not
    // with `fit`, which sizes the frame itself.
    frameMaxWidth?: string;
}) {
    const frame = React.useRef<HTMLDivElement>(null);
    const designFrame = React.useRef<HTMLIFrameElement>(null);
    const overlayFrame = React.useRef<HTMLIFrameElement>(null);
    const frames = React.useMemo(
        () => ({design: designFrame, overlay: overlayFrame}), []);
    const [width, height] = size ?? [OVERLAY_WIDTH, OVERLAY_HEIGHT];
    const scale = usePreviewScale(frame, design !== null, fit, width, height);
    const renders = renderPayload !== undefined;
    const heading = label ?? i18n.t("commentary_design");
    const shownKey = design ? selectKey(design) : "";
    const origin = usePreviewOrigin();
    // The pane's own reloads, and the sample shown ("": the app's state).
    const [previews, setPreviews] = React.useState(0);
    const designKey = design ? layoutPath(design) : null;
    // Chosen for one design: another design starts on the app's state.
    const [sampleOf, setSampleOf] = React.useState<{
        design: string | null; name: string;
    }>({design: null, name: ""});
    const sample = sampleOf.design === designKey ? sampleOf.name : "";
    if (sampleOf.design !== null && sampleOf.design !== designKey) {
        // Left for another design: coming back starts on the state too.
        setSampleOf({design: null, name: ""});
    }
    // Whether the design is on autona.js: its entry declares the contract, or
    // it said it was ready before in this page.
    const [autonaKnown, setAutonaKnown] = React.useState<ReadonlySet<string>>(
        () => new Set());
    const onAutona = design !== null && designKey !== null && !renders
        && (typeof design.contract === "number" || autonaKnown.has(designKey));
    // The frame's page: the sample's only for a layout on autona.js; a
    // template's names this page's origin, the one it takes renders from.
    const page = design
        ? layoutPath(design) + (renders
            ? `?autonaParent=${encodeURIComponent(window.location.origin)}`
            : onAutona && sample
                ? `?autonaSample=${encodeURIComponent(sample)}` : "")
        : null;
    // The document the frames show: a new one is watched afresh.
    const documentKey = page !== null
        ? `${page}#${reloadKey ?? 0}.${previews}` : null;
    const shownDocument = useShownDocument(documentKey);
    const quarantined = useQuarantine(
        frame, origin ? documentKey : null);
    const [report, restartCheck] = useLayoutCheck(
        origin, shownDocument, designKey, frames, setAutonaKnown);
    const notReady = useReadyTimeout(
        shownDocument, Boolean(origin) && !quarantined
            && (onAutona || (renders && design !== null)),
        report.ready);
    // The render given, posted to the design's frame once it is ready: one
    // post per document and serial. A document's later renders start their
    // errors afresh; its first keeps what the page said while it loaded.
    const posted = React.useRef<{document: string | null; serial: number}>(
        {document: null, serial: 0});
    React.useEffect(() => {
        if (!renderPayload || !origin || quarantined || !report.ready
                || shownDocument === null) {
            return;
        }
        const target = designFrame.current?.contentWindow;
        if (!target) {
            return;
        }
        const last = posted.current;
        if (last.document === shownDocument
                && last.serial === renderPayload.serial) {
            return;
        }
        if (last.document === shownDocument) {
            restartCheck();
        }
        posted.current = {document: shownDocument,
                          serial: renderPayload.serial};
        target.postMessage({type: "autona:render",
                            serial: renderPayload.serial,
                            payload: renderPayload.payload}, origin);
    }, [renderPayload, origin, quarantined, report.ready, shownDocument,
        restartCheck]);
    // Posted and not said drawn yet (or no payload yet): drawing.
    const drawing = renders && (!renderPayload || report.rendered === null
        || report.rendered < renderPayload.serial);
    const note = origin === null
        ? i18n.t("commentary_preview_unavailable")
        : quarantined ? i18n.t("commentary_preview_quarantined") : null;
    const reload = () => {
        onReload?.();
        setPreviews((count) => count + 1);
    };

    const preview = design && (
        <Box
            ref={fit ? undefined : frame}
            sx={{
                position: "relative",
                ...(fit ? {
                    flexShrink: 0,
                    boxSizing: "content-box",
                    width: `${width * scale}px`,
                    height: `${height * scale}px`,
                } : {
                    width: "100%",
                    aspectRatio: `${width} / ${height}`,
                    ...(frameMaxWidth ? {
                        maxWidth: frameMaxWidth,
                        mx: "auto",
                    } : {}),
                }),
                overflow: "hidden",
                borderRadius: 1,
                border: 1,
                borderColor: "divider",
                bgcolor: "grey.900",
                ...(background ? {
                    backgroundImage: `url("${BASE_URL}${background}")`,
                    backgroundSize: "100% 100%",
                } : {}),
            }}
        >
            {origin && !quarantined && (
                <Box
                    component="iframe"
                    ref={designFrame}
                    // A new design is a new document.
                    key={shownDocument}
                    title={i18n.t("commentary_preview")}
                    src={`${origin}${page}`}
                    sandbox={PREVIEW_SANDBOX}
                    tabIndex={-1}
                    sx={frameSx(scale, width, height)}
                />
            )}
            {origin && !quarantined && overlay && (
                <Box
                    component="iframe"
                    ref={overlayFrame}
                    key={`overlay#${reloadKey ?? 0}.${previews}`}
                    title={overlay.title}
                    src={`${origin}${overlay.src}`}
                    sandbox={PREVIEW_SANDBOX}
                    tabIndex={-1}
                    sx={frameSx(scale, width, height)}
                />
            )}
            {note && (
                <Box sx={{
                    position: "absolute",
                    inset: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    p: 2,
                    bgcolor: "rgba(0, 0, 0, 0.6)",
                }}>
                    <Typography variant="body2" role="status"
                                sx={{color: "common.white", textAlign: "center"}}>
                        {note}
                    </Typography>
                </Box>
            )}
        </Box>
    );

    return (
        <Stack spacing={1} sx={fit ? {height: {md: "100%"}} : undefined}>
            {selectable && (
                <Typography variant="subtitle1" component="h2">{heading}</Typography>
            )}
            {selectable && <TextField
                select
                size="small"
                // The select's own name (no floating label: the heading).
                slotProps={{htmlInput: {"aria-label": heading}}}
                value={design ? selectKey(design) : ""}
                disabled={designs.length === 0}
                onChange={(event) => onSelect(event.target.value)}
                fullWidth
            >
                {designs.map((entry) => {
                    const key = selectKey(entry);
                    return (
                        <MenuItem
                            key={key}
                            value={key}
                            // The select reports no pick of the value it holds,
                            // and what it shows may be the fallback for a kept
                            // design that is gone: that pick is the operator's
                            // choice as well. The server writes only what is
                            // not kept already.
                            onClick={key === shownKey
                                ? () => onSelect(key) : undefined}
                        >
                            {entry.displayName}
                        </MenuItem>
                    );
                })}
            </TextField>}
            {actions}
            {unlisted && <UnlistedDesigns folders={unlisted}/>}
            {controls}
            {design && (onAutona || onReload || obsBoards) && (
                <Stack direction="row" spacing={1} alignItems="center"
                       flexWrap="wrap" useFlexGap>
                    {onAutona && (
                        <TextField
                            select
                            size="small"
                            label={i18n.t("layout_sample")}
                            value={sample}
                            onChange={(event) => setSampleOf({
                                design: designKey, name: event.target.value})}
                            sx={{minWidth: 200}}
                        >
                            <MenuItem value="">
                                {i18n.t("layout_sample_live")}
                            </MenuItem>
                            {SAMPLE_NAMES.map((name) => (
                                <MenuItem key={name} value={name}>
                                    {i18n.t(`layout_sample_${name}`)}
                                </MenuItem>
                            ))}
                        </TextField>
                    )}
                    {onReload && (
                        <Button variant="outlined" size="small" onClick={reload}>
                            {i18n.t("layout_reload")}
                        </Button>
                    )}
                    {obsBoards && (
                        <ObsUrlButton design={design} boards={obsBoards}/>
                    )}
                </Stack>
            )}
            {design && (
                <LayoutCheckPanel design={design} report={report}
                                  notReady={notReady} drawing={drawing}/>
            )}
            {preview && fit ? (
                <Box
                    ref={frame}
                    sx={{
                        // The height that is left, up from the md breakpoint;
                        // below it the column has no height to share out.
                        flex: {md: "1 1 0"},
                        minHeight: {md: 200},
                        aspectRatio: {
                            xs: `${width} / ${height}`,
                            md: "auto",
                        },
                        width: "100%",
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "flex-start",
                    }}
                >
                    {preview}
                </Box>
            ) : preview || (
                <Typography color="text.secondary">
                    {empty ?? i18n.t("commentary_no_design")}
                </Typography>
            )}
        </Stack>
    );
}
