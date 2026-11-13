import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The settings page in React (ADR-005 S4af): /web/settings on the headless
// app reads GET /api/v1/settings and saves each section with
// PATCH /api/v1/settings/<section> (general WRITE; network, language and
// hotkeys THIS_PC, which a request from this PC passes). A language change
// is applied by a restart: the page says so and stays. A hotkey is composed
// from Ctrl / Alt / Shift and a key; one that is not a shortcut is refused
// with 400. Checked through text, the DOM and the API, never screenshots.

let pageErrors: string[] = [];

async function settings(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/settings"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data;
}

async function save(request: APIRequestContext, section: string,
                    body: Record<string, unknown>) {
    return request.patch(serverUrl(`/api/v1/settings/${section}`),
                         {data: body});
}

// Every test leaves what it changed as the defaults: the specs share one app.
async function restore(request: APIRequestContext) {
    const read = await settings(request);
    for (const [section, body] of [
        ["general", {disable_scoreupdate: read.defaults.general.disable_scoreupdate}],
        ["language", {program: read.defaults.language.program}],
        ["hotkeys", {keys: {swap_teams: read.hotkeys.keys.swap_teams.default}}],
    ] as const) {
        const response = await save(request, section, body);
        expect(response.ok(), `${section} restored`).toBe(true);
    }
}

// A select is named by its label and the value it shows, and the other two
// language selects' values say "program language" too.
const PROGRAM_LANGUAGE = /^Program language/;

function region(page: Page, name: string) {
    return page.getByRole("region", {name, exact: true});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/settings: ${error}`));
    await keepLocal(page);
    await restore(request);
});

test.afterEach(async ({request}) => {
    await restore(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page shows the four sections with what the server holds", async ({page, request}) => {
    const read = await settings(request);
    await page.goto(serverUrl("/web/settings"));

    await expect(page).toHaveTitle("Autona Settings");
    await expect(region(page, "General").getByRole("checkbox", {
        name: "Enable profanity filter"})).toBeChecked({
        checked: read.general.profanity_filter});
    const network = region(page, "Network");
    await expect(network.getByRole("textbox", {name: "Web server port"}))
        .toHaveValue(String(read.network.port));
    await expect(network).toContainText(
        `Listening: port ${read.network.listening.port}`);
    await expect(region(page, "Language").getByRole("combobox", {
        name: PROGRAM_LANGUAGE})).toHaveText("System language");
    // The headless app registers no hotkeys (it saves them all the same).
    const hotkeys = region(page, "Hotkeys");
    await expect(hotkeys).toContainText("registers no hotkeys");
    await expect(region(page, "Swap teams").getByLabel(
        'Current shortcut of "Swap teams"'))
        .toHaveText(read.hotkeys.keys.swap_teams.value);
});

test("a general setting is saved at once and read back", async ({page, request}) => {
    await page.goto(serverUrl("/web/settings"));
    const box = page.getByRole("checkbox", {
        name: "Disable automatic score updating for the scoreboard"});
    await expect(box).not.toBeChecked();

    await box.click();

    await expect.poll(async () =>
        (await settings(request)).general.disable_scoreupdate).toBe(true);
    await page.reload();
    await expect(page.getByRole("checkbox", {
        name: "Disable automatic score updating for the scoreboard"}))
        .toBeChecked();
});

test("a language change says it needs a restart, and the page stays", async ({page, request}) => {
    await page.goto(serverUrl("/web/settings"));
    const language = region(page, "Language");

    await language.getByRole("combobox", {name: PROGRAM_LANGUAGE}).click();
    await page.getByRole("option", {name: /Japanese/}).click();

    await expect(language).toContainText(
        "Language changes take effect when Autona is restarted.");
    const read = await settings(request);
    expect(read.language.program).not.toBe("default");
    expect(read.language.restartPending).toBe(true);
    // Still the page, still working.
    await expect(region(page, "Hotkeys")).toBeVisible();
    await expect(language.getByRole("combobox", {name: PROGRAM_LANGUAGE}))
        .toHaveText(/Japanese/);
});

test("a hotkey is composed and saved; a duplicate is told", async ({page, request}) => {
    await page.goto(serverUrl("/web/settings"));
    const row = region(page, "Swap teams");
    const current = row.getByLabel('Current shortcut of "Swap teams"');
    const saveButton = row.getByRole("button", {name: 'Save "Swap teams"'});
    await expect(current).toHaveText("Ctrl+Shift+S");
    await expect(saveButton).toBeDisabled();

    await row.getByRole("checkbox", {name: "Shift"}).click();
    await row.getByRole("checkbox", {name: "Alt"}).click();
    await row.getByRole("combobox", {name: "Key"}).click();
    await page.getByRole("option", {name: "F7", exact: true}).click();
    await saveButton.click();

    await expect(current).toHaveText("Ctrl+Alt+F7");
    await expect.poll(async () =>
        (await settings(request)).hotkeys.keys.swap_teams.value)
        .toBe("Ctrl+Alt+F7");

    // Team 1 score up's default: refused, and named.
    await row.getByRole("checkbox", {name: "Alt"}).click();
    await row.getByRole("checkbox", {name: "Shift"}).click();
    await row.getByRole("combobox", {name: "Key"}).click();
    await page.getByRole("option", {name: "1", exact: true}).click();
    await saveButton.click();

    await expect(page.getByText(
        "Hotkey conflict: 'Ctrl+Shift+1' is already assigned to: Team 1 score up."))
        .toBeVisible();
    expect((await settings(request)).hotkeys.keys.swap_teams.value)
        .toBe("Ctrl+Alt+F7");
});

test("a shortcut that is not Ctrl / Alt / Shift and one key is refused with 400", async ({request}) => {
    const before = (await settings(request)).hotkeys.keys.swap_teams.value;

    for (const shortcut of ["Ctrl+Q+W", "Ctrl+Esc", "Ctrl+Ctrl+A", "Ctrl+"]) {
        const response = await save(request, "hotkeys",
                                    {keys: {swap_teams: shortcut}});
        expect(response.status(), shortcut).toBe(400);
        expect((await response.json()).error.code, shortcut)
            .toBe("INVALID_ARGUMENT");
    }

    expect((await settings(request)).hotkeys.keys.swap_teams.value)
        .toBe(before);
});
