import React from "react";
import ShellCheckPage from "./ShellCheckPage";
import CommentaryPage from "../CommentaryPage";
import StreamPage from "../StreamPage";

// The React panels, by the name in their URL (/web/<panel>). Must name the
// same panels as WEB_PANELS in src/core/WebAccess.py, which decides what the
// server serves (tests/test_web_access.py compares the two). "shell-check"
// is the empty page that checks the shell itself (ADR-005 S4a);
// "commentary" the caster line-up (S4b); "stream" 配信設定 (S4e).
export const PANELS: Record<string, React.ComponentType> = {
    "shell-check": ShellCheckPage,
    "commentary": CommentaryPage,
    "stream": StreamPage,
};

export function panelPage(panel: string): React.ComponentType | undefined {
    // Own names only: "constructor" is not a panel.
    return Object.prototype.hasOwnProperty.call(PANELS, panel)
        ? PANELS[panel]
        : undefined;
}
