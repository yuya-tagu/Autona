import table from "./analysisRows.cases.json";
import i18n from "../i18n/config";
import {
    COLUMNS,
    DEFAULT_SORT,
    compareKeys,
    filterRows,
    progressLine,
    rowCells,
    sortRows,
} from "./analysisRows";

// The Statistics page's match list against the cases tests/
// test_analysis_rows.py checks against core and the Qt panel's recording
// (ADR-005 S4k): core's rows, as GET /api/v1/analysis gives them, become
// the cells the Qt panel showed, in its default order, and the status line
// it showed once the fetch was over.

const ja = i18n.getFixedT("ja");
const inJapanese = (key, values) => ja(key, values);

test.each(table.cases.map((c) => [c.scenario, c]))(
    "the cells, in the default order: %s", (_scenario, c) => {
        expect(sortRows(c.rows.map(rowCells), DEFAULT_SORT)).toEqual(c.cells);
    });

test.each(table.cases.map((c) => [c.scenario, c]))(
    "the status line: %s", (_scenario, c) => {
        expect(progressLine(c.progress, c.cells.length, inJapanese))
            .toBe(c.line);
    });

test("the cases hold every kind of cell and line", () => {
    const cells = table.cases.flatMap((c) => c.cells);
    const texts = (column) => cells.map((row) => row[column][0]);
    expect(texts(2).concat(texts(4))).toContain("TBD");
    expect(texts(2).concat(texts(4)).some((name) => /^\(seed \d+\)$/.test(name)))
        .toBe(true);
    expect(texts(1)).toContain("-");
    expect(texts(3)).toContain("-");
    expect(texts(6).some((upset) => upset.startsWith("-"))).toBe(true);
    expect(texts(7).some(Boolean)).toBe(true);
    expect(texts(8).some(Boolean)).toBe(true);
    const lines = table.cases.map((c) => c.line);
    expect(lines.some((line) => line.includes("件取得失敗"))).toBe(true);
    expect(lines).toContain("取得に失敗しました");
    expect(lines).toContain(ja("stats_analysis_no_phases"));
    // Core's rows come in another order than the table's.
    expect(table.cases.some((c) => c.rows.length > 1
        && JSON.stringify(c.rows.map(rowCells)) !== JSON.stringify(c.cells)))
        .toBe(true);
});

test("the columns are the Qt panel's", () => {
    expect(COLUMNS).toEqual(["Round", "P1 Seed", "P1 Name", "Score",
                             "P2 Name", "P2 Seed", "UpSet", "LR", "SeedOver"]);
});

test("keys compare as Python's do", () => {
    expect(compareKeys(["21", -4, 0], ["21", -3, 0])).toBeLessThan(0);
    expect(compareKeys(["50", 1, 0], ["21", 9, 9])).toBeGreaterThan(0);
    expect(compareKeys(["21", 1], ["21", 1, 0])).toBeLessThan(0);
    expect(compareKeys(10, 9)).toBeGreaterThan(0);
    expect(compareKeys("b", "a")).toBeGreaterThan(0);
    expect(compareKeys(["21", 2, 0], ["21", 2, 0])).toBe(0);
});

function row(upset, name, seed = 1) {
    return rowCells({round: "R", roundOrder: ["1", 1, upset],
                     p1: {seed, name}, p2: {seed: null, name: ""},
                     score: null, upset, losersRun: 0, seedOver: null});
}

test("numbers sort as numbers, both ways; equal keys keep their order", () => {
    const rows = [row(-1, "a"), row(10, "b"), row(2, "c"), row(-4, "d"),
                  row(null, "e"), row(2, "f")];
    const upsets = (sorted) => sorted.map((cells) => cells[6][0]);
    expect(upsets(sortRows(rows, {column: 6, descending: false})))
        .toEqual(["-4", "-1", "", "2", "2", "10"]);
    expect(upsets(sortRows(rows, {column: 6, descending: true})))
        .toEqual(["10", "2", "2", "", "-1", "-4"]);
    // The two 2s: "c" before "f" either way.
    expect(sortRows(rows, {column: 6, descending: true})
        .map((cells) => cells[2][0])).toEqual(["b", "c", "f", "e", "a", "d"]);
    // An unknown seed sorts last.
    expect(sortRows([row(0, "x", null), row(0, "y", 3)],
                    {column: 1, descending: false})
        .map((cells) => cells[1][0])).toEqual(["3", "-"]);
});

