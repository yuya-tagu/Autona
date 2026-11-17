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

// ADR-006 A6: the templates' view lies over the job's, which stays as it
// was (hidden, not unmounted).
test("the new job's form is as it was after the templates' view", async () => {
    install({handlers: {
        "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: []}}),
        "/api/v1/archive/templates": () => Promise.resolve(
            {data: {templates: []}}),
    }});
    render(<ArchivePage/>);
    fireEvent.change(await screen.findByLabelText(i18n.t("archive_source")),
                     {target: {value: "https://youtu.be/x"}});
    fireEvent.click(screen.getByTestId("archive-templates-button"));
    expect(await screen.findByTestId("archive-templates")).toBeTruthy();
    expect(screen.getByTestId("archive-jobs-view")).not.toBeVisible();
    fireEvent.click(screen.getByTestId("archive-templates-button"));
    expect(screen.queryByTestId("archive-templates")).toBeNull();
    expect(screen.getByTestId("archive-jobs-view")).toBeVisible();
    expect(screen.getByLabelText(i18n.t("archive_source")).value)
        .toBe("https://youtu.be/x");
});

// The results a job is given (ADR-006 A5): prepared before the job starts
// (POST results:prepare, its own long timeout), summed up, then handed to
// the job's creation as they are.
describe("the results of a new job", () => {
    const PREPARE = "/api/v1/archive/results:prepare";
    const RESULTS = {event: "Fake Cup", sets: [
        {p1: "Kappa", s1: 3, p2: "Lambda", s2: 1, c1: "マリオ",
         char_source: "scoreboard",
         autona: {game: "tgame", c1: ["mario"], c2: []}}]};
    const SUMMARY = {sets: 3, game: "tgame", records: 2,
                     sources: {scoreboard: 1, report: 1, mains: 0, given: 0,
                               none: 1}};
    let posts;

    const answer = (summary = SUMMARY) => JSON.stringify(
        {data: {results: RESULTS, summary}});

    beforeEach(() => {
        posts = [];
        apiPost.mockImplementation((path, body, opts) => {
            posts.push([path, body, opts]);
            if (path === PREPARE) {
                return Promise.resolve(answer());
            }
            if (path === "/api/v1/archive/jobs") {
                return Promise.resolve(JSON.stringify(
                    {data: {job: job("J", "failed")}}));
            }
            return Promise.reject(new Error(`unexpected ${path}`));
        });
    });

    afterEach(() => {
        apiPost.mockReset();
    });

    const shell = (extra = {}) => install({handlers: {
        "/api/v1/archive/jobs": () => Promise.resolve({data: {jobs: []}}),
        "/api/v1/archive/jobs/J": () => Promise.resolve(
            {data: {job: job("J", "failed"), sets: null}}),
        "/api/v1/archive/match-logs": () => Promise.resolve({data: {days: [
            {date: "20261115", records: 3, tournaments: [
                {name: "Fake Cup", records: 2}, {name: "", records: 1}]},
            {date: "20261114", records: 1, tournaments: [
                {name: "Other Cup", records: 1}]},
        ]}}),
        ...extra,
    }});
    const startButton = () => screen.getByRole("button",
                                               {name: i18n.t("archive_start")});
    const typeSource = async () => {
        fireEvent.change(await screen.findByLabelText(i18n.t("archive_source")),
                         {target: {value: "https://youtu.be/x"}});
        await waitFor(() => expect(count("/api/v1/shell-session")).toBe(1));
    };

    test("start.gg's sets are fetched, summed up and given to the job",
         async () => {
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_startgg")));
        expect(startButton()).toBeDisabled();
        const fetch = screen.getByRole("button",
                                       {name: i18n.t("archive_results_fetch")});
        expect(fetch).toBeDisabled();
        fireEvent.change(screen.getByLabelText(
            i18n.t("archive_results_startgg_url")),
            {target: {value: " https://www.start.gg/tournament/x/event/y "}});
        fireEvent.click(fetch);

        const summary = await screen.findByTestId("archive-results-summary");
        expect(posts[0]).toEqual([PREPARE, {
            kind: "startgg", url: "https://www.start.gg/tournament/x/event/y"},
            {timeoutMs: 150000}]);
        expect(summary.textContent).toContain(
            i18n.t("archive_results_summary", {count: 3}));
        expect(summary.textContent).toContain(
            `${i18n.t("archive_char_source_scoreboard")} 1`);
        expect(summary.textContent).toContain(
            `${i18n.t("archive_char_source_none")} 1`);
        // A count of 0 is not said; nothing is estimated.
        expect(summary.textContent).not.toContain(
            i18n.t("archive_char_source_mains"));
        expect(summary.textContent).toContain(
            i18n.t("archive_results_records", {count: 2}));
        expect(startButton()).toBeEnabled();

        fireEvent.click(startButton());
        await waitFor(() => expect(posts).toHaveLength(2));
        expect(posts[1][0]).toBe("/api/v1/archive/jobs");
        expect(posts[1][1]).toEqual({source: "https://youtu.be/x",
                                     results: RESULTS, event: null});
    });

    test("another URL asks again: the earlier results are not the job's",
         async () => {
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_startgg")));
        const url = screen.getByLabelText(i18n.t("archive_results_startgg_url"));
        fireEvent.change(url, {target: {value: "https://www.start.gg/a"}});
        fireEvent.click(screen.getByRole("button",
                                         {name: i18n.t("archive_results_fetch")}));
        await screen.findByTestId("archive-results-summary");
        fireEvent.change(url, {target: {value: "https://www.start.gg/b"}});
        expect(screen.queryByTestId("archive-results-summary")).toBeNull();
        expect(startButton()).toBeDisabled();
    });

    test("a day of the match log is picked by its tournament", async () => {
        apiPost.mockImplementation((path, body, opts) => {
            posts.push([path, body, opts]);
            return Promise.resolve(answer(
                {...SUMMARY, sources: {scoreboard: 2, mains: 1}}));
        });
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_matchlog")));

        // The newest day, the whole day, at first.
        const date = await screen.findByLabelText(
            i18n.t("archive_results_matchlog_date"));
        expect(date.textContent).toBe("2026-11-15 (3)");
        const tournament = screen.getByLabelText(
            i18n.t("archive_results_matchlog_tournament"));
        expect(tournament.textContent).toBe(
            `${i18n.t("archive_results_matchlog_all")} (3)`);
        fireEvent.mouseDown(tournament);
        fireEvent.click(within(await screen.findByRole("listbox")).getByText(
            `${i18n.t("archive_results_matchlog_unnamed")} (1)`));
        fireEvent.click(screen.getByRole("button",
                                         {name: i18n.t("archive_results_load")}));

        const summary = await screen.findByTestId("archive-results-summary");
        expect(posts[0][1]).toEqual({kind: "matchlog", date: "20261115",
                                     tournament: ""});
        expect(summary.textContent).toContain(
            `${i18n.t("archive_char_source_mains")} 1`);
        expect(summary.textContent).toContain(
            i18n.t("archive_results_estimate_note"));
        // Not a start.gg event: no records to check against.
        expect(summary.textContent).not.toContain(
            i18n.t("archive_results_records", {count: 2}));

        // The whole of another day: null.
        fireEvent.mouseDown(date);
        fireEvent.click(within(await screen.findByRole("listbox")).getByText(
            "2026-11-14 (1)"));
        expect(screen.queryByTestId("archive-results-summary")).toBeNull();
        fireEvent.click(screen.getByRole("button",
                                         {name: i18n.t("archive_results_load")}));
        await screen.findByTestId("archive-results-summary");
        expect(posts[1][1]).toEqual({kind: "matchlog", date: "20261114",
                                     tournament: null});
    });

    test("an empty match log says so", async () => {
        shell({"/api/v1/archive/match-logs": () => Promise.resolve(
            {data: {days: []}})});
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_matchlog")));
        expect((await screen.findByTestId("archive-matchlog-empty")).textContent)
            .toBe(i18n.t("archive_results_matchlog_empty"));
        expect(startButton()).toBeDisabled();
    });

    const pick = (text) => fireEvent.change(
        screen.getByLabelText(i18n.t("archive_results_pick")),
        {target: {files: [{text: () => Promise.resolve(text)}]}});

    test("a JSON file is prepared as it is picked", async () => {
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        pick(JSON.stringify({sets: [{p1: "A", s1: 1, p2: "B", s2: 0}]}));

        await screen.findByTestId("archive-results-summary");
        expect(posts[0].slice(0, 2)).toEqual([PREPARE, {kind: "json",
            results: {sets: [{p1: "A", s1: 1, p2: "B", s2: 0}]}}]);
        expect(startButton()).toBeEnabled();
    });

    // A file read while the form moves on (8): another file, another kind.
    const deferredFile = () => {
        const file = {};
        file.text = () => new Promise((resolve) => {
            file.release = resolve;
        });
        return file;
    };
    const pickFile = (file) => fireEvent.change(
        screen.getByLabelText(i18n.t("archive_results_pick")),
        {target: {files: [file]}});
    const GOOD = JSON.stringify({sets: [{p1: "A", s1: 1, p2: "B", s2: 0}]});
    const parseError = () => {
        const said = i18n.t("archive_results_parse_error", {message: "@"})
            .split("@")[0];
        return screen.queryByText((text) => text.startsWith(said));
    };

    test("a file still being read when another is picked is dropped",
         async () => {
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        const first = deferredFile();
        pickFile(first);
        pick(GOOD);
        await screen.findByTestId("archive-results-summary");

        // The first file's late text: neither its error nor its results.
        await act(async () => {
            first.release("not json");
            await sleep(50);
        });
        expect(posts).toHaveLength(1);
        expect(parseError()).toBeNull();
        expect(screen.getByTestId("archive-results-summary")).toBeTruthy();
    });

    test("a file still being read when another kind is chosen is dropped",
         async () => {
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        const good = deferredFile();
        pickFile(good);
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_startgg")));
        await act(async () => {
            good.release(GOOD);
            await sleep(50);
        });
        expect(posts).toEqual([]);
        expect(screen.queryByTestId("archive-results-summary")).toBeNull();

        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        const bad = deferredFile();
        pickFile(bad);
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_none")));
        await act(async () => {
            bad.release("not json");
            await sleep(50);
        });
        expect(parseError()).toBeNull();
    });

    // The match log's days (9): only the latest list asked is shown.
    test("an earlier list of days answering late is dropped", async () => {
        const lists = [];
        shell({"/api/v1/archive/match-logs": () => new Promise((resolve) => {
            lists.push(resolve);
        })});
        const day = (date) => ({data: {days: [
            {date, records: 1, tournaments: [{name: "Fake Cup", records: 1}]}]}});
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_matchlog")));
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_matchlog")));
        await waitFor(() => expect(lists).toHaveLength(2));

        await act(async () => {
            lists[1](day("20261115"));
            await sleep(20);
        });
        await act(async () => {
            lists[0](day("20261101"));
            await sleep(50);
        });
        expect(screen.getByLabelText(i18n.t("archive_results_matchlog_date"))
            .textContent).toBe("2026-11-15 (1)");
    });

    test("a list of days answering after the page is gone sets nothing",
         async () => {
        const lists = [];
        shell({"/api/v1/archive/match-logs": () => new Promise((resolve) => {
            lists.push(resolve);
        })});
        const errors = jest.spyOn(console, "error");
        try {
            const {unmount} = render(<ArchivePage/>);
            await typeSource();
            fireEvent.click(screen.getByLabelText(
                i18n.t("archive_results_matchlog")));
            await waitFor(() => expect(lists).toHaveLength(1));
            unmount();
            await act(async () => {
                lists[0]({data: {days: []}});
                await sleep(50);
            });
            expect(errors).not.toHaveBeenCalled();
        } finally {
            errors.mockRestore();
        }
    });

    test("results the server refuses say why and start nothing", async () => {
        apiPost.mockImplementation((path, body, opts) => {
            posts.push([path, body, opts]);
            return Promise.reject(Object.assign(new Error("refused"), {
                status: 400, detail: "結果データにセットが1件もありません。"}));
        });
        shell();
        render(<ArchivePage/>);
        await typeSource();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        pick(JSON.stringify({sets: []}));

        expect(await screen.findByText("結果データにセットが1件もありません。"))
            .toBeTruthy();
        expect(screen.queryByTestId("archive-results-summary")).toBeNull();
        expect(startButton()).toBeDisabled();
    });

    test("outside the desktop's window nothing is prepared", async () => {
        shell({"/api/v1/shell-session": forbidden});
        render(<ArchivePage/>);
        await screen.findByTestId("archive-read-only");
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_startgg")));
        fireEvent.change(screen.getByLabelText(
            i18n.t("archive_results_startgg_url")),
            {target: {value: "https://www.start.gg/a"}});
        expect(screen.getByRole("button", {name: i18n.t("archive_results_fetch")}))
            .toBeDisabled();
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_json")));
        pick(JSON.stringify({sets: [{p1: "A", s1: 1, p2: "B", s2: 0}]}));
        await act(async () => {
            await sleep(50);
        });
        expect(posts).toEqual([]);
        // The match log is this PC's read: it is listed.
        fireEvent.click(screen.getByLabelText(i18n.t("archive_results_matchlog")));
        await screen.findByLabelText(i18n.t("archive_results_matchlog_date"));
        expect(screen.getByRole("button", {name: i18n.t("archive_results_load")}))
            .toBeDisabled();
    });
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

    // The characters of a set (ADR-006 A5): the icons by their codenames
    // (Autona's key in the result set), 推定 for a player's main.
    test("a set's characters show as icons, an estimate as such", async () => {
        const characters = {...SETS, sets: [
            {...SETS.sets[0], set: {...SETS.sets[0].set, c1: "マリオ/Bowser",
                                    c2: "ルイージ", char_source: "scoreboard",
                                    // Bowser has no icon file.
                                    autona: {game: "tgame",
                                             c1: ["mario", "koopa"],
                                             c2: ["luigi"], icons: {
                                                 mario: "base_files/icon/m_0.png",
                                                 luigi: "base_files/icon/l_0.png",
                                             }}}},
            SETS.sets[1],
            {...SETS.sets[2], set: {round: "GF", p1: "Gamma", s1: 3,
                                    p2: "Delta", s2: 0, c1: "Zelda",
                                    char_source: "mains",
                                    autona: {game: "tgame",
                                             c1: ["zelda", "peach", "../x"],
                                             c2: [], icons: {
                                                 zelda: "base_files/icon/z_0.png",
                                                 peach: "../../p.png",
                                                 "../x": "base_files/icon/x.png",
                                             }}}},
        ]};
        installJob(characters);
        render(<ArchivePage/>);

        // The files the prepared results name, in the game's folder.
        const first = await screen.findByTestId("archive-set-characters-1");
        expect([...first.querySelectorAll("img")].map(
            (img) => img.getAttribute("src"))).toEqual(
            ["m_0", "l_0"].map((file) => expect.stringMatching(
                `/user_data/games/tgame/base_files/icon/${file}\\.png$`)));
        expect(within(first).queryByTestId("archive-estimate")).toBeNull();
        expect(screen.queryByTestId("archive-set-characters-2")).toBeNull();
        const third = screen.getByTestId("archive-set-characters-3");
        // A name or a file that would leave the game's folder is no icon.
        expect(third.querySelectorAll("img")).toHaveLength(1);
        expect(within(third).getByTestId("archive-estimate").textContent)
            .toBe(i18n.t("archive_char_estimate"));
        // An icon the game's assets do not have is hidden.
        fireEvent.error(within(first).getByTestId("archive-character-mario"));
        expect(within(first).queryByTestId("archive-character-mario")).toBeNull();

        fireEvent.click(screen.getByTestId("archive-set-1"));
        const details = screen.getByTestId("archive-detail-characters");
        expect(details.textContent).toContain("マリオ/Bowser");
        expect(details.textContent).toContain("ルイージ");
        expect(within(details).queryByTestId("archive-estimate")).toBeNull();
        fireEvent.click(screen.getByTestId("archive-set-3"));
        const guessed = screen.getByTestId("archive-detail-characters");
        expect(guessed.textContent).toContain("Zelda");
        expect(guessed.textContent).toContain("—");
        expect(within(guessed).getByTestId("archive-estimate")).toBeTruthy();
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

    // ADR-006 A6: the confirmed sets' thumbnails, drawn on the desktop.
    test("the confirmed sets' thumbnails are made after the save, followed and shown",
         async () => {
        let thumbs = null;
        installJob(SETS, {
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                job: {...JOB, thumbs}, sets: SETS}}),
        });
        apiPost.mockImplementation(() => {
            thumbs = {state: "running", done: 0, total: 1, failed: [],
                      files: [], revision: 0};
            return Promise.resolve(JSON.stringify(
                {data: {job: {...JOB, thumbs}}}));
        });
        try {
            const review = await open(1);
            const button = screen.getByTestId("archive-thumbs");
            // Nothing confirmed: nothing to draw.
            expect(button).toBeDisabled();
            fireEvent.keyDown(review, {key: "Enter"});
            expect(button).toBeEnabled();
            fireEvent.click(button);
            expect(await screen.findByTestId("archive-thumbs-running"))
                .toBeTruthy();
            expect(apiPost.mock.calls[0][0])
                .toBe(`/api/v1/archive/jobs/${ID}/thumbs:generate`);
            expect(apiPut.mock.invocationCallOrder[0])
                .toBeLessThan(apiPost.mock.invocationCallOrder[0]);
            expect(button).toBeDisabled();

            // Followed (the poll) to the end; then shown in the list and
            // the details, the frame as a fallback.
            thumbs = {state: "done", done: 1, total: 1, failed: [],
                      files: [1], revision: 3};
            const result = await screen.findByTestId("archive-thumbs-result",
                                                     {}, {timeout: 5000});
            expect(result.textContent)
                .toBe(i18n.t("archive_thumbs_done", {count: 1}));
            const listed = screen.getByTestId("archive-thumb-1");
            expect(listed.getAttribute("src"))
                .toMatch(new RegExp(`/jobs/${ID}/thumbs/1\\?r=3$`));
            expect(screen.queryByTestId("archive-frame-1")).toBeNull();
            fireEvent.click(screen.getByTestId("archive-set-1"));
            expect(screen.getByTestId("archive-detail-thumb").getAttribute("src"))
                .toBe(listed.getAttribute("src"));
            fireEvent.click(screen.getByTestId("archive-set-2"));
            expect(screen.queryByTestId("archive-detail-thumb")).toBeNull();
            fireEvent.error(listed);
            expect(screen.getByTestId("archive-frame-1").getAttribute("src"))
                .toMatch(new RegExp(`/jobs/${ID}/frames/1$`));
        } finally {
            apiPost.mockReset();
        }
    });

    test("thumbnails that could not all be made say which", async () => {
        installJob(SETS, {
            [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                job: {...JOB, thumbs: {state: "done", done: 1, total: 3,
                                       failed: [2, 3], files: [1],
                                       revision: 1}},
                sets: SETS}}),
        });
        render(<ArchivePage/>);
        const result = await screen.findByTestId("archive-thumbs-result");
        expect(result.textContent).toContain(
            i18n.t("archive_thumbs_failed_sets", {sets: "#2, #3"}));
    });

    test("the job view names the template its event takes and opens the templates",
         async () => {
        const standard = {
            name: "標準", match: null, title: "{event} {round}", title_team: "",
            description: "", chapter_format: "{time} Game {n}",
            overflow: "drop_characters", round_map: {}, aliases: {},
            order: "result"};
        installJob({...SETS, template: "標準"}, {
            "/api/v1/archive/templates": () => Promise.resolve({data: {
                templates: [
                    {id: "20261117-100000-aaaa", stored: true, role: "match",
                     template: {...standard, name: "Fake 用", match: "Fake"}},
                    {id: "default", stored: false, role: "default",
                     template: standard}]}}),
        });
        render(<ArchivePage/>);
        await screen.findByTestId("archive-review");
        const line = await screen.findByTestId("archive-template-line");
        await waitFor(() => expect(line.textContent).toContain("Fake 用"));
        expect(line.textContent).toContain(
            i18n.t("archive_tpl_role_match", {match: "Fake"}));
        expect(screen.getByTestId("archive-analysis-template").textContent)
            .toBe(i18n.t("archive_tpl_analysis", {name: "標準"}));
        fireEvent.click(screen.getByTestId("archive-set-2"));
        expect(current()).toEqual(["archive-set-2"]);
        pause.mockClear();
        fireEvent.click(within(line).getByRole("button", {
            name: i18n.t("archive_tpl_edit")}));
        expect(await screen.findByTestId("archive-templates")).toBeTruthy();
        // The job's view is hidden, its video paused (it would be heard).
        expect(screen.getByTestId("archive-jobs-view")).not.toBeVisible();
        expect(pause).toHaveBeenCalled();
        // The job's template is the one open: edited, then back to the job.
        expect(screen.getByTestId("archive-tpl-name").value).toBe("Fake 用");
        fireEvent.change(screen.getByTestId("archive-tpl-name"),
                         {target: {value: "Fake 用 改"}});
        fireEvent.click(screen.getByTestId("archive-templates-button"));
        expect(screen.queryByTestId("archive-templates")).toBeNull();
        expect(screen.getByTestId("archive-jobs-view")).toBeVisible();
        // The set picked is still the one.
        expect(current()).toEqual(["archive-set-2"]);
        // The edit not saved is there when the templates are open again.
        fireEvent.click(screen.getByTestId("archive-templates-button"));
        expect((await screen.findByTestId("archive-tpl-name")).value)
            .toBe("Fake 用 改");
        expect(within(screen.getByTestId("archive-template-20261117-100000-aaaa"))
            .getByText(i18n.t("archive_tpl_unsaved"))).toBeTruthy();
    });

    // A YouTube job (D7): its video plays in YouTube's embedded player, its
    // frame spoken to over postMessage. jsdom loads nothing into the frame:
    // the test records what the page posts to it and plays the frame's part.
    describe("of a YouTube job", () => {
        const VIDEO = "abcDEF12_-9";
        const EMBED = "https://www.youtube-nocookie.com";
        const YT_JOB = {...JOB, source: `https://www.youtube.com/watch?v=${VIDEO}`};

        beforeEach(() => {
            install({handlers: {
                "/api/v1/archive/jobs": () => Promise.resolve(
                    {data: {jobs: [YT_JOB]}}),
                [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                    job: YT_JOB, sets: {...SETS, source_id: VIDEO}}}),
            }});
        });

        afterEach(() => {
            cleanup();
        });

        const PLAYING = 1;
        const PAUSED = 2;
        const timecode = () => screen.getByTestId("archive-timecode")
            .textContent;
        const watch = (s) => `https://www.youtube.com/watch?v=${VIDEO}&t=${s}s`;
        // The player's frame: the commands the page posts to it from now on,
        // and its voice.
        const frame = async () => {
            const host = await screen.findByTestId("archive-youtube");
            const iframe = await waitFor(() => {
                const found = host.querySelector("iframe");
                expect(found).not.toBeNull();
                return found;
            });
            const posts = jest.spyOn(iframe.contentWindow, "postMessage")
                .mockImplementation(() => undefined);
            const commands = () => posts.mock.calls.map(([data, target]) => {
                expect(target).toBe(EMBED);
                return JSON.parse(data);
            }).filter((message) => message.event === "command")
                .map((message) => [message.func, ...message.args]);
            const say = (message) => act(() => {
                window.dispatchEvent(new MessageEvent("message", {
                    data: JSON.stringify(message), origin: EMBED,
                    source: iframe.contentWindow}));
            });
            return {host, iframe, commands, say};
        };
        // The frame, once it said its player is ready.
        const ready = async () => {
            const played = await frame();
            played.say({event: "onReady", info: null});
            return played;
        };

        test("plays in YouTube's frame, driven by the page's keys", async () => {
            render(<ArchivePage/>);
            const review = await screen.findByTestId("archive-review");
            expect(screen.queryByTestId("archive-video")).toBeNull();
            const {iframe, commands, say} = await ready();
            expect(iframe.getAttribute("src")).toBe(
                `${EMBED}/embed/${VIDEO}?enablejsapi=1&origin=`
                + `${encodeURIComponent(window.location.origin)}`
                + "&playsinline=1&rel=0");
            expect(iframe.title).toBe(i18n.t("archive_preview"));
            // No script of YouTube's runs in the page.
            expect([...document.scripts].filter((script) => script.src))
                .toEqual([]);
            expect(commands()).toEqual([["addEventListener", "onStateChange"],
                                        ["addEventListener", "onError"]]);

            // A set picked is cued at its start, not played.
            fireEvent.keyDown(review, {key: "ArrowDown"});
            expect(current()).toEqual(["archive-set-1"]);
            expect(commands().at(-1)).toEqual(
                ["cueVideoById", {videoId: VIDEO, startSeconds: 90}]);
            expect(timecode()).toMatch(/^0:01:30/);
            expect(screen.getByTestId("archive-youtube-link")
                .getAttribute("href")).toBe(watch(90));

            // Space plays it; the frame delivers where it is.
            fireEvent.keyDown(review, {key: " "});
            expect(commands().at(-1)).toEqual(["playVideo"]);
            say({event: "onStateChange", info: PLAYING});
            say({event: "infoDelivery",
                 info: {currentTime: 95, duration: 3600, playerState: PLAYING}});
            expect(timecode()).toMatch(/^0:01:35/);
            say({event: "infoDelivery", info: {currentTime: 97.5}});
            expect(timecode()).toMatch(/^0:01:37/);
            fireEvent.keyDown(review, {key: " "});
            expect(commands().at(-1)).toEqual(["pauseVideo"]);
            say({event: "onStateChange", info: PAUSED});

            // Started, a set picked is sought.
            fireEvent.keyDown(review, {key: "n"});
            expect(current()).toEqual(["archive-set-2"]);
            expect(commands().at(-1)).toEqual(["seekTo", 290, true]);
            // The transport links to the video on YouTube where it is.
            const link = screen.getByTestId("archive-youtube-link");
            expect(link.textContent).toBe(i18n.t("archive_youtube_open_here"));
            expect(link.getAttribute("href")).toBe(watch(290));
            expect(link.getAttribute("target")).toBe("_blank");
            expect(link.getAttribute("rel")).toBe("noreferrer");
        });

        test("a set picked before the player is ready is where it starts",
             async () => {
            render(<ArchivePage/>);
            const review = await screen.findByTestId("archive-review");
            fireEvent.keyDown(review, {key: "ArrowDown"});
            const {commands} = await ready();
            expect(commands().at(-1)).toEqual(
                ["cueVideoById", {videoId: VIDEO, startSeconds: 90}]);
            expect(timecode()).toMatch(/^0:01:30/);
        });

        test("I / O take where the player is", async () => {
            render(<ArchivePage/>);
            const review = await screen.findByTestId("archive-review");
            const {say} = await ready();
            fireEvent.click(screen.getByTestId("archive-set-1"));
            await screen.findByTestId("archive-confirm");
            say({event: "infoDelivery",
                 info: {currentTime: 100.456, playerState: PLAYING}});
            say({event: "infoDelivery", info: {playerState: PAUSED}});
            fireEvent.keyDown(review, {key: "i"});
            say({event: "infoDelivery", info: {currentTime: 200.001}});
            fireEvent.keyDown(review, {key: "o"});
            fireEvent.keyDown(review, {key: "Enter"});
            expect(current()).toEqual(["archive-set-2"]);
            await saved();
            expect(puts()).toEqual([
                {sets: {1: {confirmed: true, start: 100.45, end: 200.01}}}]);
        });

        test("a click in the player gives the keys back to the page",
             async () => {
            render(<ArchivePage/>);
            await screen.findByTestId("archive-review");
            const {host, iframe, commands} = await ready();
            // window.focus(): jsdom has none (it says so on the console).
            const focusWindow = jest.spyOn(window, "focus")
                .mockImplementation(() => undefined);
            try {
                iframe.focus();
                expect(document.activeElement).toBe(iframe);
                window.dispatchEvent(new Event("blur"));
                await waitFor(() => expect(document.activeElement).toBe(host));
                expect(focusWindow).toHaveBeenCalled();
            } finally {
                focusWindow.mockRestore();
            }
            fireEvent.keyDown(host, {key: " "});
            expect(commands().at(-1)).toEqual(["playVideo"]);
        });

        test("a video YouTube will not embed plays as the downloaded file",
             async () => {
            render(<ArchivePage/>);
            const review = await screen.findByTestId("archive-review");
            const {say} = await ready();
            fireEvent.keyDown(review, {key: "ArrowDown"});
            say({event: "onError", info: 150});
            const video = await screen.findByTestId("archive-video");
            expect(video.getAttribute("src"))
                .toContain(`/archive/jobs/${ID}/video`);
            expect(screen.queryByTestId("archive-youtube")).toBeNull();
            expect(document.querySelector("iframe")).toBeNull();
            expect(screen.queryByTestId("archive-video-problem")).toBeNull();
            // The transport links to YouTube all the same, following the sets.
            const link = screen.getByTestId("archive-youtube-link");
            expect(link.getAttribute("href")).toBe(watch(90));
            fireEvent.keyDown(review, {key: "ArrowDown"});
            expect(current()).toEqual(["archive-set-2"]);
            expect(link.getAttribute("href")).toBe(watch(290));
        });

        test("a frame that errs before it is ready falls back the same",
             async () => {
            render(<ArchivePage/>);
            await screen.findByTestId("archive-review");
            const {say} = await frame();
            say({event: "onError", info: 153});
            expect(await screen.findByTestId("archive-video")).toBeTruthy();
            expect(screen.queryByTestId("archive-youtube")).toBeNull();
            expect(screen.getByTestId("archive-youtube-link")
                .getAttribute("href")).toBe(watch(0));
        });

        test("the file that cannot play here offers the browser and YouTube",
             async () => {
            global.fetch = jest.fn(() => Promise.resolve({status: 206}));
            const canPlay = jest.spyOn(window.HTMLMediaElement.prototype,
                                       "canPlayType")
                .mockImplementation(() => "");
            try {
                render(<ArchivePage/>);
                const review = await screen.findByTestId("archive-review");
                const {say} = await ready();
                fireEvent.keyDown(review, {key: "ArrowDown"});
                say({event: "onError", info: 150});
                const video = await screen.findByTestId("archive-video");
                Object.defineProperty(video, "error", {configurable: true,
                                                       value: {code: 4}});
                fireEvent.error(video);
                const problem = await screen.findByTestId(
                    "archive-video-problem");
                expect(problem.textContent)
                    .toContain(i18n.t("archive_video_open_in_browser_body"));
                expect(within(problem).getByRole("link", {
                    name: i18n.t("archive_video_open_in_browser")})).toBeTruthy();
                const open = within(problem).getByRole("link", {
                    name: i18n.t("archive_youtube_open")});
                expect(open.getAttribute("href")).toBe(watch(90));
                expect(open.getAttribute("target")).toBe("_blank");
                expect(open.getAttribute("rel")).toBe("noreferrer");
                expect(screen.getByTestId("archive-youtube-link")).toBeTruthy();
            } finally {
                canPlay.mockRestore();
            }
        });

        test("its own video is played when its id is not YouTube's", async () => {
            install({handlers: {
                "/api/v1/archive/jobs": () => Promise.resolve(
                    {data: {jobs: [YT_JOB]}}),
                [`/api/v1/archive/jobs/${ID}`]: () => Promise.resolve({data: {
                    job: YT_JOB, sets: {...SETS, source_id: "x"}}}),
            }});
            render(<ArchivePage/>);
            expect(await screen.findByTestId("archive-video")).toBeTruthy();
            expect(screen.queryByTestId("archive-youtube")).toBeNull();
            expect(screen.queryByTestId("archive-youtube-link")).toBeNull();
            expect(document.querySelector("iframe")).toBeNull();
        });
    });
});
