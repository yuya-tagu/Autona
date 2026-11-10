"""SettingsManager: a launch that could not read settings.json.

LoadSettings() leaves `settings` as {} and `loadFailed` True, and the file as
it is. Until 2026-11-11 the first Set / SetMany / Unset then wrote {} plus
its key over the file: the port, LAN access, hotkeys, languages and the
access token were gone for good. Now the file is read again
(SettingsManager._Settle) -- by Initialize() at once, before anything reads
a setting (a file that cannot be opened is retried for about a second), and
by every write while it still cannot be opened:

- readable now (the failure was passing: an antivirus or sync tool holding
  the file): adopted; what Set / Unset changed in memory meanwhile is put
  back over it, in order, and the write applied over that;
- read but corrupt (not JSON, not an object): moved aside with os.replace to
  settings.json.unreadable-<time>.bak, never over an earlier copy, then
  saved from the defaults (and the changes made meanwhile);
- still cannot be opened: nothing is written -- SetMany / PrepareWrite raise
  SettingsUnreadable and change nothing; Set / Unset change memory only (in
  effect for the run), log it, and record the change to put back later;
- gone: nothing to lose, the write creates it.
"""
import ast
import datetime
import os
import types
from pathlib import Path

import orjson
import pytest
from loguru import logger

from src.core import SettingsManager as module
from src.core.SettingsManager import SettingsManager, SettingsUnreadable

SAVED = {
    "general": {"webserver_port": 6000, "webserver_listen_lan": True,
                "webserver_token": "kept-token-0123456789abcdef"},
    "hotkeys": {"load_set": "Ctrl+Alt+O"},
    "program_language": "ja",
}

CORRUPT = [
    b'{"general": {"webserver_port": 6000',     # cut short
    b"[1, 2]",                                   # JSON, not an object
    b"null",
    b'"text"',
    b"\xff\xfe\x00garbage",                      # not UTF-8
    b"",
]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("user_data")
    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(SettingsManager, "_initialized", False)
    monkeypatch.setattr(SettingsManager, "_unsaved", [])
    monkeypatch.setattr(SettingsManager, "movedAside", None)
    return tmp_path / "user_data" / "settings.json"


@pytest.fixture
def warnings():
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink)


def _files(path):
    return sorted(p.name for p in path.parent.iterdir())


def _write(writer):
    """One write of each kind; returns what it leaves in the settings."""
    if writer == "Set":
        SettingsManager.Set("general.profanity_filter", False)
        return {"general": {"profanity_filter": False}}
    if writer == "SetMany":
        SettingsManager.SetMany([("general.profanity_filter", False),
                                 ("hotkeys.swap_teams", "Ctrl+Alt+S")])
        return {"general": {"profanity_filter": False},
                "hotkeys": {"swap_teams": "Ctrl+Alt+S"}}
    SettingsManager.Unset("program_language")
    return {}


def _merged(base, written):
    merged = orjson.loads(orjson.dumps(base))
    for key, value in written.items():
        merged.setdefault(key, {}).update(value)
    return merged


WRITERS = ["Set", "SetMany", "Unset"]


# ── the launch ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", CORRUPT)
def test_loading_alone_moves_nothing_aside(isolated, content, warnings):
    # LoadSettings is the read; what to do about a failure is Initialize's
    # (below) and a write's.
    isolated.write_bytes(content)

    SettingsManager.LoadSettings()

    assert SettingsManager.loadFailed is True
    assert SettingsManager.settings == {}
    assert _files(isolated) == ["settings.json"]
    assert isolated.read_bytes() == content
    assert any("Could not read" in m for m in warnings)


def _sleeps(monkeypatch):
    """The launch's waits, recorded instead of slept."""
    slept = []
    monkeypatch.setattr(module, "time", types.SimpleNamespace(
        sleep=slept.append))
    return slept


