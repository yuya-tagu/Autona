import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import i18n from "../i18n/config";
import {BACKEND_PORT} from "../env";
import DesignPane, {
    HEALTH_PATH,
    PREVIEW_SANDBOX,
    QUARANTINE_POLL_MS,
} from "./DesignPane";

// The layout previews (ADR-005 S-final SF-8a): the frames load from the
// server's read-only preview listener -- another origin, the port GET
// /api/v1/health names -- sandboxed with their origin kept; no port, no frame
// (never one on this page's origin); a frame found holding the keyboard focus
// is taken out with a note.

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

function Pane({design = SIMPLE, overlay = null, reloadKey}) {
    return (
        <div>
            <input aria-label="typed into"/>
            <DesignPane designs={[SIMPLE, PLAIN]} design={design}
                        onSelect={() => undefined} overlay={overlay}
                        reloadKey={reloadKey}/>
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

test("the page listens to no message from the frames", async () => {
    const listen = jest.spyOn(window, "addEventListener");
    render(<Pane overlay={POPUP}/>);
    await settle();
    preview().focus();
    fireEvent.blur(window);

    expect(listen.mock.calls.map(([type]) => type)).not.toContain("message");
    listen.mockRestore();
});
