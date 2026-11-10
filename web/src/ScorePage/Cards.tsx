import React from "react";
import {
    Alert,
    Autocomplete,
    Box,
    Button,
    Card,
    CardContent,
    Checkbox,
    Chip,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    FormControlLabel,
    IconButton,
    MenuItem,
    Stack,
    TextField,
    Typography,
    createFilterOptions,
} from "@mui/material";
import {
    ArrowDownward,
    ArrowUpward,
    RadioButtonChecked,
    RadioButtonUnchecked,
} from "@mui/icons-material";
import i18n from "../i18n/config";
import {AutonaPlayerDbEntry} from "../backendDataTypes";
import {apiErrorStatus} from "../api";
import {getWithRetry} from "../PanelShell/requests";
import {useStateField} from "../PanelShell/useStateField";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {RangeField} from "../ThumbnailPage/Cards";
import {CharacterSelects, CountryEntry, CountryField} from "../Top8Page/Cards";
import {
    CharacterPick,
    Characters,
    NO_PICK,
    characterPick,
} from "../Top8Page/characterPick";
import {ColorRow} from "../CommentaryPage/SideCards";

// The SCORE page's cards (ADR-005 S4aa): the Qt SCORE panel's player cards
// with their details dialog, the team cards, the score panel and the crew
// board's team columns. They render the board as the page draws it (the
// unapplied values of a held board, else the state) and hand every edit to
// the page, which writes it through /api/v1. Every choice goes as an
// identifier -- a country or region code, a character's en_name and skin
// row, a variant's en_name -- and core builds the value (PlayerService).

export type Fields = Record<string, unknown>;

export function fields(value: unknown): Fields {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Fields : {};
}

export function text(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    return typeof value === "number" ? String(value) : "";
}

export function count(value: unknown, fallback = 0): number {
    const number = typeof value === "number" ? value : Number(value);
    return Number.isFinite(number) ? number : fallback;
}

// The keys of a team's player (or a slot's character) object, in order.
export function numbered(value: unknown): string[] {
    return Object.keys(fields(value))
        .filter((key) => /^\d+$/.test(key))
        .sort((a, b) => Number(a) - Number(b));
}

// The limits of what a field takes (S4z: PUT /players' rule, which PATCH
// .../fields checks): one line of up to 256 characters, the note up to 4096
// with its line breaks. Kept by the inputs, so a refusal is not the way the
// operator learns them.
export const LINE_MAX = 256;
export const NOTE_MAX = 4096;

type InputChange = React.ChangeEvent<HTMLInputElement>;

// PATCH .../players/{p}/fields: v1's names for the card's fields.
export interface PlayerFieldsSent {
    prefix?: string;
    gamerTag?: string;
    name?: string;
    twitter?: string;
    custom_textbox?: string;
    country_code?: string;
    state_code?: string;
}

// PATCH .../players/{p}/characters/{row}, with the codename of the game whose
// list it was picked on (409 CONFLICT when that is not the game loaded).
export interface CharacterSent {
    en_name?: string;
    skin?: number;
    variant?: string | null;
    game: string;
}

// What a player card asks the page for.
export interface PlayerActions {
    sendFields: (team: number, slot: number,
                 sent: PlayerFieldsSent) => Promise<unknown>;
    sendCharacter: (team: number, slot: number, row: number,
                    sent: CharacterSent) => Promise<unknown>;
    load: (team: number, slot: number,
           player: AutonaPlayerDbEntry) => Promise<unknown>;
    clear: (team: number, slot: number) => void;
    move: (team: number, first: number, second: number) => void;
}

// The character list of the state's game (Top8Page's useCharacters).
export interface CharacterList {
    list: Characters | null;
    failed: boolean;
    reload: () => void;
}

// A text field of the board: the state's (or the held board's) value, what is
// typed sent as useStateField sends it.
export function StateLine({label, value, send, multiline, width, placeholder}: {
    label: string;
    value: string;
    send: (value: string) => unknown;
    multiline?: boolean;
    width?: number;
    placeholder?: string;
}) {
    // The controls are for fields with suggestions.
    const {pick, replace, ...field} = useStateField(value, send);
    return (
        <TextField
            size="small"
            label={label}
            placeholder={placeholder}
            {...field}
            multiline={multiline}
            minRows={multiline ? 2 : undefined}
            slotProps={{htmlInput: {maxLength: multiline ? NOTE_MAX : LINE_MAX}}}
            sx={width ? {width, flexShrink: 0} : {flex: 1, minWidth: 0}}
        />
    );
}

