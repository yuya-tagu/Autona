import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {
    autonaPlayersSlice,
    autonaStateSlice,
    websocketInfoSlice,
} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import {openScoreSets} from "../PanelShell/hostApi";
import {LOAD_ATTEMPTS, LOAD_RETRY_MS} from "../PanelShell/requests";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import {
    MADE_IMAGE_COPY_TIMEOUT_MS,
    copyImageOnceMade,
} from "../PanelShell/clipboard";
import socketConnection from "../websocketConnection";
import table from "../Top8Page/characterPick.cases.json";
import setCases from "./setRows.cases.json";
import ScorePage, {POST_READ_DEBOUNCE_MS, runOutcome} from "./index";
import {loadBody} from "./Cards";

// The SCORE page in React (ADR-005 S4aa): the board of the battle format in
// force (GET /battle-format, read again on `battle_format`), a held board
// drawn from GET .../pending (read again on `scoreboard_pending`) and any
// other from the state, every edit written through /api/v1 one at a time.

jest.setTimeout(30000);

jest.mock("../websocketConnection", () => {
    const handlers = {};
    const socket = {
        connected: false,
        emit: jest.fn(),
        handlers,
        // Not jest.fn: CRA resets mocks' implementations before each test.
        on: (event, handler) => {
            (handlers[event] = handlers[event] ?? new Set()).add(handler);
        },
        off: (event, handler) => {
            handlers[event]?.delete(handler);
        },
    };
    return {__esModule: true, default: {instance: () => socket}};
});

jest.mock("../PanelShell/clipboard", () => ({
    ...jest.requireActual("../PanelShell/clipboard"),
    canCopyImage: () => true,
    copyImageOnceMade: jest.fn(),
}));

const BOARD = "/api/v1/scoreboards";
const CUP = "https://www.start.gg/tournament/cup/event/singles";
const PHASES = [{id: 11, name: "Pools"}, {id: 12, name: "Top 8"}];
// When the rows were fetched: 09:05 local.
const FETCHED_AT = new Date(2026, 10, 10, 9, 5).getTime() / 1000;

// set_selection.{phases,sets,stations} in the state.
function fetchState(status = "idle", rest = {}) {
    return {status, error: "", done: 0, total: 0, failed: 0, revision: 1, ...rest};
}

function selection(lists = {}) {
    return {phases: fetchState("done"), sets: fetchState(), stations: fetchState(),
            ...lists};
}

// GET /tournament/{phases,sets,stations}.
function listOf(rows, {status = "done", url = CUP, query = null} = {}) {
    return {status, url, fetchedAt: url ? FETCHED_AT : null, rows,
            query: url ? query : null};
}

function noFollow() {
    return {station: null, poll: null, pollingEnabled: false, setId: null};
}

function slot(fields = {}) {
    return {
        team: "", name: "", real_name: "", twitter: "", custom_textbox: "",
        seed: 0, country: {}, state: {}, character: {"1": {}}, ...fields,
    };
}

function board({phase = "", pool = "", match = "", bestOf = 0, scores = [0, 0],
                players = [[slot()], [slot()]], names = ["", ""]} = {}) {
    const team = (index) => ({
        teamName: names[index], losers: false, color: "#ff0000",
        score: scores[index],
        player: Object.fromEntries(players[index].map(
            (player, i) => [String(i + 1), player])),
    });
    return {phase, pool, match, best_of: bestOf, teamsSwapped: false,
            team: {"1": team(0), "2": team(1)}};
}

function crew({members = 3} = {}) {
    const team = (name) => ({
        teamName: name, color: "#ff0000", memberNum: members, activeMember: 0,
        score: 0,
        player: Object.fromEntries(Array.from({length: 9}, (_, i) => [
            String(i + 1), {name: "", stock: 3}])),
    });
    return {phase: "", pool: "", match: "", team: {"1": team(""), "2": team("")}};
}

// What the server has.
let server;
// path -> {status, code} the next requests of it are refused with.
let refuse;
// path -> the answers the next requests of it wait for (holdNext).
let holds;
let revision = 0;

// The next request of `path` waits until the test answers it.
function holdNext(path) {
    let resolve;
    const response = new Promise((done) => {
        resolve = done;
    });
    (holds[path] = holds[path] ?? []).push(response);
    return {
        answer: (data) => act(async () => {
            resolve(answer(200, {ok: true, data}));
        }),
        refuse: (status) => act(async () => {
            resolve(refusal(status));
        }),
    };
}

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function refusal(status, code = "X") {
    return answer(status, {ok: false, error: {code, message: "no"}});
}

function requests() {
    return global.fetch.mock.calls.map(([url, init]) => ({
        method: init?.method ?? "GET",
        path: new URL(url).pathname,
        body: init?.body ? JSON.parse(init.body) : undefined,
    }));
}

function writes(method, path) {
    return requests().filter((call) => call.method === method
        && call.path === path).map((call) => call.body);
}

function reads(path) {
    return requests().filter((call) => call.method === "GET"
        && call.path === path).length;
}

function setState(rest = {}) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                score: {"1": board(), "2": board(), "3": crew()},
                display_options: {show_sponsor: true},
                tournamentInfo: {tournamentName: "", eventName: "",
                                 displayTournamentName: "", url: ""},
                game: {codename: "tgame"},
                stream: {url: ""},
                set_selection: selection(),
                ...rest,
            },
            revision,
        }));
    });
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 10; i++) {
            await Promise.resolve();
        }
    });
}

async function renderPage(desktop = false) {
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "scoreboard", desktop, language: "en"}}>
                <ScorePage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await settle();
}

async function notify(event, message = {}) {
    const socket = socketConnection.instance();
    await act(async () => {
        (socket.handlers[event] ?? new Set()).forEach((handler) => handler(message));
    });
    await settle();
    await settle();
}

function card(team, slotNumber = 1) {
    return screen.getByRole("region", {
        name: i18n.t("score_card", {team, slot: slotNumber})});
}

function tagField(team) {
    return screen.getByRole("combobox",
                            {name: i18n.t("score_player_name", {team})});
}

function sponsorField(team) {
    return within(card(team)).getByRole("textbox",
                                        {name: i18n.t("score_sponsor")});
}

function button(key, options) {
    return screen.getByRole("button", {name: i18n.t(key, options)});
}

async function type(input, value) {
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    await settle();
}

async function openDetails(team) {
    fireEvent.click(within(card(team)).getByRole("button",
                                                 {name: i18n.t("score_details")}));
    await settle();
    await settle();
    return screen.getByRole("dialog");
}

beforeEach(() => {
    jest.useFakeTimers();
    refuse = {};
    holds = {};
    server = {
        format: {format: "singles", board: 1},
        pending: {
            1: {held: true, dirty: false, board: board()},
            2: {held: true, dirty: false, board: board()},
        },
        // GET /scoreboards/{n}: the published board.
        published: {1: board(), 2: board()},
        post: {1: {text: "post 1", missing: []}, 2: {text: "post 2", missing: []},
               3: {text: "post 3", missing: []}},
        swapped: false,
        formatFailures: 0,
        // GET /api/v1/state.
        state: {},
        phases: listOf(PHASES),
        sets: listOf([], {status: "idle", url: ""}),
        stations: listOf([], {status: "idle", url: ""}),
        follow: {1: noFollow(), 2: noFollow()},
    };
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => "visible"});
    window.open = jest.fn();
    // The clipboard waits for the image (set here: CRA resets mocks).
    copyImageOnceMade.mockImplementation(() => new Promise(() => undefined));
    const socket = socketConnection.instance();
    Object.keys(socket.handlers).forEach((key) => delete socket.handlers[key]);
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path, search} = new URL(url);
        const held = holds[path]?.shift();
        if (held) {
            return held;
        }
        const refused = refuse[path];
        if (refused) {
            return refusal(refused.status, refused.code);
        }
        if (method === "GET") {
            if (path === "/api/v1/battle-format") {
                if (server.formatFailures > 0) {
                    server.formatFailures -= 1;
                    return refusal(500);
                }
                return answer(200, {ok: true, data: server.format});
            }
            const pending = path.match(/^\/api\/v1\/scoreboards\/(\d)\/pending$/);
            if (pending) {
                const live = server.pending[pending[1]];
                return answer(200, {ok: true, data: {
                    ...live, board: {...live.board, swapped: server.swapped}}});
            }
            const post = path.match(/^\/api\/v1\/scoreboards\/(\d)\/post$/);
            if (post) {
                return answer(200, {ok: true, data: server.post[post[1]]});
            }
            const follow = path.match(/^\/api\/v1\/scoreboards\/(\d)\/follow$/);
            if (follow) {
                return answer(200, {ok: true, data: server.follow[follow[1]]});
            }
            const list = path.match(/^\/api\/v1\/tournament\/(phases|sets|stations)$/);
            if (list) {
                return answer(200, {ok: true, data: server[list[1]]});
            }
            const published = path.match(/^\/api\/v1\/scoreboards\/(\d)$/);
            if (published) {
                return answer(200, {ok: true, data: {
                    ...server.published[published[1]], swapped: server.swapped}});
            }
            if (path === "/api/v1/state") {
                return answer(200, {ok: true, data: server.state});
            }
            if (path === "/api/v1/match-names/candidates") {
                return answer(200, {ok: true, data: {
                    phase: ["Pools", "Top 8"], pool: ["A"],
                    match: [`Round ${search}`]}});
            }
            if (path === "/api/v1/characters") {
                return answer(200, {ok: true, data: table.characters});
            }
            if (path === "/api/v1/countries") {
                return answer(200, {ok: true, data: [
                    {code: "JP", label: "Japan", asset: "", custom: false},
                    {code: "US", label: "United States", asset: "", custom: false},
                ]});
            }
            if (path === "/api/v1/countries/JP/states") {
                return answer(200, {ok: true, data: {
                    "13": {name: "Tokyo", code: "13"}}});
            }
            if (path === "/api/v1/variants") {
                return answer(200, {ok: true, data: {}});
            }
            return answer(404, {ok: false});
        }
        if (path === "/api/v1/thumbnail:generate") {
            return answer(202, {ok: true, data: {renderRevision: 5}});
        }
        return answer(200, {ok: true, revision: 1});
    });
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("connected"));
        autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
    });
    setState();
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

// ── the board of the format ──────────────────────────────────────────────

test("the format's board is shown, and another format's after its notice", async () => {
    await renderPage();

    expect(reads("/api/v1/battle-format")).toBe(1);
    expect(reads(`${BOARD}/1/pending`)).toBe(1);
    expect(tagField(1)).toBeInTheDocument();
    // The singles board: no team header, no player count.
    expect(screen.queryByText(i18n.t("score_team", {team: 1}))).toBeNull();
    expect(screen.queryByRole("textbox",
                              {name: i18n.t("score_players_per_team")})).toBeNull();
    expect(document.title).toBe(`Autona ${i18n.t("score_title")}`);

    server.format = {format: "team", board: 2};
    server.pending[2] = {held: true, dirty: false, board: board({
        players: [[slot({name: "A1"}), slot({name: "A2"})], [slot(), slot()]],
        names: ["Alpha", ""]})};
    await notify("battle_format");

    expect(reads("/api/v1/battle-format")).toBe(2);
    // Once as the other board at the start (dirty only), once as the shown.
    expect(reads(`${BOARD}/2/pending`)).toBe(2);
    expect(screen.getByRole("textbox", {name: i18n.t("score_players_per_team")}))
        .toHaveValue("2");
    expect(screen.getAllByRole("textbox", {name: i18n.t("score_team_name")})[0])
        .toHaveValue("Alpha");
    expect(card(1, 2)).toBeInTheDocument();

    server.format = {format: "crew", board: 3};
    await notify("battle_format");
    // The crew board has nothing held back: drawn from the state.
    expect(reads(`${BOARD}/3/pending`)).toBe(0);
    expect(screen.getByRole("textbox", {name: i18n.t("score_members")}))
        .toHaveValue("3");
});

