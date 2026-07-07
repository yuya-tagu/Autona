import React from "react";
import {IconButton, Paper, Stack, Typography} from "@mui/material";
import {Add, Remove, SwapHoriz} from "@mui/icons-material";
import {apiGet} from "../api";
import {useApiAction} from "./Feedback";
import i18n from "../i18n/config";

// Phone-only (xs) bottom-fixed score bar: one-handed +/- for both teams
// plus swap, without scrolling to the set card. Shares the page-level
// useScoreSubmit instance with CurrentSet so both views show the same
// optimistic value.
//
//   [P1] [-] 2 [+]  ⇄  [-] 1 [+] [P2]

function maxWins(bestOf) {
    const n = parseInt(bestOf);
    return (Number.isNaN(n) || n <= 0) ? null : Math.floor(n / 2) + 1;
}

function teamLabel(team) {
    if (!team) return "—";
    if (team.teamName) return team.teamName;
    const keys = Object.keys(team.player || {}).sort();
    return team.player?.[keys[0]]?.name || "—";
}

export default function ScoreControlBar({scoreboardNumber, score, teamKeys, scoreSubmit}) {
    const {pending: swapPending, run: runSwap} = useApiAction();

    if (!score?.team || teamKeys.length < 2) {
        return null;
    }

    const {scores, pending, setScores} = scoreSubmit;
    const cap = maxWins(score.best_of);

    const bump = (side, delta) => {
        const next = {...scores, [side]: scores[side] + delta};
        if (next[side] < 0) return;
        if (cap !== null && next[side] > cap) return;
        setScores(next);
    };

    const swapTeams = () => {
        runSwap(() => apiGet(`/scoreboard${scoreboardNumber}-swap-teams`));
    };

    const scoreSx = {
        fontFamily: '"JetBrains Mono", monospace',
        fontWeight: 800,
        fontSize: 22,
        minWidth: 28,
        textAlign: "center",
        transition: "color 0.15s",
        color: pending ? "autona.gold" : "text.primary",
    };
    const labelSx = {
        flex: 1,
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        color: "text.secondary",
        fontWeight: 700,
    };

    return (
        <Paper
            elevation={8}
            square
            sx={(theme) => ({
                position: "fixed",
                bottom: 0,
                left: 0,
                right: 0,
                display: {xs: "block", sm: "none"},
                zIndex: theme.zIndex.appBar,
                borderTop: `1px solid ${theme.palette.divider}`,
                backgroundColor: theme.palette.autona.bgSecondary,
                paddingBottom: "env(safe-area-inset-bottom)",
            })}
        >
            <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                gap={0.5}
                paddingX={1}
                paddingY={0.5}
            >
                <Typography variant="body2" sx={labelSx}>
                    {teamLabel(score.team[teamKeys[0]])}
                </Typography>

                <IconButton size="small" onClick={() => bump("left", -1)} disabled={scores.left <= 0}>
                    <Remove />
                </IconButton>
                <Typography sx={scoreSx}>{scores.left}</Typography>
                <IconButton size="small" onClick={() => bump("left", 1)} disabled={cap !== null && scores.left >= cap}>
                    <Add />
                </IconButton>

                <IconButton
                    size="small"
                    onClick={swapTeams}
                    disabled={swapPending}
                    title={i18n.t("swap_teams")}
                >
                    <SwapHoriz />
                </IconButton>

                <IconButton size="small" onClick={() => bump("right", -1)} disabled={scores.right <= 0}>
                    <Remove />
                </IconButton>
                <Typography sx={scoreSx}>{scores.right}</Typography>
                <IconButton size="small" onClick={() => bump("right", 1)} disabled={cap !== null && scores.right >= cap}>
                    <Add />
                </IconButton>

                <Typography variant="body2" sx={{...labelSx, textAlign: "right"}}>
                    {teamLabel(score.team[teamKeys[1]])}
                </Typography>
            </Stack>
        </Paper>
    );
}
