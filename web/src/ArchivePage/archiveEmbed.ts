// YouTube's embedded player for a YouTube job's preview (ADR-006 D7): the
// worker downloads H.264, which Autona's window (QtWebEngine) cannot decode,
// so the page plays the video on YouTube instead -- in YouTube's own frame
// (youtube-nocookie.com, never playing by itself), driven as a <video> is
// (archivePreview.PreviewPlayer).
//
// No script of YouTube's runs in the page: its IFrame Player API would run in
// the shared view's main frame, whose fetches carry the shell's secret
// (state-and-webserver.md, the shared view). The page speaks to the frame
// itself, over postMessage, as that API does (enablejsapi=1): it says it is
// listening until the frame answers, sends commands
// ({event: "command", func, args}) to YouTube's origin only, and takes only
// messages from that origin and that frame, checked before they are used.
// The frame's messages are not a published contract: if YouTube changes
// them, the player never says it is ready (or errs), and the page offers the
// video on YouTube -- as for a video it will not embed (its error) or a frame
// that does not answer within READY_TIMEOUT_MS.

import {PreviewPlayer} from "./archivePreview";

export const EMBED_HOST = "https://www.youtube-nocookie.com";
// The frame and the player it loads in turn.
export const READY_TIMEOUT_MS = 15000;
// How often the page says it is listening until the frame answers.
export const LISTEN_MS = 250;
// What the frame is allowed: playing, protected media, picture-in-picture.
export const EMBED_ALLOW = "autoplay; encrypted-media; picture-in-picture";
// The embed refuses a page that sends no referrer (its error 153).
export const EMBED_REFERRER_POLICY = "strict-origin-when-cross-origin";

// YT.PlayerState.
export const UNSTARTED = -1;
export const ENDED = 0;
export const PLAYING = 1;
export const PAUSED = 2;
export const BUFFERING = 3;
export const CUED = 5;
const STATES = [UNSTARTED, ENDED, PLAYING, PAUSED, BUFFERING, CUED];

// The player in the frame, as the page drives it.
export interface YTPlayer {
    playVideo(): void;
    pauseVideo(): void;
    seekTo(seconds: number, allowSeekAhead: boolean): void;
    cueVideoById(options: {videoId: string; startSeconds?: number}): void;
    getCurrentTime(): number;
    getDuration(): number;
    getPlayerState(): number;
    destroy(): void;
}

// The frame's address for `videoId`, answering the page at `origin`.
export function embedUrl(videoId: string, origin: string): string {
    const query = new URLSearchParams({enablejsapi: "1", origin,
                                       playsinline: "1", rel: "0"});
    return `${EMBED_HOST}/embed/${encodeURIComponent(videoId)}?${query}`;
}

// A message of the frame's -- {event, info} -- or null for anything else.
export function readMessage(data: unknown): {event: string; info: unknown} | null {
    let value = data;
    if (typeof value === "string") {
        try {
            value = JSON.parse(value);
        } catch {
            return null;
        }
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        return null;
    }
    const {event, info} = value as {event?: unknown; info?: unknown};
    return typeof event === "string" ? {event, info} : null;
}

