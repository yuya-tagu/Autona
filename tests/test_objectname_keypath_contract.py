"""Contract test for Qt objectName -> StateManager keypath stability (S-14).

A number of Qt ``objectName`` values are used *verbatim* as StateManager
keypath segments (e.g. ``score.1.team.1.teamName``) or as ``findChild`` lookup
keys. Renaming or removing one silently breaks backward compatibility with
existing user ``program_state.json`` files -- and there is **no** StateManager
migration layer (``STATE_VERSION`` only covers the Qt dock layout, not the
state schema).

This test freezes that contract as a snapshot. If it fails, an objectName was
renamed/removed: add a StateManager migration (or restore the name) BEFORE
updating the snapshot below.

Headless core Step 5 converted the *derivation* widget by widget, and is
done: for the SCORE panel, the player card and the EVENT tab alike, the key
names are ``src/core/StateSchema.py`` constants now and each widget looks its
inputs up by them, instead of reading them back off whatever it found. That
does not retire this file -- the .ui names and the schema names still have to
be the same strings, or an existing ``program_state.json`` stops matching and
``findChild`` stops resolving -- so the sections below pin exactly that
agreement, plus the absence of the old derivation.

Beyond that agreement the test stays runtime-free: it parses the ``.ui`` XML
and reads source text. The one import it does make (inside the test, not at
collection) is ``src.core.StateSchema``, which is Qt-free and side-effect-free
by construction -- ``tests/test_core_qt_free.py`` and
``tests/test_import_side_effects.py`` pin both. ``src`` itself is still never
imported wholesale (agent-pitfalls #1). See ``.claude/known-smells.md`` S-14
and ``.claude/rules/python-widgets.md``.
"""
import ast
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAYOUT = PROJECT_ROOT / "src" / "layout"

# Widget classes whose objectName is read back into a StateManager keypath
# (the code iterates them via findChildren / findChild).
INPUT_WIDGET_CLASSES = {
    "QLineEdit", "QCheckBox", "QComboBox", "QDateEdit", "QSpinBox",
    "QPlainTextEdit", "QTextEdit", "QDoubleSpinBox", "QTimeEdit",
    "QDateTimeEdit",
}

# Frozen contract: .ui file -> the set of input-widget objectNames whose value
# is persisted under a StateManager keypath (or used as a findChild key).
# Adding a name here is safe (a new key); removing/renaming one is a
# compatibility break that needs a migration first.
EXPECTED = {
    "AutonaScoreboardTeam.ui": {"teamName", "losers"},
    "AutonaScoreboardScore.ui": {
        "phase", "pool", "match", "best_of", "score_left", "score_right",
    },
    "AutonaScoreboardPlayer.ui": {
        "team", "name", "seed", "real_name", "twitter", "birthday",
        "country", "state",
    },
    "AutonaTournamentInfo.ui": {
        "tournamentName", "eventName", "gameTitleCombo", "numEntrants",
        "startAt", "address",
        # streamTitleEdit moved to AutonaStreamSettings.ui. It only ever
        # reached the state via this tab's generic "every QLineEdit becomes
        # tournamentInfo.{objectName}" loop and nothing read the resulting
        # tournamentInfo.streamTitleEdit key, so no migration is owed; the
        # 配信設定 panel Unsets the residual key on startup.
    },
    # 配信設定 panel. Its inputs are wired explicitly (no findChildren keypath
    # loop), so these objectNames are findChild keys rather than keypaths —
    # listed to keep them under the same rename guard as the rest. Only the
    # classes in INPUT_WIDGET_CLASSES above are scanned, so the panel's
    # QRadioButtons are not covered here.
    "AutonaStreamSettings.ui": {
        "streamUrlCombo", "streamTitleEdit", "cbStreamThumbOverlayOrgIcon",
    },
}


def _object_names(ui_path):
    tree = ET.parse(ui_path)
    names = set()
    for widget in tree.iter("widget"):
        if widget.get("class") in INPUT_WIDGET_CLASSES:
            name = widget.get("name")
            # Qt auto-names a QSpinBox's internal QLineEdit; never a real key.
            if name and name != "qt_spinbox_lineedit":
                names.add(name)
    return names


