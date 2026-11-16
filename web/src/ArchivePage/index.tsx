import React from "react";
import {
    Alert,
    Box,
    Button,
    Chip,
    FormControl,
    FormControlLabel,
    InputLabel,
    LinearProgress,
    Link,
    MenuItem,
    Radio,
    RadioGroup,
    Select,
    Stack,
    TextField,
    ToggleButton,
    ToggleButtonGroup,
    Tooltip,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {DARK_TOKENS as tokens} from "../themes";
import {apiGetJson, apiPatch, apiPost, ApiError} from "../api";
import {FeedbackProvider} from "../ScoreboardPage/Feedback";
import {
    ArchiveStatus,
    Job,
    JobSummary,
    SetFilter,
    SetRow,
    SetsJson,
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
} from "./archiveSets";
import {
    framePath,
    jobFromQuery,
    keyAction,
    keyTargetKind,
    nextReviewSet,
    setNumber,
    stepSet,
    videoExtension,
} from "./archivePreview";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {
    TimelineView,
    bestShare,
    gameNames,
    timelineRange,
    videoLength,
} from "./archiveTimeline";
import Preview from "./Preview";
import Timeline from "./Timeline";

// The Archive Clipper page (ADR-006 A1), /web/app/archive: starts a worker
// job on a VOD, shows its progress and the sets it found. State is the
// page's own (no Redux / StateManager, ADR-006 D6); a running job is polled
// every second through GET /api/v1/archive/jobs/<id>. A job with sets shows
// the mock's review screen (A3): the set list, the preview of the job's own
// video, the set's details (read-only) and the timeline. ?job=<id> opens a
// job (the preview's "open in browser"). The writes are SHELL routes (D7):
// outside the desktop's own window (this PC's browser) the page only reads,
// and says so.

const BASE = "/api/v1/archive";
const POLL_MS = 1000;
const LIST_WIDTH = 320;
const DETAIL_WIDTH = 300;

interface Detail {
    job: Job;
    sets: SetsJson | null;
}

// The server's own (Japanese) refusal message when it sent one.
function messageOf(error: unknown): string {
    return (error as ApiError | null)?.detail
        ?? i18n.t("archive_request_failed");
}

async function getData<T>(path: string): Promise<T> {
    return (await apiGetJson<{data: T}>(path)).data;
}

async function postJob(path: string, body: unknown): Promise<Job> {
    const text = await apiPost(path, body);
    return (JSON.parse(text) as {data: {job: Job}}).data.job;
}

function Unavailable({status}: {status: ArchiveStatus | null}) {
    return (
        <Box sx={{p: 3, maxWidth: 640}}>
            <Alert severity="info">
                <Typography fontWeight={700}>
                    {i18n.t("archive_unavailable_title")}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("archive_unavailable_body")}
                </Typography>
                {status?.reason ? (
                    <Typography variant="body2" sx={{mt: 1}}>
                        {i18n.t("archive_unavailable_reason",
                                {reason: status.reason})}
                    </Typography>
                ) : null}
            </Alert>
        </Box>
    );
}

function TimeLink({source, sourceId, seconds}: {source?: string | null;
                                                sourceId?: string | null;
                                                seconds: number}) {
    const text = formatClock(seconds);
    const href = youtubeLink(source, sourceId, seconds);
    return href === null ? <span>{text}</span> : (
        <Link href={href} target="_blank" rel="noreferrer" underline="hover">
            {text}
        </Link>
    );
}

