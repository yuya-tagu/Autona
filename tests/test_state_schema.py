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


def test_the_scoreboard_field_names_stay_inside_the_catalog():
    """The static names and the catalog must not drift apart.

    SCORE_MATCH_FIELDS / SCORE_TEAM_INPUT_KEYS are what AutonaActions writes
    and what AutonaScoreboardWidget finds its inputs by (headless core
    Step 5); SCORE_BOARD_KEYS / SCORE_TEAM_KEYS are what KEY_CATALOG
    enumerates. A name in one but not the other is either a key nothing
    documents or a key nothing writes.
    """
    assert set(schema.SCORE_MATCH_FIELDS) <= set(schema.SCORE_BOARD_KEYS)
    assert set(schema.SCORE_TEAM_INPUT_KEYS) <= set(schema.SCORE_TEAM_KEYS)
    # Spelled out too: these are the strings an existing program_state.json is
    # keyed by, so a "harmless" rename here is a data break.
    assert schema.SCORE_MATCH_FIELDS == ("phase", "pool", "match")
    assert schema.SCORE_TEAM_INPUT_KEYS == ("teamName", "losers")


def test_the_player_field_names_stay_inside_the_catalog():
    """Same contract for the player card (headless core Step 5, instalment 2).

    PLAYER_INPUT_KEYS is what AutonaScoreboardPlayerWidget finds its inputs
    by and what PlayerService accepts; SCORE_PLAYER_KEYS is what KEY_CATALOG
    enumerates. The catalog used to name three keys that were never written
    (`gamerTag`, `country_code`, `state_code` -- the first is a provider
    payload's field name, the other two are how the player *database* spells
    a location) and to omit `birthday`, which is exactly the drift an
    enumeration nothing consumes collects.
    """
    assert set(schema.PLAYER_INPUT_KEYS) <= set(schema.SCORE_PLAYER_KEYS)
    # Spelled out: an existing program_state.json is keyed by these.
    assert schema.PLAYER_TEXT_KEYS == (
        "team", "name", "real_name", "twitter", "custom_textbox")
    assert schema.PLAYER_LOCATION_KEYS == ("country", "state")
    assert schema.PLAYER_SEED_KEY == "seed"
    assert schema.PLAYER_BIRTHDAY_KEY == "birthday"
    # The retired spellings must not come back as catalog entries.
    assert "gamerTag" not in schema.SCORE_PLAYER_KEYS
    assert "country_code" not in schema.SCORE_PLAYER_KEYS
    assert "state_code" not in schema.SCORE_PLAYER_KEYS


def test_the_player_service_writes_only_catalogued_keys():
    """Every leaf PlayerService names has to be in the catalog.

    Read out of the source rather than by running it: the point is the
    *enumeration*, and a key that only some payload reaches at runtime would
    slip past a behavioural test. The regex only catches `Key(<literal>)`
    calls, though -- keys built from a variable (`SetText` / `SetLocation`
    / `SetSeed` / `SetBirthday` each take the field name as an argument) do
    not show up here. Those are covered by the other half of this pair:
    `test_the_player_field_names_stay_inside_the_catalog`'s
    `set(schema.PLAYER_INPUT_KEYS) <= set(schema.SCORE_PLAYER_KEYS)`, above.
    Together the two tests cover both call shapes.
    """
    source = (PROJECT_ROOT / "src" / "core"
              / "PlayerService.py").read_text(encoding="utf-8")
    leaves = set(re.findall(r'self\.Key\("([^"]+)"\)', source))
    assert leaves, "PlayerService no longer builds its keys through Key()"

    catalogued = set(schema.SCORE_PLAYER_KEYS) | {
        # Written whole rather than per index; the catalog spells out the
        # indexed leaves the dict expands into.
        "character",
    }
    assert leaves <= catalogued, (
        f"PlayerService writes {sorted(leaves - catalogued)}, which "
        "StateSchema.SCORE_PLAYER_KEYS does not list")


