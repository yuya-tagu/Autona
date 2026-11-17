// The archive page's title templates (ADR-006 A6, D5): the shapes GET
// /api/v1/archive/templates answers (core/ArchiveTemplates.py, vodsplit's
// template format), which one the worker takes for an event (its select,
// over the list in the server's order), and the editor's pure helpers.

import {SetsJson} from "./archiveSets";

export type Overflow = "drop_characters" | "short_round" | "truncate";
export type Order = "result" | "winner_first";

// A round's other names: {round_ja} and {round_short}.
export interface RoundNames {
    ja?: string;
    short?: string;
}

export interface Template {
    name: string;
    match: string | null;
    title: string;
    // The format's team title: vodsplit does not use it yet. Not edited here,
    // sent back as it was read.
    title_team: string;
    description: string;
    chapter_format: string;
    overflow: Overflow;
    round_map: Record<string, RoundNames>;
    aliases: Record<string, string>;
    order: Order;
}

// What the worker uses it for: "match" (events its match occurs in, when no
// earlier one matched), "shadowed" (an earlier template has the same match:
// never used), "default" (an event no match fits), "unused" (another
// template without a match comes before it).
export type TemplateRole = "match" | "shadowed" | "default" | "unused";

export interface TemplateEntry {
    id: string;
    // false: the standard template, not saved yet (no file).
    stored: boolean;
    role: TemplateRole;
    template: Template;
}

// POST /archive/jobs/<id>/titles:preview: a set's title and description as
// the template makes them (null for a set with no result) and how many
// chapter lines the description got (0: YouTube would ignore them).
export interface TitleRow {
    no: number;
    title: string | null;
    description: string | null;
    chapters: number;
}

export interface TitlePreview {
    template: string;
    sets: TitleRow[];
}

export const DEFAULT_ID = "default";
// The worker's (vodsplit templates.TITLE_MAX / DESC_MAX: YouTube's limits).
export const TITLE_LIMIT = 100;
export const DESCRIPTION_LIMIT = 5000;
// The server's checks (ArchiveTemplates.MAX_*).
export const MAX_NAME = 60;
export const MAX_MATCH = 100;
export const MAX_TITLE = 500;
export const MAX_DESCRIPTION = 10000;
export const MAX_CHAPTER_FORMAT = 200;
export const MAX_ROUND = 100;
export const MAX_ALIAS = 100;

export const OVERFLOWS: Overflow[] = ["drop_characters", "short_round",
                                      "truncate"];
export const ORDERS: Order[] = ["result", "winner_first"];

// vodsplit templates.variables: the ones a title can use, and the ones only
// a description makes sense with (links, chapters). {number} is left out:
// Autona's requests give none (it is always empty).
export const TITLE_VARIABLES = ["event", "round", "round_ja", "round_short",
                                "p1", "p2", "c1", "c2", "score", "winner",
                                "date", "p1_tag", "p2_tag"];
export const DESCRIPTION_VARIABLES = [...TITLE_VARIABLES, "chapters",
                                      "source_url", "source_url_t",
                                      "bracket_url"];

// The template vodsplit takes for `event` (templates.select): the first
// whose match occurs in the event name, else the first without a match.
// The server lists them in the order it gives them to the worker, so this
// is the one the worker uses; null only for an empty list.
export function selectTemplate(entries: TemplateEntry[],
                               event: string | null | undefined):
    TemplateEntry | null {
    const name = typeof event === "string" ? event : "";
    const matched = entries.find((entry) => entry.template.match
        && name !== "" && name.includes(entry.template.match));
    return matched ?? entries.find((entry) => !entry.template.match) ?? null;
}

// The length YouTube and the worker count (code points, not UTF-16 units).
export function textLength(text: string | null | undefined): number {
    return typeof text === "string" ? Array.from(text).length : 0;
}

// `text` with `insert` put over [start, end) -> the text and where the caret
// goes (after the insert).
export function insertAt(text: string, start: number | null | undefined,
                         end: number | null | undefined, insert: string):
    {text: string; caret: number} {
    const from = Math.min(Math.max(0, start ?? text.length), text.length);
    const to = Math.min(Math.max(from, end ?? from), text.length);
    return {text: text.slice(0, from) + insert + text.slice(to),
            caret: from + insert.length};
}

// Whether the two templates save the same file (key order aside).
export function sameTemplate(a: Template, b: Template): boolean {
    return JSON.stringify(normalized(a)) === JSON.stringify(normalized(b));
}

function sortedObject<T>(value: Record<string, T>): Record<string, T> {
    return Object.fromEntries(Object.keys(value ?? {}).sort()
        .map((key) => [key, value[key]]));
}

function normalized(template: Template) {
    const rounds: Record<string, RoundNames> = {};
    for (const [key, names] of Object.entries(template.round_map ?? {})) {
        const entry: RoundNames = {};
        if (names?.ja) {
            entry.ja = names.ja;
        }
        if (names?.short) {
            entry.short = names.short;
        }
        rounds[key] = entry;
    }
    return {...template, match: template.match || null,
            round_map: sortedObject(rounds),
            aliases: sortedObject(template.aliases ?? {})};
}

// The template as the server takes it: a round with neither name is left
// out (the server drops it too), an empty match is none.
export function forSaving(template: Template): Template {
    const rounds: Record<string, RoundNames> = {};
    for (const [key, names] of Object.entries(template.round_map ?? {})) {
        const entry: RoundNames = {};
        if (names?.ja) {
            entry.ja = names.ja;
        }
        if (names?.short) {
            entry.short = names.short;
        }
        if (entry.ja || entry.short) {
            rounds[key] = entry;
        }
    }
    const match = (template.match ?? "").trim();
    return {...template, match: match === "" ? null : match,
            round_map: rounds};
}

// The rounds the editor offers names for: the template's own, then the
// job's (its sets' and unused results') that it does not name yet, in the
// order they come.
export function roundKeys(template: Template, sets: SetsJson | null):
    string[] {
    const keys = Object.keys(template.round_map ?? {});
    const seen = new Set(keys);
    const results = [...(sets?.sets ?? []).map((row) => row.set),
                     ...(sets?.unused_sets ?? [])];
    for (const result of results) {
        const round = typeof result?.round === "string"
            ? result.round.trim() : "";
        if (round !== "" && !seen.has(round)) {
            seen.add(round);
            keys.push(round);
        }
    }
    return keys;
}

// The round's names set to `names` (a round left with none is removed).
export function withRound(template: Template, round: string,
                          names: RoundNames): Template {
    const next = {...(template.round_map ?? {})};
    if (names.ja || names.short) {
        next[round] = names;
    } else {
        delete next[round];
    }
    return {...template, round_map: next};
}

// The aliases as rows, in their order (an alias being typed keeps its place
// while its key changes).
export type AliasRow = [string, string];

export function aliasRows(template: Template): AliasRow[] {
    return Object.entries(template.aliases ?? {});
}

// The aliases from rows: a row with either side empty is left out (the
// server takes neither; a row being typed is not saved until both are
// there); a later row with the same name wins (an object has one).
export function withAliases(template: Template, rows: AliasRow[]): Template {
    const aliases: Record<string, string> = {};
    for (const [from, to] of rows) {
        if (from.trim() !== "" && to.trim() !== "") {
            aliases[from] = to;
        }
    }
    return {...template, aliases};
}
