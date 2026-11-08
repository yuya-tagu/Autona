"""The tournament's icon and banner (core/TournamentMediaService.py, ADR-005
S4f).

Until S4f all of this was the EVENT panel's (AutonaTournamentInfoWidget):
the files were written with QPixmap (the banner as it came, unscaled) and
shutil, the fetch was a job the panel ran with ``requests``, and the
thumbnail / result previews were told by the panel -- so nothing happened
without the panel, and nothing headless. Pinned here:

- the import: checked, scaled down (the icon to 512, the banner to 1920 --
  owner's decision 4), turned upright by its EXIF orientation, saved as PNG
  through a hidden temporary file and ``os.replace`` (the helper shared
  with the stream org assets; what a crash left of those is removed at
  construction), and each file's mtime published as its version
  (``logoVersion`` as before, ``bannerVersion`` new);
- the reset;
- the fetch: a job of the task executor whose run is
  ``tournamentInfo.media``; a second one for the same tournament refused
  while one runs (BUSY), one for another tournament run after it (QUEUED --
  a tournament set while a fetch runs, S4f review M1), a forced reload of
  the same tournament not dropping it (L11); its failures -- a lookup
  start.gg did not answer told from a tournament with no media, with the
  real provider (L1) --, the shutdown and a tournament replaced meanwhile;
- the launch seed of the new keys.

Each test runs a service of its own in a directory of its own; the state
and the settings are handed back untouched.
"""
import os
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.core import ImageImport
from src.core import StateSchema as schema
from src.core import TournamentMediaService as media
from src.core.DictHelper import deep_clone
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TaskExecutor import TaskExecutor
from src.core.TournamentMediaService import TournamentMediaService

WAIT = 30

RED = (220, 20, 20)
BLUE = (20, 40, 200)
ICON_PATH, LOGO_VERSION, _ = media.MEDIA[media.ICON]
BANNER_PATH, BANNER_VERSION, _ = media.MEDIA[media.BANNER]


def _image_bytes(size, color=RED, fmt="PNG", mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, fmt)
    return buffer.getvalue()


# ── harness ──────────────────────────────────────────────────────────────

