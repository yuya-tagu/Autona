import React from "react";
import {ECHO_GRACE_MS} from "./useStateField";

// A choice control (a checkbox, a select, a colour) that shows a value of
// the state and sends what is chosen (ADR-005 S4b) -- useStateField's echo
// rule without the typing:
//
// - The control shows the choice at once, until the state changes (the
//   echo, or another writer: the state wins), the send fails (a refusal,
//   e.g. a fifth caster shown: the control goes back to the state), or no
//   echo comes within ECHO_GRACE_MS of its answer (the server kept the
//   value as it was).
// - Nothing is debounced: a choice is one action.
//
// `send` may return a promise (an apiPatch); a rejection counts as a failed
// send. Returns [what to show, choose].
export function useStateChoice<T>(
    value: T,
    send: (value: T) => unknown,
): [T, (value: T) => void] {
    const [pending, setPending] = React.useState<{value: T} | null>(null);
    const sendRef = React.useRef(send);
    sendRef.current = send;
    // The latest choice: an answer to an older one does not clear it.
    const latest = React.useRef(0);
    const mounted = React.useRef(true);
    const graceTimers = React.useRef(new Set<ReturnType<typeof setTimeout>>());

    // The state changed: the echo, or another writer. Either way it is shown.
    React.useEffect(() => {
        setPending(null);
    }, [value]);

    React.useEffect(() => {
        mounted.current = true;
        const timers = graceTimers.current;
        return () => {
            mounted.current = false;
            timers.forEach(clearTimeout);
            timers.clear();
        };
    }, []);

    const choose = React.useCallback((next: T) => {
        const id = ++latest.current;
        setPending({value: next});
        const settle = () => {
            if (mounted.current && latest.current === id) {
                setPending(null);
            }
        };
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
                    settle();
                }, ECHO_GRACE_MS);
                graceTimers.current.add(grace);
            },
            settle,
        );
    }, []);

    return [pending ? pending.value : value, choose];
}
