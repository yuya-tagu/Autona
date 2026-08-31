"""Contract tests for src/core/StateSchema.py (headless core Step 2).

Two kinds of check live here:

- **Snapshots of definitions that moved.** ``STATS_KEYS`` was a class
  attribute of ``AutonaScoreboardWidget`` and the display-option key set was
  written out twice (that widget's ``elements`` and
  ``WebServerActions._DISPLAY_OPTION_KEYS``). The values below are the
  pre-move ones, copied verbatim: the point of the move was that nothing
  about the schema changed, and these fail if the "move" quietly edited it.
  ``STATS_KEYS`` in particular is the 反映 hold's exemption list -- getting it
  wrong makes fetched stats invisible until an operator presses a button they
  have no reason to press.
- **Behaviour of the seeds.** ``SeedBoard`` is called from two places that
  used to carry their own copy, so what it does on a *second* call is now
  load-bearing: the lazily-built board reseeds over the manager's
  placeholder.

StateManager writes ./out relative to the CWD, so everything runs inside a
temp directory, and each test restores the shared state it touched -- this
module runs in the same process (and against the same singleton) as the
widget harness.
"""
import ast
import os
import re
import tempfile
from pathlib import Path

import pytest

from src.core import StateSchema as schema
from src.Helpers.AutonaDictHelper import deep_clone
from src.core.SettingsManager import SettingsManager
from src.StateManager import StateManager as SM

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_WORK_DIR = tempfile.mkdtemp(prefix="autona_schema_test_")
_ORIGINAL_CWD = os.getcwd()

# StateManager.Initialize() is deliberately NOT called here. It is a
# once-per-process no-op after the first caller, and in a full-suite run that
# caller is tests/test_state_manager_held.py -- pytest collects by ascending
# filename and "test_state_manager_held" sorts before "test_state_schema", so
# by the time this module is imported the one call is already spent (in *that*
# module's temp directory). Nothing here needs the load half of Initialize
# either: these tests seed state rather than restore it. Only the directories
# the export thread writes into have to exist, and since Initialize() will not
# create them here they are created directly.
os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)


@pytest.fixture(autouse=True)
def _isolated_state():
    """Run under the temp dir and hand the shared state back untouched.

    The seeds write whole subtrees (score.{N}, top8, commentary), and the
    widget harness in later test modules drives the same StateManager
    singleton. Snapshotting is cheaper to get right than enumerating every
    key a seed might have created.

    ``lastSavedState`` and ``revision`` ride along: they are the published
    view and the delta counter, so restoring ``state`` alone would leave a
    later module diffing its own writes against subtrees this module made up.
    """
    os.chdir(_WORK_DIR)
    with SM.lock:
        snapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = snapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
        os.chdir(_ORIGINAL_CWD)


# ── Definitions that moved out of the widgets ────────────────────────────


def test_stats_keys_are_the_pre_move_exemption_list():
    # Verbatim from AutonaScoreboardWidget.STATS_KEYS before the move.
    assert list(schema.STATS_KEYS) == [
        "last_sets", "history_sets", "recent_sets", "upset_factor"]


def test_stats_keys_register_as_hold_exemptions():
    # The list is only worth anything if HoldSubtree still accepts its shape
    # (it was a list, it is a tuple now) and expands every entry.
    prefix = "score.9"
    try:
        SM.HoldSubtree(prefix, exempt=schema.STATS_KEYS)
        exempt = dict(SM.heldExempt)[prefix]
        assert [dotted for dotted, _bracket in exempt] == [
            f"{prefix}.{key}" for key in schema.STATS_KEYS]
    finally:
        with SM.lock:
            SM.heldPrefixes.discard(prefix)
            SM.heldExempt.pop(prefix, None)
            SM.heldKeys.pop(prefix, None)


def test_display_options_are_the_pre_move_key_set():
    # Verbatim from AutonaScoreboardWidget.elements (label column dropped --
    # it stayed on the Qt side) and identical to the tuple that used to be
    # WebServerActions._DISPLAY_OPTION_KEYS.
    assert [(o.key, list(o.fields), o.default) for o in schema.DISPLAY_OPTIONS] == [
        ("show_sponsor", ["team"], True),
        ("show_name", ["name"], True),
        ("show_seed", ["seedDisplay"], True),
        ("show_characters", ["characters"], True),
        ("show_social", ["twitter"], True),
        ("show_real_name", ["real_name"], False),
        ("show_location", ["country", "state"], True),
        ("show_additional", ["custom_textbox"], False),
    ]
    assert schema.DISPLAY_OPTION_KEYS == (
        "show_sponsor", "show_name", "show_seed", "show_characters",
        "show_social", "show_real_name", "show_location", "show_additional")


