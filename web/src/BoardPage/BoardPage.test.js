import React from "react";
import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import {LOAD_ATTEMPTS, LOAD_RETRY_MS} from "../PanelShell/requests";
import DesignPane from "../CommentaryPage/DesignPane";
import BoardPage from "./index";

// The Board panel in React (ADR-005 S4s): the design from the scoreboards and
// the Designer projects (GET / PUT /api/v1/layouts/selected/board), the OBS
// files' paths (GET .../obs-path, this PC or the token), "show in Explorer"
// (POST ...:reveal), the winner popup on the preview (the page's own), the
// feature's colours and the Designer's new window.

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const SIMPLE = {id: "scoreboard_simple", kind: "scoreboard", displayName: "Simple",
                format: "1v1 / チーム", game: "汎用（全ゲーム）", entry: "index.html"};
const PLAIN = {id: "scoreboard_plain", kind: "scoreboard", displayName: "Plain",
               format: null, game: null, entry: "index.html"};
const MINE = {id: "custom/mine", kind: "designer", displayName: "Custom: 決勝",
              format: "<b>3v3</b>", game: "カスタム", entry: "index.html"};
const NEW = {id: "custom/new", kind: "designer", displayName: "Custom: 新作",
             format: "1v1", game: "カスタム", entry: "index.html"};

let designs;
let selected;
// The status GET .../obs-path / the list / every write answers instead of 200,
// when set.
let refuseFiles;
let refuseList;
let refuseWrite;
// What a GET of the list / a PUT or POST waits on, when set.
let holdRead;
let holdWrite;
// Run as GET .../obs-path is asked, once: what happens between the two reads.
let beforePaths;

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function refusal(status) {
    return answer(status, {ok: false, error: {code: "X", message: "no"}});
}

function calls(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && new URL(url).pathname === path)
        .map(([url, init]) => (init?.body ? JSON.parse(init.body)
                                          : new URL(url).search));
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
                value={{panel: "board", desktop: false, language: "en"}}>
                <BoardPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
}

function designSelect() {
    return screen.getByRole("combobox", {name: i18n.t("board_design")});
}

function popupSelect() {
    return screen.getByRole("combobox", {name: i18n.t("board_popup_label")});
}

function pick(select, name) {
    fireEvent.mouseDown(select);
    fireEvent.click(screen.getByRole("option", {name}));
}

function preview() {
    return screen.getByTitle(i18n.t("commentary_preview"));
}

function popupFrame() {
    return screen.queryByTitle(i18n.t("board_popup_preview"));
}

function designPath() {
    return screen.getByRole("textbox", {name: i18n.t("board_obs_design_path")});
}

function popupPath() {
    return screen.getByRole("textbox", {name: i18n.t("board_obs_popup_path")});
}

function fileButton(path, action) {
    return screen.getByRole("button", {name: `${i18n.t(path)}: ${i18n.t(action)}`});
}

async function comeBack(how) {
    await act(async () => {
        how();
    });
    await settle();
}

function showAgain() {
    return comeBack(() => {
        for (const state of ["hidden", "visible"]) {
            Object.defineProperty(document, "visibilityState", {
                configurable: true, get: () => state});
            document.dispatchEvent(new Event("visibilitychange"));
        }
    });
}

// The folder is read this long after the focus came back: the Qt panel's value.
const FOCUS_READ_DELAY_MS = 750;

async function focusAgain() {
    await comeBack(() => window.dispatchEvent(new Event("focus")));
    await settle(FOCUS_READ_DELAY_MS);
}

function reloadButton() {
    return screen.getByRole("button", {name: i18n.t("board_reload")});
}

// A promise and what ends it.
function gate() {
    let release;
    const promise = new Promise((resolve) => {
        release = resolve;
    });
    return {promise, release};
}

async function open(held) {
    await act(async () => {
        held.release();
    });
    await settle();
}

