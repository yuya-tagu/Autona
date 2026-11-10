import {APIRequestContext, expect, Locator, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The SCORE page in React (ADR-005 S4aa): /web/scoreboard on the headless app
// shows the board of the battle format in force, writes a card's field through
// PATCH .../fields (the headless boards are not held: published at once, so
// the overlay follows), and follows a format switched elsewhere (the
// `battle_format` notice). Checked through text, the DOM and the API. The set
// selection (S4ab) needs a tournament, which the e2e app cannot load: only its
// absence is checked here, and that the desktop's request for it (S4ab-2)
// reaches the built page (the rest is ScorePage.test.js's).

let pageErrors: string[] = [];

function collectErrors(page: Page, name: string) {
    page.on("pageerror", (error) => pageErrors.push(`${name}: ${error}`));
}

async function setFormat(request: APIRequestContext, format: string) {
    const response = await request.put(serverUrl("/api/v1/battle-format"),
                                       {data: {format}});
    expect(response.ok()).toBe(true);
}

async function tagOf(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.score?.["1"]?.team?.["1"]
        ?.player?.["1"]?.name;
}

async function clearTag(request: APIRequestContext) {
    const response = await request.patch(
        serverUrl("/api/v1/scoreboards/1/teams/1/players/1/fields"),
        {data: {gamerTag: ""}});
    expect(response.ok()).toBe(true);
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    collectErrors(page, "/web/scoreboard");
    await keepLocal(page);
    await setFormat(request, "singles");
    await clearTag(request);
});

