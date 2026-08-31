"""Tournament-wide and per-feature theme / accent colors.

Autona used to have a single pair of colors — SettingsManager
``general.theme_color`` / ``general.accent_color``, mirrored into StateManager
as ``tournamentInfo.themeColor`` / ``accentColor`` — shared by every OBS layout
and image renderer. This module keeps that pair as the *tournament* colors and
adds an optional per-feature override on top of it:

    SettingsManager  colors.{feature}.theme_color / accent_color   (正本)
    StateManager     colors.{feature}.themeColor  / accentColor    (表示用展開)

Resolution order is ``colors.{feature}`` -> tournament colors -> the layout's
own CSS default. Only the features the operator has explicitly set are written
into StateManager (see SeedColorState), so an unset feature has no
``colors.{feature}`` node at all and the layout JS falls back to
``tournamentInfo`` by itself.

The ``tournamentInfo.themeColor`` / ``accentColor`` keys keep their names and
their meaning (= the tournament colors) for backwards compatibility: existing
templates and ``out/`` consumers are unaffected.

Import side effects: none (agent-pitfalls #1). Every read and write happens
inside a call.
"""

from qtpy.QtCore import QEvent, QObject
from qtpy.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton

from .StateManager import StateManager
from .SettingsManager import SettingsManager
# schema.DEFAULT_THEME_COLOR / DEFAULT_ACCENT_COLOR are Autona's own defaults,
# used when neither the tournament nor the feature has a stored color. They
# live in the core schema because the launch seed writes
# tournamentInfo.themeColor / accentColor from them before any panel exists
# (headless core Step 2).
from .core import StateSchema as schema
from .AutonaColorButton import AutonaColorButton
from . import ui_constants as UI
from . import ui_layout as L

# Feature keys. One per settings panel that owns an overlay / rendered image.
FEATURE_BOARD = "board"
FEATURE_THUMBNAIL = "thumbnail"
FEATURE_RESULT = "result"
FEATURE_TOP8 = "top8"
FEATURE_COMMENTARY = "commentary"
FEATURE_STATS = "stats"

FEATURES = (
    FEATURE_BOARD,
    FEATURE_THUMBNAIL,
    FEATURE_RESULT,
    FEATURE_TOP8,
    FEATURE_COMMENTARY,
    FEATURE_STATS,
)

# Color kinds, and their key spelling on each side: SettingsManager uses
# snake_case, StateManager camelCase (the spelling the layouts already read).
KIND_THEME = "theme"
KIND_ACCENT = "accent"

_SETTINGS_SUFFIX = {
    KIND_THEME: "theme_color",
    KIND_ACCENT: "accent_color",
}
_STATE_SUFFIX = {
    KIND_THEME: "themeColor",
    KIND_ACCENT: "accentColor",
}


# ── Tournament colors (shared default for every feature) ─────────────────

def GetTournamentColors():
    """Return the (theme, accent) tournament colors, falling back to the
    Autona defaults when nothing has been stored yet."""
    return (
        SettingsManager.Get("general.theme_color", schema.DEFAULT_THEME_COLOR),
        SettingsManager.Get(
            "general.accent_color", schema.DEFAULT_ACCENT_COLOR),
    )


def SetTournamentColor(kind: str, color: str):
    """Persist one tournament color, republish it, and drop every per-feature
    override of the same kind.

    The StateManager key is the historical ``tournamentInfo.*`` one — layouts
    and ``out/`` consumers read it as "the tournament color".

    Changing a tournament color is an "apply this everywhere" action: clearing
    the overrides makes the layouts' ``colors.{feature}`` -> ``tournamentInfo``
    fallback deliver the new color to every overlay at once, instead of
    leaving the features the operator once customised stuck on their old
    color. A feature becomes independent again as soon as it is edited.
    Clearing is per kind, so re-picking the tournament theme leaves the
    per-feature accent overrides alone.
    """
    if not color:
        return
    # One save cycle for the whole cascade (1 write + up to 6 clears).
    with StateManager.SaveBlock():
        SettingsManager.Set(f"general.{_SETTINGS_SUFFIX[kind]}", color)
        StateManager.Set(f"tournamentInfo.{_STATE_SUFFIX[kind]}", color)
        for feature in FEATURES:
            _ClearFeatureColor(feature, kind)


def _ClearFeatureColor(feature: str, kind: str):
    """Drop one stored feature override if it exists.

    Both Unset helpers create the intermediate dicts on their way down
    (deep_unset in AutonaDictHelper), and SettingsManager.Unset rewrites
    settings.json on every call, so the existence guards are what keep an
    untouched feature from collecting empty ``colors.{feature}`` nodes and
    what keeps a single color pick from triggering six pointless file
    writes. The StateManager guard additionally avoids queueing a changed key
    for a path that was never published (same reason as in _SeedPanelState).
    """
    settingsPath = f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}"
    if SettingsManager.Get(settingsPath, None) is not None:
        SettingsManager.Unset(settingsPath)
    statePath = f"colors.{feature}.{_STATE_SUFFIX[kind]}"
    if StateManager.Get(statePath, None) is not None:
        StateManager.Unset(statePath)


# ── Per-feature colors ───────────────────────────────────────────────────

def GetFeatureColors(feature: str):
    """Return the (theme, accent) colors in effect for one feature.

    An unset feature inherits the tournament colors, which in turn fall back
    to the Autona defaults."""
    theme, accent = GetTournamentColors()
    return (
        SettingsManager.Get(f"colors.{feature}.theme_color", theme),
        SettingsManager.Get(f"colors.{feature}.accent_color", accent),
    )


def SetFeatureColor(feature: str, kind: str, color: str):
    """Persist one feature color and republish it to the overlays."""
    if not color:
        return
    SettingsManager.Set(f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}", color)
    StateManager.Set(f"colors.{feature}.{_STATE_SUFFIX[kind]}", color)


def SeedColorState():
    """Expand the stored per-feature colors into StateManager at launch.

    Called from Window._SeedPanelState() inside its SaveBlock, because the
    panels that own these cards are built on first reveal and the OBS overlays
    read the colors from launch.

    Only features with an explicit setting are written: leaving the
    ``colors.{feature}`` node absent is what makes the layout JS fall back to
    the tournament colors. A previously written key whose setting has since
    gone away is dropped for the same reason — guarded, because Unset queues a
    changed key unconditionally.
    """
    for feature in FEATURES:
        for kind in (KIND_THEME, KIND_ACCENT):
            path = f"colors.{feature}.{_STATE_SUFFIX[kind]}"
            stored = SettingsManager.Get(
                f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}", None)
            if stored:
                StateManager.Set(path, stored)
            elif StateManager.Get(path, None) is not None:
                StateManager.Unset(path)


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
