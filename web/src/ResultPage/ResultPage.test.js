import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import {
    ECHO_GRACE_MS,
    STATE_FIELD_DEBOUNCE_MS,
} from "../PanelShell/useStateField";
import {
    MADE_IMAGE_COPY_TIMEOUT_MS,
    copyImageOnceMade,
} from "../PanelShell/clipboard";
import {LOAD_ATTEMPTS, LOAD_RETRY_MS} from "../PanelShell/requests";
import table from "../Top8Page/characterPick.cases.json";
import ResultPage, {PREVIEW_DEBOUNCE_MS, READ_DEBOUNCE_MS} from "./index";

// The tournament result's page in React (ADR-005 S4r): it reads and writes
// /api/v1/result, asks for a preview PREVIEW_DEBOUNCE_MS after a change while
// it is shown, shows the image of the revision result_render ended with, makes
// the image, imports the standings, and in one click makes the image, asks the
// clipboard for it and opens X's composer.

jest.setTimeout(30000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const LAYOUTS = [
    {id: "horizontal", name: "H", width: 1920, height: 1080, slots: 8,
     team: false},
    {id: "horizontal_16", name: "H16", width: 1920, height: 1080, slots: 16,
     team: false},
    {id: "vertical", name: "V", width: 960, height: 1080, slots: 8,
     team: false},
    {id: "team", name: "T", width: 1920, height: 1080, slots: 8, team: true},
];
const POST_TEXT = "Big Cup #1\n1st Alpha & co";
const PATH = "out/results/Big Cup-top8-1.png";
const ENCODED = "out/results/Big%20Cup-top8-1.png";
const X_URL = "https://twitter.com/intent/tweet?text=";

let world;
let revision;
let standingsRevision;
let stateRevision = 0;
// Write path -> {status, code} it answers instead.
let refuse;
// "METHOD path" -> a promise its answer waits for (hold()).
let gates;

function player(slot, fields = {}) {
    return {slot, rank: slot, name: "", sponsor: "", twitter: "",
            characters: [null, null, null], fromScoreboard: null, ...fields};
}

function team(slot, fields = {}) {
    return {slot, rank: slot, name: "", members: [], ...fields};
}

function freshWorld() {
    return {
        layout: "horizontal",
        players: Array.from({length: 16}, (_unused, index) => player(index + 1)),
        teams: Array.from({length: 8}, (_unused, index) => team(index + 1)),
        sample: true,
        game: {codename: "tgame", name: "T Game"},
        canRender: true,
        post: POST_TEXT,
        saved: 3,
    };
}

function described() {
    const {layout, players, teams, sample, game, canRender, render} = world;
    return JSON.parse(JSON.stringify({layout, layouts: LAYOUTS, players, teams,
                                      sample, game, canRender, render}));
}

// What PATCH /result/players/<n> and /teams/<n> do with the places a body names,
// as core does (read back as identifiers).
function writeRow(row, {characters, members, ...fields}) {
    const read = (pick) => pick && {en_name: pick.en_name, skin: pick.skin};
    Object.assign(row, fields);
    Object.entries(characters ?? {}).forEach(([place, pick]) => {
        row.characters[Number(place) - 1] = read(pick);
    });
    Object.entries(members ?? {}).forEach(([place, {character, ...texts}]) => {
        const index = Number(place) - 1;
        while (row.members.length <= index) {
            row.members.push({name: "", twitter: "", character: null,
                              fromScoreboard: null});
        }
        Object.assign(row.members[index], texts);
        if (character !== undefined) {
            row.members[index].character = read(character);
        }
    });
}

function hold(method, path) {
    const key = `${method} ${path}`;
    let release;
    gates[key] = new Promise((resolve) => {
        release = () => {
            delete gates[key];
            resolve();
        };
    });
    return release;
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

function writes(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && new URL(url).pathname === path)
        .map(([, init]) => JSON.parse(init.body));
}

function reads(path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "GET"
            && new URL(url).pathname === path).length;
}

const RESULT = "/api/v1/result";

function previews() {
    return writes("POST", `${RESULT}:preview`).length;
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 12; i++) {
            await Promise.resolve();
        }
    });
}

function setState(state) {
    stateRevision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {game: {codename: "tgame", name: "T Game"}, ...state},
            revision: stateRevision,
        }));
    });
}

function rendered(fields) {
    return {status: "done", path: "", error: "", revision: 1, ...fields};
}

function pageIn(language) {
    return (
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "result", desktop: false, language}}>
                <ResultPage/>
            </PanelShellContext.Provider>
        </Provider>
    );
}

function mountPage() {
    return render(pageIn("en"));
}

async function renderPage() {
    const page = mountPage();
    await settle();
    await screen.findByRole("region", {name: i18n.t("result_preview")});
    await settle();
    return page;
}

function region(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function button(scope, key) {
    return within(scope).getByRole("button", {name: i18n.t(key)});
}

// The rows make a large tree: by selector, as the Top 8 page's test does.
function row(slot) {
    return document.querySelector(`[data-result-row="${slot}"]`);
}

function rowCount() {
    return document.querySelectorAll("[data-result-row]").length;
}

function field(slot, key) {
    return within(row(slot)).getByLabelText(i18n.t(key), {selector: "input"});
}

function combo(slot, key) {
    return within(row(slot)).getByLabelText(i18n.t(key),
                                            {selector: "[role=combobox]"});
}

function grip(slot) {
    return within(row(slot)).getByTitle(i18n.t("result_drag"));
}

function moveButton(slot, way) {
    return row(slot).querySelector(`[data-move="${way}"]`);
}

function memberGroup(slot, number) {
    return row(slot).querySelector(
        `[aria-label="${i18n.t("result_member", {number})}"]`);
}

async function type(input, value) {
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
}

async function pickOption(input, typed, name) {
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value: typed}});
    fireEvent.click(await screen.findByRole("option", {name}));
    await settle();
}

async function chooseLayout(id) {
    const select = screen.getByRole("combobox",
                                    {name: i18n.t("result_layout_field")});
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole("option", {
        name: i18n.t(`result_layout_${id}`)}));
    await settle();
    await settle(READ_DEBOUNCE_MS);
    await settle();
}

// A dialog answered: gone once its transition is over (until then the page
// behind it is hidden from the accessibility tree).
async function dialogGone() {
    await settle(1000);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
}

function setVisible(visible) {
    Object.defineProperty(document, "visibilityState",
                          {value: visible ? "visible" : "hidden",
                           configurable: true});
    act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
}

function setSecure(secure) {
    Object.defineProperty(window, "isSecureContext",
                          {value: secure, configurable: true});
    if (secure) {
        global.ClipboardItem = class {
            constructor(items) {
                this.items = items;
            }
        };
    } else {
        delete global.ClipboardItem;
    }
}

function withData() {
    world.players[0].name = "Alpha";
    world.sample = false;
}

let opened;

beforeEach(() => {
    jest.useFakeTimers();
    world = freshWorld();
    revision = 0;
    standingsRevision = 0;
    refuse = {};
    gates = {};
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    Object.defineProperty(navigator, "clipboard", {
        // As a browser's: it answers once the image it was given has come.
        value: {write: jest.fn(async (items) => {
            await items[0].items["image/png"];
        })},
        configurable: true,
    });
    setSecure(false);
    setVisible(true);
    opened = jest.spyOn(window, "open").mockImplementation(() => null);
    global.createImageBitmap = jest.fn(async () => ({width: 4, height: 2,
                                                     close: jest.fn()}));
    jest.spyOn(HTMLCanvasElement.prototype, "getContext")
        .mockReturnValue({drawImage: jest.fn()});
    jest.spyOn(HTMLCanvasElement.prototype, "toBlob")
        .mockImplementation((done) => done(new Blob(["png"])));
    jest.spyOn(console, "error").mockImplementation(() => {});
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : {};
        // What it reads now, as a server has answered by the time it arrives.
        const description = described();
        const gate = gates[`${method} ${path}`];
        if (gate) {
            await gate;
        }
        const refused = refuse[path];
        if (refused && (method !== "GET" || path === `${RESULT}/post`)
                && !(refused.unlessConfirmed && body.confirm)) {
            return refusal(refused.status, refused.code);
        }
        if (method === "GET") {
            if (path === RESULT) {
                return answer(200, {ok: true, data: description});
            }
            if (path === `${RESULT}/post`) {
                return answer(200, {ok: true, data: {text: world.post}});
            }
            if (path === "/api/v1/characters") {
                return answer(200, {ok: true, data: table.characters});
            }
            if (path.startsWith("/out/")) {
                return {ok: true, status: 200, blob: async () => new Blob(["x"])};
            }
            return answer(404, {ok: false});
        }
        if (path === RESULT && method === "PATCH") {
            world.layout = body.layout;
            return answer(200, {ok: true});
        }
        const edited = path.match(/\/api\/v1\/result\/(players|teams)\/(\d+)$/);
        if (edited) {
            writeRow(world[edited[1]][Number(edited[2]) - 1], body);
            return answer(200, {ok: true});
        }
        if (path === `${RESULT}:preview` || path === `${RESULT}:generate`) {
            revision += 1;
            return answer(202, {ok: true, data: {accepted: true,
                                                 renderRevision: revision}});
        }
        if (path === `${RESULT}:loadStandings`) {
            standingsRevision += 1;
            return answer(202, {ok: true, data: {
                accepted: true, standingsRevision}});
        }
        if (path === `${RESULT}:saveToDb`) {
            return answer(200, {ok: true, data: {saved: world.saved}});
        }
        return answer(200, {ok: true, revision: 1});
    });
    setState({});
});

