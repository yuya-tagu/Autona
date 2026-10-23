"""Player cards show what the state holds for their slot (ADR-005 S2a).

Until S2a a player card was only ever a writer: what it showed was whatever
the operator, a set load or another card's SwapWith had typed into it, and
the state followed. ADR-005 S2 moves the slot's source of truth to the state
(the swap / clear operations become core operations that never touch a
widget), which needs the opposite direction first: a card that renders its
slot from the state, writing nothing while it does. That is all S2a adds --
the writers are unchanged -- so it is pinned two ways:

- **nothing the state receives changed.** The board operations an operator
  runs (fill, swap, move a player, reset, clear, a set load, reset-all) were
  recorded against the last pre-S2a tree (bd0fa50b), through the widgets'
  public behaviour only, and are replayed here: the board's whole subtree
  after every step (key order included), the save cycles each step took,
  and what 反映 then published. A render that wrote, or that changed a
  widget an operation goes on to read, would show up as a difference.

      AUTONA_RECORD_BOARD_OPERATIONS=1 python -m pytest tests/test_player_card_render.py

  (on a pre-migration checkout with this file and fixtures/board_operations
  copied in). Re-record only when the fixture games or the country data
  change.

  S2b (the losers flag's " [L]" moved from the cards into core) extended the
  steps and re-recorded them on the last pre-S2b tree (76a740fb; the
  original fourteen came out identical to the bd0fa50b recording). The
  recording is still the *pre*-migration truth: what S2b changed on purpose
  is listed step by step in ``S2B_CHANGES`` and applied to it before the
  comparison, so every other field of every step still has to match.
- **the card shows its slot after every one of those steps**, and a slot
  written straight into the state -- which is what S2c's core swap will do --
  reaches the card without a single write on the way back.

The fixture world (games, custom flags, locale) is the S1d recording's
(tests/test_player_slot_publish.py), so the character combos have real rows,
skins (one of which is a different character -- Dr. Mario) and variants.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import orjson
import pytest

from qtpy.QtCore import QCoreApplication, QEvent, QSignalBlocker, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import (QCheckBox, QComboBox, QLineEdit, QMessageBox,
                            QWidget)
from shiboken6 import Shiboken

from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from src.core import StateSchema as schema
from src.core.DictHelper import deep_clone
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.StateManager import StateManager
# The S1d world: the fixture games under the recording's locale. Imported so
# both recordings are made in exactly the same one.
from tests.test_player_slot_publish import slotEnv  # noqa: F401  (fixture)
from tests.fixtures.player_slots.recording import digest, encode

RECORD = os.environ.get("AUTONA_RECORD_BOARD_OPERATIONS") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures"
               / "board_operations" / "golden.json")
GOLDEN = None if RECORD else json.loads(
    GOLDEN_PATH.read_text(encoding="utf-8"))

BOARD = 2   # the team board: two players a side, team names, move up/down
PREFIX = f"score.{BOARD}"


# ── observing a board ────────────────────────────────────────────────────

class _Cycles:
    """held_state_updated notifications for the board, i.e. save cycles."""

    def __init__(self):
        self.count = 0

    def _onHeld(self, prefix):
        if prefix == PREFIX:
            self.count += 1

    def __enter__(self):
        StateManager.observers.held_state_updated.subscribe(self._onHeld)
        return self

    def __exit__(self, *exc):
        StateManager.observers.held_state_updated.unsubscribe(self._onHeld)
        return False


class _Published:
    """What the delta stream carried: one entry per state_updated, and the
    number of state_big_change notifications (a delta over 100 rows goes out
    as one of those instead)."""

    def __init__(self):
        self.deltas = []
        self.bigChanges = 0

    def _onDelta(self, payload):
        self.deltas.append(payload.get("delta", []))

    def _onBigChange(self):
        self.bigChanges += 1

    def __enter__(self):
        StateManager.observers.state_updated.subscribe(self._onDelta)
        StateManager.observers.state_big_change.subscribe(self._onBigChange)
        return self

    def __exit__(self, *exc):
        StateManager.observers.state_updated.unsubscribe(self._onDelta)
        StateManager.observers.state_big_change.unsubscribe(
            self._onBigChange)
        return False

    def encoded(self, sortKeys=False):
        # The flat delta rows carry Python types (`type`: <class 'str'>);
        # named, they are as comparable as the rest of the row. `sortKeys`
        # gives the same rows with every dict's keys sorted: what was
        # published, regardless of the order a new slot's keys were written
        # in.
        option = orjson.OPT_NON_STR_KEYS
        if sortKeys:
            option |= orjson.OPT_SORT_KEYS
        return orjson.dumps(
            self.deltas, option=option,
            default=lambda o: getattr(o, "__name__", repr(o))).decode()


@contextmanager
def _noWrites():
    """Every state write attempted inside the block, as (operation, key)."""
    attempts = []
    originals = {name: getattr(StateManager, name)
                 for name in ("Set", "Unset", "SetIfChanged")}

    def trap(name):
        def recording(key, *args, **kwargs):
            attempts.append((name, key))
            return originals[name](key, *args, **kwargs)
        return recording

    patches = [patch.object(StateManager, name, side_effect=trap(name))
               for name in originals]
    for p in patches:
        p.start()
    try:
        yield attempts
    finally:
        for p in patches:
            p.stop()


def _normalise(text, app):
    """Absolute runtime paths, should any reach the state, made portable."""
    root = str(app.dir)
    for spelling in (root, root.replace("\\", "/"),
                     root.replace("\\", "\\\\")):
        text = text.replace(spelling, "<RUNTIME>")
    return text


def _digestAssets(value):
    """The board subtree with every `assets` table as a digest: those are
    the bulk of it and are covered byte for byte by the state digest."""
    if isinstance(value, dict):
        return {key: (digest(item) if key == "assets" else _digestAssets(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_digestAssets(item) for item in value]
    return value


def _boardState(app):
    with StateManager.lock:
        value = deep_clone(StateManager.Get(PREFIX) or {})
    exact = _normalise(encode(value), app)
    readable = json.loads(_normalise(orjson.dumps(
        _digestAssets(value),
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS).decode(), app))
    return {"state": readable, "stateDigest": digest(exact)}


# ── the operations ───────────────────────────────────────────────────────

def _losers(board, team):
    column = board.team1column if team == 1 else board.team2column
    return column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)


def _teamName(board, team, text):
    column = board.team1column if team == 1 else board.team2column
    edit = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
    edit.setText(text)
    edit.editingFinished.emit()


def _rename(card, text):
    """The operator typing a new tag into a card and leaving the field."""
    edit = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
    edit.setText(text)
    edit.editingFinished.emit()


def _confirmed(operation):
    """Run an operation that asks first, answering Yes."""
    with patch.object(QMessageBox, "question",
                      return_value=QMessageBox.StandardButton.Yes):
        operation()


PLAYERS = {
    (1, 0): {"gamerTag": "ALPHA", "prefix": "SP", "name": "Real Alpha",
             "twitter": "alpha_tw", "custom_textbox": "first\\nnote",
             "country_code": "JP", "state_code": "13", "seed": 3,
             "city": "Shibuya", "avatar": "avatars/alpha.png",
             # skin 2 of Mario is Dr. Mario, a different character: the
             # slot then carries Dr. Mario's name and en_name.
             "mains": {"tgame": [["Mario", 2, "EX"], ["Luigi", 1]]}},
    (1, 1): {"gamerTag": "BETA", "country_code": "US", "state_code": "CA",
             "seed": 7, "mains": {"tgame": [["Peach", 1, "Alt"]]}},
    (2, 0): {"gamerTag": "GAMMA", "prefix": "GG", "country_code": "GB",
             "state_code": "CON", "seed": 12,
             "mains": {"tgame": [["Bowser", 0]]}},
    (2, 1): {"gamerTag": "DELTA"},
}


def _fill(board, team):
    widgets = board.team1playerWidgets if team == 1 \
        else board.team2playerWidgets
    for index, card in enumerate(widgets):
        card.SetData(dict(PLAYERS[(team, index)]), dontLoadFromDB=True)


def _steps(board):
    """What an operator (or a set load) does to a board, in order."""
    return [
        ("two players, two characters", lambda: (
            board.playerNumber.setValue(2), board.charNumber.setValue(2))),
        ("fill team 1", lambda: _fill(board, 1)),
        ("fill team 2", lambda: _fill(board, 2)),
        ("team names", lambda: (_teamName(board, 1, "Alpha"),
                                _teamName(board, 2, "Bravo"))),
        ("score", lambda: (board.CommandScoreChange(0, 2),
                           board.CommandScoreChange(1, 1))),
        ("losers", lambda: _losers(board, 2).setChecked(True)),
        ("swap teams", board.SwapTeams),
        ("move a player down",
         lambda: board.team1playerWidgets[0].btMoveDown.click()),
        ("swap teams back", board.SwapTeams),
        ("pick a character", lambda: board.team2playerWidgets[1]
         .character_elements[0][1].setCurrentIndex(5)),
        ("reset match", board.ResetMatch),
        ("clear players", board.CommandClearAll),
        ("load a set", lambda: board.ChangeSetData({
            "round_name": "Winners Final", "team1score": 1,
            "team2score": 0, "overwrite": True,
            "entrants": [
                [{"gamerTag": "E1", "country_code": "JP",
                  "state_code": "27",
                  "mains": {"tgame": [["Zelda", 0]]}}],
                [{"gamerTag": "E2", "prefix": "PX", "seed": 4}]]})),
        ("reset all inputs", lambda: _confirmed(board.ResetAllInputs)),
        # The losers flag and the " [L]" it puts on every name of its team
        # (S2b moved that suffix from the cards to core). Recorded on the
        # pre-S2b tree (76a740fb).
        ("two players again", lambda: (
            board.playerNumber.setValue(2), _fill(board, 1), _fill(board, 2))),
        ("losers on a filled team", lambda: _losers(board, 1).setChecked(True)),
        ("rename on a losers team",
         lambda: _rename(board.team1playerWidgets[0], "ALPHA2")),
        ("grow the roster on a losers team",
         lambda: board.playerNumber.setValue(3)),
        ("back to one player", lambda: board.playerNumber.setValue(1)),
        ("load a losers set that grows the roster", lambda: board.ChangeSetData({
            "team1losers": False, "team2losers": True, "overwrite": True,
            "entrants": [
                [{"gamerTag": "L1"}, {"gamerTag": "L2", "prefix": "LP"}],
                [{"gamerTag": "W1", "prefix": "WP"}, {"gamerTag": "W2"}]]})),
        ("swap teams with a losers side", board.SwapTeams),
        ("load the losers flags while swapped", lambda: board.ChangeSetData({
            "team1losers": True, "team2losers": False})),
    ]


def _runSteps(app, board, afterEach=None):
    recorded = []
    for name, step in _steps(board):
        with _Cycles() as cycles:
            step()
            app.settle()
        entry = {"step": name, "cycles": cycles.count}
        entry.update(_boardState(app))
        if afterEach is not None:
            afterEach(name)
        with _Published() as published:
            StateManager.CommitHeld(PREFIX)
            app.settle()
        entry["published"] = [len(published.deltas), published.bigChanges,
                              digest(_normalise(published.encoded(), app))]
        entry["publishedRows"] = digest(
            _normalise(published.encoded(sortKeys=True), app))
        recorded.append(entry)
    return recorded


@pytest.fixture
def board(slotEnv, autonaApp):
    slotEnv("tgame")
    return autonaApp.board(BOARD)


# What ADR-005 S2b changed on purpose, against the pre-S2b recording. Each
# entry replaces the listed fields of that step; `dropped` are paths taken
# out of the recorded `state`. Everything not listed must still match.
S2B_CHANGES = {
    # The flag and the " [L]" on each name of its team are one save cycle
    # (AutonaActions.SetTeamLosers); they were the flag plus one cycle per
    # player card (SetLosers). Same state, same delta published by 反映.
    "losers": {"cycles": 1},
    "losers on a filled team": {"cycles": 1},
    # A card added to a losers team no longer publishes " [L]" for a player
    # it does not have yet (_AdoptTeamLosers is gone). Its name gets the
    # suffix once one is typed or loaded -- as on a team that is not losers,
    # where a new blank card has never published a name.
    "grow the roster on a losers team": {
        "dropped": ["team.1.player.3.mergedName",
                    "team.1.player.3.mergedOnlyName"],
        "stateDigest": "dd980b0f6c120fc5",
        "published": [1, 0, "4819ec28dc08861c"],
        "publishedRows": "342100cde21357ad"},
    # Key order only (state and publishedRows match the recording): the new
    # card on the losers team writes its names once, after its entrant's
    # tag, instead of " [L]" first and the tag over it.
    "load a losers set that grows the roster": {
        "stateDigest": "66843402bfc798d6",
        "published": [1, 0, "218d15dc5cab7632"]},
    # The cards "back to one player" removed were still connected to the
    # LOSERS box until their deleteLater ran; this harness never runs it,
    # so the swap's toggles made them re-create their gone slots with just
    # a merged name. In the app that needs a toggle in the same event-loop
    # turn as the removal. The connection itself is gone with S2b.
    "swap teams with a losers side": {
        "dropped": ["team.1.player.3", "team.2.player.3"],
        "stateDigest": "ee445bb1781e88f3",
        "published": [1, 0, "d7765079abe64354"],
        "publishedRows": "a3834ea0c315eaad"},
    "load the losers flags while swapped": {
        "dropped": ["team.1.player.3", "team.2.player.3"],
        "stateDigest": "7aac9cae3ee8607f"},
}


def _afterS2b(entry):
    """A recorded step with S2B_CHANGES applied."""
    change = S2B_CHANGES.get(entry["step"])
    if change is None:
        return entry
    expected = json.loads(json.dumps(entry))
    for path in change.get("dropped", ()):
        *parents, leaf = path.split(".")
        node = expected["state"]
        for key in parents:
            node = node[key]
        del node[leaf]
    expected.update({field: value for field, value in change.items()
                     if field != "dropped"})
    return expected


def test_board_operations_publish_the_recorded_state(board, autonaApp):
    """The state after every operation, its save cycles and what 反映 then
    published are what the pre-migration tree produced, apart from what S2b
    changed on purpose -- and every card shows its slot after every one of
    them."""
    mismatches = {}

    def check(step):
        if not RECORD:
            mismatches[step] = _cardMismatches(board)
            mismatches[step].update(_losersMismatches(board))

    recorded = _runSteps(autonaApp, board, afterEach=check)

    if RECORD:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(
            {"operations": recorded}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return
    golden = [_afterS2b(entry) for entry in GOLDEN["operations"]]
    assert [e["step"] for e in recorded] == [e["step"] for e in golden]
    for actual, expected in zip(recorded, golden):
        assert actual["state"] == expected["state"], actual["step"]
        assert actual == expected, actual["step"]
    assert mismatches == {step: {} for step in mismatches}


# ── the card shows its slot ──────────────────────────────────────────────
#
# Read straight off the widgets and compared with the state, through the
# same value builders the card publishes with -- not through the render
# code, so a render that decided wrongly cannot also be the judge.

def _comboCode(combo):
    data = combo.currentData()
    return data.get("code") if isinstance(data, dict) else None


def _slotOf(card):
    with StateManager.lock:
        return deep_clone(StateManager.Get(card.path) or {})


def _shown(card):
    """What one card shows, keyed like its slot."""
    shown = {field: card._w(field, QLineEdit).text()
             for field in schema.PLAYER_LINE_EDIT_KEYS}
    shown[schema.PLAYER_NOTE_KEY] = card.custom_textbox.toPlainText()
    seed = card._w(schema.PLAYER_SEED_KEY).value()
    shown[schema.PLAYER_SEED_KEY] = seed
    shown["seedBadge"] = card.seedDisplay.text()
    shown[schema.PLAYER_BIRTHDAY_KEY] = card._w(
        schema.PLAYER_BIRTHDAY_KEY).isChecked()
    shown["countryCode"] = _comboCode(card._w(schema.PLAYER_COUNTRY_KEY,
                                              QComboBox))
    shown["regionCode"] = _comboCode(card._w(schema.PLAYER_STATE_KEY,
                                             QComboBox))
    service = PlayerService(card.path)
    shown["character"] = {
        index + 1: service.CharacterValue(
            card._CharacterSlot(character, color, variant))
        for index, (_element, character, color, variant)
        in enumerate(card.character_elements)}
    return shown


# What an empty character row publishes (no character, no skin, no variant):
# also what a card shows for a row its slot has no entry for.
BLANK_CHARACTER = {"name": "", "assets": {}, "skin": -1, "variant": {}}


def _expected(slot, card):
    seed = slot.get(schema.PLAYER_SEED_KEY) or 0
    expected = {field: slot.get(field) or ""
                for field in schema.PLAYER_TEXT_KEYS}
    expected[schema.PLAYER_SEED_KEY] = seed
    expected["seedBadge"] = f"Seed: {seed}" if seed > 0 else ""
    expected[schema.PLAYER_BIRTHDAY_KEY] = bool(
        slot.get(schema.PLAYER_BIRTHDAY_KEY))
    expected["countryCode"] = (slot.get(schema.PLAYER_COUNTRY_KEY)
                               or {}).get("code")
    expected["regionCode"] = (slot.get(schema.PLAYER_STATE_KEY)
                              or {}).get("code")
    # One per combo row the card has (the board's character count).
    characters = slot.get("character")
    if not isinstance(characters, dict):
        characters = {}
    expected["character"] = {}
    for number in range(1, len(card.character_elements) + 1):
        value = characters.get(number, characters.get(str(number)))
        expected["character"][number] = \
            value if isinstance(value, dict) else BLANK_CHARACTER
    return expected


def _losersMismatches(board):
    """{team: (box, state)} for a LOSERS box that is off its team's flag.

    The flag's other carrier, the " [L]" in the names, is in the recorded
    state itself.
    """
    result = {}
    for team in (1, 2):
        shown = _losers(board, team).isChecked()
        flag = bool(StateManager.Get(
            f"{PREFIX}.team.{team}.{schema.TEAM_LOSERS_KEY}", False))
        if shown != flag:
            result[f"team.{team}.{schema.TEAM_LOSERS_KEY}"] = (shown, flag)
    return result


def _cardMismatches(board):
    """{path: {field: (shown, state)}} for every card that is off."""
    result = {}
    for card in board.playerWidgets:
        shown = _shown(card)
        expected = _expected(_slotOf(card), card)
        off = {key: (shown[key], expected[key]) for key in expected
               if shown[key] != expected[key]}
        if off:
            result[card.path] = off
    return result


# The steps that are one operation in one save cycle today. The others are
# still a widget writing field by field (a SetData per card, a Clear per
# card): S2c/S2d move those writers into core, where an operation is one save
# block. The losers flag became one in S2b -- it was the flag plus one cycle
# per player card.
ONE_CYCLE_STEPS = ("losers", "swap teams", "move a player down",
                   "swap teams back", "reset match", "load a set",
                   "reset all inputs", "losers on a filled team",
                   "grow the roster on a losers team", "back to one player",
                   "load a losers set that grows the roster",
                   "swap teams with a losers side",
                   "load the losers flags while swapped")


def test_a_single_operation_is_a_single_save_cycle(board, autonaApp):
    if RECORD:
        pytest.skip("checked against the recording")
    recorded = {e["step"]: e["cycles"] for e in _runSteps(autonaApp, board)}
    assert {step: recorded[step] for step in ONE_CYCLE_STEPS} \
        == {step: 1 for step in ONE_CYCLE_STEPS}


# ── a slot written in the state reaches its card ─────────────────────────
#
# What S2c's core swap and S2d's core clear will do: write the slot without
# going through the card. Nothing below touches a widget before asserting
# on it.

def _publishSlot(path, name, sponsor, country, region, seed, characters):
    """Write a whole slot the way core will: PlayerService, one block."""
    from src.core.PlayerService import CharacterSlot
    service = PlayerService(path)
    with StateManager.SaveBlock():
        service.SetText(schema.PLAYER_TAG_KEY, name)
        service.SetText(schema.PLAYER_SPONSOR_KEY, sponsor)
        service.SetText(schema.PLAYER_REAL_NAME_KEY, f"{name} real")
        service.SetText(schema.PLAYER_TWITTER_KEY, f"{name}_tw")
        service.SetText(schema.PLAYER_NOTE_KEY, f"{name}\nnote")
        service.SetSeed(seed)
        service.SetBirthday(True)
        service.SetCountry(country)
        service.SetRegion(country, region)
        service.PublishCharacterSlots(
            [CharacterSlot(*slot) for slot in characters])


def _onePlayerTwoCharacters(board, app):
    board.charNumber.setValue(2)
    app.settle()
    StateManager.CommitHeld(PREFIX)
    app.settle()


def test_a_slot_written_in_the_state_is_shown_on_its_card(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    _onePlayerTwoCharacters(board, autonaApp)
    card = board.team1playerWidgets[0]

    with _Cycles() as cycles:
        _publishSlot(card.path, "STATE", "ST", "JP", "13", 9, [
            # Dr. Mario: Mario's skin 2, a different character.
            ("Mario", 2, "EX", ""),
            # A name typed by hand: no row of the game.
            (None, -1, None, "Typed Name")])
        written = cycles.count
        autonaApp.settle()
    assert (written, cycles.count) == (1, 1), \
        "the write is one cycle and rendering it adds none"

    assert _cardMismatches(board) == {}
    assert card._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "STATE"
    assert card.seedDisplay.text() == "Seed: 9"
    assert _comboCode(card._w(schema.PLAYER_STATE_KEY, QComboBox)) == "13"
    character, color, variant = card.character_elements[0][1:]
    assert character.currentData()["en_name"] == "Mario"
    assert color.currentIndex() == 2
    assert variant.currentData()["en_name"] == "EX"
    assert card.character_elements[1][1].currentText() == "Typed Name"


def test_rendering_writes_nothing(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    for step, operation in _steps(board)[:3]:   # two a side, filled
        operation()
        autonaApp.settle()

    # Knock every card off its slot without telling anyone, then render.
    regionOnly = set()
    for card in board.playerWidgets:
        for field in schema.PLAYER_LINE_EDIT_KEYS:
            edit = card._w(field, QLineEdit)
            with QSignalBlocker(edit):
                edit.setText("stale")
        with QSignalBlocker(card.custom_textbox):
            card.custom_textbox.setPlainText("stale")
        spin = card._w(schema.PLAYER_SEED_KEY)
        with QSignalBlocker(spin):
            spin.setValue(99)
        birthday = card._w(schema.PLAYER_BIRTHDAY_KEY, QCheckBox)
        with QSignalBlocker(birthday):
            birthday.setChecked(not birthday.isChecked())
        region = card._w(schema.PLAYER_STATE_KEY, QComboBox)
        if region.count() > 2:
            # Another region of the same country: only the region is off.
            with QSignalBlocker(region):
                region.setCurrentIndex(
                    1 if region.currentIndex() != 1 else 2)
            regionOnly.add(card.path)
        else:
            country = card._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
            with QSignalBlocker(country):
                country.setCurrentIndex(1)
        # The first row: a name typed by hand. The second: other rows.
        first, second = card.character_elements[:2]
        with QSignalBlocker(first[1]):
            first[1].setCurrentIndex(-1)
            first[1].setEditText("stale typed")
        for combo in second[1:]:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(1)
        card._renderedSlot = None

    knocked = _cardMismatches(board)
    assert regionOnly, "no card had a region to move"
    for card in board.playerWidgets:
        off = knocked.get(card.path, {})
        assert {schema.PLAYER_TAG_KEY, schema.PLAYER_NOTE_KEY,
                schema.PLAYER_SEED_KEY, schema.PLAYER_BIRTHDAY_KEY,
                "character"} <= off.keys(), card.path
        if card.path in regionOnly:
            assert "regionCode" in off and "countryCode" not in off
        else:
            assert "countryCode" in off
        shown, state = off["character"]
        assert shown[1] != state[1] and shown[2] != state[2], card.path

    with _Cycles() as cycles, _noWrites() as attempts:
        board._RenderPlayerCards()
        autonaApp.settle()

    assert attempts == []
    assert cycles.count == 0
    assert _cardMismatches(board) == {}


def test_two_slots_exchanged_in_the_state_follow_on_their_cards(
        board, autonaApp):
    """The precondition of S2c: the swap moves slots in the state and the
    cards follow."""
    if RECORD:
        pytest.skip("not part of the recording")
    _onePlayerTwoCharacters(board, autonaApp)
    left, right = board.team1playerWidgets[0], board.team2playerWidgets[0]
    left.SetData(dict(PLAYERS[(1, 0)]), dontLoadFromDB=True)
    right.SetData(dict(PLAYERS[(2, 0)]), dontLoadFromDB=True)
    autonaApp.settle()

    with StateManager.SaveBlock():
        leftSlot, rightSlot = _slotOf(left), _slotOf(right)
        StateManager.Set(left.path, rightSlot)
        StateManager.Set(right.path, leftSlot)
    autonaApp.settle()

    assert _cardMismatches(board) == {}
    assert left._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "GAMMA"
    assert right._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "ALPHA"
    assert right.seedDisplay.text() == "Seed: 3"
    assert _comboCode(right._w(schema.PLAYER_STATE_KEY, QComboBox)) == "13"

    # The name now on the card came with the slot: finishing an edit of
    # the tag without changing it is not a new player arriving, so nothing
    # derived from the name (merged name, images, the id) is re-written --
    # and the tag itself is what the state already holds, so neither is it.
    with _noWrites() as attempts:
        right._w(schema.PLAYER_TAG_KEY, QLineEdit).editingFinished.emit()
    assert attempts == []


def test_a_field_being_edited_is_left_alone(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    edit = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
    with QSignalBlocker(edit):
        edit.setText("typing")

    with patch.object(edit, "hasFocus", return_value=True):
        PlayerService(card.path).SetText(schema.PLAYER_TAG_KEY, "ELSEWHERE")
        autonaApp.settle()
        assert edit.text() == "typing"

    # Not marked as shown, so the next pass puts it right.
    board._RenderPlayerCards()
    assert edit.text() == "ELSEWHERE"


def test_one_render_pass_per_event_loop_turn(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    service = PlayerService(card.path)
    with patch.object(board, "_RenderPlayerCards",
                      wraps=board._RenderPlayerCards) as render:
        for name in ("ONE", "TWO", "THREE"):   # three save cycles
            service.SetText(schema.PLAYER_TAG_KEY, name)
        assert render.call_count == 0, "rendered inside the operation"
        autonaApp.settle()
    assert render.call_count == 1
    assert card._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "THREE"


def test_a_failing_board_sync_does_not_hold_the_cards_back(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    with patch.object(board, "_SyncSetFields",
                      side_effect=RuntimeError("sync failed")) as sync:
        PlayerService(card.path).SetText(schema.PLAYER_TAG_KEY, "SHOWN")
        autonaApp.settle()
    assert sync.called
    assert card._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "SHOWN"


# ── the operator's fields ────────────────────────────────────────────────
#
# Real focus, on the board's window shown offscreen: what is pinned is how
# Qt's own focus-out (editingFinished, then focusChanged) meets the render,
# so it is not simulated. Qt 6 finishes a line edit on focus-out whenever
# its text changed since the last finish -- a render's blocked setText
# included -- which each test asserts, so none of them can pass vacuously.

@contextmanager
def _onScreen(board, app):
    """The board's window (the scoreboard manager) shown and active, with
    this board the one it shows (AutonaScoreboardManager.SetBattleFormat)."""
    window = board.window()
    board.setVisible(True)
    window.show()
    window.activateWindow()
    app.settle()
    try:
        yield
    finally:
        # Activation goes to a stand-in before the window is hidden. The
        # offscreen platform keeps a hidden window as its focus window, so
        # the next test's activateWindow() would change nothing and no
        # field of the window could take focus again.
        standIn = QWidget()
        standIn.show()
        standIn.activateWindow()
        app.settle()
        window.hide()
        board.setVisible(False)
        standIn.hide()
        standIn.deleteLater()
        QCoreApplication.sendPostedEvents(standIn, QEvent.Type.DeferredDelete)
        app.settle()


def _focus(widget, app):
    widget.setFocus()
    app.settle()
    assert widget.isVisible() and widget.hasFocus(), \
        "the harness could not focus the field"


def _finishes(edit):
    finished = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))
    return finished


def test_tabbing_through_a_rendered_field_writes_nothing(board, autonaApp):
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    tag = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
    sponsor = card._w(schema.PLAYER_SPONSOR_KEY, QLineEdit)
    PlayerService(card.path).SetText(schema.PLAYER_TAG_KEY, "RENDERED")
    autonaApp.settle()
    assert tag.text() == "RENDERED"
    finished = _finishes(tag)

    with _onScreen(board, autonaApp):
        with _Cycles() as cycles, _noWrites() as attempts:
            _focus(tag, autonaApp)
            _focus(sponsor, autonaApp)

    assert finished == ["RENDERED"], "Qt no longer finishes a rendered field"
    assert attempts == []
    assert cycles.count == 0


def test_a_value_written_while_its_field_is_focused_shows_after_focus_out(
        board, autonaApp):
    """Scenario A: the operator sits in a field, the slot changes elsewhere,
    the operator leaves without typing. The new value is shown, and the old
    text the focus-out finishes is not written over it."""
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    service = PlayerService(card.path)
    tag = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
    sponsor = card._w(schema.PLAYER_SPONSOR_KEY, QLineEdit)
    tagKey = f"{card.path}.{schema.PLAYER_TAG_KEY}"
    service.SetText(schema.PLAYER_TAG_KEY, "BEFORE")
    autonaApp.settle()
    finished = _finishes(tag)

    with _onScreen(board, autonaApp):
        _focus(tag, autonaApp)
        service.SetText(schema.PLAYER_TAG_KEY, "AFTER")
        autonaApp.settle()
        assert tag.text() == "BEFORE", "the field being edited was redrawn"

        with _noWrites() as attempts:
            _focus(sponsor, autonaApp)
        assert finished == ["BEFORE"]
        assert attempts == []
        assert autonaApp.get(tagKey) == "AFTER"
        assert tag.text() == "AFTER"
        assert _cardMismatches(board) == {}

        # Typing is an edit, left-behind field or not: what the operator
        # typed is published (last writer wins).
        _focus(tag, autonaApp)
        service.SetText(schema.PLAYER_TAG_KEY, "EXTERNAL")
        autonaApp.settle()
        QTest.keyClicks(tag, "!")
        _focus(sponsor, autonaApp)
        assert autonaApp.get(tagKey) == "AFTER!"
        assert tag.text() == "AFTER!"
        assert _cardMismatches(board) == {}


@pytest.mark.parametrize("finish", ["focus-out", "enter"])
def test_typing_back_to_the_left_behind_text_is_not_an_edit(
        board, autonaApp, finish):
    """Scenario A with keystrokes that end where they started: the text
    finished is still the one the render left behind, so it is not written
    over the newer value -- whether the field is left or Enter is pressed."""
    if RECORD:
        pytest.skip("not part of the recording")
    card = board.team1playerWidgets[0]
    service = PlayerService(card.path)
    tag = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
    sponsor = card._w(schema.PLAYER_SPONSOR_KEY, QLineEdit)
    service.SetText(schema.PLAYER_TAG_KEY, "BEFORE")
    autonaApp.settle()
    finished = _finishes(tag)

    with _onScreen(board, autonaApp):
        _focus(tag, autonaApp)
        service.SetText(schema.PLAYER_TAG_KEY, "AFTER")
        autonaApp.settle()
        QTest.keyClicks(tag, "x")
        QTest.keyClick(tag, Qt.Key.Key_Backspace)
        assert tag.text() == "BEFORE" and tag.isModified()

        with _noWrites() as attempts:
            if finish == "enter":
                QTest.keyClick(tag, Qt.Key.Key_Return)
                autonaApp.settle()
            _focus(sponsor, autonaApp)
        assert finished == ["BEFORE"]
        assert attempts == []
        assert autonaApp.get(f"{card.path}.{schema.PLAYER_TAG_KEY}") == "AFTER"
        assert tag.text() == "AFTER"


# ── what a render must not do ────────────────────────────────────────────

def _widgetState(card):
    """Everything a render could change on the card's combos."""
    country = card._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    region = card._w(schema.PLAYER_STATE_KEY, QComboBox)
    return {
        "country": country.currentIndex(),
        "region": (region.currentIndex(), region.count()),
        "regionRowsFor": card._loadedStatesCountryCode,
        "characters": [
            (character.currentIndex(), character.currentText(),
             color.currentIndex(), color.count(),
             Shiboken.getCppPointer(color.model())[0],
             variant.currentIndex())
            for _element, character, color, variant
            in card.character_elements],
    }


