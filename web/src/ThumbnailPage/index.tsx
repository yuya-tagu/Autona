import React from "react";
import {shallowEqual, useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    CircularProgress,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {ReduxState} from "../redux/store";
import {apiErrorStatus, apiGetJson, apiPatch, apiPost} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {useDocumentVisibility} from "../PanelShell/useDocumentVisibility";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
    withDeadline,
} from "../PanelShell/requests";
import {
    TemplateChoice,
    templateLabel,
    useTemplatePackagesRead,
} from "../PanelShell/templatePackages";
import {
    IMAGE_UPLOAD_MAX_BYTES,
    UPLOAD_TIMEOUT_MS,
    readDataUrl,
} from "../PanelShell/imageUpload";
import {
    ColorKind,
    FeatureColorCard,
    LayoutEntry,
} from "../CommentaryPage/SideCards";
import DesignPane, {RenderPayload} from "../CommentaryPage/DesignPane";
import {
    LayoutCheckPanel,
    reportFromErrors,
} from "../CommentaryPage/LayoutCheck";
import {
    CharactersCard,
    DesignCard,
    GenerateResult,
    ItemsCard,
    PreviewCard,
    ResetButton,
    SettingsChange,
    ThumbnailGame,
    ThumbnailRender,
    ThumbnailSettings,
    ThumbnailTemplate,
    VideoTitleCard,
    errorText,
} from "./Cards";

// The match thumbnail's settings page (サムネイル, ADR-005 S4p),
// /web/thumbnail: the template, the background, the items shown, each game's
// character image placement and the colours, a preview drawn by the real
// renderer, the generation of the thumbnail (out/thumbnails) and the video
// title. Shown in the desktop app's shared web view and in a browser alike.
// The settings are settings.json's, not state: read when the page loads and
// taken from each write's answer. It writes through /api/v1 only (core's
// ThumbnailService does the work) and follows thumbnail.render in the state.
//
// The preview: PREVIEW_DEBOUNCE_MS after a change of what it shows (a
// setting, the colours, the match, the tournament), POST :preview, whose
// answer is the run's revision; the image is GET /thumbnail/preview?v=<the
// revision> once thumbnail.render has ended with it. Only while the page is
// shown: hidden (another Qt panel in front of the shared view, a background
// tab), nothing is drawn, so the renderer does not work for nobody; shown
// again, it draws (and reads the title) whatever changed, as the Qt panel did
// on every reveal. The state cannot say whether anything did: core draws and
// titles board 1 with its unapplied edits (the desktop holds the board until
// 反映), which the published state this page follows does not carry.
//
// A template on template contract 2 (ADR-008 D7 R2) is previewed live
// instead: on the same changes, GET /api/v1/thumbnail/payload (the preview's
// payload, no renderer needed: the headless shell and a browser on the LAN
// preview too), drawn by the template itself in the preview listener's frame
// (DesignPane), with its errors beside it. The generated image is still the
// desktop's off-screen render; a generate that failed on the template's
// errors shows them (thumbnail.render.errors). The first preview waits for
// the catalog (which contract the template is); a payload refused (no
// access token on the LAN: PLAYERDB) or not of contract 2 falls back to the
// renderer's image (GET /thumbnail/preview, READ).

export const PREVIEW_DEBOUNCE_MS = 300;
// The template contract previewed live.
export const LIVE_CONTRACT = 2;

const FEATURE = "thumbnail";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

// GET /api/v1/thumbnail.
interface Description {
    settings: ThumbnailSettings;
    game: ThumbnailGame | null;
    templates: ThumbnailTemplate[];
    videoTitle: string;
    // False on the headless shell, which cannot draw (POST :preview and
    // :generate answer 503 there).
    canRender: boolean;
}

const IDLE: ThumbnailRender = {status: "idle", path: "", error: "",
                               revision: 0};

function readRender(raw: Partial<ThumbnailRender> | undefined): ThumbnailRender {
    if (!raw || typeof raw !== "object") {
        return IDLE;
    }
    return {
        status: raw.status ?? "idle",
        path: typeof raw.path === "string" ? raw.path : "",
        error: typeof raw.error === "string" ? raw.error : "",
        revision: typeof raw.revision === "number" ? raw.revision : 0,
    };
}

