// Board 1's statistics as the Statistics page shows them (ADR-005 S4k):
// score.1.{upset_factor,recent_sets,last_sets,history_sets} and the
// players' names, as published (Socket.IO), in the Qt panel's words
// (the pre-S4k AutonaStatsWidget._updateDisplay). Core's StatsService
// writes them; nothing here writes. Where the Qt panel printed Python's
// "None" for a field that is null, the page shows nothing.

type Fields = Record<string, unknown>;

interface Player {
    name?: unknown;
}

export interface StatsBoard {
    team?: Record<string, {player?: Record<string, Player>} | undefined>;
    upset_factor?: unknown;
    recent_sets?: {sets?: unknown} | null;
    last_sets?: Record<string, unknown> | null;
    history_sets?: Record<string, unknown> | null;
}

function isFields(value: unknown): value is Fields {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

// A field as text: `missing` when the key is absent, nothing when null.
export function text(value: unknown, missing = ""): string {
    if (value === undefined) {
        return missing;
    }
    if (value === null) {
        return "";
    }
    return String(value);
}

function playerName(board: StatsBoard | undefined, team: string): string {
    const name = board?.team?.[team]?.player?.["1"]?.name;
    return typeof name === "string" ? name : "";
}

export interface Names {
    // Both players named: the upset factor is shown.
    both: boolean;
    // The headings' names: trimmed, "P1" / "P2" when blank.
    p1: string;
    p2: string;
    // The head-to-head record's: as typed, "P1" / "P2" when empty.
    p1Record: string;
    p2Record: string;
}

export function names(board: StatsBoard | undefined): Names {
    const raw1 = playerName(board, "1");
    const raw2 = playerName(board, "2");
    return {
        both: Boolean(raw1.trim() && raw2.trim()),
        p1: raw1.trim() || "P1",
        p2: raw2.trim() || "P2",
        p1Record: raw1 || "P1",
        p2Record: raw2 || "P2",
    };
}

export function upsetFactor(board: StatsBoard | undefined): string {
    return text(board?.upset_factor, "0");
}

// ── head-to-head ─────────────────────────────────────────────────────────

export function recentSets(board: StatsBoard | undefined): Fields[] {
    const sets = board?.recent_sets?.sets;
    const list = Array.isArray(sets) ? sets
        : isFields(sets) ? Object.values(sets) : [];
    return list.filter(isFields);
}

// Python's "%.0f": the nearest whole number, a half to the even one.
export function percent(part: number, total: number): string {
    const value = part / total * 100;
    const floor = Math.floor(value);
    const rounded = value - floor === 0.5
        ? (floor % 2 === 0 ? floor : floor + 1)
        : Math.round(value);
    return `${rounded}%`;
}

export interface H2HRecord {
    p1Wins: number;
    p2Wins: number;
    total: number;
    p1Rate: string;
    p2Rate: string;
}

// winner 0 is player 1's set, 1 player 2's; a set with neither is not
// counted.
export function h2hRecord(sets: Fields[]): H2HRecord {
    const p1Wins = sets.filter((set) => set.winner === 0).length;
    const p2Wins = sets.filter((set) => set.winner === 1).length;
    const total = p1Wins + p2Wins;
    return {
        p1Wins,
        p2Wins,
        total,
        p1Rate: total > 0 ? percent(p1Wins, total) : "-",
        p2Rate: total > 0 ? percent(p2Wins, total) : "-",
    };
}

function pad(value: number): string {
    return String(value).padStart(2, "0");
}

// A set's start, in seconds, as this computer's local date.
export function localDate(timestamp: unknown): string {
    if (typeof timestamp !== "number" || !timestamp) {
        return "";
    }
    const date = new Date(timestamp * 1000);
    if (Number.isNaN(date.getTime())) {
        return "";
    }
    return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())}`;
}

// Date | Event | Round | Score | Winner. The event is the tournament with
// the event's name on a second line.
export function recentSetCells(set: Fields, names: Names): string[] {
    const tournament = set.tournament ? String(set.tournament) : "";
    const score = set.score === undefined ? [0, 0] : set.score;
    return [
        localDate(set.timestamp),
        set.event ? `${tournament}\n${String(set.event)}` : tournament,
        set.round ? String(set.round) : text(set.phase_name),
        Array.isArray(score) ? `${text(score[0])} - ${text(score[1])}` : "",
        set.winner === 0 ? names.p1Record : names.p2Record,
    ];
}

// ── last sets and placements (per player) ────────────────────────────────

// A player's entries ("1", "2", ...) in their number's order.
export function numbered(value: unknown): Fields[] {
    if (Array.isArray(value)) {
        return value.filter(isFields);
    }
    if (!isFields(value)) {
        return [];
    }
    const order = (key: string) => (/^\d+$/.test(key) ? Number(key) : 0);
    return Object.keys(value)
        .map((key, index) => ({key, index}))
        .sort((a, b) => order(a.key) - order(b.key) || a.index - b.index)
        .map(({key}) => value[key])
        .filter(isFields);
}

export function lastSets(board: StatsBoard | undefined, team: string): Fields[] {
    return numbered(board?.last_sets?.[team]);
}

export function historySets(board: StatsBoard | undefined,
                            team: string): Fields[] {
    return numbered(board?.history_sets?.[team]);
}

// Phase | Round | Opponent | Score. The opponent's sponsor on a first line.
export function lastSetCells(set: Fields): string[] {
    const opponent = text(set.oponent_name);
    return [
        text(set.phase_name),
        text(set.round_name),
        set.oponent_team ? `${String(set.oponent_team)}\n${opponent}`
            : opponent,
        `${text(set.player_score, "0")} - ${text(set.oponent_score, "0")}`,
    ];
}

// Tournament | Event | Placement | Entrants | Date (the month is the
// state's English name: core's contract with the overlays).
export function historyCells(set: Fields): string[] {
    return [
        text(set.tournament_name),
        text(set.event_name),
        text(set.placement),
        text(set.entrants),
        `${text(set.event_date_year)}/${text(set.event_date_month)}/${text(set.event_date_day)}`,
    ];
}
