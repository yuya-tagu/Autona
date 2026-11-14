import React from "react";
import fs from "fs";
import path from "path";
import {act, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {Provider} from "react-redux";
import {BrowserRouter, Route, Routes} from "react-router-dom";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {
    SHELL_ANSWER_TIMEOUT_MS,
    installPanelShellApi,
    loadShellSession,
    receiveShellResult,
    receiveShellState,
} from "../PanelShell/hostApi";
import {WRITE_TIMEOUT_MS} from "../PanelShell/requests";
import AppShell from "./index";
import {MENU_PAGES, NAV_SECTIONS} from "./navTable";
import {BUSINESS_INQUIRY_EMAIL, BUSINESS_INQUIRY_URL, DISCORD_URL} from "./externalLinks";
import {FORMATS, gamesByName} from "./Header";

// The AppShell (ADR-005 S-final SF-5a-3): /web/app/:panel, the panel's page
// inside the Qt main window's frame -- the navigation, the header
// (tournament, game, modded content, battle format) and the footer (Help &
// Settings, what the app is busy with), all through /api/v1 and the shell
// requests.

jest.setTimeout(20000);

jest.mock("../ScoreboardPage/websocketInit", () => jest.fn());

// Plain functions, not jest.fn: restoreAllMocks would reset their bodies.
jest.mock("../websocketConnection", () => {
    const handlers = {};
    const socket = {
        connected: false,
        emit: () => {},
        on: (name, fn) => {
            (handlers[name] = handlers[name] || new Set()).add(fn);
        },
        off: (name, fn) => {
            handlers[name]?.delete(fn);
        },
        __handlers: handlers,
    };
    return {__esModule: true, default: {instance: () => socket}};
});

// Every panel a probe: the shell's own parts are what is tested here. No
// JSX in a jest.mock factory.
jest.mock("../PanelShell/registry", () => {
    const h = jest.requireActual("react").createElement;
    const actual = jest.requireActual("../PanelShell/registry");
    const {usePanelShell} = jest.requireActual("../PanelShell/context");
    const probe = (name) => function Probe() {
        const {panel, desktop} = usePanelShell();
        return h("p", null, `page ${name} (${panel}) desktop=${desktop}`);
    };
    const PANELS = Object.fromEntries(
        Object.keys(actual.PANELS).map((name) => [name, probe(name)]));
    return {
        PANELS,
        panelPage: (panel) => (
            Object.prototype.hasOwnProperty.call(PANELS, panel)
                ? PANELS[panel] : undefined),
    };
});

const socket = require("../websocketConnection").default.instance();

const GAMES = {
    sf6: {name: "Street Fighter 6"},
    ssbu: {name: "Super Smash Bros. Ultimate"},
    abc: {name: "arcana heart"},
    nameless: {},
};

let server;
let revision = 0;

function answer(status, body) {
    const text = JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => JSON.parse(text),
        text: async () => text,
    };
}

function ok(status, data) {
    return answer(status, {ok: true, data, revision: 1});
}

function refuse(status, code = "X") {
    return answer(status, {ok: false, error: {code, message: "no"}});
}

function freshServer() {
    return {
        session: refuse(403, "FORBIDDEN"),
        games: ok(200, GAMES),
        format: {format: "singles", board: 1},
        status: ok(200, {revision: 0, jobs: []}),
        tournament: () => ok(202, {accepted: true,
                                   operation: "tournament.load",
                                   loadRevision: 4}),
        game: () => ok(202, {accepted: true}),
        put: () => ok(200, {...server.format, changed: true}),
    };
}

function calls(method, pathname) {
    return global.fetch.mock.calls.filter(([url, init]) =>
        (init?.method ?? "GET") === method
        && new URL(url).pathname === pathname);
}

function bodies(method, pathname) {
    return calls(method, pathname).map(([, init]) => JSON.parse(init.body));
}

function setState(tournamentInfo = {}, game = {}) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                tournamentInfo: {
                    tournamentName: "",
                    url: "",
                    load: {status: "idle", error: "", revision: 0},
                    ...tournamentInfo,
                },
                game,
            },
            revision,
        }));
    });
}

async function settle() {
    await act(async () => {
        for (let i = 0; i < 12; i++) {
            await Promise.resolve();
        }
    });
}

function emit(event, payload) {
    act(() => {
        socket.__handlers[event]?.forEach((fn) => fn(payload));
    });
}

async function renderAt(url) {
    window.history.replaceState(null, "", url);
    installPanelShellApi();
    const view = render(
        <Provider store={autonaStore}>
            <BrowserRouter>
                <Routes>
                    <Route path="/web/app/:panel" element={<AppShell/>}/>
                </Routes>
            </BrowserRouter>
        </Provider>,
    );
    await settle();
    return view;
}

function navButton(key) {
    return within(screen.getByRole("navigation", {name: i18n.t("app_navigation")}))
        .getByRole("button", {name: i18n.t(key)});
}

async function openMenu(testId) {
    fireEvent.click(screen.getByTestId(testId));
    await settle();
    return screen.getByRole("menu");
}

