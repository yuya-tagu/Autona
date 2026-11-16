import React from "react";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import i18n from "../i18n/config";
import ArchivePage from "./index";

jest.mock("../api", () => ({
    apiGetJson: jest.fn(),
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
    apiPut: jest.fn(),
    apiErrorStatus: (error) => error?.status,
    ApiError: class ApiError extends Error {},
}));
// The shell probe's module (hostApi) imports the socket: never connected.
jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {},
                                on: () => {}, off: () => {}})},
}));
// eslint-disable-next-line import/first
import {apiGetJson, apiPost, apiPut} from "../api";

// The archive page's async guards (ADR-006 A1): a late answer for a job that
// is no longer selected is dropped, and polling stops once the job is not
// running. The api is mocked; the clock is real (the poll is 1 s).

jest.setTimeout(20000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// jsdom plays no media: the preview's pause / load (it lets go of its video
// on unmount) are spies for every test.
let pause;
let load;

beforeEach(() => {
    pause = jest.spyOn(window.HTMLMediaElement.prototype, "pause")
        .mockImplementation(() => undefined);
    load = jest.spyOn(window.HTMLMediaElement.prototype, "load")
        .mockImplementation(() => undefined);
    // A done job's review is saved as it changes (A4).
    apiPut.mockImplementation(() => Promise.resolve("{}"));
});

afterEach(() => {
    // Unmount while they are still spies.
    cleanup();
    pause.mockRestore();
    load.mockRestore();
    apiPut.mockReset();
});

function job(id, state, extra = {}) {
    return {id, created_at: `2026-11-16T10:0${id === "A" ? 1 : 2}:00`,
            source: `src-${id}`, state, ...extra};
}

const SETS_B = {sets: [{no: 7, start: 0, end: 120, matched: true}]};

let calls;
let deferredA;

function install({statusExtra = {}, handlers = {}}) {
    calls = [];
    apiGetJson.mockImplementation((path) => {
        calls.push(path);
        if (handlers[path]) {
            return handlers[path]();
        }
        if (path === "/api/v1/archive/status") {
            return Promise.resolve({data: {available: true, work_dir: "w",
                                           ...statusExtra}});
        }
        // The desktop's own window (its writes are SHELL routes).
        if (path === "/api/v1/shell-session") {
            return Promise.resolve({data: {ticket: "never-kept"}});
        }
        // A done job's review (A4): nothing changed yet.
        if (path.endsWith("/review")) {
            return Promise.resolve({data: {review: {format: 1, sets: {}}}});
        }
        return Promise.reject(new Error(`unexpected ${path}`));
    });
}

const forbidden = () => Promise.reject(
    Object.assign(new Error("forbidden"), {status: 403}));

const count = (path) => calls.filter((c) => c === path).length;

test("a late answer for the previous job is ignored and polling stays off", async () => {
    deferredA = {};
    const jobs = {data: {jobs: [job("A", "running"), job("B", "done")]}};
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(jobs),
            "/api/v1/archive/jobs/A": () => new Promise((resolve) => {
                deferredA.resolve = resolve;
            }),
            "/api/v1/archive/jobs/B": () => Promise.resolve(
                {data: {job: job("B", "done"), sets: SETS_B}}),
        },
    });
    render(<ArchivePage/>);

    // The running job is opened on its own (status.running_job).
    await waitFor(() => expect(deferredA.resolve).toBeDefined());

    // Switch to B while A's answer is still in flight.
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await within(await screen.findByRole("listbox"))
        .findByText(/src-B/));
    expect(await screen.findByTestId("archive-set-7")).toBeTruthy();

    // A's late answer says "running": it must not replace B's view ...
    await act(async () => {
        deferredA.resolve({data: {job: job("A", "running"), sets: null}});
        await sleep(50);
    });
    expect(screen.getByTestId("archive-set-7")).toBeTruthy();
    expect(screen.queryByText(i18n.t("archive_abort"))).toBeNull();
    // ... and must not start polling A.
    const before = count("/api/v1/archive/jobs/A");
    await act(async () => {
        await sleep(1500);
    });
    expect(count("/api/v1/archive/jobs/A")).toBe(before);
});

test("polling stops when the job is no longer running", async () => {
    let n = 0;
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [job("A", "running")]}}),
            "/api/v1/archive/jobs/A": () => {
                n += 1;
                return Promise.resolve({data: {
                    job: job("A", n >= 2 ? "done" : "running"), sets: null}});
            },
        },
    });
    render(<ArchivePage/>);
    await waitFor(() => expect(n).toBeGreaterThanOrEqual(2), {timeout: 5000});
    const settled = count("/api/v1/archive/jobs/A");
    await act(async () => {
        await sleep(1500);
    });
    expect(count("/api/v1/archive/jobs/A")).toBe(settled);
});

