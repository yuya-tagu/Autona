"""Setting and clearing the tournament (core/TournamentService.py, ADR-005
S4f).

Until S4f the four entrances disagreed: the URL dialog (the EVENT panel's
button and the sidebar's "Set tournament") resolved an admin/brackets URL
with a start.gg request on the GUI thread; the panel's and the sidebar's
clear kept the selected game, while the web clears forgot it (a slot of
``tournament_url_update``, which they emitted as a command). Pinned here:

- the URL forms taken, and the normalisation of an event URL;
- the order of a set (the URL saved, the adapter's load, the announcement,
  then the media fetch when asked) and of a clear (the URL emptied, the
  selected game forgotten, the adapter's unload -- whose tournament_changed
  the game pickers re-read the selection on -- and the announcement);
- an admin URL resolved by a job, never on the caller's thread and with no
  provider (a QObject) built for it, and a later set / clear winning over a
  resolution that answers after it;
- every clear entrance forgetting the selected game: the web operation (the
  legacy route, Socket.IO, /api/v1), the confirmation the panel and the
  sidebar share;
- (ADR-005 S4g, its review M1 / L2) how the newest set / clear is going, in
  the state: tournamentInfo.load -- published by the request itself, one
  revision per set / clear that its progress keeps -- and tournamentInfo.url
  beside it; a load that raises is a failed one.
"""
import os
import threading
from types import SimpleNamespace

import pytest

from src.core import TournamentService as tournament
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from src.core.TaskExecutor import TaskExecutor
from src.core.TournamentService import TournamentService

WAIT = 30
EVENT_URL = "https://www.start.gg/tournament/autona-cup/event/singles"
ADMIN_URL = "https://www.start.gg/admin/tournament/autona-cup/brackets/123"


# ── the URL forms ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, kind", [
    (EVENT_URL, tournament.EVENT),
    ("start.gg/tournament/autona-cup/events/singles/overview",
     tournament.EVENT),
    (ADMIN_URL, tournament.ADMIN),
    ("https://www.start.gg/tournament/autona-cup", None),
    ("https://example.com/tournament/a/event/b", None),
    ("", None),
    (None, None),
])
def test_the_url_forms_are_the_dialogs(url, kind):
    assert tournament.UrlKind(url) == kind


def test_an_event_url_is_cut_after_its_event_and_made_singular():
    assert tournament.NormalizeEventUrl(
        "https://www.start.gg/tournament/autona-cup/events/singles/"
        "brackets/1/2") == EVENT_URL
    assert tournament.NormalizeEventUrl(EVENT_URL) == EVENT_URL
    assert tournament.NormalizeEventUrl(ADMIN_URL) == ADMIN_URL


# ── harness: the service with a stand-in adapter ─────────────────────────

class _Adapter:
    def __init__(self, calls):
        self.calls = calls
        self.resolved = {}
        self.resolveThreads = []

    def LoadTournament(self, url, initialLoading=False, force=False):
        self.calls.append(("load", url, initialLoading, force))
        return True

    def UnloadTournament(self):
        self.calls.append(("unload",))

    def AnnounceUrl(self, url):
        self.calls.append(("announce", url))

    def ResolveEventUrl(self, url):
        self.resolveThreads.append(threading.current_thread())
        return self.resolved.get(url, url)


