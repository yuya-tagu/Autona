import React from "react";
import {useSelector} from "react-redux";
import {Box, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {BrowserOnly, usePanelShell} from "./context";

// The empty page that checks the panel shell itself (ADR-005 S4a): it shows
// what the shell received (`desktop`, `lng`) and whether it is on Socket.IO.
// No panel content, and it writes nothing.
export default function ShellCheckPage() {
    const {desktop, language} = usePanelShell();
    const status = useSelector(
        (state: ReduxState) => state.websocketInfo.status);

    // The tab's title while this page is shown; the previous one is put
    // back when another panel takes over.
    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("panel_shell_check")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    return (
        <Box component="main" sx={{p: 3}}>
            <Stack spacing={1}>
                <Typography variant="h5" component="h1">
                    {i18n.t("panel_shell_check")}
                </Typography>
                <Typography>
                    {i18n.t("panel_shell_mode")}: {desktop
                        ? i18n.t("panel_shell_mode_desktop")
                        : i18n.t("panel_shell_mode_browser")}
                </Typography>
                <Typography>
                    {i18n.t("panel_shell_language")}: {language}
                </Typography>
                <Typography>
                    {i18n.t("panel_shell_connection")}: {status}
                </Typography>
                <BrowserOnly>
                    <Typography>{i18n.t("panel_shell_browser_only")}</Typography>
                </BrowserOnly>
            </Stack>
        </Box>
    );
}
