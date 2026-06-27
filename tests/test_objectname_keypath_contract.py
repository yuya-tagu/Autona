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

The test is deliberately runtime-free: it only parses the ``.ui`` XML and reads
source text, so it never imports the ``src`` package (see
``tests/conftest.py`` and agent-pitfalls #1). See ``.claude/known-smells.md``
S-14 and ``.claude/rules/python-widgets.md``.
"""
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
    "AutonaBattleTeam.ui": {"teamName", "separateSponsors"},
    "AutonaScoreboardScore.ui": {
        "phase", "match", "best_of", "score_left", "score_right",
    },
    "AutonaScoreboardPlayer.ui": {
        "team", "name", "seed", "real_name", "twitter", "birthday",
        "country", "state",
    },
    "AutonaTeamPlayer.ui": {
        "team", "name", "country", "state", "twitter", "activePlayer",
        "dead", "dynamicSpinner",
    },
    "AutonaTournamentInfo.ui": {
        "tournamentName", "eventName", "gameTitleCombo", "numEntrants",
        "startAt", "address", "streamTitleEdit",
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


# objectNames assigned programmatically (not in .ui) that also feed keypaths.
PROGRAMMATIC_TOKENS = {
    "src/AutonaNotesWidget.py": [
        'setObjectName(f"notes_widget_{index+1}")',
        'f"notes.{widget.objectName()}.title"',
        'f"notes.{widget.objectName()}.contents"',
    ],
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
    "src/AutonaTeamPlayerWidget.py",
    "src/AutonaTournamentInfoWidget.py",
]


def test_qt_spinbox_fallback_present():
    for rel in QT_SPINBOX_FALLBACK_FILES:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "qt_spinbox_lineedit" in text, (
            f"{rel}: the qt_spinbox_lineedit -> parent objectName fallback is "
            "gone; QSpinBox values would persist under the wrong key (S-14)."
        )
