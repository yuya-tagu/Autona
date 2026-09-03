"""The Qt cards for the tournament-wide and per-feature theme / accent colors.

The colors themselves — what they mean, where they are persisted, how a
feature falls back to the tournament pair and what a tournament-color change
does to the overrides — live in ``src/core/ColorService.py`` since headless
core Step 5 (instalment 5). This module is what is left once that moved out:
two card builders and the show-time refresher that keeps their swatches
honest.

The names below are re-exported from the service rather than re-declared, so
every existing importer (the six settings panels, ``Window._SeedPanelState``
and the two image renderers under ``src/result`` / ``src/thumbnail``) keeps
working unchanged. New code should import them from
``src.core.ColorService`` — a headless caller has no reason to load a module
that imports QtWidgets.

Import side effects: none (agent-pitfalls #1). Every read and write happens
inside a call.
"""

from qtpy.QtCore import QEvent, QObject
from qtpy.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton

# schema.DEFAULT_THEME_COLOR / DEFAULT_ACCENT_COLOR are Autona's own defaults,
# used when neither the tournament nor the feature has a stored color. They
# live in the core schema because the launch seed writes
# tournamentInfo.themeColor / accentColor from them before any panel exists
# (headless core Step 2).
from .core import StateSchema as schema
from .core.ColorService import (           # noqa: F401  (re-export)
    FEATURE_BOARD, FEATURE_COMMENTARY, FEATURE_RESULT, FEATURE_STATS,
    FEATURE_THUMBNAIL, FEATURE_TOP8, FEATURES,
    KIND_ACCENT, KIND_THEME,
    GetFeatureColors, GetTournamentColors, SeedColorState, SetFeatureColor,
    SetTournamentColor,
)
from .AutonaColorButton import AutonaColorButton
from . import ui_constants as UI
from . import ui_layout as L


# ── Shared color card ────────────────────────────────────────────────────

def _colorRow(labelText: str, button: AutonaColorButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(UI.SPACE_MD)
    row.addWidget(QLabel(labelText))
    row.addWidget(button)
    row.addStretch(1)
    return row


def _makeColorButton(color: str) -> AutonaColorButton:
    button = AutonaColorButton(color=color)
    button.setMinimumSize(32, 32)
    return button


class _ColorCardRefresher(QObject):
    """Re-reads a color card's stored colors every time the card is shown.

    The cards live on pages of the main window's QStackedWidget, so switching
    pages delivers QEvent.Show to the page and its children. That is enough to
    keep a card honest without any cross-panel signal wiring: a color edited
    on another panel — or a per-feature override dropped by a tournament-color
    change (SetTournamentColor) — is picked up the next time the panel is
    looked at.

    Signals are blocked while re-applying: AutonaColorButton.setColor() emits
    colorChanged, which would persist the value we are only reading back and
    re-pin an override that was just deliberately cleared. Blocking costs
    nothing visually — setColor() writes the swatch stylesheet outside the
    emit (AutonaColorButton.setColor).

    Parented to the card and installed as its event filter, mirroring
    ui_layout._CompleterPopupOnFocus.
    """

    def __init__(self, card: QGroupBox, buttons, read):
        super().__init__(card)
        self._buttons = buttons   # (theme button, accent button)
        self._read = read         # () -> (theme, accent)
        card.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show:
            self.Refresh()
        return False

    def Refresh(self):
        for button, color in zip(self._buttons, self._read()):
            blocked = button.signalsBlocked()
            button.blockSignals(True)
            try:
                button.setColor(color)
            finally:
                button.blockSignals(blocked)


def MakeFeatureColorCard(feature: str, title: str) -> QGroupBox:
    """Build the per-feature theme / accent card used by the six overlay
    settings panels.

    Layout: one row per color plus a "match the tournament" button. The card
    is a plain L.make_card, so callers just addWidget it into their left
    column.

    The colorChanged connections are made *after* construction on purpose:
    AutonaColorButton's constructor calls setColor(), which emits — connecting
    first would persist the initial value on every panel build (the existing
    BOARD-tab idiom).

    A _ColorCardRefresher keeps the swatches in sync with the stored colors on
    every show, which matters because a tournament-color change clears the
    per-feature overrides underneath an already-built panel.
    """
    card = L.make_card(title)
    theme, accent = GetFeatureColors(feature)

    btTheme = _makeColorButton(theme)
    btAccent = _makeColorButton(accent)
    btTheme.colorChanged.connect(
        lambda color: SetFeatureColor(feature, KIND_THEME, color))
    btAccent.colorChanged.connect(
        lambda color: SetFeatureColor(feature, KIND_ACCENT, color))

    card.layout().addLayout(_colorRow("テーマカラー", btTheme))
    card.layout().addLayout(_colorRow("アクセントカラー", btAccent))

    def _matchTournament():
        # Persist explicitly rather than relying on the setColor() emit:
        # AutonaColorButton defaults to ignore_same_color=True, so a button
        # already holding the tournament color would stay silent and the
        # override would never be written.
        tTheme, tAccent = GetTournamentColors()
        btTheme.setColor(tTheme)
        btAccent.setColor(tAccent)
        SetFeatureColor(feature, KIND_THEME, tTheme)
        SetFeatureColor(feature, KIND_ACCENT, tAccent)

    btMatch = QPushButton("大会のカラーに合わせる")
    btMatch.clicked.connect(_matchTournament)
    card.layout().addWidget(btMatch)

    _ColorCardRefresher(
        card, (btTheme, btAccent), lambda: GetFeatureColors(feature))
    return card


def MakeTournamentColorCard() -> QGroupBox:
    """Build the tournament-wide color card for the EVENT panel.

    Same shape as MakeFeatureColorCard — including the show-time refresh, for
    symmetry — but it writes the ``general.*`` settings / ``tournamentInfo.*``
    state, and its action button restores Autona's own defaults instead of
    copying another scope's colors.
    """
    card = L.make_card("大会カラー")
    theme, accent = GetTournamentColors()

    btTheme = _makeColorButton(theme)
    btAccent = _makeColorButton(accent)
    btTheme.colorChanged.connect(
        lambda color: SetTournamentColor(KIND_THEME, color))
    btAccent.colorChanged.connect(
        lambda color: SetTournamentColor(KIND_ACCENT, color))

    card.layout().addLayout(_colorRow("テーマカラー", btTheme))
    card.layout().addLayout(_colorRow("アクセントカラー", btAccent))

    def _resetToDefaults():
        # Explicit persistence for the same ignore_same_color reason as above.
        btTheme.setColor(schema.DEFAULT_THEME_COLOR)
        btAccent.setColor(schema.DEFAULT_ACCENT_COLOR)
        SetTournamentColor(KIND_THEME, schema.DEFAULT_THEME_COLOR)
        SetTournamentColor(KIND_ACCENT, schema.DEFAULT_ACCENT_COLOR)

    btDefault = QPushButton("Autonaデフォルトカラーを設定する")
    btDefault.clicked.connect(_resetToDefaults)
    card.layout().addWidget(btDefault)

    _ColorCardRefresher(card, (btTheme, btAccent), GetTournamentColors)
    return card
