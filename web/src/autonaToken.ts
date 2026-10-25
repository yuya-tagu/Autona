// The desktop app's web server access token (ADR-005 S3a).
//
// From another computer, the desktop app takes writes (and the player DB)
// only with this token. "ブラウザ版を開く" opens the Web UI as
// `/scoreboard?token=...`: the token is kept in sessionStorage for this tab
// and taken out of the address bar, then sent with every request (api.ts:
// the X-Autona-Token header) and on the Socket.IO connection
// (websocketConnection.ts: `auth`). Without one -- the Web UI on this PC,
// http://127.0.0.1 -- everything works as before.
//
// The desktop Designer's URL (`?desktop=1&token=...`) also has a `token`:
// the Designer's own, which desktopApi.ts reads from the address bar on
// every call. That one is left alone.

export const TOKEN_HEADER = "X-Autona-Token";

const STORAGE_KEY = "autona.webserverToken";

// When sessionStorage is unavailable (blocked storage, some embedded views).
let memoryToken: string | null = null;
let captured = false;

function store(token: string): void {
    memoryToken = token;
    try {
        window.sessionStorage.setItem(STORAGE_KEY, token);
    } catch {
        // Kept in memory only: this page load still has it.
    }
}

// Take `?token=` off the URL this page was opened with, once.
export function captureTokenFromUrl(): void {
    if (captured) {
        return;
    }
    captured = true;
    let url: URL;
    try {
        url = new URL(window.location.href);
    } catch {
        return;
    }
    if (url.searchParams.get("desktop") === "1") {
        return;
    }
    const token = url.searchParams.get("token");
    if (!token) {
        return;
    }
    store(token);
    url.searchParams.delete("token");
    try {
        // Out of the address bar (and the history entry), so a copied or
        // shared URL does not carry it.
        window.history.replaceState(
            window.history.state, "", url.pathname + url.search + url.hash);
    } catch {
        // Leaving it in the address bar does not stop anything from working.
    }
}

export function getToken(): string | null {
    captureTokenFromUrl();
    try {
        const stored = window.sessionStorage.getItem(STORAGE_KEY);
        if (stored) {
            return stored;
        }
    } catch {
        // Fall back to the in-memory copy.
    }
    return memoryToken;
}

// The request headers that carry the token, or none.
export function tokenHeaders(): Record<string, string> {
    const token = getToken();
    return token ? {[TOKEN_HEADER]: token} : {};
}

// `headers` (any HeadersInit: a plain object, a Headers, [name, value]
// pairs) with the token header set on top, as a Headers.
export function withToken(headers?: HeadersInit): Headers {
    const merged = new Headers(headers);
    for (const [name, value] of Object.entries(tokenHeaders())) {
        merged.set(name, value);
    }
    return merged;
}

// The Socket.IO `auth` payload.
export function socketAuth(): {token?: string} {
    const token = getToken();
    return token ? {token} : {};
}

export function resetTokenForTests(): void {
    memoryToken = null;
    captured = false;
    try {
        window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {
        // Nothing stored.
    }
}
