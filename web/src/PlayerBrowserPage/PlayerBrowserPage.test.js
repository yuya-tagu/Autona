import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {
    autonaCharactersSlice,
    autonaPlayersSlice,
    autonaStateSlice,
} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import PlayerBrowserPage from "./index";

// The Player List in React (ADR-005 S4n): the entrants and the registered
// players from GET /api/v1/players[/entrants], read again when the DB, the
// tournament or the game changes; an editor that saves through PUT
// /api/v1/players what was edited and nothing else; the delete's
// confirmation; "Set as P1 / P2" (:load).

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const BLANK = {
    prefix: "", name: "", twitter: "", country_code: "", state_code: "",
    custom_textbox: "", mains: [],
};
const ALPHA = {
    key: "SP Alpha", prefix: "SP", gamerTag: "Alpha", name: "Realname One",
    twitter: "@alpha", country_code: "JP", state_code: "13",
    custom_textbox: "line 1\nline 2",
    mains: [["Mario", 1, "EX"], ["Gone", 5, ""]],
};
const BRAVO = {...BLANK, key: "Bravo", gamerTag: "Bravo", twitter: "@zed"};
const CHARLIE = {...BLANK, key: "AA Charlie", prefix: "AA", gamerTag: "Charlie",
                 country_code: "US", twitter: "@bee"};

const SKIN = {assets: {"base_files/icon": {asset: "./assets/icon.png"}}};
const CHARACTERS = {
    "Mario / Mario": {en_name: "Mario", display_name: "Mario", name: "Mario",
                      codename: "mario", skins: [SKIN, SKIN]},
    "Luigi / Luigi": {en_name: "Luigi", display_name: "Luigi", name: "Luigi",
                      codename: "luigi", skins: [SKIN]},
    "Peach / Peach": {en_name: "Peach", display_name: "Peach", name: "Peach",
                      codename: "peach", skins: [SKIN, SKIN]},
};
const VARIANTS = {"EX Mode": {en_name: "EX", display_name: "EX Mode"}};
const COUNTRIES = [
    {code: "JP", name: "Japan", label: "Japan (JP)",
     asset: "./assets/country_flag/jp.png", custom: false},
    {code: "US", name: "United States", label: "United States (US)",
     asset: "./assets/country_flag/us.png", custom: false},
    {code: "My Flag", name: "My Flag", label: "My Flag",
     asset: "./user_data/additional_flag/My Flag.png", custom: true},
];

let players;
let entrants;
let game;
// (method, path) => a response instead of the normal one, or undefined.
let intercept;

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function refused(status, code) {
    return answer(status, {ok: false, error: {code, message: "no"}});
}

function sent(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && new URL(url).pathname === path)
        .map(([, init]) => (init.body ? JSON.parse(init.body) : null));
}

function reads(path) {
    return sent("GET", path).length;
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

// The queries the tests repeat stay away from getByRole: it reads the computed
// style of every element and its ancestors, which costs seconds per test on
// this page in jsdom (and slows every other suite of a full run down with it).
function shownPanel() {
    return within(document.querySelector("[role=tabpanel]:not([hidden])"));
}

function openTab(key) {
    fireEvent.click(screen.getByText(i18n.t(key)));
}

async function renderPage(tab = "players_tab_registered") {
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "playerBrowser", desktop: false, language: "en"}}>
                <PlayerBrowserPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await settle();
    openTab(tab);
    await settle();
}

function table(key = "players_tab_registered") {
    return document.querySelector(`table[aria-label="${i18n.t(key)}"]`);
}

// A column's cells, from the top.
function tagsIn(tableElement, column) {
    return Array.from(tableElement.querySelectorAll("tbody tr"))
        .map((row) => row.cells[column].textContent);
}

function rowOf(tag, key = "players_tab_registered") {
    const rows = Array.from(table(key).querySelectorAll("tbody tr"))
        .filter((row) => row.textContent.includes(tag));
    expect(rows).toHaveLength(1);
    return rows[0];
}

// The one shown with that text or that aria-label: the two tables' panels
// are both mounted, and each has its own.
function button(key) {
    const name = i18n.t(key);
    const found = Array.from(document.querySelectorAll("button"))
        .filter((element) => !element.closest("[hidden]")
            && (element.textContent === name
                || element.getAttribute("aria-label") === name));
    expect(found).toHaveLength(1);
    return found[0];
}

// The topmost: a confirmation opens over the editor.
function dialog() {
    const dialogs = document.querySelectorAll("[role=dialog]");
    return dialogs.length ? dialogs[dialogs.length - 1] : null;
}

function characterRow(row) {
    return document.querySelector(
        `[role=group][aria-label="${i18n.t("players_character_row", {n: row})}"]`);
}

function characterField(row) {
    return characterRow(row).querySelector("input[role=combobox]");
}

async function removeCharacter(row) {
    fireEvent.click(characterRow(row).querySelector(
        `button[aria-label="${i18n.t("players_character_remove")}"]`));
    await settle();
}

async function clickOption(pattern) {
    await settle();
    const options = Array.from(document.querySelectorAll("[role=option]"))
        .filter((option) => pattern.test(option.textContent));
    expect(options).toHaveLength(1);
    fireEvent.click(options[0]);
}

function search(text) {
    fireEvent.change(shownPanel().getByLabelText(i18n.t("players_search")),
                     {target: {value: text}});
}

function nextPage() {
    fireEvent.click(shownPanel().getByLabelText(i18n.t("players_page_next")));
}

function sortBy(key) {
    fireEvent.click(within(table()).getByText(i18n.t(key)));
}

async function openEditor(tag, key) {
    fireEvent.doubleClick(rowOf(tag, key));
    await settle();
}

