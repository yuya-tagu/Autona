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
import {apiErrorStatus, apiGetJson, apiPatch, apiPost, apiPut, ApiError} from "../api";
import {FeedbackProvider} from "../ScoreboardPage/Feedback";
import {
    ArchiveStatus,
    Job,
    JobExport,
    JobSummary,
    JobThumbs,
    MatchLogDay,
    PrepareSummary,
    SetCharacters,
    SetFilter,
    SetResult,
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
    characterIcon,
    dayText,
    isEstimate,
    setCharacters,
    sourceCounts,
} from "./archiveSets";
import {
    PreviewPlayer,
    framePath,
    jobFromQuery,
    keyAction,
    keyTargetKind,
    nextReviewSet,
    setNumber,
    stepSet,
    thumbPath,
    videoExtension,
    youtubeEmbedId,
} from "./archivePreview";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {
    TimelineView,
    bestShare,
    gameNames,
    timelineRange,
    videoLength,
} from "./archiveTimeline";
import {
    EMPTY_REVIEW,
    Review,
    ReviewSaver,
    ReviewedRow,
    SaveState,
    canonical,
    candidates,
    confirmedCount,
    holders,
    nextUnconfirmed,
    readReview,
    reviewedRows,
    sameResult,
    withConfirmed,
    withEdge,
    withRangeReset,
    withResult,
} from "./archiveReview";
import Preview from "./Preview";
import Timeline from "./Timeline";
import Templates, {TemplateLine} from "./Templates";
import {Template, TemplateEntry} from "./archiveTemplates";
import WorkerInstall, {WorkerChips} from "./WorkerInstall";

// The Archive Clipper page (ADR-006 A1), /web/app/archive: starts a worker
// job on a VOD, shows its progress and the sets it found. State is the
// page's own (no Redux / StateManager, ADR-006 D6); a running job (or its
// export) is polled every second through GET /api/v1/archive/jobs/<id>. A
// job with sets shows the mock's review screen (A3): the set list, the
// preview of the job's video (its own, or a YouTube job's in YouTube's
// embedded player: D7), the set's details and the timeline. A
// finished job's review is edited there (A4: IN / OUT, the result given,
// confirmed; saved to its review.json as it changes) and its confirmed sets
// exported to clips. ?job=<id> opens a job (the preview's "open in
// browser"). The title templates have their own view (A6: Templates, the
// header's button; the job view says which one the job's event takes): the
// job's view stays under it, hidden (the new job's form, the review, the
// selected set as they were), and the template edits not saved yet are the
// page's (they outlive the template view), and
// a finished job's confirmed sets get thumbnails (its thumbs/, shown in the
// set list and the details). The writes are SHELL routes (D7): outside the
// desktop's own window (this PC's browser) the page only reads, and says
// so. With no usable worker the page is the worker's install card (A7:
// WorkerInstall); the header shows the worker's version and a newer one.

const BASE = "/api/v1/archive";
const POLL_MS = 1000;
const LIST_WIDTH = 320;
const DETAIL_WIDTH = 300;
// The set view's room around the set where it can be edited (seconds): an
// edge dragged out of the set has somewhere to go.
const SET_VIEW_MARGIN = 30;

// The saves a job's review left going when its view closed (the saver's
// dispose): a view opened on the job again reads the review after them, so
// its GET never overtakes the last PUT.
const pendingSaves = new Map<string, Promise<unknown>>();

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

