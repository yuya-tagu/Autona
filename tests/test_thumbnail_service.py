"""The match thumbnail's core (core/ThumbnailService.py, ADR-005 S4o).

Moved from the Qt panel and src/thumbnail/main_generate_thumbnail.py: the
payload and the video title, replayed against what the old code answered
(tests/fixtures/thumbnail/golden.json), and the settings, the background and
the output's names. New: a render in the background, whose result is the
state's thumbnail.render.

On a ThumbnailService of the test's own in a directory of its own, with a
stand-in for the shell's renderer and a stepped executor: the real renderer's
half is tests/test_thumbnail_render.py, the routes tests/test_api_v1_thumbnail.py.
"""
import copy
import datetime
import json
import os
import random
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger
from PIL import Image

from src.core import ImageImport
from src.core import StateSchema as schema
from src.core import ThumbnailService as thumbnails
from src.core.DictHelper import deep_clone
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TaskExecutor import TaskExecutor
from src.core.ThumbnailService import ThumbnailService
from src.Helpers.AutonaOutputRetention import PruneOutputFiles
from tests.fixtures.thumbnail import scenarios as world

WAIT = 30.0
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "thumbnail"
                     / "golden.json").read_text(encoding="utf-8"))

PLAYERS = copy.deepcopy(world.WITH_PLAYERS)
KNOWN_GAMES = ("ssbu", "sf6", "new", "g", "a", "b")


class _Catalog:
    """What a placeholder preview reads of the game asset catalog."""

    def __init__(self, games):
        self._snapshot = SimpleNamespace(characters=games["characters"],
                                         game=games["game"])
        self._assets = games["assets"]

    def Current(self):
        return self._snapshot

    def CharacterAssets(self, codename, skin, assetpack=None):
        return world.CharacterAssets(self._assets, codename, skin)


class _Renderer:
    """The shell's renderer, as ThumbnailService's `render` sees it: what was
    asked is kept, and answered when the test says."""

    def __init__(self):
        self.asked = []
        self.refuse = None

    def __call__(self, kind, payload, done):
        if self.refuse is not None:
            raise self.refuse
        self.asked.append(SimpleNamespace(kind=kind, payload=payload,
                                          done=done))

    def answer(self, image=b"an image", index=0):
        asked = self.asked.pop(index)
        asked.done(lambda file_format, fast=False:
                   image + b"." + file_format.encode(), "")
        return asked

    def fail(self, index=0):
        asked = self.asked.pop(index)
        asked.done(None, "it broke")
        return asked


class _Env(SimpleNamespace):
    def enter(self, scenario):
        world.Install(self.dir, scenario)
        SM.state = copy.deepcopy(scenario["state"])
        SettingsManager.settings = copy.deepcopy(scenario["settings"])
        self.locale.update(scenario["locale"])
        random.seed(world.SEED)

    def config(self):
        return SettingsManager.settings.get("thumbnail_config")

    def render(self):
        return SM.Get(thumbnails.RENDER_PATH)

    def run_queued(self):
        ran = 0
        while self.queued:
            self.queued.pop(0)()
            ran += 1
        return ran


def _write_image(path, size=(64, 32), color=(0, 200, 0), fmt="PNG"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, fmt)


def _image_bytes(size=(64, 32), color=(0, 200, 0), fmt="PNG"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def env(autonaApp, tmp_path, monkeypatch):
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
        SM.state = {}
        SM.lastSavedState = {}
    settings = SettingsManager.settings
    SettingsManager.settings = {}

    queued = []
    locale = dict(world.LOCALE)
    renderer = _Renderer()
    service = ThumbnailService(
        later=queued.append, render=renderer,
        locale=lambda: (locale["programLocale"], locale["matchNames"],
                        locale["phaseNames"]),
        prune=PruneOutputFiles)
    monkeypatch.setattr(ThumbnailService, "instance", service)
    # The game list the tests' updates are for.
    monkeypatch.setattr(GameAssetCatalog.instance, "GameCodenames",
                        lambda: list(KNOWN_GAMES))
    try:
        environment = _Env(service=service, queued=queued, locale=locale,
                           renderer=renderer, dir=tmp_path)
        world.Install(tmp_path, world._scenario("files"))
        yield environment
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
    """A stepped executor of the test's own: a job runs when the test says."""
    assert TaskExecutor.instance.drain(WAIT)
    made = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", made)
    try:
        yield made
    finally:
        made.shutdown(WAIT)


@pytest.fixture
def match(env, executor):
    """A complete match in the state, with no settings saved."""
    SM.state = copy.deepcopy(PLAYERS)
    return env


# ── what moved: the payload and the title, against the recording ─────────

@pytest.mark.parametrize("scenario", world.SCENARIOS,
                         ids=[s["name"] for s in world.SCENARIOS])
def test_the_payload_is_what_the_old_code_built(env, scenario):
    env.enter(scenario)
    expected = GOLDEN["payloads"][scenario["name"]]
    games = _Catalog(scenario["games"]) if scenario["games"] else None

    if "raises" in expected:
        with pytest.raises(Exception) as raised:
            env.service.BuildPayload(preview=scenario["preview"], games=games,
                                     board=scenario["board"])
        assert str(raised.value) == expected["raises"]
        return

    data, config, template = env.service.BuildPayload(
        preview=scenario["preview"], games=games, board=scenario["board"])
    # As text: the key order is kept too.
    assert json.dumps({"data": data, "config": config, "template": template},
                      ensure_ascii=False) \
        == json.dumps(expected, ensure_ascii=False)


def test_the_recording_covers_every_scenario():
    assert set(GOLDEN["payloads"]) == {s["name"] for s in world.SCENARIOS}
    assert set(GOLDEN["titles"]) == {s["name"] for s in world.TITLE_SCENARIOS}


@pytest.mark.parametrize("scenario", world.TITLE_SCENARIOS,
                         ids=[s["name"] for s in world.TITLE_SCENARIOS])
def test_the_video_title_is_what_the_old_code_built(env, scenario):
    SM.state = copy.deepcopy(scenario["state"])

    assert env.service.VideoTitle(scenario["board"]) \
        == GOLDEN["titles"][scenario["name"]]


def test_the_video_title_reads_slots_keyed_by_integers(env):
    # What a player card stores in memory; json cannot record it.
    SM.state = {"score": {"1": {"team": {
        "1": {"player": {"1": {"gamerTag": "Alpha", "character": {
            10: {"name": "Link"}, 2: {"name": "Mario"}}}}},
        "2": {"player": {"1": {"gamerTag": "Bravo"}}}}}}}

    assert env.service.VideoTitle() == "Alpha(Mario) vs Bravo"


def test_a_preview_shows_what_is_not_published_yet(match):
    # The form: held for 反映, not in what a client is sent.
    SM.state["score"]["1"]["team"]["1"]["player"]["1"]["name"] = "Typed"
    with SM.lock:
        SM.lastSavedState = deep_clone(SM.state)
        SM.lastSavedState["score"]["1"]["team"]["1"]["player"]["1"][
            "name"] = "Published"

    data, _config, _template = match.service.BuildPayload()

    assert data["score"]["1"]["team"]["1"]["player"]["1"]["name"] == "Typed"


def test_the_payload_shares_nothing_with_the_settings(env):
    SettingsManager.settings = {"thumbnail_config": {
        "game": {"tgame": {"zoom": 150}}}}
    SM.state = copy.deepcopy(PLAYERS)

    _data, config, _template = env.service.BuildPayload()
    config["game"]["tgame"]["zoom"] = 1

    assert env.config()["game"]["tgame"]["zoom"] == 150


def test_a_placeholder_needs_a_selected_game(env):
    # The catalog keeps the characters when the selection is forgotten (the
    # tournament was cleared). The old code fell over here.
    catalog = _Catalog(dict(world.GAMES, game={}))

    data, _config, _template = env.service.BuildPayload(
        preview=True, games=catalog)

    assert data["game"]["codename"] == "test"
    assert data["score"]["1"]["team"]["1"]["player"]["1"]["name"] == ""


def test_a_missing_match_says_which_in_its_code(env):
    (env.dir / "out" / "program_state.json").write_text(json.dumps({}))
    with pytest.raises(thumbnails.ThumbnailDataMissing) as raised:
        env.service.BuildPayload()
    assert raised.value.code == thumbnails.NO_GAME

    (env.dir / "out" / "program_state.json").write_text(json.dumps(
        {"game": {"codename": "tgame"}}))
    with pytest.raises(thumbnails.ThumbnailDataMissing) as raised:
        env.service.BuildPayload()
    assert raised.value.code == thumbnails.NO_PLAYER


@pytest.mark.parametrize("program", [
    None, "{not json", "[]", '{"game": 5}', '{"game": {"codename": "x"}, '
    '"score": {"1": {"team": {"1": {"player": {"1": 5}}}}}}'])
@pytest.mark.parametrize("state, code", [
    ({}, thumbnails.NO_GAME),
    ({"game": {"codename": "tgame"}}, thumbnails.NO_PLAYER),
])
def test_a_file_that_cannot_be_read_is_a_missing_match_too(
        env, program, state, code):
    # The state, which has no complete match, says what is missing.
    path = env.dir / "out" / "program_state.json"
    path.unlink(missing_ok=True)
    if program is not None:
        path.write_text(program)
    SM.state = copy.deepcopy(state)

    with pytest.raises(thumbnails.ThumbnailDataMissing) as raised:
        env.service.BuildPayload()

    assert raised.value.code == code


# ── the background's resolution ──────────────────────────────────────────

def test_the_banner_is_looked_for_on_every_render(env):
    config = {"background_source": "banner"}
    assert thumbnails.ResolveBackgroundPath(config) \
        == thumbnails.DEFAULT_BANNER_PATH
    _write_image(env.dir / "user_data" / "tournament_banner.png")
    assert thumbnails.ResolveBackgroundPath(config) == thumbnails.BANNER_PATH


def test_the_banner_constants_are_one_pair():
    from src.core import StreamService as stream
    from src.thumbnail import main_generate_thumbnail as legacy

    assert stream.BANNER_PATH == legacy.BANNER_PATH == thumbnails.BANNER_PATH \
        == schema.TOURNAMENT_BANNER_PATH == "./user_data/tournament_banner.png"
    assert stream.DEFAULT_BANNER_PATH == legacy.DEFAULT_BANNER_PATH \
        == thumbnails.DEFAULT_BANNER_PATH == schema.DEFAULT_BANNER_PATH \
        == "./assets/thumbnail_base/default_banner.png"


# ── reading the settings ─────────────────────────────────────────────────

def test_the_defaults_when_nothing_is_saved(env):
    assert env.service.Settings() == {
        "template_id": "type_a",
        "background_source": "default",
        "background_name": "",
        "display_phase": True,
        "use_team_names": True,
        "use_sponsors": True,
        "game": {},
    }


def test_reading_writes_nothing(env, monkeypatch):
    # The panel's GetSetting saved every default it read.
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))
    SM.state = copy.deepcopy(PLAYERS)
    legacy = {"background_path": "./user_data/old.png"}
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(legacy)}

    env.service.Settings()
    env.service.Describe()
    env.service.Templates()

    assert writes == []
    assert env.config() == legacy


