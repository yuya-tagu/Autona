"""The tournament result's core (core/ResultService.py, ADR-005 S4q).

Moved from the old Qt panel and src/result/main_generate_result.py: the payload,
the values the panel built of its inputs, the player DB's records, the post's text
and the output's name, replayed against what the old code answered
(tests/fixtures/result/golden.json). New: the rows are written by identifiers
one at a time, the standings' import and a render in the background, whose
results are the state's result_standings and result_render.

On a ResultService of the test's own in a directory of its own, with stand-ins
for the catalog, the shell's renderer and the provider: the real renderer's half
is tests/test_result_render.py, the routes tests/test_api_v1_result.py.
"""
import ast
import copy
import datetime
import json
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import ResultService as results
from src.core import StateMigrations
from src.core import StateSchema as schema
from src.core import Top8Service
from src.core.Actions import AutonaActions
from src.core.DictHelper import deep_clone
from src.core.GameAssetCatalog import (
    GAMES_DIR, GameAssetCatalog, GameAssetSnapshot)
from src.core.PlayerDB import PlayerDB
from src.core.ResultService import ResultService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TaskExecutor import TaskExecutor
from src.Helpers.AutonaOutputRetention import PruneOutputFiles
from tests.fixtures.result import scenarios as world

WAIT = 30.0
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "result"
                     / "golden.json").read_text(encoding="utf-8"))

# What the service answers differently from the recording, on purpose. Each is
# asserted to differ (when the difference goes, so must its line) and what it
# answers now is pinned by a test of its own below.
DIFFERENCES = {
    "payloads": {
        "a_team_with_only_members":
            "decision 13: a team of members without a team name is data. The "
            "old preview drew the sample over them.",
        "result_config_other_keys":
            "decision 14: config is what the templates read. The old code "
            "passed result_config's other keys on, and no template reads them.",
        "result_config_empty_background":
            "decision 14: the same values, in config's own order. The old "
            "config started from result_config's keys.",
        "background_legacy_banner_path_without_the_file":
            "decision 15: the background is what the thumbnail's settings "
            "draw, through the thumbnail's reading. The old code drew a file "
            "that was not there; the thumbnail draws the default banner.",
    },
    "inputs": {
        "a_character_row_alone":
            "decision 7: a pick is an en_name and a skin row, so a character "
            "whose colour combo is empty is on its first skin, and is drawn. "
            "The panel wrote the character's row, which has no assets.",
    },
    "records": {},
    "posts": {
        "ranks_by_hand":
            "decision 18: the post lists the standings in the order of the "
            "ranks, as the picture does. The old text was in the slots' order.",
    },
    "names": {
        "no_tournament":
            "decision 12: a name that starts with a dash is hard to use; the "
            "old name was -top8-<date>.png.",
    },
}


class _Catalog:
    """What the service reads of the game asset catalog: a snapshot of a game
    of the scenarios', and the real row lookups."""

    def __init__(self, games=world.GAME, codename=None):
        self._current = GameAssetSnapshot(
            generation=1,
            codename=games["game"]["codename"] if codename is None
            else codename,
            game=games["game"],
            characters=games["characters"],
            characterRows=tuple(games["characterRows"]),
            skinRows=games["skinRows"])
        self._assets = games["assets"]

    def Current(self):
        return self._current

    CharacterRow = GameAssetCatalog.CharacterRow
    SkinRow = GameAssetCatalog.SkinRow
    VariantRow = GameAssetCatalog.VariantRow

    def CharacterAssets(self, codename, skin, assetpack=None):
        return world.CharacterAssets(self._assets, codename, skin)


class _Renderer:
    """The shell's renderer, as the service's `render` sees it: what was asked
    is kept, and answered when the test says."""

    def __init__(self):
        self.asked = []

    def __call__(self, kind, payload, done):
        self.asked.append(SimpleNamespace(kind=kind, payload=payload,
                                          done=done))

    def answer(self, image=b"an image"):
        asked = self.asked.pop(0)
        asked.done(lambda file_format, fast=False:
                   image + b"." + file_format.encode(), "")
        return asked


class _Provider:
    """The tournament's provider, whose answer the test says."""

    def __init__(self, answer=None, raises=None):
        self.answer = answer
        self.raises = raises
        self.asked = []

    def GetStandings(self, playerNumber, progress_callback=None,
                     cancel_event=None):
        self.asked.append(playerNumber)
        if self.raises is not None:
            raise self.raises
        return self.answer


class _Fetcher:
    """RunWithProvider: the job is kept, and run when the test says."""

    def __init__(self):
        self.provider = _Provider([])
        self.jobs = []
        self.statuses = []

    def __call__(self, job, status=None):
        if self.provider is None:
            return False
        self.statuses.append(status)
        self.jobs.append((job, self.provider))
        return True

    def run(self):
        job, provider = self.jobs.pop(0)
        job(provider)


class _Env(SimpleNamespace):
    def enter(self, scenario):
        world.Install(self.dir, scenario)
        SM.state = StateMigrations.Migrate(
            copy.deepcopy(scenario["state"]), 1)
        SettingsManager.settings = copy.deepcopy(scenario["settings"])
        self.locale["value"] = scenario["locale"]
        layout = scenario["layout"]
        SettingsManager.settings.setdefault(
            results.CONFIG_SETTING, {})["layout"] = layout

    def render(self):
        return SM.Get(schema.RESULT_RENDER_ROOT)

    def standings(self):
        return SM.Get(schema.RESULT_STANDINGS_ROOT)

    def run_queued(self):
        ran = 0
        while self.queued:
            self.queued.pop(0)()
            ran += 1
        return ran


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
    locale = {"value": world.LOCALE}
    renderer = _Renderer()
    fetcher = _Fetcher()
    refreshed = []

    def refresh(done):
        # The data is in at once unless a test says otherwise.
        if fetcher.provider is None:
            return False
        refreshed.append(True)
        done()
        return True

    service = ResultService(
        later=queued.append, render=renderer,
        locale=lambda: locale["value"], prune=PruneOutputFiles,
        fetcher=fetcher, refreshTournament=refresh,
        currentProvider=lambda: fetcher.provider)
    monkeypatch.setattr(ResultService, "instance", service)
    catalog = _Catalog()
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog)
    database = SimpleNamespace(given=[], records={})
    database.AddPlayers = lambda players, overwrite=False: database.given.append(
        {"players": players, "overwrite": overwrite})
    database.Get = lambda tag, default=None: database.records.get(tag, default)
    monkeypatch.setattr(PlayerDB, "instance", database)
    try:
        yield _Env(service=service, queued=queued, locale=locale,
                   renderer=renderer, fetcher=fetcher, refreshed=refreshed,
                   catalog=catalog, database=database, dir=tmp_path,
                   monkeypatch=monkeypatch)
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


def _setLayout(layout):
    SettingsManager.settings.setdefault(
        results.CONFIG_SETTING, {})["layout"] = layout


def _named(*names):
    """Rows with these names, on the service's own path."""
    for index, name in enumerate(names):
        assert results.UpdatePlayer(index + 1, {"name": name}) is None


def _writePlayers(rows, games):
    for index, row in enumerate(rows):
        fields = {key: row[key] for key in ("name", "sponsor", "twitter")}
        if row["rank"] is not None:
            fields["rank"] = row["rank"]
        fields["characters"] = [
            (pick[0], pick[1] or 0, None) if pick else None
            for pick in row["picks"]]
        assert results.UpdatePlayer(index + 1, fields, games) is None


def _writeTeams(rows, games):
    for index, row in enumerate(rows):
        fields = {"name": row["name"], "members": [
            {"name": member["name"],
             "character": (member["pick"][0], member["pick"][1] or 0, None)
             if member["pick"] else None}
            for member in row["members"]]}
        if row["rank"] is not None:
            fields["rank"] = row["rank"]
        assert results.UpdateTeam(index + 1, fields, games) is None


def _writeRows(scenario, games):
    if scenario["layout"] in world.TEAM_LAYOUTS:
        _writeTeams(scenario["rows"], games)
    else:
        _writePlayers(scenario["rows"], games)


def _drawn(data):
    """What the templates read of the data: the assets of a character, whose
    other keys are what decision 7 changes."""
    def assets(value):
        return (value or {}).get("assets") or {}
    return dict(data, players=[{
        "rank": row["rank"], "name": row["name"], "sponsor": row["sponsor"],
        "character": assets(row["character"]),
        "characters": [assets(value) for value in row["characters"]],
        "members": [{"name": member["name"],
                     "character": assets(member["character"])}
                    for member in row["members"]],
    } for row in data["players"]])


# ── what moved, against the recording ────────────────────────────────────

def test_the_recording_covers_every_scenario():
    assert set(GOLDEN["payloads"]) == {s["name"] for s in world.SCENARIOS}
    assert set(GOLDEN["inputs"]) == {s["name"] for s in world.INPUT_SCENARIOS}
    assert set(GOLDEN["records"]) == {s["name"] for s in world.RECORD_SCENARIOS}
    assert set(GOLDEN["posts"]) == {s["name"] for s in world.POST_SCENARIOS}
    assert set(GOLDEN["names"]) == {s["name"] for s in world.NAME_SCENARIOS}


def _payloadOf(env, scenario):
    env.enter(scenario)
    games = _Catalog(scenario["games"]) if scenario["games"] else None
    data, config, layout = env.service.BuildPayload(
        preview=scenario["preview"], games=games, layout=scenario["layout"])
    assert layout == scenario["layout"]
    return data, config


