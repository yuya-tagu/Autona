"""The web operations of both shells (src/AutonaWebOperations.py, ADR-005 S3c).

The routes call the same functions in the desktop app and in
``main.py --headless``; what differs is the board host the shell registered.
Pinned here:

- the S2 operations write the same state whichever host runs them -- the
  desktop's (the board built, the write held for 反映) and the headless one
  (no board, published at once);
- headless, only the two shell operations (ADR-005 S3e-9) -- opening the
  set selection window and the thumbnail, which the desktop registers --
  are refused with NotAvailable, and the server turns that into 503 in the
  /api/v1 error shape: legacy routes, Socket.IO acks (/api/v1 has neither;
  its UNAVAILABLE stays the contract). Every other route and event answers
  in both shells (pinned below by walking the route table): the crew
  board's operations and the caster line-up's since ADR-005 S3e-2 (their
  recordings: tests/test_crew_operations.py,
  tests/test_commentary_service.py), the player slots' -- the Web's player
  edit and the tag lookup -- since S3e-4 (pinned below; the board's
  recording: tests/test_player_apply.py), the player / character counts of
  boards 1/2 since S3e-6 (pinned below; the board's recording:
  tests/test_roster_flow.py), and set loading and following -- load-set,
  POST /score, pull-stream -- since S3e-7 (tests/test_set_follow.py; the
  board's recording: tests/test_set_follow_flow.py);
- headless, the S2 operations run one at a time (the host's re-entrant
  lock), so a composite one (clear_all) is not interleaved with another
  request.
"""
import copy
import json
import os
import shutil
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebOperations import BoardHost, NotAvailable
from tests.fixtures.stats.spies import StatsTriggers
from tests.test_designer_service import make_project
from tests.test_webserver_routes import (
    EXPECTED_API_V1_ROUTES, EXPECTED_EVENTS, EXPECTED_ROUTES)

BOARD = 2   # the team board: every S2 field (team names included) applies

UNAVAILABLE_BODY = {"ok": False, "error": {
    "code": "UNAVAILABLE", "message": "not available in headless"}}


def _state():
    from src.core.StateManager import StateManager
    return StateManager


def _prepare(n):
    """The same starting point for both hosts, written through core."""
    from src.core.Actions import AutonaActions
    actions = AutonaActions.instance
    with _state().SaveBlock():
        actions.SetTeamName(n, 1, "Alpha")
        actions.SetTeamName(n, 2, "Bravo")
        actions.SetScore(n, 1, 2)
        actions.SetScore(n, 2, 1)
        # Both flags: a built board seeds team 1's as False, the headless
        # skeleton (the desktop's unbuilt boards' too) has no key yet.
        actions.SetTeamLosers(n, 1, False)
        actions.SetTeamLosers(n, 2, True)
        actions.SetMatchField(n, "phase", "Pools")
        actions.SetMatchField(n, "pool", "A")
        actions.SetMatchField(n, "match", "Winners Final")
        actions.SetBestOf(n, 5)


def _view(app, n):
    """What the operations below write: the teams and the match bar."""
    def get(key):
        return app.get(f"score.{n}.{key}")
    return {
        "teams": {team: {key: get(f"team.{team}.{key}")
                         for key in ("teamName", "score", "losers")}
                  for team in ("1", "2")},
        "match": {key: get(key)
                  for key in ("phase", "pool", "match", "match_en",
                              "best_of")},
        "teamsSwapped": bool(get("teamsSwapped")),
    }


def _goHeadless(app, monkeypatch):
    """No board built and the headless host: what main.py --headless runs.
    The skeleton is the one the headless shell seeds (StateSchema)."""
    from src.core import StateSchema as schema
    app.dropAllBoards()
    with _state().SaveBlock():
        for number in (1, 2, 3):
            schema.SeedBoard(number)
    app.settle()
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())


@pytest.fixture
def headless(autonaApp, monkeypatch):
    _goHeadless(autonaApp, monkeypatch)
    try:
        yield autonaApp
    finally:
        for number in (1, 2, 3):
            _state().Unset(f"score.{number}")
        autonaApp.settle()


# ── the same state from both hosts ───────────────────────────────────────

OPERATIONS = {
    "swap": lambda n: WebOps.swap_teams(n),
    "reset scores": lambda n: WebOps.reset_scores(n),
    "reset match": lambda n: WebOps.reset_match(n),
    "clear all": lambda n: WebOps.clear_all(n),
    "set fields": lambda n: WebOps.set_route(
        n, phase="Top 8", pool="", match="Grand Final", bestOf=3),
    "team name": lambda n: WebOps.set_route(n, team="2", teamName="Charlie"),
    "set losers": lambda n: WebOps.set_route(n, team="1", losers="true"),
    "team losers": lambda n: WebOps.set_team_losers(n, 1, True),
    "score up": lambda n: WebOps.team_scoreup(n, 1),
    "scores": lambda n: WebOps.set_scores(n, 3, 0),
}


@pytest.mark.parametrize("name", list(OPERATIONS))
def test_both_hosts_write_the_same_state(autonaApp, monkeypatch, name):
    operation = OPERATIONS[name]

    # The desktop: the board built (held), the operation on the GUI thread.
    autonaApp.board(BOARD)
    _prepare(BOARD)
    autonaApp.settle()
    # 反映 first, so what is pending afterwards is the operation's.
    WebOps.apply_changes(BOARD)
    autonaApp.settle()
    assert not _state().HasPendingChanges(f"score.{BOARD}")
    operation(BOARD)
    autonaApp.settle()
    desktop = _view(autonaApp, BOARD)
    # Held for 反映 there, as every write to a built board is.
    assert _state().HasPendingChanges(f"score.{BOARD}")

    # Headless: no board, the write published at once. The player slots
    # the headless shell seeds, as the desktop board has its own: clear_all
    # sets the roster (ADR-005 S3e-6), and a roster that goes to one player
    # from any other count empties the team names.
    try:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
        _prepare(BOARD)
        autonaApp.settle()
        operation(BOARD)
        autonaApp.settle()
        headless = _view(autonaApp, BOARD)
        assert not _state().HasPendingChanges(f"score.{BOARD}")
        assert _state().GetCommitted(f"score.{BOARD}.team.1.teamName") == (
            headless["teams"]["1"]["teamName"])
    finally:
        for number in (1, 2, 3):
            _state().Unset(f"score.{number}")
        autonaApp.settle()

    assert headless == desktop


# ── the player slots (ADR-005 S3e-4) ─────────────────────────────────────
#
# The Web's player edit (v1's PATCH players, the legacy update-team and its
# Socket.IO event) and the tag lookup were the board's (WebServerActions)
# and answered 503 headless. They are PlayerService.ApplyData on the slot
# the *state* has, through the board host: the same state from both shells.

_PLAYER = {"gamerTag": "ALPHA", "prefix": "SP", "name": "Real Alpha",
           "twitter": "alphatw", "custom_textbox": "note", "id": 7,
           "seed": 2, "city": "Osaka", "country_code": "JP",
           "state_code": "13"}


def _goHeadlessWithSlots(app, monkeypatch):
    """_goHeadless plus the player slots the headless shell seeds for boards
    1/2 (src/headless._SeedBoards: PlayerService.SeedEmptyRoster)."""
    from src.core.PlayerService import SeedEmptyRoster
    _goHeadless(app, monkeypatch)
    with _state().SaveBlock():
        for number in (1, 2):
            SeedEmptyRoster(number)
    app.settle()


def _dropHeadlessBoards(app):
    for number in (1, 2, 3):
        _state().Unset(f"score.{number}")
    app.settle()


@pytest.fixture
def playerDB(monkeypatch):
    """The local player database the loads read, as `db.players` ({DB tag:
    record}); what savePlayerToDb hands it is recorded in `db.saved`
    instead of written (the harness's database file is process-global),
    and neither the file write nor the change announcement that follow a
    save happen."""
    from src.core.PlayerDB import PlayerDB
    instance = PlayerDB.instance
    db = types.SimpleNamespace(players={}, saved=[])
    monkeypatch.setattr(instance, "database", db.players)

    def addPlayers(players, overwrite=False, deferRefresh=False):
        db.saved.append(copy.deepcopy(players))

    monkeypatch.setattr(instance, "AddPlayers", addPlayers)
    monkeypatch.setattr(instance, "SaveDB", lambda: None)
    monkeypatch.setattr(instance, "NotifyChanged", lambda: None)
    return db


def _swapThenLookup(n):
    WebOps.swap_teams(n)
    # The set's team 1, shown on the right after the swap: state team 2.
    assert WebOps.load_player_from_tag(n, "alpha", 1, 1) == "OK"


def _updateThenRename(n):
    assert WebOps.update_player(n, 2, 1, dict(_PLAYER)) is True
    assert WebOps.update_player(n, 2, 1, {"gamerTag": "BRAVO"}) is True


def _updateThenAddASponsor(n):
    # What the Web UI sends: the whole card, the sponsor typed in front of
    # the tag. Another player by the rule (the id goes), the face and the
    # seed stay (LIVE-BUG-1, S3e-4 review).
    assert WebOps.update_player(n, 2, 1, dict(
        _PLAYER, prefix="", avatar="avatars/alpha.png")) is True
    assert WebOps.update_player(n, 2, 1, {
        "gamerTag": "ALPHA", "prefix": "TEAM", "name": "", "twitter": "",
        "custom_textbox": "", "country_code": "", "state_code": ""}) is True


