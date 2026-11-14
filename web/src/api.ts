import {BASE_URL} from "./env";
import {withToken} from "./autonaToken";

// Thin fetch wrapper for the desktop app's REST endpoints.
// Non-2xx responses and timeouts reject, so callers (useApiAction /
// class components) can surface failures instead of silently logging.
// URL shape stays `http://${hostname}:${BACKEND_PORT}` via BASE_URL
// (.claude/rules/react-web-ui.md).
//
// Controls go through the external control API (`/api/v1/...`, ADR-001);
// the legacy `/scoreboard<N>-...` routes are deprecated. v1 answers with an
// envelope `{"ok": true, "data"?: ..., "revision"?: n}`, so reads take
// `.data` off the parsed JSON; its errors are non-2xx and reject here.

const DEFAULT_TIMEOUT_MS = 10000;

export interface ApiOptions {
    // Endpoints that proxy Start.gg (get-sets, load-set) can legitimately
    // take tens of seconds; pass a larger value there.
    timeoutMs?: number;
    // Let the request outlive the page's unloading (a best-effort DELETE
    // sent as the page goes away).
    keepalive?: boolean;
}

async function request(path: string, init?: RequestInit, opts?: ApiOptions): Promise<Response> {
    const resp = await fetch(`${BASE_URL}${path}`, {
        signal: AbortSignal.timeout(opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS),
        ...init,
        ...(opts?.keepalive ? {keepalive: true} : {}),
        // The access token when this page was opened with one (ADR-005 S3a):
        // from another computer, writes need it. None on this PC. Merged
        // through Headers, so the caller's headers survive in any form.
        headers: withToken(init?.headers),
    });
    if (!resp.ok) {
        // The status rides along (ApiError) for a caller that answers one
        // refusal differently (a 409 from /api/v1), and so does /api/v1's
        // error code, for one whose refusals share a status (two 409s).
        throw Object.assign(
            new Error(`${init?.method ?? "GET"} ${path} failed: HTTP ${resp.status}`),
            {status: resp.status, ...await errorFields(resp)});
    }
    return resp;
}

// /api/v1's error envelope: {"ok": false, "error": {"code", "message",
// "reason"?}}. `reason` is the finer fixed code some routes add (the game
// asset routes: spec 2.5).
async function errorFields(resp: Response): Promise<{code?: string; reason?: string}> {
    try {
        const error = (await resp.json())?.error;
        return {
            code: typeof error?.code === "string" ? error.code : undefined,
            reason: typeof error?.reason === "string" ? error.reason : undefined,
        };
    } catch {
        return {};
    }
}

export type ApiError = Error & {status?: number; code?: string; reason?: string};

export function apiErrorStatus(error: unknown): number | undefined {
    return (error as ApiError | null)?.status;
}

export function apiErrorCode(error: unknown): string | undefined {
    return (error as ApiError | null)?.code;
}

export function apiErrorReason(error: unknown): string | undefined {
    return (error as ApiError | null)?.reason;
}

export async function apiGetJson<T = unknown>(path: string, opts?: ApiOptions): Promise<T> {
    return (await request(path, undefined, opts)).json();
}

async function send(method: string, path: string, body?: unknown, opts?: ApiOptions): Promise<string> {
    const resp = await request(path, {
        method,
        headers: {"content-type": "application/json"},
        body: JSON.stringify(body ?? {}),
    }, opts);
    return resp.text();
}

export async function apiPost(path: string, body?: unknown, opts?: ApiOptions): Promise<string> {
    return send("POST", path, body, opts);
}

export async function apiPatch(path: string, body?: unknown, opts?: ApiOptions): Promise<string> {
    return send("PATCH", path, body, opts);
}

export async function apiPut(path: string, body?: unknown, opts?: ApiOptions): Promise<string> {
    return send("PUT", path, body, opts);
}

export async function apiDelete(path: string, opts?: ApiOptions): Promise<string> {
    return send("DELETE", path, undefined, opts);
}