afterEach(async () => {
    // A making left on its way lets go of the store.
    await settle(MADE_IMAGE_COPY_TIMEOUT_MS);
    jest.useRealTimers();
    jest.restoreAllMocks();
    delete global.fetch;
    delete global.ClipboardItem;
    delete global.createImageBitmap;
});

// ── the page ──────────────────────────────────────────────────────────────

test("the cards come up with the rows of the layout, and one preview after the wait",
     async () => {
    await renderPage();

    expect(document.title).toBe(`Autona ${i18n.t("result_title")}`);
    ["result_layout", "result_save_db", "result_colors", "result_preview"]
        .forEach((key) => expect(region(key)).toBeInTheDocument());
    expect(screen.getByRole("region", {
        name: i18n.t("result_rows_players", {count: 8})})).toBeInTheDocument();
    expect(rowCount()).toBe(8);
    expect(reads(RESULT)).toBe(1);
    expect(reads(`${RESULT}/post`)).toBe(1);
    expect(previews()).toBe(0);

    await settle(PREVIEW_DEBOUNCE_MS);

    expect(previews()).toBe(1);
});

test("a page that cannot load says so, and loads again when asked", async () => {
    refuse[RESULT] = null;
    const failing = global.fetch.getMockImplementation();
    global.fetch.mockImplementation(async (url, init) => (
        new URL(url).pathname === RESULT && !init?.method
            ? refusal(404) : failing(url, init)));
    mountPage();
    await settle();
    await settle();

    const reload = await screen.findByRole("button",
                                           {name: i18n.t("result_reload")});
    expect(screen.getByText(i18n.t("result_load_failed"))).toBeInTheDocument();

    global.fetch.mockImplementation(failing);
    fireEvent.click(reload);
    await settle();

    expect(await screen.findByRole("region", {name: i18n.t("result_preview")}))
        .toBeInTheDocument();
});

test("the layout chosen is saved, and says how many rows and of what kind",
     async () => {
    await renderPage();

    await chooseLayout("horizontal_16");

    expect(writes("PATCH", RESULT)).toEqual([{layout: "horizontal_16"}]);
    expect(rowCount()).toBe(16);

    await chooseLayout("team");

    expect(rowCount()).toBe(8);
    expect(screen.getByRole("region", {
        name: i18n.t("result_rows_teams", {count: 8})})).toBeInTheDocument();
    expect(field(1, "result_team_name")).toBeInTheDocument();
});

test("a layout changed elsewhere (the sidebar's battle format) is followed",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const before = previews();
    setState({result_layout: "horizontal"});
    await settle(PREVIEW_DEBOUNCE_MS);
    const asked = reads(RESULT);

    world.layout = "team";
    setState({result_layout: "team"});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(reads(RESULT)).toBe(asked + 1);
    expect(screen.getByRole("region", {
        name: i18n.t("result_rows_teams", {count: 8})})).toBeInTheDocument();
    expect(previews()).toBeGreaterThan(before);
});

test("no game: the page says the characters need one", async () => {
    world.game = null;
    await renderPage();

    expect(within(region("result_layout")).getByText(i18n.t("result_no_game")))
        .toBeInTheDocument();
});

// ── the rows ──────────────────────────────────────────────────────────────

test("a player's fields go one at a time, to that row", async () => {
    await renderPage();

    await type(field(2, "result_name"), "Bravo");
    await type(field(2, "result_sponsor"), "SP");
    await type(field(3, "result_twitter"), "@c");

    expect(writes("PATCH", `${RESULT}/players/2`))
        .toEqual([{name: "Bravo"}, {sponsor: "SP"}]);
    expect(writes("PATCH", `${RESULT}/players/3`)).toEqual([{twitter: "@c"}]);
});

test("a rank goes on Enter or on leaving, within its range", async () => {
    await renderPage();
    const rank = field(1, "result_rank");

    fireEvent.focus(rank);
    fireEvent.change(rank, {target: {value: "5"}});
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(writes("PATCH", `${RESULT}/players/1`)).toEqual([]);

    fireEvent.keyDown(rank, {key: "Enter"});
    await settle();
    expect(writes("PATCH", `${RESULT}/players/1`)).toEqual([{rank: 5}]);

    fireEvent.change(rank, {target: {value: "1000"}});
    fireEvent.blur(rank);
    await settle();
    await settle(READ_DEBOUNCE_MS);
    await settle();
    expect(writes("PATCH", `${RESULT}/players/1`)).toEqual([{rank: 5}]);
    expect(rank.value).toBe("5");
});

test("a character is sent by its identifiers with the game it was picked on, and by its own place only",
     async () => {
    await renderPage();

    await pickOption(combo(1, "result_character_1"), "Peach", "Peach");
    await pickOption(combo(1, "result_character_2"), "Bowser", "Bowser");

    const peach = {en_name: "Peach", skin: 0, game: "tgame"};
    const bowser = {en_name: "Bowser", skin: 0, game: "tgame"};
    expect(writes("PATCH", `${RESULT}/players/1`)).toEqual([
        {characters: {"1": peach}},
        {characters: {"2": bowser}},
    ]);
});

test("a refused character goes back, and says why", async () => {
    await renderPage();
    refuse[`${RESULT}/players/1`] = {status: 400, code: "INVALID_REQUEST"};

    await pickOption(combo(1, "result_character_1"), "Peach", "Peach");
    await settle();

    expect(await screen.findByText(i18n.t("top8_pick_refused")))
        .toBeInTheDocument();
    expect(combo(1, "result_character_1")).toHaveValue("");
});

test("a text refused is not told as a character", async () => {
    await renderPage();
    refuse[`${RESULT}/players/1`] = {status: 400, code: "INVALID_ARGUMENT"};

    await type(field(1, "result_name"), "Alpha");
    await settle();

    expect(await screen.findByText(i18n.t("result_row_refused")))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("top8_pick_refused")))
        .not.toBeInTheDocument();
});

test("a character of a game no longer loaded reads the list again", async () => {
    await renderPage();
    const before = reads("/api/v1/characters");
    refuse[`${RESULT}/players/1`] = {status: 409, code: "CONFLICT"};

    await pickOption(combo(1, "result_character_1"), "Peach", "Peach");
    await settle();

    expect(await screen.findByText(i18n.t("top8_pick_other_game")))
        .toBeInTheDocument();
    expect(reads("/api/v1/characters")).toBe(before + 1);
});

test("a row filled in from the scoreboard says so", async () => {
    world.players[0] = player(1, {name: "Alpha", fromScoreboard: "Peach"});
    world.players[1] = player(2, {name: "Bravo", fromScoreboard: ""});
    world.players[2] = player(3, {name: "Charlie"});
    world.sample = false;
    await renderPage();
    const said = i18n.t("result_from_scoreboard");

    expect(within(row(1)).getByText(`${said}: Peach`)).toBeInTheDocument();
    expect(within(row(2)).getByText(said)).toBeInTheDocument();
    expect(within(row(3)).queryByText(new RegExp(said)))
        .not.toBeInTheDocument();
});

