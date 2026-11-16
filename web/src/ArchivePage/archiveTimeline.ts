// The archive page's timeline (ADR-006 A3): where the sets, the games, the
// scoreboard corrections and the names read off the scoreboard lie on the
// video, as percents of the range shown (the whole video, or the selected
// set). Pure helpers: the page draws them in an SVG.

import {GameOcr, SetRow, SetsJson} from "./archiveSets";

export type TimelineView = "all" | "set";

export interface Range {
    start: number;
    end: number;
}

const finite = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);

// A span as [start, end], or null when it is not two numbers in order.
function span(value: unknown): [number, number] | null {
    if (!Array.isArray(value) || value.length < 2) {
        return null;
    }
    const [start, end] = value;
    return finite(start) && finite(end) && end >= start ? [start, end] : null;
}

// The video's length: sets.json's duration, else the last thing on it.
export function videoLength(sets: SetsJson): number {
    if (finite(sets.duration) && sets.duration > 0) {
        return sets.duration;
    }
    let end = 0;
    for (const row of sets.sets) {
        end = Math.max(end, finite(row.end) ? row.end : 0);
    }
    for (const game of sets.games ?? []) {
        end = Math.max(end, span(game)?.[1] ?? 0);
    }
    return Math.max(end, 1);
}

// The range the timeline shows: the whole video, or the selected set with
// `margin` seconds around it (where its IN / OUT can be dragged out of it),
// within the video.
export function timelineRange(sets: SetsJson, view: TimelineView,
                              row: SetRow | null, margin = 0): Range {
    if (view === "set" && row && finite(row.start) && finite(row.end)) {
        const end = Math.max(row.end, row.start + 1);
        if (!(margin > 0)) {
            return {start: row.start, end};
        }
        return {start: Math.max(0, row.start - margin),
                end: Math.min(Math.max(videoLength(sets), end), end + margin)};
    }
    return {start: 0, end: videoLength(sets)};
}

// Time `t` as a percent of `range`, clamped to it.
export function toPercent(t: number, range: Range): number {
    const length = range.end - range.start;
    if (!(length > 0)) {
        return 0;
    }
    return Math.min(100, Math.max(0, ((t - range.start) / length) * 100));
}

// Where the playhead is drawn for time `t`: its percent of `range`, or null
// when `t` lies outside it (the set view while the video plays elsewhere: no
// playhead pinned to an edge).
export function playheadPercent(t: number, range: Range): number | null {
    if (!finite(t) || t < range.start || t > range.end) {
        return null;
    }
    return toPercent(t, range);
}

// The time at `fraction` (0..1, clamped) of `range`: a click on the lane.
export function toTime(fraction: number, range: Range): number {
    const f = Math.min(1, Math.max(0, Number(fraction) || 0));
    return range.start + f * (range.end - range.start);
}

// A span as {left, width} percents of `range` (clipped to it), or null when
// it lies outside the range.
export function spanPercent(start: number, end: number,
                            range: Range): {left: number; width: number} | null {
    if (!(end > range.start) || !(start < range.end)) {
        return null;
    }
    const left = toPercent(start, range);
    return {left, width: toPercent(end, range) - left};
}

const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600,
                    7200];

// The ruler's ticks: multiples of the smallest step that cuts the range into
// at most `max` steps.
export function ticks(range: Range, max = 8): number[] {
    const length = range.end - range.start;
    if (!(length > 0)) {
        return [];
    }
    const step = TICK_STEPS.find((s) => length / s <= max)
        ?? Math.ceil(length / max / 3600) * 3600;
    const out: number[] = [];
    for (let t = Math.ceil(range.start / step) * step; t <= range.end;
         t += step) {
        out.push(t);
    }
    return out;
}

// A tick's label: h:mm when the ticks are whole minutes, else h:mm:ss.
export function tickLabel(t: number, wholeMinutes: boolean): string {
    const total = Math.max(0, Math.round(t));
    const h = Math.floor(total / 3600);
    const m = String(Math.floor(total / 60) % 60).padStart(2, "0");
    return wholeMinutes ? `${h}:${m}`
        : `${h}:${m}:${String(total % 60).padStart(2, "0")}`;
}