@pytest.fixture
def env(autonaApp, tmp_path, monkeypatch):
    os.chdir(tmp_path)
    for directory in ("out", "user_data", "layout", "assets/icons"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    # Autona's own icon, which the reset puts back.
    (tmp_path / media.DEFAULT_ICON_PATH).write_bytes(_image_bytes((64, 64),
                                                                  BLUE))
    if SM._export_thread is not None:
        SM._export_thread.join()
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(schema.TOURNAMENT_INFO_ROOT, None)
        SM.lastSavedState.pop(schema.TOURNAMENT_INFO_ROOT, None)
    settings = SettingsManager.settings
    SettingsManager.settings = {}

    loaded = {"provider": None}
    service = TournamentMediaService(currentProvider=lambda: loaded["provider"])
    monkeypatch.setattr(TournamentMediaService, "instance", service)
    try:
        yield SimpleNamespace(service=service, loaded=loaded, dir=tmp_path)
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved
            SM.revision = revision
            SM.changedKeys = changedKeys
        SettingsManager.settings = settings
        os.chdir(autonaApp.dir)


@pytest.fixture
def executor(env, monkeypatch):
    """A stepped executor of the test's own: a job runs when the test says
    (``run_next`` / ``drain``)."""
    assert TaskExecutor.instance.drain(WAIT)
    made = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", made)
    try:
        yield made
    finally:
        made.shutdown(WAIT)


def _media():
    return SM.Get(media.MEDIA_PATH)


# ── importing ────────────────────────────────────────────────────────────

def test_an_icon_is_scaled_to_512_saved_as_png_and_its_version_published(
        env):
    assert env.service.ImportMedia("icon", _image_bytes((2048, 1024))) == (
        media.IMPORTED)
    with Image.open(ICON_PATH) as saved:
        assert saved.format == "PNG" and saved.size == (512, 256)
    # The version is the file's mtime -- what logoVersion always was.
    assert SM.Get(LOGO_VERSION) == int(os.path.getmtime(ICON_PATH) * 1000)


def test_a_banner_is_scaled_to_1920_and_keeps_its_transparency(env):
    """Owner's decision 4 (S4f): it used to be saved as it came."""
    data = _image_bytes((3840, 2160), (10, 20, 30, 128), mode="RGBA")
    assert env.service.ImportMedia("banner", data) == media.IMPORTED
    with Image.open(BANNER_PATH) as saved:
        assert saved.format == "PNG" and saved.size == (1920, 1080)
        assert saved.mode == "RGBA"
    assert SM.Get(BANNER_VERSION) == int(os.path.getmtime(BANNER_PATH) * 1000)


def test_a_small_image_is_never_enlarged_and_a_jpeg_becomes_png(env):
    assert env.service.ImportMedia(
        "banner", _image_bytes((320, 180), fmt="JPEG")) == media.IMPORTED
    with Image.open(BANNER_PATH) as saved:
        assert saved.format == "PNG" and saved.size == (320, 180)


@pytest.mark.parametrize("kind, data", [
    ("icon", _image_bytes((10, 10), fmt="GIF")),
    ("icon", b"not an image"),
    ("icon", b""),
    ("icon", None),
    ("icon", _image_bytes((400, 400))[:200]),   # truncated
    ("logo", _image_bytes((10, 10))),           # not a kind
])
def test_anything_else_is_refused_and_changes_nothing(env, kind, data):
    assert env.service.ImportMedia(kind, data) == media.INVALID_IMAGE
    assert not os.path.exists(ICON_PATH)
    assert SM.Get(LOGO_VERSION, None) is None


def test_a_phone_photo_is_turned_upright(env):
    """A phone saves a portrait photo landscape with an EXIF orientation
    (6: turn it 90 degrees clockwise to view it); the import applies it, for
    the stream org assets too (the shared helper; S4f review L9)."""
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = BytesIO()
    Image.new("RGB", (400, 200), RED).save(buffer, "JPEG",
                                           exif=exif.tobytes())
    assert env.service.ImportMedia("banner", buffer.getvalue()) == (
        media.IMPORTED)
    with Image.open(BANNER_PATH) as saved:
        assert saved.size == (200, 400)

    # Scaled first, turned after: the longest side is the cap either way.
    exif_big = BytesIO()
    Image.new("RGB", (4000, 2000), RED).save(exif_big, "JPEG",
                                             exif=exif.tobytes())
    assert env.service.ImportMedia("icon", exif_big.getvalue()) == (
        media.IMPORTED)
    with Image.open(ICON_PATH) as saved:
        assert saved.size == (256, 512)


def test_the_temporary_file_is_hidden_and_leftovers_go_at_construction(
        env, monkeypatch):
    """Written through ``.<name>.<random>.tmp`` beside the target (S4f
    review L8); a crash between the write and the replace leaves one, which
    the next service removes -- and the visible ones S4d named."""
    real_replace = os.replace
    sources = []

    def replace(source, target):
        sources.append(Path(source).name)
        real_replace(source, target)
    monkeypatch.setattr(os, "replace", replace)
    assert env.service.ImportMedia("icon", _image_bytes((64, 64))) == (
        media.IMPORTED)
    assert sources and sources[0].startswith(".logo.")
    assert sources[0].endswith(".tmp")

    layout, user_data = env.dir / "layout", env.dir / "user_data"
    leftovers = [layout / ".logo.k3j4h5.tmp", layout / "logo.a1b2c3.tmp",
                 user_data / ".tournament_banner.x9y8z7.tmp"]
    kept = [layout / "other.tmp", layout / "logo.png",
            user_data / ".stream_org.tmp"]
    for path in leftovers + kept[:1] + kept[2:]:
        path.write_bytes(b"left")
    TournamentMediaService(currentProvider=lambda: None)
    assert [path for path in leftovers if path.exists()] == []
    assert all(path.exists() for path in kept)


def test_the_pixel_cap_is_the_shared_one(env, monkeypatch):
    monkeypatch.setattr(ImageImport, "MAX_PIXELS", 200 * 100)
    assert env.service.ImportMedia(
        "banner", _image_bytes((201, 100))) == media.INVALID_IMAGE
    assert env.service.ImportMedia(
        "banner", _image_bytes((200, 100))) == media.IMPORTED


def test_a_target_held_open_is_retried_then_reported(env, monkeypatch):
    """Written through a sibling temporary file and os.replace, retried
    while a reader (an overlay, a preview) holds the target open; a file
    that cannot be replaced leaves the old one, its version and no
    temporary file behind."""
    real_replace = os.replace
    failures = {"left": 2}

    def replace(source, target):
        if Path(target).name == "tournament_banner.png" and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(32, "in use")
        real_replace(source, target)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(ImageImport, "_REPLACE_BACKOFF", 0)
    assert env.service.ImportMedia(
        "banner", _image_bytes((64, 64))) == media.IMPORTED

    failures["left"] = ImageImport._REPLACE_ATTEMPTS
    version = SM.Get(BANNER_VERSION)
    assert env.service.ImportMedia(
        "banner", _image_bytes((32, 32), BLUE)) == media.SAVE_FAILED
    assert SM.Get(BANNER_VERSION) == version
    with Image.open(BANNER_PATH) as kept:
        assert kept.size == (64, 64)
    assert not list((env.dir / "user_data").glob("*.tmp"))


# ── resetting ────────────────────────────────────────────────────────────

def test_the_reset_puts_autonas_icon_back_and_removes_the_banner(env):
    env.service.ImportMedia("icon", _image_bytes((600, 600)))
    env.service.ImportMedia("banner", _image_bytes((640, 360)))
    revision = SM.revision
    assert env.service.ResetMedia() is True
    # Both versions in one publish.
    assert SM.revision == revision + 1
    assert Path(ICON_PATH).read_bytes() == (
        env.dir / media.DEFAULT_ICON_PATH).read_bytes()
    assert SM.Get(LOGO_VERSION) == int(os.path.getmtime(ICON_PATH) * 1000)
    assert not os.path.exists(BANNER_PATH)
    assert SM.Get(BANNER_VERSION) == 0


def test_one_medium_can_be_reset_alone(env):
    env.service.ImportMedia("icon", _image_bytes((600, 600)))
    env.service.ImportMedia("banner", _image_bytes((640, 360)))
    icon = Path(ICON_PATH).read_bytes()
    assert env.service.ResetMedia("banner") is True
    assert not os.path.exists(BANNER_PATH)
    assert Path(ICON_PATH).read_bytes() == icon
    assert env.service.ResetMedia("logo") is False


def test_a_reset_without_autonas_icon_reports_it(env):
    (env.dir / media.DEFAULT_ICON_PATH).unlink()
    assert env.service.ResetMedia() is False
    assert not os.path.exists(BANNER_PATH)


# ── fetching ─────────────────────────────────────────────────────────────

class _Provider:
    """Shaped like StartGGDataProvider for the fetch: strict lookups (None:
    the tournament has none; an exception: the lookup failed) and the
    tournament's URL (None here unless given: the provider itself is then
    the tournament)."""

    def __init__(self, icon="https://images.example/icon.png",
                 banner="https://images.example/banner.png", url=None):
        self.icon = icon
        self.banner = banner
        self.url = url
        self.threads = []
        self.strict = []

    def GetIconURL(self, strict=False):
        self.threads.append(threading.current_thread())
        self.strict.append(strict)
        if isinstance(self.icon, Exception):
            raise self.icon
        return self.icon

    def GetBannerURL(self, strict=False):
        self.strict.append(strict)
        if isinstance(self.banner, Exception):
            raise self.banner
        return self.banner


def _serve(monkeypatch, images, asked=None, before=None):
    """requests.get answering `images` (url -> bytes, or an exception)."""
    def get(url, headers, timeout):
        if asked is not None:
            asked.append((url, timeout, headers.get("User-Agent")))
        if before is not None:
            before(url)
        answer = images[url]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=answer, raise_for_status=lambda: None)
    monkeypatch.setattr(media.requests, "get", get)


