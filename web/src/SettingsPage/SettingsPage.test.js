import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {PanelShellContext} from "../PanelShell/context";
import SettingsPage from "./index";
import {joinShortcut, sharingActions, splitShortcut} from "./hotkeys";

// The settings page (ADR-005 S4af): it reads GET /api/v1/settings and saves
// each section with PATCH /api/v1/settings/<section>, showing what each
// answer says. Language and network changes are applied by a restart: the
// page says so and the app keeps running. A hotkey is composed (Ctrl / Alt
// / Shift and a key of the vocabulary) and saved with its row's button.

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const VOCABULARY = {
    modifiers: ["Ctrl", "Alt", "Shift"],
    keys: ["A", "B", "R", "S", "T", "X", "1", "2", "3", "4", "F5", "Enter",
           "Escape", "Space"],
};
const DEFAULT_KEYS = {
    load_set: "Ctrl+Shift+S",
    team1_score_up: "Ctrl+Shift+1",
    team1_score_down: "Ctrl+Shift+2",
    team2_score_up: "Ctrl+Shift+3",
    team2_score_down: "Ctrl+Shift+4",
    reset_scores: "Ctrl+Shift+R",
    swap_teams: "Ctrl+Shift+X",
    refresh_phase_group: "Ctrl+Shift+T",
    apply_changes: "Ctrl+Shift+A",
};
const DEFAULTS = {
    general: {
        profanity_filter: true, disable_autoupdate: true,
        disable_scoreupdate: false, disable_export: false,
        disable_overwrite: false, team_1_default_color: "#fe3636",
        team_2_default_color: "#2e89ff",
    },
    network: {port: 5107, listenLan: false},
    language: {program: "default", gameAsset: "default", fgTerm: "default"},
    hotkeys: {enabled: true},
};

let server;
// {status, code} every write of `section` answers instead, when set.
let refuse;

function freshServer() {
    return {
        general: {...DEFAULTS.general},
        network: {port: 5107, listenLan: false,
                  listening: {port: 5107, lan: false}, restartPending: false},
        language: {program: "default", gameAsset: "default", fgTerm: "default",
                   choices: [
                       {code: "en", name: "English", englishName: "English"},
                       {code: "ja", name: "日本語", englishName: "Japanese"},
                   ],
                   restartPending: false},
        hotkeys: {
            enabled: true,
            keys: Object.fromEntries(Object.entries(DEFAULT_KEYS).map(
                ([action, value]) => [action, {value, default: value,
                                               valid: true, warning: null}])),
            vocabulary: VOCABULARY,
            applied: true,
        },
        defaults: DEFAULTS,
        loadFailed: false,
        movedAside: null,
    };
}

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        // A copy: the page must not hold the server's own object.
        json: async () => JSON.parse(JSON.stringify(body)),
        text: async () => JSON.stringify(body),
    };
}

function writes(section) {
    return global.fetch.mock.calls
        .filter(([url, init]) => init?.method === "PATCH"
            && new URL(url).pathname === `/api/v1/settings/${section}`)
        .map(([, init]) => JSON.parse(init.body));
}

function reads() {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "GET"
            && new URL(url).pathname === "/api/v1/settings").length;
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
                value={{panel: "settings", desktop: true, language: "en"}}>
                <SettingsPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();
    await screen.findByRole("region", {name: i18n.t("settings_hotkeys")});
}

function card(key) {
    return screen.getByRole("region", {name: i18n.t(key)});
}

function hotkeyRow(action) {
    return screen.getByRole("region", {
        name: i18n.t(`settings_hotkey_action_${action}`)});
}

function pickIn(container, comboName, optionName) {
    fireEvent.mouseDown(within(container).getByRole("combobox",
                                                    {name: comboName}));
    fireEvent.click(screen.getByRole("option", {name: optionName}));
}