function field(key) {
    return within(dialog()).getByLabelText(new RegExp(`^${i18n.t(key)}`));
}

function type(key, value) {
    fireEvent.change(field(key), {target: {value}});
}

async function pickCountry(text, option) {
    const input = within(dialog()).getByLabelText(i18n.t("country"));
    // Unfocused, the field puts back its value's text.
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value: text}});
    await clickOption(option);
    await settle();
}

async function saveWith(key) {
    fireEvent.click(button(key));
    await settle();
    await settle();
}

beforeEach(() => {
    jest.useFakeTimers();
    players = [ALPHA, BRAVO, CHARLIE];
    entrants = [
        {...ALPHA, seed: 1},
        {...BLANK, key: null, gamerTag: "Delta", seed: null},
        {...CHARLIE, seed: 999999},
        {...BRAVO},
    ];
    game = "tgame";
    intercept = () => undefined;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path} = new URL(url);
        const special = intercept(method, path);
        if (special !== undefined) {
            return special;
        }
        if (method === "GET" && path === "/api/v1/players") {
            return answer(200, {ok: true, data: {game, players}});
        }
        if (method === "GET" && path === "/api/v1/players/entrants") {
            return answer(200, {ok: true, data: {game, entrants}});
        }
        if (method === "GET" && path === "/api/v1/characters") {
            return answer(200, {ok: true, data: CHARACTERS});
        }
        if (method === "GET" && path === "/api/v1/variants") {
            return answer(200, {ok: true, data: VARIANTS});
        }
        if (method === "GET" && path === "/api/v1/countries") {
            return answer(200, {ok: true, data: COUNTRIES});
        }
        if (method === "GET" && path.endsWith("/states")) {
            return answer(200, {ok: true, data: {
                13: {name: "Tokyo", code: "13"}}});
        }
        return answer(200, {ok: true, data: {key: "x", created: true},
                            revision: 1});
    });
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {game: {codename: "tgame"},
                    tournamentInfo: {load: {revision: 1, status: "loaded"}}},
            revision: 1,
        }));
        autonaStore.dispatch(autonaCharactersSlice.actions.overwrite(CHARACTERS));
    });
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

describe("the entrants", () => {
    test("are in the server's order, a missing seed shown as -", async () => {
        await renderPage("players_tab_entrants");

        const entrantsTable = table("players_tab_entrants");
        expect(tagsIn(entrantsTable, 0)).toEqual(["1", "-", "-", "-"]);
        expect(tagsIn(entrantsTable, 1)).toEqual(["SP", "", "AA", ""]);
        expect(tagsIn(entrantsTable, 2))
            .toEqual(["Alpha", "Delta", "Charlie", "Bravo"]);
        expect(tagsIn(entrantsTable, 3)).toEqual(["JP", "", "US", ""]);
        expect(tagsIn(entrantsTable, 4)[0]).toBe("Mario, Gone");
    });

    test("say so when no tournament is loaded", async () => {
        entrants = [];
        await renderPage("players_tab_entrants");

        expect(screen.getByText(i18n.t("players_no_tournament")))
            .toBeInTheDocument();
        expect(table("players_tab_entrants")).toBeNull();
    });

    test.each([
        ["players_set_p1", 1],
        ["players_set_p2", 2],
    ])("%s loads the tag onto board 1", async (key, team) => {
        await renderPage("players_tab_entrants");
        expect(button(key)).toBeDisabled();

        fireEvent.click(rowOf("Alpha", "players_tab_entrants"));
        await settle();
        fireEvent.click(button(key));
        await settle();

        expect(sent("POST", `/api/v1/scoreboards/1/teams/${team}/players/1:load`))
            .toEqual([{tag: "Alpha"}]);
        expect(await screen.findByText(i18n.t("players_loaded",
                                              {tag: "Alpha", team})))
            .toBeInTheDocument();
    });

    test("a tag the DB does not have is told", async () => {
        intercept = (method, path) => (method === "POST"
            && path.endsWith(":load") ? refused(404, "NOT_FOUND") : undefined);
        await renderPage("players_tab_entrants");

        fireEvent.click(rowOf("Delta", "players_tab_entrants"));
        await settle();
        fireEvent.click(button("players_set_p1"));
        await settle();

        expect(await screen.findByText(i18n.t("players_load_not_found")))
            .toBeInTheDocument();
    });

    test("an entrant the DB does not have is edited as its tag alone", async () => {
        await renderPage("players_tab_entrants");

        await openEditor("Delta", "players_tab_entrants");

        expect(field("players_gamertag")).toHaveValue("Delta");
        expect(field("players_sponsor")).toHaveValue("");
        expect(button("players_delete_entry")).toBeDisabled();
        await saveWith("players_save_new");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            prefix: "", gamerTag: "Delta", name: "", twitter: "",
            custom_textbox: "", country_code: "", state_code: "", mains: [],
            game: "tgame",
        }]);
    });
});

