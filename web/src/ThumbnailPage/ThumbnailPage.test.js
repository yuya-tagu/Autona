import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import ThumbnailPage, {PREVIEW_DEBOUNCE_MS} from "./index";

// The match thumbnail's page in React (ADR-005 S4p): it reads and writes
// /api/v1/thumbnail, asks for a preview PREVIEW_DEBOUNCE_MS after a change
// while it is shown, shows the image of the revision thumbnail.render ended
// with, and generates the thumbnail (409: one is on its way; 503: the
// headless shell cannot draw).

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const DEFAULTS = {
    template_id: "type_a", background_source: "default", background_name: "",
    display_phase: true, use_team_names: true, use_sponsors: true,
    game: {tgame: {flip_p1: false, flip_p2: true, smooth_scale: true,
                   zoom: 100, align: {horizontal: 50, vertical: 40}}},
};
const TEMPLATES = [{id: "type_a", name: "Standard"},
                   {id: "type_b", name: "Center Panel"}];

let settings;
let videoTitle;
let canRender;
let game;
let revision;
let stateRevision = 0;
// Write path -> the status it answers instead.
let refuse;
// "METHOD path" -> a promise its answer waits for (hold()).
let gates;

// The answers to `method` `path` wait until the returned function is
// called.
function hold(method, path) {
    const key = `${method} ${path}`;
    let release;
    gates[key] = new Promise((resolve) => {
        release = () => {
            delete gates[key];
            resolve();
        };
    });
    return release;
}

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
        .map(([, init]) => JSON.parse(init.body));
}

function reads(path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "GET"
            && new URL(url).pathname === path).length;
}

function previews() {
    return writes("POST", "/api/v1/thumbnail:preview").length;
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

function setState(state) {
    stateRevision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {game: {codename: "tgame", name: "T Game"}, ...state},
            revision: stateRevision,
        }));
    });
}

function rendered(render) {
    return {status: "done", path: "", error: "", revision: 1, ...render};
}

function mountPage({desktop = false} = {}) {
    return render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "thumbnail", desktop, language: "en"}}>
                <ThumbnailPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
}

async function renderPage(options) {
    const page = mountPage(options);
    await settle();
    await screen.findByRole("region", {name: i18n.t("thumbnail_preview")});
    return page;
}

function region(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function button(scope, key) {
    return within(scope).getByRole("button", {name: i18n.t(key)});
}

function checkbox(key) {
    return screen.getByRole("checkbox", {name: i18n.t(key)});
}

function setVisible(visible) {
    Object.defineProperty(document, "visibilityState",
                          {value: visible ? "visible" : "hidden",
                           configurable: true});
    act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
}

function setSecure(secure) {
    Object.defineProperty(window, "isSecureContext",
                          {value: secure, configurable: true});
    if (secure) {
        global.ClipboardItem = class {
            constructor(items) {
                this.items = items;
            }
        };
    } else {
        delete global.ClipboardItem;
    }
}

function merge(target, change) {
    Object.entries(change).forEach(([key, value]) => {
        if (value && typeof value === "object" && !Array.isArray(value)) {
            target[key] = merge({...(target[key] ?? {})}, value);
        } else {
            target[key] = value;
        }
    });
    return target;
}

beforeEach(() => {
    jest.useFakeTimers();
    settings = JSON.parse(JSON.stringify(DEFAULTS));
    videoTitle = "Cup | Alpha vs Bravo | TG";
    canRender = true;
    game = {codename: "tgame", name: "T Game"};
    revision = 0;
    refuse = {};
    gates = {};
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText: jest.fn(async () => {}),
                write: jest.fn(async () => {})},
        configurable: true,
    });
    setSecure(false);
    setVisible(true);
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : {};
        const gate = gates[`${method} ${path}`];
        if (gate) {
            await gate;
        }
        if (method !== "GET" && refuse[path]) {
            return answer(refuse[path], {ok: false,
                                         error: {code: "X", message: "no"}});
        }
        if (method === "GET") {
            if (path === "/api/v1/thumbnail") {
                return answer(200, {ok: true, data: {
                    settings, game, templates: TEMPLATES, videoTitle,
                    canRender}});
            }
            return answer(404, {ok: false});
        }
        if (path === "/api/v1/thumbnail" && method === "PATCH") {
            settings = merge(settings, body);
            return answer(200, {ok: true, data: settings});
        }
        if (path === "/api/v1/thumbnail:preview"
                || path === "/api/v1/thumbnail:generate") {
            revision += 1;
            return answer(202, {ok: true, data: {accepted: true,
                                                 renderRevision: revision}});
        }
        if (path === "/api/v1/thumbnail:reset") {
            settings = JSON.parse(JSON.stringify(DEFAULTS));
            return answer(200, {ok: true, data: settings});
        }
        if (path === "/api/v1/thumbnail/background") {
            settings = {...settings, background_source: "custom",
                        background_name: "thumbnail_background_ab12.png"};
            return answer(200, {ok: true, data: settings});
        }
        return answer(200, {ok: true, revision: 1});
    });
    setState({});
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
    delete global.ClipboardItem;
});

