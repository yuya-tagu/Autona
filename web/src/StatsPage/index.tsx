import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Stack,
    Tab,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    TableSortLabel,
    Tabs,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiErrorCode, apiErrorStatus, apiPost} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {
    AnalysisRow,
    COLUMNS,
    Cell,
    DEFAULT_SORT,
    NUMERIC_COLUMNS,
    Progress,
    Sort,
    filterRows,
    progressLine,
    rowCells,
    sortRows,
} from "./analysisRows";
import {
    Names,
    StatsBoard,
    h2hRecord,
    historyCells,
    historySets,
    lastSetCells,
    lastSets,
    names as boardNames,
    recentSetCells,
    recentSets,
    upsetFactor,
} from "./format";

// The Statistics panel as a React page (ADR-005 S4k), /web/stats: board 1's
// statistics -- the upset factor, the head-to-head, both players' last sets
// and placements -- and the tournament's match list. Shown in the desktop
// app's shared web view and in a browser alike. The statistics are core's
// (StatsService): the page asks for them (POST
// /api/v1/scoreboards/1/stats:refresh) and renders score.1.* as published,
// over Socket.IO -- the players' names too, as OBS shows them. The match
// list is core's too (AnalysisService): the page starts a fetch (POST
// /api/v1/analysis:refresh), follows its progress in the state (analysis.*)
// and reads the rows (GET /api/v1/analysis) when the page comes up and when
// the fetch ends or the tournament changes; they are too many for the state.

const TABS = [
    "stats_tab_recent",
    "stats_tab_last",
    "stats_tab_history",
    "stats_tab_analysis",
];

// The tab shown: kept while the page is away (the shared view shows another
// panel) and back.
let selectedTab = 0;

// The match list's table while no rows are shown (one array: the table's
// memo keeps it).
const NO_ROWS: Cell[][] = [];

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface AnalysisResult {
    status?: string;
    url?: string;
    rows?: AnalysisRow[];
}

interface StatsState {
    score?: Record<string, StatsBoard | undefined>;
    analysis?: Progress | null;
}

// What a refused write says; null: nothing (the caller tells it).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;
type Write = <T>(request: () => Promise<T>, refusal?: Refusal) => Promise<T>;

function translate(key: string, values?: Record<string, unknown>): string {
    return i18n.t(key, values);
}

