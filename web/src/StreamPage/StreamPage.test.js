import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {STATE_FIELD_DEBOUNCE_MS} from "../PanelShell/useStateField";
import {PanelShellContext} from "../PanelShell/context";
import StreamPage from "./index";
import {ChannelDialog} from "./Dialogs";

// The 配信設定 panel in React (ADR-005 S4e): it shows stream.* from the state
// and the channels / thumbnail options from /api/v1/stream, and writes
// through /api/v1/stream. The title follows core's rule (titleRule.ts): a
// typed title beats an auto title that lands meanwhile, not another
// writer's.

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const CHANNELS = [
    {name: "Main", url: "https://example.invalid/main"},
    {name: "Sub", url: "https://example.invalid/sub"},
];
const OPTIONS = {source: "banner", style: "center", overlayOrgIcon: false};

let revision = 0;

function setStream(stream) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {stream: {url: "", title: "", titleAuto: "", ...stream}},
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

// Write path suffix -> the status it answers (200 / 202 otherwise).
let refuse;
// Write path -> the answer it waits for (a promise the test settles).
let held;
let channels;
let options;

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

async function renderPage({desktop = false} = {}) {
    render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "stream", desktop, language: "en"}}>
                <StreamPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await screen.findByRole("region", {name: i18n.t("stream_destination")});
}

function region(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function channelSelect() {
    return within(region("stream_destination"))
        .getByRole("combobox", {name: i18n.t("stream_channel")});
}

function titleField() {
    return within(region("stream_title_card"))
        .getByRole("textbox", {name: i18n.t("stream_title_field")});
}

function button(scope, key) {
    return within(scope).getByRole("button", {name: i18n.t(key)});
}

async function choose(select, optionName) {
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole("option", {name: optionName}));
    await settle();
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

beforeEach(() => {
    jest.useFakeTimers();
    refuse = {};
    held = {};
    channels = CHANNELS.map((channel) => ({...channel}));
    options = {...OPTIONS};
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText: jest.fn(async () => {}),
                write: jest.fn(async () => {})},
        configurable: true,
    });
    setSecure(false);
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : {};
        const refused = Object.keys(refuse).find((suffix) => path.endsWith(suffix));
        if (method !== "GET" && held[path]) {
            return held[path];
        }
        if (method !== "GET" && refused) {
            return answer(refuse[refused], {ok: false,
                                            error: {code: "X", message: "no"}});
        }
        if (method === "GET") {
            if (path === "/api/v1/stream/channels") {
                return answer(200, {ok: true, data: {channels}});
            }
            if (path === "/api/v1/stream/thumbnail/options") {
                return answer(200, {ok: true, data: options});
            }
            return answer(404, {ok: false});
        }
        if (method === "POST" && path === "/api/v1/stream/channels") {
            channels = [...channels, body];
            return answer(200, {ok: true, data: {index: channels.length - 1,
                                                 channels}});
        }
        const channel = /^\/api\/v1\/stream\/channels\/(\d+)$/.exec(path);
        if (channel && method === "PUT") {
            channels = channels.map((c, i) => (i === Number(channel[1]) ? body : c));
            return answer(200, {ok: true, data: {channels}, revision: 1});
        }
        if (channel && method === "DELETE") {
            channels = channels.filter((_c, i) => i !== Number(channel[1]));
            return answer(200, {ok: true, data: {channels}, revision: 1});
        }
        if (path === "/api/v1/stream/org" && method === "DELETE") {
            options = {...options, source: "banner"};
            return answer(200, {ok: true, data: {thumbnailOptions: options},
                                revision: 1});
        }
        if (path === "/api/v1/stream/thumbnail/options") {
            options = {...options, ...body};
            return answer(200, {ok: true, data: options});
        }
        if (path === "/api/v1/stream/thumbnail") {
            return answer(202, {ok: true, data: {accepted: true}, revision: 1});
        }
        return answer(200, {ok: true, revision: 1});
    });
    setStream({});
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
    delete global.ClipboardItem;
});

// ── the page ──────────────────────────────────────────────────────────────

test("the four cards come up with the saved settings", async () => {
    await renderPage();

    ["stream_destination", "stream_title_card", "stream_org", "stream_thumbnail"]
        .forEach((key) => expect(region(key)).toBeInTheDocument());
    expect(reads("/api/v1/stream/channels")).toBe(1);
    expect(reads("/api/v1/stream/thumbnail/options")).toBe(1);
    expect(screen.getByRole("radio", {name: i18n.t("stream_thumbnail_source_banner")}))
        .toBeChecked();
});