@pytest.mark.parametrize("scenario", world.SCENARIOS,
                         ids=[s["name"] for s in world.SCENARIOS])
def test_the_payload_is_what_the_old_code_built(env, scenario):
    data, config = _payloadOf(env, scenario)
    expected = GOLDEN["payloads"][scenario["name"]]
    # As text: the key order is kept too, and is what a template iterates.
    same = json.dumps({"data": data, "config": config}, ensure_ascii=False) \
        == json.dumps(expected, ensure_ascii=False)
    if scenario["name"] in DIFFERENCES["payloads"]:
        assert not same
    else:
        assert json.dumps({"data": data, "config": config}, ensure_ascii=False,
                          indent=1) \
            == json.dumps(expected, ensure_ascii=False, indent=1)


@pytest.mark.parametrize("scenario", world.INPUT_SCENARIOS,
                         ids=[s["name"] for s in world.INPUT_SCENARIOS])
def test_a_pick_draws_what_the_panel_drew(env, scenario):
    SM.state = copy.deepcopy(scenario["state"])
    _setLayout(scenario["layout"])
    _writeRows(scenario, env.catalog)

    data, _config, _layout = env.service.BuildPayload(layout=scenario["layout"])

    expected = GOLDEN["inputs"][scenario["name"]]["data"]
    if scenario["name"] in DIFFERENCES["inputs"]:
        assert _drawn(data) != _drawn(expected)
    else:
        assert _drawn(data) == _drawn(expected)


@pytest.mark.parametrize("scenario", world.RECORD_SCENARIOS,
                         ids=[s["name"] for s in world.RECORD_SCENARIOS])
def test_the_records_are_what_the_panel_saved(env, scenario):
    _setLayout(scenario["layout"])
    _writeRows(scenario, env.catalog)
    if scenario["game"] != env.catalog.Current().codename:
        env.monkeypatch.setattr(GameAssetCatalog, "instance",
                                _Catalog(codename=scenario["game"]))

    count = env.service.SaveToDb()

    expected = GOLDEN["records"][scenario["name"]]
    assert count == expected["count"]
    assert env.database.given == expected["given"]


@pytest.mark.parametrize("scenario", world.POST_SCENARIOS,
                         ids=[s["name"] for s in world.POST_SCENARIOS])
def test_the_post_is_what_the_panel_built(env, scenario):
    env.database.records = scenario["db"]
    SM.state = StateMigrations.Migrate(copy.deepcopy(scenario["state"]), 1)
    SettingsManager.settings = copy.deepcopy(scenario["settings"])
    _setLayout(scenario["layout"])

    text = env.service.PostText()

    expected = GOLDEN["posts"][scenario["name"]]
    if scenario["name"] in DIFFERENCES["posts"]:
        assert text != expected
    else:
        assert text == expected


@pytest.mark.parametrize("scenario", world.NAME_SCENARIOS,
                         ids=[s["name"] for s in world.NAME_SCENARIOS])
def test_the_file_is_named_as_it_was(scenario):
    now = datetime.datetime(*world.NOW, tzinfo=datetime.timezone.utc)
    stem = results.OutputStem(
        {"tournamentName": scenario["tournament"],
         "players": [{}] * scenario["players"]}, now)

    expected = GOLDEN["names"][scenario["name"]]
    if scenario["name"] in DIFFERENCES["names"]:
        assert f"{stem}.png" != expected
    else:
        assert f"{stem}.png" == expected


def test_the_differences_are_of_existing_scenarios():
    for section, lines in DIFFERENCES.items():
        assert set(lines) <= set(GOLDEN[section])


# What each of the differences answers.

def test_a_team_of_members_without_a_name_is_drawn(env):
    scenario = next(s for s in world.SCENARIOS
                    if s["name"] == "a_team_with_only_members")

    data, _config = _payloadOf(env, scenario)

    assert data["players"][0]["name"] == ""
    assert [member["name"] for member in data["players"][0]["members"]] == ["A1"]
    assert "tournamentName" in data and data["tournamentName"] == "Big Cup"
    assert env.service.Describe()["sample"] is False


def test_the_config_is_what_the_templates_read(env):
    scenario = next(s for s in world.SCENARIOS
                    if s["name"] == "result_config_other_keys")

    _data, config = _payloadOf(env, scenario)

    assert list(config) == ["main_icon_path", "background_path", "theme_color",
                            "accent_color"]


def test_an_empty_result_config_background_is_the_same_config(env):
    scenario = next(s for s in world.SCENARIOS
                    if s["name"] == "result_config_empty_background")

    _data, config = _payloadOf(env, scenario)

    assert config == GOLDEN["payloads"][scenario["name"]]["config"]


def test_a_legacy_banner_path_without_the_file_is_the_default_banner(env):
    scenario = next(s for s in world.SCENARIOS if s["name"]
                    == "background_legacy_banner_path_without_the_file")

    _data, config = _payloadOf(env, scenario)

    assert config["background_path"] == schema.DEFAULT_BANNER_PATH


def test_a_character_alone_is_on_its_first_skin(env):
    results.UpdatePlayer(1, {"name": "Alpha",
                             "characters": [("Mario", 0, None)]}, env.catalog)

    data, _config, _layout = env.service.BuildPayload()

    assert data["players"][0]["character"]["assets"]["full"]["asset"] \
        == "./games/mario/full_0.png"


def test_the_post_is_in_the_order_of_the_ranks(env):
    scenario = next(s for s in world.POST_SCENARIOS
                    if s["name"] == "ranks_by_hand")
    SM.state = copy.deepcopy(scenario["state"])

    assert env.service.PostText().split("\n")[-4:] \
        == ["1/ First", "2/ Second", "3/ Third", "4/ Fourth"]


def test_a_nameless_tournament_has_a_file_name_that_says_what_it_is():
    now = datetime.datetime(*world.NOW, tzinfo=datetime.timezone.utc)

    assert results.OutputStem({"tournamentName": "", "players": [{}] * 8}, now) \
        == "result-top8-2026-11-09-01-02-03"


# ── the sample ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("layout", world.LAYOUTS)
def test_the_sample_is_always_the_same(env, layout):
    _setLayout(layout)

    first = env.service.BuildPayload(preview=True, games=env.catalog)
    second = env.service.BuildPayload(preview=True, games=env.catalog)

    assert first == second
    assert first[0]["tournamentName"] == "Sample Tournament"


def test_the_service_draws_nothing_at_random():
    source = Path(results.__file__).read_text(encoding="utf-8")

    assert "random" not in source.replace(
        "It is always the\n  same one (the old code's was random", "")


def test_the_sample_is_only_for_a_preview(env):
    assert env.service.BuildPayload(games=env.catalog)[0]["players"][0]["name"] \
        == ""


def test_the_sample_words_are_translated(env):
    env.service._translate = lambda context, text: f"{context}:{text}"

    data = env.service.BuildPayload(preview=True, games=env.catalog)[0]

    assert data["tournamentName"] == "app:Sample Tournament"


# ── the rows ─────────────────────────────────────────────────────────────

def test_a_row_is_written_in_one_save_cycle_with_the_values_core_builds(env):
    SM.SaveState()
    revision = SM.revision

    results.UpdatePlayer(3, {
        "rank": 7, "name": " Alpha ", "sponsor": "SPN ", "twitter": " @a ",
        "characters": [("Mario", 2, "tgame"), None, ("Link", 1, None)],
    })

    assert SM.revision == revision + 1
    row = SM.Get("result_players.3")
    assert (row["rank"], row["name"], row["sponsor"], row["twitter"]) \
        == (7, "Alpha", "SPN", "@a")
    assert row["character"] == Top8Service.CharacterValue(
        "Mario", 2, env.catalog)
    assert row["characters"] == [
        Top8Service.CharacterValue("Mario", 2, env.catalog), {},
        Top8Service.CharacterValue("Link", 1, env.catalog)]


@pytest.mark.parametrize("sent", [
    [("Mario", 0, None), None, ("Link", 0, None)],
    [None, None, ("Link", 0, None)],
    [None, ("Link", 0, None), ("Mario", 0, None)],
    [None, ("Link", 0, None)],
    [("Mario", 0, None), ("Mario", 0, None)],
    [("Mario", 0, None), ("Mario", 0, None), ("Mario", 0, None)],
    [("Mario", 0, None), ("Link", 0, None), None],
])
def test_an_empty_field_keeps_its_place(env, sent):
    """A form's third field stays the third when the second is emptied."""
    results.UpdatePlayer(1, {"name": "A", "characters": sent})

    picks = env.service.Describe()["players"][0]["characters"]

    expected = [{"en_name": pick[0], "skin": pick[1]} if pick else None
                for pick in sent]
    assert picks == expected + [None] * (results.MAX_CHARACTERS - len(sent))
    # A template draws what is in the list.
    drawn = env.service.BuildPayload()[0]["players"][0]["characters"]
    assert [value["en_name"] for value in drawn] \
        == [pick[0] for pick in sent if pick]


def test_the_empty_fields_after_the_last_one_are_not_kept(env):
    results.UpdatePlayer(1, {"characters": [None, None, None]})

    assert SM.Get("result_players.1.character") == {}
    assert SM.Get("result_players.1.characters") == []


def test_a_field_not_sent_stays(env):
    results.UpdatePlayer(1, {"name": "Alpha", "sponsor": "SPN",
                             "characters": [("Mario", 1, None)]})

    results.UpdatePlayer(1, {"sponsor": ""})

    row = SM.Get("result_players.1")
    assert row["name"] == "Alpha" and row["sponsor"] == ""
    assert row["character"]["en_name"] == "Mario"