beforeEach(() => {
    jest.useFakeTimers();
    designs = [SIMPLE, PLAIN, MINE];
    selected = SIMPLE;
    refuseFiles = null;
    refuseList = null;
    refuseWrite = null;
    holdRead = null;
    holdWrite = null;
    beforePaths = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => "visible"});
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path} = new URL(url);
        if (method === "GET" && path === "/api/v1/layouts") {
            // The answer is what the server has when it is asked.
            const list = designs;
            if (holdRead) {
                await holdRead;
            }
            return refuseList ? refusal(refuseList)
                : answer(200, {ok: true, data: list});
        }
        if (method === "GET" && path === "/api/v1/layouts/selected/board") {
            const entry = selected;
            if (holdRead) {
                await holdRead;
            }
            if (refuseList) {
                return refusal(refuseList);
            }
            return entry ? answer(200, {ok: true, data: entry}) : refusal(404);
        }
        if (method === "GET"
                && path === "/api/v1/layouts/selected/board/obs-path") {
            if (beforePaths) {
                beforePaths();
                beforePaths = null;
            }
            return refuseFiles ? refusal(refuseFiles) : answer(200, {ok: true, data: {
                designId: selected ? selected.id : null,
                design: selected ? `C:\\Autona\\layout\\${selected.id}\\${selected.entry}` : null,
                winnerPopup: "C:\\Autona\\layout\\winner_popup\\index.html",
            }});
        }
        if (holdWrite && method !== "GET") {
            await holdWrite;
        }
        if (refuseWrite && method !== "GET") {
            return refusal(refuseWrite);
        }
        if (method === "PUT" && path === "/api/v1/layouts/selected/board") {
            const {id} = JSON.parse(init.body);
            selected = designs.find((item) => item.id === id);
            return answer(200, {ok: true, data: selected});
        }
        return answer(200, {ok: true, revision: 1});
    });
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                colors: {},
                tournamentInfo: {themeColor: "#181b21", accentColor: "#f0a500"},
            },
            revision: 1,
        }));
    });
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

// ── the design ───────────────────────────────────────────────────────────

test("the page lists both kinds and previews the kept design", async () => {
    await renderPage();

    expect(calls("GET", "/api/v1/layouts")).toEqual(["?kind=scoreboard,designer"]);
    expect(designSelect()).toHaveTextContent("Simple");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/scoreboard_simple\/index\.html$/));
    fireEvent.mouseDown(designSelect());
    expect(screen.getAllByRole("option").map((option) => option.textContent))
        .toEqual(["Simple", "Plain", "Custom: 決勝"]);
    expect(document.title).toBe(`Autona ${i18n.t("board_title")}`);
});

test("choosing the design shown keeps it: it may be a fallback", async () => {
    // A pick of the value it holds is nothing to the select (no change event);
    // the Qt panel's `activated`. The server writes only what is not kept yet.
    await renderPage();

    pick(designSelect(), "Simple");
    await settle();

    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([
        {id: "scoreboard_simple"}]);
    expect(designSelect()).toHaveTextContent("Simple");
    // Picking it again from another is an ordinary pick, sent once.
    pick(designSelect(), "Plain");
    await settle();
    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([
        {id: "scoreboard_simple"}, {id: "scoreboard_plain"}]);
});

test("a design path is encoded segment by segment", async () => {
    selected = MINE;
    await renderPage();

    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/custom\/mine\/index\.html$/));
});

test("the format and the game are the catalog's, as text", async () => {
    selected = MINE;
    await renderPage();

    expect(screen.getByText(i18n.t("board_meta_format", {value: "<b>3v3</b>"})))
        .toBeInTheDocument();
    expect(screen.getByText(i18n.t("board_meta_game", {value: "カスタム"})))
        .toBeInTheDocument();
    expect(document.querySelector("main b")).toBeNull();
});

test("a design that declares neither shows a dash for each", async () => {
    selected = PLAIN;
    await renderPage();

    expect(screen.getByText(i18n.t("board_meta_format", {value: "—"})))
        .toBeInTheDocument();
    expect(screen.getByText(i18n.t("board_meta_game", {value: "—"})))
        .toBeInTheDocument();
});

test("picking a design keeps it and follows the answer", async () => {
    await renderPage();

    pick(designSelect(), "Custom: 決勝");
    await settle();

    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([
        {id: "custom/mine"}]);
    expect(designSelect()).toHaveTextContent("Custom: 決勝");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/custom\/mine\/index\.html$/));
    expect(designPath()).toHaveValue("C:\\Autona\\layout\\custom/mine\\index.html");
});