@pytest.fixture
def env(autonaApp, tmp_path, monkeypatch):
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.TournamentMediaService import TournamentMediaService

    os.chdir(tmp_path)
    (tmp_path / "user_data").mkdir(exist_ok=True)
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    # The published status starts as a fresh launch's (idle, revision 0),
    # and the session's is handed back.
    published = {path: StateManager.Get(path, None)
                 for path in (tournament.LOAD_PATH, tournament.URL_PATH)}
    StateManager.Set(tournament.LOAD_PATH,
                     {"status": "idle", "error": "", "revision": 0})
    StateManager.Set(tournament.URL_PATH, "")

    calls = []
    adapter = _Adapter(calls)
    queued = []
    service = TournamentService(provider=adapter, onMain=queued.append)
    monkeypatch.setattr(TournamentService, "instance", service)
    monkeypatch.setattr(GameAssetCatalog.instance, "ForgetSelection",
                        lambda: calls.append(("forget",)))
    monkeypatch.setattr(
        TournamentMediaService, "instance",
        SimpleNamespace(FetchMedia=lambda: calls.append(("fetchMedia",))))

    def run_queued():
        while queued:
            queued.pop(0)()

    try:
        yield SimpleNamespace(service=service, adapter=adapter, calls=calls,
                              queued=queued, run_queued=run_queued)
    finally:
        SettingsManager.settings = settings
        for path, value in published.items():
            if value is None:
                StateManager.Unset(path)
            else:
                StateManager.Set(path, value)
        os.chdir(autonaApp.dir)


@pytest.fixture
def executor(env, monkeypatch):
    assert TaskExecutor.instance.drain(WAIT)
    made = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", made)
    try:
        yield made
    finally:
        made.shutdown(WAIT)


# ── setting ──────────────────────────────────────────────────────────────

def test_an_event_url_is_saved_loaded_and_announced_on_the_main_thread(env):
    url = "https://www.start.gg/tournament/autona-cup/events/singles/overview"
    assert env.service.SetTournament(url) == tournament.STARTED
    # Nothing until the main thread runs it (the GUI executor: in place
    # when called there).
    assert env.calls == []
    env.run_queued()
    assert TournamentService.TournamentUrl() == EVENT_URL
    assert env.calls == [("load", EVENT_URL, False, False),
                         ("announce", EVENT_URL)]


def test_force_and_the_media_fetch_are_passed_on_after_the_load(env):
    env.service.SetTournament(EVENT_URL, force=True, fetchMedia=True)
    env.run_queued()
    assert env.calls == [("load", EVENT_URL, False, True),
                         ("announce", EVENT_URL), ("fetchMedia",)]


@pytest.mark.parametrize("url", [
    "https://www.start.gg/tournament/autona-cup", "https://example.com",
    "", None])
def test_any_other_url_is_refused_and_changes_nothing(env, url):
    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    assert env.service.SetTournament(url) == tournament.INVALID_URL
    assert env.queued == []
    assert TournamentService.TournamentUrl() == EVENT_URL


def test_an_admin_url_is_resolved_by_a_job_never_on_the_callers_thread(
        env, executor):
    """The dialog used to resolve it on the GUI thread (a start.gg request
    with up to five 20 s attempts): the window froze meanwhile."""
    env.adapter.resolved[ADMIN_URL] = (
        "https://www.start.gg/tournament/autona-cup/events/singles")
    assert env.service.SetTournament(ADMIN_URL, force=True) == (
        tournament.STARTED)
    assert env.adapter.resolveThreads == []
    assert env.queued == []
    assert executor.queued() == 1

    assert executor.run_next(WAIT) is True
    assert env.adapter.resolveThreads[0] is not threading.current_thread()
    env.run_queued()
    assert env.calls == [("load", EVENT_URL, False, True),
                         ("announce", EVENT_URL)]
    assert TournamentService.TournamentUrl() == EVENT_URL


def test_an_admin_url_that_cannot_be_resolved_changes_nothing(env,
                                                              executor):
    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    env.service.SetTournament(ADMIN_URL)   # resolves to itself: a failure
    assert executor.drain(WAIT)
    env.run_queued()
    assert env.calls == []
    assert TournamentService.TournamentUrl() == EVENT_URL


