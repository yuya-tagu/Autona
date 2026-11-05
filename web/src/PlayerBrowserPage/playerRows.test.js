import {
    EMPTY_ROW,
    NO_SEED,
    PAGE_SIZE,
    characterOf,
    filterRows,
    lastPage,
    pageOf,
    pickable,
    recordKey,
    refusedMains,
    saveBody,
    seedText,
    shownRows,
    sortRows,
} from "./playerRows";

// The Player List's rows (ADR-005 S4n): what the tables show, how they
// filter, sort and page, and what an edit sends (external-control-api §6.26).

const ALPHA = {
    key: "SP Alpha", prefix: "SP", gamerTag: "Alpha", name: "Realname One",
    twitter: "@alpha", country_code: "JP", state_code: "13",
    custom_textbox: "note", mains: [["Mario", 1, "EX"], ["Gone", 5, ""]],
};
const BRAVO = {...EMPTY_ROW, key: "bravo", gamerTag: "bravo"};
const CHARLIE = {...EMPTY_ROW, key: "Z Charlie", prefix: "Z",
                 gamerTag: "Charlie", country_code: "US"};

const CHARACTERS = {
    "Mario / マリオ": {
        en_name: "Mario", display_name: "マリオ",
        skins: [{assets: {"base_files/icon": {asset: "./m/0.png"}}},
                {assets: {"base_files/icon": {asset: "./m/1.png"}}}],
    },
};

function draftFrom(row, changes = {}) {
    const {key, seed, ...fields} = row;
    return {...fields, ...changes};
}

test("the key is the sponsor and the tag, trimmed as the server does", () => {
    expect(recordKey("SP", "Alpha")).toBe("SP Alpha");
    expect(recordKey(" SP ", " Alpha ")).toBe("SP Alpha");
    expect(recordKey("", "Alpha")).toBe("Alpha");
    expect(recordKey("  ", " Alpha")).toBe("Alpha");
});

test.each([
    [1, "1"], [128, "128"], [null, "-"], [undefined, "-"], [0, "-"],
    [NO_SEED, "-"], [NO_SEED + 1, "-"],
])("seed %p shows as %p", (seed, text) => {
    expect(seedText(seed)).toBe(text);
});

describe("shownRows", () => {
    test("a character shows its name, and an unknown one its en_name", () => {
        const [row] = shownRows([ALPHA], CHARACTERS);
        expect(row.characters).toBe("マリオ, Gone");
    });

    test("the icon is the first character's skin, the first when out of range", () => {
        expect(shownRows([ALPHA], CHARACTERS)[0].icon).toBe("./m/1.png");
        const outOfRange = {...ALPHA, mains: [["Mario", 9, ""]]};
        expect(shownRows([outOfRange], CHARACTERS)[0].icon).toBe("./m/0.png");
        expect(shownRows([BRAVO], CHARACTERS)[0].icon).toBeNull();
        const unknown = {...ALPHA, mains: [["Gone", 0, ""]]};
        expect(shownRows([unknown], CHARACTERS)[0].icon).toBeNull();
    });

    test("rows without a key get one id each, by their tag", () => {
        const twin = {...EMPTY_ROW, gamerTag: "Twin"};
        expect(shownRows([ALPHA, twin, twin], {}).map((row) => row.id))
            .toEqual(["SP Alpha", "Twin", "Twin#1"]);
    });
});

describe("filterRows", () => {
    const rows = shownRows([ALPHA, BRAVO, CHARLIE], CHARACTERS);
    const found = (text) => filterRows(rows, text).map((row) => row.id);

    test.each([
        ["the tag, whatever its case", "ALPH", ["SP Alpha"]],
        ["the sponsor", "z", ["Z Charlie"]],
        ["the real name, which no table shows", "realname", ["SP Alpha"]],
        ["the country", "us", ["Z Charlie"]],
        ["the twitter", "@al", ["SP Alpha"]],
        ["a character's name", "マリオ", ["SP Alpha"]],
        ["a character's en_name", "mario", ["SP Alpha"]],
        ["nothing", "nobody", []],
    ])("looks in %s", (_what, text, ids) => {
        expect(found(text)).toEqual(ids);
    });

    test("an empty search keeps every row", () => {
        expect(found("  ")).toEqual(["SP Alpha", "bravo", "Z Charlie"]);
    });
});

describe("sortRows", () => {
    const rows = shownRows([CHARLIE, ALPHA, BRAVO], CHARACTERS);
    const sorted = (column, descending = false) =>
        sortRows(rows, {column, descending}).map((row) => row.row.gamerTag);

    test("by a column, whatever the case", () => {
        expect(sorted("gamerTag")).toEqual(["Alpha", "bravo", "Charlie"]);
        expect(sorted("gamerTag", true)).toEqual(["Charlie", "bravo", "Alpha"]);
    });

    test("rows that tie stay in key order whichever way it goes", () => {
        expect(sorted("twitter")).toEqual(["bravo", "Charlie", "Alpha"]);
        expect(sorted("twitter", true)).toEqual(["Alpha", "bravo", "Charlie"]);
    });

    test("does not change the rows given", () => {
        sorted("gamerTag");
        expect(rows.map((row) => row.row.gamerTag))
            .toEqual(["Charlie", "Alpha", "bravo"]);
    });
});

test("the pages hold PAGE_SIZE rows", () => {
    const rows = Array.from({length: PAGE_SIZE * 2 + 5}, (_v, index) => index);
    expect(pageOf(rows, 0)).toHaveLength(PAGE_SIZE);
    expect(pageOf(rows, 1)[0]).toBe(PAGE_SIZE);
    expect(pageOf(rows, 2)).toHaveLength(5);
    expect(lastPage(rows.length)).toBe(2);
    expect(lastPage(PAGE_SIZE)).toBe(0);
    expect(lastPage(0)).toBe(0);
});

