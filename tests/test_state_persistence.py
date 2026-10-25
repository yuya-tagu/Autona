"""Pins for the persisted state store (ADR-005 S3b).

``user_data/state.json`` became the store of record: the published state
without program_state.json's ``timestamp`` / ``revision`` stamps, in a
``{"state_version": N, "state": {...}}`` envelope, written by a debounced
writer of its own. ``out/program_state.json`` went back to being only an
export (same place, shape, stamps and write order -- pinned by
tests/test_state_manager_save_cost.py, which this change leaves untouched).

Pinned here:

1. ``StateMigrations``: the v0 -> v1 step (the three ad-hoc migrations that
   predate it), the version contract, and that running it before
   ``SeedPanelState()``'s identical cleanup is harmless.
2. ``StatePersistence.Load``: every launch branch -- usable store, first
   launch after the update (migration from program_state.json), neither
   file, corrupt store, store from a newer build.
3. The writer: debounce, the maximum delay, Flush, the envelope, failures.
4. StateManager in-process: every published cycle reaches the store, held
   edits do not, ``general.disable_export`` does not stop it.
5. StateManager in a fresh interpreter: a real launch from a pre-S3b
   install, and the last state reaching the store when the process exits.

The store's path is process-global (StateManager.Initialize() fixes it), so
every test here that moves it puts it back; files go to throwaway
directories only.
"""
import copy
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

import src.core.StatePersistence as persistence
from src.core import CommentaryModel
from src.core import StateMigrations as migrations
from src.core import StateSchema as schema
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.StatePersistence import StatePersistence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ORIGINAL_CWD = os.getcwd()

MARKER = "<<<AUTONA_STATE_PROBE>>>"


@pytest.fixture(autouse=True)
def _restore_store():
    """Hand the store's process-wide path back as it was.

    Anything another module left pending is written first, to *its* path,
    so it cannot land in this test's directory.
    """
    assert StatePersistence.Flush(30)
    previous = StatePersistence._path
    yield
    assert StatePersistence.Flush(30)
    with StatePersistence._cond:
        StatePersistence._path = previous


def _legacy_v0():
    """What a pre-S3b build left in out/program_state.json."""
    return {
        "timestamp": 1761370000.5,
        "revision": 42,
        "score": {"1": {"team": {"1": {"score": 2}}}},
        "game": {"name": "Some Game"},
        "bracket": {"rounds": {}},
        "player_list": {"slot": {"1": {"name": "x"}}},
        "player_editor": {"1": {"name": "scratch"}},
        "tournamentInfo": {
            "tournamentName": "Kept",
            "streamTitleEdit": "old title",
            "streamUrl": "https://old.example/stream",
        },
        "commentary": {
            "1": {"name": "Caster", "custom_textbox": "legacy note",
                  "display": 3},
            "99": {"name": "out of range"},
        },
    }


def _write_store(path, version, state):
    path.write_bytes(orjson.dumps({"state_version": version, "state": state}))


def _age(path, seconds):
    """Move `path`'s mtime `seconds` into the past."""
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns,
                       stat.st_mtime_ns - int(seconds * 1e9)))


def _read_store(path):
    return orjson.loads(path.read_bytes())


@pytest.fixture
def files(tmp_path):
    (tmp_path / "user_data").mkdir()
    (tmp_path / "out").mkdir()
    return SimpleNamespace(
        dir=tmp_path,
        store=tmp_path / "user_data" / "state.json",
        legacy=tmp_path / "out" / "program_state.json")


def _load(files):
    return StatePersistence.Load(str(files.store), str(files.legacy))


def _backups(files, pattern):
    return sorted(files.store.parent.glob(pattern))


@pytest.fixture
def logged():
    from loguru import logger

    records = []
    sink = logger.add(lambda message: records.append(message.record),
                      level="INFO")
    yield records
    logger.remove(sink)


def _levels(records, level):
    return [r["message"] for r in records if r["level"].name == level]


# ── 1. StateMigrations ──────────────────────────────────────────────────

def test_v0_to_v1_drops_the_retired_roots_and_stale_keys():
    state = migrations.Migrate(_legacy_v0(), 0)

    for root in ("bracket", "player_list", "player_editor"):
        assert root not in state, root
    assert state["tournamentInfo"] == {"tournamentName": "Kept"}
    # Untouched: wiping score / game is the launch's job, not a migration's.
    assert state["score"] == {"1": {"team": {"1": {"score": 2}}}}
    assert state["game"] == {"name": "Some Game"}


def test_v0_to_v1_normalises_the_legacy_commentary_shape():
    state = migrations.Migrate(_legacy_v0(), 0)

    commentary = state["commentary"]
    assert list(commentary) == CommentaryModel.SLOTS
    assert commentary["1"]["note"] == "legacy note"
    assert commentary["1"]["display"] == 1
    assert "custom_textbox" not in commentary["1"]


def test_v0_to_v1_does_not_seed_a_missing_commentary():
    # Seeding a fresh install is SeedPanelState()'s job.
    assert "commentary" not in migrations.Migrate({"stream": {}}, 0)


