import React from "react";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import i18n from "../i18n/config";
import Templates from "./Templates";

jest.mock("../api", () => ({
    apiPost: jest.fn(),
    apiPut: jest.fn(),
    apiPatch: jest.fn(),
    apiDelete: jest.fn(),
    ApiError: class ApiError extends Error {},
}));
jest.mock("../PanelShell/hostApi", () => ({navigateToPanel: jest.fn()}));
// eslint-disable-next-line import/first
import {apiDelete, apiPatch, apiPost, apiPut} from "../api";
// eslint-disable-next-line import/first
import {navigateToPanel} from "../PanelShell/hostApi";

// The archive page's template view (ADR-006 A6): the list in the worker's
// order, an edit kept per template and saved whole, the variables put in at
// the caret, the preview asked a moment after the edits (and not at all
// with a worker from before `titles`), the standard template's reset and
// the thumbnails' background. The api is mocked; the clock is real.

jest.setTimeout(20000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const STANDARD = {
    name: "標準", match: null, title: "{event} {round} {p1} vs {p2}",
    title_team: "", description: "{event}\n\n{chapters}",
    chapter_format: "{time} Game {n}", overflow: "drop_characters",
    round_map: {}, aliases: {}, order: "result",
};
const RAIEI = "20261117-100000-aaaa";
const SPARE = "20261117-100000-bbbb";
const JOB = "20261116-100000-abcd";

function entries({defaultStored = false} = {}) {
    return [
        {id: RAIEI, stored: true, role: "match",
         template: {...STANDARD, name: "雷影杯", match: "雷影杯"}},
        {id: "default", stored: defaultStored, role: "default",
         template: STANDARD},
        {id: SPARE, stored: true, role: "unused",
         template: {...STANDARD, name: "予備"}},
    ];
}

const SETS = {
    event: "第5回 雷影杯", sets: [
        {no: 1, start: 0, end: 100, set: {round: "Winners Round 1", p1: "A",
                                          s1: 2, p2: "B", s2: 0}},
        {no: 2, start: 100, end: 200, set: null},
    ],
};

const PREVIEW = {data: {template: "雷影杯", sets: [
    {no: 1, title: "第5回 雷影杯 Winners Round 1 A vs B",
     description: "第5回 雷影杯\n\n0:00 Game 1", chapters: 3},
    {no: 2, title: null, description: null, chapters: 0},
]}};

let onReload;
let onStatus;

// The page holds the drafts (they outlive the view).
function Host({list, ...props}) {
    const [drafts, setDrafts] = React.useState({});
    return <Templates entries={list} drafts={drafts} setDrafts={setDrafts}
                      {...props}/>;
}

function view({list, loadError = "", readOnly = false, jobId = JOB,
               sets = SETS, status}) {
    return <Host list={list} loadError={loadError} onReload={onReload}
                 status={status} onStatus={onStatus} readOnly={readOnly}
                 jobId={jobId} sets={sets} setNo={null}
                 onBack={() => undefined}/>;
}

function show({list = entries(), features = ["titles"], readOnly = false,
               jobId = JOB, sets = SETS, loadError = "", reload} = {}) {
    onReload = reload ?? jest.fn(() => Promise.resolve(list));
    onStatus = jest.fn();
    const status = {available: true, features, thumbnail_background: "frame"};
    const shown = render(view({list, loadError, readOnly, jobId, sets,
                               status}));
    return {...shown, status,
            again: (more) => shown.rerender(view({list, loadError, readOnly,
                                                  jobId, sets, status,
                                                  ...more}))};
}

beforeEach(() => {
    apiPost.mockImplementation(() => Promise.resolve(JSON.stringify(PREVIEW)));
});

afterEach(() => {
    cleanup();
    apiPost.mockReset();
    apiPut.mockReset();
    apiPatch.mockReset();
    apiDelete.mockReset();
    navigateToPanel.mockReset();
});

const field = (testId) => screen.getByTestId(testId);
const previews = () => apiPost.mock.calls.filter(
    ([path]) => path.endsWith("/titles:preview"));

test("lists the templates in the worker's order and opens the one the job's event takes",
     async () => {
    show();
    const rows = screen.getAllByTestId(/^archive-template-/)
        .filter((row) => row.getAttribute("role") === "button");
    expect(rows.map((row) => row.dataset.testid)).toEqual([
        `archive-template-${RAIEI}`, "archive-template-default",
        `archive-template-${SPARE}`]);
    const first = within(rows[0]);
    expect(first.getByText(i18n.t("archive_tpl_role_match", {match: "雷影杯"})))
        .toBeTruthy();
    expect(first.getByText(i18n.t("archive_tpl_for_job"))).toBeTruthy();
    expect(within(rows[1]).getByText(i18n.t("archive_tpl_role_default")))
        .toBeTruthy();
    expect(rows[1].textContent).toContain(i18n.t("archive_tpl_not_stored"));
    expect(within(rows[2]).getByText(i18n.t("archive_tpl_role_unused")))
        .toBeTruthy();
    expect(field(`archive-template-role-${SPARE}`).className)
        .not.toContain("Primary");
    expect(rows[0].getAttribute("aria-current")).toBe("true");
    expect(field("archive-tpl-name").value).toBe("雷影杯");
    // Let the first preview go out before the test ends.
    await act(async () => {
        await sleep(700);
    });
});

test("an edit is kept per template, marked unsaved and saved whole", async () => {
    const saved = {...entries()[0], template: {
        ...entries()[0].template, title: "{event} {p1} vs {p2}"}};
    apiPut.mockImplementation(() => Promise.resolve(JSON.stringify(
        {data: {template: saved}})));
    show();
    const save = field("archive-tpl-save");
    expect(save).toBeDisabled();
    fireEvent.change(field("archive-tpl-title"),
                     {target: {value: "{event} {p1} vs {p2}"}});
    expect(save).toBeEnabled();
    const raiei = screen.getByTestId(`archive-template-${RAIEI}`);
    expect(within(raiei).getByText(i18n.t("archive_tpl_unsaved"))).toBeTruthy();

    // Another template and back: the edit is still there.
    fireEvent.click(screen.getByTestId("archive-template-default"));
    expect(field("archive-tpl-title").value).toBe(STANDARD.title);
    fireEvent.click(raiei);
    expect(field("archive-tpl-title").value).toBe("{event} {p1} vs {p2}");

    // (The editor is the template's own: its button is a new one.)
    fireEvent.click(field("archive-tpl-save"));
    await waitFor(() => expect(onReload).toHaveBeenCalled());
    expect(apiPut).toHaveBeenCalledTimes(1);
    const [path, body] = apiPut.mock.calls[0];
    expect(path).toBe(`/api/v1/archive/templates/${RAIEI}`);
    expect(body).toEqual({template: {...entries()[0].template,
                                     title: "{event} {p1} vs {p2}"}});
    expect((await screen.findByTestId("archive-tpl-notice")).textContent)
        .toBe(i18n.t("archive_tpl_saved"));
});

test("a variable goes in where the caret is", async () => {
    show();
    const title = field("archive-tpl-title");
    act(() => title.focus());
    title.setSelectionRange(8, 8);
    fireEvent.mouseDown(screen.getByTestId("archive-tpl-var-winner"));
    fireEvent.click(screen.getByTestId("archive-tpl-var-winner"));
    expect(title.value).toBe("{event} {winner}{round} {p1} vs {p2}");
    await act(async () => {
        await sleep(20);
    });
    expect(title.selectionStart).toBe(16);
});

test("the preview is asked a moment after the last edit, the latest answer shown",
     async () => {
    show();
    expect(previews()).toHaveLength(0);
    await waitFor(() => expect(previews()).toHaveLength(1), {timeout: 2000});
    const [path, body, options] = previews()[0];
    expect(path).toBe(`/api/v1/archive/jobs/${JOB}/titles:preview`);
    expect(body.template.name).toBe("雷影杯");
    expect(options.timeoutMs).toBeGreaterThan(8000);
    const title = await screen.findByTestId("archive-preview-title");
    expect(title.textContent).toBe("第5回 雷影杯 Winners Round 1 A vs B");
    expect(screen.getByTestId("archive-preview-description").textContent)
        .toContain("0:00 Game 1");
    expect(screen.getByTestId("archive-preview-chapters").textContent)
        .toBe(i18n.t("archive_tpl_preview_chapters", {count: 3}));
    expect(screen.getByTestId("archive-preview-pane").textContent)
        .toContain(`${"第5回 雷影杯 Winners Round 1 A vs B".length} / 100`);

    // Two edits in a row: one more request, with the last of them.
    fireEvent.change(field("archive-tpl-title"), {target: {value: "{p1}"}});
    fireEvent.change(field("archive-tpl-title"), {target: {value: "{p1} {p2}"}});
    await act(async () => {
        await sleep(900);
    });
    expect(previews()).toHaveLength(2);
    expect(previews()[1][1].template.title).toBe("{p1} {p2}");
});

test("a preview the server refuses is told", async () => {
    apiPost.mockImplementation(() => Promise.reject(Object.assign(
        new Error("bad"), {status: 400, detail: "チャプターの書式に使える変数は {time} と {n} だけです"})));
    show();
    expect((await screen.findByTestId("archive-preview-error", {},
                                      {timeout: 2000})).textContent)
        .toContain("{time} と {n}");
});

test("a worker from before titles leaves only the preview out", async () => {
    show({features: []});
    expect(screen.getByTestId("archive-preview-unsupported").textContent)
        .toBe(i18n.t("archive_tpl_preview_unsupported"));
    await act(async () => {
        await sleep(800);
    });
    expect(previews()).toHaveLength(0);
    // The rest still works.
    expect(field("archive-tpl-title")).toBeEnabled();
});

test("no done job: the preview says to pick one", async () => {
    show({jobId: null, sets: null});
    expect(screen.getByTestId("archive-preview-pane").textContent)
        .toContain(i18n.t("archive_tpl_preview_pick_job"));
    await act(async () => {
        await sleep(800);
    });
    expect(previews()).toHaveLength(0);
});

test("the standard template takes no match and is reset, a stored one deleted",
     async () => {
    const list = entries({defaultStored: true});
    apiDelete.mockImplementation(() => Promise.resolve(JSON.stringify(
        {data: {template: {...list[1], stored: false}}})));
    show({list});
    fireEvent.click(screen.getByTestId("archive-template-default"));
    expect(field("archive-tpl-match")).toBeDisabled();
    const reset = field("archive-tpl-delete");
    expect(reset.textContent).toBe(i18n.t("archive_tpl_reset"));
    fireEvent.click(reset);
    // The dialog's (the rest of the page is hidden from it).
    fireEvent.click(await screen.findByRole("button", {
        name: i18n.t("archive_tpl_reset")}));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledTimes(1));
    expect(apiDelete.mock.calls[0][0]).toBe("/api/v1/archive/templates/default");
    await waitFor(() => expect(onReload).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId(`archive-template-${SPARE}`));
    expect(field("archive-tpl-delete").textContent)
        .toBe(i18n.t("archive_tpl_delete"));
});

