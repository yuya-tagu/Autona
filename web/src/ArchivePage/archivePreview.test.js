import {
    H264_TYPE,
    MEDIA_ERR_ABORTED,
    MEDIA_ERR_DECODE,
    MEDIA_ERR_NETWORK,
    MEDIA_ERR_SRC_NOT_SUPPORTED,
    browserPageUrl,
    canPlayH264,
    cssUrl,
    framePath,
    jobFromQuery,
    keyAction,
    keyTargetKind,
    nextReviewSet,
    playbackProblem,
    releaseVideo,
    setNumber,
    stepSet,
    videoExtension,
    videoPath,
} from "./archivePreview";

const ROWS = [
    {no: 1, start: 0, end: 1},
    {no: 2, start: 1, end: 2, needs_review: true},
    {no: 3, start: 2, end: 3},
    {no: 5, start: 3, end: 4, needs_review: true},
];

test("the video's extension names its format", () => {
    // A local job's own path (either separator, any case, spaces).
    expect(videoExtension("D:\\rec\\2026-11-16 20-00-00.FLV", null))
        .toBe(".flv");
    expect(videoExtension("  C:/vods/match.ts  ", "ignored.mp4")).toBe(".ts");
    // A URL job's: the worker's download, not the URL's.
    expect(videoExtension("https://youtu.be/x.mp4",
                          "C:\\w\\downloads\\x.open.webm")).toBe(".webm");
    expect(videoExtension("HTTPS://example.com/v", "x.m2ts")).toBe(".m2ts");
    // Nothing to name.
    for (const [source, sets] of [["https://youtu.be/x", undefined],
                                  ["https://youtu.be/x", 5],
                                  ["C:\\vods\\match", null],
                                  ["C:\\vods.d\\match", null],
                                  ["C:\\vods\\match.", null],
                                  ["", null]]) {
        expect(videoExtension(source, sets)).toBeNull();
    }
});

test("the job names its video and frames, never a path", () => {
    expect(videoPath("20261116-100000-abcd"))
        .toBe("/api/v1/archive/jobs/20261116-100000-abcd/video");
    expect(framePath("20261116-100000-abcd", 3))
        .toBe("/api/v1/archive/jobs/20261116-100000-abcd/frames/3");
    expect(browserPageUrl("http://127.0.0.1:5107", "20261116-100000-abcd"))
        .toBe("http://127.0.0.1:5107/web/archive?job=20261116-100000-abcd");
    expect(jobFromQuery("?job=20261116-100000-abcd&nav=collapsed"))
        .toBe("20261116-100000-abcd");
    expect(jobFromQuery("?job=../x")).toBeNull();
    expect(jobFromQuery("")).toBeNull();
});

function element(html) {
    const host = document.createElement("div");
    host.innerHTML = html;
    document.body.appendChild(host);
    return host.querySelector("[data-target]");
}

test("a key's target: typed into, a control, or the page", () => {
    expect(keyTargetKind(element('<input data-target>'))).toBe("text");
    expect(keyTargetKind(element('<textarea data-target></textarea>'))).toBe("text");
    expect(keyTargetKind(element('<div role="combobox" data-target></div>'))).toBe("text");
    expect(keyTargetKind(element('<ul role="listbox"><li data-target>x</li></ul>'))).toBe("text");
    expect(keyTargetKind(element('<button data-target>x</button>'))).toBe("control");
    expect(keyTargetKind(element('<a href="#" data-target>x</a>'))).toBe("control");
    // A set row is a button, but Space there is the page's.
    expect(keyTargetKind(element('<div role="button" data-archive-set="1" data-target></div>')))
        .toBe("other");
    expect(keyTargetKind(element('<video data-target></video>'))).toBe("other");
    expect(keyTargetKind(element('<div data-target></div>'))).toBe("other");
    expect(keyTargetKind(null)).toBe("other");
});

test("the page's keys: Space, Up / Down and N", () => {
    expect(keyAction({key: " "}, "other")).toBe("togglePlay");
    expect(keyAction({key: "ArrowUp"}, "other")).toBe("previousSet");
    expect(keyAction({key: "ArrowDown"}, "control")).toBe("nextSet");
    expect(keyAction({key: "n"}, "other")).toBe("nextReview");
    expect(keyAction({key: "N"}, "other")).toBe("nextReview");
    // Space presses a button itself.
    expect(keyAction({key: " "}, "control")).toBeNull();
});

test("no key of the page while typing, composing or with a modifier", () => {
    for (const key of [" ", "ArrowUp", "ArrowDown", "n"]) {
        expect(keyAction({key}, "text")).toBeNull();
        expect(keyAction({key, isComposing: true}, "other")).toBeNull();
        expect(keyAction({key, ctrlKey: true}, "other")).toBeNull();
        expect(keyAction({key, altKey: true}, "other")).toBeNull();
        expect(keyAction({key, metaKey: true}, "other")).toBeNull();
        expect(keyAction({key, defaultPrevented: true}, "other")).toBeNull();
    }
});

test("I, O and Enter are not the page's (a later stage's)", () => {
    for (const key of ["i", "I", "o", "O", "Enter", "j", "l"]) {
        expect(keyAction({key}, "other")).toBeNull();
    }
});

