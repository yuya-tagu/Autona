import React from "react";
import {
    Box,
    Button,
    Card,
    CardContent,
    Checkbox,
    FormControl,
    FormControlLabel,
    FormLabel,
    MenuItem,
    Radio,
    RadioGroup,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {useStateField} from "../PanelShell/useStateField";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {canCopyImage, copyImage, copyText} from "../PanelShell/clipboard";
import {usePanelShell} from "../PanelShell/context";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {ChannelDialog, ChannelForm, ConfirmDialog} from "./Dialogs";
import {titleIsAuto, typedTitleWins} from "./titleRule";

// The Stream page's four cards (ADR-005 S4e), the Qt 配信設定 panel's four
// groups: the destination, the title, the stream org's images and the
// stream thumbnail. They draw the state (stream.*) and the page's settings
// (the channels, the thumbnail options) and ask the page to write.

// A saved channel (GET /api/v1/stream/channels).
export interface StreamChannel {
    name: string;
    url: string;
}

// The thumbnail options (GET /api/v1/stream/thumbnail/options).
export interface ThumbnailOptions {
    source: "banner" | "org";
    style: "center" | "netflix";
    overlayOrgIcon: boolean;
}

// stream.thumbnail: the generator's last run (core StreamService).
export interface StreamThumbnail {
    status: "idle" | "running" | "done" | "error";
    path: string;
    error: string;
    revision: number;
}

export type OrgKind = "icon" | "thumbnail";

// The largest image the server takes (src/AutonaApiV1.py
// ORG_IMAGE_MAX_BYTES; tests/test_api_v1_stream.py holds the two equal): a
// larger one is refused here before it is sent.
export const ORG_IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const ORG_IMAGE_ACCEPT = "image/png,image/jpeg,image/bmp,.png,.jpg,.jpeg,.bmp";

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

// ── 配信先 ───────────────────────────────────────────────────────────────

type ChannelDialogState =
    | {mode: "add"; initial: ChannelForm}
    | {mode: "edit"; index: number; initial: ChannelForm}
    | null;

const EMPTY_CHANNEL: ChannelForm = {name: "", url: ""};

export function DestinationCard({channels, url, choose, sendUrl, addChannel,
                                 updateChannel, deleteChannel}: {
    channels: StreamChannel[];
    url: string;
    // The destination: a saved channel's URL, or "" (none).
    choose: (url: string) => Promise<unknown>;
    sendUrl: (url: string) => Promise<unknown>;
    addChannel: (form: ChannelForm) => Promise<boolean>;
    updateChannel: (index: number, form: ChannelForm) => Promise<boolean>;
    deleteChannel: (index: number) => Promise<boolean>;
}) {
    // The channel the destination is: the first with its URL (the Qt
    // combo's findData). A URL typed by hand is none of them.
    const matched = channels.findIndex((channel) => channel.url === url);
    const sendChoice = React.useCallback((choice: string) => choose(
        choice === "" ? "" : channels[Number(choice)]?.url ?? ""),
    [choose, channels]);
    const [choice, setChoice] = useStateChoice(
        matched >= 0 ? String(matched) : "", sendChoice);
    const {pick, replace, ...urlField} = useStateField(url, sendUrl);
    const [dialog, setDialog] = React.useState<ChannelDialogState>(null);
    const title = i18n.t("stream_destination");

    return (
        <CardFrame title={title}>
            <Typography variant="body2" color="text.secondary">
                {i18n.t("stream_destination_note")}
            </Typography>
            <Stack direction="row" spacing={1} alignItems="center">
                <TextField
                    select
                    size="small"
                    label={i18n.t("stream_channel")}
                    value={choice}
                    onChange={(event) => setChoice(event.target.value)}
                    // "None" is a choice to show, not an empty field.
                    slotProps={{select: {displayEmpty: true},
                                inputLabel: {shrink: true}}}
                    sx={{flex: 1, minWidth: 0}}
                >
                    <MenuItem value="">
                        <em>{i18n.t("stream_channel_none")}</em>
                    </MenuItem>
                    {channels.map((channel, index) => (
                        <MenuItem key={`${index}:${channel.name}`}
                                  value={String(index)}>
                            {channel.name}
                        </MenuItem>
                    ))}
                </TextField>
                <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setDialog({mode: "add",
                                              initial: EMPTY_CHANNEL})}
                >
                    {i18n.t("stream_channel_add")}
                </Button>
                <Button
                    variant="outlined"
                    size="small"
                    // A saved channel only: a typed URL has none behind it.
                    disabled={matched < 0}
                    onClick={() => setDialog({
                        mode: "edit", index: matched,
                        initial: {...channels[matched]}})}
                >
                    {i18n.t("stream_channel_edit")}
                </Button>
            </Stack>
            <TextField
                size="small"
                label={i18n.t("stream_url")}
                placeholder="https://"
                {...urlField}
            />
            <ChannelDialog
                open={dialog !== null}
                title={dialog?.mode === "edit"
                    ? i18n.t("stream_channel_edit_title")
                    : i18n.t("stream_channel_add_title")}
                initial={dialog?.initial ?? EMPTY_CHANNEL}
                onSave={(form) => (dialog?.mode === "edit"
                    ? updateChannel(dialog.index, form)
                    : addChannel(form))}
                onDelete={dialog?.mode === "edit"
                    ? () => deleteChannel(dialog.index) : undefined}
                onClose={() => setDialog(null)}
            />
        </CardFrame>
    );
}

