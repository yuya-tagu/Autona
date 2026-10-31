import cases from "./titleRule.cases.json";
import {titleIsAuto, typedTitleWins} from "./titleRule";

// The Stream page's title rule against the table core's rule is tested
// with too (tests/test_stream_service.py): the two never drift apart.

test.each(cases.titleIsAuto.map((c) => [c.why, c]))(
    "whether a title is auto: %s", (_why, c) => {
        expect(titleIsAuto(c.title, c.titleAuto)).toBe(c.auto);
    });

test.each(cases.typedTitleWins.map((c) => [c.why, c]))(
    "whether a typed title is written: %s", (_why, c) => {
        expect(typedTitleWins(c.typed, c.shown, c.title, c.titleAuto))
            .toBe(c.wins);
    });

test("the table has cases on both sides", () => {
    expect(cases.titleIsAuto.length).toBeGreaterThan(0);
    expect(cases.typedTitleWins.length).toBeGreaterThan(0);
});
