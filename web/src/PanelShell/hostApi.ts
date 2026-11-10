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

import socketConnection from "../websocketConnection";

type Navigate = (to: string, options?: {replace?: boolean}) => void;
type OpenScoreSets = (board: number) => void;

export interface PanelShellApi {
    navigate(panel: string): void;
    refresh(): void;
    openScoreSets(board: number): void;
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

export function installPanelShellApi(): void {
    window.autonaPanelShell = {
        navigate: navigateToPanel,
        refresh: requestFullState,
        openScoreSets,
    };
}
