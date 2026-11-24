import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import i18n from "../i18n/config";
import {BACKEND_PORT} from "../env";
import DesignPane, {
    HEALTH_PATH,
    PREVIEW_SANDBOX,
    QUARANTINE_POLL_MS,
    SAMPLE_NAMES,
} from "./DesignPane";
import {
    COUNT_MAX,
    EMPTY_REPORT,
    ERRORS_MAX,
    MESSAGE_MAX,
    READY_TIMEOUT_MS,
    addMessage,
    parseLayoutMessage,
} from "./LayoutCheck";

// The layout previews (ADR-005 S-final SF-8a): the frames load from the
// server's read-only preview listener -- another origin, the port GET
// /api/v1/health names -- sandboxed with their origin kept; no port, no frame
// (never one on this page's origin); a frame found holding the keyboard focus
// is taken out with a note. The check of a layout (ADR-008 D3): the page takes
// a `message` only from its own frames and the preview origin, of the one shape
// autona.js posts, and shows it as text in the check panel.

jest.setTimeout(20000);

// The page's Socket.IO: the health is asked again on each (re)connect.
const mockSocket = {
    listeners: new Map(),
    on(event, handler) {
        this.listeners.set(event, [...(this.listeners.get(event) ?? []), handler]);
    },
    off(event, handler) {
        this.listeners.set(event, (this.listeners.get(event) ?? []).filter(
            (known) => known !== handler));
    },
    fire(event) {
        (this.listeners.get(event) ?? []).forEach((handler) => handler());
    },
};

jest.mock("../websocketConnection", () => ({
    __esModule: true, default: {instance: () => mockSocket}}));

const PREVIEW_PORT = 52345;
const SIMPLE = {id: "scoreboard_simple", kind: "scoreboard", displayName: "Simple",
                entry: "index.html"};
const PLAIN = {id: "scoreboard_plain", kind: "scoreboard", displayName: "Plain",
               entry: "index.html"};
// A design made from the layout kit: its settings.json declares the contract.
const KIT = {id: "scoreboard_mine", kind: "scoreboard", displayName: "Mine",
             entry: "index.html", contract: 1};
const POPUP = {src: "/layout/winner_popup/index.html#preview=winner",
               title: "Popup preview"};

// What GET /api/v1/health answers: [status, body], or a promise.
let health;
// window.focus(): jsdom has none (it says so on the console).
let focusWindow;

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

function Pane({design = SIMPLE, overlay = null, reloadKey, ...rest}) {
    return (
        <div>
            <input aria-label="typed into"/>
            <DesignPane designs={[SIMPLE, PLAIN, KIT]} design={design}
                        onSelect={() => undefined} overlay={overlay}
                        reloadKey={reloadKey} {...rest}/>
        </div>
    );
}

function frames() {
    return Array.from(document.querySelectorAll("iframe"));
}

function preview() {
    return screen.queryByTitle(i18n.t("commentary_preview"));
}

beforeEach(() => {
    mockSocket.listeners.clear();
    jest.useFakeTimers();
    focusWindow = jest.spyOn(window, "focus").mockImplementation(() => undefined);
    health = [200, {ok: true, data: {status: "ok", previewPort: PREVIEW_PORT}}];
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url) => {
        if (new URL(url).pathname === HEALTH_PATH) {
            if (health instanceof Promise) {
                return health;
            }
            return answer(...health);
        }
        return answer(404, {ok: false});
    });
});

afterEach(() => {
    focusWindow.mockRestore();
    jest.useRealTimers();
    delete global.fetch;
});

test("the frames load from the preview origin, sandboxed with it kept", async () => {
    render(<Pane overlay={POPUP}/>);
    await settle();

    expect(PREVIEW_SANDBOX).toBe("allow-scripts allow-same-origin");
    expect(frames()).toHaveLength(2);
    const [design, popup] = frames();
    expect(design.getAttribute("src")).toBe(
        `http://localhost:${PREVIEW_PORT}/layout/scoreboard_simple/index.html`);
    expect(popup.getAttribute("src")).toBe(
        `http://localhost:${PREVIEW_PORT}/layout/winner_popup/index.html#preview=winner`);
    frames().forEach((frame) => {
        // Another origin than the page's and the server's own.
        const port = new URL(frame.getAttribute("src")).port;
        expect(port).not.toBe(String(BACKEND_PORT));
        expect(port).not.toBe(window.location.port);
        expect(frame.getAttribute("sandbox")).toBe(PREVIEW_SANDBOX);
        expect(frame.getAttribute("tabindex")).toBe("-1");
    });
});

