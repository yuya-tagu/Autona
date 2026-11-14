import React from "react";
import fs from "fs";
import path from "path";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
    waitForElementToBeRemoved,
    within,
} from "@testing-library/react";
import i18n from "../i18n/config";
import {PanelShellContext} from "../PanelShell/context";
import {canCopyImage, copyImage} from "../PanelShell/clipboard";
import {PANELS} from "../PanelShell/registry";
import ApiPage, {CATALOG_PATH, NARROW_REFERENCE} from "./ApiPage";
import CompanionPage from "./CompanionPage";
import ObsPage from "./ObsPage";
import AboutPage from "./AboutPage";
import {BUSINESS_INQUIRY_EMAIL} from "../AppShell/externalLinks";
import {PAGE_API_URL} from "./ConnectionCard";
import {browserUrl} from "./OpenInBrowserButton";
import {examplesOf} from "./examples";
import {
    buildCurl,
    carriesToken,
    compactJson,
    destructiveMarkers,
    hideToken,
    isDestructive,
    spacedJson,
    testerPath,
    watchPath,
} from "./format";

// The guide pages (ADR-005 S-final SF-4a-2): the Qt API Settings, Companion
// and OBS windows and the About box as /web/api, /web/companion, /web/obs
// and /web/about. Each reads GET /api/v1/connection (THIS_PC); the API page
// also reads the catalogue and, in the desktop's shared view only, the
// access token (GET /api/v1/access-token: 403 anywhere else), which it
// keeps in its state only.

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

// The image clipboard is a browser's (ClipboardItem in a secure context):
// what the Companion page asks of it is stood in for.
jest.mock("../PanelShell/clipboard", () => ({
    ...jest.requireActual("../PanelShell/clipboard"),
    canCopyImage: jest.fn(() => true),
    copyImage: jest.fn(() => Promise.resolve()),
}));

const TOKEN = "tok-1234567890";
const NEW_TOKEN = "tok-new-0987654321";

const CONNECTION = {
    port: 5107,
    lan: false,
    lanAtRestart: false,
    baseUrl: "http://127.0.0.1:5107/api/v1",
    localBaseUrl: "http://127.0.0.1:5107/api/v1",
    outDir: "C:\\Autona\\out",
    pathSeparator: "\\",
};

const LAN_CONNECTION = {
    ...CONNECTION,
    lan: true,
    baseUrl: "http://192.168.1.20:5107/api/v1",
};

const CATALOG = {
    endpoints: [
        {id: "health", method: "GET", path: "/health", example: "/health",
         description: "Liveness check", body: null, destructive: false,
         logged: false, confirm: false},
        {id: "team_score_adjust", method: "POST",
         path: "/scoreboards/{n}/teams/{t}/score:adjust",
         example: "/scoreboards/1/teams/1/score:adjust",
         description: "Adjust a team's score", body: {delta: 1},
         destructive: false, logged: true, confirm: false},
        {id: "scoreboard_clear", method: "POST", path: "/scoreboards/{n}:clear",
         example: "/scoreboards/1:clear", description: "Clear a scoreboard",
         body: {confirm: true}, destructive: true, logged: true,
         confirm: true},
    ],
    errorCodes: [
        {code: "NOT_FOUND", status: 404, description: "No such resource"},
    ],
};

// What the fake server answers; each test changes what it needs.
let server;
// The Companion icons' canvas (jsdom draws none): what was drawn on it.
let canvas;

// A 2d context that records what is drawn, and a fixed PNG.
function fakeCanvas() {
    const context = {
        fillRect: jest.fn(),
        fillText: jest.fn(),
        measureText: jest.fn((text) => ({width: text.length * 10})),
    };
    jest.spyOn(HTMLCanvasElement.prototype, "getContext")
        .mockImplementation(() => context);
    jest.spyOn(HTMLCanvasElement.prototype, "toDataURL")
        .mockImplementation(() => "data:image/png;base64,AAAA");
    return context;
}


function freshServer() {
    return {
        connection: {status: 200, data: CONNECTION},
        token: {status: 200, data: {token: TOKEN, available: true}},
        regenerate: {status: 200, data: {token: NEW_TOKEN, available: true}},
        catalog: {status: 200, data: CATALOG},
        health: {status: 200, data: {version: "9.9.9", status: "ok"}},
        other: {status: 200, body: {ok: true, data: {done: true}}},
    };
}

function answer(status, body) {
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 200 ? "OK" : status === 404 ? "NOT FOUND"
            : status === 403 ? "FORBIDDEN" : "",
        json: async () => JSON.parse(text),
        text: async () => text,
    };
}

function envelope(entry, code) {
    if (entry.status >= 200 && entry.status < 300) {
        return answer(entry.status, {ok: true, data: entry.data});
    }
    return answer(entry.status, {ok: false, error: {
        code: entry.code ?? code, message: "no"}});
}

function calls(method, pathname) {
    return global.fetch.mock.calls.filter(([url, init]) =>
        (init?.method ?? "GET") === method
        && new URL(url).pathname === pathname);
}

