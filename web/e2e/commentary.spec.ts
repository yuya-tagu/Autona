import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Commentary panel in React (ADR-005 S4b): /web/commentary on the
// headless app writes the casters through /api/v1/commentary, and the OBS
// overlay (/layout/commentators) draws them. Checked through text and the
// DOM, never screenshots.

let pageErrors: string[] = [];

function collectErrors(page: Page, name: string) {
    page.on("pageerror", (error) => pageErrors.push(`${name}: ${error}`));
}

async function commentary(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.commentary ?? {};
}

// The tests share one app: slots 1-5 start empty and hidden.
async function clearCasters(request: APIRequestContext) {
    for (const slot of ["1", "2", "3", "4", "5"]) {
        const response = await request.patch(
            serverUrl(`/api/v1/commentary/${slot}`), {data: {
                name: "", team: "", twitter: "", role: "", icon: "",
                note: "", display: false,
            }});
        expect(response.ok()).toBe(true);
    }
}

function slot(page: Page, number: string) {
    return page.getByRole("region", {name: `Slot ${number}`, exact: true});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    collectErrors(page, "/web/commentary");
    await keepLocal(page);
    await clearCasters(request);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Commentary page opens with its twelve slots and the design", async ({page}) => {
    await page.goto(serverUrl("/web/commentary"));

    await expect(page).toHaveTitle("Autona Commentary");
    await expect(slot(page, "1")).toBeVisible();
    await expect(page.getByRole("region", {name: /^Slot \d+$/})).toHaveCount(12);
    // The built-in design is selected, previewed and given to OBS.
    await expect(page.getByRole("textbox", {name: "URL for OBS"}))
        .toHaveValue(/\/layout\/commentators\/index\.html$/);
    await expect(page.getByTitle("Preview"))
        .toHaveAttribute("src", /\/layout\/commentators\/index\.html$/);
});

test("a caster typed in and shown appears on the overlay", async ({page, context, request}) => {
    const overlay = await context.newPage();
    collectErrors(overlay, "overlay");
    await keepLocal(overlay);
    await overlay.goto(serverUrl("/layout/commentators/index.html"));
    await overlay.waitForFunction(
        () => (window as any).tshSocket?.connected === true);

    await page.goto(serverUrl("/web/commentary"));
    const card = slot(page, "1");
    await card.getByRole("combobox", {name: "Name"}).fill("E2E Caster");
    await card.getByRole("textbox", {name: "Team / affiliation"}).fill("E2E Team");
    await card.getByRole("checkbox", {name: "Show on stream"}).check();

    await expect.poll(async () => (await commentary(request))["1"])
        .toMatchObject({name: "E2E Caster", team: "E2E Team", display: 1});
    await expect(overlay.locator(".caster-card .name")).toHaveText("E2E Caster");
    await expect(overlay.locator(".caster-card .team")).toHaveText("E2E Team");
});

test("with four shown, a fifth cannot be checked", async ({page, request}) => {
    for (const number of ["1", "2", "3", "4"]) {
        const response = await request.patch(
            serverUrl(`/api/v1/commentary/${number}`),
            {data: {name: `Caster ${number}`, display: true}});
        expect(response.ok()).toBe(true);
    }

    await page.goto(serverUrl("/web/commentary"));

    await expect(slot(page, "4").getByRole("checkbox", {name: "Show on stream"}))
        .toBeChecked();
    await expect(slot(page, "5").getByRole("checkbox", {name: "Show on stream"}))
        .toBeDisabled();
    // And the server refuses it all the same.
    const refused = await request.patch(
        serverUrl("/api/v1/commentary/5"), {data: {display: true}});
    expect(refused.status()).toBe(409);
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/commentary?desktop=1&lng=ja"));

    const first = page.getByRole("region", {name: "スロット 1", exact: true});
    await expect(first).toBeVisible();
    await expect(first.getByRole("checkbox", {name: "配信に表示"})).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
});
