"""How the ./out export decides that a state value is an image (LIVE-BUG-2).

CreateFilesDict is the whole OBS integration: every leaf of the state tree
becomes a file under ./out, and a leaf whose value is an http URL pointing at
an image becomes the *downloaded image* rather than a text file holding the
URL. An OBS image source points at ./out/score/1/team/1/player/1/
online_avatar.png and never learns about anything else.

That decision used to be `di.endswith(".png") or di.endswith(".jpg")`, and
start.gg hands its avatars out with a cache key attached:

    https://images.start.gg/images/user/.../image-<hash>.jpg?ehk=<key>

which ends in the query string, not in ".jpg". So a real avatar fell through
to the plain-text branch and was written as online_avatar.txt containing the
URL. The Web UI reads the URL straight out of program_state.json and looked
fine, which is why this survived: only an OBS image source could see it.
Found by tests/live against the real API.

The extension now comes from urlparse(...).path, and these tests pin every
branch that depends on it. requests.get is replaced by a fake that serves
bytes from memory -- the export layer is what is under test, not the network.

Most strings in the state are text a client typed, so what looks like a path or
a URL is not taken at its word (the last two sections): only an image the web
server serves anyway is copied, and only start.gg's image host is fetched.
Before that, PATCH /api/v1/result/players/1 {"name": "./user_data/settings.json"}
put the settings file, with the web server's token, in ./out, which is served.
"""
import io
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

# The module object, not the shim: `requests` is patched on it below, and
# the binding lives in src/core/StateManager.py since headless core Step 1
# (src/StateManager.py only re-exports the class).
import src.core.StateManager as state_manager
from src.core.CountryCatalog import CUSTOM_FLAG_DIR
from src.core.GameAssetCatalog import CANCEL_ICON_PATH, GAMES_DIR
from src.core.PlayerService import AVATAR_DIR
from src.core.SetLoadService import TEAM_LOGO_DIR
from src.core.StateManager import StateManager as SM
from src.core.StateSchema import (
    STREAM_ORG_ICON_PATH, STREAM_ORG_THUMB_PATH, TOURNAMENT_BANNER_PATH)
from src.core.ThumbnailService import DEFAULT_BANNER_PATH
from src.core.TournamentInfoService import TOURNAMENT_LOGO_PATH

_ORIGINAL_CWD = os.getcwd()

_AVATAR = "https://images.start.gg/images/user/1/image-9f3"


@pytest.fixture(autouse=True)
def _in_work_dir(tmp_path):
    # CreateFilesDict writes ./out relative to the process CWD, and each test
    # gets its own so a leftover file can never satisfy another's assertion.
    (tmp_path / "out").mkdir()
    state_manager._partsSwept = False
    os.chdir(tmp_path)
    try:
        yield
    finally:
        # Never restore the CWD with a download still resolving its relative
        # output path (tests/conftest.py settle() has the same rule).
        SM.JoinDownloads()
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def exported(tmp_path):
    def _exported(*parts):
        return Path(tmp_path, "out", *parts)
    return _exported


def _imageBytes(fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (12, 34, 56)).save(buffer, format=fmt)
    return buffer.getvalue()


class _Raw(io.BytesIO):
    # shutil.copyfileobj(r.raw, ...) after r.raw.decode_content = True, so the
    # body has to be a real file object that accepts that attribute.
    decode_content = False


@pytest.fixture
def served(monkeypatch):
    """Serve one in-memory image for every download; record the URLs asked for.

    Only the module-level `requests` binding inside StateManager is replaced,
    so nothing else in the session sees a patched requests.
    """
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        fmt = "JPEG" if SM._FileExtension(url) == "jpg" else "PNG"
        return SimpleNamespace(status_code=200, raw=_Raw(_imageBytes(fmt)))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))
    return requested