const filterMatchNames = createFilterOptions<string>();

// An editable field with suggestions (the Qt board's editable combos: the
// phase, the pool and the round): a pick is sent at once, typing as any
// field's. `onOpen`: the list is about to show (read it again). The list
// shows every name until something is typed (as the Qt combo's does), then
// the ones that match.
export function MatchField({label, value, send, options, onOpen}: {
    label: string;
    value: string;
    send: (value: string) => unknown;
    options: string[];
    onOpen?: () => void;
}) {
    const {pick, replace, ...field} = useStateField(value, send);
    // As NameField's: Enter takes a name the keyboard moved to only (the
    // mouse highlights whatever it passes over); else it leaves the typing,
    // which goes as any field's. A click still picks.
    const highlightedBy = React.useRef<string | null>(null);
    const [typed, setTyped] = React.useState(false);
    return (
        <Autocomplete<string, false, true, true>
            freeSolo
            disableClearable
            openOnFocus
            size="small"
            sx={{flex: 1, minWidth: 0}}
            options={options}
            filterOptions={(names, state) => (typed
                ? filterMatchNames(names, state) : names)}
            value={null as unknown as string}
            inputValue={field.value}
            onOpen={(event) => {
                // Opened by typing (the list was closed): it stays filtered.
                if (event.type !== "change" && event.type !== "input") {
                    setTyped(false);
                }
                onOpen?.();
            }}
            onInputChange={(event, _text, reason) => {
                if (reason === "input" && event) {
                    setTyped(true);
                    field.onChange(event as InputChange);
                }
            }}
            onHighlightChange={(_event, _name, reason) => {
                highlightedBy.current = reason;
            }}
            onChange={(event, choice, reason) => {
                if (reason !== "selectOption" || typeof choice !== "string") {
                    return;
                }
                if (event.type === "keydown"
                        && highlightedBy.current !== "keyboard") {
                    return;
                }
                pick(choice);
            }}
            renderInput={(params) => (
                <TextField
                    {...params}
                    label={label}
                    // Merged: the list's own input props stay.
                    slotProps={{htmlInput: {...params.inputProps,
                                            maxLength: LINE_MAX}}}
                    onFocus={field.onFocus}
                    onBlur={field.onBlur}
                    onCompositionStart={field.onCompositionStart}
                    onCompositionEnd={field.onCompositionEnd}
                />
            )}
        />
    );
}

// A long player DB: the list shows the first matches only.
const filterPlayers = createFilterOptions<AutonaPlayerDbEntry>({
    limit: 50,
    stringify: (player) => player.prefixed_tag ?? player.gamerTag ?? "",
});

function playerLabel(player: AutonaPlayerDbEntry | string): string {
    return typeof player === "string"
        ? player
        : (player.prefixed_tag ?? player.gamerTag ?? "");
}

// What POST .../players/{p}:load takes for a candidate: its database key
// (S4z: that record exactly), else -- a row without one -- its tag (the
// first record that has it).
export function loadBody(player: AutonaPlayerDbEntry): {key: string} | {tag: string} {
    return player.prefixed_tag
        ? {key: player.prefixed_tag}
        : {tag: (player.gamerTag ?? "").trim()};
}