@pytest.mark.parametrize("content", CORRUPT)
def test_the_launch_moves_a_corrupt_file_aside_at_once(
        isolated, monkeypatch, content, warnings):
    # Before anything reads a setting: the port, the languages, the hotkeys
    # are read once at launch, so the defaults they lock in are the ones
    # saved from now on. The backup loses nothing. A file that reads corrupt
    # is not waited for.
    slept = _sleeps(monkeypatch)
    isolated.write_bytes(content)

    SettingsManager.Initialize()

    backups = [name for name in _files(isolated) if name != "settings.json"]
    assert len(backups) == 1
    backup = isolated.parent / backups[0]
    assert backup.read_bytes() == content
    assert orjson.loads(isolated.read_bytes()) == {}
    assert SettingsManager.settings == {}
    assert SettingsManager.loadFailed is False
    assert os.path.samefile(SettingsManager.movedAside, backup)
    assert slept == []
    assert any(backup.name in m for m in warnings)


def test_the_launch_waits_for_a_file_held_by_another_program(
        isolated, monkeypatch):
    # An antivirus scan or a sync tool holding it for a moment: read again
    # after 0.1 s, then 0.3 s; adopted, and not written (nothing changed).
    slept = _sleeps(monkeypatch)
    content = orjson.dumps(SAVED)
    isolated.write_bytes(content)
    # LoadSettings, the read at once and the one after 0.1 s fail.
    _locked(monkeypatch, isolated, 3)

    SettingsManager.Initialize()

    assert SettingsManager.settings == SAVED
    assert SettingsManager.loadFailed is False
    assert slept == [0.1, 0.3]
    assert isolated.read_bytes() == content
    assert _files(isolated) == ["settings.json"]
    assert SettingsManager.movedAside is None


def test_the_launch_waits_about_a_second_and_then_runs_on_the_defaults(
        isolated, monkeypatch, warnings):
    slept = _sleeps(monkeypatch)
    content = orjson.dumps(SAVED)
    isolated.write_bytes(content)
    _locked(monkeypatch, isolated, 99)

    SettingsManager.Initialize()

    assert slept == list(SettingsManager.LAUNCH_RETRY_WAITS) == [0.1, 0.3,
                                                                 0.6]
    assert SettingsManager.loadFailed is True
    assert SettingsManager.settings == {}
    assert isolated.read_bytes() == content
    assert any("This run uses the defaults" in m for m in warnings)


def test_a_launch_that_reads_the_file_waits_for_nothing(isolated, monkeypatch):
    slept = _sleeps(monkeypatch)
    isolated.write_bytes(orjson.dumps(SAVED))

    SettingsManager.Initialize()

    assert SettingsManager.settings == SAVED
    assert SettingsManager.loadFailed is False
    assert slept == []


def test_a_launch_that_read_the_file_does_not_read_it_again(isolated):
    isolated.write_bytes(orjson.dumps(SAVED))
    SettingsManager.LoadSettings()
    # Edited on disk while the app runs: memory is what is saved, as ever.
    isolated.write_bytes(b"{}")

    SettingsManager.Set("general.profanity_filter", False)

    assert orjson.loads(isolated.read_bytes()) == _merged(
        SAVED, {"general": {"profanity_filter": False}})


# ── readable at the first write: adopted ─────────────────────────────────

def _locked(monkeypatch, path, times):
    """The first `times` opens of settings.json fail as a file held by
    another program does on Windows; the next ones go through."""
    left = [times]

    def flaky(file, *args, **kwargs):
        if (isinstance(file, str) and left[0] > 0
                and os.path.abspath(file) == os.path.abspath(path)):
            left[0] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return open(file, *args, **kwargs)

    monkeypatch.setattr(module, "open", flaky, raising=False)


def _lockedOnce(monkeypatch, path):
    _locked(monkeypatch, path, 1)


@pytest.mark.parametrize("writer", WRITERS)
def test_a_passing_failure_at_launch_is_read_again_and_kept(
        isolated, monkeypatch, writer):
    isolated.write_bytes(orjson.dumps(SAVED))
    _lockedOnce(monkeypatch, isolated)
    SettingsManager.LoadSettings()
    assert SettingsManager.loadFailed is True
    assert SettingsManager.settings == {}

    written = _write(writer)

    expected = _merged(SAVED, written)
    if writer == "Unset":
        expected.pop("program_language")
    assert orjson.loads(isolated.read_bytes()) == expected
    assert SettingsManager.settings == expected
    assert SettingsManager.loadFailed is False
    assert _files(isolated) == ["settings.json"]


