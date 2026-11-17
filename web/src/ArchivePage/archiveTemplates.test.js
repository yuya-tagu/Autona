import {
    aliasRows,
    forSaving,
    insertAt,
    roundKeys,
    sameTemplate,
    selectTemplate,
    textLength,
    withAliases,
    withRound,
} from "./archiveTemplates";

// The template helpers (ADR-006 A6): the selection mirrors vodsplit's
// templates.select over the server's order; the editor's pure parts.

const STANDARD = {
    name: "標準", match: null, title: "{event} {round} {p1}({c1}) vs {p2}({c2})",
    title_team: "", description: "{event}\n\n{chapters}",
    chapter_format: "{time} Game {n}", overflow: "drop_characters",
    round_map: {}, aliases: {}, order: "result",
};

const entry = (id, template, role = "match") => (
    {id, stored: true, role, template: {...STANDARD, ...template}});

describe("selectTemplate", () => {
    // In the server's order: the longer match first, then the plain ones.
    const entries = [
        entry("b", {name: "2on2", match: "雷影杯 2on2"}),
        entry("a", {name: "雷影杯", match: "雷影杯"}),
        entry("default", {}, "default"),
        entry("c", {name: "予備"}, "unused"),
    ];

    test("the first whose match is in the event name", () => {
        expect(selectTemplate(entries, "第5回 雷影杯 2on2").id).toBe("b");
        expect(selectTemplate(entries, "雷影杯 #12").id).toBe("a");
    });

    test("else the first without a match", () => {
        expect(selectTemplate(entries, "Other Cup").id).toBe("default");
        expect(selectTemplate(entries, "").id).toBe("default");
        expect(selectTemplate(entries, null).id).toBe("default");
    });

    test("an empty list has none", () => {
        expect(selectTemplate([], "x")).toBeNull();
    });

    test("a template shadowed by one of the same match is never taken", () => {
        const shadowed = [
            entry("a", {name: "先", match: "雷影杯"}),
            entry("s", {name: "後", match: "雷影杯"}, "shadowed"),
            entry("default", {}, "default"),
        ];
        expect(selectTemplate(shadowed, "雷影杯 #12").id).toBe("a");
    });
});

test("textLength counts code points, as the worker and YouTube do", () => {
    expect(textLength("ab")).toBe(2);
    expect(textLength("𠮷野家")).toBe(3);
    expect(textLength(null)).toBe(0);
});

test("insertAt puts the text over the selection and says where the caret goes", () => {
    expect(insertAt("ab cd", 2, 2, "{p1}")).toEqual({text: "ab{p1} cd", caret: 6});
    expect(insertAt("ab cd", 0, 2, "X")).toEqual({text: "X cd", caret: 1});
    // No caret: at the end.
    expect(insertAt("ab", null, null, "{n}")).toEqual({text: "ab{n}", caret: 5});
    // Out of range is clamped.
    expect(insertAt("ab", 9, 1, "!")).toEqual({text: "ab!", caret: 3});
});

test("sameTemplate ignores the order of keys and empty round names", () => {
    const a = {...STANDARD, aliases: {x: "1", y: "2"},
               round_map: {R: {ja: "あ"}}};
    const b = {...STANDARD, aliases: {y: "2", x: "1"},
               round_map: {R: {ja: "あ", short: ""}}};
    expect(sameTemplate(a, b)).toBe(true);
    expect(sameTemplate({...a, match: ""}, {...a, match: null})).toBe(true);
    expect(sameTemplate(a, {...b, title: "x"})).toBe(false);
});

test("forSaving leaves out a round with no name and an empty match", () => {
    const saved = forSaving({...STANDARD, match: "  ",
                             round_map: {A: {ja: "", short: ""},
                                         B: {ja: "ビー", short: ""}}});
    expect(saved.match).toBeNull();
    expect(saved.round_map).toEqual({B: {ja: "ビー"}});
    expect(forSaving({...STANDARD, match: " 杯 "}).match).toBe("杯");
});

test("roundKeys: the template's rounds, then the job's it does not name", () => {
    const template = {...STANDARD, round_map: {"Grand Final": {short: "GF"}}};
    const sets = {
        sets: [{no: 1, start: 0, end: 1, set: {round: "Winners Round 1"}},
               {no: 2, start: 0, end: 1, set: null},
               {no: 3, start: 0, end: 1, set: {round: "Grand Final"}}],
        unused_sets: [{round: "Losers Final"}, {round: " "}],
    };
    expect(roundKeys(template, sets))
        .toEqual(["Grand Final", "Winners Round 1", "Losers Final"]);
    expect(roundKeys(template, null)).toEqual(["Grand Final"]);
});

test("withRound sets a round's names and drops a round left with none", () => {
    const named = withRound(STANDARD, "WR1", {ja: "勝者側1回戦"});
    expect(named.round_map).toEqual({WR1: {ja: "勝者側1回戦"}});
    expect(withRound(named, "WR1", {ja: "", short: ""}).round_map).toEqual({});
});

test("the aliases from rows: a row with a side empty is not kept", () => {
    const template = {...STANDARD, aliases: {"らぎぴ/Ragipi": "らぎぴ"}};
    expect(aliasRows(template)).toEqual([["らぎぴ/Ragipi", "らぎぴ"]]);
    const next = withAliases(template, [["らぎぴ/Ragipi", "らぎぴ"],
                                        ["Mario", ""], ["", "x"],
                                        ["Luigi", "ルイージ"]]);
    expect(next.aliases).toEqual({"らぎぴ/Ragipi": "らぎぴ", Luigi: "ルイージ"});
});