// The tag with the player database's suggestions: the Commentary page's
// NameField (S4b decision 16). A pick loads the player into the slot.
function NameField({label, value, send, players, onPick}: {
    label: string;
    value: string;
    send: (value: string) => unknown;
    players: AutonaPlayerDbEntry[];
    onPick: (player: AutonaPlayerDbEntry) => Promise<unknown>;
}) {
    const {pick, replace, ...field} = useStateField(value, send);
    // How the candidate Enter would take got highlighted: only one the
    // keyboard moved to is taken (the mouse highlights whatever it passes
    // over). A click still picks.
    const highlightedBy = React.useRef<string | null>(null);
    return (
        <Autocomplete<AutonaPlayerDbEntry, false, true, true>
            freeSolo
            disableClearable
            openOnFocus
            size="small"
            sx={{flex: 1, minWidth: 0}}
            options={players}
            filterOptions={filterPlayers}
            getOptionLabel={playerLabel}
            // Nothing stays selected: the field is text, a pick fills it.
            value={null as unknown as AutonaPlayerDbEntry}
            inputValue={field.value}
            onInputChange={(event, _text, reason) => {
                if (reason === "input" && event) {
                    field.onChange(event as InputChange);
                }
            }}
            onHighlightChange={(_event, _player, reason) => {
                highlightedBy.current = reason;
            }}
            onChange={(event, player, reason) => {
                // Enter on typed text ("createOption") is the IME's, not a
                // send.
                if (reason !== "selectOption" || !player
                        || typeof player === "string") {
                    return;
                }
                if (event.type === "keydown"
                        && highlightedBy.current !== "keyboard") {
                    return;
                }
                replace((player.gamerTag ?? "").trim(), onPick(player));
            }}
            renderInput={(params) => (
                <TextField
                    {...params}
                    label={label}
                    slotProps={{htmlInput: {...params.inputProps,
                                            maxLength: LINE_MAX}}}
                    onFocus={field.onFocus}
                    onBlur={field.onBlur}
                    onCompositionStart={field.onCompositionStart}
                    onCompositionEnd={field.onCompositionEnd}
                />
            )}
        />
    );
}

// ── the details dialog ──

interface RegionEntry {
    code: string;
    name: string;
}