# ── corrupt at the first write: moved aside ──────────────────────────────

@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("content", CORRUPT)
def test_a_corrupt_file_is_moved_aside_before_the_first_write(
        isolated, writer, content, warnings):
    isolated.write_bytes(content)
    SettingsManager.LoadSettings()

    written = _write(writer)

    backups = [name for name in _files(isolated) if name != "settings.json"]
    assert len(backups) == 1
    backup = isolated.parent / backups[0]
    assert backup.name.startswith("settings.json.unreadable-")
    assert backup.name.endswith(".bak")
    assert backup.read_bytes() == content
    # Only the write, over the defaults.
    assert orjson.loads(isolated.read_bytes()) == written
    assert SettingsManager.settings == written
    assert SettingsManager.loadFailed is False
    assert any(backup.name in m for m in warnings)


def test_prepare_write_leaves_a_settings_file_even_with_nothing_written(
        isolated):
    # AppSettings / HotkeyRules settle first and may then write nothing (the
    # same values): settings.json is there all the same.
    isolated.write_bytes(b"{broken")
    SettingsManager.LoadSettings()

    SettingsManager.PrepareWrite()

    assert orjson.loads(isolated.read_bytes()) == {}
    assert len(_files(isolated)) == 2
    assert SettingsManager.loadFailed is False


class _Frozen(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 11, 12, 0, 0)


def test_a_backup_never_replaces_an_earlier_one(isolated, monkeypatch):
    monkeypatch.setattr(module, "datetime",
                        types.SimpleNamespace(datetime=_Frozen))
    stamp = "settings.json.unreadable-20261111-120000"
    earlier = {f"{stamp}.bak": b"first", f"{stamp}-2.bak": b"second"}
    for name, content in earlier.items():
        (isolated.parent / name).write_bytes(content)

    for content in (b"third", b"fourth"):
        isolated.write_bytes(content)
        SettingsManager.LoadSettings()
        SettingsManager.Set("a", 1)

    for name, content in earlier.items():
        assert (isolated.parent / name).read_bytes() == content
    assert (isolated.parent / f"{stamp}-3.bak").read_bytes() == b"third"
    assert (isolated.parent / f"{stamp}-4.bak").read_bytes() == b"fourth"


# ── cannot be opened: nothing written ────────────────────────────────────

def _unopenable(path):
    # Opening a directory fails with an OSError other than "not found"
    # (PermissionError on Windows, IsADirectoryError elsewhere), as a file
    # another program holds exclusively does.
    path.mkdir()


@pytest.mark.parametrize("writer", ["Set", "Unset"])
def test_set_and_unset_change_memory_only_while_it_cannot_be_opened(
        isolated, writer, warnings):
    # In effect for this run (what the operator just set is what the app
    # shows and does), not saved, and kept to put back over the file.
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.settings = {"program_language": "en"}

    written = _write(writer)   # no error: their callers do not expect one

    assert SettingsManager.settings == (
        {"program_language": "en", **written} if writer == "Set" else {})
    assert SettingsManager._unsaved == (
        [("general.profanity_filter", False)] if writer == "Set"
        else [("program_language", module._ABSENT)])
    assert SettingsManager.loadFailed is True
    assert isolated.is_dir()
    assert _files(isolated) == ["settings.json"]
    assert any("for this run only and was not saved" in m for m in warnings)


@pytest.mark.parametrize("write", [
    lambda: SettingsManager.SetMany([("general.profanity_filter", False)]),
    SettingsManager.PrepareWrite,
])
def test_set_many_and_prepare_write_raise_while_it_cannot_be_opened(
        isolated, write):
    _unopenable(isolated)
    SettingsManager.LoadSettings()

    with pytest.raises(SettingsUnreadable) as raised:
        write()

    assert isinstance(raised.value, OSError)
    assert SettingsManager.settings == {}
    assert SettingsManager.loadFailed is True
    assert _files(isolated) == ["settings.json"]


