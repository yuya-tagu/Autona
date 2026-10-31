import React from "react";
import {useSelector} from "react-redux";
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
import {apiDelete, apiErrorStatus, apiPost, apiPut} from "../api";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {
    WRITE_OPTIONS,
    getWithRetry,
    useSerialRequests,
    withDeadline,
} from "../PanelShell/requests";
import {
    DestinationCard,
    ORG_IMAGE_MAX_BYTES,
    OrgAssetsCard,
    OrgKind,
    StreamChannel,
    StreamThumbnail,
    ThumbnailCard,
    ThumbnailOptions,
    TitleCard,
} from "./Cards";
import {ChannelForm} from "./Dialogs";

// The 配信設定 panel as a React page (ADR-005 S4e), /web/stream: the
// destination, the broadcast title, the stream org's images and the stream
// thumbnail. Shown in the desktop app's shared web view and in a browser
// alike. It writes through /api/v1 only -- core's StreamService does the
// work -- and renders the state it gets over Socket.IO (stream.*). The
// channels and the thumbnail options are settings, not state: read when
// the page loads and taken from each write's answer.

// An upload of up to 20 MB (27 MB of base64) over the LAN may take longer
// than a write. Not one of the page's queued writes: the title and the
// rest are not held behind it (S4e review).
export const UPLOAD_TIMEOUT_MS = 60000;

interface Envelope<T> {
    ok: boolean;
    data: T;
}

interface StreamState {
    url?: string;
    title?: string;
    titleAuto?: string | null;
    orgIconVersion?: number;
    orgThumbVersion?: number;
    thumbnail?: Partial<StreamThumbnail>;
}

const IDLE: StreamThumbnail = {status: "idle", path: "", error: "",
                               revision: 0};