test("a team's members are five places, and only what was edited goes",
     async () => {
    world.layout = "team";
    world.teams[0] = team(1, {name: "Reds", members: [
        {name: "A1", twitter: "@a1", character: null, fromScoreboard: "Peach"},
    ]});
    world.sample = false;
    await renderPage();

    expect(row(1).querySelectorAll("[role=group]").length).toBe(5);
    expect(within(memberGroup(1, 1)).getByText(
        `${i18n.t("result_from_scoreboard")}: Peach`)).toBeInTheDocument();

    await type(within(memberGroup(1, 2)).getByLabelText(
        i18n.t("result_name"), {selector: "input"}), "A2");

    expect(writes("PATCH", `${RESULT}/teams/1`)).toEqual([
        {members: {"2": {name: "A2"}}},
    ]);

    await pickOption(within(memberGroup(1, 3)).getByLabelText(
        i18n.t("top8_character"), {selector: "[role=combobox]"}), "Peach", "Peach");
    expect(writes("PATCH", `${RESULT}/teams/1`).at(-1)).toEqual({
        members: {"3": {character: {en_name: "Peach", skin: 0, game: "tgame"}}}});

    await type(field(1, "result_team_name"), "Blues");
    expect(writes("PATCH", `${RESULT}/teams/1`).at(-1)).toEqual({name: "Blues"});
});

test("a member's character not touched is not sent: it may be of the game before",
     async () => {
    world.layout = "team";
    world.teams[0] = team(1, {members: [
        {name: "A1", twitter: "", character: {en_name: "Peach", skin: 0},
         fromScoreboard: null},
    ]});
    await renderPage();

    await type(within(memberGroup(1, 1)).getByLabelText(
        i18n.t("result_twitter"), {selector: "input"}), "@a");

    expect(writes("PATCH", `${RESULT}/teams/1`)).toEqual([
        {members: {"1": {twitter: "@a"}}},
    ]);
});

test("a row's text follows the state, a team's members too", async () => {
    setState({
        result_players: {"2": {name: "Bravo", sponsor: "SP", twitter: "@b"}},
    });
    await renderPage();

    expect(field(2, "result_name")).toHaveValue("Bravo");
    expect(field(2, "result_sponsor")).toHaveValue("SP");
    expect(field(2, "result_twitter")).toHaveValue("@b");
    // One the state does not have yet: what the page read.
    expect(field(3, "result_name")).toHaveValue("");

    setState({result_players: {"2": {name: "Bravo2"}}});
    expect(field(2, "result_name")).toHaveValue("Bravo2");

    world.layout = "team";
    setState({result_layout: "team", result_teams: {"1": {
        name: "Reds", members: [{name: "A1", twitter: "@a1"}],
    }}});
    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(field(1, "result_team_name")).toHaveValue("Reds");
    const member = within(memberGroup(1, 1));
    expect(member.getByLabelText(i18n.t("result_name"), {selector: "input"}))
        .toHaveValue("A1");
    expect(member.getByLabelText(i18n.t("result_twitter"), {selector: "input"}))
        .toHaveValue("@a1");
});

test("what is being typed is not taken back by a read later than the echo's wait",
     async () => {
    setState({result_players: {"1": {name: ""}}});
    await renderPage();
    const name = field(1, "result_name");
    const releaseRead = hold("GET", RESULT);

    await type(name, "Al");
    await settle();
    await settle(READ_DEBOUNCE_MS);
    await settle();
    await settle(ECHO_GRACE_MS);
    fireEvent.change(name, {target: {value: "Alp"}});
    releaseRead();
    await settle();
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PATCH", `${RESULT}/players/1`))
        .toEqual([{name: "Al"}, {name: "Alp"}]);
    expect(name).toHaveValue("Alp");
});

test("a read that was on its way while a row was written is not shown",
     async () => {
    world.players[0] = player(1, {name: "Alpha", fromScoreboard: "Old"});
    await renderPage();
    const said = `${i18n.t("result_from_scoreboard")}: Old`;
    world.players[0].fromScoreboard = "Older";
    const releaseRead = hold("GET", RESULT);
    setState({result_players: {"1": {name: "Alpha"}}});
    await settle(READ_DEBOUNCE_MS);
    await settle();

    world.players[0].fromScoreboard = "New";
    const releaseWrite = hold("PATCH", `${RESULT}/players/2`);
    await type(field(2, "result_name"), "Bravo");
    releaseRead();
    await settle();

    // Dropped: not "Older", the row is as it was.
    expect(within(row(1)).getByText(said)).toBeInTheDocument();

    releaseWrite();
    await settle();
    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(within(row(1)).getByText(
        `${i18n.t("result_from_scoreboard")}: New`)).toBeInTheDocument();
});

test("a read that starts while a row is being written is not shown either",
     async () => {
    world.players[0] = player(1, {name: "Alpha", fromScoreboard: "Old"});
    await renderPage();
    const said = (name) => `${i18n.t("result_from_scoreboard")}: ${name}`;

    const releaseWrite = hold("PATCH", `${RESULT}/players/2`);
    await type(field(2, "result_name"), "Bravo");
    world.players[0].fromScoreboard = "Older";
    const releaseRead = hold("GET", RESULT);
    setState({result_players: {"1": {name: "Alpha"}}});
    await settle(READ_DEBOUNCE_MS);
    await settle();

    world.players[0].fromScoreboard = "New";
    releaseWrite();
    await settle();
    releaseRead();
    await settle();

    // The write was answered before it, and it still does not have the write.
    expect(within(row(1)).getByText(said("Old"))).toBeInTheDocument();

    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(within(row(1)).getByText(said("New"))).toBeInTheDocument();
});

test("a change of the state during the first load is read once it has loaded",
     async () => {
    const release = hold("GET", RESULT);
    mountPage();
    await settle();

    world.players[0].fromScoreboard = "Peach";
    setState({result_players: {"1": {name: "Alpha"}}});
    await settle(READ_DEBOUNCE_MS);
    release();
    await settle();
    await settle();
    expect(reads(RESULT)).toBe(1);

    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(reads(RESULT)).toBe(2);
    expect(within(row(1)).getByText(
        `${i18n.t("result_from_scoreboard")}: Peach`)).toBeInTheDocument();
});

test("changes during a read are one more read", async () => {
    await renderPage();
    const before = reads(RESULT);
    const release = hold("GET", RESULT);
    setState({result_players: {"1": {name: "a"}}});
    await settle(READ_DEBOUNCE_MS);
    await settle();
    expect(reads(RESULT)).toBe(before + 1);

    setState({result_players: {"1": {name: "ab"}}});
    await settle(READ_DEBOUNCE_MS);
    setState({result_players: {"1": {name: "abc"}}});
    await settle(READ_DEBOUNCE_MS);
    await settle();
    expect(reads(RESULT)).toBe(before + 1);

    release();
    await settle();
    await settle();

    expect(reads(RESULT)).toBe(before + 2);
});

// ── the order ─────────────────────────────────────────────────────────────

test("the buttons move a row by its place, and the focus goes with it",
     async () => {
    await renderPage();

    fireEvent.click(moveButton(2, "down"));
    await settle();
    fireEvent.click(moveButton(2, "up"));
    await settle();

    expect(writes("POST", `${RESULT}:reorder`)).toEqual([
        {kind: "players", order: [1, 3, 2, 4, 5, 6, 7, 8]},
        {kind: "players", order: [2, 1, 3, 4, 5, 6, 7, 8]},
    ]);
    expect(document.activeElement).toBe(moveButton(1, "down"));
});

test("the ends of the list cannot go further, and the focus turns back",
     async () => {
    await renderPage();

    expect(moveButton(1, "up")).toBeDisabled();
    expect(moveButton(8, "down")).toBeDisabled();
    expect(moveButton(1, "down")).toBeEnabled();

    moveButton(7, "down").focus();
    fireEvent.click(moveButton(7, "down"));
    await settle();

    expect(document.activeElement).toBe(moveButton(8, "up"));
});

test("the buttons of a row are named for it, for the keyboard's users",
     async () => {
    await renderPage();

    expect(within(row(3)).getByRole("button", {
        name: i18n.t("result_move_up", {row: i18n.t("result_row", {slot: 3})}),
    })).toBeInTheDocument();
    expect(within(row(3)).getByRole("button", {
        name: i18n.t("result_move_down", {row: i18n.t("result_row", {slot: 3})}),
    })).toBeInTheDocument();
});

test("the grip is the mouse's: not one of the keyboard's stops, the buttons are",
     async () => {
    await renderPage();

    expect(grip(3)).toHaveAttribute("aria-hidden", "true");
    expect(grip(3)).not.toHaveAttribute("role");
});

