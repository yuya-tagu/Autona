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
import {BASE_URL} from "../env";
import {Connection, ConnectionStatus} from "./useConnection";
import {CardFrame, Code, CopyButton} from "./parts";

// The guides' "Connection" card (ADR-005 S-final SF-4a-2), the windows'
// group of the same name: the base URL to copy, the port and what LAN
// access means for the guide's reader, from GET /api/v1/connection.

export type LanState = "lan" | "lanAtRestart" | "off";

export function lanState(connection: Connection): LanState {
    if (connection.lan) {
        return "lan";
    }
    return connection.lanAtRestart ? "lanAtRestart" : "off";
}

// The API as this page reached it: what a page from another computer --
// refused the connection, which names this PC's folders -- offers instead.
export const PAGE_API_URL = `${BASE_URL}/api/v1`;

// The base URL a guide shows: the connection's `field` when it was read,
// the page's own when it was refused (another computer), none while it is
// not known (loading, failed) -- the loopback then would look like the
// answer.
export function guideBaseUrl(connection: Connection | null,
                             status: ConnectionStatus,
                             field: "baseUrl" | "localBaseUrl"): string | null {
    if (connection) {
        return connection[field];
    }
    return status === "thisPcOnly" ? PAGE_API_URL : null;
}

// A request's URL to show: the base URL and the path, or "…" and the path
// while the base URL is not known.
export function shownUrl(baseUrl: string | null, path: string): string {
    return baseUrl === null ? `…${path}` : baseUrl + path;
}

export default function ConnectionCard({connection, status, refresh, url,
                                        lanNote, lanWarning = false,
                                        children}: {
    connection: Connection | null;
    status: ConnectionStatus;
    refresh: () => void;
    // The base URL shown (guideBaseUrl()); null while it is not known.
    url: string | null;
    // What LAN access means, in this guide's words.
    lanNote: (state: LanState) => string;
    // The API guide's: with LAN access the note is a warning (each device
    // needs the token).
    lanWarning?: boolean;
    // The guide's own rows (the API's token, the OBS guide's out/).
    children?: React.ReactNode;
}) {
    const state = connection ? lanState(connection) : null;
    return (
        <CardFrame title={i18n.t("guide_connection")}>
            {status === "loading" ? (
                <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={18}/>
                    <Typography variant="body2">
                        {i18n.t("guide_connection_loading")}
                    </Typography>
                </Stack>
            ) : null}
            {status === "failed" ? (
                <Alert severity="error" action={
                    <Button color="inherit" size="small" onClick={refresh}>
                        {i18n.t("guide_retry")}
                    </Button>
                }>
                    {i18n.t("guide_connection_failed")}
                </Alert>
            ) : null}
            {status === "thisPcOnly" ? (
                <Alert severity="info">
                    {i18n.t("guide_connection_this_pc_only")}
                </Alert>
            ) : null}
            {url !== null ? (
                <Stack direction="row" spacing={1} alignItems="center"
                       flexWrap="wrap" useFlexGap>
                    <Typography variant="body2">
                        {i18n.t("guide_base_url")}:
                    </Typography>
                    <Box sx={{flex: 1, minWidth: 200}}>
                        <Code>{url}</Code>
                    </Box>
                    <CopyButton text={url}
                                name={i18n.t("guide_copy_base_url")}/>
                    <Button size="small" onClick={refresh}>
                        {i18n.t("guide_refresh")}
                    </Button>
                </Stack>
            ) : null}
            {connection ? (
                <Typography variant="body2">
                    {i18n.t("guide_port")}: {connection.port}
                </Typography>
            ) : null}
            {children}
            {state ? (
                <Alert severity={state === "lan" && lanWarning
                    ? "warning" : "info"}>
                    {lanNote(state)}
                </Alert>
            ) : null}
        </CardFrame>
    );
}
