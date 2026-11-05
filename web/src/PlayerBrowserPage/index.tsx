import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    Stack,
    Tab,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TablePagination,
    TableRow,
    TableSortLabel,
    Tabs,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {inlineFlagWidth} from "../env";
import {ReduxState} from "../redux/store";
import {apiErrorCode, apiErrorStatus, apiPost, apiPut} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    LOAD_RETRY_MS,
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {CountryEntry, assetUrl} from "../Top8Page/Cards";
import {
    CharacterInfo,
    Column,
    DEFAULT_SORT,
    Draft,
    EMPTY_ROW,
    Main,
    PAGE_SIZE,
    PlayerRow,
    ShownRow,
    Sort,
    filterRows,
    lastPage,
    pageOf,
    saveBody,
    shownRows,
    sortRows,
} from "./playerRows";
import {EditorDialog} from "./EditorDialog";

// The Player List as a React page (ADR-005 S4n), /web/playerBrowser: the
// loaded tournament's entrants and the players registered in the player DB
// (user_data/local_players.json). Shown in the desktop app's shared web view
// and in a browser alike. The rows are core's (PlayerDirectory, GET
// /api/v1/players and /players/entrants) and are not in the state: the page
// reads them when it comes up and when the DB, the tournament or the game
// changes. Editing is a draft of the page's own until it is saved (PUT
// /api/v1/players); "Set as P1 / P2" load the tag onto board 1 (:load).

const TABS = ["players_tab_entrants", "players_tab_registered"];

// The tab shown: kept while the page is away (the shared view shows another
// panel) and back.
let selectedTab = 0;

// How many times the country rows are read while the server is still loading
// the country data (NOT_READY), the waits growing by LOAD_RETRY_MS. After
// that, the next reread tries again.
const COUNTRY_READS = 5;

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface Listing {
    game: string | null;
    rows: PlayerRow[];
}

interface Editing {
    row: PlayerRow;
    title: string;
    game: string | null;
}

// What a refused write says; null: nothing (the caller tells it).
type Refusal = (status: number | undefined,
                code: string | undefined) => string | null;

interface ColumnSpec {
    id: Column | "seed";
    label: string;
    sortable: boolean;
}

function Flag({country}: {country: unknown}) {
    const src = assetUrl((country as {asset?: unknown} | undefined)?.asset);
    return src ? (
        <img
            alt=""
            loading="lazy"
            src={src}
            width={inlineFlagWidth}
            style={{marginRight: 8, verticalAlign: "middle"}}
            onError={(event) => {
                (event.target as HTMLImageElement).style.display = "none";
            }}
        />
    ) : null;
}

function cellText(item: ShownRow, column: ColumnSpec["id"]): string {
    switch (column) {
        case "seed":
            return item.seed;
        case "prefix":
            return item.row.prefix;
        case "gamerTag":
            return item.row.gamerTag;
        case "twitter":
            return item.row.twitter;
        default:
            return "";
    }
}