test("a reconnect reads the format and the held values again", async () => {
    await renderPage();
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("disconnected"));
    });
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("connected"));
    });
    await settle();
    await settle();

    expect(reads("/api/v1/battle-format")).toBe(2);
    expect(reads(`${BOARD}/1/pending`)).toBe(2);
});

test("a first load that fails says so, and the reload reads it again", async () => {
    server.formatFailures = LOAD_ATTEMPTS;
    await renderPage();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    expect(screen.getByText(i18n.t("score_load_failed"))).toBeInTheDocument();
    expect(reads("/api/v1/battle-format")).toBe(LOAD_ATTEMPTS);

    fireEvent.click(button("score_reload"));
    await settle();
    await settle();
    expect(tagField(1)).toBeInTheDocument();
});

test("a load that fails once is tried again", async () => {
    server.formatFailures = 1;
    await renderPage();
    expect(screen.queryByText(i18n.t("score_load_failed"))).toBeNull();

    await settle(LOAD_RETRY_MS);
    await settle();
    expect(tagField(1)).toBeInTheDocument();
});

// ── a read made again on a notice (useNoticedRead) ───────────────────────

function named(name) {
    return board({players: [[slot({name})], [slot()]]});
}

test("a notice during a read drops that read's answer and reads again", async () => {
    server.pending[1].board = named("First");
    await renderPage();
    const stale = holdNext(`${BOARD}/1/pending`);
    await notify("scoreboard_pending", {board: 1, dirty: true});
    expect(reads(`${BOARD}/1/pending`)).toBe(2);
    await notify("scoreboard_pending", {board: 1, dirty: true});
    // One read at a time.
    expect(reads(`${BOARD}/1/pending`)).toBe(2);

    const fresh = holdNext(`${BOARD}/1/pending`);
    await stale.answer({held: true, dirty: true, board: named("Stale")});
    await settle();
    await settle();
    // It may be from before the second notice: not drawn, read again.
    expect(tagField(1)).toHaveValue("First");
    expect(reads(`${BOARD}/1/pending`)).toBe(3);

    await fresh.answer({held: true, dirty: true, board: named("Fresh")});
    await settle();
    await settle();
    expect(tagField(1)).toHaveValue("Fresh");
});

test("a read of the board shown before a format switch is not drawn", async () => {
    await renderPage();
    const old = holdNext(`${BOARD}/1/pending`);
    await notify("scoreboard_pending", {board: 1, dirty: false});
    server.format = {format: "team", board: 2};
    const next = holdNext(`${BOARD}/2/pending`);
    await notify("battle_format");

    await old.answer({held: true, dirty: false, board: named("Board 1")});
    await settle();
    await settle();
    // Board 1's values are not drawn as board 2's.
    expect(screen.queryByDisplayValue("Board 1")).toBeNull();
    // The start's read of it as the other board, and the shown one's.
    expect(reads(`${BOARD}/2/pending`)).toBe(2);

    await next.answer({held: true, dirty: false, board: named("Board 2")});
    await settle();
    await settle();
    expect(tagField(1)).toHaveValue("Board 2");
});

test("a read no longer wanted while it is tried again is dropped", async () => {
    await renderPage();
    const first = holdNext(`${BOARD}/1/pending`);
    await notify("scoreboard_pending", {board: 1, dirty: false});
    await first.refuse(500);
    await settle();
    // Tried again after LOAD_RETRY_MS; a notice comes meanwhile.
    const retry = holdNext(`${BOARD}/1/pending`);
    await notify("scoreboard_pending", {board: 1, dirty: false});
    await settle(LOAD_RETRY_MS);
    expect(reads(`${BOARD}/1/pending`)).toBe(3);

    await retry.refuse(500);
    await settle();
    await settle();
    // Neither tried a third time nor told: read again at once.
    expect(reads(`${BOARD}/1/pending`)).toBe(4);
    expect(screen.queryByText(i18n.t("score_pending_failed"))).toBeNull();
    expect(tagField(1)).toBeInTheDocument();
});

// ── held and not ─────────────────────────────────────────────────────────

test("a held board is drawn from its pending values, read again on its notice", async () => {
    server.pending[1].board = board({players: [[slot({name: "Unapplied"})],
                                               [slot()]]});
    setState({score: {"1": board({players: [[slot({name: "Published"})],
                                            [slot()]]})}});
    await renderPage();

    expect(tagField(1)).toHaveValue("Unapplied");

    server.pending[1].board = board({players: [[slot({name: "Newer"})],
                                               [slot()]]});
    await notify("scoreboard_pending", {board: 1, dirty: true});
    expect(tagField(1)).toHaveValue("Newer");
    // A delta of the published state does not draw over it.
    setState({score: {"1": board({players: [[slot({name: "Other"})], [slot()]]})}});
    expect(tagField(1)).toHaveValue("Newer");
});

test("a board that is not held is drawn from the state's deltas", async () => {
    server.pending[1] = {held: false, dirty: false, board: board({
        players: [[slot({name: "Live"})], [slot()]]})};
    setState({score: {"1": board({players: [[slot({name: "Live"})], [slot()]]})}});
    await renderPage();

    expect(tagField(1)).toHaveValue("Live");
    expect(screen.queryByRole("button", {name: i18n.t("score_apply")})).toBeNull();
    setState({score: {"1": board({players: [[slot({name: "Changed"})], [slot()]]})}});
    expect(tagField(1)).toHaveValue("Changed");
    expect(reads(`${BOARD}/1/pending`)).toBe(1);
});

test("what is being typed is not drawn over", async () => {
    await renderPage();
    const field = tagField(1);
    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ali"}});

    server.pending[1].board = board({players: [[slot({name: "Someone"})],
                                               [slot()]]});
    await notify("scoreboard_pending", {board: 1, dirty: true});

    expect(tagField(1)).toHaveValue("Ali");
});

test("反映 is offered only with something to apply, the field being typed going first",
     async () => {
    await renderPage();
    const apply = button("score_apply");
    expect(apply).toBeDisabled();

    server.pending[1].dirty = true;
    await notify("scoreboard_pending", {board: 1, dirty: true});
    expect(button("score_apply")).toBeEnabled();

    const field = tagField(1);
    fireEvent.focus(field);
    field.focus();
    fireEvent.change(field, {target: {value: "Typed"}});
    fireEvent.click(button("score_apply"));
    await settle();
    await settle();

    const order = requests().filter((call) => call.method !== "GET")
        .map((call) => call.path);
    expect(order).toEqual([`${BOARD}/1/teams/1/players/1/fields`,
                           `${BOARD}/1:apply`]);
    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`))
        .toEqual([{gamerTag: "Typed"}]);
});

test("the other board's edits waiting are told, and can be applied", async () => {
    await renderPage();
    expect(screen.queryByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_2")}))).toBeNull();

    // Read once when the page opened (dirty only), not again on a notice.
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
    await notify("scoreboard_pending", {board: 2, dirty: true});
    const notice = screen.getByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_2")}));
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
    fireEvent.click(within(notice.closest("[role=alert]"))
        .getByRole("button", {name: i18n.t("score_apply")}));
    await settle();
    expect(writes("POST", `${BOARD}/2:apply`)).toEqual([{}]);

    await notify("scoreboard_pending", {board: 2, dirty: false});
    expect(screen.queryByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_2")}))).toBeNull();
});

const OTHER_DIRTY_2 = () => i18n.t("score_other_dirty", {
    board: i18n.t("score_board_name_2")});

test("edits waiting on the other board before the page opened are told", async () => {
    server.pending[2] = {held: true, dirty: true, board: board()};
    await renderPage();

    const notice = screen.getByText(OTHER_DIRTY_2());
    fireEvent.click(within(notice.closest("[role=alert]"))
        .getByRole("button", {name: i18n.t("score_apply")}));
    await settle();
    expect(writes("POST", `${BOARD}/2:apply`)).toEqual([{}]);
});

test("a read of the other board that a notice overtook is not believed", async () => {
    server.pending[2] = {held: true, dirty: true, board: board()};
    const late = holdNext(`${BOARD}/2/pending`);
    await renderPage();
    expect(screen.queryByText(OTHER_DIRTY_2())).toBeNull();

    await notify("scoreboard_pending", {board: 2, dirty: false});
    await late.answer({held: true, dirty: true, board: board()});
    await settle();
    await settle();
    expect(screen.queryByText(OTHER_DIRTY_2())).toBeNull();
});

test("a reconnect reads the other board again", async () => {
    await renderPage();
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
    server.pending[2].dirty = true;
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("disconnected"));
    });
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("connected"));
    });
    await settle();
    await settle();

    expect(reads(`${BOARD}/2/pending`)).toBe(2);
    expect(screen.getByText(OTHER_DIRTY_2())).toBeInTheDocument();
});

test("the crew board reads boards 1 and 2, and tells only the held ones", async () => {
    server.format = {format: "crew", board: 3};
    server.pending[1] = {held: true, dirty: true, board: board()};
    server.pending[2] = {held: false, dirty: true, board: board()};
    await renderPage();

    expect(reads(`${BOARD}/1/pending`)).toBe(1);
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
    expect(screen.getByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_1")}))).toBeInTheDocument();
    expect(screen.queryByText(OTHER_DIRTY_2())).toBeNull();
});

test("a page without the token draws the published values and says so once", async () => {
    refuse[`${BOARD}/1/pending`] = {status: 401};
    setState({score: {"1": board({players: [[slot({name: "Published"})],
                                            [slot()]]})}});
    await renderPage();

    expect(tagField(1)).toHaveValue("Published");
    expect(await screen.findAllByText(i18n.t("stream_token_required")))
        .toHaveLength(1);
    // 反映 would be refused: not offered.
    expect(screen.queryByRole("button", {name: i18n.t("score_apply")})).toBeNull();

    refuse[`${BOARD}/1/teams/1/score:adjust`] = {status: 403};
    fireEvent.click(button("score_up", {team: 1}));
    await settle();
    await settle();
    // The click closed the first snackbar (click-away); the refusal opens
    // none again.
    expect(writes("POST", `${BOARD}/1/teams/1/score:adjust`)).toEqual([{delta: 1}]);
    expect(screen.queryByText(i18n.t("stream_token_required"))).toBeNull();
});

test("a held board whose values cannot be read at first offers to read them again",
     async () => {
    refuse[`${BOARD}/1/pending`] = {status: 500};
    await renderPage();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    // Not the published values in their place.
    expect(screen.getByText(i18n.t("score_load_failed"))).toBeInTheDocument();
    expect(screen.queryByRole("combobox",
                              {name: i18n.t("score_player_name", {team: 1})}))
        .toBeNull();

    delete refuse[`${BOARD}/1/pending`];
    fireEvent.click(button("score_reload"));
    await settle();
    await settle();
    expect(tagField(1)).toBeInTheDocument();
});

test("a held board read before keeps its values when a later read fails", async () => {
    server.pending[1].board = named("Read");
    await renderPage();
    refuse[`${BOARD}/1/pending`] = {status: 500};
    await notify("scoreboard_pending", {board: 1, dirty: true});
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    expect(screen.getByText(i18n.t("score_pending_failed"))).toBeInTheDocument();
    expect(tagField(1)).toHaveValue("Read");
});

test("on the crew board, boards 1 and 2's edits waiting are told and applied", async () => {
    server.format = {format: "crew", board: 3};
    await renderPage();
    await notify("scoreboard_pending", {board: 1, dirty: true});
    await notify("scoreboard_pending", {board: 2, dirty: true});

    const notices = [1, 2].map((n) => screen.getByText(i18n.t(
        "score_other_dirty", {board: i18n.t(`score_board_name_${n}`)})));
    // Read once at the start (dirty only), not again on the notices.
    expect(reads(`${BOARD}/1/pending`)).toBe(1);
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
    notices.forEach((notice) => fireEvent.click(within(
        notice.closest("[role=alert]")).getByRole(
        "button", {name: i18n.t("score_apply")})));
    await settle();
    await settle();
    expect(writes("POST", `${BOARD}/1:apply`)).toEqual([{}]);
    expect(writes("POST", `${BOARD}/2:apply`)).toEqual([{}]);
});

// ── the player cards ─────────────────────────────────────────────────────

test("a card's fields are sent field by field", async () => {
    await renderPage();

    await type(sponsorField(1), "AB");
    await type(tagField(2), "Bob");
    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`))
        .toEqual([{prefix: "AB"}]);
    expect(writes("PATCH", `${BOARD}/1/teams/2/players/1/fields`))
        .toEqual([{gamerTag: "Bob"}]);
    // Kept to one line of 256: the input does not take more.
    expect(tagField(1)).toHaveAttribute("maxLength", "256");
});