test.each([
    ["no preview port", () => [200, {ok: true, data: {previewPort: null}}]],
    ["not a port", () => [200, {ok: true, data: {previewPort: "52345"}}]],
    ["this server's own port", () => [200, {ok: true, data: {
        previewPort: Number(BACKEND_PORT)}}]],
    ["a refused health", () => [404, {ok: false}]],
])("%s: no frame, and the note", async (_, reply) => {
    health = reply();
    render(<Pane overlay={POPUP}/>);
    await settle();

    expect(frames()).toEqual([]);
    expect(screen.getByRole("status")).toHaveTextContent(
        i18n.t("commentary_preview_unavailable"));
});

test("no frame while the server is asked, none on this origin", async () => {
    health = new Promise(() => {});
    render(<Pane overlay={POPUP}/>);
    await settle();

    expect(frames()).toEqual([]);
    expect(screen.queryByRole("status")).toBeNull();
});

test("a frame holding the focus as the window loses it is taken out", async () => {
    render(<Pane overlay={POPUP}/>);
    await settle();
    const typed = screen.getByRole("textbox", {name: "typed into"});
    typed.focus();

    preview().focus();
    expect(document.activeElement).toBe(preview());
    fireEvent.blur(window);

    expect(frames()).toEqual([]);
    expect(screen.getByRole("status")).toHaveTextContent(
        i18n.t("commentary_preview_quarantined"));
    // What had the focus here has it again.
    expect(focusWindow).toHaveBeenCalled();
    expect(document.activeElement).toBe(typed);
});

test("the poll takes out a frame that took the focus unseen", async () => {
    render(<Pane/>);
    await settle();

    preview().focus();
    await settle(QUARANTINE_POLL_MS - 1);
    expect(frames()).toHaveLength(1);
    await settle(1);

    expect(frames()).toEqual([]);
    expect(screen.getByRole("status")).toHaveTextContent(
        i18n.t("commentary_preview_quarantined"));
});

test("the window losing the focus elsewhere takes nothing out", async () => {
    render(<Pane overlay={POPUP}/>);
    await settle();
    screen.getByRole("textbox", {name: "typed into"}).focus();

    fireEvent.blur(window);
    await settle(QUARANTINE_POLL_MS * 4);

    expect(frames()).toHaveLength(2);
    expect(screen.queryByRole("status")).toBeNull();
});

test("another design, or a reload, shows the frames again", async () => {
    const {rerender} = render(<Pane reloadKey={1}/>);
    await settle();
    preview().focus();
    fireEvent.blur(window);
    expect(frames()).toEqual([]);

    rerender(<Pane design={PLAIN} reloadKey={1}/>);
    expect(preview()).toHaveAttribute(
        "src", `http://localhost:${PREVIEW_PORT}/layout/scoreboard_plain/index.html`);

    preview().focus();
    await settle(QUARANTINE_POLL_MS);
    expect(frames()).toEqual([]);
    rerender(<Pane design={PLAIN} reloadKey={2}/>);
    expect(frames()).toHaveLength(1);
});

test("coming back to a design taken out shows it again (A, B, A)", async () => {
    const {rerender} = render(<Pane/>);
    await settle();
    preview().focus();
    fireEvent.blur(window);
    expect(frames()).toEqual([]);

    rerender(<Pane design={PLAIN}/>);
    await settle();
    expect(frames()).toHaveLength(1);
    rerender(<Pane design={SIMPLE}/>);
    await settle();

    expect(preview()).toHaveAttribute(
        "src", `http://localhost:${PREVIEW_PORT}/layout/scoreboard_simple/index.html`);
    expect(screen.queryByRole("status")).toBeNull();
    // And watched again.
    preview().focus();
    fireEvent.blur(window);
    expect(frames()).toEqual([]);
});

test("the preview port is asked again on a reconnect", async () => {
    render(<Pane overlay={POPUP}/>);
    await settle();
    expect(preview().getAttribute("src")).toContain(`:${PREVIEW_PORT}/`);

    // The app restarted: its preview listener is on another port.
    health = [200, {ok: true, data: {status: "ok", previewPort: 52346}}];
    mockSocket.fire("connect");
    await settle();

    expect(frames().map((frame) => new URL(frame.getAttribute("src")).port))
        .toEqual(["52346", "52346"]);
});

