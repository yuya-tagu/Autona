import React from "react";
import {
    Autocomplete,
    Box,
    Button,
    Card,
    CardContent,
    InputAdornment,
    ListItemIcon,
    MenuItem,
    Stack,
    TextField,
    Typography,
    createFilterOptions,
} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL, inlineFlagWidth} from "../env";
import {useStateField} from "../PanelShell/useStateField";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {usePanelShell} from "../PanelShell/context";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {
    CharacterEntry,
    CharacterPick,
    Characters,
    NO_PICK,
    characterPick,
} from "./characterPick";

// The Top 8 page's cards (ADR-005 S4i): the eight players (the Qt panel's
// player cards) and the score table. Every choice goes to core as an
// identifier -- a country code, a character's en_name and skin row -- and
// core builds the value (Top8Service); the cards render top8.*.

// top8.players.{slot} (core/Top8Model.DefaultPlayer).
export interface Top8Player {
    name?: unknown;
    sponsor?: unknown;
    country?: {code?: unknown} | null;
    characters?: Record<string, unknown> | null;
}

// top8.matches.{id} (core/Top8Model.DefaultMatch).
export interface Top8Match {
    name?: unknown;
    slots?: unknown;
    score?: unknown;
    finished?: unknown;
    active?: unknown;
}

// GET /api/v1/countries: the country combo's rows, the custom flags last.
export interface CountryEntry {
    code: string;
    label: string;
    asset?: string;
    custom?: boolean;
}

// PATCH /api/v1/top8/players/<slot>: some of a player's fields. A
// character goes with the codename of the game whose list it was picked
// on: the server refuses it (409 CONFLICT, nothing written) when that is
// not the game loaded any more.
export interface PlayerFields {
    name?: string;
    sponsor?: string;
    country?: string | null;
    character?: {en_name: string; skin: number; game: string} | null;
}

// core/Top8Model.py's constants.
export const PLAYER_SLOTS = ["1", "2", "3", "4", "5", "6", "7", "8"];
export const SLOT_POSITIONS: Record<string, string> = {
    "1": "WSF1", "2": "WSF1",
    "3": "WSF2", "4": "WSF2",
    "5": "LR1A", "6": "LR1A",
    "7": "LR1B", "8": "LR1B",
};
export const DEFAULT_PHASE_LABEL = "TOP 8";
export const SCORE_MAX = 99;
export const DEFAULT_ROUND_NAMES: Record<string, string> = {
    wsf1: "Winners Semi-Final",
    wsf2: "Winners Semi-Final",
    wf: "Winners Final",
    lr1a: "Losers Round 1",
    lr1b: "Losers Round 1",
    lqf1: "Losers Quarter-Final",
    lqf2: "Losers Quarter-Final",
    lsf: "Losers Semi-Final",
    lf: "Losers Final",
    gf: "Grand Final",
    gf_reset: "Grand Final Reset",
};

// The score table's sections and its compact row labels (the Qt panel's;
// the overlay shows the long names, top8.matches.{id}.name).
export const MATCH_SECTIONS: [string, string[]][] = [
    ["Winners", ["wsf1", "wsf2", "wf"]],
    ["Losers", ["lr1a", "lr1b", "lqf1", "lqf2", "lsf", "lf"]],
    ["Grand Final", ["gf", "gf_reset"]],
];
export const SHORT_MATCH_LABELS: Record<string, string> = {
    wsf1: "WSF1", wsf2: "WSF2", wf: "WF",
    lr1a: "LR1A", lr1b: "LR1B",
    lqf1: "LQF1", lqf2: "LQF2", lsf: "LSF", lf: "LF",
    gf: "GF", gf_reset: "RESET",
};

const SPONSOR_WIDTH = 96;
const SKIN_ICON = 24;

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

