"""The 配信設定 operations (core/StreamService.py, ADR-005 S4d).

Until S4d all of this was the 配信設定 panel's (AutonaStreamSettingsWidget):
the stream title was generated only once the page had been opened (never
headless), the thumbnail was made on the GUI thread with a blocking banner
download, and its options were forgotten with the page. Pinned here:

- the title: the truncation to 100 characters, the form without seeds,
  the rule that keeps the operator's own title, and that it follows the
  tournament with no panel -- written from the event loop, never from
  inside the publish that told the service;
- the channels (settings.json ``stream_channels``) and the destination
  following an edited / deleted channel;
- the org assets: scaled down, saved as PNG, their versions published, and
  the reset;
- the thumbnail generator: every option, the banner's fallbacks, a second
  run refused while one runs, the options saved;
- that the panel keeps none of it (AST), and draws what core saved.

Each test runs a service of its own (its `later` queue in the test's hands)
in a directory of its own, the session's service stopped meanwhile; the
state and the settings are handed back untouched.
"""
import ast
import os
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageFile
from qtpy.QtWidgets import QFileDialog, QMessageBox

from src.core import StateSchema as schema
from src.core import StreamService as stream
from src.core.Actions import AutonaActions
from src.core.DictHelper import deep_clone
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.StreamService import StreamService
from src.core.TaskExecutor import TaskExecutor
from src.Helpers.AutonaOutputRetention import PruneOutputFiles

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WAIT = 30

BLUE = (20, 40, 200)
GREEN = (30, 180, 60)
ORANGE = (240, 140, 20)
RED = (220, 20, 20)
PURPLE = (120, 30, 150)


def _image_bytes(size, color, fmt="PNG", mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, fmt)
    return buffer.getvalue()


def _save_image(path, size, color, mode="RGB"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)


def _near(pixel, color, tolerance=12):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, color))


# ── harness ──────────────────────────────────────────────────────────────

class _Env(SimpleNamespace):
    def run_queued(self):
        """Run what the service handed to its `later` (the event loop)."""
        ran = 0
        while self.queued:
            self.queued.pop(0)()
            ran += 1
        return ran

    def title(self):
        return SM.Get(stream.TITLE_PATH, "")

    def thumbnail(self):
        return SM.Get(stream.THUMBNAIL_PATH)


@pytest.fixture
def env(autonaApp, tmp_path, monkeypatch):
    harness = StreamService.instance
    harness.Stop()
    os.chdir(tmp_path)
    for directory in ("out", "user_data", "layout"):
        (tmp_path / directory).mkdir(exist_ok=True)
    if SM._export_thread is not None:
        SM._export_thread.join()
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        for root in (schema.STREAM_ROOT, schema.TOURNAMENT_INFO_ROOT,
                     schema.GAME_ROOT):
            SM.state.pop(root, None)
            SM.lastSavedState.pop(root, None)
    settings = SettingsManager.settings
    SettingsManager.settings = {}

    queued = []
    banner = {"url": None}
    service = StreamService(
        later=queued.append, bannerUrl=lambda: banner["url"],
        prune=PruneOutputFiles)
    monkeypatch.setattr(StreamService, "instance", service)
    service.Start()
    try:
        yield _Env(service=service, queued=queued, banner=banner,
                   dir=tmp_path)
    finally:
        service.Stop()
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved
            SM.revision = revision
            SM.changedKeys = changedKeys
        SettingsManager.settings = settings
        os.chdir(autonaApp.dir)
        harness.Start()


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


# ── the title: the pure rules ────────────────────────────────────────────

def _entrants(*tags, seeded=True):
    return [{"seed": index + 1 if seeded else stream.NO_SEED,
             "gamerTag": tag} for index, tag in enumerate(tags)]


def test_the_title_names_the_top_entrants():
    assert stream.BuildTitle("Cup", "SSBU", _entrants("Ada", "Bob")) == (
        "Cup ft. Ada Bob ... and more! - SSBU")
    # An entrant without a tag is left out; no abbreviation, no suffix.
    assert stream.BuildTitle("Cup", None, _entrants("Ada", "", "Cy")) == (
        "Cup ft. Ada Cy ... and more!")


def test_the_title_drops_entrants_from_the_tail_until_it_fits():
    tags = [f"{letter * 20}" for letter in "ABCDEFGHIJ"]
    title = stream.BuildTitle("Cup", "SSBU", _entrants(*tags))
    # 3 + 5 + 20k + (k - 1) + 14 + 7 <= 100: three tags fit, four do not.
    assert title == ("Cup ft. " + " ".join(tags[:3]) + " ... and more!"
                     " - SSBU")
    assert len(title) <= stream.STREAM_TITLE_MAX_LENGTH
    # Hundreds of entrants make the same title.
    many = tags + [f"P{index}" for index in range(500)]
    assert stream.BuildTitle("Cup", "SSBU", _entrants(*many)) == title


def test_with_no_entrant_fitting_the_title_is_the_name_and_the_game():
    name = "N" * 95
    assert stream.BuildTitle(name, "SSBU", _entrants("Ada")) == (
        f"{name} - SSBU")