def test_a_later_event_url_wins_over_an_admin_url_still_resolving(
        env, executor):
    other = "https://www.start.gg/tournament/other-cup/event/doubles"
    env.adapter.resolved[ADMIN_URL] = EVENT_URL
    env.service.SetTournament(ADMIN_URL)
    env.service.SetTournament(other)
    env.run_queued()                 # the event URL, at once
    assert executor.drain(WAIT)      # the admin URL resolves late
    env.run_queued()
    assert env.calls == [("load", other, False, False),
                         ("announce", other)]
    assert TournamentService.TournamentUrl() == other


def test_a_later_clear_wins_over_a_resolution_that_answers_after_it(
        env, executor):
    env.adapter.resolved[ADMIN_URL] = EVENT_URL
    env.service.SetTournament(ADMIN_URL)
    env.service.ClearTournament()
    env.run_queued()                 # the clear
    assert executor.drain(WAIT)      # the resolution answers late
    env.run_queued()
    assert env.calls == [("forget",), ("unload",), ("announce", "")]
    assert TournamentService.TournamentUrl() == ""


def test_the_launch_restores_the_saved_url_as_an_initial_load(env):
    env.service.Restore()
    env.run_queued()
    assert env.calls == []

    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    env.service.Restore()
    env.run_queued()
    assert env.calls == [("load", EVENT_URL, True, False),
                         ("announce", EVENT_URL)]


def test_the_url_reader_answers_a_string(env):
    assert TournamentService.TournamentUrl() == ""
    SettingsManager.Set("TOURNAMENT_URL", None)
    assert TournamentService.TournamentUrl() == ""
    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    assert TournamentService.TournamentUrl() == EVENT_URL


# ── clearing ─────────────────────────────────────────────────────────────

def test_a_clear_empties_the_url_forgets_the_game_then_unloads(env):
    """Owner's decision 2 (S4f): every clear forgets the selected game --
    before the unload, whose tournament_changed the EVENT panel's combo and
    the sidebar re-read the selection on (S4f review M2), and before the
    announcement the sidebar's game button follows."""
    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    env.service.ClearTournament()
    env.run_queued()
    assert TournamentService.TournamentUrl() == ""
    assert env.calls == [("forget",), ("unload",), ("announce", "")]


# ── every clear entrance, through the real adapter ──────────────────────

@pytest.fixture
def forgets(autonaApp, monkeypatch, tmp_path):
    """The session's TournamentService and adapter, the selected game's
    forgetting recorded. TOURNAMENT_URL is handed back."""
    from src.core.GameAssetCatalog import GameAssetCatalog

    recorded = []
    monkeypatch.setattr(GameAssetCatalog.instance, "ForgetSelection",
                        lambda: recorded.append(True))
    missing = object()
    stored = SettingsManager.Get("TOURNAMENT_URL", missing)
    SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)
    try:
        yield recorded
    finally:
        if stored is missing:
            SettingsManager.Unset("TOURNAMENT_URL")
        else:
            SettingsManager.Set("TOURNAMENT_URL", stored)
        autonaApp.settle()


@pytest.mark.parametrize("url", [None, ""])
def test_the_web_operation_clears_through_core(autonaApp, forgets, url):
    """The legacy /set-tournament, Socket.IO set_tournament and /api/v1's
    POST /tournament with no URL."""
    from src import AutonaWebOperations

    assert AutonaWebOperations.load_tournament(url) == "OK"
    autonaApp.settle()
    assert forgets == [True]
    assert TournamentService.TournamentUrl() == ""


def test_v1_clears_through_core(autonaApp, forgets):
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import _build_web_app

    app, _socketio = _build_web_app()
    if "api_v1" not in app.blueprints:
        app.register_blueprint(api_v1)
    response = app.test_client().post("/api/v1/tournament", json={"url": ""})
    assert response.status_code == 202, response.get_data(as_text=True)
    autonaApp.settle()
    assert forgets == [True]
    assert TournamentService.TournamentUrl() == ""


