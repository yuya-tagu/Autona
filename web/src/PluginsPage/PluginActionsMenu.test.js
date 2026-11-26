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
import PluginActionsMenu, {fileNameOf} from "./PluginActionsMenu";
import PluginsPage from "./index";
import {createMockPluginsApi} from "./mockPluginsApi";
import {versionLabel} from "./pluginText";

// The pages' "Plugins" menu (ADR-009 D4, I3) against the in-memory mock API,
// and the two display fixes of the plugins page.

jest.setTimeout(20000);

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
    URL.createObjectURL = () => "blob:stub";
    URL.revokeObjectURL = () => {};
});

function wrap(node) {
    return render(
        <PanelShellContext.Provider
            value={{panel: "result", desktop: true, language: "en"}}>
            {node}
        </PanelShellContext.Provider>);
}

describe("the Plugins menu", () => {
    test("is not drawn without an action or outside the shared view",
         async () => {
             const api = createMockPluginsApi();
             const spy = jest.spyOn(api, "actions");
             wrap(<PluginActionsMenu api={api} target="thumbnail_image"
                                     context="t.png"/>);
             await waitFor(() => expect(spy).toHaveBeenCalledWith(
                 "thumbnail_image"));
             expect(screen.queryByTestId("plugin-actions-button")).toBeNull();
             cleanup();
             mockAccess = "elsewhere";
             const other = createMockPluginsApi();
             const read = jest.spyOn(other, "actions");
             wrap(<PluginActionsMenu api={other} target="result_image"
                                     context="r.png"/>);
             expect(screen.queryByTestId("plugin-actions-button")).toBeNull();
             expect(read).not.toHaveBeenCalled();
         });

    test("waits while nothing is made yet", async () => {
        wrap(<PluginActionsMenu api={createMockPluginsApi()}
                                target="result_image" context={null}/>);
        const button = await screen.findByTestId("plugin-actions-button");
        expect(button.disabled).toBe(true);
        expect(screen.getByTestId("plugin-actions-why").textContent)
            .toBe(i18n.t("plugins_action_no_context"));
    });

    test("waits while the page is making the image", async () => {
        wrap(<PluginActionsMenu api={createMockPluginsApi()} blocked
                                target="result_image" context="r.png"/>);
        expect((await screen.findByTestId("plugin-actions-button")).disabled)
            .toBe(true);
        expect(screen.getByTestId("plugin-actions-why").textContent)
            .toBe(i18n.t("plugins_action_generating"));
    });

    test("starts an action with the page's file as its context", async () => {
        const api = createMockPluginsApi({runningReads: 1});
        const start = jest.spyOn(api, "startRun");
        wrap(<PluginActionsMenu api={api} target="result_image"
                                context="result_1.png"/>);
        fireEvent.click(await screen.findByTestId("plugin-actions-button"));
        fireEvent.click(await screen.findByTestId(
            "plugin-action-taguyan.webhook-post_image"));
        const dialog = await screen.findByTestId("plugin-action-dialog");
        expect(dialog.textContent).toContain("Send to webhook");
        // The image is the page's: not asked for.
        expect(screen.getByTestId("plugin-action-param-image").textContent)
            .toBe(i18n.t("plugins_source_context", {label: "Result image"}));
        fireEvent.click(screen.getByTestId("plugin-action-run"));
        await waitFor(() => expect(start).toHaveBeenCalled());
        expect(start.mock.calls[0][0]).toBe("taguyan.webhook");
        expect(start.mock.calls[0][1]).toEqual({
            job: "post_image",
            trigger: {kind: "action", target: "result_image"},
            params: {}, inputs: {image: "result_1.png"}});
        expect(await screen.findByTestId("plugin-run-progress")).toBeTruthy();
        expect(await screen.findByTestId("plugin-run-view")).toBeTruthy();
        expect(screen.getByTestId("plugin-action-again").disabled).toBe(false);
    });

    test("does not ask for what the trigger fills (a state, a match log)",
         async () => {
        const api = createMockPluginsApi();
        api.actions = async () => [{
            plugin: "taguyan.webhook", name: "Webhook",
            job: {id: "both", title: "Both", lane: "light",
                  timeout_seconds: 60,
                  params: [
                      {key: "image", type: "result_image", required: true,
                       label: "Result image"},
                      {key: "log", type: "match_log", required: true,
                       label: "Match log"},
                      {key: "st", type: "state", label: "State"},
                      {key: "note", type: "text", label: "Note"}],
                  actions: [{target: "result_image", label: "Both"}]}}];
        const start = jest.spyOn(api, "startRun");
        wrap(<PluginActionsMenu api={api} target="result_image"
                                context="r.png"/>);
        fireEvent.click(await screen.findByTestId("plugin-actions-button"));
        fireEvent.click(await screen.findByTestId("plugin-action-taguyan.webhook-both"));
        await screen.findByTestId("plugin-action-dialog");
        expect(screen.queryByTestId("plugin-action-param-log")).toBeNull();
        expect(screen.queryByTestId("plugin-action-param-st")).toBeNull();
        expect(screen.getByTestId("plugin-action-param-note")).toBeTruthy();
        // The required match log does not block the start.
        fireEvent.click(screen.getByTestId("plugin-action-run"));
        await waitFor(() => expect(start).toHaveBeenCalled());
        expect(start.mock.calls[0][1].inputs).toEqual({image: "r.png"});
    });

    test("is not closed by Esc or a click outside while the job runs",
         async () => {
        const api = createMockPluginsApi({runningReads: 50});
        wrap(<PluginActionsMenu api={api} target="result_image"
                                context="r.png"/>);
        fireEvent.click(await screen.findByTestId("plugin-actions-button"));
        fireEvent.click(await screen.findByTestId(
            "plugin-action-taguyan.webhook-post_image"));
        fireEvent.click(await screen.findByTestId("plugin-action-run"));
        await screen.findByTestId("plugin-run-progress");
        expect(screen.getByTestId("plugin-action-goes-on")).toBeTruthy();
        fireEvent.keyDown(screen.getByRole("dialog"), {key: "Escape"});
        expect(screen.getByTestId("plugin-action-dialog")).toBeTruthy();
        // The button closes it.
        fireEvent.click(screen.getByTestId("plugin-action-close"));
        await waitFor(() => expect(
            screen.queryByTestId("plugin-action-dialog")).toBeNull());
    });

    test("says a busy refusal with the seconds left", async () => {
        const api = createMockPluginsApi();
        api.startRun = async () => {
            throw Object.assign(new Error("busy"), {
                status: 409, code: "BUSY", reason: "busy", detail: "12"});
        };
        wrap(<PluginActionsMenu api={api} target="result_image"
                                context="r.png"/>);
        fireEvent.click(await screen.findByTestId("plugin-actions-button"));
        fireEvent.click(await screen.findByTestId(
            "plugin-action-taguyan.webhook-post_image"));
        fireEvent.click(await screen.findByTestId("plugin-action-run"));
        const error = await screen.findByTestId("plugin-action-error");
        expect(error.textContent).toContain("12 s left");
    });

    test("names the file of a made image", () => {
        expect(fileNameOf("out/results/a-b.png")).toBe("a-b.png");
        expect(fileNameOf("")).toBeNull();
    });
});