test("a health that failed is asked again on a reconnect", async () => {
    health = [503, {ok: false}];
    render(<Pane/>);
    // Its retries (getWithRetry), then the note.
    for (let i = 0; i < 4; i++) {
        await settle(2000);
    }
    expect(frames()).toEqual([]);
    expect(screen.getByRole("status")).toHaveTextContent(
        i18n.t("commentary_preview_unavailable"));

    health = [200, {ok: true, data: {status: "ok", previewPort: PREVIEW_PORT}}];
    mockSocket.fire("connect");
    await settle();

    expect(frames()).toHaveLength(1);
    expect(screen.queryByRole("status")).toBeNull();
});

test("only the latest answer of the health counts", async () => {
    let answerFirst;
    health = new Promise((resolve) => {
        answerFirst = resolve;
    });
    render(<Pane/>);
    await settle();

    health = [200, {ok: true, data: {status: "ok", previewPort: 52346}}];
    mockSocket.fire("connect");
    await settle();
    answerFirst(answer(200, {ok: true, data: {previewPort: PREVIEW_PORT}}));
    await settle();

    expect(new URL(preview().getAttribute("src")).port).toBe("52346");
});

test("a preview port lost and found again is not another design", async () => {
    render(<Pane/>);
    await settle();
    preview().focus();
    fireEvent.blur(window);
    expect(frames()).toEqual([]);

    health = [404, {ok: false}];
    mockSocket.fire("connect");
    await settle();
    health = [200, {ok: true, data: {status: "ok", previewPort: PREVIEW_PORT}}];
    mockSocket.fire("connect");
    await settle();

    expect(frames()).toEqual([]);
    expect(screen.getByRole("status")).toHaveTextContent(
        i18n.t("commentary_preview_quarantined"));
});

test("the reconnect listener goes with the pane", async () => {
    const {unmount} = render(<Pane/>);
    await settle();
    expect(mockSocket.listeners.get("connect")).toHaveLength(1);

    unmount();
    expect(mockSocket.listeners.get("connect")).toEqual([]);
});

test("the page listens to messages only while it shows a preview", async () => {
    const listen = jest.spyOn(window, "addEventListener");
    const unlisten = jest.spyOn(window, "removeEventListener");
    health = new Promise(() => {});
    const {unmount} = render(<Pane overlay={POPUP}/>);
    await settle();
    // No preview origin yet: no frame, no listener.
    expect(listen.mock.calls.map(([type]) => type)).not.toContain("message");
    unmount();

    health = [200, {ok: true, data: {status: "ok", previewPort: PREVIEW_PORT}}];
    const view = render(<Pane overlay={POPUP}/>);
    await settle();
    const added = listen.mock.calls.filter(([type]) => type === "message");
    expect(added).toHaveLength(1);
    view.unmount();
    expect(unlisten.mock.calls.filter(([type]) => type === "message")
        .map(([, handler]) => handler)).toContain(added[0][1]);
    listen.mockRestore();
    unlisten.mockRestore();
});

// ── the check of a layout (ADR-008 D3) ───────────────────────────────────

const PREVIEW = `http://localhost:${PREVIEW_PORT}`;
const READY = {type: "autona:ready", contract: 1, kind: "ready", message: "",
               source: "/layout/scoreboard_simple/index.html", line: null,
               column: null};

function failure(extra = {}) {
    return {type: "autona:error", contract: 1, kind: "error",
            message: "Uncaught ReferenceError: foo is not defined",
            source: "/layout/scoreboard_simple/index.js", line: 12, column: 3,
            ...extra};
}

function post(source, data, origin = PREVIEW) {
    act(() => {
        window.dispatchEvent(new MessageEvent("message", {data, origin, source}));
    });
}

function panel() {
    return screen.queryByRole("region", {name: i18n.t("layout_check_title")});
}

function shownErrors() {
    return screen.queryAllByTestId("layout-check-error");
}

test("an error the design's frame posts is shown as text, with a hint", async () => {
    render(<Pane/>);
    await settle();
    expect(panel()).toBeNull();

    post(preview().contentWindow, failure({
        message: "<img src=x onerror=alert(1)> is not defined"}));

    expect(panel()).not.toBeNull();
    const [shown] = shownErrors();
    expect(shown).toHaveTextContent(
        "Error: <img src=x onerror=alert(1)> is not defined");
    expect(shown).toHaveTextContent(
        "At /layout/scoreboard_simple/index.js, line 12, column 3");
    expect(shown).toHaveTextContent(i18n.t("layout_check_hint_not_defined"));
    expect(within(panel()).queryByRole("img")).toBeNull();
    expect(panel()).toHaveTextContent("Simple (overlay contract 1)");
});

