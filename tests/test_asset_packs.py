"""Game asset packs: core's AssetPackService (ADR-005 S-final SF-3b).

The index and the downloads go through a fake ``requests`` (no network);
the install's 7z files are real, made with py7zr, split like upstream's. A
7z with a name py7zr refuses to write (``../x``) is a fake py7zr module.
Every test runs in tmp_path (user_data/games is CWD-relative) on a stepped
TaskExecutor of its own: a job runs when the test says (``run_next``).
"""
import ast
import json
import os
import shutil
import stat
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import py7zr as _py7zr   # the real one: a test may fake the service's
import pytest
import requests

from src.core import AssetPackService as assets
from src.core.AssetPackService import AssetPackError, AssetPackService
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.Observable import ObserverChannel
from src.core.StatusService import StatusService
from src.core.TaskExecutor import NestedSubmitError, TaskExecutor

WAIT = 30


# ── helpers ──────────────────────────────────────────────────────────────

def _write(path, data=b"x"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def _config(**fields):
    return json.dumps(fields).encode()


def _game(root, name, packs=("full",), version="1", others=(), marker=b"x"):
    """A game folder: base_files/config.json, and each pack with its
    config.json and an image; ``others``: packs the app does not use."""
    folder = Path(root) / name
    _write(folder / "base_files" / "config.json",
           _config(name=name.upper(), version=version))
    _write(folder / "base_files" / "logo.png", marker)
    for pack in tuple(packs) + tuple(others):
        _write(folder / pack / "config.json", _config(version=version))
        _write(folder / pack / "a.png", marker)
    return folder


def _zip(path, entries):
    """A ZIP holding ``entries`` (name -> bytes) under their names as given
    (ZipInfo: no clean-up of ``..`` or a leading ``/``)."""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(zipfile.ZipInfo(name), data)
    return str(path)


def _game_entries(name, packs=("full",), others=(), prefix=None):
    prefix = name + "/" if prefix is None else prefix
    entries = {
        f"{prefix}base_files/config.json": _config(name=name.upper()),
        f"{prefix}base_files/logo.png": b"logo",
    }
    for pack in tuple(packs) + tuple(others):
        entries[f"{prefix}{pack}/config.json"] = _config(version="1")
        entries[f"{prefix}{pack}/a.png"] = b"new"
    return entries


def _files(root):
    root = Path(root)
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file()}


def _staging(games):
    games = Path(games)
    if not games.exists():
        return []
    return [p.name for p in games.iterdir()
            if p.name.startswith(assets.STAGING_PREFIX)]


class _Response:
    def __init__(self, body=b"", status=200, data=None, pieces=1,
                 onChunk=None):
        self.status_code = status
        self._body = body
        self._data = data
        self._pieces = pieces
        self._onChunk = onChunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data

    def iter_content(self, size):
        body = self._body
        step = max(1, -(-len(body) // self._pieces))
        for index, start in enumerate(range(0, len(body), step)):
            if self._onChunk is not None:
                self._onChunk(index)
            yield body[start:start + step]


class FakeNet:
    """``requests`` as the service uses it: the index at INDEX_URL, the
    release files under DOWNLOAD_BASE."""

    RequestException = requests.RequestException
    HTTPError = requests.HTTPError

    def __init__(self):
        self.index = {}
        self.indexError = None
        self.indexGate = None       # an Event the index fetch waits on
        self.indexAsked = threading.Event()
        self.files = {}
        self.pieces = 1
        self.onChunk = None
        self.failFile = None
        self.calls = []

    def get(self, url, timeout=None, stream=False, allow_redirects=False):
        self.calls.append(url)
        assert timeout, "every request has a timeout"
        if url == assets.INDEX_URL:
            self.indexAsked.set()
            if self.indexGate is not None:
                assert self.indexGate.wait(WAIT)
            if self.indexError is not None:
                raise self.indexError
            return _Response(data=json.loads(json.dumps(self.index)))
        assert url.startswith(assets.DOWNLOAD_BASE), url
        name = url[len(assets.DOWNLOAD_BASE):]
        if name == self.failFile:
            raise requests.ConnectionError("the connection was reset")
        if name not in self.files:
            return _Response(status=404)
        return _Response(self.files[name], pieces=self.pieces,
                         onChunk=self.onChunk)

    def indexFetches(self):
        return self.calls.count(assets.INDEX_URL)


def _seven_zip(path, entries):
    with _py7zr.SevenZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(data, name)
    return Path(path).read_bytes()


def _publish(net, tmp, code, pack, entries, version="1", parts=1,
             name="Game"):
    """Put one pack in the fake release (split in ``parts`` files) and the
    index."""
    data = _seven_zip(Path(tmp) / f"{code}.{pack}.7z", entries)
    step = -(-len(data) // parts)
    files = {}
    for index in range(parts):
        fileName = f"{code}.{pack}.7z.{index + 1:03d}"
        net.files[fileName] = data[index * step:(index + 1) * step]
        files[fileName] = {"size": len(net.files[fileName])}
    game = net.index.setdefault(code, {"name": name, "assets": {}})
    game["assets"][pack] = {"version": version, "files": files}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """The service on a stepped executor, in tmp_path: its own status, its
    own catalog (the scan), a fake network and a still clock."""
    previous = TaskExecutor.instance
    if previous is not None:
        assert previous.drain(WAIT)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    monkeypatch.setattr(StatusService, "_jobs", {})
    monkeypatch.setattr(StatusService, "_revision", 0)
    monkeypatch.setattr(StatusService, "_nextToken", 0)
    channel = ObserverChannel("status")
    monkeypatch.setattr(StatusService, "changes", channel)

    clock = [1000.0]
    sleeps = []
    monkeypatch.setattr(assets, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time, sleep=sleeps.append))
    net = FakeNet()
    monkeypatch.setattr(assets, "requests", net)

    catalog = GameAssetCatalog()
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog)
    scans = []

    def scan():
        scans.append(threading.current_thread().name)
        catalog.ScanGames()

    service = AssetPackService(scan=scan)
    monkeypatch.setattr(AssetPackService, "instance", service)
    executor = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", executor)

    told = []
    channel.subscribe(lambda: told.append(
        (StatusService.Snapshot(), service.Job())))

    e = SimpleNamespace(
        root=tmp_path, work=work, games=work / "user_data" / "games",
        service=service, catalog=catalog, net=net, executor=executor,
        scans=scans, told=told, clock=clock, channel=channel, sleeps=sleeps)
    try:
        yield e
    finally:
        executor.shutdown(WAIT)


def _run(env):
    assert env.executor.run_next(WAIT)
    return env.service.Job()


# ── names and paths ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name, safe", [
    ("ssbu", True), ("Street Fighter 6", True), ("a.b", True),
    ("", False), (".", False), ("..", False), (".hidden", False),
    (".autona-staging-x", False), ("a/b", False), ("a\\b", False),
    ("a:b", False), ("C:", False), ("con", False), ("NUL.txt", False),
    ("com1", False), ("trailing.", False), ("trailing ", False),
    ("tab\there", False), (None, False), (3, False),
])
def test_a_game_folder_name_is_one_safe_part(name, safe):
    assert assets.SafeName(name) is safe


@pytest.mark.parametrize("name, parts", [
    ("game/base_files/config.json", ("game", "base_files", "config.json")),
    ("game/full/", ("game", "full")),
    ("game\\full\\a.png", ("game", "full", "a.png")),
    ("../evil.txt", None),
    ("game/../../evil.txt", None),
    ("game\\..\\..\\evil.txt", None),
    ("/abs/evil.txt", None),
    ("\\abs\\evil.txt", None),
    ("C:/evil.txt", None),
    ("C:evil.txt", None),
    ("game/a.png:stream", None),
    ("game/./a.png", None),
    ("game//a.png", None),
    ("game/con/a.png", None),
    ("game/a.png.", None),
    ("game/nul\x00.png", None),
    ("", None),
    ("/", None),
])
def test_an_archive_entry_stays_inside_its_folder(name, parts):
    assert assets.ArchivePath(name) == parts


@pytest.mark.parametrize("fileName, hint", [
    ("C:\\Downloads\\ssbu.zip", "ssbu"), ("/tmp/sf6.zip", "sf6"),
    ("game.tar.zip", "game.tar"), ("", None), (None, None), (".zip", ".zip"),
])
def test_a_zip_holding_one_game_at_its_top_is_named_after_its_file(
        fileName, hint):
    assert assets.ZipNameHint(fileName) == hint


# ── probing a ZIP ────────────────────────────────────────────────────────

def test_a_zip_with_a_game_at_its_top_holds_it_under_the_files_name(env):
    path = _zip(env.root / "ssbu.zip", _game_entries("x", prefix=""))
    assert assets.ProbeZip(path) == ["ssbu"]
    assert assets.ProbeZip(path, "other") == ["other"]


def test_a_zip_with_game_folders_holds_each_of_them(env):
    entries = _game_entries("aaa") | _game_entries("bbb")
    entries["readme.txt"] = b"not a game"
    entries["ccc/full/a.png"] = b"no base_files: not a game"
    path = _zip(env.root / "two.zip", entries)
    assert assets.ProbeZip(path) == ["aaa", "bbb"]


@pytest.mark.parametrize("name, parts, verdict", [
    ("aaa/full/a.png", ("aaa", "full", "a.png"), assets._ENTRY_OK),
    ("aaa\\full\\", ("aaa", "full"), assets._ENTRY_OK),
    ("./aaa//full/a.png", ("aaa", "full", "a.png"), assets._ENTRY_OK),
    ("./", (), assets._ENTRY_OK),
    ("aaa/full/b?.png", ("aaa", "full", "b?.png"), assets._ENTRY_SKIP),
    ("aaa/full/c\x01.png", ("aaa", "full", "c\x01.png"), assets._ENTRY_SKIP),
    ("aaa/full/dot.", ("aaa", "full", "dot."), assets._ENTRY_SKIP),
    ("aaa/full/space ", ("aaa", "full", "space "), assets._ENTRY_SKIP),
    ("aaa/con/a.png", ("aaa", "con", "a.png"), assets._ENTRY_SKIP),
    ("aaa/full/<a>|\".png", ("aaa", "full", "<a>|\".png"),
     assets._ENTRY_SKIP),
    ("../evil.txt", None, assets._ENTRY_UNSAFE),
    ("aaa\\..\\..\\evil.txt", None, assets._ENTRY_UNSAFE),
    ("/abs/evil.txt", None, assets._ENTRY_UNSAFE),
    ("\\\\server\\share\\evil.txt", None, assets._ENTRY_UNSAFE),
    ("C:/evil.txt", None, assets._ENTRY_UNSAFE),
    ("aaa/a.png:stream", None, assets._ENTRY_UNSAFE),
])
def test_a_zip_entry_either_stays_inside_or_refuses_the_zip(
        name, parts, verdict):
    assert assets._ZipEntryPath(name) == (parts, verdict)


