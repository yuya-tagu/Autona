"""The EVENT panel after it stopped owning tournamentInfo.* (Step 4b).

The panel is lazy now, which puts two new things at risk and neither of them
is visible from the service tests:

- it may be built long *after* a tournament was loaded, so it has to render
  the state it finds rather than assume it wrote it;
- it still writes when a human edits a field, so rendering must not echo
  back into that write path.

Nothing here is stubbed -- this is the production widget, built the way
Window._CreateTournamentInfoPanel builds it. It is destroyed explicitly
afterwards: the panel connects bound-method slots to the process-wide
StateManagerBridge and AutonaGameAssetManager, and a widget left alive would
keep answering them for the rest of the session (the "ghost panel" the board
harness has the same teardown for).
"""
import contextlib

import pytest

from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import QDateEdit, QLineEdit, QSpinBox

from src.core.TournamentInfoService import TournamentInfoService
from src.StateManager import StateManager


@contextlib.contextmanager
def _stateWrites():
    """Record every StateManager.Set made inside the block.

    Counting revisions is not enough for an echo check: writing a key the
    value it already holds produces no delta at all, so the revision would
    not move and a widget echoing exactly what it was told to display would
    pass. The write itself is the thing that must not happen -- under a held
    prefix it is also what marks a subtree dirty.
    """
    written = []
    original = StateManager.Set

    def spy(key, value):
        written.append(key)
        return original(key, value)

    StateManager.Set = spy
    try:
        yield written
    finally:
        StateManager.Set = original


@pytest.fixture
def makePanel(autonaApp):
    """Build the panel on demand, and take it back down afterwards.

    A factory rather than a plain fixture because *when* the panel is built
    is what several of these tests are about -- a lazy page can be revealed
    long after a tournament was loaded.
    """
    from src.AutonaTournamentInfoWidget import AutonaTournamentInfoWidget

    built = []

    def _make():
        widget = AutonaTournamentInfoWidget()
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


@pytest.fixture
def panel(makePanel):
    return makePanel()


@pytest.fixture(autouse=True)
def _restoreTournamentInfo(autonaApp):
    """Hand tournamentInfo.* back untouched -- it is process-global."""
    from src.Helpers.AutonaDictHelper import deep_clone

    saved = deep_clone(StateManager.Get("tournamentInfo") or {})
    try:
        yield
    finally:
        StateManager.Set("tournamentInfo", saved)
        autonaApp.settle()


def _field(panel, name, cls=QLineEdit):
    widget = panel.findChild(cls, name)
    assert widget is not None, f"no {cls.__name__} named {name} on the panel"
    return widget


def test_the_panel_renders_a_tournament_loaded_before_it_existed(
        autonaApp, makePanel):
    """The whole point of the extraction: the writer runs without the panel.

    The tournament is applied first, with no EVENT page in existence -- the
    ordinary case now that the page is lazy -- and the panel has to come up
    showing it.
    """
    TournamentInfoService.instance.ApplyTournamentData({
        "tournamentName": "Autona Cup",
        "eventName": "Singles",
        "numEntrants": 128,
        "initial_load": True,
    })
    autonaApp.settle()

    panel = makePanel()

    assert _field(panel, "tournamentName").text() == "Autona Cup"
    assert _field(panel, "eventName").text() == "Singles"
    assert _field(panel, "numEntrants", QSpinBox).value() == 128


def test_the_panel_follows_a_later_state_change(autonaApp, panel):
    TournamentInfoService.instance.ApplyTournamentData({
        "tournamentName": "Other Cup", "initial_load": True})
    autonaApp.settle()

    assert _field(panel, "tournamentName").text() == "Other Cup"