test.afterEach(async ({request}) => {
    await setFormat(request, "singles");
    await clearTag(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("a tag typed on the page reaches the state and the overlay", async ({page, context, request}) => {
    const overlay = await context.newPage();
    collectErrors(overlay, "overlay");
    await keepLocal(overlay);
    await overlay.goto(serverUrl("/layout/scoreboard_simple/index.html"));
    await overlay.waitForFunction(
        () => (window as any).tshSocket?.connected === true);

    await page.goto(serverUrl("/web/scoreboard"));
    await expect(page).toHaveTitle("Autona Scoreboard");
    const tag = page.getByRole("combobox", {name: "1P player name"});
    await expect(tag).toBeVisible();
    // The headless board is not held: there is no 反映.
    await expect(page.getByRole("button", {name: "Apply", exact: true}))
        .toHaveCount(0);

    await tag.fill("E2E Player");
    await tag.blur();

    await expect.poll(() => tagOf(request)).toBe("E2E Player");
    await expect(overlay.locator(".p1.container .name")).toContainText("E2E Player");
});

test("a format switched elsewhere switches the board shown", async ({page, request}) => {
    await page.goto(serverUrl("/web/scoreboard"));
    await expect(page.getByRole("combobox", {name: "1P player name"})).toBeVisible();
    await expect(page.getByRole("textbox", {name: "Players per team"}))
        .toHaveCount(0);

    await setFormat(request, "team");
    await expect(page.getByRole("textbox", {name: "Players per team"}))
        .toBeVisible();
    await expect(page.getByRole("heading", {name: "Team 1", exact: true}))
        .toBeVisible();

    await setFormat(request, "crew");
    await expect(page.getByRole("textbox", {name: "Members", exact: true}))
        .toBeVisible();
    await expect(page.getByRole("button", {name: "Reset stocks"})).toBeVisible();
});

// ── the Qt SCORE's arrangement, at the widths the desktop's view takes ──

type Box = {x: number; y: number; width: number; height: number};

async function boxOf(locator: Locator): Promise<Box> {
    await expect(locator).toBeVisible();
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    return box as Box;
}

const region = (page: Page, name: string) =>
    page.getByRole("region", {name, exact: true});

async function noSideScroll(page: Page) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth
        - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
}

for (const width of [600, 657, 1024, 1400]) {
    test(`at ${width}px the teams side by side, the score box under them`, async ({page}) => {
        await page.setViewportSize({width, height: 1000});
        await page.goto(serverUrl("/web/scoreboard"));

        const team1 = await boxOf(region(page, "Team 1"));
        const team2 = await boxOf(region(page, "Team 2"));
        const score = await boxOf(region(page, "Score"));
        // Side by side, team 1 on the left.
        expect(Math.abs(team1.y - team2.y)).toBeLessThan(2);
        expect(team2.x).toBeGreaterThanOrEqual(team1.x + team1.width - 1);
        // The score box under both, across both.
        expect(score.y).toBeGreaterThanOrEqual(
            Math.max(team1.y + team1.height, team2.y + team2.height) - 1);
        expect(score.x).toBeLessThanOrEqual(team1.x + 1);
        expect(score.x + score.width).toBeGreaterThanOrEqual(
            team2.x + team2.width - 1);
        // Its +1 / -1 and the typed score whole and usable.
        for (const name of ["Team 1 score +1", "Team 1 score -1",
                            "Team 2 score +1", "Team 2 score -1"]) {
            const button = await boxOf(page.getByRole("button", {name, exact: true}));
            expect(button.width).toBeGreaterThanOrEqual(40);
            expect(button.x + button.width).toBeLessThanOrEqual(width);
        }
        for (const name of ["Team 1 score", "Team 2 score"]) {
            const field = await boxOf(page.getByRole("textbox", {name, exact: true}));
            expect(field.width).toBeGreaterThanOrEqual(60);
        }
        // Then the reset, at the bottom.
        const reset = await boxOf(page.getByRole("button", {name: "Reset all inputs"}));
        expect(reset.y).toBeGreaterThanOrEqual(score.y + score.height - 1);
        await noSideScroll(page);
    });
}

test("narrower than 600px the teams are stacked", async ({page}) => {
    await page.setViewportSize({width: 400, height: 1000});
    await page.goto(serverUrl("/web/scoreboard"));

    const team1 = await boxOf(region(page, "Team 1"));
    const team2 = await boxOf(region(page, "Team 2"));
    const score = await boxOf(region(page, "Score"));
    expect(team2.y).toBeGreaterThanOrEqual(team1.y + team1.height - 1);
    expect(score.y).toBeGreaterThanOrEqual(team2.y + team2.height - 1);
    await noSideScroll(page);
});

async function playersOnBoard2(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/scoreboards/2"));
    expect(response.ok()).toBe(true);
    return Object.keys((await response.json()).data?.team?.["1"]?.player ?? {})
        .length;
}

async function setPlayers(request: APIRequestContext, players: number) {
    const response = await request.patch(serverUrl("/api/v1/scoreboards/2/set"),
                                         {data: {players}});
    expect(response.ok()).toBe(true);
}

test("a team of three keeps two columns, its cards stacked in its own", async ({page, request}) => {
    const before = await playersOnBoard2(request);
    await setFormat(request, "team");
    await setPlayers(request, 3);
    try {
        await page.setViewportSize({width: 657, height: 1400});
        await page.goto(serverUrl("/web/scoreboard"));

        const team1 = await boxOf(region(page, "Team 1"));
        const team2 = await boxOf(region(page, "Team 2"));
        expect(Math.abs(team1.y - team2.y)).toBeLessThan(2);
        expect(team2.x).toBeGreaterThanOrEqual(team1.x + team1.width - 1);
        for (const [team, column] of [[1, team1], [2, team2]] as const) {
            let above = -1;
            for (const slot of [1, 2, 3]) {
                const card = await boxOf(region(page, `Team ${team} player ${slot}`));
                expect(card.x).toBeGreaterThanOrEqual(column.x - 1);
                expect(card.x + card.width).toBeLessThanOrEqual(
                    column.x + column.width + 1);
                expect(card.y).toBeGreaterThan(above);
                above = card.y + card.height - 1;
            }
        }
        const score = await boxOf(region(page, "Score"));
        expect(score.y).toBeGreaterThanOrEqual(
            Math.max(team1.y + team1.height, team2.y + team2.height) - 1);
        await noSideScroll(page);
    } finally {
        await setPlayers(request, before);
    }
});

// ── the set selection (S4ab) ──
// The e2e app has no tournament (no start.gg to load one from): the page
// offers no set selection, and the board follows nothing.

test("with no tournament there is no set selection and no timer row", async ({page, request}) => {
    const follow = await request.get(serverUrl("/api/v1/scoreboards/1/follow"));
    expect(follow.ok()).toBe(true);
    expect((await follow.json()).data?.poll).toBeNull();

    await page.goto(serverUrl("/web/scoreboard"));
    await expect(page.getByRole("combobox", {name: "1P player name"})).toBeVisible();
    await expect(page.getByRole("button", {name: "Choose a match of the tournament"}))
        .toHaveCount(0);
    await expect(page.getByTestId("score-follow")).toHaveCount(0);
});

test("the desktop's view asking for the set selection shows SCORE, no dialog without a tournament",
     async ({page}) => {
    // What src/AutonaWebPanelHost.py runs for Ctrl+Shift+O and the legacy
    // open-set (S4ab-2) when the view was on another panel: navigate, then
    // the request, answered "taken" by the built bundle's panel shell.
    await page.goto(serverUrl("/web/shell-check?desktop=1&lng=en"));
    await expect(page.getByRole("heading", {name: "Panel shell check"})).toBeVisible();
    const settled = page.waitForEvent("console", (message) =>
        message.text().includes("set selection of board 1 was asked for"));

    const answer = await page.evaluate(() => {
        const shell = (window as any).autonaPanelShell;
        shell.navigate("scoreboard");
        if (shell && shell.openScoreSets) {
            shell.openScoreSets(1);
            return "taken";
        }
        return "";
    });

    expect(answer).toBe("taken");
    await expect(page.getByRole("combobox", {name: "1P player name"})).toBeVisible();
    // The page settled it -- no tournament, so nothing to open.
    expect((await settled).text()).toContain("no tournament");
    await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/scoreboard?desktop=1&lng=ja"));

    await expect(page.getByRole("combobox", {name: "1Pプレイヤー名"})).toBeVisible();
    await expect(page.getByRole("heading", {name: "スコアボード", exact: true}))
        .toBeVisible();
    await expect(page.getByRole("button", {name: "ブラウザ版を開く"})).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
    await expect(page).toHaveTitle("Autona スコアボード入力");
});