function SetListRow({row, jobId, source, sourceId, selected, onSelect}: {
    row: SetRow;
    jobId: string;
    source?: string | null;
    sourceId?: string | null;
    selected: boolean;
    onSelect: () => void;
}) {
    const [noFrame, setNoFrame] = React.useState(false);
    const text = rowText(row, i18n.t("archive_no_match"));
    const score = scoreText(row);
    const path = row.thumb && !noFrame ? framePath(jobId, row.no) : null;
    return (
        // Enter picks the row (a button); Space is the page's (play / pause).
        <Box role="button" tabIndex={0} onClick={onSelect}
             onKeyDown={(event) => {
                 if (event.key === "Enter") {
                     event.preventDefault();
                     onSelect();
                 }
             }}
             data-testid={`archive-set-${row.no}`}
             data-archive-set={row.no}
             aria-current={selected || undefined}
             sx={{display: "flex", gap: 1, p: 1, cursor: "pointer",
                  borderBottom: "1px solid", borderColor: "divider",
                  bgcolor: selected ? tokens.bgTertiary : "transparent",
                  "&:hover": {bgcolor: tokens.bgTertiary}}}>
            {/* The set's background frame (--thumbs), else a box. */}
            {path !== null ? (
                <Box component="img" alt="" loading="lazy"
                     src={`${BASE_URL}${path}`}
                     data-testid={`archive-frame-${row.no}`}
                     onError={() => setNoFrame(true)}
                     sx={{width: 48, height: 27, flexShrink: 0,
                          objectFit: "cover", borderRadius: "2px"}}/>
            ) : (
                <Box data-testid={`archive-frame-none-${row.no}`}
                     sx={{width: 48, height: 27, flexShrink: 0,
                          bgcolor: tokens.bgQuaternary, borderRadius: "2px"}}/>
            )}
            <Box sx={{minWidth: 0, flex: 1}}>
                <Tooltip title={text} enterDelay={600}>
                    <Typography variant="body2"
                                sx={{display: "-webkit-box",
                                     WebkitLineClamp: 2,
                                     WebkitBoxOrient: "vertical",
                                     overflow: "hidden",
                                     wordBreak: "break-all"}}>
                        {text}
                    </Typography>
                </Tooltip>
                <Typography variant="caption" component="div"
                            sx={{color: tokens.textMuted}}>
                    #{row.no} · <TimeLink source={source} sourceId={sourceId}
                                          seconds={row.start}/>
                    {" · "}{formatDuration(row.end - row.start)}
                    {score ? ` · ${score}` : ""}
                    {row.needs_review ? (
                        <Tooltip title={row.reason ?? ""}>
                            <Box component="span"
                                 sx={{color: "error.main", fontWeight: 700,
                                      ml: 1}}>
                                {i18n.t("archive_review_badge")}
                            </Box>
                        </Tooltip>
                    ) : null}
                </Typography>
            </Box>
        </Box>
    );
}

function SetList({sets, rows, jobId, source, filter, onFilter, selectedNo,
                  onSelect}: {
    sets: SetsJson;
    rows: SetRow[];
    jobId: string;
    source?: string | null;
    filter: SetFilter;
    onFilter: (filter: SetFilter) => void;
    selectedNo: number | null;
    onSelect: (no: number) => void;
}) {
    const unused = sets.unused_sets ?? [];
    return (
        <Box sx={{display: "flex", flexDirection: "column", minHeight: 0,
                  minWidth: 0, borderRight: "1px solid",
                  borderColor: "divider"}}>
            <ToggleButtonGroup exclusive size="small" value={filter}
                               onChange={(_e, next) => next && onFilter(next)}
                               sx={{p: 1}}>
                <ToggleButton value="all">
                    {i18n.t("archive_filter_all")}
                </ToggleButton>
                <ToggleButton value="matched">
                    {i18n.t("archive_filter_matched")}
                </ToggleButton>
                <ToggleButton value="review">
                    {i18n.t("archive_filter_review")}
                </ToggleButton>
            </ToggleButtonGroup>
            <Box sx={{flex: 1, minHeight: 0, overflowY: "auto"}}>
                {rows.length === 0 ? (
                    <Typography variant="body2"
                                sx={{p: 2, color: tokens.textMuted}}>
                        {i18n.t("archive_no_sets")}
                    </Typography>
                ) : rows.map((row) => (
                    <SetListRow key={row.no} row={row} jobId={jobId}
                                source={source} sourceId={sets.source_id}
                                selected={row.no === selectedNo}
                                onSelect={() => onSelect(row.no)}/>
                ))}
                {unused.length > 0 ? (
                    <Box component="details" sx={{p: 1}}>
                        <Box component="summary" sx={{cursor: "pointer"}}>
                            {i18n.t("archive_unused")} ({unused.length})
                        </Box>
                        {unused.map((set, index) => (
                            <Typography key={index} variant="caption"
                                        component="div"
                                        sx={{color: tokens.textMuted}}>
                                {unusedText(set)}
                            </Typography>
                        ))}
                    </Box>
                ) : null}
            </Box>
        </Box>
    );
}