describe("the registered players", () => {
    test("do not show the real name, and the search finds it", async () => {
        await renderPage();

        expect(screen.queryByText("Realname One")).toBeNull();
        search("realname");
        await settle();

        expect(tagsIn(table(), 0)).toEqual(["Alpha"]);
        expect(screen.queryByText("Realname One")).toBeNull();
    });

    test("say so when the search finds none", async () => {
        await renderPage();

        search("nobody");
        await settle();
        expect(screen.getByText(i18n.t("players_no_match"))).toBeInTheDocument();
    });

    test("say so when none is registered", async () => {
        players = [];
        await renderPage();

        expect(screen.getByText(i18n.t("players_none_registered")))
            .toBeInTheDocument();
    });

    test("sort by the column clicked, then the other way", async () => {
        await renderPage();
        expect(tagsIn(table(), 0)).toEqual(["Alpha", "Bravo", "Charlie"]);

        sortBy("players_gamertag");
        await settle();
        expect(tagsIn(table(), 0)).toEqual(["Charlie", "Bravo", "Alpha"]);

        sortBy("players_twitter");
        await settle();
        expect(tagsIn(table(), 0)).toEqual(["Alpha", "Charlie", "Bravo"]);
    });

    test("delete only after the confirmation, which names the key", async () => {
        await renderPage();
        expect(button("players_delete")).toBeDisabled();

        fireEvent.click(rowOf("Alpha"));
        await settle();
        fireEvent.click(button("players_delete"));
        await settle();

        expect(dialog()).toHaveTextContent(
            i18n.t("players_delete_confirm", {key: "SP Alpha"}));
        fireEvent.click(button("stream_cancel"));
        await settle(1000);
        expect(sent("POST", "/api/v1/players:delete")).toEqual([]);

        const before = reads("/api/v1/players");
        fireEvent.click(button("players_delete"));
        await settle();
        fireEvent.click(button("players_delete_ok"));
        await settle();
        await settle();

        expect(sent("POST", "/api/v1/players:delete"))
            .toEqual([{key: "SP Alpha", confirm: true}]);
        expect(reads("/api/v1/players")).toBeGreaterThan(before);
    });

    test("a player already gone is told, and the list read again", async () => {
        intercept = (method, path) => (method === "POST"
            && path === "/api/v1/players:delete"
            ? refused(404, "NOT_FOUND") : undefined);
        await renderPage();

        fireEvent.click(rowOf("Bravo"));
        await settle();
        fireEvent.click(button("players_delete"));
        await settle();
        const before = reads("/api/v1/players");
        fireEvent.click(button("players_delete_ok"));
        await settle();
        await settle();

        expect(await screen.findByText(i18n.t("players_delete_gone")))
            .toBeInTheDocument();
        expect(reads("/api/v1/players")).toBeGreaterThan(before);
    });
});

describe("the pages", () => {
    function many(count) {
        return Array.from({length: count}, (_v, index) => {
            const tag = `P${String(index).padStart(3, "0")}`;
            return {...BLANK, key: tag, gamerTag: tag};
        });
    }

    test("hold 100 rows", async () => {
        players = many(250);
        await renderPage();

        expect(screen.getByText(i18n.t("players_page_rows",
                                       {from: 1, to: 100, count: 250})))
            .toBeInTheDocument();
        expect(tagsIn(table(), 0)).toHaveLength(100);

        nextPage();
        await settle();

        expect(screen.getByText(i18n.t("players_page_rows",
                                       {from: 101, to: 200, count: 250})))
            .toBeInTheDocument();
        expect(tagsIn(table(), 0)[0]).toBe("P100");
    });

    test("go back to the first on a search and on a sort", async () => {
        players = many(250);
        await renderPage();
        nextPage();
        await settle();

        search("P1");
        await settle();
        expect(tagsIn(table(), 0)[0]).toBe("P100");
        expect(screen.getByText(i18n.t("players_page_rows",
                                       {from: 1, to: 100, count: 100})))
            .toBeInTheDocument();

        search("");
        await settle();
        nextPage();
        await settle();
        sortBy("players_gamertag");
        await settle();
        expect(tagsIn(table(), 0)[0]).toBe("P249");
    });

    test("a row that leaves the table is no longer selected", async () => {
        await renderPage();
        fireEvent.click(rowOf("Alpha"));
        await settle();
        expect(button("players_edit")).toBeEnabled();

        search("bravo");
        await settle();
        expect(button("players_edit")).toBeDisabled();

        search("");
        await settle();
        expect(button("players_edit")).toBeDisabled();
    });

    test("a row deleted elsewhere is no longer selected", async () => {
        await renderPage();
        fireEvent.click(rowOf("Alpha"));
        await settle();

        players = [BRAVO, CHARLIE];
        act(() => {
            autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
        });
        await settle();
        await settle();

        expect(tagsIn(table(), 0)).toEqual(["Bravo", "Charlie"]);
        expect(button("players_delete")).toBeDisabled();
    });
});

describe("the rows are read again", () => {
    test("when the DB is pushed", async () => {
        await renderPage();
        const before = reads("/api/v1/players");

        players = [BRAVO];
        act(() => {
            autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
        });
        await settle();
        await settle();

        expect(reads("/api/v1/players")).toBe(before + 1);
        expect(tagsIn(table(), 0)).toEqual(["Bravo"]);
    });

    test("when the tournament changes, not on another state change", async () => {
        await renderPage();
        const before = reads("/api/v1/players/entrants");

        act(() => {
            autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                state: {game: {codename: "tgame"},
                        tournamentInfo: {load: {revision: 1, status: "loaded"},
                                         eventName: "Other"}},
                revision: 2,
            }));
        });
        await settle();
        expect(reads("/api/v1/players/entrants")).toBe(before);

        act(() => {
            autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                state: {game: {codename: "tgame"},
                        tournamentInfo: {load: {revision: 2, status: "loaded"}}},
                revision: 3,
            }));
        });
        await settle();
        await settle();
        expect(reads("/api/v1/players/entrants")).toBe(before + 1);
    });

    test("when the game changes", async () => {
        await renderPage();
        const before = reads("/api/v1/players");
        const beforeCharacters = reads("/api/v1/characters");

        act(() => {
            autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                state: {game: {codename: "game2"},
                        tournamentInfo: {load: {revision: 1, status: "loaded"}}},
                revision: 2,
            }));
        });
        await settle();
        await settle();

        expect(reads("/api/v1/players")).toBe(before + 1);
        expect(reads("/api/v1/characters")).toBe(beforeCharacters + 1);
    });

    test("and an older answer does not replace a newer one", async () => {
        await renderPage();
        let answerOld;
        const old = new Promise((resolve) => {
            answerOld = resolve;
        });
        let first = true;
        intercept = (method, path) => {
            if (method === "GET" && path === "/api/v1/players" && first) {
                first = false;
                return old;
            }
            return undefined;
        };
        act(() => {
            autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
        });
        await settle();

        players = [BRAVO];
        act(() => {
            autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
        });
        await settle();
        await settle();
        expect(tagsIn(table(), 0)).toEqual(["Bravo"]);

        await act(async () => {
            answerOld(answer(200, {ok: true, data: {game, players: [ALPHA]}}));
        });
        await settle();
        await settle();
        expect(tagsIn(table(), 0)).toEqual(["Bravo"]);
    });
});

