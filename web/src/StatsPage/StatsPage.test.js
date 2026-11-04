import React from "react";
import {act, fireEvent, render, screen, within} from "@testing-library/react";
import {Provider} from "react-redux";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {PanelShellContext} from "../PanelShell/context";
import StatsPage from "./index";

// The Statistics panel in React (ADR-005 S4k): it shows board 1's
// statistics from the state (score.1.*, as published), asks for them
// through POST /api/v1/scoreboards/1/stats:refresh, and shows the
// tournament's match list: the fetch through POST /api/v1/analysis:refresh,
// its progress from the state (analysis.*), the rows from GET
// /api/v1/analysis, read when the page comes up and again when the fetch
// ends or the tournament changes (not when a fetch starts).

jest.setTimeout(20000);

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn(), on: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

const URL_ = "https://www.start.gg/tournament/autona-cup/event/singles";
const IDLE = {status: "idle", error: "", done: 0, total: 0, failed: 0,
              revision: 0};

let revision = 0;

function board(p1 = "", p2 = "", extra = {}) {
    return {
        team: {
            "1": {player: {"1": {name: p1, seed: 0}}},
            "2": {player: {"1": {name: p2, seed: 0}}},
        },
        ...extra,
    };
}

function setState(score1 = board(), analysis = IDLE) {
    revision += 1;
    act(() => {
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: {score: {"1": score1}, analysis},
            revision,
        }));
    });
}

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

// Write path suffix -> [status, the error's code] it answers instead.
let refuse;
// What GET /api/v1/analysis answers: its data, or a status to fail with.
let analysisData;
let analysisFails;
// Holds the writes' answers back while set (a promise the test resolves).
let writeGate;

function writes(path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "POST"
            && new URL(url).pathname === path).length;
}

function reads() {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === "GET"
            && new URL(url).pathname === "/api/v1/analysis").length;
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        for (let i = 0; i < 8; i++) {
            await Promise.resolve();
        }
    });
}

function renderPage() {
    return render(
        <Provider store={autonaStore}>
            <PanelShellContext.Provider
                value={{panel: "stats", desktop: false, language: "en"}}>
                <StatsPage/>
            </PanelShellContext.Provider>
        </Provider>,
    );
}

async function openTab(key) {
    fireEvent.click(screen.getByRole("tab", {name: i18n.t(key)}));
    await settle();
}

function row(round, order, p1, p2, extra = {}) {
    return {round, roundOrder: ["50", order, 0],
            p1: {seed: p1[0], name: p1[1]}, p2: {seed: p2[0], name: p2[1]},
            score: [2, 1], upset: 0, losersRun: 0, seedOver: null, ...extra};
}

const ROWS = [
    row("Winners Final", 3, [1, "Alpha"], [2, "Bravo"], {upset: 10}),
    row("Losers Round 1", -1, [3, "Charlie"], [null, ""],
        {score: null, upset: null}),
    row("Winners Round 1", 1, [4, "delta"], [5, ""], {upset: -2}),
];

function tableRows() {
    const tableEl = screen.getByRole("table",
                                     {name: i18n.t("stats_tab_analysis")});
    return within(tableEl).getAllByRole("row").slice(1)
        .map((tr) => within(tr).getAllByRole("cell")
            .map((cell) => cell.textContent));
}

function line() {
    return screen.getByRole("status").textContent;
}