def test_v0_to_v1_accepts_garbage_where_it_looks():
    state = migrations.Migrate(
        {"tournamentInfo": "not a dict", "commentary": None}, 0)
    assert state["tournamentInfo"] == "not a dict"
    assert state["commentary"] == CommentaryModel.DefaultState()


def test_the_v1_step_is_frozen():
    # A step describes what changed between two versions; editing it after
    # it shipped would silently do something else to old data. New launch
    # cleanups go to StateSchema, new shape changes to a new step.
    assert migrations._V1_RETIRED_ROOTS == (
        "bracket", "player_list", "player_editor")
    assert migrations._V1_STALE_KEYS == (
        ("tournamentInfo", "streamTitleEdit"),
        ("tournamentInfo", "streamUrl"))
    # Same names as the launch cleanup they were copied from.
    assert set(migrations._V1_RETIRED_ROOTS) == set(schema.RETIRED_ROOTS)
    assert {".".join(k) for k in migrations._V1_STALE_KEYS} == set(
        schema.STALE_KEYS)


def test_every_version_below_the_current_one_has_a_step():
    assert migrations.CURRENT_VERSION == 1
    assert sorted(migrations._STEPS) == list(
        range(migrations.CURRENT_VERSION))


def test_a_current_state_comes_back_untouched():
    state = {"bracket": {"kept": "a v1 state is not migrated again"}}
    assert migrations.Migrate(state, migrations.CURRENT_VERSION) is state
    assert state == {"bracket": {"kept": "a v1 state is not migrated again"}}


@pytest.mark.parametrize("version", [-1, 2, 99])
def test_a_version_out_of_range_is_refused(version):
    with pytest.raises(ValueError):
        migrations.Migrate({}, version)


@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_a_version_that_is_not_an_int_is_refused(version):
    with pytest.raises(TypeError):
        migrations.Migrate({}, version)


def test_a_state_that_is_not_a_dict_is_refused():
    with pytest.raises(TypeError):
        migrations.Migrate([], 0)


def test_the_launch_cleanup_has_nothing_left_to_do_after_the_migration():
    """SeedPanelState() runs the same three steps at every launch.

    They stay there on purpose (player_editor is also written by the current
    build, and the commentary pass doubles as the fresh-install seed), so a
    migrated state goes through both. That is only safe if the second pass
    finds nothing: the commentary is a fixed point of its normaliser and the
    retired roots / stale keys are already gone.
    """
    state = migrations.Migrate(_legacy_v0(), 0)

    assert CommentaryModel.NormalizeState(state["commentary"]) == \
        state["commentary"]
    for root in schema.RETIRED_ROOTS:
        assert root not in state, root
    for stale in schema.STALE_KEYS:
        root, key = stale.split(".")
        assert key not in state.get(root, {}), stale


def test_v0_to_v1_leaves_a_state_already_at_v1_as_it_is():
    """Every step is idempotent (StateMigrations' rules).

    program_state.json carries no version, so it is always migrated from 0
    -- also when a build with the store wrote it in the v1 shape (a crash,
    a downgrade and back).
    """
    v1 = migrations.Migrate(_legacy_v0(), 0)
    v1["commentary"]["2"]["note"] = "a v1 note"

    again = migrations.Migrate(copy.deepcopy(v1), 0)

    assert again == v1


# ── 2. Load ─────────────────────────────────────────────────────────────

def test_a_usable_store_is_loaded_and_left_as_it_is(files):
    stored = {"tournamentInfo": {"tournamentName": "Stored"}}
    _write_store(files.store, 1, stored)
    files.legacy.write_bytes(orjson.dumps(
        {"tournamentInfo": {"tournamentName": "Legacy"}}))
    _age(files.legacy, 60)  # an older export: the store is the newer record
    before = files.store.read_bytes()

    state, found = _load(files)

    assert (state, found) == (stored, True)
    assert files.store.read_bytes() == before, "the store was rewritten"
    assert StatePersistence.Path() == str(files.store)


def test_a_store_written_at_version_0_is_migrated(files):
    _write_store(files.store, 0, {"bracket": {}, "kept": 1})

    state, _found = _load(files)

    assert state == {"kept": 1}


def test_the_first_launch_after_the_update_migrates_program_state(files):
    legacy = orjson.dumps(_legacy_v0())
    files.legacy.write_bytes(legacy)

    state, found = _load(files)

    assert found is True
    assert "timestamp" not in state and "revision" not in state
    assert state == migrations.Migrate(
        {k: v for k, v in _legacy_v0().items()
         if k not in ("timestamp", "revision")}, 0)
    # Written right away: from now on it is the store, whatever happens to
    # ./out. The export file itself is not touched.
    assert _read_store(files.store) == {"state_version": 1, "state": state}
    assert files.legacy.read_bytes() == legacy
    assert StatePersistence.Path() == str(files.store)


def test_with_neither_file_the_launch_starts_empty(files):
    state, found = _load(files)

    assert (state, found) == ({}, False)
    assert not files.store.exists(), "nothing to migrate, yet a store appeared"
    assert StatePersistence.Path() == str(files.store)


