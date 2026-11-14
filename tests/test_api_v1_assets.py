"""The game asset routes of /api/v1 (ADR-005 S-final SF-4b-1).

What the shared view's "Add game assets" page calls (SF-4b-2), all
WebAccess.SHELL -- the shared view's own fetch, with the shell's secret --
and every answer no-store:

- ``POST /api/v1/assets/uploads``           the ZIP's bytes -> 201 its id
- ``DELETE /api/v1/assets/uploads/<id>``    drop it
- ``POST /api/v1/assets:importZip``         import it (a job)
- ``POST /api/v1/assets/packs:install``     install an index game's packs
- ``POST /api/v1/assets/job:cancel``        stop the job by its revision
- ``POST /api/v1/assets/games-folder:reveal`` open user_data/games
- ``GET /api/v1/assets/job``                the last job (SHELL since 4b-1)

Core is the real AssetPackService on a stepped executor in tmp_path
(tests/test_asset_packs.py's ``env`` / ``api``); the upload is read through
werkzeug's request stream as the server reads it. Who else is refused is
pinned in tests/test_web_access.py too (the SHELL table); the shared view's
real XHR upload in tests/test_web_panel_host.py.
"""
import ast
import io
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from loguru import logger

from src import AutonaApiV1 as v1
from src.core import AssetPackService as assets
from src.core import WebAccess
from src.core.AssetPackService import AssetPackError
from tests.test_asset_packs import (  # noqa: F401
    _game, _game_entries, _run, _upstream, _write, _zip, api, env)

UPLOADS = "/api/v1/assets/uploads"
IMPORT = "/api/v1/assets:importZip"
INSTALL = "/api/v1/assets/packs:install"
CANCEL = "/api/v1/assets/job:cancel"
REVEAL = "/api/v1/assets/games-folder:reveal"
JOB = "/api/v1/assets/job"
FILE_NAME = v1.ASSET_FILE_NAME_HEADER
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def shell(api, monkeypatch):
    """The real app, and the headers of the shared view's own fetch."""
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    api.headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
                   "Sec-Fetch-Dest": "empty",
                   WebAccess.SHELL_HEADER: WebAccess.IssueShellSecret()}
    api.uploads = api.work / "user_data" / ".autona-uploads"
    return api


class _Body:
    """A request body for the test client (tell / seek: it measures it):
    ``data``, then ``fail`` raised on the read after it (a disconnect), and
    every read counted."""

    def __init__(self, data=b"", fail=None):
        self.data = data
        self.fail = fail
        self.at = 0
        self.reads = 0

    def tell(self):
        return self.at

    def seek(self, offset, whence=0):
        self.at = len(self.data) if whence == 2 else offset
        return self.at

    def read(self, size=-1):
        self.reads += 1
        if self.at >= len(self.data) and self.fail is not None:
            raise self.fail
        end = len(self.data) if size is None or size < 0 else self.at + size
        chunk = self.data[self.at:end]
        self.at += len(chunk)
        return chunk


class _Unread(_Body):
    def read(self, size=-1):
        raise AssertionError("the body was read")


def _zipBytes(api, entries, name="up.zip"):
    return Path(_zip(api.root / name, entries)).read_bytes()


def _upload(api, data, fileName=None, **headers):
    sent = dict(api.headers)
    if fileName is not None:
        sent[FILE_NAME] = quote(fileName)
    sent.update(headers)
    return api.client.post(UPLOADS, data=data, headers=sent,
                           content_type="application/zip")


def _uploaded(api, entries=None, fileName=None):
    response = _upload(api, _zipBytes(api, entries or _game_entries("aaa")),
                       fileName)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["data"]


def _files(api):
    if not api.uploads.exists():
        return []
    return sorted(p.name for p in api.uploads.iterdir())


def _post(api, path, body=None):
    return api.client.post(path, json=body or {}, headers=api.headers)


def _error(response, status, code, reason=None):
    assert response.status_code == status, response.get_data(as_text=True)
    error = response.get_json()["error"]
    assert error["code"] == code
    assert error.get("reason") == reason
    return error


# ── the upload ───────────────────────────────────────────────────────────

def test_an_upload_is_held_and_answered_without_its_path(shell):
    response = _upload(shell, _zipBytes(shell, _game_entries("aaa")),
                       "games.zip")

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    data = response.get_json()["data"]
    assert data["games"] == ["aaa"] and data["conflicts"] == []
    assert data["expiresInSec"] == assets.UPLOAD_EXPIRY_SECS == 15 * 60
    assert len(data["upload"]) >= 20 and set(data) == {
        "upload", "games", "conflicts", "expiresInSec"}
    text = response.get_data(as_text=True)
    for path in (str(shell.work), "user_data", "autona-uploads",
                 assets.UPLOAD_PREFIX):
        assert path not in text
    # One file, outside the served games folder.
    assert len(_files(shell)) == 1
    assert not (shell.games.exists() and list(shell.games.iterdir()))
    assert not WebAccess.StaticPath("user_data/.autona-uploads/" +
                                    _files(shell)[0])