test("the standard one not saved yet has nothing to reset", async () => {
    show();
    fireEvent.click(screen.getByTestId("archive-template-default"));
    expect(field("archive-tpl-delete")).toBeDisabled();
    await act(async () => {
        await sleep(700);
    });
});

test("a duplicate is made of the saved one and opened", async () => {
    const made = {id: "20261117-100500-cccc", stored: true, role: "unused",
                  template: {...STANDARD, name: "雷影杯 のコピー", match: "雷影杯"}};
    apiPost.mockImplementation((path) => path.endsWith(":duplicate")
        ? Promise.resolve(JSON.stringify({data: {template: made}}))
        : Promise.resolve(JSON.stringify(PREVIEW)));
    const list = entries();
    const {rerender} = show({list});
    const duplicate = field("archive-tpl-duplicate");
    // Not over an edit (the server copies what is saved).
    fireEvent.change(field("archive-tpl-title"), {target: {value: "x"}});
    expect(duplicate).toBeDisabled();
    fireEvent.click(screen.getByText(i18n.t("archive_tpl_revert")));
    expect(duplicate).toBeEnabled();
    fireEvent.click(duplicate);
    await waitFor(() => expect(onReload).toHaveBeenCalled());
    expect(apiPost.mock.calls.some(([path]) =>
        path === `/api/v1/archive/templates/${RAIEI}:duplicate`)).toBe(true);
    // The parent's list now has it: it is the one open.
    rerender(view({list: [...list, made],
                   status: {available: true, features: ["titles"]}}));
    expect(field("archive-tpl-name").value).toBe("雷影杯 のコピー");
});

