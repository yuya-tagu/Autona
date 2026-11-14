import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {BACKEND_PORT} from "../env";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import {WRITE_TIMEOUT_MS} from "../PanelShell/requests";
import {PanelShellContext} from "../PanelShell/context";
import Top8Page from "./index";
import {assetUrl, scoreValue} from "./Cards";
import {characterValue} from "./characterPick";
import table from "./characterPick.cases.json";

// The Top 8 panel in React (ADR-005 S4i): it shows top8.* and the game
// from the state, the countries from GET /api/v1/countries, the characters
// of the state's game from GET /api/v1/characters and the designs, and
// writes through /api/v1 (a player's fields as identifiers, the caption,
// the staged scores, their reset, the import, the colours, the design).

// The page is a large tree of MUI fields (8 player cards, 22 score
// fields): in jsdom each event on it costs tens of milliseconds, and a test
// here fires a dozen. 1-3 s each on an idle machine; the default 5 s left
// no room on a loaded one.
jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn(), off: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const COUNTRIES = [
    {code: "JP", label: "日本 / Japan (JP)",
     asset: "./assets/country_flag/jp.png", custom: false},
    {code: "US", label: "アメリカ合衆国 / United States (US)",
     asset: "./assets/country_flag/us.png", custom: false},
    {code: "My Flag", label: "My Flag",
     asset: "./user_data/additional_flag/My Flag.png", custom: true},
];
const CHARACTERS = table.characters;
const BRACKET = {id: "bracket_top8", kind: "top8",
                 displayName: "Top 8", entry: "index.html"};
const CUSTOM = {id: "custom/top8", kind: "top8",
                displayName: "Custom", entry: "index.html"};
const MATCH_IDS = ["wsf1", "wsf2", "lr1a", "lr1b", "wf", "lqf1", "lqf2",
                   "lsf", "lf", "gf", "gf_reset"];
const FIRST = {wsf1: [1, 2], wsf2: [3, 4], lr1a: [5, 6], lr1b: [7, 8]};

function matches(over = {}) {
    const all = {};
    MATCH_IDS.forEach((id) => {
        all[id] = {name: id, slots: FIRST[id] ?? [0, 0], score: [0, 0],
                   finished: false, active: false, ...(over[id] ?? {})};
    });
    return all;
}

function player(fields = {}) {
    return {name: "", sponsor: "", country: {}, characters: {}, ...fields};
}

function players(over = {}) {
    const all = {};
    ["1", "2", "3", "4", "5", "6", "7", "8"].forEach((slot) => {
        all[slot] = player(over[slot]);
    });
    return all;
}

const GAME = {codename: "tgame"};
let revision = 0;

function setState(top8 = {}, game = GAME) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                top8: {phaseLabel: "TOP 8", players: players(),
                       matches: matches(), ...top8},
                game,
                colors: {top8: {themeColor: "#112233", accentColor: "#445566"}},
                tournamentInfo: {themeColor: "#181B21", accentColor: "#f0a500"},
            },
            revision,
        }));
    });
}

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

// Write path suffix -> the status it answers, or [status, the error's code].
let refuse;
// GET path -> [status, code] it answers instead.
let failing;
// The characters GET's answer: a promise the test holds, when set.
let charactersGate;

function writes(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && new URL(url).pathname === path)
        .map(([, init]) => (init.body ? JSON.parse(init.body) : null));
}

function reads(path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "GET"
            && new URL(url).pathname === path).length;
}

function sent() {
    return global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET");
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

async function renderPage() {
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "top8", desktop: false, language: "en"}}>
                <Top8Page/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await screen.findByRole("region", {name: i18n.t("top8_players")});
}

// The one element `selector` matches under `root` (none or several fail,
// as a getBy query does).
function only(root, selector) {
    const found = root.querySelectorAll(selector);
    expect(found).toHaveLength(1);
    return found[0];
}

// By their aria-label, not by role or label text: those queries walk every
// element of this large tree (8 cards, 22 score fields), and these are
// called in loops. The tests that pin the roles (region, textbox,
// combobox, group) query them by role.
function card(slot) {
    return only(document,
                `section[aria-label="${i18n.t("top8_slot", {slot})}"]`);
}

