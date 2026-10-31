import React from "react";
import {
    Autocomplete,
    Box,
    Card,
    Checkbox,
    FormControlLabel,
    ListItemIcon,
    MenuItem,
    Stack,
    TextField,
    Typography,
    createFilterOptions,
} from "@mui/material";
import {DragIndicator} from "@mui/icons-material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {AutonaPlayerDbEntry} from "../backendDataTypes";
import {useStateField} from "../PanelShell/useStateField";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {usePanelShell} from "../PanelShell/context";

// One caster slot of the Commentary page (ADR-005 S4b): the Qt panel's
// card (removed in S4c) -- name (player DB suggestions) |
// team, role (suggestions, free text) | icon, comment | Twitter, and
// "show on stream". The card edits slot `slot`; a drag moves the casters
// between slots, so the card at a position always edits that position.

// commentary.{slot} (core/CommentaryModel.DefaultSlot).
export interface Caster {
    name: string;
    team: string;
    twitter: string;
    role: string;
    icon: string;
    note: string;
    display: number;
}

export const EMPTY_CASTER: Caster = {
    name: "", team: "", twitter: "", role: "", icon: "", note: "", display: 0,
};

// GET /api/v1/commentary/options (core's constants).
export interface CommentaryOptions {
    roles: string[];
    icons: {id: string; label: string}[];
    slots: string[];
    maxDisplay: number;
}

export type CasterFields = Partial<Omit<Caster, "display">> & {display?: boolean};

export interface CardDrag {
    onGripStart: (slot: string, event: React.DragEvent<HTMLElement>) => void;
    onEnd: () => void;
    onOver: (index: number, event: React.DragEvent<HTMLElement>) => void;
    onDrop: (index: number, event: React.DragEvent<HTMLElement>) => void;
}

interface CasterCardProps {
    slot: string;
    index: number;
    caster: Caster;
    options: CommentaryOptions;
    players: AutonaPlayerDbEntry[];
    // MAX_DISPLAY casters are shown: an unshown one cannot be.
    displayLocked: boolean;
    sendFields: (slot: string, fields: CasterFields) => Promise<unknown>;
    pickPlayer: (slot: string, player: AutonaPlayerDbEntry) => Promise<unknown>;
    drag: CardDrag;
}

const NARROW = 128;

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

type InputChange = React.ChangeEvent<HTMLInputElement>;

function useFieldSender(
    slot: string,
    field: keyof Caster,
    sendFields: CasterCardProps["sendFields"],
) {
    return React.useCallback(
        (value: string) => sendFields(slot, {[field]: value}),
        [slot, field, sendFields]);
}

function StateText({label, value, send, width}: {
    label: string;
    value: string;
    send: (value: string) => unknown;
    width?: number;
}) {
    // The controls are for fields with suggestions.
    const {pick, replace, ...field} = useStateField(value, send);
    return (
        <TextField
            size="small"
            label={label}
            {...field}
            sx={width ? {width, flexShrink: 0} : {flex: 1, minWidth: 0}}
        />
    );
}

function NameField({value, send, players, onPick}: {
    value: string;
    send: (value: string) => unknown;
    players: AutonaPlayerDbEntry[];
    onPick: (player: AutonaPlayerDbEntry) => Promise<unknown>;
}) {
    const {pick, replace, ...field} = useStateField(value, send);
    // How the candidate Enter would take got highlighted. Only one the
    // keyboard moved to is taken: the mouse highlights whatever it passes
    // over on its way, and Enter then filled the whole caster (team,
    // Twitter, comment) with a player nobody chose. A click still picks.
    const highlightedBy = React.useRef<string | null>(null);
    return (
        <Autocomplete<AutonaPlayerDbEntry, false, true, true>
            freeSolo
            disableClearable
            // The app's text box rule (src/ui_layout.py
            // attach_popup_on_focus): the candidates show on focus.
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
                // A player from the DB fills the whole caster (core's
                // ApplyPlayerData): the name shows the tag at once. Enter on
                // typed text ("createOption") is the IME's, not a send.
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
                    label={i18n.t("commentary_name")}
                    onFocus={field.onFocus}
                    onBlur={field.onBlur}
                    onCompositionStart={field.onCompositionStart}
                    onCompositionEnd={field.onCompositionEnd}
                />
            )}
        />
    );
}

function RoleField({value, send, roles}: {
    value: string;
    send: (value: string) => unknown;
    roles: string[];
}) {
    const {pick, replace, ...field} = useStateField(value, send);
    return (
        <Autocomplete<string, false, true, true>
            freeSolo
            disableClearable
            openOnFocus
            size="small"
            sx={{flex: 1, minWidth: 0}}
            options={roles}
            value={null as unknown as string}
            inputValue={field.value}
            onInputChange={(event, _text, reason) => {
                if (reason === "input" && event) {
                    field.onChange(event as InputChange);
                }
            }}
            onChange={(_event, role, reason) => {
                if (reason === "selectOption" && typeof role === "string") {
                    pick(role);
                }
            }}
            renderInput={(params) => (
                <TextField
                    {...params}
                    label={i18n.t("commentary_role")}
                    onFocus={field.onFocus}
                    onBlur={field.onBlur}
                    onCompositionStart={field.onCompositionStart}
                    onCompositionEnd={field.onCompositionEnd}
                />
            )}
        />
    );
}