beforeEach(() => {
    server = freshServer();
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname} = new URL(url);
        if (method === "GET" && pathname === "/api/v1/connection") {
            if (server.connection === "never") {
                return new Promise(() => {});
            }
            return envelope(server.connection, "FORBIDDEN");
        }
        if (method === "GET" && pathname === "/api/v1/access-token") {
            return envelope(server.token, "FORBIDDEN");
        }
        if (method === "POST"
                && pathname === "/api/v1/access-token:regenerate") {
            return envelope(server.regenerate, "NOT_READY");
        }
        if (method === "GET" && pathname === CATALOG_PATH) {
            return envelope(server.catalog, "INTERNAL");
        }
        if (method === "GET" && pathname === "/api/v1/health") {
            return envelope(server.health, "INTERNAL");
        }
        return answer(server.other.status, server.other.body);
    });
    Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {writeText: jest.fn(() => Promise.resolve())},
    });
    window.open = jest.fn();
    canCopyImage.mockImplementation(() => true);
    canvas = fakeCanvas();
    copyImage.mockImplementation(() => Promise.resolve());
});

afterEach(() => {
    delete global.fetch;
    jest.restoreAllMocks();
});

async function settle() {
    await act(async () => {
        for (let i = 0; i < 10; i++) {
            await Promise.resolve();
        }
    });
}

async function renderPage(Page, panel, {desktop = true} = {}) {
    render(
        <PanelShellContext.Provider value={{panel, desktop, language: "en"}}>
            <Page/>
        </PanelShellContext.Provider>,
    );
    await settle();
}

