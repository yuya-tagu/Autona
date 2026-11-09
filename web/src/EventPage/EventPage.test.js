import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import {WRITE_TIMEOUT_MS} from "../PanelShell/requests";
import {PanelShellContext} from "../PanelShell/context";
import EventPage from "./index";
import {ENTRANTS_MAX, isSendableDay, parseEntrants} from "./Cards";

// The 大会情報 panel in React (ADR-005 S4g): it shows tournamentInfo.* --
// the tournament's URL and how its set / clear went among them (S4g review
// M1) -- and the game from the state, the games from GET /api/v1/games,
// and writes through /api/v1 (the tournament, its details, its colours,
// its icon / banner).

// CPU, not a wait (fake timers): up to 1.2 s a test in a whole `npm test`, the
// same shape as the Commentary page's, which went past the default 5 s there.
jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const EVENT_URL = "https://www.start.gg/tournament/autona-cup/event/singles";
const OTHER_URL = "https://www.start.gg/tournament/other-cup/event/doubles";
const GAMES = {
    ssbu: {name: "Super Smash Bros. Ultimate"},
    sf6: {name: "Street Fighter 6"},
};
// The state's game, the same object from one state to the next unless a
// test gives another (the page reads the games again when it changes).
const NO_GAME = {};
const LOADED = {status: "loaded", error: "", revision: 3};

let revision = 0;

function setState(tournamentInfo = {}, game = NO_GAME) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {
                tournamentInfo: {
                    tournamentName: "", eventName: "", address: "",
                    numEntrants: 0, startAtDate: null,
                    themeColor: "#181B21", accentColor: "#f0a500",
                    logoVersion: 5, bannerVersion: 0,
                    media: {status: "idle", error: "", revision: 0},
                    url: EVENT_URL,
                    load: LOADED,
                    ...tournamentInfo,
                },
                game,
            },
            revision,
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

// Write path suffix -> the status it answers (200 / 202 otherwise), or
// [status, the error's code].
let refuse;
// The revision POST / DELETE /api/v1/tournament answer (loadRevision).
let loadRevision;
// What POST /api/v1/tournament/media/fetch answers.
let fetchAnswer;

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
                value={{panel: "event", desktop: false, language: "en"}}>
                <EventPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await screen.findByRole("region", {name: i18n.t("event_tournament")});
}

