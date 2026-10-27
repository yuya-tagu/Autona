"""The core player DB keeps the old one's data, file and notifications.

ADR-005 S3e-1 moved the data half of src/AutonaPlayerDB.py (the players by
tag, LoadDB / AddPlayers / DeletePlayer / GetPlayerFromTag / SaveDB, the
tournament's entrant tags) into src/core/PlayerDB.py. Pinned here:

- the same operations leave the same database in memory (key order
  included), the same bytes in user_data/local_players.json and the same
  /playerdb payload as the old code did (tests/fixtures/player_db/golden.json,
  recorded from it -- see record_golden.py there);
- one change announcement where the old code rebuilt the model (LoadDB,
  AddPlayers unless deferred, DeletePlayer, NotifyChanged), to every
  subscriber, made outside the lock;
- the save writes outside the DB lock, in snapshot order, through a temp
  file that never stays behind; until the replace succeeds the database is
  untouched, and when the replace keeps failing it overwrites in place (the
  old way) instead of not saving; LoadDB clears temp files a crash left;
- readers can run while a worker adds players, and what goes out to the web
  (TournamentPlayers) is a deep copy;
- the start.gg entrants fetch ends with SaveDB -> SetTournamentPlayers ->
  NotifyChanged, in that order (read from the provider's source);
- through the Qt adapter, db_updated fires once per announcement, from a
  worker as well (the adapter marshals to the GUI thread and the core does
  not hold its lock meanwhile), and the model build still normalises the
  loaded game's mains in place.
"""
import ast
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.core import PlayerDB as player_db_module
from src.core.PlayerDB import PlayerDB
from src.core.SettingsManager import SettingsManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "player_db" / "golden.json")
    .read_text(encoding="utf-8"))


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(SettingsManager, "settings", {})


def _file_bytes(text):
    # The old writer opened the file in text mode: "\n" became os.linesep.
    return text.replace("\n", os.linesep).encode("utf-8")


def _player(tag, **extra):
    return dict({"gamerTag": tag}, **extra)


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize(
    "scenario", GOLDEN["scenarios"], ids=lambda s: s["name"])
def test_operations_leave_what_the_old_player_db_left(
        scenario, tmp_path, monkeypatch, settings):
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    for name, text in scenario["files"].items():
        (user_data / name).write_bytes(text.encode("utf-8"))
    # The default paths, resolved against the CWD as the old ones were.
    monkeypatch.chdir(tmp_path)
    db = PlayerDB()

    steps = json.loads(json.dumps(scenario["steps"]))
    for number, (step, want) in enumerate(zip(steps, scenario["after"])):
        where = f"{scenario['name']} step {number} ({step['op']})"
        result = None
        if step["op"] == "load":
            db.LoadDB()
        elif step["op"] == "save":
            db.SaveDB()
        elif step["op"] == "add":
            monkeypatch.setattr(SettingsManager, "settings", {"general": {
                "disable_overwrite": step.get("disable_overwrite", False)}})
            db.AddPlayers(step["players"], overwrite=step.get("overwrite", False),
                          deferRefresh=step.get("deferRefresh", False))
            monkeypatch.setattr(SettingsManager, "settings", {})
        elif step["op"] == "delete":
            db.DeletePlayer(step["tag"])
        elif step["op"] == "get":
            result = {"value": db.GetPlayerFromTag(step["tag"])}
        else:
            raise ValueError(step["op"])

        path = user_data / "local_players.json"
        if want["file"] is None:
            assert not path.exists(), where
        else:
            assert path.read_bytes() == _file_bytes(want["file"]), where
        assert (user_data / "local_players.csv").exists() == want["csv_exists"], where
        assert json.loads(json.dumps(db.database)) == want["database"], where
        # Unsorted dumps: the players' order and each player's key order too.
        assert _dumps(db.database) == _dumps(want["database"]), where
        # What /playerdb and its Socket.IO push serialise (no tournament
        # loaded: the whole DB), in the same order.
        assert _dumps(db.TournamentPlayers()) == _dumps(want["database"]), where
        if "result" in want:
            assert json.loads(json.dumps(result)) == want["result"], where
            assert _dumps(result) == _dumps(want["result"]), where
    assert not list(user_data.glob("local_players.*.tmp"))


# -- change announcements ---------------------------------------------------


def _db(tmp_path):
    db = PlayerDB(path=str(tmp_path / "local_players.json"),
                  legacyCsvPath=str(tmp_path / "local_players.csv"))
    return db


