import React from "react";
import {
    cleanup,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import i18n from "../i18n/config";
import {PanelShellContext} from "../PanelShell/context";
import PluginsPage from "./index";
import {createMockPluginsApi} from "./mockPluginsApi";

// The hook section of the plugin page (ADR-009 P7, appendix D) against the
// mock API.

jest.setTimeout(30000);

let mockAccess = "shell";
jest.mock("../PanelShell/useShellAccess", () => ({
    useShellAccess: () => ({access: mockAccess, token: "", available: false,
                            retry: () => {}, regenerate: async () => ""}),
}));

jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {},
                                on: () => {}, off: () => {}})},
}));

beforeEach(() => {
    mockAccess = "shell";
});
afterEach(cleanup);

beforeAll(async () => {
    await i18n.changeLanguage("en");
});

function show(api) {
    return render(
        <PanelShellContext.Provider
            value={{panel: "plugins", desktop: true, language: "en"}}>
            <PluginsPage api={api}/>
        </PanelShellContext.Provider>);
}

const WEBHOOK = "taguyan.webhook";
const KEY = "export_log-match_decided";

function row(extra = {}) {
    return {
        key: "export_log/match_decided", job: "export_log",
        on: "match_decided", enabled: false, params: {}, pending: false,
        failures: 0, disabled_reason: null, last_error: null, history: [],
        ...extra};
}