function region(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function button(scope, key) {
    return within(scope).getByRole("button", {name: i18n.t(key)});
}

function textbox(key) {
    return screen.getByRole("textbox", {name: i18n.t(key)});
}

async function type(field, value) {
    fireEvent.focus(field);
    fireEvent.change(field, {target: {value}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
}

beforeEach(() => {
    jest.useFakeTimers();
    refuse = {};
    loadRevision = 4;
    fetchAnswer = () => answer(202, {ok: true, data: {
        accepted: true, operation: "tournament.media.fetch", queued: false},
        revision: 1});
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const path = new URL(url).pathname;
        const refused = Object.keys(refuse).find((suffix) => path.endsWith(suffix));
        if (method !== "GET" && refused) {
            const [status, code] = [].concat(refuse[refused], "X");
            return answer(status, {ok: false,
                                   error: {code, message: "no"}});
        }
        if (method === "GET") {
            if (path === "/api/v1/games") {
                return answer(200, {ok: true, data: GAMES});
            }
            return answer(404, {ok: false});
        }
        if (path === "/api/v1/tournament") {
            return answer(202, {ok: true, data: {
                accepted: true,
                operation: method === "POST" ? "tournament.load"
                    : "tournament.clear",
                loadRevision}, revision: 1});
        }
        if (path === "/api/v1/tournament/media/fetch") {
            return fetchAnswer();
        }
        return answer(200, {ok: true, revision: 1});
    });
    setState();
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

// ── the page ──────────────────────────────────────────────────────────────

test("the four cards come up, the URL field with the state's URL", async () => {
    await renderPage();

    ["event_tournament", "event_info", "event_colors", "event_media"]
        .forEach((key) => expect(region(key)).toBeInTheDocument());
    // The tournament is the state's: not read (S4g review M1).
    expect(reads("/api/v1/tournament")).toBe(0);
    expect(reads("/api/v1/games")).toBe(1);
    expect(textbox("event_url")).toHaveValue(EVENT_URL);
    expect(within(region("event_tournament")).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_loaded"));
    expect(document.title).toBe(`Autona ${i18n.t("event_title")}`);
});

test("mounting and state updates alone send nothing", async () => {
    await renderPage();
    setState({tournamentName: "Cup", numEntrants: 12,
              startAtDate: "2026-11-03"}, {codename: "ssbu"});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    await settle(WRITE_TIMEOUT_MS);

    const sent = global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET");
    expect(sent).toEqual([]);
    expect(reads("/api/v1/tournament")).toBe(0);
});

test("a page that cannot read the games says so and reads again", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    const real = global.fetch;
    global.fetch = jest.fn(async (url, init) => (
        new URL(url).pathname === "/api/v1/games"
            ? answer(404, {ok: false}) : real(url, init)));
    render(
        <Provider store={autonaStore}>
            <EventPage/>
        </Provider>,
    );
    await settle();
    expect(await screen.findByText(i18n.t("event_load_failed")))
        .toBeInTheDocument();

    global.fetch = real;
    fireEvent.click(screen.getByRole("button", {name: i18n.t("event_reload")}));
    await settle();
    expect(await screen.findByRole("region", {name: i18n.t("event_tournament")}))
        .toBeInTheDocument();
    console.error.mockRestore();
});

test("the games are read again when the state's game changes", async () => {
    // S4g review L8: a game loaded changes the state's game; the list read
    // at mount went stale.
    await renderPage();
    expect(reads("/api/v1/games")).toBe(1);

    setState({tournamentName: "Cup"});
    await settle();
    expect(reads("/api/v1/games")).toBe(1);

    setState({}, {codename: "ssbu"});
    await settle();
    expect(reads("/api/v1/games")).toBe(2);
});

test("the games are read again when the page is on screen again", async () => {
    await renderPage();
    const visibility = jest.spyOn(document, "visibilityState", "get");

    visibility.mockReturnValue("hidden");
    act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle();
    expect(reads("/api/v1/games")).toBe(1);

    visibility.mockReturnValue("visible");
    act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await settle();
    expect(reads("/api/v1/games")).toBe(2);
    visibility.mockRestore();
});

test("a list read already stays when reading it again fails", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage();
    const real = global.fetch;
    global.fetch = jest.fn(async (url, init) => (
        new URL(url).pathname === "/api/v1/games"
            ? answer(404, {ok: false}) : real(url, init)));

    setState({}, {codename: "ssbu"});
    await settle();

    expect(reads("/api/v1/games")).toBe(1);
    expect(screen.queryByText(i18n.t("event_load_failed"))).not.toBeInTheDocument();
    expect(region("event_info")).toBeInTheDocument();
    console.error.mockRestore();
});

// ── start.gg の大会 ─────────────────────────────────────────────────────────

test("setting a URL fetches the media too; the state tells how it goes", async () => {
    setState({url: "", load: {status: "idle", error: "", revision: 3}});
    await renderPage();
    const card = region("event_tournament");
    expect(button(card, "event_set")).toBeDisabled();

    fireEvent.change(textbox("event_url"), {target: {value: ` ${EVENT_URL} `}});
    fireEvent.click(button(card, "event_set"));
    await settle();

    expect(writes("POST", "/api/v1/tournament"))
        .toEqual([{url: EVENT_URL, fetchMedia: true}]);
    // Answered, the state not there yet (still the previous request's):
    // busy -- the previous result is not this one's.
    expect(button(card, "event_set")).toBeDisabled();
    expect(button(card, "event_clear")).toBeDisabled();

    setState({url: EVENT_URL,
              load: {status: "loading", error: "", revision: 4}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_loading"));
    expect(button(card, "event_set")).toBeDisabled();
    expect(button(card, "event_clear")).toBeEnabled();

    setState({url: EVENT_URL, load: {status: "loaded", error: "", revision: 4}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_loaded"));
    expect(button(card, "event_set")).toBeEnabled();
    expect(textbox("event_url")).toHaveValue(EVENT_URL);
    // Nothing is polled.
    await settle(WRITE_TIMEOUT_MS * 2);
    expect(reads("/api/v1/tournament")).toBe(0);
});

test("a state that never comes does not hold the buttons for ever", async () => {
    await renderPage();
    const card = region("event_tournament");

    fireEvent.click(button(card, "event_refetch"));
    await settle();
    expect(button(card, "event_set")).toBeDisabled();

    await settle(WRITE_TIMEOUT_MS);
    expect(button(card, "event_set")).toBeEnabled();
});

test("loading again forces the fetch", async () => {
    await renderPage();

    fireEvent.click(button(region("event_tournament"), "event_refetch"));
    await settle();

    expect(writes("POST", "/api/v1/tournament"))
        .toEqual([{url: EVENT_URL, force: true}]);
});

test.each([
    ["unresolved", "event_status_unresolved"],
    ["load_failed", "event_status_load_failed"],
])("a set that went wrong (%s) says why", async (error, key) => {
    await renderPage();

    setState({load: {status: "error", error, revision: 4}});

    const line = within(region("event_tournament")).getByRole("status");
    expect(line).toHaveTextContent(i18n.t(key));
});

test("a set / clear of another writer's shows as it goes", async () => {
    // The sidebar's dialog, another client, the launch's restore.
    await renderPage();
    const card = region("event_tournament");

    setState({url: OTHER_URL,
              load: {status: "resolving", error: "", revision: 9}});

    expect(textbox("event_url")).toHaveValue(OTHER_URL);
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_status_resolving"));
    expect(button(card, "event_set")).toBeDisabled();
});

test("another writer's URL drops what was typed over the old one", async () => {
    // S4g review M3: an old URL typed and then set after the sidebar set
    // another put the old tournament back.
    await renderPage();
    const card = region("event_tournament");
    const field = textbox("event_url");

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: `${EVENT_URL}/typed`}});
    // The state again, the URL unchanged: the typing stays.
    setState({tournamentName: "Cup"});
    expect(field).toHaveValue(`${EVENT_URL}/typed`);

    setState({url: OTHER_URL,
              load: {status: "loaded", error: "", revision: 4}});

    expect(field).toHaveValue(OTHER_URL);
    fireEvent.click(button(card, "event_set"));
    await settle();
    expect(writes("POST", "/api/v1/tournament"))
        .toEqual([{url: OTHER_URL, fetchMedia: true}]);
});

test.each([
    [400, "event_url_invalid"],
    [409, "event_busy"],
])("a set refused with %i says why", async (code, key) => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage();
    refuse["/api/v1/tournament"] = code;

    fireEvent.click(button(region("event_tournament"), "event_set"));
    await settle();

    expect(await screen.findByText(i18n.t(key))).toBeInTheDocument();
    // Refused: nothing awaited.
    expect(button(region("event_tournament"), "event_set")).toBeEnabled();
    console.error.mockRestore();
});

test("the tournament is disconnected only once that is confirmed", async () => {
    await renderPage();
    const card = region("event_tournament");

    fireEvent.click(button(card, "event_clear"));
    let confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "stream_cancel"));
    await settle(1000);
    expect(writes("DELETE", "/api/v1/tournament")).toEqual([]);

    fireEvent.click(button(card, "event_clear"));
    confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "event_clear_yes"));
    await settle();
    expect(writes("DELETE", "/api/v1/tournament")).toEqual([{}]);

    // The field empties with the state.
    setState({url: "", load: {status: "idle", error: "", revision: 4}});
    // The dialog's exit transition keeps the page aria-hidden until it ends.
    await settle(1000);

    expect(textbox("event_url")).toHaveValue("");
    expect(within(card).queryByRole("status")).not.toBeInTheDocument();
});

