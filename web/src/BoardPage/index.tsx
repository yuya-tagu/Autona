import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiErrorStatus, apiPatch, apiPost, apiPut} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {useDocumentVisibility} from "../PanelShell/useDocumentVisibility";
import DesignPane from "../CommentaryPage/DesignPane";
import {
    ColorKind,
    FeatureColorCard,
    LayoutEntry,
} from "../CommentaryPage/SideCards";
import {BoardFile, BoardFiles, DesignerCard, ObsFilesCard} from "./Cards";

// The Board panel as a React page (ADR-005 S4s), /web/board: the scoreboard
// design OBS shows, previewed on the picture of a game with the winner popup
// optionally on top, the scoreboard's colours, the Designer and how to add it
// all to OBS. Shown in the desktop app's shared web view and in a browser alike.
// The design is read and kept through /api/v1/layouts (the selection "board":
// scoreboards and Designer projects), the colours are the state's, and the OBS
// files' paths have a route of their own, for this PC or the token: they name
// the operator's account.
//
// Nothing tells the page that a design folder changed, so it reads the list
// again whenever it is back: shown again, or its window has the focus again
// (the Designer's window was closed), and on its reload button, which is also
// the way when no focus comes. A read that finds the same design leaves the
// preview alone (its frame's key is the design); saving the design on show is
// what the reload button loads the frames again for.
//
// The paths are read after the selection, and say whose they are: only the
// design shown shows a path, and an answer for another (another client chose)
// reads the selection again. "Show in Explorer" says which design it means, and
// the server refuses it if that is not the one selected by now.

const FEATURE = "board";
const SELECTED = "/api/v1/layouts/selected/board";
// Behind the preview, as the Qt panel had it: what the overlay sits on in OBS.
const BACKGROUND = "/layout/videoframe_1659973.png";
const WINNER_POPUP = "/layout/winner_popup/index.html";
// A format / game the layout does not declare.
const UNKNOWN_META = "—";
// How long after the focus came back the folder is read: the Qt panel's. The
// Designer may still be saving as its window closes, and flipping to OBS and back
// is one read.
const FOCUS_READ_DELAY_MS = 750;

// The winner popup on the preview, with the page's own "#preview=" fragment
// (layout/winner_popup/index.js). Only this page's: never kept, never in the
// state, nothing to do with the popup OBS shows.
const POPUP_MODES = ["", "winner", "champion"] as const;
type PopupMode = typeof POPUP_MODES[number];

interface Envelope<T> {
    ok: boolean;
    data: T;
}

function useBoardColors() {
    return useSelector((state: ReduxState) => {
        const root = state.autonaState.autonaState as unknown as {
            colors?: Record<string, {themeColor?: string; accentColor?: string}>;
            tournamentInfo?: {themeColor?: string; accentColor?: string};
        };
        return {
            feature: root.colors?.[FEATURE],
            tournament: root.tournamentInfo,
        };
    }, (a, b) => a.feature === b.feature && a.tournament === b.tournament);
}

// Whether the window got the focus back, counted once it has kept it for a while.
function useWindowFocuses(): number {
    const [focuses, setFocuses] = React.useState(0);
    React.useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const focused = () => {
            clearTimeout(timer);
            timer = setTimeout(() => setFocuses((count) => count + 1),
                               FOCUS_READ_DELAY_MS);
        };
        window.addEventListener("focus", focused);
        return () => {
            clearTimeout(timer);
            window.removeEventListener("focus", focused);
        };
    }, []);
    return focuses;
}

function selectedOrNone(error: unknown): null {
    // No scoreboard design installed: none is shown.
    if (apiErrorStatus(error) === 404) {
        return null;
    }
    throw error;
}

