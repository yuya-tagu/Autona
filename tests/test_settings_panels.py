"""Widget-level checks for the two settings surfaces migrated in Step 5-5.

The other panels this instalment touched (TOP 8, 実況, RESULT, 配信設定) are
built lazily and three of them own a QWebEngineView, so the harness does not
build them -- their coverage is the core-level one in test_top8_service.py /
test_actions.py plus the AST ratchet in
test_objectname_keypath_contract.py. These two are plain widgets, so the real
thing can be driven here:

* ``LayoutOptionsWidget`` -- one control per option an OBS layout exposes,
  four kinds, each of which used to write LayoutOptionsManager *and*
  StateManager itself. Both halves now come from
  ``AutonaActions.SetLayoutOption``, and what matters is that both still
  land, once, for every kind.
* the shared colour cards (``AutonaFeatureColors``) -- the Qt half of what
  moved to ``core/ColorService``. The cascade is tested headless in
  test_color_service.py; what is left to check here is that the buttons are
  still wired to it.

Layout items are deliberately never fetched (``findChildren``, not
``itemAtPosition``): a QLayoutItem wrapper outlives the C++ item these
widgets destroy at teardown, which is agent-pitfalls #14.
"""
import pytest
from qtpy.QtWidgets import QCheckBox, QComboBox, QLineEdit

from src.AutonaColorButton import AutonaColorButton
from src.core import ColorService as colors
from src.core import StateSchema as schema
from src.core.LayoutOptionsManager import LayoutOptionsManager
from src.core.SettingsManager import SettingsManager
from src.StateManager import StateManager as SM

BASE = "chip-options"

SETTINGS = [
    ("Show flags", "show_flags", "checkbox", False),
    ("Font", "font", "textbox", ""),
    ("Background", "background", "color", "#000000"),
    ("Corner", "corner", "dropdown", ["left", "right"]),
]


@pytest.fixture
def isolated(autonaApp):
    """Hand the three process-global stores back untouched."""
    layoutSettings = LayoutOptionsManager.settings
    appSettings = SettingsManager.settings
    LayoutOptionsManager.settings = {}
    SettingsManager.settings = {}
    with SM.lock:
        stateRoots = {
            root: SM.state.pop(root, None)
            for root in (schema.LAYOUT_OPTIONS_ROOT, schema.COLORS_ROOT)
        }
    try:
        yield autonaApp
    finally:
        autonaApp.settle()
        LayoutOptionsManager.settings = layoutSettings
        SettingsManager.settings = appSettings
        with SM.lock:
            for root, value in stateRoots.items():
                SM.state.pop(root, None)
                if value is not None:
                    SM.state[root] = value


def _destroy(widget):
    from qtpy.QtCore import QCoreApplication, QEvent

    widget.setParent(None)
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


# ── LayoutOptionsWidget ──────────────────────────────────────────────────

def test_every_control_kind_writes_both_halves_of_the_mirror(isolated):
    from src.LayoutOptions.LayoutOptionsWidget import LayoutOptionsWidget

    panel = LayoutOptionsWidget(BASE, SETTINGS)
    try:
        checkbox = panel.findChildren(QCheckBox)[0]
        lineEdit = panel.findChildren(QLineEdit)[0]
        colorButton = panel.findChildren(AutonaColorButton)[0]
        combo = panel.findChildren(QComboBox)[0]

        checkbox.setChecked(True)
        lineEdit.setText("Inter")
        colorButton.setColor("#123456")
        combo.setCurrentIndex(1)
        isolated.settle()

        for setting, expected in (
                ("show_flags", True),
                ("font", "Inter"),
                ("background", "#123456"),
                ("corner", "right")):
            assert LayoutOptionsManager.Get(f"{BASE}.{setting}") == expected, \
                setting
            assert SM.Get(
                f"{schema.LAYOUT_OPTIONS_ROOT}.{BASE}.{setting}") == expected, \
                setting
    finally:
        _destroy(panel)


def test_one_option_edit_is_one_save_cycle(isolated):
    """`layout_options.*` is not held, so every write is a delta.

    Two of them per edit -- which is what a widget owning half the mirror
    could have drifted into -- would double the overlay's reload rate while
    an operator drags a colour picker.
    """
    from src.LayoutOptions.LayoutOptionsWidget import LayoutOptionsWidget

    panel = LayoutOptionsWidget(BASE, SETTINGS)
    try:
        isolated.settle()
        checkbox = panel.findChildren(QCheckBox)[0]
        before = SM.revision

        checkbox.setChecked(True)
        isolated.settle()

        assert SM.revision == before + 1
    finally:
        _destroy(panel)


# ── The shared colour cards ──────────────────────────────────────────────

def _buttons(card):
    found = card.findChildren(AutonaColorButton)
    assert len(found) == 2, "a colour card is a theme button and an accent one"
    return {button.color(): button for button in found}


def test_the_tournament_card_runs_the_whole_cascade(isolated):
    from src.AutonaFeatureColors import MakeTournamentColorCard

    SettingsManager.Set("general.theme_color", "#111111")
    SettingsManager.Set("general.accent_color", "#222222")
    colors.SetFeatureColor(colors.FEATURE_BOARD, colors.KIND_THEME, "#aaaaaa")
    colors.SetFeatureColor(colors.FEATURE_BOARD, colors.KIND_ACCENT, "#bbbbbb")

    card = MakeTournamentColorCard()
    try:
        _buttons(card)["#111111"].setColor("#dddddd")
        isolated.settle()

        assert SM.Get(
            f"{schema.TOURNAMENT_INFO_ROOT}.{schema.THEME_COLOR_KEY}") == \
            "#dddddd"
        # The card is still wired to the service, so picking a tournament
        # theme dropped the per-feature theme overrides and left the accents.
        assert SM.Get(
            colors.FeatureColorKey(colors.FEATURE_BOARD, colors.KIND_THEME),
            None) is None
        assert SM.Get(
            colors.FeatureColorKey(
                colors.FEATURE_BOARD, colors.KIND_ACCENT)) == "#bbbbbb"
    finally:
        _destroy(card)


def test_a_feature_card_writes_only_its_own_feature(isolated):
    from src.AutonaFeatureColors import MakeFeatureColorCard

    SettingsManager.Set("general.theme_color", "#111111")
    SettingsManager.Set("general.accent_color", "#222222")

    card = MakeFeatureColorCard(colors.FEATURE_STATS, "統計の色")
    try:
        _buttons(card)["#222222"].setColor("#654321")
        isolated.settle()

        assert SM.Get(
            colors.FeatureColorKey(
                colors.FEATURE_STATS, colors.KIND_ACCENT)) == "#654321"
        assert SM.Get(
            colors.FeatureColorKey(
                colors.FEATURE_TOP8, colors.KIND_ACCENT), None) is None
        # The tournament pair is untouched by a per-feature pick.
        assert SettingsManager.Get("general.accent_color") == "#222222"
    finally:
        _destroy(card)