@pytest.mark.parametrize("body", [
    b"",
    b"{not json",
    b"[]",
    b'{"state": {}}',
    b'{"state_version": "1", "state": {}}',
    b'{"state_version": true, "state": {}}',
    b'{"state_version": 1}',
    b'{"state_version": 1, "state": []}',
    b'{"state_version": -1, "state": {}}',
], ids=["empty", "not-json", "array", "no-version", "string-version",
        "bool-version", "no-state", "state-not-object", "negative-version"])
def test_a_corrupt_store_is_set_aside_and_program_state_migrated(
        files, logged, body):
    files.store.write_bytes(body)
    files.legacy.write_bytes(orjson.dumps(
        {"revision": 7, "tournamentInfo": {"tournamentName": "Legacy"}}))

    state, found = _load(files)

    assert found is True
    assert state == {"tournamentInfo": {"tournamentName": "Legacy"}}
    backups = _backups(files, "state.json.corrupt-*.bak")
    assert len(backups) == 1
    assert backups[0].read_bytes() == body, "the corrupt store was not kept"
    assert _read_store(files.store) == {"state_version": 1, "state": state}
    assert any("unusable" in m for m in _levels(logged, "WARNING"))


def test_a_corrupt_store_without_program_state_starts_empty(files):
    files.store.write_bytes(b"{not json")

    state, found = _load(files)

    assert (state, found) == ({}, True)
    assert len(_backups(files, "state.json.corrupt-*.bak")) == 1
    assert not files.store.exists()


def test_corrupt_backups_never_overwrite_each_other(files, monkeypatch):
    fixed = datetime.datetime(2026, 10, 25, 12, 0, 0)

    class _Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(persistence, "datetime",
                        SimpleNamespace(datetime=_Clock))
    for body in (b"first", b"second", b"third"):
        files.store.write_bytes(body)
        _load(files)

    backups = _backups(files, "state.json.corrupt-*.bak")
    assert [p.name for p in backups] == [
        "state.json.corrupt-20261025-120000-2.bak",
        "state.json.corrupt-20261025-120000-3.bak",
        "state.json.corrupt-20261025-120000.bak",
    ]
    assert sorted(p.read_bytes() for p in backups) == [
        b"first", b"second", b"third"]


def test_a_store_from_a_newer_build_is_set_aside(files, logged):
    newer = orjson.dumps({"state_version": 2, "state": {"shape": "v2"}})
    files.store.write_bytes(newer)
    files.legacy.write_bytes(orjson.dumps({"kept": "legacy"}))

    state, found = _load(files)

    assert (state, found) == ({"kept": "legacy"}, True)
    backups = _backups(files, "state.json.v2.*.bak")
    assert len(backups) == 1
    assert backups[0].read_bytes() == newer
    assert _read_store(files.store) == {"state_version": 1,
                                        "state": {"kept": "legacy"}}
    assert any("newer Autona" in m for m in _levels(logged, "WARNING"))


def test_newer_backups_never_overwrite_each_other(files, monkeypatch):
    # Two downgrades in the same second: each set-aside copy survives.
    fixed = datetime.datetime(2026, 10, 25, 12, 0, 0)

    class _Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(persistence, "datetime",
                        SimpleNamespace(datetime=_Clock))
    bodies = [orjson.dumps({"state_version": 2, "state": {"n": n}})
              for n in (1, 2)]
    for body in bodies:
        files.store.write_bytes(body)
        _load(files)

    backups = _backups(files, "state.json.v2.*.bak")
    assert [p.name for p in backups] == [
        "state.json.v2.20261025-120000-2.bak",
        "state.json.v2.20261025-120000.bak",
    ]
    assert sorted(p.read_bytes() for p in backups) == sorted(bodies)


def test_a_newer_store_that_cannot_be_set_aside_is_never_overwritten(
        files, logged, monkeypatch):
    newer = orjson.dumps({"state_version": 2, "state": {"shape": "v2"}})
    files.store.write_bytes(newer)
    files.legacy.write_bytes(orjson.dumps({"kept": "legacy"}))
    monkeypatch.setattr(StatePersistence, "_SetAside",
                        staticmethod(lambda path, backup: False))

    state, _found = _load(files)

    assert state == {"kept": "legacy"}
    assert StatePersistence.Path() is None, "the store was left open"
    StatePersistence.Schedule({"would": "overwrite"})
    assert StatePersistence.Flush(30)
    assert files.store.read_bytes() == newer
    assert any("could not be moved" in m for m in _levels(logged, "ERROR"))


def test_an_unreadable_program_state_starts_empty(files, logged):
    files.legacy.write_bytes(b"{truncated")

    state, found = _load(files)

    assert (state, found) == ({}, True)
    assert not files.store.exists()
    assert _levels(logged, "ERROR"), "the unreadable export was not logged"


def test_a_program_state_that_is_not_an_object_starts_empty(files):
    files.legacy.write_bytes(b"[1, 2]")

    assert _load(files) == ({}, True)