@pytest.mark.parametrize("bad", [
    "../evil.txt", "aaa/../../evil.txt", "/abs/evil.txt", "C:/evil.txt",
    "aaa/full/a.png:hidden",
])
def test_an_entry_outside_its_folder_refuses_the_whole_zip(env, bad):
    entries = _game_entries("aaa")
    entries[bad] = b"evil"
    path = _zip(env.root / "evil.zip", entries)

    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == assets.UNSAFE_ARCHIVE
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportZip(path)
    assert refused.value.code == assets.UNSAFE_ARCHIVE

    # Refused before a job: nothing queued, nothing written anywhere.
    assert env.executor.queued() == 0
    assert env.service.Job()["status"] == assets.IDLE
    assert not env.games.exists()
    assert not list(env.root.rglob("evil.txt"))
    assert not (env.root.parent / "evil.txt").exists()


@pytest.mark.parametrize("make, code", [
    (lambda root: _write(root / "x.zip", b"not a zip at all"), assets.NOT_A_ZIP),
    (lambda root: root / "missing.zip", assets.NOT_A_ZIP),
    (lambda root: _zip(root / "x.zip", {"a/b.png": b"1"}), assets.NO_GAME),
    (lambda root: _zip(root / "x.zip", _game_entries(".hidden")),
     assets.BAD_NAME),
    (lambda root: _zip(root / "con.zip", _game_entries("x", prefix="")),
     assets.BAD_NAME),
])
def test_a_zip_that_cannot_be_imported_says_why(env, make, code):
    path = str(make(env.root))
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == code


def test_a_zip_over_the_limits_is_refused(env, monkeypatch):
    path = _zip(env.root / "big.zip", _game_entries("aaa"))
    monkeypatch.setattr(assets, "MAX_ENTRIES", 3)
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == assets.TOO_LARGE

    monkeypatch.setattr(assets, "MAX_ENTRIES", 200_000)
    monkeypatch.setattr(assets, "MAX_UNPACKED_BYTES", 10)
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == assets.TOO_LARGE


# The central directory, checked from the end records before zipfile reads
# it (SF-4b-1): zipfile.ZipFile() reads the whole directory and makes an
# object of every entry before _ZipPlan can count them.

_EOCD_FIELDS = ("sig", "disk", "diskStart", "here", "entries", "size",
                "offset", "comment")


def _end_record(data):
    # (where the end record starts, its fields) of a ZIP with no comment.
    at = len(data) - 22
    assert data[at:at + 4] == b"PK\x05\x06"
    return at, dict(zip(_EOCD_FIELDS, struct.unpack("<4s4H2LH", data[at:])))


def _patch_end(path, **fields):
    data = Path(path).read_bytes()
    at, record = _end_record(data)
    record.update(fields)
    Path(path).write_bytes(
        data[:at] + struct.pack("<4s4H2LH", *record.values()))


def _make_zip64(path, entries=None, size=None, offset=None, diskNumber=0,
                disks=1):
    """The ZIP rewritten with ZIP64 end records (zipfile writes them only
    past 65535 entries or 4 GiB): its count, directory size and offset in
    the ZIP64 record (or the ones given), the old record's at "see ZIP64"."""
    data = Path(path).read_bytes()
    at, record = _end_record(data)
    count = record["entries"] if entries is None else entries
    eocd64 = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count,
        record["size"] if size is None else size,
        record["offset"] if offset is None else offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", diskNumber, at, disks)
    eocd = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF,
                       0xFFFFFFFF, 0xFFFFFFFF, 0)
    Path(path).write_bytes(data[:at] + eocd64 + locator + eocd)


def _spoil_first_header(path):
    data = bytearray(Path(path).read_bytes())
    _at, record = _end_record(bytes(data))
    data[record["offset"]:record["offset"] + 4] = b"XXXX"
    Path(path).write_bytes(bytes(data))


def _with_comment(path, comment):
    with zipfile.ZipFile(path, "a") as z:
        z.comment = comment


def _truncate_end(path):
    data = Path(path).read_bytes()
    at, _record = _end_record(data)
    Path(path).write_bytes(data[:at])


def _directory_size_over(path):
    _patch_end(path, size=assets.MAX_CENTRAL_DIRECTORY_BYTES + 1)


def _directory_before_the_file(path):
    at, _record = _end_record(Path(path).read_bytes())
    _patch_end(path, size=at + 1)


def _offset_past_the_directory(path):
    _at, record = _end_record(Path(path).read_bytes())
    _patch_end(path, offset=record["offset"] + 1)


@pytest.mark.parametrize("spoil, maxEntries, code", [
    # The end record lists more entries than MAX_ENTRIES (4 here).
    (lambda path: None, 3, assets.TOO_LARGE),
    # It lists 1; the directory holds 4 (zipfile reads to its size).
    (lambda path: _patch_end(path, entries=1, here=1), 3, assets.TOO_LARGE),
    (_directory_size_over, None, assets.TOO_LARGE),
    (_directory_before_the_file, None, assets.NOT_A_ZIP),
    (_offset_past_the_directory, None, assets.NOT_A_ZIP),
    (_spoil_first_header, None, assets.NOT_A_ZIP),
    (_truncate_end, None, assets.NOT_A_ZIP),
    (lambda path: _make_zip64(path, entries=assets.MAX_ENTRIES + 1), None,
     assets.TOO_LARGE),
    (lambda path: _make_zip64(path, size=2 ** 40), None, assets.TOO_LARGE),
    (lambda path: _make_zip64(path, diskNumber=1), None, assets.NOT_A_ZIP),
    (lambda path: _make_zip64(path, disks=2), None, assets.NOT_A_ZIP),
], ids=["entries", "entries-walked", "directory-size", "before-the-file",
        "offset-past-it", "bad-header", "no-end-record", "zip64-entries",
        "zip64-directory-size", "zip64-disk", "zip64-disks"])
def test_a_zips_central_directory_is_checked_before_zipfile_reads_it(
        env, monkeypatch, spoil, maxEntries, code):
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    spoil(path)
    if maxEntries is not None:
        monkeypatch.setattr(assets, "MAX_ENTRIES", maxEntries)

    def notRead(*args, **kwargs):
        raise AssertionError("zipfile read a refused ZIP")

    monkeypatch.setattr(assets.zipfile, "ZipFile", notRead)
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == code
    assert str(env.root) not in refused.value.message
    assert "aaa.zip" not in refused.value.message


@pytest.mark.parametrize("make", [
    lambda path: None,
    _make_zip64,
    # A stub before it (a self-extractor's): the offsets are its own.
    lambda path: Path(path).write_bytes(b"stub" + Path(path).read_bytes()),
    # The end record is then searched for (not in the last 22 bytes).
    lambda path: _with_comment(path, b"notes"),
], ids=["plain", "zip64", "prefixed", "comment"])
def test_a_sound_zip_passes_the_central_directory_check(env, make):
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    make(path)
    assert assets.ProbeZip(path, "aaa") == ["aaa"]


# _EndRecord finds the end records where zipfile does (the SF-4b-1 review):
# each crafted file read by zipfile._EndRecData (private: here only, to
# compare) and by _EndRecord -- the same record, or refused by both.

_FAKE_EOCD = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 7, 7, 99, 5, 0)
_LOCATOR = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)


def _raw_comment(path, comment):
    # The comment written after the record as is (its length set).
    data = Path(path).read_bytes()
    at, record = _end_record(data)
    record["comment"] = len(comment)
    Path(path).write_bytes(
        data[:at] + struct.pack("<4s4H2LH", *record.values()) + comment)


def _locator_alone(path):
    # A ZIP64 locator before the record, no ZIP64 record before it.
    data = Path(path).read_bytes()
    at, _record = _end_record(data)
    Path(path).write_bytes(data[:at] + _LOCATOR + data[at:])


_END_CASES = {
    "plain": (lambda path: None, "record"),
    "comment": (lambda path: _raw_comment(path, b"notes"), "record"),
    # A record's signature in the comment, after the real one: both take it.
    "fake-in-comment": (
        lambda path: _raw_comment(path, _FAKE_EOCD + b"tail"), "record"),
    "fake-ends-comment": (
        lambda path: _raw_comment(path, b"x" + _FAKE_EOCD), "record"),
    "cut-in-comment": (
        lambda path: _raw_comment(path, b"PK\x05\x06abc"), None),
    "offset-out-of-range": (
        lambda path: _patch_end(path, offset=2 ** 31), "record"),
    "zip64": (_make_zip64, "record"),
    "zip64-offset-out-of-range": (
        lambda path: _make_zip64(path, offset=2 ** 40), "record"),
    "zip64-locator-alone": (_locator_alone, "record"),
    "zip64-locator-without-room": (
        lambda path: Path(path).write_bytes(
            b"x" * 10 + _LOCATOR + _FAKE_EOCD), "refused"),
    "zip64-split": (lambda path: _make_zip64(path, disks=2), "refused"),
    # The search window: the last 65,557 bytes. A signature 65,558 bytes
    # from the end is outside it, 65,557 inside.
    "window-edge": (
        lambda path: Path(path).write_bytes(_FAKE_EOCD + b"\0" * 65536),
        None),
    "window-inside": (
        lambda path: Path(path).write_bytes(_FAKE_EOCD + b"\0" * 65535),
        "record"),
    "short": (lambda path: Path(path).write_bytes(b"PK\x05\x06"), None),
    "empty": (lambda path: Path(path).write_bytes(b""), None),
}