def test_each_change_is_announced_once_to_every_subscriber(tmp_path, settings):
    db = _db(tmp_path)
    first, second = [], []
    db.changed.subscribe(lambda: first.append(1))
    db.changed.subscribe(lambda: second.append(1))

    def announced(action):
        before = len(first)
        action()
        assert len(second) == len(first)
        return len(first) - before

    assert announced(db.LoadDB) == 1
    assert announced(lambda: db.AddPlayers([_player("A")])) == 1
    assert announced(lambda: db.AddPlayers([_player("B")], overwrite=True)) == 1
    assert announced(lambda: db.AddPlayers([_player("C")], deferRefresh=True)) == 0
    assert announced(db.SaveDB) == 0
    assert announced(db.NotifyChanged) == 1
    assert announced(lambda: db.DeletePlayer("A")) == 1
    assert announced(lambda: db.DeletePlayer("nobody")) == 1  # saves too, as before
    assert announced(lambda: db.SetTournamentTags(["B"])) == 0
    assert announced(db.ClearTournamentTags) == 0
    assert announced(lambda: db.GetPlayerFromTag("b")) == 0


def test_a_failed_load_is_not_announced(tmp_path, settings):
    db = _db(tmp_path)
    (tmp_path / "local_players.json").write_text("not json", encoding="utf-8")
    seen = []
    db.changed.subscribe(lambda: seen.append(1))
    db.LoadDB()  # logged, as before
    assert seen == []
    assert db.database == {}


def test_announcements_are_made_outside_the_lock(tmp_path, settings):
    # The Qt adapter's subscriber waits for the GUI thread, whose model build
    # takes this lock: announcing under it would deadlock against a worker.
    db = _db(tmp_path)
    acquired = []

    def from_another_thread():
        def take():
            got = db.lock.acquire(timeout=2)
            if got:
                db.lock.release()
            acquired.append(got)
        thread = threading.Thread(target=take)
        thread.start()
        thread.join()

    db.changed.subscribe(from_another_thread)
    db.LoadDB()
    db.AddPlayers([_player("A")])
    db.NotifyChanged()
    db.DeletePlayer("A")
    assert acquired == [True, True, True, True]


def test_a_failing_subscriber_neither_breaks_the_change_nor_the_others(
        tmp_path, settings):
    db = _db(tmp_path)
    seen = []

    def broken():
        raise RuntimeError("subscriber failure")

    db.changed.subscribe(broken)
    db.changed.subscribe(lambda: seen.append(1))
    db.LoadDB()
    db.AddPlayers([_player("A")])
    assert seen == [1, 1]
    assert db.Contains("A")
    assert "A" in (tmp_path / "local_players.json").read_text(encoding="utf-8")


# -- saving ----------------------------------------------------------------


def _saved_tags(tmp_path):
    saved = json.loads((tmp_path / "local_players.json").read_text(encoding="utf-8"))
    return [player["gamerTag"] for player in saved]


def _temps(tmp_path):
    return sorted(path.name for path in tmp_path.glob("local_players.*.tmp"))


def test_until_the_replace_succeeds_the_database_is_untouched(
        tmp_path, monkeypatch, settings):
    # A process dying between the temp write and the replace is simulated by
    # a replace that raises something SaveDB does not fall back on.
    db = _db(tmp_path)
    db.LoadDB()
    db.AddPlayers([_player("A", name="kept")])
    path = tmp_path / "local_players.json"
    before = path.read_bytes()

    def die(src, dst):
        assert '"B"' in Path(src).read_text(encoding="utf-8")
        raise RuntimeError("the process died here")

    monkeypatch.setattr(player_db_module.os, "replace", die)
    db.AddPlayers([_player("B")])  # logged, never raised (as before)

    assert path.read_bytes() == before
    assert db.Contains("B")  # the change itself is kept in memory
    assert _temps(tmp_path) == []


def test_a_replace_that_keeps_failing_falls_back_to_overwriting(
        tmp_path, monkeypatch, settings):
    # Where the old in-place overwrite worked, saving must still work.
    db = _db(tmp_path)
    db.LoadDB()
    db.AddPlayers([_player("A")])
    attempts = []

    def refuse(src, dst):
        attempts.append((src, dst))
        raise PermissionError("sharing violation")

    sleeps = []
    monkeypatch.setattr(player_db_module.os, "replace", refuse)
    monkeypatch.setattr(player_db_module.time, "sleep", sleeps.append)
    db.AddPlayers([_player("B")])

    # Tried 4 times (retried 3 times, 20 / 40 / 80 ms apart), then overwritten.
    assert len(attempts) == player_db_module._REPLACE_ATTEMPTS == 4
    assert sleeps == [0.02, 0.04, 0.08]
    assert _saved_tags(tmp_path) == ["A", "B"]
    assert _temps(tmp_path) == []