function readThumbnail(raw: Partial<StreamThumbnail> | undefined): StreamThumbnail {
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

function readDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

// What a refused write says; null: nothing (the caller tells it).
type Refusal = (status: number | undefined) => string | null;

function StreamContent() {
    const {language} = usePanelShell();
    const stream = useSelector((state: ReduxState) =>
        (state.autonaState.autonaState as unknown as {
            stream?: StreamState;
        }).stream);
    const [channels, setChannels] = React.useState<StreamChannel[] | null>(null);
    const [options, setOptions] = React.useState<ThumbnailOptions | null>(null);
    const [loadFailed, setLoadFailed] = React.useState(false);
    // Bumped by the reload button: loads the page's settings again.
    const [loads, setLoads] = React.useState(0);
    // A refusal for want of the access token (another computer opened the
    // page without it) is told once: every write after it is refused too.
    const toldToken = React.useRef(false);
    const serial = useSerialRequests();

    React.useEffect(() => {
        const previous = document.title;
        document.title = `Autona ${i18n.t("stream_title")}`;
        return () => {
            document.title = previous;
        };
    }, [language]);

    React.useEffect(() => {
        let live = true;
        const isLive = () => live;
        Promise.all([
            getWithRetry<Envelope<{channels: StreamChannel[]}>>(
                "/api/v1/stream/channels", isLive),
            getWithRetry<Envelope<ThumbnailOptions>>(
                "/api/v1/stream/thumbnail/options", isLive),
        ]).then(([channelsBody, optionsBody]) => {
            if (live) {
                setChannels(channelsBody.data.channels);
                setOptions(optionsBody.data);
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

    // A channel index the server no longer has (another client removed it):
    // the list is read again, so the page shows what there is.
    const channelGone: Refusal = React.useCallback((status) => {
        if (status === 404) {
            setLoads((count) => count + 1);
            return i18n.t("stream_channel_missing");
        }
        return i18n.t("request_failed");
    }, []);

    const sendStream = React.useCallback(
        (fields: {url?: string; title?: string}) =>
            write(() => apiPut("/api/v1/stream", fields, WRITE_OPTIONS)),
        [write]);
    const sendUrl = React.useCallback(
        (url: string) => sendStream({url}), [sendStream]);
    const sendTitle = React.useCallback(
        (title: string) => sendStream({title}), [sendStream]);

    const addChannel = React.useCallback((form: ChannelForm) =>
        write(() => apiPost("/api/v1/stream/channels", form, WRITE_OPTIONS))
            .then((text) => {
                const {index, channels: saved} = (JSON.parse(text) as
                    Envelope<{index: number; channels: StreamChannel[]}>).data;
                setChannels(saved);
                // The new channel becomes the destination, as in the Qt
                // panel.
                const url = saved[index]?.url;
                if (url) {
                    sendUrl(url).catch(() => undefined);
                }
                return true;
            }, () => false), [write, sendUrl]);
    const updateChannel = React.useCallback((index: number, form: ChannelForm) =>
        write(() => apiPut(`/api/v1/stream/channels/${index}`, form,
                           WRITE_OPTIONS), channelGone)
            .then((text) => {
                // A destination that was its URL follows it (core).
                setChannels((JSON.parse(text) as
                    Envelope<{channels: StreamChannel[]}>).data.channels);
                return true;
            }, () => false), [write, channelGone]);
    const deleteChannel = React.useCallback((index: number) =>
        write(() => apiDelete(`/api/v1/stream/channels/${index}`,
                              WRITE_OPTIONS), channelGone)
            .then((text) => {
                setChannels((JSON.parse(text) as
                    Envelope<{channels: StreamChannel[]}>).data.channels);
                return true;
            }, () => false), [write, channelGone]);

    const upload = React.useCallback((kind: OrgKind, file: File) => {
        if (file.size > ORG_IMAGE_MAX_BYTES) {
            showSnackbar(i18n.t("stream_org_too_large",
                                {max: ORG_IMAGE_MAX_BYTES / 1024 / 1024}),
                         "error");
            return;
        }
        const refusal: Refusal = (status) => {
            if (status === 413) {
                return i18n.t("stream_org_too_large",
                              {max: ORG_IMAGE_MAX_BYTES / 1024 / 1024});
            }
            if (status === 400) {
                return i18n.t("stream_org_invalid");
            }
            if (status === 500) {
                return i18n.t("stream_org_save_failed");
            }
            return i18n.t("request_failed");
        };
        // The preview follows the version the import publishes.
        withDeadline(async () => apiPut(`/api/v1/stream/org/${kind}`,
                                        {image: await readDataUrl(file)},
                                        {timeoutMs: UPLOAD_TIMEOUT_MS}),
                     UPLOAD_TIMEOUT_MS)
            .catch((error: unknown) => report(error, refusal));
    }, [report]);

    const resetOrg = React.useCallback(() => {
        write(() => apiDelete("/api/v1/stream/org", WRITE_OPTIONS))
            .then((text) => {
                // A thumbnail background that was the org's is the banner
                // again (core).
                setOptions((JSON.parse(text) as
                    Envelope<{thumbnailOptions: ThumbnailOptions}>)
                    .data.thumbnailOptions);
            }, () => undefined);
    }, [write]);

    const setOption = React.useCallback(
        (change: Partial<ThumbnailOptions>) =>
            write(() => apiPut("/api/v1/stream/thumbnail/options", change,
                               WRITE_OPTIONS))
                .then((text) => {
                    setOptions((JSON.parse(text) as
                        Envelope<ThumbnailOptions>).data);
                }),
        [write]);

    const generate = React.useCallback(() => {
        // Accepted (202): the run reports itself in stream.thumbnail.
        write(() => apiPost("/api/v1/stream/thumbnail", {}, WRITE_OPTIONS),
              (status) => (status === 409 ? null : i18n.t("request_failed")))
            .catch((error: unknown) => {
                if (apiErrorStatus(error) === 409) {
                    showSnackbar(i18n.t("stream_thumbnail_running"), "info");
                }
            });
    }, [write]);

    if (loadFailed) {
        return (
            <Box component="main" sx={{p: 3}}>
                <Alert
                    severity="error"
                    action={
                        <Button color="inherit" size="small" onClick={reload}>
                            {i18n.t("stream_reload")}
                        </Button>
                    }
                >
                    {i18n.t("stream_load_failed")}
                </Alert>
            </Box>
        );
    }
    if (!channels || !options) {
        return (
            <Box component="main" sx={{p: 3, display: "flex", gap: 1,
                                       alignItems: "center"}}>
                <CircularProgress size={20}/>
                <Typography>{i18n.t("stream_loading")}</Typography>
            </Box>
        );
    }

    return (
        <Box component="main"
             sx={{minHeight: "100vh", bgcolor: "background.default", p: 2}}>
            <Stack spacing={2} sx={{maxWidth: 720, mx: "auto"}}>
                <DestinationCard
                    channels={channels}
                    url={stream?.url ?? ""}
                    choose={sendUrl}
                    sendUrl={sendUrl}
                    addChannel={addChannel}
                    updateChannel={updateChannel}
                    deleteChannel={deleteChannel}
                />
                <TitleCard
                    title={stream?.title ?? ""}
                    titleAuto={stream?.titleAuto}
                    send={sendTitle}
                />
                <OrgAssetsCard
                    iconVersion={stream?.orgIconVersion ?? 0}
                    thumbVersion={stream?.orgThumbVersion ?? 0}
                    upload={upload}
                    reset={resetOrg}
                />
                <ThumbnailCard
                    options={options}
                    thumbnail={readThumbnail(stream?.thumbnail)}
                    setOption={setOption}
                    generate={generate}
                />
            </Stack>
        </Box>
    );
}

export default function StreamPage() {
    return (
        <FeedbackProvider>
            <StreamContent/>
        </FeedbackProvider>
    );
}
