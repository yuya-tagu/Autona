import React from "react";
import {Provider} from "react-redux";
import {act, cleanup, fireEvent, render as renderInto, screen, waitFor} from "@testing-library/react";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import ArchivePage from "./index";
import {canCancel, isWorking, progressValue, sizeText} from "./archiveWorker";

jest.mock("../api", () => ({
    apiGetJson: jest.fn(),
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
    apiPut: jest.fn(),
    apiErrorStatus: (error) => error?.status,
    ApiError: class ApiError extends Error {},
}));
// The shell probe's module (hostApi) imports the socket: never connected.
jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {},
                                on: () => {}, off: () => {}})},
}));
// eslint-disable-next-line import/first
import {apiGetJson, apiPost} from "../api";

// The archive page's worker install (ADR-006 A7): with no usable worker the
// page is the install card (the latest release, the button, the progress,
// why it failed); once installed it is the job view, whose header shows the
// worker's version and, when a newer one is out, the update card. The api
// is mocked; the clock is real (the card asks again every second while the
// release check or the install runs).

jest.setTimeout(20000);

// The new job's form reads the loaded tournament from the state: the page
// is rendered under the store (no tournament in it here).
const render = (ui) => renderInto(
    <Provider store={autonaStore}>{ui}</Provider>);

const MB = 1024 * 1024;

function snapshot(extra = {}) {
    return {
        installed: null, env_command: false,
        latest: {version: "0.3.0", size: 146 * MB,
                 published_at: "2026-11-17T00:00:00Z"},
        check: {status: "done", error: null, message: null, checked_at: 1},
        update_available: false,
        job: {revision: 0, status: "idle", stage: null, done: 0, total: 0,
              version: null, error: null, message: null},
        ...extra,
    };
}

function job(extra = {}) {
    return {revision: 1, status: "running", stage: "download", done: 0,
            total: 146 * MB, version: "0.3.0", error: null, message: null,
            ...extra};
}

let calls;
let posts;
let status;
let workers;

// A read of the worker's snapshot that fails.
const FAIL = "fail";

// The server: the status (`status`), the worker's snapshots in turn (the
// last one repeats; FAIL fails that read), the shell's ticket (`shell`:
// forbidden outside the desktop's window).
function serve({shell = true} = {}) {
    calls = [];
    posts = [];
    apiGetJson.mockImplementation((path) => {
        calls.push(path);
        if (path.startsWith("/api/v1/archive/status")) {
            return Promise.resolve({data: status});
        }
        if (path.startsWith("/api/v1/archive/worker")) {
            const next = workers.length > 1 ? workers.shift() : workers[0];
            return next === FAIL ? Promise.reject(Object.assign(
                new Error("read"), {detail: "読めません。"}))
                : Promise.resolve({data: next});
        }
        if (path === "/api/v1/shell-session") {
            return shell ? Promise.resolve({data: {ticket: "never-kept"}})
                : Promise.reject(Object.assign(new Error("forbidden"),
                                               {status: 403}));
        }
        if (path === "/api/v1/archive/jobs") {
            return Promise.resolve({data: {jobs: []}});
        }
        if (path === "/api/v1/archive/templates") {
            return Promise.resolve({data: {templates: []}});
        }
        return Promise.reject(new Error(`unexpected ${path}`));
    });
}

beforeEach(() => {
    status = {available: false, reason: "切り抜きワーカー（vodsplit-worker.exe）が入っていません。"};
    workers = [snapshot()];
});

afterEach(() => {
    cleanup();
    apiGetJson.mockReset();
    apiPost.mockReset();
});

test("the helpers", () => {
    expect(sizeText(146 * MB)).toBe("146.0 MB");
    expect(isWorking(null)).toBe(false);
    expect(isWorking(snapshot())).toBe(false);
    expect(isWorking(snapshot({check: {status: "checking"}}))).toBe(true);
    expect(isWorking(snapshot({job: job()}))).toBe(true);
    expect(progressValue(job({done: 73 * MB}))).toBe(50);
    expect(progressValue(job({stage: "extract", done: 1, total: 4}))).toBe(25);
    expect(progressValue(job({stage: "verify"}))).toBeNull();
    expect(progressValue(job({stage: "download", total: 0}))).toBeNull();
    expect(canCancel(job())).toBe(true);
    expect(canCancel(job({stage: "probe"}))).toBe(true);
    expect(canCancel(job({stage: "swap"}))).toBe(false);
    expect(canCancel(job({status: "done"}))).toBe(false);
});

