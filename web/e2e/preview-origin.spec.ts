import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {boardScores, keepLocal, previewOrigin, resetBoard, serverUrl} from "./server";

// The layout previews (ADR-005 S-final SF-8a): the pages' preview frames load
// from the headless app's preview listener -- this host, another port (GET
// /api/v1/health's previewPort) -- sandboxed with that origin kept. There the
// layout draws and follows the state as on OBS, and the server takes no write,
// token or not; from there it cannot write to the app's own port either. A
// frame that takes the keyboard focus is taken out. Checked through the DOM
// and the API, never screenshots.
//
// Nor can the layout get onto the app's own port by navigating its frame, or
// a frame it makes, there (the SF-8a review): the sandbox stops neither, and
// a layout loaded there would be one of the app's pages. The app answers a
// frame another page asks for with a refusal shown in no frame.

const SANDBOX = "allow-scripts allow-same-origin";
const SCORE_SET = "/api/v1/scoreboards/1/score:set";
const LAYOUT = "/layout/commentators/index.html";
// A legacy write a GET makes: what a frame navigated there would do.
const GET_WRITE = "/scoreboard1-team1-scoreup";

let pageErrors: string[] = [];

async function clearCaster(request: APIRequestContext) {
    const response = await request.patch(serverUrl("/api/v1/commentary/1"), {data: {
        name: "", team: "", twitter: "", role: "", icon: "", note: "",
        display: false,
    }});
    expect(response.ok()).toBe(true);
}

function preview(page: Page) {
    return page.getByTitle("Preview", {exact: true});
}

function shown(page: Page) {
    return page.frameLocator('iframe[title="Preview"]');
}

// The preview's layout, connected to its own origin's Socket.IO.
async function layoutConnected(page: Page) {
    await expect.poll(() => shown(page).locator("body").evaluate(
        () => (window as any).tshSocket?.connected === true)).toBe(true);
}

// The frames of the page with a layout running on the app's own port.
async function layoutsOnTheAppsPort(page: Page): Promise<string[]> {
    const app = new URL(serverUrl("/")).origin;
    const running: string[] = [];
    for (const frame of page.frames()) {
        if (frame === page.mainFrame() || !frame.url().startsWith(app)) {
            continue;
        }
        const socket = await frame.evaluate(
            () => typeof (window as any).tshSocket).catch(() => "unreachable");
        if (socket !== "undefined" && socket !== "unreachable") {
            running.push(frame.url());
        }
    }
    return running;
}

// Whether the page sees a document of its own origin in its preview frame
// (what a layout loaded on the app's port would be to it).
function frameDocumentSeen(page: Page) {
    return page.evaluate(() => {
        const frame = document.querySelector<HTMLIFrameElement>(
            'iframe[title="Preview"]');
        try {
            return frame?.contentDocument != null;
        } catch {
            return false;
        }
    });
}

// The preview's layout sends its own frame to `url` (location.href).
async function sendPreviewTo(page: Page, url: string) {
    await shown(page).locator("body").evaluate((body, to) => {
        setTimeout(() => {
            window.location.href = to;
        }, 0);
    }, url);
}

test.beforeEach(async ({page, request}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`/web/commentary: ${error}`));
    await keepLocal(page);
    await clearCaster(request);
    await resetBoard(request);
});

