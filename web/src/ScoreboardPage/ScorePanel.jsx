import React from "react";
import {Button, Paper, Stack, Typography} from "@mui/material";
import {SwapHoriz} from "@mui/icons-material";
import i18n from "../i18n/config";

// Desktop SCORE-panel parity: Swap on top, +1 row, the two score displays,
// -1 row, and the danger-styled Reset at the bottom.
//
//   [Swap 1P and 2P]
//   [ +1 ] [ +1 ]
//   [  2 ] [  1 ]   <- gold, monospace
//   [ -1 ] [ -1 ]
//   [ Reset Score ]

function maxWins(bestOf) {
    const n = parseInt(bestOf);
    return (Number.isNaN(n) || n <= 0) ? null : Math.floor(n / 2) + 1;
}

function ScoreDisplay({value, pending}) {
    return (
        <Paper
            variant="outlined"
            sx={(theme) => ({
                flex: 1,
                textAlign: "center",
                paddingY: 0.5,
                backgroundColor: theme.palette.autona.bgSecondary,
                opacity: pending ? 0.7 : 1,
                transition: "opacity 0.15s",
            })}
        >
            <Typography
                sx={{
                    fontFamily: '"JetBrains Mono", monospace',
                    fontWeight: 800,
                    fontSize: 24,
                    color: "autona.gold",
                    lineHeight: 1.2,
                }}
            >
                {value}
            </Typography>
        </Paper>
    );
}

export default function ScorePanel({scoreSubmit, bestOf, onSwap, swapPending, onReset, resetPending}) {
    const {scores, pending, setScores} = scoreSubmit;
    const cap = maxWins(bestOf);

    const bump = (side, delta) => {
        const next = {...scores, [side]: scores[side] + delta};
        if (next[side] < 0) return;
        if (cap !== null && next[side] > cap) return;
        setScores(next);
    };

    return (
        <Paper sx={{padding: 1.5}} elevation={3}>
            <Stack gap={1}>
                <Button
                    variant="outlined"
                    size="small"
                    fullWidth
                    startIcon={<SwapHoriz />}
                    onClick={onSwap}
                    loading={swapPending}
                >
                    {i18n.t("swap_teams")}
                </Button>

                <Stack direction="row" gap={1}>
                    <Button fullWidth variant="outlined" size="small"
                            onClick={() => bump("left", 1)}
                            disabled={cap !== null && scores.left >= cap}>
                        +1
                    </Button>
                    <Button fullWidth variant="outlined" size="small"
                            onClick={() => bump("right", 1)}
                            disabled={cap !== null && scores.right >= cap}>
                        +1
                    </Button>
                </Stack>

                <Stack direction="row" gap={1}>
                    <ScoreDisplay value={scores.left} pending={pending} />
                    <ScoreDisplay value={scores.right} pending={pending} />
                </Stack>

                <Stack direction="row" gap={1}>
                    <Button fullWidth variant="outlined" size="small"
                            onClick={() => bump("left", -1)}
                            disabled={scores.left <= 0}>
                        -1
                    </Button>
                    <Button fullWidth variant="outlined" size="small"
                            onClick={() => bump("right", -1)}
                            disabled={scores.right <= 0}>
                        -1
                    </Button>
                </Stack>

                <Button
                    variant="outlined"
                    size="small"
                    color="error"
                    fullWidth
                    onClick={onReset}
                    loading={resetPending}
                >
                    {i18n.t("reset_scores")}
                </Button>
            </Stack>
        </Paper>
    );
}
