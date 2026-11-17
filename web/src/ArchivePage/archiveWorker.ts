// The archive clipper's worker install (ADR-006 A7): the shape GET
// /api/v1/archive/worker answers (core/ArchiveWorkerInstaller.py's
// Snapshot) and the card's pure helpers.

export type CheckStatus = "idle" | "checking" | "done" | "error";
export type InstallStatus = "idle" | "running" | "done" | "error" | "cancelled";
export type InstallStage = "download" | "verify" | "extract" | "probe" | "swap";

export interface WorkerInstallJob {
    revision: number;
    status: InstallStatus;
    stage: InstallStage | null;
    done: number;
    total: number;
    version: string | null;
    // The server's code (error) and its Japanese message (done / error /
    // cancelled).
    error: string | null;
    message: string | null;
}

export interface WorkerSnapshot {
    // The status' last probe (null before one).
    installed: {available: boolean; version: string | null} | null;
    // AUTONA_VODSPLIT_CMD names the worker: the installed one is not used.
    env_command: boolean;
    latest: {version: string; size: number; published_at: string | null} | null;
    check: {status: CheckStatus; error: string | null; message: string | null;
            checked_at: number | null};
    update_available: boolean;
    job: WorkerInstallJob;
}

// The read is asked again while the release check or an install runs.
export function isWorking(snapshot: WorkerSnapshot | null): boolean {
    return snapshot !== null && (snapshot.check.status === "checking"
        || snapshot.job.status === "running");
}

// "152.3 MB" (MiB, as Windows' Explorer counts).
export function sizeText(bytes: number): string {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The progress bar's value (0..100), or null for a stage that has none to
// show (the checks and the swap: an indeterminate bar).
export function progressValue(job: WorkerInstallJob): number | null {
    if ((job.stage === "download" || job.stage === "extract") && job.total > 0) {
        return Math.min(100, (job.done / job.total) * 100);
    }
    return null;
}

// What can stop it: the install looks at a cancel before each step, the
// swap itself is not stopped.
export function canCancel(job: WorkerInstallJob): boolean {
    return job.status === "running" && job.stage !== "swap";
}