@pytest.mark.parametrize("answer, cleared", [("Yes", True), ("No", False)])
def test_the_shared_confirmation_clears_through_core(
        autonaApp, forgets, monkeypatch, answer, cleared):
    """What the EVENT panel's button and the sidebar's "Unset tournament"
    call (the sidebar used to clear without asking, and kept the game)."""
    from qtpy.QtWidgets import QMessageBox
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: getattr(QMessageBox.StandardButton, answer))
    assert AutonaTournamentDataProvider.ConfirmClearTournament(None) is (
        cleared)
    autonaApp.settle()
    assert forgets == ([True] if cleared else [])
    assert TournamentService.TournamentUrl() == ("" if cleared else EVENT_URL)


def test_an_unsupported_legacy_url_changes_nothing(autonaApp, forgets):
    """The legacy route used to save any URL and then load no tournament
    (a clear that kept the game); it is refused now, still "OK"."""
    from src import AutonaWebOperations

    assert AutonaWebOperations.load_tournament("https://example.com") == "OK"
    autonaApp.settle()
    assert forgets == []
    assert TournamentService.TournamentUrl() == EVENT_URL


# ── the admin URL's resolution (S4f review L10) ─────────────────────────

def test_an_admin_url_is_resolved_without_building_a_provider(
        monkeypatch):
    """The job used to build a StartGGDataProvider -- a QObject -- on its
    thread to resolve it; a plain function makes the request now."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.TournamentDataProvider import StartGGDataProvider as startgg

    built = []
    monkeypatch.setattr(startgg.StartGGDataProvider, "__init__",
                        lambda self, *args: built.append(args))
    asked = []

    def query(url, type, headers, jsonParams, params, timeout):
        asked.append(jsonParams["variables"])
        return {"data": {"event": {
            "slug": "tournament/autona-cup/event/singles"}}}
    monkeypatch.setattr(startgg, "_QueryRequests", query)

    assert AutonaTournamentDataProvider.ResolveEventUrl(ADMIN_URL) == (
        EVENT_URL)
    assert asked == [{"id": "123"}]
    assert built == []


@pytest.mark.parametrize("answer", [{}, {"data": {"event": None}}])
def test_an_admin_url_start_gg_does_not_resolve_comes_back_as_it_was(
        monkeypatch, answer):
    from src.TournamentDataProvider import StartGGDataProvider as startgg

    monkeypatch.setattr(startgg, "_QueryRequests",
                        lambda *args: answer)
    assert startgg.ResolveEventUrl(ADMIN_URL) == ADMIN_URL
    assert startgg.ResolveEventUrl(EVENT_URL) == EVENT_URL


# ── the status (ADR-005 S4g: tournamentInfo.load / url, its review M1;
# S4f review L14) ─────────────────────────────────────────────────────────

def _status(service):
    load = service.Load()
    return load["status"], load["error"]


def _revision():
    return TournamentService.Load()["revision"]


def test_an_event_url_loads_then_settles_on_its_payload(env):
    assert _status(env.service) == (tournament.IDLE, "")
    before = _revision()
    env.service.SetTournament(EVENT_URL)
    # Published by the request, before the main thread has run it: what a
    # client is answered after is in the state already.
    assert _status(env.service) == (tournament.LOADING, "")
    assert _revision() == before + 1
    env.run_queued()
    assert _status(env.service) == (tournament.LOADING, "")

    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _status(env.service) == (tournament.LOADED, "")
    # Its progress keeps the request's revision.
    assert _revision() == before + 1
    assert StateManager.Get(tournament.LOAD_PATH) == {
        "status": "loaded", "error": "", "revision": before + 1}


@pytest.mark.parametrize("payload, applied", [
    # What the provider answers when start.gg did not answer or has no
    # such event: every field empty.
    ({"tournamentName": "", "eventName": ""}, True),
    ({}, True),
    # A key that did not apply (ApplyProviderPayload's failure).
    ({"tournamentName": "Autona Cup"}, False),
])
def test_a_payload_that_brings_no_tournament_is_a_load_failure(
        env, payload, applied):
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.PayloadApplied(payload, applied)
    assert _status(env.service) == (tournament.ERROR, tournament.LOAD_FAILED)


def test_a_load_that_raises_is_a_load_failure(env):
    """S4g review L2: it stayed "loading" -- no payload was coming."""
    def boom(url, initialLoading=False, force=False):
        raise RuntimeError("the provider could not be built")
    env.adapter.LoadTournament = boom
    env.service.SetTournament(EVENT_URL, fetchMedia=True)
    env.run_queued()
    assert _status(env.service) == (tournament.ERROR, tournament.LOAD_FAILED)
    # The URL was saved and is announced; no media fetch for it.
    assert env.calls == [("announce", EVENT_URL)]
    # A payload reported later (none should come) changes nothing.
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _status(env.service) == (tournament.ERROR, tournament.LOAD_FAILED)


def test_an_admin_url_resolves_then_loads(env, executor):
    env.adapter.resolved[ADMIN_URL] = EVENT_URL
    env.service.SetTournament(ADMIN_URL)
    assert _status(env.service) == (tournament.RESOLVING, "")
    revision = _revision()
    assert executor.drain(WAIT)
    env.run_queued()
    assert _status(env.service) == (tournament.LOADING, "")
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _status(env.service) == (tournament.LOADED, "")
    assert _revision() == revision


def test_an_admin_url_that_cannot_be_resolved_says_so(env, executor):
    """S4f review L14: it was only logged."""
    env.service.SetTournament(ADMIN_URL)   # resolves to itself: a failure
    assert executor.drain(WAIT)
    assert _status(env.service) == (tournament.ERROR, tournament.UNRESOLVED)


def test_a_resolver_that_raises_is_unresolved_too(env, executor):
    def boom(url):
        raise RuntimeError("start.gg is down")
    env.adapter.ResolveEventUrl = boom
    env.service.SetTournament(ADMIN_URL)
    assert executor.drain(WAIT)
    assert _status(env.service) == (tournament.ERROR, tournament.UNRESOLVED)


def test_a_late_resolution_failure_leaves_a_later_set_alone(env, executor):
    env.service.SetTournament(ADMIN_URL)
    env.service.SetTournament(EVENT_URL)
    revision = _revision()
    env.run_queued()
    assert executor.drain(WAIT)    # the admin URL fails late
    assert _status(env.service) == (tournament.LOADING, "")
    assert _revision() == revision


def test_a_clear_is_idle_and_its_empty_payload_changes_nothing(env):
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.ClearTournament()
    assert _status(env.service) == (tournament.IDLE, "")
    env.run_queued()
    # The adapter's unload emits an empty payload: no load waits for it.
    env.service.PayloadApplied({}, True)
    assert _status(env.service) == (tournament.IDLE, "")


def test_the_loaded_url_again_reports_what_its_data_did(env):
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.PayloadApplied({"tournamentName": ""}, True)
    assert _status(env.service) == (tournament.ERROR, tournament.LOAD_FAILED)

    # Not re-fetched (no force): the adapter does nothing, the status says
    # what the data did -- and a force fetches it again.
    env.adapter.LoadTournament = lambda url, initialLoading, force: force
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    assert _status(env.service) == (tournament.ERROR, tournament.LOAD_FAILED)
    env.service.SetTournament(EVENT_URL, force=True)
    env.run_queued()
    assert _status(env.service) == (tournament.LOADING, "")
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _status(env.service) == (tournament.LOADED, "")
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    assert _status(env.service) == (tournament.LOADED, "")


def test_a_payload_reported_during_the_load_settles_it(env):
    """A provider that answers before LoadTournament returns."""
    def load(url, initialLoading=False, force=False):
        env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
        return True
    env.adapter.LoadTournament = load
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    assert _status(env.service) == (tournament.LOADED, "")


def test_an_earlier_payload_does_not_settle_a_set_still_queued(env):
    other = "https://www.start.gg/tournament/other-cup/event/doubles"
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.SetTournament(other)      # queued: not applied yet
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _status(env.service) == (tournament.LOADING, "")
    env.run_queued()
    env.service.PayloadApplied({"tournamentName": "Other Cup"}, True)
    assert _status(env.service) == (tournament.LOADED, "")


def test_each_set_or_clear_takes_the_next_revision(env):
    """Like tournamentInfo.media's: a request's revision, which its
    progress keeps -- what a client tells its own request's result by."""
    start = _revision()
    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, True)
    assert _revision() == start + 1
    env.service.ClearTournament()
    assert _revision() == start + 2
    env.run_queued()
    # A refused URL is no request.
    assert env.service.SetTournament("https://example.com") == (
        tournament.INVALID_URL)
    assert _revision() == start + 2
    env.service.SetTournament(EVENT_URL)
    assert _revision() == start + 3


