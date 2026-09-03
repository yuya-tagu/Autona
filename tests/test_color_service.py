"""Contract tests for src/core/ColorService.py (headless core Step 5-5).

The theme / accent colors were the last key family whose read, write *and*
launch seed all lived next to the Qt cards that drive them
(src/AutonaFeatureColors.py). Step 2 moved every other seed into StateSchema
and deliberately left this one behind rather than split a family across two
owners; this module is what moving the family whole made testable -- **no
QApplication, no widget**.

What is pinned here is the part that was never obvious from the cards: the
cascade. Picking a tournament colour is an "apply this everywhere" action, so
it drops the per-feature overrides *of the same kind* and leaves the other
kind alone; a feature with no stored setting must stay absent from the state,
because that absence is what makes the overlay's
``colors.{feature}`` -> ``tournamentInfo`` fallback fire at all.

Isolation follows tests/test_actions.py: a temp CWD (SettingsManager rewrites
./user_data/settings.json on every Set) and snapshot/restore of the shared
StateManager and SettingsManager, because this module shares one process with
the widget harness.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core import ColorService as colors
from src.core import StateSchema as schema
from src.core.SettingsManager import SettingsManager
from src.Helpers.AutonaDictHelper import deep_clone
from src.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_colors_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(schema.COLORS_ROOT, None)
        SM.state.pop(schema.TOURNAMENT_INFO_ROOT, None)
        SM.lastSavedState.pop(schema.COLORS_ROOT, None)
        SM.lastSavedState.pop(schema.TOURNAMENT_INFO_ROOT, None)
        SM.changedKeys = []
    settingsSnapshot = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
        SettingsManager.settings = settingsSnapshot
        os.chdir(_ORIGINAL_CWD)


def _themeKey(feature):
    return colors.FeatureColorKey(feature, colors.KIND_THEME)


def _accentKey(feature):
    return colors.FeatureColorKey(feature, colors.KIND_ACCENT)


TOURNAMENT_THEME = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.THEME_COLOR_KEY}"
TOURNAMENT_ACCENT = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.ACCENT_COLOR_KEY}"


# ── key spellings ────────────────────────────────────────────────────────

def test_the_state_keys_are_the_schema_spellings():
    """One spelling of "themeColor" for the catalog and for the writer.

    The service used to carry its own ``_STATE_SUFFIX`` literals while the
    catalog listed the same two names twice more (COLORS_FEATURE_KEYS and
    TOURNAMENT_INFO_KEYS). Three copies is how a catalog and its writer drift
    -- the lesson instalment 3 recorded -- so the suffix map reads the schema
    now and this checks it still does.
    """
    assert colors._STATE_SUFFIX[colors.KIND_THEME] is schema.THEME_COLOR_KEY
    assert colors._STATE_SUFFIX[colors.KIND_ACCENT] is schema.ACCENT_COLOR_KEY
    assert schema.COLORS_FEATURE_KEYS == (
        schema.THEME_COLOR_KEY, schema.ACCENT_COLOR_KEY)
    assert schema.THEME_COLOR_KEY in schema.TOURNAMENT_INFO_KEYS
    assert schema.ACCENT_COLOR_KEY in schema.TOURNAMENT_INFO_KEYS
    assert _themeKey("board") == "colors.board.themeColor"
    assert TOURNAMENT_THEME == "tournamentInfo.themeColor"


def test_every_feature_card_has_a_feature_key():
    """The six panels that own an overlay, and nothing else."""
    assert colors.FEATURES == (
        "board", "thumbnail", "result", "top8", "commentary", "stats")


# ── reading ──────────────────────────────────────────────────────────────

def test_unset_colors_fall_back_to_the_autona_defaults():
    assert colors.GetTournamentColors() == (
        schema.DEFAULT_THEME_COLOR, schema.DEFAULT_ACCENT_COLOR)
    assert colors.GetFeatureColors(colors.FEATURE_TOP8) == (
        schema.DEFAULT_THEME_COLOR, schema.DEFAULT_ACCENT_COLOR)


def test_a_feature_without_an_override_inherits_the_tournament_colors():
    SettingsManager.Set("general.theme_color", "#111111")
    SettingsManager.Set("general.accent_color", "#222222")
    assert colors.GetFeatureColors(colors.FEATURE_RESULT) == (
        "#111111", "#222222")

    colors.SetFeatureColor(colors.FEATURE_RESULT, colors.KIND_THEME, "#333333")
    assert colors.GetFeatureColors(colors.FEATURE_RESULT) == (
        "#333333", "#222222")
    # ...and only that feature.
    assert colors.GetFeatureColors(colors.FEATURE_TOP8) == (
        "#111111", "#222222")


# ── writing ──────────────────────────────────────────────────────────────

def test_setting_a_feature_color_writes_both_sides_of_the_mirror():
    colors.SetFeatureColor(colors.FEATURE_STATS, colors.KIND_ACCENT, "#abcdef")
    assert SettingsManager.Get("colors.stats.accent_color") == "#abcdef"
    assert SM.Get(_accentKey(colors.FEATURE_STATS)) == "#abcdef"


def test_an_empty_color_is_ignored_on_both_setters():
    colors.SetFeatureColor(colors.FEATURE_BOARD, colors.KIND_THEME, "")
    colors.SetTournamentColor(colors.KIND_THEME, None)
    assert SM.Get(_themeKey(colors.FEATURE_BOARD), None) is None
    assert SM.Get(TOURNAMENT_THEME, None) is None
    assert SettingsManager.Get("general.theme_color", None) is None


def test_a_tournament_color_clears_the_overrides_of_that_kind_only():
    colors.SetFeatureColor(colors.FEATURE_BOARD, colors.KIND_THEME, "#aaaaaa")
    colors.SetFeatureColor(colors.FEATURE_BOARD, colors.KIND_ACCENT, "#bbbbbb")
    colors.SetFeatureColor(colors.FEATURE_TOP8, colors.KIND_THEME, "#cccccc")

    colors.SetTournamentColor(colors.KIND_THEME, "#dddddd")

    assert SM.Get(TOURNAMENT_THEME) == "#dddddd"
    assert SettingsManager.Get("general.theme_color") == "#dddddd"
    # Theme overrides gone, on both sides...
    assert SM.Get(_themeKey(colors.FEATURE_BOARD), None) is None
    assert SM.Get(_themeKey(colors.FEATURE_TOP8), None) is None
    assert SettingsManager.Get("colors.board.theme_color", None) is None
    # ...and the accent override untouched, which is what makes re-picking a
    # theme colour safe for an operator who customised only accents.
    assert SM.Get(_accentKey(colors.FEATURE_BOARD)) == "#bbbbbb"
    assert SettingsManager.Get("colors.board.accent_color") == "#bbbbbb"
    # The feature is back on the fallback path.
    assert colors.GetFeatureColors(colors.FEATURE_BOARD) == (
        "#dddddd", "#bbbbbb")


def test_a_tournament_color_is_one_save_cycle():
    """The write plus up to six clears, published together.

    Not cosmetic: `colors.*` is not held, so every unblocked write is its own
    delta -- an overlay would otherwise see the new tournament colour before
    the override that hides it has gone.
    """
    for feature in colors.FEATURES:
        colors.SetFeatureColor(feature, colors.KIND_THEME, "#aaaaaa")
    if SM._export_thread is not None:
        SM._export_thread.join()

    before = SM.revision
    colors.SetTournamentColor(colors.KIND_THEME, "#dddddd")
    assert SM.revision == before + 1


# ── the launch seed ──────────────────────────────────────────────────────

def test_the_seed_publishes_only_the_features_with_a_stored_setting():
    SettingsManager.Set("colors.commentary.theme_color", "#0f0f0f")

    colors.SeedColorState()

    assert SM.Get(_themeKey(colors.FEATURE_COMMENTARY)) == "#0f0f0f"
    # Absent, not empty: the layout JS falls back to tournamentInfo when the
    # colors.{feature} node is missing.
    assert SM.Get(_accentKey(colors.FEATURE_COMMENTARY), None) is None
    for feature in colors.FEATURES:
        if feature != colors.FEATURE_COMMENTARY:
            assert SM.Get(_themeKey(feature), None) is None


def test_the_seed_drops_a_published_key_whose_setting_went_away():
    SM.Set(_themeKey(colors.FEATURE_THUMBNAIL), "#123456")

    colors.SeedColorState()

    assert SM.Get(_themeKey(colors.FEATURE_THUMBNAIL), None) is None


def test_the_seed_of_an_untouched_install_writes_nothing():
    """The guard that keeps a launch from queueing six no-op changed keys."""
    if SM._export_thread is not None:
        SM._export_thread.join()
    before = SM.revision
    colors.SeedColorState()
    assert SM.revision == before