test("a template shadowed by one of the same match says it is never used",
     async () => {
    const list = [
        ...entries().slice(0, 2),
        {id: SPARE, stored: true, role: "shadowed",
         template: {...STANDARD, name: "雷影杯 その2", match: "雷影杯"}}];
    show({list});
    const row = screen.getByTestId(`archive-template-${SPARE}`);
    expect(within(row).getByText(i18n.t("archive_tpl_role_shadowed")))
        .toBeTruthy();
    expect(field(`archive-template-role-${SPARE}`).className)
        .not.toContain("Primary");
    // The job's event takes the first one, not the shadowed one.
    expect(within(row).queryByText(i18n.t("archive_tpl_for_job"))).toBeNull();
    await act(async () => {
        await sleep(700);
    });
});

test("the fields wait while a save runs; the draft stays until the list has it",
     async () => {
    let answer;
    apiPut.mockImplementation(() => new Promise((resolve) => {
        answer = resolve;
    }));
    // The list cannot be read again after the save.
    show({reload: jest.fn(() => Promise.resolve(null))});
    fireEvent.change(field("archive-tpl-title"), {target: {value: "{p1}"}});
    fireEvent.click(field("archive-tpl-save"));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(field("archive-tpl-title").readOnly).toBe(true);
    expect(field("archive-tpl-name").readOnly).toBe(true);
    await act(async () => {
        answer(JSON.stringify({data: {template: entries()[0]}}));
    });
    await waitFor(() => expect(onReload).toHaveBeenCalled());
    await waitFor(() => expect(field("archive-tpl-title").readOnly)
        .toBe(false));
    // Not dropped for the old list: the editor still shows what was saved.
    expect(field("archive-tpl-title").value).toBe("{p1}");
    expect(within(screen.getByTestId(`archive-template-${RAIEI}`))
        .getByText(i18n.t("archive_tpl_unsaved"))).toBeTruthy();
});

