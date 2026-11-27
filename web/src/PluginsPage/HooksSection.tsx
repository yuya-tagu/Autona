import React from "react";
import {
    Alert,
    Box,
    Button,
    Chip,
    LinearProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Stack,
    Switch,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {CardFrame, Note} from "../GuidePages/parts";
import {PluginsApi} from "./pluginsApi";
import {
    HookHistoryEntry,
    HookRow,
    PluginDetail,
    PluginJob,
} from "./pluginTypes";
import {pluginText, startedText} from "./pluginText";
import FieldForm from "./FieldForm";
import {
    FormValues,
    collectValues,
    fieldKind,
    initialValues,
    validateAll,
} from "./formFields";
import RunView, {
    pickErrorText,
    refusalText,
    usePolledRun,
} from "./RunView";

// The hooks of a plugin's jobs (ADR-009 D5, appendix D.3-D.7): one row per
// job hook with its switch, why it is off, what is pending, the last runs and
// the last failed run's log. Enabling asks for the job's value fields first.
// The detail view is SHELL-only, so this is too.

const POLL_MS = 5000;

// A job's value fields: the input sources are the trigger's, not the user's.
function valueFields(job: PluginJob) {
    return (job.params ?? []).filter((f) => fieldKind(f) === "value");
}

export function hookOutcomeText(outcome: string): string {
    const key = `plugins_hook_outcome_${outcome}`;
    return i18n.exists(key) ? i18n.t(key) : outcome;
}

// A history reason: the host's own (expired ...), an end reason, or a
// refusal; one not known shows as its code.
export function hookReasonText(reason: string): string {
    for (const prefix of ["plugins_reason_", "plugins_refuse_"]) {
        if (i18n.exists(`${prefix}${reason}`)) {
            return i18n.t(`${prefix}${reason}`);
        }
    }
    return reason;
}

function disabledText(row: HookRow): string | null {
    // "user" is only the switch being off: nothing to explain.
    if (row.enabled || !row.disabled_reason || row.disabled_reason === "user") {
        return null;
    }
    const key = `plugins_hook_disabled_${row.disabled_reason}`;
    return i18n.exists(key) ? i18n.t(key) : row.disabled_reason;
}

function HookForm({api, job, row, language, onSave, onClose, onError}: {
    api: PluginsApi;
    job: PluginJob;
    row: HookRow | undefined;
    language: string;
    onSave: (params: Record<string, unknown>) => Promise<boolean>;
    onClose: () => void;
    onError: (text: string) => void;
}) {
    const fields = valueFields(job);
    const [values, setValues] = React.useState<FormValues>(
        () => initialValues(fields, row?.params ?? {}));
    const [tried, setTried] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    const alive = React.useRef(true);
    React.useEffect(() => () => {
        alive.current = false;
    }, []);

    const save = async () => {
        setTried(true);
        if (Object.keys(validateAll(fields, values)).length > 0) {
            return;
        }
        setSaving(true);
        try {
            // clearing: a file / folder let go is sent as null (the host
            // resets it); one left out keeps the saved path.
            await onSave(collectValues(
                fields, values, {clearing: true}).params);
        } finally {
            if (alive.current) {
                setSaving(false);
            }
        }
    };

    return (
        <Dialog open onClose={onClose} fullWidth maxWidth="sm">
            <DialogTitle>{i18n.t("plugins_hook_form_title")}</DialogTitle>
            <DialogContent>
                <Stack spacing={1.5} data-testid="plugin-hook-form"
                       sx={{pt: 1}}>
                    <Note>{i18n.t("plugins_hook_form_note")}</Note>
                    <FieldForm fields={fields} values={values}
                               onChange={setValues} language={language}
                               showErrors={tried}
                               pickFile={api.pickFile}
                               pickFolder={api.pickFolder}
                               onPickError={(error) => {
                                   const text = pickErrorText(error);
                                   if (text) {
                                       onError(text);
                                   }
                               }}
                               testIdPrefix={`plugin-hook-param-${job.id}`}/>
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}
                        data-testid="plugin-hook-form-cancel">
                    {i18n.t("plugins_cancel")}
                </Button>
                <Button variant="contained" disabled={saving}
                        data-testid="plugin-hook-form-save"
                        onClick={() => void save()}>
                    {i18n.t("plugins_hook_enable")}
                </Button>
            </DialogActions>
        </Dialog>
    );
}

