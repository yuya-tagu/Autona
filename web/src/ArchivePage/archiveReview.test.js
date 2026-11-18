import {
    EMPTY_REVIEW,
    ReviewSaver,
    candidates,
    canonical,
    confirmedCount,
    holders,
    keptRows,
    movedRange,
    nextUnconfirmed,
    pointerTime,
    rangeFits,
    readReview,
    removedRows,
    reviewedRow,
    reviewedRows,
    sameResult,
    withConfirmed,
    withEdge,
    withEntry,
    withRangeReset,
    withRemoved,
    withRestored,
    withResult,
    withSkin,
} from "./archiveReview";

// The archive page's review (ADR-006 A4): what the server keeps (only what
// differs from the worker's), the IN / OUT the keys and a drag give, the
// results a set may be given, Enter's next set and the coalescing save.

const KAPPA = {round: "WF", p1: "Kappa", s1: 3, p2: "Lambda", s2: 1};
const MU = {round: "GF", p1: "Kappa", s1: 2, p2: "Mu", s2: 3};
const UNUSED = {round: "LF", p1: "Mu", s1: 3, p2: "Lambda", s2: 0};
const SETS = {
    duration: 3600,
    sets: [
        {no: 1, start: 120, end: 720, set: KAPPA, title: "Cup WF Kappa vs Lambda",
         matched: true},
        {no: 2, start: 780, end: 1380, set: null, needs_review: true},
        {no: 3, start: 1400, end: 2000, set: MU, matched: true},
    ],
    unused_sets: [UNUSED, {...KAPPA}],
};
const [ONE, TWO, THREE] = SETS.sets;

test("an entry keeps only what differs from the worker's", () => {
    let review = withEntry(EMPTY_REVIEW, ONE, {start: 120, end: 700,
                                               confirmed: false,
                                               set: {...KAPPA}});
    expect(review.sets).toEqual({1: {end: 700}});
    review = withConfirmed(review, ONE, true);
    expect(review.sets).toEqual({1: {confirmed: true, end: 700}});
    review = withRangeReset(withConfirmed(review, ONE, false), ONE);
    expect(review.sets).toEqual({});
    // The empty review is never changed in place.
    expect(EMPTY_REVIEW.sets).toEqual({});
});

test("a result given is kept unless it is the set's own", () => {
    let review = withResult(EMPTY_REVIEW, TWO, UNUSED);
    expect(review.sets).toEqual({2: {set: UNUSED}});
    review = withResult(review, ONE, null);
    expect(review.sets[1]).toEqual({set: null});
    // Its own again (keys in another order: the same result).
    review = withResult(review, ONE, {p2: "Lambda", s2: 1, round: "WF",
                                      p1: "Kappa", s1: 3});
    expect(review.sets[1]).toBeUndefined();
    // No result on a set that has none is no change.
    expect(withResult(EMPTY_REVIEW, TWO, null).sets).toEqual({});
});

test("a set as reviewed", () => {
    const review = {sets: {1: {confirmed: true, start: 100, set: UNUSED},
                           3: {set: null}}};
    const one = reviewedRow(ONE, review);
    expect(one).toMatchObject({no: 1, start: 100, end: 720, set: UNUSED,
                               confirmed: true, rangeEdited: true,
                               setEdited: true, matched: true,
                               title: "LF Mu vs Lambda"});
    expect(reviewedRow(THREE, review)).toMatchObject(
        {set: null, matched: false, title: null, confirmed: false});
    expect(reviewedRow(TWO, review)).toMatchObject(
        {start: 780, end: 1380, rangeEdited: false, setEdited: false,
         confirmed: false});
    expect(confirmedCount(reviewedRows(SETS.sets, review))).toBe(1);
});

test("a confirmed set no longer asks for review", () => {
    const doubted = {...TWO, needs_review: true, reason: "no result"};
    expect(reviewedRow(doubted, {sets: {}}).needs_review).toBe(true);
    expect(reviewedRow(doubted, {sets: {2: {confirmed: true}}}).needs_review)
        .toBe(false);
    // A removed set is not confirmed: it keeps the worker's doubt.
    expect(reviewedRow(doubted, {sets: {2: {confirmed: true, removed: true}}})
        .needs_review).toBe(true);
});

// Results with their characters (the "autona" key Autona puts in them).
const DRAWN = {...KAPPA, autona: {game: "ssbu", c1: ["mario"],
                                  c2: ["luigi", "peach"]}};