// The status could not be read (this page opened where the archive's reads
// are refused): nothing to show. No worker is the install card's.
function Unavailable() {
    return (
        <Box sx={{p: 3, maxWidth: 640}}>
            <Alert severity="info">
                <Typography fontWeight={700}>
                    {i18n.t("archive_unavailable_title")}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("archive_unavailable_body")}
                </Typography>
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

// A character's icon (ADR-006 A5): its file in the game's folder, as the
// prepared results name it; hidden when there is none or it does not load.
function CharacterIcon({game, codename, file, size}: {
    game: string;
    codename: string;
    file?: string;
    size: number;
}) {
    const [gone, setGone] = React.useState(false);
    const path = characterIcon(game, file);
    return path === null || gone ? null : (
        <Box component="img" alt="" loading="lazy" src={`${BASE_URL}${path}`}
             data-testid={`archive-character-${codename}`}
             onError={() => setGone(true)}
             sx={{width: size, height: size, flexShrink: 0,
                  objectFit: "contain"}}/>
    );
}

function SideIcons({characters, side, size}: {
    characters: SetCharacters | null;
    side: "c1" | "c2";
    size: number;
}) {
    return characters === null ? null : (
        <>
            {characters[side].map((codename) => (
                <CharacterIcon key={codename} game={characters.game}
                               codename={codename}
                               file={characters.icons[codename]} size={size}/>
            ))}
        </>
    );
}

// 推定: the characters are (partly) a player's main, not a record of the set.
function EstimateBadge() {
    return (
        <Tooltip title={i18n.t("archive_results_estimate_note")}>
            <Box component="span" data-testid="archive-estimate"
                 sx={{color: tokens.gold, fontSize: "0.7rem", fontWeight: 700,
                      border: "1px solid", borderRadius: "2px", px: 0.5,
                      lineHeight: 1.4, flexShrink: 0}}>
                {i18n.t("archive_char_estimate")}
            </Box>
        </Tooltip>
    );
}

function SetListRow({row, jobId, source, sourceId, thumb, selected,
                     onSelect}: {
    row: ReviewedRow;
    jobId: string;
    source?: string | null;
    sourceId?: string | null;
    // The set's thumbnail (A6), when it has one.
    thumb: string | null;
    selected: boolean;
    onSelect: () => void;
}) {
    const [noFrame, setNoFrame] = React.useState(false);
    const [noThumb, setNoThumb] = React.useState(false);
    // A thumbnail drawn again is tried again.
    React.useEffect(() => setNoThumb(false), [thumb]);
    const text = rowText(row, i18n.t("archive_no_match"));
    const score = scoreText(row);
    const shownThumb = thumb !== null && !noThumb ? thumb : null;
    const path = shownThumb ?? (row.thumb && !noFrame
        ? framePath(jobId, row.no) : null);
    const characters = setCharacters(row.set);
    return (
        // Enter picks the row (a button); on the selected one it is the
        // page's (confirm), as Space is (play / pause).
        <Box role="button" tabIndex={0} onClick={onSelect}
             onKeyDown={(event) => {
                 if (event.key === "Enter" && !selected) {
                     event.preventDefault();
                     onSelect();
                 }
             }}
             data-testid={`archive-set-${row.no}`}
             data-archive-set={row.no}
             data-confirmed={row.confirmed || undefined}
             aria-current={selected || undefined}
             sx={{display: "flex", gap: 1, p: 1, cursor: "pointer",
                  borderBottom: "1px solid", borderColor: "divider",
                  bgcolor: selected ? tokens.bgTertiary : "transparent",
                  "&:hover": {bgcolor: tokens.bgTertiary}}}>
            {/* The set's thumbnail, else its background frame (--thumbs),
                else a box. */}
            {path !== null ? (
                <Box component="img" alt="" loading="lazy"
                     src={`${BASE_URL}${path}`}
                     data-testid={shownThumb !== null
                         ? `archive-thumb-${row.no}` : `archive-frame-${row.no}`}
                     onError={() => (shownThumb !== null ? setNoThumb(true)
                                     : setNoFrame(true))}
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
                {characters !== null ? (
                    <Stack direction="row" spacing={0.5} alignItems="center"
                           data-testid={`archive-set-characters-${row.no}`}>
                        <SideIcons characters={characters} side="c1" size={20}/>
                        <Typography variant="caption"
                                    sx={{color: tokens.textMuted}}>
                            vs
                        </Typography>
                        <SideIcons characters={characters} side="c2" size={20}/>
                        {isEstimate(row.set) ? <EstimateBadge/> : null}
                    </Stack>
                ) : null}
                <Typography variant="caption" component="div"
                            sx={{color: tokens.textMuted}}>
                    #{row.no} · <TimeLink source={source} sourceId={sourceId}
                                          seconds={row.start}/>
                    {" · "}{formatDuration(row.end - row.start)}
                    {score ? ` · ${score}` : ""}
                    {row.confirmed ? (
                        <Box component="span"
                             sx={{color: tokens.positive, fontWeight: 700, ml: 1}}>
                            ✓ {i18n.t("archive_confirmed_badge")}
                        </Box>
                    ) : null}
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

// The path of set `no`'s thumbnail when the job has one (A6).
function thumbOf(jobId: string, thumbs: JobThumbs | null | undefined,
                 no: number): string | null {
    return thumbs?.files?.includes(no)
        ? thumbPath(jobId, no, thumbs.revision) : null;
}

function SetList({sets, rows, jobId, source, thumbs, filter, onFilter,
                  selectedNo, onSelect}: {
    sets: SetsJson;
    rows: ReviewedRow[];
    jobId: string;
    source?: string | null;
    thumbs: JobThumbs | null;
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
                                thumb={thumbOf(jobId, thumbs, row.no)}
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

// What the details edit (A4), where the review can be edited: IN / OUT at
// the current position or back to the worker's, the result given (one of
// the job's, each to one set: the others' are disabled) and confirmed.
interface DetailEdits {
    onEdge: (edge: "start" | "end") => void;
    onResetRange: () => void;
    onAssign: (result: SetResult | null) => void;
    onConfirm: () => void;
    candidates: SetResult[];
    // canonical(result) -> the set that holds it.
    held: Map<string, number>;
}

function AssignSelect({row, edit}: {row: ReviewedRow; edit: DetailEdits}) {
    const index = row.set ? edit.candidates.findIndex(
        (c) => sameResult(c, row.set)) : -1;
    const label = i18n.t("archive_detail_assign");
    return (
        <FormControl size="small" fullWidth sx={{mt: 1}}>
            <InputLabel id="archive-assign-label">{label}</InputLabel>
            <Select labelId="archive-assign-label" label={label}
                    data-testid="archive-assign"
                    value={index >= 0 ? String(index) : "none"}
                    onChange={(event) => {
                        const value = String(event.target.value);
                        edit.onAssign(value === "none" ? null
                            : edit.candidates[Number(value)] ?? null);
                    }}>
                <MenuItem value="none">{i18n.t("archive_detail_assign_none")}</MenuItem>
                {edit.candidates.map((candidate, i) => {
                    const holder = edit.held.get(canonical(candidate));
                    return (
                        <MenuItem key={i} value={String(i)}
                                  disabled={holder !== undefined}>
                            {unusedText(candidate)}
                            {holder !== undefined
                                ? ` ${i18n.t("archive_detail_assign_taken",
                                             {no: holder})}` : ""}
                        </MenuItem>
                    );
                })}
            </Select>
        </FormControl>
    );
}

// The selected set as reviewed (`original`: as the worker found it). Read
// only without `edit` (a job not done, this PC's browser).
function DetailThumb({path}: {path: string}) {
    const [gone, setGone] = React.useState(false);
    React.useEffect(() => setGone(false), [path]);
    return gone ? null : (
        <Box component="img" alt="" src={`${BASE_URL}${path}`}
             data-testid="archive-detail-thumb"
             onError={() => setGone(true)}
             sx={{width: "100%", aspectRatio: "16 / 9", objectFit: "contain",
                  borderRadius: 1, bgcolor: tokens.bgSecondary, mt: 0.5}}/>
    );
}

function SetDetails({row, original, sets, source, thumb, onSeek, edit}: {
    row: ReviewedRow | null;
    original: SetRow | null;
    sets: SetsJson;
    source?: string | null;
    // The set's thumbnail (A6), when it has one.
    thumb?: string | null;
    onSeek: (t: number) => void;
    edit?: DetailEdits;
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
    const characters = setCharacters(result);
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
            {thumb ? <DetailThumb path={thumb}/> : null}
            {edit ? (
                <Box>
                    <Button size="small" data-testid="archive-confirm"
                            variant={row.confirmed ? "outlined" : "contained"}
                            onClick={edit.onConfirm}>
                        {row.confirmed ? i18n.t("archive_detail_unconfirm")
                            : i18n.t("archive_detail_confirm")}
                    </Button>
                </Box>
            ) : row.confirmed ? (
                <Typography variant="body2" sx={{color: tokens.positive}}>
                    ✓ {i18n.t("archive_confirmed_badge")}
                </Typography>
            ) : null}
            {edit ? <AssignSelect row={row} edit={edit}/> : null}
            {row.title && !row.setEdited ? (
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
                {row.rangeEdited && original ? (
                    <Box data-testid="archive-detected-range"
                         sx={{color: tokens.textMuted, fontSize: "0.75rem"}}>
                        {i18n.t("archive_detail_detected", {
                            start: formatClock(original.start),
                            end: formatClock(original.end)})}
                    </Box>
                ) : null}
                {edit ? (
                    <Stack direction="row" spacing={0.5} sx={{mt: 0.5}}>
                        <Button size="small" variant="outlined"
                                onClick={() => edit.onEdge("start")}>
                            {i18n.t("archive_detail_set_in")}
                        </Button>
                        <Button size="small" variant="outlined"
                                onClick={() => edit.onEdge("end")}>
                            {i18n.t("archive_detail_set_out")}
                        </Button>
                        {row.rangeEdited ? (
                            <Button size="small" onClick={edit.onResetRange}>
                                {i18n.t("archive_detail_reset_range")}
                            </Button>
                        ) : null}
                    </Stack>
                ) : null}
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
                    {result?.c1 || result?.c2 ? (
                        <>
                            <dt>{i18n.t("archive_detail_characters")}</dt>
                            <dd data-testid="archive-detail-characters">
                                {(["c1", "c2"] as const).map((side) => (
                                    <Box key={side}
                                         sx={{display: "flex", gap: 0.5,
                                              alignItems: "center"}}>
                                        <SideIcons characters={characters}
                                                   side={side} size={24}/>
                                        <span>{result?.[side] || "—"}</span>
                                    </Box>
                                ))}
                                {isEstimate(result) ? <EstimateBadge/> : null}
                            </dd>
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

// The thumbnails of the confirmed sets (A6): how far their drawing is, how
// it ended.
function ThumbsState({thumbs}: {thumbs: JobThumbs | null}) {
    if (thumbs === null || thumbs.state === null) {
        return null;
    }
    const failed = thumbs.failed ?? [];
    const failedText = failed.length > 0
        ? ` ${i18n.t("archive_thumbs_failed_sets",
                     {sets: failed.map((no) => `#${no}`).join(", ")})}` : "";
    if (thumbs.state === "running") {
        const finished = thumbs.done + failed.length;
        return (
            <Box data-testid="archive-thumbs-running">
                <Typography variant="body2">
                    {i18n.t("archive_thumbs_running",
                            {done: finished, total: thumbs.total})}
                </Typography>
                <LinearProgress variant="determinate"
                                value={thumbs.total > 0
                                    ? Math.round(finished / thumbs.total * 100)
                                    : 0}/>
            </Box>
        );
    }
    if (thumbs.state === "done") {
        return (
            <Alert severity={failed.length > 0 ? "warning" : "success"}
                   data-testid="archive-thumbs-result">
                {i18n.t("archive_thumbs_done", {count: thumbs.done})}
                {failedText}
            </Alert>
        );
    }
    return (
        <Alert severity={thumbs.state === "cancelled" ? "info" : "error"}
               data-testid="archive-thumbs-result">
            {thumbs.state === "cancelled" ? i18n.t("archive_thumbs_cancelled")
                : i18n.t("archive_thumbs_failed")}
            {failedText}
        </Alert>
    );
}

// The review's save and the export (A4), over the review screen: how many
// sets are confirmed, whether the review is saved, the export of the
// confirmed ones (its progress, its stop, what it cut and where), the
// template the export names them with and the confirmed sets' thumbnails
// (A6).
function ExportBar({exported, total, confirmed, canExport, readOnly, starting,
                    save, reviewError, onExport, onCancel, templates, event,
                    analysisTemplate, onTemplates, thumbs, onThumbs}: {
    exported: JobExport | null;
    total: number;
    confirmed: number;
    canExport: boolean;
    readOnly: boolean;
    starting: boolean;
    save: SaveState;
    reviewError: string;
    onExport: () => void;
    onCancel: () => void;
    templates: TemplateEntry[] | null;
    event: string | null | undefined;
    // sets.json's: the template the analysis named the sets with.
    analysisTemplate: string | null | undefined;
    onTemplates: () => void;
    thumbs: JobThumbs | null;
    onThumbs: () => void;
}) {
    const running = exported?.state === "running";
    const saveText = save.status === "pending" || save.status === "saving"
        ? i18n.t("archive_review_saving")
        : save.status === "saved" ? i18n.t("archive_review_saved") : "";
    const progress = exported?.progress;
    const files = exported?.files ?? [];
    const failure = exported && (exported.state === "error"
                                 || exported.state === "failed")
        ? exported.error?.message ?? i18n.t("archive_export_failed_unknown")
        : null;
    return (
        <Stack spacing={1} data-testid="archive-export"
               sx={{px: 2, py: 1, borderBottom: "1px solid",
                    borderColor: "divider"}}>
            <Stack direction="row" spacing={1.5} alignItems="center"
                   sx={{flexWrap: "wrap"}}>
                <Chip size="small" data-testid="archive-confirmed-count"
                      label={i18n.t("archive_export_count",
                                    {confirmed, total})}/>
                <Button variant="contained" size="small"
                        disabled={!canExport || confirmed === 0 || running
                                  || starting}
                        onClick={onExport}>
                    {i18n.t("archive_export")}
                </Button>
                <Button variant="outlined" size="small"
                        data-testid="archive-thumbs"
                        disabled={!canExport || confirmed === 0 || starting
                                  || thumbs?.state === "running"}
                        onClick={onThumbs}>
                    {i18n.t("archive_thumbs_make")}
                </Button>
                <Typography variant="caption" data-testid="archive-save-state"
                            sx={{color: tokens.textMuted}}>
                    {saveText}
                </Typography>
            </Stack>
            <Stack direction="row" spacing={1} alignItems="center"
                   sx={{flexWrap: "wrap"}}>
                <TemplateLine entries={templates} event={event}
                              label={i18n.t("archive_tpl_export_line")}
                              onEdit={onTemplates}/>
                {analysisTemplate ? (
                    <Typography variant="caption"
                                data-testid="archive-analysis-template"
                                sx={{color: tokens.textMuted}}>
                        {i18n.t("archive_tpl_analysis",
                                {name: analysisTemplate})}
                    </Typography>
                ) : null}
            </Stack>
            <ThumbsState thumbs={thumbs}/>
            {reviewError ? (
                <Alert severity="warning">
                    {i18n.t("archive_review_load_failed", {message: reviewError})}
                </Alert>
            ) : null}
            {save.status === "error" ? (
                <Alert severity="error" data-testid="archive-save-error">
                    {i18n.t("archive_review_save_failed",
                            {message: messageOf(save.error)})}
                </Alert>
            ) : null}
            {running ? (
                <Stack direction="row" spacing={1.5} alignItems="center">
                    <Box sx={{flex: 1}}>
                        <Typography variant="body2">
                            {i18n.t("archive_export_running", {
                                percent: Math.round((progress?.overall ?? 0) * 100)})}
                        </Typography>
                        <LinearProgress variant="determinate"
                                        data-testid="archive-export-progress"
                                        value={Math.round((progress?.overall ?? 0) * 100)}/>
                    </Box>
                    <Button color="error" variant="outlined" size="small"
                            disabled={readOnly} onClick={onCancel}>
                        {i18n.t("archive_abort")}
                    </Button>
                </Stack>
            ) : null}
            {exported?.state === "done" ? (
                <Alert severity="success" data-testid="archive-export-result">
                    <Typography variant="body2">
                        {i18n.t("archive_export_done", {count: Math.max(
                            0, (exported.clips ?? 0) - (exported.skipped ?? 0))})}
                        {exported.skipped
                            ? ` ${i18n.t("archive_export_skipped",
                                         {skipped: exported.skipped})}` : ""}
                    </Typography>
                    {exported.out_dir ? (
                        <Typography variant="body2"
                                    data-testid="archive-export-dir"
                                    sx={{wordBreak: "break-all"}}>
                            {i18n.t("archive_export_out_dir",
                                    {path: exported.out_dir})}
                        </Typography>
                    ) : null}
                    {files.length > 0 ? (
                        <Box component="details" sx={{mt: 0.5}}>
                            <Box component="summary" sx={{cursor: "pointer"}}>
                                {i18n.t("archive_export_files")} ({files.length})
                            </Box>
                            {files.map((clip) => (
                                <Typography key={`${clip.no}-${clip.file}`}
                                            variant="caption" component="div"
                                            sx={{wordBreak: "break-all"}}>
                                    #{clip.no} {clip.file}
                                </Typography>
                            ))}
                        </Box>
                    ) : null}
                </Alert>
            ) : null}
            {exported?.state === "cancelled" ? (
                <Alert severity="info">{i18n.t("archive_export_cancelled")}</Alert>
            ) : null}
            {failure !== null ? (
                <Alert severity="error" data-testid="archive-export-failure">
                    {failure}
                    {exported?.error?.hint ? (
                        <Typography variant="body2">{exported.error.hint}</Typography>
                    ) : null}
                </Alert>
            ) : null}
        </Stack>
    );
}

// The review screen of a job with sets (ADR-006 A3): the mock's grid -- the
// set list, the preview and the details over the timeline. Its keys work
// within it, not while typing (archivePreview.keyAction). A finished job's
// review (A4) is read with it and, in the desktop's window, edited there:
// each change is saved (ReviewSaver: one PUT of the whole review after the
// edits stop) and the export saves what is pending first.
function SetsView({job, sets, setNo, onSetNo, readOnly, onAct, templates,
                   onTemplates, hidden = false}: {
    job: Job;
    sets: SetsJson;
    setNo: number | null;
    onSetNo: (no: number) => void;
    readOnly: boolean;
    onAct: (action: string) => Promise<void>;
    templates: TemplateEntry[] | null;
    onTemplates: () => void;
    // Under the templates' view: kept as it is, its video paused.
    hidden?: boolean;
}) {
    const [filter, setFilter] = React.useState<SetFilter>("all");
    const [view, setView] = React.useState<TimelineView>("all");
    const [time, setTime] = React.useState(0);
    const [duration, setDuration] = React.useState(0);
    const [playing, setPlaying] = React.useState(false);
    const [review, setReview] = React.useState<Review | null>(null);
    const [reviewError, setReviewError] = React.useState("");
    const [save, setSave] = React.useState<SaveState>({status: "idle"});
    const [starting, setStarting] = React.useState(false);
    // The preview's player (its <video>, or YouTube's embedded one), once
    // it can be driven.
    const playerRef = React.useRef<PreviewPlayer | null>(null);
    const root = React.useRef<HTMLDivElement>(null);
    const saver = React.useRef<ReviewSaver<Review> | null>(null);
    // A key moved the selection from a focused set row: the focus follows
    // (Enter on the next row confirms it, not picks the last one again).
    const followFocus = React.useRef(false);
    const done = job.state === "done";
    const editable = !readOnly && done && review !== null;

    const shown = React.useMemo(() => {
        const reviewed = reviewedRows(sets.sets, review ?? EMPTY_REVIEW);
        return {...sets, sets: reviewed};
    }, [sets, review]);
    const rows = filterSets(shown.sets, filter) as ReviewedRow[];
    const row = shown.sets.find((r) => r.no === setNo) ?? null;
    const original = sets.sets.find((r) => r.no === setNo) ?? null;
    const videoEnd = typeof sets.duration === "number"
        && Number.isFinite(sets.duration) && sets.duration > 0
        ? sets.duration : null;

    // The job's review, saved as it changes (the saver is this job's: the
    // view is keyed by job). A save the server refuses (400: it no longer
    // fits the job's sets) is dropped and the server's review shown again.
    // Closing the view still sends what is waiting (pendingSaves).
    React.useEffect(() => {
        const path = `${BASE}/jobs/${encodeURIComponent(job.id)}/review`;
        let live = true;
        const made = new ReviewSaver<Review>(
            (value, options) => apiPut(path, {sets: value.sets}, options),
            (state) => {
                setSave(state);
                if (state.status === "error"
                    && apiErrorStatus(state.error) === 400) {
                    made.forget();
                    getData<{review: unknown}>(path).then((data) => {
                        // Not over an edit made since (it is being saved).
                        if (live && made.idle()) {
                            setReview(readReview(data.review));
                        }
                    }, () => undefined);
                }
            });
        saver.current = made;
        return () => {
            live = false;
            const settled = made.dispose();
            pendingSaves.set(job.id, settled);
            settled.then(() => {
                if (pendingSaves.get(job.id) === settled) {
                    pendingSaves.delete(job.id);
                }
            });
            if (saver.current === made) {
                saver.current = null;
            }
        };
    }, [job.id]);
    React.useEffect(() => {
        setReview(null);
        setReviewError("");
        if (!done) {
            return undefined;
        }
        let live = true;
        const path = `${BASE}/jobs/${encodeURIComponent(job.id)}/review`;
        // After the saves the job's last view left going.
        (pendingSaves.get(job.id) ?? Promise.resolve()).then(
            () => getData<{review: unknown}>(path)).then(
            (data) => live && setReview(readReview(data.review)),
            (e) => live && setReviewError(messageOf(e)));
        return () => {
            live = false;
        };
    }, [job.id, done]);
    // Leaving the page (a reload, the window closing) sends what is waiting,
    // in a request that outlives the page.
    React.useEffect(() => {
        const leave = () => saver.current?.sendOnUnload();
        window.addEventListener("beforeunload", leave);
        return () => window.removeEventListener("beforeunload", leave);
    }, []);

    // Opening the job (or coming back to it from the templates) hands the
    // keys to it (Space / arrows / N), without scrolling the page to it. A
    // hidden view's video does not play on (it would be heard).
    React.useEffect(() => {
        if (!hidden) {
            root.current?.focus({preventScroll: true});
            return;
        }
        try {
            playerRef.current?.pause();
        } catch {
            // A video that cannot play is not playing.
        }
    }, [hidden]);
    // Keep the selected set's row in view (after a filter reset too). Only a
    // set number goes into the selector.
    React.useEffect(() => {
        const number = setNumber(setNo);
        const follow = followFocus.current;
        followFocus.current = false;
        if (number !== null) {
            const element = root.current?.querySelector<HTMLElement>(
                `[data-archive-set="${number}"]`);
            element?.scrollIntoView?.({block: "nearest"});
            if (follow) {
                element?.focus({preventScroll: true});
            }
        }
    }, [setNo, filter]);

    const seek = (t: number) => {
        try {
            playerRef.current?.seek(t);
        } catch {
            // Not seekable yet: the time still shows where it was asked.
        }
        setTime(t);
    };
    // Pick set `no` and play from `at` (its start by default) -> the set
    // picked, or null. A set the filter hides (picked on the timeline) shows
    // all the sets again, so the list shows what is selected.
    const choose = (no: number | null, at?: number): number | null => {
        const next = shown.sets.find((r) => r.no === no);
        if (!next) {
            return null;
        }
        if (!rows.some((r) => r.no === next.no)) {
            setFilter("all");
        }
        onSetNo(next.no);
        seek(at ?? next.start);
        return next.no;
    };
    const togglePlay = () => {
        const player = playerRef.current;
        if (!player) {
            return;
        }
        try {
            if (player.paused()) {
                player.play();
            } else {
                player.pause();
            }
        } catch {
            // A video that cannot play: the preview says why.
        }
    };

    // A change of the selected set's review: shown now, saved soon.
    const change = (make: (current: Review, set: SetRow) => Review) => {
        if (!editable || review === null || original === null) {
            return null;
        }
        const next = make(review, original);
        setReview(next);
        saver.current?.push(next);
        return next;
    };
    // The position the preview is at: the player's own time when it has one.
    const position = () => {
        let current: number | null | undefined;
        try {
            current = playerRef.current?.currentTime();
        } catch {
            current = null;
        }
        return typeof current === "number" && Number.isFinite(current)
            ? current : time;
    };
    const setEdge = (edge: "start" | "end", t: number) => {
        change((current, set) => withEdge(current, set, edge, t, videoEnd));
    };
    // Confirm the selected set and go to the next one not confirmed; or
    // take a confirmation back (staying there) -> the set picked, or null.
    const confirm = (): number | null => {
        if (row === null) {
            return null;
        }
        const next = change((current, set) =>
            withConfirmed(current, set, !row.confirmed));
        if (next !== null && !row.confirmed) {
            const listed = filterSets(reviewedRows(sets.sets, next), filter);
            return choose(nextUnconfirmed(listed as ReviewedRow[], row.no));
        }
        return null;
    };
    // The export and the thumbnails take the review the server has: what is
    // not saved yet goes first.
    const afterSaving = async (action: string) => {
        setStarting(true);
        try {
            if (await (saver.current?.flush() ?? Promise.resolve(true))) {
                await onAct(action);
            }
        } finally {
            setStarting(false);
        }
    };

    const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        const action = keyAction({
            key: event.key, ctrlKey: event.ctrlKey, altKey: event.altKey,
            metaKey: event.metaKey, shiftKey: event.shiftKey,
            repeat: event.repeat, defaultPrevented: event.defaultPrevented,
            isComposing: event.nativeEvent.isComposing,
        }, keyTargetKind(event.target));
        if (action === null) {
            return;
        }
        const editing = action === "setIn" || action === "setOut"
            || action === "confirm";
        // The review's keys only where it can be edited, on a selected set:
        // else Enter keeps its own meaning.
        if (editing && (!editable || original === null)) {
            return;
        }
        event.preventDefault();
        const fromRow = Boolean(
            (event.target as HTMLElement | null)?.closest?.("[data-archive-set]"));
        let picked: number | null = null;
        if (action === "togglePlay") {
            togglePlay();
        } else if (action === "previousSet") {
            picked = choose(stepSet(rows, setNo, -1));
        } else if (action === "nextSet") {
            picked = choose(stepSet(rows, setNo, 1));
        } else if (action === "nextReview") {
            picked = choose(nextReviewSet(rows, setNo));
        } else if (action === "setIn") {
            setEdge("start", position());
        } else if (action === "setOut") {
            setEdge("end", position());
        } else {
            picked = confirm();
        }
        // The focus follows a set the key picked from a row. A key that left
        // the selection where it was (the last row, nothing more to confirm)
        // has nothing to follow: a later pick by the mouse must not take it.
        followFocus.current = fromRow && picked !== null && picked !== setNo;
    };
    const edit: DetailEdits | undefined = editable ? {
        onEdge: (edge) => setEdge(edge, position()),
        onResetRange: () => change(withRangeReset),
        onAssign: (result) => change((current, set) =>
            withResult(current, set, result)),
        onConfirm: confirm,
        candidates: candidates(sets),
        held: holders(shown.sets, setNo),
    } : undefined;
    const shownView = row === null ? "all" : view;
    return (
        <Box ref={root} tabIndex={-1} onKeyDown={onKeyDown}
             data-testid="archive-review"
             sx={{flex: 1, minHeight: 0, display: "flex",
                  flexDirection: "column", outline: "none"}}>
            {done ? (
                <ExportBar exported={job.export ?? null} total={shown.sets.length}
                           confirmed={confirmedCount(shown.sets)}
                           canExport={editable} readOnly={readOnly}
                           starting={starting} save={save}
                           reviewError={reviewError}
                           onExport={() => afterSaving("export")}
                           onCancel={() => onAct("cancel")}
                           templates={templates} event={sets.event}
                           analysisTemplate={sets.template}
                           onTemplates={onTemplates}
                           thumbs={job.thumbs ?? null}
                           onThumbs={() => afterSaving("thumbs")}/>
            ) : null}
            <Box sx={{flex: 1, minHeight: 0, display: "grid",
                      gridTemplateColumns:
                          `${LIST_WIDTH}px minmax(0, 1fr) ${DETAIL_WIDTH}px`,
                      gridTemplateRows: "minmax(0, 1fr) auto"}}>
                <SetList sets={shown} rows={rows} jobId={job.id}
                         source={job.source} thumbs={job.thumbs ?? null}
                         filter={filter}
                         onFilter={setFilter} selectedNo={setNo}
                         onSelect={(no) => choose(no)}/>
                <Preview jobId={job.id}
                         extension={videoExtension(job.source, sets.source)}
                         row={row} source={job.source}
                         youtubeId={youtubeEmbedId(job.source, sets.source_id)}
                         playerRef={playerRef} time={time}
                         duration={Number.isFinite(duration) && duration > 0
                             ? duration : videoLength(sets)}
                         playing={playing} editable={editable}
                         onTogglePlay={togglePlay}
                         onPrevious={() => choose(stepSet(rows, setNo, -1))}
                         onNext={() => choose(stepSet(rows, setNo, 1))}
                         onTime={setTime} onDuration={setDuration}
                         onPlaying={setPlaying}/>
                <Box sx={{overflowY: "auto", minHeight: 0, borderLeft: "1px solid",
                          borderColor: "divider"}}>
                    <SetDetails row={row} original={original} sets={shown}
                                source={job.source} onSeek={seek} edit={edit}
                                thumb={row === null ? null
                                    : thumbOf(job.id, job.thumbs, row.no)}/>
                </Box>
                <Box sx={{gridColumn: "1 / -1", minWidth: 0}}>
                    <Timeline sets={shown} rows={rows} selected={row}
                              view={shownView} onView={setView} time={time}
                              onSeek={seek} onPick={(no, t) => choose(no, t)}
                              onEdge={editable ? setEdge : undefined}
                              range={timelineRange(shown, shownView, row,
                                                   editable ? SET_VIEW_MARGIN : 0)}/>
                </Box>
            </Box>
        </Box>
    );
}

type ResultsMode = "none" | "startgg" | "matchlog" | "json";

// The results ready for the job (POST results:prepare, A5).
interface Prepared {
    results: object;
    summary: PrepareSummary;
}

// The preparation reaches start.gg: an event of many pages, each retried
// on a busy answer (the footer shows its pages). The server gives up after
// 120 s (StartGGDataProvider.ARCHIVE_FETCH_DEADLINE_SECS) and says so; the
// page waits a little longer, to hear it.
const PREPARE_TIMEOUT_MS = 150000;
// The match log's tournament picks: the whole day, or one name ("" is the
// records with no tournament name, which a Select cannot take as a value).
const WHOLE_DAY = "*";
const NAMED = "=";

async function prepareResults(body: object): Promise<Prepared> {
    const text = await apiPost(`${BASE}/results:prepare`, body,
                               {timeoutMs: PREPARE_TIMEOUT_MS});
    return (JSON.parse(text) as {data: Prepared}).data;
}

// The prepared results' summary: how many sets, where their characters
// came from (A5).
function PreparedSummary({prepared, mode}: {
    prepared: Prepared;
    mode: ResultsMode;
}) {
    const {summary} = prepared;
    const counts = sourceCounts(summary);
    return (
        <Alert severity="success" sx={{mt: 1}}
               data-testid="archive-results-summary">
            <Typography variant="body2" fontWeight={700}>
                {i18n.t("archive_results_summary", {count: summary.sets})}
            </Typography>
            {counts.length > 0 ? (
                <Typography variant="body2">
                    {i18n.t("archive_results_characters")}:{" "}
                    {counts.map(([key, n]) =>
                        `${i18n.t(`archive_char_source_${key}`)} ${n}`)
                        .join(" · ")}
                </Typography>
            ) : null}
            {mode === "startgg" ? (
                <Typography variant="body2">
                    {i18n.t("archive_results_records",
                            {count: summary.records})}
                </Typography>
            ) : null}
            {summary.sources.mains ? (
                <Typography variant="body2" sx={{color: tokens.textMuted}}>
                    {i18n.t("archive_results_estimate_note")}
                </Typography>
            ) : null}
        </Alert>
    );
}

function NewJobForm({status, readOnly, onStatus, onStarted, onFailed,
                     templates, onTemplates}: {
    status: ArchiveStatus;
    readOnly: boolean;
    onStatus: (status: ArchiveStatus) => void;
    onStarted: (job: Job) => void;
    onFailed: () => void;
    templates: TemplateEntry[] | null;
    onTemplates: () => void;
}) {
    const [source, setSource] = React.useState("");
    const [event, setEvent] = React.useState("");
    const [mode, setMode] = React.useState<ResultsMode>("none");
    const [fileError, setFileError] = React.useState("");
    const [url, setUrl] = React.useState("");
    const [days, setDays] = React.useState<MatchLogDay[] | null>(null);
    const [daysError, setDaysError] = React.useState("");
    const [date, setDate] = React.useState("");
    const [tournament, setTournament] = React.useState(WHOLE_DAY);
    const [prepared, setPrepared] = React.useState<Prepared | null>(null);
    const [preparing, setPreparing] = React.useState(false);
    const [prepareError, setPrepareError] = React.useState("");
    const [error, setError] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [editing, setEditing] = React.useState(false);
    const [workDir, setWorkDir] = React.useState(status.work_dir ?? "");
    // The latest preparation asked: an earlier one's late answer, or one
    // the form stopped waiting for (another choice, unmounted), is dropped.
    const asked = React.useRef(0);
    // The latest list of days asked, the same way: one the form no longer
    // waits for (another choice, unmounted) is dropped.
    const daysAsked = React.useRef(0);

    React.useEffect(() => () => {
        asked.current += 1;
        daysAsked.current += 1;
    }, []);

    // The results no longer answer what is picked: they are asked again.
    const forget = () => {
        asked.current += 1;
        setPrepared(null);
        setPrepareError("");
        setPreparing(false);
    };
    const prepare = async (body: object) => {
        asked.current += 1;
        const mine = asked.current;
        setPrepared(null);
        setPrepareError("");
        setPreparing(true);
        try {
            const answer = await prepareResults(body);
            if (asked.current === mine) {
                setPrepared(answer);
            }
        } catch (e) {
            if (asked.current === mine) {
                setPrepareError(messageOf(e));
            }
        } finally {
            if (asked.current === mine) {
                setPreparing(false);
            }
        }
    };
    const loadDays = async () => {
        daysAsked.current += 1;
        const mine = daysAsked.current;
        setDaysError("");
        try {
            const found = (await getData<{days: MatchLogDay[]}>(
                `${BASE}/match-logs`)).days;
            if (daysAsked.current !== mine) {
                return;
            }
            setDays(found);
            setDate(found[0]?.date ?? "");
            setTournament(WHOLE_DAY);
        } catch (e) {
            if (daysAsked.current === mine) {
                setDaysError(messageOf(e));
            }
        }
    };
    const choose = (next: ResultsMode) => {
        daysAsked.current += 1;
        setMode(next);
        setFileError("");
        forget();
        if (next === "matchlog") {
            loadDays();
        }
    };
    const onFile = async (file: File | undefined) => {
        forget();
        // Another choice (or file) while this one is read drops it.
        const mine = asked.current;
        setFileError("");
        if (!file) {
            return;
        }
        let parsed: unknown;
        try {
            parsed = JSON.parse(await file.text());
            if (typeof parsed !== "object" || parsed === null) {
                throw new Error("object expected");
            }
        } catch (e) {
            if (asked.current === mine) {
                setFileError(i18n.t("archive_results_parse_error",
                                    {message: (e as Error).message}));
            }
            return;
        }
        if (asked.current !== mine) {
            return;
        }
        // Checked and given the characters it lacks (the players' mains);
        // the browser cannot (SHELL), and cannot start the job either.
        if (!readOnly) {
            await prepare({kind: "json", results: parsed});
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
                results: mode === "none" ? null : prepared?.results ?? null,
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
    const day = days?.find((each) => each.date === date) ?? null;
    const ready = source.trim() !== ""
        && (mode === "none" || (prepared !== null && !preparing));
    // The event the worker names the sets with: the one typed, else the
    // results' (vodsplit run: --event, else the results' "event").
    const resultsEvent = mode === "none" ? null
        : (prepared?.results as {event?: unknown} | undefined)?.event;
    const effectiveEvent = event.trim() !== "" ? event.trim()
        : typeof resultsEvent === "string" ? resultsEvent : null;
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
                                choose(e.target.value as ResultsMode)}>
                    <FormControlLabel value="none" control={<Radio/>}
                                      label={i18n.t("archive_results_none")}/>
                    <FormControlLabel value="startgg" control={<Radio/>}
                                      label={i18n.t("archive_results_startgg")}/>
                    <FormControlLabel value="matchlog" control={<Radio/>}
                                      label={i18n.t("archive_results_matchlog")}/>
                    <FormControlLabel value="json" control={<Radio/>}
                                      label={i18n.t("archive_results_json")}/>
                </RadioGroup>
                {mode === "startgg" ? (
                    <Stack direction="row" spacing={1} alignItems="center">
                        <TextField size="small" fullWidth value={url}
                                   label={i18n.t("archive_results_startgg_url")}
                                   onChange={(e) => {
                                       setUrl(e.target.value);
                                       forget();
                                   }}/>
                        <Button variant="outlined"
                                disabled={readOnly || preparing
                                          || url.trim() === ""}
                                onClick={() => prepare(
                                    {kind: "startgg", url: url.trim()})}>
                            {i18n.t("archive_results_fetch")}
                        </Button>
                    </Stack>
                ) : null}
                {mode === "matchlog" ? (
                    days === null ? (
                        daysError ? (
                            <Alert severity="error">
                                {i18n.t("archive_results_matchlog_failed",
                                        {message: daysError})}
                            </Alert>
                        ) : <LinearProgress/>
                    ) : days.length === 0 ? (
                        <Typography variant="body2"
                                    data-testid="archive-matchlog-empty"
                                    sx={{color: tokens.textMuted}}>
                            {i18n.t("archive_results_matchlog_empty")}
                        </Typography>
                    ) : (
                        <Stack direction="row" spacing={1} alignItems="center">
                            <FormControl size="small" sx={{minWidth: 180}}>
                                <InputLabel id="archive-matchlog-date">
                                    {i18n.t("archive_results_matchlog_date")}
                                </InputLabel>
                                <Select labelId="archive-matchlog-date"
                                        value={date}
                                        label={i18n.t("archive_results_matchlog_date")}
                                        onChange={(e) => {
                                            setDate(String(e.target.value));
                                            setTournament(WHOLE_DAY);
                                            forget();
                                        }}>
                                    {days.map((each) => (
                                        <MenuItem key={each.date} value={each.date}>
                                            {`${dayText(each.date)} (${each.records})`}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                            <FormControl size="small" sx={{minWidth: 220}}>
                                <InputLabel id="archive-matchlog-tournament">
                                    {i18n.t("archive_results_matchlog_tournament")}
                                </InputLabel>
                                <Select labelId="archive-matchlog-tournament"
                                        value={tournament}
                                        label={i18n.t("archive_results_matchlog_tournament")}
                                        onChange={(e) => {
                                            setTournament(String(e.target.value));
                                            forget();
                                        }}>
                                    <MenuItem value={WHOLE_DAY}>
                                        {`${i18n.t("archive_results_matchlog_all")} (${day?.records ?? 0})`}
                                    </MenuItem>
                                    {(day?.tournaments ?? []).map((each) => (
                                        <MenuItem key={each.name}
                                                  value={NAMED + each.name}>
                                            {`${each.name
                                                || i18n.t("archive_results_matchlog_unnamed")} (${each.records})`}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                            <Button variant="outlined"
                                    disabled={readOnly || preparing || !date}
                                    onClick={() => prepare({
                                        kind: "matchlog", date,
                                        tournament: tournament === WHOLE_DAY
                                            ? null
                                            : tournament.slice(NAMED.length),
                                    })}>
                                {i18n.t("archive_results_load")}
                            </Button>
                        </Stack>
                    )
                ) : null}
                {mode === "json" ? (
                    <input type="file" accept=".json,application/json"
                           aria-label={i18n.t("archive_results_pick")}
                           onChange={(e) => onFile(e.target.files?.[0])}/>
                ) : null}
                {fileError ? <Alert severity="error" sx={{mt: 1}}>
                    {fileError}
                </Alert> : null}
                {preparing ? (
                    <Stack spacing={0.5} sx={{mt: 1}}
                           data-testid="archive-results-preparing">
                        <Typography variant="body2"
                                    sx={{color: tokens.textMuted}}>
                            {i18n.t("archive_results_preparing")}
                        </Typography>
                        <LinearProgress/>
                    </Stack>
                ) : null}
                {prepareError ? <Alert severity="error" sx={{mt: 1}}>
                    {prepareError}
                </Alert> : null}
                {prepared !== null && mode !== "none" ? (
                    <PreparedSummary prepared={prepared} mode={mode}/>
                ) : null}
            </FormControl>
            <Box>
                <TextField size="small" fullWidth value={event}
                           label={i18n.t("archive_event")}
                           onChange={(e) => setEvent(e.target.value)}/>
                <TemplateLine entries={templates} event={effectiveEvent}
                              label={i18n.t("archive_tpl_job_line")}
                              onEdit={onTemplates}/>
            </Box>
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

// A job runs its analysis or its export (A4), or its thumbnails are drawn
// (A6): each is followed.
function isBusy(job: Job): boolean {
    return job.state === "running" || job.export?.state === "running"
        || job.thumbs?.state === "running";
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
    // The job screen, or the title templates' (A6).
    const [view, setView] = React.useState<"jobs" | "templates">("jobs");
    const [templates, setTemplates] = React.useState<TemplateEntry[] | null>(null);
    const [templatesError, setTemplatesError] = React.useState("");
    // The template edits not saved yet, by id (Templates edits them): they
    // outlive the template view.
    const [templateDrafts, setTemplateDrafts] =
        React.useState<Record<string, Template>>({});
    // The worker's update card, opened from the header (A7).
    const [updating, setUpdating] = React.useState(false);
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

    // The templates (THIS_PC: readable in this PC's browser too) -> the list,
    // or null when it could not be read (said on the template view).
    const loadTemplates = React.useCallback(async () => {
        try {
            const data = await getData<{templates: TemplateEntry[]}>(
                `${BASE}/templates`);
            setTemplates(data.templates);
            setTemplatesError("");
            return data.templates;
        } catch (e) {
            setTemplatesError(messageOf(e));
            return null;
        }
    }, []);

    const loadJobs = React.useCallback(async () => {
        const data = await getData<{jobs: JobSummary[]}>(`${BASE}/jobs`);
        // Newest first (the list is shown in this order whatever the server's).
        setJobs([...data.jobs].sort((a, b) =>
            b.created_at.localeCompare(a.created_at)));
    }, []);

    const applyStatus = React.useCallback((data: ArchiveStatus) => {
        setStatus(data);
        if (data.available) {
            loadJobs().catch(() => undefined);
            loadTemplates();
            // A reload while a job runs shows its progress.
            if (data.running_job && selectedRef.current === "new") {
                select(data.running_job);
            }
        }
    }, [loadJobs, loadTemplates, select]);

    React.useEffect(() => {
        let live = true;
        getData<ArchiveStatus>(`${BASE}/status`).then((data) => {
            if (live) {
                applyStatus(data);
            }
        }, () => live && setStatusFailed(true));
        return () => {
            live = false;
        };
    }, [applyStatus]);

    // A worker installed (A7): the status probes it again.
    const reloadStatus = React.useCallback(() => {
        getData<ArchiveStatus>(`${BASE}/status?refresh=true`)
            .then(applyStatus, (e) => setError(messageOf(e)));
    }, [applyStatus]);

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

    const running = !pollFailed && detail && isBusy(detail.job)
        ? detail.job.id : null;
    React.useEffect(() => {
        if (running === null) {
            return undefined;
        }
        const timer = setInterval(() => {
            loadDetail(running).then((data) => {
                if (data !== null && !isBusy(data.job)) {
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
        // "thumbs" draws the confirmed sets' thumbnails (A6); the others are
        // the job's own actions (:cancel, :resume, :export).
        const path = action === "thumbs" ? "/thumbs:generate" : `:${action}`;
        try {
            const job = await postJob(
                `${BASE}/jobs/${encodeURIComponent(id)}${path}`, {});
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
        return statusFailed ? <Unavailable/> : null;
    }
    if (!status.available) {
        return (
            <>
                {error ? <Alert severity="error" onClose={() => setError("")}>
                    {error}
                </Alert> : null}
                <WorkerInstall update={false} reason={status.reason}
                               readOnly={readOnly} onInstalled={reloadStatus}/>
            </>
        );
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
                <Button variant="outlined" onClick={() => {
                    setView("jobs");
                    select("new");
                }}>
                    {i18n.t("archive_new_job")}
                </Button>
                <Button variant={view === "templates" ? "contained" : "outlined"}
                        data-testid="archive-templates-button"
                        onClick={() => setView(
                            view === "templates" ? "jobs" : "templates")}>
                    {i18n.t("archive_templates")}
                </Button>
                {view === "jobs" && selected !== "new" && sets ? (
                    <Stack direction="row" spacing={1}>
                        <Chip label={`${i18n.t("archive_chip_sets")} ${summary.sets}`}/>
                        <Chip label={`${i18n.t("archive_chip_matched")} ${summary.matched}`}/>
                        <Chip label={`${i18n.t("archive_chip_review")} ${summary.review}`}
                              color={summary.review > 0 ? "error" : "default"}/>
                        <Chip label={`${i18n.t("archive_chip_games")} ${summary.games}`}/>
                    </Stack>
                ) : null}
                <Box sx={{flex: 1}}/>
                <WorkerChips key={status.version ?? ""} version={status.version}
                             onUpdate={() => setUpdating(true)}/>
            </Stack>
            {updating ? (
                <WorkerInstall update readOnly={readOnly}
                               onInstalled={reloadStatus}
                               onClose={() => setUpdating(false)}/>
            ) : null}
            {readOnly ? (
                <Alert severity="info" data-testid="archive-read-only">
                    {i18n.t("archive_read_only")}
                </Alert>
            ) : null}
            {error ? <Alert severity="error" onClose={() => setError("")}>
                {error}
            </Alert> : null}
            {view === "templates" ? (
                <Templates entries={templates} loadError={templatesError}
                           onReload={loadTemplates}
                           drafts={templateDrafts}
                           setDrafts={setTemplateDrafts} status={status}
                           onStatus={setStatus} readOnly={readOnly}
                           jobId={detail?.job.state === "done" && sets
                               ? detail.job.id : null}
                           sets={sets} setNo={setNo}
                           onBack={() => setView("jobs")}/>
            ) : null}
            {/* Hidden, not unmounted, under the templates' view: the form's
                input and the review screen are as they were on the way back. */}
            <Box data-testid="archive-jobs-view"
                 sx={{flex: 1, minHeight: 0, flexDirection: "column"}}
                 style={{display: view === "templates" ? "none" : "flex"}}>
                {selected === "new" ? (
                    <NewJobForm status={status} readOnly={readOnly}
                                templates={templates}
                                onTemplates={() => setView("templates")}
                                onStatus={setStatus}
                                onStarted={(job) => {
                                    setJobs((known) => [job, ...known]);
                                    // Left for another job meanwhile: stay
                                    // there.
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
                                      onSetNo={setSetNo} readOnly={readOnly}
                                      onAct={act} templates={templates}
                                      onTemplates={() => setView("templates")}
                                      hidden={view === "templates"}/>
                        ) : null}
                    </Box>
                )}
            </Box>
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