test("mounting and state updates alone send nothing", async () => {
    await renderPage();
    setStream({url: CHANNELS[1].url, title: "Cup", titleAuto: "Cup"});
    await settle(STATE_FIELD_DEBOUNCE_MS);

    const sent = global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET");
    expect(sent).toEqual([]);
});

// ── 配信先 ─────────────────────────────────────────────────────────────────

test("the destination shows the channel its URL is, or the URL typed by hand", async () => {
    setStream({url: CHANNELS[1].url});
    await renderPage();
    const card = region("stream_destination");

    expect(channelSelect()).toHaveTextContent("Sub");
    expect(button(card, "stream_channel_edit")).toBeEnabled();

    setStream({url: "https://example.invalid/typed"});

    expect(channelSelect()).toHaveTextContent(i18n.t("stream_channel_none"));
    expect(button(card, "stream_channel_edit")).toBeDisabled();
    expect(within(card).getByRole("textbox", {name: i18n.t("stream_url")}))
        .toHaveValue("https://example.invalid/typed");
});

test("choosing a channel makes its URL the destination", async () => {
    await renderPage();

    await choose(channelSelect(), "Sub");

    expect(writes("PUT", "/api/v1/stream")).toEqual([{url: CHANNELS[1].url}]);
});

test("choosing none clears the destination", async () => {
    setStream({url: CHANNELS[0].url});
    await renderPage();

    await choose(channelSelect(), i18n.t("stream_channel_none"));

    expect(writes("PUT", "/api/v1/stream")).toEqual([{url: ""}]);
});

