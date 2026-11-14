import React from "react";
import fs from "fs";
import path from "path";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import i18n from "../i18n/config";
import {PANELS} from "../PanelShell/registry";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {hasShellSession} from "../PanelShell/hostApi";
import {PanelShellContext} from "../PanelShell/context";
import AssetsPage from "./index";
import {MAX_UPLOAD_BYTES, SOURCE_URL, humanSize} from "./assetsApi";

// The game assets page (ADR-005 S-final SF-4b-2): the Qt dialog
// AutonaGameAssetGuideWindow as /web/assets. In the desktop's shared view
// (GET /api/v1/shell-session answers 200, SF-5a-1; the access token is never
// read) it lists the upstream packs, installs them and imports a ZIP as jobs;
// in a browser (403) it only explains and links the source.

jest.setTimeout(20000);

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

const TOKEN = "tok-never-kept-1234";
const TICKET = "ticket-never-stored-5678";

const PACKS = {
    source: {page: SOURCE_URL, index: "https://example.invalid/assets.json"},
    index: {status: "ready", error: null, fetchedAt: 1},
    games: [
        {code: "ssbu", name: "Super Smash Bros. Ultimate", available: true,
         installed: true, packs: [
             {name: "base_files", size: 2 * 1024 * 1024, version: "2",
              installedVersion: "1", installed: true, updateAvailable: true,
              default: true},
             {name: "full", size: 100 * 1024 * 1024, version: "2",
              installedVersion: null, installed: false,
              updateAvailable: false, default: true},
             {name: "portrait", size: 5 * 1024 * 1024, version: "1",
              installedVersion: null, installed: false,
              updateAvailable: false, default: false}]},
        {code: "local", name: "Local only", available: false,
         installed: true, packs: []}],
};

function job(fields = {}) {
    return {
        revision: 0, status: "idle", operation: null, game: null,
        stage: null, pack: null, file: null, done: 0, total: 0,
        imported: [], skipped: [], skippedEntries: [],
        skippedEntriesMore: 0, failed: [], error: null, message: null,
        ...fields,
    };
}

let server;
let xhrs;

class FakeXHR {
    constructor() {
        this.upload = {};
        this.headers = {};
        this.aborted = false;
        xhrs.push(this);
    }

    open(method, url) {
        this.method = method;
        this.url = url;
    }

    setRequestHeader(name, value) {
        this.headers[name] = value;
    }

    send(body) {
        this.body = body;
    }

    abort() {
        this.aborted = true;
        this.onabort?.();
    }

    progress(loaded, total) {
        this.upload.onprogress?.({loaded, total, lengthComputable: true});
    }

    respond(status, body) {
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.onload?.();
    }
}

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
    return answer(status, {ok: true, data});
}

function refuse(status, code, reason) {
    return answer(status, {ok: false, error: {code, message: "no", reason}});
}

function freshServer() {
    return {
        token: ok(200, {token: TOKEN, available: true}),
        session: ok(200, {ticket: TICKET}),
        packs: ok(200, PACKS),
        job: ok(200, job()),
        install: null,
        importZip: null,
        cancel: ok(200, {cancelled: true}),
        revision: 0,
    };
}

function calls(method, pathname) {
    return global.fetch.mock.calls.filter(([url, init]) =>
        (init?.method ?? "GET") === method
        && new URL(url).pathname === pathname);
}

function body(call) {
    return JSON.parse(call[1].body);
}

function setJob(fields) {
    server.job = ok(200, job(fields));
}

beforeEach(() => {
    server = freshServer();
    xhrs = [];
    global.XMLHttpRequest = FakeXHR;
    AbortSignal.timeout = jest.fn(() => undefined);
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname} = new URL(url);
        if (method === "GET" && pathname === "/api/v1/access-token") {
            return server.token;
        }
        if (method === "GET" && pathname === "/api/v1/shell-session") {
            return server.session;
        }
        if (method === "GET" && pathname === "/api/v1/assets/packs") {
            return server.packs;
        }
        if (method === "GET" && pathname === "/api/v1/assets/job") {
            return server.job;
        }
        if (method === "POST" && server.gate
                && (pathname === "/api/v1/assets/packs:install"
                    || pathname === "/api/v1/assets:importZip")) {
            await server.gate;
        }
        if (method === "POST" && pathname === "/api/v1/assets/packs:install") {
            if (server.install) {
                return server.install;
            }
            server.revision += 1;
            setJob({revision: server.revision, status: "running",
                    operation: "install", game: "ssbu", stage: "download",
                    pack: "full", file: "ssbu.full.7z.001",
                    done: 50 * 1024 * 1024, total: 100 * 1024 * 1024});
            return ok(202, {accepted: true, operation: "install",
                            job: server.revision});
        }
        if (method === "POST" && pathname === "/api/v1/assets:importZip") {
            if (server.importZip) {
                return server.importZip;
            }
            server.revision += 1;
            setJob({revision: server.revision, status: "running",
                    operation: "importZip", stage: "copy", done: 3,
                    total: 10});
            return ok(202, {accepted: true, operation: "importZip",
                            job: server.revision});
        }
        if (method === "POST" && pathname === "/api/v1/assets/job:cancel") {
            return server.cancel;
        }
        if (method === "POST"
                && pathname === "/api/v1/assets/games-folder:reveal") {
            return ok(200, {});
        }
        if (method === "DELETE" && pathname.startsWith(
                "/api/v1/assets/uploads/")) {
            return ok(200, {removed: true});
        }
        return refuse(404, "NOT_FOUND");
    });
    sessionStorage.clear();
    localStorage.clear();
});