// GET /api/v1/thumbnail/payload.
interface LivePayload {
    templateId: string;
    folder: string;
    entry: string;
    contract: number;
    size: [number, number];
    payload: unknown;
    // The config's keys whose image the live preview cannot show.
    omitted: string[];
}

// What the live preview shows: the design framed and the render posted.
interface LivePreview {
    design: LayoutEntry;
    size: [number, number];
    render: RenderPayload;
    omitted: string[];
}

interface ThumbnailStateRoot {
    thumbnail?: {render?: Partial<ThumbnailRender>};
    colors?: Record<string, {themeColor?: string; accentColor?: string}>;
    tournamentInfo?: Record<string, unknown> & {
        themeColor?: string;
        accentColor?: string;
    };
    game?: unknown;
    score?: Record<string, unknown>;
    display_options?: unknown;
}

function root(state: ReduxState): ThumbnailStateRoot {
    return state.autonaState.autonaState as unknown as ThumbnailStateRoot;
}

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields {
    return value && typeof value === "object" ? value as Fields : {};
}

// What the picture and the video title are drawn from, as one string: a
// change of it is a new preview (and a new title). Only what they read, so
// that what neither shows (the score, the statistics kept beside the match,
// a player's country, the other display options) draws nothing:
// - the picture: what layout/thumbnail/thumbnail.js and
//   layout/thumbnail_type_b/thumbnail.js read of the payload's data --
//   game.codename; score.1's phase, pool and match; each team's teamName
//   and its players' name, team (the sponsor) and character (the images);
//   tournamentInfo's tournamentName, name and startAt;
//   display_options.show_sponsor. And the banner a background may be, a
//   file: tournamentInfo.bannerVersion says it changed;
// - the title: what core BuildVideoTitle reads -- tournamentInfo.name,
//   score.1's match_en, the game's abbreviation (else its smashgg_id's),
//   and each team's first player's mergedName (without the losers mark it
//   leaves out), gamerTag, character and mains.
// Board 1 is core's RENDER_BOARD. The colours are followed apart (colors).
function matchSignature(state: ReduxState): string {
    const data = root(state);
    const board = fields(data.score?.["1"]);
    const teams = fields(board.team);
    const tournament = fields(data.tournamentInfo);
    const game = fields(data.game);
    const team = (number: string) => {
        const side = fields(teams[number]);
        const players = Object.entries(fields(side.player)).map(
            ([key, value]) => {
                const player = fields(value);
                const drawn = {key, name: player.name, sponsor: player.team,
                               character: player.character ?? null};
                if (key !== "1") {
                    return drawn;
                }
                const merged = player.mergedName;
                return {...drawn, gamerTag: player.gamerTag,
                        mains: player.mains ?? null,
                        merged: typeof merged === "string"
                            ? merged.split(" [L]").join("") : merged};
            });
        return {teamName: side.teamName, players};
    };
    return JSON.stringify({
        game: [game.codename, game.abbreviation, game.smashgg_id],
        tournament: [tournament.tournamentName, tournament.name,
                     tournament.startAt, tournament.bannerVersion],
        showSponsor: fields(data.display_options).show_sponsor,
        phase: board.phase, pool: board.pool, match: board.match,
        matchEn: board.match_en,
        teams: [team("1"), team("2")],
    });
}

// What a refused write says; null: nothing (the caller tells it).
type Refusal = (status: number | undefined) => string | null;