test("a design refused is told and the list is read again", async () => {
    await renderPage();
    const reads = calls("GET", "/api/v1/layouts").length;

    refuseWrite = 400;
    pick(designSelect(), "Plain");
    await settle();

    expect(await screen.findByText(i18n.t("board_design_refused")))
        .toBeInTheDocument();
    expect(designSelect()).toHaveTextContent("Simple");
    expect(calls("GET", "/api/v1/layouts").length).toBe(reads + 1);
});

test("no design at all: the text, no preview, the popup's path still", async () => {
    designs = [];
    selected = null;
    await renderPage();

    expect(screen.getByText(i18n.t("board_no_design"))).toBeInTheDocument();
    expect(screen.queryByTitle(i18n.t("commentary_preview"))).toBeNull();
    expect(designPath()).toHaveValue("");
    expect(popupPath()).toHaveValue("C:\\Autona\\layout\\winner_popup\\index.html");

    // A design added since: the reload button is the way to it, with no focus.
    designs = [SIMPLE];
    selected = SIMPLE;
    fireEvent.click(reloadButton());
    await settle();
    expect(designSelect()).toHaveTextContent("Simple");
});

// ── the list follows the folder ──────────────────────────────────────────

test("shown again, the list is read again and the same design stays loaded", async () => {
    await renderPage();
    const frame = preview();

    designs = [SIMPLE, PLAIN, MINE, NEW];
    await showAgain();

    fireEvent.mouseDown(designSelect());
    expect(screen.getByRole("option", {name: "Custom: 新作"})).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), {key: "Escape"});
    expect(preview()).toBe(frame);
});

test("the window's focus back reads the list again", async () => {
    await renderPage();
    const frame = preview();

    designs = [SIMPLE, PLAIN, MINE, NEW];
    await focusAgain();

    fireEvent.mouseDown(designSelect());
    expect(screen.getByRole("option", {name: "Custom: 新作"})).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), {key: "Escape"});
    expect(preview()).toBe(frame);
});

test("the focus is read after a while, and flipping back and forth once", async () => {
    await renderPage();
    const reads = () => calls("GET", "/api/v1/layouts").length;
    const before = reads();

    await comeBack(() => window.dispatchEvent(new Event("focus")));
    await settle(FOCUS_READ_DELAY_MS - 1);
    expect(reads()).toBe(before);

    // Back again before it was read: the wait starts over.
    await comeBack(() => window.dispatchEvent(new Event("focus")));
    await settle(FOCUS_READ_DELAY_MS - 1);
    expect(reads()).toBe(before);

    await settle(1);
    expect(reads()).toBe(before + 1);
    await settle(FOCUS_READ_DELAY_MS * 2);
    expect(reads()).toBe(before + 1);
});

test("a focus is forgotten with the page", async () => {
    await renderPage();
    const before = calls("GET", "/api/v1/layouts").length;

    await comeBack(() => window.dispatchEvent(new Event("focus")));
    cleanup();
    await settle(FOCUS_READ_DELAY_MS);

    expect(calls("GET", "/api/v1/layouts").length).toBe(before);
});

test("a design missing from one read, mid-save, is back with the next", async () => {
    // Its project.json was locked: that read has the fallback, on screen only.
    selected = MINE;
    await renderPage();

    selected = SIMPLE;
    await focusAgain();
    expect(designSelect()).toHaveTextContent("Simple");

    selected = MINE;
    await focusAgain();

    expect(designSelect()).toHaveTextContent("Custom: 決勝");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/custom\/mine\/index\.html$/));
    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([]);
});

test("the kept design gone from the folder: the fallback is shown, its path too", async () => {
    selected = MINE;
    await renderPage();

    designs = [SIMPLE, PLAIN];
    selected = SIMPLE;
    await focusAgain();

    expect(designSelect()).toHaveTextContent("Simple");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/scoreboard_simple\/index\.html$/));
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_simple\\index.html");
    // The fallback is the server's to show, not the page's to keep.
    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([]);
});