afterEach(() => {
    delete global.fetch;
    delete global.XMLHttpRequest;
    Object.defineProperty(document, "visibilityState", {
        configurable: true, value: "visible"});
    jest.restoreAllMocks();
});

async function settle() {
    await act(async () => {
        for (let i = 0; i < 10; i++) {
            await Promise.resolve();
        }
    });
}

async function renderPage() {
    const view = render(<AssetsPage/>);
    await settle();
    return view;
}

async function renderShell() {
    const view = await renderPage();
    await screen.findByText(i18n.t("assets_pick_game"));
    return view;
}

const socketHandlers = () =>
    require("../websocketConnection").default.instance().__handlers;

async function fire(name) {
    await act(async () => {
        for (const fn of [...(socketHandlers()[name] ?? [])]) {
            fn({});
        }
    });
    await settle();
}

async function pickGame(name = /Super Smash/) {
    const box = screen.getByRole("combobox", {name: i18n.t("assets_game")});
    fireEvent.keyDown(box, {key: "ArrowDown"});
    fireEvent.click(await screen.findByRole("option", {name}));
    await settle();
}

function zipFile(name = "game.zip", size = 1234) {
    const file = new File(["x"], name, {type: "application/zip"});
    Object.defineProperty(file, "size", {value: size});
    return file;
}

async function chooseZip(file) {
    const input = screen.getByLabelText(i18n.t("assets_zip_choose"),
                                        {selector: "input"});
    await act(async () => {
        fireEvent.change(input, {target: {files: [file]}});
    });
    await settle();
}

async function answerUpload(data) {
    await act(async () => {
        xhrs[xhrs.length - 1].respond(201, {ok: true, data});
    });
    await settle();
}

// ── the three states ───────────────────────────────────────────────────

test("it says it is checking until the probe answers", async () => {
    let release;
    global.fetch.mockImplementationOnce(() => new Promise((resolve) => {
        release = resolve;
    }));
    await renderPage();

    expect(screen.getByText(i18n.t("assets_checking"))).toBeTruthy();
    expect(calls("GET", "/api/v1/assets/packs")).toHaveLength(0);
    expect(calls("GET", "/api/v1/assets/job")).toHaveLength(0);

    await act(async () => release(refuse(403, "FORBIDDEN")));
    await settle();
    expect(screen.queryByText(i18n.t("assets_checking"))).toBeNull();
});

test("elsewhere it explains and links the source, reading nothing", async () => {
    server.session = refuse(403, "FORBIDDEN");
    await renderPage();

    await screen.findByText(i18n.t("assets_elsewhere_title"));
    expect(screen.getByText(i18n.t("assets_elsewhere"))).toBeTruthy();
    const link = screen.getByRole("link", {name: i18n.t("assets_open_source")});
    expect(link.getAttribute("href")).toBe(SOURCE_URL);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.queryByRole("button", {name: i18n.t("assets_download")}))
        .toBeNull();
    expect(screen.queryByRole("button", {name: i18n.t("assets_open_folder")}))
        .toBeNull();
    expect(screen.queryByRole("button", {name: i18n.t("assets_zip_choose")}))
        .toBeNull();
    // Nothing of the lists or the job is asked for.
    expect(calls("GET", "/api/v1/assets/packs")).toHaveLength(0);
    expect(calls("GET", "/api/v1/assets/job")).toHaveLength(0);
    expect(xhrs).toHaveLength(0);
});

test("a probe that fails says so and can be tried again", async () => {
    server.session = refuse(500, "INTERNAL");
    await renderPage();
    await screen.findByText(i18n.t("assets_failed"), {}, {timeout: 8000});

    server.session = ok(200, {ticket: TICKET});
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_retry")}));
    await screen.findByText(i18n.t("assets_pick_game"));
});

