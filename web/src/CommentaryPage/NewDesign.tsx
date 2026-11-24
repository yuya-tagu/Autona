import React from "react";
import {
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogContentText,
    DialogTitle,
    Stack,
    TextField,
} from "@mui/material";
import i18n from "../i18n/config";
import {apiErrorReason, apiErrorStatus, apiGetJson, apiPost} from "../api";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {copyText} from "../PanelShell/clipboard";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {LayoutEntry} from "./SideCards";

// The design lists' layout kit (ADR-008 D2 / L2), on the BOARD, Commentary
// and Top 8 pages, and the Thumbnail and Result pages' templates (D7 R3 / R4,
// kinds "thumbnail" / "result"): "make a new design" -- a display name and a folder name,
// then core makes layout/<prefix><folder>/ from the kit (core/LayoutKit.py),
// the page lists and selects it, and its folder opens in Explorer for the
// operator's AI -- and "copy for an AI", the kit's AI_PROMPT.md of the kind
// for a chat AI (read ahead, so the click writes the clipboard at once).
// The routes are WebAccess.SHELL, so both buttons show in the desktop's
// shared view alone (useShellAccess: a browser or headless is refused, and
// shows neither). Text only: nothing here renders HTML.

export type KitKind =
    "scoreboard" | "commentary" | "top8" | "thumbnail" | "result";

export const CREATE_PATH = "/api/v1/layouts:create";
export const REVEAL_PATH = "/api/v1/layouts:reveal";
export function aiPromptPath(kind: KitKind): string {
    return `/api/v1/layouts/kit/${kind}/ai-prompt`;
}

// Must be src/core/LayoutKit.py's NAME, NAME_MAX and DISPLAY_NAME_MAX
// (tests/test_layout_kit.py compares them): checked here so a name the server
// refuses is told before it is sent.
export const NAME_MAX = 40;
export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
export const DISPLAY_NAME_MAX = 64;
// Must be LayoutKit.FOLDER_PREFIXES: what the dialog shows the folder as.
export const FOLDER_PREFIXES: Record<KitKind, string> = {
    scoreboard: "scoreboard_",
    commentary: "commentators_",
    top8: "bracket_top8_",
    thumbnail: "thumbnail_",
    result: "result_",
};

// The dialog's text of a kind: the overlays share one, a template has its own.
const NEW_TEXT: Partial<Record<KitKind, string>> = {
    thumbnail: "layout_kit_new_text_thumbnail",
    result: "layout_kit_new_text_result",
};

const REQUEST_OPTIONS = {timeoutMs: 30000};

export function nameIsValid(name: string): boolean {
    return name.length >= 1 && name.length <= NAME_MAX
        && NAME_PATTERN.test(name);
}

export function displayNameIsValid(displayName: string): boolean {
    const text = displayName.trim();
    // eslint-disable-next-line no-control-regex
    return text.length <= DISPLAY_NAME_MAX && !/[\u0000-\u001f\u007f]/.test(text);
}

// The folder name drafted from a display name of printable ASCII: other
// characters as "_", none at either end; "" for any other display name
// (the operator types one).
export function draftName(displayName: string): string {
    const text = displayName.trim();
    if (!/^[\x20-\x7e]*$/.test(text)) {
        return "";
    }
    return text.replace(/[^A-Za-z0-9_-]+/g, "_")
        .replace(/^[_-]+/, "")
        .slice(0, NAME_MAX)
        .replace(/[_-]+$/, "");
}

// What a refusal says: core's reason, else the shell's or the generic one.
export function failureText(error: unknown, folder = ""): string {
    const reason = apiErrorReason(error);
    if (reason) {
        return i18n.t(`layout_kit_error_${reason}`, {
            folder, max: NAME_MAX, displayMax: DISPLAY_NAME_MAX,
            defaultValue: i18n.t("layout_kit_error_failed")});
    }
    if (apiErrorStatus(error) === 403) {
        return i18n.t("layout_kit_error_forbidden");
    }
    return i18n.t("layout_kit_error_failed");
}

// Refusals after which the list may have changed (a folder left behind).
const RELIST_REASONS = new Set(["kit_missing", "not_listed"]);

function useMounted(): React.MutableRefObject<boolean> {
    const mounted = React.useRef(true);
    React.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);
    return mounted;
}

