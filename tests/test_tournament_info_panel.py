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
import time

import pytest

from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import QDateEdit, QLineEdit, QSpinBox

from src.core.TournamentInfoService import TournamentInfoService
from src.core.StateManager import StateManager


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
    """Hand tournamentInfo.* back untouched -- it is process-global.

    The service's two ledgers go with it: `_rawDates` is what a date field
    renders from (so a test that picks a date would otherwise decide what a
    later one sees in its QDateEdit) and `_lastTournamentName` is the whole
    表示大会名 follow rule.
    """
    from src.core.DictHelper import deep_clone

    saved = deep_clone(StateManager.Get("tournamentInfo") or {})
    service = TournamentInfoService.instance
    rawDates = dict(service._rawDates)
    lastName = service._lastTournamentName
    try:
        yield
    finally:
        StateManager.Set("tournamentInfo", saved)
        service._rawDates = rawDates
        service._lastTournamentName = lastName
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


# ── the inputs, once they stopped naming their own keys (Step 5) ─────────
#
# Each field is wired by a StateSchema name that is also its findChild key,
# and hands the value to TournamentInfoService. What these check is the pair
# of things a source-level test cannot: that the wiring reaches the *right*
# key, and that it reaches it exactly once.

@pytest.mark.parametrize("field, typed", [
    ("tournamentName", "Autona Cup"),
    ("eventName", "Singles"),
    ("address", "Tokyo"),
])
def test_each_text_field_writes_its_own_key_once(
        autonaApp, panel, field, typed):
    edit = _field(panel, field)
    with _stateWrites() as written:
        edit.setText(typed)
        edit.editingFinished.emit()
        autonaApp.settle()

    assert StateManager.Get(f"tournamentInfo.{field}") == typed
    assert written.count(f"tournamentInfo.{field}") == 1, (
        f"{field} was written {written.count(f'tournamentInfo.{field}')} "
        f"times: {written}")


def test_the_entrant_count_writes_its_own_key_once(autonaApp, panel):
    spin = _field(panel, "numEntrants", QSpinBox)
    with _stateWrites() as written:
        spin.setValue(96)
        autonaApp.settle()

    assert StateManager.Get("tournamentInfo.numEntrants") == 96
    assert written.count("tournamentInfo.numEntrants") == 1, written


def test_the_date_field_writes_its_own_key_once(autonaApp, panel):
    from qtpy.QtCore import QDate

    widget = _field(panel, "startAt", QDateEdit)
    with _stateWrites() as written:
        widget.setDate(QDate(2026, 3, 14))
        autonaApp.settle()

    assert written.count("tournamentInfo.startAt") == 1, written
    # The published form is the formatted short date, and the raw timestamp
    # behind it is the one the operator picked.
    raw = TournamentInfoService.instance.RawDate("startAt")
    assert time.strftime("%Y-%m-%d", time.localtime(raw)) == "2026-03-14"


def test_leaving_a_field_untouched_writes_nothing(autonaApp, panel):
    """editingFinished fires on focus-out whether or not anything was typed.

    The old lambda wrote the key unconditionally; the service applies
    _SetIfChanged like every other tournamentInfo writer, so tabbing through
    the form no longer queues changed keys. tournamentInfo.* is not held, so
    this was never a 反映 problem -- it was a save cycle per field.
    """
    edit = _field(panel, "eventName")
    edit.setText("Singles")
    edit.editingFinished.emit()
    autonaApp.settle()

    with _stateWrites() as written:
        edit.editingFinished.emit()
        autonaApp.settle()

    assert written == []


