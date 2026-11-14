import React from "react";
import {
    Box,
    Divider,
    LinearProgress,
    ListItemIcon,
    ListItemText,
    ListSubheader,
    Menu,
    MenuItem,
    Typography,
} from "@mui/material";
import CheckIcon from "@mui/icons-material/Check";
import i18n from "../i18n/config";
import {DARK_TOKENS} from "../themes";
import {copyText} from "../PanelShell/clipboard";
import {usePanelShell} from "../PanelShell/context";
import {
    ShellRequestError,
    ShellResult,
    ShellVerb,
    currentShellState,
    shellRequest,
    subscribeShellState,
} from "../PanelShell/hostApi";
import {ShellAccess} from "../PanelShell/useShellAccess";
import {statusText, useAppStatus} from "../PanelShell/useAppStatus";
import {BUSINESS_INQUIRY_EMAIL, BUSINESS_INQUIRY_URL, DISCORD_URL} from "./externalLinks";
import {iconUrl} from "./navTable";
import {Pulldown, SIDEBAR_WIDTH, Tell} from "./parts";

// The AppShell's footer (ADR-005 S-final SF-5a-3): the Qt sidebar's
// "Help & Settings" menu, in its order, and what the app is busy with (the
// Qt window's status bar: core's StatusService through useAppStatus, a
// progress bar when the job counts).
//
// In the desktop's own window (`access` "shell": the shell request's ticket
// was read) the menu asks the desktop itself for what only it can do
// (hostApi's shellRequest, in the click): its "always on top" (shown checked
// as the desktop says: shellState), the update check, the two outside
// addresses. Anywhere else -- a browser, the headless app -- the first two
// are not offered, and the addresses are plain links (Discord in a new tab,
// the inquiry as mailto:). The desktop's view whose read failed (not known
// to be the desktop's: no shell request) cannot hand a mailto: on, so the
// inquiry is its address to copy there, as on the About page; opening the
// menu reads again.

function MenuIcon({icon}: {icon?: string}) {
    return (
        <ListItemIcon>
            {icon && <Box component="img" src={iconUrl(icon)} alt=""
                          sx={{width: 16, height: 16}}/>}
        </ListItemIcon>
    );
}

function useAlwaysOnTop(): boolean {
    const [on, setOn] = React.useState(
        () => Boolean(currentShellState().alwaysOnTop));
    React.useEffect(() => {
        setOn(Boolean(currentShellState().alwaysOnTop));
        return subscribeShellState((state) => setOn(Boolean(state.alwaysOnTop)));
    }, []);
    return on;
}

// Where the status shows: the sidebar's footer; a small box at the bottom
// left while the sidebar is hidden (the window's corner, as the button that
// brings it back is at the top left); a line at the bottom of the page
// below 600 px (the sidebar is a drawer there). One of them at a time: the
// page has one live region for it.
export type StatusPlace = "footer" | "corner" | "bar";

export function StatusLine({place}: {place: StatusPlace}) {
    const {current} = useAppStatus();
    const said = statusText(current);
    const total = current ? current.total : 0;
    const counted = current !== null && total > 0;
    const placed = {
        footer: {pt: said ? 1 : 0},
        corner: {position: "fixed", left: 6, bottom: 6,
                 width: SIDEBAR_WIDTH - 12, zIndex: 1100},
        bar: {position: "sticky", bottom: 0, zIndex: 1100},
    } as const;
    // The corner's and the bar's frame only while there is something to say:
    // the region itself stays, empty, for the next thing said.
    const framed = said && place !== "footer" ? {
        px: 1.5, py: 1,
        bgcolor: DARK_TOKENS.bgSecondary,
        border: "1px solid", borderColor: "divider",
        borderRadius: place === "corner" ? "6px" : 0,
        ...(place === "bar" ? {borderWidth: "1px 0 0 0"} : {}),
    } : {};
    return (
        <Box role="status" aria-label={i18n.t("app_status")}
             data-testid="app-status" data-place={place}
             sx={{display: "flex", flexDirection: "column", gap: "4px",
                  ...placed[place], ...framed}}>
            {said && (
                <Typography variant="caption" color="text.secondary"
                            title={said}
                            sx={place === "bar" ? {
                                overflow: "hidden", textOverflow: "ellipsis",
                                whiteSpace: "nowrap", lineHeight: 1.4,
                            } : {
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                                overflow: "hidden",
                                lineHeight: 1.4,
                            }}>
                    {said}
                </Typography>
            )}
            {counted && (
                <LinearProgress
                    variant="determinate"
                    value={Math.min(100, (Math.min(current.done, total)
                                          / total) * 100)}
                    aria-label={said}
                    sx={{height: place === "bar" ? 4 : 6, borderRadius: 3}}
                />
            )}
        </Box>
    );
}