test("the details send the real name, X, the note and the country as a code", async () => {
    await renderPage();
    const dialog = await openDetails(1);

    await type(within(dialog).getByRole("textbox",
                                        {name: i18n.t("score_real_name")}), "Taro");
    await type(within(dialog).getByRole("textbox",
                                        {name: i18n.t("score_twitter")}), "taro_x");
    const note = within(dialog).getByRole("textbox", {name: i18n.t("score_note")});
    expect(note).toHaveAttribute("maxLength", "4096");
    await type(note, "line 1\nline 2");
    const country = within(dialog).getByRole("combobox",
                                             {name: i18n.t("score_country")});
    fireEvent.focus(country);
    fireEvent.change(country, {target: {value: "Jap"}});
    fireEvent.click(await screen.findByRole("option", {name: /Japan/}));
    await settle();

    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`)).toEqual([
        {name: "Taro"}, {twitter: "taro_x"}, {custom_textbox: "line 1\nline 2"},
        {country_code: "JP"},
    ]);
});

test("a region is sent as its code", async () => {
    server.pending[1].board = board({players: [[slot({country: {code: "JP"}})],
                                               [slot()]]});
    await renderPage();
    const dialog = await openDetails(1);

    fireEvent.mouseDown(within(dialog).getByRole("combobox",
                                                 {name: i18n.t("score_region")}));
    fireEvent.click(await screen.findByRole("option", {name: "Tokyo (13)"}));
    await settle();

    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`))
        .toEqual([{state_code: "13"}]);
});

test("a character is sent with the game its list was read for", async () => {
    await renderPage();
    const dialog = await openDetails(1);

    const character = within(dialog).getByRole("combobox",
                                               {name: i18n.t("score_character")});
    fireEvent.focus(character);
    fireEvent.change(character, {target: {value: "Peach"}});
    fireEvent.click(await screen.findByRole("option", {name: "Peach"}));
    await settle();

    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/characters/1`))
        .toEqual([{en_name: "Peach", skin: 0, game: "tgame"}]);
});

test("a character of a game no longer loaded reads the list again", async () => {
    await renderPage();
    const dialog = await openDetails(1);
    const before = reads("/api/v1/characters");
    refuse[`${BOARD}/1/teams/1/players/1/characters/1`] = {
        status: 409, code: "CONFLICT"};

    const character = within(dialog).getByRole("combobox",
                                               {name: i18n.t("score_character")});
    fireEvent.focus(character);
    fireEvent.change(character, {target: {value: "Peach"}});
    fireEvent.click(await screen.findByRole("option", {name: "Peach"}));
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("top8_pick_other_game")))
        .toBeInTheDocument();
    expect(reads("/api/v1/characters")).toBe(before + 1);
});

function loadPlayerDb() {
    act(() => {
        autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({
            "AB | Taro": {gamerTag: "Taro", prefix: "AB", name: "", twitter: "",
                          country_code: "", custom_textbox: ""},
            "Hanako": {gamerTag: "Hanako", prefix: "", name: "", twitter: "",
                       country_code: "", custom_textbox: ""},
        }));
    });
}

test("a name candidate picked loads its record by key", async () => {
    loadPlayerDb();
    await renderPage();
    const field = tagField(1);

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ta"}});
    fireEvent.click(await screen.findByRole("option", {name: "AB | Taro"}));
    await settle();
    await settle();

    expect(writes("POST", `${BOARD}/1/teams/1/players/1:load`))
        .toEqual([{key: "AB | Taro"}]);
    expect(tagField(1)).toHaveValue("Taro");
    // The typed "Ta" is replaced by the pick, not sent.
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);
    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`)).toEqual([]);
});

test("after a swap the load names the set's team", async () => {
    loadPlayerDb();
    server.swapped = true;
    await renderPage();
    const field = tagField(1);

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Han"}});
    fireEvent.click(await screen.findByRole("option", {name: "Hanako"}));
    await settle();
    await settle();

    expect(writes("POST", `${BOARD}/1/teams/2/players/1:load`))
        .toEqual([{key: "Hanako"}]);
});

test("Enter takes a candidate the keyboard moved to, not one the mouse is over", async () => {
    loadPlayerDb();
    await renderPage();
    const field = tagField(1);

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ta"}});
    const before = reads(`${BOARD}/1/pending`);
    fireEvent.mouseMove(await screen.findByRole("option", {name: "AB | Taro"}));
    fireEvent.keyDown(field, {key: "Enter"});
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);
    await settle();
    await settle();
    expect(writes("POST", `${BOARD}/1/teams/1/players/1:load`)).toEqual([]);
    // Not even the load's first step (reading the board in the queue).
    expect(reads(`${BOARD}/1/pending`)).toBe(before);

    fireEvent.keyDown(field, {key: "ArrowDown"});
    fireEvent.keyDown(field, {key: "ArrowDown"});
    await screen.findByRole("option", {name: "AB | Taro"});
    fireEvent.keyDown(field, {key: "Enter"});
    await settle();
    await settle();
    expect(writes("POST", `${BOARD}/1/teams/1/players/1:load`))
        .toEqual([{key: "AB | Taro"}]);
});

test("a candidate without a key is loaded by its tag", () => {
    expect(loadBody({prefixed_tag: "AB | Taro", gamerTag: "Taro"}))
        .toEqual({key: "AB | Taro"});
    expect(loadBody({prefixed_tag: null, gamerTag: " Taro "}))
        .toEqual({tag: "Taro"});
    expect(loadBody({gamerTag: "Hanako"})).toEqual({tag: "Hanako"});
});

test("a slot moves up and down with players:swap", async () => {
    server.pending[1].board = board({players: [[slot(), slot()], [slot()]]});
    await renderPage();

    expect(button("score_move_up", {team: 1, slot: 1})).toBeDisabled();
    expect(button("score_move_down", {team: 1, slot: 2})).toBeDisabled();
    fireEvent.click(button("score_move_down", {team: 1, slot: 1}));
    fireEvent.click(button("score_move_up", {team: 1, slot: 2}));
    await settle();
    await settle();

    expect(writes("POST", `${BOARD}/1/teams/1/players:swap`)).toEqual([
        {first: 1, second: 2}, {first: 2, second: 1}]);
});

