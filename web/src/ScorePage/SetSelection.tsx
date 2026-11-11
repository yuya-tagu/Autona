import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Checkbox,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    FormControlLabel,
    IconButton,
    LinearProgress,
    MenuItem,
    Stack,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    TableSortLabel,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import CancelIcon from "@mui/icons-material/Cancel";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiErrorCode, apiErrorStatus, apiPost} from "../api";
import {WRITE_OPTIONS, getWithRetry} from "../PanelShell/requests";
import {fields, text} from "./Cards";
import {
    ALL_PHASES,
    NO_PHASE,
    PhaseRow,
    Progress,
    SetRow,
    Shown,
    Sort,
    StationRow,
    chosenRow,
    defaultPhase,
    filterShown,
    isCompletePair,
    nextSort,
    phaseQuery,
    progressOf,
    setShown,
    sortShown,
    stationShown,
    statusLine,
} from "./setRows";

// The SCORE page's set selection (ADR-005 S4ab): what the Qt set selection
// window (AutonaSelectSetWindow), its station window
// (AutonaSelectStationWindow) and the board's timer row did until ADR-005
// S4ac-3 removed them, through S4v's v1. The lists are core's (SetSelectionService): read with GET
// /tournament/{phases,sets,stations} -- reading never fetches, as opening a
// window never did -- when a dialog opens and whenever their progress in the
// state (set_selection.*) changes, and fetched only when asked (the windows'
// rule). What is chosen is loaded / followed by core (SetFollowService) with
// POST /scoreboards/{n}:load-set / :follow. Every write is the page's
// queue's (`write`).

// What a refused write says; null: nothing (the caller tells it).
export type Refusal = (status: number | undefined,
                       code: string | undefined) => string | null;
// The page's write queue: `counted` false for what changes no board.
export type Write = <T>(request: () => Promise<T>, refusal?: Refusal,
                        counted?: boolean) => Promise<T>;

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// GET /tournament/{phases,sets,stations}.
interface ListAnswer<T> {
    status?: unknown;
    url?: unknown;
    fetchedAt?: unknown;
    query?: {phase?: unknown; finished?: unknown} | null;
    rows?: T[];
}

interface SelectionState {
    phases?: Progress | null;
    sets?: Progress | null;
    stations?: Progress | null;
}

function useProgress(kind: keyof SelectionState): Progress | null {
    return useSelector((state: ReduxState) => {
        const root = state.autonaState.autonaState as unknown as
            {set_selection?: SelectionState | null};
        return root.set_selection?.[kind] ?? null;
    });
}

function progressKey(progress: Progress | null): string | null {
    return progress ? `${String(progress.revision)}:${String(progress.status)}`
        : null;
}

function rowsOf<T>(list: ListAnswer<T> | null): T[] {
    return Array.isArray(list?.rows) ? list!.rows : [];
}

const noop = () => undefined;

// A list read while `open`: each time it becomes true, when the list's
// progress changes -- not to "running": the rows stay the last fetch's
// until it ends -- and when asked again. The newest read wins. `failed`: the
// newest read failed (the last rows read stay). `current`: the rows read are
// those of the progress now (read after it settled where it is), not of an
// earlier one.
function useListRead<T>(path: string, open: boolean,
                        progress: Progress | null) {
    const running = progress?.status === "running";
    const key = progressKey(progress);
    const settled = React.useRef(key);
    if (!running) {
        settled.current = key;
    }
    const settledKey = settled.current;
    const [rereads, setRereads] = React.useState(0);
    const [read, setRead] = React.useState<{list: ListAnswer<T> | null;
                                            failed: boolean;
                                            key: string | null}>(
        {list: null, failed: false, key: null});

    React.useEffect(() => {
        if (!open) {
            return undefined;
        }
        let live = true;
        getWithRetry<Envelope<ListAnswer<T>>>(path, () => live).then((body) => {
            if (live) {
                setRead({list: fields(body.data) as ListAnswer<T>,
                         failed: false, key: settledKey});
            }
        }, (error) => {
            console.error(error);
            if (live) {
                setRead((last) => ({...last, failed: true}));
            }
        });
        return () => {
            live = false;
        };
    }, [path, open, settledKey, rereads]);

    const reread = React.useCallback(() => setRereads((n) => n + 1), []);
    const clear = React.useCallback(
        () => setRead({list: null, failed: false, key: null}), []);
    return {list: read.list, failed: read.failed,
            current: read.list !== null && !running && read.key === key,
            reread, clear};
}