def _zipfiles_end(path):
    with open(path, "rb") as f:
        try:
            record = zipfile._EndRecData(f)
        except (OSError, zipfile.BadZipFile):
            return "refused"
    if record is None:
        return None
    return (record[zipfile._ECD_ENTRIES_TOTAL], record[zipfile._ECD_SIZE],
            record[zipfile._ECD_OFFSET], record[zipfile._ECD_LOCATION],
            record[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64)


def _our_end(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        try:
            return assets._EndRecord(f, f.tell())
        except AssetPackError as refused:
            assert refused.code == assets.NOT_A_ZIP
            return "refused"


@pytest.mark.parametrize("case", list(_END_CASES))
def test_the_end_records_are_found_where_zipfile_finds_them(env, case):
    craft, kind = _END_CASES[case]
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    craft(path)
    ours = _our_end(path)
    assert ours == _zipfiles_end(path)
    if kind == "record":
        assert isinstance(ours, tuple)
    else:
        assert ours == kind


def test_the_job_checks_the_central_directory_again(env, monkeypatch):
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    assert env.service.ImportZip(path)[0] == assets.STARTED
    # Swapped between the start's check and the job.
    _directory_size_over(path)

    def notRead(*args, **kwargs):
        raise AssertionError("zipfile read a refused ZIP")

    monkeypatch.setattr(assets.zipfile, "ZipFile", notRead)
    job = _run(env)

    assert job["status"] == assets.ERROR
    assert job["error"] == assets.TOO_LARGE
    assert not (env.games / "aaa").exists()


def test_a_zip_that_cannot_be_opened_is_refused_without_its_path(env):
    path = env.root / "secret-folder" / "missing.zip"
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(str(path))
    assert refused.value.code == assets.NOT_A_ZIP
    assert "secret-folder" not in refused.value.message
    assert "missing.zip" not in refused.value.message


# ── importing a ZIP ──────────────────────────────────────────────────────

def test_a_zip_import_moves_the_games_in_and_scans_once(env):
    entries = _game_entries("aaa", others=("logo_art",)) | \
        _game_entries("bbb", packs=("full", "portrait"))
    path = _zip(env.root / "games.zip", entries)

    assert env.service.ImportZip(path) == (assets.STARTED, 1)
    assert env.service.Job()["status"] == assets.RUNNING
    job = _run(env)

    assert job["status"] == assets.DONE, job
    assert job["operation"] == assets.IMPORT_ZIP
    assert job["imported"] == ["aaa", "bbb"]
    assert job["skipped"] == [] and job["failed"] == []
    assert _files(env.games / "aaa") == {
        "base_files/config.json", "base_files/logo.png",
        "full/config.json", "full/a.png"}       # logo_art left out
    assert "portrait/a.png" in _files(env.games / "bbb")
    assert len(env.scans) == 1
    assert set(env.catalog.GamesView()) == {"aaa", "bbb"}
    assert _staging(env.games) == []
    assert os.path.exists(path)                 # not the job's file


def test_the_footer_shows_the_job_and_its_outcome_is_final_before_it_drops(
        env):
    path = _zip(env.root / "ssbu.zip", _game_entries("x", prefix=""))
    env.service.ImportZip(path)
    _run(env)

    shown = [snapshot["jobs"] for snapshot, _job in env.told
             if snapshot["jobs"]]
    assert shown and all(
        jobs[0]["code"] == "assets_load"
        and jobs[0]["args"] == {"codename": "ssbu"} for jobs in shown)
    last, job = env.told[-1]
    assert last["jobs"] == []
    assert job["status"] == assets.DONE and job["imported"] == ["ssbu"]
    assert StatusService.Snapshot()["jobs"] == []


def test_an_installed_game_is_skipped_unless_it_is_to_be_replaced(env):
    _game(env.games, "aaa", marker=b"old")
    _write(env.games / "aaa" / "full" / "only-old.png")
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))

    env.service.ImportZip(path)
    job = _run(env)
    assert job["status"] == assets.DONE
    assert job["skipped"] == ["aaa"] and job["imported"] == []
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"
    assert env.scans == []          # nothing moved in: nothing to scan

    env.service.ImportZip(path, overwrite={"aaa"})
    job = _run(env)
    assert job["imported"] == ["aaa"] and job["skipped"] == []
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"new"
    assert not (env.games / "aaa" / "full" / "only-old.png").exists()
    assert len(env.scans) == 1
    assert _staging(env.games) == []


def test_the_job_checks_the_zip_again_before_it_writes(env):
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    env.service.ImportZip(path)
    # Swapped between the start's check and the job.
    evil = _game_entries("aaa")
    evil["../evil.txt"] = b"evil"
    _zip(path, evil)

    job = _run(env)

    assert job["status"] == assets.ERROR
    assert job["error"] == assets.UNSAFE_ARCHIVE
    assert not (env.games / "aaa").exists()
    assert not list(env.root.rglob("evil.txt"))
    assert env.scans == []
    assert _staging(env.games) == []


def test_an_uploaded_zip_is_removed_however_its_import_ends(env):
    good = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    env.service.ImportZip(good, removeAfter=True)
    assert os.path.exists(good)             # until the job ends
    assert _run(env)["status"] == assets.DONE
    assert not os.path.exists(good)

    bad = str(_write(env.root / "bad.zip", b"no zip"))
    with pytest.raises(AssetPackError):
        env.service.ImportZip(bad, removeAfter=True)
    assert not os.path.exists(bad)

    # BUSY excepted: the file stays the caller's (ImportUpload holds the
    # upload again; the SF-4b-1 review's M1).
    first = _zip(env.root / "bbb.zip", _game_entries("bbb"))
    env.service.ImportZip(first)
    busy = _zip(env.root / "ccc.zip", _game_entries("ccc"))
    assert env.service.ImportZip(busy, removeAfter=True)[0] == assets.BUSY
    assert os.path.exists(busy)
    _run(env)


def test_a_zip_entry_windows_cannot_write_is_left_out_alone(env):
    # The window's extractall wrote the rest of such a ZIP: so does the job,
    # and it says what it left out.
    entries = _game_entries("aaa")
    bad = ["aaa/full/b?.png", "aaa/full/c\x01.png", "aaa/full/dot.",
           "aaa/full/space ", "aaa/con/a.png"]
    for name in bad:
        entries[name] = b"bad"
    entries["read?me.txt"] = b"no game's: not imported, not listed"
    path = _zip(env.root / "aaa.zip", entries)

    assert assets.ProbeZip(path) == ["aaa"]
    env.service.ImportZip(path)
    job = _run(env)

    assert job["status"] == assets.DONE, job
    assert job["imported"] == ["aaa"]
    assert job["skippedEntries"] == bad and job["skippedEntriesMore"] == 0
    assert _files(env.games / "aaa") == {
        "base_files/config.json", "base_files/logo.png",
        "full/config.json", "full/a.png"}
    assert job["done"] == job["total"] == 4


def test_the_left_out_entries_are_named_up_to_a_limit(env, monkeypatch):
    monkeypatch.setattr(assets, "MAX_SKIPPED_ENTRIES", 2)
    entries = _game_entries("aaa")
    for index in range(5):
        entries[f"aaa/full/{index}?.png"] = b"bad"
    env.service.ImportZip(_zip(env.root / "aaa.zip", entries))
    job = _run(env)
    assert job["skippedEntries"] == ["aaa/full/0?.png", "aaa/full/1?.png"]
    assert job["skippedEntriesMore"] == 3


def test_names_are_matched_as_windows_matches_them(env):
    # Base_Files/Config.json makes a game; LOGO/CONFIG.JSON is the logo
    # pack, which the app does not use (ADR-002); FULL is the full pack.
    path = _zip(env.root / "sf6.zip", {
        "Base_Files/Config.json": _config(name="SF6"),
        "FULL/config.json": _config(version="1"),
        "FULL/a.png": b"full",
        "LOGO/CONFIG.JSON": _config(version="1"),
        "LOGO/a.png": b"logo",
        "logo/b.png": b"logo too: one folder on Windows",
    })
    assert assets.ProbeZip(path) == ["sf6"]
    env.service.ImportZip(path)
    job = _run(env)
    assert job["imported"] == ["sf6"]
    assert _files(env.games / "sf6") == {
        "Base_Files/Config.json", "FULL/config.json", "FULL/a.png"}

    folders = _zip(env.root / "two.zip", {
        "AAA/BASE_FILES/CONFIG.JSON": _config(name="AAA"),
        "aaa/full/a.png": b"the same game's, as Windows extracts it",
    })
    assert assets.ProbeZip(folders) == ["AAA"]
    env.service.ImportZip(folders)
    job = _run(env)
    assert job["imported"] == ["AAA"]
    assert _files(env.games / "AAA") == {
        "BASE_FILES/CONFIG.JSON", "full/a.png"}


def test_two_games_differing_only_in_case_are_refused(env, monkeypatch):
    path = _zip(env.root / "two.zip",
                _game_entries("ssbu") | _game_entries("SSBU"))
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeZip(path)
    assert refused.value.code == assets.BAD_NAME
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportZip(path)
    assert refused.value.code == assets.BAD_NAME
    assert env.executor.queued() == 0

    # A folder cannot hold both on Windows; a case-sensitive one could.
    monkeypatch.setattr(assets, "_FolderGames", lambda root: [
        ("ssbu", os.path.join(root, "ssbu")),
        ("SSBU", os.path.join(root, "SSBU"))])
    with pytest.raises(AssetPackError) as refused:
        assets.ProbeFolder(str(env.root))
    assert refused.value.code == assets.BAD_NAME


def _asides(games):
    games = Path(games)
    if not games.exists():
        return []
    return sorted(p.name for p in games.iterdir()
                  if p.name.startswith(assets.REPLACED_PREFIX))


def _refuse_moves(monkeypatch, refuse):
    """os.replace failing (as a file in use does on Windows) for the moves
    ``refuse(source, target)`` names; the refused tries, recorded."""
    real = os.replace
    tried = []

    def replace(source, target):
        if refuse(str(source), str(target)):
            tried.append((str(source), str(target)))
            raise PermissionError(13, "in use", str(source))
        real(source, target)

    monkeypatch.setattr(os, "replace", replace)
    return tried