// The set `t` falls in (the first one), or null.
export function setAt(rows: SetRow[], t: number): SetRow | null {
    return rows.find((row) => finite(row.start) && finite(row.end)
        && t >= row.start && t <= row.end) ?? null;
}

function overlap(a: [number, number], b: [number, number]): number {
    return Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
}

// A set's games vodsplit moved there from the scoreboard's score
// (corrected_games: 1-based within the set) as spans.
export function correctedSpans(row: SetRow): [number, number][] {
    const corrected = Array.isArray(row.corrected_games)
        ? row.corrected_games : [];
    const spans = row.game_spans ?? [];
    return corrected
        .filter((k): k is number => Number.isInteger(k) && k >= 1)
        .map((k) => span(spans[k - 1]))
        .filter((s): s is [number, number] => s !== null);
}

export interface GameMark {
    start: number;
    end: number;
    corrected: boolean;
}

// The games lane: every detected game (sets.json "games"; a set's own
// game_spans when there is none), a corrected one marked.
export function gameMarks(sets: SetsJson): GameMark[] {
    const corrected = sets.sets.flatMap(correctedSpans);
    const games = (sets.games?.length
        ? sets.games : sets.sets.flatMap((row) => row.game_spans ?? []))
        .map(span)
        .filter((s): s is [number, number] => s !== null);
    return games.map(([start, end]) => ({
        start, end,
        corrected: corrected.some((c) => overlap(c, [start, end]) > 0),
    }));
}

// The names read off the scoreboard in the detected game that overlaps
// `game` (one of a set's game_spans) the most, or null.
export function gameNames(sets: SetsJson,
                          game: unknown): Record<string, number> | null {
    const target = span(game);
    const ocr: GameOcr[] = sets.games_ocr ?? [];
    if (target === null) {
        return null;
    }
    let best = -1;
    let most = 0;
    (sets.games ?? []).forEach((value, index) => {
        const s = span(value);
        const shared = s === null ? 0 : overlap(s, target);
        if (shared > most) {
            most = shared;
            best = index;
        }
    });
    const names = best >= 0 ? ocr[best]?.names : undefined;
    return names && typeof names === "object" ? names : null;
}

// The highest share of `names` (0 when there is none).
export function bestShare(names: Record<string, number> | null): number {
    return Math.max(0, ...Object.values(names ?? {}).filter(finite));
}

export interface PlayerCell {
    start: number;
    end: number;
    share: number;
}

export interface PlayerLane {
    name: string;
    cells: PlayerCell[];
}

// One lane per name read off the scoreboard: the games it was read in and
// its share there. The `max` names read the most (summed shares) first.
export function playerLanes(sets: SetsJson, max = 12): PlayerLane[] {
    const lanes = new Map<string, {cells: PlayerCell[]; total: number}>();
    const ocr = sets.games_ocr ?? [];
    (sets.games ?? []).forEach((value, index) => {
        const s = span(value);
        const names = ocr[index]?.names;
        if (s === null || !names || typeof names !== "object") {
            return;
        }
        for (const [name, share] of Object.entries(names)) {
            if (!finite(share) || share <= 0) {
                continue;
            }
            const lane = lanes.get(name) ?? {cells: [], total: 0};
            lane.cells.push({start: s[0], end: s[1], share});
            lane.total += share;
            lanes.set(name, lane);
        }
    });
    return [...lanes.entries()]
        .sort((a, b) => b[1].total - a[1].total || a[0].localeCompare(b[0]))
        .slice(0, max)
        .map(([name, lane]) => ({name, cells: lane.cells}));
}

// A share's shade: 0 (none) to 4 (darkest), the mock's four steps.
export function shadeLevel(share: number): 0 | 1 | 2 | 3 | 4 {
    if (!(share > 0)) {
        return 0;
    }
    return share >= 0.85 ? 4 : share >= 0.6 ? 3 : share >= 0.35 ? 2 : 1;
}
