"""The Statistics page's match list routes (ADR-005 S4k).

What the React Statistics page (web/src/StatsPage) asks the tournament's
match list through, added to /api/v1 (core's AnalysisService; ADR-005
S4j):

- ``POST /api/v1/analysis:refresh``  start a fetch: 202, the progress in
                                     the state (``analysis``); 409 CONFLICT
                                     while one runs (dropped, not queued),
                                     409 NOT_READY with no tournament
- ``GET  /api/v1/analysis``          ``{status, url, rows}``: the newest
                                     fetch's rows that made them, of the
                                     tournament loaded now -- else none

Through Flask's test client against the real route table, on the session's
AnalysisService and the fake providers of tests/test_analysis_rows.py
(which replays every recorded scenario through these routes): the contract
-- the envelopes, BUSY / no tournament, an error keeping the rows, a switch
dropping them, another tournament's rows never given, the rows and the
status read in one hold of StateManager.lock -- and the access
rules (this PC without the token, another computer with it for the write).
"""
import threading

import pytest

from src import AutonaWebOperations as WebOps
from src.core import AnalysisService as service
from src.core import WebAccess
from src.core.AnalysisService import AnalysisService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from tests.test_api_v1 import webApp  # noqa: F401
from tests.test_analysis_rows import (  # noqa: F401
    FakeProvider, _drain, _expectedRows, _pageCells, _provider,
    _switchTournament, analysis)
from tests.test_analysis_service import GatedProvider

WAIT = 10
SEEDS_KNOWN = "one group, seeds known"


@pytest.fixture
def client(analysis, webApp):  # noqa: F811
    return webApp.test_client()


def _cells(rows):
    return sorted((_pageCells(row) for row in rows),
                  key=lambda row: row[0][1])


def _read(client):
    response = client.get("/api/v1/analysis")
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    assert isinstance(body["revision"], int)
    return body["data"]


def _refresh(client, status=202, code=None):
    response = client.post("/api/v1/analysis:refresh")
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    if code is None:
        assert body["ok"] is True
        assert body["data"] == {"accepted": True,
                                "operation": "analysis.refresh"}
    else:
        assert body["ok"] is False
        assert body["error"]["code"] == code
    return body


def _progress():
    return StateManager.Get("analysis")


# ── the fetch ────────────────────────────────────────────────────────────

def test_a_fetch_is_started_and_its_rows_read(client, analysis):  # noqa: F811
    """202 once it is started -- its progress published at the next
    revision before the answer --, and the rows read once it is over,
    with the tournament's URL."""
    _switchTournament(_provider(SEEDS_KNOWN))
    before = _progress()["revision"]
    assert _read(client) == {"status": "idle", "url": "", "rows": []}

    _refresh(client)
    assert _progress()["revision"] == before + 1
    _drain(analysis)

    data = _read(client)
    assert data["status"] == "done"
    assert data["url"] == FakeProvider.url
    assert _cells(data["rows"]) == _expectedRows(SEEDS_KNOWN)
    # The rows as core makes them: structures, the wording the page's.
    assert set(data["rows"][0]) == {"round", "roundOrder", "p1", "p2",
                                    "score", "upset", "losersRun",
                                    "seedOver"}


def test_one_fetch_at_a_time(client, analysis):  # noqa: F811
    """A fetch asked for while one runs is 409 CONFLICT and changes
    nothing; the running one goes on."""
    provider = GatedProvider(*_scenario(SEEDS_KNOWN), gatePhases=True)
    _switchTournament(provider)
    try:
        _refresh(client)
        assert provider.phasesAsked.wait(WAIT)
        published = dict(_progress())
        assert published["status"] == "running"

        _refresh(client, 409, "CONFLICT")
        assert _progress() == published
        assert _read(client)["status"] == "running"
    finally:
        provider.release.set()
    _drain(analysis)
    data = _read(client)
    assert data["status"] == "done"
    assert _cells(data["rows"]) == _expectedRows(SEEDS_KNOWN)
    assert provider.calls.count(["phases"]) == 1


def _scenario(label):
    from tests.test_analysis_rows import SCENARIOS
    return SCENARIOS[label]


def test_no_tournament_is_not_ready(client, analysis):  # noqa: F811
    """With no tournament loaded nothing starts and nothing is published:
    409 NOT_READY (the page says no tournament is selected)."""
    _switchTournament(None)
    before = dict(_progress())
    _refresh(client, 409, "NOT_READY")
    _drain(analysis)
    assert _progress() == before
    assert _read(client) == {"status": "idle", "url": "", "rows": []}


def test_a_shell_without_the_service(client, monkeypatch):
    """No AnalysisService (a shell that did not build one): no tournament
    to fetch for, no rows."""
    _switchTournament(_provider(SEEDS_KNOWN))
    monkeypatch.setattr(AnalysisService, "instance", None)
    _refresh(client, 409, "NOT_READY")
    data = _read(client)
    assert data["url"] == "" and data["rows"] == []
    assert data["status"] == AnalysisService.Progress()["status"]


