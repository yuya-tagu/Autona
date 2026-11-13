import React from "react";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {apiErrorCode, apiErrorStatus, apiPatch} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {
    Defaults,
    GeneralCard,
    GeneralSettings,
    HotkeySettings,
    HotkeysCard,
    LanguageCard,
    LanguageSettings,
    NetworkCard,
    NetworkSettings,
    Section,
    hotkeyActionLabel,
    hotkeyWarningText,
} from "./Cards";
import {sharingActions} from "./hotkeys";

// The settings page (ADR-005 S4af), /web/settings: the Qt settings
// window's settings -- general, the web server's network, the languages,
// the hotkeys -- read from GET /api/v1/settings (an allow-list, never the
// access token) and saved with PATCH /api/v1/settings/<section>. Opened
// from the desktop's Help & Settings menu in the shared web view, and in a
// browser alike: general needs the token from another computer (WRITE);
// network, language and hotkeys are this PC's only (THIS_PC: refused from
// another computer even with the token). Settings, not state: nothing here
// reads the Redux state; each save answers its section as it now is.

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface Settings {
    general: GeneralSettings;
    network: NetworkSettings;
    language: LanguageSettings;
    hotkeys: HotkeySettings;
    defaults: Defaults;
    loadFailed: boolean;
    movedAside: string | null;
}

type Warning = {action: string; code: string};

function SettingsContent() {
    const {language} = usePanelShell();
    const [settings, setSettings] = React.useState<Settings | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    const [loads, setLoads] = React.useState(0);
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();
    // The latest settings, for a save's refusal (the actions a conflict
    // was about) without re-creating the save on each answer.
    const latest = React.useRef<Settings | null>(null);
    latest.current = settings;

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("settings_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Settings>>("/api/v1/settings", () => live)
            .then((body) => {
                if (live) {
                    setSettings(body.data);
                }
            }, (error) => {
                console.error(error);
                if (live) {
                    setLoadFailed(true);
                }
            });
        return () => {
            live = false;
        };
    }, [loads]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
    }, []);

    // A refusal told: what it was about, else the generic failure.
    const tell = React.useCallback((section: Section, error: unknown,
                                    asked: Record<string, unknown>) => {
        const status = apiErrorStatus(error);
        const code = apiErrorCode(error);
        if (status === 403 && section !== "general") {
            showSnackbar(i18n.t("settings_this_pc_only"), "error");
        } else if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                showSnackbar(i18n.t("stream_token_required"), "error");
            }
        } else if (status === 409 && code === "NOT_READY") {
            showSnackbar(i18n.t("settings_not_ready"), "error");
        } else if (status === 409 && code === "CONFLICT" && section === "hotkeys") {
            const keys = (asked.keys ?? {}) as Record<string, string>;
            const [action, shortcut] = Object.entries(keys)[0] ?? ["", ""];
            const current = latest.current?.hotkeys;
            const others = current ? sharingActions(
                shortcut, action,
                Object.fromEntries(Object.entries(current.keys)
                    .map(([name, entry]) => [name, entry.value])),
                current.vocabulary.keys) : [];
            showSnackbar(others.length
                ? i18n.t("settings_hotkey_conflict", {
                    shortcut,
                    others: others.map(hotkeyActionLabel).join(", "),
                })
                : i18n.t("settings_hotkey_conflict_unknown", {shortcut}),
                "error");
        } else if (status === 400 && section === "hotkeys") {
            showSnackbar(i18n.t("settings_hotkey_unreadable_shortcut"), "error");
        } else {
            showSnackbar(i18n.t("request_failed"), "error");
        }
    }, []);

    const save = React.useCallback((section: Section,
                                    body: Record<string, unknown>) =>
        serial(() => apiPatch(`/api/v1/settings/${section}`, body,
                              WRITE_OPTIONS))
            .then((text) => {
                const data = (JSON.parse(text) as Envelope<
                    Settings[Section] & {warnings?: Warning[]}>).data;
                const {warnings, ...rest} = data as typeof data & {
                    warnings?: Warning[];
                };
                setSettings((current) => current
                    ? {...current, [section]: rest, loadFailed: false}
                    : current);
                // A launch that could not read settings.json showed the
                // defaults; this save read the file again (or moved a
                // corrupt one aside): the other sections are read again.
                if (latest.current?.loadFailed) {
                    setLoads((count) => count + 1);
                }
                const first = warnings?.[0];
                if (first) {
                    const keys = (body.keys ?? {}) as Record<string, string>;
                    const text = hotkeyWarningText(
                        first.code, keys[first.action] ?? "");
                    if (text) {
                        showSnackbar(text, "warning");
                    }
                }
                return data;
            }, (error: unknown) => {
                console.error(error);
                tell(section, error, body);
                throw error;
            }), [serial, tell]);

    const saveKey = React.useCallback(
        (action: string, value: string) =>
            save("hotkeys", {keys: {[action]: value}}),
        [save]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("settings_reload")}
                        </Button>
                    }
                >
                    {i18n.t("settings_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!settings) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("settings_loading")}</Typography>
            </Box>
        );
    }

    return (
        <Box component="main" sx={{p: 2, bgcolor: "background.default",
                                   minHeight: "100vh"}}>
            <Stack spacing={2} sx={{maxWidth: 820, mx: "auto"}}>
                <Typography variant="h6" component="h1">
                    {i18n.t("settings_title")}
                </Typography>
                {settings.loadFailed ? (
                    <Alert severity="warning">
                        {i18n.t("settings_file_unreadable")}
                    </Alert>
                ) : null}
                {settings.movedAside ? (
                    <Alert severity="warning">
                        {i18n.t("settings_file_moved_aside",
                                {name: settings.movedAside})}
                    </Alert>
                ) : null}
                <GeneralCard general={settings.general}
                             defaults={settings.defaults.general} save={save}/>
                <NetworkCard network={settings.network}
                             defaults={settings.defaults.network} save={save}/>
                <LanguageCard language={settings.language}
                              defaults={settings.defaults.language}
                              save={save}/>
                <HotkeysCard hotkeys={settings.hotkeys}
                             defaults={settings.defaults.hotkeys} save={save}
                             saveKey={saveKey}/>
            </Stack>
        </Box>
    );
}

export default function SettingsPage() {
    return (
        <FeedbackProvider>
            <SettingsContent/>
        </FeedbackProvider>
    );
}