test.afterEach(async ({request}) => {
    await clearCaster(request);
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("the preview loads from the preview origin, sandboxed, and draws live", async ({page, request}) => {
    const origin = await previewOrigin(request);
    await page.goto(serverUrl("/web/commentary"));

    await expect(preview(page)).toHaveAttribute(
        "src", `${origin}/layout/commentators/index.html`);
    await expect(preview(page)).toHaveAttribute("sandbox", SANDBOX);
    const frameUrl = new URL((await preview(page).getAttribute("src"))!);
    expect(frameUrl.port).not.toBe(new URL(page.url()).port);
    expect(frameUrl.hostname).toBe(new URL(page.url()).hostname);

    // The layout runs there and follows the state, as OBS's does.
    await layoutConnected(page);
    const response = await request.patch(serverUrl("/api/v1/commentary/1"),
                                         {data: {name: "Preview Caster", display: true}});
    expect(response.ok()).toBe(true);
    await expect(shown(page).locator(".caster-card .name")).toHaveText("Preview Caster");
});

test("the preview origin serves a layout's reads and nothing else", async ({request}) => {
    const origin = await previewOrigin(request);

    expect((await request.get(`${origin}/api/v1/state`)).ok()).toBe(true);
    expect((await request.get(`${origin}/layout/commentators/index.html`)).ok()).toBe(true);
    for (const [method, pathname] of [
        ["POST", SCORE_SET], ["GET", "/scoreboard1-team1-scoreup"],
        ["GET", "/web/board"], ["GET", "/api/v1/connection"],
        ["GET", "/program-state"], ["GET", "/api/designer/projects"],
    ]) {
        const refused = await request.fetch(`${origin}${pathname}`, {
            method, data: method === "POST" ? {team1: 3, team2: 3} : undefined});
        expect(refused.status(), `${method} ${pathname}`).toBe(403);
        expect((await refused.json()).error.code).toBe("FORBIDDEN");
    }
    expect(await boardScores(request)).toEqual([0, 0]);
});

test("the preview's layout can write neither there nor to the app's port", async ({page, request}) => {
    await page.goto(serverUrl("/web/commentary"));
    await layoutConnected(page);

    // Over its own Socket.IO: read-only.
    const ack = await shown(page).locator("body").evaluate(() => new Promise(
        (resolve) => (window as any).tshSocket.emit(
            "team_scoreup", JSON.stringify({scoreboardNumber: "1", team: "1"}),
            resolve)));
    expect(ack).toMatchObject({ok: false, error: {code: "FORBIDDEN"}});

    // To the app's own port: another origin, so no write without the token.
    const status = await shown(page).locator("body").evaluate(
        (body, url) => fetch(url, {
            method: "POST",
            headers: {"content-type": "application/json"},
            body: JSON.stringify({team1: 3, team2: 3}),
        }).then((response) => response.status),
        serverUrl(SCORE_SET));
    expect(status).toBe(403);
    expect(await boardScores(request)).toEqual([0, 0]);
});

test("a preview frame sent to the app's own port gets nothing there", async ({page, request}) => {
    const origin = await previewOrigin(request);
    await page.goto(serverUrl("/web/commentary"));
    await layoutConnected(page);
    const frame = await (await preview(page).elementHandle())!.contentFrame();

    // Its own layout, on the app's port (the port is in document.referrer).
    const answered = page.waitForResponse(serverUrl(LAYOUT));
    await sendPreviewTo(page, serverUrl(LAYOUT));
    const response = await answered;
    expect(response.status()).toBe(403);
    // The refusal, shown in no frame (its body as the browser keeps it for a
    // blocked frame is not the server's: read the headers).
    expect(response.headers()["content-type"]).toBe("application/json");
    expect(response.headers()["content-security-policy"]).toContain(
        "frame-ancestors 'none'");
    await expect.poll(() => frame!.url()).not.toBe(`${origin}${LAYOUT}`);

    expect(await layoutsOnTheAppsPort(page)).toEqual([]);
    expect(await frameDocumentSeen(page)).toBe(false);

    // A write a GET makes, navigated to: refused too.
    await page.reload();
    await layoutConnected(page);
    const refused = page.waitForResponse(serverUrl(GET_WRITE));
    await sendPreviewTo(page, serverUrl(GET_WRITE));
    expect((await refused).status()).toBe(403);
    expect(await boardScores(request)).toEqual([0, 0]);
    expect(await layoutsOnTheAppsPort(page)).toEqual([]);
});

test("a frame the preview makes on the app's own port gets nothing there", async ({page, request}) => {
    await page.goto(serverUrl("/web/commentary"));
    await layoutConnected(page);

    const layout = page.waitForResponse(serverUrl(LAYOUT));
    const write = page.waitForResponse(serverUrl(GET_WRITE));
    await shown(page).locator("body").evaluate((body, urls) => {
        for (const url of urls) {
            const nested = document.createElement("iframe");
            nested.src = url;
            document.body.appendChild(nested);
        }
    }, [serverUrl(LAYOUT), serverUrl(GET_WRITE)]);
    expect((await layout).status()).toBe(403);
    expect((await write).status()).toBe(403);

    // The nested frames are there, on no document of the app's.
    await expect.poll(() => page.frames().length).toBeGreaterThanOrEqual(4);
    expect(await layoutsOnTheAppsPort(page)).toEqual([]);
    expect(await boardScores(request)).toEqual([0, 0]);
    // The preview itself goes on.
    await expect.poll(() => shown(page).locator("body").evaluate(
        () => (window as any).tshSocket?.connected === true)).toBe(true);
});

test("a preview that takes the keyboard focus is taken out", async ({page}) => {
    await page.goto(serverUrl("/web/commentary"));
    await layoutConnected(page);

    // The frame is taken out as it takes the focus, so the script that took
    // it may not get to return: its frame is gone ("Frame was detached").
    await shown(page).locator("body").evaluate(() => {
        document.body.tabIndex = -1;
        window.focus();
        document.body.focus();
    }).catch((error: Error) => {
        expect(error.message).toContain("detached");
    });

    await expect(preview(page)).toHaveCount(0);
    await expect(page.getByText(
        "This design's preview was stopped because it took the keyboard focus.",
        {exact: false})).toBeVisible();
});