def test_default_colors_are_the_pre_move_values():
    # Verbatim from AutonaFeatureColors before the move (and from
    # AutonaBoardWidget before that). Spelled out rather than read back from
    # the module: these are what every layout renders when the operator has
    # picked nothing, and the same pair is the CSS --theme-color /
    # --accent-color default (Design.md §5).
    assert schema.DEFAULT_THEME_COLOR == "#181B21"
    assert schema.DEFAULT_ACCENT_COLOR == "#f0a500"


def test_retired_roots_are_the_subtrees_the_launch_drops():
    # Every entry is a whole subtree deleted at every launch, so a wrong one
    # destroys live data on the next start. Literal on purpose.
    assert list(schema.RETIRED_ROOTS) == [
        "bracket",         # legacy full bracket, replaced by top8.*
        "player_list",     # retired PLAYERS roster editor
        "player_editor",   # editor dialog scratch subtree
    ]
    # AutonaPlayerBrowserWidget builds its dialog path from this constant, so
    # the two spellings have to agree.
    assert schema.PLAYER_EDITOR_ROOT == "player_editor"


def test_stale_keys_are_the_retired_event_tab_keys():
    # Residual keys from the layouts that kept the stream title and
    # destination on the EVENT tab. Unset (not deleted wholesale) so a rename
    # here silently stops cleaning them up instead of failing loudly.
    assert list(schema.STALE_KEYS) == [
        "tournamentInfo.streamTitleEdit",
        "tournamentInfo.streamUrl",
    ]


def test_stream_asset_versions_pin_both_keys_and_paths():
    # The paths are where AutonaStreamSettingsWidget saves the files and what
    # AutonaWebServer serves; the keys are what the OBS overlays append as a
    # cache-bust query string. A typo on either side fails silently -- a
    # missing file publishes version 0 and the overlay keeps its cached image.
    assert schema.STREAM_ORG_ICON_PATH == "./layout/stream_org_icon.png"
    assert schema.STREAM_ORG_THUMB_PATH == "./layout/stream_org_thumbnail.png"
    assert list(schema.STREAM_ASSET_VERSIONS) == [
        ("stream.orgIconVersion", "./layout/stream_org_icon.png"),
        ("stream.orgThumbVersion", "./layout/stream_org_thumbnail.png"),
    ]


def test_display_options_match_the_web_ui_list():
    """The Web UI carries the fourth copy, and no build step ties them.

    web/src/ScoreboardPage/displayOptions.js cannot import from Python, so
    the eye menu the operator sees in the browser and the key set the desktop
    accepts can drift silently -- a key the Web UI offers but the desktop
    rejects fails the POST with "unknown display option".
    """
    source = (PROJECT_ROOT / "web" / "src" / "ScoreboardPage"
              / "displayOptions.js").read_text(encoding="utf-8")
    entries = re.findall(
        r'\{\s*key:\s*"([^"]+)"[^}]*?default:\s*(true|false)\s*\}', source)
    assert entries, "could not parse DISPLAY_OPTIONS out of displayOptions.js"
    assert [(key, value == "true") for key, value in entries] == [
        (option.key, option.default) for option in schema.DISPLAY_OPTIONS]


# ── Wiring: who calls the seeds, and who still owns the labels ───────────
#
# Both checks read source text instead of importing. src/Autona.py must never
# be imported from a test -- that import *is* the app's startup sequence
# (agent-pitfalls #1) -- and AutonaScoreboardWidget's labels dict is a local,
# so it survives no further than __init__ and cannot be read off an instance.


def _function_node(relative_path: str, name: str):
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{relative_path}: no function named {name}")


def _called_function_names(node):
    """Bare names of everything called under `node` (`a.b.C()` -> "C")."""
    return {
        ast.unparse(call.func).rsplit(".", 1)[-1]
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    }


