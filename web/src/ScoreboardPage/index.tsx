import "../App.css";
import i18n from "../i18n/config";
import {
    Button, Stack,
} from "@mui/material";
import React from "react";
import {Box} from "@mui/system";

import CurrentSet from "./CurrentSet";
import UpcomingSets from "./UpcomingSets";
import {Header} from "./Header";
import {ReduxState} from "../redux/store"
import {useDispatch, useSelector} from "react-redux";
import {autonaStateSlice} from "../redux/autonaState";
import websocketInit from "./websocketInit";
import {apiGet} from "../api";
import {FeedbackProvider, useApiAction} from "./Feedback";
import {useScoreSubmit} from "./useScoreSubmit";
import LoadingSkeleton from "./LoadingSkeleton";
import ConnectionBanner from "./ConnectionBanner";
import ScoreControlBar from "./ScoreControlBar";

// scoreboardNumber selects which board this page edits: 1 = SCORE (1v1),
// 2 = TEAM (team mode). Routed via `/scoreboard` (default 1) and `/team`
// (2) in index.js. Defaults to 1 for backward compatibility.

/**
 * Main page for the scoreboard. This whole contraption is powered by Autona's python-side
 * program state. In order to do that, we subscribe to updates that get sent out and update
 * our state piecemeal. Each update has a number so that we can tell if our updates are stale
 * or out of order and request a full state send-over.
 */
function ScoreboardPageInner(props: any) {
    const SCOREBOARD_ID = props.scoreboardNumber ?? 1;
    const dispatch = useDispatch();
    const loading = useSelector((state: ReduxState) => (
        state.autonaState.initializing
        || state.autonaCharacters.initializing
        || state.autonaPlayers.initializing
        || state.autonaGames.initializing
        || state.autonaCountries.initializing
    ));
    const {pending: clearPending, run: runClear} = useApiAction();

    // Page-level optimistic score state, shared by the set card (CurrentSet
    // -> ScorePanel) and the phone bottom bar so both show the same value.
    const score = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as any)?.score?.[SCOREBOARD_ID]);
    const teamKeys = Object.keys(score?.team || {}).sort();
    const scoreSubmit = useScoreSubmit(SCOREBOARD_ID, teamKeys);

    React.useEffect(() => {
        document.title = `Autona ${i18n.t("scoreboard")}`;
        websocketInit();
    }, []);

    const onSelectedSetChanged = () => {
        dispatch(autonaStateSlice.actions.loadingNewData({}));
    };

    const clearAll = () => {
        if (!window.confirm(i18n.t("confirm_clear_all"))) {
            return;
        }
        runClear(() => apiGet(`/scoreboard${SCOREBOARD_ID}-clear-all`));
    };

    let body;
    if (loading) {
        body = <LoadingSkeleton key="loading" />;
    } else {
        body = (
            // Extra margin at the bottom allows for mobile users to see the bottom of the page better.
            <>
                <Header scoreboardNumber={SCOREBOARD_ID}/>
                <Box
                    paddingX={1}
                    paddingTop={0.5}
                    paddingBottom={1}
                >
                    <Stack gap={2} marginBottom={12}>
                        <CurrentSet scoreboardNumber={SCOREBOARD_ID} scoreSubmit={scoreSubmit} />
                        <UpcomingSets onSelectedSetChanged={onSelectedSetChanged} scoreboardNumber={SCOREBOARD_ID}/>
                        <Box display="flex" justifyContent="center" mt={4}>
                            <Button
                                variant={"outlined"}
                                size="small"
                                color="error"
                                loading={clearPending}
                                onClick={clearAll}
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
            sx={{
                display: "flex",
                flexDirection: "column",
                // dvh tracks the iOS/Android dynamic address bar; vh is the
                // fallback for older browsers.
                height: "100vh",
                "@supports (height: 100dvh)": {height: "100dvh"},
                gap: 1,
                overflow: "auto !important",
            }}
        >
            <ConnectionBanner />
            {body}
            {!loading && (
                <ScoreControlBar
                    scoreboardNumber={SCOREBOARD_ID}
                    score={score}
                    teamKeys={teamKeys}
                    scoreSubmit={scoreSubmit}
                />
            )}
        </Box>
    )
}

export default function ScoreboardPage(props: any) {
    return (
        <FeedbackProvider>
            <ScoreboardPageInner {...props} />
        </FeedbackProvider>
    );
}
