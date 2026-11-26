"""ZipSafety (ADR-009 C.1 / C.3, lane L4): the zip's checks before
anything is unpacked, the unpacking, and the swap with its put-back."""

import io
import os
import stat
import zipfile

import pytest

from src.core import ZipSafety
from src.core.ZipSafety import (BAD_NAME, DUPLICATE_NAME, ENCRYPTED,
                                EXPANDED_TOO_LARGE, LINK, NOT_ASCII,
                                RESERVED_NAME, TOO_MANY_ENTRIES, UNSAFE_PATH)


def _zip(entries, *, mutate=None):
    """A zip in memory: ``entries`` is [(name, bytes)] (bytes None: a
    folder entry). ``mutate(info)`` may change each ZipInfo."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            if data is None:
                info.external_attr = 0o40755 << 16 | 0x10
            if mutate:
                mutate(info)
            archive.writestr(info, b"" if data is None else data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def _inspect(entries, **kwargs):
    kwargs.setdefault("maxEntries", 100)
    kwargs.setdefault("maxExpandedBytes", 10 ** 6)
    return ZipSafety.Inspect(_zip(entries), **kwargs)


@pytest.mark.parametrize("name, problem", [
    ("/abs/a", UNSAFE_PATH),
    ("\\abs\\a", UNSAFE_PATH),
    ("C:/a", UNSAFE_PATH),
    ("a/../b", UNSAFE_PATH),
    ("a\\..\\b", UNSAFE_PATH),
    ("a/file.txt:stream", UNSAFE_PATH),
    ("a/CON", RESERVED_NAME),
    ("a/nul.txt", RESERVED_NAME),
    ("a/com1.dll", RESERVED_NAME),
    ("a/LPT9", RESERVED_NAME),
    ("a/trailing.", BAD_NAME),
    ("a/trailing ", BAD_NAME),
    ("a//b", BAD_NAME),
    ("./a", BAD_NAME),
    ("a/q?.txt", BAD_NAME),
    ("a/tab\tname", BAD_NAME),
    ("a/caf\u00e9", NOT_ASCII),
])
def test_a_name_that_leaves_or_cannot_be_written_is_a_problem(name, problem):
    assert ZipSafety.NameProblem(name) == (None, problem)
    assert _inspect([(name, b"x")]).problems == (problem,)


def test_backslashes_are_separators_and_a_folder_slash_is_dropped():
    assert ZipSafety.NameProblem("x\\bin\\a.exe") == (("x", "bin", "a.exe"),
                                                      None)
    assert ZipSafety.NameProblem("x/bin/") == (("x", "bin"), None)
    plan = _inspect([("x\\bin\\a.exe", b"abc"), ("x/bin/", None)])
    assert plan.problems == ()
    assert [entry.parts for entry in plan.entries] == [("x", "bin", "a.exe"),
                                                       ("x", "bin")]
    assert plan.expanded_bytes == 3


def test_non_ascii_is_allowed_when_asked():
    assert ZipSafety.NameProblem("a/caf\u00e9", asciiOnly=False) == (
        ("a", "caf\u00e9"), None)


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_duplicates_case_folded_and_across_separators_are_refused():
    assert _inspect([("x/A.txt", b"1"), ("x/a.TXT", b"2")]).problems == (
        DUPLICATE_NAME,)
    assert _inspect([("x/a.txt", b"1"), ("x\\a.txt", b"2")]).problems == (
        DUPLICATE_NAME,)
    assert _inspect([("x/bin/", None), ("x/bin/", None)]).problems == (
        DUPLICATE_NAME,)


def test_a_file_that_another_entry_needs_as_its_folder_is_refused():
    assert _inspect([("x/bin", b"1"), ("x/bin/a", b"2")]).problems == (
        DUPLICATE_NAME,)
    assert _inspect([("x/bin/a", b"2"), ("x/bin", b"1")]).problems == (
        DUPLICATE_NAME,)


def test_a_unix_symlink_and_a_windows_reparse_point_are_links():
    def unixLink(info):
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16

    def reparse(info):
        info.create_system = 0
        info.external_attr = 0x400

    for mutate in (unixLink, reparse):
        plan = ZipSafety.Inspect(_zip([("x/link", b"target")], mutate=mutate),
                                 maxEntries=10, maxExpandedBytes=100)
        assert plan.problems == (LINK,)


def test_an_encrypted_entry_is_refused():
    archive = _zip([("x/a", b"1")])
    # zipfile writes no encrypted entry: mark the read central directory.
    archive.infolist()[0].flag_bits |= 0x1
    plan = ZipSafety.Inspect(archive, maxEntries=10, maxExpandedBytes=100)
    assert plan.problems == (ENCRYPTED,)


def test_the_entry_count_and_the_declared_size_have_ceilings():
    entries = [(f"x/{index}", b"1234") for index in range(5)]
    assert _inspect(entries, maxEntries=4).problems == (TOO_MANY_ENTRIES,)
    assert _inspect(entries, maxEntries=5).problems == ()
    assert _inspect(entries, maxExpandedBytes=19).problems == (
        EXPANDED_TOO_LARGE,)
    assert _inspect(entries, maxExpandedBytes=20).problems == ()


def test_each_problem_is_reported_once_in_the_order_found():
    plan = _inspect([("/a", b""), ("x/CON", b""), ("/b", b"")])
    assert plan.problems == (UNSAFE_PATH, RESERVED_NAME)


def test_longest_path_counts_under_the_root_after_strip(tmp_path):
    plan = _inspect([("x/bin/abc.exe", b"1"), ("x/a", b"1")])
    root = str(tmp_path / "plugins" / "x")
    assert ZipSafety.LongestPath(root, plan.entries, strip=1) == len(
        os.path.join(root, "bin", "abc.exe"))
    assert ZipSafety.LongestPath(root, (), strip=1) == len(root)


def test_extract_strips_and_writes_inside_the_target(tmp_path):
    archive = _zip([("x/", None), ("x/bin/", None), ("x/bin/a.exe", b"exe"),
                    ("x/plugin.json", b"{}")])
    plan = ZipSafety.Inspect(archive, maxEntries=10, maxExpandedBytes=100)
    target = tmp_path / "x.new"
    written = ZipSafety.Extract(archive, plan.entries, str(target), strip=1)
    assert written == 5
    assert (target / "bin" / "a.exe").read_bytes() == b"exe"
    assert (target / "plugin.json").read_bytes() == b"{}"


def test_extract_never_writes_over_an_existing_file(tmp_path):
    archive = _zip([("x/a", b"new")])
    plan = ZipSafety.Inspect(archive, maxEntries=10, maxExpandedBytes=100)
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        ZipSafety.Extract(archive, plan.entries, str(tmp_path / "t"), strip=1)
    assert (tmp_path / "t" / "a").read_bytes() == b"old"


def test_extract_stops_when_asked(tmp_path):
    archive = _zip([("x/a", b"1"), ("x/b", b"2")])
    plan = ZipSafety.Inspect(archive, maxEntries=10, maxExpandedBytes=100)
    with pytest.raises(ZipSafety.ZipStopped):
        ZipSafety.Extract(archive, plan.entries, str(tmp_path / "t"),
                          strip=1, step=lambda: True)


# ── the swap ────────────────────────────────────────────────────────────

def _folder(path, content):
    path.mkdir(parents=True)
    (path / "file").write_text(content)


def _paths(tmp_path):
    current = tmp_path / "p"
    return (tmp_path / "p.new", current, tmp_path / "p.old")


def test_swap_moves_the_new_folder_in_and_removes_the_old(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    _folder(current, "old")
    assert ZipSafety.Swap(str(new), str(current), str(old)) is True
    assert (current / "file").read_text() == "new"
    assert not new.exists() and not old.exists()


def test_swap_without_a_current_folder(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    assert ZipSafety.Swap(str(new), str(current), str(old)) is False
    assert (current / "file").read_text() == "new"


def test_a_failing_commit_puts_the_old_folder_back(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    _folder(current, "old")

    def commit():
        raise OSError("the record could not be written")

    with pytest.raises(OSError):
        ZipSafety.Swap(str(new), str(current), str(old), commit=commit)
    assert (current / "file").read_text() == "old"
    assert not new.exists() and not old.exists()


def test_a_failing_commit_on_a_first_install_leaves_nothing(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    with pytest.raises(RuntimeError):
        ZipSafety.Swap(str(new), str(current), str(old),
                       commit=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert not current.exists() and not new.exists() and not old.exists()


def test_a_failing_move_in_puts_the_old_folder_back(tmp_path, monkeypatch):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    _folder(current, "old")
    real = os.rename

    def rename(source, target):
        if os.path.abspath(source) == str(new):
            raise OSError("in use")
        real(source, target)

    monkeypatch.setattr(ZipSafety.os, "rename", rename)
    with pytest.raises(OSError):
        ZipSafety.Swap(str(new), str(current), str(old))
    assert (current / "file").read_text() == "old"
    assert not old.exists()
    assert new.exists()


def test_a_rename_held_by_an_antivirus_is_tried_again(tmp_path, monkeypatch):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    real, calls, slept = os.rename, [], []

    def rename(source, target):
        calls.append(source)
        if len(calls) < 3:
            raise PermissionError("held")
        real(source, target)

    monkeypatch.setattr(ZipSafety.os, "rename", rename)
    ZipSafety.Swap(str(new), str(current), str(old), sleep=slept.append)
    assert (current / "file").read_text() == "new"
    assert slept == [0.2, 0.4]


def test_a_crashed_swaps_old_folder_is_put_back_first(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "new")
    _folder(old, "old")
    ZipSafety.Swap(str(new), str(current), str(old))
    assert (current / "file").read_text() == "new"
    assert not old.exists()


def test_clean_swap_removes_the_leftovers_and_puts_old_back(tmp_path):
    new, current, old = _paths(tmp_path)
    _folder(new, "half")
    _folder(old, "old")
    part = tmp_path / "p.part"
    part.write_bytes(b"partial")
    ZipSafety.CleanSwap(str(current))
    assert (current / "file").read_text() == "old"
    assert not new.exists() and not old.exists() and not part.exists()


def test_clean_swap_removes_old_when_the_folder_is_there(tmp_path):
    _new, current, old = _paths(tmp_path)
    _folder(current, "current")
    _folder(old, "old")
    ZipSafety.CleanSwap(str(current))
    assert (current / "file").read_text() == "current"
    assert not old.exists()