test("with no worker the page offers the latest and installs it", async () => {
    workers = [snapshot(), snapshot({job: job({done: 73 * MB})}),
               snapshot({job: job({stage: "probe"})}),
               snapshot({job: job({status: "done", stage: null,
                                   message: "切り抜きワーカー 0.3.0 を入れました。"})})];
    apiPost.mockImplementation((path, body) => {
        posts.push([path, body]);
        return Promise.resolve(JSON.stringify({data: {job: job()}}));
    });
    serve();
    render(<ArchivePage/>);
    expect(await screen.findByTestId("archive-worker-card")).toBeTruthy();
    expect(screen.getByText(i18n.t("archive_worker_install_title"))).toBeTruthy();
    expect(screen.getByText(i18n.t("archive_unavailable_reason",
                                   {reason: status.reason}))).toBeTruthy();
    expect((await screen.findByTestId("archive-worker-latest")).textContent)
        .toBe(i18n.t("archive_worker_latest",
                     {version: "0.3.0", size: "146.0 MB"}));
    const button = screen.getByTestId("archive-worker-install");
    await waitFor(() => expect(button).toBeEnabled());
    expect(button.textContent).toBe(i18n.t("archive_worker_install"));

    fireEvent.click(button);
    await waitFor(() => expect(posts).toEqual(
        [["/api/v1/archive/worker:install", {version: "0.3.0"}]]));
    // The download's progress, then a check, then done: the status is
    // asked again (with a probe) and the page is the job view.
    expect((await screen.findByTestId("archive-worker-progress")).textContent)
        .toContain(i18n.t("archive_worker_stage_download"));
    expect(screen.getByTestId("archive-worker-cancel")).toBeTruthy();
    status = {available: true, version: "0.3.0", work_dir: "w"};
    await waitFor(() => expect(calls).toContain(
        "/api/v1/archive/status?refresh=true"), {timeout: 6000});
    expect(await screen.findByTestId("archive-worker-version")).toBeTruthy();
    expect(screen.getByTestId("archive-worker-version").textContent).toBe(
        i18n.t("archive_worker_chip", {version: "0.3.0"}));
    expect(screen.queryByTestId("archive-worker-card")).toBeNull();
    // Its end is reported once.
    expect(calls.filter((path) => path === "/api/v1/archive/status?refresh=true"))
        .toHaveLength(1);
});

test("a failed read while an install runs does not stop the card asking", async () => {
    workers = [snapshot({job: job()}), FAIL,
               snapshot({job: job({stage: "probe"})}),
               snapshot({job: job({status: "done", stage: null,
                                   message: "切り抜きワーカー 0.3.0 を入れました。"})})];
    serve();
    render(<ArchivePage/>);
    expect(await screen.findByTestId("archive-worker-progress")).toBeTruthy();
    expect(await screen.findByText("読めません。", {}, {timeout: 4000}))
        .toBeTruthy();
    // Asked again (a longer interval) until the install's end.
    status = {available: true, version: "0.3.0", work_dir: "w"};
    await waitFor(() => expect(calls).toContain(
        "/api/v1/archive/status?refresh=true"), {timeout: 8000});
    expect(await screen.findByTestId("archive-worker-version")).toBeTruthy();
});

test("a read asked before the install's answer does not undo it", async () => {
    // The check runs (the card asks every second) while the install is
    // pressed; a read asked before the press answers after it.
    const checking = snapshot({check: {status: "checking", error: null,
                                       message: null, checked_at: null}});
    workers = [checking];
    let holding = false;
    let release = null;
    serve();
    const answer = apiGetJson.getMockImplementation();
    apiGetJson.mockImplementation((path) => {
        if (holding && path === "/api/v1/archive/worker") {
            holding = false;
            calls.push(path);
            return new Promise((resolve) => {
                release = () => resolve({data: checking});
            });
        }
        return answer(path);
    });
    apiPost.mockImplementation((path, body) => {
        posts.push([path, body]);
        return Promise.resolve(JSON.stringify({data: {job: job()}}));
    });
    render(<ArchivePage/>);
    const button = await screen.findByTestId("archive-worker-install");
    await waitFor(() => expect(button).toBeEnabled());
    holding = true;
    await waitFor(() => expect(release).not.toBeNull(), {timeout: 3000});
    workers = [snapshot({job: job({done: 10 * MB})})];
    fireEvent.click(button);
    expect(await screen.findByTestId("archive-worker-progress")).toBeTruthy();
    await act(async () => {
        release();
    });
    expect(screen.getByTestId("archive-worker-progress")).toBeTruthy();
    expect(screen.getByTestId("archive-worker-install")).toBeDisabled();
});

test("the update card does not offer again the version it installed", async () => {
    status = {available: true, version: "0.2.0", work_dir: "w"};
    const installed = {available: true, version: "0.2.0"};
    // The header's chips and the card read the same snapshots: the
    // install's begin once it is asked.
    workers = [snapshot({installed, update_available: true})];
    apiPost.mockImplementation((path, body) => {
        posts.push([path, body]);
        workers = [snapshot({installed, update_available: true, job: job()}),
                   snapshot({installed: null, update_available: false,
                             job: job({status: "done", stage: null,
                                       message: "切り抜きワーカー 0.3.0 を入れました。"})})];
        return Promise.resolve(JSON.stringify({data: {job: job()}}));
    });
    serve();
    render(<ArchivePage/>);
    fireEvent.click(await screen.findByTestId("archive-worker-update"));
    const button = await screen.findByTestId("archive-worker-install");
    await waitFor(() => expect(button).toBeEnabled());
    status = {available: true, version: "0.3.0", work_dir: "w"};
    fireEvent.click(button);
    expect((await screen.findByTestId("archive-worker-result", {},
                                      {timeout: 6000})).textContent)
        .toBe("切り抜きワーカー 0.3.0 を入れました。");
    expect(screen.getByTestId("archive-worker-install")).toBeDisabled();
});