def test_an_error_keeps_the_rows_and_a_switch_drops_them(
        client, analysis):  # noqa: F811
    """A fetch that fails keeps the rows of the one before (the Qt panel's
    table did); loading a tournament drops them."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    _switchTournament(_provider(SEEDS_KNOWN))
    _refresh(client)
    _drain(analysis)
    rows = _read(client)["rows"]
    assert _cells(rows) == _expectedRows(SEEDS_KNOWN)

    # The same tournament, its groups failing this time.
    AutonaTournamentDataProvider.instance.provider = _provider(
        "every group failed")
    _refresh(client)
    _drain(analysis)
    assert _progress()["status"] == "error"
    data = _read(client)
    assert data == {"status": "error", "url": FakeProvider.url, "rows": rows}

    _switchTournament(_provider("one group, byes"))
    assert _read(client) == {"status": "idle", "url": "", "rows": []}


def test_another_tournaments_rows_are_never_given(
        client, analysis):  # noqa: F811
    """The rows are given only while their tournament is the one loaded:
    the service drops them when the tournament changes (Forget), and the
    route compares the URL as well."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    _switchTournament(_provider(SEEDS_KNOWN))
    _refresh(client)
    _drain(analysis)
    loaded = AutonaTournamentDataProvider.instance.provider

    other = _provider(SEEDS_KNOWN)
    other.url = "https://www.start.gg/tournament/other/event/analysis"
    AutonaTournamentDataProvider.instance.provider = other
    assert _read(client) == {"status": "done", "url": "", "rows": []}

    AutonaTournamentDataProvider.instance.provider = None
    assert _read(client) == {"status": "done", "url": "", "rows": []}

    AutonaTournamentDataProvider.instance.provider = loaded
    assert _cells(_read(client)["rows"]) == _expectedRows(SEEDS_KNOWN)


def test_the_read_is_of_one_moment(client, analysis, monkeypatch):  # noqa: F811
    """The rows, the provider and the status are read in one hold of
    StateManager.lock -- the service changes its rows and publishes
    `analysis` in one (_Publish / Forget) -- so that they are of the same
    moment (S4k review): a reader waits for a publish in progress."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    _switchTournament(_provider(SEEDS_KNOWN))
    _refresh(client)
    _drain(analysis)
    adapter = AutonaTournamentDataProvider.instance
    held = []

    def spy(name, real):
        def call(*args, **kwargs):
            held.append((name, StateManager.lock._is_owned()))
            return real(*args, **kwargs)
        return call

    monkeypatch.setattr(AnalysisService.instance, "Result",
                        spy("Result", AnalysisService.instance.Result))
    monkeypatch.setattr(AnalysisService, "Progress",
                        staticmethod(spy("Progress", AnalysisService.Progress)))
    monkeypatch.setattr(adapter, "GetProvider",
                        spy("GetProvider", adapter.GetProvider))
    read = WebOps.get_analysis()
    assert read["status"] == "done"
    assert _cells(read["rows"]) == _expectedRows(SEEDS_KNOWN)
    assert sorted(held) == [("GetProvider", True), ("Progress", True),
                            ("Result", True)]

    # While another thread holds the lock (a publish in progress), the
    # read waits for it.
    reads = []
    reader = threading.Thread(
        target=lambda: reads.append(WebOps.get_analysis()))
    with StateManager.lock:
        reader.start()
        reader.join(0.2)
        assert reader.is_alive() and reads == []
    reader.join(WAIT)
    assert not reader.is_alive()
    assert reads[0]["status"] == "done"


def test_the_operations_answer_from_any_thread(client, analysis):  # noqa: F811
    """The route's operations run on the calling (Flask) thread: nothing
    is handed to the GUI thread or waited on."""
    _switchTournament(_provider(SEEDS_KNOWN))
    answers = []
    thread = threading.Thread(
        target=lambda: answers.append(WebOps.refresh_analysis()))
    thread.start()
    thread.join(WAIT)
    assert answers == [service.STARTED]
    _drain(analysis)
    reads = []
    thread = threading.Thread(
        target=lambda: reads.append(WebOps.get_analysis()))
    thread.start()
    thread.join(WAIT)
    assert reads and reads[0]["status"] == "done"
    assert _cells(reads[0]["rows"]) == _expectedRows(SEEDS_KNOWN)


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

def test_the_routes_are_classified():
    assert WebAccess.ROUTE_ACCESS["api_v1.analysis_refresh"] == WebAccess.WRITE
    assert WebAccess.ROUTE_ACCESS["api_v1.get_analysis"] == WebAccess.READ
    assert "stats" in WebAccess.WEB_PANELS


@pytest.fixture
def lan(monkeypatch, webApp):  # noqa: F811
    """The routes' operations recorded, the token private."""
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    calls = []
    monkeypatch.setattr(
        WebOps, "refresh_analysis",
        lambda: calls.append("refresh_analysis") or service.STARTED)
    monkeypatch.setattr(
        WebOps, "get_analysis",
        lambda: calls.append("get_analysis") or {
            "status": "idle", "url": "", "rows": []})
    return calls


def test_the_fetch_needs_the_token_from_another_computer(
        autonaApp, webApp, lan):  # noqa: F811
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    refused = peer.post("/api/v1/analysis:refresh")
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert peer.post("/api/v1/analysis:refresh",
                     headers={"X-Autona-Token": token}).status_code == 202
    assert lan == ["refresh_analysis"]

    # This PC (the desktop's embedded view): no token.
    assert webApp.test_client().post(
        "/api/v1/analysis:refresh").status_code == 202
    assert lan == ["refresh_analysis", "refresh_analysis"]


def test_the_read_needs_no_token(autonaApp, webApp, lan):  # noqa: F811
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    assert peer.get("/api/v1/analysis").status_code == 200
    assert lan == ["get_analysis"]
