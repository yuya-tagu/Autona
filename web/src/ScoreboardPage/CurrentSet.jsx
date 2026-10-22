import React from "react";
import {
    Button,
    Card,
    CardContent,
    Collapse, Container, Divider, IconButton,
    Typography, useMediaQuery
} from "@mui/material";
import i18n from "../i18n/config";

import MatchInfoBar from "./MatchInfoBar";
import ScorePanel from "./ScorePanel";
import Team from "./Team";
import {ExpandMore} from "@mui/icons-material";
import {useTheme} from "@mui/material/styles";
import {apiPost} from "../api";
import {useApiAction} from "./Feedback";
import {useSelector} from "react-redux";
import {Grid} from "@mui/system";

/**
 * Current-set card, mirroring the desktop Score tab: the match metadata bar
 * (Phase / Match / Best Of + X-post) on top, then Team 1 | score panel |
 * Team 2. Everything submits immediately — there is no submit button.
 */
export default function CurrentSet({scoreboardNumber, scoreSubmit}) {
    const [expanded, setExpanded] = React.useState(true);

    const autonaState = useSelector(state => state.autonaState.autonaState);
    const theme = useTheme();
    const isSmall = useMediaQuery(theme.breakpoints.down('lg'));

    /** @type {AutonaScoreInfo} */
    const score = autonaState?.score[scoreboardNumber];

    const {pending: resetPending, run: runReset} = useApiAction();
    const {pending: swapPending, run: runSwap} = useApiAction();

    const resetScores = () => {
        runReset(() => apiPost(`/api/v1/scoreboards/${scoreboardNumber}/score:reset`));
    }

    const swapTeams = () => {
        runSwap(() => apiPost(`/api/v1/scoreboards/${scoreboardNumber}:swap-teams`));
    }

    const postToX = () => {
        const tournamentName = autonaState?.tournamentInfo?.tournamentName || "";
        const phase = [score?.phase, score?.pool].filter(Boolean).join(" - ");
        const match = score?.match || "";
        const streamUrl = autonaState?.stream?.url || "";

        const getTeamLine = (teamKey) => {
            const team = score?.team?.[teamKey];
            if (!team?.player) return "???";
            const players = Object.keys(team.player).sort().map(k => {
                const p = team.player[k];
                const name = (p.name || "").trim();
                if (!name) return null;
                const chars = p.character ? Object.keys(p.character).sort().map(ck =>
                    p.character[ck]?.name
                ).filter(Boolean) : [];
                let entry = name;
                if (chars.length > 0) entry += `(${chars.join("/")})`;
                if (Number(p.seed) > 0) entry += ` [Seed:${p.seed}]`;
                return entry;
            }).filter(Boolean);
            return players.length > 0 ? players.join(" / ") : "???";
        };

        const teamKeys = Object.keys(score?.team || {}).sort();
        const parts = [];
        if (tournamentName) parts.push(tournamentName);
        const roundParts = [];
        if (phase) roundParts.push(`[${phase}]`);
        if (match) roundParts.push(match);
        if (roundParts.length > 0) parts.push(roundParts.join(" - "));
        parts.push("");
        parts.push(getTeamLine(teamKeys[0]));
        parts.push("vs");
        parts.push(getTeamLine(teamKeys[1]));
        parts.push("");
        if (streamUrl) parts.push(`\u{1F534}STREAM: ${streamUrl}`);

        const text = parts.join("\n");
        const url = "https://twitter.com/intent/tweet?text=" + encodeURIComponent(text);
        window.open(url, "_blank");
    };

    const hasSuitableTeamCount =
        !!score
        && !!score.team
        && Object.keys(score.team).length >= 2;

    if (!hasSuitableTeamCount) {
        return <Typography sx={{typography: isSmall ? "h7": "h5"}}>
            {i18n.t("no_set")}
        </Typography>
    }

    const teamKeys = Object.keys(score.team).sort()
    const teams = [score.team[teamKeys[0]], score.team[teamKeys[1]]];

    return (<>
        <span style={{position: 'relative', float: 'right'}}>
                <IconButton
                    title={i18n.t("expand_scoreboard")}
                    size="small"
                    sx={{
                        position: 'absolute',
                        top: "-48px",
                        right: "0px",
                    }}
                    onClick={() => setExpanded(!expanded)}
                >
                    <ExpandMore sx={{
                        transform: expanded ? 'rotate(0deg)' : 'rotate(270deg)'
                    }} />
                </IconButton>
            </span>
        <Card>
            <Collapse in={expanded} timeout={"auto"}>
                <CardContent sx={{padding: 1, '&:last-child': {paddingBottom: 1}}}>
                    {/* Desktop scoreBar parity: match metadata above the
                        columns, X-post on the row's trailing edge. */}
                    <MatchInfoBar
                        scoreboardNumber={scoreboardNumber}
                        phase={score.phase}
                        pool={score.pool}
                        match={score.match}
                        bestOf={score.best_of}
                        action={
                            <Button variant="outlined" size="small" onClick={postToX}>
                                {i18n.t("post_to_x")}
                            </Button>
                        }
                    />
                    <Divider sx={{marginY: 1}} />
                    <Grid
                        container
                        direction={"row"}
                        spacing={1}
                        alignItems={"stretch"}
                        justifyContent={"space-evenly"}
                    >
                        {/* Phones (xs): the two player columns sit side by
                            side (6+6) on top, with the score panel full-width
                            below them. sm..lg stacks full-width in source
                            order; lg+ is the desktop 3-column layout. */}
                        <Grid
                            size={{xs: 6, sm: 12, lg: 4}}
                            sx={{order: {xs: 1, sm: 0}, minWidth: 0}}
                        >
                            <Container disableGutters maxWidth={'sm'}>
                                <Team
                                    key={`s-${scoreboardNumber}-t-${teamKeys[0]}`}
                                    teamId={`s-${scoreboardNumber}-t-${teamKeys[0]}`}
                                    autonaTeamId={teamKeys[0]}
                                    team={teams[0]}
                                    scoreboardNumber={scoreboardNumber}
                                />
                            </Container>
                        </Grid>

                        <Grid size={{xs: 12, lg: 4}} sx={{order: {xs: 3, sm: 0}}}>
                            <Container disableGutters maxWidth={'xs'}>
                                <ScorePanel
                                    scoreSubmit={scoreSubmit}
                                    bestOf={score.best_of}
                                    onSwap={swapTeams}
                                    swapPending={swapPending}
                                    onReset={resetScores}
                                    resetPending={resetPending}
                                />
                            </Container>
                        </Grid>

                        <Grid size={{xs: 6, sm: 12, lg: 4}} sx={{order: {xs: 2, sm: 0}, minWidth: 0}}>
                            <Container disableGutters maxWidth={'sm'}>
                                <Team key={`s-${scoreboardNumber}-t-${teamKeys[1]}`}
                                      teamId={`s-${scoreboardNumber}-t-${teamKeys[1]}`}
                                      autonaTeamId={teamKeys[1]}
                                      team={teams[1]}
                                      scoreboardNumber={scoreboardNumber}
                                />
                            </Container>
                        </Grid>
                    </Grid>
                </CardContent>
            </Collapse>
        </Card>
    </>)
}