test("dragging a row by its grip reorders them, a team's as well", async () => {
    world.layout = "team";
    await renderPage();
    const dragged = grip(3);
    const dataTransfer = {setData: jest.fn(), setDragImage: jest.fn()};

    fireEvent.dragStart(dragged, {dataTransfer});
    fireEvent.dragOver(row(1), {dataTransfer});
    fireEvent.drop(row(1), {dataTransfer});
    fireEvent.dragEnd(dragged, {dataTransfer});
    await settle();

    expect(writes("POST", `${RESULT}:reorder`)).toEqual([
        {kind: "teams", order: [1, 3, 2, 4, 5, 6, 7, 8]},
    ]);
});

test("a drop that moves nothing and a drag from elsewhere write nothing",
     async () => {
    await renderPage();
    const dataTransfer = {setData: jest.fn(), setDragImage: jest.fn()};

    fireEvent.drop(row(2), {dataTransfer});
    const dragged = grip(1);
    fireEvent.dragStart(dragged, {dataTransfer});
    fireEvent.drop(row(1), {dataTransfer});
    fireEvent.dragEnd(dragged, {dataTransfer});
    await settle();

    expect(writes("POST", `${RESULT}:reorder`)).toEqual([]);
});

test("the fields wait for a move's answer and the read after it, the moves do not",
     async () => {
    world.players[0].characters[0] = {en_name: "Peach", skin: 0};
    await renderPage();
    const release = hold("POST", `${RESULT}:reorder`);

    fireEvent.click(moveButton(2, "down"));
    await settle();

    // Read-only, not disabled: a focused field that is disabled never hears it
    // lost the focus.
    expect(field(1, "result_name")).toHaveAttribute("readonly");
    expect(field(1, "result_name")).toBeEnabled();
    expect(field(1, "result_rank")).toHaveAttribute("readonly");
    expect(combo(1, "result_character_1")).toHaveAttribute("readonly");
    fireEvent.mouseDown(within(row(1)).getByLabelText(
        i18n.t("result_color", {number: 1}), {selector: "[role=combobox]"}));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(moveButton(4, "down")).toBeEnabled();

    release();
    await settle();
    // Answered, not read yet.
    expect(field(1, "result_name")).toHaveAttribute("readonly");

    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(field(1, "result_name")).not.toHaveAttribute("readonly");
    expect(combo(1, "result_character_1")).not.toHaveAttribute("readonly");
});

test("a move refused gives the fields back", async () => {
    await renderPage();
    refuse[`${RESULT}:reorder`] = {status: 400, code: "INVALID_ARGUMENT"};

    fireEvent.click(moveButton(2, "down"));
    await settle();
    await settle(READ_DEBOUNCE_MS);
    await settle();

    expect(field(1, "result_name")).not.toHaveAttribute("readonly");
});

test("what is being typed goes to its row ahead of the move", async () => {
    await renderPage();
    const name = field(2, "result_name");
    name.focus();
    fireEvent.change(name, {target: {value: "Bravo"}});

    fireEvent.click(moveButton(2, "down"));
    await settle();

    const paths = global.fetch.mock.calls
        .filter(([, init]) => init?.method)
        .map(([url]) => new URL(url).pathname);
    expect(paths.indexOf(`${RESULT}/players/2`)).toBeGreaterThanOrEqual(0);
    expect(paths.indexOf(`${RESULT}/players/2`))
        .toBeLessThan(paths.indexOf(`${RESULT}:reorder`));
});

test("the arrow keys step a rank, within its range, and it goes as typing does",
     async () => {
    await renderPage();
    const rank = field(2, "result_rank");

    fireEvent.focus(rank);
    fireEvent.keyDown(rank, {key: "ArrowUp"});
    expect(rank).toHaveValue("3");
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(writes("PATCH", `${RESULT}/players/2`)).toEqual([]);

    fireEvent.keyDown(rank, {key: "ArrowDown"});
    fireEvent.keyDown(rank, {key: "ArrowDown"});
    fireEvent.keyDown(rank, {key: "ArrowDown"});
    expect(rank).toHaveValue("1");

    fireEvent.keyDown(rank, {key: "Enter"});
    await settle();
    expect(writes("PATCH", `${RESULT}/players/2`)).toEqual([{rank: 1}]);
});

// ── the standings' import ─────────────────────────────────────────────────

test("the import starts at once with nothing entered, and says how it ended",
     async () => {
    await renderPage();
    const card = region("result_layout");

    fireEvent.click(button(card, "result_import"));
    await settle();

    expect(writes("POST", `${RESULT}:loadStandings`)).toEqual([{}]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    setState({result_standings: {status: "running", error: "", revision: 1}});
    expect(button(card, "result_import")).toBeDisabled();
    expect(within(card).getByText(i18n.t("result_importing")))
        .toBeInTheDocument();

    setState({result_standings: {status: "done", error: "", revision: 1}});
    await settle();

    expect(button(card, "result_import")).toBeEnabled();
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_import_done"));
});

test("while the standings are on their way the rows and the clearing wait",
     async () => {
    await renderPage();
    const card = region("result_layout");

    setState({result_standings: {status: "running", error: "", revision: 1}});

    expect(button(card, "result_clear")).toBeDisabled();
    expect(field(1, "result_name")).toHaveAttribute("readonly");
    expect(field(1, "result_rank")).toHaveAttribute("readonly");
    expect(combo(1, "result_character_1")).toHaveAttribute("readonly");
    expect(moveButton(2, "down")).toBeDisabled();
    expect(grip(2)).toHaveAttribute("draggable", "false");

    setState({result_standings: {status: "done", error: "", revision: 1}});
    await settle();

    expect(button(card, "result_clear")).toBeEnabled();
    expect(field(1, "result_name")).not.toHaveAttribute("readonly");
    expect(moveButton(2, "down")).toBeEnabled();
    expect(grip(2)).toHaveAttribute("draggable", "true");
});

test("an import over what is entered asks first, and only then overwrites",
     async () => {
    await renderPage();
    refuse[`${RESULT}:loadStandings`] = {status: 409,
                                         code: "CONFIRMATION_REQUIRED",
                                         unlessConfirmed: true};

    fireEvent.click(button(region("result_layout"), "result_import"));
    await settle();
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(i18n.t("result_import_confirm_players")))
        .toBeInTheDocument();

    fireEvent.click(button(dialog, "stream_cancel"));
    await dialogGone();
    expect(writes("POST", `${RESULT}:loadStandings`)).toEqual([{}]);

    fireEvent.click(button(region("result_layout"), "result_import"));
    await settle();
    dialog = await screen.findByRole("dialog");
    fireEvent.click(button(dialog, "result_import_yes"));
    await settle();

    expect(writes("POST", `${RESULT}:loadStandings`))
        .toEqual([{}, {}, {confirm: true}]);
});

test("no tournament: the card says where to load one", async () => {
    await renderPage();
    refuse[`${RESULT}:loadStandings`] = {status: 409, code: "NOT_READY"};
    const card = region("result_layout");

    fireEvent.click(button(card, "result_import"));
    await settle();

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_import_no_tournament"));
});

test("an import on its way from elsewhere is said", async () => {
    await renderPage();
    refuse[`${RESULT}:loadStandings`] = {status: 409, code: "CONFLICT"};

    fireEvent.click(button(region("result_layout"), "result_import"));
    await settle();

    expect(await screen.findByText(i18n.t("result_import_running")))
        .toBeInTheDocument();
});

test.each(["fetch_failed", "no_standings", "changed", "import_failed"])(
    "the import's %s is on the card", async (code) => {
        await renderPage();
        const card = region("result_layout");

        fireEvent.click(button(card, "result_import"));
        await settle();
        setState({result_standings: {status: "error", error: code, revision: 1}});
        await settle();

        expect(within(card).getByRole("status"))
            .toHaveTextContent(i18n.t(`result_import_error_${code}`));
    });

test("another client's import is not told as this page's", async () => {
    await renderPage();
    const card = region("result_layout");

    setState({result_standings: {status: "done", error: "", revision: 7}});
    await settle();

    expect(within(card).queryByRole("status")).not.toBeInTheDocument();
});

// ── clearing ──────────────────────────────────────────────────────────────