// A file the server serves (an asset path as the catalogs give it,
// "./assets/..."), each segment encoded: a custom flag's file is named
// after the flag, and a name may hold "#" or "?".
export function assetUrl(asset: unknown): string | null {
    if (typeof asset !== "string" || !asset) {
        return null;
    }
    const path = asset.replace(/^\.\//, "").split("/")
        .map(encodeURIComponent).join("/");
    return `${BASE_URL}/${path}`;
}

function skinIcon(entry: CharacterEntry | undefined, row: number): string | null {
    const skin = Array.isArray(entry?.skins) ? entry?.skins[row] : undefined;
    const assets = skin?.assets as Record<string, {asset?: unknown}> | undefined;
    return assetUrl(assets?.["base_files/icon"]?.asset);
}

export function StateText({label, value, send, width, readOnly}: {
    label: string;
    value: string;
    send: (value: string) => unknown;
    width?: number;
    // Not disabled: a focused field that is disabled never hears it lost the
    // focus (Chrome), and useStateField would go on taking it as focused.
    readOnly?: boolean;
}) {
    // The controls are for fields with suggestions.
    const {pick, replace, ...field} = useStateField(value, send);
    return (
        <TextField
            size="small"
            label={label}
            {...field}
            slotProps={{htmlInput: {readOnly}}}
            sx={width ? {width, flexShrink: 0} : {flex: 1, minWidth: 0}}
        />
    );
}

// ── the players ──

const filterCountries = createFilterOptions<CountryEntry>({
    stringify: (country) => country.label,
});

function Flag({asset}: {asset: unknown}) {
    const src = assetUrl(asset);
    return src ? (
        <img
            alt=""
            loading="lazy"
            src={src}
            width={inlineFlagWidth}
            onError={(event) => {
                (event.target as HTMLImageElement).style.visibility = "hidden";
            }}
        />
    ) : null;
}

function CountryField({code, countries, send}: {
    // The state's country code ("" none).
    code: string;
    // GET /api/v1/countries; null while the country data is not loaded
    // (NOT_READY): the field is disabled, showing the state's code.
    countries: CountryEntry[] | null;
    send: (code: string) => unknown;
}) {
    const [shown, choose] = useStateChoice(code, send);
    // First row wins, as core's (CountryCatalog.CountryByCode).
    const byCode = React.useMemo(() => {
        const rows = new Map<string, CountryEntry>();
        (countries ?? []).forEach((country) => {
            if (!rows.has(country.code)) {
                rows.set(country.code, country);
            }
        });
        return rows;
    }, [countries]);
    const country = byCode.get(shown)
        ?? (countries === null && shown ? {code: shown, label: shown} : null);

    return (
        <Autocomplete<CountryEntry, false, false, false>
            size="small"
            disabled={countries === null}
            options={countries ?? (country ? [country] : [])}
            filterOptions={filterCountries}
            getOptionLabel={(option) => option.label}
            isOptionEqualToValue={(option, value) => option.code === value.code}
            value={country}
            onChange={(_event, next) => choose(next?.code ?? "")}
            renderOption={(props, option) => {
                const {key, ...rest} = props as typeof props & {key: string};
                return (
                    <li key={key} {...rest}>
                        <Box sx={{display: "flex", width: inlineFlagWidth,
                                  mr: 1, flexShrink: 0}}>
                            <Flag asset={option.asset}/>
                        </Box>
                        {option.label}
                    </li>
                );
            }}
            renderInput={({InputProps, ...params}) => (
                <TextField
                    {...params}
                    label={i18n.t("top8_country")}
                    // The chosen country's flag ahead of its name.
                    slotProps={{input: {
                        ...InputProps,
                        startAdornment: country ? (
                            <InputAdornment position="start">
                                <Flag asset={country.asset}/>
                            </InputAdornment>
                        ) : undefined,
                    }}}
                />
            )}
        />
    );
}

// The character select and the colour select show one pick; one choice
// control holds it, as a key (useStateChoice compares values).
function pickKey(pick: CharacterPick): string {
    return pick[0] === null ? "" : JSON.stringify(pick);
}

function readKey(key: string): CharacterPick {
    return key ? JSON.parse(key) as CharacterPick : NO_PICK;
}

const filterCharacters = createFilterOptions<CharacterEntry>({
    stringify: (entry) => `${text(entry.name)} ${text(entry.en_name)}`,
});

// A character and its colour, as one choice: shared with the Result page,
// whose rows are told their picks by the server (S4r).
export function CharacterSelects({pick, characters, send, characterLabel,
                                  colorLabel, readOnly}: {
    // What the state has.
    pick: CharacterPick;
    // GET /api/v1/characters for the state's game; null while it is read
    // (the selects are disabled: no pick on the previous game's list).
    characters: Characters | null;
    send: (pick: CharacterPick) => unknown;
    characterLabel?: string;
    colorLabel?: string;
    readOnly?: boolean;
}) {
    const options = React.useMemo(
        () => Object.values(characters ?? {}), [characters]);
    const stateKey = characters ? pickKey(pick) : "";
    const sendKey = React.useCallback(
        (key: string) => send(readKey(key)), [send]);
    const [shownKey, choose] = useStateChoice(stateKey, sendKey);
    const [enName, row] = readKey(shownKey);
    const entry = enName === null ? undefined
        : options.find((option) => option.en_name === enName);
    const skins = Array.isArray(entry?.skins) ? entry!.skins : [];
    const loading = characters === null;

    return (
        <Stack direction="row" spacing={1}>
            <Autocomplete<CharacterEntry, false, false, false>
                size="small"
                sx={{flex: 1, minWidth: 0}}
                disabled={loading}
                readOnly={readOnly}
                options={options}
                filterOptions={filterCharacters}
                getOptionLabel={(option) => text(option.name)
                    || text(option.en_name)}
                isOptionEqualToValue={(option, other) =>
                    option.en_name === other.en_name}
                value={entry ?? null}
                onChange={(_event, next) => {
                    const name = text(next?.en_name);
                    // A character picked shows its first colour (the Qt
                    // panel's); none empties the slot.
                    choose(name ? pickKey([name, 0]) : "");
                }}
                renderOption={(props, option) => {
                    const {key, ...rest} = props as typeof props & {key: string};
                    const icon = skinIcon(option, 0);
                    return (
                        <li key={key} {...rest}>
                            <Box sx={{display: "flex", width: SKIN_ICON,
                                      height: SKIN_ICON, mr: 1,
                                      flexShrink: 0}}>
                                {icon && <img alt="" loading="lazy" src={icon}
                                              width={SKIN_ICON}
                                              height={SKIN_ICON}/>}
                            </Box>
                            {text(option.name) || text(option.en_name)}
                        </li>
                    );
                }}
                renderInput={(params) => (
                    <TextField {...params}
                               label={characterLabel
                                   ?? i18n.t("top8_character")}/>
                )}
            />
            <TextField
                select
                size="small"
                label={colorLabel ?? i18n.t("top8_color")}
                disabled={loading || !entry || skins.length === 0}
                slotProps={{select: {inputProps: {readOnly}}}}
                value={entry && row >= 0 && row < skins.length ? String(row) : ""}
                onChange={(event) => {
                    if (enName !== null) {
                        choose(pickKey([enName, Number(event.target.value)]));
                    }
                }}
                sx={{width: 104, flexShrink: 0}}
            >
                {skins.map((_skin, index) => {
                    const icon = skinIcon(entry, index);
                    return (
                        <MenuItem key={index} value={String(index)}>
                            <ListItemIcon sx={{minWidth: SKIN_ICON + 8}}>
                                {icon && <img alt="" src={icon}
                                              width={SKIN_ICON}
                                              height={SKIN_ICON}/>}
                            </ListItemIcon>
                            {index + 1}
                        </MenuItem>
                    );
                })}
            </TextField>
        </Stack>
    );
}

function CharacterFields({value, characters, send}: {
    // The state's characters["1"].
    value: unknown;
    characters: Characters | null;
    send: (pick: CharacterPick) => unknown;
}) {
    const pick = React.useMemo(
        () => (characters ? characterPick(value, characters) : NO_PICK),
        [value, characters]);
    return <CharacterSelects pick={pick} characters={characters} send={send}/>;
}

function PlayerCard({slot, player, countries, characters, game, send}: {
    slot: string;
    player: Top8Player;
    countries: CountryEntry[] | null;
    characters: Characters | null;
    // The codename of the game `characters` is the list of.
    game: string;
    send: (slot: string, fields: PlayerFields) => Promise<unknown>;
}) {
    // The shell's language: a memoized card still re-renders its labels
    // when it changes.
    usePanelShell();
    const sendName = React.useCallback(
        (value: string) => send(slot, {name: value}), [slot, send]);
    const sendSponsor = React.useCallback(
        (value: string) => send(slot, {sponsor: value}), [slot, send]);
    const sendCountry = React.useCallback(
        (code: string) => send(slot, {country: code || null}), [slot, send]);
    const sendCharacter = React.useCallback(
        ([enName, row]: CharacterPick) => send(slot, {
            // Core takes the first colour for a character without any.
            character: enName === null ? null
                : {en_name: enName, skin: Math.max(row, 0), game},
        }), [slot, send, game]);
    const country = player.country && typeof player.country === "object"
        ? text(player.country.code) : "";

    return (
        <Card
            variant="outlined"
            component="section"
            aria-label={i18n.t("top8_slot", {slot})}
            sx={{p: 1}}
        >
            <Stack spacing={1}>
                <Typography variant="body2" color="text.secondary">
                    {`${slot} · ${SLOT_POSITIONS[slot]}`}
                </Typography>
                {/* Sponsor left of the name ("[Sponsor] Name"), as the
                    scoreboard and the results show it. */}
                <Stack direction="row" spacing={1}>
                    <StateText
                        label={i18n.t("top8_sponsor")}
                        value={text(player.sponsor)}
                        send={sendSponsor}
                        width={SPONSOR_WIDTH}
                    />
                    <StateText
                        label={i18n.t("top8_name")}
                        value={text(player.name)}
                        send={sendName}
                    />
                </Stack>
                <CountryField
                    code={country}
                    countries={countries}
                    send={sendCountry}
                />
                <CharacterFields
                    value={player.characters?.["1"]}
                    characters={characters}
                    send={sendCharacter}
                />
            </Stack>
        </Card>
    );
}

const MemoPlayerCard = React.memo(PlayerCard);

export function PlayersCard({players, countries, characters, game, send,
                             notices}: {
    players: Record<string, Top8Player> | undefined;
    countries: CountryEntry[] | null;
    characters: Characters | null;
    game: string;
    send: (slot: string, fields: PlayerFields) => Promise<unknown>;
    // Why a select is disabled (a list not read), told above the cards
    // with the way to read it again.
    notices?: React.ReactNode;
}) {
    const title = i18n.t("top8_players");
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("top8_players_note")}
                    </Typography>
                    {notices}
                    {PLAYER_SLOTS.map((slot) => (
                        <MemoPlayerCard
                            key={slot}
                            slot={slot}
                            player={players?.[slot] ?? {}}
                            countries={countries}
                            characters={characters}
                            game={game}
                            send={send}
                        />
                    ))}
                </Stack>
            </CardContent>
        </Card>
    );
}

