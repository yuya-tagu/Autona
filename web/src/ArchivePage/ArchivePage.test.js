import React from "react";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import i18n from "../i18n/config";
import ArchivePage from "./index";

jest.mock("../api", () => ({
    apiGetJson: jest.fn(),
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
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
import {apiGetJson, apiPost} from "../api";

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
});

afterEach(() => {
    // Unmount while they are still spies.
    cleanup();
    pause.mockRestore();
    load.mockRestore();
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
        // Not with a modifier; I / O / Enter do nothing here.
        fireEvent.keyDown(review, {key: "ArrowDown", ctrlKey: true});
        for (const key of ["i", "o", "Enter"]) {
            fireEvent.keyDown(review, {key});
        }
        expect(current()).toEqual(["archive-set-2"]);
        expect(screen.getByTestId("archive-key-hints").textContent)
            .not.toMatch(/Enter|\bI\b|\bO\b/);

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
