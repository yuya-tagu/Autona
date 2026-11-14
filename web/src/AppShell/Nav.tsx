import React from "react";
import {Box, List, ListItemButton, ListSubheader} from "@mui/material";
import i18n from "../i18n/config";
import {DARK_TOKENS as tokens} from "../themes";
import {NAV_SECTIONS, iconUrl} from "./navTable";
import {ROW_HEIGHT} from "./parts";

// The AppShell's navigation (ADR-005 S-final SF-5a-3): the Qt sidebar's
// items under their headings, the page shown highlighted (crimson tint,
// brand text). A menu page (Settings, the guides, About, the game assets
// page) is none of them: nothing is highlighted then. An item moves the
// router (hostApi's navigateToPanel: no reload, the URL's query kept).
export function Nav({panel, go}: {
    panel: string;
    go: (panel: string) => void;
}) {
    return (
        <Box component="nav" aria-label={i18n.t("app_navigation")}
             sx={{flex: 1, minHeight: 0, overflowY: "auto", px: 1,
                  pt: 0, pb: "10px"}}>
            {NAV_SECTIONS.map((section) => {
                const headingId = `app-nav-${section.heading}`;
                return (
                    <List key={section.heading} dense disablePadding
                          aria-labelledby={headingId}
                          subheader={(
                              <ListSubheader
                                  id={headingId}
                                  disableSticky
                                  disableGutters
                                  sx={{bgcolor: "transparent",
                                       color: tokens.textMuted,
                                       fontSize: "0.6875rem",
                                       fontWeight: 700,
                                       lineHeight: 1.4,
                                       mt: "10px",
                                       mb: "3px"}}>
                                  {i18n.t(section.heading)}
                              </ListSubheader>
                          )}>
                        {section.items.map((item) => {
                            const current = item.panel === panel;
                            return (
                                <ListItemButton
                                    key={item.panel}
                                    selected={current}
                                    aria-current={current ? "page" : undefined}
                                    onClick={() => go(item.panel)}
                                    sx={{
                                        minHeight: ROW_HEIGHT,
                                        px: 1,
                                        py: 0,
                                        mb: "1px",
                                        gap: 1,
                                        borderRadius: "6px",
                                        color: tokens.textSecondary,
                                        fontSize: "0.8125rem",
                                        "&:hover": {
                                            bgcolor: "rgba(255, 255, 255, 0.06)",
                                        },
                                        "&.Mui-selected, &.Mui-selected:hover": {
                                            bgcolor: "rgba(220, 20, 60, 0.18)",
                                            color: tokens.brand,
                                            fontWeight: 600,
                                        },
                                    }}
                                >
                                    <Box component="img" src={iconUrl(item.icon)}
                                         alt=""
                                         sx={{width: 16, height: 16,
                                              flexShrink: 0}}/>
                                    <Box component="span"
                                         sx={{minWidth: 0, overflow: "hidden",
                                              textOverflow: "ellipsis",
                                              whiteSpace: "nowrap"}}>
                                        {i18n.t(item.label)}
                                    </Box>
                                </ListItemButton>
                            );
                        })}
                    </List>
                );
            })}
        </Box>
    );
}
