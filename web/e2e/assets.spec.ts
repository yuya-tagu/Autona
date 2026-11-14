import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The game assets page in React (ADR-005 S-final SF-4b-2): the Qt dialog
// "Add game assets" as /web/assets. Its writes and its job are the desktop
// shared view's (SHELL routes: 403 to a browser, this PC's or not), so on
// the headless app in a browser the page is the "elsewhere" one -- an
// explanation and the link to the source, nothing read. The shared view's
// layout is checked with the three reads stubbed (the probe, the pack list,
// the job): there is no desktop here to give the shell's secret. Checked
// through text, the DOM and the network, never screenshots.

let pageErrors: string[] = [];

test.beforeEach(async ({page}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
    await keepLocal(page);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

const SOURCE = "https://github.com/joaorb64/TournamentStreamHelper";

const PACKS = {
    source: {page: SOURCE, index: "https://example.invalid/assets.json"},
    index: {status: "ready", error: null, fetchedAt: 1},
    games: [{
        code: "a_game_with_a_rather_long_folder_name_for_a_narrow_view",
        name: "A Game With A Rather Long Title For A Narrow View Of The App",
        available: true, installed: false,
        packs: [
            {name: "base_files", size: 2097152, version: "2",
             installedVersion: null, installed: false, updateAvailable: false,
             default: true},
            {name: "stage_icon_with_a_long_name", size: 123456789,
             version: "2", installedVersion: "1", installed: true,
             updateAvailable: true, default: true},
        ],
    }],
};

const IDLE_JOB = {
    revision: 0, status: "idle", operation: null, game: null, stage: null,
    pack: null, file: null, done: 0, total: 0, imported: [], skipped: [],
    skippedEntries: [], skippedEntriesMore: 0, failed: [], error: null,
    message: null,
};

// What is laid out wider than the view: the page itself, a control past the
// right edge. Only what is shown.
async function overflow(page: Page) {
    return page.evaluate(() => {
        const width = document.documentElement.clientWidth;
        const shown = (element: Element) =>
            (element as HTMLElement).offsetParent !== null;
        const problems: string[] = [];
        if (document.documentElement.scrollWidth > width) {
            problems.push(`page ${document.documentElement.scrollWidth} > ${width}`);
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

async function stubShell(page: Page) {
    const json = (data: unknown) => ({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ok: true, data})});
    await page.route("**/api/v1/shell-session", (route) =>
        route.fulfill(json({ticket: "e2e-never-kept"})));
    await page.route("**/api/v1/assets/packs*", (route) =>
        route.fulfill(json(PACKS)));
    await page.route("**/api/v1/assets/job", (route) =>
        route.fulfill(json(IDLE_JOB)));
}

test("in a browser the page explains and reads nothing", async ({page}) => {
    const asked: string[] = [];
    page.on("request", (request) => {
        const {pathname} = new URL(request.url());
        if (pathname.startsWith("/api/v1/assets")) {
            asked.push(`${request.method()} ${pathname}`);
        }
    });
    const probe = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/v1/shell-session");
    await page.goto(serverUrl("/web/assets"));

    expect((await probe).status()).toBe(403);
    await expect(page).toHaveTitle("Autona Add game assets");
    await expect(page.getByText("Open this page in the Autona desktop app"))
        .toBeVisible();
    await expect(page.getByRole("link", {name: "Open asset source (GitHub)"}))
        .toHaveAttribute("href", SOURCE);
    await expect(page.getByRole("button", {name: "Download & load"}))
        .toHaveCount(0);
    await expect(page.getByRole("button", {name: "Import from ZIP"}))
        .toHaveCount(0);
    // The folder import is the desktop's dialog (SF-5a-2): not offered.
    await expect(page.getByRole("button", {name: "Import from folder"}))
        .toHaveCount(0);
    await expect(page.getByRole("button", {name: "Open games folder"}))
        .toHaveCount(0);
    expect(asked).toEqual([]);
});

test("the asset routes the page uses are the shell's: 403 to a browser", async ({request}) => {
    const job = await request.get(serverUrl("/api/v1/assets/job"));
    expect(job.status()).toBe(403);
    const upload = await request.post(serverUrl("/api/v1/assets/uploads"), {
        headers: {"content-length": "3"}, data: "abc"});
    expect(upload.status()).toBe(403);
    const reveal = await request.post(
        serverUrl("/api/v1/assets/games-folder:reveal"), {data: {}});
    expect(reveal.status()).toBe(403);
    // SF-5a-2: the picked folder's import, by its id alone.
    const folder = await request.post(
        serverUrl("/api/v1/assets:importFolder"), {data: {pick: "abcdefgh"}});
    expect(folder.status()).toBe(403);
});

for (const width of [600, 860]) {
    for (const lng of ["en", "ja"]) {
        test(`/web/assets elsewhere (${lng}) fits a ${width} px view`, async ({page}) => {
            await page.setViewportSize({width, height: 900});
            await page.goto(serverUrl(`/web/assets?lng=${lng}`));
            await expect(page.getByRole("link").first()).toBeVisible();
            expect(await overflow(page)).toEqual([]);
        });

        test(`/web/assets in the shared view (${lng}) fits a ${width} px view`, async ({page}) => {
            await stubShell(page);
            await page.setViewportSize({width, height: 900});
            await page.goto(serverUrl(`/web/assets?lng=${lng}`));
            const game = page.getByRole("combobox");
            await expect(game).toBeEnabled();
            await game.click();
            await page.getByRole("option").first().click();
            await expect(page.getByRole("checkbox").first()).toBeVisible();
            expect(await overflow(page)).toEqual([]);
        });
    }
}

// The progress card, a result with a long entry name and the two dialogs at
// 600 px (the shared view is about 604 CSS px wide).
const LONG = "a_very_long_entry_name_without_any_break_".repeat(4);

async function stubJob(page: Page, job: object, remembered?: number) {
    await stubShell(page);
    await page.route("**/api/v1/assets/job", (route) =>
        route.fulfill({status: 200, contentType: "application/json",
                       body: JSON.stringify({ok: true, data: {
                           ...IDLE_JOB, ...job}})}));
    if (remembered !== undefined) {
        await page.addInitScript((revision) => {
            sessionStorage.setItem("autona.assets.job", String(revision));
        }, remembered);
    }
}

for (const lng of ["en", "ja"]) {
    test(`the progress card (${lng}) fits 600 px with long names`, async ({page}) => {
        await stubJob(page, {
            revision: 3, status: "running", operation: "install",
            game: LONG, stage: "download", pack: LONG, file: `${LONG}.7z.001`,
            done: 1048576, total: 4194304});
        await page.setViewportSize({width: 600, height: 900});
        await page.goto(serverUrl(`/web/assets?lng=${lng}`));
        await expect(page.getByRole("progressbar").first()).toBeVisible();
        expect(await overflow(page)).toEqual([]);
    });

    test(`a result with a long entry name (${lng}) fits 600 px`, async ({page}) => {
        await stubJob(page, {
            revision: 3, status: "done", operation: "importZip",
            imported: [LONG], skipped: [LONG], skippedEntries: [LONG],
            skippedEntriesMore: 2,
            failed: [{name: LONG, message: LONG}]}, 3);
        await page.setViewportSize({width: 600, height: 900});
        await page.goto(serverUrl(`/web/assets?lng=${lng}`));
        await expect(page.getByRole("alert").first()).toBeVisible();
        for (const summary of await page.locator("summary, button[aria-expanded]").all()) {
            await summary.click();
        }
        expect(await overflow(page)).toEqual([]);
    });

    test(`the install and overwrite dialogs (${lng}) fit 600 px`, async ({page}) => {
        await stubShell(page);
        await page.route("**/api/v1/assets/uploads", (route) =>
            route.fulfill({
                status: 201, contentType: "application/json",
                body: JSON.stringify({ok: true, data: {
                    upload: "e2e-upload", games: [LONG, "other"],
                    conflicts: [LONG], expiresInSec: 900}})}));
        await page.route("**/api/v1/assets/uploads/*", (route) =>
            route.fulfill({status: 200, contentType: "application/json",
                           body: JSON.stringify({ok: true, data: {
                               removed: true}})}));
        await page.setViewportSize({width: 600, height: 900});
        await page.goto(serverUrl(`/web/assets?lng=${lng}`));

        const game = page.getByRole("combobox");
        await expect(game).toBeEnabled();
        await game.click();
        await page.getByRole("option").first().click();
        await page.getByRole("button", {name: /^(Download|ダウンロード)/}).first().click();
        await expect(page.getByRole("dialog")).toBeVisible();
        expect(await overflow(page)).toEqual([]);
        await page.getByRole("dialog").getByRole("button").first().click();
        await expect(page.getByRole("dialog")).toBeHidden();

        await page.locator('input[type="file"]').setInputFiles({
            name: "game.zip", mimeType: "application/zip",
            buffer: Buffer.from("zip")});
        await expect(page.getByRole("dialog")).toBeVisible();
        expect(await overflow(page)).toEqual([]);
    });

    test(`the picked folder's overwrite dialog (${lng}) fits 600 px`, async ({page}) => {
        // The folder button asks the desktop in a click (window.open of
        // /web/shell-request, SF-5a-2): kept here, and the desktop's answer
        // handed to the page as the shared view would.
        await stubShell(page);
        await page.addInitScript(() => {
            (window as any).__opened = [];
            window.open = ((url: string) => {
                (window as any).__opened.push(url);
                return null;
            }) as typeof window.open;
        });
        await page.setViewportSize({width: 600, height: 900});
        await page.goto(serverUrl(`/web/assets?lng=${lng}`));
        await expect(page.getByRole("combobox")).toBeEnabled();

        await page.getByRole("button", {
            name: /^(Import from folder|フォルダから取り込む)$/}).click();
        const opened: string[] = await page.evaluate(
            () => (window as any).__opened);
        expect(opened).toHaveLength(1);
        const url = new URL(opened[0]);
        expect(url.pathname).toBe("/web/shell-request");
        expect(url.searchParams.get("verb")).toBe("pickFolder");
        await page.evaluate(({id, games}) => {
            window.autonaPanelShell!.shellResult(id, {
                ok: true, status: "picked", pick: "e2e-pick", games,
                conflicts: [games[0]], expiresInSec: 900});
        }, {id: url.searchParams.get("request")!, games: [LONG, "other"]});
        await expect(page.getByRole("dialog")).toBeVisible();
        expect(await overflow(page)).toEqual([]);
    });
}