// A line of the details that seeks the preview to `seconds` when clicked
// (the mock's game rows). Its time is the YouTube link as every time shown
// is (D6), which opens YouTube instead of seeking.
function SeekRow({seconds, source, sourceId, onSeek, columns, children,
                  testId}: {
    seconds: number;
    source?: string | null;
    sourceId?: string | null;
    onSeek: (t: number) => void;
    columns: string;
    children?: React.ReactNode;
    testId?: string;
}) {
    return (
        <Box onClick={() => onSeek(seconds)} title={i18n.t("archive_detail_seek")}
             data-testid={testId}
             sx={{display: "grid", gridTemplateColumns: columns, gap: 1,
                  alignItems: "center", py: 0.25, px: 0.5, mx: -0.5,
                  borderRadius: 1, cursor: "pointer",
                  fontVariantNumeric: "tabular-nums",
                  "&:hover": {bgcolor: tokens.bgTertiary}}}>
            <span onClick={(event) => {
                // A link opens YouTube; plain text (a local file) seeks.
                if ((event.target as HTMLElement).closest("a")) {
                    event.stopPropagation();
                }
            }}>
                <TimeLink source={source} sourceId={sourceId}
                          seconds={seconds}/>
            </span>
            {children}
        </Box>
    );
}

function Note({color, children}: {color: string; children: React.ReactNode}) {
    return (
        <Box sx={{display: "flex", gap: 1, p: 1, borderRadius: 1,
                  bgcolor: tokens.bgSecondary, fontSize: "0.8rem"}}>
            <Box component="i" sx={{flexShrink: 0, width: 8, height: 8, mt: 0.6,
                                    borderRadius: "50%", bgcolor: color}}/>
            <span>{children}</span>
        </Box>
    );
}

function Field({label, children}: {label: string; children: React.ReactNode}) {
    return (
        <Typography variant="caption" component="div"
                    sx={{color: tokens.textMuted, mt: 1}}>
            {label}
            <Box sx={{color: tokens.textPrimary, fontSize: "0.85rem", mt: 0.25}}>
                {children}
            </Box>
        </Typography>
    );
}

