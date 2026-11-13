import React from "react";
import {act, render, screen} from "@testing-library/react";
import i18n from "../i18n/config";
import socketConnection from "../websocketConnection";
import en from "../i18n/locales/en.json";
import ja from "../i18n/locales/ja.json";
import {
    STATUS_EVENT,
    STATUS_PATH,
    readStatus,
    statusText,
    useAppStatus,
} from "./useAppStatus";

// What the app is busy with (ADR-005 S-final SF-2): GET /api/v1/status on
// mount, Socket.IO's `status` after it, the newer revision winning, and a
// reconnect taking the next status whatever its revision.

jest.mock("../websocketConnection", () => {
    const handlers = {};
    const socket = {
        handlers,
        // Not jest.fn: CRA resets mocks' implementations before each test.
        on: (event, handler) => {
            (handlers[event] = handlers[event] ?? new Set()).add(handler);
        },
        off: (event, handler) => {
            handlers[event]?.delete(handler);
        },
    };
    return {__esModule: true, default: {instance: () => socket}};
});

const socket = socketConnection.instance();

function tell(event, payload) {
    act(() => {
        for (const handler of [...(socket.handlers[event] ?? [])]) {
            handler(payload);
        }
    });
}

const LOAD = {id: 4, code: "assets_load", args: {codename: "ssbu"},
              done: 10, total: 80};
const PAGES = {id: 5, code: "startgg_sets_page", args: {page: 2, pages: 5},
               done: 2, total: 5};

function Footer() {
    const {status, current} = useAppStatus();
    return (
        <div>
            <span data-testid="revision">{status.revision}</span>
            <span data-testid="jobs">{status.jobs.map((job) => job.code).join(",")}</span>
            <span data-testid="text">{statusText(current)}</span>
            <span data-testid="progress">
                {current ? `${current.done}/${current.total}` : ""}
            </span>
        </div>
    );
}

let answer;

function answerWith(data) {
    answer = {ok: true, data};
}

beforeEach(async () => {
    // jest 27's jsdom has no AbortSignal.timeout (api.ts's deadline).
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    await i18n.changeLanguage("en");
    answerWith({revision: 0, jobs: []});
    global.fetch = jest.fn(async () => ({
        ok: true,
        json: async () => answer,
    }));
});

afterEach(() => {
    delete global.fetch;
    for (const event of Object.keys(socket.handlers)) {
        socket.handlers[event].clear();
    }
});

async function mount() {
    render(<Footer/>);
    // The GET's answer.
    await act(async () => {});
}

const text = () => screen.getByTestId("text").textContent;
const revision = () => Number(screen.getByTestId("revision").textContent);

test("the status is read once on mount", async () => {
    answerWith({revision: 3, jobs: [LOAD]});
    await mount();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls[0][0]).toMatch(new RegExp(`${STATUS_PATH}$`));
    expect(revision()).toBe(3);
    expect(text()).toBe("Loading game assets... (ssbu)");
    expect(screen.getByTestId("progress").textContent).toBe("10/80");
});

test("an event shows its jobs and the last one is the current", async () => {
    await mount();
    expect(text()).toBe("");

    tell(STATUS_EVENT, {revision: 1, jobs: [LOAD, PAGES]});

    expect(screen.getByTestId("jobs").textContent)
        .toBe("assets_load,startgg_sets_page");
    expect(text()).toBe("Fetching the set list from Start.gg... (2/5)");

    tell(STATUS_EVENT, {revision: 2, jobs: []});
    expect(text()).toBe("");
    expect(screen.getByTestId("progress").textContent).toBe("");
});

test("an older revision does not replace a newer one", async () => {
    answerWith({revision: 5, jobs: [LOAD]});
    await mount();

    tell(STATUS_EVENT, {revision: 4, jobs: []});

    expect(revision()).toBe(5);
    expect(text()).toBe("Loading game assets... (ssbu)");
});

test("an answer older than an event is dropped", async () => {
    let release;
    global.fetch = jest.fn(() => new Promise((resolve) => {
        release = () => resolve({ok: true, json: async () => (
            {ok: true, data: {revision: 1, jobs: [LOAD]}})});
    }));
    render(<Footer/>);
    tell(STATUS_EVENT, {revision: 2, jobs: [PAGES]});

    await act(async () => release());

    expect(revision()).toBe(2);
    expect(screen.getByTestId("jobs").textContent).toBe("startgg_sets_page");
});

test("after a reconnect the next status is taken whatever its revision", async () => {
    answerWith({revision: 40, jobs: [LOAD]});
    await mount();

    // A restarted app counts from 0 again; it sends the new connection one.
    tell("connect");
    tell(STATUS_EVENT, {revision: 0, jobs: []});

    expect(revision()).toBe(0);
    expect(text()).toBe("");
});

test("something not a status is ignored", async () => {
    answerWith({revision: 2, jobs: [LOAD]});
    await mount();

    tell(STATUS_EVENT, {jobs: []});
    tell(STATUS_EVENT, "busy");
    tell(STATUS_EVENT, null);

    expect(revision()).toBe(2);
    expect(readStatus({revision: 1, jobs: [{id: 1}, PAGES]}).jobs)
        .toEqual([PAGES]);
});

test("unmounting stops listening", async () => {
    const {unmount} = render(<Footer/>);
    await act(async () => {});
    expect(socket.handlers[STATUS_EVENT].size).toBe(1);

    unmount();

    expect(socket.handlers[STATUS_EVENT].size).toBe(0);
    expect(socket.handlers.connect.size).toBe(0);
});

test("the words follow the language, never the server", async () => {
    await i18n.changeLanguage("ja");
    expect(statusText(LOAD)).toBe("ゲームアセットを読み込み中... (ssbu)");
    expect(statusText({...PAGES, code: "startgg_entrants_page"}))
        .toBe("Start.gg から参加者情報を取得中... (2/5)");
    await i18n.changeLanguage("en");
    expect(statusText({...PAGES, code: "startgg_sets_failed",
                       args: {failed: 1, pages: 5}}))
        .toBe("Some pages of the set list could not be fetched (1/5)");
    // A code this build does not know shows as itself.
    expect(statusText({...LOAD, code: "something_new"})).toBe("something_new");
    expect(statusText(null)).toBe("");
});

test("every status key is in both languages, with the same args", () => {
    const keys = (table) => Object.keys(table).filter((key) => key.startsWith("status_"));
    const args = (words) => [...words.matchAll(/{{(\w+)}}/g)].map((m) => m[1]).sort();
    expect(keys(ja).sort()).toEqual(keys(en).sort());
    expect(keys(en).length).toBeGreaterThan(0);
    for (const key of keys(en)) {
        expect([key, args(ja[key])]).toEqual([key, args(en[key])]);
        // No Japanese in the English words.
        expect([key, /[぀-ヿ一-鿿]/.test(en[key])]).toEqual([key, false]);
    }
});
