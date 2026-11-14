import React from "react";
import {apiGetJson} from "../api";
import socketConnection from "../websocketConnection";
import {JOB_PATH, JobState} from "./assetsApi";

// The asset job's state (GET /api/v1/assets/job, SHELL), read:
// - when the page mounts (a job already running is adopted as this page's),
// - about every second while a job runs and the page is shown,
// - whenever the app says its status changed (Socket.IO `status`, which
//   carries `assets_load` while a job runs) or the socket reconnects, and
//   when the page is shown again after being hidden.
// A page hidden does not poll: the shared view freezes it anyway. A read
// is booked once the one before it has answered (never two in flight).
//
// The job this page owns is `tracked` (its revision: the one it started or
// adopted). A job with a newer revision means the page's ended unseen: one
// job runs at a time, and only the last one's result can be read, so the
// outcome is `lost` -- shown as unknown, never polled for.
//
// The revision a page started is also kept in sessionStorage (just the
// number: never the token), so a page that was left and came back -- or was
// reloaded -- while its job ran shows the result of the same job when the
// server's last job is that one.

export const POLL_MS = 1000;
export const JOB_STORAGE_KEY = "autona.assets.job";

function rememberedRevision(): number | null {
    try {
        const value = Number(sessionStorage.getItem(JOB_STORAGE_KEY));
        return Number.isInteger(value) && value > 0 ? value : null;
    } catch {
        return null;
    }
}

function remember(revision: number) {
    try {
        sessionStorage.setItem(JOB_STORAGE_KEY, String(revision));
    } catch {
        // No storage: the page just forgets, as it did.
    }
}

export interface AssetJob {
    job: JobState | null;
    // The revision of the job this page owns; null for none.
    tracked: number | null;
    // The page's job ended and another has started since: result unknown.
    lost: boolean;
    // Any job runs (the page's or not).
    running: boolean;
    // The job could not be read.
    readFailed: boolean;
    // Own a job just started (its revision, from the 202's `data.job`).
    // Resolves once the job has been read once.
    begin: (revision: number) => Promise<void>;
    read: () => Promise<void>;
}

export function useAssetJob(enabled: boolean, visible: boolean,
                            reveals: number): AssetJob {
    const [job, setJob] = React.useState<JobState | null>(null);
    const [tracked, setTracked] = React.useState<number | null>(null);
    const [readFailed, setReadFailed] = React.useState(false);
    const trackedRef = React.useRef<number | null>(null);
    const sequence = React.useRef(0);
    const first = React.useRef(true);
    const live = React.useRef(true);

    React.useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
        };
    }, []);

    const own = React.useCallback((revision: number) => {
        trackedRef.current = revision;
        setTracked(revision);
        remember(revision);
    }, []);

    const read = React.useCallback(async () => {
        const mine = ++sequence.current;
        try {
            const body = await apiGetJson<{data: JobState}>(JOB_PATH);
            // Only the newest request's answer is used.
            if (!live.current || mine !== sequence.current) {
                return;
            }
            const next = body.data;
            const floor = trackedRef.current;
            if (floor !== null && next.revision < floor) {
                return;
            }
            if (first.current) {
                first.current = false;
                // A running job is adopted; so is the finished one this
                // page started before (the revision it remembered).
                if (next.status === "running"
                        || (next.status !== "idle"
                            && next.revision === rememberedRevision())) {
                    own(next.revision);
                }
            }
            setJob(next);
            setReadFailed(false);
        } catch (error) {
            if (live.current && mine === sequence.current) {
                console.error(error);
                setReadFailed(true);
            }
        }
    }, [own]);

    const begin = React.useCallback((revision: number) => {
        own(revision);
        return read();
    }, [own, read]);

    React.useEffect(() => {
        if (enabled) {
            void read();
        }
    }, [enabled, read]);

    React.useEffect(() => {
        if (enabled && reveals > 0) {
            void read();
        }
    }, [enabled, reveals, read]);

    const running = job?.status === "running";
    React.useEffect(() => {
        if (!enabled || !visible || !running) {
            return undefined;
        }
        let stopped = false;
        let timer: ReturnType<typeof setTimeout>;
        const tick = async () => {
            await read();
            if (!stopped) {
                timer = setTimeout(() => void tick(), POLL_MS);
            }
        };
        timer = setTimeout(() => void tick(), POLL_MS);
        return () => {
            stopped = true;
            clearTimeout(timer);
        };
    }, [enabled, visible, running, read]);

    React.useEffect(() => {
        if (!enabled) {
            return undefined;
        }
        const socket = socketConnection.instance();
        const onChange = () => {
            if (document.visibilityState !== "hidden") {
                void read();
            }
        };
        socket.on("status", onChange);
        socket.on("connect", onChange);
        return () => {
            socket.off("status", onChange);
            socket.off("connect", onChange);
        };
    }, [enabled, read]);

    const lost = tracked !== null && job !== null && job.revision > tracked;
    return {job, tracked, lost, running, readFailed, begin, read};
}