PLAYER_OPERATIONS = {
    "v1 player edit": lambda n: WebOps.update_player(
        n, 2, 1, dict(_PLAYER)),
    "legacy update-team": lambda n: WebOps.set_team_data(
        n, "2", "1", dict(_PLAYER)),
    "tag lookup": lambda n: WebOps.load_player_from_tag(n, "alpha", 2, 1),
    "tag lookup after a swap": _swapThenLookup,
    "an update naming another player": _updateThenRename,
    "a sponsor added to the tag": _updateThenAddASponsor,
}


def _playerView(app, n):
    def get(key):
        return app.get(f"score.{n}.{key}")
    return {
        "players": {team: copy.deepcopy(get(f"team.{team}.player"))
                    for team in ("1", "2")},
        "match_en": get("match_en"),
        "teamsSwapped": bool(get("teamsSwapped")),
    }


@pytest.mark.parametrize("name", list(PLAYER_OPERATIONS))
def test_both_hosts_load_the_same_player(autonaApp, monkeypatch, playerDB,
                                         name):
    operation = PLAYER_OPERATIONS[name]
    playerDB.players["SP ALPHA"] = dict(_PLAYER)
    n = 1

    # The desktop: the board built (held), the load on the GUI thread.
    autonaApp.board(n)
    _state().Set(f"score.{n}.match_en", "Winners Final")
    autonaApp.settle()
    operation(n)
    autonaApp.settle()
    desktop = _playerView(autonaApp, n)
    assert _state().HasPendingChanges(f"score.{n}")

    # Headless: the seeded slots, the load published at once.
    try:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
        _state().Set(f"score.{n}.match_en", "Winners Final")
        autonaApp.settle()
        operation(n)
        autonaApp.settle()
        headless = _playerView(autonaApp, n)
        assert not _state().HasPendingChanges(f"score.{n}")
    finally:
        _dropHeadlessBoards(autonaApp)

    # Values and key order (program_state.json is written in it).
    assert json.dumps(headless) == json.dumps(desktop)
    loaded = headless["players"]["2"]["1"]
    if name == "an update naming another player":
        assert (loaded["name"], loaded["team"], loaded["id"],
                loaded["city"], loaded["seed"]) == ("BRAVO", "SP", None,
                                                    None, 2)
    elif name == "a sponsor added to the tag":
        assert (loaded["name"], loaded["team"], loaded["id"],
                loaded["city"], loaded["seed"], loaded["online_avatar"])             == ("ALPHA", "TEAM", None, None, 2, "avatars/alpha.png")
    else:
        assert (loaded["name"], loaded["team"], loaded["id"],
                loaded["city"], loaded["seed"]) == ("ALPHA", "SP", 7,
                                                    "Osaka", 2)
    # The legacy route's set payload blanks the English round text; the
    # v1 edit and the tag lookup leave it.
    assert headless["match_en"] == (
        "" if name == "legacy update-team" else "Winners Final")


def test_both_hosts_save_the_player_to_the_database(autonaApp, monkeypatch,
                                                    playerDB):
    data = dict(_PLAYER, savePlayerToDb=True)
    autonaApp.board(1)
    assert WebOps.update_player(1, 1, 1, dict(data)) is True
    assert WebOps.set_team_data(1, "2", "1", dict(data)) == "OK"
    autonaApp.settle()
    desktop = copy.deepcopy(playerDB.saved)
    playerDB.saved.clear()

    try:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
        assert WebOps.update_player(1, 1, 1, dict(data)) is True
        assert WebOps.set_team_data(1, "2", "1", dict(data)) == "OK"
        autonaApp.settle()
    finally:
        _dropHeadlessBoards(autonaApp)

    assert playerDB.saved == desktop
    assert [[(p["prefix"], p["gamerTag"], p["name"], p["country_code"])
             for p in saved] for saved in desktop] \
        == [[("SP", "ALPHA", "Real Alpha", "JP")]] * 2
    # Without the flag nothing is saved.
    autonaApp.board(1)
    playerDB.saved.clear()
    WebOps.update_player(1, 1, 1, dict(_PLAYER))
    WebOps.set_team_data(1, "1", "1", dict(_PLAYER, savePlayerToDb=False))
    autonaApp.settle()
    assert playerDB.saved == []


@pytest.fixture
def realPlayerDB(autonaApp, tmp_path):
    """PlayerDB.instance with its real AddPlayers -- the file write and the
    change announcement, relayed to AutonaPlayerDB.SetupModel -- over an
    empty database of the test's own, saved under tmp_path. Put back, and
    the relay run for it, afterwards."""
    from src.core.PlayerDB import PlayerDB
    instance = PlayerDB.instance
    with instance.lock:
        saved = (instance.database, instance.path)
        instance.database = {}
        instance.path = str(tmp_path / "local_players.json")
    try:
        yield instance
    finally:
        with instance.lock:
            instance.database, instance.path = saved
        instance.NotifyChanged()
        autonaApp.settle()


def _heldElsewhere(lock):
    """Whether a thread holds `lock` (an RLock: tried from another thread,
    since the holder's own acquire would succeed)."""
    free = []

    def attempt():
        if lock.acquire(blocking=False):
            lock.release()
            free.append(True)

    worker = threading.Thread(target=attempt)
    worker.start()
    worker.join()
    return not free


@pytest.mark.parametrize("host", ["desktop", "headless"])
def test_the_database_save_is_made_outside_the_board_host(
        autonaApp, monkeypatch, realPlayerDB, host):
    """savePlayerToDb's database write is PlayerDB.AddPlayers, whose change
    announcement is relayed to AutonaPlayerDB.SetupModel -- subscribed in
    both shells -- which runs on the main thread and is waited for. Made
    under the headless host's lock, a request thread held the lock across
    that wait (ADR-005 S3e-4 review). Through the real AddPlayers: the write
    comes after the host has run the load, with neither the host's lock
    nor the state's held, and the slot already holding what is saved."""
    if host == "desktop":
        autonaApp.board(1)
    else:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
    boardHost = WebOps.boardHost
    hostRun = boardHost.Run
    running = []

    def run(op):
        running.append(op)
        try:
            return hostRun(op)
        finally:
            running.pop()

    monkeypatch.setattr(boardHost, "Run", run)
    seen = []

    def onChanged():
        get = _state().Get
        seen.append({
            "in the host": bool(running),
            "host lock held": _heldElsewhere(boardHost._lock),
            "state lock held": _heldElsewhere(_state().lock),
            "names": (get("score.1.team.1.player.1.name"),
                      get("score.1.team.2.player.1.name"))})

    realPlayerDB.changed.subscribe(onChanged)
    try:
        data = dict(_PLAYER, savePlayerToDb=True)
        assert WebOps.update_player(1, 1, 1, dict(data)) is True
        assert WebOps.set_team_data(
            1, "2", "1", dict(data, gamerTag="BRAVO", name="Real Bravo")) \
            == "OK"
        autonaApp.settle()
    finally:
        realPlayerDB.changed.unsubscribe(onChanged)
        if host == "headless":
            _dropHeadlessBoards(autonaApp)

    outside = {"in the host": False, "host lock held": False,
               "state lock held": False}
    assert seen == [dict(outside, names=("ALPHA", None)),
                    dict(outside, names=("ALPHA", "BRAVO"))]
    # What the slots held, in the database and its file.
    with realPlayerDB.lock:
        database = copy.deepcopy(realPlayerDB.database)
    assert {tag: (player["name"], player["country_code"],
                  player["state_code"])
            for tag, player in database.items()} == {
        "SP ALPHA": ("Real Alpha", "JP", "13"),
        "SP BRAVO": ("Real Bravo", "JP", "13")}
    with open(realPlayerDB.path, encoding="utf-8") as file:
        written = json.load(file)
    assert sorted((p["prefix"], p["gamerTag"]) for p in written) \
        == [("SP", "ALPHA"), ("SP", "BRAVO")]


def test_two_saves_of_one_player_arriving_out_of_order_keep_the_newer(
        autonaApp, monkeypatch, realPlayerDB):
    """Each edit's database write comes after its board host let go, so two
    edits of one player can write in the other order (the first request's
    thread scheduled last). The record is numbered as the host reads the
    slot, and the older one is dropped (S3e-4 review)."""
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    save = WebOps._save_record
    held = []
    monkeypatch.setattr(WebOps, "_save_record", held.append)
    try:
        assert WebOps.update_player(1, 1, 1, dict(
            _PLAYER, name="Older", savePlayerToDb=True)) is True
        assert WebOps.update_player(1, 1, 1, {
            "gamerTag": "ALPHA", "prefix": "SP", "name": "Newer",
            "savePlayerToDb": True}) is True
    finally:
        _dropHeadlessBoards(autonaApp)

    for prepared in reversed(held):
        save(prepared)

    with realPlayerDB.lock:
        assert realPlayerDB.database["SP ALPHA"]["name"] == "Newer"
    with open(realPlayerDB.path, encoding="utf-8") as file:
        assert [p["name"] for p in json.load(file)] == ["Newer"]


def _onAWorkerThread(call, timeout=10.0):
    """`call()` on a request thread while this thread -- the main thread,
    where the harness's Qt objects live -- keeps dispatching its events, as
    the shell's event loop does. Returns its result; fails if it has not
    finished after `timeout` seconds."""
    from qtpy.QtCore import QCoreApplication

    outcome = {}

    def work():
        try:
            outcome["result"] = call()
        except BaseException as e:
            outcome["error"] = e

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        worker.join(0.005)
    assert not worker.is_alive(), f"the request did not finish in {timeout}s"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


