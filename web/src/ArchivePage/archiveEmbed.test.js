import {
    BUFFERING,
    CUED,
    EMBED_ALLOW,
    EMBED_HOST,
    ENDED,
    LISTEN_MS,
    PAUSED,
    PLAYING,
    READY_TIMEOUT_MS,
    UNSTARTED,
    embedUrl,
    mountYouTube,
    readMessage,
    youtubePlayer,
} from "./archiveEmbed";

// YouTube's embedded player for a YouTube job's preview (ADR-006 D7): its
// frame, spoken to over postMessage. jsdom loads nothing into the frame: the
// test records what the page posts to it and plays the frame's part,
// dispatching its messages on the page's window.

const ID = "abcDEF12_-9";

afterEach(() => {
    document.body.innerHTML = "";
    jest.useRealTimers();
});

// The player mounted for ID: its frame, what the page posted to it, what
// the page was told, and the frame's voice.
function mount() {
    const element = document.createElement("div");
    document.body.appendChild(element);
    const told = {ready: [], time: [], duration: [], playing: [], failed: 0};
    const unmount = mountYouTube(element, ID, {
        ready: (p) => told.ready.push(p),
        time: (t) => told.time.push(t),
        duration: (d) => told.duration.push(d),
        playing: (p) => told.playing.push(p),
        failed: () => {
            told.failed += 1;
        },
    }, "Preview");
    const frame = element.querySelector("iframe");
    const posts = jest.spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => undefined);
    // What the page posted since the spy: [message, target origin].
    const sent = () => posts.mock.calls.map(
        ([data, target]) => [JSON.parse(data), target]);
    const commands = () => sent()
        .filter(([message]) => message.event === "command")
        .map(([message]) => [message.func, ...message.args]);
    const say = (message, {origin = EMBED_HOST,
                           source = frame.contentWindow} = {}) => {
        window.dispatchEvent(new MessageEvent("message", {
            data: typeof message === "string" ? message
                : JSON.stringify(message),
            origin, source}));
    };
    return {element, frame, told, unmount, posts, sent, commands, say};
}