def test_the_crew_field_names_stay_inside_the_catalog():
    """Same contract for the crew board (headless core Step 5, instalment 4).

    The crew keys were never objectName-derived -- board 3 builds its rows in
    code -- but they were spelled out as string literals at each of the
    widget's thirty write sites, which is the same drift by a different
    route. They are named constants now, consumed by
    core/CrewBattleService.py, and KEY_CATALOG's crew entries are built from
    the same names rather than repeating them.
    """
    assert schema.CREW_MEMBER_NUM_KEY == "memberNum"
    assert schema.CREW_ACTIVE_MEMBER_KEY == "activeMember"
    assert schema.CREW_STOCK_KEY == "stock"
    assert schema.SCORE_CREW_TEAM_KEYS == (
        schema.CREW_MEMBER_NUM_KEY, schema.CREW_ACTIVE_MEMBER_KEY)
    assert schema.SCORE_CREW_PLAYER_KEYS == (schema.CREW_STOCK_KEY,)
    # Board 3's roster rides on the shared player subtree: a member's name is
    # the same key an entrant's tag is on boards 1/2, so an overlay written
    # for one reads the other.
    assert schema.PLAYER_TAG_KEY in schema.SCORE_PLAYER_KEYS
    for key in schema.SCORE_CREW_TEAM_KEYS:
        assert f"3.team.{{T}}.{key}" in schema.KEY_CATALOG["score"]
    for key in schema.SCORE_CREW_PLAYER_KEYS:
        assert f"3.team.{{T}}.player.{{M}}.{key}" in schema.KEY_CATALOG["score"]


def test_the_crew_service_writes_only_catalogued_keys():
    """Every leaf CrewBattleService names has to be in the catalog.

    Read out of the source for the same reason the PlayerService check is:
    the point is the enumeration, and a key only some payload reaches at
    runtime would slip past a behavioural test. The crew service builds its
    paths through _teamKey / _memberKey, plus the board-level fields it
    writes with an f-string.

    Only the *literal* leaves show up here -- the ones taken from a schema
    constant are covered by the test above, which is what leaves so few of
    them. The anchors below are there so that this does not degenerate into
    a test of the empty set if the shape of those calls ever changes.
    """
    source = (PROJECT_ROOT / "src" / "core"
              / "CrewBattleService.py").read_text(encoding="utf-8")

    teamLeaves = set(re.findall(r'_teamKey\([^,]+,\s*"([^"]+)"\)', source))
    memberLeaves = set(
        re.findall(r'_memberKey\([^,]+,\s*[^,]+,\s*"([^"]+)"\)', source))
    boardLeaves = set(re.findall(r'\{self\.prefix\}\.([a-z_]+)"', source))
    assert "score" in teamLeaves, (
        "the crew team total is no longer written through _teamKey(...) -- "
        "update this test's reader before trusting its result")
    assert "best_of" in boardLeaves, (
        "the crew set length is no longer written as a prefix f-string -- "
        "update this test's reader before trusting its result")

    catalogued = set(schema.SCORE_TEAM_KEYS) | set(schema.SCORE_CREW_TEAM_KEYS)
    assert teamLeaves <= catalogued, (
        f"CrewBattleService writes team key(s) {sorted(teamLeaves - catalogued)} "
        "that StateSchema does not list")

    catalogued = set(schema.SCORE_PLAYER_KEYS) | set(
        schema.SCORE_CREW_PLAYER_KEYS)
    assert memberLeaves <= catalogued, (
        "CrewBattleService writes member key(s) "
        f"{sorted(memberLeaves - catalogued)} that StateSchema does not list")

    assert boardLeaves <= set(schema.SCORE_BOARD_KEYS), (
        "CrewBattleService writes board key(s) "
        f"{sorted(boardLeaves - set(schema.SCORE_BOARD_KEYS))} that "
        "StateSchema.SCORE_BOARD_KEYS does not list")


def test_the_scoreboard_families_left_the_dynamic_key_inventory():
    """DYNAMIC_KEY_FAMILIES is the Step 5 checklist, so it has to shrink.

    Nothing consumes it, which is exactly why it needs a test: an instalment
    that converts a widget without striking its family off leaves the
    remaining work overstated, and the next one understates it. The three
    entries came off as their instalments landed -- the SCORE panel's
    board-level and team-card inputs first, the player card second, the
    EVENT tab third -- and this is the assertion each of them had to flip.

    It is empty now, and the emptiness is the assertion: a widget that
    reaches for `objectName()`-derived keys again either gets converted or
    gets listed here, and listing it fails this test on purpose.
    """
    described = " ".join(
        f"{family} {owner}" for family, owner in schema.DYNAMIC_KEY_FAMILIES)
    assert "AutonaScoreboardWidget" not in described, (
        "the SCORE panel's team card and match bar are static keys now")
    assert "AutonaScoreboardPlayerWidget" not in described, (
        "the player card's inputs are StateSchema.PLAYER_INPUT_KEYS now")
    assert "AutonaTournamentInfoWidget" not in described, (
        "the EVENT tab's inputs are StateSchema.TOURNAMENT_INPUT_KEYS now")
    assert schema.DYNAMIC_KEY_FAMILIES == (), (
        f"a key family is being derived from Qt objectNames again: "
        f"{schema.DYNAMIC_KEY_FAMILIES}. Name it in StateSchema and have the "
        "widget look its inputs up by that name (S-14), the way the three "
        "Step 5 instalments did.")