// ── the page and its preview ──────────────────────────────────────────────

test("the cards come up with the saved settings, and one preview after the wait",
     async () => {
    await renderPage();

    ["thumbnail_design", "thumbnail_items", "thumbnail_characters",
     "thumbnail_colors", "thumbnail_preview", "thumbnail_video_title"]
        .forEach((key) => expect(region(key)).toBeInTheDocument());
    expect(reads("/api/v1/thumbnail")).toBe(1);
    expect(checkbox("thumbnail_display_phase")).toBeChecked();
    expect(checkbox("thumbnail_flip_p2")).toBeChecked();
    // The smooth scaling is kept, not shown (S4p).
    expect(screen.queryByRole("checkbox", {name: /smooth/i}))
        .not.toBeInTheDocument();
    expect(previews()).toBe(0);

    await settle(PREVIEW_DEBOUNCE_MS);

    expect(previews()).toBe(1);
});

test("a choice is saved at once and drawn once the changes stop", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.click(checkbox("thumbnail_display_phase"));
    await settle(100);
    fireEvent.click(checkbox("thumbnail_use_sponsors"));
    await settle(100);

    expect(writes("PATCH", "/api/v1/thumbnail"))
        .toEqual([{display_phase: false}, {use_sponsors: false}]);
    expect(previews()).toBe(1);

    await settle(PREVIEW_DEBOUNCE_MS);

    expect(previews()).toBe(2);
    expect(checkbox("thumbnail_display_phase")).not.toBeChecked();
});

test("the image is the revision thumbnail.render ended with", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");
    // Asked for (revision 1), not drawn yet.
    expect(within(card).getByText(i18n.t("thumbnail_previewing")))
        .toBeInTheDocument();
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();

    setState({thumbnail: {render: rendered({status: "running"})}});
    expect(within(card).getByText(i18n.t("thumbnail_previewing")))
        .toBeInTheDocument();

    setState({thumbnail: {render: rendered({})}});

    expect(within(card).queryByText(i18n.t("thumbnail_previewing")))
        .not.toBeInTheDocument();
    expect(within(card).getByRole("img", {name: i18n.t("thumbnail_preview_image")}))
        .toHaveAttribute("src", "http://localhost:5107/api/v1/thumbnail/preview?v=1");

    // A failed preview of the page's says why; the last image stays.
    fireEvent.click(checkbox("thumbnail_use_sponsors"));
    await settle();
    await settle(PREVIEW_DEBOUNCE_MS);
    setState({thumbnail: {render: rendered({status: "error",
                                            error: "render_failed",
                                            revision: 2})}});
    expect(within(card).getByText(i18n.t("thumbnail_error_render_failed")))
        .toBeInTheDocument();
    expect(within(card).getByRole("img")).toHaveAttribute(
        "src", "http://localhost:5107/api/v1/thumbnail/preview?v=1");
});

