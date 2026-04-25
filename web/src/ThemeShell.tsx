import React, {useEffect, useMemo} from "react";
import {ThemeProvider} from "@mui/material/styles";
import {CssBaseline} from "@mui/material";
import {useSelector} from "react-redux";
import {darkTheme, lightTheme} from "./themes";
import type {TshStateReduxState} from "./redux/tshState";

// localStorage cache key. Per Design.md §5.4.3 R-Theme-1..R-Theme-3,
// this is a flicker-prevention cache only — the source-of-truth is
// `tshState.ui.light_mode` (which mirrors SettingsManager on the
// backend, §5.4.1).
const LIGHT_MODE_CACHE_KEY = "ui.light_mode";

function readCachedLightMode(): boolean {
    try {
        const raw = window.localStorage.getItem(LIGHT_MODE_CACHE_KEY);
        if (raw === null) return false;
        return raw === "true";
    } catch {
        return false;
    }
}

function writeCachedLightMode(value: boolean) {
    try {
        window.localStorage.setItem(LIGHT_MODE_CACHE_KEY, value ? "true" : "false");
    } catch {
        // Storage may be disabled (private mode etc.) — silently ignore;
        // we just lose flicker protection on the next reload.
    }
}

interface RootStateShape {
    tshState: TshStateReduxState;
}

interface Props {
    children: React.ReactNode;
}

export default function ThemeShell({children}: Props) {
    // Tentative theme from localStorage cache (R-Theme-1) used until the
    // first snapshot arrives. After that, Redux drives the value.
    const cachedDefault = useMemo(readCachedLightMode, []);

    const lightMode = useSelector((state: RootStateShape) => {
        const fromState = state.tshState?.tshState?.ui?.light_mode;
        return typeof fromState === "boolean" ? fromState : cachedDefault;
    });

    // Mirror the live value back to localStorage so the next page load
    // can pick the right theme immediately (R-Theme-2 / R-Theme-3).
    useEffect(() => {
        writeCachedLightMode(!!lightMode);
    }, [lightMode]);

    const theme = lightMode ? lightTheme : darkTheme;

    return (
        <ThemeProvider theme={theme}>
            <CssBaseline />
            {children}
        </ThemeProvider>
    );
}