// The selected set, read-only (the mock's right pane without its edits:
// assigning a result and confirming are a later stage).
function SetDetails({row, sets, source, onSeek}: {
    row: SetRow | null;
    sets: SetsJson;
    source?: string | null;
    onSeek: (t: number) => void;
}) {
    if (row === null) {
        return (
            <Typography sx={{p: 2, color: tokens.textMuted}}>
                {i18n.t("archive_detail_pick")}
            </Typography>
        );
    }
    const result = row.set ?? null;
    const players = result?.p1 && result?.p2
        ? `${result.p1} vs ${result.p2}` : null;
    const heading = players ?? (row.needs_review
        ? i18n.t("archive_detail_review_block")
        : rowText(row, i18n.t("archive_no_match")));
    const sub = result
        ? [sets.event, result.round].filter(Boolean).join(" · ")
        : i18n.t("archive_no_match");
    const score = scoreText(row);
    const total = typeof result?.s1 === "number" && typeof result?.s2 === "number"
        ? result.s1 + result.s2 : null;
    const corrected = correctedText(row.corrected_games);
    const correctedGames = new Set(Array.isArray(row.corrected_games)
        ? row.corrected_games : []);
    const games = row.game_spans ?? [];
    return (
        <Stack spacing={0.5} sx={{p: 2}} data-testid="archive-details">
            <Typography variant="caption" sx={{color: tokens.textMuted}}>
                {i18n.t("archive_details")} #{row.no}
            </Typography>
            <Typography variant="subtitle1" fontWeight={700}
                        sx={{wordBreak: "break-word"}}>
                {heading}
            </Typography>
            {sub ? (
                <Typography variant="body2" sx={{color: tokens.textSecondary}}>
                    {sub}
                </Typography>
            ) : null}
            {row.title ? (
                <Field label={i18n.t("archive_detail_title")}>
                    <Box sx={{wordBreak: "break-word"}}>{row.title}</Box>
                </Field>
            ) : null}
            {corrected ? (
                <Note color={tokens.negative}>
                    {i18n.t("archive_detail_corrected")}: {corrected}
                </Note>
            ) : null}
            {row.reason ? (
                <Note color={tokens.gold}>
                    {i18n.t("archive_detail_reason")}: {row.reason}
                </Note>
            ) : null}
            <Field label={i18n.t("archive_detail_bounds")}>
                {([["IN", row.start], ["OUT", row.end]] as [string, number][])
                    .map(([edge, seconds]) => (
                        <SeekRow key={edge} seconds={seconds} source={source}
                                 sourceId={sets.source_id} onSeek={onSeek}
                                 columns="34px 1fr">
                            <Box component="span"
                                 sx={{order: -1, color: tokens.textMuted}}>
                                {edge}
                            </Box>
                        </SeekRow>
                    ))}
            </Field>
            <Field label={i18n.t("archive_detail_result")}>
                <Box component="dl" sx={{m: 0, display: "grid", gap: "2px 8px",
                                         gridTemplateColumns: "88px 1fr",
                                         "& dt": {color: tokens.textMuted},
                                         "& dd": {m: 0}}}>
                    {score ? (
                        <>
                            <dt>{i18n.t("archive_detail_score")}</dt>
                            <dd>{`${result?.p1 ?? ""} ${score} ${result?.p2 ?? ""}`.trim()}</dd>
                        </>
                    ) : null}
                    <dt>{i18n.t("archive_detail_games")}</dt>
                    <dd>
                        {row.games ?? games.length}
                        {total !== null ? (
                            <Box component="span"
                                 sx={{ml: 1, color: total === (row.games ?? games.length)
                                     ? tokens.positive : tokens.negative}}>
                                {total === (row.games ?? games.length)
                                    ? `✓ ${i18n.t("archive_detail_score_match")}`
                                    : `! ${i18n.t("archive_detail_score_mismatch")}`}
                            </Box>
                        ) : null}
                    </dd>
                    <dt>{i18n.t("archive_detail_name_match")}</dt>
                    <dd>{nameMatchText(row.name_match)}</dd>
                    <dt>{i18n.t("archive_detail_length")}</dt>
                    <dd>{formatDuration(row.end - row.start)}</dd>
                </Box>
            </Field>
            {games.length > 0 ? (
                <Field label={i18n.t("archive_detail_game_list")}>
                    {games.map((game, index) => {
                        const share = bestShare(gameNames(sets, game));
                        const start = Number(game?.[0]);
                        const end = Number(game?.[1]);
                        return (
                            <SeekRow key={index} seconds={start} source={source}
                                     sourceId={sets.source_id} onSeek={onSeek}
                                     columns="64px 12px 1fr 40px"
                                     testId={`archive-detail-game-${index + 1}`}>
                                <Box component="span"
                                     title={correctedGames.has(index + 1)
                                         ? i18n.t("archive_legend_corrected")
                                         : undefined}
                                     sx={{color: tokens.negative}}>
                                    {correctedGames.has(index + 1) ? "●" : ""}
                                </Box>
                                <Box title={`OCR ${Math.round(share * 100)}%`}
                                     sx={{height: 5, borderRadius: 3,
                                          bgcolor: tokens.bgQuaternary,
                                          overflow: "hidden"}}>
                                    <Box sx={{height: "100%",
                                              width: `${Math.round(share * 100)}%`,
                                              bgcolor: "#2a78d6"}}/>
                                </Box>
                                <Box component="span"
                                     sx={{color: tokens.textMuted,
                                          textAlign: "right"}}>
                                    {formatDuration(end - start)}
                                </Box>
                            </SeekRow>
                        );
                    })}
                </Field>
            ) : null}
        </Stack>
    );
}