test("the Board's popup frame is heard too, but its ready is not the design's", async () => {
    render(<Pane overlay={POPUP}/>);
    await settle();
    const popup = screen.getByTitle(POPUP.title).contentWindow;

    post(popup, READY);
    expect(panel()).toBeNull();
    post(popup, failure({message: "popup broke"}));
    expect(shownErrors()).toHaveLength(1);
    expect(shownErrors()[0]).toHaveTextContent("popup broke");
});

test("messages from another frame, a nested one, another origin or of another shape are dropped", async () => {
    render(<Pane/>);
    await settle();
    const own = preview().contentWindow;
    const stray = document.createElement("iframe");
    document.body.appendChild(stray);
    // A frame inside the preview's frame (its page not loaded here).
    const inner = preview().contentDocument;
    if (!inner.documentElement) {
        inner.appendChild(inner.createElement("html"));
    }
    const nested = inner.createElement("iframe");
    inner.documentElement.appendChild(nested);

    post(stray.contentWindow, failure());
    post(nested.contentWindow, failure());
    post(null, failure());
    post(window, failure());
    post(own, failure(), `http://localhost:${BACKEND_PORT}`);
    post(own, failure(), "null");
    const withoutLine = failure();
    delete withoutLine.line;
    [
        withoutLine,
        {...failure(), extra: "x"},
        failure({line: "12"}),
        failure({line: 0}),
        failure({message: 42}),
        failure({type: "autona:navigate"}),
        failure({kind: "click"}),
        failure({type: "autona:ready"}),
        failure({contract: "1"}),
        [failure()],
        "autona:error",
        null,
    ].forEach((data) => post(own, data));

    try {
        expect(nested.contentWindow).not.toBeNull();
        expect(panel()).toBeNull();
        // What is right is heard.
        post(own, failure());
        expect(shownErrors()).toHaveLength(1);
    } finally {
        stray.remove();
    }
});

test("strings are cut, repeats counted and the list capped", async () => {
    render(<Pane/>);
    await settle();
    const own = preview().contentWindow;

    for (let i = 0; i < ERRORS_MAX + 5; i++) {
        post(own, failure({message: `${i}:${"x".repeat(2000)}`,
                           source: "s".repeat(1000)}));
    }
    post(own, failure({message: `0:${"x".repeat(2000)}`,
                       source: "s".repeat(1000)}));

    const shown = shownErrors();
    expect(shown).toHaveLength(ERRORS_MAX);
    const first = within(shown[0]).getByText(/^Error: 0:/).textContent;
    expect(first).toBe(`Error: 0:${"x".repeat(MESSAGE_MAX - 3)}…`);
    expect(shown[0]).toHaveTextContent("2 times");
    expect(panel()).toHaveTextContent(
        i18n.t("layout_check_dropped", {count: 5}));
});

test("another design, a reload or the sample empties the panel", async () => {
    const {rerender} = render(<Pane/>);
    await settle();
    post(preview().contentWindow, failure());
    expect(shownErrors()).toHaveLength(1);

    rerender(<Pane design={PLAIN}/>);
    expect(panel()).toBeNull();
    post(preview().contentWindow, failure());
    expect(shownErrors()).toHaveLength(1);
    rerender(<Pane design={PLAIN} reloadKey={1}/>);
    expect(panel()).toBeNull();
});

test("copy for an AI writes the Japanese report in the click", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    render(<Pane/>);
    await settle();
    post(preview().contentWindow, failure());
    post(preview().contentWindow, failure({
        kind: "onUpdate",
        message: "TypeError: Cannot read properties of undefined (reading 'name')",
        source: "", line: null, column: null}));

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_check_copy")}));

    // Written in the click, before any await.
    expect(writeText).toHaveBeenCalledWith([
        "Autona のプレビューで、オーバーレイ「Simple」（layout/scoreboard_simple/index.html・オーバーレイ契約 1）に次の問題が出ています。原因を直して、変更したファイルの全文を返してください。",
        "",
        "[1] エラー: Uncaught ReferenceError: foo is not defined",
        "    場所: /layout/scoreboard_simple/index.js の 12 行 3 列",
        "    ヒント: 存在しない名前です。綴り、スクリプトを読む順番、(function () { ... })(); の中で宣言しているかを確かめてください。",
        "",
        "[2] Autona.onUpdate の中のエラー: TypeError: Cannot read properties of undefined (reading 'name')",
        "    場所: 不明",
        "    ヒント: 読んだものが undefined か null です。view のキー名を CONTRACT.md と照らし、getElementById / querySelector で探す要素が index.html にあるかを確かめてください。",
        "",
        "守る規則と view のキーは AGENTS.md と CONTRACT.md のとおりです。",
    ].join("\n"));
    await settle();
    delete navigator.clipboard;
});