beforeEach(() => {
    server = freshServer();
    AbortSignal.timeout = () => undefined;
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname} = new URL(url);
        if (method === "GET") {
            switch (pathname) {
                case "/api/v1/shell-session":
                    return server.session;
                case "/api/v1/games":
                    return server.games;
                case "/api/v1/battle-format":
                    return server.formatRead ? server.formatRead()
                        : ok(200, server.format);
                case "/api/v1/status":
                    return server.status;
                default:
                    return refuse(404, "NOT_FOUND");
            }
        }
        if (pathname === "/api/v1/tournament") {
            return method === "POST" ? server.tournament(JSON.parse(init.body))
                : ok(202, {accepted: true, operation: "tournament.clear",
                           loadRevision: 5});
        }
        if (method === "POST" && pathname === "/api/v1/game") {
            return server.game(JSON.parse(init.body));
        }
        if (method === "PUT" && pathname === "/api/v1/battle-format") {
            return server.put(JSON.parse(init.body));
        }
        return refuse(404, "NOT_FOUND");
    });
    setState();
});

afterEach(async () => {
    delete global.fetch;
    delete window.matchMedia;
    jest.useRealTimers();
    jest.restoreAllMocks();
    await act(async () => {
        await i18n.changeLanguage("en");
    });
});

// ── the frame and the navigation ────────────────────────────────────────

test("the page named in the URL shows inside the frame", async () => {
    await renderAt("/web/app/event?desktop=1&lng=en");

    expect(screen.getByText("page event (event) desktop=true")).toBeInTheDocument();
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("app-tournament")).toBeInTheDocument();
    expect(screen.getByTestId("app-game")).toBeInTheDocument();
    expect(screen.getByTestId("app-help")).toBeInTheDocument();
});

test("the navigation is the Qt sidebar's: its headings and items in order", async () => {
    await renderAt("/web/app/event?lng=en");
    const nav = screen.getByRole("navigation", {name: i18n.t("app_navigation")});
    const lists = within(nav).getAllByRole("list");

    expect(lists.map((list) => list.getAttribute("aria-labelledby")))
        .toEqual(NAV_SECTIONS.map((section) => `app-nav-${section.heading}`));
    lists.forEach((list, index) => {
        const section = NAV_SECTIONS[index];
        expect(document.getElementById(`app-nav-${section.heading}`))
            .toHaveTextContent(i18n.t(section.heading));
        expect(within(list).getAllByRole("button").map((item) => item.textContent))
            .toEqual(section.items.map((item) => i18n.t(item.label)));
    });
    expect(NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.panel)))
        .toEqual(["event", "stream", "scoreboard", "board", "thumbnail",
                  "result", "top8", "commentary", "statsPreview", "stats",
                  "playerBrowser"]);
});

test("the page shown is the highlighted item", async () => {
    await renderAt("/web/app/scoreboard?lng=en");

    expect(navButton("app_scoreboard")).toHaveAttribute("aria-current", "page");
    expect(navButton("app_tournament_info")).not.toHaveAttribute("aria-current");
});

test("an item moves to its page without a reload, keeping /web/app/ and the query", async () => {
    await renderAt("/web/app/event?desktop=1&lng=en");
    const entries = window.history.length;

    fireEvent.click(navButton("app_stream_settings"));
    await settle();

    expect(window.location.pathname).toBe("/web/app/stream");
    expect(window.location.search).toBe("?desktop=1&lng=en");
    expect(window.history.length).toBe(entries);
    expect(screen.getByText("page stream (stream) desktop=true")).toBeInTheDocument();
    expect(navButton("app_stream_settings")).toHaveAttribute("aria-current", "page");
});

test("the desktop's navigate keeps the AppShell", async () => {
    await renderAt("/web/app/event?desktop=1");

    act(() => {
        window.autonaPanelShell.navigate("top8");
    });
    await settle();

    expect(window.location.pathname).toBe("/web/app/top8");
    expect(screen.getByText("page top8 (top8) desktop=true")).toBeInTheDocument();
});

test("a menu page highlights no item", async () => {
    for (const panel of MENU_PAGES) {
        const {unmount} = await renderAt(`/web/app/${panel}?lng=en`);
        expect(screen.getByText(`page ${panel} (${panel}) desktop=false`))
            .toBeInTheDocument();
        const nav = screen.getByRole("navigation", {name: i18n.t("app_navigation")});
        within(nav).getAllByRole("button").forEach((item) =>
            expect(item).not.toHaveAttribute("aria-current"));
        unmount();
    }
});

test("the sidebar hides and comes back (not saved)", async () => {
    await renderAt("/web/app/event?lng=en");
    const stored = () => JSON.stringify([{...localStorage}, {...sessionStorage}]);
    const before = stored();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("app_hide_sidebar")}));
    await settle();
    expect(screen.queryByTestId("app-sidebar")).not.toBeInTheDocument();
    expect(screen.getByText("page event (event) desktop=false")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("app_show_sidebar")}));
    await settle();
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();
    expect(stored()).toBe(before);
});

test("the words follow lng=ja", async () => {
    await renderAt("/web/app/event?lng=ja");

    expect(navButton("app_tournament_info")).toHaveTextContent("大会情報設定");
    expect(screen.getByTestId("app-help")).toHaveTextContent("ヘルプ＆設定");
    expect(screen.getByTestId("app-tournament")).toHaveTextContent("大会を選ぶ");
});


