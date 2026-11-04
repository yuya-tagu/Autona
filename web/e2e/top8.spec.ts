import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Top 8 panel in React (ADR-005 S4i): /web/top8 on the headless app
// writes through /api/v1/top8 (a player's fields, the caption, the staged
// scores and their reset), and the OBS overlay (/layout/bracket_top8) draws
// top8.*. The headless app loads no game, so no character is picked here
// (the page's character pick is the jest test's, against core's table).
// Nothing reaches start.gg: no tournament is loaded. Checked through text,
// the DOM and the API, never screenshots.

let pageErrors: string[] = [];

function collectErrors(page: Page, name: string) {
    page.on("pageerror", (error) => pageErrors.push(`${name}: ${error}`));
}

async function top8(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.top8 ?? {};
}

// The tests share one app: eight empty players, the default caption, no
// scores.
async function resetTop8(request: APIRequestContext) {
    for (const slot of ["1", "2", "3", "4", "5", "6", "7", "8"]) {
        const response = await request.patch(
            serverUrl(`/api/v1/top8/players/${slot}`), {data: {
                name: "", sponsor: "", country: null, character: null,
            }});
        expect(response.ok()).toBe(true);
    }
    expect((await request.patch(serverUrl("/api/v1/top8"),
                                {data: {phaseLabel: ""}})).ok()).toBe(true);
    expect((await request.delete(serverUrl("/api/v1/top8/scores"))).ok())
        .toBe(true);
}

function slot(page: Page, number: string) {
    return page.getByRole("region", {name: `Slot ${number}`, exact: true});
}

function score(page: Page, match: string, side: number) {
    return page.getByRole("textbox", {name: `${match} player ${side} score`});
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    collectErrors(page, "/web/top8");
    await keepLocal(page);
    await resetTop8(request);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Top 8 page opens with its eight players and the design", async ({page}) => {
    await page.goto(serverUrl("/web/top8"));

    await expect(page).toHaveTitle("Autona Top 8 Display Settings");
    await expect(slot(page, "1")).toBeVisible();
    await expect(page.getByRole("region", {name: /^Slot \d$/})).toHaveCount(8);
    for (const name of ["Match scores", "Top 8 colors", "Adding it to OBS"]) {
        await expect(page.getByRole("region", {name, exact: true})).toBeVisible();
    }
    // The built-in design is selected, previewed and given to OBS.
    await expect(page.getByRole("textbox", {name: "URL for OBS"}))
        .toHaveValue(/\/layout\/bracket_top8\/index\.html$/);
    await expect(page.getByTitle("Preview"))
        .toHaveAttribute("src", /\/layout\/bracket_top8\/index\.html$/);
});

test("a player typed in and the caption appear on the overlay", async ({page, context, request}) => {
    const overlay = await context.newPage();
    collectErrors(overlay, "overlay");
    await keepLocal(overlay);
    await overlay.goto(serverUrl("/layout/bracket_top8/index.html"));

    await page.goto(serverUrl("/web/top8"));
    const card = slot(page, "1");
    await card.getByRole("textbox", {name: "Player name"}).fill("E2E Player");
    await card.getByRole("textbox", {name: "Sponsor"}).fill("E2E");
    const country = card.getByRole("combobox", {name: "Country"});
    await country.fill("Japan");
    await page.getByRole("option", {name: /Japan \(JP\)$/}).click();
    await page.getByRole("textbox", {name: "Display label"}).fill("GRAND FINALS");

    await expect.poll(async () => {
        const state = await top8(request);
        const player = state.players?.["1"] ?? {};
        return [player.name, player.sponsor, player.country?.code,
                state.phaseLabel];
    }).toEqual(["E2E Player", "E2E", "JP", "GRAND FINALS"]);
    // The score table names the player.
    await expect(page.getByRole("group", {name: "WSF1"}).getByText("E2E Player"))
        .toBeVisible();
    // And the overlay draws them.
    const first = overlay.locator(
        '.match-card[data-match="wsf1"] .slot[data-slot="0"] .name');
    await expect(first).toContainText("E2E Player");
    await expect(first).toContainText("E2E");
    await expect(overlay.locator(".stage-title")).toHaveText("GRAND FINALS");
});

test("scores are staged until 反映, and reset once confirmed", async ({page, request}) => {
    await page.goto(serverUrl("/web/top8"));
    const apply = page.getByRole("button", {name: "Apply", exact: true});
    await expect(apply).toBeDisabled();
    // The reset game takes scores only once it is on.
    await expect(score(page, "RESET", 1)).toBeDisabled();

    await score(page, "WSF1", 1).fill("2");
    await score(page, "WSF1", 2).fill("1");
    // Staged: the state still has none.
    expect((await top8(request)).matches?.wsf1?.score).toEqual([0, 0]);

    await apply.click();
    await expect.poll(async () => {
        const matches = (await top8(request)).matches ?? {};
        return [matches.wsf1?.score, matches.wsf1?.finished,
                matches.wf?.slots];
    }).toEqual([[2, 1], true, [1, 0]]);
    await expect(apply).toBeDisabled();
    await expect(score(page, "WSF1", 1)).toHaveValue("2");

    await page.getByRole("button", {name: "Reset all"}).click();
    const confirm = page.getByRole("dialog");
    await confirm.getByRole("button", {name: "Reset", exact: true}).click();

    await expect.poll(async () => (await top8(request)).matches?.wsf1?.score)
        .toEqual([0, 0]);
    await expect(score(page, "WSF1", 1)).toHaveValue("0");
});

test("the import says a tournament is needed first", async ({page}) => {
    await page.goto(serverUrl("/web/top8"));

    await page.getByRole("button", {name: "Load tournament results"}).click();

    await expect(page.getByText("Load a tournament first.")).toBeVisible();
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/top8?desktop=1&lng=ja"));

    const first = page.getByRole("region", {name: "スロット 1", exact: true});
    await expect(first).toBeVisible();
    await expect(first.getByRole("textbox", {name: "プレイヤー名"})).toBeVisible();
    await expect(page.getByRole("button", {name: "反映"})).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
});