function field(slot, key) {
    return within(card(slot)).getByLabelText(i18n.t(key),
                                             {selector: "input"});
}

function combo(slot, key) {
    return within(card(slot)).getByLabelText(i18n.t(key),
                                             {selector: "[role=combobox]"});
}

function scoreBox(match, side) {
    return only(document, `input[aria-label="${
        i18n.t("top8_score", {match, side})}"]`);
}

function button(key) {
    return screen.getByRole("button", {name: i18n.t(key)});
}

async function type(input, value) {
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
}

// An Autocomplete's clear button: shown on hover / focus only (hidden to
// the accessibility tree otherwise, and so without a name there).
function clearButton(slot, key) {
    return combo(slot, key).closest(".MuiAutocomplete-root")
        .querySelector("button[title='Clear']");
}

async function pickOption(input, typed, name) {
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value: typed}});
    fireEvent.click(await screen.findByRole("option", {name}));
    await settle();
}

beforeEach(() => {
    jest.useFakeTimers();
    refuse = {};
    failing = {};
    charactersGate = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path, searchParams} = new URL(url);
        if (method === "GET") {
            if (failing[path]) {
                const [status, code] = failing[path];
                return answer(status, {ok: false, error: {code, message: "no"}});
            }
            if (path === "/api/v1/countries") {
                return answer(200, {ok: true, data: COUNTRIES});
            }
            if (path === "/api/v1/characters") {
                if (charactersGate) {
                    return charactersGate;
                }
                return answer(200, {ok: true, data: CHARACTERS});
            }
            if (path === "/api/v1/layouts") {
                expect(searchParams.get("kind")).toBe("top8");
                return answer(200, {ok: true, data: [BRACKET, CUSTOM]});
            }
            if (path === "/api/v1/layouts/selected/top8") {
                return answer(200, {ok: true, data: BRACKET});
            }
            if (path === "/api/v1/health") {
                // The preview listener's port (ADR-005 S-final SF-8a).
                return answer(200, {ok: true, data: {previewPort: 52345}});
            }
            return answer(404, {ok: false});
        }
        const refused = Object.keys(refuse).find((suffix) => path.endsWith(suffix));
        if (refused) {
            const [status, code] = [].concat(refuse[refused], "X");
            return answer(status, {ok: false, error: {code, message: "no"}});
        }
        if (path === "/api/v1/bracket:refresh") {
            return answer(202, {ok: true, data: {accepted: true,
                                                 operation: "bracket.refresh"}});
        }
        if (path === "/api/v1/layouts/selected/top8") {
            return answer(200, {ok: true, data: CUSTOM, revision: 1});
        }
        if (path === "/api/v1/top8/scores") {
            return answer(200, {ok: true, data: matches(), revision: 1});
        }
        return answer(200, {ok: true, revision: 1});
    });
    setState();
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

// ── the page ──────────────────────────────────────────────────────────────

test("the eight player cards and the cards around them come up", async () => {
    await renderPage();

    expect(screen.getAllByRole("region", {name: /^Slot \d$/})).toHaveLength(8);
    expect(within(card("1")).getByText("1 · WSF1")).toBeInTheDocument();
    expect(within(card("8")).getByText("8 · LR1B")).toBeInTheDocument();
    ["top8_players", "top8_scores", "top8_colors", "commentary_obs_title"]
        .forEach((key) => expect(screen.getByRole("region",
                                                  {name: i18n.t(key)}))
            .toBeInTheDocument());
    expect(button("top8_import")).toBeEnabled();
    expect(document.title).toBe(`Autona ${i18n.t("top8_title")}`);
    expect(reads("/api/v1/countries")).toBe(1);
    expect(reads("/api/v1/characters")).toBe(1);
    expect(reads("/api/v1/layouts")).toBe(1);
    expect(reads("/api/v1/layouts/selected/top8")).toBe(1);
});

