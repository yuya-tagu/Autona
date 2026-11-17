import React from "react";
import {Box, Button, IconButton, Link, Tooltip, Typography} from "@mui/material";
import PauseIcon from "@mui/icons-material/Pause";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SkipNextIcon from "@mui/icons-material/SkipNext";
import SkipPreviousIcon from "@mui/icons-material/SkipPrevious";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {DARK_TOKENS as tokens} from "../themes";
import {SetRow, formatClock, youtubeLink} from "./archiveSets";
import {
    PlaybackProblem,
    PreviewPlayer,
    browserPageUrl,
    canPlayH264,
    cssUrl,
    framePath,
    playbackProblem,
    releaseVideo,
    videoPath,
    videoPlayer,
} from "./archivePreview";
import {mountYouTube} from "./archiveEmbed";

// The archive page's preview (ADR-006 A3, the mock's centre), the transport
// and the keys it answers. The page drives it through `playerRef`
// (archivePreview.PreviewPlayer), whichever player it is:
//
// - a YouTube job's video (its id known) in YouTube's embedded player (D7:
//   the worker downloads H.264, which Autona's window, QtWebEngine, cannot
//   decode). One it cannot play (embedding not allowed, gone, the frame not
//   answering) is given up for the job's own downloaded video (below), from
//   the current time, whose problems also offer the video on YouTube; the
//   transport links there whenever;
// - any other job's own video, a plain <video> (GET /archive/jobs/<id>/video,
//   ranges for seeking). A video it cannot play is told apart (gone /
//   refused / a format this window cannot decode) and an H.264 one offers
//   this page in this PC's browser (read-only there: the writes are the
//   desktop window's). Leaving it lets go of the video's request.

const PROBE_TIMEOUT_MS = 10000;

// The video URL's status after an error (null when it could not be asked).
async function probeStatus(url: string): Promise<number | null> {
    if (typeof fetch !== "function") {
        return null;
    }
    try {
        const timeout = typeof AbortSignal?.timeout === "function"
            ? {signal: AbortSignal.timeout(PROBE_TIMEOUT_MS)} : {};
        const response = await fetch(url, {method: "HEAD", ...timeout});
        return response.status;
    } catch {
        return null;
    }
}

function Kbd({children}: {children: React.ReactNode}) {
    return (
        <Box component="kbd"
             sx={{px: 0.5, mr: 0.5, border: "1px solid", borderRadius: "3px",
                  borderColor: tokens.borderDefault, fontSize: "0.68rem",
                  fontFamily: "inherit", color: tokens.textSecondary}}>
            {children}
        </Box>
    );
}

interface SurfaceProps {
    playerRef: React.MutableRefObject<PreviewPlayer | null>;
    // A problem is shown over it.
    hidden: boolean;
    onTime: (t: number) => void;
    onDuration: (d: number) => void;
    onPlaying: (playing: boolean) => void;
    onProblem: (problem: PlaybackProblem | null) => void;
}

// The job's own video.
function VideoSurface({src, startAt, playerRef, hidden, onTime, onDuration,
                       onPlaying, onProblem}: SurfaceProps & {
    src: string;
    // Where it starts (the page's time when this one took over from YouTube's
    // player); read at mount only.
    startAt: number;
}) {
    const videoRef = React.useRef<HTMLVideoElement>(null);
    const live = React.useRef(true);
    const start = React.useRef(startAt);
    React.useEffect(() => {
        live.current = true;
        // The element of this mount: React clears the ref before this
        // cleanup runs. The page keys the preview by job, so it is unmounted
        // (not given another src) when the job changes or runs again.
        const video = videoRef.current;
        const player = video === null ? null : videoPlayer(video);
        if (video !== null && start.current > 0) {
            try {
                video.currentTime = start.current;
            } catch {
                // A media element that cannot seek yet: it starts at 0.
            }
        }
        playerRef.current = player;
        return () => {
            live.current = false;
            if (playerRef.current === player) {
                playerRef.current = null;
            }
            releaseVideo(video);
        };
    }, [playerRef]);

    const onError = async () => {
        const errorCode = videoRef.current?.error?.code ?? null;
        const status = await probeStatus(src);
        if (live.current) {
            onProblem(playbackProblem(errorCode, status,
                                      canPlayH264(videoRef.current)));
        }
    };
    return (
        <video ref={videoRef} src={src} controls preload="metadata"
               data-testid="archive-video"
               onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
               onSeeked={(e) => onTime(e.currentTarget.currentTime)}
               onLoadedMetadata={(e) => {
                   onProblem(null);
                   onDuration(e.currentTarget.duration);
               }}
               onPlay={() => onPlaying(true)}
               onPause={() => onPlaying(false)}
               onError={onError}
               style={{width: "100%", height: "100%", objectFit: "contain",
                       display: hidden ? "none" : "block"}}/>
    );
}

