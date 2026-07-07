import {BASE_URL} from "./env";

// Thin fetch wrapper for the desktop app's REST endpoints.
// Non-2xx responses and timeouts reject, so callers (useApiAction /
// class components) can surface failures instead of silently logging.
// URL shape stays `http://${hostname}:${BACKEND_PORT}` via BASE_URL
// (.claude/rules/react-web-ui.md).

const DEFAULT_TIMEOUT_MS = 10000;

export interface ApiOptions {
    // Endpoints that proxy Start.gg (get-sets, load-set) can legitimately
    // take tens of seconds; pass a larger value there.
    timeoutMs?: number;
}

async function request(path: string, init?: RequestInit, opts?: ApiOptions): Promise<Response> {
    const resp = await fetch(`${BASE_URL}${path}`, {
        signal: AbortSignal.timeout(opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS),
        ...init,
    });
    if (!resp.ok) {
        throw new Error(`${init?.method ?? "GET"} ${path} failed: HTTP ${resp.status}`);
    }
    return resp;
}

export async function apiGet(path: string, opts?: ApiOptions): Promise<string> {
    return (await request(path, undefined, opts)).text();
}

export async function apiGetJson<T = unknown>(path: string, opts?: ApiOptions): Promise<T> {
    return (await request(path, undefined, opts)).json();
}

export async function apiPost(path: string, body?: unknown, opts?: ApiOptions): Promise<string> {
    const resp = await request(path, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(body ?? {}),
    }, opts);
    return resp.text();
}
