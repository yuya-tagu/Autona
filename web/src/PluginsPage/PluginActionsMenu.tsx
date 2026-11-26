import React from "react";
import {
    Alert,
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    ListItemText,
    Menu,
    MenuItem,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {usePanelShell} from "../PanelShell/context";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {PluginsApi, httpPluginsApi} from "./pluginsApi";
import {ActionTarget, PluginAction} from "./pluginTypes";
import {pluginText} from "./pluginText";
import FieldForm from "./FieldForm";
import {
    FormValues,
    collectValues,
    initialValues,
    validateAll,
} from "./formFields";
import RunView, {pickErrorText, refusalText, usePolledRun} from "./RunView";

// The "Plugins" menu a page puts beside what it made (ADR-009 D4): the jobs
// of the installed plugins that offer themselves for this place
// (GET /plugins/actions?target=), each opening its form in a dialog and
// starting as an action (trigger kind "action"). The routes are SHELL, so
// the menu exists in the desktop's shared view only; with no action to offer
// (or outside the view) it draws nothing at all.
//
// `context` is what the page hands the plugin as its own file: the name of
// the image in out/results / out/thumbnails, or the archive job's id for an
// export (routes_v1.json RunBody; PluginInputs). Null: nothing made yet, the
// button is there but waits.

// The file name of a made image's path (out/results/<name>): what an action's
// context names.
export function fileNameOf(path: string): string | null {
    const name = path.split(/[\/]/).pop() ?? "";
    return name || null;
}

// Source types an action's trigger fills by itself.
const AT_TRIGGER_TYPES = ["state", "match_log"];

function labelOf(action: PluginAction, target: ActionTarget,
                 language: string): string {
    const own = (action.job.actions ?? []).find((a) => a.target === target);
    return pluginText(own?.label ?? action.job.title, language);
}

function ActionDialog({api, action, target, context, language, onClose}: {
    api: PluginsApi;
    action: PluginAction;
    target: ActionTarget;
    context: string;
    language: string;
    onClose: () => void;
}) {
    const fields = action.job.params ?? [];
    // The source the page fills; the form does not ask for it.
    const filledKey = fields.find((f) => f.type === target)?.key;
    const filled = filledKey ? [filledKey] : [];
    // What the form draws: the trigger fills a state / match log itself
    // (contract_1.json at_trigger), so those are not asked either. What it
    // checks and sends leaves out the page's own source too.
    const shown = fields.filter((f) => !AT_TRIGGER_TYPES.includes(f.type));
    const asked = shown.filter((f) => f.key !== filledKey);
    const [values, setValues] = React.useState<FormValues>(
        () => initialValues(fields));
    const [tried, setTried] = React.useState(false);
    const [starting, setStarting] = React.useState(false);
    const [error, setError] = React.useState("");
    const [runId, setRunId] = React.useState<string | null>(null);
    const loadChoices = React.useCallback(
        (kind: string) => api.inputChoices(kind), [api]);
    const {run, lost} = usePolledRun(
        api, action.plugin, runId, () => undefined);
    const running = runId !== null && lost === null
        && (run === null || run.state === "running");

    const start = async () => {
        setTried(true);
        if (Object.keys(validateAll(asked, values)).length > 0) {
            return;
        }
        const {params, inputs} = collectValues(asked, values);
        if (filledKey) {
            inputs[filledKey] = context;
        }
        setStarting(true);
        setError("");
        try {
            const started = await api.startRun(action.plugin, {
                job: action.job.id,
                trigger: {kind: "action", target}, params, inputs});
            setRunId(started.run_id);
        } catch (e) {
            setError(refusalText(e));
        } finally {
            setStarting(false);
        }
    };
    const cancel = () => {
        if (runId) {
            api.cancelRun(action.plugin, runId).then(
                () => undefined, (e) => setError(refusalText(e)));
        }
    };

    return (
        <Dialog open fullWidth maxWidth="sm"
                // A click outside and Esc do not close it while the job is
                // being started or runs: the button does (the job goes on).
                onClose={() => {
                    if (!starting && !running) {
                        onClose();
                    }
                }}
                data-testid="plugin-action-dialog">
            <DialogTitle>{labelOf(action, target, language)}</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{pt: 1}}>
                    <Typography variant="caption" color="text.secondary">
                        {pluginText(action.name, language)}
                    </Typography>
                    {shown.length > 0 && runId === null ? (
                        <FieldForm fields={shown} values={values}
                                   onChange={setValues} language={language}
                                   showErrors={tried} filled={filled}
                                   loadChoices={loadChoices}
                                   pickFile={api.pickFile}
                                   pickFolder={api.pickFolder}
                                   onPickError={(e) => {
                                       const text = pickErrorText(e);
                                       if (text) {
                                           setError(text);
                                       }
                                   }}
                                   testIdPrefix="plugin-action-param"/>
                    ) : null}
                    {starting || running ? (
                        <Typography variant="caption" color="text.secondary"
                                    data-testid="plugin-action-goes-on">
                            {i18n.t("plugins_action_goes_on")}
                        </Typography>
                    ) : null}
                    {error ? (
                        <Alert severity="warning"
                               data-testid="plugin-action-error">
                            {error}
                        </Alert>
                    ) : null}
                    {lost ? (
                        <Alert severity="warning">
                            {i18n.t(`plugins_run_lost_${lost}`)}
                        </Alert>
                    ) : null}
                    {run ? (
                        <RunView api={api} pluginId={action.plugin} run={run}
                                 job={action.job} language={language}
                                 onCancel={cancel} onError={setError}/>
                    ) : null}
                </Stack>
            </DialogContent>
            <DialogActions>
                {runId === null ? (
                    <Button variant="contained" disabled={starting}
                            data-testid="plugin-action-run"
                            onClick={() => void start()}>
                        {i18n.t("plugins_run")}
                    </Button>
                ) : (
                    <Button disabled={running}
                            data-testid="plugin-action-again"
                            onClick={() => {
                                setRunId(null);
                                setError("");
                            }}>
                        {i18n.t("plugins_action_again")}
                    </Button>
                )}
                <Button data-testid="plugin-action-close" onClick={onClose}>
                    {i18n.t("plugins_action_close")}
                </Button>
            </DialogActions>
        </Dialog>
    );
}

