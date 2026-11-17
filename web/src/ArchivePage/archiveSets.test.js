import {
    displayLabel,
    etaParts,
    filterSets,
    formatClock,
    formatDuration,
    rowText,
    scoreText,
    summarize,
    unusedText,
    youtubeLink,
    sourceName,
    nameMatchText,
    correctedText,
    characterIcon,
    dayText,
    isEstimate,
    setCharacters,
    sourceCounts,
} from "./archiveSets";

const NO_MATCH = "(結果に該当なし)";

test("clock is h:mm:ss", () => {
    expect(formatClock(0)).toBe("0:00:00");
    expect(formatClock(3725.9)).toBe("1:02:05");
    expect(formatClock(-3)).toBe("0:00:00");
});

test("duration is m:ss", () => {
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(3700)).toBe("61:40");
});

test("youtube link needs a plausible id and floors the second", () => {
    const url = "https://www.youtube.com/watch?v=abcDEF12345";
    expect(youtubeLink(url, "abcDEF12345", 61.9))
        .toBe("https://www.youtube.com/watch?v=abcDEF12345&t=61s");
    expect(youtubeLink(url, null, 5)).toBeNull();
    expect(youtubeLink(url, "a b&x", 5)).toBeNull();
    // A local file never links, even if its source_id looks like an id.
    expect(youtubeLink("/vods/match_abcDEF12345", "abcDEF12345", 5))
        .toBeNull();
    expect(youtubeLink(null, "abcDEF12345", 5)).toBeNull();
});

test("display label follows vodsplit", () => {
    expect(displayLabel({seen_names: ["A", "B"], labels: ["x"]}, NO_MATCH))
        .toBe("（A / B）");
    expect(displayLabel({labels: ["x"]}, NO_MATCH)).toBe("（x）");
    expect(displayLabel({}, NO_MATCH)).toBe(NO_MATCH);
    expect(rowText({title: "T", seen_names: ["A"]}, NO_MATCH)).toBe("T");
    expect(rowText({seen_names: ["A"]}, NO_MATCH)).toBe("（A）");
});

test("score text only with both scores", () => {
    expect(scoreText({set: {s1: 3, s2: 0}})).toBe("3-0");
    expect(scoreText({set: {s1: 3, s2: null}})).toBe("");
    expect(scoreText({set: null})).toBe("");
});

const ROWS = [
    {no: 1, start: 0, end: 1, matched: true, needs_review: false},
    {no: 2, start: 0, end: 1, matched: false, needs_review: true},
    {no: 3, start: 0, end: 1, matched: true, needs_review: true},
];

test("filter and summary", () => {
    expect(filterSets(ROWS, "all")).toHaveLength(3);
    expect(filterSets(ROWS, "matched").map((r) => r.no)).toEqual([1, 3]);
    expect(filterSets(ROWS, "review").map((r) => r.no)).toEqual([2, 3]);
    expect(summarize({sets: ROWS, games: [[0, 1], [2, 3]]}))
        .toEqual({sets: 3, matched: 2, review: 2, games: 2});
    expect(summarize(null)).toEqual({sets: 0, matched: 0, review: 0, games: 0});
});

test("eta parts", () => {
    expect(etaParts(125)).toEqual({min: 2, sec: 5});
    expect(etaParts(null)).toBeNull();
    expect(etaParts(-1)).toBeNull();
});

test("unused result text", () => {
    expect(unusedText({round: "WF", p1: "a", s1: 3, p2: "b", s2: 1}))
        .toBe("WF: a 3-1 b");
});

test("source name keeps URLs and shortens local paths", () => {
    expect(sourceName("C:\\vods\\2026\\final.mp4")).toBe("final.mp4");
    expect(sourceName("/home/u/vods/final.mp4")).toBe("final.mp4");
    expect(sourceName("https://www.youtube.com/watch?v=abc/def"))
        .toBe("https://www.youtube.com/watch?v=abc/def");
    expect(sourceName("final.mp4")).toBe("final.mp4");
});

