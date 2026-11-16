// The archive page's review (ADR-006 A4): what the operator decides about the
// sets the worker found -- which are confirmed, their IN / OUT, which result
// each one is -- as GET / PUT /api/v1/archive/jobs/<id>/review carry it
// (core/ArchiveReview.py: one entry per set changed, keyed by its number,
// what an entry leaves out is the worker's), the sets as reviewed, and the
// saver that sends it. Pure helpers but the saver's timer.

import {SetResult, SetRow, SetsJson} from "./archiveSets";
import {Range, toTime} from "./archiveTimeline";

export interface ReviewEntry {
    confirmed?: boolean;
    start?: number;
    end?: number;
    // A result of the job's (a set's own or one of its unused_sets), or
    // null for none (the clip is exported without a title).
    set?: SetResult | null;
}

export interface Review {
    format?: number;
    sets: Record<string, ReviewEntry>;
}

export const EMPTY_REVIEW: Review = {format: 1, sets: {}};

// vodsplit export.MIN_CLIP: the worker refuses a shorter range (and so does
// the server, before it).
export const MIN_CLIP = 1;
// What of a range must lie before the video's end, less this (core
// ArchiveReview.END_MARGIN: the worker cuts an end past the end at the length
// it probes).
export const END_MARGIN = 0.05;

// A set as reviewed: its range and result, whether it is confirmed and what
// the review changed (the list, the details and the timeline draw this).
export interface ReviewedRow extends SetRow {
    confirmed: boolean;
    rangeEdited: boolean;
    setEdited: boolean;
}

const finite = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);

