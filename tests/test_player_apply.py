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
player database file.
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

from qtpy.QtWidgets import QLineEdit

from src import AutonaWebOperations as WebOps
from src.core import StateSchema as schema
from src.core.BadWordFilter import BadWordFilter
from src.core.DictHelper import deep_clone
from src.core.PlayerDB import PlayerDB
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
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
    for card in cards:
        with StateManager.lock:
            value = deep_clone(StateManager.Get(card.path) or {})
        result[card.path] = digest(normalise(encode(value), app.dir))
    return result


def _comboCode(combo):
    data = combo.currentData()
    return data.get("code") if isinstance(data, dict) else None


def _shown(card):
    """What one card shows: its fields, and its character rows as the
    identifiers a pick hands over."""
    shown = {field: card._w(field, QLineEdit).text()
             for field in schema.PLAYER_LINE_EDIT_KEYS}
    shown[schema.PLAYER_NOTE_KEY] = card.custom_textbox.toPlainText()
    shown[schema.PLAYER_SEED_KEY] = card._w(schema.PLAYER_SEED_KEY).value()
    shown["country"] = _comboCode(card._w(schema.PLAYER_COUNTRY_KEY))
    shown["region"] = _comboCode(card._w(schema.PLAYER_STATE_KEY))
    shown["character"] = []
    for _element, character, color, variant in card.character_elements:
        slot = card._CharacterSlot(character, color, variant)
        shown["character"].append(
            [slot.character, slot.skin, slot.variant, slot.text])
    return shown


def _dbText():
    path = Path(PlayerDB.instance.path)
    if not path.exists():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


# ── the world a scenario runs in ─────────────────────────────────────────

class _World:
    """The board a scenario drives."""

    def __init__(self, app, scenario):
        self.app = app
        self.scenario = scenario
        self.board = app.board(scenario["board"])
        self.board.playerNumber.setValue(scenario.get("players", 1))
        self.board.charNumber.setValue(3)
        app.settle()
        self.root = self.board.statePrefix

    @property
    def cards(self):
        return list(self.board.playerWidgets)

    def card(self, slot):
        team, player = slot
        widgets = (self.board.team1playerWidgets if team == 1
                   else self.board.team2playerWidgets)
        return widgets[player - 1]

    def snapshot(self):
        entry = _subtree(self.app, self.root)
        entry["slots"] = _slots(self.app, self.cards)
        entry["shown"] = {card.path: _shown(card) for card in self.cards}
        entry["db"] = _dbText()
        return entry

    def close(self):
        self.app.settle()


def _run(world, step):
    """One step of a scenario, through the entry point it names."""
    op = step["op"]
    board = world.board
    if op == "set":
        return world.card(step["slot"]).SetData(
            copy.deepcopy(step["data"]), dontLoadFromDB=not step["loadFromDB"],
            clear=step["clear"], no_mains=step["no_mains"])
    if op == "clear":
        return world.card(step["slot"]).Clear(no_mains=step["no_mains"])
    if op == "save":
        return world.card(step["slot"]).player.SavePlayerToDB()
    if op == "typed":
        with StateManager.SaveBlock():
            PlayerService(world.card(step["slot"]).path).PublishCharacterSlots(
                [CharacterSlot(**slot) for slot in step["slots"]])
        return None
    if op == "chars":
        board.charNumber.setValue(step["count"])
        return None
    if op == "tag":
        # The Web's tag lookup (the board's LoadPlayerFromTag when this was
        # recorded): its "OK" / "ERROR" are what that answered True / False.
        answer = WebOps.load_player_from_tag(
            board.scoreboardNumber, step["tag"], step["team"],
            step["player"], step.get("no_mains", False))
        return {"OK": True, "ERROR": False}[answer]
    if op == "update":
        # /api/v1's player PATCH: the board's ApplyPlayerData when this was
        # recorded, the Web's operation since ADR-005 S3e-4.
        return WebOps.update_player(board.scoreboardNumber, step["team"],
                                    step["player"],
                                    copy.deepcopy(step["data"]))
    if op == "setdata":
        return board.ChangeSetData(copy.deepcopy(step["payload"]))
    if op == "swap":
        return board.SwapTeams()
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


# ── the recording, replayed through the card and the board ───────────────

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
    for field in ("result", "cycles", "stats", "state", "keyOrder", "slots",
                  "shown", "db", "stateDigest"):
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
    initial = {k: v for k, v in record["initial"].items()}
    assert initial == expected["initial"], scenario["name"]
    assert [s["op"] for s in record["steps"]] \
        == [s["op"] for s in expected["steps"]]
    for index, (actual, wanted) in enumerate(
            zip(record["steps"], expected["steps"])):
        _assertStep(scenario["name"], index, actual, wanted)


# ── the card asks, the core writes ───────────────────────────────────────

SRC = Path(__file__).resolve().parent.parent / "src"

# What a card's load / clear may no longer do: scan a combo's model
# for a row, put a value into a widget, or emit a widget's signal for its
# handler to publish. Showing what was written is the render's
# (_RenderSlot), which writes nothing.
WIDGET_FILLING = {
    "model", "rowCount", "item", "findData", "findText", "currentData",
    "setCurrentIndex", "setText", "setPlainText", "setValue", "setEditText",
    "emit",
}
CORE_BACKED = ("SetData", "Clear", "_OnCompleterPick", "_ShowOwnOperation")


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


def test_a_cards_load_and_clear_only_ask_the_core():
    methods = _methods("AutonaScoreboardPlayerWidget.py",
                       "AutonaScoreboardPlayerWidget")
    for name in CORE_BACKED:
        assert name in methods, name
        assert _attributeCalls(methods[name]) & WIDGET_FILLING == set(), name
    assert {"ApplyData"} <= _attributeCalls(methods["SetData"])
    assert {"ClearSlot"} <= _attributeCalls(methods["Clear"])
    # The country model and the bad-word filter were SetData's: the card
    # has no use for either any more.
    source = (SRC / "AutonaScoreboardPlayerWidget.py").read_text(
        encoding="utf-8")
    assert "AutonaCountryHelper" not in source
    assert "AutonaBadWordFilter" not in source


def test_the_board_loads_players_through_the_core():
    methods = _methods("AutonaScoreboardWidget.py", "AutonaScoreboardWidget")
    # A set's entrants and a one-player payload are core's since ADR-005
    # S3e-7 (SetFollowService): the board's ChangeSetData hands it over. The
    # tag lookup is the Web's (AutonaWebOperations.load_player_from_tag).
    assert not {"_ApplyEntrants", "_ApplySinglePlayerData", "_WritePlayerSlot",
                "LoadPlayerFromTag", "_ApplyPlayer",
                "_PlayerSlotPath"} & set(methods)
    assert "ApplySetPayload" in _attributeCalls(methods["ChangeSetData"])
    follow = _methods("core/SetFollowService.py", "SetFollowService")
    for name in ("_ApplyEntrants", "_ApplySinglePlayer"):
        calls = _attributeCalls(follow[name])
        assert "SetData" not in calls, name
        assert "SavePlayerToDB" not in calls, name
    assert "ApplyData" in _attributeCalls(follow["_ApplyPlayer"])


def test_no_card_lock_is_left():
    """`dataLock` serialised SetData against the card's own handlers -- all
    of them on the GUI thread. The load is one core write under the state's
    lock now."""
    for module in ("AutonaScoreboardPlayerWidget.py",
                   "AutonaBasePlayerWidget.py", "AutonaScoreboardWidget.py"):
        tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
        names = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)}
        assert not names & {"dataLock", "_bulk_updating"}, module
