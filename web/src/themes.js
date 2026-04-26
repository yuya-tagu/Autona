import {createTheme, responsiveFontSizes} from "@mui/material/styles";

// Autona Admin UI System — warm-neutral palette.
// Brand and accent share the crimson family per Design.md §5.1
// (accent = unified crimson #8A1A2C). Use `error`/`secondary` for live signals.
// Keep in sync with src/ui_constants.py and 30_Products/参考資料/color-system.html

const DARK_TOKENS = {
    canvas:          "#0A0A0A",
    bgPrimary:       "#121212",
    bgSecondary:     "#1A1A1A",
    bgTertiary:      "#252524",
    bgQuaternary:    "#2F2F2D",
    borderSubtle:    "#252524",
    borderDefault:   "#393936",
    borderStrong:    "#55534E",
    textPrimary:     "#F0EAE0",
    textSecondary:   "#B5ADA0",
    textMuted:       "#85807A",
    textLabel:       "#A29A90",
    textDisabled:    "#524F4A",
    brand:           "#DC143C",
    brandSubtle:     "#2A1418",
    accent:          "#8A1A2C",
    accentHover:     "#A62238",
    accentSubtle:    "#33121A",
    gold:            "#F0C45A",
    goldSubtle:      "#2E2A1E",
};

const LIGHT_TOKENS = {
    canvas:          "#E8E7E4",
    bgPrimary:       "#F4F3F0",
    bgSecondary:     "#FFFFFF",
    bgTertiary:      "#EAE8E3",
    bgQuaternary:    "#D8D5CE",
    borderSubtle:    "#E0DDD6",
    borderDefault:   "#C8C4BB",
    borderStrong:    "#9E968B",
    textPrimary:     "#1A1917",
    textSecondary:   "#48433D",
    textMuted:       "#76706B",
    textLabel:       "#58544E",
    textDisabled:    "#B0AAA2",
    brand:           "#C41232",
    brandSubtle:     "#F5E0E4",
    accent:          "#8A1A2C",
    accentHover:     "#A62238",
    accentSubtle:    "#F6D8DD",
    gold:            "#8E6812",
    goldSubtle:      "#F0E8D0",
};

const TYPOGRAPHY = {
    fontFamily: [
        '"LINE Seed JP"',
        '"Inter"',
        '"Noto Sans JP"',
        '-apple-system',
        'BlinkMacSystemFont',
        '"Segoe UI"',
        'Roboto',
        'sans-serif',
    ].join(','),
    fontSize: 13,
    button: {
        fontWeight: 700,
        letterSpacing: '0.04em',
        textTransform: 'none',
    },
};

function buildTheme(mode) {
    const t = mode === 'light' ? LIGHT_TOKENS : DARK_TOKENS;
    // accent is now crimson; both modes need light text on top per Design.md §5.1
    const onAccent = '#FFFFFF';

    const base = createTheme({ palette: { mode } });

    return responsiveFontSizes(createTheme({
        breakpoints: {
            values: { xs: 0, sm: 600, md: 900, lg: 1100, xl: 1536 },
        },
        palette: {
            mode,
            primary: base.palette.augmentColor({
                color: { main: t.accent, dark: t.accentHover, contrastText: onAccent },
                name: 'primary',
            }),
            secondary: base.palette.augmentColor({
                color: { main: t.brand, contrastText: t.textPrimary },
                name: 'secondary',
            }),
            error:   { main: t.brand },
            warning: { main: t.accent },
            info:    { main: t.textSecondary },
            success: { main: t.gold },
            background: {
                default: t.canvas,
                paper: t.bgPrimary,
            },
            text: {
                primary: t.textPrimary,
                secondary: t.textSecondary,
                disabled: t.textDisabled,
            },
            divider: t.borderSubtle,
            // Custom slots for player colors (kept for scoreboard usage).
            p1color: base.palette.augmentColor({
                color: { main: t.brand },
                name: 'p1color',
            }),
            p2color: base.palette.augmentColor({
                color: { main: '#3D8BE8' },
                name: 'p2color',
            }),
            // Exposed design tokens so components can read them directly.
            autona: t,
        },
        typography: TYPOGRAPHY,
        shape: { borderRadius: 4 },
        components: {
            MuiCssBaseline: {
                styleOverrides: {
                    body: {
                        backgroundColor: t.canvas,
                        color: t.textPrimary,
                    },
                },
            },
            MuiPaper: {
                styleOverrides: {
                    root: {
                        backgroundImage: 'none',
                        backgroundColor: t.bgPrimary,
                        borderColor: t.borderSubtle,
                    },
                },
            },
            MuiAppBar: {
                styleOverrides: {
                    root: {
                        backgroundColor: t.bgPrimary,
                        color: t.textPrimary,
                        borderBottom: `1px solid ${t.borderSubtle}`,
                        boxShadow: 'none',
                    },
                },
            },
            MuiButton: {
                styleOverrides: {
                    root: {
                        borderRadius: 4,
                        fontWeight: 700,
                        textTransform: 'none',
                    },
                    containedPrimary: {
                        backgroundColor: t.accent,
                        color: onAccent,
                        '&:hover': { backgroundColor: t.accentHover },
                    },
                    outlined: {
                        borderColor: t.borderDefault,
                        color: t.textPrimary,
                        '&:hover': {
                            borderColor: t.borderStrong,
                            backgroundColor: t.bgTertiary,
                        },
                    },
                    text: {
                        color: t.textPrimary,
                        '&:hover': { backgroundColor: t.bgTertiary },
                    },
                },
            },
            MuiOutlinedInput: {
                styleOverrides: {
                    root: {
                        backgroundColor: t.bgSecondary,
                        '& .MuiOutlinedInput-notchedOutline': {
                            borderColor: t.borderDefault,
                        },
                        '&:hover .MuiOutlinedInput-notchedOutline': {
                            borderColor: t.borderStrong,
                        },
                        '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                            borderColor: t.accent,
                        },
                    },
                },
            },
            MuiTab: {
                styleOverrides: {
                    root: {
                        textTransform: 'uppercase',
                        fontWeight: 700,
                        letterSpacing: '0.08em',
                        fontSize: 11,
                        color: t.textMuted,
                        '&.Mui-selected': {
                            color: t.accent,
                            backgroundColor: t.bgQuaternary,
                        },
                    },
                },
            },
            MuiTabs: {
                styleOverrides: {
                    indicator: { backgroundColor: t.accent },
                },
            },
            MuiChip: {
                styleOverrides: {
                    root: { fontWeight: 700, letterSpacing: '0.04em' },
                },
            },
            MuiTooltip: {
                styleOverrides: {
                    tooltip: {
                        backgroundColor: t.bgQuaternary,
                        color: t.textPrimary,
                        border: `1px solid ${t.borderDefault}`,
                        fontSize: 11,
                    },
                },
            },
            MuiDivider: {
                styleOverrides: {
                    root: { borderColor: t.borderSubtle },
                },
            },
        },
    }));
}

export const defaultTheme = createTheme({ palette: { mode: 'dark' } });
export const darkTheme = buildTheme('dark');
export const lightTheme = buildTheme('light');