function card(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function copied() {
    return navigator.clipboard.writeText.mock.calls.map(([text]) => text);
}

// ── format.ts: what the windows built ──────────────────────────────────

test("JSON is written as the windows wrote it", () => {
    expect(spacedJson({delta: 1})).toBe('{"delta": 1}');
    expect(spacedJson({a: [1, "b"], c: {d: null}})).toBe(
        '{"a": [1, "b"], "c": {"d": null}}');
    expect(compactJson({delta: -1})).toBe('{"delta":-1}');
    expect(compactJson(null)).toBe("");
});

test("a curl carries the token only for a write from another device", () => {
    const base = "http://192.168.1.20:5107/api/v1";
    const read = {method: "GET", path: "/health", body: null};
    const write = {method: "POST", path: "/scoreboards/1/score:reset",
                   body: null};
    const withBody = {method: "POST",
                      path: "/scoreboards/1/teams/1/score:adjust",
                      body: {delta: 1}};

    expect(buildCurl(read, base, true, TOKEN)).toBe(
        `curl -X GET "${base}/health"`);
    expect(buildCurl(write, base, false, TOKEN)).toBe(
        `curl -X POST "${base}/scoreboards/1/score:reset"`);
    expect(buildCurl(write, base, true, TOKEN)).toBe(
        `curl -X POST "${base}/scoreboards/1/score:reset" `
        + `-H "X-Autona-Token: ${TOKEN}"`);
    // The page that cannot know the token says where it goes.
    expect(buildCurl(write, base, true, "")).toBe(
        `curl -X POST "${base}/scoreboards/1/score:reset" `
        + '-H "X-Autona-Token: <token>"');
    expect(buildCurl(withBody, base, false, "")).toBe(
        `curl -X POST "${base}/scoreboards/1/teams/1/score:adjust" `
        + `-H "Content-Type: application/json" -d '{"delta": 1}'`);
});

test("the tester asks before a destructive write the catalogue marks", () => {
    const markers = destructiveMarkers(CATALOG.endpoints);
    expect(markers).toEqual([":clear"]);
    expect(isDestructive("POST", "/scoreboards/2:clear", markers)).toBe(true);
    expect(isDestructive("POST", "/scoreboards/2:clear?x=1", markers))
        .toBe(true);
    expect(isDestructive("GET", "/scoreboards/2:clear", markers)).toBe(false);
    expect(isDestructive("POST", "/scoreboards/2/score:adjust", markers))
        .toBe(false);
    expect(testerPath(" health ")).toBe("/health");
    expect(testerPath("/health")).toBe("/health");
});

test("the tester asks before regenerating the token however it is spelt",
     () => {
    // The shell's own route is not in the catalogue: asked about by name,
    // as the server matches it.
    for (const spelt of ["/access-token:regenerate",
                         "//access-token:regenerate",
                         "/access-token%3Aregenerate",
                         "/access-token:regenerate/",
                         "/access-token:regenerate?x=1",
                         "/Access-Token:Regenerate"]) {
        expect([spelt, isDestructive("POST", spelt, [])])
            .toEqual([spelt, true]);
    }
    expect(isDestructive("GET", "/access-token:regenerate", [])).toBe(false);
    expect(isDestructive("POST", "/access-token", [])).toBe(false);
});

test("a tester answer hides the access token", () => {
    const hidden = "(hidden)";
    expect(hideToken(JSON.stringify({ok: true,
                                     data: {token: "abc", available: true}}),
                     "", hidden))
        .toBe(JSON.stringify({ok: true,
                              data: {token: hidden, available: true}},
                             null, 2));
    // Wherever else the token the page knows turns up.
    expect(hideToken(`a ${TOKEN} b ${TOKEN}`, TOKEN, hidden))
        .toBe(`a ${hidden} b ${hidden}`);
    expect(hideToken(JSON.stringify({ok: false, error: {message: TOKEN}}),
                     TOKEN, hidden)).not.toContain(TOKEN);
    expect(hideToken('{"ok": true, "data": {"done": true}}', TOKEN, hidden))
        .toBe(JSON.stringify({ok: true, data: {done: true}}, null, 2));
    expect(hideToken("", "", hidden)).toBe("");

    expect(carriesToken(JSON.stringify({ok: true, data: {token: "x"}})))
        .toBe(true);
    expect(carriesToken('{"ok": true, "data": {"done": true}}')).toBe(false);
    expect(carriesToken("not json")).toBe(false);
});

test("a watch file's path is joined with this PC's separator", () => {
    expect(watchPath("C:\\Autona\\out", "\\", "out/score/1/match.txt"))
        .toBe("C:\\Autona\\out\\score\\1\\match.txt");
    expect(watchPath("C:\\Autona\\out\\", "\\", "out/program_state.json"))
        .toBe("C:\\Autona\\out\\program_state.json");
    expect(watchPath("/home/me/Autona/out", "/", "out/score/1/phase.txt"))
        .toBe("/home/me/Autona/out/score/1/phase.txt");
});

test("the four pages are panels of the shell", () => {
    expect(PANELS.api).toBe(ApiPage);
    expect(PANELS.companion).toBe(CompanionPage);
    expect(PANELS.obs).toBe(ObsPage);
    expect(PANELS.about).toBe(AboutPage);
});

test("no guide page renders HTML it did not make", () => {
    const dir = __dirname;
    const sources = fs.readdirSync(dir)
        .filter((name) => /\.(tsx?|jsx?)$/.test(name)
            && !name.endsWith(".test.js"));
    expect(sources.length).toBeGreaterThan(5);
    for (const name of sources) {
        const text = fs.readFileSync(path.join(dir, name), "utf8");
        expect([name, text.includes("dangerouslySetInnerHTML")])
            .toEqual([name, false]);
        expect([name, /\binnerHTML\b/.test(text)]).toEqual([name, false]);
    }
});

// ── the connection (every guide) ───────────────────────────────────────

test.each([
    ["lan", LAN_CONNECTION],
    ["lanAtRestart", {...CONNECTION, lanAtRestart: true}],
    ["off", CONNECTION],
])("the API page tells what LAN access %s means", async (state, connection) => {
    server.connection.data = connection;
    await renderPage(ApiPage, "api");

    const connectionCard = await screen.findByRole("region", {
        name: i18n.t("guide_connection")});
    expect(within(connectionCard).getByText(connection.baseUrl))
        .toBeInTheDocument();
    expect(within(connectionCard).getByText(
        `${i18n.t("guide_port")}: 5107`)).toBeInTheDocument();
    expect(within(connectionCard).getByText(
        i18n.t(`guide_api_lan_${state}`))).toBeInTheDocument();
    for (const other of ["lan", "lanAtRestart", "off"]
        .filter((name) => name !== state)) {
        expect(within(connectionCard).queryByText(
            i18n.t(`guide_api_lan_${other}`))).toBeNull();
    }

    fireEvent.click(within(connectionCard).getByRole("button", {
        name: i18n.t("guide_copy_base_url")}));
    await settle();
    expect(copied()).toEqual([connection.baseUrl]);
});

test("a page refused the connection shows the API as it reached it",
     async () => {
    server.connection = {status: 403, code: "FORBIDDEN"};
    await renderPage(CompanionPage, "companion", {desktop: false});

    const connectionCard = card("guide_connection");
    expect(within(connectionCard).getByText(
        i18n.t("guide_connection_this_pc_only"))).toBeInTheDocument();
    expect(within(connectionCard).getByText(PAGE_API_URL))
        .toBeInTheDocument();
});

test("Refresh reads the connection again", async () => {
    await renderPage(ObsPage, "obs");
    expect(calls("GET", "/api/v1/connection")).toHaveLength(1);
    server.connection.data = {...CONNECTION, port: 6000,
                              localBaseUrl: "http://127.0.0.1:6000/api/v1"};

    fireEvent.click(within(card("guide_connection")).getByRole("button", {
        name: i18n.t("guide_refresh")}));
    await settle();

    expect(calls("GET", "/api/v1/connection")).toHaveLength(2);
    expect(within(card("guide_connection")).getByText(
        "http://127.0.0.1:6000/api/v1")).toBeInTheDocument();
});

// While the connection is not known, no address is shown as if it were:
// not the loopback (another device needs the LAN address), not a curl
// without the token header it may need.
function expectNoAddress() {
    const connectionCard = card("guide_connection");
    expect(within(connectionCard).queryByRole("button", {
        name: i18n.t("guide_copy_base_url")})).toBeNull();
    expect(within(connectionCard).queryByText(PAGE_API_URL)).toBeNull();
    expect(within(connectionCard).queryByText(CONNECTION.baseUrl)).toBeNull();
}

test("a connection that fails shows the failure, a retry and no address",
     async () => {
    server.connection = {status: 500, code: "INTERNAL"};
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage(ApiPage, "api");

    // getWithRetry tries three times, a second and two apart.
    const failed = await screen.findByText(i18n.t("guide_connection_failed"),
                                           {}, {timeout: 6000});
    expectNoAddress();
    expect(referenceRow("GET", "/health")).toBeDisabled();
    expect(referenceRow("POST", "/scoreboards/{n}/teams/{t}/score:adjust"))
        .toBeDisabled();

    server.connection = {status: 200, data: LAN_CONNECTION};
    fireEvent.click(within(failed.closest("[role=alert]")).getByRole(
        "button", {name: i18n.t("guide_retry")}));
    await settle();
    expect(within(card("guide_connection")).getByText(LAN_CONNECTION.baseUrl))
        .toBeInTheDocument();
    expect(referenceRow("GET", "/health")).toBeEnabled();
});

test("a connection still loading shows no address", async () => {
    server.connection = "never";
    await renderPage(CompanionPage, "companion");

    expect(within(card("guide_connection")).getByText(
        i18n.t("guide_connection_loading"))).toBeInTheDocument();
    expectNoAddress();
    const table = screen.getByRole("table", {
        name: i18n.t("guide_companion_mappings")});
    const label = i18n.t("guide_example_team1_score_up");
    // "+1" and "-1": one URL, two bodies.
    expect(within(table).getAllByText("…/scoreboards/1/teams/1/score:adjust"))
        .toHaveLength(2);
    expect(within(table).queryByText(/127\.0\.0\.1/)).toBeNull();
    expect(within(table).getByRole("button", {
        name: `${i18n.t("guide_copy_url")}: ${label}`})).toBeDisabled();
    // The body is the same wherever it is sent.
    expect(within(table).getByRole("button", {
        name: `${i18n.t("guide_copy_body")}: ${label}`})).toBeEnabled();
});

test("the OBS guide without the connection says nothing about this PC",
     async () => {
    server.connection = "never";
    await renderPage(ObsPage, "obs");

    expectNoAddress();
    const actions = card("guide_obs_http_title");
    expect(within(actions).getByText("…/scoreboards/1/score:reset"))
        .toBeInTheDocument();
    // The note is for a page from another computer, which this is not
    // known to be.
    expect(within(card("guide_obs_watch_title")).queryByText(
        i18n.t("guide_obs_paths_this_pc_only"))).toBeNull();
});

// ── the API page's access token ────────────────────────────────────────

function tokenField() {
    return screen.getByLabelText(i18n.t("guide_api_token"));
}

test("the app's own view shows the token masked, then shown and copied",
     async () => {
    await renderPage(ApiPage, "api");

    const field = tokenField();
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveValue(TOKEN);
    expect(field).toHaveAttribute("readonly");

    const show = screen.getByRole("button", {
        name: i18n.t("guide_api_token_show")});
    expect(show).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(show);
    expect(tokenField()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", {name: i18n.t("guide_api_token_hide")}))
        .toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_api_copy_token")}));
    await settle();
    expect(copied()).toEqual([TOKEN]);
    expect(await screen.findByText(i18n.t("guide_copied")))
        .toBeInTheDocument();
});

test("a browser is told to open the page in the app for the token",
     async () => {
    server.token = {status: 403, code: "FORBIDDEN"};
    await renderPage(ApiPage, "api", {desktop: false});

    expect(screen.getByText(i18n.t("guide_api_token_in_app")))
        .toBeInTheDocument();
    expect(screen.queryByLabelText(i18n.t("guide_api_token"))).toBeNull();
    expect(screen.queryByRole("button", {
        name: i18n.t("guide_api_token_regenerate")})).toBeNull();
    // Its way to the settings is the settings page's link.
    expect(screen.getByRole("link", {name: i18n.t("guide_api_open_settings")}))
        .toHaveAttribute("href", "/web/settings?lng=en");
});

test("in the AppShell the settings link moves in its frame (S-final SF-5a-3)",
     async () => {
    server.token = {status: 403, code: "FORBIDDEN"};
    window.history.replaceState(null, "", "/web/app/api?lng=en");
    try {
        await renderPage(ApiPage, "api", {desktop: false});
        const link = screen.getByRole("link",
                                      {name: i18n.t("guide_api_open_settings")});
        expect(link).toHaveAttribute("href", "/web/app/settings?lng=en");

        fireEvent.click(link);

        expect(window.location.pathname).toBe("/web/app/settings");
        expect(window.location.search).toBe("?lng=en");
    } finally {
        window.history.replaceState(null, "", "/");
    }
});

test("the app's view is told where the settings are", async () => {
    await renderPage(ApiPage, "api");

    expect(screen.getByText(i18n.t("guide_api_settings_in_app")))
        .toBeInTheDocument();
    expect(screen.queryByRole("link", {
        name: i18n.t("guide_api_open_settings")})).toBeNull();
});

test("a token read that fails offers to read it again", async () => {
    server.token = {status: 500, code: "INTERNAL"};
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage(ApiPage, "api");

    // getWithRetry tries three times, a second and two apart.
    const failed = await screen.findByText(i18n.t("guide_api_token_failed"),
                                           {}, {timeout: 6000});
    expect(screen.queryByLabelText(i18n.t("guide_api_token"))).toBeNull();
    server.token = {status: 200, data: {token: TOKEN, available: true}};

    fireEvent.click(within(failed.closest("[role=alert]")).getByRole(
        "button", {name: i18n.t("guide_retry")}));
    await settle();

    expect(tokenField()).toHaveValue(TOKEN);
});

test("regenerating asks first and then shows the new token", async () => {
    await renderPage(ApiPage, "api");

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_api_token_regenerate")}));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(i18n.t("guide_api_regenerate_confirm")))
        .toBeInTheDocument();
    expect(calls("POST", "/api/v1/access-token:regenerate")).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("guide_api_token_regenerate")}));
    await settle();

    const sent = calls("POST", "/api/v1/access-token:regenerate");
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0][1].body)).toEqual({confirm: true});
    expect(tokenField()).toHaveValue(NEW_TOKEN);
    expect(await screen.findByText(i18n.t("guide_api_token_regenerated")))
        .toBeInTheDocument();
});