def test_the_tournament_field_names_stay_inside_the_catalog():
    """Same contract for the EVENT tab (headless core Step 5, instalment 3).

    TOURNAMENT_INPUT_KEYS is what AutonaTournamentInfoWidget finds its
    inputs by and what TournamentInfoService accepts; TOURNAMENT_INFO_KEYS is
    what KEY_CATALOG enumerates. The catalog used to list only five of these
    keys and describe the rest in a comment, which is how an enumeration
    nobody consumes drifts.
    """
    assert set(schema.TOURNAMENT_INPUT_KEYS) <= set(
        schema.TOURNAMENT_INFO_KEYS)
    assert schema.KEY_CATALOG["tournamentInfo"] == schema.TOURNAMENT_INFO_KEYS
    # Spelled out: an existing program_state.json is keyed by these.
    assert schema.TOURNAMENT_TEXT_FIELDS == (
        "tournamentName", "eventName", "address")
    assert schema.TOURNAMENT_NUMBER_FIELDS == ("numEntrants",)
    assert schema.TOURNAMENT_DATE_FIELDS == ("startAt",)
    assert schema.TOURNAMENT_SHORT_DATE_KEYS == (
        "endAt", "eventStartAt", "eventEndAt")
    assert schema.TOURNAMENT_DISPLAY_NAME_KEY == "displayTournamentName"


def test_the_tournament_service_field_lists_are_the_schema_ones():
    """One spelling, not two.

    TournamentInfoService named these itself in Step 4b, before the widget
    side existed; the panel's findChild keys have to be the same strings, so
    the service aliases the schema tuples rather than repeating them. A copy
    that drifts would leave the panel wiring a field the service rejects.
    """
    from src.core.TournamentInfoService import TournamentInfoService

    assert TournamentInfoService.TEXT_FIELDS is schema.TOURNAMENT_TEXT_FIELDS
    assert TournamentInfoService.NUMBER_FIELDS is (
        schema.TOURNAMENT_NUMBER_FIELDS)
    assert TournamentInfoService.DATE_FIELDS is schema.TOURNAMENT_DATE_FIELDS
    assert TournamentInfoService.SHORT_DATE_KEYS is (
        schema.TOURNAMENT_SHORT_DATE_KEYS)


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
    notice, because they all call the seed directly. SeedColorState() is
    core's too since Step 5-5 (core/ColorService.py) but is kept a separate
    call -- that module reads StateSchema for its defaults and its key
    spellings, so the schema cannot call back into it -- and it has to run in
    the same block to stay one save cycle.
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


# ── Subtree lifecycle ops (headless core Step 5-5) ───────────────────────
#
# Two Unsets that used to sit in a QDockWidget's body. They are here for the
# same reason SeedBoard is: `score` and `player_editor` are schema names, and
# the completion condition of Step 5 is that no Qt widget spells a state key.


def test_reset_score_root_drops_the_whole_subtree():
    """What AutonaScoreboardManager.__init__ runs before any board is built.

    A program_state.json from an older multi-scoreboard build can carry
    boards no widget owns; dropping the root is how a launch starts from the
    single-scoreboard layout. Destructive by design -- nothing may call it
    once a board exists.
    """
    schema.SeedBoard(1)
    SM.Set("score.1.team.1.teamName", "Alpha")
    SM.Set("score.bracket_type", "DOUBLE_ELIMINATION")

    schema.ResetScoreRoot()

    assert SM.Get(schema.SCORE_ROOT, None) in (None, {})
    assert SM.Get("score.1.team.1.teamName", None) is None


def test_clear_player_editor_drops_the_scratch_subtree():
    """What the PLAYERS editor dialog runs on close.

    Same root the launch seed purges (RETIRED_ROOTS), for the case where a
    crash meant the dialog never closed.
    """
    SM.Set(f"{schema.PLAYER_EDITOR_ROOT}.1.name", "Ada")

    schema.ClearPlayerEditor()

    assert SM.Get(schema.PLAYER_EDITOR_ROOT, None) in (None, {})
    assert schema.PLAYER_EDITOR_ROOT in schema.RETIRED_ROOTS


