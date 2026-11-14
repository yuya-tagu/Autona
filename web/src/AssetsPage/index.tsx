import React from "react";
import {
    Alert,
    Autocomplete,
    Box,
    Button,
    Checkbox,
    CircularProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogContentText,
    DialogTitle,
    FormControlLabel,
    FormGroup,
    LinearProgress,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {apiDelete, apiErrorStatus, apiGetJson, apiPost} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {WRITE_OPTIONS} from "../PanelShell/requests";
import {useDocumentVisibility} from "../PanelShell/useDocumentVisibility";
import {useShellAccess} from "../PanelShell/useShellAccess";
import socketConnection from "../websocketConnection";
import {CardFrame, GuideFrame, Note} from "../GuidePages/parts";
import {
    CANCEL_PATH,
    GameRow,
    IMPORT_ZIP_PATH,
    INSTALL_PATH,
    JobState,
    MAX_UPLOAD_BYTES,
    PACKS_PATH,
    PACKS_TIMEOUT_MS,
    PacksPayload,
    REVEAL_PATH,
    SOURCE_URL,
    UPLOADS_PATH,
    UploadAnswer,
    UploadHandle,
    describeFailure,
    errorText,
    humanSize,
    uploadZip,
} from "./assetsApi";
import {useAssetJob} from "./useAssetJob";

// Add game assets (ADR-005 S-final SF-4b-2), /web/assets: the Qt dialog
// AutonaGameAssetGuideWindow as a page of the shared view. Three steps: the
// explanation, download the packs from the public upstream source, import a
// ZIP. The work is core's (AssetPackService); this page asks for it through
// /api/v1/assets* and shows the job.
//
// The writes and the job are the shell's (SHELL routes: only the desktop's
// shared view may call them), so anywhere else -- a browser, the headless
// app -- the page says so and links the source, and reads nothing.
// useShellAccess({withToken: false}) tells the two apart without keeping
// the access token. A folder cannot be imported from a page (it cannot name
// one): the ZIP route is the only one until the shell's folder picker
// (SF-5a). Everything shown is text: nothing here renders HTML.

type Notice = {severity: "success" | "info" | "warning" | "error";
    text: string};

function useUnmountCleanup(cleanup: () => void) {
    const latest = React.useRef(cleanup);
    latest.current = cleanup;
    React.useEffect(() => () => latest.current(), []);
}

function Collapsible({label, children}: {
    label: string;
    children: React.ReactNode;
}) {
    const [open, setOpen] = React.useState(false);
    return (
        <Box>
            <Button size="small" aria-expanded={open}
                    onClick={() => setOpen(!open)}>
                {open ? i18n.t("assets_details_hide", {label})
                      : i18n.t("assets_details_show", {label})}
            </Button>
            {open ? <Box sx={{mt: 0.5}}>{children}</Box> : null}
        </Box>
    );
}

const WRAP = {overflowWrap: "anywhere"} as const;

// ── The source row and the games folder ─────────────────────────────────

function SourceRow({withFolder}: {withFolder: boolean}) {
    const reveal = async () => {
        try {
            await apiPost(REVEAL_PATH, {}, WRITE_OPTIONS);
        } catch (error) {
            console.error(error);
            showSnackbar(i18n.t("assets_reveal_failed"), "error");
        }
    };
    return (
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap"
               useFlexGap>
            <Button variant="outlined" size="small" component="a"
                    href={SOURCE_URL} target="_blank"
                    rel="noopener noreferrer">
                {i18n.t("assets_open_source")}
            </Button>
            {withFolder ? (
                <Button variant="outlined" size="small"
                        onClick={() => void reveal()}>
                    {i18n.t("assets_open_folder")}
                </Button>
            ) : null}
        </Stack>
    );
}

// ── Online download ─────────────────────────────────────────────────────

type PacksState =
    | {status: "loading"; payload: PacksPayload | null}
    | {status: "ready"; payload: PacksPayload}
    | {status: "failed"; payload: PacksPayload | null};

function usePacks(enabled: boolean) {
    const [state, setState] = React.useState<PacksState>(
        {status: "loading", payload: null});
    const sequence = React.useRef(0);
    const live = React.useRef(true);
    React.useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
        };
    }, []);

    const load = React.useCallback(async (refresh: boolean) => {
        const mine = ++sequence.current;
        setState((known) => ({status: "loading", payload: known.payload}));
        try {
            const body = await apiGetJson<{data: PacksPayload}>(
                `${PACKS_PATH}${refresh ? "?refresh=true" : ""}`,
                {timeoutMs: PACKS_TIMEOUT_MS});
            if (live.current && mine === sequence.current) {
                setState({status: "ready", payload: body.data});
            }
        } catch (error) {
            if (live.current && mine === sequence.current) {
                console.error(error);
                setState((known) => ({status: "failed",
                                      payload: known.payload}));
            }
        }
    }, []);

    React.useEffect(() => {
        if (enabled) {
            void load(false);
        }
    }, [enabled, load]);

    return {state, load};
}

