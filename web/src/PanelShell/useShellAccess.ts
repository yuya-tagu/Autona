import React from "react";
import {apiErrorStatus, apiPost} from "../api";
import {WRITE_OPTIONS, getWithRetry} from "./requests";

// Whether this page is the desktop app's own window (ADR-005 S-final
// SF-4a): the shared view adds the shell's secret to its own fetches to
// /api/v1/ (src/AutonaWebPanelHost.py), which the SHELL routes ask for.
// GET /api/v1/access-token is the probe: 200 in the shared view, 403
// anywhere else -- a browser (this PC's too), OBS, another computer with
// the token or not, the headless app.
//
// What it answers is the access token, and it is kept in this hook's
// state only: never in sessionStorage / localStorage, a BroadcastChannel,
// the URL or autonaToken.ts's store (the page's own token for its writes
// from another computer), so it leaves with the page.

export type ShellAccess =
    // Reading.
    | "checking"
    // The desktop's shared view: the SHELL routes answer.
    | "shell"
    // Anywhere else: refused (403), as the SHELL routes always are there.
    | "elsewhere"
    // Neither: the read failed (the server is not up, timed out, ...).
    | "failed";

interface TokenAnswer {
    token: string;
    available: boolean;
}

interface Envelope<T> {
    ok: boolean;
    data: T;
}

export interface ShellAccessValue {
    access: ShellAccess;
    // The token in use; "" unless `access` is "shell" and one is.
    token: string;
    // Whether there is a token to use (settings.json read).
    available: boolean;
    // Read again (after "failed").
    retry: () => void;
    // Replace the token (POST /access-token:regenerate with confirm); the
    // new one is the hook's from then on. Rejects with api.ts's ApiError
    // (409 NOT_READY: settings.json could not be opened, nothing changed).
    regenerate: () => Promise<string>;
}

export const ACCESS_TOKEN_PATH = "/api/v1/access-token";
export const REGENERATE_PATH = "/api/v1/access-token:regenerate";

export function useShellAccess(): ShellAccessValue {
    const [access, setAccess] = React.useState<ShellAccess>("checking");
    const [answer, setAnswer] = React.useState<TokenAnswer>(
        {token: "", available: false});
    const [reads, setReads] = React.useState(0);
    const live = React.useRef(true);

    React.useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
        };
    }, []);

    React.useEffect(() => {
        let current = true;
        setAccess("checking");
        getWithRetry<Envelope<TokenAnswer>>(ACCESS_TOKEN_PATH, () => current)
            .then((body) => {
                if (current) {
                    setAnswer({token: body.data?.token ?? "",
                               available: Boolean(body.data?.available)});
                    setAccess("shell");
                }
            }, (error: unknown) => {
                if (!current) {
                    return;
                }
                setAnswer({token: "", available: false});
                if (apiErrorStatus(error) === 403) {
                    setAccess("elsewhere");
                } else {
                    console.error(error);
                    setAccess("failed");
                }
            });
        return () => {
            current = false;
        };
    }, [reads]);

    const retry = React.useCallback(() => setReads((count) => count + 1), []);

    const regenerate = React.useCallback(async () => {
        const text = await apiPost(REGENERATE_PATH, {confirm: true},
                                   WRITE_OPTIONS);
        const data = (JSON.parse(text) as Envelope<TokenAnswer>).data;
        const next = {token: data?.token ?? "",
                      available: Boolean(data?.available)};
        if (live.current) {
            setAnswer(next);
        }
        return next.token;
    }, []);

    return {access, token: answer.token, available: answer.available,
            retry, regenerate};
}
