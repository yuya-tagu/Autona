"""Pins for the SaveState cost reductions (2026-10).

Three behaviour-preserving changes, each pinned here:

1. The delta rows are computed once per save cycle (the size check and the
   payload used to build `Delta(diff)` twice).
2. `include_paths` drops keys that sit below another queued key. DeepDiff
   keeps every path under an included one anyway, so the diff is identical;
   its per-node filter is linear in the number of include paths, which is
   what a commit after a set load (a cleared player subtree plus every field
   written back into it) was paying for.
3. The export no longer runs one thread per save cycle that the *next*
   SaveState joins from the GUI thread. One writer drains a queue in save
   order, and a backlog writes program_state.json once, with the newest
   snapshot. What the join used to guarantee is pinned explicitly: write
   order, the last state reaching disk at interpreter shutdown, and a
   published snapshot never being mutated under the next diff.

Importing StateManager is side-effect free, but the export writes ./out
relative to the CWD, so every test runs inside a throwaway directory.
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

import orjson
import pytest

import src.core.StateManager as module
from src.core.StateManager import StateManager as SM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ORIGINAL_CWD = os.getcwd()

ROOT = "savecost"


@pytest.fixture(scope="module")
def _workDir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("autona_save_cost")
    (directory / "out").mkdir()
    (directory / "user_data").mkdir()
    return directory


def _join_export():
    if SM._export_thread is not None:
        SM._export_thread.join()


@pytest.fixture(autouse=True)
def _in_work_dir(_workDir):
    os.chdir(_workDir)
    # Initialize() is a no-op after its first call anywhere in the process;
    # the directories above are what the export needs either way.
    SM.Initialize()
    _join_export()
    yield
    SM.Unset(ROOT)
    _join_export()
    os.chdir(_ORIGINAL_CWD)


def _program_state(workDir):
    return orjson.loads((Path(workDir) / "out" / "program_state.json")
                        .read_bytes())


class _Gate:
    """Makes the export writer block inside its program_state.json write.

    Records every payload it was asked to write, and appends ("json", value
    of ROOT.value) to `events` so a test can interleave it with its own
    records. The first write waits for `release()` and `parked` is True for
    exactly as long as the writer sits there; later writes go straight
    through. Always released on teardown, so a failing test cannot leave the
    writer parked. The 60 s cap is only a backstop against a hung suite --
    no assertion depends on it.
    """

    def __init__(self, monkeypatch):
        self.entered = threading.Event()
        self._open = threading.Event()
        self.parked = False
        self.writes = []
        self.events = []
        real = module._atomic_write_bytes

        def blocking_write(path, data):
            if path.endswith("program_state.json"):
                payload = orjson.loads(data)
                self.writes.append(payload)
                self.events.append(
                    ("json", payload.get(ROOT, {}).get("value")))
                if len(self.writes) == 1:
                    self.parked = True
                    self.entered.set()
                    self._open.wait(60)
                    self.parked = False
            real(path, data)

        monkeypatch.setattr(module, "_atomic_write_bytes", blocking_write)

    def release(self):
        self._open.set()


@pytest.fixture
def gate(monkeypatch):
    g = _Gate(monkeypatch)
    yield g
    g.release()
    _join_export()


class _SignallingCondition(threading.Condition):
    """The export condition, plus an Event set whenever someone waits on it.

    Only _QueueExport ever waits on it (backlog full), so `waiting` turning
    on is the exact moment SaveState started to block.
    """

    def __init__(self):
        super().__init__(threading.Lock())
        self.waiting = threading.Event()

    def wait(self, timeout=None):
        self.waiting.set()
        return super().wait(timeout)


def _recordTextExports(monkeypatch, events):
    real = SM.ExportText

    def recording(oldState, diff, stateSnapshot):
        events.append(("text", stateSnapshot.get(ROOT, {}).get("value")))
        return real(oldState, diff, stateSnapshot)

    monkeypatch.setattr(SM, "ExportText", recording)


# ── 1. delta rows computed once ─────────────────────────────────────────

def test_a_save_cycle_builds_the_delta_once(monkeypatch):
    built = []
    real = module.Delta

    def counting_delta(diff, *args, **kwargs):
        built.append(diff)
        return real(diff, *args, **kwargs)

    monkeypatch.setattr(module, "Delta", counting_delta)
    payloads = []
    callback = SM.observers.state_updated.subscribe(payloads.append)
    try:
        SM.Set(f"{ROOT}.name", "once")
    finally:
        SM.observers.state_updated.unsubscribe(callback)

    assert len(built) == 1, "the delta rows were built more than once"
    assert len(payloads) == 1
    # The payload still carries exactly the rows of that one Delta.
    assert payloads[0]["delta"] == real(
        built[0], always_include_values=True).to_flat_dicts()
    assert "once" in str(payloads[0]["delta"])


# ── 2. include_paths without redundant descendants ──────────────────────

def test_include_paths_drop_keys_below_another_queued_key():
    p = SM._bracketPath
    paths = SM._includePaths([
        p("score.1.team.1.player.1"),
        p("score.1.team.1.player.1.name"),
        p("score.1.team.1.player.1.character.1"),
        p("score.1.team.1.player.1"),        # duplicate
        p("score.1.team.1.player.10.name"),  # sibling sharing a prefix
        p("score.1.team.2.teamName"),
        p("game.name"),
    ])

    assert sorted(paths) == sorted([
        p("score.1.team.1.player.1"),
        p("score.1.team.1.player.10.name"),
        p("score.1.team.2.teamName"),
        p("game.name"),
    ])


def test_include_paths_keep_unrelated_keys_untouched():
    p = SM._bracketPath
    keys = [p("a.b"), p("a.bc"), p("a.c.d"), p("x")]
    assert sorted(SM._includePaths(keys)) == sorted(keys)


def test_a_collapsed_diff_publishes_exactly_what_the_raw_keys_would(
        monkeypatch):
    """The shape that motivated the change: clear a subtree, write it back."""
    SM.Set(f"{ROOT}.player", {"name": "Old", "city": "Tokyo",
                              "character": {"1": {"name": "A", "skin": 0}}})
    _join_export()

    seen = []
    real = module.DeepDiff

    def comparing_diff(t1, t2, **kwargs):
        collapsed = real(t1, t2, **kwargs)
        raw = real(t1, t2, **{**kwargs,
                              "include_paths": list(set(SM.changedKeys))})
        seen.append((kwargs["include_paths"], collapsed, raw))
        return collapsed

    monkeypatch.setattr(module, "DeepDiff", comparing_diff)
    payloads = []
    callback = SM.observers.state_updated.subscribe(payloads.append)
    try:
        with SM.SaveBlock():
            SM.Unset(f"{ROOT}.player")
            SM.Set(f"{ROOT}.player.name", "New")
            SM.Set(f"{ROOT}.player.character.1.name", "B")
            SM.Set(f"{ROOT}.player.character.1.skin", 3)
            SM.Set(f"{ROOT}.match", "Final")
    finally:
        SM.observers.state_updated.unsubscribe(callback)

    assert len(seen) == 1
    include_paths, collapsed, raw = seen[0]
    assert set(include_paths) == {
        SM._bracketPath(f"{ROOT}.player"), SM._bracketPath(f"{ROOT}.match")}
    assert collapsed == raw
    published = str(payloads)
    assert "New" in published and "Final" in published
    assert "'city'" in published  # removed by the Unset, still reported


# ── 2b. include_paths narrowed to the subtrees that differ ──────────────
#
# _narrowPaths drops the queued paths whose old and new values are strictly
# equal and replaces a changed dict by its changed children. The rows DeepDiff
# -> Delta make from the narrowed paths must be exactly the rows of the paths
# _includePaths gives (order, path, value, type).

def _rows(old, new, paths):
    """The rows SaveState publishes for these include paths."""
    if not paths:
        return []
    diff = module.DeepDiff(old, new, exclude_types=[type(None)],
                           include_paths=list(paths), verbose_level=2,
                           zip_ordered_iterables=True)
    return module.Delta(diff, always_include_values=True).to_flat_dicts()


def _board(**player):
    slot = {"name": "A", "score": 1, "flag": True, "note": "n",
            "list": [1, 2], "chars": {"1": {"n": "x", "s": 0}}}
    slot.update(player)
    return {"b": {"p": slot, "q": {"name": "B", "score": 2}},
            "other": {"v": 1}}


def _keys(*dotted):
    return SM._includePaths([SM._bracketPath(k) for k in dotted])


_SLOT = ("b.p", "b.q", "other")

_NARROW_CASES = {
    "int -> float": (_board(), _board(score=1.0), _SLOT),
    "bool -> int": (_board(), _board(flag=1), _SLOT),
    "nested int -> float": (
        _board(), _board(chars={"1": {"n": "x", "s": 0.0}}), ("b",)),
    "value -> None": (_board(), _board(note=None), _SLOT),
    "None -> value": (_board(note=None), _board(), _SLOT),
    "key added": (_board(), _board(extra="e"), _SLOT),
    "key removed": (_board(extra="e"), _board(), _SLOT),
    "key order only": (
        _board(),
        {"b": {"p": dict(reversed(list(_board()["b"]["p"].items()))),
               "q": {"name": "B", "score": 2}}, "other": {"v": 1}},
        _SLOT),
    "list grows": (_board(), _board(list=[1, 2, 3]), _SLOT),
    "list shrinks": (_board(), _board(list=[1]), _SLOT),
    "list replaced": (_board(), _board(list=[3, 4]), _SLOT),
    "list -> tuple": (_board(), _board(list=(1, 2)), _SLOT),
    "dict -> list": (_board(), _board(chars=[{"n": "x", "s": 0}]), _SLOT),
    "int keys": (_board(ik={1: "a", "2": "b"}),
                 _board(ik={1: "c", "2": "b"}), ("b",)),
    "int key vs str key": (_board(ik={1: "a"}), _board(ik={"1": "a"}),
                           ("b",)),
    "a key with a quote": (_board(**{"it's": 1}), _board(**{"it's": 2}),
                           ("b",)),
    "a path on neither side": (_board(), _board(name="Z"),
                               ("b.p", "b.zzz.y", "nowhere")),
    "a whole board, one leaf changed": (
        _board(), _board(name="Z"), ("b", "other")),
    "everything equal": (_board(), _board(), _SLOT),
    # DeepDiff's threshold_to_diff_deeper: with the unchanged `x` dropped,
    # none of the keys compared under `A` would be on both sides and the
    # whole dict would come as one values_changed row.
    "added keys beside an unchanged one": (
        {"A": {"x": {"p": 1, "q": 2}}},
        {"A": {"x": {"p": 1, "q": 2}, "y": 1, "z": 2}},
        ("A.x", "A.y", "A.z")),
    "added keys beside an unchanged leaf": (
        {"A": {"x": 1, "w": {"k": 1}}},
        {"A": {"x": 1, "w": {"k": 1}, "y": 1, "z": 2}},
        ("A.x", "A.w", "A.y", "A.z")),
    "added key beside two unchanged ones": (
        {"A": {"x": 1, "w": 2, "v": {"k": 1}}},
        {"A": {"x": 1, "w": 2, "v": {"k": 2}, "y": 1}},
        ("A.x", "A.w", "A.v", "A.y")),
    "removed key at the root beside an unchanged one": (
        {"gone": 1, "kept": {"a": 1}}, {"kept": {"a": 1}},
        ("gone", "kept")),
    "type change beside an unchanged sibling": (
        {"A": {"x": 1, "t": {"a": 1}}}, {"A": {"x": 1, "t": [1]}},
        ("A.x", "A.t")),
}


@pytest.mark.parametrize("name", sorted(_NARROW_CASES))
def test_narrowed_paths_publish_exactly_the_rows_of_the_queued_paths(name):
    old, new, dotted = _NARROW_CASES[name]
    paths = _keys(*dotted)
    narrowed = SM._narrowPaths(paths, old, new)

    assert narrowed == sorted(set(narrowed))
    for i, path in enumerate(narrowed):
        assert not any(other.startswith(path + "[")
                       for other in narrowed[i + 1:]), narrowed
    assert _rows(old, new, narrowed) == _rows(old, new, paths)


def test_narrowing_drops_equal_subtrees_and_keeps_only_changed_leaves():
    narrowed = SM._narrowPaths(
        _keys(*_SLOT), _board(), _board(score=1.0))
    assert narrowed == ["root['b']['p']['score']"]

    narrowed = SM._narrowPaths(
        _keys("b", "other"), _board(),
        _board(chars={"1": {"n": "x", "s": 3}}))
    assert narrowed == ["root['b']['p']['chars']['1']['s']"]

    assert SM._narrowPaths(_keys(*_SLOT), _board(), _board()) == []


def test_narrowing_stops_at_keys_deepdiff_could_render_differently():
    old = _board(**{"it's": 1})
    new = _board(**{"it's": 2})
    # p holds a key with a quote: p stays whole, the unchanged q goes.
    assert SM._narrowPaths(_keys("b"), old, new) == ["root['b']['p']"]

    old = _board(ik={1: "a"})
    new = _board(ik={1: "b"})
    assert SM._narrowPaths(_keys("b"), old, new) == ["root['b']['p']['ik']"]


def test_narrowing_keeps_every_key_compared_beside_an_added_one():
    old, new, dotted = _NARROW_CASES["added keys beside an unchanged one"]
    paths = _keys(*dotted)
    # Dropping x would turn two dictionary_item_added rows into one
    # values_changed row for A.
    assert SM._narrowPaths(paths, old, new) == paths
    assert [row["action"] for row in _rows(old, new, paths)] == [
        "dictionary_item_added", "dictionary_item_added"]


def test_a_path_not_in_bracket_form_leaves_all_paths_as_they_are():
    paths = ["root['b']['q']", "root['b'][\"it's\"]"]
    assert SM._narrowPaths(paths, _board(), _board()) == paths


def test_too_many_narrowed_paths_fall_back_to_the_queued_ones():
    old = {"big": {str(i): 0 for i in range(10)}}
    new = {"big": {str(i): 1 for i in range(10)}}
    paths = _keys("big")
    assert SM._narrowPaths(paths, old, new, limit=4) == paths
    assert len(SM._narrowPaths(paths, old, new, limit=64)) == 10
    assert _rows(old, new, SM._narrowPaths(paths, old, new, limit=4)) == \
        _rows(old, new, SM._narrowPaths(paths, old, new, limit=64))


def test_strict_equality_tells_apart_what_deepdiff_might():
    eq = SM._strictlyEqual
    assert eq({"a": [1, {"b": "c"}]}, {"a": [1, {"b": "c"}]})
    assert not eq(1, 1.0)
    assert not eq(True, 1)
    assert not eq([1], (1,))
    assert not eq({"a": [1]}, {"a": [1.0]})
    assert not eq({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not eq({1: "a"}, {True: "a"})
    assert not eq(float("nan"), float("nan"))
    nan = float("nan")
    assert eq(nan, nan)


def test_a_save_of_equal_values_runs_no_diff_and_publishes_nothing(
        monkeypatch):
    SM.Set(f"{ROOT}.slot", {"name": "Same", "chars": {"1": {"s": 0}}})
    _join_export()
    revision = SM.revision

    calls = []
    real = module.DeepDiff

    def counting(*args, **kwargs):
        calls.append(kwargs.get("include_paths"))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "DeepDiff", counting)
    payloads = []
    callback = SM.observers.state_updated.subscribe(payloads.append)
    try:
        with SM.SaveBlock():
            SM.Set(f"{ROOT}.slot.name", "Same")
            SM.Set(f"{ROOT}.slot.chars", {"1": {"s": 0}})
    finally:
        SM.observers.state_updated.unsubscribe(callback)

    assert calls == []
    assert SM.revision == revision
    assert payloads == []
    assert SM.changedKeys == []


def test_a_written_back_subtree_diffs_only_its_changed_leaf(monkeypatch):
    SM.Set(f"{ROOT}.slot", {"name": "Old", "city": "Tokyo",
                            "chars": {"1": {"n": "A", "s": 0}}})
    _join_export()

    seen = []
    real = module.DeepDiff

    def comparing(t1, t2, **kwargs):
        narrowed = real(t1, t2, **kwargs)
        raw = real(t1, t2, **{**kwargs, "include_paths": SM._includePaths(
            SM.changedKeys)})
        seen.append((kwargs["include_paths"], narrowed, raw))
        return narrowed

    monkeypatch.setattr(module, "DeepDiff", comparing)
    payloads = []
    callback = SM.observers.state_updated.subscribe(payloads.append)
    try:
        with SM.SaveBlock():
            SM.Set(f"{ROOT}.slot", {"name": "Old", "city": "Tokyo",
                                    "chars": {"1": {"n": "A", "s": 2}}})
            SM.Set(f"{ROOT}.slot.name", "Old")
    finally:
        SM.observers.state_updated.unsubscribe(callback)

    assert len(seen) == 1
    include_paths, narrowed, raw = seen[0]
    assert include_paths == [f"root['{ROOT}']['slot']['chars']['1']['s']"]
    assert narrowed == raw
    assert len(payloads) == 1
    assert [row["path"] for row in payloads[0]["delta"]] == [
        [ROOT, "slot", "chars", "1", "s"]]


# ── 3. export writer ────────────────────────────────────────────────────

def test_a_save_does_not_wait_for_the_previous_export(gate, _workDir):
    SM.Set(f"{ROOT}.value", 1)
    assert gate.entered.wait(30), "the export writer never started"

    # The writer is now parked inside program_state.json. The old SaveState
    # joined it here, so this Set could only return once the writer had
    # left the gate -- i.e. with `parked` already False again.
    SM.Set(f"{ROOT}.value", 2)
    assert gate.parked, "SaveState waited for the previous export"

    gate.release()
    _join_export()
    assert _program_state(_workDir)[ROOT]["value"] == 2


def test_a_backlog_writes_program_state_once_with_the_newest_snapshot(
        gate, _workDir):
    SM.Set(f"{ROOT}.value", 0)
    assert gate.entered.wait(30)
    for value in range(1, 6):
        SM.Set(f"{ROOT}.value", value)
    revision = SM.revision

    gate.release()
    _join_export()

    # One write for the cycle that was in flight, one for the five queued
    # behind it -- not six.
    assert [w[ROOT]["value"] for w in gate.writes] == [0, 5]
    state = _program_state(_workDir)
    assert state[ROOT]["value"] == 5
    assert state["revision"] == revision


def test_every_queued_cycle_still_runs_its_text_export_in_order(
        gate, monkeypatch, _workDir):
    events = gate.events
    _recordTextExports(monkeypatch, events)
    SM.Set(f"{ROOT}.value", 1)
    assert gate.entered.wait(30)
    for value in (2, 3, 4):
        SM.Set(f"{ROOT}.value", value)

    gate.release()
    _join_export()

    assert [v for kind, v in events if kind == "text"] == [1, 2, 3, 4]
    text = Path(_workDir, "out", ROOT, "value.txt").read_text(encoding="utf-8")
    assert text == "4"


def test_program_state_is_written_after_the_text_files_it_announces(
        gate, monkeypatch, _workDir):
    """The OBS guide has operators watch program_state.json and then read the
    .txt exports, so within a batch the JSON must come after every .txt."""
    events = gate.events
    _recordTextExports(monkeypatch, events)
    SM.Set(f"{ROOT}.value", 1)
    assert gate.entered.wait(30)
    for value in (2, 3, 4):
        SM.Set(f"{ROOT}.value", value)

    gate.release()
    _join_export()

    assert events == [
        ("text", 1), ("json", 1),
        ("text", 2), ("text", 3), ("text", 4), ("json", 4),
    ]


def test_the_writer_never_mutates_a_published_snapshot(gate, _workDir):
    SM.Set(f"{ROOT}.value", "a")
    assert gate.entered.wait(30)
    snapshot = SM.lastSavedState

    gate.release()
    _join_export()

    # timestamp / revision go into the file, not into lastSavedState -- the
    # next diff reads that dict without waiting for the writer. (The live
    # state may legitimately carry both from a loaded program_state.json;
    # the snapshot must then hold the same values, not freshly stamped ones.)
    assert snapshot.get("timestamp") == SM.state.get("timestamp")
    assert snapshot.get("revision") == SM.state.get("revision")
    state = _program_state(_workDir)
    assert "timestamp" in state and "revision" in state


def test_a_failing_export_does_not_stop_later_exports(monkeypatch, _workDir):
    from loguru import logger

    calls = []
    real = SM.ExportText

    def failing_once(oldState, diff, stateSnapshot):
        calls.append(stateSnapshot.get(ROOT, {}).get("value"))
        if len(calls) == 1:
            raise RuntimeError("export blew up")
        return real(oldState, diff, stateSnapshot)

    monkeypatch.setattr(SM, "ExportText", failing_once)
    logged = []
    sink = logger.add(logged.append, level="ERROR")
    try:
        SM.Set(f"{ROOT}.value", "first")
        _join_export()
        SM.Set(f"{ROOT}.value", "second")
        _join_export()
    finally:
        logger.remove(sink)

    assert calls == ["first", "second"]
    assert any("export blew up" in str(m) for m in logged), (
        "the export failure was swallowed without a log line")
    assert SM._export_running is False
    assert _program_state(_workDir)[ROOT]["value"] == "second"


def test_a_full_backlog_makes_save_wait_for_the_writer(gate, monkeypatch,
                                                       _workDir):
    # Patched before the first Set, so the writer started below uses it too.
    cond = _SignallingCondition()
    monkeypatch.setattr(SM, "_export_cond", cond)
    monkeypatch.setattr(module, "_EXPORT_BACKLOG_LIMIT", 1)
    SM.Set(f"{ROOT}.value", 1)
    assert gate.entered.wait(30)  # writer took job 1, queue empty
    SM.Set(f"{ROOT}.value", 2)    # queue: [2] -- at the limit
    assert not cond.waiting.is_set()

    # Opens the gate only once SaveState is actually blocked, so the order
    # below is fixed by events, not by how fast this machine is.
    releaser = threading.Thread(
        target=lambda: (cond.waiting.wait(60), gate.release()), daemon=True)
    releaser.start()
    SM.Set(f"{ROOT}.value", 3)    # must wait for the writer to take [2]
    blocked = cond.waiting.is_set()
    cond.waiting.set()            # let the releaser finish if it never fired
    releaser.join()

    assert blocked, "the backlog limit did not hold SaveState back"
    _join_export()
    assert _program_state(_workDir)[ROOT]["value"] == 3


class _UnstartableThread(threading.Thread):
    def start(self):
        raise RuntimeError("can't start new thread")


def test_a_writer_that_fails_to_start_leaves_its_job_for_the_next_save(
        monkeypatch, _workDir):
    events = []
    _recordTextExports(monkeypatch, events)
    previous = SM._export_thread

    with monkeypatch.context() as m:
        m.setattr(module.threading, "Thread", _UnstartableThread)
        with pytest.raises(RuntimeError):
            SM.Set(f"{ROOT}.value", "stranded")

    # Not left claiming a writer that does not exist, and not pointing
    # `_export_thread` at a thread that was never started (join() on one
    # raises).
    assert SM._export_running is False
    assert SM._export_thread is previous
    assert [job[0][ROOT]["value"] for job in SM._export_jobs] == ["stranded"]

    SM.Set(f"{ROOT}.value", "next")
    _join_export()

    assert SM._export_jobs == []
    assert [v for kind, v in events if kind == "text"] == ["stranded", "next"]
    assert _program_state(_workDir)[ROOT]["value"] == "next"


class _WriterKilled(BaseException):
    """Escapes _WriteExports' per-step `except Exception`, like SystemExit."""