def test_export_stamps_in_a_restored_store_are_dropped(files):
    # A hand-restored copy of program_state.json pasted into the envelope.
    _write_store(files.store, 1, {"timestamp": 1.0, "revision": 9, "k": "v"})

    state, _found = _load(files)

    assert state == {"k": "v"}


def test_a_program_state_newer_than_the_store_wins(files, logged):
    """A crash or a forced exit: the export got the last seconds, the
    debounced store did not."""
    _write_store(files.store, 1, {"tournamentInfo": {"tournamentName": "Old"}})
    _age(files.store, 3)
    files.legacy.write_bytes(orjson.dumps(
        {"timestamp": 2.0, "revision": 9,
         "tournamentInfo": {"tournamentName": "Last seconds"}}))

    state, found = _load(files)

    assert found is True
    assert state == {"tournamentInfo": {"tournamentName": "Last seconds"}}
    # Rewritten right away, so the next launch has it even if ./out goes.
    assert _read_store(files.store) == {"state_version": 1, "state": state}
    assert StatePersistence.Path() == str(files.store)
    assert any("is newer than" in m for m in _levels(logged, "INFO"))


def test_returning_from_a_downgrade_keeps_what_the_old_build_did(files):
    """A pre-S3b build never writes the store, only program_state.json.

    Back on this build, the store still holds the state from before the
    downgrade; the newer export is what the operator last saw.
    """
    _write_store(files.store, 1,
                 {"tournamentInfo": {"tournamentName": "Before downgrade"}})
    _age(files.store, 3600)
    files.legacy.write_bytes(orjson.dumps(
        {"timestamp": 3.0, "revision": 120, "bracket": {"old build": 1},
         "tournamentInfo": {"tournamentName": "Entered on the old build",
                            "streamUrl": "https://old.example"}}))

    state, _found = _load(files)

    # Migrated as v0: the old build's leftovers are gone too.
    assert state == {
        "tournamentInfo": {"tournamentName": "Entered on the old build"}}
    assert _read_store(files.store)["state"] == state


def test_a_program_state_as_new_as_the_store_does_not_win(files):
    stored = {"k": "store"}
    _write_store(files.store, 1, stored)
    files.legacy.write_bytes(orjson.dumps({"k": "export"}))
    mtime = os.stat(files.store).st_mtime_ns
    os.utime(files.legacy, ns=(mtime, mtime))
    before = files.store.read_bytes()

    state, _found = _load(files)

    assert state == stored
    assert files.store.read_bytes() == before


@pytest.mark.parametrize("body", [b"{torn", b"[1]"],
                         ids=["not-json", "not-object"])
def test_a_newer_program_state_that_is_unusable_keeps_the_store(
        files, logged, body):
    stored = {"k": "store"}
    _write_store(files.store, 1, stored)
    _age(files.store, 3)
    files.legacy.write_bytes(body)
    before = files.store.read_bytes()

    state, _found = _load(files)

    assert state == stored
    assert files.store.read_bytes() == before
    assert any("keeping" in m for m in _levels(logged, "WARNING"))


def test_a_newer_program_state_that_fails_to_migrate_keeps_the_store(
        files, logged, monkeypatch):
    stored = {"k": "store"}
    _write_store(files.store, 1, stored)  # v1: no step runs on it
    _age(files.store, 3)
    files.legacy.write_bytes(orjson.dumps({"k": "export"}))
    monkeypatch.setitem(migrations._STEPS, 0, _raising_step)

    state, _found = _load(files)

    assert state == stored
    assert StatePersistence.Path() == str(files.store)
    assert _levels(logged, "ERROR")


def _raising_step(state):
    raise RuntimeError("a broken migration step")


def test_a_store_whose_migration_fails_is_treated_as_corrupt(
        files, logged, monkeypatch):
    body = orjson.dumps({"state_version": 0, "state": {"k": "v0"}})
    files.store.write_bytes(body)
    monkeypatch.setitem(migrations._STEPS, 0, _raising_step)

    state, found = _load(files)

    assert (state, found) == ({}, True)
    backups = _backups(files, "state.json.corrupt-*.bak")
    assert [b.read_bytes() for b in backups] == [body]
    assert any("migration from state_version 0 failed" in m
               for m in _levels(logged, "WARNING"))


def test_a_program_state_whose_migration_fails_starts_empty(
        files, logged, monkeypatch):
    files.legacy.write_bytes(orjson.dumps({"k": "export"}))
    monkeypatch.setitem(migrations._STEPS, 0, _raising_step)

    state, found = _load(files)

    assert (state, found) == ({}, True)
    assert not files.store.exists(), "nothing migrated, yet a store appeared"
    assert _levels(logged, "ERROR")


def test_a_corrupt_store_that_cannot_be_set_aside_is_never_overwritten(
        files, logged, monkeypatch):
    files.store.write_bytes(b"{not json")
    files.legacy.write_bytes(orjson.dumps({"kept": "legacy"}))
    monkeypatch.setattr(StatePersistence, "_SetAside",
                        staticmethod(lambda path, backup: False))

    state, _found = _load(files)

    assert state == {"kept": "legacy"}
    # Kept as the evidence: neither the migration nor a save replaces it.
    assert StatePersistence.Path() is None, "the store was left open"
    StatePersistence.Schedule({"would": "overwrite"})
    assert StatePersistence.Flush(30)
    assert files.store.read_bytes() == b"{not json"
    assert any("could not be moved" in m for m in _levels(logged, "ERROR"))