test("a read that crossed a pick does not bring the old design back", async () => {
    await renderPage();

    // The read asks the server before the pick is written, and answers after.
    let release;
    holdRead = new Promise((resolve) => {
        release = resolve;
    });
    await focusAgain();
    holdRead = null;
    pick(designSelect(), "Plain");
    await settle();
    expect(designSelect()).toHaveTextContent("Plain");

    await act(async () => {
        release();
    });
    await settle();

    expect(designSelect()).toHaveTextContent("Plain");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/scoreboard_plain\/index\.html$/));
});

test("a read that started during a pick does not bring the old design back", async () => {
    // The order the read above does not catch: the read starts with the write on
    // its way, asks before it is done, and answers after it is.
    await renderPage();

    const write = gate();
    holdWrite = write.promise;
    pick(designSelect(), "Plain");
    await settle();

    const read = gate();
    holdRead = read.promise;
    await focusAgain();
    holdRead = null;

    await open(write);
    expect(designSelect()).toHaveTextContent("Plain");
    const frame = preview();
    const reads = calls("GET", "/api/v1/layouts/selected/board").length;

    await open(read);

    expect(designSelect()).toHaveTextContent("Plain");
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_plain\\index.html");
    // Dropped, not taken and put right by the paths' answer: the old design
    // never reached the frame.
    expect(preview()).toBe(frame);
    expect(calls("GET", "/api/v1/layouts/selected/board").length).toBe(reads);
});

test("after a refused pick the read that follows is taken", async () => {
    await renderPage();

    selected = PLAIN;
    refuseWrite = 500;
    pick(designSelect(), "Custom: 決勝");
    await settle();

    expect(designSelect()).toHaveTextContent("Plain");
});

test("the reload button reads everything again and loads the frames again", async () => {
    await renderPage();
    const frame = preview();
    const paths = calls("GET", "/api/v1/layouts/selected/board/obs-path").length;

    designs = [SIMPLE, PLAIN, MINE, NEW];
    fireEvent.click(reloadButton());
    await settle();

    expect(preview()).not.toBe(frame);
    expect(preview()).toHaveAttribute("src", frame.getAttribute("src"));
    fireEvent.mouseDown(designSelect());
    expect(screen.getByRole("option", {name: "Custom: 新作"})).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), {key: "Escape"});
    expect(calls("GET", "/api/v1/layouts/selected/board/obs-path").length)
        .toBe(paths + 1);
    expect(calls("PUT", "/api/v1/layouts/selected/board")).toEqual([]);
});

test("the reload button follows another selection, with no focus needed", async () => {
    await renderPage();

    selected = MINE;
    fireEvent.click(reloadButton());
    await settle();

    expect(designSelect()).toHaveTextContent("Custom: 決勝");
    expect(designPath()).toHaveValue("C:\\Autona\\layout\\custom/mine\\index.html");
});

// ── the preview's size ───────────────────────────────────────────────────

function measured(width, height) {
    const box = (name, value) => jest.spyOn(HTMLElement.prototype, name, "get")
        .mockReturnValue(value);
    return [box("clientWidth", width), box("clientHeight", height)];
}

function scaleOf(frame) {
    return window.getComputedStyle(frame).transform;
}

test("the preview keeps to the height as well as the width", async () => {
    // Wider than 16:9 by far: the height decides. The frame's border is left out.
    const spies = measured(1002, 272);
    await renderPage();

    expect(scaleOf(preview())).toBe("scale(0.25)");
    const style = window.getComputedStyle(preview().parentElement);
    expect(style.width).toBe("480px");
    expect(style.height).toBe("270px");
    spies.forEach((spy) => spy.mockRestore());
});

test("a tall pane leaves it to the width", async () => {
    const spies = measured(482, 900);
    await renderPage();

    expect(scaleOf(preview())).toBe("scale(0.25)");
    spies.forEach((spy) => spy.mockRestore());
});

test("without fit the pane is by width, as the other pages have it", async () => {
    const spies = measured(960, 100);
    render(<DesignPane designs={[SIMPLE]} design={SIMPLE} onSelect={() => undefined}/>);
    await settle();

    expect(scaleOf(preview())).toBe("scale(0.5)");
    const style = window.getComputedStyle(preview().parentElement);
    expect(style.width).toBe("100%");
    spies.forEach((spy) => spy.mockRestore());
});

// ── the winner popup on the preview ──────────────────────────────────────