test("disconnecting drops what was typed, the URL empty already", async () => {
    // No tournament, a URL typed and refused: nothing changed, the typing
    // stays to be corrected. Disconnecting (the state's URL "" before and
    // after) empties the field.
    jest.spyOn(console, "error").mockImplementation(() => {});
    setState({url: "", load: {status: "idle", error: "", revision: 3}});
    await renderPage();
    const card = region("event_tournament");
    const field = textbox("event_url");
    refuse["/api/v1/tournament"] = 400;
    fireEvent.change(field, {target: {value: "https://example.invalid/x"}});
    fireEvent.click(button(card, "event_set"));
    await settle();
    expect(field).toHaveValue("https://example.invalid/x");

    // Another set of the same URL elsewhere: the field's value unchanged,
    // the typing kept.
    setState({url: "", load: {status: "idle", error: "", revision: 4}});
    expect(field).toHaveValue("https://example.invalid/x");

    delete refuse["/api/v1/tournament"];
    fireEvent.click(button(card, "event_clear"));
    fireEvent.click(button(await screen.findByRole("dialog"),
                           "event_clear_yes"));
    await settle();

    expect(writes("DELETE", "/api/v1/tournament")).toEqual([{}]);
    expect(field).toHaveValue("");
    console.error.mockRestore();
});

