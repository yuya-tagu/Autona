import React from "react";
import {render, screen} from "@testing-library/react";
import Team from "./Team";

jest.mock("../api", () => ({
    apiPost: jest.fn(() => Promise.resolve("")),
    apiPatch: jest.fn(() => Promise.resolve("")),
    apiGetJson: jest.fn(() => Promise.resolve({data: {}})),
}));

// A board no player card has written carries only the keys written so far:
// a score from the API makes `team` without `player`. The column still
// renders (it used to throw, and React dropped the whole page with it).
test("a team without player slots still renders its column", () => {
    render(
        <Team
            teamId="t1"
            autonaTeamId="1"
            team={{score: 2}}
            scoreboardNumber={1}
        />,
    );

    expect(screen.getByText("1P")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeInTheDocument();
});
