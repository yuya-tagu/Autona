import React from "react";
import {
    Box,
    Button,
    Card,
    CardContent,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {useStateField} from "../PanelShell/useStateField";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {IMAGE_UPLOAD_ACCEPT} from "../PanelShell/imageUpload";
import {ColorRow, ColorKind} from "../CommentaryPage/SideCards";
import {ConfirmDialog} from "../StreamPage/Dialogs";

// The Event page's four cards (ADR-005 S4g), the Qt 大会情報 panel's
// groups: the start.gg tournament, the tournament's details (the five
// fields and the game), its colours and its icon / banner. They draw the
// state (tournamentInfo.*, game) and ask the page to write.

// tournamentInfo.load: how the newest set / clear of the tournament went
// (core/TournamentService, S4g review M1). `revision` counts the sets /
// clears.
export interface TournamentLoad {
    status: "idle" | "resolving" | "loading" | "loaded" | "error";
    error: string;
    revision: number;
}

// tournamentInfo.media: the icon / banner download's last run
// (core/TournamentMediaService).
export interface TournamentMedia {
    status: "idle" | "running" | "done" | "error";
    error: string;
    revision: number;
}

// GET /api/v1/games, keyed by codename (core/GameAssetCatalog.GamesPayload).
export interface GameEntry {
    name?: string | null;
}

export type MediaKind = "icon" | "banner";

// The fields PATCH /api/v1/tournament/info takes
// (StateSchema.TOURNAMENT_INPUT_KEYS).
export interface InfoFields {
    tournamentName: string;
    eventName: string;
    address: string;
    numEntrants: number;
    startAtDate: string;
}

export const BUSY_STATUSES = new Set(["resolving", "loading"]);

function CardFrame({title, children}: {
    title: string;
    children: React.ReactNode;
}) {
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1.5}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    {children}
                </Stack>
            </CardContent>
        </Card>
    );
}

function Note({children}: {children: React.ReactNode}) {
    return (
        <Typography variant="body2" color="text.secondary">
            {children}
        </Typography>
    );
}

// ── start.gg の大会 ──────────────────────────────────────────────────────

function statusText(status: TournamentLoad): string {
    switch (status.status) {
        case "resolving":
            return i18n.t("event_status_resolving");
        case "loading":
            return i18n.t("event_status_loading");
        case "loaded":
            return i18n.t("event_status_loaded");
        case "error":
            return status.error === "unresolved"
                ? i18n.t("event_status_unresolved")
                : i18n.t("event_status_load_failed");
        default:
            return "";
    }
}

// The URL field: the state's URL (tournamentInfo.url), or what is typed
// over it (null: nothing typed). A change of the state's URL -- the page's
// own set, the sidebar's, another client's, a clear -- drops what was
// typed, focused or not (the Qt panels' HeldBackText rule: another writer
// wins). Unlike a state field (useStateField), the typing is not kept on
// screen until the field is left: the URL is sent by the buttons, and they
// send what is shown -- an old URL typed before another writer's set is
// never set over it (S4g review M3). A set that changes nothing (refused,
// an admin URL that stood for no event) leaves the typing to be corrected.
function useUrlField(url: string): [string, (text: string | null) => void] {
    const [draft, setDraft] = React.useState<string | null>(null);
    const [seen, setSeen] = React.useState(url);
    if (url !== seen) {
        setSeen(url);
        setDraft(null);
    }
    return [draft ?? url, setDraft];
}

