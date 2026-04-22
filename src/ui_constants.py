"""
Centralized UI design tokens for the Autona desktop application.

Based on the Autona Admin UI System (warm-dark palette, crimson brand,
OBS-style high-density UI). Keep in sync with:
  - assets/design-tokens.json
  - layout/main.css
  - 30_Products/参考資料/color-system.html
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
FONT_FAMILY_MONO = "JetBrains Mono"

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
BTN_MD = 28
BTN_LG = 36

# ─── Border Radius ──────────────────────────────────────────────

RADIUS_SM = 3
RADIUS_MD = 5
RADIUS_LG = 8

# ─── Palette (warm dark / warm light) ───────────────────────────

# Brand (crimson) is reserved for live/status signals only.
# Accent (warm orange) is the primary action color.
DARK = {
    "canvas":          "#0A0A0A",
    "bg_primary":      "#121212",
    "bg_secondary":    "#1A1A1A",
    "bg_tertiary":     "#252524",
    "bg_quaternary":   "#2F2F2D",
    "border_subtle":   "#252524",
    "border_default":  "#393936",
    "border_strong":   "#55534E",
    "text_primary":    "#F0EAE0",
    "text_secondary":  "#B5ADA0",
    "text_muted":      "#85807A",
    "text_label":      "#A29A90",
    "text_disabled":   "#524F4A",
    "brand":           "#DC143C",
    "brand_subtle":    "#2A1418",
    # Accent: based on the tab-bar gradient's top stop but nudged toward
    # brighter red for a bolder primary. Hover lifts further.
    "accent":          "#8A1A2C",
    "accent_hover":    "#A62238",
    "accent_subtle":   "#33121A",
    "gold":            "#F0C45A",
    "gold_subtle":     "#2E2A1E",
    "positive":        "#6EC77A",
    "negative":        "#DC143C",
    "on_accent":       "#FFFFFF",
}

LIGHT = {
    "canvas":          "#E8E7E4",
    "bg_primary":      "#F4F3F0",
    "bg_secondary":    "#FFFFFF",
    "bg_tertiary":     "#EAE8E3",
    "bg_quaternary":   "#D8D5CE",
    "border_subtle":   "#E0DDD6",
    "border_default":  "#C8C4BB",
    "border_strong":   "#9E968B",
    "text_primary":    "#1A1917",
    "text_secondary":  "#48433D",
    "text_muted":      "#76706B",
    "text_label":      "#58544E",
    "text_disabled":   "#B0AAA2",
    "brand":           "#C41232",
    "brand_subtle":    "#F5E0E4",
    "accent":          "#8A1A2C",
    "accent_hover":    "#A62238",
    "accent_subtle":   "#F6D8DD",
    "gold":            "#8E6812",
    "gold_subtle":     "#F0E8D0",
    "positive":        "#2E8C3E",
    "negative":        "#C41232",
    "on_accent":       "#FFFFFF",
}

# ─── Theme Detection ────────────────────────────────────────────

def _is_light_mode() -> bool:
    return SettingsManager.Get("light_mode", False)


def palette() -> dict:
    return LIGHT if _is_light_mode() else DARK


def semantic_colors() -> dict:
    p = palette()
    return {
        "field_readonly_bg":     p["bg_tertiary"],
        "field_readonly_text":   p["text_muted"],
        "text_secondary":        p["text_secondary"],
        "toggle_border":         p["border_default"],
        "toggle_checked_bg":     p["accent_subtle"],
        "toggle_checked_border": p["accent"],
    }


# ─── Style Generators ───────────────────────────────────────────

def readonly_style() -> str:
    c = semantic_colors()
    return (
        f"background-color: {c['field_readonly_bg']}; "
        f"color: {c['field_readonly_text']};"
    )


def disabled_style() -> str:
    return readonly_style()


def secondary_label_style() -> str:
    c = semantic_colors()
    return f"color: {c['text_secondary']}; font-size: {FONT_XS}pt;"


def auto_update_toggle_style() -> str:
    c = semantic_colors()
    return (
        f"QPushButton {{ border: 1px solid {c['toggle_border']}; "
        f"border-radius: {RADIUS_SM}px; }}\n"
        f"QPushButton:checked {{ background-color: {c['toggle_checked_bg']}; "
        f"border: 1px solid {c['toggle_checked_border']}; }}"
    )


def score_btn_style(scale: float = 1.0) -> str:
    p = palette()
    size = max(1, round(FONT_LG * scale))
    return (
        f"font-family: '{FONT_FAMILY_MONO}'; "
        f"font-size: {size}pt; font-weight: bold; color: {p['text_primary']};"
    )


def score_display_style(scale: float = 1.0) -> str:
    p = palette()
    size = max(1, round(FONT_XL * scale))
    return (
        f"font-family: '{FONT_FAMILY_MONO}'; "
        f"font-size: {size}pt; font-weight: 800; color: {p['gold']};"
    )


# ─── Paths ───────────────────────────────────────────────────────

def icon_path(name: str) -> str:
    return f"assets/icons/{name}"


# ─── qdarktheme custom_colors mapping ───────────────────────────

def _custom_colors(p: dict) -> dict:
    # Primary = accent (warm orange) per updated design system:
    # brand (crimson) is reserved for live/status signals, not buttons.
    return {
        "background":            p["bg_primary"],
        "foreground":            p["text_primary"],
        "primary":               p["accent"],
        "border":                p["border_default"],
        "input.background":      p["bg_secondary"],
        "statusBar.background":  p["bg_secondary"],
        "toolbar.background":    p["bg_primary"],
        "scrollbar.background":  p["bg_tertiary"],
    }


DARK_CUSTOM_COLORS = _custom_colors(DARK)
LIGHT_CUSTOM_COLORS = _custom_colors(LIGHT)

# ─── Uniform Widget Height ──────────────────────────────────────

_WIDGET_H = 26   # px — standard height for interactive controls (high-density)

# ─── Global QSS generator ───────────────────────────────────────

def _qss_for(p: dict) -> str:
    return f"""