# ── Subtree roots ────────────────────────────────────────────────────────


def test_every_root_constant_is_a_catalog_key():
    """The roots and the catalog cannot disagree.

    Step 5-5 gave the top-level names constants because each of them is
    written from outside this module now (a core service or AutonaActions),
    and a root spelled at its write site drifts exactly the way a leaf does.
    KEY_CATALOG is keyed by the constants, so this checks the spellings as
    well as the membership.
    """
    roots = {
        schema.SCORE_ROOT: "score",
        schema.GAME_ROOT: "game",
        schema.TOP8_ROOT: "top8",
        schema.COMMENTARY_ROOT: "commentary",
        schema.TOURNAMENT_INFO_ROOT: "tournamentInfo",
        schema.STREAM_ROOT: "stream",
        schema.COLORS_ROOT: "colors",
        schema.DISPLAY_OPTIONS_ROOT: "display_options",
        schema.LAYOUT_OPTIONS_ROOT: "layout_options",
        schema.RESULT_PLAYERS_ROOT: "result_players",
        schema.PROVIDER_VIDEOGAME_ROOT: "provider_videogame",
    }
    for constant, expected in roots.items():
        # Literal, because these are the names an existing
        # out/program_state.json is keyed by and there is no migration layer.
        assert constant == expected
        assert constant in schema.KEY_CATALOG


def test_the_color_key_names_are_shared_by_both_families():
    """`themeColor` / `accentColor` name a tournament colour *and* an override.

    One pair of constants for both, so the catalog entry and
    core/ColorService cannot end up spelling them differently.
    """
    assert schema.COLORS_FEATURE_KEYS == (
        schema.THEME_COLOR_KEY, schema.ACCENT_COLOR_KEY)
    assert schema.THEME_COLOR_KEY in schema.TOURNAMENT_INFO_KEYS
    assert schema.ACCENT_COLOR_KEY in schema.TOURNAMENT_INFO_KEYS
    assert (schema.THEME_COLOR_KEY, schema.ACCENT_COLOR_KEY) == (
        "themeColor", "accentColor")


def test_the_top8_service_paths_come_from_the_schema():
    """The TOP 8 panel's six hand-spelled key paths, as three constants."""
    from src.core import Top8Service

    assert Top8Service.PLAYERS_PATH == (
        f"{schema.TOP8_ROOT}.{schema.TOP8_PLAYERS_KEY}")
    assert Top8Service.MATCHES_PATH == (
        f"{schema.TOP8_ROOT}.{schema.TOP8_MATCHES_KEY}")
    assert Top8Service.PHASE_LABEL_PATH == (
        f"{schema.TOP8_ROOT}.{schema.TOP8_PHASE_LABEL_KEY}")
    assert schema.TOP8_PHASE_LABEL_KEY in schema.TOP8_KEYS
    catalogued = {key.split(".", 1)[0] for key in schema.TOP8_KEYS}
    assert {schema.TOP8_PLAYERS_KEY, schema.TOP8_MATCHES_KEY} <= catalogued


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


# ── PublishDisplayOptions ────────────────────────────────────────────────


def test_publish_display_options_rewrites_every_key_from_settings():
    """The board-build pass, as opposed to SeedPanelState's missing-keys one.

    This is the loop AutonaScoreboardWidget.__init__ used to spell out over
    its own copy of the option list (headless core Step 5). It rewrites all
    eight, which is the point: SettingsManager is where the toggles persist,
    so building a board is what brings a program_state.json restored from an
    older session back in line with them.
    """
    previous = SettingsManager.settings
    SettingsManager.settings = {"display_options": {"show_seed": False}}
    try:
        SM.Unset("display_options")
        # A value a previous session left in the state, with nothing in
        # settings backing it.
        SM.Set("display_options.show_name", False)

        schema.PublishDisplayOptions()

        assert SM.Get("display_options.show_seed") is False   # from settings
        assert SM.Get("display_options.show_name") is True    # back to default
        assert set(SM.Get("display_options")) == set(
            schema.DISPLAY_OPTION_KEYS)
    finally:
        SettingsManager.settings = previous


def test_publish_display_options_does_not_write_settings(_blank_settings):
    # One direction only. The toggle (AutonaActions.SetDisplayOption) writes
    # both halves of the mirror; this republishes one of them, and writing
    # back would turn a factory default into a persisted operator choice.
    SM.Unset("display_options")
    schema.PublishDisplayOptions()
    assert SettingsManager.settings == {}


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
