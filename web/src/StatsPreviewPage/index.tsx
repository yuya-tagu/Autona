import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    CircularProgress,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiErrorStatus, apiPatch, apiPut} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
} from "../PanelShell/requests";
import {copyText} from "../PanelShell/clipboard";
import DesignPane from "../CommentaryPage/DesignPane";
import {
    ColorKind,
    FeatureColorCard,
    LayoutEntry,
    ObsUrlCard,
} from "../CommentaryPage/SideCards";

// The Stats Display Settings panel as a React page (ADR-005 S4l),
// /web/statsPreview: which statistics overlay to look at (kept in
// settings.json by core's StatsOverlays), the feature's colours, the
// Companion macro for stats:refresh, and the overlay itself with its OBS
// URL. Shown in the desktop app's shared web view and in a browser alike.
// It writes through /api/v1 only and renders colors.stats from the state.

const FEATURE = "stats";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// GET /api/v1/stats/overlays: `key` is what is kept; two overlays can share
// one folder (`id`) and differ by `entry` (player1.html / player2.html).
interface Overlay {
    key: string;
    id: string;
    entry: string;
    displayName: string;
}

interface Overlays {
    overlays: Overlay[];
    selected: Overlay;
    refresh: {method: string; path: string; url: string};
}

// The English source name is the fallback for a key this build has no
// translation of.
function toEntry(overlay: Overlay): LayoutEntry {
    return {
        id: overlay.id,
        kind: "stats",
        displayName: i18n.t(`statspreview_overlay_${overlay.key}`,
                            {defaultValue: overlay.displayName}),
        entry: overlay.entry,
        key: overlay.key,
    };
}

function useStatsColors() {
    return useSelector((state: ReduxState) => {
        const root = state.autonaState.autonaState as unknown as {
            colors?: Record<string, {themeColor?: string; accentColor?: string}>;
            tournamentInfo?: {themeColor?: string; accentColor?: string};
        };
        return {
            feature: root.colors?.[FEATURE],
            tournament: root.tournamentInfo,
        };
    }, (a, b) => a.feature === b.feature && a.tournament === b.tournament);
}

function CompanionCard({refresh}: {refresh: Overlays["refresh"]}) {
    const title = i18n.t("statspreview_companion");
    const copy = async () => {
        if (await copyText(refresh.url)) {
            showSnackbar(i18n.t("statspreview_companion_copied"), "success");
        } else {
            showSnackbar(i18n.t("statspreview_companion_copy_failed"), "error");
        }
    };
    const rows: [string, string][] = [
        [i18n.t("statspreview_companion_method"), refresh.method],
        [i18n.t("statspreview_companion_path"), refresh.path],
        [i18n.t("statspreview_companion_body"),
         i18n.t("statspreview_companion_body_none")],
    ];
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("statspreview_companion_note")}
                    </Typography>
                    {rows.map(([label, value]) => (
                        <Stack key={label} direction="row" spacing={1}>
                            <Typography variant="body2"
                                        sx={{width: 72, flexShrink: 0}}>
                                {label}
                            </Typography>
                            <Typography variant="body2">{value}</Typography>
                        </Stack>
                    ))}
                    <Stack direction="row" spacing={1}>
                        <TextField
                            size="small"
                            value={refresh.url}
                            slotProps={{htmlInput: {
                                readOnly: true,
                                "aria-label": i18n.t("statspreview_companion_url"),
                            }}}
                            onFocus={(event) => event.target.select()}
                            sx={{flex: 1, minWidth: 0}}
                        />
                        <Button variant="contained" size="small" onClick={copy}>
                            {i18n.t("statspreview_companion_copy")}
                        </Button>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("statspreview_companion_guide")}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("statspreview_companion_token")}
                    </Typography>
                </Stack>
            </CardContent>
        </Card>
    );
}

function StatsPreviewContent() {
    const {language} = usePanelShell();
    const colors = useStatsColors();
    const [loaded, setLoaded] = React.useState(false);
    const [loadFailed, setLoadFailed] = React.useState(false);
    const [loads, setLoads] = React.useState(0);
    const [overlays, setOverlays] = React.useState<Overlay[]>([]);
    const [selected, setSelected] = React.useState<Overlay | null>(null);
    const [refresh, setRefresh] = React.useState<Overlays["refresh"] | null>(null);
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("statspreview_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Overlays>>("/api/v1/stats/overlays", () => live)
            .then((body) => {
                if (live) {
                    setOverlays(body.data.overlays);
                    setSelected(body.data.selected);
                    setRefresh(body.data.refresh);
                    setLoaded(true);
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

    // A failed write told: the token once, else the generic failure.
    const write = React.useCallback(<T,>(request: () => Promise<T>) =>
        serial(request).catch((error: unknown) => {
            console.error(error);
            const status = apiErrorStatus(error);
            if (status === 401 || status === 403) {
                if (!toldToken.current) {
                    toldToken.current = true;
                    showSnackbar(i18n.t("stream_token_required"), "error");
                }
            } else {
                showSnackbar(i18n.t("request_failed"), "error");
            }
            throw error;
        }), [serial]);

    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) =>
            write(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                 WRITE_OPTIONS)),
        [write]);
    const select = React.useCallback((key: string) => {
        write(() => apiPut("/api/v1/stats/overlays/selected", {key},
                           WRITE_OPTIONS))
            .then((text) => setSelected(
                (JSON.parse(text) as Envelope<Overlay>).data))
            .catch(() => undefined);
    }, [write]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("statspreview_reload")}
                        </Button>
                    }
                >
                    {i18n.t("statspreview_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!loaded || !refresh) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("statspreview_loading")}</Typography>
            </Box>
        );
    }

    const designs = overlays.map(toEntry);
    const design = selected
        ? designs.find((entry) => entry.key === selected.key) ?? null : null;
    const tournamentTheme = colors.tournament?.themeColor;
    const tournamentAccent = colors.tournament?.accentColor;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;

    return (
        <Box
            component="main"
            sx={{
                display: "flex",
                flexDirection: {xs: "column", md: "row"},
                height: {md: "100vh"},
                bgcolor: "background.default",
            }}
        >
            <Box sx={{width: {md: 440}, flexShrink: 0, overflowY: {md: "auto"},
                      p: 2}}>
                <Stack spacing={2}>
                    <FeatureColorCard
                        title={i18n.t("statspreview_colors")}
                        theme={colors.feature?.themeColor ?? tournamentTheme ?? ""}
                        accent={colors.feature?.accentColor ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                    <CompanionCard refresh={refresh}/>
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <Stack spacing={2}>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("statspreview_type_note")}
                    </Typography>
                    <DesignPane
                        designs={designs}
                        design={design}
                        onSelect={select}
                        label={i18n.t("statspreview_type")}
                        empty={i18n.t("statspreview_no_design")}
                    />
                    <ObsUrlCard design={design}
                                note={i18n.t("statspreview_obs_note")}/>
                </Stack>
            </Box>
        </Box>
    );
}

export default function StatsPreviewPage() {
    return (
        <FeedbackProvider>
            <StatsPreviewContent/>
        </FeedbackProvider>
    );
}