test("in the shared view the page reads no access token", async () => {
    await renderShell();

    // The probe is the shell session (SF-5a-1): the token is never asked.
    expect(calls("GET", "/api/v1/shell-session")).toHaveLength(1);
    expect(calls("GET", "/api/v1/access-token")).toHaveLength(0);
    // The ticket is hostApi's alone: nowhere the page shows or keeps.
    for (const secret of [TOKEN, TICKET]) {
        expect(document.body.textContent).not.toContain(secret);
        expect(JSON.stringify({...sessionStorage})).not.toContain(secret);
        expect(JSON.stringify({...localStorage})).not.toContain(secret);
        expect(window.location.href).not.toContain(secret);
    }
});

test("useShellAccess({withToken: false}) answers shell with no token", async () => {
    let seen;
    function Probe() {
        seen = useShellAccess({withToken: false});
        return null;
    }
    render(<Probe/>);
    await waitFor(() => expect(seen.access).toBe("shell"));

    expect(seen.token).toBe("");
    expect(seen.available).toBe(false);
    await expect(seen.regenerate()).rejects.toThrow();
    expect(calls("POST", "/api/v1/access-token:regenerate")).toHaveLength(0);
    // Its probe is the shell session; the token is never read (SF-5a-1).
    expect(calls("GET", "/api/v1/shell-session")).toHaveLength(1);
    expect(calls("GET", "/api/v1/access-token")).toHaveLength(0);
    expect(hasShellSession()).toBe(true);
});

test("useShellAccess({withToken: false}) is elsewhere on a 403, ticket dropped", async () => {
    server.session = refuse(403, "FORBIDDEN");
    let seen;
    function Probe() {
        seen = useShellAccess({withToken: false});
        return null;
    }
    render(<Probe/>);
    await waitFor(() => expect(seen.access).toBe("elsewhere"));

    expect(hasShellSession()).toBe(false);
    expect(calls("GET", "/api/v1/access-token")).toHaveLength(0);
});

test("useShellAccess() (the API page's) still reads the token", async () => {
    let seen;
    function Probe() {
        seen = useShellAccess();
        return null;
    }
    render(<Probe/>);
    await waitFor(() => expect(seen.access).toBe("shell"));

    expect(seen.token).toBe(TOKEN);
    expect(calls("GET", "/api/v1/access-token")).toHaveLength(1);
    expect(calls("GET", "/api/v1/shell-session")).toHaveLength(0);
});

// ── the pack list ──────────────────────────────────────────────────────

test("the pack list is read with a longer deadline than the default", async () => {
    await renderShell();

    expect(calls("GET", "/api/v1/assets/packs")).toHaveLength(1);
    expect(AbortSignal.timeout.mock.calls.some(([ms]) => ms >= 60000))
        .toBe(true);
});

test("a game's packs show their sizes and the defaults are checked", async () => {
    await renderShell();
    await pickGame();

    const group = screen.getByRole("group", {name: i18n.t("assets_packs")});
    const boxes = within(group).getAllByRole("checkbox");
    expect(boxes.map((box) => box.checked)).toEqual([true, true, false]);
    expect(group.textContent).toContain("100.0 MB".replace(".0", ""));
    expect(group.textContent).toContain(i18n.t("assets_pack_update"));
    // Only the games the index offers.
    fireEvent.keyDown(screen.getByRole("combobox"), {key: "ArrowDown"});
    expect(screen.queryByRole("option", {name: /Local only/})).toBeNull();
});

test("a failed index says so and keeps the list", async () => {
    server.packs = ok(200, {...PACKS, index: {
        status: "error", error: "fetch_failed", fetchedAt: 1}});
    await renderPage();

    const text = await screen.findByText(
        new RegExp(i18n.t("assets_list_failed").slice(0, 20)));
    expect(text.textContent).toContain(i18n.t("assets_index_fetch_failed"));
    await pickGame();
    expect(screen.getByRole("group", {name: i18n.t("assets_packs")}))
        .toBeTruthy();
});

test("reloading the list asks the server to refresh", async () => {
    await renderShell();
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("assets_reload_list")}));
    await settle();

    const urls = calls("GET", "/api/v1/assets/packs");
    expect(urls).toHaveLength(2);
    expect(urls[1][0]).toContain("refresh=true");
});

test("games_changed reads the list again", async () => {
    await renderShell();
    expect(calls("GET", "/api/v1/assets/packs")).toHaveLength(1);

    await fire("games_changed");

    expect(calls("GET", "/api/v1/assets/packs")).toHaveLength(2);
    expect(calls("GET", "/api/v1/assets/packs")[1][0]).not.toContain("refresh");
});

// ── install ────────────────────────────────────────────────────────────

