import React from "react";

// A text field that shows a value of the state and sends what is typed into
// it (ADR-005 S4a) -- the Qt panels' input rules, as one hook for every
// React panel:
//
// - The field shows `value` (the state), except while it has focus: then a
//   state update does not overwrite what is being typed. Once it loses
//   focus it shows the state again.
// - Another writer's value wins over the typing in progress (S4b review;
//   the Qt panels' HeldBackText rule): when the field's value is written
//   elsewhere while it has focus, what was typed and not yet sent is
//   dropped -- still shown while the field has focus, but not sent, and
//   leaving it shows the other writer's value. What is typed after that
//   write is sent as usual. The echo of the field's own send is not
//   another writer: the oldest send still awaiting its echo takes the next
//   change of the value, whatever the server made of it (trimmed,
//   censored).
// - Typing is sent `debounceMs` (300 ms) after the last change, and at once
//   when the field loses focus or goes away. Only typing is sent: focusing
//   and leaving a field without typing sends nothing, so a value another
//   client wrote meanwhile is not overwritten with the old one.
// - Nothing is sent while an IME composition is in progress (isComposing):
//   the composition's end (compositionend) starts the debounce. Enter is the
//   IME's (confirming a conversion): the hook neither sends on Enter nor
//   moves the focus.
// - After losing focus, the field keeps showing what it sent until the
//   state changes (the echo), instead of flashing back to the old value for
//   the round trip. If the send fails, or no echo comes within
//   ECHO_GRACE_MS of its answer (the server kept the value as it was), the
//   field shows the state again.
//
// `send` may return a promise (an apiPatch); a rejection counts as a failed
// send. Spread the result onto an <input> or a MUI TextField -- all of it
// but the two controls (S4b; take them off first, they are not DOM props),
// which a field with suggestions calls itself:
//
// - pick(text): a suggestion chosen from the field's own list (a role):
//   shown, and sent at once, like typing.
// - replace(text, request): a choice another write makes for the field (a
//   player picked from the name's suggestions, which fills the whole
//   caster): shown at once, typing not sent yet is dropped (the choice
//   replaces it), and `request` stands for the send -- the field waits for
//   its echo the same way.
//
// `options.wins` (S4e, the stream title) replaces the "another writer wins"
// rule with the field's own: each send of typing first asks
// wins(typed, shown, current) -- `shown` what the field last showed in step
// with the state (rendered while not focused, equal to the state while
// focused, or sent), `current` the state now. A change of the state alone
// does not drop the typing: the rule decides. Once it refuses typing that
// is not the state's, the field sends nothing more while it has focus
// (shown, not sent, like typing another writer overtook), and leaving it
// asks the rule once more, about what is in the field then and against the
// same `shown`: sent if it wins now, else the field shows the state (S4e
// review; the Qt panel's rule, asked once on leaving, S4d).

export const STATE_FIELD_DEBOUNCE_MS = 300;
export const ECHO_GRACE_MS = 1000;

export interface StateFieldOptions {
    debounceMs?: number;
    wins?: (typed: string, shown: string, current: string) => boolean;
}

type TextElement = HTMLInputElement | HTMLTextAreaElement;

export interface StateFieldProps {
    value: string;
    onChange: (event: React.ChangeEvent<TextElement>) => void;
    onFocus: () => void;
    onBlur: () => void;
    // Element, not the input: on a TextField they are caught on its root.
    onCompositionStart: (event: React.CompositionEvent<Element>) => void;
    onCompositionEnd: (event: React.CompositionEvent<Element>) => void;
}

export interface StateFieldControls {
    pick: (text: string) => void;
    replace: (text: string, request: unknown) => void;
}