def test_the_game_in_the_state_gets_its_defaults(env):
    SM.state = {"game": {"codename": "tgame", "name": "Test Game"}}

    assert env.service.Settings()["game"] == {"tgame": {
        "flip_p1": False, "flip_p2": True, "smooth_scale": True, "zoom": 100,
        "align": {"horizontal": 50, "vertical": 40}}}


def test_saved_settings_are_read_and_filled_in(env):
    SettingsManager.settings = {"thumbnail_config": {
        "template_id": "type_b", "display_phase": False,
        "game": {"ssbu": {"zoom": 150, "align": {"vertical": 10},
                          "asset_pack": "costume"}}}}

    settings = env.service.Settings()

    assert settings["template_id"] == "type_b"
    assert settings["display_phase"] is False
    assert settings["use_sponsors"] is True
    assert settings["game"]["ssbu"] == {
        "flip_p1": False, "flip_p2": True, "smooth_scale": True, "zoom": 150,
        "align": {"horizontal": 50, "vertical": 10}}


def test_the_remains_of_the_old_reads_are_skipped_not_removed(env):
    remains = {"zoom": 100, "flip_p2": True}
    SettingsManager.settings = {"thumbnail_config": {
        "game": {"None": remains, "ssbu": {"zoom": 120}}}}

    assert list(env.service.Settings()["game"]) == ["ssbu"]
    env.service.Update({"game": {"ssbu": {"zoom": 130}}})

    assert env.config()["game"]["None"] == remains


def test_a_malformed_saved_setting_reads_as_its_default(env):
    SettingsManager.settings = {"thumbnail_config": {
        "template_id": "nope", "background_source": "nope",
        "display_phase": "yes", "use_team_names": 1,
        "game": {"ssbu": {"zoom": "big", "flip_p1": 1,
                          "align": {"horizontal": 900}},
                 "broken": "not a dict", "a.b": {"zoom": 1}}}}

    settings = env.service.Settings()

    assert settings["template_id"] == "type_a"
    assert settings["background_source"] == "default"
    assert settings["display_phase"] is True
    assert settings["use_team_names"] is True
    assert settings["game"] == {"ssbu": {
        "flip_p1": False, "flip_p2": True, "smooth_scale": True, "zoom": 100,
        "align": {"horizontal": 50, "vertical": 40}}}


def test_an_alignment_of_0_reads_as_what_the_template_draws_for_it(env):
    # The panel's spin boxes save it.
    SettingsManager.settings = {"thumbnail_config": {"game": {"ssbu": {
        "align": {"horizontal": 0, "vertical": 0}}}}}

    assert env.service.Settings()["game"]["ssbu"]["align"] == {
        "horizontal": 50, "vertical": 40}


@pytest.mark.parametrize("name", ["thumbnail", "thumbnail_type_b"])
def test_the_alignments_default_is_the_templates(name):
    # What makes the reading above true, and the reason 0 is not taken. If the
    # templates change, so does that.
    script = (Path(__file__).parent.parent / "layout" / name
              / "thumbnail.js").read_text(encoding="utf-8")
    align = thumbnails.DEFAULT_GAME_SETTINGS["align"]

    assert f"gameConfig.align?.horizontal || {align['horizontal']})" in script
    assert f"gameConfig.align?.vertical || {align['vertical']})" in script


@pytest.mark.parametrize("path, source, name", [
    ("", "default", ""),
    ("./user_data/tournament_banner.png", "banner", ""),
    ("./assets/thumbnail_base/default_banner.png", "banner", ""),
    ("C:\\Users\\someone\\Pictures\\my image.png", "custom", "my image.png"),
    ("/home/someone/bg.jpg", "custom", "bg.jpg"),
])
def test_a_config_from_before_the_selector_reads_as_it_will_move(
        env, path, source, name):
    SettingsManager.settings = {"thumbnail_config": {"background_path": path}}

    settings = env.service.Settings()

    assert settings["background_source"] == source
    # A name to show: where the operator's files are is not told.
    assert settings["background_name"] == name


# ── writing the settings ─────────────────────────────────────────────────

def test_an_update_merges_and_comes_back_as_saved(env):
    SettingsManager.settings = {"thumbnail_config": {
        "background_source": "default", "use_sponsors": False,
        "game": {"ssbu": {"zoom": 150, "align": {"horizontal": 10}},
                 "sf6": {"zoom": 90}}}}

    settings = env.service.Update({
        "template_id": "type_b", "display_phase": False,
        "game": {"ssbu": {"flip_p1": True, "align": {"vertical": 20}},
                 "new": {"zoom": 1000}}})

    assert env.config() == {
        "background_source": "default", "use_sponsors": False,
        "template_id": "type_b", "display_phase": False,
        "game": {"ssbu": {"zoom": 150, "flip_p1": True,
                          "align": {"horizontal": 10, "vertical": 20}},
                 "sf6": {"zoom": 90}, "new": {"zoom": 1000}}}
    assert settings == env.service.Settings()
    assert settings["game"]["ssbu"]["align"] == {"horizontal": 10,
                                                 "vertical": 20}


def test_an_update_is_saved_once(env, monkeypatch):
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))

    env.service.Update({"template_id": "type_b", "display_phase": False,
                        "game": {"ssbu": {"zoom": 2, "flip_p1": True}}})

    assert len(writes) == 1


def test_an_update_that_changes_nothing_is_not_saved(env, monkeypatch):
    SettingsManager.settings = {"thumbnail_config": {
        "background_source": "default", "display_phase": False}}
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))

    env.service.Update({"display_phase": False})

    assert writes == []


def test_an_update_moves_a_config_from_before_the_selector(env):
    SettingsManager.settings = {"thumbnail_config": {
        "background_path": "./user_data/tournament_banner.png"}}

    env.service.Update({"display_phase": False})

    assert env.config() == {"background_path": "",
                            "background_source": "banner",
                            "display_phase": False}


def test_the_edges_of_the_ranges_are_taken(env):
    settings = env.service.Update({"game": {"g": {
        "zoom": 1, "align": {"horizontal": 1, "vertical": 100}}}})
    assert settings["game"]["g"]["zoom"] == 1
    assert settings["game"]["g"]["align"] == {"horizontal": 1, "vertical": 100}
    settings = env.service.Update({"game": {"g": {"zoom": 1000}}})
    assert settings["game"]["g"]["zoom"] == 1000


def test_what_comes_back_of_an_alignment_is_what_was_asked_for(env):
    # Why 0 is not taken: it would come back, and be drawn, as the default.
    assert env.service.Update({"game": {"g": {
        "align": {"horizontal": 0}}}}) is None
    assert env.service.Update({"game": {"g": {
        "align": {"vertical": 0}}}}) is None
    assert env.config() is None


def test_a_game_that_is_not_in_the_list_is_not_saved(env, monkeypatch):
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))

    assert env.service.Update({"game": {"typo": {"zoom": 100}}}) is None
    # One of them is enough, as with any other bad value.
    assert env.service.Update({"display_phase": False, "game": {
        "ssbu": {"zoom": 100}, "typo": {"zoom": 100}}}) is None

    assert env.config() is None
    assert writes == []


def test_the_game_in_the_state_is_taken_though_it_is_not_in_the_list(env):
    SM.state = {"game": {"codename": "tgame", "name": "Test Game"}}

    settings = env.service.Update({"game": {"tgame": {"zoom": 200}}})

    assert settings["game"]["tgame"]["zoom"] == 200


@pytest.mark.parametrize("nothing", ["an empty list", "no catalog"])
def test_before_the_list_is_read_only_the_game_in_the_state_is_taken(
        env, monkeypatch, nothing):
    if nothing == "no catalog":
        monkeypatch.setattr(GameAssetCatalog, "instance", None)
    else:
        monkeypatch.setattr(GameAssetCatalog.instance, "GameCodenames",
                            lambda: [])

    # No game is on offer yet.
    assert env.service.Update({"game": {"ssbu": {"zoom": 100}}}) is None
    assert env.service.Update({"display_phase": False}) is not None

    SM.state = {"game": {"codename": "tgame", "name": "Test Game"}}
    assert env.service.Update({"game": {"ssbu": {"zoom": 100}}}) is None
    assert env.service.Update({"game": {"tgame": {"zoom": 100}}}) is not None


def test_a_saved_game_that_is_gone_from_the_list_is_still_read(env):
    # And not removed: the game may come back.
    SettingsManager.settings = {"thumbnail_config": {
        "game": {"gone": {"zoom": 120}}}}

    assert env.service.Settings()["game"]["gone"]["zoom"] == 120
    assert env.service.Update({"game": {"gone": {"zoom": 130}}}) is None
    assert env.service.Update({"display_phase": False}) is not None
    assert env.config()["game"]["gone"] == {"zoom": 120}


