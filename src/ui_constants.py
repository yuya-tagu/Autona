"""
Centralized UI design tokens for the TSH desktop application.

All hardcoded styling values (fonts, spacing, colors, sizes) should be
defined here so the entire UI can be tuned from a single location.

Keep in sync with: assets/design-tokens.json, layout/main.css
"""

from .SettingsManager import SettingsManager

# ─── Typography ──────────────────────────────────────────────────

FONT_XS = 8
FONT_SM = 9
FONT_MD = 11
FONT_LG = 13
FONT_XL = 16

FONT_FAMILY = "LINE Seed JP"
FONT_FAMILY_CONDENSED = "Roboto Condensed"

# ─── Spacing ─────────────────────────────────────────────────────

SPACE_XS = 2
SPACE_SM = 4
SPACE_MD = 8
SPACE_LG = 12
SPACE_XL = 16

# ─── Icon & Button Sizes ────────────────────────────────────────

ICON_SM = 24
ICON_MD = 32
ICON_LG = 64

BTN_SM = 24
BTN_MD = 32
BTN_LG = 42

# ─── Border Radius ──────────────────────────────────────────────

RADIUS_SM = 4
RADIUS_MD = 8

# ─── Theme Detection ────────────────────────────────────────────

def _is_light_mode() -> bool:
    return SettingsManager.Get("light_mode", False)


def semantic_colors() -> dict:
    if _is_light_mode():
        return {
            "field_readonly_bg": "rgba(0,0,0,0.04)",
            "field_readonly_text": "rgba(0,0,0,0.55)",
            # secondary text
            "text_secondary": "rgba(0,0,0,0.45)",
            # toggle
            "toggle_border": "rgba(0,0,0,0.15)",
            "toggle_checked_bg": "rgba(46,137,255,0.15)",
            "toggle_checked_border": "rgba(46,137,255,0.6)",
        }
    else:
        return {
            "field_readonly_bg": "rgba(255,255,255,0.03)",
            "field_readonly_text": "rgba(255,255,255,0.7)",
            "text_secondary": "rgba(255,255,255,0.5)",
            "toggle_border": "rgba(255,255,255,0.2)",
            "toggle_checked_bg": "rgba(46,137,255,0.4)",
            "toggle_checked_border": "rgba(46,137,255,0.8)",
        }


# ─── Style Generators ───────────────────────────────────────────

def readonly_style() -> str:
    c = semantic_colors()
    return (
        f"background-color: {c['field_readonly_bg']}; color: "
        f"{c['field_readonly_text']};"
    )


def disabled_style() -> str:
    return readonly_style()


def secondary_label_style() -> str:
    c = semantic_colors()
    return f"color: {c['text_secondary']}; font-size: {FONT_XS}pt;"


def auto_update_toggle_style() -> str:
    c = semantic_colors()
    return (
        f"QPushButton {{ border: 1px solid {c['toggle_border']}; border-radius: "
        f"{RADIUS_SM}px; }}\n"
        f"QPushButton:checked {{ background-color: {c['toggle_checked_bg']}; "
        f"border: 1px solid {c['toggle_checked_border']}; }}"
    )


def score_btn_style() -> str:
    return f"font-size: {FONT_LG}pt; font-weight: bold;"


def score_display_style() -> str:
    return f"font-size: {FONT_XL}pt; font-weight: bold;"


# ─── Paths ───────────────────────────────────────────────────────

def icon_path(name: str) -> str:
    return f"assets/icons/{name}"


# ─── Blue-tinted Dark Theme Colors ──────────────────────────────

DARK_CUSTOM_COLORS = {
    "background": "#1b1e2b",
    "foreground": "#dce0e8",
    "primary": "#7eaaed",
    "border": "#333852",
    "input.background": "#252a3a",
    "statusBar.background": "#161926",
    "toolbar.background": "#212538",
    "scrollbar.background": "#ffffff08",
}

# ─── Uniform Widget Height ──────────────────────────────────────

_WIDGET_H = 28   # px — standard height for interactive controls

# ─── Global QSS ─────────────────────────────────────────────────

GLOBAL_QSS = f"""
/* Uniform height for all interactive controls */
QPushButton, QToolButton {{
    min-height: {_WIDGET_H}px;
    padding: 0px 8px;
}}
QComboBox, QSpinBox, QLineEdit {{
    min-height: {_WIDGET_H}px;
    padding: 0px 4px;
}}
QTabBar::tab {{
    padding: 4px 8px;
}}
"""

# ─── Theme Change Listeners ─────────────────────────────────────

_theme_listeners: list = []


def on_theme_change(callback):
    _theme_listeners.append(callback)


def notify_theme_change():
    for cb in _theme_listeners:
        try:
            cb()
        except Exception:
            pass