@pytest.fixture
def failing_open(monkeypatch):
    """open() of the store raises PermissionError `failures` times."""
    monkeypatch.setattr(persistence, "_READ_RETRY_DELAY", 0.0)
    calls = SimpleNamespace(store=0, failures=0, path=None)

    def fake_open(path, *args, **kwargs):
        if os.path.abspath(path) == calls.path:
            calls.store += 1
            if calls.store <= calls.failures:
                raise PermissionError(13, "locked by another process", path)
        return open(path, *args, **kwargs)

    monkeypatch.setattr(persistence, "open", fake_open, raising=False)
    return calls


def test_a_store_that_stays_unreadable_is_never_overwritten(
        files, logged, failing_open):
    stored = orjson.dumps({"state_version": 1, "state": {"k": "store"}})
    files.store.write_bytes(stored)
    files.legacy.write_bytes(orjson.dumps({"k": "export"}))
    _age(files.legacy, 60)
    failing_open.path = str(files.store)
    failing_open.failures = 99

    state, found = _load(files)

    assert failing_open.store == persistence._READ_ATTEMPTS
    assert (state, found) == ({"k": "export"}, True)
    assert StatePersistence.Path() is None, "the store was left open"
    StatePersistence.Schedule({"would": "overwrite"})
    assert StatePersistence.Flush(30)
    assert files.store.read_bytes() == stored
    assert any("Could not read" in m for m in _levels(logged, "ERROR"))


def test_a_store_locked_for_a_moment_is_read_on_a_retry(files, failing_open):
    _write_store(files.store, 1, {"k": "store"})
    failing_open.path = str(files.store)
    failing_open.failures = persistence._READ_ATTEMPTS - 1

    state, _found = _load(files)

    assert state == {"k": "store"}
    assert StatePersistence.Path() == str(files.store)


def test_stale_temp_files_are_removed_at_launch(files):
    old = files.store.parent / "state.abc123.tmp"
    fresh = files.store.parent / "state.def456.tmp"
    unrelated = files.store.parent / "settings.abc123.tmp"
    for path in (old, fresh, unrelated):
        path.write_bytes(b"partial")
    for path in (old, unrelated):
        _age(path, 2 * persistence._STALE_TEMP_AGE)

    _load(files)

    assert not old.exists(), "a temp file from a killed writer was left"
    assert fresh.exists(), "a write that may be in progress was removed"
    assert unrelated.exists(), "another store's temp file was removed"


def test_a_stale_temp_file_that_cannot_be_removed_is_ignored(
        files, monkeypatch):
    old = files.store.parent / "state.abc123.tmp"
    old.write_bytes(b"partial")
    _age(old, 2 * persistence._STALE_TEMP_AGE)
    _write_store(files.store, 1, {"k": "store"})

    def refuse(path, *args, **kwargs):
        raise PermissionError(13, "in use", path)

    monkeypatch.setattr(os, "remove", refuse)

    state, _found = _load(files)

    assert state == {"k": "store"}
    assert old.exists()


# ── 3. The writer ───────────────────────────────────────────────────────

@pytest.fixture
def store(files):
    """An open store in a throwaway directory."""
    with StatePersistence._cond:
        StatePersistence._path = str(files.store)
    return files.store


@pytest.fixture
def writes(monkeypatch):
    """Every store write, as (monotonic time, snapshot), then the real one."""
    recorded = []
    real = StatePersistence._WriteStore

    def recording(path, state):
        recorded.append((time.monotonic(), state))
        real(path, state)

    monkeypatch.setattr(StatePersistence, "_WriteStore",
                        staticmethod(recording))
    return recorded


def _join_writer():
    thread = StatePersistence._thread
    if thread is not None:
        thread.join(30)
        assert not thread.is_alive(), "the store writer never finished"


def test_the_writer_waits_for_the_state_to_be_quiet(store, writes,
                                                    monkeypatch):
    monkeypatch.setattr(persistence, "WRITE_DELAY", 0.3)
    monkeypatch.setattr(persistence, "MAX_WRITE_DELAY", 30.0)

    for value in range(5):
        StatePersistence.Schedule({"value": value})
    lastChange = time.monotonic()
    _join_writer()

    assert [snapshot for _t, snapshot in writes] == [{"value": 4}], (
        "a burst of changes was not written once, with the newest snapshot")
    assert writes[0][0] - lastChange >= 0.3 * 0.9, "written before the delay"
    assert _read_store(store) == {"state_version": 1, "state": {"value": 4}}


def test_a_steady_stream_of_changes_cannot_postpone_the_write(
        store, writes, monkeypatch):
    monkeypatch.setattr(persistence, "WRITE_DELAY", 0.3)
    monkeypatch.setattr(persistence, "MAX_WRITE_DELAY", 0.6)

    end = time.monotonic() + 1.5
    value = 0
    while time.monotonic() < end:
        StatePersistence.Schedule({"value": value})
        value += 1
        time.sleep(0.1)
    duringStream = len(writes)
    _join_writer()

    assert duringStream >= 1, (
        "changes every 0.1 s held the write back for the whole stream")
    assert _read_store(store)["state"] == {"value": value - 1}


