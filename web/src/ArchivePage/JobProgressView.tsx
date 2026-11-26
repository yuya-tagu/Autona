import React from "react";
import {Box, Button, LinearProgress, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {DARK_TOKENS as tokens} from "../themes";
import {etaParts} from "./archiveSets";

// Lives with the archive page, whose job it first drew; the plugins page
// (PluginsPage/RunView.tsx) uses it too.
// A running job's progress: its stage line, the bar, the remaining time, the
// latest log lines and the stop button. Shared by the archive page's job and
// the plugins page's run (ADR-009 D4), so both look and read the same.
// `line` is the stage line already worded; null shows "Running".
export default function JobProgressView({line, overall, eta, logs, readOnly,
                                         onCancel}: {
    line: string | null;
    overall: number | null | undefined;
    eta: number | null | undefined;
    logs: string[];
    readOnly: boolean;
    onCancel: () => void;
}) {
    const remaining = etaParts(eta);
    return (
        <Stack spacing={1} sx={{p: 2}}>
            <Typography>{line ?? i18n.t("archive_state_running")}</Typography>
            <LinearProgress variant="determinate"
                            value={Math.min(100, Math.max(
                                0, Math.round((overall ?? 0) * 100)))}/>
            {remaining ? (
                <Typography variant="body2" sx={{color: tokens.textMuted}}>
                    {remaining.min > 0
                        ? i18n.t("archive_eta_min", {
                            min: remaining.min,
                            sec: String(remaining.sec).padStart(2, "0")})
                        : i18n.t("archive_eta_sec", {sec: remaining.sec})}
                </Typography>
            ) : null}
            {logs.length > 0 ? (
                <Box component="pre" aria-label={i18n.t("archive_logs")}
                     sx={{m: 0, fontSize: "0.75rem", whiteSpace: "pre-wrap",
                          color: tokens.textMuted}}>
                    {logs.join("\n")}
                </Box>
            ) : null}
            <Box>
                <Button color="error" variant="outlined" disabled={readOnly}
                        onClick={onCancel}>
                    {i18n.t("archive_abort")}
                </Button>
            </Box>
        </Stack>
    );
}