test("a change of the match or the colours is drawn again", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    setState({score: {"1": {team: {"1": {player: {"1": {name: "Alpha"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(2);
    // And the title is read again.
    expect(reads("/api/v1/thumbnail")).toBe(2);

    setState({score: {"1": {team: {"1": {player: {"1": {name: "Alpha"}}}}}},
              colors: {thumbnail: {themeColor: "#112233"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(3);

    // The statistics beside the match are not the picture.
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Alpha"}}}},
                            last_sets: [1]}},
              colors: {thumbnail: {themeColor: "#112233"}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(3);
});

// Only what the picture (thumbnail.js) and the title (BuildVideoTitle) read
// (S4p review): the score, a player's country, the other display options
// draw nothing; the sponsors' display option, the round's English name (the
// title) and a new banner do.
test("what neither the picture nor the title shows draws nothing", async () => {
    const player = {name: "Alpha", team: "SP", country: {code: "JP"},
                    character: {"1": {name: "Hero"}}};
    const at = (change = {}) => ({
        score: {"1": {match: "Winners Final", match_en: "Winners Final",
                      team: {"1": {score: 0, player: {"1": player}},
                             "2": {score: 0}},
                      ...change.board}},
        display_options: {show_sponsor: true, show_country: true,
                          ...change.display},
        tournamentInfo: {name: "Cup", bannerVersion: 1, url: "u",
                         ...change.tournament},
    });
    setState(at());
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);
    const titles = reads("/api/v1/thumbnail");

    for (const change of [
        {board: {team: {"1": {score: 2, player: {"1": player}},
                        "2": {score: 1}}}},
        {board: {team: {"1": {score: 0, player: {"1": {
            ...player, country: {code: "US"}}}}, "2": {score: 0}}}},
        {display: {show_country: false}},
        {tournament: {url: "v"}},
        {board: {best_of: 5}},
    ]) {
        setState(at(change));
        await settle(PREVIEW_DEBOUNCE_MS);
    }
    expect(previews()).toBe(1);
    expect(reads("/api/v1/thumbnail")).toBe(titles);

    let drawn = 1;
    for (const change of [
        {display: {show_sponsor: false}},
        {board: {match_en: "Grand Final"}},
        {tournament: {bannerVersion: 2}},
        {board: {team: {"1": {score: 0, player: {"1": {
            ...player, character: {"1": {name: "Mage"}}}}}, "2": {score: 0}}}},
    ]) {
        setState(at(change));
        await settle(PREVIEW_DEBOUNCE_MS);
        drawn += 1;
        expect(previews()).toBe(drawn);
    }
    expect(reads("/api/v1/thumbnail")).toBe(titles + 4);
});

test("while hidden nothing is drawn; each time it is shown again, it is",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    setVisible(false);
    fireEvent.click(checkbox("thumbnail_display_phase"));
    await settle();
    await settle(PREVIEW_DEBOUNCE_MS * 3);
    expect(writes("PATCH", "/api/v1/thumbnail")).toHaveLength(1);
    expect(previews()).toBe(1);

    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(2);

    // Shown again with nothing changed in the state: drawn again, as the
    // picture may show an edit the state does not carry (below), like the Qt
    // panel on every reveal.
    setVisible(false);
    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(3);
});

// The S4p check on the desktop: the page in front, the scoreboard (a Qt
// panel) in front of the shared view, a player renamed through v1 on board 1,
// back to the page. Board 1 is held on the desktop: the edit is pending until
// 反映, so core draws it and titles it (include_pending) while the published
// state this page follows (Socket.IO) does not carry it.
test("an edit the published state does not carry is drawn once shown again",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);

    setVisible(false);
    videoTitle = "Cup | Charlie vs Bravo | TG";
    await settle(2000);
    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);

    expect(screen.getByRole("textbox", {name: i18n.t("thumbnail_video_title_field")}))
        .toHaveValue("Cup | Charlie vs Bravo | TG");
    expect(previews()).toBe(2);
});

// The orders the desktop's view may give a reveal in: it freezes a hidden
// page and thaws it before it says it is shown, so the state that came
// meanwhile may land before or after the show.
test("a published change while hidden is drawn once shown, before or after",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    setVisible(false);
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Alpha"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);
    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(2);

    setVisible(false);
    await settle(PREVIEW_DEBOUNCE_MS);
    setVisible(true);
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Delta"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    // One preview for the reveal and the change in the same wait.
    expect(previews()).toBe(3);
});

// A hide and the show after it seen in one update: React never renders the
// page hidden (a page frozen before it rendered its hide runs both once
// thawed). Still a reveal.
test("a hide and a show in one update are a reveal", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);
    const titles = reads("/api/v1/thumbnail");

    act(() => {
        Object.defineProperty(document, "visibilityState",
                              {value: "hidden", configurable: true});
        document.dispatchEvent(new Event("visibilitychange"));
        Object.defineProperty(document, "visibilityState",
                              {value: "visible", configurable: true});
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle(PREVIEW_DEBOUNCE_MS);

    expect(reads("/api/v1/thumbnail")).toBe(titles + 1);
    expect(previews()).toBe(2);
});

// A reveal and the first display are one preview (S4p review).
test("a reveal while the page loads is its first preview, once", async () => {
    mountPage();
    setVisible(false);
    setVisible(true);
    await settle();
    await screen.findByRole("region", {name: i18n.t("thumbnail_preview")});
    await settle(PREVIEW_DEBOUNCE_MS * 2);

    expect(reads("/api/v1/thumbnail")).toBe(1);
    expect(previews()).toBe(1);
});

test("a reveal before the first preview is sent is that preview", async () => {
    await renderPage();
    setVisible(false);
    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS * 2);

    expect(previews()).toBe(1);
});

test("a page hidden before its preview draws it once shown", async () => {
    await renderPage();
    setVisible(false);
    await settle(PREVIEW_DEBOUNCE_MS * 2);
    expect(previews()).toBe(0);

    setVisible(true);
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(1);
});

// Another page shown in the desktop's view unmounts this one (S4p review
// #10): what it was about to ask for is not asked.
test("a page that goes away asks for nothing after it", async () => {
    const page = await renderPage();  // its first preview is waiting
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Alpha"}}}}}}});
    const titles = reads("/api/v1/thumbnail");

    page.unmount();
    await settle(PREVIEW_DEBOUNCE_MS * 3);

    expect(previews()).toBe(0);
    expect(reads("/api/v1/thumbnail")).toBe(titles);
});

test("the headless shell's page says it cannot draw and asks for nothing",
     async () => {
    canRender = false;
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");

    expect(within(card).getByText(i18n.t("thumbnail_cannot_render")))
        .toBeInTheDocument();
    expect(button(card, "thumbnail_generate")).toBeDisabled();
    expect(previews()).toBe(0);
    // The settings are still saved.
    fireEvent.click(checkbox("thumbnail_use_team_names"));
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(writes("PATCH", "/api/v1/thumbnail"))
        .toEqual([{use_team_names: false}]);
    expect(previews()).toBe(0);
});

// 503 (S4p review): the description is read again and its canRender
// followed, not taken to be false by the page itself.
test("a preview refused with 503 asks again whether this shell draws",
     async () => {
    refuse["/api/v1/thumbnail:preview"] = 503;
    await renderPage();
    canRender = false;  // the headless shell's answer
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();

    const card = region("thumbnail_preview");
    expect(reads("/api/v1/thumbnail")).toBe(2);
    expect(within(card).getByText(i18n.t("thumbnail_cannot_render")))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

test("a 503 from a shell that still draws takes nothing away", async () => {
    refuse["/api/v1/thumbnail:generate"] = 503;
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");

    fireEvent.click(button(card, "thumbnail_generate"));
    await settle();
    await settle();

    expect(reads("/api/v1/thumbnail")).toBe(2);
    expect(within(card).queryByText(i18n.t("thumbnail_cannot_render")))
        .not.toBeInTheDocument();
    expect(button(card, "thumbnail_generate")).toBeEnabled();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
    // And the previews go on.
    fireEvent.click(checkbox("thumbnail_use_sponsors"));
    await settle();
    await settle(PREVIEW_DEBOUNCE_MS);
    expect(previews()).toBe(2);
});

// ── the generation ────────────────────────────────────────────────────────

const PATH = "out/thumbnails/Al#pha-vs-Bravo-2026-11-08-01-02-03.png";
const ENCODED = "out/thumbnails/Al%23pha-vs-Bravo-2026-11-08-01-02-03.png";

test("a generation is said once it ended: the snackbar and the status line",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");

    fireEvent.click(button(card, "thumbnail_generate"));
    await settle();
    expect(writes("POST", "/api/v1/thumbnail:generate")).toEqual([{}]);
    // Revision 2: the preview had 1.
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("thumbnail_generating"));
    expect(button(card, "thumbnail_generate")).toBeDisabled();

    setState({thumbnail: {render: rendered({status: "running", revision: 2})}});
    setState({thumbnail: {render: rendered({path: PATH, revision: 2})}});

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("thumbnail_saved", {path: PATH}));
    expect(screen.getAllByText(i18n.t("thumbnail_saved", {path: PATH})))
        .toHaveLength(2);
    expect(button(card, "thumbnail_generate")).toBeEnabled();
});

test("a generation that failed says why", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");

    fireEvent.click(button(card, "thumbnail_generate"));
    await settle();
    setState({thumbnail: {render: rendered({status: "error", error: "no_player",
                                            revision: 2})}});

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("thumbnail_error_no_player"));
});

