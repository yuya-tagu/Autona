import React from "react";
import {
    Alert,
    Box,
    Button,
    Card,
    Chip,
    CircularProgress,
    IconButton,
    Link,
    MenuItem,
    Stack,
    Tab,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Tabs,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import ContentCopyOutlined from "@mui/icons-material/ContentCopyOutlined";
import ScienceOutlined from "@mui/icons-material/ScienceOutlined";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {apiErrorCode, apiErrorStatus} from "../api";
import {withToken} from "../autonaToken";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {usePanelShell} from "../PanelShell/context";
import {getWithRetry} from "../PanelShell/requests";
import {ShellAccessValue, useShellAccess} from "../PanelShell/useShellAccess";
import ConnectionCard, {LanState, guideBaseUrl} from "./ConnectionCard";
import {useConnection} from "./useConnection";
import {CopyButton, GuideFrame, Note, copyAndTell, useNarrow} from "./parts";
import {
    buildCurl,
    carriesToken,
    destructiveMarkers,
    hideToken,
    isDestructive,
    testerPath,
} from "./format";

// The API reference & tester (ADR-005 S-final SF-4a-2), /web/api: the Qt
// API Settings window as a page -- the connection and the access token,
// the catalogue's rows (GET /api/v1/api-catalog) with a curl to copy, the
// envelope and the error codes, and a tester that sends what it is given.
// The token is the shared view's only (GET /api/v1/access-token answers it
// there, 403 anywhere else); a browser is told to open the page in the app.

interface Endpoint {
    id: string;
    method: string;
    path: string;
    // `path` with its placeholders filled in.
    example: string;
    // The English text; the page shows guide_api_endpoint_<id>.
    description: string;
    body: unknown;
    destructive: boolean;
    logged: boolean;
    confirm: boolean;
}

interface ErrorCode {
    code: string;
    status: number;
    description: string;
}

interface Catalog {
    endpoints: Endpoint[];
    errorCodes: ErrorCode[];
}

interface Envelope<T> {
    ok: boolean;
    data: T;
}

export const CATALOG_PATH = "/api/v1/api-catalog";

// The window's: a request the tester sends gives up after this long.
export const TESTER_TIMEOUT_MS = 25000;

export const TESTER_METHODS = ["GET", "POST", "PATCH", "PUT"];

const DEFAULT_TESTER_PATH = "/health";

// A row's description in the page's language; the catalogue's English when
// a row has no translation yet (tests/test_api_v1_guides.py checks there
// is one for every row).
function endpointText(row: Endpoint): string {
    const key = `guide_api_endpoint_${row.id}`;
    return i18n.exists(key) ? i18n.t(key) : row.description;
}

function errorText(row: ErrorCode): string {
    const key = `guide_api_error_${row.code}`;
    return i18n.exists(key) ? i18n.t(key) : row.description;
}

function apiLanNote(state: LanState): string {
    return i18n.t(`guide_api_lan_${state}`);
}

// What a body looks like in the tester ("" for none, as the window).
function testerBody(body: unknown): string {
    if (body === null || body === undefined) {
        return "";
    }
    if (typeof body === "object" && Object.keys(body as object).length === 0) {
        return "";
    }
    return JSON.stringify(body, null, 2);
}

// The access token's row: masked until shown, copied and replaced here.
// The shared view's only: a browser is told where it is.
function TokenRow({shell}: {shell: ShellAccessValue}) {
    const [shown, setShown] = React.useState(false);
    const [confirming, setConfirming] = React.useState(false);

    const regenerate = () => {
        setConfirming(false);
        shell.regenerate().then(
            () => showSnackbar(i18n.t("guide_api_token_regenerated"),
                               "success"),
            (error: unknown) => {
                console.error(error);
                if (apiErrorStatus(error) === 409
                        && apiErrorCode(error) === "NOT_READY") {
                    showSnackbar(i18n.t("guide_api_token_not_ready"), "error");
                } else {
                    showSnackbar(i18n.t("request_failed"), "error");
                }
            });
    };

    if (shell.access === "checking") {
        return (
            <Stack direction="row" spacing={1} alignItems="center">
                <CircularProgress size={18}/>
                <Typography variant="body2">
                    {i18n.t("guide_api_token_checking")}
                </Typography>
            </Stack>
        );
    }
    if (shell.access === "elsewhere") {
        return (
            <Alert severity="info">{i18n.t("guide_api_token_in_app")}</Alert>
        );
    }
    if (shell.access === "failed") {
        return (
            <Alert severity="error" action={
                <Button color="inherit" size="small" onClick={shell.retry}>
                    {i18n.t("guide_retry")}
                </Button>
            }>
                {i18n.t("guide_api_token_failed")}
            </Alert>
        );
    }
    return (
        <Stack spacing={1}>
            <Stack direction="row" spacing={1} alignItems="center"
                   flexWrap="wrap" useFlexGap>
                <TextField
                    size="small"
                    label={i18n.t("guide_api_token")}
                    type={shown ? "text" : "password"}
                    value={shell.token}
                    sx={{flex: 1, minWidth: 240}}
                    slotProps={{htmlInput: {readOnly: true,
                                            autoComplete: "off"}}}
                />
                <Button size="small" variant="outlined" aria-pressed={shown}
                        disabled={!shell.token}
                        onClick={() => setShown((value) => !value)}>
                    {shown ? i18n.t("guide_api_token_hide")
                        : i18n.t("guide_api_token_show")}
                </Button>
                <CopyButton text={shell.token}
                            name={i18n.t("guide_api_copy_token")}/>
                <Button size="small" variant="outlined" color="error"
                        onClick={() => setConfirming(true)}>
                    {i18n.t("guide_api_token_regenerate")}
                </Button>
            </Stack>
            {!shell.available ? (
                <Note>{i18n.t("guide_api_token_unavailable")}</Note>
            ) : null}
            <ConfirmDialog
                open={confirming}
                title={i18n.t("guide_api_regenerate_title")}
                message={i18n.t("guide_api_regenerate_confirm")}
                confirm={i18n.t("guide_api_token_regenerate")}
                onConfirm={regenerate}
                onClose={() => setConfirming(false)}
            />
        </Stack>
    );
}

// Where the port and LAN access are changed: the settings page. The
// shared view shows one page at a time, so the desktop is told the way
// there; a browser gets the page's link.
function SettingsHint() {
    const {desktop, language} = usePanelShell();
    if (desktop) {
        return <Note>{i18n.t("guide_api_settings_in_app")}</Note>;
    }
    return (
        <Typography variant="body2">
            <Link href={`/web/settings?lng=${encodeURIComponent(language)}`}>
                {i18n.t("guide_api_open_settings")}
            </Link>
        </Typography>
    );
}

function Legend({errorCodes}: {errorCodes: ErrorCode[]}) {
    const lines = [
        `${i18n.t("guide_api_success")}: `
            + '{ "ok": true, "data": ..., "revision": 1234 }',
        `${i18n.t("guide_api_error")}: `
            + '{ "ok": false, "error": { "code": ..., "message": ... } }',
        "",
        ...errorCodes.map((row) =>
            `${row.code}  (${row.status})  ${errorText(row)}`),
    ];
    return (
        <Box component="section" aria-label={i18n.t("guide_api_legend")}>
            <Typography variant="subtitle2" component="h3">
                {i18n.t("guide_api_legend")}
            </Typography>
            <Typography component="pre" variant="body2"
                        sx={{fontFamily: "monospace", whiteSpace: "pre-wrap",
                             m: 0}}>
                {lines.join("\n")}
            </Typography>
        </Box>
    );
}

// A row's marks: whether the body needs {"confirm": true}, whether it is
// written to the operation log. Small chips, so the path keeps its width:
// a column of them beside the path, or a row under it (narrow).
function Marks({row, direction}: {row: Endpoint;
                                  direction: "row" | "column"}) {
    if (!row.confirm && !row.logged) {
        return null;
    }
    return (
        <Stack direction={direction} spacing={0.5} alignItems="flex-start">
            {row.confirm ? (
                <Tooltip describeChild title={i18n.t("guide_api_confirm_hint")}>
                    <Chip size="small" variant="outlined" color="warning"
                          label={i18n.t("guide_api_confirm")}/>
                </Tooltip>
            ) : null}
            {row.logged ? (
                <Tooltip describeChild title={i18n.t("guide_api_logged_hint")}>
                    <Chip size="small" variant="outlined"
                          label={i18n.t("guide_api_logged")}/>
                </Tooltip>
            ) : null}
        </Stack>
    );
}

// A row's two icon buttons: copy its curl, load it into the tester.
function RowActions({row, curlOf, load}: {
    row: Endpoint;
    curlOf: ((row: Endpoint) => string) | null;
    load: (row: Endpoint) => void;
}) {
    const name = `${row.method} ${row.path}`;
    return (
        <Box sx={{whiteSpace: "nowrap"}}>
            <Tooltip title={i18n.t("guide_api_copy_curl")}>
                <span>
                    <IconButton
                        size="small"
                        aria-label={`${i18n.t("guide_api_copy_curl")}: ${name}`}
                        disabled={!curlOf}
                        onClick={() => curlOf && void copyAndTell(curlOf(row))}>
                        <ContentCopyOutlined fontSize="small"/>
                    </IconButton>
                </span>
            </Tooltip>
            <Tooltip title={i18n.t("guide_api_load_into_tester")}>
                <IconButton
                    size="small"
                    aria-label={`${i18n.t("guide_api_load_into_tester")}: ${name}`}
                    onClick={() => load(row)}>
                    <ScienceOutlined fontSize="small"/>
                </IconButton>
            </Tooltip>
        </Box>
    );
}

// The path: on one line where it fits (the longest is ~50 characters),
// else broken after a "/" only -- each part kept whole, never at its "-".
const PATH_SX = {display: "block", fontFamily: "monospace",
                 fontSize: "0.8125rem", overflowWrap: "normal",
                 wordBreak: "normal"} as const;

function ApiPath({path}: {path: string}) {
    const parts = path.match(/[^/]*\/|[^/]+$/g) ?? [path];
    return (
        <Box component="code" data-api-path="" sx={PATH_SX}>
            {parts.map((part, index) => (
                <React.Fragment key={index}>
                    {index > 0 ? <wbr/> : null}
                    <span style={{whiteSpace: "nowrap"}}>{part}</span>
                </React.Fragment>
            ))}
        </Box>
    );
}

// Narrow cells: the table must fit the shared view without scrolling.
const CELL_SX = {px: 1} as const;
const TOP_CELL_SX = {...CELL_SX, verticalAlign: "top"} as const;

// Below this width of the table's container, the marks and the buttons go
// under the path and its description (two columns, not four): the app's
// view gives the table ~530 CSS px, four columns need ~650.
export const NARROW_REFERENCE = 720;

function Reference({catalog, curlOf, load}: {
    catalog: Catalog;
    // null while the connection is not known: no curl to copy then.
    curlOf: ((row: Endpoint) => string) | null;
    load: (row: Endpoint) => void;
}) {
    const [container, narrow] = useNarrow<HTMLDivElement>(NARROW_REFERENCE);
    return (
        <Stack spacing={2}>
            <TableContainer ref={container}>
                <Table size="small" aria-label={i18n.t("guide_api_reference")}>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_method")}</TableCell>
                            <TableCell sx={CELL_SX}>
                                {`${i18n.t("guide_api_path")} / `
                                 + i18n.t("guide_api_description")}
                            </TableCell>
                            {narrow ? null : (
                                <>
                                    <TableCell sx={CELL_SX}/>
                                    <TableCell sx={CELL_SX}/>
                                </>
                            )}
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {catalog.endpoints.map((row) => (
                            <TableRow key={row.id} hover>
                                <TableCell sx={{...TOP_CELL_SX,
                                                fontFamily: "monospace"}}>
                                    {row.method}
                                </TableCell>
                                <TableCell sx={{...CELL_SX, width: "100%"}}>
                                    <ApiPath path={row.path}/>
                                    <Typography variant="body2"
                                                color="text.secondary">
                                        {endpointText(row)}
                                    </Typography>
                                    {narrow ? (
                                        <Stack direction="row" spacing={1}
                                               alignItems="center"
                                               sx={{mt: 0.5}}>
                                            <Marks row={row} direction="row"/>
                                            <Box sx={{ml: "auto"}}>
                                                <RowActions row={row}
                                                            curlOf={curlOf}
                                                            load={load}/>
                                            </Box>
                                        </Stack>
                                    ) : null}
                                </TableCell>
                                {narrow ? null : (
                                    <>
                                        <TableCell sx={TOP_CELL_SX}>
                                            <Marks row={row}
                                                   direction="column"/>
                                        </TableCell>
                                        <TableCell sx={TOP_CELL_SX}>
                                            <RowActions row={row}
                                                        curlOf={curlOf}
                                                        load={load}/>
                                        </TableCell>
                                    </>
                                )}
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
            <Legend errorCodes={catalog.errorCodes}/>
            <Typography variant="body2" color="error">
                {i18n.t("guide_api_legacy_note")}
            </Typography>
        </Stack>
    );
}

interface TesterForm {
    method: string;
    path: string;
    idempotencyKey: string;
    body: string;
}

function Tester({catalog, form, setForm, token, onTokenChange}: {
    catalog: Catalog;
    form: TesterForm;
    setForm: React.Dispatch<React.SetStateAction<TesterForm>>;
    // The token the page knows, hidden in what the tester shows.
    token: string;
    // A regeneration sent from the tester: the token row reads it again.
    onTokenChange: () => void;
}) {
    const [preset, setPreset] = React.useState("");
    const [sending, setSending] = React.useState(false);
    const [response, setResponse] = React.useState("");
    const [asking, setAsking] = React.useState<{method: string;
                                                path: string} | null>(null);
    const live = React.useRef(true);
    const markers = React.useMemo(
        () => destructiveMarkers(catalog.endpoints), [catalog]);

    React.useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
        };
    }, []);

    const choosePreset = (value: string) => {
        setPreset(value);
        const row = catalog.endpoints[Number(value)];
        if (value !== "" && row) {
            setForm((current) => ({
                ...current,
                method: TESTER_METHODS.includes(row.method) ? row.method : "GET",
                path: row.example,
                body: testerBody(row.body),
            }));
        }
    };

    const perform = async (method: string, path: string) => {
        let body: unknown;
        const text = form.body.trim();
        if (text) {
            try {
                body = JSON.parse(text);
            } catch (error) {
                setResponse(`${i18n.t("guide_api_invalid_json")}: `
                            + `${(error as Error).message}`);
                return;
            }
        }
        const headers: Record<string, string> = {};
        if (form.idempotencyKey.trim()) {
            headers["Idempotency-Key"] = form.idempotencyKey.trim();
        }
        // A GET carries no body (fetch refuses one; no GET reads one).
        const sent = method === "GET" ? undefined : body;
        if (sent !== undefined) {
            headers["Content-Type"] = "application/json";
        }
        setSending(true);
        setResponse(i18n.t("guide_api_sending"));
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), TESTER_TIMEOUT_MS);
        let shown: string;
        try {
            // The page's own token when it was opened with one (another
            // computer); the shared view's fetch takes the shell's secret
            // on the way out.
            const answer = await fetch(`${BASE_URL}/api/v1${path}`, {
                method,
                headers: withToken(headers),
                body: sent === undefined ? undefined : JSON.stringify(sent),
                signal: controller.signal,
                cache: "no-store",
            });
            const raw = await answer.text();
            if (answer.ok && method !== "GET" && carriesToken(raw)
                    && live.current) {
                onTokenChange();
            }
            const pretty = hideToken(raw, token,
                                     i18n.t("guide_api_token_hidden"));
            shown = `HTTP ${answer.status} ${answer.statusText}\n\n${pretty}`;
        } catch (error) {
            const why = controller.signal.aborted
                ? i18n.t("guide_api_timed_out",
                         {seconds: TESTER_TIMEOUT_MS / 1000})
                : String(error);
            shown = `${i18n.t("guide_api_request_failed")}:\n${why}`;
        } finally {
            clearTimeout(timer);
        }
        if (live.current) {
            setResponse(shown);
            setSending(false);
        }
    };

    const send = () => {
        const path = testerPath(form.path);
        if (isDestructive(form.method, path, markers)) {
            setAsking({method: form.method, path});
            return;
        }
        void perform(form.method, path);
    };

    return (
        <Stack spacing={2}>
            <TextField
                select
                size="small"
                label={i18n.t("guide_api_preset")}
                value={preset}
                onChange={(event) => choosePreset(event.target.value)}
            >
                <MenuItem value="">—</MenuItem>
                {catalog.endpoints.map((row, index) => (
                    <MenuItem key={row.id} value={String(index)}>
                        {`${row.method}  ${row.path}`}
                    </MenuItem>
                ))}
            </TextField>
            <Stack direction="row" spacing={1}>
                <TextField
                    select
                    size="small"
                    label={i18n.t("guide_method")}
                    value={form.method}
                    sx={{width: 120}}
                    onChange={(event) => setForm((current) => (
                        {...current, method: event.target.value}))}
                >
                    {TESTER_METHODS.map((method) => (
                        <MenuItem key={method} value={method}>{method}</MenuItem>
                    ))}
                </TextField>
                <TextField
                    size="small"
                    label={i18n.t("guide_api_path")}
                    value={form.path}
                    sx={{flex: 1}}
                    onChange={(event) => setForm((current) => (
                        {...current, path: event.target.value}))}
                />
            </Stack>
            <TextField
                size="small"
                label="Idempotency-Key"
                placeholder={i18n.t("guide_api_optional")}
                value={form.idempotencyKey}
                onChange={(event) => setForm((current) => (
                    {...current, idempotencyKey: event.target.value}))}
            />
            <TextField
                multiline
                minRows={3}
                maxRows={10}
                label={i18n.t("guide_body_json")}
                placeholder='{ "delta": 1 }'
                value={form.body}
                slotProps={{htmlInput: {style: {fontFamily: "monospace"}}}}
                onChange={(event) => setForm((current) => (
                    {...current, body: event.target.value}))}
            />
            <Box>
                <Button variant="contained" disabled={sending} onClick={send}>
                    {i18n.t("guide_api_send")}
                </Button>
            </Box>
            <Box>
                <Typography variant="subtitle2" component="h3">
                    {i18n.t("guide_api_response")}
                </Typography>
                <Typography
                    component="pre"
                    variant="body2"
                    aria-label={i18n.t("guide_api_response")}
                    aria-live="polite"
                    sx={{fontFamily: "monospace", whiteSpace: "pre-wrap",
                         wordBreak: "break-all", m: 0, p: 1, minHeight: 120,
                         border: 1, borderColor: "divider", borderRadius: 1}}>
                    {response}
                </Typography>
            </Box>
            <ConfirmDialog
                open={asking !== null}
                title={i18n.t("guide_api_confirm")}
                message={asking ? `${i18n.t("guide_api_destructive_confirm")} `
                    + `(${asking.method} ${asking.path})` : ""}
                confirm={i18n.t("guide_api_send")}
                onConfirm={() => {
                    const request = asking;
                    setAsking(null);
                    if (request) {
                        void perform(request.method, request.path);
                    }
                }}
                onClose={() => setAsking(null)}
            />
        </Stack>
    );
}

