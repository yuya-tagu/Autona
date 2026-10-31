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

// The Stream page's dialogs (ADR-005 S4e): the channel form the Qt panel's
// add / edit buttons opened, and a yes / no confirmation.

export function ConfirmDialog({open, title, message, confirm, onConfirm,
                               onClose}: {
    open: boolean;
    title: string;
    message: string;
    confirm: string;
    onConfirm: () => void;
    onClose: () => void;
}) {
    return (
        <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
            <DialogTitle>{title}</DialogTitle>
            <DialogContent>
                <DialogContentText>{message}</DialogContentText>
            </DialogContent>
            <DialogActions>
                {/* No is the default, as in the Qt panel's question. */}
                <Button onClick={onClose} autoFocus>
                    {i18n.t("stream_cancel")}
                </Button>
                <Button color="error" onClick={onConfirm}>
                    {confirm}
                </Button>
            </DialogActions>
        </Dialog>
    );
}

export interface ChannelForm {
    name: string;
    url: string;
}

// Adds a channel, or edits one (`onDelete` given: its delete button, behind
// a confirmation). `onSave` answers whether the dialog may close (false:
// the request failed, and its snackbar said so).
export function ChannelDialog({open, title, initial, onSave, onDelete,
                               onClose}: {
    open: boolean;
    title: string;
    initial: ChannelForm;
    onSave: (form: ChannelForm) => Promise<boolean>;
    onDelete?: () => Promise<boolean>;
    onClose: () => void;
}) {
    const [name, setName] = React.useState(initial.name);
    const [url, setUrl] = React.useState(initial.url);
    const [missing, setMissing] = React.useState(false);
    const [busy, setBusy] = React.useState(false);
    const [confirming, setConfirming] = React.useState(false);

    React.useEffect(() => {
        if (open) {
            setName(initial.name);
            setUrl(initial.url);
            setMissing(false);
            setBusy(false);
            setConfirming(false);
        }
    }, [open, initial]);

    const save = async () => {
        const form = {name: name.trim(), url: url.trim()};
        if (!form.name || !form.url) {
            // Refused here, as the panel's dialog did (and core does).
            setMissing(true);
            return;
        }
        setBusy(true);
        let done = false;
        try {
            done = await onSave(form);
        } catch (error) {
            console.error(error);
        } finally {
            // A save that throws leaves the dialog usable (S4e review).
            setBusy(false);
        }
        if (done) {
            onClose();
        }
    };

    const remove = async () => {
        setConfirming(false);
        if (!onDelete) {
            return;
        }
        setBusy(true);
        let done = false;
        try {
            done = await onDelete();
        } catch (error) {
            console.error(error);
        } finally {
            setBusy(false);
        }
        if (done) {
            onClose();
        }
    };

    return (
        <>
            <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
                <DialogTitle>{title}</DialogTitle>
                <DialogContent>
                    <Stack spacing={2} sx={{pt: 1}}>
                        <TextField
                            size="small"
                            label={i18n.t("stream_channel_name")}
                            placeholder="BST Stream"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            error={missing && !name.trim()}
                            autoFocus
                        />
                        <TextField
                            size="small"
                            label={i18n.t("stream_channel_url")}
                            placeholder="https://www.youtube.com/@B_ST_Stream/live"
                            value={url}
                            onChange={(event) => setUrl(event.target.value)}
                            error={missing && !url.trim()}
                            helperText={missing
                                ? i18n.t("stream_channel_required") : " "}
                        />
                    </Stack>
                </DialogContent>
                <DialogActions>
                    {onDelete && (
                        <Button color="error" disabled={busy}
                                onClick={() => setConfirming(true)}
                                sx={{mr: "auto"}}>
                            {i18n.t("stream_channel_delete")}
                        </Button>
                    )}
                    <Button onClick={onClose}>{i18n.t("stream_cancel")}</Button>
                    <Button variant="contained" disabled={busy} onClick={save}>
                        {i18n.t("stream_save")}
                    </Button>
                </DialogActions>
            </Dialog>
            <ConfirmDialog
                open={confirming}
                title={i18n.t("stream_channel_delete_title")}
                message={i18n.t("stream_channel_delete_confirm")}
                confirm={i18n.t("stream_channel_delete")}
                onConfirm={remove}
                onClose={() => setConfirming(false)}
            />
        </>
    );
}