def test_no_temp_file_at_all_falls_back_to_overwriting(
        tmp_path, monkeypatch, settings):
    db = _db(tmp_path)
    db.LoadDB()

    def no_temp(*args, **kwargs):
        raise PermissionError("cannot create files here")

    monkeypatch.setattr(player_db_module.tempfile, "mkstemp", no_temp)
    db.AddPlayers([_player("A")])

    assert _saved_tags(tmp_path) == ["A"]


def test_a_transient_replace_failure_is_retried(tmp_path, monkeypatch, settings):
    db = _db(tmp_path)
    db.LoadDB()
    real_replace = os.replace
    failures = [PermissionError("sharing violation")]

    def flaky(src, dst):
        if failures:
            raise failures.pop()
        return real_replace(src, dst)

    monkeypatch.setattr(player_db_module.os, "replace", flaky)
    monkeypatch.setattr(player_db_module.time, "sleep", lambda seconds: None)
    db.AddPlayers([_player("A")])

    assert _saved_tags(tmp_path) == ["A"]
    assert _temps(tmp_path) == []


def test_the_file_is_written_outside_the_db_lock(tmp_path, monkeypatch, settings):
    db = _db(tmp_path)
    db.LoadDB()
    real_replace = os.replace
    acquired = []

    def replace(src, dst):
        def take():
            got = db.lock.acquire(timeout=2)
            if got:
                db.lock.release()
            acquired.append(got)
        thread = threading.Thread(target=take)
        thread.start()
        thread.join()
        return real_replace(src, dst)

    monkeypatch.setattr(player_db_module.os, "replace", replace)
    db.AddPlayers([_player("A")])
    db.DeletePlayer("A")
    db.SaveDB()
    assert acquired == [True, True, True]


def test_an_older_snapshot_never_lands_after_a_newer_one(tmp_path, settings):
    db = _db(tmp_path)
    db.LoadDB()
    db.AddPlayers([_player("A")])
    with db.lock:
        old = db._Snapshot()  # a save that lost the race to the next one
    db.AddPlayers([_player("B")])

    db._Persist(*old)
    assert _saved_tags(tmp_path) == ["A", "B"]


def test_loading_removes_the_temp_files_a_crash_left(tmp_path, settings):
    (tmp_path / "local_players.json").write_text("[]", encoding="utf-8")
    (tmp_path / "local_players.abc123.tmp").write_text("[", encoding="utf-8")
    (tmp_path / "local_players.zz.tmp").write_text("", encoding="utf-8")
    (tmp_path / "other.tmp").write_text("not ours", encoding="utf-8")
    (tmp_path / "local_players.csv.bak").write_text("not ours", encoding="utf-8")

    _db(tmp_path).LoadDB()

    assert _temps(tmp_path) == []
    assert (tmp_path / "other.tmp").exists()
    assert (tmp_path / "local_players.csv.bak").exists()
    assert (tmp_path / "local_players.json").read_text(encoding="utf-8") == "[]"


# -- readers ---------------------------------------------------------------


def test_readers_and_the_tournament_filter(tmp_path, settings):
    db = _db(tmp_path)
    db.LoadDB()
    db.AddPlayers([_player("Alpha", prefix="SPN"), _player("Bravo"),
                   _player("Charlie")])

    assert db.Get("SPN Alpha")["gamerTag"] == "Alpha"
    assert db.Get("Alpha") is None
    assert db.Get("Alpha", "missing") == "missing"
    assert db.Contains("Bravo") and not db.Contains("bravo")
    assert [tag for tag, _ in db.Items()] == ["SPN Alpha", "Bravo", "Charlie"]
    assert db.GetPlayerFromTag("ALPHA") is db.Get("SPN Alpha")
    assert db.GetPlayerFromTag("zulu") is None

    everyone = db.TournamentPlayers()
    assert list(everyone) == ["SPN Alpha", "Bravo", "Charlie"]
    everyone.pop("Bravo")  # a new dict: the DB is not touched
    assert db.Contains("Bravo")

    db.SetTournamentTags(["Bravo", "", None, "SPN Alpha", "nobody"])
    assert db.tournamentTags == {"Bravo", "SPN Alpha", "nobody"}
    assert list(db.TournamentPlayers()) == ["SPN Alpha", "Bravo"]
    assert db.ClearTournamentTags() is True
    assert db.ClearTournamentTags() is False
    assert list(db.TournamentPlayers()) == ["SPN Alpha", "Bravo", "Charlie"]


