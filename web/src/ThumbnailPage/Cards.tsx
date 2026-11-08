import React from "react";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    Checkbox,
    CircularProgress,
    FormControl,
    FormControlLabel,
    FormLabel,
    InputAdornment,
    MenuItem,
    Radio,
    RadioGroup,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {canCopyImage, copyImage, copyText} from "../PanelShell/clipboard";
import {IMAGE_UPLOAD_ACCEPT} from "../PanelShell/imageUpload";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {ConfirmDialog} from "../StreamPage/Dialogs";

// The Thumbnail page's cards (ADR-005 S4p), the Qt サムネイル panel's groups:
// the design (template and background), the items shown, the character
// images' placement for the selected game, the preview with the generation,
// and the video title. They draw the page's settings (GET / PATCH
// /api/v1/thumbnail) and thumbnail.render from the state, and ask the page
// to write.

// A game's character image settings (core ThumbnailService.Settings()).
// smooth_scale is kept and not shown (owner's call, S4p).
export interface GameSettings {
    flip_p1: boolean;
    flip_p2: boolean;
    smooth_scale: boolean;
    zoom: number;
    align: {horizontal: number; vertical: number};
}

export type BackgroundSource = "default" | "banner" | "custom";

// GET /api/v1/thumbnail's settings, as every write answers them.
export interface ThumbnailSettings {
    template_id: string;
    background_source: BackgroundSource;
    // The uploaded image's file name ("" none): shown, never sent.
    background_name: string;
    display_phase: boolean;
    use_team_names: boolean;
    use_sponsors: boolean;
    game: Record<string, GameSettings>;
}

// What PATCH /api/v1/thumbnail takes: some of the settings, a game's one
// at a time.
export interface SettingsChange {
    template_id?: string;
    background_source?: BackgroundSource;
    display_phase?: boolean;
    use_team_names?: boolean;
    use_sponsors?: boolean;
    game?: Record<string, {
        flip_p1?: boolean;
        flip_p2?: boolean;
        zoom?: number;
        align?: {horizontal?: number; vertical?: number};
    }>;
}

export interface ThumbnailTemplate {
    id: string;
    // The server's (translated by the app); the page's own words first.
    name: string;
}

export interface ThumbnailGame {
    codename: string;
    name: string;
}

// thumbnail.render: the render lane's last run (core ThumbnailService).
export interface ThumbnailRender {
    status: "idle" | "running" | "done" | "error";
    path: string;
    error: string;
    revision: number;
}

// What a generation of this page's came to (the status line).
export type GenerateResult =
    | {status: "done"; path: string}
    | {status: "error"; error: string};

// The defaults core reads a game with (ThumbnailService
// DEFAULT_GAME_SETTINGS), for a game the settings do not list yet.
export const DEFAULT_GAME_SETTINGS: GameSettings = {
    flip_p1: false,
    flip_p2: true,
    smooth_scale: true,
    zoom: 100,
    align: {horizontal: 50, vertical: 40},
};

// The ranges core takes (ZOOM_RANGE / ALIGN_RANGE).
export const ZOOM_RANGE: [number, number] = [1, 1000];
export const ALIGN_RANGE: [number, number] = [1, 100];

const ERRORS = ["no_game", "no_player", "payload_failed", "render_failed",
                "save_failed", "cancelled"];

// thumbnail.render.error as words: a code core knows, else the render's
// failure (the code is not a person's to read).
export function errorText(code: string): string {
    return i18n.t(`thumbnail_error_${ERRORS.includes(code)
        ? code : "render_failed"}`);
}

// The newest render as a PNG (GET /api/v1/thumbnail/preview). `v` is the
// run's revision: what the server answers is always the newest, and the
// revision tells the image cache one run from another.
export function previewUrl(revision: number): string {
    return `${BASE_URL}/api/v1/thumbnail/preview?v=${revision}`;
}

