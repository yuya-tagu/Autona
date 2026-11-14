// What the guide pages build from the catalogue and the connection (ADR-005
// S-final SF-4a-2): the strings the Qt windows built, each a pure function
// so the tables of GuidePages.test.js can pin them.

export const TOKEN_HEADER = "X-Autona-Token";

// Shown in a copied curl for another computer when the page cannot know
// the token (a browser: the token is the app's window's only).
export const TOKEN_PLACEHOLDER = "<token>";

// JSON as the windows showed it (Python's json.dumps: ", " and ": "), for
// reading: the Companion table's body and the API reference's curl.
export function spacedJson(value: unknown): string {
    if (Array.isArray(value)) {
        return `[${value.map(spacedJson).join(", ")}]`;
    }
    if (value !== null && typeof value === "object") {
        return `{${Object.entries(value as Record<string, unknown>)
            .map(([key, item]) => `${JSON.stringify(key)}: ${spacedJson(item)}`)
            .join(", ")}}`;
    }
    return JSON.stringify(value);
}

// JSON without spaces, so a pasted value is visibly one token: the OBS
// guide's body for an Advanced Scene Switcher HTTP action ("" when none).
export function compactJson(value: unknown): string {
    if (value === null || value === undefined) {
        return "";
    }
    return JSON.stringify(value);
}

export interface CurlRequest {
    method: string;
    // The example path (placeholders filled in), below the base URL.
    path: string;
    body: unknown;
}

// The API reference's "Copy curl". `baseUrl` is the connection's (this
// PC's LAN address with LAN access): with LAN access the command is meant
// for another device, so a write carries the token there needs (ADR-005
// S3a) -- the window's condition. `token` is "" when the page does not
// know it: the placeholder stands in.
export function buildCurl(request: CurlRequest, baseUrl: string, lan: boolean,
                          token: string): string {
    const parts = [`curl -X ${request.method} "${baseUrl}${request.path}"`];
    if (request.method !== "GET" && lan) {
        parts.push(`-H "${TOKEN_HEADER}: ${token || TOKEN_PLACEHOLDER}"`);
    }
    if (request.body !== null && request.body !== undefined) {
        parts.push('-H "Content-Type: application/json"');
        parts.push(`-d '${spacedJson(request.body)}'`);
    }
    return parts.join(" ");
}

// The markers of the paths the tester asks about before sending: the
// action of each destructive catalogue row (":clear", ":reset-all", ...).
export function destructiveMarkers(
    rows: {path: string; destructive: boolean}[]): string[] {
    const markers = new Set<string>();
    for (const row of rows) {
        const action = row.path.split("?")[0].split(":");
        if (row.destructive && action.length > 1) {
            markers.add(`:${action[action.length - 1]}`);
        }
    }
    return Array.from(markers).sort();
}

// What the tester asks about whatever the catalogue lists (the shell's own
// routes are not in it): a new token locks every device out until it is
// given the new one.
export const ALWAYS_CONFIRM = ["/access-token:regenerate"];

// A path as the server matches it: percent-decoded, "//" merged, no
// trailing "/".
function routeOf(path: string): string {
    let route = path.split("?")[0].split("#")[0];
    try {
        route = decodeURIComponent(route);
    } catch {
        // Kept as typed: the server refuses it as well.
    }
    return route.replace(/\/{2,}/g, "/").replace(/(.)\/$/, "$1");
}

export function isDestructive(method: string, path: string,
                              markers: string[]): boolean {
    if (method === "GET") {
        return false;
    }
    const route = routeOf(path);
    return markers.some((marker) => route.includes(marker))
        || ALWAYS_CONFIRM.includes(route.toLowerCase());
}

// A tester answer as shown: the access token hidden (the token row is the
// place to see it; the tester is for trying requests). `data.token` --
// only the access token routes answer one -- and, wherever else it might
// appear, the token the page knows.
export function hideToken(text: string, known: string,
                          hidden: string): string {
    let shown = text;
    try {
        const body = JSON.parse(text);
        if (body && typeof body === "object" && body.data
                && typeof body.data === "object"
                && typeof body.data.token === "string") {
            body.data = {...body.data, token: hidden};
        }
        shown = JSON.stringify(body, null, 2);
    } catch {
        // Not JSON: shown as it came.
    }
    return known ? shown.split(known).join(hidden) : shown;
}

// Whether a tester answer carries the access token: a write's (the
// regeneration's) is a new one, which the token row then reads again.
export function carriesToken(text: string): boolean {
    try {
        const body = JSON.parse(text);
        return typeof body?.data?.token === "string";
    } catch {
        return false;
    }
}

// The tester's path: a leading "/" added, as the window did.
export function testerPath(path: string): string {
    const trimmed = path.trim();
    return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

// An out/ file's absolute path (the OBS guide's watch files): `relative`
// is "out/..." with "/", joined below out/ with this PC's separator.
export function watchPath(outDir: string, separator: string,
                          relative: string): string {
    const below = relative.replace(/^out\//, "").split("/");
    const base = outDir.endsWith(separator)
        ? outDir.slice(0, -separator.length) : outDir;
    return [base, ...below].join(separator);
}