test("the copy tells a layout that is not ready", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    render(<Pane design={KIT}/>);
    await settle();
    await settle(READY_TIMEOUT_MS);

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_check_copy")}));
    const text = writeText.mock.calls[0][0];
    expect(text).toContain("「Mine」（layout/scoreboard_mine/index.html・オーバーレイ契約 1）");
    expect(text).toContain("[!] ../include/autona.js が読み込まれていません");
    await settle();
    delete navigator.clipboard;
});

test("a design declaring the contract that is not ready in time is told so", async () => {
    render(<Pane design={KIT}/>);
    await settle();
    await settle(READY_TIMEOUT_MS - 1);
    expect(panel()).toBeNull();
    await settle(1);

    expect(screen.getByTestId("layout-check-not-ready")).toHaveTextContent(
        i18n.t("layout_check_not_ready"));
    // Ready late still clears it.
    post(preview().contentWindow, READY);
    expect(screen.queryByTestId("layout-check-not-ready")).toBeNull();
    expect(screen.getByRole("status", {name: i18n.t("layout_check_title")}))
        .toHaveTextContent("no errors (overlay contract 1)");
});

test("a design declaring the contract that says it is ready is not told", async () => {
    render(<Pane design={KIT}/>);
    await settle();
    post(preview().contentWindow, READY);
    await settle(READY_TIMEOUT_MS * 2);
    expect(screen.queryByTestId("layout-check-not-ready")).toBeNull();
});

test("a layout that never said anything shows nothing, unless it posts an error", async () => {
    render(<Pane/>);
    await settle();
    await settle(READY_TIMEOUT_MS * 2);
    expect(panel()).toBeNull();
    expect(screen.queryByRole("status", {name: i18n.t("layout_check_title")}))
        .toBeNull();

    post(preview().contentWindow, failure());
    expect(shownErrors()).toHaveLength(1);
    expect(screen.queryByTestId("layout-check-not-ready")).toBeNull();
});

test("a design that said it was ready once is expected to again", async () => {
    const {rerender} = render(<Pane reloadKey={1}/>);
    await settle();
    post(preview().contentWindow, READY);
    rerender(<Pane reloadKey={2}/>);
    await settle(READY_TIMEOUT_MS);
    expect(screen.getByTestId("layout-check-not-ready")).toBeTruthy();
});

function chooseSample(name) {
    fireEvent.mouseDown(screen.getByRole("combobox",
                                         {name: i18n.t("layout_sample")}));
    fireEvent.click(screen.getByRole("option", {name}));
}

test("the sample switch is for a layout on autona.js and loads the sample", async () => {
    const {rerender} = render(<Pane/>);
    await settle();
    expect(screen.queryByRole("combobox", {name: i18n.t("layout_sample")}))
        .toBeNull();

    rerender(<Pane design={KIT}/>);
    await settle();
    expect(SAMPLE_NAMES).toEqual(["singles", "teams", "empty"]);
    chooseSample(i18n.t("layout_sample_teams"));
    expect(preview().getAttribute("src")).toBe(
        `${PREVIEW}/layout/scoreboard_mine/index.html?autonaSample=teams`);
    chooseSample(i18n.t("layout_sample_live"));
    expect(preview().getAttribute("src")).toBe(
        `${PREVIEW}/layout/scoreboard_mine/index.html`);

    // A layout that says it is ready is on autona.js too.
    rerender(<Pane design={SIMPLE}/>);
    await settle();
    post(preview().contentWindow, READY);
    chooseSample(i18n.t("layout_sample_empty"));
    expect(preview().getAttribute("src")).toBe(
        `${PREVIEW}/layout/scoreboard_simple/index.html?autonaSample=empty`);
});

test("the OBS URL is copied over http, with the board", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    const {rerender} = render(<Pane obsBoards={[1, 2, 3]}/>);
    await settle();

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_obs_url_copy")}));
    fireEvent.click(screen.getByRole("menuitem", {name: "Board 2"}));
    expect(writeText).toHaveBeenLastCalledWith(
        `http://127.0.0.1:${BACKEND_PORT}/layout/scoreboard_simple/index.html?scoreboardNumber=2`);

    rerender(<Pane obsBoards={[]} design={PLAIN}/>);
    await settle();
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_obs_url_copy")}));
    expect(writeText).toHaveBeenLastCalledWith(
        `http://127.0.0.1:${BACKEND_PORT}/layout/scoreboard_plain/index.html`);
    await settle();
    delete navigator.clipboard;
});