// A table of players: the search over it, its actions and its pages. The
// parent is told which row is selected (null when it leaves the table: it
// no longer matches the search, it is on another page, or it is gone).
function PlayersTable({label, columns, rows, sort, onSort, empty, countries,
                       actions, onSelect, onOpen}: {
    label: string;
    columns: ColumnSpec[];
    rows: ShownRow[];
    // Null: the rows stay in the server's order.
    sort: Sort | null;
    onSort?: (sort: Sort) => void;
    // What an empty table says.
    empty: string;
    countries: Record<string, unknown> | undefined;
    actions: React.ReactNode;
    onSelect: (item: ShownRow | null) => void;
    onOpen: (item: ShownRow) => void;
}) {
    const [filter, setFilter] = React.useState("");
    const [page, setPage] = React.useState(0);
    const [selectedId, setSelectedId] = React.useState<string | null>(null);
    // Typing stays quick with thousands of players: the table follows the
    // text when React has the time.
    const deferredFilter = React.useDeferredValue(filter);
    const shown = React.useMemo(() => {
        const found = filterRows(rows, deferredFilter);
        return sort ? sortRows(found, sort) : found;
    }, [rows, deferredFilter, sort]);
    // A read may leave fewer pages than the one being looked at.
    const currentPage = Math.min(page, lastPage(shown.length));
    const onPage = React.useMemo(() => pageOf(shown, currentPage),
                                 [shown, currentPage]);
    const selected = onPage.find((item) => item.id === selectedId) ?? null;

    if (selectedId !== null && selected === null) {
        setSelectedId(null);
    }
    React.useEffect(() => {
        onSelect(selected);
    }, [onSelect, selected]);

    return (
        <Stack spacing={1} sx={{flex: 1, minHeight: 0}}>
            <Stack direction={{xs: "column", md: "row"}} spacing={1}
                   alignItems={{md: "center"}}>
                <TextField
                    size="small"
                    type="search"
                    label={i18n.t("players_search")}
                    value={filter}
                    onChange={(event) => {
                        setFilter(event.target.value);
                        setPage(0);
                    }}
                    sx={{flex: 1}}
                />
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                    {actions}
                </Stack>
            </Stack>
            <TableContainer sx={{flex: 1, minHeight: 0, overflowY: "auto"}}>
                <Table size="small" stickyHeader aria-label={label}>
                    <TableHead>
                        <TableRow>
                            {columns.map((column) => {
                                const active = sort?.column === column.id;
                                const direction = active && sort?.descending
                                    ? "desc" : "asc";
                                return (
                                    <TableCell
                                        key={column.id}
                                        sortDirection={active ? direction
                                            : false}
                                    >
                                        {column.sortable ? (
                                            <TableSortLabel
                                                active={active}
                                                direction={direction}
                                                onClick={() => {
                                                    setPage(0);
                                                    onSort?.({
                                                        column: column.id as Column,
                                                        descending: active
                                                            && !sort?.descending,
                                                    });
                                                }}
                                            >
                                                {column.label}
                                            </TableSortLabel>
                                        ) : column.label}
                                    </TableCell>
                                );
                            })}
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {shown.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={columns.length}>
                                    <Typography variant="body2"
                                                color="text.secondary">
                                        {rows.length === 0 ? empty
                                            : i18n.t("players_no_match")}
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        )}
                        {onPage.map((item) => (
                            <TableRow
                                key={item.id}
                                hover
                                selected={item.id === selectedId}
                                tabIndex={0}
                                sx={{cursor: "pointer"}}
                                onClick={() => setSelectedId(item.id)}
                                onDoubleClick={() => onOpen(item)}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                        onOpen(item);
                                    } else if (event.key === " ") {
                                        event.preventDefault();
                                        setSelectedId(item.id);
                                    }
                                }}
                            >
                                {columns.map((column) => (
                                    <TableCell key={column.id}>
                                        {column.id === "country" && (
                                            <>
                                                <Flag country={countries
                                                    ?.[item.row.country_code]}/>
                                                {item.row.country_code}
                                            </>
                                        )}
                                        {column.id === "characters" && (
                                            <>
                                                {item.icon && (
                                                    <img
                                                        alt=""
                                                        loading="lazy"
                                                        src={assetUrl(item.icon)
                                                            ?? undefined}
                                                        height={24}
                                                        style={{
                                                            marginRight: 8,
                                                            verticalAlign:
                                                                "middle",
                                                        }}
                                                    />
                                                )}
                                                {item.characters}
                                            </>
                                        )}
                                        {cellText(item, column.id)}
                                    </TableCell>
                                ))}
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
            <TablePagination
                component="div"
                count={shown.length}
                page={currentPage}
                rowsPerPage={PAGE_SIZE}
                rowsPerPageOptions={[]}
                onPageChange={(_event, next) => setPage(next)}
                labelDisplayedRows={({from, to, count}) =>
                    i18n.t("players_page_rows", {from, to, count})}
                getItemAriaLabel={(type) => i18n.t(type === "next"
                    ? "players_page_next" : "players_page_previous")}
            />
        </Stack>
    );
}

