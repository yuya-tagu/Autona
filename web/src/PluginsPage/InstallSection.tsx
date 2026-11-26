import React from "react";
import {
    Alert,
    Button,
    CircularProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {apiErrorStatus} from "../api";
import {PluginsApi} from "./pluginsApi";
import {ConsentSheet, InstallFailure, InstallStatus} from "./pluginTypes";
import {pluginText, sizeLabel, versionLabel} from "./pluginText";
import {refusalText} from "./RunView";

// The install flow (ADR-009 P5a): the zip's bytes -> an install -> the
// consent sheet -> installing -> done. The dialog follows one install; the
// host holds one at a time.

const POLL_MS = 1000;
// Polls that fail in a row before the page gives the install up.
const MAX_POLL_FAILURES = 5;

// An install's or a removal's failure in words: the code (contract_1.json
// install.errors), then the problems it carries (a code the page has no
// words for is shown as it is).
export function failureText(failure: InstallFailure): string {
    const key = `plugins_install_err_${failure.code}`;
    const head = i18n.exists(key)
        ? i18n.t(key) : i18n.t("plugins_install_err_failed");
    const problems = (failure.problems ?? []).map((problem) => {
        const pKey = `plugins_install_problem_${problem}`;
        return i18n.exists(pKey) ? i18n.t(pKey) : problem;
    });
    return problems.length ? `${head} (${problems.join(" / ")})` : head;
}

function Row({label, value, testId}: {
    label: string; value: React.ReactNode; testId?: string;
}) {
    return (
        <Typography variant="body2" data-testid={testId}>
            <strong>{label}</strong>{": "}{value}
        </Typography>
    );
}

export function SheetView({sheet, language}: {
    sheet: ConsentSheet; language: string;
}) {
    const p = sheet.permissions;
    return (
        <Stack spacing={1} data-testid="plugin-sheet">
            <Typography variant="subtitle1">
                {pluginText(sheet.name, language)}
            </Typography>
            {sheet.description ? (
                <Typography variant="body2">
                    {pluginText(sheet.description, language)}
                </Typography>
            ) : null}
            <Row label={i18n.t("plugins_sheet_id")} value={sheet.id}/>
            <Row label={i18n.t("plugins_sheet_version")}
                 value={versionLabel(sheet.version)}/>
            {sheet.installed_version !== null ? (
                <Row label={i18n.t("plugins_sheet_installed")}
                     testId="plugin-sheet-installed"
                     value={versionLabel(sheet.installed_version)}/>
            ) : null}
            <Row label={i18n.t("plugins_sheet_author")} value={sheet.author}/>
            <Row label={i18n.t("plugins_sheet_license")}
                 value={sheet.license}/>
            <Row label={i18n.t("plugins_sheet_source")}
                 testId="plugin-sheet-source"
                 value={[i18n.t(`plugins_sheet_source_${sheet.source.kind}`),
                         sheet.source.name].filter(Boolean).join(" ")}/>
            <Row label={i18n.t("plugins_sheet_verified")}
                 testId="plugin-sheet-verified"
                 value={i18n.t(sheet.verified ? "plugins_sheet_verified_yes"
                     : "plugins_sheet_verified_no")}/>
            <Row label={i18n.t("plugins_sheet_size")}
                 value={`${sizeLabel(sheet.size_bytes)} (zip ${
                     sizeLabel(sheet.zip_bytes)})`}/>
            <Typography variant="subtitle2">
                {i18n.t("plugins_declared")}
            </Typography>
            <Stack spacing={0.25} data-testid="plugin-sheet-permissions">
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
                    {i18n.t("plugins_declared_spawn", {value: i18n.t(
                        p.spawn ? "plugins_yes" : "plugins_no")})}
                </Typography>
                <Typography variant="body2">
                    {i18n.t("plugins_declared_api", {
                        value: i18n.t(`plugins_api_${p.autona_api}`)})}
                </Typography>
                {p.note ? (
                    <Typography variant="body2">
                        {pluginText(p.note, language)}
                    </Typography>
                ) : null}
            </Stack>
            <Typography variant="subtitle2">
                {i18n.t("plugins_settings")}
            </Typography>
            <Typography variant="body2" data-testid="plugin-sheet-settings">
                {sheet.settings.length
                    ? sheet.settings.map(
                        (f) => pluginText(f.label, language)).join(", ")
                    : i18n.t("plugins_declared_none")}
            </Typography>
            <Typography variant="subtitle2">
                {i18n.t("plugins_jobs")}
            </Typography>
            <Typography variant="body2" data-testid="plugin-sheet-jobs">
                {sheet.jobs.length
                    ? sheet.jobs.map(
                        (j) => pluginText(j.title, language)).join(", ")
                    : i18n.t("plugins_declared_none")}
            </Typography>
        </Stack>
    );
}

export default function InstallSection({api, language, onChanged, onError,
                                        onDone}: {
    api: PluginsApi;
    language: string;
    // The list changed (a plugin was installed).
    onChanged: () => void;
    onError: (text: string) => void;
    onDone: (text: string) => void;
}) {
    const input = React.useRef<HTMLInputElement>(null);
    const [uploading, setUploading] = React.useState(false);
    const [status, setStatus] = React.useState<InstallStatus | null>(null);
    // A request (consent / cancel) is on its way: the buttons wait.
    const [sending, setSending] = React.useState(false);
    const mounted = React.useRef(true);
    React.useEffect(() => () => {
        mounted.current = false;
    }, []);

    const installId = status?.install_id ?? null;
    const state = status?.state ?? null;
    const following = state === "preparing" || state === "installing";

    // Follow a preparing / installing install until it needs the user or ends.
    React.useEffect(() => {
        if (!installId || !following) {
            return undefined;
        }
        let live = true;
        let failures = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;
        const read = async () => {
            try {
                const next = await api.installStatus(installId);
                if (!live) {
                    return;
                }
                failures = 0;
                setStatus(next);
                if (next.state === "preparing"
                    || next.state === "installing") {
                    timer = setTimeout(() => void read(), POLL_MS);
                }
            } catch (error) {
                if (!live) {
                    return;
                }
                failures += 1;
                if (apiErrorStatus(error) === 404
                    || failures >= MAX_POLL_FAILURES) {
                    setStatus(null);
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
        // onError is the page's: not a reason to start the polling again.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api, installId, following]);

    // The end states: done refreshes the list; cancelled just closes.
    React.useEffect(() => {
        if (state === "done") {
            onDone(i18n.t("plugins_install_done"));
            onChanged();
            setStatus(null);
        } else if (state === "cancelled") {
            setStatus(null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state]);

    const upload = async (file: File) => {
        setUploading(true);
        try {
            const {upload_id: uploadId} = await api.uploadZip(file);
            setStatus(await api.startInstall(uploadId));
        } catch (error) {
            onError(refusalText(error));
        } finally {
            if (mounted.current) {
                setUploading(false);
            }
        }
    };

    const onPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        // The same file can be picked again after this one.
        event.target.value = "";
        if (file) {
            void upload(file);
        }
    };

    const approve = async () => {
        if (!status?.sheet) {
            return;
        }
        setSending(true);
        try {
            setStatus(await api.consentInstall(
                status.install_id, status.sheet.consent_digest));
        } catch (error) {
            onError(refusalText(error));
        } finally {
            setSending(false);
        }
    };

    const decline = async () => {
        const current = status;
        if (!current) {
            return;
        }
        setSending(true);
        try {
            setStatus(await api.cancelInstall(current.install_id));
        } catch (error) {
            onError(refusalText(error));
        } finally {
            setSending(false);
        }
    };

    const sheet = status?.sheet ?? null;
    const refused = sheet !== null && !sheet.needs_consent;
    const shown = state === "preparing" || state === "consent"
        || state === "installing" || state === "failed";

    return (
        <>
            <input ref={input} type="file" hidden accept=".zip,application/zip"
                   data-testid="plugin-install-input" onChange={onPicked}/>
            <div>
                <Button size="small" variant="outlined"
                        disabled={uploading || status !== null}
                        data-testid="plugin-install-add"
                        onClick={() => input.current?.click()}>
                    {uploading ? i18n.t("plugins_install_uploading")
                        : i18n.t("plugins_install_add")}
                </Button>
            </div>
            <Dialog open={shown} fullWidth maxWidth="sm"
                    onClose={() => {
                        // Never closes by a stray click while it works.
                        if (state === "consent") {
                            void decline();
                        } else if (state === "failed") {
                            setStatus(null);
                        }
                    }}>
                <DialogTitle>{i18n.t("plugins_install_title")}</DialogTitle>
                <DialogContent data-testid="plugin-install-dialog">
                    {state === "preparing" || state === "installing" ? (
                        <Stack direction="row" spacing={1}
                               alignItems="center">
                            <CircularProgress size={20}/>
                            <Typography variant="body2"
                                        data-testid="plugin-install-working">
                                {i18n.t(state === "preparing"
                                    ? "plugins_install_preparing"
                                    : "plugins_install_installing")}
                            </Typography>
                        </Stack>
                    ) : null}
                    {state === "failed" && status?.error ? (
                        <Alert severity="error"
                               data-testid="plugin-install-failed">
                            {failureText(status.error)}
                        </Alert>
                    ) : null}
                    {state === "consent" && sheet ? (
                        <Stack spacing={1.5}>
                            <Alert severity="warning">
                                {i18n.t("plugins_consent_text")}
                            </Alert>
                            {sheet.decision === "update" ? (
                                <Alert severity="info"
                                       data-testid="plugin-sheet-decision">
                                    {i18n.t("plugins_sheet_update", {
                                        from: versionLabel(
                                            sheet.installed_version),
                                        to: versionLabel(sheet.version)})}
                                </Alert>
                            ) : null}
                            {sheet.decision === "confirm_same_or_older" ? (
                                <Alert severity="warning"
                                       data-testid="plugin-sheet-decision">
                                    {i18n.t("plugins_sheet_same_or_older", {
                                        from: versionLabel(
                                            sheet.installed_version),
                                        to: versionLabel(sheet.version)})}
                                </Alert>
                            ) : null}
                            {sheet.decision === "refuse_dev_id"
                                || sheet.decision === "refuse_reserved" ? (
                                <Alert severity="error"
                                       data-testid="plugin-sheet-decision">
                                    {i18n.t(`plugins_sheet_${sheet.decision}`)}
                                </Alert>
                            ) : null}
                            {!sheet.verified && sheet.source.kind === "manual"
                                ? (
                                <Alert severity="warning">
                                    {i18n.t("plugins_sheet_unverified_note")}
                                </Alert>
                            ) : null}
                            {status?.error ? (
                                <Alert severity="error"
                                       data-testid="plugin-install-error">
                                    {status.error.reason === "busy"
                                        ? i18n.t("plugins_install_err_running")
                                        : failureText(status.error)}
                                </Alert>
                            ) : null}
                            <SheetView sheet={sheet} language={language}/>
                        </Stack>
                    ) : null}
                </DialogContent>
                <DialogActions>
                    {state === "failed" ? (
                        <Button data-testid="plugin-install-close"
                                onClick={() => setStatus(null)}>
                            {i18n.t("plugins_action_close")}
                        </Button>
                    ) : null}
                    {state === "preparing" || state === "consent" ? (
                        <Button disabled={sending}
                                data-testid="plugin-install-cancel"
                                onClick={() => void decline()}>
                            {i18n.t("plugins_cancel")}
                        </Button>
                    ) : null}
                    {state === "consent" ? (
                        <Button variant="contained" color="warning"
                                disabled={sending || refused}
                                data-testid="plugin-install-approve"
                                onClick={() => void approve()}>
                            {i18n.t("plugins_install_approve")}
                        </Button>
                    ) : null}
                </DialogActions>
            </Dialog>
        </>
    );
}