function packLabel(pack: GameRow["packs"][number]): string {
    const flags: string[] = [];
    if (pack.updateAvailable) {
        flags.push(i18n.t("assets_pack_update"));
    } else if (pack.installed) {
        flags.push(i18n.t("assets_pack_installed"));
    }
    return `${pack.name}  (${humanSize(pack.size ?? 0)})`
        + (flags.length ? `  - ${flags.join(", ")}` : "");
}

function OnlineCard({packs, busy, onStart, onRefresh}: {
    packs: PacksState;
    busy: boolean;
    onStart: (game: GameRow, names: string[]) => void;
    onRefresh: () => void;
}) {
    const games = (packs.payload?.games ?? []).filter((game) => game.available);
    const [code, setCode] = React.useState<string | null>(null);
    // Pack choices by game; a game not touched has its default packs.
    const [chosen, setChosen] = React.useState<Record<string, string[]>>({});
    const [confirming, setConfirming] = React.useState<
        {game: GameRow; names: string[]} | null>(null);
    const game = games.find((row) => row.code === code) ?? null;
    const names = game
        ? chosen[game.code] ?? game.packs.filter((p) => p.default)
            .map((p) => p.name)
        : [];

    const toggle = (name: string) => {
        if (!game) {
            return;
        }
        setChosen({...chosen, [game.code]: names.includes(name)
            ? names.filter((n) => n !== name) : [...names, name]});
    };

    let status: string;
    if (packs.status === "loading" && games.length === 0) {
        status = i18n.t("assets_loading_list");
    } else if (packs.status === "failed") {
        status = i18n.t("assets_list_failed");
    } else if (packs.payload?.index.status === "error") {
        // A refresh that failed over an older list keeps the list.
        status = i18n.t("assets_list_failed") + " "
            + i18n.t(`assets_index_${packs.payload.index.error}`, {
                defaultValue: ""});
    } else if (games.length === 0) {
        status = i18n.t("assets_no_games");
    } else {
        status = i18n.t("assets_pick_game");
    }

    return (
        <CardFrame title={i18n.t("assets_online_title")}>
            <Typography variant="body2" sx={WRAP}>{status.trim()}</Typography>
            <Autocomplete
                size="small"
                options={games}
                value={game}
                disabled={games.length === 0}
                getOptionLabel={(row) => `${row.name}  (${row.code})`}
                isOptionEqualToValue={(a, b) => a.code === b.code}
                onChange={(_event, row) => setCode(row ? row.code : null)}
                renderInput={(params) => (
                    <TextField {...params} label={i18n.t("assets_game")}/>
                )}/>
            {game ? (
                <Box role="group" aria-label={i18n.t("assets_packs")}>
                    <Typography variant="subtitle2">
                        {i18n.t("assets_packs")}
                    </Typography>
                    <FormGroup>
                        {game.packs.map((pack) => (
                            <FormControlLabel
                                key={pack.name}
                                control={<Checkbox
                                    checked={names.includes(pack.name)}
                                    onChange={() => toggle(pack.name)}/>}
                                label={packLabel(pack)}
                                sx={WRAP}/>
                        ))}
                    </FormGroup>
                </Box>
            ) : null}
            <Note>{i18n.t("assets_packs_note")}</Note>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="contained" size="small"
                        disabled={busy || !game || names.length === 0}
                        onClick={() => game && setConfirming(
                            {game, names})}>
                    {i18n.t("assets_download")}
                </Button>
                <Button variant="outlined" size="small"
                        disabled={packs.status === "loading"}
                        onClick={onRefresh}>
                    {i18n.t("assets_reload_list")}
                </Button>
            </Stack>
            <Dialog open={confirming !== null} fullWidth maxWidth="xs"
                    onClose={() => setConfirming(null)}>
                <DialogTitle>{i18n.t("assets_install_confirm_title")}</DialogTitle>
                <DialogContent>
                    <DialogContentText sx={WRAP}>
                        {confirming ? i18n.t("assets_install_confirm_body", {
                            game: `${confirming.game.name} (${confirming.game.code})`,
                            count: confirming.names.length,
                            size: humanSize(confirming.game.packs
                                .filter((p) => confirming.names.includes(p.name))
                                .reduce((sum, p) => sum + (p.size ?? 0), 0)),
                        }) : ""}
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setConfirming(null)}>
                        {i18n.t("assets_cancel")}
                    </Button>
                    <Button variant="contained" onClick={() => {
                        const request = confirming;
                        setConfirming(null);
                        if (request) {
                            onStart(request.game, request.names);
                        }
                    }}>
                        {i18n.t("assets_download")}
                    </Button>
                </DialogActions>
            </Dialog>
        </CardFrame>
    );
}