test("a list that could not be read is read again on retry", async () => {
    let answer;
    const reload = jest.fn(() => new Promise((resolve) => {
        answer = resolve;
    }));
    show({list: null, loadError: "boom", reload});
    expect(field("archive-tpl-load-error").textContent)
        .toContain(i18n.t("archive_tpl_load_failed", {message: "boom"}));
    fireEvent.click(field("archive-tpl-retry"));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(field("archive-tpl-retry")).toBeDisabled();
    await act(async () => {
        answer(null);
    });
    expect(field("archive-tpl-retry")).toBeEnabled();
});

test("a duplicate whose list could not be read is not opened before it is",
     async () => {
    const made = {id: "20261117-100500-cccc", stored: true, role: "shadowed",
                  template: {...STANDARD, name: "雷影杯 のコピー", match: "雷影杯"}};
    apiPost.mockImplementation((path) => path.endsWith(":duplicate")
        ? Promise.resolve(JSON.stringify({data: {template: made}}))
        : Promise.resolve(JSON.stringify(PREVIEW)));
    const list = entries();
    const {again} = show({list, reload: jest.fn(() => Promise.resolve(null))});
    fireEvent.click(field("archive-tpl-duplicate"));
    await waitFor(() => expect(onReload).toHaveBeenCalled());
    await waitFor(() => expect(field("archive-tpl-duplicate")).toBeEnabled());
    // The page says why with a retry (loadError); the one open stays.
    again({loadError: "boom"});
    expect(field("archive-tpl-name").value).toBe("雷影杯");
    // The retry reads the list, the copy in it.
    onReload.mockImplementation(() => Promise.resolve([...list, made]));
    fireEvent.click(field("archive-tpl-retry"));
    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(2));
    again({list: [...list, made]});
    expect(screen.getByTestId(`archive-template-${made.id}`)).toBeTruthy();
});

