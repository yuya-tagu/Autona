import React from "react";
import {apiErrorStatus, apiGetJson} from "../api";

// How a React panel talks to /api/v1 (ADR-005 S4b's Commentary page; shared
// with the Stream page since S4e): its writes go one at a time, none waits
// longer than WRITE_TIMEOUT_MS, and the reads it cannot work without are
// tried again while the server may still be starting.

// How long a write may take. The fetch is aborted then (api.ts's timeout),
// and the page stops waiting for it at the same moment: its snackbar shows
// and the writes queued behind it go on, instead of the whole page's
// writes waiting on a request that never answers.
export const WRITE_TIMEOUT_MS = 10000;
export const WRITE_OPTIONS = {timeoutMs: WRITE_TIMEOUT_MS};

// The reads the page cannot work without are tried this many times, the
// waits between them growing by LOAD_RETRY_MS (the server may be busy
// starting). A refusal (4xx) is an answer, not tried again.
export const LOAD_ATTEMPTS = 3;
export const LOAD_RETRY_MS = 1000;

export function withDeadline<T>(request: () => Promise<T>,
                                ms: number = WRITE_TIMEOUT_MS): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => reject(Object.assign(
            new Error(`no answer within ${ms} ms`),
            {name: "TimeoutError"})), ms);
        Promise.resolve().then(request).then(resolve, reject)
            .finally(() => clearTimeout(timer));
    });
}

// The page's writes, one at a time in the order they were made: a field
// typed into and then dragged elsewhere is written to the slot it was typed
// in before the casters move, and a name's pick lands after its typing.
export function useSerialRequests() {
    const tail = React.useRef<Promise<unknown>>(Promise.resolve());
    return React.useCallback(<T,>(request: () => Promise<T>): Promise<T> => {
        const next = () => withDeadline(request);
        const run = tail.current.then(next, next);
        tail.current = run.catch(() => undefined);
        return run;
    }, []);
}

export async function getWithRetry<T>(path: string,
                                       live: () => boolean): Promise<T> {
    for (let attempt = 1; ; attempt++) {
        try {
            return await apiGetJson<T>(path);
        } catch (error) {
            const status = apiErrorStatus(error);
            const refused = status !== undefined && status < 500;
            if (refused || attempt >= LOAD_ATTEMPTS || !live()) {
                throw error;
            }
            await new Promise((resolve) =>
                setTimeout(resolve, LOAD_RETRY_MS * attempt));
        }
    }
}