// A later run's end seen first (two state updates as one): the generation
// ended before it, with a new file or without (S4p review #10).
test.each([
    ["with a new file", PATH, "thumbnail_saved"],
    ["without one", "", "thumbnail_error_render_failed"],
])("a generation whose end was overtaken is said %s", async (_name, path,
                                                              said) => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");

    fireEvent.click(button(card, "thumbnail_generate"));
    await settle();
    // The generation is revision 2; revision 3 (a preview) is seen first.
    setState({thumbnail: {render: rendered({path, revision: 3})}});

    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t(said, {path}));
    expect(button(card, "thumbnail_generate")).toBeEnabled();
});

// Not twice, and not with the old background (S4p review #8).
test("no generation is asked for while one awaits its answer", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");
    const release = hold("POST", "/api/v1/thumbnail:generate");

    fireEvent.click(button(card, "thumbnail_generate"));
    fireEvent.click(button(card, "thumbnail_generate"));
    await settle();

    expect(button(card, "thumbnail_generate")).toBeDisabled();
    release();
    await settle();
    await settle();
    expect(writes("POST", "/api/v1/thumbnail:generate")).toHaveLength(1);
    // Revision 2: generating.
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("thumbnail_generating"));
});

test("no generation is asked for while a background is uploaded", async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const card = region("thumbnail_preview");
    const release = hold("POST", "/api/v1/thumbnail/background");

    fireEvent.change(screen.getByTestId("thumbnail-background-file"),
                     {target: {files: [new File([new Uint8Array([137, 80])],
                                                "mine.png",
                                                {type: "image/png"})]}});
    for (let i = 0; i < 5; i++) {
        await settle(10);
    }
    expect(button(card, "thumbnail_generate")).toBeDisabled();

    release();
    for (let i = 0; i < 5; i++) {
        await settle(10);
    }
    expect(button(card, "thumbnail_generate")).toBeEnabled();
});

