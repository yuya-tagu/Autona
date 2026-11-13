import React from "react";
import i18n from "../i18n/config";
import socketConnection from "../websocketConnection";
import {getWithRetry} from "./requests";

// What the app is busy with (ADR-005 S-final SF-2): core's StatusService,
// the desktop footer's source -- the start.gg fetches, the game asset load,
// the Top 8 / result imports. For the AppShell's footer (SF-5a); no page
// shows it yet.
//
// The server answers GET /api/v1/status and sends Socket.IO's `status` on
// every change and to every new connection, both with the same
// {revision, jobs}. The revision grows on every change, so the newer of an
// answer and an event wins. A restarted app counts from 0 again: after a
// reconnect, the next status is taken whatever its revision (the server
// sends one to the new connection).
//
// A job's words are a code and its args, translated here (i18next's
// status_<code>), never text from the server: the English UI shows no
// Japanese.

export interface StatusJob {
    id: number;
    code: string;
    args: Record<string, string | number>;
    done: number;
    total: number;
}

export interface AppStatus {
    revision: number;
    jobs: StatusJob[];
}

export const STATUS_PATH = "/api/v1/status";
export const STATUS_EVENT = "status";

// Nothing known yet: any status is newer.
const UNKNOWN: AppStatus = {revision: -1, jobs: []};

function readJob(value: unknown): StatusJob | null {
    if (typeof value !== "object" || value === null) {
        return null;
    }
    const job = value as Record<string, unknown>;
    if (typeof job.code !== "string") {
        return null;
    }
    const args = typeof job.args === "object" && job.args !== null
        ? job.args as Record<string, string | number> : {};
    return {
        id: Number(job.id),
        code: job.code,
        args,
        done: Number(job.done) || 0,
        total: Number(job.total) || 0,
    };
}

// The server's {revision, jobs}, or null for anything else.
export function readStatus(value: unknown): AppStatus | null {
    if (typeof value !== "object" || value === null) {
        return null;
    }
    const status = value as Record<string, unknown>;
    if (typeof status.revision !== "number" || !Array.isArray(status.jobs)) {
        return null;
    }
    return {
        revision: status.revision,
        jobs: status.jobs.map(readJob).filter(
            (job): job is StatusJob => job !== null),
    };
}

// The status and its current job (the last one: what the footer shows).
export function useAppStatus(): {status: AppStatus; current: StatusJob | null} {
    const [status, setStatus] = React.useState<AppStatus>(UNKNOWN);
    React.useEffect(() => {
        let mounted = true;
        const take = (next: AppStatus | null) => {
            if (next !== null && mounted) {
                setStatus((known) => next.revision > known.revision ? next : known);
            }
        };
        const socket = socketConnection.instance();
        const onStatus = (message: unknown) => take(readStatus(message));
        const onConnect = () => {
            if (mounted) {
                setStatus((known) => ({...known, revision: UNKNOWN.revision}));
            }
        };
        socket.on("connect", onConnect);
        socket.on(STATUS_EVENT, onStatus);
        // The socket may have connected before this mounted: its status is
        // not seen, so ask.
        getWithRetry<{data?: unknown}>(STATUS_PATH, () => mounted).then(
            (body) => take(readStatus(body?.data)),
            (error) => {
                if (mounted) {
                    console.error(error);
                }
            });
        return () => {
            mounted = false;
            socket.off("connect", onConnect);
            socket.off(STATUS_EVENT, onStatus);
        };
    }, []);
    const current = status.jobs.length > 0
        ? status.jobs[status.jobs.length - 1] : null;
    return {status, current};
}

// A job as the footer shows it, in the page's language; "" for none. A code
// this build does not know shows as itself.
export function statusText(job: StatusJob | null): string {
    if (job === null) {
        return "";
    }
    return i18n.t(`status_${job.code}`, {...job.args, defaultValue: job.code});
}
