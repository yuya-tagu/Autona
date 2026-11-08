import {createNextState} from "@reduxjs/toolkit";
import {applyDeltas} from "./stateDelta";
import cases from "./stateDelta.cases.json";

// The rows of these cases are held to what StateManager publishes by
// tests/test_state_delta_cases.py: here, that they do what they say.

function under(key, value) {
    return key.split(".").reduceRight((inner, piece) => ({[piece]: inner}),
                                      JSON.parse(JSON.stringify(value)));
}

describe.each(cases.map((one) => [one.name, one]))("%s", (_name, one) => {
    const start = one.states[0];
    const end = one.states[one.states.length - 1];

    it("turns the first state into the last", () => {
        expect(applyDeltas(under(one.key, start), one.delta))
            .toEqual(under(one.key, end));
    });

    // What the store does: the rows are applied to an immer draft.
    it("does so on a draft", () => {
        const next = createNextState(under(one.key, start), (draft) => {
            applyDeltas(draft, one.delta);
        });
        expect(next).toEqual(under(one.key, end));
    });
});

// The overlays have their own copy (a plain script, it cannot import this one).
describe("the copy in layout/include/globals.js", () => {
    const source = require("fs").readFileSync(
        require("path").join(__dirname, "../../layout/include/globals.js"), "utf8");
    const body = /function applyDelta\(data, delta\) \{[\s\S]*?\r?\n\}\r?\n/.exec(source)[0];
    // eslint-disable-next-line no-new-func
    const overlayApply = new Function(`${body}; return applyDelta;`)();

    it.each(cases.map((one) => [one.name, one]))("%s", (_name, one) => {
        const data = under(one.key, one.states[0]);
        one.delta.forEach((row) => overlayApply(data, row));
        expect(data).toEqual(under(one.key, one.states[one.states.length - 1]));
    });
});

it("keeps what the rows do not touch, by the reference", () => {
    const untouched = {name: "kept"};
    const next = createNextState(
        {rows: {other: untouched, list: ["A", "B"]}},
        (draft) => {
            applyDeltas(draft, [{path: ["rows", "list", 1],
                                 action: "iterable_item_removed", value: "B"}]);
        });
    expect(next.rows.list).toEqual(["A"]);
    expect(next.rows.other).toBe(untouched);
});

it("does not lengthen a list when the row of its removal comes twice", () => {
    const rows = [{path: ["list", 3], action: "iterable_item_removed", value: "D"},
                  {path: ["list", 2], action: "iterable_item_removed", value: "C"}];
    expect(applyDeltas({list: ["A", "B"]}, rows)).toEqual({list: ["A", "B"]});
});

it("says so when a removal finds something else than a list", () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    try {
        const rows = [{path: ["list", 1], action: "iterable_item_removed", value: "B"}];
        expect(applyDeltas({list: {0: "A"}}, rows)).toEqual({list: {0: "A"}});
        expect(warn).toHaveBeenCalledTimes(1);
    } finally {
        warn.mockRestore();
    }
});
