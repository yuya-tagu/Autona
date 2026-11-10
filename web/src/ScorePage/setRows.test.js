import cases from "./setRows.cases.json";
import {
    ALL_PHASES,
    NO_PHASE,
    TAKE_FIRST_ROW_WHEN_NONE_SELECTED,
    chosenRow,
    clock,
    defaultPhase,
    filterShown,
    isCompletePair,
    nextSort,
    phaseQuery,
    progressOf,
    setCells,
    setShown,
    sortShown,
    stationCells,
    stationShown,
    statusLine,
} from "./setRows";

// The SCORE page's set selection rules (ADR-005 S4ab): the rows as the Qt
// windows show them -- setRows.cases.json, which tests/test_set_selection.py
// checks against core's rows and the windows' tables -- and what the windows
// do with them.

const SET_ROWS = cases.sets.map((item) => item.row);
const STATION_ROWS = cases.stations.map((item) => item.row);

describe("the windows' columns, one table for the page, core and the windows", () => {
    test.each(cases.sets.map((item) => [item.why, item]))("set: %s", (_why, item) => {
        expect(setCells(item.row)).toEqual(item.cells);
        expect(isCompletePair(item.row)).toBe(item.complete);
    });

    test.each(cases.stations.map((item) => [item.why, item]))(
        "station: %s", (_why, item) => {
            expect(stationCells(item.row)).toEqual(item.cells);
        });
});

test("the search reaches every column and the set's id, in any case, as typed", () => {
    const shown = setShown(SET_ROWS);
    const ids = (filter) => filterShown(shown, filter).map((item) => item.row.id);

    // The id is the window's hidden column: searched, not shown.
    expect(ids("1006")).toEqual([1006]);
    expect(ids("alice")).toEqual([1005, 1007]);
    // What is shown is searched: 1008's members are not (singles there).
    expect(ids("BOB")).toEqual([1005]);
    // Not trimmed.
    expect(ids(" ()")).toEqual([1007]);
    expect(ids("プール")).toEqual([1011]);
    expect(filterShown(shown, "")).toBe(shown);

    const stations = stationShown(STATION_ROWS);
    expect(filterShown(stations, "CHAN").map((item) => item.row.id))
        .toEqual([77, 90]);
    expect(filterShown(stations, "78").map((item) => item.row.id)).toEqual([78]);
});

test("the order: as read until a header is clicked, then Qt's, ties as read", () => {
    const rows = [{id: 1, stream: "b"}, {id: 2, stream: "B"}, {id: 3, stream: "10"},
                  {id: 4, stream: "2"}, {id: 5, stream: "b"}];
    const shown = setShown(rows);
    const ids = (sort) => sortShown(shown, sort).map((item) => item.row.id);

    expect(sortShown(shown, null)).toBe(shown);
    // Case-sensitive, code unit by code unit: "10" < "2" < "B" < "b".
    expect(ids({column: 0, descending: false})).toEqual([3, 4, 2, 1, 5]);
    expect(ids({column: 0, descending: true})).toEqual([1, 5, 2, 4, 3]);

    // A header: its column ascending, the same column the other way round.
    expect(nextSort(null, 2)).toEqual({column: 2, descending: false});
    expect(nextSort({column: 2, descending: false}, 2))
        .toEqual({column: 2, descending: true});
    expect(nextSort({column: 2, descending: true}, 2))
        .toEqual({column: 2, descending: false});
    expect(nextSort({column: 2, descending: true}, 5))
        .toEqual({column: 5, descending: false});
});

test("OK / Enter take the row selected, else the first one shown (as the windows)", () => {
    const shown = setShown(SET_ROWS);
    const filtered = filterShown(shown, "alice");

    expect(TAKE_FIRST_ROW_WHEN_NONE_SELECTED).toBe(true);
    expect(chosenRow(shown, 3).row.id).toBe(1004);
    expect(chosenRow(shown, null).row.id).toBe(1001);
    // The selected row filtered out: the first one shown.
    expect(chosenRow(filtered, 0).row.id).toBe(1005);
    expect(chosenRow(sortShown(shown, {column: 5, descending: true}), null).row.id)
        .toBe(1011);
    expect(chosenRow([], null)).toBeNull();
});

test("the phase combo's default and what a fetch asks for", () => {
    const phases = [{id: 11, name: "Pools"}, {id: 12, name: "Top 8"}];

    expect(defaultPhase(phases, null)).toBe(NO_PHASE);
    expect(defaultPhase([phases[0]], null)).toBe("11");
    expect(defaultPhase(phases, {phase: 12, finished: false})).toBe("12");
    expect(defaultPhase(phases, {phase: ALL_PHASES})).toBe(ALL_PHASES);
    // A phase the rows were fetched for that is not there any more.
    expect(defaultPhase(phases, {phase: 99})).toBe(NO_PHASE);
    expect(defaultPhase([], {phase: ALL_PHASES})).toBe(NO_PHASE);

    // No phases: the whole event.
    expect(phaseQuery([], NO_PHASE)).toBeNull();
    // Phases and none chosen: choose one first.
    expect(phaseQuery(phases, NO_PHASE)).toBeUndefined();
    expect(phaseQuery(phases, "99")).toBeUndefined();
    expect(phaseQuery(phases, ALL_PHASES)).toBe(ALL_PHASES);
    // The row's id as it is.
    expect(phaseQuery(phases, "12")).toBe(12);
});

test("the progress: busy while the pages are not known, then the share done", () => {
    expect(progressOf(null)).toBeNull();
    expect(progressOf({status: "done", done: 3, total: 3})).toBeNull();
    expect(progressOf({status: "running", done: 0, total: 0})).toEqual({value: null});
    expect(progressOf({status: "running", done: 2, total: 8})).toEqual({value: 25});
});

test("the status line", () => {
    const t = (key, values) => (values ? `${key}${JSON.stringify(values)}` : key);
    const at = new Date(2026, 10, 10, 9, 5).getTime() / 1000;
    const list = {url: "https://start.gg/x", rows: 11, fetchedAt: at};

    expect(clock(at)).toBe("09:05");
    expect(clock(null)).toBe("");

    expect(statusLine(null, null, null, t)).toBe("score_sets_idle");
    expect(statusLine({status: "idle"}, {url: "", rows: 0, fetchedAt: null},
                      null, t)).toBe("score_sets_idle");
    // Running: said over everything else, its pages once known.
    expect(statusLine({status: "running", total: 0}, list, "told", t))
        .toBe("score_sets_fetching");
    expect(statusLine({status: "running", done: 2, total: 8}, list, null, t))
        .toBe('score_sets_fetching_pages{"done":2,"total":8}');
    expect(statusLine({status: "done"}, list, "told", t)).toBe("told");
    expect(statusLine({status: "error"}, list, null, t)).toBe("score_sets_failed");
    expect(statusLine({status: "done", failed: 0}, list, null, t))
        .toBe('score_sets_count{"n":11,"at":"09:05"}');
    expect(statusLine({status: "done", failed: 2}, list, null, t))
        .toBe('score_sets_count{"n":11,"at":"09:05"} score_sets_failed_pages{"n":2}');
    // Rows kept from before (a fetch stopped, the same tournament again).
    expect(statusLine({status: "idle"}, list, null, t))
        .toBe('score_sets_count{"n":11,"at":"09:05"}');
});