def test_without_seeds_the_title_is_the_name_and_the_game():
    unseeded = _entrants("Ada", "Bob", seeded=False)
    assert stream.BuildTitle("Cup", "SF6", unseeded) == "Cup - SF6"
    assert stream.BuildTitle("Cup", "SF6", []) == "Cup - SF6"
    assert stream.BuildTitle("Cup", None, []) == "Cup"
    assert stream.BuildTitle("", None, []) == ""


def test_the_abbreviation_falls_back_by_start_gg_id():
    assert stream.GameAbbreviation(
        {"abbreviation": "XX", "smashgg_id": 1386}) == "XX"
    assert stream.GameAbbreviation({"smashgg_id": 1386}) == "SSBU"
    assert stream.GameAbbreviation({"smashgg_id": "43868"}) == "SF6"
    assert stream.GameAbbreviation({"smashgg_id": 7}) is None
    assert stream.GameAbbreviation(None) is None


# ── the title: the service ───────────────────────────────────────────────

def test_the_title_follows_the_tournament_without_a_panel(env):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    SM.Set("game", {"abbreviation": "SF6"})
    # Nothing is written from inside the publishes that told the service;
    # the two changes queued one refresh.
    assert env.title() == ""
    assert len(env.queued) == 1
    env.run_queued()
    assert env.title() == "Cup - SF6"
    # A publish that changes neither input queues nothing.
    SM.Set("tournamentInfo.eventName", "Singles")
    assert env.queued == []


def test_a_title_still_auto_generated_follows_the_next_change(env):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    env.run_queued()
    assert env.title() == "Cup 2"


def test_the_operators_title_is_kept_until_they_clear_it(env):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    AutonaActions.instance.SetStreamTitle("My own title")
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    env.run_queued()
    assert env.title() == "My own title"
    env.service.SetTopEntrants(_entrants("Ada"))
    env.run_queued()
    assert env.title() == "My own title"
    # Cleared: the next refresh writes the generated title again.
    AutonaActions.instance.SetStreamTitle("")
    SM.Set("tournamentInfo.tournamentName", "Cup 3")
    env.run_queued()
    assert env.title() == "Cup 3 ft. Ada ... and more!"


def test_the_last_auto_title_is_published_with_the_title(env):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    assert SM.Get(stream.TITLE_AUTO_PATH) == "Cup"
    # Recorded even while the operator's own title is kept.
    AutonaActions.instance.SetStreamTitle("Mine")
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    env.run_queued()
    assert (env.title(), SM.Get(stream.TITLE_AUTO_PATH)) == ("Mine", "Cup 2")


def test_a_typed_title_survives_a_restart(env):
    """stream.titleAuto is persisted state: a restart (a new service over
    the restored state) still tells the typed title from an auto one."""
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    AutonaActions.instance.SetStreamTitle("Mine")
    env.service.Stop()

    restarted = StreamService(later=env.queued.append)
    restarted.Start()
    try:
        SM.Set("tournamentInfo.tournamentName", "Cup 2")
        env.run_queued()
        assert env.title() == "Mine"
    finally:
        restarted.Stop()


def test_an_auto_title_restored_at_start_follows_the_tournament(env):
    SM.Set("tournamentInfo.tournamentName", "Yesterday")
    env.run_queued()
    env.service.Stop()
    restarted = StreamService(later=env.queued.append)
    restarted.Start()
    try:
        SM.Set("tournamentInfo.tournamentName", "Today")
        env.run_queued()
        assert env.title() == "Today"
    finally:
        restarted.Stop()


def test_a_state_from_before_titleauto_counts_its_title_as_auto(env):
    """No stream.titleAuto (a state saved before S4d): the restored title is
    regenerated, as the panel did whenever it was opened."""
    env.service.Stop()
    SM.Set("tournamentInfo.tournamentName", "Today")
    SM.Set(stream.TITLE_PATH, "Yesterday's title")
    assert SM.Get(stream.TITLE_AUTO_PATH) is None
    env.service.Start()
    env.service.RefreshTitle()
    assert env.title() == "Today"
    assert SM.Get(stream.TITLE_AUTO_PATH) == "Today"


def test_whether_a_title_is_auto():
    assert stream.TitleIsAuto("Cup", "Cup") is True
    assert stream.TitleIsAuto("", "Cup") is True
    assert stream.TitleIsAuto(None, "") is True
    assert stream.TitleIsAuto("Mine", "Cup") is False
    assert stream.TitleIsAuto("Mine", "") is False
    assert stream.TitleIsAuto("Anything", None) is True


@pytest.mark.parametrize("typed, shown, title, auto, wins", [
    # Typed over the title it started from (auto or not).
    ("Mine", "Cup", "Cup", "Cup", True),
    ("Mine 2", "Mine", "Mine", "Cup", True),
    # An auto title landed while the operator typed: the typing wins.
    ("Mine", "Cup", "Cup ft. Ada", "Cup ft. Ada", True),
    ("Mine", "", "Cup", "Cup", True),
    # Another writer's own title landed meanwhile: that one wins.
    ("Mine", "Cup", "Web's", "Cup", False),
    # Nothing typed: the field merely kept an old value -- never written
    # back over the newer one.
    ("Cup", "Cup", "Cup ft. Ada", "Cup ft. Ada", False),
    # Already the state's.
    ("Cup", "Old", "Cup", "Cup", False),
    # Cleared by hand: written (core makes the next auto title).
    ("", "Mine", "Mine", "Cup", True),
])
def test_whether_a_typed_title_is_written(typed, shown, title, auto, wins):
    assert stream.TypedTitleWins(typed, shown, title, auto) is wins