def test_an_installed_game_is_a_conflict(shell):
    _game(shell.games, "aaa")
    data = _uploaded(shell, _game_entries("aaa") | _game_entries("bbb"))
    assert data["games"] == ["aaa", "bbb"]
    assert data["conflicts"] == ["aaa"]


def test_a_game_at_the_zips_top_is_named_after_the_header(shell):
    top = _game_entries("x", prefix="")
    data = _uploaded(shell, top, "Ünïcode game.zip")
    assert data["games"] == ["Ünïcode game"]

    # No file name: such a game has none.
    _error(_upload(shell, _zipBytes(shell, top)), 400, v1.INVALID_ARGUMENT,
           assets.BAD_NAME)


@pytest.mark.parametrize("raw", ["%FF%FE.zip", "a%0Ab.zip", "%00",
                                 "x" * 300])
def test_the_file_name_must_be_a_percent_encoded_name(shell, raw):
    body = _Unread(b"PK" * 10)
    response = shell.client.post(
        UPLOADS, input_stream=body, headers=dict(shell.headers,
                                                 **{FILE_NAME: raw}))
    _error(response, 400, v1.INVALID_ARGUMENT)
    assert response.headers["Connection"] == "close"
    assert _files(shell) == []


def test_without_a_length_the_body_is_not_read(shell):
    body = _Unread(b"PK" * 10)
    response = shell.client.post(
        UPLOADS, input_stream=body,
        headers=dict(shell.headers, **{"Transfer-Encoding": "chunked"}))

    _error(response, 400, v1.INVALID_ARGUMENT)
    assert response.headers["Connection"] == "close"
    assert response.headers["Cache-Control"] == "no-store"
    assert _files(shell) == []


def test_over_the_limit_the_body_is_not_read(shell, monkeypatch):
    received = []
    monkeypatch.setattr(shell.service, "ReceiveUpload",
                        lambda *args: received.append(args))
    body = _Unread(b"PK" * 10)
    response = shell.client.post(
        UPLOADS, input_stream=body, headers=shell.headers,
        environ_overrides={"CONTENT_LENGTH": str(assets.MAX_UPLOAD_BYTES + 1)})

    _error(response, 413, v1.PAYLOAD_TOO_LARGE, assets.TOO_LARGE)
    assert response.headers["Connection"] == "close"
    assert received == []
    assert assets.MAX_UPLOAD_BYTES == assets.MAX_UNPACKED_BYTES


def test_the_free_space_is_checked_before_anything_is_written(shell,
                                                              monkeypatch):
    monkeypatch.setattr(assets, "_FreeBytes", lambda path: 1024)
    body = _Unread(b"PK" * 10)
    response = shell.client.post(UPLOADS, input_stream=body,
                                 headers=shell.headers)

    _error(response, 409, v1.NOT_READY, assets.DISK_FULL)
    assert response.headers["Connection"] == "close"
    assert _files(shell) == []


def test_the_upload_it_replaces_counts_as_free_space(env, monkeypatch):
    # L1: the held upload's file goes when the new one starts, so its room
    # counts -- and it is not removed first: a refusal leaves it held.
    folder = env.work / "user_data" / ".autona-uploads"
    first = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    newer = Path(_zip(env.root / "b.zip", _game_entries("bbb") | {
        "bbb/full/pad.bin": b"p" * 4096})).read_bytes()
    heldId, _g, _c = env.service.ReceiveUpload(io.BytesIO(first), len(first))
    [heldFile] = os.listdir(folder)
    heldSize = os.path.getsize(folder / heldFile)
    need = len(newer) + assets.DISK_MARGIN_BYTES

    monkeypatch.setattr(assets, "_FreeBytes",
                        lambda path: need - heldSize - 1)
    with pytest.raises(AssetPackError) as refused:
        env.service.ReceiveUpload(io.BytesIO(newer), len(newer))
    assert refused.value.code == assets.DISK_FULL
    assert env.service.UploadGames(heldId)[0] == ["aaa"]
    assert os.listdir(folder) == [heldFile]

    monkeypatch.setattr(assets, "_FreeBytes", lambda path: need - heldSize)
    newerId, games, _c = env.service.ReceiveUpload(io.BytesIO(newer),
                                                   len(newer))
    assert games == ["bbb"] and len(os.listdir(folder)) == 1

    # Nothing held to replace: the same free space is short.
    env.service.DropUpload(newerId)
    with pytest.raises(AssetPackError) as refused:
        env.service.ReceiveUpload(io.BytesIO(newer), len(newer))
    assert refused.value.code == assets.DISK_FULL
    assert os.listdir(folder) == []


