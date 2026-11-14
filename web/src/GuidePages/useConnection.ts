import React from "react";
import {apiErrorStatus} from "../api";
import {getWithRetry} from "../PanelShell/requests";

// GET /api/v1/connection (ADR-005 S-final SF-3a): what the guide pages show
// of the server and its files. THIS_PC: refused (403, token or not) to a
// page from another computer -- it names this PC's folders.

export interface Connection {
    port: number;
    lan: boolean;
    // LAN access saved as on while the running server is this PC's only
    // (it takes effect at the next start).
    lanAtRestart: boolean;
    // The API at the address user-facing URLs advertise (this PC's LAN IP
    // with LAN access, else the loopback): the API and Companion guides'.
    baseUrl: string;
    // The loopback's: the OBS guide's (OBS runs on this PC).
    localBaseUrl: string;
    // out/'s absolute path, and the separator to join below it with.
    outDir: string;
    pathSeparator: string;
}

export type ConnectionStatus = "loading" | "ready" | "thisPcOnly" | "failed";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

export const CONNECTION_PATH = "/api/v1/connection";

export function useConnection() {
    const [connection, setConnection] = React.useState<Connection | null>(
        null);
    const [status, setStatus] = React.useState<ConnectionStatus>("loading");
    const [reads, setReads] = React.useState(0);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<Connection>>(CONNECTION_PATH, () => live)
            .then((body) => {
                if (live) {
                    setConnection(body.data);
                    setStatus("ready");
                }
            }, (error: unknown) => {
                if (!live) {
                    return;
                }
                if (apiErrorStatus(error) === 403) {
                    setStatus("thisPcOnly");
                } else {
                    console.error(error);
                    setStatus("failed");
                }
            });
        return () => {
            live = false;
        };
    }, [reads]);

    // Read again: the guides' "Refresh" (a port or LAN access saved since).
    const refresh = React.useCallback(() => setReads((count) => count + 1),
                                      []);

    return {connection, status, refresh};
}