def test_the_top_entrants_come_from_the_provider(env):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    env.service.SetTopEntrants(
        [{"seed": 2, "gamerTag": "Bob"}, {"seed": 1, "gamerTag": "Ada"},
         "not an entrant", {"seed": None, "gamerTag": 5}])
    assert env.service.TopEntrants() == [
        {"seed": 2, "gamerTag": "Bob"}, {"seed": 1, "gamerTag": "Ada"},
        {"seed": stream.NO_SEED, "gamerTag": ""}]
    assert env.run_queued() == 1
    assert env.title() == "Cup ft. Bob Ada ... and more!"
    # The same list again changes nothing.
    env.service.SetTopEntrants([{"seed": 2, "gamerTag": "Bob"},
                                {"seed": 1, "gamerTag": "Ada"},
                                {"seed": None, "gamerTag": 5}])
    assert env.queued == []


def test_a_replaced_tournament_takes_its_entrants_along(env):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.service.SetTopEntrants(_entrants("Ada"))
    env.run_queued()
    assert env.title() == "Cup ft. Ada ... and more!"
    adapter = AutonaTournamentDataProvider.instance
    cancel = threading.Event()
    previous = adapter._entrantsCancel
    adapter._entrantsCancel = cancel
    try:
        adapter._CancelEntrants()
    finally:
        adapter._entrantsCancel = previous
    assert cancel.is_set()
    assert env.service.TopEntrants() == []
    env.run_queued()
    assert env.title() == "Cup"
    # The replaced tournament's fetch, ending just now, hands nothing over.
    env.service.SetTopEntrants(_entrants("Late"), cancelled=cancel)
    assert env.service.TopEntrants() == []


def test_a_refresh_that_cannot_be_queued_is_dropped_not_stuck(env):
    def broken(fn):
        raise RuntimeError("no event loop")
    env.service._later = broken
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.service._later = env.queued.append
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    assert len(env.queued) == 1
    env.run_queued()
    assert env.title() == "Cup 2"


# ── the channels ─────────────────────────────────────────────────────────

def test_channels_are_added_updated_and_deleted_in_settings(env):
    service = env.service
    assert service.AddChannel("  Main ", " https://a.example/live ") == 0
    assert service.AddChannel("Sub", "https://b.example") == 1
    assert service.AddChannel("", "https://c.example") is None
    assert service.AddChannel("No URL", "  ") is None
    assert SettingsManager.Get(stream.CHANNELS_SETTING) == [
        {"name": "Main", "url": "https://a.example/live"},
        {"name": "Sub", "url": "https://b.example"}]

    # The destination follows the channel it is.
    AutonaActions.instance.SetStreamUrl("https://a.example/live")
    assert service.UpdateChannel(0, "Main", "https://a2.example") is True
    assert SM.Get(stream.URL_PATH) == "https://a2.example"
    # ...and not one it is not.
    assert service.UpdateChannel(1, "Sub 2", "https://b2.example") is True
    assert SM.Get(stream.URL_PATH) == "https://a2.example"
    assert service.UpdateChannel(2, "X", "https://x.example") is False
    assert service.UpdateChannel(0, "", "https://x.example") is False

    assert service.DeleteChannel(1) is True
    assert SM.Get(stream.URL_PATH) == "https://a2.example"
    assert service.DeleteChannel(0) is True
    assert SM.Get(stream.URL_PATH) == ""
    assert service.DeleteChannel(0) is False
    assert SettingsManager.Get(stream.CHANNELS_SETTING) == []
    # Written to the file, in the shape it always had.
    assert (env.dir / "user_data" / "settings.json").is_file()


def test_saved_channels_of_any_shape_read_as_name_and_url(env):
    SettingsManager.settings[stream.CHANNELS_SETTING] = [
        {"name": "A", "url": "u", "extra": 1}, "x", {"name": 3}]
    # Not a dict: skipped, and not counted by the indexes.
    assert env.service.Channels() == [
        {"name": "A", "url": "u"}, {"name": "", "url": ""}]
    SettingsManager.settings[stream.CHANNELS_SETTING] = "broken"
    assert env.service.Channels() == []


def test_a_write_keeps_the_extra_keys_and_drops_what_is_not_a_channel(env):
    SettingsManager.settings[stream.CHANNELS_SETTING] = [
        {"name": "A", "url": "u", "extra": 1}, "x", {"name": "B", "url": "v",
                                                     "id": 7}]
    assert env.service.UpdateChannel(1, "B2", "v2") is True
    assert SettingsManager.Get(stream.CHANNELS_SETTING) == [
        {"name": "A", "url": "u", "extra": 1},
        {"name": "B2", "url": "v2", "id": 7}]
    assert env.service.AddChannel("C", "w") == 2
    assert env.service.DeleteChannel(0) is True
    assert SettingsManager.Get(stream.CHANNELS_SETTING) == [
        {"name": "B2", "url": "v2", "id": 7}, {"name": "C", "url": "w"}]