// A table of the statistics: the cells' second lines (an event's name, an
// opponent's sponsor) below their first.
function StatsTable({label, columns, rows}: {
    label: string;
    columns: string[];
    rows: string[][];
}) {
    return (
        <TableContainer>
            <Table size="small" aria-label={label}>
                <TableHead>
                    <TableRow>
                        {columns.map((column) => (
                            <TableCell key={column}>{column}</TableCell>
                        ))}
                    </TableRow>
                </TableHead>
                <TableBody>
                    {rows.length === 0 && (
                        <TableRow>
                            <TableCell colSpan={columns.length}>
                                <Typography variant="body2"
                                            color="text.secondary">
                                    {i18n.t("stats_empty")}
                                </Typography>
                            </TableCell>
                        </TableRow>
                    )}
                    {rows.map((cells, index) => (
                        <TableRow key={index}>
                            {cells.map((cell, column) => (
                                <TableCell key={column}
                                           sx={{whiteSpace: "pre-line"}}>
                                    {cell}
                                </TableCell>
                            ))}
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </TableContainer>
    );
}

function PlayerSection({name, children}: {
    name: string;
    children: React.ReactNode;
}) {
    return (
        <Box component="section" aria-label={name}>
            <Typography variant="subtitle1" component="h2"
                        sx={{fontWeight: "bold"}}>
                {name}
            </Typography>
            {children}
        </Box>
    );
}

function RecentSetsTab({board, players}: {
    board: StatsBoard | undefined;
    players: Names;
}) {
    const sets = recentSets(board);
    const record = h2hRecord(sets);
    return (
        <Stack spacing={1}>
            <Typography sx={{fontWeight: "bold", whiteSpace: "pre-wrap"}}>
                {i18n.t("stats_h2h_record", {
                    p1: players.p1Record, w1: record.p1Wins, r1: record.p1Rate,
                    p2: players.p2Record, w2: record.p2Wins, r2: record.p2Rate,
                    total: record.total,
                })}
            </Typography>
            <StatsTable
                label={i18n.t("stats_tab_recent")}
                columns={[
                    i18n.t("stats_col_date"),
                    i18n.t("stats_col_event"),
                    i18n.t("stats_col_round"),
                    i18n.t("stats_col_score"),
                    i18n.t("stats_col_winner"),
                ]}
                rows={sets.map((set) => recentSetCells(set, players))}
            />
        </Stack>
    );
}

function LastSetsTab({board, players}: {
    board: StatsBoard | undefined;
    players: Names;
}) {
    const columns = [
        i18n.t("stats_col_phase"),
        i18n.t("stats_col_round"),
        i18n.t("stats_col_opponent"),
        i18n.t("stats_col_score"),
    ];
    return (
        <Stack spacing={2}>
            {(["1", "2"] as const).map((team) => {
                const name = team === "1" ? players.p1 : players.p2;
                return (
                    <PlayerSection key={team} name={name}>
                        <StatsTable
                            label={`${i18n.t("stats_tab_last")} ${name}`}
                            columns={columns}
                            rows={lastSets(board, team).map(lastSetCells)}
                        />
                    </PlayerSection>
                );
            })}
        </Stack>
    );
}

function HistoryTab({board, players}: {
    board: StatsBoard | undefined;
    players: Names;
}) {
    const columns = [
        i18n.t("stats_col_tournament"),
        i18n.t("stats_col_event"),
        i18n.t("stats_col_placement"),
        i18n.t("stats_col_entrants"),
        i18n.t("stats_col_date"),
    ];
    return (
        <Stack spacing={2}>
            {(["1", "2"] as const).map((team) => {
                const name = team === "1" ? players.p1 : players.p2;
                return (
                    <PlayerSection key={team} name={name}>
                        <StatsTable
                            label={`${i18n.t("stats_tab_history")} ${name}`}
                            columns={columns}
                            rows={historySets(board, team).map(historyCells)}
                        />
                    </PlayerSection>
                );
            })}
        </Stack>
    );
}

// ── the match list ───────────────────────────────────────────────────────

function AnalysisTab({write}: {write: Write}) {
    const progress = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as unknown as StatsState).analysis);
    const status = typeof progress?.status === "string"
        ? progress.status : "idle";
    // Which fetch and how far (not every group's answer). None until the
    // state has arrived.
    const revision = Number(progress?.revision);
    const key = progress ? `${String(progress.revision)}:${status}` : null;
    const keyRef = React.useRef(key);
    keyRef.current = key;
    // The key the rows are read for: the one at mount, then each change to
    // done, error or idle. Not a fetch starting (running): core's rows stay
    // the last fetch's until it is over, so the ones read before it are
    // still core's.
    const [readKey, setReadKey] = React.useState(key);
    if (key !== null && key !== readKey
            && (readKey === null || status !== "running")) {
        setReadKey(key);
    }
    // The rows last read, for which key and at which revision.
    const [read, setRead] = React.useState<{key: string; revision: number;
                                            rows: Cell[][]} | null>(null);
    // The key whose read failed (told, with a way to read again).
    const [readFailed, setReadFailed] = React.useState<string | null>(null);
    const [rereads, setRereads] = React.useState(0);
    // What the status line says instead, until `analysis` changes: what is
    // not in the state (no tournament loaded, a fetch that did not start).
    const [notice, setNotice] = React.useState<{key: string | null;
                                                text: string} | null>(null);
    const [asking, setAsking] = React.useState(false);
    const [filter, setFilter] = React.useState("");
    const [sort, setSort] = React.useState<Sort>(DEFAULT_SORT);
    const mounted = React.useRef(true);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        if (readKey === null) {
            return;
        }
        // The newest read wins: an older one answering late is dropped.
        let live = true;
        const readRevision = Number(readKey.slice(0, readKey.indexOf(":")));
        getWithRetry<Envelope<AnalysisResult>>("/api/v1/analysis", () => live)
            .then((body) => {
                if (live) {
                    const rows = Array.isArray(body.data?.rows)
                        ? body.data.rows : [];
                    setRead({key: readKey, revision: readRevision,
                             rows: rows.map(rowCells)});
                    setReadFailed(null);
                }
            }, (error) => {
                console.error(error);
                if (live) {
                    setReadFailed(readKey);
                }
            });
        return () => {
            live = false;
        };
    }, [readKey, rereads]);

    const fetchRows = React.useCallback(() => {
        setAsking(true);
        setNotice(null);
        // Accepted (202): `analysis` says how it goes. 409: none is loaded
        // (NOT_READY), or one is running already (CONFLICT).
        write(() => apiPost("/api/v1/analysis:refresh", {}, WRITE_OPTIONS),
              () => null)
            .catch((error: unknown) => {
                const status = apiErrorStatus(error);
                if (status === 409 && apiErrorCode(error) === "NOT_READY") {
                    setNotice({key: keyRef.current,
                               text: i18n.t("stats_analysis_no_tournament")});
                } else if (status === 409) {
                    showSnackbar(i18n.t("stats_analysis_running"), "info");
                } else if (status !== 401 && status !== 403) {
                    setNotice({key: keyRef.current,
                               text: i18n.t("stats_analysis_failed")});
                }
            })
            .finally(() => {
                if (mounted.current) {
                    setAsking(false);
                }
            });
    }, [write]);

    // The rows read, while they are core's for this tournament: those read
    // for this key, or -- while a fetch runs -- those read at the change
    // just before it (the previous revision: core still has them). None
    // while idle (the tournament changed, or none was fetched), while the
    // read for the key has not answered, or when it failed: never another
    // fetch's or tournament's.
    const readNow = read !== null && status !== "idle"
        && read.key === readKey && readFailed !== readKey
        && (read.key === key
            || (status === "running" && read.revision === revision - 1));
    const rows = readNow ? read.rows : NO_ROWS;
    // Typing in the filter stays quick with a thousand rows: the table
    // follows the text when React has the time (it is not virtualised).
    const deferredFilter = React.useDeferredValue(filter);
    const shown = React.useMemo(
        () => sortRows(filterRows(rows, deferredFilter), sort),
        [rows, deferredFilter, sort]);

    // A done fetch is counted by the rows read for it.
    const line = notice && notice.key === key ? notice.text
        : progressLine(progress, readNow && read.key === key
            ? read.rows.length : null, translate);

    const sortBy = (column: number) => setSort((current) => (
        current.column === column
            ? {column, descending: !current.descending}
            : {column, descending: false}));

    return (
        <Stack spacing={1} sx={{height: "100%", minHeight: 0}}>
            <Stack direction={{xs: "column", sm: "row"}} spacing={1}
                   alignItems={{sm: "center"}}>
                <Button
                    variant="contained"
                    disabled={asking || status === "running"}
                    onClick={fetchRows}
                >
                    {i18n.t("stats_analysis_fetch")}
                </Button>
                <Typography role="status" color="text.secondary"
                            sx={{flex: 1}}>
                    {line}
                </Typography>
                <TextField
                    size="small"
                    type="search"
                    label={i18n.t("stats_analysis_filter")}
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                    sx={{width: {sm: 260}}}
                />
            </Stack>
            {readFailed !== null && readFailed === readKey && (
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small"
                                onClick={() => setRereads((n) => n + 1)}>
                            {i18n.t("stats_reload")}
                        </Button>
                    }
                >
                    {i18n.t("stats_analysis_read_failed")}
                </Alert>
            )}
            <TableContainer sx={{flex: 1, minHeight: 0, overflowY: "auto"}}>
                <Table size="small" stickyHeader
                       aria-label={i18n.t("stats_tab_analysis")}>
                    <TableHead>
                        <TableRow>
                            {COLUMNS.map((name, column) => {
                                const active = sort.column === column;
                                const direction = active && sort.descending
                                    ? "desc" : "asc";
                                return (
                                    <TableCell
                                        key={name}
                                        align={NUMERIC_COLUMNS.has(column)
                                            ? "center" : "left"}
                                        sortDirection={active ? direction
                                            : false}
                                    >
                                        <TableSortLabel
                                            active={active}
                                            direction={direction}
                                            onClick={() => sortBy(column)}
                                        >
                                            {name}
                                        </TableSortLabel>
                                    </TableCell>
                                );
                            })}
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {shown.map((cells, index) => (
                            <TableRow key={index} hover>
                                {cells.map(([text], column) => (
                                    <TableCell
                                        key={column}
                                        align={NUMERIC_COLUMNS.has(column)
                                            ? "center" : "left"}
                                    >
                                        {text}
                                    </TableCell>
                                ))}
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
        </Stack>
    );
}

// ── the page ─────────────────────────────────────────────────────────────

function StatsContent() {
    const {language} = usePanelShell();
    const board = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as unknown as StatsState).score?.["1"]);
    const [tab, setTab] = React.useState(selectedTab);
    const [refreshing, setRefreshing] = React.useState(false);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();
    const mounted = React.useRef(true);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("stats_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    // A failed request told: the token once, else what `refusal` makes of
    // its status and code (or the generic failure).
    const report = React.useCallback((error: unknown, refusal?: Refusal) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                showSnackbar(i18n.t("stream_token_required"), "error");
            }
        } else {
            const message = refusal ? refusal(status, apiErrorCode(error))
                : i18n.t("request_failed");
            if (message) {
                showSnackbar(message, "error");
            }
        }
    }, []);

    const write: Write = React.useCallback(
        <T,>(request: () => Promise<T>, refusal?: Refusal) =>
            serial(request).catch((error: unknown) => {
                report(error, refusal);
                throw error;
            }), [serial, report]);

    // Starts core's fetches (StatsService.RefreshAll) and answers at once:
    // the statistics land in the state as start.gg answers.
    const refresh = React.useCallback(() => {
        setRefreshing(true);
        write(() => apiPost("/api/v1/scoreboards/1/stats:refresh", {},
                            WRITE_OPTIONS))
            .then(() => {
                showSnackbar(i18n.t("stats_refresh_started"), "success");
            }, () => undefined)
            .finally(() => {
                if (mounted.current) {
                    setRefreshing(false);
                }
            });
    }, [write]);

    const choose = (_event: React.SyntheticEvent, value: number) => {
        selectedTab = value;
        setTab(value);
    };

    const players = boardNames(board);
    // The upset factor only with both players named; else the pairing.
    const matchup = `${players.p1} vs ${players.p2}`;
    const top = players.both
        ? `${i18n.t("stats_upset_factor")}: ${upsetFactor(board)}    |    ${matchup}`
        : matchup;

    return (
        <Box
            component="main"
            sx={{
                display: "flex",
                flexDirection: "column",
                height: {md: "100vh"},
                p: 2,
                gap: 1,
                bgcolor: "background.default",
            }}
        >
            <Stack direction={{xs: "column", sm: "row"}} spacing={2}
                   alignItems={{sm: "center"}}>
                <Button variant="contained" disabled={refreshing}
                        onClick={refresh}>
                    {i18n.t("stats_refresh")}
                </Button>
                <Typography sx={{fontWeight: "bold", whiteSpace: "pre-wrap"}}>
                    {top}
                </Typography>
            </Stack>
            <Tabs
                value={tab}
                onChange={choose}
                variant="scrollable"
                aria-label={i18n.t("stats_title")}
            >
                {TABS.map((label, index) => (
                    <Tab key={label} label={i18n.t(label)}
                         id={`stats-tab-${index}`}
                         aria-controls={`stats-tabpanel-${index}`}/>
                ))}
            </Tabs>
            <Box
                role="tabpanel"
                id={`stats-tabpanel-${tab}`}
                aria-labelledby={`stats-tab-${tab}`}
                sx={{flex: 1, minHeight: 0, overflowY: tab === 3 ? "hidden"
                    : "auto"}}
            >
                {tab === 0 && <RecentSetsTab board={board} players={players}/>}
                {tab === 1 && <LastSetsTab board={board} players={players}/>}
                {tab === 2 && <HistoryTab board={board} players={players}/>}
                {tab === 3 && <AnalysisTab write={write}/>}
            </Box>
        </Box>
    );
}

export default function StatsPage() {
    return (
        <FeedbackProvider>
            <StatsContent/>
        </FeedbackProvider>
    );
}
