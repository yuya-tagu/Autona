"""Players per team and characters per player, from every entry point
(ADR-005 S3e-6).

The counts used to be the 1v1 / team board's two spinboxes (playerNumber /
charNumber): building the board, the singles mode, a game load (its
``players_per_team``), a set's entrants, the Web's ``set_route`` and
``clear_all`` all drove them, and the player cards they built and tore down
published the slots. Since S3e-6 the count is the state's -- the slots
themselves, one operation (``AutonaActions.SetRoster``) for every entry
point -- and the board builds and removes cards to follow it.

What the desktop publishes is pinned against a recording made on the last
tree where the spinboxes were the source of truth (7169efd5), through the
public entry points only, so the same drivers replay it. For every step:

- the board subtree afterwards (orjson: key order included),
- whether the board has edits waiting behind 反映,
- what 反映 then published (the Socket.IO deltas, as a client receives
  them),
- the cards: how many each team has, their character rows, the tag they
  show, whether their "Player N" title shows; the two spinboxes; whether
  the team-name fields show,
- what a Web route answered.

The save cycles a step took are not part of it: a count change used to be
one cycle per card for the characters, and is one now (the held cycles are
the board's own business -- nothing is published until 反映).

Record (on the pre-S3e-6 tree only):

    AUTONA_RECORD_ROSTER=1 python -m pytest tests/test_roster_flow.py -k recorded
"""
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import (QApplication, QCheckBox, QGroupBox,
                            QHBoxLayout, QLabel, QLayout, QLineEdit,
                            QMessageBox, QWidget)
from shiboken6 import Shiboken

from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from src.AutonaScoreboardWidget import AutonaScoreboardWidget
from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.StateManager import StateManager
from tests.conftest import copy_trees, drain_tasks, remove_tree
from tests.fixtures.write_recording import encode, encodeDelta
# The S1d world: the fixture games (with real characters, skins and
# variants) under the recording's locale.
from tests.test_player_slot_publish import slotEnv  # noqa: F401  (fixture)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "roster" / "golden.json"
RECORD = os.environ.get("AUTONA_RECORD_ROSTER") == "1"

# A game whose roster default is two players a team (the fixture games have
# none, i.e. one).
TEAM_GAME = "rostergame"


def _normalise(text, app):
    root = str(app.dir)
    for spelling in (root, root.replace("\\", "/"),
                     root.replace("\\", "\\\\")):
        text = text.replace(spelling, "<RUNTIME>")
    return text


class _Published:
    def __init__(self):
        self.deltas = []

    def _onDelta(self, payload):
        self.deltas.append(encodeDelta(payload.get("delta")))

    def _onBig(self):
        self.deltas.append("BIG")

    def __enter__(self):
        StateManager.observers.state_updated.subscribe(self._onDelta)
        StateManager.observers.state_big_change.subscribe(self._onBig)
        return self

    def __exit__(self, *exc):
        StateManager.observers.state_updated.unsubscribe(self._onDelta)
        StateManager.observers.state_big_change.unsubscribe(self._onBig)
        return False


def _cards(board):
    result = {}
    for team, cards in ((1, board.team1playerWidgets),
                        (2, board.team2playerWidgets)):
        result[str(team)] = [
            [len(card.character_elements),
             card.findChild(QLineEdit, "name").text(),
             not card.findChild(QLabel, "title").isHidden()]
            for card in cards]
    return result


def _teamNamesShown(board):
    return [not column.findChild(QLineEdit, "teamName").isHidden()
            for column in (board.team1column, board.team2column)]


def snapshot(app, board, number):
    """The board as it stands: state, pending edits and the widgets."""
    prefix = f"score.{number}"
    with StateManager.lock:
        state = _normalise(encode(StateManager.Get(prefix)), app)
    return {
        "state": state,
        "dirty": StateManager.HasPendingChanges(prefix),
        "cards": _cards(board),
        "spin": [board.playerNumber.value(), board.charNumber.value()],
        "teamNamesShown": _teamNamesShown(board),
    }


class Steps:
    """Runs and records steps on one board."""

    def __init__(self, app, number):
        self.app = app
        self.number = number
        self.recorded = []

    @property
    def board(self):
        return self.app.manager.scoreboards[self.number]

    def run(self, label, action, commit=True):
        response = action()
        self.app.settle()
        entry = {"step": label}
        if isinstance(response, (str, bool)):
            entry["response"] = response
        entry.update(snapshot(self.app, self.board, self.number))
        if commit:
            with _Published() as published:
                StateManager.CommitHeld(f"score.{self.number}")
                self.app.settle()
            entry["published"] = [_normalise(d, self.app)
                                  for d in published.deltas]
        self.recorded.append(entry)

    def record(self, label):
        """The board as it is, without running anything (after a build)."""
        self.run(label, lambda: None, commit=False)


# ── golden ───────────────────────────────────────────────────────────────

def _check(name, recorded):
    if RECORD:
        golden = {}
        if GOLDEN_PATH.exists():
            golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        golden[name] = recorded
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(golden, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))[name]
    assert [s["step"] for s in recorded] == [s["step"] for s in golden]
    for actual, expected in zip(recorded, golden):
        differ = [field for field in expected
                  if actual.get(field) != expected[field]]
        assert not differ, (
            f"{name}: {actual['step']!r} differs in {differ}\n"
            f"recorded: {json.dumps(expected)[:4000]}\n"
            f"now:      {json.dumps(actual)[:4000]}")


# ── fixtures and drivers ─────────────────────────────────────────────────

@pytest.fixture
def world(slotEnv, autonaApp):
    """The fixture games loaded (tgame: one player a team by default), plus
    TEAM_GAME (two), removed afterwards."""
    from src.core.GameAssetCatalog import GameAssetCatalog

    games = autonaApp.dir / "user_data" / "games"
    team = games / TEAM_GAME
    copy_trees([(games / "tgame", team)])
    config = team / "base_files" / "config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["name"] = "Roster Game"
    data["defaults"] = {"players_per_team": 2, "best_of": 5}
    config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    GameAssetCatalog.instance.ScanGames()
    slotEnv("tgame")
    try:
        yield slotEnv
    finally:
        autonaApp.dropAllBoards()
        remove_tree(team)
        GameAssetCatalog.instance.ScanGames()


