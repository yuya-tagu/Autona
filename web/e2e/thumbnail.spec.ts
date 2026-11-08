import {APIRequestContext, expect, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The match thumbnail's page in React (ADR-005 S4p): /web/thumbnail on the
// headless app keeps its settings through /api/v1/thumbnail (core's
// ThumbnailService, settings.json's thumbnail_config). The headless shell
// has no off-screen view to draw with: the page says so and asks for no
// preview. Checked through text, the DOM and the API, never screenshots.

let pageErrors: string[] = [];

// A 1x1 PNG.
const PNG = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64");

async function settings(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/thumbnail"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.settings;
}

// The tests share one app: every one starts from the defaults.
async function reset(request: APIRequestContext) {
    const response = await request.post(serverUrl("/api/v1/thumbnail:reset"),
                                        {data: {confirm: true}});
    expect(response.ok()).toBe(true);
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) =>
        pageErrors.push(`/web/thumbnail: ${error}`));
    await keepLocal(page);
    await reset(request);
});

test.afterEach(async ({request}) => {
    await reset(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page opens with its cards and says the headless app cannot draw", async ({page}) => {
    const previews: string[] = [];
    page.on("request", (request) => {
        if (request.url().includes("/api/v1/thumbnail:preview")) {
            previews.push(request.url());
        }
    });
    await page.goto(serverUrl("/web/thumbnail"));

    await expect(page).toHaveTitle("Autona Match Video Thumbnail");
    for (const name of ["Design", "Items to show", "Character image placement",
                        "Thumbnail colors", "Preview", "Video title"]) {
        await expect(page.getByRole("region", {name, exact: true})).toBeVisible();
    }
    const preview = page.getByRole("region", {name: "Preview", exact: true});
    await expect(preview.getByText(/runs without a window \(headless\)/))
        .toBeVisible();
    await expect(preview.getByRole("button", {name: "Generate thumbnail"}))
        .toBeDisabled();
    // Past the preview's wait: nothing was asked of a shell that cannot draw.
    await page.waitForTimeout(1000);
    expect(previews).toEqual([]);
});

test("a choice on the page is the saved setting", async ({page, request}) => {
    await page.goto(serverUrl("/web/thumbnail"));
    const phase = page.getByRole("checkbox", {name: "Phase name"});
    await expect(phase).toBeChecked();

    await phase.click();
    await expect.poll(async () => (await settings(request)).display_phase)
        .toBe(false);

    await page.getByRole("combobox", {name: "Template"}).click();
    await page.getByRole("option", {name: "Center Panel"}).click();
    await expect.poll(async () => (await settings(request)).template_id)
        .toBe("type_b");

    await page.getByRole("radio", {name: "Tournament banner"}).check();
    await expect.poll(async () => (await settings(request)).background_source)
        .toBe("banner");

    // A reload shows what is saved.
    await page.reload();
    await expect(page.getByRole("checkbox", {name: "Phase name"}))
        .not.toBeChecked();
    await expect(page.getByRole("combobox", {name: "Template"}))
        .toHaveText("Center Panel");
    await expect(page.getByRole("radio", {name: "Tournament banner"}))
        .toBeChecked();
});

test("an uploaded background is selected and shown by its file name", async ({page, request}) => {
    await page.goto(serverUrl("/web/thumbnail"));

    await page.getByTestId("thumbnail-background-file").setInputFiles(
        {name: "mine.png", mimeType: "image/png", buffer: PNG});

    await expect.poll(async () => (await settings(request)).background_source)
        .toBe("custom");
    const name = (await settings(request)).background_name;
    expect(name).toMatch(/^thumbnail_background_[0-9a-f]{16}\.png$/);
    await expect(page.getByText(`Image: ${name}`)).toBeVisible();
    await expect(page.getByRole("radio", {name: "An image of your own"}))
        .toBeChecked();
});

test("the defaults come back only once that is confirmed", async ({page, request}) => {
    const response = await request.patch(serverUrl("/api/v1/thumbnail"),
                                         {data: {use_sponsors: false}});
    expect(response.ok()).toBe(true);
    await page.goto(serverUrl("/web/thumbnail"));
    const sponsors = page.getByRole("checkbox", {name: "Sponsors"});
    await expect(sponsors).not.toBeChecked();

    await page.getByRole("button", {name: "Reset to defaults"}).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByRole("button", {name: "Cancel"}).click();
    await expect(dialog).toBeHidden();
    expect((await settings(request)).use_sponsors).toBe(false);

    await page.getByRole("button", {name: "Reset to defaults"}).click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", {name: "Reset", exact: true}).click();

    await expect.poll(async () => (await settings(request)).use_sponsors)
        .toBe(true);
    await expect(sponsors).toBeChecked();
});
