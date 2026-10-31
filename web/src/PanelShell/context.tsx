import React from "react";

// What the panel shell tells the panel pages (ADR-005 S4a).
export interface PanelShellValue {
    // The panel's name in the URL (/web/<panel>).
    panel: string;
    // Opened in the desktop app's embedded view (`?desktop=1`), not in a
    // browser.
    desktop: boolean;
    // i18next's current language (`?lng=`, the desktop's UI language).
    language: string;
}

export const PanelShellContext = React.createContext<PanelShellValue>({
    panel: "",
    desktop: false,
    language: "en",
});

export function usePanelShell(): PanelShellValue {
    return React.useContext(PanelShellContext);
}

// Rendered in a browser only: what the desktop's embedded view has no use
// for ("open in a browser" and the like).
export function BrowserOnly({children}: {children: React.ReactNode}) {
    const {desktop} = usePanelShell();
    return desktop ? null : <>{children}</>;
}
