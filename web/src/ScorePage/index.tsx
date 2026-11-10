import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    Checkbox,
    CircularProgress,
    FormControlLabel,
    Stack,
    Tooltip,
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
import socketConnection from "../websocketConnection";
import {AutonaPlayerDbEntry} from "../backendDataTypes";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    MADE_IMAGE_COPY_TIMEOUT_MS,
    canCopyImage,
    copyImageOnceMade,
} from "../PanelShell/clipboard";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {RangeField, ThumbnailRender, generatedUrl} from "../ThumbnailPage/Cards";
import {useCharacters} from "../Top8Page";
import {
    CharacterSent,
    CrewTeamCard,
    Fields,
    MEMBER_RANGE,
    MatchField,
    MemberSent,
    PlayerActions,
    PlayerCard,
    PlayerFieldsSent,
    PostMissingDialog,
    ScorePanel,
    StateLine,
    TeamCard,
    count,
    fields,
    loadBody,
    numbered,
    text,
} from "./Cards";
import {
    FollowRow,
    Poll,
    SetDialog,
    StationDialog,
    readPoll,
    useSecondsLeft,
} from "./SetSelection";

// The SCORE page (スコアボード入力, ADR-005 S4aa), /web/scoreboard: the board
// of the battle format in force, as the Qt SCORE panel shows it -- the match
// bar, the teams and their player cards, the score, the crew board's crews --
// with its 反映 and its X post. Shown in the desktop app's shared web view and
// in a browser alike. It writes through /api/v1 only, one write at a time
// (useSerialRequests), and draws:
//
// - which board: GET /battle-format, read again on the Socket.IO
//   `battle_format` notice (S4z) and after a reconnect.
// - a held board (the desktop's boards 1/2): GET /scoreboards/{n}/pending's
//   live values, 反映 or not, read again on each `scoreboard_pending` for it
//   (S4u: the notice is a hint, a read started after it is the truth).
//   Another board's notice says whether that one has edits waiting: the page
//   offers to apply them.
// - any other board (the crew board, every headless board): the published
//   state the page follows (the redux store's deltas), as every page does.
// - boards 1/2's set selection (S4ab, SetSelection.tsx): 大会の試合を選ぶ
//   opens the page's dialogs (the Qt set and station windows), and the
//   timer row of a board following a set / stream / station: GET
//   /scoreboards/{n}/follow, read again on each `scoreboard_follow` for it
//   and after a reconnect, its seconds counted down by the page.
//
// Every edit goes as the Qt board's inputs make it: a field as a field
// (PATCH .../fields, the country and the region as codes), a pick as an
// identifier (a character's en_name and skin row, a variant's en_name, with
// the game its list was read for), a name candidate as its database key.

export const POST_READ_DEBOUNCE_MS = 300;
const X_POST_URL = "https://twitter.com/intent/tweet?text=";
// The page's own code for a :load whose slot the board does not have.
const NO_SLOT = "NO_SLOT";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// GET /battle-format.
interface BattleFormat {
    format: string;
    board: number;
}

// GET /scoreboards/{n}/pending, for board `board`.
interface Pending {
    board: number;
    held: boolean;
    dirty: boolean;
    data: Fields;
}

interface PendingAnswer {
    held: boolean;
    dirty: boolean;
    board: unknown;
}

// GET /match-names/candidates?board=n.
interface Candidates {
    phase: string[];
    pool: string[];
    match: string[];
}

const NO_CANDIDATES: Candidates = {phase: [], pool: [], match: []};

type PostState = "loading" | "ready" | "token" | "failed";

// What a refused write says; null: nothing (the caller tells it). `code` is
// /api/v1's error code (CONFLICT, NOT_READY, ...).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;

interface ScoreStateRoot {
    score?: Record<string, unknown>;
    display_options?: Record<string, unknown>;
    tournamentInfo?: Record<string, unknown>;
    game?: {codename?: unknown} | null;
    stream?: {url?: unknown} | null;
    thumbnail?: {render?: Partial<ThumbnailRender>} | null;
}

function root(state: ReduxState): ScoreStateRoot {
    return state.autonaState.autonaState as unknown as ScoreStateRoot;
}

const IDLE_RENDER: ThumbnailRender = {status: "idle", path: "", error: "",
                                      revision: 0};

