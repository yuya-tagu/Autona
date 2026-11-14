// The page's shell requests (ADR-005 S-final SF-5a-1, hostApi.ts): the ticket
// read before the click (GET /api/v1/shell-session) and kept in the module
// only, the window opened in the click itself, the answer awaited by the
// request's id for SHELL_ANSWER_TIMEOUT_MS, and the desktop's state heard.
// What the desktop does with the window is src/AutonaWebPanelHost.py's
// (tests/test_web_panel_host.py).

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: () => {}};
    return {__esModule: true, default: {instance: () => socket}};
});

const TICKET = "ticket-of-this-process-1234";

function answer(status, body) {
    const text = JSON.stringify(body);
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => JSON.parse(text),
        text: async () => text,
    };
}

let host;
let session;
let opened;

beforeEach(() => {
    // A fresh module each test: the ticket and the waiting requests are its
    // state.
    jest.resetModules();
    host = require("./hostApi");
    session = answer(200, {ok: true, data: {ticket: TICKET}});
    AbortSignal.timeout = jest.fn(() => undefined);
    global.fetch = jest.fn(async (url) => (
        new URL(url).pathname === "/api/v1/shell-session"
            ? session : answer(404, {ok: false})));
    opened = [];
    jest.spyOn(window, "open").mockImplementation((...args) => {
        opened.push(args);
        return null;
    });
    sessionStorage.clear();
    localStorage.clear();
});

afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
    delete global.fetch;
});

function openedQuery() {
    const [url, target, features] = opened[opened.length - 1];
    expect(target).toBe("_blank");
    expect(features).toBe("noopener");
    const parsed = new URL(url);
    expect(parsed.origin).toBe(window.location.origin);
    expect(parsed.pathname).toBe("/web/shell-request");
    expect(parsed.hash).toBe("");
    return Object.fromEntries(parsed.searchParams);
}

test("without the ticket nothing is opened", async () => {
    await expect(host.shellRequest("checkUpdates")).rejects.toMatchObject(
        {code: "no_session"});
    expect(opened).toEqual([]);
    expect(host.hasShellSession()).toBe(false);
});

test("the window is opened in the call, with exactly the request's keys", async () => {
    await host.loadShellSession();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(new URL(global.fetch.mock.calls[0][0]).pathname)
        .toBe("/api/v1/shell-session");

    const asked = host.shellRequest("alwaysOnTop", {on: "1"});

    // Opened before the call returned: in the click, as the desktop needs.
    expect(opened).toHaveLength(1);
    const query = openedQuery();
    expect(Object.keys(query).sort()).toEqual(
        ["on", "request", "ticket", "verb"]);
    expect(query.verb).toBe("alwaysOnTop");
    expect(query.on).toBe("1");
    expect(query.ticket).toBe(TICKET);
    expect(query.request).toMatch(/^[A-Za-z0-9_-]{1,64}$/);

    host.receiveShellResult(query.request, {ok: true, alwaysOnTop: true});
    await expect(asked).resolves.toEqual({ok: true, alwaysOnTop: true});
});

test("a verb without arguments carries the three keys only", async () => {
    await host.loadShellSession();
    host.shellRequest("checkUpdates");
    expect(Object.keys(openedQuery()).sort())
        .toEqual(["request", "ticket", "verb"]);
});

test("an argument cannot stand in for a fixed key", async () => {
    await host.loadShellSession();
    host.shellRequest("openExternal",
                      {target: "discord", ticket: "x", verb: "y", request: "z"});
    const query = openedQuery();
    expect(query.verb).toBe("openExternal");
    expect(query.ticket).toBe(TICKET);
    expect(query.request).not.toBe("z");
    expect(query.target).toBe("discord");
});

test("each request has an id of its own", async () => {
    await host.loadShellSession();
    host.shellRequest("checkUpdates");
    host.shellRequest("openExternal", {target: "discord"});
    const ids = opened.map(([url]) => new URL(url).searchParams.get("request"));
    expect(new Set(ids).size).toBe(2);
});

test("an answer for no waiting request is dropped", async () => {
    await host.loadShellSession();
    const asked = host.shellRequest("checkUpdates");
    const {request} = openedQuery();

    host.receiveShellResult("another-documents-id", {ok: false});
    host.receiveShellResult(request, {ok: true});
    // A second answer for the same id: already answered.
    host.receiveShellResult(request, {ok: false, error: "late"});

    await expect(asked).resolves.toEqual({ok: true});
});

test("no answer within the deadline rejects, and a late one is dropped", async () => {
    await host.loadShellSession();
    jest.useFakeTimers();
    const asked = host.shellRequest("checkUpdates");
    const {request} = openedQuery();
    const settled = expect(asked).rejects.toMatchObject({code: "timeout"});

    jest.advanceTimersByTime(host.SHELL_ANSWER_TIMEOUT_MS - 1);
    jest.advanceTimersByTime(1);
    await settled;
    expect(host.SHELL_ANSWER_TIMEOUT_MS).toBe(10000);
    host.receiveShellResult(request, {ok: true});
});