test("no OBS URL nor reload button unless asked for", async () => {
    render(<Pane/>);
    await settle();
    expect(screen.queryByRole("button", {name: i18n.t("layout_obs_url_copy")}))
        .toBeNull();
    expect(screen.queryByRole("button", {name: i18n.t("layout_reload")}))
        .toBeNull();
});

test("the reload asks the page to read the catalog and loads the frames again", async () => {
    const onReload = jest.fn();
    render(<Pane onReload={onReload} overlay={POPUP}/>);
    await settle();
    const [before, popupBefore] = frames();
    post(before.contentWindow, failure());

    fireEvent.click(screen.getByRole("button", {name: i18n.t("layout_reload")}));

    expect(onReload).toHaveBeenCalledTimes(1);
    const [after, popupAfter] = frames();
    expect(after).not.toBe(before);
    expect(popupAfter).not.toBe(popupBefore);
    expect(after.getAttribute("src")).toBe(before.getAttribute("src"));
    expect(panel()).toBeNull();
});

test("the folders the catalog could not list are shown under the select", async () => {
    render(<Pane unlisted={[
        {folder: "my_overlay", kind: null, reason: "no_manifest", detail: null},
        {folder: "scoreboard_x", kind: "scoreboard", reason: "bad_settings",
         detail: "JSONDecodeError: <b>line 3</b>"},
        {folder: "scoreboard_y", kind: "scoreboard", reason: "from_the_future",
         detail: null},
    ]}/>);
    await settle();

    const list = screen.getByRole("region",
                                  {name: i18n.t("layout_unlisted_title")});
    expect(list).toHaveTextContent(
        `layout/my_overlay/ — ${i18n.t("layout_unlisted_no_manifest")}`);
    expect(list).toHaveTextContent("JSONDecodeError: <b>line 3</b>");
    // An element <b> would be found by its own text.
    expect(within(list).queryByText("line 3")).toBeNull();
    expect(list).toHaveTextContent(
        `layout/scoreboard_y/ — ${i18n.t("layout_unlisted_other")}`);
});

test("a template's folder with no block is told in a template's words", async () => {
    render(<Pane unlisted={[
        {folder: "result_neon", kind: "result", reason: "no_manifest",
         detail: null},
        {folder: "thumbnail_neon", kind: "thumbnail", reason: "no_manifest",
         detail: null},
        {folder: "my_overlay", kind: null, reason: "no_manifest", detail: null},
    ]}/>);
    await settle();

    const list = screen.getByRole("region",
                                  {name: i18n.t("layout_unlisted_title")});
    const template = i18n.t("layout_unlisted_no_manifest_template");
    expect(list).toHaveTextContent(`layout/result_neon/ — ${template}`);
    expect(list).toHaveTextContent(`layout/thumbnail_neon/ — ${template}`);
    // An overlay's folder keeps the words that name the overlay folder names.
    expect(list).toHaveTextContent(
        `layout/my_overlay/ — ${i18n.t("layout_unlisted_no_manifest")}`);
    expect(template).not.toContain("scoreboard");
});

test("no folder that could not be listed, no list", async () => {
    render(<Pane unlisted={[]}/>);
    await settle();
    expect(screen.queryByRole("region", {name: i18n.t("layout_unlisted_title")}))
        .toBeNull();
});

test("coming back to a design (A, B, A) shows none of what it said before", async () => {
    const {rerender} = render(<Pane/>);
    await settle();
    post(preview().contentWindow, failure());
    expect(shownErrors()).toHaveLength(1);

    // B says nothing at all.
    rerender(<Pane design={PLAIN}/>);
    await settle();
    rerender(<Pane design={SIMPLE}/>);
    await settle();
    expect(panel()).toBeNull();
});

test("a ready and a late of before are not carried back either", async () => {
    const {rerender} = render(<Pane design={KIT}/>);
    await settle();
    await settle(READY_TIMEOUT_MS);
    expect(screen.getByTestId("layout-check-not-ready")).toBeTruthy();

    rerender(<Pane design={PLAIN}/>);
    await settle();
    rerender(<Pane design={KIT}/>);
    await settle();
    // Shown afresh: it has its time again.
    expect(screen.queryByTestId("layout-check-not-ready")).toBeNull();
    post(preview().contentWindow, READY);
    rerender(<Pane design={PLAIN}/>);
    await settle();
    rerender(<Pane design={KIT}/>);
    await settle();
    expect(screen.queryByRole("status", {name: i18n.t("layout_check_title")}))
        .toBeNull();
});