// The review screen of a job with sets (ADR-006 A3): the mock's grid -- the
// set list, the preview and the details over the timeline. Its keys work
// within it, not while typing (archivePreview.keyAction).
function SetsView({job, sets, setNo, onSetNo}: {
    job: Job;
    sets: SetsJson;
    setNo: number | null;
    onSetNo: (no: number) => void;
}) {
    const [filter, setFilter] = React.useState<SetFilter>("all");
    const [view, setView] = React.useState<TimelineView>("all");
    const [time, setTime] = React.useState(0);
    const [duration, setDuration] = React.useState(0);
    const [playing, setPlaying] = React.useState(false);
    const videoRef = React.useRef<HTMLVideoElement>(null);
    const root = React.useRef<HTMLDivElement>(null);
    const rows = filterSets(sets.sets, filter);
    const row = sets.sets.find((r) => r.no === setNo) ?? null;

    // Opening the job hands the keys to it (Space / arrows / N), without
    // scrolling the page to it.
    React.useEffect(() => {
        root.current?.focus({preventScroll: true});
    }, []);
    // Keep the selected set's row in view (after a filter reset too). Only a
    // set number goes into the selector.
    React.useEffect(() => {
        const number = setNumber(setNo);
        if (number !== null) {
            root.current?.querySelector(`[data-archive-set="${number}"]`)
                ?.scrollIntoView?.({block: "nearest"});
        }
    }, [setNo, filter]);

    const seek = (t: number) => {
        const video = videoRef.current;
        if (video) {
            try {
                video.currentTime = t;
            } catch {
                // Not seekable yet: the time still shows where it was asked.
            }
        }
        setTime(t);
    };
    // Pick set `no` and play from `at` (its start by default). A set the
    // filter hides (picked on the timeline) shows all the sets again, so the
    // list shows what is selected.
    const choose = (no: number | null, at?: number) => {
        const next = sets.sets.find((r) => r.no === no);
        if (!next) {
            return;
        }
        if (!rows.some((r) => r.no === next.no)) {
            setFilter("all");
        }
        onSetNo(next.no);
        seek(at ?? next.start);
    };
    const togglePlay = () => {
        const video = videoRef.current;
        if (!video) {
            return;
        }
        try {
            if (video.paused) {
                video.play()?.catch(() => undefined);
            } else {
                video.pause();
            }
        } catch {
            // A video that cannot play: the preview says why.
        }
    };
    const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        const action = keyAction({
            key: event.key, ctrlKey: event.ctrlKey, altKey: event.altKey,
            metaKey: event.metaKey, defaultPrevented: event.defaultPrevented,
            isComposing: event.nativeEvent.isComposing,
        }, keyTargetKind(event.target));
        if (action === null) {
            return;
        }
        event.preventDefault();
        if (action === "togglePlay") {
            togglePlay();
        } else if (action === "previousSet") {
            choose(stepSet(rows, setNo, -1));
        } else if (action === "nextSet") {
            choose(stepSet(rows, setNo, 1));
        } else {
            choose(nextReviewSet(rows, setNo));
        }
    };
    const shownView = row === null ? "all" : view;
    return (
        <Box ref={root} tabIndex={-1} onKeyDown={onKeyDown}
             data-testid="archive-review"
             sx={{flex: 1, minHeight: 0, display: "grid", outline: "none",
                  gridTemplateColumns:
                      `${LIST_WIDTH}px minmax(0, 1fr) ${DETAIL_WIDTH}px`,
                  gridTemplateRows: "minmax(0, 1fr) auto"}}>
            <SetList sets={sets} rows={rows} jobId={job.id} source={job.source}
                     filter={filter} onFilter={setFilter} selectedNo={setNo}
                     onSelect={(no) => choose(no)}/>
            <Preview jobId={job.id}
                     extension={videoExtension(job.source, sets.source)}
                     row={row} videoRef={videoRef} time={time}
                     duration={Number.isFinite(duration) && duration > 0
                         ? duration : videoLength(sets)}
                     playing={playing} onTogglePlay={togglePlay}
                     onPrevious={() => choose(stepSet(rows, setNo, -1))}
                     onNext={() => choose(stepSet(rows, setNo, 1))}
                     onTime={setTime} onDuration={setDuration}
                     onPlaying={setPlaying}/>
            <Box sx={{overflowY: "auto", minHeight: 0, borderLeft: "1px solid",
                      borderColor: "divider"}}>
                <SetDetails row={row} sets={sets} source={job.source}
                            onSeek={seek}/>
            </Box>
            <Box sx={{gridColumn: "1 / -1", minWidth: 0}}>
                <Timeline sets={sets} rows={rows} selected={row}
                          view={shownView} onView={setView} time={time}
                          onSeek={seek} onPick={(no, t) => choose(no, t)}
                          range={timelineRange(sets, shownView, row)}/>
            </Box>
        </Box>
    );
}