test("a newer request of a verb supersedes the older one", async () => {
    await host.loadShellSession();
    const first = host.shellRequest("alwaysOnTop", {on: "1"});
    const other = host.shellRequest("checkUpdates");
    const second = host.shellRequest("alwaysOnTop", {on: "0"});
    const [firstUrl, otherUrl, secondUrl] = opened.map(
        ([url]) => new URL(url).searchParams.get("request"));

    await expect(first).rejects.toMatchObject({code: "superseded"});
    host.receiveShellResult(firstUrl, {ok: true, alwaysOnTop: true});
    host.receiveShellResult(secondUrl, {ok: true, alwaysOnTop: false});
    host.receiveShellResult(otherUrl, {ok: true});
    await expect(second).resolves.toEqual({ok: true, alwaysOnTop: false});
    await expect(other).resolves.toEqual({ok: true});
});

test("a refused session drops the ticket", async () => {
    await host.loadShellSession();
    expect(host.hasShellSession()).toBe(true);
    session = answer(403, {ok: false, error: {code: "FORBIDDEN"}});

    await expect(host.loadShellSession()).rejects.toMatchObject({status: 403});

    expect(host.hasShellSession()).toBe(false);
    await expect(host.shellRequest("checkUpdates")).rejects.toMatchObject(
        {code: "no_session"});
    expect(opened).toEqual([]);
});

test("a session without a ticket is no session", async () => {
    session = answer(200, {ok: true, data: {}});
    await expect(host.loadShellSession()).rejects.toThrow();
    expect(host.hasShellSession()).toBe(false);
});

// Only a 403 says this is not the shared view. Any other failed read keeps
// the ticket already held (the process's ticket does not change), and the
// page can still ask.
async function expectKept(load) {
    await host.loadShellSession();
    expect(host.hasShellSession()).toBe(true);

    await expect(load()).rejects.toBeDefined();

    expect(host.hasShellSession()).toBe(true);
    host.shellRequest("checkUpdates");
    expect(openedQuery().ticket).toBe(TICKET);
}

function retriesAtOnce() {
    // getWithRetry waits between its tries; run them now.
    jest.spyOn(global, "setTimeout").mockImplementation((fn) => {
        fn();
        return 0;
    });
}

test.each([500, 502, 503])(
    "a %i after the retries keeps the ticket", async (status) => {
        await expectKept(async () => {
            session = answer(status, {ok: false, error: {code: "INTERNAL"}});
            retriesAtOnce();
            try {
                await host.loadShellSession();
            } finally {
                global.setTimeout.mockRestore();
            }
        });
        // Read as often as getWithRetry tries (LOAD_ATTEMPTS), after the
        // first read.
        expect(global.fetch).toHaveBeenCalledTimes(1 + 3);
    });

test("a network failure keeps the ticket", async () => {
    await expectKept(async () => {
        global.fetch.mockImplementation(async () => {
            throw new TypeError("Failed to fetch");
        });
        retriesAtOnce();
        try {
            await host.loadShellSession();
        } finally {
            global.setTimeout.mockRestore();
        }
    });
});

test("a read given up as the page left keeps the ticket", async () => {
    await expectKept(async () => {
        session = answer(503, {ok: false, error: {code: "UNAVAILABLE"}});
        // live() false: the page unmounted; not tried again.
        await host.loadShellSession(() => false);
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
});

test("an answer without a ticket keeps the one held", async () => {
    await expectKept(async () => {
        session = answer(200, {ok: true, data: {}});
        await host.loadShellSession();
    });
});

test("another refusal than 403 keeps the ticket", async () => {
    await expectKept(async () => {
        session = answer(401, {ok: false, error: {code: "UNAUTHENTICATED"}});
        await host.loadShellSession();
    });
});

test("the ticket is kept nowhere but the module", async () => {
    await host.loadShellSession();
    host.shellRequest("checkUpdates");

    expect(JSON.stringify({...sessionStorage})).not.toContain(TICKET);
    expect(JSON.stringify({...localStorage})).not.toContain(TICKET);
    expect(window.location.href).not.toContain(TICKET);
    expect(document.body.innerHTML).not.toContain(TICKET);
});

test("the desktop answers and tells its state through the panel shell", async () => {
    host.installPanelShellApi();
    await host.loadShellSession();
    const asked = host.shellRequest("checkUpdates");
    const {request} = openedQuery();
    const heard = [];
    const unsubscribe = host.subscribeShellState((state) => heard.push(state));

    window.autonaPanelShell.shellResult(request, {ok: true});
    window.autonaPanelShell.shellState({alwaysOnTop: true});
    unsubscribe();
    window.autonaPanelShell.shellState({alwaysOnTop: false});

    await expect(asked).resolves.toEqual({ok: true});
    expect(heard).toEqual([{alwaysOnTop: true}]);
    expect(host.currentShellState()).toEqual({alwaysOnTop: false});
});