def test_what_goes_to_the_web_is_a_deep_copy_in_the_same_order(
        tmp_path, settings):
    # /playerdb and its Socket.IO push serialise this after the lock is
    # released, while the entrants worker (and the model build) change
    # player dicts and their mains lists in place.
    db = _db(tmp_path)
    db.LoadDB()
    db.AddPlayers([
        _player("Alpha", prefix="SPN", name="A", mains={"ssbu": [["mario", 2]]}),
        _player("Bravo", twitter="b", seed=3, id=[1, 2])])

    out = db.TournamentPlayers()
    assert _dumps(out) == _dumps(db.database)
    out["SPN Alpha"]["name"] = "changed"
    out["SPN Alpha"]["mains"]["ssbu"][0].append("variant")
    out["Bravo"]["id"].append(3)
    assert db.Get("SPN Alpha")["name"] == "A"
    assert db.Get("SPN Alpha")["mains"] == {"ssbu": [["mario", 2]]}
    assert db.Get("Bravo")["id"] == [1, 2]

    db.SetTournamentTags(["Bravo"])
    filtered = db.TournamentPlayers()
    assert _dumps(filtered) == _dumps({"Bravo": db.Get("Bravo")})
    assert filtered["Bravo"] is not db.Get("Bravo")


def test_readers_can_run_while_a_worker_adds_players(tmp_path, settings):
    # The start.gg entrants worker adds page after page while the GUI thread
    # and the web server read. Unguarded, iterating the dict raised
    # "dictionary changed size during iteration".
    db = _db(tmp_path)
    db.LoadDB()
    stop = threading.Event()
    errors = []

    def worker():
        try:
            for page in range(40):
                db.AddPlayers([_player(f"P{page}-{i}") for i in range(50)],
                              deferRefresh=True)
                # Merges into an existing dict in place (mains included).
                db.AddPlayers([_player("P0-0", mains={"g": [[f"c{page}"]]})],
                              deferRefresh=True)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
        finally:
            stop.set()

    thread = threading.Thread(target=worker)
    thread.start()
    reads = 0
    try:
        while not stop.is_set() or reads == 0:
            db.Items()
            json.dumps(db.TournamentPlayers())  # what /playerdb does
            db.GetPlayerFromTag("nobody")
            reads += 1
    except Exception as e:
        errors.append(e)
    thread.join()

    assert not errors
    assert len(db.Items()) == 40 * 50


# -- the Qt adapter --------------------------------------------------------


@pytest.fixture
def adapter(autonaApp, tmp_path):
    """A fresh core DB (in tmp) with the adapter attached to it, and a
    counter on db_updated. Afterwards the session's DB is attached again and
    the adapter's models are put back as they were."""
    from src.AutonaPlayerDB import AutonaPlayerDB

    session_db = PlayerDB.instance
    saved = {name: getattr(AutonaPlayerDB, name)
             for name in ("model", "completerModel", "_retiredModel")}
    assert AutonaPlayerDB._attachedTo is session_db
    assert AutonaPlayerDB.modelEnabled  # the desktop harness built the model

    db = _db(tmp_path)
    PlayerDB.instance = db
    AutonaPlayerDB.Attach()

    emitted = []

    def count():
        emitted.append(threading.current_thread() is threading.main_thread())

    AutonaPlayerDB.signals.db_updated.connect(count)
    try:
        yield AutonaPlayerDB, db, emitted
    finally:
        AutonaPlayerDB.signals.db_updated.disconnect(count)
        PlayerDB.instance = session_db
        AutonaPlayerDB.Attach()
        # The test's builds re-pointed the (shared) completer proxy at the
        # test's model. Point it back at the session's model before dropping
        # the test's (agent-pitfalls #13: rewire first, then let go).
        proxy, model = saved["completerModel"], saved["model"]
        if proxy is not None and model is not None:
            proxy.setSourceModel(model)
        for name, value in saved.items():
            setattr(AutonaPlayerDB, name, value)


def test_db_updated_fires_once_per_change(adapter, settings):
    AutonaPlayerDB, db, emitted = adapter

    db.LoadDB()
    assert len(emitted) == 1
    db.AddPlayers([_player("Alpha")])
    assert len(emitted) == 2
    db.AddPlayers([_player("Bravo")], deferRefresh=True)
    db.SaveDB()
    assert len(emitted) == 2
    db.NotifyChanged()
    assert len(emitted) == 3
    db.DeletePlayer("Alpha")
    assert len(emitted) == 4
    AutonaPlayerDB.SetTournamentPlayers(["Bravo"])
    AutonaPlayerDB.ClearTournamentPlayers()
    assert len(emitted) == 4
    assert all(emitted)  # on the GUI thread

    tags = [AutonaPlayerDB.model.item(row).text()
            for row in range(AutonaPlayerDB.model.rowCount())]
    assert tags == ["Bravo"]


