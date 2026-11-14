import React from "react";
import {Button} from "@mui/material";
import OpenInNew from "@mui/icons-material/OpenInNew";
import i18n from "../i18n/config";
import {usePanelShell} from "../PanelShell/context";

// "Open in the default browser" (ADR-005 S-final SF-4a-2): the guides were
// windows of their own beside the main window until SF-4a-2, so a guide
// could stay open while the operator worked; as a page of the shared view
// it takes the main window's place, and this opens it beside it. In the
// desktop's shared view only (a browser has it open already). The URL is
// the page's own: no `desktop` (the browser's page is a browser's) and no
// token -- the shared view hands its user-initiated http(s) window.open to
// the system browser (src/AutonaWebPanelHost.py's _OpenOutside).
export function browserUrl(panel: string, language: string): string {
    return `${window.location.origin}/web/${panel}`
        + `?lng=${encodeURIComponent(language)}`;
}

export default function OpenInBrowserButton({panel}: {panel: string}) {
    const {desktop, language} = usePanelShell();
    if (!desktop) {
        return null;
    }
    return (
        <Button variant="outlined" size="small" startIcon={<OpenInNew/>}
                onClick={() => window.open(browserUrl(panel, language),
                                           "_blank", "noopener")}>
            {i18n.t("guide_open_in_browser")}
        </Button>
    );
}
