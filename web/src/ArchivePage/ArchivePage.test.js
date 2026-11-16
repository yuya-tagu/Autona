import React from "react";
import {act, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import i18n from "../i18n/config";
import ArchivePage from "./index";

jest.mock("../api", () => ({
    apiGetJson: jest.fn(),
    apiPost: jest.fn(),
    apiPatch: jest.fn(),
    ApiError: class ApiError extends Error {},
}));
// eslint-disable-next-line import/first
import {apiGetJson} from "../api";

// The archive page's async guards (ADR-006 A1): a late answer for a job that
// is no longer selected is dropped, and polling stops once the job is not
// running. The api is mocked; the clock is real (the poll is 1 s).

jest.setTimeout(20000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function job(id, state, extra = {}) {
    return {id, created_at: `2026-11-16T10:0${id === "A" ? 1 : 2}:00`,
            source: `src-${id}`, state, ...extra};
}

const SETS_B = {sets: [{no: 7, start: 0, end: 120, matched: true}]};

let calls;
let deferredA;

function install({statusExtra = {}, handlers = {}}) {
    calls = [];
    apiGetJson.mockImplementation((path) => {
        calls.push(path);
        if (handlers[path]) {
            return handlers[path]();
        }
        if (path === "/api/v1/archive/status") {
            return Promise.resolve({data: {available: true, work_dir: "w",
                                           ...statusExtra}});
        }
        return Promise.reject(new Error(`unexpected ${path}`));
    });
}

const count = (path) => calls.filter((c) => c === path).length;

test("a late answer for the previous job is ignored and polling stays off", async () => {
    deferredA = {};
    const jobs = {data: {jobs: [job("A", "running"), job("B", "done")]}};
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(jobs),
            "/api/v1/archive/jobs/A": () => new Promise((resolve) => {
                deferredA.resolve = resolve;
            }),
            "/api/v1/archive/jobs/B": () => Promise.resolve(
                {data: {job: job("B", "done"), sets: SETS_B}}),
        },
    });
    render(<ArchivePage/>);

    // The running job is opened on its own (status.running_job).
    await waitFor(() => expect(deferredA.resolve).toBeDefined());

    // Switch to B while A's answer is still in flight.
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await within(await screen.findByRole("listbox"))
        .findByText(/src-B/));
    expect(await screen.findByTestId("archive-set-7")).toBeTruthy();

    // A's late answer says "running": it must not replace B's view ...
    await act(async () => {
        deferredA.resolve({data: {job: job("A", "running"), sets: null}});
        await sleep(50);
    });
    expect(screen.getByTestId("archive-set-7")).toBeTruthy();
    expect(screen.queryByText(i18n.t("archive_abort"))).toBeNull();
    // ... and must not start polling A.
    const before = count("/api/v1/archive/jobs/A");
    await act(async () => {
        await sleep(1500);
    });
    expect(count("/api/v1/archive/jobs/A")).toBe(before);
});

test("polling stops when the job is no longer running", async () => {
    let n = 0;
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [job("A", "running")]}}),
            "/api/v1/archive/jobs/A": () => {
                n += 1;
                return Promise.resolve({data: {
                    job: job("A", n >= 2 ? "done" : "running"), sets: null}});
            },
        },
    });
    render(<ArchivePage/>);
    await waitFor(() => expect(n).toBeGreaterThanOrEqual(2), {timeout: 5000});
    const settled = count("/api/v1/archive/jobs/A");
    await act(async () => {
        await sleep(1500);
    });
    expect(count("/api/v1/archive/jobs/A")).toBe(settled);
});

test("a failing poll stops polling and shows the error", async () => {
    let n = 0;
    install({
        statusExtra: {running_job: "A"},
        handlers: {
            "/api/v1/archive/jobs": () => Promise.resolve(
                {data: {jobs: [job("A", "running")]}}),
            "/api/v1/archive/jobs/A": () => {
                n += 1;
                return n === 1
                    ? Promise.resolve({data: {job: job("A", "running"),
                                              sets: null}})
                    : Promise.reject(new Error("gone"));
            },
        },
    });
    render(<ArchivePage/>);
    expect(await screen.findByText(i18n.t("archive_request_failed"),
                                   {}, {timeout: 5000})).toBeTruthy();
    const settled = n;
    await act(async () => {
        await sleep(1500);
    });
    expect(n).toBe(settled);
});
