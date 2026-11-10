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
import {LOAD_ATTEMPTS, LOAD_RETRY_MS} from "../PanelShell/requests";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import {
    MADE_IMAGE_COPY_TIMEOUT_MS,
    copyImageOnceMade,
} from "../PanelShell/clipboard";
import socketConnection from "../websocketConnection";
import table from "../Top8Page/characterPick.cases.json";
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
        openSetStatus: 200,
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
            const published = path.match(/^\/api\/v1\/scoreboards\/(\d)$/);
            if (published) {
                return answer(200, {ok: true, data: {
                    ...server.published[published[1]], swapped: server.swapped}});
            }
            if (path === "/api/v1/state") {
                return answer(200, {ok: true, data: server.state});
            }
            if (/^\/scoreboard\d-open-set$/.test(path)) {
                return answer(server.openSetStatus, "OK");
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
    expect(reads(`${BOARD}/2/pending`)).toBe(1);
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
    expect(reads(`${BOARD}/2/pending`)).toBe(1);

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

    await notify("scoreboard_pending", {board: 2, dirty: true});
    const notice = screen.getByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_2")}));
    // Not the shown board's: its values are not read for it.
    expect(reads(`${BOARD}/2/pending`)).toBe(0);
    fireEvent.click(within(notice.closest("[role=alert]"))
        .getByRole("button", {name: i18n.t("score_apply")}));
    await settle();
    expect(writes("POST", `${BOARD}/2:apply`)).toEqual([{}]);

    await notify("scoreboard_pending", {board: 2, dirty: false});
    expect(screen.queryByText(i18n.t("score_other_dirty", {
        board: i18n.t("score_board_name_2")}))).toBeNull();
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
    // Told by their notices: a board not built is not read.
    expect(reads(`${BOARD}/1/pending`)).toBe(0);
    expect(reads(`${BOARD}/2/pending`)).toBe(0);
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

const TOURNAMENT = {tournamentName: "Cup", eventName: "", displayTournamentName: "",
                    url: "https://www.start.gg/tournament/cup/event/singles"};

test("大会の試合を選ぶ opens the desktop's set window through the legacy route",
     async () => {
    setState({tournamentInfo: TOURNAMENT});
    server.format = {format: "team", board: 2};
    await renderPage(true);

    fireEvent.click(button("score_open_sets"));
    await settle();
    await settle();
    expect(reads("/scoreboard2-open-set")).toBe(1);

    // The headless app has no window to open.
    server.openSetStatus = 503;
    fireEvent.click(button("score_open_sets"));
    expect(await screen.findByText(i18n.t("score_open_sets_unavailable")))
        .toBeInTheDocument();
});

test("大会の試合を選ぶ shows with a tournament, on the desktop's boards 1/2 only",
     async () => {
    // No tournament: as the Qt button (hidden).
    await renderPage(true);
    expect(screen.queryByRole("button", {name: i18n.t("score_open_sets")}))
        .toBeNull();
    setState({tournamentInfo: TOURNAMENT});
    expect(button("score_open_sets")).toBeInTheDocument();

    server.format = {format: "crew", board: 3};
    await notify("battle_format");
    expect(screen.queryByRole("button", {name: i18n.t("score_open_sets")}))
        .toBeNull();
});

test("in a browser there is no 大会の試合を選ぶ", async () => {
    setState({tournamentInfo: TOURNAMENT});
    await renderPage(false);
    expect(screen.queryByRole("button", {name: i18n.t("score_open_sets")}))
        .toBeNull();
});