# ── the org assets ───────────────────────────────────────────────────────

def test_the_org_icon_is_scaled_down_saved_as_png_and_versioned(env):
    assert SM.Get("stream.orgIconVersion", 0) == 0
    data = _image_bytes((1000, 600), (*RED, 128), mode="RGBA")
    assert env.service.ImportOrgAsset("icon", data) == stream.IMPORTED
    with Image.open(schema.STREAM_ORG_ICON_PATH) as saved:
        assert saved.format == "PNG"
        assert saved.size == (512, 307)
        assert saved.mode == "RGBA"
    assert SM.Get("stream.orgIconVersion") == int(
        os.path.getmtime(schema.STREAM_ORG_ICON_PATH) * 1000)


def test_the_org_thumbnail_keeps_up_to_1920_and_is_never_enlarged(env):
    assert env.service.ImportOrgAsset(
        "thumbnail", _image_bytes((4000, 1000), BLUE, fmt="JPEG")) == (
        stream.IMPORTED)
    with Image.open(schema.STREAM_ORG_THUMB_PATH) as saved:
        assert saved.format == "PNG"
        assert saved.size == (1920, 480)
        assert saved.mode == "RGB"
    assert SM.Get("stream.orgThumbVersion") > 0
    assert env.service.ImportOrgAsset(
        "thumbnail", _image_bytes((100, 50), GREEN, fmt="BMP")) == (
        stream.IMPORTED)
    with Image.open(schema.STREAM_ORG_THUMB_PATH) as saved:
        assert saved.size == (100, 50)


def test_a_palette_image_keeps_its_transparency(env):
    image = Image.new("P", (600, 600), 0)
    image.putpalette([*RED, *BLUE] + [0] * 762)
    image.info["transparency"] = 1
    buffer = BytesIO()
    image.save(buffer, "PNG", transparency=1)
    assert env.service.ImportOrgAsset("icon", buffer.getvalue()) == (
        stream.IMPORTED)
    with Image.open(schema.STREAM_ORG_ICON_PATH) as saved:
        assert saved.mode == "RGBA" and saved.size == (512, 512)


def test_an_mpo_is_taken_as_its_first_frame(env):
    first = Image.new("RGB", (800, 400), RED)
    second = Image.new("RGB", (800, 400), BLUE)
    buffer = BytesIO()
    first.save(buffer, "MPO", save_all=True, append_images=[second])
    with Image.open(BytesIO(buffer.getvalue())) as probe:
        assert probe.format == "MPO" and probe.n_frames == 2
    assert env.service.ImportOrgAsset("icon", buffer.getvalue()) == (
        stream.IMPORTED)
    with Image.open(schema.STREAM_ORG_ICON_PATH) as saved:
        assert saved.format == "PNG" and saved.size == (512, 256)
        assert _near(saved.convert("RGB").getpixel((256, 128)), RED)


def test_an_image_over_the_pixel_cap_is_refused_before_decoding(
        env, monkeypatch):
    assert stream.ORG_ASSET_MAX_PIXELS == 50_000_000
    monkeypatch.setattr(stream, "ORG_ASSET_MAX_PIXELS", 200 * 100)
    decoded = []
    real_load = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load",
                        lambda image: decoded.append(image.size)
                        or real_load(image))
    assert env.service.ImportOrgAsset(
        "icon", _image_bytes((201, 100), RED)) == stream.INVALID_IMAGE
    # (Other threads' images may pass through the patched load.)
    assert (201, 100) not in decoded
    assert env.service.ImportOrgAsset(
        "icon", _image_bytes((200, 100), RED)) == stream.IMPORTED


@pytest.mark.parametrize("kind, data", [
    ("icon", _image_bytes((10, 10), RED, fmt="GIF")),
    ("icon", b"not an image"),
    ("icon", b""),
    ("icon", _image_bytes((400, 400), RED)[:200]),   # truncated
    ("banner", _image_bytes((10, 10), RED)),
])
def test_anything_else_is_refused_and_changes_nothing(env, kind, data):
    assert env.service.ImportOrgAsset(kind, data) == stream.INVALID_IMAGE
    assert not os.path.exists(schema.STREAM_ORG_ICON_PATH)
    assert SM.Get("stream.orgIconVersion", 0) == 0