// ── 大会の詳細 ─────────────────────────────────────────────────────────────

test("each field writes its own key", async () => {
    await renderPage();

    await type(textbox("event_tournament_name"), "Autona Cup");
    await type(textbox("event_event_name"), "Singles");
    await type(textbox("event_address"), "Tokyo");
    await type(textbox("event_entrants"), "128");
    await type(screen.getByLabelText(i18n.t("event_date")), "2026-11-03");

    expect(writes("PATCH", "/api/v1/tournament/info")).toEqual([
        {tournamentName: "Autona Cup"}, {eventName: "Singles"},
        {address: "Tokyo"}, {numEntrants: 128}, {startAtDate: "2026-11-03"},
    ]);
});

test("the fields show the state, the day as the date input takes it", async () => {
    setState({tournamentName: "Cup", eventName: "Singles", address: "Tokyo",
              numEntrants: 64, startAtDate: "2026-11-03"});
    await renderPage();

    expect(textbox("event_tournament_name")).toHaveValue("Cup");
    expect(textbox("event_event_name")).toHaveValue("Singles");
    expect(textbox("event_address")).toHaveValue("Tokyo");
    expect(textbox("event_entrants")).toHaveValue("64");
    expect(screen.getByLabelText(i18n.t("event_date"))).toHaveValue("2026-11-03");
});

test("an entrant count that is not one is refused before it is sent", async () => {
    await renderPage();

    await type(textbox("event_entrants"), "12a");

    expect(writes("PATCH", "/api/v1/tournament/info")).toEqual([]);
    expect(await screen.findByText(i18n.t("event_entrants_invalid")))
        .toBeInTheDocument();
});

test("the entrant count is a whole number up to what start.gg reports", () => {
    // S4g review L7: v1 and the page take the same range.
    expect(ENTRANTS_MAX).toBe(999999);
    expect(parseEntrants(" 42 ")).toBe(42);
    expect(parseEntrants("0")).toBe(0);
    expect(parseEntrants("12000")).toBe(12000);
    expect(parseEntrants("999999")).toBe(999999);
    expect(parseEntrants("1000000")).toBeNull();
    expect(parseEntrants("")).toBeNull();
    expect(parseEntrants("-1")).toBeNull();
    expect(parseEntrants("1.5")).toBeNull();
});

test("an emptied entrant count is not sent, nor refused", async () => {
    setState({numEntrants: 64});
    await renderPage();
    const field = textbox("event_entrants");

    await type(field, "");
    fireEvent.blur(field);
    await settle();

    expect(writes("PATCH", "/api/v1/tournament/info")).toEqual([]);
    expect(screen.queryByText(i18n.t("event_entrants_invalid")))
        .not.toBeInTheDocument();
    expect(field).toHaveValue("64");
});

test("a day is sent once its year is a whole one from 2000 to 2999", () => {
    expect(isSendableDay("2026-11-03")).toBe(true);
    expect(isSendableDay("2000-01-01")).toBe(true);
    expect(isSendableDay("2999-12-31")).toBe(true);
    // A year typed digit by digit (S4g review L1).
    expect(isSendableDay("0002-11-03")).toBe(false);
    expect(isSendableDay("0020-11-03")).toBe(false);
    expect(isSendableDay("0202-11-03")).toBe(false);
    expect(isSendableDay("1999-12-31")).toBe(false);
    expect(isSendableDay("3000-01-01")).toBe(false);
    expect(isSendableDay("")).toBe(false);
});