export function useStateField(
    value: string,
    send: (value: string) => unknown,
    options: StateFieldOptions = {},
): StateFieldProps & StateFieldControls {
    const debounceMs = options.debounceMs ?? STATE_FIELD_DEBOUNCE_MS;
    const [draft, setDraft] = React.useState(value);

    const text = React.useRef(value);         // what the field shows
    const state = React.useRef(value);        // the latest `value`
    const focused = React.useRef(false);
    const composing = React.useRef(false);
    const edited = React.useRef(false);       // typed since the last send
    const sent = React.useRef<string | null>(null);  // awaiting its echo
    // What the field last showed in step with the state (options.wins).
    const shown = React.useRef(value);
    // options.wins refused the typing: nothing is sent until the field is
    // left, when the rule is asked once more.
    const held = React.useRef(false);
    // Every send not yet echoed, failed or past its grace, oldest first:
    // the changes of `value` they account for are not another writer's.
    const pending = React.useRef<number[]>([]);
    const sends = React.useRef(0);
    const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const graceTimers = React.useRef(new Set<ReturnType<typeof setTimeout>>());
    // Cleared on unmount: a send answered after the field went away
    // neither arms an echo timer nor touches the field.
    const mounted = React.useRef(true);
    const sendRef = React.useRef(send);
    sendRef.current = send;
    const winsRef = React.useRef(options.wins);
    winsRef.current = options.wins;

    const show = React.useCallback((next: string) => {
        text.current = next;
        setDraft(next);
    }, []);

    const cancelTimer = React.useCallback(() => {
        if (timer.current !== null) {
            clearTimeout(timer.current);
            timer.current = null;
        }
    }, []);

    // `sentText` got no echo: show the state, unless it was superseded.
    const resync = React.useCallback((sentText: string) => {
        if (!mounted.current || sent.current !== sentText) {
            return;
        }
        sent.current = null;
        shown.current = state.current;
        if (!focused.current) {
            show(state.current);
        }
    }, [show]);

    // `next` was sent (`result` its answer): wait for its echo.
    const awaitEcho = React.useCallback((next: string, result: unknown) => {
        sent.current = next;
        const id = ++sends.current;
        pending.current.push(id);
        // No echo is coming any more (failed, or none within the grace).
        const settle = () => {
            pending.current = pending.current.filter((other) => other !== id);
            resync(next);
        };
        Promise.resolve(result).then(
            () => {
                if (!mounted.current) {
                    return;
                }
                const grace = setTimeout(() => {
                    graceTimers.current.delete(grace);
                    settle();
                }, ECHO_GRACE_MS);
                graceTimers.current.add(grace);
            },
            settle,
        );
    }, [resync]);

    // `leaving`: the field loses focus or goes away (a held typing is
    // asked about once more).
    const commit = React.useCallback((leaving = false) => {
        cancelTimer();
        if (held.current) {
            if (!leaving) {
                return;
            }
            held.current = false;
            edited.current = true;
        }
        if (!edited.current) {
            return;
        }
        edited.current = false;
        const next = text.current;
        const wins = winsRef.current;
        if (wins && !wins(next, shown.current, state.current)) {
            // Refused: not sent. Typing at odds with the state is held
            // until the field is left (`shown` kept: the rule is asked
            // about it then against the same start).
            if (!leaving && next !== state.current) {
                held.current = true;
            }
            return;
        }
        if (next === (sent.current ?? state.current)) {
            return;
        }
        let result: unknown;
        sent.current = next;
        shown.current = next;
        try {
            result = sendRef.current(next);
        } catch (error) {
            result = Promise.reject(error);
        }
        awaitEcho(next, result);
    }, [cancelTimer, awaitEcho]);

    const schedule = React.useCallback(() => {
        cancelTimer();
        timer.current = setTimeout(() => commit(), debounceMs);
    }, [cancelTimer, commit, debounceMs]);

    // The state changed: our echo, or another writer.
    React.useEffect(() => {
        state.current = value;
        const echo = pending.current.shift() !== undefined;
        if (focused.current) {
            // Being typed into: not overwritten. Our own echo is noted.
            if (value === sent.current) {
                sent.current = null;
            }
            if (value === text.current) {
                shown.current = value;
            }
            if (!echo && edited.current && !winsRef.current) {
                // Another writer: the typing in progress is not sent.
                cancelTimer();
                edited.current = false;
            }
            return;
        }
        sent.current = null;
        shown.current = value;
        show(value);
    }, [value, show, cancelTimer]);

    // Going away (another panel shown): what was typed is still sent --
    // waiting on the debounce, or mid-composition (no timer is running
    // then). Its answer is not waited for.
    const flushOnUnmount = React.useCallback(() => {
        mounted.current = false;
        commit(true);
        graceTimers.current.forEach(clearTimeout);
        graceTimers.current.clear();
    }, [commit]);
    React.useEffect(() => {
        // Set again on mount: StrictMode mounts, unmounts and remounts.
        mounted.current = true;
        return flushOnUnmount;
    }, [flushOnUnmount]);

    const onChange = (event: React.ChangeEvent<TextElement>) => {
        show(event.target.value);
        edited.current = true;
        const native = event.nativeEvent as InputEvent | undefined;
        if (composing.current || native?.isComposing) {
            cancelTimer();
            return;
        }
        schedule();
    };

    const onCompositionStart = () => {
        composing.current = true;
        cancelTimer();
    };

    const onCompositionEnd = (event: React.CompositionEvent<Element>) => {
        composing.current = false;
        const target = event.target as TextElement;
        if (typeof target.value === "string") {
            show(target.value);
            edited.current = true;
        }
        schedule();
    };

    const onFocus = () => {
        focused.current = true;
    };

    const onBlur = () => {
        focused.current = false;
        composing.current = false;
        commit(true);
        if (sent.current === null) {
            shown.current = state.current;
            show(state.current);
        }
    };

    const pick = (next: string) => {
        show(next);
        edited.current = true;
        commit();
    };

    const replace = (next: string, request: unknown) => {
        cancelTimer();
        edited.current = false;
        held.current = false;
        show(next);
        shown.current = next;
        awaitEcho(next, request);
    };

    return {
        value: draft,
        onChange,
        onFocus,
        onBlur,
        onCompositionStart,
        onCompositionEnd,
        pick,
        replace,
    };
}
