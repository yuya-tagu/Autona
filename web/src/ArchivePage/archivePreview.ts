// The archive page's preview (ADR-006 A3): which job's video and frames it
// asks for, what a key does there, which set comes next, and why a video does
// not play. Pure helpers (the DOM only as far as a key's target).

import {SetRow} from "./archiveSets";

const JOB_ID = /^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$/;
const API = "/api/v1/archive/jobs";

// The job's video and a set's frame (THIS_PC routes: the job names them,
// never a path).
export function videoPath(jobId: string): string {
    return `${API}/${encodeURIComponent(jobId)}/video`;
}

// A set's number as sets.json gives it, when it is one (a whole number the
// server answers frames for); else null -- never put in a URL or a selector.
export function setNumber(no: unknown): number | null {
    return typeof no === "number" && Number.isInteger(no) && no >= 0
        ? no : null;
}

// Set `no`'s frame, or null for what is not a set number.
export function framePath(jobId: string, no: unknown): string | null {
    const number = setNumber(no);
    return number === null ? null
        : `${API}/${encodeURIComponent(jobId)}/frames/${number}`;
}

// `url` as a CSS url() value: quoted, with what would end the string or the
// declaration escaped (CSS hex escapes).
export function cssUrl(url: string): string {
    const escaped = url.replace(/["\\\n\r\f]/g,
                                (c) => `\\${c.charCodeAt(0).toString(16)} `);
    return `url("${escaped}")`;
}

// This page with the job open, for this PC's browser (it plays H.264).
export function browserPageUrl(base: string, jobId: string): string {
    return `${base}/web/archive?job=${encodeURIComponent(jobId)}`;
}

// The job ?job= names, or null when it names none.
export function jobFromQuery(search: string): string | null {
    const job = new URLSearchParams(search).get("job");
    return job !== null && JOB_ID.test(job) ? job : null;
}

export type KeyAction = "togglePlay" | "previousSet" | "nextSet" | "nextReview"
    | "setIn" | "setOut" | "confirm";

// What a key's target is to the page's keys: typed into ("text": no key of
// the page), a control that takes Space itself ("control"), or anything else
// (a set row, the video, the page).
export type KeyTarget = "text" | "control" | "other";

const TEXT_ROLES = ["textbox", "searchbox", "combobox", "listbox", "option",
                    "menu", "menuitem", "slider", "spinbutton"];
const CONTROL_ROLES = ["button", "link", "checkbox", "radio", "switch", "tab"];

export function keyTargetKind(target: EventTarget | null): KeyTarget {
    const element = target as HTMLElement | null;
    if (!element || typeof element.closest !== "function") {
        return "other";
    }
    if (element.closest("[data-archive-set]")) {
        return "other";
    }
    const tag = element.tagName;
    const role = element.getAttribute("role") ?? "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
        || element.isContentEditable || TEXT_ROLES.includes(role)
        || element.closest('[role="listbox"], [role="menu"]')) {
        return "text";
    }
    if (tag === "BUTTON" || tag === "A" || tag === "SUMMARY"
        || CONTROL_ROLES.includes(role)) {
        return "control";
    }
    return "other";
}

export interface KeyLike {
    key: string;
    ctrlKey?: boolean;
    altKey?: boolean;
    metaKey?: boolean;
    shiftKey?: boolean;
    // Held down (the key's auto-repeat).
    repeat?: boolean;
    isComposing?: boolean;
    defaultPrevented?: boolean;
}

// The page's keys: Space plays / pauses, Up / Down move between the listed
// sets, N goes to the next one to review; I / O put the selected set's IN /
// OUT at the current position and Enter confirms it and moves on (ADR-006
// A4: the page acts on these only where it can edit). Not while typing or
// composing, not with a modifier, not Space or Enter on a button or a link
// (it presses it). The review's keys not with Shift either, and Enter not
// held down (its repeat would confirm set after set).
export function keyAction(event: KeyLike, target: KeyTarget): KeyAction | null {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey
        || event.altKey || event.metaKey || target === "text") {
        return null;
    }
    const review = ["i", "I", "o", "O", "Enter"].includes(event.key);
    if (review && (event.shiftKey || (event.key === "Enter" && event.repeat))) {
        return null;
    }
    switch (event.key) {
        case " ":
        case "Spacebar":
            return target === "control" ? null : "togglePlay";
        case "ArrowUp":
            return "previousSet";
        case "ArrowDown":
            return "nextSet";
        case "n":
        case "N":
            return "nextReview";
        case "i":
        case "I":
            return "setIn";
        case "o":
        case "O":
            return "setOut";
        case "Enter":
            return target === "control" ? null : "confirm";
        default:
            return null;
    }
}

