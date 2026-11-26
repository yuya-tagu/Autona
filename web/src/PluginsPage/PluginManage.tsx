import React from "react";
import {
    Alert,
    Button,
    Checkbox,
    CircularProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    FormControlLabel,
    Stack,
    Switch,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {apiErrorStatus} from "../api";
import {PluginsApi} from "./pluginsApi";
import {PluginDetail, RemovalInfo, RemoveBody} from "./pluginTypes";
import {sizeLabel} from "./pluginText";
import {refusalText} from "./RunView";
import {failureText} from "./InstallSection";

// An installed plugin's enable switch and its removal (ADR-009 P5a). Not for
// the built-in or a dev folder's plugin: the host refuses both.

const POLL_MS = 1000;
const MAX_POLL_FAILURES = 5;

export function canManage(plugin: PluginDetail): boolean {
    return plugin.badge === "listed" || plugin.badge === "manual";
}

export default function PluginManage({api, plugin, onChanged, onRemoved,
                                      onError}: {
    api: PluginsApi;
    plugin: PluginDetail;
    // The plugin's state changed (enabled / disabled).
    onChanged: () => void;
    // The plugin is gone.
    onRemoved: () => void;
    onError: (text: string) => void;
}) {
    const id = plugin.id;
    const [switching, setSwitching] = React.useState(false);
    const [info, setInfo] = React.useState<RemovalInfo | null>(null);
    const [open, setOpen] = React.useState(false);
    const [removing, setRemoving] = React.useState(false);
    const [data, setData] = React.useState(false);
    const [runs, setRuns] = React.useState(false);
    const [keepOut, setKeepOut] = React.useState(false);
    const [failure, setFailure] = React.useState<string | null>(null);
    const mounted = React.useRef(true);
    React.useEffect(() => () => {
        mounted.current = false;
    }, []);

    const enabled = plugin.state !== "disabled";

    const toggle = async () => {
        setSwitching(true);
        try {
            await api.setEnabled(id, !enabled);
            onChanged();
        } catch (error) {
            onError(refusalText(error));
        } finally {
            if (mounted.current) {
                setSwitching(false);
            }
        }
    };

    const openRemove = async () => {
        try {
            const next = await api.removalInfo(id);
            setInfo(next);
            setData(false);
            setRuns(false);
            setKeepOut(false);
            setFailure(next.error ? failureText(next.error) : null);
            setOpen(true);
        } catch (error) {
            onError(refusalText(error));
        }
    };

    // Follow the removal job: the plugin is gone when its route answers 404.
    React.useEffect(() => {
        if (!removing) {
            return undefined;
        }
        let live = true;
        let failures = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;
        const read = async () => {
            try {
                const next = await api.removalInfo(id);
                if (!live) {
                    return;
                }
                failures = 0;
                if (next.state === "removing") {
                    timer = setTimeout(() => void read(), POLL_MS);
                } else {
                    setRemoving(false);
                    if (next.state === "failed" && next.error) {
                        setInfo(next);
                        setFailure(failureText(next.error));
                    } else {
                        // idle again: nothing left to wait for.
                        setOpen(false);
                        onRemoved();
                    }
                }
            } catch (error) {
                if (!live) {
                    return;
                }
                failures += 1;
                if (apiErrorStatus(error) === 404) {
                    setRemoving(false);
                    setOpen(false);
                    onRemoved();
                } else if (failures >= MAX_POLL_FAILURES) {
                    setRemoving(false);
                    setOpen(false);
                    onError(i18n.t("plugins_install_lost"));
                } else {
                    timer = setTimeout(() => void read(), POLL_MS);
                }
            }
        };
        timer = setTimeout(() => void read(), POLL_MS);
        return () => {
            live = false;
            if (timer !== null) {
                clearTimeout(timer);
            }
        };
        // The callbacks are the page's: not a reason to poll again.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api, id, removing]);

    const confirm = async () => {
        const body: RemoveBody = {
            data,
            runs: !runs ? "keep" : keepOut ? "keep_out" : "delete"};
        setFailure(null);
        setRemoving(true);
        try {
            setInfo(await api.startRemoval(id, body));
        } catch (error) {
            setRemoving(false);
            setFailure(refusalText(error));
        }
    };

    return (
        <>
            <FormControlLabel
                label={i18n.t("plugins_enable_toggle")}
                control={<Switch checked={enabled} disabled={switching}
                                 data-testid="plugin-enable"
                                 inputProps={{"aria-label":
                                     i18n.t("plugins_enable_toggle")}}
                                 onChange={() => void toggle()}/>}/>
            <Button size="small" color="error" variant="outlined"
                    data-testid="plugin-remove"
                    onClick={() => void openRemove()}>
                {i18n.t("plugins_remove")}
            </Button>
            <Dialog open={open} fullWidth maxWidth="sm"
                    onClose={() => {
                        if (!removing) {
                            setOpen(false);
                        }
                    }}>
                <DialogTitle>{i18n.t("plugins_remove_title")}</DialogTitle>
                <DialogContent data-testid="plugin-remove-dialog">
                    <Stack spacing={1}>
                        <Typography variant="body2">
                            {i18n.t("plugins_remove_text")}
                        </Typography>
                        {info ? (
                            <Typography variant="body2"
                                        data-testid="plugin-remove-sizes">
                                {i18n.t("plugins_remove_sizes", {
                                    data: sizeLabel(info.data_bytes),
                                    runs: info.runs,
                                    runsSize: sizeLabel(info.runs_bytes),
                                    out: sizeLabel(info.out_bytes)})}
                            </Typography>
                        ) : null}
                        {info?.work_folder ? (
                            <Typography variant="body2"
                                        data-testid="plugin-remove-work">
                                {i18n.t("plugins_remove_work",
                                        {name: info.work_folder})}
                            </Typography>
                        ) : null}
                        <FormControlLabel
                            label={i18n.t("plugins_remove_data")}
                            control={<Checkbox checked={data}
                                               disabled={removing}
                                               data-testid="plugin-remove-data"
                                               onChange={(e) => setData(
                                                   e.target.checked)}/>}/>
                        <FormControlLabel
                            label={i18n.t("plugins_remove_runs")}
                            control={<Checkbox checked={runs}
                                               disabled={removing}
                                               data-testid="plugin-remove-runs"
                                               onChange={(e) => setRuns(
                                                   e.target.checked)}/>}/>
                        {runs && (info?.out_bytes ?? 0) > 0 ? (
                            <FormControlLabel
                                sx={{ml: 3}}
                                label={i18n.t("plugins_remove_keep_out")}
                                control={<Checkbox
                                    checked={keepOut} disabled={removing}
                                    data-testid="plugin-remove-keep-out"
                                    onChange={(e) => setKeepOut(
                                        e.target.checked)}/>}/>
                        ) : null}
                        {removing ? (
                            <Stack direction="row" spacing={1}
                                   alignItems="center">
                                <CircularProgress size={20}/>
                                <Typography variant="body2">
                                    {i18n.t("plugins_remove_working")}
                                </Typography>
                            </Stack>
                        ) : null}
                        {failure ? (
                            <Alert severity="error"
                                   data-testid="plugin-remove-failed">
                                {failure}
                            </Alert>
                        ) : null}
                    </Stack>
                </DialogContent>
                <DialogActions>
                    <Button disabled={removing}
                            data-testid="plugin-remove-cancel"
                            onClick={() => setOpen(false)}>
                        {i18n.t("plugins_cancel")}
                    </Button>
                    <Button color="error" variant="contained"
                            disabled={removing}
                            data-testid="plugin-remove-confirm"
                            onClick={() => void confirm()}>
                        {i18n.t("plugins_remove_go")}
                    </Button>
                </DialogActions>
            </Dialog>
        </>
    );
}