test("clearing asks first and says which kind goes", async () => {
    await renderPage();
    const card = region("result_layout");

    fireEvent.click(button(card, "result_clear"));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(i18n.t("result_clear_confirm_players")))
        .toBeInTheDocument();
    fireEvent.click(button(dialog, "stream_cancel"));
    await dialogGone();
    expect(writes("POST", `${RESULT}:clear`)).toEqual([]);

    fireEvent.click(button(card, "result_clear"));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(button(dialog, "result_clear_yes"));
    await settle();

    expect(writes("POST", `${RESULT}:clear`))
        .toEqual([{kind: "players", confirm: true}]);
    expect(await screen.findByText(i18n.t("result_cleared"))).toBeInTheDocument();
});

test("on a team layout the teams are what is cleared", async () => {
    world.layout = "team";
    await renderPage();

    fireEvent.click(button(region("result_layout"), "result_clear"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(i18n.t("result_clear_confirm_teams")))
        .toBeInTheDocument();
    fireEvent.click(button(dialog, "result_clear_yes"));
    await settle();

    expect(writes("POST", `${RESULT}:clear`))
        .toEqual([{kind: "teams", confirm: true}]);
});

// ── the preview ───────────────────────────────────────────────────────────

test("changes of what is drawn are one preview, once they stop", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    setState({result_players: {"1": {name: "a"}}});
    await settle(PREVIEW_DEBOUNCE_MS - 100);
    setState({result_players: {"1": {name: "ab"}}});
    await settle(PREVIEW_DEBOUNCE_MS - 100);
    expect(previews()).toBe(1);

    await settle(100);
    expect(previews()).toBe(2);
});

test("a change that is not drawn asks for no preview: a handle, the post's own",
     async () => {
    setState({result_players: {"1": {name: "a", twitter: ""}}});
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const posts = reads(`${RESULT}/post`);

    setState({result_players: {"1": {name: "a", twitter: "@a"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(previews()).toBe(1);
    expect(reads(`${RESULT}/post`)).toBe(posts + 1);
});

test.each([
    ["the tournament", {tournamentInfo: {tournamentName: "Cup"}}],
    ["the banner", {tournamentInfo: {bannerVersion: 2}}],
    ["the colors", {colors: {result: {themeColor: "#112233"}}}],
    ["the teams", {result_teams: {"1": {name: "Reds"}}}],
])("%s changing is drawn", async (_name, state) => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    setState(state);
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(previews()).toBe(2);
});

test("the image is the revision result_render ended with", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");
    expect(within(card).getByText(i18n.t("result_previewing")))
        .toBeInTheDocument();
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();

    setState({result_render: rendered({status: "running"})});
    expect(within(card).getByText(i18n.t("result_previewing")))
        .toBeInTheDocument();

    setState({result_render: rendered({})});

    expect(within(card).queryByText(i18n.t("result_previewing")))
        .not.toBeInTheDocument();
    expect(within(card).getByRole("img", {name: i18n.t("result_preview_image")}))
        .toHaveAttribute("src", "http://localhost:5107/api/v1/result/preview?v=1");

    // A failed preview of the page's says why; the last image stays.
    setState({result_players: {"1": {name: "a"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    setState({result_render: rendered({status: "error", error: "render_failed",
                                       revision: 2})});

    expect(within(card).getByText(i18n.t("result_error_render_failed")))
        .toBeInTheDocument();
    expect(within(card).getByRole("img")).toHaveAttribute(
        "src", "http://localhost:5107/api/v1/result/preview?v=1");
});

test("the sample's words change with the language, drawn again", async () => {
    const page = await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    page.rerender(pageIn("ja"));
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(previews()).toBe(2);
});

test("rows of the operator's own do not change with the language", async () => {
    withData();
    const page = await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    page.rerender(pageIn("ja"));
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(previews()).toBe(1);
});

test("an image that cannot be shown says so", async () => {
    setState({result_render: rendered({})});
    await renderPage();
    const card = region("result_preview");

    fireEvent.error(within(card).getByRole("img"));

    expect(within(card).getByText(i18n.t("result_preview_failed_to_show")))
        .toBeInTheDocument();
});

test("hidden, nothing is read or drawn; shown again, once, changed or not",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    const before = {preview: previews(), rows: reads(RESULT),
                    post: reads(`${RESULT}/post`)};

    setVisible(false);
    setState({result_players: {"1": {name: "a"}}});
    await settle(PREVIEW_DEBOUNCE_MS * 2);
    await settle();
    expect(previews()).toBe(before.preview);
    expect(reads(RESULT)).toBe(before.rows);

    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(previews()).toBe(before.preview + 1);
    expect(reads(RESULT)).toBe(before.rows + 1);
    expect(reads(`${RESULT}/post`)).toBe(before.post + 1);

    // Without any change too: a background is settings.json's, not the state's.
    setVisible(false);
    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    expect(previews()).toBe(before.preview + 2);
});

test("hidden before the wait is over, the preview waits to be shown", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    setState({result_players: {"1": {name: "a"}}});
    await settle(100);
    setVisible(false);
    await settle(PREVIEW_DEBOUNCE_MS * 2);

    expect(previews()).toBe(1);
});

test("with nothing entered the preview is marked a sample", async () => {
    await renderPage();
    const card = region("result_preview");

    expect(within(card).getByText(i18n.t("result_sample"))).toBeInTheDocument();
    expect(within(card).getByText(i18n.t("result_sample_note")))
        .toBeInTheDocument();
    expect(within(card).getByText(i18n.t("result_background_note")))
        .toBeInTheDocument();
});

test("with a row entered it is not", async () => {
    withData();
    await renderPage();
    const card = region("result_preview");

    expect(within(card).queryByText(i18n.t("result_sample")))
        .not.toBeInTheDocument();
    expect(within(card).queryByText(i18n.t("result_sample_note")))
        .not.toBeInTheDocument();
});

test("the headless shell cannot draw: it says so and asks for nothing", async () => {
    world.canRender = false;
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS * 2);
    const card = region("result_preview");

    expect(within(card).getByText(i18n.t("result_cannot_render")))
        .toBeInTheDocument();
    expect(button(card, "result_generate")).toBeDisabled();
    expect(button(card, "result_post")).toBeDisabled();
    expect(within(card).queryByText(i18n.t("result_sample")))
        .not.toBeInTheDocument();
    expect(previews()).toBe(0);
});

test("a 503 reads the description again instead of saying a failure", async () => {
    await renderPage();
    const before = reads(RESULT);
    refuse[`${RESULT}:preview`] = {status: 503, code: "UNAVAILABLE"};
    world.canRender = false;

    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(reads(RESULT)).toBe(before + 1);
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
    expect(within(region("result_preview"))
        .getByText(i18n.t("result_cannot_render"))).toBeInTheDocument();
});

// ── making the image ──────────────────────────────────────────────────────

test("making is asked for once, and its end said with where it was saved",
     async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");

    fireEvent.click(button(card, "result_generate"));
    fireEvent.click(button(card, "result_generate"));
    expect(button(card, "result_generate")).toBeDisabled();
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_generating"));

    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_saved", {path: PATH}));
    expect(button(card, "result_generate")).toBeEnabled();
});

test("a failed making says why", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");

    fireEvent.click(button(card, "result_generate"));
    await settle();
    setState({result_render: rendered({status: "error", error: "save_failed",
                                       revision: 2})});
    await settle();

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_error_save_failed"));
});

test("a run past it (two state updates as one) still ends the making", async () => {
    withData();
    setState({result_render: rendered({path: "out/results/before.png"})});
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");

    fireEvent.click(button(card, "result_generate"));
    await settle();
    setState({result_render: rendered({path: PATH, revision: 9})});
    await settle();

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_saved", {path: PATH}));
});

test("nothing to make is said on the page, not as a failure", async () => {
    await renderPage();
    const card = region("result_preview");
    refuse[`${RESULT}:generate`] = {status: 400, code: "NO_DATA"};

    fireEvent.click(button(card, "result_generate"));
    await settle();
    await settle();

    expect(within(card).getByText(i18n.t("result_no_data"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

test("one on its way already is said, not a failure", async () => {
    withData();
    await renderPage();
    refuse[`${RESULT}:generate`] = {status: 409, code: "CONFLICT"};

    fireEvent.click(button(region("result_preview"), "result_generate"));
    await settle();

    expect(await screen.findByText(i18n.t("result_busy"))).toBeInTheDocument();
    expect(button(region("result_preview"), "result_generate")).toBeEnabled();
});

test("what is typed goes ahead of the making", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const name = field(2, "result_name");
    name.focus();
    fireEvent.change(name, {target: {value: "Bravo"}});

    fireEvent.click(button(region("result_preview"), "result_generate"));
    await settle();

    const paths = global.fetch.mock.calls
        .filter(([, init]) => init?.method)
        .map(([url]) => new URL(url).pathname);
    expect(paths.indexOf(`${RESULT}/players/2`)).toBeGreaterThanOrEqual(0);
    expect(paths.indexOf(`${RESULT}/players/2`))
        .toBeLessThan(paths.indexOf(`${RESULT}:generate`));
});

// The page has made the image of `state`: revision 2, the preview having been
// the first.
async function pageWithImage(state = {}) {
    setSecure(true);
    withData();
    setState(state);
    const page = await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    fireEvent.click(button(region("result_preview"), "result_generate"));
    await settle();
    setState({...state, result_render: rendered({path: PATH, revision: 2})});
    await settle();
    return page;
}

test("a secure page copies the image made, not before there is one", async () => {
    setSecure(true);
    withData();
    await renderPage();
    const card = region("result_preview");
    expect(button(card, "result_copy_image")).toBeDisabled();
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(button(card, "result_generate"));
    await settle();
    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();
    fireEvent.click(button(card, "result_copy_image"));
    await settle();
    await settle();

    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls.map(([url]) => url))
        .toContain(`http://localhost:5107/${ENCODED}?v=2`);
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
    expect(await screen.findByText(i18n.t("result_image_copied")))
        .toBeInTheDocument();
});

test("a copy after a change makes the image again, the clipboard asked in the click",
     async () => {
    await pageWithImage();
    const card = region("result_preview");
    setState({result_render: rendered({path: PATH, revision: 2}),
              result_players: {"1": {name: "Alpha2"}}});
    await settle();

    fireEvent.click(button(card, "result_copy_image"));

    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
    const image = navigator.clipboard.write.mock.calls[0][0][0]
        .items["image/png"];
    expect(image).toBeInstanceOf(Promise);
    await settle();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
    expect(global.fetch.mock.calls.map(([url]) => new URL(url).pathname))
        .not.toContain("/out/results/again.png");

    setState({result_render: rendered({path: "out/results/again.png",
                                       revision: 3}),
              result_players: {"1": {name: "Alpha2"}}});
    await settle();
    await settle();
    await settle();

    expect(global.fetch.mock.calls.map(([url]) => url))
        .toContain("http://localhost:5107/out/results/again.png?v=3");
    await expect(image).resolves.toBeInstanceOf(Blob);
    expect(await screen.findByText(i18n.t("result_image_copied")))
        .toBeInTheDocument();

    // And that one is the image now.
    fireEvent.click(button(card, "result_copy_image"));
    await settle();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
});

test("a change from elsewhere while it is made makes the copy make it again",
     async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");
    fireEvent.click(button(card, "result_generate"));
    await settle();

    // Another client's write came in during the run: on the picture or not, the
    // page cannot tell. What the state says at the end is not what was drawn.
    setState({result_players: {"1": {name: "Alpha2"}},
              result_render: rendered({path: PATH, revision: 2})});
    await settle();
    fireEvent.click(button(card, "result_copy_image"));
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
});

test("a copy right after typing makes it again, whose echo has not come yet",
     async () => {
    await pageWithImage();
    const name = field(1, "result_name");
    name.focus();
    fireEvent.change(name, {target: {value: "Alpha2"}});

    fireEvent.click(button(region("result_preview"), "result_copy_image"));
    await settle();

    expect(writes("PATCH", `${RESULT}/players/1`)).toEqual([{name: "Alpha2"}]);
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
});

test("an X handle is not on the picture: the copy is of the image made", async () => {
    await pageWithImage({result_players: {"1": {name: "Alpha"}}});

    await type(field(1, "result_twitter"), "@a");
    setState({result_render: rendered({path: PATH, revision: 2}),
              result_players: {"1": {name: "Alpha", twitter: "@a"}}});
    await settle();
    fireEvent.click(button(region("result_preview"), "result_copy_image"));
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
});

test("shown again, the copy makes it again: the background is not the state's",
     async () => {
    await pageWithImage();

    setVisible(false);
    setVisible(true);
    await settle();
    fireEvent.click(button(region("result_preview"), "result_copy_image"));
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
});

test("an image from before the page opened is made again for the copy", async () => {
    setSecure(true);
    withData();
    setState({result_render: rendered({path: PATH, revision: 1})});
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(button(region("result_preview"), "result_copy_image"));
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
});

test("with nothing to draw any more the copy says so, and copies nothing", async () => {
    setSecure(true);
    setState({result_render: rendered({path: PATH, revision: 1})});
    await renderPage();
    const card = region("result_preview");

    fireEvent.click(button(card, "result_copy_image"));
    await settle();

    expect(navigator.clipboard.write).not.toHaveBeenCalled();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([]);
    expect(within(card).getByText(i18n.t("result_no_data"))).toBeInTheDocument();
});

test("a click between the making's answer and the next render asks for nothing",
     async () => {
    await pageWithImage();
    const card = region("result_preview");
    const release = hold("POST", `${RESULT}:generate`);
    fireEvent.click(button(card, "result_generate"));
    await settle();
    const copy = button(card, "result_copy_image");

    await act(async () => {
        release();
        for (let i = 0; i < 12; i++) {
            await Promise.resolve();
        }
        // Answered, and what is on the screen is from before it.
        expect(copy).toBeEnabled();
        fireEvent.click(copy);
    });
    await settle();

    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}, {}]);
    expect(navigator.clipboard.write).not.toHaveBeenCalled();
});