test("hidden is what the popup's select says, not a blank", async () => {
    // The bug: "" is its value, and a select draws no label for an empty value
    // unless told to (displayEmpty). Found on the real app.
    await renderPage();

    expect(popupSelect()).toHaveTextContent(i18n.t("board_popup_hidden"));

    pick(popupSelect(), i18n.t("board_popup_winner"));
    await settle();
    expect(popupSelect()).toHaveTextContent(i18n.t("board_popup_winner"));

    pick(popupSelect(), i18n.t("board_popup_hidden"));
    await settle();
    expect(popupSelect()).toHaveTextContent(i18n.t("board_popup_hidden"));
});

test("the popup is the page's own: on top, across designs, and never written", async () => {
    await renderPage();
    const writes = () => global.fetch.mock.calls.filter(
        ([url, init]) => (init?.method ?? "GET") !== "GET"
            && !new URL(url).pathname.endsWith("/layouts/selected/board")).length;
    expect(popupFrame()).toBeNull();

    pick(popupSelect(), i18n.t("board_popup_winner"));
    await settle();
    expect(popupFrame()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/winner_popup\/index\.html#preview=winner$/));
    // After the design's frame: on top of it.
    expect(preview().compareDocumentPosition(popupFrame())
           & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const frame = popupFrame();

    // WINNER <-> CHAMPION: the fragment, not a new document.
    pick(popupSelect(), i18n.t("board_popup_champion"));
    await settle();
    expect(popupFrame()).toBe(frame);
    expect(frame.getAttribute("src")).toMatch(/#preview=champion$/);

    // Another design keeps it, as the same document.
    pick(designSelect(), "Plain");
    await settle();
    expect(popupFrame()).toBe(frame);

    pick(popupSelect(), i18n.t("board_popup_hidden"));
    await settle();
    expect(popupFrame()).toBeNull();
    expect(writes()).toBe(0);
});

test("neither the frames nor the select take the operator's tab or click", async () => {
    await renderPage();
    pick(popupSelect(), i18n.t("board_popup_winner"));
    await settle();

    for (const frame of [preview(), popupFrame()]) {
        expect(frame).toHaveAttribute("tabindex", "-1");
        expect(window.getComputedStyle(frame).pointerEvents).toBe("none");
    }
});

test("the picture is behind the frames, sized to the preview", async () => {
    await renderPage();

    const style = window.getComputedStyle(preview().parentElement);
    expect(style.backgroundImage).toContain("/layout/videoframe_1659973.png");
    expect(style.backgroundSize).toBe("100% 100%");
});

// ── the OBS files ────────────────────────────────────────────────────────

test("the paths are shown and copied", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        configurable: true, value: {writeText}});
    await renderPage();

    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_simple\\index.html");
    expect(designPath()).toHaveAttribute("readonly");
    expect(popupPath()).toHaveValue("C:\\Autona\\layout\\winner_popup\\index.html");

    fireEvent.click(fileButton("board_obs_popup_path", "commentary_obs_copy"));
    await settle();

    expect(writeText).toHaveBeenCalledWith(
        "C:\\Autona\\layout\\winner_popup\\index.html");
    expect(await screen.findByText(i18n.t("commentary_obs_copied")))
        .toBeInTheDocument();
});

test("the path never shows another design's while the new one's is read", async () => {
    await renderPage();

    let release;
    const gate = new Promise((resolve) => {
        release = resolve;
    });
    const real = global.fetch.getMockImplementation();
    global.fetch.mockImplementation(async (url, init) => {
        if (new URL(url).pathname.endsWith("/obs-path")) {
            await gate;
        }
        return real(url, init);
    });

    pick(designSelect(), "Plain");
    await settle();
    expect(designSelect()).toHaveTextContent("Plain");
    expect(designPath()).toHaveValue("");
    expect(fileButton("board_obs_design_path", "commentary_obs_copy")).toBeDisabled();
    // The popup's is the same whatever the design.
    expect(popupPath()).toHaveValue("C:\\Autona\\layout\\winner_popup\\index.html");

    await act(async () => {
        release();
    });
    await settle();
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_plain\\index.html");
});