def test_a_writer_killed_mid_batch_requeues_what_it_had_not_finished(
        monkeypatch, _workDir):
    calls = []
    real = SM.ExportText

    def dying_once(oldState, diff, stateSnapshot):
        calls.append(stateSnapshot.get(ROOT, {}).get("value"))
        if len(calls) == 1:
            raise _WriterKilled()
        return real(oldState, diff, stateSnapshot)

    monkeypatch.setattr(SM, "ExportText", dying_once)
    died = []
    monkeypatch.setattr(threading, "excepthook", died.append)

    SM.Set(f"{ROOT}.value", "A")
    _join_export()

    assert [d.exc_type for d in died] == [_WriterKilled]
    assert SM._export_running is False
    # Job A never finished its text export (and never got its JSON), so it
    # is back in the queue rather than lost.
    assert [job[0][ROOT]["value"] for job in SM._export_jobs] == ["A"]

    SM.Set(f"{ROOT}.value", "B")
    _join_export()

    # The next writer runs the re-queued job first, then the new one.
    assert calls == ["A", "A", "B"]
    assert SM._export_jobs == []
    assert _program_state(_workDir)[ROOT]["value"] == "B"


def test_the_writer_is_not_a_daemon():
    SM.Set(f"{ROOT}.value", "daemon?")
    assert SM._export_thread is not None
    # Interpreter shutdown joins non-daemon threads only; that join is what
    # gets the last state onto disk when the app exits.
    assert SM._export_thread.daemon is False
    _join_export()