describe("the frame", () => {
    test("is YouTube's, from youtube-nocookie, not autoplaying, sending a referrer",
         () => {
        expect(embedUrl(ID, "http://127.0.0.1:5107")).toBe(
            "https://www.youtube-nocookie.com/embed/abcDEF12_-9?enablejsapi=1"
            + "&origin=http%3A%2F%2F127.0.0.1%3A5107&playsinline=1&rel=0");
        const {frame} = mount();
        expect(frame.getAttribute("src"))
            .toBe(embedUrl(ID, window.location.origin));
        expect(frame.getAttribute("src")).not.toContain("autoplay");
        expect(frame.getAttribute("allow")).toBe(EMBED_ALLOW);
        expect(EMBED_ALLOW).toBe("autoplay; encrypted-media; picture-in-picture");
        expect(frame.getAttribute("referrerpolicy"))
            .toBe("strict-origin-when-cross-origin");
        expect(frame.title).toBe("Preview");
        // No script of YouTube's in the page.
        expect(document.scripts.length).toBe(0);
    });

    test("is told the page listens, at YouTube's origin only, until it answers",
         () => {
        jest.useFakeTimers();
        const {sent, say} = mount();
        jest.advanceTimersByTime(LISTEN_MS);
        jest.advanceTimersByTime(LISTEN_MS);
        expect(sent()).toEqual([
            [{event: "listening", id: 1, channel: "widget"}, EMBED_HOST],
            [{event: "listening", id: 1, channel: "widget"}, EMBED_HOST]]);
        say({event: "initialDelivery", info: {playerState: UNSTARTED}});
        jest.advanceTimersByTime(LISTEN_MS * 8);
        expect(sent()).toHaveLength(2);
    });

    test("is heard only from YouTube's origin and the frame itself, in its form",
         () => {
        jest.useFakeTimers();
        const {told, sent, say} = mount();
        const other = document.createElement("iframe");
        document.body.appendChild(other);
        say({event: "onReady"}, {origin: "https://evil.example"});
        say({event: "onReady"}, {origin: "https://www.youtube.com"});
        say({event: "onReady"}, {source: window});
        say({event: "onReady"}, {source: other.contentWindow});
        say({event: "onReady"}, {source: null});
        for (const junk of ["not json", "[]", "null", "42", '"onReady"',
                            '{"info": 1}', '{"event": 1}']) {
            say(junk);
        }
        expect(told.ready).toEqual([]);
        // None was taken for an answer: the page still listens.
        jest.advanceTimersByTime(LISTEN_MS);
        expect(sent().at(-1)[0].event).toBe("listening");
        say({event: "onReady"});
        expect(told.ready).toHaveLength(1);
    });

    test("ready, is driven by commands to YouTube's origin", () => {
        const {told, sent, commands, say} = mount();
        say({event: "onReady", info: null});
        expect(commands()).toEqual([["addEventListener", "onStateChange"],
                                    ["addEventListener", "onError"]]);
        expect(told.ready).toHaveLength(1);
        expect(told.time).toEqual([0]);
        const player = told.ready[0];

        // Not started: cued where asked, which is where it is.
        player.seek(90);
        expect(commands().at(-1))
            .toEqual(["cueVideoById", {videoId: ID, startSeconds: 90}]);
        expect(player.currentTime()).toBe(90);
        expect(player.paused()).toBe(true);
        player.play();
        expect(commands().at(-1)).toEqual(["playVideo"]);

        say({event: "onStateChange", info: PLAYING});
        expect(told.playing).toEqual([true]);
        expect(player.paused()).toBe(false);
        say({event: "infoDelivery",
             info: {currentTime: 95, duration: 3600, playerState: PLAYING}});
        expect(told.time.at(-1)).toBe(95);
        expect(told.duration.at(-1)).toBe(3600);
        expect(player.currentTime()).toBe(95);

        // Started: sought, and there until the frame says otherwise.
        player.seek(120);
        expect(commands().at(-1)).toEqual(["seekTo", 120, true]);
        expect(player.currentTime()).toBe(120);
        player.pause();
        expect(commands().at(-1)).toEqual(["pauseVideo"]);
        say({event: "infoDelivery", info: {playerState: PAUSED}});
        expect(told.playing).toEqual([true, false]);

        expect(sent().every(([message, target]) => target === EMBED_HOST
            && message.channel === "widget")).toBe(true);
    });

    test("delivers its position itself: nothing is polled, nothing odd taken",
         () => {
        jest.useFakeTimers();
        const {told, say} = mount();
        say({event: "onReady"});
        // Listening and the wait for it are over: no timer is left.
        expect(jest.getTimerCount()).toBe(0);
        say({event: "infoDelivery",
             info: {currentTime: 10, duration: 3600, playerState: PLAYING}});
        expect(told.time.at(-1)).toBe(10);
        say({event: "infoDelivery", info: {currentTime: 10.5}});
        expect(told.time.at(-1)).toBe(10.5);
        const player = told.ready[0];
        for (const info of [{currentTime: "11"}, {currentTime: -1},
                            {currentTime: null}, {duration: 0},
                            {duration: "long"}, {playerState: 7},
                            {playerState: "1"}]) {
            say({event: "infoDelivery", info});
        }
        say({event: "infoDelivery", info: "bad"});
        say({event: "onStateChange", info: "playing"});
        expect(player.currentTime()).toBe(10.5);
        expect(told.duration.at(-1)).toBe(3600);
        expect(player.paused()).toBe(false);
        expect(told.playing).toEqual([true]);
        expect(jest.getTimerCount()).toBe(0);
    });

    test.each([2, 5, 100, 101, 150, 153])(
        "its error %p says it cannot play", (code) => {
        const {told, say} = mount();
        say({event: "onReady"});
        say({event: "onStateChange", info: PLAYING});
        say({event: "onError", info: code});
        expect(told.failed).toBe(1);
        expect(told.playing.at(-1)).toBe(false);
    });

    test("that does not answer in time cannot play", () => {
        jest.useFakeTimers();
        const {told} = mount();
        jest.advanceTimersByTime(READY_TIMEOUT_MS - 1);
        expect(told.failed).toBe(0);
        jest.advanceTimersByTime(1);
        expect(told.failed).toBe(1);
        expect(jest.getTimerCount()).toBe(0);
    });

    test("that answers but is never ready in time cannot play", () => {
        jest.useFakeTimers();
        const {told, say} = mount();
        say({event: "initialDelivery", info: {playerState: UNSTARTED}});
        jest.advanceTimersByTime(READY_TIMEOUT_MS);
        expect(told.ready).toEqual([]);
        expect(told.failed).toBe(1);
    });

    test("that failed for want of an answer ignores a late ready", () => {
        jest.useFakeTimers();
        const {element, told, commands, say} = mount();
        jest.advanceTimersByTime(READY_TIMEOUT_MS);
        expect(told.failed).toBe(1);
        // Its frame is taken down: nothing reads on behind the fallback.
        expect(element.querySelector("iframe")).toBeNull();
        say({event: "onReady"});
        expect(told.ready).toEqual([]);
        expect(commands()).toEqual([]);
        expect(told.failed).toBe(1);
    });

    test("that failed by its error ignores what comes after", () => {
        const {told, commands, say} = mount();
        say({event: "onError", info: 150});
        expect(told.failed).toBe(1);
        say({event: "onReady"});
        say({event: "infoDelivery",
             info: {currentTime: 5, duration: 60, playerState: PLAYING}});
        say({event: "onStateChange", info: PLAYING});
        say({event: "onError", info: 150});
        expect(told).toEqual({ready: [], time: [], duration: [], playing: [false],
                              failed: 1});
        expect(commands()).toEqual([]);
    });

    test("that failed can be taken down again", () => {
        const {element, unmount, say} = mount();
        say({event: "onError", info: 150});
        expect(() => {
            unmount();
            unmount();
        }).not.toThrow();
        expect(element.childNodes.length).toBe(0);
    });

    test("taken down, is gone and tells nothing", () => {
        jest.useFakeTimers();
        const {element, told, unmount, say} = mount();
        unmount();
        expect(element.childNodes.length).toBe(0);
        expect(jest.getTimerCount()).toBe(0);
        say({event: "onReady"});
        jest.advanceTimersByTime(READY_TIMEOUT_MS);
        expect(told).toEqual({ready: [], time: [], duration: [], playing: [],
                              failed: 0});
    });
});