test("a regeneration cancelled sends nothing", async () => {
    await renderPage(ApiPage, "api");

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_api_token_regenerate")}));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("stream_cancel")}));
    await settle();

    expect(calls("POST", "/api/v1/access-token:regenerate")).toHaveLength(0);
    expect(tokenField()).toHaveValue(TOKEN);
});

test("a regeneration refused as not ready keeps the token", async () => {
    server.regenerate = {status: 409, code: "NOT_READY"};
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage(ApiPage, "api");

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_api_token_regenerate")}));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole(
        "button", {name: i18n.t("guide_api_token_regenerate")}));
    await settle();

    expect(await screen.findByText(i18n.t("guide_api_token_not_ready")))
        .toBeInTheDocument();
    expect(tokenField()).toHaveValue(TOKEN);
});

// Whether a value handed somewhere names a token.
function mentionsToken(value) {
    let text = "";
    try {
        text = typeof value === "string" ? value : JSON.stringify(value) ?? "";
    } catch {
        // A value JSON cannot write: its String() below.
    }
    text += ` ${String(value)}`;
    return text.includes(TOKEN) || text.includes(NEW_TOKEN);
}

test("the token is kept nowhere but the page", async () => {
    const stored = jest.spyOn(Storage.prototype, "setItem");
    const before = window.location.href;
    // Other pages and frames: a BroadcastChannel, postMessage.
    const broadcast = [];
    const hadChannel = "BroadcastChannel" in global;
    const channel = global.BroadcastChannel;
    global.BroadcastChannel = class {
        constructor(name) {
            broadcast.push(name);
        }

        postMessage(message) {
            broadcast.push(message);
        }

        addEventListener() {}

        removeEventListener() {}

        close() {}
    };
    const posted = jest.spyOn(window, "postMessage");
    // The log (the console, which the shell's log may carry).
    const logged = ["log", "info", "warn", "error", "debug"].map(
        (level) => jest.spyOn(console, level));
    try {
        await renderPage(ApiPage, "api");

        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("guide_api_token_show")}));
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("guide_api_copy_token")}));
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("guide_api_token_regenerate")}));
        fireEvent.click(within(await screen.findByRole("dialog")).getByRole(
            "button", {name: i18n.t("guide_api_token_regenerate")}));
        await settle();
        expect(tokenField()).toHaveValue(NEW_TOKEN);

        expect(stored).not.toHaveBeenCalled();
        expect(window.location.href).toBe(before);
        expect(window.location.href).not.toContain(TOKEN);
        expect(broadcast.filter(mentionsToken)).toEqual([]);
        expect(posted.mock.calls.flat().filter(mentionsToken)).toEqual([]);
        for (const spy of logged) {
            expect(spy.mock.calls.flat().filter(mentionsToken)).toEqual([]);
        }
    } finally {
        if (hadChannel) {
            global.BroadcastChannel = channel;
        } else {
            delete global.BroadcastChannel;
        }
    }
});

