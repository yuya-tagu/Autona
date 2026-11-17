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
    MenuItem,
    Radio,
    RadioGroup,
    Select,
    Stack,
    Tab,
    Tabs,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {DARK_TOKENS as tokens} from "../themes";
import {apiDelete, apiPatch, apiPost, apiPut, ApiError} from "../api";
import {navigateToPanel} from "../PanelShell/hostApi";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {ArchiveStatus, SetsJson} from "./archiveSets";
import {
    AliasRow,
    DEFAULT_ID,
    DESCRIPTION_LIMIT,
    DESCRIPTION_VARIABLES,
    MAX_ALIAS,
    MAX_CHAPTER_FORMAT,
    MAX_DESCRIPTION,
    MAX_MATCH,
    MAX_NAME,
    MAX_ROUND,
    MAX_TITLE,
    ORDERS,
    OVERFLOWS,
    Order,
    Overflow,
    TITLE_LIMIT,
    TITLE_VARIABLES,
    Template,
    TemplateEntry,
    TitlePreview,
    aliasRows,
    forSaving,
    insertAt,
    roundKeys,
    sameTemplate,
    selectTemplate,
    textLength,
    withAliases,
    withRound,
} from "./archiveTemplates";

// The archive page's template view (ADR-006 A6): the title templates the
// worker names the clips with (core/ArchiveTemplates.py), in the order it
// picks from, each edited here -- its title, description and naming rules
// -- and previewed with the selected job's sets (the worker's `titles`,
// asked again a moment after the edits stop). The edits are kept per
// template by the page (`drafts`: switching templates or leaving this view
// does not lose one), saved with Save; the fields wait while a save runs.
// The thumbnail tab leads to the thumbnail settings and picks the background
// of the job's thumbnails. Outside the desktop's window it only reads (the
// writes and the preview are SHELL routes).

const BASE = "/api/v1/archive";
// The preview is asked this long after the last edit.
const PREVIEW_DELAY_MS = 600;
// The server answers within 12 s (TITLES_DEADLINE_SECS: the worker's probe
// and its run).
const PREVIEW_TIMEOUT_MS = 15000;

type TabName = "title" | "description" | "rules" | "thumbnail";

function messageOf(error: unknown): string {
    return (error as ApiError | null)?.detail
        ?? i18n.t("archive_request_failed");
}

async function entryOf(text: Promise<string>): Promise<TemplateEntry | null> {
    return (JSON.parse(await text) as {data: {template: TemplateEntry | null}})
        .data.template;
}

function roleText(entry: TemplateEntry): string {
    if (entry.role === "match") {
        return i18n.t("archive_tpl_role_match", {match: entry.template.match});
    }
    return i18n.t(`archive_tpl_role_${entry.role}`);
}

// The template the worker uses for an event, with why: for the new job's
// form and the job view.
export function TemplateLine({entries, event, label, onEdit}: {
    entries: TemplateEntry[] | null;
    event: string | null | undefined;
    label: string;
    onEdit: () => void;
}) {
    const chosen = entries === null ? null : selectTemplate(entries, event);
    return (
        <Stack direction="row" spacing={1} alignItems="center"
               data-testid="archive-template-line">
            <Typography variant="body2" sx={{color: tokens.textSecondary}}>
                {chosen === null ? `${label}: —`
                    : `${label}: ${chosen.template.name}（${roleText(chosen)}）`}
            </Typography>
            <Button size="small" onClick={onEdit}>
                {i18n.t("archive_tpl_edit")}
            </Button>
        </Stack>
    );
}

// A template the worker never takes (another comes before it).
function idle(entry: TemplateEntry): boolean {
    return entry.role === "unused" || entry.role === "shadowed";
}