test("what was said of the making goes with an input", async () => {
    await renderPage();
    const card = region("result_preview");
    refuse[`${RESULT}:generate`] = {status: 400, code: "NO_DATA"};
    fireEvent.click(button(card, "result_generate"));
    await settle();
    await settle();
    expect(within(card).getByText(i18n.t("result_no_data"))).toBeInTheDocument();

    setState({result_players: {"1": {name: "a"}}});

    expect(within(card).queryByText(i18n.t("result_no_data")))
        .not.toBeInTheDocument();
});

test("a failed making's words go with an input too", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");
    fireEvent.click(button(card, "result_generate"));
    await settle();
    setState({result_render: rendered({status: "error", error: "save_failed",
                                       revision: 2})});
    await settle();
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_error_save_failed"));

    setState({result_render: rendered({status: "error", error: "save_failed",
                                       revision: 2}),
              result_players: {"1": {name: "a"}}});

    expect(within(card).queryByRole("status")).not.toBeInTheDocument();
});

test("clearing takes what was said of the making with it", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");
    fireEvent.click(button(card, "result_generate"));
    await settle();
    setState({result_render: rendered({status: "error", error: "save_failed",
                                       revision: 2})});
    await settle();

    fireEvent.click(button(region("result_layout"), "result_clear"));
    fireEvent.click(button(await screen.findByRole("dialog"), "result_clear_yes"));
    await settle();

    expect(within(card).queryByRole("status")).not.toBeInTheDocument();
});

test("the file made last is said when the page opens, or when another makes one",
     async () => {
    setState({result_render: rendered({path: PATH, revision: 1})});
    await renderPage();
    const card = region("result_preview");

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_saved", {path: PATH}));

    setState({result_render: rendered({path: "out/results/other.png",
                                       revision: 2})});

    expect(within(card).getByRole("status")).toHaveTextContent(
        i18n.t("result_saved", {path: "out/results/other.png"}));
});

test("a page that cannot copy offers the image made to open", async () => {
    setState({result_render: rendered({path: PATH, revision: 4})});
    await renderPage();
    const card = region("result_preview");

    expect(within(card).queryByRole("button", {name: i18n.t("result_copy_image")}))
        .not.toBeInTheDocument();
    expect(within(card).getByRole("link", {name: i18n.t("result_open_image")}))
        .toHaveAttribute("href", `http://localhost:5107/${ENCODED}?v=4`);
    expect(within(card).getByText(i18n.t("result_post_note_no_copy")))
        .toBeInTheDocument();
});

// ── the post on X ─────────────────────────────────────────────────────────

test("one click asks the clipboard and opens the composer before anything can wait",
     async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const order = [];
    navigator.clipboard.write.mockImplementation(async (items) => {
        order.push("clipboard");
        await items[0].items["image/png"];
    });
    opened.mockImplementation(() => {
        order.push("composer");
        return null;
    });
    const card = region("result_preview");

    fireEvent.click(button(card, "result_post"));

    // In the click itself, the clipboard first: it goes to a page with the
    // focus, and the composer takes the focus away.
    expect(order).toEqual(["clipboard", "composer"]);
    expect(opened).toHaveBeenCalledWith(
        `${X_URL}${encodeURIComponent(POST_TEXT)}`, "_blank", "noopener");
    expect(navigator.clipboard.write.mock.calls[0][0][0].items["image/png"])
        .toBeInstanceOf(Promise);

    // And the image was asked for by it.
    await settle();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
});

