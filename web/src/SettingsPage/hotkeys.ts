// The hotkey card's composition (ADR-005 S4af): a shortcut is Ctrl / Alt /
// Shift and one key of the vocabulary GET /api/v1/settings sends, spelled
// as core's HotkeyRules.Canonical saves it ("Ctrl+Alt+Shift+Key"). The
// server is the judge (an unreadable shortcut is 400, a duplicate 409);
// this only splits a saved value into the controls and names the actions
// a 409 was about.

export interface Composition {
    ctrl: boolean;
    alt: boolean;
    shift: boolean;
    // A key of the vocabulary, or "" for none (no shortcut).
    key: string;
}

export const NO_SHORTCUT: Composition = {ctrl: false, alt: false, shift: false, key: ""};

// What Qt's key recorder (the Qt settings window's until S4af) wrote for
// keys the vocabulary names otherwise: core compares them as those keys
// (HotkeyRules._QT_SPELLINGS, and "Return" for "Enter").
const OTHER_SPELLINGS: Record<string, string> = {
    esc: "escape", del: "delete", ins: "insert", pgup: "pageup",
    pgdown: "pagedown", return: "enter",
};

// `value` split into the controls: the modifiers it has, and its key when
// the vocabulary has it ("" when it has none, or when the value is not one
// of Ctrl / Alt / Shift and one key: the card then starts from nothing).
export function splitShortcut(value: string | null, keys: string[]): Composition {
    if (!value) {
        return NO_SHORTCUT;
    }
    const spelling = new Map(keys.map((key) => [key.toLowerCase(), key]));
    const result = {...NO_SHORTCUT};
    for (const raw of value.split("+")) {
        const token = raw.trim().toLowerCase();
        if (token === "ctrl" || token === "alt" || token === "shift") {
            if (result[token]) {
                return NO_SHORTCUT;
            }
            result[token] = true;
            continue;
        }
        const key = spelling.get(OTHER_SPELLINGS[token] ?? token);
        if (key === undefined || result.key) {
            return NO_SHORTCUT;
        }
        result.key = key;
    }
    return result.key ? result : NO_SHORTCUT;
}

// The value a composition saves: "" with no key (no shortcut).
export function joinShortcut(composition: Composition): string {
    if (!composition.key) {
        return "";
    }
    return [
        composition.ctrl ? "Ctrl" : null,
        composition.alt ? "Alt" : null,
        composition.shift ? "Shift" : null,
        composition.key,
    ].filter(Boolean).join("+");
}

// What a saved value names, for comparing two: the modifiers and the key
// however spelled; the value itself (lower case) when it is not one.
function keyOf(value: string, keys: string[]): string {
    const composition = splitShortcut(value, keys);
    return composition.key
        ? joinShortcut(composition).toLowerCase()
        : value.trim().toLowerCase();
}

// The other actions that have `value` (the names a 409 CONFLICT was about:
// its message is the server's, in English).
export function sharingActions(value: string, action: string,
                               current: Record<string, string | null>,
                               keys: string[]): string[] {
    if (!value) {
        return [];
    }
    const mine = keyOf(value, keys);
    return Object.entries(current)
        .filter(([other, saved]) =>
            other !== action && saved && keyOf(saved, keys) === mine)
        .map(([other]) => other);
}
