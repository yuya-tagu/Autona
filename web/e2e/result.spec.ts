import {APIRequestContext, expect, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The tournament result's page in React (ADR-005 S4r): /web/result on the
// headless app keeps its rows through /api/v1/result (core's ResultService).
// The headless shell has no off-screen view to draw with: the page says so and
// asks for no preview. Checked through text, the DOM and the API, never
// screenshots.

let pageErrors: string[] = [];

async function described(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/result"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data;
}

async function setPlayer(request: APIRequestContext, slot: number,
                         fields: Record<string, unknown>) {
    const response = await request.patch(
        serverUrl(`/api/v1/result/players/${slot}`), {data: fields});
    expect(response.ok()).toBe(true);
}

// The tests share one app: every one starts from nothing entered, on the
// layout the app starts with.
async function reset(request: APIRequestContext) {
    for (const kind of ["players", "teams"]) {
        const response = await request.post(serverUrl("/api/v1/result:clear"),
                                            {data: {kind, confirm: true}});
        expect(response.ok()).toBe(true);
    }
    const response = await request.patch(serverUrl("/api/v1/result"),
                                         {data: {layout: "horizontal"}});
    expect(response.ok()).toBe(true);
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/result: ${error}`));
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
        if (request.url().includes("/api/v1/result:preview")) {
            previews.push(request.url());
        }
    });
    await page.goto(serverUrl("/web/result"));

    await expect(page).toHaveTitle("Autona Tournament Result");
    for (const name of ["Layout", "Standings (8 players)", "Result colors",
                        "Preview", "Save players to the database"]) {
        await expect(page.getByRole("region", {name, exact: true})).toBeVisible();
    }
    const preview = page.getByRole("region", {name: "Preview", exact: true});
    await expect(preview.getByText(/runs without a window \(headless\)/))
        .toBeVisible();
    await expect(preview.getByRole("button", {name: "Make"})).toBeDisabled();
    await expect(preview.getByRole("button", {name: "Post on X"})).toBeDisabled();
    await expect(preview.getByText("The background follows the thumbnail's settings."))
        .toBeVisible();
    // Past the preview's wait: nothing was asked of a shell that cannot draw.
    await page.waitForTimeout(1000);
    expect(previews).toEqual([]);
});

test("a row typed on the page is saved, and there after a reload", async ({page, request}) => {
    await page.goto(serverUrl("/web/result"));
    const row = page.getByRole("region", {name: "Row 2", exact: true});

    await row.getByRole("textbox", {name: "Player name"}).fill("Bravo");
    await row.getByRole("textbox", {name: "Sponsor"}).fill("SP");
    await row.getByRole("textbox", {name: "X handle"}).fill("bravo_x");
    await row.getByRole("textbox", {name: "X handle"}).blur();

    await expect.poll(async () => {
        const {players} = await described(request);
        return [players[1].name, players[1].sponsor, players[1].twitter];
    }).toEqual(["Bravo", "SP", "bravo_x"]);

    await page.reload();
    await expect(page.getByRole("region", {name: "Row 2", exact: true})
        .getByRole("textbox", {name: "Player name"})).toHaveValue("Bravo");
});

test("a rank goes when it is confirmed", async ({page, request}) => {
    await page.goto(serverUrl("/web/result"));
    const rank = page.getByRole("region", {name: "Row 3", exact: true})
        .getByLabel("Rank");

    await rank.fill("9");
    await rank.press("Enter");

    await expect.poll(async () => (await described(request)).players[2].rank)
        .toBe(9);
});

test("the layout chosen is saved and says the rows, and one changed elsewhere is followed", async ({page, request}) => {
    await page.goto(serverUrl("/web/result"));
    const layout = page.getByRole("combobox", {name: "Result layout"});
    await expect(layout).toHaveText("Horizontal (1920x1080)");

    await layout.click();
    await page.getByRole("option", {name: "Horizontal TOP16 (1920x1080)"}).click();

    await expect.poll(async () => (await described(request)).layout)
        .toBe("horizontal_16");
    await expect(page.getByRole("region", {name: "Standings (16 players)", exact: true}))
        .toBeVisible();
    await expect(page.getByRole("region", {name: "Row 16", exact: true}))
        .toBeVisible();

    // As the sidebar's battle format does: none of this page's doing. The state
    // tells it (result_layout).
    const response = await request.patch(serverUrl("/api/v1/result"),
                                         {data: {layout: "team"}});
    expect(response.ok()).toBe(true);

    await expect(layout).toHaveText("Team / Crew (1920x1080)");
    await expect(page.getByRole("region", {name: "Standings (8 teams)", exact: true}))
        .toBeVisible();
    const team = page.getByRole("region", {name: "Team 1", exact: true});
    await team.getByRole("textbox", {name: "Team name"}).fill("Reds");
    await team.getByRole("group", {name: "Member 1"})
        .getByRole("textbox", {name: "Player name"}).fill("A1");
    await team.getByRole("group", {name: "Member 1"})
        .getByRole("textbox", {name: "X handle"}).fill("a1_x");
    await team.getByRole("group", {name: "Member 1"})
        .getByRole("textbox", {name: "X handle"}).blur();

    await expect.poll(async () => {
        const {teams} = await described(request);
        return [teams[0].name, teams[0].members[0]?.name,
                teams[0].members[0]?.twitter];
    }).toEqual(["Reds", "A1", "a1_x"]);
});

test("a tall layout's preview keeps its shape", async ({page, request}) => {
    const response = await request.patch(serverUrl("/api/v1/result"),
                                         {data: {layout: "vertical"}});
    expect(response.ok()).toBe(true);
    await page.goto(serverUrl("/web/result"));

    const box = page.getByRole("region", {name: "Preview", exact: true})
        .getByText("No preview yet").locator("..");
    await expect(box).toBeVisible();
    expect(await box.evaluate((element) =>
        getComputedStyle(element).aspectRatio)).toBe("960 / 1080");
    // Within the pane, however tall the picture.
    const {height} = (await box.boundingBox())!;
    expect(height).toBeLessThanOrEqual(page.viewportSize()!.height);
});

test("the keyboard moves a row, and the focus goes along with it", async ({page, request}) => {
    await setPlayer(request, 1, {name: "Alpha"});
    await setPlayer(request, 2, {name: "Bravo"});
    await setPlayer(request, 3, {name: "Charlie"});
    await page.goto(serverUrl("/web/result"));
    const first = page.getByRole("button", {name: "Move Row 1 down"});
    await expect(page.getByRole("button", {name: "Move Row 1 up"})).toBeDisabled();

    await first.focus();
    await first.press("Enter");

    await expect.poll(async () => (await described(request)).players
        .slice(0, 3).map((row: {name: string}) => row.name))
        .toEqual(["Bravo", "Alpha", "Charlie"]);
    await expect(page.getByRole("button", {name: "Move Row 2 down"})).toBeFocused();
    await expect(page.getByRole("region", {name: "Row 1", exact: true})
        .getByRole("textbox", {name: "Player name"})).toHaveValue("Bravo");

    await page.keyboard.press("Enter");

    await expect.poll(async () => (await described(request)).players
        .slice(0, 3).map((row: {name: string}) => row.name))
        .toEqual(["Bravo", "Charlie", "Alpha"]);
});

test("dragging a row by its grip moves it", async ({page, request}) => {
    await setPlayer(request, 1, {name: "Alpha"});
    await setPlayer(request, 2, {name: "Bravo"});
    await setPlayer(request, 3, {name: "Charlie"});
    // Tall enough for both rows, so that nothing scrolls under the drag.
    await page.setViewportSize({width: 1280, height: 1800});
    await page.goto(serverUrl("/web/result"));
    await expect(page.getByRole("region", {name: "Row 3", exact: true})
        .getByRole("textbox", {name: "Player name"})).toHaveValue("Charlie");
    const from = page.getByRole("region", {name: "Row 3", exact: true})
        .getByTitle("Drag to reorder");
    const onto = page.getByRole("region", {name: "Row 1", exact: true});

    await from.dragTo(onto, {targetPosition: {x: 40, y: 4}});

    await expect.poll(async () => (await described(request)).players
        .slice(0, 3).map((row: {name: string}) => row.name))
        .toEqual(["Charlie", "Alpha", "Bravo"]);
});

test("clearing asks first, and only the kind on the page goes", async ({page, request}) => {
    await setPlayer(request, 1, {name: "Alpha"});
    const team = await request.patch(serverUrl("/api/v1/result/teams/1"),
                                     {data: {name: "Reds"}});
    expect(team.ok()).toBe(true);
    await page.goto(serverUrl("/web/result"));
    const card = page.getByRole("region", {name: "Layout", exact: true});

    await card.getByRole("button", {name: "Clear all"}).click();
    let dialog = page.getByRole("dialog");
    await expect(dialog.getByText(/singles layouts/)).toBeVisible();
    await dialog.getByRole("button", {name: "Cancel"}).click();
    await expect(dialog).toBeHidden();
    expect((await described(request)).players[0].name).toBe("Alpha");

    await card.getByRole("button", {name: "Clear all"}).click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", {name: "Clear", exact: true}).click();

    await expect.poll(async () => (await described(request)).players[0].name)
        .toBe("");
    expect((await described(request)).teams[0].name).toBe("Reds");
    await expect(page.getByRole("region", {name: "Row 1", exact: true})
        .getByRole("textbox", {name: "Player name"})).toHaveValue("");
});

// The text fields draw the state, which reaches an open page as rows over
// Socket.IO (web/src/stateDelta.js): a list that gets shorter has to get
// shorter there too, or what went comes back in its field.
test("a member who goes is gone from the open page, by its own write or another's", async ({page, request}) => {
    const layout = await request.patch(serverUrl("/api/v1/result"),
                                       {data: {layout: "team"}});
    expect(layout.ok()).toBe(true);
    const saved = await request.patch(serverUrl("/api/v1/result/teams/1"), {
        data: {name: "Reds", members: [{name: "A1"}, {name: "A2"}]},
    });
    expect(saved.ok()).toBe(true);
    await page.goto(serverUrl("/web/result"));
    const team = page.getByRole("region", {name: "Team 1", exact: true});
    const nameOf = (place: number) => team
        .getByRole("group", {name: `Member ${place}`})
        .getByRole("textbox", {name: "Player name"});
    await expect(nameOf(2)).toHaveValue("A2");

    await nameOf(2).fill("");
    await nameOf(2).blur();

    await expect.poll(async () => (await described(request)).teams[0].members.length)
        .toBe(1);
    // Past the time a field keeps what was typed over what the state says.
    await page.waitForTimeout(1500);
    await expect(nameOf(2)).toHaveValue("");
    await expect(nameOf(1)).toHaveValue("A1");

    const cleared = await request.post(serverUrl("/api/v1/result:clear"),
                                       {data: {kind: "teams", confirm: true}});
    expect(cleared.ok()).toBe(true);

    await expect(team.getByRole("textbox", {name: "Team name"})).toHaveValue("");
    await expect(nameOf(1)).toHaveValue("");
    await expect(nameOf(2)).toHaveValue("");
});

test("without a tournament the import says where to load one", async ({page}) => {
    await page.goto(serverUrl("/web/result"));
    const card = page.getByRole("region", {name: "Layout", exact: true});

    await card.getByRole("button", {name: "Load tournament results"}).click();

    await expect(card.getByRole("status"))
        .toHaveText(/No tournament is set/);
});