/* === Base typography === */
QWidget {{
    font-family: "{FONT_FAMILY}", "Inter", "Noto Sans JP", -apple-system, sans-serif;
    font-size: {FONT_SM}pt;
    color: {p['text_primary']};
}}

/* Labels are text-only: no border, no background */
QLabel {{
    background: transparent;
    border: none;
    padding: 0;
}}

/* === Interactive controls: uniform dense height, subtle elevation === */
QPushButton, QToolButton {{
    min-height: {_WIDGET_H}px;
    padding: 0px 12px;
    border: 1px solid {p['border_default']};
    border-radius: {RADIUS_MD}px;
    background-color: {p['bg_tertiary']};
    color: {p['text_primary']};
    font-weight: 600;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {p['bg_quaternary']};
    border-color: {p['border_strong']};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {p['bg_secondary']};
    padding-top: 1px;
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {p['text_disabled']};
    background-color: {p['bg_secondary']};
    border-color: {p['border_subtle']};
}}
/* Primary action buttons (opt-in via objectName="primary") and default
   dialog buttons (QDialogButtonBox::Ok etc.) — both render on the
   accent colour, so the foreground must track on_accent (white) rather
   than falling back to the standard text colour. */
QPushButton#primary,
QPushButton:default {{
    background-color: {p['accent']};
    border-color: {p['accent']};
    color: {p['on_accent']};
}}
QPushButton#primary:hover,
QPushButton:default:hover {{
    background-color: {p['accent_hover']};
    border-color: {p['accent_hover']};
    color: {p['on_accent']};
}}
QPushButton#primary:pressed,
QPushButton:default:pressed {{
    color: {p['on_accent']};
}}

