// What the desktop app's embedded view calls on the panel pages (ADR-005 S4a).
//
// The desktop shows every panel moved to React in one shared web view
// (src/AutonaWebPanelHost.py). Picking another of them in its sidebar does
// not reload the view: it runs `window.autonaPanelShell.navigate(panel)`,
// and this router moves to `/web/<panel>`. When the view is on screen again
// after being frozen, it runs `.refresh()`, which asks the server for the
// full state (deltas broadcast meanwhile may be lost). The load_set hotkey
// (Ctrl+Shift+O) and the legacy open-set route run `.openScoreSets(board)`
// after navigating to SCORE (ADR-005 S4ab-2): the SCORE page opens that
// board's set selection dialog. All are harmless in a browser, where nothing
// calls them.
//
// The other way, a page asks the desktop itself through shell requests
// (ADR-005 S-final SF-5a-1; shellRequest below), answered through
// `.shellResult(id, result)`, and hears the desktop's state through
// `.shellState(state)`.

import socketConnection from "../websocketConnection";
import {apiErrorStatus} from "../api";
import {getWithRetry} from "./requests";

type Navigate = (to: string, options?: {replace?: boolean}) => void;
type OpenScoreSets = (board: number) => void;

export interface PanelShellApi {
    navigate(panel: string): void;
    refresh(): void;
    openScoreSets(board: number): void;
    shellResult(id: string, result: ShellResult): void;
    shellState(state: ShellState): void;
}

// The panel whose set selection openScoreSets opens.
export const SCORE_PANEL = "scoreboard";

declare global {
    interface Window {
        autonaPanelShell?: PanelShellApi;
    }
}

// The mounted panel shell's router navigate (PanelShell registers it).
let routerNavigate: Navigate | null = null;

export function panelPath(panel: string): string {
    return `/web/${encodeURIComponent(panel)}`;
}

// Returns the unregister, for the effect's cleanup.
export function registerRouterNavigate(navigate: Navigate): () => void {
    routerNavigate = navigate;
    return () => {
        if (routerNavigate === navigate) {
            routerNavigate = null;
        }
    };
}

// The mounted SCORE page's receiver of openScoreSets, and a request made
// while there is none (the page still loading, the router not on it yet),
// handed to it when it registers.
let scoreSetsReceiver: OpenScoreSets | null = null;
let pendingScoreSets: number | null = null;

// SCORE registers on mount; returns the unregister, for the effect's cleanup.
// A request made before is handed over now, once.
export function registerScoreSetsReceiver(receiver: OpenScoreSets): () => void {
    scoreSetsReceiver = receiver;
    if (pendingScoreSets !== null) {
        const board = pendingScoreSets;
        pendingScoreSets = null;
        receiver(board);
    }
    return () => {
        if (scoreSetsReceiver === receiver) {
            scoreSetsReceiver = null;
        }
    };
}

export function openScoreSets(board: number): void {
    if (scoreSetsReceiver) {
        pendingScoreSets = null;
        scoreSetsReceiver(board);
        return;
    }
    // The newest one only.
    pendingScoreSets = board;
}

export function navigateToPanel(panel: string): void {
    if (panel !== SCORE_PANEL) {
        // The operator went elsewhere before SCORE took it: not to be opened
        // when SCORE is shown later.
        pendingScoreSets = null;
    }
    const pathname = panelPath(panel);
    if (window.location.pathname === pathname) {
        return;
    }
    // The query (desktop=1, lng=...) says how the page was opened: kept.
    // Replaced, not pushed: the desktop has no back button.
    const to = pathname + window.location.search + window.location.hash;
    if (routerNavigate) {
        routerNavigate(to, {replace: true});
        return;
    }
    // No shell mounted yet (the page is still starting): move the URL. The
    // router reads it when it renders, or hears the popstate if it already
    // listens.
    window.history.replaceState(window.history.state, "", to);
    window.dispatchEvent(
        new PopStateEvent("popstate", {state: window.history.state}));
}

export function requestFullState(): void {
    const socket = socketConnection.instance();
    // Not connected: socket.io reconnects on its own, and the server sends
    // the full state on every connect.
    if (socket.connected) {
        socket.emit("program_state", {});
    }
}

// ── Shell requests (ADR-005 S-final SF-5a-1) ────────────────────────────
// What a page asks of the desktop itself: the window's "always on top", the
// update check, one of a fixed few outside addresses. There is no privileged
// channel: in a click, the page opens a new window of this server's
// /web/shell-request carrying the verb, an id and the ticket, which the
// desktop's shared view takes instead of opening a window
// (src/AutonaWebPanelHost.py: only exactly that, only in a click, only with
// the ticket), runs, and answers with `.shellResult(id, result)`.
//
// The ticket is GET /api/v1/shell-session's, which only the shared view's
// own fetch can read (403 anywhere else). It is kept in this module only --
// never in sessionStorage / localStorage, the URL or a BroadcastChannel --
// and leaves with the page. The window must be opened in the click itself
// (the view takes a user-initiated one only), so the ticket is read before:
// a page that offers shell requests calls loadShellSession() when it mounts
// (useShellAccess({withToken: false}) does).

