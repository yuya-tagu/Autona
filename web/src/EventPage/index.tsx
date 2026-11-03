import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {
    apiDelete,
    apiErrorCode,
    apiErrorStatus,
    apiPatch,
    apiPost,
    apiPut,
} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    WRITE_TIMEOUT_MS,
    getWithRetry,
    useSerialRequests,
    withDeadline,
} from "../PanelShell/requests";
import {
    IMAGE_UPLOAD_MAX_BYTES,
    UPLOAD_TIMEOUT_MS,
    readDataUrl,
} from "../PanelShell/imageUpload";
import {ColorKind} from "../CommentaryPage/SideCards";
import {
    ColorsCard,
    GameEntry,
    InfoCard,
    InfoFields,
    MediaCard,
    MediaKind,
    TournamentCard,
    TournamentLoad,
    TournamentMedia,
} from "./Cards";

// The 大会情報 panel as a React page (ADR-005 S4g), /web/event: the start.gg
// tournament, its details, its colours and its icon / banner. Shown in the
// desktop app's shared web view and in a browser alike. It writes through
// /api/v1 only -- core's TournamentService / TournamentInfoService /
// TournamentMediaService / ColorService do the work -- and renders the
// state it gets over Socket.IO (tournamentInfo.*, game): the tournament's
// URL and how its set / clear is going too (tournamentInfo.url / load,
// S4g review M1), whoever set it -- the page, the sidebar, another client,
// the launch's restore. Only the game list is read (GET /api/v1/games):
// when the page opens, when the state's game changes (a game loaded) and
// when the page is on screen again.

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface EventState {
    tournamentInfo?: Partial<Record<keyof InfoFields, unknown>> & {
        themeColor?: string;
        accentColor?: string;
        logoVersion?: number;
        bannerVersion?: number;
        media?: Partial<TournamentMedia>;
        url?: unknown;
        load?: Partial<TournamentLoad>;
    };
    game?: {codename?: string | null};
}

function version(value: unknown): number {
    return typeof value === "number" ? value : 0;
}

function readMedia(raw: Partial<TournamentMedia> | undefined): TournamentMedia {
    const media = raw && typeof raw === "object" ? raw : {};
    return {
        status: media.status ?? "idle",
        error: typeof media.error === "string" ? media.error : "",
        revision: version(media.revision),
    };
}

function readLoad(raw: Partial<TournamentLoad> | undefined): TournamentLoad {
    const load = raw && typeof raw === "object" ? raw : {};
    return {
        status: load.status ?? "idle",
        error: typeof load.error === "string" ? load.error : "",
        revision: version(load.revision),
    };
}

// PATCH /api/v1/colors/tournament: a colour or two, or back to the
// defaults.
type ColorsBody = Partial<Record<ColorKind, string>> | {reset: true};

// What a refused write says; null: nothing (the caller tells it). `code`
// is /api/v1's error code (CONFLICT, NOT_READY, ...).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;