test("a failing poll stops polling and shows the error", async () => {
    let n = 0;
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [job("A", "running")]}}),
            "/api/v1/archive/jobs/A": () => {
                n += 1;
                return n === 1
                    ? Promise.resolve({data: {job: job("A", "running"),
                                              sets: null}})
                    : Promise.reject(new Error("gone"));
            },
        },
    });
    render(<ArchivePage/>);
    expect(await screen.findByText(i18n.t("archive_request_failed"),
                                   {}, {timeout: 5000})).toBeTruthy();
    const settled = n;
    await act(async () => {
        await sleep(1500);
    });
    expect(n).toBe(settled);
});

test("outside the desktop's window the page only reads", async () => {
    install({handlers: {
        "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: []}}),
        "/api/v1/shell-session": forbidden,
    }});
    render(<ArchivePage/>);
    expect((await screen.findByTestId("archive-read-only")).textContent)
        .toBe(i18n.t("archive_read_only"));
    fireEvent.change(screen.getByLabelText(i18n.t("archive_source")),
                     {target: {value: "https://youtu.be/x"}});
    expect(screen.getByRole("button", {name: i18n.t("archive_start")}))
        .toBeDisabled();
    expect(screen.getByRole("button", {name: i18n.t("archive_edit")}))
        .toBeDisabled();
});

test("in the desktop's window the writes are on", async () => {
    install({handlers: {
        "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: []}}),
    }});
    render(<ArchivePage/>);
    fireEvent.change(await screen.findByLabelText(i18n.t("archive_source")),
                     {target: {value: "https://youtu.be/x"}});
    await waitFor(() => expect(count("/api/v1/shell-session")).toBe(1));
    expect(screen.getByRole("button", {name: i18n.t("archive_start")}))
        .toBeEnabled();
    expect(screen.queryByTestId("archive-read-only")).toBeNull();
});

test("the closed players lane counts in the language's plural", () => {
    const en = i18n.getFixedT("en");
    const ja = i18n.getFixedT("ja");
    expect(en("archive_players_closed", {count: 1})).toMatch(/^1 player ·/);
    expect(en("archive_players_closed", {count: 3})).toMatch(/^3 players ·/);
    expect(ja("archive_players_closed", {count: 1})).toMatch(/^1人 ·/);
    expect(ja("archive_players_closed", {count: 3})).toMatch(/^3人 ·/);
});

test("every english key of the page has a japanese one", () => {
    const en = require("../i18n/locales/en.json");
    const ja = require("../i18n/locales/ja.json");
    // en has _one and _other, ja only _other (i18next plural suffixes).
    const base = (key) => key.replace(/_(one|other)$/, "");
    const bases = (dict) => [...new Set(Object.keys(dict)
        .filter((key) => key.startsWith("archive_")).map(base))].sort();
    for (const key of Object.keys(en).filter((k) => k.startsWith("archive_"))) {
        expect(ja[key] || ja[`${base(key)}_other`]).toBeTruthy();
    }
    expect(bases(ja)).toEqual(bases(en));
});