def test_a_picked_date_survives_the_next_state_change(autonaApp, panel):
    """The regression the raw-date ledger used to have.

    The QDateEdit is restored from TournamentInfoService.RawDate(), not from
    the state (which holds a formatted date). While a manual edit wrote the
    key directly, that ledger still held the *loaded* tournament's date, so
    the next tournamentInfo delta -- any of them -- re-rendered the panel and
    put the loaded date back over what the operator had picked.
    """
    from qtpy.QtCore import QDate

    TournamentInfoService.instance.ApplyTournamentData({
        "startAt": 1735689600, "initial_load": True})
    autonaApp.settle()

    widget = _field(panel, "startAt", QDateEdit)
    widget.setDate(QDate(2026, 3, 14))
    autonaApp.settle()

    # Any other tournamentInfo write re-renders the whole tab.
    TournamentInfoService.instance.SetDisplayName("SPECIAL EDITION")
    autonaApp.settle()

    assert widget.date().toString("yyyy-MM-dd") == "2026-03-14"


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
    # Through the wiring the headless shell makes too (ADR-005 S3c): the
    # window hands it the failure dialog, and the wiring connects the
    # payload writer.
    assert ("wire_tournament_data(on_failure=self."
            "_ReportTournamentLoadFailure)") in _windowTokens(), (
        "Window no longer routes tournament_data_updated into "
        "TournamentInfoService; tournamentInfo.* would stop being written "
        "whenever the EVENT page is not built")
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    wiring = "".join(
        (root / "src" / "bootstrap.py").read_text(encoding="utf-8").split())
    assert "signals.tournament_data_updated.connect(apply)" in wiring
    assert "TournamentInfoService.instance.ApplyProviderPayload(data)" in wiring


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
    # Installs and scans a real game; the autonaApp teardown removes it.
    autonaApp.loadCharacters()

    panel = makePanel()
    texts = [panel.gameTitleCombo.itemText(i)
             for i in range(panel.gameTitleCombo.count())]

    assert "Harness Game" in texts


@pytest.mark.parametrize("executorUp", [True, False],
                         ids=["fetched", "the app is quitting"])
def test_the_media_download_gives_the_button_back_and_clears_its_status(
        autonaApp, panel, monkeypatch, executorUp):
    """ADR-005 S3f-2: the icon / banner download is a job of the task
    executor whose answer reaches the panel through the GUI executor. It used
    to come back through a Worker's signals, which the garbage collector
    could take first: the button then stayed disabled and the footer status
    stayed up. Nothing of the call is kept here, and a collection runs while
    the job is in flight. When the executor has shut down nothing runs and
    the button is given back at once."""
    import gc
    import threading
    from types import SimpleNamespace

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.TaskExecutor import TaskExecutor
    from src.Helpers.AutonaStatusManager import AutonaStatusManager

    if not executorUp:
        assert TaskExecutor.instance.drain(10)
        stopped = TaskExecutor()
        stopped.shutdown(10)
        monkeypatch.setattr(TaskExecutor, "instance", stopped)

    gate = threading.Event()

    def noUrl():
        assert gate.wait(10)
        return None  # no icon / banner: nothing is requested

    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        SimpleNamespace(GetIconURL=noUrl, GetBannerURL=noUrl))
    statuses = []
    push, pop = AutonaStatusManager.push, AutonaStatusManager.pop
    monkeypatch.setattr(
        AutonaStatusManager, "push",
        lambda message, *args: statuses.append(("push", push(message, *args)))
        or statuses[-1][1])
    monkeypatch.setattr(AutonaStatusManager, "pop",
                        lambda token: (statuses.append(("pop", token)),
                                       pop(token)))

    panel._OnDownloadMedia()
    if not executorUp:
        assert panel.btDownloadMedia.isEnabled()
        assert statuses == []
        return
    assert not panel.btDownloadMedia.isEnabled()

    gc.collect()
    gate.set()
    assert TaskExecutor.instance.drain(10)
    gc.collect()
    deadline = time.monotonic() + 10
    while (not panel.btDownloadMedia.isEnabled()
           and time.monotonic() < deadline):
        QCoreApplication.processEvents()

    assert panel.btDownloadMedia.isEnabled()
    assert [kind for kind, _token in statuses] == ["push", "pop"]
    assert statuses[0][1] == statuses[1][1]