# ── the extension itself ─────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("https://cdn.example.invalid/a/image-9f3.jpg?ehk=abc%3D", "jpg"),
    ("https://cdn.example.invalid/a/image-9f3.jpg", "jpg"),
    ("https://cdn.example.invalid/a/image-9f3.PNG", "png"),
    ("https://cdn.example.invalid/a/image.png?w=64&h=64", "png"),
    ("./user_data/player_avatar/SPONSOR TAG.png", "png"),
    # A query string that merely mentions an extension is not one; a path
    # with no dot at all yields itself, which is never an image extension.
    ("https://cdn.example.invalid/redirect?to=avatar.png", "/redirect"),
])
def test_the_extension_ignores_the_query_string(value, expected):
    assert SM._FileExtension(value) == expected


# ── which branch a value takes ───────────────────────────────────────────

def test_a_cache_keyed_jpg_url_is_downloaded_as_an_image(exported, served):
    """The bug, at the layer that had it.

    .jpg is stored converted (OBS wants one filename), so the file that has
    to appear is the .png -- and no .txt may be left holding the URL.
    """
    url = f"{_AVATAR}.jpg?ehk=abc"

    SM.CreateFilesDict("probe/online_avatar", url)
    SM.JoinDownloads()

    assert served == [url]
    landed = exported("probe", "online_avatar.png")
    assert landed.is_file(), "the avatar never landed as an image"
    with Image.open(landed) as image:
        assert (image.width, image.height) == (4, 3)

    assert not exported("probe", "online_avatar.txt").exists()
    # The intermediate download must not survive the conversion, or ./out
    # ends up with two avatars for one player.
    assert not exported("probe", "online_avatar.jpg").exists()


def test_a_cache_keyed_png_url_is_downloaded_as_an_image(exported, served):
    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png?ehk=abc")
    SM.JoinDownloads()

    assert exported("probe", "online_avatar.png").is_file()
    assert not exported("probe", "online_avatar.txt").exists()


def test_a_plain_image_url_still_downloads(exported, served):
    # Characterization: the case that always worked must keep working.
    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png")
    SM.JoinDownloads()

    assert exported("probe", "online_avatar.png").is_file()


def test_a_non_image_url_is_still_written_as_text(exported, served):
    """Not every http value is an image; twitter handles and the like are not.

    Widening the image test is the obvious way to fix the bug wrongly -- e.g.
    "startswith http and has a query string" -- and this is what that breaks.
    """
    url = "https://start.gg/user/abc123"

    SM.CreateFilesDict("probe/profile", url)
    SM.JoinDownloads()

    assert served == []
    assert exported("probe", "profile.txt").read_text(encoding="utf-8") == url
    assert not exported("probe", "profile.png").exists()


def test_a_query_string_mentioning_png_is_not_an_image(exported, served):
    url = "https://images.start.gg/redirect?to=avatar.png"

    SM.CreateFilesDict("probe/profile", url)
    SM.JoinDownloads()

    assert served == []
    assert exported("probe", "profile.txt").read_text(encoding="utf-8") == url


def test_a_plain_string_is_written_as_text(exported, served):
    SM.CreateFilesDict("probe/name", "TAG")

    assert exported("probe", "name.txt").read_text(encoding="utf-8") == "TAG"


# ── and the same value can be removed again ──────────────────────────────

def test_removing_a_cache_keyed_avatar_deletes_the_downloaded_file(
        exported, served):
    """RemoveFilesDict derives the filename the same way CreateFilesDict does.

    Otherwise the key can go away while the .png stays, and OBS keeps showing
    the previous entrant's face with nothing in the state tree explaining it.
    """
    url = f"{_AVATAR}.jpg?ehk=abc"
    SM.CreateFilesDict("probe/online_avatar", url)
    SM.JoinDownloads()
    assert exported("probe", "online_avatar.png").is_file()

    SM.RemoveFilesDict("probe/online_avatar", url)

    assert not exported("probe", "online_avatar.png").exists()


# ── a local path: what is copied ─────────────────────────────────────────

