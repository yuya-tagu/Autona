import React from "react";
import {flushSync} from "react-dom";
import {Box, MenuItem, Stack, TextField, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {BACKEND_PORT, BASE_URL} from "../env";
import {getWithRetry} from "../PanelShell/requests";
import socketConnection from "../websocketConnection";
import {LayoutEntry} from "./SideCards";

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
// out with a note, until another design (or a reload) is shown. The page listens
// to no `message` from the frames.

export const HEALTH_PATH = "/api/v1/health";
export const PREVIEW_SANDBOX = "allow-scripts allow-same-origin";
export const QUARANTINE_POLL_MS = 250;

const OVERLAY_WIDTH = 1920;
const OVERLAY_HEIGHT = 1080;
// The preview's border, on both sides: what `fit` leaves out of what it measures.
const FRAME_BORDERS = 2;

function layoutPath(design: LayoutEntry): string {
    const id = design.id.split("/").map(encodeURIComponent).join("/");
    return `/layout/${id}/${encodeURIComponent(design.entry)}`;
}

// What the select picks a design by: `id`, unless the entry has its own key.
function selectKey(design: LayoutEntry): string {
    return design.key ?? design.id;
}

function usePreviewScale(
    frame: React.RefObject<HTMLDivElement | null>,
    shown: boolean,
    fit: boolean,
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
                        (element.clientWidth - FRAME_BORDERS) / OVERLAY_WIDTH,
                        (element.clientHeight - FRAME_BORDERS) / OVERLAY_HEIGHT)
                    : element.clientWidth / OVERLAY_WIDTH);
            }
        };
        measure();
        if (typeof ResizeObserver === "undefined") {
            return undefined;
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [frame, shown, fit]);
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

// The overlay pages lie on one another: one 1920x1080 frame each, scaled.
function frameSx(scale: number) {
    return {
        position: "absolute",
        top: 0,
        left: 0,
        width: OVERLAY_WIDTH,
        height: OVERLAY_HEIGHT,
        border: 0,
        transform: `scale(${scale})`,
        transformOrigin: "0 0",
        pointerEvents: "none",
    };
}

export default function DesignPane({
    designs, design, onSelect, label, empty, controls, background, overlay,
    reloadKey, fit = false,
}: {
    designs: LayoutEntry[];
    design: LayoutEntry | null;
    // Called with the design's select key (its `key`, else its `id`).
    onSelect: (key: string) => void;
    // The heading (and the select's name), and what shows without a
    // design: the Commentary page's by default.
    label?: string;
    empty?: string;
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
}) {
    const frame = React.useRef<HTMLDivElement>(null);
    const scale = usePreviewScale(frame, design !== null, fit);
    const heading = label ?? i18n.t("commentary_design");
    const shownKey = design ? selectKey(design) : "";
    const origin = usePreviewOrigin();
    // The document the frames show: a new one is watched afresh.
    const documentKey = design
        ? `${layoutPath(design)}#${reloadKey ?? 0}` : null;
    const quarantined = useQuarantine(
        frame, origin ? documentKey : null);
    const note = origin === null
        ? i18n.t("commentary_preview_unavailable")
        : quarantined ? i18n.t("commentary_preview_quarantined") : null;

    const preview = design && (
        <Box
            ref={fit ? undefined : frame}
            sx={{
                position: "relative",
                ...(fit ? {
                    flexShrink: 0,
                    boxSizing: "content-box",
                    width: `${OVERLAY_WIDTH * scale}px`,
                    height: `${OVERLAY_HEIGHT * scale}px`,
                } : {
                    width: "100%",
                    aspectRatio: `${OVERLAY_WIDTH} / ${OVERLAY_HEIGHT}`,
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
                    // A new design is a new document.
                    key={documentKey}
                    title={i18n.t("commentary_preview")}
                    src={`${origin}${layoutPath(design)}`}
                    sandbox={PREVIEW_SANDBOX}
                    tabIndex={-1}
                    sx={frameSx(scale)}
                />
            )}
            {origin && !quarantined && overlay && (
                <Box
                    component="iframe"
                    key={`overlay#${reloadKey ?? 0}`}
                    title={overlay.title}
                    src={`${origin}${overlay.src}`}
                    sandbox={PREVIEW_SANDBOX}
                    tabIndex={-1}
                    sx={frameSx(scale)}
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
            <Typography variant="subtitle1" component="h2">{heading}</Typography>
            <TextField
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
            </TextField>
            {controls}
            {preview && fit ? (
                <Box
                    ref={frame}
                    sx={{
                        // The height that is left, up from the md breakpoint;
                        // below it the column has no height to share out.
                        flex: {md: "1 1 0"},
                        minHeight: {md: 200},
                        aspectRatio: {
                            xs: `${OVERLAY_WIDTH} / ${OVERLAY_HEIGHT}`,
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
