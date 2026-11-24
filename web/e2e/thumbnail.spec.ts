import fs from "node:fs";
import path from "node:path";
import {APIRequestContext, expect, test} from "@playwright/test";
import {appDir, keepLocal, previewOrigin, serverUrl} from "./server";

// The match thumbnail's page in React (ADR-005 S4p): /web/thumbnail on the
// headless app keeps its settings through /api/v1/thumbnail (core's
// ThumbnailService, settings.json's thumbnail_config). The headless shell
// has no off-screen view to draw with: the page says so and asks for no
// preview. Checked through text, the DOM and the API, never screenshots.
//
// A template on template contract 2 (ADR-008 D7 R2; the shipped type_a is)
// needs no renderer to preview: the page frames it from the preview origin
// and posts it GET /api/v1/thumbnail/payload, and its errors are shown in
// the layout check -- in a real browser, under the template folder's CSP.

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

// ── the live preview of a template on contract 2 (ADR-008 D7 R2) ─────────

// A contract 2 package whose drawing throws.
const BROKEN = "thumbnail_e2e_broken";
const BROKEN_FILES: Record<string, string> = {
    "settings.json": JSON.stringify({autona: {
        kind: "thumbnail", contract: 2, size: [1920, 1080],
        template: "layout.json", displayName: "E2E broken"}}),
    "layout.json": "{}",
    "thumbnail.html": `<!DOCTYPE html><html><head><meta charset="utf-8"></head>`
        + `<body><script src="../include/autona.js"></script>`
        + `<script src="./thumbnail.js"></script></body></html>`,
    "thumbnail.js": "Autona.onRender(function (payload) {\n"
        + "  missingFunction(payload);\n});\n",
};

test.describe("the live preview", () => {
    test.beforeAll(() => {
        const folder = path.join(appDir(), "layout", BROKEN);
        fs.mkdirSync(folder, {recursive: true});
        for (const [file, content] of Object.entries(BROKEN_FILES)) {
            fs.writeFileSync(path.join(folder, file), content, "utf8");
        }
    });

    test.afterAll(() => {
        fs.rmSync(path.join(appDir(), "layout", BROKEN),
                  {recursive: true, force: true});
    });

    test("the shipped template is drawn in the page's frame from the preview origin", async ({page}) => {
        const payloads: string[] = [];
        page.on("request", (request) => {
            if (request.url().includes("/api/v1/thumbnail/payload")) {
                payloads.push(request.url());
            }
        });
        const origin = await previewOrigin(page.request);
        await page.goto(serverUrl("/web/thumbnail"));

        // The page names its own origin: autona.js takes renders from it only.
        const frame = page.getByTitle("Preview", {exact: true});
        await expect(frame).toHaveAttribute(
            "src", `${origin}/layout/thumbnail/thumbnail.html?autonaParent=`
                + encodeURIComponent(new URL(serverUrl("/")).origin));
        // Drawn: the default banner, read from ../../assets under the CSP.
        const banner = page.frameLocator("iframe[title='Preview']")
            .locator(".background img");
        await expect(banner).toHaveCount(1);
        await expect.poll(() => banner.evaluate(
            (image: HTMLImageElement) => image.complete && image.naturalWidth))
            .toBeGreaterThan(0);
        expect(payloads.length).toBeGreaterThan(0);
        // Said drawn (autona:rendered), and with no errors.
        await expect(page.getByRole("status", {name: "Layout check"}))
            .toContainText("no errors");
        // Ready in time, and no error: no layout check to show.
        await page.waitForTimeout(5500);
        await expect(page.getByTestId("layout-check-not-ready")).toHaveCount(0);
        await expect(page.getByTestId("layout-check-error")).toHaveCount(0);
    });

    test("a template whose drawing throws shows its error in the layout check", async ({page, request}) => {
        const response = await request.patch(serverUrl("/api/v1/thumbnail"),
                                             {data: {template_id: BROKEN}});
        expect(response.ok(), await response.text()).toBe(true);
        await page.goto(serverUrl("/web/thumbnail"));

        await expect(page.getByTitle("Preview", {exact: true}))
            .toHaveAttribute("src", new RegExp(
                `/layout/${BROKEN}/thumbnail\\.html\\?autonaParent=[^&]+$`));
        const check = page.getByRole("region", {name: "Layout check", exact: true});
        await expect(check.getByTestId("layout-check-error").first())
            .toContainText("missingFunction");
        await expect(check).toContainText("template contract 2");
    });
});
