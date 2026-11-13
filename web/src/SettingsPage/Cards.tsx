import React from "react";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    Checkbox,
    FormControlLabel,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {ColorRow} from "../CommentaryPage/SideCards";
import {Composition, joinShortcut, splitShortcut} from "./hotkeys";

// The settings page's four cards (ADR-005 S4af), the Qt settings window's
// sections (its General split in two: the web server's network settings
// are applied by a restart). They draw what GET /api/v1/settings answered
// and ask the page to save; each row has the window's "Default" button,
// which saves the default GET sends.

export interface GeneralSettings {
    profanity_filter: boolean;
    disable_autoupdate: boolean;
    disable_scoreupdate: boolean;
    disable_export: boolean;
    disable_overwrite: boolean;
    team_1_default_color: string;
    team_2_default_color: string;
}

export interface NetworkSettings {
    port: number;
    // null: not decided yet (the desktop asks at its next start).
    listenLan: boolean | null;
    listening: {port: number; lan: boolean} | null;
    restartPending: boolean;
}

export interface LanguageChoice {
    code: string;
    name: string;
    englishName: string;
}

export interface LanguageSettings {
    program: string;
    gameAsset: string;
    fgTerm: string;
    choices: LanguageChoice[];
    restartPending: boolean;
}

export interface HotkeyEntry {
    // null: what is saved is not a string.
    value: string | null;
    default: string;
    valid: boolean;
    warning: string | null;
}

export interface HotkeySettings {
    enabled: boolean;
    keys: Record<string, HotkeyEntry>;
    vocabulary: {modifiers: string[]; keys: string[]};
    applied: boolean;
}

export interface Defaults {
    general: GeneralSettings;
    network: {port: number; listenLan: boolean};
    language: {program: string; gameAsset: string; fgTerm: string};
    // The switch's (core's HotkeyRules.DEFAULT_ENABLED); each shortcut's
    // default is its entry's.
    hotkeys: {enabled: boolean};
}

export type Section = "general" | "network" | "language" | "hotkeys";
export type Save = (section: Section, body: Record<string, unknown>) =>
    Promise<unknown>;

// The order of core's HotkeyRules.DEFAULT_KEYS (the Qt window's): the
// answer is a JSON object, whose keys the server sorts.
export const HOTKEY_ACTIONS = [
    "load_set", "team1_score_up", "team1_score_down", "team2_score_up",
    "team2_score_down", "reset_scores", "swap_teams", "refresh_phase_group",
    "apply_changes",
];

const SWITCHES: (keyof GeneralSettings)[] = [
    "profanity_filter", "disable_autoupdate", "disable_scoreupdate",
    "disable_export", "disable_overwrite",
];

export const LANGUAGE_FIELDS = ["program", "gameAsset", "fgTerm"] as const;
type LanguageField = typeof LANGUAGE_FIELDS[number];

export const PORT_MIN = 1;
export const PORT_MAX = 65535;

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

// A row: what it sets, and the "Default" button (named for the row, for a
// screen reader and the tests).
function Row({label, isDefault, onDefault, children, note}: {
    label: string;
    isDefault: boolean;
    onDefault: () => void;
    children: React.ReactNode;
    note?: React.ReactNode;
}) {
    return (
        <Box>
            <Stack direction="row" alignItems="center" spacing={1}>
                <Box sx={{flex: 1, minWidth: 0}}>{children}</Box>
                <Button size="small" disabled={isDefault} onClick={onDefault}
                        aria-label={i18n.t("settings_default_for",
                                           {label})}>
                    {i18n.t("settings_default")}
                </Button>
            </Stack>
            {note ? <Box sx={{pl: 4}}><Note>{note}</Note></Box> : null}
        </Box>
    );
}

function SwitchRow({label, value, defaultValue, send, note}: {
    label: string;
    value: boolean;
    defaultValue: boolean;
    send: (value: boolean) => Promise<unknown>;
    note?: React.ReactNode;
}) {
    const [checked, choose] = useStateChoice(value, send);
    return (
        <Row label={label} isDefault={value === defaultValue}
             onDefault={() => choose(defaultValue)} note={note}>
            <FormControlLabel
                control={<Checkbox checked={checked} size="small"
                                   onChange={(event) => choose(event.target.checked)}/>}
                label={label}
            />
        </Row>
    );
}