test("a move and a swap send the field being typed first", async () => {
    // As the Qt board's CommitFocusedEdit: what is typed goes with its slot,
    // not to the one that moves in.
    server.pending[1].board = board({players: [[slot(), slot()], [slot()]]});
    await renderPage();

    const sponsor = sponsorField(1);
    fireEvent.focus(sponsor);
    sponsor.focus();
    fireEvent.change(sponsor, {target: {value: "TEAM"}});
    fireEvent.click(button("score_move_down", {team: 1, slot: 1}));
    await settle();
    await settle();
    const field = tagField(2);
    fireEvent.focus(field);
    field.focus();
    fireEvent.change(field, {target: {value: "Typed"}});
    fireEvent.click(button("score_swap"));
    await settle();
    await settle();

    expect(requests().filter((call) => call.method !== "GET")
        .map((call) => call.path)).toEqual([
        `${BOARD}/1/teams/1/players/1/fields`,
        `${BOARD}/1/teams/1/players:swap`,
        `${BOARD}/1/teams/2/players/1/fields`,
        `${BOARD}/1:swap-teams`]);
    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`))
        .toEqual([{prefix: "TEAM"}]);
});

test("with one player a team, a card has no title and no moves", async () => {
    await renderPage();
    expect(within(card(1)).queryByText(i18n.t("score_player", {slot: 1})))
        .toBeNull();
    expect(screen.queryByRole("button", {
        name: i18n.t("score_move_down", {team: 1, slot: 1})})).toBeNull();

    server.pending[1].board = board({players: [[slot(), slot()], [slot(), slot()]]});
    await notify("scoreboard_pending", {board: 1, dirty: true});
    expect(within(card(1)).getByText(i18n.t("score_player", {slot: 1})))
        .toBeInTheDocument();
    expect(button("score_move_down", {team: 1, slot: 1})).toBeInTheDocument();
});

test("a card shows its seed, as the Qt card's badge", async () => {
    server.pending[1].board = board({players: [[slot({seed: 3})], [slot()]]});
    await renderPage();

    expect(within(card(1)).getByText(i18n.t("score_seed", {seed: 3})))
        .toBeInTheDocument();
    // None without one.
    const anySeed = new RegExp(i18n.t("score_seed", {seed: "@"}).replace("@", "\\d+"));
    expect(within(card(2)).queryByText(anySeed)).toBeNull();
});

test("the singles board names its teams when team 1 has more than one player",
     async () => {
    // The Qt board's rule: team 1's count (team 2's does not decide it).
    server.pending[1].board = board({players: [[slot()], [slot(), slot()]]});
    await renderPage();
    expect(screen.queryByRole("textbox", {name: i18n.t("score_team_name")}))
        .toBeNull();

    server.pending[1].board = board({players: [[slot(), slot()], [slot()]]});
    await notify("scoreboard_pending", {board: 1, dirty: true});
    expect(screen.getAllByRole("textbox", {name: i18n.t("score_team_name")}))
        .toHaveLength(2);
});

async function pickName(team, typed, name) {
    const field = tagField(team);
    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: typed}});
    fireEvent.click(await screen.findByRole("option", {name}));
    await settle();
    await settle();
}

test("a load says whether the record or the slot is missing", async () => {
    loadPlayerDb();
    await renderPage();
    refuse[`${BOARD}/1/teams/1/players/1:load`] = {status: 404, code: "NOT_FOUND"};

    await pickName(1, "Ta", "AB | Taro");
    expect(await screen.findByText(i18n.t("score_load_missing")))
        .toBeInTheDocument();
    expect(writes("POST", `${BOARD}/1/teams/1/players/1:load`)).toHaveLength(1);

    // The slot gone from the board (not drawn yet): looked for in the same
    // read, and nothing asked of :load.
    server.pending[1].board = board({players: [[], [slot()]]});
    await pickName(1, "Han", "Hanako");
    expect(await screen.findByText(i18n.t("score_load_no_slot")))
        .toBeInTheDocument();
    expect(writes("POST", `${BOARD}/1/teams/1/players/1:load`)).toHaveLength(1);
});

// ── the layout ───────────────────────────────────────────────────────────

test("the teams side by side, the score box under them, then reset and 反映",
     async () => {
    server.format = {format: "team", board: 2};
    server.pending[2].board = board({players: [[slot(), slot(), slot()],
                                               [slot(), slot(), slot()]]});
    await renderPage();

    const team1 = screen.getByRole("region", {name: i18n.t("score_team", {team: 1})});
    const team2 = screen.getByRole("region", {name: i18n.t("score_team", {team: 2})});
    // One row of the two teams and nothing else.
    const row = team1.parentElement;
    expect(team2.parentElement).toBe(row);
    expect(Array.from(row.children)).toEqual([team1, team2]);
    // Each team's cards one under the other, in its own column.
    expect(within(team1).getAllByRole("region").map(
        (region) => region.getAttribute("aria-label"))).toEqual(
        [1, 2, 3].map((n) => i18n.t("score_card", {team: 1, slot: n})));
    // The score box after the row, its reset and swap under its columns.
    const scores = screen.getByRole("region", {name: i18n.t("score_scores")});
    const after = (a, b) => Boolean(
        a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(row.contains(scores)).toBe(false);
    expect(after(row, scores)).toBe(true);
    expect(after(within(scores).getByRole("button", {name: i18n.t("score_down",
                                                                    {team: 2})}),
                 within(scores).getByRole("button",
                                          {name: i18n.t("score_score_reset")})))
        .toBe(true);
    // Then 入力値をリセット | 反映, side by side.
    const resetAll = button("score_reset_all");
    expect(after(scores, resetAll)).toBe(true);
    expect(resetAll.closest("[data-testid=score-bottom]"))
        .toBe(button("score_apply").closest("[data-testid=score-bottom]"));
});

// ── the match bar ────────────────────────────────────────────────────────

test("the match bar writes PATCH match, with the board's candidates", async () => {
    await renderPage();

    expect(reads("/api/v1/match-names/candidates")).toBeGreaterThanOrEqual(1);
    await type(screen.getByRole("combobox", {name: i18n.t("score_match")}),
               "Grand Final");
    const phase = screen.getByRole("combobox", {name: i18n.t("score_phase")});
    fireEvent.focus(phase);
    fireEvent.click(await screen.findByRole("option", {name: "Top 8"}));
    await settle();
    const bestOf = screen.getByRole("textbox", {name: i18n.t("score_best_of")});
    fireEvent.focus(bestOf);
    fireEvent.change(bestOf, {target: {value: "5"}});
    fireEvent.blur(bestOf);
    await settle();

    expect(writes("PATCH", `${BOARD}/1/match`)).toEqual([
        {match: "Grand Final"}, {phase: "Top 8"}, {bestOf: 5}]);
});

test("the crew board's match bar goes through PATCH set", async () => {
    server.format = {format: "crew", board: 3};
    await renderPage();

    await type(screen.getByRole("combobox", {name: i18n.t("score_phase")}),
               "Pools");
    expect(writes("PATCH", `${BOARD}/3/set`)).toEqual([{phase: "Pools"}]);
});

test("Enter takes a match name the keyboard moved to, not one the mouse is over",
     async () => {
    await renderPage();
    const phase = screen.getByRole("combobox", {name: i18n.t("score_phase")});
    fireEvent.focus(phase);
    fireEvent.change(phase, {target: {value: "P"}});
    fireEvent.mouseMove(await screen.findByRole("option", {name: "Pools"}));
    fireEvent.keyDown(phase, {key: "Enter"});
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);
    await settle();
    await settle();
    // What was typed goes, not the name under the mouse.
    expect(writes("PATCH", `${BOARD}/1/match`)).toEqual([{phase: "P"}]);

    fireEvent.keyDown(phase, {key: "ArrowDown"});
    fireEvent.keyDown(phase, {key: "ArrowDown"});
    await screen.findByRole("option", {name: "Pools"});
    fireEvent.keyDown(phase, {key: "Enter"});
    await settle();
    await settle();
    expect(writes("PATCH", `${BOARD}/1/match`)).toEqual([
        {phase: "P"}, {phase: "Pools"}]);
});

test("a match name's list shows every name until something is typed", async () => {
    server.pending[1].board = board({phase: "Top 8"});
    await renderPage();
    const phase = screen.getByRole("combobox", {name: i18n.t("score_phase")});

    // Opened on the board's value: all of them, as the Qt combo's list.
    fireEvent.focus(phase);
    expect(await screen.findByRole("option", {name: "Pools"})).toBeInTheDocument();
    expect(screen.getByRole("option", {name: "Top 8"})).toBeInTheDocument();

    fireEvent.change(phase, {target: {value: "Po"}});
    await settle();
    expect(screen.getByRole("option", {name: "Pools"})).toBeInTheDocument();
    expect(screen.queryByRole("option", {name: "Top 8"})).toBeNull();
});

test("the team name, LOSERS and the sponsor toggle", async () => {
    server.format = {format: "team", board: 2};
    await renderPage();

    await type(screen.getAllByRole("textbox",
                                   {name: i18n.t("score_team_name")})[1], "Beta");
    fireEvent.click(screen.getByRole("checkbox",
                                     {name: i18n.t("score_losers_of", {team: 1})}));
    fireEvent.click(screen.getByRole("checkbox",
                                     {name: i18n.t("score_hide_sponsor")}));
    await settle();

    expect(writes("PATCH", `${BOARD}/2/set`)).toEqual([
        {teamName: {team: 2, value: "Beta"}}, {losers: {team: 1, value: true}}]);
    expect(writes("PATCH", "/api/v1/display-options"))
        .toEqual([{show_sponsor: false}]);
});

test("the displayed tournament name is the tournament's", async () => {
    setState({tournamentInfo: {tournamentName: "Cup", eventName: "Singles",
                               displayTournamentName: "", url: ""}});
    await renderPage();

    // Joined by a full-width space, as the Qt label (the matcher collapses it).
    expect(screen.getByText(`${i18n.t("score_tournament", {name: "Cup"})} `
        + i18n.t("score_event", {name: "Singles"}))).toBeInTheDocument();
    await type(screen.getByRole("textbox", {name: i18n.t("score_display_name")}),
               "Cup #1");
    expect(writes("PATCH", "/api/v1/tournament/info"))
        .toEqual([{displayTournamentName: "Cup #1"}]);
});

// ── the score ────────────────────────────────────────────────────────────

test("the score: ±1, a typed value, reset and swap", async () => {
    server.pending[1].board = board({scores: [1, 2]});
    await renderPage();

    fireEvent.click(button("score_up", {team: 1}));
    fireEvent.click(button("score_down", {team: 2}));
    const score1 = screen.getByRole("textbox",
                                    {name: i18n.t("score_score_of", {team: 1})});
    fireEvent.focus(score1);
    fireEvent.change(score1, {target: {value: "3"}});
    fireEvent.blur(score1);
    fireEvent.click(button("score_score_reset"));
    fireEvent.click(button("score_swap"));
    await settle();
    await settle();

    expect(writes("POST", `${BOARD}/1/teams/1/score:adjust`)).toEqual([{delta: 1}]);
    expect(writes("POST", `${BOARD}/1/teams/2/score:adjust`)).toEqual([{delta: -1}]);
    // Both teams: the other one's as shown.
    expect(writes("POST", `${BOARD}/1/score:set`)).toEqual([{team1: 3, team2: 2}]);
    expect(writes("POST", `${BOARD}/1/score:reset`)).toEqual([{}]);
    expect(writes("POST", `${BOARD}/1:swap-teams`)).toEqual([{}]);
});

async function typeScore(team, value) {
    const field = screen.getByRole("textbox",
                                   {name: i18n.t("score_score_of", {team})});
    fireEvent.focus(field);
    fireEvent.change(field, {target: {value}});
    fireEvent.blur(field);
    await settle();
    await settle();
}

test("a typed score sends the other team's as it is now, not as drawn", async () => {
    server.pending[1].board = board({scores: [1, 2]});
    await renderPage();
    // Changed elsewhere, and not drawn yet.
    server.pending[1].board = board({scores: [5, 2]});

    await typeScore(2, "3");
    // Read in the write queue: the held board's live values.
    expect(writes("POST", `${BOARD}/1/score:set`)).toEqual([{team1: 5, team2: 3}]);
});

test("a board that is not held reads the other team's score from the board", async () => {
    server.pending[1] = {held: false, dirty: false, board: board()};
    await renderPage();
    server.published[1] = board({scores: [0, 6]});
    const before = reads(`${BOARD}/1/pending`);

    await typeScore(1, "2");
    expect(writes("POST", `${BOARD}/1/score:set`)).toEqual([{team1: 2, team2: 6}]);
    expect(reads(`${BOARD}/1`)).toBe(1);
    expect(reads(`${BOARD}/1/pending`)).toBe(before);
});

test("すべての入力値をリセット asks first", async () => {
    await renderPage();

    fireEvent.click(button("score_reset_all"));
    expect(screen.getByText(i18n.t("score_reset_all_confirm"))).toBeInTheDocument();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("score_reset_all")}));
    await settle();

    expect(writes("POST", `${BOARD}/1:reset-all`)).toEqual([{confirm: true}]);
});

// ── the crew board ───────────────────────────────────────────────────────

test("the crew board: members, stocks, who plays, the count and the reset", async () => {
    server.format = {format: "crew", board: 3};
    await renderPage();

    const crew1 = screen.getAllByRole("region",
                                      {name: i18n.t("score_team", {team: 1})})[0];
    await type(within(crew1).getByRole("textbox",
                                       {name: i18n.t("score_member", {member: 2})}),
               "Ken");
    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("score_playing", {team: 1, member: 2})}));
    const stock = within(crew1).getAllByRole("textbox",
                                             {name: i18n.t("score_stock")})[0];
    fireEvent.focus(stock);
    fireEvent.change(stock, {target: {value: "2"}});
    fireEvent.blur(stock);
    const members = screen.getByRole("textbox", {name: i18n.t("score_members")});
    fireEvent.focus(members);
    fireEvent.change(members, {target: {value: "5"}});
    fireEvent.blur(members);
    fireEvent.click(button("score_reset_stocks"));
    await settle();
    await settle();

    expect(writes("PATCH", `${BOARD}/3/teams/1/members/2`)).toEqual([
        {name: "Ken"}, {active: true}]);
    expect(writes("PATCH", `${BOARD}/3/teams/1/members/1`)).toEqual([{stock: 2}]);
    expect(writes("PATCH", `${BOARD}/3/set`)).toEqual([{players: 5}]);
    expect(writes("POST", `${BOARD}/3/score:reset`)).toEqual([{}]);
    expect(screen.queryByRole("button", {name: i18n.t("score_apply")})).toBeNull();
});

test("a crew's post is text only", async () => {
    server.format = {format: "crew", board: 3};
    await renderPage();
    await settle(POST_READ_DEBOUNCE_MS);
    await settle();

    fireEvent.click(button("score_post"));
    await settle();
    await settle();
    expect(window.open).toHaveBeenCalledWith(
        `https://twitter.com/intent/tweet?text=${encodeURIComponent("post 3")}`,
        "_blank", "noopener");
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([]);
    expect(copyImageOnceMade).not.toHaveBeenCalled();
});