// Where a generated thumbnail is served: thumbnail.render.path is relative
// to the app's folder (out/thumbnails/<tags>-vs-<tags>-<time>.png), which the
// server serves as it is. The tags are the operator's: each segment is
// encoded (a "#" or "%" in a name would end or garble the URL).
export function generatedUrl(render: ThumbnailRender): string {
    const path = render.path.replace(/^\.?\//, "").split("/")
        .map(encodeURIComponent).join("/");
    return `${BASE_URL}/${path}?v=${render.revision}`;
}

export function CardFrame({title, children}: {
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

export function Note({children}: {children: React.ReactNode}) {
    return (
        <Typography variant="body2" color="text.secondary">
            {children}
        </Typography>
    );
}

function SettingCheckbox({label, value, send, note}: {
    label: string;
    value: boolean;
    send: (value: boolean) => Promise<unknown>;
    note?: string;
}) {
    const [checked, choose] = useStateChoice(value, send);
    return (
        <Box>
            <FormControlLabel
                control={<Checkbox checked={checked}
                                   onChange={(event) =>
                                       choose(event.target.checked)}/>}
                label={label}
            />
            {note && (
                <Typography variant="caption" color="text.secondary"
                            component="p" sx={{ml: 4, mt: -0.5}}>
                    {note}
                </Typography>
            )}
        </Box>
    );
}

// ── デザイン ─────────────────────────────────────────────────────────────

export function DesignCard({settings, templates, change, upload}: {
    settings: ThumbnailSettings;
    templates: ThumbnailTemplate[];
    change: (next: SettingsChange) => Promise<unknown>;
    upload: (file: File) => void;
}) {
    const [template, chooseTemplate] = useStateChoice(
        settings.template_id, React.useCallback(
            (value: string) => change({template_id: value}), [change]));
    const [source, chooseSource] = useStateChoice(
        settings.background_source, React.useCallback(
            (value: BackgroundSource) => change({background_source: value}),
            [change]));
    const input = React.useRef<HTMLInputElement>(null);
    const name = settings.background_name;

    const pickSource = (value: BackgroundSource) => {
        if (value === "custom" && !name) {
            // No image of the operator's yet: the file picker, from the
            // click (as the Qt panel asked at once). The upload selects
            // "custom" itself; a cancelled picker changes nothing.
            input.current?.click();
            return;
        }
        chooseSource(value);
    };

    return (
        <CardFrame title={i18n.t("thumbnail_design")}>
            <TextField
                select
                size="small"
                label={i18n.t("thumbnail_template")}
                value={templates.some((entry) => entry.id === template)
                    ? template : ""}
                onChange={(event) => chooseTemplate(event.target.value)}
            >
                {templates.map((entry) => (
                    <MenuItem key={entry.id} value={entry.id}>
                        {i18n.t(`thumbnail_template_${entry.id}`,
                                {defaultValue: entry.name})}
                    </MenuItem>
                ))}
            </TextField>
            <FormControl>
                <FormLabel id="thumbnail-background">
                    {i18n.t("thumbnail_background")}
                </FormLabel>
                <RadioGroup
                    aria-labelledby="thumbnail-background"
                    value={source}
                    onChange={(event) => pickSource(
                        event.target.value as BackgroundSource)}
                >
                    <FormControlLabel value="default" control={<Radio/>}
                                      label={i18n.t("thumbnail_background_default")}/>
                    <FormControlLabel value="banner" control={<Radio/>}
                                      label={i18n.t("thumbnail_background_banner")}/>
                    <FormControlLabel value="custom" control={<Radio/>}
                                      label={i18n.t("thumbnail_background_custom")}/>
                </RadioGroup>
            </FormControl>
            {source === "custom" && (
                <Stack direction="row" spacing={1} alignItems="center"
                       sx={{pl: 4}}>
                    <Button variant="outlined" size="small"
                            onClick={() => input.current?.click()}>
                        {i18n.t("thumbnail_background_choose")}
                    </Button>
                    <Typography variant="body2" color="text.secondary"
                                sx={{minWidth: 0, wordBreak: "break-all"}}>
                        {name ? i18n.t("thumbnail_background_file", {name})
                            : i18n.t("thumbnail_background_none")}
                    </Typography>
                </Stack>
            )}
            <input
                ref={input}
                type="file"
                accept={IMAGE_UPLOAD_ACCEPT}
                hidden
                data-testid="thumbnail-background-file"
                onChange={(event) => {
                    const chosen = event.target.files?.[0];
                    // The same file may be chosen again later.
                    event.target.value = "";
                    if (chosen) {
                        upload(chosen);
                    }
                }}
            />
        </CardFrame>
    );
}

// ── 表示する項目 ─────────────────────────────────────────────────────────

export function ItemsCard({settings, change}: {
    settings: ThumbnailSettings;
    change: (next: SettingsChange) => Promise<unknown>;
}) {
    const send = (key: "display_phase" | "use_team_names" | "use_sponsors") =>
        (value: boolean) => change({[key]: value} as SettingsChange);
    return (
        <CardFrame title={i18n.t("thumbnail_items")}>
            <Box>
                <SettingCheckbox label={i18n.t("thumbnail_display_phase")}
                                 value={settings.display_phase}
                                 send={send("display_phase")}/>
                {/* Always shown: the page does not follow the battle format
                    (the Qt panel hid it in 1v1). It only matters where the
                    teams have names. */}
                <SettingCheckbox label={i18n.t("thumbnail_use_team_names")}
                                 value={settings.use_team_names}
                                 send={send("use_team_names")}
                                 note={i18n.t("thumbnail_use_team_names_note")}/>
                <SettingCheckbox label={i18n.t("thumbnail_use_sponsors")}
                                 value={settings.use_sponsors}
                                 send={send("use_sponsors")}/>
            </Box>
        </CardFrame>
    );
}

// ── キャラクター画像の配置 ───────────────────────────────────────────────

// A whole number in [min, max] from what was typed; null otherwise.
export function parseRange(text: string, min: number, max: number): number | null {
    if (!/^\s*\d+\s*$/.test(text)) {
        return null;
    }
    const value = Number(text.trim());
    return value >= min && value <= max ? value : null;
}

// A number is sent once it is confirmed -- Enter, or leaving the field --
// not as it is typed: each pause in the typing was a save and a preview of
// a number on its way ("15" of "150"; S4p review). Out of the range it is
// refused then, aloud, and the field shows the saved value again; emptied,
// it just shows it again. While the field has focus, the saved value does
// not overwrite what is typed. A percentage with its range under it, unless
// told otherwise (the Result page's ranks). With a `step`, the arrow keys go up
// and down by it, as a spin box's do, and it is sent as typing is.
export function RangeField({label, value, range, send, percent = true,
                            width = 150, step, readOnly}: {
    label: string;
    value: number;
    range: [number, number];
    send: (value: number) => Promise<unknown>;
    percent?: boolean;
    width?: number;
    step?: number;
    readOnly?: boolean;
}) {
    const [min, max] = range;
    const [text, setText] = React.useState(String(value));
    const focused = React.useRef(false);
    const edited = React.useRef(false);
    // Sent and not answered yet: shown meanwhile, not the old value.
    const sending = React.useRef(false);
    const saved = React.useRef(value);
    saved.current = value;

    React.useEffect(() => {
        if (!focused.current) {
            setText(String(value));
        }
    }, [value]);

    const confirm = () => {
        if (!edited.current) {
            return;
        }
        edited.current = false;
        const number = parseRange(text, min, max);
        if (number === null) {
            if (text.trim() !== "") {
                showSnackbar(i18n.t("thumbnail_out_of_range", {min, max}),
                             "error");
            }
            setText(String(saved.current));
            return;
        }
        setText(String(number));
        if (number === saved.current) {
            return;
        }
        sending.current = true;
        send(number).then(() => {
            sending.current = false;
        }, () => {
            sending.current = false;
            if (!focused.current) {
                setText(String(saved.current));
            }
        });
    };

    const invalid = text.trim() !== "" && parseRange(text, min, max) === null;
    return (
        <TextField
            size="small"
            label={label}
            error={invalid}
            helperText={percent ? i18n.t("thumbnail_range", {min, max})
                : undefined}
            slotProps={{
                htmlInput: {inputMode: "numeric", readOnly},
                input: percent ? {endAdornment:
                    <InputAdornment position="end">%</InputAdornment>}
                    : undefined,
            }}
            value={text}
            onChange={(event) => {
                setText(event.target.value);
                edited.current = true;
            }}
            onFocus={() => {
                focused.current = true;
            }}
            onBlur={() => {
                focused.current = false;
                if (edited.current) {
                    confirm();
                } else if (!sending.current) {
                    setText(String(saved.current));
                }
            }}
            onKeyDown={(event) => {
                // Not while an IME composes: its Enter confirms the text.
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                    confirm();
                } else if (step && !readOnly
                           && (event.key === "ArrowUp"
                               || event.key === "ArrowDown")) {
                    event.preventDefault();
                    const from = parseRange(text, min, max) ?? saved.current;
                    const change = event.key === "ArrowUp" ? step : -step;
                    setText(String(Math.min(max, Math.max(min, from + change))));
                    edited.current = true;
                }
            }}
            sx={{width, flexShrink: 0}}
        />
    );
}

// Keyed by the game's codename by its caller: another game's settings
// start afresh.
export function CharactersCard({game, settings, change}: {
    game: ThumbnailGame | null;
    settings: GameSettings | undefined;
    change: (next: SettingsChange) => Promise<unknown>;
}) {
    const title = i18n.t("thumbnail_characters");
    if (!game) {
        return (
            <CardFrame title={title}>
                <Note>{i18n.t("thumbnail_characters_no_game")}</Note>
            </CardFrame>
        );
    }
    return (
        <CardFrame title={title}>
            <CharacterSettings game={game}
                               settings={settings ?? DEFAULT_GAME_SETTINGS}
                               change={change}/>
        </CardFrame>
    );
}

function CharacterSettings({game, settings, change}: {
    game: ThumbnailGame;
    settings: GameSettings;
    change: (next: SettingsChange) => Promise<unknown>;
}) {
    const codename = game.codename;
    const sendGame = React.useCallback(
        (next: NonNullable<SettingsChange["game"]>[string]) =>
            change({game: {[codename]: next}}), [change, codename]);
    const sendZoom = React.useCallback(
        (zoom: number) => sendGame({zoom}), [sendGame]);
    const sendHorizontal = React.useCallback(
        (horizontal: number) => sendGame({align: {horizontal}}), [sendGame]);
    const sendVertical = React.useCallback(
        (vertical: number) => sendGame({align: {vertical}}), [sendGame]);
    return (
        <>
            <Note>
                {i18n.t("thumbnail_characters_game",
                        {game: game.name || codename})}
            </Note>
            <Box>
                <SettingCheckbox label={i18n.t("thumbnail_flip_p1")}
                                 value={settings.flip_p1}
                                 send={(value) => sendGame({flip_p1: value})}/>
                <SettingCheckbox label={i18n.t("thumbnail_flip_p2")}
                                 value={settings.flip_p2}
                                 send={(value) => sendGame({flip_p2: value})}/>
            </Box>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                <RangeField label={i18n.t("thumbnail_zoom")}
                            value={settings.zoom} range={ZOOM_RANGE}
                            send={sendZoom}/>
                <RangeField label={i18n.t("thumbnail_align_horizontal")}
                            value={settings.align.horizontal}
                            range={ALIGN_RANGE} send={sendHorizontal}/>
                <RangeField label={i18n.t("thumbnail_align_vertical")}
                            value={settings.align.vertical}
                            range={ALIGN_RANGE} send={sendVertical}/>
            </Stack>
        </>
    );
}

// ── 既定に戻す ───────────────────────────────────────────────────────────

export function ResetButton({reset}: {reset: () => void}) {
    const [confirming, setConfirming] = React.useState(false);
    return (
        <Box>
            <Button variant="outlined" color="error" size="small"
                    onClick={() => setConfirming(true)}>
                {i18n.t("thumbnail_reset")}
            </Button>
            <ConfirmDialog
                open={confirming}
                title={i18n.t("thumbnail_reset")}
                message={i18n.t("thumbnail_reset_confirm")}
                confirm={i18n.t("thumbnail_reset_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    reset();
                }}
                onClose={() => setConfirming(false)}
            />
        </Box>
    );
}

// ── プレビューと作成 ─────────────────────────────────────────────────────

function generateStatus(generating: boolean,
                        result: GenerateResult | null): string {
    if (generating) {
        return i18n.t("thumbnail_generating");
    }
    if (!result) {
        return "";
    }
    return result.status === "done"
        ? i18n.t("thumbnail_saved", {path: result.path})
        : errorText(result.error);
}

export function PreviewCard({canRender, desktop, render, imageRevision,
                             previewing, previewError, generating, blocked,
                             result, generate}: {
    // False on the headless shell: there is no off-screen view to draw with.
    canRender: boolean;
    desktop: boolean;
    render: ThumbnailRender;
    // The newest run that ended with an image (0: none yet).
    imageRevision: number;
    previewing: boolean;
    // A preview of this page's that failed: its code ("" none).
    previewError: string;
    generating: boolean;
    // No generation is taken now: the page's request awaits its answer, or
    // a background is being uploaded.
    blocked: boolean;
    result: GenerateResult | null;
    generate: () => void;
}) {
    const [failedRevision, setFailedRevision] = React.useState(0);
    const shown = imageRevision > 0 && failedRevision !== imageRevision;
    const made = Boolean(render.path);
    const url = made ? generatedUrl(render) : "";
    const status = generateStatus(generating, result);
    const imageName = i18n.t("thumbnail_preview_image");

    const copy = () => {
        copyImage(url).then(
            () => showSnackbar(i18n.t("thumbnail_image_copied"), "success"),
            (error) => {
                console.error(error);
                showSnackbar(i18n.t("thumbnail_copy_failed"), "error");
            });
    };

    return (
        <CardFrame title={i18n.t("thumbnail_preview")}>
            {!canRender && (
                <Alert severity="info">{i18n.t("thumbnail_cannot_render")}</Alert>
            )}
            <Box
                sx={{position: "relative", width: "100%", aspectRatio: "16 / 9",
                     display: "flex", alignItems: "center",
                     justifyContent: "center", border: 1,
                     borderColor: "divider", borderRadius: 1,
                     overflow: "hidden", bgcolor: "action.hover"}}
            >
                {shown ? (
                    <Box component="img" alt={imageName}
                         src={previewUrl(imageRevision)}
                         onError={() => setFailedRevision(imageRevision)}
                         sx={{width: "100%", height: "100%",
                              objectFit: "contain"}}/>
                ) : (
                    <Typography variant="body2" color="text.secondary">
                        {imageRevision > 0
                            ? i18n.t("thumbnail_preview_failed_to_show")
                            : i18n.t("thumbnail_preview_none")}
                    </Typography>
                )}
                {previewing && (
                    <Stack direction="row" spacing={1} alignItems="center"
                           sx={{position: "absolute", top: 8, right: 8,
                                px: 1, py: 0.5, borderRadius: 1,
                                bgcolor: "background.paper"}}>
                        <CircularProgress size={16}/>
                        <Typography variant="caption">
                            {i18n.t("thumbnail_previewing")}
                        </Typography>
                    </Stack>
                )}
            </Box>
            {previewError && (
                <Typography variant="body2" color="error">
                    {errorText(previewError)}
                </Typography>
            )}
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap"
                   alignItems="center">
                <Button variant="contained"
                        disabled={!canRender || generating || blocked}
                        onClick={generate}>
                    {i18n.t("thumbnail_generate")}
                </Button>
                {/* The generated thumbnail (out/thumbnails), not the preview.
                    An image reaches the clipboard from a secure context only
                    (this PC); a page from another computer over http offers
                    it to open or save instead (a new window of the desktop's
                    view opens in the default browser: AutonaWebPanelHost). */}
                {canCopyImage() ? (
                    <Button variant="outlined" disabled={!made || generating}
                            onClick={copy}>
                        {i18n.t("thumbnail_copy_image")}
                    </Button>
                ) : desktop ? (
                    <Button variant="outlined" disabled>
                        {i18n.t("thumbnail_copy_image")}
                    </Button>
                ) : (
                    <Button variant="outlined" component="a"
                            href={made ? url : undefined}
                            target="_blank" rel="noopener noreferrer"
                            disabled={!made || generating}>
                        {i18n.t("thumbnail_open_image")}
                    </Button>
                )}
            </Stack>
            {!canCopyImage() && (
                <Note>
                    {desktop ? i18n.t("thumbnail_copy_unavailable")
                        : i18n.t("thumbnail_open_note")}
                </Note>
            )}
            {status && (
                <Typography variant="body2" role="status"
                            color={result?.status === "error" && !generating
                                ? "error" : "text.secondary"}
                            sx={{wordBreak: "break-all"}}>
                    {status}
                </Typography>
            )}
        </CardFrame>
    );
}

// ── 動画タイトル ─────────────────────────────────────────────────────────

// The title core makes of the match (GET /api/v1/thumbnail's videoTitle),
// to copy into the video's page. An edit is the page's alone, not saved:
// a new title replaces the field only while it is the last one made, or
// empty.
export function VideoTitleCard({auto}: {auto: string}) {
    const [text, setText] = React.useState(auto);
    const last = React.useRef(auto);

    React.useEffect(() => {
        const previous = last.current;
        last.current = auto;
        setText((current) => (current === previous || current === ""
            ? auto : current));
    }, [auto]);

    const copy = async () => {
        const title = text.trim();
        if (!title) {
            return;
        }
        if (await copyText(title)) {
            showSnackbar(i18n.t("thumbnail_video_title_copied"), "success");
        } else {
            showSnackbar(i18n.t("thumbnail_copy_failed"), "error");
        }
    };

    return (
        <CardFrame title={i18n.t("thumbnail_video_title")}>
            <Stack direction="row" spacing={1} alignItems="flex-start">
                <TextField
                    size="small"
                    label={i18n.t("thumbnail_video_title_field")}
                    placeholder={i18n.t("thumbnail_video_title_placeholder")}
                    helperText={i18n.t("thumbnail_video_title_note")}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    onBlur={() => {
                        // Emptied: the title made of the match again.
                        if (text.trim() === "") {
                            setText(last.current);
                        }
                    }}
                    sx={{flex: 1, minWidth: 0}}
                />
                <Button variant="contained" size="small" onClick={copy}
                        disabled={!text.trim()} sx={{mt: "4px"}}>
                    {i18n.t("thumbnail_video_title_copy")}
                </Button>
            </Stack>
        </CardFrame>
    );
}
