import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    CircularProgress,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {AutonaPlayerDbEntry} from "../backendDataTypes";
import {apiErrorStatus, apiGetJson, apiPatch, apiPost, apiPut} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import CasterCard, {
    CardDrag,
    Caster,
    CasterFields,
    CommentaryOptions,
    EMPTY_CASTER,
} from "./CasterCard";
import DesignPane from "./DesignPane";
import {ColorKind, FeatureColorCard, LayoutEntry, ObsUrlCard} from "./SideCards";

// The Commentary panel as a React page (ADR-005 S4b), /web/commentary: the
// caster line-up (commentary.*), the design OBS shows, and the feature's
// colours. Shown in the desktop app's shared web view and in a browser
// alike. It writes through /api/v1 only -- the core operations the Qt panel
// (src/AutonaCommentaryWidget.py) asks for -- and renders the state it gets
// over Socket.IO.

const FEATURE = "commentary";
const DRAG_TYPE = "application/x-autona-commentary-card";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// How long a write may take. The fetch is aborted then (api.ts's timeout),
// and the page stops waiting for it at the same moment: its snackbar shows
// and the writes queued behind it go on, instead of the whole page's
// writes waiting on a request that never answers.
export const WRITE_TIMEOUT_MS = 10000;
const WRITE_OPTIONS = {timeoutMs: WRITE_TIMEOUT_MS};

// The reads the page cannot work without are tried this many times, the
// waits between them growing by LOAD_RETRY_MS (the server may be busy
// starting). A refusal (4xx) is an answer, not tried again.
export const LOAD_ATTEMPTS = 3;
export const LOAD_RETRY_MS = 1000;

function withDeadline<T>(request: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => reject(Object.assign(
            new Error(`no answer within ${WRITE_TIMEOUT_MS} ms`),
            {name: "TimeoutError"})), WRITE_TIMEOUT_MS);
        Promise.resolve().then(request).then(resolve, reject)
            .finally(() => clearTimeout(timer));
    });
}

// The page's writes, one at a time in the order they were made: a field
// typed into and then dragged elsewhere is written to the slot it was typed
// in before the casters move, and a name's pick lands after its typing.
function useSerialRequests() {
    const tail = React.useRef<Promise<unknown>>(Promise.resolve());
    return React.useCallback(<T,>(request: () => Promise<T>): Promise<T> => {
        const next = () => withDeadline(request);
        const run = tail.current.then(next, next);
        tail.current = run.catch(() => undefined);
        return run;
    }, []);
}

async function getWithRetry<T>(path: string, live: () => boolean): Promise<T> {
    for (let attempt = 1; ; attempt++) {
        try {
            return await apiGetJson<T>(path);
        } catch (error) {
            const status = apiErrorStatus(error);
            const refused = status !== undefined && status < 500;
            if (refused || attempt >= LOAD_ATTEMPTS || !live()) {
                throw error;
            }
            await new Promise((resolve) =>
                setTimeout(resolve, LOAD_RETRY_MS * attempt));
        }
    }
}

function useCommentaryState() {
    const commentary = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as unknown as {
            commentary?: Record<string, Caster>;
        }).commentary);
    const colors = useSelector((state: ReduxState) => {
        const root = state.autonaState.autonaState as unknown as {
            colors?: Record<string, {themeColor?: string; accentColor?: string}>;
            tournamentInfo?: {themeColor?: string; accentColor?: string};
        };
        return {
            feature: root.colors?.[FEATURE],
            tournament: root.tournamentInfo,
        };
    }, (a, b) => a.feature === b.feature && a.tournament === b.tournament);
    const players = useSelector(
        (state: ReduxState) => state.autonaPlayers.players);
    const playerList = React.useMemo(
        () => Object.values(players ?? {}) as AutonaPlayerDbEntry[], [players]);
    return {commentary, colors, players: playerList};
}

function DropLine() {
    return <Box sx={{height: 2, bgcolor: "primary.main", borderRadius: 1}}/>;
}

