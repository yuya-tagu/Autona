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
import os
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

    The service's ledger goes with it: `_lastTournamentName` is the whole
    表示大会名 follow rule. (The raw date is state since ADR-005 S4f --
    tournamentInfo.startAtDate -- so the subtree restore covers it.) So do
    the icon / banner files a media test writes into the runtime directory.
    """
    from pathlib import Path
    from src.core.DictHelper import deep_clone
    from src.core import TournamentMediaService as media

    saved = deep_clone(StateManager.Get("tournamentInfo") or {})
    service = TournamentInfoService.instance
    lastName = service._lastTournamentName
    files = {}
    for path, _key, _side in media.MEDIA.values():
        target = Path(path)
        files[target] = target.read_bytes() if target.exists() else None
    try:
        yield
    finally:
        for target, data in files.items():
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(data)
        StateManager.Set("tournamentInfo", saved)
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
    """Rendered from the day the state holds beside the formatted date
    (tournamentInfo.startAtDate, ADR-005 S4f)."""
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
    assert written.count("tournamentInfo.startAtDate") == 1, written
    # The published form is the formatted short date, and the day beside it
    # is the one the operator picked (its local midnight is core's).
    assert StateManager.Get("tournamentInfo.startAtDate") == "2026-03-14"
    assert TournamentInfoService.instance.DateOf("startAt") == "2026-03-14"


def test_the_date_edit_is_restored_from_the_state_after_a_restart(
        autonaApp, makePanel):
    """ADR-005 S4f: the day is state now, so a panel built in a new process
    (the state restored from user_data/state.json, nothing applied since)
    shows it. It used to live in the service's memory only."""
    from qtpy.QtCore import QDate

    StateManager.Set("tournamentInfo.startAtDate", "2024-07-05")
    autonaApp.settle()

    panel = makePanel()
    assert _field(panel, "startAt", QDateEdit).date() == QDate(2024, 7, 5)


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

    The QDateEdit is restored from the day beside the formatted date
    (tournamentInfo.startAtDate since S4f; a ledger in the service before).
    Were a manual edit to write the formatted key alone, the day would still
    hold the *loaded* tournament's date, so the next tournamentInfo delta --
    any of them -- would re-render the panel and put the loaded date back
    over what the operator had picked.
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


def test_the_sidebar_unset_asks_and_clears_through_core():
    """Owner's decision 2 (S4f): the sidebar's "Unset tournament" asks
    first, like the panel's button, through the same helper (which hands
    the clear to core's TournamentService)."""
    tokens = _windowTokens()
    assert "AutonaTournamentDataProvider.ConfirmClearTournament(self)" in (
        tokens)
    assert ".SetTournament(None)" not in tokens


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


