import {expect, Page, test} from "@playwright/test";
import {boardScores, keepLocal, resetBoard, serverUrl} from "./server";

// The headless app's two browser faces (ADR-005 S3d): the Web UI (/web)
// and an OBS overlay, both served over HTTP by `python main.py --headless`.
// Checked through text and the DOM, never screenshots.

// Uncaught errors on the pages a test opens (the Web UI, the overlay): any
// fails the test. React drops the whole page on one, and an overlay's
// handler stops drawing at one, so they are reported with whatever the
// test itself saw.
let pageErrors: string[] = [];

function collectErrors(page: Page, name: string) {
    page.on("pageerror", (error) => pageErrors.push(`${name}: ${error}`));
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    collectErrors(page, "/web");
    await keepLocal(page);
    await resetBoard(request);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Web UI opens and its score +1 reaches the state", async ({page, request}) => {
    await page.goto(serverUrl("/web"));

    await expect(page).toHaveTitle("Autona Scoreboard");
    // The current-set card (score controls) and the page-level actions.
    await expect(page.getByRole("button", {name: "Swap Teams"})).toBeVisible();
    await expect(page.getByRole("button", {name: "Clear All"})).toBeVisible();
    const plusOne = page.getByRole("button", {name: "+1", exact: true});
    await expect(plusOne).toHaveCount(2);

    await plusOne.first().click();

    await expect.poll(() => boardScores(request)).toEqual([1, 0]);
});

test("an overlay shows the score changed from the Web UI", async ({page, context, request}) => {
    const overlay = await context.newPage();
    collectErrors(overlay, "overlay");
    await keepLocal(overlay);
    await overlay.goto(serverUrl("/layout/scoreboard_simple/index.html"));
    // Its libraries came from the headless app and it is on Socket.IO.
    await overlay.waitForFunction(
        () => (window as any).tshSocket?.connected === true);

    await page.goto(serverUrl("/web"));
    const plusOne = page.getByRole("button", {name: "+1", exact: true}).first();
    await expect(plusOne).toBeVisible();
    await plusOne.click();
    await expect.poll(() => boardScores(request)).toEqual([1, 0]);

    // The score overlays draw only a board whose team 1 has a player slot:
    // the headless shell seeds the desktop's empty player cards for that.
    await expect(overlay.locator(".p1 .score")).toHaveText("1");
    await expect(overlay.locator(".p2 .score")).toHaveText("0");
});
