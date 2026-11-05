// The Player List's rows (ADR-005 S4n): how the tables show, filter, sort and
// page GET /api/v1/players and /players/entrants, and what an edit sends to
// PUT /api/v1/players. No React here.

// [en_name, skin row, variant's en_name], as a row gives it: not checked
// against the catalog, so another game's character, a skin out of range and
// an unknown variant are all kept as they are.
export type Main = [string, number, string];

// A row of GET /api/v1/players (or /players/entrants, with `seed`, and
// `key` null for an entrant the DB does not have).
export interface PlayerRow {
    key: string | null;
    prefix: string;
    gamerTag: string;
    name: string;
    twitter: string;
    country_code: string;
    state_code: string;
    custom_textbox: string;
    mains: Main[];
    seed?: number | null;
}

// What the editor holds: a row's fields, none of them `key` or `seed`.
export type Draft = Omit<PlayerRow, "key" | "seed">;

// A row of the editor's characters: one field, for as long as the editor is
// open. The id keeps the field with its row whatever happens to the rows around
// it, and an emptied field stays where it is: what is typed next goes in front
// of the operator's eyes, into that field.
export interface CharacterRow {
    id: number;
    // What saving sends for this row. Null: nothing.
    main: Main | null;
    // What it had when it was emptied: the same character again is that one,
    // colour and variant included.
    emptied: Main | null;
    // The field's text while it is not the name of `main`. Null: that name, or
    // nothing without a main.
    typed: string | null;
    // `typed` was left in the field and names no character. What the field
    // shows is not what would be saved, so nothing is until it is corrected.
    unknown: boolean;
}

// PUT /api/v1/players's body.
export interface SaveBody {
    prefix: string;
    gamerTag: string;
    name: string;
    twitter: string;
    custom_textbox: string;
    country_code?: string;
    state_code?: string;
    mains?: Main[];
    game?: string | null;
}

// A character of GET /api/v1/characters, as far as the tables read it.
export interface CharacterInfo {
    en_name?: unknown;
    display_name?: unknown;
    skins?: unknown;
}

export type Column = "prefix" | "gamerTag" | "country" | "characters"
    | "twitter";

export interface Sort {
    column: Column;
    descending: boolean;
}

// A row as a table shows it.
export interface ShownRow {
    // The React key and the selection's: a row's DB key, or an entrant's tag
    // (two entrants may share one).
    id: string;
    row: PlayerRow;
    // "-" for an entrant without a seed.
    seed: string;
    // The characters' names, joined.
    characters: string;
    // The first character's icon (the asset path as the catalog gives it).
    icon: string | null;
    // What the search looks in, lower case: every column and the real name,
    // which no table shows.
    search: string;
}

export const PAGE_SIZE = 100;

// The seed GetEntrantsWorker gives an entrant without one (the server says
// null; the page does not count on it).
export const NO_SEED = 999999;

// How many characters the editor offers to add (the board's default).
export const MAX_NEW_CHARACTERS = 3;

export const DEFAULT_SORT: Sort = {column: "gamerTag", descending: false};

export const EMPTY_ROW: PlayerRow = {
    key: null,
    prefix: "",
    gamerTag: "",
    name: "",
    twitter: "",
    country_code: "",
    state_code: "",
    custom_textbox: "",
    mains: [],
};

// The DB's key of a sponsor and a tag (core/PlayerDB.PlayerTag), as the
// server trims them.
export function recordKey(prefix: string, gamerTag: string): string {
    const tag = gamerTag.trim();
    const sponsor = prefix.trim();
    return sponsor ? `${sponsor} ${tag}` : tag;
}

export function seedText(seed: number | null | undefined): string {
    return typeof seed === "number" && seed > 0 && seed < NO_SEED
        ? String(seed) : "-";
}

function byEnName(characters: Record<string, CharacterInfo>) {
    const found = new Map<string, CharacterInfo>();
    Object.values(characters).forEach((entry) => {
        if (typeof entry?.en_name === "string") {
            found.set(entry.en_name, entry);
        }
    });
    return found;
}