function CommentaryContent() {
    const {language} = usePanelShell();
    const {commentary, colors, players} = useCommentaryState();
    const [options, setOptions] = React.useState<CommentaryOptions | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    const [designs, setDesigns] = React.useState<LayoutEntry[]>([]);
    const [design, setDesign] = React.useState<LayoutEntry | null>(null);
    const [dropAt, setDropAt] = React.useState<number | null>(null);
    // Bumped by the reload button: loads the page's settings again.
    const [loads, setLoads] = React.useState(0);
    const dragFrom = React.useRef<string | null>(null);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("commentary_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        const isLive = () => live;
        Promise.all([
            getWithRetry<Envelope<CommentaryOptions>>(
                "/api/v1/commentary/options", isLive),
            getWithRetry<Envelope<LayoutEntry[]>>(
                "/api/v1/layouts?kind=commentary", isLive),
            getWithRetry<Envelope<LayoutEntry>>(
                "/api/v1/layouts/selected/commentary", isLive).then(
                (body) => body.data,
                (error) => {
                    // No commentary design installed: none is shown.
                    if (apiErrorStatus(error) === 404) {
                        return null;
                    }
                    throw error;
                }),
        ]).then(([optionsBody, designsBody, selected]) => {
            if (live) {
                setOptions(optionsBody.data);
                setDesigns(designsBody.data);
                setDesign(selected);
            }
        }, (error) => {
            console.error(error);
            if (live) {
                setLoadFailed(true);
            }
        });
        return () => {
            live = false;
        };
    }, [loads]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
    }, []);

    const maxDisplay = options?.maxDisplay ?? 0;
    const write = React.useCallback(<T,>(request: () => Promise<T>) =>
        serial(request).catch((error: unknown) => {
            console.error(error);
            const status = apiErrorStatus(error);
            if (status === 401 || status === 403) {
                if (!toldToken.current) {
                    toldToken.current = true;
                    showSnackbar(i18n.t("commentary_token_required"), "error");
                }
            } else {
                showSnackbar(status === 409
                    ? i18n.t("commentary_display_limit", {max: maxDisplay})
                    : i18n.t("request_failed"), "error");
            }
            throw error;
        }), [serial, maxDisplay]);

    const sendFields = React.useCallback(
        (slot: string, fields: CasterFields) =>
            write(() => apiPatch(`/api/v1/commentary/${slot}`, fields,
                                 WRITE_OPTIONS)),
        [write]);
    const pickPlayer = React.useCallback(
        (slot: string, player: AutonaPlayerDbEntry) =>
            write(() => apiPost(`/api/v1/commentary/${slot}/player`, player,
                                WRITE_OPTIONS)),
        [write]);
    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) =>
            write(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                 WRITE_OPTIONS)),
        [write]);
    const selectDesign = React.useCallback((id: string) => {
        write(() => apiPut("/api/v1/layouts/selected/commentary", {id},
                           WRITE_OPTIONS))
            .then((text) => setDesign((JSON.parse(text) as Envelope<LayoutEntry>).data))
            .catch(() => undefined);
    }, [write]);

    const slots = options?.slots;
    const drag: CardDrag = React.useMemo(() => ({
        onGripStart(slot, event) {
            // What is being typed goes to the slot it was typed into: leaving
            // the field sends it now, ahead of the reorder (serial writes).
            const active = document.activeElement as HTMLElement | null;
            active?.blur?.();
            dragFrom.current = slot;
            const transfer = event.dataTransfer;
            if (transfer) {
                transfer.setData(DRAG_TYPE, slot);
                transfer.effectAllowed = "move";
                const card = (event.currentTarget as HTMLElement)
                    .closest("[data-caster-card]");
                if (card && transfer.setDragImage) {
                    transfer.setDragImage(card, 16, 16);
                }
            }
        },
        onEnd() {
            dragFrom.current = null;
            setDropAt(null);
        },
        onOver(index, event) {
            if (dragFrom.current === null) {
                return;  // not one of these cards
            }
            event.preventDefault();
            setDropAt(dropIndex(index, event));
        },
        onDrop(index, event) {
            const from = dragFrom.current;
            dragFrom.current = null;
            setDropAt(null);
            if (from === null || !slots) {
                return;
            }
            event.preventDefault();
            const order = movedOrder(slots, from, dropIndex(index, event));
            if (order.join() !== slots.join()) {
                // The state moves the casters; the cards follow its echo.
                write(() => apiPost("/api/v1/commentary/reorder", {order},
                                    WRITE_OPTIONS))
                    .catch(() => undefined);
            }
        },
    }), [slots, write]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("commentary_reload")}
                        </Button>
                    }
                >
                    {i18n.t("commentary_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!options || !slots) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("commentary_loading")}</Typography>
            </Box>
        );
    }

    const shownCount = slots.filter(
        (slot) => Boolean(commentary?.[slot]?.display)).length;
    // Seeded at launch (core StateSchema, from the settings or Autona's
    // defaults): missing only until the state has arrived.
    const tournamentTheme = colors.tournament?.themeColor;
    const tournamentAccent = colors.tournament?.accentColor;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;

    return (
        <Box
            component="main"
            sx={{
                display: "flex",
                flexDirection: {xs: "column", md: "row"},
                height: {md: "100vh"},
                bgcolor: "background.default",
            }}
        >
            <Box sx={{width: {md: 420}, flexShrink: 0, overflowY: {md: "auto"},
                      p: 2}}>
                <Stack spacing={2}>
                    <Card variant="outlined" component="section"
                          aria-label={i18n.t("commentary_casters",
                                             {count: slots.length})}>
                        <CardContent>
                            <Stack spacing={1}>
                                <Typography variant="subtitle1" component="h2">
                                    {i18n.t("commentary_casters",
                                            {count: slots.length})}
                                </Typography>
                                <Typography variant="body2"
                                            color="text.secondary">
                                    {i18n.t("commentary_casters_note",
                                            {max: maxDisplay})}
                                </Typography>
                                {slots.map((slot, index) => (
                                    <React.Fragment key={slot}>
                                        {dropAt === index && <DropLine/>}
                                        <CasterCard
                                            slot={slot}
                                            index={index}
                                            caster={commentary?.[slot] ?? EMPTY_CASTER}
                                            options={options}
                                            players={players}
                                            displayLocked={shownCount >= maxDisplay}
                                            sendFields={sendFields}
                                            pickPlayer={pickPlayer}
                                            drag={drag}
                                        />
                                    </React.Fragment>
                                ))}
                                {dropAt === slots.length && <DropLine/>}
                            </Stack>
                        </CardContent>
                    </Card>
                    <FeatureColorCard
                        title={i18n.t("commentary_colors")}
                        theme={colors.feature?.themeColor ?? tournamentTheme ?? ""}
                        accent={colors.feature?.accentColor ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                    <ObsUrlCard design={design}/>
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <DesignPane
                    designs={designs}
                    design={design}
                    onSelect={selectDesign}
                />
            </Box>
        </Box>
    );
}

// Where a card dropped on card `index` goes: before it in its upper half,
// after it in the lower (the Qt list's midpoint rule).
function dropIndex(index: number, event: React.DragEvent<HTMLElement>): number {
    const box = event.currentTarget.getBoundingClientRect();
    return event.clientY < box.top + box.height / 2 ? index : index + 1;
}

// The order Reorder takes: for slot 1, 2, ... the slot whose caster goes
// there, after moving `from` to position `at` (0..slots.length).
export function movedOrder(slots: string[], from: string, at: number): string[] {
    const source = slots.indexOf(from);
    const order = slots.filter((slot) => slot !== from);
    order.splice(at > source ? at - 1 : at, 0, from);
    return order;
}

export default function CommentaryPage() {
    return (
        <FeedbackProvider>
            <CommentaryContent/>
        </FeedbackProvider>
    );
}
