import React from "react";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import i18n from "../i18n/config";
import {PanelShellContext} from "../PanelShell/context";
import PluginsPage from "./index";
import FieldForm from "./FieldForm";
import {createMockPluginsApi} from "./mockPluginsApi";
import {pluginText, sizeLabel} from "./pluginText";
import {
    collectValues,
    initialValues,
    validateAll,
    validateField,
} from "./formFields";

// The plugins page (ADR-009 D4) against the in-memory mock API. The shell
// probe is stubbed: the page is shown as the desktop's shared view.

jest.setTimeout(20000);

jest.mock("../PanelShell/useShellAccess", () => ({
    useShellAccess: () => ({access: "shell", token: "", available: false,
                            retry: () => {}, regenerate: async () => ""}),
}));

jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {},
                                on: () => {}, off: () => {}})},
}));

beforeAll(async () => {
    await i18n.changeLanguage("en");
    // jsdom has no object URLs.
    URL.createObjectURL = () => "blob:stub";
    URL.revokeObjectURL = () => {};
});

function show(api, language = "en") {
    return render(
        <PanelShellContext.Provider
            value={{panel: "plugins", desktop: true, language}}>
            <PluginsPage api={api}/>
        </PanelShellContext.Provider>);
}

describe("pluginText", () => {
    test("current locale, then en, then ja, then the first value", () => {
        expect(pluginText("plain", "ja")).toBe("plain");
        expect(pluginText({ja: "あ", en: "a"}, "ja")).toBe("あ");
        expect(pluginText({ja: "あ", en: "a"}, "fr")).toBe("a");
        expect(pluginText({ja: "あ", de: "d"}, "fr")).toBe("あ");
        expect(pluginText({de: "d", it: "i"}, "fr")).toBe("d");
        expect(pluginText({"en-GB": "x", en: "a"}, "en-US")).toBe("a");
        expect(pluginText(null, "en")).toBe("");
    });

    test("sizes", () => {
        expect(sizeLabel(40 * 1024)).toBe("40 KB");
        expect(sizeLabel(3 * 1024 * 1024)).toBe("3.0 MB");
    });
});

describe("the form's rules", () => {
    const fields = [
        {key: "name", type: "text", label: "Name", required: true,
         max_length: 5},
        {key: "n", type: "number", label: "N", min: 1, max: 10, step: 1},
        {key: "pw", type: "secret", label: "PW", required: true},
        {key: "dir", type: "folder", label: "Dir", required: true},
        {key: "odd", type: "future_type", label: "Odd"},
    ];

    test("required, length, range, step, secret, pick, unknown type", () => {
        const values = initialValues(fields);
        expect(validateAll(fields, values)).toEqual({
            name: "required", pw: "required", dir: "required",
            odd: "unknown_type"});
        expect(validateField(fields[0], "abcdef")).toBe("too_long");
        expect(validateField(fields[1], "0")).toBe("below_min");
        expect(validateField(fields[1], "11")).toBe("above_max");
        expect(validateField(fields[1], "2.5")).toBe("bad_step");
        expect(validateField(fields[1], "x")).toBe("not_number");
        expect(validateField(fields[2], "", true)).toBeNull();
        expect(validateField(fields[2], "a\nb")).toBe("line_break");
        expect(validateField(
            fields[3], {pick: null, stored: "Saved"})).toBeNull();
    });

    test("the request holds pick ids, never a path or a secret", () => {
        const all = [
            ...fields.slice(0, 4),
            {key: "log", type: "match_log", label: "Log"},
            {key: "st", type: "state", label: "State"}];
        const values = {
            ...initialValues(all), name: "ab", n: "3", pw: "secret!",
            dir: {pick: {id: "p1", name: "Dir"}, stored: null}, log: "d/t"};
        expect(collectValues(all, values)).toEqual({
            params: {name: "ab", n: 3, dir: "p1"}, inputs: {log: "d/t"}});
    });
});