// The icon of a character's skin row, the first skin's when the row is out
// of range (a row is not checked against the catalog).
function skinIcon(entry: CharacterInfo | undefined,
                  row: number): string | null {
    const skins = entry?.skins as Record<string, {assets?: unknown}> | undefined;
    const skin = skins?.[row] ?? skins?.[0];
    const assets = skin?.assets as Record<string, {asset?: unknown}> | undefined;
    const asset = assets?.["base_files/icon"]?.asset;
    return typeof asset === "string" && asset ? asset : null;
}

// The rows of a listing as a table shows them, against the character list
// of GET /api/v1/characters: a character it does not have shows its en_name
// and no icon.
export function shownRows(rows: PlayerRow[],
                          characters: Record<string, CharacterInfo>): ShownRow[] {
    const known = byEnName(characters);
    const seen = new Map<string, number>();
    return rows.map((row) => {
        const names = row.mains.map(([enName]) => {
            const display = known.get(enName)?.display_name;
            return typeof display === "string" && display ? display : enName;
        });
        const first = row.mains[0];
        const base = row.key ?? row.gamerTag;
        const repeats = seen.get(base) ?? 0;
        seen.set(base, repeats + 1);
        const seed = seedText(row.seed);
        return {
            id: repeats ? `${base}#${repeats}` : base,
            row,
            seed,
            characters: names.join(", "),
            icon: first ? skinIcon(known.get(first[0]), first[1]) : null,
            search: [row.prefix, row.gamerTag, row.name, row.country_code,
                     row.twitter, seed === "-" ? "" : seed, ...names,
                     ...row.mains.map(([enName]) => enName)]
                .join("\n").toLocaleLowerCase(),
        };
    });
}

export function filterRows(rows: ShownRow[], filter: string): ShownRow[] {
    const needle = filter.trim().toLocaleLowerCase();
    return needle ? rows.filter((item) => item.search.includes(needle)) : rows;
}

const collator = new Intl.Collator(undefined, {sensitivity: "base"});

function columnText(item: ShownRow, column: Column): string {
    switch (column) {
        case "prefix":
            return item.row.prefix;
        case "gamerTag":
            return item.row.gamerTag;
        case "country":
            return item.row.country_code;
        case "characters":
            return item.characters;
        default:
            return item.row.twitter;
    }
}

// A new array. Rows that tie stay in their DB key's order whichever way the
// column goes.
export function sortRows(rows: ShownRow[], sort: Sort): ShownRow[] {
    const sign = sort.descending ? -1 : 1;
    return [...rows].sort((a, b) =>
        sign * collator.compare(columnText(a, sort.column),
                                columnText(b, sort.column))
        || collator.compare(a.id, b.id));
}

export function lastPage(count: number): number {
    return Math.max(0, Math.ceil(count / PAGE_SIZE) - 1);
}

export function pageOf<T>(rows: T[], page: number): T[] {
    return rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
}

export function draftOf(row: PlayerRow): Draft {
    return {
        prefix: row.prefix,
        gamerTag: row.gamerTag,
        name: row.name,
        twitter: row.twitter,
        custom_textbox: row.custom_textbox,
        country_code: row.country_code,
        state_code: row.state_code,
        mains: row.mains,
    };
}

// `rows`, with blank ones after them up to MAX_NEW_CHARACTERS.
export function withBlankRows(rows: CharacterRow[]): CharacterRow[] {
    const next = Math.max(-1, ...rows.map((row) => row.id)) + 1;
    const blanks = Array.from(
        {length: Math.max(0, MAX_NEW_CHARACTERS - rows.length)},
        (_blank, index) => ({id: next + index, main: null, emptied: null,
                             typed: null, unknown: false}));
    return [...rows, ...blanks];
}

export function characterRows(mains: Main[]): CharacterRow[] {
    return withBlankRows(mains.map((main, id) => ({
        id, main, emptied: null, typed: null, unknown: false})));
}