export function TournamentCard({url, load, waiting, setTournament, clear}: {
    // tournamentInfo.url: the URL last set ("" none).
    url: string;
    load: TournamentLoad;
    // A set / clear of this page's not in the state yet.
    waiting: boolean;
    setTournament: (url: string, flags: {force?: boolean;
                                         fetchMedia?: boolean}) => void;
    clear: () => void;
}) {
    const [shown, type] = useUrlField(url);
    const [confirming, setConfirming] = React.useState(false);
    const busy = waiting || BUSY_STATUSES.has(load.status);
    const typed = shown.trim();
    const text = statusText(load);

    return (
        <CardFrame title={i18n.t("event_tournament")}>
            <Note>{i18n.t("event_tournament_note")}</Note>
            <TextField
                size="small"
                label={i18n.t("event_url")}
                placeholder="https://www.start.gg/tournament/…/event/…"
                value={shown}
                onChange={(event) => type(event.target.value)}
            />
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="contained" size="small"
                        disabled={busy || !typed}
                        onClick={() => setTournament(typed,
                                                     {fetchMedia: true})}>
                    {i18n.t("event_set")}
                </Button>
                <Button variant="outlined" size="small"
                        disabled={busy || !typed}
                        onClick={() => setTournament(typed, {force: true})}>
                    {i18n.t("event_refetch")}
                </Button>
                <Button variant="outlined" color="error" size="small"
                        disabled={waiting}
                        onClick={() => setConfirming(true)}>
                    {i18n.t("event_clear")}
                </Button>
            </Stack>
            {text && (
                <Typography variant="body2" role="status"
                            color={load.status === "error"
                                ? "error" : "text.secondary"}>
                    {text}
                </Typography>
            )}
            <ConfirmDialog
                open={confirming}
                title={i18n.t("event_clear_title")}
                message={i18n.t("event_clear_confirm")}
                confirm={i18n.t("event_clear_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    // The typing goes with the tournament (the state's URL
                    // may be "" already: no change to drop it).
                    type(null);
                    clear();
                }}
                onClose={() => setConfirming(false)}
            />
        </CardFrame>
    );
}

// ── 大会の詳細 ───────────────────────────────────────────────────────────

// The most entrants the field takes: as many as start.gg reports for an
// event (S4g review L7; the Qt panel's spin box stopped at 9999). Equal to
// AutonaApiV1.TOURNAMENT_ENTRANTS_MAX (tests/test_api_v1_tournament.py).
export const ENTRANTS_MAX = 999999;

// The entrant count as typed: a whole number from 0 to ENTRANTS_MAX. Null:
// not one (an empty field neither).
export function parseEntrants(text: string): number | null {
    const trimmed = text.trim();
    if (!/^\d+$/.test(trimmed)) {
        return null;
    }
    const count = Number(trimmed);
    return count <= ENTRANTS_MAX ? count : null;
}

// A day <input type="date"> answers ("YYYY-MM-DD") that is worth sending: a
// four-figure year from 2000 to 2999, as the field's min / max. While a
// year is typed digit by digit the input answers "0002-...", "0020-..."
// -- not sent, nor refused aloud (S4g review L1).
export function isSendableDay(value: string): boolean {
    const match = /^(\d{4})-\d{2}-\d{2}$/.exec(value);
    if (!match) {
        return false;
    }
    const year = Number(match[1]);
    return year >= 2000 && year <= 2999;
}

function gameLogo(codename: string): string {
    return `${BASE_URL}/user_data/games/${encodeURIComponent(codename)}`
        + "/base_files/logo.png";
}

