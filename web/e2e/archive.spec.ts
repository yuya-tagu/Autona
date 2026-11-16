import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Archive Clipper page (ADR-006 A1): /web/app/archive on the headless app,
// with the fake worker (e2e/fake_worker.py, set up by global-setup.ts through
// AUTONA_VODSPLIT_CMD), so the status read is the real one. The writes (start,
// cancel, resume, work folder) are the desktop shared view's (SHELL routes:
// 403 to a browser), and there is no desktop here to give the shell's secret,
// so they and the job reads are stubbed with page.route (as assets.spec.ts
// does) to cover the UI flow; the real write routes are only checked to answer
// 403. Checked through text and the DOM, never screenshots.
//
// Not covered: the nav item hidden without a worker -- the app is started once
// for the whole run, with the fake worker (tests/test_archive_jobs.py and
// tests/test_webserver_routes.py cover the unavailable status).

let pageErrors: string[] = [];

test.beforeEach(async ({page}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`${error}`));
    await keepLocal(page);
});

test.afterEach(() => {
    expect(pageErrors, "uncaught page errors").toEqual([]);
});

const JOB_ID = "20261116-100000-abcd";

const SETS = {
    source_id: "abcDEF12345",
    sets: [
        {no: 1, start: 60, end: 180, matched: true, needs_review: false,
         title: "Winners Final", set: {s1: 3, s2: 1}},
        {no: 2, start: 200, end: 260, matched: false, needs_review: true,
         reason: "name mismatch", seen_names: ["A", "B"]},
    ],
};

function job(state: string, extra: object = {}) {
    return {id: JOB_ID, created_at: "2026-11-16T10:00:00",
            source: "C:/vods/match.mp4", state, ...extra};
}

const envelope = (data: unknown, status = 200) => ({
    status, contentType: "application/json",
    body: JSON.stringify({ok: true, data})});

// The archive's job routes, stubbed. `detail` is what GET of the job answers
// (it changes as the scenario moves); `posts` records the writes.
async function stubJobs(page: Page, scenario: {
    detail: () => object;
    posts: string[];
    afterPost?: (action: string) => void;
}) {
    await page.route("**/api/v1/archive/jobs**", (route) => {
        const request = route.request();
        const {pathname} = new URL(request.url());
        if (request.method() === "POST") {
            const action = pathname.includes(":")
                ? pathname.split(":").pop()! : "create";
            scenario.posts.push(action);
            scenario.afterPost?.(action);
            return route.fulfill(envelope({job: (scenario.detail() as any).job}, 201));
        }
        if (pathname.endsWith("/jobs")) {
            return route.fulfill(envelope({jobs: [(scenario.detail() as any).job]}));
        }
        return route.fulfill(envelope(scenario.detail()));
    });
}

async function startJob(page: Page, source: string) {
    await page.getByRole("textbox", {name: /^Source video/}).fill(source);
    await page.getByRole("button", {name: "Start", exact: true}).click();
}

test("the nav shows the archive item (the real status read)", async ({page}) => {
    await page.goto(serverUrl("/web/app/stats"));
    const nav = page.getByRole("navigation");
    await expect(nav.getByText("Archive Clipper")).toBeVisible();
    await nav.getByText("Archive Clipper").click();
    await expect(page).toHaveURL(/\/web\/app\/archive/);
});

test("a started job shows its progress and then its sets", async ({page}) => {
    let state = "running";
    const posts: string[] = [];
    await stubJobs(page, {
        posts,
        detail: () => state === "running"
            ? {job: job("running", {progress: {index: 1, count: 3,
                                              label: "scan", overall: 0.4}}),
               sets: null}
            : {job: job("done"), sets: SETS},
    });
    await page.goto(serverUrl("/web/app/archive"));
    await startJob(page, "C:/vods/match.mp4");
    expect(posts).toEqual(["create"]);

    await expect(page.getByRole("progressbar")).toBeVisible();
    state = "done";

    const first = page.getByTestId("archive-set-1");
    const second = page.getByTestId("archive-set-2");
    await expect(first).toBeVisible({timeout: 15_000});
    await expect(first).toContainText("#1");
    await expect(first).toContainText("0:01:00");
    await expect(first).toContainText("2:00");
    await expect(second).toContainText("#2");
    await expect(first).not.toContainText("Needs review");
    await expect(second).toContainText("Needs review");
    // A local file's source never becomes a YouTube link.
    await expect(page.locator('a[href*="youtube.com"]')).toHaveCount(0);

    // The review filter keeps only the set that needs one.
    await page.getByRole("button", {name: "Needs review", exact: true}).click();
    await expect(first).toHaveCount(0);
    await expect(second).toBeVisible();
    await page.getByRole("button", {name: "All", exact: true}).click();
    await expect(first).toBeVisible();
});

test("a running job can be stopped and then resumed", async ({page}) => {
    let state = "running";
    const posts: string[] = [];
    await stubJobs(page, {
        posts,
        detail: () => ({job: job(state), sets: null}),
        afterPost: (action) => {
            state = action === "cancel" ? "cancelled" : "running";
        },
    });
    await page.goto(serverUrl("/web/app/archive"));
    await startJob(page, "C:/vods/slow.mp4");

    const stop = page.getByRole("button", {name: "Stop", exact: true});
    await expect(stop).toBeVisible({timeout: 15_000});
    await stop.click();

    const resume = page.getByRole("button", {name: "Resume", exact: true});
    await expect(resume).toBeVisible({timeout: 15_000});
    await expect(stop).toHaveCount(0);
    expect(posts).toEqual(["create", "cancel"]);
});

test("the real write routes answer 403 to a browser, and the page says why", async ({page, request}) => {
    const base = serverUrl("/api/v1/archive");
    expect((await request.post(`${base}/jobs`,
                               {data: {source: "x"}})).status()).toBe(403);
    expect((await request.patch(`${base}/settings`,
                                {data: {work_dir: "x"}})).status()).toBe(403);
    expect((await request.post(`${base}/jobs/${JOB_ID}:cancel`,
                               {data: {}})).status()).toBe(403);
    expect((await request.post(`${base}/jobs/${JOB_ID}:resume`,
                               {data: {}})).status()).toBe(403);
    // Reads stay open to this PC.
    expect((await request.get(`${base}/status`)).ok()).toBe(true);

    // The page shows the server's own refusal text, not its generic one.
    await page.goto(serverUrl("/web/app/archive"));
    const refusal = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/v1/archive/jobs"
        && response.request().method() === "POST");
    await startJob(page, "C:/vods/match.mp4");
    expect((await refusal).status()).toBe(403);
    const message = (await (await refusal).json()).error.message as string;
    expect(message).not.toBe("");
    await expect(page.getByRole("alert").filter({hasText: message})).toBeVisible();
});
