import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import {apiGetJson} from "./api";
import i18n from "./i18n/config";
import {CountryStateSelector} from "./CountryStateSelector";

// The region field reads the country's regions when the country changes:
// only those of the country it shows are ever offered.

jest.mock("./api", () => ({apiGetJson: jest.fn()}));

const STATES = {
    JP: {13: {name: "Tokyo", code: "13"}},
    US: {CA: {name: "California", code: "CA"}},
};

let answers;

function answer(country) {
    return act(async () => {
        answers[country]({data: STATES[country]});
    });
}

function renderField(countryCode) {
    return render(
        <CountryStateSelector countryCode={countryCode} value=""
                              onChange={() => undefined}/>);
}

function field() {
    return screen.getByRole("combobox");
}

beforeEach(() => {
    answers = {};
    apiGetJson.mockReset();
    apiGetJson.mockImplementation((path) => new Promise((resolve) => {
        answers[decodeURIComponent(path.split("/")[4])] = resolve;
    }));
});

test("without a country there is nothing to choose from", () => {
    renderField("");

    expect(field()).toBeDisabled();
});

test("it says it is loading until the regions are read, and offers them then", async () => {
    renderField("JP");
    fireEvent.mouseDown(field());
    expect(await screen.findByText(i18n.t("loading"))).toBeInTheDocument();
    expect(screen.queryByRole("option")).toBeNull();

    await answer("JP");

    expect(await screen.findByRole("option", {name: /Tokyo/})).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("loading"))).toBeNull();
});

// Tab from the country field lands here as the country changes: a disabled
// field would lose the focus.
test("another country's regions go at once, and the field stays usable", async () => {
    const {rerender} = renderField("JP");
    await answer("JP");
    field().focus();

    rerender(<CountryStateSelector countryCode="US" value=""
                                   onChange={() => undefined}/>);

    expect(field()).toBeEnabled();
    expect(document.activeElement).toBe(field());
    fireEvent.mouseDown(field());
    expect(await screen.findByText(i18n.t("loading"))).toBeInTheDocument();
    expect(screen.queryByRole("option", {name: /Tokyo/})).toBeNull();
});

test("the answer of the country before is dropped", async () => {
    const {rerender} = renderField("JP");
    rerender(<CountryStateSelector countryCode="US" value=""
                                   onChange={() => undefined}/>);

    await answer("US");
    await answer("JP");

    fireEvent.mouseDown(field());
    expect(await screen.findByRole("option", {name: /California/}))
        .toBeInTheDocument();
    expect(screen.queryByRole("option", {name: /Tokyo/})).toBeNull();
});

test("a failed read of the country before does not stop the current one", async () => {
    jest.spyOn(console, "warn").mockImplementation(() => undefined);
    const rejects = {};
    apiGetJson.mockImplementation((path) => new Promise((resolve, reject) => {
        const country = decodeURIComponent(path.split("/")[4]);
        answers[country] = resolve;
        rejects[country] = reject;
    }));
    const {rerender} = renderField("JP");
    rerender(<CountryStateSelector countryCode="US" value=""
                                   onChange={() => undefined}/>);

    await act(async () => {
        rejects.JP(new Error("gone"));
    });
    fireEvent.mouseDown(field());
    expect(await screen.findByText(i18n.t("loading"))).toBeInTheDocument();

    await answer("US");
    expect(await screen.findByRole("option", {name: /California/}))
        .toBeInTheDocument();
    console.warn.mockRestore();
});
