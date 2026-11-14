import {APIRequestContext, expect, Page, test, WebSocketRoute} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The AppShell (ADR-005 S-final SF-5a-3): /web/app/<panel> on the headless
// app -- the panel's page inside the navigation, the header (tournament,
// game, battle format) and the footer (Help & Settings, what the app is busy
// with). The headless app has no shell secret, so this is the browser's
// AppShell: no shell request is offered. It has no games (user_data/games is
// empty) and loads no tournament: the game list and the game write are
// stubbed, the tournament set is a refused URL. Checked through text, the
// DOM and the API, never screenshots.

let pageErrors: string[] = [];

async function tournamentInfo(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.tournamentInfo ?? {};
}

async function battleFormat(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/battle-format"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.format;
}

async function setFormat(request: APIRequestContext, format: string) {
    expect((await request.put(serverUrl("/api/v1/battle-format"),
                              {data: {format}})).ok()).toBe(true);
}

// The tests share one app: no tournament, no tournament name, 1v1.
async function reset(request: APIRequestContext) {
    expect((await request.delete(serverUrl("/api/v1/tournament"))).ok())
        .toBe(true);
    expect((await request.patch(serverUrl("/api/v1/tournament/info"),
                                {data: {tournamentName: ""}})).ok()).toBe(true);
    await setFormat(request, "singles");
}

// What is laid out wider than the view: the page itself, a control past the
// right edge (assets.spec.ts's), a pane that scrolls sideways. Only what is
// shown. A scrollable tab row (the Stats page's: MUI Tabs variant
// "scrollable", with its arrows) scrolls sideways by design: not counted.
async function overflow(page: Page) {
    return page.evaluate(() => {
        const width = document.documentElement.clientWidth;
        const shown = (element: Element) =>
            (element as HTMLElement).offsetParent !== null
            && !element.closest(".MuiTabs-scroller");
        const problems: string[] = [];
        if (document.documentElement.scrollWidth > width) {
            problems.push(`page ${document.documentElement.scrollWidth} > ${width}`);
        }
        // A pane that scrolls sideways (its content wider than it).
        for (const element of Array.from(document.querySelectorAll(
            "[data-testid=app-content] *")).filter(shown)) {
            if (element instanceof HTMLInputElement
                || element instanceof HTMLTextAreaElement) {
                continue;
            }
            const style = getComputedStyle(element);
            if ((style.overflowX === "auto" || style.overflowX === "scroll")
                && element.scrollWidth > element.clientWidth + 1) {
                const name = element.getAttribute("aria-label")
                    || element.getAttribute("data-testid")
                    || element.className.toString().slice(0, 60);
                problems.push(`${name} scrolls sideways `
                              + `(${element.scrollWidth} > ${element.clientWidth})`);
            }
        }
        for (const element of Array.from(document.querySelectorAll(
            "button, input, textarea, .MuiChip-root, label, a")).filter(shown)) {
            const right = element.getBoundingClientRect().right;
            if (right > width + 0.5) {
                const name = element.textContent?.trim()
                    || element.getAttribute("aria-label") || element.tagName;
                problems.push(`${name} ends at ${right} > ${width}`);
            }
        }
        return problems;
    });
}

function navigation(page: Page) {
    return page.getByRole("navigation", {name: "Navigation"});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await keepLocal(page);
    await reset(request);
});

test.afterEach(async ({request}) => {
    await reset(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page shows inside the frame, the item highlighted", async ({page}) => {
    await page.goto(serverUrl("/web/app/event"));

    await expect(page).toHaveTitle("Autona Tournament info");
    await expect(page.getByRole("region", {name: "start.gg tournament",
                                           exact: true})).toBeVisible();
    const current = navigation(page).getByRole("button", {name: "Tournament Info"});
    await expect(current).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("app-tournament")).toHaveText("Set tournament");
    await expect(page.getByTestId("app-tournament"))
        .toHaveAttribute("data-attention", "true");
    await expect(page.getByTestId("app-help")).toHaveText("Help & Settings");
});

test("as the desktop's view would open it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/app/event?desktop=1&lng=ja"));

    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
    await expect(page.getByRole("navigation", {name: "ナビゲーション"})
        .getByRole("button", {name: "大会情報設定"}))
        .toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("app-help")).toHaveText("ヘルプ＆設定");
});