@pytest.mark.parametrize("changes", [
    "not a dict",
    ["display_phase"],
    {"unknown": True},
    {"template_id": "type_z"},
    {"template_id": 1},
    {"template_id": ["type_a"]},
    {"background_source": "elsewhere"},
    {"background_source": None},
    # A path would have the render read any local file.
    {"background_path": "C:/Windows/secret.png"},
    {"main_icon_path": "./layout/logo.png"},
    {"display_phase": 1},
    {"use_team_names": "true"},
    {"use_sponsors": None},
    {"game": "x"},
    {"game": {"ssbu": "x"}},
    {"game": {"ssbu": {"unknown": 1}}},
    {"game": {"ssbu": {"asset_pack": "costume"}}},
    {"game": {"ssbu": {"flip_p1": 1}}},
    {"game": {"ssbu": {"smooth_scale": "yes"}}},
    {"game": {"ssbu": {"zoom": 0}}},
    {"game": {"ssbu": {"zoom": 1001}}},
    {"game": {"ssbu": {"zoom": 1.5}}},
    {"game": {"ssbu": {"zoom": True}}},
    {"game": {"ssbu": {"align": 50}}},
    {"game": {"ssbu": {"align": {}}}},
    {"game": {"ssbu": {"align": {"depth": 50}}}},
    {"game": {"ssbu": {"align": {"horizontal": -1}}}},
    {"game": {"ssbu": {"align": {"horizontal": 0}}}},
    {"game": {"ssbu": {"align": {"vertical": 0}}}},
    {"game": {"ssbu": {"align": {"vertical": 101}}}},
    {"game": {"not_in_the_list": {"zoom": 100}}},
    {"game": {"a.b": {"zoom": 100}}},
    {"game": {"a/b": {"zoom": 100}}},
    {"game": {"a\\b": {"zoom": 100}}},
    {"game": {"..": {"zoom": 100}}},
    {"game": {"": {"zoom": 100}}},
    {"game": {"None": {"zoom": 100}}},
    {"game": {"a\nb": {"zoom": 100}}},
    {"game": {"x" * 129: {"zoom": 100}}},
])
def test_a_bad_update_saves_nothing(env, changes, monkeypatch):
    kept = {"display_phase": True}
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(kept)}
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))

    assert env.service.Update(changes) is None

    assert writes == []
    assert env.config() == kept


def test_one_bad_value_refuses_the_good_ones_too(env):
    assert env.service.Update({"display_phase": False, "zoom": 1}) is None
    assert env.service.Update(
        {"display_phase": False,
         "game": {"a": {"zoom": 2}, "b": {"zoom": 0}}}) is None

    assert env.config() is None


def test_reset_goes_back_to_the_defaults_for_every_game(env):
    SettingsManager.settings = {"other": 1, "thumbnail_config": {
        "template_id": "type_b", "game": {"ssbu": {"zoom": 2}}}}

    env.service.Reset()

    assert SettingsManager.settings == {"other": 1}
    assert env.service.Settings()["template_id"] == "type_a"


@pytest.mark.parametrize("legacy, moved", [
    ({}, {"background_source": "default"}),
    ({"background_path": ""}, {"background_path": "",
                               "background_source": "default"}),
    ({"background_path": "./user_data/tournament_banner.png"},
     {"background_path": "", "background_source": "banner"}),
    ({"background_path": "./assets/thumbnail_base/default_banner.png"},
     {"background_path": "", "background_source": "banner"}),
    ({"background_path": "C:/x.png"},
     {"background_path": "C:/x.png", "background_source": "custom"}),
    ({"background_source": "default", "background_path": "C:/x.png"},
     {"background_source": "default", "background_path": "C:/x.png"}),
])
def test_an_old_config_is_drawn_moved_and_left_as_saved(
        match, legacy, moved, monkeypatch):
    # The move the Qt panel made when it was built (and saved), made on the
    # copy drawn only (S4p review): a write saves it (the next test).
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(legacy)}
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))

    _data, config, _template = match.service.RenderPayload()

    assert config["background_source"] == moved["background_source"]
    assert config["background_path"] \
        == thumbnails.ResolveBackgroundPath(moved)
    assert match.config() == legacy
    assert writes == []


@pytest.mark.parametrize("legacy, moved", [
    ({"background_path": "./user_data/tournament_banner.png"},
     {"background_path": "", "background_source": "banner"}),
    ({"background_path": "C:/x.png"},
     {"background_path": "C:/x.png", "background_source": "custom"}),
])
def test_a_write_saves_the_move_of_an_old_config(env, legacy, moved):
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(legacy)}

    env.service.Update({"display_phase": False})

    assert env.config() == {**moved, "display_phase": False}


@pytest.mark.parametrize("saved", [
    {},
    {"background_source": "banner"},                     # no background_path
    {"background_source": "banner", "background_path": ""},
    {"background_source": "banner", "background_path": 7},
    {"background_source": "default", "background_path": "C:/x.png"},
    {"background_source": "custom", "background_path": "C:/x.png"},
    {"background_source": "custom"},
    {"background_source": "custom", "background_path": ""},
    {"background_source": "custom", "background_path": 7},
    {"background_source": "unheard of", "background_path": "C:/x.png"},
    {"background_path": "C:/x.png"},                     # before the selector
    {"background_path": "./user_data/tournament_banner.png"},
    {"background_path": 7},
])
def test_the_background_the_result_follows_is_the_one_a_render_draws(
        match, saved):
    """The result's picture says it has the thumbnail's background (ADR-005 S4q,
    decision 15). It read background_path before resolving it: a banner saved
    without one was the template's default."""
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(saved)}

    _data, config, _template = match.service.RenderPayload()

    assert thumbnails.EffectiveBackgroundPath() == config["background_path"]


def test_a_banner_saved_without_a_path_is_the_banner(match):
    SettingsManager.settings = {"thumbnail_config": {
        "background_source": "banner"}}

    assert thumbnails.EffectiveBackgroundPath() in (
        thumbnails.BANNER_PATH, thumbnails.DEFAULT_BANNER_PATH)


# ── the custom background ────────────────────────────────────────────────

def test_an_uploaded_background_is_saved_and_selected(env):
    answer = env.service.ImportBackground(_image_bytes())

    assert answer == thumbnails.IMPORTED
    config = env.config()
    assert config["background_source"] == "custom"
    path = config["background_path"]
    assert path.startswith("./user_data/thumbnail_background_")
    assert path.endswith(".png")
    assert Image.open(env.dir / path).size == (64, 32)
    # What the template and the Result generator read.
    assert thumbnails.ResolveBackgroundPath(config) == path
    assert env.service.Settings()["background_name"] == Path(path).name


def test_a_large_background_is_scaled_down(env):
    env.service.ImportBackground(_image_bytes(size=(3840, 2160)))

    path = env.dir / env.config()["background_path"]
    assert Image.open(path).size == (1920, 1080)


def test_a_new_image_has_a_new_name_and_the_old_one_goes(env):
    # The template has no cache buster: the same path would keep showing the
    # image it decoded first.
    env.service.ImportBackground(_image_bytes(color=(1, 2, 3)))
    first = env.config()["background_path"]
    env.service.ImportBackground(_image_bytes(color=(200, 2, 3)))
    second = env.config()["background_path"]

    assert first != second
    assert not (env.dir / first).exists()
    assert (env.dir / second).exists()


def test_the_same_image_keeps_its_file(env):
    env.service.ImportBackground(_image_bytes())
    first = env.config()["background_path"]
    env.service.ImportBackground(_image_bytes())

    assert env.config()["background_path"] == first
    assert (env.dir / first).exists()


def test_only_uploaded_backgrounds_are_ever_removed(env):
    keep = env.dir / "user_data" / "my_background.png"
    _write_image(keep)
    banner = env.dir / "user_data" / "tournament_banner.png"
    _write_image(banner)

    env.service.ImportBackground(_image_bytes())
    uploaded = env.dir / env.config()["background_path"]
    assert uploaded.exists()
    env.service.Reset()

    assert not uploaded.exists()
    assert keep.exists()
    assert banner.exists()


@pytest.mark.parametrize("data", [b"", b"not an image",
                                  _image_bytes(fmt="GIF")])
def test_what_is_not_an_image_changes_nothing(env, data):
    kept = {"background_source": "banner"}
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(kept)}

    assert env.service.ImportBackground(data) == thumbnails.INVALID_IMAGE

    assert env.config() == kept
    assert list((env.dir / "user_data").glob("thumbnail_background_*")) == []


def test_a_background_that_cannot_be_saved_changes_nothing(env, monkeypatch):
    kept = {"background_source": "banner"}
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(kept)}

    def fail(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(ImageImport, "WriteFile", fail)

    assert env.service.ImportBackground(_image_bytes()) \
        == thumbnails.SAVE_FAILED
    assert env.config() == kept


def test_what_a_crash_left_goes_at_construction(env):
    leftovers = [
        env.dir / "tmp" / "thumbnail" / ".preview.abc.tmp",
        env.dir / "out" / "thumbnails" / ".A-vs-B-1.xyz.tmp",
        env.dir / "user_data" / ".thumbnail_background_12.xyz.tmp",
    ]
    others = [
        env.dir / "user_data" / ".settings.xyz.tmp",
        env.dir / "tmp" / "thumbnail" / "template.png",
        env.dir / "out" / "thumbnails" / "A-vs-B-1.png",
        env.dir / "user_data" / "thumbnail_background_12.png",
    ]
    for path in [*leftovers, *others]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    ThumbnailService(later=env.queued.append)

    assert [path for path in leftovers if path.exists()] == []
    assert [path for path in others if not path.exists()] == []


def test_the_last_sessions_preview_goes_at_construction(env):
    # The headless shell and a crash leave ./tmp behind, and its image is not
    # of this session: GET /thumbnail/preview has nothing to show yet.
    preview = env.dir / "tmp" / "thumbnail" / "preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"the last session's")

    service = ThumbnailService(later=env.queued.append)

    assert not preview.exists()
    assert service.PreviewImage() is None


def test_a_preview_that_cannot_be_removed_does_not_stop_the_launch(
        env, monkeypatch):
    def fail(_path, missing_ok=False):
        raise OSError("in use")

    monkeypatch.setattr(Path, "unlink", fail)

    ThumbnailService(later=env.queued.append)


# ── the output's names and generations (an external contract) ────────────

def test_the_file_name_is_the_tags_and_the_time_in_utc():
    data = {"score": {"2": {"team": {
        "1": {"player": {"1": {"name": 'A<l>p:h"a/\\|?* X'}}},
        "2": {"player": {"1": {"name": "Bravo"}}}}}}}
    moment = datetime.datetime(2026, 11, 7, 1, 2, 3,
                               tzinfo=datetime.timezone.utc)

    assert thumbnails.OutputStem(data, 2, now=moment) \
        == "AlphaX-vs-Bravo-2026-11-07-01-02-03"
    assert thumbnails.OutputStem({}, 1, now=moment) \
        == "-vs--2026-11-07-01-02-03"


def test_the_stem_uses_the_current_time_in_utc(monkeypatch):
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    stem = thumbnails.OutputStem({})
    after = datetime.datetime.now(datetime.timezone.utc)

    stamped = datetime.datetime.strptime(
        stem, "-vs--%Y-%m-%d-%H-%M-%S").replace(tzinfo=datetime.timezone.utc)
    assert before <= stamped <= after


def test_a_generated_thumbnail_is_a_png_and_a_jpg_in_out_thumbnails(
        match, executor):
    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)

    png = sorted((match.dir / "out" / "thumbnails").glob("*.png"))
    jpg = sorted((match.dir / "out" / "thumbnails").glob("*.jpg"))
    assert len(png) == len(jpg) == 1
    assert png[0].read_bytes() == b"an image.PNG"
    assert jpg[0].read_bytes() == b"an image.JPG"
    assert png[0].stem == jpg[0].stem
    assert png[0].stem.startswith("Alpha-vs-Bravo-2")
    assert match.render()["path"] == f"out/thumbnails/{png[0].name}"
    # The preview's too.
    assert match.service.PreviewImage() == b"an image.PNG"


