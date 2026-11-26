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
import {MOCK_SHEET, createMockPluginsApi} from "./mockPluginsApi";
import {failureText} from "./InstallSection";

// The install, enable and removal flows (ADR-009 P5a) against the mock API.

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

const WAIT = {timeout: 6000};

function pickZip() {
    const file = new File(["PK"], "hello.zip", {type: "application/zip"});
    fireEvent.change(screen.getByTestId("plugin-install-input"),
                     {target: {files: [file]}});
    return file;
}

describe("install", () => {
    test("upload, consent sheet, approve, installed", async () => {
        const api = createMockPluginsApi();
        const upload = jest.spyOn(api, "uploadZip");
        const consent = jest.spyOn(api, "consentInstall");
        show(api);
        await screen.findByTestId("plugin-row-taguyan.webhook");
        const file = pickZip();
        await screen.findByTestId("plugin-sheet", {}, WAIT);
        expect(upload).toHaveBeenCalledWith(file);
        const sheet = screen.getByTestId("plugin-sheet");
        expect(sheet.textContent).toContain("Hello");
        expect(sheet.textContent).toContain("v1.2.0");
        expect(sheet.textContent).toContain("MIT");
        expect(sheet.textContent).toContain("acme");
        expect(screen.getByTestId("plugin-sheet-verified").textContent)
            .toContain("Not verified");
        expect(screen.getByTestId("plugin-sheet-source").textContent)
            .toContain("Manual");
        expect(screen.getByTestId("plugin-sheet-permissions").textContent)
            .toContain("example.com");
        fireEvent.click(screen.getByTestId("plugin-install-approve"));
        await waitFor(() => expect(consent).toHaveBeenCalledWith(
            "in-1", "digest-1"));
        await screen.findByTestId("plugin-row-acme.hello", {}, WAIT);
        await waitFor(() => expect(
            screen.queryByTestId("plugin-sheet")).toBeNull());
    });

    test("cancel at the sheet declines the install", async () => {
        const api = createMockPluginsApi();
        const cancel = jest.spyOn(api, "cancelInstall");
        show(api);
        await screen.findByTestId("plugin-row-taguyan.webhook");
        pickZip();
        await screen.findByTestId("plugin-sheet", {}, WAIT);
        fireEvent.click(screen.getByTestId("plugin-install-cancel"));
        await waitFor(() => expect(cancel).toHaveBeenCalledWith("in-1"));
        await waitFor(() => expect(
            screen.queryByTestId("plugin-sheet")).toBeNull());
        expect(screen.queryByTestId("plugin-row-acme.hello")).toBeNull();
    });

    test("an update shows the installed version", async () => {
        const api = createMockPluginsApi({sheet: {
            ...MOCK_SHEET, decision: "update", installed_version: "1.0.0"}});
        show(api);
        await screen.findByTestId("plugin-row-taguyan.webhook");
        pickZip();
        await screen.findByTestId("plugin-sheet", {}, WAIT);
        expect(screen.getByTestId("plugin-sheet-installed").textContent)
            .toContain("v1.0.0");
        expect(screen.getByTestId("plugin-sheet-decision").textContent)
            .toContain("v1.0.0");
    });

    test("a refused decision cannot be approved", async () => {
        const api = createMockPluginsApi({sheet: {
            ...MOCK_SHEET, decision: "refuse_dev_id", needs_consent: false}});
        show(api);
        await screen.findByTestId("plugin-row-taguyan.webhook");
        pickZip();
        await screen.findByTestId("plugin-sheet", {}, WAIT);
        expect(screen.getByTestId("plugin-install-approve")).toBeDisabled();
    });

    test("a failed install says why in words", async () => {
        const api = createMockPluginsApi();
        api.installStatus = async () => ({
            install_id: "in-1", state: "failed", plugin_id: null,
            sheet: null, error: {
                reason: "incompatible", code: "bad_zip",
                problems: ["sha256_mismatch"], detail: ""}});
        show(api);
        await screen.findByTestId("plugin-row-taguyan.webhook");
        pickZip();
        const failed = await screen.findByTestId(
            "plugin-install-failed", {}, WAIT);
        expect(failed.textContent).toContain("The zip has a problem.");
        expect(failed.textContent).toContain("SHA-256");
        fireEvent.click(screen.getByTestId("plugin-install-close"));
        await waitFor(() => expect(
            screen.queryByTestId("plugin-install-failed")).toBeNull());
    });

    test("failureText falls back for an unknown code and problem", () => {
        expect(failureText({reason: "x", code: "weird", problems: ["odd"],
                            detail: ""}))
            .toBe("It could not be installed. (odd)");
    });
});

describe("enable and remove", () => {
    test("the switch enables and disables an installed plugin only", async () => {
        const api = createMockPluginsApi();
        const set = jest.spyOn(api, "setEnabled");
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.webhook"));
        const toggle = await screen.findByTestId("plugin-enable");
        fireEvent.click(toggle.querySelector("input"));
        await waitFor(() => expect(set).toHaveBeenCalledWith(
            "taguyan.webhook", false));
        await waitFor(() => expect(
            screen.getByTestId("plugin-enable").querySelector("input").checked)
            .toBe(false));
        // A dev row has no switch and no remove button.
        fireEvent.click(screen.getByTestId("plugin-row-me.sandbox"));
        await screen.findByTestId("plugin-reload");
        expect(screen.queryByTestId("plugin-enable")).toBeNull();
        expect(screen.queryByTestId("plugin-remove")).toBeNull();
    });

    test("remove shows the sizes and sends the choices", async () => {
        const api = createMockPluginsApi();
        const remove = jest.spyOn(api, "startRemoval");
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.webhook"));
        fireEvent.click(await screen.findByTestId("plugin-remove"));
        const sizes = await screen.findByTestId("plugin-remove-sizes");
        expect(sizes.textContent).toContain("2 KB");
        expect(sizes.textContent).toContain("3 past jobs");
        expect(screen.getByTestId("plugin-remove-work").textContent)
            .toContain("acme-work");
        fireEvent.click(screen.getByTestId("plugin-remove-data")
            .querySelector("input"));
        fireEvent.click(screen.getByTestId("plugin-remove-runs")
            .querySelector("input"));
        fireEvent.click(screen.getByTestId("plugin-remove-keep-out")
            .querySelector("input"));
        fireEvent.click(screen.getByTestId("plugin-remove-confirm"));
        await waitFor(() => expect(remove).toHaveBeenCalledWith(
            "taguyan.webhook", {data: true, runs: "keep_out"}));
        await waitFor(() => expect(
            screen.queryByTestId("plugin-row-taguyan.webhook")).toBeNull(),
        WAIT);
        expect(screen.queryByTestId("plugin-remove-dialog")).toBeNull();
    });

    test("nothing ticked keeps the data and the runs", async () => {
        const api = createMockPluginsApi();
        const remove = jest.spyOn(api, "startRemoval");
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.webhook"));
        fireEvent.click(await screen.findByTestId("plugin-remove"));
        fireEvent.click(await screen.findByTestId("plugin-remove-confirm"));
        await waitFor(() => expect(remove).toHaveBeenCalledWith(
            "taguyan.webhook", {data: false, runs: "keep"}));
    });
});

describe("outside the desktop", () => {
    test("no add button in a browser", async () => {
        mockAccess = "elsewhere";
        show(createMockPluginsApi());
        await screen.findByTestId("plugin-row-taguyan.webhook");
        expect(screen.queryByTestId("plugin-install-add")).toBeNull();
    });
});