def _unreproducible(service, slot):
    """Slot values no combo row reproduces, each applied over `slot`."""
    luigi = service.CharacterValue(CharacterSlot("Luigi", 0))
    badVariant = dict(luigi, variant={"en_name": "NoSuchVariant"})
    badSkin = service.CharacterValue(CharacterSlot("Luigi", 99))

    def characters(first):
        value = deep_clone(slot["character"])
        value[1] = first
        return value

    return {
        # Moving the country would also rebuild the region rows.
        "a region the country does not have": {
            schema.PLAYER_COUNTRY_KEY: service.CountryValue("JP"),
            schema.PLAYER_STATE_KEY: {"code": "ZZ", "name": "Nowhere"}},
        "an unknown country": {
            schema.PLAYER_COUNTRY_KEY: {"code": "XX", "name": "Nowhere"},
            schema.PLAYER_STATE_KEY: {}},
        "an unknown variant": {"character": characters(badVariant)},
        "a skin the character does not have": {
            "character": characters(badSkin)},
    }


@pytest.mark.parametrize("case", [
    "a region the country does not have", "an unknown country",
    "an unknown variant", "a skin the character does not have"])
def test_a_value_no_row_reproduces_changes_nothing(board, autonaApp, case):
    if RECORD:
        pytest.skip("not part of the recording")
    _onePlayerTwoCharacters(board, autonaApp)
    card = board.team1playerWidgets[0]
    # BETA: US / CA, Peach with a skin and a variant -- every combo off its
    # blank row, and a different country and character from the cases.
    card.SetData(dict(PLAYERS[(1, 1)]), dontLoadFromDB=True)
    autonaApp.settle()
    assert _cardMismatches(board) == {}
    before = _widgetState(card)

    change = _unreproducible(card.player, _slotOf(card))[case]
    with StateManager.SaveBlock():
        for key, value in change.items():
            StateManager.Set(f"{card.path}.{key}", value)
    autonaApp.settle()

    assert card.RenderFromState() is False
    assert _widgetState(card) == before
    assert card._renderedSlot is None, "not marked as shown"