function History({entries, language}: {
    entries: HookHistoryEntry[]; language: string;
}) {
    // Newest first, at most 20 (the host keeps that many).
    const shown = [...entries].sort((a, b) => b.at.localeCompare(a.at))
        .slice(0, 20);
    if (shown.length === 0) {
        return <Note>{i18n.t("plugins_hook_history_empty")}</Note>;
    }
    return (
        <Stack spacing={0.25} data-testid="plugin-hook-history">
            {shown.map((entry, i) => (
                <Typography key={`${entry.at}-${i}`} variant="caption"
                            data-testid="plugin-hook-history-item">
                    {[startedText(entry.at, language),
                      hookOutcomeText(entry.outcome),
                      pluginText(entry.summary, language),
                      entry.reason ? hookReasonText(entry.reason) : ""]
                        .filter(Boolean).join(" · ")}
                </Typography>
            ))}
        </Stack>
    );
}

function HookItem({api, plugin, job, hook, row, language, onChanged, onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    job: PluginJob;
    hook: {on: string; label: PluginDetail["name"]};
    row: HookRow | undefined;
    language: string;
    onChanged: (row?: HookRow) => void;
    onError: (text: string) => void;
}) {
    const [editing, setEditing] = React.useState(false);
    const [log, setLog] = React.useState<string | null>(null);
    const [sampleId, setSampleId] = React.useState<string | null>(null);
    const alive = React.useRef(true);
    React.useEffect(() => () => {
        alive.current = false;
    }, []);
    const {run: sample, lost} = usePolledRun(
        api, plugin.id, sampleId, () => onChanged());
    const enabled = row?.enabled === true;
    const usable = plugin.state !== "disabled" && plugin.state !== "blocked"
        && plugin.state !== "incompatible";
    const failedRun = row ? [...row.history]
        .sort((a, b) => b.at.localeCompare(a.at))
        .find((e) => (e.outcome === "failed" || e.outcome === "error")
            && e.run_id) : undefined;
    const fields = valueFields(job);

    // Whether the host took it; a failure is shown and the caller's dialog
    // stays open with its input.
    const put = async (on: boolean,
                       params?: Record<string, unknown>): Promise<boolean> => {
        try {
            const next = await api.setHook(plugin.id, job.id, hook.on, on,
                                           params);
            if (alive.current) {
                onChanged(next);
            }
            return true;
        } catch (error) {
            if (alive.current) {
                onError(refusalText(error));
            }
            return false;
        }
    };
    const toggle = () => {
        if (enabled) {
            void put(false);
        } else if (fields.length > 0) {
            setEditing(true);
        } else {
            void put(true, {});
        }
    };
    const showLog = () => {
        if (log !== null) {
            setLog(null);
            return;
        }
        api.runLog(plugin.id, failedRun!.run_id!).then(
            (body) => alive.current && setLog(body),
            (e) => alive.current && onError(refusalText(e)));
    };
    const sampleRunning = sampleId !== null && lost === null
        && (!sample || sample.state === "running");
    const trySample = () => {
        setSampleId(null);
        api.hookSample(plugin.id, job.id, hook.on).then(
            (started) => alive.current && setSampleId(started.run_id),
            (e) => alive.current && onError(refusalText(e)));
    };

    const why = row ? disabledText(row) : null;
    const testKey = `${job.id}-${hook.on}`;
    return (
        <Stack spacing={0.75} data-testid={`plugin-hook-${testKey}`}
               sx={{py: 1}}>
            <Stack direction="row" spacing={1} alignItems="center">
                <Switch checked={enabled}
                        disabled={!enabled && !usable}
                        inputProps={{"aria-label": pluginText(
                            hook.label, language)}}
                        data-testid={`plugin-hook-switch-${testKey}`}
                        onChange={toggle}/>
                <Box sx={{flex: 1, minWidth: 0}}>
                    <Typography variant="subtitle2">
                        {pluginText(hook.label, language)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                        {`${pluginText(job.title, language)} · ${hook.on}`}
                    </Typography>
                </Box>
                {row?.pending ? (
                    <Chip size="small" color="info"
                          label={i18n.t("plugins_hook_pending")}
                          data-testid={`plugin-hook-pending-${testKey}`}/>
                ) : null}
                {enabled && fields.length > 0 ? (
                    <Button size="small" variant="outlined"
                            data-testid={`plugin-hook-edit-${testKey}`}
                            onClick={() => setEditing(true)}>
                        {i18n.t("plugins_hook_edit")}
                    </Button>
                ) : null}
            </Stack>
            {why ? (
                <Alert severity="warning"
                       data-testid={`plugin-hook-reason-${testKey}`}>
                    {why}
                    {row?.last_error
                        ? ` ${pluginText(row.last_error.message, language)}`
                          + ` (${row.last_error.code})`
                        : ""}
                </Alert>
            ) : null}
            <History entries={row?.history ?? []} language={language}/>
            <Stack direction="row" spacing={1}>
                {failedRun ? (
                    <Button size="small"
                            data-testid={`plugin-hook-log-${testKey}`}
                            onClick={showLog}>
                        {i18n.t(log === null ? "plugins_hook_log_show"
                            : "plugins_log_hide")}
                    </Button>
                ) : null}
                {plugin.badge === "dev" ? (
                    <Button size="small" variant="outlined"
                            data-testid={`plugin-hook-sample-${testKey}`}
                            disabled={sampleRunning}
                            onClick={trySample}>
                        {i18n.t("plugins_hook_sample")}
                    </Button>
                ) : null}
            </Stack>
            {log !== null ? (
                <Box component="pre" data-testid={`plugin-hook-logtext-${testKey}`}
                     aria-label={i18n.t("plugins_log")}
                     sx={{m: 0, p: 1, fontSize: "0.75rem",
                          whiteSpace: "pre-wrap", maxHeight: 280,
                          overflow: "auto", border: 1,
                          borderColor: "divider"}}>
                    {log || i18n.t("plugins_log_empty")}
                </Box>
            ) : null}
            {sampleRunning ? (
                <Box data-testid={`plugin-hook-sample-running-${testKey}`}>
                    <Typography variant="caption">
                        {i18n.t("plugins_hook_sample_running")}
                    </Typography>
                    <LinearProgress/>
                </Box>
            ) : null}
            {sampleId && lost !== null ? (
                <Alert severity="warning"
                       data-testid={`plugin-hook-sample-lost-${testKey}`}>
                    {i18n.t(`plugins_run_lost_${lost}`)}
                </Alert>
            ) : null}
            {sampleId && lost === null && sample
                && sample.state !== "running" ? (
                <RunView api={api} pluginId={plugin.id} run={sample}
                         job={job} language={language}
                         onCancel={() => undefined} onError={onError}/>
            ) : null}
            {editing ? (
                <HookForm api={api} job={job} row={row} language={language}
                          onError={onError}
                          onClose={() => setEditing(false)}
                          onSave={async (params) => {
                              const ok = await put(true, params);
                              if (ok && alive.current) {
                                  setEditing(false);
                              }
                              return ok;
                          }}/>
            ) : null}
        </Stack>
    );
}

export default function HooksSection({api, plugin, language, onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    language: string;
    onError: (text: string) => void;
}) {
    const id = plugin.id;
    const [rows, setRows] = React.useState<HookRow[]>([]);
    const [loadFailed, setLoadFailed] = React.useState(false);
    const jobs = plugin.jobs.filter((j) => (j.hooks ?? []).length > 0);
    const hasHooks = jobs.length > 0;
    // The newest read of each wins; one for another plugin or after the
    // section is gone is dropped.
    const seq = React.useRef(0);
    const read = React.useCallback(() => {
        const mine = ++seq.current;
        api.hooks(id).then((next) => {
            if (mine === seq.current) {
                setRows(next);
                setLoadFailed(false);
            }
        }, () => {
            // The rows shown stay (as PR #404): only say the read failed.
            if (mine === seq.current) {
                setLoadFailed(true);
            }
        });
    }, [api, id]);

    React.useEffect(() => {
        setRows([]);
        setLoadFailed(false);
        if (!hasHooks) {
            return undefined;
        }
        read();
        const timer = window.setInterval(read, POLL_MS);
        return () => {
            window.clearInterval(timer);
            seq.current += 1;
        };
    }, [read, hasHooks]);

    if (!hasHooks) {
        return null;
    }
    return (
        <CardFrame title={i18n.t("plugins_hooks")}>
            <Note>{i18n.t("plugins_hooks_note")}</Note>
            {loadFailed ? (
                <Alert severity="warning" data-testid="plugin-hooks-load-failed">
                    {i18n.t("plugins_hook_load_failed")}
                </Alert>
            ) : null}
            <Stack spacing={1} data-testid="plugin-hooks">
                {jobs.flatMap((job) => (job.hooks ?? []).map((hook) => (
                    <HookItem key={`${job.id}/${hook.on}`} api={api}
                              plugin={plugin} job={job} hook={hook}
                              row={rows.find(
                                  (r) => r.key === `${job.id}/${hook.on}`)}
                              language={language}
                              onChanged={(row) => {
                                  if (row) {
                                      // The answer shows at once; the read
                                      // that follows fills in the rest.
                                      setRows((now) => [
                                          ...now.filter((r) => r.key
                                              !== row.key), row]);
                                  }
                                  read();
                              }}
                              onError={onError}/>)))}
            </Stack>
        </CardFrame>
    );
}
