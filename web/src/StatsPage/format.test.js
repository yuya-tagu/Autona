import {
    h2hRecord,
    historyCells,
    historySets,
    lastSetCells,
    lastSets,
    localDate,
    names,
    numbered,
    percent,
    recentSetCells,
    recentSets,
    upsetFactor,
} from "./format";

// Board 1's statistics in the Qt panel's words (the pre-S4k
// AutonaStatsWidget._updateDisplay), from score.1.* as published.

function board(p1, p2, extra = {}) {
    return {
        team: {
            "1": {player: {"1": {name: p1}}},
            "2": {player: {"1": {name: p2}}},
        },
        ...extra,
    };
}

test("the names: trimmed for the headings, as typed for the record", () => {
    expect(names(board(" Alpha ", "Bravo"))).toEqual({
        both: true, p1: "Alpha", p2: "Bravo",
        p1Record: " Alpha ", p2Record: "Bravo",
    });
    expect(names(board("  ", ""))).toEqual({
        both: false, p1: "P1", p2: "P2", p1Record: "  ", p2Record: "P2",
    });
    expect(names(undefined).both).toBe(false);
    expect(names(board("Alpha", 7)).p2).toBe("P2");
});

test("the upset factor: 0 until there is one", () => {
    expect(upsetFactor(board("A", "B"))).toBe("0");
    expect(upsetFactor(board("A", "B", {upset_factor: 3}))).toBe("3");
    expect(upsetFactor(board("A", "B", {upset_factor: null}))).toBe("");
});

test("rates round a half to the even number, as Python's %.0f", () => {
    expect(percent(1, 8)).toBe("12%");   // 12.5
    expect(percent(3, 8)).toBe("38%");   // 37.5
    expect(percent(5, 8)).toBe("62%");   // 62.5
    expect(percent(7, 8)).toBe("88%");   // 87.5
    expect(percent(1, 3)).toBe("33%");
    expect(percent(2, 3)).toBe("67%");
    expect(percent(1, 1)).toBe("100%");
    expect(percent(0, 4)).toBe("0%");
});

test("the head-to-head record counts winner 0 and 1 only", () => {
    const sets = [{winner: 0}, {winner: 1}, {winner: 1}, {winner: null},
                  {winner: 1}];
    expect(h2hRecord(sets)).toEqual({
        p1Wins: 1, p2Wins: 3, total: 4, p1Rate: "25%", p2Rate: "75%"});
    expect(h2hRecord([])).toEqual({
        p1Wins: 0, p2Wins: 0, total: 0, p1Rate: "-", p2Rate: "-"});
});

test("the recent sets are a list, or a dict's values", () => {
    expect(recentSets(board("A", "B", {recent_sets: {
        state: "done", sets: [{winner: 0}, 3, {winner: 1}]}})))
        .toEqual([{winner: 0}, {winner: 1}]);
    expect(recentSets(board("A", "B", {recent_sets: {
        sets: {"0": {winner: 1}}}}))).toEqual([{winner: 1}]);
    expect(recentSets(board("A", "B"))).toEqual([]);
});

test("a set's date is this computer's local day", () => {
    const seconds = 1700049600;
    const day = new Date(seconds * 1000);
    const expected = `${day.getFullYear()}/`
        + `${String(day.getMonth() + 1).padStart(2, "0")}/`
        + `${String(day.getDate()).padStart(2, "0")}`;
    expect(localDate(seconds)).toBe(expected);
    expect(localDate(0)).toBe("");
    expect(localDate(null)).toBe("");
    expect(localDate("1700049600")).toBe("");
    expect(localDate(Number.NaN)).toBe("");
    expect(localDate(1e20)).toBe("");
});

test("a recent set's cells", () => {
    const players = names(board("Alpha ", "Bravo"));
    expect(recentSetCells({
        timestamp: 0, tournament: "Cup", event: "Singles",
        round: "Winners Final", phase_name: "Top 8", score: [3, 1],
        winner: 0,
    }, players)).toEqual(["", "Cup\nSingles", "Winners Final", "3 - 1",
                          "Alpha "]);
    // No round: the phase. No score: 0 - 0. Neither winner: player 2's.
    expect(recentSetCells({tournament: null, event: "", phase_name: "Pools",
                           winner: null}, players))
        .toEqual(["", "", "Pools", "0 - 0", "Bravo"]);
    expect(recentSetCells({round: null, phase_name: null, score: null,
                           winner: 1}, players))
        .toEqual(["", "", "", "", "Bravo"]);
});

test("a player's entries in their number's order", () => {
    expect(numbered({"10": {n: 10}, "2": {n: 2}, "1": {n: 1}})
        .map((set) => set.n)).toEqual([1, 2, 10]);
    expect(numbered([{n: 1}, null, {n: 2}]).map((set) => set.n))
        .toEqual([1, 2]);
    expect(numbered(null)).toEqual([]);
    expect(lastSets(board("A", "B", {last_sets: {"2": {"1": {n: 1}}}}), "2"))
        .toEqual([{n: 1}]);
    expect(historySets(board("A", "B"), "1")).toEqual([]);
});

test("a last set's cells", () => {
    expect(lastSetCells({
        phase_name: "Pools", round_name: "Winners Round 1",
        oponent_team: "SP", oponent_name: "Bravo",
        player_score: 2, oponent_score: 1,
    })).toEqual(["Pools", "Winners Round 1", "SP\nBravo", "2 - 1"]);
    expect(lastSetCells({phase_name: null, oponent_name: "Bravo",
                         oponent_team: null}))
        .toEqual(["", "", "Bravo", "0 - 0"]);
    expect(lastSetCells({player_score: null, oponent_score: 0}))
        .toEqual(["", "", "", " - 0"]);
});

test("a placement's cells, the month as the state has it", () => {
    expect(historyCells({
        tournament_name: "Cup", event_name: "Singles", placement: 3,
        entrants: 64, event_date_year: "2026", event_date_month: "November",
        event_date_day: "04",
    })).toEqual(["Cup", "Singles", "3", "64", "2026/November/04"]);
    expect(historyCells({})).toEqual(["", "", "", "", "//"]);
});
