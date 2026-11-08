import React from "react";
import {shallowEqual, useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState, autonaStore} from "../redux/store";
import {
    apiErrorCode,
    apiErrorStatus,
    apiGetJson,
    apiPatch,
    apiPost,
} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {useDocumentVisibility} from "../PanelShell/useDocumentVisibility";
import {
    MADE_IMAGE_COPY_TIMEOUT_MS,
    canCopyImage,
    copyImage,
    copyImageOnceMade,
} from "../PanelShell/clipboard";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {ColorKind, FeatureColorCard} from "../CommentaryPage/SideCards";
import {generatedUrl} from "../ThumbnailPage/Cards";
import {useCharacters} from "../Top8Page";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {
    GenerateResult,
    LayoutCard,
    PlayerFields,
    PostState,
    PreviewCard,
    ResultLayout,
    ResultPlayerRow,
    ResultRender,
    ResultTeamRow,
    RowKind,
    RowsCard,
    SaveToDbCard,
    TeamFields,
    errorText,
    importErrorText,
} from "./Cards";

// The tournament result's page (大会リザルト, ADR-005 S4r), /web/result: the rows
// of the layout chosen, the import of the event's standings, a preview drawn by
// the real renderer, the making of the image (out/results), its copy and the
// post on X. Shown in the desktop app's shared web view and in a browser alike.
// It writes through /api/v1 only (core's ResultService does the work) and
// follows the state.
//
// The rows: always GET /api/v1/result's, which reads them as they are drawn (a
// character of another game as none, a character lent by the scoreboard), the
// text fields aside, which show the state's as every other page's do. Read
// again after a write and when the state says they changed. A read that was on
// its way while a write was, whichever started first, is dropped: it may not
// have the write in it, and the write's answer reads again, so that no row goes
// back to what it was.
//
// The preview: PREVIEW_DEBOUNCE_MS after a change of what it draws, POST
// :preview, whose answer is the run's revision; the image is GET
// /result/preview?v=<the revision> once result_render has ended with it. Only
// while the page is shown: hidden (another Qt panel in front of the shared
// view, a background tab), nothing is drawn; shown again, it draws, changed or
// not. The state cannot say whether anything did: the thumbnail's background is
// in settings.json, and core reads board 1's unapplied edits, which the
// published state this page follows does not carry.

export const PREVIEW_DEBOUNCE_MS = 300;
// Rows are read soon after a change, several changes at once (a write's answer
// and its echo in the state) being one read.
export const READ_DEBOUNCE_MS = 100;

const FEATURE = "result";
const X_POST_URL = "https://twitter.com/intent/tweet?text=";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// GET /api/v1/result.
interface Description {
    layout: string;
    layouts: ResultLayout[];
    players: ResultPlayerRow[];
    teams: ResultTeamRow[];
    // A preview now draws the sample.
    sample: boolean;
    game: {codename: string; name: string} | null;
    // False on the headless shell, which cannot draw (POST :preview and
    // :generate answer 503 there).
    canRender: boolean;
    render: Partial<ResultRender>;
}

// result_standings (core ResultService).
interface ResultStandings {
    status: "idle" | "running" | "done" | "error";
    error: string;
    revision: number;
}

const IDLE_RENDER: ResultRender = {status: "idle", path: "", error: "",
                                   revision: 0};
const IDLE_STANDINGS: ResultStandings = {status: "idle", error: "", revision: 0};

function readRender(raw: Partial<ResultRender> | undefined): ResultRender {
    if (!raw || typeof raw !== "object") {
        return IDLE_RENDER;
    }
    return {
        status: raw.status ?? "idle",
        path: typeof raw.path === "string" ? raw.path : "",
        error: typeof raw.error === "string" ? raw.error : "",
        revision: typeof raw.revision === "number" ? raw.revision : 0,
    };
}

function readStandings(raw: Partial<ResultStandings> | undefined): ResultStandings {
    if (!raw || typeof raw !== "object") {
        return IDLE_STANDINGS;
    }
    return {
        status: raw.status ?? "idle",
        error: typeof raw.error === "string" ? raw.error : "",
        revision: typeof raw.revision === "number" ? raw.revision : 0,
    };
}

interface ResultStateRoot {
    result_players?: unknown;
    result_teams?: unknown;
    result_layout?: unknown;
    result_render?: Partial<ResultRender>;
    result_standings?: Partial<ResultStandings>;
    colors?: Record<string, {themeColor?: string; accentColor?: string}>;
    tournamentInfo?: Record<string, unknown> & {
        themeColor?: string;
        accentColor?: string;
    };
    game?: {codename?: unknown} | null;
    score?: unknown;
    stream?: unknown;
}