test("name match is a whole percent or a dash", () => {
    expect(nameMatchText(0.99)).toBe("99%");
    expect(nameMatchText(1)).toBe("100%");
    expect(nameMatchText(null)).toBe("—");
    expect(nameMatchText(undefined)).toBe("—");
});

test("corrected games list or null", () => {
    expect(correctedText([2, 3])).toBe("Game 2, 3");
    expect(correctedText([])).toBeNull();
    expect(correctedText(null)).toBeNull();
});

// The results' characters (ADR-006 A5).

test("a set's codenames are Autona's key, only names that stay in their folder", () => {
    const icons = {mario: "base_files/icon/chara_2_mario_00.png",
                   luigi: "base_files/icon/chara_2_luigi_00.png"};
    expect(setCharacters({autona: {game: "ssbu", c1: ["mario", "koopa"],
                                   c2: ["luigi"], icons}}))
        .toEqual({game: "ssbu", c1: ["mario", "koopa"], c2: ["luigi"],
                  icons});
    // An icon that may leave the game's folder is left out.
    expect(setCharacters({autona: {game: "ssbu", c1: ["../x", "a/b", 3],
                                   c2: ["pikachu"], icons: {
                                       pikachu: "../../secret.png",
                                       mario: "/etc/passwd",
                                       luigi: "base_files/../x.png",
                                       "a/b": "base_files/icon/a.png",
                                       ness: 3}}}))
        .toEqual({game: "ssbu", c1: [], c2: ["pikachu"], icons: {}});
    expect(setCharacters({autona: {game: "ssbu", c1: ["mario"],
                                   icons: ["base_files/icon/x.png"]}}))
        .toEqual({game: "ssbu", c1: ["mario"], c2: [], icons: {}});
    expect(setCharacters({autona: {game: "ssbu", c1: [], c2: []}})).toBeNull();
    expect(setCharacters({autona: {game: "..", c1: ["mario"]}})).toBeNull();
    expect(setCharacters({autona: "ssbu"})).toBeNull();
    expect(setCharacters({c1: "マリオ"})).toBeNull();
    expect(setCharacters(null)).toBeNull();
});

test("a character's icon is its file in the game's folder", () => {
    expect(characterIcon("ssbu", "base_files/icon/chara_2_mario_00.png"))
        .toBe("/user_data/games/ssbu/base_files/icon/chara_2_mario_00.png");
    expect(characterIcon("game2", "base_files/icon/ryu_0.png"))
        .toBe("/user_data/games/game2/base_files/icon/ryu_0.png");
    expect(characterIcon("ssbu", "base_files/../../x.png")).toBeNull();
    expect(characterIcon("ssbu", "/base_files/icon/x.png")).toBeNull();
    expect(characterIcon("a/b", "base_files/icon/x.png")).toBeNull();
    expect(characterIcon("ssbu", "")).toBeNull();
    expect(characterIcon("ssbu", undefined)).toBeNull();
});

test("only a player's main is an estimate", () => {
    expect(isEstimate({char_source: "mains"})).toBe(true);
    expect(isEstimate({char_source: "scoreboard"})).toBe(false);
    expect(isEstimate({})).toBe(false);
    expect(isEstimate(null)).toBe(false);
});

test("the summary says the counts that are not 0, strongest first", () => {
    expect(sourceCounts({sets: 6, game: "ssbu", records: 0, sources: {
        none: 1, mains: 2, scoreboard: 3, report: 0}}))
        .toEqual([["scoreboard", 3], ["mains", 2], ["none", 1]]);
    expect(sourceCounts({sets: 0, game: null, records: 0, sources: {}}))
        .toEqual([]);
});

test("a match log day reads as a date", () => {
    expect(dayText("20261114")).toBe("2026-11-14");
    expect(dayText("2026-11-14")).toBe("2026-11-14");
});