def _file(path, content=b"the file's bytes"):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.mark.parametrize("value, extension", [
    # Every root the app publishes, by the constants that name them. A new one
    # that fails here is one that has to be served (WebAccess.STATIC_*) first.
    ("./assets/country_flag/JP.png", "png"),
    ("./assets/state_flag/JP/13.png", "png"),
    (CANCEL_ICON_PATH, "svg"),
    (DEFAULT_BANNER_PATH, "png"),
    (f"{GAMES_DIR}ssbu/full/chara_0_mario_00.png", "png"),
    (f"{GAMES_DIR}ssbu//full/chara_0_mario_00.png", "png"),
    (f"{GAMES_DIR}ssbu/base_files/logo.png", "png"),
    (f"{CUSTOM_FLAG_DIR}/mine.png", "png"),
    (f"{CUSTOM_FLAG_DIR}/mine.jpg", "jpg"),
    (f"{CUSTOM_FLAG_DIR}/mine.JPEG", "JPEG"),
    (f"{CUSTOM_FLAG_DIR}/mine.webp", "webp"),
    (f"{AVATAR_DIR}/SPN Tag.png", "png"),
    (f"{TEAM_LOGO_DIR}/reds.png", "png"),
    ("./user_data/sponsor_logo/SPN.png", "png"),
    (TOURNAMENT_BANNER_PATH, "png"),
    (TOURNAMENT_LOGO_PATH, "png"),
    (STREAM_ORG_ICON_PATH, "png"),
    (STREAM_ORG_THUMB_PATH, "png"),
])
def test_an_image_the_app_publishes_is_copied_as_it_always_was(
        exported, served, value, extension):
    """What an OBS image source points at. Under its own extension, its case
    included, and no text next to it."""
    _file(value)

    SM.CreateFilesDict("probe/asset", value)
    SM.JoinDownloads()

    assert exported("probe", f"asset.{extension}").read_bytes() \
        == b"the file's bytes"
    assert not exported("probe", "asset.txt").exists()
    assert served == []

    SM.RemoveFilesDict("probe/asset", value)

    assert not exported("probe", f"asset.{extension}").exists()


def test_a_published_image_that_is_gone_writes_nothing(exported, served):
    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/nobody.png")

    assert not exported("probe", "asset.png").exists()
    assert not exported("probe", "asset.txt").exists()


def test_the_copy_replaces_the_one_before(exported, served):
    _file(f"{AVATAR_DIR}/a.png", b"a")
    _file(f"{AVATAR_DIR}/b.png", b"b")

    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/a.png")
    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/b.png")

    assert exported("probe", "asset.png").read_bytes() == b"b"


@pytest.mark.parametrize("value", [
    # The probe: the settings hold the web server's token.
    "./user_data/settings.json",
    "./user_data/state.json",
    "./user_data/local_players.json",
    # Served, not images.
    "./layout/custom/page/index.html",
    "./assets/countries.json",
    "./user_data/games/ssbu/base_files/config.json",
    # Images, not served.
    "./secret.png",
    "./user_data/secret.png",
    "./user_data/thumbnail_background_1.png",
    "./tmp/result/result_preview.png",
    "./logs/a.png",
    "./src/a.png",
    # Out of a served folder.
    "./assets/../secret.png",
    "./assets/../user_data/settings.json",
    "./user_data/games/../secret.png",
    "./layout/..\\secret.png",
    ".//assets/../../secret.png",
    "./../secret.png",
    # Absolute, a drive, a stream, NUL, a name Windows reads as another's.
    ".//secret.png",
    "./C:/secret.png",
    "./assets/a.png:hidden.png",
    "./assets/a\x00.png",
    "./layout./secret.png",
    # The extension a path would be given, with a separator in it.
    "./assets/a.b/../../../secret",
    "./assets/a.png/",
    # Text.
    "./",
    "./foo",
    "./.",
])
def test_a_path_typed_in_a_text_field_is_text(exported, served, value, tmp_path):
    """Written down, as any other name, whatever is at that path."""
    for path in ("user_data/settings.json", "user_data/state.json",
                 "user_data/local_players.json", "secret.png",
                 "user_data/secret.png", "user_data/thumbnail_background_1.png",
                 "tmp/result/result_preview.png", "logs/a.png", "src/a.png",
                 "layout/custom/page/index.html", "assets/countries.json",
                 "user_data/games/ssbu/base_files/config.json"):
        _file(tmp_path / path)
    before = sorted(str(path) for path in tmp_path.rglob("*"))

    SM.CreateFilesDict("probe/name", value)
    SM.JoinDownloads()

    written = exported("probe", "name.txt")
    assert written.read_text(encoding="utf-8") == value
    assert served == []
    after = sorted(str(path) for path in tmp_path.rglob("*"))
    assert sorted(set(after) - set(before)) == sorted(
        str(path) for path in (exported("probe"), written))

    SM.RemoveFilesDict("probe/name", value)

    assert not written.exists()
    assert sorted(str(path) for path in tmp_path.rglob("*")
                  if path != exported("probe")) == before