test("install confirms with the size and the game, then starts the job", async () => {
    await renderShell();
    await pickGame();
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_download")}));

    const dialog = await screen.findByRole("dialog");
    // 2 MB + 100 MB: base_files and full, the defaults.
    expect(dialog.textContent).toContain("102 MB");
    expect(dialog.textContent).toContain("Super Smash Bros. Ultimate (ssbu)");
    expect(calls("POST", "/api/v1/assets/packs:install")).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_download")}));
    await settle();

    const [call] = calls("POST", "/api/v1/assets/packs:install");
    expect(body(call)).toEqual({game: "ssbu", packs: ["base_files", "full"],
                                confirm: true});
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The job is shown: stage / game / pack and the bytes.
    const progress = screen.getByRole("region",
                                      {name: i18n.t("assets_job_title")});
    expect(progress.textContent).toContain(i18n.t("assets_stage_download"));
    expect(progress.textContent).toContain("ssbu");
    expect(progress.textContent).toContain("full");
    expect(progress.textContent).toContain("50 MB / 100 MB");
});

test("cancelling the confirmation sends nothing", async () => {
    await renderShell();
    await pickGame();
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_download")}));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_cancel")}));
    await settle();

    expect(calls("POST", "/api/v1/assets/packs:install")).toHaveLength(0);
});

test("a refused install shows its reason in words", async () => {
    server.install = refuse(409, "NOT_READY", "index_unavailable");
    await renderShell();
    await pickGame();
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_download")}));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_download")}));
    await settle();

    expect(screen.getByText(i18n.t("assets_error_index_unavailable")))
        .toBeTruthy();
});

test("the progress is cancelled by the job's revision", async () => {
    setJob({revision: 7, status: "running", operation: "install",
            game: "ssbu", stage: "extract", pack: "full", done: 0, total: 0});
    await renderShell();

    // A job already running is adopted: its progress shows and the buttons
    // wait for it.
    const progress = await screen.findByRole("region",
                                             {name: i18n.t("assets_job_title")});
    expect(progress.textContent).toContain(i18n.t("assets_stage_extract"));
    expect(screen.getByRole("button", {name: i18n.t("assets_download")})
        .disabled).toBe(true);

    fireEvent.click(within(progress).getByRole(
        "button", {name: i18n.t("assets_job_cancel")}));
    await settle();

    expect(body(calls("POST", "/api/v1/assets/job:cancel")[0]))
        .toEqual({revision: 7});
});

// ── the job is followed ────────────────────────────────────────────────

test("a running job is read again about every second", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "download", pack: "full", done: 1, total: 4});
    await renderShell();
    const first = calls("GET", "/api/v1/assets/job").length;

    await waitFor(() => expect(calls("GET", "/api/v1/assets/job").length)
        .toBeGreaterThan(first), {timeout: 4000});
    // Its end shows the result and stops the polling.
    setJob({revision: 1, status: "done", operation: "install", game: "ssbu",
            imported: ["ssbu"]});
    await waitFor(() => expect(screen.getByText(
        i18n.t("assets_result_installed", {games: "ssbu"}))).toBeTruthy(),
    {timeout: 4000});
    const after = calls("GET", "/api/v1/assets/job").length;
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(calls("GET", "/api/v1/assets/job").length).toBe(after);
    // The finished job installed something: the list was read again.
    expect(calls("GET", "/api/v1/assets/packs").length).toBeGreaterThan(1);
});

test("the status notice reads the job at once", async () => {
    await renderShell();
    const before = calls("GET", "/api/v1/assets/job").length;

    await fire("status");

    expect(calls("GET", "/api/v1/assets/job").length).toBeGreaterThan(before);
});

test("a hidden page does not poll and reads again when shown", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "download", pack: "full", done: 1, total: 4});
    await renderShell();

    Object.defineProperty(document, "visibilityState", {
        configurable: true, value: "hidden"});
    await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle();
    const hidden = calls("GET", "/api/v1/assets/job").length;
    await new Promise((resolve) => setTimeout(resolve, 2300));
    expect(calls("GET", "/api/v1/assets/job").length).toBe(hidden);
    // The status notice does not read a hidden page's job either.
    await fire("status");
    expect(calls("GET", "/api/v1/assets/job").length).toBe(hidden);

    Object.defineProperty(document, "visibilityState", {
        configurable: true, value: "visible"});
    await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle();
    expect(calls("GET", "/api/v1/assets/job").length).toBeGreaterThan(hidden);
});

test("a newer job than the page's means the result is unknown", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "download", pack: "full", done: 1, total: 4});
    await renderShell();
    await screen.findByRole("region", {name: i18n.t("assets_job_title")});

    // The page's job ended and another began between two reads.
    setJob({revision: 2, status: "running", operation: "importZip",
            stage: "copy", done: 1, total: 3});
    await fire("status");

    expect(screen.getByText(i18n.t("assets_job_unknown"))).toBeTruthy();
    expect(screen.queryByRole("region", {name: i18n.t("assets_job_title")}))
        .toBeNull();
    // The other job still blocks a new one.
    expect(screen.getByRole("button", {name: i18n.t("assets_download")})
        .disabled).toBe(true);
});