test("Up / Down step through the listed sets and stop at the ends", () => {
    expect(stepSet(ROWS, null, 1)).toBe(1);
    expect(stepSet(ROWS, null, -1)).toBe(5);
    expect(stepSet(ROWS, 2, 1)).toBe(3);
    expect(stepSet(ROWS, 3, 1)).toBe(5);
    expect(stepSet(ROWS, 5, 1)).toBe(5);
    expect(stepSet(ROWS, 1, -1)).toBe(1);
    // A set the filter hides: from the list's end.
    expect(stepSet(ROWS, 4, 1)).toBe(1);
    expect(stepSet([], 1, 1)).toBeNull();
});

test("N goes to the next set to review, wrapping around", () => {
    expect(nextReviewSet(ROWS, null)).toBe(2);
    expect(nextReviewSet(ROWS, 1)).toBe(2);
    expect(nextReviewSet(ROWS, 2)).toBe(5);
    expect(nextReviewSet(ROWS, 5)).toBe(2);
    expect(nextReviewSet([{no: 7, needs_review: true}], 7)).toBe(7);
    expect(nextReviewSet(ROWS.filter((row) => !row.needs_review), 1)).toBeNull();
});

test("why a video does not play, by the MediaError and the HEAD", () => {
    const [ABORTED, NETWORK, DECODE, UNSUPPORTED] = [
        MEDIA_ERR_ABORTED, MEDIA_ERR_NETWORK, MEDIA_ERR_DECODE,
        MEDIA_ERR_SRC_NOT_SUPPORTED];
    expect([ABORTED, NETWORK, DECODE, UNSUPPORTED]).toEqual([1, 2, 3, 4]);
    // Gone, whatever the element says.
    for (const code of [null, ABORTED, NETWORK, DECODE, UNSUPPORTED]) {
        expect(playbackProblem(code, 404, false)).toBe("missing");
        expect(playbackProblem(code, 404, true)).toBe("missing");
    }
    // There, but a video of a format the preview does not serve (.flv /
    // .ts ...): whatever the <video> says, and never offered to the browser.
    for (const code of [UNSUPPORTED, DECODE, NETWORK, null]) {
        expect(playbackProblem(code, 415, false)).toBe("notPreviewable");
        expect(playbackProblem(code, 415, true)).toBe("notPreviewable");
    }
    // Refused, not asked, or dropped by the network.
    expect(playbackProblem(UNSUPPORTED, 403, false)).toBe("unreachable");
    expect(playbackProblem(UNSUPPORTED, 500, false)).toBe("unreachable");
    expect(playbackProblem(UNSUPPORTED, null, false)).toBe("unreachable");
    expect(playbackProblem(NETWORK, 200, false)).toBe("unreachable");
    expect(playbackProblem(NETWORK, 206, true)).toBe("unreachable");
    // Served, a source this window does not support, no H.264 here: this
    // PC's browser can.
    expect(playbackProblem(UNSUPPORTED, 200, false)).toBe("openInBrowser");
    expect(playbackProblem(UNSUPPORTED, 206, false)).toBe("openInBrowser");
    // Anything else does not play in the browser either.
    expect(playbackProblem(UNSUPPORTED, 200, true)).toBe("unplayable");
    expect(playbackProblem(DECODE, 200, false)).toBe("unplayable");
    expect(playbackProblem(ABORTED, 200, false)).toBe("unplayable");
    expect(playbackProblem(null, 200, false)).toBe("unplayable");
});

test("only a set number goes into a frame's URL", () => {
    expect([0, 3, 12].map(setNumber)).toEqual([0, 3, 12]);
    for (const no of [-1, 1.5, NaN, Infinity, "3", '3"]', null, undefined,
                      {}, [3]]) {
        expect(setNumber(no)).toBeNull();
        expect(framePath("20261116-100000-abcd", no)).toBeNull();
    }
});

test("a CSS url() cannot be ended by what it quotes", () => {
    expect(cssUrl("/a/b.jpg")).toBe('url("/a/b.jpg")');
    expect(cssUrl('/x"), url(evil')).toBe('url("/x\\22 ), url(evil")');
    expect(cssUrl("/x\\y")).toBe('url("/x\\5c y")');
    expect(cssUrl("/x\ny\rz\f")).toBe('url("/x\\a y\\d z\\c ")');
});

test("letting go of a video stops it and drops its source", () => {
    const done = [];
    const video = {
        pause: () => done.push("pause"),
        removeAttribute: (name) => done.push(`remove ${name}`),
        load: () => done.push("load"),
    };
    releaseVideo(video);
    expect(done).toEqual(["pause", "remove src", "load"]);
    expect(() => releaseVideo(null)).not.toThrow();
    expect(() => releaseVideo({...video, load: () => {
        throw new Error("not implemented");
    }})).not.toThrow();
});

test("whether this browser decodes H.264", () => {
    const asked = [];
    const video = (answer) => ({canPlayType: (type) => {
        asked.push(type);
        return answer;
    }});
    expect(canPlayH264(video(""))).toBe(false);
    expect(canPlayH264(video("probably"))).toBe(true);
    expect(canPlayH264(video("maybe"))).toBe(true);
    expect(canPlayH264(null)).toBe(false);
    expect(canPlayH264({canPlayType: () => {
        throw new Error("x");
    }})).toBe(false);
    expect(asked[0]).toBe(H264_TYPE);
});
