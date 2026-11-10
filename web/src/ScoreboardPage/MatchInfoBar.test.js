import React from "react";
import {act, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {apiGetJson} from "../api";
import i18n from "../i18n/config";
import MatchInfoBar, {candidateLists} from "./MatchInfoBar";

// What the server answers. GET /api/v1/match-names is the terms by key, kept
// in that shape for the callers outside the page; the combos' lists are
// GET /api/v1/match-names/candidates (tests/test_api_v1_crew.py pins that
// they are what the desktop's combos offer). Handed the first, an
// Autocomplete threw "options.filter is not a function" the moment its
// popup opened, and the whole page went blank.
const mockAnswers = {
    "/api/v1/match-names": {
        phase: {pools: "Pools", group: "Group {0}"},
        match: {grand_final: "Grand Final"},
    },
    "/api/v1/match-names/candidates": {
        phase: ["Pools", "Top 8"],
        pool: ["Group A", "Group B"],
        match: ["Winners Final", "Grand Final"],
    },
};

jest.mock("../api", () => ({
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
    apiGetJson: jest.fn(),
}));

// The only test that renders the bar: the candidates are fetched once per
// page load (a module-level cache), so a second render would not ask again.
test("each combo offers its list of /match-names/candidates, and opens", async () => {
    // Here, not in the factory: CRA resets mock implementations per test.
    apiGetJson.mockImplementation(
        (path) => Promise.resolve({data: mockAnswers[path] ?? {}}));
    render(<MatchInfoBar scoreboardNumber={1} phase="" pool="" match="" bestOf={0}/>);
    await waitFor(() => expect(apiGetJson).toHaveBeenCalledWith(
        "/api/v1/match-names/candidates"));
    await act(() => Promise.resolve());
    expect(apiGetJson).not.toHaveBeenCalledWith("/api/v1/match-names");

    const lists = mockAnswers["/api/v1/match-names/candidates"];
    for (const field of ["phase", "pool", "match"]) {
        const input = screen.getByLabelText(i18n.t(field));
        // A click in the field: focus, then the press that opens the popup.
        fireEvent.focus(input);
        fireEvent.mouseDown(input);
        const listbox = await screen.findByRole("listbox");
        expect(within(listbox).getAllByRole("option").map((option) => option.textContent))
            .toEqual(lists[field]);

        fireEvent.keyDown(input, {key: "Escape"});
        fireEvent.blur(input);
        await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    }
});

test.each([
    ["the lists", mockAnswers["/api/v1/match-names/candidates"],
        mockAnswers["/api/v1/match-names/candidates"]],
    // The terms by key: none of them is a list.
    ["objects", mockAnswers["/api/v1/match-names"], {phase: [], pool: [], match: []}],
    ["what is not text in a list", {phase: ["P", 5, null, {a: 1}], pool: "Group A", match: null},
        {phase: ["P"], pool: [], match: []}],
    ["no answer", undefined, {phase: [], pool: [], match: []}],
])("an answer of %s becomes three arrays of strings", (_what, data, lists) => {
    expect(candidateLists(data)).toEqual(lists);
});
