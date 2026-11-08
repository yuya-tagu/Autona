"""Record golden.json (next to this file) from the PRE-S4q implementation.

ADR-005 S4q moved the result image's payload, the values and records the Qt
panel made of its inputs, its post text and the file's name into the Qt-free
src/core/ResultService.py. tests/test_result_service.py replays golden.json
against the service. This is the script that recorded it.

It drives the OLD functions, so it cannot run against a tree where they are
gone. Run it against a copy of the last commit before S4q (bd5b9b97):

    mkdir ../autona-pre-s4q
    git archive bd5b9b97 src | tar -x -C ../autona-pre-s4q
    python tests/fixtures/result/record_golden.py ../autona-pre-s4q \\
        tests/fixtures/result/golden.json

Arguments: <old checkout> <output json>. Each scenario (scenarios.py) runs in
a throwaway working directory holding only its files. The panel's methods run
on a stand-in for `self`, whose fields answer what the scenario says they
hold.
"""
import copy
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios as world  # noqa: E402


class _FakeManager:
    """The asset adapter, as the old sample reads it."""

    def __init__(self, games):
        self.instance = self
        self.characters = games["characters"]
        self.selectedGame = games["game"]
        self._assets = games["assets"]

    def GetCharacterAssets(self, codename, skin):
        return world.CharacterAssets(self._assets, codename, skin)


class _Text:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


class _Rank:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _Combo:
    """A combo: its current row's UserRole data, and its index."""

    def __init__(self, data=None, index=-1):
        self._data = data
        self._index = index

    def currentData(self, _role=None):
        return copy.deepcopy(self._data)

    def currentIndex(self):
        return self._index


def _characterRow(en_name):
    for _text, data in world.GAME["characterRows"]:
        if data["en_name"] == en_name:
            return data
    raise AssertionError(en_name)


def _combos(pick):
    """The (character combo, colour combo) of a pick."""
    if pick is None:
        return _Combo(), _Combo()
    en_name, skin = pick
    if skin is None:
        # The colour combo is empty.
        return _Combo(_characterRow(en_name), 0), _Combo()
    data = world.GAME["skinRows"][en_name][skin][2]
    return _Combo(_characterRow(en_name), 0), _Combo(data, skin)


def _playerEntry(index, row):
    picks = (row["picks"] + [None, None, None])[:3]
    combos = []
    for pick in picks:
        combos.extend(_combos(pick))
    rank = row["rank"] if row["rank"] is not None else index + 1
    return (_Rank(rank), _Text(row["name"]), _Text(row["sponsor"]), *combos,
            _Text(row["twitter"]))


def _teamEntry(index, row):
    members = []
    for member in row["members"]:
        members.append((_Text(member["name"]), *_combos(member["pick"])))
    rank = row["rank"] if row["rank"] is not None else index + 1
    return {"rank_label": _Rank(rank), "name_edit": _Text(row["name"]),
            "members": members}


def _panelSelf(Panel, layout, rows=()):
    """What the panel's methods read of `self`."""
    team = layout in world.TEAM_LAYOUTS
    panel = SimpleNamespace(
        _previewTimer=SimpleNamespace(start=lambda: None),
        _isTeamLayout=lambda: team,
        _standingsTwitter={},
        playerInputs=[] if team else [
            _playerEntry(i, row) for i, row in enumerate(rows)],
        teamInputs=[_teamEntry(i, row) for i, row in enumerate(rows)]
        if team else [])
    for name in ("_resolveCharData", "_findCharacterForPlayer",
                 "_findAllCharactersForPlayer", "_mainsFromResultCombos",
                 "_syncPlayersToState", "_syncTeamsToState",
                 "_persistPlayersToDB", "_buildResultPostText"):
        method = getattr(Panel, name)
        setattr(panel, name, (lambda m: lambda *a, **k: m(panel, *a, **k))(
            method))
    panel._mainsFromResultCombos = Panel._mainsFromResultCombos
    return panel


def _isolated(run):
    work = Path(tempfile.mkdtemp(prefix="autona-result-golden-"))
    try:
        os.chdir(work)
        return run(work)
    finally:
        os.chdir(PROJECT_ROOT)
        shutil.rmtree(work, ignore_errors=True)


