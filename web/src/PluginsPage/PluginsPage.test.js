import React from "react";
import {
    act,
    cleanup,
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
import {pickErrorText, refusalText} from "./RunView";
import {timeoutText} from "./PluginDetailView";

// The plugins page (ADR-009 D4) against the in-memory mock API. The shell
// probe is stubbed: the page is shown as the desktop's shared view.

jest.setTimeout(20000);

// What the shell probe answers (a test sets it) and its retry.
let mockAccess = "shell";
const mockRetry = jest.fn();
jest.mock("../PanelShell/useShellAccess", () => ({
    useShellAccess: () => ({access: mockAccess, token: "", available: false,
                            retry: mockRetry, regenerate: async () => ""}),
}));

jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {},
                                on: () => {}, off: () => {}})},
}));

beforeEach(() => {
    mockAccess = "shell";
    mockRetry.mockClear();
});

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
            .toContain("handed over");
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

    test("a blocked plugin's link says so", async () => {
        const api = createMockPluginsApi();
        api.openLink = async () => {
            throw Object.assign(new Error("refused"), {code: "blocked"});
        };
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-taguyan.webhook"));
        fireEvent.click(await screen.findByTestId("plugin-link-homepage"));
        expect(await screen.findByText(
            "This plugin is blocked: its links are not opened.")).toBeTruthy();
        api.openLink = async () => {
            throw Object.assign(new Error("refused"), {code: "no_link"});
        };
        fireEvent.click(screen.getByTestId("plugin-link-homepage"));
        expect(await screen.findByText("The link could not be opened."))
            .toBeTruthy();
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
        expect(revoked).not.toHaveBeenCalled();
        cleanup();
        expect(revoked).toHaveBeenCalledWith("blob:mock-1");
    });

    test("a body the host did not serve as an image is not shown", async () => {
        const created = jest.fn(() => "blob:never");
        URL.createObjectURL = created;
        const api = createMockPluginsApi({runningReads: 0});
        jest.spyOn(api, "artifactBlob").mockResolvedValue(
            new Blob(["<html>"], {type: "text/html"}));
        const id = "taguyan.webhook";
        const started = await api.startRun(id, {
            job: "export_log", trigger: {kind: "manual", target: null},
            params: {}, inputs: {}});
        await api.run(id, started.run_id);
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${id}`));
        fireEvent.click(within(await screen.findByTestId("plugin-history"))
            .getByRole("button"));
        await screen.findByTestId("plugin-artifact-1");
        await waitFor(() => expect(screen.getByText("It did not work."))
            .toBeTruthy());
        expect(created).not.toHaveBeenCalled();
        expect(screen.queryByTestId("plugin-artifact-1-image")).toBeNull();
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

    test("the add button waits while the folder dialog is open", async () => {
        const api = createMockPluginsApi();
        await api.setDev(true);
        let finish;
        api.pickFolder = jest.fn(() => new Promise((resolve) => {
            finish = () => resolve(null);
        }));
        show(api);
        const add = await screen.findByTestId("plugin-dev-add");
        fireEvent.click(add);
        expect(add.disabled).toBe(true);
        fireEvent.click(add);
        expect(api.pickFolder).toHaveBeenCalledTimes(1);
        await act(async () => {
            finish();
        });
        expect(add.disabled).toBe(false);
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

const WEBHOOK = "taguyan.webhook";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A job record as the host answers it, for a test to bend.
function record(extra = {}) {
    return {
        run_id: "20261126-100000-aaaa", plugin: WEBHOOK, job: "post_image",
        trigger: {kind: "manual", target: null}, lane: "light",
        state: "failed", reason: null,
        started_at: "2026-11-26T10:00:00+09:00",
        finished_at: "2026-11-26T10:00:05+09:00", exit_code: 1, stage: null,
        progress: null, log: null, warnings: [], summary: null, artifacts: [],
        error: null, cancel_requested: false, ...extra};
}

// A refusal as api.ts raises it.
function refused(status, fields = {}) {
    return Object.assign(new Error(`HTTP ${status}`), {status, ...fields});
}

// The page with a run already going in the plugin (a long one).
async function withRunning(options = {}) {
    const api = createMockPluginsApi({runningReads: 1000, ...options});
    await api.startRun(WEBHOOK, {
        job: "export_log", trigger: {kind: "manual", target: null},
        params: {}, inputs: {}});
    return api;
}

async function openWebhook(api) {
    show(api);
    fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
    return screen.findByTestId("plugin-detail");
}

describe("following a run", () => {
    test("a run still going is followed again when the page comes back",
         async () => {
             const api = await withRunning();
             await openWebhook(api);
             expect(await screen.findByTestId("plugin-run-progress"))
                 .toBeTruthy();
             // [1/2] Sending, 30 %, the latest log line, and Stop.
             expect(screen.getByText("[1/2] Sending")).toBeTruthy();
             expect(screen.getByRole("progressbar")
                 .getAttribute("aria-valuenow")).toBe("30");
             expect(screen.getByRole("button", {name: "Stop"})).toBeEnabled();
             // The jobs wait: one run at a time.
             expect(screen.getByTestId("plugin-run-export_log")).toBeDisabled();
         });

    test("the polling stops when the page is left", async () => {
        const api = await withRunning();
        const read = jest.spyOn(api, "run");
        await openWebhook(api);
        await screen.findByTestId("plugin-run-progress");
        cleanup();
        const after = read.mock.calls.length;
        await act(async () => {
            await sleep(1300);
        });
        expect(read.mock.calls.length).toBe(after);
    });

    test("the polling stops when the run ends", async () => {
        const api = await withRunning({runningReads: 1});
        const read = jest.spyOn(api, "run");
        await openWebhook(api);
        await screen.findByTestId("plugin-run-progress");
        await screen.findByTestId("plugin-run-view", {}, {timeout: 4000});
        const after = read.mock.calls.length;
        await act(async () => {
            await sleep(1300);
        });
        expect(read.mock.calls.length).toBe(after);
        // The jobs can start again.
        expect(screen.getByTestId("plugin-run-export_log")).toBeEnabled();
    });

    test("a 404 stops the polling at once and says why", async () => {
        const api = await withRunning();
        jest.spyOn(api, "run").mockRejectedValue(refused(404));
        const read = api.run;
        await openWebhook(api);
        const lost = await screen.findByTestId("plugin-run-lost");
        expect(lost.textContent).toBe("The job was not found.");
        await act(async () => {
            await sleep(1300);
        });
        expect(read).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId("plugin-run-export_log")).toBeEnabled();
    });

    test("a 403 stops it too", async () => {
        const api = await withRunning();
        jest.spyOn(api, "run").mockRejectedValue(refused(403));
        await openWebhook(api);
        expect((await screen.findByTestId("plugin-run-lost")).textContent)
            .toContain("not allowed");
    });

    test("other failures are tried again a few times, then it gives up",
         async () => {
             const api = await withRunning();
             const read = jest.spyOn(api, "run")
                 .mockRejectedValue(refused(500));
             await openWebhook(api);
             const lost = await screen.findByTestId(
                 "plugin-run-lost", {}, {timeout: 8000});
             expect(lost.textContent).toContain("can no longer be read");
             expect(read).toHaveBeenCalledTimes(5);
             expect(screen.getByTestId("plugin-run-export_log")).toBeEnabled();
         });

    test("Stop asks the host and waits for the end once a stop is asked",
         async () => {
             const api = await withRunning();
             const cancel = jest.spyOn(api, "cancelRun");
             const [going] = await api.runs(WEBHOOK);
             await openWebhook(api);
             fireEvent.click(await screen.findByRole("button", {name: "Stop"}));
             expect(cancel).toHaveBeenCalledWith(WEBHOOK, going.run_id);
             // The next read says the stop is asked: the button waits.
             jest.spyOn(api, "run").mockResolvedValue(
                 {...going, cancel_requested: true});
             await waitFor(() => expect(
                 screen.getByRole("button", {name: "Stop"})).toBeDisabled(),
                          {timeout: 3000});
         });
});

describe("the detail's reads", () => {
    test("a slow answer for the plugin left behind is dropped", async () => {
        const api = createMockPluginsApi();
        const real = api.detail;
        let release;
        jest.spyOn(api, "detail").mockImplementation((id) => (
            id === WEBHOOK
                ? new Promise((resolve) => {
                    release = async () => resolve(await real(id));
                })
                : real(id)));
        show(api);
        fireEvent.click(await screen.findByTestId(`plugin-row-${WEBHOOK}`));
        fireEvent.click(screen.getByTestId("plugin-row-me.sandbox"));
        await waitFor(() => expect(screen.getByTestId("plugin-detail")
            .textContent).toContain("me.sandbox"));
        await act(async () => {
            await release();
        });
        expect(screen.getByTestId("plugin-detail").textContent)
            .toContain("me.sandbox");
        expect(screen.getByTestId("plugin-detail").textContent)
            .not.toContain("taguyan.webhook");
    });

    test("a plugin in development can be read again", async () => {
        const api = createMockPluginsApi();
        const reload = jest.spyOn(api, "reload");
        show(api);
        fireEvent.click(await screen.findByTestId("plugin-row-me.sandbox"));
        fireEvent.click(await screen.findByTestId("plugin-reload"));
        expect(reload).toHaveBeenCalledWith("me.sandbox");
        // An installed plugin has no such button.
        fireEvent.click(screen.getByTestId(`plugin-row-${WEBHOOK}`));
        await waitFor(() => expect(screen.getByTestId("plugin-detail")
            .textContent).toContain(WEBHOOK));
        expect(screen.queryByTestId("plugin-reload")).toBeNull();
    });
});

describe("wiring (I1b)", () => {
    test("a dev folder from the environment has no remove button", async () => {
        const api = createMockPluginsApi();
        jest.spyOn(api, "dev").mockResolvedValue({enabled: true, folders: [
            {id: "aaaaaaaaaaaa", name: "FromEnv", from_env: true},
            {id: "bbbbbbbbbbbb", name: "Mine"}]});
        show(api);
        const list = await screen.findByTestId("plugin-dev-folders");
        await within(list).findByText("FromEnv");
        expect(within(list).getAllByRole("button", {name: "Remove"}))
            .toHaveLength(1);
    });

    test("a dev folder from the environment shows with the switch off",
         async () => {
             const api = createMockPluginsApi();
             jest.spyOn(api, "dev").mockResolvedValue({
                 enabled: false, folders: [
                     {id: "aaaaaaaaaaaa", name: "FromEnv", from_env: true},
                     {id: "bbbbbbbbbbbb", name: "Mine"}]});
             show(api);
             const list = await screen.findByTestId("plugin-dev-folders");
             expect(within(list).getByText("FromEnv")).toBeTruthy();
             expect(within(list).getByText("from the environment"))
                 .toBeTruthy();
             expect(within(list).queryByText("Mine")).toBeNull();
             expect(within(list).queryByRole("button")).toBeNull();
         });

    test("time limits read in the largest fitting unit", () => {
        expect(timeoutText(30)).toBe("Time limit: 30 s");
        expect(timeoutText(60)).toBe("Time limit: 1 min");
        expect(timeoutText(150)).toBe("Time limit: 2 min");
        expect(timeoutText(3600)).toBe("Time limit: 1 h");
        expect(timeoutText(5400)).toBe("Time limit: 1 h 30 min");
    });

    test("a job without a time limit says so", async () => {
        const api = createMockPluginsApi();
        const detail = await api.detail(WEBHOOK);
        detail.jobs[0].timeout_seconds = null;
        jest.spyOn(api, "detail").mockResolvedValue(detail);
        await openWebhook(api);
        expect(screen.getByTestId(`plugin-timeout-${detail.jobs[0].id}`)
            .textContent).toBe("No time limit");
        expect(screen.getByTestId(`plugin-timeout-${detail.jobs[1].id}`)
            .textContent).toBe("Time limit: 30 s");
    });

    test("only the built-in vodsplit has the open button", async () => {
        for (const [id, badge] of [["taguyan.vodsplit", "listed"],
                                   ["me.other", "builtin"]]) {
            cleanup();
            const api = createMockPluginsApi();
            const detail = {...(await api.detail(WEBHOOK)), id, badge};
            const list = await api.list();
            jest.spyOn(api, "list").mockResolvedValue(
                [{...list[0], id, badge}]);
            jest.spyOn(api, "detail").mockResolvedValue(detail);
            show(api);
            fireEvent.click(await screen.findByTestId(`plugin-row-${id}`));
            await screen.findByTestId("plugin-detail");
            expect(screen.queryByTestId("plugin-open-archive")).toBeNull();
        }
    });

    test("the built-in vodsplit opens the archive page", async () => {
        const api = createMockPluginsApi();
        const detail = {...(await api.detail(WEBHOOK)),
                        id: "taguyan.vodsplit", badge: "builtin"};
        const list = await api.list();
        jest.spyOn(api, "list").mockResolvedValue(
            [...list, {...list[0], id: "taguyan.vodsplit",
                       badge: "builtin"}]);
        jest.spyOn(api, "detail").mockResolvedValue(detail);
        show(api);
        fireEvent.click(await screen.findByTestId(
            "plugin-row-taguyan.vodsplit"));
        fireEvent.click(await screen.findByTestId("plugin-open-archive"));
        expect(window.location.pathname).toMatch(/\/archive$/);
    });
});

describe("what the contract asks the page to show", () => {
    async function openHistory(api) {
        await openWebhook(api);
        fireEvent.click(within(await screen.findByTestId("plugin-history"))
            .getByRole("button"));
        return screen.findByTestId("plugin-run-view");
    }

    test("the end reason, the early finish, the error code and the log",
         async () => {
             const api = createMockPluginsApi();
             jest.spyOn(api, "runs").mockResolvedValue([record({
                 reason: "entry_missing", finished_before_cancel: true,
                 error: {code: "boom", message: "It broke", hint: "Retry"}})]);
             const log = jest.spyOn(api, "runLog");
             const view = await openHistory(api);
             expect(within(view).getByTestId("plugin-run-reason").textContent)
                 .toContain("Antivirus software may have quarantined it");
             expect(within(view).getByTestId("plugin-run-early").textContent)
                 .toBe("The job had already finished before the stop.");
             expect(within(view).getByTestId("plugin-run-error").textContent)
                 .toBe("It broke Retry (boom)");
             // A failed run opens the worker's log without a click.
             await waitFor(() => expect(log).toHaveBeenCalled());
             expect((await screen.findByTestId("plugin-run-log")).textContent)
                 .toContain("stderr line");
         });

    test("each reason has its words; an unknown one shows its code",
         async () => {
             const en = i18n.getFixedT("en");
             for (const reason of ["launch_failed", "entry_missing",
                                   "timed_out", "memory_limit",
                                   "abnormal_exit", "autona_exited"]) {
                 expect(en(`plugins_reason_${reason}`)).not.toBe(
                     `plugins_reason_${reason}`);
             }
             const api = createMockPluginsApi();
             jest.spyOn(api, "runs").mockResolvedValue(
                 [record({state: "error", reason: "something_new"})]);
             const view = await openHistory(api);
             expect(within(view).getByTestId("plugin-run-reason").textContent)
                 .toBe("something_new");
             // A run that is not failed keeps its log closed.
             expect(screen.queryByTestId("plugin-run-log")).toBeNull();
         });

    test("a plugin's words are text, never markup", async () => {
        const api = createMockPluginsApi();
        jest.spyOn(api, "runs").mockResolvedValue([record({
            state: "done", exit_code: 0,
            summary: "<b>bold</b> summary",
            artifacts: [{path: "r.txt", kind: "text",
                         label: "<i>label</i>"}]})]);
        jest.spyOn(api, "artifactText").mockResolvedValue("<b>x</b>\n<script>");
        const view = await openHistory(api);
        expect(view.textContent).toContain("<b>bold</b> summary");
        await waitFor(() => expect(
            screen.getByTestId("plugin-artifact-0-text").textContent)
            .toBe("<b>x</b>\n<script>"));
        expect(view.textContent).toContain("<i>label</i>");
        expect(view.querySelector("b, i, script")).toBeNull();
    });

    test("the history names the job and formats the time", async () => {
        const api = createMockPluginsApi();
        jest.spyOn(api, "runs").mockResolvedValue([record({state: "done"})]);
        await openWebhook(api);
        const row = within(await screen.findByTestId("plugin-history"))
            .getByRole("button");
        expect(row.textContent).toContain("Post the result image");
        expect(row.textContent).not.toContain("2026-11-26T10:00:00+09:00");
        expect(row.textContent).toContain("Done");
    });
});

describe("refusals and access", () => {
    test("a refused start says why (busy carries the host's seconds)",
         async () => {
             const api = createMockPluginsApi();
             await openWebhook(api);
             jest.spyOn(api, "startRun").mockRejectedValue(refused(409, {
                 code: "CONFLICT", reason: "busy", detail: "12"}));
             fireEvent.mouseDown(within(screen.getByTestId(
                 "plugin-job-export_log")).getByRole("combobox"));
             fireEvent.click(await screen.findByRole("option", {
                 name: "2026-11-26 Fake Cup"}));
             fireEvent.click(screen.getByTestId("plugin-run-export_log"));
             expect(await screen.findByText(
                 "Wait until the running job ends, then try again (12 s left)."))
                 .toBeTruthy();
         });

    test("a busy refusal without seconds says it without them", () => {
        for (const detail of [undefined, "", "soon", "0"]) {
            expect(refusalText(refused(409, {reason: "busy", detail})))
                .toBe("Wait until the running job ends, then try again.");
        }
        expect(refusalText(refused(409, {reason: "busy", detail: "3"})))
            .toBe("Wait until the running job ends, then try again"
                  + " (3 s left).");
    });

    test("refusal and pick wording", () => {
        expect(refusalText(refused(409, {reason: "needs_settings"})))
            .toBe("Settings are needed.");
        expect(refusalText(refused(409, {code: "BLOCKED"})))
            .toBe("This plugin is blocked.");
        expect(refusalText(refused(500))).toBe("It did not work.");
        expect(pickErrorText({code: "superseded"})).toBeNull();
        expect(pickErrorText({code: "busy"})).toBeNull();
        expect(pickErrorText({code: "unknown_verb"}))
            .toBe("This cannot be chosen here yet.");
        expect(pickErrorText({code: "timeout"}))
            .toBe("The picker could not be opened.");
        expect(pickErrorText({code: "bad_extension"}))
            .toBe("A file of this type cannot be chosen.");
        expect(pickErrorText({code: "not_local"})).toBe(
            "A network path (\\\\server) cannot be chosen.");
    });

    test("in another computer's browser it lists and explains", async () => {
        mockAccess = "elsewhere";
        show(createMockPluginsApi());
        const row = await screen.findByTestId(`plugin-row-${WEBHOOK}`);
        expect(row).toBeDisabled();
        expect(screen.getByText(/only inside Autona on this PC/)).toBeTruthy();
        expect(screen.queryByText("For developers")).toBeNull();
    });

    test("a probe that failed says so and tries again", async () => {
        mockAccess = "failed";
        show(createMockPluginsApi());
        const alert = await screen.findByTestId("plugin-access-failed");
        fireEvent.click(within(alert).getByRole("button", {name: "Reload"}));
        expect(mockRetry).toHaveBeenCalledTimes(1);
    });

    test("a list that cannot be read offers to read it again", async () => {
        const api = createMockPluginsApi();
        const list = jest.spyOn(api, "list").mockRejectedValueOnce(
            refused(500));
        show(api);
        fireEvent.click(await screen.findByRole("button", {name: "Reload"}));
        expect(await screen.findByTestId(`plugin-row-${WEBHOOK}`)).toBeTruthy();
        expect(list).toHaveBeenCalledTimes(2);
    });
});

describe("saving settings", () => {
    test("a secret goes to its own route, never into the settings body",
         async () => {
             const api = createMockPluginsApi();
             const settings = jest.spyOn(api, "saveSettings");
             const secret = jest.spyOn(api, "saveSecret");
             await openWebhook(api);
             const input = screen.getByTestId("plugin-setting-webhook_url");
             fireEvent.change(input, {target: {value: "https://hook"}});
             fireEvent.click(screen.getByTestId("plugin-settings-save"));
             await waitFor(() => expect(secret).toHaveBeenCalledWith(
                 WEBHOOK, "webhook_url", "https://hook"));
             expect(settings).toHaveBeenCalledWith(WEBHOOK, {mention: "none"});
             expect(JSON.stringify(settings.mock.calls))
                 .not.toContain("https://hook");
             // Saved: the box is empty again and says "Set".
             await waitFor(() => expect(
                 screen.getByTestId("plugin-setting-webhook_url").value)
                 .toBe(""));
             const clear = await screen.findByRole("button", {name: "Clear"});
             const cleared = jest.spyOn(api, "clearSecret");
             fireEvent.click(clear);
             await waitFor(() => expect(cleared).toHaveBeenCalledWith(
                 WEBHOOK, "webhook_url"));
         });

    test("an item this Autona cannot use does not stop the others from saving",
         async () => {
             const api = createMockPluginsApi();
             const real = api.detail;
             jest.spyOn(api, "detail").mockImplementation(async (id) => {
                 const detail = await real(id);
                 detail.settings.push({key: "odd", type: "future",
                                       label: "Odd"});
                 return detail;
             });
             const settings = jest.spyOn(api, "saveSettings");
             await openWebhook(api);
             expect(screen.getByTestId("plugin-setting-odd").textContent)
                 .toContain("future");
             fireEvent.change(screen.getByTestId("plugin-setting-webhook_url"),
                              {target: {value: "https://hook"}});
             fireEvent.click(screen.getByTestId("plugin-settings-save"));
             await waitFor(() => expect(settings).toHaveBeenCalledWith(
                 WEBHOOK, {mention: "none"}));
         });

    test("a value let go is sent as null", () => {
        const fields = [
            {key: "t", type: "text", label: "T"},
            {key: "n", type: "number", label: "N"},
            {key: "s", type: "select", label: "S",
             options: [{value: "a", label: "A"}]},
            {key: "dir", type: "folder", label: "D"},
            {key: "keep", type: "folder", label: "K"},
            {key: "b", type: "bool", label: "B"}];
        const values = {
            ...initialValues(fields), b: true,
            dir: {pick: null, stored: null, cleared: true},
            keep: {pick: null, stored: "Kept"}};
        expect(collectValues(fields, values, {clearing: true}).params)
            .toEqual({t: null, n: null, s: null, dir: null, b: true});
        // A run's params leave the empty ones out.
        expect(collectValues(fields, values).params).toEqual({b: true});
    });

    test("lengths count code points", () => {
        const field = {key: "t", type: "text", label: "T", max_length: 2};
        expect(validateField(field, "😀😀")).toBeNull();
        expect(validateField(field, "😀😀😀")).toBe("too_long");
    });
});

describe("FieldForm picks", () => {
    test("the pick buttons wait while a dialog is open", async () => {
        const fields = [{key: "dir", type: "folder", label: "Dir"},
                        {key: "img", type: "file", label: "Img",
                         extensions: ["png"]}];
        let finish;
        const pickFolder = jest.fn(() => new Promise((resolve) => {
            finish = () => resolve({id: "p1", name: "Dir1"});
        }));
        const pickFile = jest.fn(async () => null);
        const Host = () => {
            const [values, setValues] = React.useState(initialValues(fields));
            return (
                <FieldForm fields={fields} values={values}
                           onChange={setValues} language="en"
                           pickFolder={pickFolder} pickFile={pickFile}/>
            );
        };
        render(<Host/>);
        const folderButton = screen.getByTestId("plugin-field-dir-pick");
        const fileButton = screen.getByTestId("plugin-field-img-pick");
        fireEvent.click(folderButton);
        expect(folderButton.disabled).toBe(true);
        expect(fileButton.disabled).toBe(true);
        fireEvent.click(folderButton);
        fireEvent.click(fileButton);
        expect(pickFolder).toHaveBeenCalledTimes(1);
        expect(pickFile).not.toHaveBeenCalled();
        await act(async () => {
            finish();
        });
        expect(folderButton.disabled).toBe(false);
        expect(fileButton.disabled).toBe(false);
        expect(screen.getByTestId("plugin-field-dir-name").textContent)
            .toBe("Dir1");
    });

    test("a pick without a name says chosen, and can be released", async () => {
        const fields = [{key: "dir", type: "folder", label: "Dir"}];
        const Host = () => {
            const [values, setValues] = React.useState(initialValues(fields));
            return (
                <FieldForm fields={fields} values={values}
                           onChange={setValues} language="en"
                           pickFolder={async () => ({id: "p1"})}/>
            );
        };
        render(<Host/>);
        fireEvent.click(screen.getByRole("button", {name: "Choose a folder"}));
        await waitFor(() => expect(
            screen.getByTestId("plugin-field-dir-name").textContent)
            .toBe("Chosen"));
        fireEvent.click(screen.getByRole("button", {name: "Release"}));
        expect(screen.getByTestId("plugin-field-dir-name").textContent)
            .toBe("Not chosen");
    });

    test("a pick that ends after the form changed keeps the newer values",
         async () => {
             const fields = [
                 {key: "a", type: "text", label: "A"},
                 {key: "dir", type: "folder", label: "Dir"}];
             let finish;
             const Host = () => {
                 const [values, setValues] = React.useState(
                     initialValues(fields));
                 return (
                     <FieldForm fields={fields} values={values}
                                onChange={setValues} language="en"
                                pickFolder={() => new Promise((resolve) => {
                                    finish = () => resolve({id: "p1",
                                                            name: "Late"});
                                })}/>
                 );
             };
             render(<Host/>);
             fireEvent.click(screen.getByRole("button",
                                              {name: "Choose a folder"}));
             fireEvent.change(screen.getByLabelText("A"),
                              {target: {value: "typed meanwhile"}});
             await act(async () => {
                 finish();
             });
             expect(screen.getByLabelText("A").value)
                 .toBe("typed meanwhile");
             expect(screen.getByTestId("plugin-field-dir-name").textContent)
                 .toBe("Late");
         });

    test("a source's choices are read again when the list opens", async () => {
        const fields = [{key: "img", type: "result_image", label: "Image"}];
        const load = jest.fn(async () => [{id: "r1", label: "one.png"}]);
        render(<FieldForm fields={fields} values={initialValues(fields)}
                          onChange={() => {}} language="en"
                          loadChoices={load}/>);
        await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
        fireEvent.mouseDown(screen.getByRole("combobox"));
        await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
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
