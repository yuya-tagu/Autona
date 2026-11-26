import React from "react";
import {Alert, Box, Button, Chip, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {CardFrame, Note} from "../GuidePages/parts";
import {navigateToPanel} from "../PanelShell/hostApi";
import {PluginsApi} from "./pluginsApi";
import {
    PluginDetail,
    PluginJob,
    PluginRun,
    SOURCE_TYPES,
} from "./pluginTypes";
import {
    pluginText,
    sizeLabel,
    startedText,
    versionLabel,
} from "./pluginText";
import FieldForm from "./FieldForm";
import PluginManage, {canManage} from "./PluginManage";
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

// One plugin's page (ADR-009 D4): its settings, its jobs with their forms,
// the running job's progress, the past jobs, and what it declares. The
// hooks, the install and the listing are later stages' (P7 / P5): no place
// is drawn for them yet.

// The built-in vodsplit (ADR-009 D9): its page is the archive page (D4).
const BUILTIN_VODSPLIT_ID = "taguyan.vodsplit";

// A time limit in the largest unit that reads well: seconds under a minute,
// minutes under an hour (a remainder of seconds is dropped), else hours and
// the remaining minutes.
export function timeoutText(seconds: number): string {
    if (seconds < 60) {
        return i18n.t("plugins_timeout_seconds", {seconds});
    }
    const minutes = Math.floor(seconds / 60);
    if (seconds < 3600) {
        return i18n.t("plugins_timeout_minutes", {minutes});
    }
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest === 0
        ? i18n.t("plugins_timeout_hours", {hours})
        : i18n.t("plugins_timeout_hours_minutes", {hours, minutes: rest});
}

function pickFailed(onError: (text: string) => void) {
    return (error: unknown) => {
        const text = pickErrorText(error);
        if (text) {
            onError(text);
        }
    };
}

function JobCard({api, plugin, job, language, busy, onStart, onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    job: PluginJob;
    language: string;
    busy: boolean;
    onStart: (runId: string) => void;
    onError: (text: string) => void;
}) {
    const fields = job.params ?? [];
    const [values, setValues] = React.useState<FormValues>(
        () => initialValues(fields));
    const [tried, setTried] = React.useState(false);
    const manual = job.manual !== false;
    const loadChoices = React.useCallback(
        (kind: string) => api.inputChoices(kind), [api]);

    const run = async () => {
        setTried(true);
        if (Object.keys(validateAll(fields, values)).length > 0) {
            return;
        }
        const {params, inputs} = collectValues(fields, values);
        try {
            const started = await api.startRun(plugin.id, {
                job: job.id, trigger: {kind: "manual", target: null},
                params, inputs});
            setTried(false);
            onStart(started.run_id);
        } catch (error) {
            onError(refusalText(error));
        }
    };

    return (
        <Stack spacing={1.5} data-testid={`plugin-job-${job.id}`}
               sx={{py: 1}}>
            <Typography variant="subtitle2">
                {pluginText(job.title, language)}
            </Typography>
            <Typography variant="caption" color="text.secondary"
                        data-testid={`plugin-timeout-${job.id}`}>
                {job.timeout_seconds === null
                    ? i18n.t("plugins_timeout_none")
                    : timeoutText(job.timeout_seconds)}
            </Typography>
            {manual ? (
                <>
                    <FieldForm fields={fields} values={values}
                               onChange={setValues} language={language}
                               showErrors={tried} loadChoices={loadChoices}
                               pickFile={api.pickFile}
                               pickFolder={api.pickFolder}
                               onPickError={pickFailed(onError)}
                               testIdPrefix={`plugin-param-${job.id}`}/>
                    <Box>
                        <Button variant="contained" size="small"
                                data-testid={`plugin-run-${job.id}`}
                                disabled={busy || plugin.state === "disabled"
                                    || plugin.state === "blocked"
                                    || plugin.state === "incompatible"}
                                onClick={() => void run()}>
                            {i18n.t("plugins_run")}
                        </Button>
                    </Box>
                </>
            ) : (
                <Box data-testid={`plugin-job-note-${job.id}`}>
                    <Note>
                        {i18n.t(plugin.badge === "builtin"
                                && plugin.id === BUILTIN_VODSPLIT_ID
                            ? "plugins_job_builtin_page"
                            : "plugins_job_context_only")}
                    </Note>
                </Box>
            )}
        </Stack>
    );
}

// The settings form. A setting is a value type only: an input source or a
// secret in a job's params is refused by the manifest check, so it never
// reaches here. A type this page does not know is shown as unusable and left
// out of the save, so the other settings still save.
function Settings({api, plugin, language, onSaved, onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    language: string;
    // Reads the detail again; the form takes the saved values from it.
    onSaved: () => Promise<PluginDetail | null>;
    onError: (text: string) => void;
}) {
    const [values, setValues] = React.useState<FormValues>(
        () => initialValues(plugin.settings, plugin.setting_values));
    const [tried, setTried] = React.useState(false);
    const known = plugin.settings.filter((f) => fieldKind(f) === "value");
    const secretSet: Record<string, boolean> = {};
    for (const field of plugin.settings) {
        const saved = plugin.setting_values[field.key];
        secretSet[field.key] = saved?.kind === "secret" && saved.set;
    }

    const save = async () => {
        setTried(true);
        if (Object.keys(validateAll(known, values, secretSet)).length > 0) {
            return;
        }
        // Emptied values go as null (the host clears them). A secret is not
        // among them: it has its own route and is only ever set here.
        const {params} = collectValues(known, values, {clearing: true});
        try {
            if (Object.keys(params).length > 0) {
                await api.saveSettings(plugin.id, params);
            }
            for (const field of known) {
                const typed = values[field.key];
                if (field.type === "secret" && typeof typed === "string"
                    && typed !== "") {
                    await api.saveSecret(plugin.id, field.key, typed);
                }
            }
            setTried(false);
            const fresh = await onSaved();
            if (fresh) {
                setValues(initialValues(fresh.settings, fresh.setting_values));
            }
        } catch (error) {
            onError(refusalText(error));
        }
    };

    const clear = async (key: string) => {
        try {
            await api.clearSecret(plugin.id, key);
            await onSaved();
        } catch (error) {
            onError(refusalText(error));
        }
    };

    return (
        <CardFrame title={i18n.t("plugins_settings")}>
            <FieldForm fields={plugin.settings} values={values}
                       onChange={setValues} language={language}
                       showErrors={tried} secretSet={secretSet}
                       onClearSecret={(key) => void clear(key)}
                       pickFile={api.pickFile} pickFolder={api.pickFolder}
                       onPickError={pickFailed(onError)}
                       testIdPrefix="plugin-setting"/>
            <Box>
                <Button variant="contained" size="small"
                       data-testid="plugin-settings-save"
                       onClick={() => void save()}>
                    {i18n.t("plugins_save")}
                </Button>
            </Box>
        </CardFrame>
    );
}

function Declared({plugin, language}: {
    plugin: PluginDetail;
    language: string;
}) {
    const p = plugin.permissions;
    // The source types the jobs take: what the plugin is handed.
    const passed = new Set<string>();
    for (const job of plugin.jobs) {
        for (const field of job.params ?? []) {
            if ((SOURCE_TYPES as readonly string[]).includes(field.type)) {
                passed.add(field.type);
            }
        }
    }
    return (
        <CardFrame title={i18n.t("plugins_declared")}>
            <Note>{i18n.t("plugins_declared_note")}</Note>
            <Stack spacing={0.5} data-testid="plugin-declared">
                <Typography variant="body2">
                    {i18n.t("plugins_declared_network", {
                        value: p.network.length
                            ? p.network.join(", ")
                            : i18n.t("plugins_declared_none")})}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("plugins_declared_files",
                            {value: i18n.t(`plugins_files_${p.files}`)})}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("plugins_declared_spawn",
                            {value: i18n.t(p.spawn ? "plugins_yes"
                                : "plugins_no")})}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("plugins_declared_api",
                            {value: i18n.t(`plugins_api_${p.autona_api}`)})}
                </Typography>
                {p.note ? (
                    <Typography variant="body2">
                        {pluginText(p.note, language)}
                    </Typography>
                ) : null}
            </Stack>
            <Typography variant="subtitle2">
                {i18n.t("plugins_passed")}
            </Typography>
            <Typography variant="body2" data-testid="plugin-passed">
                {passed.size
                    ? [...passed].map((t) => i18n.t(`plugins_source_${t}`))
                        .join(", ")
                    : i18n.t("plugins_declared_none")}
            </Typography>
            {passed.size ? (
                <Note>{i18n.t("plugins_passed_names")}</Note>
            ) : null}
        </CardFrame>
    );
}