def test_the_url_is_published_beside_the_setting(env):
    env.service.SetTournament(
        "https://www.start.gg/tournament/autona-cup/events/singles/overview")
    # Saved and published on the main thread, normalised.
    assert StateManager.Get(tournament.URL_PATH) == ""
    env.run_queued()
    assert StateManager.Get(tournament.URL_PATH) == EVENT_URL

    # A payload that failed drops the setting (TournamentInfoService.
    # ApplyTournamentData); the published URL follows it.
    SettingsManager.Set("TOURNAMENT_URL", "")
    env.service.PayloadApplied({"tournamentName": "Autona Cup"}, False)
    assert StateManager.Get(tournament.URL_PATH) == ""

    env.service.SetTournament(EVENT_URL)
    env.run_queued()
    env.service.ClearTournament()
    env.run_queued()
    assert StateManager.Get(tournament.URL_PATH) == ""
    assert TournamentService.TournamentUrl() == ""


def test_the_status_survives_a_malformed_publication(env):
    """Read as idle at revision 0 (StateSchema.ReadTournamentLoad): what a
    state.json from before the key existed, or a hand-edited one, holds."""
    StateManager.Set(tournament.LOAD_PATH, "junk")
    assert TournamentService.Load() == {
        "status": "idle", "error": "", "revision": 0}
    StateManager.Set(tournament.LOAD_PATH,
                     {"status": 5, "revision": True})
    assert TournamentService.Load() == {
        "status": "idle", "error": "", "revision": 0}
    env.service.SetTournament(EVENT_URL)
    assert TournamentService.Load() == {
        "status": "loading", "error": "", "revision": 1}


