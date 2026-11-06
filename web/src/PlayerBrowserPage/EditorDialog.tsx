import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    IconButton,
    Stack,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {CountrySelector as CountryField} from "../CountrySelector";
import {CountryStateSelector as RegionField} from "../CountryStateSelector";
import {CharSelector as CharacterField} from "../ScoreboardPage/CharSelector";
import {
    CharacterRow,
    Draft,
    Main,
    characterOf,
    characterRows,
    draftOf,
    mainsOf,
    pickable,
    recordKey,
    refusedMains,
    saveBody,
    withBlankRows,
    PlayerRow,
} from "./playerRows";

// The Player List's editor (ADR-005 S4n): a form over a draft of the page's
// own. Nothing reaches the state or the overlays while it is open (the Qt
// editor mirrored every field into player_editor.*); only Save writes, to
// the player DB.

// The JavaScript fields: their JSDoc does not describe them to TypeScript.
const CountrySelector = CountryField as unknown as React.ComponentType<any>;
const CountryStateSelector = RegionField as unknown as React.ComponentType<any>;
const CharSelector = CharacterField as unknown as React.ComponentType<any>;

interface CharacterChoice {
    en_name?: string;
    display_name?: string;
    skins?: unknown;
}

function skinCount(entry: CharacterChoice): number {
    return Object.keys((entry.skins ?? {}) as object).length;
}

// What the character field answers: the list's entry, or what was typed
// (the field is freeSolo), which counts when it names one.
function chosenCharacter(value: unknown,
                         characters: Record<string, CharacterChoice>):
    CharacterChoice | null {
    if (typeof value !== "string") {
        return value as CharacterChoice | null;
    }
    const typed = value.trim().toLocaleLowerCase();
    return Object.values(characters).find((entry) =>
        entry.en_name?.toLocaleLowerCase() === typed
        || entry.display_name?.toLocaleLowerCase() === typed) ?? null;
}

// What a row holds once `value` was picked in its field, or typed and left
// there. Nothing, spaces included: the row is blank, in its place. A name that
// is no character stays in the field, in error.
function picked(row: CharacterRow, value: unknown,
                characters: Record<string, CharacterChoice>): CharacterRow {
    if (value === null || (typeof value === "string" && value.trim() === "")) {
        return {...row, main: null, emptied: row.main ?? row.emptied,
                typed: null, unknown: false};
    }
    const entry = chosenCharacter(value, characters);
    if (!entry || typeof entry.en_name !== "string") {
        return {...row, typed: typeof value === "string" ? value : row.typed,
                unknown: true};
    }
    // Its own name, spelled another way or typed again after emptying the
    // field, keeps its colour and its variant. Another character starts from
    // its first colour, with no variant: those were the other one's.
    const enName = entry.en_name;
    const own = [row.main, row.emptied].find((main) => main?.[0] === enName);
    return {...row, main: own ?? [enName, skinCount(entry) > 0 ? 0 : -1, ""],
            emptied: null, typed: null, unknown: false};
}