def test_an_empty_set_many_does_not_try(isolated):
    _unopenable(isolated)
    SettingsManager.LoadSettings()

    SettingsManager.SetMany([])

    assert SettingsManager.loadFailed is True


def test_the_next_write_tries_again(isolated):
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.Set("a", 1)
    assert SettingsManager.loadFailed is True

    isolated.rmdir()
    isolated.write_bytes(orjson.dumps(SAVED))
    SettingsManager.Set("a", 2)

    assert orjson.loads(isolated.read_bytes()) == {**SAVED, "a": 2}
    assert SettingsManager.loadFailed is False


def test_a_file_gone_since_the_launch_is_written(isolated):
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    isolated.rmdir()

    SettingsManager.Set("a", 1)

    assert orjson.loads(isolated.read_bytes()) == {"a": 1}
    assert SettingsManager.loadFailed is False


def test_a_corrupt_file_that_cannot_be_moved_aside_is_not_written(
        isolated, monkeypatch):
    isolated.write_bytes(b"{broken")
    SettingsManager.LoadSettings()

    def refuse(src, dst):
        raise PermissionError(13, "in use")

    with monkeypatch.context() as patched:
        patched.setattr(module.os, "replace", refuse)
        with pytest.raises(SettingsUnreadable):
            SettingsManager.SetMany([("a", 1)])
        assert SettingsManager.settings == {}
        SettingsManager.Set("b", 2)   # memory only, as for an unopenable one

    assert isolated.read_bytes() == b"{broken"
    assert _files(isolated) == ["settings.json"]
    assert SettingsManager.settings == {"b": 2}
    assert SettingsManager.loadFailed is True


# ── changes made while it could not be opened: put back ──────────────────

def _readable(path, content):
    path.rmdir()
    path.write_bytes(orjson.dumps(content))


TRIGGERS = {
    "Set": lambda: SettingsManager.Set("zz", 1),
    "SetMany": lambda: SettingsManager.SetMany([("zz", 1)]),
    "Unset": lambda: SettingsManager.Unset("not.there"),
    "PrepareWrite": SettingsManager.PrepareWrite,
}


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_changes_made_while_it_could_not_be_opened_are_put_back_over_it(
        isolated, trigger):
    # What this run changed wins over what is saved; the rest of the file is
    # kept. Any write puts them back and saves, PrepareWrite too (with
    # nothing of its own to write, as AppSettings may).
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.Set("general.profanity_filter", False)
    SettingsManager.Unset("program_language")
    SettingsManager.Set("hotkeys.swap_teams", "Ctrl+Alt+S")
    _readable(isolated, SAVED)

    TRIGGERS[trigger]()

    expected = _merged(SAVED, {"general": {"profanity_filter": False},
                               "hotkeys": {"swap_teams": "Ctrl+Alt+S"}})
    expected.pop("program_language")
    if trigger in ("Set", "SetMany"):
        expected["zz"] = 1
    assert orjson.loads(isolated.read_bytes()) == expected
    assert SettingsManager.settings == expected
    assert SettingsManager.loadFailed is False
    assert SettingsManager._unsaved == []


ON_FILE = {"a": 0, "p": {"old": True, "c": 0}, "other": "kept"}
P = ON_FILE["p"]


@pytest.mark.parametrize("changes, expected", [
    # The same key changed again: the last change is the one put back.
    ([("Set", "a", 1), ("Set", "a", 2)],
     {"a": 2, "p": P, "other": "kept"}),
    ([("Set", "a", 1), ("Unset", "a")],
     {"p": P, "other": "kept"}),
    ([("Unset", "a"), ("Set", "a", 3)],
     {"a": 3, "p": P, "other": "kept"}),
    # A parent and its child, in either order: as it was in memory.
    ([("Set", "p.c", 1), ("Set", "p", {"x": 1})],
     {"a": 0, "p": {"x": 1}, "other": "kept"}),
    ([("Set", "p", {"x": 1}), ("Set", "p.c", 2)],
     {"a": 0, "p": {"x": 1, "c": 2}, "other": "kept"}),
    ([("Unset", "p"), ("Set", "p.c", 1)],
     {"a": 0, "p": {"c": 1}, "other": "kept"}),
    ([("Set", "p.c", 1), ("Unset", "p")],
     {"a": 0, "other": "kept"}),
    # A child alone keeps the file's other children.
    ([("Set", "p.c", 1)],
     {"a": 0, "p": {"old": True, "c": 1}, "other": "kept"}),
    ([("Set", "p.c", 1), ("Unset", "p.old")],
     {"a": 0, "p": {"c": 1}, "other": "kept"}),
], ids=["set-set", "set-unset", "unset-set", "child-parent", "parent-child",
        "unset-parent-child", "child-unset-parent", "child", "two-children"])