function BoardContent() {
    const {language} = usePanelShell();
    const colors = useBoardColors();
    const [loaded, setLoaded] = React.useState(false);
    const [loadFailed, setLoadFailed] = React.useState(false);
    const [designs, setDesigns] = React.useState<LayoutEntry[]>([]);
    const [design, setDesign] = React.useState<LayoutEntry | null>(null);
    // The OBS files as read, which say for which design (`design` is not to
    // show another's path while the new one's is on its way).
    const [read, setRead] = React.useState<BoardFiles | null>(null);
    const [filesNotice, setFilesNotice] = React.useState<string | null>(null);
    const [popup, setPopup] = React.useState<PopupMode>("");
    // Bumped by the reload buttons: the page's reads, and the preview's frames.
    const [loads, setLoads] = React.useState(0);
    const [previews, setPreviews] = React.useState(0);
    // Bumped as a read of the selection ends: the paths' read comes after it,
    // never alongside, where an answer for another design (below) could call off
    // the read it asks for.
    const [pathReads, setPathReads] = React.useState(0);
    const [revealing, setRevealing] = React.useState(false);
    // The selection's writes under way and how many there were, to tell a read
    // that crossed one, whichever started first: it may have the selection of
    // before it, and the write's answer says what is selected.
    const selecting = React.useRef({underWay: 0, made: 0});
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const anyRead = React.useRef(false);
    const serial = useSerialRequests();
    const {reveals} = useDocumentVisibility();
    const focuses = useWindowFocuses();

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("board_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        const isLive = () => live;
        const writing = selecting.current.underWay > 0;
        const madeBefore = selecting.current.made;
        Promise.all([
            getWithRetry<Envelope<LayoutEntry[]>>(
                "/api/v1/layouts?kind=scoreboard,designer", isLive),
            getWithRetry<Envelope<LayoutEntry>>(SELECTED, isLive).then(
                (body) => body.data, selectedOrNone),
        ]).then(([list, selected]) => {
            if (!live) {
                return;
            }
            anyRead.current = true;
            setDesigns(list.data);
            if (!writing && selecting.current.underWay === 0
                    && selecting.current.made === madeBefore) {
                setDesign(selected);
            }
            setLoadFailed(false);
            setLoaded(true);
            setPathReads((count) => count + 1);
        }, (error) => {
            console.error(error);
            // Reading again later failing keeps what the page shows.
            if (live && !anyRead.current) {
                setLoadFailed(true);
            }
        });
        return () => {
            live = false;
        };
    }, [loads, reveals, focuses]);

    const designId = design?.id ?? null;
    const designEntry = design?.entry ?? null;
    React.useEffect(() => {
        if (!loaded) {
            return undefined;
        }
        let live = true;
        getWithRetry<Envelope<BoardFiles>>(`${SELECTED}/obs-path`, () => live)
            .then((body) => {
                if (live) {
                    setFilesNotice(null);
                    setRead(body.data);
                    // A write's answer brings its own design.
                    if (body.data.designId !== designId
                            && selecting.current.underWay === 0) {
                        setLoads((count) => count + 1);
                    }
                }
            }, (error) => {
                console.error(error);
                if (live) {
                    const status = apiErrorStatus(error);
                    setRead(null);
                    setFilesNotice(i18n.t(status === 401 || status === 403
                        ? "board_obs_token_required" : "board_obs_load_failed"));
                }
            });
        return () => {
            live = false;
        };
    }, [loaded, designId, designEntry, pathReads]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
        setPreviews((count) => count + 1);
    }, []);

    // The token once, else `message` (or the generic failure).
    const report = React.useCallback((error: unknown, message?: string) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                showSnackbar(i18n.t("stream_token_required"), "error");
            }
        } else {
            showSnackbar(message ?? i18n.t("request_failed"), "error");
        }
    }, []);

    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) =>
            serial(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                  WRITE_OPTIONS))
                .catch((error: unknown) => {
                    report(error);
                    throw error;
                }),
        [serial, report]);

    const selectDesign = React.useCallback((id: string) => {
        selecting.current.underWay += 1;
        selecting.current.made += 1;
        serial(() => apiPut(SELECTED, {id}, WRITE_OPTIONS))
            .then((text) => {
                selecting.current.underWay -= 1;
                setDesign((JSON.parse(text) as Envelope<LayoutEntry>).data);
            }, (error: unknown) => {
                // Ended before the read below starts: that one counts.
                selecting.current.underWay -= 1;
                // A 400 is a design that is gone since the list was read.
                report(error, apiErrorStatus(error) === 400
                    ? i18n.t("board_design_refused") : undefined);
                setLoads((count) => count + 1);
            });
    }, [serial, report]);

    const reveal = React.useCallback((target: BoardFile) => {
        setRevealing(true);
        serial(() => apiPost(`${SELECTED}:reveal`,
                             target === "design"
                                 ? {target, designId} : {target},
                             WRITE_OPTIONS))
            .catch((error: unknown) => {
                console.error(error);
                const status = apiErrorStatus(error);
                showSnackbar(
                    status === 403 ? i18n.t("board_reveal_this_pc_only")
                        : status === 404 ? i18n.t("board_reveal_missing")
                            : status === 409 ? i18n.t("board_reveal_changed")
                                : i18n.t("request_failed"),
                    "error");
                if (status === 409) {
                    setLoads((count) => count + 1);
                }
            })
            .finally(() => setRevealing(false));
    }, [serial, designId]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("board_reload")}
                        </Button>
                    }
                >
                    {i18n.t("board_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!loaded) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("board_loading")}</Typography>
            </Box>
        );
    }

    // Seeded at launch (core StateSchema): missing only until the state has
    // arrived.
    const tournamentTheme = colors.tournament?.themeColor;
    const tournamentAccent = colors.tournament?.accentColor;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;
    const files = read && {
        design: read.designId === designId ? read.design : null,
        winnerPopup: read.winnerPopup,
    };
    const popupLabel = i18n.t("board_popup_label");

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
                    <DesignerCard/>
                    <FeatureColorCard
                        title={i18n.t("board_colors")}
                        theme={colors.feature?.themeColor ?? tournamentTheme ?? ""}
                        accent={colors.feature?.accentColor ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                    <ObsFilesCard
                        files={files}
                        notice={filesNotice}
                        reveal={reveal}
                        revealing={revealing}
                    />
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <DesignPane
                    fit
                    designs={designs}
                    design={design}
                    onSelect={selectDesign}
                    label={i18n.t("board_design")}
                    empty={i18n.t("board_no_design")}
                    background={BACKGROUND}
                    overlay={popup ? {
                        src: `${WINNER_POPUP}#preview=${popup}`,
                        title: i18n.t("board_popup_preview"),
                    } : null}
                    reloadKey={previews}
                    controls={
                        <Stack spacing={1}>
                            {design && (
                                <Box>
                                    <Typography variant="body2"
                                                color="text.secondary">
                                        {i18n.t("board_meta_format", {
                                            value: design.format || UNKNOWN_META,
                                        })}
                                    </Typography>
                                    <Typography variant="body2"
                                                color="text.secondary">
                                        {i18n.t("board_meta_game", {
                                            value: design.game || UNKNOWN_META,
                                        })}
                                    </Typography>
                                </Box>
                            )}
                            <Typography variant="subtitle1" component="h2">
                                {popupLabel}
                            </Typography>
                            <TextField
                                select
                                size="small"
                                // "" is the "hidden" choice: a select shows no
                                // label for an empty value unless told to.
                                slotProps={{
                                    htmlInput: {"aria-label": popupLabel},
                                    select: {displayEmpty: true},
                                }}
                                value={popup}
                                helperText={i18n.t("board_popup_note")}
                                onChange={(event) => setPopup(
                                    event.target.value as PopupMode)}
                                fullWidth
                            >
                                {POPUP_MODES.map((mode) => (
                                    <MenuItem key={mode} value={mode}>
                                        {i18n.t(`board_popup_${mode || "hidden"}`)}
                                    </MenuItem>
                                ))}
                            </TextField>
                            <Box>
                                <Button
                                    variant="outlined"
                                    size="small"
                                    onClick={reload}
                                >
                                    {i18n.t("board_reload")}
                                </Button>
                            </Box>
                        </Stack>
                    }
                />
            </Box>
        </Box>
    );
}

export default function BoardPage() {
    return (
        <FeedbackProvider>
            <BoardContent/>
        </FeedbackProvider>
    );
}