def test_keypath_objectnames_unchanged():
    for filename, expected in EXPECTED.items():
        ui_path = LAYOUT / filename
        assert ui_path.exists(), f"{filename} no longer exists under src/layout/"
        actual = _object_names(ui_path)
        assert actual == expected, (
            f"\n{filename}: input-widget objectName set changed.\n"
            f"  added   (new keys, OK if intended): {sorted(actual - expected)}\n"
            f"  removed (BREAKS program_state.json compat): "
            f"{sorted(expected - actual)}\n"
            "Update EXPECTED only after confirming a StateManager migration "
            "covers any rename/removal. See known-smells S-14."
        )


# ── The scoreboard's static keypaths (headless core Step 5) ──────────────
#
# The two families AutonaScoreboardWidget used to derive at runtime --
# score.{N}.{combo.objectName()} and score.{N}.team.{T}.{widget.objectName()}
# -- are StateSchema constants now. Two things have to hold for that to be a
# migration-free change, and both are checked here.


def test_the_scoreboard_schema_names_are_the_ui_objectnames():
    """The strings on both sides of the (now explicit) binding must match.

    The schema names are what gets written to the state, so they have to stay
    equal to what an existing program_state.json is keyed by -- i.e. the .ui
    objectNames. They are *also* what findChild() is given, so a disagreement
    would additionally leave the widget unwired and the key never written.
    Frozen in EXPECTED above as well, which is what makes a rename fail
    loudly on both counts.
    """
    from src.core import StateSchema as schema

    scoreNames = _object_names(LAYOUT / "AutonaScoreboardScore.ui")
    assert set(schema.SCORE_MATCH_FIELDS) <= scoreNames, (
        "StateSchema.SCORE_MATCH_FIELDS names a widget "
        "AutonaScoreboardScore.ui does not have")
    assert set(schema.SCORE_MATCH_FIELDS) <= EXPECTED[
        "AutonaScoreboardScore.ui"]

    teamNames = _object_names(LAYOUT / "AutonaScoreboardTeam.ui")
    assert set(schema.SCORE_TEAM_INPUT_KEYS) <= teamNames, (
        "StateSchema.SCORE_TEAM_INPUT_KEYS names a widget "
        "AutonaScoreboardTeam.ui does not have")
    assert set(schema.SCORE_TEAM_INPUT_KEYS) <= EXPECTED[
        "AutonaScoreboardTeam.ui"]


def _object_name_calls(relative_path):
    """Every ``<something>.objectName()`` call in a source file."""
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "objectName"
    ]


def test_the_player_schema_names_are_the_ui_objectnames():
    """The player card's half of the same agreement (Step 5, instalment 2).

    Every operator-editable field of AutonaScoreboardPlayer.ui is a
    StateSchema constant now, used both as the state key and as the
    findChild argument. The note box is the one input built in code rather
    than in the .ui, so it is checked against PROGRAMMATIC_TOKENS below
    instead.
    """
    from src.core import StateSchema as schema

    playerNames = _object_names(LAYOUT / "AutonaScoreboardPlayer.ui")
    fromUi = set(schema.PLAYER_INPUT_KEYS) - {schema.PLAYER_NOTE_KEY}
    assert fromUi <= playerNames, (
        "StateSchema.PLAYER_INPUT_KEYS names a widget "
        f"AutonaScoreboardPlayer.ui does not have: "
        f"{sorted(fromUi - playerNames)}")
    assert fromUi <= EXPECTED["AutonaScoreboardPlayer.ui"]
    # The note box's objectName is assigned in code; both spellings have to
    # be the same string or the eye menu's "show_additional" toggle stops
    # finding the widget its key belongs to.
    assert schema.PLAYER_NOTE_KEY == "custom_textbox"


_STATE_WRITE_METHODS = ("Set", "Unset", "SetIfChanged")


def _state_write_calls(relative_path):
    """Every ``StateManager.Set`` / ``.Unset`` / ``.SetIfChanged`` call in a
    source file (``SetIfChanged`` is a write too: the guarded Set the core
    writers share).

    Matches on the receiver name being literally ``StateManager``, so an
    aliased import (``from ...StateManager import StateManager as SM``)
    would slip through undetected. If a source file ever introduces an
    alias like that, update this AST check to match it too.
    """
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _STATE_WRITE_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "StateManager"
    ]