test("a day still being typed is not sent; the whole day is", async () => {
    setState({startAtDate: "2026-11-03"});
    await renderPage();
    const date = screen.getByLabelText(i18n.t("event_date"));

    await type(date, "");
    for (const partial of ["0002-11-04", "0020-11-04", "0202-11-04"]) {
        fireEvent.change(date, {target: {value: partial}});
        await settle(STATE_FIELD_DEBOUNCE_MS);
    }
    expect(writes("PATCH", "/api/v1/tournament/info")).toEqual([]);
    expect(screen.queryByText(i18n.t("event_date_invalid")))
        .not.toBeInTheDocument();

    fireEvent.change(date, {target: {value: "2027-11-04"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    expect(writes("PATCH", "/api/v1/tournament/info"))
        .toEqual([{startAtDate: "2027-11-04"}]);
});

test("a day the server refuses says why", async () => {
    // This PC cannot make a time of it (S4g review L4).
    jest.spyOn(console, "error").mockImplementation(() => {});
    setState({startAtDate: "2026-11-03"});
    await renderPage();
    refuse["/api/v1/tournament/info"] = 400;

    await type(screen.getByLabelText(i18n.t("event_date")), "2038-01-19");

    expect(writes("PATCH", "/api/v1/tournament/info"))
        .toEqual([{startAtDate: "2038-01-19"}]);
    expect(await screen.findByText(i18n.t("event_date_invalid")))
        .toBeInTheDocument();
    console.error.mockRestore();
});

test("a game is chosen by its codename", async () => {
    setState({}, {codename: "ssbu"});
    await renderPage();
    const select = screen.getByRole("combobox", {name: i18n.t("event_game")});
    expect(select).toHaveTextContent("Super Smash Bros. Ultimate");

    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole("option", {name: "Street Fighter 6"}));
    await settle();

    expect(writes("POST", "/api/v1/game")).toEqual([{codename: "sf6"}]);
});

test("no game is shown as none", async () => {
    await renderPage();

    expect(screen.getByRole("combobox", {name: i18n.t("event_game")}))
        .toHaveTextContent(i18n.t("event_game_none"));
});

// ── 大会カラー ─────────────────────────────────────────────────────────────

test("a tournament colour is sent once picked, and reset to the defaults", async () => {
    await renderPage();
    const card = region("event_colors");
    const theme = within(card).getByLabelText(i18n.t("event_color_theme"));
    expect(theme).toHaveValue("#181b21");

    fireEvent.change(theme, {target: {value: "#123456"}});
    await settle();
    fireEvent.click(button(card, "event_colors_reset"));
    await settle();

    expect(writes("PATCH", "/api/v1/colors/tournament"))
        .toEqual([{theme: "#123456"}, {reset: true}]);
});

// ── 大会アイコン / バナー ─────────────────────────────────────────────────

function pick(kind, file) {
    fireEvent.change(screen.getByTestId(`event-media-${kind}-file`),
                     {target: {files: [file]}});
}

async function uploaded() {
    for (let i = 0; i < 5; i++) {
        await settle(10);
    }
}

test("the previews follow the versions, the default banner without one", async () => {
    await renderPage();
    const card = region("event_media");
    expect(within(card).getByRole("img", {name: i18n.t("event_media_icon")}))
        .toHaveAttribute("src", "http://localhost:5107/layout/logo.png?v=5");
    expect(within(card).getByRole("img", {name: i18n.t("event_media_banner")}))
        .toHaveAttribute("src",
                         "http://localhost:5107/assets/thumbnail_base/default_banner.png");
    expect(within(card).getByText(i18n.t("event_media_default_banner")))
        .toBeInTheDocument();

    setState({logoVersion: 6, bannerVersion: 9});

    expect(within(card).getByRole("img", {name: i18n.t("event_media_icon")}))
        .toHaveAttribute("src", "http://localhost:5107/layout/logo.png?v=6");
    expect(within(card).getByRole("img", {name: i18n.t("event_media_banner")}))
        .toHaveAttribute("src",
                         "http://localhost:5107/user_data/tournament_banner.png?v=9");
    expect(within(card).queryByText(i18n.t("event_media_default_banner")))
        .not.toBeInTheDocument();
});

test("a chosen image is sent as it is, as a data URL", async () => {
    await renderPage();

    pick("banner", new File([new Uint8Array([137, 80, 78, 71])], "b.png",
                            {type: "image/png"}));
    await uploaded();

    const sent = writes("PUT", "/api/v1/tournament/media/banner");
    expect(sent).toHaveLength(1);
    expect(sent[0].image).toBe("data:image/png;base64,iVBORw==");
});

test("an image over 20 MB is refused before it is sent", async () => {
    await renderPage();
    const file = new File(["x"], "huge.png", {type: "image/png"});
    Object.defineProperty(file, "size", {value: 20 * 1024 * 1024 + 1});

    pick("icon", file);
    await uploaded();

    expect(writes("PUT", "/api/v1/tournament/media/icon")).toEqual([]);
    expect(await screen.findByText(i18n.t("stream_org_too_large", {max: 20})))
        .toBeInTheDocument();
});

test.each([
    [400, "stream_org_invalid"],
    [413, "stream_org_too_large"],
    [500, "event_media_save_failed"],
])("an image the server refuses with %i says why", async (code, key) => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage();
    refuse["/api/v1/tournament/media/icon"] = code;

    pick("icon", new File(["not an image"], "icon.png", {type: "image/png"}));
    await uploaded();

    expect(await screen.findByText(i18n.t(key, {max: 20}))).toBeInTheDocument();
    console.error.mockRestore();
});

test("a download is asked for, then drawn from the state", async () => {
    await renderPage();
    const card = region("event_media");

    fireEvent.click(button(card, "event_media_fetch"));
    await settle();
    expect(writes("POST", "/api/v1/tournament/media/fetch")).toEqual([{}]);

    setState({media: {status: "running", error: "", revision: 1}});
    expect(button(card, "event_media_fetch")).toBeDisabled();
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_media_running"));

    setState({media: {status: "done", error: "", revision: 2}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_media_done"));

    for (const error of ["no_media", "fetch_failed", "save_failed",
                         "superseded", "cancelled"]) {
        setState({media: {status: "error", error, revision: 3}});
        expect(within(card).getByRole("status"))
            .toHaveTextContent(i18n.t(`event_media_${error}`));
    }
    setState({media: {status: "error", error: "disk full", revision: 4}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("event_media_error", {error: "disk full"}));
});

test("a download queued behind another tournament's says so", async () => {
    fetchAnswer = () => answer(202, {ok: true, data: {
        accepted: true, operation: "tournament.media.fetch", queued: true},
        revision: 1});
    await renderPage();

    fireEvent.click(button(region("event_media"), "event_media_fetch"));
    await settle();

    expect(await screen.findByText(i18n.t("event_media_queued")))
        .toBeInTheDocument();
});

test("a refused download says why by the answer's code", async () => {
    // S4g review L5: the 409's two reasons used to be told apart by the
    // state the page had, which a download just started elsewhere has not
    // reached yet.
    jest.spyOn(console, "error").mockImplementation(() => {});
    await renderPage();
    const card = region("event_media");

    refuse["/api/v1/tournament/media/fetch"] = [409, "NOT_READY"];
    fireEvent.click(button(card, "event_media_fetch"));
    await settle();
    expect(await screen.findByText(i18n.t("event_media_no_tournament")))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("event_media_running")))
        .not.toBeInTheDocument();

    // Running, though the state the page has does not say so yet.
    refuse["/api/v1/tournament/media/fetch"] = [409, "CONFLICT"];
    fireEvent.click(button(card, "event_media_fetch"));
    await settle();
    expect(await screen.findByText(i18n.t("event_media_running")))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
    console.error.mockRestore();
});

test("the icon / banner are set back only once that is confirmed", async () => {
    await renderPage();
    const card = region("event_media");

    fireEvent.click(button(card, "event_media_reset"));
    let confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "stream_cancel"));
    await settle(1000);
    expect(writes("DELETE", "/api/v1/tournament/media")).toEqual([]);

    fireEvent.click(button(card, "event_media_reset"));
    confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "event_media_reset_yes"));
    await settle();

    expect(writes("DELETE", "/api/v1/tournament/media")).toEqual([{}]);
});