def test_no_tournament_starts_nothing_and_publishes_nothing(env, executor):
    assert env.service.FetchMedia() == media.NO_TOURNAMENT
    assert executor.queued() == 0
    assert _media() is None


def test_the_tournaments_icon_and_banner_are_fetched_as_a_job(
        env, executor, monkeypatch):
    provider = env.loaded["provider"] = _Provider()
    asked = []
    _serve(monkeypatch, {
        provider.icon: _image_bytes((1024, 1024)),
        provider.banner: _image_bytes((3000, 1000)),
    }, asked)

    assert env.service.FetchMedia() == media.STARTED
    # Running before FetchMedia returns, nothing looked up on this thread.
    assert _media() == {"status": media.RUNNING, "error": "", "revision": 1}
    assert env.service.FetchRunning() is True
    assert provider.threads == []
    assert executor.run_next(WAIT) is True

    assert _media() == {"status": media.DONE, "error": "", "revision": 1}
    assert env.service.FetchRunning() is False
    assert provider.threads and provider.threads[0] is not (
        threading.current_thread())
    assert [(url, timeout) for url, timeout, _ in asked] == [
        (provider.icon, 20), (provider.banner, 20)]
    assert all(agent == ImageImport.DOWNLOAD_HEADERS["User-Agent"]
               for _url, _timeout, agent in asked)
    # A failed lookup must raise, not answer "none" (S4f review L1).
    assert provider.strict == [True, True]
    with Image.open(ICON_PATH) as icon:
        assert icon.size == (512, 512)
    with Image.open(BANNER_PATH) as banner:
        assert banner.size == (1920, 640)
    assert SM.Get(LOGO_VERSION) > 0 and SM.Get(BANNER_VERSION) > 0


