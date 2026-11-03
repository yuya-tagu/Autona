import path from "node:path";
import {APIRequestContext, expect, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The 大会情報 panel in React (ADR-005 S4g): /web/event on the headless app
// writes through /api/v1 (the tournament, its details, its colours, its
// icon / banner), and core does the work (TournamentService,
// TournamentInfoService, ColorService, TournamentMediaService). Nothing
// here reaches start.gg: no tournament is loaded. Checked through text, the
// DOM and the API, never screenshots.

// A real PNG of the repository (a small flag), chosen as the banner.
const PNG = path.resolve(__dirname, "..", "..", "assets", "country_flag",
                         "jp.png");

let pageErrors: string[] = [];

async function tournamentInfo(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data?.tournamentInfo ?? {};
}

async function tournamentUrl(request: APIRequestContext) {
    const response = await request.get(serverUrl("/api/v1/tournament"));
    expect(response.ok()).toBe(true);
    return (await response.json()).data.url;
}

// The tests share one app: no tournament, empty details, Autona's colours,
// icon and (no) banner.
async function resetEvent(request: APIRequestContext) {
    expect((await request.delete(serverUrl("/api/v1/tournament"))).ok())
        .toBe(true);
    await expect.poll(() => tournamentUrl(request)).toBe("");
    expect((await request.patch(serverUrl("/api/v1/tournament/info"), {
        data: {tournamentName: "", eventName: "", address: "",
               numEntrants: 0},
    })).ok()).toBe(true);
    expect((await request.patch(serverUrl("/api/v1/colors/tournament"),
                                {data: {reset: true}})).ok()).toBe(true);
    expect((await request.delete(serverUrl("/api/v1/tournament/media"))).ok())
        .toBe(true);
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/event: ${error}`));
    await keepLocal(page);
    await resetEvent(request);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the Event page opens with its four cards", async ({page}) => {
    await page.goto(serverUrl("/web/event"));

    await expect(page).toHaveTitle("Autona Tournament info");
    for (const name of ["start.gg tournament", "Tournament details",
                        "Tournament colors", "Tournament icon / banner"]) {
        await expect(page.getByRole("region", {name, exact: true})).toBeVisible();
    }
});

test("the details typed on the page are the tournament info in the state", async ({page, request}) => {
    await page.goto(serverUrl("/web/event"));
    const card = page.getByRole("region", {name: "Tournament details",
                                           exact: true});

    await card.getByRole("textbox", {name: "Tournament name"}).fill("E2E Cup");
    await card.getByRole("textbox", {name: "Event name"}).fill("Singles");
    await card.getByRole("textbox", {name: "Venue"}).fill("Tokyo");
    await card.getByRole("textbox", {name: "Entrants"}).fill("64");
    await card.getByLabel("Date", {exact: true}).fill("2026-11-03");

    await expect.poll(async () => {
        const info = await tournamentInfo(request);
        return [info.tournamentName, info.eventName, info.address,
                info.numEntrants, info.startAtDate];
    }).toEqual(["E2E Cup", "Singles", "Tokyo", 64, "2026-11-03"]);

    // Not a count: refused on the page, the state left as it was.
    await card.getByRole("textbox", {name: "Entrants"}).fill("6x");
    await expect(page.getByText("Enter a whole number from 0 to 999999."))
        .toBeVisible();
    expect((await tournamentInfo(request)).numEntrants).toBe(64);
});

test("a tournament colour picked on the page, then the defaults again", async ({page, request}) => {
    await page.goto(serverUrl("/web/event"));
    const card = page.getByRole("region", {name: "Tournament colors",
                                           exact: true});

    await card.getByLabel("Theme color").fill("#123456");
    await expect.poll(async () =>
        String((await tournamentInfo(request)).themeColor).toLowerCase())
        .toBe("#123456");

    await card.getByRole("button", {name: "Back to the default colors"}).click();
    await expect.poll(async () => {
        const info = await tournamentInfo(request);
        return [String(info.themeColor).toLowerCase(),
                String(info.accentColor).toLowerCase()];
    }).toEqual(["#181b21", "#f0a500"]);
    await expect(card.getByLabel("Theme color")).toHaveValue("#181b21");
});

test("a banner chosen on the page is saved and shown, and set back once confirmed", async ({page, request}) => {
    await page.goto(serverUrl("/web/event"));
    const card = page.getByRole("region", {name: "Tournament icon / banner",
                                           exact: true});
    const banner = card.getByRole("img", {name: "Tournament banner"});
    await expect(banner).toHaveAttribute(
        "src", /\/assets\/thumbnail_base\/default_banner\.png$/);
    await expect(card.getByText("The default banner")).toBeVisible();

    await page.getByTestId("event-media-banner-file").setInputFiles(PNG);

    await expect.poll(async () => (await tournamentInfo(request)).bannerVersion)
        .toBeGreaterThan(0);
    await expect(banner).toHaveAttribute(
        "src", /\/user_data\/tournament_banner\.png\?v=\d+$/);
    // Served: the saved PNG is what the preview draws.
    await expect.poll(() => banner.evaluate(
        (image: HTMLImageElement) => image.complete && image.naturalWidth))
        .toBeGreaterThan(0);
    await expect(card.getByText("The default banner")).toBeHidden();

    await card.getByRole("button", {name: "Back to Autona's own"}).click();
    const confirm = page.getByRole("dialog");
    await confirm.getByRole("button", {name: "Set back"}).click();

    await expect.poll(async () => (await tournamentInfo(request)).bannerVersion)
        .toBe(0);
    await expect(banner).toHaveAttribute(
        "src", /\/assets\/thumbnail_base\/default_banner\.png$/);
});

test("a URL that is no start.gg event's is refused; disconnecting asks first", async ({page, request}) => {
    expect((await request.patch(serverUrl("/api/v1/tournament/info"),
                                {data: {tournamentName: "E2E Cup"}})).ok())
        .toBe(true);
    await page.goto(serverUrl("/web/event"));
    const card = page.getByRole("region", {name: "start.gg tournament",
                                           exact: true});
    const url = card.getByRole("textbox", {name: "Tournament URL"});

    await url.fill("https://example.invalid/not-an-event");
    await card.getByRole("button", {name: "Set (with the icon / banner)"})
        .click();
    await expect(page.getByText(/^Enter the URL of a start\.gg event/))
        .toBeVisible();
    expect(await tournamentUrl(request)).toBe("");

    await card.getByRole("button", {name: "Disconnect", exact: true}).click();
    const confirm = page.getByRole("dialog",
                                   {name: "Disconnect from the tournament"});
    await confirm.getByRole("button", {name: "Disconnect", exact: true}).click();

    await expect(url).toHaveValue("");
    await expect.poll(async () => (await tournamentInfo(request)).tournamentName)
        .toBe("");
});

test("the tournament is the state's: a clear is not polled for", async ({page, request}) => {
    // S4g review M1 / M3: the page used to read GET /api/v1/tournament every
    // second after its set / clear. Its buttons are busy until the state
    // (over Socket.IO) reaches the revision the 202 named.
    const reads: string[] = [];
    page.on("request", (sent) => {
        if (new URL(sent.url()).pathname === "/api/v1/tournament") {
            reads.push(sent.method());
        }
    });
    await page.goto(serverUrl("/web/event"));
    const card = page.getByRole("region", {name: "start.gg tournament",
                                           exact: true});
    const url = card.getByRole("textbox", {name: "Tournament URL"});
    const disconnect = card.getByRole("button", {name: "Disconnect",
                                                 exact: true});
    const before = (await tournamentInfo(request)).load.revision;
    await url.fill("https://example.invalid/typed");

    await disconnect.click();
    await page.getByRole("dialog", {name: "Disconnect from the tournament"})
        .getByRole("button", {name: "Disconnect", exact: true}).click();

    await expect(url).toHaveValue("");
    await expect.poll(async () => (await tournamentInfo(request)).load)
        .toEqual({status: "idle", error: "", revision: before + 1});
    await expect(disconnect).toBeEnabled();
    expect(reads).toEqual(["DELETE"]);
});

test("as the desktop's view opens it: in its language", async ({page}) => {
    await page.goto(serverUrl("/web/event?desktop=1&lng=ja"));

    await expect(page).toHaveTitle("Autona 大会情報");
    await expect(page.getByRole("region", {name: "start.gg の大会", exact: true}))
        .toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "ja");
});
