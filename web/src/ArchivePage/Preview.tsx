import React from "react";
import {Box, Button, IconButton, Tooltip, Typography} from "@mui/material";
import PauseIcon from "@mui/icons-material/Pause";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SkipNextIcon from "@mui/icons-material/SkipNext";
import SkipPreviousIcon from "@mui/icons-material/SkipPrevious";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {DARK_TOKENS as tokens} from "../themes";
import {SetRow, formatClock} from "./archiveSets";
import {
    PlaybackProblem,
    browserPageUrl,
    canPlayH264,
    cssUrl,
    framePath,
    playbackProblem,
    releaseVideo,
    videoPath,
} from "./archivePreview";

// The archive page's preview (ADR-006 A3, the mock's centre): a plain
// <video> of the job's own video (GET /archive/jobs/<id>/video, ranges for
// seeking), the transport and the keys it answers. A video it cannot play is
// told apart (gone / refused / a format this window cannot decode) and an
// H.264 one -- which Autona's window (QtWebEngine) cannot decode -- offers
// this page in this PC's browser (read-only there: the writes are the
// desktop window's). Leaving it lets go of the video's request.

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

export default function Preview({jobId, extension, row, videoRef, time,
                                 duration, playing, editable, onTogglePlay,
                                 onPrevious, onNext, onTime, onDuration,
                                 onPlaying}: {
    jobId: string;
    // The video's extension (".flv"), or null: named when the preview
    // cannot play its format.
    extension: string | null;
    row: SetRow | null;
    videoRef: React.RefObject<HTMLVideoElement | null>;
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
    const src = `${BASE_URL}${videoPath(jobId)}`;
    const live = React.useRef(true);
    React.useEffect(() => {
        live.current = true;
        // The element of this mount: React clears the ref before this
        // cleanup runs. The page keys the preview by job, so it is unmounted
        // (not given another src) when the job changes or runs again.
        const video = videoRef.current;
        return () => {
            live.current = false;
            releaseVideo(video);
        };
    }, [videoRef]);

    const onError = async () => {
        const errorCode = videoRef.current?.error?.code ?? null;
        const status = await probeStatus(src);
        if (live.current) {
            setProblem(playbackProblem(errorCode, status,
                                       canPlayH264(videoRef.current)));
        }
    };
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
    return (
        <Box component="section" aria-label={i18n.t("archive_preview")}
             sx={{display: "flex", flexDirection: "column", minHeight: 0,
                  minWidth: 0, bgcolor: "#000"}}>
            <Box sx={{flex: 1, minHeight: 0, position: "relative",
                      display: "flex", alignItems: "center",
                      justifyContent: "center"}}>
                <video ref={videoRef} src={src} controls preload="metadata"
                       data-testid="archive-video"
                       onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
                       onSeeked={(e) => onTime(e.currentTarget.currentTime)}
                       onLoadedMetadata={(e) => {
                           setProblem(null);
                           onDuration(e.currentTarget.duration);
                       }}
                       onPlay={() => onPlaying(true)}
                       onPause={() => onPlaying(false)}
                       onError={onError}
                       style={{width: "100%", height: "100%",
                               objectFit: "contain",
                               display: problem ? "none" : "block"}}/>
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