test("a sample there and back (live, teams, live) starts the live one afresh", async () => {
    render(<Pane design={KIT}/>);
    await settle();
    post(preview().contentWindow, failure());
    expect(shownErrors()).toHaveLength(1);

    chooseSample(i18n.t("layout_sample_teams"));
    await settle();
    chooseSample(i18n.t("layout_sample_live"));
    await settle();
    expect(panel()).toBeNull();
});

test("another design starts on the app's state, not the sample", async () => {
    const {rerender} = render(<Pane design={KIT}/>);
    await settle();
    chooseSample(i18n.t("layout_sample_teams"));
    expect(preview().getAttribute("src")).toContain("?autonaSample=teams");

    rerender(<Pane design={SIMPLE}/>);
    await settle();
    // Said ready: on autona.js, yet on the app's state.
    post(preview().contentWindow, READY);
    expect(preview().getAttribute("src")).toBe(
        `${PREVIEW}/layout/scoreboard_simple/index.html`);
    rerender(<Pane design={KIT}/>);
    await settle();
    expect(preview().getAttribute("src")).toBe(
        `${PREVIEW}/layout/scoreboard_mine/index.html`);
});

test("a frame posting in a loop stops changing the panel at the caps", () => {
    let report = EMPTY_REPORT;
    const message = parseLayoutMessage(failure());
    for (let i = 0; i < COUNT_MAX + 10; i++) {
        report = addMessage(report, message, "design");
    }
    expect(report.errors[0].count).toBe(COUNT_MAX);
    expect(addMessage(report, message, "design")).toBe(report);
    for (let i = 1; i < ERRORS_MAX; i++) {
        report = addMessage(report, {...message, message: `e${i}`}, "design");
    }
    for (let i = 0; i < COUNT_MAX + 10; i++) {
        report = addMessage(report, {...message, message: `x${i}`}, "design");
    }
    expect(report.dropped).toBe(COUNT_MAX);
    expect(addMessage(report, {...message, message: "more"}, "design"))
        .toBe(report);
});

test("the catalog's warnings of a listed design are in the panel and its copy", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    const broken = {...SIMPLE, warnings: [
        {reason: "bad_settings",
         detail: "JSONDecodeError: Expecting ',' delimiter: line 3 column 5"}]};
    render(<Pane design={broken}/>);
    await settle();

    const [warning] = screen.getAllByTestId("layout-check-warning");
    expect(warning).toHaveTextContent(
        `settings.json: ${i18n.t("layout_unlisted_bad_settings")}`);
    expect(warning).toHaveTextContent("line 3 column 5");
    expect(warning).toHaveTextContent(i18n.t("layout_check_hint_manifest"));

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_check_copy")}));
    const text = writeText.mock.calls[0][0];
    expect(text).toContain("[!] settings.json: settings.json が JSON として正しくありません");
    expect(text).toContain("    JSONDecodeError: Expecting ',' delimiter: line 3 column 5");
    // Ready, but warned: no "no errors".
    post(preview().contentWindow, READY);
    expect(screen.queryByRole("status", {name: i18n.t("layout_check_title")}))
        .toBeNull();
    await settle();
    delete navigator.clipboard;
});

// ── the live preview of a template (ADR-008 D7 R2) ───────────────────────

const TEMPLATE = {id: "type_a", kind: "thumbnail", displayName: "Type A",
                  folder: "thumbnail", entry: "thumbnail.html", contract: 2,
                  size: [1280, 720]};
const TEMPLATE_READY = {...READY, contract: 2,
                        source: "/layout/thumbnail/thumbnail.html"};

function Live({renderPayload, ...rest}) {
    return (
        <DesignPane designs={[TEMPLATE]} design={TEMPLATE}
                    onSelect={() => undefined} selectable={false}
                    size={TEMPLATE.size} renderPayload={renderPayload}
                    {...rest}/>
    );
}

