// The archive clipper page's data (ADR-006 A1): the worker's sets.json and the
// job as /api/v1/archive answers them, and the pure helpers the page shows
// them with. Shapes follow vodsplit/export.py (save / write_outputs).

export interface SetResult {
    round?: string | null;
    p1?: string | null;
    s1?: number | null;
    p2?: string | null;
    s2?: number | null;
}

export interface SetRow {
    no: number;
    start: number;
    end: number;
    games?: number;
    matched?: boolean;
    title?: string | null;
    description?: string | null;
    set?: SetResult | null;
    labels?: string[];
    seen_names?: string[];
    game_starts?: number[];
    game_spans?: number[][];
    name_match?: unknown;
    corrected_games?: unknown;
    needs_review?: boolean;
    reason?: string | null;
    // The set's background frame in the job's out/ (vodsplit --thumbs; the
    // page reads it through /frames/<no>) and its time.
    thumb?: string | null;
    thumb_time?: number | null;
}

// Names read off the scoreboard in one detected game (sets.json "games_ocr",
// parallel to "games"): name -> share of the game's OCR samples showing it.
export interface GameOcr {
    samples?: number;
    names?: Record<string, number>;
}

export interface SetsJson {
    source?: string | null;
    source_id?: string | null;
    event?: string | null;
    duration?: number | null;
    games?: number[][];
    games_ocr?: GameOcr[];
    warnings?: string[];
    unused_sets?: SetResult[];
    sets: SetRow[];
}

export type JobState = "running" | "done" | "error" | "cancelled" | "failed";

export interface JobProgress {
    stage?: string;
    label?: string;
    index?: number;
    count?: number;
    fraction?: number;
    overall?: number;
    eta?: number | null;
}

export interface JobError {
    code?: string;
    message?: string;
    hint?: string;
}

export interface JobSummary {
    id: string;
    created_at: string;
    source: string;
    state: JobState;
    sets?: unknown;
    needs_review?: unknown;
}

export interface Job extends JobSummary {
    finished_at?: string | null;
    progress?: JobProgress | null;
    logs?: string[];
    warnings?: string[];
    error?: JobError | null;
    exit_code?: number | null;
    has_results?: boolean;
}

export interface ArchiveStatus {
    available: boolean;
    version?: string | null;
    format?: number | null;
    reason?: string | null;
    work_dir?: string | null;
    running_job?: string | null;
}

export type SetFilter = "all" | "matched" | "review";

// h:mm:ss (the hour always shown: the same shape in every link).
export function formatClock(seconds: number): string {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor(total / 60) % 60;
    const s = total % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// m:ss, minutes not wrapped at the hour (a set's length).
export function formatDuration(seconds: number): string {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// A YouTube id is made of these; anything else is not put into a URL.
const YOUTUBE_ID = /^[A-Za-z0-9_-]{6,20}$/;

// The link to a time of the source, or null for a local file / a bad id.
// Only a job whose source is itself an http(s) URL gets one: vodsplit's
// source_id of a local file can match an 11-char tail of its name.
export function youtubeLink(source: string | null | undefined,
                            sourceId: string | null | undefined,
                            seconds: number): string | null {
    if (typeof source !== "string" || !/^https?:\/\//i.test(source.trim())) {
        return null;
    }
    if (typeof sourceId !== "string" || !YOUTUBE_ID.test(sourceId)) {
        return null;
    }
    const t = Math.max(0, Math.floor(Number(seconds) || 0));
    return `https://www.youtube.com/watch?v=${sourceId}&t=${t}s`;
}

// vodsplit.export.display_label: names read off the scoreboard, else the raw
// tokens, else "no match".
export function displayLabel(row: SetRow, noMatch: string): string {
    const names = row.seen_names?.length ? row.seen_names : row.labels ?? [];
    return names.length > 0 ? `（${names.join(" / ")}）` : noMatch;
}

export function rowText(row: SetRow, noMatch: string): string {
    return row.title ? row.title : displayLabel(row, noMatch);
}

// "s1-s2" when the set has a score, else "".
export function scoreText(row: SetRow): string {
    const set = row.set;
    if (!set || set.s1 === null || set.s1 === undefined
        || set.s2 === null || set.s2 === undefined) {
        return "";
    }
    return `${set.s1}-${set.s2}`;
}

export function filterSets(rows: SetRow[], filter: SetFilter): SetRow[] {
    if (filter === "matched") {
        return rows.filter((row) => row.matched === true);
    }
    if (filter === "review") {
        return rows.filter((row) => row.needs_review === true);
    }
    return rows;
}

export interface Summary {
    sets: number;
    matched: number;
    review: number;
    games: number;
}

export function summarize(sets: SetsJson | null | undefined): Summary {
    const rows = sets?.sets ?? [];
    return {
        sets: rows.length,
        matched: rows.filter((row) => row.matched === true).length,
        review: rows.filter((row) => row.needs_review === true).length,
        games: sets?.games?.length ?? 0,
    };
}

// The remaining time as {min, sec}, or null when unknown.
export function etaParts(eta: number | null | undefined):
    {min: number; sec: number} | null {
    if (typeof eta !== "number" || !Number.isFinite(eta) || eta < 0) {
        return null;
    }
    const total = Math.round(eta);
    return {min: Math.floor(total / 60), sec: total % 60};
}

// "round: p1 s1-s2 p2" for a result no video block was assigned to.
export function unusedText(set: SetResult): string {
    const score = set.s1 !== null && set.s1 !== undefined
        && set.s2 !== null && set.s2 !== undefined ? `${set.s1}-${set.s2}` : "-";
    const head = set.round ? `${set.round}: ` : "";
    return `${head}${set.p1 ?? ""} ${score} ${set.p2 ?? ""}`.trim();
}

// A URL stays whole; a local path shrinks to its file name (either separator).
export function sourceName(source: string): string {
    if (/^https?:\/\//i.test(source)) {
        return source;
    }
    const parts = source.split(/[\\/]/).filter((part) => part !== "");
    return parts.length > 0 ? parts[parts.length - 1] : source;
}

// name_match as a whole percent ("99%"), or a dash when it is not a number.
export function nameMatchText(value: unknown): string {
    return typeof value === "number" && Number.isFinite(value)
        ? `${Math.round(value * 100)}%` : "—";
}

// corrected_games as "Game 2, 3"; null when nothing was corrected.
export function correctedText(value: unknown): string | null {
    if (!Array.isArray(value) || value.length === 0) {
        return null;
    }
    return `Game ${value.join(", ")}`;
}
