import React from "react";
import {
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogContentText,
    DialogTitle,
    FormControlLabel,
    Stack,
    Switch,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {CardFrame} from "../GuidePages/parts";
import {apiErrorReason, apiErrorCode} from "../api";
import {PluginsApi} from "./pluginsApi";
import {DevState, PickResult} from "./pluginTypes";
import {pickErrorText} from "./RunView";

// The developer's entrance (ADR-009 D4): the toggle and the dev folders. Both
// ways in ask first, with D6's fixed text plus the dev folder's own line.

type Asking = {kind: "toggle"} | {kind: "folder"; pick: PickResult};

export default function DevSection({api, onChanged, onError}: {
    api: PluginsApi;
    // The list changed (a dev folder came or went).
    onChanged: () => void;
    onError: (text: string) => void;
}) {
    const [dev, setDev] = React.useState<DevState | null>(null);
    const [asking, setAsking] = React.useState<Asking | null>(null);
    // The folder dialog is open: the add button waits for it.
    const [picking, setPicking] = React.useState(false);

    const read = React.useCallback(() => {
        api.dev().then(setDev, () => setDev(null));
    }, [api]);
    React.useEffect(read, [read]);

    const fail = (error: unknown) => {
        const reason = apiErrorReason(error) ?? apiErrorCode(error);
        onError(String(reason).toLowerCase() === "id_taken"
            ? i18n.t("plugins_dev_id_taken") : i18n.t("plugins_failed"));
    };

    const addFolder = async () => {
        setPicking(true);
        try {
            const pick = await api.pickFolder();
            if (pick) {
                setAsking({kind: "folder", pick});
            }
        } catch (error) {
            const text = pickErrorText(error);
            if (text) {
                onError(text);
            }
        } finally {
            setPicking(false);
        }
    };

    const answer = async (go: boolean) => {
        const request = asking;
        setAsking(null);
        if (!request || !go || !dev) {
            return;
        }
        try {
            if (request.kind === "toggle") {
                await api.setDev(!dev.enabled);
                onChanged();
            } else {
                await api.addDevFolder(request.pick.id);
                onChanged();
            }
        } catch (error) {
            fail(error);
        }
        read();
    };

    const remove = async (id: string) => {
        try {
            await api.removeDevFolder(id);
            onChanged();
        } catch (error) {
            fail(error);
        }
        read();
    };

    // Turning it off needs no warning: only the way in asks.
    const toggle = async () => {
        if (!dev) {
            return;
        }
        if (dev.enabled) {
            try {
                await api.setDev(false);
                onChanged();
            } catch (error) {
                fail(error);
            }
            read();
        } else {
            setAsking({kind: "toggle"});
        }
    };

    return (
        <CardFrame title={i18n.t("plugins_dev_title")}>
            <FormControlLabel
                label={i18n.t("plugins_dev_toggle")}
                control={<Switch checked={Boolean(dev?.enabled)}
                                 disabled={dev === null}
                                 inputProps={{"aria-label":
                                     i18n.t("plugins_dev_toggle")}}
                                 onChange={() => void toggle()}/>}/>
            {dev?.enabled ? (
                <Stack spacing={1} data-testid="plugin-dev-folders">
                    {dev.folders.map((folder) => (
                        <Stack key={folder.id} direction="row" spacing={1}
                               alignItems="center">
                            <Typography variant="body2" sx={{flex: 1}}>
                                {folder.name}
                            </Typography>
                            <Button size="small" color="error"
                                    onClick={() => void remove(folder.id)}>
                                {i18n.t("plugins_dev_remove")}
                            </Button>
                        </Stack>
                    ))}
                    <div>
                        <Button size="small" variant="outlined"
                                disabled={picking}
                                data-testid="plugin-dev-add"
                                onClick={() => void addFolder()}>
                            {i18n.t("plugins_dev_add")}
                        </Button>
                    </div>
                </Stack>
            ) : null}
            <Dialog open={asking !== null} onClose={() => void answer(false)}>
                <DialogTitle>{i18n.t("plugins_dev_confirm_title")}</DialogTitle>
                <DialogContent>
                    <DialogContentText data-testid="plugin-dev-consent">
                        {i18n.t("plugins_consent_text")}
                    </DialogContentText>
                    <DialogContentText sx={{mt: 1}}>
                        {i18n.t("plugins_dev_consent_extra")}
                    </DialogContentText>
                    {asking?.kind === "folder" ? (
                        <DialogContentText sx={{mt: 1}}>
                            {asking.pick.name || i18n.t("plugins_pick_chosen")}
                        </DialogContentText>
                    ) : null}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => void answer(false)}>
                        {i18n.t("plugins_cancel")}
                    </Button>
                    <Button color="warning" variant="contained"
                            onClick={() => void answer(true)}>
                        {i18n.t("plugins_dev_confirm_go")}
                    </Button>
                </DialogActions>
            </Dialog>
        </CardFrame>
    );
}
