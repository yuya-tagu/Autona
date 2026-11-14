import React from "react";
import {Box, Typography, useTheme} from "@mui/material";
import {useNavigate, useParams, useSearchParams} from "react-router-dom";
import i18n from "../i18n/config";
import websocketInit from "../ScoreboardPage/websocketInit";
import {PanelShellContext, PanelShellValue} from "./context";
import {registerRouterNavigate} from "./hostApi";
import {panelPage} from "./registry";
import "./panelScrollbar.css";

// The shell every React panel page runs in (ADR-005 S4a), routed at
// /web/:panel (index.js). It
// - takes `desktop=1` (the desktop app's embedded view) and `lng=` (the
//   desktop's UI language) off the URL and hands them to the page
//   (usePanelShell(); BrowserOnly hides browser-only parts on the desktop),
// - puts `lng` into i18next and <html lang> (Japanese glyphs and IME),
// - gives the desktop's view its router (hostApi: navigate between panels
//   without a reload),
// - starts the Socket.IO state, like the scoreboard page.
// Writes go through /api/v1 like any other page: from this PC without the
// token, from another computer with it (?token=, autonaToken.ts).
//
// The same set-up (usePanelShellSetup) runs the AppShell at /web/app/:panel
// (ADR-005 S-final SF-5a-3, ../AppShell): the same page (PanelContent),
// inside the navigation, the header and the footer.

function usePanelLanguage(lng: string | null): string {
    const [language, setLanguage] = React.useState(i18n.language);

    React.useEffect(() => {
        const onChange = (next: string) => setLanguage(next);
        i18n.on("languageChanged", onChange);
        return () => i18n.off("languageChanged", onChange);
    }, []);

    React.useEffect(() => {
        if (lng && i18n.language !== lng) {
            i18n.changeLanguage(lng);
        }
    }, [lng]);

    React.useEffect(() => {
        if (language) {
            document.documentElement.lang = language;
        }
    }, [language]);

    return language;
}

function UnknownPanel({panel}: {panel: string}) {
    return (
        <Box component="main" sx={{p: 3}}>
            <Typography>{i18n.t("panel_unknown", {panel})}</Typography>
        </Box>
    );
}

// What both shells do: the URL's panel, `desktop` and `lng`, the language,
// the scrollbar, the router the desktop's view drives, the Socket.IO state.
// Returns what the shell gives its page (PanelShellContext).
export function usePanelShellSetup(): PanelShellValue {
    const {panel = ""} = useParams();
    const [searchParams] = useSearchParams();
    const desktop = searchParams.get("desktop") === "1";
    const language = usePanelLanguage(searchParams.get("lng"));
    const navigate = useNavigate();
    const textColor = useTheme().palette.text.primary;

    // Scrollbar styling (panelScrollbar.css) hangs on html.panel-shell, and
    // takes its color from <html>'s text color; both undone on unmount.
    React.useEffect(() => {
        const root = document.documentElement;
        const previous = root.style.color;
        root.classList.add("panel-shell");
        root.style.color = textColor;
        return () => {
            root.classList.remove("panel-shell");
            root.style.color = previous;
        };
    }, [textColor]);

    // A layout effect: registered as the router commits, so a navigation
    // the desktop asks for right after the page loads is not missed.
    React.useLayoutEffect(() => registerRouterNavigate(navigate), [navigate]);

    React.useEffect(() => {
        websocketInit();
    }, []);

    return React.useMemo(
        () => ({panel, desktop, language}), [panel, desktop, language]);
}

// The panel's page, or what says there is none by that name.
export function PanelContent({panel}: {panel: string}) {
    const Page = panelPage(panel);
    return Page ? <Page/> : <UnknownPanel panel={panel}/>;
}

export default function PanelShell() {
    const value = usePanelShellSetup();
    return (
        <PanelShellContext.Provider value={value}>
            <PanelContent panel={value.panel}/>
        </PanelShellContext.Provider>
    );
}
