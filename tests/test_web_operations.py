"""The web operations of both shells (src/AutonaWebOperations.py, ADR-005 S3c).

The routes call the same functions in the desktop app and in
``main.py --headless``; what differs is the board host the shell registered.
Pinned here:

- the S2 operations write the same state whichever host runs them -- the
  desktop's (the board built, the write held for 反映) and the headless one
  (no board, published at once);
- headless, what a widget answers -- the player / character counts of
  boards 1/2, the board-dependent actions (WebServerActions) -- is refused
  with NotAvailable, and the server turns that into 503 in the /api/v1
  error shape: legacy routes, Socket.IO acks, /api/v1 (UNAVAILABLE). The
  crew board's operations and the caster line-up's are core's since
  ADR-005 S3e-2 and answer in both shells (their recordings:
  tests/test_crew_operations.py, tests/test_commentary_service.py), and so
  are the player slots' -- the Web's player edit and the tag lookup -- since
  S3e-4 (pinned below; the board's recording: tests/test_player_apply.py);
- headless, the S2 operations run one at a time (the host's re-entrant
  lock), so a composite one (clear_all) is not interleaved with another
  request.
"""
import copy
import json
import threading
import time
import types

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebOperations import BoardHost, NotAvailable

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

    # Headless: no board, the write published at once.
    try:
        _goHeadless(autonaApp, monkeypatch)
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


STATS = ("RecentSetsSignal", "LastSetsP1Signal", "LastSetsP2Signal",
         "PlayerHistoryStandingsP1Signal", "PlayerHistoryStandingsP2Signal",
         "UpsetFactorCalculation")


def test_a_web_load_asks_the_desktop_board_for_the_statistics(
        autonaApp, playerDB):
    """The statistics a load calls for (another player in the slot: the
    recent sets and that team's last sets / standings; a seed that moved:
    the upset factor) are still the built board's: the operation hands its
    change to the board's card (DesktopBoardHost.PlayerLoaded), as the
    board's own loads do. Headless nothing fetches them (ADR-005 S3e-5)."""
    playerDB.players["SP ALPHA"] = dict(_PLAYER)
    board = autonaApp.board(1)
    counts = dict.fromkeys(STATS, 0)
    signals = board.stats.signals
    slots = {}
    for name in STATS:
        def count(*_args, name=name):
            counts[name] += 1
        slots[name] = count
        getattr(signals, name).connect(count)
    try:
        assert WebOps.update_player(1, 2, 1, dict(_PLAYER)) is True
        autonaApp.settle()
        afterUpdate = dict(counts)
        # The same player again: nothing changed, nothing asked for.
        assert WebOps.set_team_data(1, "2", "1", {
            "gamerTag": "ALPHA", "prefix": "SP"}) == "OK"
        autonaApp.settle()
        afterSame = dict(counts)
        # Another player through the tag lookup, on team 1.
        assert WebOps.load_player_from_tag(1, "alpha", 1, 1) == "OK"
        autonaApp.settle()
    finally:
        for name, slot in slots.items():
            getattr(signals, name).disconnect(slot)

    assert afterUpdate == {
        "RecentSetsSignal": 1, "LastSetsP1Signal": 0, "LastSetsP2Signal": 1,
        "PlayerHistoryStandingsP1Signal": 0,
        "PlayerHistoryStandingsP2Signal": 1, "UpsetFactorCalculation": 1}
    assert afterSame == afterUpdate
    assert counts == {
        "RecentSetsSignal": 2, "LastSetsP1Signal": 1, "LastSetsP2Signal": 1,
        "PlayerHistoryStandingsP1Signal": 1,
        "PlayerHistoryStandingsP2Signal": 1, "UpsetFactorCalculation": 2}


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


# What only a widget answers: the player / character counts of boards 1/2
# (the crew board's member count is core's, S3e-2). Refused before anything
# is written.
REFUSED = {
    "player count": lambda: WebOps.set_route(1, players=2, match="Final"),
    "character count": lambda: WebOps.set_route(1, characters=2),
    "team board's player count": lambda: WebOps.set_route(2, players=3),
}


@pytest.mark.parametrize("name", list(REFUSED))
def test_headless_refuses_what_needs_a_widget(headless, name):
    before = json.dumps(headless.get("score"), sort_keys=True)

    with pytest.raises(NotAvailable) as refused:
        REFUSED[name]()
    headless.settle()

    assert refused.value.message == "not available in headless"
    assert json.dumps(headless.get("score"), sort_keys=True) == before


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
    """The real Flask app + Socket.IO server with no board-dependent actions
    (the headless shell's WebServer(actions=None)), cwd = tmp_path."""
    from src import AutonaApiV1
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WebServer, "actions", None)
    monkeypatch.setattr(AutonaApiV1, "_actions", None)
    monkeypatch.setattr(AutonaApiV1, "_initialized", True)
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


# What a web operation itself refuses headless (the board host's
# NotAvailable, not WebServer._board_actions()): the same 503.
REFUSED_ROUTES = [
    "/scoreboard1-set?players=2",
    "/scoreboard2-set?characters=2",
]


@pytest.mark.parametrize("path", REFUSED_ROUTES)
def test_a_legacy_route_a_web_operation_refuses_answers_503(
        server, monkeypatch, path):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get(path)

    assert response.status_code == 503
    assert response.get_json() == UNAVAILABLE_BODY


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


@pytest.mark.parametrize("event,body", [
    ("set", {"scoreboardNumber": "1", "players": 2}),
    ("set", {"scoreboardNumber": "2", "characters": 2}),
])
def test_a_socketio_event_a_web_operation_refuses_acks_the_503_body(
        server, monkeypatch, event, body):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    ack = ws.emit(event, json.dumps(body), callback=True)

    assert ack == UNAVAILABLE_BODY
    assert ws.get_received() == []


# /api/v1/bracket:refresh answered 503 until ADR-005 S3e-8 (the Top 8
# import is core's now: tests/test_top8_refresh.py).
@pytest.mark.parametrize("path,body", [
    ("/api/v1/scoreboards/1:load-set", {"setId": "1"}),
    ("/api/v1/scoreboards/1:pull-stream", None),
    ("/api/v1/scoreboards/1/stats:refresh", None),
])
def test_a_v1_operation_answers_unavailable(server, path, body):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.post(path, json=body or {})

    assert response.status_code == 503, response.get_json()
    assert response.get_json()["ok"] is False
    assert response.get_json()["error"]["code"] == "UNAVAILABLE"


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


def test_the_board_actions_are_refused_without_a_shell_that_has_them(
        monkeypatch):
    from src.AutonaWebServer import WebServer
    monkeypatch.setattr(WebServer, "actions", None)

    with pytest.raises(NotAvailable):
        WebServer._board_actions()