@pytest.mark.parametrize("emptied", ["unset", "empty"])
def test_an_emptied_slot_shows_a_blank_card(board, autonaApp, emptied):
    if RECORD:
        pytest.skip("not part of the recording")
    _onePlayerTwoCharacters(board, autonaApp)
    card = board.team1playerWidgets[0]
    card.SetData(dict(PLAYERS[(1, 0)]), dontLoadFromDB=True)
    autonaApp.settle()
    StateManager.CommitHeld(PREFIX)
    autonaApp.settle()

    with _Cycles() as cycles:
        if emptied == "unset":
            StateManager.Unset(card.path)
        else:
            StateManager.Set(card.path, {})
        autonaApp.settle()
    assert cycles.count == 1

    assert _cardMismatches(board) == {}
    assert card._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == ""
    assert card.seedDisplay.text() == ""
    assert _comboCode(card._w(schema.PLAYER_COUNTRY_KEY, QComboBox)) is None
    assert card._w(schema.PLAYER_STATE_KEY, QComboBox).count() == 1
    assert [element[1].currentText() for element in card.character_elements] \
        == ["", ""]


def test_the_player_editor_is_not_rendered(board, autonaApp):
    """The PLAYERS panel's editor is the same widget on player_editor.1: a
    draft the operator types into, which nothing else writes."""
    if RECORD:
        pytest.skip("not part of the recording")
    editorPath = f"{schema.PLAYER_EDITOR_ROOT}.1"
    editor = AutonaScoreboardPlayerWidget(index=1, teamNumber=1,
                                          path=editorPath)
    try:
        editor.SetData({"gamerTag": "DRAFT"}, dontLoadFromDB=True)
        autonaApp.settle()
        PlayerService(editorPath).SetText(schema.PLAYER_TAG_KEY, "ELSEWHERE")
        # ...and a board pass, which only knows the board's cards.
        boardCard = board.team1playerWidgets[0]
        PlayerService(boardCard.path).SetText(
            schema.PLAYER_TAG_KEY, "ON THE BOARD")
        autonaApp.settle()

        assert editor.RenderFromState() is True
        assert editor._w(schema.PLAYER_TAG_KEY, QLineEdit).text() == "DRAFT"
        assert boardCard._w(schema.PLAYER_TAG_KEY, QLineEdit).text() \
            == "ON THE BOARD"
    finally:
        editor.setParent(None)
        editor.deleteLater()
        # Reaped here: pytest runs no event loop (see test_player_widgets).
        QCoreApplication.sendPostedEvents(editor, QEvent.Type.DeferredDelete)
        StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        autonaApp.settle()


def test_a_card_removed_before_the_pass_is_not_rendered(board, autonaApp):
    """A pass queued while a card was on the board runs after the roster
    shrank: it renders the cards the board has then, never the removed one
    (whose slot is gone and whose widget is being deleted)."""
    if RECORD:
        pytest.skip("not part of the recording")
    board.playerNumber.setValue(2)
    autonaApp.settle()
    StateManager.CommitHeld(PREFIX)
    autonaApp.settle()
    removed = [board.team1playerWidgets[1], board.team2playerWidgets[1]]

    rendered = []
    original = AutonaScoreboardPlayerWidget.RenderFromState

    def spy(card):
        rendered.append(card)
        return original(card)

    with patch.object(AutonaScoreboardPlayerWidget, "RenderFromState",
                      autospec=True, side_effect=spy):
        PlayerService(removed[0].path).SetText(
            schema.PLAYER_TAG_KEY, "LEAVING")     # queues a pass...
        board.playerNumber.setValue(1)            # ...the card goes first
        autonaApp.settle()

    assert rendered, "no pass ran"
    assert not [card for card in rendered
                if any(card is gone for gone in removed)]
    assert {id(card) for card in rendered} \
        == {id(card) for card in board.playerWidgets}
