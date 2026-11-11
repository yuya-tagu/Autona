"""Loading a player into a slot is core's (ADR-005 S3e-3, S2f).

Until S3e-3 the card's SetData was the one place a player's data met the
slot: it set the line edits' text and emitted their editingFinished, picked
the country / region / character / skin / variant rows on its combos, and
the state followed from the signals. Every way in -- a set's entrants, the
Web's player edit, a tag lookup, the name completer, the Qt Player List's
editor -- went through it, so none of them could run without the widgets.
`PlayerService.ApplyData` is that now, on the state alone (the DB merge,
the bad-word filter, the location and character lookups); the card asks
for it and renders the slot like any other write. The card's own Clear and
SavePlayerToDB went the same way (`PlayerService.ClearSlot` /
`SavePlayerToDB`).

The state, the save cycles and local_players.json are external contracts,
so the move is pinned against a recording made through those entry points
on the last tree that had the widget implementation (ae922c24, with
AUTONA_RECORD_PLAYER_APPLY=1). It cannot be recorded again: the `tag` steps go
through AutonaWebOperations.load_player_from_tag now, which that tree does not
have (the recording went through the board's LoadPlayerFromTag, gone with the
Qt Player List, ADR-005 S4n), and on a later tree the switch would only record
that tree, which pins nothing. A step that has to change is edited in
golden.json, as S4n's were. The scenarios are
tests/fixtures/player_apply/scenarios.py; every step records the outcome, the
save cycles, the statistics triggers, every state write under the board, the
whole subtree afterwards (key order included), what each card shows and the
player database file. This replays it through what the card and the board
called -- the card and the board themselves until ADR-005 S4ac-3, which
removed them with the Qt SCORE; what each card showed is no longer compared
(no card is left to show it), every other field is.
tests/test_player_service.py replays the same recording through
PlayerService alone.

What S3e-3 changed on purpose is listed in ``CHANGES`` and applied to the
recording before the comparison; everything else has to match. The
steps the S3e-3 review changed were recorded again on that tree instead
(marked "rerecorded" in golden.json; see ``CHANGES``).
"""
import ast
import copy
import json
import os
from pathlib import Path

import orjson
import pytest

from src import AutonaWebOperations as WebOps
from src.core.Actions import AutonaActions
from src.core.BadWordFilter import BadWordFilter
from src.core.DictHelper import deep_clone
from src.core.PlayerDB import PlayerDB
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from tests.conftest import apply_set
# The board's players / characters per player spinboxes, as they asked.
from tests.test_board_operations import _setCharacters, _setPlayers
# The S1d world: the fixture games under the slot recording's locale.
from tests.test_player_slot_publish import slotEnv  # noqa: F401  (fixture)
from tests.fixtures.player_slots.recording import digest, encode
from tests.fixtures.stats.spies import StatsTriggers
from tests.fixtures.player_apply.scenarios import (
    DB_PLAYERS, SCENARIOS, Cycles, Expected, Writes, byPlayer, collapsed,
    compact, normalise)

RECORD = os.environ.get("AUTONA_RECORD_PLAYER_APPLY") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures" / "player_apply"
               / "golden.json")
GOLDEN = None if RECORD or not GOLDEN_PATH.exists() else json.loads(
    GOLDEN_PATH.read_text(encoding="utf-8"))

# ── observing a step ─────────────────────────────────────────────────────

# How often each of the board's statistics triggers fired: the recording's
# names are the board's old AutonaStatsUtil signals, which core's
# StatsService.Refresh replaced in ADR-005 S3e-5 (the spy maps them).
_StatsTriggers = StatsTriggers


