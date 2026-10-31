import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import {
    ECHO_GRACE_MS,
    STATE_FIELD_DEBOUNCE_MS,
    useStateField,
} from "./useStateField";

// The input rules every React panel shares (ADR-005 S4a): the focused field
// is not overwritten by the state, typing is sent 300 ms after the last
// change (at once on blur), and nothing is sent while an IME composes.

function Field({value, send, controls}) {
    const {pick, replace, ...props} = useStateField(value, send);
    if (controls) {
        controls.pick = pick;
        controls.replace = replace;
    }
    return <input aria-label="name" {...props}/>;
}

function setup(value = "Alice", send = jest.fn()) {
    const controls = {};
    const utils = render(<Field value={value} send={send} controls={controls}/>);
    const input = screen.getByLabelText("name");
    const setState = (next) => utils.rerender(
        <Field value={next} send={send} controls={controls}/>);
    return {...utils, input, send, setState, controls};
}

function type(input, text) {
    fireEvent.change(input, {target: {value: text}});
}

function wait(ms) {
    act(() => {
        jest.advanceTimersByTime(ms);
    });
}

beforeEach(() => {
    jest.useFakeTimers();
});

afterEach(() => {
    jest.useRealTimers();
});

test("an unfocused field shows the state as it changes", () => {
    const {input, setState, send} = setup("Alice");
    expect(input).toHaveValue("Alice");

    setState("Bob");

    expect(input).toHaveValue("Bob");
    expect(send).not.toHaveBeenCalled();
});

test("a state update does not overwrite the field being typed into", () => {
    const {input, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alic");

    setState("Someone else");

    expect(input).toHaveValue("Alic");
});

// Another writer wins over the typing in progress (S4b review): the Qt
// panels' HeldBackText rule.

test("typing in progress when another writer writes the field is not sent", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alic");

    setState("Bob");
    wait(STATE_FIELD_DEBOUNCE_MS);

    // Still shown while the field has focus, but not sent.
    expect(input).toHaveValue("Alic");
    expect(send).not.toHaveBeenCalled();
    // Leaving shows the other writer's value, and sends nothing.
    fireEvent.blur(input);
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("Bob");
});

test("what is typed after another writer's write is sent", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alic");
    setState("Bob");

    type(input, "Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("Alicia");
});

test("the field's own echo is not another writer", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, " Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenLastCalledWith(" Alicia");
    type(input, " Alicia K");

    // The server trimmed it: still the echo of the send, not another
    // writer, so the typing after the send is kept and sent.
    setState("Alicia");
    fireEvent.blur(input);

    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith(" Alicia K");
});

test("each of several sends in flight takes one echo", () => {
    const {input, send, setState} = setup("");
    fireEvent.focus(input);
    type(input, "A");
    wait(STATE_FIELD_DEBOUNCE_MS);
    type(input, "AB");
    wait(STATE_FIELD_DEBOUNCE_MS);
    type(input, "ABC");

    setState("A");
    setState("AB");
    fireEvent.blur(input);

    expect(send.mock.calls).toEqual([["A"], ["AB"], ["ABC"]]);
});

test("a write after the echo is another writer's", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);
    setState("Alicia");
    type(input, "Alicia K");

    setState("Bob");
    fireEvent.blur(input);

    expect(send.mock.calls).toEqual([["Alicia"]]);
    expect(input).toHaveValue("Bob");
});

test("a send with no echo stops counting once its grace is up", async () => {
    // The server kept the value as it was: no change comes for that send,
    // so a later change is not taken as its echo.
    const send = jest.fn(() => Promise.resolve());
    const {input, setState} = setup("Alice", send);
    act(() => input.focus());
    type(input, "Alice ");
    wait(STATE_FIELD_DEBOUNCE_MS);
    await act(async () => {});
    wait(ECHO_GRACE_MS);
    type(input, "Alice K");

    setState("Bob");
    fireEvent.blur(input);

    expect(send.mock.calls).toEqual([["Alice "]]);
    expect(input).toHaveValue("Bob");
});