test("the navigation moves between pages without a reload", async ({page}) => {
    await page.goto(serverUrl("/web/app/event?lng=en"));
    await expect(page).toHaveTitle("Autona Tournament info");
    // Gone if the page reloads.
    await page.evaluate(() => { (window as any).__sameDocument = true; });

    await navigation(page).getByRole("button", {name: "Stream Settings"}).click();
    await expect(page).toHaveTitle("Autona Stream settings");
    expect(new URL(page.url()).pathname).toBe("/web/app/stream");
    expect(new URL(page.url()).search).toBe("?lng=en");

    // What the desktop's view runs keeps the AppShell too.
    await page.evaluate(() => (window as any).autonaPanelShell.navigate("scoreboard"));
    await expect(page).toHaveTitle("Autona Scoreboard");
    expect(new URL(page.url()).pathname).toBe("/web/app/scoreboard");

    // A menu page highlights no item.
    await page.getByTestId("app-help").click();
    await page.getByRole("menuitem", {name: "Settings", exact: true}).click();
    await expect(page).toHaveTitle("Autona Settings");
    expect(new URL(page.url()).pathname).toBe("/web/app/settings");
    await expect(navigation(page).locator("[aria-current]")).toHaveCount(0);

    expect(await page.evaluate(() => (window as any).__sameDocument)).toBe(true);
});

test("the tournament dialog sends the URL with force, and says the refusal", async ({page, request}) => {
    const sent: unknown[] = [];
    page.on("request", (request) => {
        if (request.method() === "POST"
                && new URL(request.url()).pathname === "/api/v1/tournament") {
            sent.push(request.postDataJSON());
        }
    });
    await page.goto(serverUrl("/web/app/event"));

    await page.getByTestId("app-tournament").click();
    await page.getByRole("menuitem", {name: "Set tournament", exact: true}).click();
    const dialog = page.getByRole("dialog", {name: "Set tournament URL"});
    await dialog.getByRole("textbox", {name: "Tournament URL"})
        .fill("https://example.invalid/not-an-event");
    await dialog.getByRole("button", {name: "OK"}).click();

    await expect(dialog.getByText(/^Enter the URL of a start\.gg event/))
        .toBeVisible();
    expect(sent).toEqual([{url: "https://example.invalid/not-an-event",
                           force: true}]);
    expect((await tournamentInfo(request)).url).toBe("");
    await dialog.getByRole("button", {name: "Cancel"}).click();
    await expect(dialog).toHaveCount(0);
});

test("unsetting the tournament asks first, then clears it", async ({page, request}) => {
    expect((await request.patch(serverUrl("/api/v1/tournament/info"),
                                {data: {tournamentName: "E2E Cup"}})).ok())
        .toBe(true);
    await page.goto(serverUrl("/web/app/event"));
    const button = page.getByTestId("app-tournament");
    await expect(button).toHaveText("E2E Cup");
    await expect(button).toHaveAttribute("data-attention", "false");

    await button.click();
    await page.getByRole("menuitem", {name: "Unset tournament"}).click();
    const confirm = page.getByRole("dialog", {name: "Confirm"});
    await expect(confirm).toContainText("Disconnect from Start.gg. Are you sure?");
    await confirm.getByRole("button", {name: "Disconnect", exact: true}).click();

    await expect.poll(async () => (await tournamentInfo(request)).tournamentName)
        .toBe("");
    await expect(button).toHaveText("Set tournament");
});

test("a game picked is POST /game; the games are by name", async ({page}) => {
    const sent: unknown[] = [];
    await page.route("**/api/v1/games", (route) => route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ok: true, data: {
            zeta: {name: "Zeta Fighters"}, alpha: {name: "Alpha Brawl"}}}),
    }));
    // Not the headless app's: it has no such game (404).
    await page.route("**/api/v1/game", (route) => {
        if (route.request().method() !== "POST") {
            return route.continue();
        }
        sent.push(route.request().postDataJSON());
        return route.fulfill({
            status: 202, contentType: "application/json",
            body: JSON.stringify({ok: true, data: {accepted: true}})});
    });
    await page.goto(serverUrl("/web/app/event"));
    const button = page.getByTestId("app-game");
    await expect(button).toHaveText("Select game title");
    await expect(button).toHaveAttribute("data-attention", "true");

    await button.click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem")).toHaveText(
        ["Alpha Brawl", "Zeta Fighters", "Add game assets"]);
    await menu.getByRole("menuitem", {name: "Zeta Fighters"}).click();

    await expect.poll(() => sent).toEqual([{codename: "zeta"}]);

    await button.click();
    await page.getByRole("menuitem", {name: "Add game assets"}).click();
    expect(new URL(page.url()).pathname).toBe("/web/app/assets");
});