const DRAWN_OTHER = {...UNUSED, autona: {game: "ssbu", c1: ["mario"],
                                         c2: ["koopa"]}};
const DRAWN_ROW = {...ONE, set: DRAWN};

test("a colour is kept per side and character, the default never", () => {
    let review = withSkin(EMPTY_REVIEW, DRAWN_ROW, "c2", "peach", 3);
    expect(review.sets).toEqual({1: {skins: {c2: {peach: 3}}}});
    review = withSkin(review, DRAWN_ROW, "c1", "mario", 1);
    expect(review.sets[1].skins).toEqual({c1: {mario: 1}, c2: {peach: 3}});
    expect(reviewedRow(DRAWN_ROW, review).skins)
        .toEqual({c1: {mario: 1}, c2: {peach: 3}});
    // Back to the default: its key goes, then the side, then the entry.
    review = withSkin(review, DRAWN_ROW, "c2", "peach", 0);
    expect(review.sets[1].skins).toEqual({c1: {mario: 1}});
    review = withSkin(review, DRAWN_ROW, "c1", "mario", 0);
    expect(review.sets).toEqual({});
    expect(reviewedRow(DRAWN_ROW, review).skins).toEqual({});
    // A character the set's result does not name on that side is not kept.
    expect(withSkin(EMPTY_REVIEW, DRAWN_ROW, "c1", "peach", 2).sets).toEqual({});
    // Other edits keep the colours.
    review = withConfirmed(withSkin(EMPTY_REVIEW, DRAWN_ROW, "c2", "luigi", 2),
                           DRAWN_ROW, true);
    expect(review.sets[1]).toEqual({confirmed: true, skins: {c2: {luigi: 2}}});
});

test("another result keeps only the colours of its characters", () => {
    let review = withSkin(withSkin(EMPTY_REVIEW, DRAWN_ROW, "c1", "mario", 1),
                          DRAWN_ROW, "c2", "luigi", 2);
    review = withResult(review, DRAWN_ROW, DRAWN_OTHER);
    expect(review.sets[1]).toEqual({set: DRAWN_OTHER, skins: {c1: {mario: 1}}});
    expect(reviewedRow(DRAWN_ROW, review).skins).toEqual({c1: {mario: 1}});
    // No result: no characters, no colours.
    expect(withResult(review, DRAWN_ROW, null).sets[1]).toEqual({set: null});
    // Removed: they go with the rest.
    expect(withRemoved(review, DRAWN_ROW).sets[1]).toEqual({removed: true});
    expect(reviewedRow(DRAWN_ROW, {sets: {1: {removed: true,
                                              skins: {c1: {mario: 1}}}}}).skins)
        .toEqual({});
});

test("IN / OUT stay at least a second apart and inside the video", () => {
    const range = {start: 120, end: 720};
    expect(movedRange(range, "start", 300.456, 3600)).toEqual({start: 300.45, end: 720});
    expect(movedRange(range, "end", 300.451, 3600)).toEqual({start: 120, end: 300.46});
    // Past the other edge: held a second off it.
    expect(movedRange(range, "start", 800, 3600)).toEqual({start: 719, end: 720});
    expect(movedRange(range, "end", 50, 3600)).toEqual({start: 120, end: 121});
    // Before the video / past its end.
    expect(movedRange(range, "start", -5, 3600)).toEqual({start: 0, end: 720});
    expect(movedRange({start: 3000, end: 3500}, "end", 3700, 3600))
        .toEqual({start: 3000, end: 3600});
    // A second before the video's end less END_MARGIN (0.05).
    expect(movedRange({start: 3000, end: 3700}, "start", 3650, 3600))
        .toEqual({start: 3598.95, end: 3700});
    expect(movedRange({start: 3000, end: 3600}, "start", 3599.5, 3600))
        .toEqual({start: 3598.95, end: 3600});
    // OUT up to the video's end, kept to 2 decimals (down).
    expect(movedRange({start: 3000, end: 3500}, "end", 3700, 3600.017))
        .toEqual({start: 3000, end: 3600.01});
    // No duration: only the minimum length.
    expect(movedRange(range, "end", 5000, null)).toEqual({start: 120, end: 5000});
    // An odd worker edge keeps the length at least a second when rounded.
    const odd = movedRange({start: 0, end: 720.005}, "start", 800, null);
    expect(720.005 - odd.start).toBeGreaterThanOrEqual(1);
});