// A window `width` px wide, as useMediaQuery reads it.
function viewWidth(width) {
    window.matchMedia = (query) => {
        const max = /max-width:\s*([\d.]+)px/.exec(query);
        const min = /min-width:\s*([\d.]+)px/.exec(query);
        return {
            matches: (!max || width <= Number(max[1]))
                && (!min || width >= Number(min[1])),
            media: query,
            onchange: null,
            addListener: () => {},
            removeListener: () => {},
            addEventListener: () => {},
            removeEventListener: () => {},
            dispatchEvent: () => false,
        };
    };
}

function statusRegions() {
    return screen.getAllByRole("status", {name: i18n.t("app_status")});
}

const LOADING = {revision: 1, jobs: [
    {id: 1, code: "assets_load", args: {codename: "ssbu"}, done: 1, total: 4}]};

test("hidden, the sidebar leaves the status at the bottom left: one live region", async () => {
    await renderAt("/web/app/event?lng=en");
    fireEvent.click(screen.getByRole("button", {name: i18n.t("app_hide_sidebar")}));
    await settle();
    server.status = ok(200, LOADING);
    emit("status", LOADING);
    await settle();

    let regions = statusRegions();
    expect(regions).toHaveLength(1);
    expect(regions[0]).toHaveAttribute("data-place", "corner");
    expect(regions[0]).toHaveTextContent(
        i18n.t("status_assets_load", {codename: "ssbu"}));
    expect(within(regions[0]).getByRole("progressbar"))
        .toHaveAttribute("aria-valuenow", "25");

    fireEvent.click(screen.getByRole("button", {name: i18n.t("app_show_sidebar")}));
    await settle();
    regions = statusRegions();
    expect(regions).toHaveLength(1);
    expect(regions[0]).toHaveAttribute("data-place", "footer");
    expect(regions[0]).toHaveTextContent(
        i18n.t("status_assets_load", {codename: "ssbu"}));
});

test("hiding and showing the sidebar puts the focus on the button that undoes it", async () => {
    await renderAt("/web/app/event?lng=en");
    const hide = screen.getByRole("button", {name: i18n.t("app_hide_sidebar")});
    act(() => hide.focus());
    fireEvent.click(hide);
    await settle();
    expect(screen.getByRole("button", {name: i18n.t("app_show_sidebar")})).toHaveFocus();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("app_show_sidebar")}));
    await settle();
    expect(screen.getByRole("button", {name: i18n.t("app_hide_sidebar")})).toHaveFocus();
});

test("below 600 px the sidebar is a drawer, opened from a row above the page", async () => {
    viewWidth(500);
    await renderAt("/web/app/event?lng=en");
    const content = screen.getByTestId("app-content");
    // In the row above the page, not over it.
    const opener = within(content).getByRole("button", {name: i18n.t("app_show_sidebar")});
    expect(screen.queryByRole("navigation", {name: i18n.t("app_navigation")}))
        .not.toBeInTheDocument();
    // The status: a line below the page, the one live region.
    server.status = ok(200, LOADING);
    emit("status", LOADING);
    await settle();
    const regions = statusRegions();
    expect(regions).toHaveLength(1);
    expect(regions[0]).toHaveAttribute("data-place", "bar");
    expect(regions[0]).toHaveTextContent(
        i18n.t("status_assets_load", {codename: "ssbu"}));

    fireEvent.click(opener);
    await settle();
    expect(screen.getByTestId("app-sidebar")).toBeVisible();
    fireEvent.click(navButton("app_stream_settings"));
    await settle();

    expect(window.location.pathname).toBe("/web/app/stream");
    await waitFor(() => expect(screen.queryByRole("navigation",
                                                  {name: i18n.t("app_navigation")}))
        .not.toBeInTheDocument());
    expect(screen.getByText("page stream (stream) desktop=false")).toBeInTheDocument();
});

// ── the tournament ──────────────────────────────────────────────────────

test("the tournament button: its name, or the call to set one outlined", async () => {
    await renderAt("/web/app/event?lng=en");
    const button = screen.getByTestId("app-tournament");
    expect(button).toHaveTextContent(i18n.t("app_set_tournament"));
    expect(button).toHaveAttribute("data-attention", "true");

    setState({tournamentName: "  Autona Cup  "});
    await settle();

    expect(button).toHaveTextContent("Autona Cup");
    expect(button).toHaveAttribute("data-attention", "false");
});