def test_a_target_held_open_is_retried_then_reported(env, monkeypatch):
    real_replace = os.replace
    failures = {"left": 2}

    # os.replace is the process's: other threads (the state store's writer)
    # go through this too, untouched.
    def replace(source, target):
        if Path(target).name == "stream_org_icon.png" and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(32, "in use")
        real_replace(source, target)
    monkeypatch.setattr(stream.os, "replace", replace)
    monkeypatch.setattr(stream, "_REPLACE_BACKOFF", 0)
    data = _image_bytes((64, 64), RED)
    assert env.service.ImportOrgAsset("icon", data) == stream.IMPORTED
    assert os.path.exists(schema.STREAM_ORG_ICON_PATH)

    failures["left"] = stream._REPLACE_ATTEMPTS
    version = SM.Get("stream.orgIconVersion")
    assert env.service.ImportOrgAsset(
        "icon", _image_bytes((32, 32), BLUE)) == stream.SAVE_FAILED
    assert SM.Get("stream.orgIconVersion") == version
    with Image.open(schema.STREAM_ORG_ICON_PATH) as kept:
        assert kept.size == (64, 64)
    assert not list((env.dir / "layout").glob("*.tmp"))


def test_the_reset_unregisters_both_and_moves_the_thumbnail_to_the_banner(
        env):
    service = env.service
    service.ImportOrgAsset("icon", _image_bytes((64, 64), RED))
    service.ImportOrgAsset("thumbnail", _image_bytes((160, 90), BLUE))
    service.SetThumbnailOptions({"source": "org", "style": "netflix"})
    service.ResetOrgAssets()
    assert not os.path.exists(schema.STREAM_ORG_ICON_PATH)
    assert not os.path.exists(schema.STREAM_ORG_THUMB_PATH)
    assert SM.Get("stream.orgIconVersion") == 0
    assert SM.Get("stream.orgThumbVersion") == 0
    assert service.ThumbnailOptions() == {
        "source": "banner", "style": "netflix", "overlayOrgIcon": False}


# ── the thumbnail options ────────────────────────────────────────────────

def test_the_thumbnail_options_are_saved_and_validated(env, monkeypatch):
    service = env.service
    assert service.ThumbnailOptions() == stream.DEFAULT_THUMBNAIL_OPTIONS
    assert service.SetThumbnailOptions({"style": "netflix"}) == {
        "source": "banner", "style": "netflix", "overlayOrgIcon": False}
    assert service.SetThumbnailOptions(
        {"source": "org", "overlayOrgIcon": True}) == {
        "source": "org", "style": "netflix", "overlayOrgIcon": True}
    assert SettingsManager.Get(stream.THUMBNAIL_OPTIONS_SETTING) == {
        "source": "org", "style": "netflix", "overlayOrgIcon": True}
    # A bad key or value saves nothing.
    for bad in ({"style": "fancy"}, {"source": "org", "colour": 1},
                {"overlayOrgIcon": 1}, ["style"]):
        assert service.SetThumbnailOptions(bad) is None
    assert service.ThumbnailOptions()["style"] == "netflix"
    # An unchanged value is not written again.
    writes = []
    monkeypatch.setattr(SettingsManager, "Set",
                        lambda key, value: writes.append(key))
    service.SetThumbnailOptions({"style": "netflix"})
    assert writes == []
    # What a hand edit broke falls back to the default.
    SettingsManager.settings[stream.THUMBNAIL_OPTIONS_SETTING] = {
        "source": "nope", "style": "center", "overlayOrgIcon": "yes"}
    assert service.ThumbnailOptions() == stream.DEFAULT_THUMBNAIL_OPTIONS


# ── the thumbnail generator ──────────────────────────────────────────────

def _generate(env, executor):
    assert env.service.GenerateThumbnail() == stream.STARTED
    assert executor.drain(WAIT)
    return env.thumbnail()


@pytest.mark.parametrize("source", ["banner", "org"])
@pytest.mark.parametrize("style", ["center", "netflix"])
@pytest.mark.parametrize("overlay", [False, True])
def test_every_option_makes_its_thumbnail(env, executor, source, style,
                                         overlay):
    # A wide source, so the two styles differ at the top edge: a centre crop
    # fills it, the Netflix style shows the darkened blurred background.
    _save_image(stream.BANNER_PATH, (800, 200), GREEN)
    _save_image(schema.STREAM_ORG_THUMB_PATH, (800, 200), BLUE)
    _save_image(schema.STREAM_ORG_ICON_PATH, (100, 100), RED)
    env.service.SetThumbnailOptions(
        {"source": source, "style": style, "overlayOrgIcon": overlay})

    result = _generate(env, executor)
    assert result["status"] == stream.DONE
    assert result["error"] == ""
    assert result["revision"] == 1
    assert result["path"].startswith(f"out/stream/thumbnail_{source}_{style}_")
    assert result["path"].endswith(".jpg")
    with Image.open(result["path"]) as made:
        assert made.format == "JPEG"
        assert made.size == (1920, 1080)
        image = made.convert("RGB")
    color = GREEN if source == "banner" else BLUE
    assert _near(image.getpixel((960, 540)), color)
    top = image.getpixel((960, 20))
    if style == "center":
        assert _near(top, color)
    else:
        assert not _near(top, color) and sum(top) < sum(color)
    # The org icon: 12% of the height, inset by half that, bottom right.
    corner = image.getpixel((1790, 950))
    assert _near(corner, RED, 30) is overlay


