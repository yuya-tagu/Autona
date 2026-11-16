import {
    bestShare,
    correctedSpans,
    gameMarks,
    gameNames,
    playerLanes,
    playheadPercent,
    setAt,
    shadeLevel,
    spanPercent,
    tickLabel,
    ticks,
    timelineRange,
    toPercent,
    toTime,
    videoLength,
} from "./archiveTimeline";

const SETS = {
    duration: 3600,
    games: [[100, 200], [220, 300], [400, 500], [900, 950]],
    games_ocr: [
        {samples: 10, names: {Alpha: 1.0, Beta: 0.4}},
        {samples: 10, names: {Alpha: 0.9}},
        {samples: 10, names: {Gamma: 0.7, Beta: 0.2}},
        {samples: 0, names: {}},
    ],
    sets: [
        {no: 1, start: 90, end: 310, game_spans: [[100, 200], [220, 300]],
         corrected_games: [2]},
        {no: 2, start: 390, end: 510, game_spans: [[400, 500]],
         needs_review: true},
    ],
};

test("the range is the whole video or the selected set", () => {
    expect(timelineRange(SETS, "all", SETS.sets[0])).toEqual({start: 0, end: 3600});
    expect(timelineRange(SETS, "set", SETS.sets[1])).toEqual({start: 390, end: 510});
    expect(timelineRange(SETS, "set", null)).toEqual({start: 0, end: 3600});
    // Where it can be edited: room around the set, within the video.
    expect(timelineRange(SETS, "set", SETS.sets[1], 30))
        .toEqual({start: 360, end: 540});
    expect(timelineRange(SETS, "set", {no: 9, start: 10, end: 3590}, 30))
        .toEqual({start: 0, end: 3600});
    expect(timelineRange(SETS, "all", SETS.sets[1], 30))
        .toEqual({start: 0, end: 3600});
});

test("without a duration the video ends at the last thing on it", () => {
    expect(videoLength({sets: SETS.sets, games: SETS.games})).toBe(950);
    expect(videoLength({sets: [], duration: null})).toBe(1);
    expect(videoLength({sets: [{no: 1, start: 0, end: 70}], duration: 0})).toBe(70);
});

test("times map to percents of the range and back, clamped", () => {
    const range = {start: 100, end: 300};
    expect(toPercent(100, range)).toBe(0);
    expect(toPercent(200, range)).toBe(50);
    expect(toPercent(50, range)).toBe(0);
    expect(toPercent(400, range)).toBe(100);
    expect(toPercent(5, {start: 0, end: 0})).toBe(0);
    expect(toTime(0.25, range)).toBe(150);
    expect(toTime(-1, range)).toBe(100);
    expect(toTime(2, range)).toBe(300);
    expect(toTime(NaN, range)).toBe(100);
});

test("the playhead shows only within the range", () => {
    const range = {start: 100, end: 300};
    expect(playheadPercent(100, range)).toBe(0);
    expect(playheadPercent(200, range)).toBe(50);
    expect(playheadPercent(300, range)).toBe(100);
    expect(playheadPercent(99, range)).toBeNull();
    expect(playheadPercent(301, range)).toBeNull();
    expect(playheadPercent(NaN, range)).toBeNull();
});

test("a span is clipped to the range, or left out", () => {
    const range = {start: 100, end: 300};
    expect(spanPercent(150, 250, range)).toEqual({left: 25, width: 50});
    expect(spanPercent(50, 150, range)).toEqual({left: 0, width: 25});
    expect(spanPercent(0, 100, range)).toBeNull();
    expect(spanPercent(300, 400, range)).toBeNull();
});

test("the ruler takes a round step that keeps the ticks few", () => {
    expect(ticks({start: 0, end: 3600})).toEqual(
        [0, 600, 1200, 1800, 2400, 3000, 3600]);
    expect(ticks({start: 390, end: 510})).toEqual(
        [390, 405, 420, 435, 450, 465, 480, 495, 510]);
    expect(ticks({start: 395, end: 420})).toEqual([395, 400, 405, 410, 415, 420]);
    expect(ticks({start: 0, end: 0})).toEqual([]);
    expect(ticks({start: 0, end: 13 * 3600}).length).toBeLessThanOrEqual(9);
    expect(ticks({start: 0, end: 100 * 3600}).length).toBeLessThanOrEqual(9);
    expect(tickLabel(3900, true)).toBe("1:05");
    expect(tickLabel(3905, false)).toBe("1:05:05");
});

test("the set under a time", () => {
    expect(setAt(SETS.sets, 95).no).toBe(1);
    expect(setAt(SETS.sets, 510).no).toBe(2);
    expect(setAt(SETS.sets, 350)).toBeNull();
});

test("a corrected game is the set's game of that number", () => {
    expect(correctedSpans(SETS.sets[0])).toEqual([[220, 300]]);
    expect(correctedSpans({no: 3, start: 0, end: 1, game_spans: [[0, 1]],
                           corrected_games: [0, 5, "1", 1.5]})).toEqual([]);
    expect(correctedSpans({no: 4, start: 0, end: 1})).toEqual([]);
    expect(gameMarks(SETS).map((mark) => mark.corrected))
        .toEqual([false, true, false, false]);
    // No "games": the sets' own spans.
    expect(gameMarks({sets: SETS.sets}).length).toBe(3);
});

test("a set's game reads the names of the detected game it overlaps most", () => {
    expect(gameNames(SETS, [105, 210])).toEqual({Alpha: 1.0, Beta: 0.4});
    expect(gameNames(SETS, [600, 700])).toBeNull();
    expect(gameNames(SETS, "x")).toBeNull();
    expect(bestShare({Alpha: 0.9, Beta: 0.4})).toBe(0.9);
    expect(bestShare(null)).toBe(0);
});

test("one player lane per name, the most read first", () => {
    const lanes = playerLanes(SETS);
    expect(lanes.map((lane) => lane.name)).toEqual(["Alpha", "Gamma", "Beta"]);
    expect(lanes[0].cells).toEqual([
        {start: 100, end: 200, share: 1.0}, {start: 220, end: 300, share: 0.9}]);
    expect(playerLanes(SETS, 1).map((lane) => lane.name)).toEqual(["Alpha"]);
    expect(playerLanes({sets: [], games: [[0, 1]]})).toEqual([]);
});

test("shares shade in four steps", () => {
    expect([0, 0.1, 0.35, 0.6, 0.85, 1].map(shadeLevel))
        .toEqual([0, 1, 2, 3, 4, 4]);
});