function TemplateList({entries, selectedId, jobTemplate, dirty, onSelect}: {
    entries: TemplateEntry[];
    selectedId: string | null;
    jobTemplate: string | null;
    dirty: (id: string) => boolean;
    onSelect: (id: string) => void;
}) {
    return (
        <Box sx={{overflowY: "auto", minHeight: 0}}>
            {entries.map((entry) => (
                <Box key={entry.id} role="button" tabIndex={0}
                     data-testid={`archive-template-${entry.id}`}
                     aria-current={entry.id === selectedId || undefined}
                     onClick={() => onSelect(entry.id)}
                     onKeyDown={(event) => {
                         if (event.key === "Enter" || event.key === " ") {
                             event.preventDefault();
                             onSelect(entry.id);
                         }
                     }}
                     sx={{p: 1, cursor: "pointer", borderBottom: "1px solid",
                          borderColor: "divider",
                          bgcolor: entry.id === selectedId
                              ? tokens.bgTertiary : "transparent",
                          "&:hover": {bgcolor: tokens.bgTertiary}}}>
                    <Typography variant="body2" fontWeight={700}
                                sx={{wordBreak: "break-word"}}>
                        {entry.template.name}
                        {entry.stored ? "" : ` ${i18n.t("archive_tpl_not_stored")}`}
                    </Typography>
                    <Stack direction="row" spacing={0.5} sx={{mt: 0.5,
                           flexWrap: "wrap", rowGap: 0.5}}>
                        <Tooltip title={idle(entry)
                            ? i18n.t(`archive_tpl_role_${entry.role}_hint`)
                            : ""}>
                            <Chip size="small" label={roleText(entry)}
                                  data-testid={`archive-template-role-${entry.id}`}
                                  color={idle(entry) ? "default" : "primary"}
                                  variant="outlined"/>
                        </Tooltip>
                        {entry.id === jobTemplate ? (
                            <Chip size="small" color="success"
                                  label={i18n.t("archive_tpl_for_job")}/>
                        ) : null}
                        {dirty(entry.id) ? (
                            <Chip size="small" color="warning"
                                  label={i18n.t("archive_tpl_unsaved")}/>
                        ) : null}
                    </Stack>
                </Box>
            ))}
        </Box>
    );
}

// Chips that put a variable into the field `target` names, where its caret
// is (the field keeps its focus: a chip takes no focus on press).
function VariableChips({variables, onInsert}: {
    variables: string[];
    onInsert: (text: string) => void;
}) {
    return (
        <Box>
            <Typography variant="caption" sx={{color: tokens.textMuted}}>
                {i18n.t("archive_tpl_variables")}
            </Typography>
            <Box sx={{display: "flex", flexWrap: "wrap", gap: 0.5, mt: 0.5}}>
                {variables.map((name) => (
                    <Tooltip key={name} title={i18n.t(`archive_tpl_var_${name}`)}>
                        <Chip size="small" label={`{${name}}`}
                              data-testid={`archive-tpl-var-${name}`}
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => onInsert(`{${name}}`)}/>
                    </Tooltip>
                ))}
            </Box>
        </Box>
    );
}

// A text field whose variables are put in at its caret.
function useCaretInsert(value: string, onChange: (next: string) => void) {
    const ref = React.useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
    const insert = (text: string) => {
        const input = ref.current;
        const made = insertAt(value, input?.selectionStart,
                              input?.selectionEnd, text);
        onChange(made.text);
        // After React has put the new value in.
        window.setTimeout(() => {
            if (ref.current) {
                ref.current.focus();
                ref.current.setSelectionRange(made.caret, made.caret);
            }
        }, 0);
    };
    return {ref, insert};
}

function TitleTab({draft, readOnly, onChange}: {
    draft: Template;
    readOnly: boolean;
    onChange: (next: Template) => void;
}) {
    const title = useCaretInsert(draft.title,
                                 (next) => onChange({...draft, title: next}));
    return (
        <Stack spacing={2}>
            <TextField size="small" fullWidth label={i18n.t("archive_tpl_tab_title")}
                       value={draft.title} inputRef={title.ref}
                       inputProps={{maxLength: MAX_TITLE, readOnly,
                                    "data-testid": "archive-tpl-title"}}
                       onChange={(e) => onChange({...draft, title: e.target.value})}/>
            <VariableChips variables={TITLE_VARIABLES} onInsert={title.insert}/>
            <Typography variant="caption" sx={{color: tokens.textMuted}}>
                {i18n.t("archive_tpl_title_hint")}
            </Typography>
            <FormControl size="small" sx={{maxWidth: 420}}>
                <InputLabel id="archive-tpl-overflow">
                    {i18n.t("archive_tpl_overflow")}
                </InputLabel>
                <Select labelId="archive-tpl-overflow" value={draft.overflow}
                        label={i18n.t("archive_tpl_overflow")}
                        inputProps={{readOnly}}
                        onChange={(e) => onChange(
                            {...draft, overflow: e.target.value as Overflow})}>
                    {OVERFLOWS.map((name) => (
                        <MenuItem key={name} value={name}>
                            {i18n.t(`archive_tpl_overflow_${name}`)}
                        </MenuItem>
                    ))}
                </Select>
            </FormControl>
        </Stack>
    );
}

