import React from "react";
import {act, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {apiGetJson} from "../api";
import i18n from "../i18n/config";
import MatchInfoBar, {candidateLists} from "./MatchInfoBar";

// What the server answers. GET /api/v1/match-names is the terms by key, kept
// in that shape for the callers outside the page; the combos' lists are
// GET /api/v1/match-names/candidates?board=N (tests/test_match_name_candidates.py
// pins that they are what the desktop's combos offer for that board). Handed
// the first, an Autocomplete threw "options.filter is not a function" the
// moment its popup opened, and the whole page went blank.
const LISTS = {
    phase: ["Pools", "Top 8"],
    pool: ["Group A", "Group B"],
    match: ["Winners Final", "Grand Final"],
};
const mockAnswers = {
    "/api/v1/match-names": {
        phase: {pools: "Pools", group: "Group {0}"},
        match: {grand_final: "Grand Final"},
    },
    "/api/v1/match-names/candidates?board=1": LISTS,
};

jest.mock("../api", () => ({
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
    apiGetJson: jest.fn(),
}));

beforeEach(() => {
    // Here, not in the factory: CRA resets mock implementations per test.
    apiGetJson.mockReset();
    apiGetJson.mockImplementation(
        (path) => Promise.resolve({data: mockAnswers[path] ?? {}}));
});

async function openedOptions(field) {
    const input = screen.getByLabelText(i18n.t(field));
    // A click in the field: focus, then the press that opens the popup.
    fireEvent.focus(input);
    fireEvent.mouseDown(input);
    const listbox = await screen.findByRole("listbox");
    return {input, listbox};
}

async function close(input) {
    fireEvent.keyDown(input, {key: "Escape"});
    fireEvent.blur(input);
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
}

const names = (listbox) => within(listbox).getAllByRole("option")
    .map((option) => option.textContent);

test("each combo offers its list of /match-names/candidates, and opens", async () => {
    render(<MatchInfoBar scoreboardNumber={1} phase="" pool="" match="" bestOf={0}/>);
    await waitFor(() => expect(apiGetJson).toHaveBeenCalledWith(
        "/api/v1/match-names/candidates?board=1"));
    await act(() => Promise.resolve());
    expect(apiGetJson).not.toHaveBeenCalledWith("/api/v1/match-names");

    for (const field of ["phase", "pool", "match"]) {
        const {input, listbox} = await openedOptions(field);
        await waitFor(() => expect(names(listbox)).toEqual(LISTS[field]));
        await close(input);
    }
});

test("the board's own lists are asked for", async () => {
    render(<MatchInfoBar scoreboardNumber={2} phase="" pool="" match="" bestOf={0}/>);
    await waitFor(() => expect(apiGetJson).toHaveBeenCalledWith(
        "/api/v1/match-names/candidates?board=2"));
    expect(apiGetJson).not.toHaveBeenCalledWith(
        "/api/v1/match-names/candidates");
});

test("an opened combo asks again: a tournament loaded meanwhile is in it", async () => {
    render(<MatchInfoBar scoreboardNumber={1} phase="" pool="" match="" bestOf={0}/>);
    await waitFor(() => expect(apiGetJson).toHaveBeenCalledTimes(1));
    await act(() => Promise.resolve());

    // The tournament's pools, ahead of the terms (what core answers).
    apiGetJson.mockImplementation(() => Promise.resolve(
        {data: {...LISTS, pool: ["A1", "A2", ...LISTS.pool]}}));
    const {input, listbox} = await openedOptions("pool");
    expect(apiGetJson).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(names(listbox))
        .toEqual(["A1", "A2", "Group A", "Group B"]));
    await close(input);
});

test("a reply to an earlier ask is dropped", async () => {
    const replies = [];
    apiGetJson.mockImplementation(() => new Promise((resolve) => {
        replies.push(resolve);
    }));
    render(<MatchInfoBar scoreboardNumber={1} phase="" pool="" match="" bestOf={0}/>);
    await waitFor(() => expect(replies).toHaveLength(1));

    // Opened with no list yet (a freeSolo combo shows no popup for none).
    const input = screen.getByLabelText(i18n.t("match"));
    fireEvent.focus(input);
    fireEvent.mouseDown(input);
    await waitFor(() => expect(replies).toHaveLength(2));
    // The newer ask answers first; the older one's late reply changes nothing.
    await act(async () => {
        replies[1]({data: {...LISTS, match: ["Newer"]}});
    });
    await act(async () => {
        replies[0]({data: {...LISTS, match: ["Older"]}});
    });
    const listbox = await screen.findByRole("listbox");
    expect(names(listbox)).toEqual(["Newer"]);
    await close(input);
});

test.each([
    ["the lists", LISTS, LISTS],
    // The terms by key: none of them is a list.
    ["objects", mockAnswers["/api/v1/match-names"], {phase: [], pool: [], match: []}],
    ["what is not text in a list", {phase: ["P", 5, null, {a: 1}], pool: "Group A", match: null},
        {phase: ["P"], pool: [], match: []}],
    ["no answer", undefined, {phase: [], pool: [], match: []}],
])("an answer of %s becomes three arrays of strings", (_what, data, lists) => {
    expect(candidateLists(data)).toEqual(lists);
});