describe("the editor", () => {
    const TEXTS = {prefix: "SP", gamerTag: "Alpha", name: "Realname One",
                   twitter: "@alpha", custom_textbox: "line 1\nline 2"};

    test("opens from the button as from the double click", async () => {
        await renderPage();
        expect(button("players_edit")).toBeDisabled();

        fireEvent.click(rowOf("Alpha"));
        await settle();
        fireEvent.click(button("players_edit"));
        await settle();

        expect(field("players_gamertag")).toHaveValue("Alpha");
        expect(field("players_real_name")).toHaveValue("Realname One");
    });

    test("saves the text fields alone when nothing else changed", async () => {
        await renderPage();
        await openEditor("Alpha");

        type("players_real_name", "Someone");
        await saveWith("players_save_update");

        expect(sent("PUT", "/api/v1/players"))
            .toEqual([{...TEXTS, name: "Someone"}]);
        expect(dialog()).toBeNull();
    });

    test("closes without saving", async () => {
        await renderPage();
        await openEditor("Alpha");

        type("players_real_name", "Someone");
        fireEvent.click(button("players_close"));
        await settle(1000);

        expect(sent("PUT", "/api/v1/players")).toEqual([]);
        expect(dialog()).toBeNull();
    });

    test("another tag says everything: nothing is taken over", async () => {
        const mains = [["Mario", 1, "EX"], ["Luigi", 0, ""]];
        players = [{...ALPHA, mains}];
        await renderPage();
        await openEditor("Alpha");

        type("players_gamertag", "Alpha2");
        expect(button("players_save_new")).toBeEnabled();
        expect(button("players_delete_entry")).toBeDisabled();
        await saveWith("players_save_new");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            ...TEXTS, gamerTag: "Alpha2", country_code: "JP", state_code: "13",
            mains, game: "tgame",
        }]);
    });

    // The server keeps what the record it saves to has, and looks up the
    // rest. A tag that names no record has nothing, so a row's mains the
    // editor only kept are looked up too (core/PlayerDirectory._MainsRecord).
    describe("another tag with characters the server would refuse", () => {
        test.each([
            ["off the game's list", ["Gone", 5, ""], "Gone", 5],
            ["on a skin out of range", ["Mario", 7, ""], "Mario", 7],
            ["without a skin it has", ["Mario", -1, ""], "Mario", -1],
            ["with a variant the game does not have", ["Mario", 1, "Nope"],
             "Mario", 1],
        ])("%s is not sent, and is named", async (_what, main, name, color) => {
            players = [{...ALPHA, mains: [["Luigi", 0, ""], main]}];
            await renderPage();
            await openEditor("Alpha");
            expect(button("players_save_update")).toBeEnabled();

            type("players_gamertag", "Alpha2");

            const shown = i18n.t("players_character_kept", {name, color});
            const names = main[2]
                ? `${shown} ${i18n.t("players_character_variant", {name: main[2]})}`
                : shown;
            expect(screen.getByText(i18n.t("players_save_characters_refused",
                                           {names})))
                .toBeInTheDocument();
            expect(button("players_save_new")).toBeDisabled();
            expect(sent("PUT", "/api/v1/players")).toEqual([]);
        });

        test("the rest is saved once it is removed", async () => {
            await renderPage();
            await openEditor("Alpha");
            type("players_gamertag", "Alpha2");

            await removeCharacter(2);
            await saveWith("players_save_new");

            expect(sent("PUT", "/api/v1/players")).toEqual([{
                ...TEXTS, gamerTag: "Alpha2", country_code: "JP",
                state_code: "13", mains: [["Mario", 1, "EX"]], game: "tgame",
            }]);
        });

        test("what the record it saves to has already goes through", async () => {
            players = [ALPHA, {...BRAVO, mains: [["Gone", 5, ""]]}];
            await renderPage();
            await openEditor("Alpha");
            await removeCharacter(1);
            type("players_sponsor", "");
            type("players_gamertag", "Bravo");

            await saveWith("players_save_update");

            expect(sent("PUT", "/api/v1/players")[0].mains)
                .toEqual([["Gone", 5, ""]]);
        });

        // Not a new tag at all: the row as it is goes through as what the
        // record has, and another colour of it is looked up.
        test("another colour of a row with a variant the game does not have",
             async () => {
                 const mains = [["Mario", 1, "Nope"]];
                 players = [{...ALPHA, mains}];
                 await renderPage();
                 await openEditor("Alpha");

                 fireEvent.mouseDown(
                     characterRow(1).querySelector("div[role=combobox]"));
                 await settle();
                 fireEvent.click(document.querySelector(
                     "[role=option][data-value='0']"));
                 await settle();

                 const shown = i18n.t("players_character_kept",
                                      {name: "Mario", color: 0});
                 const variant = i18n.t("players_character_variant",
                                        {name: "Nope"});
                 expect(screen.getByText(i18n.t(
                     "players_save_characters_refused",
                     {names: `${shown} ${variant}`}))).toBeInTheDocument();
                 expect(button("players_save_update")).toBeDisabled();

                 await removeCharacter(1);
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains).toEqual([]);
             });

        test("the same tag is not looked at: nothing is sent to be refused",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");
                 type("players_real_name", "Other");

                 await saveWith("players_save_update");

                 expect(sent("PUT", "/api/v1/players")).toEqual([{
                     ...TEXTS, name: "Other"}]);
             });
    });

    test("a changed country goes with its region cleared", async () => {
        await renderPage();
        await openEditor("Alpha");

        await pickCountry("United", /United States \(US\)/);
        await saveWith("players_save_update");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            ...TEXTS, country_code: "US", state_code: "",
        }]);
    });

    test("a custom flag is offered by its code", async () => {
        await renderPage();
        await openEditor("Bravo");

        await pickCountry("My F", /My Flag/);
        await saveWith("players_save_update");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            prefix: "", gamerTag: "Bravo", name: "", twitter: "@zed",
            custom_textbox: "", country_code: "My Flag",
        }]);
    });

    test("the characters it cannot show are kept by name and can be removed", async () => {
        await renderPage();
        await openEditor("Alpha");

        expect(characterRow(2)).toHaveTextContent(
            i18n.t("players_character_kept", {name: "Gone", color: 5}));

        await removeCharacter(2);
        await saveWith("players_save_update");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            ...TEXTS, mains: [["Mario", 1, "EX"]], game: "tgame",
        }]);
    });

    test("a character is added from the game's list", async () => {
        await renderPage();
        await openEditor("Bravo");

        fireEvent.change(characterField(1), {target: {value: "Lui"}});
        await clickOption(/Luigi/);
        await settle();
        await saveWith("players_save_update");

        expect(sent("PUT", "/api/v1/players")).toEqual([{
            prefix: "", gamerTag: "Bravo", name: "", twitter: "@zed",
            custom_textbox: "", mains: [["Luigi", 0, ""]], game: "tgame",
        }]);
    });

    test("no more than three characters are offered", async () => {
        players = [{...ALPHA, mains: [["Mario", 0, ""], ["Mario", 1, ""],
                                      ["Luigi", 0, ""], ["Mario", 1, ""]]}];
        await renderPage();
        await openEditor("Alpha");

        expect(dialog().querySelectorAll("input[role=combobox]"))
            .toHaveLength(3 + 2);
        expect(characterField(4)).toBeNull();
        expect(characterRow(4)).toHaveTextContent(
            i18n.t("players_character_kept", {name: "Mario", color: 1}));
    });

    describe("a name typed and left without picking from the list", () => {
        // Clicking a button takes the focus out of the field first.
        async function typeAndLeave(row, text) {
            const input = characterField(row);
            fireEvent.focus(input);
            fireEvent.change(input, {target: {value: text}});
            fireEvent.blur(input);
            await settle();
        }

        test("is saved when it names a character", async () => {
            await renderPage();
            await openEditor("Bravo");

            await typeAndLeave(1, "Luigi");
            await saveWith("players_save_update");

            expect(sent("PUT", "/api/v1/players")).toEqual([{
                prefix: "", gamerTag: "Bravo", name: "", twitter: "@zed",
                custom_textbox: "", mains: [["Luigi", 0, ""]], game: "tgame",
            }]);
        });

        test("holds the save back until it is corrected when it does not",
             async () => {
                 await renderPage();
                 await openEditor("Bravo");

                 await typeAndLeave(1, "Nobody");

                 expect(screen.getByText(i18n.t("players_character_unknown")))
                     .toBeInTheDocument();
                 expect(characterField(1)).toHaveValue("Nobody");
                 expect(characterField(1)).toHaveAttribute("aria-invalid", "true");
                 expect(button("players_save_update")).toBeDisabled();

                 await typeAndLeave(1, "Luigi");

                 expect(screen.queryByText(i18n.t("players_character_unknown")))
                     .toBeNull();
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains)
                     .toEqual([["Luigi", 0, ""]]);
             });

        test("emptied, it is nothing to hold the save back for", async () => {
            await renderPage();
            await openEditor("Bravo");
            await typeAndLeave(1, "Nobody");

            const input = characterField(1);
            fireEvent.focus(input);
            fireEvent.change(input, {target: {value: ""}});
            fireEvent.blur(input);
            await settle();

            expect(screen.queryByText(i18n.t("players_character_unknown")))
                .toBeNull();
            await saveWith("players_save_update");
            expect(sent("PUT", "/api/v1/players")).toEqual([{
                prefix: "", gamerTag: "Bravo", name: "", twitter: "@zed",
                custom_textbox: "",
            }]);
        });

        test("Enter tells the same", async () => {
            await renderPage();
            await openEditor("Bravo");

            const input = characterField(1);
            fireEvent.focus(input);
            fireEvent.change(input, {target: {value: "Nobody"}});
            fireEvent.keyDown(input, {key: "Enter"});
            await settle();

            expect(screen.getByText(i18n.t("players_character_unknown")))
                .toBeInTheDocument();
            expect(button("players_save_update")).toBeDisabled();
        });

        test("does not touch the colour and the variant when it is the row's own name",
             async () => {
                 players = [{...ALPHA, mains: [["Mario", 1, "EX"]]}];
                 await renderPage();
                 await openEditor("Alpha");

                 await typeAndLeave(1, "mario");

                 // And the field says it as the list does.
                 expect(characterField(1)).toHaveValue("Mario");
                 await saveWith("players_save_update");

                 expect(sent("PUT", "/api/v1/players")).toEqual([{...TEXTS}]);
             });

        test("nothing but spaces is an empty field, not the name it replaced",
             async () => {
                 players = [{...ALPHA, mains: [["Mario", 1, "EX"],
                                               ["Luigi", 0, ""]]}];
                 await renderPage();
                 await openEditor("Alpha");

                 await typeAndLeave(1, "   ");

                 expect(characterField(1)).toHaveValue("");
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains)
                     .toEqual([["Luigi", 0, ""]]);
             });

        test("the list sent again leaves the text, the error and the hold",
             async () => {
                 await renderPage();
                 await openEditor("Bravo");
                 await typeAndLeave(1, "Nobody");

                 act(() => {
                     autonaStore.dispatch(autonaCharactersSlice.actions.overwrite(
                         JSON.parse(JSON.stringify(CHARACTERS))));
                 });
                 await settle();

                 expect(characterField(1)).toHaveValue("Nobody");
                 expect(characterField(1)).toHaveAttribute("aria-invalid", "true");
                 expect(button("players_save_update")).toBeDisabled();
             });

        test("the list sent again leaves a name being typed over a character",
             async () => {
                 players = [{...ALPHA, mains: [["Mario", 1, "EX"]]}];
                 await renderPage();
                 await openEditor("Alpha");
                 fireEvent.change(characterField(1), {target: {value: "Pea"}});

                 act(() => {
                     autonaStore.dispatch(autonaCharactersSlice.actions.overwrite(
                         JSON.parse(JSON.stringify(CHARACTERS))));
                 });
                 await settle();

                 expect(characterField(1)).toHaveValue("Pea");
             });

        test("a field only passed through changes nothing", async () => {
            players = [{...ALPHA, mains: [["Mario", 1, "EX"]]}];
            await renderPage();
            await openEditor("Alpha");

            fireEvent.focus(characterField(1));
            fireEvent.blur(characterField(1));
            await settle();
            await saveWith("players_save_update");

            expect(sent("PUT", "/api/v1/players")).toEqual([{...TEXTS}]);
        });

        test("a character picked past a blank field stays in that field",
             async () => {
                 await renderPage();
                 await openEditor("Bravo");
                 const input = characterField(3);

                 await typeAndLeave(3, "Luigi");

                 // The same input: the focus has nowhere to jump from.
                 expect(characterField(3)).toBe(input);
                 expect(characterField(1)).toHaveValue("");
                 expect(characterField(2)).toHaveValue("");
                 expect(characterField(3)).toHaveValue("Luigi");
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains)
                     .toEqual([["Luigi", 0, ""]]);
             });
    });

    describe("a character's field emptied to type another", () => {
        function empty(row) {
            fireEvent.focus(characterField(row));
            fireEvent.change(characterField(row), {target: {value: ""}});
        }

        beforeEach(() => {
            players = [{...ALPHA, mains: [["Mario", 1, "EX"], ["Luigi", 0, ""]]}];
        });

        test("stays that field: the row under it does not move into it",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");
                 const input = characterField(1);

                 empty(1);
                 await settle();

                 expect(characterField(1)).toBe(input);
                 expect(characterField(1)).toHaveValue("");
                 expect(characterField(2)).toHaveValue("Luigi");

                 fireEvent.change(input, {target: {value: "Peach"}});
                 await settle();
                 expect(characterField(1)).toHaveValue("Peach");
             });

        test("takes another character on its first colour, with no variant",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");

                 empty(1);
                 fireEvent.change(characterField(1), {target: {value: "Peach"}});
                 fireEvent.blur(characterField(1));
                 await settle();

                 expect(characterField(1)).toHaveValue("Peach");
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains)
                     .toEqual([["Peach", 0, ""], ["Luigi", 0, ""]]);
             });

        test("gets the colour and the variant back with the same character",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");

                 empty(1);
                 fireEvent.change(characterField(1), {target: {value: "Mario"}});
                 fireEvent.blur(characterField(1));
                 await settle();
                 await saveWith("players_save_update");

                 expect(sent("PUT", "/api/v1/players")).toEqual([{...TEXTS}]);
             });

        test("picked from the list, the same character is that one too",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");

                 empty(1);
                 fireEvent.change(characterField(1), {target: {value: "Mar"}});
                 await clickOption(/Mario/);
                 await settle();
                 await saveWith("players_save_update");

                 expect(sent("PUT", "/api/v1/players")).toEqual([{...TEXTS}]);
             });

        test("left empty, the row is not saved and the others are in order",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");

                 empty(1);
                 fireEvent.blur(characterField(1));
                 await settle();

                 expect(characterField(1)).toHaveValue("");
                 expect(characterField(2)).toHaveValue("Luigi");
                 await saveWith("players_save_update");
                 expect(sent("PUT", "/api/v1/players")[0].mains)
                     .toEqual([["Luigi", 0, ""]]);
             });

        test("× takes the row out, and what is typed under it moves up with it",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");
                 fireEvent.change(characterField(2), {target: {value: "Pea"}});

                 await removeCharacter(1);

                 expect(characterField(1)).toHaveValue("Pea");
                 expect(characterField(2)).toHaveValue("");
                 expect(characterField(3)).toHaveValue("");
             });
    });

    test("Esc does not lose what was typed", async () => {
        await renderPage();
        await openEditor("Alpha");
        type("players_real_name", "Typed");

        fireEvent.keyDown(dialog(), {key: "Escape"});
        await settle();

        expect(field("players_real_name")).toHaveValue("Typed");
    });

    describe("when the game changes while it is open", () => {
        async function changeGame(to = "game2") {
            game = to;
            act(() => {
                autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                    state: {game: {codename: to},
                            tournamentInfo: {load: {revision: 1, status: "loaded"}}},
                    revision: 2,
                }));
            });
            await settle();
            await settle();
        }

        test("the characters are not sent to the other game", async () => {
            await renderPage();
            await openEditor("Alpha");
            type("players_gamertag", "Alpha2");

            await changeGame();

            expect(screen.getByText(i18n.t("players_game_changed")))
                .toBeInTheDocument();
            expect(button("players_save_new")).toBeDisabled();
            expect(sent("PUT", "/api/v1/players")).toEqual([]);
        });

        test("what does not send them is still saved", async () => {
            await renderPage();
            await openEditor("Alpha");
            type("players_real_name", "Other");

            await changeGame();
            await saveWith("players_save_update");

            expect(sent("PUT", "/api/v1/players")).toEqual([{
                ...TEXTS, name: "Other"}]);
        });

        test("coming back, no name is left held against what is not shown",
             async () => {
                 await renderPage();
                 await openEditor("Bravo");
                 const input = characterField(1);
                 fireEvent.focus(input);
                 fireEvent.change(input, {target: {value: "Nobody"}});
                 fireEvent.blur(input);
                 await settle();
                 expect(button("players_save_update")).toBeDisabled();

                 await changeGame();
                 expect(characterRow(1)).toBeNull();

                 await changeGame("tgame");

                 expect(characterField(1)).toHaveValue("");
                 expect(characterField(1)).toHaveAttribute("aria-invalid", "false");
                 expect(screen.queryByText(i18n.t("players_character_unknown")))
                     .toBeNull();
                 expect(button("players_save_update")).toBeEnabled();
             });

        test("a save it lost to shows the same, not the same failure again",
             async () => {
                 await renderPage();
                 await openEditor("Alpha");
                 type("players_gamertag", "Alpha2");
                 await removeCharacter(2);
                 // The server knows before the page does.
                 game = "game2";
                 intercept = (method, path) => (
                     method === "PUT" && path === "/api/v1/players"
                         ? refused(409, "CONFLICT") : undefined);

                 await saveWith("players_save_new");
                 await settle();

                 expect(screen.getByText(i18n.t("players_game_changed")))
                     .toBeInTheDocument();
                 expect(button("players_save_new")).toBeDisabled();
                 expect(sent("PUT", "/api/v1/players")).toHaveLength(1);
             });
    });

    test("with no game selected it offers no characters", async () => {
        game = null;
        await renderPage();
        await openEditor("Bravo");

        expect(screen.getByText(i18n.t("players_no_game"))).toBeInTheDocument();
        expect(characterRow(1)).toBeNull();
    });

    test("a new player needs a tag, and a known key updates", async () => {
        await renderPage();

        fireEvent.click(button("players_add"));
        await settle();
        expect(button("players_save_new")).toBeDisabled();
        expect(button("players_delete_entry")).toBeDisabled();

        type("players_gamertag", "   ");
        expect(button("players_save_new")).toBeDisabled();

        type("players_gamertag", "Bravo");
        expect(button("players_save_update")).toBeEnabled();
        expect(button("players_delete_entry")).toBeEnabled();

        type("players_sponsor", "SP");
        expect(button("players_save_new")).toBeEnabled();
        expect(button("players_delete_entry")).toBeDisabled();
    });

    test("deletes the key typed, after the confirmation", async () => {
        await renderPage();
        await openEditor("Alpha");

        fireEvent.click(button("players_delete_entry"));
        await settle();
        expect(dialog()).toHaveTextContent("SP Alpha");
        fireEvent.click(button("players_delete_ok"));
        await settle();
        await settle();

        expect(sent("POST", "/api/v1/players:delete"))
            .toEqual([{key: "SP Alpha", confirm: true}]);
        await settle(1000);
        expect(dialog()).toBeNull();
    });

    describe("a refused save stays open and says why", () => {
        function refuseSave(status, code) {
            intercept = (method, path) => (
                method === "PUT" && path === "/api/v1/players"
                    ? refused(status, code) : undefined);
        }

        test.each([
            [400, "INVALID_ARGUMENT", "players_save_refused"],
            [409, "CONFLICT", "players_save_conflict"],
            [409, "NOT_READY", "players_countries_not_ready"],
        ])("%i %s", async (status, code, message) => {
            await renderPage();
            await openEditor("Alpha");
            refuseSave(status, code);

            await saveWith("players_save_update");

            expect(await screen.findByText(i18n.t(message))).toBeInTheDocument();
            expect(field("players_gamertag")).toHaveValue("Alpha");
        });

        test("a conflict reads the list again", async () => {
            await renderPage();
            await openEditor("Alpha");
            refuseSave(409, "CONFLICT");
            const before = reads("/api/v1/players");

            await saveWith("players_save_update");

            expect(reads("/api/v1/players")).toBe(before + 1);
        });

        test.each([401, 403])("a %i is told as the access token, once", async (status) => {
            await renderPage();
            await openEditor("Alpha");
            refuseSave(status, "X");

            await saveWith("players_save_update");
            expect(await screen.findByText(i18n.t("stream_token_required")))
                .toBeInTheDocument();

            // The click closes the snackbar, and this refusal does not bring
            // it back.
            await saveWith("players_save_update");
            expect(screen.queryByText(i18n.t("stream_token_required")))
                .toBeNull();
            expect(screen.queryByText(i18n.t("request_failed"))).toBeNull();
        });
    });
});

