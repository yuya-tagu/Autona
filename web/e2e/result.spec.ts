import fs from "node:fs";
import path from "node:path";
import {APIRequestContext, expect, test} from "@playwright/test";
import {appDir, keepLocal, previewOrigin, serverUrl} from "./server";

// The tournament result's page in React (ADR-005 S4r): /web/result on the
// headless app keeps its rows through /api/v1/result (core's ResultService).
// The headless shell has no off-screen view to draw with: the page says so and
// asks for no preview image. Checked through text, the DOM and the API, never
// screenshots.
//
// A layout on template contract 2 (ADR-008 D7 R4; the shipped ones are)
// needs no renderer to preview: the page frames it from the preview origin
// and posts it GET /api/v1/result/payload, and its errors are shown in the
// layout check -- in a real browser, under the template folder's CSP.

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

test("the page opens with its cards and says the headless app cannot make the image", async ({page}) => {
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
    // The shipped layout is previewed live; the image is the desktop's.
    await expect(preview.getByText(/This preview is drawn by the browser/))
        .toBeVisible();
    await expect(preview.getByTitle("Preview", {exact: true})).toBeVisible();
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

    // Previewed live (contract 2): the frame's box is the layout's shape.
    const box = page.getByRole("region", {name: "Preview", exact: true})
        .getByTitle("Preview", {exact: true}).locator("..");
    await expect(box).toBeVisible();
    expect(await box.evaluate((element) =>
        getComputedStyle(element).aspectRatio)).toBe("960 / 1080");
    // Within the pane, however tall the picture.
    const {height, width} = (await box.boundingBox())!;
    expect(height).toBeLessThanOrEqual(page.viewportSize()!.height);
    // Only the frame is narrowed: the layout check keeps the pane's width.
    const check = page.getByRole("status", {name: "Layout check"});
    await expect(check).toBeVisible();
    expect((await check.boundingBox())!.width).toBeGreaterThan(width + 1);
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

// ── the live preview of a layout on contract 2 (ADR-008 D7 R4) ───────────

// A layout made from the layout kit as "make a new design" makes it: the
// kit's files as they are, with the manifest core writes
// (core/LayoutKit._Manifest). Copied here: the page's button is the shared
// view's alone (SHELL).
const KIT_MADE = "result_e2e_kit";
const KIT_FILES = ["result.html", "result.css", "result.js",
                   "sample_payload.json", "sample_payload_teams.json"];
// A result_ folder the catalog cannot list: told on this page.
const UNREADABLE = "result_e2e_unreadable";

test.describe("the live preview", () => {
    test.beforeAll(() => {
        const kit = path.join(appDir(), "layout", "include", "kit", "result");
        const made = path.join(appDir(), "layout", KIT_MADE);
        fs.mkdirSync(made, {recursive: true});
        for (const file of KIT_FILES) {
            fs.copyFileSync(path.join(kit, file), path.join(made, file));
        }
        fs.writeFileSync(path.join(made, "settings.json"), JSON.stringify({
            autona: {kind: "result", contract: 2, entry: "result.html",
                     size: [1920, 1080], rows: "players",
                     displayName: "E2E kit", author: "unknown",
                     license: "All rights reserved"}}), "utf8");
        const unreadable = path.join(appDir(), "layout", UNREADABLE);
        fs.mkdirSync(unreadable, {recursive: true});
        fs.writeFileSync(path.join(unreadable, "settings.json"), "{not json",
                         "utf8");
        fs.writeFileSync(path.join(unreadable, "result.html"), "", "utf8");
    });

    test.afterAll(async ({request}) => {
        // No layout of these folders stays chosen once they are gone.
        await reset(request);
        expect((await described(request)).layout).toBe("horizontal");
        for (const name of [KIT_MADE, UNREADABLE]) {
            fs.rmSync(path.join(appDir(), "layout", name),
                      {recursive: true, force: true});
        }
    });

    test("the shipped layout is drawn in the page's frame from the preview origin", async ({page}) => {
        const payloads: string[] = [];
        page.on("request", (request) => {
            if (request.url().includes("/api/v1/result/payload")) {
                payloads.push(request.url());
            }
        });
        const origin = await previewOrigin(page.request);
        await page.goto(serverUrl("/web/result"));

        // The page names its own origin: autona.js takes renders from it only.
        const frame = page.getByTitle("Preview", {exact: true});
        await expect(frame).toHaveAttribute(
            "src", `${origin}/layout/result/result.html?autonaParent=`
                + encodeURIComponent(new URL(serverUrl("/")).origin));
        // Drawn: the default banner, read from ../../assets under the CSP,
        // and the sample's tournament.
        const drawn = page.frameLocator("iframe[title='Preview']");
        const banner = drawn.locator(".background img");
        await expect(banner).toHaveCount(1);
        await expect.poll(() => banner.evaluate(
            (image: HTMLImageElement) => image.complete && image.naturalWidth))
            .toBeGreaterThan(0);
        await expect(drawn.locator("#tournamentName")).not.toHaveText("");
        expect(payloads.length).toBeGreaterThan(0);
        // Said drawn (autona:rendered), and with no errors.
        await expect(page.getByRole("status", {name: "Layout check"}))
            .toContainText("no errors");
        await expect(page.getByTestId("layout-check-error")).toHaveCount(0);
    });

    test("a teams' layout draws the teams' rows", async ({page, request}) => {
        const response = await request.patch(serverUrl("/api/v1/result"),
                                             {data: {layout: "team"}});
        expect(response.ok()).toBe(true);
        await page.goto(serverUrl("/web/result"));

        await expect(page.getByTitle("Preview", {exact: true}))
            .toHaveAttribute("src", /\/layout\/result_team\/result\.html\?/);
        await expect(page.getByRole("status", {name: "Layout check"}))
            .toContainText("no errors");
        await expect(page.getByTestId("layout-check-error")).toHaveCount(0);
    });

    test("a layout with its own result.js (vertical_geki) is drawn with no errors", async ({page, request}) => {
        const response = await request.patch(serverUrl("/api/v1/result"),
                                             {data: {layout: "vertical_geki"}});
        expect(response.ok()).toBe(true);
        await setPlayer(request, 1, {name: "Alpha"});
        await page.goto(serverUrl("/web/result"));

        await expect(page.getByTitle("Preview", {exact: true})).toHaveAttribute(
            "src", /\/layout\/result_vertical_geki\/result\.html\?/);
        // Its own script registered its drawing, under the folder's CSP.
        const drawn = page.frameLocator("iframe[title='Preview']");
        await expect(drawn.locator(".player-slot.slot-1 .player-name"))
            .toHaveText("Alpha");
        await expect(page.getByRole("status", {name: "Layout check"}))
            .toContainText("no errors");
        await expect(page.getByTestId("layout-check-error")).toHaveCount(0);
    });

    test("a layout made from the kit is drawn with no errors", async ({page, request}) => {
        const response = await request.patch(serverUrl("/api/v1/result"),
                                             {data: {layout: KIT_MADE}});
        expect(response.ok(), await response.text()).toBe(true);
        await page.goto(serverUrl("/web/result"));

        await expect(page.getByRole("combobox", {name: "Result layout"}))
            .toHaveText("E2E kit");
        await expect(page.getByRole("region", {name: "Standings (16 players)", exact: true}))
            .toBeVisible();
        await expect(page.getByTitle("Preview", {exact: true}))
            .toHaveAttribute("src", new RegExp(
                `/layout/${KIT_MADE}/result\\.html\\?autonaParent=[^&]+$`));
        // Drawn: the default banner under the CSP, and the sample's rows.
        const frame = page.frameLocator("iframe[title='Preview']");
        await expect.poll(() => frame.locator("#background").evaluate(
            (image: HTMLImageElement) => image.complete && image.naturalWidth))
            .toBeGreaterThan(0);
        await expect(frame.locator("#first")).not.toBeEmpty();
        await expect(page.getByRole("status", {name: "Layout check"}))
            .toContainText("no errors");
        await expect(page.getByTestId("layout-check-error")).toHaveCount(0);

        // A row typed is drawn by the next payload.
        await setPlayer(request, 1, {name: "Typed Alpha"});
        await page.reload();
        await expect(frame.locator("#first")).toContainText("Typed Alpha");
    });

    test("a browser gets no kit, and is told the folder it cannot list", async ({page, request}) => {
        await page.goto(serverUrl("/web/result"));
        const card = page.getByRole("region", {name: "Layout", exact: true});

        const unlisted = card.getByRole("region",
                                        {name: "Designs that could not be read"});
        await expect(unlisted).toContainText(`layout/${UNREADABLE}/`);
        // The kit's buttons are the desktop's shared view's (SHELL).
        await expect(card.getByRole("combobox", {name: "Result layout"}))
            .toBeVisible();
        await expect(card.getByRole("button", {name: "Make a new design"}))
            .toHaveCount(0);
        await expect(card.getByRole("button", {name: "Copy for an AI"}))
            .toHaveCount(0);
        const made = await request.post(serverUrl("/api/v1/layouts:create"),
                                        {data: {kind: "result", name: "x"}});
        expect(made.status()).toBe(403);
        expect(fs.existsSync(path.join(appDir(), "layout", "result_x")))
            .toBe(false);
    });
});