def loadGame(codename):
    """A game load through the desktop's adapter, as the GAME menu and the
    Web's set_game run it: the load job, run to the end with this (the GUI)
    thread pumped (ADR-005 S3f-4; it was the GUI thread's synchronous load
    here until the review)."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    manager = AutonaGameAssetManager.instance
    manager.LoadGameByCodename(codename, mods_reload_mode=True)
    drain_tasks()


PLAYER_A = {"gamerTag": "ALPHA", "prefix": "SP", "country_code": "JP",
            "state_code": "13", "seed": 3,
            "mains": {"tgame": [["Mario", 2, "EX"], ["Luigi", 1],
                                ["Peach", 1]]}}
PLAYER_B = {"gamerTag": "BETA", "country_code": "US",
            "mains": {"tgame": [["Bowser", 0], ["Zelda", 1]]}}


def _entrant(tag, *mains):
    return {"gamerTag": tag,
            "mains": {"tgame": [[name, 0] for name in mains]}}


def _set(*teams, **extra):
    return dict({"overwrite": True, "entrants": [list(t) for t in teams]},
                **extra)


# ── scenarios ────────────────────────────────────────────────────────────

def test_the_team_board_publishes_the_recorded_rosters(world, autonaApp):
    web = autonaApp.web()
    steps = Steps(autonaApp, 2)
    autonaApp.board(2)
    steps.record("built")
    board = steps.board

    steps.run("players 2", lambda: board.playerNumber.setValue(2))
    steps.run("fill", lambda: (
        web.update_player(2, 1, 1, dict(PLAYER_A)),
        web.update_player(2, 2, 2, dict(PLAYER_B))))
    steps.run("team names", lambda: (
        web.set_route(2, team=1, teamName="Alpha"),
        web.set_route(2, team=2, teamName="Bravo")))
    steps.run("characters 2", lambda: board.charNumber.setValue(2))
    steps.run("characters 4", lambda: board.charNumber.setValue(4))
    steps.run("players 3", lambda: board.playerNumber.setValue(3))
    steps.run("swap", board.SwapTeams)
    steps.run("move a player down", lambda: board._MovePlayer(
        board.team1playerWidgets[0], 1))
    steps.run("players 1", lambda: board.playerNumber.setValue(1))
    steps.run("web players 3", lambda: web.set_route(2, players=3))
    steps.run("web characters 1", lambda: web.set_route(2, characters=1))
    steps.run("web players and characters", lambda: web.set_route(
        2, players=2, characters=3, match="Final"))
    steps.run("swap back", board.SwapTeams)
    steps.run("load a 2v2 set", lambda: board.ChangeSetData(_set(
        [_entrant("A1", "Mario"), _entrant("A2", "Luigi", "Peach")],
        [_entrant("B1", "Bowser"), _entrant("B2")],
        p1_name="TEAM A", p2_name="TEAM B")))
    steps.run("load a 3v3 set", lambda: board.ChangeSetData(_set(
        [_entrant("C1"), _entrant("C2"), _entrant("C3", "Zelda")],
        [_entrant("D1"), _entrant("D2", "Mario"), _entrant("D3")],
        p1_name="TEAM C", p2_name="TEAM D")))
    steps.run("load a 1v1 set", lambda: board.ChangeSetData(_set(
        [_entrant("E1", "Luigi")], [_entrant("F1")])))
    steps.run("players 2 again", lambda: board.playerNumber.setValue(2))
    steps.run("web clear all", lambda: web.clear_all(2))
    steps.run("players 0", lambda: board.playerNumber.setValue(0))
    steps.run("players 2 from none", lambda: board.playerNumber.setValue(2))
    steps.run("web characters 0", lambda: web.set_route(2, characters=0))
    steps.run("players 3 without characters",
              lambda: board.playerNumber.setValue(3))
    steps.run("characters 3 again", lambda: board.charNumber.setValue(3))
    steps.run("fill again", lambda: (
        web.update_player(2, 1, 2, dict(PLAYER_A)),
        web.update_player(2, 2, 3, dict(PLAYER_B))))
    with patch.object(QMessageBox, "question",
                      return_value=QMessageBox.StandardButton.Yes):
        steps.run("reset all inputs", board.ResetAllInputs)
    steps.run("web players 1", lambda: web.set_route(2, players=1))

    _check("team board", steps.recorded)


def test_a_game_load_applies_its_roster_default(world, autonaApp):
    web = autonaApp.web()
    steps = Steps(autonaApp, 2)
    autonaApp.board(2)
    steps.record("built")
    board = steps.board

    steps.run("fill", lambda: web.update_player(2, 1, 1, dict(PLAYER_A)))
    steps.run("characters 2", lambda: board.charNumber.setValue(2))
    steps.run("load a two-player game", lambda: loadGame(TEAM_GAME),
              commit=False)
    steps.run("team names", lambda: (
        web.set_route(2, team=1, teamName="Alpha"),
        web.set_route(2, team=2, teamName="Bravo")))
    steps.run("players 3", lambda: board.playerNumber.setValue(3))
    steps.run("reload the two-player game", lambda: loadGame(TEAM_GAME),
              commit=False)
    steps.run("load a one-player game", lambda: loadGame("tgame"),
              commit=False)
    steps.run("load the two-player game again", lambda: loadGame(TEAM_GAME),
              commit=False)

    singles = Steps(autonaApp, 1)
    autonaApp.board(1)
    singles.record("singles board built after the load")
    singles.run("singles web players 3", lambda: web.set_route(1, players=3))
    singles.run("singles web clear all", lambda: web.clear_all(1))
    singles.run("singles load a one-player game", lambda: loadGame("tgame"),
                commit=False)

    _check("game load", steps.recorded + singles.recorded)


def test_the_boards_are_built_with_the_recorded_rosters(world, autonaApp):
    """What a board publishes as it is built, and what it waits to publish
    (nothing: building a board leaves nothing behind 反映)."""
    recorded = []
    for number in (1, 2):
        autonaApp.board(number)
        board = autonaApp.manager.scoreboards[number]
        recorded.append(dict({"step": f"board {number}"},
                             **snapshot(autonaApp, board, number)))
    loadGame(TEAM_GAME)
    autonaApp.settle()
    for number in (1, 2):
        autonaApp.board(number)
        board = autonaApp.manager.scoreboards[number]
        recorded.append(dict({"step": f"board {number}, a two-player game"},
                             **snapshot(autonaApp, board, number)))

    _check("built", recorded)


# ── the cards' lifecycle ─────────────────────────────────────────────────
#
# The board builds a card per slot and retires the card of a slot that is
# gone, in the held cycle, and destroys what it retired from the event loop
# (AutonaScoreboardWidget._SyncPlayerCards / _DestroyRetiredCards); a card
# takes its character rows off the same way (_SetCharacterRows /
# _DestroyRemovedRows). What can go wrong is what went wrong elsewhere:
# a retired card answering an edit finished after its slot went (a ghost
# slot written back into the state), something torn down inside the
# notification that noticed the change, and QLayoutItem wrappers left
# registered for items Qt frees (agent-pitfalls #14).

class _Lifecycle:
    """The cards the board retires and the character rows the cards take
    off, kept to poke and to reap (pytest never runs the event loop that
    would reap a deleteLater())."""

    def __init__(self):
        self.retired = []
        self.rows = []

    def __enter__(self):
        lifecycle = self
        retire = AutonaScoreboardPlayerWidget.Retire
        destroyRows = AutonaScoreboardPlayerWidget._DestroyRemovedRows

        def _retire(card):
            lifecycle.retired.append(card)
            retire(card)

        def _destroyRows(card):
            lifecycle.rows.extend(card._removedRows)
            destroyRows(card)

        self._patches = [
            patch.object(AutonaScoreboardPlayerWidget, "Retire", _retire),
            patch.object(AutonaScoreboardPlayerWidget, "_DestroyRemovedRows",
                         _destroyRows)]
        for patcher in self._patches:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._patches):
            patcher.stop()
        self.reap()
        return False

    def reap(self):
        """Run the deletions the board and the cards asked for."""
        for widget in self.retired + self.rows:
            if Shiboken.isValid(widget):
                QCoreApplication.sendPostedEvents(
                    widget, QEvent.Type.DeferredDelete)


class _Pick:
    """A name-completer row, as a queued pick hands it over."""

    def data(self, role):
        return {"gamerTag": "GHOST", "prefix": "GHOST"}


def _poke(card):
    """Every edit a card could still finish once its slot is gone: the two
    name fields (a focus-out finishes them), a queued completer pick, a
    character combo settling."""
    for key in (schema.PLAYER_TAG_KEY, schema.PLAYER_SPONSOR_KEY):
        field = card.findChild(QLineEdit, key)
        field.setText("GHOST")
        field.editingFinished.emit()
    card._OnCompleterPick(_Pick())
    card.CharactersChanged(includeMains=True)
    card.Clear()


def _boardState(number=2):
    with StateManager.lock:
        return encode(StateManager.Get(f"score.{number}"))


def _assertCardsFollow(board, number=2):
    """One live card per slot, at its slot's path, with one row per
    character slot; nothing retired left on the board; the counts shown."""
    prefix = f"score.{number}"
    teams = ((1, board.team1playerWidgets), (2, board.team2playerWidgets))
    for team, cards in teams:
        roster = StateManager.Get(f"{prefix}.team.{team}.player") or {}
        assert sorted(roster, key=int) == [
            str(i) for i in range(1, len(roster) + 1)], (
            f"team {team}: the roster has a gap or a ghost slot: "
            f"{sorted(roster)}")
        assert len(cards) == len(roster), f"team {team}"
        for index, card in enumerate(cards, 1):
            assert card.path == f"{prefix}.team.{team}.player.{index}"
            assert not card._deleted and not card.isHidden(), card.path
            picks = StateManager.Get(f"{card.path}.character")
            assert len(card.character_elements) == len(picks), card.path
            assert not any(row[0].isHidden()
                           for row in card.character_elements), card.path
            assert card.character_container.layout().count() \
                == len(picks), f"{card.path}: a row left in the layout"
    live = board.team1playerWidgets + board.team2playerWidgets
    assert sorted(map(id, board.playerWidgets)) == sorted(map(id, live))
    assert {id(card) for card in board.findChildren(
        AutonaScoreboardPlayerWidget)} == {id(card) for card in live}, (
        "a retired card is still on the board")
    assert board.playerNumber.value() == len(board.team1playerWidgets)
    if live:
        assert board.charNumber.value() == len(live[0].character_elements)


def _rosterSteps(board, web):
    return [
        ("players 3", lambda: board.playerNumber.setValue(3)),
        ("characters 4", lambda: board.charNumber.setValue(4)),
        ("fill", lambda: (
            web.update_player(2, 1, 3, dict(PLAYER_A)),
            web.update_player(2, 2, 1, dict(PLAYER_B)))),
        ("swap", board.SwapTeams),
        ("move the last player up", lambda: board._MovePlayer(
            board.team1playerWidgets[-1], -1)),
        ("characters 1", lambda: board.charNumber.setValue(1)),
        ("web players 1", lambda: web.set_route(2, players=1)),
        ("web players and characters", lambda: web.set_route(
            2, players=2, characters=3)),
        ("load a 3v3 set", lambda: board.ChangeSetData(_set(
            [_entrant("C1"), _entrant("C2"), _entrant("C3", "Zelda")],
            [_entrant("D1"), _entrant("D2", "Mario"), _entrant("D3")]))),
        ("swap the 3v3", board.SwapTeams),
        ("load a 2v2 set", lambda: board.ChangeSetData(_set(
            [_entrant("A1", "Mario"), _entrant("A2", "Luigi", "Peach")],
            [_entrant("B1", "Bowser"), _entrant("B2")]))),
        ("load a 1v1 set", lambda: board.ChangeSetData(_set(
            [_entrant("E1", "Luigi")], [_entrant("F1")]))),
        ("load a two-player game", lambda: loadGame(TEAM_GAME)),
        ("players 4", lambda: board.playerNumber.setValue(4)),
        ("load a one-player game", lambda: loadGame("tgame")),
        ("web clear all", lambda: web.clear_all(2)),
        ("players 0", lambda: board.playerNumber.setValue(0)),
        ("players 2 from none", lambda: board.playerNumber.setValue(2)),
        ("web characters 0", lambda: web.set_route(2, characters=0)),
        ("characters 2", lambda: board.charNumber.setValue(2)),
        ("core roster 1 / 1", lambda: AutonaActions.instance.SetRoster(
            2, 1, 1)),
    ]


def test_the_cards_follow_repeated_roster_changes(world, autonaApp):
    """Cards and character rows built and torn down over and over, from
    every entry point, around swaps, moves and set loads: after each step
    the board has a live card per slot and a row per character slot, the
    cards it retired wrote nothing however they were poked, and everything
    it took off is destroyed once the event loop gets to it -- not before."""
    web = autonaApp.web()
    board = autonaApp.board(2)
    with _Lifecycle() as lifecycle:
        for lap in range(4):
            for label, action in _rosterSteps(board, web):
                step = f"lap {lap}: {label}"
                retiredBefore = len(lifecycle.retired)
                action()
                retired = lifecycle.retired[retiredBefore:]
                for card in retired:
                    assert card not in board.playerWidgets, step
                    # Retired in the held cycle; destroyed from the event
                    # loop (a game load runs the loop itself).
                    if not Shiboken.isValid(card):
                        continue
                    assert card.isHidden(), step
                    before = _boardState()
                    _poke(card)
                    assert _boardState() == before, (
                        f"{step}: a retired card {card.path} wrote")
                autonaApp.settle()
                for card in retired:
                    assert not Shiboken.isValid(card) \
                        or card.parentWidget() is None, (
                        f"{step}: {card.path} was not taken off the board")
                lifecycle.reap()
                assert not any(Shiboken.isValid(card) for card in retired), (
                    f"{step}: a retired card was not deleted")
                assert not any(Shiboken.isValid(row)
                               for row in lifecycle.rows), (
                    f"{step}: a character row taken off was not deleted")
                _assertCardsFollow(board)
                assert "GHOST" not in _boardState(), step
                StateManager.CommitHeld("score.2")
                autonaApp.settle()
        # Cards and rows did come and go, on every lap.
        assert len(lifecycle.retired) >= 4 * 10
        assert len(lifecycle.rows) >= 4 * 6


def test_a_board_card_writes_nothing_as_it_is_built(world, autonaApp):
    """The slots are seeded before their cards exist (SetRoster): building
    a card, and the rows of a character count, leaves the state as the
    roster change wrote it."""
    web = autonaApp.web()
    board = autonaApp.board(2)
    sync = board._SyncPlayerCards
    passes = []

    def _sync():
        before = _boardState()
        cards = len(board.playerWidgets)
        rows = sum(len(card.character_elements)
                   for card in board.playerWidgets)
        sync()
        passes.append((before, _boardState(), cards,
                       len(board.playerWidgets), rows,
                       sum(len(card.character_elements)
                           for card in board.playerWidgets)))

    with patch.object(board, "_SyncPlayerCards", _sync):
        for action in (
                lambda: board.playerNumber.setValue(3),
                lambda: board.charNumber.setValue(4),
                lambda: web.set_route(2, players=4, characters=2),
                lambda: board.ChangeSetData(_set(
                    *[[_entrant(f"T{t}P{i}") for i in range(1, 6)]
                      for t in (1, 2)])),
                lambda: loadGame(TEAM_GAME),
                lambda: board.playerNumber.setValue(1)):
            action()
            autonaApp.settle()
    grew = [p for p in passes if p[3] > p[2] or p[5] > p[4]]
    assert grew, "no pass built a card or a row"
    for before, after, *_counts in passes:
        assert after == before, "building the cards wrote to the state"


def test_a_shrink_destroys_from_the_event_loop_and_releases_the_items(
        world, autonaApp):
    """Nothing is torn down inside the held cycle that noticed the slots go:
    the cards and rows are hidden there and destroyed once control is back
    in the event loop, every QLayoutItem wrapper that goes with them
    released from PySide first (agent-pitfalls #14) -- a wrapper still
    registered for a freed item is what turned into access violations in
    unrelated constructors."""
    web = autonaApp.web()
    board = autonaApp.board(2)
    board.playerNumber.setValue(3)
    board.charNumber.setValue(4)
    autonaApp.settle()

    gone = [board.team1playerWidgets[2], board.team2playerWidgets[2]]
    kept = [card for card in board.playerWidgets if card not in gone]
    rows = [row[0] for card in kept for row in card.character_elements[1:]]
    # Wrappers handed out for the items that go: the slots of the team
    # columns' layouts, the cards' own, the kept cards' character lists.
    items = []
    layouts = [card.character_container.layout() for card in kept]
    for card in gone:
        layouts += [card.parentWidget().layout(), card.layout()]
        layouts += card.findChildren(QLayout)
    for layout in layouts:
        items += [layout.itemAt(i) for i in range(layout.count())]
    items = [item for item in items
             if item is not None and item.layout() is None]
    try:
        with _Lifecycle() as lifecycle:
            web.set_route(2, players=2, characters=1)
            # The held cycle: retired and taken off, nothing destroyed.
            assert lifecycle.retired == gone
            for widget in gone + rows:
                assert Shiboken.isValid(widget) and widget.isHidden()
            for card in gone:
                assert isinstance(card.parentWidget(), QGroupBox)
            assert all(Shiboken.isValid(item) for item in items)

            autonaApp.settle()
            # The event loop: out of the layouts, the items' wrappers
            # released, and deleted (or about to be).
            assert not any(Shiboken.isValid(item) for item in items), (
                "a QLayoutItem wrapper outlived its item")
            for widget in gone + rows:
                assert not Shiboken.isValid(widget) \
                    or widget.parentWidget() is None
            assert sorted(map(id, lifecycle.rows)) == sorted(map(id, rows))
        assert not any(Shiboken.isValid(widget) for widget in gone + rows)
        _assertCardsFollow(board)
    finally:
        for item in items:
            if Shiboken.isValid(item):
                Shiboken.invalidate(item)


def test_a_card_retired_mid_edit_does_not_write_its_slot_back(
        world, autonaApp):
    """The slot goes before the board's held cycle retires the card (the
    roster change is one save cycle, and its notification comes last): an
    edit the card finishes in between -- a focus-out, a queued pick -- must
    not write the slot back. Nor once it is retired, with the slot there
    again under a new card."""
    board = autonaApp.board(2)
    board.playerNumber.setValue(2)
    autonaApp.settle()
    StateManager.CommitHeld("score.2")
    autonaApp.settle()
    card = board.team1playerWidgets[1]
    path = card.path

    with _Lifecycle() as lifecycle:
        with StateManager.SaveBlock():
            AutonaActions.instance.SetRoster(2, players=1)
            # In the save cycle: the slot is gone, the card not retired yet.
            assert StateManager.Get(path) is None
            assert card in board.team1playerWidgets
            _poke(card)
            assert StateManager.Get(path) is None, "a ghost slot"
        assert lifecycle.retired[0] is card
        assert StateManager.Get(path) is None

        # The slot comes back under a new card; the old one still writes
        # nothing to it.
        board.playerNumber.setValue(2)
        assert board.team1playerWidgets[1] is not card
        before = _boardState()
        _poke(card)
        assert _boardState() == before
        autonaApp.settle()
    assert not Shiboken.isValid(card)
    _assertCardsFollow(board)
    assert "GHOST" not in _boardState()


def _loadGameThreaded(codename):
    """A game load as a job of the TaskExecutor, as the GAME menu runs it
    (ADR-005 S3f-4; the asset loader's QThread until then): the models, then
    the roster (gameLoaded) and the boards' onLoad handlers run on the GUI
    thread, marshalled from the job while its save block is still open. This
    thread is pumped while waiting -- the job marshals onto it (run_sync),
    so blocking on its Future would deadlock."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    manager = AutonaGameAssetManager.instance
    manager.LoadGameByCodename(codename, async_mode=True,
                               mods_reload_mode=True)
    drain_tasks()


def test_a_threaded_game_load_publishes_the_recorded_roster(
        world, autonaApp):
    """The recorded game load (a synchronous one) replayed on the loader's
    thread: the same state, the same cards, nothing left behind 反映."""
    if RECORD:
        pytest.skip("not part of the recording")
    web = autonaApp.web()
    steps = Steps(autonaApp, 2)
    autonaApp.board(2)
    steps.record("built")
    board = steps.board
    steps.run("fill", lambda: web.update_player(2, 1, 1, dict(PLAYER_A)))
    steps.run("characters 2", lambda: board.charNumber.setValue(2))
    with _Lifecycle():
        steps.run("load a two-player game",
                  lambda: _loadGameThreaded(TEAM_GAME), commit=False)
        _assertCardsFollow(board)

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["game load"]
    for actual, expected in zip(steps.recorded, golden):
        assert actual["step"] == expected["step"]
        differ = [field for field in expected
                  if actual.get(field) != expected[field]]
        assert not differ, f"{actual['step']!r} differs in {differ}"


def test_a_game_load_and_its_roster_are_one_save_cycle_on_the_gui_thread(
        world, autonaApp, monkeypatch):
    """ADR-005 S3f-4 review: the load job opens the save cycle, the GUI
    thread publishes it at the end of _Publish. The game's writes and the
    roster's (board 2 to the game's two players, committed by the board
    within the load) go out as that one cycle's delta, published on the GUI
    thread while the load is still publishing -- and the bridge delivers
    the cycle's notifications there, direct, not queued after it
    (agent-pitfalls #17: one cycle under one hold of the lock)."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.StateManagerBridge import StateManagerBridge

    if RECORD:
        pytest.skip("not part of the recording")
    manager = AutonaGameAssetManager.instance
    autonaApp.board(2)
    main = threading.main_thread().name
    cycles = []
    events = []
    publishing = []

    realSave = StateManager.SaveState

    def saveState():
        if StateManager.saveBlocked == 0:
            cycles.append(threading.current_thread().name)
        return realSave()

    realPublish = manager._Publish

    def publish(*args):
        publishing.append(1)
        try:
            return realPublish(*args)
        finally:
            publishing.pop()

    def here():
        return threading.current_thread().name, bool(publishing)

    def onDelta(payload):
        events.append(("delta", len(cycles)) + here()
                      + ({tuple(row["path"][:5]) for row in payload["delta"]},))

    def onBridgeDelta(payload):
        events.append(("bridge delta",) + here())

    def onBridgeDirty(prefix, dirty):
        events.append(("bridge dirty", prefix, dirty) + here())

    monkeypatch.setattr(StateManager, "SaveState", saveState)
    monkeypatch.setattr(manager, "_Publish", publish)
    observers = StateManager.observers
    observers.state_updated.subscribe(onDelta)
    bridge = StateManagerBridge.instance
    bridge.state_updated.connect(onBridgeDelta)
    bridge.held_dirty_changed.connect(onBridgeDirty)
    try:
        with _Lifecycle():
            _loadGameThreaded(TEAM_GAME)
            autonaApp.settle()
    finally:
        bridge.held_dirty_changed.disconnect(onBridgeDirty)
        bridge.state_updated.disconnect(onBridgeDelta)
        observers.state_updated.unsubscribe(onDelta)

    deltas = [event for event in events if event[0] == "delta"]
    assert len(deltas) == 1, events
    _delta, cycle, thread, published, paths = deltas[0]
    assert (thread, published) == (main, True)
    assert cycles[cycle - 1] == main
    roots = {path[0] for path in paths}
    assert roots == {"game", "score"}, paths
    assert ("score", "2", "team", "1", "player") in paths
    assert ("bridge delta", main, True) in events, events
    assert ("bridge dirty", "score.2", False, main, True) in events, events
    assert len(StateManager.Get("score.2.team.1.player")) == 2
    assert not StateManager.HasPendingChanges("score.2")


# ── ADR-005 S3e-6 review ─────────────────────────────────────────────────

def _losers(board, team):
    column = board.team1column if team == 1 else board.team2column
    return column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)


def test_the_singles_losers_box_outlives_the_players(world, autonaApp):
    """The singles board's LOSERS box is on a row of its own below the
    cards, not on the solo card's ">>詳細" row: the Web can take the board
    to no players, and the card went with its slot -- the box with it, for
    good. Through 0 -> 1 -> 0 -> 1 players it stays, and still writes."""
    web = autonaApp.web()
    board = autonaApp.board(1)
    boxes = [_losers(board, team) for team in (1, 2)]
    for team, column in ((1, board.team1column), (2, board.team2column)):
        row = column.findChild(QHBoxLayout, "losersRow")
        assert row is not None and row.indexOf(boxes[team - 1]) >= 0
        groupBox = column.findChild(QGroupBox, "groupBox")
        layout = column.layout()
        assert layout.indexOf(groupBox) + 1 == next(
            index for index in range(layout.count())
            if layout.itemAt(index).layout() is row)
        assert not isinstance(boxes[team - 1].parentWidget(),
                              AutonaScoreboardPlayerWidget)

    with _Lifecycle() as lifecycle:
        flag = False
        for players in (0, 1, 0, 1):
            web.set_route(1, players=players)
            autonaApp.settle()
            lifecycle.reap()
            assert len(board.team1playerWidgets) == players
            flag = not flag
            for team in (1, 2):
                box = _losers(board, team)
                assert box is boxes[team - 1] and Shiboken.isValid(box), (
                    f"players {players}: the LOSERS box went")
                assert not box.isHidden()
                box.setChecked(flag)
                assert StateManager.Get(f"score.1.team.{team}.losers") \
                    is flag, f"players {players}: the box wrote nothing"
        assert lifecycle.retired, "no card was retired"


def test_no_thread_is_waited_on_inside_a_held_cycle(world, autonaApp):
    """The held notifications -- and, for a write on the GUI thread, the
    board's handlers on them, direct -- run inside StateManager.lock
    (SaveState's, or a caller's around it), and the board builds its cards
    there: its later handlers in the same cycle rely on them. So nothing
    there waits on another thread (agent-pitfalls #17): a run_sync (what a
    gui_thread_sync does) made holding the lock never returns if the thread
    it waits on is the one that wants the lock. The roster's entry points
    -- a change under a caller's lock, the Web's, a threaded game load, a
    commit -- call none from inside a notification or with the lock held,
    on any thread."""
    from src.Helpers import AutonaQtHelper
    web = autonaApp.web()
    board = autonaApp.board(2)
    executor = AutonaQtHelper.gui_executor
    runSync = executor.run_sync
    inside = threading.local()
    waits = []

    def _counted(channel):
        notify = channel.notify

        def _notify(*args):
            inside.depth = getattr(inside, "depth", 0) + 1
            try:
                notify(*args)
            finally:
                inside.depth -= 1
        return _notify

    def _runSync(fn):
        waits.append((threading.current_thread().name,
                      StateManager.lock._is_owned(),
                      getattr(inside, "depth", 0)))
        return runSync(fn)

    observers = StateManager.observers
    with patch.object(observers.held_state_updated, "notify",
                      _counted(observers.held_state_updated)), \
            patch.object(observers.held_dirty_changed, "notify",
                         _counted(observers.held_dirty_changed)), \
            patch.object(observers.state_updated, "notify",
                         _counted(observers.state_updated)), \
            patch.object(executor, "run_sync", _runSync), _Lifecycle():
        with StateManager.lock:
            AutonaActions.instance.SetRoster(2, 3, 2)
        assert len(board.team1playerWidgets) == 3
        with StateManager.lock:
            with StateManager.SaveBlock():
                AutonaActions.instance.SetRoster(2, 1)
        web.set_route(2, players=4, characters=3)
        _loadGameThreaded(TEAM_GAME)
        autonaApp.settle()
        StateManager.CommitHeld("score.2")
        autonaApp.settle()
    assert waits, "no entry point waited on the GUI thread at all"
    assert not [wait for wait in waits if wait[1] or wait[2]], waits
    _assertCardsFollow(board)


def test_a_commit_inside_another_save_block_leaves_no_card_behind(
        world, autonaApp):
    """CommitHeld drops the held cycle a board still had owed (反映 is not
    an edit). A roster change another thread's save block made just before
    it was in that cycle: the board follows it from the commit instead
    (the save block is process-wide, so this thread's stands for it)."""
    board = autonaApp.board(2)
    with _Lifecycle():
        with StateManager.SaveBlock():
            AutonaActions.instance.SetRoster(2, 3, 2)
            StateManager.CommitHeld("score.2")
        autonaApp.settle()
        _assertCardsFollow(board)
        assert len(board.team1playerWidgets) == 3

        with StateManager.SaveBlock():
            AutonaActions.instance.SetRoster(2, 1, 4)
            StateManager.CommitHeld("score.2")
        autonaApp.settle()
    _assertCardsFollow(board)
    assert len(board.team1playerWidgets) == 1
    assert len(board.team1playerWidgets[0].character_elements) == 4


def _fillPicks(number=2):
    """Every slot of board `number` with three picks and the same mains."""
    picks = [CharacterSlot(character="Mario"), CharacterSlot(
        character="Luigi"), CharacterSlot(character="Peach")]
    with StateManager.SaveBlock():
        for team in (1, 2):
            for slot in StateManager.Get(f"score.{number}.team.{team}.player"):
                PlayerService(f"score.{number}.team.{team}.player.{slot}"
                              ).PublishCharacterSlots(picks, includeMains=True)


def _picks(number=2, read=StateManager.Get):
    """{team.slot.group: [names]} of board `number`, read with `read` (the
    live state; StateManager.GetCommitted: what is published)."""
    return {f"{team}.{slot}.{group}": [value.get("name") for value in
                                       read(f"score.{number}.team.{team}"
                                            f".player.{slot}.{group}"
                                            ).values()]
            for team in (1, 2)
            for slot in read(f"score.{number}.team.{team}.player")
            for group in ("character", "mains")}


def test_a_game_switch_forgets_every_pick_a_reload_keeps_them(
        world, autonaApp):
    """The previous game's characters and mains are emptied when the game
    loaded changes to another one -- whatever the characters per player,
    the default three both before and after here -- and kept on the first
    load of a session and on a reload of the same game (the Top 8's rule,
    from the same snapshot field). Core's, in both shells: run as the shell
    runs it after a load (bootstrap.wire_game_roster), with no board built,
    so no card's own rebind is in the way."""
    from src.core.GameAssetCatalog import GameAssetCatalog
    catalog = GameAssetCatalog.instance
    actions = AutonaActions.instance

    def load(codename, reload=False):
        snapshot = catalog.SelectGame(codename, reload=reload)
        if snapshot is not None:
            actions.ApplyGameRoster(snapshot.replaced)
        return snapshot

    actions.SetRoster(2, 2, 3)
    _fillPicks()
    kept = _picks()
    assert set(kept["1.1.character"]) - {""} and kept["2.2.mains"][0]

    # The first load of a session: `game` is empty (ResetGameState).
    catalog.ResetGameState()
    assert load(TEAM_GAME).replaced is None
    assert _picks() == kept
    # The same game again: skipped, or reloaded.
    assert load(TEAM_GAME) is None
    assert load(TEAM_GAME, reload=True).replaced is None
    assert _picks() == kept

    # Another game: every pick and main of every slot blank, the counts
    # the game's (one player a team).
    assert load("tgame").replaced == TEAM_GAME
    assert sorted(StateManager.Get("score.2.team.1.player")) == ["1"]
    assert _picks() == {f"{team}.1.{group}": ["", "", ""]
                        for team in (1, 2) for group in ("character", "mains")}

    # Already blank: a switch back writes no pick (the roster's count
    # change apart), and nothing at all when the counts stay.
    actions.SetRoster(2, 1, 3)
    writes = []
    with patch.object(StateManager, "Set",
                      side_effect=lambda key, value: writes.append(key)):
        actions.ApplyGameRoster(catalog.SelectGame(
            "tgame", reload=True).replaced)
    assert not [key for key in writes if key.startswith("score.")], writes

    # A forgotten selection (a cleared tournament URL) is no switch: what
    # the last load replaced goes with it.
    load(TEAM_GAME)
    assert catalog.Current().replaced == "tgame"
    catalog.ForgetSelection()
    assert catalog.Current().replaced is None


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_switch_followed_by_a_reload_forgets_the_previous_picks(
        world, autonaApp, monkeypatch, shell):
    """A switch to another game, then at once a reload of it (the GAME
    menu, the Web's set_game, a tournament's game): the roster each load
    asks for runs after the load is over, queued onto the main thread --
    after the reload, here. Each runs with its own load's snapshot, so the
    switch still empties the previous game's picks; read from the catalog
    when it runs, both would see the reload ("no switch") and keep them.
    Both shells' loaders' signals, through the shared wiring
    (bootstrap.wire_game_roster). Both loaders now hand each load's
    follow-up to the main thread before they select the next one (a
    run_sync marshal: ADR-005 S3f-4 / S3f-6), so the two emits come from
    the test, as from a loader's thread, with the snapshots the two loads
    published (the headless loader itself: the next test)."""
    from src import AutonaWebOperations as WebOps
    from src.core.GameAssetCatalog import GameAssetCatalog
    catalog = GameAssetCatalog.instance
    actions = AutonaActions.instance
    actions.SetRoster(2, 2, 3)
    _fillPicks()
    picked = _picks()
    assert all(all(names) for names in picked.values()), picked
    assert StateManager.Get("game.codename") == "tgame"

    if shell == "desktop":
        # The wiring bootstrap_gui() made is the one that runs.
        from src.AutonaGameAssetManager import AutonaGameAssetManager
        signals = AutonaGameAssetManager.instance.signals
    else:
        from src.AutonaWebOperations import BoardHost
        from src.bootstrap import wire_game_roster
        from src.headless import HeadlessGames
        monkeypatch.setattr(WebOps, "boardHost", BoardHost())
        signals = HeadlessGames(catalog, provider=None).signals
        wire_game_roster(signals.gameLoaded)
    published = [catalog.SelectGame(TEAM_GAME),
                 catalog.SelectGame(TEAM_GAME, reload=True)]

    def loads():
        for snapshot in published:
            signals.gameLoaded.emit(snapshot)

    worker = threading.Thread(target=loads)
    worker.start()
    worker.join()
    assert _picks() == picked, "the roster ran before both loads were over"
    autonaApp.settle()
    # TEAM_GAME's roster (two a team), every pick and main blank.
    assert _picks() == {f"{team}.{slot}.{group}": ["", "", ""]
                        for team in (1, 2) for slot in (1, 2)
                        for group in ("character", "mains")}


def test_the_headless_loader_hands_a_switch_and_a_reload_to_the_roster(
        world, autonaApp, monkeypatch):
    """The same switch and reload, asked of the headless loader itself
    (ADR-005 S3f-6): each load's follow-up runs on the main thread, which
    the loader hands it to and waits for (run_sync -> _OnLoaded ->
    gameLoaded -> bootstrap.wire_game_roster), the switch's roster with the
    game it replaced. The reload may be selected after it or supersede it
    while queued (the latest request wins); either way the previous game's
    picks are gone and the pick is persisted."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaWebOperations import BoardHost
    from src.bootstrap import wire_game_roster
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager
    from src.headless import HeadlessGames

    actions = AutonaActions.instance
    actions.SetRoster(2, 2, 3)
    _fillPicks()
    assert all(all(names) for names in _picks().values()), _picks()
    assert StateManager.Get("game.codename") == "tgame"

    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    games = HeadlessGames(GameAssetCatalog.instance, provider=None)
    wire_game_roster(games.signals.gameLoaded)
    rosters = []
    applyRoster = actions.ApplyGameRoster

    def recorded(replaced):
        rosters.append((threading.current_thread() is threading.main_thread(),
                        replaced))
        return applyRoster(replaced)

    monkeypatch.setattr(actions, "ApplyGameRoster", recorded)
    # Where the follow-up runs: a function slot runs on the main thread
    # whoever emits, so the roster's thread alone would not tell.
    followUps = []
    onLoaded = games._OnLoaded

    def followUp(snapshot):
        followUps.append(threading.current_thread() is threading.main_thread())
        return onLoaded(snapshot)

    monkeypatch.setattr(games, "_OnLoaded", followUp)

    games.LoadGameByCodename(TEAM_GAME)
    games.LoadGameByCodename(TEAM_GAME, mods_reload_mode=True)
    drain_tasks()

    assert followUps and all(followUps), followUps
    assert len(rosters) == len(followUps), (rosters, followUps)
    assert all(onMain for onMain, _ in rosters), rosters
    # The switch replaced the session's game; a reload after it replaced
    # nothing.
    assert rosters[0][1] == "tgame", rosters
    assert [codename for _, codename in rosters[1:]] in ([], [None]), rosters
    assert StateManager.Get("game.codename") == TEAM_GAME
    assert SettingsManager.Get("SELECTED_GAME_CODENAME") == TEAM_GAME
    assert _picks() == {f"{team}.{slot}.{group}": ["", "", ""]
                        for team in (1, 2) for slot in (1, 2)
                        for group in ("character", "mains")}


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_load_the_shutdown_stopped_after_its_selection_keeps_one_game(
        world, autonaApp, monkeypatch, shell):
    """The shutdown comes after a switch's selection, before the main
    thread runs what follows it (the marshal is abandoned). The selection
    is published -- and so is what goes with it, written from the loader's
    thread: the previous game's picks emptied with the new game's roster
    (committed, on the desktop's held board) and the pick persisted
    (ADR-005 S3f-4 review). Without it the next launch restored the new
    game over the old game's characters (desktop: the pick was persisted,
    the roster never ran) or the old game over the new one's state
    (headless: neither ran)."""
    from src import AutonaWebOperations as WebOps
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager
    from src.Helpers import AutonaQtHelper

    catalog = GameAssetCatalog.instance
    actions = AutonaActions.instance
    assert StateManager.Get("game.codename") == "tgame"
    if shell == "desktop":
        autonaApp.board(2)  # built: its writes are held until committed
    actions.SetRoster(2, 1, 3)
    _fillPicks()
    actions.ApplyChanges(2)
    autonaApp.settle()
    picked = _picks(read=StateManager.GetCommitted)
    assert all(all(names) for names in picked.values()), picked

    # The shutdown, from right after the selection: what run_sync gives up
    # on, and what a load checks before it starts.
    stopping = []
    realSelect = catalog.SelectGame

    def select(*args, **kwargs):
        try:
            return realSelect(*args, **kwargs)
        finally:
            stopping.append(1)

    monkeypatch.setattr(catalog, "SelectGame", select)
    monkeypatch.setattr(
        AutonaQtHelper, "gui_unavailable_reason",
        lambda: "the app is shutting down" if stopping else None)

    if shell == "desktop":
        from src.AutonaGameAssetManager import (STOPPED,
                                                AutonaGameAssetManager)
        future = AutonaGameAssetManager.instance.LoadGameByCodename(
            TEAM_GAME, mods_reload_mode=True)
        # This (the GUI) thread does not pump: the marshal is never started.
        assert future.result(10) == STOPPED
    else:
        from src.AutonaWebOperations import BoardHost
        from src.bootstrap import wire_game_roster
        from src.headless import HeadlessGames
        monkeypatch.setattr(WebOps, "boardHost", BoardHost())
        games = HeadlessGames(catalog, provider=None)
        wire_game_roster(games.signals.gameLoaded)
        worker = threading.Thread(target=lambda: games.LoadGameByCodename(
            TEAM_GAME, async_mode=False, mods_reload_mode=True))
        worker.start()
        worker.join(10)
        assert not worker.is_alive()

    assert stopping and StateManager.saveBlocked == 0
    assert StateManager.GetCommitted("game.codename") == TEAM_GAME
    assert SettingsManager.Get("SELECTED_GAME_CODENAME") == TEAM_GAME
    # TEAM_GAME's roster (two a team), every pick and main blank, published.
    blank = {f"{team}.{slot}.{group}": ["", "", ""]
             for team in (1, 2) for slot in (1, 2)
             for group in ("character", "mains")}
    assert _picks(read=StateManager.GetCommitted) == blank
    assert not StateManager.HasPendingChanges("score.2")


def test_a_load_whose_models_fail_keeps_one_game(world, autonaApp,
                                                 monkeypatch):
    """The desktop's model rebuild fails on the GUI thread after a switch's
    selection (FAILED): the load is not announced, but its selection is
    published with its cycle -- and so is what the announcement's slots
    would have written, there, before the cycle closes: the previous game's
    picks emptied with the new game's roster (committed on the held board)
    and the pick persisted, as for a load the shutdown stopped (ADR-005
    S3f-4 review). Without it the state held the new game over the old
    game's characters, and the next launch restored the old game."""
    from src.AutonaGameAssetManager import FAILED, AutonaGameAssetManager
    from src.core.SettingsManager import SettingsManager

    manager = AutonaGameAssetManager.instance
    actions = AutonaActions.instance
    assert StateManager.Get("game.codename") == "tgame"
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") != TEAM_GAME
    autonaApp.board(2)  # built: its writes are held until committed
    actions.SetRoster(2, 1, 3)
    _fillPicks()
    actions.ApplyChanges(2)
    autonaApp.settle()
    picked = _picks(read=StateManager.GetCommitted)
    assert all(all(names) for names in picked.values()), picked

    failures = []
    realRebuild = manager._RebuildModels

    def failing(decodes=None):
        if not failures:  # the load's; not a later rebuild (the teardown's)
            failures.append(threading.current_thread().name)
            raise RuntimeError("a model failed")
        return realRebuild(decodes)

    monkeypatch.setattr(manager, "_RebuildModels", failing)
    future = manager.LoadGameByCodename(TEAM_GAME, mods_reload_mode=True)
    drain_tasks()

    assert future.result(0) == FAILED
    assert failures == [threading.main_thread().name]
    assert StateManager.saveBlocked == 0
    assert StateManager.GetCommitted("game.codename") == TEAM_GAME
    assert SettingsManager.Get("SELECTED_GAME_CODENAME") == TEAM_GAME
    # TEAM_GAME's roster (two a team), every pick and main blank, published.
    blank = {f"{team}.{slot}.{group}": ["", "", ""]
             for team in (1, 2) for slot in (1, 2)
             for group in ("character", "mains")}
    assert _picks(read=StateManager.GetCommitted) == blank
    assert not StateManager.HasPendingChanges("score.2")


def test_a_request_for_the_characters_leaves_the_players_as_they_are(
        world, autonaApp):
    """SetRoster without a player count -- the characters spinbox, the
    Web's characters=N -- resizes the character slots of every slot each
    team has and touches nothing else: a team larger than the other, or a
    roster with a gap (no slot 1), keeps all its slots, and the team names
    stay."""
    actions = AutonaActions.instance
    actions.SetRoster(2, 3, 2)
    StateManager.Unset("score.2.team.1.player.1")
    actions.TrimTeamRoster(2, 2, 2)
    actions.SetTeamName(2, 1, "Team One")
    assert sorted(StateManager.Get("score.2.team.1.player")) == ["2", "3"]
    assert sorted(StateManager.Get("score.2.team.2.player")) == ["1", "2"]

    assert actions.SetRoster(2, characters=4)
    assert sorted(StateManager.Get("score.2.team.1.player")) == ["2", "3"]
    assert sorted(StateManager.Get("score.2.team.2.player")) == ["1", "2"]
    for team, slots in ((1, ("2", "3")), (2, ("1", "2"))):
        for slot in slots:
            assert len(StateManager.Get(
                f"score.2.team.{team}.player.{slot}.character")) == 4
    assert StateManager.Get("score.2.team.1.teamName") == "Team One"

    # A count of players still sets them: the gap is filled, the rest go.
    assert actions.SetRoster(2, 2)
    assert sorted(StateManager.Get("score.2.team.1.player")) == ["1", "2"]
    assert len(StateManager.Get("score.2.team.1.player.1.character")) == 4


@contextmanager
def _shown(board):
    """The board's window shown, as on screen (a popup needs it)."""
    window = board.window()
    board.setVisible(True)
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    try:
        yield
    finally:
        standIn = QWidget()
        standIn.show()
        standIn.activateWindow()
        QApplication.processEvents()
        window.hide()
        board.setVisible(False)
        standIn.hide()
        standIn.deleteLater()
        QCoreApplication.sendPostedEvents(standIn, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


def test_a_retired_card_closes_its_open_combo_list(world, autonaApp):
    """A combo's list open on a card the roster drops: the list is a popup
    window of its own (its parent is the combo, so it is no descendant of
    the card as isAncestorOf() sees one), and is closed with the card
    rather than left over a card that is gone."""
    board = autonaApp.board(2)
    AutonaActions.instance.SetRoster(2, 2, 2)
    autonaApp.settle()
    card = board.team1playerWidgets[1]
    combo = card.character_elements[0][1]
    with _shown(board):
        combo.showPopup()
        QApplication.processEvents()
        view = combo.view()
        assert view.isVisible() and QApplication.activePopupWidget(), (
            "the harness could not open the list")
        with _Lifecycle() as lifecycle:
            AutonaActions.instance.SetRoster(2, 1)
            assert card in lifecycle.retired
            assert QApplication.activePopupWidget() is None
            assert not view.isVisible()
            autonaApp.settle()
    _assertCardsFollow(board)


def test_the_same_roster_writes_nothing(world, autonaApp):
    """SetRoster with the counts the board already has -- given, or kept
    with None -- writes nothing, counted as the board counts its cards
    (AutonaActions.RosterCount)."""
    actions = AutonaActions.instance
    autonaApp.board(2)
    actions.SetRoster(2, 2, 4)
    autonaApp.settle()
    StateManager.CommitHeld("score.2")
    autonaApp.settle()
    writes = []
    realSet, realUnset = StateManager.Set, StateManager.Unset
    with patch.object(StateManager, "Set", side_effect=lambda key, value: (
            writes.append(key), realSet(key, value))), \
            patch.object(StateManager, "Unset", side_effect=lambda key: (
                writes.append(key), realUnset(key))):
        for players, characters in ((2, 4), (None, None), (2, None),
                                    (None, 4)):
            assert actions.SetRoster(2, players, characters)
    assert writes == []
    assert not StateManager.HasPendingChanges("score.2")
    assert [actions.RosterCount(2, team) for team in (1, 2)] == [2, 2]
    # A gap counts as the end of the roster, as on the board.
    StateManager.Unset("score.2.team.1.player.1")
    assert actions.RosterCount(2, 1) == 0
    assert actions.RosterCount(2, 2) == 2


def test_a_board_card_that_fails_to_go_does_not_keep_the_others(
        world, autonaApp):
    """The defences around the cards' lifecycle: a retired card whose
    destruction raises leaves the others to be destroyed, and a card sync
    that raises does not stop the CommitHeld a game load ends with."""
    board = autonaApp.board(2)
    board.playerNumber.setValue(3)
    autonaApp.settle()
    StateManager.CommitHeld("score.2")
    autonaApp.settle()
    gone = board.team1playerWidgets[1:] + board.team2playerWidgets[1:]
    destroy = AutonaScoreboardPlayerWidget.Destroy

    def _destroy(card):
        if card is gone[0]:
            raise ValueError("boom")
        destroy(card)

    with patch.object(AutonaScoreboardPlayerWidget, "Destroy", _destroy), \
            _Lifecycle() as lifecycle:
        board.playerNumber.setValue(1)
        autonaApp.settle()
        lifecycle.reap()
        assert not any(Shiboken.isValid(card) for card in gone[1:])
    if Shiboken.isValid(gone[0]):
        destroy(gone[0])
        autonaApp.settle()

    def _raise(_board):
        raise RuntimeError("sync failed")

    board.playerNumber.setValue(2)
    assert StateManager.HasPendingChanges("score.2")
    with patch.object(AutonaScoreboardWidget, "_SyncPlayerCards", _raise):
        board._onGameAssetsLoaded()
    assert not StateManager.HasPendingChanges("score.2"), (
        "the load's CommitHeld did not run")
    autonaApp.settle()