// The characters: those the picker can show in it, the others by name, and
// blank pickers up to MAX_NEW_CHARACTERS. A row's field shows what the row says
// (CharacterRow), so it never shows other than what will be saved without
// saying so. Saving skips the blank rows; × takes a row out, and those under it
// move up with what is in their fields.
function Characters({rows, characters, onRows}: {
    rows: CharacterRow[];
    characters: Record<string, CharacterChoice>;
    onRows: React.Dispatch<React.SetStateAction<CharacterRow[]>>;
}) {
    const change = (id: number, step: (row: CharacterRow) => CharacterRow) => {
        onRows((current) => current.map((row) => (row.id === id ? step(row) : row)));
    };

    return (
        <Stack spacing={1}>
            {rows.map((row, index) => {
                const {id, main} = row;
                const label = i18n.t("players_character_row", {n: index + 1});
                const entry = main ? characterOf(characters, main[0]) : undefined;
                const inPicker = !main || row.typed !== null
                    || pickable(main, index, characters);
                return (
                    <Stack key={id} direction="row" spacing={1}
                           alignItems="center" role="group" aria-label={label}>
                        <Box sx={{flex: 1, minWidth: 0}}>
                            {inPicker ? (
                                <CharSelector
                                    id={`player-editor-main-${id}`}
                                    charName={main ? main[0] : ""}
                                    charSkin={main && entry && skinCount(entry) > 0
                                        ? main[1] : ""}
                                    onCharNameChanged={(_event: unknown,
                                                        value: unknown) =>
                                        change(id, (current) =>
                                            picked(current, value, characters))}
                                    onCharSkinChanged={(event: {target:
                                        {value: unknown}}) => {
                                        // A character's other colour keeps its
                                        // variant.
                                        const skin = Number(event.target.value);
                                        change(id, (current) => (current.main
                                            ? {...current,
                                               main: [current.main[0], skin,
                                                      current.main[2]]}
                                            : current));
                                    }}
                                    stackProps={{direction: "row", spacing: 1}}
                                    nameText={row.typed ?? (main
                                        ? entry?.display_name ?? main[0] : "")}
                                    onNameText={(text: string, reason: string) => {
                                        // "reset": the field's own idea of its
                                        // text after its value changed, which
                                        // would replace what was typed when the
                                        // list is sent again.
                                        if (reason !== "reset") {
                                            change(id, (current) => ({
                                                ...current, typed: text,
                                                unknown: false}));
                                        }
                                    }}
                                    onNameLeft={() => change(id, (current) =>
                                        (current.typed === null ? current
                                            : picked(current, current.typed,
                                                     characters)))}
                                    nameInvalid={row.unknown}
                                    compactSkin
                                />
                            ) : (
                                <Typography variant="body2">
                                    {i18n.t("players_character_kept", {
                                        name: entry?.display_name ?? main[0],
                                        color: main[1],
                                    })}
                                </Typography>
                            )}
                            {main && main[2] && (
                                <Typography variant="caption"
                                            color="text.secondary">
                                    {i18n.t("players_character_variant",
                                            {name: main[2]})}
                                </Typography>
                            )}
                        </Box>
                        <Box sx={{width: 40, flexShrink: 0}}>
                            {main && (
                                <Tooltip
                                    title={i18n.t("players_character_remove")}>
                                    <IconButton
                                        aria-label={i18n.t(
                                            "players_character_remove")}
                                        onClick={() => onRows((current) =>
                                            withBlankRows(current.filter(
                                                (other) => other.id !== id)))}
                                    >
                                        <CloseIcon/>
                                    </IconButton>
                                </Tooltip>
                            )}
                        </Box>
                    </Stack>
                );
            })}
        </Stack>
    );
}