def test_a_second_fetch_is_refused_while_one_runs(env, executor,
                                                  monkeypatch):
    provider = env.loaded["provider"] = _Provider(banner=None)
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64))})
    assert env.service.FetchMedia() == media.STARTED
    assert env.service.FetchMedia() == media.BUSY
    assert executor.queued() == 1
    assert executor.run_next(WAIT) is True
    # A tournament with an icon and no banner: done.
    assert _media() == {"status": media.DONE, "error": "", "revision": 1}
    assert env.service.FetchMedia() == media.STARTED
    assert executor.drain(WAIT)
    assert _media()["revision"] == 2


@pytest.mark.parametrize("icon, banner, served, error", [
    (None, None, {}, media.NO_MEDIA),
    ("https://i/icon.png", None, {"https://i/icon.png": OSError("offline")},
     media.FETCH_FAILED),
    (RuntimeError("start.gg is down"), None, {}, media.FETCH_FAILED),
    ("https://i/icon.png", None, {"https://i/icon.png": b"<html>"},
     media.FETCH_FAILED),
    # The icon came; the banner did not: the run says so, the icon stays.
    ("https://i/icon.png", "https://i/banner.png",
     {"https://i/icon.png": _image_bytes((64, 64)),
      "https://i/banner.png": OSError("offline")}, media.FETCH_FAILED),
])
def test_a_failed_fetch_is_an_error_that_lets_the_next_one_start(
        env, executor, monkeypatch, icon, banner, served, error):
    env.loaded["provider"] = _Provider(icon=icon, banner=banner)
    _serve(monkeypatch, served)
    assert env.service.FetchMedia() == media.STARTED
    assert executor.drain(WAIT)
    assert _media() == {"status": media.ERROR, "error": error,
                        "revision": 1}
    assert env.service.FetchRunning() is False
    assert os.path.exists(ICON_PATH) == (error == media.FETCH_FAILED
                                         and banner is not None)


