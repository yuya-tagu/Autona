import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {BACKEND_PORT} from "../env";
import {PanelShellContext} from "../PanelShell/context";
import StatsPreviewPage from "./index";

// The Stats Display Settings panel in React (ADR-005 S4l): it reads
// GET /api/v1/stats/overlays, keeps the choice by PUT .../selected (by key:
// the player 1 and 2 entries share one folder), writes the feature's colours
// and shows the Companion macro's request with the URL the server built.

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const OVERLAYS = [
    {key: "upset_factor", id: "upset_factor", entry: "index.html",
     displayName: "Upset Factor"},
    {key: "recent_sets", id: "recent_sets", entry: "index.html",
     displayName: "Recent Sets"},
    {key: "last_sets_p1", id: "last_sets", entry: "player1.html",
     displayName: "Last Sets (Player 1)"},
    {key: "last_sets_p2", id: "last_sets", entry: "player2.html",
     displayName: "Last Sets (Player 2)"},
];
const REFRESH = {method: "POST", path: "/scoreboards/1/stats:refresh",
                 url: "http://192.168.0.5:5107/api/v1/scoreboards/1/stats:refresh"};

let selected;
// The status every write answers instead of 200, when set.
let refuse;

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function writes(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && new URL(url).pathname === path)
        .map(([, init]) => (init.body ? JSON.parse(init.body) : null));
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
                value={{panel: "statsPreview", desktop: false, language: "en"}}>
                <StatsPreviewPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await screen.findByRole("region", {name: i18n.t("statspreview_companion")});
}

function typeSelect() {
    return screen.getByRole("combobox", {name: i18n.t("statspreview_type")});
}

function pick(name) {
    fireEvent.mouseDown(typeSelect());
    fireEvent.click(screen.getByRole("option", {name}));
}

beforeEach(() => {
    jest.useFakeTimers();
    selected = OVERLAYS[2];
    refuse = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path} = new URL(url);
        if (method === "GET" && path === "/api/v1/stats/overlays") {
            return answer(200, {ok: true, data: {
                overlays: OVERLAYS, selected, refresh: REFRESH}});
        }
        if (refuse && method !== "GET") {
            return answer(refuse, {ok: false,
                                   error: {code: "X", message: "no"}});
        }
        if (method === "PUT" && path === "/api/v1/stats/overlays/selected") {
            const {key} = JSON.parse(init.body);
            selected = OVERLAYS.find((item) => item.key === key);
            return answer(200, {ok: true, data: selected});
        }
        return answer(200, {ok: true, revision: 1});
    });
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                colors: {stats: {themeColor: "#112233", accentColor: "#445566"}},
                tournamentInfo: {themeColor: "#181B21", accentColor: "#f0a500"},
            },
            revision: 1,
        }));
    });
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

test("the kept overlay is previewed and given to OBS by its entry", async () => {
    await renderPage();

    expect(typeSelect()).toHaveTextContent("Last Sets (Player 1)");
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/last_sets\/player1\.html$/));
    expect(screen.getByRole("textbox", {name: i18n.t("commentary_obs_url")}))
        .toHaveValue(`http://localhost:${BACKEND_PORT}/layout/last_sets/player1.html`);
});

test("there is one select for the overlay, with its note above the preview", async () => {
    await renderPage();

    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    expect(screen.getByText(i18n.t("statspreview_type_note")))
        .toBeInTheDocument();
});

test("player 2 of the same folder is picked by its key", async () => {
    await renderPage();

    pick("Last Sets (Player 2)");
    await settle();

    expect(writes("PUT", "/api/v1/stats/overlays/selected"))
        .toEqual([{key: "last_sets_p2"}]);
    expect(typeSelect()).toHaveTextContent("Last Sets (Player 2)");
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/last_sets\/player2\.html$/));
});

test("the colours are written to the stats feature", async () => {
    await renderPage();

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("commentary_color_match_tournament")}));
    await settle();

    expect(writes("PATCH", "/api/v1/colors/stats")).toEqual([
        {theme: "#181B21", accent: "#f0a500"}]);
});