def test_a_new_row_has_every_key_with_its_rank_from_the_slot(env):
    results.UpdatePlayer(5, {"name": "Alpha"})

    assert SM.Get("result_players.5") == {
        "rank": 5, "name": "Alpha", "sponsor": "", "twitter": "",
        "character": {}, "characters": []}


@pytest.mark.parametrize("pick, code", [
    (("Nobody", 0, None), results.UNKNOWN_CHARACTER),
    (("Mario", 99, None), results.UNKNOWN_SKIN),
    (("Mario", 0, "other"), results.OTHER_GAME),
])
def test_a_refused_pick_writes_nothing_of_the_row(env, pick, code):
    results.UpdatePlayer(1, {"name": "Alpha"})
    before = deep_clone(SM.Get("result_players"))

    refused = results.UpdatePlayer(1, {
        "name": "Changed", "characters": [("Link", 0, None), pick]})

    assert refused == results.Refusal(code, pick)
    assert SM.Get("result_players") == before


@pytest.mark.parametrize("pick, code", [
    (("Nobody", 0, None), results.UNKNOWN_CHARACTER),
    (("Mario", 99, None), results.UNKNOWN_SKIN),
    (("Mario", 0, "other"), results.OTHER_GAME),
])
def test_a_refused_pick_writes_nothing_of_the_team(env, pick, code):
    results.UpdateTeam(1, {"name": "Reds"})
    before = deep_clone(SM.Get("result_teams"))

    refused = results.UpdateTeam(1, {"name": "Changed", "members": [
        {"name": "A1", "character": ("Link", 0, None)},
        {"name": "B1", "character": pick}]})

    assert refused == results.Refusal(code, pick)
    assert SM.Get("result_teams") == before


def test_a_skin_of_a_character_without_skins_is_taken_as_its_only_one(env):
    games = copy.deepcopy(world.GAME)
    games["skinRows"]["Kirby"] = []
    catalog = _Catalog(games)

    assert results.UpdatePlayer(
        1, {"characters": [("Kirby", 5, None)]}, catalog) is None
    assert SM.Get("result_players.1.character")["skin"] == -1


def test_a_team_row_keeps_its_members_in_place(env):
    results.UpdateTeam(2, {"rank": 4, "name": " Reds ", "members": [
        {"name": "A1", "twitter": " @a1 ", "character": ("Mario", 1, "tgame")},
        {"name": "", "twitter": "", "character": None},
        {"name": "C1", "twitter": "", "character": None},
    ]})

    row = SM.Get("result_teams.2")
    assert (row["rank"], row["name"]) == (4, "Reds")
    assert [member["name"] for member in row["members"]] == ["A1", "", "C1"]
    assert row["members"][0]["twitter"] == "@a1"
    assert row["members"][0]["character"] == Top8Service.CharacterValue(
        "Mario", 1, env.catalog)

    data = env.service.BuildPayload(layout="team")[0]
    assert [member["name"] for member in data["players"][1]["members"]] \
        == ["A1", "C1"]
    assert list(data["players"][1]["members"][0]) == ["name", "character"]


def test_the_two_kinds_of_rows_do_not_touch_each_other(env):
    results.UpdatePlayer(1, {"name": "Alpha"})
    results.UpdateTeam(1, {"name": "Reds"})

    assert env.service.BuildPayload(layout="horizontal")[0]["players"][0]["name"] \
        == "Alpha"
    assert env.service.BuildPayload(layout="team")[0]["players"][0]["name"] \
        == "Reds"
    assert results.Clear(results.TEAMS)
    assert SM.Get("result_players.1.name") == "Alpha"
    assert SM.Get("result_teams.1.name") == ""


def test_a_team_row_the_panel_left_is_swept_at_startup(env):
    # The Qt panel, until S4r, wrote a team layout's rows to result_players:
    # StateMigrations' v1 -> v2 moved what it found, a state saved at version 2
    # may hold more. Not a player's: the seed removes them, the players stay.
    SM.state = {"result_players": {
        "1": {"rank": 1, "name": "Reds", "sponsor": "", "members": [
            {"name": "A1", "character": {}}]},
        "2": {"rank": 2, "name": "Alpha", "sponsor": ""}}}

    schema.SeedPanelState()

    assert list(SM.Get("result_players")) == ["2"]
    assert results.HasData("horizontal")


def test_a_root_of_only_team_rows_goes_with_them(env):
    SM.state = {"result_players": {
        "1": {"rank": 1, "name": "Reds", "sponsor": "", "members": []}}}

    schema.SeedPanelState()

    assert SM.Get("result_players") is None
    assert not results.HasInput(results.PLAYERS)
    assert not results.HasData("horizontal")
    assert env.service.Describe()["players"][0]["name"] == ""


def test_the_sweep_leaves_a_state_without_rows_and_the_rows_alone(env):
    SM.state = {}
    schema.SeedPanelState()
    assert SM.Get("result_players") is None

    _named("Alpha", "Beta")
    before = copy.deepcopy(SM.Get("result_players"))
    schema.SeedPanelState()
    assert SM.Get("result_players") == before


def test_reorder_moves_the_rows_and_ranks_them(env):
    _named("A", "B", "C")
    results.UpdatePlayer(1, {"rank": 9, "sponsor": "SA"})

    assert results.Reorder(results.PLAYERS, [3, 1, 2])

    rows = SM.Get("result_players")
    assert [(rows[slot]["rank"], rows[slot]["name"], rows[slot]["sponsor"])
            for slot in "123"] \
        == [(1, "C", ""), (2, "A", "SA"), (3, "B", "")]


def test_reorder_leaves_the_rows_after_them_alone(env):
    _named("A", "B", "C")
    results.UpdatePlayer(3, {"rank": 12})

    assert results.Reorder(results.PLAYERS, [2, 1])

    rows = SM.Get("result_players")
    assert [rows[slot]["name"] for slot in "123"] == ["B", "A", "C"]
    assert rows["3"]["rank"] == 12


def test_reorder_is_one_save_cycle(env):
    _named("A", "B")
    SM.SaveState()
    revision = SM.revision

    results.Reorder(results.PLAYERS, [2, 1])

    assert SM.revision == revision + 1


def test_reorder_teams(env):
    results.UpdateTeam(1, {"name": "Reds"})
    results.UpdateTeam(2, {"name": "Blues"})

    assert results.Reorder(results.TEAMS, [2, 1])

    assert SM.Get("result_teams.1.name") == "Blues"
    assert SM.Get("result_teams.2.rank") == 2


@pytest.mark.parametrize("kind, order", [
    (results.PLAYERS, [1, 1]),
    (results.PLAYERS, [0, 1]),
    (results.PLAYERS, [2, 3]),
    (results.PLAYERS, []),
    (results.PLAYERS, "12"),
    (results.PLAYERS, list(range(1, 18))),
    (results.TEAMS, list(range(1, 10))),
    ("nobody", [1]),
])
def test_a_bad_order_changes_nothing(env, kind, order):
    _named("A", "B")
    before = deep_clone(SM.Get("result_players"))

    assert not results.Reorder(kind, order)

    assert SM.Get("result_players") == before
    assert SM.Get("result_teams") is None


def test_clear_empties_every_row_of_the_kind(env):
    _named("A", "B")

    assert results.Clear(results.PLAYERS)

    rows = SM.Get("result_players")
    assert list(rows) == [str(slot) for slot in range(1, 17)]
    assert rows["1"] == results.DefaultPlayer(1)
    assert not results.HasInput(results.PLAYERS)
    assert not results.Clear("nobody")


def test_what_counts_as_input_and_as_data(env):
    results.UpdatePlayer(1, {"sponsor": "SPN"})
    assert results.HasInput(results.PLAYERS)
    assert not results.HasData("horizontal")

    results.UpdatePlayer(9, {"name": "Ninth"})
    assert not results.HasData("horizontal")
    assert results.HasData("horizontal_16")

    results.UpdateTeam(1, {"members": [{"name": "A1", "character": None}]})
    assert results.HasInput(results.TEAMS)
    assert results.HasData("team")


def test_the_payload_shares_nothing_with_the_state(env):
    results.UpdatePlayer(1, {"name": "Alpha",
                             "characters": [("Mario", 0, None)]})

    data = env.service.BuildPayload()[0]
    data["players"][0]["character"]["assets"] = "changed"
    data["players"][0]["characters"].append("changed")

    assert SM.Get("result_players.1.character")["assets"] != "changed"
    assert len(SM.Get("result_players.1.characters")) == 1


def test_reading_writes_nothing(env):
    SM.state = {"tournamentInfo": {"tournamentName": "Cup"}}
    before = deep_clone(SM.state)

    env.service.BuildPayload()
    env.service.Describe()
    env.service.PostText()

    assert SM.state == before
    assert SettingsManager.settings == {}


# ── what the scoreboard lends ────────────────────────────────────────────

def _onTheBoard(name="alpha"):
    return {"score": {"1": {"team": {"1": {"player": {"1": {
        "name": name, "character": {
            "2": world._character("link", 1),
            "1": world._character("mario", 2)}}}}}}}}


def test_a_player_without_a_character_borrows_the_boards_when_drawn(env):
    SM.state = _onTheBoard()
    results.UpdatePlayer(1, {"name": "ALPHA "})

    data = env.service.BuildPayload()[0]

    assert data["players"][0]["character"]["en_name"] == "Mario"
    assert data["players"][0]["characters"][0]["en_name"] == "Mario"
    # Looked up when drawing, never written.
    assert SM.Get("result_players.1.character") == {}
    assert SM.Get("result_players.1.characters") == []
    assert env.service.Describe()["players"][0]["fromScoreboard"] == "Mario"


