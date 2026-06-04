import "../App.css";
import {darkTheme} from "../themes";
import i18n from "../i18n/config";
import {
    Button,
    Paper, Stack,
} from "@mui/material";
import React from "react";
import {Box} from "@mui/system";

import CurrentSet from "./CurrentSet";
import UpcomingSets from "./UpcomingSets";
import {Header} from "./Header";
import {ReduxState} from "../redux/store"
import {shallowEqual, useDispatch, useSelector} from "react-redux";
import {tshStateSlice} from "../redux/tshState";
import websocketInit from "./websocketInit";
import websocketConnection from "../websocketConnection";
import {BACKEND_PORT} from "../env";

// scoreboardNumber selects which board this page edits: 1 = SCORE (1v1),
// 2 = TEAM (team mode). Routed via `/scoreboard` (default 1) and `/team`
// (2) in index.js. Defaults to 1 for backward compatibility.

/**
 * Main page for the scoreboard. This whole contraption is powered by TSH's python-side
 * program state. In order to do that, we subscribe to updates that get sent out and update
 * our state piecemeal. Each update has a number so that we can tell if our updates are stale
 * or out of order and request a full state send-over.
 */
export default function ScoreboardPage(props: any) {
    const SCOREBOARD_ID = props.scoreboardNumber ?? 1;
    const dispatch = useDispatch();
    const {loading, errored} = useSelector((state: ReduxState) => ({
        loading: (
            state.tshState.initializing
            || state.tshCharacters.initializing
            || state.tshPlayers.initializing
            || state.tshGames.initializing
            || state.tshCountries.initializing
        ),
        errored: state.websocketInfo.status === "errored",
    }), shallowEqual);

    React.useEffect(() => {
        document.title = `TSH ${i18n.t("scoreboard")}`;
        websocketInit();
    }, []);

    let body;
    const connectionError = (
        <Paper key="connection_error" elevation={2} sx={{padding: '8px'}}>
            <div>{i18n.t("failed_to_connect")}</div>
        </Paper>
    );
    const loadingEl = (
        <Paper key="loading" elevation={2} sx={{padding: '8px'}}>
            <div>{i18n.t("loading")}</div>
        </Paper>
    );

    const onSelectedSetChanged = () => {
        dispatch(tshStateSlice.actions.loadingNewData({}));
    };

    const onSelectedGameChanged= (newGame: string) => {
        websocketConnection.instance().emit('update_game', {codename: newGame});
    };

    if (errored) {
        body = connectionError;
    } else if (loading) {
        body = loadingEl;
    } else {
        body = (
            // Extra margin at the bottom allows for mobile users to see the bottom of the page better.
            <>
                <Header onSelectedGameChange={onSelectedGameChanged} scoreboardNumber={SCOREBOARD_ID}/>
                <Box
                    paddingX={1}
                    paddingTop={0.5}
                    paddingBottom={1}
                >
                    <Stack gap={2} marginBottom={12}>
                        <CurrentSet scoreboardNumber={SCOREBOARD_ID} />
                        <UpcomingSets onSelectedSetChanged={onSelectedSetChanged} scoreboardNumber={SCOREBOARD_ID}/>
                        <Box display="flex" justifyContent="center" mt={4}>
                            <Button
                                variant={"outlined"}
                                size="small"
                                color="error"
                                onClick={() => {
                                    fetch(`http://${window.location.hostname}:${BACKEND_PORT}/scoreboard${SCOREBOARD_ID}-clear-all`)
                                        .catch(console.error);
                                }}
                            >
                                {i18n.t("clear_all")}
                            </Button>
                        </Box>
                    </Stack>
                </Box>
            </>
        );
    }

    return (
        <Box
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100vh",
                gap: darkTheme.spacing(1),
            }}
            sx={{overflow: "auto !important"}}
        >
            {body}
        </Box>
    )
}