test("a job that finished before the page opened is not shown", async () => {
    setJob({revision: 4, status: "done", operation: "install",
            imported: ["ssbu"]});
    await renderShell();

    expect(screen.queryByRole("region", {name: i18n.t("assets_job_title")}))
        .toBeNull();
    expect(screen.queryByText(i18n.t("assets_job_unknown"))).toBeNull();
});

// ── results ────────────────────────────────────────────────────────────

async function startImportedJob() {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-1", games: ["ssbu"], conflicts: [],
                        expiresInSec: 900});
}

test("skipped entries are folded away, with the count that did not fit", async () => {
    await startImportedJob();
    setJob({revision: 1, status: "done", operation: "importZip",
            imported: ["ssbu"], skipped: ["old"],
            skippedEntries: ["a/CON.png", "b/x:y.png"],
            skippedEntriesMore: 5});
    await fire("status");

    expect(screen.getByText(i18n.t("assets_result_imported",
                                   {games: "ssbu"}))).toBeTruthy();
    expect(screen.getByText(i18n.t("assets_result_skipped",
                                   {games: "old"}))).toBeTruthy();
    // Folded: the names are not in the page until asked.
    expect(screen.queryByText("a/CON.png")).toBeNull();
    const label = i18n.t("assets_result_skipped_entries", {count: 7});
    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("assets_details_show", {label})}));
    expect(screen.getByText("a/CON.png")).toBeTruthy();
    expect(screen.getByText("b/x:y.png")).toBeTruthy();
    expect(screen.getByText(i18n.t("assets_result_skipped_more",
                                   {count: 5}))).toBeTruthy();
});

test("an error job shows the translated reason, the English detail folded", async () => {
    await startImportedJob();
    setJob({revision: 1, status: "error", operation: "importZip",
            error: "disk_full", message: "C:\\secret\\path: no space"});
    await fire("status");

    expect(screen.getByText(i18n.t("assets_error_disk_full"))).toBeTruthy();
    expect(document.body.textContent).not.toContain("C:\\secret");
    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("assets_details_show",
                     {label: i18n.t("assets_details")})}));
    expect(document.body.textContent).toContain("C:\\secret\\path: no space");
});

test("a cancelled job and a job that imported nothing say so", async () => {
    await startImportedJob();
    setJob({revision: 1, status: "cancelled", operation: "importZip"});
    await fire("status");
    expect(screen.getByText(i18n.t("assets_result_cancelled"))).toBeTruthy();

    setJob({revision: 1, status: "done", operation: "importZip"});
    await fire("status");
    expect(screen.getByText(i18n.t("assets_nothing_imported"))).toBeTruthy();
});

test("a game that could not be copied is listed with its detail folded", async () => {
    await startImportedJob();
    setJob({revision: 1, status: "done", operation: "importZip",
            imported: [], failed: [{name: "ssbu", message: "boom"}]});
    await fire("status");

    expect(screen.getByText(i18n.t("assets_result_failed_item",
                                   {game: "ssbu"}))).toBeTruthy();
    expect(screen.queryByText("boom")).toBeNull();
});

// ── the ZIP upload ─────────────────────────────────────────────────────

test("a ZIP is sent as the body with its name in a header and shows progress", async () => {
    await renderShell();
    const file = zipFile("ゲーム game.zip", 4096);
    await chooseZip(file);

    expect(xhrs).toHaveLength(1);
    const xhr = xhrs[0];
    expect(xhr.method).toBe("POST");
    expect(new URL(xhr.url).pathname).toBe("/api/v1/assets/uploads");
    expect(xhr.body).toBe(file);
    expect(xhr.headers["X-Autona-File-Name"]).toBe(
        encodeURIComponent("ゲーム game.zip"));
    expect(Object.keys(xhr.headers)).toEqual(["X-Autona-File-Name"]);

    await act(async () => xhr.progress(1024, 4096));
    const bar = screen.getByRole("progressbar", {
        name: i18n.t("assets_upload_progress")});
    expect(bar.getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getAllByText(/1 KB \/ 4 KB/).length).toBeGreaterThan(0);
});

test("an upload with no conflicts goes straight to the import job", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-1", games: ["ssbu"], conflicts: [],
                        expiresInSec: 900});

    expect(screen.queryByRole("dialog")).toBeNull();
    const [call] = calls("POST", "/api/v1/assets:importZip");
    expect(body(call)).toEqual({upload: "id-1"});
    expect(screen.getByRole("region", {name: i18n.t("assets_job_title")}))
        .toBeTruthy();
    // The job took the file: the page does not drop it.
    expect(calls("DELETE", "/api/v1/assets/uploads/id-1")).toHaveLength(0);
});