// `value` as JSON with its keys sorted: two results are the same when this is
// (core ArchiveReview.ResultKey makes the same string: whole numbers are
// integers in both). Every comparison of results goes through it.
export function canonical(value: unknown): string {
    if (Array.isArray(value)) {
        return `[${value.map(canonical).join(",")}]`;
    }
    if (value !== null && typeof value === "object") {
        const keys = Object.keys(value as object).sort();
        return `{${keys.map((key) => `${JSON.stringify(key)}:${
            canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
    }
    return JSON.stringify(value ?? null);
}

const ownSet = (row: SetRow): SetResult | null =>
    row.set !== null && typeof row.set === "object" ? row.set : null;

export const sameResult = (a: SetResult | null | undefined,
                           b: SetResult | null | undefined): boolean =>
    canonical(a ?? null) === canonical(b ?? null);

// The review as read from the server, or an empty one for what is not one.
export function readReview(value: unknown): Review {
    const sets = (value as Review | null)?.sets;
    return sets !== null && typeof sets === "object" && !Array.isArray(sets)
        ? {format: 1, sets: {...sets}} : {...EMPTY_REVIEW, sets: {}};
}

function entryOf(review: Review, row: SetRow): ReviewEntry {
    return review.sets[String(row.no)] ?? {};
}

// A result as a set's line ("Winners Final Alpha vs Beta"): what the list
// shows for a set given another result than the worker's (its title was
// made for that one; the export makes the new one from its template).
export function resultTitle(set: SetResult): string {
    return [set.round, `${set.p1 ?? ""} vs ${set.p2 ?? ""}`.trim()]
        .filter(Boolean).join(" ");
}

export function reviewedRow(row: SetRow, review: Review): ReviewedRow {
    const entry = entryOf(review, row);
    const start = finite(entry.start) ? entry.start : row.start;
    const end = finite(entry.end) ? entry.end : row.end;
    const setEdited = "set" in entry;
    const set = setEdited ? entry.set ?? null : ownSet(row);
    return {
        ...row, start, end,
        ...(setEdited ? {set, matched: set !== null,
                         title: set ? resultTitle(set) : null} : {}),
        confirmed: entry.confirmed === true,
        rangeEdited: start !== row.start || end !== row.end,
        setEdited,
    };
}

export function reviewedRows(rows: SetRow[], review: Review): ReviewedRow[] {
    return rows.map((row) => reviewedRow(row, review));
}

// `review` with set `row`'s entry changed by `change` (undefined drops a
// key), keeping only what differs from the worker's (as the server keeps it).
export function withEntry(review: Review, row: SetRow,
                          change: Partial<Record<keyof ReviewEntry, unknown>>):
    Review {
    const merged: Record<string, unknown> = {...entryOf(review, row), ...change};
    const entry: ReviewEntry = {};
    if (merged.confirmed === true) {
        entry.confirmed = true;
    }
    if (finite(merged.start) && merged.start !== row.start) {
        entry.start = merged.start;
    }
    if (finite(merged.end) && merged.end !== row.end) {
        entry.end = merged.end;
    }
    if ("set" in merged && merged.set !== undefined
        && !sameResult(merged.set as SetResult | null, ownSet(row))) {
        entry.set = (merged.set as SetResult | null) ?? null;
    }
    const sets = {...review.sets};
    if (Object.keys(entry).length > 0) {
        sets[String(row.no)] = entry;
    } else {
        delete sets[String(row.no)];
    }
    return {...review, sets};
}

// Seconds kept to 2 decimals (the server's), rounded so that a range made
// from them keeps its minimum length: IN down, OUT up.
const down = (t: number) => Math.floor(t * 100 + 1e-6) / 100;
const up = (t: number) => Math.ceil(t * 100 - 1e-6) / 100;
const HUNDREDTH = 0.01;

const videoEnd = (duration: number | null | undefined): number | null =>
    finite(duration) && duration > 0 ? duration : null;

// Whether [start, end] is a range the server and the worker take, by their
// arithmetic (core ArchiveReview._RangeFits): two decimals do not make an
// exact length in floats (1.13 - 0.13 < 1).
export function rangeFits(start: number, end: number,
                          duration: number | null | undefined): boolean {
    if (start < 0 || end - start < MIN_CLIP) {
        return false;
    }
    const known = videoEnd(duration);
    return known === null
        || Math.min(end, known - END_MARGIN) - start >= MIN_CLIP;
}

// The range with its IN (`edge` "start") or OUT moved to `t`, kept at least
// MIN_CLIP long and inside the video (`duration`, when known: OUT up to it,
// and MIN_CLIP of the range before its end less END_MARGIN). A rounded range
// that falls a hair short of MIN_CLIP takes a hundredth more on the moved
// side.
export function movedRange(range: Range, edge: "start" | "end", t: number,
                           duration: number | null | undefined): Range {
    const known = videoEnd(duration);
    const at = finite(t) ? t : 0;
    if (edge === "start") {
        const tail = known === null ? Infinity : known - END_MARGIN;
        let start = down(Math.max(0, Math.min(at,
                                              Math.min(range.end, tail) - MIN_CLIP)));
        for (let k = 0; k < 5 && start > 0
             && !rangeFits(start, range.end, known); k += 1) {
            start = down(Math.max(0, start - HUNDREDTH));
        }
        return {start, end: range.end};
    }
    const limit = known === null ? Infinity : down(known);
    let end = up(Math.min(limit, Math.max(at, range.start + MIN_CLIP)));
    for (let k = 0; k < 5 && end - range.start < MIN_CLIP
         && end + HUNDREDTH <= limit; k += 1) {
        end = up(end + HUNDREDTH);
    }
    return {start: range.start, end};
}

// `review` with set `row`'s IN or OUT at `t`.
export function withEdge(review: Review, row: SetRow, edge: "start" | "end",
                         t: number, duration: number | null | undefined): Review {
    const now = reviewedRow(row, review);
    const range = movedRange({start: now.start, end: now.end}, edge, t, duration);
    return withEntry(review, row, {start: range.start, end: range.end});
}

// `review` with set `row`'s range back to the worker's.
export function withRangeReset(review: Review, row: SetRow): Review {
    return withEntry(review, row, {start: undefined, end: undefined});
}

export function withConfirmed(review: Review, row: SetRow,
                              confirmed: boolean): Review {
    return withEntry(review, row, {confirmed});
}

// `review` with set `row` given `result` (null: no result).
export function withResult(review: Review, row: SetRow,
                           result: SetResult | null): Review {
    return withEntry(review, row, {set: result});
}

// The results a set may be given (core ArchiveReview.Candidates): each set's
// own and sets.json's unused_sets, once each.
export function candidates(sets: SetsJson): SetResult[] {
    const found: SetResult[] = [];
    const seen = new Set<string>();
    const add = (value: unknown) => {
        if (value !== null && typeof value === "object" && !Array.isArray(value)) {
            const key = canonical(value);
            if (!seen.has(key)) {
                seen.add(key);
                found.push(value as SetResult);
            }
        }
    };
    sets.sets.forEach((row) => add(row.set));
    (sets.unused_sets ?? []).forEach(add);
    return found;
}

// The set (as reviewed) other than `except` that holds each result, by its
// canonical JSON: a result is given to one set at most.
export function holders(rows: ReviewedRow[], except: number | null):
    Map<string, number> {
    const held = new Map<string, number>();
    for (const row of rows) {
        if (row.no !== except && row.set) {
            held.set(canonical(row.set), row.no);
        }
    }
    return held;
}

// The next set after `current` in `rows` that is not confirmed, wrapping
// around, or null when every other one is (Enter confirms and moves on).
export function nextUnconfirmed(rows: ReviewedRow[],
                                current: number | null): number | null {
    const index = rows.findIndex((row) => row.no === current);
    for (let k = 1; k < rows.length + (index < 0 ? 1 : 0); k += 1) {
        const row = rows[(index + k + rows.length) % rows.length];
        if (!row.confirmed) {
            return row.no;
        }
    }
    return null;
}

export function confirmedCount(rows: ReviewedRow[]): number {
    return rows.filter((row) => row.confirmed).length;
}

// The time under a pointer at `clientX` over a lane `left` / `width` pixels
// wide showing `range` (a drag of IN / OUT).
export function pointerTime(clientX: number, left: number, width: number,
                            range: Range): number {
    return toTime(width > 0 ? (clientX - left) / width : 0, range);
}

export type SaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

export interface SaveState {
    status: SaveStatus;
    error?: unknown;
}

export const SAVE_DELAY_MS = 600;

export interface SendOptions {
    // The page is going away: the request must outlive it (fetch keepalive).
    keepalive?: boolean;
}

// Sends the latest value pushed, `delayMs` after the last push (edits in a
// row are one save), one at a time and in order (each PUT replaces the
// whole review). A value that failed is kept for `flush` (the export saves
// first) and `dispose`, and dropped by the next push, which supersedes it,
// or by `forget` (a value the server refused). After `dispose` what is
// pending or failed is still sent, but nothing is reported.
export class ReviewSaver<T> {
    private pending: T | undefined;
    private failed: T | undefined;
    private flight: Promise<void> | null = null;
    private timer: ReturnType<typeof setTimeout> | null = null;
    private disposed = false;

    constructor(private readonly send: (value: T, options?: SendOptions) =>
                    Promise<unknown>,
                private readonly onState: (state: SaveState) => void,
                private readonly delayMs = SAVE_DELAY_MS) {}

    private report(state: SaveState) {
        if (!this.disposed) {
            this.onState(state);
        }
    }

    private clearTimer() {
        if (this.timer !== null) {
            clearTimeout(this.timer);
            this.timer = null;
        }
    }

    push(value: T) {
        if (this.disposed) {
            return;
        }
        this.pending = value;
        this.failed = undefined;
        this.report({status: "pending"});
        this.clearTimer();
        this.timer = setTimeout(() => {
            this.timer = null;
            this.start();
        }, this.delayMs);
    }

    private start() {
        if (this.flight !== null || this.pending === undefined) {
            return;
        }
        const value = this.pending;
        this.pending = undefined;
        this.report({status: "saving"});
        this.flight = this.send(value).then(() => {
            if (this.pending === undefined) {
                this.report({status: "saved"});
            }
        }, (error) => {
            if (this.pending === undefined) {
                this.failed = value;
                this.report({status: "error", error});
            }
        }).then(() => {
            this.flight = null;
            // A push while it was in flight whose delay is over.
            if (this.pending !== undefined && this.timer === null) {
                this.start();
            }
        });
    }

    // Send what is pending (or failed last) now and wait for it -> whether
    // the last value was saved.
    async flush(): Promise<boolean> {
        this.clearTimer();
        if (this.pending === undefined && this.failed !== undefined) {
            this.pending = this.failed;
            this.failed = undefined;
        }
        while (this.flight !== null || this.pending !== undefined) {
            if (this.flight === null) {
                this.start();
            }
            await this.flight;
        }
        return this.failed === undefined;
    }

    // Drop the value that failed (the server refused it: what it has is read
    // again instead).
    forget() {
        this.failed = undefined;
    }

    // Whether nothing is waiting to be sent or being sent.
    idle(): boolean {
        return this.flight === null && this.pending === undefined
            && this.failed === undefined;
    }

    // The page is being left: send what is waiting now, in a request that
    // outlives the page (not awaited, nothing reported; one in flight is
    // older and may still arrive after it).
    sendOnUnload() {
        this.clearTimer();
        const value = this.pending ?? this.failed;
        this.pending = undefined;
        this.failed = undefined;
        if (value !== undefined) {
            this.send(value, {keepalive: true}).catch(() => undefined);
        }
    }

    // Stop reporting; what is pending or failed last is still sent -> settled
    // once it was (whether it was saved): a view opened on the job again reads
    // its review after it.
    dispose(): Promise<boolean> {
        this.disposed = true;
        return this.flush();
    }
}
