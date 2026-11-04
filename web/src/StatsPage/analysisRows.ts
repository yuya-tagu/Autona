// The tournament's match list (この大会の試合一覧, ADR-005 S4k): core's rows
// (AnalysisService.BracketRows, read from GET /api/v1/analysis) as the
// table's nine cells, and how the table sorts and filters them. Each cell
// is [text, sort key], the Qt panel's rules (the pre-S4k RowCells): an
// unknown name is its seed's "(seed N)", or "TBD"; an unknown seed "-"
// (sorted last); an unfinished set's score "-" (sorted as 0); no upset
// factor / seed over, and no losers run, an empty cell (sorted as 0). The
// wording is the page's -- core's rows carry none -- and, as in the Qt
// panel, the columns' and the cells' words are not translated.
// analysisRows.cases.json holds core's rows and the cells the Qt panel
// showed for them (tests/test_analysis_rows.py checks the same file
// against core).

export interface AnalysisSide {
    seed?: number | null;
    name?: string | null;
}

export interface AnalysisRow {
    round?: string | null;
    // [phase group id, round, index in the round]: the Round column's key.
    roundOrder?: SortKey[] | null;
    p1?: AnalysisSide | null;
    p2?: AnalysisSide | null;
    // Null until the set is finished.
    score?: [number, number] | null;
    upset?: number | null;
    losersRun?: number | null;
    seedOver?: number | null;
}

export type SortKey = number | string | SortKey[];
export type Cell = [string, SortKey];

export const COLUMNS = [
    "Round",
    "P1 Seed",
    "P1 Name",
    "Score",
    "P2 Name",
    "P2 Seed",
    "UpSet",
    "LR",
    "SeedOver",
];

// Centred and narrow, as the Qt panel's numeric columns were.
export const NUMERIC_COLUMNS: ReadonlySet<number> = new Set([1, 3, 5, 6, 7, 8]);

// What the filter box looks in: the round and both names.
const FILTER_COLUMNS = [0, 2, 4];

function seedCell(side: AnalysisSide): Cell {
    const seed = side.seed;
    return [seed ? String(seed) : "-", seed || 9999];
}

// A name sorts by its lower case. toLowerCase and Python's str.lower (the
// key tests/test_analysis_rows.py makes) both apply Unicode's full
// lower-case mapping, its special casings included (the final sigma, the
// dotted capital I); they differ only for a character that one of their
// Unicode versions has and the other has not. The cases file's name sorts
// check ASCII, full-width and astral (emoji) names against Python.
function nameCell(side: AnalysisSide): Cell {
    const seed = side.seed;
    const text = side.name || (seed ? `(seed ${seed})` : "TBD");
    return [text, text.toLowerCase()];
}

export function rowCells(row: AnalysisRow): Cell[] {
    const p1 = row.p1 ?? {};
    const p2 = row.p2 ?? {};
    const score = row.score;
    const upset = row.upset;
    const losersRun = row.losersRun || 0;
    const seedOver = row.seedOver;
    const finished = Array.isArray(score);
    return [
        [row.round || "", row.roundOrder ?? []],
        seedCell(p1),
        nameCell(p1),
        [finished ? `${score[0]}-${score[1]}` : "-",
         finished ? score[0] + score[1] : 0],
        nameCell(p2),
        seedCell(p2),
        [upset !== null && upset !== undefined ? String(upset) : "",
         upset || 0],
        [losersRun > 0 ? String(losersRun) : "", losersRun],
        [seedOver !== null && seedOver !== undefined ? String(seedOver) : "",
         seedOver || 0],
    ];
}

// Python's order of two strings: by their code points. Not JS's `<`, which
// compares UTF-16 code units -- an astral character (an emoji, a surrogate
// pair from 0xD800) would sort before "Ａ" (0xFF21) there, after it in
// Python.
function compareText(x: string, y: string): number {
    let i = 0;
    while (i < x.length && i < y.length) {
        const a = x.codePointAt(i) as number;
        const b = y.codePointAt(i) as number;
        if (a !== b) {
            return a < b ? -1 : 1;
        }
        // The same code point in both: as many code units in both.
        i += a > 0xffff ? 2 : 1;
    }
    return x.length === y.length ? 0 : x.length < y.length ? -1 : 1;
}

// Python's ordering of the keys: numbers by value, strings by their code
// points, lists element by element (a shorter list first when it is the
// start of the longer one).
export function compareKeys(a: SortKey, b: SortKey): number {
    if (Array.isArray(a) && Array.isArray(b)) {
        for (let i = 0; i < Math.min(a.length, b.length); i++) {
            const order = compareKeys(a[i], b[i]);
            if (order !== 0) {
                return order;
            }
        }
        return a.length - b.length;
    }
    if (typeof a === "number" && typeof b === "number") {
        return a < b ? -1 : a > b ? 1 : 0;
    }
    return compareText(String(a), String(b));
}

export interface Sort {
    column: number;
    descending: boolean;
}

// The Qt panel's: the Round column, ascending.
export const DEFAULT_SORT: Sort = {column: 0, descending: false};

// By the column's sort keys -- numbers as numbers, the Round column by its
// order in the bracket -- rows with the same key kept in core's order.
export function sortRows(rows: Cell[][], sort: Sort): Cell[][] {
    const sign = sort.descending ? -1 : 1;
    return rows
        .map((cells, index) => ({cells, index}))
        .sort((a, b) => sign * compareKeys(a.cells[sort.column][1],
                                           b.cells[sort.column][1])
            || a.index - b.index)
        .map(({cells}) => cells);
}

// ── the status line ──────────────────────────────────────────────────────

// `analysis` as published (StateSchema.ReadAnalysis).
export interface Progress {
    status?: unknown;
    error?: unknown;
    done?: unknown;
    total?: unknown;
    failed?: unknown;
    revision?: unknown;
}

type Translate = (key: string, values?: Record<string, unknown>) => string;

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

// What the fetch says, in the Qt panel's words: idle, the phases being
// fetched, n of m groups answered, the matches made (and the groups that
// failed), or the error -- no phase group, or the fetch failed. `rows`:
// how many rows the page has read for this fetch, null while they are not
// read (being read, or the read failed): a fetch that is over then says
// so without the count -- never its progress again.
export function progressLine(progress: Progress | null | undefined,
                             rows: number | null, t: Translate): string {
    const status = progress?.status;
    if (status === "running") {
        const total = count(progress?.total);
        return total === 0 ? t("stats_analysis_phases")
            : t("stats_analysis_progress",
                {done: count(progress?.done), total});
    }
    if (status === "done") {
        const failed = count(progress?.failed);
        const line = rows === null ? t("stats_analysis_fetched")
            : t("stats_analysis_done", {n: rows});
        return failed
            ? `${line} ${t("stats_analysis_failed_groups", {n: failed})}`
            : line;
    }
    if (status === "error") {
        return progress?.error === "no_phases"
            ? t("stats_analysis_no_phases") : t("stats_analysis_failed");
    }
    return t("stats_analysis_idle");
}

// ── filtering ────────────────────────────────────────────────────────────

// The rows whose round or either name holds the filter's text, any case
// (the Qt panel's filter).
export function filterRows(rows: Cell[][], filter: string): Cell[][] {
    const needle = filter.trim().toLowerCase();
    if (!needle) {
        return rows;
    }
    return rows.filter((cells) => FILTER_COLUMNS.some(
        (column) => cells[column][0].toLowerCase().includes(needle)));
}
