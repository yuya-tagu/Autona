import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaPlayersSlice, autonaStateSlice} from "../redux/autonaState";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import CommentaryPage, {
    LOAD_ATTEMPTS,
    LOAD_RETRY_MS,
    WRITE_TIMEOUT_MS,
    movedOrder,
} from "./index";

// The Commentary panel in React (ADR-005 S4b): it shows commentary.* from
// the state and writes through /api/v1/commentary. The input rules are
// useStateField's (focused field not overwritten, no send while an IME
// composes); a fifth caster refused by the server puts the checkbox back.

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const SLOTS = Array.from({length: 12}, (_, i) => String(i + 1));
const OPTIONS = {
    roles: ["実況", "解説"],
    icons: [{id: "mic", label: "マイク"}],
    slots: SLOTS,
    maxDisplay: 4,
};
const DESIGN = {
    id: "commentators", kind: "commentary", displayName: "Default",
    entry: "index.html",
};
const OTHER_DESIGN = {
    id: "custom/casters", kind: "commentary", displayName: "Custom",
    entry: "index.html",
};

function caster(fields = {}) {
    return {
        name: "", team: "", twitter: "", role: "", icon: "", note: "",
        display: 0, ...fields,
    };
}

function lineup(overrides = {}) {
    const slots = {};
    SLOTS.forEach((slot) => {
        slots[slot] = caster(overrides[slot]);
    });
    return slots;
}

let revision = 0;

function setState(commentary, rest = {}) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {commentary, tournamentInfo: {}, ...rest}, revision,
        }));
    });
}

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

// What the server answers to a write (PATCH / POST / PUT): 200 by default.
let writeStatus;
// GET path suffix -> how many more times it answers 500.
let failing;
// A write path suffix whose next request never answers.
let hang;

function writes(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && url.endsWith(path))
        .map(([, init]) => JSON.parse(init.body));
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

function renderBare() {
    render(
        <Provider store={autonaStore}>
            <CommentaryPage/>
        </Provider>,
    );
}

async function renderPage() {
    renderBare();
    await settle();
    await screen.findByRole("region", {name: i18n.t("commentary_slot", {slot: "1"})});
}

function card(slot) {
    return screen.getByRole("region", {name: i18n.t("commentary_slot", {slot})});
}

function nameField(slot) {
    return within(card(slot)).getByRole("combobox", {name: i18n.t("commentary_name")});
}

function displayBox(slot) {
    return within(card(slot)).getByRole("checkbox", {name: i18n.t("commentary_display")});
}

beforeEach(() => {
    jest.useFakeTimers();
    writeStatus = 200;
    failing = {};
    hang = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const failed = Object.keys(failing).find((path) => url.endsWith(path));
        if (method === "GET" && failed && failing[failed] > 0) {
            failing[failed] -= 1;
            return answer(500, {ok: false});
        }
        if (method !== "GET" && hang && url.endsWith(hang)) {
            hang = null;
            return new Promise(() => {});
        }
        if (method !== "GET") {
            if (url.endsWith("/api/v1/layouts/selected/commentary")) {
                return answer(writeStatus, {ok: true, data: OTHER_DESIGN});
            }
            return answer(writeStatus, writeStatus === 200
                ? {ok: true, revision: 1}
                : {ok: false, error: {code: "CONFLICT", message: "no"}});
        }
        if (url.endsWith("/api/v1/commentary/options")) {
            return answer(200, {ok: true, data: OPTIONS});
        }
        if (url.endsWith("/api/v1/layouts?kind=commentary")) {
            return answer(200, {ok: true, data: [DESIGN, OTHER_DESIGN]});
        }
        if (url.endsWith("/api/v1/layouts/selected/commentary")) {
            return answer(200, {ok: true, data: DESIGN});
        }
        return answer(404, {ok: false});
    });
    setState(lineup());
    act(() => {
        autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({}));
    });
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

test("the slots and the role suggestions come from the server", async () => {
    await renderPage();

    SLOTS.forEach((slot) => expect(card(slot)).toBeInTheDocument());
    const role = within(card("1")).getByRole("combobox", {name: i18n.t("commentary_role")});
    fireEvent.focus(role);
    fireEvent.keyDown(role, {key: "ArrowDown"});

    expect(screen.getByRole("option", {name: "実況"})).toBeInTheDocument();
    expect(screen.getByRole("option", {name: "解説"})).toBeInTheDocument();
});