test("mounting and state updates alone send nothing", async () => {
    await renderPage();
    setState({phaseLabel: "TOP 8 FINALS",
              players: players({"1": {name: "Alice", country: {code: "JP"}}}),
              matches: matches({wsf1: {score: [2, 1], finished: true}})});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    await settle(WRITE_TIMEOUT_MS);

    expect(sent()).toEqual([]);
});

test("the design is the selected one, previewed and given to OBS", async () => {
    await renderPage();

    const select = screen.getByRole("combobox", {name: i18n.t("top8_design")});
    expect(select).toHaveTextContent("Top 8");
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/bracket_top8\/index\.html$/));
    expect(screen.getByRole("textbox", {name: i18n.t("commentary_obs_url")}))
        .toHaveValue(`http://localhost:${BACKEND_PORT}/layout/bracket_top8/index.html`);
    expect(screen.getByText(i18n.t("top8_obs_note"))).toBeInTheDocument();

    fireEvent.mouseDown(select);
    fireEvent.click(screen.getByRole("option", {name: "Custom"}));
    await settle();

    expect(writes("PUT", "/api/v1/layouts/selected/top8"))
        .toEqual([{id: "custom/top8"}]);
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/custom\/top8\/index\.html$/));
});

test("choosing the design shown saves it: it may be a fallback", async () => {
    // A pick of the value it holds is nothing to the select.
    await renderPage();

    const select = screen.getByRole("combobox", {name: i18n.t("top8_design")});
    fireEvent.mouseDown(select);
    fireEvent.click(screen.getByRole("option", {name: "Top 8"}));
    await settle();

    expect(writes("PUT", "/api/v1/layouts/selected/top8"))
        .toEqual([{id: "bracket_top8"}]);
});

test("a list that cannot be read shows the reload button", async () => {
    failing["/api/v1/layouts"] = [400, "INVALID_ARGUMENT"];
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "top8", desktop: false, language: "en"}}>
                <Top8Page/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();

    expect(await screen.findByText(i18n.t("top8_load_failed")))
        .toBeInTheDocument();
    delete failing["/api/v1/layouts"];
    fireEvent.click(button("top8_reload"));
    await settle();

    expect(await screen.findByRole("region", {name: i18n.t("top8_players")}))
        .toBeInTheDocument();
    expect(reads("/api/v1/layouts")).toBe(2);
});

test("without the country data the country fields wait, the rest works", async () => {
    failing["/api/v1/countries"] = [409, "NOT_READY"];
    setState({players: players({"2": {country: {code: "US", name: "x"}}})});
    await renderPage();

    expect(screen.getByText(i18n.t("top8_countries_unavailable")))
        .toBeInTheDocument();
    expect(combo("1", "top8_country")).toBeDisabled();
    // The state's country still shows, as its code.
    expect(combo("2", "top8_country")).toHaveValue("US");
    // Everything else is usable.
    expect(combo("1", "top8_character")).toBeEnabled();
    await type(field("1", "top8_name"), "Alice");
    expect(writes("PATCH", "/api/v1/top8/players/1")).toEqual([{name: "Alice"}]);

    delete failing["/api/v1/countries"];
    fireEvent.click(button("top8_reload"));
    await settle();

    expect(reads("/api/v1/countries")).toBe(2);
    expect(combo("1", "top8_country")).toBeEnabled();
    expect(combo("2", "top8_country"))
        .toHaveValue("アメリカ合衆国 / United States (US)");
    expect(screen.queryByText(i18n.t("top8_countries_unavailable"))).toBeNull();
});

test("an asset's path is encoded segment by segment", () => {
    expect(assetUrl("./user_data/additional_flag/A#B?.png")).toBe(
        `http://localhost:${BACKEND_PORT}/user_data/additional_flag/A%23B%3F.png`);
    expect(assetUrl("")).toBeNull();
});

// ── a player ──────────────────────────────────────────────────────────────

test("the name and the sponsor are sent as typed, each on its own", async () => {
    await renderPage();

    await type(field("3", "top8_name"), "Carol");
    await type(field("3", "top8_sponsor"), "TEAM");

    expect(writes("PATCH", "/api/v1/top8/players/3"))
        .toEqual([{name: "Carol"}, {sponsor: "TEAM"}]);
    expect(sent()).toHaveLength(2);
});