def test_a_whole_row_goes_through_the_same_door(exported, served):
    """What ExportText hands over when a row is written at once."""
    _file("user_data/settings.json", b'{"secret": true}')

    SM.CreateFilesDict("result_players/1", {
        "name": "./user_data/settings.json", "rank": 1,
        "character": {"assets": {"full": {"asset": "./user_data/settings.json"}}},
    })

    assert exported("result_players", "1", "name.txt").read_text(
        encoding="utf-8") == "./user_data/settings.json"
    assert not list(Path(exported()).rglob("*.json"))


def test_removing_a_path_never_reaches_out_of_the_export(exported, tmp_path):
    """The extension of the path was added to the one it removes."""
    outside = tmp_path / "keep"
    outside.write_text("still here", encoding="utf-8")

    SM.RemoveFilesDict("probe/name", "./a.b/../../../keep")

    assert outside.read_text(encoding="utf-8") == "still here"


# ── a URL: what is fetched ───────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "http://images.start.gg/a.png",               # not https
    "https://example.invalid/a.png",
    "http://example.invalid/a.jpg",
    "http://127.0.0.1:5107/out/a.png",            # this machine
    "https://localhost/a.png",
    "http://192.168.0.1/a.png",                   # the LAN
    "http://169.254.169.254/latest/a.png",
    "https://images.start.gg.example.invalid/a.png",
    "https://images.start.gg@example.invalid/a.png",
    "https://example.invalid@images.start.gg/a.png",
    "https://images.start.gg:8443/a.png",
    "https://example.invalid\\@images.start.gg/a.png",  # requests reads this
    "https://example.invalid#@images.start.gg/a.png",
    "https://images.start.gg\t@example.invalid/a.png",
    "https://images.start.gg/a.png\n",
    "https://images.start.gg/a.gif",               # what is converted or kept
    "https://images.start.gg/a.svg",
    "https://images.start.gg/a",
    "ftp://images.start.gg/a.png",
    "https://[",                                   # a text a client typed
    "http://[",
    "http",
    "https",
])
def test_a_url_that_is_not_the_image_host_is_text(exported, served, value):
    SM.CreateFilesDict("probe/name", value)
    SM.JoinDownloads()

    assert served == []
    written = exported("probe", "name.txt").read_bytes().decode("utf-8")
    assert written.replace("\r\n", "\n") == value
    assert not exported("probe", "name.png").exists()

    SM.RemoveFilesDict("probe/name", value)

    assert not exported("probe", "name.txt").exists()


@pytest.mark.parametrize("value", [
    "https://images.start.gg/images/user/1/image-abc.jpg?ehk=key%3D&ehkOptimized=x",
    "https://images.start.gg/images/user/1/image-abc.png",
    "https://IMAGES.START.GG/images/user/1/image-abc.PNG",
    "HTTPS://images.start.gg/a.jpg",
    "https://images.smash.gg/images/user/1/image-abc.jpg",
])
def test_an_avatar_from_the_image_host_is_fetched(exported, served, value):
    SM.CreateFilesDict("probe/online_avatar", value)
    SM.JoinDownloads()

    assert served == [value]
    assert exported("probe", "online_avatar.png").is_file()
    assert not exported("probe", "online_avatar.txt").exists()


# ── a key: where it may write ────────────────────────────────────────────
#
# The keys come from the state tree, and game.defaults is a game's
# config.json taken as it is: a key ".." there used to put a .txt outside
# ./out, and RemoveFilesDict to delete (rmtree, even) outside it.