test("typing is sent 300 ms after the last change, once", () => {
    const {input, send} = setup("Alice");
    fireEvent.focus(input);

    type(input, "Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS - 1);
    type(input, "Alicia K");
    wait(STATE_FIELD_DEBOUNCE_MS - 1);
    expect(send).not.toHaveBeenCalled();

    wait(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("Alicia K");
    // Still focused, still the typed text.
    expect(input).toHaveValue("Alicia K");
});

test("nothing is sent while the IME composes; compositionend debounces", () => {
    const {input, send} = setup("");
    act(() => input.focus());

    fireEvent.compositionStart(input);
    type(input, "に");
    type(input, "にほn");
    type(input, "日本");
    wait(5 * STATE_FIELD_DEBOUNCE_MS);
    expect(send).not.toHaveBeenCalled();

    // Confirmed with Enter: Enter itself sends nothing.
    fireEvent.keyDown(input, {key: "Enter", code: "Enter", isComposing: true});
    fireEvent.compositionEnd(input, {data: "日本"});
    wait(STATE_FIELD_DEBOUNCE_MS - 1);
    expect(send).not.toHaveBeenCalled();

    wait(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("日本");
    expect(document.activeElement).toBe(input);
});

test("an input event after compositionend (Firefox order) still sends once", () => {
    const {input, send} = setup("");
    fireEvent.focus(input);
    fireEvent.compositionStart(input);
    type(input, "日本");

    input.value = "日本";
    fireEvent.compositionEnd(input, {data: "日本"});
    type(input, "日本語");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("日本語");
});

test("Enter neither sends nor moves the focus", () => {
    const {input, send} = setup("Alice");
    act(() => input.focus());
    type(input, "Alicia");

    fireEvent.keyDown(input, {key: "Enter", code: "Enter"});
    fireEvent.keyUp(input, {key: "Enter", code: "Enter"});

    expect(send).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(input);
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenCalledTimes(1);
});

test("leaving the field sends at once and keeps the text until the echo", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alicia");

    fireEvent.blur(input);

    expect(send).toHaveBeenCalledWith("Alicia");
    // No flash back to the old value while the echo is on its way.
    expect(input).toHaveValue("Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenCalledTimes(1);

    setState("Alicia");
    expect(input).toHaveValue("Alicia");
});

test("a field left without typing sends nothing and shows the state", () => {
    const {input, send, setState} = setup("Alice");
    fireEvent.focus(input);
    setState("Written elsewhere");
    expect(input).toHaveValue("Alice");

    fireEvent.blur(input);

    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("Written elsewhere");
});

test("typing back to the state's value sends nothing", () => {
    const {input, send} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alic");
    type(input, "Alice");

    wait(STATE_FIELD_DEBOUNCE_MS);
    fireEvent.blur(input);

    expect(send).not.toHaveBeenCalled();
});

test("a failed send shows the state again", async () => {
    const send = jest.fn(() => Promise.reject(new Error("409")));
    const {input} = setup("Alice", send);
    fireEvent.focus(input);
    type(input, "Alicia");

    fireEvent.blur(input);
    expect(input).toHaveValue("Alicia");
    await act(async () => {});

    expect(send).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue("Alice");
});

test("a send the state never echoes falls back to the state", async () => {
    // e.g. the server kept the value as it was: no delta comes.
    const send = jest.fn(() => Promise.resolve());
    const {input} = setup("Alice", send);
    fireEvent.focus(input);
    type(input, "Alice ");
    fireEvent.blur(input);
    await act(async () => {});
    expect(input).toHaveValue("Alice ");

    wait(ECHO_GRACE_MS);

    expect(input).toHaveValue("Alice");
});

test("a state change after leaving the field wins over what was sent", () => {
    // e.g. the server normalized the value.
    const {input, setState} = setup("Alice");
    fireEvent.focus(input);
    type(input, "alice");
    fireEvent.blur(input);

    setState("Alice!");

    expect(input).toHaveValue("Alice!");
});

test("typing still pending when the field goes away is sent", () => {
    const {input, send, unmount} = setup("Alice");
    fireEvent.focus(input);
    type(input, "Alicia");

    unmount();

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("Alicia");
});

// A send whose answer the test gives: answer() resolves, fail() rejects.
function deferredSend() {
    const calls = [];
    const send = jest.fn(() => new Promise((resolve, reject) => {
        calls.push({resolve, reject});
    }));
    const last = () => calls[calls.length - 1];
    return {
        send,
        answer: () => act(async () => last().resolve()),
        fail: () => act(async () => last().reject(new Error("409"))),
    };
}

test("an echo that beats the send's answer is taken as the echo", async () => {
    const {send, answer} = deferredSend();
    const {input, setState} = setup("Alice", send);
    act(() => input.focus());
    type(input, "Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenCalledWith("Alicia");

    // The delta arrives before the HTTP response, then another writer's.
    setState("Alicia");
    setState("Bob");
    await answer();
    expect(input).toHaveValue("Alicia");

    // Leaving shows the state at once: nothing is still awaiting an echo.
    fireEvent.blur(input);
    expect(input).toHaveValue("Bob");
    wait(ECHO_GRACE_MS);
    expect(input).toHaveValue("Bob");
    expect(send).toHaveBeenCalledTimes(1);
});

test("an echo that beats the answer after leaving the field is kept", async () => {
    const {send, answer} = deferredSend();
    const {input, setState} = setup("Alice", send);
    fireEvent.focus(input);
    type(input, "Alicia");
    fireEvent.blur(input);

    setState("Alicia");
    await answer();
    wait(ECHO_GRACE_MS);

    expect(input).toHaveValue("Alicia");
    expect(send).toHaveBeenCalledTimes(1);
});

test("a send that fails while the field has focus keeps the typing until it is left", async () => {
    const {send, fail} = deferredSend();
    const {input, setState} = setup("Alice", send);
    act(() => input.focus());
    type(input, "Alicia");
    wait(STATE_FIELD_DEBOUNCE_MS);

    await fail();
    setState("Bob");
    // Still being typed into: neither the failure nor the state overwrite it.
    expect(input).toHaveValue("Alicia");

    // Left: the state, without sending the refused value again.
    fireEvent.blur(input);
    expect(input).toHaveValue("Bob");
    expect(send).toHaveBeenCalledTimes(1);
});

test("a send from a focused field that is never echoed falls back once the field is left", async () => {
    const send = jest.fn(() => Promise.resolve());
    const {input} = setup("Alice", send);
    act(() => input.focus());
    type(input, "Alice ");
    wait(STATE_FIELD_DEBOUNCE_MS);
    await act(async () => {});

    // The grace passes while the field still has focus: it is not touched.
    wait(ECHO_GRACE_MS);
    expect(input).toHaveValue("Alice ");

    fireEvent.blur(input);
    expect(input).toHaveValue("Alice");
    expect(send).toHaveBeenCalledTimes(1);
});

test("a send left before its grace is up falls back when the grace ends", async () => {
    const send = jest.fn(() => Promise.resolve());
    const {input} = setup("Alice", send);
    act(() => input.focus());
    type(input, "Alice ");
    wait(STATE_FIELD_DEBOUNCE_MS);
    await act(async () => {});

    fireEvent.blur(input);
    expect(input).toHaveValue("Alice ");
    wait(ECHO_GRACE_MS);

    expect(input).toHaveValue("Alice");
    expect(send).toHaveBeenCalledTimes(1);
});

test("a composition in progress when the field goes away is sent", () => {
    const {input, send, unmount} = setup("");
    act(() => input.focus());
    fireEvent.compositionStart(input);
    type(input, "日本");

    unmount();

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("日本");
});

test("an answer that comes after the field went away does nothing", async () => {
    // The echo timer is told apart by its delay: an async act() leaves
    // timers of its own, so the timer count says nothing here.
    const timeouts = jest.spyOn(global, "setTimeout");
    const echoTimers = () => timeouts.mock.calls.filter(
        ([, ms]) => ms === ECHO_GRACE_MS);
    try {
        const answered = deferredSend();
        const first = setup("Alice", answered.send);
        fireEvent.focus(first.input);
        type(first.input, "Alicia");
        first.unmount();
        expect(answered.send).toHaveBeenCalledWith("Alicia");

        await answered.answer();

        // No echo timer is armed for a field that is gone.
        expect(echoTimers()).toHaveLength(0);

        const refused = deferredSend();
        const second = setup("Alice", refused.send);
        fireEvent.focus(second.input);
        type(second.input, "Alicia");
        second.unmount();

        await refused.fail();

        expect(echoTimers()).toHaveLength(0);
    } finally {
        timeouts.mockRestore();
    }
});


// ── the controls of a field with suggestions (S4b) ──

test("a picked suggestion is shown and sent at once", () => {
    const {input, send, controls} = setup("");
    fireEvent.focus(input);
    type(input, "実");

    act(() => controls.pick("実況"));

    expect(input).toHaveValue("実況");
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("実況");
    // The typing it replaced is not sent after it.
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenCalledTimes(1);
});

test("a replacing choice drops the typing and waits for its own echo", async () => {
    const {input, send, setState, controls} = setup("Old");
    fireEvent.focus(input);
    type(input, "Ad");
    let answer;
    const request = new Promise((resolve) => {
        answer = resolve;
    });

    act(() => controls.replace("Ada", request));

    expect(input).toHaveValue("Ada");
    wait(STATE_FIELD_DEBOUNCE_MS);
    fireEvent.blur(input);
    // Neither the typing nor the choice goes through this field's send.
    expect(send).not.toHaveBeenCalled();
    // Left before the echo: the choice stays shown, not the old state.
    expect(input).toHaveValue("Ada");
    await act(async () => answer());
    setState("Ada");
    expect(input).toHaveValue("Ada");
});

test("a replacing choice the server changes shows the server's value", async () => {
    const {input, setState, controls} = setup("Old");
    fireEvent.focus(input);

    await act(async () => controls.replace("qbad", Promise.resolve()));
    // Censored on the way: the echo is another value. Being typed into,
    // the field is left alone; once left, the state wins when no echo of
    // the choice came within the grace (as for typing).
    setState("***");
    expect(input).toHaveValue("qbad");
    fireEvent.blur(input);
    wait(ECHO_GRACE_MS);
    expect(input).toHaveValue("***");
});

test("a replacing choice that fails falls back to the state", async () => {
    const {input, controls} = setup("Old");
    fireEvent.focus(input);
    fireEvent.blur(input);

    await act(async () => controls.replace("Ada", Promise.reject(new Error("409"))));

    expect(input).toHaveValue("Old");
});


// A field with a rule of its own (options.wins, S4e: the stream title).

function RuledField({value, send, wins}) {
    const {pick, replace, ...props} = useStateField(value, send, {wins});
    return <input aria-label="name" {...props}/>;
}

function setupRuled(value, wins, send = jest.fn()) {
    const utils = render(<RuledField value={value} send={send} wins={wins}/>);
    const input = screen.getByLabelText("name");
    const setState = (next) => utils.rerender(
        <RuledField value={next} send={send} wins={wins}/>);
    return {input, send, setState, unmount: utils.unmount};
}

test("with a rule of its own, another writer's value leaves the typing to the rule", () => {
    const wins = jest.fn(() => true);
    const {input, send, setState} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");

    setState("Cup ft. Ada");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(wins).toHaveBeenCalledWith("Mine", "Cup", "Cup ft. Ada");
    expect(send).toHaveBeenCalledWith("Mine");
});

test("typing the rule refuses is not sent, and leaving shows the state", () => {
    const wins = jest.fn(() => false);
    const {input, send, setState} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    setState("Web's");

    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("Mine");

    fireEvent.blur(input);
    expect(input).toHaveValue("Web's");
    expect(send).not.toHaveBeenCalled();
});

// The rule of the stream title, in short: typing wins over the value it
// started from or an empty one, not over another writer's.
const startedFrom = (typed, shown, current) =>
    typed !== current && (current === shown || current === "");

test("after a refusal nothing is sent while the field has focus", () => {
    // S4e review: the typing after a refusal used to be weighed against the
    // state then -- the other writer's title, which it then overwrote.
    const wins = jest.fn(startedFrom);
    const {input, send, setState} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    setState("Web's");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(wins).toHaveBeenLastCalledWith("Mine", "Cup", "Web's");

    type(input, "Mine 2");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(wins).toHaveBeenCalledTimes(1);
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("Mine 2");

    // Left: asked once more, against the same start -- refused.
    fireEvent.blur(input);
    expect(wins).toHaveBeenLastCalledWith("Mine 2", "Cup", "Web's");
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("Web's");
});

test("a held typing the rule takes on leaving is sent then", () => {
    const wins = jest.fn(startedFrom);
    const {input, send, setState} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    setState("Web's");
    wait(STATE_FIELD_DEBOUNCE_MS);
    // The other writer clears it meanwhile: the typing wins now, but is
    // not sent before the field is left.
    setState("");
    type(input, "Mine 2");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).not.toHaveBeenCalled();

    fireEvent.blur(input);

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("Mine 2");
    expect(input).toHaveValue("Mine 2");
});

test("a held typing is asked about once more when the field goes away", () => {
    const wins = jest.fn(startedFrom);
    const {input, send, setState, unmount} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    setState("Web's");
    wait(STATE_FIELD_DEBOUNCE_MS);
    setState("");

    unmount();

    expect(send).toHaveBeenCalledWith("Mine");
});

test("after a held typing is left, the next typing is asked about afresh", () => {
    const wins = jest.fn(startedFrom);
    const {input, send, setState} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    setState("Web's");
    wait(STATE_FIELD_DEBOUNCE_MS);
    fireEvent.blur(input);
    expect(input).toHaveValue("Web's");

    fireEvent.focus(input);
    type(input, "Web's 2");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(wins).toHaveBeenLastCalledWith("Web's 2", "Web's", "Web's");
    expect(send).toHaveBeenCalledWith("Web's 2");
});

test("what the field sent is what it showed in step with the state", () => {
    const wins = jest.fn(() => true);
    const {input, send} = setupRuled("Cup", wins);
    fireEvent.focus(input);
    type(input, "Mine");
    wait(STATE_FIELD_DEBOUNCE_MS);
    expect(send).toHaveBeenLastCalledWith("Mine");

    // No echo yet: the state is still the old title.
    type(input, "Mine 2");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(wins).toHaveBeenLastCalledWith("Mine 2", "Mine", "Cup");
});

test("a state that lands while the field is left unfocused is what it shows", () => {
    const wins = jest.fn(() => true);
    const {input, setState} = setupRuled("Cup", wins);
    setState("Cup ft. Ada");
    fireEvent.focus(input);
    type(input, "Mine");
    wait(STATE_FIELD_DEBOUNCE_MS);

    expect(wins).toHaveBeenLastCalledWith("Mine", "Cup ft. Ada", "Cup ft. Ada");
});