def test_a_replaced_game_is_put_back_when_the_new_one_cannot_go_in(
        env, monkeypatch):
    _game(env.games, "aaa", marker=b"old")
    tried = _refuse_moves(
        monkeypatch, lambda source, target: assets.STAGING_PREFIX in source)

    env.service.ImportZip(_zip(env.root / "aaa.zip", _game_entries("aaa")),
                          overwrite=True)
    job = _run(env)

    assert job["status"] == assets.DONE
    assert job["imported"] == [] and [f["name"] for f in job["failed"]] == [
        "aaa"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"
    # Retried a few times (a scanner holding a file), then given up.
    assert len(tried) == assets._REPLACE_ATTEMPTS
    assert env.sleeps == [assets._REPLACE_BACKOFF * 2 ** i
                          for i in range(assets._REPLACE_ATTEMPTS - 1)]
    assert _asides(env.games) == [] and _staging(env.games) == []


def test_a_game_that_cannot_be_moved_aside_is_left_as_it_was(
        env, monkeypatch):
    _game(env.games, "aaa", marker=b"old")
    _refuse_moves(monkeypatch,
                  lambda source, target: assets.REPLACED_PREFIX in target)
    env.service.ImportZip(_zip(env.root / "aaa.zip", _game_entries("aaa")),
                          overwrite=True)
    job = _run(env)
    assert [f["name"] for f in job["failed"]] == ["aaa"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"
    assert _asides(env.games) == [] and _staging(env.games) == []


def test_a_replaced_game_that_cannot_be_put_back_is_kept_then_restored(
        env, monkeypatch):
    _game(env.games, "aaa", marker=b"old")
    blocked = [True]
    _refuse_moves(monkeypatch, lambda source, target: blocked[0] and (
        assets.STAGING_PREFIX in source or assets.REPLACED_PREFIX in source))

    env.service.ImportZip(_zip(env.root / "aaa.zip", _game_entries("aaa")),
                          overwrite=True)
    job = _run(env)

    # Neither in place nor put back: kept outside the staging folder (which
    # the job removed), never removed, and named.
    assert [f["name"] for f in job["failed"]] == ["aaa"]
    kept = _asides(env.games)
    assert len(kept) == 1 and kept[0] in job["failed"][0]["message"]
    assert (env.games / kept[0] / "aaa" / "full" / "a.png").read_bytes() == \
        b"old"
    assert not (env.games / "aaa").exists()
    assert _staging(env.games) == []

    # The next job puts it back first (its place is empty), and shows it.
    blocked[0] = False
    _game(env.root / "src", "bbb")
    env.service.ImportFolder(str(env.root / "src"))
    job = _run(env)
    assert job["imported"] == ["bbb"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"
    assert _asides(env.games) == []
    assert {"aaa", "bbb"} <= set(env.catalog.GamesView())


def test_what_a_dead_job_moved_aside_is_put_back_by_the_next_one(env):
    left = env.games / (assets.REPLACED_PREFIX + "aaa-dead")
    _game(left, "aaa", marker=b"old")
    _game(env.games, "ccc")
    env.service.ImportZip(_zip(env.root / "ccc.zip", _game_entries("ccc")))
    job = _run(env)
    assert job["skipped"] == ["ccc"] and job["imported"] == []
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"
    assert not left.exists()
    # Put back is moved in: scanned, though the job itself moved nothing.
    assert len(env.scans) == 1 and "aaa" in env.catalog.GamesView()


def test_an_earlier_jobs_copy_is_kept_while_the_game_is_installed(env):
    _game(env.games, "aaa", marker=b"current")
    left = env.games / (assets.REPLACED_PREFIX + "aaa-old")
    _game(left, "aaa", marker=b"older")
    _game(env.root / "src", "bbb")
    env.service.ImportFolder(str(env.root / "src"))
    job = _run(env)
    assert job["imported"] == ["bbb"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"current"
    assert (left / "aaa" / "full" / "a.png").read_bytes() == b"older"
    assert set(env.catalog.GamesView()) == {"aaa", "bbb"}     # not a game


def test_a_replaced_copy_that_cannot_be_removed_is_never_put_back(
        env, monkeypatch):
    _game(env.games, "aaa", marker=b"old")
    realUnlink = os.unlink
    blocked = [True]

    def unlink(path, *args, **kwargs):
        # The moved-aside copy's image in use (a scanner, an overlay).
        text = str(path)
        if blocked[0] and assets.REPLACED_PREFIX in text and \
                text.endswith("a.png"):
            raise PermissionError(13, "in use", text)
        return realUnlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    env.service.ImportZip(_zip(env.root / "aaa.zip", _game_entries("aaa")),
                          overwrite=True)
    job = _run(env)

    # The replacement is in; its old copy is partly removed, and marked.
    assert job["imported"] == ["aaa"] and job["failed"] == []
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"new"
    [kept] = _asides(env.games)
    assert (env.games / kept / assets.DISCARD_MARKER).exists()
    assert (env.games / kept / "aaa" / "full" / "a.png").exists()
    assert not (env.games / kept / "aaa" / "base_files").exists()

    # Even with the game's place empty, the marked copy is not put back
    # (it is partly gone): removed again, and kept while it cannot be.
    shutil.rmtree(env.games / "aaa")
    assert env.service.RestoreReplaced() is False
    assert not (env.games / "aaa").exists()
    assert _asides(env.games) == [kept]

    # The next job removes it once it can.
    blocked[0] = False
    _game(env.root / "src", "bbb")
    env.service.ImportFolder(str(env.root / "src"))
    assert _run(env)["imported"] == ["bbb"]
    assert _asides(env.games) == [] and not (env.games / "aaa").exists()


def test_a_read_only_file_does_not_keep_a_folder_a_job_removes(env):
    # A file copied from a read-only source keeps the attribute, and
    # Windows refuses to delete it until the attribute is cleared.
    stale = env.games / (assets.STAGING_PREFIX + "old")
    leftover = _write(stale / "extract" / "x.png")
    _game(env.games, "aaa", marker=b"old")
    installed = env.games / "aaa" / "full" / "a.png"
    for path in (leftover, installed):
        os.chmod(path, stat.S_IREAD)

    env.service.ImportZip(_zip(env.root / "aaa.zip", _game_entries("aaa")),
                          overwrite=True)
    job = _run(env)

    assert job["imported"] == ["aaa"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"new"
    assert _staging(env.games) == [] and _asides(env.games) == []


def test_the_newest_of_several_copies_is_put_back(env):
    # Named so that their names sort the other way round.
    newer = env.games / (assets.REPLACED_PREFIX + "aaa-0")
    older = env.games / (assets.REPLACED_PREFIX + "aaa-9")
    _game(newer, "aaa", marker=b"newer")
    _game(older, "aaa", marker=b"older")
    now = time.time()
    os.utime(older, (now - 3600, now - 3600))
    os.utime(newer, (now, now))

    assert env.service.RestoreReplaced() is True

    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"newer"
    assert not newer.exists()
    assert (older / "aaa" / "full" / "a.png").read_bytes() == b"older"
    # Kept again: the game is installed now.
    assert env.service.RestoreReplaced() is False
    assert (older / "aaa" / "full" / "a.png").read_bytes() == b"older"


def test_a_shells_restore_does_not_wait_for_a_running_job(env):
    seen = []

    def scan():
        # On the job (its scan): the shell's call, from another thread.
        answers = []
        helper = threading.Thread(
            target=lambda: answers.append(env.service.RestoreReplaced()))
        helper.start()
        helper.join(5)
        seen.append((env.service._filesLock.locked(), helper.is_alive(),
                     answers))
        env.catalog.ScanGames()

    left = env.games / (assets.REPLACED_PREFIX + "zzz-dead")
    _game(env.root / "src", "aaa")
    env.service.ImportFolder(str(env.root / "src"))
    # Moved aside while the job runs (after its own restore): left alone.
    env.service.scan = lambda: (_game(left, "zzz"), scan())
    job = _run(env)

    assert job["imported"] == ["aaa"]
    # The job holds the files: answered at once, nothing touched.
    assert seen == [(True, False, [False])]
    assert left.exists() and not (env.games / "zzz").exists()
    # With no job running, it puts the game back.
    assert env.service.RestoreReplaced() is True
    assert (env.games / "zzz" / "full" / "a.png").exists()


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_each_shells_first_scan_puts_back_what_a_job_moved_aside(env, shell):
    if shell == "desktop":
        from src.AutonaGameAssetManager import AutonaGameAssetManager as owner
    else:
        from src.headless import HeadlessGames as owner
    left = env.games / (assets.REPLACED_PREFIX + "aaa-dead")
    _game(left, "aaa", marker=b"old")
    # An upload an earlier run left (SF-4b-1).
    uploads = env.work / "user_data" / ".autona-uploads"
    _write(uploads / (assets.UPLOAD_PREFIX + "dead.zip"), b"PK" * 100)
    scanned = []

    owner._FirstScan(SimpleNamespace(_Scan=lambda: scanned.append(
        (sorted(p.name for p in env.games.iterdir()),
         sorted(p.name for p in uploads.iterdir())))))

    # Put back, and the upload removed, before the scan lists the games.
    assert scanned == [(["aaa"], [])]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"old"


@pytest.mark.parametrize("path, owner, method", [
    ("AutonaGameAssetManager.py", "AutonaGameAssetManager", "UiMounted"),
    ("headless.py", "HeadlessGames", "Start"),
])
def test_each_shells_startup_scan_is_its_first_scan(path, owner, method):
    tree = ast.parse((Path(__file__).resolve().parent.parent / "src" / path)
                     .read_text(encoding="utf-8"))
    [cls] = [node for node in tree.body
             if isinstance(node, ast.ClassDef) and node.name == owner]
    [body] = [node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method]
    submitted = [(ast.unparse(call.args[0]),
                  [ast.unparse(k.value) for k in call.keywords])
                 for call in ast.walk(body) if isinstance(call, ast.Call)
                 and ast.unparse(call.func).endswith(".submit")]
    assert ("self._FirstScan", ["'game scan'"]) in submitted
    assert not [call for call in ast.walk(body) if isinstance(call, ast.Call)
                and ast.unparse(call.func) in ("self._Scan", "self.LoadGames")]


def test_a_game_moved_aside_counts_as_installed_for_the_overwrite_question(
        env):
    _game(env.games / (assets.REPLACED_PREFIX + "aaa-x"), "aaa")
    marked = env.games / (assets.REPLACED_PREFIX + "bbb-x")
    _game(marked, "bbb")
    _write(marked / assets.DISCARD_MARKER, b"")
    _write(env.games / (assets.REPLACED_PREFIX + "ccc-x") / "ccc", b"a file")
    _game(env.games, "ddd")

    # aaa: the next job puts it back first, so replacing it is asked; bbb's
    # copy is never put back, ccc's is no game.
    assert assets.ImportConflicts(["aaa", "bbb", "ccc", "ddd", "eee"]) == [
        "aaa", "ddd"]


def test_a_job_that_cannot_be_queued_does_not_stay_running(env, monkeypatch):
    _game(env.root / "src", "aaa")
    real = env.executor.submit
    blocked = [True]

    def submit(*args, **kwargs):
        if blocked[0]:
            # As a start from a job lane would be refused.
            raise NestedSubmitError("a job submitted from a job thread")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.executor, "submit", submit)
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportFolder(str(env.root / "src"))
    assert refused.value.code == assets.FAILED
    job = env.service.Job()
    assert job["status"] == assets.ERROR and job["error"] == assets.FAILED
    assert not env.service.Busy()
    upload = _zip(env.root / "up.zip", _game_entries("ccc"))
    with pytest.raises(AssetPackError):
        env.service.ImportZip(upload, removeAfter=True)
    assert not os.path.exists(upload)

    blocked[0] = False
    assert env.service.ImportFolder(str(env.root / "src")) == (
        assets.STARTED, 3)
    assert _run(env)["imported"] == ["aaa"]


def test_a_job_whose_footer_fails_ends_rather_than_stays_running(
        env, monkeypatch):
    _game(env.root / "src", "aaa")
    real = StatusService.Push
    blocked = [True]

    def push(*args, **kwargs):
        if blocked[0]:
            raise RuntimeError("no footer")
        return real(*args, **kwargs)

    monkeypatch.setattr(StatusService, "Push", push)
    env.service.ImportFolder(str(env.root / "src"))
    job = _run(env)
    assert job["status"] == assets.ERROR and job["error"] == assets.FAILED
    assert not (env.games / "aaa").exists()
    assert not env.service.Busy()

    blocked[0] = False
    assert env.service.ImportFolder(str(env.root / "src"))[0] == \
        assets.STARTED
    assert _run(env)["imported"] == ["aaa"]


# ── importing a folder ───────────────────────────────────────────────────

def test_a_folder_import_copies_the_game_but_no_link_nor_other_pack(env):
    source = _game(env.root / "src", "ssbu", others=("logo_art",))
    linked = False
    try:
        os.symlink(env.root / "secret.txt", source / "full" / "link.png")
        _write(env.root / "secret.txt", b"secret")
        linked = True
    except (OSError, NotImplementedError):
        pass

    assert env.service.ImportFolder(str(source))[0] == assets.STARTED
    job = _run(env)

    assert job["status"] == assets.DONE, job
    assert job["operation"] == assets.IMPORT_FOLDER
    assert job["imported"] == ["ssbu"]
    copied = _files(env.games / "ssbu")
    assert copied == {"base_files/config.json", "base_files/logo.png",
                      "full/config.json", "full/a.png"}
    if linked:
        assert not (env.games / "ssbu" / "full" / "link.png").exists()
    assert job["done"] == job["total"] == 4
    assert len(env.scans) == 1
    assert _files(source) >= copied          # the source is left as it was


def test_a_folder_import_matches_pack_names_as_windows_does(env):
    # FULL is the full pack, LOGO (with its config.json) the logo pack the
    # app does not use (ADR-002).
    source = _game(env.root / "src", "aaa", packs=())
    _write(source / "FULL" / "config.json", _config(version="1"))
    _write(source / "FULL" / "a.png", b"full")
    _write(source / "LOGO" / "config.json", _config(version="1"))
    _write(source / "LOGO" / "a.png", b"logo")
    env.service.ImportFolder(str(source))
    job = _run(env)
    assert job["imported"] == ["aaa"]
    assert _files(env.games / "aaa") == {
        "base_files/config.json", "base_files/logo.png",
        "FULL/config.json", "FULL/a.png"}


def test_a_folder_holding_several_games_imports_each(env):
    _game(env.root / "src", "aaa")
    _game(env.root / "src", "bbb")
    (env.root / "src" / "notes").mkdir()

    assert [name for name, _f in assets.ProbeFolder(str(env.root / "src"))] \
        == ["aaa", "bbb"]
    env.service.ImportFolder(str(env.root / "src"))
    assert _run(env)["imported"] == ["aaa", "bbb"]


def test_an_installed_games_own_folder_is_imported_as_it_is(env):
    _game(env.games, "aaa")
    assert assets.ImportConflicts(
        assets.ProbeFolder(str(env.games / "aaa"))) == []
    env.service.ImportFolder(str(env.games / "aaa"))
    job = _run(env)
    assert job["imported"] == ["aaa"] and job["failed"] == []
    assert _files(env.games / "aaa")


def test_a_folder_holding_the_games_folder_is_not_copied_into_itself(env):
    # The working folder is a game itself, user_data/games inside it.
    _write(env.work / "base_files" / "config.json", _config(name="W"))
    env.service.ImportFolder(str(env.work))
    job = _run(env)
    assert job["status"] == assets.DONE
    assert job["imported"] == []
    assert [f["name"] for f in job["failed"]] == ["work"]
    assert not (env.games / "work").exists()


@pytest.mark.parametrize("make, code", [
    (lambda root: root / "missing", assets.NOT_A_FOLDER),
    (lambda root: _write(root / "file.txt").parent / "file.txt",
     assets.NOT_A_FOLDER),
    (lambda root: (root / "empty").mkdir() or root / "empty",
     assets.NO_GAME),
    (lambda root: _game(root / "bad", ".hidden").parent, assets.BAD_NAME),
])
def test_a_folder_that_cannot_be_imported_says_why(env, make, code):
    path = str(make(env.root))
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportFolder(path)
    assert refused.value.code == code
    assert env.executor.queued() == 0


# ── the folder pick (SF-5a-2) ────────────────────────────────────────────
# The folder the shell's native dialog picked (AutonaShellOperations'
# pickFolder): held under an id, one at a time, for PICK_EXPIRY_SECS; the
# page imports it by the id (v1 POST /assets:importFolder) and never sees the
# path (tests/test_api_v1_assets.py).

def _picked(env, *names, under="src"):
    for name in names:
        _game(env.root / under, name)
    return env.service.HoldFolderPick(str(env.root / under))


def _unknown_pick(env, pick):
    with pytest.raises(AssetPackError) as refused:
        env.service.FolderPickGames(pick)
    assert refused.value.code == assets.UNKNOWN_PICK


def test_a_folder_pick_is_held_under_an_id_and_imported_by_it(env):
    pick, games, conflicts = _picked(env, "aaa", "bbb")

    assert games == ["aaa", "bbb"] and conflicts == []
    assert isinstance(pick, str) and len(pick) >= 16
    assert assets._UPLOAD_ID.match(pick)
    assert str(env.root) not in pick
    assert env.service.FolderPickGames(pick) == (["aaa", "bbb"], [])

    assert env.service.ImportFolderPick(pick, []) == (assets.STARTED, 1)
    job = _run(env)
    assert job["operation"] == assets.IMPORT_FOLDER
    assert job["imported"] == ["aaa", "bbb"]
    assert (env.games / "bbb" / "base_files" / "config.json").is_file()
    # Taken: an id imports once.
    _unknown_pick(env, pick)
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportFolderPick(pick, [])
    assert refused.value.code == assets.UNKNOWN_PICK


def test_a_folder_picks_conflicts_are_read_now_and_overwritten_by_name(env):
    pick, _games, conflicts = _picked(env, "aaa", "bbb")
    assert conflicts == []
    _game(env.games, "aaa", marker=b"old")
    assert env.service.FolderPickGames(pick) == (["aaa", "bbb"], ["aaa"])

    env.service.ImportFolderPick(pick, ["aaa"])
    job = _run(env)
    assert sorted(job["imported"]) == ["aaa", "bbb"]
    assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"x"


def test_a_folder_pick_expires_when_it_is_looked_at(env):
    pick, _g, _c = _picked(env, "aaa")
    env.clock[0] += assets.PICK_EXPIRY_SECS - 1
    assert env.service.FolderPickGames(pick)[0] == ["aaa"]

    env.clock[0] += 1
    _unknown_pick(env, pick)
    assert env.service._pick is None
    assert assets.PICK_EXPIRY_SECS == 15 * 60


def test_an_expired_pick_is_let_go_by_a_lookup_under_another_id(env):
    _picked(env, "aaa")
    env.clock[0] += assets.PICK_EXPIRY_SECS
    _unknown_pick(env, "another-id-0123456789")
    assert env.service._pick is None


def test_a_new_pick_replaces_the_held_one_and_a_refused_one_does_not(env):
    first, _g, _c = _picked(env, "aaa", under="one")
    second, games, _c = _picked(env, "bbb", under="two")
    assert games == ["bbb"] and first != second
    _unknown_pick(env, first)

    (env.root / "empty").mkdir()
    with pytest.raises(AssetPackError) as refused:
        env.service.HoldFolderPick(str(env.root / "empty"))
    assert refused.value.code == assets.NO_GAME
    assert env.service.FolderPickGames(second)[0] == ["bbb"]


@pytest.mark.parametrize("make, code", [
    (lambda root: root / "missing", assets.NOT_A_FOLDER),
    (lambda root: (root / "empty").mkdir() or root / "empty",
     assets.NO_GAME),
    (lambda root: _game(root / "bad", ".hidden").parent, assets.BAD_NAME),
])
def test_a_folder_that_cannot_be_picked_holds_nothing(env, make, code):
    with pytest.raises(AssetPackError) as refused:
        env.service.HoldFolderPick(str(make(env.root)))
    assert refused.value.code == code
    assert env.service._pick is None


def test_a_folder_pick_matches_its_id_exactly(env, monkeypatch):
    pick, _g, _c = _picked(env, "aaa")
    for other in (pick[:-1], pick + "x", pick.upper() if pick.upper() != pick
                  else pick.lower(), "", " " + pick, None, 7, [pick],
                  "../" + pick):
        _unknown_pick(env, other)
    assert env.service.FolderPickGames(pick)[0] == ["aaa"]

    # The comparison is hmac.compare_digest's (the time it takes does not
    # tell how much of an id matched).
    compared = []

    def compare(a, b):
        compared.append((a, b))
        return a == b

    monkeypatch.setattr(assets, "hmac", SimpleNamespace(
        compare_digest=compare))
    assert env.service.FolderPickGames(pick)[0] == ["aaa"]
    assert compared == [(pick, pick)]


def test_a_pick_import_while_a_job_runs_is_busy_and_keeps_the_pick(env):
    other = _zip(env.root / "other.zip", _game_entries("ccc"))
    assert env.service.ImportZip(other)[0] == assets.STARTED
    pick, _g, _c = _picked(env, "aaa")

    assert env.service.ImportFolderPick(pick, []) == (assets.BUSY, 1)
    assert env.service.FolderPickGames(pick)[0] == ["aaa"]
    _run(env)
    assert env.service.ImportFolderPick(pick, [])[0] == assets.STARTED
    assert _run(env)["imported"] == ["aaa"]


def test_a_job_that_starts_while_the_pick_is_probed_leaves_it_held(
        env, monkeypatch):
    # As the upload's M1 (SF-4b-1): ImportFolderPick's busy check passed,
    # then another job started while ImportFolder probed the folder: held
    # again under the same id, its expiry unchanged.
    pick, _g, _c = _picked(env, "aaa")
    expiresAt = env.service._pick["expiresAt"]
    start = env.service._Start
    monkeypatch.setattr(env.service, "_Start",
                        lambda *args, **kwargs: (assets.BUSY, 7))

    assert env.service.ImportFolderPick(pick, []) == (assets.BUSY, 7)
    assert env.service._pick["expiresAt"] == expiresAt
    assert env.service.FolderPickGames(pick)[0] == ["aaa"]

    monkeypatch.setattr(env.service, "_Start", start)
    assert env.service.ImportFolderPick(pick, [])[0] == assets.STARTED
    assert _run(env)["imported"] == ["aaa"]


def test_a_newer_pick_during_the_probe_replaces_the_one_a_job_beat(
        env, monkeypatch):
    old, _g, _c = _picked(env, "aaa", under="one")
    _game(env.root / "two", "bbb")
    held = {}

    def busy(*args, **kwargs):
        held["newer"] = env.service.HoldFolderPick(str(env.root / "two"))
        return assets.BUSY, 7

    monkeypatch.setattr(env.service, "_Start", busy)
    assert env.service.ImportFolderPick(old, []) == (assets.BUSY, 7)
    newer, games, _c = held["newer"]
    assert games == ["bbb"]
    assert env.service.FolderPickGames(newer)[0] == ["bbb"]
    _unknown_pick(env, old)


def test_a_pick_whose_folder_changed_is_refused_and_gone(env):
    pick, _g, _c = _picked(env, "aaa")
    shutil.rmtree(env.root / "src")
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportFolderPick(pick, [])
    assert refused.value.code == assets.NOT_A_FOLDER
    _unknown_pick(env, pick)
    assert env.executor.queued() == 0


def test_a_pick_of_the_folder_holding_the_games_folder_copies_nothing(env):
    # Picked, but the job does not copy user_data/games into itself.
    _write(env.work / "base_files" / "config.json", _config(name="W"))
    pick, games, _c = env.service.HoldFolderPick(str(env.work))
    assert games == ["work"]
    env.service.ImportFolderPick(pick, [])
    job = _run(env)
    assert job["imported"] == [] and [f["name"] for f in job["failed"]] == [
        "work"]
    assert not (env.games / "work").exists()


def test_nothing_of_the_picked_folder_reaches_the_logs(env):
    from loguru import logger

    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        first, _g, _c = _picked(env, "aaa", under="private-folder-7Q")
        second, _g, _c = _picked(env, "bbb", under="private-folder-7Q")
        env.service.ImportFolderPick(second, [])
        _run(env)
        _picked(env, "ccc", under="private-folder-8Q")
        env.clock[0] += assets.PICK_EXPIRY_SECS
        _unknown_pick(env, first)
    finally:
        logger.remove(sink)

    text = "\n".join(lines)
    for secret in ("private-folder", str(env.root), first, second):
        assert secret not in text, secret
    assert "Asset folder pick: held" in text
    assert "Asset folder pick: expired" in text


def test_what_the_games_folder_itself_is_to_a_folder_import(env):
    # The folder of Autona: its games are two levels down (user_data/games),
    # so it holds none -- refused when picked, nothing held.
    _game(env.games, "aaa")
    _game(env.games, "bbb")
    with pytest.raises(AssetPackError) as refused:
        env.service.HoldFolderPick(str(env.work))
    assert refused.value.code == assets.NO_GAME
    # user_data/games itself: its games, each imported as it is (nothing
    # copied, nothing to overwrite).
    before = {name: _files(env.games / name) for name in ("aaa", "bbb")}
    pick, games, conflicts = env.service.HoldFolderPick(str(env.games))
    assert games == ["aaa", "bbb"] and conflicts == []
    env.service.ImportFolderPick(pick, [])
    job = _run(env)
    assert job["status"] == assets.DONE
    assert job["imported"] == ["aaa", "bbb"] and job["failed"] == []
    assert {name: _files(env.games / name) for name in ("aaa", "bbb")} == \
        before
    assert _staging(env.games) == [] and _asides(env.games) == []


def _junction(link, target):
    """A junction (else a symbolic link) at ``link`` onto the folder
    ``target``; False when neither can be made here."""
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return True
    except (ImportError, AttributeError, OSError):
        pass
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_a_picked_link_onto_an_installed_game_is_that_game(env):
    # The folder picked is followed (a link or a junction): one onto an
    # installed game is that game -- imported as it is, no conflict -- not
    # a copy of it over itself.
    _game(env.games, "aaa", marker=b"mine")
    (env.root / "elsewhere").mkdir()
    link = env.root / "elsewhere" / "aaa"
    if not _junction(link, env.games / "aaa"):
        pytest.skip("no junction nor symbolic link can be made here")
    try:
        pick, games, conflicts = env.service.HoldFolderPick(str(link))
        assert games == ["aaa"] and conflicts == []
        env.service.ImportFolderPick(pick, [])
        job = _run(env)
        assert job["imported"] == ["aaa"] and job["failed"] == []
        assert job["total"] == 0          # nothing copied
        assert (env.games / "aaa" / "full" / "a.png").read_bytes() == b"mine"
        assert _asides(env.games) == []
    finally:
        os.rmdir(link)


def test_a_picked_link_onto_a_game_holding_the_games_folder_is_not_copied(
        env):
    # Resolved, the game's folder holds user_data/games: not copied into
    # itself (by its link's own path it would not seem to).
    _write(env.work / "base_files" / "config.json", _config(name="W"))
    link = env.root / "work-link"
    if not _junction(link, env.work):
        pytest.skip("no junction nor symbolic link can be made here")
    try:
        pick, games, _c = env.service.HoldFolderPick(str(link))
        assert games == ["work-link"]
        env.service.ImportFolderPick(pick, [])
        job = _run(env)
        assert job["imported"] == []
        assert [f["name"] for f in job["failed"]] == ["work-link"]
        assert not (env.games / "work-link").exists()
        assert _staging(env.games) == []
    finally:
        os.rmdir(link)


def _logs():
    """Everything logged at DEBUG and up, and at INFO and up: (debug, info,
    stop)."""
    from loguru import logger

    debug, info = [], []
    sinks = [logger.add(lambda m: debug.append(str(m)), level="DEBUG"),
             logger.add(lambda m: info.append(str(m)), level="INFO")]
    return debug, info, lambda: [logger.remove(sink) for sink in sinks]


LOCKED = ("The process cannot access the file because it is being used by "
          "another process")


def test_a_file_the_copy_cannot_read_fails_its_game_without_a_path(
        env, monkeypatch):
    # A locked file (Windows' sharing violation) in the folder picked: the
    # game fails, its message says how many files and the OS's reason --
    # never the folder's path nor a file's -- and so do the error lines;
    # the whole failure goes to the debug log only.
    source = env.root / "private-folder-7Q"
    _game(source, "aaa")
    _game(source, "bbb")
    real = shutil.copy2

    def copy2(src, dst, *args, **kwargs):
        if os.path.basename(src) == "a.png" and os.sep + "aaa" + os.sep in src:
            raise PermissionError(13, LOCKED, src)
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy2)
    debug, info, stop = _logs()
    try:
        pick, _g, _c = env.service.HoldFolderPick(str(source))
        env.service.ImportFolderPick(pick, [])
        job = _run(env)
    finally:
        stop()

    assert job["status"] == assets.DONE
    assert job["imported"] == ["bbb"]
    assert job["failed"] == [{
        "name": "aaa", "message": f"1 file(s) could not be copied ({LOCKED})"}]
    assert not (env.games / "aaa").exists()
    infoText = "\n".join(info)
    for secret in ("private-folder", str(env.root), "a.png"):
        assert secret not in json.dumps(job), secret
        assert secret not in infoText, secret
    assert "Game asset import failed for aaa: 1 file(s) could not be copied" \
        in infoText
    # The detail is the debug log's.
    assert "private-folder" in "\n".join(debug)


def test_a_folder_the_copy_cannot_open_fails_its_game_without_a_path(
        env, monkeypatch):
    source = env.root / "private-folder-7Q"
    _game(source, "aaa")
    real = shutil.copytree

    def copytree(src, dst, *args, **kwargs):
        raise PermissionError(13, "Access is denied", src)

    monkeypatch.setattr(shutil, "copytree", copytree)
    debug, info, stop = _logs()
    try:
        pick, _g, _c = env.service.HoldFolderPick(str(source))
        env.service.ImportFolderPick(pick, [])
        job = _run(env)
    finally:
        stop()
        monkeypatch.setattr(shutil, "copytree", real)

    assert job["failed"] == [{"name": "aaa", "message": "Access is denied"}]
    assert "private-folder" not in json.dumps(job)
    assert "private-folder" not in "\n".join(info)


@pytest.mark.parametrize("failure, error, message", [
    (lambda path: PermissionError(13, "Access is denied", path),
     assets.FAILED, "Access is denied"),
    (lambda path: OSError(28, "No space left on device", path),
     assets.DISK_FULL, "No space left on device"),
    (lambda path: ValueError(f"cannot read {path}"),
     assets.FAILED, "unexpected error (ValueError)"),
])
def test_a_folder_job_that_fails_says_why_without_a_path(
        env, monkeypatch, failure, error, message):
    # The job's own failure (not a game's): its message in the job state
    # and its error line name no path; the traceback is the debug log's.
    source = env.root / "private-folder-7Q"
    _game(source, "aaa")

    def refuse(where, needed):
        raise failure(str(source))

    monkeypatch.setattr(assets, "_RequireSpace", refuse)
    debug, info, stop = _logs()
    try:
        pick, _g, _c = env.service.HoldFolderPick(str(source))
        env.service.ImportFolderPick(pick, [])
        job = _run(env)
    finally:
        stop()

    assert job["status"] == assets.ERROR
    assert job["error"] == error and job["message"] == message
    assert "private-folder" not in json.dumps(job)
    assert "private-folder" not in "\n".join(info)
    assert f"failed: {error}: {message}" in "\n".join(info)
    assert "private-folder" in "\n".join(debug)


# ── the install ──────────────────────────────────────────────────────────

def _upstream(env):
    """ssbu: base_files (split in two), full (with a top folder that is no
    allowed pack) and logo (not offered)."""
    _publish(env.net, env.root, "ssbu", "base_files", {
        "base_files/config.json": _config(name="Smash", version="3"),
        "base_files/logo.png": b"logo",
    }, version="3", parts=2, name="Super Smash Bros. Ultimate")
    _publish(env.net, env.root, "ssbu", "full", {
        "full/config.json": _config(version="2"),
        "full/mario.png": b"mario" * 1000,
        "promo_art/poster.png": b"not an allowed pack",
    }, version="2")
    _publish(env.net, env.root, "ssbu", "logo", {
        "logo/config.json": _config(version="1"),
    })


def test_an_install_fetches_concatenates_and_extracts_the_allowed_packs(env):
    _upstream(env)
    _write(env.games / "ssbu" / "full" / "kept.png", b"user file")

    assert env.service.Install("ssbu", ["full", "base_files"]) == (
        assets.STARTED, 1)
    job = _run(env)

    assert job["status"] == assets.DONE, job
    assert job["operation"] == assets.INSTALL and job["game"] == "ssbu"
    assert job["imported"] == ["ssbu"]
    assert _files(env.games / "ssbu") == {
        "base_files/config.json", "base_files/logo.png",
        "full/config.json", "full/mario.png", "full/kept.png"}
    assert (env.games / "ssbu" / "full" / "mario.png").read_bytes() == \
        b"mario" * 1000
    downloads = [url[len(assets.DOWNLOAD_BASE):] for url in env.net.calls
                 if url != assets.INDEX_URL]
    assert downloads == ["ssbu.base_files.7z.001", "ssbu.base_files.7z.002",
                         "ssbu.full.7z.001"]
    assert len(env.scans) == 1
    assert _staging(env.games) == []
    assert job["done"] == job["total"] == sum(
        len(env.net.files[name]) for name in downloads)

    game = env.service.Packs(fetch=False)["games"][0]
    packs = {pack["name"]: pack for pack in game["packs"]}
    assert game["installed"] and set(packs) == {"base_files", "full"}
    assert packs["base_files"]["installedVersion"] == "3"
    assert packs["full"]["installedVersion"] == "2"
    assert not packs["full"]["updateAvailable"]


def _pack_like_upstream(env, code, pack, files, version="1"):
    """A pack as upstream builds it (StreamHelperAssets' gen_config.py:
    ``7z a games/<code>/<code>.<pack>.7z games/<code>/<pack>``): the pack's
    folder at the top, its directory entries included -- as the real
    sf3ts.variant_icon.7z.001 (2026-11-14): ``variant_icon``,
    ``variant_icon/config.json``, ``variant_icon/sa1.png``, ..."""
    source = env.root / "upstream" / code / pack
    for relative, data in files.items():
        _write(source / relative, data)
    archive = env.root / "upstream" / f"{code}.{pack}.7z.001"
    with _py7zr.SevenZipFile(archive, "w") as z:
        z.writeall(source, arcname=pack)
    data = archive.read_bytes()
    env.net.files[archive.name] = data
    game = env.net.index.setdefault(code, {"name": code, "assets": {}})
    game["assets"][pack] = {"version": version, "files": {
        archive.name: {"name": archive.name, "size": len(data)}}}
    return archive


def _contents(root):
    root = Path(root)
    return ({p.relative_to(root).as_posix(): p.read_bytes()
             for p in root.rglob("*") if p.is_file()},
            {p.relative_to(root).as_posix()
             for p in root.rglob("*") if p.is_dir()})


def test_an_install_puts_in_place_what_the_old_window_extracted(env):
    archives = [
        _pack_like_upstream(env, "sf3ts", "variant_icon", {
            "config.json": _config(version="26.07.25"),
            "sa1.png": b"sa1", "sa2.png": b"sa2"}),
        _pack_like_upstream(env, "sf3ts", "base_files", {
            "config.json": _config(name="SF3"), "logo.png": b"logo",
            "icon/config.json": _config(version="1"), "icon/ryu.png": b"ryu"}),
    ]
    with _py7zr.SevenZipFile(archives[0], "r") as z:
        names = [f.filename for f in z.files]
    assert "variant_icon" in names and all(
        name.split("/")[0] == "variant_icon" for name in names)
    # A file the operator had in the pack's folder stays, as an extraction
    # over it left it.
    _write(env.games / "sf3ts" / "variant_icon" / "mine.png", b"mine")
    old = env.root / "old" / "user_data" / "games" / "sf3ts"
    _write(old / "variant_icon" / "mine.png", b"mine")
    # The old window (d393a1b2 _AssetDownloadThread.run): py7zr extractall of
    # each pack's concatenated parts into user_data/games/<code>/.
    for archive in archives:
        with _py7zr.SevenZipFile(archive, "r") as z:
            z.extractall(old)

    env.service.Install("sf3ts", ["variant_icon", "base_files"])
    job = _run(env)

    assert job["status"] == assets.DONE, job
    assert _contents(env.games / "sf3ts") == _contents(old)
    assert _staging(env.games) == []


def test_an_install_without_packs_takes_the_offered_ones(env):
    _upstream(env)
    env.service.Install("ssbu", None)
    _run(env)
    assert {url.rsplit(".7z", 1)[0][len(assets.DOWNLOAD_BASE):]
            for url in env.net.calls if url != assets.INDEX_URL} == {
        "ssbu.base_files", "ssbu.full"}


@pytest.mark.parametrize("code, packs, error", [
    ("", ["full"], assets.UNKNOWN_GAME),
    ("nope", ["full"], assets.UNKNOWN_GAME),
    ("ssbu", ["logo"], assets.UNKNOWN_PACK),
    ("ssbu", ["portrait"], assets.UNKNOWN_PACK),   # not in this game
    ("ssbu", [3], assets.UNKNOWN_PACK),
    ("ssbu", [], assets.NO_PACKS),
    ("ssbu", "full", assets.NO_PACKS),
])
def test_an_install_that_cannot_start_says_why(env, code, packs, error):
    _upstream(env)
    with pytest.raises(AssetPackError) as refused:
        env.service.Install(code, packs)
    assert refused.value.code == error
    assert env.executor.queued() == 0


def test_an_install_needs_the_index(env):
    env.net.indexError = requests.ConnectionError("offline")
    with pytest.raises(AssetPackError) as refused:
        env.service.Install("ssbu", ["full"])
    assert refused.value.code == assets.INDEX_UNAVAILABLE
    # The window's: the cached index only, never a fetch.
    env.net.calls.clear()
    with pytest.raises(AssetPackError):
        env.service.Install("ssbu", ["full"], fetch=False)
    assert env.net.calls == []


def test_an_index_naming_a_game_badly_installs_nothing(env):
    env.net.index = {
        "../up": {"assets": {"full": {"files": {"a.7z.001": {"size": 1}}}}}}
    with pytest.raises(AssetPackError) as refused:
        env.service.Install("../up", ["full"])
    assert refused.value.code == assets.BAD_NAME
    assert env.executor.queued() == 0


@pytest.mark.parametrize("fileName, pack", [
    ("../a.7z.001", "full"), ("a/b.7z.001", "full"), ("a b.7z.001", "full"),
    (".", "full"), ("..", "full"), (".a.7z.001", "full"),
    # The name of a pack's merged archive, written next to the parts.
    ("full.7z", "full"), ("FULL.7z", "full"), ("full.7z", "base_files"),
])
def test_an_index_naming_a_file_badly_installs_nothing(env, fileName, pack):
    env.net.index = {"ssbu": {"assets": {
        "base_files": {"files": {"ssbu.base_files.7z.001": {"size": 1}}},
        "full": {"files": {"ssbu.full.7z.001": {"size": 1}}}}}}
    env.net.index["ssbu"]["assets"][pack]["files"] = {fileName: {"size": 1}}

    env.service.Install("ssbu", ["base_files", "full"])
    job = _run(env)
    assert job["status"] == assets.ERROR and job["error"] == assets.BAD_INDEX
    assert [url for url in env.net.calls if url != assets.INDEX_URL] == []
    assert not (env.games / "ssbu").exists()


@pytest.mark.parametrize("how", ["missing", "reset"])
def test_a_failed_download_installs_nothing(env, how):
    _upstream(env)
    if how == "missing":
        del env.net.files["ssbu.full.7z.001"]
    else:
        env.net.failFile = "ssbu.full.7z.001"
    env.service.Install("ssbu", ["base_files", "full"])
    job = _run(env)
    assert job["status"] == assets.ERROR
    assert job["error"] == assets.DOWNLOAD_FAILED
    assert not (env.games / "ssbu").exists()   # all or nothing
    assert env.scans == []
    assert _staging(env.games) == []


def test_a_cancel_during_the_download_installs_nothing(env):
    _upstream(env)
    env.net.pieces = 3
    env.net.onChunk = lambda index: index == 1 and env.service.Cancel(
        env.service.Job()["revision"])

    env.service.Install("ssbu", ["base_files", "full"])
    job = _run(env)

    assert job["status"] == assets.CANCELLED and job["error"] is None
    assert not (env.games / "ssbu").exists()
    assert _staging(env.games) == []
    assert env.scans == []
    assert StatusService.Snapshot()["jobs"] == []
    assert env.service.Cancel(env.service.Job()["revision"]) is False  # none runs


def test_the_shutdown_during_a_job_stops_it_without_a_scan(env):
    _upstream(env)
    env.net.pieces = 3
    env.net.onChunk = lambda index: index == 1 and \
        env.executor.stopping.set()

    env.service.Install("ssbu", ["base_files", "full"])
    job = _run(env)

    assert job["status"] == assets.ERROR and job["error"] == assets.STOPPED
    assert not (env.games / "ssbu").exists()
    assert env.scans == []
    assert StatusService.Snapshot()["jobs"] == []


def test_the_shutdown_settles_a_queued_job_and_removes_its_upload(env):
    path = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    env.service.ImportZip(path, removeAfter=True)
    assert env.executor.queued() == 1

    assert env.executor.shutdown(WAIT)

    job = env.service.Job()
    assert job["status"] == assets.ERROR and job["error"] == assets.STOPPED
    assert not os.path.exists(path)
    assert env.scans == []
    assert env.told == []           # it never ran: no footer
    # And nothing starts any more.
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportZip(_zip(env.root / "b.zip", _game_entries("b")))
    assert refused.value.code == assets.STOPPED


def test_one_job_runs_at_a_time(env):
    first = _zip(env.root / "aaa.zip", _game_entries("aaa"))
    _game(env.root / "src", "bbb")
    assert env.service.ImportZip(first) == (assets.STARTED, 1)
    assert env.service.Busy()
    assert env.service.ImportFolder(str(env.root / "src")) == (
        assets.BUSY, 1)
    assert env.executor.queued() == 1

    _run(env)
    assert not env.service.Busy()
    assert env.service.ImportFolder(str(env.root / "src")) == (
        assets.STARTED, 2)
    assert env.service.Job()["revision"] == 2
    _run(env)


def test_what_a_dead_job_left_is_removed_by_the_next_one(env):
    stale = env.games / (assets.STAGING_PREFIX + "old")
    _write(stale / "extract" / "x.png")
    _game(env.root / "src", "aaa")
    env.service.ImportFolder(str(env.root / "src"))
    _run(env)
    assert _staging(env.games) == []
    assert "aaa" in env.catalog.GamesView()     # the scan skipped it


def test_a_job_that_runs_out_of_disk_says_so(env, monkeypatch):
    _game(env.root / "src", "aaa")
    monkeypatch.setattr(assets.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=1))
    env.service.ImportFolder(str(env.root / "src"))
    job = _run(env)
    assert job["status"] == assets.ERROR and job["error"] == assets.DISK_FULL
    assert not (env.games / "aaa").exists()


# ── a 7z is read before it is extracted ──────────────────────────────────

def _entry(name, size=1, directory=False, symlink=False, junction=False):
    return SimpleNamespace(filename=name, uncompressed=size,
                           is_directory=directory, is_symlink=symlink,
                           is_junction=junction, is_socket=False)


@pytest.fixture
def fake7z(monkeypatch):
    """py7zr replaced: its archives list ``entries`` (py7zr refuses to
    write such names, so a real archive cannot be made); ``extracted``
    records the extractions."""
    state = SimpleNamespace(entries=[], extracted=[])

    class SevenZipFile:
        def __init__(self, path, mode):
            self.files = state.entries

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extractall(self, path):
            state.extracted.append(path)

    monkeypatch.setitem(sys.modules, "py7zr",
                        SimpleNamespace(SevenZipFile=SevenZipFile))
    return state


@pytest.mark.parametrize("bad, error", [
    (_entry("../evil.txt"), assets.UNSAFE_ARCHIVE),
    (_entry("full/../../evil.txt"), assets.UNSAFE_ARCHIVE),
    (_entry("/abs/evil.txt"), assets.UNSAFE_ARCHIVE),
    (_entry("C:/evil.txt"), assets.UNSAFE_ARCHIVE),
    (_entry("full\\..\\..\\evil.txt"), assets.UNSAFE_ARCHIVE),
    (_entry("full/link.png", symlink=True), assets.UNSAFE_ARCHIVE),
    (_entry("full/junction", directory=True, junction=True),
     assets.UNSAFE_ARCHIVE),
    (_entry("full/huge.png", size=assets.MAX_UNPACKED_BYTES),
     assets.TOO_LARGE),
])
def test_an_unsafe_7z_is_refused_before_anything_is_extracted(
        env, fake7z, bad, error):
    fake7z.entries = [_entry("full/config.json"), bad]
    with pytest.raises(assets._JobError) as refused:
        assets._Extract7z("pack.7z", str(env.root))
    assert refused.value.code == error
    assert fake7z.extracted == []


def test_a_7z_with_too_many_entries_is_refused(env, fake7z, monkeypatch):
    monkeypatch.setattr(assets, "MAX_ENTRIES", 2)
    fake7z.entries = [_entry(f"full/{i}.png") for i in range(3)]
    with pytest.raises(assets._JobError) as refused:
        assets._Extract7z("pack.7z", str(env.root))
    assert refused.value.code == assets.TOO_LARGE
    assert fake7z.extracted == []


def test_an_install_of_an_unsafe_7z_writes_nothing(env, fake7z):
    _upstream(env)
    fake7z.entries = [_entry("full/config.json"), _entry("../evil.txt")]
    env.service.Install("ssbu", ["full"])
    job = _run(env)
    assert job["status"] == assets.ERROR
    assert job["error"] == assets.UNSAFE_ARCHIVE
    assert fake7z.extracted == []
    assert not (env.games / "ssbu").exists()
    assert _staging(env.games) == []


# ── the index and the list ───────────────────────────────────────────────

def test_the_index_is_fetched_once_and_kept_for_a_while(env):
    _upstream(env)
    assert env.service.IndexState()["status"] == assets.INDEX_IDLE
    env.service.Packs()
    env.service.Packs()
    env.service.Install("ssbu", ["full"])        # the cached one
    assert env.net.indexFetches() == 1
    assert env.service.IndexState()["status"] == assets.INDEX_READY

    env.clock[0] += assets.INDEX_MAX_AGE_SECS
    env.service.Packs()
    assert env.net.indexFetches() == 2


def test_a_refresh_fetches_at_most_once_every_few_seconds(env):
    _upstream(env)
    env.service.Packs(refresh=True)
    env.service.Packs(refresh=True)
    assert env.net.indexFetches() == 1
    env.clock[0] += assets.INDEX_MIN_REFRESH_SECS
    env.service.Packs(refresh=True)
    assert env.net.indexFetches() == 2


def test_a_failed_refresh_keeps_the_older_index(env):
    _upstream(env)
    env.service.Packs()
    env.net.indexError = requests.ConnectionError("offline")
    env.clock[0] += assets.INDEX_MIN_REFRESH_SECS

    payload = env.service.Packs(refresh=True)

    assert payload["index"]["status"] == assets.INDEX_ERROR
    assert payload["index"]["error"] == assets.INDEX_FETCH_FAILED
    assert "offline" not in json.dumps(payload)     # in the log only
    assert [game["code"] for game in payload["games"]] == ["ssbu"]


@pytest.mark.parametrize("answer, code", [
    ("refused", assets.INDEX_FETCH_FAILED),
    ("http", assets.INDEX_FETCH_FAILED),
    ("list", assets.BAD_INDEX),
])
def test_an_index_that_cannot_be_read_says_so_with_a_code(env, answer, code):
    secret = "C:\\Users\\someone\\proxy-secret"
    if answer == "refused":
        env.net.indexError = requests.ConnectionError(f"refused by {secret}")
    elif answer == "http":
        env.net.indexError = requests.HTTPError(f"HTTP 500 at {secret}")
    else:
        env.net.index = [secret]

    payload = env.service.Packs()

    assert payload["index"]["status"] == assets.INDEX_ERROR
    assert payload["index"]["error"] == code
    assert env.service.IndexState()["error"] == code
    assert "secret" not in json.dumps(payload)


def test_callers_that_come_during_a_fetch_share_it(env):
    _upstream(env)
    env.net.indexGate = threading.Event()
    answers = []
    first = threading.Thread(
        target=lambda: answers.append(env.service.EnsureIndex()))
    first.start()
    assert env.net.indexAsked.wait(WAIT)
    assert env.service.IndexState()["status"] == assets.INDEX_LOADING
    second = threading.Thread(
        target=lambda: answers.append(env.service.EnsureIndex(refresh=True)))
    second.start()
    env.net.indexGate.set()
    first.join(WAIT)
    second.join(WAIT)
    assert answers == [True, True]
    assert env.net.indexFetches() == 1


def test_the_list_offers_the_allowed_packs_and_the_installed_games(env):
    _upstream(env)
    env.net.index["aaa"] = {"name": "Zeta", "assets": {
        "full": {"version": "1", "files": {"a.7z.001": {"size": 10},
                                           "a.7z.002": {"size": 5}}},
        "portrait": "not a pack"}}
    _game(env.games, "aaa", packs=("full",), version="0")
    _game(env.games, "local", packs=("full", "portrait"), others=("music",))
    env.catalog.ScanGames()

    payload = env.service.Packs()

    assert payload["source"] == {"page": assets.SOURCE_URL,
                                 "index": assets.INDEX_URL}
    assert set(payload) == {"source", "index", "games"}
    games = {game["code"]: game for game in payload["games"]}
    # Sorted by "name  (code)", case-insensitively.
    assert [game["code"] for game in payload["games"]] == [
        "local", "ssbu", "aaa"]
    ssbu = games["ssbu"]
    assert ssbu["available"] and not ssbu["installed"]
    assert [pack["name"] for pack in ssbu["packs"]] == ["base_files", "full"]
    assert all(pack["default"] and not pack["installed"]
               for pack in ssbu["packs"])
    assert games["aaa"]["packs"] == [{
        "name": "full", "size": 15, "version": "1", "installedVersion": "0",
        "installed": True, "updateAvailable": True, "default": True}]
    local = games["local"]
    assert not local["available"] and local["installed"]
    assert [pack["name"] for pack in local["packs"]] == [
        "base_files", "full", "portrait"]
    assert all(pack["size"] is None for pack in local["packs"])
    # No local path in it (a READ).
    assert str(env.work) not in json.dumps(payload)
    assert "user_data" not in json.dumps(payload)


# ── /api/v1 ──────────────────────────────────────────────────────────────
# The list and the last job's state. The jobs' routes, the upload and the
# job's state under the shell's secret (SF-4b-1) are
# tests/test_api_v1_assets.py's; here a job starts through the service, as
# the window's.

@pytest.fixture
def api(env, monkeypatch):
    """The real app on the test's service."""
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", ("192.168.1.5",))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    env.client = client
    return env


def test_the_list_is_read_through_v1(api):
    _upstream(api)
    response = api.client.get("/api/v1/assets/packs")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert [game["code"] for game in data["games"]] == ["ssbu"]
    assert data["index"]["status"] == assets.INDEX_READY

    api.net.indexError = requests.ConnectionError("offline")
    api.clock[0] += assets.INDEX_MIN_REFRESH_SECS
    response = api.client.get("/api/v1/assets/packs?refresh=true")
    assert response.status_code == 200
    index = response.get_json()["data"]["index"]
    assert index["status"] == assets.INDEX_ERROR
    assert index["error"] == assets.INDEX_FETCH_FAILED


def test_a_job_is_followed_through_v1(api, monkeypatch):
    from src.core import WebAccess

    # The shared view's fetch (SF-4b-1: SHELL).
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    shell = {"Sec-Fetch-Site": "same-origin",
             WebAccess.SHELL_HEADER: WebAccess.IssueShellSecret()}
    assert api.client.get("/api/v1/assets/job").status_code == 403

    job = api.client.get("/api/v1/assets/job", headers=shell)
    assert job.headers["Cache-Control"] == "no-store"
    assert job.get_json()["data"]["status"] == assets.IDLE

    _upstream(api)
    started, revision = api.service.Install("ssbu", ["full"])
    assert started == assets.STARTED and revision == 1
    data = api.client.get("/api/v1/assets/job",
                          headers=shell).get_json()["data"]
    assert data["revision"] == 1 and data["status"] == assets.RUNNING

    assert api.executor.run_next(WAIT)
    data = api.client.get("/api/v1/assets/job",
                          headers=shell).get_json()["data"]
    assert data["revision"] == 1 and data["status"] == assets.DONE
    assert data["imported"] == ["ssbu"]

