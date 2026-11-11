// The SCORE page's set selection (ADR-005 S4ab): the rows of GET
// /api/v1/tournament/sets and /stations as the Qt set and station windows
// showed them (AutonaSelectSetWindow / AutonaSelectStationWindow, removed in
// ADR-005 S4ac-3), and what the windows did with them -- the search, the
// order, the row OK / Enter takes.
// The rows carry no wording (external-control-api.md §5.18): the doubles'
// " (A, B)" and the filters are the page's. setRows.cases.json holds what the
// windows showed for core's rows (tests/test_set_selection.py checks the same
// file against core; against the windows too until S4ac-3).

export interface SetSide {
    name?: unknown;
    members?: unknown;
}

// A row of GET /tournament/sets.
export interface SetRow {
    id?: unknown;
    stream?: unknown;
    station?: unknown;
    phase?: unknown;
    pool?: unknown;
    round?: unknown;
    p1?: SetSide | null;
    p2?: SetSide | null;
}

// A row of GET /tournament/stations: what POST /scoreboards/{n}:follow takes
// as it is.
export interface StationRow {
    type?: unknown;
    id?: unknown;
    identifier?: unknown;
    stream?: unknown;
}

// A row of GET /tournament/phases.
export interface PhaseRow {
    id?: unknown;
    name?: unknown;
}

function cell(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    return typeof value === "number" ? String(value) : "";
}

function members(side: SetSide | null | undefined): string[] {
    return Array.isArray(side?.members)
        ? side!.members.map((member) => cell(member)) : [];
}

// The window's Player columns: a side's name and, for doubles -- the first
// side having more than one member -- both sides' members in brackets (the
// window's punctuation, the same in both languages).
function sideCell(row: SetRow, side: SetSide | null | undefined): string {
    const name = cell(side?.name);
    if (members(row.p1).length > 1) {
        return `${name} (${members(side).join(", ")})`;
    }
    return name;
}

// Stream, Station, Phase, Pool, Match, Player 1, Player 2.
export function setCells(row: SetRow): string[] {
    return [cell(row.stream), cell(row.station), cell(row.phase),
            cell(row.pool), cell(row.round), sideCell(row, row.p1),
            sideCell(row, row.p2)];
}

// "Show complete pairs": both sides named.
export function isCompletePair(row: SetRow): boolean {
    return cell(row.p1?.name) !== "" && cell(row.p2?.name) !== "";
}

// Type, Id, Stream, Identifier.
export function stationCells(row: StationRow): string[] {
    return [cell(row.type), cell(row.id), cell(row.stream),
            cell(row.identifier)];
}

// A row of the table: its cells and what the search also reaches (the set
// window's hidden id column), with its index in the rows read.
export interface Shown<T> {
    index: number;
    row: T;
    cells: string[];
    search: string[];
}

export function setShown(rows: SetRow[]): Shown<SetRow>[] {
    return rows.map((row, index) => {
        const cells = setCells(row);
        return {index, row, cells, search: [...cells, cell(row.id)]};
    });
}

export function stationShown(rows: StationRow[]): Shown<StationRow>[] {
    return rows.map((row, index) => {
        const cells = stationCells(row);
        return {index, row, cells, search: cells};
    });
}

// The windows' search: any column (and the set's id) holding the text, any
// case. The text as typed, as the windows take it.
export function filterShown<T>(shown: Shown<T>[], filter: string): Shown<T>[] {
    if (!filter) {
        return shown;
    }
    const needle = filter.toLowerCase();
    return shown.filter((item) => item.search.some(
        (text) => text.toLowerCase().includes(needle)));
}

export interface Sort {
    column: number;
    descending: boolean;
}

// A header clicked: its column ascending, the column sorted by the other way
// round (the windows' header).
export function nextSort(sort: Sort | null, column: number): Sort {
    return sort?.column === column
        ? {column, descending: !sort.descending}
        : {column, descending: false};
}

// The windows' order: none until a header is clicked, then the column's text
// compared as Qt compares it (case-sensitive, code unit by code unit -- "B"
// before "a", "10" before "2"), the rows the same there kept in the order
// read, either way round.
export function sortShown<T>(shown: Shown<T>[], sort: Sort | null): Shown<T>[] {
    if (sort === null) {
        return shown;
    }
    const sign = sort.descending ? -1 : 1;
    return [...shown].sort((a, b) => {
        const x = a.cells[sort.column] ?? "";
        const y = b.cells[sort.column] ?? "";
        return (x < y ? -sign : x > y ? sign : 0) || a.index - b.index;
    });
}

