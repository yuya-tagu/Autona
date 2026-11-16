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

// The Archive Clipper page (ADR-006 A1), /web/app/archive: starts a worker
// job on a VOD, shows its progress and the sets it found. State is the
// page's own (no Redux / StateManager, ADR-006 D6); a running job is polled
// every second through GET /api/v1/archive/jobs/<id>.

const BASE = "/api/v1/archive";
const POLL_MS = 1000;
const LIST_WIDTH = 320;

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

function SetListRow({row, source, sourceId, selected, onSelect}: {
    row: SetRow;
    source?: string | null;
    sourceId?: string | null;
    selected: boolean;
    onSelect: () => void;
}) {
    const text = rowText(row, i18n.t("archive_no_match"));
    const score = scoreText(row);
    return (
        <Box role="button" tabIndex={0} onClick={onSelect}
             onKeyDown={(event) => {
                 if (event.key === "Enter" || event.key === " ") {
                     event.preventDefault();
                     onSelect();
                 }
             }}
             data-testid={`archive-set-${row.no}`}
             sx={{display: "flex", gap: 1, p: 1, cursor: "pointer",
                  borderBottom: "1px solid", borderColor: "divider",
                  bgcolor: selected ? tokens.bgTertiary : "transparent",
                  "&:hover": {bgcolor: tokens.bgTertiary}}}>
            {/* Frames come in a later stage: a placeholder box. */}
            <Box sx={{width: 48, height: 27, flexShrink: 0,
                      bgcolor: tokens.bgQuaternary, borderRadius: "2px"}}/>
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

function SetList({sets, source, selectedNo, onSelect}: {
    sets: SetsJson;
    source?: string | null;
    selectedNo: number | null;
    onSelect: (no: number) => void;
}) {
    const [filter, setFilter] = React.useState<SetFilter>("all");
    const rows = filterSets(sets.sets, filter);
    const unused = sets.unused_sets ?? [];
    return (
        <Box sx={{width: LIST_WIDTH, flexShrink: 0, display: "flex",
                  flexDirection: "column", minHeight: 0,
                  borderRight: "1px solid", borderColor: "divider"}}>
            <ToggleButtonGroup exclusive size="small" value={filter}
                               onChange={(_e, next) => next && setFilter(next)}
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
                    <SetListRow key={row.no} row={row} source={source}
                                sourceId={sets.source_id}
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

function SetDetails({row}: {row: SetRow | null}) {
    if (row === null) {
        return (
            <Typography sx={{p: 2, color: tokens.textMuted}}>
                {i18n.t("archive_detail_pick")}
            </Typography>
        );
    }
    const fields: [string, string][] = [
        ["archive_detail_games", String(row.games ?? "")],
        ["archive_detail_spans", String(row.game_spans?.length ?? 0)],
        ["archive_detail_name_match", nameMatchText(row.name_match)],
        ["archive_detail_corrected", correctedText(row.corrected_games)
            ?? i18n.t("archive_detail_none")],
    ];
    if (row.reason) {
        fields.push(["archive_detail_reason", row.reason]);
    }
    return (
        <Stack spacing={1} sx={{p: 2}}>
            <Typography variant="subtitle2">
                {i18n.t("archive_details")} #{row.no}
            </Typography>
            {fields.map(([key, value]) => (
                <Box key={key}>
                    <Typography variant="caption"
                                sx={{color: tokens.textMuted}}>
                        {i18n.t(key)}
                    </Typography>
                    <Typography variant="body2"
                                sx={{wordBreak: "break-all"}}>
                        {value}
                    </Typography>
                </Box>
            ))}
        </Stack>
    );
}

function NewJobForm({status, onStatus, onStarted, onFailed}: {
    status: ArchiveStatus;
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
                        <Button onClick={saveWorkDir}>
                            {i18n.t("archive_save")}
                        </Button>
                    </>
                ) : (
                    <>
                        <Typography variant="body2" sx={{flex: 1,
                                    wordBreak: "break-all"}}>
                            {i18n.t("archive_work_dir")}: {status.work_dir}
                        </Typography>
                        <Button onClick={() => {
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
                <Button variant="contained" disabled={!ready || busy}
                        onClick={start}>
                    {i18n.t("archive_start")}
                </Button>
            </Box>
        </Stack>
    );
}

function Progress({job, onAct}: {job: Job; onAct: (action: string) => void}) {
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
                <Button color="error" variant="outlined"
                        onClick={() => onAct("cancel")}>
                    {i18n.t("archive_abort")}
                </Button>
            </Box>
        </Stack>
    );
}

function Ended({job, onAct}: {job: Job; onAct: (action: string) => void}) {
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
                <Button variant="outlined" onClick={() => onAct("resume")}>
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
    const [selected, setSelected] = React.useState<string | "new">("new");
    const [detail, setDetail] = React.useState<Detail | null>(null);
    const [setNo, setSetNo] = React.useState<number | null>(null);
    const [error, setError] = React.useState("");
    const [pollFailed, setPollFailed] = React.useState(false);
    // The id the view shows right now: an async result for any other job is
    // stale and dropped.
    const selectedRef = React.useRef<string | "new">("new");

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
                setDetail({job, sets: detail.sets});
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
    const row = sets?.sets.find((r) => r.no === setNo) ?? null;
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
            {error ? <Alert severity="error" onClose={() => setError("")}>
                {error}
            </Alert> : null}
            {selected === "new" ? (
                <NewJobForm status={status} onStatus={setStatus}
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
                        ? <Progress job={detail.job} onAct={act}/>
                        : <Ended job={detail.job} onAct={act}/>}
                    {(detail.job.warnings ?? []).length > 0 ? (
                        <Alert severity="warning" sx={{mx: 2}}>
                            {(detail.job.warnings ?? []).join(" / ")}
                        </Alert>
                    ) : null}
                    {sets ? (
                        <Box sx={{flex: 1, minHeight: 0, display: "grid",
                                  gridTemplateColumns: `${LIST_WIDTH}px 1fr`}}>
                            <SetList sets={sets} source={detail.job.source}
                                     selectedNo={setNo}
                                     onSelect={setSetNo}/>
                            <Box sx={{overflowY: "auto"}}>
                                <SetDetails row={row}/>
                            </Box>
                        </Box>
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