@pytest.mark.parametrize("host", ["desktop", "headless"])
def test_a_save_from_a_request_thread_finishes(
        autonaApp, monkeypatch, realPlayerDB, host):
    """The real path from a request thread: the desktop's board host queues
    the operation on the GUI thread and waits for it; the headless one runs
    it on the request thread, under its lock. Either way the database write
    that follows announces the change, and AutonaPlayerDB.SetupModel runs
    on the main thread (the GUI executor) while the request thread waits.

    While it waits, the main thread runs a board operation of its own
    (another edit) -- the lock it takes must be free. With the write made
    under the headless host's lock, the main thread would wait for the lock
    the request thread holds while that thread waits for the main thread:
    the lock is tried with a timeout here, so that is a failure rather than
    a hang. Headless, the relay only emits db_updated (no completer model),
    as the headless shell's does."""
    from src.AutonaPlayerDB import AutonaPlayerDB
    from src.Helpers.AutonaQtHelper import gui_thread_sync

    if host == "desktop":
        autonaApp.board(1)
    else:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
        monkeypatch.setattr(AutonaPlayerDB, "modelEnabled", False)
    boardHost = WebOps.boardHost
    seen = []

    @gui_thread_sync
    def onTheMainThread():
        lock = boardHost._lock
        if not lock.acquire(timeout=2):
            seen.append("the host's lock is held")
            return
        lock.release()
        seen.append(("main thread",
                     threading.current_thread() is threading.main_thread()))
        seen.append(("edit", WebOps.update_player(
            1, 2, 1, {"gamerTag": "BRAVO"})))

    realPlayerDB.changed.subscribe(onTheMainThread)
    try:
        applied = _onAWorkerThread(lambda: WebOps.update_player(
            1, 1, 1, dict(_PLAYER, savePlayerToDb=True)))
        autonaApp.settle()
        names = (_state().Get("score.1.team.1.player.1.name"),
                 _state().Get("score.1.team.2.player.1.name"))
    finally:
        realPlayerDB.changed.unsubscribe(onTheMainThread)
        if host == "headless":
            _dropHeadlessBoards(autonaApp)

    assert applied is True
    assert seen == [("main thread", True), ("edit", True)]
    assert names == ("ALPHA", "BRAVO")
    with realPlayerDB.lock:
        assert list(realPlayerDB.database) == ["SP ALPHA"]


@pytest.mark.parametrize("host", ["desktop", "headless"])
def test_a_web_load_asks_for_the_statistics(autonaApp, monkeypatch,
                                            playerDB, host):
    """The statistics a load calls for (another player in the slot: the
    recent sets and that team's last sets / standings; a seed that moved:
    the upset factor) are the load's own since ADR-005 S3e-5
    (PlayerService.Announce), in both shells. Until then the built board's
    card asked for them (DesktopBoardHost.PlayerLoaded), and headless
    nothing did."""
    playerDB.players["SP ALPHA"] = dict(_PLAYER)
    if host == "desktop":
        autonaApp.board(1)
    else:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
    try:
        with StatsTriggers(1) as triggers:
            assert WebOps.update_player(1, 2, 1, dict(_PLAYER)) is True
            autonaApp.settle()
            afterUpdate = dict(triggers.counts)
            # The same player again: nothing changed, nothing asked for.
            assert WebOps.set_team_data(1, "2", "1", {
                "gamerTag": "ALPHA", "prefix": "SP"}) == "OK"
            autonaApp.settle()
            afterSame = dict(triggers.counts)
            # Another player through the tag lookup, on team 1.
            assert WebOps.load_player_from_tag(1, "alpha", 1, 1) == "OK"
            autonaApp.settle()
        upset = autonaApp.get("score.1.upset_factor")
    finally:
        if host == "headless":
            _dropHeadlessBoards(autonaApp)

    assert afterUpdate == {
        "RecentSetsSignal": 1, "LastSetsP1Signal": 0, "LastSetsP2Signal": 1,
        "PlayerHistoryStandingsP1Signal": 0,
        "PlayerHistoryStandingsP2Signal": 1, "UpsetFactorCalculation": 1}
    assert afterSame == afterUpdate
    assert triggers.counts == {
        "RecentSetsSignal": 2, "LastSetsP1Signal": 1, "LastSetsP2Signal": 1,
        "PlayerHistoryStandingsP1Signal": 1,
        "PlayerHistoryStandingsP2Signal": 1, "UpsetFactorCalculation": 2}
    # Both players seeded 2: no upset (recalculated, not left unset).
    assert upset == 0


def test_a_load_names_the_state_team_or_the_set_team(autonaApp, playerDB):
    """After a swap, the edit (v1 and legacy) names the state team -- the
    column the Web UI shows -- and the tag lookup the set's team, as each
    always did."""
    playerDB.players["SP ALPHA"] = dict(_PLAYER)
    autonaApp.board(1)
    WebOps.swap_teams(1)
    autonaApp.settle()

    assert WebOps.update_player(1, 1, 1, {"gamerTag": "LEFT"}) is True
    assert WebOps.set_team_data(1, "2", "1", {"gamerTag": "RIGHT"}) == "OK"
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.player.1.name") == "LEFT"
    assert autonaApp.get("score.1.team.2.player.1.name") == "RIGHT"

    assert WebOps.load_player_from_tag(1, "ALPHA", 2, 1) == "OK"
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.player.1.name") == "ALPHA"
    assert autonaApp.get("score.1.team.2.player.1.name") == "RIGHT"


@pytest.mark.parametrize("host", ["desktop", "headless"])
def test_a_slot_the_state_does_not_have_is_not_written(
        autonaApp, monkeypatch, playerDB, host):
    """v1 answers False (its 404), the legacy edit logs and answers "OK",
    the tag lookup raises (the legacy route's 500) for a tag it found and
    answers "ERROR" for one it did not. Board 1 has one player a side; a 0
    is no slot either (it used to wrap around to the last card)."""
    playerDB.players["SP ALPHA"] = dict(_PLAYER)
    if host == "desktop":
        autonaApp.board(1)
    else:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
    try:
        before = json.loads(json.dumps(autonaApp.get("score.1")))
        for team, player in ((1, 2), (1, 0), (3, 1), (0, 1)):
            assert WebOps.update_player(
                1, team, player, {"gamerTag": "X"}) is False
            assert WebOps.set_team_data(
                1, str(team), str(player), {"gamerTag": "X"}) == "OK"
            with pytest.raises(IndexError):
                WebOps.load_player_from_tag(1, "alpha", team, player)
            assert WebOps.load_player_from_tag(
                1, "nobody", team, player) == "ERROR"
        autonaApp.settle()
        after = json.loads(json.dumps(autonaApp.get("score.1")))
        # The legacy edit's set payload blanks match_en, slot or not.
        for state in (before, after):
            state.pop("match_en", None)
        assert after == before
    finally:
        if host == "headless":
            _dropHeadlessBoards(autonaApp)


def test_the_crew_board_has_no_player_slots(headless):
    assert WebOps.update_player(3, 1, 1, {"gamerTag": "X"}) is False
    with pytest.raises(ValueError):
        WebOps.load_player_from_tag(3, "alpha", 1, 1)


# ── headless ─────────────────────────────────────────────────────────────

def test_a_headless_write_is_published_at_once(headless):
    assert WebOps.team_scoreup(1, 1) == "OK"
    headless.settle()

    assert _state().GetCommitted("score.1.team.1.score") == 1
    assert not _state().HasPendingChanges("score.1")
    assert WebOps.get_scoreboard(1)["team"]["1"]["score"] == 1


def test_headless_clears_the_players_without_a_board(headless):
    state = _state()
    with state.SaveBlock():
        state.Set("score.1.team.1.teamName", "Alpha")
        state.Set("score.1.team.1.player.1.name", "LEFT")
        state.Set("score.1.team.2.player.1.name", "RIGHT")
    headless.settle()

    assert WebOps.reset_players(1) == "OK"
    headless.settle()
    assert headless.get("score.1.team.1.player.1.name") == ""
    assert headless.get("score.1.team.2.player.1.name") == ""

    state.Set("score.1.team.1.player.1.name", "AGAIN")
    assert WebOps.clear_all(1) == "OK"
    headless.settle()
    assert headless.get("score.1.team.1.player.1.name") == ""
    # The legacy clear keeps the team names (ADR-005 S2 decision 3).
    assert headless.get("score.1.team.1.teamName") == "Alpha"


def test_headless_has_no_last_set(headless):
    assert WebOps.get_set(1) == "0"
    assert WebOps.get_swap(1) == "False"


# The player / character counts of boards 1/2 were the board's spinboxes
# and answered NotAvailable (503) headless until ADR-005 S3e-6. They are the
# state's now (AutonaActions.SetRoster): both hosts write the same roster --
# the slots, their character slots, the team names and crests a roster of
# one empties -- byte for byte (what the board's cards follow:
# tests/test_roster_flow.py).
ROSTER_OPERATIONS = {
    "players and a match": lambda n: WebOps.set_route(
        n, players=2, match="Final"),
    "characters": lambda n: WebOps.set_route(n, characters=2),
    "players and characters": lambda n: WebOps.set_route(
        n, players=3, characters=1),
    "back to one player": lambda n: (WebOps.set_route(n, players=2),
                                     WebOps.set_route(n, players=1)),
    "no players, then two": lambda n: (WebOps.set_route(n, players=0),
                                       WebOps.set_route(n, players=2)),
    "clear all": lambda n: (WebOps.set_route(n, players=3, characters=2),
                            WebOps.clear_all(n)),
    # A board with no player left keeps a characters per player for the
    # next ones, as its spinbox did: the one asked for meanwhile, else the
    # one it had (BoardHost.EmptyRosterCharacters).
    "no players, two characters, then two players": lambda n: (
        WebOps.set_route(n, players=0), WebOps.set_route(n, characters=2),
        WebOps.set_route(n, players=2)),
    "four characters, no players, then two": lambda n: (
        WebOps.set_route(n, characters=4), WebOps.set_route(n, players=0),
        WebOps.set_route(n, players=2)),
}