def test_a_run_reports_running_first_and_keeps_the_last_path(env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    first = _generate(env, executor)
    assert env.service.GenerateThumbnail() == stream.STARTED
    running = env.thumbnail()
    assert running == {"status": stream.RUNNING, "path": first["path"],
                       "error": "", "revision": 2}
    assert executor.drain(WAIT)
    assert env.thumbnail()["revision"] == 2


def test_the_org_thumbnail_missing_is_an_error_that_keeps_the_last_path(
        env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    first = _generate(env, executor)
    env.service.SetThumbnailOptions({"source": "org"})
    result = _generate(env, executor)
    assert result == {"status": stream.ERROR, "path": first["path"],
                      "error": stream.NO_ORG_THUMBNAIL, "revision": 2}


def test_the_banner_is_downloaded_from_the_tournament(env, executor,
                                                      monkeypatch):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    env.banner["url"] = "https://images.example/banner.png"
    asked = []

    def get(url, headers, timeout):
        asked.append((url, timeout))
        return SimpleNamespace(content=_image_bytes((320, 180), ORANGE),
                               raise_for_status=lambda: None)
    monkeypatch.setattr(stream.requests, "get", get)
    result = _generate(env, executor)
    assert asked == [("https://images.example/banner.png", 20)]
    with Image.open(result["path"]) as made:
        assert _near(made.convert("RGB").getpixel((960, 540)), ORANGE)


@pytest.mark.parametrize("saved, default, expected", [
    (True, True, GREEN),      # the banner saved on the EVENT tab
    (False, True, PURPLE),    # the bundled default
    (False, False, None),     # nothing: an error
])
def test_a_failed_download_falls_back(env, executor, monkeypatch, saved,
                                      default, expected):
    env.banner["url"] = "https://images.example/banner.png"

    def get(url, headers, timeout):
        raise OSError("offline")
    monkeypatch.setattr(stream.requests, "get", get)
    if saved:
        _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    if default:
        _save_image(stream.DEFAULT_BANNER_PATH, (320, 180), PURPLE)
    result = _generate(env, executor)
    if expected is None:
        assert result["status"] == stream.ERROR
        assert result["error"] == stream.NO_BANNER
        return
    assert result["status"] == stream.DONE
    with Image.open(result["path"]) as made:
        assert _near(made.convert("RGB").getpixel((960, 540)), expected)


def test_a_lookup_that_raises_falls_back_too(env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)

    def lookup():
        raise RuntimeError("start.gg is down")
    env.service._bannerUrl = lookup
    assert _generate(env, executor)["status"] == stream.DONE


def test_a_second_run_is_refused_while_one_runs(env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    assert env.service.GenerateThumbnail() == stream.STARTED
    assert env.service.ThumbnailRunning() is True
    assert env.service.GenerateThumbnail() == stream.BUSY
    assert executor.queued() == 1
    assert env.thumbnail()["revision"] == 1
    assert executor.run_next(WAIT) is True
    assert env.thumbnail()["status"] == stream.DONE
    assert env.service.ThumbnailRunning() is False
    assert env.service.GenerateThumbnail() == stream.STARTED
    assert executor.drain(WAIT)
    assert env.thumbnail()["revision"] == 2


def test_a_job_the_shutdown_cancels_does_not_stay_running(env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    assert env.service.GenerateThumbnail() == stream.STARTED
    assert executor.queued() == 1
    assert executor.shutdown(WAIT)
    assert env.thumbnail() == {"status": stream.ERROR, "path": "",
                               "error": stream.CANCELLED, "revision": 1}
    assert env.service.ThumbnailRunning() is False
    # Submitted after the shutdown: cancelled at once, not left running.
    assert env.service.GenerateThumbnail() == stream.STARTED
    assert env.thumbnail()["error"] == stream.CANCELLED
    assert env.thumbnail()["revision"] == 2
    assert env.service.ThumbnailRunning() is False


def test_a_job_told_to_stop_during_the_download_writes_nothing(
        env, executor, monkeypatch):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    env.banner["url"] = "https://images.example/banner.png"

    def get(url, headers, timeout):
        executor.stopping.set()   # the shutdown began meanwhile
        return SimpleNamespace(content=_image_bytes((320, 180), ORANGE),
                               raise_for_status=lambda: None)
    monkeypatch.setattr(stream.requests, "get", get)
    result = _generate(env, executor)
    assert result["status"] == stream.ERROR
    assert result["error"] == stream.CANCELLED
    assert not (env.dir / "out" / "stream").exists() or not list(
        (env.dir / "out" / "stream").glob("thumbnail_*.jpg"))
    assert env.service.ThumbnailRunning() is False


def test_a_run_that_fails_reports_it_and_lets_the_next_one_start(
        env, executor, monkeypatch):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)

    def boom(image):
        raise ValueError("boom")
    monkeypatch.setattr(stream, "CenterCrop", boom)
    result = _generate(env, executor)
    assert result["status"] == stream.ERROR
    assert result["error"] == "boom"
    assert env.service.ThumbnailRunning() is False


def test_only_thirty_generations_of_our_own_output_are_kept(env, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)
    out = env.dir / "out" / "stream"
    out.mkdir(parents=True)
    for index in range(35):
        old = out / f"thumbnail_banner_center_2020010{index:02d}.jpg"
        old.write_bytes(b"x")
        os.utime(old, (1_000_000 + index, 1_000_000 + index))
    (out / "orgIconVersion.txt").write_text("0")
    result = _generate(env, executor)
    left = sorted(path.name for path in out.glob("thumbnail_*.jpg"))
    assert len(left) == stream.THUMBNAIL_KEEP
    assert Path(result["path"]).name in left
    assert (out / "orgIconVersion.txt").exists()


def test_the_thumbnail_starts_idle_at_every_launch(env):
    SM.Set(stream.THUMBNAIL_PATH, {"status": "running", "path": "x",
                                   "error": "", "revision": 4})
    with SM.SaveBlock():
        schema.SeedPanelState()
    assert env.thumbnail() == schema.DefaultStreamThumbnail()


# ── the panel keeps none of it ───────────────────────────────────────────

PANEL = PROJECT_ROOT / "src" / "AutonaStreamSettingsWidget.py"

# Calls that write a file or a setting, by attribute name.
_WRITES = {"save", "write", "write_bytes", "write_text", "mkdir", "makedirs",
           "remove", "unlink", "rename", "replace", "copyfile", "Set",
           "SetIfChanged", "Unset", "PublishAssetVersion"}


def test_the_panel_writes_no_settings_files_or_images():
    source = PANEL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[-1])
    assert not imported & {"PIL", "requests", "SettingsManager",
                           "AutonaOutputRetention", "datetime"}, imported
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    writes = sorted({call.func.attr for call in calls
                     if isinstance(call.func, ast.Attribute)
                     and call.func.attr in _WRITES})
    assert writes == [], writes
    assert not [call for call in calls
                if isinstance(call.func, ast.Name) and call.func.id == "open"]
    # The title is core's: the panel no longer listens to the provider or
    # the game assets to make one.
    for name in ("AutonaTournamentDataProvider", "AutonaGameAssetManager",
                 "top_entrants", "SettingsManager"):
        assert name not in source, name


def test_the_panel_asks_the_core_for_every_operation():
    tree = ast.parse(PANEL.read_text(encoding="utf-8"))
    asked = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Attribute)
             and ast.unparse(node.func.value) == "StreamService.instance"}
    assert asked == {
        "Channels", "AddChannel", "UpdateChannel", "DeleteChannel",
        "ImportOrgAsset", "ResetOrgAssets", "ThumbnailOptions",
        "SetThumbnailOptions", "GenerateThumbnail"}