def test_window_seed_panel_state_runs_both_seeds_in_one_save_block():
    """The GUI launch has to reach the core seed, and in one save cycle.

    Window._SeedPanelState is the only caller of StateSchema.SeedPanelState():
    if it stops calling it, every OBS overlay reads a state that was never
    normalised (unresolved Top 8 slots, legacy commentary schema, retired
    subtrees restored from program_state.json) and no test above would
    notice, because they all call the seed directly. SeedColorState() is the
    one seed the core cannot host yet -- colors.{feature}.* is still owned by
    AutonaFeatureColors -- so it has to run in the same block.
    """
    function = _function_node("src/Autona.py", "_SeedPanelState")
    blocks = [node for node in function.body if isinstance(node, ast.With)]
    assert len(blocks) == 1, (
        "_SeedPanelState should be exactly one `with StateManager.SaveBlock():` "
        "-- a seed outside it fires its own save cycle at launch")
    assert {ast.unparse(item.context_expr.func) for item in blocks[0].items} == {
        "StateManager.SaveBlock"}

    called = _called_function_names(blocks[0])
    assert "SeedPanelState" in called, (
        "Window._SeedPanelState no longer calls StateSchema.SeedPanelState()")
    assert "SeedColorState" in called, (
        "the per-feature color seed dropped out of the launch")


def test_eye_menu_labels_cover_every_display_option():
    """The labels are the one half of the eye menu Qt still owns.

    AutonaScoreboardWidget iterates schema.DISPLAY_OPTIONS and looks each
    label up by key, falling back to the raw key rather than raising -- which
    is right for the board but would ship "show_real_name" as a menu string
    if the two lists drifted. This is the check that fallback relies on.
    """
    source = (PROJECT_ROOT / "src" / "AutonaScoreboardWidget.py").read_text(
        encoding="utf-8")
    literals = [
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
        and any(isinstance(target, ast.Name) and target.id == "labels"
                for target in node.targets)
    ]
    assert len(literals) == 1, (
        "expected exactly one `labels = {...}` in AutonaScoreboardWidget")
    keys = [key.value for key in literals[0].keys]
    assert len(keys) == len(set(keys)), f"duplicate label keys: {keys}"
    assert set(keys) == set(schema.DISPLAY_OPTION_KEYS)


# ── SeedBoard ────────────────────────────────────────────────────────────

_BOARD_SKELETON = {
    "last_sets": {"1": {}, "2": {}},
    "history_sets": {"1": {}, "2": {}},
    "station_queue": {},
}


@pytest.mark.parametrize("number", [1, 2])
def test_seed_board_writes_the_skeleton_boards_1_and_2_share(number):
    schema.SeedBoard(number)
    assert SM.Get(f"score.{number}") == _BOARD_SKELETON


@pytest.mark.parametrize("number", [3, "3"])
def test_seed_board_gives_the_crew_board_only_its_root(number):
    # Board 3 is AutonaCrewBattleWidget: no set history, no station queue.
    # The rest of its subtree comes from its own _writeInitialState().
    schema.SeedBoard(number)
    assert SM.Get("score.3") == {}


def test_seed_board_resets_rather_than_fills_in():
    """Documents the *actual* behaviour, which is not "leave what exists".

    ``Set(prefix, {})`` replaces the subtree, so a second call wipes whatever
    the board wrote. That is what the lazy build depends on -- a board built
    after launch reseeds over the manager's placeholder -- and it is why this
    must never be called on a live board.
    """
    schema.SeedBoard(1)
    SM.Set("score.1.team.1.teamName", "Alpha")
    SM.Set("score.1.last_sets.1.0", {"id": 1})

    schema.SeedBoard(1)

    assert SM.Get("score.1") == _BOARD_SKELETON
    assert SM.Get("score.1.team.1.teamName", None) is None


def test_seed_board_is_repeatable():
    schema.SeedBoard(2)
    first = deep_clone(SM.Get("score.2"))
    schema.SeedBoard(2)
    assert SM.Get("score.2") == first


# ── SeedPanelState ───────────────────────────────────────────────────────


@pytest.fixture
def _blank_settings():
    """Run the seed against a settings file with nothing stored yet.

    The dict is swapped in place rather than written through
    SettingsManager.Set, which would rewrite settings.json.
    """
    previous = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        yield
    finally:
        SettingsManager.settings = previous


def test_seed_panel_state_publishes_the_default_tournament_colors(
        _blank_settings):
    schema.SeedPanelState()
    assert SM.Get("tournamentInfo.themeColor") == schema.DEFAULT_THEME_COLOR
    assert SM.Get("tournamentInfo.accentColor") == schema.DEFAULT_ACCENT_COLOR