test("a refused read is told, with a way to read again", async () => {
    intercept = (method, path) => (method === "GET"
        && path === "/api/v1/players"
        ? refused(400, "INVALID_ARGUMENT") : undefined);
    await renderPage();

    expect(await screen.findByText(i18n.t("players_read_failed")))
        .toBeInTheDocument();

    intercept = () => undefined;
    fireEvent.click(button("players_reload"));
    await settle();
    await settle();

    expect(screen.queryByText(i18n.t("players_read_failed"))).toBeNull();
    expect(tagsIn(table(), 0)).toEqual(["Alpha", "Bravo", "Charlie"]);
});

describe("the entrants not read", () => {
    beforeEach(() => {
        intercept = (method, path) => (method === "GET"
            && path === "/api/v1/players/entrants"
            ? refused(400, "INVALID_ARGUMENT") : undefined);
    });

    test("leave the registered players as they are", async () => {
        await renderPage();

        expect(tagsIn(table(), 0)).toEqual(["Alpha", "Bravo", "Charlie"]);
        expect(button("players_add")).toBeEnabled();
        expect(screen.queryByText(i18n.t("players_read_failed"))).toBeNull();
    });

    test("are told on their tab, which says nothing of the tournament", async () => {
        await renderPage("players_tab_entrants");

        expect(screen.getByText(i18n.t("players_read_failed"))).toBeInTheDocument();
        expect(screen.queryByText(i18n.t("players_no_tournament"))).toBeNull();

        intercept = () => undefined;
        fireEvent.click(button("players_reload"));
        await settle();
        await settle();

        expect(screen.queryByText(i18n.t("players_read_failed"))).toBeNull();
        expect(tagsIn(table("players_tab_entrants"), 2)).toContain("Delta");
    });
});