function DescriptionTab({draft, readOnly, onChange}: {
    draft: Template;
    readOnly: boolean;
    onChange: (next: Template) => void;
}) {
    const description = useCaretInsert(
        draft.description, (next) => onChange({...draft, description: next}));
    return (
        <Stack spacing={2}>
            <TextField size="small" fullWidth multiline minRows={10}
                       label={i18n.t("archive_tpl_tab_description")}
                       value={draft.description} inputRef={description.ref}
                       inputProps={{maxLength: MAX_DESCRIPTION, readOnly,
                                    "data-testid": "archive-tpl-description"}}
                       onChange={(e) => onChange(
                           {...draft, description: e.target.value})}/>
            <VariableChips variables={DESCRIPTION_VARIABLES}
                           onInsert={description.insert}/>
            <TextField size="small" sx={{maxWidth: 420}}
                       label={i18n.t("archive_tpl_chapter_format")}
                       value={draft.chapter_format}
                       inputProps={{maxLength: MAX_CHAPTER_FORMAT, readOnly}}
                       helperText={i18n.t("archive_tpl_chapter_hint")}
                       onChange={(e) => onChange(
                           {...draft, chapter_format: e.target.value})}/>
        </Stack>
    );
}

// The naming rules: the players' order, the rounds' other names, the names
// replaced. The rounds shown stay for the editor's life (a round whose names
// are cleared stays a row); the alias rows are the editor's own (a row
// being typed is saved once both sides are there).
function RulesTab({draft, sets, readOnly, onChange}: {
    draft: Template;
    sets: SetsJson | null;
    readOnly: boolean;
    onChange: (next: Template) => void;
}) {
    const [rounds, setRounds] = React.useState<string[]>(
        () => roundKeys(draft, sets));
    const [newRound, setNewRound] = React.useState("");
    const [aliases, setAliases] = React.useState<AliasRow[]>(
        () => aliasRows(draft));
    // Another job's rounds join the list.
    React.useEffect(() => {
        setRounds((shown) => {
            const more = roundKeys(draft, sets).filter((k) => !shown.includes(k));
            return more.length > 0 ? [...shown, ...more] : shown;
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sets]);
    const changeAliases = (rows: AliasRow[]) => {
        setAliases(rows);
        onChange(withAliases(draft, rows));
    };
    const addRound = () => {
        const round = newRound.trim();
        if (round !== "" && !rounds.includes(round)) {
            setRounds([...rounds, round]);
        }
        setNewRound("");
    };
    return (
        <Stack spacing={2}>
            <FormControl>
                <Typography variant="caption" sx={{color: tokens.textMuted}}>
                    {i18n.t("archive_tpl_order")}
                </Typography>
                <RadioGroup row value={draft.order}
                            onChange={(e) => !readOnly && onChange(
                                {...draft, order: e.target.value as Order})}>
                    {ORDERS.map((name) => (
                        <FormControlLabel key={name} value={name}
                                          control={<Radio size="small"/>}
                                          disabled={readOnly}
                                          label={i18n.t(`archive_tpl_order_${name}`)}/>
                    ))}
                </RadioGroup>
            </FormControl>
            <Box>
                <Typography variant="subtitle2">
                    {i18n.t("archive_tpl_rounds")}
                </Typography>
                {rounds.length === 0 ? (
                    <Typography variant="body2" sx={{color: tokens.textMuted}}>
                        {i18n.t("archive_tpl_rounds_empty")}
                    </Typography>
                ) : null}
                <Box sx={{display: "grid", gap: 1, mt: 1, alignItems: "center",
                          gridTemplateColumns: "minmax(120px, 1fr) 1fr 1fr"}}>
                    {rounds.map((round) => {
                        const names = draft.round_map?.[round] ?? {};
                        return (
                            <React.Fragment key={round}>
                                <Typography variant="body2"
                                            data-testid="archive-tpl-round"
                                            sx={{wordBreak: "break-word"}}>
                                    {round}
                                </Typography>
                                <TextField size="small"
                                           label={i18n.t("archive_tpl_round_ja")}
                                           value={names.ja ?? ""}
                                           inputProps={{maxLength: MAX_ROUND,
                                                        readOnly}}
                                           onChange={(e) => onChange(withRound(
                                               draft, round,
                                               {...names, ja: e.target.value}))}/>
                                <TextField size="small"
                                           label={i18n.t("archive_tpl_round_short")}
                                           value={names.short ?? ""}
                                           inputProps={{maxLength: MAX_ROUND,
                                                        readOnly}}
                                           onChange={(e) => onChange(withRound(
                                               draft, round,
                                               {...names, short: e.target.value}))}/>
                            </React.Fragment>
                        );
                    })}
                </Box>
                <Stack direction="row" spacing={1} sx={{mt: 1}}>
                    <TextField size="small" value={newRound}
                               label={i18n.t("archive_tpl_round_new")}
                               inputProps={{maxLength: MAX_ROUND, readOnly}}
                               onChange={(e) => setNewRound(e.target.value)}
                               onKeyDown={(e) => {
                                   if (e.key === "Enter") {
                                       e.preventDefault();
                                       addRound();
                                   }
                               }}/>
                    <Button size="small" disabled={readOnly
                                                   || newRound.trim() === ""}
                            onClick={addRound}>
                        {i18n.t("archive_tpl_round_add")}
                    </Button>
                </Stack>
            </Box>
            <Box>
                <Typography variant="subtitle2">
                    {i18n.t("archive_tpl_aliases")}
                </Typography>
                <Stack spacing={1} sx={{mt: 1}}>
                    {aliases.map(([from, to], index) => (
                        <Stack key={index} direction="row" spacing={1}
                               alignItems="center"
                               data-testid="archive-tpl-alias">
                            <TextField size="small" value={from}
                                       label={i18n.t("archive_tpl_alias_from")}
                                       inputProps={{maxLength: MAX_ROUND, readOnly}}
                                       onChange={(e) => changeAliases(aliases.map(
                                           (row, i) => i === index
                                               ? [e.target.value, row[1]] : row))}/>
                            <span>→</span>
                            <TextField size="small" value={to}
                                       label={i18n.t("archive_tpl_alias_to")}
                                       inputProps={{maxLength: MAX_ALIAS, readOnly}}
                                       onChange={(e) => changeAliases(aliases.map(
                                           (row, i) => i === index
                                               ? [row[0], e.target.value] : row))}/>
                            <Button size="small" disabled={readOnly}
                                    onClick={() => changeAliases(
                                        aliases.filter((_row, i) => i !== index))}>
                                {i18n.t("archive_tpl_alias_remove")}
                            </Button>
                        </Stack>
                    ))}
                    <Box>
                        <Button size="small" disabled={readOnly}
                                onClick={() => setAliases([...aliases, ["", ""]])}>
                            {i18n.t("archive_tpl_alias_add")}
                        </Button>
                    </Box>
                </Stack>
            </Box>
        </Stack>
    );
}

// The thumbnails' look is the thumbnail settings'; the background of a set's
// thumbnail is picked here (archive.thumbnail_background).
function ThumbnailTab({status, readOnly, onStatus}: {
    status: ArchiveStatus;
    readOnly: boolean;
    onStatus: (status: ArchiveStatus) => void;
}) {
    const [error, setError] = React.useState("");
    // The choice shown while it is saved (back to the saved one if refused).
    const [chosen, setChosen] = React.useState<string | null>(null);
    const choose = async (value: string) => {
        setError("");
        setChosen(value);
        try {
            const text = await apiPatch(`${BASE}/settings`,
                                        {thumbnail_background: value});
            onStatus((JSON.parse(text) as {data: ArchiveStatus}).data);
        } catch (e) {
            setError(messageOf(e));
        } finally {
            setChosen(null);
        }
    };
    return (
        <Stack spacing={2}>
            <Typography variant="body2">
                {i18n.t("archive_tpl_thumb_body")}
            </Typography>
            <Box>
                <Button variant="outlined" size="small"
                        onClick={() => navigateToPanel("thumbnail")}>
                    {i18n.t("archive_tpl_thumb_open")}
                </Button>
            </Box>
            <FormControl>
                <Typography variant="caption" sx={{color: tokens.textMuted}}>
                    {i18n.t("archive_tpl_thumb_background")}
                </Typography>
                <RadioGroup value={chosen ?? status.thumbnail_background ?? "frame"}
                            data-testid="archive-thumb-background"
                            onChange={(e) => choose(e.target.value)}>
                    <FormControlLabel value="frame" disabled={readOnly}
                                      control={<Radio size="small"/>}
                                      label={i18n.t("archive_tpl_thumb_frame")}/>
                    <FormControlLabel value="settings" disabled={readOnly}
                                      control={<Radio size="small"/>}
                                      label={i18n.t("archive_tpl_thumb_settings")}/>
                </RadioGroup>
                <Typography variant="caption" sx={{color: tokens.textMuted}}>
                    {i18n.t("archive_tpl_thumb_frame_hint")}
                </Typography>
            </FormControl>
            {error ? <Alert severity="error">{error}</Alert> : null}
        </Stack>
    );
}

function Count({value, limit}: {value: number; limit: number}) {
    return (
        <Box component="span" sx={{color: value >= limit ? tokens.negative
                                       : tokens.textMuted, ml: 1}}>
            {value} / {limit}
        </Box>
    );
}

// The draft's titles for the selected job's sets, asked a moment after the
// last edit (the latest asked only: an earlier answer is dropped).
function PreviewPane({draft, jobId, status, readOnly, initialNo}: {
    draft: Template;
    jobId: string | null;
    status: ArchiveStatus;
    readOnly: boolean;
    initialNo: number | null;
}) {
    const [preview, setPreview] = React.useState<TitlePreview | null>(null);
    const [error, setError] = React.useState("");
    const [loading, setLoading] = React.useState(false);
    const [no, setNo] = React.useState<number | null>(initialNo);
    const asked = React.useRef(0);
    const supported = (status.features ?? []).includes("titles");
    const usable = supported && !readOnly && jobId !== null;
    const body = JSON.stringify(forSaving(draft));

    React.useEffect(() => {
        asked.current += 1;
        if (!usable || jobId === null) {
            setPreview(null);
            setError("");
            setLoading(false);
            return undefined;
        }
        const mine = asked.current;
        setLoading(true);
        const timer = window.setTimeout(async () => {
            try {
                const text = await apiPost(
                    `${BASE}/jobs/${encodeURIComponent(jobId)}/titles:preview`,
                    {template: JSON.parse(body)},
                    {timeoutMs: PREVIEW_TIMEOUT_MS});
                if (asked.current === mine) {
                    setPreview((JSON.parse(text) as {data: TitlePreview}).data);
                    setError("");
                }
            } catch (e) {
                if (asked.current === mine) {
                    setError(messageOf(e));
                }
            } finally {
                if (asked.current === mine) {
                    setLoading(false);
                }
            }
        }, PREVIEW_DELAY_MS);
        return () => {
            asked.current += 1;
            window.clearTimeout(timer);
        };
    }, [usable, jobId, body]);
    // Another job: its own sets.
    React.useEffect(() => {
        setPreview(null);
        setError("");
        setNo(initialNo);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jobId]);

    let content: React.ReactNode;
    if (!supported) {
        content = <Alert severity="info" data-testid="archive-preview-unsupported">
            {i18n.t("archive_tpl_preview_unsupported")}
        </Alert>;
    } else if (readOnly) {
        content = <Alert severity="info">
            {i18n.t("archive_tpl_preview_read_only")}
        </Alert>;
    } else if (jobId === null) {
        content = <Alert severity="info">
            {i18n.t("archive_tpl_preview_pick_job")}
        </Alert>;
    } else {
        const rows = (preview?.sets ?? []).filter((row) => row.title !== null);
        const row = rows.find((r) => r.no === no) ?? rows[0] ?? null;
        content = (
            <Stack spacing={1}>
                {error ? (
                    <Alert severity="error" data-testid="archive-preview-error">
                        {i18n.t("archive_tpl_preview_failed", {message: error})}
                    </Alert>
                ) : null}
                {loading ? <LinearProgress/> : null}
                {preview !== null && rows.length === 0 ? (
                    <Typography variant="body2" sx={{color: tokens.textMuted}}>
                        {i18n.t("archive_tpl_preview_no_result")}
                    </Typography>
                ) : null}
                {rows.length > 0 && row !== null ? (
                    <>
                        <FormControl size="small" fullWidth>
                            <InputLabel id="archive-preview-set">
                                {i18n.t("archive_tpl_preview_set")}
                            </InputLabel>
                            <Select labelId="archive-preview-set"
                                    label={i18n.t("archive_tpl_preview_set")}
                                    value={String(row.no)}
                                    onChange={(e) => setNo(Number(e.target.value))}>
                                {rows.map((r) => (
                                    <MenuItem key={r.no} value={String(r.no)}>
                                        #{r.no} {r.title}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                        <Typography variant="caption" sx={{color: tokens.textMuted}}>
                            {i18n.t("archive_tpl_tab_title")}
                            <Count value={textLength(row.title)} limit={TITLE_LIMIT}/>
                        </Typography>
                        <Box data-testid="archive-preview-title"
                             sx={{p: 1, borderRadius: 1, bgcolor: tokens.bgSecondary,
                                  fontWeight: 700, wordBreak: "break-word"}}>
                            {row.title}
                        </Box>
                        <Typography variant="caption" sx={{color: tokens.textMuted}}>
                            {i18n.t("archive_tpl_tab_description")}
                            <Count value={textLength(row.description)}
                                   limit={DESCRIPTION_LIMIT}/>
                        </Typography>
                        <Box data-testid="archive-preview-description"
                             sx={{p: 1, borderRadius: 1, bgcolor: tokens.bgSecondary,
                                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                                  fontSize: "0.8rem"}}>
                            {row.description}
                        </Box>
                        <Typography variant="caption"
                                    data-testid="archive-preview-chapters"
                                    sx={{color: tokens.textMuted}}>
                            {row.chapters > 0
                                ? i18n.t("archive_tpl_preview_chapters",
                                         {count: row.chapters})
                                : i18n.t("archive_tpl_preview_no_chapters")}
                        </Typography>
                    </>
                ) : null}
            </Stack>
        );
    }
    return (
        <Stack spacing={1} sx={{p: 2, overflowY: "auto", minHeight: 0}}
               data-testid="archive-preview-pane">
            <Typography variant="subtitle2">
                {i18n.t("archive_tpl_preview")}
            </Typography>
            {content}
        </Stack>
    );
}

export default function Templates({entries, loadError, onReload, drafts,
                                   setDrafts, status, onStatus, readOnly,
                                   jobId, sets, setNo, onBack}: {
    entries: TemplateEntry[] | null;
    loadError: string;
    // Reads the list again -> it (null when that failed: loadError says
    // why, with a retry).
    onReload: () => Promise<TemplateEntry[] | null>;
    // The edits not saved yet, by template id: the page's, so leaving this
    // view keeps them.
    drafts: Record<string, Template>;
    setDrafts: React.Dispatch<React.SetStateAction<Record<string, Template>>>;
    status: ArchiveStatus;
    onStatus: (status: ArchiveStatus) => void;
    readOnly: boolean;
    // The selected job when its analysis is done (the preview's sets).
    jobId: string | null;
    sets: SetsJson | null;
    setNo: number | null;
    onBack: () => void;
}) {
    const jobTemplate = entries === null ? null
        : selectTemplate(entries, sets?.event)?.id ?? null;
    const [selectedId, setSelectedId] = React.useState<string | null>(
        () => jobTemplate ?? entries?.[0]?.id ?? null);
    // Bumped when a template's draft is replaced from the server (saved,
    // reset): its editor starts again from it.
    const [generation, setGeneration] = React.useState(0);
    const [tab, setTab] = React.useState<TabName>("title");
    const [busy, setBusy] = React.useState(false);
    const [error, setError] = React.useState("");
    const [notice, setNotice] = React.useState("");
    const [confirming, setConfirming] = React.useState(false);
    const [retrying, setRetrying] = React.useState(false);
    // The fields wait while a save (or another write) runs: what is sent is
    // what the draft is when it comes back.
    const locked = readOnly || busy;

    // The list came (or the selected one went): pick one.
    React.useEffect(() => {
        if (entries !== null && !entries.some((e) => e.id === selectedId)) {
            setSelectedId(jobTemplate ?? entries[0]?.id ?? null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [entries]);

    const entry = entries?.find((e) => e.id === selectedId) ?? null;
    const dirty = (id: string) => {
        const saved = entries?.find((e) => e.id === id);
        return Boolean(drafts[id] && saved
                       && !sameTemplate(drafts[id], saved.template));
    };
    const draft = entry === null ? null : drafts[entry.id] ?? entry.template;
    const setDraft = (next: Template) => {
        if (entry !== null) {
            setDrafts((known) => ({...known, [entry.id]: next}));
            setNotice("");
        }
    };
    // Drop template `id`'s draft -- only while it is still `sent` (the one
    // saved), when given.
    const forget = (id: string, sent?: Template) => {
        setDrafts((known) => {
            if (!(id in known) || (sent !== undefined
                                   && !sameTemplate(known[id], sent))) {
                return known;
            }
            const next = {...known};
            delete next[id];
            return next;
        });
        setGeneration((n) => n + 1);
    };
    const run = async (work: () => Promise<void>) => {
        setBusy(true);
        setError("");
        setNotice("");
        try {
            await work();
        } catch (e) {
            setError(messageOf(e));
        } finally {
            setBusy(false);
        }
    };
    const path = (id: string) => `${BASE}/templates/${encodeURIComponent(id)}`;
    const save = () => entry !== null && draft !== null && run(async () => {
        const sent = draft;
        await entryOf(apiPut(path(entry.id), {template: forSaving(sent)}));
        setNotice(i18n.t("archive_tpl_saved"));
        // The draft stays until the list has the saved one (else the editor
        // would show the old one): a list that could not be read is retried.
        if (await onReload() !== null) {
            forget(entry.id, sent);
        }
    });
    const duplicate = () => entry !== null && run(async () => {
        const made = await entryOf(apiPost(`${path(entry.id)}:duplicate`, {}));
        // Opened once the list has it (a list that could not be read is
        // retried, the copy then in it).
        if (await onReload() !== null && made !== null) {
            setSelectedId(made.id);
        }
    });
    const retry = async () => {
        setRetrying(true);
        try {
            await onReload();
        } finally {
            setRetrying(false);
        }
    };
    const remove = () => entry !== null && run(async () => {
        setConfirming(false);
        await apiDelete(path(entry.id));
        await onReload();
        forget(entry.id);
        if (entry.id !== DEFAULT_ID) {
            setSelectedId(DEFAULT_ID);
        }
    });

    const isDefault = entry?.id === DEFAULT_ID;
    return (
        <Box sx={{flex: 1, minHeight: 0, display: "flex",
                  flexDirection: "column"}} data-testid="archive-templates">
            <Stack direction="row" spacing={2} alignItems="center"
                   sx={{px: 2, py: 1, borderBottom: "1px solid",
                        borderColor: "divider"}}>
                <Button size="small" onClick={onBack}>
                    ← {i18n.t("archive_tpl_back")}
                </Button>
                <Typography variant="subtitle1" fontWeight={700}>
                    {i18n.t("archive_tpl_heading")}
                </Typography>
            </Stack>
            {loadError ? (
                <Alert severity="error" data-testid="archive-tpl-load-error"
                       action={(
                           <Button color="inherit" size="small"
                                   data-testid="archive-tpl-retry"
                                   disabled={retrying} onClick={retry}>
                               {i18n.t("archive_tpl_retry")}
                           </Button>
                       )}>
                    {i18n.t("archive_tpl_load_failed", {message: loadError})}
                </Alert>
            ) : null}
            {entries === null ? (loadError ? null : <LinearProgress/>) : (
                <Box sx={{flex: 1, minHeight: 0, display: "grid",
                          gridTemplateColumns: "260px minmax(0, 1fr) 360px"}}>
                    <Box sx={{display: "flex", flexDirection: "column",
                              minHeight: 0, borderRight: "1px solid",
                              borderColor: "divider"}}>
                        <TemplateList entries={entries} selectedId={selectedId}
                                      jobTemplate={jobTemplate} dirty={dirty}
                                      onSelect={(id) => {
                                          setSelectedId(id);
                                          setError("");
                                          setNotice("");
                                      }}/>
                        <Stack direction="row" spacing={1} sx={{p: 1,
                               borderTop: "1px solid", borderColor: "divider"}}>
                            <Tooltip title={entry !== null && dirty(entry.id)
                                ? i18n.t("archive_tpl_duplicate_dirty") : ""}>
                                <span>
                                    <Button size="small" variant="outlined"
                                            data-testid="archive-tpl-duplicate"
                                            disabled={readOnly || busy
                                                      || entry === null
                                                      || dirty(entry.id)}
                                            onClick={duplicate}>
                                        {i18n.t("archive_tpl_duplicate")}
                                    </Button>
                                </span>
                            </Tooltip>
                            <Button size="small" color="error"
                                    data-testid="archive-tpl-delete"
                                    disabled={readOnly || busy || entry === null
                                              || (isDefault && !entry.stored)}
                                    onClick={() => setConfirming(true)}>
                                {isDefault ? i18n.t("archive_tpl_reset")
                                    : i18n.t("archive_tpl_delete")}
                            </Button>
                        </Stack>
                    </Box>
                    {entry !== null && draft !== null ? (
                        <Stack key={`${entry.id}:${generation}`} spacing={2}
                               sx={{p: 2, overflowY: "auto", minHeight: 0}}>
                            <Stack direction="row" spacing={1} alignItems="center">
                                <Button variant="contained" size="small"
                                        data-testid="archive-tpl-save"
                                        disabled={readOnly || busy
                                                  || !dirty(entry.id)}
                                        onClick={save}>
                                    {i18n.t("archive_tpl_save")}
                                </Button>
                                <Button size="small"
                                        disabled={busy || !dirty(entry.id)}
                                        onClick={() => forget(entry.id)}>
                                    {i18n.t("archive_tpl_revert")}
                                </Button>
                                <Typography variant="caption"
                                            data-testid="archive-tpl-notice"
                                            sx={{color: tokens.positive}}>
                                    {notice}
                                </Typography>
                            </Stack>
                            {error ? (
                                <Alert severity="error"
                                       data-testid="archive-tpl-error">
                                    {error}
                                </Alert>
                            ) : null}
                            <TextField size="small" fullWidth
                                       label={i18n.t("archive_tpl_name")}
                                       value={draft.name}
                                       inputProps={{maxLength: MAX_NAME,
                                                    readOnly: locked,
                                                    "data-testid": "archive-tpl-name"}}
                                       onChange={(e) => setDraft(
                                           {...draft, name: e.target.value})}/>
                            <TextField size="small" fullWidth
                                       label={i18n.t("archive_tpl_match")}
                                       value={draft.match ?? ""}
                                       disabled={isDefault}
                                       helperText={isDefault
                                           ? i18n.t("archive_tpl_match_default")
                                           : undefined}
                                       inputProps={{maxLength: MAX_MATCH,
                                                    readOnly: locked,
                                                    "data-testid": "archive-tpl-match"}}
                                       onChange={(e) => setDraft(
                                           {...draft, match: e.target.value})}/>
                            <Tabs value={tab} onChange={(_e, next) => setTab(next)}
                                  variant="scrollable">
                                {(["title", "description", "rules",
                                   "thumbnail"] as TabName[]).map((name) => (
                                    <Tab key={name} value={name}
                                         label={i18n.t(`archive_tpl_tab_${name}`)}/>
                                ))}
                            </Tabs>
                            {tab === "title" ? (
                                <TitleTab draft={draft} readOnly={locked}
                                          onChange={setDraft}/>
                            ) : tab === "description" ? (
                                <DescriptionTab draft={draft} readOnly={locked}
                                                onChange={setDraft}/>
                            ) : tab === "rules" ? (
                                <RulesTab draft={draft} sets={sets}
                                          readOnly={locked}
                                          onChange={setDraft}/>
                            ) : (
                                <ThumbnailTab status={status} readOnly={readOnly}
                                              onStatus={onStatus}/>
                            )}
                        </Stack>
                    ) : <Box/>}
                    <Box sx={{borderLeft: "1px solid", borderColor: "divider",
                              minHeight: 0, display: "flex",
                              flexDirection: "column"}}>
                        {draft !== null ? (
                            <PreviewPane draft={draft} jobId={jobId}
                                         status={status} readOnly={readOnly}
                                         initialNo={setNo}/>
                        ) : null}
                    </Box>
                </Box>
            )}
            {entry !== null ? (
                <ConfirmDialog
                    open={confirming}
                    title={isDefault ? i18n.t("archive_tpl_reset_title")
                        : i18n.t("archive_tpl_delete_title")}
                    message={isDefault ? i18n.t("archive_tpl_reset_confirm")
                        : i18n.t("archive_tpl_delete_confirm",
                                 {name: entry.template.name})}
                    confirm={isDefault ? i18n.t("archive_tpl_reset")
                        : i18n.t("archive_tpl_delete")}
                    onConfirm={remove}
                    onClose={() => setConfirming(false)}
                />
            ) : null}
        </Box>
    );
}