def test_a_worker_change_reaches_db_updated_on_the_gui_thread(
        adapter, settings):
    from qtpy.QtCore import QCoreApplication

    AutonaPlayerDB, db, emitted = adapter
    db.LoadDB()
    emitted.clear()

    def entrants_fetch():
        # What StartGGDataProvider does at the end of a fetch.
        db.AddPlayers([_player(f"E{i}") for i in range(20)], deferRefresh=True)
        db.SaveDB()
        AutonaPlayerDB.SetTournamentPlayers(["E1", "E2"])
        db.NotifyChanged()

    thread = threading.Thread(target=entrants_fetch, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while thread.is_alive() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        thread.join(0.01)
    assert not thread.is_alive(), "the worker is stuck (lock held across the hop?)"

    assert emitted == [True]
    assert db.tournamentTags == {"E1", "E2"}
    proxy = AutonaPlayerDB.GetCompleterModel()
    assert sorted(proxy.data(proxy.index(row, 0))
                  for row in range(proxy.rowCount())) == ["E1", "E2"]
    AutonaPlayerDB.ClearTournamentPlayers()
    assert proxy.rowCount() == 20


def test_the_model_build_still_normalises_the_loaded_games_mains(
        adapter, settings, monkeypatch):
    # S3c-2 decision 5: the adapter's model build rewrites the selected
    # game's mains in place (skin to int, a variant slot added). It stays in
    # the adapter for now; this pins that the move did not lose it.
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    AutonaPlayerDB, db, emitted = adapter
    manager = AutonaGameAssetManager.instance
    monkeypatch.setattr(type(manager), "selectedGame",
                        property(lambda self: {"codename": "testgame"}),
                        raising=False)
    db.LoadDB()
    db.AddPlayers([_player("Alpha", mains={"testgame": [["mario", "2"]],
                                            "other": [["luigi", "x"]]})])

    mains = db.Get("Alpha")["mains"]
    assert mains["testgame"] == [["mario", 2, ""]]
    assert mains["other"] == [["luigi", "x"]]  # another game: untouched


def test_attach_moves_the_relay_to_a_replaced_instance(adapter, settings):
    AutonaPlayerDB, db, emitted = adapter
    assert db.changed.subscriber_count() == 1

    replacement = _db(Path(db.path).parent / "replacement")
    Path(replacement.path).parent.mkdir()
    PlayerDB.instance = replacement
    try:
        AutonaPlayerDB.Attach()
        assert db.changed.subscriber_count() == 0
        assert replacement.changed.subscriber_count() == 1
        AutonaPlayerDB.Attach()  # idempotent
        assert replacement.changed.subscriber_count() == 1

        replacement.LoadDB()
        db.NotifyChanged()  # the old DB no longer drives db_updated
        assert len(emitted) == 1
    finally:
        PlayerDB.instance = db
        AutonaPlayerDB.Attach()
    assert replacement.changed.subscriber_count() == 0
    assert db.changed.subscriber_count() == 1


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_the_entrants_fetch_saves_filters_then_announces():
    # The rebuilt completer proxy must filter by the new entrants from its
    # first build, so the tags go in between the save and the announcement
    # (the announcement is what rebuilds the model and fires db_updated).
    source = (PROJECT_ROOT / "src" / "TournamentDataProvider"
              / "StartGGDataProvider.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    announcing = [
        function for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        and any(isinstance(node, ast.Call)
                and _dotted(node.func) == "PlayerDB.instance.NotifyChanged"
                for node in ast.walk(function))]
    assert [function.name for function in announcing] == ["GetEntrantsWorker"]

    calls = sorted(
        ((node.lineno, node.col_offset), _dotted(node.func), node)
        for node in ast.walk(announcing[0])
        if isinstance(node, ast.Call)
        and _dotted(node.func).startswith(("PlayerDB.instance.",
                                           "AutonaPlayerDB.")))
    names = [name for _, name, _ in calls]
    assert names == [
        "PlayerDB.instance.AddPlayers",
        "PlayerDB.instance.SaveDB",
        "AutonaPlayerDB.SetTournamentPlayers",
        "PlayerDB.instance.NotifyChanged",
    ]
    add = calls[0][2]
    assert [(k.arg, getattr(k.value, "value", None)) for k in add.keywords] \
        == [("deferRefresh", True)]

