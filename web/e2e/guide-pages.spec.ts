import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The guide pages in React (ADR-005 S-final SF-4a-2): the Qt API Settings,
// Companion and OBS windows as /web/api, /web/companion and /web/obs, on the
// headless app in a browser. The access token is the desktop's shared view's
// only (GET /api/v1/access-token is a SHELL route: 403 to this browser, this
// PC's or not), so the API page tells the browser to open it in the app; the
// catalogue, the connection (THIS_PC, which this PC's request passes) and the
// tester's requests are the browser's too. Checked through text, the DOM and
// the network, never screenshots.

let pageErrors: string[] = [];

test.beforeEach(async ({page}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
    await keepLocal(page);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

function region(page: Page, name: string) {
    return page.getByRole("region", {name, exact: true});
}

test("the API page in a browser points to the app for the token", async ({page}) => {
    const tokenRead = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/v1/access-token");
    await page.goto(serverUrl("/web/api"));

    expect((await tokenRead).status()).toBe(403);
    await expect(page).toHaveTitle("Autona API Settings");
    await expect(page.getByText(
        "To show or regenerate the access token, open this page in the Autona desktop app."))
        .toBeVisible();
    await expect(page.getByLabel("Access token")).toHaveCount(0);
    await expect(page.getByRole("button", {name: "Regenerate"})).toHaveCount(0);
    // A browser has the page open already: no browser to open.
    await expect(page.getByRole("button", {name: "Open in the default browser"}))
        .toHaveCount(0);
    // The settings are the settings page's, linked.
    await expect(page.getByRole("link", {name: "Open settings"}))
        .toHaveAttribute("href", /^\/web\/settings\?lng=/);

    // The connection, read from this PC.
    const connection = region(page, "Connection");
    await expect(connection.getByText(/^http:\/\/[^ ]+\/api\/v1$/)).toBeVisible();
    await expect(connection.getByText(/^Port: \d+$/)).toBeVisible();
});

test("the API page lists the catalogue and its tester sends a GET", async ({page, request}) => {
    const catalog = await request.get(serverUrl("/api/v1/api-catalog"));
    expect(catalog.ok()).toBe(true);
    const endpoints = (await catalog.json()).data.endpoints;
    await page.goto(serverUrl("/web/api"));

    const reference = page.getByRole("table", {name: "Reference"});
    await expect(reference.getByRole("row")).toHaveCount(1 + endpoints.length);
    await expect(reference.getByText("Liveness / version / activeBoard"))
        .toBeVisible();
    await expect(region(page, "Envelope & error codes")
        .getByText(/NOT_FOUND {2}\(404\)/)).toBeVisible();

    await page.getByRole("tab", {name: "Tester"}).click();
    await expect(page.getByLabel("Path", {exact: true})).toHaveValue("/health");
    const health = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/v1/health"
        && response.request().method() === "GET");
    await page.getByRole("button", {name: "Send", exact: true}).click();
    expect((await health).status()).toBe(200);
    const answer = page.getByLabel("Response", {exact: true});
    await expect(answer).toContainText("HTTP 200");
    await expect(answer).toContainText('"ok": true');
});

test("the OBS guide shows this PC's absolute paths", async ({page}) => {
    await page.goto(serverUrl("/web/obs"));

    await expect(page).toHaveTitle("Autona OBS / Advanced Scene Switcher");
    await expect(page.getByRole("heading", {name: "OBS / Advanced Scene Switcher"}))
        .toBeVisible();
    // OBS runs on this PC: the loopback's URL.
    const actions = region(page, "Control Autona from Advanced Scene Switcher");
    await expect(actions.getByText(/^http:\/\/127\.0\.0\.1:\d+\/api\/v1\/scoreboards\/1\/score:reset$/))
        .toBeVisible();
    const watch = region(page, "Trigger OBS from Autona state");
    await expect(watch.getByText(/^(?:[A-Za-z]:\\|\/).*[\\/]out[\\/]program_state\.json$/))
        .toBeVisible();
});

test("the Companion guide shows its mappings and draws its icons", async ({page}) => {
    await page.goto(serverUrl("/web/companion"));

    await expect(page).toHaveTitle("Autona Companion settings");
    const mappings = page.getByRole("table", {name: "Example button mappings"});
    await expect(mappings.getByRole("row")).toHaveCount(11);
    await expect(mappings.getByText(/\/api\/v1\/scoreboards\/1:swap-teams$/))
        .toBeVisible();
    // Drawn on a canvas: a PNG to copy onto a key.
    const icon = region(page, "Button icons (click to copy)")
        .getByRole("img", {name: "Swap teams"});
    await expect(icon).toHaveAttribute("src", /^data:image\/png;base64,/);
    expect(await icon.evaluate((image: HTMLImageElement) => image.naturalWidth))
        .toBe(144);
});

// What is laid out wider than the view: the page itself, a table's
// container that scrolls sideways, a control past the right edge, an API
// path on more lines than it needs. Only what is shown (not a hidden tab).
async function overflow(page: Page) {
    return page.evaluate(() => {
        const width = document.documentElement.clientWidth;
        const shown = (element: Element) =>
            (element as HTMLElement).offsetParent !== null;
        const problems: string[] = [];
        if (document.documentElement.scrollWidth > width) {
            problems.push(`page ${document.documentElement.scrollWidth} > ${width}`);
        }
        for (const element of Array.from(
            document.querySelectorAll(".MuiTableContainer-root")).filter(shown)) {
            if (element.scrollWidth > element.clientWidth + 1) {
                problems.push(`table ${element.scrollWidth} > ${element.clientWidth}`);
            }
        }
        for (const element of Array.from(document.querySelectorAll(
            "button, input, textarea, .MuiChip-root, .MuiSelect-select"))
            .filter(shown)) {
            const right = element.getBoundingClientRect().right;
            if (right > width + 0.5) {
                const name = element.textContent?.trim()
                    || element.getAttribute("aria-label") || element.tagName;
                problems.push(`${name} ends at ${right} > ${width}`);
            }
        }
        for (const element of Array.from(
            document.querySelectorAll("[data-api-path]")).filter(shown)) {
            const style = getComputedStyle(element);
            const line = parseFloat(style.lineHeight)
                || 1.5 * parseFloat(style.fontSize);
            if (element.getBoundingClientRect().height >= 1.5 * line) {
                problems.push(`${element.textContent} wraps`);
            }
        }
        return problems;
    });
}

// The app's view is ~600 CSS px wide (860 device px at 150 %): there the
// reference puts its marks and buttons under the path (two columns); in a
// wide browser it keeps four. Each page fits either -- nothing cut off, no
// sideways scroll, an API path on one line (review of SF-4a-2: the marks
// and the buttons were cut off at the right, "/sta" over "te").
for (const width of [600, 860]) {
    for (const lng of ["en", "ja"]) {
        for (const panel of ["api", "companion", "obs"]) {
            test(`/web/${panel} (${lng}) fits a ${width} px view`, async ({page}) => {
                await page.setViewportSize({width, height: 900});
                await page.goto(serverUrl(`/web/${panel}?lng=${lng}`));
                await expect(page.locator("table tbody tr").first()).toBeVisible();
                if (panel === "api") {
                    const reference = page.locator("table").first();
                    await expect(reference.locator("[data-api-path]").first())
                        .toBeVisible();
                    // The container measured: 534 px at 600, 794 at 860.
                    await expect(reference.locator("thead th"))
                        .toHaveCount(width === 600 ? 2 : 4);
                    await expect(reference.locator(".MuiChip-root").first())
                        .toBeVisible();
                }
                expect(await overflow(page)).toEqual([]);

                if (panel === "api") {
                    await page.getByRole("tab").nth(1).click();
                    await expect(page.locator("textarea").first()).toBeVisible();
                    expect(await overflow(page)).toEqual([]);
                }
            });
        }
    }
}