test("another computer without the token: told, the page goes on", async () => {
    refuseFiles = 401;
    await renderPage();

    const card = screen.getByRole("region", {name: i18n.t("board_obs_title")});
    expect(within(card).getByText(i18n.t("board_obs_token_required")))
        .toBeInTheDocument();
    expect(designPath()).toHaveValue("");
    expect(fileButton("board_obs_design_path", "commentary_obs_copy")).toBeDisabled();
    expect(fileButton("board_obs_design_path", "board_obs_reveal")).toBeDisabled();
    expect(designSelect()).toHaveTextContent("Simple");
});

test("the paths failing otherwise are told as such", async () => {
    refuseFiles = 404;
    await renderPage();

    expect(screen.getByText(i18n.t("board_obs_load_failed"))).toBeInTheDocument();
});

test.each([
    ["shown again", () => showAgain()],
    ["the focus back", () => focusAgain()],
    ["the reload button", async () => {
        fireEvent.click(reloadButton());
        await settle();
    }],
])("paths that could not be read are read again on %s", async (_name, again) => {
    refuseFiles = 404;
    await renderPage();
    expect(screen.getByText(i18n.t("board_obs_load_failed"))).toBeInTheDocument();
    expect(designPath()).toHaveValue("");

    refuseFiles = null;
    await again();

    expect(screen.queryByText(i18n.t("board_obs_load_failed"))).toBeNull();
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_simple\\index.html");
});

test("the same design with another entry file: the path and the preview follow it", async () => {
    await renderPage();

    selected = {...SIMPLE, entry: "main.html"};
    await focusAgain();

    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_simple\\main.html");
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/scoreboard_simple\/main\.html$/));
});

test("paths answered for another design are not shown, and the selection is read again", async () => {
    await renderPage();
    const held = gate();
    let selectionReads = 0;
    const real = global.fetch.getMockImplementation();

    // Another client chooses after the page has read the selection, before it
    // reads the paths. The read of the selection this causes is held, to look.
    selected = SIMPLE;
    beforePaths = () => {
        selected = PLAIN;
        global.fetch.mockImplementation(async (url, init) => {
            if (new URL(url).pathname.endsWith("/layouts/selected/board")) {
                selectionReads += 1;
                await held.promise;
            }
            return real(url, init);
        });
    };
    await focusAgain();

    expect(selectionReads).toBe(1);
    expect(designSelect()).toHaveTextContent("Simple");
    expect(designPath()).toHaveValue("");
    expect(fileButton("board_obs_design_path", "board_obs_reveal")).toBeDisabled();

    await open(held);

    expect(selectionReads).toBe(1);
    expect(designSelect()).toHaveTextContent("Plain");
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_plain\\index.html");
});

test("an answer for another design reads the selection once, then agrees", async () => {
    await renderPage();
    selected = SIMPLE;
    beforePaths = () => {
        selected = PLAIN;
    };
    const reads = calls("GET", "/api/v1/layouts/selected/board").length;

    await focusAgain();
    await settle();

    expect(designSelect()).toHaveTextContent("Plain");
    // The focus's, and the one the answer for another design asked for.
    expect(calls("GET", "/api/v1/layouts/selected/board").length).toBe(reads + 2);
});

test("show in Explorer names the target and the design shown, never a path", async () => {
    await renderPage();

    fireEvent.click(fileButton("board_obs_design_path", "board_obs_reveal"));
    await settle();
    fireEvent.click(fileButton("board_obs_popup_path", "board_obs_reveal"));
    await settle();

    expect(calls("POST", "/api/v1/layouts/selected/board:reveal")).toEqual([
        {target: "design", designId: "scoreboard_simple"},
        {target: "winnerPopup"}]);
});

test("the design selected elsewhere: refused, told, and read again", async () => {
    await renderPage();
    selected = PLAIN;
    refuseWrite = 409;

    fireEvent.click(fileButton("board_obs_design_path", "board_obs_reveal"));
    await settle();

    expect(await screen.findByText(i18n.t("board_reveal_changed"))).toBeInTheDocument();
    expect(designSelect()).toHaveTextContent("Plain");
    expect(designPath()).toHaveValue(
        "C:\\Autona\\layout\\scoreboard_plain\\index.html");
});