test("names sort by their lower case", () => {
    const rows = [row(0, "bravo"), row(0, "Alpha"), row(0, "charlie")];
    expect(sortRows(rows, {column: 2, descending: false})
        .map((cells) => cells[2][0])).toEqual(["Alpha", "bravo", "charlie"]);
});

test("names sort as Python sorts them: by their lower case's code points", () => {
    // Emoji (surrogate pairs) after full-width, as in Python -- not before,
    // where UTF-16's order would put them.
    const {names, ascending, descending} = table.nameSort;
    const rows = names.map((name) => row(0, name));
    const sorted = (down) => sortRows(rows, {column: 2, descending: down})
        .map((cells) => cells[2][0]);
    expect(sorted(false)).toEqual(ascending);
    expect(sorted(true)).toEqual(descending);
    expect(compareKeys("\u{1F98A}", "Ｚ")).toBeGreaterThan(0);
    expect(compareKeys("a\u{1F600}", "a\u{1F600}b")).toBeLessThan(0);
    expect(compareKeys("\u{1F600}b", "\u{1F600}a")).toBeGreaterThan(0);
    expect(compareKeys("\u{1F600}", "\u{1F600}")).toBe(0);
});

test("the filter looks in the round and both names, any case", () => {
    const rows = [
        rowCells({round: "Winners Final", roundOrder: ["1", 3, 0],
                  p1: {seed: 12, name: "Alpha"}, p2: {seed: 2, name: "Bravo"},
                  score: [2, 1], upset: 0, losersRun: 0, seedOver: null}),
        rowCells({round: "Losers Round 1", roundOrder: ["1", -1, 0],
                  p1: {seed: 3, name: "Charlie"}, p2: {seed: null, name: ""},
                  score: null, upset: null, losersRun: 0, seedOver: null}),
    ];
    const rounds = (filter) => filterRows(rows, filter)
        .map((cells) => cells[0][0]);
    expect(rounds("")).toEqual(["Winners Final", "Losers Round 1"]);
    expect(rounds("  BRAVO ")).toEqual(["Winners Final"]);
    expect(rounds("losers")).toEqual(["Losers Round 1"]);
    expect(rounds("tbd")).toEqual(["Losers Round 1"]);
    // Not the seeds or the score.
    expect(rounds("12")).toEqual([]);
    expect(rounds("2-1")).toEqual([]);
});

test("the status line before and during a fetch", () => {
    expect(progressLine(undefined, null, inJapanese))
        .toBe(ja("stats_analysis_idle"));
    expect(progressLine({status: "idle"}, 3, inJapanese))
        .toBe(ja("stats_analysis_idle"));
    expect(progressLine({status: "running", done: 0, total: 0}, null,
                        inJapanese)).toBe("フェーズ情報を取得中...");
    expect(progressLine({status: "running", done: 2, total: 5}, null,
                        inJapanese)).toBe("取得中 2/5");
    // Over, its rows not read (yet, or the read failed): over without the
    // count -- not its progress again.
    expect(progressLine({status: "done", done: 5, total: 5, failed: 0}, null,
                        inJapanese)).toBe("取得完了");
    expect(progressLine({status: "done", done: 5, total: 5, failed: 2}, null,
                        inJapanese)).toBe("取得完了 (2 件取得失敗)");
    expect(progressLine({status: "error", error: "fetch_failed"}, null,
                        inJapanese)).toBe("取得に失敗しました");
});