# ── the panel draws what core saved ──────────────────────────────────────

@pytest.fixture
def panel(env, qapp):
    from src.AutonaStreamSettingsWidget import AutonaStreamSettingsWidget
    widget = AutonaStreamSettingsWidget()
    try:
        yield widget
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_the_panel_shows_the_saved_options_and_saves_its_changes(
        env, panel, qapp):
    env.service.SetThumbnailOptions({"style": "netflix"})
    panel._renderThumbnailOptions()
    assert panel.rbStreamThumbSourceBanner.isChecked()
    assert panel.rbStreamThumbNetflix.isChecked()
    # The source and the style are two choices, not one of four (the .ui
    # puts the four radios under one parent).
    panel.rbStreamThumbSourceOrg.click()
    assert panel.rbStreamThumbSourceOrg.isChecked()
    assert panel.rbStreamThumbNetflix.isChecked()
    panel.rbStreamThumbCenter.click()
    panel.cbStreamThumbOverlayOrgIcon.click()
    assert env.service.ThumbnailOptions() == {
        "source": "org", "style": "center", "overlayOrgIcon": True}


def test_the_panel_draws_the_title_and_the_thumbnail_from_the_state(
        env, panel, qapp):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    qapp.processEvents()
    assert panel.streamTitleEdit.text() == "Cup"

    _save_image("out/stream/thumbnail_banner_center_1.jpg", (320, 180),
                GREEN)
    SM.Set(stream.THUMBNAIL_PATH, {
        "status": stream.DONE, "path": "out/stream/thumbnail_banner_center_1.jpg",
        "error": "", "revision": 1})
    qapp.processEvents()
    assert panel.streamThumbPath.text().endswith(
        "out/stream/thumbnail_banner_center_1.jpg")
    assert not panel.streamThumbPreview.pixmap().isNull()
    assert panel.btGenerateStreamThumbnail.isEnabled()

    SM.Set(stream.THUMBNAIL_PATH, {
        "status": stream.RUNNING, "path": "", "error": "", "revision": 2})
    qapp.processEvents()
    assert not panel.btGenerateStreamThumbnail.isEnabled()


def test_the_panel_draws_the_options_without_saving_them_back(
        env, panel, monkeypatch):
    saved = []
    monkeypatch.setattr(env.service, "SetThumbnailOptions",
                        lambda changes: saved.append(changes))
    SettingsManager.settings[stream.THUMBNAIL_OPTIONS_SETTING] = {
        "source": "org", "style": "netflix", "overlayOrgIcon": True}
    panel._renderThumbnailOptions()
    SettingsManager.settings[stream.THUMBNAIL_OPTIONS_SETTING] = {
        "source": "banner", "style": "center", "overlayOrgIcon": False}
    panel._renderThumbnailOptions()
    assert panel.rbStreamThumbSourceBanner.isChecked()
    assert panel.rbStreamThumbCenter.isChecked()
    assert saved == []