def main(old_checkout, output):
    old_checkout = Path(old_checkout).resolve()
    output = Path(output).resolve()
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(old_checkout))

    from loguru import logger
    logger.remove()

    from src.core.Actions import AutonaActions
    from src.core.PlayerDB import PlayerDB
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from src.result import main_generate_result as result
    from src import AutonaResultSettingsWidget as panelModule
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    Panel = panelModule.AutonaResultSettingsWidget
    assert Path(sys.modules["src"].__file__).resolve().is_relative_to(
        old_checkout), "imported the current tree, not the old checkout"

    AutonaActions.instance = SimpleNamespace(
        PublishResultPlayers=lambda players: StateManager.state.__setitem__(
            "result_players", players))

    def enter(state, settings, locale=world.LOCALE):
        StateManager.state = copy.deepcopy(state)
        SettingsManager.settings = copy.deepcopy(settings)
        AutonaLocaleHelper.programLocale = locale

    def payload(layout, preview, games):
        result.random = world.FixedDraws(layout in world.TEAM_LAYOUTS)
        data, config = result.prepare_data(
            SettingsManager, isPreview=preview,
            gameAssetManager=_FakeManager(games) if games else None,
            max_players=result._max_players_for_layout(layout), layout=layout)
        return json.loads(json.dumps({"data": data, "config": config}))

    payloads = {}
    for scenario in world.SCENARIOS:
        def run(work, scenario=scenario):
            world.Install(work, scenario)
            enter(scenario["state"], scenario["settings"], scenario["locale"])
            return payload(scenario["layout"], scenario["preview"],
                           scenario["games"])
        payloads[scenario["name"]] = _isolated(run)

    inputs = {}
    for scenario in world.INPUT_SCENARIOS:
        def run(_work, scenario=scenario):
            enter(scenario["state"], {})
            panel = _panelSelf(Panel, scenario["layout"], scenario["rows"])
            if scenario["layout"] in world.TEAM_LAYOUTS:
                panel._syncTeamsToState()
            else:
                panel._syncPlayersToState()
            return payload(scenario["layout"], False, None)
        inputs[scenario["name"]] = _isolated(run)

    records = {}
    for scenario in world.RECORD_SCENARIOS:
        def run(_work, scenario=scenario):
            given = []
            PlayerDB.instance = SimpleNamespace(
                AddPlayers=lambda players, overwrite=False: given.append(
                    {"players": players, "overwrite": overwrite}))
            AutonaGameAssetManager.instance = SimpleNamespace(
                selectedGame={"codename": scenario["game"]})
            enter({}, {})
            panel = _panelSelf(Panel, scenario["layout"], scenario["rows"])
            return {"count": panel._persistPlayersToDB(), "given": given}
        records[scenario["name"]] = _isolated(run)

    posts = {}
    for scenario in world.POST_SCENARIOS:
        def run(_work, scenario=scenario):
            PlayerDB.instance = SimpleNamespace(
                Get=lambda tag, default=None: scenario["db"].get(tag, default))
            enter(scenario["state"], scenario["settings"])
            panel = _panelSelf(Panel, scenario["layout"])
            for row in scenario["state"].get("result_players", {}).values():
                for member in row.get("members", []):
                    if member.get("twitter"):
                        panel._standingsTwitter[member["name"]] = \
                            member["twitter"]
            return panel._buildResultPostText()
        posts[scenario["name"]] = _isolated(run)

    class _Fixed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*world.NOW, tzinfo=tz)

    result.datetime = SimpleNamespace(datetime=_Fixed,
                                      timezone=datetime.timezone)
    names = {}
    for scenario in world.NAME_SCENARIOS:
        def run(_work, scenario=scenario):
            saved = result.save_result(
                SimpleNamespace(save=lambda _path: None),
                {"tournamentName": scenario["tournament"],
                 "players": [{}] * scenario["players"]})
            return Path(saved).name
        names[scenario["name"]] = _isolated(run)

    StateManager.state = {}
    SettingsManager.settings = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"payloads": payloads, "inputs": inputs,
                    "records": records, "posts": posts, "names": names},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
