import React from "react";
import {Checkbox, FormControlLabel, FormGroup, Paper, Stack, Typography} from "@mui/material";
import TextField from "./TextField";
import Player from "./Player";
import {Box} from "@mui/system";
import i18n from "../i18n/config";
import {apiPatch} from "../api";
import {useApiAction} from "./Feedback";

/**
 * Team column. Mirrors the desktop Score tab: a team label ("1P" / "TEAM 1"),
 * a team-name input on the team board, the Losers toggle, and the player
 * forms. Every control submits immediately (no submit button).
 *
 * @param {Object} props
 * @param {string} props.teamId
 * @param {string|int} props.autonaTeamId
 * @param {AutonaTeamInfo} props.team
 * @param {number|string} props.scoreboardNumber
 */
export default function Team({teamId, autonaTeamId, team, scoreboardNumber}) {
    const [inLosers, setInLosers] = React.useState(team.losers);
    const [teamName, setTeamName] = React.useState(team.teamName ?? "");
    const {run} = useApiAction();

    React.useEffect(() => {
        setInLosers(team.losers);
    }, [team.losers]);

    React.useEffect(() => {
        setTeamName(team.teamName ?? "");
    }, [team.teamName]);

    // Board 2 is the team-mode board (desktop: teamName only in team mode).
    const isTeamBoard = String(scoreboardNumber) === "2";
    const teamLabel = isTeamBoard
        ? `${i18n.t("team")} ${autonaTeamId}`
        : `${autonaTeamId}P`;

    // PATCH /api/v1/scoreboards/{n}/set takes the team as a number.
    const teamNumber = Number(autonaTeamId);

    const onLosersChanged = (e) => {
        const checked = e.target.checked;
        setInLosers(checked);
        run(() => apiPatch(`/api/v1/scoreboards/${scoreboardNumber}/set`, {
            losers: {team: teamNumber, value: checked},
        }));
    };

    const submitTeamName = () => {
        run(() => apiPatch(`/api/v1/scoreboards/${scoreboardNumber}/set`, {
            teamName: {team: teamNumber, value: teamName},
        }));
    };

    // A team can have no player slots: a board no player card has written
    // (an unbuilt desktop board, written to through the API) carries only
    // the keys written so far. One throw here would blank the whole page.
    const playerWidgets = Object.keys(team.player ?? {}).sort().map((playerKey) => (
        <Player
            key={`${teamId}-p-${playerKey}`}
            teamId={teamId}
            teamKey={playerKey}
            player={team.player[playerKey]}
            scoreboardNumber={scoreboardNumber}
            autonaTeamId={autonaTeamId}
        />
    ));

    return <Paper padding={2} elevation={3}>
        <Box paddingX={1} paddingTop={0.5}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap">
                <Typography variant="overline" fontWeight={700} color="text.secondary">
                    {teamLabel}
                </Typography>
                <FormGroup>
                    <FormControlLabel
                        control={
                            <Checkbox
                                id={teamId + "-losers"}
                                size="small"
                                checked={inLosers}
                                onChange={onLosersChanged}
                            />
                        }
                        label={i18n.t("losers_bracket")}
                    />
                </FormGroup>
            </Stack>
            {isTeamBoard && (
                <Box paddingBottom={1}>
                    <TextField
                        label={i18n.t("team_name")}
                        id={teamId + "-team-name"}
                        fullWidth
                        value={teamName}
                        onChange={(e) => setTeamName(e.target.value)}
                        onBlur={submitTeamName}
                        onKeyDown={(e) => { if (e.key === 'Enter') submitTeamName(); }}
                    />
                </Box>
            )}
        </Box>

        <Stack gap={1} padding={0.5}>
            {playerWidgets}
        </Stack>
    </Paper>
}