test("the tournament dialog sets the URL with force, and says a refusal", async () => {
    const url = "https://www.start.gg/tournament/cup/event/singles";
    setState({url: "https://www.start.gg/tournament/old/event/doubles"});
    server.tournament = (body) => (body.url.includes("start.gg")
        ? ok(202, {accepted: true, operation: "tournament.load", loadRevision: 4})
        : refuse(400, "INVALID_ARGUMENT"));
    await renderAt("/web/app/event?lng=en");

    const menu = await openMenu("app-tournament");
    fireEvent.click(within(menu).getByRole("menuitem",
                                          {name: i18n.t("app_set_tournament")}));
    await settle();
    const dialog = screen.getByRole("dialog", {name: i18n.t("app_set_tournament_url")});
    const field = within(dialog).getByRole("textbox", {name: i18n.t("event_url")});
    expect(field).toHaveValue("https://www.start.gg/tournament/old/event/doubles");

    fireEvent.change(field, {target: {value: "not a url"}});
    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("app_tournament_ok")}));
    await settle();
    expect(within(dialog).getByRole("status"))
        .toHaveTextContent(i18n.t("event_url_invalid"));

    fireEvent.change(field, {target: {value: `  ${url}  `}});
    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("app_tournament_ok")}));
    await settle();
    expect(bodies("POST", "/api/v1/tournament")).toEqual([
        {url: "not a url", force: true}, {url, force: true}]);
    // Busy until the state has it.
    expect(within(dialog).getByRole("button", {name: i18n.t("app_tournament_ok")}))
        .toBeDisabled();

    setState({url, load: {status: "loading", error: "", revision: 4}});
    await settle();
    expect(within(dialog).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_loading"));

    setState({url, tournamentName: "Cup",
              load: {status: "loaded", error: "", revision: 4}});
    await settle();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a failed load is said in the dialog, which stays", async () => {
    await renderAt("/web/app/event?lng=en");
    await openMenu("app-tournament");
    fireEvent.click(screen.getByRole("menuitem", {name: i18n.t("app_set_tournament")}));
    await settle();
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"),
                     {target: {value: "https://www.start.gg/tournament/x/event/y"}});
    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("app_tournament_ok")}));
    await settle();

    setState({load: {status: "error", error: "load_failed", revision: 4}});
    await settle();

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_load_failed"));
    // Typed again: what was said of the last URL goes.
    fireEvent.change(within(dialog).getByRole("textbox"),
                     {target: {value: "https://www.start.gg/tournament/x/event/z"}});
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("stream_cancel")}));
    await settle();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("unsetting the tournament asks first, then DELETE", async () => {
    setState({tournamentName: "Cup"});
    await renderAt("/web/app/event?lng=en");

    await openMenu("app-tournament");
    fireEvent.click(screen.getByRole("menuitem", {name: i18n.t("app_unset_tournament")}));
    await settle();
    const dialog = screen.getByRole("dialog", {name: i18n.t("app_confirm")});
    expect(dialog).toHaveTextContent(i18n.t("app_unset_confirm"));
    expect(calls("DELETE", "/api/v1/tournament")).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("event_clear_yes")}));
    await settle();
    expect(calls("DELETE", "/api/v1/tournament")).toHaveLength(1);
});

test("a refusal for want of the token is told once", async () => {
    server.game = () => refuse(401, "UNAUTHENTICATED");
    await renderAt("/web/app/event?lng=en");

    for (let i = 0; i < 2; i++) {
        await openMenu("app-game");
        fireEvent.click(screen.getByRole("menuitem", {name: "Street Fighter 6"}));
        await settle();
    }

    expect(calls("POST", "/api/v1/game")).toHaveLength(2);
    expect(screen.getAllByText(i18n.t("stream_token_required"))).toHaveLength(1);
});

// ── the game, its modded content, the battle format ─────────────────────

test("the games are by name, the state's game checked", async () => {
    setState({}, {codename: "ssbu"});
    await renderAt("/web/app/event?lng=en");
    const button = screen.getByTestId("app-game");
    expect(button).toHaveTextContent("Super Smash Bros. Ultimate");
    expect(button).toHaveAttribute("data-attention", "false");

    const menu = await openMenu("app-game");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual([
        "arcana heart", "nameless", "Street Fighter 6",
        "Super Smash Bros. Ultimate", i18n.t("app_add_game_assets")]);
    expect(items[3]).toHaveClass("Mui-selected");
    expect(gamesByName(GAMES)).toEqual(["abc", "nameless", "sf6", "ssbu"]);
});

test("picking a game loads it (mods off), the one shown too", async () => {
    setState({}, {codename: "ssbu"});
    await renderAt("/web/app/event?lng=en");

    await openMenu("app-game");
    fireEvent.click(screen.getByRole("menuitem", {name: "Street Fighter 6"}));
    await settle();
    await openMenu("app-game");
    fireEvent.click(screen.getByRole("menuitem", {name: "Super Smash Bros. Ultimate"}));
    await settle();

    expect(bodies("POST", "/api/v1/game")).toEqual([
        {codename: "sf6"}, {codename: "ssbu"}]);
});

test("a game load already running is said", async () => {
    server.game = () => refuse(409, "CONFLICT");
    await renderAt("/web/app/event?lng=en");

    await openMenu("app-game");
    fireEvent.click(screen.getByRole("menuitem", {name: "arcana heart"}));
    await settle();

    expect(screen.getByText(i18n.t("event_game_busy"))).toBeInTheDocument();
});

test("no game: the call to pick one outlined; the assets page from the menu", async () => {
    setState({}, {codename: "gone"});
    await renderAt("/web/app/event?lng=en");
    const button = screen.getByTestId("app-game");
    expect(button).toHaveTextContent(i18n.t("app_select_game"));
    expect(button).toHaveAttribute("data-attention", "true");

    await openMenu("app-game");
    fireEvent.click(screen.getByRole("menuitem", {name: i18n.t("app_add_game_assets")}));
    await settle();

    expect(window.location.pathname).toBe("/web/app/assets");
    expect(calls("POST", "/api/v1/game")).toHaveLength(0);
});