export function GeneralCard({general, defaults, save}: {
    general: GeneralSettings;
    defaults: GeneralSettings;
    save: Save;
}) {
    const send = (name: keyof GeneralSettings) =>
        (value: boolean | string) => save("general", {[name]: value});
    return (
        <CardFrame title={i18n.t("settings_general")}>
            {SWITCHES.map((name) => (
                <SwitchRow
                    key={name}
                    label={i18n.t(`settings_general_${name}`)}
                    value={general[name] as boolean}
                    defaultValue={defaults[name] as boolean}
                    send={send(name)}
                    note={name === "disable_autoupdate"
                        ? i18n.t("settings_general_disable_autoupdate_note")
                        : undefined}
                />
            ))}
            {(["team_1_default_color", "team_2_default_color"] as const).map((name) => (
                <Row key={name}
                     label={i18n.t(`settings_general_${name}`)}
                     isDefault={general[name].toLowerCase()
                                === defaults[name].toLowerCase()}
                     onDefault={() => {
                         save("general", {[name]: defaults[name]})
                             .catch(() => undefined);
                     }}>
                    <ColorRow label={i18n.t(`settings_general_${name}`)}
                              value={general[name]}
                              send={send(name)}/>
                </Row>
            ))}
            <Note>{i18n.t("settings_general_note")}</Note>
        </CardFrame>
    );
}

export function NetworkCard({network, defaults, save}: {
    network: NetworkSettings;
    defaults: Defaults["network"];
    save: Save;
}) {
    const [draft, setDraft] = React.useState(String(network.port));
    React.useEffect(() => {
        setDraft(String(network.port));
    }, [network.port]);
    const parsed = /^\d+$/.test(draft.trim()) ? Number(draft.trim()) : NaN;
    const portValid = Number.isInteger(parsed)
        && parsed >= PORT_MIN && parsed <= PORT_MAX;
    const sendPort = (port: number) => {
        save("network", {port}).catch(() => setDraft(String(network.port)));
    };
    const sendLan = React.useCallback(
        (listenLan: boolean) => save("network", {listenLan}), [save]);
    const portLabel = i18n.t("settings_network_port");
    const listening = network.listening
        ? i18n.t("settings_network_listening", {
            port: network.listening.port,
            scope: i18n.t(network.listening.lan
                ? "settings_network_scope_lan" : "settings_network_scope_local"),
        })
        : i18n.t("settings_network_not_listening");
    return (
        <CardFrame title={i18n.t("settings_network")}>
            <Row label={portLabel} isDefault={network.port === defaults.port}
                 onDefault={() => sendPort(defaults.port)}>
                <Stack direction="row" spacing={1} alignItems="flex-start"
                       component="form"
                       onSubmit={(event: React.FormEvent) => {
                           event.preventDefault();
                           if (portValid && parsed !== network.port) {
                               sendPort(parsed);
                           }
                       }}>
                    <TextField
                        size="small"
                        label={portLabel}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        error={!portValid}
                        helperText={portValid ? undefined
                            : i18n.t("settings_network_port_invalid",
                                     {min: PORT_MIN, max: PORT_MAX})}
                        slotProps={{htmlInput: {inputMode: "numeric"}}}
                        sx={{width: 160}}
                    />
                    <Button type="submit" variant="outlined"
                            disabled={!portValid || parsed === network.port}>
                        {i18n.t("settings_network_port_apply")}
                    </Button>
                </Stack>
            </Row>
            <LanRow network={network} defaultValue={defaults.listenLan}
                    send={sendLan}/>
            <Note>{listening}</Note>
            {network.restartPending ? (
                <Alert severity="info">{i18n.t("settings_network_restart")}</Alert>
            ) : null}
        </CardFrame>
    );
}