beforeEach(() => {
    jest.useFakeTimers();
    server = freshServer();
    refuse = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const {pathname: path} = new URL(url);
        if (method === "GET" && path === "/api/v1/settings") {
            return answer(200, {ok: true, data: server});
        }
        const section = path.replace("/api/v1/settings/", "");
        if (method !== "PATCH" || !(section in server)) {
            return answer(404, {ok: false, error: {code: "NOT_FOUND"}});
        }
        if (refuse && refuse.section === section) {
            return answer(refuse.status, {ok: false, error: {
                code: refuse.code, message: "no"}});
        }
        const body = JSON.parse(init.body);
        server.loadFailed = false;
        if (section === "hotkeys") {
            if ("enabled" in body) {
                server.hotkeys.enabled = body.enabled;
            }
            const warnings = [];
            for (const [action, value] of Object.entries(body.keys ?? {})) {
                const warning = value === "Shift+A" ? "shift_only" : null;
                server.hotkeys.keys[action] = {
                    ...server.hotkeys.keys[action], value, warning};
                if (warning) {
                    warnings.push({action, code: warning});
                }
            }
            return answer(200, {ok: true, data: {...server.hotkeys, warnings}});
        }
        Object.assign(server[section], body);
        if (section === "network") {
            server.network.restartPending = server.network.port !== 5107
                || server.network.listenLan !== false;
        }
        if (section === "language") {
            server.language.restartPending = ["program", "gameAsset", "fgTerm"]
                .some((field) => server.language[field] !== "default");
        }
        return answer(200, {ok: true, data: server[section]});
    });
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

test("the four sections show what the server holds", async () => {
    await renderPage();

    const general = card("settings_general");
    expect(within(general).getByRole("checkbox", {
        name: i18n.t("settings_general_profanity_filter")})).toBeChecked();
    // The table's default, not the Qt window's unticked box (S4af).
    expect(within(general).getByRole("checkbox", {
        name: i18n.t("settings_general_disable_autoupdate")})).toBeChecked();
    expect(within(general).getByRole("checkbox", {
        name: i18n.t("settings_general_disable_overwrite")})).not.toBeChecked();
    expect(general).toHaveTextContent(
        i18n.t("settings_general_disable_autoupdate_note"));
    const network = card("settings_network");
    expect(within(network).getByRole("textbox", {
        name: i18n.t("settings_network_port")})).toHaveValue("5107");
    expect(network).toHaveTextContent(i18n.t("settings_network_listening", {
        port: 5107, scope: i18n.t("settings_network_scope_local")}));
    expect(within(card("settings_language")).getByRole("combobox", {
        name: i18n.t("settings_language_program")}))
        .toHaveTextContent(i18n.t("settings_language_system"));
    expect(within(hotkeyRow("team1_score_up"))
        .getByLabelText(i18n.t("settings_hotkey_current", {
            label: i18n.t("settings_hotkey_action_team1_score_up")})))
        .toHaveTextContent("Ctrl+Shift+1");
    expect(screen.queryByText(i18n.t("settings_network_restart"))).toBeNull();
    expect(screen.queryByText(i18n.t("settings_language_restart"))).toBeNull();
    expect(screen.queryByText(i18n.t("settings_hotkeys_not_applied"))).toBeNull();
});

test("the hotkeys are in the Qt window's order", async () => {
    await renderPage();

    const labels = within(card("settings_hotkeys")).getAllByRole("region")
        .map((region) => region.getAttribute("aria-label"));
    expect(labels).toEqual(Object.keys(DEFAULT_KEYS).map(
        (action) => i18n.t(`settings_hotkey_action_${action}`)));
});