export const SHELL_SESSION_PATH = "/api/v1/shell-session";
export const SHELL_REQUEST_PATH = "/web/shell-request";
// How long a request waits for its answer. The desktop answers at once (the
// update check answers that it started; its result is its own dialog).
export const SHELL_ANSWER_TIMEOUT_MS = 10000;

export type ShellVerb = "alwaysOnTop" | "checkUpdates" | "openExternal";

// The desktop's answer as it sent it: ok, or the error's code.
export interface ShellResult {
    ok: boolean;
    error?: string;
    // alwaysOnTop's: the window's state after it.
    alwaysOnTop?: boolean;
}

// What the desktop says of itself, on the page's load and every change.
export interface ShellState {
    alwaysOnTop?: boolean;
}

// Why a request got no answer: "no_session" (no ticket read: not the shared
// view, or loadShellSession() not done), "timeout", "superseded" (a newer
// request of the same verb: the desktop keeps each verb's newest answer
// only).
export type ShellRequestError = Error & {code: string};

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface PendingRequest {
    verb: ShellVerb;
    resolve: (result: ShellResult) => void;
    reject: (error: ShellRequestError) => void;
    timer: ReturnType<typeof setTimeout>;
}

let shellTicket: string | null = null;
let shellRequestCount = 0;
const pendingRequests = new Map<string, PendingRequest>();
let currentState: ShellState = {};
const stateListeners = new Set<(state: ShellState) => void>();

function shellRequestError(code: string): ShellRequestError {
    return Object.assign(new Error(`shell request: ${code}`), {code});
}

// Read the ticket (again): resolves once it is held; rejects as the read
// does (api.ts's ApiError: 403 anywhere but the shared view). Only a 403
// drops a ticket already held -- this is not the shared view (any more).
// A failed read (5xx, the network, an answer without a ticket, a page gone
// meanwhile) keeps it: the process's ticket does not change, and dropping it
// would leave a page that read it once unable to ask until it remounts.
export async function loadShellSession(
    live: () => boolean = () => true): Promise<void> {
    let body: Envelope<{ticket?: unknown}>;
    try {
        body = await getWithRetry<Envelope<{ticket?: unknown}>>(
            SHELL_SESSION_PATH, live);
    } catch (error) {
        if (apiErrorStatus(error) === 403) {
            shellTicket = null;
        }
        throw error;
    }
    const ticket = body.data?.ticket;
    if (typeof ticket !== "string" || !ticket) {
        throw new Error("the shell session carried no ticket");
    }
    shellTicket = ticket;
}

export function hasShellSession(): boolean {
    return shellTicket !== null;
}

// [A-Za-z0-9] only: within what the desktop takes as an id
// ([A-Za-z0-9_-]{1,64}).
function nextRequestId(): string {
    shellRequestCount += 1;
    return `r${shellRequestCount.toString(36)}`
        + Math.random().toString(36).slice(2, 10);
}

function settle(id: string): PendingRequest | undefined {
    const pending = pendingRequests.get(id);
    if (pending) {
        pendingRequests.delete(id);
        clearTimeout(pending.timer);
    }
    return pending;
}

// Ask the desktop for `verb` with its arguments. Call it in the click
// itself: the window is opened before this returns. Resolves with the
// desktop's answer; rejects with a ShellRequestError.
export function shellRequest(verb: ShellVerb,
                             args: Record<string, string> = {}):
    Promise<ShellResult> {
    if (shellTicket === null) {
        return Promise.reject(shellRequestError("no_session"));
    }
    const id = nextRequestId();
    // The fixed keys last: an argument cannot stand in for one of them.
    const query = new URLSearchParams(
        {...args, verb, request: id, ticket: shellTicket});
    for (const [otherId, other] of Array.from(pendingRequests)) {
        if (other.verb === verb) {
            settle(otherId);
            other.reject(shellRequestError("superseded"));
        }
    }
    const answer = new Promise<ShellResult>((resolve, reject) => {
        const timer = setTimeout(() => {
            if (settle(id)) {
                reject(shellRequestError("timeout"));
            }
        }, SHELL_ANSWER_TIMEOUT_MS);
        pendingRequests.set(id, {verb, resolve, reject, timer});
    });
    // This server's origin exactly, which the desktop checks. No reference
    // kept (noopener): the window is never made.
    window.open(
        `${window.location.origin}${SHELL_REQUEST_PATH}?${query.toString()}`,
        "_blank", "noopener");
    return answer;
}

// window.autonaPanelShell.shellResult: a request's answer. One this page is
// not waiting for (answered, timed out, superseded, another document's) is
// dropped.
export function receiveShellResult(id: string, result: ShellResult): void {
    settle(id)?.resolve(result);
}

// window.autonaPanelShell.shellState.
export function receiveShellState(state: ShellState): void {
    currentState = {...state};
    stateListeners.forEach((listener) => listener(currentState));
}

export function currentShellState(): ShellState {
    return currentState;
}

// Returns the unsubscribe, for the effect's cleanup.
export function subscribeShellState(
    listener: (state: ShellState) => void): () => void {
    stateListeners.add(listener);
    return () => {
        stateListeners.delete(listener);
    };
}

export function installPanelShellApi(): void {
    window.autonaPanelShell = {
        navigate: navigateToPanel,
        refresh: requestFullState,
        openScoreSets,
        shellResult: receiveShellResult,
        shellState: receiveShellState,
    };
}