// ── the scores ──

// A score as the table stages it: what is typed (digits, at most two).
export type StagedScore = [string, string];

function score(match: Top8Match | undefined): [number, number] {
    const value = Array.isArray(match?.score) ? match!.score as unknown[] : [];
    const side = (index: number) =>
        (typeof value[index] === "number" ? value[index] as number : 0);
    return [side(0), side(1)];
}

function slotsOf(match: Top8Match | undefined): [number, number] {
    const value = Array.isArray(match?.slots) ? match!.slots as unknown[] : [];
    const side = (index: number) =>
        (typeof value[index] === "number" ? value[index] as number : 0);
    return [side(0), side(1)];
}

// Who the state puts on one side of a match: TBD for none, the slot in
// brackets for a player without a name.
function playerLabel(slot: number,
                     players: Record<string, Top8Player> | undefined): string {
    if (!slot) {
        return i18n.t("top8_tbd");
    }
    const name = text(players?.[String(slot)]?.name).trim();
    return name || `(${slot})`;
}

// The staged scores: what is typed over top8.matches, by match, until 反映
// sends it. Any change of the matches drops it all and the table shows the
// state again: an import, リセット, another client -- and the page's own
// 反映 too, whose echo changes the matches (a score typed while it was on
// its way goes with it: the state wins, as for every other change). 反映 /
// リセット answered without changing them (what was sent was the state's
// already) drop it as well (`clear`); a refused one keeps it. A player's
// edit does not: the matches did not change.
export function useStagedScores(matches: unknown): [
    Record<string, StagedScore>,
    (id: string, staged: StagedScore) => void,
    () => void,
] {
    const signature = React.useMemo(() => JSON.stringify(matches ?? null),
                                     [matches]);
    const [seen, setSeen] = React.useState(signature);
    const [staged, setStaged] = React.useState<Record<string, StagedScore>>({});
    if (signature !== seen) {
        setSeen(signature);
        setStaged({});
    }
    const stage = React.useCallback((id: string, value: StagedScore) => {
        setStaged((current) => ({...current, [id]: value}));
    }, []);
    const clear = React.useCallback(() => setStaged({}), []);
    return [staged, stage, clear];
}