function iconLabel(icon: {id: string; label: string}): string {
    // Translated by stem, else core's own label (a new icon still shows).
    return i18n.t(`commentary_icon_${icon.id}`, {defaultValue: icon.label});
}

function CasterCard({
    slot, index, caster, options, players, displayLocked, sendFields,
    pickPlayer, drag,
}: CasterCardProps) {
    // The shell's language: a memoized card still re-renders its labels
    // when it changes.
    usePanelShell();
    const sendName = useFieldSender(slot, "name", sendFields);
    const sendTeam = useFieldSender(slot, "team", sendFields);
    const sendRole = useFieldSender(slot, "role", sendFields);
    const sendNote = useFieldSender(slot, "note", sendFields);
    const sendTwitter = useFieldSender(slot, "twitter", sendFields);
    const onPick = React.useCallback(
        (player: AutonaPlayerDbEntry) => pickPlayer(slot, player),
        [slot, pickPlayer]);

    const [icon, chooseIcon] = useStateChoice(
        caster.icon || "",
        (next: string) => sendFields(slot, {icon: next}));
    const [shown, chooseShown] = useStateChoice(
        Boolean(caster.display),
        (next: boolean) => sendFields(slot, {display: next}));

    return (
        <Card
            variant="outlined"
            component="section"
            aria-label={i18n.t("commentary_slot", {slot})}
            data-caster-card={slot}
            onDragOver={(event) => drag.onOver(index, event)}
            onDrop={(event) => drag.onDrop(index, event)}
            sx={{
                display: "flex",
                gap: 0.5,
                p: 1,
                pl: 0.5,
                // On air: one step brighter, with a stronger border (the Qt
                // card's `onair` variant).
                bgcolor: shown ? "action.selected" : "background.paper",
                borderColor: shown ? "text.secondary" : "divider",
            }}
        >
            <Box
                component="span"
                draggable
                role="button"
                aria-label={i18n.t("commentary_drag")}
                title={i18n.t("commentary_drag")}
                onDragStart={(event) => drag.onGripStart(slot, event)}
                onDragEnd={drag.onEnd}
                sx={{
                    alignSelf: "center",
                    display: "flex",
                    cursor: "grab",
                    color: "text.secondary",
                }}
            >
                <DragIndicator fontSize="small"/>
            </Box>
            <Stack spacing={1} sx={{flex: 1, minWidth: 0}}>
                <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography variant="body2" color="text.secondary">
                        {slot}
                    </Typography>
                    <Box sx={{flex: 1}}/>
                    <FormControlLabel
                        sx={{mr: 0}}
                        control={
                            <Checkbox
                                size="small"
                                checked={shown}
                                disabled={displayLocked && !shown}
                                onChange={(event) =>
                                    chooseShown(event.target.checked)}
                            />
                        }
                        label={i18n.t("commentary_display")}
                    />
                </Stack>
                <Stack direction="row" spacing={1}>
                    <NameField
                        value={caster.name || ""}
                        send={sendName}
                        players={players}
                        onPick={onPick}
                    />
                    <StateText
                        label={i18n.t("commentary_team")}
                        value={caster.team || ""}
                        send={sendTeam}
                        width={NARROW}
                    />
                </Stack>
                <Stack direction="row" spacing={1}>
                    <RoleField
                        value={caster.role || ""}
                        send={sendRole}
                        roles={options.roles}
                    />
                    <TextField
                        select
                        size="small"
                        label={i18n.t("commentary_icon")}
                        value={icon}
                        onChange={(event) => chooseIcon(event.target.value)}
                        sx={{width: NARROW, flexShrink: 0}}
                    >
                        <MenuItem value="">
                            {i18n.t("commentary_icon_none")}
                        </MenuItem>
                        {options.icons.map((option) => (
                            <MenuItem key={option.id} value={option.id}>
                                <ListItemIcon sx={{minWidth: 24}}>
                                    <img
                                        src={`${BASE_URL}/assets/icons/${encodeURIComponent(option.id)}.svg`}
                                        alt=""
                                        width={16}
                                        height={16}
                                    />
                                </ListItemIcon>
                                {iconLabel(option)}
                            </MenuItem>
                        ))}
                    </TextField>
                </Stack>
                <Stack direction="row" spacing={1}>
                    <StateText
                        label={i18n.t("commentary_note")}
                        value={caster.note || ""}
                        send={sendNote}
                    />
                    <StateText
                        label={i18n.t("commentary_twitter")}
                        value={caster.twitter || ""}
                        send={sendTwitter}
                        width={NARROW}
                    />
                </Stack>
            </Stack>
        </Card>
    );
}

export default React.memo(CasterCard);