def _Encoder(asked):
    """A render's `encode` that says which encoding it gave."""
    def encode(file_format, fast=False):
        asked.append((file_format, fast))
        return (b"fast." if fast else b"default.") + file_format.encode()
    return encode


def test_a_preview_is_the_fast_png_and_a_generation_the_default_encodings(
        match, executor):
    """ADR-005 S4o judgment 23: a busy picture's PNG at the default
    compression took most of a preview's time, so a preview asks the shell
    for the fast one. A generation does not: out/thumbnails, and the preview
    file it writes too, keep the encodings they had."""
    asked = []
    match.service.Preview()
    match.renderer.asked.pop().done(_Encoder(asked), "")
    executor.drain(WAIT)

    assert match.render()["status"] == "done"
    assert asked == [("PNG", True)]
    assert match.service.PreviewImage() == b"fast.PNG"

    asked.clear()
    match.service.Generate()
    match.renderer.asked.pop().done(_Encoder(asked), "")
    executor.drain(WAIT)

    assert match.render()["status"] == "done"
    assert asked == [("PNG", False), ("JPG", False)]
    directory = match.dir / "out" / "thumbnails"
    assert [p.read_bytes() for p in directory.glob("*.png")] \
        == [b"default.PNG"]
    assert [p.read_bytes() for p in directory.glob("*.jpg")] \
        == [b"default.JPG"]
    assert match.service.PreviewImage() == b"default.PNG"


@pytest.mark.parametrize("kind, line", [
    ("preview", r"Thumbnail preview: encoded in \d+ ms \(PNG fast 8 bytes "
                r"in \d+ ms\), written in \d+ ms"),
    ("generate", r"Thumbnail generate: encoded in \d+ ms \(PNG 11 bytes in "
                 r"\d+ ms, JPG 11 bytes in \d+ ms\), written in \d+ ms"),
])
def test_a_save_logs_its_encoding_and_writing_times(
        match, executor, kind, line):
    """The debug line that splits a run's time after the render between the
    encoding and the files (judgment 23's measurement)."""
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)),
                      level="DEBUG", format="{message}")
    try:
        (match.service.Preview if kind == "preview"
         else match.service.Generate)()
        match.renderer.asked.pop().done(_Encoder([]), "")
        executor.drain(WAIT)
    finally:
        logger.remove(sink)

    assert any(re.fullmatch(line, text.strip()) for text in logged), logged


def test_thirty_generations_are_kept(match, executor):
    directory = match.dir / "out" / "thumbnails"
    directory.mkdir(parents=True)
    for index in range(35):
        for extension in ("png", "jpg"):
            path = directory / f"old-{index:02}.{extension}"
            path.write_bytes(b"x")
            os.utime(path, (1000 + index, 1000 + index))
    other = directory / "notes.txt"
    other.write_bytes(b"x")

    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)

    stems = {path.stem for path in directory.glob("*.png")}
    assert len(stems) == 30
    assert {path.stem for path in directory.glob("*.jpg")} == stems
    assert match.render()["path"].split("/")[-1][:-4] in stems
    assert "old-34" in stems and "old-05" not in stems
    assert other.exists()


def test_the_legacy_save_keeps_its_names_and_generations(
        env, autonaApp):
    from qtpy.QtGui import QPixmap
    from src.thumbnail import main_generate_thumbnail as legacy

    directory = env.dir / "out" / "thumbnails"
    directory.mkdir(parents=True)
    for index in range(31):
        path = directory / f"old-{index:02}.png"
        path.write_bytes(b"x")
        os.utime(path, (1000 + index, 1000 + index))
    pixmap = QPixmap(16, 9)
    pixmap.fill()

    saved = legacy.save_result(pixmap, PLAYERS, {})

    assert saved.startswith("./out/thumbnails/Alpha-vs-Bravo-")
    assert saved.endswith(".png")
    name = Path(saved).name
    assert (directory / name).exists()
    assert Image.open(directory / name).format == "PNG"
    jpg = directory / name.replace(".png", ".jpg")
    assert Image.open(jpg).format == "JPEG"
    assert len(list(directory.glob("*.png"))) == 30


def test_the_legacy_entry_point_goes_through_the_service(env):
    from src.core.SettingsManager import SettingsManager as settings
    from src.thumbnail import main_generate_thumbnail as legacy

    SM.state = copy.deepcopy(PLAYERS)

    assert legacy.prepare_data(settings, scoreboardNumber=2) \
        == env.service.RenderPayload(board=2)
    with pytest.raises(thumbnails.ThumbnailDataMissing):
        SM.state = {}
        (env.dir / "out" / "program_state.json").write_text("{}")
        legacy.prepare_data(settings)


def test_the_synchronous_preview_is_gone():
    # ADR-005 S4ac: nothing drew the Qt panel's tmp/thumbnail/template.png
    # after S4p; the preview is core's (Preview / PreviewImage).
    import inspect
    from src.thumbnail import main_generate_thumbnail as legacy

    for function in (legacy.generate, legacy.save_result):
        assert "isPreview" not in inspect.signature(function).parameters
    assert "AutonaResolve" not in inspect.getsource(legacy)


# ── drawn at once: DrawBoard (ADR-005 S4y) ───────────────────────────────
# SCORE's X post and the desktop's legacy get-thumbnail draw synchronously
# through the shell (AutonaShellOperations.DrawThumbnailNow is `draw`); the
# files are this service's. The shell's half is tests/test_thumbnail_render.py.

def _drawn(_payload):
    return lambda file_format, fast=False: b"drawn." + file_format.encode()


def test_a_board_drawn_at_once_is_saved_as_png_and_jpg(match):
    asked = []

    def draw(payload):
        asked.append(payload)
        return _drawn(payload)

    path = match.service.DrawBoard(1, draw)

    name = Path(path).name
    assert path == f"./out/thumbnails/{name}"
    assert name.startswith("Alpha-vs-Bravo-")
    directory = match.dir / "out" / "thumbnails"
    assert (directory / name).read_bytes() == b"drawn.PNG"
    assert (directory / name).with_suffix(".jpg").read_bytes() == b"drawn.JPG"
    assert len(asked) == 1
    # Not a run of the lane: thumbnail.render is not written.
    assert match.render() is None


@pytest.mark.parametrize("failing", ["png", "jpg"])
def test_a_board_drawn_at_once_that_cannot_be_saved_raises(
        match, monkeypatch, failing):
    """OSError to the caller (the legacy route's 500, the X post's message):
    QPixmap.save() said nothing and the path was returned until S4y. A .png
    written before its .jpg failed stays where it is, and nothing is
    pruned."""
    write = ImageImport.WriteFile

    def fail(path, data):
        if str(path).endswith(f".{failing}"):
            raise OSError("disk full")
        write(path, data)

    pruned = []
    monkeypatch.setattr(ImageImport, "WriteFile", fail)
    monkeypatch.setattr(match.service, "_prune",
                        lambda *args, **kwargs: pruned.append(args))

    with pytest.raises(OSError):
        match.service.DrawBoard(1, _drawn)

    left = sorted(path.suffix
                  for path in (match.dir / "out").glob("thumbnails/*"))
    assert left == ([".png"] if failing == "jpg" else [])
    assert pruned == []


def test_a_board_drawn_at_once_keeps_thirty_and_never_the_one_it_saved(match):
    """The file just written is protected: thirty newer ones (a clock that
    went back) do not push it out, older ones go."""
    import time

    directory = match.dir / "out" / "thumbnails"
    directory.mkdir(parents=True)
    later = time.time() + 3600
    for index in range(30):
        for extension in ("png", "jpg"):
            path = directory / f"newer-{index:02}.{extension}"
            path.write_bytes(b"x")
            os.utime(path, (later + index, later + index))
    old = directory / "old.png"
    old.write_bytes(b"x")
    os.utime(old, (1000, 1000))

    saved = Path(match.service.DrawBoard(1, _drawn)).stem

    stems = {path.stem for path in directory.glob("*.png")}
    assert saved in stems and (directory / f"{saved}.jpg").exists()
    assert len(stems) == 31 and "old" not in stems


def test_a_board_drawn_at_once_with_nothing_drawn_writes_nothing(match):
    def refuse(_payload):
        raise RuntimeError("the page did not load")

    with pytest.raises(RuntimeError):
        match.service.DrawBoard(1, refuse)
    with pytest.raises(RuntimeError):
        match.service.DrawBoard(
            1, lambda _payload: lambda file_format, fast=False: b"")
    SM.state = {}
    (match.dir / "out" / "program_state.json").write_text("{}")
    with pytest.raises(thumbnails.ThumbnailDataMissing):
        match.service.DrawBoard(1, refuse)

    assert list((match.dir / "out").glob("thumbnails/*")) == []