// The rows' mains, in their order: a blank row is no main.
export function mainsOf(rows: CharacterRow[]): Main[] {
    return rows.flatMap((row) => (row.main ? [row.main] : []));
}

function sameMain(a: Main, b: Main): boolean {
    return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

export function sameMains(a: Main[], b: Main[]): boolean {
    return a.length === b.length
        && a.every((main, index) => sameMain(main, b[index]));
}

// What saving `draft` sends, for the row it was opened on and the game that
// row was read with (external-control-api §6.26). What was not edited stays
// out, so the save does not touch it: a row's "" country or region does not
// mean the DB's is empty (a place name that does not resolve reads as ""),
// and the mains go only with the game they are of. A save under another
// sponsor or tag is another record, which takes nothing over: that one says
// everything.
export function saveBody(draft: Draft, original: PlayerRow,
                         game: string | null): SaveBody {
    const body: SaveBody = {
        prefix: draft.prefix,
        gamerTag: draft.gamerTag,
        name: draft.name,
        twitter: draft.twitter,
        custom_textbox: draft.custom_textbox,
    };
    const sameRecord = original.key !== null
        && original.key === recordKey(draft.prefix, draft.gamerTag);
    if (!sameRecord || draft.country_code !== original.country_code) {
        body.country_code = draft.country_code;
    }
    if (!sameRecord || draft.state_code !== original.state_code) {
        body.state_code = draft.state_code;
    }
    if (!sameRecord || !sameMains(draft.mains, original.mains)) {
        body.mains = draft.mains;
        body.game = game;
    }
    return body;
}

// The entry of the list `characters` (keyed by en_name) a main names. The
// name comes from the DB, so it can be any text: "constructor" is not a
// character.
export function characterOf<T>(characters: Record<string, T>,
                               enName: string): T | undefined {
    return Object.prototype.hasOwnProperty.call(characters, enName)
        ? characters[enName] : undefined;
}

// Whether a character of the list takes a skin row: one the list has, or any
// when it has none (the server saves -1).
function takesSkin(entry: {skins?: unknown}, row: number): boolean {
    const skins = Object.keys((entry.skins ?? {}) as object).length;
    return skins === 0 || (row >= 0 && row < skins);
}

// Whether the editor can show a main in the character picker: one of the
// first MAX_NEW_CHARACTERS, of the list `characters` (keyed by en_name), on
// a skin that list has. Any other is kept as it is, shown by name, and can
// only be removed: the picker could not show it, and picking would lose it.
export function pickable(main: Main, index: number,
                         characters: Record<string, {skins?: unknown}>): boolean {
    if (index >= MAX_NEW_CHARACTERS) {
        return false;
    }
    const entry = characterOf(characters, main[0]);
    return entry !== undefined && takesSkin(entry, main[1]);
}

// The mains a save would be refused for, by the server's rule
// (core/PlayerDirectory._MainsRecord), so that the editor can say which: the
// answer only says that one was. One the record saved to has already
// (`stored`) goes as it is. Any other must be on the game's list (`characters`,
// keyed by en_name), on a skin it has, with a variant it has. A sponsor or a
// tag that names no record yet stores nothing, so a row's mains the editor
// only kept, out of the list or not, are looked at then.
//
// A list that is not read leaves it to the server: `characters` empty or
// `variants` null.
export function refusedMains(mains: Main[], stored: Main[],
                             characters: Record<string, {skins?: unknown}>,
                             variants: ReadonlySet<string> | null): Main[] {
    if (Object.keys(characters).length === 0) {
        return [];
    }
    return mains.filter((main) => {
        if (stored.some((held) => sameMain(held, main))) {
            return false;
        }
        const entry = characterOf(characters, main[0]);
        return entry === undefined || !takesSkin(entry, main[1])
            || (main[2] !== "" && variants !== null && !variants.has(main[2]));
    });
}
