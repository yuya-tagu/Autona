import React from "react";
import {Provider} from "react-redux";
import {configureStore} from "@reduxjs/toolkit";
import {act, fireEvent, render, renderHook, screen, waitFor} from "@testing-library/react";
import {apiGetJson, apiPatch, apiPost} from "../api";
import i18n from "../i18n/config";
import {useScoreSubmit} from "./useScoreSubmit";
import Player from "./Player";
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

// What the server does with it: tests/test_api_v1.py
// (test_player_patch_with_a_new_country_and_no_region_leaves_none).
describe("a player's country", () => {
    const COUNTRIES = {
        JP: {code: "JP", name: "Japan"},
        US: {code: "US", name: "United States"},
    };

    function renderPlayer() {
        apiGetJson.mockImplementation((path) => Promise.resolve({
            data: path.endsWith("/countries/JP/states")
                ? {13: {name: "Tokyo", code: "13"}} : {},
        }));
        const store = configureStore({
            reducer: {
                autonaState: () => ({autonaState: {game: {codename: "tgame"}}}),
                autonaPlayers: () => ({players: {}}),
                autonaCharacters: () => ({characters: {}}),
                autonaCountries: () => ({value: COUNTRIES}),
            },
        });
        render(
            <Provider store={store}>
                <Player
                    teamId="t1"
                    teamKey="1"
                    scoreboardNumber={1}
                    autonaTeamId="1"
                    player={{
                        name: "Alpha", team: "", real_name: "", twitter: "",
                        country: COUNTRIES.JP,
                        state: {code: "13", name: "Tokyo"},
                        character: {},
                    }}
                />
            </Provider>,
        );
        fireEvent.click(screen.getByText(`>>${i18n.t("details")}`));
    }

    async function pickCountry(text, option) {
        const input = screen.getByRole("combobox", {name: i18n.t("country")});
        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: text}});
        fireEvent.click(await screen.findByRole("option", {name: option}));
    }

    test("goes with no region when it is another one", async () => {
        renderPlayer();

        await pickCountry("Unit", /United States/);

        await waitFor(() => expect(apiPatch).toHaveBeenCalled());
        const [path, body] = apiPatch.mock.calls[apiPatch.mock.calls.length - 1];
        expect(path).toBe("/api/v1/scoreboards/1/teams/1/players/1");
        expect(body).toMatchObject({country_code: "US", state_code: ""});
    });

    function countryField() {
        return screen.getByRole("combobox", {name: i18n.t("country")});
    }

    function regionField() {
        return screen.getByRole("combobox", {name: i18n.t("state")});
    }

    // tests/test_api_v1.py (test_player_patch_with_no_country_says_nothing_of_it):
    // the server keeps both, so the screen does.
    test("emptied and left, it goes back with its region", async () => {
        renderPlayer();
        await waitFor(() => expect(regionField()).toHaveValue("Tokyo"));
        const input = countryField();

        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: ""}});
        expect(input).toHaveValue("");
        fireEvent.blur(input);

        expect(input).toHaveValue("Japan");
        expect(regionField()).toHaveValue("Tokyo");
        expect(apiPatch).not.toHaveBeenCalled();
    });

    test("emptied, then the same one again keeps the region", async () => {
        renderPlayer();
        await waitFor(() => expect(regionField()).toHaveValue("Tokyo"));
        const input = countryField();

        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: ""}});
        await pickCountry("Japa", /Japan/);

        expect(input).toHaveValue("Japan");
        expect(regionField()).toHaveValue("Tokyo");
        expect(apiPatch).not.toHaveBeenCalled();
    });

    test("emptied, then another one goes with no region", async () => {
        renderPlayer();
        await waitFor(() => expect(regionField()).toHaveValue("Tokyo"));
        const input = countryField();

        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: ""}});
        await pickCountry("Unit", /United States/);

        await waitFor(() => expect(apiPatch).toHaveBeenCalledTimes(1));
        expect(apiPatch.mock.calls[0][1])
            .toMatchObject({country_code: "US", state_code: ""});
        expect(regionField()).toHaveValue("");
    });
});