// ── ZIP import ──────────────────────────────────────────────────────────

type Upload =
    | {phase: "idle"}
    | {phase: "sending"; name: string; sent: number; total: number};

type Overwrite = {answer: UploadAnswer; selected: string[]};

function ZipCard({busy, onBusyChange, onImport, notice, setNotice}: {
    busy: boolean;
    // Whether the card itself is busy (sending, or asking about
    // overwrites): the download waits for it as the ZIP waits for the job.
    onBusyChange: (busy: boolean) => void;
    // Start the import job of an uploaded ZIP; resolves true when started.
    onImport: (upload: string, overwrite: string[]) => Promise<boolean>;
    notice: Notice | null;
    setNotice: (notice: Notice | null) => void;
}) {
    const [upload, setUpload] = React.useState<Upload>({phase: "idle"});
    const [asking, setAsking] = React.useState<Overwrite | null>(null);
    const handle = React.useRef<UploadHandle | null>(null);
    const held = React.useRef<string | null>(null);
    const input = React.useRef<HTMLInputElement | null>(null);

    const cardBusy = upload.phase === "sending" || asking !== null;
    React.useEffect(() => {
        onBusyChange(cardBusy);
    }, [cardBusy, onBusyChange]);
    React.useEffect(() => () => onBusyChange(false), [onBusyChange]);

    // The server holds one uploaded ZIP for 15 minutes. One this page no
    // longer wants is dropped (best effort: a page going away may not get
    // the request out).
    const drop = React.useCallback((id: string | null, leaving = false) => {
        if (id === null) {
            return;
        }
        if (held.current === id) {
            held.current = null;
        }
        apiDelete(`${UPLOADS_PATH}/${encodeURIComponent(id)}`,
                  leaving ? {...WRITE_OPTIONS, keepalive: true}
                          : WRITE_OPTIONS).catch(
            (error: unknown) => console.error(error));
    }, []);

    const leave = React.useCallback(() => {
        handle.current?.abort();
        handle.current = null;
        drop(held.current, true);
    }, [drop]);
    useUnmountCleanup(leave);
    React.useEffect(() => {
        window.addEventListener("pagehide", leave);
        return () => window.removeEventListener("pagehide", leave);
    }, [leave]);

    const choose = async (file: File) => {
        setNotice(null);
        if (file.size > MAX_UPLOAD_BYTES) {
            setNotice({severity: "error", text: i18n.t(
                "assets_zip_too_large", {
                    size: humanSize(file.size),
                    limit: humanSize(MAX_UPLOAD_BYTES)})});
            return;
        }
        drop(held.current);
        const sending = uploadZip(file, (sent, total) => setUpload({
            phase: "sending", name: file.name, sent, total}));
        handle.current = sending;
        setUpload({phase: "sending", name: file.name, sent: 0,
                   total: file.size});
        let answer: UploadAnswer;
        try {
            answer = await sending.promise;
        } catch (error) {
            if (handle.current === sending) {
                handle.current = null;
                setUpload({phase: "idle"});
                if ((error as Error).name === "AbortError") {
                    setNotice({severity: "info",
                               text: i18n.t("assets_upload_cancelled")});
                } else {
                    setNotice({severity: "error",
                               text: describeFailure(error)});
                }
            }
            return;
        }
        if (handle.current !== sending) {
            // Stopped while the answer was on its way: it is not wanted.
            drop(answer.upload);
            return;
        }
        handle.current = null;
        setUpload({phase: "idle"});
        held.current = answer.upload;
        if (answer.conflicts.length === 0) {
            await startImport(answer.upload, []);
        } else {
            setAsking({answer, selected: []});
        }
    };

    const startImport = async (id: string, overwrite: string[]) => {
        // While the request is out the job may already have taken the file:
        // the page no longer holds it (leaving now drops nothing), and drops
        // it itself only when the job did not start.
        if (held.current === id) {
            held.current = null;
        }
        const started = await onImport(id, overwrite);
        if (!started) {
            drop(id);
        }
    };

    const stopSending = () => {
        handle.current?.abort();
    };

    const answerDialog = (go: boolean) => {
        const request = asking;
        setAsking(null);
        if (!request) {
            return;
        }
        const {answer, selected} = request;
        // Every game here is installed and every one declined: nothing to
        // import (as the Qt dialog said).
        const nothing = answer.games.every(
            (name) => answer.conflicts.includes(name)
                && !selected.includes(name));
        if (!go || nothing) {
            drop(answer.upload);
            setNotice({severity: "info", text: i18n.t(
                "assets_nothing_imported")});
            return;
        }
        void startImport(answer.upload, selected);
    };

    // The upload is held for `expiresInSec`: the dialog is closed when it
    // is over (nothing to import any more).
    const askedUpload = asking?.answer.upload ?? null;
    const askedExpiry = asking?.answer.expiresInSec ?? 0;
    React.useEffect(() => {
        if (askedUpload === null) {
            return undefined;
        }
        const timer = setTimeout(() => {
            setAsking(null);
            drop(askedUpload);
            setNotice({severity: "warning",
                       text: i18n.t("assets_upload_expired")});
        }, askedExpiry * 1000);
        return () => clearTimeout(timer);
    }, [askedUpload, askedExpiry, drop, setNotice]);

    const sending = upload.phase === "sending" ? upload : null;
    const percent = sending && sending.total > 0
        ? Math.min(100, Math.round(100 * sending.sent / sending.total)) : 0;

    return (
        <CardFrame title={i18n.t("assets_zip_title")}>
            <Typography variant="body2">{i18n.t("assets_zip_note")}</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="outlined" size="small"
                        disabled={busy || sending !== null || asking !== null}
                        onClick={() => input.current?.click()}>
                    {i18n.t("assets_zip_choose")}
                </Button>
                {sending ? (
                    <Button size="small" color="warning"
                            onClick={stopSending}>
                        {i18n.t("assets_upload_cancel")}
                    </Button>
                ) : null}
            </Stack>
            <input ref={input} type="file" hidden
                   accept=".zip,application/zip"
                   aria-label={i18n.t("assets_zip_choose")}
                   onChange={(event) => {
                       const file = event.target.files?.[0];
                       // The same file can be chosen again.
                       event.target.value = "";
                       if (file) {
                           void choose(file);
                       }
                   }}/>
            {sending ? (
                <Box>
                    <Typography variant="body2" sx={WRAP}>
                        {i18n.t("assets_uploading", {
                            name: sending.name,
                            sent: humanSize(sending.sent),
                            total: humanSize(sending.total)})}
                    </Typography>
                    <LinearProgress variant="determinate" value={percent}
                                    aria-label={i18n.t("assets_upload_progress")}/>
                </Box>
            ) : null}
            {notice ? (
                <Alert severity={notice.severity} sx={WRAP}>{notice.text}</Alert>
            ) : null}
            {/* A click outside or Esc does not close it: the upload is only
                dropped by the explicit Cancel (or when it expires). */}
            <Dialog open={asking !== null} fullWidth maxWidth="xs"
                    onClose={(_event, reason) => {
                        if (reason !== "backdropClick"
                                && reason !== "escapeKeyDown") {
                            answerDialog(false);
                        }
                    }}>
                <DialogTitle>{i18n.t("assets_overwrite_title")}</DialogTitle>
                <DialogContent>
                    {asking ? (
                        <Stack spacing={1}>
                            <DialogContentText sx={WRAP}>
                                {i18n.t("assets_overwrite_games", {
                                    games: asking.answer.games.join(", ")})}
                            </DialogContentText>
                            <DialogContentText>
                                {i18n.t("assets_overwrite_body")}
                            </DialogContentText>
                            <FormGroup>
                                {asking.answer.conflicts.map((name) => (
                                    <FormControlLabel
                                        key={name}
                                        control={<Checkbox
                                            checked={asking.selected
                                                .includes(name)}
                                            onChange={() => setAsking({
                                                ...asking,
                                                selected: asking.selected
                                                    .includes(name)
                                                    ? asking.selected.filter(
                                                        (n) => n !== name)
                                                    : [...asking.selected,
                                                       name]})}/>}
                                        label={name} sx={WRAP}/>
                                ))}
                            </FormGroup>
                        </Stack>
                    ) : null}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => answerDialog(false)}>
                        {i18n.t("assets_cancel")}
                    </Button>
                    <Button variant="contained"
                            onClick={() => answerDialog(true)}>
                        {i18n.t("assets_import")}
                    </Button>
                </DialogActions>
            </Dialog>
        </CardFrame>
    );
}