def _everything(root):
    return sorted(str(path) for path in Path(root).rglob("*"))


@pytest.mark.parametrize("path", [
    "game/defaults/../../../evil",
    "../evil",
    "..",
    "game/..",
    "game/./evil",
    "game//evil",
    "game/evil/",
    "a:b",
    "game/a:b/evil",
    "C:/evil",
    "/evil",
    "game/x\\..\\..\\..\\evil",
])
def test_a_key_out_of_the_export_writes_nothing(tmp_path, path):
    before = _everything(tmp_path)
    beside = sorted(os.listdir(tmp_path.parent))

    SM.CreateFilesDict(path, "x")
    SM.CreateFilesDict(path, {"name": "x"})

    assert _everything(tmp_path) == before
    assert sorted(os.listdir(tmp_path.parent)) == beside


def test_a_row_with_a_key_out_of_the_export_writes_only_inside(
        tmp_path, exported):
    beside = sorted(os.listdir(tmp_path.parent))

    SM.CreateFilesDict("game/defaults",
                       {"..": {"..": {"evil": "x"}}, "name": "kept"})

    assert exported("game", "defaults", "name.txt").read_text(
        encoding="utf-8") == "kept"
    assert [path for path in _everything(tmp_path)
            if "evil" in path] == []
    assert all(Path(path) == exported()
               or Path(path).is_relative_to(exported())
               for path in _everything(tmp_path))
    assert sorted(os.listdir(tmp_path.parent)) == beside


def test_an_ordinary_key_is_still_written(exported):
    SM.CreateFilesDict("game/defaults/name", "x")
    SM.CreateFilesDict("game/defaults/..x", "y")

    assert exported("game", "defaults", "name.txt").read_text(
        encoding="utf-8") == "x"
    assert exported("game", "defaults", "..x.txt").read_text(
        encoding="utf-8") == "y"


def test_removing_a_key_out_of_the_export_deletes_nothing(tmp_path, exported):
    (tmp_path / "keep.txt").write_text("still here", encoding="utf-8")
    kept = tmp_path / "kept"
    kept.mkdir()
    (kept / "a.txt").write_text("still here", encoding="utf-8")
    SM.CreateFilesDict("probe/name", "x")

    SM.RemoveFilesDict("../keep", "x")
    SM.RemoveFilesDict("../kept", {"a": "x"})
    SM.RemoveFilesDict("probe/..", {"name": "x"})
    SM.RemoveFilesDict("..", {})

    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "still here"
    assert (kept / "a.txt").read_text(encoding="utf-8") == "still here"
    assert exported("probe", "name.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("path", [
    ".. ", "x/.. ", ". ", "x/. ", "x/a.", "x/... ", "x/a ",
])
def test_a_key_ending_in_a_space_or_dot_is_refused(tmp_path, exported, path):
    # Windows resolves ".. " to ".." (and "a." to "a"), so abspath alone
    # let ".. " name ./out itself.
    (tmp_path / "keep.txt").write_text("still here", encoding="utf-8")
    before = _everything(tmp_path)

    SM.CreateFilesDict(path, "x")
    SM.CreateFilesDict(path, {"name": "x"})
    SM.RemoveFilesDict(path, "x")
    SM.RemoveFilesDict(path, {"name": "x"})

    assert _everything(tmp_path) == before
    assert not state_manager._InsideOut(path)


def test_removing_dot_dot_space_leaves_the_export_alone(exported):
    SM.CreateFilesDict("probe/name", "x")

    SM.RemoveFilesDict(".. ", {"probe": {"name": "x"}})

    assert exported("probe", "name.txt").read_text(encoding="utf-8") == "x"


# ── stale part files and failed removals ─────────────────────────────────

def test_the_first_export_sweeps_the_part_files_a_killed_download_left(
        tmp_path, exported):
    (tmp_path / "out" / "probe").mkdir()
    mine = [exported("probe", "a.png.7.part"),
            exported("probe", "a.12.png.part")]
    other = [exported("probe", "notes.part"),
             exported("probe", "b.txt"),
             exported("probe", "c.x.part")]
    for path in mine + other:
        path.write_text("x", encoding="utf-8")

    SM.ExportText({}, {}, {})

    assert [p.exists() for p in mine] == [False, False]
    assert [p.exists() for p in other] == [True, True, True]


