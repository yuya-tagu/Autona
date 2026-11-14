import React from "react";
import {useSelector} from "react-redux";
import {
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogContentText,
    DialogTitle,
    Divider,
    FormControlLabel,
    IconButton,
    ListItemIcon,
    ListItemText,
    Menu,
    MenuItem,
    Switch,
    TextField,
    ToggleButton,
    ToggleButtonGroup,
    Typography,
} from "@mui/material";
import CheckIcon from "@mui/icons-material/Check";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiDelete, apiErrorStatus, apiPost, apiPut} from "../api";
import socketConnection from "../websocketConnection";
import {WRITE_OPTIONS, WRITE_TIMEOUT_MS, getWithRetry} from "../PanelShell/requests";
import {useAppStatus} from "../PanelShell/useAppStatus";
import {
    BUSY_STATUSES,
    GameEntry,
    TournamentLoad,
    statusText,
    useUrlField,
} from "../EventPage/Cards";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {iconUrl} from "./navTable";
import {Pulldown, ROW_HEIGHT, Refusal, Report} from "./parts";

// The AppShell's header (ADR-005 S-final SF-5a-3): the Qt sidebar's
// operations band, top to bottom -- the collapse button, the tournament
// (its name; set / unset), the game (by name; the game assets page), the
// battle format and, for a game with modded content, its switch. Each is
// what the Qt control does, through /api/v1: POST / DELETE /tournament,
// POST /game, PUT /battle-format.

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface HeaderState {
    tournamentInfo?: {
        tournamentName?: unknown;
        url?: unknown;
        load?: Partial<TournamentLoad>;
    };
    game?: {
        codename?: unknown;
        mods_active?: unknown;
        has_modded_content?: unknown;
    };
}

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

function readLoad(raw: Partial<TournamentLoad> | undefined): TournamentLoad {
    const load = raw && typeof raw === "object" ? raw : {};
    return {
        status: load.status ?? "idle",
        error: typeof load.error === "string" ? load.error : "",
        revision: typeof load.revision === "number" ? load.revision : 0,
    };
}

// What PUT /battle-format takes, and the Qt segment's labels (not
// translated there either: AutonaSidebar._FORMATS).
export const FORMATS: [string, string][] = [
    ["singles", "1v1"], ["team", "Team"], ["crew", "Crew"]];

// The games by name, as the Qt menu lists them (Window.ReloadGames: the
// name or the codename, lower-cased).
export function gamesByName(games: Record<string, GameEntry>): string[] {
    const name = (codename: string) =>
        (games[codename]?.name || codename).toLowerCase();
    return Object.keys(games).sort((a, b) => {
        const left = name(a);
        const right = name(b);
        return left < right ? -1 : left > right ? 1 : 0;
    });
}

// Reads `path` again on the Socket.IO notice `event` and after a lost
// connection comes back (what was told meanwhile is not known). The newest
// read wins. Returns [what was read (null: nothing yet), read again, whether
// the last read failed].
function useNoticedList<T>(path: string, event: string, extra: unknown):
    [T | null, () => void, boolean] {
    const [value, setValue] = React.useState<T | null>(null);
    const [failed, setFailed] = React.useState(false);
    const [reads, setReads] = React.useState(0);
    const status = useSelector((root: ReduxState) => root.websocketInfo.status);
    const reread = React.useCallback(() => setReads((count) => count + 1), []);

    React.useEffect(() => {
        const socket = socketConnection.instance();
        socket.on(event, reread);
        return () => {
            socket.off(event, reread);
        };
    }, [event, reread]);

    const lastStatus = React.useRef(status);
    React.useEffect(() => {
        const was = lastStatus.current;
        lastStatus.current = status;
        if (status === "connected" && was !== "connected") {
            reread();
        }
    }, [status, reread]);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<T>>(path, () => live).then((body) => {
            if (live) {
                setValue(body.data);
                setFailed(false);
            }
        }, (error) => {
            // What was read stays.
            if (live) {
                console.error(error);
                setFailed(true);
            }
        });
        return () => {
            live = false;
        };
    }, [path, reads, extra]);

    return [value, reread, failed];
}