describe("FieldForm", () => {
    test("draws each type, picks a folder and hides a secret", async () => {
        const fields = [
            {key: "msg", type: "textarea", label: {ja: "本文", en: "Body"}},
            {key: "on", type: "bool", label: "On"},
            {key: "dir", type: "folder", label: "Dir"},
            {key: "pw", type: "secret", label: "PW"},
            {key: "st", type: "state", label: "State"}];
        let values = initialValues(fields);
        const onChange = jest.fn((next) => {
            values = next;
        });
        const pickFolder = jest.fn(async () => ({id: "p9", name: "Picked"}));
        const {rerender} = render(
            <FieldForm fields={fields} values={values} onChange={onChange}
                       language="en" pickFolder={pickFolder}
                       secretSet={{pw: true}} onClearSecret={() => {}}/>);
        expect(screen.getByLabelText("Body")).toBeTruthy();
        // A saved secret shows "Set" only, never a value.
        expect(screen.getAllByText("Set").length).toBeGreaterThan(0);
        expect(screen.getByTestId("plugin-field-st").textContent)
            .toContain("Published state");
        fireEvent.click(screen.getByRole("button", {name: "Choose a folder"}));
        await waitFor(() => expect(onChange).toHaveBeenCalled());
        expect(values.dir.pick).toEqual({id: "p9", name: "Picked"});
        rerender(
            <FieldForm fields={fields} values={values} onChange={onChange}
                       language="en" pickFolder={pickFolder}/>);
        expect(screen.getByTestId("plugin-field-dir-name").textContent)
            .toBe("Picked");
    });

    test("shows the reason of an invalid field and a type it does not know",
         () => {
             const fields = [
                 {key: "a", type: "text", label: "A", required: true},
                 {key: "z", type: "future", label: "Z"}];
             render(<FieldForm fields={fields} values={initialValues(fields)}
                               onChange={() => {}} language="en" showErrors/>);
             expect(screen.getByText("Required.")).toBeTruthy();
             expect(screen.getByTestId("plugin-field-z").textContent)
                 .toContain("future");
         });
});