def _digestAssets(value):
    if isinstance(value, dict):
        return {key: (digest(item) if key == "assets" else _digestAssets(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_digestAssets(item) for item in value]
    return value


def _keyOrders(value, path=""):
    """{dot path: [keys in insertion order]} for every dict in `value`
    (an `assets` table's own keys excluded: the digest covers them)."""
    orders = {}
    if isinstance(value, dict):
        orders[path] = [str(key) for key in value]
        for key, item in value.items():
            if key != "assets":
                orders.update(_keyOrders(
                    item, f"{path}.{key}" if path else str(key)))
    return orders


def _subtree(app, root):
    """The subtree as a digest of its exact encoding (key order included),
    plus, readable, what a player load can move: the teams and the board's
    own scalars (the upset factor)."""
    with StateManager.lock:
        value = deep_clone(StateManager.Get(root) or {})
    exact = normalise(encode(value), app.dir)
    shown = {key: item for key, item in value.items()
             if key == "team" or not isinstance(item, (dict, list))}
    readable = json.loads(normalise(orjson.dumps(
        _digestAssets(shown),
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS).decode(),
        app.dir))
    return {"state": readable, "stateDigest": digest(exact),
            "keyOrder": _keyOrders(shown)}


def _slots(app, cards):
    """The exact encoding of each card's slot, as a digest: what the
    service-only replay compares (it builds the slots, not the board)."""
    result = {}
    for path in cards:
        with StateManager.lock:
            value = deep_clone(StateManager.Get(path) or {})
        result[path] = digest(normalise(encode(value), app.dir))
    return result


def _dbText():
    path = Path(PlayerDB.instance.path)
    if not path.exists():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


# ── the world a scenario runs in ─────────────────────────────────────────

class _World:
    """The board a scenario drives (its number)."""

    def __init__(self, app, scenario):
        self.app = app
        self.scenario = scenario
        self.board = app.ready(scenario["board"])
        _setPlayers(self.board, scenario.get("players", 1))
        _setCharacters(self.board, 3)
        app.settle()
        self.root = f"score.{self.board}"

    @property
    def cards(self):
        """The slot paths the board's cards stood for: team 1's, then team
        2's (a card per slot the state has)."""
        return [f"{self.root}.team.{team}.player.{player}"
                for team in (1, 2)
                for player in range(
                    1, AutonaActions.RosterCount(self.board, team) + 1)]

    def card(self, slot):
        """The slot path of the card at (team, player)."""
        team, player = slot
        return f"{self.root}.team.{team}.player.{player}"

    def snapshot(self):
        entry = _subtree(self.app, self.root)
        entry["slots"] = _slots(self.app, self.cards)
        entry["db"] = _dbText()
        return entry

    def close(self):
        self.app.settle()


def _run(world, step):
    """One step of a scenario, through what the entry point it names
    called."""
    op = step["op"]
    board = world.board
    if op == "set":
        # The card's SetData (the name completer's pick and the Qt Player
        # List's editor): PlayerService.ApplyData. The card answered
        # nothing.
        PlayerService(world.card(step["slot"])).ApplyData(
            copy.deepcopy(step["data"]), step["loadFromDB"], step["clear"],
            step["no_mains"])
        return None
    if op == "clear":
        # The card's clear button: PlayerService.ClearSlot, answering
        # nothing as above.
        PlayerService(world.card(step["slot"])).ClearSlot(step["no_mains"])
        return None
    if op == "save":
        return PlayerService(world.card(step["slot"])).SavePlayerToDB()
    if op == "typed":
        with StateManager.SaveBlock():
            PlayerService(world.card(step["slot"])).PublishCharacterSlots(
                [CharacterSlot(**slot) for slot in step["slots"]])
        return None
    if op == "chars":
        _setCharacters(board, step["count"])
        return None
    if op == "tag":
        # The Web's tag lookup (the board's LoadPlayerFromTag when this was
        # recorded): its "OK" / "ERROR" are what that answered True / False.
        answer = WebOps.load_player_from_tag(
            board, step["tag"], step["team"], step["player"],
            step.get("no_mains", False))
        return {"OK": True, "ERROR": False}[answer]
    if op == "update":
        # /api/v1's player PATCH: the board's ApplyPlayerData when this was
        # recorded, the Web's operation since ADR-005 S3e-4.
        return WebOps.update_player(board, step["team"], step["player"],
                                    copy.deepcopy(step["data"]))
    if op == "setdata":
        return apply_set(board, copy.deepcopy(step["payload"]))
    if op == "swap":
        # The board's SWAP button, which answered nothing.
        AutonaActions.instance.SwapTeams(board)
        return None
    raise AssertionError(f"unknown op {op!r}")


def _outcome(call):
    try:
        return call()
    except Exception as e:
        return {"raises": type(e).__name__}


@pytest.fixture
def playerWorld(slotEnv, autonaApp, tmp_path, monkeypatch):
    """Yields `run(scenario)`, which plays one scenario from a fresh board
    and returns its record. The player database, the bad-word filter and the
    profanity setting are each scenario's own and put back afterwards."""
    slotEnv("tgame")
    db = PlayerDB.instance
    dbPath = Path(db.path)
    savedFile = dbPath.read_bytes() if dbPath.exists() else None
    with db.lock:
        savedDatabase = db.database
    savedSetting = SettingsManager.Get("general.profanity_filter", None)
    SettingsManager.Set("general.profanity_filter", True)

    def censor(words):
        directory = tmp_path / f"filter{len(list(tmp_path.iterdir()))}"
        ngword = directory / "ngword"
        ngword.mkdir(parents=True)
        if words:
            (ngword / "common.txt").write_text("\n".join(words),
                                               encoding="utf-16")
        result = BadWordFilter(ngwordDir=str(ngword),
                               userDataDir=str(directory / "user_data"))
        result.Initialize()
        return result

    def run(scenario):
        with db.lock:
            db.database = {tag: copy.deepcopy(DB_PLAYERS[tag])
                           for tag in scenario["db"]}
        db.SaveDB()
        db.NotifyChanged()
        monkeypatch.setattr(BadWordFilter, "instance",
                            censor(scenario.get("words")))
        autonaApp.settle()

        world = _World(autonaApp, scenario)
        try:
            record = {"initial": world.snapshot(), "steps": []}
            for step in scenario["steps"]:
                with Cycles(world.root) as cycles, \
                        _StatsTriggers(world.board) as stats, \
                        Writes(world.root) as writes:
                    result = _outcome(lambda: _run(world, step))
                    autonaApp.settle()
                entry = {"op": step["op"], "result": result,
                         "cycles": cycles.count,
                         "calls": compact(writes.calls),
                         "stats": stats.counts}
                entry.update(world.snapshot())
                record["steps"].append(entry)
            return record
        finally:
            world.close()

    try:
        yield run
    finally:
        with db.lock:
            db.database = savedDatabase
        if savedFile is None:
            dbPath.unlink(missing_ok=True)
        else:
            dbPath.write_bytes(savedFile)
        db.NotifyChanged()
        if savedSetting is None:
            SettingsManager.Unset("general.profanity_filter")
        else:
            SettingsManager.Set("general.profanity_filter", savedSetting)
        autonaApp.settle()


def _save(name, record):
    golden = {}
    if GOLDEN_PATH.exists():
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden[name] = record
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")


# ── the recording, replayed through what the card and the board called ───

# The steps whose code S3e-3 did not touch: their writes must match one for
# one. Everything else is compared per player slot, runs collapsed.
EXACT_OPS = ("typed", "chars", "swap")


def _assertStep(name, index, actual, expected):
    where = (name, index, expected["op"])
    expected = Expected(name, index, expected)
    if expected["op"] in EXACT_OPS:
        assert actual["calls"] == expected["calls"], where
    else:
        actualCalls = {path: collapsed(calls) for path, calls
                       in byPlayer(actual["calls"]).items()}
        expectedCalls = {path: collapsed(calls) for path, calls
                         in byPlayer(expected["calls"]).items()}
        assert actualCalls == expectedCalls, where
    # Every recorded field but what the cards showed ("shown"): no card is
    # left to show it since ADR-005 S4ac-3.
    for field in ("result", "cycles", "stats", "state", "keyOrder", "slots",
                  "db", "stateDigest"):
        assert actual.get(field) == expected.get(field), (where, field)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"]
                                                     for s in SCENARIOS])