describe("hooks section", () => {
    test("enabling opens the job's value form and sends the values", async () => {
        const api = createMockPluginsApi();
        const set = jest.spyOn(api, "setHook");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        const sw = await screen.findByTestId(`plugin-hook-switch-${KEY}`);
        fireEvent.click(sw.querySelector("input"));
        const form = await screen.findByTestId("plugin-hook-form");
        // The value field (count, default 3) is there; the source is not.
        expect(form.querySelector("input[type=number], input")).not.toBeNull();
        expect(form.textContent).not.toContain("Match log");
        fireEvent.click(screen.getByTestId("plugin-hook-form-save"));
        await waitFor(() => expect(set).toHaveBeenCalledWith(
            WEBHOOK, "export_log", "match_decided", true, {count: 3}));
        await waitFor(() => expect(
            screen.queryByTestId("plugin-hook-form")).toBeNull());
        await waitFor(() => expect(
            screen.getByTestId(`plugin-hook-switch-${KEY}`)
                .querySelector("input").checked).toBe(true));
    });

    test("disabling sends enabled false only", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({enabled: true, params: {count: 5}})];
        const set = jest.spyOn(api, "setHook");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        const sw = await screen.findByTestId(`plugin-hook-switch-${KEY}`);
        await waitFor(() => expect(sw.querySelector("input").checked)
            .toBe(true));
        fireEvent.click(sw.querySelector("input"));
        await waitFor(() => expect(set).toHaveBeenCalledWith(
            WEBHOOK, "export_log", "match_decided", false, undefined));
    });

    test("an auto-disabled hook says why, with the last error", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({
            disabled_reason: "failures", failures: 5,
            last_error: {code: "http_500", message: "Server said no"}})];
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        const why = await screen.findByTestId(`plugin-hook-reason-${KEY}`);
        expect(why.textContent).toContain("failed 5 times in a row");
        expect(why.textContent).toContain("Server said no");
        expect(why.textContent).toContain("http_500");
    });

    test("pending and the history are shown, the failed run's log opens", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({
            enabled: true, pending: true,
            history: [
                {at: "2026-11-26T10:00:00+09:00", outcome: "done",
                 summary: "Wrote 1", reason: null, run_id: "r1"},
                {at: "2026-11-26T10:01:00+09:00", outcome: "overwritten",
                 summary: null, reason: null, run_id: null},
                {at: "2026-11-26T10:02:00+09:00",
                 outcome: "skipped_rate_limit", summary: null, reason: null,
                 run_id: null},
                {at: "2026-11-26T10:03:00+09:00", outcome: "failed",
                 summary: null, reason: null, run_id: "r-bad"}]})];
        const log = jest.spyOn(api, "runLog");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        await screen.findByTestId(`plugin-hook-pending-${KEY}`);
        const items = await screen.findAllByTestId("plugin-hook-history-item");
        expect(items).toHaveLength(4);
        // Newest first.
        expect(items[0].textContent).toContain("Failed");
        const text = items.map((i) => i.textContent).join("|");
        expect(text).toContain("Overwritten");
        expect(text).toContain("Skipped (limit)");
        expect(text).toContain("Wrote 1");
        fireEvent.click(screen.getByTestId(`plugin-hook-log-${KEY}`));
        await waitFor(() => expect(log).toHaveBeenCalledWith(WEBHOOK, "r-bad"));
        await screen.findByTestId(`plugin-hook-logtext-${KEY}`);
    });

    test("the sample button is for a dev plugin only", async () => {
        const api = createMockPluginsApi();
        const sample = jest.spyOn(api, "hookSample");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        await screen.findByTestId(`plugin-hook-switch-${KEY}`);
        expect(screen.queryByTestId(`plugin-hook-sample-${KEY}`)).toBeNull();
        fireEvent.click(screen.getByTestId("plugin-row-me.sandbox"));
        const button = await screen.findByTestId(`plugin-hook-sample-${KEY}`);
        fireEvent.click(button);
        await waitFor(() => expect(sample).toHaveBeenCalledWith(
            "me.sandbox", "export_log", "match_decided"));
        await screen.findByTestId("plugin-run-view", {}, {timeout: 6000});
    });

    // A detail whose export_log job also has a bool value field.
    function withBool(api) {
        const detail = api.detail;
        api.detail = async (id) => {
            const d = await detail(id);
            d.jobs.forEach((j) => {
                if (j.id === "export_log") {
                    j.params.push({key: "verbose", type: "bool",
                                   label: "Verbose"});
                }
            });
            return d;
        };
    }
    const SAVED = {
        count: {kind: "value", value: 7},
        verbose: {kind: "value", value: true},
        folder: {kind: "pick", name: "OutDir"}};

    test("changing the values shows what is saved and sends it back", async () => {
        const api = createMockPluginsApi();
        withBool(api);
        api.hooks = async () => [row({enabled: true, params: SAVED})];
        const set = jest.spyOn(api, "setHook");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        fireEvent.click(await screen.findByTestId(`plugin-hook-edit-${KEY}`));
        const form = await screen.findByTestId("plugin-hook-form");
        expect(form.querySelector("input[type=number]").value).toBe("7");
        expect(form.querySelector("input[type=checkbox]").checked).toBe(true);
        expect(screen.getByTestId(
            "plugin-hook-param-export_log-folder-name").textContent)
            .toBe("OutDir");
        fireEvent.click(screen.getByTestId("plugin-hook-form-save"));
        // The file stays (left out); the others go back as they were.
        await waitFor(() => expect(set).toHaveBeenCalledWith(
            WEBHOOK, "export_log", "match_decided", true,
            {count: 7, verbose: true}));
    });

    test("releasing a saved folder sends null for it", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({enabled: true, params: SAVED})];
        const set = jest.spyOn(api, "setHook");
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        fireEvent.click(await screen.findByTestId(`plugin-hook-edit-${KEY}`));
        await screen.findByTestId("plugin-hook-form");
        fireEvent.click(screen.getByText("Release"));
        fireEvent.click(screen.getByTestId("plugin-hook-form-save"));
        await waitFor(() => expect(set).toHaveBeenCalledWith(
            WEBHOOK, "export_log", "match_decided", true,
            {count: 7, folder: null}));
    });

    test("a failed save keeps the dialog open with its input", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({enabled: true, params: SAVED})];
        const set = jest.spyOn(api, "setHook").mockRejectedValue(
            Object.assign(new Error("no"), {status: 400}));
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        fireEvent.click(await screen.findByTestId(`plugin-hook-edit-${KEY}`));
        const form = await screen.findByTestId("plugin-hook-form");
        fireEvent.click(screen.getByTestId("plugin-hook-form-save"));
        await waitFor(() => expect(set).toHaveBeenCalled());
        expect(screen.getByTestId("plugin-hook-form")).toBeTruthy();
        expect(form.querySelector("input[type=number]").value).toBe("7");
    });

    test("a failed read says so and keeps the rows", async () => {
        const api = createMockPluginsApi();
        let calls = 0;
        api.hooks = async () => {
            calls += 1;
            if (calls > 1) {
                throw new Error("down");
            }
            return [row({enabled: true})];
        };
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        const sw = await screen.findByTestId(`plugin-hook-switch-${KEY}`);
        await waitFor(() => expect(sw.querySelector("input").checked)
            .toBe(true));
        // The next poll (5 s) fails.
        await screen.findByTestId("plugin-hooks-load-failed", {},
                                  {timeout: 8000});
        expect(screen.getByTestId(`plugin-hook-switch-${KEY}`)
            .querySelector("input").checked).toBe(true);
    });

    test("history reasons the host emits are in words", async () => {
        const api = createMockPluginsApi();
        api.hooks = async () => [row({history: [
            {at: "2026-11-26T10:00:00+09:00", outcome: "discarded",
             summary: null, reason: "input_unavailable", run_id: null},
            {at: "2026-11-26T10:01:00+09:00", outcome: "skipped",
             summary: null, reason: "expired", run_id: null},
            {at: "2026-11-26T10:02:00+09:00", outcome: "error",
             summary: null, reason: "needs_settings", run_id: null}]})];
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        const items = await screen.findAllByTestId("plugin-hook-history-item");
        const text = items.map((i) => i.textContent).join("|");
        expect(text).toContain("could not be prepared");
        expect(text).toContain("went stale");
        expect(text).toContain("Settings are needed");
    });

    test("outside the desktop the detail, and so the hooks, are not drawn", async () => {
        mockAccess = "elsewhere";
        const api = createMockPluginsApi();
        const hooks = jest.spyOn(api, "hooks");
        show(api);
        await screen.findByTestId(`plugin-row-${WEBHOOK}`);
        expect(screen.queryByTestId("plugin-hooks")).toBeNull();
        expect(hooks).not.toHaveBeenCalled();
    });
});