beforeEach(() => {
    jest.useFakeTimers();
    refuse = {};
    analysisData = {status: "idle", url: "", rows: []};
    analysisFails = null;
    writeGate = null;
    if (typeof AbortSignal.timeout !== "function") {
        AbortSignal.timeout = () => undefined;
    }
    global.fetch = jest.fn(async (url, init) => {
        const method = init?.method ?? "GET";
        const path = new URL(url).pathname;
        if (method === "GET") {
            if (path === "/api/v1/analysis") {
                if (analysisFails) {
                    return answer(analysisFails, {ok: false});
                }
                return answer(200, {ok: true, data: analysisData,
                                    revision: 1});
            }
            return answer(404, {ok: false});
        }
        if (writeGate) {
            await writeGate;
        }
        const refused = Object.keys(refuse).find((s) => path.endsWith(s));
        if (refused) {
            const [status, code] = refuse[refused];
            return answer(status, {ok: false, error: {code, message: "no"}});
        }
        if (path === "/api/v1/analysis:refresh") {
            return answer(202, {ok: true, data: {
                accepted: true, operation: "analysis.refresh"}, revision: 1});
        }
        return answer(200, {ok: true, revision: 1});
    });
    setState();
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

// ── the statistics ───────────────────────────────────────────────────────

test("the page comes up with its four tabs and the pairing", async () => {
    renderPage();
    await openTab("stats_tab_recent");

    expect(screen.getByRole("button", {name: i18n.t("stats_refresh")}))
        .toBeEnabled();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent))
        .toEqual(["stats_tab_recent", "stats_tab_last", "stats_tab_history",
                  "stats_tab_analysis"].map((key) => i18n.t(key)));
    expect(screen.getByText("P1 vs P2")).toBeInTheDocument();
    expect(document.title).toBe(`Autona ${i18n.t("stats_title")}`);
    // Nothing is written by showing it.
    expect(global.fetch.mock.calls
        .filter(([, init]) => (init?.method ?? "GET") !== "GET")).toEqual([]);
});

test("the upset factor shows with both players named", async () => {
    renderPage();
    setState(board("Alpha", ""));
    expect(screen.getByText("Alpha vs P2")).toBeInTheDocument();

    setState(board("Alpha", "Bravo", {upset_factor: 3}));
    expect(screen.getByText(
        `${i18n.t("stats_upset_factor")}: 3 | Alpha vs Bravo`))
        .toBeInTheDocument();
});

test("the head-to-head: the record and the sets", async () => {
    renderPage();
    await openTab("stats_tab_recent");
    setState(board("Alpha", "Bravo", {recent_sets: {state: "done", sets: [
        {tournament: "Cup", event: "Singles", round: "Grand Final",
         score: [3, 1], winner: 0, timestamp: 0},
        {tournament: "Weekly", phase_name: "Pools", score: [0, 2],
         winner: 1},
        {tournament: "Weekly", round: "Winners Final", score: [2, 3],
         winner: 1},
    ]}}));

    expect(screen.getByText("Alpha 1W (33%) - 2W (67%) Bravo [3 sets]"))
        .toBeInTheDocument();
    const sets = screen.getByRole("table", {name: i18n.t("stats_tab_recent")});
    const rows = within(sets).getAllByRole("row").slice(1)
        .map((tr) => within(tr).getAllByRole("cell")
            .map((cell) => cell.textContent));
    expect(rows).toEqual([
        ["", "Cup\nSingles", "Grand Final", "3 - 1", "Alpha"],
        ["", "Weekly", "Pools", "0 - 2", "Bravo"],
        ["", "Weekly", "Winners Final", "2 - 3", "Bravo"],
    ]);
});

