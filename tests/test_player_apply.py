"""Loading a player into a slot is core's (ADR-005 S3e-3, S2f).

Until S3e-3 the card's SetData was the one place a player's data met the
slot: it set the line edits' text and emitted their editingFinished, picked
the country / region / character / skin / variant rows on its combos, and
the state followed from the signals. Every way in -- a set's entrants, the
Web's player edit, a tag lookup, the name completer, the PLAYERS editor --
went through it, so none of them could run without the widgets.
`PlayerService.ApplyData` is that now, on the state alone (the DB merge,
the bad-word filter, the location and character lookups); the card asks
for it and renders the slot like any other write. The card's own Clear and
SavePlayerToDB went the same way (`PlayerService.ClearSlot` /
`SavePlayerToDB`).

The state, the save cycles and local_players.json are external contracts,
so the move is pinned against a recording made through those entry points
on the last tree that had the widget implementation (ae922c24):

    AUTONA_RECORD_PLAYER_APPLY=1 python -m pytest tests/test_player_apply.py -k recorded

(on that checkout, with this file and tests/fixtures/player_apply copied
in). The scenarios are tests/fixtures/player_apply/scenarios.py; every step
records the outcome, the save cycles, the statistics triggers, every state
write under the board (or the editor), the whole subtree afterwards (key
order included), what each card shows and the player database file.
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

from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtWidgets import QApplication, QLineEdit

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
    compact, effect, normalise)

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
    own scalars (the upset factor) for a board, all of it for the editor."""
    with StateManager.lock:
        value = deep_clone(StateManager.Get(root) or {})
    exact = normalise(encode(value), app.dir)
    shown = value
    if root.startswith("score."):
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
    """The board (or the PLAYERS editor) a scenario drives."""

    def __init__(self, app, scenario):
        self.app = app
        self.scenario = scenario
        self.board = None
        self.editor = None
        if scenario["board"] is None:
            self.root = f"{schema.PLAYER_EDITOR_ROOT}.1"
            self.editor = _buildEditor(self.root)
        else:
            self.board = app.board(scenario["board"])
            self.board.playerNumber.setValue(scenario.get("players", 1))
            self.board.charNumber.setValue(3)
            app.settle()
            self.root = self.board.statePrefix

    @property
    def cards(self):
        if self.editor is not None:
            return [self.editor]
        return list(self.board.playerWidgets)

    def card(self, slot):
        if slot == "editor":
            return self.editor
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
        if self.editor is not None:
            editor = self.editor
            self.editor = None
            editor.setParent(None)
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(editor,
                                              QEvent.Type.DeferredDelete)
            StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        self.app.settle()


def _buildEditor(path):
    """The PLAYERS panel's editor card, as AutonaPlayerBrowserWidget builds
    it (three character rows)."""
    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
    editor = AutonaScoreboardPlayerWidget(index=1, teamNumber=1, path=path)
    editor.SetCharactersPerPlayer(3)
    QApplication.processEvents()
    return editor


def _row(combo, enName):
    model = combo.model()
    for row in range(model.rowCount()):
        data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("en_name") == enName:
            return row
    raise AssertionError(f"no row for {enName!r}")


def _run(world, step, web=False):
    """One step of a scenario, through the entry point it names. `web`: a
    tag lookup through the Web's operation instead of the board's (the
    PLAYERS panel's), its "OK" / "ERROR" read as the board's True / False."""
    op = step["op"]
    board = world.board
    if op == "set":
        data = step["data"]
        if isinstance(data, str):
            # The PLAYERS panel hands the editor the DB's own record.
            data = PlayerDB.instance.Get(data)
        else:
            data = copy.deepcopy(data)
        return world.card(step["slot"]).SetData(
            data, dontLoadFromDB=not step["loadFromDB"], clear=step["clear"],
            no_mains=step["no_mains"])
    if op == "clear":
        return world.card(step["slot"]).Clear(no_mains=step["no_mains"])
    if op == "save":
        return world.card(step["slot"]).SavePlayerToDB()
    if op == "typed":
        with StateManager.SaveBlock():
            PlayerService(world.card(step["slot"]).path).PublishCharacterSlots(
                [CharacterSlot(**slot) for slot in step["slots"]])
        return None
    if op == "pick":
        _element, character, color, variant = \
            world.card(step["slot"]).character_elements[step["index"] - 1]
        character.setCurrentIndex(_row(character, step["character"]))
        color.setCurrentIndex(step["skin"])
        variant.setCurrentIndex(_row(variant, step["variant"]))
        return None
    if op == "chars":
        board.charNumber.setValue(step["count"])
        return None
    if op == "tag":
        if web:
            answer = WebOps.load_player_from_tag(
                board.scoreboardNumber, step["tag"], step["team"],
                step["player"], step.get("no_mains", False))
            return {"OK": True, "ERROR": False}[answer]
        return board.LoadPlayerFromTag(step["tag"], step["team"],
                                       step["player"],
                                       step.get("no_mains", False))
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
    """Yields `run(scenario, web=False)`, which plays one scenario from a
    fresh board and returns its record (`web`: see _run). The player database, the bad-word filter and
    the profanity setting are each scenario's own and put back afterwards."""
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

    def run(scenario, web=False):
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
                    result = _outcome(lambda: _run(world, step, web))
                    autonaApp.settle()
                entry = {"op": step["op"], "result": result,
                         "cycles": cycles.count,
                         "calls": compact(writes.calls)}
                if world.board is not None:
                    entry["stats"] = stats.counts
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
EXACT_OPS = ("typed", "pick", "chars", "swap")


