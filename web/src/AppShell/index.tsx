import React from "react";
import {
    Alert,
    Box,
    Divider,
    Drawer,
    IconButton,
    Snackbar,
    useMediaQuery,
} from "@mui/material";
import {ThemeProvider, useTheme} from "@mui/material/styles";
import i18n from "../i18n/config";
import {DARK_TOKENS, themeBeside} from "../themes";
import {PanelShellContext} from "../PanelShell/context";
import {PanelContent, usePanelShellSetup} from "../PanelShell";
import {navigateToPanel} from "../PanelShell/hostApi";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {Header} from "./Header";
import {Nav} from "./Nav";
import {Footer, StatusLine} from "./Footer";
import {iconUrl} from "./navTable";
import {SIDEBAR_WIDTH, Tell, useReport} from "./parts";

// The AppShell (ADR-005 S-final SF-5a-3), routed at /web/app/:panel: the
// panel's page (the same page /web/:panel shows bare, PanelContent) inside
// the Qt main window's frame -- the sidebar's header (tournament, game,
// battle format, modded content), its navigation and its footer (Help &
// Settings, what the app is busy with). Everything goes through /api/v1 and
// the shell requests; the desktop still shows /web/<panel> until SF-5b.
//
// Widths: the sidebar is the Qt one's 196 px, beside the page; the page
// keeps the whole height (the panels' two-pane layouts take 100vh), and its
// breakpoints move by the sidebar's width (themes.js's themeBeside), so its
// layout follows the width it has, as beside the Qt sidebar. Hidden (the
// collapse button; kept in memory only, not saved), a small button at the
// top left brings it back, as in the Qt window, and the status is a small
// box at the bottom left. Below 600 px the sidebar is a drawer over the
// page, opened from a row above it, and the status a line below it.

interface Notice {
    message: string;
    severity: "error" | "info";
    key: number;
}

function Sidebar({panel, go, report, tell, shellAccess, onCollapse,
                  collapseRef, withStatus}: {
    panel: string;
    go: (panel: string) => void;
    report: ReturnType<typeof useReport>;
    tell: Tell;
    shellAccess: ReturnType<typeof useShellAccess>;
    onCollapse: () => void;
    collapseRef: React.Ref<HTMLButtonElement>;
    withStatus: boolean;
}) {
    return (
        <Box component="aside" aria-label={i18n.t("app_sidebar")}
             data-testid="app-sidebar"
             sx={{width: SIDEBAR_WIDTH, height: "100%",
                  display: "flex", flexDirection: "column",
                  bgcolor: DARK_TOKENS.bgSecondary,
                  borderRight: "1px solid", borderColor: "divider"}}>
            <Header go={go} report={report} onCollapse={onCollapse}
                    collapseRef={collapseRef}/>
            <Divider/>
            <Nav panel={panel} go={go}/>
            <Divider/>
            <Footer access={shellAccess.access} retry={shellAccess.retry}
                    go={go} tell={tell} withStatus={withStatus}/>
        </Box>
    );
}

function MenuGlyph() {
    return (
        <Box component="img" src={iconUrl("menu.svg")} alt=""
             sx={{width: 22, height: 22}}/>
    );
}