// `shell`: the page already knows it is the desktop's shared view (its own
// probe); without it the menu asks for itself. `blocked`: the page is making
// the image (the file named is the old one). `note`: why the button waits
// when nothing is made yet, in the page's words.
export interface PluginActionsMenuProps {
    target: ActionTarget;
    context: string | null;
    api?: PluginsApi;
    shell?: boolean;
    blocked?: boolean;
    note?: string;
    size?: "small" | "medium";
}

export default function PluginActionsMenu(props: PluginActionsMenuProps) {
    if (props.shell !== undefined) {
        return <ActionsMenu {...props} shell={props.shell}/>;
    }
    return <ProbedMenu {...props}/>;
}

function ProbedMenu(props: PluginActionsMenuProps) {
    const {access} = useShellAccess({withToken: false});
    return <ActionsMenu {...props} shell={access === "shell"}/>;
}

function ActionsMenu({
    target, context, api = httpPluginsApi, shell, blocked = false, note,
    size = "medium",
}: PluginActionsMenuProps & {shell: boolean}) {
    const {language} = usePanelShell();
    const [actions, setActions] = React.useState<PluginAction[]>([]);
    const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
    const [chosen, setChosen] = React.useState<PluginAction | null>(null);
    const seq = React.useRef(0);

    const read = React.useCallback(() => {
        const mine = ++seq.current;
        api.actions(target).then((rows) => {
            if (mine === seq.current) {
                setActions(rows);
            }
        }, () => undefined);
    }, [api, target]);
    React.useEffect(() => {
        if (shell) {
            read();
        }
        return () => {
            seq.current += 1;
        };
    }, [shell, read]);

    if (!shell || actions.length === 0) {
        return null;
    }
    const waiting = context === null || blocked;
    const why = blocked ? i18n.t("plugins_action_generating")
        : context === null ? (note ?? i18n.t("plugins_action_no_context"))
            : "";
    return (
        <Box sx={{display: "flex", alignItems: "center", gap: 1}}>
            <Button variant="outlined" size={size}
                    data-testid="plugin-actions-button" disabled={waiting}
                    onClick={(e) => {
                        // Plugins may have been installed since: read again.
                        read();
                        setAnchor(e.currentTarget);
                    }}>
                {i18n.t("plugins_actions_menu")} ▾
            </Button>
            {waiting ? (
                <Typography variant="caption" color="text.secondary"
                            data-testid="plugin-actions-why">
                    {why}
                </Typography>
            ) : null}
            <Menu anchorEl={anchor} open={anchor !== null}
                  onClose={() => setAnchor(null)}>
                {actions.map((action) => (
                    <MenuItem key={`${action.plugin}/${action.job.id}`}
                              data-testid={
                                  `plugin-action-${action.plugin}-${action.job.id}`}
                              onClick={() => {
                                  setAnchor(null);
                                  setChosen(action);
                              }}>
                        <ListItemText
                            primary={labelOf(action, target, language)}
                            secondary={pluginText(action.name, language)}/>
                    </MenuItem>
                ))}
            </Menu>
            {chosen && context !== null ? (
                <ActionDialog api={api} action={chosen} target={target}
                              context={context} language={language}
                              onClose={() => setChosen(null)}/>
            ) : null}
        </Box>
    );
}