def test_changes_are_put_back_in_the_order_they_were_made(
        isolated, changes, expected):
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    for change in changes:
        getattr(SettingsManager, change[0])(*change[1:])
    _readable(isolated, ON_FILE)

    SettingsManager.PrepareWrite()

    assert orjson.loads(isolated.read_bytes()) == expected
    assert SettingsManager.settings == expected


def test_what_is_recorded_is_bounded_by_the_keys_changed(isolated):
    # A long run that cannot open the file keeps one entry per key: a later
    # change of a key, or of its parent, replaces what was recorded for it.
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    for value in range(5):
        SettingsManager.Set("a", value)
    SettingsManager.Set("p.c", 1)
    SettingsManager.Set("p.d", 2)
    SettingsManager.Set("p", {"x": 1})
    SettingsManager.Set("p.e", 3)

    assert SettingsManager._unsaved == [("a", 4), ("p", {"x": 1, "e": 3}),
                                        ("p.e", 3)]


def test_a_file_moved_aside_keeps_the_changes_made_meanwhile(isolated):
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.Set("a", 1)
    isolated.rmdir()
    isolated.write_bytes(b"{broken")

    SettingsManager.Set("b", 2)

    assert orjson.loads(isolated.read_bytes()) == {"a": 1, "b": 2}
    assert SettingsManager._unsaved == []
    assert SettingsManager.movedAside is not None
    assert Path(SettingsManager.movedAside).read_bytes() == b"{broken"


def test_a_file_gone_keeps_the_changes_made_meanwhile(isolated):
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.Set("a", 1)
    isolated.rmdir()

    SettingsManager.PrepareWrite()   # saves them, with nothing of its own

    assert orjson.loads(isolated.read_bytes()) == {"a": 1}
    assert SettingsManager._unsaved == []
    assert SettingsManager.loadFailed is False


def test_a_change_that_cannot_be_put_back_leaves_what_the_file_holds(
        isolated, warnings):
    # Hand-edited: not an object on the way down to the key.
    _unopenable(isolated)
    SettingsManager.LoadSettings()
    SettingsManager.Set("general.profanity_filter", False)
    SettingsManager.Set("a", 1)
    _readable(isolated, {"general": 5})

    SettingsManager.Set("b", 2)

    assert orjson.loads(isolated.read_bytes()) == {"general": 5, "a": 1,
                                                   "b": 2}
    assert any("could not be put back" in m for m in warnings)


# ── Unset of what is not there ───────────────────────────────────────────

def test_unsetting_what_is_not_there_saves_nothing(isolated, monkeypatch):
    # ColorService clears up to six feature overrides per color pick; with
    # no guard of its own reading the stand-in, this is what keeps an
    # untouched feature from a file write and an empty node.
    isolated.write_bytes(orjson.dumps(SAVED))
    SettingsManager.LoadSettings()
    saves = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: saves.append(1))

    SettingsManager.Unset("colors.board.theme_color")
    SettingsManager.Unset("general.not_there")

    assert saves == []
    assert SettingsManager.settings == SAVED

    SettingsManager.Unset("program_language")
    assert saves == [1]


# ── a save that fails after the file was read again ──────────────────────

def _failingSave(monkeypatch):
    def fail():
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(SettingsManager, "SaveSettings", fail)


