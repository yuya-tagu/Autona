import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The 配信設定 panel in React (ADR-005 S4e): /web/stream on the headless
// app writes through /api/v1/stream, and core's StreamService does the work
// (the channels in settings.json, the destination in the state, the stream
// thumbnail made by a job). Checked through text, the DOM and the API, never
// screenshots.

let pageErrors: string[] = [];

async function stream(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.stream ?? {};
}

// The tests share one app: no saved channel, no destination, the default
// thumbnail options.
async function resetStream(request: APIRequestContext) {
    const listed = await request.get(serverUrl("/api/v1/stream/channels"));
    expect(listed.ok()).toBe(true);
    const count = (await listed.json()).data.channels.length;
    for (let index = count - 1; index >= 0; index--) {
        const response = await request.delete(
            serverUrl(`/api/v1/stream/channels/${index}`));
        expect(response.ok()).toBe(true);
    }
    expect((await request.put(serverUrl("/api/v1/stream"),
                              {data: {url: ""}})).ok()).toBe(true);
    expect((await request.put(serverUrl("/api/v1/stream/thumbnail/options"), {
        data: {source: "banner", style: "center", overlayOrgIcon: false},
    })).ok()).toBe(true);
}

async function addChannel(page: Page, name: string, url: string) {
    await page.getByRole("button", {name: "Add", exact: true}).click();
    const dialog = page.getByRole("dialog", {name: "Add a stream channel"});
    await dialog.getByRole("textbox", {name: "Channel name"}).fill(name);
    await dialog.getByRole("textbox", {name: "URL"}).fill(url);
    await dialog.getByRole("button", {name: "Save"}).click();
    await expect(dialog).toBeHidden();
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/stream: ${error}`));
    await keepLocal(page);
    await resetStream(request);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Stream page opens with its four cards", async ({page}) => {
    await page.goto(serverUrl("/web/stream"));

    await expect(page).toHaveTitle("Autona Stream settings");
    for (const name of ["Destination", "Stream title",
                        "Stream org icon / thumbnail", "Stream thumbnail"]) {
        await expect(page.getByRole("region", {name, exact: true})).toBeVisible();
    }
});

test("channels added and chosen on the page are the destination in the state", async ({page, request}) => {
    await page.goto(serverUrl("/web/stream"));
    const main = "https://example.invalid/e2e-main";
    const sub = "https://example.invalid/e2e-sub";

    // An added channel becomes the destination, as in the Qt panel.
    await addChannel(page, "E2E Main", main);
    await expect.poll(async () => (await stream(request)).url).toBe(main);
    await addChannel(page, "E2E Sub", sub);
    await expect.poll(async () => (await stream(request)).url).toBe(sub);

    const select = page.getByRole("combobox", {name: "Stream channel"});
    await select.click();
    await page.getByRole("option", {name: "E2E Main"}).click();

    await expect.poll(async () => (await stream(request)).url).toBe(main);
    await expect(select).toHaveText("E2E Main");
    await expect(page.getByRole("textbox", {name: "Destination URL"}))
        .toHaveValue(main);
    const saved = await request.get(serverUrl("/api/v1/stream/channels"));
    expect((await saved.json()).data.channels).toEqual([
        {name: "E2E Main", url: main}, {name: "E2E Sub", url: sub}]);
});

test("a made thumbnail is previewed and can be copied as an image", async ({page}) => {
    // This PC's page is a secure context: the copy is offered. The
    // clipboard's write is stubbed in the page (the machine's own
    // clipboard is left alone, S4e review); what it is handed is recorded.
    await page.addInitScript(() => {
        const handed: string[][] = [];
        (window as unknown as {__clipboardWrites: string[][]})
            .__clipboardWrites = handed;
        Object.defineProperty(navigator.clipboard, "write", {
            configurable: true,
            value: async (items: ClipboardItem[]) => {
                for (const item of items) {
                    const types = [...item.types];
                    const blobs = await Promise.all(
                        types.map((type) => item.getType(type)));
                    handed.push([...types,
                                 ...blobs.map((blob) => `${blob.type}:${blob.size > 0}`)]);
                }
            },
        });
    });
    await page.goto(serverUrl("/web/stream"));
    const card = page.getByRole("region", {name: "Stream thumbnail", exact: true});

    await card.getByRole("button", {name: "Make the stream thumbnail"}).click();

    // No tournament loaded: the bundled default banner, center-cropped.
    await expect(card.getByRole("status"))
        .toHaveText(/^Saved: out\/stream\/thumbnail_banner_center_\d{8}_\d{6}\.jpg$/);
    const preview = card.getByRole("img", {name: "Stream thumbnail preview"});
    await expect(preview).toHaveAttribute(
        "src", /\/out\/stream\/thumbnail_banner_center_\d{8}_\d{6}\.jpg\?v=\d+$/);
    await expect.poll(() => preview.evaluate(
        (image: HTMLImageElement) => image.complete && image.naturalWidth))
        .toBe(1920);

    await card.getByRole("button", {name: "Copy the thumbnail to the clipboard"})
        .click();
    await expect(page.getByText("Copied the thumbnail to the clipboard"))
        .toBeVisible();
    // One item, the JPEG made a PNG (the only image type ClipboardItem
    // takes).
    const handed = await page.evaluate(() =>
        (window as unknown as {__clipboardWrites: string[][]})
            .__clipboardWrites);
    expect(handed).toEqual([["image/png", "image/png:true"]]);
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/stream?desktop=1&lng=ja"));

    await expect(page).toHaveTitle("Autona 配信設定");
    await expect(page.getByRole("region", {name: "配信先", exact: true}))
        .toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
});
