"""
Layout primitives for the Autona desktop UI.

Centralises the visual contract for cards, section titles, form rows,
button hierarchy, two-column layouts, media previews, and the score
control panel. Built on top of `ui_constants` (color/spacing/font tokens)
— do not introduce hard-coded colors or sizes here; pull from `UI`.

Usage pattern:
  from . import ui_layout as L
  card = L.make_card(title="Tournament Info", child_layout=...)
  L.apply_section_title(label_widget)
  L.tag_button(btn, kind="primary")  # or "secondary" / "danger"

The functions either:
  - return a freshly constructed QWidget/QLayout, or
  - mutate an existing widget in-place (apply_*, tag_*).

Kept import-light: only qtpy + ui_constants. No side effects on import
beyond what `ui_constants` already triggers (palette/SettingsManager).
"""

from qtpy.QtWidgets import (
    QWidget, QLabel, QFrame, QGroupBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSizePolicy, QPushButton, QLayout,
)
from qtpy.QtCore import Qt

from . import ui_constants as UI


# ─── Button Hierarchy ────────────────────────────────────────────
#
# Three roles:
#   primary   — the dominant call-to-action in a section (accent fill).
#               Limit to one per visible group to preserve hierarchy.
#   secondary — neutral default (bg_tertiary). The unset state of QPushButton.
#   danger    — destructive / clearing actions. Renders with brand color.
#
# Implemented via objectName so QSS in ui_constants picks them up. The
# existing rule for `QPushButton#primary` already exists — we add danger
# below.

BUTTON_PRIMARY = "primary"
BUTTON_SECONDARY = ""        # default — leave objectName untouched
BUTTON_DANGER = "danger"


def tag_button(button: QPushButton, kind: str) -> None:
    """Tag a button with one of BUTTON_PRIMARY / BUTTON_DANGER /
    BUTTON_SECONDARY. Re-polishes so the global QSS selector
    (`QPushButton#primary` / `#danger`) kicks in even when the widget
    is already on screen."""
    button.setObjectName(kind or "")
    s = button.style()
    s.unpolish(button)
    s.polish(button)


# ─── Card style ──────────────────────────────────────────────────
#
# A "card" is a QGroupBox with the title rendered as an uppercase label
# on the top-left, a subtle border, and consistent inner padding. The
# global QSS already paints QGroupBox this way; this helper just builds
# one with the right inner layout + content margins so callers don't have
# to remember the spacing tokens.

CARD_PADDING = (UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD)
CARD_INNER_SPACING = UI.SPACE_SM


def make_card(title: str, parent: QWidget | None = None) -> QGroupBox:
    """Create an empty card (QGroupBox styled per ui_constants global QSS).
    Add child widgets via card.layout().addWidget(...).

    The QGroupBox stylesheet in ui_constants already supplies the title
    treatment (uppercase, letter-spacing, accent_subtle bottom rule), so
    this helper only normalises the inner layout."""
    group = QGroupBox(title, parent)
    layout = QVBoxLayout(group)
    layout.setContentsMargins(*CARD_PADDING)
    layout.setSpacing(CARD_INNER_SPACING)
    return group


def apply_card_layout(group: QGroupBox) -> None:
    """Normalise an existing QGroupBox loaded from .ui to use the standard
    card padding and inner spacing. Use this for groups defined in .ui
    files where we cannot rebuild the widget tree.

    Safe to call on a group whose layout is None (does nothing) — .ui
    forms always populate a layout, so the no-op branch is defensive."""
    layout = group.layout()
    if layout is None:
        return
    layout.setContentsMargins(*CARD_PADDING)
    layout.setSpacing(CARD_INNER_SPACING)


# ─── Section title style ─────────────────────────────────────────
#
# Used INSIDE a card when the card needs sub-sections (e.g. "Display"
# / "Game Settings" within a single Thumbnail card). Smaller and quieter
# than the card title so the hierarchy stays readable.

def apply_section_title(label: QLabel) -> None:
    """Style a QLabel as a section sub-title (uppercase, letter-spaced,
    text_label color, bold). Use only inside cards; the card itself
    already provides the top-level heading."""
    p = UI.palette()
    label.setStyleSheet(
        f"color: {p['text_label']};"
        f"font-size: {UI.FONT_SM}pt;"
        f"font-weight: 700;"
        f"text-transform: uppercase;"
        f"letter-spacing: 0.5px;"
        f"background: transparent;"
        f"border: none;"
        f"padding: 4px 0 2px 0;"
    )


