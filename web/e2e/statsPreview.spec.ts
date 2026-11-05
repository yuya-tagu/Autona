import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Stats Display Settings panel in React (ADR-005 S4l): /web/statsPreview
// on the headless app keeps the chosen overlay through
// PUT /api/v1/stats/overlays/selected (by key: the player 1 and player 2
// entries share one folder), previews it from /layout/<folder>/<entry> and
// shows the Companion macro's request. Checked through text, the DOM and the
// API, never screenshots.

let pageErrors: string[] = [];

async function selectedKey(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/stats/overlays"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.selected.key;
}

async function choose(request: APIRequestContext, key: string) {
    const response = await request.put(
        serverUrl("/api/v1/stats/overlays/selected"), {data: {key}});
    expect(response.ok()).toBe(true);
}

function typeSelect(page: Page) {
    return page.getByRole("combobox", {name: "Choose the statistics overlay"});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) =>
        pageErrors.push(`/web/statsPreview: ${error}`));
    await keepLocal(page);
    await choose(request, "upset_factor");
});

test.afterEach(async ({request}) => {
    await choose(request, "upset_factor");
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page opens on the kept overlay with the OBS URL and the macro", async ({page, request}) => {
    await choose(request, "last_sets_p1");
    await page.goto(serverUrl("/web/statsPreview"));

    await expect(page).toHaveTitle("Autona Stats Display Settings");
    await expect(typeSelect(page)).toHaveText("Last Sets (Player 1)");
    await expect(page.getByTitle("Preview")).toHaveAttribute(
        "src", /\/layout\/last_sets\/player1\.html$/);
    await expect(page.getByRole("textbox", {name: "URL for OBS"}))
        .toHaveValue(/\/layout\/last_sets\/player1\.html$/);

    const macro = page.getByRole("region", {name: "Companion macro"});
    await expect(macro).toContainText("POST");
    await expect(macro).toContainText("/scoreboards/1/stats:refresh");
    await expect(page.getByRole("textbox", {name: "Request URL"}))
        .toHaveValue(/\/api\/v1\/scoreboards\/1\/stats:refresh$/);
});

test("the choice is kept, and player 2 of one folder is not player 1", async ({page, request}) => {
    await page.goto(serverUrl("/web/statsPreview"));
    await expect(typeSelect(page)).toHaveText("Upset Factor");

    await typeSelect(page).click();
    await page.getByRole("option", {name: "Last Sets (Player 2)"}).click();

    await expect.poll(() => selectedKey(request)).toBe("last_sets_p2");
    await expect(page.getByTitle("Preview")).toHaveAttribute(
        "src", /\/layout\/last_sets\/player2\.html$/);

    await typeSelect(page).click();
    await page.getByRole("option", {name: "Last Sets (Player 1)"}).click();

    await expect.poll(() => selectedKey(request)).toBe("last_sets_p1");
    await expect(page.getByTitle("Preview")).toHaveAttribute(
        "src", /\/layout\/last_sets\/player1\.html$/);

    // A reload shows the kept one.
    await page.reload();
    await expect(typeSelect(page)).toHaveText("Last Sets (Player 1)");
});

test("an unknown key is refused and nothing is kept", async ({request}) => {
    await choose(request, "recent_sets");

    const response = await request.put(
        serverUrl("/api/v1/stats/overlays/selected"), {data: {key: "gone"}});

    expect(response.status()).toBe(400);
    expect((await response.json()).error.code).toBe("INVALID_ARGUMENT");
    expect(await selectedKey(request)).toBe("recent_sets");
});