function NewJobForm({status, readOnly, onStatus, onStarted, onFailed}: {
    status: ArchiveStatus;
    readOnly: boolean;
    onStatus: (status: ArchiveStatus) => void;
    onStarted: (job: Job) => void;
    onFailed: () => void;
}) {
    const [source, setSource] = React.useState("");
    const [event, setEvent] = React.useState("");
    const [mode, setMode] = React.useState<"none" | "json">("none");
    const [results, setResults] = React.useState<object | null>(null);
    const [fileError, setFileError] = React.useState("");
    const [error, setError] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [editing, setEditing] = React.useState(false);
    const [workDir, setWorkDir] = React.useState(status.work_dir ?? "");

    const onFile = async (file: File | undefined) => {
        setResults(null);
        setFileError("");
        if (!file) {
            return;
        }
        try {
            const parsed = JSON.parse(await file.text());
            if (typeof parsed !== "object" || parsed === null) {
                throw new Error("object expected");
            }
            setResults(parsed);
        } catch (e) {
            setFileError(i18n.t("archive_results_parse_error",
                                {message: (e as Error).message}));
        }
    };
    const saveWorkDir = async () => {
        try {
            const text = await apiPatch(`${BASE}/settings`,
                                        {work_dir: workDir});
            onStatus((JSON.parse(text) as {data: ArchiveStatus}).data);
            setEditing(false);
            setError("");
        } catch (e) {
            setError(messageOf(e));
        }
    };
    const start = async () => {
        setBusy(true);
        setError("");
        try {
            onStarted(await postJob(`${BASE}/jobs`, {
                source: source.trim(),
                results: mode === "json" ? results : null,
                event: event.trim() === "" ? null : event.trim(),
            }));
        } catch (e) {
            setError(messageOf(e));
            // A failed start may still have left a (failed) job folder.
            onFailed();
        } finally {
            setBusy(false);
        }
    };
    const ready = source.trim() !== ""
        && (mode === "none" || (results !== null && fileError === ""));
    return (
        <Stack spacing={2} sx={{p: 2, maxWidth: 720}}>
            <TextField size="small" fullWidth value={source}
                       label={i18n.t("archive_source")}
                       onChange={(e) => setSource(e.target.value)}/>
            <FormControl>
                <Typography variant="caption"
                            sx={{color: tokens.textMuted}}>
                    {i18n.t("archive_results")}
                </Typography>
                <RadioGroup row value={mode}
                            onChange={(e) =>
                                setMode(e.target.value as "none" | "json")}>
                    <FormControlLabel value="none" control={<Radio/>}
                                      label={i18n.t("archive_results_none")}/>
                    <FormControlLabel value="json" control={<Radio/>}
                                      label={i18n.t("archive_results_json")}/>
                </RadioGroup>
                {mode === "json" ? (
                    <input type="file" accept=".json,application/json"
                           aria-label={i18n.t("archive_results_pick")}
                           onChange={(e) => onFile(e.target.files?.[0])}/>
                ) : null}
                {fileError ? <Alert severity="error" sx={{mt: 1}}>
                    {fileError}
                </Alert> : null}
            </FormControl>
            <TextField size="small" fullWidth value={event}
                       label={i18n.t("archive_event")}
                       onChange={(e) => setEvent(e.target.value)}/>
            <Stack direction="row" spacing={1} alignItems="center">
                {editing ? (
                    <>
                        <TextField size="small" fullWidth value={workDir}
                                   label={i18n.t("archive_work_dir")}
                                   onChange={(e) => setWorkDir(e.target.value)}/>
                        <Button onClick={saveWorkDir} disabled={readOnly}>
                            {i18n.t("archive_save")}
                        </Button>
                    </>
                ) : (
                    <>
                        <Typography variant="body2" sx={{flex: 1,
                                    wordBreak: "break-all"}}>
                            {i18n.t("archive_work_dir")}: {status.work_dir}
                        </Typography>
                        <Button disabled={readOnly} onClick={() => {
                            setWorkDir(status.work_dir ?? "");
                            setEditing(true);
                        }}>
                            {i18n.t("archive_edit")}
                        </Button>
                    </>
                )}
            </Stack>
            {error ? <Alert severity="error">{error}</Alert> : null}
            <Box>
                <Button variant="contained" disabled={!ready || busy || readOnly}
                        onClick={start}>
                    {i18n.t("archive_start")}
                </Button>
            </Box>
        </Stack>
    );
}

function Progress({job, readOnly, onAct}: {
    job: Job;
    readOnly: boolean;
    onAct: (action: string) => void;
}) {
    const progress = job.progress;
    const eta = etaParts(progress?.eta);
    const logs = (job.logs ?? []).slice(-5);
    return (
        <Stack spacing={1} sx={{p: 2}}>
            <Typography>
                {progress
                    ? `[${progress.index ?? 0}/${progress.count ?? 0}] ${progress.label ?? ""}`
                    : i18n.t("archive_state_running")}
            </Typography>
            <LinearProgress variant="determinate"
                            value={Math.round((progress?.overall ?? 0) * 100)}/>
            {eta ? (
                <Typography variant="body2" sx={{color: tokens.textMuted}}>
                    {eta.min > 0
                        ? i18n.t("archive_eta_min", {
                            min: eta.min,
                            sec: String(eta.sec).padStart(2, "0")})
                        : i18n.t("archive_eta_sec", {sec: eta.sec})}
                </Typography>
            ) : null}
            {logs.length > 0 ? (
                <Box component="pre" aria-label={i18n.t("archive_logs")}
                     sx={{m: 0, fontSize: "0.75rem", whiteSpace: "pre-wrap",
                          color: tokens.textMuted}}>
                    {logs.join("\n")}
                </Box>
            ) : null}
            <Box>
                <Button color="error" variant="outlined" disabled={readOnly}
                        onClick={() => onAct("cancel")}>
                    {i18n.t("archive_abort")}
                </Button>
            </Box>
        </Stack>
    );
}