function ThumbnailContent() {
    const {language, desktop} = usePanelShell();
    const rawRender = useSelector((state: ReduxState) =>
        root(state).thumbnail?.render);
    const render = readRender(rawRender);
    const renderErrors = (rawRender as {errors?: unknown} | undefined)?.errors;
    // By value: a write's echo may bring the same colours in new objects.
    const colors = useSelector((state: ReduxState) => {
        const data = root(state);
        return {
            theme: data.colors?.[FEATURE]?.themeColor,
            accent: data.colors?.[FEATURE]?.accentColor,
            tournamentTheme: data.tournamentInfo?.themeColor,
            tournamentAccent: data.tournamentInfo?.accentColor,
        };
    }, shallowEqual);
    const signature = useSelector(matchSignature);
    const {visible, reveals} = useDocumentVisibility();

    const [loaded, setLoaded] = React.useState(false);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped by the reload button: loads the page again.
    const [loads, setLoads] = React.useState(0);
    const [settings, setSettings] = React.useState<ThumbnailSettings | null>(null);
    const [templates, setTemplates] = React.useState<ThumbnailTemplate[]>([]);
    // The templates to choose from: the catalog's thumbnail packages
    // (ADR-007 T1), or the description's until (unless) they are read.
    const {packages, settled: packagesSettled} =
        useTemplatePackagesRead("thumbnail", loads);
    const choices: TemplateChoice[] = React.useMemo(() => (packages
        ? packages.map((entry) => ({
            id: entry.id,
            label: templateLabel(entry, "thumbnail_template_", language)}))
        : templates.map((entry) => ({
            id: entry.id,
            label: i18n.t(`thumbnail_template_${entry.id}`,
                          {defaultValue: entry.name})}))),
    [packages, templates, language]);
    // The template chosen, as the catalog lists it (null: not listed, or
    // the packages not read yet).
    const templateId = settings?.template_id;
    const chosen = React.useMemo(() => packages?.find(
        (entry) => entry.id === templateId) ?? null, [packages, templateId]);
    // A template whose live preview was refused (its payload: 401 / 403 on
    // the LAN without the token, or not of contract 2 after all): previewed
    // by the renderer's image instead, until the page loads again.
    const [liveRefused, setLiveRefused] = React.useState<string | null>(null);
    const live = chosen !== null && chosen.contract === LIVE_CONTRACT
        && liveRefused !== chosen.id;
    const [livePreview, setLivePreview] =
        React.useState<LivePreview | null>(null);
    const [game, setGame] = React.useState<ThumbnailGame | null>(null);
    const [videoTitle, setVideoTitle] = React.useState("");
    const [canRender, setCanRender] = React.useState(true);
    // The newest revision a preview of this page's was answered with.
    const [previewAsked, setPreviewAsked] = React.useState(0);
    // The generation of this page's on its way: its revision, and the saved
    // file before it (render.path).
    const [generating, setGenerating] =
        React.useState<{revision: number; from: string} | null>(null);
    const [result, setResult] = React.useState<GenerateResult | null>(null);
    const [imageRevision, setImageRevision] = React.useState(0);
    // No generation is asked for while one awaits its answer (twice) or a
    // background is on its way up (the old one would be drawn).
    const [asking, setAsking] = React.useState(false);
    const askingRef = React.useRef(false);
    const [uploading, setUploading] = React.useState(0);

    // A refusal for want of the access token is told once: every write
    // after it is refused too.
    const toldToken = React.useRef(false);
    // Counted as each write of the settings starts (a change, an upload, the
    // reset): a description read before one does not put back the settings
    // it changed.
    const writesStarted = React.useRef(0);
    const mounted = React.useRef(true);
    const serial = useSerialRequests();

    const visibleRef = React.useRef(visible);
    visibleRef.current = visible;
    const canRenderRef = React.useRef(canRender);
    canRenderRef.current = canRender;
    const liveRef = React.useRef(live);
    liveRef.current = live;
    // The newest live payload asked for: an older answer is not shown.
    const liveAsked = React.useRef(0);
    const liveTemplateRef = React.useRef<string | null>(null);
    liveTemplateRef.current = live ? chosen?.id ?? null : null;
    // Loaded, and the catalog read (or given up on): what the template is
    // decides how it is previewed.
    const previewable = loaded && packagesSettled;
    const loadedRef = React.useRef(previewable);
    loadedRef.current = previewable;
    const renderRef = React.useRef(render);
    renderRef.current = render;
    const previewTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const describeTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    // The game the page shows (the last description's).
    const gameRef = React.useRef<string | null>(null);

    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            [previewTimer, describeTimer].forEach((timer) => {
                if (timer.current !== null) {
                    clearTimeout(timer.current);
                    timer.current = null;
                }
            });
        };
    }, []);

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("thumbnail_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    const apply = React.useCallback((description: Description,
                                     withSettings: boolean) => {
        const codename = description.game?.codename ?? null;
        const newGame = codename !== gameRef.current;
        gameRef.current = codename;
        setGame(description.game);
        setTemplates(description.templates);
        setVideoTitle(description.videoTitle);
        setCanRender(description.canRender);
        if (withSettings) {
            setSettings(description.settings);
        } else if (newGame && codename !== null
                   && description.settings.game[codename]) {
            // The settings were changed meanwhile and are not put back, but
            // a game just shown has none of this page's writes yet (its card
            // showed the game before): its own are taken.
            const own = description.settings.game[codename];
            setSettings((current) => current && {
                ...current, game: {...current.game, [codename]: own}});
        }
    }, []);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Description>>("/api/v1/thumbnail", () => live)
            .then((body) => {
                if (live) {
                    apply(body.data, true);
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
    }, [loads, apply]);

    const reload = React.useCallback(() => {
        setLoadFailed(false);
        setLoads((count) => count + 1);
    }, []);

    // The title (and the game, the settings it brings) again.
    const describe = React.useCallback(() => {
        const since = writesStarted.current;
        apiGetJson<Envelope<Description>>("/api/v1/thumbnail").then((body) => {
            if (mounted.current) {
                apply(body.data, writesStarted.current === since);
            }
        }, (error) => console.error(error));
    }, [apply]);

    // A failed request told: the token once, else what `refusal` makes of
    // its status (or the generic failure).
    const report = React.useCallback((error: unknown, refusal?: Refusal) => {
        console.error(error);
        const status = apiErrorStatus(error);
        if (status === 401 || status === 403) {
            if (!toldToken.current) {
                toldToken.current = true;
                showSnackbar(i18n.t("stream_token_required"), "error");
            }
        } else {
            const message = refusal ? refusal(status)
                : i18n.t("request_failed");
            if (message) {
                showSnackbar(message, "error");
            }
        }
    }, []);

    const write = React.useCallback(<T,>(request: () => Promise<T>,
                                         refusal?: Refusal) =>
        serial(request).catch((error: unknown) => {
            report(error, refusal);
            throw error;
        }), [serial, report]);

    // 503: the shell did not draw -- the headless one cannot, the desktop's
    // may be shutting down. Not a snackbar: the description is read again and
    // its canRender said (the headless one's on the page), rather than
    // taking the page's renders away for good over one refusal.
    const cannotRender: Refusal = React.useCallback((status) => {
        if (status === 503) {
            describe();
            return null;
        }
        return i18n.t("request_failed");
    }, [describe]);

    const startPreview = React.useCallback(() => {
        // Queued behind the writes before it: it draws what they saved.
        write(() => apiPost("/api/v1/thumbnail:preview", {}, WRITE_OPTIONS),
              cannotRender)
            .then((text) => {
                const revision = (JSON.parse(text) as
                    Envelope<{renderRevision: number}>).data.renderRevision;
                setPreviewAsked((asked) => Math.max(asked, revision));
            }, () => undefined);
    }, [write, cannotRender]);

    // The live preview's payload, read now: its answer is the next render.
    const startLive = React.useCallback(() => {
        const asked = ++liveAsked.current;
        apiGetJson<Envelope<LivePayload>>("/api/v1/thumbnail/payload").then(
            (body) => {
                if (!mounted.current || asked !== liveAsked.current) {
                    return;
                }
                const data = body.data;
                if (data.contract !== LIVE_CONTRACT) {
                    // The catalog said otherwise a moment ago: the renderer.
                    setLiveRefused(data.templateId);
                    return;
                }
                setLivePreview({
                    design: {
                        id: data.templateId,
                        kind: "thumbnail",
                        displayName: "",
                        entry: data.entry,
                        folder: data.folder,
                        contract: data.contract,
                        size: data.size,
                    },
                    size: data.size,
                    render: {payload: data.payload, serial: asked},
                    omitted: Array.isArray(data.omitted) ? data.omitted : [],
                });
            }, (error: unknown) => {
                if (!mounted.current || asked !== liveAsked.current) {
                    return;
                }
                const status = apiErrorStatus(error);
                if ((status === 401 || status === 403)
                        && liveTemplateRef.current !== null) {
                    // The image instead (READ); the token is told once.
                    setLiveRefused(liveTemplateRef.current);
                }
                report(error);
            });
    }, [report]);

    const requestPreview = React.useCallback(() => {
        // Hidden: the reveal draws it. Not loaded yet: the first load does.
        // The live preview needs no renderer.
        if (!visibleRef.current || !loadedRef.current
                || (!canRenderRef.current && !liveRef.current)) {
            return;
        }
        if (previewTimer.current !== null) {
            clearTimeout(previewTimer.current);
        }
        previewTimer.current = setTimeout(() => {
            previewTimer.current = null;
            if (!visibleRef.current) {
                return;  // Hidden meanwhile: drawn once shown.
            }
            if (liveRef.current) {
                startLive();
            } else if (canRenderRef.current) {
                startPreview();
            }
        }, PREVIEW_DEBOUNCE_MS);
    }, [startPreview, startLive]);

    const requestDescribe = React.useCallback(() => {
        if (describeTimer.current !== null) {
            clearTimeout(describeTimer.current);
        }
        describeTimer.current = setTimeout(() => {
            describeTimer.current = null;
            describe();
        }, PREVIEW_DEBOUNCE_MS);
    }, [describe]);

    // The first preview, once the page has its settings; and again when the
    // template chosen is previewed the other way (or is another live one).
    const liveTemplate = live ? chosen?.id ?? null : null;
    React.useEffect(() => {
        if (previewable) {
            requestPreview();
        }
    }, [previewable, requestPreview, liveTemplate]);

    // Shown again: drawn and titled again, changed or not (the header). Not
    // loaded yet: the first load draws.
    const seenReveals = React.useRef(reveals);
    React.useEffect(() => {
        if (seenReveals.current === reveals) {
            return;
        }
        seenReveals.current = reveals;
        if (loadedRef.current) {
            requestDescribe();
            requestPreview();
        }
    }, [reveals, requestDescribe, requestPreview]);

    // Hidden: a preview not asked for yet is dropped (the reveal draws).
    React.useEffect(() => {
        if (!visible && previewTimer.current !== null) {
            clearTimeout(previewTimer.current);
            previewTimer.current = null;
        }
    }, [visible]);

    // What the picture shows changed in the state: the match, the
    // tournament (its banner and colours among it), the thumbnail's colours.
    const seen = React.useRef({signature, colors});
    React.useEffect(() => {
        const before = seen.current;
        seen.current = {signature, colors};
        if (!loadedRef.current) {
            return;
        }
        if (before.signature !== signature) {
            if (visibleRef.current) {
                requestDescribe();
            }
            requestPreview();
        } else if (before.colors !== colors) {
            requestPreview();
        }
    }, [signature, colors, requestDescribe, requestPreview]);

    // The image: the newest run that ended with one (a generation draws the
    // preview too).
    React.useEffect(() => {
        if (render.status === "done" && render.revision > 0) {
            setImageRevision(render.revision);
        }
    }, [render.status, render.revision]);

    // A generation of this page's ended: said, and kept on the status line.
    React.useEffect(() => {
        if (!generating || render.revision < generating.revision) {
            return;
        }
        let outcome: GenerateResult;
        if (render.revision === generating.revision) {
            if (render.status !== "done" && render.status !== "error") {
                return;
            }
            outcome = render.status === "done"
                ? {status: "done", path: render.path}
                : {status: "error", error: render.error};
        } else {
            // A later run already (two state updates seen as one): the
            // generation ended before it, with a new file or without.
            outcome = render.path && render.path !== generating.from
                ? {status: "done", path: render.path}
                : {status: "error", error: ""};
        }
        setGenerating(null);
        setResult(outcome);
        if (outcome.status === "done") {
            showSnackbar(i18n.t("thumbnail_saved", {path: outcome.path}),
                         "success");
        } else {
            showSnackbar(errorText(outcome.error), "error");
        }
    }, [generating, render.revision, render.status, render.path,
        render.error]);

    const change = React.useCallback((next: SettingsChange) => {
        writesStarted.current += 1;
        return write(() => apiPatch("/api/v1/thumbnail", next, WRITE_OPTIONS))
            .then((text) => {
                setSettings((JSON.parse(text) as
                    Envelope<ThumbnailSettings>).data);
                requestPreview();
            });
    }, [write, requestPreview]);

    const sendColors = React.useCallback(
        (next: Partial<Record<ColorKind, string>>) =>
            write(() => apiPatch(`/api/v1/colors/${FEATURE}`, next,
                                 WRITE_OPTIONS)),
        [write]);

    const upload = React.useCallback((file: File) => {
        if (file.size > IMAGE_UPLOAD_MAX_BYTES) {
            showSnackbar(i18n.t("thumbnail_background_too_large",
                                {max: IMAGE_UPLOAD_MAX_BYTES / 1024 / 1024}),
                         "error");
            return;
        }
        const refusal: Refusal = (status) => {
            if (status === 413) {
                return i18n.t("thumbnail_background_too_large",
                              {max: IMAGE_UPLOAD_MAX_BYTES / 1024 / 1024});
            }
            if (status === 400) {
                return i18n.t("thumbnail_background_invalid");
            }
            if (status === 500) {
                return i18n.t("thumbnail_background_save_failed");
            }
            return i18n.t("request_failed");
        };
        // Not one of the queued writes: an upload of up to a minute does
        // not hold the page's other writes behind it (S4e review).
        writesStarted.current += 1;
        setUploading((count) => count + 1);
        withDeadline(async () => apiPost("/api/v1/thumbnail/background",
                                         {image: await readDataUrl(file)},
                                         {timeoutMs: UPLOAD_TIMEOUT_MS}),
                     UPLOAD_TIMEOUT_MS)
            .then((text) => {
                if (mounted.current) {
                    setSettings((JSON.parse(text) as
                        Envelope<ThumbnailSettings>).data);
                    requestPreview();
                }
            }, (error: unknown) => report(error, refusal))
            .finally(() => {
                if (mounted.current) {
                    setUploading((count) => count - 1);
                }
            });
    }, [report, requestPreview]);

    const reset = React.useCallback(() => {
        writesStarted.current += 1;
        write(() => apiPost("/api/v1/thumbnail:reset", {confirm: true},
                            WRITE_OPTIONS))
            .then((text) => {
                setSettings((JSON.parse(text) as
                    Envelope<ThumbnailSettings>).data);
                showSnackbar(i18n.t("thumbnail_reset_done"), "success");
                requestPreview();
            }, () => undefined);
    }, [write, requestPreview]);

    const generate = React.useCallback(() => {
        // The button is disabled meanwhile; a second click in the same
        // frame is not a second generation.
        if (askingRef.current) {
            return;
        }
        askingRef.current = true;
        setAsking(true);
        const from = renderRef.current.path;
        write(() => apiPost("/api/v1/thumbnail:generate", {}, WRITE_OPTIONS),
              (status) => (status === 409 ? null : cannotRender(status)))
            .then((text) => {
                const revision = (JSON.parse(text) as
                    Envelope<{renderRevision: number}>).data.renderRevision;
                setResult(null);
                setGenerating({revision, from});
            }, (error: unknown) => {
                if (apiErrorStatus(error) === 409) {
                    showSnackbar(i18n.t("thumbnail_busy"), "info");
                }
            })
            .finally(() => {
                askingRef.current = false;
                if (mounted.current) {
                    setAsking(false);
                }
            });
    }, [write, cannotRender]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("thumbnail_reload")}
                        </Button>
                    }
                >
                    {i18n.t("thumbnail_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!loaded || !settings) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("thumbnail_loading")}</Typography>
            </Box>
        );
    }

    const {tournamentTheme, tournamentAccent} = colors;
    const tournament = tournamentTheme && tournamentAccent
        ? {theme: tournamentTheme, accent: tournamentAccent} : null;
    const previewing = canRender && (render.status === "running"
        || previewAsked > render.revision);
    const previewError = render.status === "error"
        && render.revision === previewAsked ? render.error : "";
    // The template's errors, when this page's preview or generation failed
    // on them.
    const templateFailed = render.status === "error"
        && render.error === "template_error"
        && (previewError === "template_error"
            || (result?.status === "error" && result.error === "template_error"));
    const templateReport = templateFailed
        ? reportFromErrors(renderErrors, LIVE_CONTRACT) : null;
    const templateName = chosen
        ? templateLabel(chosen, "thumbnail_template_", language)
        : settings.template_id;
    const checkDesign: LayoutEntry = {
        id: chosen?.id ?? settings.template_id,
        kind: "thumbnail",
        displayName: templateName,
        entry: chosen?.entry ?? "thumbnail.html",
        folder: chosen?.folder,
        contract: chosen?.contract,
    };
    const shownLive = live && livePreview !== null
        && livePreview.design.id === chosen?.id ? livePreview : null;
    const liveDesign = shownLive
        ? {...shownLive.design, displayName: templateName} : null;
    const livePane = live ? (
        liveDesign && shownLive ? (
            <DesignPane
                designs={[liveDesign]}
                design={liveDesign}
                onSelect={() => undefined}
                selectable={false}
                renderPayload={shownLive.render}
                size={shownLive.size}
                // Its page again, and the payload: after an edit of the
                // template's files.
                onReload={startLive}
            />
        ) : (
            <Box sx={{display: "flex", gap: 1, alignItems: "center"}}>
                <CircularProgress size={16}/>
                <Typography variant="body2">
                    {i18n.t("thumbnail_previewing")}
                </Typography>
            </Box>
        )
    ) : undefined;
    // Each image the preview cannot show, in its own words.
    const liveNote = shownLive && shownLive.omitted.length > 0
        ? shownLive.omitted.map((key) => i18n.t(`thumbnail_live_omitted_${key}`,
            {defaultValue: ""})).filter(Boolean).join(" ") || undefined
        : undefined;

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
                    <DesignCard settings={settings} choices={choices}
                                change={change} upload={upload}/>
                    <ItemsCard settings={settings} change={change}/>
                    <CharactersCard
                        key={game?.codename ?? ""}
                        game={game}
                        settings={game ? settings.game[game.codename]
                            : undefined}
                        change={change}
                    />
                    <FeatureColorCard
                        title={i18n.t("thumbnail_colors")}
                        theme={colors.theme ?? tournamentTheme ?? ""}
                        accent={colors.accent ?? tournamentAccent ?? ""}
                        tournament={tournament}
                        send={sendColors}
                    />
                    <ResetButton reset={reset}/>
                </Stack>
            </Box>
            <Box sx={{flex: 1, minWidth: 0, overflowY: {md: "auto"}, p: 2}}>
                <Stack spacing={2}>
                    <PreviewCard
                        canRender={canRender}
                        desktop={desktop}
                        render={render}
                        imageRevision={imageRevision}
                        previewing={previewing && !live}
                        previewError={live ? "" : previewError}
                        generating={generating !== null}
                        blocked={asking || uploading > 0}
                        result={result}
                        generate={generate}
                        live={livePane}
                        liveNote={liveNote}
                        check={templateReport ? (
                            <LayoutCheckPanel design={checkDesign}
                                              report={templateReport}
                                              notReady={false}/>
                        ) : undefined}
                    />
                    <VideoTitleCard auto={videoTitle}/>
                </Stack>
            </Box>
        </Box>
    );
}

export default function ThumbnailPage() {
    return (
        <FeedbackProvider>
            <ThumbnailContent/>
        </FeedbackProvider>
    );
}