// The caption as its field shows it. Core writes the default ("TOP 8") for
// an emptied caption; the field emptied here stays empty over it, the
// default its placeholder (the Qt panel's _phaseLabelCleared), until the
// state says something else -- then it shows the state again, the default
// included.
function useCaption(phaseLabel: string,
                    send: (label: string) => unknown):
        [string, (label: string) => unknown] {
    // The state's caption when this page sent an empty one, the default once
    // that landed; null: the last caption sent here was not empty.
    const [cleared, setCleared] = React.useState<string | null>(null);
    if (cleared !== null && phaseLabel !== cleared) {
        setCleared(phaseLabel === DEFAULT_PHASE_LABEL
            ? DEFAULT_PHASE_LABEL : null);
    }
    const label = React.useRef(phaseLabel);
    label.current = phaseLabel;
    const sendCaption = React.useCallback((typed: string) => {
        // Core's rule (Top8Service.SetPhaseLabel): blank is the default.
        const empty = !typed.trim();
        setCleared(empty ? label.current : null);
        const result = send(typed);
        if (empty) {
            // Not written: the field shows the state as it is.
            Promise.resolve(result).catch(() => setCleared(null));
        }
        return result;
    }, [send]);
    const shown = cleared === DEFAULT_PHASE_LABEL
        && phaseLabel === DEFAULT_PHASE_LABEL ? "" : phaseLabel;
    return [shown, sendCaption];
}