def _assertStep(name, index, actual, expected):
    where = (name, index, expected["op"])
    expected = Expected(name, index, expected)
    if expected.get("callsByEffect"):
        assert effect(actual["calls"]) == effect(expected["calls"]), where
    elif expected["op"] in EXACT_OPS:
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


def test_the_webs_tag_lookup_publishes_what_the_boards_does(playerWorld):
    """The Web's tag lookup (legacy load_player_from_tag / Socket.IO) is
    AutonaWebOperations' since ADR-005 S3e-4; it was the board's
    LoadPlayerFromTag, which the recording went through. Same state, same
    save cycles, same statistics triggers -- the swap step included (the
    slot is resolved from the state's teamsSwapped)."""
    if RECORD:
        pytest.skip("recording")
    scenario = next(s for s in SCENARIOS if s["name"] == "load from a tag")
    record = playerWorld(scenario, web=True)
    expected = GOLDEN[scenario["name"]]
    assert record["initial"] == expected["initial"]
    for index, (actual, wanted) in enumerate(
            zip(record["steps"], expected["steps"])):
        _assertStep(scenario["name"], index, actual, wanted)


# ── the card asks, the core writes ───────────────────────────────────────

SRC = Path(__file__).resolve().parent.parent / "src"

# What a card's load / clear / save may no longer do: scan a combo's model
# for a row, put a value into a widget, or emit a widget's signal for its
# handler to publish. Showing what was written is the render's
# (_RenderSlot), which writes nothing.
WIDGET_FILLING = {
    "model", "rowCount", "item", "findData", "findText", "currentData",
    "setCurrentIndex", "setText", "setPlainText", "setValue", "setEditText",
    "emit",
}
CORE_BACKED = ("SetData", "Clear", "SavePlayerToDB", "_OnCompleterPick",
               "_ShowOwnOperation")


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


def test_a_cards_load_clear_and_save_only_ask_the_core():
    methods = _methods("AutonaScoreboardPlayerWidget.py",
                       "AutonaScoreboardPlayerWidget")
    for name in CORE_BACKED:
        assert name in methods, name
        assert _attributeCalls(methods[name]) & WIDGET_FILLING == set(), name
    assert {"ApplyData"} <= _attributeCalls(methods["SetData"])
    assert {"ClearSlot"} <= _attributeCalls(methods["Clear"])
    assert {"SavePlayerToDB"} <= _attributeCalls(methods["SavePlayerToDB"])
    # The country model and the bad-word filter were SetData's: the card
    # has no use for either any more.
    source = (SRC / "AutonaScoreboardPlayerWidget.py").read_text(
        encoding="utf-8")
    assert "AutonaCountryHelper" not in source
    assert "AutonaBadWordFilter" not in source


def test_the_board_loads_players_through_the_core():
    methods = _methods("AutonaScoreboardWidget.py", "AutonaScoreboardWidget")
    calls = _attributeCalls(methods["LoadPlayerFromTag"])
    assert "SetData" not in calls
    assert "SavePlayerToDB" not in calls
    assert "ApplyData" in _attributeCalls(methods["_ApplyPlayer"])
    # A set's entrants and a one-player payload are core's since ADR-005
    # S3e-7 (SetFollowService): the board's ChangeSetData hands it over.
    assert not {"_ApplyEntrants", "_ApplySinglePlayerData",
                "_WritePlayerSlot"} & set(methods)
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


# ── the PLAYERS editor stays off the boards (agent-pitfalls #7) ──────────

def test_the_editor_loads_clears_and_saves_without_touching_a_board(
        slotEnv, autonaApp, tmp_path, monkeypatch):
    slotEnv("tgame")
    board = autonaApp.board(1)
    autonaApp.settle()
    db = PlayerDB.instance
    dbPath = Path(db.path)
    savedFile = dbPath.read_bytes() if dbPath.exists() else None
    with db.lock:
        savedDatabase = db.database
        db.database = {"SP TAG": copy.deepcopy(DB_PLAYERS["SP TAG"])}
    with StateManager.lock:
        before = deep_clone(StateManager.Get("score"))
    editorRoot = f"{schema.PLAYER_EDITOR_ROOT}.1"
    editor = _buildEditor(editorRoot)
    try:
        with Writes("score") as writes:
            editor.SetData({"gamerTag": "TAG", "prefix": "SP", "id": 5,
                            "seed": 3})
            autonaApp.settle()
            assert StateManager.Get(f"{editorRoot}.real_name") == "Real Tag"
            assert editor._w(schema.PLAYER_REAL_NAME_KEY, QLineEdit).text() \
                == "Real Tag"
            editor.SavePlayerToDB()
            editor.Clear()
            autonaApp.settle()
        assert writes.calls == []
        with StateManager.lock:
            assert StateManager.Get("score") == before
        assert StateManager.Get(f"{editorRoot}.{schema.PLAYER_TAG_KEY}") \
            == ""
        assert board.team1playerWidgets[0]._w(
            schema.PLAYER_TAG_KEY, QLineEdit).text() == ""
    finally:
        editor.setParent(None)
        editor.deleteLater()
        QCoreApplication.sendPostedEvents(editor, QEvent.Type.DeferredDelete)
        StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        with db.lock:
            db.database = savedDatabase
        if savedFile is None:
            dbPath.unlink(missing_ok=True)
        else:
            dbPath.write_bytes(savedFile)
        db.NotifyChanged()
        autonaApp.settle()