test("conflicts are asked in one dialog, with the games to replace checked", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-2", games: ["ssbu", "melee", "brawl"],
                        conflicts: ["ssbu", "melee"], expiresInSec: 900});

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("ssbu, melee, brawl");
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes.map((box) => box.checked)).toEqual([false, false]);
    fireEvent.click(within(dialog).getByRole("checkbox", {name: "melee"}));
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_import")}));
    await settle();

    const [call] = calls("POST", "/api/v1/assets:importZip");
    expect(body(call)).toEqual({upload: "id-2", overwrite: ["melee"],
                                confirm: true});
});

test("declining every game of a ZIP that is all installed imports nothing", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-3", games: ["ssbu"],
                        conflicts: ["ssbu"], expiresInSec: 900});

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_import")}));
    await settle();

    expect(calls("POST", "/api/v1/assets:importZip")).toHaveLength(0);
    expect(calls("DELETE", "/api/v1/assets/uploads/id-3")).toHaveLength(1);
    expect(screen.getByText(i18n.t("assets_nothing_imported"))).toBeTruthy();
});

test("cancelling the overwrite dialog drops the upload", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-4", games: ["ssbu"],
                        conflicts: ["ssbu"], expiresInSec: 900});

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button",
                                             {name: i18n.t("assets_cancel")}));
    await settle();

    expect(calls("POST", "/api/v1/assets:importZip")).toHaveLength(0);
    expect(calls("DELETE", "/api/v1/assets/uploads/id-4")).toHaveLength(1);
});

test("an import that is refused to start drops the upload too", async () => {
    server.importZip = refuse(409, "CONFLICT", "busy");
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-5", games: ["ssbu"], conflicts: [],
                        expiresInSec: 900});

    expect(screen.getByText(i18n.t("assets_error_busy"))).toBeTruthy();
    expect(calls("DELETE", "/api/v1/assets/uploads/id-5")).toHaveLength(1);
});

test("stopping the upload aborts the request and says so", async () => {
    await renderShell();
    await chooseZip(zipFile());
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("assets_upload_cancel")}));
    await settle();

    expect(xhrs[0].aborted).toBe(true);
    expect(screen.getByText(i18n.t("assets_upload_cancelled"))).toBeTruthy();
    expect(screen.queryByRole("progressbar", {
        name: i18n.t("assets_upload_progress")})).toBeNull();
    // No id was ever given: there is nothing to drop.
    expect(calls("DELETE", "/api/v1/assets/uploads/x")).toHaveLength(0);
    expect(global.fetch.mock.calls.filter(
        ([, init]) => init?.method === "DELETE")).toHaveLength(0);
});

test("leaving the page mid-upload aborts it", async () => {
    const view = await renderShell();
    await chooseZip(zipFile());

    view.unmount();

    expect(xhrs[0].aborted).toBe(true);
});

test("leaving the page with an upload held drops it", async () => {
    const view = await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-6", games: ["ssbu"],
                        conflicts: ["ssbu"], expiresInSec: 900});
    await screen.findByRole("dialog");

    view.unmount();

    expect(calls("DELETE", "/api/v1/assets/uploads/id-6")).toHaveLength(1);
});

test("a ZIP over the limit is refused before anything is sent", async () => {
    await renderShell();
    await chooseZip(zipFile("huge.zip", MAX_UPLOAD_BYTES + 1));

    expect(xhrs).toHaveLength(0);
    expect(screen.getByText(i18n.t("assets_zip_too_large", {
        size: humanSize(MAX_UPLOAD_BYTES + 1),
        limit: humanSize(MAX_UPLOAD_BYTES)}))).toBeTruthy();
});

test("a ZIP at the limit is sent", async () => {
    await renderShell();
    await chooseZip(zipFile("exact.zip", MAX_UPLOAD_BYTES));

    expect(xhrs).toHaveLength(1);
});

test("the server's refusal of an upload is shown by its reason", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await act(async () => {
        xhrs[0].respond(413, {ok: false, error: {
            code: "PAYLOAD_TOO_LARGE", message: "x", reason: "too_large"}});
    });
    await settle();

    expect(screen.getByText(i18n.t("assets_error_too_large"))).toBeTruthy();
});

test("a ZIP the server cannot read says so", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await act(async () => {
        xhrs[0].respond(400, {ok: false, error: {
            code: "INVALID_ARGUMENT", message: "x", reason: "not_a_zip"}});
    });
    await settle();

    expect(screen.getByText(i18n.t("assets_error_not_a_zip"))).toBeTruthy();
});