// What is typed into a score: its digits, at most two (0..99).
function digits(typed: string): string {
    return typed.replace(/\D/g, "").slice(0, 2);
}

export function scoreValue(typed: string): number {
    const value = Number(typed || "0");
    return Math.min(Math.max(Number.isFinite(value) ? value : 0, 0), SCORE_MAX);
}

// One match of the table. Memoized, and given its players' labels rather
// than the players: a typed score re-renders its own row, a player's edit
// the rows that name them -- not the eleven rows of two fields each time.
function ScoreRow({id, match, left, right, staged, stage}: {
    id: string;
    match: Top8Match | undefined;
    // Who the state puts on each side (playerLabel).
    left: string;
    right: string;
    staged: StagedScore | undefined;
    stage: (id: string, staged: StagedScore) => void;
}) {
    const published = score(match);
    const shown: StagedScore = staged
        ?? [String(published[0]), String(published[1])];
    // The reset game exists only once the losers' side won the first.
    const enabled = id !== "gf_reset" || Boolean(match?.active);
    const short = SHORT_MATCH_LABELS[id];
    const input = (side: 0 | 1) => (
        <TextField
            size="small"
            disabled={!enabled}
            value={shown[side]}
            onChange={(event) => {
                const next: StagedScore = [...shown];
                next[side] = digits(event.target.value);
                stage(id, next);
            }}
            onFocus={(event) => event.target.select()}
            slotProps={{htmlInput: {
                inputMode: "numeric",
                "aria-label": i18n.t("top8_score", {match: short,
                                                    side: side + 1}),
                style: {textAlign: "center"},
            }}}
            sx={{width: 52}}
        />
    );
    const name = (label: string, align: "right" | "left") => (
        <Typography
            variant="body2"
            noWrap
            title={label}
            sx={{textAlign: align, minWidth: 0,
                 color: enabled ? "text.primary" : "text.disabled"}}
        >
            {label}
        </Typography>
    );

    return (
        <Box
            role="group"
            aria-label={short}
            sx={{display: "grid", alignItems: "center", columnGap: 1,
                 gridTemplateColumns: "48px minmax(0, 1fr) 52px 52px minmax(0, 1fr)"}}
        >
            <Typography
                variant="body2"
                title={DEFAULT_ROUND_NAMES[id]}
                sx={{color: enabled ? "text.secondary" : "text.disabled"}}
            >
                {short}
            </Typography>
            {name(left, "right")}
            {input(0)}
            {input(1)}
            {name(right, "left")}
        </Box>
    );
}