def test_the_tournament_info_schema_names_are_the_ui_objectnames():
    """The EVENT tab's half of the same agreement (Step 5, instalment 3).

    The last family that was built from objectNames at runtime: three
    findChildren loops (QLineEdit / QSpinBox / QDateEdit) each wrote
    `tournamentInfo.{widget.objectName()}`. The names are StateSchema
    constants now, used both as the state key and as the findChild argument,
    so the two spellings have to agree or the field goes unwired *and* stops
    matching an existing program_state.json.
    """
    from src.core import StateSchema as schema

    names = _object_names(LAYOUT / "AutonaTournamentInfo.ui")
    assert set(schema.TOURNAMENT_INPUT_KEYS) <= names, (
        "StateSchema.TOURNAMENT_INPUT_KEYS names a widget "
        "AutonaTournamentInfo.ui does not have: "
        f"{sorted(set(schema.TOURNAMENT_INPUT_KEYS) - names)}")
    assert set(schema.TOURNAMENT_INPUT_KEYS) <= EXPECTED[
        "AutonaTournamentInfo.ui"]


def test_the_event_panel_no_longer_derives_keypaths_from_objectname():
    """The EVENT tab's `findChildren` + objectName() loops must not return.

    Same tripwire as the scoreboard's above, and the reason this file's
    qt_spinbox_lineedit check is gone: that guard existed only because the
    panel's QLineEdit loop swept up a QSpinBox's internal line edit and had
    to recognise it. There is no loop to sweep anything now -- numEntrants
    is reached by findChild(QSpinBox, "numEntrants") -- so the guard has
    nothing left to protect and this assertion covers strictly more.
    """
    calls = _object_name_calls("src/AutonaTournamentInfoWidget.py")
    assert calls == [], (
        "AutonaTournamentInfoWidget calls objectName() again (line(s) "
        f"{sorted(node.lineno for node in calls)}). Field names come from "
        "StateSchema.TOURNAMENT_INPUT_KEYS now and are passed *to* "
        "findChild. If the new call builds a StateManager key path, use a "
        "StateSchema constant instead of reverting this test; if it is a "
        "legitimate non-keypath use, add an allowlist here.")


PLAYER_WIDGET_SOURCES = [
    "src/AutonaScoreboardPlayerWidget.py",
    "src/AutonaBasePlayerWidget.py",
]


def test_the_player_widgets_do_not_write_the_state_directly():
    """The completion condition for this instalment, as a tripwire.

    A player widget that writes StateManager itself is, in this codebase,
    always a keypath built from `self.path` plus something -- and the
    "something" was an objectName every time. Checking for the write rather
    than for `objectName()` is what makes this readable: the widgets still
    call objectName() legitimately (Clear's field filter, SwapWith's
    widget-to-widget copy, the character combos' width rules), so an
    objectName ban would need an allowlist that grows until it means
    nothing.

    SaveBlock / BlockSaving / ReleaseSaving are deliberately not covered:
    they coalesce cycles, they do not name keys.
    """
    for relative in PLAYER_WIDGET_SOURCES:
        calls = _state_write_calls(relative)
        assert calls == [], (
            f"{relative} writes StateManager directly again (line(s) "
            f"{sorted(node.lineno for node in calls)}). The player subtree "
            "is core/PlayerService.py's -- add a method there and call it "
            "through self.player instead of reverting this test.")


def test_the_event_panel_does_not_write_the_state_directly():
    """The completion condition for the EVENT tab, as the same tripwire.

    `tournamentInfo.*` has one writer for this panel's own fields --
    core/TournamentInfoService -- from both ends now: the provider payload
    and an operator typing. A StateManager.Set here would be a second one for
    those fields, and the display-name follow rule is stateful enough that
    two writers disagreeing is a silent bug rather than a visible one.
    (One other tournamentInfo.* writer exists deliberately outside this
    panel and outside the service: core/ColorService.py's themeColor/
    accentColor -- static, non-objectName-derived keys that belong to the
    colour feature and only carry this prefix for backwards compatibility.
    It is not this panel's file, so this tripwire is unaffected. The crew
    board used to be
    a second one; its displayTournamentName write goes through
    TournamentInfoService.SetDisplayName since Step 5's crew instalment,
    which is what test_the_crew_board_does_not_write_the_state_directly
    below now keeps true.) Reads (`StateManager.Get`, which
    _SyncFieldsFromState needs) are deliberately not covered: rendering the
    state is what this panel is for.
    """
    calls = _state_write_calls("src/AutonaTournamentInfoWidget.py")
    assert calls == [], (
        "AutonaTournamentInfoWidget writes StateManager directly again "
        f"(line(s) {sorted(node.lineno for node in calls)}). "
        "tournamentInfo.* is core/TournamentInfoService.py's -- add a method "
        "there and call it instead of reverting this test.")


