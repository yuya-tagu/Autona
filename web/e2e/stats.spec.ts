import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Statistics panel in React (ADR-005 S4k): /web/stats on the headless
// app draws board 1's statistics from score.1.* and asks for them through
// /api/v1 (stats:refresh, and the tournament's match list:
// analysis:refresh / GET analysis). No tournament is loaded and the players
// have no start.gg id, so nothing reaches start.gg: the tables stay empty
// here (filling them is the jest tests' job, against the Qt panel's
// wording and core's rows). Checked through text, the DOM and the API,
// never screenshots.

let pageErrors: string[] = [];

async function boardNames(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    const team = (await response.json()).data?.score?.["1"]?.team ?? {};
    // An empty card's slot has no name yet: "" for both.
    return [team["1"]?.player?.["1"]?.name ?? "",
            team["2"]?.player?.["1"]?.name ?? ""];
}

// The tests share one app: board 1's players empty.
async function resetPlayers(request: APIRequestContext) {
    const response = await request.post(
        serverUrl("/api/v1/scoreboards/1:reset-players"),
        {data: {confirm: true}});
    expect(response.ok()).toBe(true);
    await expect.poll(() => boardNames(request)).toEqual(["", ""]);
}

async function name(request: APIRequestContext, team: number, tag: string) {
    const response = await request.patch(
        serverUrl(`/api/v1/scoreboards/1/teams/${team}/players/1`),
        {data: {gamerTag: tag}});
    expect(response.ok()).toBe(true);
}

function tab(page: Page, label: string) {
    return page.getByRole("tab", {name: label, exact: true});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/stats: ${error}`));
    await keepLocal(page);
    await resetPlayers(request);
});

test.afterEach(async ({request}) => {
    await resetPlayers(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Statistics page opens with its four tabs", async ({page}) => {
    await page.goto(serverUrl("/web/stats"));

    await expect(page).toHaveTitle("Autona Statistics");
    for (const label of ["Head-to-Head", "Last Sets", "Tournament History",
                         "Matches in This Tournament"]) {
        await expect(tab(page, label)).toBeVisible();
    }
    await expect(tab(page, "Head-to-Head"))
        .toHaveAttribute("aria-selected", "true");
    // No players named: the pairing only, no upset factor.
    await expect(page.getByText("P1 vs P2", {exact: true})).toBeVisible();
    await expect(page.getByRole("table", {name: "Head-to-Head"})
        .getByText("No data")).toBeVisible();
});

test("the players named on the board head the page and its tabs", async ({page, request}) => {
    await page.goto(serverUrl("/web/stats"));
    await name(request, 1, "E2E One");
    await name(request, 2, "E2E Two");

    // Both named: the upset factor (0, no seeds) and the pairing.
    await expect(page.getByText(
        /^Upset Factor: 0\s+\|\s+E2E One vs E2E Two$/)).toBeVisible();
    // The head-to-head record, no sets yet.
    await expect(page.getByText(
        /^E2E One\s+0W \(-\)\s+-\s+0W \(-\)\s+E2E Two\s+\[0 sets\]$/))
        .toBeVisible();

    await tab(page, "Last Sets").click();
    for (const player of ["E2E One", "E2E Two"]) {
        const section = page.getByRole("region", {name: player, exact: true});
        await expect(section.getByRole("heading", {name: player}))
            .toBeVisible();
        await expect(section.getByRole("table")).toBeVisible();
    }

    await tab(page, "Tournament History").click();
    await expect(page.getByRole("table", {
        name: "Tournament History E2E Two"})).toBeVisible();
});

test("the statistics are asked for", async ({page}) => {
    await page.goto(serverUrl("/web/stats"));

    const answered = page.waitForResponse(
        (response) => response.url().endsWith(
            "/api/v1/scoreboards/1/stats:refresh")
            && response.request().method() === "POST");
    await page.getByRole("button", {name: "Fetch statistics"}).click();

    expect((await answered).status()).toBe(200);
    await expect(page.getByText(/^Fetching the statistics\./)).toBeVisible();
    await expect(page.getByRole("button", {name: "Fetch statistics"}))
        .toBeEnabled();
});

test("the match list says a tournament is needed first", async ({page}) => {
    await page.goto(serverUrl("/web/stats"));
    await tab(page, "Matches in This Tournament").click();
    const panel = page.getByRole("tabpanel");
    const status = panel.getByRole("status");
    await expect(status).toHaveText("Press “Fetch from start.gg”.");
    // The Qt panel's columns, sortable.
    const table = panel.getByRole("table",
                                  {name: "Matches in This Tournament"});
    for (const column of ["Round", "P1 Seed", "P1 Name", "Score", "P2 Name",
                          "P2 Seed", "UpSet", "LR", "SeedOver"]) {
        await expect(table.getByRole("columnheader", {name: column,
                                                      exact: true}))
            .toBeVisible();
    }

    await panel.getByRole("button", {name: "Fetch from start.gg"}).click();

    await expect(status).toHaveText("No tournament selected");
    await expect(panel.getByRole("button", {name: "Fetch from start.gg"}))
        .toBeEnabled();
});

test("the desktop's view comes back to the tab last chosen", async ({page}) => {
    await page.goto(serverUrl("/web/stats?desktop=1&lng=en"));
    await tab(page, "Tournament History").click();
    await expect(tab(page, "Tournament History"))
        .toHaveAttribute("aria-selected", "true");

    // Another panel and back, as src/AutonaWebPanelHost.py moves the view
    // (no reload): the same tab.
    await page.evaluate(() => (window as any).autonaPanelShell.navigate("top8"));
    await expect(page).toHaveTitle("Autona Top 8 Display Settings");
    await page.evaluate(() => (window as any).autonaPanelShell.navigate("stats"));
    await expect(tab(page, "Tournament History"))
        .toHaveAttribute("aria-selected", "true");

    // A reload is a new page: the first tab again.
    await page.reload();
    await expect(tab(page, "Head-to-Head"))
        .toHaveAttribute("aria-selected", "true");
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/stats?desktop=1&lng=ja"));

    await expect(page).toHaveTitle("Autona 統計情報");
    for (const label of ["直近の対戦", "戦績", "大会履歴",
                         "この大会の試合一覧"]) {
        await expect(tab(page, label)).toBeVisible();
    }
    await expect(page.getByRole("button", {name: "統計情報を取得する"}))
        .toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
});