export function EditorDialog({
    original,
    title,
    game,
    gameChanged,
    countries,
    registered,
    variants,
    busy,
    onSave,
    onDelete,
    onClose,
}: {
    // The row it was opened on; EMPTY_ROW for a new player, or an entrant's
    // row with just the tag when the DB does not have it.
    original: PlayerRow;
    title: string;
    // The game `original` was read with: what its mains are of. Null: none
    // is selected, and a player's characters cannot be set.
    game: string | null;
    // The game is not that one any more. The mains are not sent to another
    // game, so what would send them cannot be saved; the rest still can.
    gameChanged: boolean;
    // The country rows by code, or undefined while the server has none.
    countries: Record<string, unknown> | undefined;
    // The DB's keys, each with its mains in the game read: whether what is
    // typed is a record already, and what it has.
    registered: ReadonlyMap<string, Main[]>;
    // The game's variants by en_name; null while they are not read.
    variants: ReadonlySet<string> | null;
    busy: boolean;
    onSave: (draft: Draft) => void;
    onDelete: (key: string) => void;
    onClose: () => void;
}) {
    const characters = useSelector((state: ReduxState) =>
        state.autonaCharacters.characters) as unknown as
        Record<string, CharacterChoice>;
    const [fields, setDraft] = React.useState<Draft>(() => draftOf(original));
    const [rows, setRows] = React.useState<CharacterRow[]>(
        () => characterRows(original.mains));
    const draft: Draft = {...fields, mains: mainsOf(rows)};

    // The text in the fields was typed against the list they are gone with.
    // Coming back, they show what the rows hold.
    React.useEffect(() => {
        if (gameChanged) {
            setRows((current) => current.map((row) => ({
                ...row, typed: null, unknown: false})));
        }
    }, [gameChanged]);

    const change = (field: keyof Draft) =>
        (event: React.ChangeEvent<HTMLInputElement>) => {
            setDraft((current) => ({...current, [field]: event.target.value}));
        };

    const typedKey = recordKey(draft.prefix, draft.gamerTag);
    const exists = registered.has(typedKey);
    const hasTag = draft.gamerTag.trim() !== "";
    const sentMains = saveBody(draft, original, game).mains;
    const refused = sentMains && !gameChanged
        ? refusedMains(sentMains, registered.get(typedKey) ?? [], characters,
                       variants)
        : [];
    const refusedNames = refused.map((main) => {
        const shown = i18n.t("players_character_kept", {
            name: characterOf(characters, main[0])?.display_name ?? main[0],
            color: main[1],
        });
        return main[2]
            ? `${shown} ${i18n.t("players_character_variant", {name: main[2]})}`
            : shown;
    }).join(", ");
    const unknown = rows.some((row) => row.unknown);
    const canSave = hasTag && (gameChanged
        ? sentMains === undefined
        : !unknown && refused.length === 0);

    return (
        // No onClose: neither a click beside the form nor Esc loses what was
        // typed. Only the Close button does.
        <Dialog open maxWidth="sm" fullWidth>
            <DialogTitle>{title}</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{pt: 1}}>
                    <Stack direction={{xs: "column", sm: "row"}} spacing={1}>
                        <TextField
                            size="small"
                            label={i18n.t("players_sponsor")}
                            value={draft.prefix}
                            onChange={change("prefix")}
                            sx={{flex: 1}}
                        />
                        <TextField
                            size="small"
                            required
                            label={i18n.t("players_gamertag")}
                            value={draft.gamerTag}
                            onChange={change("gamerTag")}
                            sx={{flex: 2}}
                        />
                    </Stack>
                    <Stack direction={{xs: "column", sm: "row"}} spacing={1}>
                        <TextField
                            size="small"
                            label={i18n.t("players_real_name")}
                            value={draft.name}
                            onChange={change("name")}
                            sx={{flex: 1}}
                        />
                        <TextField
                            size="small"
                            label={i18n.t("players_twitter")}
                            value={draft.twitter}
                            onChange={change("twitter")}
                            sx={{flex: 1}}
                        />
                    </Stack>
                    <Stack direction={{xs: "column", sm: "row"}} spacing={1}>
                        <CountrySelector
                            countries={countries}
                            value={draft.country_code}
                            onChange={(code: string) => {
                                // A region's code means another place in
                                // another country.
                                setDraft((current) => ({
                                    ...current,
                                    country_code: code,
                                    state_code: code === current.country_code
                                        ? current.state_code : "",
                                }));
                            }}
                            sx={{flex: 1}}
                        />
                        <CountryStateSelector
                            countryCode={draft.country_code}
                            value={draft.state_code}
                            onChange={(code: string) => {
                                setDraft((current) => ({...current,
                                                         state_code: code}));
                            }}
                            sx={{flex: 1}}
                        />
                    </Stack>
                    {gameChanged ? (
                        <Alert severity="warning">
                            {i18n.t("players_game_changed")}
                        </Alert>
                    ) : game === null ? (
                        <Typography variant="body2" color="text.secondary">
                            {i18n.t("players_no_game")}
                        </Typography>
                    ) : (
                        <Characters
                            rows={rows}
                            characters={characters}
                            onRows={setRows}
                        />
                    )}
                    {!gameChanged && unknown && (
                        <Alert severity="warning">
                            {i18n.t("players_character_unknown")}
                        </Alert>
                    )}
                    {refused.length > 0 && (
                        <Alert severity="warning">
                            {i18n.t("players_save_characters_refused",
                                    {names: refusedNames})}
                        </Alert>
                    )}
                    <TextField
                        size="small"
                        multiline
                        minRows={2}
                        label={i18n.t("players_additional")}
                        value={draft.custom_textbox}
                        onChange={change("custom_textbox")}
                    />
                </Stack>
            </DialogContent>
            <DialogActions>
                {/* The negative action leftmost, as in the tables' rows. */}
                <Button
                    color="error"
                    disabled={busy || !hasTag || !exists}
                    onClick={() => onDelete(typedKey)}
                    sx={{mr: "auto"}}
                >
                    {i18n.t("players_delete_entry")}
                </Button>
                <Button onClick={onClose}>{i18n.t("players_close")}</Button>
                <Button
                    variant="contained"
                    disabled={busy || !canSave}
                    onClick={() => onSave(draft)}
                >
                    {i18n.t(exists ? "players_save_update" : "players_save_new")}
                </Button>
            </DialogActions>
        </Dialog>
    );
}
