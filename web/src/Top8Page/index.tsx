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
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import DesignPane from "../CommentaryPage/DesignPane";
import {
    ColorKind,
    FeatureColorCard,
    LayoutEntry,
    ObsUrlCard,
} from "../CommentaryPage/SideCards";
import {Characters} from "./characterPick";
import {
    CountryEntry,
    PlayerFields,
    PlayersCard,
    ScoresCard,
    Top8Match,
    Top8Player,
} from "./Cards";

// The Top 8 panel as a React page (ADR-005 S4i), /web/top8: the eight
// players, the overlay's caption, the scores (staged, sent by 反映), the
// import of the tournament's results, the feature's colours and the design
// OBS shows. Shown in the desktop app's shared web view and in a browser
// alike. It writes through /api/v1 only -- core's Top8Service does the work
// (identifiers in, values built by core) -- and renders the state it gets
// over Socket.IO (top8.*). Read besides: the country list (once; the page
// goes on without it while the country data is not loaded), the character
// list (for the state's game: read again when it changes, and when the page
// is on screen again) and the designs.

const FEATURE = "top8";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface Top8State {
    top8?: {
        phaseLabel?: unknown;
        players?: Record<string, Top8Player>;
        matches?: Record<string, Top8Match>;
    };
    game?: {codename?: unknown} | null;
}

// What a refused write says; null: nothing (the caller tells it). `code`
// is /api/v1's error code (CONFLICT, NOT_READY, ...).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;

function useTop8State() {
    const top8 = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as unknown as Top8State).top8);
    const codename = useSelector((state: ReduxState) => {
        const game = (state.autonaState.autonaState as unknown as Top8State).game;
        return typeof game?.codename === "string" ? game.codename : "";
    });
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
    return {top8, codename, colors};
}

interface CharacterList {
    // The list of the state's game; null until it is read for that game.
    list: Characters | null;
    // Reading it for this game failed (told; `reload` reads it again).
    failed: boolean;
    reload: () => void;
}

// The character list for the state's game. Null until it is read for that
// game: a pick is never made on another game's list (S4h review L7 -- the
// state's game changed and the list has not followed yet). Read again when
// the page is on screen again (a rescan alone writes no state), keeping
// the list it has meanwhile, by the page's reload button (`loads`) and by
// `reload`. The page's first read failing fails the page (`failed`, the
// argument); a later read failing is told (S4i review M2) when it leaves
// the state's game without a list -- the game changed -- and is silent
// when the list it has is for this game. Shared with the Result page (S4r).
export function useCharacters(codename: string, loads: number,
                              failed: () => void): CharacterList {
    const [read, setRead] = React.useState<{codename: string;
                                            list: Characters} | null>(null);
    // The game the last read failed for, while no list for it is read.
    const [missed, setMissed] = React.useState<string | null>(null);
    const [rereads, setRereads] = React.useState(0);
    const failedRef = React.useRef(failed);
    failedRef.current = failed;
    const anyRead = React.useRef(false);
    const readRef = React.useRef(read);
    readRef.current = read;

    React.useEffect(() => {
        const shown = () => {
            if (document.visibilityState === "visible") {
                setRereads((count) => count + 1);
            }
        };
        document.addEventListener("visibilitychange", shown);
        return () => document.removeEventListener("visibilitychange", shown);
    }, []);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Characters>>("/api/v1/characters", () => live)
            .then((body) => {
                if (live) {
                    anyRead.current = true;
                    setMissed(null);
                    setRead({codename, list: body.data ?? {}});
                }
            }, (error) => {
                console.error(error);
                if (!live) {
                    return;
                }
                if (!anyRead.current) {
                    failedRef.current();
                } else if (readRef.current?.codename !== codename) {
                    setMissed(codename);
                    showSnackbar(i18n.t("top8_characters_failed"), "error");
                }
            });
        return () => {
            live = false;
        };
    }, [codename, loads, rereads]);

    const reload = React.useCallback(() => {
        setMissed(null);
        setRereads((count) => count + 1);
    }, []);
    const list = read && read.codename === codename ? read.list : null;
    return {list, failed: list === null && missed === codename, reload};
}