test("the entrants on their way do not hold the registered players back", async () => {
    intercept = (method, path) => (method === "GET"
        && path === "/api/v1/players/entrants"
        ? new Promise(() => undefined) : undefined);
    await renderPage();

    expect(tagsIn(table(), 0)).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(button("players_add")).toBeEnabled();

    openTab("players_tab_entrants");
    await settle();

    expect(document.querySelector("[role=progressbar]")).not.toBeNull();
    expect(screen.queryByText(i18n.t("players_no_tournament"))).toBeNull();
});

test("the registered players not read leave the entrants, and what is edited is known", async () => {
    intercept = (method, path) => (method === "GET"
        && path === "/api/v1/players"
        ? refused(400, "INVALID_ARGUMENT") : undefined);
    await renderPage("players_tab_entrants");

    expect(tagsIn(table("players_tab_entrants"), 2)).toContain("Alpha");
    expect(screen.queryByText(i18n.t("players_read_failed"))).toBeNull();

    openTab("players_tab_registered");
    await settle();
    expect(screen.getByText(i18n.t("players_read_failed"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("players_none_registered"))).toBeNull();

    await openEditor("Alpha", "players_tab_entrants");

    // The record it opened on is still that one, not a new one.
    expect(button("players_save_update")).toBeEnabled();
});

test("a new player waits for the list: the game is not known until then", async () => {
    intercept = (method, path) => (method === "GET"
        && path === "/api/v1/players"
        ? refused(400, "INVALID_ARGUMENT") : undefined);
    await renderPage();
    expect(button("players_add")).toBeDisabled();

    intercept = () => undefined;
    fireEvent.click(button("players_reload"));
    await settle();
    await settle();

    expect(button("players_add")).toBeEnabled();
});

describe("the country rows", () => {
    function notReadyFor(answers) {
        let left = answers;
        intercept = (method, path) => {
            if (method === "GET" && path === "/api/v1/countries" && left > 0) {
                left -= 1;
                return refused(409, "NOT_READY");
            }
            return undefined;
        };
    }

    test("are asked for again while the server is still loading them", async () => {
        notReadyFor(2);
        await renderPage();
        expect(reads("/api/v1/countries")).toBe(1);

        await settle(1000);
        expect(reads("/api/v1/countries")).toBe(2);
        await settle(2000);
        await settle();
        expect(reads("/api/v1/countries")).toBe(3);

        // Read: asked for no more.
        await settle(10000);
        expect(reads("/api/v1/countries")).toBe(3);
    });

    test("stop being asked for after a while, until the next reread", async () => {
        notReadyFor(100);
        await renderPage();
        for (let attempt = 1; attempt < 10; attempt++) {
            await settle(attempt * 1000);
        }
        expect(reads("/api/v1/countries")).toBe(5);

        await openEditor("Alpha");
        await saveWith("players_save_update");
        await settle();

        expect(reads("/api/v1/countries")).toBe(6);
    });
});

test("a read refused for the token says so", async () => {
    intercept = (method, path) => (method === "GET"
        && path === "/api/v1/players" ? refused(401, "X") : undefined);
    // No click after it: one closes the snackbar.
    render(
        <Provider store={autonaStore}>
            <PlayerBrowserPage/>
        </Provider>,
    );
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("stream_token_required")))
        .toBeInTheDocument();
});

test("mounting sends nothing", async () => {
    await renderPage();

    expect(global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET")).toEqual([]);
});

test("the tab is kept while the page is away", async () => {
    const {unmount} = render(
        <Provider store={autonaStore}>
            <PlayerBrowserPage/>
        </Provider>,
    );
    await settle();
    await settle();
    openTab("players_tab_entrants");
    await settle();
    unmount();

    render(
        <Provider store={autonaStore}>
            <PlayerBrowserPage/>
        </Provider>,
    );
    await settle();
    await settle();

    expect(screen.getByRole("tab", {name: i18n.t("players_tab_entrants")}))
        .toHaveAttribute("aria-selected", "true");
});