test("the folder import is gone and the page says to make a ZIP", async () => {
    await renderShell();

    expect(screen.queryByRole("button", {name: /import from folder/i})).toBeNull();
    expect(screen.getByText(i18n.t("assets_zip_note"))).toBeTruthy();
    expect(i18n.t("assets_zip_note")).toContain("ZIP");
});

// ── the rest ───────────────────────────────────────────────────────────

test("the games folder is opened through the reveal route", async () => {
    await renderShell();
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("assets_open_folder")}));
    await settle();

    const [call] = calls("POST", "/api/v1/assets/games-folder:reveal");
    expect(body(call)).toEqual({});
});

// ── review fixes ───────────────────────────────────────────────────────

test("an install counts bytes in every stage, an import counts entries", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "copy", pack: "full",
            done: 2 * 1024 * 1024, total: 4 * 1024 * 1024});
    const view = await renderShell();
    const region = await screen.findByRole("region",
                                           {name: i18n.t("assets_job_title")});
    expect(region.textContent).toContain("2 MB / 4 MB");
    view.unmount();

    sessionStorage.clear();
    setJob({revision: 2, status: "running", operation: "importZip",
            stage: "copy", done: 3, total: 10});
    await renderShell();
    const next = await screen.findByRole("region",
                                         {name: i18n.t("assets_job_title")});
    expect(next.textContent).toContain(": 3 / 10");
});

test("the file being fetched is shown", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "download", pack: "full",
            file: "ssbu.full.7z.001", done: 1, total: 4});
    await renderShell();
    const region = await screen.findByRole("region",
                                           {name: i18n.t("assets_job_title")});
    expect(region.textContent).toContain("ssbu.full.7z.001");
});

test("a page that started a job shows its result when it is opened again", async () => {
    sessionStorage.setItem("autona.assets.job", "4");
    setJob({revision: 4, status: "done", operation: "install",
            imported: ["ssbu"]});
    await renderShell();
    await screen.findByRole("region", {name: i18n.t("assets_job_title")});
    expect(screen.getByText(i18n.t("assets_result_installed",
                                   {games: "ssbu"}))).toBeTruthy();
});

test("a job another page started is not shown as a result", async () => {
    sessionStorage.setItem("autona.assets.job", "3");
    setJob({revision: 4, status: "done", operation: "install",
            imported: ["ssbu"]});
    await renderShell();
    expect(screen.queryByRole("region", {name: i18n.t("assets_job_title")}))
        .toBeNull();
});

test("a storage that throws does not break the page", async () => {
    const get = jest.spyOn(Storage.prototype, "getItem")
        .mockImplementation(() => {
            throw new Error("denied");
        });
    const set = jest.spyOn(Storage.prototype, "setItem")
        .mockImplementation(() => {
            throw new Error("denied");
        });
    await renderShell();
    get.mockRestore();
    set.mockRestore();
    expect(screen.getByText(i18n.t("assets_pick_game"))).toBeTruthy();
});

test("both buttons wait from the start request until the job is read", async () => {
    let release;
    server.gate = new Promise((resolve) => {
        release = resolve;
    });
    await renderShell();
    await pickGame();
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_download")}));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getAllByRole("button").pop());
    await settle();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect(screen.getByRole("button", {name: i18n.t("assets_download")})
        .disabled).toBe(true);
    expect(screen.getByRole("button", {name: i18n.t("assets_zip_choose")})
        .disabled).toBe(true);
    await act(async () => release());
    await settle();
    await screen.findByRole("region", {name: i18n.t("assets_job_title")});
    expect(calls("POST", "/api/v1/assets/packs:install")).toHaveLength(1);
});

test("the ZIP and the download wait for each other while a file is sent", async () => {
    await renderShell();
    await pickGame();
    await chooseZip(zipFile());

    expect(screen.getByRole("button", {name: i18n.t("assets_download")})
        .disabled).toBe(true);
    expect(screen.getByRole("button", {name: i18n.t("assets_zip_choose")})
        .disabled).toBe(true);
});

test("an upload the job took while the import request is out is not dropped", async () => {
    let release;
    server.gate = new Promise((resolve) => {
        release = resolve;
    });
    const view = await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-7", games: ["ssbu"], conflicts: [],
                        expiresInSec: 900});
    // The request is out: leaving now must not delete the file.
    view.unmount();
    await act(async () => release());
    expect(calls("DELETE", "/api/v1/assets/uploads/id-7")).toHaveLength(0);
});

test("a click outside or Esc does not discard the held upload", async () => {
    await renderShell();
    await chooseZip(zipFile());
    await answerUpload({upload: "id-8", games: ["ssbu"],
                        conflicts: ["ssbu"], expiresInSec: 900});
    const dialog = await screen.findByRole("dialog");

    fireEvent.keyDown(dialog, {key: "Escape"});
    await settle();
    fireEvent.click(document.querySelector(".MuiBackdrop-root"));
    await settle();

    expect(screen.queryByRole("dialog")).not.toBeNull();
    expect(calls("DELETE", "/api/v1/assets/uploads/id-8")).toHaveLength(0);
});