export default function NewDesignActions({kind, onCreated, onRelist}: {
    kind: KitKind;
    // The new design's catalog entry: the page lists it again and selects it.
    onCreated: (entry: LayoutEntry) => void;
    // A creation that failed may have left a folder: the page lists again.
    onRelist: () => void;
}) {
    const {access} = useShellAccess({withToken: false});
    const mounted = useMounted();
    const [open, setOpen] = React.useState(false);
    const [displayName, setDisplayName] = React.useState("");
    const [name, setName] = React.useState("");
    // Whether the operator typed the folder name (no more drafts then).
    const [nameTyped, setNameTyped] = React.useState(false);
    const [busy, setBusy] = React.useState(false);
    const [refusal, setRefusal] = React.useState<string | null>(null);
    // The kind's AI text, read ahead: null while it is read or has failed.
    const [prompt, setPrompt] = React.useState<string | null>(null);
    const [promptReads, setPromptReads] = React.useState(0);
    const typed = name.trim();
    const valid = nameIsValid(typed) && displayNameIsValid(displayName);
    const shell = access === "shell";

    React.useEffect(() => {
        if (!shell) {
            return undefined;
        }
        let current = true;
        setPrompt(null);
        apiGetJson<{data: {text: string}}>(aiPromptPath(kind), REQUEST_OPTIONS)
            .then((body) => {
                if (current) {
                    setPrompt(body.data.text);
                }
            }, (error: unknown) => console.error(error));
        return () => {
            current = false;
        };
    }, [kind, shell, promptReads]);

    if (!shell) {
        return null;
    }

    const close = () => {
        if (!busy) {
            setOpen(false);
        }
    };

    const changeDisplayName = (value: string) => {
        setDisplayName(value);
        setRefusal(null);
        if (!nameTyped) {
            setName(draftName(value));
        }
    };

    const create = async () => {
        if (!valid || busy) {
            return;
        }
        setBusy(true);
        setRefusal(null);
        const folder = `${FOLDER_PREFIXES[kind]}${typed}`;
        let entry: LayoutEntry;
        try {
            const text = await apiPost(CREATE_PATH, {
                kind, name: typed, displayName: displayName.trim()},
                REQUEST_OPTIONS);
            entry = (JSON.parse(text) as {data: LayoutEntry}).data;
        } catch (error) {
            console.error(error);
            if (RELIST_REASONS.has(apiErrorReason(error) ?? "")) {
                onRelist();
            }
            if (mounted.current) {
                setRefusal(failureText(error, folder));
                setBusy(false);
            }
            return;
        }
        if (mounted.current) {
            setBusy(false);
            setOpen(false);
            setDisplayName("");
            setName("");
            setNameTyped(false);
        }
        showSnackbar(i18n.t("layout_kit_created", {folder: entry.id}),
                     "success");
        onCreated(entry);
        // Its folder, for the operator's AI agent (or editor). Made already
        // when this fails: only that it did not open is told.
        apiPost(REVEAL_PATH, {id: entry.id}, REQUEST_OPTIONS).catch(
            (error: unknown) => {
                console.error(error);
                showSnackbar(i18n.t("layout_kit_reveal_failed"), "error");
            });
    };

    // In the click itself, with no wait before it: the clipboard takes a
    // write only while the click's activation lasts.
    const copyPrompt = () => {
        if (prompt === null) {
            showSnackbar(i18n.t("layout_kit_prompt_unavailable"), "error");
            setPromptReads((count) => count + 1);
            return;
        }
        copyText(prompt).then((copied) => showSnackbar(
            i18n.t(copied ? "layout_kit_prompt_copied"
                          : "layout_kit_prompt_copy_failed"),
            copied ? "success" : "error"));
    };

    const nameError = typed !== "" && !nameIsValid(typed);
    const displayError = !displayNameIsValid(displayName);
    let nameHelp: string;
    if (refusal) {
        nameHelp = refusal;
    } else if (nameError) {
        nameHelp = i18n.t("layout_kit_name_rule", {max: NAME_MAX});
    } else if (typed === "") {
        nameHelp = i18n.t("layout_kit_name_needed", {max: NAME_MAX});
    } else {
        nameHelp = i18n.t("layout_kit_folder", {
            folder: `layout/${FOLDER_PREFIXES[kind]}${typed}/`});
    }

    return (
        <>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button
                    variant="outlined"
                    size="small"
                    onClick={() => {
                        setRefusal(null);
                        setOpen(true);
                    }}
                >
                    {i18n.t("layout_kit_new")}
                </Button>
                <Button variant="outlined" size="small" onClick={copyPrompt}>
                    {i18n.t("layout_kit_copy_prompt")}
                </Button>
            </Stack>
            <Dialog open={open} onClose={close} fullWidth maxWidth="xs">
                <DialogTitle>{i18n.t("layout_kit_new_title")}</DialogTitle>
                <DialogContent>
                    <Stack spacing={2}>
                        <DialogContentText>
                            {i18n.t(NEW_TEXT[kind] ?? "layout_kit_new_text")}
                        </DialogContentText>
                        <TextField
                            autoFocus
                            size="small"
                            label={i18n.t("layout_kit_display_name")}
                            value={displayName}
                            disabled={busy}
                            onChange={(event) =>
                                changeDisplayName(event.target.value)}
                            error={displayError}
                            helperText={i18n.t(displayError
                                ? "layout_kit_display_name_rule"
                                : "layout_kit_display_name_help",
                                               {max: DISPLAY_NAME_MAX})}
                        />
                        <TextField
                            size="small"
                            label={i18n.t("layout_kit_name")}
                            value={name}
                            disabled={busy}
                            onChange={(event) => {
                                setName(event.target.value);
                                setNameTyped(true);
                                setRefusal(null);
                            }}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                    event.preventDefault();
                                    create();
                                }
                            }}
                            error={nameError || refusal !== null}
                            helperText={nameHelp}
                            slotProps={{htmlInput: {maxLength: NAME_MAX}}}
                        />
                    </Stack>
                </DialogContent>
                <DialogActions>
                    <Button onClick={close} disabled={busy}>
                        {i18n.t("layout_kit_cancel")}
                    </Button>
                    <Button variant="contained" onClick={create}
                            disabled={!valid || busy}>
                        {i18n.t("layout_kit_create")}
                    </Button>
                </DialogActions>
            </Dialog>
        </>
    );
}