function Ended({job, readOnly, onAct}: {
    job: Job;
    readOnly: boolean;
    onAct: (action: string) => void;
}) {
    if (job.state === "done") {
        return null;
    }
    const error = job.error;
    const message = error?.message
        ?? (job.state === "cancelled" ? "" : i18n.t("archive_failed_unknown"));
    return (
        <Stack spacing={1} sx={{p: 2}}>
            {message ? (
                <Alert severity="error">
                    {message}
                    {error?.hint ? (
                        <Typography variant="body2">{error.hint}</Typography>
                    ) : null}
                </Alert>
            ) : null}
            <Box>
                <Button variant="outlined" disabled={readOnly}
                        onClick={() => onAct("resume")}>
                    {job.state === "error" ? i18n.t("archive_rerun")
                        : i18n.t("archive_resume")}
                </Button>
            </Box>
        </Stack>
    );
}

function jobLabel(job: JobSummary): string {
    return `${job.created_at.replace("T", " ").slice(0, 16)}  ${sourceName(job.source)}`;
}

function ArchiveContent() {
    const [status, setStatus] = React.useState<ArchiveStatus | null>(null);
    const [statusFailed, setStatusFailed] = React.useState(false);
    const [jobs, setJobs] = React.useState<JobSummary[]>([]);
    // ?job=<id> opens that job (the preview's "open in browser").
    const [selected, setSelected] = React.useState<string | "new">(
        () => jobFromQuery(window.location.search) ?? "new");
    const [detail, setDetail] = React.useState<Detail | null>(null);
    const [setNo, setSetNo] = React.useState<number | null>(null);
    const [error, setError] = React.useState("");
    const [pollFailed, setPollFailed] = React.useState(false);
    // Outside the desktop's window the writes (SHELL) are refused: only
    // reading here. Still checking or a failed probe leaves them on (the
    // server answers for itself).
    const {access} = useShellAccess({withToken: false});
    const readOnly = access === "elsewhere";
    // The id the view shows right now: an async result for any other job is
    // stale and dropped.
    const selectedRef = React.useRef<string | "new">(selected);

    const select = React.useCallback((id: string | "new") => {
        selectedRef.current = id;
        setError("");
        setPollFailed(false);
        setSelected(id);
    }, []);

    const loadJobs = React.useCallback(async () => {
        const data = await getData<{jobs: JobSummary[]}>(`${BASE}/jobs`);
        // Newest first (the list is shown in this order whatever the server's).
        setJobs([...data.jobs].sort((a, b) =>
            b.created_at.localeCompare(a.created_at)));
    }, []);

    React.useEffect(() => {
        let live = true;
        getData<ArchiveStatus>(`${BASE}/status`).then((data) => {
            if (live) {
                setStatus(data);
                if (data.available) {
                    loadJobs().catch(() => undefined);
                    // A reload while a job runs shows its progress.
                    if (data.running_job && selectedRef.current === "new") {
                        select(data.running_job);
                    }
                }
            }
        }, () => live && setStatusFailed(true));
        return () => {
            live = false;
        };
    }, [loadJobs, select]);

    // Null when the answer is for a job that is no longer the selected one.
    const loadDetail = React.useCallback(async (id: string) => {
        const data = await getData<Detail>(`${BASE}/jobs/${encodeURIComponent(id)}`);
        if (selectedRef.current !== id) {
            return null;
        }
        setDetail(data);
        return data;
    }, []);

    // Open a job: fetch it once; the poll below takes over while it runs.
    React.useEffect(() => {
        setDetail(null);
        setSetNo(null);
        if (selected === "new") {
            return undefined;
        }
        let live = true;
        loadDetail(selected).catch((e) => live && setError(messageOf(e)));
        return () => {
            live = false;
        };
    }, [selected, loadDetail]);

    const running = !pollFailed && detail?.job.state === "running"
        ? detail.job.id : null;
    React.useEffect(() => {
        if (running === null) {
            return undefined;
        }
        const timer = setInterval(() => {
            loadDetail(running).then((data) => {
                if (data !== null && data.job.state !== "running") {
                    loadJobs().catch(() => undefined);
                }
            }, (e) => {
                // Stop polling (a 404 will not heal) and say why.
                if (selectedRef.current === running) {
                    setPollFailed(true);
                    setError(messageOf(e));
                }
            });
        }, POLL_MS);
        return () => clearInterval(timer);
    }, [running, loadDetail, loadJobs]);

    const act = async (action: string) => {
        if (detail === null) {
            return;
        }
        const id = detail.job.id;
        setError("");
        try {
            const job = await postJob(
                `${BASE}/jobs/${encodeURIComponent(id)}:${action}`, {});
            if (selectedRef.current === id) {
                setPollFailed(false);
                // A resumed job runs: its sets (and the preview's video, which
                // the server no longer serves) go until it ends, as GET says.
                setDetail({job, sets: job.state === "running"
                    ? null : detail.sets});
            }
            await loadJobs();
        } catch (e) {
            if (selectedRef.current === id) {
                setError(messageOf(e));
            }
        }
    };

    if (status === null) {
        return statusFailed ? <Unavailable status={null}/> : null;
    }
    if (!status.available) {
        return <Unavailable status={status}/>;
    }

    const sets = detail?.sets ?? null;
    const summary = summarize(sets);
    return (
        <Box sx={{height: "100vh", display: "flex", flexDirection: "column",
                  minHeight: 0}}>
            <Stack direction="row" spacing={2} alignItems="center"
                   sx={{p: 1.5, borderBottom: "1px solid",
                        borderColor: "divider", flexWrap: "wrap"}}>
                <FormControl size="small" sx={{minWidth: 320}}>
                    <InputLabel>{i18n.t("archive_job")}</InputLabel>
                    <Select label={i18n.t("archive_job")}
                            value={selected === "new" ? "" : selected}
                            displayEmpty={false}
                            onChange={(e) => select(String(e.target.value))}>
                        {jobs.length === 0 ? (
                            <MenuItem value="" disabled>
                                {i18n.t("archive_no_jobs")}
                            </MenuItem>
                        ) : null}
                        {jobs.map((job) => (
                            <MenuItem key={job.id} value={job.id} title={job.source}>
                                {jobLabel(job)} ({i18n.t(`archive_state_${job.state}`,
                                                         {defaultValue: job.state})})
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>
                <Button variant="outlined" onClick={() => select("new")}>
                    {i18n.t("archive_new_job")}
                </Button>
                {selected !== "new" && sets ? (
                    <Stack direction="row" spacing={1}>
                        <Chip label={`${i18n.t("archive_chip_sets")} ${summary.sets}`}/>
                        <Chip label={`${i18n.t("archive_chip_matched")} ${summary.matched}`}/>
                        <Chip label={`${i18n.t("archive_chip_review")} ${summary.review}`}
                              color={summary.review > 0 ? "error" : "default"}/>
                        <Chip label={`${i18n.t("archive_chip_games")} ${summary.games}`}/>
                    </Stack>
                ) : null}
            </Stack>
            {readOnly ? (
                <Alert severity="info" data-testid="archive-read-only">
                    {i18n.t("archive_read_only")}
                </Alert>
            ) : null}
            {error ? <Alert severity="error" onClose={() => setError("")}>
                {error}
            </Alert> : null}
            {selected === "new" ? (
                <NewJobForm status={status} readOnly={readOnly}
                            onStatus={setStatus}
                            onStarted={(job) => {
                                setJobs((known) => [job, ...known]);
                                // Left for another job meanwhile: stay there.
                                if (selectedRef.current === "new") {
                                    select(job.id);
                                }
                            }}
                            onFailed={() => {
                                loadJobs().catch(() => undefined);
                            }}/>
            ) : detail === null ? null : (
                <Box sx={{flex: 1, minHeight: 0, display: "flex",
                          flexDirection: "column"}}>
                    {detail.job.state === "running"
                        ? <Progress job={detail.job} readOnly={readOnly}
                                    onAct={act}/>
                        : <Ended job={detail.job} readOnly={readOnly}
                                 onAct={act}/>}
                    {(detail.job.warnings ?? []).length > 0 ? (
                        <Alert severity="warning" sx={{mx: 2}}>
                            {(detail.job.warnings ?? []).join(" / ")}
                        </Alert>
                    ) : null}
                    {sets ? (
                        <SetsView key={detail.job.id} job={detail.job}
                                  sets={sets} setNo={setNo}
                                  onSetNo={setSetNo}/>
                    ) : null}
                </Box>
            )}
        </Box>
    );
}

export default function ArchivePage() {
    return (
        <FeedbackProvider>
            <ArchiveContent/>
        </FeedbackProvider>
    );
}