// ── the X post ───────────────────────────────────────────────────────────

test("a post missing something says what, and opens nothing", async () => {
    server.post[1] = {text: "", missing: ["team1", "round"]};
    await renderPage();
    await settle(POST_READ_DEBOUNCE_MS);
    await settle();

    fireEvent.click(button("score_post"));
    await settle();

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(i18n.t("score_post_missing_team1")))
        .toBeInTheDocument();
    expect(within(dialog).getByText(i18n.t("score_post_missing_round")))
        .toBeInTheDocument();
    expect(window.open).not.toHaveBeenCalled();
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([]);
});

test("a post opens the composer and copies the thumbnail of its own run", async () => {
    await renderPage();
    await settle(POST_READ_DEBOUNCE_MS);
    await settle();

    fireEvent.click(button("score_post"));
    expect(window.open).toHaveBeenCalledWith(
        `https://twitter.com/intent/tweet?text=${encodeURIComponent("post 1")}`,
        "_blank", "noopener");
    expect(copyImageOnceMade).toHaveBeenCalledTimes(1);
    const made = copyImageOnceMade.mock.calls[0][0];
    let url = null;
    made.then((value) => {
        url = value;
    }, () => undefined);
    await settle();
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([{board: 1}]);

    // An earlier run's end is not this one's.
    setState({thumbnail: {render: {status: "done", path: "out/thumbnails/old.png",
                                   error: "", revision: 4}}});
    await settle();
    expect(url).toBeNull();
    setState({thumbnail: {render: {status: "done", path: "out/thumbnails/new.png",
                                   error: "", revision: 5}}});
    await settle();
    expect(url).toMatch(/\/out\/thumbnails\/new\.png\?v=0$/);
});

const POSTED = (text) => [
    `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}`,
    "_blank", "noopener"];

async function postRead() {
    await settle(POST_READ_DEBOUNCE_MS);
    await settle();
}

test("a post right after typing reads the text again before it posts", async () => {
    await renderPage();
    await postRead();
    const field = tagField(1);
    fireEvent.focus(field);
    field.focus();
    fireEvent.change(field, {target: {value: "Typed"}});
    server.post[1] = {text: "post Typed", missing: []};

    // The field goes first; the text read before it is not posted.
    fireEvent.click(button("score_post"));
    expect(window.open).not.toHaveBeenCalled();
    expect(await screen.findByText(i18n.t("score_post_reread")))
        .toBeInTheDocument();
    expect(writes("PATCH", `${BOARD}/1/teams/1/players/1/fields`))
        .toEqual([{gamerTag: "Typed"}]);
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([]);

    // Not read while the write is on its way.
    expect(reads(`${BOARD}/1/post`)).toBe(1);
    // Read again once the write ended: the next click posts it.
    await settle();
    await postRead();
    expect(reads(`${BOARD}/1/post`)).toBe(2);
    fireEvent.click(button("score_post"));
    expect(window.open).toHaveBeenCalledWith(...POSTED("post Typed"));
});

test("a post after the board changed reads the text again first", async () => {
    await renderPage();
    await postRead();
    server.pending[1].board = named("Newer");
    server.post[1] = {text: "post Newer", missing: []};
    await notify("scoreboard_pending", {board: 1, dirty: true});

    // Before the text was read for it: not posted, read now.
    fireEvent.click(button("score_post"));
    expect(window.open).not.toHaveBeenCalled();
    await settle();
    await settle();
    fireEvent.click(button("score_post"));
    expect(window.open).toHaveBeenCalledWith(...POSTED("post Newer"));
});

test("a click while the post's thumbnail is made is dropped", async () => {
    await renderPage();
    await postRead();

    // A double click: both before React draws the button disabled.
    const post = button("score_post");
    act(() => {
        fireEvent.click(post);
        fireEvent.click(post);
    });
    await settle();
    expect(button("score_post")).toBeDisabled();
    // And one once it is.
    fireEvent.click(button("score_post"));
    expect(window.open).toHaveBeenCalledTimes(1);
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([{board: 1}]);

    // Made: a post again.
    setState({thumbnail: {render: {status: "done", path: "out/thumbnails/new.png",
                                   error: "", revision: 5}}});
    await settle();
    await settle();
    expect(button("score_post")).toBeEnabled();
    fireEvent.click(button("score_post"));
    expect(window.open).toHaveBeenCalledTimes(2);
});

test("a run whose end is not seen is asked of the server at the deadline", async () => {
    server.state = {thumbnail: {render: {status: "done",
                                         path: "out/thumbnails/late.png",
                                         error: "", revision: 5}}};
    await renderPage();
    await postRead();

    fireEvent.click(button("score_post"));
    let url = null;
    copyImageOnceMade.mock.calls[0][0].then((value) => {
        url = value;
    }, () => undefined);
    await settle();
    await settle();
    expect(url).toBeNull();
    expect(reads("/api/v1/state")).toBe(0);

    await settle(MADE_IMAGE_COPY_TIMEOUT_MS);
    await settle();
    expect(reads("/api/v1/state")).toBe(1);
    expect(url).toMatch(/\/out\/thumbnails\/late\.png\?v=0$/);
});

test("what a thumbnail run came to", () => {
    const render = (status, revision, path = "") => ({status, revision, path,
                                                      error: ""});
    expect(runOutcome(render("done", 4, "a.png"), 5, "")).toBeNull();
    expect(runOutcome(render("running", 5), 5, "")).toBeNull();
    expect(runOutcome(render("done", 5, "b.png"), 5, "")).toBe("b.png");
    expect(runOutcome(render("error", 5), 5, "")).toBe("");
    // A later run seen first: this one ended, with a new file or without.
    expect(runOutcome(render("running", 6, "c.png"), 5, "b.png")).toBe("c.png");
    expect(runOutcome(render("running", 6, "b.png"), 5, "b.png")).toBe("");
});

test("ブラウザ版を開く opens the browser version, on the desktop only", async () => {
    await renderPage(true);
    fireEvent.click(button("score_open_browser"));
    expect(window.open).toHaveBeenCalledWith("/scoreboard", "_blank", "noopener");
});

test("in a browser there is no ブラウザ版を開く", async () => {
    await renderPage(false);
    expect(screen.queryByRole("button", {name: i18n.t("score_open_browser")}))
        .toBeNull();
});

test("ブラウザ版を開く opens the team version on the team board, none on the crew's",
     async () => {
    server.format = {format: "team", board: 2};
    await renderPage(true);
    fireEvent.click(button("score_open_browser"));
    expect(window.open).toHaveBeenCalledWith("/team", "_blank", "noopener");

    server.format = {format: "crew", board: 3};
    await notify("battle_format");
    expect(screen.queryByRole("button", {name: i18n.t("score_open_browser")}))
        .toBeNull();
});


// ── the set selection (S4ab) ─────────────────────────────────────────────

const TOURNAMENT = {tournamentName: "Cup", eventName: "", displayTournamentName: "",
                    url: CUP};
const SET_ROWS = setCases.sets.map((item) => item.row);
const STATION_ROWS = setCases.stations.map((item) => item.row);
const SET_CELLS = setCases.sets.map((item) => item.cells);
// What GET /tournament/sets has after a fetch of phase 12.
const FETCHED = {query: {phase: 12, finished: false}};

function selectionState(lists = {}) {
    setState({tournamentInfo: TOURNAMENT, set_selection: selection(lists)});
}

async function selectionPage(lists = {}) {
    selectionState(lists);
    await renderPage();
}

async function openSets() {
    fireEvent.click(button("score_open_sets"));
    await settle();
    await settle();
    return screen.getByRole("dialog", {name: i18n.t("score_sets_title")});
}

async function openStations() {
    const sets = await openSets();
    fireEvent.click(within(sets).getByRole("button",
                                           {name: i18n.t("score_sets_track")}));
    await settle(1000);
    await settle();
    return screen.getByRole("dialog", {name: i18n.t("score_stations_title")});
}

async function closeDialog(dialog) {
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("score_sets_close")}));
    await settle(1000);
}

function tableRows(dialog) {
    return within(within(dialog).getByRole("table")).getAllByRole("row").slice(1);
}

function cellsOf(dialog) {
    return tableRows(dialog).map((row) =>
        within(row).getAllByRole("cell").map((cell) => cell.textContent));
}

function rowWith(dialog, text) {
    return tableRows(dialog).find((row) => within(row).getAllByRole("cell")
        .some((cell) => cell.textContent === text));
}

function statusOf(dialog) {
    return within(dialog).getByRole("status").textContent;
}

function inDialog(dialog, role, key, options) {
    return within(dialog).getByRole(role, {name: i18n.t(key, options)});
}

async function pickPhase(dialog, name) {
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    fireEvent.click(options.find((option) => option.textContent === name));
    await settle(1000);
    await settle();
    return options.map((option) => option.textContent);
}

const refreshes = () => writes("POST", "/api/v1/tournament/sets:refresh");
const loads = (n = 1) => writes("POST", `${BOARD}/${n}:load-set`);

test("大会の試合を選ぶ opens the page's dialog: the lists read, nothing fetched",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    const dialog = await openSets();

    expect(reads("/api/v1/tournament/phases")).toBe(1);
    expect(reads("/api/v1/tournament/sets")).toBe(1);
    // Opening goes to start.gg for nothing (the phases are there), and to
    // the desktop's window no more.
    expect(requests().filter((call) => call.method !== "GET")).toEqual([]);
    expect(reads("/scoreboard1-open-set")).toBe(0);
    // The window's columns, doubles and all (the shared table).
    expect(within(dialog).getAllByRole("columnheader").map((cell) => cell.textContent))
        .toEqual(["score_sets_col_stream", "score_sets_col_station",
                  "score_sets_col_phase", "score_sets_col_pool",
                  "score_sets_col_match"].map((key) => i18n.t(key))
            .concat([i18n.t("score_sets_col_player", {n: 1}),
                     i18n.t("score_sets_col_player", {n: 2})]));
    expect(cellsOf(dialog)).toEqual(SET_CELLS);
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_count",
                                         {n: SET_ROWS.length, at: "09:05"}));
});

test("with no rows the headers stay on one line", async () => {
    // Seen on the desktop (S4ab-2): an empty table's Japanese headers broke
    // at every character, stacked down the column.
    server.sets = listOf([], FETCHED);
    await selectionPage();
    const dialog = await openSets();

    expect(cellsOf(dialog)).toEqual([]);
    const headers = within(dialog).getAllByRole("columnheader");
    // The five and the two players' (as above).
    expect(headers).toHaveLength(7);
    for (const header of headers) {
        expect(window.getComputedStyle(header).whiteSpace).toBe("nowrap");
    }
});