test("a general switch is saved at once", async () => {
    await renderPage();

    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_disable_overwrite")}));
    await settle();

    expect(writes("general")).toEqual([{disable_overwrite: true}]);
    expect(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_disable_overwrite")})).toBeChecked();
});

test("a colour's Default button saves the default, and is off at the default", async () => {
    server.general.team_1_default_color = "#123456";
    await renderPage();
    const label = i18n.t("settings_general_team_1_default_color");
    const reset = screen.getByRole("button", {
        name: i18n.t("settings_default_for", {label})});

    expect(screen.getByRole("button", {name: i18n.t("settings_default_for", {
        label: i18n.t("settings_general_team_2_default_color")})})).toBeDisabled();
    fireEvent.click(reset);
    await settle();

    expect(writes("general")).toEqual([{team_1_default_color: "#fe3636"}]);
    expect(reset).toBeDisabled();
});

test("a port is checked, saved, and told to need a restart", async () => {
    await renderPage();
    const network = card("settings_network");
    const port = within(network).getByRole("textbox", {
        name: i18n.t("settings_network_port")});
    const apply = within(network).getByRole("button", {
        name: i18n.t("settings_network_port_apply")});

    expect(apply).toBeDisabled();
    fireEvent.change(port, {target: {value: "70000"}});
    expect(network).toHaveTextContent(i18n.t("settings_network_port_invalid",
                                             {min: 1, max: 65535}));
    expect(apply).toBeDisabled();
    fireEvent.change(port, {target: {value: "6000"}});
    fireEvent.click(apply);
    await settle();

    expect(writes("network")).toEqual([{port: 6000}]);
    expect(network).toHaveTextContent(i18n.t("settings_network_restart"));
});

test("LAN access not decided yet is shown unticked, with a note", async () => {
    server.network.listenLan = null;
    await renderPage();

    const network = card("settings_network");
    expect(within(network).getByRole("checkbox", {
        name: i18n.t("settings_network_lan")})).not.toBeChecked();
    expect(network).toHaveTextContent(i18n.t("settings_network_lan_undecided"));
    // Not decided is the default: "Default" would save false, and the
    // desktop would no longer ask at its start.
    expect(within(network).getByRole("button", {name: i18n.t(
        "settings_default_for", {label: i18n.t("settings_network_lan")})}))
        .toBeDisabled();
});

test("a language change is saved and told to need a restart; the page stays", async () => {
    await renderPage();

    pickIn(card("settings_language"), i18n.t("settings_language_program"),
           "日本語 / Japanese");
    await settle();

    expect(writes("language")).toEqual([{program: "ja"}]);
    expect(card("settings_language")).toHaveTextContent(
        i18n.t("settings_language_restart"));
    expect(card("settings_hotkeys")).toBeInTheDocument();
});

test("a hotkey is composed and saved by its row's button", async () => {
    await renderPage();
    const row = hotkeyRow("team1_score_up");
    const save = within(row).getByRole("button", {name: i18n.t(
        "settings_hotkey_save_for",
        {label: i18n.t("settings_hotkey_action_team1_score_up")})});

    expect(save).toBeDisabled();
    fireEvent.click(within(row).getByRole("checkbox", {name: "Shift"}));
    fireEvent.click(within(row).getByRole("checkbox", {name: "Alt"}));
    pickIn(row, i18n.t("settings_hotkey_key"), "F5");
    expect(writes("hotkeys")).toEqual([]);
    fireEvent.click(save);
    await settle();

    expect(writes("hotkeys")).toEqual([{keys: {team1_score_up: "Ctrl+Alt+F5"}}]);
    expect(within(row).getByLabelText(i18n.t("settings_hotkey_current", {
        label: i18n.t("settings_hotkey_action_team1_score_up")})))
        .toHaveTextContent("Ctrl+Alt+F5");
    expect(save).toBeDisabled();
});

test("no key is no shortcut", async () => {
    await renderPage();
    const row = hotkeyRow("swap_teams");

    pickIn(row, i18n.t("settings_hotkey_key"), i18n.t("settings_hotkey_none"));
    fireEvent.click(within(row).getByRole("button", {name: i18n.t(
        "settings_hotkey_save_for",
        {label: i18n.t("settings_hotkey_action_swap_teams")})}));
    await settle();

    expect(writes("hotkeys")).toEqual([{keys: {swap_teams: ""}}]);
    expect(within(row).getByLabelText(i18n.t("settings_hotkey_current", {
        label: i18n.t("settings_hotkey_action_swap_teams")})))
        .toHaveTextContent(i18n.t("settings_hotkey_none"));
});

test("a hotkey's Default button saves its default", async () => {
    server.hotkeys.keys.load_set.value = "Ctrl+B";
    await renderPage();

    fireEvent.click(screen.getByRole("button", {name: i18n.t(
        "settings_default_for",
        {label: i18n.t("settings_hotkey_action_load_set")})}));
    await settle();

    expect(writes("hotkeys")).toEqual([{keys: {load_set: "Ctrl+Shift+S"}}]);
});

test("a conflict names the action that has the shortcut", async () => {
    await renderPage();
    refuse = {section: "hotkeys", status: 409, code: "CONFLICT"};
    const row = hotkeyRow("team1_score_up");

    pickIn(row, i18n.t("settings_hotkey_key"), "2");
    fireEvent.click(within(row).getByRole("button", {name: i18n.t(
        "settings_hotkey_save_for",
        {label: i18n.t("settings_hotkey_action_team1_score_up")})}));
    await settle();

    expect(await screen.findByText(i18n.t("settings_hotkey_conflict", {
        shortcut: "Ctrl+Shift+2",
        others: i18n.t("settings_hotkey_action_team1_score_down")})))
        .toBeInTheDocument();
    // The composition stays, to be changed.
    expect(within(row).getByRole("combobox", {
        name: i18n.t("settings_hotkey_key")})).toHaveTextContent("2");
});

test("a shortcut with Shift alone is saved and warned about", async () => {
    await renderPage();
    const row = hotkeyRow("apply_changes");

    fireEvent.click(within(row).getByRole("checkbox", {name: "Ctrl"}));
    fireEvent.click(within(row).getByRole("button", {name: i18n.t(
        "settings_hotkey_save_for",
        {label: i18n.t("settings_hotkey_action_apply_changes")})}));
    await settle();

    const words = i18n.t("settings_hotkey_warning_shift_only",
                         {shortcut: "Shift+A"});
    expect(writes("hotkeys")).toEqual([{keys: {apply_changes: "Shift+A"}}]);
    // The snackbar, and the row's own note.
    expect(screen.getAllByText(words)).toHaveLength(2);
});

test("the hotkeys switch is saved", async () => {
    await renderPage();

    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_hotkeys_enabled")}));
    await settle();

    expect(writes("hotkeys")).toEqual([{enabled: false}]);
});

test("the hotkeys switch's Default sends the default the server gives", async () => {
    server.defaults = {...DEFAULTS, hotkeys: {enabled: false}};
    await renderPage();
    const reset = screen.getByRole("button", {name: i18n.t(
        "settings_default_for", {label: i18n.t("settings_hotkeys_enabled")})});

    expect(reset).not.toBeDisabled();
    fireEvent.click(reset);
    await settle();

    expect(writes("hotkeys")).toEqual([{enabled: false}]);
    expect(reset).toBeDisabled();
});

function hotkeySave(row, action) {
    return within(row).getByRole("button", {name: i18n.t(
        "settings_hotkey_save_for",
        {label: i18n.t(`settings_hotkey_action_${action}`)})});
}

test("saving one row keeps another row's composition", async () => {
    await renderPage();
    const composing = hotkeyRow("team1_score_up");
    const saved = hotkeyRow("team2_score_up");

    fireEvent.click(within(composing).getByRole("checkbox", {name: "Alt"}));
    pickIn(saved, i18n.t("settings_hotkey_key"), "F5");
    fireEvent.click(hotkeySave(saved, "team2_score_up"));
    await settle();
    // The switch answers a new hotkeys section as well.
    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_hotkeys_enabled")}));
    await settle();

    expect(writes("hotkeys")).toEqual([
        {keys: {team2_score_up: "Ctrl+Shift+F5"}}, {enabled: false}]);
    expect(within(composing).getByRole("checkbox", {name: "Alt"})).toBeChecked();
    expect(hotkeySave(composing, "team1_score_up")).not.toBeDisabled();
    expect(hotkeySave(saved, "team2_score_up")).toBeDisabled();
});

test("a saved value that is not a string says it registers nothing", async () => {
    server.hotkeys.keys.load_set = {
        value: null, default: "Ctrl+Shift+S", valid: false, warning: null};
    await renderPage();
    const row = hotkeyRow("load_set");

    expect(row).toHaveTextContent(i18n.t("settings_hotkey_unreadable"));
    expect(row).toHaveTextContent(i18n.t("settings_hotkey_invalid"));
    expect(hotkeySave(row, "load_set")).toBeDisabled();
});

test.each(["ctrl+shift+s", "Shift+Ctrl+S"])(
    "a shortcut spelled otherwise (%s) is not offered to save as it is", async (value) => {
        server.hotkeys.keys.load_set.value = value;
        await renderPage();
        const row = hotkeyRow("load_set");

        expect(within(row).getByRole("checkbox", {name: "Ctrl"})).toBeChecked();
        expect(within(row).getByRole("checkbox", {name: "Shift"})).toBeChecked();
        expect(hotkeySave(row, "load_set")).toBeDisabled();
        fireEvent.click(within(row).getByRole("checkbox", {name: "Alt"}));
        expect(hotkeySave(row, "load_set")).not.toBeDisabled();
        fireEvent.click(within(row).getByRole("checkbox", {name: "Alt"}));
        expect(hotkeySave(row, "load_set")).toBeDisabled();
    });

test("a value the controls cannot show saves only once the row is touched", async () => {
    server.hotkeys.keys.load_set = {
        value: "Shift+!", default: "Ctrl+Shift+S", valid: false, warning: null};
    await renderPage();
    const row = hotkeyRow("load_set");
    const save = hotkeySave(row, "load_set");

    expect(row).toHaveTextContent("Shift+!");
    expect(row).toHaveTextContent(i18n.t("settings_hotkey_invalid"));
    expect(save).toBeDisabled();
    // Touched and put back: "None", which clears it.
    fireEvent.click(within(row).getByRole("checkbox", {name: "Ctrl"}));
    fireEvent.click(within(row).getByRole("checkbox", {name: "Ctrl"}));
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    await settle();

    expect(writes("hotkeys")).toEqual([{keys: {load_set: ""}}]);
});

test("a shell that registers no hotkeys says so", async () => {
    server.hotkeys.applied = false;
    await renderPage();

    expect(card("settings_hotkeys")).toHaveTextContent(
        i18n.t("settings_hotkeys_not_applied"));
});

test("a 403 on this PC's settings says so; on general, the token", async () => {
    await renderPage();
    refuse = {section: "language", status: 403, code: "FORBIDDEN"};

    pickIn(card("settings_language"), i18n.t("settings_language_fgTerm"),
           "English / English");
    await settle();

    expect(await screen.findByText(i18n.t("settings_this_pc_only")))
        .toBeInTheDocument();
    expect(within(card("settings_language")).getByRole("combobox", {
        name: i18n.t("settings_language_fgTerm")}))
        .toHaveTextContent(i18n.t("settings_language_same"));

    refuse = {section: "general", status: 403, code: "FORBIDDEN"};
    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_profanity_filter")}));
    await settle();

    expect(await screen.findByText(i18n.t("stream_token_required")))
        .toBeInTheDocument();
    expect(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_profanity_filter")})).toBeChecked();
});

test("a settings.json that cannot be opened is told", async () => {
    await renderPage();
    refuse = {section: "general", status: 409, code: "NOT_READY"};

    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_disable_export")}));
    await settle();

    expect(await screen.findByText(i18n.t("settings_not_ready")))
        .toBeInTheDocument();
});

test("a launch that could not read settings.json is told; a save reads all again", async () => {
    server.loadFailed = true;
    server.movedAside = "settings.json.unreadable-20261113-101010.bak";
    await renderPage();

    expect(screen.getByText(i18n.t("settings_file_unreadable")))
        .toBeInTheDocument();
    expect(screen.getByText(i18n.t("settings_file_moved_aside", {
        name: "settings.json.unreadable-20261113-101010.bak"})))
        .toBeInTheDocument();
    expect(reads()).toBe(1);
    // What the file held, read again by the save.
    server.language.program = "ja";
    fireEvent.click(screen.getByRole("checkbox", {
        name: i18n.t("settings_general_disable_export")}));
    await settle();
    await settle();

    expect(reads()).toBe(2);
    expect(screen.queryByText(i18n.t("settings_file_unreadable"))).toBeNull();
    expect(within(card("settings_language")).getByRole("combobox", {
        name: i18n.t("settings_language_program")}))
        .toHaveTextContent("日本語 / Japanese");
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
                value={{panel: "settings", desktop: true, language: "en"}}>
                <SettingsPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
    await settle();

    expect(await screen.findByText(i18n.t("settings_load_failed")))
        .toBeInTheDocument();
    global.fetch = real;
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("settings_reload")}));
    await settle();

    expect(await screen.findByRole(
        "region", {name: i18n.t("settings_hotkeys")})).toBeInTheDocument();
});