test("the games are read again on games_changed", async () => {
    await renderAt("/web/app/event?lng=en");
    const before = calls("GET", "/api/v1/games").length;

    server.games = ok(200, {...GAMES, zzz: {name: "Zeta"}});
    emit("games_changed", {});
    await settle();

    expect(calls("GET", "/api/v1/games").length).toBe(before + 1);
    await openMenu("app-game");
    expect(screen.getByRole("menuitem", {name: "Zeta"})).toBeInTheDocument();
});

test("the modded content switch only for a game that has some", async () => {
    setState({}, {codename: "ssbu", has_modded_content: false});
    await renderAt("/web/app/event?lng=en");
    expect(screen.queryByRole("switch", {name: i18n.t("app_modded_content")}))
        .not.toBeInTheDocument();

    setState({}, {codename: "ssbu", has_modded_content: true, mods_active: false});
    await settle();
    const toggle = screen.getByRole("switch", {name: i18n.t("app_modded_content")});
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    await settle();
    expect(bodies("POST", "/api/v1/game")).toEqual([
        {codename: "ssbu", modsActive: true}]);

    setState({}, {codename: "ssbu", has_modded_content: true, mods_active: true});
    await settle();
    expect(screen.getByRole("switch", {name: i18n.t("app_modded_content")}))
        .toBeChecked();
});

test("the battle format: shown as read, switched with PUT, read again on its notice", async () => {
    await renderAt("/web/app/event?lng=en");
    const group = screen.getByRole("group", {name: i18n.t("app_battle_format")});
    expect(within(group).getAllByRole("button").map((b) => b.textContent))
        .toEqual(FORMATS.map(([, label]) => label));
    expect(within(group).getByRole("button", {name: "1v1"}))
        .toHaveAttribute("aria-pressed", "true");

    server.format = {format: "team", board: 2};
    fireEvent.click(within(group).getByRole("button", {name: "Team"}));
    await settle();
    expect(bodies("PUT", "/api/v1/battle-format")).toEqual([{format: "team"}]);
    expect(within(group).getByRole("button", {name: "Team"}))
        .toHaveAttribute("aria-pressed", "true");

    // The format in force: nothing sent.
    fireEvent.click(within(group).getByRole("button", {name: "Team"}));
    await settle();
    expect(bodies("PUT", "/api/v1/battle-format")).toHaveLength(1);

    // Switched elsewhere (the Qt sidebar, another client).
    server.format = {format: "crew", board: 3};
    emit("battle_format", {});
    await settle();
    expect(within(group).getByRole("button", {name: "Crew"}))
        .toHaveAttribute("aria-pressed", "true");
});


function openTournamentDialog() {
    return openMenu("app-tournament").then(async (menu) => {
        fireEvent.click(within(menu).getByRole("menuitem",
                                              {name: i18n.t("app_set_tournament")}));
        await settle();
        return screen.getByRole("dialog", {name: i18n.t("app_set_tournament_url")});
    });
}

function okButton(dialog) {
    return within(dialog).getByRole("button", {name: i18n.t("app_tournament_ok")});
}