// ── 配信タイトル ─────────────────────────────────────────────────────────

export function TitleCard({title, titleAuto, send}: {
    title: string;
    titleAuto: string | null | undefined;
    send: (title: string) => Promise<unknown>;
}) {
    // core's rule for a typed title (titleRule.ts): it beats an auto title
    // that lands while the operator types, but not another writer's own.
    const autoRef = React.useRef(titleAuto);
    autoRef.current = titleAuto;
    const wins = React.useCallback(
        (typed: string, shown: string, current: string) =>
            typedTitleWins(typed, shown, current, autoRef.current), []);
    const {pick, replace, ...field} = useStateField(title, send, {wins});
    const heading = i18n.t("stream_title_card");

    const copy = async () => {
        const text = field.value.trim();
        if (!text) {
            return;
        }
        if (await copyText(text)) {
            showSnackbar(i18n.t("stream_copied"), "success");
        } else {
            showSnackbar(i18n.t("stream_copy_failed"), "error");
        }
    };

    return (
        <CardFrame title={heading}>
            <Stack direction="row" spacing={1} alignItems="flex-start">
                <TextField
                    size="small"
                    label={i18n.t("stream_title_field")}
                    helperText={titleIsAuto(title, titleAuto)
                        ? i18n.t("stream_title_auto")
                        : i18n.t("stream_title_typed")}
                    {...field}
                    sx={{flex: 1, minWidth: 0}}
                />
                <Button variant="contained" size="small" onClick={copy}
                        disabled={!field.value.trim()} sx={{mt: "4px"}}>
                    {i18n.t("stream_copy")}
                </Button>
            </Stack>
        </CardFrame>
    );
}

// ── 配信団体アイコン / サムネイル ───────────────────────────────────────