test("the cards show the state as it changes", async () => {
    await renderPage();

    setState(lineup({2: {name: "Alice", display: 1}}));

    expect(nameField("2")).toHaveValue("Alice");
    expect(displayBox("2")).toBeChecked();
    expect(nameField("1")).toHaveValue("");
});

test("typing is sent to the slot's field after the debounce", async () => {
    await renderPage();

    fireEvent.focus(nameField("3"));
    fireEvent.change(nameField("3"), {target: {value: "Bob"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PATCH", "/api/v1/commentary/3")).toEqual([{name: "Bob"}]);
});

test("a state update does not overwrite the field being typed into", async () => {
    await renderPage();
    const field = nameField("1");

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ali"}});
    // Another writer changes the name while it is being typed.
    setState(lineup({1: {name: "Someone else"}}));

    expect(nameField("1")).toHaveValue("Ali");
});

test("nothing is sent while an IME composes", async () => {
    await renderPage();
    const field = nameField("1");

    fireEvent.focus(field);
    fireEvent.compositionStart(field);
    fireEvent.change(field, {target: {value: "やま"}});
    await settle(STATE_FIELD_DEBOUNCE_MS * 4);
    expect(writes("PATCH", "/api/v1/commentary/1")).toEqual([]);

    // Enter confirms the conversion: not a pick, not a send of its own.
    fireEvent.keyDown(field, {key: "Enter", keyCode: 229, which: 229});
    fireEvent.change(field, {target: {value: "山田"}});
    fireEvent.compositionEnd(field);
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PATCH", "/api/v1/commentary/1")).toEqual([{name: "山田"}]);
});

test("checking a caster shows them on stream", async () => {
    await renderPage();

    fireEvent.click(displayBox("2"));
    await settle();

    expect(writes("PATCH", "/api/v1/commentary/2")).toEqual([{display: true}]);
    expect(displayBox("2")).toBeChecked();
});

test("a fifth caster refused by the server puts the checkbox back", async () => {
    // Three shown here; the server already has a fourth (another writer).
    setState(lineup({1: {display: 1}, 2: {display: 1}, 3: {display: 1}}));
    await renderPage();
    writeStatus = 409;

    fireEvent.click(displayBox("5"));
    expect(displayBox("5")).toBeChecked();
    await settle();

    expect(writes("PATCH", "/api/v1/commentary/5")).toEqual([{display: true}]);
    expect(displayBox("5")).not.toBeChecked();
    expect(screen.getByText(i18n.t("commentary_display_limit", {max: 4})))
        .toBeInTheDocument();
});

test("a write that never answers is given up on and the next one goes", async () => {
    await renderPage();
    hang = "/api/v1/commentary/1";

    fireEvent.focus(nameField("1"));
    fireEvent.change(nameField("1"), {target: {value: "Ada"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    fireEvent.click(displayBox("2"));
    await settle(WRITE_TIMEOUT_MS - STATE_FIELD_DEBOUNCE_MS - 1);
    // Queued behind the write that does not answer.
    expect(writes("PATCH", "/api/v1/commentary/2")).toEqual([]);

    await settle(STATE_FIELD_DEBOUNCE_MS + 1);

    expect(screen.getByText(i18n.t("request_failed"))).toBeInTheDocument();
    expect(writes("PATCH", "/api/v1/commentary/2")).toEqual([{display: true}]);
});

test("a write refused for want of the access token is told once", async () => {
    await renderPage();
    writeStatus = 401;
    const told = () => screen.queryByText(i18n.t("commentary_token_required"));

    fireEvent.click(displayBox("1"));
    await settle();
    expect(told()).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button"));
    await settle(1000);
    expect(told()).toBeNull();

    writeStatus = 403;
    fireEvent.click(displayBox("2"));
    await settle();

    expect(writes("PATCH", "/api/v1/commentary/2")).toEqual([{display: true}]);
    expect(told()).toBeNull();
    expect(screen.queryByText(i18n.t("request_failed"))).toBeNull();
});

test("a load that fails is tried again", async () => {
    failing["/api/v1/commentary/options"] = LOAD_ATTEMPTS - 1;
    renderBare();
    await settle();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }

    expect(await screen.findByRole("region", {
        name: i18n.t("commentary_slot", {slot: "1"}),
    })).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("commentary_load_failed"))).toBeNull();
});

test("a load that keeps failing can be reloaded", async () => {
    failing["/api/v1/layouts?kind=commentary"] = LOAD_ATTEMPTS;
    renderBare();
    await settle();
    for (let attempt = 1; attempt < LOAD_ATTEMPTS; attempt++) {
        await settle(LOAD_RETRY_MS * attempt);
    }
    expect(screen.getByText(i18n.t("commentary_load_failed"))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("commentary_reload")}));
    await settle();

    expect(await screen.findByRole("region", {
        name: i18n.t("commentary_slot", {slot: "1"}),
    })).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("commentary_load_failed"))).toBeNull();
});