export function InfoCard({info, games, game, sendField, chooseGame,
                          refuseEntrants}: {
    info: Partial<Record<keyof InfoFields, unknown>>;
    games: Record<string, GameEntry>;
    // state.game.codename: the game loaded ("" none).
    game: string;
    sendField: (fields: Partial<InfoFields>) => Promise<unknown>;
    chooseGame: (codename: string) => Promise<unknown>;
    refuseEntrants: () => void;
}) {
    const text = (key: keyof InfoFields) => {
        const value = info[key];
        return typeof value === "string" ? value : "";
    };
    const entrants = typeof info.numEntrants === "number"
        ? String(info.numEntrants) : "";

    const {pick: _n, replace: _nr, ...nameField} = useStateField(
        text("tournamentName"), React.useCallback(
            (value: string) => sendField({tournamentName: value}),
            [sendField]));
    const {pick: _e, replace: _er, ...eventField} = useStateField(
        text("eventName"), React.useCallback(
            (value: string) => sendField({eventName: value}), [sendField]));
    const {pick: _a, replace: _ar, ...addressField} = useStateField(
        text("address"), React.useCallback(
            (value: string) => sendField({address: value}), [sendField]));
    // An emptied field is not sent (S4g review L7: it used to send 0);
    // leaving it shows the state again. Anything else not a count is
    // refused aloud.
    const {pick: _c, replace: _cr, ...entrantsField} = useStateField(
        entrants, React.useCallback((value: string) => {
            if (value.trim() === "") {
                return Promise.reject(new Error("no entrant count"));
            }
            const count = parseEntrants(value);
            if (count === null) {
                refuseEntrants();
                return Promise.reject(new Error(`not an entrant count: ${value}`));
            }
            return sendField({numEntrants: count});
        }, [sendField, refuseEntrants]));
    // <input type="date"> answers "YYYY-MM-DD", or "" while the day is not
    // whole: only a day isSendableDay takes is sent (the day cannot be
    // emptied, as the Qt date edit's could not), and leaving the field
    // shows the state again.
    const {pick: _d, replace: _dr, ...dateField} = useStateField(
        text("startAtDate"), React.useCallback((value: string) => (
            isSendableDay(value)
                ? sendField({startAtDate: value})
                : Promise.reject(new Error(`not a day to send: ${value}`))),
        [sendField]));

    const [shownGame, pickGame] = useStateChoice(game, chooseGame);
    const codenames = Object.keys(games);
    // A game the list does not have (a rescan dropped it) is shown as none.
    const value = codenames.includes(shownGame) ? shownGame : "";

    return (
        <CardFrame title={i18n.t("event_info")}>
            <Note>{i18n.t("event_info_note")}</Note>
            <TextField size="small" label={i18n.t("event_tournament_name")}
                       {...nameField}/>
            <TextField size="small" label={i18n.t("event_event_name")}
                       {...eventField}/>
            <Stack direction="row" spacing={1}>
                <TextField
                    size="small"
                    label={i18n.t("event_entrants")}
                    slotProps={{htmlInput: {inputMode: "numeric"}}}
                    {...entrantsField}
                    sx={{flex: 1, minWidth: 0}}
                />
                <TextField
                    size="small"
                    type="date"
                    label={i18n.t("event_date")}
                    slotProps={{inputLabel: {shrink: true},
                                htmlInput: {min: "2000-01-01",
                                            max: "2999-12-31"}}}
                    {...dateField}
                    sx={{flex: 1, minWidth: 0}}
                />
            </Stack>
            <TextField size="small" label={i18n.t("event_address")}
                       {...addressField}/>
            <TextField
                select
                size="small"
                label={i18n.t("event_game")}
                value={value}
                onChange={(event) => {
                    if (event.target.value) {
                        pickGame(event.target.value);
                    }
                }}
                slotProps={{select: {displayEmpty: true},
                            inputLabel: {shrink: true}}}
            >
                <MenuItem value="" disabled>
                    <em>{i18n.t("event_game_none")}</em>
                </MenuItem>
                {codenames.map((codename) => (
                    <MenuItem key={codename} value={codename}>
                        <Stack direction="row" spacing={1}
                               alignItems="center">
                            <Box component="img" alt="" src={gameLogo(codename)}
                                 onError={(event: React.SyntheticEvent<HTMLImageElement>) => {
                                     event.currentTarget.style.visibility = "hidden";
                                 }}
                                 sx={{width: 24, height: 24,
                                      objectFit: "contain"}}/>
                            <span>{games[codename]?.name || codename}</span>
                        </Stack>
                    </MenuItem>
                ))}
            </TextField>
        </CardFrame>
    );
}

// ── 大会カラー ───────────────────────────────────────────────────────────

export function ColorsCard({theme, accent, send, reset}: {
    theme: string;
    accent: string;
    send: (colors: Partial<Record<ColorKind, string>>) => Promise<unknown>;
    reset: () => void;
}) {
    const sendTheme = React.useCallback(
        (color: string) => send({theme: color}), [send]);
    const sendAccent = React.useCallback(
        (color: string) => send({accent: color}), [send]);
    return (
        <CardFrame title={i18n.t("event_colors")}>
            <Note>{i18n.t("event_colors_note")}</Note>
            <ColorRow label={i18n.t("event_color_theme")} value={theme}
                      send={sendTheme}/>
            <ColorRow label={i18n.t("event_color_accent")} value={accent}
                      send={sendAccent}/>
            <Box>
                <Button variant="outlined" size="small" onClick={reset}>
                    {i18n.t("event_colors_reset")}
                </Button>
            </Box>
        </CardFrame>
    );
}

// ── 大会アイコン / バナー ───────────────────────────────────────────────

// Where the previews come from. The icon is layout/logo.png (Autona's own
// until one is imported: TournamentMediaService.ResetMedia writes it), the
// banner user_data/tournament_banner.png; with no banner (version 0) its
// readers use the bundled default banner, shown as such. `?v=` is the
// version core publishes (the file's mtime), so a new image is fetched.
export function iconUrl(version: number): string {
    return `${BASE_URL}/layout/logo.png?v=${version}`;
}