test("the last sets and the placements, one table per player", async () => {
    renderPage();
    setState(board("Alpha", "Bravo", {
        last_sets: {
            "1": {"2": {phase_name: "Pools", round_name: "R2",
                        oponent_name: "Zed", player_score: 2,
                        oponent_score: 0},
                  "1": {phase_name: "Pools", round_name: "R1",
                        oponent_team: "SP", oponent_name: "Yan",
                        player_score: 2, oponent_score: 1}},
            "2": {},
        },
        history_sets: {
            "2": {"1": {tournament_name: "Cup", event_name: "Singles",
                        placement: 3, entrants: 64,
                        event_date_year: "2026",
                        event_date_month: "November",
                        event_date_day: "04"}},
        },
    }));
    await openTab("stats_tab_last");

    const alpha = screen.getByRole("region", {name: "Alpha"});
    expect(within(alpha).getAllByRole("row").slice(1)
        .map((tr) => tr.textContent)).toEqual([
        "PoolsR1SP\nYan2 - 1",
        "PoolsR2Zed2 - 0",
    ]);
    expect(within(screen.getByRole("region", {name: "Bravo"}))
        .getByText(i18n.t("stats_empty"))).toBeInTheDocument();

    await openTab("stats_tab_history");
    expect(within(screen.getByRole("region", {name: "Bravo"}))
        .getAllByRole("cell").map((cell) => cell.textContent))
        .toEqual(["Cup", "Singles", "3", "64", "2026/November/04"]);
    expect(within(screen.getByRole("region", {name: "Alpha"}))
        .getByText(i18n.t("stats_empty"))).toBeInTheDocument();
});

test("the tab chosen is kept while the page is away", async () => {
    const view = renderPage();
    await openTab("stats_tab_history");
    view.unmount();

    renderPage();
    await settle();
    expect(screen.getByRole("tab", {name: i18n.t("stats_tab_history")}))
        .toHaveAttribute("aria-selected", "true");
    await openTab("stats_tab_recent");
});

test("fetching the statistics: one request at a time, told when started", async () => {
    renderPage();
    let release;
    writeGate = new Promise((resolve) => {
        release = resolve;
    });
    const button = screen.getByRole("button", {name: i18n.t("stats_refresh")});
    fireEvent.click(button);
    await settle();
    expect(button).toBeDisabled();
    expect(writes("/api/v1/scoreboards/1/stats:refresh")).toBe(1);

    release();
    await settle();
    expect(button).toBeEnabled();
    expect(await screen.findByText(i18n.t("stats_refresh_started")))
        .toBeInTheDocument();
});

test("a statistics request that fails is told", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    refuse["/stats:refresh"] = [500, "INTERNAL"];
    fireEvent.click(screen.getByRole("button", {name: i18n.t("stats_refresh")}));
    await settle();
    expect(await screen.findByText(i18n.t("request_failed")))
        .toBeInTheDocument();

    // Without the token (another computer): told once.
    refuse["/stats:refresh"] = [401, "UNAUTHENTICATED"];
    fireEvent.click(screen.getByRole("button", {name: i18n.t("stats_refresh")}));
    await settle();
    expect(await screen.findByText(i18n.t("stream_token_required")))
        .toBeInTheDocument();
    console.error.mockRestore();
});

// ── the match list ───────────────────────────────────────────────────────

test("the match list: fetched, followed, read when it is over", async () => {
    renderPage();
    await openTab("stats_tab_analysis");
    expect(reads()).toBe(1);
    expect(line()).toBe(i18n.t("stats_analysis_idle"));
    const fetchButton = screen.getByRole("button",
                                         {name: i18n.t("stats_analysis_fetch")});

    fireEvent.click(fetchButton);
    await settle();
    expect(writes("/api/v1/analysis:refresh")).toBe(1);

    // Core publishes the fetch's progress. Not read for it starting (core
    // keeps the rows of the fetch before until it is over), nor for its
    // progress.
    setState(board(), {...IDLE, status: "running", revision: 1});
    await settle();
    expect(fetchButton).toBeDisabled();
    expect(line()).toBe(i18n.t("stats_analysis_phases"));
    expect(reads()).toBe(1);
    setState(board(), {...IDLE, status: "running", total: 3, done: 1,
                       revision: 1});
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_progress",
                               {done: 1, total: 3}));
    expect(reads()).toBe(1);

    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 3, done: 3, failed: 1,
                       revision: 1});
    await settle();
    expect(reads()).toBe(2);
    expect(fetchButton).toBeEnabled();
    expect(line()).toBe(`${i18n.t("stats_analysis_done", {n: 3})} `
        + i18n.t("stats_analysis_failed_groups", {n: 1}));
    // The Round column's order: losers round 1, winners round 1, final.
    expect(tableRows()).toEqual([
        ["Losers Round 1", "3", "Charlie", "-", "TBD", "-", "", "", ""],
        ["Winners Round 1", "4", "delta", "2-1", "(seed 5)", "5", "-2", "",
         ""],
        ["Winners Final", "1", "Alpha", "2-1", "Bravo", "2", "10", "", ""],
    ]);
});