test("a preview error goes when the preview cannot be asked", async () => {
    apiPost.mockImplementation(() => Promise.reject(Object.assign(
        new Error("bad"), {status: 503, detail: "ワーカーが止まりました"})));
    const {again} = show();
    expect((await screen.findByTestId("archive-preview-error", {},
                                      {timeout: 2000})).textContent)
        .toContain("ワーカーが止まりました");
    // Read only for a moment, then writable again: the old error is not
    // shown over the new preview being asked.
    again({readOnly: true});
    apiPost.mockImplementation(() => new Promise(() => undefined));
    again({readOnly: false});
    expect(screen.queryByTestId("archive-preview-error")).toBeNull();
});

test("the naming rules: the job's rounds named, aliases replaced", async () => {
    apiPut.mockImplementation(() => Promise.resolve(JSON.stringify(
        {data: {template: entries()[0]}})));
    show();
    fireEvent.click(screen.getByRole("tab", {name: i18n.t("archive_tpl_tab_rules")}));
    expect(screen.getAllByTestId("archive-tpl-round").map((r) => r.textContent))
        .toEqual(["Winners Round 1"]);
    fireEvent.change(screen.getByLabelText(i18n.t("archive_tpl_round_ja")),
                     {target: {value: "勝者側1回戦"}});
    fireEvent.click(screen.getByText(i18n.t("archive_tpl_alias_add")));
    const alias = screen.getByTestId("archive-tpl-alias");
    fireEvent.change(within(alias).getByLabelText(i18n.t("archive_tpl_alias_from")),
                     {target: {value: "らぎぴ/Ragipi"}});
    fireEvent.change(within(alias).getByLabelText(i18n.t("archive_tpl_alias_to")),
                     {target: {value: "らぎぴ"}});
    fireEvent.click(screen.getByLabelText(i18n.t("archive_tpl_order_winner_first")));
    fireEvent.click(field("archive-tpl-save"));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    const sent = apiPut.mock.calls[0][1].template;
    expect(sent.round_map).toEqual({"Winners Round 1": {ja: "勝者側1回戦"}});
    expect(sent.aliases).toEqual({"らぎぴ/Ragipi": "らぎぴ"});
    expect(sent.order).toBe("winner_first");
});

test("the thumbnail tab leads to the settings and picks the background", async () => {
    apiPatch.mockImplementation(() => Promise.resolve(JSON.stringify(
        {data: {available: true, thumbnail_background: "settings"}})));
    show();
    fireEvent.click(screen.getByRole("tab", {
        name: i18n.t("archive_tpl_tab_thumbnail")}));
    fireEvent.click(screen.getByText(i18n.t("archive_tpl_thumb_open")));
    expect(navigateToPanel).toHaveBeenCalledWith("thumbnail");
    fireEvent.click(screen.getByLabelText(i18n.t("archive_tpl_thumb_settings")));
    await waitFor(() => expect(onStatus).toHaveBeenCalledWith(
        {available: true, thumbnail_background: "settings"}));
    expect(apiPatch).toHaveBeenCalledWith("/api/v1/archive/settings",
                                          {thumbnail_background: "settings"});
});

test("outside the desktop's window it only reads", async () => {
    show({readOnly: true});
    expect(field("archive-tpl-title").readOnly).toBe(true);
    expect(field("archive-tpl-duplicate")).toBeDisabled();
    expect(screen.getByTestId("archive-preview-pane").textContent)
        .toContain(i18n.t("archive_tpl_preview_read_only"));
    await act(async () => {
        await sleep(800);
    });
    expect(previews()).toHaveLength(0);
});