# ── the state's key ──────────────────────────────────────────────────────

def test_the_render_key_is_in_the_catalog():
    assert schema.KEY_CATALOG[schema.THUMBNAIL_ROOT] == (
        "render.status", "render.path", "render.error", "render.revision")


def test_the_launch_seeds_idle_and_the_revision_goes_on(env):
    SM.Set(thumbnails.RENDER_PATH, {
        "status": "running", "path": "out/thumbnails/x.png",
        "error": "e", "revision": 7})

    schema.SeedPanelState()

    assert env.render() == {"status": "idle", "path": "", "error": "",
                            "revision": 7}


@pytest.mark.parametrize("saved, revision", [
    (None, 0), ("x", 0), ({}, 0), ({"revision": "1"}, 0),
    ({"revision": True}, 0), ({"revision": -1}, 0), ({"revision": 4}, 4)])
def test_a_malformed_render_reads_as_idle(env, saved, revision):
    SM.state = {"thumbnail": {"render": saved}}

    assert schema.ReadThumbnailRender() == {
        "status": "idle", "path": "", "error": "", "revision": revision}


# ── a render in the background ───────────────────────────────────────────

def test_a_preview_runs_and_ends_in_the_state(match, executor):
    answer = match.service.Preview()

    assert answer == 1
    assert match.render() == {"status": "running", "path": "", "error": "",
                              "revision": 1}
    asked = match.renderer.asked[0]
    assert asked.kind == "thumbnail"
    data, config, template = asked.payload
    assert data["game"]["codename"] == "tgame"
    assert template["template_id"] == "type_a"
    assert config["scoreboardNumber"] == 1

    match.renderer.answer()
    # The render is over; the files are the job's.
    assert match.render()["status"] == "running"
    assert match.service.PreviewImage() is None
    executor.drain(WAIT)

    assert match.render() == {"status": "done", "path": "", "error": "",
                              "revision": 1}
    assert match.service.PreviewImage() == b"an image.PNG"
    assert list((match.dir / "out").glob("thumbnails/*")) == []


def test_every_run_has_the_next_revision_and_a_preview_keeps_the_path(
        match, executor):
    assert match.service.Generate() == 1
    match.renderer.answer()
    executor.drain(WAIT)
    saved = match.render()["path"]
    assert saved.startswith("out/thumbnails/")

    assert match.service.Preview() == 2
    assert match.render() == {"status": "running", "path": saved, "error": "",
                              "revision": 2}
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render() == {"status": "done", "path": saved, "error": "",
                              "revision": 2}


def test_the_revision_goes_on_from_the_state(match, executor):
    SM.Set(thumbnails.RENDER_PATH, schema.DefaultThumbnailRender(41))

    assert match.service.Preview() == 42


def test_previews_during_a_run_are_one_more_run_with_the_newest_settings(
        match, executor):
    assert match.service.Preview() == 1
    SettingsManager.settings = {"thumbnail_config": {"template_id": "type_b"}}
    assert match.service.Preview() == 2
    SettingsManager.settings = {"thumbnail_config": {"display_phase": False}}
    assert match.service.Preview() == 2

    assert len(match.renderer.asked) == 1
    match.renderer.answer()
    executor.drain(WAIT)
    assert match.render()["revision"] == 1
    assert match.render()["status"] == "done"
    assert match.renderer.asked == []

    # Not right away: on the main thread, once its event loop is back.
    assert match.run_queued() == 1
    assert match.render() == {"status": "running", "path": "", "error": "",
                              "revision": 2}
    assert len(match.renderer.asked) == 1
    assert match.renderer.asked[0].payload[1]["display_phase"] is False

    match.renderer.answer(b"newer")
    executor.drain(WAIT)
    assert match.render()["status"] == "done"
    assert match.render()["revision"] == 2
    assert match.service.PreviewImage() == b"newer.PNG"
    assert match.queued == []


def test_a_generate_during_a_preview_runs_next(match, executor):
    assert match.service.Preview() == 1

    assert match.service.Generate() == 2
    # And only one is kept.
    assert match.service.Generate() == thumbnails.BUSY

    match.renderer.answer()
    executor.drain(WAIT)
    assert match.run_queued() == 1
    match.renderer.answer(b"saved")
    executor.drain(WAIT)

    assert match.render()["status"] == "done"
    assert match.render()["revision"] == 2
    assert match.render()["path"].startswith("out/thumbnails/")
    assert match.service.PreviewImage() == b"saved.PNG"


def test_a_generate_while_one_runs_is_busy(match, executor):
    assert match.service.Generate() == 1
    assert match.service.Generate() == thumbnails.BUSY

    assert match.render()["revision"] == 1
    assert len(match.renderer.asked) == 1
    match.renderer.answer()
    executor.drain(WAIT)

    # Over: the next one is taken.
    assert match.service.Generate() == 2


def test_a_preview_during_a_generate_runs_after_it(match, executor):
    assert match.service.Generate() == 1
    assert match.service.Preview() == 2

    match.renderer.answer()
    executor.drain(WAIT)
    saved = match.render()["path"]
    assert match.run_queued() == 1
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render() == {"status": "done", "path": saved, "error": "",
                              "revision": 2}


def test_a_kept_generate_stands_in_for_a_kept_preview(match, executor):
    # It draws the same picture and updates the preview's file too.
    assert match.service.Preview() == 1
    assert match.service.Preview() == 2
    assert match.service.Generate() == 2
    assert match.service.Preview() == 2

    match.renderer.answer()
    executor.drain(WAIT)
    assert match.run_queued() == 1
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.queued == []
    assert match.renderer.asked == []
    assert match.render()["path"].startswith("out/thumbnails/")


# ── a generate of board 2 (ADR-005 S4z) ──────────────────────────────────
# v1's POST /thumbnail:generate takes a `board`: the run draws that board's
# match and names the files after it. A preview draws board 1, whatever.

def _drawnBoard(asked):
    return asked.payload[1]["scoreboardNumber"]


def _saved(env):
    return [path.stem for path in
            sorted((env.dir / "out" / "thumbnails").glob("*.png"))]


def test_a_generate_of_board_2_draws_and_names_board_2(match, executor):
    assert match.service.Generate(2) == 1
    assert _drawnBoard(match.renderer.asked[0]) == 2
    match.renderer.answer()
    executor.drain(WAIT)

    [stem] = _saved(match)
    assert stem.startswith("Charlie-vs-Echo-2")
    assert match.render()["path"] == f"out/thumbnails/{stem}.png"
    # The preview image is board 1's: a generate of board 2 does not write it
    # (S4z review).
    assert match.service.PreviewImage() is None

    # The next one without a board is board 1 again.
    assert match.service.Generate() == 2
    assert _drawnBoard(match.renderer.asked[0]) == 1


def test_a_kept_generate_of_board_2_draws_board_2_after_the_preview(
        match, executor):
    assert match.service.Preview() == 1
    assert match.service.Generate(2) == 2
    assert _drawnBoard(match.renderer.asked[0]) == 1
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 2
    match.renderer.answer()
    executor.drain(WAIT)
    [stem] = _saved(match)
    assert stem.startswith("Charlie-vs-Echo-2")


def test_a_preview_after_a_generate_of_board_2_draws_board_1(match, executor):
    assert match.service.Generate(2) == 1
    assert match.service.Preview() == 2
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 1


def test_a_busy_generate_does_not_change_the_board_of_the_one_on_its_way(
        match, executor):
    assert match.service.Generate(1) == 1
    assert match.service.Generate(2) == thumbnails.BUSY
    match.renderer.answer()
    executor.drain(WAIT)

    [stem] = _saved(match)
    assert stem.startswith("Alpha-vs-Bravo-2")


def test_a_generate_of_board_2_leaves_the_preview_image_as_it_was(
        match, executor):
    assert match.service.Preview() == 1
    match.renderer.answer(b"board one")
    executor.drain(WAIT)
    assert match.service.PreviewImage() == b"board one.PNG"

    assert match.service.Generate(2) == 2
    match.renderer.answer(b"board two")
    executor.drain(WAIT)

    [stem] = _saved(match)
    assert (match.dir / "out" / "thumbnails" / f"{stem}.png").read_bytes() \
        == b"board two.PNG"
    assert match.render() == {"status": "done", "error": "", "revision": 2,
                              "path": f"out/thumbnails/{stem}.png"}
    assert match.service.PreviewImage() == b"board one.PNG"
    assert match.queued == []


@pytest.mark.parametrize("board", [(), (1,)], ids=["no board", "board 1"])
def test_a_kept_generate_of_board_1_stands_in_for_the_preview(
        match, executor, board):
    # The lane's rule, unchanged for board 1 (with v1's `board: 1` too): the
    # kept generate draws the picture the kept preview would have, writes
    # the preview and answers it; nothing is drawn after it.
    assert match.service.Preview() == 1
    assert match.service.Preview() == 2
    assert match.service.Generate(*board) == 2
    assert match.service.Preview() == 2
    match.renderer.answer(b"first")
    executor.drain(WAIT)

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 1
    match.renderer.answer(b"board one")
    executor.drain(WAIT)
    assert match.queued == [] and match.renderer.asked == []
    assert match.render()["revision"] == 2
    assert match.service.PreviewImage() == b"board one.PNG"


