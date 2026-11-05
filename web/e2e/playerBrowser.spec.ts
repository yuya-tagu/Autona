import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Player List in React (ADR-005 S4n): /web/playerBrowser on the headless
// app adds, edits and deletes the player database through /api/v1/players,
// searches it (the real name included, which it does not show) and loads an
// entrant onto the board through :load. Checked through text, the DOM and the
// API, never screenshots.

let pageErrors: string[] = [];

type Row = Record<string, unknown> & {key: string};

async function players(request: APIRequestContext): Promise<Row[]> {
    const response = await request.get(serverUrl("/api/v1/players"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.players;
}

async function save(request: APIRequestContext, body: Record<string, unknown>) {
    const response = await request.put(serverUrl("/api/v1/players"), {data: body});
    expect(response.ok()).toBe(true);
}

async function remove(request: APIRequestContext, key: string) {
    const response = await request.post(
        serverUrl("/api/v1/players:delete"), {data: {key, confirm: true}});
    expect(response.ok()).toBe(true);
}

async function boardNames(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    const team = (await response.json()).data?.score?.["1"]?.team ?? {};
    // An empty card's slot has no name yet: "" for both.
    return [team["1"]?.player?.["1"]?.name ?? "",
            team["2"]?.player?.["1"]?.name ?? ""];
}

// The tests share one app: no player registered, board 1's players empty.
async function clean(request: APIRequestContext) {
    for (const row of await players(request)) {
        await remove(request, row.key);
    }
    const response = await request.post(
        serverUrl("/api/v1/scoreboards/1:reset-players"),
        {data: {confirm: true}});
    expect(response.ok()).toBe(true);
    await expect.poll(() => boardNames(request)).toEqual(["", ""]);
}

function tab(page: Page, label: string) {
    return page.getByRole("tab", {name: label, exact: true});
}

function registered(page: Page) {
    return page.getByRole("table", {name: "Registered Players"});
}

async function openRegistered(page: Page) {
    await page.goto(serverUrl("/web/playerBrowser"));
    await tab(page, "Registered Players").click();
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) =>
        pageErrors.push(`/web/playerBrowser: ${error}`));
    await keepLocal(page);
    await clean(request);
});

test.afterEach(async ({request}) => {
    await clean(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the page opens on its two tabs, with no tournament and no players", async ({page}) => {
    await page.goto(serverUrl("/web/playerBrowser"));

    await expect(page).toHaveTitle("Autona Player List");
    await expect(page.getByText("No tournament loaded.")).toBeVisible();

    await tab(page, "Registered Players").click();
    await expect(page.getByText("No players registered.")).toBeVisible();
    await expect(page.getByRole("button", {name: "Edit player"})).toBeDisabled();
    await expect(page.getByRole("button", {name: "Delete selected player"}))
        .toBeDisabled();
});

test("a player is added, edited and deleted", async ({page, request}) => {
    await openRegistered(page);

    await page.getByRole("button", {name: "Add new player"}).click();
    let dialog = page.getByRole("dialog", {name: "Add new player"});
    const saveNew = dialog.getByRole("button", {name: "Save new player"});
    await expect(saveNew).toBeDisabled();
    await expect(dialog.getByRole("button", {name: "Delete player entry"}))
        .toBeDisabled();
    await dialog.getByLabel("Sponsor").fill("E2E");
    await dialog.getByLabel("GamerTag").fill("e2eAlpha");
    await dialog.getByLabel("Real Name").fill("Hidden Name");
    await saveNew.click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText("Player saved")).toBeVisible();
    const row = registered(page).getByRole("row", {name: /e2eAlpha/});
    await expect(row).toContainText("E2E");
    await expect.poll(async () => (await players(request))[0]?.key)
        .toBe("E2E e2eAlpha");
    expect((await players(request))[0].name).toBe("Hidden Name");

    // The selected row's editor: the same key updates it.
    await row.click();
    await page.getByRole("button", {name: "Edit player"}).click();
    dialog = page.getByRole("dialog", {name: "Edit player"});
    await expect(dialog.getByLabel("GamerTag")).toHaveValue("e2eAlpha");
    await dialog.getByLabel("Twitter").fill("@alpha");
    await dialog.getByRole("button", {name: "Update player"}).click();

    await expect(dialog).toBeHidden();
    await expect(row).toContainText("@alpha");
    const updated = await players(request);
    expect(updated).toHaveLength(1);
    expect(updated[0]).toMatchObject({key: "E2E e2eAlpha", twitter: "@alpha",
                                      name: "Hidden Name"});

    await page.getByRole("button", {name: "Delete selected player"}).click();
    const confirm = page.getByRole("dialog", {name: "Delete selected player"});
    await expect(confirm).toContainText("E2E e2eAlpha");
    await confirm.getByRole("button", {name: "Delete", exact: true}).click();

    await expect(page.getByText("Player deleted")).toBeVisible();
    await expect(page.getByText("No players registered.")).toBeVisible();
    expect(await players(request)).toEqual([]);
});

