import React from "react";
import {Alert, LinearProgress} from "@mui/material";
import {useSelector} from "react-redux";
import i18n from "../i18n/config";

// Overlay banner shown while the socket is down. The page keeps rendering
// the last known state underneath instead of being replaced wholesale, so
// an operator can still read the board during a brief drop.
export default function ConnectionBanner() {
    const status = useSelector(state => state.websocketInfo.status);

    if (status === "connected" || status === "initial") {
        return null;
    }

    const message = status === "errored"
        ? i18n.t("failed_to_connect")
        : i18n.t("reconnecting");

    return (
        <Alert
            severity="warning"
            icon={false}
            sx={(theme) => ({
                position: "sticky",
                top: 0,
                zIndex: theme.zIndex.snackbar,
                borderRadius: 0,
            })}
        >
            {message}
            <LinearProgress color="warning" sx={{mt: 0.5}} />
        </Alert>
    );
}
