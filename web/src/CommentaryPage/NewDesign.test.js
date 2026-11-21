import React from "react";
import {act, fireEvent, render, screen} from "@testing-library/react";
import i18n from "../i18n/config";
import NewDesignActions, {
    CREATE_PATH,
    DISPLAY_NAME_MAX,
    NAME_MAX,
    REVEAL_PATH,
    aiPromptPath,
    displayNameIsValid,
    draftName,
    nameIsValid,
} from "./NewDesign";

// The design lists' layout kit (ADR-008 L2): shown in the desktop's shared
// view alone (the routes are SHELL); "make a new design" sends the kind, the
// folder name and the display name, hands the new entry to the page and
// opens its folder; a refusal is told in the dialog by core's reason (and a
// broken kit has the page list again); "copy for an AI" writes the text read
// ahead, in the click. Nothing is sent for a name the server would refuse.

jest.mock("../ScoreboardPage/Feedback", () => ({
    showSnackbar: jest.fn(),
}));
// eslint-disable-next-line import/first
import {showSnackbar} from "../ScoreboardPage/Feedback";

const SESSION_PATH = "/api/v1/shell-session";
const ENTRY = {id: "commentators_Duo", kind: "commentary",
               displayName: "実況 2 人", entry: "index.html"};

function answer(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function calls(method, path) {
    return global.fetch.mock.calls
        .filter(([url, init]) => (init?.method ?? "GET") === method
            && url.endsWith(path))
        .map(([, init]) => (init?.body ? JSON.parse(init.body) : null));
}

async function settle() {
    await act(async () => {
        for (let i = 0; i < 12; i++) {
            await Promise.resolve();
        }
    });
}

let routes;

beforeEach(() => {
    // jsdom has no AbortSignal.timeout (api.ts asks for one per request).
    AbortSignal.timeout = () => undefined;
    routes = {
        [`GET ${SESSION_PATH}`]: () => answer(200, {ok: true, data: {ticket: "t"}}),
        [`POST ${CREATE_PATH}`]: () => answer(201, {ok: true, data: ENTRY}),
        [`POST ${REVEAL_PATH}`]: () => answer(200, {ok: true}),
        [`GET ${aiPromptPath("commentary")}`]: () => answer(
            200, {ok: true, data: {kind: "commentary", text: "# prompt"}}),
    };
    global.fetch = jest.fn(async (url, init) => {
        const key = `${init?.method ?? "GET"} ${new URL(url).pathname}`;
        const route = routes[key];
        return route ? route() : answer(404, {ok: false});
    });
    showSnackbar.mockClear();
});

async function renderActions(props = {}) {
    const onCreated = jest.fn();
    const onRelist = jest.fn();
    render(<NewDesignActions kind="commentary" onCreated={onCreated}
                             onRelist={onRelist} {...props}/>);
    await settle();
    return {onCreated, onRelist};
}

async function openDialog() {
    const {onCreated, onRelist} = await renderActions();
    fireEvent.click(screen.getByRole("button", {name: i18n.t("layout_kit_new")}));
    return {onCreated, onRelist};
}

function typeInto(label, value) {
    fireEvent.change(screen.getByLabelText(i18n.t(label)), {target: {value}});
}

test("the rules are core's and a folder name is drafted from ASCII", () => {
    expect(nameIsValid("Finals_2026")).toBe(true);
    expect(nameIsValid("a".repeat(NAME_MAX))).toBe(true);
    for (const name of ["", "a".repeat(NAME_MAX + 1), "a b", "決勝", "-x",
                        "x.y", "../x"]) {
        expect(nameIsValid(name)).toBe(false);
    }
    expect(displayNameIsValid("  決勝  ")).toBe(true);
    expect(displayNameIsValid("あ".repeat(DISPLAY_NAME_MAX + 1))).toBe(false);
    expect(displayNameIsValid("a\nb")).toBe(false);
    expect(draftName("  My Finals 2026! ")).toBe("My_Finals_2026");
    expect(draftName("決勝")).toBe("");
    expect(nameIsValid(draftName("x".repeat(80)))).toBe(true);
});

test("outside the shared view neither button shows", async () => {
    routes[`GET ${SESSION_PATH}`] = () => answer(403, {
        ok: false, error: {code: "FORBIDDEN", message: "no"}});
    await renderActions();

    expect(screen.queryByRole("button", {name: i18n.t("layout_kit_new")}))
        .toBeNull();
    expect(screen.queryByRole("button",
                              {name: i18n.t("layout_kit_copy_prompt")}))
        .toBeNull();
    expect(calls("GET", aiPromptPath("commentary"))).toEqual([]);
});

test("a design is made, handed to the page and its folder opened", async () => {
    const {onCreated} = await openDialog();
    typeInto("layout_kit_display_name", "Duo Cast");
    // Drafted from the ASCII display name until the folder name is typed.
    expect(screen.getByLabelText(i18n.t("layout_kit_name")).value)
        .toBe("Duo_Cast");
    typeInto("layout_kit_display_name", "実況 2 人");
    expect(screen.getByLabelText(i18n.t("layout_kit_name")).value).toBe("");
    expect(screen.getByText(i18n.t("layout_kit_name_needed", {max: NAME_MAX})))
        .toBeTruthy();
    typeInto("layout_kit_name", "Duo");
    typeInto("layout_kit_display_name", "  実況 2 人 ");
    expect(screen.getByLabelText(i18n.t("layout_kit_name")).value).toBe("Duo");
    expect(screen.getByText(i18n.t("layout_kit_folder",
                                   {folder: "layout/commentators_Duo/"})))
        .toBeTruthy();

    fireEvent.click(screen.getByRole("button", {name: i18n.t("layout_kit_create")}));
    await settle();

    expect(calls("POST", CREATE_PATH)).toEqual([
        {kind: "commentary", name: "Duo", displayName: "実況 2 人"}]);
    expect(onCreated).toHaveBeenCalledWith(ENTRY);
    expect(calls("POST", REVEAL_PATH)).toEqual([{id: ENTRY.id}]);
    expect(showSnackbar).toHaveBeenCalledWith(
        i18n.t("layout_kit_created", {folder: ENTRY.id}), "success");
});

test("a name of other characters is not sent", async () => {
    const {onCreated} = await openDialog();
    typeInto("layout_kit_name", "決勝");

    const make = screen.getByRole("button", {name: i18n.t("layout_kit_create")});
    expect(make.disabled).toBe(true);
    expect(screen.getByText(i18n.t("layout_kit_name_rule", {max: NAME_MAX})))
        .toBeTruthy();
    fireEvent.keyDown(screen.getByLabelText(i18n.t("layout_kit_name")),
                      {key: "Enter"});
    await settle();
    expect(calls("POST", CREATE_PATH)).toEqual([]);
    expect(onCreated).not.toHaveBeenCalled();
});

test("a refusal is told in the dialog by its reason", async () => {
    routes[`POST ${CREATE_PATH}`] = () => answer(409, {
        ok: false, error: {code: "CONFLICT", message: "exists",
                           reason: "exists"}});
    const {onCreated, onRelist} = await openDialog();
    typeInto("layout_kit_name", "Duo");
    fireEvent.keyDown(screen.getByLabelText(i18n.t("layout_kit_name")),
                      {key: "Enter"});
    await settle();

    expect(screen.getByText(i18n.t("layout_kit_error_exists"))).toBeTruthy();
    expect(onCreated).not.toHaveBeenCalled();
    expect(onRelist).not.toHaveBeenCalled();
    expect(calls("POST", REVEAL_PATH)).toEqual([]);
});

test("a folder left by a broken kit is told and the list read again", async () => {
    routes[`POST ${CREATE_PATH}`] = () => answer(500, {
        ok: false, error: {code: "INTERNAL", message: "x",
                           reason: "not_listed"}});
    const {onCreated, onRelist} = await openDialog();
    typeInto("layout_kit_name", "Duo");
    fireEvent.click(screen.getByRole("button", {name: i18n.t("layout_kit_create")}));
    await settle();

    expect(screen.getByText(i18n.t("layout_kit_error_not_listed",
                                   {folder: "commentators_Duo"}))).toBeTruthy();
    expect(onRelist).toHaveBeenCalledTimes(1);
    expect(onCreated).not.toHaveBeenCalled();
});

test("the kit's text for an AI is read ahead and copied in the click", async () => {
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    await renderActions();
    expect(calls("GET", aiPromptPath("commentary"))).toEqual([null]);

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_kit_copy_prompt")}));
    // Written in the click, before any await.
    expect(writeText).toHaveBeenCalledWith("# prompt");
    await settle();
    expect(calls("GET", aiPromptPath("commentary"))).toEqual([null]);
    expect(showSnackbar).toHaveBeenCalledWith(
        i18n.t("layout_kit_prompt_copied"), "success");
});

test("a text not read yet is told and read again", async () => {
    routes[`GET ${aiPromptPath("commentary")}`] = () => answer(500, {ok: false});
    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
        value: {writeText}, configurable: true});
    await renderActions();

    fireEvent.click(screen.getByRole("button",
                                     {name: i18n.t("layout_kit_copy_prompt")}));
    await settle();
    expect(writeText).not.toHaveBeenCalled();
    expect(showSnackbar).toHaveBeenCalledWith(
        i18n.t("layout_kit_prompt_unavailable"), "error");
    expect(calls("GET", aiPromptPath("commentary")).length).toBe(2);
});