describe("the page", () => {
    test("lists the plugins and opens one's detail", async () => {
        show(createMockPluginsApi());
        const row = await screen.findByTestId("plugin-row-taguyan.webhook");
        expect(row.textContent).toContain("Webhook post");
        expect(row.textContent).toContain("Listed");
        expect(row.textContent).toContain("3.0 MB");
        const dev = screen.getByTestId("plugin-row-me.sandbox");
        expect(dev.textContent).toContain("In development");
        fireEvent.click(row);
        const detail = await screen.findByTestId("plugin-detail");
        expect(detail.textContent).toContain("Posts result images");
        // The secret is "Set" or not, never a value.
        expect(within(detail).getByTestId("plugin-setting-webhook_url")
            .value).toBe("");
        expect(within(detail).getByTestId("plugin-declared").textContent)
            .toContain("discord.com");
        expect(within(detail).getByTestId("plugin-passed").textContent)
            .toContain("Result image");
        expect(within(detail).getByTestId("plugin-link-homepage")).toBeTruthy();
        expect(within(detail).queryByTestId("plugin-link-funding")).toBeNull();
    });

    test("reads the author's text in the page's language", async () => {
        show(createMockPluginsApi(), "ja");
        const row = await screen.findByTestId("plugin-row-taguyan.webhook");
        expect(row.textContent).toContain("Webhook 送信");
    });

    test("a required field blocks the run, then the run shows its progress"
         + " and its artifacts", async () => {
        const api = createMockPluginsApi({runningReads: 1});
        const started = jest.spyOn(api, "startRun");
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.webhook"));
        await screen.findByTestId("plugin-detail");

        // export_log needs a match log: not started without one.
        fireEvent.click(screen.getByTestId("plugin-run-export_log"));
        expect(started).not.toHaveBeenCalled();
        expect(screen.getAllByText("Required.").length).toBeGreaterThan(0);

        const select = within(screen.getByTestId("plugin-job-export_log"))
            .getByRole("combobox");
        fireEvent.mouseDown(select);
        fireEvent.click(await screen.findByRole("option",
                                                {name: "2026-11-26 Fake Cup"}));
        jest.useFakeTimers();
        try {
            fireEvent.click(screen.getByTestId("plugin-run-export_log"));
            await act(async () => {
                await Promise.resolve();
            });
            expect(started).toHaveBeenCalledWith("taguyan.webhook", {
                job: "export_log",
                trigger: {kind: "manual", target: null},
                params: {count: 3}, inputs: {log: "2026-11-26/cup"}});
            await act(async () => {
                await Promise.resolve();
            });
            expect(screen.getByTestId("plugin-run-progress")).toBeTruthy();
            // The page asks again every second until the run ends.
            await act(async () => {
                jest.advanceTimersByTime(1000);
            });
            await act(async () => {
                await Promise.resolve();
            });
        } finally {
            jest.useRealTimers();
        }
        await waitFor(() => expect(screen.queryByTestId("plugin-run-progress"))
            .toBeNull());
        const view = await screen.findByTestId("plugin-run-view");
        expect(view.textContent).toContain("Sent 1 message");
        // A text artifact: its whole text; a json artifact: open the folder.
        await waitFor(() => expect(
            screen.getByTestId("plugin-artifact-0-text").textContent)
            .toContain("Sent to the webhook."));
        expect(within(view).getAllByRole("button",
                                         {name: "Open the folder"}).length)
            .toBeGreaterThan(0);

        fireEvent.click(screen.getByTestId("plugin-run-log-toggle"));
        expect((await screen.findByTestId("plugin-run-log")).textContent)
            .toContain("stderr line");
    });

    test("an image artifact is fetched into a blob URL", async () => {
        const created = jest.fn(() => "blob:mock-1");
        const revoked = jest.fn();
        URL.createObjectURL = created;
        URL.revokeObjectURL = revoked;
        const api = createMockPluginsApi({runningReads: 0});
        const blob = jest.spyOn(api, "artifactBlob");
        // A finished run already in the plugin's history.
        const id = "taguyan.webhook";
        const started = await api.startRun(id, {
            job: "export_log", trigger: {kind: "manual", target: null},
            params: {}, inputs: {}});
        await api.run(id, started.run_id);
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${id}`));
        const history = await screen.findByTestId("plugin-history");
        fireEvent.click(within(history).getByRole("button"));
        await waitFor(() => expect(blob).toHaveBeenCalled());
        const img = await screen.findByTestId("plugin-artifact-1-image");
        expect(img.getAttribute("src")).toBe("blob:mock-1");
    });

    test("turning the developer toggle on asks first, with D6's fixed text",
         async () => {
             const api = createMockPluginsApi();
             show(api);
             const toggle = await screen.findByRole("switch", {
                 name: "Use the developer features"});
             await waitFor(() => expect(toggle.disabled).toBe(false));
             fireEvent.click(toggle);
             const text = await screen.findByTestId("plugin-dev-consent");
             expect(text.textContent).toContain(
                 "A plugin is a program that runs on this PC");
             expect(await api.dev()).toEqual({enabled: false, folders: []});
             fireEvent.click(screen.getByRole("button", {
                 name: "I understand, continue"}));
             await waitFor(async () => expect((await api.dev()).enabled)
                 .toBe(true));
             await waitFor(() => expect(
                 screen.queryByTestId("plugin-dev-consent")).toBeNull());
             // A folder is added after a pick and a second confirmation.
             fireEvent.click(await screen.findByRole("button", {
                 name: "Add a folder in development"}));
             fireEvent.click(await screen.findByRole("button", {
                 name: "I understand, continue"}));
             await waitFor(async () => expect((await api.dev()).folders)
                 .toHaveLength(1));
         });

    test("cancelling the dialog changes nothing", async () => {
        const api = createMockPluginsApi();
        show(api);
        const toggle = await screen.findByRole("switch", {
            name: "Use the developer features"});
        await waitFor(() => expect(toggle.disabled).toBe(false));
        fireEvent.click(toggle);
        fireEvent.click(await screen.findByRole("button", {name: "Cancel"}));
        await waitFor(() => expect(screen.queryByTestId("plugin-dev-consent"))
            .toBeNull());
        expect((await api.dev()).enabled).toBe(false);
    });
});

test("every english plugins key has a japanese one and the reverse", () => {
    const en = require("../i18n/locales/en.json");
    const ja = require("../i18n/locales/ja.json");
    const keys = (dict) => Object.keys(dict)
        .filter((k) => k.startsWith("plugins_")).sort();
    expect(keys(ja)).toEqual(keys(en));
    expect(keys(en).length).toBeGreaterThan(50);
});
