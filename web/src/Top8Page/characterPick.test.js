import table from "./characterPick.cases.json";
import {characterPick, sameValue} from "./characterPick";

// The Top 8 page's character pick against the table core's
// Top8Service.CharacterPick is tested with too (tests/test_top8_service.py):
// the page shows what core would pick, for every shape a slot holds.

function charactersFor(c) {
    if (!c.reversed) {
        return table.characters;
    }
    return Object.fromEntries(Object.entries(table.characters).reverse());
}

test.each(table.cases.map((c) => [c.why, c]))("the pick: %s", (_why, c) => {
    expect(characterPick(c.value, charactersFor(c))).toEqual(c.pick);
});

test("the table has every kind of case", () => {
    const whys = table.cases.map((c) => c.why);
    expect(whys.some((why) => why.startsWith("the page's pick"))).toBe(true);
    expect(whys.some((why) => why.startsWith("an import"))).toBe(true);
    expect(whys.some((why) => why.includes("before S4h"))).toBe(true);
    expect(table.cases.some((c) => c.reversed)).toBe(true);
    expect(table.cases.some((c) => c.pick[0] === null)).toBe(true);
});

test("values compare as Python's == does", () => {
    expect(sameValue({a: 1, b: [1, {c: 2}]}, {b: [1, {c: 2}], a: 1})).toBe(true);
    expect(sameValue({a: 1}, {a: 1, b: undefined})).toBe(false);
    expect(sameValue([1, 2], [2, 1])).toBe(false);
    expect(sameValue(null, {})).toBe(false);
    expect(sameValue(100, 100.0)).toBe(true);
});