test("a selection that fails leaves the choice and the preview as they were", async () => {
    await renderPage();
    refuse = 500;

    pick("Upset Factor");
    await settle();

    expect(await screen.findByText(i18n.t("request_failed")))
        .toBeInTheDocument();
    expect(typeSelect()).toHaveTextContent("Last Sets (Player 1)");
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/last_sets\/player1\.html$/));
});

test.each([401, 403])("a %i is told as the access token, once", async (status) => {
    await renderPage();
    refuse = status;

    pick("Upset Factor");
    await settle();
    pick("Recent Sets");
    await settle();

    expect(await screen.findAllByText(i18n.t("stream_token_required")))
        .toHaveLength(1);
    expect(screen.queryByText(i18n.t("request_failed"))).toBeNull();
    expect(typeSelect()).toHaveTextContent("Last Sets (Player 1)");
});

test("choosing again at once keeps the last choice", async () => {
    await renderPage();

    pick("Upset Factor");
    pick("Last Sets (Player 2)");
    await settle();
    await settle();

    expect(writes("PUT", "/api/v1/stats/overlays/selected")).toEqual([
        {key: "upset_factor"}, {key: "last_sets_p2"}]);
    expect(typeSelect()).toHaveTextContent("Last Sets (Player 2)");
    expect(screen.getByTitle(i18n.t("commentary_preview")))
        .toHaveAttribute("src", expect.stringMatching(
            /\/layout\/last_sets\/player2\.html$/));
});

test("the Companion card shows the request and the server's URL", async () => {
    await renderPage();

    const card = screen.getByRole("region",
                                  {name: i18n.t("statspreview_companion")});
    expect(card).toHaveTextContent("POST");
    expect(card).toHaveTextContent("/scoreboards/1/stats:refresh");
    expect(card).toHaveTextContent(i18n.t("statspreview_companion_body_none"));
    expect(card).toHaveTextContent(i18n.t("statspreview_companion_guide"));
    expect(card).toHaveTextContent("X-Autona-Token");
    expect(screen.getByRole("textbox",
                            {name: i18n.t("statspreview_companion_url")}))
        .toHaveValue(REFRESH.url);
});

describe("the copy buttons", () => {
    let writeText;

    beforeEach(() => {
        writeText = jest.fn(async () => undefined);
        Object.defineProperty(navigator, "clipboard", {
            value: {writeText}, configurable: true});
    });

    afterEach(() => {
        delete navigator.clipboard;
    });

    test("the Companion request URL is copied", async () => {
        await renderPage();

        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("statspreview_companion_copy")}));
        await settle();

        expect(writeText).toHaveBeenCalledWith(REFRESH.url);
        expect(await screen.findByText(i18n.t("statspreview_companion_copied")))
            .toBeInTheDocument();
    });

    test("the OBS URL is copied", async () => {
        await renderPage();

        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("commentary_obs_copy")}));
        await settle();

        expect(writeText).toHaveBeenCalledWith(
            `http://localhost:${BACKEND_PORT}/layout/last_sets/player1.html`);
        expect(await screen.findByText(i18n.t("commentary_obs_copied")))
            .toBeInTheDocument();
    });
});

test("mounting sends nothing", async () => {
    await renderPage();

    const sent = global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET");
    expect(sent).toEqual([]);
});

test("a settings read that is refused shows the reload button", async () => {
    const real = global.fetch;
    global.fetch = jest.fn(async () => answer(400, {
        ok: false, error: {code: "INVALID_ARGUMENT", message: "no"}}));
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "statsPreview", desktop: false, language: "en"}}>
                <StatsPreviewPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();

    expect(await screen.findByText(i18n.t("statspreview_load_failed")))
        .toBeInTheDocument();
    global.fetch = real;
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("statspreview_reload")}));
    await settle();

    expect(await screen.findByRole(
        "region", {name: i18n.t("statspreview_companion")})).toBeInTheDocument();
});