function ApiContent() {
    const {connection, status, refresh} = useConnection();
    const shell = useShellAccess();
    const [catalog, setCatalog] = React.useState<Catalog | null>(null);
    const [catalogFailed, setCatalogFailed] = React.useState(false);
    const [catalogReads, setCatalogReads] = React.useState(0);
    const [tab, setTab] = React.useState(0);
    const [form, setForm] = React.useState<TesterForm>({
        method: "GET", path: DEFAULT_TESTER_PATH, idempotencyKey: "",
        body: ""});

    React.useEffect(() => {
        let live = true;
        setCatalogFailed(false);
        getWithRetry<Envelope<Catalog>>(CATALOG_PATH, () => live)
            .then((body) => {
                if (live) {
                    setCatalog(body.data);
                }
            }, (error: unknown) => {
                console.error(error);
                if (live) {
                    setCatalogFailed(true);
                }
            });
        return () => {
            live = false;
        };
    }, [catalogReads]);

    const baseUrl = guideBaseUrl(connection, status, "baseUrl");
    // With LAN access a curl is for another device, which needs the token
    // to write; a page from another computer (refused the connection) is
    // that device. No curl while the connection is not known (loading,
    // failed): neither its URL nor whether it needs the token is.
    const lan = connection ? connection.lan : status === "thisPcOnly";
    const curlOf = baseUrl === null ? null : (row: Endpoint) => buildCurl(
        {method: row.method, path: row.example, body: row.body},
        baseUrl, lan, shell.token);
    const load = (row: Endpoint) => {
        setForm((current) => ({
            ...current,
            method: TESTER_METHODS.includes(row.method) ? row.method : "GET",
            path: row.example,
            body: testerBody(row.body),
        }));
        setTab(1);
    };

    return (
        <GuideFrame panel="api" title={i18n.t("guide_api_title")}>
            <ConnectionCard connection={connection} status={status}
                            refresh={refresh} url={baseUrl}
                            lanNote={apiLanNote} lanWarning>
                <TokenRow shell={shell}/>
                <SettingsHint/>
            </ConnectionCard>
            <Card variant="outlined">
                <Tabs value={tab} onChange={(_event, value) => setTab(value)}>
                    <Tab label={i18n.t("guide_api_reference")}/>
                    <Tab label={i18n.t("guide_api_tester")}/>
                </Tabs>
                <Box sx={{p: 2}}>
                    {catalogFailed ? (
                        <Alert severity="error" action={
                            <Button color="inherit" size="small" onClick={
                                () => setCatalogReads((count) => count + 1)}>
                                {i18n.t("guide_retry")}
                            </Button>
                        }>
                            {i18n.t("guide_api_catalog_failed")}
                        </Alert>
                    ) : !catalog ? (
                        <Stack direction="row" spacing={1} alignItems="center">
                            <CircularProgress size={18}/>
                            <Typography variant="body2">
                                {i18n.t("guide_api_catalog_loading")}
                            </Typography>
                        </Stack>
                    ) : (
                        // Both kept, the other hidden: the tester's answer
                        // (or the request it waits on) survives a look at
                        // the reference, as the window's tabs.
                        <>
                            <Box hidden={tab !== 0}>
                                <Reference catalog={catalog} curlOf={curlOf}
                                           load={load}/>
                            </Box>
                            <Box hidden={tab !== 1}>
                                <Tester catalog={catalog} form={form}
                                        setForm={setForm}
                                        token={shell.token}
                                        onTokenChange={shell.retry}/>
                            </Box>
                        </>
                    )}
                </Box>
            </Card>
        </GuideFrame>
    );
}

export default function ApiPage() {
    return (
        <FeedbackProvider>
            <ApiContent/>
        </FeedbackProvider>
    );
}
