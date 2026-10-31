import React from "react";

// A text field that shows a value of the state and sends what is typed into
// it (ADR-005 S4a) -- the Qt panels' input rules, as one hook for every
// React panel:
//
// - The field shows `value` (the state), except while it has focus: then a
//   state update does not overwrite what is being typed. Once it loses
//   focus it shows the state again.
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
// send. Spread the result onto an <input> or a MUI TextField.

export const STATE_FIELD_DEBOUNCE_MS = 300;
export const ECHO_GRACE_MS = 1000;

export interface StateFieldOptions {
    debounceMs?: number;
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

export function useStateField(
    value: string,
    send: (value: string) => unknown,
    options: StateFieldOptions = {},
): StateFieldProps {
    const debounceMs = options.debounceMs ?? STATE_FIELD_DEBOUNCE_MS;
    const [draft, setDraft] = React.useState(value);

    const text = React.useRef(value);         // what the field shows
    const state = React.useRef(value);        // the latest `value`
    const focused = React.useRef(false);
    const composing = React.useRef(false);
    const edited = React.useRef(false);       // typed since the last send
    const sent = React.useRef<string | null>(null);  // awaiting its echo
    const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const graceTimers = React.useRef(new Set<ReturnType<typeof setTimeout>>());
    // Cleared on unmount: a send answered after the field went away
    // neither arms an echo timer nor touches the field.
    const mounted = React.useRef(true);
    const sendRef = React.useRef(send);
    sendRef.current = send;

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
        if (!focused.current) {
            show(state.current);
        }
    }, [show]);

    const commit = React.useCallback(() => {
        cancelTimer();
        if (!edited.current) {
            return;
        }
        edited.current = false;
        const next = text.current;
        if (next === (sent.current ?? state.current)) {
            return;
        }
        sent.current = next;
        let result: unknown;
        try {
            result = sendRef.current(next);
        } catch (error) {
            result = Promise.reject(error);
        }
        Promise.resolve(result).then(
            () => {
                if (!mounted.current) {
                    return;
                }
                const grace = setTimeout(() => {
                    graceTimers.current.delete(grace);
                    resync(next);
                }, ECHO_GRACE_MS);
                graceTimers.current.add(grace);
            },
            () => resync(next),
        );
    }, [cancelTimer, resync]);

    const schedule = React.useCallback(() => {
        cancelTimer();
        timer.current = setTimeout(commit, debounceMs);
    }, [cancelTimer, commit, debounceMs]);

    // The state changed: our echo, or another writer.
    React.useEffect(() => {
        state.current = value;
        if (focused.current) {
            // Being typed into: not overwritten. Our own echo is noted.
            if (value === sent.current) {
                sent.current = null;
            }
            return;
        }
        sent.current = null;
        show(value);
    }, [value, show]);

    // Going away (another panel shown): what was typed is still sent --
    // waiting on the debounce, or mid-composition (no timer is running
    // then). Its answer is not waited for.
    const flushOnUnmount = React.useCallback(() => {
        mounted.current = false;
        commit();
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
        commit();
        if (sent.current === null) {
            show(state.current);
        }
    };

    return {
        value: draft,
        onChange,
        onFocus,
        onBlur,
        onCompositionStart,
        onCompositionEnd,
    };
}
