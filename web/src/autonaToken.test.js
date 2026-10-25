import {
    captureTokenFromUrl,
    getToken,
    resetTokenForTests,
    socketAuth,
    tokenHeaders,
    withToken,
} from "./autonaToken";

// ADR-005 S3a: "ブラウザ版を開く" opens the Web UI with ?token=; from another
// computer the desktop app takes writes only with it. These pin the client
// half: the token is kept for the tab, taken off the address bar, and sent
// on every request and on the Socket.IO connection -- and without one,
// nothing changes.

jest.mock("socket.io-client", () => ({io: jest.fn(() => ({}))}));

function openAt(url) {
    window.history.replaceState(null, "", url);
    resetTokenForTests();
}

afterEach(() => {
    openAt("/");
    jest.restoreAllMocks();
});

test("the token is taken off the URL and kept for the tab", () => {
    openAt("/scoreboard?token=abc123&scoreboardNumber=1#x");

    captureTokenFromUrl();

    expect(getToken()).toBe("abc123");
    expect(window.sessionStorage.getItem("autona.webserverToken")).toBe("abc123");
    expect(window.location.pathname).toBe("/scoreboard");
    expect(window.location.search).toBe("?scoreboardNumber=1");
    expect(window.location.hash).toBe("#x");

    // A reload (same tab, token gone from the URL) still has it.
    window.history.replaceState(null, "", "/scoreboard");
    expect(getToken()).toBe("abc123");
});

test("without a token nothing is sent", () => {
    openAt("/team");

    expect(getToken()).toBeNull();
    expect(tokenHeaders()).toEqual({});
    expect(socketAuth()).toEqual({});
    expect(window.location.pathname + window.location.search).toBe("/team");
});

test("the desktop Designer's own token stays in its URL", () => {
    openAt("/web?view=designer&desktop=1&token=designer-token");

    captureTokenFromUrl();

    expect(getToken()).toBeNull();
    expect(window.location.search).toBe(
        "?view=designer&desktop=1&token=designer-token");
});

test("the token survives blocked storage for this page load", () => {
    openAt("/scoreboard?token=abc123");
    jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("blocked");
    });
    jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
    });

    expect(getToken()).toBe("abc123");
    expect(tokenHeaders()).toEqual({"X-Autona-Token": "abc123"});
});

describe("api.ts", () => {
    let fetchMock;

    beforeEach(() => {
        fetchMock = jest.fn(() => Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ok: true, data: 1}),
            text: () => Promise.resolve("{}"),
        }));
        global.fetch = fetchMock;
        if (typeof AbortSignal.timeout !== "function") {
            AbortSignal.timeout = () => undefined;
        }
    });

    afterEach(() => {
        delete global.fetch;
    });

    test("every request carries the token header", async () => {
        openAt("/scoreboard?token=abc123");
        const {apiGetJson, apiPost, apiPatch} = require("./api");

        await apiGetJson("/api/v1/state");
        await apiPost("/api/v1/scoreboards/1:apply", {});
        await apiPatch("/api/v1/scoreboards/1/set", {bestOf: 3});

        const headers = fetchMock.mock.calls.map(
            ([, init]) => Object.fromEntries(init.headers.entries()));
        expect(headers[0]).toEqual({"x-autona-token": "abc123"});
        expect(headers[1]).toEqual({
            "content-type": "application/json", "x-autona-token": "abc123"});
        expect(headers[2]).toEqual({
            "content-type": "application/json", "x-autona-token": "abc123"});
    });

    test("no token, no header", async () => {
        openAt("/scoreboard");
        const {apiPost} = require("./api");

        await apiPost("/api/v1/scoreboards/1:apply", {});

        expect(Object.fromEntries(fetchMock.mock.calls[0][1].headers.entries()))
            .toEqual({"content-type": "application/json"});
    });
});

describe("withToken", () => {
    // Whatever form the caller's headers take, none of them is dropped.
    test.each([
        ["a plain object", {"content-type": "application/json", "X-Extra": "1"}],
        ["a Headers", new Headers({"content-type": "application/json", "X-Extra": "1"})],
        ["pairs", [["content-type", "application/json"], ["X-Extra", "1"]]],
    ])("keeps headers given as %s", (_kind, given) => {
        openAt("/scoreboard?token=abc123");
        const merged = withToken(given);
        expect(merged.get("content-type")).toBe("application/json");
        expect(merged.get("x-extra")).toBe("1");
        expect(merged.get("X-Autona-Token")).toBe("abc123");
    });

    test("the token replaces a stale header of the same name", () => {
        openAt("/scoreboard?token=abc123");
        expect(withToken({"x-autona-token": "old"}).get("X-Autona-Token"))
            .toBe("abc123");
    });

    test("without a token the headers are left as they are", () => {
        openAt("/scoreboard");
        const merged = withToken([["X-Extra", "1"]]);
        expect(Object.fromEntries(merged.entries())).toEqual({"x-extra": "1"});
        expect(Object.fromEntries(withToken().entries())).toEqual({});
    });
});

test("the Socket.IO connection offers the token as auth", () => {
    openAt("/scoreboard?token=abc123");
    const {io} = require("socket.io-client");
    const {SocketSingleton} = require("./websocketConnection");

    new SocketSingleton().instance();

    const options = io.mock.calls[io.mock.calls.length - 1][1];
    const offered = jest.fn();
    options.auth(offered);
    expect(offered).toHaveBeenCalledWith({token: "abc123"});

    // Read on every (re)connect: without a token, an empty payload.
    resetTokenForTests();
    options.auth(offered);
    expect(offered).toHaveBeenLastCalledWith({});
});