def test_the_sweep_runs_once_per_process(exported):
    SM.ExportText({}, {}, {})
    late = exported("a.png.3.part")
    late.write_text("x", encoding="utf-8")

    SM.ExportText({}, {}, {})

    assert late.exists()


def test_a_removal_that_fails_is_warned_about_but_not_a_missing_file(
        tmp_path, logged, monkeypatch):
    state_manager._remove_quietly(str(tmp_path / "gone.png"))
    assert not logged

    def _refuse(path):
        raise PermissionError("locked")
    monkeypatch.setattr(state_manager.os, "remove", _refuse)

    state_manager._remove_quietly("./out/probe/a.png")

    assert any("Could not remove ./out/probe/a.png" in record["message"]
               for record in logged)


# ── a download: what lands, and when ─────────────────────────────────────

_A = f"{_AVATAR}-a.png"
_B = f"{_AVATAR}-b.png"


@pytest.fixture
def gated(monkeypatch):
    """_A waits for `release`; every other URL answers at once. The body is
    the URL's last letter, so what landed says which download it was."""
    started = threading.Event()
    release = threading.Event()

    def fake_get(url, **kwargs):
        if url == _A:
            started.set()
            assert release.wait(10), "the test never released A"
        return SimpleNamespace(status_code=200,
                               raw=_Raw(url[-5:-4].encode()))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))
    return SimpleNamespace(started=started, release=release)


def _waitFor(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _parts(tmp_path):
    return [path for path in _everything(tmp_path) if ".part" in path]


def test_a_late_download_never_lands_over_a_newer_one(
        exported, gated, tmp_path):
    landed = exported("probe", "online_avatar.png")

    SM.CreateFilesDict("probe/online_avatar", _A)
    assert gated.started.wait(10)
    SM.CreateFilesDict("probe/online_avatar", _B)
    _waitFor(lambda: landed.is_file())
    gated.release.set()
    SM.JoinDownloads()

    assert landed.read_bytes() == b"b"
    assert _parts(tmp_path) == []
    assert "./out/probe/online_avatar" not in SM._download_latest


def test_a_newer_text_wins_over_a_late_download(exported, gated, tmp_path):
    SM.CreateFilesDict("probe/online_avatar", _A)
    assert gated.started.wait(10)
    SM.CreateFilesDict("probe/online_avatar", "plain text")
    gated.release.set()
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert exported("probe", "online_avatar.txt").read_text(
        encoding="utf-8") == "plain text"
    assert _parts(tmp_path) == []


def test_a_removal_during_a_download_stays_removed(exported, gated, tmp_path):
    SM.CreateFilesDict("probe/online_avatar", _A)
    assert gated.started.wait(10)
    SM.RemoveFilesDict("probe/online_avatar", _A)
    gated.release.set()
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert _parts(tmp_path) == []
    assert "./out/probe/online_avatar" not in SM._download_latest


class _Recording(io.BytesIO):
    decode_content = False

    def __init__(self, data, onRead):
        super().__init__(data)
        self._onRead = onRead

    def read(self, *args):
        self._onRead()
        return super().read(*args)


def test_the_image_before_stays_until_the_new_one_is_complete(
        exported, monkeypatch, tmp_path):
    landed = exported("probe", "online_avatar.png")
    landed.parent.mkdir(parents=True)
    landed.write_bytes(b"old")
    seen = []

    def fake_get(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            raw=_Recording(b"new", lambda: seen.append(
                (landed.is_file(), landed.read_bytes()))))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png")
    SM.JoinDownloads()

    assert seen and all(entry == (True, b"old") for entry in seen)
    assert landed.read_bytes() == b"new"
    assert _parts(tmp_path) == []


def test_a_download_superseded_while_waiting_for_a_slot_never_asks(
        exported, monkeypatch, tmp_path):
    # Every slot taken: both downloads queue up behind the semaphore, and A
    # has stopped being the latest before it gets one.
    monkeypatch.setattr(SM, "_download_semaphore", threading.Semaphore(0))
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return SimpleNamespace(status_code=200,
                               raw=_Raw(url[-5:-4].encode()))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", _A)
    SM.CreateFilesDict("probe/online_avatar", _B)
    SM._download_semaphore.release()
    SM._download_semaphore.release()
    SM.JoinDownloads()

    assert _A not in requested
    assert requested == [_B]
    assert exported("probe", "online_avatar.png").read_bytes() == b"b"
    assert _parts(tmp_path) == []
    assert "./out/probe/online_avatar" not in SM._download_latest
    # Both downloads gave their slot back (the two released above): exactly
    # two can be taken again, a third cannot.
    assert SM._download_semaphore.acquire(blocking=False)
    assert SM._download_semaphore.acquire(blocking=False)
    assert not SM._download_semaphore.acquire(blocking=False)


class _Chunked(_Recording):
    """Hands the body out two bytes at a time, so it takes several reads."""

    def read(self, size=-1):
        return super().read(2)


def test_a_download_superseded_mid_body_stops_reading(
        exported, monkeypatch, tmp_path, logged):
    reads = []

    def supersede():
        reads.append(True)
        if len(reads) == 1:
            SM.CreateFilesDict("probe/online_avatar", "plain text")

    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=200,
                               raw=_Chunked(b"abcdefgh", supersede))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png")
    SM.JoinDownloads()

    assert len(reads) == 1
    assert not exported("probe", "online_avatar.png").exists()
    assert exported("probe", "online_avatar.txt").read_text(
        encoding="utf-8") == "plain text"
    assert _parts(tmp_path) == []
    assert "./out/probe/online_avatar" not in SM._download_latest
    # Dropped for being superseded, not for being over the cap.
    assert [r for r in logged if r["level"].name == "WARNING"] == []