test("a country is sent as its code, and emptied as null", async () => {
    setState({players: players({"2": {country: {code: "US", name: "x"}}})});
    await renderPage();

    const country = combo("2", "top8_country");
    expect(country).toHaveValue("アメリカ合衆国 / United States (US)");
    // Its flag ahead of it.
    expect(card("2").querySelector("img[src$='/assets/country_flag/us.png']"))
        .not.toBeNull();

    await pickOption(combo("1", "top8_country"), "japan", "日本 / Japan (JP)");
    await pickOption(combo("1", "top8_country"), "flag", "My Flag");
    fireEvent.click(clearButton("2", "top8_country"));
    await settle();

    expect(writes("PATCH", "/api/v1/top8/players/1"))
        .toEqual([{country: "JP"}, {country: "My Flag"}]);
    expect(writes("PATCH", "/api/v1/top8/players/2"))
        .toEqual([{country: null}]);
});

test("the character and colour show the pick core would make", async () => {
    setState({players: players({
        // The page's own value: Bowser, his second colour.
        "1": {characters: {"1": characterValue(CHARACTERS.Bowser, 1)}},
        // A character without colours.
        "2": {characters: {"1": characterValue(CHARACTERS.NoArt, 0)}},
        // An import of a character the game does not have.
        "3": {characters: {"1": {en_name: "Nobody", codename: "nobody"}}},
    })});
    await renderPage();

    expect(combo("1", "top8_character")).toHaveValue("Bowser");
    expect(combo("1", "top8_color")).toHaveTextContent("2");
    expect(combo("2", "top8_character")).toHaveValue("NoArt");
    expect(combo("2", "top8_color")).toHaveAttribute("aria-disabled", "true");
    expect(combo("3", "top8_character")).toHaveValue("");
    expect(combo("3", "top8_color")).toHaveAttribute("aria-disabled", "true");
});

test("every pick of the table shows on the card", async () => {
    // The table's cases against this game's list (not reversed), each in
    // slot 1: the select shows the character, the colour select the row.
    const cases = table.cases.filter((c) => !c.reversed && c.pick[0]);
    // Only slot 1 changes between cases (the other players and the matches
    // stay the same objects, as a state delta leaves them): the memoized
    // cards and score rows are not drawn again.
    const others = players();
    const steady = matches();
    await renderPage();
    for (const c of cases) {
        setState({players: {...others, "1": player({characters: {"1": c.value}})},
                  matches: steady});
        await settle();
        const entry = Object.values(CHARACTERS)
            .find((row) => row.en_name === c.pick[0]);
        expect(combo("1", "top8_character")).toHaveValue(entry.name);
        if (c.pick[1] >= 0) {
            expect(combo("1", "top8_color"))
                .toHaveTextContent(String(c.pick[1] + 1));
        }
    }
    expect(sent()).toEqual([]);
}, 60000);

test("a character, a colour and none are sent as identifiers", async () => {
    setState({players: players({
        "1": {characters: {"1": characterValue(CHARACTERS.Bowser, 0)}},
    })});
    await renderPage();

    await pickOption(combo("2", "top8_character"), "Peach", "Peach");
    fireEvent.mouseDown(combo("1", "top8_color"));
    fireEvent.click(screen.getByRole("option", {name: "2"}));
    await settle();
    fireEvent.click(clearButton("1", "top8_character"));
    await settle();

    // With the game whose list they were picked on.
    expect(writes("PATCH", "/api/v1/top8/players/2"))
        .toEqual([{character: {en_name: "Peach", skin: 0, game: "tgame"}}]);
    expect(writes("PATCH", "/api/v1/top8/players/1")).toEqual([
        {character: {en_name: "Bowser", skin: 1, game: "tgame"}},
        {character: null},
    ]);
});