export function bannerUrl(version: number): string {
    return version > 0
        ? `${BASE_URL}/user_data/tournament_banner.png?v=${version}`
        : `${BASE_URL}/assets/thumbnail_base/default_banner.png`;
}

const MEDIA_ERRORS = new Set(["no_media", "fetch_failed", "save_failed",
                              "superseded", "cancelled"]);

function mediaText(media: TournamentMedia): string {
    switch (media.status) {
        case "running":
            return i18n.t("event_media_running");
        case "done":
            return i18n.t("event_media_done");
        case "error":
            return MEDIA_ERRORS.has(media.error)
                ? i18n.t(`event_media_${media.error}`)
                : i18n.t("event_media_error", {error: media.error});
        default:
            return "";
    }
}

function MediaRow({kind, label, src, caption, width, height, upload}: {
    kind: MediaKind;
    label: string;
    src: string;
    caption?: string;
    width: number;
    height: number;
    upload: (kind: MediaKind, file: File) => void;
}) {
    const input = React.useRef<HTMLInputElement>(null);
    return (
        <Stack direction="row" spacing={2} alignItems="center">
            <Box
                sx={{width, height, flexShrink: 0, display: "flex",
                     alignItems: "center", justifyContent: "center",
                     border: 1, borderColor: "divider", borderRadius: 1,
                     overflow: "hidden"}}
            >
                <Box component="img" alt={label} src={src}
                     sx={{maxWidth: "100%", maxHeight: "100%"}}/>
            </Box>
            <Stack spacing={0.5} sx={{minWidth: 0}}>
                <Typography variant="body2">{label}</Typography>
                {caption && (
                    <Typography variant="caption" color="text.secondary">
                        {caption}
                    </Typography>
                )}
                <Box>
                    <Button variant="outlined" size="small"
                            aria-label={`${label}: ${i18n.t("event_media_choose")}`}
                            onClick={() => input.current?.click()}>
                        {i18n.t("event_media_choose")}
                    </Button>
                </Box>
                <input
                    ref={input}
                    type="file"
                    accept={IMAGE_UPLOAD_ACCEPT}
                    hidden
                    data-testid={`event-media-${kind}-file`}
                    onChange={(event) => {
                        const chosen = event.target.files?.[0];
                        // The same file may be chosen again later.
                        event.target.value = "";
                        if (chosen) {
                            upload(kind, chosen);
                        }
                    }}
                />
            </Stack>
        </Stack>
    );
}

export function MediaCard({logoVersion, bannerVersion, media, upload, fetch,
                           reset}: {
    logoVersion: number;
    bannerVersion: number;
    media: TournamentMedia;
    upload: (kind: MediaKind, file: File) => void;
    fetch: () => void;
    reset: () => void;
}) {
    const [confirming, setConfirming] = React.useState(false);
    const running = media.status === "running";
    const text = mediaText(media);
    return (
        <CardFrame title={i18n.t("event_media")}>
            <Note>{i18n.t("event_media_note")}</Note>
            <MediaRow kind="icon" label={i18n.t("event_media_icon")}
                      src={iconUrl(logoVersion)} width={60} height={60}
                      upload={upload}/>
            <MediaRow kind="banner" label={i18n.t("event_media_banner")}
                      src={bannerUrl(bannerVersion)}
                      caption={bannerVersion > 0
                          ? undefined : i18n.t("event_media_default_banner")}
                      width={160} height={90} upload={upload}/>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="outlined" size="small" disabled={running}
                        onClick={fetch}>
                    {i18n.t("event_media_fetch")}
                </Button>
                <Button variant="outlined" color="error" size="small"
                        disabled={running}
                        onClick={() => setConfirming(true)}>
                    {i18n.t("event_media_reset")}
                </Button>
            </Stack>
            {text && (
                <Typography variant="body2" role="status"
                            color={media.status === "error"
                                ? "error" : "text.secondary"}>
                    {text}
                </Typography>
            )}
            <ConfirmDialog
                open={confirming}
                title={i18n.t("event_media_reset")}
                message={i18n.t("event_media_reset_confirm")}
                confirm={i18n.t("event_media_reset_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    reset();
                }}
                onClose={() => setConfirming(false)}
            />
        </CardFrame>
    );
}