test("with four shown, the others cannot be checked", async () => {
    setState(lineup({1: {display: 1}, 2: {display: 1}, 3: {display: 1},
                     4: {display: 1}}));
    await renderPage();

    expect(displayBox("5")).toBeDisabled();
    expect(displayBox("4")).toBeEnabled();
});

function dragCard(from, onto) {
    const grip = within(card(from)).getByRole("button", {name: i18n.t("commentary_drag")});
    const dataTransfer = {setData: jest.fn(), setDragImage: jest.fn()};
    fireEvent.dragStart(grip, {dataTransfer});
    fireEvent.dragOver(card(onto), {dataTransfer});
    fireEvent.drop(card(onto), {dataTransfer});
    fireEvent.dragEnd(grip, {dataTransfer});
}

test("dragging a card reorders the casters on the server", async () => {
    await renderPage();

    // jsdom has no layout: a drop lands below the card it is dropped on.
    dragCard("1", "3");
    await settle();

    expect(writes("POST", "/api/v1/commentary/reorder")).toEqual([
        {order: ["2", "3", "1", ...SLOTS.slice(3)]},
    ]);
});

test("a card dropped where it was sends nothing", async () => {
    await renderPage();

    dragCard("2", "1");  // below card 1: where card 2 already is
    await settle();

    expect(writes("POST", "/api/v1/commentary/reorder")).toEqual([]);
});

test("a drag sends what is being typed to the slot it was typed into first", async () => {
    await renderPage();
    const field = nameField("1");

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Carol"}});
    field.focus();
    dragCard("1", "3");
    await settle();

    const sent = global.fetch.mock.calls
        .filter(([, init]) => init?.method && init.method !== "GET")
        .map(([url, init]) => [url.replace(/^.*\/api\/v1/, ""), JSON.parse(init.body)]);
    expect(sent[0]).toEqual(["/commentary/1", {name: "Carol"}]);
    expect(sent[1][0]).toEqual("/commentary/reorder");
});

test("the order moves one card and keeps the rest", () => {
    const slots = ["1", "2", "3", "4"];
    expect(movedOrder(slots, "3", 0)).toEqual(["3", "1", "2", "4"]);
    expect(movedOrder(slots, "1", 4)).toEqual(["2", "3", "4", "1"]);
    expect(movedOrder(slots, "2", 1)).toEqual(slots);
    expect(movedOrder(slots, "2", 2)).toEqual(slots);
});

function loadPlayerDb() {
    act(() => {
        autonaStore.dispatch(autonaPlayersSlice.actions.overwrite({
            "AB | Taro": {
                gamerTag: "Taro", prefix: "AB", name: "Taro Yamada",
                twitter: "taro", country_code: "JP", custom_textbox: "",
                prefixed_tag: "",
            },
            "Hanako": {
                gamerTag: "Hanako", prefix: "", name: "", twitter: "",
                country_code: "", custom_textbox: "", prefixed_tag: "",
            },
        }));
    });
}

test("the name field offers the player DB on focus", async () => {
    loadPlayerDb();
    await renderPage();

    fireEvent.focus(nameField("1"));

    expect(screen.getByRole("option", {name: "AB | Taro"})).toBeInTheDocument();
    expect(screen.getByRole("option", {name: "Hanako"})).toBeInTheDocument();
});

