import React from "react";
import {Box, Typography} from "@mui/material";
import {useNavigate, useParams, useSearchParams} from "react-router-dom";
import i18n from "../i18n/config";
import websocketInit from "../ScoreboardPage/websocketInit";
import {PanelShellContext} from "./context";
import {registerRouterNavigate} from "./hostApi";
import {panelPage} from "./registry";

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

export default function PanelShell() {
    const {panel = ""} = useParams();
    const [searchParams] = useSearchParams();
    const desktop = searchParams.get("desktop") === "1";
    const language = usePanelLanguage(searchParams.get("lng"));
    const navigate = useNavigate();

    // A layout effect: registered as the router commits, so a navigation
    // the desktop asks for right after the page loads is not missed.
    React.useLayoutEffect(() => registerRouterNavigate(navigate), [navigate]);

    React.useEffect(() => {
        websocketInit();
    }, []);

    const value = React.useMemo(
        () => ({panel, desktop, language}), [panel, desktop, language]);
    const Page = panelPage(panel);

    return (
        <PanelShellContext.Provider value={value}>
            {Page ? <Page/> : <UnknownPanel panel={panel}/>}
        </PanelShellContext.Provider>
    );
}