export function Footer({access, retry, go, tell, withStatus}: {
    access: ShellAccess;
    // Reads `access` again (it failed).
    retry: () => void;
    go: (panel: string) => void;
    tell: Tell;
    // The status in the footer (the sidebar beside the page); else the
    // AppShell shows it elsewhere (StatusPlace).
    withStatus: boolean;
}) {
    const {desktop} = usePanelShell();
    const [anchor, setAnchor] = React.useState<HTMLButtonElement | null>(null);
    const [menu, setMenu] = React.useState(false);
    const onTop = useAlwaysOnTop();
    const shell = access === "shell";

    const open = (panel: string) => {
        setMenu(false);
        go(panel);
    };

    // In the click itself: the request opens its window before this returns.
    const ask = (verb: ShellVerb, args: Record<string, string> = {}) => {
        setMenu(false);
        shellRequest(verb, args).then((result: ShellResult) => {
            if (result.ok) {
                return;
            }
            // Answered: the desktop could not (or is checking already).
            if (verb === "checkUpdates" && result.error === "busy") {
                tell(i18n.t("app_updates_busy"), "info");
            } else {
                tell(i18n.t("app_shell_not_done"));
            }
        }, (error: ShellRequestError) => {
            // A newer request of the verb took its place: that one answers.
            if (error.code === "superseded") {
                return;
            }
            tell(error.code === "no_session" ? i18n.t("app_shell_unavailable")
                : i18n.t("app_shell_failed"));
        });
    };

    const copyInquiry = () => {
        setMenu(false);
        copyText(BUSINESS_INQUIRY_EMAIL).then((copied) => {
            tell(i18n.t(copied ? "guide_copied" : "guide_copy_failed"),
                 copied ? "info" : "error");
        });
    };

    // The addresses: asked of the desktop in its window; links elsewhere
    // (not while it is not known yet which: neither would work then).
    const outside = (target: "discord" | "inquiry", href: string,
                     label: string) => {
        if (shell) {
            return (
                <MenuItem onClick={() => ask("openExternal", {target})}>
                    <MenuIcon/>
                    <ListItemText>{label}</ListItemText>
                </MenuItem>
            );
        }
        if (target === "inquiry" && desktop && access === "failed") {
            return (
                <MenuItem onClick={copyInquiry}>
                    <MenuIcon/>
                    <ListItemText primary={label}
                                  secondary={BUSINESS_INQUIRY_EMAIL}/>
                    <Typography variant="caption" color="text.secondary"
                                sx={{ml: 1}}>
                        {i18n.t("guide_copy")}
                    </Typography>
                </MenuItem>
            );
        }
        const linkProps = target === "discord"
            ? {target: "_blank", rel: "noopener noreferrer"} : {};
        return (
            <MenuItem component="a" href={href} {...linkProps}
                      disabled={access === "checking"}
                      onClick={() => setMenu(false)}>
                <MenuIcon/>
                <ListItemText>{label}</ListItemText>
            </MenuItem>
        );
    };

    return (
        <Box sx={{px: 1, py: "10px"}}>
            <Pulldown
                ref={setAnchor}
                icon="settings.svg"
                text={i18n.t("app_help_settings")}
                menuId="app-help-menu"
                open={menu}
                onClick={() => {
                    // The read failed (the app was starting, ...): again.
                    if (access === "failed") {
                        retry();
                    }
                    setMenu(true);
                }}
                testId="app-help"
            />
            <Menu id="app-help-menu" anchorEl={anchor} open={menu}
                  onClose={() => setMenu(false)}
                  anchorOrigin={{vertical: "top", horizontal: "left"}}
                  transformOrigin={{vertical: "bottom", horizontal: "left"}}>
                <MenuItem onClick={() => open("settings")}>
                    <MenuIcon icon="settings.svg"/>
                    <ListItemText>{i18n.t("app_settings")}</ListItemText>
                </MenuItem>
                {shell && (
                    <MenuItem role="menuitemcheckbox" aria-checked={onTop}
                              onClick={() => ask("alwaysOnTop",
                                                 {on: onTop ? "0" : "1"})}>
                        <ListItemIcon>
                            {onTop ? <CheckIcon fontSize="small"/> : null}
                        </ListItemIcon>
                        <ListItemText>{i18n.t("app_always_on_top")}</ListItemText>
                    </MenuItem>
                )}
                <Divider/>
                <MenuItem onClick={() => open("assets")}>
                    <MenuIcon/>
                    <ListItemText>{i18n.t("app_add_game_assets")}</ListItemText>
                </MenuItem>
                <Divider/>
                <ListSubheader disableSticky
                               sx={{lineHeight: "32px", bgcolor: "transparent"}}>
                    {i18n.t("app_api_settings")}
                </ListSubheader>
                <MenuItem onClick={() => open("api")}>
                    <MenuIcon/>
                    <ListItemText>{i18n.t("app_open_reference")}</ListItemText>
                </MenuItem>
                <MenuItem onClick={() => open("companion")}>
                    <MenuIcon/>
                    <ListItemText>{i18n.t("app_companion_settings")}</ListItemText>
                </MenuItem>
                <MenuItem onClick={() => open("obs")}>
                    <MenuIcon/>
                    <ListItemText>{i18n.t("app_obs_settings")}</ListItemText>
                </MenuItem>
                <Divider/>
                {shell && (
                    <MenuItem onClick={() => ask("checkUpdates")}>
                        <MenuIcon icon="update_circle.svg"/>
                        <ListItemText>{i18n.t("app_check_updates")}</ListItemText>
                    </MenuItem>
                )}
                {outside("discord", DISCORD_URL, i18n.t("app_discord"))}
                {outside("inquiry", BUSINESS_INQUIRY_URL, i18n.t("app_inquiry"))}
                <MenuItem onClick={() => open("about")}>
                    <MenuIcon/>
                    <ListItemText>{i18n.t("app_about")}</ListItemText>
                </MenuItem>
            </Menu>
            {withStatus && <StatusLine place="footer"/>}
        </Box>
    );
}