@pytest.mark.parametrize("fail", [ConnectionResetError("gone"), None],
                         ids=["reset", "short"])
def test_a_body_that_stops_leaves_no_file(shell, fail):
    data = _zipBytes(shell, _game_entries("aaa"))
    body = _Body(data[:len(data) // 2], fail=fail)
    response = shell.client.post(
        UPLOADS, input_stream=body, headers=shell.headers,
        environ_overrides={"CONTENT_LENGTH": str(len(data))})

    _error(response, 400, v1.INVALID_ARGUMENT, assets.INCOMPLETE)
    assert response.headers["Connection"] == "close"
    assert _files(shell) == []
    assert shell.service._upload is None


def test_bytes_past_the_declared_length_are_not_read(shell):
    # werkzeug's request.stream is a LimitedStream at the Content-Length:
    # what a client sends past it never reaches core.
    data = _zipBytes(shell, _game_entries("aaa"))
    body = _Body(data + b"x" * 4096)
    response = shell.client.post(
        UPLOADS, input_stream=body, headers=shell.headers,
        environ_overrides={"CONTENT_LENGTH": str(len(data))})

    assert response.status_code == 201, response.get_data(as_text=True)
    [held] = _files(shell)
    assert (shell.uploads / held).read_bytes() == data


def test_the_body_is_read_a_chunk_at_a_time(env):
    sizes = []
    data = Path(_zip(env.root / "a.zip", _game_entries("aaa") | {
        "aaa/full/big.png": os.urandom(3 * assets.UPLOAD_CHUNK)})).read_bytes()
    stream = io.BytesIO(data)

    class Counted:
        def read(self, size):
            sizes.append(size)
            return stream.read(size)

    uploadId, games, _conflicts = env.service.ReceiveUpload(
        Counted(), len(data))
    try:
        assert games == ["aaa"]
        assert max(sizes) == assets.UPLOAD_CHUNK == 1024 * 1024
        assert sum(sizes) == len(data)
    finally:
        env.service.DropUpload(uploadId)


def test_a_zip_that_is_refused_is_removed_and_answered_in_fixed_words(
        shell):
    response = _upload(shell, b"not a zip at all", "C:/Users/me/x.zip")
    error = _error(response, 400, v1.INVALID_ARGUMENT, assets.NOT_A_ZIP)
    assert error["message"] == v1._ASSET_REFUSALS[assets.NOT_A_ZIP][1]
    assert _files(shell) == []


def test_a_new_upload_replaces_the_held_one(shell):
    first = _uploaded(shell)
    [firstFile] = _files(shell)
    second = _uploaded(shell, _game_entries("bbb"))

    assert first["upload"] != second["upload"]
    assert len(_files(shell)) == 1 and firstFile not in _files(shell)
    _error(_post(shell, IMPORT, {"upload": first["upload"]}), 404,
           v1.NOT_FOUND, assets.UNKNOWN_UPLOAD)
    assert shell.client.delete(f"{UPLOADS}/{first['upload']}",
                               headers=shell.headers).get_json()[
        "data"] == {"removed": False}
    assert _files(shell) != []


@pytest.mark.parametrize("chunk", [None, 64], ids=["at-its-end", "midway"])
def test_an_upload_still_arriving_is_replaced_too(env, monkeypatch, chunk):
    # Seen after the body arrived, or between two chunks: the rest is not
    # read then.
    if chunk is not None:
        monkeypatch.setattr(assets, "UPLOAD_CHUNK", chunk)
    held = {}

    class Slow:
        """Reads a chunk, then a newer upload starts."""

        def __init__(self, data):
            self.stream = io.BytesIO(data)
            self.first = True

        def read(self, size):
            if self.first:
                self.first = False
                held["newer"] = env.service.ReceiveUpload(
                    io.BytesIO(newer), len(newer))
            chunk = self.stream.read(size)
            held["read"] = held.get("read", 0) + len(chunk)
            return chunk

    data = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    newer = Path(_zip(env.root / "b.zip", _game_entries("bbb"))).read_bytes()
    with pytest.raises(AssetPackError) as refused:
        env.service.ReceiveUpload(Slow(data), len(data))
    assert refused.value.code == assets.SUPERSEDED
    assert v1._ASSET_REFUSALS[assets.SUPERSEDED][::2] == (v1.CONFLICT, 409)
    if chunk is not None:
        assert held["read"] == chunk < len(data)
    uploadId, games, _conflicts = held["newer"]
    assert games == ["bbb"]
    assert len(os.listdir(env.work / "user_data" / ".autona-uploads")) == 1
    env.service.DropUpload(uploadId)


def test_an_upload_expires(shell):
    data = _uploaded(shell)
    assert data["expiresInSec"] == assets.UPLOAD_EXPIRY_SECS

    shell.clock[0] += assets.UPLOAD_EXPIRY_SECS - 1
    assert shell.service.UploadGames(data["upload"])[0] == ["aaa"]
    shell.clock[0] += 1
    _error(_post(shell, IMPORT, {"upload": data["upload"]}), 404,
           v1.NOT_FOUND, assets.UNKNOWN_UPLOAD)
    assert _files(shell) == []


def test_an_expired_upload_is_removed_by_a_lookup_under_another_id(shell):
    # No timer removes it (no thread beside the executor): the next look at
    # the slot does, whatever id it asks for.
    data = _uploaded(shell)
    shell.clock[0] += assets.UPLOAD_EXPIRY_SECS
    assert len(_files(shell)) == 1
    other = "A" * len(data["upload"])
    assert other != data["upload"]

    response = shell.client.delete(f"{UPLOADS}/{other}",
                                   headers=shell.headers)
    assert response.get_json()["data"] == {"removed": False}
    assert _files(shell) == []
    assert shell.service._upload is None


def test_an_upload_that_expires_before_its_import_is_removed(env):
    # The route looks it up first (UploadGames); ImportUpload's own check is
    # for one that expires between the two.
    data = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    uploadId, _games, _c = env.service.ReceiveUpload(io.BytesIO(data),
                                                     len(data))
    env.clock[0] += assets.UPLOAD_EXPIRY_SECS
    with pytest.raises(AssetPackError) as refused:
        env.service.ImportUpload(uploadId, False)
    assert refused.value.code == assets.UNKNOWN_UPLOAD
    assert os.listdir(env.work / "user_data" / ".autona-uploads") == []
    assert env.service.Job()["status"] == assets.IDLE


def test_a_dropped_upload_is_removed(shell):
    data = _uploaded(shell)
    path = f"{UPLOADS}/{data['upload']}"

    response = shell.client.delete(path, headers=shell.headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["data"] == {"removed": True}
    assert _files(shell) == []
    assert shell.client.delete(path, headers=shell.headers).get_json()[
        "data"] == {"removed": False}


def test_a_left_upload_is_removed_but_the_held_one(env):
    data = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    uploadId, _games, _c = env.service.ReceiveUpload(io.BytesIO(data),
                                                     len(data))
    folder = env.work / "user_data" / ".autona-uploads"
    _write(folder / "upload-left.zip", b"left")
    _write(folder / "other.part", b"left")

    assert env.service.RemoveStaleUploads() == 2
    assert len(os.listdir(folder)) == 1
    env.service.DropUpload(uploadId)
    assert os.listdir(folder) == []


def test_the_stale_sweep_leaves_an_upload_arriving_and_one_taken(env):
    folder = env.work / "user_data" / ".autona-uploads"
    seen = {}

    class Sweeping:
        """The sweep runs while the body arrives (its file made, empty)."""

        def __init__(self, data):
            self.stream = io.BytesIO(data)

        def read(self, size):
            if not seen:
                seen["removed"] = env.service.RemoveStaleUploads()
                seen["files"] = os.listdir(folder)
            return self.stream.read(size)

    data = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    uploadId, _games, _c = env.service.ReceiveUpload(Sweeping(data),
                                                     len(data))
    assert seen["removed"] == 0 and len(seen["files"]) == 1
    assert env.service._receiving == set()

    # Taken by an import whose job has not run yet.
    assert env.service.ImportUpload(uploadId, False)[0] == assets.STARTED
    assert env.service._upload is None and len(env.service._taken) == 1
    assert env.service.RemoveStaleUploads() == 0
    assert len(os.listdir(folder)) == 1

    # L5: the job ended -- the file removed and no longer one to keep.
    assert _run(env)["imported"] == ["aaa"]
    assert os.listdir(folder) == [] and env.service._taken == set()


# ── importZip ────────────────────────────────────────────────────────────

def test_an_upload_is_imported_as_a_job(shell):
    data = _uploaded(shell)

    response = _post(shell, IMPORT, {"upload": data["upload"]})

    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["data"] == {
        "accepted": True, "operation": "importZip", "job": 1}
    assert len(_files(shell)) == 1          # the job's until it ends
    assert len(shell.service._taken) == 1
    job = _run(shell)
    assert job["status"] == assets.DONE and job["imported"] == ["aaa"]
    assert (shell.games / "aaa" / "base_files" / "config.json").is_file()
    assert _files(shell) == [] and shell.service._taken == set()
    assert shell.client.get(JOB, headers=shell.headers).get_json()[
        "data"]["status"] == assets.DONE
    # Taken: not held any more.
    _error(_post(shell, IMPORT, {"upload": data["upload"]}), 404,
           v1.NOT_FOUND, assets.UNKNOWN_UPLOAD)


def test_overwrite_names_only_the_conflicts_and_needs_confirm(shell):
    _game(shell.games, "aaa", marker=b"old")
    data = _uploaded(shell, _game_entries("aaa") | _game_entries("bbb"))
    assert data["conflicts"] == ["aaa"]
    upload = data["upload"]

    _error(_post(shell, IMPORT, {"upload": upload, "overwrite": ["bbb"],
                                 "confirm": True}),
           400, v1.INVALID_ARGUMENT)
    _error(_post(shell, IMPORT, {"upload": upload, "overwrite": ["aaa"]}),
           409, v1.CONFIRMATION_REQUIRED)
    for bad in ("aaa", [1], [""], {"aaa": True}):
        _error(_post(shell, IMPORT, {"upload": upload, "overwrite": bad,
                                     "confirm": True}),
               400, v1.INVALID_ARGUMENT)
    # None of those took it.
    assert len(_files(shell)) == 1

    response = _post(shell, IMPORT, {"upload": upload, "overwrite": ["aaa"],
                                     "confirm": True})
    assert response.status_code == 202
    job = _run(shell)
    assert sorted(job["imported"]) == ["aaa", "bbb"]
    assert (shell.games / "aaa" / "full" / "a.png").read_bytes() == b"new"


def test_without_overwrite_no_confirm_is_needed_and_installed_games_stay(
        shell):
    _game(shell.games, "aaa", marker=b"old")
    data = _uploaded(shell, _game_entries("aaa") | _game_entries("bbb"))

    assert _post(shell, IMPORT, {"upload": data["upload"],
                                 "overwrite": []}).status_code == 202
    job = _run(shell)
    assert job["imported"] == ["bbb"] and job["skipped"] == ["aaa"]
    assert (shell.games / "aaa" / "full" / "a.png").read_bytes() == b"old"


def test_an_import_while_a_job_runs_is_busy_and_keeps_the_upload(shell):
    other = _zip(shell.root / "other.zip", _game_entries("ccc"))
    assert shell.service.ImportZip(other)[0] == assets.STARTED
    data = _uploaded(shell)

    _error(_post(shell, IMPORT, {"upload": data["upload"]}), 409,
           v1.CONFLICT, assets.BUSY)
    assert len(_files(shell)) == 1
    _run(shell)

    assert _post(shell, IMPORT, {"upload": data["upload"]}).status_code == 202
    assert _run(shell)["imported"] == ["aaa"]


def test_a_job_that_starts_while_the_upload_is_probed_leaves_it_held(
        shell, monkeypatch):
    # M1: ImportUpload's busy check passed, then another job started while
    # ImportZip probed the file: _Start answers BUSY. The file stays and is
    # held again under the same id, its expiry unchanged.
    data = _uploaded(shell)
    [held] = _files(shell)
    expiresAt = shell.service._upload["expiresAt"]
    start = shell.service._Start
    monkeypatch.setattr(shell.service, "_Start",
                        lambda *args, **kwargs: (assets.BUSY, 7))

    _error(_post(shell, IMPORT, {"upload": data["upload"]}), 409,
           v1.CONFLICT, assets.BUSY)
    assert _files(shell) == [held]
    assert shell.service._taken == set()
    assert shell.service._upload["expiresAt"] == expiresAt
    assert shell.service.UploadGames(data["upload"])[0] == ["aaa"]

    monkeypatch.setattr(shell.service, "_Start", start)
    assert _post(shell, IMPORT, {"upload": data["upload"]}).status_code == 202
    assert _run(shell)["imported"] == ["aaa"]
    assert _files(shell) == [] and shell.service._taken == set()


def test_a_newer_upload_during_the_probe_replaces_the_one_a_job_beat(
        env, monkeypatch):
    # One slot: a newer upload that started meanwhile wins; the old file
    # goes.
    folder = env.work / "user_data" / ".autona-uploads"
    data = Path(_zip(env.root / "a.zip", _game_entries("aaa"))).read_bytes()
    newer = Path(_zip(env.root / "b.zip", _game_entries("bbb"))).read_bytes()
    oldId, _g, _c = env.service.ReceiveUpload(io.BytesIO(data), len(data))
    held = {}

    def busy(*args, **kwargs):
        held["newer"] = env.service.ReceiveUpload(io.BytesIO(newer),
                                                  len(newer))
        return assets.BUSY, 7

    monkeypatch.setattr(env.service, "_Start", busy)
    assert env.service.ImportUpload(oldId, False) == (assets.BUSY, 7)
    newerId, games, _c = held["newer"]
    assert games == ["bbb"] and len(os.listdir(folder)) == 1
    assert env.service.UploadGames(newerId)[0] == ["bbb"]
    with pytest.raises(AssetPackError) as refused:
        env.service.UploadGames(oldId)
    assert refused.value.code == assets.UNKNOWN_UPLOAD
    assert env.service._taken == set()


def test_an_import_refused_at_its_probe_removes_the_upload(shell):
    # The held file changed since it was received: ImportZip's probe
    # refuses it, and it goes (L5: no longer one to keep either).
    data = _uploaded(shell)
    [held] = _files(shell)
    (shell.uploads / held).write_bytes(b"not a zip any more")

    _error(_post(shell, IMPORT, {"upload": data["upload"]}), 400,
           v1.INVALID_ARGUMENT, assets.NOT_A_ZIP)
    assert _files(shell) == [] and shell.service._taken == set()


@pytest.mark.parametrize("body", [{}, {"upload": 3}, {"upload": ""},
                                  {"upload": "../x"}])
def test_an_import_names_an_upload(shell, body):
    response = _post(shell, IMPORT, body)
    assert response.status_code in (400, 404)
    assert response.headers["Cache-Control"] == "no-store"


# ── install ──────────────────────────────────────────────────────────────

def test_an_install_needs_confirm_and_the_cached_index(shell):
    body = {"game": "ssbu", "packs": ["full"], "confirm": True}
    _upstream(shell)

    # No index fetched yet: this write does not fetch it.
    _error(_post(shell, INSTALL, body), 409, v1.NOT_READY,
           assets.INDEX_UNAVAILABLE)
    assert shell.net.indexFetches() == 0

    assert shell.service.EnsureIndex()
    _error(_post(shell, INSTALL, {"game": "ssbu", "packs": ["full"]}), 409,
           v1.CONFIRMATION_REQUIRED)
    response = _post(shell, INSTALL, body)
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["data"] == {
        "accepted": True, "operation": "install", "job": 1}
    assert shell.net.indexFetches() == 1
    assert _run(shell)["status"] == assets.DONE


@pytest.mark.parametrize("body, status, code, reason", [
    ({"game": "nope"}, 404, v1.NOT_FOUND, assets.UNKNOWN_GAME),
    ({"game": "ssbu", "packs": ["logo"]}, 400, v1.INVALID_ARGUMENT,
     assets.UNKNOWN_PACK),
    ({"game": "ssbu", "packs": []}, 400, v1.INVALID_ARGUMENT,
     assets.NO_PACKS),
    ({"game": "ssbu", "packs": "full"}, 400, v1.INVALID_ARGUMENT, None),
    ({"game": ""}, 400, v1.INVALID_ARGUMENT, None),
    ({"game": 3}, 400, v1.INVALID_ARGUMENT, None),
])
def test_an_install_is_refused_with_cores_reason(shell, body, status, code,
                                                 reason):
    _upstream(shell)
    assert shell.service.EnsureIndex()
    _error(_post(shell, INSTALL, dict(body, confirm=True)), status, code,
           reason)


def test_the_default_packs_are_installed_when_none_are_named(shell):
    _upstream(shell)
    assert shell.service.EnsureIndex()
    assert _post(shell, INSTALL, {"game": "ssbu", "packs": None,
                                  "confirm": True}).status_code == 202
    job = _run(shell)
    assert job["status"] == assets.DONE


# ── cancel ───────────────────────────────────────────────────────────────

def test_cancel_names_the_job_it_stops(shell):
    assert _post(shell, CANCEL, {"revision": 1}).get_json()["data"] == {
        "cancelled": False}
    _upstream(shell)
    assert shell.service.EnsureIndex()
    assert _post(shell, INSTALL, {"game": "ssbu", "packs": ["full"],
                                  "confirm": True}).status_code == 202

    response = _post(shell, CANCEL, {"revision": 2})
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["data"] == {"cancelled": False}
    assert _post(shell, CANCEL, {"revision": 1}).get_json()["data"] == {
        "cancelled": True}
    assert _run(shell)["status"] == assets.CANCELLED


@pytest.mark.parametrize("revision", [None, "1", True, 0, -1, 1.0])
def test_cancel_takes_a_revision(shell, revision):
    _error(_post(shell, CANCEL, {"revision": revision}), 400,
           v1.INVALID_ARGUMENT)


# ── the games folder ─────────────────────────────────────────────────────

EXPLORER = os.path.join(r"C:\Windows", "explorer.exe")


@pytest.mark.parametrize("platform, expected", [
    ("win32", lambda games: f'"{EXPLORER}" "{games}"'),
    ("darwin", lambda games: ["open", games]),
    ("linux", lambda games: ["xdg-open", games]),
])
def test_the_games_folder_is_shown_and_takes_no_path(shell, monkeypatch,
                                                     platform, expected):
    started = []
    monkeypatch.setattr(assets.subprocess, "Popen",
                        lambda args, **kwargs: started.append(args))
    monkeypatch.setattr(assets.sys, "platform", platform)
    monkeypatch.setenv("WINDIR", r"C:\Windows")

    response = _post(shell, REVEAL, {"path": "C:/Windows"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    games = os.path.abspath(assets.GAMES_DIR)
    assert started == [expected(games)]
    assert shell.games.is_dir()


# ── what every route shares ──────────────────────────────────────────────

ROUTES = [
    ("POST", UPLOADS), ("DELETE", f"{UPLOADS}/abcdefgh"), ("POST", IMPORT),
    ("POST", INSTALL), ("POST", CANCEL), ("POST", REVEAL), ("GET", JOB)]


@pytest.mark.parametrize("method, path", ROUTES)
def test_each_route_is_the_shared_views_alone(shell, monkeypatch, method,
                                              path):
    started = []
    monkeypatch.setattr(assets.subprocess, "Popen",
                        lambda args, **kwargs: started.append(args))
    # This PC's own request without the secret, and with a wrong one.
    for headers in ({"Sec-Fetch-Site": "same-origin"},
                    dict(shell.headers, **{WebAccess.SHELL_HEADER: "x"}),
                    {}):
        response = shell.client.open(path, method=method, headers=headers,
                                     data=b"PK")
        assert response.status_code == 403, (headers, response.get_data())
        assert response.get_json()["error"]["code"] == "FORBIDDEN"
    assert started == [] and _files(shell) == []


@pytest.mark.parametrize("method, path", ROUTES)
def test_a_request_the_shell_check_refuses_is_not_read(shell, monkeypatch,
                                                       method, path):
    # Refused before anything reads its body (_Unread fails on a read).
    monkeypatch.setattr(assets.subprocess, "Popen",
                        lambda args, **kwargs: None)
    for headers in ({"Sec-Fetch-Site": "same-origin"},
                    dict(shell.headers, **{WebAccess.SHELL_HEADER: "x"}),
                    {}):
        response = shell.client.open(path, method=method, headers=headers,
                                     input_stream=_Unread(b"PK" * 10))
        assert response.status_code == 403, (headers, response.get_data())
    assert _files(shell) == []


@pytest.mark.parametrize("method, path", ROUTES)
def test_each_answer_is_no_store(shell, monkeypatch, method, path):
    monkeypatch.setattr(assets.subprocess, "Popen",
                        lambda args, **kwargs: None)
    # A refusal of most (not a ZIP / no upload / no game / no revision).
    response = shell.client.open(path, method=method, headers=shell.headers,
                                 json={"revision": "x"})
    assert response.headers["Cache-Control"] == "no-store"


def test_every_refusal_core_raises_has_fixed_words():
    tree = ast.parse((SRC / "core" / "AssetPackService.py").read_text(
        encoding="utf-8"))
    raised = {getattr(assets, call.args[0].id)
              for call in ast.walk(tree) if isinstance(call, ast.Call)
              and isinstance(call.func, ast.Name)
              and call.func.id == "AssetPackError"
              and call.args and isinstance(call.args[0], ast.Name)}
    assert {assets.NOT_A_ZIP, assets.UNKNOWN_UPLOAD, assets.INCOMPLETE,
            assets.SUPERSEDED, assets.DISK_FULL} <= raised
    assert raised | {assets.BUSY} <= set(v1._ASSET_REFUSALS)
    for code, (api, words, status) in v1._ASSET_REFUSALS.items():
        assert isinstance(words, str) and words and "{" not in words
        refusal = v1._asset_refused("x", code, "C:\\Users\\me\\secret")
        assert (refusal.code, refusal.message, refusal.status,
                refusal.reason) == (api, words, status, code)


def test_a_failure_before_the_hold_removes_the_upload(shell, monkeypatch):
    # L6: reading the installed games fails -- 500, nothing held, no file.
    def fail(games):
        raise RuntimeError("the games folder could not be read")

    monkeypatch.setattr(assets, "ImportConflicts", fail)
    response = _upload(shell, _zipBytes(shell, _game_entries("aaa")))
    _error(response, 500, v1.INTERNAL)
    assert shell.service._upload is None
    assert _files(shell) == []


def test_cores_message_is_not_answered(shell, monkeypatch):
    def refuse(*args):
        raise AssetPackError(assets.NOT_A_ZIP,
                             r"not a readable ZIP: C:\Users\me\secret.zip")

    monkeypatch.setattr(shell.service, "ReceiveUpload", refuse)
    response = _upload(shell, b"PK" * 10)
    error = _error(response, 400, v1.INVALID_ARGUMENT, assets.NOT_A_ZIP)
    assert "secret" not in response.get_data(as_text=True)
    assert set(error) == {"code", "message", "reason"}


def test_an_error_without_a_reason_is_as_it_was(shell):
    # error.reason is added only where core gave one.
    body = _post(shell, CANCEL, {"revision": "x"}).get_json()
    assert set(body["error"]) == {"code", "message"}


# ── the logs ─────────────────────────────────────────────────────────────

def test_nothing_of_the_upload_reaches_the_logs(shell):
    everything, info = [], []
    sinks = [logger.add(lambda m: everything.append(str(m)), level="DEBUG"),
             logger.add(lambda m: info.append(str(m)), level="INFO")]
    try:
        entries = _game_entries("aaa") | {
            "aaa/full/marker.png": b"BODY-MARKER-7Q"}
        response = _upload(shell, _zipBytes(shell, entries),
                           "private-name-7Q.zip")
        assert response.status_code == 201
        first = response.get_json()["data"]["upload"]
        refused = _upload(shell, b"not a zip", "private-name-7Q.zip")
        assert refused.status_code == 400
        # (It replaced the first at its start.) Again, and imported.
        second = _uploaded(shell, entries, "private-name-7Q.zip")["upload"]
        assert _post(shell, IMPORT, {"upload": second}).status_code == 202
        _run(shell)
        dropped = _uploaded(shell, entries, "private-name-7Q.zip")["upload"]
        assert shell.client.delete(f"{UPLOADS}/{dropped}",
                                   headers=shell.headers).status_code == 200
    finally:
        for sink in sinks:
            logger.remove(sink)

    text = "\n".join(everything)
    for secret in ("private-name-7Q", "BODY-MARKER-7Q", str(shell.work),
                   "autona-uploads", assets.UPLOAD_PREFIX):
        assert secret not in text, secret
    infoText = "\n".join(info)
    for uploadId in (first, second, dropped):
        assert uploadId not in infoText
    opLines = [line for line in info if "op=assets." in line]
    assert any("params=<" in line and " bytes>" in line for line in opLines)
    assert any(f"/uploads/{dropped[:6]}…" in line for line in opLines)
    assert any("op=assets.importZip params=['upload']" in line
               for line in opLines)


# ── write_op(body="none") ────────────────────────────────────────────────

def test_a_route_that_reads_no_body_gets_it_whole_and_logs_its_size():
    from flask import Blueprint, Flask, request

    app = Flask(__name__)
    bp = Blueprint("probe", __name__)
    seen = {}

    @bp.route("/raw/<upload_id>", methods=["POST"])
    @v1.write_op("probe.raw", body=v1.BODY_NONE)
    def raw(upload_id):
        seen["body"] = request.stream.read()
        return v1.ok()

    @bp.route("/refused", methods=["POST"])
    @v1.write_op("probe.refused", body=v1.BODY_NONE)
    def refused():
        raise v1.ApiError(v1.INVALID_ARGUMENT, "no", 400, reason="x")

    app.register_blueprint(bp)
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        client = app.test_client()
        response = client.post("/raw/abcdefghijkl",
                               json={"secret": "SECRET-VALUE"})
        refusal = client.post("/refused", data=b"xyz")
    finally:
        logger.remove(sink)

    assert response.status_code == 200
    assert json.loads(seen["body"]) == {"secret": "SECRET-VALUE"}
    assert refusal.status_code == 400
    assert refusal.headers["Connection"] == "close"
    assert refusal.get_json()["error"]["reason"] == "x"
    text = "\n".join(lines)
    assert "SECRET-VALUE" not in text and "secret" not in text
    assert (f"/raw/abcdef… op=probe.raw params=<{len(seen['body'])} bytes>"
            " -> 200") in text
    assert "abcdefghijkl" not in text
    assert "op=probe.refused params=<3 bytes> -> 400 INVALID_ARGUMENT (x)" \
        in text
    with pytest.raises(ValueError):
        v1.write_op("probe.bad", body="raw")