def test_a_pick_wins_over_the_board(env):
    SM.state = _onTheBoard()
    results.UpdatePlayer(1, {"name": "alpha",
                             "characters": [("Pikachu", 1, None)]})

    data = env.service.BuildPayload()[0]

    assert data["players"][0]["character"]["en_name"] == "Pikachu"
    assert env.service.Describe()["players"][0]["fromScoreboard"] is None


def test_a_member_borrows_too(env):
    SM.state = _onTheBoard()
    results.UpdateTeam(1, {"name": "Reds", "members": [
        {"name": "alpha", "character": None}]})

    data = env.service.BuildPayload(layout="team")[0]

    assert data["players"][0]["members"][0]["character"]["en_name"] == "Mario"
    assert SM.Get("result_teams.1.members")[0]["character"] == {}
    described = env.service.Describe()["teams"][0]["members"][0]
    assert described["fromScoreboard"] == "Mario"


@pytest.mark.parametrize("board", [
    None, {"1": None}, {"1": {"team": None}}, {"1": {"team": {"1": None}}},
    {"1": {"team": {"1": {"player": None}}}},
    {"1": {"team": {"1": {"player": {"1": None}}}}},
    {"1": {"team": {"1": {"player": {"1": {"name": None}}}}}},
    {"1": {"team": {"1": {"player": {"1": {
        "name": "alpha", "character": None}}}}}},
])
def test_a_board_with_holes_lends_nothing(env, board):
    SM.state = {"score": board}
    results.UpdatePlayer(1, {"name": "alpha"})

    assert env.service.BuildPayload()[0]["players"][0]["character"] == {}


# ── reading a stored value back ──────────────────────────────────────────

def test_a_stored_value_reads_back_as_the_pick_of_any_shape(env):
    # The three shapes the panel wrote (Top8Service.CharacterPick's), and what
    # core writes.
    full = world._character("mario", 2)
    SM.state = {"result_players": {
        "1": world._single(1, "A", "", "", Top8Service.CharacterValue(
            "Mario", 2, env.catalog)),
        "2": world._single(2, "B", "", "", world._skinValue("link", 1)),
        "3": world._single(3, "C", "", "", {"codename": "mario",
                                            "en_name": "Mario"}),
        "4": world._single(4, "D", "", "", full),
    }}

    players = env.service.Describe()["players"]

    assert players[0]["characters"][0] == {"en_name": "Mario", "skin": 2}
    assert players[1]["characters"][0] == {"en_name": "Link", "skin": 1}
    assert players[2]["characters"][0]["en_name"] == "Mario"
    assert players[3]["characters"][0]["en_name"] == "Mario"


def test_three_picks_read_back_in_their_places(env):
    results.UpdatePlayer(1, {"name": "A", "characters": [
        None, ("Link", 1, None), ("Pikachu", 0, None)]})

    picks = env.service.Describe()["players"][0]["characters"]

    assert picks == [None, {"en_name": "Link", "skin": 1},
                     {"en_name": "Pikachu", "skin": 0}]


def test_a_value_of_another_game_reads_back_as_none(env):
    results.UpdatePlayer(1, {"name": "A", "characters": [("Mario", 0, None)]})
    env.monkeypatch.setattr(GameAssetCatalog, "instance", _Catalog(
        dict(world.GAME, game={"codename": "other", "name": "Other"},
             characters={}, characterRows=[], skinRows={})))

    assert env.service.Describe()["players"][0]["characters"] \
        == [None, None, None]


def test_describe_fills_every_slot(env):
    described = env.service.Describe()

    assert [row["slot"] for row in described["players"]] \
        == list(range(1, 17))
    assert [row["slot"] for row in described["teams"]] == list(range(1, 9))
    assert described["sample"] is True
    assert described["layout"] == "horizontal"
    assert [layout["id"] for layout in described["layouts"]] \
        == list(results.LAYOUT_SUBDIRS)
    assert described["render"] == schema.DefaultResultRender()
    assert described["standings"] == schema.DefaultResultStandings()


# ── a saved character of another game ────────────────────────────────────

# What a value looks like, its pictures under the game's folder.
def _inGame(codename, en_name="Mario"):
    folder = f"{GAMES_DIR}{codename}/"
    return {"name": en_name, "en_name": en_name, "display_name": en_name,
            "codename": en_name.lower(), "modded": False,
            "assets": {"full": {"asset": f"{folder}/full/{en_name}.png"},
                       "base_files/icon": {"asset": f"{folder}icon/a.png"}},
            "skin": 0, "variant": {}}


def _savedRow(value, name="Alpha"):
    SM.Set("result_players.1", dict(
        results.DefaultPlayer(1), name=name, character=value,
        characters=[value]))


def _scoreboardHas(name, value):
    SM.Set("score.1.team.1.player.1", {"name": name, "character": {"1": value}})


def test_a_character_of_the_game_loaded_is_read(env):
    value = _inGame("tgame")
    _savedRow(value)

    assert env.service.BuildPayload()[0]["players"][0]["character"] == value
    assert env.service.Describe()["players"][0]["characters"][0] \
        == {"en_name": "Mario", "skin": 0}
    assert results.HasInput(results.PLAYERS)


def test_a_character_saved_with_another_game_is_not_drawn_nor_shown(env):
    """The app started on another game than it was closed on: the switch that
    empties the rows never happened (`game` is empty at the launch), and the
    other game's picture went on being drawn. Mario is in both."""
    _savedRow(_inGame("other"))

    player = env.service.BuildPayload()[0]["players"][0]

    assert player["character"] == {}
    assert player["characters"] == []
    described = env.service.Describe()["players"][0]
    assert described["characters"] == [None, None, None]
    assert described["fromScoreboard"] is None


def test_it_stays_in_the_state_and_is_back_with_its_game(env):
    value = _inGame("other")
    _savedRow(value)
    env.service.BuildPayload()
    env.service.Describe()

    assert SM.Get("result_players.1.character") == value
    assert SM.Get("result_players.1.characters") == [value]

    env.monkeypatch.setattr(GameAssetCatalog, "instance",
                            _Catalog(codename="other"))

    assert env.service.BuildPayload()[0]["players"][0]["character"] == value


def test_the_scoreboard_lends_in_its_place(env):
    _savedRow(_inGame("other"))
    lent = _inGame("tgame", "Link")
    _scoreboardHas("Alpha", lent)

    player = env.service.BuildPayload()[0]["players"][0]

    assert player["character"] == lent
    assert player["characters"] == [lent]
    assert env.service.Describe()["players"][0]["fromScoreboard"] == "Link"


def test_the_scoreboard_does_not_lend_another_games(env):
    results.UpdatePlayer(1, {"name": "Alpha"})
    _scoreboardHas("Alpha", _inGame("other"))

    assert env.service.BuildPayload()[0]["players"][0]["character"] == {}
    assert env.service.Describe()["players"][0]["fromScoreboard"] is None


def test_a_team_members_is_read_the_same_way(env):
    SM.Set("result_teams.1", dict(results.DefaultTeam(1), name="Reds", members=[
        {"name": "Stale", "twitter": "", "character": _inGame("other")},
        {"name": "Fresh", "twitter": "", "character": _inGame("tgame")}]))
    SettingsManager.settings = {results.CONFIG_SETTING: {"layout": "team"}}

    members = env.service.BuildPayload()[0]["players"][0]["members"]

    assert members[0] == {"name": "Stale", "character": {}}
    assert members[1]["character"] == _inGame("tgame")
    assert env.service.Describe()["teams"][0]["members"][0]["character"] is None
    assert SM.Get("result_teams.1.members")[0]["character"] == _inGame("other")


def test_a_member_with_nothing_but_another_games_character_is_no_data(env):
    SM.Set("result_teams.1", dict(results.DefaultTeam(1), members=[
        {"name": "", "twitter": "", "character": _inGame("other")}]))
    SettingsManager.settings = {results.CONFIG_SETTING: {"layout": "team"}}

    assert not results.HasData("team")
    assert not results.HasInput(results.TEAMS)
    assert env.service.Generate() == results.NO_DATA


def test_it_is_not_saved_in_the_database(env):
    _savedRow(_inGame("other"))

    env.service.SaveToDb()

    assert env.database.given[0]["players"] == [{"gamerTag": "Alpha"}]


def _savedCharacters(values):
    """A player's fields as UpdatePlayer leaves them: `characters` starts with
    the first when that is not empty."""
    SM.Set("result_players.1", dict(
        results.DefaultPlayer(1), name="Alpha", character=values[0],
        characters=values if values[0] else values[1:]))


def test_an_unread_first_character_keeps_the_others_in_their_places(env):
    """`characters` starts with the first. Read as empty, the two were no
    longer the same, and the first place came twice: the others were a place
    late, and the last one fell off."""
    link = _inGame("tgame", "Link")
    pikachu = _inGame("tgame", "Pikachu")
    _savedCharacters([_inGame("other"), link, pikachu])

    assert env.service.Describe()["players"][0]["characters"] == [
        None, {"en_name": "Link", "skin": 0}, {"en_name": "Pikachu", "skin": 0}]
    env.service.SaveToDb()
    assert env.database.given[0]["players"][0]["mains"] == {
        "tgame": [["Link", 0, ""], ["Pikachu", 0, ""]]}
    player = env.service.BuildPayload()[0]["players"][0]
    assert player["character"] == {}
    assert player["characters"] == [link, pikachu]