test("a message of the frame's is an object naming its event", () => {
    expect(readMessage('{"event":"onReady","info":null}'))
        .toEqual({event: "onReady", info: null});
    expect(readMessage({event: "onError", info: 150}))
        .toEqual({event: "onError", info: 150});
    for (const junk of ["{", "[]", "null", "1", '"x"', '{"event":2}', null,
                        undefined, 3, [], {info: 1}]) {
        expect(readMessage(junk)).toBeNull();
    }
});

describe("the frame's player as the page's", () => {
    // The player in the frame, its state set by the test.
    const player = (state, time = 0) => ({
        state, time, calls: [],
        playVideo() {
            this.calls.push(["playVideo"]);
        },
        pauseVideo() {
            this.calls.push(["pauseVideo"]);
        },
        seekTo(t, ahead) {
            this.calls.push(["seekTo", t, ahead]);
            this.time = t;
        },
        cueVideoById(options) {
            this.calls.push(["cueVideoById", options]);
        },
        getCurrentTime() {
            return this.time;
        },
        getDuration() {
            return 0;
        },
        getPlayerState() {
            return this.state;
        },
        destroy() {},
    });

    test("plays, pauses and says whether it plays", () => {
        const made = player(PAUSED);
        const page = youtubePlayer(made, ID);
        page.play();
        page.pause();
        expect(made.calls).toEqual([["playVideo"], ["pauseVideo"]]);
        for (const [state, paused] of [[UNSTARTED, true], [ENDED, true],
                                        [PLAYING, false], [PAUSED, true],
                                        [BUFFERING, false], [CUED, true]]) {
            made.state = state;
            expect(page.paused()).toBe(paused);
        }
    });

    test("seeks a started video and cues one not started, which it does not play",
         () => {
        const made = player(PAUSED, 30);
        const page = youtubePlayer(made, ID);
        page.seek(90);
        expect(made.calls).toEqual([["seekTo", 90, true]]);
        expect(page.currentTime()).toBe(90);
        made.state = PLAYING;
        page.seek(120);
        expect(made.calls[1]).toEqual(["seekTo", 120, true]);

        for (const state of [UNSTARTED, CUED, ENDED]) {
            const fresh = player(state);
            const cued = youtubePlayer(fresh, ID);
            cued.seek(290.5);
            expect(fresh.calls).toEqual([
                ["cueVideoById", {videoId: ID, startSeconds: 290.5}]]);
            // The player says 0 until it plays: the cued time is where it is.
            expect(cued.currentTime()).toBe(290.5);
            fresh.state = PLAYING;
            fresh.time = 291;
            expect(cued.currentTime()).toBe(291);
            fresh.state = PAUSED;
            expect(cued.currentTime()).toBe(291);
        }
    });

    test("says no time it cannot", () => {
        expect(youtubePlayer(player(PAUSED, NaN), ID).currentTime()).toBeNull();
    });
});
