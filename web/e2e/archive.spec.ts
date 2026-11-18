import {expect, Page, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The Archive Clipper page (ADR-006 A1): /web/app/archive on the headless app,
// with the fake worker (e2e/fake_worker.py, set up by global-setup.ts through
// AUTONA_VODSPLIT_CMD), so the status read is the real one. The writes (start,
// cancel, resume, work folder, A4's review save and export, A5's results
// preparation, and A6's template saves, titles preview, thumbnails and their
// background) are the desktop shared view's (SHELL routes: 403 to a
// browser), and there is no desktop here to give the shell's secret, so they
// and the job reads are stubbed with page.route (as assets.spec.ts does) to
// cover the UI flow -- the shell probe (GET /api/v1/shell-session) too where
// a scenario writes, else the page knows it is a browser and only reads; the
// real write routes are only checked to answer 403 (the real export with the
// fake worker is tests/test_archive_jobs.py's, the real preparation
// tests/test_archive_results.py's). Checked through text and the DOM, never
// screenshots.
//
// The worker's install (ADR-006 A7): GET /api/v1/archive/worker is stubbed
// for every test (the real one checks the releases on GitHub from the
// server: never from a test run; tests/test_archive_worker_install.py reads
// a local fake release server, tests/test_web_operations.py the real route
// offline). The install flow stubs the status (no worker), the snapshots and
// the install's write (SHELL: its real 403 is checked below). The nav item
// always shows since A7 (no status read).

let pageErrors: string[] = [];

// A worker snapshot (core/ArchiveWorkerInstaller.py's): the latest release
// and the worker the status probed, no install yet.
function workerSnapshot(extra: object = {}) {
    return {
        installed: {available: true, version: "0.0.0-fake"},
        env_command: true,
        latest: {version: "0.3.0", size: 152950633,
                 published_at: "2026-11-17T00:00:00Z"},
        check: {status: "done", error: null, message: null, checked_at: 1},
        update_available: false,
        job: {revision: 0, status: "idle", stage: null, done: 0, total: 0,
              version: null, error: null, message: null},
        ...extra,
    };
}

const isWorkerRead = (url: URL) => url.pathname === "/api/v1/archive/worker";

test.beforeEach(async ({page}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(`${error}`));
    await keepLocal(page);
    // Registered first: a test's own route for it runs before this one.
    await page.route(isWorkerRead, (route) =>
        route.fulfill(envelope(workerSnapshot())));
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

test("the nav shows the archive item", async ({page}) => {
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
    // A6: the templates' writes, the titles' preview (it starts the worker)
    // and the thumbnails (the desktop's renderer), and their background.
    expect((await request.put(`${base}/templates/default`,
                              {data: {template: {}}})).status()).toBe(403);
    expect((await request.delete(`${base}/templates/default`)).status())
        .toBe(403);
    expect((await request.post(`${base}/templates/default:duplicate`,
                               {data: {}})).status()).toBe(403);
    expect((await request.post(`${base}/jobs/${JOB_ID}/titles:preview`,
                               {data: {template: {}}})).status()).toBe(403);
    expect((await request.post(`${base}/jobs/${JOB_ID}/thumbs:generate`,
                               {data: {}})).status()).toBe(403);
    expect((await request.patch(`${base}/settings`,
                                {data: {thumbnail_background: "settings"}}))
        .status()).toBe(403);
    // A7: the worker's install and its cancel.
    expect((await request.post(`${base}/worker:install`,
                               {data: {version: "0.3.0"}})).status()).toBe(403);
    expect((await request.post(`${base}/worker:cancel`,
                               {data: {revision: 1}})).status()).toBe(403);
    // Reads stay open to this PC (the match log's days too, A5; the
    // templates and a thumbnail, A6: no job of that id, 404).
    expect((await request.get(`${base}/status`)).ok()).toBe(true);
    const days = await request.get(`${base}/match-logs`);
    expect(days.ok()).toBe(true);
    expect(Array.isArray((await days.json()).data.days)).toBe(true);
    expect((await request.get(`${base}/templates`)).ok()).toBe(true);
    expect((await request.get(`${base}/templates/default`)).ok()).toBe(true);
    expect((await request.get(`${base}/jobs/${JOB_ID}/thumbs/1`)).status())
        .toBe(404);
    // A6: a game's skins (no game here: none), the game asked.
    const skins = await request.get(`${base}/characters?game=ssbu`);
    expect(skins.ok()).toBe(true);
    expect((await skins.json()).data).toEqual({characters: {}});
    expect((await request.get(`${base}/characters`)).status()).toBe(400);

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

// The title templates (ADR-006 A6): the archive page's own view, from the
// job screen. The list is the real one (THIS_PC: the standard template, no
// file yet) and the status the real one (the fake worker says "titles");
// the save, the preview (SHELL routes: their 403 is checked above) and the
// background's PATCH are stubbed, as the job is -- whose thumbnail (A6)
// shows in its set list.
test("a template is edited, previewed with the job's sets and saved; the job's thumbnail shows",
     async ({page, request}) => {
    const base = serverUrl("/api/v1/archive");
    const real = (await (await request.get(`${base}/templates`)).json()).data;
    expect(real.templates).toEqual([expect.objectContaining(
        {id: "default", stored: false, role: "default"})]);
    expect((await (await request.get(`${base}/status`)).json()).data.features)
        .toEqual(["titles"]);
    let store = real.templates;
    const original: string = store[0].template.title;
    const puts: any[] = [];
    const previews: any[] = [];
    const patches: any[] = [];
    await stubShell(page);
    await stubJobs(page, {posts: [], detail: () => ({
        job: job("done", {thumbs: {state: "done", done: 1, total: 1,
                                   failed: [], files: [1], revision: 2}}),
        sets: REVIEW_SETS})});
    // After stubJobs: these take their own paths first.
    await page.route("**/api/v1/archive/jobs/*/thumbs/1**", (route) =>
        route.fulfill({status: 200, contentType: "image/png", body: PNG}));
    await page.route("**/api/v1/archive/jobs/*/titles:preview", (route) => {
        const body = route.request().postDataJSON();
        previews.push(body);
        const winner = body.template.title.includes("{winner}") ? "Alpha " : "";
        return route.fulfill(envelope({template: body.template.name, sets: [
            {no: 1, title: `${winner}Fake Cup Winners Final Alpha vs Beta`,
             description: "Fake Cup\n\n0:00 Game 1\n0:30 Game 2\n1:00 Game 3",
             chapters: 3},
            {no: 2, title: null, description: null, chapters: 0}]}));
    });
    await page.route("**/api/v1/archive/templates**", (route) => {
        const asked = route.request();
        if (asked.method() === "PUT") {
            const body = asked.postDataJSON();
            puts.push(body);
            store = [{...store[0], stored: true, template: body.template}];
            return route.fulfill(envelope({template: store[0]}));
        }
        return route.fulfill(envelope({templates: store}));
    });
    await page.route("**/api/v1/archive/settings", (route) => {
        patches.push(route.request().postDataJSON());
        return route.fulfill(envelope({available: true, work_dir: "w",
                                       features: ["titles"],
                                       thumbnail_background: "settings"}));
    });
    await page.goto(serverUrl(`/web/app/archive?job=${JOB_ID}`));

    // The set's thumbnail, not its frame; the template the event takes.
    await expect(page.getByTestId("archive-thumb-1"))
        .toHaveJSProperty("naturalWidth", 1, {timeout: 15_000});
    await expect(page.getByTestId("archive-template-line"))
        .toContainText(store[0].template.name);

    await page.getByTestId("archive-templates-button").click();
    await expect(page.getByTestId("archive-template-default"))
        .toContainText("Used for this job");
    const title = page.getByTestId("archive-tpl-title");
    await expect(title).toHaveValue(original);
    // A variable put in at the caret.
    await title.click();
    await page.keyboard.press("Home");
    await page.getByTestId("archive-tpl-var-winner").click();
    await expect(title).toHaveValue(`{winner}${original}`);
    await expect(page.getByTestId("archive-template-default"))
        .toContainText("Unsaved");

    // The preview: asked with the draft a moment after the edit.
    await expect(page.getByTestId("archive-preview-title"))
        .toHaveText("Alpha Fake Cup Winners Final Alpha vs Beta");
    await expect(page.getByTestId("archive-preview-chapters"))
        .toHaveText("3 chapters");
    expect(previews[previews.length - 1].template.title)
        .toBe(`{winner}${original}`);

    await page.getByTestId("archive-tpl-save").click();
    await expect(page.getByTestId("archive-tpl-notice")).toHaveText("Saved");
    expect(puts).toHaveLength(1);
    expect(puts[0].template.title).toBe(`{winner}${original}`);
    await expect(page.getByTestId("archive-template-default"))
        .not.toContainText("Unsaved");
    await expect(page.getByTestId("archive-template-default"))
        .not.toContainText("(as installed)");

    // The thumbnail tab: the background of the job's thumbnails.
    await page.getByRole("tab", {name: "Thumbnail"}).click();
    await page.getByLabel("The thumbnail settings' background").check();
    await expect.poll(() => patches)
        .toEqual([{thumbnail_background: "settings"}]);

    await page.getByRole("button", {name: /Back to the jobs/}).click();
    await expect(page.getByTestId("archive-review")).toBeVisible();
});

// A character's colour on the set's thumbnail (ADR-006 A6): picked among the
// game's skins (GET /archive/characters: stubbed, the headless app has no
// game; its real answer is checked above), saved with the review and then
// drawn (thumbs:generate, the desktop's renderer: stubbed, its 403 is
// checked above).
test("a character's colour is picked, saved and then drawn on the thumbnail",
     async ({page}) => {
    const posts: string[] = [];
    const puts: any[] = [];
    const asked: string[] = [];
    let thumbs: object | null = null;
    let savedAtDraw: any = null;
    await stubShell(page);
    await stubJobs(page, {
        posts, puts,
        detail: () => ({job: job("done", {thumbs}), sets: {
            duration: 600, source_id: "abcDEF12345", sets: [
                {no: 1, start: 60, end: 180, matched: true,
                 title: "Winners Final", set: PREPARED.sets[0]},
                {no: 2, start: 200, end: 320, matched: true,
                 title: "Grand Final", set: PREPARED.sets[1]}]}}),
        afterPost: (action) => {
            if (action === "generate") {
                savedAtDraw = puts[puts.length - 1];
                thumbs = {state: "done", done: 1, total: 1, failed: [],
                          files: [1], revision: 1};
            }
        },
    });
    await page.route("**/api/v1/archive/characters**", (route) => {
        asked.push(new URL(route.request().url()).searchParams.get("game")!);
        return route.fulfill(envelope({characters: {
            mario: [{skin: 0, icon: "base_files/icon/chara_2_mario_00.png"},
                    {skin: 3, icon: "base_files/icon/chara_2_mario_03.png"}],
            luigi: [{skin: 0, icon: "base_files/icon/chara_2_luigi_00.png"}],
            zelda: [{skin: 0, icon: "base_files/icon/chara_2_zelda_00.png"}],
        }}));
    });
    await page.route("**/user_data/games/**", (route) =>
        route.fulfill({status: 200, contentType: "image/png", body: PNG}));
    await page.route("**/api/v1/archive/jobs/*/thumbs/1**", (route) =>
        route.fulfill({status: 200, contentType: "image/png", body: PNG}));
    await page.goto(serverUrl(`/web/app/archive?job=${JOB_ID}`));

    await page.getByTestId("archive-set-1").click({timeout: 15_000});
    await expect(page.getByTestId("archive-confirm")).toBeVisible();
    const select = page.getByTestId("archive-skin-c1-mario");
    await expect(select).toBeVisible();
    // Luigi has one colour: nothing to pick.
    await expect(page.getByTestId("archive-skin-c2-luigi")).toHaveCount(0);
    expect(asked).toEqual(["ssbu"]);
    await select.getByRole("combobox").click();
    await page.getByRole("option", {name: "2", exact: true}).click();
    // The list draws the colour's icon.
    await expect(page.getByTestId("archive-set-characters-1").locator("img").first())
        .toHaveAttribute("src", /\/base_files\/icon\/chara_2_mario_03\.png$/);

    // Confirmed, saved, and then drawn: the save goes first.
    await page.getByTestId("archive-confirm").click();
    await expect(page.getByTestId("archive-save-state")).toHaveText("Saved");
    expect(puts[puts.length - 1].sets["1"])
        .toEqual({confirmed: true, skins: {c1: {mario: 3}}});
    await page.getByTestId("archive-thumbs").click();
    await expect.poll(() => posts).toEqual(["generate"]);
    expect(savedAtDraw.sets["1"].skins).toEqual({c1: {mario: 3}});
    await expect(page.getByTestId("archive-thumb-1"))
        .toHaveJSProperty("naturalWidth", 1, {timeout: 15_000});
});

// The worker's install (ADR-006 A7): with no usable worker the page is the
// install card. The status (no worker, then the installed one once the
// install is done), the snapshots and the install's write are stubbed.
test("with no worker the page installs the latest release and opens", async ({page}) => {
    await stubShell(page);
    const MB = 1024 * 1024;
    const missing = {installed: {available: false, version: null},
                     env_command: false};
    const running = (extra: object) => ({revision: 1, status: "running",
        version: "0.3.0", error: null, message: null, done: 0,
        total: 152950633, stage: "download", ...extra});
    // Before the install, then each read after it in turn (the last one
    // repeats): the download, the new worker's check, done.
    const after = [
        workerSnapshot({...missing, job: running({done: 60 * MB})}),
        workerSnapshot({...missing, job: running({stage: "probe"})}),
        workerSnapshot({...missing, job: running({
            status: "done", stage: null,
            message: "切り抜きワーカー 0.3.0 を入れました。"})}),
    ];
    let started = false;
    let installed = false;
    const statusReads: string[] = [];
    await page.route((url) => url.pathname === "/api/v1/archive/status",
                     (route) => {
        statusReads.push(new URL(route.request().url()).search);
        return route.fulfill(envelope(installed
            ? {available: true, version: "0.3.0", format: 1,
               features: ["titles"], reason: null, work_dir: "C:/work",
               running_job: null, running_kind: null}
            : {available: false, version: null, format: null, features: [],
               reason: "切り抜きワーカー（vodsplit-worker.exe）が入っていません。",
               work_dir: "C:/work", running_job: null, running_kind: null}));
    });
    await page.route(isWorkerRead, (route) => {
        if (!started) {
            return route.fulfill(envelope(workerSnapshot(missing)));
        }
        const next = after.length > 1 ? after.shift() : after[0];
        installed = after.length === 1;
        return route.fulfill(envelope(next));
    });
    const bodies: unknown[] = [];
    await page.route("**/api/v1/archive/worker:install", (route) => {
        bodies.push(route.request().postDataJSON());
        started = true;
        return route.fulfill(envelope({job: running({})}, 202));
    });
    await page.route("**/api/v1/archive/jobs", (route) =>
        route.fulfill(envelope({jobs: []})));

    await page.goto(serverUrl("/web/app/archive"));
    const card = page.getByTestId("archive-worker-card");
    await expect(card).toContainText("Install the clip worker");
    await expect(card).toContainText("vodsplit-worker.exe");
    await expect(page.getByTestId("archive-worker-latest"))
        .toHaveText("Available: 0.3.0 (145.9 MB)");
    await page.getByTestId("archive-worker-install").click();
    await expect.poll(() => bodies).toEqual([{version: "0.3.0"}]);
    await expect(page.getByTestId("archive-worker-progress"))
        .toContainText("Downloading");
    // Done: the status is read again with a probe, and the page opens.
    await expect(page.getByTestId("archive-worker-version"))
        .toHaveText("Worker 0.3.0", {timeout: 10000});
    await expect(card).toHaveCount(0);
    await expect(page.getByRole("textbox", {name: /^Source video/})).toBeVisible();
    expect(statusReads).toContain("?refresh=true");
});

// A newer worker than the installed one: the header says so and its card
// updates it (the write stubbed).
test("a newer worker is offered from the header", async ({page}) => {
    await stubShell(page);
    await page.route(isWorkerRead, (route) => route.fulfill(envelope(
        workerSnapshot({update_available: true, env_command: false}))));
    const bodies: unknown[] = [];
    await page.route("**/api/v1/archive/worker:install", (route) => {
        bodies.push(route.request().postDataJSON());
        return route.fulfill(envelope({job: {revision: 1, status: "running",
            stage: "download", done: 0, total: 1, version: "0.3.0",
            error: null, message: null}}, 202));
    });
    await page.goto(serverUrl("/web/app/archive"));
    await expect(page.getByTestId("archive-worker-version"))
        .toHaveText("Worker 0.0.0-fake");
    await page.getByTestId("archive-worker-update").click();
    const card = page.getByTestId("archive-worker-card");
    await expect(card).toContainText("Update the clip worker");
    await expect(card).toContainText("Installed: 0.0.0-fake");
    await page.getByTestId("archive-worker-install").click();
    await expect.poll(() => bodies).toEqual([{version: "0.3.0"}]);
});