test("a character without colours is sent on its first", async () => {
    await renderPage();

    await pickOption(combo("4", "top8_character"), "NoArt", "NoArt");

    expect(writes("PATCH", "/api/v1/top8/players/4"))
        .toEqual([{character: {en_name: "NoArt", skin: 0, game: "tgame"}}]);
});

test("a pick for a game no longer loaded is refused, and the list read again", async () => {
    await renderPage();
    refuse["/top8/players/1"] = [409, "CONFLICT"];

    await pickOption(combo("1", "top8_character"), "Peach", "Peach");

    expect(writes("PATCH", "/api/v1/top8/players/1"))
        .toEqual([{character: {en_name: "Peach", skin: 0, game: "tgame"}}]);
    expect(await screen.findByText(i18n.t("top8_pick_other_game")))
        .toBeInTheDocument();
    expect(reads("/api/v1/characters")).toBe(2);
    // Nothing was written: the card shows the state again.
    expect(combo("1", "top8_character")).toHaveValue("");
});

test("no character is picked on the previous game's list", async () => {
    await renderPage();
    let release;
    charactersGate = new Promise((resolve) => {
        release = () => resolve(answer(200, {ok: true, data: CHARACTERS}));
    });

    setState({}, {codename: "other"});
    await settle();

    // Read again for the new game; until it answers, nothing to pick on.
    expect(reads("/api/v1/characters")).toBe(2);
    expect(combo("1", "top8_character")).toBeDisabled();
    expect(combo("1", "top8_color")).toHaveAttribute("aria-disabled", "true");

    release();
    await settle();
    expect(combo("1", "top8_character")).toBeEnabled();
});

test("a character list that cannot be read for the new game says so", async () => {
    await renderPage();
    failing["/api/v1/characters"] = [400, "INVALID_ARGUMENT"];

    setState({}, {codename: "other"});
    await settle();

    expect(await screen.findByText(i18n.t("top8_characters_failed")))
        .toBeInTheDocument();
    expect(screen.getByText(i18n.t("top8_characters_unavailable")))
        .toBeInTheDocument();
    // The rest of the page stays.
    expect(combo("1", "top8_character")).toBeDisabled();
    expect(field("1", "top8_name")).toBeEnabled();

    delete failing["/api/v1/characters"];
    fireEvent.click(button("top8_characters_reload"));
    await settle();

    expect(reads("/api/v1/characters")).toBe(3);
    expect(combo("1", "top8_character")).toBeEnabled();
    expect(screen.queryByText(i18n.t("top8_characters_unavailable")))
        .toBeNull();
});

test("the character list is read again when the page is shown again", async () => {
    await renderPage();

    Object.defineProperty(document, "visibilityState",
                          {value: "visible", configurable: true});
    act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle();

    expect(reads("/api/v1/characters")).toBe(2);
    // Kept meanwhile: the selects stay usable.
    expect(combo("1", "top8_character")).toBeEnabled();
});

test("a refused pick says so, the token once", async () => {
    await renderPage();

    refuse["/top8/players/1"] = [400, "INVALID_ARGUMENT"];
    await pickOption(combo("1", "top8_country"), "japan", "日本 / Japan (JP)");
    expect(await screen.findByText(i18n.t("top8_pick_refused")))
        .toBeInTheDocument();

    refuse["/top8/players/1"] = [409, "NOT_READY"];
    await pickOption(combo("1", "top8_country"), "japan", "日本 / Japan (JP)");
    expect(await screen.findByText(i18n.t("top8_countries_not_ready")))
        .toBeInTheDocument();

    refuse["/top8/players/2"] = [401, "UNAUTHORIZED"];
    await type(field("2", "top8_name"), "Dan");
    expect(await screen.findByText(i18n.t("stream_token_required")))
        .toBeInTheDocument();
});

// ── the caption and the scores ────────────────────────────────────────────

