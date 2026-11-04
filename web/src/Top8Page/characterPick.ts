// Which character and skin row a Top 8 player's characters["1"] shows on
// the Top 8 page (ADR-005 S4i): core's Top8Service.CharacterPick, against
// the character list the page reads (GET /api/v1/characters). The two are
// held to one table (characterPick.cases.json, read by this module's jest
// test and by tests/test_top8_service.py), so the page shows the pick core
// would make for every shape a slot holds -- the page's own pick, an
// import's, and the shapes the Qt panel wrote before S4h.

// A skin of GET /api/v1/characters: its data, `assets` among it.
export interface CharacterSkin {
    assets?: unknown;
    name?: unknown;
    en_name?: unknown;
    [key: string]: unknown;
}

// A character of GET /api/v1/characters: the character combo's row, with
// its skins.
export interface CharacterEntry {
    en_name?: unknown;
    codename?: unknown;
    skins?: CharacterSkin[];
    [key: string]: unknown;
}

// GET /api/v1/characters: by display name, in the combo's order.
export type Characters = Record<string, CharacterEntry>;

// [en_name, skin row]; [null, -1] for none.
export type CharacterPick = [string | null, number];

export const NO_PICK: CharacterPick = [null, -1];

type Plain = Record<string, unknown>;

function isPlain(value: unknown): value is Plain {
    return typeof value === "object" && value !== null
        && !Array.isArray(value);
}

function isRow(value: unknown): value is number {
    return typeof value === "number" && Number.isInteger(value);
}

// Python's == on JSON values: objects whatever their key order.
export function sameValue(a: unknown, b: unknown): boolean {
    if (a === b) {
        return true;
    }
    if (Array.isArray(a) || Array.isArray(b)) {
        return Array.isArray(a) && Array.isArray(b) && a.length === b.length
            && a.every((item, index) => sameValue(item, b[index]));
    }
    if (!isPlain(a) || !isPlain(b)) {
        return false;
    }
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length
        && keys.every((key) => Object.prototype.hasOwnProperty.call(b, key)
            && sameValue(a[key], b[key]));
}

function skinsOf(entry: CharacterEntry): CharacterSkin[] {
    return Array.isArray(entry.skins) ? entry.skins : [];
}

// `skin` when it is one of the character's rows, else its first (-1: a
// character without skins). Top8Service._SkinRow.
export function skinRow(entry: CharacterEntry, skin: unknown): number {
    const rows = skinsOf(entry).length;
    if (isRow(skin) && skin >= 0 && skin < rows) {
        return skin;
    }
    return rows ? 0 : -1;
}

// What the page's pick of `entry` on `skin` writes: core's
// Top8Service.CharacterValue (the player cards' slot value). Compared with
// the state's value, never sent (the page sends the identifiers).
export function characterValue(entry: CharacterEntry, skin: unknown): Plain {
    const {skins: _skins, ...row} = entry;
    const value: Plain = {...row};
    const row_ = skinRow(entry, skin);
    const data = row_ >= 0 ? skinsOf(entry)[row_] : undefined;
    if (data && Object.keys(data).length) {
        if (data.name) {
            value.name = data.name;
        }
        if (data.en_name) {
            value.en_name = data.en_name;
        }
        value.assets = data.assets ?? {};
    }
    if (value.assets === undefined || value.assets === null) {
        value.assets = {};
    }
    value.skin = row_;
    value.variant = {};
    return value;
}

// The identifiers that show `value` (Top8Service.CharacterPick): the rows
// sharing its codename first, then those sharing its en_name; the one
// whose value with the stored skin is `value`, else the skin whose assets
// are `value`'s (among the codename's character's, or every character's
// when there is no codename), else the first candidate on its skin row.
export function characterPick(value: unknown,
                              characters: Characters): CharacterPick {
    if (!isPlain(value) || Object.keys(value).length === 0) {
        return NO_PICK;
    }
    const rows = Object.values(characters);
    const codename = value.codename;
    const enName = value.en_name;
    const byCodename = rows.filter(
        (row) => Boolean(codename) && row.codename === codename);
    const byEnName = rows.filter(
        (row) => Boolean(enName) && row.en_name === enName
            && !byCodename.includes(row));
    const candidates = [...byCodename, ...byEnName];
    const name = (row: CharacterEntry) =>
        (typeof row.en_name === "string" ? row.en_name : null);

    const skin = value.skin;
    if (isRow(skin)) {
        for (const row of candidates) {
            if (sameValue(characterValue(row, skin), value)) {
                return [name(row), skin];
            }
        }
    }

    const assets = value.assets;
    if (isPlain(assets) && Object.keys(assets).length) {
        const searched = byCodename.length ? byCodename : rows;
        for (const row of searched) {
            const skins = skinsOf(row);
            for (let index = 0; index < skins.length; index++) {
                if (sameValue(skins[index]?.assets, assets)) {
                    return [name(row), index];
                }
            }
        }
    }

    if (candidates.length) {
        return [name(candidates[0]), skinRow(candidates[0], skin)];
    }
    return NO_PICK;
}