test("the image reaches the clipboard once it is made", async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();

    let image = null;
    navigator.clipboard.write.mock.calls[0][0][0].items["image/png"]
        .then((blob) => {
            image = blob;
        });
    await settle();
    expect(image).toBeNull();
    expect(global.fetch.mock.calls.map(([url]) => url))
        .not.toContain(`http://localhost:5107/${ENCODED}?v=2`);

    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();
    await settle();
    await settle();

    expect(global.fetch.mock.calls.map(([url]) => url))
        .toContain(`http://localhost:5107/${ENCODED}?v=2`);
    expect(image).toBeInstanceOf(Blob);
    expect(await screen.findByText(i18n.t("result_post_copied")))
        .toBeInTheDocument();
});

test("a failed image is told once, beside the composer already open", async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();
    setState({result_render: rendered({status: "error", error: "render_failed",
                                       revision: 2})});
    await settle();
    await settle();

    expect(opened).toHaveBeenCalledTimes(1);
    expect(within(region("result_preview")).getByRole("status"))
        .toHaveTextContent(i18n.t("result_error_render_failed"));
    expect(screen.getAllByText(i18n.t("result_error_render_failed")).length)
        .toBeGreaterThan(1);
    expect(screen.queryByText(i18n.t("result_post_copy_failed")))
        .not.toBeInTheDocument();
});

test("a clipboard that refuses is told on its own", async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    navigator.clipboard.write.mockImplementation(async () => {
        throw new Error("denied");
    });

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("result_post_copy_failed")))
        .toBeInTheDocument();
});

test("one on its way already is what the post's click is told", async () => {
    setSecure(true);
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    refuse[`${RESULT}:generate`] = {status: 409, code: "CONFLICT"};

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("result_busy"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("result_post_copy_failed")))
        .not.toBeInTheDocument();
});

test("an image too late is refused: the write does not come over what was copied since",
     async () => {
    setSecure(true);
    const copying = copyImageOnceMade(new Promise(() => {}));
    let failed = false;
    copying.catch(() => {
        failed = true;
    });
    let refused = false;
    navigator.clipboard.write.mock.calls[0][0][0].items["image/png"]
        .catch(() => {
            refused = true;
        });

    await settle(MADE_IMAGE_COPY_TIMEOUT_MS - 1);
    expect(refused).toBe(false);

    await settle(1);
    expect(refused).toBe(true);
    expect(failed).toBe(true);
});

// A page frozen while another panel is shown (HiddenPageLifecycle) comes back
// with its timers due ahead of the messages that came in meanwhile: the store
// says nothing yet of a run that may be over.
async function runPastItsDeadline(clicked) {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    fireEvent.click(button(region("result_preview"), clicked));
    await settle();
}

async function passTheDeadline() {
    await settle(MADE_IMAGE_COPY_TIMEOUT_MS);
    await settle();
    await settle();
}

test("past the deadline the server says the image was made", async () => {
    await runPastItsDeadline("result_generate");
    world.render = rendered({path: PATH, revision: 2});
    const before = reads(RESULT);

    await passTheDeadline();

    expect(reads(RESULT)).toBe(before + 1);
    // On the status line and in the snackbar.
    expect(screen.getAllByText(i18n.t("result_saved", {path: PATH})))
        .toHaveLength(2);
    expect(screen.getByRole("alert")).toHaveTextContent(
        i18n.t("result_saved", {path: PATH}));
    expect(screen.queryByText(i18n.t("result_error_render_failed")))
        .not.toBeInTheDocument();
});

test("past the deadline the server says the run ended badly", async () => {
    await runPastItsDeadline("result_generate");
    world.render = rendered({status: "error", error: "render_failed", revision: 2});

    await passTheDeadline();

    expect(screen.getByRole("alert")).toHaveTextContent(
        i18n.t("result_error_render_failed"));
});

test.each([
    ["still draws it", () => {
        world.render = {status: "running", path: "", error: "", revision: 2};
    }],
    ["does not answer", () => {
        refuse[RESULT] = {status: 500};
        global.fetch.mockImplementationOnce(async () => {
            throw new Error("gone");
        });
    }],
])("past the deadline it is a failure when the server %s", async (_name, set) => {
    await runPastItsDeadline("result_generate");
    set();

    await passTheDeadline();

    expect(screen.queryByText(i18n.t("result_saved", {path: PATH})))
        .not.toBeInTheDocument();
    expect(button(region("result_preview"), "result_generate")).toBeEnabled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
});

test("the store catching up while the server is asked ends the run once", async () => {
    await runPastItsDeadline("result_generate");
    world.render = rendered({path: PATH, revision: 2});
    const release = hold("GET", RESULT);

    await settle(MADE_IMAGE_COPY_TIMEOUT_MS);
    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();
    expect(screen.getByRole("alert")).toHaveTextContent(
        i18n.t("result_saved", {path: PATH}));

    const shown = screen.getByRole("alert");
    release();
    await settle();
    await settle();
    // Keyed by the time it was shown: a second one would be a new element.
    expect(screen.getByRole("alert")).toBe(shown);
});

test("back past the deadline the copy is refused: the image is saved, and that is said",
     async () => {
    setSecure(true);
    await runPastItsDeadline("result_post");
    const image = navigator.clipboard.write.mock.calls[0][0][0]
        .items["image/png"];
    image.catch(() => undefined);
    world.render = rendered({path: PATH, revision: 2});

    await passTheDeadline();

    await expect(image).rejects.toMatchObject({name: "TimeoutError"});
    // Over "saved", which the status line goes on saying.
    expect(screen.getByRole("alert")).toHaveTextContent(
        i18n.t("result_post_copy_failed"));
    expect(screen.getByText(i18n.t("result_saved", {path: PATH})))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("result_error_render_failed")))
        .not.toBeInTheDocument();
    // Nothing is lost: the copy makes nothing again.
    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();
    navigator.clipboard.write.mockClear();
    fireEvent.click(button(region("result_preview"), "result_copy_image"));
    await settle();
    await settle();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
});

test("a run that ended in nothing tells that, and not that the copy failed as well",
     async () => {
    setSecure(true);
    await runPastItsDeadline("result_post");
    world.render = rendered({status: "error", error: "render_failed", revision: 2});

    await passTheDeadline();

    expect(screen.getByRole("alert")).toHaveTextContent(
        i18n.t("result_error_render_failed"));
    expect(screen.queryByText(i18n.t("result_post_copy_failed")))
        .not.toBeInTheDocument();
});

test("a page that cannot copy opens the composer and says where the image was saved",
     async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");

    fireEvent.click(button(card, "result_post"));
    await settle();

    expect(navigator.clipboard.write).not.toHaveBeenCalled();
    expect(opened).toHaveBeenCalledTimes(1);
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);

    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("result_saved", {path: PATH}));
});

test("nothing to post: nothing is made, nothing opens, the page says so",
     async () => {
    setSecure(true);
    await renderPage();
    const card = region("result_preview");

    fireEvent.click(button(card, "result_post"));
    await settle();

    expect(opened).not.toHaveBeenCalled();
    expect(navigator.clipboard.write).not.toHaveBeenCalled();
    expect(writes("POST", `${RESULT}:generate`)).toEqual([]);
    expect(within(card).getByText(i18n.t("result_no_data"))).toBeInTheDocument();
});

test("while the image is being made the post waits its turn", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("result_preview");

    fireEvent.click(button(card, "result_generate"));
    await settle();

    expect(button(card, "result_post")).toBeDisabled();
    expect(opened).not.toHaveBeenCalled();
});

test("a post right after typing the first name is not stopped by the sample",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const name = field(1, "result_name");
    name.focus();
    fireEvent.change(name, {target: {value: "Alpha"}});

    fireEvent.click(button(region("result_preview"), "result_post"));

    expect(opened).toHaveBeenCalledTimes(1);
    await settle();
    const paths = global.fetch.mock.calls
        .filter(([, init]) => init?.method)
        .map(([url]) => new URL(url).pathname);
    expect(paths.indexOf(`${RESULT}/players/1`)).toBeGreaterThanOrEqual(0);
    expect(paths.indexOf(`${RESULT}/players/1`))
        .toBeLessThan(paths.indexOf(`${RESULT}:generate`));
});