function readRender(raw: Partial<ThumbnailRender> | undefined): ThumbnailRender {
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

// What the thumbnail run `revision` came to: its PNG's path, "" for a run
// that failed, null while it has not ended. `from`: the file saved before.
// A later run seen first (two updates as one) ended this one, with a new
// file or without (the Result page's rule, S4r).
export function runOutcome(render: ThumbnailRender, revision: number,
                           from: string): string | null {
    if (render.revision < revision) {
        return null;
    }
    if (render.revision === revision) {
        if (render.status === "done") {
            return render.path;
        }
        return render.status === "error" ? "" : null;
    }
    return render.path && render.path !== from ? render.path : "";
}

// What the run comes to, from the store and not from the page, which may
// have gone by then (the clipboard is waiting for it). No end within the
// deadline is asked of the server: a page frozen meanwhile comes back with
// its timers due ahead of the messages that came in.
function endOfRun(revision: number, from: string): Promise<string> {
    return new Promise((resolve) => {
        const end = (path: string) => {
            clearTimeout(timer);
            unsubscribe();
            resolve(path);
        };
        const look = () => {
            const path = runOutcome(
                readRender(root(autonaStore.getState()).thumbnail?.render),
                revision, from);
            if (path !== null) {
                end(path);
            }
        };
        const timer = setTimeout(() => {
            apiGetJson<Envelope<ScoreStateRoot>>("/api/v1/state").then(
                (body) => runOutcome(readRender(body.data?.thumbnail?.render),
                                     revision, from),
                () => null,
            ).then((path) => end(path ?? ""));
        }, MADE_IMAGE_COPY_TIMEOUT_MS);
        const unsubscribe = autonaStore.subscribe(look);
        look();
    });
}

// What is being typed goes now, ahead of what follows (serial writes): a
// click takes the focus away from it in most browsers, not in all.
function leaveField() {
    const active = document.activeElement;
    if (active instanceof HTMLInputElement
            || active instanceof HTMLTextAreaElement) {
        active.blur();
    }
}

function list(value: unknown): string[] {
    return Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string") : [];
}

// A read the page cannot draw without, made again when told to (a notice):
// one at a time, and a request during one drops that one's answer and reads
// again once it ends -- the answer may be from before what the notice told
// of. `path` null: nothing to read. Read on mount and when `path` changes.
function useNoticedRead<T>(path: string | null,
                           take: (data: T) => void,
                           fail: (error: unknown) => void): () => void {
    const pathRef = React.useRef(path);
    pathRef.current = path;
    const takeRef = React.useRef(take);
    takeRef.current = take;
    const failRef = React.useRef(fail);
    failRef.current = fail;
    const reading = React.useRef(false);
    const again = React.useRef(false);
    const mounted = React.useRef(true);
    const readRef = React.useRef<() => void>(() => undefined);

    const read = React.useCallback(() => {
        const current = pathRef.current;
        if (current === null || !mounted.current) {
            return;
        }
        if (reading.current) {
            again.current = true;
            return;
        }
        reading.current = true;
        again.current = false;
        const live = () => mounted.current && !again.current
            && pathRef.current === current;
        getWithRetry<Envelope<T>>(current, live).then((body) => {
            if (live()) {
                takeRef.current(body.data);
            }
        }, (error) => {
            if (live()) {
                failRef.current(error);
            }
        }).finally(() => {
            reading.current = false;
            if (mounted.current
                    && (again.current || pathRef.current !== current)) {
                readRef.current();
            }
        });
    }, []);
    readRef.current = read;

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        read();
    }, [path, read]);
    return read;
}