test("the tournament dialog: a load running, a 202 naming no revision, closed while sending", async () => {
    server.tournament = () => refuse(409, "CONFLICT");
    await renderAt("/web/app/event?lng=en");
    let dialog = await openTournamentDialog();
    fireEvent.change(within(dialog).getByRole("textbox"),
                     {target: {value: "https://www.start.gg/tournament/x/event/y"}});
    fireEvent.click(okButton(dialog));
    await settle();
    expect(within(dialog).getByRole("status")).toHaveTextContent(i18n.t("event_busy"));

    // Nothing to wait for: closed.
    server.tournament = () => ok(202, {accepted: true, operation: "tournament.load"});
    fireEvent.click(okButton(dialog));
    await settle();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Closed while its request is out: the answer does nothing.
    let release;
    server.tournament = () => new Promise((resolve) => {
        release = () => resolve(refuse(500, "INTERNAL"));
    });
    jest.spyOn(console, "error").mockImplementation(() => {});
    dialog = await openTournamentDialog();
    fireEvent.change(within(dialog).getByRole("textbox"),
                     {target: {value: "https://www.start.gg/tournament/x/event/y"}});
    fireEvent.click(okButton(dialog));
    await settle();
    fireEvent.click(within(dialog).getByRole("button", {name: i18n.t("stream_cancel")}));
    await settle();
    release();
    await settle();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

test("a load whose word never comes is said, and OK is offered again", async () => {
    await renderAt("/web/app/event?lng=en");
    const dialog = await openTournamentDialog();
    const field = within(dialog).getByRole("textbox");
    fireEvent.change(field, {target: {value: "https://www.start.gg/tournament/x/event/y"}});
    jest.useFakeTimers();
    fireEvent.click(okButton(dialog));
    await settle();
    expect(okButton(dialog)).toBeDisabled();
    act(() => {
        jest.advanceTimersByTime(WRITE_TIMEOUT_MS - 1);
    });
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    act(() => {
        jest.advanceTimersByTime(1);
    });
    await settle();
    jest.useRealTimers();

    expect(within(dialog).getByRole("status"))
        .toHaveTextContent(i18n.t("app_tournament_no_word"));
    expect(okButton(dialog)).toBeEnabled();
    // Typed again: what was said goes.
    fireEvent.change(field, {target: {value: "https://www.start.gg/tournament/x/event/z"}});
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
});

test("before the games are read nothing is called missing", async () => {
    let release;
    server.games = new Promise((resolve) => {
        release = resolve;
    });
    setState({}, {codename: "ssbu"});
    await renderAt("/web/app/event?lng=en");
    const button = screen.getByTestId("app-game");
    expect(button).toHaveTextContent("ssbu");
    expect(button).toHaveAttribute("data-attention", "false");

    setState({}, {});
    await settle();
    expect(button).toHaveTextContent(i18n.t("event_loading"));
    expect(button).toHaveAttribute("data-attention", "false");

    setState({}, {codename: "ssbu"});
    release(ok(200, GAMES));
    await settle();
    expect(button).toHaveTextContent("Super Smash Bros. Ultimate");
});

test("the games not readable: the state's game still named, none called missing", async () => {
    server.games = refuse(403, "FORBIDDEN");
    jest.spyOn(console, "error").mockImplementation(() => {});
    setState({}, {codename: "ssbu"});
    await renderAt("/web/app/event?lng=en");
    const button = screen.getByTestId("app-game");
    expect(button).toHaveTextContent("ssbu");
    expect(button).toHaveAttribute("data-attention", "false");

    // No game at all: that is known without the list.
    setState({}, {});
    await settle();
    expect(button).toHaveTextContent(i18n.t("app_select_game"));
    expect(button).toHaveAttribute("data-attention", "true");
});

test("the modded content switch keeps its turn until the load is over", async () => {
    setState({}, {codename: "ssbu", has_modded_content: true, mods_active: false});
    await renderAt("/web/app/event?lng=en");
    const toggle = () => screen.getByRole("switch", {name: i18n.t("app_modded_content")});
    jest.useFakeTimers();

    fireEvent.click(toggle());
    await settle();
    expect(bodies("POST", "/api/v1/game")).toEqual([
        {codename: "ssbu", modsActive: true}]);
    emit("status", {revision: 1, jobs: [
        {id: 1, code: "assets_load", args: {codename: "ssbu"}, done: 0, total: 3}]});
    await settle();
    act(() => {
        jest.advanceTimersByTime(WRITE_TIMEOUT_MS * 2);
    });
    await settle();
    expect(toggle()).toBeChecked();

    // The load over and the state as it was (it failed): the state's.
    emit("status", {revision: 2, jobs: []});
    await settle();
    expect(toggle()).not.toBeChecked();

    // No load told at all: the state's, WRITE_TIMEOUT_MS after the answer.
    fireEvent.click(toggle());
    await settle();
    act(() => {
        jest.advanceTimersByTime(WRITE_TIMEOUT_MS - 1);
    });
    expect(toggle()).toBeChecked();
    act(() => {
        jest.advanceTimersByTime(1);
    });
    await settle();
    expect(toggle()).not.toBeChecked();
    jest.useRealTimers();

    // Loaded: the state says so.
    fireEvent.click(toggle());
    await settle();
    setState({}, {codename: "ssbu", has_modded_content: true, mods_active: true});
    await settle();
    expect(toggle()).toBeChecked();
});

test("the battle format: the later choice wins, shown until the read after it", async () => {
    await renderAt("/web/app/event?lng=en");
    const group = screen.getByRole("group", {name: i18n.t("app_battle_format")});
    const pressed = () => within(group).getAllByRole("button")
        .filter((button) => button.getAttribute("aria-pressed") === "true")
        .map((button) => button.textContent);
    const answers = [];
    server.put = (body) => new Promise((resolve) => {
        answers.push(() => resolve(ok(200, {format: body.format, changed: true})));
    });

    fireEvent.click(within(group).getByRole("button", {name: "Team"}));
    await settle();
    fireEvent.click(within(group).getByRole("button", {name: "Crew"}));
    await settle();
    expect(bodies("PUT", "/api/v1/battle-format")).toEqual([
        {format: "team"}, {format: "crew"}]);
    expect(pressed()).toEqual(["Crew"]);

    // The earlier one's answer: nothing changes.
    answers[0]();
    await settle();
    expect(pressed()).toEqual(["Crew"]);

    // The later one's: still shown while it is read again.
    let read;
    server.formatRead = () => new Promise((resolve) => {
        read = () => resolve(ok(200, {format: "crew", board: 3}));
    });
    answers[1]();
    await settle();
    expect(read).toBeDefined();
    expect(pressed()).toEqual(["Crew"]);
    read();
    await settle();
    expect(pressed()).toEqual(["Crew"]);
    server.formatRead = undefined;
});

// ── the footer ──────────────────────────────────────────────────────────

test("the footer shows the app's current job, a bar when it counts", async () => {
    await renderAt("/web/app/event?lng=en");
    const status = screen.getByTestId("app-status");
    expect(status).toHaveAttribute("role", "status");
    expect(status).toHaveTextContent("");
    expect(within(status).queryByRole("progressbar")).not.toBeInTheDocument();

    emit("status", {revision: 1, jobs: [
        {id: 1, code: "startgg_set", args: {}, done: 0, total: 0}]});
    await settle();
    expect(status).toHaveTextContent(i18n.t("status_startgg_set"));
    expect(within(status).queryByRole("progressbar")).not.toBeInTheDocument();

    emit("status", {revision: 2, jobs: [
        {id: 2, code: "assets_load", args: {codename: "ssbu"}, done: 3, total: 4}]});
    await settle();
    expect(status).toHaveTextContent(
        i18n.t("status_assets_load", {codename: "ssbu"}));
    expect(within(status).getByRole("progressbar"))
        .toHaveAttribute("aria-valuenow", "75");

    emit("status", {revision: 3, jobs: []});
    await settle();
    expect(status).toHaveTextContent("");
});

function shellUrls() {
    return window.open.mock.calls.map(([url]) => new URL(url));
}

test("in the desktop's window the Help menu asks the desktop", async () => {
    server.session = ok(200, {ticket: "ticket-1"});
    jest.spyOn(window, "open").mockImplementation(() => null);
    act(() => receiveShellState({alwaysOnTop: false}));
    await renderAt("/web/app/event?desktop=1&lng=en");

    let menu = await openMenu("app-help");
    const items = Array.from(menu.querySelectorAll('[role^="menuitem"]'))
        .map((item) => item.textContent);
    expect(items).toEqual([
        i18n.t("app_settings"), i18n.t("app_always_on_top"),
        i18n.t("app_add_game_assets"), i18n.t("app_open_reference"),
        i18n.t("app_companion_settings"), i18n.t("app_obs_settings"),
        i18n.t("app_check_updates"), i18n.t("app_discord"),
        i18n.t("app_inquiry"), i18n.t("app_about")]);
    expect(within(menu).getByText(i18n.t("app_api_settings"))).toBeInTheDocument();
    const onTop = within(menu).getByRole("menuitemcheckbox",
                                         {name: i18n.t("app_always_on_top")});
    expect(onTop).toHaveAttribute("aria-checked", "false");

    fireEvent.click(onTop);
    await settle();
    let url = shellUrls()[0];
    expect(url.pathname).toBe("/web/shell-request");
    expect(url.searchParams.get("verb")).toBe("alwaysOnTop");
    expect(url.searchParams.get("on")).toBe("1");
    expect(url.searchParams.get("ticket")).toBe("ticket-1");
    act(() => {
        receiveShellResult(url.searchParams.get("request"),
                           {ok: true, alwaysOnTop: true});
        receiveShellState({alwaysOnTop: true});
    });
    await settle();

    menu = await openMenu("app-help");
    expect(within(menu).getByRole("menuitemcheckbox",
                                  {name: i18n.t("app_always_on_top")}))
        .toHaveAttribute("aria-checked", "true");
    fireEvent.click(within(menu).getByRole("menuitem", {name: i18n.t("app_discord")}));
    await settle();
    url = shellUrls()[1];
    expect(url.searchParams.get("verb")).toBe("openExternal");
    expect(url.searchParams.get("target")).toBe("discord");
    act(() => receiveShellResult(url.searchParams.get("request"), {ok: true}));

    menu = await openMenu("app-help");
    fireEvent.click(within(menu).getByRole("menuitem", {name: i18n.t("app_inquiry")}));
    await settle();
    url = shellUrls()[2];
    expect(url.searchParams.get("target")).toBe("inquiry");
    act(() => receiveShellResult(url.searchParams.get("request"),
                                 {ok: false, error: "open_failed"}));
    await settle();
    // Answered, not done: not "no answer".
    expect(screen.getByText(i18n.t("app_shell_not_done"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("app_shell_failed"))).not.toBeInTheDocument();

    menu = await openMenu("app-help");
    fireEvent.click(within(menu).getByRole("menuitem", {name: i18n.t("app_check_updates")}));
    await settle();
    url = shellUrls()[3];
    expect(url.searchParams.get("verb")).toBe("checkUpdates");
    act(() => receiveShellResult(url.searchParams.get("request"),
                                 {ok: false, error: "busy"}));
    await settle();
    expect(screen.getByText(i18n.t("app_updates_busy"))).toBeInTheDocument();
    act(() => receiveShellState({}));
});

test("elsewhere the Help menu offers no shell request, and links the addresses", async () => {
    server.session = refuse(403, "FORBIDDEN");
    jest.spyOn(window, "open").mockImplementation(() => null);
    await renderAt("/web/app/event?lng=en");

    const menu = await openMenu("app-help");
    expect(within(menu).queryByText(i18n.t("app_always_on_top"))).not.toBeInTheDocument();
    expect(within(menu).queryByText(i18n.t("app_check_updates"))).not.toBeInTheDocument();
    const discord = within(menu).getByRole("menuitem", {name: i18n.t("app_discord")});
    expect(discord.tagName).toBe("A");
    expect(discord).toHaveAttribute("href", DISCORD_URL);
    expect(discord).toHaveAttribute("target", "_blank");
    expect(discord).toHaveAttribute("rel", "noopener noreferrer");
    const inquiry = within(menu).getByRole("menuitem", {name: i18n.t("app_inquiry")});
    expect(inquiry.tagName).toBe("A");
    expect(inquiry).toHaveAttribute("href", `mailto:${BUSINESS_INQUIRY_EMAIL}`);
    expect(inquiry).not.toHaveAttribute("target");
    expect(window.open).not.toHaveBeenCalled();
});


test("the Help menu's shell requests: a newer request, no answer, no ticket", async () => {
    server.session = ok(200, {ticket: "ticket-2"});
    jest.spyOn(window, "open").mockImplementation(() => null);
    await renderAt("/web/app/event?desktop=1&lng=en");
    const click = async (key) => {
        const menu = await openMenu("app-help");
        fireEvent.click(within(menu).getByRole("menuitem", {name: i18n.t(key)}));
        await settle();
    };

    // The newer one answers; the older says nothing.
    await click("app_check_updates");
    await click("app_check_updates");
    const second = shellUrls()[1];
    act(() => receiveShellResult(second.searchParams.get("request"), {ok: true}));
    await settle();
    expect(screen.queryByText(i18n.t("app_shell_failed"))).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t("app_shell_not_done"))).not.toBeInTheDocument();

    // No answer within the wait.
    jest.useFakeTimers();
    await click("app_discord");
    act(() => {
        jest.advanceTimersByTime(SHELL_ANSWER_TIMEOUT_MS);
    });
    await settle();
    jest.useRealTimers();
    expect(screen.getByText(i18n.t("app_shell_failed"))).toBeInTheDocument();

    // The ticket dropped (read again: 403, not the desktop's any more).
    server.session = refuse(403, "FORBIDDEN");
    await act(async () => {
        await loadShellSession().catch(() => undefined);
    });
    await click("app_inquiry");
    expect(screen.getByText(i18n.t("app_shell_unavailable"))).toBeInTheDocument();
});

test("while it is not known whose window this is, the addresses wait", async () => {
    server.session = new Promise(() => {});
    await renderAt("/web/app/event?lng=en");

    const menu = await openMenu("app-help");
    expect(within(menu).getByRole("menuitem", {name: i18n.t("app_discord")}))
        .toHaveAttribute("aria-disabled", "true");
    expect(within(menu).getByRole("menuitem", {name: i18n.t("app_inquiry")}))
        .toHaveAttribute("aria-disabled", "true");
    expect(within(menu).queryByText(i18n.t("app_check_updates"))).not.toBeInTheDocument();
});

test("a failed read in the desktop's view: read again from the menu; the inquiry to copy", async () => {
    // A session answer without a ticket: the read failed.
    server.session = ok(200, {});
    jest.spyOn(console, "error").mockImplementation(() => {});
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard",
                          {value: {writeText}, configurable: true});
    try {
        await renderAt("/web/app/event?desktop=1&lng=en");
        const before = calls("GET", "/api/v1/shell-session").length;

        await openMenu("app-help");
        await settle();
        expect(calls("GET", "/api/v1/shell-session").length).toBe(before + 1);
        let menu = screen.getByRole("menu");
        const inquiry = within(menu).getByRole("menuitem", {
            name: (name) => name.startsWith(i18n.t("app_inquiry"))});
        expect(inquiry).toHaveTextContent(BUSINESS_INQUIRY_EMAIL);
        expect(inquiry).not.toHaveAttribute("href");
        // Discord stays a link: the view hands a clicked http(s) window on.
        expect(within(menu).getByRole("menuitem", {name: i18n.t("app_discord")}))
            .toHaveAttribute("href", DISCORD_URL);
        fireEvent.click(inquiry);
        await settle();
        expect(writeText).toHaveBeenCalledWith(BUSINESS_INQUIRY_EMAIL);
        expect(screen.getByText(i18n.t("guide_copied"))).toBeInTheDocument();

        // Read again, and now it is the desktop's.
        server.session = ok(200, {ticket: "ticket-3"});
        await openMenu("app-help");
        await settle();
        menu = screen.getByRole("menu");
        expect(within(menu).getByRole("menuitemcheckbox",
                                      {name: i18n.t("app_always_on_top")}))
            .toBeInTheDocument();
    } finally {
        delete navigator.clipboard;
    }
});

