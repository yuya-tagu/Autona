import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import {ECHO_GRACE_MS} from "./useStateField";
import {useStateChoice} from "./useStateChoice";

// A choice control's echo rule (ADR-005 S4b): the choice shows at once and
// stays until the state changes, the send fails, or no echo comes within
// ECHO_GRACE_MS of its answer.

function Choice({value, send}) {
    const [checked, choose] = useStateChoice(value, send);
    return (
        <input
            type="checkbox"
            aria-label="shown"
            checked={checked}
            onChange={(event) => choose(event.target.checked)}
        />
    );
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((ok, fail) => {
        resolve = ok;
        reject = fail;
    });
    return {promise, resolve, reject};
}

function setup(value, send) {
    const utils = render(<Choice value={value} send={send}/>);
    const box = screen.getByLabelText("shown");
    const setState = (next) => utils.rerender(<Choice value={next} send={send}/>);
    return {...utils, box, setState};
}

async function settle(ms = 0) {
    await act(async () => {
        jest.advanceTimersByTime(ms);
        await Promise.resolve();
        await Promise.resolve();
    });
}

beforeEach(() => {
    jest.useFakeTimers();
});

afterEach(() => {
    jest.useRealTimers();
});

test("the choice shows at once and is sent once", () => {
    const send = jest.fn(() => new Promise(() => {}));
    const {box} = setup(false, send);

    fireEvent.click(box);

    expect(box).toBeChecked();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(true);
});

test("the echo takes over from the choice", async () => {
    const answer = deferred();
    const {box, setState} = setup(false, () => answer.promise);

    fireEvent.click(box);
    await act(async () => answer.resolve());
    setState(true);
    await settle(ECHO_GRACE_MS * 2);

    expect(box).toBeChecked();
});

test("a refused send puts the state back", async () => {
    const answer = deferred();
    const {box} = setup(false, () => answer.promise);

    fireEvent.click(box);
    expect(box).toBeChecked();
    await act(async () => answer.reject(new Error("409")));

    expect(box).not.toBeChecked();
});

test("a send that throws puts the state back", async () => {
    const {box} = setup(false, () => {
        throw new Error("no");
    });

    fireEvent.click(box);
    await settle();

    expect(box).not.toBeChecked();
});

test("no echo within the grace shows the state again", async () => {
    const {box} = setup(false, () => Promise.resolve());

    fireEvent.click(box);
    await settle();
    expect(box).toBeChecked();

    await settle(ECHO_GRACE_MS);

    expect(box).not.toBeChecked();
});

test("another writer's change wins over a pending choice", () => {
    const {box, setState} = setup(false, () => new Promise(() => {}));

    fireEvent.click(box);
    expect(box).toBeChecked();
    // The state moves on (e.g. a reorder brings another caster here):
    // what it says is shown.
    setState(true);
    setState(false);

    expect(box).not.toBeChecked();
});

test("an older choice's refusal does not undo a newer choice", async () => {
    const first = deferred();
    const second = deferred();
    const answers = [first, second, deferred()];
    const {box} = setup(false, () => answers.shift().promise);

    fireEvent.click(box);  // true
    fireEvent.click(box);  // false
    fireEvent.click(box);  // true again: its answer never comes
    await act(async () => first.reject(new Error("409")));

    expect(box).toBeChecked();
});