function PlayerBrowserContent() {
    const {language} = usePanelShell();
    // What makes the rows stale. The DB's push is a new object every time;
    // the tournament's by how its newest set or clear goes.
    const playerDb = useSelector((state: ReduxState) =>
        state.autonaPlayers.players);
    const codename = useSelector((state: ReduxState) => {
        const game = (state.autonaState.autonaState as unknown as
            {game?: {codename?: unknown}}).game;
        return typeof game?.codename === "string" ? game.codename : "";
    });
    const tournamentLoad = useSelector((state: ReduxState) => {
        const load = (state.autonaState.autonaState as unknown as
            {tournamentInfo?: {load?: {revision?: unknown;
                                       status?: unknown}}}).tournamentInfo?.load;
        return load ? `${String(load.revision)}:${String(load.status)}` : "";
    });
    const [tab, setTab] = React.useState(selectedTab);
    const [entrants, setEntrants] = React.useState<Listing | null>(null);
    const [registered, setRegistered] = React.useState<Listing | null>(null);
    const [entrantsFailed, setEntrantsFailed] = React.useState(false);
    const [registeredFailed, setRegisteredFailed] = React.useState(false);
    const [rereads, setRereads] = React.useState(0);
    const [characters, setCharacters] = React.useState<
        Record<string, CharacterInfo>>({});
    const [variants, setVariants] = React.useState<ReadonlySet<string> | null>(
        null);
    const [countryRows, setCountryRows] = React.useState<CountryEntry[] | null>(
        null);
    const [sort, setSort] = React.useState<Sort>(DEFAULT_SORT);
    const [entrant, setEntrant] = React.useState<ShownRow | null>(null);
    const [player, setPlayer] = React.useState<ShownRow | null>(null);
    const [editing, setEditing] = React.useState<Editing | null>(null);
    // The key the delete confirmation asks about.
    const [deleting, setDeleting] = React.useState<string | null>(null);
    const [busy, setBusy] = React.useState(false);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every request after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("players_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    const tellToken = React.useCallback((status: number | undefined) => {
        if (status !== 401 && status !== 403) {
            return false;
        }
        if (!toldToken.current) {
            toldToken.current = true;
            showSnackbar(i18n.t("stream_token_required"), "error");
        }
        return true;
    }, []);

    React.useEffect(() => {
        // The newest read wins: an older one answering late is dropped. Each
        // listing on its own: the DB's does not need the tournament's.
        let live = true;
        const isLive = () => live;
        const failed = (setFailed: (value: boolean) => void) => (error: unknown) => {
            console.error(error);
            if (live) {
                tellToken(apiErrorStatus(error));
                setFailed(true);
            }
        };
        getWithRetry<Envelope<{game: string | null; entrants: PlayerRow[]}>>(
            "/api/v1/players/entrants", isLive)
            .then((body) => {
                if (live) {
                    setEntrants({game: body.data.game, rows: body.data.entrants});
                    setEntrantsFailed(false);
                }
            }, failed(setEntrantsFailed));
        getWithRetry<Envelope<{game: string | null; players: PlayerRow[]}>>(
            "/api/v1/players", isLive)
            .then((body) => {
                if (live) {
                    setRegistered({game: body.data.game, rows: body.data.players});
                    setRegisteredFailed(false);
                }
            }, failed(setRegisteredFailed));
        return () => {
            live = false;
        };
    }, [playerDb, codename, tournamentLoad, rereads, tellToken]);

    // The icons and the characters' names in the tables. Without them a
    // table still works: it shows the en_names the rows have.
    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Record<string, CharacterInfo>>>(
            "/api/v1/characters", () => live)
            .then((body) => {
                if (live) {
                    setCharacters(body.data ?? {});
                }
            }, (error) => {
                console.error(error);
            });
        // What the editor checks a save's mains against. Without them the
        // server is the one to say.
        getWithRetry<Envelope<Record<string, {en_name?: unknown}>>>(
            "/api/v1/variants", () => live)
            .then((body) => {
                if (live) {
                    setVariants(new Set(
                        Object.values(body.data ?? {})
                            .map((row) => row?.en_name)
                            .filter((name): name is string =>
                                typeof name === "string")));
                }
            }, (error) => {
                console.error(error);
                if (live) {
                    setVariants(null);
                }
            });
        return () => {
            live = false;
        };
    }, [codename, rereads]);

    // The country field's rows, the custom flags among them. Not read (the
    // country data is still loading, NOT_READY): the field offers the list
    // the Web UI has, and they are asked for again in a while.
    React.useEffect(() => {
        let live = true;
        let wait: ReturnType<typeof setTimeout> | undefined;
        const read = (attempt: number) => {
            getWithRetry<Envelope<CountryEntry[]>>("/api/v1/countries",
                                                   () => live)
                .then((body) => {
                    if (live) {
                        setCountryRows(body.data ?? []);
                    }
                }, (error) => {
                    console.error(error);
                    if (live && apiErrorCode(error) === "NOT_READY"
                        && attempt < COUNTRY_READS) {
                        wait = setTimeout(() => read(attempt + 1),
                                          LOAD_RETRY_MS * attempt);
                    }
                });
        };
        read(1);
        return () => {
            live = false;
            clearTimeout(wait);
        };
    }, [rereads]);

    const countries = React.useMemo(() => {
        if (countryRows === null) {
            return undefined;
        }
        const byCode: Record<string, unknown> = {};
        countryRows.forEach((row) => {
            // First row wins, as core's (CountryCatalog.CountryByCode).
            if (!Object.prototype.hasOwnProperty.call(byCode, row.code)) {
                byCode[row.code] = {...row, flag: assetUrl(row.asset)};
            }
        });
        return byCode;
    }, [countryRows]);

    const entrantRows = React.useMemo(
        () => shownRows(entrants?.rows ?? [], characters),
        [entrants, characters]);
    const playerRows = React.useMemo(
        () => shownRows(registered?.rows ?? [], characters),
        [registered, characters]);
    const registeredMains = React.useMemo(
        () => new Map<string, Main[]>((registered?.rows ?? []).map(
            (row) => [row.key as string, row.mains])),
        [registered]);

    const write = React.useCallback(
        <T,>(request: () => Promise<T>, refusal: Refusal) =>
            serial(request).catch((error: unknown) => {
                console.error(error);
                const status = apiErrorStatus(error);
                if (!tellToken(status)) {
                    const message = refusal(status, apiErrorCode(error));
                    if (message) {
                        showSnackbar(message, "error");
                    }
                }
                throw error;
            }), [serial, tellToken]);

    const reread = React.useCallback(() => setRereads((n) => n + 1), []);

    const openEntrant = React.useCallback((item: ShownRow) => {
        setEditing({row: item.row, title: i18n.t("players_edit"),
                    game: entrants?.game ?? null});
    }, [entrants]);

    const openPlayer = React.useCallback((item: ShownRow) => {
        setEditing({row: item.row, title: i18n.t("players_edit"),
                    game: registered?.game ?? null});
    }, [registered]);

    const openNew = () => {
        setEditing({row: EMPTY_ROW, title: i18n.t("players_add"),
                    game: registered?.game ?? null});
    };

    const save = (draft: Draft) => {
        if (editing === null) {
            return;
        }
        const opened = editing;
        setBusy(true);
        write(() => apiPut("/api/v1/players",
                           saveBody(draft, opened.row, opened.game),
                           WRITE_OPTIONS),
              (status, code) => {
                  if (status === 400) {
                      return i18n.t("players_save_refused");
                  }
                  if (status === 409) {
                      return i18n.t(code === "NOT_READY"
                          ? "players_countries_not_ready"
                          : "players_save_conflict");
                  }
                  return i18n.t("request_failed");
              })
            .then(() => {
                showSnackbar(i18n.t("players_saved"), "success");
                setEditing((current) => (current === opened ? null : current));
                reread();
            }, (error: unknown) => {
                // What the save lost to is read; the draft stays to try again.
                if (apiErrorStatus(error) === 409) {
                    reread();
                }
            })
            .finally(() => setBusy(false));
    };

    const confirmDelete = () => {
        if (deleting === null) {
            return;
        }
        const key = deleting;
        setDeleting(null);
        setBusy(true);
        write(() => apiPost("/api/v1/players:delete", {key, confirm: true},
                            WRITE_OPTIONS),
              (status) => (status === 404 ? i18n.t("players_delete_gone")
                  : i18n.t("request_failed")))
            .then(() => {
                showSnackbar(i18n.t("players_deleted"), "success");
                setEditing(null);
                reread();
            }, (error: unknown) => {
                if (apiErrorStatus(error) === 404) {
                    reread();
                }
            })
            .finally(() => setBusy(false));
    };

    const loadToBoard = (team: number) => {
        if (entrant === null) {
            return;
        }
        const tag = entrant.row.gamerTag;
        write(() => apiPost(`/api/v1/scoreboards/1/teams/${team}/players/1:load`,
                            {tag}, WRITE_OPTIONS),
              (status) => (status === 404 ? i18n.t("players_load_not_found")
                  : i18n.t("request_failed")))
            .then(() => {
                showSnackbar(i18n.t("players_loaded", {tag, team}), "success");
            }, () => undefined);
    };

    const choose = (_event: React.SyntheticEvent, value: number) => {
        selectedTab = value;
        setTab(value);
    };

    const entrantColumns: ColumnSpec[] = [
        {id: "seed", label: i18n.t("players_seed"), sortable: false},
        {id: "prefix", label: i18n.t("players_sponsor"), sortable: false},
        {id: "gamerTag", label: i18n.t("players_gamertag"), sortable: false},
        {id: "country", label: i18n.t("players_country"), sortable: false},
        {id: "characters", label: i18n.t("players_characters"), sortable: false},
    ];
    const playerColumns: ColumnSpec[] = [
        {id: "gamerTag", label: i18n.t("players_gamertag"), sortable: true},
        {id: "prefix", label: i18n.t("players_sponsor"), sortable: true},
        {id: "country", label: i18n.t("players_country"), sortable: true},
        {id: "characters", label: i18n.t("players_characters"), sortable: true},
        {id: "twitter", label: i18n.t("players_twitter"), sortable: true},
    ];
    // A tab waits for its own listing, and tells of its own.
    const loading = tab === 0
        ? entrants === null && !entrantsFailed
        : registered === null && !registeredFailed;
    const readFailed = tab === 0 ? entrantsFailed : registeredFailed;
    const teamHint = i18n.t("players_team_hint");

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
            <Tabs
                value={tab}
                onChange={choose}
                variant="scrollable"
                aria-label={i18n.t("players_title")}
            >
                {TABS.map((label, index) => (
                    <Tab key={label} label={i18n.t(label)}
                         id={`players-tab-${index}`}
                         aria-controls={`players-tabpanel-${index}`}/>
                ))}
            </Tabs>
            {readFailed && (
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reread}>
                            {i18n.t("players_reload")}
                        </Button>
                    }
                >
                    {i18n.t("players_read_failed")}
                </Alert>
            )}
            {loading && (
                <Box sx={{display: "flex", justifyContent: "center", pt: 4}}>
                    <CircularProgress/>
                </Box>
            )}
            {/* Both stay mounted: each keeps its search, its page and
                its selection while the other is shown. */}
            <Box
                role="tabpanel"
                id="players-tabpanel-0"
                aria-labelledby="players-tab-0"
                hidden={tab !== 0 || loading}
                sx={{display: tab === 0 && !loading ? "flex" : "none",
                     flex: 1, minHeight: 0}}
            >
                {entrantRows.length === 0 ? (
                    // Not read says nothing of the tournament.
                    entrants !== null && (
                        <Typography color="text.secondary"
                                    sx={{m: "auto", textAlign: "center"}}>
                            {i18n.t("players_no_tournament")}
                        </Typography>
                    )
                ) : (
                    <PlayersTable
                        label={i18n.t("players_tab_entrants")}
                        columns={entrantColumns}
                        rows={entrantRows}
                        sort={null}
                        empty=""
                        countries={countries}
                        onSelect={setEntrant}
                        onOpen={openEntrant}
                        actions={(
                            <>
                                {[1, 2].map((team) => (
                                    <Tooltip key={team} title={teamHint}>
                                        <span>
                                            <Button
                                                variant="outlined"
                                                disabled={entrant === null}
                                                onClick={() =>
                                                    loadToBoard(team)}
                                            >
                                                {i18n.t(`players_set_p${team}`)}
                                            </Button>
                                        </span>
                                    </Tooltip>
                                ))}
                                <Button
                                    variant="outlined"
                                    disabled={entrant === null}
                                    onClick={() => {
                                        if (entrant) {
                                            openEntrant(entrant);
                                        }
                                    }}
                                >
                                    {i18n.t("players_edit")}
                                </Button>
                            </>
                        )}
                    />
                )}
            </Box>
            <Box
                role="tabpanel"
                id="players-tabpanel-1"
                aria-labelledby="players-tab-1"
                hidden={tab !== 1 || loading}
                sx={{display: tab === 1 && !loading ? "flex" : "none",
                     flex: 1, minHeight: 0}}
            >
                <PlayersTable
                    label={i18n.t("players_tab_registered")}
                    columns={playerColumns}
                    rows={playerRows}
                    sort={sort}
                    onSort={setSort}
                    // Not read says nothing of the DB.
                    empty={registered === null
                        ? "" : i18n.t("players_none_registered")}
                    countries={countries}
                    onSelect={setPlayer}
                    onOpen={openPlayer}
                    actions={(
                        <>
                            {/* The negative action leftmost. */}
                            <Button
                                color="error"
                                variant="outlined"
                                disabled={player === null || busy}
                                onClick={() => {
                                    setDeleting(player?.row.key ?? null);
                                }}
                            >
                                {i18n.t("players_delete")}
                            </Button>
                            {/* Not read: the game a new player's
                                characters would be of is not known. */}
                            <Button
                                variant="contained"
                                disabled={registered === null}
                                onClick={openNew}
                            >
                                {i18n.t("players_add")}
                            </Button>
                            <Button
                                variant="outlined"
                                disabled={player === null}
                                onClick={() => {
                                    if (player) {
                                        openPlayer(player);
                                    }
                                }}
                            >
                                {i18n.t("players_edit")}
                            </Button>
                        </>
                    )}
                />
            </Box>
            {editing !== null && (
                <EditorDialog
                    original={editing.row}
                    title={editing.title}
                    game={editing.game}
                    gameChanged={registered !== null
                        && registered.game !== editing.game}
                    countries={countries}
                    // The DB's list not read, an entrant's record is still
                    // known by the row it was opened on.
                    registered={registered === null && editing.row.key !== null
                        ? new Map([[editing.row.key, editing.row.mains]])
                        : registeredMains}
                    variants={variants}
                    busy={busy}
                    onSave={save}
                    onDelete={setDeleting}
                    onClose={() => setEditing(null)}
                />
            )}
            <ConfirmDialog
                open={deleting !== null}
                title={i18n.t("players_delete")}
                message={i18n.t("players_delete_confirm", {key: deleting ?? ""})}
                confirm={i18n.t("players_delete_ok")}
                onConfirm={confirmDelete}
                onClose={() => setDeleting(null)}
            />
        </Box>
    );
}

export default function PlayerBrowserPage() {
    return (
        <FeedbackProvider>
            <PlayerBrowserContent/>
        </FeedbackProvider>
    );
}