// ── "open in the default browser" ──────────────────────────────────────

test.each([
    ["api", ApiPage],
    ["companion", CompanionPage],
    ["obs", ObsPage],
])("the %s guide opens itself in the default browser from the app",
   async (panel, Page) => {
    await renderPage(Page, panel);

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_open_in_browser")}));

    expect(window.open).toHaveBeenCalledWith(
        `${window.location.origin}/web/${panel}?lng=en`, "_blank",
        "noopener");
    const [url] = window.open.mock.calls[0];
    expect(url).not.toContain("desktop");
    expect(url).not.toContain("token");
    expect(url).not.toContain(TOKEN);
});

test.each([
    ["api", ApiPage],
    ["companion", CompanionPage],
    ["obs", ObsPage],
])("the %s guide in a browser has no browser to open", async (panel, Page) => {
    server.token = {status: 403, code: "FORBIDDEN"};
    await renderPage(Page, panel, {desktop: false});

    expect(screen.queryByRole("button", {
        name: i18n.t("guide_open_in_browser")})).toBeNull();
});

test("the browser's URL keeps the page's language", () => {
    expect(browserUrl("obs", "ja")).toBe(
        `${window.location.origin}/web/obs?lng=ja`);
});

// ── the API reference and the tester ───────────────────────────────────

function referenceRow(method, routePath) {
    return screen.getByRole("button", {
        name: `${i18n.t("guide_api_copy_curl")}: ${method} ${routePath}`});
}

test("the reference lists the catalogue in the page's language",
     async () => {
    await renderPage(ApiPage, "api");

    const table = screen.getByRole("table", {
        name: i18n.t("guide_api_reference")});
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(1 + CATALOG.endpoints.length);
    // Wide (jsdom lays nothing out: not measured, so wide): the method, the
    // path and description, the marks, the buttons.
    expect(within(table).getAllByRole("columnheader")).toHaveLength(4);
    expect(within(table).getByText(i18n.t("guide_api_endpoint_health")))
        .toBeInTheDocument();
    expect(screen.getByRole("region", {name: i18n.t("guide_api_legend")})
        .textContent).toContain(
        `NOT_FOUND  (404)  ${i18n.t("guide_api_error_NOT_FOUND")}`);
});