test("a generation already on its way is told, not reported as a failure",
     async () => {
    refuse["/api/v1/thumbnail:generate"] = 409;
    await renderPage();

    fireEvent.click(button(region("thumbnail_preview"), "thumbnail_generate"));
    await settle();

    expect(await screen.findByText(i18n.t("thumbnail_busy"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

test("a secure page copies the generated thumbnail, not the preview", async () => {
    setSecure(true);
    setState({thumbnail: {render: rendered({path: PATH, revision: 4})}});
    await renderPage();
    const card = region("thumbnail_preview");
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();

    fireEvent.click(button(card, "thumbnail_copy_image"));
    await settle();
    await settle();

    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls.map(([url]) => url))
        .toContain(`http://localhost:5107/${ENCODED}?v=4`);
    expect(await screen.findByText(i18n.t("thumbnail_image_copied")))
        .toBeInTheDocument();
});

test("a secure page copies nothing before a thumbnail is generated", async () => {
    setSecure(true);
    await renderPage();

    expect(button(region("thumbnail_preview"), "thumbnail_copy_image"))
        .toBeDisabled();
});

test("a page that cannot copy an image offers the generated one to open",
     async () => {
    setState({thumbnail: {render: rendered({path: PATH, revision: 4})}});
    await renderPage();
    const card = region("thumbnail_preview");

    expect(within(card).queryByRole("button", {name: i18n.t("thumbnail_copy_image")}))
        .not.toBeInTheDocument();
    expect(within(card).getByRole("link", {name: i18n.t("thumbnail_open_image")}))
        .toHaveAttribute("href", `http://localhost:5107/${ENCODED}?v=4`);
});

test("the desktop's view that cannot copy an image says so, with no link",
     async () => {
    setState({thumbnail: {render: rendered({path: PATH, revision: 4})}});
    await renderPage({desktop: true});
    const card = region("thumbnail_preview");

    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    expect(button(card, "thumbnail_copy_image")).toBeDisabled();
    expect(within(card).getByText(i18n.t("thumbnail_copy_unavailable")))
        .toBeInTheDocument();
});

// ── the settings ──────────────────────────────────────────────────────────

test("the settings go back to the defaults only once that is confirmed",
     async () => {
    settings = {...settings, use_sponsors: false};
    await renderPage();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("thumbnail_reset")}));
    let confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "stream_cancel"));
    await settle(1000);
    expect(writes("POST", "/api/v1/thumbnail:reset")).toEqual([]);

    fireEvent.click(screen.getByRole("button", {name: i18n.t("thumbnail_reset")}));
    confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "thumbnail_reset_yes"));
    await settle();
    await settle();

    expect(writes("POST", "/api/v1/thumbnail:reset")).toEqual([{confirm: true}]);
    await settle(1000);
    expect(checkbox("thumbnail_use_sponsors")).toBeChecked();
});