def test_a_kept_preview_is_drawn_after_a_generate_of_board_2_as_board_1(
        match, executor):
    # S4z review: a generate of board 2 does not stand in for a kept preview
    # -- no preview is answered with another board's picture. The preview
    # (answered 2, before the generate came) is drawn after the generate,
    # as board 1, under the next revision: as a preview owed after a
    # stand-in that found no match is.
    assert match.service.Preview() == 1
    assert match.service.Preview() == 2
    assert match.service.Generate(2) == 2
    match.renderer.answer(b"first")
    executor.drain(WAIT)
    assert match.service.PreviewImage() == b"first.PNG"

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 2
    match.renderer.answer(b"board two")
    executor.drain(WAIT)
    [stem] = _saved(match)
    assert stem.startswith("Charlie-vs-Echo-2")
    assert match.render() == {"status": "done", "error": "", "revision": 2,
                              "path": f"out/thumbnails/{stem}.png"}
    # What GET /thumbnail/preview reads: still board 1's.
    assert match.service.PreviewImage() == b"first.PNG"

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 1
    assert match.render()["status"] == "running"
    assert match.render()["revision"] == 3
    match.renderer.answer(b"board one")
    executor.drain(WAIT)
    assert match.render() == {"status": "done", "error": "", "revision": 3,
                              "path": f"out/thumbnails/{stem}.png"}
    assert match.service.PreviewImage() == b"board one.PNG"
    assert match.queued == [] and match.renderer.asked == []


def test_a_preview_asked_after_a_kept_generate_of_board_2_is_drawn_after_it(
        match, executor):
    assert match.service.Preview() == 1
    assert match.service.Generate(2) == 2
    # The revision of the run that draws it: the one after the generate's.
    assert match.service.Preview() == 3
    assert match.service.Preview() == 3
    match.renderer.answer(b"first")
    executor.drain(WAIT)

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 2
    match.renderer.answer(b"board two")
    executor.drain(WAIT)
    assert match.service.PreviewImage() == b"first.PNG"

    assert match.run_queued() == 1
    assert _drawnBoard(match.renderer.asked[0]) == 1
    match.renderer.answer(b"board one")
    executor.drain(WAIT)
    assert match.render()["revision"] == 3
    assert match.service.PreviewImage() == b"board one.PNG"
    assert match.queued == [] and match.renderer.asked == []


def _no_players(env, monkeypatch):
    """A game and no players, in the state and the exported file: a
    generate has no match to save, a preview draws a placeholder."""
    from src.core.GameAssetCatalog import GameAssetCatalog
    monkeypatch.setattr(GameAssetCatalog, "instance", _Catalog(world.GAMES))
    program = {"game": {"codename": "tgame"}}
    SM.state = copy.deepcopy(program)
    (env.dir / "out" / "program_state.json").write_text(json.dumps(program))


@pytest.mark.parametrize("board", [(), (1,)], ids=["no board", "board 1"])
@pytest.mark.parametrize("kept", [("generate", "preview"),
                                  ("preview", "generate")])
def test_a_generate_with_no_match_still_draws_the_preview_it_stood_in_for(
        env, executor, monkeypatch, kept, board):
    # S4p review: the preview asked for was answered with the generate's
    # revision; it is not lost with the generate's missing match. Board 1
    # with v1's `board: 1` too (S4z review), and the preview is board 1's.
    _no_players(env, monkeypatch)
    assert env.service.Preview() == 1
    for kind in kept:
        args = board if kind == "generate" else ()
        assert getattr(env.service, kind.capitalize())(*args) == 2

    env.renderer.answer()
    executor.drain(WAIT)
    env.queued.pop(0)()  # the generate: no match, nothing drawn

    assert env.renderer.asked == []
    assert len(env.queued) == 1
    assert env.render() == {"status": "error", "path": "",
                            "error": thumbnails.NO_PLAYER, "revision": 2}

    env.queued.pop(0)()  # the preview

    assert env.render() == {"status": "running", "path": "", "error": "",
                            "revision": 3}
    assert _drawnBoard(env.renderer.asked[0]) == 1
    data, _config, _template = env.renderer.asked[0].payload
    assert data["score"]["1"]["match"] == "Winners Final"  # a placeholder
    env.renderer.answer(b"placeholder")
    executor.drain(WAIT)
    assert env.render()["status"] == "done"
    assert env.service.PreviewImage() == b"placeholder.PNG"
    assert env.queued == []


@pytest.mark.parametrize("kept, answers", [
    (("generate", "preview"), (2, 3)), (("preview", "generate"), (2, 2))])
def test_a_generate_of_board_2_with_no_match_is_followed_by_the_kept_preview(
        env, executor, monkeypatch, kept, answers):
    # Not a stand-in (S4z review): the preview kept beside it is drawn after
    # it whatever it ends with, as board 1.
    _no_players(env, monkeypatch)
    assert env.service.Preview() == 1
    for kind, answer in zip(kept, answers):
        args = (2,) if kind == "generate" else ()
        assert getattr(env.service, kind.capitalize())(*args) == answer

    env.renderer.answer()
    executor.drain(WAIT)
    env.queued.pop(0)()  # the generate: no match, nothing drawn

    assert env.renderer.asked == []
    assert env.render()["error"] == thumbnails.NO_PLAYER
    assert env.render()["revision"] == 2

    env.queued.pop(0)()  # the preview
    assert env.render()["revision"] == 3
    assert _drawnBoard(env.renderer.asked[0]) == 1
    env.renderer.answer(b"placeholder")
    executor.drain(WAIT)
    assert env.service.PreviewImage() == b"placeholder.PNG"
    assert env.queued == []


def test_a_generate_that_stood_in_for_no_preview_is_not_followed_by_one(
        env, executor, monkeypatch):
    _no_players(env, monkeypatch)
    assert env.service.Preview() == 1
    assert env.service.Generate() == 2

    env.renderer.answer()
    executor.drain(WAIT)
    env.queued.pop(0)()

    assert env.render()["error"] == thumbnails.NO_PLAYER
    assert env.queued == [] and env.renderer.asked == []
    assert env.service._running is None


def test_a_generate_that_stood_in_for_a_preview_and_failed_to_draw_is_not_redrawn(
        match, executor):
    # The preview would draw the same picture: only a missing match, which a
    # preview draws as a placeholder, is drawn again.
    assert match.service.Preview() == 1
    assert match.service.Generate() == 2
    assert match.service.Preview() == 2
    match.renderer.answer()
    executor.drain(WAIT)
    assert match.run_queued() == 1

    match.renderer.fail()

    assert match.render()["error"] == thumbnails.RENDER_FAILED
    assert match.queued == []
    assert match.service._running is None


def test_what_is_drawn_has_what_the_settings_say_of_a_missing_value(
        match, executor):
    # thumbnail.js reads these two as false when they are not saved; GET says
    # true, as the panel did, which saved them when opened.
    match.service.Preview()

    _data, config, _template = match.renderer.asked[0].payload
    assert config["use_team_names"] is True
    assert config["game"]["tgame"]["flip_p2"] is True
    assert config["game"]["tgame"]["zoom"] == 100
    assert config["game"]["tgame"]["align"] == {"horizontal": 50,
                                                "vertical": 40}
    # Nothing of it was saved (a render writes no setting).
    assert match.config() is None


def test_what_is_saved_is_what_is_drawn(match, executor):
    SettingsManager.settings = {"thumbnail_config": {
        "use_team_names": False, "template_id": "type_b",
        "game": {"tgame": {"flip_p2": False, "zoom": 150}}}}

    match.service.Preview()

    _data, config, template = match.renderer.asked[0].payload
    assert template["template_id"] == "type_b"
    assert config["use_team_names"] is False
    assert config["game"]["tgame"]["flip_p2"] is False
    assert config["game"]["tgame"]["zoom"] == 150


@pytest.mark.parametrize("saved", [
    {},
    {"use_team_names": 0, "display_phase": 0, "use_sponsors": "no"},
    {"use_team_names": False, "display_phase": False, "use_sponsors": False},
    {"game": "not a dict"},
    {"game": {"tgame": "not a dict"}},
    {"game": {"tgame": {"zoom": 150.0, "flip_p1": 1, "flip_p2": 0}}},
    {"game": {"tgame": {"zoom": 0, "align": 50}}},
    {"game": {"tgame": {"align": {"horizontal": 0, "vertical": 0}}}},
    {"game": {"tgame": {"align": {"horizontal": 900, "vertical": "10"}}}},
    {"game": {"tgame": {"zoom": 150, "flip_p1": True,
                        "align": {"horizontal": 10}}}},
])
def test_what_get_says_is_what_is_drawn(match, executor, saved):
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(saved)}
    settings = match.service.Settings()

    match.service.Preview()

    _data, config, _template = match.renderer.asked[0].payload
    for key in thumbnails.DEFAULT_TOGGLES:
        assert config[key] is settings[key], key
    drawn = config["game"]["tgame"]
    for key, value in settings["game"]["tgame"].items():
        assert drawn[key] == value and type(drawn[key]) is type(value), key


def test_the_rest_of_a_games_settings_stays_in_what_is_drawn(match, executor):
    # The template and the placeholder read asset_pack; GET does not tell it.
    SettingsManager.settings = {"thumbnail_config": {
        "game": {"tgame": {"zoom": 150.0, "asset_pack": "costume"},
                 "other": {"zoom": 150.0}}}}

    match.service.Preview()

    _data, config, _template = match.renderer.asked[0].payload
    assert config["game"]["tgame"]["asset_pack"] == "costume"
    assert config["game"]["tgame"]["zoom"] == 100
    # What is not drawn is left alone.
    assert config["game"]["other"] == {"zoom": 150.0}


@pytest.mark.parametrize("saved", [
    {"background_source": "nope", "background_path": "C:/x.png"},
    {"background_source": ["custom"], "background_path": "C:/x.png"},
    {"background_source": "custom", "background_path": 5},
    {"background_source": "default", "background_path": "C:/x.png"},
])
def test_a_background_get_says_is_none_is_none_in_what_is_drawn(
        match, executor, saved):
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(saved)}

    match.service.Preview()

    assert match.renderer.asked[0].payload[1]["background_path"] is None


def test_what_is_saved_is_not_changed_by_what_is_drawn(match, executor):
    saved = {"background_source": "default", "use_team_names": 0,
             "game": {"tgame": {"zoom": 150.0,
                                "align": {"horizontal": 0}}}}
    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(saved)}

    match.service.Preview()

    assert match.config() == saved


