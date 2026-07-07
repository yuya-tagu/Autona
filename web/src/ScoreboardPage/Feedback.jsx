import React from "react";
import {Alert, Snackbar} from "@mui/material";
import i18n from "../i18n/config";

// Transient UI feedback (snackbars + per-action pending state). Kept in
// React context instead of Redux on purpose: none of this belongs in the
// synced program state (Design.md §2.2 R-React-2).

const SnackbarContext = React.createContext(() => {});

// Module-level escape hatch so class components (UpcomingSets) and plain
// modules can raise a snackbar without threading the context through props.
let globalShow = null;

export function showSnackbar(message, severity = "error") {
    if (globalShow) {
        globalShow(message, severity);
    } else {
        console.warn("Snackbar requested before FeedbackProvider mounted:", message);
    }
}

export function FeedbackProvider({children}) {
    const [snack, setSnack] = React.useState(null);

    const show = React.useCallback((message, severity = "error") => {
        setSnack({message, severity, key: Date.now()});
    }, []);

    React.useEffect(() => {
        globalShow = show;
        return () => { globalShow = null; };
    }, [show]);

    return (
        <SnackbarContext.Provider value={show}>
            {children}
            <Snackbar
                key={snack?.key}
                open={Boolean(snack)}
                autoHideDuration={4000}
                onClose={() => setSnack(null)}
                anchorOrigin={{vertical: "bottom", horizontal: "center"}}
            >
                {snack ? (
                    <Alert
                        severity={snack.severity}
                        variant="filled"
                        onClose={() => setSnack(null)}
                    >
                        {snack.message}
                    </Alert>
                ) : undefined}
            </Snackbar>
        </SnackbarContext.Provider>
    );
}

/**
 * Wraps an async action with pending state + error snackbar.
 * Usage:
 *   const {pending, run} = useApiAction();
 *   <Button loading={pending} onClick={() => run(() => apiGet("/..."))}>
 */
export function useApiAction() {
    const show = React.useContext(SnackbarContext);
    const [pending, setPending] = React.useState(false);

    const run = React.useCallback(async (action, {errorMessage} = {}) => {
        setPending(true);
        try {
            return await action();
        } catch (e) {
            console.error(e);
            show(errorMessage ?? i18n.t("request_failed"), "error");
            return undefined;
        } finally {
            setPending(false);
        }
    }, [show]);

    return {pending, run};
}