function zoomField() {
    return within(region("thumbnail_characters"))
        .getByRole("textbox", {name: i18n.t("thumbnail_zoom")});
}

// Sent once confirmed, not as typed (S4p review #6): every pause in the
// typing was a save and a preview of a number on its way.
test("a number is saved for the selected game once Enter confirms it",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);

    fireEvent.focus(zoomField());
    for (const typed of ["1", "15", "150"]) {
        fireEvent.change(zoomField(), {target: {value: typed}});
        await settle(1000);
    }
    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);
    expect(previews()).toBe(1);

    fireEvent.keyDown(zoomField(), {key: "Enter"});
    await settle();
    await settle(PREVIEW_DEBOUNCE_MS);

    expect(writes("PATCH", "/api/v1/thumbnail"))
        .toEqual([{game: {tgame: {zoom: 150}}}]);
    expect(previews()).toBe(2);
    // Still in the field: leaving it sends nothing more.
    fireEvent.blur(zoomField());
    await settle(1000);
    expect(writes("PATCH", "/api/v1/thumbnail")).toHaveLength(1);
    expect(zoomField()).toHaveValue("150");
});

test("a number is saved once the field is left", async () => {
    await renderPage();

    fireEvent.focus(zoomField());
    fireEvent.change(zoomField(), {target: {value: "150"}});
    await settle(1000);
    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);

    fireEvent.blur(zoomField());
    await settle();

    expect(writes("PATCH", "/api/v1/thumbnail"))
        .toEqual([{game: {tgame: {zoom: 150}}}]);
});

test("a number out of its range is refused aloud once confirmed", async () => {
    await renderPage();

    fireEvent.focus(zoomField());
    fireEvent.change(zoomField(), {target: {value: "0"}});
    await settle(1000);
    expect(screen.queryByText(
        i18n.t("thumbnail_out_of_range", {min: 1, max: 1000})))
        .not.toBeInTheDocument();

    fireEvent.keyDown(zoomField(), {key: "Enter"});
    await settle();

    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);
    expect(await screen.findByText(
        i18n.t("thumbnail_out_of_range", {min: 1, max: 1000})))
        .toBeInTheDocument();
    expect(zoomField()).toHaveValue("100");
});

test("an emptied number is not sent: leaving it shows the saved one",
     async () => {
    await renderPage();

    fireEvent.focus(zoomField());
    fireEvent.change(zoomField(), {target: {value: ""}});
    fireEvent.blur(zoomField());
    await settle();

    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);
    expect(screen.queryByText(
        i18n.t("thumbnail_out_of_range", {min: 1, max: 1000})))
        .not.toBeInTheDocument();
    expect(zoomField()).toHaveValue("100");
});

