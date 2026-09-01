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

Headless core Step 5 is converting the *derivation* widget by widget: for the
SCORE panel the key names are ``src/core/StateSchema.py`` constants now and
the widget looks its inputs up by them, instead of reading them back off
whatever it found. That does not retire this file -- the .ui names and the
schema names still have to be the same strings, or an existing
``program_state.json`` stops matching and ``findChild`` stops resolving -- so
the scoreboard section below pins exactly that agreement, plus the absence of
the old derivation.

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


# The QSpinBox internal QLineEdit reports objectName 'qt_spinbox_lineedit';
# keypath code must fall back to the parent objectName. Guard that fallback so
# spinbox values keep landing on the right key.
QT_SPINBOX_FALLBACK_FILES = [
    "src/AutonaScoreboardPlayerWidget.py",
    "src/AutonaTournamentInfoWidget.py",
]


def test_qt_spinbox_fallback_present():
    for rel in QT_SPINBOX_FALLBACK_FILES:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "qt_spinbox_lineedit" in text, (
            f"{rel}: the qt_spinbox_lineedit -> parent objectName fallback is "
            "gone; QSpinBox values would persist under the wrong key (S-14)."
        )