test("the match list sorts by a column and filters", async () => {
    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 4});
    renderPage();
    await openTab("stats_tab_analysis");
    const upsets = () => tableRows().map((cells) => cells[6]);

    fireEvent.click(screen.getByRole("button", {name: "UpSet"}));
    await settle();
    expect(upsets()).toEqual(["-2", "", "10"]);
    fireEvent.click(screen.getByRole("button", {name: "UpSet"}));
    await settle();
    expect(upsets()).toEqual(["10", "", "-2"]);

    fireEvent.change(screen.getByRole("searchbox",
                                      {name: i18n.t("stats_analysis_filter")}),
                     {target: {value: " ALPHA"}});
    await settle();
    expect(tableRows().map((cells) => cells[2])).toEqual(["Alpha"]);
    fireEvent.change(screen.getByRole("searchbox",
                                      {name: i18n.t("stats_analysis_filter")}),
                     {target: {value: "round 1"}});
    await settle();
    // Still in the column's order (UpSet, descending).
    expect(tableRows().map((cells) => cells[0]))
        .toEqual(["Losers Round 1", "Winners Round 1"]);
});

test("no tournament: told until the match list changes", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    await openTab("stats_tab_analysis");
    refuse["/analysis:refresh"] = [409, "NOT_READY"];
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("stats_analysis_fetch")}));
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_no_tournament"));

    // A tournament loaded: core forgets the match list (the next revision).
    setState(board(), {...IDLE, revision: 1});
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_idle"));
    console.error.mockRestore();
});

test("a fetch running already, and the errors, are told", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    await openTab("stats_tab_analysis");
    refuse["/analysis:refresh"] = [409, "CONFLICT"];
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("stats_analysis_fetch")}));
    await settle();
    expect(await screen.findByText(i18n.t("stats_analysis_running")))
        .toBeInTheDocument();

    setState(board(), {...IDLE, status: "error", error: "no_phases",
                       revision: 2});
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_no_phases"));
    setState(board(), {...IDLE, status: "error", error: "fetch_failed",
                       revision: 3});
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_failed"));
    console.error.mockRestore();
});

test("a failed fetch keeps the rows of the one before", async () => {
    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 1});
    renderPage();
    await openTab("stats_tab_analysis");
    expect(tableRows()).toHaveLength(3);

    // Core keeps them on an error: so does the page, whatever it reads.
    analysisData = {status: "error", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "error", error: "fetch_failed",
                       total: 1, done: 1, failed: 1, revision: 2});
    await settle();
    expect(tableRows()).toHaveLength(3);
    expect(line()).toBe(i18n.t("stats_analysis_failed"));

    // Another tournament: core drops them.
    analysisData = {status: "idle", url: "", rows: []};
    setState(board(), {...IDLE, revision: 3});
    await settle();
    expect(tableRows()).toHaveLength(0);
});

test("a fetch running keeps the rows read before it, not another tournament's", async () => {
    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 1});
    renderPage();
    await openTab("stats_tab_analysis");
    expect(reads()).toBe(1);
    expect(tableRows()).toHaveLength(3);

    // The next fetch: core's rows are the last fetch's until it is over.
    setState(board(), {...IDLE, status: "running", revision: 2});
    await settle();
    setState(board(), {...IDLE, status: "running", total: 4, done: 2,
                       revision: 2});
    await settle();
    expect(tableRows()).toHaveLength(3);
    expect(reads()).toBe(1);

    analysisData = {status: "done", url: URL_, rows: ROWS.slice(0, 2)};
    setState(board(), {...IDLE, status: "done", total: 4, done: 4,
                       revision: 2});
    await settle();
    expect(reads()).toBe(2);
    expect(tableRows()).toHaveLength(2);
    expect(line()).toBe(i18n.t("stats_analysis_done", {n: 2}));

    // A fetch of another tournament seen without the idle of the switch
    // between them (revision 3): the rows read before are not its.
    setState(board(), {...IDLE, status: "running", revision: 4});
    await settle();
    expect(tableRows()).toHaveLength(0);
    expect(reads()).toBe(2);
});