function OrgImageRow({kind, label, version, width, height, upload}: {
    kind: OrgKind;
    label: string;
    // stream.org*Version: 0 (or none) while nothing is registered.
    version: number;
    width: number;
    height: number;
    upload: (kind: OrgKind, file: File) => void;
}) {
    const input = React.useRef<HTMLInputElement>(null);
    const file = kind === "icon" ? "stream_org_icon.png"
        : "stream_org_thumbnail.png";
    return (
        <Stack direction="row" spacing={2} alignItems="center">
            <Box
                sx={{width, height, flexShrink: 0, display: "flex",
                     alignItems: "center", justifyContent: "center",
                     border: 1, borderColor: "divider", borderRadius: 1,
                     overflow: "hidden"}}
            >
                {version > 0 ? (
                    <Box component="img" alt={label}
                         src={`${BASE_URL}/layout/${file}?v=${version}`}
                         sx={{maxWidth: "100%", maxHeight: "100%"}}/>
                ) : (
                    <Typography variant="caption" color="text.secondary">
                        {i18n.t("stream_org_none")}
                    </Typography>
                )}
            </Box>
            <Stack spacing={0.5} sx={{minWidth: 0}}>
                <Typography variant="body2">{label}</Typography>
                <Box>
                    <Button variant="outlined" size="small"
                            aria-label={`${label}: ${i18n.t("stream_org_choose")}`}
                            onClick={() => input.current?.click()}>
                        {i18n.t("stream_org_choose")}
                    </Button>
                </Box>
                <input
                    ref={input}
                    type="file"
                    accept={ORG_IMAGE_ACCEPT}
                    hidden
                    data-testid={`stream-org-${kind}-file`}
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

export function OrgAssetsCard({iconVersion, thumbVersion, upload, reset}: {
    iconVersion: number;
    thumbVersion: number;
    upload: (kind: OrgKind, file: File) => void;
    reset: () => void;
}) {
    const [confirming, setConfirming] = React.useState(false);
    return (
        <CardFrame title={i18n.t("stream_org")}>
            <Typography variant="body2" color="text.secondary">
                {i18n.t("stream_org_note")}
            </Typography>
            <OrgImageRow kind="icon" label={i18n.t("stream_org_icon")}
                         version={iconVersion} width={60} height={60}
                         upload={upload}/>
            <OrgImageRow kind="thumbnail"
                         label={i18n.t("stream_org_thumbnail")}
                         version={thumbVersion} width={160} height={90}
                         upload={upload}/>
            <Box>
                <Button
                    variant="outlined"
                    color="error"
                    size="small"
                    disabled={!(iconVersion > 0 || thumbVersion > 0)}
                    onClick={() => setConfirming(true)}
                >
                    {i18n.t("stream_org_reset")}
                </Button>
            </Box>
            <ConfirmDialog
                open={confirming}
                title={i18n.t("stream_org_reset")}
                message={i18n.t("stream_org_reset_confirm")}
                confirm={i18n.t("stream_org_reset_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    reset();
                }}
                onClose={() => setConfirming(false)}
            />
        </CardFrame>
    );
}

// ── 配信サムネイル生成 ───────────────────────────────────────────────────

// Where the made thumbnail is served: stream.thumbnail.path is relative to
// the app's folder (out/stream/...), which the server serves as it is. The
// run's revision tells two runs apart that wrote the same file name.
export function thumbnailUrl(thumbnail: StreamThumbnail): string {
    const path = thumbnail.path.replace(/^\.?\//, "");
    return `${BASE_URL}/${path}?v=${thumbnail.revision}`;
}

function thumbnailStatus(thumbnail: StreamThumbnail): string {
    switch (thumbnail.status) {
        case "running":
            return i18n.t("stream_thumbnail_running");
        case "done":
            return i18n.t("stream_thumbnail_saved", {path: thumbnail.path});
        case "error":
            if (thumbnail.error === "no_org_thumbnail"
                    || thumbnail.error === "no_banner"
                    || thumbnail.error === "cancelled") {
                return i18n.t(`stream_thumbnail_${thumbnail.error}`);
            }
            return i18n.t("stream_thumbnail_error", {error: thumbnail.error});
        default:
            return "";
    }
}

export function ThumbnailCard({options, thumbnail, setOption, generate}: {
    options: ThumbnailOptions;
    thumbnail: StreamThumbnail;
    setOption: (change: Partial<ThumbnailOptions>) => Promise<unknown>;
    generate: () => void;
}) {
    const [source, chooseSource] = useStateChoice(
        options.source, React.useCallback(
            (value: ThumbnailOptions["source"]) => setOption({source: value}),
            [setOption]));
    const [style, chooseStyle] = useStateChoice(
        options.style, React.useCallback(
            (value: ThumbnailOptions["style"]) => setOption({style: value}),
            [setOption]));
    const [overlay, chooseOverlay] = useStateChoice(
        options.overlayOrgIcon, React.useCallback(
            (value: boolean) => setOption({overlayOrgIcon: value}),
            [setOption]));
    const running = thumbnail.status === "running";
    const made = Boolean(thumbnail.path);
    const url = made ? thumbnailUrl(thumbnail) : "";
    const status = thumbnailStatus(thumbnail);
    const preview = i18n.t("stream_thumbnail_preview");
    const {desktop} = usePanelShell();

    const copy = () => {
        copyImage(url).then(
            () => showSnackbar(i18n.t("stream_thumbnail_copied"), "success"),
            (error) => {
                console.error(error);
                showSnackbar(i18n.t("stream_copy_failed"), "error");
            });
    };

    return (
        <CardFrame title={i18n.t("stream_thumbnail")}>
            <FormControl>
                <FormLabel id="stream-thumbnail-source">
                    {i18n.t("stream_thumbnail_source")}
                </FormLabel>
                <RadioGroup
                    row
                    aria-labelledby="stream-thumbnail-source"
                    value={source}
                    onChange={(event) => chooseSource(
                        event.target.value as ThumbnailOptions["source"])}
                >
                    <FormControlLabel value="banner" control={<Radio/>}
                                      label={i18n.t("stream_thumbnail_source_banner")}/>
                    <FormControlLabel value="org" control={<Radio/>}
                                      label={i18n.t("stream_thumbnail_source_org")}/>
                </RadioGroup>
            </FormControl>
            <FormControl>
                <FormLabel id="stream-thumbnail-style">
                    {i18n.t("stream_thumbnail_style")}
                </FormLabel>
                <RadioGroup
                    row
                    aria-labelledby="stream-thumbnail-style"
                    value={style}
                    onChange={(event) => chooseStyle(
                        event.target.value as ThumbnailOptions["style"])}
                >
                    <FormControlLabel value="center" control={<Radio/>}
                                      label={i18n.t("stream_thumbnail_style_center")}/>
                    <FormControlLabel value="netflix" control={<Radio/>}
                                      label={i18n.t("stream_thumbnail_style_netflix")}/>
                </RadioGroup>
            </FormControl>
            <FormControlLabel
                control={<Checkbox checked={overlay}
                                   onChange={(event) =>
                                       chooseOverlay(event.target.checked)}/>}
                label={i18n.t("stream_thumbnail_overlay")}
            />
            <Box>
                <Button variant="contained" disabled={running}
                        onClick={generate}>
                    {i18n.t("stream_thumbnail_generate")}
                </Button>
            </Box>
            {status && (
                <Typography variant="body2" role="status"
                            color={thumbnail.status === "error"
                                ? "error" : "text.secondary"}
                            sx={{wordBreak: "break-all"}}>
                    {status}
                </Typography>
            )}
            {made && (
                <Box component="img" alt={preview} src={url}
                     sx={{width: "100%", maxWidth: 480,
                          aspectRatio: "16 / 9", objectFit: "contain",
                          border: 1, borderColor: "divider",
                          borderRadius: 1}}/>
            )}
            {/* An image reaches the clipboard from a secure context only
                (this PC); a page from another computer over http offers
                the image to open or save instead (S4 論点 1). The desktop's
                view opens no new window, so a link would do nothing there:
                the copy is shown off, with why (S4e review). */}
            {canCopyImage() ? (
                <Box>
                    <Button variant="outlined" disabled={!made || running}
                            onClick={copy}>
                        {i18n.t("stream_thumbnail_copy")}
                    </Button>
                </Box>
            ) : desktop ? (
                <Stack spacing={0.5}>
                    <Box>
                        <Button variant="outlined" disabled>
                            {i18n.t("stream_thumbnail_copy")}
                        </Button>
                    </Box>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("stream_thumbnail_copy_unavailable")}
                    </Typography>
                </Stack>
            ) : (
                <Stack spacing={0.5}>
                    <Box>
                        <Button variant="outlined" component="a"
                                href={made ? url : undefined}
                                target="_blank" rel="noopener noreferrer"
                                disabled={!made || running}>
                            {i18n.t("stream_thumbnail_open")}
                        </Button>
                    </Box>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("stream_thumbnail_open_note")}
                    </Typography>
                </Stack>
            )}
        </CardFrame>
    );
}
