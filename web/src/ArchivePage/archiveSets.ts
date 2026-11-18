// The archive clipper page's data (ADR-006 A1): the worker's sets.json and the
// job as /api/v1/archive answers them, and the pure helpers the page shows
// them with. Shapes follow vodsplit/export.py (save / write_outputs).

export interface SetResult {
    round?: string | null;
    p1?: string | null;
    s1?: number | null;
    p2?: string | null;
    s2?: number | null;
    // The characters (ADR-006 A5, vodsplit's optional fields): the display
    // names ("/" between two), each game's, where they came from, the set's
    // id in its source, and Autona's own key (setCharacters).
    c1?: string | null;
    c2?: string | null;
    games?: {c1?: string | null; c2?: string | null}[] | null;
    char_source?: string | null;
    id?: string | number | null;
    autona?: unknown;
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
    // The name of the template the worker made the titles with (A6).
    template?: string | null;
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

// A clip of the export's clips/export.json (ADR-006 A4).
export interface ClipFile {
    no: number;
    file: string;
    title?: string | null;
    start?: number | null;
    end?: number | null;
    exported?: boolean;
    skipped?: boolean;
}

// The job's last export of its confirmed sets (ADR-006 A4): its state as a
// job's, the clips cut and skipped (already cut from the same range), the
// folder they are in and the clips listed there (null while it runs).
export interface JobExport {
    state: JobState;
    started_at?: string | null;
    finished_at?: string | null;
    exit_code?: number | null;
    error?: JobError | null;
    warnings?: string[];
    logs?: string[];
    progress?: JobProgress | null;
    clips?: number | null;
    skipped?: number | null;
    out_dir?: string | null;
    files?: ClipFile[] | null;
}

// The job's thumbnails (ADR-006 A6): how far the drawing of the confirmed
// sets' is ("running"), how this run of the app's last drawing of them ended
// (or null), and the sets that have one in the job's thumbs/ ("revision"
// moves with every thumbnail written: the page's cache-buster).
export interface JobThumbs {
    state: "running" | "done" | "cancelled" | "failed" | null;
    done: number;
    total: number;
    failed: number[];
    files: number[];
    revision: number;
}

export interface Job extends JobSummary {
    finished_at?: string | null;
    progress?: JobProgress | null;
    logs?: string[];
    warnings?: string[];
    error?: JobError | null;
    exit_code?: number | null;
    has_results?: boolean;
    export?: JobExport | null;
    thumbs?: JobThumbs | null;
    // Whether running its analysis again reuses the worker's cache (the
    // video it was made from is still there): a reanalysis says so first.
    analysis_cached?: boolean;
}

export interface ArchiveStatus {
    available: boolean;
    version?: string | null;
    format?: number | null;
    reason?: string | null;
    work_dir?: string | null;
    running_job?: string | null;
    // What it runs: the job's analysis or the export of its sets (A4).
    running_kind?: "analysis" | "export" | null;
    // What the worker says it can do besides runs ("titles": the template
    // page's preview, vodsplit V10) and the thumbnails' background (A6).
    features?: string[];
    thumbnail_background?: "frame" | "settings";
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

// ── The results' characters (ADR-006 A5) ─────────────────────────────────

// Where a set's characters came from (POST results:prepare's char_source and
// summary): the match log's record, start.gg's report, the player's main in
// the player database (an estimate), a JSON file's own, none.
export const CHARACTER_SOURCES = ["scoreboard", "report", "mains", "given",
                                  "none"] as const;
export type CharacterSource = typeof CHARACTER_SOURCES[number];

// What POST /api/v1/archive/results:prepare answers besides the results.
export interface PrepareSummary {
    sets: number;
    game: string | null;
    records: number;
    sources: Partial<Record<CharacterSource, number>>;
}

// A day of GET /api/v1/archive/match-logs.
export interface MatchLogDay {
    date: string;
    records: number;
    tournaments: {name: string; records: number}[];
}

// The codenames behind a set's c1 / c2, their game and their icons (the
// "autona" key Autona puts in a result set; vodsplit keeps it in sets.json):
// an icon is its file in the game's folder, as the game's icon pack names it
// (codename -> "base_files/icon/<file>"; a codename with none is not in it).
export interface SetCharacters {
    game: string;
    c1: string[];
    c2: string[];
    icons: Record<string, string>;
}

// A game's or a character's codename, or a file's name, as it may name a
// file: nothing that leaves its folder.
const ASSET_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;

function assetName(value: unknown): value is string {
    return typeof value === "string" && ASSET_NAME.test(value)
        && !value.includes("..");
}

// A file's path in a game's folder: names as assetName takes them, "/"
// between.
function assetPath(value: unknown): value is string {
    return typeof value === "string"
        && value.split("/").every((part) => assetName(part));
}

// The set's codenames, or null when it has none (a set with no character,
// a JSON file's set, a malformed key). An icon that may leave the game's
// folder is left out.
export function setCharacters(set: SetResult | null | undefined):
    SetCharacters | null {
    const key = set?.autona as {
        game?: unknown; c1?: unknown; c2?: unknown; icons?: unknown;
    } | null | undefined;
    if (!key || typeof key !== "object" || !assetName(key.game)) {
        return null;
    }
    const side = (value: unknown) => Array.isArray(value)
        ? value.filter(assetName) : [];
    const icons: Record<string, string> = {};
    if (key.icons && typeof key.icons === "object"
        && !Array.isArray(key.icons)) {
        for (const [codename, file] of Object.entries(key.icons)) {
            if (assetName(codename) && assetPath(file)) {
                icons[codename] = file;
            }
        }
    }
    const found = {game: key.game, c1: side(key.c1), c2: side(key.c2), icons};
    return found.c1.length > 0 || found.c2.length > 0 ? found : null;
}

// A character's icon, the server's path (as framePath: after BASE_URL):
// `file` (setCharacters' icons) in the game's folder; null when there is no
// file or a name may not name one. The page hides an icon that does not
// load.
export function characterIcon(game: string, file: string | null | undefined):
    string | null {
    if (!assetName(game) || !assetPath(file)) {
        return null;
    }
    return `/user_data/games/${game}/${file}`;
}

// A game's characters' colours (GET /api/v1/archive/characters?game=, ADR-006
// A6; core GameAssetCatalog.GameCharacterSkins): codename -> its skins in
// ascending order, a skin being the number in its files' names (what the
// review's skins name), its icon that skin's file in the game's folder (as
// setCharacters' icons), null when it has none.
export interface CharacterSkin {
    skin: number;
    icon: string | null;
}
export type GameSkins = Record<string, CharacterSkin[]>;

// The skins of the server's answer that are one (a whole number >= 0, once
// each; an icon that may leave the game's folder is none).
export function readGameSkins(value: unknown): GameSkins {
    const table: GameSkins = {};
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return table;
    }
    for (const [codename, rows] of Object.entries(value)) {
        if (!assetName(codename) || !Array.isArray(rows)) {
            continue;
        }
        const seen = new Set<number>();
        table[codename] = rows.flatMap((row) => {
            const skin = (row as {skin?: unknown} | null)?.skin;
            const icon = (row as {icon?: unknown} | null)?.icon;
            if (typeof skin !== "number" || !Number.isInteger(skin) || skin < 0
                || seen.has(skin)) {
                return [];
            }
            seen.add(skin);
            return [{skin, icon: assetPath(icon) ? icon : null}];
        });
    }
    return table;
}

// The icon (a file in the game's folder, as setCharacters' icons) of
// `codename` drawn in `skin` (the review's; 0 or none is its first colour):
// that skin's icon in `table` when it has one, else the set's own.
export function skinIcon(characters: SetCharacters, codename: string,
                         skin: number | undefined,
                         table: GameSkins | null | undefined): string | undefined {
    const found = skin ? table?.[codename]?.find((row) => row.skin === skin)
        : undefined;
    return found?.icon ?? characters.icons[codename];
}

// Whether the set's characters are (partly) an estimate: a player's main.
export function isEstimate(set: SetResult | null | undefined): boolean {
    return set?.char_source === "mains";
}

// The summary's counts that are not 0, in CHARACTER_SOURCES' order.
export function sourceCounts(summary: PrepareSummary):
    [CharacterSource, number][] {
    return CHARACTER_SOURCES
        .map((key): [CharacterSource, number] => [key, summary.sources?.[key] ?? 0])
        .filter(([, n]) => n > 0);
}

// YYYYMMDD as YYYY-MM-DD.
export function dayText(date: string): string {
    return /^[0-9]{8}$/.test(date)
        ? `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)}` : date;
}