test("the search reaches the real name it does not show", async ({page, request}) => {
    await save(request, {gamerTag: "e2eAlpha", name: "Hidden Name"});
    await save(request, {gamerTag: "e2eBeta"});
    await openRegistered(page);
    const table = registered(page);
    await expect(table.getByRole("row", {name: /e2eAlpha/})).toBeVisible();
    await expect(table.getByRole("row", {name: /e2eBeta/})).toBeVisible();

    const search = page.getByRole("searchbox", {name: "Search players"});
    await search.fill("hidden");
    await expect(table.getByRole("row", {name: /e2eBeta/})).toBeHidden();
    await expect(table.getByRole("row", {name: /e2eAlpha/})).toBeVisible();
    await expect(table).not.toContainText("Hidden Name");

    await search.fill("nobody at all");
    await expect(page.getByText("No players match the search.")).toBeVisible();

    await search.fill("");
    await expect(table.getByRole("row", {name: /e2eBeta/})).toBeVisible();
});

test("a stored country is chosen again, with a region of the new one", async ({page, request}) => {
    await save(request, {gamerTag: "e2eAlpha", country_code: "JP"});
    await openRegistered(page);

    await registered(page).getByRole("row", {name: /e2eAlpha/}).dblclick();
    const dialog = page.getByRole("dialog", {name: "Edit player"});
    const country = dialog.getByRole("combobox", {name: "Country"});
    await expect(country).toHaveValue(/Japan/);

    await country.fill("United States");
    await page.getByRole("option", {name: /United States \(US\)/}).click();
    await expect(country).toHaveValue(/United States/);
    const region = dialog.getByRole("combobox", {name: "State"});
    await region.fill("California");
    await page.getByRole("option", {name: /California/}).click();
    await dialog.getByRole("button", {name: "Update player"}).click();

    await expect(dialog).toBeHidden();
    await expect.poll(async () => (await players(request))[0]?.country_code)
        .toBe("US");
    expect((await players(request))[0].state_code).toBe("CA");
});

test("an entrant is set on the board, and one no longer registered is refused", async ({page, request}) => {
    await save(request, {gamerTag: "e2eAlpha"});
    const [row] = await players(request);
    // No tournament in this app, so only the list of entrants is made up;
    // the :load the buttons send is the real one.
    await page.route("**/api/v1/players/entrants", (route) => route.fulfill({
        json: {ok: true, data: {game: null, entrants: [{...row, seed: 1}]}},
    }));
    await page.goto(serverUrl("/web/playerBrowser"));

    const setP1 = page.getByRole("button", {name: "Set as P1"});
    const setP2 = page.getByRole("button", {name: "Set as P2"});
    await expect(setP1).toBeDisabled();
    await page.getByRole("table", {name: "Tournament Entrants"})
        .getByRole("row", {name: /e2eAlpha/}).click();

    await setP1.click();
    await expect(page.getByText("e2eAlpha set as P1")).toBeVisible();
    await expect.poll(() => boardNames(request)).toEqual(["e2eAlpha", ""]);

    await remove(request, row.key);
    await setP2.click();
    await expect(page.getByText("This player is not in the player list"))
        .toBeVisible();
    expect(await boardNames(request)).toEqual(["e2eAlpha", ""]);
});