test("a failed install says why and can be tried again", async () => {
    workers = [snapshot({job: job({status: "error", stage: null,
                                   error: "hash_mismatch",
                                   message: "一致しません（SHA-256）。"})})];
    serve();
    render(<ArchivePage/>);
    expect((await screen.findByTestId("archive-worker-result")).textContent)
        .toBe("一致しません（SHA-256）。");
    await waitFor(() => expect(screen.getByTestId("archive-worker-install"))
        .toBeEnabled());
});

test("a refused install says the server's words", async () => {
    apiPost.mockImplementation(() => Promise.reject(Object.assign(
        new Error("busy"), {detail: "切り抜きジョブの実行中は入れ替えられません。"})));
    serve();
    render(<ArchivePage/>);
    const button = await screen.findByTestId("archive-worker-install");
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect((await screen.findByTestId("archive-worker-refused")).textContent)
        .toBe("切り抜きジョブの実行中は入れ替えられません。");
});

test("the release check is followed and a failed one can be asked again", async () => {
    workers = [snapshot({latest: null, check: {status: "checking"}}),
               snapshot({latest: null,
                         check: {status: "error", error: "fetch_failed",
                                 message: "確認できません。"}})];
    serve();
    render(<ArchivePage/>);
    expect(await screen.findByText(i18n.t("archive_worker_checking")))
        .toBeTruthy();
    expect((await screen.findByTestId("archive-worker-check-error",
                                      {}, {timeout: 4000})).textContent)
        .toBe("確認できません。");
    expect(screen.getByTestId("archive-worker-install")).toBeDisabled();
    workers = [snapshot()];
    fireEvent.click(screen.getByTestId("archive-worker-recheck"));
    await waitFor(() => expect(calls).toContain(
        "/api/v1/archive/worker?refresh=true"));
    expect(await screen.findByTestId("archive-worker-latest")).toBeTruthy();
});

test("none usable is said, and an environment command is named", async () => {
    workers = [snapshot({latest: null, env_command: true})];
    serve();
    render(<ArchivePage/>);
    expect(await screen.findByText(i18n.t("archive_worker_no_release")))
        .toBeTruthy();
    expect(screen.getByTestId("archive-worker-env").textContent).toBe(
        i18n.t("archive_worker_env_command"));
    expect(screen.getByTestId("archive-worker-install")).toBeDisabled();
});

test("outside the desktop's window the card only reads", async () => {
    serve({shell: false});
    render(<ArchivePage/>);
    expect(await screen.findByTestId("archive-worker-read-only")).toBeTruthy();
    expect(screen.getByTestId("archive-worker-install")).toBeDisabled();
});

test("a newer worker is shown in the header and updated from its card", async () => {
    status = {available: true, version: "0.2.0", work_dir: "w"};
    workers = [snapshot({installed: {available: true, version: "0.2.0"},
                         update_available: true})];
    apiPost.mockImplementation((path, body) => {
        posts.push([path, body]);
        return Promise.resolve(JSON.stringify({data: {job: job()}}));
    });
    serve();
    render(<ArchivePage/>);
    expect((await screen.findByTestId("archive-worker-version")).textContent)
        .toBe(i18n.t("archive_worker_chip", {version: "0.2.0"}));
    const chip = await screen.findByTestId("archive-worker-update");
    expect(chip.textContent).toBe(
        i18n.t("archive_worker_update_available", {version: "0.3.0"}));
    expect(screen.queryByTestId("archive-worker-card")).toBeNull();
    fireEvent.click(chip);
    expect(await screen.findByText(i18n.t("archive_worker_update_title")))
        .toBeTruthy();
    expect(screen.getByText(i18n.t("archive_worker_installed",
                                   {version: "0.2.0"}))).toBeTruthy();
    const button = screen.getByTestId("archive-worker-install");
    expect(button.textContent).toBe(i18n.t("archive_worker_update"));
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(posts).toEqual(
        [["/api/v1/archive/worker:install", {version: "0.3.0"}]]));
});

test("no chip says an update when the worker is the latest", async () => {
    status = {available: true, version: "0.3.0", work_dir: "w"};
    workers = [snapshot({installed: {available: true, version: "0.3.0"}})];
    serve();
    render(<ArchivePage/>);
    expect(await screen.findByTestId("archive-worker-version")).toBeTruthy();
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
    });
    expect(screen.queryByTestId("archive-worker-update")).toBeNull();
});
