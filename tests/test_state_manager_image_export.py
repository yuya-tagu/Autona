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
"""
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import src.StateManager as state_manager
from src.StateManager import StateManager as SM

_ORIGINAL_CWD = os.getcwd()


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
    url = "https://cdn.example.invalid/user/image-9f3.jpg?ehk=abc"

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
    SM.CreateFilesDict(
        "probe/online_avatar",
        "https://cdn.example.invalid/user/image-9f3.png?ehk=abc")
    SM.JoinDownloads()

    assert exported("probe", "online_avatar.png").is_file()
    assert not exported("probe", "online_avatar.txt").exists()


def test_a_plain_image_url_still_downloads(exported, served):
    # Characterization: the case that always worked must keep working.
    SM.CreateFilesDict("probe/online_avatar",
                       "https://cdn.example.invalid/user/plain.png")
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
    url = "https://cdn.example.invalid/redirect?to=avatar.png"

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
    url = "https://cdn.example.invalid/user/image-9f3.jpg?ehk=abc"
    SM.CreateFilesDict("probe/online_avatar", url)
    SM.JoinDownloads()
    assert exported("probe", "online_avatar.png").is_file()

    SM.RemoveFilesDict("probe/online_avatar", url)

    assert not exported("probe", "online_avatar.png").exists()