test("the phases are asked for when there are none to read; with none the combo hides",
     async () => {
    server.phases = listOf([], {status: "idle", url: ""});
    await selectionPage({phases: fetchState("idle")});
    let dialog = await openSets();

    expect(writes("POST", "/api/v1/tournament/phases:refresh")).toEqual([{}]);
    expect(refreshes()).toEqual([]);
    expect(within(dialog).queryByRole("combobox")).toBeNull();
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_idle"));
    // Not known yet: an empty list is not "no phases" (a fetch would ask for
    // the whole event), so nothing is fetched -- the button, 完了 neither.
    const fetch = inDialog(dialog, "button", "score_sets_fetch");
    const finished = inDialog(dialog, "checkbox", "score_sets_finished");
    expect(fetch).toBeDisabled();
    fireEvent.click(finished);
    fireEvent.click(finished);
    await settle();
    expect(refreshes()).toEqual([]);

    // Read again once their fetch ends, not while it runs.
    selectionState({phases: fetchState("running", {revision: 2})});
    await settle();
    await settle();
    expect(reads("/api/v1/tournament/phases")).toBe(1);
    expect(fetch).toBeDisabled();
    // An event with no phases: fetched whole, as the window does.
    server.phases = listOf([]);
    selectionState({phases: fetchState("done", {revision: 2})});
    await settle();
    await settle();
    expect(reads("/api/v1/tournament/phases")).toBe(2);
    expect(within(dialog).queryByRole("combobox")).toBeNull();
    expect(fetch).toBeEnabled();
    fireEvent.click(fetch);
    await settle();
    await settle();
    expect(refreshes()).toEqual([{phase: null, finished: false}]);

    // Fetched again: the empty list read before is not this fetch's.
    selectionState({phases: fetchState("running", {revision: 3})});
    await settle();
    expect(fetch).toBeDisabled();
    const phasesRead = holdNext("/api/v1/tournament/phases");
    selectionState({phases: fetchState("done", {revision: 3})});
    await settle();
    expect(fetch).toBeDisabled();
    fireEvent.click(finished);
    await settle();
    expect(refreshes()).toHaveLength(1);
    fireEvent.click(finished);
    await phasesRead.answer(listOf(PHASES));
    await settle();
    expect(within(dialog).getByRole("combobox"))
        .toHaveTextContent(i18n.t("score_sets_phase_none"));
    expect(fetch).toBeEnabled();

    // Opened again with them there: not asked for again.
    await closeDialog(dialog);
    dialog = await openSets();
    expect(writes("POST", "/api/v1/tournament/phases:refresh")).toHaveLength(1);
    // Their fetch failed: asked for again (the window's empty list), and
    // said, with a way to ask again, while it is so.
    await closeDialog(dialog);
    selectionState({phases: fetchState("error", {revision: 4, error: "fetch_failed"})});
    dialog = await openSets();
    expect(writes("POST", "/api/v1/tournament/phases:refresh")).toHaveLength(2);
    expect(within(dialog).getByText(i18n.t("score_sets_phases_failed")))
        .toBeInTheDocument();
    expect(inDialog(dialog, "button", "score_sets_fetch")).toBeDisabled();
    fireEvent.click(inDialog(dialog, "button", "score_reload"));
    await settle();
    expect(writes("POST", "/api/v1/tournament/phases:refresh")).toHaveLength(3);
});

test("phases that cannot be read are said, with a reload; nothing fetched meanwhile",
     async () => {
    refuse["/api/v1/tournament/phases"] = {status: 400};
    await selectionPage();
    const dialog = await openSets();
    expect(within(dialog).getByText(i18n.t("score_sets_phases_read_failed")))
        .toBeInTheDocument();
    expect(inDialog(dialog, "button", "score_sets_fetch")).toBeDisabled();

    delete refuse["/api/v1/tournament/phases"];
    fireEvent.click(inDialog(dialog, "button", "score_reload"));
    await settle();
    await settle();
    expect(within(dialog).queryByText(i18n.t("score_sets_phases_read_failed")))
        .toBeNull();
    expect(within(dialog).getByRole("combobox")).toBeInTheDocument();
    expect(inDialog(dialog, "button", "score_sets_fetch")).toBeEnabled();
});

test("a phase picked fetches its sets, the same one again too; the placeholder none",
     async () => {
    await selectionPage();
    const dialog = await openSets();
    expect(within(dialog).getByRole("combobox"))
        .toHaveTextContent(i18n.t("score_sets_phase_none"));

    expect(await pickPhase(dialog, "Top 8")).toEqual([
        i18n.t("score_sets_phase_none"), i18n.t("score_sets_phase_all"),
        "Pools", "Top 8"]);
    expect(refreshes()).toEqual([{phase: 12, finished: false}]);
    await pickPhase(dialog, "Top 8");
    await pickPhase(dialog, i18n.t("score_sets_phase_all"));
    expect(refreshes()).toEqual([{phase: 12, finished: false},
                                 {phase: 12, finished: false},
                                 {phase: "all", finished: false}]);

    // The placeholder fetches nothing, and the button then asks for a phase.
    await pickPhase(dialog, i18n.t("score_sets_phase_none"));
    fireEvent.click(inDialog(dialog, "button", "score_sets_fetch"));
    await settle();
    expect(refreshes()).toHaveLength(3);
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_choose_phase"));
});

test("the phase shown first: the only one, else the one the rows were fetched with",
     async () => {
    server.phases = listOf([PHASES[0]]);
    await selectionPage();
    let dialog = await openSets();
    expect(within(dialog).getByRole("combobox")).toHaveTextContent("Pools");
    fireEvent.click(inDialog(dialog, "button", "score_sets_fetch"));
    await settle();
    await settle();
    expect(refreshes()).toEqual([{phase: 11, finished: false}]);
    await closeDialog(dialog);

    // Rows of the whole event with its completed sets: their query comes
    // back with them (the window's cache).
    server.phases = listOf(PHASES);
    server.sets = listOf(SET_ROWS, {query: {phase: "all", finished: true}});
    dialog = await openSets();
    expect(within(dialog).getByRole("combobox"))
        .toHaveTextContent(i18n.t("score_sets_phase_all"));
    expect(inDialog(dialog, "checkbox", "score_sets_finished")).toBeChecked();
});

test("fetching: the button waits for the answer and the fetch; 完了 fetches again, complete pairs filter",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage({sets: fetchState("done", {revision: 3})});
    const dialog = await openSets();
    const fetch = inDialog(dialog, "button", "score_sets_fetch");

    const held = holdNext("/api/v1/tournament/sets:refresh");
    fireEvent.click(fetch);
    await settle();
    expect(fetch).toBeDisabled();
    await held.answer({revision: 4});
    await settle();
    expect(fetch).toBeEnabled();
    expect(refreshes()).toEqual([{phase: 12, finished: false}]);

    // The fetch running: the button off, the rows there.
    selectionState({sets: fetchState("running", {revision: 4})});
    await settle();
    expect(fetch).toBeDisabled();
    expect(cellsOf(dialog)).toEqual(SET_CELLS);
    selectionState({sets: fetchState("done", {revision: 4})});
    await settle();
    await settle();
    expect(fetch).toBeEnabled();

    fireEvent.click(inDialog(dialog, "checkbox", "score_sets_finished"));
    await settle();
    await settle();
    expect(refreshes()).toEqual([{phase: 12, finished: false},
                                 {phase: 12, finished: true}]);

    // The page's filter: nothing asked.
    fireEvent.click(inDialog(dialog, "checkbox", "score_sets_complete_pairs"));
    await settle();
    expect(cellsOf(dialog)).toEqual(setCases.sets.filter((item) => item.complete)
        .map((item) => item.cells));
    expect(refreshes()).toHaveLength(2);
});

test("the progress and the status: busy, then the pages; what failed and no tournament",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    const dialog = await openSets();
    expect(within(dialog).queryByRole("progressbar")).toBeNull();

    selectionState({sets: fetchState("running", {revision: 2})});
    await settle();
    expect(within(dialog).getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_fetching"));
    expect(cellsOf(dialog)).toEqual(SET_CELLS);

    selectionState({sets: fetchState("running", {revision: 2, done: 2, total: 8})});
    await settle();
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_fetching_pages",
                                         {done: 2, total: 8}));

    selectionState({sets: fetchState("done", {revision: 2, done: 8, total: 8,
                                              failed: 1})});
    await settle();
    await settle();
    expect(within(dialog).queryByRole("progressbar")).toBeNull();
    expect(statusOf(dialog)).toBe(
        `${i18n.t("score_sets_count", {n: SET_ROWS.length, at: "09:05"})} `
        + i18n.t("score_sets_failed_pages", {n: 1}));

    selectionState({sets: fetchState("error", {revision: 3, error: "fetch_failed"})});
    await settle();
    await settle();
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_failed"));

    refuse["/api/v1/tournament/sets:refresh"] = {status: 409, code: "NOT_READY"};
    fireEvent.click(inDialog(dialog, "button", "score_sets_fetch"));
    await settle();
    await settle();
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_no_tournament"));
});

test("a list that cannot be read is said, with a reload", async () => {
    refuse["/api/v1/tournament/sets"] = {status: 400};
    await selectionPage();
    const dialog = await openSets();
    expect(within(dialog).getByText(i18n.t("score_sets_read_failed")))
        .toBeInTheDocument();

    delete refuse["/api/v1/tournament/sets"];
    server.sets = listOf(SET_ROWS, FETCHED);
    fireEvent.click(inDialog(dialog, "button", "score_reload"));
    await settle();
    await settle();
    expect(within(dialog).queryByText(i18n.t("score_sets_read_failed"))).toBeNull();
    expect(cellsOf(dialog)).toEqual(SET_CELLS);
});

test("a phase core no longer knows (400 / 404) reads the phases again and asks for one",
     async () => {
    await selectionPage();
    const dialog = await openSets();
    expect(reads("/api/v1/tournament/phases")).toBe(1);

    refuse["/api/v1/tournament/sets:refresh"] = {status: 400, code: "PHASE_REQUIRED"};
    await pickPhase(dialog, "Top 8");
    await settle();
    expect(refreshes()).toEqual([{phase: 12, finished: false}]);
    expect(reads("/api/v1/tournament/phases")).toBe(2);
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_choose_phase"));

    refuse["/api/v1/tournament/sets:refresh"] = {status: 404, code: "NOT_FOUND"};
    await pickPhase(dialog, "Pools");
    await settle();
    expect(refreshes()).toEqual([{phase: 12, finished: false},
                                 {phase: 11, finished: false}]);
    expect(reads("/api/v1/tournament/phases")).toBe(3);
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_choose_phase"));

    // Another refusal says only that it failed.
    refuse["/api/v1/tournament/sets:refresh"] = {status: 500};
    await pickPhase(dialog, "Pools");
    await settle();
    expect(reads("/api/v1/tournament/phases")).toBe(3);
    expect(statusOf(dialog)).toBe(i18n.t("request_failed"));
});

test("every header's sort button is named by its column", async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    const sets = await openSets();
    const labels = (dialog) => within(dialog).getAllByRole("columnheader")
        .map((cell) => within(cell).getByRole("button").getAttribute("aria-label"));
    expect(labels(sets)).toEqual(within(sets).getAllByRole("columnheader")
        .map((cell) => cell.textContent));
    // The first one too (the browser left it nameless).
    expect(labels(sets)[0]).toBe(i18n.t("score_sets_col_stream"));

    fireEvent.click(inDialog(sets, "button", "score_sets_track"));
    await settle(1000);
    const stations = screen.getByRole("dialog", {name: i18n.t("score_stations_title")});
    expect(labels(stations)).toEqual(["score_stations_col_type", "score_stations_col_id",
                                     "score_stations_col_stream",
                                     "score_stations_col_identifier"]
        .map((key) => i18n.t(key)));
});