def _prepareRoster(n):
    """Team names and a match bar, the same on both hosts. Both LOSERS flags
    are written as they stand (False) straight into the state: a built
    board seeds team 1's and the headless skeleton neither, and the first
    write of one through core (_prepare's, or clear_all's reset) republishes
    that team's player names -- on one host only."""
    from src.core.Actions import AutonaActions
    actions = AutonaActions.instance
    with _state().SaveBlock():
        for team in (1, 2):
            _state().Set(f"score.{n}.team.{team}.losers", False)
        actions.SetTeamName(n, 1, "Alpha")
        actions.SetTeamName(n, 2, "Bravo")
        actions.SetMatchField(n, "match", "Winners Final")


def _roster(app, n):
    def get(key):
        return app.get(f"score.{n}.{key}")
    return {"teams": {team: {key: get(f"team.{team}.{key}")
                             for key in ("player", "teamName", "logo")}
                      for team in ("1", "2")},
            "match": get("match")}


@pytest.mark.parametrize("name", list(ROSTER_OPERATIONS))
def test_both_hosts_write_the_same_roster(autonaApp, monkeypatch, name):
    operation = ROSTER_OPERATIONS[name]

    board = autonaApp.board(BOARD)
    _prepareRoster(BOARD)
    autonaApp.settle()
    WebOps.apply_changes(BOARD)
    autonaApp.settle()
    operation(BOARD)
    autonaApp.settle()
    desktop = json.dumps(_roster(autonaApp, BOARD), ensure_ascii=False)
    # The board's cards follow the slots.
    roster = _roster(autonaApp, BOARD)["teams"]
    assert [len(board.team1playerWidgets), len(board.team2playerWidgets)] \
        == [len(roster["1"]["player"] or {}), len(roster["2"]["player"] or {})]

    try:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
        _prepareRoster(BOARD)
        autonaApp.settle()
        operation(BOARD)
        autonaApp.settle()
        headless = json.dumps(_roster(autonaApp, BOARD), ensure_ascii=False)
        assert not _state().HasPendingChanges(f"score.{BOARD}")
    finally:
        _dropHeadlessBoards(autonaApp)

    assert headless == desktop


@pytest.mark.parametrize("host", ["desktop", "headless"])
def test_an_emptied_board_gives_its_next_players_the_characters_it_keeps(
        autonaApp, monkeypatch, host):
    """players=0, characters=2, players=2: two players a team with two
    characters each -- and the characters a board had when its last player
    went, when nothing was asked for since -- as the two spinboxes had it.
    SetRoster alone would give them the default (3)."""
    if host == "desktop":
        autonaApp.board(BOARD)
    else:
        _goHeadlessWithSlots(autonaApp, monkeypatch)
    try:
        def counts():
            autonaApp.settle()
            return [len(slot["character"]) for team in (1, 2)
                    for slot in (autonaApp.get(
                        f"score.{BOARD}.team.{team}.player") or {}).values()]

        WebOps.set_route(BOARD, players=0)
        assert counts() == []
        WebOps.set_route(BOARD, characters=2)
        assert counts() == []
        WebOps.set_route(BOARD, players=2)
        assert counts() == [2, 2, 2, 2]

        WebOps.set_route(BOARD, characters=5)
        WebOps.set_route(BOARD, players=0)
        WebOps.set_route(BOARD, players=1)
        assert counts() == [5, 5]
    finally:
        if host == "headless":
            _dropHeadlessBoards(autonaApp)


def test_headless_answers_the_counts_without_a_board(headless):
    # The headless fixture's boards have no slot yet: two players a side,
    # with the characters per player a new player gets on a board that has
    # none (core's default), then two characters each.
    assert WebOps.set_route(1, players=2, match="Final") == "OK"
    headless.settle()
    assert sorted(headless.get("score.1.team.1.player")) == ["1", "2"]
    assert sorted(headless.get("score.1.team.2.player")) == ["1", "2"]
    assert headless.get("score.1.match") == "Final"
    assert WebOps.set_route(1, characters=2) == "OK"
    headless.settle()
    assert [len(headless.get(f"score.1.team.{team}.player.{slot}.character"))
            for team in (1, 2) for slot in (1, 2)] == [2, 2, 2, 2]
    assert not _state().HasPendingChanges("score.1")


def test_the_headless_host_runs_one_operation_at_a_time():
    # One lock: an operation another request's thread asks for waits for
    # the one running. Re-entrant: an operation may run another (v1's
    # PATCH set runs set_route inside its own task).
    host = BoardHost()
    inside = threading.Event()
    release = threading.Event()
    order = []

    def first():
        order.append("first")
        inside.set()
        release.wait(5)
        host.Run(lambda: order.append("first, nested"))
        order.append("first, done")

    running = threading.Thread(target=host.Run, args=(first,))
    running.start()
    assert inside.wait(5)
    waiting = threading.Thread(
        target=host.Run, args=(lambda: order.append("second"),))
    waiting.start()
    waiting.join(0.3)
    try:
        assert waiting.is_alive()
        assert order == ["first"]
    finally:
        release.set()
        running.join(5)
        waiting.join(5)

    assert not running.is_alive() and not waiting.is_alive()
    assert order == ["first", "first, nested", "first, done", "second"]


def test_a_headless_clear_is_not_interleaved_with_another_request(
        headless, monkeypatch):
    # clear_all is four steps; a swap asked for while it is between two of
    # them lands after the clear, not inside it.
    host = WebOps.boardHost
    midway = threading.Event()
    release = threading.Event()

    def resetCounts(board):   # the step between the reset and the players
        midway.set()
        release.wait(5)

    monkeypatch.setattr(host, "ResetCounts", resetCounts)
    state = _state()
    state.Set("score.1.team.1.player.1.name", "LEFT")
    headless.settle()

    clearing = threading.Thread(target=WebOps.clear_all, args=(1,))
    clearing.start()
    assert midway.wait(5)
    swapping = threading.Thread(target=WebOps.swap_teams, args=(1,))
    swapping.start()
    swapping.join(0.3)
    try:
        assert swapping.is_alive()
        assert not state.Get("score.1.teamsSwapped", False)
        assert state.Get("score.1.team.1.player.1.name") == "LEFT"
    finally:
        release.set()
        clearing.join(5)
        swapping.join(5)
    headless.settle()

    assert not clearing.is_alive() and not swapping.is_alive()
    assert state.Get("score.1.teamsSwapped") is True
    assert state.Get("score.1.team.1.player.1.name") == ""


def test_get_set_is_zero_headless_rather_than_refused(headless):
    # "0" is the answer for "no set loaded", and with no board none is --
    # the crew board included (the desktop's crew board has no set loader
    # and raises; headless there is nothing to ask).
    assert WebOps.get_set(2) == "0"
    assert WebOps.get_set(3) == "0"


# ── the 503 answers ──────────────────────────────────────────────────────