test("the overwrite dialog closes when the upload expires", async () => {
    await renderShell();
    await chooseZip(zipFile());
    jest.useFakeTimers();
    try {
        await answerUpload({upload: "id-9", games: ["ssbu"],
                            conflicts: ["ssbu"], expiresInSec: 30});
        expect(screen.queryByRole("dialog")).not.toBeNull();

        await act(async () => {
            jest.advanceTimersByTime(31 * 1000);
        });
        await settle();

        expect(screen.getByText(i18n.t("assets_upload_expired"))).toBeTruthy();
        expect(calls("DELETE", "/api/v1/assets/uploads/id-9")).toHaveLength(1);
    } finally {
        jest.useRealTimers();
    }
});

test("a failed read of the job offers a retry", async () => {
    server.job = refuse(500, "INTERNAL");
    await renderShell();
    await screen.findByText(i18n.t("assets_job_read_failed"));
    server.job = ok(200, job());
    const before = calls("GET", "/api/v1/assets/job").length;
    fireEvent.click(screen.getByRole("button", {name: i18n.t("assets_retry")}));
    await settle();

    expect(calls("GET", "/api/v1/assets/job").length).toBeGreaterThan(before);
    expect(screen.queryByText(i18n.t("assets_job_read_failed"))).toBeNull();
});

test("reads of the job do not overlap", async () => {
    setJob({revision: 1, status: "running", operation: "install",
            game: "ssbu", stage: "download", pack: "full", done: 1, total: 4});
    let open = 0;
    let most = 0;
    const base = global.fetch;
    global.fetch = jest.fn(async (url, init) => {
        const slow = new URL(url).pathname === "/api/v1/assets/job"
            && (init?.method ?? "GET") === "GET";
        if (slow) {
            open += 1;
            most = Math.max(most, open);
            await new Promise((resolve) => setTimeout(resolve, 300));
        }
        try {
            return await base(url, init);
        } finally {
            if (slow) {
                open -= 1;
            }
        }
    });
    await renderShell();
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 2500));
    });
    expect(most).toBe(1);
});

test("the upload progress bar has a fixed label and the value in aria-valuenow", async () => {
    await renderShell();
    await chooseZip(zipFile("a-long-name.zip", 4096));
    await act(async () => xhrs[0].progress(2048, 4096));
    const bar = screen.getByRole("progressbar", {
        name: i18n.t("assets_upload_progress")});
    expect(bar.getAttribute("aria-valuenow")).toBe("50");
});

test("the page is registered under assets", () => {
    expect(PANELS.assets).toBe(AssetsPage);
});

test("nothing renders HTML", () => {
    const dir = __dirname;
    for (const name of fs.readdirSync(dir)) {
        if (/\.(tsx?|jsx?)$/.test(name) && !name.endsWith(".test.js")) {
            const text = fs.readFileSync(path.join(dir, name), "utf8");
            expect(text).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
        }
    }
});

test("every english key of the page has a japanese one", () => {
    const en = require("../i18n/locales/en.json");
    const ja = require("../i18n/locales/ja.json");
    // en has _one and _other, ja only _other (i18next plural suffixes).
    const base = (key) => key.replace(/_(one|other)$/, "");
    const bases = (dict) => [...new Set(Object.keys(dict)
        .filter((key) => key.startsWith("assets_")).map(base))].sort();
    const keys = bases(en);
    expect(keys.length).toBeGreaterThan(50);
    for (const key of Object.keys(en).filter((k) => k.startsWith("assets_"))) {
        expect(ja[key] || ja[`${base(key)}_other`]).toBeTruthy();
    }
    expect(bases(ja)).toEqual(keys);
});

// ── "open in the default browser" (owner, 2026-11-14) ─────────────────────

test("the page opens itself in the default browser from the app", async () => {
    window.open = jest.fn();
    render(
        <PanelShellContext.Provider
            value={{panel: "assets", desktop: true, language: "en"}}>
            <AssetsPage/>
        </PanelShellContext.Provider>,
    );
    await settle();

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("guide_open_in_browser")}));

    expect(window.open).toHaveBeenCalledWith(
        `${window.location.origin}/web/assets?lng=en`, "_blank", "noopener");
});

test("the page in a browser has no browser to open", async () => {
    server.session = refuse(403, "FORBIDDEN");
    await renderPage();

    expect(screen.queryByRole("button", {
        name: i18n.t("guide_open_in_browser")})).toBeNull();
});