test("one window at a time: the buttons wait for the answer", async () => {
    await renderPage();
    const held = gate();
    holdWrite = held.promise;
    const design = fileButton("board_obs_design_path", "board_obs_reveal");
    const popup = fileButton("board_obs_popup_path", "board_obs_reveal");

    fireEvent.click(design);
    await settle();
    expect(design).toBeDisabled();
    expect(popup).toBeDisabled();
    // What needs no window stays.
    expect(fileButton("board_obs_design_path", "commentary_obs_copy")).toBeEnabled();
    fireEvent.click(design);
    fireEvent.click(popup);
    await settle();
    expect(calls("POST", "/api/v1/layouts/selected/board:reveal")).toHaveLength(1);

    await open(held);

    expect(design).toBeEnabled();
    expect(popup).toBeEnabled();
});

test("the buttons come back after a refusal too", async () => {
    await renderPage();
    refuseWrite = 404;

    fireEvent.click(fileButton("board_obs_design_path", "board_obs_reveal"));
    await settle();

    expect(fileButton("board_obs_design_path", "board_obs_reveal")).toBeEnabled();
});

test.each([
    [403, "board_reveal_this_pc_only"],
    [404, "board_reveal_missing"],
    [500, "request_failed"],
])("show in Explorer refused with %i says why", async (status, message) => {
    await renderPage();
    refuseWrite = status;

    fireEvent.click(fileButton("board_obs_design_path", "board_obs_reveal"));
    await settle();

    expect(await screen.findByText(i18n.t(message))).toBeInTheDocument();
});

// ── the Designer ─────────────────────────────────────────────────────────

test("the Designer's button asks for exactly the URL the host keeps", async () => {
    const open = jest.spyOn(window, "open").mockImplementation(() => null);
    await renderPage();

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("board_designer_open")}));

    // src/AutonaWebPanelHost.py's _IsDesignerUrl.
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith("/web?view=designer", "_blank", "noopener");
    open.mockRestore();
});

// ── the colours ──────────────────────────────────────────────────────────

test("unset colours show the tournament's and writes go to the board's", async () => {
    await renderPage();
    const card = screen.getByRole("region", {name: i18n.t("board_colors")});
    const theme = within(card).getByLabelText(i18n.t("commentary_color_theme"));

    expect(theme).toHaveValue("#181b21");

    fireEvent.click(within(card).getByRole("button", {
        name: i18n.t("commentary_color_match_tournament")}));
    await settle();

    expect(calls("PATCH", "/api/v1/colors/board")).toEqual([
        {theme: "#181b21", accent: "#f0a500"}]);
});

test("the board's own colour wins over the tournament's", async () => {
    await renderPage();
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                colors: {board: {themeColor: "#112233", accentColor: "#445566"}},
                tournamentInfo: {themeColor: "#181b21", accentColor: "#f0a500"},
            },
            revision: 2,
        }));
    });
    await settle();

    const card = screen.getByRole("region", {name: i18n.t("board_colors")});
    expect(within(card).getByLabelText(i18n.t("commentary_color_theme")))
        .toHaveValue("#112233");
});

test("a write refused for the token is told once", async () => {
    await renderPage();
    refuseWrite = 401;
    const card = screen.getByRole("region", {name: i18n.t("board_colors")});
    const match = within(card).getByRole("button", {
        name: i18n.t("commentary_color_match_tournament")});

    fireEvent.click(match);
    await settle();
    fireEvent.click(match);
    await settle();

    expect(await screen.findAllByText(i18n.t("stream_token_required")))
        .toHaveLength(1);
});

// ── loading ──────────────────────────────────────────────────────────────

test("the first read failing fails the page, and its button reads again", async () => {
    refuseList = 500;
    await renderPage();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    expect(screen.getByText(i18n.t("board_load_failed"))).toBeInTheDocument();

    refuseList = null;
    fireEvent.click(screen.getByRole("button", {name: i18n.t("board_reload")}));
    await settle();

    expect(designSelect()).toHaveTextContent("Simple");
});

test("a later read failing keeps what the page shows, without a word", async () => {
    await renderPage();
    const frame = preview();

    refuseList = 500;
    await focusAgain();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    expect(screen.queryByText(i18n.t("board_load_failed"))).toBeNull();
    expect(preview()).toBe(frame);
    expect(designSelect()).toHaveTextContent("Simple");
});