def test_the_scoreboard_does_not_write_the_state_directly():
    """The scoreboard's own writer tripwire, matching the player/event ones.

    score.* keypaths are StateSchema constants now (see _WireTeamColumn), but
    nothing pinned that AutonaScoreboardWidget itself never calls
    StateManager.Set/Unset with a hand-built keypath -- it measures zero
    today, same as the player and EVENT-tab widgets, so it gets the same
    ratchet instead of staying an unenforced assumption.
    """
    calls = _state_write_calls("src/AutonaScoreboardWidget.py")
    assert calls == [], (
        "AutonaScoreboardWidget writes StateManager directly (line(s) "
        f"{sorted(node.lineno for node in calls)}). score.* keypaths come "
        "from StateSchema constants -- route the write through the "
        "appropriate service instead of reverting this test.")


def test_the_crew_board_does_not_write_the_state_directly():
    """The crew board's completion condition, as the same ratchet.

    Board 3 never derived a key from an objectName -- its rows are built in
    code and carry none -- so what it had instead was thirty hand-built
    keypath literals spread across a launch seed, seven signal handlers, a
    tolerant set-data path and a full-board flush. They are
    core/CrewBattleService.py's now (plus AutonaActions for the three keys
    board 3 shares with the others, and TournamentInfoService for 表示大会名),
    and this is what stops the thirty-first from being written here.

    Reads are deliberately not covered: PostToX builds its tweet body out of
    the state, and _SyncTeamColors renders the team colour.
    """
    calls = _state_write_calls("src/AutonaCrewBattleWidget.py")
    assert calls == [], (
        "AutonaCrewBattleWidget writes StateManager directly (line(s) "
        f"{sorted(node.lineno for node in calls)}). score.3 belongs to "
        "core/CrewBattleService.py -- add a method there and call it through "
        "self.crew instead of reverting this test.")


def test_the_scoreboard_no_longer_derives_keypaths_from_objectname():
    """The `findChildren` + objectName() loops must not come back.

    That pattern is what made the .ui file the state schema: a key existed
    because a designer named a widget, no catalog could be trusted, and a
    rename was a silent data-compatibility break rather than a failing test.
    Read as a syntax tree so the docstrings that *describe* the old pattern
    do not count as the pattern.
    """
    calls = _object_name_calls("src/AutonaScoreboardWidget.py")
    assert calls == [], (
        "AutonaScoreboardWidget calls objectName() again (line(s) "
        f"{sorted(node.lineno for node in calls)}). Key path segments come "
        "from StateSchema now -- see _WireTeamColumn. If the new call builds "
        "a StateManager key path, replace it with a StateSchema constant "
        "instead of reverting this test; if it is a legitimate non-keypath "
        "use (logging, etc.), add it to an allowlist here instead of "
        "deleting the assertion.")


# objectNames assigned programmatically (not in .ui) that also feed keypaths.
PROGRAMMATIC_TOKENS = {
    # AutonaNotesWidget removed (NOTES tab deleted) — no notes keypaths remain.
    "src/AutonaScoreboardPlayerWidget.py": [
        'self.custom_textbox.setObjectName("custom_textbox")',
    ],
}


def test_programmatic_keypath_names_present():
    for rel, tokens in PROGRAMMATIC_TOKENS.items():
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, (
                f"{rel}: keypath token `{token}` is gone. A programmatic "
                "objectName/keypath changed -- add a migration first (S-14)."
            )


# There used to be a `test_qt_spinbox_fallback_present` here, guarding the
# "a QSpinBox's internal QLineEdit reports objectName 'qt_spinbox_lineedit',
# so fall back to the parent's" branch that every findChildren(QLineEdit)
# keypath loop needed. It was checked in AutonaScoreboardPlayerWidget until
# the player instalment and in AutonaTournamentInfoWidget until this one; no
# such loop exists anywhere now, so there is no fallback left to guard. The
# two `..._no_longer_derives_keypaths_from_objectname` tests above are what
# replaced it, and they fail if a loop comes back -- with or without the
# fallback.


