import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Board panel in React (ADR-005 S4s): /web/board on the headless app keeps
// the chosen design through PUT /api/v1/layouts/selected/board, previews it from
// /layout/<design>/<entry> with the winner popup optionally on top, and gives
// the OBS files' paths. "Show in Explorer" is taken off the wire before the
// server: this PC's request would really open a window (the unknown target's
// refusal starts nothing, and goes to the server). Checked through text, the DOM
// and the API, never screenshots.

const SELECTED = "/api/v1/layouts/selected/board";
const REVEAL = `${SELECTED}:reveal`;
const DEFAULT = "scoreboard_crewbattle";

let pageErrors: string[] = [];

async function selectedId(request: APIRequestContext) {
    const response = await request.get(serverUrl(SELECTED));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.id;
}

async function choose(request: APIRequestContext, id: string) {
    const response = await request.put(serverUrl(SELECTED), {data: {id}});
    expect(response.ok()).toBe(true);
}

function designSelect(page: Page) {
    return page.getByRole("combobox", {name: "Choose the scoreboard design"});
}

function popupSelect(page: Page) {
    return page.getByRole("combobox", {name: "Show the winner popup on the preview"});
}

function preview(page: Page) {
    return page.getByTitle("Preview", {exact: true});
}

async function pick(page: Page, select: ReturnType<Page["getByRole"]>, name: string) {
    await select.click();
    await page.getByRole("option", {name, exact: true}).click();
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/board: ${error}`));
    await keepLocal(page);
    await choose(request, DEFAULT);
});

test.afterEach(async ({request}) => {
    await choose(request, DEFAULT);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page opens on the kept design, its metadata and its OBS paths", async ({page}) => {
    await page.goto(serverUrl("/web/board"));

    await expect(page).toHaveTitle("Autona Scoreboard Settings");
    await expect(designSelect(page)).toHaveText("Crew Battle");
    await expect(preview(page)).toHaveAttribute(
        "src", /\/layout\/scoreboard_crewbattle\/index\.html$/);
    await expect(page.getByText("Format: クルーバトル")).toBeVisible();
    await expect(page.getByText("Game: スマブラSP（ストック制）")).toBeVisible();

    // Absolute, with this machine's separators: what OBS is told to read.
    const design = page.getByRole("textbox", {name: "Scoreboard path"});
    await expect(design).toHaveValue(/[\\/]layout[\\/]scoreboard_crewbattle[\\/]index\.html$/);
    await expect(design).toHaveAttribute("readonly");
    await expect(page.getByRole("textbox", {name: "Winner popup path"}))
        .toHaveValue(/[\\/]layout[\\/]winner_popup[\\/]index\.html$/);

    // The preview is the overlay, on the picture.
    await expect(page.frameLocator('iframe[title="Preview"]').locator("body"))
        .toBeAttached();
    const backdrop = await preview(page).evaluate(
        (frame) => getComputedStyle(frame.parentElement!).backgroundImage);
    expect(backdrop).toContain("/layout/videoframe_1659973.png");
    const picture = await page.request.get(serverUrl("/layout/videoframe_1659973.png"));
    expect(picture.ok()).toBe(true);
    expect(await preview(page).evaluate(
        (frame) => getComputedStyle(frame).pointerEvents)).toBe("none");
});

test("the choice is kept, and the paths follow it", async ({page, request}) => {
    await page.goto(serverUrl("/web/board"));
    await expect(designSelect(page)).toHaveText("Crew Battle");

    await pick(page, designSelect(page), "Simple");

    await expect.poll(() => selectedId(request)).toBe("scoreboard_simple");
    await expect(preview(page)).toHaveAttribute(
        "src", /\/layout\/scoreboard_simple\/index\.html$/);
    await expect(page.getByRole("textbox", {name: "Scoreboard path"}))
        .toHaveValue(/[\\/]scoreboard_simple[\\/]index\.html$/);

    // A reload shows the kept one.
    await page.reload();
    await expect(designSelect(page)).toHaveText("Simple");
});

test("the list follows the server when the window has the focus again", async ({page, request}) => {
    await page.goto(serverUrl("/web/board"));
    await expect(designSelect(page)).toHaveText("Crew Battle");
    await preview(page).evaluate((frame) => {
        (frame as HTMLIFrameElement & {__same?: boolean}).__same = true;
    });

    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    // Past the wait before it reads (the Qt panel's 750 ms).
    await page.waitForTimeout(1000);
    // The same design: the same frame, not loaded again.
    expect(await preview(page).evaluate(
        (frame) => (frame as HTMLIFrameElement & {__same?: boolean}).__same))
        .toBe(true);

    await choose(request, "scoreboard_simple");
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(designSelect(page)).toHaveText("Simple");
});

test("the winner popup lies on the preview and stays the page's own", async ({page, request}) => {
    await page.goto(serverUrl("/web/board"));
    await expect(designSelect(page)).toHaveText("Crew Battle");
    const popup = page.getByTitle("Winner popup preview");
    await expect(popup).toHaveCount(0);
    await expect(popupSelect(page)).toHaveText("Hidden");

    await pick(page, popupSelect(page), "WINNER");

    await expect(popup).toHaveAttribute(
        "src", /\/layout\/winner_popup\/index\.html#preview=winner$/);
    const shown = page.frameLocator('iframe[title="Winner popup preview"]');
    await expect(shown.locator(".winner_popup")).toContainText("WINNER");
    await shown.locator("body").evaluate((body) => {
        (body.ownerDocument.defaultView as Window & {__same?: boolean}).__same = true;
    });

    // The fragment, not a new document: re-rendered by the page's hashchange.
    await pick(page, popupSelect(page), "CHAMPION");
    await expect(shown.locator(".winner_popup")).toContainText("CHAMPION");
    expect(await shown.locator("body").evaluate(
        (body) => (body.ownerDocument.defaultView as Window & {__same?: boolean}).__same))
        .toBe(true);

    // Another design keeps it.
    await pick(page, designSelect(page), "Simple");
    await expect(popup).toHaveAttribute("src", /#preview=champion$/);

    await pick(page, popupSelect(page), "Hidden");
    await expect(popup).toHaveCount(0);

    // Never in the state, never in the settings the page reads.
    const state = await (await request.get(serverUrl("/api/v1/state"))).text();
    expect(state).not.toContain("preview=");
});

test("show in Explorer names a target, and the page says what came of it", async ({page}) => {
    const asked: unknown[] = [];
    let answer = {status: 200, json: {ok: true} as object};
    await page.route(serverUrl(REVEAL), async (route) => {
        asked.push(route.request().postDataJSON());
        await route.fulfill(answer);
    });
    await page.goto(serverUrl("/web/board"));

    await page.getByRole("button", {name: "Scoreboard path: Show in Explorer"}).click();
    await expect.poll(() => asked.length).toBe(1);
    expect(asked[0]).toEqual({target: "design", designId: DEFAULT});

    answer = {status: 403, json: {ok: false, error: {code: "FORBIDDEN", message: "no"}}};
    await page.getByRole("button", {name: "Winner popup path: Show in Explorer"}).click();
    await expect.poll(() => asked.length).toBe(2);
    expect(asked[1]).toEqual({target: "winnerPopup"});
    await expect(page.getByText("Explorer can only be opened from the PC running Autona"))
        .toBeVisible();
});

test("the server refuses a target it does not know, starting nothing", async ({request}) => {
    const response = await request.post(serverUrl(REVEAL), {
        data: {target: "../../anything"}});
    expect(response.status()).toBe(400);
    expect((await response.json()).error.code).toBe("INVALID_ARGUMENT");
});

test("the server refuses a design that is not the selected one", async ({request}) => {
    const response = await request.post(serverUrl(REVEAL), {
        data: {target: "design", designId: "scoreboard_simple"}});
    expect(response.status()).toBe(409);
    expect((await response.json()).error.code).toBe("CONFLICT");
});

test("a Designer project is listed and previewed, with the shared runtime", async ({page, request}) => {
    await page.goto(serverUrl("/web/board"));
    await expect(designSelect(page)).toHaveText("Crew Battle");

    await pick(page, designSelect(page), "Custom: E2E");

    await expect.poll(() => selectedId(request)).toBe("custom/e2e_design");
    await expect(preview(page)).toHaveAttribute(
        "src", /\/layout\/custom\/e2e_design\/index\.html$/);
    await expect(page.getByRole("textbox", {name: "Scoreboard path"}))
        .toHaveValue(/[\\/]custom[\\/]e2e_design[\\/]index\.html$/);
    // The runtime ran in the frame and read the project: its colour, no error.
    const shown = page.frameLocator('iframe[title="Preview"]');
    await expect(shown.locator("#scoreboard-composer"))
        .toHaveCSS("background-color", "rgb(18, 52, 86)");
    await expect(shown.locator("#composer-error")).toBeEmpty();
});

test("the preview stays in a short, wide window, the frame's size with it", async ({page}) => {
    await page.setViewportSize({width: 1400, height: 620});
    await page.goto(serverUrl("/web/board"));
    await expect(designSelect(page)).toHaveText("Crew Battle");

    const size = await preview(page).evaluate((frame) => {
        const shown = frame.getBoundingClientRect();
        const box = frame.parentElement!.getBoundingClientRect();
        return {bottom: box.bottom, width: box.width, height: box.height,
                frameWidth: shown.width, frameHeight: shown.height};
    });
    expect(size.bottom).toBeLessThanOrEqual(620);
    expect(size.width / size.height).toBeCloseTo(16 / 9, 1);
    expect(Math.abs(size.frameWidth - size.width)).toBeLessThanOrEqual(2);
    expect(Math.abs(size.frameHeight - size.height)).toBeLessThanOrEqual(2);
    // The column does not have to scroll for it.
    expect(await preview(page).evaluate((frame) => {
        const column = frame.closest("main")!.children[1];
        return column.scrollHeight <= column.clientHeight;
    })).toBe(true);
});

test("the catalog names no path of this machine", async ({request}) => {
    for (const pathname of ["/api/v1/layouts?kind=scoreboard,designer", SELECTED]) {
        const text = await (await request.get(serverUrl(pathname))).text();
        expect(text).not.toMatch(/[A-Za-z]:\\\\|\\\\layout\\\\/);
    }
});

test("in a browser the Designer's button opens the page that sends you to the app", async ({page, context}) => {
    await page.goto(serverUrl("/web/board"));

    const opened = context.waitForEvent("page");
    await page.getByRole("button", {name: "Open the Designer"}).click();
    const designer = await opened;

    await designer.waitForLoadState();
    expect(new URL(designer.url()).search).toBe("?view=designer");
    await expect(designer.locator(".designer-desktop-required")).toContainText(
        "Designer");
    await designer.close();
});

test("the colours are the board's, written to the state", async ({page, request}) => {
    const clear = await request.patch(serverUrl("/api/v1/colors/board"), {
        data: {theme: "#123456"}});
    expect(clear.ok()).toBe(true);
    await page.goto(serverUrl("/web/board"));

    const card = page.getByRole("region", {name: "Scoreboard colors"});
    await expect(card.getByLabel("Theme color")).toHaveValue("#123456");

    await card.getByRole("button", {name: "Match the tournament colors"}).click();
    await expect.poll(async () => {
        const state = await (await request.get(serverUrl("/api/v1/state"))).json();
        return state.data.colors.board.themeColor;
    }).not.toBe("#123456");
});