// ── The job ─────────────────────────────────────────────────────────────

// An install counts bytes in every stage (download, extract, copy, scan);
// an import counts entries / files.
function jobAmount(job: JobState, value: number): string {
    return job.operation === "install" ? humanSize(value) : String(value);
}

function JobProgress({job, onCancel}: {job: JobState; onCancel: () => void}) {
    const parts = [
        job.stage ? i18n.t(`assets_stage_${job.stage}`, {
            defaultValue: job.stage}) : "",
        job.game ?? "",
        job.pack ?? "",
        job.file ?? "",
    ].filter(Boolean);
    const known = job.total > 0;
    return (
        <Stack spacing={1}>
            <Typography variant="body2" sx={WRAP}>
                {parts.join(" / ")}
                {known ? `: ${jobAmount(job, job.done)} / ${jobAmount(job, job.total)}` : ""}
            </Typography>
            <LinearProgress
                aria-label={i18n.t("assets_job_title")}
                variant={known ? "determinate" : "indeterminate"}
                value={known ? Math.min(100, 100 * job.done / job.total) : 0}/>
            <Box>
                <Button size="small" color="warning" onClick={onCancel}>
                    {i18n.t("assets_job_cancel")}
                </Button>
            </Box>
        </Stack>
    );
}

function JobResult({job}: {job: JobState}) {
    if (job.status === "cancelled") {
        return <Alert severity="info">{i18n.t("assets_result_cancelled")}</Alert>;
    }
    if (job.status === "error") {
        return (
            <Stack spacing={0.5}>
                <Alert severity="error" sx={WRAP}>
                    {errorText(job.error)}
                </Alert>
                {job.message ? (
                    <Collapsible label={i18n.t("assets_details")}>
                        <Typography variant="body2" sx={WRAP}>
                            {job.message}
                        </Typography>
                    </Collapsible>
                ) : null}
                <Imported job={job}/>
            </Stack>
        );
    }
    return <Imported job={job} done/>;
}