test("an emptied caption stays empty over the default it stands for", async () => {
    setState({phaseLabel: "TOP 8 FINALS"});
    await renderPage();
    const caption = screen.getByRole("textbox",
                                     {name: i18n.t("top8_phase_label")});

    await type(caption, "");
    expect(writes("PATCH", "/api/v1/top8")).toEqual([{phaseLabel: ""}]);
    // Core writes the default for it: the field stays empty, the default
    // its placeholder (the Qt panel's).
    setState({phaseLabel: "TOP 8"});
    fireEvent.blur(caption);
    await settle();
    expect(caption).toHaveValue("");
    expect(caption).toHaveAttribute("placeholder", "TOP 8");

    // Another writer's caption shows, and so does the default after it.
    setState({phaseLabel: "SEMIS"});
    await settle();
    expect(caption).toHaveValue("SEMIS");
    setState({phaseLabel: "TOP 8"});
    await settle();
    expect(caption).toHaveValue("TOP 8");

    // Emptied over the default: nothing changes in the state, the field
    // stays empty.
    await type(caption, "");
    fireEvent.blur(caption);
    await settle(WRITE_TIMEOUT_MS);
    expect(caption).toHaveValue("");
    expect(writes("PATCH", "/api/v1/top8"))
        .toEqual([{phaseLabel: ""}, {phaseLabel: ""}]);
});

test("the caption shows the state's and sends what is typed", async () => {
    setState({phaseLabel: "TOP 8 FINALS"});
    await renderPage();

    const caption = screen.getByRole("textbox",
                                     {name: i18n.t("top8_phase_label")});
    expect(caption).toHaveValue("TOP 8 FINALS");
    expect(caption).toHaveAttribute("placeholder", "TOP 8");
    await type(caption, "GRAND FINALS");

    expect(writes("PATCH", "/api/v1/top8")).toEqual([{phaseLabel: "GRAND FINALS"}]);
});

test("the score rows show the state's players and scores", async () => {
    setState({
        players: players({"1": {name: "Alice"}, "2": {name: "Bob"}}),
        matches: matches({wsf1: {score: [2, 1], finished: true},
                          wf: {slots: [1, 0]}}),
    });
    await renderPage();

    const wsf1 = screen.getByRole("group", {name: "WSF1"});
    expect(within(wsf1).getByText("Alice")).toBeInTheDocument();
    expect(within(wsf1).getByText("Bob")).toBeInTheDocument();
    expect(scoreBox("WSF1", 1)).toHaveValue("2");
    expect(scoreBox("WSF1", 2)).toHaveValue("1");
    // No name: the slot; nobody yet: TBD.
    expect(within(screen.getByRole("group", {name: "WSF2"})).getByText("(3)"))
        .toBeInTheDocument();
    const wf = screen.getByRole("group", {name: "WF"});
    expect(within(wf).getByText("Alice")).toBeInTheDocument();
    expect(within(wf).getByText("TBD")).toBeInTheDocument();
    // The long name on the short label.
    expect(within(wf).getByText("WF")).toHaveAttribute("title", "Winners Final");
    ["Winners", "Losers", "Grand Final"].forEach((section) =>
        expect(screen.getByRole("heading", {name: section})).toBeInTheDocument());
});

test("scores are staged, and 反映 sends the matches typed into", async () => {
    await renderPage();
    expect(button("top8_apply")).toBeDisabled();

    fireEvent.change(scoreBox("WSF1", 1), {target: {value: "2"}});
    fireEvent.change(scoreBox("WSF1", 2), {target: {value: "1x"}});
    fireEvent.change(scoreBox("LR1A", 2), {target: {value: "123"}});
    await settle(WRITE_TIMEOUT_MS);

    // Nothing sent while staged; only digits, at most two.
    expect(sent()).toEqual([]);
    expect(scoreBox("WSF1", 2)).toHaveValue("1");
    expect(scoreBox("LR1A", 2)).toHaveValue("12");
    expect(button("top8_apply")).toBeEnabled();

    fireEvent.click(button("top8_apply"));
    await settle();

    expect(writes("POST", "/api/v1/top8/scores"))
        .toEqual([{wsf1: [2, 1], lr1a: [0, 12]}]);
    // Answered: the table shows the state again (here unchanged).
    expect(scoreBox("WSF1", 1)).toHaveValue("0");
    expect(button("top8_apply")).toBeDisabled();
});