function LanRow({network, defaultValue, send}: {
    network: NetworkSettings;
    defaultValue: boolean;
    send: (value: boolean) => Promise<unknown>;
}) {
    const label = i18n.t("settings_network_lan");
    const [checked, choose] = useStateChoice(Boolean(network.listenLan), send);
    // Not decided yet is the default as well: "Default" would save false,
    // and the desktop would no longer ask at its next start (the Qt window
    // left it undecided too).
    return (
        <Row label={label}
             isDefault={network.listenLan === null
                        || network.listenLan === defaultValue}
             onDefault={() => choose(defaultValue)}
             note={network.listenLan === null
                 ? i18n.t("settings_network_lan_undecided") : undefined}>
            <FormControlLabel
                control={<Checkbox checked={checked} size="small"
                                   onChange={(event) => choose(event.target.checked)}/>}
                label={label}
            />
        </Row>
    );
}

function LanguageRow({field, language, defaultValue, save}: {
    field: LanguageField;
    language: LanguageSettings;
    defaultValue: string;
    save: Save;
}) {
    const send = React.useCallback(
        (value: string) => save("language", {[field]: value}), [field, save]);
    const [value, choose] = useStateChoice(language[field], send);
    const label = i18n.t(`settings_language_${field}`);
    const known = value === "default"
        || language.choices.some((choice) => choice.code === value);
    return (
        <Row label={label} isDefault={language[field] === defaultValue}
             onDefault={() => choose(defaultValue)}>
            <TextField
                select
                fullWidth
                size="small"
                label={label}
                value={known ? value : "default"}
                onChange={(event) => choose(event.target.value)}
            >
                <MenuItem value="default">
                    {i18n.t(field === "program"
                        ? "settings_language_system" : "settings_language_same")}
                </MenuItem>
                {language.choices.map((choice) => (
                    <MenuItem key={choice.code} value={choice.code}>
                        {`${choice.name} / ${choice.englishName}`}
                    </MenuItem>
                ))}
            </TextField>
        </Row>
    );
}

export function LanguageCard({language, defaults, save}: {
    language: LanguageSettings;
    defaults: Defaults["language"];
    save: Save;
}) {
    return (
        <CardFrame title={i18n.t("settings_language")}>
            {LANGUAGE_FIELDS.map((field) => (
                <LanguageRow key={field} field={field} language={language}
                             defaultValue={defaults[field]} save={save}/>
            ))}
            {language.restartPending ? (
                <Alert severity="info">{i18n.t("settings_language_restart")}</Alert>
            ) : null}
        </CardFrame>
    );
}

export function hotkeyActionLabel(action: string): string {
    return i18n.t(`settings_hotkey_action_${action}`, {defaultValue: action});
}

export function hotkeyWarningText(code: string | null, shortcut: string): string | null {
    if (!code) {
        return null;
    }
    return i18n.t(`settings_hotkey_warning_${code}`, {
        shortcut,
        defaultValue: "",
    }) || null;
}