def test_the_reset_puts_the_panel_back_on_the_banner(env, panel,
                                                     monkeypatch):
    env.service.ImportOrgAsset("thumbnail", _image_bytes((160, 90), BLUE))
    panel.rbStreamThumbSourceOrg.click()
    assert env.service.ThumbnailOptions()["source"] == "org"
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.ResetOrgMedia()
    assert panel.rbStreamThumbSourceBanner.isChecked()
    assert env.service.ThumbnailOptions()["source"] == "banner"
    assert panel.orgThumbView.pixmap().isNull()


def test_the_panels_channel_edits_take_the_destination_along(
        env, panel, qapp, monkeypatch):
    env.service.AddChannel("Main", "https://a.example")
    env.service.AddChannel("Sub", "https://b.example")
    panel._loadStreamChannels()
    panel.streamUrlCombo.setCurrentIndex(2)
    assert SM.Get(stream.URL_PATH) == "https://b.example"

    monkeypatch.setattr(panel, "_promptStreamChannel",
                        lambda *args, **kwargs: (
                            "save", "Sub", "https://b2.example"))
    panel._editStreamChannel()
    qapp.processEvents()
    assert SM.Get(stream.URL_PATH) == "https://b2.example"
    assert panel.streamUrlCombo.currentIndex() == 2
    assert panel.streamUrlCombo.currentData() == "https://b2.example"

    monkeypatch.setattr(panel, "_promptStreamChannel",
                        lambda *args, **kwargs: ("delete", None, None))
    panel._editStreamChannel()
    qapp.processEvents()
    assert SM.Get(stream.URL_PATH) == ""
    assert panel.streamUrlCombo.currentIndex() == 0
    assert panel.streamUrlCombo.currentText() == ""
    assert panel.streamUrlCombo.count() == 2
    assert not panel.btEditStreamChannel.isEnabled()


@pytest.mark.parametrize("data, outcome", [
    (b"not an image", "読み込めません"),
    (None, "保存できません"),
])
def test_an_import_that_fails_is_reported(env, panel, tmp_path, monkeypatch,
                                         data, outcome):
    source = tmp_path / "picked.png"
    source.write_bytes(data if data is not None
                       else _image_bytes((32, 32), RED))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(source), ""))
    if data is None:
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == "stream_org_icon.png":
                raise PermissionError(32, "in use")
            real_replace(source, target)
        monkeypatch.setattr(stream.os, "replace", replace)
        monkeypatch.setattr(stream, "_REPLACE_BACKOFF", 0)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text: warned.append(text))
    panel.LoadOrgIcon()
    assert len(warned) == 1 and outcome in warned[0]
    assert panel.orgIconView.pixmap().isNull()


def test_typing_wins_over_an_auto_title_that_lands_meanwhile(
        env, panel, qapp, monkeypatch):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    qapp.processEvents()
    edit = panel.streamTitleEdit
    assert edit.text() == "Cup"

    monkeypatch.setattr(edit, "hasFocus", lambda: True)
    edit.setText("Typed by hand")
    # The entrants fetch ends while the operator types: a new auto title.
    env.service.SetTopEntrants(_entrants("Ada"))
    env.run_queued()
    qapp.processEvents()
    assert env.title() == "Cup ft. Ada ... and more!"
    assert edit.text() == "Typed by hand"      # not redrawn while in it

    monkeypatch.setattr(edit, "hasFocus", lambda: False)
    edit.editingFinished.emit()
    qapp.processEvents()
    assert env.title() == "Typed by hand"
    assert edit.text() == "Typed by hand"
    # ...and kept by the next refresh.
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    env.run_queued()
    qapp.processEvents()
    assert env.title() == "Typed by hand"


def test_a_field_merely_left_alone_writes_nothing_back(env, panel, qapp,
                                                      monkeypatch):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    qapp.processEvents()
    edit = panel.streamTitleEdit
    monkeypatch.setattr(edit, "hasFocus", lambda: True)
    SM.Set("tournamentInfo.tournamentName", "Cup 2")
    env.run_queued()
    qapp.processEvents()
    assert edit.text() == "Cup"
    monkeypatch.setattr(edit, "hasFocus", lambda: False)
    edit.editingFinished.emit()
    qapp.processEvents()
    assert env.title() == "Cup 2"
    assert edit.text() == "Cup 2"


def test_another_writers_title_wins_over_the_typing(env, panel, qapp,
                                                    monkeypatch):
    SM.Set("tournamentInfo.tournamentName", "Cup")
    env.run_queued()
    qapp.processEvents()
    edit = panel.streamTitleEdit
    monkeypatch.setattr(edit, "hasFocus", lambda: True)
    edit.setText("Typed by hand")
    AutonaActions.instance.SetStreamTitle("From the Web")
    qapp.processEvents()
    monkeypatch.setattr(edit, "hasFocus", lambda: False)
    edit.editingFinished.emit()
    qapp.processEvents()
    assert env.title() == "From the Web"
    assert edit.text() == "From the Web"