def test_a_file_that_cannot_be_written_is_a_save_failure(env, executor,
                                                         monkeypatch):
    provider = env.loaded["provider"] = _Provider(banner=None)
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64))})

    def full(path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ImageImport, "WriteFile", full)
    assert env.service.FetchMedia() == media.STARTED
    assert executor.drain(WAIT)
    assert _media()["error"] == media.SAVE_FAILED


def test_a_tournament_replaced_meanwhile_drops_what_was_downloaded(
        env, executor, monkeypatch):
    provider = env.loaded["provider"] = _Provider()

    def replaced(url):
        env.loaded["provider"] = _Provider()   # another tournament loaded
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64)),
                         provider.banner: _image_bytes((64, 64))},
           before=replaced)
    assert env.service.FetchMedia() == media.STARTED
    assert executor.drain(WAIT)
    assert _media()["error"] == media.SUPERSEDED
    assert not os.path.exists(ICON_PATH)
    assert not os.path.exists(BANNER_PATH)


def test_a_tournament_set_while_a_fetch_runs_is_fetched_after_it(
        env, executor, monkeypatch):
    """S4f review M1: the panel's download ran for tournament A while an
    admin URL resolved; B loaded then, and the set's fetchMedia answered
    BUSY and was dropped -- A's run was superseded and B's media never
    came. Now the set's fetch is queued, and the same job runs again for B
    once A's run ends (superseded), under a new revision."""
    from src.core.TournamentService import TournamentService

    a = _Provider(icon="https://i/a.png", banner=None,
                  url="https://www.start.gg/tournament/a/event/s")
    b = _Provider(icon="https://i/b.png", banner=None,
                  url="https://www.start.gg/tournament/b/event/s")
    providers = {a.url: a, b.url: b}
    env.loaded["provider"] = a
    _serve(monkeypatch, {a.icon: _image_bytes((64, 64), RED),
                         b.icon: _image_bytes((32, 32), BLUE)})

    class Adapter:
        def LoadTournament(self, url, initialLoading=False, force=False):
            env.loaded["provider"] = providers[url]

        def AnnounceUrl(self, url):
            pass

    tournaments = TournamentService(provider=Adapter(), onMain=lambda fn: fn())
    assert env.service.FetchMedia() == media.STARTED   # the panel's, for A
    assert tournaments.SetTournament(b.url, fetchMedia=True) == "started"
    # Asked again for B: still the one queued run.
    assert env.service.FetchMedia() == media.QUEUED
    assert executor.queued() == 1                      # one job does both

    assert executor.run_next(WAIT) is True
    assert _media() == {"status": media.DONE, "error": "", "revision": 2}
    assert env.service.FetchRunning() is False
    with Image.open(ICON_PATH) as icon:
        assert icon.size == (32, 32)   # B's; A's was dropped


def test_a_queued_fetch_for_a_tournament_cleared_meanwhile_runs_nothing(
        env, executor, monkeypatch):
    a = _Provider(banner=None, url="https://www.start.gg/tournament/a/event/s")
    b = _Provider(banner=None, url="https://www.start.gg/tournament/b/event/s")
    env.loaded["provider"] = a
    _serve(monkeypatch, {a.icon: _image_bytes((64, 64))})
    assert env.service.FetchMedia() == media.STARTED
    env.loaded["provider"] = b
    assert env.service.FetchMedia() == media.QUEUED
    env.loaded["provider"] = None                  # then cleared
    assert executor.run_next(WAIT) is True
    assert _media() == {"status": media.ERROR, "error": media.SUPERSEDED,
                        "revision": 1}
    assert b.threads == []
    assert env.service.FetchRunning() is False