test("a change of the matches drops what is staged, a player's does not", async () => {
    await renderPage();

    fireEvent.change(scoreBox("WSF2", 1), {target: {value: "3"}});
    setState({players: players({"3": {name: "Carol"}})});
    await settle();
    expect(scoreBox("WSF2", 1)).toHaveValue("3");

    setState({players: players({"3": {name: "Carol"}}),
              matches: matches({wsf1: {score: [2, 0], finished: true}})});
    await settle();
    expect(scoreBox("WSF2", 1)).toHaveValue("0");
    expect(scoreBox("WSF1", 1)).toHaveValue("2");
    expect(button("top8_apply")).toBeDisabled();
});

test("a refused 反映 keeps what is staged", async () => {
    await renderPage();
    refuse["/top8/scores"] = [400, "INVALID_ARGUMENT"];

    fireEvent.change(scoreBox("WSF1", 1), {target: {value: "2"}});
    fireEvent.click(button("top8_apply"));
    await settle();

    expect(await screen.findByText(i18n.t("request_failed"))).toBeInTheDocument();
    expect(scoreBox("WSF1", 1)).toHaveValue("2");
});

test("the reset game takes scores only once it is on", async () => {
    await renderPage();
    expect(scoreBox("RESET", 1)).toBeDisabled();
    expect(scoreBox("GF", 1)).toBeEnabled();

    setState({matches: matches({gf_reset: {active: true}})});
    await settle();
    expect(scoreBox("RESET", 1)).toBeEnabled();
});

test("全リセット asks first, and sends nothing when cancelled", async () => {
    await renderPage();

    fireEvent.click(button("top8_reset"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(i18n.t("top8_reset_confirm"));
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("stream_cancel")}));
    // The dialog's exit transition: the page is the accessibility tree's
    // again once it is gone.
    await settle(1000);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(writes("DELETE", "/api/v1/top8/scores")).toEqual([]);

    fireEvent.change(scoreBox("WSF1", 1), {target: {value: "2"}});
    fireEvent.click(button("top8_reset"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole(
        "button", {name: i18n.t("top8_reset_yes")}));
    await settle(1000);

    expect(writes("DELETE", "/api/v1/top8/scores")).toHaveLength(1);
    // Answered: what was staged went with it.
    expect(scoreBox("WSF1", 1)).toHaveValue("0");
});

test("a typed score reads as its number, 0 for none", () => {
    expect(scoreValue("")).toBe(0);
    expect(scoreValue("07")).toBe(7);
    expect(scoreValue("99")).toBe(99);
});

// ── the import ────────────────────────────────────────────────────────────

test("大会結果を読み込む starts the import", async () => {
    await renderPage();

    fireEvent.click(button("top8_import"));
    await settle();

    expect(writes("POST", "/api/v1/bracket:refresh")).toEqual([{}]);
    expect(await screen.findByText(i18n.t("top8_import_started")))
        .toBeInTheDocument();
    expect(button("top8_import")).toBeEnabled();
});

test.each([
    ["NOT_READY", "top8_import_no_tournament"],
    ["CONFLICT", "top8_import_running"],
])("a refused import (%s) says why", async (code, key) => {
    await renderPage();
    refuse["/bracket:refresh"] = [409, code];

    fireEvent.click(button("top8_import"));
    await settle();

    expect(await screen.findByText(i18n.t(key))).toBeInTheDocument();
});

// ── the colours ───────────────────────────────────────────────────────────

test("the colours are the feature's, matched to the tournament's", async () => {
    await renderPage();
    const colors = screen.getByRole("region", {name: i18n.t("top8_colors")});

    expect(within(colors).getByLabelText(i18n.t("commentary_color_theme")))
        .toHaveValue("#112233");
    fireEvent.click(within(colors).getByRole("button", {
        name: i18n.t("commentary_color_match_tournament")}));
    await settle();

    expect(writes("PATCH", "/api/v1/colors/top8"))
        .toEqual([{theme: "#181B21", accent: "#f0a500"}]);
});
