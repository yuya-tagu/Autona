import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The React panels' shell (ADR-005 S4a): /web/<panel> on the headless app,
// opened as a browser on another computer would and as the desktop's shared
// view does (?desktop=1&lng=...). The only panel so far is the empty page
// that checks the shell itself ("shell-check"). Checked through text and the
// DOM, never screenshots.

let pageErrors: string[] = [];

function collectErrors(page: Page) {
    page.on("pageerror", (error) => pageErrors.push(String(error)));
}

test.beforeEach(async ({page}) => {
    pageErrors = [];
    collectErrors(page);
    await keepLocal(page);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the shell check page opens in a browser and joins the state", async ({page}) => {
    await page.goto(serverUrl("/web/shell-check"));

    await expect(page.getByRole("heading", {name: "Panel shell check"})).toBeVisible();
    await expect(page).toHaveTitle("Autona Panel shell check");
    await expect(page.getByText("Opened in: a browser")).toBeVisible();
    await expect(page.getByText("Shown in a browser only")).toBeVisible();
    await expect(page.getByText("Connection: connected")).toBeVisible();
});

test("as the desktop's view opens it: desktop parts only, in its language", async ({page}) => {
    await page.goto(serverUrl("/web/shell-check?desktop=1&lng=ja"));

    await expect(page.getByRole("heading", {name: "パネルシェルの確認"})).toBeVisible();
    await expect(page.getByText("表示先: デスクトップアプリ")).toBeVisible();
    await expect(page.getByText("ブラウザでだけ表示")).toHaveCount(0);
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
    await expect(page.getByText("接続: connected")).toBeVisible();
});

test("the desktop's view moves between panels without a reload", async ({page}) => {
    await page.goto(serverUrl("/web/shell-check?desktop=1&lng=en"));
    await expect(page.getByRole("heading", {name: "Panel shell check"})).toBeVisible();
    // Gone if the page reloads.
    await page.evaluate(() => { (window as any).__sameDocument = true; });

    // What src/AutonaWebPanelHost.py runs. "elsewhere" is not a panel, so
    // the shell says so -- in the router, without asking the server.
    await page.evaluate(() => (window as any).autonaPanelShell.navigate("elsewhere"));
    await expect(page.getByText("Unknown panel: elsewhere")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/web/elsewhere");
    expect(new URL(page.url()).search).toBe("?desktop=1&lng=en");

    await page.evaluate(() => (window as any).autonaPanelShell.navigate("shell-check"));
    await expect(page.getByRole("heading", {name: "Panel shell check"})).toBeVisible();
    // And the full-state request on reveal does not break the page.
    await page.evaluate(() => (window as any).autonaPanelShell.refresh());
    await expect(page.getByText("Connection: connected")).toBeVisible();
    expect(await page.evaluate(() => (window as any).__sameDocument)).toBe(true);
});

test("only the allowed panels are pages", async ({request}) => {
    expect((await request.get(serverUrl("/web/shell-check"))).status()).toBe(200);
    expect((await request.get(serverUrl("/web/elsewhere"))).status()).toBe(404);
    // The Designer stays a query on /web.
    expect((await request.get(serverUrl("/web?view=designer"))).status()).toBe(200);
    expect((await request.get(serverUrl("/web/designer"))).status()).toBe(404);
});
