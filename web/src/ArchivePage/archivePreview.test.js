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
    videoPlayer,
    youtubeEmbedId,
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
    for (const key of [" ", "ArrowUp", "ArrowDown", "n", "i", "o", "Enter"]) {
        expect(keyAction({key}, "text")).toBeNull();
        expect(keyAction({key, isComposing: true}, "other")).toBeNull();
        expect(keyAction({key, ctrlKey: true}, "other")).toBeNull();
        expect(keyAction({key, altKey: true}, "other")).toBeNull();
        expect(keyAction({key, metaKey: true}, "other")).toBeNull();
        expect(keyAction({key, defaultPrevented: true}, "other")).toBeNull();
    }
});

test("I / O set IN / OUT and Enter confirms (ADR-006 A4)", () => {
    expect(keyAction({key: "i"}, "other")).toBe("setIn");
    expect(keyAction({key: "I"}, "other")).toBe("setIn");
    expect(keyAction({key: "o"}, "control")).toBe("setOut");
    expect(keyAction({key: "O"}, "other")).toBe("setOut");
    expect(keyAction({key: "Enter"}, "other")).toBe("confirm");
    // Enter presses a button or follows a link itself.
    expect(keyAction({key: "Enter"}, "control")).toBeNull();
    for (const key of ["j", "l", "Escape", "Tab"]) {
        expect(keyAction({key}, "other")).toBeNull();
    }
    // Not with Shift (a capital I / O by Caps Lock still is), and Enter not
    // held down: its repeat would confirm set after set.
    for (const key of ["i", "I", "o", "O", "Enter"]) {
        expect(keyAction({key, shiftKey: true}, "other")).toBeNull();
    }
    expect(keyAction({key: "Enter", repeat: true}, "other")).toBeNull();
    expect(keyAction({key: "i", repeat: true}, "other")).toBe("setIn");
    // The other keys keep Shift (N is Shift+n) and their repeat.
    expect(keyAction({key: "N", shiftKey: true}, "other")).toBe("nextReview");
    expect(keyAction({key: "ArrowDown", repeat: true}, "other")).toBe("nextSet");
});

test("an open list's options are typed into: no key of the page", () => {
    // MUI's Select puts its listbox in a portal; React still bubbles its keys
    // to the review screen.
    document.body.innerHTML = `<ul role="listbox"><li role="option" id="o">x</li></ul>
        <div role="combobox" id="c"></div>`;
    for (const id of ["o", "c"]) {
        const kind = keyTargetKind(document.getElementById(id));
        expect(kind).toBe("text");
        for (const key of ["i", "o", "Enter"]) {
            expect(keyAction({key}, kind)).toBeNull();
        }
    }
    expect(keyTargetKind(document.querySelector('[role="listbox"]')))
        .toBe("text");
    document.body.innerHTML = "";
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

test("a YouTube job plays in the embed: its source is YouTube's, its id one",
     () => {
    const ID = "abcDEF12_-9";
    for (const source of [
        "https://www.youtube.com/watch?v=abcDEF12_-9",
        "https://youtube.com/live/abcDEF12_-9",
        "  https://m.youtube.com/watch?v=abcDEF12_-9 ",
        "http://youtu.be/abcDEF12_-9",
        "https://WWW.YouTube.com/watch?v=x",
        "https://www.youtube-nocookie.com/embed/abcDEF12_-9",
    ]) {
        expect(youtubeEmbedId(source, ID)).toBe(ID);
    }
    // Not YouTube: another site's URL (vodsplit can read an id off its
    // download's name), a local file, a look-alike host.
    for (const source of ["https://www.twitch.tv/videos/123",
                          "https://notyoutube.com/watch?v=x",
                          "https://youtube.com.evil.example/watch",
                          "C:\\vods\\abcDEF12_-9.mp4",
                          "file:///C:/youtube.com/x.mp4",
                          "javascript:alert(1)//youtube.com",
                          "", null, undefined]) {
        expect(youtubeEmbedId(source, ID)).toBeNull();
    }
    // Only an id of the embed's shape goes into its URL.
    const url = "https://www.youtube.com/watch?v=x";
    for (const id of ["abcDEF12_-", "abcDEF12_-90", "abc DEF12_-", "a&b=c#defgh",
                      "abcDEF12_-\n", null, undefined, 12345678901]) {
        expect(youtubeEmbedId(url, id)).toBeNull();
    }
});

test("the job's own video as the page's player", () => {
    const done = [];
    const video = {
        paused: true,
        currentTime: 12.5,
        play: () => {
            done.push("play");
            return Promise.reject(new Error("not allowed"));
        },
        pause: () => done.push("pause"),
    };
    const player = videoPlayer(video);
    expect(player.paused()).toBe(true);
    expect(player.currentTime()).toBe(12.5);
    // A play the browser refuses is not thrown.
    player.play();
    player.pause();
    player.seek(90);
    expect(video.currentTime).toBe(90);
    expect(done).toEqual(["play", "pause"]);
    // A play that returns nothing (an older DOM) is fine too.
    expect(() => videoPlayer({...video, play: () => undefined}).play())
        .not.toThrow();
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