# ── The write ledger (headless core Step 5, completion condition) ────────


def _source_files_outside_core():
    """Every src/*.py except the core package itself."""
    core = PROJECT_ROOT / "src" / "core"
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        if core in path.parents or path == core:
            continue
        yield path.relative_to(PROJECT_ROOT).as_posix()


# ADR-004 D2's completion condition, as a ledger rather than a count: no Qt
# widget writes StateManager, and the writers that remain outside core are
# named, with a reason, in docs/changes/headless-core-redesign.md ("Step 5
# 完了時点の書き手台帳").
#
# The remaining entry is a provider-layer singleton, not a widget: it
# publishes what it just loaded. The key family is not operator input, so
# there is no operation to route through Actions. Adding a file here is a
# design decision, not a bookkeeping one. (`game.*` left this ledger in
# ADR-005 S1a: src/core/GameAssetCatalog.py publishes it now, and the Qt
# AutonaGameAssetManager only builds models.)
STATE_WRITERS_OUTSIDE_CORE = {
    # `provider_videogame` -- the Start.gg videogame id, mirrored from the
    # provider so overlays can tell which game a set belongs to.
    "src/AutonaTournamentDataProvider.py",
}


def test_the_state_writer_ledger_outside_core_is_the_documented_one():
    """Pins the exact set of non-core files that still call StateManager.

    Detection limit: `_state_write_calls` (like `_no_qt_widget_writes...`
    above) recognizes `StateManager.Set`/`Unset` by matching the receiver
    name literally against the string "StateManager". An import under a
    different local name (`from src.core.StateManager import StateManager
    as SM`, then `SM.Set(...)`) would not be flagged and would slip through
    this ledger undetected -- this is the shared assumption behind every
    `_state_write_calls` call in this file, not just this one.
    """
    actual = {
        relative for relative in _source_files_outside_core()
        if _state_write_calls(relative)
    }
    assert actual == STATE_WRITERS_OUTSIDE_CORE, (
        "\nThe set of StateManager writers outside src/core changed.\n"
        f"  added   (a widget writing the state again?): "
        f"{sorted(actual - STATE_WRITERS_OUTSIDE_CORE)}\n"
        f"  removed (migrated -- update the ledger): "
        f"{sorted(STATE_WRITERS_OUTSIDE_CORE - actual)}\n"
        "Writes belong to a core service or AutonaActions. If a new writer "
        "is genuinely provider-layer, add it here *and* to the ledger in "
        "docs/changes/headless-core-redesign.md.")


def test_no_qt_widget_writes_the_state():
    """The same condition stated the way ADR-004 D2 states it.

    Redundant with the ledger above by construction today, and deliberately
    so: the ledger is a list someone can extend, while this says the thing
    the decision actually promised -- that a class deriving from a Qt widget
    never writes StateManager. A future provider-layer writer may join the
    ledger; a QDockWidget may not.

    Detection limit: the base-class check only matches a class whose *own*
    base name starts with "Q" (e.g. `class Foo(QWidget)`). A class that
    inherits from a non-Qt intermediate that itself derives from a Qt
    widget (e.g. `AutonaBasePlayerWidget` subclasses) is not walked back to
    its Qt ancestor and slips through this test undetected. That gap is
    covered by the other layer instead: the ledger test above pins the
    *complete* set of writer files outside core, so an indirect-subclass
    offender still fails there even if it passes here.
    """
    offenders = {}
    for relative in _source_files_outside_core():
        if not _state_write_calls(relative):
            continue
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(base).rsplit(".", 1)[-1]
                     for base in node.bases}
            if not any(base.startswith("Q") and base not in ("QObject",)
                       for base in bases):
                continue
            if any(isinstance(inner, ast.Call)
                   and isinstance(inner.func, ast.Attribute)
                   and inner.func.attr in _STATE_WRITE_METHODS
                   and isinstance(inner.func.value, ast.Name)
                   and inner.func.value.id == "StateManager"
                   for inner in ast.walk(node)):
                offenders.setdefault(relative, []).append(node.name)
    assert offenders == {}, (
        f"Qt widget classes writing StateManager directly: {offenders}. "
        "Route the write through a core service or AutonaActions "
        "(ADR-004 D2).")