test("a URL typed by hand is sent", async () => {
    await renderPage();
    const field = within(region("stream_destination"))
        .getByRole("textbox", {name: i18n.t("stream_url")});

    fireEvent.focus(field);
    fireEvent.change(field, {target: {value: "https://example.invalid/x"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PUT", "/api/v1/stream"))
        .toEqual([{url: "https://example.invalid/x"}]);
});

test("a channel is added from the dialog and becomes the destination", async () => {
    await renderPage();
    fireEvent.click(button(region("stream_destination"), "stream_channel_add"));
    const dialog = await screen.findByRole("dialog");

    // Both are required, as in the Qt panel's dialog.
    fireEvent.click(button(dialog, "stream_save"));
    await settle();
    expect(within(dialog).getByText(i18n.t("stream_channel_required")))
        .toBeInTheDocument();
    expect(writes("POST", "/api/v1/stream/channels")).toEqual([]);

    fireEvent.change(within(dialog).getByRole("textbox", {name: i18n.t("stream_channel_name")}),
                     {target: {value: " Third "}});
    fireEvent.change(within(dialog).getByRole("textbox", {name: i18n.t("stream_channel_url")}),
                     {target: {value: "https://example.invalid/third"}});
    fireEvent.click(button(dialog, "stream_save"));
    await settle();
    await settle();

    expect(writes("POST", "/api/v1/stream/channels"))
        .toEqual([{name: "Third", url: "https://example.invalid/third"}]);
    expect(writes("PUT", "/api/v1/stream"))
        .toEqual([{url: "https://example.invalid/third"}]);
    await settle(1000);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("the channel the destination is can be edited", async () => {
    setStream({url: CHANNELS[0].url});
    await renderPage();
    fireEvent.click(button(region("stream_destination"), "stream_channel_edit"));
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByRole("textbox", {name: i18n.t("stream_channel_name")});
    expect(name).toHaveValue("Main");

    fireEvent.change(name, {target: {value: "Main 2"}});
    fireEvent.click(button(dialog, "stream_save"));
    await settle();
    await settle();

    expect(writes("PUT", "/api/v1/stream/channels/0"))
        .toEqual([{name: "Main 2", url: CHANNELS[0].url}]);
    expect(writes("PUT", "/api/v1/stream")).toEqual([]);
});

test("a channel is deleted only once the deletion is confirmed", async () => {
    setStream({url: CHANNELS[0].url});
    await renderPage();
    fireEvent.click(button(region("stream_destination"), "stream_channel_edit"));
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(button(dialog, "stream_channel_delete"));
    let confirm = await screen.findByRole("dialog",
                                          {name: i18n.t("stream_channel_delete_title")});
    fireEvent.click(button(confirm, "stream_cancel"));
    await settle(1000);
    expect(writes("DELETE", "/api/v1/stream/channels/0")).toEqual([]);

    fireEvent.click(button(dialog, "stream_channel_delete"));
    confirm = await screen.findByRole("dialog",
                                      {name: i18n.t("stream_channel_delete_title")});
    fireEvent.click(button(confirm, "stream_channel_delete"));
    await settle();
    await settle();

    expect(writes("DELETE", "/api/v1/stream/channels/0")).toEqual([{}]);
    await settle(1000);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a channel another client removed meanwhile is read again", async () => {
    setStream({url: CHANNELS[0].url});
    await renderPage();
    refuse["/api/v1/stream/channels/0"] = 404;
    fireEvent.click(button(region("stream_destination"), "stream_channel_edit"));
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(button(dialog, "stream_save"));
    await settle();
    await settle();

    expect(await screen.findByText(i18n.t("stream_channel_missing")))
        .toBeInTheDocument();
    expect(reads("/api/v1/stream/channels")).toBe(2);
    // The dialog stays: nothing was saved.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
});

// ── 配信タイトル ───────────────────────────────────────────────────────────

test("a typed title beats an auto title that lands meanwhile", async () => {
    setStream({title: "Cup", titleAuto: "Cup"});
    await renderPage();

    fireEvent.focus(titleField());
    fireEvent.change(titleField(), {target: {value: "Mine"}});
    setStream({title: "Cup ft. Ada", titleAuto: "Cup ft. Ada"});
    expect(titleField()).toHaveValue("Mine");
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PUT", "/api/v1/stream")).toEqual([{title: "Mine"}]);
});

test("another writer's own title beats the typing", async () => {
    setStream({title: "Cup", titleAuto: "Cup"});
    await renderPage();

    fireEvent.focus(titleField());
    fireEvent.change(titleField(), {target: {value: "Mine"}});
    setStream({title: "Web's", titleAuto: "Cup"});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    expect(writes("PUT", "/api/v1/stream")).toEqual([]);
    // Typing on: still not sent over the other writer's (S4e review).
    fireEvent.change(titleField(), {target: {value: "Mine 2"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    expect(writes("PUT", "/api/v1/stream")).toEqual([]);

    fireEvent.blur(titleField());
    await settle();
    expect(titleField()).toHaveValue("Web's");
    expect(writes("PUT", "/api/v1/stream")).toEqual([]);
});

test("a field that merely kept an old title never writes it back", async () => {
    setStream({title: "Cup", titleAuto: "Cup"});
    await renderPage();

    fireEvent.focus(titleField());
    fireEvent.change(titleField(), {target: {value: "Cupx"}});
    // Typed back to what was shown, while an auto title landed.
    setStream({title: "Cup ft. Ada", titleAuto: "Cup ft. Ada"});
    fireEvent.change(titleField(), {target: {value: "Cup"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);
    fireEvent.blur(titleField());
    await settle();

    expect(writes("PUT", "/api/v1/stream")).toEqual([]);
    expect(titleField()).toHaveValue("Cup ft. Ada");
});

test("the note tells an auto title from a typed one", async () => {
    setStream({title: "Cup", titleAuto: "Cup"});
    await renderPage();
    const card = region("stream_title_card");
    expect(within(card).getByText(i18n.t("stream_title_auto"))).toBeInTheDocument();

    setStream({title: "Mine", titleAuto: "Cup"});

    expect(within(card).getByText(i18n.t("stream_title_typed"))).toBeInTheDocument();
});

test("the title is copied as it is shown", async () => {
    setStream({title: " Cup ", titleAuto: " Cup "});
    await renderPage();

    fireEvent.click(button(region("stream_title_card"), "stream_copy"));
    await settle();

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Cup");
    expect(await screen.findByText(i18n.t("stream_copied"))).toBeInTheDocument();
});

// ── 配信団体アイコン / サムネイル ─────────────────────────────────────────

function pick(kind, file) {
    fireEvent.change(screen.getByTestId(`stream-org-${kind}-file`),
                     {target: {files: [file]}});
}

async function uploaded() {
    for (let i = 0; i < 5; i++) {
        await settle(10);
    }
}

test("a chosen image is sent as it is, as a data URL", async () => {
    await renderPage();

    pick("icon", new File([new Uint8Array([137, 80, 78, 71])], "icon.png",
                          {type: "image/png"}));
    await uploaded();

    const sent = writes("PUT", "/api/v1/stream/org/icon");
    expect(sent).toHaveLength(1);
    expect(sent[0].image).toBe("data:image/png;base64,iVBORw==");
});

test("an image over 20 MB is refused before it is sent", async () => {
    await renderPage();
    const file = new File(["x"], "huge.png", {type: "image/png"});
    Object.defineProperty(file, "size", {value: 20 * 1024 * 1024 + 1});

    pick("thumbnail", file);
    await uploaded();

    expect(writes("PUT", "/api/v1/stream/org/thumbnail")).toEqual([]);
    expect(await screen.findByText(i18n.t("stream_org_too_large", {max: 20})))
        .toBeInTheDocument();
});

test.each([
    [400, "stream_org_invalid"],
    [413, "stream_org_too_large"],
    [500, "stream_org_save_failed"],
])("an image the server refuses with %i says why", async (status, key) => {
    await renderPage();
    refuse["/api/v1/stream/org/icon"] = status;

    pick("icon", new File(["not an image"], "icon.png", {type: "image/png"}));
    await uploaded();

    expect(await screen.findByText(i18n.t(key, {max: 20}))).toBeInTheDocument();
});

test("an upload does not hold the page's other writes behind it", async () => {
    // S4e review: up to a minute on the LAN; the title is not queued
    // behind it.
    await renderPage();
    let answerUpload;
    held["/api/v1/stream/org/icon"] = new Promise((resolve) => {
        answerUpload = () => resolve(answer(200, {ok: true, revision: 1}));
    });
    pick("icon", new File([new Uint8Array([137, 80, 78, 71])], "icon.png",
                          {type: "image/png"}));
    await uploaded();
    expect(writes("PUT", "/api/v1/stream/org/icon")).toHaveLength(1);

    fireEvent.focus(titleField());
    fireEvent.change(titleField(), {target: {value: "Mine"}});
    await settle(STATE_FIELD_DEBOUNCE_MS);

    expect(writes("PUT", "/api/v1/stream")).toEqual([{title: "Mine"}]);
    answerUpload();
    await settle();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

test("the previews follow the versions in the state", async () => {
    await renderPage();
    const card = region("stream_org");
    expect(within(card).getAllByText(i18n.t("stream_org_none"))).toHaveLength(2);

    setStream({orgIconVersion: 3, orgThumbVersion: 7});

    expect(within(card).getByRole("img", {name: i18n.t("stream_org_icon")}))
        .toHaveAttribute("src", expect.stringMatching(/\/layout\/stream_org_icon\.png\?v=3$/));
    expect(within(card).getByRole("img", {name: i18n.t("stream_org_thumbnail")}))
        .toHaveAttribute("src",
                         expect.stringMatching(/\/layout\/stream_org_thumbnail\.png\?v=7$/));
});

test("the org images are removed only once that is confirmed", async () => {
    options = {...OPTIONS, source: "org"};
    await renderPage();
    const card = region("stream_org");
    expect(button(card, "stream_org_reset")).toBeDisabled();
    setStream({orgIconVersion: 3});

    fireEvent.click(button(card, "stream_org_reset"));
    let confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "stream_cancel"));
    await settle(1000);
    expect(writes("DELETE", "/api/v1/stream/org")).toEqual([]);

    fireEvent.click(button(card, "stream_org_reset"));
    confirm = await screen.findByRole("dialog");
    fireEvent.click(button(confirm, "stream_org_reset_yes"));
    await settle();
    await settle();

    expect(writes("DELETE", "/api/v1/stream/org")).toEqual([{}]);
    await settle(1000);
    // A background that was the org's is the banner again (core).
    expect(screen.getByRole("radio", {name: i18n.t("stream_thumbnail_source_banner")}))
        .toBeChecked();
});

// ── 配信サムネイル生成 ─────────────────────────────────────────────────────

test("an option is saved as it is chosen", async () => {
    await renderPage();

    fireEvent.click(screen.getByRole("radio", {name: i18n.t("stream_thumbnail_style_netflix")}));
    await settle();
    fireEvent.click(screen.getByRole("checkbox", {name: i18n.t("stream_thumbnail_overlay")}));
    await settle();

    expect(writes("PUT", "/api/v1/stream/thumbnail/options"))
        .toEqual([{style: "netflix"}, {overlayOrgIcon: true}]);
    expect(screen.getByRole("radio", {name: i18n.t("stream_thumbnail_style_netflix")}))
        .toBeChecked();
    expect(screen.getByRole("checkbox", {name: i18n.t("stream_thumbnail_overlay")}))
        .toBeChecked();
});

test("a run is asked for, then drawn from the state: running, done, failed", async () => {
    await renderPage();
    const card = region("stream_thumbnail");
    const generate = button(card, "stream_thumbnail_generate");

    fireEvent.click(generate);
    await settle();
    expect(writes("POST", "/api/v1/stream/thumbnail")).toEqual([{}]);

    setStream({thumbnail: {status: "running", path: "", error: "", revision: 1}});
    expect(button(card, "stream_thumbnail_generate")).toBeDisabled();
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("stream_thumbnail_running"));

    const path = "out/stream/thumbnail_20261031_120000.jpg";
    setStream({thumbnail: {status: "done", path, error: "", revision: 1}});
    expect(button(card, "stream_thumbnail_generate")).toBeEnabled();
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("stream_thumbnail_saved", {path}));
    expect(within(card).getByRole("img", {name: i18n.t("stream_thumbnail_preview")}))
        .toHaveAttribute("src", `http://localhost:5107/${path}?v=1`);

    setStream({thumbnail: {status: "error", path, error: "no_banner", revision: 2}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("stream_thumbnail_no_banner"));
    setStream({thumbnail: {status: "error", path, error: "disk full", revision: 3}});
    expect(within(card).getByRole("status"))
        .toHaveTextContent(i18n.t("stream_thumbnail_error", {error: "disk full"}));
});

test("a run already going is told, not reported as a failure", async () => {
    await renderPage();
    refuse["/api/v1/stream/thumbnail"] = 409;

    fireEvent.click(button(region("stream_thumbnail"), "stream_thumbnail_generate"));
    await settle();

    expect(await screen.findByText(i18n.t("stream_thumbnail_running")))
        .toBeInTheDocument();
    expect(screen.queryByText(i18n.t("request_failed"))).not.toBeInTheDocument();
});

const DONE = {status: "done", path: "out/stream/thumbnail_1.jpg", error: "",
              revision: 4};

test("a secure page copies the made thumbnail to the clipboard", async () => {
    setSecure(true);
    setStream({thumbnail: DONE});
    await renderPage();
    const card = region("stream_thumbnail");
    expect(within(card).queryByRole("link", {name: i18n.t("stream_thumbnail_open")}))
        .not.toBeInTheDocument();

    fireEvent.click(button(card, "stream_thumbnail_copy"));
    await settle();
    await settle();

    expect(navigator.clipboard.write).toHaveBeenCalledTimes(1);
    const [items] = navigator.clipboard.write.mock.calls[0];
    expect(Object.keys(items[0].items)).toEqual(["image/png"]);
    expect(await screen.findByText(i18n.t("stream_thumbnail_copied")))
        .toBeInTheDocument();
});

test("a secure page copies nothing before a thumbnail is made", async () => {
    setSecure(true);
    await renderPage();

    expect(button(region("stream_thumbnail"), "stream_thumbnail_copy")).toBeDisabled();
});

test("a page that cannot copy an image offers it to open or save", async () => {
    setSecure(false);
    setStream({thumbnail: DONE});
    await renderPage();
    const card = region("stream_thumbnail");

    expect(within(card).queryByRole("button", {name: i18n.t("stream_thumbnail_copy")}))
        .not.toBeInTheDocument();
    expect(within(card).getByRole("link", {name: i18n.t("stream_thumbnail_open")}))
        .toHaveAttribute("href", `http://localhost:5107/${DONE.path}?v=4`);
});

test("the desktop's view that cannot copy an image says so, with no link", async () => {
    // The view opens no new window: a link would do nothing (S4e review).
    setSecure(false);
    setStream({thumbnail: DONE});
    await renderPage({desktop: true});
    const card = region("stream_thumbnail");

    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    expect(button(card, "stream_thumbnail_copy")).toBeDisabled();
    expect(within(card).getByText(i18n.t("stream_thumbnail_copy_unavailable")))
        .toBeInTheDocument();
});

test("a channel dialog whose save throws can be used again", async () => {
    // S4e review: busy was left on, the buttons off for good.
    const onSave = jest.fn(async () => {
        throw new Error("no");
    });
    jest.spyOn(console, "error").mockImplementation(() => {});
    const onClose = jest.fn();
    render(<ChannelDialog open title="Add" initial={{name: "", url: ""}}
                          onSave={onSave} onClose={onClose}/>);
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox",
                                              {name: i18n.t("stream_channel_name")}),
                     {target: {value: "Main"}});
    fireEvent.change(within(dialog).getByRole("textbox",
                                              {name: i18n.t("stream_channel_url")}),
                     {target: {value: "https://example.invalid/main"}});

    fireEvent.click(button(dialog, "stream_save"));
    await settle();

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(button(dialog, "stream_save")).toBeEnabled();
    console.error.mockRestore();
});