export default function PluginDetailView({api, id, language, onChanged,
                                          onRemoved, onError}: {
    api: PluginsApi;
    id: string;
    language: string;
    // The list's row may have changed (state, size).
    onChanged: () => void;
    // The plugin was removed: the page leaves it.
    onRemoved: () => void;
    onError: (text: string) => void;
}) {
    const [plugin, setPlugin] = React.useState<PluginDetail | null>(null);
    const [failed, setFailed] = React.useState(false);
    const [runs, setRuns] = React.useState<PluginRun[]>([]);
    const [activeId, setActiveId] = React.useState<string | null>(null);
    const [shownId, setShownId] = React.useState<string | null>(null);
    // The newest read of each wins: a slow answer for a plugin the page has
    // left (or an older read) is dropped.
    const detailSeq = React.useRef(0);
    const runsSeq = React.useRef(0);
    // The plugin shown now: a late call made for another one reads nothing.
    const shownPlugin = React.useRef(id);
    shownPlugin.current = id;

    const read = React.useCallback((): Promise<PluginDetail | null> => {
        if (shownPlugin.current !== id) {
            return Promise.resolve(null);
        }
        const mine = ++detailSeq.current;
        return api.detail(id).then((next) => {
            if (mine !== detailSeq.current || next.id !== id) {
                return null;
            }
            setPlugin(next);
            setFailed(false);
            return next;
        }, () => {
            if (mine === detailSeq.current) {
                setFailed(true);
            }
            return null;
        });
    }, [api, id]);
    const readRuns = React.useCallback(() => {
        if (shownPlugin.current !== id) {
            return;
        }
        const mine = ++runsSeq.current;
        api.runs(id).then((rows) => {
            if (mine !== runsSeq.current) {
                return;
            }
            setRuns(rows);
            // A job still running (the page was left and came back, or an
            // action started it) is followed again. The page follows one
            // run at a time: simpler than one per lane (the host lets a
            // plugin run one job per lane at most).
            const going = rows.find((r) => r.state === "running");
            if (going) {
                setActiveId((now) => now ?? going.run_id);
            }
        }, () => undefined);
    }, [api, id]);

    React.useEffect(() => {
        setPlugin(null);
        setFailed(false);
        setRuns([]);
        setActiveId(null);
        setShownId(null);
        void read();
        readRuns();
        return () => {
            // Answers still on their way are no longer wanted.
            detailSeq.current += 1;
            runsSeq.current += 1;
        };
    }, [id, read, readRuns]);

    const ended = React.useCallback(() => {
        readRuns();
        void read();
        onChanged();
    }, [readRuns, read, onChanged]);
    const {run: active, lost} = usePolledRun(api, id, activeId, ended);
    // Running while the last read says so; a poll that gave up ends it.
    const running = activeId !== null && lost === null
        && (active === null || active.state === "running");
    // A past run picked from the history, shown in place of the latest.
    const shown = runs.find((r) => r.run_id === shownId)
        ?? (active && active.state !== "running" ? active : null);
    const jobOf = (jobId: string) => plugin?.jobs.find((j) => j.id === jobId);

    const cancel = () => {
        if (activeId) {
            api.cancelRun(id, activeId).then(
                () => undefined, (e) => onError(refusalText(e)));
        }
    };
    const link = (which: "homepage" | "funding") => {
        api.openLink("installed", id, which).catch((error) => onError(
            (error as {code?: string} | null)?.code === "blocked"
                ? i18n.t("plugins_link_blocked")
                : i18n.t("plugins_link_failed")));
    };
    const reload = () => {
        api.reload(id).then(() => {
            void read();
            onChanged();
        }, (e) => onError(refusalText(e)));
    };

    // Only a first read that failed replaces the view: a failed reread (the
    // plugin going away while its removal is followed) keeps what is shown,
    // so the removal dialog stays mounted and sees the removal through.
    if (failed && !plugin) {
        return <Alert severity="error">{i18n.t("plugins_detail_failed")}</Alert>;
    }
    if (!plugin) {
        return <Note>{i18n.t("plugins_loading")}</Note>;
    }

    return (
        <Stack spacing={2} data-testid="plugin-detail">
            <CardFrame title={pluginText(plugin.name, language)}>
                {plugin.description ? (
                    <Typography variant="body2">
                        {pluginText(plugin.description, language)}
                    </Typography>
                ) : null}
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                    <Chip size="small"
                          label={i18n.t(`plugins_state_${plugin.state}`)}/>
                    <Chip size="small" variant="outlined"
                          label={i18n.t(`plugins_badge_${plugin.badge}`)}/>
                </Stack>
                {plugin.reason ? (
                    <Alert severity="info" data-testid="plugin-reason">
                        {pluginText(plugin.reason, language)}
                    </Alert>
                ) : null}
                <Stack direction="row" spacing={1}>
                    {plugin.homepage ? (
                        <Button size="small" variant="outlined"
                                data-testid="plugin-link-homepage"
                                onClick={() => link("homepage")}>
                            {i18n.t("plugins_homepage")}
                        </Button>
                    ) : null}
                    {plugin.funding ? (
                        <Button size="small" variant="outlined"
                                data-testid="plugin-link-funding"
                                onClick={() => link("funding")}>
                            {i18n.t("plugins_funding")}
                        </Button>
                    ) : null}
                    {plugin.badge === "builtin"
                        && plugin.id === BUILTIN_VODSPLIT_ID ? (
                        <Button size="small" variant="contained"
                                data-testid="plugin-open-archive"
                                onClick={() => navigateToPanel("archive")}>
                            {i18n.t("plugins_open")}
                        </Button>
                    ) : null}
                    {plugin.badge === "dev" ? (
                        <Button size="small" variant="outlined"
                                data-testid="plugin-reload"
                                onClick={reload}>
                            {i18n.t("plugins_reload")}
                        </Button>
                    ) : null}
                    {canManage(plugin) ? (
                        <PluginManage api={api} plugin={plugin}
                                      onChanged={() => {
                                          onChanged();
                                          void read();
                                      }}
                                      onRemoved={onRemoved}
                                      onError={onError}/>
                    ) : null}
                </Stack>
            </CardFrame>

            {plugin.settings.length > 0 ? (
                <Settings key={plugin.id} api={api} plugin={plugin}
                          language={language}
                          onSaved={() => {
                              onChanged();
                              return read();
                          }}
                          onError={onError}/>
            ) : null}

            <CardFrame title={i18n.t("plugins_jobs")}>
                {plugin.jobs.map((job) => (
                    <JobCard key={`${plugin.id}/${job.id}`} api={api} plugin={plugin} job={job}
                             language={language} busy={running}
                             onStart={(runId) => {
                                 setShownId(null);
                                 setActiveId(runId);
                                 readRuns();
                             }}
                             onError={onError}/>
                ))}
                {lost ? (
                    <Alert severity="warning" data-testid="plugin-run-lost">
                        {i18n.t(`plugins_run_lost_${lost}`)}
                    </Alert>
                ) : null}
                {activeId && active && active.state === "running"
                    && lost === null ? (
                    <RunView api={api} pluginId={id} run={active}
                             job={jobOf(active.job)} language={language}
                             onCancel={cancel} onError={onError}/>
                ) : null}
            </CardFrame>

            <CardFrame title={i18n.t("plugins_history")}>
                {runs.length === 0 ? (
                    <Note>{i18n.t("plugins_history_empty")}</Note>
                ) : (
                    <Stack spacing={0.5} data-testid="plugin-history">
                        {runs.map((run) => {
                            const job = jobOf(run.job);
                            return (
                                <Button key={run.run_id} size="small"
                                        sx={{justifyContent: "flex-start"}}
                                        variant={shown?.run_id === run.run_id
                                            ? "contained" : "text"}
                                        onClick={() => setShownId(run.run_id)}>
                                    {`${startedText(run.started_at, language)}`
                                        + ` · ${job
                                            ? pluginText(job.title, language)
                                            : run.job} · `
                                        + i18n.t(`plugins_run_${run.state}`)}
                                </Button>
                            );
                        })}
                    </Stack>
                )}
                {shown && shown.state !== "running" ? (
                    <RunView api={api} pluginId={id} run={shown}
                             job={jobOf(shown.job)} language={language}
                             onCancel={cancel} onError={onError}/>
                ) : null}
            </CardFrame>

            <Declared plugin={plugin} language={language}/>
            <Note>{[plugin.id, plugin.author, versionLabel(plugin.version),
                    sizeLabel(plugin.size_bytes)]
                .filter(Boolean).join(" · ")}</Note>
        </Stack>
    );
}
