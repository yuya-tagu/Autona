import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Archive Clipper page (ADR-006 A1): /web/app/archive on the headless app,
// with the fake worker (e2e/fake_worker.py, set up by global-setup.ts through
// AUTONA_VODSPLIT_CMD), so the status read is the real one. The writes (start,
// cancel, resume, work folder, A4's review save and export, and A5's results
// preparation) are the desktop shared view's (SHELL routes: 403 to a
// browser), and there is no desktop here to give the shell's secret, so they
// and the job reads are stubbed with page.route (as assets.spec.ts does) to
// cover the UI flow -- the shell probe (GET /api/v1/shell-session) too where
// a scenario writes, else the page knows it is a browser and only reads; the
// real write routes are only checked to answer 403 (the real export with the
// fake worker is tests/test_archive_jobs.py's, the real preparation
// tests/test_archive_results.py's). Checked through text and the DOM, never
// screenshots.
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
// (it changes as the scenario moves); `posts` records the writes. The job's
// video is bytes that are no video (the page then says why it does not play)
// and it has no frames. Its review (A4) is empty and `puts` records the
// bodies saved; `bodies`, the writes' (A5: the results a job is created with).
async function stubJobs(page: Page, scenario: {
    detail: () => object;
    posts: string[];
    afterPost?: (action: string) => void;
    puts?: any[];
    bodies?: any[];
}) {
    await page.route("**/api/v1/archive/jobs**", (route) => {
        const request = route.request();
        const {pathname} = new URL(request.url());
        if (pathname.endsWith("/review")) {
            if (request.method() === "PUT") {
                const body = request.postDataJSON();
                scenario.puts?.push(body);
                return route.fulfill(envelope({review: {format: 1, ...body}}));
            }
            return route.fulfill(envelope({review: {format: 1, sets: {}}}));
        }
        if (pathname.endsWith("/video")) {
            return route.fulfill({status: 200, contentType: "video/mp4",
                                  body: "not a video"});
        }
        if (pathname.includes("/frames/")) {
            return route.fulfill({status: 404, contentType: "application/json",
                                  body: JSON.stringify({ok: false})});
        }
        if (request.method() === "POST") {
            const action = pathname.includes(":")
                ? pathname.split(":").pop()! : "create";
            scenario.posts.push(action);
            scenario.bodies?.push(request.postDataJSON());
            scenario.afterPost?.(action);
            return route.fulfill(envelope({job: (scenario.detail() as any).job}, 201));
        }
        if (pathname.endsWith("/jobs")) {
            return route.fulfill(envelope({jobs: [(scenario.detail() as any).job]}));
        }
        return route.fulfill(envelope(scenario.detail()));
    });
}