def test_an_unread_first_character_keeps_the_gap_after_it(env):
    pikachu = _inGame("tgame", "Pikachu")
    _savedCharacters([_inGame("other"), {}, pikachu])

    assert env.service.Describe()["players"][0]["characters"] == [
        None, None, {"en_name": "Pikachu", "skin": 0}]


def test_a_row_with_only_the_last_character_read_has_input(env):
    """It fell off with the others: the row asked no confirmation to be
    overwritten, and was not drawn with its character."""
    _savedCharacters([_inGame("other"), {}, _inGame("tgame", "Pikachu")])
    SM.Set("result_players.1.name", "")

    assert results.HasInput(results.PLAYERS)


def test_an_unread_character_anywhere_else_keeps_its_place_empty(env):
    link = _inGame("tgame", "Link")
    pikachu = _inGame("tgame", "Pikachu")
    _savedCharacters([link, _inGame("other"), pikachu])

    assert env.service.Describe()["players"][0]["characters"] == [
        {"en_name": "Link", "skin": 0}, None,
        {"en_name": "Pikachu", "skin": 0}]
    # The same character twice is two places, and not an unread first.
    _savedCharacters([link, link])
    assert env.service.Describe()["players"][0]["characters"] == [
        {"en_name": "Link", "skin": 0}, {"en_name": "Link", "skin": 0}, None]


def test_the_same_unread_character_twice_is_two_empty_places(env):
    other = _inGame("other")
    link = _inGame("tgame", "Link")
    _savedCharacters([other, other, link])

    assert env.service.Describe()["players"][0]["characters"] == [
        None, None, {"en_name": "Link", "skin": 0}]


def test_a_write_of_one_place_leaves_another_games_character_there(env):
    """The page shows it as empty and wrote every field back: the character
    of the game it was saved with was gone on going back to it (S4q B3)."""
    value = _inGame("other")
    _savedRow(value)

    results.UpdatePlayer(1, {"characters": {"2": ("Link", 0, "tgame")}})

    row = SM.Get("result_players.1")
    assert row["character"] == value
    assert [item.get("en_name") for item in row["characters"]] \
        == ["Mario", "Link"]
    assert row["characters"][0] == value


def test_a_write_of_the_first_place_leaves_the_others(env):
    value = _inGame("other", "Link")
    SM.Set("result_players.1", dict(
        results.DefaultPlayer(1), name="Alpha", character=_inGame("tgame"),
        characters=[_inGame("tgame"), value]))

    results.UpdatePlayer(1, {"characters": {"1": None}})

    row = SM.Get("result_players.1")
    assert row["character"] == {}
    assert row["characters"] == [value]
    assert env.service.Describe()["players"][0]["characters"] \
        == [None, None, None]


def test_emptying_the_last_place_shortens_the_list(env):
    results.UpdatePlayer(1, {"characters": [("Mario", 0, None),
                                            ("Link", 0, None)]})

    results.UpdatePlayer(1, {"characters": {"2": None}})

    row = SM.Get("result_players.1")
    assert row["character"]["en_name"] == "Mario"
    assert [item["en_name"] for item in row["characters"]] == ["Mario"]


def test_a_refused_place_writes_nothing(env):
    value = _inGame("other")
    _savedRow(value)
    before = copy.deepcopy(SM.Get("result_players"))

    refused = results.UpdatePlayer(1, {
        "name": "Changed", "characters": {"2": ("Mario", 0, "another")}})

    assert refused is not None
    assert SM.Get("result_players") == before


def test_a_members_name_leaves_the_other_members_characters(env):
    """Their characters were the game's before a switch: written back with the
    new codename, they read as the new game's. Only what was edited goes."""
    stale = _inGame("other")
    SM.Set("result_teams.1", dict(results.DefaultTeam(1), name="Reds", members=[
        {"name": "A1", "twitter": "@a1", "character": stale},
        {"name": "B1", "twitter": "", "character": stale}]))

    results.UpdateTeam(1, {"members": {"2": {"name": " B2 "}}})

    assert SM.Get("result_teams.1.members") == [
        {"name": "A1", "twitter": "@a1", "character": stale},
        {"name": "B2", "twitter": "", "character": stale}]


def test_a_member_named_past_the_end_makes_room(env):
    results.UpdateTeam(1, {"members": {"3": {"character": ("Link", 0, None)}}})

    members = SM.Get("result_teams.1.members")
    assert [member["name"] for member in members] == ["", "", ""]
    assert members[0] == {"name": "", "twitter": "", "character": {}}
    assert members[2]["character"]["en_name"] == "Link"


def test_the_empty_members_at_the_end_go(env):
    results.UpdateTeam(1, {"members": [
        {"name": "A1"}, {"name": "B1"}, {"name": "C1"}]})

    results.UpdateTeam(1, {"members": {"3": {"name": ""}}})
    assert [m["name"] for m in SM.Get("result_teams.1.members")] == ["A1", "B1"]

    results.UpdateTeam(1, {"members": {"1": {"name": ""}}})
    assert [m["name"] for m in SM.Get("result_teams.1.members")] == ["", "B1"]


def test_a_member_with_only_another_games_character_stays(env):
    stale = _inGame("other")
    SM.Set("result_teams.1", dict(results.DefaultTeam(1), members=[
        {"name": "A1", "twitter": "", "character": {}},
        {"name": "", "twitter": "", "character": stale}]))

    results.UpdateTeam(1, {"members": {"1": {"name": "A2"}}})

    assert SM.Get("result_teams.1.members")[1]["character"] == stale


def test_a_refused_member_writes_nothing(env):
    results.UpdateTeam(1, {"name": "Reds", "members": [{"name": "A1"}]})
    before = copy.deepcopy(SM.Get("result_teams"))

    refused = results.UpdateTeam(1, {"name": "Blues", "members": {
        "1": {"name": "A2"}, "2": {"character": ("Mario", 0, "another")}}})

    assert refused is not None
    assert SM.Get("result_teams") == before


def test_the_web_ui_limits_are_the_cores():
    """web/src/ResultPage/Cards.tsx cannot import from Python and no build step
    ties them: a field it offers past the limit fails its write."""
    source = (Path(__file__).parent.parent / "web" / "src" / "ResultPage"
              / "Cards.tsx").read_text(encoding="utf-8")

    def constant(name):
        match = re.search(rf"export const {name}\b[^=]*= ([^;]+);", source)
        assert match, f"could not parse {name} out of Cards.tsx"
        return json.loads(match.group(1))

    assert constant("MAX_CHARACTERS") == results.MAX_CHARACTERS
    assert constant("MAX_MEMBERS") == results.MAX_MEMBERS
    assert tuple(constant("RANK_RANGE")) == results.RANK_RANGE


def test_a_write_of_another_field_leaves_it_where_it_is(env):
    value = _inGame("other")
    _savedRow(value)

    results.UpdatePlayer(1, {"sponsor": "SPN"})
    results.Reorder(results.PLAYERS, [1])

    assert SM.Get("result_players.1.character") == value
    assert SM.Get("result_players.1.characters") == [value]


@pytest.mark.parametrize("value, read", [
    # Nothing to go by but the game's list.
    ({"name": "Mario", "en_name": "Mario", "codename": "mario", "assets": {}},
     True),
    ({"codename": "mario"}, True),
    ({"en_name": "Mario"}, True),
    ({"name": "Ryu", "en_name": "Ryu", "codename": "ryu", "assets": {}}, False),
    ({"name": "typed"}, False),
    # Pictures from somewhere else say nothing.
    ({"assets": {"full": {"asset": "./elsewhere/a.png"}}}, True),
    # A plain path, as the sample's are.
    ({"assets": {"full": f"{GAMES_DIR}other/a.png"}}, False),
    ({"assets": {"full": f"{GAMES_DIR}tgame/a.png"}}, True),
])
def test_what_says_whose_a_value_is(env, value, read):
    assert results._OfLoadedGame(value, env.catalog.Current()) is read


def test_without_a_game_loaded_nothing_saved_is_drawn(env):
    _savedRow(_inGame("tgame"))
    env.monkeypatch.setattr(GameAssetCatalog, "instance",
                            _Catalog(codename=""))

    assert env.service.BuildPayload()[0]["players"][0]["character"] == {}


def test_without_a_catalog_nothing_can_be_said(env):
    value = _inGame("other")
    _savedRow(value)
    env.monkeypatch.setattr(GameAssetCatalog, "instance", None)

    assert env.service.BuildPayload()[0]["players"][0]["character"] == value


# ── a game switch ────────────────────────────────────────────────────────

def test_a_game_switch_empties_the_characters(env):
    results.UpdatePlayer(1, {"name": "A", "characters": [("Mario", 0, None)]})
    results.UpdateTeam(1, {"name": "Reds", "members": [
        {"name": "A1", "twitter": "@a1", "character": ("Link", 0, None)},
        {"name": "B1", "twitter": "", "character": None}]})
    SM.SaveState()
    revision = SM.revision

    results.ForgetCharacters()

    assert SM.revision == revision + 1
    assert SM.Get("result_players.1.name") == "A"
    assert SM.Get("result_players.1.character") == {}
    assert SM.Get("result_players.1.characters") == []
    members = SM.Get("result_teams.1.members")
    assert [member["character"] for member in members] == [{}, {}]
    assert members[0]["twitter"] == "@a1"