def test_the_payload_itself_stays_raw(match):
    # BuildPayload is what the golden pins (the old code's output); the read
    # is RenderPayload's, on top of it.
    SettingsManager.settings = {"thumbnail_config": {
        "use_team_names": 0,
        "game": {"tgame": {"zoom": 150.0, "align": {"horizontal": 0}}}}}

    _data, config, _template = match.service.BuildPayload()

    assert config["use_team_names"] == 0
    assert config["game"]["tgame"] == {"zoom": 150.0,
                                       "align": {"horizontal": 0}}


@pytest.mark.parametrize("saved", [
    {},
    {"use_team_names": 0, "display_phase": 0, "use_sponsors": "no"},
    {"use_team_names": False, "template_id": "type_b",
     "game": {"tgame": {"flip_p2": False, "zoom": 150}}},
    {"game": {"tgame": {"zoom": 150.0, "flip_p1": 1,
                        "align": {"horizontal": 0, "vertical": 900}}}},
])
def test_scores_sync_way_draws_what_get_says_too(match, saved):
    """ADR-005 S4p (S4o judgment 6, carried over): SCORE's button, its X post
    and the legacy route render synchronously through main_generate_thumbnail
    and are given the settings as read, as the v1 runs are, so the value on
    the page is the picture at every entry. With nothing saved that is the
    page's defaults (team names on, P2 flipped, 100 %, 50 / 40), not what
    thumbnail.js makes of a missing value; a saved value is drawn as saved."""
    from src.thumbnail import main_generate_thumbnail as legacy

    SettingsManager.settings = {"thumbnail_config": copy.deepcopy(saved)}
    settings = match.service.Settings()

    _data, config, template = legacy.prepare_data(SettingsManager)

    assert template["template_id"] == settings["template_id"]
    for key in thumbnails.DEFAULT_TOGGLES:
        assert config[key] is settings[key], key
    drawn = config["game"]["tgame"]
    for key, value in settings["game"]["tgame"].items():
        assert drawn[key] == value and type(drawn[key]) is type(value), key
    if not saved:
        assert config["use_team_names"] is True
        assert drawn["flip_p2"] is True
        assert (drawn["zoom"], drawn["align"]) \
            == (100, {"horizontal": 50, "vertical": 40})


def test_scores_sync_way_moves_an_old_background_to_the_selector(match):
    # What GET says of a config from before the selector is what SCORE draws:
    # the move the Qt panel made when it was built is the render's now, on
    # what it draws (settings.json is not written).
    SettingsManager.settings = {"thumbnail_config": {
        "background_path": "C:/x.png"}}
    from src.thumbnail import main_generate_thumbnail as legacy

    _data, config, _template = legacy.prepare_data(SettingsManager)

    assert match.service.Settings()["background_source"] == "custom"
    assert config["background_source"] == "custom"
    assert config["background_path"] == "C:/x.png"
    assert match.config() == {"background_path": "C:/x.png"}


def _unsaveable(monkeypatch):
    def refuse():
        raise OSError("settings.json is locked")
    monkeypatch.setattr(SettingsManager, "SaveSettings", refuse)


def test_scores_sync_way_draws_though_the_settings_cannot_be_saved(
        match, monkeypatch):
    # SCORE's button, its X post and the legacy route (S4p review): a render
    # writes no setting, so a settings.json that cannot be written does not
    # stop one -- an old config included, which the render once saved.
    from src.thumbnail import main_generate_thumbnail as legacy
    SettingsManager.settings = {"thumbnail_config": {
        "background_path": "C:/x.png"}}
    _unsaveable(monkeypatch)

    _data, config, template = legacy.prepare_data(SettingsManager)

    assert config["background_path"] == "C:/x.png"
    assert template["template_id"] == "type_a"


def test_a_run_draws_though_the_settings_cannot_be_saved(
        match, executor, monkeypatch):
    SettingsManager.settings = {"thumbnail_config": {
        "background_path": "C:/x.png"}}
    _unsaveable(monkeypatch)

    assert match.service.Preview() == 1

    assert match.render()["status"] == "running"
    assert match.renderer.asked[0].payload[1]["background_path"] == "C:/x.png"


def test_a_preview_without_a_match_draws_a_placeholder(env, executor,
                                                       monkeypatch):
    from src.core.GameAssetCatalog import GameAssetCatalog
    monkeypatch.setattr(GameAssetCatalog, "instance",
                        _Catalog(world.GAMES))

    env.service.Preview()

    data, config, _template = env.renderer.asked[0].payload
    assert data["game"]["codename"] == "tgame"
    assert data["score"]["1"]["match"] == "Winners Final"
    assert config["game"]["tgame"]["flip_p2"] is True


@pytest.mark.parametrize("program, error", [
    ({}, thumbnails.NO_GAME),
    ({"game": {"codename": "tgame"}}, thumbnails.NO_PLAYER),
])
def test_a_generate_without_a_match_says_why(env, executor, program, error):
    # The state too: "running" is exported over the file, at any moment.
    SM.state = copy.deepcopy(program)
    (env.dir / "out" / "program_state.json").write_text(json.dumps(program))

    assert env.service.Generate() == 1

    assert env.renderer.asked == []
    assert env.render() == {"status": "error", "path": "", "error": error,
                            "revision": 1}
    # The lane is free.
    SM.state = copy.deepcopy(PLAYERS)
    assert env.service.Generate() == 2


@pytest.mark.parametrize("program", [None, "{not json", "[]"])
@pytest.mark.parametrize("state, error", [
    ({}, thumbnails.NO_GAME),
    ({"game": {"codename": "tgame"}}, thumbnails.NO_PLAYER),
])
def test_a_generate_with_no_match_says_why_whatever_the_file_is(
        env, executor, program, state, error):
    # Not what the exception said (a path of the operator's, or Python's words
    # for a JSON error, sent to every client).
    path = env.dir / "out" / "program_state.json"
    if SM._export_thread is not None:
        SM._export_thread.join()
    path.unlink(missing_ok=True)
    if program is not None:
        path.write_text(program)
    SM.state = copy.deepcopy(state)

    assert env.service.Generate() == 1

    assert env.renderer.asked == []
    assert env.render() == {"status": "error", "path": "", "error": error,
                            "revision": 1}
    assert env.service._running is None


def test_a_payload_that_cannot_be_built_says_so(env, executor):
    SM.state = copy.deepcopy(PLAYERS)
    os.remove(env.dir / "assets" / "thumbnail_base" / "thumbnail_types"
              / "type_a.json")

    env.service.Preview()

    assert env.render()["status"] == "error"
    assert env.render()["error"] == thumbnails.PAYLOAD_FAILED
    assert env.service.Preview() == 2


def test_a_missing_match_in_a_preview_is_still_a_placeholder(env, executor):
    # The file's being unreadable has not become a failure there.
    (env.dir / "out" / "program_state.json").unlink(missing_ok=True)

    env.service.Preview()

    assert env.render()["status"] == "running"
    assert len(env.renderer.asked) == 1


def test_a_failed_render_keeps_the_last_thumbnail_and_frees_the_lane(
        match, executor):
    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)
    saved = match.render()["path"]

    match.service.Preview()
    match.renderer.fail()

    assert match.render() == {"status": "error", "path": saved,
                              "error": thumbnails.RENDER_FAILED, "revision": 2}
    assert match.service.Preview() == 3


def test_a_failed_run_still_lets_what_was_kept_run(match, executor):
    match.service.Preview()
    match.service.Preview()

    match.renderer.fail()
    assert match.render()["status"] == "error"
    assert match.run_queued() == 1

    assert match.render()["status"] == "running"
    assert match.render()["revision"] == 2
    assert match.render()["error"] == ""


@pytest.mark.parametrize("image", [b""])
def test_an_image_that_will_not_encode_is_a_failed_render(
        match, executor, image):
    match.service.Generate()
    asked = match.renderer.asked.pop()
    asked.done(lambda _file_format, fast=False: image, "")
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.RENDER_FAILED
    assert list((match.dir / "out").glob("thumbnails/*")) == []


