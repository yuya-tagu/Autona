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