@pytest.fixture
def logged():
    from loguru import logger
    lines = []
    sink = logger.add(lambda message: lines.append(message.record),
                      level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def _oldAvatar(exported):
    """The previous entrant's face, as a failed download must not leave it."""
    for name in ("online_avatar.png", "online_avatar.jpg"):
        target = exported("probe", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old")


def test_a_404_leaves_no_image_and_no_traceback(
        exported, monkeypatch, logged, tmp_path):
    _oldAvatar(exported)
    closed = []

    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=404, raw=_Raw(b"not found"),
                               close=lambda: closed.append(url))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    url = f"{_AVATAR}.jpg?ehk=secret"
    SM.CreateFilesDict("probe/online_avatar", url)
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert not exported("probe", "online_avatar.jpg").exists()
    assert _parts(tmp_path) == []
    assert closed == [url]
    assert [r for r in logged if r["level"].name == "ERROR"] == []
    warnings = [r["message"] for r in logged
                if r["level"].name == "WARNING"]
    assert any("404" in message for message in warnings)
    # The state key, not the URL (it carries start.gg's cache key).
    assert not any("ehk" in message or "images.start.gg" in message
                   for message in warnings)


class _Broken(io.BytesIO):
    decode_content = False

    def read(self, *args):
        if self.tell():
            raise ConnectionError("the connection dropped")
        return super().read(4)


def test_a_body_that_breaks_off_leaves_no_image(
        exported, monkeypatch, tmp_path):
    _oldAvatar(exported)

    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=200,
                               raw=_Broken(_imageBytes("PNG")))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png")
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert _parts(tmp_path) == []


def test_a_failed_request_logs_one_warning_without_the_url(
        exported, monkeypatch, logged):
    _oldAvatar(exported)

    def fake_get(url, **kwargs):
        raise ConnectionError(
            "HTTPSConnectionPool(host='images.start.gg'): Max retries "
            "exceeded with url: /secret-id.png?k=v")

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.png")
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert [r for r in logged if r["level"].name == "ERROR"] == []
    warnings = [r["message"] for r in logged
                if r["level"].name == "WARNING"]
    assert any("probe/online_avatar" in m and "ConnectionError" in m
               for m in warnings)
    assert not any("secret-id" in m or "k=v" in m for m in warnings)