// The description read when the game changed, while a write was on its way
// (S4p review #5): the settings it brought are not put back over the write,
// but the new game's own are taken -- this page wrote none of them yet.
test("a new game's settings come with it, though a write raced the reading",
     async () => {
    await renderPage();
    await settle(PREVIEW_DEBOUNCE_MS);
    const release = hold("GET", "/api/v1/thumbnail");

    game = {codename: "ngame", name: "N Game"};
    setState({game});
    await settle(PREVIEW_DEBOUNCE_MS);  // the description is asked for
    fireEvent.click(checkbox("thumbnail_use_sponsors"));
    await settle();
    expect(writes("PATCH", "/api/v1/thumbnail"))
        .toEqual([{use_sponsors: false}]);
    // What the description says, read before the write.
    settings = {...settings, use_sponsors: true,
                game: {...settings.game, ngame: {
                    ...DEFAULTS.game.tgame, zoom: 250}}};
    release();
    await settle();
    await settle();

    expect(within(region("thumbnail_characters"))
        .getByText(i18n.t("thumbnail_characters_game", {game: "N Game"})))
        .toBeInTheDocument();
    expect(zoomField()).toHaveValue("250");
    expect(checkbox("thumbnail_use_sponsors")).not.toBeChecked();
});

test("an uploaded background is shown by its file name", async () => {
    settings = {...settings, background_source: "custom",
                background_name: "thumbnail_background_old.png"};
    await renderPage();
    expect(screen.getByText(i18n.t("thumbnail_background_file",
                                   {name: "thumbnail_background_old.png"})))
        .toBeInTheDocument();

    fireEvent.change(screen.getByTestId("thumbnail-background-file"),
                     {target: {files: [new File([new Uint8Array([137, 80])],
                                                "mine.png",
                                                {type: "image/png"})]}});
    for (let i = 0; i < 5; i++) {
        await settle(10);
    }

    const sent = writes("POST", "/api/v1/thumbnail/background");
    expect(sent).toHaveLength(1);
    expect(sent[0].image).toMatch(/^data:image\/png;base64,/);
    expect(screen.getByText(i18n.t("thumbnail_background_file",
                                   {name: "thumbnail_background_ab12.png"})))
        .toBeInTheDocument();
});

test("an image of your own with none uploaded asks for one first", async () => {
    await renderPage();
    const click = jest.spyOn(HTMLInputElement.prototype, "click")
        .mockImplementation(() => {});

    fireEvent.click(screen.getByRole("radio",
                                     {name: i18n.t("thumbnail_background_custom")}));
    await settle();

    expect(click).toHaveBeenCalledTimes(1);
    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);
    expect(screen.getByRole("radio", {name: i18n.t("thumbnail_background_default")}))
        .toBeChecked();
    click.mockRestore();
});

// ── the video title ───────────────────────────────────────────────────────

function titleField() {
    return within(region("thumbnail_video_title"))
        .getByRole("textbox", {name: i18n.t("thumbnail_video_title_field")});
}

test("the video title is shown, copied, and an edit is kept over a new one",
     async () => {
    await renderPage();
    expect(titleField()).toHaveValue("Cup | Alpha vs Bravo | TG");

    fireEvent.click(button(region("thumbnail_video_title"),
                           "thumbnail_video_title_copy"));
    await settle();
    expect(navigator.clipboard.writeText)
        .toHaveBeenCalledWith("Cup | Alpha vs Bravo | TG");

    // A new title replaces the one made before.
    videoTitle = "Cup | Alpha vs Charlie | TG";
    setState({score: {"1": {team: {"2": {player: {"1": {name: "Charlie"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    expect(titleField()).toHaveValue("Cup | Alpha vs Charlie | TG");

    // Not one edited here: that is the operator's, and is not saved.
    fireEvent.change(titleField(), {target: {value: "My title"}});
    videoTitle = "Cup | Delta vs Charlie | TG";
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Delta"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    expect(titleField()).toHaveValue("My title");
    expect(writes("PATCH", "/api/v1/thumbnail")).toEqual([]);

    // Emptied: the next one fills it.
    fireEvent.change(titleField(), {target: {value: ""}});
    videoTitle = "Cup | Echo vs Charlie | TG";
    setState({score: {"1": {team: {"1": {player: {"1": {name: "Echo"}}}}}}});
    await settle(PREVIEW_DEBOUNCE_MS);
    await settle();
    expect(titleField()).toHaveValue("Cup | Echo vs Charlie | TG");
});