// The review screen (ADR-006 A3): a job with sets, opened by ?job=, plays
// its own video and answers the page's keys.
describe("the review screen", () => {
    const ID = "20261116-100000-abcd";
    const SETS = {
        duration: 3600, event: "Fake Cup", source_id: "x",
        games: [[100, 200], [300, 400], [500, 600]],
        games_ocr: [{names: {Alpha: 1}}, {names: {Gamma: 0.5}},
                    {names: {Alpha: 0.9}}],
        sets: [
            {no: 1, start: 90, end: 210, matched: true, title: "WR1 Alpha vs Beta",
             set: {round: "WR1", p1: "Alpha", s1: 1, p2: "Beta", s2: 0},
             games: 1, game_spans: [[100, 200]], thumb: "01_bg.jpg"},
            {no: 2, start: 290, end: 410, matched: false, needs_review: true,
             reason: "name mismatch", game_spans: [[300, 400]]},
            {no: 3, start: 490, end: 610, matched: true, title: "GF",
             game_spans: [[500, 600]], corrected_games: [1]},
        ],
    };
    let play;

    beforeEach(() => {
        window.history.pushState({}, "", `/web/archive?job=${ID}`);
        install({handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: [
                {id: ID, created_at: "2026-11-16T10:00:00", source: "C:/v.mp4",
                 state: "done"}]}}),
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                job: {id: ID, created_at: "2026-11-16T10:00:00",
                      source: "C:/v.mp4", state: "done"},
                sets: SETS}}),
        }});
        play = jest.spyOn(window.HTMLMediaElement.prototype, "play")
            .mockImplementation(() => Promise.resolve());
    });

    afterEach(() => {
        cleanup();
        play.mockRestore();
        delete global.fetch;
        window.history.pushState({}, "", "/");
    });

    const current = () => screen.getAllByTestId(/^archive-set-\d+$/)
        .filter((row) => row.getAttribute("aria-current") === "true")
        .map((row) => row.dataset.testid);

    test("plays the job's own video and moves between sets by key", async () => {
        render(<ArchivePage/>);
        const video = await screen.findByTestId("archive-video");
        expect(video.getAttribute("src"))
            .toMatch(new RegExp(`/api/v1/archive/jobs/${ID}/video$`));
        // Only a set with a frame asks for one.
        const frames = document.querySelectorAll("img");
        expect([...frames].map((img) => img.getAttribute("src")))
            .toEqual([expect.stringMatching(`/jobs/${ID}/frames/1$`)]);

        const review = screen.getByTestId("archive-review");
        fireEvent.keyDown(review, {key: "ArrowDown"});
        expect(current()).toEqual(["archive-set-1"]);
        expect(screen.getByTestId("archive-timecode").textContent)
            .toMatch(/^0:01:30/);
        expect(screen.getByTestId("archive-details").textContent)
            .toContain("Alpha vs Beta");

        fireEvent.keyDown(review, {key: "n"});
        expect(current()).toEqual(["archive-set-2"]);
        expect(screen.getByTestId("archive-details").textContent)
            .toContain("name mismatch");
        fireEvent.keyDown(review, {key: "N"});
        expect(current()).toEqual(["archive-set-2"]);
        fireEvent.keyDown(review, {key: "ArrowDown"});
        expect(current()).toEqual(["archive-set-3"]);
        fireEvent.keyDown(review, {key: "ArrowDown"});
        expect(current()).toEqual(["archive-set-3"]);
        fireEvent.keyDown(review, {key: "ArrowUp"});
        expect(current()).toEqual(["archive-set-2"]);

        fireEvent.keyDown(review, {key: " "});
        expect(play).toHaveBeenCalledTimes(1);
        // Space on a button presses it, and on a set row plays.
        fireEvent.keyDown(screen.getByRole("button", {
            name: i18n.t("archive_filter_all")}), {key: " "});
        expect(play).toHaveBeenCalledTimes(1);
        fireEvent.keyDown(screen.getByTestId("archive-set-1"), {key: " "});
        expect(play).toHaveBeenCalledTimes(2);
        // Not with a modifier (the review's I / O / Enter neither: A4).
        fireEvent.keyDown(review, {key: "ArrowDown", ctrlKey: true});
        fireEvent.keyDown(review, {key: "i", ctrlKey: true});
        fireEvent.keyDown(review, {key: "Enter", altKey: true});
        expect(current()).toEqual(["archive-set-2"]);
        expect(screen.getByTestId("archive-save-state").textContent).toBe("");

        // A time in the details seeks there.
        fireEvent.click(within(screen.getByTestId("archive-details"))
            .getByText("0:06:50"));
        expect(screen.getByTestId("archive-timecode").textContent)
            .toMatch(/^0:06:50/);
    });

    // The <video>'s MediaError, as the element reports it.
    const failWith = (video, code) => {
        Object.defineProperty(video, "error", {configurable: true,
                                               value: code === null ? null
                                                   : {code}});
        fireEvent.error(video);
    };

    test("an H.264 video offers this page in the browser", async () => {
        global.fetch = jest.fn(() => Promise.resolve({status: 200}));
        render(<ArchivePage/>);
        const video = await screen.findByTestId("archive-video");
        failWith(video, 4);
        const link = await screen.findByRole("link", {
            name: i18n.t("archive_video_open_in_browser")});
        expect(link.getAttribute("href").endsWith(`/web/archive?job=${ID}`))
            .toBe(true);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(global.fetch.mock.calls[0][0])
            .toMatch(new RegExp(`/jobs/${ID}/video$`));
        expect(global.fetch.mock.calls[0][1].method).toBe("HEAD");
        // Where it goes, the page only reads.
        expect(screen.getByTestId("archive-browser-note").textContent)
            .toBe(i18n.t("archive_browser_read_only"));
    });

    // [MediaError code, HEAD's answer, H.264 here, what the page says]
    test.each([
        [4, {status: 206}, false, "archive_video_open_in_browser_body"],
        [4, {status: 200}, true, "archive_video_unplayable"],
        [3, {status: 200}, false, "archive_video_unplayable"],
        [1, {status: 200}, false, "archive_video_unplayable"],
        [null, {status: 200}, false, "archive_video_unplayable"],
        [2, {status: 200}, false, "archive_video_unreachable"],
        [4, {status: 403}, false, "archive_video_unreachable"],
        [4, {status: 500}, false, "archive_video_unreachable"],
        [4, null, false, "archive_video_unreachable"],
        [3, {status: 404}, false, "archive_video_missing"],
    ])("MediaError %p with HEAD %p (H.264 %p) says %s",
       async (code, answer, h264, key) => {
        global.fetch = jest.fn(() => (answer === null
            ? Promise.reject(new TypeError("network"))
            : Promise.resolve(answer)));
        const canPlay = jest.spyOn(window.HTMLMediaElement.prototype,
                                   "canPlayType")
            .mockImplementation(() => (h264 ? "probably" : ""));
        try {
            render(<ArchivePage/>);
            failWith(await screen.findByTestId("archive-video"), code);
            expect((await screen.findByTestId("archive-video-problem"))
                .textContent).toContain(i18n.t(key));
            const link = screen.queryByRole("link", {
                name: i18n.t("archive_video_open_in_browser")});
            expect(link !== null)
                .toBe(key === "archive_video_open_in_browser_body");
        } finally {
            canPlay.mockRestore();
        }
    });

    // [the job's source, sets.json's source, the extension named]
    test.each([
        ["D:\\rec\\2026-11-16 20-00-00.FLV", "D:\\rec\\2026-11-16 20-00-00.FLV",
         ".flv"],
        ["https://youtu.be/abc", "C:\\w\\downloads\\abc.open.ts", ".ts"],
        ["D:\\rec\\vod.avi", undefined, ".avi"],
        ["https://youtu.be/abc", undefined, null],
    ])("a video of a format the preview does not serve (%s) is named, not missing",
       async (source, setsSource, extension) => {
        const job = {id: ID, created_at: "2026-11-16T10:00:00", source,
                     state: "done"};
        install({handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [job]}}),
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                job, sets: {...SETS, source: setsSource}}}),
        }});
        global.fetch = jest.fn(() => Promise.resolve({status: 415}));
        render(<ArchivePage/>);
        failWith(await screen.findByTestId("archive-video"), 4);
        const problem = await screen.findByTestId("archive-video-problem");
        expect(problem.textContent).toBe(extension === null
            ? i18n.t("archive_video_not_previewable_format")
            : i18n.t("archive_video_not_previewable", {ext: extension}));
        expect(problem.textContent).not.toContain(
            i18n.t("archive_video_missing"));
        // Nothing to open elsewhere: no browser plays it either.
        expect(screen.queryByRole("link", {
            name: i18n.t("archive_video_open_in_browser")})).toBeNull();
        // The sets and the timeline are still there to use.
        expect(screen.getByTestId("archive-set-1")).toBeTruthy();
        expect(screen.getByTestId("archive-timeline-sets")).toBeTruthy();
        fireEvent.keyDown(screen.getByTestId("archive-review"),
                          {key: "ArrowDown"});
        expect(current()).toEqual(["archive-set-1"]);
    });

    test("opening the job hands it the keys", async () => {
        render(<ArchivePage/>);
        const review = await screen.findByTestId("archive-review");
        expect(document.activeElement).toBe(review);
    });

    test("a frame that does not load falls back to the box", async () => {
        render(<ArchivePage/>);
        const frame = await screen.findByTestId("archive-frame-1");
        expect(screen.queryByTestId("archive-frame-none-1")).toBeNull();
        fireEvent.error(frame);
        expect(screen.queryByTestId("archive-frame-1")).toBeNull();
        expect(screen.getByTestId("archive-frame-none-1")).toBeTruthy();
        // The others never asked for one.
        expect(screen.getByTestId("archive-frame-none-2")).toBeTruthy();
    });

    const timelineSet = (no) => screen.getByTestId("archive-timeline-sets")
        .querySelector(`[data-timeline-set="${no}"] rect`);

    test("a click on the timeline picks the set there", async () => {
        render(<ArchivePage/>);
        await screen.findByTestId("archive-review");
        fireEvent.click(timelineSet(3));
        expect(current()).toEqual(["archive-set-3"]);
        expect(screen.getByTestId("archive-details").textContent)
            .toContain("#3");

        // A set the filter hides shows all the sets again.
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("archive_filter_review")}));
        expect(screen.queryByTestId("archive-set-1")).toBeNull();
        fireEvent.click(timelineSet(1));
        expect(current()).toEqual(["archive-set-1"]);
        expect(screen.getByRole("button", {
            name: i18n.t("archive_filter_all")}))
            .toHaveAttribute("aria-pressed", "true");
    });

    test("the set view shows no playhead outside the set", async () => {
        render(<ArchivePage/>);
        const review = await screen.findByTestId("archive-review");
        const video = screen.getByTestId("archive-video");
        const heads = () => screen.queryAllByTestId("archive-playhead").length;
        fireEvent.keyDown(review, {key: "ArrowDown"});
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("archive_timeline_set")}));
        expect(heads()).toBeGreaterThan(0);
        // The video plays on past the set (1000 s; the set is 90-210 s).
        Object.defineProperty(video, "currentTime", {configurable: true,
                                                     writable: true,
                                                     value: 1000});
        fireEvent.timeUpdate(video);
        expect(heads()).toBe(0);
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("archive_timeline_all")}));
        expect(heads()).toBeGreaterThan(0);
    });

    test("resuming lets go of the video and shows no sets while it runs",
         async () => {
        const done = {id: ID, created_at: "2026-11-16T10:00:00",
                      source: "C:/v.mp4", state: "cancelled"};
        install({handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [done]}}),
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve(
                {data: {job: done, sets: SETS}}),
        }});
        apiPost.mockImplementation(() => Promise.resolve(JSON.stringify(
            {data: {job: {...done, state: "running"}}})));
        try {
            render(<ArchivePage/>);
            const video = await screen.findByTestId("archive-video");
            expect(video.hasAttribute("src")).toBe(true);
            fireEvent.click(screen.getByRole("button", {
                name: i18n.t("archive_resume")}));
            await waitFor(() =>
                expect(screen.queryByTestId("archive-video")).toBeNull());
            expect(apiPost.mock.calls[0][0])
                .toBe(`/api/v1/archive/jobs/${ID}:resume`);
            expect(screen.queryByTestId("archive-review")).toBeNull();
            expect(pause).toHaveBeenCalled();
            expect(load).toHaveBeenCalled();
            expect(video.hasAttribute("src")).toBe(false);
        } finally {
            apiPost.mockReset();
        }
    });

    // The review (ADR-006 A4): in the desktop's window a done job's sets are
    // confirmed, given their IN / OUT and results, and saved as a whole once
    // the edits stop; the confirmed ones are exported.
    const JOB = {id: ID, created_at: "2026-11-16T10:00:00", source: "C:/v.mp4",
                 state: "done"};
    const installJob = (sets, handlers = {}) => install({handlers: {
        "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: [JOB]}}),
        [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve(
            {data: {job: JOB, sets}}),
        ...handlers,
    }});
    // The <video>'s position (jsdom plays nothing).
    const at = (video, seconds) => Object.defineProperty(
        video, "currentTime", {configurable: true, writable: true,
                               value: seconds});
    const puts = () => apiPut.mock.calls.map(([path, body]) => {
        expect(path).toBe(`/api/v1/archive/jobs/${ID}/review`);
        return body;
    });
    const saved = () => waitFor(() => expect(
        screen.getByTestId("archive-save-state").textContent)
        .toBe(i18n.t("archive_review_saved")), {timeout: 3000});
    // Open the job and pick set `no` (its row), where it can be edited.
    const open = async (no) => {
        render(<ArchivePage/>);
        const review = await screen.findByTestId("archive-review");
        fireEvent.click(screen.getByTestId(`archive-set-${no}`));
        await screen.findByTestId("archive-confirm");
        return review;
    };

    test("I / O set IN / OUT where the video is, Enter confirms and moves on",
         async () => {
        const review = await open(1);
        const video = screen.getByTestId("archive-video");
        const hints = screen.getByTestId("archive-key-hints").textContent;
        expect(hints).toMatch(/I.*O/);
        expect(hints).toContain("Enter");

        at(video, 100.456);
        fireEvent.keyDown(review, {key: "i"});
        at(video, 200.001);
        fireEvent.keyDown(review, {key: "O"});
        // What the worker found is still told.
        expect(screen.getByTestId("archive-detected-range")).toBeTruthy();
        fireEvent.keyDown(review, {key: "Enter"});
        expect(current()).toEqual(["archive-set-2"]);
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBe("true");
        expect(screen.getByTestId("archive-confirmed-count").textContent)
            .toBe(i18n.t("archive_export_count", {confirmed: 1, total: 3}));
        // Three edits, one save: the review as it ended.
        await saved();
        expect(puts()).toEqual([
            {sets: {1: {confirmed: true, start: 100.45, end: 200.01}}}]);

        // Enter on the selected row confirms it too, and the focus follows
        // to the next set (so the next Enter is for that one).
        const two = screen.getByTestId("archive-set-2");
        two.focus();
        fireEvent.keyDown(two, {key: "Enter"});
        expect(current()).toEqual(["archive-set-3"]);
        expect(document.activeElement)
            .toBe(screen.getByTestId("archive-set-3"));
        // On a row not selected it picks the row (and confirms nothing).
        fireEvent.keyDown(screen.getByTestId("archive-set-1"), {key: "Enter"});
        expect(current()).toEqual(["archive-set-1"]);
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBe("true");
        // The button takes the confirmation back, staying there.
        fireEvent.click(screen.getByTestId("archive-confirm"));
        expect(current()).toEqual(["archive-set-1"]);
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBeUndefined();
        await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(2),
                      {timeout: 3000});
        expect(puts()[1]).toEqual(
            {sets: {1: {start: 100.45, end: 200.01}, 2: {confirmed: true}}});
    });

    test("the details give another result and put IN / OUT back", async () => {
        const LF = {round: "LF", p1: "Gamma", s1: 3, p2: "Delta", s2: 0};
        installJob({...SETS, unused_sets: [LF]});
        const review = await open(2);
        fireEvent.mouseDown(within(screen.getByTestId("archive-assign"))
            .getByRole("combobox"));
        const options = within(await screen.findByRole("listbox"))
            .getAllByRole("option");
        expect(options.map((option) => option.textContent)).toEqual([
            i18n.t("archive_detail_assign_none"),
            `WR1: Alpha 1-0 Beta ${i18n.t("archive_detail_assign_taken",
                                          {no: 1})}`,
            "LF: Gamma 3-0 Delta",
        ]);
        // Set 1's result is set 1's: not to be given twice.
        expect(options[1]).toHaveAttribute("aria-disabled", "true");
        fireEvent.click(options[2]);
        expect(screen.getByTestId("archive-set-2").textContent)
            .toContain("LF Gamma vs Delta");
        expect(screen.getByTestId("archive-details").textContent)
            .toContain("Gamma vs Delta");

        at(screen.getByTestId("archive-video"), 300);
        fireEvent.keyDown(review, {key: "i"});
        expect(screen.getByTestId("archive-detected-range")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", {
            name: i18n.t("archive_detail_reset_range")}));
        expect(screen.queryByTestId("archive-detected-range")).toBeNull();
        await saved();
        expect(puts()).toEqual([{sets: {2: {set: LF}}}]);
    });

    test("a save that fails is shown and keeps the export from starting",
         async () => {
        apiPut.mockImplementation(() => Promise.reject(Object.assign(
            new Error("refused"), {status: 500, detail: "disk full"})));
        apiPost.mockImplementation(() => Promise.reject(new Error("no")));
        try {
            await open(1);
            fireEvent.click(screen.getByTestId("archive-confirm"));
            expect((await screen.findByTestId("archive-save-error", {},
                                              {timeout: 3000})).textContent)
                .toContain("disk full");
            // The export saves first: that fails again, and nothing is cut.
            fireEvent.click(screen.getByRole("button", {
                name: i18n.t("archive_export")}));
            await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(2));
            await act(async () => {
                await sleep(50);
            });
            expect(apiPost).not.toHaveBeenCalled();
        } finally {
            apiPost.mockReset();
        }
    });

    test("the confirmed sets are exported: saved first, then followed to the end",
         async () => {
        let exported = null;
        installJob(SETS, {
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                job: {...JOB, export: exported}, sets: SETS}}),
        });
        apiPost.mockImplementation(() => {
            exported = {state: "running", progress: {overall: 0.5}};
            return Promise.resolve(JSON.stringify(
                {data: {job: {...JOB, export: exported}}}));
        });
        try {
            const review = await open(1);
            const button = screen.getByRole("button", {
                name: i18n.t("archive_export")});
            // Nothing confirmed: nothing to export.
            expect(button).toBeDisabled();
            fireEvent.keyDown(review, {key: "Enter"});
            expect(button).toBeEnabled();
            // Not waiting for the save's delay: the export sends it first.
            fireEvent.click(button);
            expect(await screen.findByTestId("archive-export-progress"))
                .toBeTruthy();
            expect(apiPost.mock.calls[0][0])
                .toBe(`/api/v1/archive/jobs/${ID}:export`);
            expect(puts()).toEqual([{sets: {1: {confirmed: true}}}]);
            expect(apiPut.mock.invocationCallOrder[0])
                .toBeLessThan(apiPost.mock.invocationCallOrder[0]);
            expect(button).toBeDisabled();

            // The worker's count is every clip, the skipped ones too: the
            // page says how many were written.
            exported = {state: "done", clips: 3, skipped: 2,
                        out_dir: `C:/w/${ID}/clips`,
                        files: [{no: 1, file: "01_WR1_Alpha_vs_Beta.mp4",
                                 exported: true}]};
            const result = await screen.findByTestId("archive-export-result",
                                                     {}, {timeout: 5000});
            expect(result.textContent)
                .toContain(i18n.t("archive_export_done", {count: 1}));
            expect(result.textContent)
                .toContain(i18n.t("archive_export_skipped", {skipped: 2}));
            expect(screen.getByTestId("archive-export-dir").textContent)
                .toContain(`C:/w/${ID}/clips`);
            expect(result.textContent).toContain("01_WR1_Alpha_vs_Beta.mp4");
            expect(screen.queryByTestId("archive-export-progress")).toBeNull();
        } finally {
            apiPost.mockReset();
        }
    });

    test("Enter held down or with Shift confirms nothing; Shift+I / O move nothing",
         async () => {
        const review = await open(1);
        fireEvent.keyDown(review, {key: "Enter", repeat: true});
        fireEvent.keyDown(review, {key: "Enter", shiftKey: true});
        at(screen.getByTestId("archive-video"), 100);
        fireEvent.keyDown(review, {key: "I", shiftKey: true});
        fireEvent.keyDown(review, {key: "O", shiftKey: true});
        expect(current()).toEqual(["archive-set-1"]);
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBeUndefined();
        expect(screen.queryByTestId("archive-detected-range")).toBeNull();
        expect(screen.getByTestId("archive-save-state").textContent).toBe("");
    });

    test("I / O / Enter in the open list of results are the list's", async () => {
        const review = await open(1);
        at(screen.getByTestId("archive-video"), 100);
        fireEvent.mouseDown(within(screen.getByTestId("archive-assign"))
            .getByRole("combobox"));
        const listbox = await screen.findByRole("listbox");
        const option = within(listbox).getAllByRole("option")[0];
        for (const target of [option, listbox]) {
            fireEvent.keyDown(target, {key: "i"});
            fireEvent.keyDown(target, {key: "o"});
        }
        fireEvent.keyDown(listbox, {key: "Enter"});
        expect(screen.queryByTestId("archive-detected-range")).toBeNull();
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBeUndefined();
        expect(current()).toEqual(["archive-set-1"]);
        expect(review).toBeTruthy();
    });

    test("a key that leaves the selection where it was leaves the focus too",
         async () => {
        await open(3);
        const three = screen.getByTestId("archive-set-3");
        three.focus();
        // The last row: nothing below to pick.
        fireEvent.keyDown(three, {key: "ArrowDown"});
        expect(current()).toEqual(["archive-set-3"]);
        // A later pick by the mouse does not take the focus to its row.
        fireEvent.click(timelineSet(1));
        expect(current()).toEqual(["archive-set-1"]);
        expect(document.activeElement).toBe(three);
    });

    test("a review that cannot be read is told and not edited", async () => {
        installJob(SETS, {
            [`/api/v1/archive/jobs/${ID}/review`]: () => Promise.reject(
                Object.assign(new Error("conflict"), {
                    status: 409, detail: "レビュー（review.json）が読めません。"})),
        });
        render(<ArchivePage/>);
        await screen.findByTestId("archive-review");
        expect((await screen.findByText(/review\.json/)).textContent)
            .toContain(i18n.t("archive_review_load_failed",
                              {message: "レビュー（review.json）が読めません。"}));
        fireEvent.click(screen.getByTestId("archive-set-1"));
        expect(screen.queryByTestId("archive-confirm")).toBeNull();
        expect(screen.getByRole("button", {name: i18n.t("archive_export")}))
            .toBeDisabled();
        fireEvent.keyDown(screen.getByTestId("archive-review"), {key: "Enter"});
        expect(apiPut).not.toHaveBeenCalled();
    });

    // A second done job to switch to and back.
    const OTHER = "20261116-110000-efgh";
    const OTHER_JOB = {...JOB, id: OTHER, created_at: "2026-11-16T11:00:00",
                       source: "C:/w.mp4"};
    const installTwo = (reviews) => installJob(SETS, {
        "/api/v1/archive/jobs": () => Promise.resolve(
            {data: {jobs: [JOB, OTHER_JOB]}}),
        [`/api/v1/archive/jobs/${OTHER}`]: () => Promise.resolve(
            {data: {job: OTHER_JOB, sets: SETS}}),
        [`/api/v1/archive/jobs/${ID}/review`]: () => Promise.resolve(
            {data: {review: reviews.current}}),
    });
    const pickJob = async (pattern) => {
        fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
        fireEvent.click(await within(await screen.findByRole("listbox"))
            .findByText(pattern));
    };
    const reviewReads = () => count(`/api/v1/archive/jobs/${ID}/review`);

    test("a job opened again reads its review after the save its view left",
         async () => {
        const reviews = {current: {format: 1, sets: {}}};
        installTwo(reviews);
        let answer;
        apiPut.mockImplementation((path, body) => new Promise((resolve) => {
            answer = () => {
                reviews.current = {format: 1, ...body};
                resolve("{}");
            };
        }));
        const review = await open(1);
        expect(reviewReads()).toBe(1);
        fireEvent.keyDown(review, {key: "Enter"});
        // Left before the save's delay: the view sends it as it closes ...
        await pickJob(/w\.mp4/);
        await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
        // ... and the job opened again waits for it before reading.
        await pickJob(/v\.mp4/);
        await screen.findByTestId("archive-set-1");
        await act(async () => {
            await sleep(100);
        });
        expect(reviewReads()).toBe(1);
        await act(async () => {
            answer();
            await sleep(50);
        });
        await waitFor(() => expect(reviewReads()).toBe(2));
        await waitFor(() => expect(screen.getByTestId("archive-set-1")
            .dataset.confirmed).toBe("true"));
    });

    test("a view that closes sends a save that failed again", async () => {
        const reviews = {current: {format: 1, sets: {}}};
        installTwo(reviews);
        apiPut.mockImplementationOnce(() => Promise.reject(Object.assign(
            new Error("refused"), {status: 500, detail: "disk full"})));
        const review = await open(1);
        fireEvent.keyDown(review, {key: "Enter"});
        await screen.findByTestId("archive-save-error", {}, {timeout: 3000});
        await pickJob(/w\.mp4/);
        await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(2));
        expect(apiPut.mock.calls[1][1]).toEqual({sets: {1: {confirmed: true}}});
    });

    test("a save the server refuses shows its review again", async () => {
        const reviews = {current: {format: 1, sets: {3: {confirmed: true}}}};
        installTwo(reviews);
        apiPut.mockImplementation(() => Promise.reject(Object.assign(
            new Error("refused"), {status: 400, detail: "合いません"})));
        const review = await open(1);
        await waitFor(() => expect(screen.getByTestId("archive-set-3")
            .dataset.confirmed).toBe("true"));
        fireEvent.keyDown(review, {key: "Enter"});
        expect(screen.getByTestId("archive-set-1").dataset.confirmed)
            .toBe("true");
        expect((await screen.findByTestId("archive-save-error", {},
                                          {timeout: 3000})).textContent)
            .toContain("合いません");
        // Read again: the refused confirmation is gone, the server's stays.
        await waitFor(() => expect(screen.getByTestId("archive-set-1")
            .dataset.confirmed).toBeUndefined());
        expect(reviewReads()).toBe(2);
        expect(screen.getByTestId("archive-set-3").dataset.confirmed)
            .toBe("true");
        // Not sent again when the view closes.
        await pickJob(/w\.mp4/);
        await act(async () => {
            await sleep(50);
        });
        expect(apiPut).toHaveBeenCalledTimes(1);
    });

    test("leaving the page sends what is not saved yet", async () => {
        const review = await open(1);
        fireEvent.keyDown(review, {key: "Enter"});
        expect(apiPut).not.toHaveBeenCalled();
        act(() => {
            window.dispatchEvent(new Event("beforeunload"));
        });
        expect(apiPut).toHaveBeenCalledTimes(1);
        expect(apiPut.mock.calls[0][1]).toEqual({sets: {1: {confirmed: true}}});
        expect(apiPut.mock.calls[0][2]).toEqual({keepalive: true});
    });

    test("outside the desktop's window the review is only shown", async () => {
        installJob(SETS, {
            "/api/v1/shell-session": forbidden,
            [`/api/v1/archive/jobs/${ID}/review`]: () => Promise.resolve(
                {data: {review: {format: 1, sets: {1: {confirmed: true}}}}}),
        });
        render(<ArchivePage/>);
        const review = await screen.findByTestId("archive-review");
        await waitFor(() => expect(screen.getByTestId("archive-set-1")
            .dataset.confirmed).toBe("true"));
        await screen.findByTestId("archive-read-only");
        fireEvent.click(screen.getByTestId("archive-set-2"));
        expect(screen.queryByTestId("archive-confirm")).toBeNull();
        expect(screen.queryByTestId("archive-assign")).toBeNull();
        expect(screen.getByTestId("archive-key-hints").textContent)
            .not.toMatch(/Enter|\bI\b|\bO\b/);
        expect(screen.getByRole("button", {name: i18n.t("archive_export")}))
            .toBeDisabled();
        // Enter / I do nothing (nothing to save).
        fireEvent.keyDown(review, {key: "Enter"});
        fireEvent.keyDown(review, {key: "i"});
        expect(current()).toEqual(["archive-set-2"]);
        expect(screen.getByTestId("archive-save-state").textContent).toBe("");
        expect(screen.getByTestId("archive-confirmed-count").textContent)
            .toBe(i18n.t("archive_export_count", {confirmed: 1, total: 3}));
    });

    test("a video that is gone says so", async () => {
        global.fetch = jest.fn(() => Promise.resolve({status: 404}));
        render(<ArchivePage/>);
        fireEvent.error(await screen.findByTestId("archive-video"));
        expect(await screen.findByText(i18n.t("archive_video_missing")))
            .toBeTruthy();
        expect(screen.queryByRole("link", {
            name: i18n.t("archive_video_open_in_browser")})).toBeNull();
    });
});