def test_a_game_switch_empties_what_the_panel_wrote_too(env):
    SM.state = {"result_players": {
        "1": world._team(1, "Reds", "A1"),
        "2": world._single(2, "B", "", "", world._skinValue("mario", 0))}}

    results.ForgetCharacters()

    assert SM.Get("result_players.1.members")[0]["character"] == {}
    assert SM.Get("result_players.2.character") == {}


def test_a_game_switch_with_nothing_to_empty_writes_nothing(env):
    _named("A")
    SM.SaveState()
    revision = SM.revision

    results.ForgetCharacters()

    assert SM.revision == revision


def test_the_catalog_empties_the_result_when_it_replaces_a_game():
    source = (Path(results.__file__).parent / "GameAssetCatalog.py") \
        .read_text(encoding="utf-8")

    replaced = source[source.index("if snapshot.replaced:"):]
    assert "ResultService.ForgetCharacters()" \
        in replaced[:replaced.index("return snapshot")]


# ── the layout ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt, layout", [
    ("singles", "horizontal"), ("team", "team"), ("crew", "team")])
def test_the_layout_starts_on_the_battle_formats(env, fmt, layout):
    env.monkeypatch.setattr(AutonaActions.instance, "ResolveBattleFormat",
                            lambda: fmt)

    assert env.service.Layout() == layout


def test_the_layout_is_saved(env):
    assert env.service.SetLayout("vertical_geki")

    assert env.service.Layout() == "vertical_geki"
    assert SettingsManager.settings[results.CONFIG_SETTING]["layout"] \
        == "vertical_geki"


@pytest.mark.parametrize("layout", ["nope", "", None, 3, ["team"]])
def test_a_bad_layout_is_refused(env, layout):
    assert not env.service.SetLayout(layout)

    assert SettingsManager.settings == {}


def test_a_bad_saved_layout_is_not_used(env):
    SettingsManager.settings = {results.CONFIG_SETTING: {"layout": "gone"}}

    assert env.service.Layout() == "horizontal"


def test_saving_the_layout_keeps_the_rest_of_the_config(env):
    SettingsManager.settings = {results.CONFIG_SETTING: {
        "main_icon_path": "./layout/own_logo.png"}}

    env.service.SetLayout("vertical")

    assert SettingsManager.settings[results.CONFIG_SETTING] == {
        "main_icon_path": "./layout/own_logo.png", "layout": "vertical"}


@pytest.mark.parametrize("saved, fmt, after", [
    ("horizontal", "team", "team"),
    ("vertical", "crew", "team"),
    ("team_split", "team", "team_split"),
    ("team", "crew", "team"),
    ("team", "singles", "horizontal"),
    ("team_split", "singles", "horizontal"),
    ("vertical", "singles", "vertical"),
])
def test_the_layout_follows_the_battle_format_as_the_panels_did(
        env, saved, fmt, after):
    env.service.SetLayout(saved)

    env.service.FollowBattleFormat(fmt)

    assert env.service.Layout() == after
    # Told to a page that is open: a change in the sidebar is none of its doing.
    assert SM.Get(schema.RESULT_LAYOUT_ROOT) == after


def test_the_layout_in_the_state_is_the_one_chosen(env):
    assert env.service.SetLayout("vertical")
    assert SM.Get(schema.RESULT_LAYOUT_ROOT) == "vertical"
    assert not env.service.SetLayout("nope")
    assert SM.Get(schema.RESULT_LAYOUT_ROOT) == "vertical"


def test_a_format_that_only_changes_the_unsaved_layout_is_told_too(
        env, monkeypatch):
    # No layout saved: Layout() answers from the format, nothing is written.
    SettingsManager.settings.pop(results.CONFIG_SETTING, None)
    monkeypatch.setattr(ResultService, "_BattleFormat",
                        staticmethod(lambda: "team"))

    env.service.FollowBattleFormat("team")

    assert results.CONFIG_SETTING not in SettingsManager.settings
    assert SM.Get(schema.RESULT_LAYOUT_ROOT) == results.DEFAULT_TEAM_LAYOUT


def test_the_seed_empties_the_layouts_echo(env):
    env.service.SetLayout("vertical")

    schema.SeedPanelState()

    assert SM.Get(schema.RESULT_LAYOUT_ROOT) == ""
    assert env.service.Layout() == "vertical"


# ── the configuration of a render ────────────────────────────────────────

def test_a_replaced_banner_is_told_apart_by_its_version(env):
    world.Install(env.dir, world._scenario("banner", files=world.BANNER_FILE))
    SettingsManager.settings = {"thumbnail_config": {
        "background_source": "banner", "background_path": ""}}
    SM.state = {"tournamentInfo": {"bannerVersion": 1234}}

    config = env.service.BuildPayload()[1]

    assert config["background_path"] == f"{schema.TOURNAMENT_BANNER_PATH}?v=1234"


def test_a_result_config_path_is_not_given_a_version(env):
    SettingsManager.settings = {results.CONFIG_SETTING: {
        "background_path": "./user_data/own_background.png"}}
    SM.state = {"tournamentInfo": {"bannerVersion": 1234}}

    assert env.service.BuildPayload()[1]["background_path"] \
        == "./user_data/own_background.png"


# ── the lock ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("layout", ["horizontal", "team"])
def test_the_sample_is_made_with_no_lock_held(env, layout):
    """It looks at the game's files, character by character: every save of the
    app would wait for it."""
    held = []
    games = _Catalog()
    assets = games.CharacterAssets

    def spy(codename, skin, assetpack=None):
        held.append(SM.lock._is_owned())
        return assets(codename, skin, assetpack)

    games.CharacterAssets = spy

    env.service.BuildPayload(preview=True, games=games, layout=layout)

    assert held and not any(held)


# ── the player database ──────────────────────────────────────────────────

def test_a_members_handle_goes_to_the_database(env):
    results.UpdateTeam(1, {"name": "Reds", "members": [
        {"name": "A1", "twitter": "@a1", "character": ("Mario", 1, None)}]})
    _setLayout("team")

    assert env.service.SaveToDb() == 1

    assert env.database.given == [{"players": [{
        "gamerTag": "A1", "twitter": "@a1",
        "mains": {"tgame": [["Mario", 1, ""]]}}], "overwrite": False}]


def test_nothing_to_save_does_not_touch_the_database(env):
    assert env.service.SaveToDb() == 0

    assert env.database.given == []


def test_no_lock_is_held_when_the_database_is_called(env):
    _named("A")
    held = []

    def add(players, overwrite=False):
        # Another thread must be able to take the lock meanwhile.
        got = threading.Event()

        def take():
            if SM.lock.acquire(timeout=WAIT):
                SM.lock.release()
                got.set()
        thread = threading.Thread(target=take)
        thread.start()
        thread.join(WAIT)
        held.append(got.is_set())
    env.database.AddPlayers = add

    env.service.SaveToDb()

    assert held == [True]


# ── the post ─────────────────────────────────────────────────────────────

def test_the_post_lists_what_the_layout_shows(env):
    _named(*[f"P{index}" for index in range(1, 17)])
    results.UpdatePlayer(16, {"rank": 1})
    results.UpdatePlayer(1, {"rank": 2})
    results.UpdatePlayer(2, {"rank": 3})
    results.UpdatePlayer(3, {"rank": 4})
    results.UpdatePlayer(4, {"rank": 5})

    assert env.service.PostText("horizontal").split("\n")[:2] \
        == ["2/ P1", "3/ P2"]
    assert env.service.PostText("horizontal_16").split("\n")[:2] \
        == ["1/ P16", "2/ P1"]


def test_the_post_of_a_member_uses_its_own_handle_first(env):
    env.database.records = {"A1": {"twitter": "db_a1"},
                            "B1": {"twitter": "@db_b1"}}
    results.UpdateTeam(1, {"name": "Reds", "members": [
        {"name": "A1", "twitter": "@own_a1", "character": None},
        {"name": "B1", "twitter": "", "character": None}]})

    assert "Reds [A1 @own_a1 / B1 @db_b1]" in env.service.PostText("team")


def test_a_post_without_a_database_still_answers(env):
    env.monkeypatch.setattr(PlayerDB, "instance", None)
    _named("Alpha")

    assert env.service.PostText() == "1/ Alpha"


# ── a render ─────────────────────────────────────────────────────────────

def test_a_preview_draws_and_publishes(env, executor):
    revision = env.service.Preview()

    assert env.render() == {"status": results.RUNNING, "path": "", "error": "",
                            "revision": revision}
    asked = env.renderer.answer()
    assert asked.kind == "result"
    data, config, layout = asked.payload
    assert data["tournamentName"] == "Sample Tournament"
    assert layout == "horizontal"
    assert set(config) == {"main_icon_path", "background_path", "theme_color",
                           "accent_color"}

    executor.run_next(WAIT)
    assert env.render()["status"] == results.DONE
    assert env.service.PreviewImage() == b"an image.PNG"
    assert Path(results.PREVIEW_PATH).exists()


def test_a_preview_does_not_touch_the_database(env, executor):
    _named("Alpha")

    env.service.Preview()
    env.renderer.answer()
    executor.run_next(WAIT)

    assert env.database.given == []