def test_flush_writes_without_waiting_out_the_delay(store, monkeypatch):
    monkeypatch.setattr(persistence, "WRITE_DELAY", 60.0)
    monkeypatch.setattr(persistence, "MAX_WRITE_DELAY", 60.0)
    StatePersistence.Schedule({"flushed": True})

    started = time.monotonic()
    assert StatePersistence.Flush(30)

    assert time.monotonic() - started < 10
    assert _read_store(store) == {"state_version": 1,
                                  "state": {"flushed": True}}


def test_the_store_never_carries_the_export_stamps(store):
    snapshot = {"timestamp": 1.0, "revision": 3, "kept": "yes"}
    StatePersistence.Schedule(snapshot)
    assert StatePersistence.Flush(30)

    assert _read_store(store) == {"state_version": 1,
                                  "state": {"kept": "yes"}}
    # The published snapshot is shared with lastSavedState: never mutated.
    assert snapshot == {"timestamp": 1.0, "revision": 3, "kept": "yes"}


def test_the_store_is_replaced_atomically(store, monkeypatch):
    # Written beside the target and swapped in; no temp file is left.
    StatePersistence.Schedule({"a": 1})
    assert StatePersistence.Flush(30)
    StatePersistence.Schedule({"a": 2})
    assert StatePersistence.Flush(30)

    assert _read_store(store)["state"] == {"a": 2}
    assert sorted(p.name for p in store.parent.iterdir()) == ["state.json"]


def test_a_closed_store_is_never_written(files):
    with StatePersistence._cond:
        StatePersistence._path = None

    StatePersistence.Schedule({"nowhere": True})

    assert StatePersistence._pending is None
    assert StatePersistence.Flush(1)
    assert not files.store.exists()


def test_a_failing_write_is_logged_once_and_the_writer_recovers(
        files, logged):
    missing = files.dir / "no_such_dir" / "state.json"
    with StatePersistence._cond:
        StatePersistence._path = str(missing)

    for value in (1, 2):
        StatePersistence.Schedule({"value": value})
        assert StatePersistence.Flush(30)
    errors = [m for m in _levels(logged, "ERROR") if "Could not write" in m]
    assert len(errors) == 1, errors

    with StatePersistence._cond:
        StatePersistence._path = str(files.store)
    StatePersistence.Schedule({"value": 3})
    assert StatePersistence.Flush(30)
    assert _read_store(files.store)["state"] == {"value": 3}


def test_the_writer_is_not_a_daemon(store, monkeypatch):
    monkeypatch.setattr(persistence, "WRITE_DELAY", 60.0)
    StatePersistence.Schedule({"daemon": "?"})
    try:
        assert StatePersistence._thread is not None
        # Interpreter shutdown joins non-daemon threads only; that join is
        # what gets the last state into the store when the app exits.
        assert StatePersistence._thread.daemon is False
    finally:
        assert StatePersistence.Flush(30)


class _UnstartableThread(threading.Thread):
    def start(self):
        raise RuntimeError("can't start new thread")