test("the battle format switched in the header is the app's", async ({page, request}) => {
    await page.goto(serverUrl("/web/app/event"));
    const group = page.getByRole("group", {name: "Battle format"});
    await expect(group.getByRole("button", {name: "1v1"}))
        .toHaveAttribute("aria-pressed", "true");

    await group.getByRole("button", {name: "Team"}).click();
    await expect.poll(() => battleFormat(request)).toBe("team");
    await expect(group.getByRole("button", {name: "Team"}))
        .toHaveAttribute("aria-pressed", "true");

    // Switched elsewhere: followed (the `battle_format` notice).
    await setFormat(request, "crew");
    await expect(group.getByRole("button", {name: "Crew"}))
        .toHaveAttribute("aria-pressed", "true");
});

test("the footer shows the app's job from the `status` event", async ({page}) => {
    // The app's own jobs come and go too quickly to watch: one is told to
    // the page as the server would tell it (Socket.IO's event packet), with
    // a revision newer than any the app has.
    let socket: WebSocketRoute | null = null;
    let told = false;
    await page.routeWebSocket(/\/socket\.io\//, (ws) => {
        const server = ws.connectToServer();
        server.onMessage((message) => {
            if (typeof message === "string" && message.startsWith('42["status"')) {
                told = true;
            }
            ws.send(message);
        });
        socket = ws;
    });
    await page.goto(serverUrl("/web/app/event"));
    const status = page.getByTestId("app-status");
    await expect(status).toHaveAttribute("role", "status");
    await expect.poll(() => told).toBe(true);

    socket!.send('42["status",{"revision":1000000,"jobs":[{"id":1,'
                 + '"code":"assets_load","args":{"codename":"e2e"},'
                 + '"done":1,"total":2}]}]');
    await expect(status).toHaveText("Loading game assets... (e2e)");
    await expect(status.getByRole("progressbar"))
        .toHaveAttribute("aria-valuenow", "50");

    socket!.send('42["status",{"revision":1000001,"jobs":[]}]');
    await expect(status).toHaveText("");
    await expect(status.getByRole("progressbar")).toHaveCount(0);
});

test("in a browser the Help menu has no shell request and links the addresses", async ({page}) => {
    const shellRequests: string[] = [];
    page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/web/shell-request") {
            shellRequests.push(request.url());
        }
    });
    await page.goto(serverUrl("/web/app/event"));

    await page.getByTestId("app-help").click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", {name: "About Autona"})).toBeVisible();
    await expect(menu.getByText("Always on top")).toHaveCount(0);
    await expect(menu.getByText("Check for updates")).toHaveCount(0);
    const discord = menu.getByRole("menuitem", {name: "Join the Discord community"});
    await expect(discord).toHaveAttribute("href", "https://discord.gg/yAFAmhaUqn");
    await expect(discord).toHaveAttribute("target", "_blank");
    const inquiry = menu.getByRole("menuitem", {name: "Event support inquiries"});
    await expect(inquiry).toHaveAttribute("href", "mailto:taguyangames@gmail.com");
    expect(shellRequests).toEqual([]);
});

// The pages' two-pane layouts switch on the view's width (MUI's md, 900 px),
// not the content's: beside the sidebar, the content is 196 px narrower than
// that -- 704 px at 900, 900 px at 1096.
const TWO_PANE_PAGES = ["board", "commentary", "thumbnail", "result", "top8",
                        "statsPreview", "stats", "playerBrowser"];

for (const width of [800, 900, 1000, 1096, 1280]) {
    test(`nothing is laid out past the view at ${width} px`, async ({page}) => {
        test.setTimeout(120_000);
        await page.setViewportSize({width, height: 800});
        const panels = width === 1280 ? ["event", "scoreboard", "settings"]
            : width === 800 ? ["event", "scoreboard", "settings", ...TWO_PANE_PAGES]
                : TWO_PANE_PAGES;
        for (const panel of panels) {
            await page.goto(serverUrl(`/web/app/${panel}`));
            await expect(page.getByTestId("app-sidebar")).toBeVisible();
            await expect(page.getByTestId("app-content")).not.toBeEmpty();
            // The page's own settling (its reads) before measuring.
            await page.waitForLoadState("networkidle");
            expect.soft(await overflow(page), panel).toEqual([]);
            const sidebar = await page.getByTestId("app-sidebar").boundingBox();
            const content = await page.getByTestId("app-content").boundingBox();
            expect(sidebar?.width).toBe(196);
            expect(Math.round((content?.x ?? 0) + (content?.width ?? 0)))
                .toBeLessThanOrEqual(width);
            expect(content?.x).toBe(196);
        }
    });
}

test("only the allowed panels are AppShell pages", async ({request}) => {
    expect((await request.get(serverUrl("/web/app/event"))).status()).toBe(200);
    expect((await request.get(serverUrl("/web/app/elsewhere"))).status()).toBe(404);
    expect((await request.get(serverUrl("/web/app/designer"))).status()).toBe(404);
});
