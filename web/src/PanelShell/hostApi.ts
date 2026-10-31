// What the desktop app's embedded view calls on the panel pages (ADR-005 S4a).
//
// The desktop shows every panel moved to React in one shared web view
// (src/AutonaWebPanelHost.py). Picking another of them in its sidebar does
// not reload the view: it runs `window.autonaPanelShell.navigate(panel)`,
// and this router moves to `/web/<panel>`. When the view is on screen again
// after being frozen, it runs `.refresh()`, which asks the server for the
// full state (deltas broadcast meanwhile may be lost). Both are harmless in
// a browser, where nothing calls them.

import socketConnection from "../websocketConnection";

type Navigate = (to: string, options?: {replace?: boolean}) => void;

export interface PanelShellApi {
    navigate(panel: string): void;
    refresh(): void;
}

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

export function navigateToPanel(panel: string): void {
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
    };
}
