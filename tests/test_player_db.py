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
  worker as well (the adapter queues to the GUI thread and the core does
  not hold its lock meanwhile; since ADR-005 S3f-2 the worker does not wait
  for the GUI thread);
- the mains normalisation (PlayerDB.NormaliseMains, S3c-2 decision 5): the
  desktop's Qt model build did it in place until the Qt model clean-up's
  PR3 moved it to core. NORMALISE_CASES and the refresh tests were green
  against the model build (0f42a3b2) before the move, and are against core
  after it, through the desktop's refresh (AutonaPlayerDB.SetupModel) and
  core alone; the outputs it changes (/playerdb, its push,
  local_players.json) match what the model build left
  (normalised_golden.json, recorded from it), on the headless shell too
  (tests/test_headless.py).
"""
import ast
import copy
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
# What the desktop's Qt model build (0f42a3b2) left of a player DB in the
# outputs (the push's JSON, local_players.json) once it normalised it.
NORMALISED_GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "player_db"
     / "normalised_golden.json").read_text(encoding="utf-8"))

# The mains normalisation (S3c-2 decision 5): the selected game's mains,
# rewritten in place -- the skin a number (0 when it is not one, logged), a
# variant slot "" added. Quirks kept: the filter reads
# `isinstance(main, list) and len(main) == 2 or len(main) == 3` (so any
# 3-long thing is taken, and a main with no len() fails the player), a list
# that is not 2 or 3 long is left as it is (not removed), and one player's
# error stops that player only.
NORMALISED_GAME = "tgame"
INVALID_SKIN = "Local DB error: Player"
PLAYER_ERROR = "Error loading player from local DB:"
# {case: (the selected game's stored mains, after, "invalid skin" errors,
#  whether the player's walk ends on an error)}. None for the last: the
# Qt model build logged one there after the normalisation (its icon lookup
# on an empty list), not part of it.
NORMALISE_CASES = {
    "skin-number": ([["mario", 2]], [["mario", 2, ""]], 0, False),
    "skin-string": ([["mario", "2"]], [["mario", 2, ""]], 0, False),
    "with-variant": ([["mario", "2", "EX"]], [["mario", 2, "EX"]], 0, False),
    "variant-kept": ([["mario", 1, None]], [["mario", 1, None]], 0, False),
    "skin-word": ([["mario", "x"]], [["mario", 0, ""]], 1, False),
    "skin-null": ([["mario", None, "EX"]], [["mario", 0, "EX"]], 1, False),
    "skin-empty": ([["mario", ""]], [["mario", 0, ""]], 1, False),
    "skin-decimal-string": ([["mario", "2.5"]], [["mario", 0, ""]], 1, False),
    "skin-float": ([["mario", 2.7]], [["mario", 2, ""]], 0, False),
    "skin-bool": ([["mario", True]], [["mario", 1, ""]], 0, False),
    "skin-spaced": ([["mario", " 3 "]], [["mario", 3, ""]], 0, False),
    "skin-list": ([["mario", [1]]], [["mario", 0, ""]], 1, False),
    "several": ([["mario", "1"], ["luigi", "x", "V"]],
                [["mario", 1, ""], ["luigi", 0, "V"]], 1, False),
    "one-long-kept": ([["mario"]], [["mario"]], 0, None),
    "four-long-kept": ([["mario", "1", "EX", "y"]],
                       [["mario", "1", "EX", "y"]], 0, None),
    "mixed-lengths": ([["mario"], ["luigi", "3"], ["peach", "a", "V"]],
                      [["mario"], ["luigi", 3, ""], ["peach", 0, "V"]],
                      1, False),
    "empty": ([], [], 0, False),
    "null": (None, None, 0, False),
    # The precedence: a 3-long string is taken, its skin read ("b": not a
    # number), and the write into it fails the player.
    "string-main": (["abc"], ["abc"], 1, True),
    "string-main-digit": (["a5c"], ["a5c"], 0, True),
    # ...a 3-key dict too: its skin (key 1) is no number, and the error's
    # own message (key 0) fails the player, before anything is written.
    "dict-main": ([{"a": 1, "b": 2, "c": 3}], [{"a": 1, "b": 2, "c": 3}],
                  0, True),
    # With keys 0 and 1: the skin's write replaces key 1, the variant's
    # read (key 2) fails the player.
    "dict-main-indexed": ([{0: "mario", 1: "x", "c": 3}],
                          [{0: "mario", 1: 0, "c": 3}], 1, True),
    # A main with no len() fails the filter, before anything is rewritten.
    "number-main": ([["mario", "2"], 5], [["mario", "2"], 5], 0, True),
    "null-main": ([["mario", "2"], None], [["mario", "2"], None], 0, True),
    # What came before the failing main stays rewritten, what follows not.
    "partial": ([["mario", "2"], "abc", ["luigi", "3"]],
                [["mario", 2, ""], "abc", ["luigi", "3"]], 1, True),
    # Not a list: a string's characters (none 3 long), a dict's keys.
    "string-mains": ("abc", "abc", 0, None),
    "dict-mains": ({"abc": 1}, {"abc": 1}, 1, True),
    "number-mains": (5, 5, 0, True),
}


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
    # The Qt adapter's model build on the GUI thread takes this lock. Until
    # ADR-005 S3f-2 its subscriber also waited for that build, so announcing
    # under the lock deadlocked a worker; a subscriber may still take it.
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


# -- the mains normalisation (core) ------------------------------------------


@pytest.fixture
def errors():
    from loguru import logger

    messages = []
    handler = logger.add(lambda message: messages.append(str(message)),
                         level="ERROR", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(handler)


def _put(db, tag, record):
    # Straight into the dict: AddPlayers would rewrite a one-element main.
    with db.lock:
        db.database[tag] = record
    return record


def _logged(messages, prefix, tag=None):
    lines = [m.rstrip("\n") for m in messages]
    return [m for m in lines if m.startswith(prefix)
            and (tag is None or m == f"{prefix} {tag}")]


@pytest.mark.parametrize("case", sorted(NORMALISE_CASES))
def test_normalise_mains_rewrites_the_games_mains(case, tmp_path, errors):
    """NORMALISE_CASES on core alone: what the model build did, with no Qt
    (the headless shell's refresh). Where the model build logged an error
    after the normalisation (None), core logs none."""
    stored, after, invalidSkins, stops = NORMALISE_CASES[case]
    db = _db(tmp_path)
    record = _put(db, "K", {"gamerTag": "K", "mains": {
        NORMALISED_GAME: copy.deepcopy(stored), "other": [["luigi", "2"]]}})

    db.NormaliseMains(NORMALISED_GAME)

    assert record["mains"] == {NORMALISED_GAME: after,
                               "other": [["luigi", "2"]]}
    assert len(_logged(errors, INVALID_SKIN)) == invalidSkins
    assert bool(_logged(errors, PLAYER_ERROR, "K")) is bool(stops)


def test_normalise_mains_is_idempotent_and_quiet(tmp_path, settings,
                                                 errors):
    """Run again, it rewrites nothing more, logs nothing more; it never
    saves nor announces (the caller's db_updated does the telling)."""
    db = _db(tmp_path)
    db.LoadDB()
    record = _put(db, "K", {"gamerTag": "K", "mains": {NORMALISED_GAME: [
        ["mario", "x"], ["luigi", "2", "V"]]}})
    db.SaveDB()
    before = Path(db.path).read_bytes()
    announced = []
    db.changed.subscribe(lambda: announced.append(1))

    db.NormaliseMains(NORMALISED_GAME)
    once = copy.deepcopy(record)
    logged = len(errors)
    db.NormaliseMains(NORMALISED_GAME)

    assert record == once == {"gamerTag": "K", "mains": {NORMALISED_GAME: [
        ["mario", 0, ""], ["luigi", 2, "V"]]}}
    assert len(errors) == logged == 2  # the invalid skin, its traceback
    assert Path(db.path).read_bytes() == before
    assert announced == []


def test_normalise_mains_holds_the_lock_for_the_walk(tmp_path):
    """The entrants worker merges into the same dicts under `lock`: the
    walk takes it (re-entrant: a caller already holding it goes on)."""
    db = _db(tmp_path)
    _put(db, "K", {"gamerTag": "K", "mains": {NORMALISED_GAME: [
        ["mario", "1"]]}})
    walked = threading.Event()

    def other():
        db.NormaliseMains(NORMALISED_GAME)
        walked.set()

    with db.lock:
        thread = threading.Thread(target=other, daemon=True)
        thread.start()
        assert not walked.wait(0.2)
        db.NormaliseMains(NORMALISED_GAME)  # re-entrant
    assert walked.wait(10)
    thread.join(10)
    assert db.Get("K")["mains"][NORMALISED_GAME] == [["mario", 1, ""]]


# -- the Qt adapter --------------------------------------------------------


@pytest.fixture
def adapter(autonaApp, tmp_path):
    """A fresh core DB (in tmp) with the adapter attached to it, and a
    counter on db_updated. Afterwards the session's DB is attached again."""
    from src.AutonaPlayerDB import AutonaPlayerDB

    session_db = PlayerDB.instance
    assert AutonaPlayerDB._attachedTo is session_db

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
        # (The adapter's models, put back here until the Qt model clean-up's
        # PR3 removed the last one, are gone.)


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
    assert len(emitted) == 4  # the fetch announces its own save
    AutonaPlayerDB.ClearTournamentPlayers()
    assert len(emitted) == 5
    AutonaPlayerDB.ClearTournamentPlayers()  # no entrants to forget
    assert len(emitted) == 5
    assert all(emitted)  # on the GUI thread
    # What the push sends (the player model held the same rows until the Qt
    # model clean-up's PR3).
    assert [tag for tag, _ in db.Items()] == ["Bravo"]


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
    # The GUI thread is not pumped here: the worker queues its GUI-thread
    # work and goes on (ADR-005 S3f-2). It used to wait for the GUI thread,
    # which kept a task executor job -- and the interpreter's exit, which
    # joins it -- hanging once the event loop had stopped.
    thread.join(10)
    assert not thread.is_alive(), "the worker waits for the GUI thread"
    assert emitted == []

    deadline = time.monotonic() + 10
    while not emitted and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    QCoreApplication.processEvents()

    assert emitted == [True]
    assert db.tournamentTags == {"E1", "E2"}
    # What the web server's player DB push sends (the completers' proxy
    # filtered the same way until ADR-005 S4ad).
    assert sorted(db.TournamentPlayers()) == ["E1", "E2"]
    AutonaPlayerDB.ClearTournamentPlayers()
    assert len(db.TournamentPlayers()) == 20


def test_unloading_the_tournament_pushes_the_whole_db_again(adapter, settings):
    """The web pages' name candidates are the player DB push, which sends
    TournamentPlayers() on every db_updated (bootstrap_gui wires it to
    ws_playerdb). Forgetting the entrants changes what that sends, so it
    is announced: without it the pages kept the previous tournament's
    entrants until the next DB change (ADR-005 S4b review)."""
    from src.AutonaWebOperations import get_playerdb

    AutonaPlayerDB, db, emitted = adapter
    db.LoadDB()
    db.AddPlayers([_player("Alpha"), _player("Bravo")])
    AutonaPlayerDB.SetTournamentPlayers(["Bravo"])
    assert sorted(get_playerdb()) == ["Bravo"]

    pushed = []

    def push():
        pushed.append(sorted(get_playerdb()))

    AutonaPlayerDB.signals.db_updated.connect(push)
    try:
        AutonaPlayerDB.ClearTournamentPlayers()
    finally:
        AutonaPlayerDB.signals.db_updated.disconnect(push)

    assert pushed == [["Alpha", "Bravo"]]


def _selectGame(monkeypatch, codename):
    """The catalog's selected game, as a refresh reads it (the asset
    adapter's `selectedGame` is the same snapshot's `game`)."""
    from dataclasses import replace

    from src.core.GameAssetCatalog import GameAssetCatalog

    catalog = GameAssetCatalog.instance
    snapshot = replace(catalog.Current(),
                       game={"codename": codename} if codename else {},
                       codename=codename)
    monkeypatch.setattr(catalog, "Current", lambda: snapshot)


@pytest.mark.parametrize("case", sorted(NORMALISE_CASES))
def test_a_refresh_normalises_the_selected_games_mains(
        case, adapter, settings, monkeypatch, errors):
    """The desktop's refresh (AutonaPlayerDB.SetupModel: every DB change
    and every game load) rewrites the selected game's mains in place;
    another game's are left as stored."""
    stored, after, invalidSkins, stops = NORMALISE_CASES[case]
    AutonaPlayerDB, db, emitted = adapter
    _selectGame(monkeypatch, NORMALISED_GAME)
    record = _put(db, "K", {"gamerTag": "K", "mains": {
        NORMALISED_GAME: copy.deepcopy(stored), "other": [["luigi", "2"]]}})

    AutonaPlayerDB.SetupModel()

    assert record["mains"] == {NORMALISED_GAME: after,
                               "other": [["luigi", "2"]]}
    assert len(_logged(errors, INVALID_SKIN)) == invalidSkins
    if stops is not None:
        assert bool(_logged(errors, PLAYER_ERROR, "K")) is stops
    assert emitted == [True]


def test_a_refresh_names_the_invalid_skin(adapter, settings, monkeypatch,
                                          errors):
    AutonaPlayerDB, db, emitted = adapter
    _selectGame(monkeypatch, NORMALISED_GAME)
    _put(db, "SPN K", {"prefix": "SPN", "gamerTag": "K",
                       "mains": {NORMALISED_GAME: [["mario", "x"]]}})

    AutonaPlayerDB.SetupModel()

    assert _logged(errors, INVALID_SKIN) == [
        "Local DB error: Player K has an invalid skin for character mario"]
    # Each error with its traceback (ValueError: int("x")).
    assert any("ValueError" in m for m in errors)


def test_a_refresh_goes_on_with_the_next_player(adapter, settings,
                                                monkeypatch, errors):
    """One player's error stops that player only (logged with the
    gamerTag); what was rewritten before it stays rewritten."""
    AutonaPlayerDB, db, emitted = adapter
    _selectGame(monkeypatch, NORMALISED_GAME)
    first = _put(db, "A", {"gamerTag": "A", "mains": {NORMALISED_GAME: [
        ["mario", "2"], "abc", ["luigi", "3"]]}})
    second = _put(db, "B", {"gamerTag": "B", "mains": {NORMALISED_GAME: [
        ["mario", "4"]]}})

    AutonaPlayerDB.SetupModel()

    assert first["mains"][NORMALISED_GAME] == [
        ["mario", 2, ""], "abc", ["luigi", "3"]]
    assert second["mains"][NORMALISED_GAME] == [["mario", 4, ""]]
    assert _logged(errors, PLAYER_ERROR) == [f"{PLAYER_ERROR} A"]
    assert emitted == [True]


@pytest.mark.parametrize("codename", [NORMALISED_GAME, None])
def test_a_refresh_leaves_what_is_not_the_selected_games_list(
        codename, adapter, settings, monkeypatch, errors):
    """Only a per-game dict (exactly a dict) holding the selected game is
    touched: not a stored JSON string, an empty dict, a record without
    mains, nor anything while no game is selected."""
    AutonaPlayerDB, db, emitted = adapter
    _selectGame(monkeypatch, codename)
    records = {
        "string": {"gamerTag": "S", "mains": json.dumps(
            {NORMALISED_GAME: [["mario", "2"]]})},
        "empty": {"gamerTag": "E", "mains": {}},
        "missing": {"gamerTag": "M"},
        "absent": {"gamerTag": "O", "mains": {"other": [["mario", "2"]]}},
        "falsy": {"gamerTag": "F", "mains": None},
    }
    if codename is None:
        records["unselected"] = {"gamerTag": "U", "mains": {
            NORMALISED_GAME: [["mario", "2"]]}}
    for tag, record in records.items():
        _put(db, tag, record)
    stored = copy.deepcopy(records)

    AutonaPlayerDB.SetupModel()

    assert records == stored
    assert errors == []
    assert emitted == [True]


def test_a_refresh_neither_saves_nor_announces(adapter, settings,
                                               monkeypatch):
    """The rewrite is in memory: the next save writes it out (as the model
    build's always was), and it is no change announcement -- db_updated
    still fires once per refresh, rewritten or not."""
    AutonaPlayerDB, db, emitted = adapter
    db.LoadDB()
    _put(db, "K", {"gamerTag": "K", "mains": {NORMALISED_GAME: [
        ["mario", "2"]]}})
    db.SaveDB()
    before = Path(db.path).read_bytes()
    announced = []
    db.changed.subscribe(lambda: announced.append(1))
    emitted.clear()
    _selectGame(monkeypatch, NORMALISED_GAME)

    AutonaPlayerDB.SetupModel()
    AutonaPlayerDB.SetupModel()  # nothing left to rewrite

    assert Path(db.path).read_bytes() == before
    assert announced == []
    assert emitted == [True, True]
    db.SaveDB()
    assert json.loads(Path(db.path).read_text(encoding="utf-8"))[0][
        "mains"] == {NORMALISED_GAME: [["mario", 2, ""]]}


def test_a_refresh_keeps_the_outputs_shape(adapter, settings, monkeypatch):
    """What a refresh rewrites reaches /playerdb, its Socket.IO push and
    local_players.json exactly as the desktop's model build left them, key
    order included (NORMALISED_GOLDEN, recorded from it). The headless
    shell is held to the same file (tests/test_headless.py)."""
    from src.AutonaWebOperations import get_playerdb

    AutonaPlayerDB, db, emitted = adapter
    Path(db.path).write_text(NORMALISED_GOLDEN["local_players"],
                             encoding="utf-8")
    _selectGame(monkeypatch, NORMALISED_GOLDEN["codename"])

    db.LoadDB()  # its announcement is the refresh
    db.SaveDB()

    assert json.dumps(get_playerdb()) == NORMALISED_GOLDEN["after"]["playerdb"]
    assert Path(db.path).read_text(encoding="utf-8") \
        == NORMALISED_GOLDEN["after"]["local_players"]


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
    # The push must send the new entrants from the announcement on, so the
    # tags go in between the save and the announcement (the announcement is
    # what fires db_updated; it rebuilt the completer proxy too, until
    # ADR-005 S4ad).
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