function HotkeyRow({action, entry, keys, saveKey}: {
    action: string;
    entry: HotkeyEntry;
    keys: string[];
    saveKey: (action: string, value: string) => Promise<unknown>;
}) {
    // Every save of the card answers a new hotkeys section (new arrays), so
    // the draft follows what this row has saved and the vocabulary's
    // contents -- not their identity, which would put every other row's
    // composition back as one row is saved.
    const vocabulary = keys.join("\n");
    const saved = React.useMemo(
        () => splitShortcut(entry.value, vocabulary ? vocabulary.split("\n") : []),
        [entry.value, vocabulary]);
    const [draft, setDraft] = React.useState<Composition>(saved);
    const [touched, setTouched] = React.useState(false);
    React.useEffect(() => {
        setDraft(saved);
        setTouched(false);
    }, [saved]);
    const label = hotkeyActionLabel(action);
    const composed = joinShortcut(draft);
    // A saved value the controls cannot show (not a string, or not Ctrl /
    // Alt / Shift and one key of the vocabulary): its row starts from
    // nothing, and saves only once the operator has touched it -- then even
    // "None", which clears it. Any other row saves a composition other than
    // the one it started from, however the saved value is spelled.
    const unreadable = entry.value !== "" && !saved.key;
    const changed = unreadable ? touched : composed !== joinShortcut(saved);
    const compose = (next: Composition) => {
        setDraft(next);
        setTouched(true);
    };
    const toggle = (modifier: "ctrl" | "alt" | "shift") =>
        (event: React.ChangeEvent<HTMLInputElement>) =>
            compose({...draft, [modifier]: event.target.checked});
    const warning = entry.value
        ? hotkeyWarningText(entry.warning, entry.value) : null;
    const shown = entry.value === null
        ? i18n.t("settings_hotkey_unreadable")
        : entry.value === "" ? i18n.t("settings_hotkey_none") : entry.value;
    return (
        <Box component="section" aria-label={label}
             sx={{borderTop: 1, borderColor: "divider", pt: 1}}>
            <Stack direction="row" alignItems="center" spacing={1}
                   sx={{flexWrap: "wrap", rowGap: 0.5}}>
                <Typography variant="body2" sx={{flex: "1 1 180px"}}>
                    {label}
                </Typography>
                <Typography variant="body2" sx={{fontFamily: "monospace"}}
                            aria-label={i18n.t("settings_hotkey_current",
                                               {label})}>
                    {shown}
                </Typography>
                {!entry.valid ? (
                    <Typography variant="body2" color="error">
                        {i18n.t("settings_hotkey_invalid")}
                    </Typography>
                ) : null}
            </Stack>
            <Stack direction="row" alignItems="center" spacing={0.5}
                   sx={{flexWrap: "wrap", rowGap: 0.5}}>
                {(["ctrl", "alt", "shift"] as const).map((modifier) => (
                    <FormControlLabel
                        key={modifier}
                        control={<Checkbox size="small" checked={draft[modifier]}
                                           onChange={toggle(modifier)}/>}
                        label={i18n.t(`settings_hotkey_${modifier}`)}
                        sx={{mr: 1}}
                    />
                ))}
                <TextField
                    select
                    size="small"
                    label={i18n.t("settings_hotkey_key")}
                    value={draft.key}
                    onChange={(event) =>
                        compose({...draft, key: event.target.value})}
                    sx={{width: 140}}
                >
                    <MenuItem value="">{i18n.t("settings_hotkey_none")}</MenuItem>
                    {keys.map((key) => (
                        <MenuItem key={key} value={key}>{key}</MenuItem>
                    ))}
                </TextField>
                <Button size="small" variant="contained" disabled={!changed}
                        aria-label={i18n.t("settings_hotkey_save_for", {label})}
                        onClick={() => {
                            saveKey(action, composed).catch(() => undefined);
                        }}>
                    {i18n.t("settings_hotkey_save")}
                </Button>
                <Button size="small" disabled={entry.value === entry.default}
                        aria-label={i18n.t("settings_default_for", {label})}
                        onClick={() => {
                            saveKey(action, entry.default).catch(() => undefined);
                        }}>
                    {i18n.t("settings_default")}
                </Button>
            </Stack>
            {warning ? (
                <Typography variant="body2" color="warning.main">
                    {warning}
                </Typography>
            ) : null}
        </Box>
    );
}

export function HotkeysCard({hotkeys, defaults, save, saveKey}: {
    hotkeys: HotkeySettings;
    defaults: Defaults["hotkeys"];
    save: Save;
    saveKey: (action: string, value: string) => Promise<unknown>;
}) {
    const sendEnabled = React.useCallback(
        (enabled: boolean) => save("hotkeys", {enabled}), [save]);
    const actions = HOTKEY_ACTIONS.filter((action) => action in hotkeys.keys)
        .concat(Object.keys(hotkeys.keys)
            .filter((action) => !HOTKEY_ACTIONS.includes(action)));
    return (
        <CardFrame title={i18n.t("settings_hotkeys")}>
            {hotkeys.applied ? null : (
                <Alert severity="info">{i18n.t("settings_hotkeys_not_applied")}</Alert>
            )}
            <SwitchRow label={i18n.t("settings_hotkeys_enabled")}
                       value={hotkeys.enabled}
                       defaultValue={defaults.enabled}
                       send={sendEnabled}/>
            <Note>{i18n.t("settings_hotkeys_note")}</Note>
            {actions.map((action) => (
                <HotkeyRow key={action} action={action}
                           entry={hotkeys.keys[action]}
                           keys={hotkeys.vocabulary.keys}
                           saveKey={saveKey}/>
            ))}
        </CardFrame>
    );
}