test("another tournament: no rows, even when the read fails or answers some", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 1});
    renderPage();
    await openTab("stats_tab_analysis");
    expect(tableRows()).toHaveLength(3);

    // Core went idle (Forget): the read for it fails.
    analysisFails = 400;
    setState(board(), {...IDLE, revision: 2});
    await settle();
    expect(reads()).toBe(2);
    expect(tableRows()).toHaveLength(0);
    expect(line()).toBe(i18n.t("stats_analysis_idle"));

    // Idle shows none, whatever is read.
    analysisFails = null;
    setState(board(), {...IDLE, revision: 3});
    await settle();
    expect(reads()).toBe(3);
    expect(tableRows()).toHaveLength(0);
    console.error.mockRestore();
});

test("a fetch over whose rows cannot be read shows none and no count", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    analysisData = {status: "done", url: URL_, rows: ROWS};
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 1});
    renderPage();
    await openTab("stats_tab_analysis");
    expect(tableRows()).toHaveLength(3);

    // An error keeps core's rows, but they could not be read again: not
    // the ones read before.
    analysisFails = 400;
    setState(board(), {...IDLE, status: "error", error: "fetch_failed",
                       total: 1, done: 1, failed: 1, revision: 2});
    await settle();
    expect(tableRows()).toHaveLength(0);
    expect(line()).toBe(i18n.t("stats_analysis_failed"));
    expect(screen.getByText(i18n.t("stats_analysis_read_failed")))
        .toBeInTheDocument();

    // Done, but not read: done without a count, not "n/n".
    setState(board(), {...IDLE, status: "done", total: 3, done: 3, failed: 1,
                       revision: 3});
    await settle();
    expect(tableRows()).toHaveLength(0);
    expect(line()).toBe(`${i18n.t("stats_analysis_fetched")} `
        + i18n.t("stats_analysis_failed_groups", {n: 1}));

    // Read again: the rows and their count.
    analysisFails = null;
    fireEvent.click(screen.getByRole("button", {name: i18n.t("stats_reload")}));
    await settle();
    expect(tableRows()).toHaveLength(3);
    expect(line()).toBe(`${i18n.t("stats_analysis_done", {n: 3})} `
        + i18n.t("stats_analysis_failed_groups", {n: 1}));
    console.error.mockRestore();
});

test("a match list that cannot be read is told, and read again", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    analysisFails = 400;
    setState(board(), {...IDLE, status: "done", total: 1, done: 1,
                       revision: 1});
    renderPage();
    await openTab("stats_tab_analysis");
    expect(screen.getByText(i18n.t("stats_analysis_read_failed")))
        .toBeInTheDocument();

    analysisFails = null;
    analysisData = {status: "done", url: URL_, rows: ROWS};
    fireEvent.click(screen.getByRole("button", {name: i18n.t("stats_reload")}));
    await settle();
    expect(screen.queryByText(i18n.t("stats_analysis_read_failed")))
        .not.toBeInTheDocument();
    expect(tableRows()).toHaveLength(3);
    console.error.mockRestore();
});

test("a fetch that cannot start says so", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    await openTab("stats_tab_analysis");
    refuse["/analysis:refresh"] = [500, "INTERNAL"];
    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("stats_analysis_fetch")}));
    await settle();
    expect(line()).toBe(i18n.t("stats_analysis_failed"));
    console.error.mockRestore();
});