test("a player picked from the DB fills the caster and shows the tag", async () => {
    loadPlayerDb();
    await renderPage();
    const field = nameField("1");

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ta"}});
    fireEvent.click(await screen.findByRole("option", {name: "AB | Taro"}));
    expect(nameField("1")).toHaveValue("Taro");
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);

    expect(writes("POST", "/api/v1/commentary/1/player")).toEqual([
        expect.objectContaining({gamerTag: "Taro", prefix: "AB", twitter: "taro"}),
    ]);
    // The typed "Ta" is replaced by the pick, not sent.
    expect(writes("PATCH", "/api/v1/commentary/1")).toEqual([]);
});

test("Enter takes a candidate the keyboard moved to, not one the mouse is over", async () => {
    loadPlayerDb();
    await renderPage();
    const field = nameField("1");

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "Ta"}});
    // The mouse passing over a candidate highlights it; Enter does not take it.
    fireEvent.mouseMove(await screen.findByRole("option", {name: "AB | Taro"}));
    fireEvent.keyDown(field, {key: "Enter"});
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);

    expect(writes("POST", "/api/v1/commentary/1/player")).toEqual([]);
    expect(nameField("1")).toHaveValue("Ta");
    // The typed text is sent as typed, as without the popup.
    expect(writes("PATCH", "/api/v1/commentary/1")).toEqual([{name: "Ta"}]);

    // Moved to with the keyboard, it is. (The first ArrowDown opens the
    // list again: Enter closed it.)
    fireEvent.keyDown(field, {key: "ArrowDown"});
    fireEvent.keyDown(field, {key: "ArrowDown"});
    const option = await screen.findByRole("option", {name: "AB | Taro"});
    expect(field.getAttribute("aria-activedescendant")).toBe(option.id);
    fireEvent.keyDown(field, {key: "Enter"});
    await settle(STATE_FIELD_DEBOUNCE_MS * 2);

    expect(writes("POST", "/api/v1/commentary/1/player")).toEqual([
        expect.objectContaining({gamerTag: "Taro", prefix: "AB"}),
    ]);
    expect(nameField("1")).toHaveValue("Taro");
});

function obsUrl() {
    return screen.getByRole("textbox", {name: i18n.t("commentary_obs_url")});
}

function preview() {
    return screen.getByTitle(i18n.t("commentary_preview"));
}

test("the selected design is previewed and its URL is the one to copy", async () => {
    await renderPage();

    expect(obsUrl().value).toMatch(
        /^http:\/\/localhost:\d+\/layout\/commentators\/index\.html$/);
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/commentators\/index\.html$/));
});

test("a design picked is saved and then previewed", async () => {
    await renderPage();

    fireEvent.mouseDown(screen.getByRole("combobox", {name: i18n.t("commentary_design")}));
    fireEvent.click(screen.getByRole("option", {name: "Custom"}));
    await settle();

    expect(writes("PUT", "/api/v1/layouts/selected/commentary")).toEqual([
        {id: "custom/casters"},
    ]);
    expect(obsUrl().value).toMatch(/\/layout\/custom\/casters\/index\.html$/);
    expect(preview()).toHaveAttribute(
        "src", expect.stringMatching(/\/layout\/custom\/casters\/index\.html$/));
});

test("matching the tournament writes its colours to the commentary's", async () => {
    setState(lineup(), {
        tournamentInfo: {themeColor: "#112233", accentColor: "#445566"},
    });
    await renderPage();

    fireEvent.click(screen.getByRole("button", {
        name: i18n.t("commentary_color_match_tournament"),
    }));
    await settle();

    expect(writes("PATCH", "/api/v1/colors/commentary")).toEqual([
        {theme: "#112233", accent: "#445566"},
    ]);
});

test("matching the tournament waits for the tournament's colours", async () => {
    await renderPage();  // a state without them: not arrived yet

    expect(screen.getByRole("button", {
        name: i18n.t("commentary_color_match_tournament"),
    })).toBeDisabled();
});

test("the colours show the commentary's own, else the tournament's", async () => {
    setState(lineup(), {
        tournamentInfo: {themeColor: "#112233", accentColor: "#445566"},
        colors: {commentary: {themeColor: "#abcdef"}},
    });
    await renderPage();

    expect(screen.getByLabelText(i18n.t("commentary_color_theme")))
        .toHaveValue("#abcdef");
    expect(screen.getByLabelText(i18n.t("commentary_color_accent")))
        .toHaveValue("#445566");
});