// A YouTube job's video in YouTube's embedded player (archiveEmbed: its frame,
// spoken to over postMessage; no script of YouTube's runs in the page).
function YouTubeSurface({videoId, time, playerRef, hidden, onTime,
                         onDuration, onPlaying, onEmbedFailed}:
                        SurfaceProps & {
    videoId: string;
    // The embedded player cannot play it: the page gives it up.
    onEmbedFailed: () => void;
    // Where the page is: a set picked before the player was ready is where
    // it starts.
    time: number;
}) {
    const host = React.useRef<HTMLDivElement>(null);
    // The latest props: the player's events outlive a render.
    const latest = React.useRef({time, onTime, onDuration, onPlaying,
                                 onEmbedFailed});
    latest.current = {time, onTime, onDuration, onPlaying, onEmbedFailed};
    React.useEffect(() => {
        const element = host.current;
        if (element === null) {
            return undefined;
        }
        let mine: PreviewPlayer | null = null;
        const unmount = mountYouTube(element, videoId, {
            ready: (player) => {
                mine = player;
                playerRef.current = player;
                if (latest.current.time > 0) {
                    player.seek(latest.current.time);
                }
            },
            time: (t) => latest.current.onTime(t),
            duration: (d) => latest.current.onDuration(d),
            playing: (playing) => latest.current.onPlaying(playing),
            failed: () => latest.current.onEmbedFailed(),
        }, i18n.t("archive_preview"));
        return () => {
            if (mine !== null && playerRef.current === mine) {
                playerRef.current = null;
            }
            unmount();
        };
    }, [videoId, playerRef]);
    // A click in the player gives its frame the keyboard, and the page's
    // keys (Space / Up / Down / N / I / O / Enter) would stop there: the
    // focus comes back to the preview (in the page's keys' frame), the click
    // still reaches the player.
    React.useEffect(() => {
        const onBlur = () => {
            window.setTimeout(() => {
                const element = host.current;
                const holder = document.activeElement;
                if (element !== null && holder instanceof HTMLIFrameElement
                    && element.contains(holder)) {
                    window.focus();
                    element.focus({preventScroll: true});
                }
            }, 0);
        };
        window.addEventListener("blur", onBlur);
        return () => window.removeEventListener("blur", onBlur);
    }, []);
    return (
        <Box ref={host} tabIndex={-1} data-testid="archive-youtube"
             sx={{width: "100%", height: "100%", outline: "none",
                  "& iframe": {display: "block", width: "100%",
                               height: "100%", border: 0}}}
             style={{display: hidden ? "none" : "block"}}/>
    );
}