const MemoScoreRow = React.memo(ScoreRow);

export function ScoresCard({phaseLabel, matches, players, sendCaption, apply,
                            reset}: {
    phaseLabel: string;
    matches: Record<string, Top8Match> | undefined;
    players: Record<string, Top8Player> | undefined;
    sendCaption: (label: string) => unknown;
    // 反映: the staged scores of the matches typed into, as numbers. Its
    // promise settles once answered.
    apply: (scores: Record<string, [number, number]>) => Promise<unknown>;
    reset: () => Promise<unknown>;
}) {
    const title = i18n.t("top8_scores");
    const [shownCaption, sendShownCaption] = useCaption(phaseLabel,
                                                         sendCaption);
    const {pick, replace, ...caption} = useStateField(shownCaption,
                                                      sendShownCaption);
    const [staged, stage, clear] = useStagedScores(matches);
    const [confirming, setConfirming] = React.useState(false);
    const dirty = Object.keys(staged).length > 0;

    const applyStaged = () => {
        const scores: Record<string, [number, number]> = {};
        Object.entries(staged).forEach(([id, value]) => {
            scores[id] = [scoreValue(value[0]), scoreValue(value[1])];
        });
        apply(scores).then(clear, () => undefined);
    };

    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <TextField
                        size="small"
                        label={i18n.t("top8_phase_label")}
                        placeholder={DEFAULT_PHASE_LABEL}
                        {...caption}
                    />
                    {MATCH_SECTIONS.map(([section, ids]) => (
                        <Stack key={section} spacing={0.5}>
                            <Typography variant="subtitle2" component="h3"
                                        sx={{pt: 1}}>
                                {section}
                            </Typography>
                            {ids.map((id) => {
                                const [left, right] = slotsOf(matches?.[id]);
                                return (
                                    <MemoScoreRow
                                        key={id}
                                        id={id}
                                        match={matches?.[id]}
                                        left={playerLabel(left, players)}
                                        right={playerLabel(right, players)}
                                        staged={staged[id]}
                                        stage={stage}
                                    />
                                );
                            })}
                        </Stack>
                    ))}
                    {/* Negative action left, positive right (the app's
                        button-pair convention). */}
                    <Stack direction="row" spacing={1}
                           justifyContent="flex-end" sx={{pt: 1}}>
                        <Button
                            variant="outlined"
                            color="error"
                            size="small"
                            onClick={() => setConfirming(true)}
                        >
                            {i18n.t("top8_reset")}
                        </Button>
                        <Button
                            variant="contained"
                            size="small"
                            disabled={!dirty}
                            onClick={applyStaged}
                        >
                            {i18n.t("top8_apply")}
                        </Button>
                    </Stack>
                </Stack>
            </CardContent>
            <ConfirmDialog
                open={confirming}
                title={i18n.t("top8_reset")}
                message={i18n.t("top8_reset_confirm")}
                confirm={i18n.t("top8_reset_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    reset().then(clear, () => undefined);
                }}
                onClose={() => setConfirming(false)}
            />
        </Card>
    );
}