def test_rendering_the_state_does_not_write_it_back(autonaApp, panel):
    """Echo-loop check: the sync sets widgets with their signals blocked.

    The spy is around the *whole* apply, panel reaction included, because
    that is where an echo would appear: the service writes each key once,
    and an unblocked valueChanged / dateChanged would write the very same
    key a second time (QLineEdit.setText() does not itself emit
    editingFinished -- only Return/focus-out do -- so the blocker on the
    text fields is defensive rather than closing a live echo path). Counting
    revisions cannot see that -- the echoed value is identical, so it
    produces no delta.
    """
    with _stateWrites() as written:
        TournamentInfoService.instance.ApplyTournamentData({
            "tournamentName": "Autona Cup",
            "eventName": "Singles",
            "numEntrants": 128,
            "address": "Tokyo",
            "startAt": 1735689600,
            "initial_load": True,     # no clear pass, so one write per key
        })
        autonaApp.settle()

    duplicates = sorted({key for key in written if written.count(key) > 1})
    assert duplicates == [], (
        f"{duplicates} were written more than once while the panel rendered "
        "them; the panel is writing back what it was told to display")
    # Sanity: the sync really did have something to render.
    assert "tournamentInfo.tournamentName" in written
    assert _field(panel, "tournamentName").text() == "Autona Cup"


def test_editing_the_name_still_writes_the_state(autonaApp, panel):
    edit = _field(panel, "tournamentName")
    edit.setText("Typed By Hand")
    edit.editingFinished.emit()
    autonaApp.settle()

    assert StateManager.Get("tournamentInfo.tournamentName") == "Typed By Hand"


def test_editing_the_name_carries_into_the_display_name(autonaApp, panel):
    """The weaker of the two 表示大会名 rules, still wired to this field."""
    TournamentInfoService.instance.Seed()
    autonaApp.settle()

    edit = _field(panel, "tournamentName")
    edit.setText("Autona Cup 2")
    edit.editingFinished.emit()
    autonaApp.settle()

    assert StateManager.Get(
        "tournamentInfo.displayTournamentName") == "Autona Cup 2"


def test_a_loaded_date_reaches_the_date_edit(autonaApp, panel):
    """The one field the state cannot restore -- it holds a formatted date."""
    import time

    timestamp = 1735689600
    TournamentInfoService.instance.ApplyTournamentData({
        "startAt": timestamp, "initial_load": True})
    autonaApp.settle()

    widget = _field(panel, "startAt", QDateEdit)
    assert widget.date().toString("yyyy-MM-dd") == time.strftime(
        "%Y-%m-%d", time.localtime(timestamp))


# ── the wiring that replaces the eager panel ─────────────────────────────
#
# Source-level, because Window cannot be built in this harness (importing
# src/Autona.py *is* the startup sequence -- agent-pitfalls #1). Losing any
# of these three lines would not fail a test anywhere else: the app would
# start, the EVENT page would look fine, and tournamentInfo.* would simply
# never be written for anyone who did not open it.

def _windowTokens():
    """src/Autona.py as a whitespace-free token stream.

    Collapsed so the pins below survive reformatting -- a line wrap moving
    by one argument is not the regression they are looking for.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "Autona.py").read_text(encoding="utf-8")
    return "".join(source.split())


def test_the_window_wires_the_tournament_writer():
    assert ("signals.tournament_data_updated.connect(self."
            "_OnTournamentDataUpdated)") in _windowTokens(), (
        "Window no longer routes tournament_data_updated into "
        "TournamentInfoService; tournamentInfo.* would stop being written "
        "whenever the EVENT page is not built")


def test_the_window_seeds_the_tournament_service():
    assert "TournamentInfoService.instance.Seed()" in _windowTokens(), (
        "Window._SeedPanelState no longer seeds tournamentInfo.*; the "
        "display name and the logo version would be missing at launch")


def test_the_event_page_is_lazy():
    assert "(self._CreateTournamentInfoPanel,'tournament.svg'," \
        in _windowTokens(), (
        "the EVENT page is eager again -- that is the startup cost Step 4b "
        "removed (startup-architecture-redesign.md §3)")


def test_the_game_combo_catches_up_on_the_assets_already_loaded(
        autonaApp, makePanel):
    """The combo used to be filled by a broadcast this panel now misses.

    onLoadAssets fires once, at startup, while an eager EVENT page was
    already listening. A lazy one is built long afterwards, so it has to
    populate itself -- otherwise the 大会タイトル combo comes up empty and an
    operator cannot pick a game from this tab at all.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    manager = AutonaGameAssetManager.instance
    savedGames = manager.games
    manager.games = {"harness_game": {"name": "Harness Game"}}
    try:
        panel = makePanel()
        texts = [panel.gameTitleCombo.itemText(i)
                 for i in range(panel.gameTitleCombo.count())]
    finally:
        manager.games = savedGames

    assert "Harness Game" in texts