def test_a_file_that_cannot_be_written_is_a_save_failure(
        match, executor, monkeypatch):
    def fail(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(ImageImport, "WriteFile", fail)
    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.SAVE_FAILED
    assert match.service.Generate() == 2


def test_a_preview_that_cannot_be_written_is_a_save_failure(
        match, executor, monkeypatch):
    def fail(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(ImageImport, "WriteFile", fail)
    match.service.Preview()
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.SAVE_FAILED


def test_a_saved_thumbnail_is_done_though_the_preview_is_not_updated(
        match, executor, monkeypatch):
    # What was asked for is there: a client that says "failed" would have the
    # operator generate it again, and the file would be there twice.
    write = ImageImport.WriteFile

    def fail_for_the_preview(path, data):
        if Path(path) == Path(thumbnails.PREVIEW_PATH):
            raise OSError("it is open somewhere")
        write(path, data)

    monkeypatch.setattr(ImageImport, "WriteFile", fail_for_the_preview)
    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)

    png = sorted((match.dir / "out" / "thumbnails").glob("*.png"))
    assert len(png) == 1
    assert match.render() == {"status": "done",
                              "path": f"out/thumbnails/{png[0].name}",
                              "error": "", "revision": 1}
    assert png[0].with_suffix(".jpg").exists()
    assert match.service.PreviewImage() is None


def test_a_thumbnail_half_written_is_a_save_failure(
        match, executor, monkeypatch):
    # Its .jpg did not make it: not done, whatever the preview would do.
    write = ImageImport.WriteFile

    def fail_for_the_jpg(path, data):
        if str(path).endswith(".jpg"):
            raise OSError("disk full")
        write(path, data)

    monkeypatch.setattr(ImageImport, "WriteFile", fail_for_the_jpg)
    match.service.Generate()
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.SAVE_FAILED
    assert match.service.PreviewImage() is None


def test_an_encoder_that_raises_is_a_failed_render(match, executor):
    def boom(_file_format, fast=False):
        raise ValueError("no encoder")

    match.service.Preview()
    match.renderer.asked.pop().done(boom, "")
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.RENDER_FAILED


def test_a_job_that_fails_is_a_save_failure_without_its_text(
        match, executor, monkeypatch):
    def boom(_path, _data):
        raise ValueError("C:/Users/someone")

    monkeypatch.setattr(ImageImport, "WriteFile", boom)
    match.service.Preview()
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.SAVE_FAILED
    assert match.service.Preview() == 2


def test_a_job_that_cannot_be_submitted_is_a_save_failure_without_its_text(
        match, executor, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("C:/Users/someone")

    monkeypatch.setattr(executor, "submit", boom)
    match.service.Preview()
    match.renderer.answer()

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.SAVE_FAILED
    assert match.service.Preview() == 2


# ── a render that cannot start ───────────────────────────────────────────

def test_a_render_that_cannot_start_answers_the_request(match, executor):
    # Whoever asked hears why, and nothing is left running. It is not the
    # shutdown, so it is not called that.
    class Refused(Exception):
        pass

    match.renderer.refuse = Refused()

    with pytest.raises(Refused):
        match.service.Preview()

    assert match.render() == {"status": "error", "path": "",
                              "error": thumbnails.RENDER_FAILED, "revision": 1}
    match.renderer.refuse = None
    assert match.service.Preview() == 2


def test_a_render_refused_by_the_shutdown_is_cancelled(match, executor):
    # The request's 503.
    class Refused(Exception):
        pass

    match.renderer.refuse = Refused()
    executor.stopping.set()

    with pytest.raises(Refused):
        match.service.Preview()

    assert match.render() == {"status": "error", "path": "",
                              "error": thumbnails.CANCELLED, "revision": 1}


def test_the_shell_says_what_a_shutdown_is(match, executor):
    # bootstrap_core() injects whether the GUI thread will still run a call,
    # which the executor's stopping is only one of the reasons for.
    match.service._shuttingDown = lambda: True
    match.renderer.refuse = RuntimeError("no")

    with pytest.raises(RuntimeError):
        match.service.Preview()

    assert match.render()["error"] == thumbnails.CANCELLED


def test_what_is_not_an_exception_ends_the_run_too(match, executor):
    match.renderer.refuse = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        match.service.Preview()

    assert match.render()["error"] == thumbnails.RENDER_FAILED
    match.renderer.refuse = None
    assert match.service.Preview() == 2


def test_a_kept_run_that_cannot_start_only_logs(match, executor):
    match.service.Preview()
    match.service.Preview()
    match.renderer.answer()
    executor.drain(WAIT)

    match.renderer.refuse = RuntimeError("no")
    assert match.run_queued() == 1

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.RENDER_FAILED
    assert match.render()["revision"] == 2
    assert match.renderer.asked == []
    # Nothing is left running.
    match.renderer.refuse = None
    assert match.service.Preview() == 3


def test_a_kept_run_refused_by_the_shutdown_is_cancelled(match, executor):
    match.service.Preview()
    match.service.Preview()
    match.renderer.answer()
    executor.drain(WAIT)

    match.renderer.refuse = RuntimeError("no")
    match.service._shuttingDown = lambda: True
    assert match.run_queued() == 1

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.CANCELLED
    assert match.render()["revision"] == 2


@pytest.mark.parametrize("shuttingDown, error", [
    (False, thumbnails.RENDER_FAILED), (True, thumbnails.CANCELLED)])
def test_no_deferral_ends_the_kept_run(match, executor, shuttingDown, error):
    def no_loop(_fn):
        raise RuntimeError("no event loop")

    match.service._later = no_loop
    match.service._shuttingDown = lambda: shuttingDown
    match.service.Preview()
    match.service.Preview()
    match.renderer.answer()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == error
    assert match.render()["revision"] == 2
    assert match.service.Preview() == 3


# ── a run ends once ──────────────────────────────────────────────────────

@pytest.fixture
def published(match, monkeypatch):
    """Every (status, error, revision) written to thumbnail.render."""
    written = []
    publish = match.service._Publish

    def record(status, path, error, revision):
        written.append((status, error, revision))
        publish(status, path, error, revision)

    monkeypatch.setattr(match.service, "_Publish", record)
    return written


def _image(file_format, fast=False):
    return b"an image." + file_format.encode()


def test_a_renderer_that_answers_and_raises_ends_the_run_once(
        match, executor, published):
    def render(_kind, _payload, done):
        done(_image, "")
        raise RuntimeError("and then this")

    match.service._render = render

    with pytest.raises(RuntimeError):
        match.service.Preview()
    # What the answer started goes on.
    assert match.render()["status"] == "running"
    executor.drain(WAIT)

    assert published == [("running", "", 1), ("done", "", 1)]
    assert match.service.PreviewImage() == b"an image.PNG"


def test_a_renderer_that_raises_and_answers_ends_the_run_once(
        match, executor, published):
    answers = []

    def render(_kind, _payload, done):
        answers.append(done)
        raise RuntimeError("it did not start")

    match.service._render = render
    with pytest.raises(RuntimeError):
        match.service.Preview()

    answers[0](_image, "")
    executor.drain(WAIT)

    assert published == [("running", "", 1),
                         ("error", thumbnails.RENDER_FAILED, 1)]
    assert match.service.PreviewImage() is None


def test_a_renderer_that_answers_twice_ends_the_run_once(
        match, executor, published):
    match.service.Preview()
    # Kept for after it: moving the lane on twice would run it at once, and
    # start another on top of it.
    assert match.service.Preview() == 2
    done = match.renderer.asked.pop().done

    done(_image, "")
    done(None, "and again")
    executor.drain(WAIT)

    assert published == [("running", "", 1), ("done", "", 1)]
    assert len(match.queued) == 1


def test_a_second_end_of_a_run_changes_nothing(match, executor, published):
    assert match.service.Preview() == 1
    assert match.service.Preview() == 2
    match.renderer.answer()
    executor.drain(WAIT)
    assert match.run_queued() == 1

    # The first run's, again, during the second.
    match.service._Ended(thumbnails.PREVIEW, 1, (thumbnails.ERROR, "late"))

    assert match.render() == {"status": "running", "path": "", "error": "",
                              "revision": 2}
    assert match.service._running == thumbnails.PREVIEW

    match.renderer.answer()
    executor.drain(WAIT)
    assert match.render()["status"] == "done"
    assert match.service._running is None
    # And the second's, after it.
    match.service._Ended(thumbnails.PREVIEW, 2, (thumbnails.ERROR, "late"))
    assert match.render()["status"] == "done"


def test_a_save_the_shutdown_cancels_ends_the_run(match, executor):
    match.service.Generate()
    match.renderer.answer()
    executor.shutdown(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.CANCELLED
    assert list((match.dir / "out").glob("thumbnails/*")) == []


def test_a_render_that_ends_after_the_shutdown_saves_nothing(match, executor):
    match.service.Generate()
    executor.shutdown(WAIT)
    match.renderer.answer()

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.CANCELLED
    assert list((match.dir / "out").glob("thumbnails/*")) == []


def test_a_job_told_to_stop_writes_nothing(match, executor):
    stopped = executor.stopping
    match.service.Generate()
    match.renderer.answer()
    stopped.set()
    executor.drain(WAIT)

    assert match.render()["status"] == "error"
    assert match.render()["error"] == thumbnails.CANCELLED
    assert match.service.PreviewImage() is None


def test_no_half_result_is_ever_seen(match, executor):
    # The state's one key is written as a whole.
    seen = []

    def on_update(*_):
        seen.append(copy.deepcopy(match.render()))

    SM.observers.state_updated.subscribe(on_update)
    try:
        match.service.Generate()
        match.renderer.answer()
        executor.drain(WAIT)
    finally:
        SM.observers.state_updated.unsubscribe(on_update)

    assert seen
    for render in seen:
        assert set(render) == {"status", "path", "error", "revision"}
        assert (render["status"] == "done") == bool(render["path"])


# ── the shell's half of the words ────────────────────────────────────────

def test_the_session_services_words_are_qt_translations(autonaApp, tmp_path,
                                                        monkeypatch):
    """What bootstrap_core() injects: the same context and text the old code
    gave QApplication.translate, the locale's names read when used."""
    from qtpy.QtCore import QCoreApplication, QTranslator
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    class Marking(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):
            return f"{context}/{source}"

    translator = Marking()
    assert QCoreApplication.installTranslator(translator)
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", "ja")
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames",
                        {"winners_final": "WF"})
    monkeypatch.setattr(AutonaLocaleHelper, "phaseNames", {"group": "G{0}"})
    service = ThumbnailService.instance
    state, settings = SM.state, SettingsManager.settings
    os.chdir(tmp_path)
    world.Install(tmp_path, world._scenario("files"))
    try:
        SM.state = {}
        SettingsManager.settings = {}
        data, _config, _template = service.BuildPayload(
            preview=True, games=_Catalog(world.GAMES))
        assert data["tournamentInfo"]["tournamentName"] \
            == "app/Sample Tournament"
        assert data["score"]["1"]["team"]["1"]["teamName"] == "app/Team A"
        assert data["score"]["1"]["match"] == "WF"
        assert data["score"]["1"]["phase"] == "GA"
        assert {name for name in (
            data["score"]["1"]["team"]["1"]["player"]["1"]["name"],
            data["score"]["1"]["team"]["2"]["player"]["1"]["name"],
            data["score"]["1"]["team"]["2"]["player"]["2"]["name"])} \
            <= {"マリオ", "Link", "Kirby", "Pikachu"}

        data, _config, _template = service.BuildPayload(preview=True)
        assert data["tournamentInfo"]["tournamentName"] \
            == "app/Sample Tournament"

        assert service.Templates() == [
            {"id": "type_a", "name": "app/Standard"},
            {"id": "type_b", "name": "app/Center Panel"}]

        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "program_state.json").write_text("{}")
        with pytest.raises(thumbnails.ThumbnailDataMissing) as raised:
            service.BuildPayload()
        assert str(raised.value) == "thumb_app/Please select a game first"

        (tmp_path / "out" / "program_state.json").write_text(json.dumps(
            {"game": {"codename": "x"}}))
        with pytest.raises(thumbnails.ThumbnailDataMissing) as raised:
            service.BuildPayload()
        assert str(raised.value) == "thumb_app/Player 1 tag missing"
    finally:
        QCoreApplication.removeTranslator(translator)
        SM.state, SettingsManager.settings = state, settings
        os.chdir(autonaApp.dir)