test("a narrow reference puts the marks and the buttons under the path",
     async () => {
    // The app's view: the table's container ~530 px wide.
    jest.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(
        function width() {
            return this.classList.contains("MuiTableContainer-root")
                ? NARROW_REFERENCE - 190 : 0;
        });
    await renderPage(ApiPage, "api");

    const table = screen.getByRole("table", {
        name: i18n.t("guide_api_reference")});
    expect(within(table).getAllByRole("columnheader")).toHaveLength(2);
    const row = referenceRow("POST", "/scoreboards/{n}:clear").closest("tr");
    const cells = within(row).getAllByRole("cell");
    expect(cells).toHaveLength(2);
    // The path, its description, then the marks and the buttons.
    expect(cells[1].querySelector("[data-api-path]").textContent)
        .toBe("/scoreboards/{n}:clear");
    expect(within(cells[1]).getByText(i18n.t("guide_api_confirm")))
        .toBeInTheDocument();
    expect(within(cells[1]).getByText(i18n.t("guide_api_logged")))
        .toBeInTheDocument();
    expect(within(cells[1]).getByRole("button", {
        name: `${i18n.t("guide_api_load_into_tester")}: POST `
            + "/scoreboards/{n}:clear"})).toBeInTheDocument();

    // The buttons still work there.
    fireEvent.click(referenceRow("GET", "/health"));
    await settle();
    expect(copied()).toEqual([`curl -X GET "${CONNECTION.baseUrl}/health"`]);
});

test("a path breaks only after a slash", async () => {
    await renderPage(ApiPage, "api");

    const path = screen.getByRole("table", {
        name: i18n.t("guide_api_reference")})
        .querySelectorAll("[data-api-path]")[1];
    expect(path.textContent).toBe("/scoreboards/{n}/teams/{t}/score:adjust");
    // Each part kept whole (no break at its "-" or ":"), a <wbr> between.
    expect(Array.from(path.querySelectorAll("span"),
                      (part) => part.textContent)).toEqual(
        ["/", "scoreboards/", "{n}/", "teams/", "{t}/", "score:adjust"]);
    expect(path.querySelectorAll("wbr")).toHaveLength(5);
});

test("a copied curl carries the shell's token for a LAN write", async () => {
    server.connection.data = LAN_CONNECTION;
    await renderPage(ApiPage, "api");

    fireEvent.click(referenceRow("POST",
                                 "/scoreboards/{n}/teams/{t}/score:adjust"));
    fireEvent.click(referenceRow("GET", "/health"));
    await settle();

    expect(copied()).toEqual([
        `curl -X POST "${LAN_CONNECTION.baseUrl}`
        + `/scoreboards/1/teams/1/score:adjust" -H "X-Autona-Token: ${TOKEN}" `
        + `-H "Content-Type: application/json" -d '{"delta": 1}'`,
        `curl -X GET "${LAN_CONNECTION.baseUrl}/health"`,
    ]);
});

test("a copied curl has no token header without LAN access", async () => {
    await renderPage(ApiPage, "api");

    fireEvent.click(referenceRow("POST",
                                 "/scoreboards/{n}/teams/{t}/score:adjust"));
    await settle();

    expect(copied()[0]).not.toContain("X-Autona-Token");
});

test("a browser's curl for a LAN write shows where the token goes",
     async () => {
    server.connection.data = LAN_CONNECTION;
    server.token = {status: 403, code: "FORBIDDEN"};
    await renderPage(ApiPage, "api", {desktop: false});

    fireEvent.click(referenceRow("POST",
                                 "/scoreboards/{n}/teams/{t}/score:adjust"));
    await settle();

    expect(copied()[0]).toContain('-H "X-Autona-Token: <token>"');
    expect(copied()[0]).not.toContain(TOKEN);
});

function testerTab() {
    fireEvent.click(screen.getByRole("tab", {name: i18n.t("guide_api_tester")}));
}

function response() {
    return screen.getByLabelText(i18n.t("guide_api_response"));
}

test("the tester sends a GET and shows the answer", async () => {
    await renderPage(ApiPage, "api");
    testerTab();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    await settle();

    const sent = calls("GET", "/api/v1/health");
    expect(sent).toHaveLength(1);
    expect(sent[0][1].body).toBeUndefined();
    expect(response().textContent).toContain("HTTP 200 OK");
    expect(response().textContent).toContain('"version": "9.9.9"');
});

test("the tester shows a refusal's body", async () => {
    server.other = {status: 404, body: {ok: false, error: {
        code: "NOT_FOUND", message: "no such thing"}}};
    await renderPage(ApiPage, "api");
    testerTab();

    fireEvent.change(screen.getByLabelText(i18n.t("guide_api_path")), {
        target: {value: "nope"}});
    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    await settle();

    expect(calls("GET", "/api/v1/nope")).toHaveLength(1);
    expect(response().textContent).toContain("HTTP 404");
    expect(response().textContent).toContain('"message": "no such thing"');
});

test("the tester asks before a destructive request", async () => {
    await renderPage(ApiPage, "api");

    fireEvent.click(screen.getByRole("button", {
        name: `${i18n.t("guide_api_load_into_tester")}: POST `
            + "/scoreboards/{n}:clear"}));
    expect(screen.getByLabelText(i18n.t("guide_api_path")))
        .toHaveValue("/scoreboards/1:clear");
    expect(screen.getByLabelText(i18n.t("guide_body_json")).value)
        .toBe('{\n  "confirm": true\n}');

    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(i18n.t("guide_api_confirm")))
        .toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("stream_cancel")}));
    // The dialog closing (its exit transition) is gone before the next.
    await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
    expect(calls("POST", "/api/v1/scoreboards/1:clear")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("guide_api_send")}));
    await settle();

    const sent = calls("POST", "/api/v1/scoreboards/1:clear");
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0][1].body)).toEqual({confirm: true});
    expect(sent[0][1].headers.get("Content-Type")).toBe("application/json");
});