function EventContent() {
    const {language} = usePanelShell();
    const state = useSelector((root: ReduxState) =>
        root.autonaState.autonaState as unknown as EventState);
    const info = state.tournamentInfo ?? {};
    const media = readMedia(info.media);
    const load = readLoad(info.load);
    const url = typeof info.url === "string" ? info.url : "";
    const game = state.game;
    const [games, setGames] = React.useState<Record<string, GameEntry> | null>(null);
    const gamesRead = React.useRef(false);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped to read the game list again (the reload button, the page on
    // screen again).
    const [loads, setLoads] = React.useState(0);
    // A set / clear of this page's: not answered yet, then -- answered --
    // the revision of tournamentInfo.load it was published at, until the
    // state the page has reaches it (at most WRITE_TIMEOUT_MS: a state that
    // never comes does not hold the buttons for ever).
    const [requesting, setRequesting] = React.useState(false);
    const [awaiting, setAwaiting] = React.useState<number | null>(null);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();
    const mounted = React.useRef(true);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("event_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    // The game list (S4g review L8): read again when the state's game
    // changes (a game loaded) and when the page is on screen again (the
    // desktop's view shown again, a browser tab back) -- a rescan alone
    // writes no state, so a game added meanwhile shows then. A list read
    // once went stale. The newest read wins.
    React.useEffect(() => {
        const shown = () => {
            if (document.visibilityState === "visible") {
                setLoads((count) => count + 1);
            }
        };
        document.addEventListener("visibilitychange", shown);
        return () => document.removeEventListener("visibilitychange", shown);
    }, []);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Record<string, GameEntry>>>(
            "/api/v1/games", () => live)
            .then((body) => {
                if (live) {
                    gamesRead.current = true;
                    setGames(body.data ?? {});
                }
            }, (error) => {
                console.error(error);
                // A list read already stays (a reread failing is no reason
                // to take the page away).
                if (live && !gamesRead.current) {
                    setLoadFailed(true);
                }
            });
        return () => {
            live = false;
        };
    }, [loads, game]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
    }, []);

    // A failed request told: the token once, else what `refusal` makes of
    // its status and code (or the generic failure).
    const report = React.useCallback((error: unknown, refusal?: Refusal) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                showSnackbar(i18n.t("stream_token_required"), "error");
            }
        } else {
            const message = refusal ? refusal(status, apiErrorCode(error))
                : i18n.t("request_failed");
            if (message) {
                showSnackbar(message, "error");
            }
        }
    }, []);

    const write = React.useCallback(<T,>(request: () => Promise<T>,
                                         refusal?: Refusal) =>
        serial(request).catch((error: unknown) => {
            report(error, refusal);
            throw error;
        }), [serial, report]);

    // ── the tournament ──

    const tournamentRefusal: Refusal = React.useCallback((status) => {
        if (status === 400) {
            return i18n.t("event_url_invalid");
        }
        if (status === 409) {
            return i18n.t("event_busy");
        }
        return i18n.t("request_failed");
    }, []);

    // The page's own set / clear is in the state once tournamentInfo.load
    // reaches the revision the 202 named (published before the 202, S4g
    // review M1): until then the buttons stay busy, so the previous
    // request's result is never taken for this one's.
    React.useEffect(() => {
        if (awaiting === null) {
            return undefined;
        }
        if (load.revision >= awaiting) {
            setAwaiting(null);
            return undefined;
        }
        const timer = setTimeout(() => setAwaiting(null), WRITE_TIMEOUT_MS);
        return () => clearTimeout(timer);
    }, [awaiting, load.revision]);

    // Accepted (202): how it goes is the state's tournamentInfo.load.
    const run = React.useCallback((request: () => Promise<string>) => {
        setRequesting(true);
        write(request, tournamentRefusal).then((text) => {
            if (!mounted.current) {
                return;
            }
            const revision = (JSON.parse(text) as
                Envelope<{loadRevision?: unknown}>).data?.loadRevision;
            setAwaiting(typeof revision === "number" ? revision : null);
            setRequesting(false);
        }, () => {
            if (mounted.current) {
                setRequesting(false);
            }
        });
    }, [write, tournamentRefusal]);

    const setTournament = React.useCallback(
        (target: string, flags: {force?: boolean; fetchMedia?: boolean}) => {
            run(() => apiPost("/api/v1/tournament", {url: target, ...flags},
                              WRITE_OPTIONS));
        }, [run]);
    const clearTournament = React.useCallback(
        () => run(() => apiDelete("/api/v1/tournament", WRITE_OPTIONS)),
        [run]);

    // ── the details ──

    const sendField = React.useCallback(
        (fields: Partial<InfoFields>) => write(
            () => apiPatch("/api/v1/tournament/info", fields, WRITE_OPTIONS),
            (status) => (status === 400 && "startAtDate" in fields
                ? i18n.t("event_date_invalid")
                : i18n.t("request_failed"))),
        [write]);
    const refuseEntrants = React.useCallback(() => {
        showSnackbar(i18n.t("event_entrants_invalid"), "error");
    }, []);
    const chooseGame = React.useCallback(
        (codename: string) => write(
            () => apiPost("/api/v1/game", {codename}, WRITE_OPTIONS),
            (status) => (status === 409 ? i18n.t("event_game_busy")
                : i18n.t("request_failed"))),
        [write]);

    // ── the colours ──

    const sendColors = React.useCallback(
        (colors: ColorsBody) => write(
            () => apiPatch("/api/v1/colors/tournament", colors,
                           WRITE_OPTIONS)),
        [write]);
    const resetColors = React.useCallback(() => {
        sendColors({reset: true}).catch(() => undefined);
    }, [sendColors]);

    // ── the icon / banner ──

    const upload = React.useCallback((kind: MediaKind, file: File) => {
        const tooLarge = () => i18n.t("stream_org_too_large",
                                      {max: IMAGE_UPLOAD_MAX_BYTES / 1024 / 1024});
        if (file.size > IMAGE_UPLOAD_MAX_BYTES) {
            showSnackbar(tooLarge(), "error");
            return;
        }
        const refusal: Refusal = (status) => {
            if (status === 413) {
                return tooLarge();
            }
            if (status === 400) {
                return i18n.t("stream_org_invalid");
            }
            if (status === 500) {
                return i18n.t("event_media_save_failed");
            }
            return i18n.t("request_failed");
        };
        // Not one of the queued writes (up to a minute on the LAN): the
        // preview follows the version the import publishes.
        withDeadline(async () => apiPut(`/api/v1/tournament/media/${kind}`,
                                        {image: await readDataUrl(file)},
                                        {timeoutMs: UPLOAD_TIMEOUT_MS}),
                     UPLOAD_TIMEOUT_MS)
            .catch((error: unknown) => report(error, refusal));
    }, [report]);

    const fetchMedia = React.useCallback(() => {
        // Accepted (202): the run reports itself in tournamentInfo.media.
        // 409: one is running for this tournament already (CONFLICT), or
        // none is loaded (NOT_READY) -- told by the answer's code, not by
        // the state the page has (S4g review L5).
        write(() => apiPost("/api/v1/tournament/media/fetch", {},
                            WRITE_OPTIONS),
              (status, code) => {
                  if (status !== 409) {
                      return i18n.t("request_failed");
                  }
                  return code === "NOT_READY"
                      ? i18n.t("event_media_no_tournament") : null;
              })
            .then((text) => {
                const queued = (JSON.parse(String(text)) as
                    Envelope<{queued?: boolean}>).data?.queued;
                if (queued) {
                    showSnackbar(i18n.t("event_media_queued"), "info");
                }
            }, (error: unknown) => {
                if (apiErrorStatus(error) === 409
                        && apiErrorCode(error) !== "NOT_READY") {
                    showSnackbar(i18n.t("event_media_running"), "info");
                }
            });
    }, [write]);

    const resetMedia = React.useCallback(() => {
        write(() => apiDelete("/api/v1/tournament/media", WRITE_OPTIONS),
              (status) => (status === 500 ? i18n.t("event_media_save_failed")
                  : i18n.t("request_failed")))
            .catch(() => undefined);
    }, [write]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("event_reload")}
                        </Button>
                    }
                >
                    {i18n.t("event_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!games) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("event_loading")}</Typography>
            </Box>
        );
    }

    return (
        <Box component="main"
             sx={{minHeight: "100vh", bgcolor: "background.default", p: 2}}>
            <Stack spacing={2} sx={{maxWidth: 720, mx: "auto"}}>
                <TournamentCard
                    url={url}
                    load={load}
                    waiting={requesting || awaiting !== null}
                    setTournament={setTournament}
                    clear={clearTournament}
                />
                <InfoCard
                    info={info}
                    games={games}
                    game={game?.codename ?? ""}
                    sendField={sendField}
                    chooseGame={chooseGame}
                    refuseEntrants={refuseEntrants}
                />
                <ColorsCard
                    theme={typeof info.themeColor === "string"
                        ? info.themeColor : ""}
                    accent={typeof info.accentColor === "string"
                        ? info.accentColor : ""}
                    send={sendColors}
                    reset={resetColors}
                />
                <MediaCard
                    logoVersion={version(info.logoVersion)}
                    bannerVersion={version(info.bannerVersion)}
                    media={media}
                    upload={upload}
                    fetch={fetchMedia}
                    reset={resetMedia}
                />
            </Stack>
        </Box>
    );
}

export default function EventPage() {
    return (
        <FeedbackProvider>
            <EventContent/>
        </FeedbackProvider>
    );
}