def test_a_writer_that_fails_to_start_leaves_the_snapshot_pending(
        store, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(persistence.threading, "Thread", _UnstartableThread)
        # Never raised into SaveState: the export still has to be queued.
        StatePersistence.Schedule({"stranded": True})

    assert StatePersistence._running is False
    assert StatePersistence._pending == {"stranded": True}
    assert StatePersistence.Flush(30)
    assert _read_store(store)["state"] == {"stranded": True}


def test_a_withdrawn_flush_leaves_no_request_behind(store, monkeypatch):
    """A flush request is consumed with the snapshot it fetched; one that
    gives up must not make later snapshots skip the delay either."""
    monkeypatch.setattr(persistence, "WRITE_DELAY", 0.5)
    monkeypatch.setattr(persistence, "MAX_WRITE_DELAY", 30.0)
    recorded = []
    started = threading.Event()
    release = threading.Event()
    real = StatePersistence._WriteStore

    def held(path, state):
        if not started.is_set():
            started.set()
            release.wait(10)
        recorded.append((time.monotonic(), state))
        real(path, state)

    monkeypatch.setattr(StatePersistence, "_WriteStore", staticmethod(held))

    StatePersistence.Schedule({"value": "a"})
    with StatePersistence._cond:  # a Flush's request, as it sets it
        StatePersistence._flushRequested = True
        StatePersistence._cond.notify_all()
    assert started.wait(10), "the writer never fetched the snapshot"
    assert StatePersistence.Flush(0.05) is False  # gives up mid-write
    assert StatePersistence._flushRequested is False
    scheduled = time.monotonic()
    StatePersistence.Schedule({"value": "b"})
    release.set()
    _join_writer()

    assert [state for _, state in recorded] == [{"value": "a"},
                                                {"value": "b"}]
    assert recorded[1][0] - scheduled >= 0.45, (
        "the second snapshot skipped the delay")


def test_a_flush_whose_writer_cannot_start_leaves_no_request_behind(
        store, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(persistence.threading, "Thread", _UnstartableThread)
        StatePersistence.Schedule({"stranded": True})
        assert StatePersistence.Flush(1) is False
        assert StatePersistence._flushRequested is False

    assert StatePersistence.Flush(30)
    assert _read_store(store)["state"] == {"stranded": True}


class _WriterKilled(BaseException):
    pass


def test_a_writer_killed_mid_write_hands_its_snapshot_back(
        store, monkeypatch):
    """_Run's finally: a BaseException out of the write (not an Exception,
    which is only logged) puts the snapshot back for the next writer."""
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    real = StatePersistence._WriteStore

    def killed(path, state):
        raise _WriterKilled()

    monkeypatch.setattr(StatePersistence, "_WriteStore", staticmethod(killed))
    StatePersistence.Schedule({"value": "in flight"})
    with StatePersistence._cond:
        StatePersistence._flushRequested = True
        StatePersistence._cond.notify_all()
    _join_writer()

    assert StatePersistence._running is False
    assert StatePersistence._writing is False
    assert StatePersistence._flushRequested is False
    assert StatePersistence._pending == {"value": "in flight"}
    assert not store.exists()

    monkeypatch.setattr(StatePersistence, "_WriteStore", staticmethod(real))
    assert StatePersistence.Flush(30)
    assert _read_store(store)["state"] == {"value": "in flight"}


# ── 4. StateManager, in process ─────────────────────────────────────────

ROOT = "s3b_probe"


@pytest.fixture(scope="module")
def _export_root(tmp_path_factory):
    """Where the in-process tests' export writes (./out under the CWD).

    Initialize() is not called (a once-per-process no-op, see
    tests/test_actions.py); the store's path is set directly instead. From
    tmp_path_factory rather than a mkdtemp() at import, which would leave a
    directory behind on every run.
    """
    root = tmp_path_factory.mktemp("persistence_export")
    (root / "out").mkdir()
    (root / "user_data").mkdir()
    return root


@pytest.fixture
def work_dir(_export_root, store, monkeypatch):
    os.chdir(_export_root)
    monkeypatch.setattr(SettingsManager, "settings", {})
    try:
        yield _export_root
    finally:
        SM.Unset(ROOT)
        _join_export()
        assert StatePersistence.Flush(30)
        os.chdir(_ORIGINAL_CWD)


def _join_export():
    if SM._export_thread is not None:
        SM._export_thread.join()


def test_every_published_save_cycle_reaches_the_store(work_dir, store):
    SM.Set(f"{ROOT}.value", "published")
    _join_export()
    assert StatePersistence.Flush(30)

    document = _read_store(store)
    assert document["state_version"] == 1
    assert document["state"][ROOT] == {"value": "published"}
    assert "timestamp" not in document["state"]
    assert "revision" not in document["state"]
    # The export is unchanged: the stamps are still in program_state.json.
    exported = orjson.loads((work_dir / "out" / "program_state.json")
                            .read_bytes())
    assert exported[ROOT] == {"value": "published"}
    assert "timestamp" in exported and exported["revision"] == SM.revision


def test_disable_export_does_not_stop_the_store(work_dir, store, monkeypatch):
    monkeypatch.setattr(SettingsManager, "settings",
                        {"general": {"disable_export": True}})

    SM.Set(f"{ROOT}.quiet", "still saved")
    _join_export()
    assert StatePersistence.Flush(30)

    assert _read_store(store)["state"][ROOT] == {"quiet": "still saved"}
    assert not (work_dir / "out" / ROOT / "quiet.txt").exists(), (
        "precondition: disable_export should have skipped the text export")


def test_held_edits_reach_the_store_only_when_applied(work_dir, store):
    prefix = f"{ROOT}.board"
    SM.Set(f"{prefix}.name", "committed")
    _join_export()
    SM.HoldSubtree(prefix)
    try:
        SM.Set(f"{prefix}.name", "pending")
        SM.Set(f"{ROOT}.other", "published")  # a cycle while held-dirty
        _join_export()
        assert StatePersistence.Flush(30)
        assert _read_store(store)["state"][ROOT] == {
            "board": {"name": "committed"}, "other": "published"}

        SM.CommitHeld(prefix)
        _join_export()
        assert StatePersistence.Flush(30)
        assert _read_store(store)["state"][ROOT]["board"] == {
            "name": "pending"}
    finally:
        with SM.lock:
            SM.heldPrefixes.discard(prefix)
            SM.heldExempt.pop(prefix, None)
            SM.heldKeys.pop(prefix, None)
            SM._commitPrefixes.discard(prefix)
            SM._heldTouched.discard(prefix)


# ── 5. StateManager, fresh interpreter ──────────────────────────────────

_LAUNCH_SCRIPT = r"""
import json, sys
sys.path.insert(0, {root!r})
from src.core.StateManager import StateManager as SM

big = []
SM.observers.state_big_change.subscribe(lambda: big.append(1))
SM.Initialize()

with open("user_data/state.json", "rb") as handle:
    store = json.loads(handle.read())
report = {{
    "big": len(big),
    "revision": SM.revision,
    "keys": sorted(SM.state),
    "full_state_keys": sorted(SM.GetFullState()["state"]),
    "store": store,
}}
SM.Set("tournamentInfo.tournamentName", "After")
SM._export_thread.join()
with open("out/program_state.json", "rb") as handle:
    report["exported"] = json.loads(handle.read())
print({marker!r} + json.dumps(report))
# No flush: the process just ends, as the app does after closeEvent.
"""


def _run(script, cwd):
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=cwd,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    line = next(l for l in result.stdout.splitlines() if l.startswith(MARKER))
    return json.loads(line[len(MARKER):])


def test_a_launch_from_a_pre_s3b_install(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    (tmp_path / "out" / "program_state.json").write_bytes(
        orjson.dumps(_legacy_v0()))
    script = _LAUNCH_SCRIPT.format(root=str(PROJECT_ROOT), marker=MARKER)

    report = _run(script, tmp_path)

    # As LoadState always did: one big change, the revision from 0.
    assert report["big"] == 1
    assert report["revision"] == 0
    # The previous session's stamps no longer ride along in the state (and
    # so in every full snapshot); the legacy subtrees are migrated away.
    for key in ("timestamp", "revision", "bracket", "player_list",
                "player_editor"):
        assert key not in report["keys"], key
        assert key not in report["full_state_keys"], key
    assert report["store"]["state_version"] == 1
    assert sorted(report["store"]["state"]) == report["keys"]
    # program_state.json keeps its contract: stamped, same place.
    exported = report["exported"]
    assert exported["revision"] == 1 and "timestamp" in exported
    assert exported["tournamentInfo"]["tournamentName"] == "After"

    # The last change reached the store at exit, without a flush.
    store = orjson.loads((tmp_path / "user_data" / "state.json").read_bytes())
    assert store["state"]["tournamentInfo"]["tournamentName"] == "After"
    assert "timestamp" not in store["state"]


_EXIT_SCRIPT = r"""
import json, sys, time
sys.path.insert(0, {root!r})
import src.core.StatePersistence as persistence
from src.core.StateManager import StateManager as SM
from src.core.StatePersistence import StatePersistence

# Far longer than the test allows: only the shutdown path can write in time.
persistence.WRITE_DELAY = 600.0
persistence.MAX_WRITE_DELAY = 600.0

big = []
SM.observers.state_big_change.subscribe(lambda: big.append(1))
SM.Initialize()
for value in range(1, 6):
    SM.Set("probe.value", value)
print({marker!r} + json.dumps({{
    "big": len(big),
    "pending": StatePersistence._pending is not None,
}}))
"""


def test_the_last_state_reaches_the_store_when_the_process_exits(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    script = _EXIT_SCRIPT.format(root=str(PROJECT_ROOT), marker=MARKER)

    started = time.monotonic()
    report = _run(script, tmp_path)
    elapsed = time.monotonic() - started

    # Neither file: no big change, as before.
    assert report["big"] == 0
    # Otherwise this would not have exercised the shutdown path at all.
    assert report["pending"] is True
    assert elapsed < 60, "the exit waited out the write delay"
    store = orjson.loads((tmp_path / "user_data" / "state.json").read_bytes())
    assert store == {"state_version": 1, "state": {"probe": {"value": 5}}}


_CRASH_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, {root!r})
import src.core.StatePersistence as persistence
from src.core.StateManager import StateManager as SM

# The store would only be written after the exit below.
persistence.WRITE_DELAY = 600.0
persistence.MAX_WRITE_DELAY = 600.0
SM.Initialize()
loaded = dict(SM.state["tournamentInfo"])
SM.Set("tournamentInfo.tournamentName", "Typed just before the crash")
SM._export_thread.join()
print({marker!r} + json.dumps({{"loaded": loaded}}))
sys.stdout.flush()
os._exit(1)  # a crash: no shutdown join, no store write
"""


def test_a_crash_loses_nothing_the_export_already_wrote(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    store = tmp_path / "user_data" / "state.json"
    _write_store(store, 1, {"tournamentInfo": {"tournamentName": "Saved"}})
    _age(store, 60)
    crash = _CRASH_SCRIPT.format(root=str(PROJECT_ROOT), marker=MARKER)

    result = subprocess.run([sys.executable, "-B", "-c", crash],
                            cwd=tmp_path, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 1, result.stderr
    assert {"tournamentName": "Saved"} == json.loads(
        next(line for line in result.stdout.splitlines()
             if line.startswith(MARKER))[len(MARKER):])["loaded"]
    assert _read_store(store)["state"]["tournamentInfo"] == {
        "tournamentName": "Saved"}, "precondition: the store missed it"

    relaunch = _LAUNCH_SCRIPT.format(root=str(PROJECT_ROOT), marker=MARKER)
    report = _run(relaunch, tmp_path)

    assert report["store"]["state"]["tournamentInfo"] == {
        "tournamentName": "Typed just before the crash"}