interface VariantEntry {
    en_name: string;
    name: string;
}

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// A list the dialog reads each time it opens; null while it is read, or when
// it cannot be (`unavailable` decides which refusals mean "none"). `key`
// names what the list is of (a country's regions, a game's variants): read
// again when it changes, and a list of another key is not shown.
function useOpenRead<T>(path: string | null, key: string, open: boolean,
                        read: (data: unknown) => T,
                        unavailable: (status: number | undefined) => T | null):
        T | null {
    const [value, setValue] = React.useState<{key: string; value: T | null} | null>(null);
    React.useEffect(() => {
        if (!open || path === null) {
            return undefined;
        }
        let live = true;
        getWithRetry<Envelope<unknown>>(path, () => live).then((body) => {
            if (live) {
                setValue({key, value: read(body.data)});
            }
        }, (error) => {
            console.error(error);
            if (live) {
                setValue({key, value: unavailable(apiErrorStatus(error))});
            }
        });
        return () => {
            live = false;
        };
        // `read` / `unavailable` are the caller's constants.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [path, key, open]);
    return value && value.key === key ? value.value : null;
}

function readCountries(data: unknown): CountryEntry[] {
    return Array.isArray(data) ? data as CountryEntry[] : [];
}

// GET /countries/{code}/states: keyed by the region's code (what
// state_code takes), shown as the browser version shows it.
function readRegions(data: unknown): RegionEntry[] {
    return Object.entries(fields(data)).map(([code, value]) => {
        const name = text(fields(value).name);
        return {code, name: name ? `${name} (${code})` : code};
    });
}

// GET /variants: keyed by export name, each with the en_name `variant`
// takes and the name the Qt combo shows.
function readVariants(data: unknown): VariantEntry[] {
    return Object.values(fields(data)).map((value) => {
        const variant = fields(value);
        return {en_name: text(variant.en_name),
                name: text(variant.display_name) || text(variant.en_name)};
    }).filter((variant) => variant.en_name);
}

// Not loaded yet (NOT_READY), or a country without regions (a custom flag:
// 404): nothing to choose from.
function noList<T>(status: number | undefined): T[] | null {
    return status === 409 ? null : [];
}

function RegionField({code, regions, send}: {
    code: string;
    regions: RegionEntry[] | null;
    send: (code: string) => unknown;
}) {
    const [shown, choose] = useStateChoice(code, send);
    const label = i18n.t("score_region");
    const known = (regions ?? []).some((region) => region.code === shown);
    return (
        <TextField
            select
            size="small"
            label={label}
            disabled={regions === null || regions.length === 0}
            slotProps={{select: {displayEmpty: true},
                        inputLabel: {shrink: true}}}
            value={known ? shown : ""}
            onChange={(event) => choose(event.target.value)}
        >
            <MenuItem value="">{i18n.t("score_region_none")}</MenuItem>
            {(regions ?? []).map((region) => (
                <MenuItem key={region.code} value={region.code}>
                    {region.name}
                </MenuItem>
            ))}
        </TextField>
    );
}

function VariantField({value, variants, send, label}: {
    value: string;
    variants: VariantEntry[];
    send: (enName: string) => unknown;
    label: string;
}) {
    const [shown, choose] = useStateChoice(value, send);
    const known = variants.some((variant) => variant.en_name === shown);
    return (
        <TextField
            select
            size="small"
            label={label}
            slotProps={{select: {displayEmpty: true},
                        inputLabel: {shrink: true}}}
            value={known ? shown : ""}
            onChange={(event) => choose(event.target.value)}
            sx={{width: 160, flexShrink: 0}}
        >
            <MenuItem value="">{i18n.t("score_variant_none")}</MenuItem>
            {variants.map((variant) => (
                <MenuItem key={variant.en_name} value={variant.en_name}>
                    {variant.name}
                </MenuItem>
            ))}
        </TextField>
    );
}

function CharacterRow({row, rows, value, characters, variants, game, send}: {
    row: number;
    rows: number;
    // The slot's character[row].
    value: unknown;
    characters: Characters | null;
    variants: VariantEntry[] | null;
    game: string;
    send: (row: number, sent: CharacterSent) => Promise<unknown>;
}) {
    const pick = React.useMemo(
        () => (characters ? characterPick(value, characters) : NO_PICK),
        [value, characters]);
    const sendPick = React.useCallback(([enName, skin]: CharacterPick) => send(
        row,
        // "" empties the row; another character's first colour unless told.
        enName === null ? {en_name: "", game}
            : {en_name: enName, skin: Math.max(skin, 0), game}),
        [row, send, game]);
    const sendVariant = React.useCallback(
        (enName: string) => send(row, {variant: enName || null, game}),
        [row, send, game]);
    const variant = text(fields(fields(value).variant).en_name);
    const label = rows > 1
        ? i18n.t("score_character_row", {row}) : i18n.t("score_character");
    return (
        <Stack direction="row" spacing={1} alignItems="center">
            <Box sx={{flex: 1, minWidth: 0}}>
                <CharacterSelects
                    pick={pick}
                    characters={characters}
                    send={sendPick}
                    characterLabel={label}
                    colorLabel={i18n.t("score_color")}
                />
            </Box>
            {variants && variants.length > 0 && (
                <VariantField
                    value={variant}
                    variants={variants}
                    send={sendVariant}
                    label={i18n.t("score_variant")}
                />
            )}
        </Stack>
    );
}

export function DetailsDialog({open, onClose, team, slot, player, characters,
                               game, actions, apply}: {
    open: boolean;
    onClose: () => void;
    team: number;
    slot: number;
    player: Fields;
    characters: CharacterList;
    game: string;
    actions: PlayerActions;
    // The board's 反映 (a held board), and whether it has anything to apply.
    apply: {run: () => void; enabled: boolean} | null;
}) {
    const countries = useOpenRead<CountryEntry[]>(
        "/api/v1/countries", "", open, readCountries, noList);
    const country = text(fields(player.country).code);
    const regions = useOpenRead<RegionEntry[]>(
        country ? `/api/v1/countries/${encodeURIComponent(country)}/states`
            : null,
        country, open, readRegions, noList);
    // Read again for another game (its own list).
    const variants = useOpenRead<VariantEntry[]>(
        "/api/v1/variants", game, open, readVariants, () => []);

    const send = React.useCallback(
        (sent: PlayerFieldsSent) => actions.sendFields(team, slot, sent),
        [actions, team, slot]);
    const sendCharacter = React.useCallback(
        (row: number, sent: CharacterSent) =>
            actions.sendCharacter(team, slot, row, sent),
        [actions, team, slot]);
    const tag = text(player.name);
    const seed = count(player.seed);
    const rows = numbered(player.character);

    return (
        <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm"
                aria-labelledby={`score-details-${team}-${slot}`}>
            <DialogTitle id={`score-details-${team}-${slot}`}>
                {tag || i18n.t("score_details_title")}
            </DialogTitle>
            <DialogContent>
                <Stack spacing={1.5} sx={{pt: 1}}>
                    {seed > 0 && (
                        <Box>
                            <Chip size="small" label={i18n.t("score_seed", {seed})}/>
                        </Box>
                    )}
                    <Stack direction="row" spacing={1}>
                        <StateLine
                            label={i18n.t("score_real_name")}
                            value={text(player.real_name)}
                            send={(value) => send({name: value})}
                        />
                        <StateLine
                            label={i18n.t("score_twitter")}
                            value={text(player.twitter)}
                            send={(value) => send({twitter: value})}
                        />
                    </Stack>
                    <CountryField
                        code={country}
                        countries={countries}
                        send={(code) => send({country_code: code})}
                        label={i18n.t("score_country")}
                    />
                    <RegionField
                        code={text(fields(player.state).code)}
                        regions={country ? regions : []}
                        send={(code) => send({state_code: code})}
                    />
                    {characters.failed && (
                        <Alert
                            severity="warning"
                            action={
                                <Button color="inherit" size="small"
                                        onClick={characters.reload}>
                                    {i18n.t("top8_characters_reload")}
                                </Button>
                            }
                        >
                            {i18n.t("top8_characters_unavailable")}
                        </Alert>
                    )}
                    {rows.map((key) => (
                        <CharacterRow
                            key={key}
                            row={Number(key)}
                            rows={rows.length}
                            value={fields(player.character)[key]}
                            characters={characters.list}
                            variants={variants}
                            game={game}
                            send={sendCharacter}
                        />
                    ))}
                    <StateLine
                        label={i18n.t("score_note")}
                        value={text(player.custom_textbox)}
                        send={(value) => send({custom_textbox: value})}
                        multiline
                    />
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button color="error" onClick={() => actions.clear(team, slot)}>
                    {i18n.t("score_clear_player")}
                </Button>
                <Box sx={{flex: 1}}/>
                {apply && (
                    <Button variant="contained" disabled={!apply.enabled}
                            onClick={apply.run}>
                        {i18n.t("score_apply")}
                    </Button>
                )}
                <Button onClick={onClose}>{i18n.t("close")}</Button>
            </DialogActions>
        </Dialog>
    );
}

// ── the player card ──

export function PlayerCard({team, slot, slots, player, showSponsor, players,
                            characters, game, actions, apply}: {
    // The state team (the column), and the slot in it.
    team: number;
    slot: number;
    // How many slots the team has (the moves at its ends are disabled; with
    // one, the card has no title and no moves).
    slots: number;
    player: Fields;
    showSponsor: boolean;
    players: AutonaPlayerDbEntry[];
    characters: CharacterList;
    game: string;
    actions: PlayerActions;
    apply: {run: () => void; enabled: boolean} | null;
}) {
    const [details, setDetails] = React.useState(false);
    const send = React.useCallback(
        (sent: PlayerFieldsSent) => actions.sendFields(team, slot, sent),
        [actions, team, slot]);
    const onPick = React.useCallback(
        (picked: AutonaPlayerDbEntry) => actions.load(team, slot, picked),
        [actions, team, slot]);
    const seed = count(player.seed);

    return (
        <Card variant="outlined" component="section"
              aria-label={i18n.t("score_card", {team, slot})} sx={{p: 1}}>
            <Stack spacing={1}>
                {/* The slot's title and its moves: with one player a team
                    the Qt card has neither. */}
                {slots > 1 && (
                    <Stack direction="row" alignItems="center" spacing={0.5}>
                        <Typography variant="body2" color="text.secondary"
                                    sx={{flex: 1}}>
                            {i18n.t("score_player", {slot})}
                        </Typography>
                        <IconButton
                            size="small"
                            aria-label={i18n.t("score_move_up", {team, slot})}
                            disabled={slot <= 1}
                            onClick={() => actions.move(team, slot, slot - 1)}
                        >
                            <ArrowUpward fontSize="small"/>
                        </IconButton>
                        <IconButton
                            size="small"
                            aria-label={i18n.t("score_move_down", {team, slot})}
                            disabled={slot >= slots}
                            onClick={() => actions.move(team, slot, slot + 1)}
                        >
                            <ArrowDownward fontSize="small"/>
                        </IconButton>
                    </Stack>
                )}
                {showSponsor && (
                    <StateLine
                        label={i18n.t("score_sponsor")}
                        value={text(player.team)}
                        send={(value) => send({prefix: value})}
                    />
                )}
                <Stack direction="row" alignItems="center" spacing={1}>
                    <NameField
                        label={i18n.t("score_player_name", {team})}
                        value={text(player.name)}
                        send={(value) => send({gamerTag: value})}
                        players={players}
                        onPick={onPick}
                    />
                    {/* The Qt card's seedDisplay: shown, not edited here. */}
                    {seed > 0 && (
                        <Typography variant="body2" color="text.secondary"
                                    sx={{flexShrink: 0}}>
                            {i18n.t("score_seed", {seed})}
                        </Typography>
                    )}
                </Stack>
                <Box>
                    <Button size="small" sx={{px: 0, minWidth: 0}}
                            onClick={() => setDetails(true)}>
                        {i18n.t("score_details")}
                    </Button>
                </Box>
            </Stack>
            <DetailsDialog
                open={details}
                onClose={() => setDetails(false)}
                team={team}
                slot={slot}
                player={player}
                characters={characters}
                game={game}
                actions={actions}
                apply={apply}
            />
        </Card>
    );
}

// ── the team card (boards 1/2) ──

export function TeamCard({team, data, label, named, sendName, sendLosers,
                          children}: {
    team: number;
    data: Fields;
    // "TEAM N" over the card (the team board).
    label: boolean;
    // The team name shows (the team board, or the singles board built with
    // more than one player a team: the Qt board's rule).
    named: boolean;
    sendName: (team: number, value: string) => Promise<unknown>;
    sendLosers: (team: number, value: boolean) => Promise<unknown>;
    children: React.ReactNode;
}) {
    const [losers, chooseLosers] = useStateChoice(
        Boolean(data.losers),
        React.useCallback((value: boolean) => sendLosers(team, value),
                          [team, sendLosers]));
    return (
        <Card variant="outlined" component="section"
              aria-label={i18n.t("score_team", {team})}
              sx={{flex: 1, minWidth: 0}}>
            <CardContent>
                <Stack spacing={1}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                        {label && (
                            <Typography variant="subtitle2" component="h3">
                                {i18n.t("score_team", {team})}
                            </Typography>
                        )}
                        <Box sx={{flex: 1}}/>
                        <FormControlLabel
                            sx={{mr: 0}}
                            control={
                                <Checkbox
                                    size="small"
                                    checked={losers}
                                    onChange={(event) =>
                                        chooseLosers(event.target.checked)}
                                    slotProps={{input: {"aria-label":
                                        i18n.t("score_losers_of", {team})}}}
                                />
                            }
                            label={i18n.t("score_losers")}
                        />
                    </Stack>
                    {named && (
                        <StateLine
                            label={i18n.t("score_team_name")}
                            value={text(data.teamName)}
                            send={(value) => sendName(team, value)}
                        />
                    )}
                    {children}
                </Stack>
            </CardContent>
        </Card>
    );
}

// ── the score panel ──

export const SCORE_RANGE: [number, number] = [0, 99];

export function ScorePanel({scores, adjust, set, reset, swap}: {
    scores: [number, number];
    adjust: (team: number, delta: number) => void;
    set: (team: number, value: number) => Promise<unknown>;
    reset: () => void;
    swap: () => void;
}) {
    return (
        <Card variant="outlined" component="section"
              aria-label={i18n.t("score_scores")}>
            <CardContent>
                <Stack spacing={1}>
                    <Stack direction="row" spacing={1}>
                        {[1, 2].map((team) => (
                            <Stack key={team} spacing={1} alignItems="stretch"
                                   sx={{flex: 1}}>
                                <Button variant="outlined"
                                        aria-label={i18n.t("score_up", {team})}
                                        onClick={() => adjust(team, 1)}>
                                    +1
                                </Button>
                                <Box sx={{display: "flex", justifyContent: "center"}}>
                                    <RangeField
                                        label={i18n.t("score_score_of", {team})}
                                        value={scores[team - 1]}
                                        range={SCORE_RANGE}
                                        percent={false}
                                        step={1}
                                        width={120}
                                        send={(value) => set(team, value)}
                                    />
                                </Box>
                                <Button variant="outlined"
                                        aria-label={i18n.t("score_down", {team})}
                                        onClick={() => adjust(team, -1)}>
                                    -1
                                </Button>
                            </Stack>
                        ))}
                    </Stack>
                    <Button variant="outlined" onClick={reset}>
                        {i18n.t("score_score_reset")}
                    </Button>
                    <Button variant="outlined" onClick={swap}>
                        {i18n.t("score_swap")}
                    </Button>
                </Stack>
            </CardContent>
        </Card>
    );
}

// ── the crew board (board 3) ──

export const MEMBER_RANGE: [number, number] = [1, 9];
export const STOCK_RANGE: [number, number] = [0, 99];

// PATCH .../teams/{t}/members/{m}.
export interface MemberSent {
    name?: string;
    stock?: number;
    active?: boolean;
}

export function CrewTeamCard({team, data, members, sendName, sendColor,
                              sendMember}: {
    team: number;
    data: Fields;
    // How many members both crews show (score.3.team.1.memberNum).
    members: number;
    sendName: (team: number, value: string) => Promise<unknown>;
    sendColor: (team: number, color: string) => Promise<unknown>;
    sendMember: (team: number, member: number,
                 sent: MemberSent) => Promise<unknown>;
}) {
    const active = count(data.activeMember);
    const rows = fields(data.player);
    return (
        <Card variant="outlined" component="section"
              aria-label={i18n.t("score_team", {team})}
              sx={{flex: 1, minWidth: 0}}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle2" component="h3">
                        {i18n.t("score_team", {team})}
                    </Typography>
                    <StateLine
                        label={i18n.t("score_team_name")}
                        placeholder={i18n.t("score_team", {team})}
                        value={text(data.teamName)}
                        send={(value) => sendName(team, value)}
                    />
                    <ColorRow
                        label={i18n.t("score_team_color_of", {team})}
                        value={text(data.color)}
                        send={(color) => sendColor(team, color)}
                    />
                    {Array.from({length: Math.min(members, MEMBER_RANGE[1])},
                                (_value, index) => index + 1).map((member) => {
                        const row = fields(rows[String(member)]);
                        const playing = active === member;
                        return (
                            <Stack key={member} direction="row" spacing={1}
                                   alignItems="center">
                                <Checkbox
                                    icon={<RadioButtonUnchecked/>}
                                    checkedIcon={<RadioButtonChecked/>}
                                    checked={playing}
                                    onChange={(event) => sendMember(
                                        team, member,
                                        {active: event.target.checked})
                                        .catch(() => undefined)}
                                    slotProps={{input: {"aria-label": i18n.t(
                                        "score_playing", {team, member})}}}
                                    sx={{color: "warning.main",
                                         "&.Mui-checked": {color: "warning.main"}}}
                                />
                                <StateLine
                                    label={i18n.t("score_member", {member})}
                                    value={text(row.name)}
                                    send={(value) => sendMember(
                                        team, member, {name: value})}
                                />
                                <RangeField
                                    label={i18n.t("score_stock")}
                                    value={count(row.stock, 3)}
                                    range={STOCK_RANGE}
                                    percent={false}
                                    step={1}
                                    width={88}
                                    send={(value) => sendMember(
                                        team, member, {stock: value})}
                                />
                            </Stack>
                        );
                    })}
                    <Stack direction="row" justifyContent="space-between">
                        <Typography variant="body2">{i18n.t("score_total")}</Typography>
                        <Typography variant="h6" component="p"
                                    aria-label={i18n.t("score_total_of", {team})}>
                            {count(data.score)}
                        </Typography>
                    </Stack>
                </Stack>
            </CardContent>
        </Card>
    );
}

// ── the X post's refusal ──

const MISSING = ["team1", "team2", "round"];

export function PostMissingDialog({missing, onClose}: {
    // GET .../post's `missing`; null: closed.
    missing: string[] | null;
    onClose: () => void;
}) {
    return (
        <Dialog open={missing !== null} onClose={onClose}
                aria-labelledby="score-post-missing">
            <DialogTitle id="score-post-missing">
                {i18n.t("score_post_missing_title")}
            </DialogTitle>
            <DialogContent>
                <Typography gutterBottom>{i18n.t("score_post_missing")}</Typography>
                <Box component="ul" sx={{m: 0, pl: 3}}>
                    {(missing ?? []).map((item) => (
                        <li key={item}>
                            {MISSING.includes(item)
                                ? i18n.t(`score_post_missing_${item}`) : item}
                        </li>
                    ))}
                </Box>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>{i18n.t("close")}</Button>
            </DialogActions>
        </Dialog>
    );
}