def _pump(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    return condition()


@pytest.mark.parametrize("executorUp", [True, False],
                         ids=["fetched", "the app is quitting"])
def test_the_media_download_gives_the_button_back_and_clears_its_status(
        autonaApp, panel, monkeypatch, executorUp):
    """The fetch is core's job (TournamentMediaService.FetchMedia, ADR-005
    S4f; the panel's own job since S3f-2, a Worker before that, whose
    signals the garbage collector could take). The panel only draws
    tournamentInfo.media: its buttons are disabled and the footer says so
    while the status is "running", and both are given back when the run
    ends -- with a collection in between, since nothing of the call is kept.
    When the executor has shut down the run ends at once (cancelled) and
    nothing is shown."""
    import gc
    import threading
    from types import SimpleNamespace

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core import TournamentMediaService as media
    from src.core.TaskExecutor import TaskExecutor
    from src.Helpers.AutonaStatusManager import AutonaStatusManager

    if not executorUp:
        assert TaskExecutor.instance.drain(10)
        stopped = TaskExecutor()
        stopped.shutdown(10)
        monkeypatch.setattr(TaskExecutor, "instance", stopped)

    gate = threading.Event()

    def noUrl(strict=False):
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
        autonaApp.settle()
        assert StateManager.Get(media.MEDIA_PATH)["error"] == media.CANCELLED
        assert panel.btDownloadMedia.isEnabled()
        assert statuses == []
        return
    assert StateManager.Get(media.MEDIA_PATH)["status"] == media.RUNNING
    assert _pump(lambda: not panel.btDownloadMedia.isEnabled())
    assert not panel.btLoadInfoFromTournament.isEnabled()

    gc.collect()
    gate.set()
    assert TaskExecutor.instance.drain(10)
    gc.collect()
    assert _pump(panel.btDownloadMedia.isEnabled)

    assert panel.btLoadInfoFromTournament.isEnabled()
    assert StateManager.Get(media.MEDIA_PATH)["error"] == media.NO_MEDIA
    assert [kind for kind, _token in statuses] == ["push", "pop"]
    assert statuses[0][1] == statuses[1][1]


def _imageFile(tmp_path, size, name="picked.png"):
    from PIL import Image
    path = tmp_path / name
    Image.new("RGB", size, (200, 30, 30)).save(path)
    return str(path)


def test_an_icon_picked_on_the_panel_is_imported_by_core(
        autonaApp, panel, monkeypatch, tmp_path):
    """The file dialog is the panel's; the checks, the scaling (512) and
    the save are core's (ADR-005 S4f), and the preview follows the version
    it publishes."""
    from PIL import Image
    from qtpy.QtWidgets import QFileDialog
    from src.core import TournamentMediaService as media

    picked = _imageFile(tmp_path, (1000, 500))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (picked, ""))
    panel.iconView.clear()
    panel.LoadIcon()
    autonaApp.settle()

    with Image.open(media.MEDIA[media.ICON][0]) as saved:
        assert saved.format == "PNG" and saved.size == (512, 256)
    assert StateManager.Get("tournamentInfo.logoVersion") > 0
    assert not panel.iconView.pixmap().isNull()


def test_a_banner_picked_on_the_panel_is_scaled_and_announced(
        autonaApp, panel, monkeypatch, tmp_path):
    """Owner's decision 4 (S4f): the banner is scaled to a longest side of
    1920. media_updated -- what the thumbnail / result previews redraw on --
    follows tournamentInfo.bannerVersion (the provider adapter's), out of
    the state lock."""
    from PIL import Image
    from qtpy.QtWidgets import QFileDialog
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core import TournamentMediaService as media

    announced = []

    def receiver():
        announced.append(True)

    signals = AutonaTournamentDataProvider.instance.signals
    signals.media_updated.connect(receiver)
    try:
        picked = _imageFile(tmp_path, (3840, 1080))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            lambda *args, **kwargs: (picked, ""))
        panel.LoadBannerFile()
        assert announced == []  # deferred to the event loop
        autonaApp.settle()
    finally:
        signals.media_updated.disconnect(receiver)

    with Image.open(media.MEDIA[media.BANNER][0]) as saved:
        assert saved.format == "PNG" and saved.size == (1920, 540)
    assert StateManager.Get("tournamentInfo.bannerVersion") > 0
    assert announced == [True]


def test_a_file_that_is_not_an_image_is_refused_with_a_message(
        autonaApp, panel, monkeypatch, tmp_path):
    from qtpy.QtWidgets import QFileDialog, QMessageBox
    from src.core import TournamentMediaService as media

    picked = tmp_path / "notes.png"
    picked.write_bytes(b"not an image")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(picked), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text: warnings.append(text))
    before = StateManager.Get("tournamentInfo.bannerVersion")
    existed = os.path.exists(media.MEDIA[media.BANNER][0])

    panel.LoadBannerFile()
    autonaApp.settle()

    assert len(warnings) == 1
    assert StateManager.Get("tournamentInfo.bannerVersion") == before
    assert os.path.exists(media.MEDIA[media.BANNER][0]) == existed