def test_a_forced_reload_of_the_same_tournament_keeps_the_running_fetch(
        env, executor, monkeypatch):
    """S4f review L11: re-setting the loaded URL (force) rebuilds the
    provider; a fetch running for it used to be superseded by that. The
    tournament is told by its URL now."""
    url = "https://www.start.gg/tournament/a/event/s"
    provider = env.loaded["provider"] = _Provider(url=url)

    def rebuilt(_url):
        env.loaded["provider"] = _Provider(url=url)
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64)),
                         provider.banner: _image_bytes((64, 64))},
           before=rebuilt)
    assert env.service.FetchMedia() == media.STARTED
    env.loaded["provider"] = _Provider(url=url)
    assert env.service.FetchMedia() == media.BUSY  # the same tournament
    assert executor.drain(WAIT)
    assert _media() == {"status": media.DONE, "error": "", "revision": 1}
    assert os.path.exists(ICON_PATH) and os.path.exists(BANNER_PATH)


@pytest.mark.parametrize("answer, error", [
    ({}, media.FETCH_FAILED),                        # retries spent
    ({"errors": [{"message": "x"}], "data": {"event": None}},
     media.FETCH_FAILED),                            # no such event
    ({"data": {"event": {"tournament": {"images": []}}}}, media.NO_MEDIA),
])
def test_the_real_provider_tells_a_failed_lookup_from_no_media(
        env, executor, monkeypatch, answer, error):
    """S4f review L1: StartGGDataProvider's lookups swallowed a failure
    (QueryRequests answers {} once its retries are spent) and answered
    None, so an offline PC read as "the tournament has no media". Strict,
    they raise; the stream thumbnail's banner lookup (not strict) still
    gets None."""
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)

    provider = StartGGDataProvider(
        "https://www.start.gg/tournament/a/event/s", None)
    monkeypatch.setattr(provider, "QueryRequests",
                        lambda *args, **kwargs: answer)
    env.loaded["provider"] = provider
    assert env.service.FetchMedia() == media.STARTED
    assert executor.drain(WAIT)
    assert _media() == {"status": media.ERROR, "error": error,
                        "revision": 1}
    assert provider.GetBannerURL() is None


def test_a_job_the_shutdown_cancels_does_not_stay_running(env, executor):
    env.loaded["provider"] = _Provider()
    assert env.service.FetchMedia() == media.STARTED
    assert executor.queued() == 1
    assert executor.shutdown(WAIT)
    assert _media() == {"status": media.ERROR, "error": media.CANCELLED,
                        "revision": 1}
    assert env.service.FetchRunning() is False
    # Submitted after the shutdown: cancelled at once, not left running.
    assert env.service.FetchMedia() == media.STARTED
    assert _media() == {"status": media.ERROR, "error": media.CANCELLED,
                        "revision": 2}
    assert env.service.FetchRunning() is False


def test_a_job_told_to_stop_during_the_download_writes_nothing(
        env, executor, monkeypatch):
    provider = env.loaded["provider"] = _Provider()
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64)),
                         provider.banner: _image_bytes((64, 64))},
           before=lambda url: executor.stopping.set())
    assert env.service.FetchMedia() == media.STARTED
    assert executor.run_next(WAIT) is True
    assert _media()["error"] == media.CANCELLED
    assert not os.path.exists(ICON_PATH)
    assert env.service.FetchRunning() is False


# ── the launch seed ──────────────────────────────────────────────────────

def test_the_banner_version_and_an_idle_fetch_are_seeded_at_launch(env):
    """A fetch does not outlive the process (a persisted "running" would
    never end), and the banner's version is current from launch, like the
    logo's and the stream org assets'."""
    Path(BANNER_PATH).write_bytes(_image_bytes((64, 64)))
    SM.Set(media.MEDIA_PATH, {"status": "running", "error": "",
                              "revision": 4})
    with SM.SaveBlock():
        schema.SeedPanelState()
    assert _media() == schema.DefaultTournamentMedia()
    assert SM.Get(BANNER_VERSION) == int(os.path.getmtime(BANNER_PATH) * 1000)


def test_the_new_keys_are_catalogued():
    keys = schema.KEY_CATALOG[schema.TOURNAMENT_INFO_ROOT]
    for key in ("startAtDate", "bannerVersion", "media.status",
                "media.error", "media.revision", "logoVersion"):
        assert key in keys