def test_a_generate_saves_the_file_and_the_players(env, executor):
    SM.state = {"tournamentInfo": {"tournamentName": "Big/Cup"}}
    _named("Alpha", "Bravo")

    revision = env.service.Generate()

    assert isinstance(revision, int)
    assert [record["gamerTag"] for record in
            env.database.given[0]["players"]] == ["Alpha", "Bravo"]
    env.renderer.answer()
    executor.run_next(WAIT)

    render = env.render()
    assert render["status"] == results.DONE
    assert render["path"].startswith("out/results/BigCup-top8-")
    assert render["path"].endswith(".png")
    assert Path(render["path"]).read_bytes() == b"an image.PNG"
    # A PNG only, and the preview follows.
    assert not list(Path("out/results").glob("*.jpg"))
    assert env.service.PreviewImage() == b"an image.PNG"


def test_a_generate_without_data_is_refused_and_changes_nothing(env, executor):
    SM.state = {"tournamentInfo": {"tournamentName": "Cup"}}

    assert env.service.Generate() == results.NO_DATA

    assert env.renderer.asked == []
    assert env.render() is None
    assert env.database.given == []
    assert not Path("out/results").exists()


def test_a_second_generate_is_busy_and_saves_nothing_twice(env, executor):
    _named("Alpha")
    env.service.Generate()

    assert env.service.Generate() == results.BUSY

    assert len(env.database.given) == 1


def test_a_failing_database_does_not_take_the_image_with_it(env, executor):
    _named("Alpha")

    def add(players, overwrite=False):
        raise OSError("the disk is full")
    env.database.AddPlayers = add

    assert isinstance(env.service.Generate(), int)
    env.renderer.answer()
    executor.run_next(WAIT)

    assert env.render()["status"] == results.DONE


def test_a_kept_generate_with_nothing_left_to_draw_says_so(env, executor):
    # The rows were emptied between the request and its turn.
    _named("Alpha")
    env.service.Preview()
    assert env.service.Generate() != results.NO_DATA
    results.Clear(results.PLAYERS)
    env.renderer.answer()
    executor.run_next(WAIT)

    env.run_queued()

    assert env.render()["error"] == results.NO_DATA


def test_the_saved_results_are_pruned_to_thirty(env, executor):
    directory = Path("out/results")
    directory.mkdir(parents=True)
    for index in range(35):
        path = directory / f"old-{index}.png"
        path.write_bytes(b"x")
        os.utime(path, (index + 1, index + 1))
    _named("Alpha")

    env.service.Generate()
    env.renderer.answer()
    executor.run_next(WAIT)

    assert len(list(directory.glob("*.png"))) == results.OUTPUT_KEEP