def test_the_reset_button_resets_both_through_core(autonaApp, panel,
                                                   monkeypatch):
    from src.core.TournamentMediaService import TournamentMediaService

    calls = []
    monkeypatch.setattr(TournamentMediaService.instance, "ResetMedia",
                        lambda kind=None: calls.append(kind) or True)
    panel.btResetMediaToDefault.click()
    assert calls == [None]


@pytest.mark.parametrize("answer, cleared", [("Yes", True), ("No", False)])
def test_the_clear_button_asks_then_clears_through_core(
        autonaApp, panel, monkeypatch, answer, cleared):
    """Owner's decision 2 (S4f): the panel and the sidebar ask, and the
    clear is core's (which also forgets the selected game)."""
    from qtpy.QtWidgets import QMessageBox
    from src.core.TournamentService import TournamentService

    clears = []
    monkeypatch.setattr(TournamentService.instance, "ClearTournament",
                        lambda: clears.append(True))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: getattr(QMessageBox.StandardButton, answer))
    panel.btClearInfo.click()
    assert clears == ([True] if cleared else [])


def test_the_set_button_asks_core_for_the_media_too(autonaApp, panel,
                                                    monkeypatch):
    """The panel's "Set tournament" is the sidebar's URL dialog with the
    icon / banner fetched once the tournament is loaded (core's)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    calls = []
    monkeypatch.setattr(
        AutonaTournamentDataProvider.instance, "SetStartggEventSlug",
        lambda parent, fetchMedia=False: calls.append(fetchMedia) or True)
    panel.btLoadInfoFromTournament.click()
    assert calls == [True]


def test_the_game_combo_loads_a_game_by_its_codename(
        autonaApp, makePanel, monkeypatch):
    """By codename, as /api/v1's POST /game does -- not by the game's
    position in the scan, which a rescan can move."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    autonaApp.loadCharacters()
    panel = makePanel()
    index = panel.gameTitleCombo.findData("harness_game")
    assert index > 0

    loads = []
    monkeypatch.setattr(AutonaGameAssetManager.instance, "LoadGameByCodename",
                        lambda codename, **kwargs: loads.append(codename))
    panel._OnGameSelected(index)
    panel._OnGameSelected(0)  # the blank item loads nothing
    assert loads == ["harness_game"]


def test_the_game_combo_shows_no_game_after_a_clear(
        autonaApp, makePanel):
    """S4f review M2: every clear forgets the selected game (owner's
    decision 2), but the combo re-read the selection on tournament_changed,
    which the unload emitted before the forgetting -- so it kept showing the
    game the sidebar no longer did. The clear forgets first now."""
    from src.core.SettingsManager import SettingsManager
    from src.core.TournamentService import TournamentService

    autonaApp.loadCharacters()   # installs and selects the harness game
    panel = makePanel()
    assert panel.gameTitleCombo.currentData() == "harness_game"

    missing = object()
    stored = SettingsManager.Get("TOURNAMENT_URL", missing)
    try:
        TournamentService.instance.ClearTournament()
        autonaApp.settle()
        assert panel.gameTitleCombo.currentIndex() == 0
        assert not panel.gameTitleCombo.currentData()
    finally:
        if stored is missing:
            SettingsManager.Unset("TOURNAMENT_URL")
        else:
            SettingsManager.Set("TOURNAMENT_URL", stored)


def test_the_panel_writes_no_file_and_fetches_nothing_itself():
    """ADR-005 S4f: the icon / banner are read by the panel only to draw
    them. Writing them, scaling them and fetching them is
    core/TournamentMediaService.py's -- a panel that did any of it again
    would bypass the checks, the atomic write and the published versions."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "AutonaTournamentInfoWidget.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[-1])
    assert not imported & {"requests", "shutil", "PIL", "tempfile",
                           "TaskExecutor", "AutonaQtHelper"}, imported
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"save", "copy", "remove", "unlink", "write_bytes",
                         "replace", "submit", "SetIfChanged", "Set",
                         "PublishLogoVersion", "PublishAssetVersion"}, called