def test_the_shells_wiring_reports_the_payload():
    """bootstrap.wire_tournament_data: what both shells connect to the
    provider's tournament_data_updated."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    wiring = "".join(
        (root / "src" / "bootstrap.py").read_text(encoding="utf-8").split())
    assert ("TournamentService.instance.PayloadApplied(data,applied)"
            in wiring)


# ── the window's wiring (moved here from test_tournament_info_panel.py
# when the Qt EVENT panel went, ADR-005 S4g) ─────────────────────────────
#
# Source-level, because Window cannot be built in this harness (importing
# src/Autona.py *is* the startup sequence -- agent-pitfalls #1). Losing any
# of these lines would not fail a test anywhere else: the app would start,
# the Event page would look fine, and tournamentInfo.* would simply never
# be written for anyone who did not open it.

def _windowTokens():
    """src/Autona.py as a whitespace-free token stream (a line wrap moving
    by one argument is not the regression these look for)."""
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
        "whenever the Event page is not built")
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
    first, through the shared helper (which hands the clear to core's
    TournamentService) -- the Event page asks with its own dialog."""
    tokens = _windowTokens()
    assert "AutonaTournamentDataProvider.ConfirmClearTournament(self)" in (
        tokens)
    assert ".SetTournament(None)" not in tokens