test("the Help menu's pages open in the AppShell", async () => {
    await renderAt("/web/app/event?lng=en");
    const pages = [["app_settings", "settings"], ["app_add_game_assets", "assets"],
                   ["app_open_reference", "api"], ["app_companion_settings", "companion"],
                   ["app_obs_settings", "obs"], ["app_about", "about"]];

    for (const [key, panel] of pages) {
        await openMenu("app-help");
        fireEvent.click(screen.getByRole("menuitem", {name: i18n.t(key)}));
        await settle();
        expect(window.location.pathname).toBe(`/web/app/${panel}`);
        expect(screen.getByText(`page ${panel} (${panel}) desktop=false`))
            .toBeInTheDocument();
    }
});

test("the outside addresses are src/AutonaExternalLinks.py's", () => {
    const python = fs.readFileSync(
        path.join(__dirname, "..", "..", "..", "src", "AutonaExternalLinks.py"),
        "utf8");
    const value = (name) => python.match(
        new RegExp(`^${name} = "([^"]+)"`, "m"))?.[1];
    expect(DISCORD_URL).toBe(value("DISCORD_URL"));
    expect(BUSINESS_INQUIRY_EMAIL).toBe(value("BUSINESS_INQUIRY_EMAIL"));
    expect(BUSINESS_INQUIRY_URL).toBe(`mailto:${value("BUSINESS_INQUIRY_EMAIL")}`);
});
