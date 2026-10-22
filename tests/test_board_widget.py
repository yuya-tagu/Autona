"""The BOARD tab's design pulldown (AutonaBoardWidget) over the layout catalog.

What is pinned here is the operator's choice surviving a re-scan that cannot
see it. The Board tab re-reads the catalog whenever a design folder changes,
and a Designer project drops out of a scan while its project.json is locked
mid-save or briefly broken. The pulldown may show the default design for that
moment, but ``board.selected_design`` must stay what the operator picked, and
the next scan that lists the design again must put it back. (It used to save
the fallback, so one bad scan silently switched the operator to another
design for good.)

The widget is the production one, built inside the bootstrapped harness
(conftest ``autonaApp``) against a throwaway layout tree, and destroyed
explicitly afterwards like the other panel tests.
"""
import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt

from src.core import LayoutCatalog
from src.core.SettingsManager import SettingsManager
from tests.test_layout_catalog import builtin, project

SAVED_KEY = "board.selected_design"
DEFAULT = "scoreboard_crewbattle"   # AutonaBoardWidget.DEFAULT_DESIGN
FINALS = "custom/finals"


@pytest.fixture
def layoutRoot(tmp_path, monkeypatch):
    root = tmp_path / "layout"
    root.mkdir()
    builtin(root, DEFAULT, {"kind": "scoreboard",
                            "displayName": "Crew Battle"})
    builtin(root, "scoreboard_simple", {
        "kind": "scoreboard", "displayName": "Simple",
        "format": "1v1 / チーム", "game": "汎用（全ゲーム）"})
    project(root, "finals", {"name": "決勝", "meta": {"format": "2v2"}})
    # The catalog resolves its default root on every call.
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))
    return root


@pytest.fixture
def settingsWrites(autonaApp, monkeypatch):
    """Hand SettingsManager back untouched, and record what was written."""
    saved = SettingsManager.settings
    SettingsManager.settings = {}
    writes = []
    realSet = SettingsManager.Set

    def spy(key, value):
        writes.append((key, value))
        return realSet(key, value)

    monkeypatch.setattr(SettingsManager, "Set", spy)
    try:
        yield writes
    finally:
        SettingsManager.settings = saved


@pytest.fixture
def makeBoard(autonaApp, layoutRoot, settingsWrites):
    from src.AutonaBoardWidget import AutonaBoardWidget

    built = []

    def _make(selected):
        SettingsManager.settings = {"board": {"selected_design": selected}}
        widget = AutonaBoardWidget()
        built.append(widget)
        autonaApp.settle()
        return widget

    try:
        yield _make
    finally:
        for widget in built:
            widget.setParent(None)
            widget.deleteLater()
            QCoreApplication.sendPostedEvents(
                widget, QEvent.Type.DeferredDelete)
        autonaApp.settle()


def _ids(board):
    combo = board.designCombo
    return [combo.itemData(i) for i in range(combo.count())]


def _breakProject(layoutRoot):
    """project.json as a scan sees it mid-save; returns the restorer."""
    path = layoutRoot / "custom" / "finals" / "project.json"
    good = path.read_text(encoding="utf-8")
    path.write_text("{half written", encoding="utf-8")
    return lambda: path.write_text(good, encoding="utf-8")


def test_a_design_missing_from_one_scan_keeps_the_saved_choice(
        makeBoard, layoutRoot, settingsWrites):
    board = makeBoard(FINALS)
    assert board.designCombo.currentData() == FINALS
    assert board.designCombo.currentText() == "Custom: 決勝"

    restore = _breakProject(layoutRoot)
    board._refreshDesigns()
    # Shown: the default, since the project is not listed this time...
    assert FINALS not in _ids(board)
    assert board.designCombo.currentData() == DEFAULT
    # ...saved: still the operator's choice.
    assert SettingsManager.Get(SAVED_KEY) == FINALS

    restore()
    board._refreshDesigns()
    assert board.designCombo.currentData() == FINALS
    assert board.designMetaLabel.text() == "対戦形式：2v2\nゲーム：カスタム"
    assert SettingsManager.Get(SAVED_KEY) == FINALS
    # Neither refresh wrote the setting at all.
    assert settingsWrites == []


def test_a_saved_design_missing_at_launch_comes_back_once_listed(
        makeBoard, layoutRoot, settingsWrites):
    restore = _breakProject(layoutRoot)
    board = makeBoard(FINALS)
    assert board.designCombo.currentData() == DEFAULT
    assert SettingsManager.Get(SAVED_KEY) == FINALS

    restore()
    board._refreshDesigns()
    assert board.designCombo.currentData() == FINALS
    assert settingsWrites == []


def test_the_operators_own_pick_is_what_gets_saved(
        makeBoard, layoutRoot, settingsWrites):
    board = makeBoard(FINALS)
    restore = _breakProject(layoutRoot)
    board._refreshDesigns()

    # Picking while the fallback is shown replaces the choice...
    board.designCombo.setCurrentIndex(
        board.designCombo.findData("scoreboard_simple"))
    assert settingsWrites == [(SAVED_KEY, "scoreboard_simple")]

    # ...so the design coming back does not take the pulldown over.
    restore()
    board._refreshDesigns()
    assert FINALS in _ids(board)
    assert board.designCombo.currentData() == "scoreboard_simple"
    assert SettingsManager.Get(SAVED_KEY) == "scoreboard_simple"


def test_choosing_the_fallback_that_is_shown_saves_it(
        makeBoard, layoutRoot, settingsWrites):
    # Saved design missing at launch: the default is shown. Choosing that
    # same item changes no index, so only `activated` reports the pick.
    restore = _breakProject(layoutRoot)
    board = makeBoard(FINALS)
    combo = board.designCombo
    assert combo.currentData() == DEFAULT

    combo.activated.emit(combo.currentIndex())
    assert settingsWrites == [(SAVED_KEY, DEFAULT)]

    # It is the choice now: the old design coming back does not take over.
    restore()
    board._refreshDesigns()
    assert combo.currentData() == DEFAULT

    # An ordinary pick is still saved once (Qt emits currentIndexChanged,
    # then activated).
    combo.setCurrentIndex(combo.findData("scoreboard_simple"))
    combo.activated.emit(combo.currentIndex())
    assert settingsWrites == [(SAVED_KEY, DEFAULT),
                              (SAVED_KEY, "scoreboard_simple")]


def test_the_meta_line_is_plain_text(makeBoard):
    # format / game are free text from settings.json / project.json.
    board = makeBoard(FINALS)
    assert board.designMetaLabel.textFormat() == Qt.TextFormat.PlainText