function Imported({job, done = false}: {job: JobState; done?: boolean}) {
    const entries = job.skippedEntries ?? [];
    return (
        <Stack spacing={0.5}>
            {job.imported.length > 0 ? (
                <Alert severity="success" sx={WRAP}>
                    {i18n.t(job.operation === "install"
                        ? "assets_result_installed" : "assets_result_imported",
                            {games: job.imported.join(", ")})}
                </Alert>
            ) : done && job.failed.length === 0 ? (
                <Alert severity="info">{i18n.t("assets_nothing_imported")}</Alert>
            ) : null}
            {job.skipped.length > 0 ? (
                <Typography variant="body2" sx={WRAP}>
                    {i18n.t("assets_result_skipped",
                            {games: job.skipped.join(", ")})}
                </Typography>
            ) : null}
            {job.failed.map((failure, index) => (
                <Box key={`${failure.name}-${index}`}>
                    <Alert severity="error" sx={WRAP}>
                        {i18n.t("assets_result_failed_item",
                                {game: failure.name})}
                    </Alert>
                    <Collapsible label={i18n.t("assets_details")}>
                        <Typography variant="body2" sx={WRAP}>
                            {failure.message}
                        </Typography>
                    </Collapsible>
                </Box>
            ))}
            {entries.length > 0 || job.skippedEntriesMore > 0 ? (
                <Collapsible label={i18n.t("assets_result_skipped_entries", {
                    count: entries.length + (job.skippedEntriesMore ?? 0)})}>
                    <Box component="ul" sx={{m: 0, pl: 3}}>
                        {entries.map((name, index) => (
                            <Typography component="li" variant="body2"
                                        key={`${name}-${index}`} sx={WRAP}>
                                {name}
                            </Typography>
                        ))}
                    </Box>
                    {job.skippedEntriesMore > 0 ? (
                        <Typography variant="body2">
                            {i18n.t("assets_result_skipped_more",
                                    {count: job.skippedEntriesMore})}
                        </Typography>
                    ) : null}
                </Collapsible>
            ) : null}
        </Stack>
    );
}

