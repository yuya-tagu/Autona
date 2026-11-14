import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {apiErrorReason, apiErrorStatus} from "../api";

// What the game assets page (ADR-005 S-final SF-4b-2) asks of /api/v1:
// the reads (the pack list THIS_PC, the job SHELL), the SHELL writes of
// SF-4b-1 and the ZIP upload. Text only: nothing here renders HTML.

export const PACKS_PATH = "/api/v1/assets/packs";
export const JOB_PATH = "/api/v1/assets/job";
export const UPLOADS_PATH = "/api/v1/assets/uploads";
export const IMPORT_ZIP_PATH = "/api/v1/assets:importZip";
export const INSTALL_PATH = "/api/v1/assets/packs:install";
export const CANCEL_PATH = "/api/v1/assets/job:cancel";
export const REVEAL_PATH = "/api/v1/assets/games-folder:reveal";
export const FILE_NAME_HEADER = "X-Autona-File-Name";

// Must be src/core/AssetPackService.py's MAX_UPLOAD_BYTES
// (tests/test_assets_page.py compares the two). A ZIP over it is
// refused here before a byte is sent (the server answers 413 without
// reading it, but would still drain what keeps arriving).
export const MAX_UPLOAD_BYTES = 8 * 1024 ** 3;

// Must be src/core/AssetPackService.py's SOURCE_URL (the same test).
export const SOURCE_URL = "https://github.com/joaorb64/TournamentStreamHelper";

// The pack list may fetch the upstream index on the server's thread (up to
// its own 30 s), so api.ts's 10 s cut-off is too short for it.
export const PACKS_TIMEOUT_MS = 120000;

export interface PackRow {
    name: string;
    size: number | null;
    version: string | null;
    installedVersion: string | null;
    installed: boolean;
    updateAvailable: boolean;
    default: boolean;
}

export interface GameRow {
    code: string;
    name: string;
    available: boolean;
    installed: boolean;
    packs: PackRow[];
}

export interface PacksPayload {
    source: {page: string; index: string};
    index: {status: string; error: string | null; fetchedAt: number | null};
    games: GameRow[];
}

export interface JobState {
    revision: number;
    status: "idle" | "running" | "done" | "error" | "cancelled";
    operation: string | null;
    game: string | null;
    stage: string | null;
    pack: string | null;
    file: string | null;
    done: number;
    total: number;
    imported: string[];
    skipped: string[];
    skippedEntries: string[];
    skippedEntriesMore: number;
    failed: {name: string; message: string}[];
    error: string | null;
    message: string | null;
}

export interface UploadAnswer {
    upload: string;
    games: string[];
    conflicts: string[];
    expiresInSec: number;
}

export function humanSize(bytes: number): string {
    let value = Math.max(0, bytes);
    for (const unit of ["B", "KB", "MB"]) {
        if (value < 1024) {
            return `${value.toFixed(0)} ${unit}`;
        }
        value /= 1024;
    }
    return `${value.toFixed(1)} GB`;
}

// The text of core's fixed code (a refusal's `error.reason` or a job's
// `error`), in the page's language. A code this build does not know says
// the operation failed.
export function errorText(code: string | null | undefined): string {
    return i18n.t(`assets_error_${code ?? "failed"}`, {
        defaultValue: i18n.t("assets_error_failed")});
}

// What a failed request says: its `reason` when it has one, else by status.
export function describeFailure(error: unknown): string {
    const reason = apiErrorReason(error);
    if (reason) {
        return errorText(reason);
    }
    if (apiErrorStatus(error) === 403) {
        return i18n.t("assets_error_forbidden");
    }
    return i18n.t("assets_error_request");
}

export class UploadFailure extends Error {
    status?: number;
    code?: string;
    reason?: string;
}

export interface UploadHandle {
    promise: Promise<UploadAnswer>;
    abort: () => void;
}

// The ZIP's bytes as the body (not multipart), the file name percent-encoded
// in a header. An XMLHttpRequest because only it reports how much of the
// body has been sent (`upload.onprogress`) and can be stopped (`abort()`);
// there is no time limit (a multi-GB ZIP takes a while). The shared view
// adds the shell's secret to it (an XHR, like a fetch).
export function uploadZip(
    file: File,
    onProgress: (sent: number, total: number) => void): UploadHandle {
    const xhr = new XMLHttpRequest();
    const promise = new Promise<UploadAnswer>((resolve, reject) => {
        const fail = (name: string, message: string) => reject(
            Object.assign(new UploadFailure(message), {name}));
        xhr.upload.onprogress = (event: ProgressEvent) => {
            onProgress(event.loaded, event.lengthComputable ? event.total
                       : file.size);
        };
        xhr.onload = () => {
            let body: any = null;
            try {
                body = JSON.parse(xhr.responseText);
            } catch {
                body = null;
            }
            if (xhr.status === 201 && body?.data?.upload) {
                resolve(body.data as UploadAnswer);
                return;
            }
            const failure = Object.assign(
                new UploadFailure(`POST ${UPLOADS_PATH} failed: HTTP ${xhr.status}`),
                {status: xhr.status,
                 code: body?.error?.code as string | undefined,
                 reason: body?.error?.reason as string | undefined});
            reject(failure);
        };
        xhr.onerror = () => fail("NetworkError", "the upload could not be sent");
        xhr.onabort = () => fail("AbortError", "the upload was stopped");
        xhr.open("POST", `${BASE_URL}${UPLOADS_PATH}`);
        xhr.setRequestHeader(FILE_NAME_HEADER, encodeURIComponent(file.name));
        xhr.send(file);
    });
    return {promise, abort: () => xhr.abort()};
}