def test_recorded_player_loads_publish_the_recorded_state(
        playerWorld, scenario):
    record = playerWorld(scenario)
    if RECORD:
        _save(scenario["name"], record)
        return
    expected = GOLDEN[scenario["name"]]
    initial = {k: v for k, v in expected["initial"].items() if k != "shown"}
    assert record["initial"] == initial, scenario["name"]
    assert [s["op"] for s in record["steps"]] \
        == [s["op"] for s in expected["steps"]]
    for index, (actual, wanted) in enumerate(
            zip(record["steps"], expected["steps"])):
        _assertStep(scenario["name"], index, actual, wanted)


# ── the set's players are loaded through the core ────────────────────────

SRC = Path(__file__).resolve().parent.parent / "src"

def _methods(module, cls):
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {item.name: item for item in node.body
                    if isinstance(item, ast.FunctionDef)}
    raise AssertionError(f"{cls} not in {module}")


def _attributeCalls(node):
    return {call.func.attr for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)}


def test_the_set_s_players_are_loaded_through_the_core():
    # A set's entrants and a one-player payload are core's since ADR-005
    # S3e-7 (SetFollowService.ApplySetPayload); the board that loaded them
    # (and then only handed them over, until S4x) went in S4ac-3. They load
    # through PlayerService.ApplyData, not a card's SetData, and save no
    # card's player.
    follow = _methods("core/SetFollowService.py", "SetFollowService")
    for name in ("_ApplyEntrants", "_ApplySinglePlayer"):
        calls = _attributeCalls(follow[name])
        assert "SetData" not in calls, name
        assert "SavePlayerToDB" not in calls, name
    assert "ApplyData" in _attributeCalls(follow["_ApplyPlayer"])