/* === Text inputs === */
QComboBox, QSpinBox, QLineEdit, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    min-height: {_WIDGET_H}px;
    padding: 0px 6px;
    border: 1px solid {p['border_default']};
    border-radius: {RADIUS_SM}px;
    background-color: {p['bg_secondary']};
    color: {p['text_primary']};
    selection-background-color: {p['accent_subtle']};
    selection-color: {p['text_primary']};
}}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus,
QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{
    border-color: {p['accent']};
}}
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    color: {p['text_disabled']};
    background-color: {p['bg_tertiary']};
}}
QComboBox QAbstractItemView {{
    background-color: {p['bg_secondary']};
    border: 1px solid {p['border_default']};
    selection-background-color: {p['accent_subtle']};
    selection-color: {p['text_primary']};
    outline: 0;
}}

QTextEdit, QPlainTextEdit {{
    background-color: {p['bg_secondary']};
    border: 1px solid {p['border_default']};
    border-radius: {RADIUS_SM}px;
    color: {p['text_primary']};
    selection-background-color: {p['accent_subtle']};
}}

/* === Tabs (accent-orange selection, clear separators) === */
QTabWidget::pane {{
    border: 1px solid {p['border_default']};
    background-color: {p['bg_primary']};
    top: -1px;
}}
QTabBar {{
    background-color: {p['bg_primary']};
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    padding: 7px 14px;
    margin-right: 2px;
    background-color: {p['bg_primary']};
    color: {p['text_muted']};
    border: 1px solid transparent;
    border-bottom: 3px solid transparent;
    font-weight: 700;
    font-size: {FONT_XS}pt;
}}
QTabBar::tab:hover {{
    color: {p['text_secondary']};
    background-color: {p['bg_tertiary']};
}}
QTabBar::tab:selected {{
    color: {p['accent']};
    background-color: {p['bg_secondary']};
    border: 1px solid {p['border_default']};
    border-bottom: 3px solid {p['accent']};
}}

/* The main-window dock tab bar (objectName="autonaDockTabBar") paints
   itself via AutonaDockTabPainter (installEventFilter), so no QSS rules
   are applied to it here — QSS for QTabBar::tab would be intercepted by
   QStyleSheetStyle before the custom paint runs. */

/* === Main window dock separators (make them visible) === */
QMainWindow::separator {{
    background-color: {p['border_default']};
    width: 3px;
    height: 3px;
}}
QMainWindow::separator:hover {{
    background-color: {p['accent']};
}}

/* === Dock widgets: card-like with accent title strip === */
QDockWidget {{
    color: {p['text_primary']};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    border: 1px solid {p['border_default']};
}}
QDockWidget::title {{
    background-color: {p['bg_tertiary']};
    color: {p['text_primary']};
    padding: 6px 12px;
    border-bottom: 2px solid {p['accent_subtle']};
    text-align: left;
    font-size: {FONT_SM}pt;
    font-weight: 700;
}}

/* === Menu bar & toolbar === */
QMenuBar {{
    background-color: {p['bg_primary']};
    color: {p['text_primary']};
    border-bottom: 1px solid {p['border_subtle']};
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {p['bg_tertiary']};
    color: {p['accent']};
}}
QMenu {{
    background-color: {p['bg_secondary']};
    color: {p['text_primary']};
    border: 1px solid {p['border_default']};
}}
QMenu::item {{
    padding: 5px 18px;
}}
QMenu::item:selected {{
    background-color: {p['bg_tertiary']};
    color: {p['accent']};
}}
QMenu::separator {{
    height: 1px;
    background: {p['border_subtle']};
    margin: 4px 6px;
}}
QToolBar {{
    background-color: {p['bg_primary']};
    border-bottom: 1px solid {p['border_subtle']};
    spacing: 4px;
    padding: 4px;
}}

/* === Tables / lists / trees === */
QHeaderView::section {{
    background-color: {p['bg_tertiary']};
    color: {p['text_label']};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {p['border_subtle']};
    border-bottom: 1px solid {p['border_subtle']};
    font-size: {FONT_SM}pt;
    font-weight: 700;
}}
QTableView, QListView, QTreeView {{
    background-color: {p['bg_primary']};
    alternate-background-color: {p['bg_secondary']};
    color: {p['text_primary']};
    border: 1px solid {p['border_subtle']};
    selection-background-color: {p['accent_subtle']};
    selection-color: {p['text_primary']};
    gridline-color: {p['border_subtle']};
    outline: 0;
}}
QTableView::item:hover, QListView::item:hover, QTreeView::item:hover {{
    background-color: {p['bg_tertiary']};
}}