export default function AppShell() {
    const value = usePanelShellSetup();
    const theme = useTheme();
    // The page's theme beside the sidebar: its breakpoints moved by it.
    const besideTheme = React.useMemo(() => themeBeside(SIDEBAR_WIDTH), []);
    const narrow = useMediaQuery(theme.breakpoints.down("sm"), {noSsr: true});
    const [collapsed, setCollapsed] = React.useState(false);
    const [drawer, setDrawer] = React.useState(false);
    const [notice, setNotice] = React.useState<Notice | null>(null);
    // Whether this is the desktop's own window (the shell request's ticket
    // read): the Help menu's shell requests.
    const shellAccess = useShellAccess({withToken: false});

    // Its own snackbar: the page inside may have its own (FeedbackProvider
    // is one per document, and the page's goes with it).
    const tell: Tell = React.useCallback((message, severity = "error") => {
        setNotice({message, severity, key: Date.now()});
    }, []);
    const report = useReport(
        tell,
        React.useCallback(() => i18n.t("stream_token_required"), []),
        React.useCallback(() => i18n.t("request_failed"), []));

    const go = React.useCallback((panel: string) => {
        setDrawer(false);
        navigateToPanel(panel);
    }, []);

    // Hidden or back, the focus goes to the button that undoes it. (The
    // drawer gives it back to its opener when it closes.)
    const collapseButton = React.useRef<HTMLButtonElement>(null);
    const reopenButton = React.useRef<HTMLButtonElement>(null);
    const focusNext = React.useRef<"reopen" | "collapse" | null>(null);
    React.useEffect(() => {
        const next = focusNext.current;
        focusNext.current = null;
        if (next === "reopen") {
            reopenButton.current?.focus();
        } else if (next === "collapse") {
            collapseButton.current?.focus();
        }
    }, [collapsed]);

    const beside = !narrow && !collapsed;
    const sidebar = (
        <Sidebar panel={value.panel} go={go} report={report} tell={tell}
                 shellAccess={shellAccess}
                 collapseRef={collapseButton}
                 withStatus={beside}
                 onCollapse={() => {
                     if (narrow) {
                         setDrawer(false);
                     } else {
                         focusNext.current = "reopen";
                         setCollapsed(true);
                     }
                 }}/>
    );

    return (
        <PanelShellContext.Provider value={value}>
            <Box sx={{display: "flex", minHeight: "100vh",
                      bgcolor: "background.default"}}>
                {beside && (
                    <Box sx={{flexShrink: 0, position: "sticky", top: 0,
                              alignSelf: "flex-start", height: "100vh"}}>
                        {sidebar}
                    </Box>
                )}
                {narrow && (
                    <Drawer open={drawer} onClose={() => setDrawer(false)}
                            ModalProps={{keepMounted: true}}>
                        {sidebar}
                    </Drawer>
                )}
                <Box data-testid="app-content"
                     sx={{flex: 1, minWidth: 0, display: "flex",
                          flexDirection: "column"}}>
                    {narrow && (
                        <Box sx={(t) => ({
                            position: "sticky", top: 0,
                            zIndex: t.zIndex.appBar,
                            display: "flex", alignItems: "center",
                            minHeight: 44, px: 0.75,
                            bgcolor: DARK_TOKENS.bgSecondary,
                            borderBottom: "1px solid", borderColor: "divider",
                        })}>
                            <IconButton
                                onClick={() => setDrawer(true)}
                                aria-label={i18n.t("app_show_sidebar")}
                                title={i18n.t("app_show_sidebar")}
                                sx={{width: 34, height: 34, borderRadius: "6px"}}
                            >
                                <MenuGlyph/>
                            </IconButton>
                        </Box>
                    )}
                    <Box sx={{flex: 1, minWidth: 0}}>
                        {/* Always the provider (only its theme changes):
                            the page is not mounted again on a collapse. */}
                        <ThemeProvider theme={beside ? besideTheme : theme}>
                            <PanelContent panel={value.panel}/>
                        </ThemeProvider>
                    </Box>
                    {narrow && <StatusLine place="bar"/>}
                </Box>
            </Box>
            {!narrow && collapsed && (
                <>
                    <IconButton
                        ref={reopenButton}
                        onClick={() => {
                            focusNext.current = "collapse";
                            setCollapsed(false);
                        }}
                        aria-label={i18n.t("app_show_sidebar")}
                        title={i18n.t("app_show_sidebar")}
                        sx={(t) => ({
                            position: "fixed", top: 6, left: 6,
                            zIndex: t.zIndex.appBar,
                            width: 34, height: 34, borderRadius: "6px",
                            bgcolor: DARK_TOKENS.bgTertiary,
                            border: "1px solid", borderColor: "divider",
                            "&:hover": {bgcolor: DARK_TOKENS.bgQuaternary},
                        })}
                    >
                        <MenuGlyph/>
                    </IconButton>
                    <StatusLine place="corner"/>
                </>
            )}
            <Snackbar
                key={notice?.key}
                open={notice !== null}
                autoHideDuration={6000}
                onClose={(_event, reason) => {
                    if (reason !== "clickaway") {
                        setNotice(null);
                    }
                }}
                anchorOrigin={{vertical: "bottom", horizontal: "center"}}
            >
                {notice ? (
                    <Alert severity={notice.severity} variant="filled"
                           onClose={() => setNotice(null)}>
                        {notice.message}
                    </Alert>
                ) : undefined}
            </Snackbar>
        </PanelShellContext.Provider>
    );
}
