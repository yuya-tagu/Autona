import React from "react";
import {Provider} from "react-redux";
import {configureStore} from "@reduxjs/toolkit";
import {act, fireEvent, render, renderHook, screen, waitFor} from "@testing-library/react";
import {apiPatch, apiPost} from "../api";
import {useScoreSubmit} from "./useScoreSubmit";
import Team from "./Team";

// The Web UI addresses teams by their *state* number -- the key under
// score.{n}.team -- and shows team "1" on the left whatever the swap state,
// as the desktop does (a swap exchanges the values, not the columns). The
// /api/v1 routes name state teams too (tests/test_api_v1.py), so a ± or a
// Losers tick lands on the side it was made on, swapped or not. These pin
// the client half: what each control sends.

jest.mock("../api", () => ({
    apiPost: jest.fn(() => Promise.resolve("")),
    apiPatch: jest.fn(() => Promise.resolve("")),
    apiGetJson: jest.fn(() => Promise.resolve({data: {}})),
}));

function storeWithScores(team1, team2) {
    return configureStore({
        reducer: {
            autonaState: () => ({
                autonaState: {
                    score: {1: {team: {1: {score: team1}, 2: {score: team2}}}},
                },
            }),
        },
    });
}

beforeEach(() => {
    apiPost.mockClear();
    apiPatch.mockClear();
});

test("score ± sends the left score as team1 and the right as team2", async () => {
    const store = storeWithScores(0, 3);
    const wrapper = ({children}) => <Provider store={store}>{children}</Provider>;
    // index.tsx passes the sorted state keys, so left = team "1".
    const {result} = renderHook(() => useScoreSubmit(1, ["1", "2"]), {wrapper});

    expect(result.current.scores).toEqual({left: 0, right: 3});

    act(() => result.current.setScores({left: 1, right: 3}));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
        "/api/v1/scoreboards/1/score:set", {team1: 1, team2: 3}));
});

test.each([["1", 1], ["2", 2]])(
    "the Losers checkbox of team %s sends state team %i",
    async (autonaTeamId, teamNumber) => {
        render(
            <Team
                teamId={`t${autonaTeamId}`}
                autonaTeamId={autonaTeamId}
                team={{losers: false, teamName: "", player: {}}}
                scoreboardNumber={1}
            />,
        );

        fireEvent.click(screen.getByRole("checkbox"));

        await waitFor(() => expect(apiPatch).toHaveBeenCalledWith(
            "/api/v1/scoreboards/1/set",
            {losers: {team: teamNumber, value: true}}));
    },
);
