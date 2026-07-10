import {createTheme, responsiveFontSizes} from "@mui/material/styles";

// Autona Admin UI System — cool-gray dark palette + warm crimson brand.
// The app is dark-only (Design.md §5.1). Accent is a scarlet (hue ~2°)
// between brand crimson and the tab-bar gradient's orange end; brand stays
// crimson and is reserved for live/status signals.
// Source of truth: assets/design-tokens.json (mirrors src/ui_constants.py).
// The contract test themes.test.js fails if DARK_TOKENS drifts from it.

export const DARK_TOKENS = {
    canvas:          "#0A0B0D",
    bgPrimary:       "#111317",
    bgSecondary:     "#191B20",
    bgTertiary:      "#23262C",
    bgQuaternary:    "#2D3138",
    bgQuinary:       "#383C44",
    borderSubtle:    "#23262C",
    borderDefault:   "#383C44",
    borderStrong:    "#53575F",
    textPrimary:     "#E9ECF1",
    textSecondary:   "#AFB3BB",
    textMuted:       "#81858E",
    textLabel:       "#9CA0A9",
    textDisabled:    "#4F525A",
    brand:           "#DC143C",
    brandSubtle:     "#2A1418",
    accent:          "#C33732",
    accentHover:     "#D8453E",
    accentSubtle:    "#3A1413",
    gold:            "#F0C45A",
    goldSubtle:      "#2E2A1E",
    positive:        "#6EC77A",
    btnNeutral:      "#3B3F47",
    btnNeutralHover: "#494D56",
    negative:        "#F04E54",
    onAccent:        "#FFFFFF",
    primaryBtnBg:    "#EFF1F5",
    primaryBtnText:  "#0E0F12",
    primaryBtnHover: "#DBDDE3",
};

// Fixed competitive identity colors (same on desktop, see design-tokens.json).
export const PLAYER_COLORS = {
    p1: "#DC143C",
    p2: "#3D8BE8",
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

function buildTheme() {
    const t = DARK_TOKENS;

    const base = createTheme({ palette: { mode: 'dark' } });

    return responsiveFontSizes(createTheme({
        breakpoints: {
            values: { xs: 0, sm: 600, md: 900, lg: 1100, xl: 1536 },
        },
        palette: {
            mode: 'dark',
            primary: base.palette.augmentColor({
                color: { main: t.accent, dark: t.accentHover, contrastText: t.onAccent },
                name: 'primary',
            }),
            secondary: base.palette.augmentColor({
                color: { main: t.brand, contrastText: t.textPrimary },
                name: 'secondary',
            }),
            // Semantic slots follow the desktop mapping (Design.md §5.1.4):
            // negative = errors/danger, positive = success, gold = warning.
            // brand crimson stays reserved for live/status signals.
            error:   { main: t.negative },
            warning: { main: t.gold },
            info:    { main: t.textSecondary },
            success: { main: t.positive },
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
                color: { main: PLAYER_COLORS.p1 },
                name: 'p1color',
            }),
            p2color: base.palette.augmentColor({
                color: { main: PLAYER_COLORS.p2 },
                name: 'p2color',
            }),
            // Exposed design tokens so components can read them directly.
            autona: t,
        },
        typography: TYPOGRAPHY,
        shape: { borderRadius: 5 },
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
            MuiCard: {
                styleOverrides: {
                    // Cards sit one surface level above the page, matching the
                    // desktop layering (window bg_primary -> card bg_secondary).
                    root: {
                        backgroundColor: t.bgSecondary,
                        border: `1px solid ${t.borderSubtle}`,
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
                        fontWeight: 700,
                        textTransform: 'none',
                        // Touch devices get taller tap targets, capped at the
                        // small-TextField height (40px) so buttons line up
                        // with the inputs. Mouse keeps the compact density
                        // (Designer unaffected).
                        '@media (pointer: coarse)': {
                            minHeight: 40,
                        },
                    },
                    // Primary action: near-white surface, near-black label
                    // (desktop primary_btn_* tokens).
                    containedPrimary: {
                        backgroundColor: t.primaryBtnBg,
                        color: t.primaryBtnText,
                        '&:hover': { backgroundColor: t.primaryBtnHover },
                    },
                    // Neutral (secondary) action: filled grey chip like the
                    // desktop btn_neutral buttons, not a bare outline.
                    outlined: {
                        backgroundColor: t.btnNeutral,
                        borderColor: t.btnNeutral,
                        color: t.textPrimary,
                        '&:hover': {
                            backgroundColor: t.btnNeutralHover,
                            borderColor: t.btnNeutralHover,
                        },
                    },
                    text: {
                        color: t.textPrimary,
                        '&:hover': { backgroundColor: t.bgTertiary },
                    },
                },
            },
            MuiIconButton: {
                styleOverrides: {
                    root: {
                        // Match the small-TextField height (40px), same as
                        // MuiButton above.
                        '@media (pointer: coarse)': {
                            minHeight: 40,
                            minWidth: 40,
                        },
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
            MuiTableCell: {
                styleOverrides: {
                    root: { borderBottom: `1px solid ${t.borderSubtle}` },
                },
            },
            MuiSkeleton: {
                styleOverrides: {
                    root: { backgroundColor: t.bgTertiary },
                },
            },
            MuiLinearProgress: {
                styleOverrides: {
                    colorPrimary: { backgroundColor: t.accentSubtle },
                },
            },
            MuiSnackbarContent: {
                styleOverrides: {
                    root: {
                        backgroundColor: t.bgQuaternary,
                        color: t.textPrimary,
                        border: `1px solid ${t.borderDefault}`,
                    },
                },
            },
        },
    }));
}

export const darkTheme = buildTheme();