test("the tester hides the access token it reads", async () => {
    await renderPage(ApiPage, "api");
    testerTab();

    fireEvent.change(screen.getByLabelText(i18n.t("guide_api_path")), {
        target: {value: "access-token"}});
    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    await settle();

    // The token row's read and the tester's.
    expect(calls("GET", "/api/v1/access-token")).toHaveLength(2);
    expect(response().textContent).toContain("HTTP 200");
    expect(response().textContent).toContain(
        `"token": "${i18n.t("guide_api_token_hidden")}"`);
    expect(response().textContent).not.toContain(TOKEN);
});

test("the tester asks before regenerating the token and hides the new one",
     async () => {
    await renderPage(ApiPage, "api");
    // A write loaded (POST), then pointed at the shell's route.
    fireEvent.click(screen.getByRole("button", {
        name: `${i18n.t("guide_api_load_into_tester")}: POST `
            + "/scoreboards/{n}/teams/{t}/score:adjust"}));
    fireEvent.change(screen.getByLabelText(i18n.t("guide_api_path")), {
        target: {value: "/access-token:regenerate"}});
    fireEvent.change(screen.getByLabelText(i18n.t("guide_body_json")), {
        target: {value: '{"confirm": true}'}});

    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/POST \/access-token:regenerate/))
        .toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("stream_cancel")}));
    await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
    expect(calls("POST", "/api/v1/access-token:regenerate")).toHaveLength(0);

    // What the server answers from now on.
    server.token = {status: 200, data: {token: NEW_TOKEN, available: true}};
    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", {
        name: i18n.t("guide_api_send")}));
    await settle();

    expect(calls("POST", "/api/v1/access-token:regenerate")).toHaveLength(1);
    expect(response().textContent).toContain(i18n.t("guide_api_token_hidden"));
    expect(response().textContent).not.toContain(NEW_TOKEN);
    // The token row shows the new token, not the one that no longer works.
    await waitFor(() => expect(tokenField()).toHaveValue(NEW_TOKEN));
});

test("the tester refuses a body that is not JSON", async () => {
    await renderPage(ApiPage, "api");
    testerTab();

    fireEvent.change(screen.getByLabelText(i18n.t("guide_body_json")), {
        target: {value: "{ nope"}});
    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    await settle();

    expect(response().textContent).toContain(i18n.t("guide_api_invalid_json"));
    expect(calls("GET", "/api/v1/health")).toHaveLength(0);
});

test("the tester sends the idempotency key it is given", async () => {
    await renderPage(ApiPage, "api");
    testerTab();

    fireEvent.change(screen.getByLabelText("Idempotency-Key"), {
        target: {value: " key-1 "}});
    fireEvent.click(screen.getByRole("button", {name: i18n.t("guide_api_send")}));
    await settle();

    expect(calls("GET", "/api/v1/health")[0][1].headers.get(
        "Idempotency-Key")).toBe("key-1");
});

// ── the Companion guide ────────────────────────────────────────────────

test("the Companion guide lists its mappings to copy", async () => {
    await renderPage(CompanionPage, "companion");

    const rows = examplesOf("companion");
    const table = screen.getByRole("table", {
        name: i18n.t("guide_companion_mappings")});
    expect(within(table).getAllByRole("row")).toHaveLength(1 + rows.length);
    const label = i18n.t("guide_example_team1_score_up");

    fireEvent.click(within(table).getByRole("button", {
        name: `${i18n.t("guide_copy_url")}: ${label}`}));
    fireEvent.click(within(table).getByRole("button", {
        name: `${i18n.t("guide_copy_body")}: ${label}`}));
    await settle();

    expect(copied()).toEqual([
        `${CONNECTION.baseUrl}/scoreboards/1/teams/1/score:adjust`,
        '{"delta": 1}',
    ]);
    // A row with no body offers none to copy.
    expect(within(table).queryByRole("button", {
        name: `${i18n.t("guide_copy_body")}: `
            + i18n.t("guide_example_reset_scores")})).toBeNull();
    expect(screen.getByText(i18n.t("guide_companion_lan_off")))
        .toBeInTheDocument();
    expect(document.title).toBe(`Autona ${i18n.t("guide_companion_title")}`);
});

test("a Companion key icon is copied as an image", async () => {
    await renderPage(CompanionPage, "companion");

    const label = i18n.t("guide_example_swap_teams");
    expect(screen.getByRole("img", {name: label}))
        .toHaveAttribute("src", "data:image/png;base64,AAAA");
    // Each icon's lines drawn: a top line ("P1"), a label of one line
    // ("+1", "SWAP") or of two ("SCORE" over "RESET").
    expect(canvas.fillText.mock.calls.map(([text]) => text)).toEqual(
        expect.arrayContaining(["P1", "+1", "SWAP", "SCORE", "RESET"]));
    fireEvent.click(screen.getByRole("button", {
        name: `${i18n.t("guide_companion_copy_icon")}: ${label}`}));
    await settle();

    expect(copyImage).toHaveBeenCalledWith("data:image/png;base64,AAAA");
    expect(await screen.findByText(i18n.t("guide_companion_icon_copied",
                                          {name: label})))
        .toBeInTheDocument();
});

test("the Companion icons are drawn again once the fonts are loaded",
     async () => {
    // A canvas draws with the font there when it draws.
    let loaded;
    const ready = new Promise((resolve) => {
        loaded = resolve;
    });
    Object.defineProperty(document, "fonts", {configurable: true,
                                              value: {ready}});
    try {
        await renderPage(CompanionPage, "companion");
        const drawn = HTMLCanvasElement.prototype.toDataURL.mock.calls.length;
        expect(drawn).toBe(examplesOf("companion").length);

        await act(async () => {
            loaded();
            await ready;
        });

        expect(HTMLCanvasElement.prototype.toDataURL.mock.calls.length)
            .toBe(2 * drawn);
    } finally {
        delete document.fonts;
    }
});