def test_seed_panel_state_fills_the_slot_fixed_subtrees(_blank_settings):
    SM.Unset("top8")
    SM.Unset("commentary")

    schema.SeedPanelState()

    assert sorted(SM.Get("top8.players").keys()) == sorted(
        schema.TOP8_PLAYER_SLOTS)
    assert sorted(SM.Get("top8.matches").keys()) == sorted(
        schema.TOP8_MATCH_IDS)
    commentary = SM.Get("commentary")
    assert sorted(commentary.keys()) == sorted(schema.COMMENTARY_SLOTS)
    for slot in schema.COMMENTARY_SLOTS:
        assert set(schema.COMMENTARY_SLOT_KEYS) <= set(commentary[slot])


def test_seed_panel_state_fills_missing_display_options(_blank_settings):
    # The only other writer is a 1v1 / team board's constructor, so an
    # operator whose saved format is the crew board (score.3 -- no eye menu)
    # would otherwise have no display_options in the state at all, and the
    # Web UI / OBS overlays would render every field regardless of settings.
    SM.Unset("display_options")

    schema.SeedPanelState()

    assert SM.Get("display_options") == dict(schema.DISPLAY_OPTION_DEFAULTS)


def test_seed_panel_state_reads_missing_display_options_from_settings():
    # SettingsManager is the persisted source of truth (every toggle writes
    # it, and a board rewrites all eight from it on build), so the seed reads
    # the same place -- otherwise a headless run would publish the factory
    # default where a GUI launch publishes the operator's.
    previous = SettingsManager.settings
    SettingsManager.settings = {"display_options": {"show_real_name": True}}
    try:
        SM.Unset("display_options")
        schema.SeedPanelState()
        assert SM.Get("display_options.show_real_name") is True
    finally:
        SettingsManager.settings = previous


def test_seed_panel_state_keeps_stored_display_options(_blank_settings):
    # Missing keys only. A value LoadState restored is already published, so
    # overwriting it with a default would flip the overlays for everyone
    # until a board is built -- and for the whole session on the crew board.
    SM.Unset("display_options")
    SM.Set("display_options.show_name", False)       # schema default: True
    SM.Set("display_options.show_real_name", True)   # schema default: False

    schema.SeedPanelState()

    assert SM.Get("display_options.show_name") is False
    assert SM.Get("display_options.show_real_name") is True
    # The keys that really were missing still got filled.
    assert SM.Get("display_options.show_sponsor") is True


def test_seed_panel_state_purges_retired_and_stale_keys(_blank_settings):
    for root in schema.RETIRED_ROOTS:
        SM.Set(f"{root}.leftover", "from an older version")
    for stale in schema.STALE_KEYS:
        SM.Set(stale, "from an older layout")

    schema.SeedPanelState()

    for root in schema.RETIRED_ROOTS:
        assert SM.Get(root, None) is None, root
    for stale in schema.STALE_KEYS:
        assert SM.Get(stale, None) is None, stale


def test_seed_panel_state_publishes_zero_for_a_missing_asset(_blank_settings):
    # No ./layout/stream_org_icon.png in the temp dir: 0 is the "no asset"
    # version the OBS overlays expect, not a missing key.
    schema.SeedPanelState()
    for key, _path in schema.STREAM_ASSET_VERSIONS:
        assert SM.Get(key) == 0


def test_seed_panel_state_converges_after_the_first_pass(_blank_settings):
    """Pinned as it behaves, which is *not* idempotent on the first pass.

    A state with no top8 takes the ``top8.DefaultState()`` branch, and that
    default carries the match slots unresolved ([0, 0]); the next pass takes
    the "normalise a restored state" branch, where ``Recompute()`` derives
    them. From there the seed is stable.

    Pre-existing launch behaviour that Step 2 moved without changing -- the
    practical effect is that a brand-new install's Top 8 overlay shows TBD
    slots until the second launch. Recorded here rather than fixed, so that a
    later fix is a deliberate change with a test to update.

    State only: GetFullState also carries the revision counter, which is
    supposed to move on every write.
    """
    schema.SeedPanelState()
    assert SM.Get("top8.matches.wsf1.slots") == [0, 0]

    schema.SeedPanelState()
    assert SM.Get("top8.matches.wsf1.slots") == [1, 2]
    second = deep_clone(SM.GetFullState()["state"])

    schema.SeedPanelState()
    assert SM.GetFullState()["state"] == second