def test_a_failed_local_copy_logs_no_absolute_path(
        exported, served, monkeypatch, logged, tmp_path):
    _file(f"{AVATAR_DIR}/a.png", b"a")

    def broken_copy(src, dst, **kwargs):
        raise PermissionError(13, "denied", str(src))

    monkeypatch.setattr(state_manager.shutil, "copyfile", broken_copy)

    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/a.png")

    assert [r for r in logged if r["level"].name == "ERROR"] == []
    warnings = [r["message"] for r in logged
                if r["level"].name == "WARNING"]
    assert any("probe/asset" in m and "PermissionError" in m
               for m in warnings)
    assert not any(str(tmp_path) in m for m in warnings)


@pytest.mark.parametrize("extension", ["png", "jpg"])
def test_a_body_over_the_cap_leaves_no_image(
        exported, monkeypatch, tmp_path, extension):
    _oldAvatar(exported)
    monkeypatch.setattr(state_manager, "_DOWNLOAD_MAX_BYTES", 16)

    def fake_get(url, **kwargs):
        fmt = "JPEG" if extension == "jpg" else "PNG"
        return SimpleNamespace(status_code=200, raw=_Raw(_imageBytes(fmt)))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.{extension}")
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert _parts(tmp_path) == []


def test_a_jpg_over_the_pixel_cap_is_not_converted(
        exported, monkeypatch, tmp_path):
    _oldAvatar(exported)
    monkeypatch.setattr(state_manager, "MAX_PIXELS", 11)

    def fake_get(url, **kwargs):   # 4 x 3 = 12 pixels
        return SimpleNamespace(status_code=200, raw=_Raw(_imageBytes("JPEG")))

    monkeypatch.setattr(state_manager, "requests",
                        SimpleNamespace(get=fake_get))

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.jpg")
    SM.JoinDownloads()

    assert not exported("probe", "online_avatar.png").exists()
    assert not exported("probe", "online_avatar.jpg").exists()
    assert _parts(tmp_path) == []


def test_a_jpg_download_leaves_the_png_alone(exported, served, tmp_path):
    _oldAvatar(exported)

    SM.CreateFilesDict("probe/online_avatar", f"{_AVATAR}.jpg?ehk=abc")
    SM.JoinDownloads()

    with Image.open(exported("probe", "online_avatar.png")) as image:
        assert (image.width, image.height) == (4, 3)
    assert not exported("probe", "online_avatar.jpg").exists()
    assert _parts(tmp_path) == []


# ── a local copy: swapped in, never missing ──────────────────────────────

def test_the_copy_keeps_the_one_before_until_it_is_complete(
        exported, monkeypatch, tmp_path):
    _file(f"{AVATAR_DIR}/a.png", b"a")
    _file(f"{AVATAR_DIR}/b.png", b"b")
    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/a.png")
    target = exported("probe", "asset.png")
    seen = []
    copyfile = state_manager.shutil.copyfile

    def watched(source, destination, *args, **kwargs):
        seen.append((target.is_file(), target.read_bytes()))
        result = copyfile(source, destination, *args, **kwargs)
        seen.append((target.is_file(), target.read_bytes()))
        return result

    monkeypatch.setattr(state_manager.shutil, "copyfile", watched)

    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/b.png")

    assert seen == [(True, b"a"), (True, b"a")]
    assert target.read_bytes() == b"b"
    assert _parts(tmp_path) == []


def test_a_copy_that_fails_leaves_no_part(exported, monkeypatch, tmp_path):
    _file(f"{AVATAR_DIR}/a.png", b"a")
    _file(f"{AVATAR_DIR}/b.png", b"b")
    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/a.png")
    copyfile = state_manager.shutil.copyfile

    def broken(source, destination, *args, **kwargs):
        copyfile(source, destination, *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(state_manager.shutil, "copyfile", broken)

    SM.CreateFilesDict("probe/asset", f"{AVATAR_DIR}/b.png")

    assert exported("probe", "asset.png").read_bytes() == b"a"
    assert _parts(tmp_path) == []
