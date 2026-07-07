import React from "react";
import {shallowEqual, useSelector} from "react-redux";
import {apiPost} from "../api";
import {showSnackbar} from "./Feedback";
import i18n from "../i18n/config";

export type Scores = {left: number, right: number};

// Optimistic score submission (Design.md §2.2 R-React-8).
//
// Score +/- taps show the new value immediately and POST /score right away
// (matching the desktop SCORE tab's +1/-1 behavior). Reconciliation stays
// simple because the POST carries absolute values and the server is
// last-writer-wins (R-Sync-4):
//   - the optimistic value is cleared once the server echo matches it
//   - rapid taps are serialized: while a POST is in flight only the latest
//     desired value is kept, and it is sent once the response arrives
//   - on failure the display reverts to the server value + snackbar
//   - a 3s timeout clears the optimistic value regardless (e.g. another
//     operator changed the score concurrently; server wins)
export function useScoreSubmit(
    scoreboardNumber: number | string,
    teamKeys: string[],
) {
    const serverScores: Scores = useSelector((state: any) => {
        const score = state.tshState.tshState?.score?.[scoreboardNumber];
        return {
            left: score?.team?.[teamKeys[0]]?.score ?? 0,
            right: score?.team?.[teamKeys[1]]?.score ?? 0,
        };
    }, shallowEqual);

    const [optimistic, setOptimistic] = React.useState<Scores | null>(null);
    const inFlightRef = React.useRef(false);
    const latestDesiredRef = React.useRef<Scores | null>(null);
    const timeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

    const clearOptimistic = React.useCallback(() => {
        setOptimistic(null);
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    // Server echo matched what we sent -> pending is over.
    React.useEffect(() => {
        if (optimistic
            && serverScores.left === optimistic.left
            && serverScores.right === optimistic.right) {
            clearOptimistic();
        }
    }, [serverScores.left, serverScores.right, optimistic, clearOptimistic]);

    React.useEffect(() => () => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
    }, []);

    const pump = React.useCallback(async () => {
        if (inFlightRef.current) {
            return;
        }
        inFlightRef.current = true;
        try {
            while (latestDesiredRef.current) {
                const target = latestDesiredRef.current;
                latestDesiredRef.current = null;
                await apiPost('/score', {
                    scoreboard: Number(scoreboardNumber),
                    team1score: target.left,
                    team2score: target.right,
                });
            }
        } catch (e) {
            console.error(e);
            showSnackbar(i18n.t("request_failed"));
            latestDesiredRef.current = null;
            clearOptimistic();
        } finally {
            inFlightRef.current = false;
        }
    }, [scoreboardNumber, clearOptimistic]);

    const setScores = React.useCallback((next: Scores) => {
        setOptimistic(next);
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
        timeoutRef.current = setTimeout(() => {
            setOptimistic(null);
            timeoutRef.current = null;
        }, 3000);
        latestDesiredRef.current = next;
        pump();
    }, [pump]);

    return {
        scores: optimistic ?? serverScores,
        pending: optimistic !== null,
        setScores,
    };
}
