"""Contract test for Qt objectName -> StateManager keypath stability (S-14).

A number of Qt ``objectName`` values are used *verbatim* as StateManager
keypath segments (e.g. ``score.1.team.1.teamName``) or as ``findChild`` lookup
keys. Renaming or removing one silently breaks backward compatibility with
existing user ``program_state.json`` files -- and there is **no** StateManager
migration layer (``STATE_VERSION`` only covers the Qt dock layout, not the
state schema).

This test froze that contract as a snapshot of the ``.ui`` files. If it
fails, a key was renamed/removed: add a StateMigrations step (or restore the
name) BEFORE updating the snapshot below.

Headless core Step 5 converted the *derivation* widget by widget, and is
done: for the SCORE panel, the player card and the EVENT tab alike, the key
names are ``src/core/StateSchema.py`` constants now and each widget looked
its inputs up by them, instead of reading them back off whatever it found.
The widgets themselves are gone -- the EVENT tab in ADR-005 S4g, the SCORE
board, its player cards and their ``.ui`` files with the Qt SCORE in S4ac-3;
the React pages write the same keys through /api/v1 -- so there is no
``.ui`` left to agree with. What stays is the snapshot: the names an existing
``state.json`` / ``program_state.json`` is keyed by, which the schema
constants must still spell (below), and the ledger of who may write the
state.

The test stays runtime-free: it reads source text. The one import it does
make (inside the tests, not at collection) is ``src.core.StateSchema``, which
is Qt-free and side-effect-free by construction --
``tests/test_core_qt_free.py`` and ``tests/test_import_side_effects.py`` pin
both. ``src`` itself is still never imported wholesale (agent-pitfalls #1).
See ``.claude/known-smells.md`` S-14 and ``.claude/rules/python-widgets.md``.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Frozen contract: the .ui file the names came from -> the input-widget
# objectNames whose value was persisted under a StateManager keypath (or used
# as a findChild key). The files went with the Qt SCORE in ADR-005 S4ac-3;
# the snapshot stays, frozen as the files last had it, because the persisted
# keys are still these strings. Removing/renaming one is a compatibility
# break that needs a StateMigrations step first.
#
# Not every name is a key: score_left / score_right were the score
# spinboxes' names (the key is team.{T}.score), and best_of is the key the
# best-of spinbox wrote.
EXPECTED = {
    "AutonaScoreboardTeam.ui": {"teamName", "losers"},
    "AutonaScoreboardScore.ui": {
        "phase", "pool", "match", "best_of", "score_left", "score_right",
    },
    "AutonaScoreboardPlayer.ui": {
        "team", "name", "seed", "real_name", "twitter", "birthday",
        "country", "state",
    },
    # The EVENT tab's .ui (AutonaTournamentInfo.ui: tournamentName,
    # eventName, gameTitleCombo, numEntrants, startAt, address) went with
    # the Qt panel in ADR-005 S4g. The React Event page writes the same
    # state keys (tournamentInfo.*) through /api/v1's PATCH
    # /tournament/info, whose field names are StateSchema.TOURNAMENT_INPUT_
    # KEYS (startAt is written from the raw day, startAtDate); nothing about
    # the saved state changed.
    # The 配信設定 panel's .ui (AutonaStreamSettings.ui, three names here)
    # went with the Qt panel in ADR-005 S4e: its inputs were findChild keys,
    # not keypaths, and the React page writes the same state keys
    # (stream.url / stream.title) through /api/v1.
}


# ── The scoreboard's static keypaths (headless core Step 5) ──────────────
#
# The two families AutonaScoreboardWidget used to derive at runtime --
# score.{N}.{combo.objectName()} and score.{N}.team.{T}.{widget.objectName()}
# -- are StateSchema constants now, and must still spell the frozen names.


def test_the_scoreboard_schema_names_are_the_frozen_objectnames():
    """The schema names are what gets written to the state, so they have to
    stay equal to what an existing state.json is keyed by -- the frozen
    objectNames. The .ui side of this agreement went with the Qt SCORE in
    ADR-005 S4ac-3; the snapshot is what a rename fails against now.
    """
    from src.core import StateSchema as schema

    assert set(schema.SCORE_MATCH_FIELDS) == {"phase", "pool", "match"}
    assert set(schema.SCORE_MATCH_FIELDS) <= EXPECTED[
        "AutonaScoreboardScore.ui"]

    assert set(schema.SCORE_TEAM_INPUT_KEYS) == EXPECTED[
        "AutonaScoreboardTeam.ui"]


def test_the_player_schema_names_are_the_frozen_objectnames():
    """The player card's half of the same agreement (Step 5, instalment 2).

    Every operator-editable field of the card is a StateSchema constant,
    the state key it wrote: the .ui's fields, frozen above, and the note box,
    the one input the card built in code (``custom_textbox``; the eye menu's
    "show_additional" toggle found it by that name). The card and its .ui
    went with the Qt SCORE in ADR-005 S4ac-3.
    """
    from src.core import StateSchema as schema

    fromUi = set(schema.PLAYER_INPUT_KEYS) - {schema.PLAYER_NOTE_KEY}
    assert fromUi == EXPECTED["AutonaScoreboardPlayer.ui"], (
        "StateSchema.PLAYER_INPUT_KEYS no longer spells the frozen names: "
        f"added {sorted(fromUi - EXPECTED['AutonaScoreboardPlayer.ui'])}, "
        f"removed {sorted(EXPECTED['AutonaScoreboardPlayer.ui'] - fromUi)}")
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


# The EVENT tab's half of the agreement (its schema names were the .ui
# objectNames, and its widget derived no keypath from objectName()) went
# with the Qt panel in ADR-005 S4g; tests/test_web_panel_host.py's
# test_the_qt_event_panel_is_gone keeps it from coming back, and
# tests/test_api_v1_tournament.py pins the field names the React page
# sends.
#
# The SCORE board's, its player cards' and the crew board's own tripwires
# (no direct state write, no objectName() keypath, the note box's
# programmatic name) went with them in ADR-005 S4ac-3;
# tests/test_web_panel_host.py's
# test_the_qt_scoreboard_panel_and_its_switch_are_gone keeps them from
# coming back, and the ledger below keeps any Qt widget from writing the
# state.


# The EVENT tab's own writer tripwire went with the Qt panel (ADR-005
# S4g): the React page writes tournamentInfo.* through /api/v1, which calls
# core/TournamentInfoService (and core/ColorService for the two colour
# keys). The ledger below still keeps any Qt widget from writing it.


# There used to be a `test_qt_spinbox_fallback_present` here, guarding the
# "a QSpinBox's internal QLineEdit reports objectName 'qt_spinbox_lineedit',
# so fall back to the parent's" branch that every findChildren(QLineEdit)
# keypath loop needed; then a `..._no_longer_derives_keypaths_from_objectname`
# test on the SCORE board, which replaced it. No such loop exists anywhere,
# and the board went with the Qt SCORE in ADR-005 S4ac-3 (the EVENT tab's
# twin with the Qt panel, S4g).


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
    widget (as the player card did, through `AutonaBasePlayerWidget`, until
    ADR-005 S4ac-3) is not walked back to its Qt ancestor and slips through
    this test undetected. That gap is
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