describe("the composition", () => {
    const keys = VOCABULARY.keys;

    test.each([
        ["Ctrl+Shift+1", {ctrl: true, alt: false, shift: true, key: "1"}],
        ["shift+ctrl+a", {ctrl: true, alt: false, shift: true, key: "A"}],
        ["Ctrl+Esc", {ctrl: true, alt: false, shift: false, key: "Escape"}],
        ["F5", {ctrl: false, alt: false, shift: false, key: "F5"}],
    ])("%s is split into the controls", (value, composition) => {
        expect(splitShortcut(value, keys)).toEqual(composition);
    });

    test.each(["", null, "Ctrl+Ctrl+A", "Ctrl+A+B", "Ctrl", "Ctrl+Q"])(
        "%p starts from nothing", (value) => {
            expect(splitShortcut(value, keys)).toEqual(
                {ctrl: false, alt: false, shift: false, key: ""});
        });

    test("a composition is spelled Ctrl, Alt, Shift, key", () => {
        expect(joinShortcut({ctrl: true, alt: true, shift: true, key: "S"}))
            .toBe("Ctrl+Alt+Shift+S");
        expect(joinShortcut({ctrl: true, alt: false, shift: false, key: ""}))
            .toBe("");
    });

    test("the actions sharing a shortcut are found however spelled", () => {
        const current = {a: "Ctrl+Esc", b: "ctrl+escape", c: "Ctrl+1", d: ""};
        expect(sharingActions("Ctrl+Escape", "c", current, keys))
            .toEqual(["a", "b"]);
        expect(sharingActions("Ctrl+1", "c", current, keys)).toEqual([]);
        expect(sharingActions("", "d", current, keys)).toEqual([]);
    });
});