// [start, end, fits]: core ArchiveReview's checks, the same arithmetic
// (tests/test_archive_jobs.py has the same table).
test.each([
    [0.13, 1.13, false], [0.13, 1.14, true],
    [15.06, 16.06, false], [15.06, 16.07, true],
    [14.06, 15.06, true], [15.08, 16.08, false],
])("a range %p - %p fits: %p", (start, end, fits) => {
    expect(rangeFits(start, end, 3600)).toBe(fits);
});

test("a range at the video's end keeps END_MARGIN", () => {
    expect(rangeFits(3598.95, 3600, 3600)).toBe(true);
    expect(rangeFits(3598.96, 3600, 3600)).toBe(false);
    expect(rangeFits(3599, 3700, 3600)).toBe(false);
    expect(rangeFits(3599, 3700, null)).toBe(true);
});

test("a rounded range a hair short of a second takes a hundredth more", () => {
    // OUT a second after IN: 1.13 - 0.13 < 1 in floats, so OUT goes on.
    expect(movedRange({start: 0.13, end: 5}, "end", 0.5, null))
        .toEqual({start: 0.13, end: 1.14});
    expect(movedRange({start: 0.13, end: 5}, "end", 1.13, null))
        .toEqual({start: 0.13, end: 1.14});
    expect(movedRange({start: 15.06, end: 20}, "end", 16, 3600))
        .toEqual({start: 15.06, end: 16.07});
    expect(movedRange({start: 15.08, end: 20}, "end", 16.08, 3600))
        .toEqual({start: 15.08, end: 16.09});
    // Exactly a second in floats: kept.
    expect(movedRange({start: 14.06, end: 20}, "end", 15, 3600))
        .toEqual({start: 14.06, end: 15.06});
    // IN a second before OUT: IN goes back.
    expect(movedRange({start: 0, end: 16.06}, "start", 15.5, null))
        .toEqual({start: 15.05, end: 16.06});
    // Whatever the hundredths, the range the page makes is one the server
    // takes.
    for (let k = 0; k < 4000; k += 1) {
        const t = k / 100;
        const out = movedRange({start: t, end: t + 5}, "end", t + 1, 3600);
        expect([t, rangeFits(out.start, out.end, 3600)]).toEqual([t, true]);
        const into = movedRange({start: 0, end: t + 1}, "start", t, 3600);
        expect([t, rangeFits(into.start, into.end, 3600)]).toEqual([t, true]);
    }
});

test("an edge moved through the review", () => {
    let review = withEdge(EMPTY_REVIEW, ONE, "start", 150.5, 3600);
    expect(review.sets).toEqual({1: {start: 150.5}});
    review = withEdge(review, ONE, "end", 151, 3600);
    expect(review.sets).toEqual({1: {start: 150.5, end: 151.5}});
    // Back on the worker's edges: nothing left.
    review = withEdge(withEdge(review, ONE, "end", 720, 3600), ONE, "start",
                      120, 3600);
    expect(review.sets).toEqual({});
});

test("the results a set may be given, and which another set holds", () => {
    expect(candidates(SETS)).toEqual([KAPPA, MU, UNUSED]);
    // One comparison: keys in any order, whole numbers as integers (the
    // server's ResultKey makes the same string).
    expect(sameResult({p2: "Lambda", s2: 1, round: "WF", p1: "Kappa", s1: 3},
                      KAPPA)).toBe(true);
    expect(sameResult(null, undefined)).toBe(true);
    expect(sameResult(KAPPA, MU)).toBe(false);
    expect(canonical({b: 1.0, a: [2, true, null, 2.5]}))
        .toBe('{"a":[2,true,null,2.5],"b":1}');
    const rows = reviewedRows(SETS.sets, {sets: {2: {set: UNUSED}}});
    const held = holders(rows, 2);
    expect(held.get(canonical(KAPPA))).toBe(1);
    expect(held.get(canonical(MU))).toBe(3);
    // The set's own is not "held by another".
    expect(held.has(canonical(UNUSED))).toBe(false);
    expect(holders(rows, 1).get(canonical(UNUSED))).toBe(2);
});