// ── the tournament ──────────────────────────────────────────────────────

// The Qt "Set tournament URL" dialog: the URL set last, re-fetched even when
// it is the loaded one (force; no icon / banner download, as the Qt dialog).
// It stays open while the set resolves and loads, saying how it goes (the
// Event page's words), and closes once it has loaded; a refusal or a failed
// load is said in it.
function TournamentDialog({url, load, onClose, report}: {
    url: string;
    load: TournamentLoad;
    onClose: () => void;
    report: Report;
}) {
    const [shown, type] = useUrlField(url);
    const [requesting, setRequesting] = React.useState(false);
    // The revision of tournamentInfo.load this dialog's set was published
    // at (the 202's loadRevision), and whether the state has not reached it
    // yet (at most WRITE_TIMEOUT_MS).
    const [sent, setSent] = React.useState<number | null>(null);
    const [awaiting, setAwaiting] = React.useState(false);
    // The state did not reach `sent` within WRITE_TIMEOUT_MS: said, as the
    // load's own words never came.
    const [late, setLate] = React.useState(false);
    // A URL typed again: what was said of the last one goes.
    const [retyped, setRetyped] = React.useState(false);
    const [refused, setRefused] = React.useState<string | null>(null);
    const mounted = React.useRef(true);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    React.useEffect(() => {
        if (sent === null || !awaiting) {
            return undefined;
        }
        if (load.revision >= sent) {
            setAwaiting(false);
            return undefined;
        }
        const timer = setTimeout(() => {
            setAwaiting(false);
            setLate(true);
        }, WRITE_TIMEOUT_MS);
        return () => clearTimeout(timer);
    }, [sent, awaiting, load.revision]);

    const loaded = sent !== null && load.revision === sent
        && load.status === "loaded";
    React.useEffect(() => {
        if (loaded) {
            onClose();
        }
    }, [loaded, onClose]);

    const busy = requesting || awaiting || BUSY_STATUSES.has(load.status);
    const typed = shown.trim();
    const reached = sent !== null && load.revision >= sent;
    const told = retyped ? ""
        : reached ? statusText(load)
            : late ? i18n.t("app_tournament_no_word") : "";

    const submit = () => {
        if (busy || !typed) {
            return;
        }
        setRefused(null);
        setRetyped(false);
        setLate(false);
        setRequesting(true);
        apiPost("/api/v1/tournament", {url: typed, force: true},
                WRITE_OPTIONS).then((answer) => {
            if (!mounted.current) {
                return;
            }
            setRequesting(false);
            const revision = (JSON.parse(answer) as
                Envelope<{loadRevision?: unknown}>).data?.loadRevision;
            if (typeof revision === "number") {
                setSent(revision);
                setAwaiting(true);
            } else {
                onClose();
            }
        }, (error: unknown) => {
            if (!mounted.current) {
                return;
            }
            setRequesting(false);
            const status = apiErrorStatus(error);
            if (status === 400) {
                setRefused(i18n.t("event_url_invalid"));
            } else if (status === 409) {
                setRefused(i18n.t("event_busy"));
            } else {
                report(error);
            }
        });
    };

    return (
        <Dialog open onClose={onClose} maxWidth="sm" fullWidth
                aria-labelledby="app-tournament-dialog-title">
            <DialogTitle id="app-tournament-dialog-title">
                {i18n.t("app_set_tournament_url")}
            </DialogTitle>
            <DialogContent>
                <DialogContentText sx={{mb: 2}}>
                    {i18n.t("event_tournament_note")}
                </DialogContentText>
                <TextField
                    autoFocus
                    fullWidth
                    size="small"
                    label={i18n.t("event_url")}
                    placeholder="https://www.start.gg/tournament/…/event/…"
                    value={shown}
                    onChange={(event) => {
                        setRefused(null);
                        setRetyped(true);
                        type(event.target.value);
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") {
                            event.preventDefault();
                            submit();
                        }
                    }}
                />
                {(refused || told) && (
                    <Typography variant="body2" role="status" sx={{mt: 1.5}}
                                color={refused || (reached && !retyped
                                                   && load.status === "error")
                                    ? "error" : "text.secondary"}>
                        {refused || told}
                    </Typography>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>{i18n.t("stream_cancel")}</Button>
                <Button variant="contained" disabled={busy || !typed}
                        onClick={submit}>
                    {i18n.t("app_tournament_ok")}
                </Button>
            </DialogActions>
        </Dialog>
    );
}

function TournamentControl({report}: {report: Report}) {
    const info = useSelector((root: ReduxState) =>
        (root.autonaState.autonaState as unknown as HeaderState)
            .tournamentInfo) ?? {};
    const name = text(info.tournamentName).trim();
    const url = text(info.url);
    const load = readLoad(info.load);
    const [anchor, setAnchor] = React.useState<HTMLButtonElement | null>(null);
    const [menu, setMenu] = React.useState(false);
    const [dialog, setDialog] = React.useState(false);
    const [confirming, setConfirming] = React.useState(false);
    const closeDialog = React.useCallback(() => setDialog(false), []);

    return (
        <>
            <Pulldown
                ref={setAnchor}
                icon="tournament.svg"
                text={name || i18n.t("app_set_tournament")}
                attention={!name}
                menuId="app-tournament-menu"
                open={menu}
                onClick={() => setMenu(true)}
                testId="app-tournament"
            />
            <Menu id="app-tournament-menu" anchorEl={anchor}
                  open={menu} onClose={() => setMenu(false)}>
                <MenuItem onClick={() => {
                    setMenu(false);
                    setDialog(true);
                }}>
                    {i18n.t("app_set_tournament")}
                </MenuItem>
                <MenuItem onClick={() => {
                    setMenu(false);
                    setConfirming(true);
                }}>
                    {i18n.t("app_unset_tournament")}
                </MenuItem>
            </Menu>
            {dialog && (
                <TournamentDialog url={url} load={load} onClose={closeDialog}
                                  report={report}/>
            )}
            <ConfirmDialog
                open={confirming}
                title={i18n.t("app_confirm")}
                message={i18n.t("app_unset_confirm")}
                confirm={i18n.t("event_clear_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    // Also forgets the selected game (TournamentService.
                    // ClearTournament, as every clear).
                    apiDelete("/api/v1/tournament", WRITE_OPTIONS)
                        .catch((error: unknown) => report(error));
                }}
                onClose={() => setConfirming(false)}
            />
        </>
    );
}

// ── the game ────────────────────────────────────────────────────────────

const gameRefusal: Refusal = (status) => (
    status === 409 ? i18n.t("event_game_busy") : i18n.t("request_failed"));

// The state's game, as the Event page reads it: a codename the list does
// not have (a rescan dropped it) is none. After the tournament is unset the
// desktop's selection is forgotten but the state's `game` stays (core's
// GameAssetCatalog.ForgetSelection), so the game is still named here, as on
// the Event page (the Qt button says "Select game title" then). Before the
// list is read nothing is called missing: the state's codename, or the
// loading word; the list not read, the codename stays.
function GameControl({go, report}: {
    go: (panel: string) => void;
    report: Report;
}) {
    const game = useSelector((root: ReduxState) =>
        (root.autonaState.autonaState as unknown as HeaderState).game) ?? {};
    const codename = text(game.codename);
    const [games, , failed] = useNoticedList<Record<string, GameEntry>>(
        "/api/v1/games", "games_changed", codename);
    const [anchor, setAnchor] = React.useState<HTMLButtonElement | null>(null);
    const [menu, setMenu] = React.useState(false);
    const list = games ?? {};
    const known = codename !== "" && codename in list;
    const ordered = gamesByName(list);
    // Missing: the list (read) does not have it, or there is none at all
    // and the read failed.
    const unread = games === null;
    const missing = unread ? failed && !codename : !known;
    const shown = known ? (list[codename]?.name || codename)
        : missing ? i18n.t("app_select_game")
            : codename || i18n.t("event_loading");

    // Picked: loaded with mods off, as the Qt menu does (it unchecks the
    // modded switch), even the game shown -- after the tournament was unset
    // that is how it is selected again.
    const pick = (next: string) => {
        setMenu(false);
        apiPost("/api/v1/game", {codename: next}, WRITE_OPTIONS)
            .catch((error: unknown) => report(error, gameRefusal));
    };

    return (
        <>
            <Pulldown
                ref={setAnchor}
                icon="gamepad.svg"
                text={shown}
                attention={missing}
                menuId="app-game-menu"
                open={menu}
                onClick={() => setMenu(true)}
                testId="app-game"
            />
            <Menu id="app-game-menu" anchorEl={anchor} open={menu}
                  onClose={() => setMenu(false)}>
                {ordered.map((entry) => (
                    <MenuItem key={entry} selected={entry === codename}
                              onClick={() => pick(entry)}>
                        <ListItemIcon>
                            {entry === codename
                                ? <CheckIcon fontSize="small"/> : null}
                        </ListItemIcon>
                        <ListItemText>{list[entry]?.name || entry}</ListItemText>
                    </MenuItem>
                ))}
                {ordered.length > 0 && <Divider/>}
                <MenuItem onClick={() => {
                    setMenu(false);
                    go("assets");
                }}>
                    <ListItemIcon/>
                    <ListItemText>{i18n.t("app_add_game_assets")}</ListItemText>
                </MenuItem>
            </Menu>
        </>
    );
}

// Shown only for a game that ships modded content (has_modded_content):
// the game loaded again with them on or off (POST /game, modsActive).
function ModsSwitch({report}: {report: Report}) {
    const game = useSelector((root: ReduxState) =>
        (root.autonaState.autonaState as unknown as HeaderState).game) ?? {};
    const codename = text(game.codename);
    if (game.has_modded_content !== true || !codename) {
        return null;
    }
    return <ModsControl key={codename} codename={codename}
                        value={Boolean(game.mods_active)} report={report}/>;
}

// What the switch was turned to, held until the load it started is over:
// the state's mods_active changes only once the game has loaded again, which
// takes seconds. Over: the state's value changed, the write failed, the
// asset load (StatusService's assets_load job) was seen and is gone, or none
// was seen within WRITE_TIMEOUT_MS of the answer (a loader that tells no
// job). The newest turn is the one held.
interface HeldTurn {
    value: boolean;
    id: number;
    answered: boolean;
    sawLoad: boolean;
}

function ModsControl({codename, value, report}: {
    codename: string;
    value: boolean;
    report: Report;
}) {
    const {status} = useAppStatus();
    const loading = status.jobs.some((job) => job.code === "assets_load");
    const [held, setHeld] = React.useState<HeldTurn | null>(null);
    const latest = React.useRef(0);

    React.useEffect(() => {
        setHeld(null);
    }, [value]);

    React.useEffect(() => {
        if (held === null) {
            return undefined;
        }
        if (loading) {
            if (!held.sawLoad) {
                setHeld({...held, sawLoad: true});
            }
            return undefined;
        }
        if (!held.answered) {
            return undefined;
        }
        if (held.sawLoad) {
            setHeld(null);
            return undefined;
        }
        const {id} = held;
        const timer = setTimeout(() => setHeld((current) => (
            current?.id === id ? null : current)), WRITE_TIMEOUT_MS);
        return () => clearTimeout(timer);
    }, [held, loading]);

    const choose = (next: boolean) => {
        const id = ++latest.current;
        setHeld({value: next, id, answered: false, sawLoad: false});
        apiPost("/api/v1/game", {codename, modsActive: next}, WRITE_OPTIONS)
            .then(() => {
                setHeld((current) => (current?.id === id
                    ? {...current, answered: true} : current));
            }, (error: unknown) => {
                setHeld((current) => (current?.id === id ? null : current));
                report(error, gameRefusal);
            });
    };

    return (
        <FormControlLabel
            sx={{mx: 0, minHeight: ROW_HEIGHT,
                 "& .MuiFormControlLabel-label": {fontSize: "0.8125rem"}}}
            control={<Switch size="small" checked={held ? held.value : value}
                             onChange={(event) => choose(event.target.checked)}/>}
            label={i18n.t("app_modded_content")}
        />
    );
}

// ── the battle format ───────────────────────────────────────────────────

interface BattleFormat {
    format: string;
    board: number;
}

// The format chosen here: shown until the read after its answer (or a
// newer read, a notice's) says which is in force -- at most WRITE_TIMEOUT_MS
// after the answer, should that read fail. A later choice wins: an answer to
// an older one does nothing.
interface Chosen {
    format: string;
    id: number;
    // The read when the answer came; undefined: not answered yet.
    answeredAt?: BattleFormat | null;
}

function FormatSegment({report}: {report: Report}) {
    const [read, reread] = useNoticedList<BattleFormat>(
        "/api/v1/battle-format", "battle_format", null);
    const [chosen, setChosen] = React.useState<Chosen | null>(null);
    const latest = React.useRef(0);
    const lastRead = React.useRef(read);
    lastRead.current = read;
    const shown = chosen?.format ?? read?.format ?? null;

    React.useEffect(() => {
        if (chosen === null || chosen.answeredAt === undefined) {
            return undefined;
        }
        if (read !== chosen.answeredAt) {
            setChosen(null);
            return undefined;
        }
        const {id} = chosen;
        const timer = setTimeout(() => setChosen((current) => (
            current?.id === id ? null : current)), WRITE_TIMEOUT_MS);
        return () => clearTimeout(timer);
    }, [chosen, read]);

    const choose = (format: string | null) => {
        if (!format || format === shown) {
            return;
        }
        const id = ++latest.current;
        setChosen({format, id});
        apiPut("/api/v1/battle-format", {format}, WRITE_OPTIONS).then(
            () => {
                if (latest.current !== id) {
                    return;
                }
                setChosen((current) => (current?.id === id
                    ? {...current, answeredAt: lastRead.current} : current));
                reread();
            },
            (error: unknown) => {
                if (latest.current === id) {
                    setChosen(null);
                }
                report(error);
                reread();
            });
    };

    return (
        <ToggleButtonGroup
            exclusive
            fullWidth
            size="small"
            value={shown}
            onChange={(_event, value: string | null) => choose(value)}
            aria-label={i18n.t("app_battle_format")}
            sx={{
                p: "2px",
                gap: "2px",
                borderRadius: "6px",
                bgcolor: "rgba(255, 255, 255, 0.05)",
                "& .MuiToggleButton-root": {
                    border: "none",
                    borderRadius: "4px !important",
                    minHeight: ROW_HEIGHT - 4,
                    p: 0,
                    fontSize: "0.8125rem",
                    fontWeight: 400,
                    color: "text.secondary",
                    "&:hover": {bgcolor: "rgba(255, 255, 255, 0.06)"},
                    "&.Mui-selected, &.Mui-selected:hover": {
                        bgcolor: "secondary.main",
                        color: "common.white",
                    },
                },
            }}
        >
            {FORMATS.map(([format, label]) => (
                <ToggleButton key={format} value={format}>{label}</ToggleButton>
            ))}
        </ToggleButtonGroup>
    );
}

// ── the band ────────────────────────────────────────────────────────────

export function Header({go, report, onCollapse, collapseRef}: {
    go: (panel: string) => void;
    report: Report;
    onCollapse: () => void;
    // The collapse button, for the focus once the sidebar is back.
    collapseRef?: React.Ref<HTMLButtonElement>;
}) {
    return (
        <Box sx={{display: "flex", flexDirection: "column", gap: "4px",
                  px: 1, py: "10px"}}>
            <Box sx={{display: "flex", justifyContent: "flex-end"}}>
                <IconButton size="small" onClick={onCollapse} ref={collapseRef}
                            aria-label={i18n.t("app_hide_sidebar")}
                            title={i18n.t("app_hide_sidebar")}
                            sx={{p: "5px", borderRadius: "6px"}}>
                    <Box component="img" src={iconUrl("menu.svg")} alt=""
                         sx={{width: 16, height: 16}}/>
                </IconButton>
            </Box>
            <TournamentControl report={report}/>
            <GameControl go={go} report={report}/>
            <FormatSegment report={report}/>
            <ModsSwitch report={report}/>
        </Box>
    );
}