// The set `delta` rows from `current` in `rows` (stopping at the ends); from
// none (or one not listed), the first going down and the last going up.
export function stepSet(rows: SetRow[], current: number | null,
                        delta: 1 | -1): number | null {
    if (rows.length === 0) {
        return null;
    }
    const index = rows.findIndex((row) => row.no === current);
    if (index < 0) {
        return (delta > 0 ? rows[0] : rows[rows.length - 1]).no;
    }
    return rows[Math.min(rows.length - 1, Math.max(0, index + delta))].no;
}

// The next set to review after `current` in `rows`, wrapping around (the
// current one when it is the only one), or null when there is none.
export function nextReviewSet(rows: SetRow[],
                              current: number | null): number | null {
    const index = rows.findIndex((row) => row.no === current);
    for (let k = 1; k <= rows.length; k += 1) {
        const row = rows[(index + k + rows.length) % rows.length];
        if (row.needs_review === true) {
            return row.no;
        }
    }
    return null;
}

// H.264 / AAC: what YouTube's mp4 is when no VP9 / AV1 was offered, and what
// Autona's window (QtWebEngine) cannot decode.
export const H264_TYPE = 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"';

export function canPlayH264(video: {canPlayType(type: string): string} | null):
    boolean {
    try {
        return (video?.canPlayType(H264_TYPE) ?? "") !== "";
    } catch {
        return false;
    }
}

export type PlaybackProblem = "missing" | "notPreviewable" | "openInBrowser"
    | "unplayable" | "unreachable";

// The server's answer for a job's video that is there but of a format the
// preview does not serve (.flv / .ts ...): the sets and the timeline still
// work.
export const NOT_PREVIEWABLE_STATUS = 415;

// The extension of the job's video (".flv"), to name the format the preview
// cannot play: a local job's own path, a URL job's download (the worker's
// sets.json `source`); null when there is none to name.
export function videoExtension(jobSource: string,
                               setsSource: unknown): string | null {
    const file = /^https?:\/\//i.test(jobSource.trim()) ? setsSource
        : jobSource;
    if (typeof file !== "string") {
        return null;
    }
    const name = file.trim().split(/[\\/]/).pop() ?? "";
    const match = /\.([A-Za-z0-9]{1,8})$/.exec(name);
    return match ? `.${match[1].toLowerCase()}` : null;
}

// MediaError's codes (the <video>'s `error.code`).
export const MEDIA_ERR_ABORTED = 1;
export const MEDIA_ERR_NETWORK = 2;
export const MEDIA_ERR_DECODE = 3;
export const MEDIA_ERR_SRC_NOT_SUPPORTED = 4;

// Why the video did not play, from the <video>'s error (its MediaError code,
// null when it has none), its URL's status (a HEAD after the error; null
// when it could not be asked) and whether this browser decodes H.264: the
// file is gone, it is a video of a format the preview does not serve (415),
// the server would not give it (or the network dropped it), or it is there
// and this browser cannot play it. Only a source this window
// does not support, served, in a window without H.264 (Autona's,
// QtWebEngine) offers this PC's browser -- a file that fails to decode does
// not play there either.
export function playbackProblem(errorCode: number | null,
                                status: number | null,
                                h264: boolean): PlaybackProblem {
    if (status === 404) {
        return "missing";
    }
    if (status === NOT_PREVIEWABLE_STATUS) {
        return "notPreviewable";
    }
    if (status === null || status < 200 || status >= 300
        || errorCode === MEDIA_ERR_NETWORK) {
        return "unreachable";
    }
    if (errorCode === MEDIA_ERR_SRC_NOT_SUPPORTED && !h264) {
        return "openInBrowser";
    }
    return "unplayable";
}

// Let go of a <video>'s source (the page leaves it, the job goes back to
// running): stop it and drop the request on the file, which the worker may
// write again. Never throws.
export function releaseVideo(video: {pause(): void; load(): void;
                                     removeAttribute(name: string): void}
                                 | null): void {
    if (!video) {
        return;
    }
    try {
        video.pause();
        video.removeAttribute("src");
        video.load();
    } catch {
        // A media element that cannot load (a test's DOM): nothing to drop.
    }
}