_SHUTDOWN_SCRIPT = r"""
import sys, threading
sys.path.insert(0, {root!r})
import src.core.StateManager as module
from src.core.StateManager import StateManager as SM

# Every program_state.json write waits until the main thread is done, so
# the exports below can only complete after it has finished.
mainDone = threading.Event()
real = module._atomic_write_bytes
def held_write(path, data):
    mainDone.wait(60)
    real(path, data)
module._atomic_write_bytes = held_write

SM.Initialize()
for value in range(1, 6):
    SM.Set("probe.value", value)
print("pending=" + str(SM._export_running))
mainDone.set()
# No join: the process just ends, as the app does after closeEvent.
"""


def test_the_last_state_reaches_disk_when_the_process_exits(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    script = tmp_path / "exit_probe.py"
    script.write_text(_SHUTDOWN_SCRIPT.format(root=str(PROJECT_ROOT)),
                      encoding="utf-8")

    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    # Otherwise this would not have exercised the shutdown join at all.
    assert "pending=True" in result.stdout

    state = orjson.loads((tmp_path / "out" / "program_state.json")
                         .read_bytes())
    assert state["probe"]["value"] == 5
    assert (tmp_path / "out" / "probe" / "value.txt").read_text(
        encoding="utf-8") == "5"


_CONTENTION_SCRIPT = r"""
import sys, threading
sys.path.insert(0, {root!r})
import src.core.StateManager as module
from src.core.StateManager import StateManager as SM

module._EXPORT_BACKLOG_LIMIT = 1

# The writer parks inside its first program_state.json write.
parked = threading.Event()
gate = threading.Event()
first = [True]
real = module._atomic_write_bytes
def gated_write(path, data):
    if first[0]:
        first[0] = False
        parked.set()
        gate.wait(60)
    real(path, data)
module._atomic_write_bytes = gated_write

# Signals the moment SaveState blocks on a full backlog.
class SignallingCondition(threading.Condition):
    def __init__(self):
        super().__init__(threading.Lock())
        self.waiting = threading.Event()
    def wait(self, timeout=None):
        self.waiting.set()
        return super().wait(timeout)
SM._export_cond = SignallingCondition()

# StateManager.lock, plus a signal when a non-main thread goes for it.
class SignallingLock:
    def __init__(self):
        self._lock = threading.RLock()
        self.contender = threading.Event()
    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            self.contender.set()
        self._lock.acquire()
        return self
    def __exit__(self, *exc):
        self._lock.release()
SM.lock = SignallingLock()

SM.Initialize()
SM.Set("probe.value", 1)          # writer takes job 1 and parks
assert parked.wait(60)
SM.Set("probe.value", 2)          # queue [2]: at the limit

otherDone = threading.Event()
def other():
    # Plays the Flask / Start.gg thread: needs StateManager.lock, which the
    # main thread holds for as long as it waits on the backlog below.
    SM.Set("probe.other", "x")
    otherDone.set()

def helper():
    SM._export_cond.waiting.wait(60)
    threading.Thread(target=other, daemon=True).start()
    SM.lock.contender.wait(60)
    gate.set()                    # only now let the writer continue
threading.Thread(target=helper, daemon=True).start()

SM.Set("probe.value", 3)          # blocks on the backlog, holding the lock
print("blocked=" + str(SM._export_cond.waiting.is_set()))
print("other=" + str(otherDone.wait(60)))
"""


def test_a_backlog_wait_does_not_deadlock_a_thread_waiting_for_the_lock(
        tmp_path):
    """The GUI thread waits for backlog room *holding* StateManager.lock
    while another thread waits for that lock. The writer never takes the
    lock, so it drains the backlog and both go through. Run in a
    subprocess: a deadlock would otherwise hang the whole suite, and the
    timeout turns it into a failure instead."""
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    script = tmp_path / "contention_probe.py"
    script.write_text(_CONTENTION_SCRIPT.format(root=str(PROJECT_ROOT)),
                      encoding="utf-8")

    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "blocked=True" in result.stdout, (
        "SaveState never blocked on the backlog, so this tested nothing")
    assert "other=True" in result.stdout

    state = orjson.loads((tmp_path / "out" / "program_state.json")
                         .read_bytes())
    assert state["probe"] == {"value": 3, "other": "x"}
