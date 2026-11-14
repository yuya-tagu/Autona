import React from "react";
import {Box, ButtonBase} from "@mui/material";
import {apiErrorCode, apiErrorStatus} from "../api";
import {iconUrl} from "./navTable";

// What the AppShell's parts share (ADR-005 S-final SF-5a-3): the Qt
// sidebar's measures and its pulldown button, and how a refused request is
// told.

// The Qt sidebar's (src/AutonaSidebar.py): its width, the height of every
// row (nav item, pulldown, format segment) and the gold outline of a pick
// still missing (the tournament, the game).
export const SIDEBAR_WIDTH = 196;
export const ROW_HEIGHT = 26;
export const ATTENTION_COLOR = "#f0a500";

// What a refused write says; null: nothing.
export type Refusal = (status: number | undefined,
                       code: string | undefined) => string | null;

export type Tell = (message: string, severity?: "error" | "info") => void;

// A failed request, told: the access token once (another computer opened
// the page without it: every write after it is refused too), else what
// `refusal` makes of its status and code, else the generic failure.
export type Report = (error: unknown, refusal?: Refusal) => void;

export function useReport(tell: Tell, tokenText: () => string,
                          failedText: () => string): Report {
    const toldToken = React.useRef(false);
    return React.useCallback((error: unknown, refusal?: Refusal) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                tell(tokenText());
            }
            return;
        }
        const message = refusal ? refusal(status, apiErrorCode(error))
            : failedText();
        if (message) {
            tell(message);
        }
    }, [tell, tokenText, failedText]);
}

// The Qt sidebar's three pulldowns (tournament / game / Help & Settings):
// one look -- an icon, the text cut short when too long, the chevron -- and
// the gold outline while the pick is missing.
export const Pulldown = React.forwardRef<HTMLButtonElement, {
    icon: string;
    text: string;
    attention?: boolean;
    menuId: string;
    open: boolean;
    onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
    testId: string;
}>(function Pulldown({icon, text, attention = false, menuId, open, onClick,
                      testId}, ref) {
    return (
        <ButtonBase
            ref={ref}
            onClick={onClick}
            data-testid={testId}
            data-attention={attention ? "true" : "false"}
            aria-haspopup="menu"
            aria-controls={open ? menuId : undefined}
            aria-expanded={open ? "true" : undefined}
            title={text}
            sx={{
                width: "100%",
                minHeight: ROW_HEIGHT,
                justifyContent: "flex-start",
                gap: 1,
                pl: "9px",
                pr: "6px",
                borderRadius: "6px",
                border: "1px solid",
                borderColor: attention ? ATTENTION_COLOR : "transparent",
                bgcolor: "rgba(255, 255, 255, 0.06)",
                color: "text.primary",
                fontSize: "0.8125rem",
                "&:hover": {bgcolor: "rgba(255, 255, 255, 0.10)"},
                "&.Mui-focusVisible": {outline: "2px solid",
                                       outlineColor: "primary.main"},
            }}
        >
            <Box component="img" src={iconUrl(icon)} alt=""
                 sx={{width: 16, height: 16, flexShrink: 0}}/>
            <Box component="span" sx={{flex: 1, minWidth: 0, textAlign: "left",
                                       overflow: "hidden",
                                       textOverflow: "ellipsis",
                                       whiteSpace: "nowrap"}}>
                {text}
            </Box>
            <Box component="img" src={iconUrl("chevron_down.svg")} alt=""
                 sx={{width: 12, height: 12, flexShrink: 0, opacity: 0.8}}/>
        </ButtonBase>
    );
});