// The page as the desktop's shared view sees itself: the shell probe answers
// (its writes are on). Without it the page is a browser's: read-only.
async function stubShell(page: Page) {
    await page.route("**/api/v1/shell-session", (route) =>
        route.fulfill(envelope({ticket: "e2e-never-kept"})));
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
    await stubShell(page);
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

// The review screen (ADR-006 A3): ?job= opens a finished job, as the
// preview's "open in browser" does, on the bare page that link opens.
const REVIEW_SETS = {
    duration: 600, event: "Fake Cup", source_id: "abcDEF12345",
    games: [[70, 130], [140, 170], [210, 250]],
    games_ocr: [{samples: 10, names: {Alpha: 1}},
                {samples: 10, names: {Alpha: 0.9, Beta: 0.5}},
                {samples: 10, names: {Gamma: 0.4}}],
    sets: [
        {no: 1, start: 60, end: 180, matched: true, title: "Winners Final",
         set: {round: "Winners Final", p1: "Alpha", s1: 2, p2: "Beta", s2: 0},
         games: 2, game_spans: [[70, 130], [140, 170]], corrected_games: [2],
         thumb: "01_x_bg.jpg"},
        {no: 2, start: 200, end: 260, matched: false, needs_review: true,
         reason: "name mismatch", game_spans: [[210, 250]]},
    ],
};

test("a finished job shows its preview, timeline and details", async ({page, request}) => {
    await stubJobs(page, {posts: [],
                          detail: () => ({job: job("done"), sets: REVIEW_SETS})});
    await page.goto(serverUrl(`/web/archive?job=${JOB_ID}`));

    const video = page.getByTestId("archive-video");
    await expect(video).toBeVisible({timeout: 15_000});
    expect(await video.getAttribute("src"))
        .toMatch(new RegExp(`/api/v1/archive/jobs/${JOB_ID}/video$`));
    const timeline = page.getByRole("region", {name: "Timeline"});
    await expect(timeline).toBeVisible();
    await expect(page.getByTestId("archive-timeline-sets")
        .locator("[data-timeline-set]")).toHaveCount(2);
    await expect(page.getByTestId("archive-timeline-games")
        .locator("rect")).toHaveCount(3);
    await expect(page.getByTestId("archive-timeline-games")
        .locator("rect[data-corrected]")).toHaveCount(1);
    // The names read off the scoreboard: collapsed until opened.
    await expect(page.getByTestId("archive-timeline-player")).toHaveCount(0);
    await timeline.getByRole("button", {name: "Players (OCR)"}).click();
    await expect(page.getByTestId("archive-timeline-player")).toHaveCount(3);
    // Only Space, Up / Down and N are hinted: I / O / Enter edit the review
    // (A4), which a browser does not.
    await expect(page.getByTestId("archive-key-hints")).toHaveText(
        /Space.*Play.*↑.*↓.*Set.*N.*Next to review/);

    // The keys work within the page.
    await page.getByTestId("archive-review").focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByTestId("archive-set-1")).toHaveAttribute("aria-current", "true");
    const details = page.getByTestId("archive-details");
    await expect(details).toContainText("Alpha vs Beta");
    await expect(details).toContainText("Fake Cup · Winners Final");
    await expect(page.getByTestId("archive-timecode")).toHaveText(/^0:01:00/);
    await page.keyboard.press("n");
    await expect(page.getByTestId("archive-set-2")).toHaveAttribute("aria-current", "true");
    await expect(details).toContainText("name mismatch");

    // A click on the timeline picks the set under it and seeks there.
    const lane = page.getByTestId("archive-timeline-sets");
    const box = (await lane.boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.2, box.y + box.height / 2);
    await expect(page.getByTestId("archive-set-1")).toHaveAttribute("aria-current", "true");
    // 20% of 10:00, give or take a pixel.
    await expect(page.getByTestId("archive-timecode")).toHaveText(/^0:0(1:5\d|2:0\d)/);
    // The set view zooms the timeline to the selected set.
    await timeline.getByRole("button", {name: "Set", exact: true}).click();
    await expect(page.getByTestId("archive-timeline-sets")
        .locator("[data-timeline-set]")).toHaveCount(1);

    // The stub's bytes are no video: the page says why. A browser without
    // H.264 (Playwright's Chromium, like Autona's window) offers this page in
    // the PC's browser.
    const h264 = await page.evaluate(() => document.createElement("video")
        .canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"'));
    const problem = page.getByTestId("archive-video-problem");
    await expect(problem).toBeVisible({timeout: 15_000});
    if (h264 === "") {
        const open = problem.getByRole("link", {name: "Open in browser"});
        await expect(open).toHaveAttribute("target", "_blank");
        expect(await open.getAttribute("href"))
            .toMatch(new RegExp(`/web/archive\\?job=${JOB_ID}$`));
        // Where it goes the page only reads (said next to the button).
        await expect(problem.getByTestId("archive-browser-note"))
            .toContainText("only reads");
    } else {
        await expect(problem).toContainText("This video cannot be played.");
    }

    // This is a browser (the real shell probe: 403): the page says it only
    // reads.
    await expect(page.getByTestId("archive-read-only")).toBeVisible();

    // The real routes: this PC reads them (no job of that id here: 404,
    // not a refusal).
    const base = serverUrl(`/api/v1/archive/jobs/${JOB_ID}`);
    expect((await request.get(`${base}/video`)).status()).toBe(404);
    expect((await request.get(`${base}/frames/1`)).status()).toBe(404);
    expect((await request.get(`${base}/review`)).status()).toBe(404);
});

// The review's edits and the export (ADR-006 A4), in the desktop's window
// (stubbed probe; the review save and the export stubbed too).
test("a finished job's sets are confirmed, IN dragged, and exported", async ({page}) => {
    let exported: object | null = null;
    const posts: string[] = [];
    const puts: any[] = [];
    await stubShell(page);
    await stubJobs(page, {
        posts, puts,
        detail: () => ({job: job("done", {export: exported}), sets: REVIEW_SETS}),
        afterPost: (action) => {
            if (action === "export") {
                exported = {state: "running", progress: {overall: 0.5}};
            }
        },
    });
    await page.goto(serverUrl(`/web/archive?job=${JOB_ID}`));
    const first = page.getByTestId("archive-set-1");
    await expect(first).toBeVisible({timeout: 15_000});
    const exportButton = page.getByRole("button", {name: "Export confirmed"});
    await expect(exportButton).toBeDisabled();

    await first.click();
    await expect(page.getByTestId("archive-confirm")).toBeVisible();
    await expect(page.getByTestId("archive-key-hints")).toContainText("Confirm & next");

    // The IN handle dragged on the timeline, from 1:00 (10% of 10:00) to 15%.
    const lane = (await page.getByTestId("archive-timeline-sets").boundingBox())!;
    const handle = (await page.getByTestId("archive-handle-in").boundingBox())!;
    const y = handle.y + handle.height / 2;
    await page.mouse.move(handle.x + handle.width / 2, y);
    await page.mouse.down();
    await page.mouse.move(lane.x + lane.width * 0.15, y, {steps: 5});
    await page.mouse.up();
    await expect(page.getByTestId("archive-detected-range")).toContainText("1:00");
    await expect(first).toHaveAttribute("aria-current", "true");

    // Enter confirms it and moves on.
    await page.keyboard.press("Enter");
    await expect(first).toHaveAttribute("data-confirmed", "true");
    await expect(page.getByTestId("archive-set-2")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("archive-confirmed-count")).toHaveText("Confirmed 1 / 2");
    // Saved once the edits stop, as a whole.
    await expect(page.getByTestId("archive-save-state")).toHaveText("Saved");
    const entry = puts[puts.length - 1].sets["1"];
    expect(entry.confirmed).toBe(true);
    // 15% of 10:00, give or take a pixel.
    expect(Math.abs(entry.start - 90)).toBeLessThan(2);
    expect(entry.end).toBeUndefined();

    await exportButton.click();
    await expect(page.getByTestId("archive-export-progress")).toBeVisible();
    expect(posts).toEqual(["export"]);
    exported = {state: "done", clips: 1, skipped: 0,
                out_dir: `C:/work/${JOB_ID}/clips`,
                files: [{no: 1, file: "01_Winners_Final_Alpha_vs_Beta.mp4",
                         exported: true}]};
    const result = page.getByTestId("archive-export-result");
    await expect(result).toBeVisible({timeout: 15_000});
    await expect(result).toContainText("Exported 1 clip.");
    await expect(page.getByTestId("archive-export-dir"))
        .toContainText(`C:/work/${JOB_ID}/clips`);
    await expect(page.getByTestId("archive-export-progress")).toHaveCount(0);
});

test("a running job can be stopped and then resumed", async ({page}) => {
    let state = "running";
    const posts: string[] = [];
    await stubShell(page);
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

// The results a job is given (ADR-006 A5): prepared before the job starts
// (POST results:prepare, a SHELL route: stubbed here, its 403 is checked
// below), summed up, handed to the job as they are; the job's sets then show
// their characters' icons (the game's icon files, stubbed) and 推定.
const PNG = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
    "base64");
const PREPARED = {event: "Fake Cup", sets: [
    {round: "Winners Final", p1: "Alpha", s1: 2, p2: "Beta", s2: 0,
     c1: "Mario", c2: "Luigi", char_source: "scoreboard",
     autona: {game: "ssbu", c1: ["mario"], c2: ["luigi"], icons: {
         mario: "base_files/icon/chara_2_mario_00.png",
         luigi: "base_files/icon/chara_2_luigi_00.png"}}},
    {round: "Grand Final", p1: "Gamma", s1: 3, p2: "Delta", s2: 1,
     c1: "Zelda", char_source: "mains",
     autona: {game: "ssbu", c1: ["zelda"], c2: [], icons: {
         zelda: "base_files/icon/chara_2_zelda_00.png"}}},
]};

test("a job's results come from the match log and its sets show their characters", async ({page}) => {
    const posts: string[] = [];
    const bodies: any[] = [];
    const prepared: any[] = [];
    let created = false;
    await stubShell(page);
    await page.route("**/api/v1/archive/match-logs", (route) =>
        route.fulfill(envelope({days: [
            {date: "20261115", records: 2,
             tournaments: [{name: "Fake Cup", records: 2}]}]})));
    await page.route("**/api/v1/archive/results:prepare", (route) => {
        prepared.push(route.request().postDataJSON());
        return route.fulfill(envelope({results: PREPARED, summary: {
            sets: 2, game: "ssbu", records: 2,
            sources: {scoreboard: 1, report: 0, mains: 1, given: 0,
                      none: 0}}}));
    });
    await page.route("**/user_data/games/**", (route) =>
        route.fulfill({status: 200, contentType: "image/png", body: PNG}));
    await stubJobs(page, {
        posts, bodies,
        afterPost: () => {
            created = true;
        },
        detail: () => created
            ? {job: job("done"), sets: {source_id: "abcDEF12345", sets: [
                {no: 1, start: 60, end: 180, matched: true,
                 title: "Winners Final", set: PREPARED.sets[0]},
                {no: 2, start: 200, end: 320, matched: true,
                 title: "Grand Final", set: PREPARED.sets[1]}]}}
            : {job: job("done"), sets: null},
    });
    await page.goto(serverUrl("/web/app/archive"));
    await page.getByRole("textbox", {name: /^Source video/})
        .fill("C:/vods/match.mp4");
    const start = page.getByRole("button", {name: "Start", exact: true});

    await page.getByLabel("Autona's match log").check();
    await expect(page.getByRole("combobox", {name: "Date"}))
        .toHaveText("2026-11-15 (2)");
    await expect(page.getByRole("combobox", {name: "Tournament"}))
        .toHaveText("The whole day (2)");
    await expect(start).toBeDisabled();
    await page.getByRole("button", {name: "Load", exact: true}).click();
    const summary = page.getByTestId("archive-results-summary");
    await expect(summary).toContainText("2 sets ready");
    await expect(summary).toContainText("Characters: recorded 1 · estimated 1");
    expect(prepared).toEqual([{kind: "matchlog", date: "20261115",
                               tournament: null}]);

    await start.click();
    await expect.poll(() => posts).toEqual(["create"]);
    expect(bodies[0]).toEqual({source: "C:/vods/match.mp4", results: PREPARED,
                               event: null});

    const first = page.getByTestId("archive-set-characters-1");
    await expect(first.locator("img")).toHaveCount(2);
    await expect(first.locator("img").first())
        .toHaveAttribute("src",
                         /\/user_data\/games\/ssbu\/base_files\/icon\/chara_2_mario_00\.png$/);
    await expect(first.locator("img").first())
        .toHaveJSProperty("naturalWidth", 1);
    await expect(first.getByTestId("archive-estimate")).toHaveCount(0);
    await expect(page.getByTestId("archive-set-characters-2")
        .getByTestId("archive-estimate")).toHaveText("Estimated");

    await page.getByTestId("archive-set-2").click();
    const details = page.getByTestId("archive-detail-characters");
    await expect(details).toContainText("Zelda");
    await expect(details.getByTestId("archive-estimate")).toBeVisible();
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
    expect((await request.put(`${base}/jobs/${JOB_ID}/review`,
                              {data: {sets: {}}})).status()).toBe(403);
    expect((await request.post(`${base}/jobs/${JOB_ID}:export`,
                               {data: {}})).status()).toBe(403);
    // A5: the results' preparation, as the job's creation it feeds.
    expect((await request.post(`${base}/results:prepare`,
                               {data: {kind: "json", results: {sets: []}}}))
        .status()).toBe(403);
    // Reads stay open to this PC (the match log's days too, A5).
    expect((await request.get(`${base}/status`)).ok()).toBe(true);
    const days = await request.get(`${base}/match-logs`);
    expect(days.ok()).toBe(true);
    expect(Array.isArray((await days.json()).data.days)).toBe(true);

    // A browser (the real shell probe): the page only reads, and says so.
    await page.goto(serverUrl("/web/app/archive"));
    await expect(page.getByTestId("archive-read-only")).toBeVisible();
    await page.getByRole("textbox", {name: /^Source video/}).fill("C:/vods/x.mp4");
    await expect(page.getByRole("button", {name: "Start", exact: true}))
        .toBeDisabled();
    await expect(page.getByRole("button", {name: "Change", exact: true}))
        .toBeDisabled();

    // Told it is the shell (stubbed probe), it writes: the server refuses,
    // and the page shows the server's own refusal text, not its generic one.
    await stubShell(page);
    await page.reload();
    await expect(page.getByTestId("archive-read-only")).toHaveCount(0);
    const refusal = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/v1/archive/jobs"
        && response.request().method() === "POST");
    await startJob(page, "C:/vods/match.mp4");
    expect((await refusal).status()).toBe(403);
    const message = (await (await refusal).json()).error.message as string;
    expect(message).not.toBe("");
    await expect(page.getByRole("alert").filter({hasText: message})).toBeVisible();
});
