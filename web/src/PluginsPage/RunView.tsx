import React from "react";
import {Alert, Box, Button, Chip, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {apiErrorReason, apiErrorCode} from "../api";
import {CopyButton} from "../GuidePages/parts";
import JobProgressView from "../ArchivePage/JobProgressView";
import {PluginsApi} from "./pluginsApi";
import {PluginRun, RunArtifact} from "./pluginTypes";
import {pluginText} from "./pluginText";

// One run of a plugin's job (contract_1.json job_record): its progress while
// it runs, then its summary, error, warnings, the worker's log and the
// artifacts it made.

// The reason a request was refused, in words (contract_1.json refusals).
export function refusalText(error: unknown): string {
    const reason = apiErrorReason(error) ?? apiErrorCode(error);
    const key = reason ? `plugins_refuse_${String(reason).toLowerCase()}` : "";
    return key && i18n.exists(key) ? i18n.t(key) : i18n.t("plugins_failed");
}

// Reads one run every second while it runs (the page does not put it in the
// StateManager: ADR-006 D6), and once more when it ends.
export function usePolledRun(
    api: PluginsApi, pluginId: string, runId: string | null,
    onEnded: () => void): PluginRun | null {
    const [run, setRun] = React.useState<PluginRun | null>(null);
    const ended = React.useRef(onEnded);
    ended.current = onEnded;
    React.useEffect(() => {
        setRun(null);
        if (!runId) {
            return undefined;
        }
        let live = true;
        let timer: ReturnType<typeof setTimeout> | null = null;
        const read = async () => {
            try {
                const next = await api.run(pluginId, runId);
                if (!live) {
                    return;
                }
                setRun(next);
                if (next.state === "running") {
                    timer = setTimeout(() => void read(), 1000);
                } else {
                    ended.current();
                }
            } catch {
                // A failed read is tried again at the next second.
                if (live) {
                    timer = setTimeout(() => void read(), 1000);
                }
            }
        };
        void read();
        return () => {
            live = false;
            if (timer !== null) {
                clearTimeout(timer);
            }
        };
    }, [api, pluginId, runId]);
    return run;
}

function Artifact({api, pluginId, runId, no, artifact, language, onError}: {
    api: PluginsApi;
    pluginId: string;
    runId: string;
    no: number;
    artifact: RunArtifact;
    language: string;
    onError: (text: string) => void;
}) {
    const [text, setText] = React.useState<string | null>(null);
    const [url, setUrl] = React.useState<string | null>(null);
    const label = pluginText(artifact.label, language) || artifact.path;

    React.useEffect(() => {
        let live = true;
        let made: string | null = null;
        if (artifact.kind === "text") {
            api.artifactText(pluginId, runId, no).then((body) => {
                if (live) {
                    setText(body);
                }
            }, (error) => live && onError(refusalText(error)));
        } else if (artifact.kind === "image") {
            api.artifactBlob(pluginId, runId, no).then((blob) => {
                if (live) {
                    made = URL.createObjectURL(blob);
                    setUrl(made);
                }
            }, (error) => live && onError(refusalText(error)));
        }
        return () => {
            live = false;
            if (made) {
                URL.revokeObjectURL(made);
            }
        };
        // onError is the page's (stable enough: not a reason to read again).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api, pluginId, runId, no, artifact.kind]);

    return (
        <Stack spacing={0.5} data-testid={`plugin-artifact-${no}`}>
            <Typography variant="subtitle2">{label}</Typography>
            {artifact.kind === "text" ? (
                <>
                    <Box component="pre" data-testid={`plugin-artifact-${no}-text`}
                         sx={{m: 0, p: 1, fontSize: "0.8rem",
                              whiteSpace: "pre-wrap", maxHeight: 240,
                              overflow: "auto", border: 1,
                              borderColor: "divider"}}>
                        {text ?? ""}
                    </Box>
                    <Box>
                        <CopyButton text={text ?? ""}
                                    name={i18n.t("plugins_artifact_copy",
                                                 {name: label})}/>
                    </Box>
                </>
            ) : null}
            {artifact.kind === "image" && url ? (
                <Box component="img" src={url} alt={label}
                     data-testid={`plugin-artifact-${no}-image`}
                     sx={{maxWidth: "100%", maxHeight: 320,
                          objectFit: "contain", alignSelf: "flex-start"}}/>
            ) : null}
            {artifact.kind === "file" || artifact.kind === "json" ? (
                <Box>
                    <Button size="small" variant="outlined"
                            onClick={() => void api.revealArtifact(
                                pluginId, runId, no).catch(
                                (e) => onError(refusalText(e)))}>
                        {i18n.t("plugins_artifact_reveal")}
                    </Button>
                </Box>
            ) : null}
        </Stack>
    );
}

export default function RunView({api, pluginId, run, language, readOnly,
                                 onCancel, onError}: {
    api: PluginsApi;
    pluginId: string;
    run: PluginRun;
    language: string;
    readOnly: boolean;
    onCancel: () => void;
    onError: (text: string) => void;
}) {
    const [log, setLog] = React.useState<string | null>(null);

    // Another run: its log is closed.
    React.useEffect(() => {
        setLog(null);
    }, [run.run_id]);

    const toggleLog = () => {
        if (log !== null) {
            setLog(null);
            return;
        }
        api.runLog(pluginId, run.run_id).then(
            (body) => setLog(body), (e) => onError(refusalText(e)));
    };

    if (run.state === "running") {
        const stage = run.stage;
        const line = stage
            ? `${stage.count ? `[${stage.index ?? 0}/${stage.count}] ` : ""}`
              + `${pluginText(stage.label, language) || stage.stage}`
            : null;
        return (
            <div data-testid="plugin-run-progress">
                <JobProgressView
                    line={line}
                    overall={run.progress?.overall}
                    eta={run.progress?.eta}
                    logs={run.log ? [pluginText(run.log, language)] : []}
                    readOnly={readOnly}
                    onCancel={onCancel}/>
            </div>
        );
    }

    return (
        <Stack spacing={1} data-testid="plugin-run-view">
            <Stack direction="row" spacing={1} alignItems="center">
                <Chip size="small" label={i18n.t(`plugins_run_${run.state}`)}
                      color={run.state === "done" ? "success"
                          : run.state === "cancelled" ? "default" : "error"}/>
                <Typography variant="caption" color="text.secondary">
                    {run.job} · {run.started_at}
                </Typography>
            </Stack>
            {run.summary ? (
                <Typography variant="body2">
                    {pluginText(run.summary, language)}
                </Typography>
            ) : null}
            {run.error ? (
                <Alert severity="error" data-testid="plugin-run-error">
                    {pluginText(run.error.message, language)}
                    {run.error.hint
                        ? ` ${pluginText(run.error.hint, language)}` : ""}
                </Alert>
            ) : null}
            {run.warnings.map((w, i) => (
                <Alert key={i} severity="warning">
                    {pluginText(w, language)}
                </Alert>
            ))}
            {run.artifacts.map((artifact, no) => (
                <Artifact key={`${run.run_id}-${no}`} api={api}
                          pluginId={pluginId} runId={run.run_id} no={no}
                          artifact={artifact} language={language}
                          onError={onError}/>
            ))}
            <Box>
                <Button size="small" onClick={toggleLog}
                        data-testid="plugin-run-log-toggle">
                    {i18n.t(log === null ? "plugins_log_show"
                        : "plugins_log_hide")}
                </Button>
            </Box>
            {log !== null ? (
                <Box component="pre" data-testid="plugin-run-log"
                     aria-label={i18n.t("plugins_log")}
                     sx={{m: 0, p: 1, fontSize: "0.75rem",
                          whiteSpace: "pre-wrap", maxHeight: 280,
                          overflow: "auto", border: 1,
                          borderColor: "divider"}}>
                    {log || i18n.t("plugins_log_empty")}
                </Box>
            ) : null}
        </Stack>
    );
}