function Top8Content() {
    const {language} = usePanelShell();
    const {top8, codename, colors} = useTop8State();
    // The page's lists are read (the first time: the page waits for them).
    const [loaded, setLoaded] = React.useState(false);
    // Null while the country data is not loaded (NOT_READY).
    const [countries, setCountries] = React.useState<CountryEntry[] | null>(null);
    const [designs, setDesigns] = React.useState<LayoutEntry[]>([]);
    const [design, setDesign] = React.useState<LayoutEntry | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped by the reload button: loads the page's lists again.
    const [loads, setLoads] = React.useState(0);
    const [importing, setImporting] = React.useState(false);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();
    const mounted = React.useRef(true);
    const fail = React.useCallback(() => setLoadFailed(true), []);
    const characters = useCharacters(codename, loads, fail);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("top8_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        const isLive = () => live;
        Promise.all([
            getWithRetry<Envelope<CountryEntry[]>>("/api/v1/countries", isLive)
                .then((body) => body.data ?? [], (error) => {
                    // The country data is not loaded yet: the page goes on
                    // without it (S4i review L5), the country fields
                    // disabled until it is read again.
                    if (apiErrorStatus(error) === 409) {
                        return null;
                    }
                    throw error;
                }),
            getWithRetry<Envelope<LayoutEntry[]>>(
                "/api/v1/layouts?kind=top8", isLive),
            getWithRetry<Envelope<LayoutEntry>>(
                "/api/v1/layouts/selected/top8", isLive).then(
                (body) => body.data,
                (error) => {
                    // No Top 8 design installed: none is shown.
                    if (apiErrorStatus(error) === 404) {
                        return null;
                    }
                    throw error;
                }),
        ]).then(([countryList, designsBody, selected]) => {
            if (live) {
                setCountries(countryList);
                setDesigns(designsBody.data);
                setDesign(selected);
                setLoaded(true);
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

    // ── the players ──

    // Nothing was written. A 400 is an identifier the catalogs do not have
    // (any more): a list read before the countries or the game's assets
    // changed. A 409 CONFLICT is a character picked on the list of a game
    // that is not the one loaded any more: the list is read again.
    const reloadCharacters = characters.reload;
    const playerRefusal: Refusal = React.useCallback((status, code) => {
        if (status === 400) {
            return i18n.t("top8_pick_refused");
        }
        if (status === 409 && code === "NOT_READY") {
            return i18n.t("top8_countries_not_ready");
        }
        if (status === 409 && code === "CONFLICT") {
            reloadCharacters();
            return i18n.t("top8_pick_other_game");
        }
        return i18n.t("request_failed");
    }, [reloadCharacters]);
    const sendPlayer = React.useCallback(
        (slot: string, fields: PlayerFields) => write(
            () => apiPatch(`/api/v1/top8/players/${slot}`, fields,
                           WRITE_OPTIONS),
            playerRefusal),
        [write, playerRefusal]);

    // ── the caption and the scores ──

    const sendCaption = React.useCallback(
        (label: string) => write(
            () => apiPatch("/api/v1/top8", {phaseLabel: label}, WRITE_OPTIONS)),
        [write]);
    const applyScores = React.useCallback(
        (scores: Record<string, [number, number]>) => write(
            () => apiPost("/api/v1/top8/scores", scores, WRITE_OPTIONS)),
        [write]);
    const resetScores = React.useCallback(
        () => write(() => apiDelete("/api/v1/top8/scores", WRITE_OPTIONS)),
        [write]);

    // ── the import ──

    // Accepted (202): it runs on the provider's worker and the cards follow
    // the state when it lands. 409: none is loaded (NOT_READY), or one is
    // running already (CONFLICT) -- told by the answer's code.
    const importResults = React.useCallback(() => {
        setImporting(true);
        write(() => apiPost("/api/v1/bracket:refresh", {}, WRITE_OPTIONS),
              (status, code) => {
                  if (status !== 409) {
                      return i18n.t("request_failed");
                  }
                  return code === "NOT_READY"
                      ? i18n.t("top8_import_no_tournament") : null;
              })
            .then(() => {
                showSnackbar(i18n.t("top8_import_started"), "info");
            }, (error: unknown) => {
                if (apiErrorStatus(error) === 409
                        && apiErrorCode(error) !== "NOT_READY") {
                    showSnackbar(i18n.t("top8_import_running"), "info");
                }
            })
            .finally(() => {
                if (mounted.current) {
                    setImporting(false);
                }
            });
    }, [write]);

    // ── the colours and the design ──

    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) =>
            write(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                 WRITE_OPTIONS)),
        [write]);
    const selectDesign = React.useCallback((id: string) => {
        write(() => apiPut("/api/v1/layouts/selected/top8", {id},
                           WRITE_OPTIONS))
            .then((text) => setDesign((JSON.parse(text) as Envelope<LayoutEntry>).data))
            .catch(() => undefined);
    }, [write]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("top8_reload")}
                        </Button>
                    }
                >
                    {i18n.t("top8_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!loaded) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("top8_loading")}</Typography>
            </Box>
        );
    }

    // Seeded at launch (core StateSchema, from the settings or Autona's
    // defaults): missing only until the state has arrived.
    const tournamentTheme = colors.tournament?.themeColor;
    const tournamentAccent = colors.tournament?.accentColor;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;
    const phaseLabel = typeof top8?.phaseLabel === "string"
        ? top8.phaseLabel : "";
    // What keeps the selects disabled, and how to read it again.
    const notices = (countries === null || characters.failed) && (
        <>
            {countries === null && (
                <Alert
                    severity="info"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("top8_reload")}
                        </Button>
                    }
                >
                    {i18n.t("top8_countries_unavailable")}
                </Alert>
            )}
            {characters.failed && (
                <Alert
                    severity="warning"
                    action={
                        <Button color="inherit" size="small"
                                onClick={characters.reload}>
                            {i18n.t("top8_characters_reload")}
                        </Button>
                    }
                >
                    {i18n.t("top8_characters_unavailable")}
                </Alert>
            )}
        </>
    );

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
            <Box sx={{width: {md: 440}, flexShrink: 0, overflowY: {md: "auto"},
                      p: 2}}>
                <Stack spacing={2}>
                    <Button
                        variant="contained"
                        disabled={importing}
                        onClick={importResults}
                    >
                        {i18n.t("top8_import")}
                    </Button>
                    <PlayersCard
                        players={top8?.players}
                        countries={countries}
                        characters={characters.list}
                        game={codename}
                        send={sendPlayer}
                        notices={notices}
                    />
                    <ScoresCard
                        phaseLabel={phaseLabel}
                        matches={top8?.matches}
                        players={top8?.players}
                        sendCaption={sendCaption}
                        apply={applyScores}
                        reset={resetScores}
                    />
                    <FeatureColorCard
                        title={i18n.t("top8_colors")}
                        theme={colors.feature?.themeColor ?? tournamentTheme ?? ""}
                        accent={colors.feature?.accentColor ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                    <ObsUrlCard design={design}
                                note={i18n.t("top8_obs_note")}/>
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <DesignPane
                    designs={designs}
                    design={design}
                    onSelect={selectDesign}
                    label={i18n.t("top8_design")}
                    empty={i18n.t("top8_no_design")}
                />
            </Box>
        </Box>
    );
}

export default function Top8Page() {
    return (
        <FeedbackProvider>
            <Top8Content/>
        </FeedbackProvider>
    );
}