describe("the plugins page's display", () => {
    test("an empty version shows no bare v", () => {
        expect(versionLabel("")).toBe("");
        expect(versionLabel("1.2.0")).toBe("v1.2.0");
    });

    test("a built-in plugin has no version and a page note, not the"
         + " context-only one", async () => {
        const api = createMockPluginsApi();
        const detail = await api.detail("taguyan.webhook");
        const builtin = {
            ...detail, id: "taguyan.vodsplit", badge: "builtin", version: "",
            jobs: [{id: "split", title: "Split", lane: "heavy",
                    timeout_seconds: null, manual: false, params: []}]};
        api.list = async () => [{
            id: builtin.id, name: "Clipping", author: "taguyan", version: "",
            state: "ready", badge: "builtin", size_bytes: 1024,
            has_icon: false}];
        api.detail = async () => builtin;
        render(
            <PanelShellContext.Provider
                value={{panel: "plugins", desktop: true, language: "en"}}>
                <PluginsPage api={api}/>
            </PanelShellContext.Provider>);
        const row = await screen.findByTestId("plugin-row-taguyan.vodsplit");
        expect(row.textContent).not.toMatch(/\bv(?=\s|$|·)/);
        expect(row.textContent).toContain("taguyan · 1 KB");
        fireEvent.click(row);
        const note = await screen.findByTestId("plugin-job-note-split");
        expect(note.textContent).toContain("Archive clipping page");
        expect(note.textContent).not.toContain("starts only from");
    });

    test("another built-in plugin keeps the context-only note", async () => {
        const api = createMockPluginsApi();
        const detail = await api.detail("taguyan.webhook");
        const other = {
            ...detail, id: "taguyan.other", badge: "builtin",
            jobs: [{id: "j", title: "J", lane: "light",
                    timeout_seconds: 5, manual: false, params: []}]};
        api.list = async () => [{
            id: other.id, name: "Other", author: "taguyan", version: "1",
            state: "ready", badge: "builtin", size_bytes: 1024,
            has_icon: false}];
        api.detail = async () => other;
        render(
            <PanelShellContext.Provider
                value={{panel: "plugins", desktop: true, language: "en"}}>
                <PluginsPage api={api}/>
            </PanelShellContext.Provider>);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.other"));
        expect((await screen.findByTestId("plugin-job-note-j")).textContent)
            .toContain("starts only from");
    });
});
