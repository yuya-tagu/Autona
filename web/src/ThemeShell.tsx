import React from "react";
import {ThemeProvider} from "@mui/material/styles";
import {CssBaseline} from "@mui/material";
import {darkTheme} from "./themes";

interface Props {
    children: React.ReactNode;
}

// The app is dark-only. A runtime light/dark toggle used to exist but was
// removed: switching themes re-applied the desktop qdarktheme app-wide and
// froze the GUI for 30s+ re-polishing the proxy-embedded bracket. With no
// toggle, the backend never sets ui.light_mode, so the web UI is fixed dark.
export default function ThemeShell({children}: Props) {
    return (
        <ThemeProvider theme={darkTheme}>
            <CssBaseline />
            {children}
        </ThemeProvider>
    );
}