// ── The page ────────────────────────────────────────────────────────────

function AssetsContent() {
    const {access, retry} = useShellAccess({withToken: false});
    const {visible, reveals} = useDocumentVisibility();
    const shell = access === "shell";
    const packs = usePacks(shell);
    const assetJob = useAssetJob(shell, visible, reveals);
    const {job, tracked, lost, running, readFailed, begin, read} = assetJob;
    const [startError, setStartError] = React.useState<string | null>(null);
    const [zipNotice, setZipNotice] = React.useState<Notice | null>(null);
    // From the start request until the first read of the job (the buttons
    // wait for it), and while the ZIP card sends / asks.
    const [starting, setStarting] = React.useState(false);
    const [zipBusy, setZipBusy] = React.useState(false);
    const busy = running || starting || zipBusy;

    // The installed games changed (a scan after a job, at start-up): read
    // the list again. The index is the server's cached one.
    const reloadPacks = packs.load;
    React.useEffect(() => {
        if (!shell) {
            return undefined;
        }
        const socket = socketConnection.instance();
        const changed = () => void reloadPacks(false);
        socket.on("games_changed", changed);
        return () => {
            socket.off("games_changed", changed);
        };
    }, [shell, reloadPacks]);

    // The page's job ended: what is installed has changed.
    const ended = tracked !== null && job?.revision === tracked
        && job.status !== "running" && job.status !== "idle";
    React.useEffect(() => {
        if (ended) {
            void reloadPacks(false);
        }
    }, [ended, tracked, reloadPacks]);

    const started = async (path: string, body: unknown): Promise<boolean> => {
        setStartError(null);
        setStarting(true);
        try {
            const text = await apiPost(path, body, WRITE_OPTIONS);
            const revision = JSON.parse(text)?.data?.job;
            if (typeof revision !== "number") {
                throw new Error("the answer has no job");
            }
            await begin(revision);
            return true;
        } catch (error) {
            console.error(error);
            setStartError(describeFailure(error));
            if (apiErrorStatus(error) === 409) {
                // Another job runs: show it.
                void read();
            }
            return false;
        } finally {
            setStarting(false);
        }
    };

    const cancel = async () => {
        if (!job) {
            return;
        }
        try {
            await apiPost(CANCEL_PATH, {revision: job.revision},
                          WRITE_OPTIONS);
        } catch (error) {
            console.error(error);
            showSnackbar(describeFailure(error), "error");
        }
    };

    let body: React.ReactNode;
    if (access === "checking") {
        body = (
            <Stack direction="row" spacing={1} alignItems="center">
                <CircularProgress size={20}/>
                <Typography variant="body2">{i18n.t("assets_checking")}</Typography>
            </Stack>
        );
    } else if (access === "failed") {
        body = (
            <Stack spacing={1} alignItems="flex-start">
                <Alert severity="error">{i18n.t("assets_failed")}</Alert>
                <Button variant="outlined" size="small" onClick={retry}>
                    {i18n.t("assets_retry")}
                </Button>
            </Stack>
        );
    } else if (access === "elsewhere") {
        body = (
            <CardFrame title={i18n.t("assets_elsewhere_title")}>
                <Typography variant="body2">
                    {i18n.t("assets_elsewhere")}
                </Typography>
                <SourceRow withFolder={false}/>
            </CardFrame>
        );
    } else {
        const owned = tracked !== null && job !== null
            && job.revision === tracked;
        body = (
            <>
                <OnlineCard
                    packs={packs.state}
                    busy={busy}
                    onRefresh={() => void packs.load(true)}
                    onStart={(game, names) => void started(INSTALL_PATH, {
                        game: game.code, packs: names, confirm: true})}/>
                <ZipCard
                    busy={running || starting}
                    onBusyChange={setZipBusy}
                    notice={zipNotice}
                    setNotice={setZipNotice}
                    onImport={(upload, overwrite) => started(
                        IMPORT_ZIP_PATH,
                        overwrite.length > 0
                            ? {upload, overwrite, confirm: true}
                            : {upload})}/>
                {startError ? (
                    <Alert severity="error" sx={WRAP}>{startError}</Alert>
                ) : null}
                {readFailed ? (
                    <Alert severity="warning"
                           action={<Button color="inherit" size="small"
                                           onClick={() => void read()}>
                               {i18n.t("assets_retry")}
                           </Button>}>
                        {i18n.t("assets_job_read_failed")}
                    </Alert>
                ) : null}
                {lost ? (
                    <Alert severity="warning">
                        {i18n.t("assets_job_unknown")}
                    </Alert>
                ) : null}
                {job && owned ? (
                    <CardFrame title={i18n.t("assets_job_title")}>
                        {job.status === "running"
                            ? <JobProgress job={job}
                                           onCancel={() => void cancel()}/>
                            : <JobResult job={job}/>}
                    </CardFrame>
                ) : null}
                <SourceRow withFolder/>
            </>
        );
    }

    return (
        <GuideFrame panel="assets" title={i18n.t("assets_title")}
                    description={i18n.t("assets_description")}
                    openInBrowser={false}>
            {body}
        </GuideFrame>
    );
}

export default function AssetsPage() {
    return (
        <FeedbackProvider>
            <AssetsContent/>
        </FeedbackProvider>
    );
}