def make_section_title(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    apply_section_title(label)
    return label


# ─── Form row layout ─────────────────────────────────────────────
#
# A single label-on-left, control(s)-on-right row. The label gets a
# fixed width so labels across multiple rows align vertically. Put rows
# inside a card via card.layout().addLayout(form_row(...)).

FORM_LABEL_WIDTH = 110   # px — accommodates "Tournament Name" / "配信先" etc.
FORM_ROW_SPACING = UI.SPACE_SM


def form_row(label_text: str, *controls: QWidget,
             label_width: int = FORM_LABEL_WIDTH) -> QHBoxLayout:
    """Build a horizontal form row: [label | control(s)…].
    The first control stretches to fill remaining width; subsequent
    controls take their preferred size."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(FORM_ROW_SPACING)
    label = QLabel(label_text)
    label.setFixedWidth(label_width)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(label)
    for i, ctrl in enumerate(controls):
        layout.addWidget(ctrl, 1 if i == 0 else 0)
    return layout


def apply_form_grid(grid: QGridLayout,
                    horizontal_spacing: int = UI.SPACE_MD,
                    vertical_spacing: int = UI.SPACE_SM) -> None:
    """Normalise a QGridLayout (e.g. from a .ui file) to the standard
    form spacing. Call after loading the .ui."""
    grid.setHorizontalSpacing(horizontal_spacing)
    grid.setVerticalSpacing(vertical_spacing)
    grid.setContentsMargins(0, 0, 0, 0)


# ─── Two-column layout ───────────────────────────────────────────
#
# A top-level horizontal split where two cards (or arbitrary widgets)
# sit side by side with a fixed gutter. Used for EVENT (info | stream)
# and similar dual-pane sections. Stretch ratios let callers control
# which side dominates when the window resizes.

TWO_COL_GUTTER = UI.SPACE_MD


def two_column(left: QWidget, right: QWidget,
               left_stretch: int = 1, right_stretch: int = 1,
               parent: QWidget | None = None) -> QWidget:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(TWO_COL_GUTTER)
    layout.addWidget(left, left_stretch)
    layout.addWidget(right, right_stretch)
    return container


# ─── Media preview layout ────────────────────────────────────────
#
# Standard frame for icon / banner / thumbnail previews: a thin border,
# fixed/min size for the QLabel, and a control row below for
# Load / Download / Default buttons.

MEDIA_PREVIEW_BORDER_RADIUS = UI.RADIUS_SM


def apply_media_preview(label: QLabel,
                        min_width: int | None = None,
                        min_height: int | None = None) -> None:
    """Style a QLabel that displays an image preview with a subtle
    bordered frame so the bounds are visible even when the pixmap is
    transparent or smaller than the slot.

    Sizes are opt-in: pass min_width / min_height only when the source
    .ui doesn't already constrain the slot. Forcing a minimum on a
    label that .ui pinned via maximumSize would push min above max and
    let Qt resolve the conflict in favour of min — which is what
    happened to streamThumbPreview before this guard was added."""
    p = UI.palette()
    if min_width is not None or min_height is not None:
        label.setMinimumSize(min_width or 0, min_height or 0)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"background-color: {p['bg_tertiary']};"
        f"border: 1px solid {p['border_subtle']};"
        f"border-radius: {MEDIA_PREVIEW_BORDER_RADIUS}px;"
    )


def media_action_row(*buttons: QPushButton) -> QHBoxLayout:
    """Standard button row that sits beneath a media preview.
    Buttons are left-aligned with a small gap; the row pushes a stretch
    on the right so the buttons hug the preview."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(UI.SPACE_SM)
    for btn in buttons:
        row.addWidget(btn)
    row.addStretch(1)
    return row


# ─── Score control panel layout ──────────────────────────────────
#
# The 5-row column in AutonaScoreboardWidget that holds:
#   [swap label] [swap btn]
#   [+1 P1] [+1 P2]
#   [score P1] [score P2]
#   [-1 P1] [-1 P2]
#   [reset btn] [reset label]
#
# The actual widgets are constructed in AutonaScoreboardWidget; we
# expose the spacing tokens + a helper that normalises an existing
# QVBoxLayout so the column can later opt-in by replacing inline magic
# numbers with these constants.

# Track the global spacing scale so the score control panel widens with the
# rest of the app. Button gap = SPACE_SM (the standard inter-element gap),
# row gap = SPACE_XS (half-step), preserving the original 2:1 ratio.
SCORE_PANEL_BUTTON_SPACING = UI.SPACE_SM
SCORE_PANEL_ROW_SPACING = UI.SPACE_XS


def apply_score_panel_layout(vbox: QVBoxLayout) -> None:
    """Normalise the outer QVBoxLayout of the score control column to
    the standard tight spacing. The inner per-row HBoxes still use
    SCORE_PANEL_BUTTON_SPACING for their internal gap."""
    vbox.setContentsMargins(0, 0, 0, 0)
    vbox.setSpacing(SCORE_PANEL_ROW_SPACING)
    vbox.setAlignment(Qt.AlignmentFlag.AlignTop)


def score_panel_row() -> QHBoxLayout:
    """Standard row used inside the score control column. Centred,
    button-spaced, no margins."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SCORE_PANEL_BUTTON_SPACING)
    row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return row


# ─── Helpers: divider ────────────────────────────────────────────

def horizontal_divider(parent: QWidget | None = None) -> QFrame:
    """Standard 1px horizontal rule using border_subtle. Use between
    grouped sections that don't warrant their own card border."""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