test("another board shown closes the dialogs (each board has its own)", async () => {
    await selectionPage();
    await openSets();
    server.format = {format: "team", board: 2};
    await notify("battle_format");
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();

    await openStations();
    server.format = {format: "singles", board: 1};
    await notify("battle_format");
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
});

test("the search reaches the hidden id; a header sorts, kept from one opening to the next",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    let dialog = await openSets();
    const search = inDialog(dialog, "searchbox", "score_sets_filter");

    fireEvent.change(search, {target: {value: "1006"}});
    expect(cellsOf(dialog)).toEqual([SET_CELLS[5]]);
    fireEvent.change(search, {target: {value: "alice"}});
    expect(cellsOf(dialog)).toEqual([SET_CELLS[4], SET_CELLS[6]]);
    fireEvent.change(search, {target: {value: ""}});

    const player1 = () => cellsOf(dialog).map((cells) => cells[5]);
    // Qt's order is the code units' (Array.prototype.sort's).
    const ascending = SET_CELLS.map((cells) => cells[5]).sort();
    fireEvent.click(inDialog(dialog, "button", "score_sets_col_player", {n: 1}));
    expect(player1()).toEqual(ascending);
    fireEvent.click(inDialog(dialog, "button", "score_sets_col_player", {n: 1}));
    expect(player1()).toEqual([...ascending].reverse());

    await closeDialog(dialog);
    dialog = await openSets();
    expect(player1()).toEqual([...ascending].reverse());
    expect(within(dialog).getByRole("columnheader", {
        name: i18n.t("score_sets_col_player", {n: 1})}))
        .toHaveAttribute("aria-sort", "descending");

    // Enter on a focused header is the header's (it sorts), not the table's:
    // no row is loaded.
    fireEvent.keyDown(inDialog(dialog, "button", "score_sets_col_phase"),
                      {key: "Enter"});
    await settle(1000);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(loads()).toEqual([]);
});

test("the set dialog's search and complete pairs are each board's, its order every board's",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    let dialog = await openSets();
    const player1 = () => cellsOf(dialog).map((cells) => cells[5]);
    const pairs = () => inDialog(dialog, "checkbox", "score_sets_complete_pairs");
    const search = () => inDialog(dialog, "searchbox", "score_sets_filter");
    fireEvent.change(search(), {target: {value: "a"}});
    fireEvent.click(pairs());
    fireEvent.click(inDialog(dialog, "button", "score_sets_col_player", {n: 1}));
    fireEvent.click(inDialog(dialog, "button", "score_sets_col_player", {n: 1}));
    const board1 = player1();
    expect(board1.length).toBeLessThan(SET_ROWS.length);
    await closeDialog(dialog);

    // Board 2's window: nothing typed, no box ticked; the order the same.
    server.format = {format: "team", board: 2};
    await notify("battle_format");
    dialog = await openSets();
    expect(search()).toHaveValue("");
    expect(pairs()).not.toBeChecked();
    expect(player1()).toEqual(SET_CELLS.map((cells) => cells[5]).sort().reverse());
    fireEvent.change(search(), {target: {value: "bravo"}});
    await closeDialog(dialog);

    // Board 1's again: as it was left.
    server.format = {format: "singles", board: 1};
    await notify("battle_format");
    dialog = await openSets();
    expect(search()).toHaveValue("a");
    expect(pairs()).toBeChecked();
    expect(player1()).toEqual(board1);
});

test("a row is loaded by a double click, OK or Enter, stopping the fetch that runs",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage({sets: fetchState("running", {revision: 2})});
    let dialog = await openSets();

    fireEvent.doubleClick(rowWith(dialog, "Team A (A1, A2)"));
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    // The fetch is stopped first.
    expect(requests().filter((call) => call.method === "POST")).toEqual([
        {method: "POST", path: "/api/v1/tournament/sets:cancel", body: {}},
        {method: "POST", path: `${BOARD}/1:load-set`, body: {setId: 1006}}]);

    // None running: nothing to stop.
    selectionState({sets: fetchState("done", {revision: 2})});
    await settle();
    dialog = await openSets();
    fireEvent.click(rowWith(dialog, "BRAVO (BRAVO, BOB)"));
    expect(rowWith(dialog, "BRAVO (BRAVO, BOB)"))
        .toHaveAttribute("aria-selected", "true");
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(loads()).toEqual([{setId: 1006}, {setId: 1005}]);

    dialog = await openSets();
    fireEvent.click(rowWith(dialog, "アルファ"));
    fireEvent.keyDown(within(dialog).getByRole("table"), {key: "Enter"});
    await settle(1000);
    expect(loads()).toEqual([{setId: 1006}, {setId: 1005}, {setId: 1011}]);
    expect(writes("POST", "/api/v1/tournament/sets:cancel")).toHaveLength(1);
});

test("OK / Enter with no row selected load the first row shown, as the windows do",
     async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    await selectionPage();
    let dialog = await openSets();
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(loads()).toEqual([{setId: 1001}]);

    dialog = await openSets();
    fireEvent.change(inDialog(dialog, "searchbox", "score_sets_filter"),
                     {target: {value: "alice"}});
    fireEvent.keyDown(within(dialog).getByRole("table"), {key: "Enter"});
    await settle(1000);
    expect(loads()).toEqual([{setId: 1001}, {setId: 1005}]);

    // Nothing shown: the dialog closes, nothing is loaded. (The search is
    // still there: the window kept it too.)
    dialog = await openSets();
    const search = inDialog(dialog, "searchbox", "score_sets_filter");
    expect(search).toHaveValue("alice");
    fireEvent.change(search, {target: {value: "nobody plays here"}});
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(loads()).toHaveLength(2);
});

test("the station dialog: fetched at each opening, a row followed as it is", async () => {
    await selectionPage();
    let dialog = await openStations();
    expect(screen.queryByRole("dialog", {name: i18n.t("score_sets_title")})).toBeNull();
    expect(writes("POST", "/api/v1/tournament/stations:refresh")).toEqual([{}]);
    expect(reads("/api/v1/tournament/stations")).toBe(0);
    expect(cellsOf(dialog)).toEqual([]);

    // Its fetch runs, then ends: its rows.
    server.stations = listOf(STATION_ROWS);
    selectionState({stations: fetchState("running", {revision: 2})});
    await settle();
    expect(within(dialog).getByRole("progressbar")).toBeInTheDocument();
    expect(reads("/api/v1/tournament/stations")).toBe(0);
    selectionState({stations: fetchState("done", {revision: 2})});
    await settle();
    await settle();
    expect(reads("/api/v1/tournament/stations")).toBe(1);
    expect(within(dialog).getAllByRole("columnheader").map((cell) => cell.textContent))
        .toEqual(["score_stations_col_type", "score_stations_col_id",
                  "score_stations_col_stream", "score_stations_col_identifier"]
            .map((key) => i18n.t(key)));
    expect(cellsOf(dialog)).toEqual(setCases.stations.map((item) => item.cells));

    const search = inDialog(dialog, "searchbox", "score_sets_filter");
    fireEvent.change(search, {target: {value: "CHAN"}});
    expect(cellsOf(dialog).map((cells) => cells[1])).toEqual(["77", "90"]);
    fireEvent.change(search, {target: {value: ""}});
    fireEvent.click(inDialog(dialog, "button", "score_stations_col_identifier"));
    fireEvent.click(inDialog(dialog, "button", "score_stations_col_identifier"));
    // A missing identifier is blank (the window writes "None": not copied).
    expect(cellsOf(dialog).map((cells) => cells[3]))
        .toEqual(["channel", "5", "4", ""]);

    fireEvent.doubleClick(rowWith(dialog, "90"));
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(writes("POST", `${BOARD}/1:follow`)).toEqual([STATION_ROWS[2]]);

    // Opened again: emptied and fetched again; the order stays (the window
    // is hidden, not destroyed).
    dialog = await openStations();
    expect(writes("POST", "/api/v1/tournament/stations:refresh")).toHaveLength(2);
    expect(cellsOf(dialog)).toEqual([]);
    selectionState({stations: fetchState("done", {revision: 3})});
    await settle();
    await settle();
    expect(cellsOf(dialog).map((cells) => cells[1])).toEqual(["90", "78", "77", "91"]);
    // None selected: the first row shown, as the window.
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(writes("POST", `${BOARD}/1:follow`))
        .toEqual([STATION_ROWS[2], STATION_ROWS[2]]);
});

test("the station dialog keeps each board's search and order; Enter in the search is OK",
     async () => {
    server.stations = listOf(STATION_ROWS);
    await selectionPage();
    let dialog = await openStations();
    selectionState({stations: fetchState("done", {revision: 2})});
    await settle();
    await settle();
    fireEvent.change(inDialog(dialog, "searchbox", "score_sets_filter"),
                     {target: {value: "CHAN"}});
    fireEvent.click(inDialog(dialog, "button", "score_stations_col_identifier"));
    fireEvent.click(inDialog(dialog, "button", "score_stations_col_identifier"));
    expect(cellsOf(dialog).map((cells) => cells[1])).toEqual(["90", "77"]);
    await closeDialog(dialog);

    // Board 2's window is another: nothing typed, the order as read.
    server.format = {format: "team", board: 2};
    await notify("battle_format");
    dialog = await openStations();
    selectionState({stations: fetchState("done", {revision: 3})});
    await settle();
    await settle();
    expect(inDialog(dialog, "searchbox", "score_sets_filter")).toHaveValue("");
    expect(cellsOf(dialog)).toEqual(setCases.stations.map((item) => item.cells));
    fireEvent.change(inDialog(dialog, "searchbox", "score_sets_filter"),
                     {target: {value: "78"}});
    await closeDialog(dialog);

    // Board 1's again: as it was left.
    server.format = {format: "singles", board: 1};
    await notify("battle_format");
    dialog = await openStations();
    selectionState({stations: fetchState("done", {revision: 4})});
    await settle();
    await settle();
    const search = inDialog(dialog, "searchbox", "score_sets_filter");
    expect(search).toHaveValue("CHAN");
    expect(cellsOf(dialog).map((cells) => cells[1])).toEqual(["90", "77"]);
    expect(within(dialog).getByRole("columnheader", {
        name: i18n.t("score_stations_col_identifier")}))
        .toHaveAttribute("aria-sort", "descending");

    // The Enter that ends an IME conversion is the IME's.
    fireEvent.keyDown(search, {key: "Enter", keyCode: 229, which: 229});
    fireEvent.keyDown(search, {key: "Enter", isComposing: true});
    await settle(1000);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(writes("POST", `${BOARD}/1:follow`)).toEqual([]);
    // Enter is OK: the row selected, else the first shown (chosenRow).
    fireEvent.click(rowWith(dialog, "77"));
    fireEvent.keyDown(search, {key: "Enter"});
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    dialog = await openStations();
    selectionState({stations: fetchState("done", {revision: 5})});
    await settle();
    await settle();
    fireEvent.keyDown(inDialog(dialog, "searchbox", "score_sets_filter"),
                      {key: "Enter"});
    await settle(1000);
    expect(writes("POST", `${BOARD}/1:follow`))
        .toEqual([STATION_ROWS[0], STATION_ROWS[2]]);
});