@pytest.mark.parametrize("case", ["moved aside", "changes put back"])
def test_prepare_write_that_cannot_save_what_it_read_is_settings_unreadable(
        isolated, monkeypatch, case):
    # Its callers refuse on SettingsUnreadable (AppSettings, HotkeyRules:
    # NOT_READY; v1's write_op: 409) -- a bare OSError would be a 500. What
    # was read stays in memory, and the next write saves it.
    if case == "moved aside":
        isolated.write_bytes(b"{broken")
        SettingsManager.LoadSettings()
    else:
        _unopenable(isolated)
        SettingsManager.LoadSettings()
        SettingsManager.Set("a", 1)
        _readable(isolated, SAVED)
    _failingSave(monkeypatch)

    with pytest.raises(SettingsUnreadable) as raised:
        SettingsManager.PrepareWrite()

    assert isinstance(raised.value.__cause__, OSError)
    assert SettingsManager.loadFailed is False
    assert SettingsManager.settings == (
        {} if case == "moved aside" else {**SAVED, "a": 1})


def test_set_many_puts_back_the_file_it_adopted_when_its_save_fails(
        isolated, monkeypatch):
    # Undone to what was read again, not to the empty stand-in.
    isolated.write_bytes(orjson.dumps(SAVED))
    _lockedOnce(monkeypatch, isolated)
    SettingsManager.LoadSettings()
    assert SettingsManager.loadFailed is True
    _failingSave(monkeypatch)

    with pytest.raises(OSError):
        SettingsManager.SetMany([("general.webserver_port", 7000),
                                 ("new.key", 1)])

    assert SettingsManager.settings == SAVED
    assert SettingsManager.loadFailed is False


# ── a byte order mark ────────────────────────────────────────────────────
# What Notepad (before Windows 10 1903) and PowerShell 5's Set-Content
# -Encoding UTF8 put before a file they save. orjson refuses it: a
# hand-edited settings.json read as unreadable, and the first save moved it
# aside.

BOM = b"\xef\xbb\xbf"


def test_a_file_with_a_byte_order_mark_is_read_at_launch(isolated):
    isolated.write_bytes(BOM + orjson.dumps(SAVED))

    SettingsManager.LoadSettings()

    assert SettingsManager.loadFailed is False
    assert SettingsManager.settings == SAVED
    SettingsManager.Set("general.profanity_filter", False)
    # Saved without one.
    assert not isolated.read_bytes().startswith(BOM)
    assert orjson.loads(isolated.read_bytes()) == _merged(
        SAVED, {"general": {"profanity_filter": False}})


def test_a_file_with_a_byte_order_mark_is_adopted_when_read_again(
        isolated, monkeypatch):
    isolated.write_bytes(BOM + orjson.dumps(SAVED))
    _lockedOnce(monkeypatch, isolated)
    SettingsManager.LoadSettings()
    assert SettingsManager.loadFailed is True

    SettingsManager.Set("general.profanity_filter", False)

    assert _files(isolated) == ["settings.json"]   # nothing moved aside
    assert not isolated.read_bytes().startswith(BOM)
    assert orjson.loads(isolated.read_bytes()) == _merged(
        SAVED, {"general": {"profanity_filter": False}})
    assert SettingsManager.loadFailed is False


def test_a_byte_order_mark_before_something_corrupt_is_still_corrupt(
        isolated):
    isolated.write_bytes(BOM + b"{broken")
    SettingsManager.LoadSettings()
    assert SettingsManager.loadFailed is True

    SettingsManager.Set("a", 1)

    backups = [name for name in _files(isolated) if name != "settings.json"]
    assert len(backups) == 1
    assert (isolated.parent / backups[0]).read_bytes() == BOM + b"{broken"
    assert orjson.loads(isolated.read_bytes()) == {"a": 1}


# ── the callers that read what is saved, change it and save it ───────────
# A write decided from what the launch's empty stand-in reads ({}: a list
# of no channels, a config of nothing) would replace what settings.json
# holds once SettingsManager read it again. So a block that writes under
# SettingsManager.lock -- directly, or through a function of its own module
# -- calls PrepareWrite first (as the block's first statement, or the first
# of a `try` that refuses on SettingsUnreadable).
#
# Two kinds of write, then. One whose value the operation decides (a pick,
# the state's game, a fixed True) needs nothing more: Set reads the file
# again first, or, while it cannot be opened, keeps the change for the run
# and puts it back over the file later. One whose value -- or whether it is
# written at all -- is decided from a read of what is saved (a list it
# changes, a comparison, "not decided yet") prepares under the lock, or
# does not write while loadFailed (the LAN question below).