test("a post while the rows are not known that had nothing to draw says so after",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    refuse[`${RESULT}:generate`] = {status: 400, code: "NO_DATA"};
    const name = field(1, "result_name");
    name.focus();
    fireEvent.change(name, {target: {value: "  "}});

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();
    await settle();

    expect(opened).toHaveBeenCalledTimes(1);
    expect(within(region("result_preview")).getByText(i18n.t("result_no_data")))
        .toBeInTheDocument();
});

test("two clicks in the same frame are one post", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const post = button(region("result_preview"), "result_post");

    fireEvent.click(post);
    fireEvent.click(post);
    await settle();

    expect(opened).toHaveBeenCalledTimes(1);
    expect(writes("POST", `${RESULT}:generate`)).toEqual([{}]);
});

test("the post's text goes with a row changed: it is read again", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    world.post = "Changed & new";
    setState({result_players: {"1": {name: "Alpha"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    fireEvent.click(button(region("result_preview"), "result_post"));

    expect(opened).toHaveBeenCalledWith(
        `${X_URL}${encodeURIComponent("Changed & new")}`, "_blank", "noopener");
});

test("without the token the post is not offered, and the page says why",
     async () => {
    withData();
    refuse[`${RESULT}/post`] = {status: 401, code: "UNAUTHENTICATED"};
    await renderPage();
    const card = region("result_preview");

    expect(button(card, "result_post")).toBeDisabled();
    expect(within(card).getByText(i18n.t("result_post_token")))
        .toBeInTheDocument();
});

test("a post's text not read for another reason is tried again, then says so, and can be asked for",
     async () => {
    withData();
    refuse[`${RESULT}/post`] = {status: 500};
    await renderPage();
    const card = region("result_preview");

    expect(button(card, "result_post")).toBeDisabled();
    expect(within(card).queryByText(i18n.t("result_post_failed")))
        .not.toBeInTheDocument();
    expect(reads(`${RESULT}/post`)).toBe(1);

    await settle(LOAD_RETRY_MS);
    await settle(LOAD_RETRY_MS * 2);
    await settle();

    expect(reads(`${RESULT}/post`)).toBe(LOAD_ATTEMPTS);
    expect(within(card).getByText(i18n.t("result_post_failed")))
        .toBeInTheDocument();
    expect(within(card).queryByText(i18n.t("result_post_token")))
        .not.toBeInTheDocument();
    expect(button(card, "result_post")).toBeDisabled();

    delete refuse[`${RESULT}/post`];
    fireEvent.click(button(card, "result_reload"));
    await settle();

    expect(within(card).queryByText(i18n.t("result_post_failed")))
        .not.toBeInTheDocument();
    expect(button(card, "result_post")).toBeEnabled();
});

test("a post's text answered late does not replace a newer one", async () => {
    withData();
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const usual = global.fetch.getMockImplementation();
    let answerOld = null;
    global.fetch.mockImplementation((url, init) => {
        if (new URL(url).pathname === `${RESULT}/post` && !answerOld) {
            return new Promise((resolve) => {
                answerOld = () => resolve(answer(200, {
                    ok: true, data: {text: "Old"}}));
            });
        }
        return usual(url, init);
    });

    setState({result_players: {"1": {name: "a"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    expect(answerOld).not.toBeNull();

    world.post = "New";
    setState({result_players: {"1": {name: "ab"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    answerOld();
    await settle();

    fireEvent.click(button(region("result_preview"), "result_post"));

    expect(opened).toHaveBeenCalledWith(
        `${X_URL}${encodeURIComponent("New")}`, "_blank", "noopener");
});

test("the page going away with a post on its way still copies the image", async () => {
    setSecure(true);
    withData();
    const page = await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(button(region("result_preview"), "result_post"));
    await settle();
    const image = navigator.clipboard.write.mock.calls[0][0][0]
        .items["image/png"];

    page.unmount();
    await settle(PREVIEW_DEBOUNCE_MS * 2);
    setState({result_render: rendered({path: PATH, revision: 2})});
    await settle();
    await settle();
    await settle();

    expect(global.fetch.mock.calls.map(([url]) => url))
        .toContain(`http://localhost:5107/${ENCODED}?v=2`);
    await expect(image).resolves.toBeInstanceOf(Blob);
});

test("the page going away drops what was waiting", async () => {
    const page = await renderPage();
    setState({result_players: {"1": {name: "a"}}});

    page.unmount();
    await settle(PREVIEW_DEBOUNCE_MS * 2);

    expect(previews()).toBe(0);
});

// ── what reaches the page as rows ──────

// As websocketInit does with a message of the server's.
function receive(delta) {
    stateRevision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.addDeltas({
            revision: stateRevision, first_revision: stateRevision, delta}));
        autonaStore.dispatch(autonaStateSlice.actions.applySavedDeltas());
    });
}

test("a character removed elsewhere is a change of the picture", async () => {
    const mario = {en_name: "Mario", assets: {full: {asset: "x/mario.png"}}};
    const luigi = {en_name: "Luigi", assets: {full: {asset: "x/luigi.png"}}};
    world.players[0].name = "Alpha";
    world.sample = false;
    setState({result_players: {"1": {name: "Alpha", character: mario,
                                     characters: [mario, luigi]}}});
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const before = previews();

    receive([{path: ["result_players", "1", "characters", 1],
              action: "iterable_item_removed", value: luigi}]);
    await settle(PREVIEW_DEBOUNCE_MS);

    expect(previews()).toBe(before + 1);
    expect(autonaStore.getState().autonaState.autonaState
        .result_players["1"].characters).toEqual([mario]);
});

test("a member removed elsewhere is a change of the post's text, and leaves its field",
     async () => {
    world.layout = "team";
    world.teams[0].name = "Reds";
    world.teams[0].members = [
        {name: "A1", twitter: "", character: null, fromScoreboard: null},
        {name: "A2", twitter: "", character: null, fromScoreboard: null}];
    setState({result_teams: {"1": {name: "Reds", members: [
        {name: "A1", twitter: "", character: {}},
        {name: "A2", twitter: "", character: {}}]}}});
    await renderPage();
    await settle(READ_DEBOUNCE_MS);
    const member = within(memberGroup(1, 2))
        .getByLabelText(i18n.t("result_name"), {selector: "input"});
    expect(member).toHaveValue("A2");
    const before = reads(`${RESULT}/post`);

    world.teams[0].members.pop();
    receive([{path: ["result_teams", "1", "members", 1],
              action: "iterable_item_removed",
              value: {name: "A2", twitter: "", character: {}}}]);
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    expect(reads(`${RESULT}/post`)).toBe(before + 1);
    expect(member).toHaveValue("");
});

// ── the rest ──────────────────────────────────────────────────────────────

test("saving to the database says how many, and reads the post's text again",
     async () => {
    await renderPage();
    const before = reads(`${RESULT}/post`);

    fireEvent.click(button(region("result_save_db"), "result_save_db"));
    await settle();
    await settle();

    expect(writes("POST", `${RESULT}:saveToDb`)).toEqual([{}]);
    expect(await screen.findByText(i18n.t("result_save_db_done", {count: 3})))
        .toBeInTheDocument();
    expect(reads(`${RESULT}/post`)).toBe(before + 1);
});

test("no one to save is said as such", async () => {
    world.saved = 0;
    await renderPage();

    fireEvent.click(button(region("result_save_db"), "result_save_db"));
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("result_save_db_none")))
        .toBeInTheDocument();
});

test("a refusal for want of the token is told once", async () => {
    await renderPage();
    refuse[`${RESULT}/players/1`] = {status: 401, code: "UNAUTHENTICATED"};
    refuse[`${RESULT}/players/2`] = {status: 401, code: "UNAUTHENTICATED"};

    await type(field(1, "result_name"), "a");
    await type(field(2, "result_name"), "b");
    await settle();

    expect(await screen.findAllByText(i18n.t("stream_token_required")))
        .toHaveLength(1);
});

test("the wording of the database's card is what it does", () => {
    // Decision 20: saved to the player list, not "easily called up".
    expect(i18n.t("result_save_db_note", {lng: "en"}))
        .not.toMatch(/easily/i);
    expect(i18n.t("result_save_db_note", {lng: "ja"}))
        .not.toMatch(/かんたん/);
});