function ScoreContent() {
    const {desktop, language} = usePanelShell();
    const liveScore = useSelector((state: ReduxState) => root(state).score);
    const showSponsor = useSelector((state: ReduxState) =>
        root(state).display_options?.show_sponsor !== false);
    const tournament = useSelector((state: ReduxState) =>
        root(state).tournamentInfo);
    const codename = useSelector((state: ReduxState) => {
        const game = root(state).game?.codename;
        return typeof game === "string" ? game : "";
    });
    const streamUrl = useSelector((state: ReduxState) =>
        text(root(state).stream?.url));
    const initializing = useSelector((state: ReduxState) =>
        state.autonaState.initializing);
    const status = useSelector((state: ReduxState) =>
        state.websocketInfo.status);
    const playerDb = useSelector((state: ReduxState) =>
        state.autonaPlayers.players);
    const players = React.useMemo(
        () => Object.values(playerDb ?? {}) as AutonaPlayerDbEntry[],
        [playerDb]);

    const [format, setFormat] = React.useState<BattleFormat | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped by the reload button: reads the character list again.
    const [loads, setLoads] = React.useState(0);
    const [pending, setPending] = React.useState<Pending | null>(null);
    // The newest read of a board's pending values failed: for want of the
    // token (the published values are drawn), or else (the last read kept).
    const [pendingError, setPendingError] = React.useState<
        {board: number; token: boolean} | null>(null);
    // Whether board 1 / 2 has edits waiting for 反映, as last told.
    const [dirtyByBoard, setDirtyByBoard] = React.useState<Record<number, boolean>>({});
    const [candidates, setCandidates] = React.useState<Candidates>(NO_CANDIDATES);
    const [postState, setPostState] = React.useState<PostState>("loading");
    const [missing, setMissing] = React.useState<string[] | null>(null);
    const [confirmingReset, setConfirmingReset] = React.useState(false);
    const [setsOpen, setSetsOpen] = React.useState(false);
    const [stationsOpen, setStationsOpen] = React.useState(false);
    // What board `board` follows, as read at `at` (Date.now()).
    const [follow, setFollow] = React.useState<{board: number;
                                                poll: Poll | null;
                                                at: number} | null>(null);

    // A refusal for want of the access token is told once: every write
    // after it is refused too.
    const toldToken = React.useRef(false);
    const mounted = React.useRef(true);
    const serial = useSerialRequests();
    const fail = React.useCallback(() => setLoadFailed(true), []);
    const characters = useCharacters(codename, loads, fail);
    // The post's text, read ahead: the composer opens in the click.
    const postText = React.useRef("");
    const postMissing = React.useRef<string[]>([]);
    const postReads = React.useRef(0);
    const postTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    // Counted as each write of the board starts and ends (the thumbnail's
    // making aside): the text read ahead is posted only when it was read
    // with none on its way and none made since (the Result page's rule).
    const writesStarted = React.useRef(0);
    const writesEnded = React.useRef(0);
    // What the text read ahead was read for: the state the page had then
    // (postSeen and the player database) and the writes made by then. null:
    // none read, or read with a write on its way (it may not have it).
    const postBasis = React.useRef<{seen: string; db: unknown;
                                    writes: number} | null>(null);
    const postSeenRef = React.useRef("");
    const playerDbRef = React.useRef<unknown>(playerDb);
    playerDbRef.current = playerDb;
    // The X post's thumbnail is being made: a click meanwhile is dropped
    // (the Result page's busyMaking()).
    const makingRef = React.useRef(false);
    const [making, setMaking] = React.useState(false);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            if (postTimer.current !== null) {
                clearTimeout(postTimer.current);
            }
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("score_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    const tellToken = React.useCallback(() => {
        if (!toldToken.current) {
            toldToken.current = true;
            showSnackbar(i18n.t("stream_token_required"), "error");
        }
    }, []);

    // ── which board ──

    const formatRef = React.useRef(format);
    formatRef.current = format;
    const readFormat = useNoticedRead<BattleFormat>(
        "/api/v1/battle-format",
        (data) => {
            setLoadFailed(false);
            setFormat({format: text(data.format), board: count(data.board, 1)});
        },
        (error) => {
            console.error(error);
            if (formatRef.current === null) {
                setLoadFailed(true);
            } else {
                showSnackbar(i18n.t("score_format_failed"), "error");
            }
        });

    const board = format?.board ?? 0;
    const boardRef = React.useRef(board);
    boardRef.current = board;
    const held12 = board === 1 || board === 2;

    // ── a held board's values ──

    const readPending = useNoticedRead<PendingAnswer>(
        held12 ? `/api/v1/scoreboards/${board}/pending` : null,
        (data) => {
            const n = boardRef.current;
            setPending({board: n, held: Boolean(data.held),
                        dirty: Boolean(data.dirty), data: fields(data.board)});
            setPendingError(null);
            setDirtyByBoard((known) => ({...known,
                                         [n]: Boolean(data.held && data.dirty)}));
        },
        (error) => {
            const n = boardRef.current;
            const status = apiErrorStatus(error);
            const token = status === 401 || status === 403;
            if (token) {
                tellToken();
            } else {
                console.error(error);
            }
            setPendingError({board: n, token});
        });

    // ── what board 1/2 follows (the Qt board's timer row) ──

    const readFollow = useNoticedRead<unknown>(
        held12 ? `/api/v1/scoreboards/${board}/follow` : null,
        (data) => setFollow({board: boardRef.current, poll: readPoll(data),
                             at: Date.now()}),
        // The row stays as last read (READ: no token is asked for).
        (error) => console.error(error));

    React.useEffect(() => {
        const socket = socketConnection.instance();
        const onFormat = () => readFormat();
        const onPending = (message: unknown) => {
            const notice = fields(message);
            const n = Number(notice.board);
            if (n === boardRef.current) {
                readPending();
            } else if (n === 1 || n === 2) {
                setDirtyByBoard((known) => ({...known, [n]: Boolean(notice.dirty)}));
            }
        };
        const onFollow = (message: unknown) => {
            if (Number(fields(message).board) === boardRef.current) {
                readFollow();
            }
        };
        socket.on("battle_format", onFormat);
        socket.on("scoreboard_pending", onPending);
        socket.on("scoreboard_follow", onFollow);
        return () => {
            socket.off("battle_format", onFormat);
            socket.off("scoreboard_pending", onPending);
            socket.off("scoreboard_follow", onFollow);
        };
    }, [readFormat, readPending, readFollow]);

    // Back from a lost connection: what was told meanwhile is not known.
    const lastStatus = React.useRef(status);
    React.useEffect(() => {
        const was = lastStatus.current;
        lastStatus.current = status;
        if (status === "connected" && was !== "connected") {
            readFormat();
            readPending();
            readFollow();
        }
    }, [status, readFormat, readPending, readFollow]);

    const followPoll = held12 && follow?.board === board ? follow.poll : null;
    const followSeconds = useSecondsLeft(followPoll, follow?.at ?? 0,
                                         readFollow);

    // Another board shown: its own windows (the Qt ones are each board's).
    React.useEffect(() => {
        setSetsOpen(false);
        setStationsOpen(false);
    }, [board]);

    // ── the match bar's candidates ──

    const readCandidates = React.useCallback(() => {
        const n = boardRef.current;
        if (n < 1) {
            return;
        }
        apiGetJson<Envelope<Partial<Candidates>>>(
            `/api/v1/match-names/candidates?board=${n}`).then((body) => {
            if (mounted.current && boardRef.current === n) {
                setCandidates({phase: list(body.data?.phase),
                               pool: list(body.data?.pool),
                               match: list(body.data?.match)});
            }
        }, (error) => console.error(error));
    }, []);

    React.useEffect(() => {
        setCandidates(NO_CANDIDATES);
        readCandidates();
    }, [board, readCandidates]);

    // ── what is drawn ──

    const tokenRefused = pendingError?.board === board && pendingError.token;
    const held = held12 && pending?.board === board && pending.held
        && !tokenRefused;
    const heldRef = React.useRef(held);
    heldRef.current = held;
    const drawn = held ? pending!.data : fields(liveScore?.[String(board)]);
    const dirty = held && pending!.dirty;
    const pendingSettled = !held12 || pending?.board === board
        || pendingError?.board === board;
    // The newest read failed (not for want of the token): with the values
    // read before still drawn, said beside them; with none, the page stops
    // there and offers to read them again, as for the first load.
    const pendingFailed = pendingError?.board === board && !pendingError.token;
    const pendingNeverRead = pendingFailed && pending?.board !== board;
    const boardPath = `/api/v1/scoreboards/${board}`;

    // ── the X post's text, read ahead ──

    const readPost = React.useCallback(() => {
        const n = boardRef.current;
        if (n < 1) {
            return;
        }
        if (postTimer.current !== null) {
            clearTimeout(postTimer.current);
            postTimer.current = null;
        }
        postReads.current += 1;
        const newest = postReads.current;
        const live = () => mounted.current && postReads.current === newest;
        // Started with a write on its way: not known to have it (read again
        // once the writes end, below).
        const basis = writesStarted.current === writesEnded.current
            ? {seen: postSeenRef.current, db: playerDbRef.current,
               writes: writesStarted.current}
            : null;
        getWithRetry<Envelope<{text: string; missing: string[]}>>(
            `/api/v1/scoreboards/${n}/post`, live).then((body) => {
            if (live()) {
                postText.current = text(body.data?.text);
                postMissing.current = list(body.data?.missing);
                postBasis.current = basis;
                setPostState("ready");
            }
        }, (error) => {
            if (!live()) {
                return;
            }
            postBasis.current = null;
            // It has handles from the player database, which a page opened
            // without the token does not read.
            const status = apiErrorStatus(error);
            if (status === 401 || status === 403) {
                setPostState("token");
            } else {
                console.error(error);
                setPostState("failed");
            }
        });
    }, []);

    // Read soon: after what it is made of changes, and once the writes end.
    const schedulePost = React.useCallback(() => {
        if (postTimer.current !== null) {
            clearTimeout(postTimer.current);
        }
        postTimer.current = setTimeout(() => {
            postTimer.current = null;
            readPost();
        }, POST_READ_DEBOUNCE_MS);
    }, [readPost]);

    // Whether the text read ahead is the one the server makes now (Qt's
    // PostToX reads it after the field being typed is committed).
    const postKnown = React.useCallback(() => {
        const basis = postBasis.current;
        return basis !== null && basis.seen === postSeenRef.current
            && basis.db === playerDbRef.current
            && basis.writes === writesStarted.current
            && writesStarted.current === writesEnded.current;
    }, []);

    // ── writes ──

    // A failed request told: the token once, else what `refusal` makes of
    // its status and code (or the generic failure).
    const report = React.useCallback((error: unknown, refusal?: Refusal) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            tellToken();
            return;
        }
        const message = refusal ? refusal(status, apiErrorCode(error))
            : i18n.t("request_failed");
        if (message) {
            showSnackbar(message, "error");
        }
    }, [tellToken]);

    // `counted`: a write of the board, which the post's text may be made
    // of (all but the thumbnail's making).
    const write = React.useCallback(<T,>(request: () => Promise<T>,
                                         refusal?: Refusal, counted = true) => {
        if (counted) {
            writesStarted.current += 1;
        }
        return serial(request).catch((error: unknown) => {
            report(error, refusal);
            throw error;
        }).finally(() => {
            if (!counted) {
                return;
            }
            writesEnded.current += 1;
            if (mounted.current
                    && writesStarted.current === writesEnded.current) {
                schedulePost();
            }
        });
    }, [serial, report, schedulePost]);

    // What the board is now, read in the write queue (after the writes
    // before it): a held board's live values, else the published state.
    const readLive = React.useCallback(async (path: string): Promise<Fields> => {
        if (heldRef.current) {
            const answer = await apiGetJson<Envelope<{board?: unknown}>>(
                `${path}/pending`, WRITE_OPTIONS);
            return fields(answer.data?.board);
        }
        const answer = await apiGetJson<Envelope<unknown>>(path, WRITE_OPTIONS);
        return fields(answer.data);
    }, []);

    // Fire and forget: what it came to has been told.
    const run = React.useCallback(<T,>(request: () => Promise<T>,
                                       refusal?: Refusal) => {
        write(request, refusal).catch(() => undefined);
    }, [write]);

    const fieldRefusal: Refusal = React.useCallback((status, code) => {
        if (status === 409 && code === "NOT_READY") {
            return i18n.t("top8_countries_not_ready");
        }
        return status === 400 ? i18n.t("top8_pick_refused")
            : i18n.t("request_failed");
    }, []);

    const reloadCharacters = characters.reload;
    const characterRefusal: Refusal = React.useCallback((status, code) => {
        if (status === 409 && code === "CONFLICT") {
            reloadCharacters();
            return i18n.t("top8_pick_other_game");
        }
        return status === 400 ? i18n.t("top8_pick_refused")
            : i18n.t("request_failed");
    }, [reloadCharacters]);

    const actions: PlayerActions = React.useMemo(() => ({
        sendFields: (team: number, slot: number, sent: PlayerFieldsSent) =>
            write(() => apiPatch(
                `${boardPath}/teams/${team}/players/${slot}/fields`, sent,
                WRITE_OPTIONS), fieldRefusal),
        sendCharacter: (team: number, slot: number, row: number,
                        sent: CharacterSent) =>
            write(() => apiPatch(
                `${boardPath}/teams/${team}/players/${slot}/characters/${row}`,
                sent, WRITE_OPTIONS), characterRefusal),
        // :load names the set's team: after a swap, the state team the card
        // is in is the other one (read as the request goes). Its 404s share
        // the code NOT_FOUND (a record not in the database, a slot the team
        // does not have): the slot is looked for in the same read first.
        load: (team: number, slot: number, player: AutonaPlayerDbEntry) =>
            write(async () => {
                const live = await readLive(boardPath);
                const slots = fields(fields(fields(live.team)[String(team)])
                    .player);
                if (!(String(slot) in slots)) {
                    throw Object.assign(new Error(`no slot ${team}/${slot}`),
                                        {status: 404, code: NO_SLOT});
                }
                const setTeam = live.swapped === true ? 3 - team : team;
                return apiPost(
                    `${boardPath}/teams/${setTeam}/players/${slot}:load`,
                    loadBody(player), WRITE_OPTIONS);
            }, (status, code) => {
                if (status !== 404) {
                    return i18n.t("request_failed");
                }
                return code === NO_SLOT ? i18n.t("score_load_no_slot")
                    : i18n.t("score_load_missing");
            }),
        clear: (team: number, slot: number) => run(() => apiPost(
            `${boardPath}/teams/${team}/players/${slot}:clear`,
            {confirm: true}, WRITE_OPTIONS)),
        move: (team: number, first: number, second: number) => {
            leaveField();
            run(() => apiPost(`${boardPath}/teams/${team}/players:swap`,
                              {first, second}, WRITE_OPTIONS));
        },
    }), [boardPath, write, run, readLive, fieldRefusal, characterRefusal]);

    const sendMatch = React.useCallback((key: string, value: string) => write(
        () => apiPatch(board === 3 ? `${boardPath}/set` : `${boardPath}/match`,
                       {[key]: value}, WRITE_OPTIONS)), [board, boardPath, write]);
    const sendBestOf = React.useCallback((value: number) => write(
        () => apiPatch(`${boardPath}/match`, {bestOf: value}, WRITE_OPTIONS)),
        [boardPath, write]);
    const sendTeamName = React.useCallback((team: number, value: string) =>
        write(() => apiPatch(`${boardPath}/set`, {teamName: {team, value}},
                             WRITE_OPTIONS)), [boardPath, write]);
    const sendLosers = React.useCallback((team: number, value: boolean) =>
        write(() => apiPatch(`${boardPath}/set`, {losers: {team, value}},
                             WRITE_OPTIONS)), [boardPath, write]);
    const sendPlayers = React.useCallback((value: number) => write(
        () => apiPatch(`${boardPath}/set`, {players: value}, WRITE_OPTIONS)),
        [boardPath, write]);
    const sendColor = React.useCallback((team: number, color: string) => write(
        () => apiPost(`${boardPath}/teams/${team}/color`, {color},
                      WRITE_OPTIONS)), [boardPath, write]);
    const sendMember = React.useCallback(
        (team: number, member: number, sent: MemberSent) => write(
            () => apiPatch(`${boardPath}/teams/${team}/members/${member}`,
                           sent, WRITE_OPTIONS)), [boardPath, write]);
    const sendDisplayName = React.useCallback((value: string) => write(
        () => apiPatch("/api/v1/tournament/info",
                       {displayTournamentName: value}, WRITE_OPTIONS)),
        [write]);
    const sendHideSponsor = React.useCallback((hide: boolean) => write(
        () => apiPatch("/api/v1/display-options", {show_sponsor: !hide},
                       WRITE_OPTIONS)), [write]);
    const [hideSponsor, chooseHideSponsor] = useStateChoice(
        !showSponsor, sendHideSponsor);

    const team1 = fields(fields(drawn.team)["1"]);
    const team2 = fields(fields(drawn.team)["2"]);
    const scores: [number, number] = [count(team1.score), count(team2.score)];

    const adjust = React.useCallback((team: number, delta: number) => {
        run(() => apiPost(`${boardPath}/teams/${team}/score:adjust`, {delta},
                          WRITE_OPTIONS));
    }, [boardPath, run]);
    // score:set takes both: the other team's as it is when the write goes
    // (read in the queue), not as drawn -- the Qt score field writes its own
    // team only (S4x), and another writer may have changed the other.
    const setScore = React.useCallback((team: number, value: number) =>
        write(async () => {
            const live = await readLive(boardPath);
            const other = count(fields(fields(live.team)[String(3 - team)])
                .score);
            return apiPost(`${boardPath}/score:set`,
                           team === 1 ? {team1: value, team2: other}
                               : {team1: other, team2: value},
                           WRITE_OPTIONS);
        }), [boardPath, write, readLive]);
    const resetScore = React.useCallback(() => {
        run(() => apiPost(`${boardPath}/score:reset`, {}, WRITE_OPTIONS));
    }, [boardPath, run]);
    const swap = React.useCallback(() => {
        leaveField();
        run(() => apiPost(`${boardPath}:swap-teams`, {}, WRITE_OPTIONS));
    }, [boardPath, run]);
    const applyBoard = React.useCallback((n: number) => {
        leaveField();
        run(() => apiPost(`/api/v1/scoreboards/${n}:apply`, {}, WRITE_OPTIONS));
    }, [run]);
    const resetAll = React.useCallback(() => {
        setConfirmingReset(false);
        run(() => apiPost(`${boardPath}:reset-all`, {confirm: true},
                          WRITE_OPTIONS));
    }, [boardPath, run]);

    // The timer row's cancel: the board stops following (the Qt row's
    // StopFollowing(forget=True)); the row goes with the read after it.
    const stopFollow = React.useCallback(() => {
        write(() => apiPost(`${boardPath}:stop-follow`, {}, WRITE_OPTIONS),
              undefined, false).then(readFollow, () => undefined);
    }, [boardPath, write, readFollow]);

    // ── the X post ──

    // Read again soon after what it is made of changes.
    const postSeen = JSON.stringify([board, drawn, text(tournament?.tournamentName),
                                     text(tournament?.displayTournamentName),
                                     streamUrl]);
    postSeenRef.current = postSeen;
    React.useEffect(() => {
        schedulePost();
    }, [postSeen, playerDb, schedulePost]);

    React.useEffect(() => {
        setPostState("loading");
    }, [board]);

    const readPostAgain = React.useCallback(() => {
        setPostState("loading");
        readPost();
    }, [readPost]);

    // One click: the composer opens in it (its permission lasts only that
    // long), with the text read ahead. Boards 1/2 copy the match thumbnail,
    // made now (POST /thumbnail:generate), the clipboard asked in the click
    // for an image still to come. A refusal of the text (something missing)
    // opens nothing and says what, as the Qt button does. A text read before
    // what the click committed, or before the state the page shows, is not
    // posted: it is read again and the operator asked to click once more
    // (the click cannot wait for it).
    const postToX = React.useCallback(() => {
        if (makingRef.current) {
            return;
        }
        leaveField();
        if (!postKnown()) {
            // A write on its way reads it once the writes end.
            if (writesStarted.current === writesEnded.current) {
                readPost();
            }
            showSnackbar(i18n.t("score_post_reread"), "info");
            return;
        }
        if (postMissing.current.length > 0) {
            setMissing(postMissing.current);
            return;
        }
        const n = boardRef.current;
        if (n !== 3 && canCopyImage()) {
            const from = readRender(
                root(autonaStore.getState()).thumbnail?.render).path;
            makingRef.current = true;
            setMaking(true);
            const made = write(
                () => apiPost("/api/v1/thumbnail:generate", {board: n},
                              WRITE_OPTIONS),
                (status) => {
                    if (status === 409) {
                        return i18n.t("score_post_busy");
                    }
                    return status === 503 ? i18n.t("score_post_no_render")
                        : i18n.t("request_failed");
                }, false)
                .then((answer) => {
                    const revision = (JSON.parse(answer) as
                        Envelope<{renderRevision: number}>).data.renderRevision;
                    return endOfRun(revision, from);
                })
                .then((path) => {
                    if (!path) {
                        showSnackbar(i18n.t("score_post_thumbnail_failed"),
                                     "error");
                        throw new Error("the thumbnail was not made");
                    }
                    return generatedUrl({...IDLE_RENDER, path, revision: 0});
                });
            const settled = () => {
                makingRef.current = false;
                if (mounted.current) {
                    setMaking(false);
                }
            };
            made.then(settled, settled);
            copyOnceMade(made);
        }
        window.open(`${X_POST_URL}${encodeURIComponent(postText.current)}`,
                    "_blank", "noopener");
    }, [write, readPost, postKnown]);

    // ── the page ──

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((value) => value + 1);
        readFormat();
    }, [readFormat]);

    const reloadPending = React.useCallback(() => {
        setPendingError(null);
        readPending();
    }, [readPending]);

    // The set and station dialogs, after the page in every return below: they
    // stay mounted through the loading ones (another board shown reads its
    // values first), so what was set in them stays, as in the Qt windows
    // (hidden, not destroyed). Open on boards 1/2 only.
    const dialogs = (
        <>
            <SetDialog
                open={setsOpen && held12}
                board={board}
                write={write}
                onClose={() => setSetsOpen(false)}
                onTrack={() => {
                    setSetsOpen(false);
                    setStationsOpen(true);
                }}
            />
            <StationDialog
                open={stationsOpen && held12}
                board={board}
                write={write}
                onClose={() => setStationsOpen(false)}
            />
        </>
    );

    if (loadFailed || pendingNeverRead) {
        return (
            <>
                <Box component="main" sx={{p: 3}}>
                    <Alert
                        severity="error"
                        action={
                            <Button color="inherit" size="small"
                                    onClick={loadFailed ? reload
                                        : reloadPending}>
                                {i18n.t("score_reload")}
                            </Button>
                        }
                    >
                        {i18n.t("score_load_failed")}
                    </Alert>
                </Box>
                {dialogs}
            </>
        );
    }
    if (!format || !pendingSettled || initializing) {
        return (
            <>
                <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                           alignItems: "center"}}>
                    <CircularProgress size={20}/>
                    <Typography>{i18n.t("score_loading")}</Typography>
                </Box>
                {dialogs}
            </>
        );
    }

    const tournamentName = text(tournament?.tournamentName);
    const eventName = text(tournament?.eventName);
    // Boards 1/2 other than the one shown (both, on the crew board) with
    // edits waiting, as their notices told: offered to apply.
    const otherDirty = [1, 2].filter((n) => n !== board
        && dirtyByBoard[n] === true);
    const apply = held ? {run: () => applyBoard(board), enabled: dirty} : null;
    const slots1 = numbered(team1.player);
    const slots2 = numbered(team2.player);
    const perTeam = Math.max(slots1.length, slots2.length);
    // The Qt board's rule: the team board, or a singles board built with
    // more than one player in team 1.
    const named = board === 2 || slots1.length > 1;
    const crewMembers = count(team1.memberNum, 1);

    const playerCards = (team: number, data: Fields, slots: string[]) =>
        slots.map((slot) => (
            <PlayerCard
                key={`${board}-${team}-${slot}`}
                team={team}
                slot={Number(slot)}
                slots={slots.length}
                player={fields(fields(data.player)[slot])}
                showSponsor={showSponsor}
                players={players}
                characters={characters}
                game={codename}
                actions={actions}
                apply={apply}
            />
        ));

    const page = (
        <Box component="main" sx={{p: 2, bgcolor: "background.default",
                                   minHeight: "100vh"}}>
            <Stack spacing={2}>
                {otherDirty.map((other) => (
                    <Alert
                        key={other}
                        severity="info"
                        action={
                            <Button color="inherit" size="small"
                                    onClick={() => applyBoard(other)}>
                                {i18n.t("score_apply")}
                            </Button>
                        }
                    >
                        {i18n.t("score_other_dirty", {
                            board: i18n.t(`score_board_name_${other}`)})}
                    </Alert>
                ))}
                {pendingFailed && (
                    <Alert
                        severity="warning"
                        action={
                            <Button color="inherit" size="small"
                                    onClick={readPending}>
                                {i18n.t("score_reload")}
                            </Button>
                        }
                    >
                        {i18n.t("score_pending_failed")}
                    </Alert>
                )}
                <Card variant="outlined" component="section"
                      aria-label={i18n.t("score_section")}>
                    <CardContent>
                        <Stack spacing={1.5}>
                            <Stack direction="row" alignItems="center" spacing={1}>
                                <Typography variant="h6" component="h2"
                                            sx={{flex: 1}}>
                                    {i18n.t("score_section")}
                                </Typography>
                                <Button
                                    variant="outlined"
                                    size="small"
                                    disabled={postState !== "ready" || making}
                                    onClick={postToX}
                                >
                                    {i18n.t("score_post")}
                                </Button>
                                {desktop && board !== 3 && (
                                    <Button
                                        variant="outlined"
                                        size="small"
                                        onClick={() => window.open(
                                            board === 2 ? "/team" : "/scoreboard",
                                            "_blank", "noopener")}
                                    >
                                        {i18n.t("score_open_browser")}
                                    </Button>
                                )}
                            </Stack>
                            {postState === "token" && (
                                <Typography variant="body2" color="text.secondary">
                                    {i18n.t("score_post_token")}
                                </Typography>
                            )}
                            {postState === "failed" && (
                                <Alert
                                    severity="warning"
                                    action={
                                        <Button color="inherit" size="small"
                                                onClick={readPostAgain}>
                                            {i18n.t("score_post_retry")}
                                        </Button>
                                    }
                                >
                                    {i18n.t("score_post_failed")}
                                </Alert>
                            )}
                            {(tournamentName || eventName) && (
                                <Typography variant="body2" color="text.secondary">
                                    {[tournamentName && i18n.t("score_tournament",
                                                               {name: tournamentName}),
                                      eventName && i18n.t("score_event",
                                                          {name: eventName})]
                                        .filter(Boolean).join("　")}
                                </Typography>
                            )}
                            <StateLine
                                label={i18n.t("score_display_name")}
                                value={text(tournament?.displayTournamentName)}
                                send={sendDisplayName}
                            />
                            {/* In the page (S4ab): wherever it is open, not
                                only in the desktop's shared view. */}
                            {board !== 3 && text(tournament?.url) && (
                                <Box>
                                    <Button variant="outlined" size="small"
                                            onClick={() => setSetsOpen(true)}>
                                        {i18n.t("score_open_sets")}
                                    </Button>
                                </Box>
                            )}
                            {/* The Qt board's timer row: shown while the board
                                follows, with or without a tournament. */}
                            {followPoll && (
                                <FollowRow poll={followPoll}
                                           seconds={followSeconds}
                                           onCancel={stopFollow}/>
                            )}
                            <Stack direction={{xs: "column", sm: "row"}} spacing={1}>
                                <MatchField
                                    label={i18n.t("score_phase")}
                                    value={text(drawn.phase)}
                                    send={(value) => sendMatch("phase", value)}
                                    options={candidates.phase}
                                    onOpen={readCandidates}
                                />
                                <MatchField
                                    label={i18n.t("score_pool")}
                                    value={text(drawn.pool)}
                                    send={(value) => sendMatch("pool", value)}
                                    options={candidates.pool}
                                    onOpen={readCandidates}
                                />
                                <MatchField
                                    label={i18n.t("score_match")}
                                    value={text(drawn.match)}
                                    send={(value) => sendMatch("match", value)}
                                    options={candidates.match}
                                    onOpen={readCandidates}
                                />
                            </Stack>
                            {board !== 3 && (
                                <Stack direction="row" alignItems="center"
                                       spacing={1}>
                                    <FormControlLabel
                                        sx={{flex: 1}}
                                        control={
                                            <Checkbox
                                                size="small"
                                                checked={hideSponsor}
                                                onChange={(event) =>
                                                    chooseHideSponsor(
                                                        event.target.checked)}
                                            />
                                        }
                                        label={i18n.t("score_hide_sponsor")}
                                    />
                                    <RangeField
                                        label={i18n.t("score_best_of")}
                                        value={count(drawn.best_of)}
                                        range={[0, 99]}
                                        percent={false}
                                        step={1}
                                        width={110}
                                        send={sendBestOf}
                                    />
                                </Stack>
                            )}
                        </Stack>
                    </CardContent>
                </Card>

                {board === 3 ? (
                    <>
                        <Stack direction="row" spacing={1} alignItems="center"
                               flexWrap="wrap" useFlexGap>
                            <RangeField
                                label={i18n.t("score_members")}
                                value={crewMembers}
                                range={MEMBER_RANGE}
                                percent={false}
                                step={1}
                                width={120}
                                send={sendPlayers}
                            />
                            <Box sx={{flex: 1}}/>
                            <Button variant="outlined" onClick={resetScore}>
                                {i18n.t("score_reset_stocks")}
                            </Button>
                            <Button variant="outlined" onClick={swap}>
                                {i18n.t("score_swap")}
                            </Button>
                        </Stack>
                        <Stack direction={{xs: "column", md: "row"}} spacing={2}>
                            {[team1, team2].map((data, index) => (
                                <CrewTeamCard
                                    key={index + 1}
                                    team={index + 1}
                                    data={data}
                                    members={crewMembers}
                                    sendName={sendTeamName}
                                    sendColor={sendColor}
                                    sendMember={sendMember}
                                />
                            ))}
                        </Stack>
                    </>
                ) : (
                    <>
                        {board === 2 && (
                            <Box>
                                <RangeField
                                    label={i18n.t("score_players_per_team")}
                                    value={perTeam}
                                    range={[0, 99]}
                                    percent={false}
                                    step={1}
                                    width={180}
                                    send={sendPlayers}
                                />
                            </Box>
                        )}
                        {/* The Qt SCORE's layout from sm (600 px) up: the two
                            teams side by side (a team's cards one under the
                            other), the score box under them, then 入力値を
                            リセット | 反映. Stacked on a phone's width. */}
                        <Stack direction={{xs: "column", sm: "row"}} spacing={2}
                               alignItems={{sm: "flex-start"}}
                               data-testid="score-teams">
                            <TeamCard team={1} data={team1} label={board === 2}
                                      named={named} sendName={sendTeamName}
                                      sendLosers={sendLosers}>
                                {playerCards(1, team1, slots1)}
                            </TeamCard>
                            <TeamCard team={2} data={team2} label={board === 2}
                                      named={named} sendName={sendTeamName}
                                      sendLosers={sendLosers}>
                                {playerCards(2, team2, slots2)}
                            </TeamCard>
                        </Stack>
                        <ScorePanel
                            scores={scores}
                            adjust={adjust}
                            set={setScore}
                            reset={resetScore}
                            swap={swap}
                        />
                        <Stack direction="row" spacing={1} alignItems="center"
                               data-testid="score-bottom">
                            <Button color="error" variant="outlined"
                                    onClick={() => setConfirmingReset(true)}>
                                {i18n.t("score_reset_all")}
                            </Button>
                            <Box sx={{flex: 1}}/>
                            {held && (
                                <Tooltip title={i18n.t("score_apply_tooltip")}>
                                    <span>
                                        <Button variant="contained"
                                                disabled={!dirty}
                                                onClick={() => applyBoard(board)}>
                                            {i18n.t("score_apply")}
                                        </Button>
                                    </span>
                                </Tooltip>
                            )}
                        </Stack>
                    </>
                )}
            </Stack>
            <PostMissingDialog missing={missing} onClose={() => setMissing(null)}/>
            <ConfirmDialog
                open={confirmingReset}
                title={i18n.t("score_reset_all")}
                message={i18n.t("score_reset_all_confirm")}
                confirm={i18n.t("score_reset_all")}
                onConfirm={resetAll}
                onClose={() => setConfirmingReset(false)}
            />
        </Box>
    );
    return (
        <>
            {page}
            {dialogs}
        </>
    );
}

// The clipboard is asked now for what `made` is going to be (in the
// click). A failure of the making is told by the making.
function copyOnceMade(made: Promise<string>) {
    let ended = false;
    const end = () => {
        ended = true;
    };
    made.then(end, end);
    copyImageOnceMade(made).then(
        () => showSnackbar(i18n.t("score_post_copied"), "success"),
        (error) => {
            console.error(error);
            const tell = () => showSnackbar(i18n.t("score_post_copy_failed"),
                                            "error");
            if (!ended) {
                tell();
            }
            made.then(tell, () => undefined);
        });
}

export default function ScorePage() {
    return (
        <FeedbackProvider>
            <ScoreContent/>
        </FeedbackProvider>
    );
}
