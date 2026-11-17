import React from "react";
import {Alert, Box, Button, Chip, LinearProgress, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {apiGetJson, apiPost, ApiError} from "../api";
import {
    WorkerInstallJob,
    WorkerSnapshot,
    canCancel,
    isWorking,
    progressValue,
    sizeText,
} from "./archiveWorker";

// The archive clipper's worker, installed and updated from the releases
// (ADR-006 A7): the card the page shows instead of the job view while no
// worker is usable, and over it when an update is asked for; the header's
// chips (the installed version, an update). GET /api/v1/archive/worker
// answers at once and checks the releases in the background: it is asked
// again every second while that check or an install runs (a failed read
// does not stop it: the interval doubles, up to POLL_MAX_MS, until one
// answers). Every read has a number: an answer older than one already
// shown, or than an install's or a cancel's answer (the reads asked before
// it was sent), is dropped. The install and its cancel are SHELL routes
// (only in the desktop's window). Nothing updates on its own: the operator
// presses the button.

const BASE = "/api/v1/archive";
const POLL_MS = 1000;
const POLL_MAX_MS = 8000;

function messageOf(error: unknown): string {
    return (error as ApiError | null)?.detail
        ?? i18n.t("archive_request_failed");
}

// The snapshot, read when mounted and again while something runs.
// `reload(true)` asks the server to check the releases again.
function useWorkerSnapshot() {
    const [snapshot, setSnapshot] = React.useState<WorkerSnapshot | null>(null);
    const [failed, setFailed] = React.useState("");
    const [delay, setDelay] = React.useState(POLL_MS);
    const live = React.useRef(true);
    // The last read's number, and the newest one whose answer counts (an
    // answer at or below it is older than what the card shows).
    const asked = React.useRef(0);
    const shown = React.useRef(0);

    const reload = React.useCallback(async (refresh = false) => {
        const number = ++asked.current;
        try {
            const body = await apiGetJson<{data: WorkerSnapshot}>(
                `${BASE}/worker${refresh ? "?refresh=true" : ""}`);
            if (live.current && number > shown.current) {
                shown.current = number;
                setSnapshot(body.data);
                setFailed("");
                setDelay(POLL_MS);
            }
        } catch (e) {
            if (live.current && number > shown.current) {
                shown.current = number;
                setFailed(messageOf(e));
                setDelay((last) => Math.min(last * 2, POLL_MAX_MS));
            }
        }
    }, []);

    React.useEffect(() => {
        live.current = true;
        reload();
        return () => {
            live.current = false;
        };
    }, [reload]);

    // Still asked while the last read failed: what ran may have ended.
    const working = isWorking(snapshot);
    React.useEffect(() => {
        if (!working) {
            return undefined;
        }
        const timer = setInterval(() => reload(), delay);
        return () => clearInterval(timer);
    }, [working, delay, reload]);

    // Before a POST: the reads asked so far are older than its answer.
    const mark = React.useCallback(() => asked.current, []);
    // A POST's answer (the job), newer than the reads asked before `mark`.
    const setJob = React.useCallback((job: WorkerInstallJob, before: number) => {
        if (!live.current) {
            return;
        }
        shown.current = Math.max(shown.current, before);
        setSnapshot((known) => known === null ? known : {...known, job});
    }, []);
    return {snapshot, failed, reload, mark, setJob};
}

// The header's chips: the worker's version and, when a newer one is out,
// the button that opens the update card.
export function WorkerChips({version, onUpdate}: {
    version?: string | null;
    onUpdate: () => void;
}) {
    const {snapshot} = useWorkerSnapshot();
    return (
        <Stack direction="row" spacing={1} alignItems="center">
            {version ? (
                <Chip size="small" variant="outlined"
                      data-testid="archive-worker-version"
                      label={i18n.t("archive_worker_chip", {version})}/>
            ) : null}
            {snapshot?.update_available && snapshot.latest ? (
                <Chip size="small" color="warning" clickable
                      data-testid="archive-worker-update"
                      onClick={onUpdate}
                      label={i18n.t("archive_worker_update_available",
                                    {version: snapshot.latest.version})}/>
            ) : null}
        </Stack>
    );
}

// The card: what is installed and what is out, the button, the progress,
// why it failed. `update`: the worker is usable (the header opened it);
// `reason`: why the status says it is not.
export default function WorkerInstall({update, reason, readOnly, onInstalled,
                                       onClose}: {
    update: boolean;
    reason?: string | null;
    readOnly: boolean;
    onInstalled: () => void;
    onClose?: () => void;
}) {
    const {snapshot, failed, reload, mark, setJob} = useWorkerSnapshot();
    const [starting, setStarting] = React.useState(false);
    const [refused, setRefused] = React.useState("");
    // The install this card saw running: its end (done) reloads the page's
    // status, which then probes the new worker -- once per install.
    const watched = React.useRef<number | null>(null);
    const reported = React.useRef<number | null>(null);

    const job = snapshot?.job ?? null;
    React.useEffect(() => {
        if (job === null) {
            return;
        }
        if (job.status === "running") {
            watched.current = job.revision;
        } else if (job.revision === watched.current) {
            watched.current = null;
            if (job.status === "done" && reported.current !== job.revision) {
                reported.current = job.revision;
                onInstalled();
            }
        }
    }, [job, onInstalled]);

    const install = async () => {
        if (!snapshot?.latest) {
            return;
        }
        setStarting(true);
        setRefused("");
        const before = mark();
        try {
            const text = await apiPost(`${BASE}/worker:install`,
                                       {version: snapshot.latest.version});
            const started = (JSON.parse(text) as {data: {job: WorkerInstallJob}})
                .data.job;
            watched.current = started.revision;
            setJob(started, before);
        } catch (e) {
            setRefused(messageOf(e));
            // The latest may have changed, or an install run elsewhere.
            reload();
        } finally {
            setStarting(false);
        }
    };

    const cancel = async () => {
        if (job === null) {
            return;
        }
        const before = mark();
        try {
            const text = await apiPost(`${BASE}/worker:cancel`,
                                       {revision: job.revision});
            setJob((JSON.parse(text) as {data: {job: WorkerInstallJob}}).data.job,
                   before);
        } catch (e) {
            setRefused(messageOf(e));
        }
    };

    const running = job?.status === "running";
    const latest = snapshot?.latest ?? null;
    // The update card offers only a newer version than the one installed
    // (once it is in, it is not offered again).
    const offered = !update || Boolean(snapshot?.update_available);
    const check = snapshot?.check ?? null;
    const value = job && running ? progressValue(job) : null;
    return (
        <Box sx={{p: update ? 2 : 3, maxWidth: 720}}>
            <Alert severity={update ? "warning" : "info"}
                   data-testid="archive-worker-card"
                   onClose={update && onClose && !running ? onClose : undefined}>
                <Typography fontWeight={700}>
                    {i18n.t(update ? "archive_worker_update_title"
                        : "archive_worker_install_title")}
                </Typography>
                {update ? null : (
                    <Typography variant="body2">
                        {i18n.t("archive_worker_install_body")}
                    </Typography>
                )}
                {reason && !update ? (
                    <Typography variant="body2" sx={{mt: 1}}>
                        {i18n.t("archive_unavailable_reason", {reason})}
                    </Typography>
                ) : null}
                {snapshot?.env_command ? (
                    <Typography variant="body2" sx={{mt: 1}}
                                data-testid="archive-worker-env">
                        {i18n.t("archive_worker_env_command")}
                    </Typography>
                ) : null}
                <Box sx={{mt: 1.5}}>
                    {snapshot?.installed?.version ? (
                        <Typography variant="body2">
                            {i18n.t("archive_worker_installed",
                                    {version: snapshot.installed.version})}
                        </Typography>
                    ) : null}
                    {latest ? (
                        <Typography variant="body2"
                                    data-testid="archive-worker-latest">
                            {i18n.t("archive_worker_latest",
                                    {version: latest.version,
                                     size: sizeText(latest.size)})}
                        </Typography>
                    ) : check?.status === "checking" || snapshot === null ? (
                        failed ? null : (
                            <Typography variant="body2">
                                {i18n.t("archive_worker_checking")}
                            </Typography>
                        )
                    ) : check?.status === "error" ? null : (
                        <Typography variant="body2">
                            {i18n.t("archive_worker_no_release")}
                        </Typography>
                    )}
                </Box>
                {check?.status === "error" && check.message ? (
                    <Typography variant="body2" color="error" sx={{mt: 1}}
                                data-testid="archive-worker-check-error">
                        {check.message}
                    </Typography>
                ) : null}
                {failed ? (
                    <Typography variant="body2" color="error" sx={{mt: 1}}>
                        {failed}
                    </Typography>
                ) : null}
                {job && running ? (
                    <Box sx={{mt: 1.5}} data-testid="archive-worker-progress">
                        <Typography variant="body2">
                            {i18n.t(`archive_worker_stage_${job.stage ?? "download"}`)}
                            {job.stage === "download" && job.total > 0
                                ? ` ${sizeText(job.done)} / ${sizeText(job.total)}`
                                : ""}
                        </Typography>
                        <LinearProgress sx={{mt: 0.5}}
                                        variant={value === null ? "indeterminate"
                                            : "determinate"}
                                        value={value ?? undefined}/>
                    </Box>
                ) : null}
                {job && !running && job.status !== "idle" && job.message ? (
                    <Typography variant="body2" sx={{mt: 1}}
                                color={job.status === "error" ? "error" : undefined}
                                data-testid="archive-worker-result">
                        {job.message}
                    </Typography>
                ) : null}
                {refused ? (
                    <Typography variant="body2" color="error" sx={{mt: 1}}
                                data-testid="archive-worker-refused">
                        {refused}
                    </Typography>
                ) : null}
                <Stack direction="row" spacing={1} sx={{mt: 1.5}}>
                    <Button variant="contained" size="small"
                            data-testid="archive-worker-install"
                            disabled={readOnly || !latest || !offered || running
                                      || starting}
                            onClick={install}>
                        {i18n.t(update ? "archive_worker_update"
                            : "archive_worker_install")}
                    </Button>
                    {job && canCancel(job) ? (
                        <Button variant="outlined" size="small"
                                data-testid="archive-worker-cancel"
                                disabled={readOnly} onClick={cancel}>
                            {i18n.t("archive_worker_cancel")}
                        </Button>
                    ) : null}
                    {check?.status === "error" || (check?.status === "done"
                                                   && !latest) ? (
                        <Button variant="text" size="small"
                                data-testid="archive-worker-recheck"
                                onClick={() => reload(true)}>
                            {i18n.t("archive_worker_recheck")}
                        </Button>
                    ) : null}
                </Stack>
                {readOnly ? (
                    <Typography variant="body2" sx={{mt: 1}}
                                data-testid="archive-worker-read-only">
                        {i18n.t("archive_worker_read_only")}
                    </Typography>
                ) : null}
            </Alert>
        </Box>
    );
}