SRC = Path(__file__).resolve().parent.parent / "src"
_WRITES = {"Set", "SetMany", "Unset"}


def _settings_call(node, names):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "SettingsManager"
            and node.func.attr in names)


def _called_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):   # self.X / cls.X / Owner.X
        return call.func.attr
    return None


def _writing_functions(tree):
    """The module's functions that write settings, directly or through
    another of them."""
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    writers = set()
    while True:
        found = {function.name for function in functions
                 if any(_settings_call(node, _WRITES)
                        or (isinstance(node, ast.Call)
                            and _called_name(node) in writers)
                        for node in ast.walk(function))}
        if found <= writers:
            return writers
        writers |= found


def _prepares_first(block):
    first = block.body[0]
    if isinstance(first, ast.Try):
        first = first.body[0]
    return (isinstance(first, ast.Expr)
            and _settings_call(first.value, {"PrepareWrite"}))


def _locked_writes():
    """{(module, function): prepares first} for every `with
    SettingsManager.lock:` block that writes, in src/ (but SettingsManager
    itself)."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "SettingsManager.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        writers = _writing_functions(tree)
        for function in ast.walk(tree):
            if not isinstance(function,
                              (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for block in ast.walk(function):
                if not isinstance(block, ast.With) or not any(
                        isinstance(item.context_expr, ast.Attribute)
                        and item.context_expr.attr == "lock"
                        and isinstance(item.context_expr.value, ast.Name)
                        and item.context_expr.value.id == "SettingsManager"
                        for item in block.items):
                    continue
                writes = any(
                    _settings_call(node, _WRITES)
                    or (isinstance(node, ast.Call)
                        and _called_name(node) in writers)
                    for statement in block.body
                    for node in ast.walk(statement))
                if writes:
                    key = (path.relative_to(SRC).as_posix(), function.name)
                    found[key] = found.get(key, True) and _prepares_first(
                        block)
    return found


def test_a_block_that_writes_under_the_settings_lock_prepares_first():
    found = _locked_writes()
    # The scan sees the callers it is about (not vacuous).
    assert {
        ("core/AppSettings.py", "_Update"),
        ("core/HotkeyRules.py", "Update"),
        ("core/StreamService.py", "AddChannel"),
        ("core/StreamService.py", "UpdateChannel"),
        ("core/StreamService.py", "DeleteChannel"),
        ("core/StreamService.py", "ResetOrgAssets"),
        ("core/StreamService.py", "SetThumbnailOptions"),
        ("core/ThumbnailService.py", "Update"),
        ("core/ThumbnailService.py", "Reset"),
        ("core/ThumbnailService.py", "ImportBackground"),
        ("core/ResultService.py", "SetLayout"),
        ("AutonaDesignerService.py", "_current_pairing_code"),
    } <= set(found)
    assert [key for key, prepared in found.items() if not prepared] == []


def test_the_lan_question_is_not_asked_while_settings_json_is_unread():
    # "Not decided yet" read from the stand-in is not the user's: the
    # answer, kept for the run and put back over the file, would replace the
    # one it holds. src/Autona.py cannot be imported in a test (its import
    # is the startup), so its condition is pinned here.
    tree = ast.parse((SRC / "Autona.py").read_text(encoding="utf-8"))
    [function] = [node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_StartWebServer"]
    asks = [node for node in ast.walk(function)
            if isinstance(node, ast.If)
            and any(_settings_call(call, {"Get"})
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "general.webserver_listen_lan"
                    for call in ast.walk(node.test))]
    assert len(asks) == 1
    assert any(isinstance(node, ast.Attribute) and node.attr == "loadFailed"
               and isinstance(node.value, ast.Name)
               and node.value.id == "SettingsManager"
               for node in ast.walk(asks[0].test))
