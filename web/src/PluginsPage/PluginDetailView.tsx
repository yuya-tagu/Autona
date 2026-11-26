import React from "react";
import {Alert, Box, Button, Chip, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {CardFrame, Note} from "../GuidePages/parts";
import {PluginsApi} from "./pluginsApi";
import {
    PluginDetail,
    PluginJob,
    PluginRun,
    SOURCE_TYPES,
} from "./pluginTypes";
import {pluginText, sizeLabel} from "./pluginText";
import FieldForm from "./FieldForm";
import {
    FormValues,
    collectValues,
    initialValues,
    validateAll,
} from "./formFields";
import RunView, {refusalText, usePolledRun} from "./RunView";

// One plugin's page (ADR-009 D4): its settings, its jobs with their forms,
// the running job's progress, the past jobs, and what it declares. The
// hooks, the install and the listing are later stages' (P7 / P5): no place
// is drawn for them yet.

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
            {manual ? (
                <>
                    <FieldForm fields={fields} values={values}
                               onChange={setValues} language={language}
                               showErrors={tried} loadChoices={loadChoices}
                               pickFile={api.pickFile}
                               pickFolder={api.pickFolder}
                               onPickError={() => onError(
                                   i18n.t("plugins_pick_failed"))}
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
                <Note>{i18n.t("plugins_job_context_only")}</Note>
            )}
        </Stack>
    );
}

function Settings({api, plugin, language, onSaved, onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    language: string;
    onSaved: () => void;
    onError: (text: string) => void;
}) {
    const [values, setValues] = React.useState<FormValues>(
        () => initialValues(plugin.settings, plugin.setting_values));
    const [tried, setTried] = React.useState(false);
    const secretSet: Record<string, boolean> = {};
    for (const field of plugin.settings) {
        const saved = plugin.setting_values[field.key];
        secretSet[field.key] = saved?.kind === "secret" && saved.set;
    }

    const save = async () => {
        setTried(true);
        if (Object.keys(validateAll(plugin.settings, values, secretSet))
            .length > 0) {
            return;
        }
        const {params} = collectValues(plugin.settings, values);
        try {
            if (Object.keys(params).length > 0) {
                await api.saveSettings(plugin.id, params);
            }
            for (const field of plugin.settings) {
                const typed = values[field.key];
                if (field.type === "secret" && typeof typed === "string"
                    && typed !== "") {
                    await api.saveSecret(plugin.id, field.key, typed);
                }
            }
            setTried(false);
            onSaved();
        } catch (error) {
            onError(refusalText(error));
        }
    };

    const clear = async (key: string) => {
        try {
            await api.clearSecret(plugin.id, key);
            onSaved();
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
                       onPickError={() => onError(i18n.t("plugins_pick_failed"))}
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

export default function PluginDetailView({api, id, language, readOnly,
                                          onChanged, onError}: {
    api: PluginsApi;
    id: string;
    language: string;
    readOnly: boolean;
    // The list's row may have changed (state, size).
    onChanged: () => void;
    onError: (text: string) => void;
}) {
    const [plugin, setPlugin] = React.useState<PluginDetail | null>(null);
    const [failed, setFailed] = React.useState(false);
    const [version, setVersion] = React.useState(0);
    const [runs, setRuns] = React.useState<PluginRun[]>([]);
    const [activeId, setActiveId] = React.useState<string | null>(null);
    const [shownId, setShownId] = React.useState<string | null>(null);

    const read = React.useCallback(() => {
        api.detail(id).then((next) => {
            setPlugin(next);
            setFailed(false);
            setVersion((v) => v + 1);
        }, () => setFailed(true));
    }, [api, id]);
    const readRuns = React.useCallback(() => {
        api.runs(id).then(setRuns, () => undefined);
    }, [api, id]);

    React.useEffect(() => {
        setPlugin(null);
        setActiveId(null);
        setShownId(null);
        read();
        readRuns();
    }, [id, read, readRuns]);

    const ended = React.useCallback(() => {
        readRuns();
        read();
        onChanged();
    }, [readRuns, read, onChanged]);
    const active = usePolledRun(api, id, activeId, ended);
    const running = activeId !== null && (active === null
        || active.state === "running");
    // A past run picked from the history, shown in place of the latest.
    const shown = runs.find((r) => r.run_id === shownId)
        ?? (active && active.state !== "running" ? active : null);

    const cancel = () => {
        if (activeId) {
            api.cancelRun(id, activeId).catch((e) => onError(refusalText(e)));
        }
    };
    const link = (which: "homepage" | "funding") => {
        api.openLink("installed", id, which).catch(
            () => onError(i18n.t("plugins_link_failed")));
    };

    if (failed) {
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
                </Stack>
            </CardFrame>

            {plugin.settings.length > 0 ? (
                <Settings key={`s${version}`} api={api} plugin={plugin}
                          language={language}
                          onSaved={() => {
                              read();
                              onChanged();
                          }}
                          onError={onError}/>
            ) : null}

            <CardFrame title={i18n.t("plugins_jobs")}>
                {plugin.jobs.map((job) => (
                    <JobCard key={job.id} api={api} plugin={plugin} job={job}
                             language={language} busy={running || readOnly}
                             onStart={(runId) => {
                                 setShownId(null);
                                 setActiveId(runId);
                                 readRuns();
                             }}
                             onError={onError}/>
                ))}
                {activeId && active && active.state === "running" ? (
                    <RunView api={api} pluginId={id} run={active}
                             language={language} readOnly={readOnly}
                             onCancel={cancel} onError={onError}/>
                ) : null}
            </CardFrame>

            <CardFrame title={i18n.t("plugins_history")}>
                {runs.length === 0 ? (
                    <Note>{i18n.t("plugins_history_empty")}</Note>
                ) : (
                    <Stack spacing={0.5} data-testid="plugin-history">
                        {runs.map((run) => (
                            <Button key={run.run_id} size="small"
                                    sx={{justifyContent: "flex-start"}}
                                    variant={shown?.run_id === run.run_id
                                        ? "contained" : "text"}
                                    onClick={() => setShownId(run.run_id)}>
                                {`${run.started_at} · ${run.job} · `
                                    + i18n.t(`plugins_run_${run.state}`)}
                            </Button>
                        ))}
                    </Stack>
                )}
                {shown && shown.state !== "running" ? (
                    <RunView api={api} pluginId={id} run={shown}
                             language={language} readOnly={readOnly}
                             onCancel={cancel} onError={onError}/>
                ) : null}
            </CardFrame>

            <Declared plugin={plugin} language={language}/>
            <Note>{`${plugin.id} · ${plugin.author} · v${plugin.version}`
                + ` · ${sizeLabel(plugin.size_bytes)}`}</Note>
        </Stack>
    );
}