test("Enter's next set: the next one not confirmed, wrapping around", () => {
    const rows = reviewedRows(SETS.sets, {sets: {2: {confirmed: true}}});
    expect(nextUnconfirmed(rows, 1)).toBe(3);
    expect(nextUnconfirmed(rows, 3)).toBe(1);
    expect(nextUnconfirmed(rows, null)).toBe(1);
    const all = reviewedRows(SETS.sets, {sets: {1: {confirmed: true},
                                                2: {confirmed: true},
                                                3: {confirmed: true}}});
    expect(nextUnconfirmed(all, 2)).toBeNull();
    expect(nextUnconfirmed(reviewedRows([ONE], EMPTY_REVIEW), 1)).toBeNull();
    // A removed set is not one to confirm.
    const gone = reviewedRows(SETS.sets, {sets: {2: {removed: true}}});
    expect(nextUnconfirmed(gone, 1)).toBe(3);
});

test("a removed set keeps nothing else and holds no result", () => {
    let review = withConfirmed(withEdge(EMPTY_REVIEW, ONE, "end", 700, 3600),
                               ONE, true);
    review = withRemoved(review, ONE);
    expect(review.sets).toEqual({1: {removed: true}});
    // An edit leaves it removed, as it was.
    expect(withConfirmed(review, ONE, true).sets).toEqual({1: {removed: true}});
    expect(withResult(review, ONE, UNUSED).sets).toEqual({1: {removed: true}});
    const rows = reviewedRows(SETS.sets, review);
    expect(rows[0]).toMatchObject({removed: true, confirmed: false,
                                   start: 120, end: 720, set: KAPPA});
    expect(rows[1].removed).toBe(false);
    expect(keptRows(rows).map((row) => row.no)).toEqual([2, 3]);
    expect(removedRows(rows).map((row) => row.no)).toEqual([1]);
    expect(confirmedCount(rows)).toBe(0);
    // Its result is free for another set.
    expect(holders(rows, 2).has(canonical(KAPPA))).toBe(false);
    expect(holders(rows, 2).get(canonical(MU))).toBe(3);
    // What the server answers with it is read as it is.
    expect(readReview({format: 1, sets: {1: {removed: true}}}).sets)
        .toEqual({1: {removed: true}});
});

test("a removed set comes back unconfirmed, as the worker found it", () => {
    const removed = withRemoved(withConfirmed(EMPTY_REVIEW, ONE, true), ONE);
    expect(withRestored(removed, SETS.sets, ONE).sets).toEqual({});
    // Its result given to another set meanwhile: back with none.
    const taken = withResult(removed, TWO, KAPPA);
    expect(withRestored(taken, SETS.sets, ONE).sets)
        .toEqual({1: {set: null}, 2: {set: KAPPA}});
    // A set with no result of its own comes back as it was.
    expect(withRestored(withRemoved(EMPTY_REVIEW, TWO), SETS.sets, TWO).sets)
        .toEqual({});
    // Other sets' entries are left as they are.
    const other = withRemoved(withConfirmed(EMPTY_REVIEW, THREE, true), TWO);
    expect(withRestored(other, SETS.sets, TWO).sets)
        .toEqual({3: {confirmed: true}});
});

test("a drag's time: the pointer over the lane, clamped to the range", () => {
    const range = {start: 100, end: 300};
    // Half way over a lane 50..250 px: half way through the range.
    expect(pointerTime(150, 50, 200, range)).toBe(200);
    expect(pointerTime(100, 50, 200, range)).toBe(150);
    expect(pointerTime(0, 50, 200, range)).toBe(100);
    expect(pointerTime(999, 50, 200, range)).toBe(300);
    expect(pointerTime(150, 50, 0, range)).toBe(100);
});

test("what the server answers is read as a review", () => {
    expect(readReview({format: 1, sets: {1: {confirmed: true}}}))
        .toEqual({format: 1, sets: {1: {confirmed: true}}});
    for (const broken of [null, undefined, {}, {sets: []}, {sets: "x"}]) {
        expect(readReview(broken)).toEqual({format: 1, sets: {}});
    }
});