test("the station dialog says its fetch failed, and a list it cannot read with a reload",
     async () => {
    await selectionPage();
    let dialog = await openStations();
    selectionState({stations: fetchState("error", {revision: 2, error: "fetch_failed"})});
    await settle();
    await settle();
    expect(within(dialog).getByText(i18n.t("score_stations_failed")))
        .toBeInTheDocument();
    await closeDialog(dialog);

    refuse["/api/v1/tournament/stations"] = {status: 400};
    dialog = await openStations();
    expect(within(dialog).queryByText(i18n.t("score_stations_failed"))).toBeNull();
    selectionState({stations: fetchState("done", {revision: 3})});
    await settle();
    await settle();
    expect(within(dialog).getByText(i18n.t("score_sets_read_failed")))
        .toBeInTheDocument();

    delete refuse["/api/v1/tournament/stations"];
    server.stations = listOf(STATION_ROWS);
    fireEvent.click(inDialog(dialog, "button", "score_reload"));
    await settle();
    await settle();
    expect(within(dialog).queryByText(i18n.t("score_sets_read_failed"))).toBeNull();
    expect(cellsOf(dialog)).toEqual(setCases.stations.map((item) => item.cells));
});

test("the station dialog says there is no tournament, as a follow refused for it",
     async () => {
    refuse["/api/v1/tournament/stations:refresh"] = {status: 409, code: "NOT_READY"};
    await selectionPage();
    const dialog = await openStations();
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_no_tournament"));

    server.stations = listOf(STATION_ROWS);
    selectionState({stations: fetchState("done", {revision: 2})});
    await settle();
    await settle();
    refuse[`${BOARD}/1:follow`] = {status: 409, code: "NOT_READY"};
    fireEvent.doubleClick(rowWith(dialog, "77"));
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(i18n.t("score_sets_no_tournament"))).toBeInTheDocument();
});

test("the timer row: what the board follows, its seconds counted down, read again",
     async () => {
    server.follow[1] = {station: null, setId: "1001", pollingEnabled: true,
                        poll: {mode: "set", identifier: "1001", secondsLeft: 4}};
    // No tournament: the row is the board's, as the Qt one.
    await renderPage();
    const row = () => screen.getByTestId("score-follow");
    const seconds = () => within(row()).getByLabelText(i18n.t("score_follow_seconds"));
    const followReads = () => reads(`${BOARD}/1/follow`);

    expect(followReads()).toBe(1);
    expect(row()).toHaveTextContent(i18n.t("score_follow_set"));
    expect(seconds().textContent).toBe("4");
    await settle(1000);
    expect(seconds().textContent).toBe("3");
    await settle(3000);
    expect(seconds().textContent).toBe("0");
    expect(followReads()).toBe(1);
    // Past it the tick has come (a set's tells nothing): read again.
    await settle(1000);
    await settle();
    expect(followReads()).toBe(2);
    expect(seconds().textContent).toBe("4");

    // Another board's notice is not this one's.
    await notify("scoreboard_follow", {board: 2});
    expect(followReads()).toBe(2);
    server.follow[1].poll = {mode: "stream", identifier: "channel", secondsLeft: 2};
    await notify("scoreboard_follow", {board: 1});
    expect(followReads()).toBe(3);
    expect(row()).toHaveTextContent(i18n.t("score_follow_stream", {id: "channel"}));
    expect(seconds().textContent).toBe("2");
    server.follow[1].poll = {mode: "station", identifier: 4, secondsLeft: 3};
    await notify("scoreboard_follow", {board: 1});
    expect(row()).toHaveTextContent(i18n.t("score_follow_station", {id: "4"}));
    expect(seconds().textContent).toBe("3");

    // The next tick not known (null): the number shown stays, as the Qt
    // row's text does; a second on, it is read again.
    await settle(1000);
    expect(seconds().textContent).toBe("2");
    server.follow[1].poll = {mode: "station", identifier: 4, secondsLeft: null};
    await notify("scoreboard_follow", {board: 1});
    expect(followReads()).toBe(5);
    expect(seconds().textContent).toBe("2");
    server.follow[1].poll = {mode: "station", identifier: 4, secondsLeft: 5};
    await settle(1000);
    await settle();
    expect(followReads()).toBe(6);
    expect(seconds().textContent).toBe("5");

    // Back from a lost connection: read again.
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("disconnected"));
    });
    act(() => {
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("connected"));
    });
    await settle();
    await settle();
    expect(followReads()).toBe(7);

    // Cancelled: :stop-follow, and the row goes with the read after it.
    server.follow[1] = noFollow();
    fireEvent.click(button("score_follow_cancel"));
    await settle();
    await settle();
    expect(writes("POST", `${BOARD}/1:stop-follow`)).toEqual([{}]);
    expect(screen.queryByTestId("score-follow")).toBeNull();
});

test("no timer row on the crew board", async () => {
    server.format = {format: "crew", board: 3};
    await renderPage();
    expect(reads(`${BOARD}/3/follow`)).toBe(0);
    expect(screen.queryByTestId("score-follow")).toBeNull();
});

test("大会の試合を選ぶ: on boards 1/2 with a tournament, in a browser as well",
     async () => {
    // No tournament: as the Qt button (hidden).
    await renderPage(false);
    expect(screen.queryByRole("button", {name: i18n.t("score_open_sets")}))
        .toBeNull();
    selectionState();
    expect(button("score_open_sets")).toBeInTheDocument();

    // The team board's: loads to board 2.
    server.format = {format: "team", board: 2};
    await notify("battle_format");
    server.sets = listOf(SET_ROWS, FETCHED);
    const dialog = await openSets();
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(loads(2)).toEqual([{setId: 1001}]);

    server.format = {format: "crew", board: 3};
    await notify("battle_format");
    expect(screen.queryByRole("button", {name: i18n.t("score_open_sets")}))
        .toBeNull();
});

// ── the desktop's Ctrl+Shift+O and legacy open-set (S4ab-2) ─────────────

function setsDialog() {
    return screen.queryByRole("dialog", {name: i18n.t("score_sets_title")});
}

async function askForSets(board) {
    await act(async () => {
        openScoreSets(board);
    });
    await settle();
    await settle();
}

test("the desktop's request opens that board's set selection, after reading the format",
     async () => {
    selectionState();
    await renderPage(true);
    const formatReads = reads("/api/v1/battle-format");
    expect(setsDialog()).toBeNull();

    await askForSets(1);

    expect(setsDialog()).toBeInTheDocument();
    expect(reads("/api/v1/battle-format")).toBe(formatReads + 1);
    // Nothing is written: it only opens the dialog.
    expect(requests().filter((call) => call.method !== "GET")).toEqual([]);

    // Settled once: closed, a later notice opens nothing.
    await closeDialog(setsDialog());
    await notify("battle_format");
    await settle(1000);
    expect(setsDialog()).toBeNull();
});

test("the desktop's request made before the page is drawn opens once it is",
     async () => {
    selectionState();
    // The view loads the page after asking (the host delivers when it can).
    openScoreSets(1);
    const firstRead = holdNext("/api/v1/battle-format");
    await renderPage(true);
    // Still loading: nothing yet.
    expect(setsDialog()).toBeNull();

    await firstRead.answer({format: "singles", board: 1});
    await settle();
    await settle();

    expect(setsDialog()).toBeInTheDocument();
    await closeDialog(setsDialog());
    await notify("battle_format");
    await settle(1000);
    expect(setsDialog()).toBeNull();
});

test("the desktop's request on a page that could not be read is dropped", async () => {
    selectionState();
    server.formatFailures = 1000;
    openScoreSets(1);
    await renderPage(true);
    // (The read on mount is dropped for the one the request makes.)
    for (let attempt = 1; attempt < LOAD_ATTEMPTS * 2; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }
    expect(screen.getByText(i18n.t("score_load_failed"))).toBeInTheDocument();

    // Read again by hand later: the page, not the dialog.
    server.formatFailures = 0;
    fireEvent.click(button("score_reload"));
    await settle();
    await settle();
    expect(button("score_open_sets")).toBeInTheDocument();
    expect(setsDialog()).toBeNull();
});

test("the desktop's request names the board of the format read after it",
     async () => {
    selectionState();
    await renderPage(true);

    // Board 2 asked while board 1 is shown (the shell checked the format
    // before a switch the page has not heard of yet): read again, board 2.
    server.format = {format: "team", board: 2};
    server.sets = listOf(SET_ROWS, FETCHED);
    await askForSets(2);
    const dialog = setsDialog();
    expect(dialog).toBeInTheDocument();
    // Board 2's dialog: it loads to board 2.
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(loads(2)).toEqual([{setId: 1001}]);
});

test("the desktop's request for another board than the format's opens nothing",
     async () => {
    selectionState();
    await renderPage(true);
    const formatReads = reads("/api/v1/battle-format");

    await askForSets(2);

    // Settled on the format read after the request (it is board 1's still),
    // not on the one the page had.
    expect(reads("/api/v1/battle-format")).toBe(formatReads + 1);
    expect(setsDialog()).toBeNull();
    // The page is shown, the board of the format (1) with its button.
    expect(button("score_open_sets")).toBeInTheDocument();
    // Not opened later by a notice either.
    await notify("battle_format");
    expect(setsDialog()).toBeNull();
});

test("the desktop's request without a tournament (or on the crew board) opens nothing",
     async () => {
    await renderPage(true);
    let formatReads = reads("/api/v1/battle-format");

    await askForSets(1);
    // Settled, on the format read after the request.
    expect(reads("/api/v1/battle-format")).toBe(formatReads + 1);
    expect(setsDialog()).toBeNull();
    // Not when the tournament comes later either: the request was settled.
    selectionState();
    await settle();
    expect(button("score_open_sets")).toBeInTheDocument();
    expect(setsDialog()).toBeNull();

    server.format = {format: "crew", board: 3};
    await notify("battle_format");
    formatReads = reads("/api/v1/battle-format");
    await askForSets(3);
    expect(reads("/api/v1/battle-format")).toBe(formatReads + 1);
    expect(screen.queryByRole("dialog")).toBeNull();
});

test("the desktop's request closes the station dialog for the set selection",
     async () => {
    selectionState();
    await renderPage(true);
    await openStations();

    await askForSets(1);
    await settle(1000);

    // Hidden ones too: a modal opened over another hides it from the
    // accessibility tree, and it would still be open.
    expect(screen.queryByRole("dialog", {name: i18n.t("score_stations_title"),
                                         hidden: true})).toBeNull();
    expect(setsDialog()).toBeInTheDocument();
});

test("a set selection write refused for want of the token is told once", async () => {
    server.sets = listOf(SET_ROWS, FETCHED);
    refuse["/api/v1/tournament/sets:refresh"] = {status: 401, code: "UNAUTHENTICATED"};
    refuse[`${BOARD}/1:load-set`] = {status: 401, code: "UNAUTHENTICATED"};
    await selectionPage();
    const dialog = await openSets();

    fireEvent.click(inDialog(dialog, "button", "score_sets_fetch"));
    expect(await screen.findAllByText(i18n.t("stream_token_required")))
        .toHaveLength(1);
    // The status has nothing to add.
    expect(statusOf(dialog)).toBe(i18n.t("score_sets_count",
                                         {n: SET_ROWS.length, at: "09:05"}));

    // A click closes the first snackbar (click-away); the next refusal
    // opens none again.
    fireEvent.click(rowWith(dialog, "Team A (A1, A2)"));
    await settle(1000);
    expect(screen.queryByText(i18n.t("stream_token_required"))).toBeNull();
    fireEvent.click(inDialog(dialog, "button", "score_sets_ok"));
    await settle(1000);
    expect(loads()).toEqual([{setId: 1006}]);
    expect(screen.queryByText(i18n.t("stream_token_required"))).toBeNull();
});