/* === Scrollbars === */
QScrollBar:vertical {{
    background-color: {p['bg_primary']};
    width: 10px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background-color: {p['bg_primary']};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {p['border_default']};
    border-radius: 3px;
    min-height: 20px;
    min-width: 20px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background-color: {p['border_strong']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* === Status bar === */
QStatusBar {{
    background-color: {p['bg_secondary']};
    color: {p['text_muted']};
    border-top: 1px solid {p['border_subtle']};
    font-size: {FONT_XS}pt;
}}
QStatusBar::item {{
    border: none;
}}

/* === Group boxes as elevated cards === */
/* Title sits as a plain label inside the top-left of the card, not as a
   chip on the border. */
QGroupBox {{
    border: 1px solid {p['border_default']};
    border-radius: {RADIUS_MD}px;
    background-color: {p['bg_secondary']};
    margin-top: 4px;
    padding: 28px 10px 10px 10px;
    color: {p['text_primary']};
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    padding: 6px 10px 4px 10px;
    background: transparent;
    border: none;
    color: {p['text_label']};
    font-size: {FONT_SM}pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
/* Compact variant: list rows that don't need the card framing. Applied via
   setProperty("compact", True). Flat, minimal padding, subtle divider. */
QGroupBox[compact="true"] {{
    border: none;
    border-bottom: 1px solid {p['border_subtle']};
    border-radius: 0;
    background: transparent;
    margin-top: 0;
    padding: 4px 6px;
}}
QGroupBox[compact="true"]::title {{
    padding: 0;
    font-size: 0pt;
}}
QLabel#compactRankLabel {{
    color: {p['text_muted']};
    font-family: "{FONT_FAMILY_MONO}", monospace;
    font-weight: 700;
    font-size: {FONT_MD}pt;
    padding-right: 6px;
}}

/* Horizontal and vertical rules: visible separators */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {p['border_default']};
    background-color: {p['border_default']};
    max-height: 1px;
    max-width: 1px;
    border: none;
    margin: 4px 0;
}}
QFrame[frameShape="4"] {{
    max-width: none;
}}
QFrame[frameShape="5"] {{
    max-height: none;
    margin: 0 4px;
}}

/* === Checkboxes / radios === */
QCheckBox, QRadioButton {{
    color: {p['text_primary']};
    spacing: 6px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}

/* === Tooltips === */
QToolTip {{
    background-color: {p['bg_quaternary']};
    color: {p['text_primary']};
    border: 1px solid {p['border_default']};
    padding: 4px 8px;
    border-radius: {RADIUS_SM}px;
}}

/* === Progress bars === */
QProgressBar {{
    background-color: {p['bg_tertiary']};
    border: 1px solid {p['border_subtle']};
    border-radius: {RADIUS_SM}px;
    text-align: center;
    color: {p['text_secondary']};
    height: {_WIDGET_H}px;
}}
QProgressBar::chunk {{
    background-color: {p['accent']};
    border-radius: {RADIUS_SM}px;
}}
"""


def global_qss() -> str:
    return _qss_for(LIGHT if _is_light_mode() else DARK)


# Back-compat: module-level GLOBAL_QSS resolves to the current theme at
# import time. Callers that need live-updating styles should use
# global_qss() instead.
GLOBAL_QSS = global_qss()

# ─── Theme Change Listeners ─────────────────────────────────────

_theme_listeners: list = []


def on_theme_change(callback):
    _theme_listeners.append(callback)


def notify_theme_change():
    global GLOBAL_QSS
    GLOBAL_QSS = global_qss()
    for cb in _theme_listeners:
        try:
            cb()
        except Exception:
            pass