describe("the saver", () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    const flushPromises = async () => {
        for (let i = 0; i < 5; i += 1) {
            await Promise.resolve();
        }
    };

    function make() {
        const sent = [];
        const states = [];
        const answers = [];
        const saver = new ReviewSaver((value) => {
            sent.push(value);
            return new Promise((resolve, reject) => answers.push({resolve, reject}));
        }, (state) => states.push(state.status), 600);
        return {saver, sent, states, answers};
    }

    test("edits in a row are one save, of the last value", async () => {
        const {saver, sent, states, answers} = make();
        saver.push("a");
        jest.advanceTimersByTime(300);
        saver.push("b");
        jest.advanceTimersByTime(599);
        expect(sent).toEqual([]);
        jest.advanceTimersByTime(1);
        expect(sent).toEqual(["b"]);
        answers[0].resolve();
        await flushPromises();
        expect(states).toEqual(["pending", "pending", "saving", "saved"]);
    });

    test("one save at a time: what comes meanwhile goes after, latest only",
         async () => {
        const {saver, sent, answers} = make();
        saver.push("a");
        jest.advanceTimersByTime(600);
        saver.push("b");
        saver.push("c");
        jest.advanceTimersByTime(600);
        // "a" is still in flight.
        expect(sent).toEqual(["a"]);
        answers[0].resolve();
        await flushPromises();
        expect(sent).toEqual(["a", "c"]);
    });

    test("a failed save is told and sent again by flush", async () => {
        const {saver, sent, states, answers} = make();
        saver.push("a");
        jest.advanceTimersByTime(600);
        answers[0].reject(new Error("refused"));
        await flushPromises();
        expect(states[states.length - 1]).toBe("error");
        // Not retried on its own.
        jest.advanceTimersByTime(5000);
        expect(sent).toEqual(["a"]);
        const flushed = saver.flush();
        await flushPromises();
        expect(sent).toEqual(["a", "a"]);
        answers[1].resolve();
        expect(await flushed).toBe(true);
    });

    test("flush sends what is pending now and says whether it was saved",
         async () => {
        const {saver, sent, answers} = make();
        saver.push("a");
        const flushed = saver.flush();
        await flushPromises();
        expect(sent).toEqual(["a"]);
        answers[0].reject(new Error("no"));
        expect(await flushed).toBe(false);
        // A new edit supersedes the failed one.
        saver.push("b");
        const again = saver.flush();
        await flushPromises();
        expect(sent).toEqual(["a", "b"]);
        answers[1].resolve();
        expect(await again).toBe(true);
    });

    test("leaving sends what is pending and reports nothing", async () => {
        const {saver, sent, states, answers} = make();
        saver.push("a");
        const told = states.length;
        const settled = saver.dispose();
        expect(sent).toEqual(["a"]);
        expect(states.length).toBe(told);
        saver.push("b");
        jest.advanceTimersByTime(600);
        expect(sent).toEqual(["a"]);
        // Settled once it was sent (the next view of the job reads after).
        answers[0].resolve();
        expect(await settled).toBe(true);
    });

    test("leaving sends a value that failed again; one forgotten is not",
         async () => {
        const {saver, sent, answers} = make();
        saver.push("a");
        jest.advanceTimersByTime(600);
        answers[0].reject(new Error("disk full"));
        await flushPromises();
        expect(saver.idle()).toBe(false);
        const settled = saver.dispose();
        expect(sent).toEqual(["a", "a"]);
        answers[1].resolve();
        expect(await settled).toBe(true);
        expect(saver.idle()).toBe(true);

        // A value the server refused (400): forgotten, never sent again.
        const other = make();
        other.saver.push("x");
        jest.advanceTimersByTime(600);
        other.answers[0].reject(Object.assign(new Error("no"), {status: 400}));
        await flushPromises();
        other.saver.forget();
        expect(other.saver.idle()).toBe(true);
        expect(await other.saver.dispose()).toBe(true);
        expect(other.sent).toEqual(["x"]);
    });

    test("leaving the page sends what is waiting in a request that outlives it",
         async () => {
        const sent = [];
        const saver = new ReviewSaver((value, options) => {
            sent.push([value, options]);
            return options?.keepalive ? Promise.resolve()
                : Promise.reject(new Error("refused"));
        }, () => undefined, 600);
        saver.sendOnUnload();
        expect(sent).toEqual([]);
        saver.push("a");
        saver.sendOnUnload();
        expect(sent).toEqual([["a", {keepalive: true}]]);
        // Sent: the timer does not send it again.
        jest.advanceTimersByTime(600);
        expect(sent.length).toBe(1);
        // A value that failed goes too.
        saver.push("b");
        jest.advanceTimersByTime(600);
        await flushPromises();
        saver.sendOnUnload();
        expect(sent.slice(1)).toEqual([["b", undefined], ["b", {keepalive: true}]]);
    });
});