// Whether OK / Enter with no row selected takes the first row shown, as both
// windows' LoadSelectedSet does. Kept by the owner's call (2026-11-11,
// question 3): on boards 1/2 what it loads waits for 反映, so a mistaken
// pick does not reach the stream. This is the one place to change it.
export const TAKE_FIRST_ROW_WHEN_NONE_SELECTED = true;

// The row OK / Enter takes: the one selected (if it is shown), else -- see
// above -- the first one shown. null: none (nothing is loaded).
export function chosenRow<T>(shown: Shown<T>[], selected: number | null):
        Shown<T> | null {
    const picked = shown.find((item) => item.index === selected);
    if (picked) {
        return picked;
    }
    return TAKE_FIRST_ROW_WHEN_NONE_SELECTED && shown.length > 0
        ? shown[0] : null;
}

// ── the phases ───────────────────────────────────────────────────────────

// The phase combo's values: "" ("Select a phase..."), "all" ("All phases")
// or a phase's id as text.
export const NO_PHASE = "";
export const ALL_PHASES = "all";

// What the combo shows before the operator picks: the phase of the rows
// fetched last (their query), else the only phase of a one-phase event, else
// "Select a phase..." (the window's _PreferredPhaseIndex).
export function defaultPhase(phases: PhaseRow[],
                             query: {phase?: unknown} | null): string {
    const fetched = query?.phase;
    if (fetched === ALL_PHASES && phases.length > 0) {
        return ALL_PHASES;
    }
    if (fetched !== undefined && fetched !== null
            && phases.some((phase) => cell(phase.id) === cell(fetched))) {
        return cell(fetched);
    }
    return phases.length === 1 ? cell(phases[0].id) : NO_PHASE;
}

// What POST /tournament/sets:refresh is asked with for the combo's value:
// the phase row's id, "all", or -- with no phases known -- null (the whole
// event). undefined: a phase must be chosen first (the window's "Select a
// phase to load sets").
export function phaseQuery(phases: PhaseRow[], value: string):
        unknown | undefined {
    if (phases.length === 0) {
        return null;
    }
    if (value === ALL_PHASES) {
        return ALL_PHASES;
    }
    const phase = phases.find((row) => cell(row.id) === value);
    return phase ? phase.id : undefined;
}

// ── the status line ──────────────────────────────────────────────────────

// set_selection.sets as published (StateSchema.ReadSetSelectionFetch).
export interface Progress {
    status?: unknown;
    error?: unknown;
    done?: unknown;
    total?: unknown;
    failed?: unknown;
    revision?: unknown;
}

function number(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

// The fetch's progress bar: null while none runs; a running fetch's share
// done, null `value` while its pages are not known (the busy bar).
export function progressOf(progress: Progress | null | undefined):
        {value: number | null} | null {
    if (progress?.status !== "running") {
        return null;
    }
    const total = number(progress.total);
    return {value: total > 0
        ? Math.min(100, (number(progress.done) / total) * 100) : null};
}

// "HH:MM", local time, of unix seconds (the window's strftime("%H:%M")).
export function clock(seconds: unknown): string {
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
        return "";
    }
    const at = new Date(seconds * 1000);
    return `${String(at.getHours()).padStart(2, "0")}:`
        + `${String(at.getMinutes()).padStart(2, "0")}`;
}

type Translate = (key: string, values?: Record<string, unknown>) => string;

// What the window's status says: a fetch running ("Fetching sets..." and
// its pages), else what the page was told and the state does not say
// (`notice`: no tournament, choose a phase), else the rows read -- their
// count and when they were fetched -- or the call to fetch them. A failed
// fetch (its rows are the last ones that came) and what a fetch could not
// get (`failed`: pages and sets) are said too: the window said neither (S4v:
// v1 tells them).
// `list`: the read of GET /tournament/sets, null while none answered.
export function statusLine(progress: Progress | null | undefined,
                           list: {url: string; rows: number;
                                  fetchedAt: unknown} | null,
                           notice: string | null, t: Translate): string {
    if (progress?.status === "running") {
        const total = number(progress.total);
        return total > 0
            ? t("score_sets_fetching_pages",
                {done: number(progress.done), total})
            : t("score_sets_fetching");
    }
    if (notice) {
        return notice;
    }
    if (progress?.status === "error") {
        return t("score_sets_failed");
    }
    if (list && list.url) {
        const line = t("score_sets_count", {n: list.rows,
                                            at: clock(list.fetchedAt)});
        const failed = progress?.status === "done" ? number(progress.failed)
            : 0;
        return failed > 0
            ? `${line} ${t("score_sets_failed_pages", {n: failed})}` : line;
    }
    return t("score_sets_idle");
}