test("a template's render is posted to its frame once it is ready, to the preview origin", async () => {
    const payload = {data: {p1_name: "A"}, config: {}, template: {}};
    render(<Live renderPayload={{payload, serial: 1}}/>);
    await settle();
    const frame = preview();
    // The page names this page's origin: autona.js takes renders from it only.
    expect(frame.getAttribute("src")).toBe(
        `${PREVIEW}/layout/thumbnail/thumbnail.html?autonaParent=`
        + encodeURIComponent(window.location.origin));
    const posted = jest.spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    // No select nor heading: the page picks the template.
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();
    await settle();
    expect(posted).not.toHaveBeenCalled();

    post(frame.contentWindow, TEMPLATE_READY);
    await settle();
    expect(posted).toHaveBeenCalledTimes(1);
    expect(posted).toHaveBeenCalledWith(
        {type: "autona:render", serial: 1, payload}, PREVIEW);
    // Ready again (the same document) posts nothing more.
    post(frame.contentWindow, TEMPLATE_READY);
    await settle();
    expect(posted).toHaveBeenCalledTimes(1);
});

test("a later render is posted again and starts its errors afresh", async () => {
    const view = render(<Live renderPayload={{payload: {n: 1}, serial: 1}}/>);
    await settle();
    const frame = preview();
    const posted = jest.spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    post(frame.contentWindow, TEMPLATE_READY);
    await settle();
    post(frame.contentWindow, failure({
        contract: 2, kind: "onRender", message: "Error: drawing failed",
        source: "/layout/thumbnail/thumbnail.js"}));
    expect(shownErrors()).toHaveLength(1);
    expect(shownErrors()[0]).toHaveTextContent(
        i18n.t("layout_check_hint_on_render"));
    expect(panel()).toHaveTextContent("Type A (template contract 2)");

    view.rerender(<Live renderPayload={{payload: {n: 2}, serial: 2}}/>);
    await settle();
    expect(posted).toHaveBeenCalledTimes(2);
    expect(posted).toHaveBeenLastCalledWith(
        {type: "autona:render", serial: 2, payload: {n: 2}}, PREVIEW);
    expect(shownErrors()).toHaveLength(0);
    // Still ready: no "not ready" for the render that drew.
    await settle(READY_TIMEOUT_MS * 2);
    expect(screen.queryByTestId("layout-check-not-ready")).toBeNull();
});

test("a template that never says it is ready is told so, and nothing is posted", async () => {
    render(<Live renderPayload={{payload: {}, serial: 1}}/>);
    await settle();
    const posted = jest.spyOn(preview().contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    await settle(READY_TIMEOUT_MS);
    expect(screen.getByTestId("layout-check-not-ready")).toBeTruthy();
    expect(panel()).toHaveTextContent(
        i18n.t("layout_check_not_ready_hint_template"));
    expect(posted).not.toHaveBeenCalled();
});

test("no payload yet, nothing is posted", async () => {
    render(<Live renderPayload={null}/>);
    await settle();
    const posted = jest.spyOn(preview().contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    post(preview().contentWindow, TEMPLATE_READY);
    await settle();
    expect(posted).not.toHaveBeenCalled();
});

const RENDERED = (serial, ok = true) =>
    ({type: "autona:rendered", contract: 2, serial, ok});

function status() {
    return screen.queryByRole("status", {name: i18n.t("layout_check_title")});
}

test("no errors is said only once the frame drew the render posted", async () => {
    const view = render(<Live renderPayload={{payload: {n: 1}, serial: 1}}/>);
    await settle();
    const frame = preview();
    jest.spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    post(frame.contentWindow, TEMPLATE_READY);
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_drawing_template", {contract: 2}));

    post(frame.contentWindow, RENDERED(1));
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_clean_template", {contract: 2}));

    // A newer render: drawing again until it is said drawn.
    view.rerender(<Live renderPayload={{payload: {n: 2}, serial: 2}}/>);
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_drawing_template", {contract: 2}));
    // An older one said late changes nothing.
    post(frame.contentWindow, RENDERED(1));
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_drawing_template", {contract: 2}));
    post(frame.contentWindow, RENDERED(2));
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_clean_template", {contract: 2}));
});

test.each([
    ["another origin", RENDERED(1), "http://evil.example"],
    ["an extra key", {...RENDERED(1), extra: 1}, undefined],
    ["contract 1", {...RENDERED(1), contract: 1}, undefined],
    ["a serial not a whole number", {...RENDERED(1), serial: 1.5}, undefined],
    ["ok not a boolean", {...RENDERED(1), ok: "yes"}, undefined],
])("a drawn message with %s is not taken", async (_name, data, origin) => {
    render(<Live renderPayload={{payload: {n: 1}, serial: 1}}/>);
    await settle();
    const frame = preview();
    jest.spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    post(frame.contentWindow, TEMPLATE_READY);
    await settle();
    post(frame.contentWindow, data, origin);
    await settle();
    expect(status()).toHaveTextContent(
        i18n.t("layout_check_drawing_template", {contract: 2}));
});