test("without an image clipboard the icons are there to save", async () => {
    canCopyImage.mockImplementation(() => false);
    await renderPage(CompanionPage, "companion", {desktop: false});

    expect(screen.getByText(i18n.t("guide_companion_icons_no_copy")))
        .toBeInTheDocument();
    const label = i18n.t("guide_example_swap_teams");
    expect(screen.getByRole("img", {name: label})).toBeInTheDocument();
    expect(screen.queryByRole("button", {
        name: `${i18n.t("guide_companion_copy_icon")}: ${label}`})).toBeNull();
});

test("without a canvas each icon is its colour", async () => {
    HTMLCanvasElement.prototype.getContext.mockImplementation(() => null);
    await renderPage(CompanionPage, "companion");

    const label = i18n.t("guide_example_swap_teams");
    expect(screen.queryByRole("img", {name: label})).toBeNull();
    expect(screen.getByLabelText(label)).toBeInTheDocument();
    expect(screen.queryByRole("button", {
        name: `${i18n.t("guide_companion_copy_icon")}: ${label}`})).toBeNull();
});

// ── the OBS guide ──────────────────────────────────────────────────────

test("the OBS guide shows this PC's paths with its separator", async () => {
    await renderPage(ObsPage, "obs");

    const watch = card("guide_obs_watch_title");
    expect(within(watch).getByText("C:\\Autona\\out\\score\\1\\match.txt"))
        .toBeInTheDocument();
    expect(within(watch).getByText("C:\\Autona\\out\\program_state.json"))
        .toBeInTheDocument();
    fireEvent.click(within(watch).getByRole("button", {
        name: `${i18n.t("guide_obs_copy_path")}: `
            + i18n.t("guide_obs_watch_match")}));
    fireEvent.click(within(card("guide_connection")).getByRole("button", {
        name: i18n.t("guide_obs_copy_out_dir")}));
    await settle();

    expect(copied()).toEqual(["C:\\Autona\\out\\score\\1\\match.txt",
                              "C:\\Autona\\out"]);
});

test("the OBS guide joins a POSIX out/ with /", async () => {
    server.connection.data = {...CONNECTION, outDir: "/home/me/Autona/out/",
                              pathSeparator: "/"};
    await renderPage(ObsPage, "obs");

    expect(within(card("guide_obs_watch_title")).getByText(
        "/home/me/Autona/out/score/1/team/1/score.txt")).toBeInTheDocument();
});

test("the OBS guide's actions go to the loopback, bodies compact",
     async () => {
    server.connection.data = LAN_CONNECTION;
    await renderPage(ObsPage, "obs");

    const actions = card("guide_obs_http_title");
    const rows = within(actions).getAllByRole("row");
    expect(rows).toHaveLength(1 + examplesOf("obs").length);
    expect(within(actions).getByText(
        `${LAN_CONNECTION.localBaseUrl}/scoreboards/1/score:reset`))
        .toBeInTheDocument();
    expect(within(actions).getAllByText('{"delta":1}').length).toBe(2);
    // The statistics are the Companion guide's only.
    expect(within(actions).queryByText(
        i18n.t("guide_example_stats_refresh"))).toBeNull();
    expect(screen.getByText(i18n.t("guide_obs_lan_lan"))).toBeInTheDocument();
});

test("a page refused the connection shows the paths below the app",
     async () => {
    server.connection = {status: 403, code: "FORBIDDEN"};
    await renderPage(ObsPage, "obs", {desktop: false});

    const watch = card("guide_obs_watch_title");
    expect(within(watch).getByText(i18n.t("guide_obs_paths_this_pc_only")))
        .toBeInTheDocument();
    expect(within(watch).getByText("out/score/1/match.txt"))
        .toBeInTheDocument();
    expect(within(watch).queryByRole("button", {
        name: new RegExp(i18n.t("guide_obs_copy_path"))})).toBeNull();
});

// ── About ──────────────────────────────────────────────────────────────

test("About shows the version and the address to copy", async () => {
    await renderPage(AboutPage, "about");

    expect(await screen.findByText("Autona v9.9.9")).toBeInTheDocument();
    // The title names the app once ("About Autona", not "Autona About
    // Autona"); the other guides' are "Autona <title>".
    expect(document.title).toBe(i18n.t("guide_about_title"));
    expect(screen.queryByRole("button", {
        name: i18n.t("guide_open_in_browser")})).toBeNull();
    expect(screen.getByText(BUSINESS_INQUIRY_EMAIL)).toBeInTheDocument();
    // No mailto: the shared view hands http(s) only to the system.
    for (const link of screen.getAllByRole("link")) {
        expect(link.getAttribute("href")).toMatch(/^https:\/\//);
        expect(link).toHaveAttribute("target", "_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
    }
    expect(screen.getByRole("link", {name: "start.gg"}))
        .toHaveAttribute("href", "https://www.start.gg/");

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_about_copy_email")}));
    await settle();
    expect(copied()).toEqual([BUSINESS_INQUIRY_EMAIL]);
});

test("About without the version still says what it is", async () => {
    server.health = {status: 404, code: "NOT_FOUND"};
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage(AboutPage, "about");

    expect(await screen.findByText("Autona v?")).toBeInTheDocument();
});