export default function Preview({jobId, extension, row, source, youtubeId,
                                 playerRef, time, duration, playing, editable,
                                 onTogglePlay, onPrevious, onNext, onTime,
                                 onDuration, onPlaying}: {
    jobId: string;
    // The video's extension (".flv"), or null: named when the preview
    // cannot play its format.
    extension: string | null;
    row: SetRow | null;
    // The job's source, and its YouTube video's id when it has one
    // (archivePreview.youtubeEmbedId): played in YouTube's embedded player.
    source: string;
    youtubeId: string | null;
    playerRef: React.MutableRefObject<PreviewPlayer | null>;
    time: number;
    duration: number;
    playing: boolean;
    // The review can be edited here (A4): I / O / Enter are hinted.
    editable: boolean;
    onTogglePlay: () => void;
    onPrevious: () => void;
    onNext: () => void;
    onTime: (t: number) => void;
    onDuration: (d: number) => void;
    onPlaying: (playing: boolean) => void;
}) {
    const [problem, setProblem] = React.useState<PlaybackProblem | null>(null);
    // The YouTube video whose embedded player gave up: the job's own video
    // plays instead (so it is reset when the video changes).
    const [gaveUp, setGaveUp] = React.useState<string | null>(null);
    const embedGivenUp = youtubeId !== null && gaveUp === youtubeId;
    // A problem belongs to the surface that told it.
    React.useEffect(() => {
        setProblem(null);
    }, [youtubeId, embedGivenUp]);
    // The video on YouTube at the current time (the picked set's start when
    // it was just picked).
    const watchUrl = youtubeId === null ? null
        : youtubeLink(source, youtubeId, time);
    const path = row?.thumb ? framePath(jobId, row.no) : null;
    const frame = path === null ? undefined : `${BASE_URL}${path}`;
    const messages: Record<PlaybackProblem, string> = {
        missing: i18n.t("archive_video_missing"),
        notPreviewable: extension
            ? i18n.t("archive_video_not_previewable", {ext: extension})
            : i18n.t("archive_video_not_previewable_format"),
        unreachable: i18n.t("archive_video_unreachable"),
        unplayable: i18n.t("archive_video_unplayable"),
        openInBrowser: i18n.t("archive_video_open_in_browser_body"),
    };
    const surface = {playerRef, hidden: problem !== null, onTime, onDuration,
                     onPlaying, onProblem: setProblem};
    return (
        <Box component="section" aria-label={i18n.t("archive_preview")}
             sx={{display: "flex", flexDirection: "column", minHeight: 0,
                  minWidth: 0, bgcolor: "#000"}}>
            <Box sx={{flex: 1, minHeight: 0, position: "relative",
                      display: "flex", alignItems: "center",
                      justifyContent: "center"}}>
                {youtubeId !== null && !embedGivenUp ? (
                    <YouTubeSurface videoId={youtubeId} time={time}
                                    onEmbedFailed={() => setGaveUp(youtubeId)}
                                    {...surface}/>
                ) : (
                    <VideoSurface src={`${BASE_URL}${videoPath(jobId)}`}
                                  startAt={embedGivenUp ? time : 0}
                                  {...surface}/>
                )}
                {problem ? (
                    <Box role="alert" data-testid="archive-video-problem"
                         sx={{position: "absolute", inset: 0, display: "flex",
                              flexDirection: "column", alignItems: "center",
                              justifyContent: "center", gap: 1.5, p: 3,
                              textAlign: "center",
                              backgroundImage: frame
                                  ? `linear-gradient(rgba(0,0,0,.7), rgba(0,0,0,.7)), ${cssUrl(frame)}`
                                  : "none",
                              backgroundSize: "cover",
                              backgroundPosition: "center"}}>
                        <Typography sx={{maxWidth: 520}}>
                            {messages[problem]}
                        </Typography>
                        {problem === "openInBrowser" ? (
                            <>
                                <Button variant="contained" component="a"
                                        href={browserPageUrl(BASE_URL, jobId)}
                                        target="_blank" rel="noreferrer">
                                    {i18n.t("archive_video_open_in_browser")}
                                </Button>
                                <Typography variant="caption"
                                            data-testid="archive-browser-note"
                                            sx={{maxWidth: 520,
                                                 color: tokens.textSecondary}}>
                                    {i18n.t("archive_browser_read_only")}
                                </Typography>
                            </>
                        ) : null}
                        {embedGivenUp && watchUrl !== null ? (
                            <Button variant="contained" component="a"
                                    href={watchUrl} target="_blank"
                                    rel="noreferrer">
                                {i18n.t("archive_youtube_open")}
                            </Button>
                        ) : null}
                    </Box>
                ) : null}
            </Box>
            <Box sx={{display: "flex", alignItems: "center", gap: 0.5,
                      height: 44, px: 1.5, flexShrink: 0,
                      bgcolor: tokens.bgPrimary, borderTop: "1px solid",
                      borderColor: "divider"}}>
                <Tooltip title={i18n.t("archive_preview_previous")}>
                    <IconButton size="small" onClick={onPrevious}
                                aria-label={i18n.t("archive_preview_previous")}>
                        <SkipPreviousIcon fontSize="small"/>
                    </IconButton>
                </Tooltip>
                <Tooltip title={i18n.t("archive_preview_play")}>
                    <span>
                        <IconButton size="small" onClick={onTogglePlay}
                                    disabled={problem !== null}
                                    aria-label={i18n.t("archive_preview_play")}
                                    sx={{bgcolor: tokens.primaryBtnBg,
                                         color: tokens.primaryBtnText,
                                         "&:hover": {bgcolor: tokens.primaryBtnHover}}}>
                            {playing ? <PauseIcon fontSize="small"/>
                                : <PlayArrowIcon fontSize="small"/>}
                        </IconButton>
                    </span>
                </Tooltip>
                <Tooltip title={i18n.t("archive_preview_next")}>
                    <IconButton size="small" onClick={onNext}
                                aria-label={i18n.t("archive_preview_next")}>
                        <SkipNextIcon fontSize="small"/>
                    </IconButton>
                </Tooltip>
                <Typography variant="body2" data-testid="archive-timecode"
                            sx={{ml: 1, fontVariantNumeric: "tabular-nums"}}>
                    {formatClock(time)}
                    <Box component="small" sx={{color: tokens.textMuted, ml: 0.5}}>
                        / {formatClock(duration)}
                    </Box>
                </Typography>
                {watchUrl !== null ? (
                    <Link href={watchUrl} target="_blank" rel="noreferrer"
                          underline="hover" variant="caption"
                          data-testid="archive-youtube-link"
                          sx={{ml: 1.5, whiteSpace: "nowrap"}}>
                        {i18n.t("archive_youtube_open_here")}
                    </Link>
                ) : null}
                <Box sx={{flex: 1}}/>
                <Box sx={{display: "flex", gap: 1.5, fontSize: "0.72rem",
                          color: tokens.textMuted, whiteSpace: "nowrap"}}
                     data-testid="archive-key-hints">
                    <span><Kbd>Space</Kbd>{i18n.t("archive_key_play")}</span>
                    <span><Kbd>↑</Kbd><Kbd>↓</Kbd>{i18n.t("archive_key_sets")}</span>
                    <span><Kbd>N</Kbd>{i18n.t("archive_key_review")}</span>
                    {editable ? (
                        <>
                            <span><Kbd>I</Kbd><Kbd>O</Kbd>{i18n.t("archive_key_in_out")}</span>
                            <span><Kbd>Enter</Kbd>{i18n.t("archive_key_confirm")}</span>
                        </>
                    ) : null}
                </Box>
            </Box>
        </Box>
    );
}