function finite(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function playerState(value: unknown): number | null {
    return typeof value === "number" && STATES.includes(value) ? value : null;
}

// The player in the frame as the page's. A video not playing yet (or ended)
// is cued at the time asked, not sought: seekTo plays a video that is not
// paused, and the preview plays only when asked. Until it plays, the cued
// time is its position (the player says 0).
export function youtubePlayer(player: YTPlayer, videoId: string): PreviewPlayer {
    let cuedAt: number | null = null;
    const started = () => {
        const state = player.getPlayerState();
        return state === PLAYING || state === PAUSED || state === BUFFERING;
    };
    return {
        play: () => player.playVideo(),
        pause: () => player.pauseVideo(),
        paused: () => {
            const state = player.getPlayerState();
            return state !== PLAYING && state !== BUFFERING;
        },
        seek: (t) => {
            if (started()) {
                cuedAt = null;
                player.seekTo(t, true);
            } else {
                cuedAt = t;
                player.cueVideoById({videoId, startSeconds: t});
            }
        },
        currentTime: () => {
            if (cuedAt !== null && !started()) {
                return cuedAt;
            }
            cuedAt = null;
            const t = player.getCurrentTime();
            return Number.isFinite(t) ? t : null;
        },
    };
}

export interface EmbedEvents {
    // The player can be driven (its onReady).
    ready: (player: PreviewPlayer) => void;
    time: (t: number) => void;
    duration: (d: number) => void;
    playing: (playing: boolean) => void;
    // It cannot play the video (its onError, or no answer in time).
    failed: () => void;
}

// YouTube's player for `videoId` in `element` (its frame, titled `title`)
// -> what takes it down again. Nothing is told after that. The position is
// the frame's own (its infoDelivery while it plays): nothing is polled.
export function mountYouTube(element: HTMLElement, videoId: string,
                             events: EmbedEvents, title = ""): () => void {
    let live = true;
    let answered = false;
    let state = UNSTARTED;
    let time = 0;
    let duration = 0;
    let playback: PreviewPlayer | null = null;
    const frame = document.createElement("iframe");
    frame.setAttribute("allow", EMBED_ALLOW);
    frame.setAttribute("referrerpolicy", EMBED_REFERRER_POLICY);
    frame.title = title;
    frame.src = embedUrl(videoId, window.location.origin);

    // To YouTube's origin only: the frame may have been sent elsewhere.
    const send = (message: object) => {
        try {
            frame.contentWindow?.postMessage(
                JSON.stringify({...message, id: 1, channel: "widget"}),
                EMBED_HOST);
        } catch {
            // A frame being taken down.
        }
    };
    const command = (func: string, args: unknown[] = []) => {
        send({event: "command", func, args});
    };
    const listen = () => send({event: "listening"});
    let listening: number | null = null;
    let timeout: number | null = null;
    const stopListening = () => {
        if (listening !== null) {
            window.clearInterval(listening);
            listening = null;
        }
    };
    const stopWaiting = () => {
        if (timeout !== null) {
            window.clearTimeout(timeout);
            timeout = null;
        }
    };
    // Once failed, nothing the frame says is taken any more (a late onReady
    // would hand out a hidden player that plays sound only), and the frame is
    // taken down: the page falls back to the downloaded file.
    let failed = false;
    const fail = () => {
        if (!live || failed) {
            return;
        }
        failed = true;
        stopListening();
        stopWaiting();
        playback = null;
        frame.remove();
        events.playing(false);
        events.failed();
    };

    const player: YTPlayer = {
        playVideo: () => command("playVideo"),
        pauseVideo: () => command("pauseVideo"),
        seekTo: (seconds, ahead) => {
            // Where it is asked to be, until the frame tells otherwise.
            time = seconds;
            command("seekTo", [seconds, ahead]);
        },
        cueVideoById: (options) => command("cueVideoById", [options]),
        getCurrentTime: () => time,
        getDuration: () => duration,
        getPlayerState: () => state,
        destroy: () => frame.remove(),
    };
    const report = () => {
        if (playback === null) {
            return;
        }
        const t = playback.currentTime();
        if (t !== null) {
            events.time(t);
        }
        if (duration > 0) {
            events.duration(duration);
        }
    };
    const changeState = (next: number) => {
        const was = state === PLAYING || state === BUFFERING;
        state = next;
        const active = next === PLAYING || next === BUFFERING;
        if (playback !== null && active !== was) {
            events.playing(active);
        }
    };

    const onMessage = (event: MessageEvent) => {
        if (!live || failed || event.origin !== EMBED_HOST
            || event.source === null || event.source !== frame.contentWindow) {
            return;
        }
        const message = readMessage(event.data);
        if (message === null) {
            return;
        }
        if (!answered) {
            answered = true;
            stopListening();
        }
        const {event: name, info} = message;
        if (name === "onReady") {
            if (playback !== null) {
                return;
            }
            stopWaiting();
            command("addEventListener", ["onStateChange"]);
            command("addEventListener", ["onError"]);
            playback = youtubePlayer(player, videoId);
            events.ready(playback);
            report();
        } else if (name === "onError") {
            // Whatever the code (2 / 5 / 100 / 101 / 150 / 153 ...): it does
            // not play here.
            fail();
        } else if (name === "onStateChange") {
            const next = playerState(info);
            if (next !== null) {
                changeState(next);
                report();
            }
        } else if (name === "infoDelivery" || name === "initialDelivery") {
            if (typeof info !== "object" || info === null) {
                return;
            }
            const given = info as {currentTime?: unknown; duration?: unknown;
                                   playerState?: unknown};
            const next = playerState(given.playerState);
            if (next !== null) {
                changeState(next);
            }
            const t = finite(given.currentTime);
            if (t !== null && t >= 0) {
                time = t;
            }
            const d = finite(given.duration);
            if (d !== null && d > 0) {
                duration = d;
            }
            report();
        }
    };
    window.addEventListener("message", onMessage);
    frame.addEventListener("load", () => {
        if (live && !answered) {
            listen();
        }
    });
    element.appendChild(frame);
    listening = window.setInterval(listen, LISTEN_MS);
    timeout = window.setTimeout(fail, READY_TIMEOUT_MS);
    listen();

    return () => {
        live = false;
        stopListening();
        stopWaiting();
        window.removeEventListener("message", onMessage);
        playback = null;
        player.destroy();
        while (element.firstChild) {
            element.removeChild(element.firstChild);
        }
    };
}