describe("saveBody", () => {
    const TEXTS = {prefix: "SP", gamerTag: "Alpha", name: "Realname One",
                   twitter: "@alpha", custom_textbox: "note"};

    test("an unedited save sends the text fields and nothing else", () => {
        expect(saveBody(draftFrom(ALPHA), ALPHA, "tgame")).toEqual(TEXTS);
    });

    test("the mains go only when changed, with the game they are of", () => {
        const mains = [["Mario", 0, "EX"]];
        expect(saveBody(draftFrom(ALPHA, {mains}), ALPHA, "tgame"))
            .toEqual({...TEXTS, mains, game: "tgame"});
    });

    test("the same mains again are not a change", () => {
        const mains = ALPHA.mains.map((main) => [...main]);
        expect(saveBody(draftFrom(ALPHA, {mains}), ALPHA, "tgame"))
            .toEqual(TEXTS);
    });

    test("a changed country goes alone, and a cleared one as empty", () => {
        expect(saveBody(draftFrom(ALPHA, {country_code: "US"}), ALPHA, "tgame"))
            .toEqual({...TEXTS, country_code: "US"});
        expect(saveBody(draftFrom(ALPHA, {country_code: "", state_code: ""}),
                        ALPHA, "tgame"))
            .toEqual({...TEXTS, country_code: "", state_code: ""});
    });

    test("a changed region goes alone", () => {
        expect(saveBody(draftFrom(ALPHA, {state_code: "27"}), ALPHA, "tgame"))
            .toEqual({...TEXTS, state_code: "27"});
    });

    test.each([
        ["tag", {gamerTag: "Alpha2"}],
        ["sponsor", {prefix: "NEW"}],
    ])("another %s is another record, and says everything", (_what, change) => {
        expect(saveBody(draftFrom(ALPHA, change), ALPHA, "tgame")).toEqual({
            ...TEXTS, ...change, country_code: "JP", state_code: "13",
            mains: ALPHA.mains, game: "tgame",
        });
    });

    test("spaces around the tag are not another record", () => {
        expect(saveBody(draftFrom(ALPHA, {gamerTag: " Alpha "}), ALPHA, "tgame"))
            .toEqual({...TEXTS, gamerTag: " Alpha "});
    });

    test("a row the DB does not have says everything", () => {
        const entrant = {...EMPTY_ROW, gamerTag: "Delta", seed: 3};
        expect(saveBody(draftFrom(entrant), entrant, null)).toEqual({
            prefix: "", gamerTag: "Delta", name: "", twitter: "",
            custom_textbox: "", country_code: "", state_code: "", mains: [],
            game: null,
        });
    });
});

describe("pickable", () => {
    const LIST = {Mario: {skins: [{}, {}]}, Blob: {skins: []}};

    test.each([
        ["one of the list on a skin it has", ["Mario", 1, ""], 0, true],
        ["the last skin", ["Mario", 1, ""], 2, true],
        ["a skin out of range", ["Mario", 2, ""], 0, false],
        ["no skin on a character with skins", ["Mario", -1, ""], 0, false],
        ["any skin on a character without", ["Blob", -1, ""], 0, true],
        ["another game's character", ["Gone", 0, ""], 0, false],
        ["a fourth character", ["Mario", 0, ""], 3, false],
        ["a name every object answers to", ["constructor", 0, ""], 0, false],
    ])("%s", (_what, main, index, expected) => {
        expect(pickable(main, index, LIST)).toBe(expected);
    });
});

test("characterOf finds only what the list has", () => {
    const list = {Mario: {skins: []}};
    expect(characterOf(list, "Mario")).toBe(list.Mario);
    expect(characterOf(list, "toString")).toBeUndefined();
});

// core/PlayerDirectory._MainsRecord, the rule tests/test_api_v1_players.py
// pins on the server's side.
describe("refusedMains", () => {
    const LIST = {
        Mario: {skins: [{}, {}]},
        Blob: {skins: []},
    };
    const VARIANTS = new Set(["EX"]);

    test.each([
        ["one of the list on a skin it has", ["Mario", 1, ""], false],
        ["with a variant of the game", ["Mario", 1, "EX"], false],
        ["any skin on a character without", ["Blob", 7, ""], false],
        ["a character off the list (a mod with mods off)", ["Gone", 0, ""], true],
        ["a skin out of range", ["Mario", 2, ""], true],
        ["no skin on a character with skins", ["Mario", -1, ""], true],
        ["a variant the game does not have", ["Mario", 0, "Nope"], true],
        ["a name every object answers to", ["constructor", 0, ""], true],
    ])("%s", (_what, main, isRefused) => {
        expect(refusedMains([main], [], LIST, VARIANTS))
            .toEqual(isRefused ? [main] : []);
    });

    test("what the record already has goes as it is", () => {
        const held = ["Gone", 5, "Nope"];
        expect(refusedMains([held], [held], LIST, VARIANTS)).toEqual([]);
        expect(refusedMains([["Gone", 4, "Nope"]], [held], LIST, VARIANTS))
            .toEqual([["Gone", 4, "Nope"]]);
    });

    test("it is the mains' own values that count, not their place", () => {
        const mains = [["Mario", 0, ""], ["Mario", 1, ""], ["Blob", -1, ""],
                       ["Mario", 1, "EX"]];
        expect(refusedMains(mains, [], LIST, VARIANTS)).toEqual([]);
    });

    test("a list that is not read leaves it to the server", () => {
        expect(refusedMains([["Gone", 0, ""]], [], {}, VARIANTS)).toEqual([]);
        expect(refusedMains([["Mario", 0, "Nope"]], [], LIST, null)).toEqual([]);
    });
});