function root(state: ReduxState): ResultStateRoot {
    return state.autonaState.autonaState as unknown as ResultStateRoot;
}

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields {
    return value && typeof value === "object" ? value as Fields : {};
}

function list(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

// A character value of the state, as what tells it from another.
function drawnCharacter(value: unknown) {
    const character = fields(value);
    return [character.en_name ?? character.codename ?? character.name ?? null,
            character.skin ?? null];
}

// Every player on the boards with their characters: what core lends a row
// without a character (ResultService._FromScoreboard).
function scoreboardPlayers(state: ReduxState) {
    return Object.values(fields(root(state).score)).map((board) => {
        const teams = fields(fields(board).team);
        return ["1", "2"].map((team) =>
            Object.values(fields(fields(teams[team]).player)).map((value) => {
                const player = fields(value);
                return [player.name,
                        Object.values(fields(player.character))
                            .map(drawnCharacter)];
            }));
    });
}

function rowsOf(value: unknown) {
    return Object.entries(fields(value)).map(([slot, raw]) => ({
        slot, row: fields(raw),
        members: list(fields(raw).members).map(fields),
    }));
}

// What GET /result reads the rows from, as one string: a change of it is a read.
function rowsSignature(state: ReduxState): string {
    const data = root(state);
    return JSON.stringify({
        players: data.result_players,
        teams: data.result_teams,
        scoreboard: scoreboardPlayers(state),
        game: data.game?.codename,
        layout: data.result_layout,
    });
}

// What the picture is drawn from, as one string: a change of it is a new
// preview. Only what it reads (the payload of core's BuildPayload), so that a
// row's X handle draws nothing. The picture's colours are followed apart; the
// background is settings.json's, which a page shown again draws.
function previewSignature(state: ReduxState): string {
    const data = root(state);
    const tournament = fields(data.tournamentInfo);
    const drawn = (value: unknown) => rowsOf(value).map(
        ({slot, row, members}) => [
            slot, row.rank, row.name, row.sponsor,
            drawnCharacter(row.character),
            list(row.characters).map(drawnCharacter),
            members.map((member) => [member.name,
                                     drawnCharacter(member.character)]),
        ]);
    return JSON.stringify({
        players: drawn(data.result_players),
        teams: drawn(data.result_teams),
        scoreboard: scoreboardPlayers(state),
        tournament: [tournament.tournamentName, tournament.eventName,
                     tournament.startAt, tournament.numEntrants,
                     tournament.bannerVersion],
        game: data.game?.codename,
        layout: data.result_layout,
    });
}

function colorsOf(state: ReduxState) {
    const data = root(state);
    return {
        theme: data.colors?.[FEATURE]?.themeColor,
        accent: data.colors?.[FEATURE]?.accentColor,
        tournamentTheme: data.tournamentInfo?.themeColor,
        tournamentAccent: data.tournamentInfo?.accentColor,
    };
}

// All the state says of what the picture is made from, its colours too.
function pictureSignature(state: ReduxState): string {
    return JSON.stringify([previewSignature(state), colorsOf(state)]);
}

// What the post's text is made of (core's PostText), the player database aside:
// a change of it is a read of the text.
function postSignature(state: ReduxState): string {
    const data = root(state);
    const tournament = fields(data.tournamentInfo);
    const told = (value: unknown) => rowsOf(value).map(
        ({row, members}) => [row.rank, row.name, row.sponsor, row.twitter,
                             members.map((member) => [member.name,
                                                      member.twitter])]);
    return JSON.stringify({
        players: told(data.result_players),
        teams: told(data.result_teams),
        tournament: [tournament.tournamentName, tournament.eventName,
                     tournament.startAt, tournament.numEntrants, tournament.url],
        stream: fields(data.stream).url,
        layout: data.result_layout,
    });
}

// Rows as read, the ones that did not change kept as the same objects: their
// memoized cards are not rendered again.
function keepRows<T>(before: T[] | undefined, after: T[]): T[] {
    return after.map((row, index) => {
        const old = before?.[index];
        return old !== undefined && JSON.stringify(old) === JSON.stringify(row)
            ? old : row;
    });
}

// What the run `revision` of this page's came to, by what the state says of the
// runs; null: it has not ended. `from` is the file saved before it.
function runOutcome(render: ResultRender, revision: number,
                    from: string): GenerateResult | null {
    if (render.revision < revision) {
        return null;
    }
    if (render.revision === revision) {
        if (render.status === "done") {
            return {status: "done", path: render.path};
        }
        return render.status === "error"
            ? {status: "error", error: render.error} : null;
    }
    // A later run already (two state updates seen as one): the generation
    // ended before it, with a new file or without.
    return render.path && render.path !== from
        ? {status: "done", path: render.path}
        : {status: "error", error: ""};
}

// What the run comes to, from the store and not from the page, which may have
// gone by then (another panel shown): the image is made all the same, and the
// clipboard is waiting for it. No end within the deadline is a failure, and only
// the server can say that there was none: a page frozen meanwhile (another panel
// shown, HiddenPageLifecycle) comes back with its timers due ahead of the
// messages that came in, its store behind.
function endOfRun(revision: number, from: string): Promise<GenerateResult> {
    return new Promise((resolve) => {
        const end = (outcome: GenerateResult) => {
            clearTimeout(timer);
            unsubscribe();
            resolve(outcome);
        };
        const look = () => {
            const outcome = runOutcome(
                readRender(root(autonaStore.getState()).result_render),
                revision, from);
            if (outcome) {
                end(outcome);
            }
        };
        const timer = setTimeout(() => {
            apiGetJson<Envelope<Description>>("/api/v1/result").then(
                (body) => runOutcome(readRender(body.data.render), revision, from),
                () => null,
            ).then((outcome) => end(outcome ?? {status: "error", error: ""}));
        }, MADE_IMAGE_COPY_TIMEOUT_MS);
        const unsubscribe = autonaStore.subscribe(look);
        look();
    });
}

// What a refused write says; null: nothing (the caller tells it). `code` is
// /api/v1's error code (CONFLICT, NOT_READY, ...).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;

// What is being typed goes now, ahead of what follows (serial writes): a click
// takes the focus away from it in most browsers, not in all.
function leaveField() {
    const active = document.activeElement;
    if (active instanceof HTMLInputElement
            || active instanceof HTMLTextAreaElement) {
        active.blur();
    }
}

// Whether a write of a row changes the picture: an X handle alone does not.
function changesPicture(next: PlayerFields | TeamFields): boolean {
    return Object.entries(next).some(([key, value]) => (key === "members"
        ? Object.values(value as NonNullable<TeamFields["members"]>).some(
            (member) => Object.keys(member).some((name) => name !== "twitter"))
        : key !== "twitter"));
}

function namesCharacter(next: PlayerFields | TeamFields): boolean {
    return "characters" in next
        || Object.values((next as TeamFields).members ?? {}).some(
            (member) => "character" in member);
}

// The clipboard is asked now for what `made` is going to be (in the click). A
// failure of the making is told by the making, which says why. An image made and
// not copied is told after "saved", which would leave the operator pasting what
// the clipboard held before; with the making still on its way, now as well.
function copyOnceMade(made: Promise<string>, copied: string, failed: string) {
    let ended = false;
    const end = () => {
        ended = true;
    };
    made.then(end, end);
    copyImageOnceMade(made).then(
        () => showSnackbar(i18n.t(copied), "success"),
        (error) => {
            console.error(error);
            const tell = () => showSnackbar(i18n.t(failed), "error");
            if (!ended) {
                tell();
            }
            made.then(tell, () => undefined);
        });
}

function ResultContent() {
    const {language} = usePanelShell();
    const rawRender = useSelector((state: ReduxState) =>
        root(state).result_render);
    const render = readRender(rawRender);
    const rawStandings = useSelector((state: ReduxState) =>
        root(state).result_standings);
    const standings = readStandings(rawStandings);
    // By value: a write's echo may bring the same colours in new objects.
    const colors = useSelector(colorsOf, shallowEqual);
    const codename = useSelector((state: ReduxState) => {
        const game = root(state).game?.codename;
        return typeof game === "string" ? game : "";
    });
    const rowsSeen = useSelector(rowsSignature);
    const drawnSeen = useSelector(previewSignature);
    const postSeen = useSelector(postSignature);
    const {visible, reveals} = useDocumentVisibility();

    const [description, setDescription] = React.useState<Description | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped by the reload button: loads the page again.
    const [loads, setLoads] = React.useState(0);
    // The newest revision a preview of this page's was answered with.
    const [previewAsked, setPreviewAsked] = React.useState(0);
    // A generation of this page's is on its way.
    const [generating, setGenerating] = React.useState(false);
    const [result, setResult] = React.useState<GenerateResult | null>(null);
    const [imageRevision, setImageRevision] = React.useState(0);
    // No generation is asked for while one awaits its answer (twice).
    const [asking, setAsking] = React.useState(false);
    const askingRef = React.useRef(false);
    // Asked for with nothing to draw: the page says so while that lasts.
    const [noData, setNoData] = React.useState(false);
    const [postState, setPostState] = React.useState<PostState>("loading");
    // The import of this page's on its way, and what the last one came to.
    const [importRevision, setImportRevision] = React.useState<number | null>(null);
    const [importNote, setImportNote] = React.useState<
        {severity: "success" | "error" | "info"; text: string} | null>(null);
    const [confirmingImport, setConfirmingImport] = React.useState(false);
    // A move awaits its answer and the read after it: until then a card may not
    // hold the row it says, and its fields are not to be edited.
    const [settling, setSettling] = React.useState(false);

    const fail = React.useCallback(() => setLoadFailed(true), []);
    const characters = useCharacters(codename, loads, fail);

    // A refusal for want of the access token is told once: every write after
    // it is refused too.
    const toldToken = React.useRef(false);
    // Counted as each write of the rows or the layout starts and ends: a read
    // that was on its way meanwhile is dropped.
    const writesStarted = React.useRef(0);
    const writesEnded = React.useRef(0);
    const reading = React.useRef(false);
    const readAgain = React.useRef(false);
    const mounted = React.useRef(true);
    const serial = useSerialRequests();
    // The post's text, read ahead: a click cannot wait for it. Only the newest
    // read's answer is taken.
    const postText = React.useRef("");
    const postReads = React.useRef(0);
    // Counted as each write of what the picture is made from starts.
    const drawnWrites = React.useRef(0);
    // What the image this page made last was made from: the writes before it
    // and what the state said at its end. null: not known, as for an image from
    // before the page opened. What the state does not say (the background in
    // settings.json, board 1's unapplied edits) may change while the page is
    // hidden, so that being shown again forgets it.
    const madeFrom = React.useRef<{writes: number; signature: string} | null>(null);

    const visibleRef = React.useRef(visible);
    visibleRef.current = visible;
    const canRender = description?.canRender ?? true;
    const canRenderRef = React.useRef(canRender);
    canRenderRef.current = canRender;
    const loaded = description !== null;
    const loadedRef = React.useRef(loaded);
    loadedRef.current = loaded;
    const renderRef = React.useRef(render);
    renderRef.current = render;
    const sampleRef = React.useRef(description?.sample ?? false);
    sampleRef.current = description?.sample ?? false;
    // Set where the answers are taken and not by a render: a click between an
    // answer and the next render is not a second generation.
    const generatingRef = React.useRef(false);
    const previewTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const readTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const postTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            [previewTimer, readTimer, postTimer].forEach((timer) => {
                if (timer.current !== null) {
                    clearTimeout(timer.current);
                    timer.current = null;
                }
            });
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("result_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    const apply = React.useCallback((next: Description) => {
        setDescription((before) => ({
            ...next,
            players: keepRows(before?.players, next.players),
            teams: keepRows(before?.teams, next.teams),
        }));
    }, []);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Description>>("/api/v1/result", () => live)
            .then((body) => {
                if (live) {
                    apply(body.data);
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
    }, [loads, apply]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
    }, []);

    // The rows again, one read at a time; asked for meanwhile, once more.
    const read = React.useCallback(() => {
        if (reading.current) {
            readAgain.current = true;
            return;
        }
        reading.current = true;
        // Started while a write was on its way: it may not have it either.
        const since = writesStarted.current === writesEnded.current
            ? writesStarted.current : null;
        apiGetJson<Envelope<Description>>("/api/v1/result").then((body) => {
            if (mounted.current && writesStarted.current === since
                    && writesEnded.current === since) {
                apply(body.data);
                setSettling(false);
            }
        }, (error) => {
            console.error(error);
            // Not left waiting for a read that did not come.
            if (writesStarted.current === writesEnded.current) {
                setSettling(false);
            }
        }).finally(() => {
            reading.current = false;
            if (readAgain.current && mounted.current) {
                readAgain.current = false;
                read();
            }
        });
    }, [apply]);

    const requestRead = React.useCallback(() => {
        if (readTimer.current !== null) {
            clearTimeout(readTimer.current);
        }
        readTimer.current = setTimeout(() => {
            readTimer.current = null;
            read();
        }, READ_DEBOUNCE_MS);
    }, [read]);

    const readPost = React.useCallback(() => {
        postReads.current += 1;
        const newest = postReads.current;
        const live = () => mounted.current && postReads.current === newest;
        getWithRetry<Envelope<{text: string}>>("/api/v1/result/post", live)
            .then((body) => {
                if (live()) {
                    postText.current = body.data.text;
                    setPostState("ready");
                }
            }, (error) => {
                if (!live()) {
                    return;
                }
                // It has handles from the player database, which a page opened
                // without the token does not read; making the image needs the
                // token too, so that the post is not offered (the page says
                // why). Else it was tried again: a text that may be out of date
                // is not posted, and the page says that too.
                const status = apiErrorStatus(error);
                if (status === 401 || status === 403) {
                    setPostState("token");
                } else {
                    console.error(error);
                    setPostState("failed");
                }
            });
    }, []);

    const readPostAgain = React.useCallback(() => {
        setPostState("loading");
        readPost();
    }, [readPost]);

    const requestPost = React.useCallback(() => {
        if (postTimer.current !== null) {
            clearTimeout(postTimer.current);
        }
        postTimer.current = setTimeout(() => {
            postTimer.current = null;
            readPost();
        }, PREVIEW_DEBOUNCE_MS);
    }, [readPost]);

    // A failed request told: the token once, else what `refusal` makes of its
    // status and code (or the generic failure).
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

    // A write of the rows or the layout: what was on its way to be read is not
    // shown, and they are read again once it is answered.
    const writeRows = React.useCallback(<T,>(request: () => Promise<T>,
                                             refusal?: Refusal,
                                             drawn = true) => {
        if (drawn) {
            drawnWrites.current += 1;
        }
        writesStarted.current += 1;
        return write(request, refusal).finally(() => {
            writesEnded.current += 1;
            if (mounted.current) {
                requestRead();
            }
        });
    }, [write, requestRead]);

    // 503: the shell did not draw -- the headless one cannot, the desktop's
    // may be shutting down. Not a snackbar: the description is read again and
    // its canRender said (the headless one's on the page), rather than taking
    // the page's renders away for good over one refusal.
    const cannotRender: Refusal = React.useCallback((status) => {
        if (status === 503) {
            read();
            return null;
        }
        return i18n.t("request_failed");
    }, [read]);

    const startPreview = React.useCallback(() => {
        // Queued behind the writes before it: it draws what they saved.
        write(() => apiPost("/api/v1/result:preview", {}, WRITE_OPTIONS),
              cannotRender)
            .then((text) => {
                const revision = (JSON.parse(text) as
                    Envelope<{renderRevision: number}>).data.renderRevision;
                setPreviewAsked((asked) => Math.max(asked, revision));
            }, () => undefined);
    }, [write, cannotRender]);

    const requestPreview = React.useCallback(() => {
        // Hidden: the reveal draws it. Not loaded yet: the first load does.
        if (!visibleRef.current || !canRenderRef.current
                || !loadedRef.current) {
            return;
        }
        if (previewTimer.current !== null) {
            clearTimeout(previewTimer.current);
        }
        previewTimer.current = setTimeout(() => {
            previewTimer.current = null;
            if (!visibleRef.current || !canRenderRef.current) {
                return;  // Hidden meanwhile: drawn once shown.
            }
            startPreview();
        }, PREVIEW_DEBOUNCE_MS);
    }, [startPreview]);

    // The first preview and the post's text, once the page has its rows.
    React.useEffect(() => {
        if (loaded) {
            requestPreview();
            readPost();
        }
    }, [loaded, requestPreview, readPost]);

    // Shown again: read and drawn again, changed or not (the header). Not
    // loaded yet: the first load does.
    const seenReveals = React.useRef(reveals);
    React.useEffect(() => {
        if (seenReveals.current === reveals) {
            return;
        }
        seenReveals.current = reveals;
        madeFrom.current = null;
        if (loadedRef.current) {
            requestRead();
            requestPost();
            requestPreview();
        }
    }, [reveals, requestRead, requestPost, requestPreview]);

    // Hidden: what was not asked for yet is dropped (the reveal does it).
    React.useEffect(() => {
        if (visible) {
            return;
        }
        [previewTimer, readTimer, postTimer].forEach((timer) => {
            if (timer.current !== null) {
                clearTimeout(timer.current);
                timer.current = null;
            }
        });
    }, [visible]);

    // What the state says changed. Not loaded yet, it is kept to be compared
    // with once loaded: what the first read was answered with may be older.
    const seen = React.useRef({rowsSeen, drawnSeen, postSeen, colors, language,
                               loaded: false});
    React.useEffect(() => {
        if (!loaded) {
            return;
        }
        const before = seen.current;
        seen.current = {rowsSeen, drawnSeen, postSeen, colors, language, loaded};
        const drawnChanged = before.drawnSeen !== drawnSeen
            || before.colors !== colors;
        if (drawnChanged && before.loaded) {
            // What was said of the last making went with what it was made from.
            setNoData(false);
            setResult(null);
        }
        if (!visibleRef.current) {
            return;
        }
        if (before.rowsSeen !== rowsSeen) {
            requestRead();
        }
        if (before.postSeen !== postSeen) {
            requestPost();
        }
        // The sample's words are in the language of the program.
        if (drawnChanged || (before.language !== language && sampleRef.current)) {
            requestPreview();
        }
    }, [loaded, rowsSeen, drawnSeen, postSeen, colors, language, requestRead,
        requestPost, requestPreview]);

    // The file made last, whoever asked for it: a page left with a generation
    // on its way says where the image is when it is shown again.
    React.useEffect(() => {
        if (render.path) {
            setResult({status: "done", path: render.path});
        }
    }, [render.path]);

    // The image: the newest run that ended with one (a generation draws the
    // preview too).
    React.useEffect(() => {
        if (render.status === "done" && render.revision > 0) {
            setImageRevision(render.revision);
        }
    }, [render.status, render.revision]);

    // An import of this page's ended: said, and kept on the card. Rows that
    // came with it are read as any change of them is.
    React.useEffect(() => {
        if (importRevision === null || standings.revision < importRevision) {
            return;
        }
        if (standings.revision > importRevision) {
            // Another client's already (two state updates seen as one).
            setImportRevision(null);
            return;
        }
        if (standings.status !== "done" && standings.status !== "error") {
            return;
        }
        setImportRevision(null);
        if (standings.status === "done") {
            const text = i18n.t("result_import_done");
            setImportNote({severity: "success", text});
            showSnackbar(text, "success");
        } else {
            const text = importErrorText(standings.error);
            setImportNote({severity: "error", text});
            showSnackbar(text, "error");
        }
    }, [importRevision, standings.revision, standings.status, standings.error]);

    // ── the writes ──

    const sendLayout = React.useCallback(
        (layout: string) => writeRows(
            () => apiPatch("/api/v1/result", {layout}, WRITE_OPTIONS)),
        [writeRows]);

    // Nothing was written. A 400 of a write with a character is an identifier
    // the catalog does not have (any more): a list read before the game's assets
    // changed. A 409 CONFLICT is a character picked on the list of a game that
    // is not the one loaded any more: the list is read again.
    const reloadCharacters = characters.reload;
    const rowRefusal = React.useCallback(
        (next: PlayerFields | TeamFields): Refusal => (status, code) => {
            if (status === 400) {
                return i18n.t(namesCharacter(next) ? "top8_pick_refused"
                    : "result_row_refused");
            }
            if (status === 409 && code === "CONFLICT") {
                reloadCharacters();
                return i18n.t("top8_pick_other_game");
            }
            return i18n.t("request_failed");
        }, [reloadCharacters]);

    const sendPlayer = React.useCallback(
        (slot: number, next: PlayerFields) => writeRows(
            () => apiPatch(`/api/v1/result/players/${slot}`, next, WRITE_OPTIONS),
            rowRefusal(next), changesPicture(next)),
        [writeRows, rowRefusal]);
    const sendTeam = React.useCallback(
        (slot: number, next: TeamFields) => writeRows(
            () => apiPatch(`/api/v1/result/teams/${slot}`, next, WRITE_OPTIONS),
            rowRefusal(next), changesPicture(next)),
        [writeRows, rowRefusal]);

    const layouts = description?.layouts;
    const layoutId = description?.layout;
    const layout = layouts?.find((entry) => entry.id === layoutId);
    const kind: RowKind = layout?.team ? "teams" : "players";

    const reorder = React.useCallback((order: number[]) => {
        // What is being typed goes to the row it was typed into, ahead of it.
        leaveField();
        setSettling(true);
        writeRows(() => apiPost("/api/v1/result:reorder", {kind, order},
                                WRITE_OPTIONS))
            .catch(() => undefined);
    }, [writeRows, kind]);

    const clear = React.useCallback(() => {
        setNoData(false);
        setResult(null);
        writeRows(() => apiPost("/api/v1/result:clear", {kind, confirm: true},
                                WRITE_OPTIONS))
            .then(() => {
                setImportNote(null);
                showSnackbar(i18n.t("result_cleared"), "success");
            }, () => undefined);
    }, [writeRows, kind]);

    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) => {
            drawnWrites.current += 1;
            return write(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                        WRITE_OPTIONS));
        },
        [write]);

    // ── the standings' import ──

    // 409: there is something to overwrite (asked first), no tournament is
    // loaded (on the page), or one is on its way (said).
    const loadStandings = React.useCallback((confirm: boolean) => {
        setImportNote(null);
        write(() => apiPost("/api/v1/result:loadStandings",
                            confirm ? {confirm: true} : {}, WRITE_OPTIONS),
              (status) => (status === 409 ? null : i18n.t("request_failed")))
            .then((text) => {
                setImportRevision((JSON.parse(text) as
                    Envelope<{standingsRevision: number}>)
                    .data.standingsRevision);
            }, (error: unknown) => {
                if (apiErrorStatus(error) !== 409) {
                    return;
                }
                const code = apiErrorCode(error);
                if (code === "CONFIRMATION_REQUIRED") {
                    setConfirmingImport(true);
                } else if (code === "NOT_READY") {
                    setImportNote({severity: "info",
                                   text: i18n.t("result_import_no_tournament")});
                } else {
                    showSnackbar(i18n.t("result_import_running"), "info");
                }
            });
    }, [write]);

    // ── the player database ──

    const saveToDb = React.useCallback(() => {
        leaveField();
        write(() => apiPost("/api/v1/result:saveToDb", {}, WRITE_OPTIONS))
            .then((text) => {
                const {saved} = (JSON.parse(text) as
                    Envelope<{saved: number}>).data;
                showSnackbar(saved > 0
                    ? i18n.t("result_save_db_done", {count: saved})
                    : i18n.t("result_save_db_none"),
                             saved > 0 ? "success" : "info");
                // A handle the rows do not have may be the database's.
                readPost();
            }, () => undefined);
    }, [write, readPost]);

    // ── the image ──

    // Asks for the image and tells what it comes to, here and not by whoever
    // waits: the answer is the URL of the image made, and refuses when there is
    // none. It goes on when the page goes away: the clipboard may be waiting.
    const startGenerate = React.useCallback((): Promise<string> => {
        askingRef.current = true;
        setAsking(true);
        setNoData(false);
        const from = renderRef.current.path;
        const writes = drawnWrites.current;
        // What is known of what it is going to be drawn from, as it is asked. A
        // change that comes in during the run may or may not be on the picture:
        // it makes the copy make the image again.
        const signature = pictureSignature(autonaStore.getState());
        return write(() => apiPost("/api/v1/result:generate", {}, WRITE_OPTIONS),
                     (status) => (status === 409 || status === 400 ? null
                         : cannotRender(status, undefined)))
            .then((text) => {
                const revision = (JSON.parse(text) as
                    Envelope<{renderRevision: number}>).data.renderRevision;
                askingRef.current = false;
                generatingRef.current = true;
                setAsking(false);
                setResult(null);
                setGenerating(true);
                return endOfRun(revision, from).then((outcome) => {
                    generatingRef.current = false;
                    setGenerating(false);
                    setResult(outcome);
                    if (outcome.status !== "done") {
                        showSnackbar(errorText(outcome.error), "error");
                        throw new Error(outcome.error);
                    }
                    madeFrom.current = {writes, signature};
                    showSnackbar(i18n.t("result_saved", {path: outcome.path}),
                                 "success");
                    return generatedUrl({...IDLE_RENDER, path: outcome.path,
                                         revision});
                });
            }, (error: unknown) => {
                askingRef.current = false;
                setAsking(false);
                const status = apiErrorStatus(error);
                if (status === 409) {
                    showSnackbar(i18n.t("result_busy"), "info");
                } else if (status === 400) {
                    // Nothing to draw: the page says so, once it has read so.
                    setNoData(true);
                    read();
                }
                throw error;
            });
    }, [write, cannotRender, read]);

    const busyMaking = React.useCallback(
        () => askingRef.current || generatingRef.current, []);

    const generate = React.useCallback(() => {
        // The button is disabled meanwhile; a second click in the same frame
        // is not a second generation.
        if (busyMaking()) {
            return;
        }
        leaveField();
        // What it comes to has been told.
        startGenerate().catch(() => undefined);
    }, [busyMaking, startGenerate]);

    // Whether what the page was last told of the rows is what they are now. Its
    // writes are ahead of whatever the click asks for (serial), but what they
    // make of the rows is not known yet.
    const rowsKnown = React.useCallback(
        () => writesStarted.current === writesEnded.current
            && readTimer.current === null && !reading.current, []);

    // The image the operator expects is the one of what they see: made again in
    // the click when anything it is made from has changed since (the X post's
    // parts, with the clipboard asked in the click). Not the case, the one made.
    const copy = React.useCallback(() => {
        if (busyMaking()) {
            return;
        }
        leaveField();
        const from = madeFrom.current;
        if (from && from.writes === drawnWrites.current
                && from.signature === pictureSignature(autonaStore.getState())) {
            copyImage(generatedUrl(renderRef.current)).then(
                () => showSnackbar(i18n.t("result_image_copied"), "success"),
                (error) => {
                    console.error(error);
                    showSnackbar(i18n.t("result_copy_failed"), "error");
                });
            return;
        }
        if (rowsKnown() && sampleRef.current) {
            // Nothing to make: nothing is copied.
            setNoData(true);
            return;
        }
        copyOnceMade(startGenerate(), "result_image_copied", "result_copy_failed");
    }, [busyMaking, rowsKnown, startGenerate]);

    // One click: the image is asked for, the clipboard is asked for an image
    // still to come, and the composer opens -- all in the click, its permission
    // lasting only that long (ADR-005 S4 decision 11). So it cannot wait for
    // anything: the text is the one read ahead, and what goes wrong with the
    // image afterwards is told beside a composer already open. X takes no image
    // URL: the operator pastes it. A page that cannot copy (from another
    // computer over http) still opens it, and the status line says where the
    // image was saved.
    const postToX = React.useCallback(() => {
        if (busyMaking()) {
            return;
        }
        leaveField();
        if (rowsKnown() && sampleRef.current) {
            // Nothing to post: nothing is made, nothing opens. What was just
            // typed may have changed that, which only its answer will say; the
            // click cannot wait for it, so that the composer opens and the
            // making says so if there is nothing to draw.
            setNoData(true);
            return;
        }
        const text = postText.current;
        const made = startGenerate();
        // What it comes to has been told.
        made.catch(() => undefined);
        if (canCopyImage()) {
            copyOnceMade(made, "result_post_copied", "result_post_copy_failed");
        }
        window.open(`${X_POST_URL}${encodeURIComponent(text)}`, "_blank",
                    "noopener");
    }, [busyMaking, rowsKnown, startGenerate]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("result_reload")}
                        </Button>
                    }
                >
                    {i18n.t("result_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!description) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("result_loading")}</Typography>
            </Box>
        );
    }

    const {tournamentTheme, tournamentAccent} = colors;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;
    const previewing = canRender && (render.status === "running"
        || previewAsked > render.revision);
    const previewError = render.status === "error"
        && render.revision === previewAsked ? render.error : "";
    const count = layout?.slots ?? description.players.length;
    // Keeps the page's rows from taking the selects away: what is disabled,
    // and how to read it again.
    const notices = characters.failed && (
        <Alert
            severity="warning"
            action={
                <Button color="inherit" size="small" onClick={characters.reload}>
                    {i18n.t("top8_characters_reload")}
                </Button>
            }
        >
            {i18n.t("top8_characters_unavailable")}
        </Alert>
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
            <Box sx={{width: {md: 520}, flexShrink: 0, overflowY: {md: "auto"},
                      p: 2}}>
                <Stack spacing={2}>
                    <LayoutCard
                        layout={description.layout}
                        layouts={description.layouts}
                        game={description.game}
                        send={sendLayout}
                        importing={standings.status === "running"}
                        importNote={importNote}
                        loadStandings={() => loadStandings(false)}
                        kind={kind}
                        clear={clear}
                    />
                    <RowsCard
                        kind={kind}
                        players={description.players.slice(0, count)}
                        teams={description.teams.slice(0, count)}
                        characters={characters.list}
                        game={codename}
                        sendPlayer={sendPlayer}
                        sendTeam={sendTeam}
                        reorder={reorder}
                        notices={notices}
                        settling={settling}
                        importing={standings.status === "running"}
                    />
                    <SaveToDbCard save={saveToDb}/>
                    <FeatureColorCard
                        title={i18n.t("result_colors")}
                        theme={colors.theme ?? tournamentTheme ?? ""}
                        accent={colors.accent ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <PreviewCard
                    canRender={canRender}
                    render={render}
                    layout={layout}
                    imageRevision={imageRevision}
                    sample={description.sample}
                    previewing={previewing}
                    previewError={previewError}
                    generating={generating}
                    blocked={asking}
                    result={result}
                    noData={noData}
                    postState={postState}
                    generate={generate}
                    copy={copy}
                    postToX={postToX}
                    readPost={readPostAgain}
                />
            </Box>
            <ConfirmDialog
                open={confirmingImport}
                title={i18n.t("result_import")}
                message={i18n.t(`result_import_confirm_${kind}`)}
                confirm={i18n.t("result_import_yes")}
                onConfirm={() => {
                    setConfirmingImport(false);
                    loadStandings(true);
                }}
                onClose={() => setConfirmingImport(false)}
            />
        </Box>
    );
}

export default function ResultPage() {
    return (
        <FeedbackProvider>
            <ResultContent/>
        </FeedbackProvider>
    );
}