@pytest.fixture
def server(monkeypatch, tmp_path):
    """The real Flask app + Socket.IO server with no shell operations
    registered (the headless shell's, ADR-005 S3e-9), cwd = tmp_path."""
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WebOps, "openSetSelection", None)
    monkeypatch.setattr(WebOps, "renderThumbnail", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", ())
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    return app, socketio


def test_a_legacy_route_answers_503(server):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get("/scoreboard1-open-set")

    assert response.status_code == 503
    assert response.get_json() == UNAVAILABLE_BODY


def test_a_socketio_event_acks_the_503_body(server):
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    ack = ws.emit("open_set", json.dumps({"scoreboardNumber": "1"}),
                  callback=True)

    assert ack == UNAVAILABLE_BODY
    assert ws.get_received() == []


# What a web operation itself refuses headless -- the board host's
# NotAvailable, from host.Widget(): the same 503, as a route's answer and
# as an event's ack. The board that only a widget could serve is one that
# is neither 1/2 nor the crew board (no shell has one: the desktop answers
# 500 for it); the player / character counts of boards 1/2 were it until
# ADR-005 S3e-6 (core's now: test_the_roster_routes_answer_headless below).
def test_a_legacy_route_a_web_operation_refuses_answers_503(
        server, monkeypatch):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get("/scoreboard4-swap-teams")

    assert response.status_code == 503
    assert response.get_json() == UNAVAILABLE_BODY


def test_a_socketio_event_a_web_operation_refuses_acks_the_503_body(
        server, monkeypatch):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    ack = ws.emit("set", json.dumps({"scoreboardNumber": "4",
                                     "match": "Final"}), callback=True)

    assert ack == UNAVAILABLE_BODY
    assert ws.get_received() == []


# The counts of a board that has none -- neither 1/2 nor the crew board:
# refused before anything is written, as when the counts were the board's
# widgets, rather than answered OK having changed nothing. Nor does the
# board host remember characters for one.
def test_the_counts_of_a_board_without_any_answer_503(server, monkeypatch):
    host = BoardHost()
    monkeypatch.setattr(WebOps, "boardHost", host)
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    for path in ("/scoreboard4-set?players=2",
                 "/scoreboard4-set?characters=2",
                 "/scoreboard4-set?players=0&match=Final",
                 "/scoreboard0-set?characters=1"):
        response = client.get(path)
        assert response.status_code == 503, path
        assert response.get_json() == UNAVAILABLE_BODY, path
    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    ack = ws.emit("set", json.dumps({"scoreboardNumber": "4",
                                     "players": 2}), callback=True)
    assert ack == UNAVAILABLE_BODY
    assert _state().Get("score.4") is None
    assert _state().Get("score.0") is None

    host.KeepEmptyRosterCharacters(4, 2)
    host.KeepEmptyRosterCharacters(3, 2)
    assert host._emptyCharacters == {}
    host.KeepEmptyRosterCharacters(2, 5)
    assert host._emptyCharacters == {2: 5}


# What answered 503 until ADR-005 S3e-6 (the board host's NotAvailable):
# the player / character counts of boards 1/2, through every route. Core's
# now (test_both_hosts_write_the_same_roster above).
def test_the_roster_routes_answer_headless(server, autonaApp, monkeypatch):
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    try:
        for path in ("/scoreboard1-set?players=2",
                     "/scoreboard2-set?characters=2"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.get_data(as_text=True) == "OK", path
        response = client.patch("/api/v1/scoreboards/2/set",
                                json={"players": 3})
        assert response.status_code == 200, response.get_json()
        ws = socketio.test_client(app, flask_test_client=client)
        ws.get_received()
        ws.emit("set", json.dumps({"scoreboardNumber": "1",
                                   "characters": 1}))
        answers = [event["args"] for event in ws.get_received()
                   if event["name"] == "set"]
        autonaApp.settle()

        assert answers == [["OK"]]
        assert sorted(autonaApp.get("score.1.team.1.player")) == ["1", "2"]
        assert [len(slot["character"]) for team in (1, 2) for slot
                in autonaApp.get(f"score.1.team.{team}.player").values()] \
            == [1, 1, 1, 1]
        assert sorted(autonaApp.get("score.2.team.2.player")) \
            == ["1", "2", "3"]
        assert [len(autonaApp.get(f"score.2.team.1.player.{slot}.character"))
                for slot in (1, 2, 3)] == [2, 2, 2]
    finally:
        _dropHeadlessBoards(autonaApp)


# What answered 503 until ADR-005 S3e-2: the crew board's operations and the
# caster line-up's are core's now, and answer headless through the routes
# too (what they write: tests/test_crew_operations.py and
# tests/test_commentary_service.py, against the widgets' recordings).
ANSWERED_ROUTES = [
    ("/scoreboard3-crew-member1-1-stockdown", "OK"),
    ("/scoreboard3-crew-active2-3", "OK"),
    ("/scoreboard3-swap-teams", "OK"),
    ("/scoreboard3-get-swap", "True"),
    ("/scoreboard3-set?players=4&match=Final", "OK"),
    ("/scoreboard3-reset-scores", "OK"),
    ("/load-commentator-from-tag-12?tag=Nobody", "ERROR"),
]


def test_the_crew_and_caster_routes_answer_headless(server, headless):
    from src.core.CrewBattleService import CrewBattleService
    CrewBattleService(3).SeedDefaults()
    headless.settle()
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    for path, answer in ANSWERED_ROUTES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.get_data(as_text=True) == answer, path
    response = client.post("/update-commentary-3", json={"gamerTag": "Cast"})
    assert response.get_data(as_text=True) == "OK"
    headless.settle()

    assert headless.get("score.3.team.2.player.1.stock") == 3   # reset
    assert headless.get("score.3.team.1.activeMember") == "0"
    assert headless.get("score.3.team.1.memberNum") == 4
    assert headless.get("score.3.match") == "Final"
    assert headless.get("commentary.3.name") == "Cast"


# No /api/v1 operation answers 503 any more. /api/v1/bracket:refresh did
# until ADR-005 S3e-8 (the Top 8 import is core's now:
# tests/test_top8_refresh.py), /api/v1/scoreboards/{n}/stats:refresh until
# S3e-5 (the statistics are core's: test_the_stats_routes_answer_headless
# below), and :load-set / :pull-stream until S3e-7 (set loading and
# following are core's: tests/test_set_follow.py, where :pull-stream
# without a channel answers NOT_READY).


def _headlessPlayers(autonaApp, monkeypatch, playerDB):
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    playerDB.players["SP ALPHA"] = dict(_PLAYER)


def test_the_player_routes_answer_headless(server, autonaApp, monkeypatch,
                                           playerDB):
    """They answered 503 until ADR-005 S3e-4. The bodies are the ones the
    desktop answers: v1's envelope (404 for a slot the state does not
    have), the legacy routes' "OK" / "ERROR" / "No tag provided"."""
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    _headlessPlayers(autonaApp, monkeypatch, playerDB)
    try:
        response = client.patch("/api/v1/scoreboards/1/teams/1/players/1",
                                json={"gamerTag": "V1", "prefix": "P"})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["ok"] is True
        response = client.patch("/api/v1/scoreboards/1/teams/1/players/2",
                                json={"gamerTag": "V1"})
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "NOT_FOUND"
        assert client.patch("/api/v1/scoreboards/1/teams/1/players/1",
                            json={}).status_code == 200

        response = client.post("/scoreboard1-update-team-2-1",
                               json={"gamerTag": "LEGACY"})
        assert response.get_data(as_text=True) == "OK"
        response = client.post("/scoreboard1-update-team-2-5",
                               json={"gamerTag": "NOWHERE"})
        assert response.get_data(as_text=True) == "OK"
        autonaApp.settle()
        assert autonaApp.get("score.1.team.2.player.1.name") == "LEGACY"

        for path, answer in (
                ("/scoreboard1-load-player-from-tag-2-1?tag=alpha", "OK"),
                ("/scoreboard1-load-player-from-tag-2-1?tag=nobody",
                 "ERROR"),
                ("/scoreboard1-load-player-from-tag-2-1", "No tag provided")):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.get_data(as_text=True) == answer, path
        autonaApp.settle()

        assert autonaApp.get("score.1.team.1.player.1.mergedName") == "P | V1"
        assert autonaApp.get("score.1.team.2.player.1.name") == "ALPHA"
        assert _state().GetCommitted("score.1.team.2.player.1.id") == 7
    finally:
        _dropHeadlessBoards(autonaApp)


def test_the_player_events_emit_their_answer_headless(
        server, autonaApp, monkeypatch, playerDB):
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    _headlessPlayers(autonaApp, monkeypatch, playerDB)
    try:
        ws = socketio.test_client(app, flask_test_client=flask_client)
        ws.get_received()

        ws.emit("update_team", json.dumps({
            "scoreboardNumber": "1", "team": "1", "player": "1",
            "gamerTag": "SOCKET"}))
        for tag in ("alpha", "nobody", None):
            message = {"scoreboardNumber": "1", "team": "2", "player": "1"}
            if tag is not None:
                message["tag"] = tag
            ws.emit("load_player_from_tag", json.dumps(message))
        answers = [(event["name"], event["args"])
                   for event in ws.get_received()
                   if event["name"] in ("update_team",
                                        "load_player_from_tag")]
        autonaApp.settle()

        assert answers == [
            ("update_team", ["OK"]),
            ("load_player_from_tag", ["OK"]),
            ("load_player_from_tag", ["ERROR"]),
            ("load_player_from_tag", ["No tag provided"]),
        ]
        assert autonaApp.get("score.1.team.1.player.1.name") == "SOCKET"
        assert autonaApp.get("score.1.team.2.player.1.name") == "ALPHA"
    finally:
        _dropHeadlessBoards(autonaApp)


# ── the statistics headless (ADR-005 S3e-5) ──────────────────────────────

def _twoPlayers():
    """Board 1's two sides, loaded through the Web's player edit."""
    assert WebOps.update_player(1, 1, 1, {
        "gamerTag": "ONE", "id": [1, 10], "seed": 1}) is True
    assert WebOps.update_player(1, 2, 1, {
        "gamerTag": "TWO", "id": [2, 20], "seed": 5}) is True


def test_headless_asks_for_the_statistics_a_swap_and_a_clear_call_for(
        autonaApp, monkeypatch, playerDB):
    """What the desktop's board asked for after a swap / move / clear --
    once the event loop was back, once per operation -- is the operation's
    since ADR-005 S3e-5, so the headless shell asks too; the upset factor
    is recalculated inside the operation, as on the desktop."""
    from src.core.StatsService import StatsService
    from tests.fixtures.stats.spies import FakeFetcher
    fetcher = FakeFetcher()
    monkeypatch.setattr(StatsService, "fetcher", fetcher)
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    videogame = autonaApp.get("provider_videogame")
    _state().Set("provider_videogame", {"id": 1386, "name": "G"})
    try:
        _twoPlayers()
        autonaApp.settle()
        assert autonaApp.get("score.1.upset_factor") == 4
        assert autonaApp.get("score.1.recent_sets") == {
            "state": "loading", "sets": []}
        fetcher.requests.clear()

        assert WebOps.swap_teams(1) == "OK"
        autonaApp.settle()
        assert fetcher.asked() == []          # not inside the operation
        fetcher.runLater()
        assert fetcher.asked() == [
            ("recent_sets", ([2, 20], [1, 10], 1386)),
            ("last_sets", ([2, 20], 1)),
            ("history_sets", ([2, 20], 1, 1386)),
            ("last_sets", ([1, 10], 2)),
            ("history_sets", ([1, 10], 2, 1386)),
        ]
        assert autonaApp.get("score.1.upset_factor") == 4
        # The head-to-head answers: written (the same two players).
        fetcher.answer(0, {"request_time": 7, "sets": ["H2H"]})
        assert autonaApp.get("score.1.recent_sets.sets") == ["H2H"]
        fetcher.requests.clear()

        # A clear: no player left to fetch for -- the statistics are
        # emptied rather than left showing the previous pairing.
        assert WebOps.clear_all(1) == "OK"
        fetcher.runLater()
        autonaApp.settle()
        assert fetcher.asked() == []
        assert autonaApp.get("score.1.recent_sets") == {
            "state": "done", "sets": []}
        assert autonaApp.get("score.1.last_sets") == {"1": {}, "2": {}}
        assert autonaApp.get("score.1.history_sets") == {"1": {}, "2": {}}
        assert autonaApp.get("score.1.upset_factor") == 0
    finally:
        if videogame is None:
            _state().Unset("provider_videogame")
        else:
            _state().Set("provider_videogame", videogame)
        _dropHeadlessBoards(autonaApp)


STATS_ROUTES = [
    "/scoreboard1-stats-recent-sets",
    "/scoreboard1-stats-upset-factor",
    "/scoreboard1-stats-last-sets-both",
    "/scoreboard1-stats-history-sets-1",
]


def test_the_stats_routes_answer_headless(server, autonaApp, monkeypatch,
                                          playerDB):
    """They answered 503 until ADR-005 S3e-5 (and v1's stats:refresh too).
    No tournament is loaded here: the head-to-head settles as "done", the
    upset factor is the seeds' -- the bodies are the desktop's."""
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    try:
        _twoPlayers()
        autonaApp.settle()
        _state().Set("score.1.upset_factor", None)
        _state().Set("score.1.recent_sets", None)

        for path in STATS_ROUTES:
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.get_data(as_text=True) == "OK", path
        autonaApp.settle()
        assert autonaApp.get("score.1.upset_factor") == 4
        assert autonaApp.get("score.1.recent_sets") == {
            "state": "done", "sets": []}

        _state().Set("score.1.upset_factor", None)
        response = client.post("/api/v1/scoreboards/1/stats:refresh",
                               json={})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["ok"] is True
        assert autonaApp.get("score.1.upset_factor") == 4

        ws = socketio.test_client(
            app, flask_test_client=client)
        ws.get_received()
        ws.emit("stats_upset_factor", json.dumps({"scoreboardNumber": "1"}))
        answers = [event["args"] for event in ws.get_received()
                   if event["name"] == "stats_upset_factor"]
        assert answers == [["OK"]]

        # The crew board has no statistics: the legacy routes' 500, v1's
        # 400 (require_board_12), as on the desktop.
        assert client.get(
            "/scoreboard3-stats-recent-sets").status_code == 500
        response = client.post("/api/v1/scoreboards/3/stats:refresh",
                               json={})
        assert response.status_code == 400
    finally:
        _dropHeadlessBoards(autonaApp)


_LAST_SET = {
    "winnerId": 1, "fullRoundText": "Winners Final",
    "entrant1Score": 3, "entrant2Score": 1,
    "phaseGroup": {"displayIdentifier": "1", "phase": {"name": "Top 8"}},
    "slots": [
        {"initialSeedNum": 1, "entrant": {"participants": [{"player": {
            "id": 1, "prefix": "SP", "gamerTag": "ONE"}}]}},
        {"initialSeedNum": 4, "entrant": {"participants": [{"player": {
            "id": 3, "prefix": None, "gamerTag": "THREE"}}]}},
    ]}


def test_headless_statistics_from_a_request_thread_to_the_state(
        server, autonaApp, monkeypatch, playerDB):
    """All the way, headless, with only the network faked: a request on a
    thread of its own (the server's) asks through the route, WebOps and
    StatsService; the fetcher bootstrap registered (AutonaStatsUtil) hands
    it to the tournament provider adapter, whose job (the task executor's,
    ADR-005 S3f-2) runs the real Start.gg provider's query and parse; the
    answer is written on the main thread (ADR-005 S3e-5 review)."""
    from src.AutonaStatsUtil import AutonaStatsUtil
    from src.AutonaTournamentDataProvider import         AutonaTournamentDataProvider
    from src.core.StatsService import StatsService
    from src.core.TaskExecutor import TaskExecutor
    from src.TournamentDataProvider.StartGGDataProvider import         StartGGDataProvider

    assert isinstance(StatsService.fetcher, AutonaStatsUtil)
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    adapter = AutonaTournamentDataProvider.instance
    try:
        # The players first, with no tournament loaded: nothing is fetched.
        monkeypatch.setattr(adapter, "provider", None)
        _twoPlayers()
        autonaApp.settle()

        startgg = StartGGDataProvider(
            "https://www.start.gg/tournament/t/event/e", adapter)
        queries = []

        def query(url=None, type=None, headers={}, jsonParams=None,
                  params=None):
            # The network: the one thing faked.
            queries.append((threading.current_thread(),
                            jsonParams.get("operationName"),
                            jsonParams.get("variables")))
            if jsonParams.get("operationName") != "PlayerLastSetsQuery":
                return {}
            return {"data": {"event": {"sets": {"nodes": [_LAST_SET]}}}}

        monkeypatch.setattr(startgg, "QueryRequests", query)
        monkeypatch.setattr(adapter, "provider", startgg)
        writes = []
        update = StatsService.UpdateLastSets

        def recording(self, data):
            writes.append(threading.current_thread())
            return update(self, data)

        monkeypatch.setattr(StatsService, "UpdateLastSets", recording)

        answers = []
        request = threading.Thread(target=lambda: answers.append(
            client.get("/scoreboard1-stats-last-sets-1")))
        request.start()
        request.join(10)
        assert [(r.status_code, r.get_data(as_text=True))
                for r in answers] == [(200, "OK")]

        deadline = time.monotonic() + 10
        while not writes and time.monotonic() < deadline:
            autonaApp.settle()
            time.sleep(0.02)
        assert TaskExecutor.instance.drain(10)
        autonaApp.settle()

        assert [(name, variables) for _, name, variables in queries] == [
            ("PlayerLastSetsQuery",
             {"eventSlug": "tournament/t/event/e", "playerID": 1})]
        worker = queries[0][0]
        assert worker not in (threading.main_thread(), request)
        assert writes == [threading.main_thread()]
        sets = autonaApp.get("score.1.last_sets.1")
        assert list(sets) == ["1"]
        assert {key: value for key, value in sets["1"].items()
                if key != "round_name"} == {
            "phase_id": "", "phase_name": "Top 8",
            "player_score": 3, "player_seed": 1, "player_team": "SP",
            "player_name": "ONE", "oponent_score": 1, "oponent_seed": 4,
            "oponent_team": None, "oponent_name": "THREE"}
    finally:
        TaskExecutor.instance.drain(10)
        _dropHeadlessBoards(autonaApp)


# ── the shell operations (ADR-005 S3e-9) ────────────────────────────────
#
# Opening a board's set selection window and rendering its thumbnail are
# the desktop's windows' work: the desktop registers its scoreboard dock's
# (bootstrap_gui()), the headless shell nothing, and the operation answers
# NotAvailable (503). They were the web server's actions class until then,
# handed in by the desktop window and None headless.

def test_the_shell_operations_are_refused_without_a_shell_that_has_them(
        monkeypatch):
    monkeypatch.setattr(WebOps, "openSetSelection", None)
    monkeypatch.setattr(WebOps, "renderThumbnail", None)

    with pytest.raises(NotAvailable):
        WebOps.open_sets("1")
    with pytest.raises(NotAvailable):
        WebOps.get_thumbnail("1", "png")

    calls = []
    monkeypatch.setattr(WebOps, "openSetSelection",
                        lambda n: calls.append(("open", n)) or "OK")
    monkeypatch.setattr(WebOps, "renderThumbnail",
                        lambda n, f: calls.append(("thumb", n, f)) or "/t.jpg")
    assert WebOps.open_sets("2") == "OK"
    assert WebOps.get_thumbnail("1", "jpg") == "/t.jpg"
    assert calls == [("open", "2"), ("thumb", "1", "jpg")]


def test_the_desktop_shell_operations_run_on_the_gui_thread(autonaApp,
                                                            monkeypatch):
    """What bootstrap_gui() registered: the scoreboard dock's, run on the
    GUI thread while the request's thread waits for the answer -- the
    window opened before "OK" (it used to be queued for later: the
    request's thread emitted the board's signal), the thumbnail's absolute
    path (its .jpg sibling for "jpg"), None when none was made."""
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    from src.Helpers.AutonaQtHelper import in_gui_thread
    manager = AutonaScoreboardManager.instance
    assert WebOps.openSetSelection == manager.OpenSetSelection
    assert WebOps.renderThumbnail == manager.RenderThumbnail
    board = autonaApp.board(1)
    window = board.selectSetWindow
    calls = []
    monkeypatch.setattr(window, "PrepareForDisplay",
                        lambda: calls.append(("prepare", in_gui_thread())))
    monkeypatch.setattr(window, "show",
                        lambda: calls.append(("show", in_gui_thread())))
    thumbnail = os.path.join("out", "thumbnails", "board.png")

    def generate(quiet_mode=False, disable_msgbox=False):
        calls.append(("thumbnail", in_gui_thread(), quiet_mode,
                      disable_msgbox))
        return thumbnail

    monkeypatch.setattr(board, "GenerateThumbnail", generate)

    answers = []

    def request():
        answers.append(WebOps.open_sets("1"))
        answers.append(len(calls))   # opened before the answer
        answers.append(WebOps.get_thumbnail("1", "png"))
        answers.append(WebOps.get_thumbnail("1", "jpg"))

    worker = threading.Thread(target=request)
    worker.start()
    deadline = time.monotonic() + 10
    while worker.is_alive() and time.monotonic() < deadline:
        autonaApp.settle()
        time.sleep(0.01)
    worker.join(1)

    assert not worker.is_alive()
    assert answers == [
        "OK", 2, os.path.abspath(thumbnail),
        os.path.abspath(os.path.join("out", "thumbnails", "board.jpg"))]
    assert calls == [("prepare", True), ("show", True),
                     ("thumbnail", True, True, True),
                     ("thumbnail", True, True, True)]

    monkeypatch.setattr(board, "GenerateThumbnail", lambda **kwargs: None)
    assert WebOps.get_thumbnail("1", "png") is None


# The desktop's shell operations as the Web reaches them: the routes'
# answers (the thumbnail sent as the image it is, under the type the route
# names), the Socket.IO event's emitted "OK", and what has no window or
# thumbnail -- the crew board, a board that is none -- the 500 it always
# was.
def test_the_desktop_shell_operations_answer_through_the_routes(
        server, autonaApp, monkeypatch, tmp_path):
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    manager = AutonaScoreboardManager.instance
    monkeypatch.setattr(WebOps, "openSetSelection", manager.OpenSetSelection)
    monkeypatch.setattr(WebOps, "renderThumbnail", manager.RenderThumbnail)
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    board = autonaApp.board(1)
    window = board.selectSetWindow
    opened = []
    monkeypatch.setattr(window, "PrepareForDisplay",
                        lambda: opened.append("prepare"))
    monkeypatch.setattr(window, "show", lambda: opened.append("show"))
    png = tmp_path / "thumbnails" / "board.png"
    png.parent.mkdir()
    png.write_bytes(b"\x89PNG\r\n\x1a\n-the-png")
    jpg = png.with_suffix(".jpg")
    jpg.write_bytes(b"\xff\xd8\xff-the-jpg")
    monkeypatch.setattr(board, "GenerateThumbnail",
                        lambda quiet_mode=False, disable_msgbox=False:
                        str(png))

    response = client.get("/scoreboard1-open-set")
    assert (response.status_code, response.get_data(as_text=True)) == (
        200, "OK")
    assert opened == ["prepare", "show"]

    for fileFormat, image in (("png", png), ("jpg", jpg)):
        response = client.get(f"/scoreboard1-get-thumbnail-{fileFormat}")
        try:
            assert response.status_code == 200
            assert response.headers["Content-Type"] == f"image/{fileFormat}"
            assert response.get_data() == image.read_bytes()
        finally:
            response.close()

    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    ack = ws.emit("open_set", json.dumps({"scoreboardNumber": "1"}),
                  callback=True)
    assert ack != UNAVAILABLE_BODY
    assert [(item["name"], item["args"]) for item in ws.get_received()] == [
        ("open_set", ["OK"])]
    assert opened == ["prepare", "show"] * 2
    ws.disconnect()

    for path in ("/scoreboard3-open-set", "/scoreboard3-get-thumbnail-png",
                 "/scoreboard4-open-set", "/scoreboard4-get-thumbnail-png"):
        assert client.get(path).status_code == 500, path
    autonaApp.settle()


# Opening the window never failed the request: it was queued until S3e-9
# (the request's thread emitted the board's signal), so what it raised was
# reported and the answer was "OK" -- still. PySide6 hands a slot's
# exception to sys.excepthook (the app logs it as uncaught) rather than
# raising it into emit(); OpenSetSelection logs one a binding would raise
# there. Either way it is reported once.
def test_a_set_selection_window_that_fails_to_open_still_answers_ok(
        autonaApp, monkeypatch):
    from loguru import logger
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    monkeypatch.setattr(WebOps, "openSetSelection",
                        AutonaScoreboardManager.instance.OpenSetSelection)
    board = autonaApp.board(1)

    def fail():
        raise RuntimeError("the window failed")

    monkeypatch.setattr(board.selectSetWindow, "PrepareForDisplay", fail)
    hooked = []
    monkeypatch.setattr(sys, "excepthook",
                        lambda kind, error, trace: hooked.append(error))
    logged = []
    sink = logger.add(lambda message: logged.append(message.record),
                      level="ERROR")
    try:
        assert WebOps.open_sets("1") == "OK"
    finally:
        logger.remove(sink)

    reported = [str(error) for error in hooked] + [
        str(record["exception"].value) for record in logged
        if record["exception"] is not None]
    assert reported == ["the window failed"]


# ── every route and event answers headless (ADR-005 S3e-9) ───────────────
#
# §4.4's completion condition, walked off the route table rather than
# listed, so a route or event added later is walked too. Headless and with
# no tournament loaded, every legacy route, /api/v1 route and Socket.IO
# event is sent what gets it past its own argument checks and guards (the
# Designer window's token and the Figma pairing code included), and what
# comes back is pinned:
#
# - every route's status: 200, but for the answers _NOT_200 lists, each the
#   operation's own (a shell operation's 503, what the crew board does not
#   have, no tournament loaded, a name that names nothing). A route that
#   fell over somewhere new answers something else;
# - every event: no exception in its handler (python-socketio's handler
#   for those, ws_on_error, is swapped for a recorder) but the one
#   _EVENT_RAISES lists, and what it emits back: its own name, but for
#   _EVENT_REPLIES;
# - the two shell operations are the only ones refused (503, the
#   UNAVAILABLE ack).
#
# What is not pinned: a 200's body (a legacy route answers its errors as
# 200 strings: "ERROR" for a tag the player DB does not have, ...). The
# 500s are the desktop's too
# (test_what_answers_500_headless_answers_500_on_the_desktop).

SHELL_ENDPOINTS = {"open_sets", "get_thumbnail"}
SHELL_EVENTS = {"open_set"}

# A value for each URL argument of the rules; the board ones are walked.
# A slug, file name and import id that name nothing (a well-formed id).
_URL_VALUES = {"team": "1", "player": "1", "member": "1", "caster": "1",
               "color": "ff0000", "fileFormat": "png", "setId": "1", "t": 1,
               "p": 1, "code": "JP", "slug": "x", "import_id": "0" * 32,
               "filename": "x", "panel": "shell-check"}
_BOARD_ARGUMENTS = ("scoreboardNumber", "n")

# What a route needs besides its URL to get to its operation rather than
# stop at a missing argument: a query string, or the JSON body of a write.
_QUERIES = {"set_route": "?match=Final", "load_set": "?set=1",
            "load_player_from_tag": "?tag=Nobody",
            "load_commentator_from_tag": "?tag=Nobody",
            "get_states": "?countryCode=JP"}
_DISPLAY_OPTION = "show_real_name"
_DESIGN = {"project": make_project("Walk", with_image=False), "assets": {}}
_BODIES = {
    "post_score": {"scoreboardNumber": "1"},
    "post_display_option": {"option": _DISPLAY_OPTION, "value": True},
    "set_team_data": {"gamerTag": "ALPHA"},
    "set_commentary_data": {"gamerTag": "Cast"},
    "set_game": {"codename": "no_such_game"},
    "designer_save_project": _DESIGN,
    "designer_receive_import": _DESIGN,
    "api_v1.score_adjust": {"delta": 1},
    "api_v1.score_set": {"team1": 1, "team2": 0},
    "api_v1.team_color": {"color": "#ff0000"},
    "api_v1.set_update": {"match": "Final"},
    "api_v1.player_update": {"gamerTag": "ALPHA"},
    "api_v1.display_options_update": {_DISPLAY_OPTION: True},
    "api_v1.clear": {"confirm": True},
    "api_v1.reset_players": {"confirm": True},
    "api_v1.reset_match": {"confirm": True},
    "api_v1.load_set": {"setId": 1},
    "api_v1.game_set": {"codename": "no_such_game"},
}

# Every route that answers anything but 200 here, by (endpoint, board),
# and what.
_NOT_200 = {
    # The shell operations (ADR-005 S3e-9).
    **{(endpoint, board): 503 for endpoint in SHELL_ENDPOINTS
       for board in (1, 2, 3)},
    # What the crew board does not have -- set loading, player slots,
    # statistics: core's ValueError, the legacy routes' 500; /api/v1's
    # 400 INVALID_ARGUMENT (every board operation of its but the crew's).
    **{(endpoint, 3): 500 for endpoint in (
        "load_set", "pull_stream_set", "load_player_from_tag",
        "stats_recent_sets", "stats_upset_factor", "stats_last_sets",
        "stats_history_sets")},
    **{(f"api_v1.{name}", 3): 400 for name in (
        "score_adjust", "score_reset", "score_set", "team_color",
        "set_update", "player_update", "swap_teams", "apply_changes",
        "stats_refresh", "clear", "reset_players", "reset_match",
        "load_set", "pull_stream")},
    # No tournament loaded: the legacy set lookup raises (its provider is
    # None -- the desktop's too), /api/v1 answers 409 NOT_READY.
    ("get_match", None): 500,
    ("api_v1.get_match", None): 409,
    ("api_v1.load_set", 1): 409, ("api_v1.load_set", 2): 409,
    ("api_v1.pull_stream", 1): 409, ("api_v1.pull_stream", 2): 409,
    ("api_v1.bracket_refresh", None): 409,
    # Accepted, and run in the background.
    ("api_v1.tournament_load", None): 202,
    # A name that names nothing: no such file, design, import or game.
    ("file_request", None): 404,
    ("designer_load_project", None): 404,
    ("designer_delete_project", None): 404,
    ("designer_get_import", None): 404,
    ("api_v1.game_set", None): 404,
}

# The Socket.IO events' messages (the routes' arguments). "connect" is the
# connection itself; () is an event that takes no message.
_EVENT_MESSAGES = {
    "connect": None,
    "get_comms": (),
    "update_game": {"codename": "no_such_game"},
    "update_team": {"scoreboardNumber": "1", "team": "1", "player": "1",
                    "gamerTag": "ALPHA"},
    "update_commentary": {"commentator": "1", "gamerTag": "Cast"},
    "load_player_from_tag": {"scoreboardNumber": "1", "team": "1",
                             "player": "1", "tag": "Nobody"},
    "load_commentator_from_tag": {"commentator": "1", "tag": "Nobody"},
    "crew_member_stock": {"scoreboardNumber": "3", "team": "1",
                          "member": "1", "change": 1},
    "crew_set_active": {"scoreboardNumber": "3", "team": "1",
                        "member": "1"},
    "states": {"countryCode": "JP"},
    "get_match": {"setId": "1"},
    "load_set": {"scoreboardNumber": "1", "set": "1"},
    "set": {"scoreboardNumber": "1", "match": "Final"},
    "team_color": {"scoreboardNumber": "1", "team": "1",
                   "color": "ff0000"},
}
_EVENT_DEFAULT = {"scoreboardNumber": "1", "team": "1", "player": "1"}

# What an event emits back when it is not one event of its own name.
_EVENT_REPLIES = {
    "current_game": ["get_current_game"],
    "program-state-update": ["program_state_update"],
    # An old slip, kept: the emitted name is the contract (the S3e residue
    # in docs/changes/architecture-modernization.md).
    "team_color": ["team_scoredown"],
    # Answered as the ack instead.
    "open_set": [],
    "states": [],
    # Raises (_EVENT_RAISES).
    "get_match": [],
}

# The events whose handler raises here: the set lookup with no tournament
# loaded, as its route (500) and on the desktop alike. Logged
# (ws_on_error), nothing acked or emitted.
_EVENT_RAISES = {"get_match": AttributeError}


def _walk_routes(app):
    """(endpoint, board, method, url) for every rule and method the app
    has, the board arguments walked over 1, 2 and 3 (board None: a rule
    that takes none)."""
    for rule in app.url_map.iter_rules():
        boards = [name for name in _BOARD_ARGUMENTS if name in rule.arguments]
        for board in ((1, 2, 3) if boards else (None,)):
            values = {name: _URL_VALUES[name] for name in rule.arguments
                      if name not in _BOARD_ARGUMENTS}
            values.update({name: board for name in boards})
            _, url = rule.build(values)
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                yield (rule.endpoint, board, method,
                       url + _QUERIES.get(rule.endpoint, ""))


def test_every_route_and_event_answers_headless_but_the_shell_operations(
        server, autonaApp, monkeypatch, playerDB, tmp_path):
    from src.AutonaDesignerService import AutonaDesignerService as designer
    from src.AutonaTournamentDataProvider import (
        AutonaTournamentDataProvider)
    from src.core.CountryCatalog import CountryCatalog
    from src.core.CrewBattleService import CrewBattleService
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    # No tournament: nothing reaches start.gg (its routes answer for "none
    # loaded"), and loading "none" changes nothing.
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        None)
    # The country data, which the window (or a player card) loads: loaded,
    # whatever ran before, and put back afterwards.
    catalog = CountryCatalog.instance
    monkeypatch.setattr(catalog, "_current", catalog._current)
    if not catalog.IsLoaded():
        assert catalog.Load()
    # The Designer's routes past their guards -- the window's token, the
    # Figma plugin's pairing code -- onto designs kept under tmp_path.
    runtime = tmp_path / "designer_runtime"
    runtime.mkdir()
    for name in designer.RUNTIME_FILES:
        (runtime / name).write_bytes(b"/* shared runtime */")
    monkeypatch.setattr(designer, "ROOT", tmp_path / "designs")
    monkeypatch.setattr(designer, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(designer, "_tokens", set())
    monkeypatch.setattr(designer, "_imports", {})
    monkeypatch.setattr(designer, "_latest_import", None)
    monkeypatch.setattr(designer, "_pairing_failures", 0)
    monkeypatch.setattr(designer, "_pairing_locked_until", 0.0)
    token = designer.issue_token()
    # The web page the routes serve, which the test's runtime has no build
    # of: a stand-in, in the directory they read it from (removed after).
    web = Path(os.path.abspath(".")) / "web"
    assert web.parent == Path(autonaApp.dir) and not web.exists()
    (web / "build").mkdir(parents=True)
    (web / "build" / "index.html").write_text("<!doctype html>",
                                              encoding="utf-8")
    raised = []
    monkeypatch.setattr(socketio, "default_exception_handler", raised.append)
    kept = {root: copy.deepcopy(value)
            for root, value in _state().state.items() if root != "score"}
    _goHeadlessWithSlots(autonaApp, monkeypatch)
    CrewBattleService(3).SeedDefaults()
    autonaApp.settle()
    answers = []
    events = {}
    try:
        for endpoint, board, method, url in _walk_routes(app):
            body = _BODIES.get(endpoint)
            # The pairing code read per request: the walk regenerates it.
            headers = {"X-Autona-Designer-Token": token,
                       designer.PAIRING_HEADER: designer.pairing_code()}
            if body is None and method == "GET":
                response = client.open(url, method=method, headers=headers)
            else:
                response = client.open(url, method=method, json=body or {},
                                       headers=headers)
            answers.append((endpoint, board, method, url,
                            response.status_code,
                            response.get_json(silent=True)))
            response.close()
            autonaApp.settle()

        ws = socketio.test_client(app, flask_test_client=client)
        ws.get_received()
        for event in sorted(socketio.server.handlers["/"]):
            message = _EVENT_MESSAGES.get(event, _EVENT_DEFAULT)
            if message is None:
                continue
            before = len(raised)
            if message == ():
                ack = ws.emit(event, callback=True)
            else:
                ack = ws.emit(event, json.dumps(message), callback=True)
            events[event] = (ack, [item["name"] for item in ws.get_received()],
                             [type(error) for error in raised[before:]])
            autonaApp.settle()
            ws.get_received()
        ws.disconnect()
    finally:
        shutil.rmtree(web)
        _dropHeadlessBoards(autonaApp)
        with _state().SaveBlock():
            for root in set(_state().state) - set(kept) - {"score"}:
                _state().Unset(root)
            for root, value in kept.items():
                if _state().Get(root) != value:
                    _state().Set(root, value)
        autonaApp.settle()

    # Every route and event of the ledger was walked
    # (tests/test_webserver_routes.py: the legacy routes and /api/v1's).
    assert {endpoint for endpoint, *_ in answers} == {
        endpoint for _, endpoint, _
        in EXPECTED_ROUTES + EXPECTED_API_V1_ROUTES}
    assert set(events) | {"connect"} == set(EXPECTED_EVENTS)
    # The shell operations, and only they, are refused.
    assert [(endpoint, board, method, url, status) for
            endpoint, board, method, url, status, body in answers
            if (status == 503) != (endpoint in SHELL_ENDPOINTS)] == []
    assert [(endpoint, board, body) for endpoint, board, _, _, _, body
            in answers if endpoint in SHELL_ENDPOINTS
            and body != UNAVAILABLE_BODY] == []
    assert sorted(event for event, (ack, _, _) in events.items()
                  if ack == UNAVAILABLE_BODY) == sorted(SHELL_EVENTS)
    # Each route answered what its operation answers.
    assert set(_NOT_200) <= {(endpoint, board)
                             for endpoint, board, *_ in answers}
    assert [(endpoint, board, method, url, status) for
            endpoint, board, method, url, status, _ in answers
            if status != _NOT_200.get((endpoint, board), 200)] == []
    # Each event reached its operation: nothing raised but the set lookup,
    # and each answered as it does.
    assert {event: kinds for event, (_, _, kinds) in events.items()
            if kinds} == {event: [kind]
                          for event, kind in _EVENT_RAISES.items()}
    assert {event: names for event, (_, names, _) in events.items()
            if names != [event]} == _EVENT_REPLIES
    assert events["states"][0] == WebOps.get_states("JP")
    assert events["states"][0]


# The set lookup headless, with a tournament: the route and the event
# reach the provider (the walk above has none, so both stop at it).
def test_the_set_lookup_answers_headless_with_a_tournament(
        server, headless, monkeypatch):
    from src.AutonaTournamentDataProvider import (
        AutonaTournamentDataProvider)
    asked = []
    provider = types.SimpleNamespace(
        GetMatch=lambda setId: asked.append(setId) or {"id": setId})
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        provider)
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get("/get-match-7")
    assert (response.status_code, response.get_json()) == (200, {"id": 7})

    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    ws.emit("get_match", json.dumps({"setId": "8"}), callback=True)
    assert [(item["name"], item["args"]) for item in ws.get_received()] == [
        ("get_match", [{"id": 8}])]
    ws.disconnect()
    assert asked == [7, 8]


# What answers 500 in the walk is not the headless shell's: the desktop's
# board host answers it alike -- the crew board's checks come before a
# board is built, and the set lookup has no provider in either shell.
def test_what_answers_500_headless_answers_500_on_the_desktop(
        server, autonaApp, monkeypatch, playerDB):
    from src.AutonaTournamentDataProvider import (
        AutonaTournamentDataProvider)
    from src.AutonaWebOperations import DesktopBoardHost
    assert isinstance(WebOps.boardHost, DesktopBoardHost)
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        None)
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    answered = {key for key, status in _NOT_200.items() if status == 500}
    walked = [(endpoint, board, method, url)
              for endpoint, board, method, url in _walk_routes(app)
              if (endpoint, board) in answered]
    assert {(endpoint, board) for endpoint, board, _, _ in walked} \
        == answered

    statuses = []
    for endpoint, board, method, url in walked:
        statuses.append((endpoint, board,
                         client.open(url, method=method).status_code))
        autonaApp.settle()

    assert statuses == [(endpoint, board, 500)
                        for endpoint, board, _, _ in walked]