def test_a_new_session_starts_without_the_last_preview(env):
    target = Path(results.PREVIEW_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old")

    ResultService(later=env.queued.append)

    assert not target.exists()


def test_the_layout_is_read_when_the_run_starts(env, executor):
    env.service.SetLayout("vertical")

    env.service.Preview()

    assert env.renderer.asked[0].payload[2] == "vertical"


# ── the standings' import ────────────────────────────────────────────────

def _standing(placement, tag, prefix="", twitter="", mains=None, name=None):
    entry = {"placement": placement, "players": [
        {"gamerTag": tag, "prefix": prefix, "twitter": twitter,
         "mains": {"tgame": mains or []}}]}
    if name is not None:
        entry["name"] = name
    return entry


def test_the_import_fills_the_rows(env):
    env.fetcher.provider.answer = [
        _standing(1, "Alpha", "SPN", " @a ", [["Mario"], ["mario"], ["Nobody"],
                                              ["Link"]]),
        _standing(2, "Bravo", mains=[["Nobody"]]),
        {"placement": None, "players": []},
    ]
    _setLayout("horizontal")

    revision = env.service.LoadStandings()

    assert env.standings() == {"status": results.RUNNING, "error": "",
                               "revision": revision}
    assert env.refreshed == [True]
    env.fetcher.run()

    assert env.fetcher.provider.asked == [8]
    assert env.standings() == {"status": results.DONE, "error": "",
                               "revision": revision}
    rows = SM.Get("result_players")
    assert (rows["1"]["rank"], rows["1"]["name"], rows["1"]["sponsor"],
            rows["1"]["twitter"]) == (1, "Alpha", "SPN", "@a")
    assert rows["1"]["character"] == Top8Service.CharacterValue(
        "Mario", 0, env.catalog)
    # Up to three mains: an unknown one is an empty place.
    assert [value["en_name"] for value in rows["1"]["characters"]] \
        == ["Mario", "Mario"]
    assert rows["2"]["character"] == {} and rows["2"]["characters"] == []
    # A row the standings have no player for keeps its place.
    assert (rows["3"]["rank"], rows["3"]["name"]) == (3, "")
    assert rows["4"] == results.DefaultPlayer(4)
    assert len(rows) == 16


def test_the_import_of_sixteen(env):
    _setLayout("horizontal_16")
    env.fetcher.provider.answer = [_standing(1, "A")]

    env.service.LoadStandings()
    env.fetcher.run()

    assert env.fetcher.provider.asked == [16]


def test_the_import_of_teams(env):
    _setLayout("team")
    env.fetcher.provider.answer = [
        {"placement": 1, "name": "Reds", "players": [
            {"gamerTag": f"M{index}", "twitter": f"@m{index}",
             "mains": {"tgame": [["Link"], ["Mario"]]}}
            for index in range(6)]},
        {"placement": 2, "players": [{"gamerTag": "Solo"}]},
    ]

    env.service.LoadStandings()
    env.fetcher.run()

    teams = SM.Get("result_teams")
    assert teams["1"]["name"] == "Reds"
    assert [member["name"] for member in teams["1"]["members"]] \
        == [f"M{index}" for index in range(5)]
    assert teams["1"]["members"][0]["twitter"] == "@m0"
    assert teams["1"]["members"][0]["character"]["en_name"] == "Link"
    # A team of one entrant without a name is called by its only player.
    assert teams["2"]["name"] == "Solo"
    assert len(teams) == 8
    # The other kind is left alone.
    assert SM.Get("result_players") is None


def test_a_placement_that_is_no_number_is_the_rows(env):
    env.fetcher.provider.answer = [
        {"placement": "first", "players": [{"gamerTag": "A"}]},
        {"placement": "5", "players": [{"gamerTag": "B"}]}]

    env.service.LoadStandings()
    env.fetcher.run()

    assert SM.Get("result_players.1.rank") == 1
    assert SM.Get("result_players.2.rank") == 5


def test_the_standings_are_fetched_after_the_tournaments_data(env):
    """The data names the tournament's game. Fetched during the import, the
    switch to it found its own import `changed`, and the operator had to ask
    again."""
    asked = []

    def refresh(done):
        asked.append(done)
        return True

    env.service._refreshTournament = refresh
    env.fetcher.provider.answer = [_standing(1, "New")]

    revision = env.service.LoadStandings()

    assert env.standings() == {"status": results.RUNNING, "error": "",
                               "revision": revision}
    assert env.fetcher.jobs == []
    assert env.service.LoadStandings(confirmed=True) == results.BUSY

    env.monkeypatch.setattr(GameAssetCatalog, "instance",
                            _Catalog(codename="another"))
    asked.pop()()
    env.fetcher.run()

    assert env.standings() == {"status": results.DONE, "error": "",
                               "revision": revision}
    assert SM.Get("result_players.1.name") == "New"


def test_the_game_switched_after_the_tournaments_data_still_drops_it(env):
    env.fetcher.provider.answer = [_standing(1, "New")]

    env.service.LoadStandings()
    env.monkeypatch.setattr(GameAssetCatalog, "instance",
                            _Catalog(codename="another"))
    env.fetcher.run()

    assert env.standings()["error"] == results.CHANGED


def test_a_tournament_switched_during_the_data_ends_the_import(env):
    asked = []

    def refresh(done):
        asked.append(done)
        return True

    env.service._refreshTournament = refresh
    revision = env.service.LoadStandings()

    env.fetcher.provider = _Provider([])
    asked.pop()()

    assert env.fetcher.jobs == []
    assert env.standings() == {"status": results.ERROR,
                               "error": results.CHANGED, "revision": revision}
    # And the flag is down.
    env.service._refreshTournament = lambda done: done() or True
    assert env.service.LoadStandings() == revision + 1


@pytest.mark.parametrize("fetcher", [
    lambda _job, _status=None: False,   # the app is quitting
    lambda _job, _status=None: 1 / 0,
])
def test_standings_that_cannot_be_asked_for_after_the_data_end_it(env, fetcher):
    asked = []

    def refresh(done):
        asked.append(done)
        return True

    env.service._refreshTournament = refresh
    revision = env.service.LoadStandings()
    env.service._fetcher = fetcher

    asked.pop()()

    assert env.standings() == {"status": results.ERROR,
                               "error": results.CHANGED, "revision": revision}
    assert env.service._importing is False


def test_no_tournament_for_the_data_is_no_tournament(env):
    env.service._refreshTournament = lambda _done: False

    assert env.service.LoadStandings() == results.NO_TOURNAMENT

    assert env.standings() is None
    assert env.service._importing is False
    assert env.fetcher.jobs == []


def test_a_failed_fetch_is_told_apart_and_writes_nothing(env):
    _named("Kept")
    env.fetcher.provider.raises = LookupError("no answer")

    revision = env.service.LoadStandings(confirmed=True)
    env.fetcher.run()

    assert env.standings() == {"status": results.ERROR,
                               "error": results.FETCH_FAILED,
                               "revision": revision}
    assert SM.Get("result_players.1.name") == "Kept"


def test_an_event_without_standings_is_told_apart_and_writes_nothing(env):
    _named("Kept")
    env.fetcher.provider.answer = []

    env.service.LoadStandings(confirmed=True)
    env.fetcher.run()

    assert env.standings()["error"] == results.NO_STANDINGS
    assert SM.Get("result_players.1.name") == "Kept"


def test_a_game_switched_during_the_import_drops_it(env):
    _named("Kept")
    env.fetcher.provider.answer = [_standing(1, "New", mains=[["Mario"]])]

    env.service.LoadStandings(confirmed=True)
    env.monkeypatch.setattr(GameAssetCatalog, "instance",
                            _Catalog(codename="another"))
    env.fetcher.run()

    assert env.standings()["error"] == results.CHANGED
    assert SM.Get("result_players.1.name") == "Kept"


def test_a_tournament_switched_during_the_import_drops_it(env):
    _named("Kept")
    env.fetcher.provider.answer = [_standing(1, "New")]

    env.service.LoadStandings(confirmed=True)
    job, _provider = env.fetcher.jobs[0]
    env.fetcher.provider = _Provider([])
    job(_provider)

    assert env.standings()["error"] == results.CHANGED
    assert SM.Get("result_players.1.name") == "Kept"


def test_the_import_needs_a_confirmation_over_the_rows(env):
    results.UpdatePlayer(1, {"sponsor": "SPN"})

    assert env.service.LoadStandings() == results.NEEDS_CONFIRMATION

    assert env.fetcher.jobs == []
    assert env.standings() is None


def test_the_import_only_asks_about_the_kind_it_would_overwrite(env):
    results.UpdatePlayer(1, {"name": "Alpha"})
    _setLayout("team")

    assert isinstance(env.service.LoadStandings(), int)


def test_an_import_that_does_not_go_in_the_rows_is_told_from_a_fetch(env):
    """The answer came: what failed is ours. They were one code, which told the
    operator start.gg did not answer."""
    _named("Kept")
    env.fetcher.provider.answer = [_standing(1, "New")]

    def boom(*_args, **_kwargs):
        raise KeyError("not what it should be")
    env.monkeypatch.setattr(results, "ImportStandings", boom)

    revision = env.service.LoadStandings(confirmed=True)
    env.fetcher.run()

    assert env.standings() == {"status": results.ERROR,
                               "error": results.IMPORT_FAILED,
                               "revision": revision}
    assert SM.Get("result_players.1.name") == "Kept"
    assert env.service._importing is False


def test_the_next_import_gets_in_when_the_end_cannot_be_published(env):
    env.fetcher.provider.answer = []
    env.service.LoadStandings()

    def boom(*_args):
        raise RuntimeError("no")
    published = env.service._PublishImport
    env.service._PublishImport = boom

    with pytest.raises(RuntimeError):
        env.service._EndImport(1, results.ERROR, results.NO_STANDINGS)

    env.service._PublishImport = published
    assert env.service._importing is False


def test_busy_is_said_before_the_rows_are_asked_about(env):
    """One in progress: there is nothing to confirm, and a confirmation would
    get the same answer."""
    env.service.LoadStandings()
    results.UpdatePlayer(1, {"name": "Alpha"})

    assert env.service.LoadStandings() == results.BUSY
    assert env.service.LoadStandings(confirmed=True) == results.BUSY


def test_the_lock_order_is_the_documented_one_when_it_asks(env, monkeypatch):
    """StateManager.lock, then the import's: HasInput takes the former."""
    orders = []
    realHasInput = results.HasInput

    def hasInput(kind):
        orders.append((SM.lock._is_owned(), env.service._importLock.locked()))
        return realHasInput(kind)

    monkeypatch.setattr(results, "HasInput", hasInput)

    env.service.LoadStandings()

    assert orders == [(True, True)]


def test_one_import_at_a_time(env):
    revision = env.service.LoadStandings()

    assert env.service.LoadStandings() == results.BUSY
    assert len(env.fetcher.jobs) == 1

    env.fetcher.provider.answer = [_standing(1, "A")]
    env.fetcher.run()
    assert env.standings()["revision"] == revision

    results.Clear(results.PLAYERS)
    assert env.service.LoadStandings() == revision + 1


def test_without_a_tournament_nothing_changes(env):
    env.fetcher.provider = None

    assert env.service.LoadStandings() == results.NO_TOURNAMENT

    assert env.standings() is None
    assert env.refreshed == []
    # And the flag is down.
    env.fetcher.provider = _Provider([])
    assert isinstance(env.service.LoadStandings(), int)


def test_without_a_fetcher_nothing_changes(env):
    env.service._fetcher = None

    assert env.service.LoadStandings() == results.NO_TOURNAMENT
    assert env.standings() is None


def test_a_fetcher_that_raises_lets_the_next_one_in(env):
    def boom(_job, _status=None):
        raise RuntimeError("no")
    env.service._fetcher = boom
    env.service._refreshTournament = None

    with pytest.raises(RuntimeError):
        env.service.LoadStandings()

    assert env.standings() is None
    env.service._fetcher = env.fetcher
    assert isinstance(env.service.LoadStandings(), int)


def test_a_job_that_ended_before_the_announcement_stands(env):
    # A worker may be done before the requester publishes "running".
    env.fetcher.provider.answer = [_standing(1, "A")]

    def atOnce(job, _status=None):
        job(env.fetcher.provider)
        return True
    env.service._fetcher = atOnce

    revision = env.service.LoadStandings()

    assert env.standings() == {"status": results.DONE, "error": "",
                               "revision": revision}


def test_a_refresh_that_fails_does_not_stop_the_import(env):
    def boom(_done):
        raise RuntimeError("no")
    env.service._refreshTournament = boom

    assert isinstance(env.service.LoadStandings(), int)
    assert len(env.fetcher.jobs) == 1


def test_a_failed_refresh_keeps_the_progress_revisions_going(env):
    env.fetcher.provider.raises = LookupError("no")
    first = env.service.LoadStandings()
    env.fetcher.run()

    assert env.service.LoadStandings() == first + 1


# ── the launch's seed ────────────────────────────────────────────────────

@pytest.mark.parametrize("root, read, default", [
    (schema.RESULT_RENDER_ROOT, schema.ReadResultRender,
     schema.DefaultResultRender),
    (schema.RESULT_STANDINGS_ROOT, schema.ReadResultStandings,
     schema.DefaultResultStandings),
])
def test_the_launch_seed_is_idle_at_the_revision_published_last(
        env, root, read, default):
    """Nothing is running at launch; the revision goes on from the one saved,
    so that a reader holding an older one sees the change."""
    SM.Set(root, dict(default(7), status="running"))
    schema.SeedPanelState()
    assert SM.Get(root) == default(7)
    SM.Set(root, "not a dict")
    schema.SeedPanelState()
    assert SM.Get(root) == default(0)
    assert read() == default(0)
    assert tuple(schema.KEY_CATALOG[root]) == tuple(default())


def test_the_launch_seed_does_not_invent_rows(env):
    schema.SeedPanelState()

    assert SM.Get(schema.RESULT_TEAMS_ROOT) is None
    assert env.service.Describe()["teams"][0]["name"] == ""
    assert schema.RESULT_TEAMS_ROOT in schema.KEY_CATALOG


# ── who calls it ─────────────────────────────────────────────────────────

def test_the_window_tells_the_service_of_a_battle_format():
    # src/Autona.py is the app's start: read, not imported (agent-pitfalls #1).
    tree = ast.parse((Path(results.__file__).parents[1] / "Autona.py")
                     .read_text(encoding="utf-8"))
    window = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "Window")
    method = next(node for node in window.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "SetBattleFormat")

    assert "ResultService.instance.FollowBattleFormat(fmt)" \
        in ast.get_source_segment(
            (Path(results.__file__).parents[1] / "Autona.py")
            .read_text(encoding="utf-8"), method)


# ── the real provider ────────────────────────────────────────────────────

_ENTRANT = {"participants": [{"player": {"id": 1, "gamerTag": "Alpha",
                                         "prefix": "SPN"}}],
            "paginatedSets": {"nodes": []}}


@pytest.mark.parametrize("answer, status, error, rows", [
    ({}, results.ERROR, results.FETCH_FAILED, None),            # retries spent
    ({"errors": [{"message": "x"}], "data": {"event": None}},   # no such event
     results.ERROR, results.FETCH_FAILED, None),
    # An answer it cannot read.
    ({"data": {"event": {"standings": {"nodes": [{"placement": 1}]}}}},
     results.ERROR, results.FETCH_FAILED, None),
    ({"data": {"event": {"standings": {"nodes": []}}}},
     results.ERROR, results.NO_STANDINGS, None),
    ({"data": {"event": {"standings": None}}},
     results.ERROR, results.NO_STANDINGS, None),
    ({"data": {"event": {"standings": {"nodes": [
        {"placement": 1, "entrant": _ENTRANT}]}}}},
     results.DONE, "", "Alpha"),
])
def test_the_real_provider_tells_a_failed_fetch_from_no_standings(
        env, monkeypatch, answer, status, error, rows):
    """StartGGDataProvider.GetStandings used to swallow a failure and answer
    None or [], so an offline PC read as "the event has no standings"."""
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)

    provider = StartGGDataProvider(
        "https://www.start.gg/tournament/a/event/s", None)
    monkeypatch.setattr(provider, "QueryRequests",
                        lambda *args, **kwargs: answer)
    env.fetcher.provider = provider
    _named("Kept")

    env.service.LoadStandings(confirmed=True)
    env.fetcher.run()

    assert env.standings()["status"] == status
    assert env.standings()["error"] == error
    assert SM.Get("result_players.1.name") == (rows or "Kept")