// A table of a window: its headers sort (`sort`, null: as read), a click
// selects a row, a double click takes it, and with the table focused the
// arrows move the selection and Enter takes the row chosenRow says (the
// windows' event filter).
function ListTable<T>({label, columns, shown, sort, onSort, selected,
                       onSelect, onTake}: {
    label: string;
    columns: string[];
    shown: Shown<T>[];
    sort: Sort | null;
    onSort: (sort: Sort) => void;
    selected: number | null;
    onSelect: (index: number) => void;
    onTake: (item: Shown<T> | null) => void;
}) {
    const rowRefs = React.useRef(new Map<number, HTMLTableRowElement>());

    const move = (step: number) => {
        if (shown.length === 0) {
            return;
        }
        const at = shown.findIndex((item) => item.index === selected);
        const next = at < 0 ? 0
            : Math.min(shown.length - 1, Math.max(0, at + step));
        onSelect(shown[next].index);
        rowRefs.current.get(shown[next].index)?.scrollIntoView?.(
            {block: "nearest"});
    };

    return (
        <TableContainer sx={{maxHeight: "50vh", overflowY: "auto"}}>
            <Table
                size="small"
                stickyHeader
                aria-label={label}
                tabIndex={0}
                onKeyDown={(event) => {
                    // The table's own keys (the window's event filter is the
                    // view's): not a header's sort button, whose Enter sorts.
                    if (event.target !== event.currentTarget) {
                        return;
                    }
                    if (event.key === "Enter") {
                        event.preventDefault();
                        onTake(chosenRow(shown, selected));
                    } else if (event.key === "ArrowDown") {
                        event.preventDefault();
                        move(1);
                    } else if (event.key === "ArrowUp") {
                        event.preventDefault();
                        move(-1);
                    }
                }}
            >
                <TableHead>
                    <TableRow>
                        {columns.map((name, column) => {
                            const active = sort?.column === column;
                            const direction = active && sort?.descending
                                ? "desc" : "asc";
                            return (
                                // On one line: with no rows to widen the
                                // columns, a Japanese header broke at every
                                // character (seen on the desktop, S4ab-2).
                                // The container scrolls sideways instead.
                                <TableCell key={column}
                                           sx={{whiteSpace: "nowrap"}}
                                           sortDirection={active ? direction
                                               : false}>
                                    {/* Named outright: the browser's tree
                                        left the first column's button
                                        nameless (seen on the desktop). */}
                                    <TableSortLabel
                                        aria-label={name}
                                        active={active}
                                        direction={direction}
                                        onClick={() => onSort(nextSort(sort,
                                                                       column))}
                                    >
                                        {name}
                                    </TableSortLabel>
                                </TableCell>
                            );
                        })}
                    </TableRow>
                </TableHead>
                <TableBody>
                    {shown.map((item) => (
                        <TableRow
                            key={item.index}
                            ref={(element: HTMLTableRowElement | null) => {
                                if (element) {
                                    rowRefs.current.set(item.index, element);
                                } else {
                                    rowRefs.current.delete(item.index);
                                }
                            }}
                            hover
                            selected={item.index === selected}
                            aria-selected={item.index === selected}
                            sx={{cursor: "pointer"}}
                            onClick={() => onSelect(item.index)}
                            onDoubleClick={() => onTake(item)}
                        >
                            {item.cells.map((cell, column) => (
                                <TableCell key={column}>{cell}</TableCell>
                            ))}
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </TableContainer>
    );
}

function FetchProgress({progress}: {progress: Progress | null}) {
    const bar = progressOf(progress);
    if (bar === null) {
        return null;
    }
    return bar.value === null
        ? <LinearProgress aria-label={i18n.t("score_sets_fetching")}/>
        : <LinearProgress variant="determinate" value={bar.value}
                          aria-label={i18n.t("score_sets_fetching")}/>;
}

// ── the set selection dialog ─────────────────────────────────────────────

const SET_COLUMNS = [
    "score_sets_col_stream",
    "score_sets_col_station",
    "score_sets_col_phase",
    "score_sets_col_pool",
    "score_sets_col_match",
];

// The window's (board 1/2's "大会の試合を選ぶ"): the phase combo, the fetch
// button, "Show completed sets" (asked of start.gg: a change fetches again)
// and "Show complete pairs" (the page's filter), the table and its search,
// the progress and the status. OK / Enter / a double click loads the row
// (chosenRow) and stops the fetch running; the station button opens the
// station dialog in its place. Mounted as long as the page is: what the
// operator set in it (the search, the boxes, the table's order -- the
// window's _sortState) stays from one opening to the next, as the window's
// did (it was hidden, not destroyed); the search and "Show complete pairs"
// each board's, as each board had its window.
export function SetDialog({open, board, write, onClose, onTrack}: {
    open: boolean;
    board: number;
    write: Write;
    onClose: () => void;
    onTrack: () => void;
}) {
    const phasesProgress = useProgress("phases");
    const setsProgress = useProgress("sets");
    const setsRunning = setsProgress?.status === "running";
    const phases = useListRead<PhaseRow>("/api/v1/tournament/phases", open,
                                         phasesProgress);
    const sets = useListRead<SetRow>("/api/v1/tournament/sets", open,
                                     setsProgress);
    // Each board's search and "Show complete pairs" (the window's, one per
    // board); the order is every board's (the window's class-wide
    // _sortState), as is "Show completed sets" (brought back from the rows'
    // query at each opening, as the window does from its shared cache).
    const [views, setViews] = React.useState<Record<number, {
        filter: string; completePairs: boolean}>>({});
    const filter = views[board]?.filter ?? "";
    const completePairs = views[board]?.completePairs ?? false;
    const setView = (change: {filter?: string; completePairs?: boolean}) =>
        setViews((known) => ({...known,
                              [board]: {filter, completePairs, ...change}}));
    const [finished, setFinished] = React.useState(false);
    // The phase the operator picked, for the phases of that revision (a
    // fetch of them, or a tournament change, starts over from the default).
    const [choice, setChoice] = React.useState<{key: string; value: string}
        | null>(null);
    const [selected, setSelected] = React.useState<number | null>(null);
    const [sort, setSort] = React.useState<Sort | null>(null);
    // What the status says that the state does not, until the set list's
    // progress changes.
    const [notice, setNotice] = React.useState<{key: string | null;
                                                text: string} | null>(null);
    const [asking, setAsking] = React.useState(false);
    // Each opening brings back the query of the rows fetched last (the
    // window's PrepareForDisplay), once they are read -- not while a fetch
    // runs, which is the query being made.
    const restoring = React.useRef(false);
    const mounted = React.useRef(true);
    const setsKey = progressKey(setsProgress);
    const setsKeyRef = React.useRef(setsKey);
    setsKeyRef.current = setsKey;

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const phaseRows = React.useMemo(() => rowsOf(phases.list), [phases.list]);
    const rereadPhases = phases.reread;
    // What a fetch asks for depends on the phases (none: the whole event), so
    // none is asked for until they are known: fetched (`done`) and read since.
    // While they are not -- idle, being fetched (each tournament's first
    // opening asks for them), failed -- an empty list is not "no phases".
    const phasesKnown = phasesProgress?.status === "done" && phases.current;
    const list = sets.list;
    const listUrl = text(list?.url);
    const query = listUrl ? fields(list?.query) : null;
    const phasesRevision = String(phasesProgress?.revision ?? "");
    const phase = choice !== null && choice.key === phasesRevision
        ? choice.value : defaultPhase(phaseRows, query);

    const askPhases = React.useCallback(() => {
        write(() => apiPost("/api/v1/tournament/phases:refresh", {},
                            WRITE_OPTIONS),
              (status) => (status === 409 ? null : i18n.t("request_failed")),
              false).catch(noop);
    }, [write]);

    // Opened: the phases asked for when there are none to read (never
    // fetched for this tournament, or the fetch failed), as the window asks
    // the provider again -- the sets never.
    const wasOpen = React.useRef(false);
    React.useEffect(() => {
        if (open && !wasOpen.current) {
            setSelected(null);
            setNotice(null);
            restoring.current = true;
            const status = phasesProgress?.status;
            if (status === undefined || status === "idle" || status === "error") {
                askPhases();
            }
        }
        wasOpen.current = open;
    }, [open, phasesProgress, askPhases]);

    // A new list read: nothing selected (the window refills its table).
    React.useEffect(() => {
        setSelected(null);
        if (!restoring.current || list === null) {
            return;
        }
        restoring.current = false;
        if (!setsRunning && query) {
            setChoice(null);
            setFinished(query.finished === true);
        }
        // Only a new read decides.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [list]);

    const fetchSets = React.useCallback((phaseValue: string,
                                         finishedValue: boolean) => {
        if (!phasesKnown) {
            return;
        }
        const asked = phaseQuery(phaseRows, phaseValue);
        if (asked === undefined) {
            setNotice({key: setsKeyRef.current,
                       text: i18n.t("score_sets_choose_phase")});
            return;
        }
        setNotice(null);
        setAsking(true);
        write(() => apiPost("/api/v1/tournament/sets:refresh",
                            {phase: asked, finished: finishedValue},
                            WRITE_OPTIONS), () => null, false)
            .catch((error: unknown) => {
                const status = apiErrorStatus(error);
                const code = apiErrorCode(error);
                if (status === 409 && code === "NOT_READY") {
                    setNotice({key: setsKeyRef.current,
                               text: i18n.t("score_sets_no_tournament")});
                } else if (status === 400 || status === 404) {
                    // The phases read are not core's any more: read again.
                    rereadPhases();
                    setNotice({key: setsKeyRef.current,
                               text: i18n.t("score_sets_choose_phase")});
                } else if (status !== 409 && status !== 401 && status !== 403) {
                    setNotice({key: setsKeyRef.current,
                               text: i18n.t("request_failed")});
                }
            })
            .finally(() => {
                if (mounted.current) {
                    setAsking(false);
                }
            });
    }, [phasesKnown, phaseRows, rereadPhases, write]);

    const pickPhase = (value: string) => {
        setChoice({key: phasesRevision, value});
        // The placeholder fetches nothing; any other pick fetches, the same
        // one again too (the window's `activated`).
        if (value !== NO_PHASE) {
            fetchSets(value, finished);
        }
    };

    const rows = React.useMemo(() => rowsOf(list), [list]);
    const shown = React.useMemo(() => {
        const all = setShown(rows);
        const pairs = completePairs
            ? all.filter((item) => isCompletePair(item.row)) : all;
        return sortShown(filterShown(pairs, filter), sort);
    }, [rows, completePairs, filter, sort]);

    const take = (item: Shown<SetRow> | null) => {
        onClose();
        // The fetch is no longer wanted (the window stops it as it loads).
        if (setsRunning) {
            write(() => apiPost("/api/v1/tournament/sets:cancel", {},
                                WRITE_OPTIONS), undefined, false).catch(noop);
        }
        if (item !== null) {
            write(() => apiPost(`/api/v1/scoreboards/${board}:load-set`,
                                {setId: item.row.id}, WRITE_OPTIONS),
                  (status, code) => (status === 409 && code === "NOT_READY"
                      ? i18n.t("score_sets_no_tournament")
                      : i18n.t("request_failed"))).catch(noop);
        }
    };

    const line = statusLine(
        setsProgress,
        list ? {url: listUrl, rows: rows.length, fetchedAt: list.fetchedAt}
            : null,
        notice && notice.key === setsKey ? notice.text : null,
        (key, values) => i18n.t(key, values));

    const columns = [...SET_COLUMNS.map((key) => i18n.t(key)),
                     i18n.t("score_sets_col_player", {n: 1}),
                     i18n.t("score_sets_col_player", {n: 2})];

    return (
        <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth
                aria-labelledby="score-sets-title">
            <DialogTitle id="score-sets-title">
                {i18n.t("score_sets_title")}
            </DialogTitle>
            <DialogContent>
                <Stack spacing={1.5} sx={{pt: 1}}>
                    <TextField
                        size="small"
                        type="search"
                        label={i18n.t("score_sets_filter")}
                        value={filter}
                        onChange={(event) => setView({filter: event.target.value})}
                    />
                    <Stack direction={{xs: "column", sm: "row"}} spacing={1}
                           alignItems={{sm: "center"}}>
                        {/* The window hides the phases (not the button)
                            when the event has none. */}
                        {phaseRows.length > 0 && (
                            <TextField
                                select
                                size="small"
                                label={i18n.t("score_sets_phase")}
                                value={phase}
                                sx={{flex: 1}}
                                slotProps={{select: {displayEmpty: true},
                                            inputLabel: {shrink: true}}}
                            >
                                <MenuItem value={NO_PHASE}
                                          onClick={() => pickPhase(NO_PHASE)}>
                                    {i18n.t("score_sets_phase_none")}
                                </MenuItem>
                                <MenuItem value={ALL_PHASES}
                                          onClick={() => pickPhase(ALL_PHASES)}>
                                    {i18n.t("score_sets_phase_all")}
                                </MenuItem>
                                {phaseRows.map((row) => (
                                    <MenuItem key={text(row.id)}
                                              value={text(row.id)}
                                              onClick={() => pickPhase(
                                                  text(row.id))}>
                                        {text(row.name)}
                                    </MenuItem>
                                ))}
                            </TextField>
                        )}
                        <Box sx={{flex: phaseRows.length > 0 ? 0 : 1}}/>
                        <Button
                            variant="contained"
                            disabled={asking || setsRunning || !phasesKnown}
                            onClick={() => fetchSets(phase, finished)}
                            sx={{whiteSpace: "nowrap"}}
                        >
                            {i18n.t("score_sets_fetch")}
                        </Button>
                    </Stack>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                        <FormControlLabel
                            control={
                                <Checkbox
                                    size="small"
                                    checked={finished}
                                    onChange={(event) => {
                                        setFinished(event.target.checked);
                                        fetchSets(phase, event.target.checked);
                                    }}
                                />
                            }
                            label={i18n.t("score_sets_finished")}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    size="small"
                                    checked={completePairs}
                                    onChange={(event) => {
                                        setView({completePairs:
                                            event.target.checked});
                                        setSelected(null);
                                    }}
                                />
                            }
                            label={i18n.t("score_sets_complete_pairs")}
                        />
                    </Stack>
                    {/* The phases not known, said (a fetch waits for them):
                        their fetch failed -- asked again -- or their list
                        could not be read -- read again. */}
                    {phasesProgress?.status === "error" && (
                        <Alert
                            severity="error"
                            action={
                                <Button color="inherit" size="small"
                                        onClick={askPhases}>
                                    {i18n.t("score_reload")}
                                </Button>
                            }
                        >
                            {i18n.t("score_sets_phases_failed")}
                        </Alert>
                    )}
                    {phases.failed && (
                        <Alert
                            severity="error"
                            action={
                                <Button color="inherit" size="small"
                                        onClick={rereadPhases}>
                                    {i18n.t("score_reload")}
                                </Button>
                            }
                        >
                            {i18n.t("score_sets_phases_read_failed")}
                        </Alert>
                    )}
                    {sets.failed && (
                        <Alert
                            severity="error"
                            action={
                                <Button color="inherit" size="small"
                                        onClick={sets.reread}>
                                    {i18n.t("score_reload")}
                                </Button>
                            }
                        >
                            {i18n.t("score_sets_read_failed")}
                        </Alert>
                    )}
                    <ListTable
                        label={i18n.t("score_sets_title")}
                        columns={columns}
                        shown={shown}
                        sort={sort}
                        onSort={setSort}
                        selected={selected}
                        onSelect={setSelected}
                        onTake={take}
                    />
                    <FetchProgress progress={setsProgress}/>
                    <Typography role="status" variant="body2"
                                color="text.secondary">
                        {line}
                    </Typography>
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button variant="contained"
                        onClick={() => take(chosenRow(shown, selected))}>
                    {i18n.t("score_sets_ok")}
                </Button>
                <Button variant="outlined" onClick={onTrack}>
                    {i18n.t("score_sets_track")}
                </Button>
                <Box sx={{flex: 1}}/>
                <Button onClick={onClose}>{i18n.t("score_sets_close")}</Button>
            </DialogActions>
        </Dialog>
    );
}

// ── the station dialog ───────────────────────────────────────────────────

const STATION_COLUMNS = [
    "score_stations_col_type",
    "score_stations_col_id",
    "score_stations_col_stream",
    "score_stations_col_identifier",
];

// The station window: its rows emptied and fetched again at every opening
// (no cache), its table searched and sorted -- the search and the order
// kept from one opening to the next, each board's (the window is each
// board's, hidden, not destroyed) -- and the row chosen (chosenRow)
// followed as it is, POST /scoreboards/{n}:follow. Enter in the search is
// OK, as the window's only button is its default one.
export function StationDialog({open, board, write, onClose}: {
    open: boolean;
    board: number;
    write: Write;
    onClose: () => void;
}) {
    const progress = useProgress("stations");
    const key = progressKey(progress);
    const running = progress?.status === "running";
    // The progress when the dialog opened (null: not open): rows are shown
    // once it changed to an end (the fetch this opening asked for, or the
    // one it found running), never those of a fetch before it. Taken as the
    // dialog opens, in the render (not after it, which shows the last
    // opening's rows once).
    const [opened, setOpened] = React.useState<{key: string | null} | null>(
        null);
    const ended = open && opened !== null && key !== opened.key && !running;
    const stations = useListRead<StationRow>("/api/v1/tournament/stations",
                                             ended, progress);
    if (open && opened === null) {
        setOpened({key});
        // Nothing of the last opening (or of a fetch while closed).
        stations.clear();
    } else if (!open && opened !== null) {
        setOpened(null);
    }
    // Each board's search and order (the window's QLineEdit and proxy).
    const [views, setViews] = React.useState<Record<number, {filter: string;
                                                             sort: Sort | null}>>(
        {});
    const filter = views[board]?.filter ?? "";
    const sort = views[board]?.sort ?? null;
    const setView = (change: {filter?: string; sort?: Sort | null}) =>
        setViews((known) => ({...known, [board]: {filter, sort, ...change}}));
    const [selected, setSelected] = React.useState<number | null>(null);
    const [notice, setNotice] = React.useState<string | null>(null);

    // Each opening asks for the stations again (the window empties its
    // table and fetches them every time it is shown; what was typed in it
    // and its order stay).
    const wasOpen = React.useRef(false);
    React.useEffect(() => {
        if (open && !wasOpen.current) {
            setSelected(null);
            setNotice(null);
            write(() => apiPost("/api/v1/tournament/stations:refresh", {},
                                WRITE_OPTIONS), () => null, false)
                .catch((error: unknown) => {
                    const status = apiErrorStatus(error);
                    if (status === 409 && apiErrorCode(error) === "NOT_READY") {
                        setNotice(i18n.t("score_sets_no_tournament"));
                    } else if (status !== 409 && status !== 401
                            && status !== 403) {
                        setNotice(i18n.t("request_failed"));
                    }
                });
        }
        wasOpen.current = open;
    }, [open, write]);

    // Read for this opening: the rows of a fetch that ended since.
    const current = opened !== null && stations.list !== null;
    const rows = React.useMemo(() => (current ? rowsOf(stations.list) : []),
                               [current, stations.list]);
    const shown = React.useMemo(
        () => sortShown(filterShown(stationShown(rows), filter), sort),
        [rows, filter, sort]);

    React.useEffect(() => {
        setSelected(null);
    }, [stations.list]);

    const take = (item: Shown<StationRow> | null) => {
        onClose();
        if (item !== null) {
            write(() => apiPost(`/api/v1/scoreboards/${board}:follow`,
                                item.row, WRITE_OPTIONS),
                  (status, code) => (status === 409 && code === "NOT_READY"
                      ? i18n.t("score_sets_no_tournament")
                      : i18n.t("request_failed"))).catch(noop);
        }
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth
                aria-labelledby="score-stations-title">
            <DialogTitle id="score-stations-title">
                {i18n.t("score_stations_title")}
            </DialogTitle>
            <DialogContent>
                <Stack spacing={1.5} sx={{pt: 1}}>
                    <TextField
                        size="small"
                        type="search"
                        label={i18n.t("score_sets_filter")}
                        value={filter}
                        onChange={(event) => setView({filter: event.target.value})}
                        onKeyDown={(event) => {
                            // Enter is OK (the window's default button), not
                            // the one that ends an IME conversion.
                            const native = event.nativeEvent;
                            if (event.key !== "Enter" || native.isComposing
                                    || native.keyCode === 229) {
                                return;
                            }
                            event.preventDefault();
                            take(chosenRow(shown, selected));
                        }}
                    />
                    <ListTable
                        label={i18n.t("score_stations_title")}
                        columns={STATION_COLUMNS.map((name) => i18n.t(name))}
                        shown={shown}
                        sort={sort}
                        onSort={(next) => setView({sort: next})}
                        selected={selected}
                        onSelect={setSelected}
                        onTake={take}
                    />
                    {open && <FetchProgress progress={progress}/>}
                    {notice && (
                        <Typography role="status" variant="body2"
                                    color="text.secondary">
                            {notice}
                        </Typography>
                    )}
                    {ended && progress?.status === "error" && (
                        <Alert severity="error">
                            {i18n.t("score_stations_failed")}
                        </Alert>
                    )}
                    {stations.failed && (
                        <Alert
                            severity="error"
                            action={
                                <Button color="inherit" size="small"
                                        onClick={stations.reread}>
                                    {i18n.t("score_reload")}
                                </Button>
                            }
                        >
                            {i18n.t("score_sets_read_failed")}
                        </Alert>
                    )}
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button variant="contained"
                        onClick={() => take(chosenRow(shown, selected))}>
                    {i18n.t("score_sets_ok")}
                </Button>
                <Box sx={{flex: 1}}/>
                <Button onClick={onClose}>{i18n.t("score_sets_close")}</Button>
            </DialogActions>
        </Dialog>
    );
}

// ── the follow row ───────────────────────────────────────────────────────

// GET /scoreboards/{n}/follow's poll: what is polled and the seconds to its
// next tick when read.
export interface Poll {
    mode: string;
    identifier: unknown;
    secondsLeft: number | null;
}

export function readPoll(data: unknown): Poll | null {
    const poll = fields(data).poll;
    if (!poll || typeof poll !== "object") {
        return null;
    }
    const value = fields(poll);
    const seconds = value.secondsLeft;
    return {mode: text(value.mode), identifier: value.identifier,
            secondsLeft: typeof seconds === "number" && Number.isFinite(seconds)
                ? seconds : null};
}

// The timer row's label, in the Qt board's words.
export function followLabel(poll: Poll): string {
    const id = text(poll.identifier);
    switch (poll.mode) {
        case "set":
            return i18n.t("score_follow_set");
        case "stream":
            return i18n.t("score_follow_stream", {id});
        case "station":
            return i18n.t("score_follow_station", {id});
        default:
            return i18n.t("score_follow_other");
    }
}

// How often the count is looked at: whole seconds are shown, so 200 ms is
// soon enough (the Qt row looks every 100 ms).
export const FOLLOW_TICK_MS = 200;

// The seconds to the next tick, counted down from `secondsLeft` as read at
// `at` (Date.now()); past 0 -- the tick has come, and a set's brings no
// notice -- it is read again (`reread`), once per read. A read with no
// seconds (null: the next tick not known) keeps the number shown last, as
// the Qt row does; null only before any.
export function useSecondsLeft(poll: Poll | null, at: number,
                               reread: () => void): number | null {
    const [now, setNow] = React.useState(() => Date.now());
    const rereadFor = React.useRef<number | null>(null);
    const lastShown = React.useRef<number | null>(null);
    const active = poll !== null;

    React.useEffect(() => {
        if (!active) {
            return undefined;
        }
        setNow(Date.now());
        const timer = setInterval(() => setNow(Date.now()), FOLLOW_TICK_MS);
        return () => clearInterval(timer);
    }, [active, at]);

    const elapsed = Math.floor(Math.max(0, now - at) / 1000);
    const left = poll === null ? 0 : (poll.secondsLeft ?? 0) - elapsed;
    const overdue = poll !== null && left < 0;
    React.useEffect(() => {
        if (overdue && rereadFor.current !== at) {
            rereadFor.current = at;
            reread();
        }
    }, [overdue, at, reread]);

    if (poll === null) {
        lastShown.current = null;
        return null;
    }
    if (poll.secondsLeft === null) {
        return lastShown.current;
    }
    lastShown.current = Math.max(0, left);
    return lastShown.current;
}

// The board's timer row: what it follows, the seconds to the next update and
// the cancel button (POST :stop-follow).
export function FollowRow({poll, seconds, onCancel}: {
    poll: Poll;
    seconds: number | null;
    onCancel: () => void;
}) {
    return (
        <Stack direction="row" spacing={1} alignItems="center"
               justifyContent="center" data-testid="score-follow">
            <Typography variant="body2">{followLabel(poll)}</Typography>
            <Typography variant="body2" aria-label={i18n.t("score_follow_seconds")}>
                {seconds === null ? "" : String(seconds)}
            </Typography>
            <Tooltip title={i18n.t("score_follow_cancel")}>
                <IconButton size="small" onClick={onCancel}
                            aria-label={i18n.t("score_follow_cancel")}>
                    <CancelIcon fontSize="small"/>
                </IconButton>
            </Tooltip>
        </Stack>
    );
}
