"""Board operations publish what they always did (ADR-005 S2 onwards).

The operations an operator runs on a board -- fill, swap, move a player,
reset, clear, a set load, reset-all, the set length -- were recorded on the
tree before each migration that moved them into core, and are replayed
here: the board's whole subtree after every step (key order included), the
save cycles each step took, the statistics it asked for and what 反映 then
published. What a migration changed on purpose is listed step by step
(S2B_CHANGES and the tables after it) and applied to the recording before
the comparison, so every other field of every step still has to match.

    AUTONA_RECORD_BOARD_OPERATIONS=1 python -m pytest tests/test_board_operations.py

(on a pre-migration checkout with this file and fixtures/board_operations
copied in). Re-record only when the fixture games or the country data
change.

- ``golden.json``: recorded on the last pre-S2a tree (bd0fa50b), extended
  on the last pre-S2b tree (76a740fb; the original fourteen steps came out
  identical to the bd0fa50b recording).
- ``swap_golden.json`` (S2c, the last pre-S2c tree 6a6f04cc): a board with
  everything a swap carries -- ids, a city on one side only, distinct
  mains, a team crest, the game tracker on -- swapped, its players moved,
  and swapped back, with the statistics fetches each step started. The key
  order of every dict after every step of both lists is recorded on that
  tree too (``key_order.json``), so the order stays pinned where S2c
  changed values and the state digest is this tree's own.
- ``clear_golden.json`` (S2d, pre-S2d 2045c973): every clear from every
  entry point; ``best_of_golden.json`` (S2e, pre-S2e 79da38bf): every way
  the set length is set.

The recordings drove the Qt SCORE -- its spinboxes, buttons, LOSERS boxes,
team-name fields, stage buttons and player cards. Those went in ADR-005
S4ac-3, and each step now calls what the widget ran: the core operation
its handler called, in the same save cycles (the step helpers below). A
spinbox or a checkbox that wrote only on a change is asked only for one.
The recordings are unchanged. What this file also pinned until then --
every card, LOSERS box, BEST OF spinbox and stage row showing the state
after every step, and the cards' render writing nothing (ADR-005 S2a) --
went with the widgets; it was tests/test_player_card_render.py.

The fixture world (games, custom flags, locale) is the S1d recording's
(tests/test_player_slot_publish.py), so the character slots have real
rows, skins (one of which is a different character -- Dr. Mario) and
variants.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import orjson
import pytest

from src import AutonaWebOperations as WebOps
from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.DictHelper import deep_clone
from src.core.GameTrackerService import TEAM_1, TEAM_2, UNREPORTED
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.SetFollowService import SetFollowService
from src.core.SetLoadService import SetLoadService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from tests.conftest import (apply_set, clear_players, edit_tag, load_player,
                            tournamentLoaded)
# The S1d world: the fixture games under the recording's locale. Imported so
# both recordings are made in exactly the same one.
from tests.test_player_slot_publish import slotEnv  # noqa: F401  (fixture)
# /api/v1 through the real route table, for the best-of recordings (S2e).
from tests.test_api_v1 import client, webApp  # noqa: F401  (fixtures)
from tests.fixtures.player_slots.recording import digest, encode
from tests.fixtures.stats.spies import StatsTriggers

RECORD = os.environ.get("AUTONA_RECORD_BOARD_OPERATIONS") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures"
               / "board_operations" / "golden.json")
GOLDEN = None if RECORD else json.loads(
    GOLDEN_PATH.read_text(encoding="utf-8"))
# The swap / move operations with everything they move set up (ids, a city,
# an avatar, distinct mains, a team crest, the game tracker on). Recorded on
# the last pre-S2c tree (6a6f04cc) with `-k swap_operations`; golden.json
# above is still the pre-S2b recording, so the two are recorded separately.
SWAP_GOLDEN_PATH = GOLDEN_PATH.parent / "swap_golden.json"
SWAP_GOLDEN = None if RECORD or not SWAP_GOLDEN_PATH.exists() else json.loads(
    SWAP_GOLDEN_PATH.read_text(encoding="utf-8"))
# The clear operations (score, match, players, the whole board) from every
# entry point -- the board's buttons, a set load's pre-pass and the Web
# actions -- on a board holding everything a clear has to empty: one section
# for the team board ("operations"), one for the singles board ("singles")
# and one for the set load's pre-pass with game rows recorded ("prepass").
# Recorded on the last pre-S2d tree (2045c973) with `-k clear_operations`;
# each test writes its own section (and reads it back, _replayClears).
CLEAR_GOLDEN_PATH = GOLDEN_PATH.parent / "clear_golden.json"
# The key order of every dict of the board subtree after each step of both
# step lists, recorded on the last pre-S2c tree (6a6f04cc):
#
#     AUTONA_RECORD_KEY_ORDER=1 python -m pytest tests/test_board_operations.py -k recorded_state
#
# The state digest pins the key order only where the state is unchanged;
# this pins it where S2c changed values on purpose too (the swap exchanges
# the slots key by key so that each dict keeps the order the old copy left
# it in -- program_state.json and the Socket.IO delta keep their order).
RECORD_KEY_ORDER = os.environ.get("AUTONA_RECORD_KEY_ORDER") == "1"
KEY_ORDER_PATH = GOLDEN_PATH.parent / "key_order.json"
KEY_ORDER = None if not KEY_ORDER_PATH.exists() else json.loads(
    KEY_ORDER_PATH.read_text(encoding="utf-8"))

BOARD = 2   # the team board: two players a side, team names, move up/down
PREFIX = f"score.{BOARD}"


# ── observing a board ────────────────────────────────────────────────────

class _Cycles:
    """held_state_updated notifications for the board, i.e. save cycles."""

    def __init__(self, prefix=PREFIX):
        self.count = 0
        self._prefix = prefix

    def _onHeld(self, prefix):
        if prefix == self._prefix:
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


# How often each of the board's statistics triggers fired. Each one runs a
# Start.gg fetch (or a local recalculation), so firing one twice for one
# operation is a wasted request and not firing it leaves the previous
# pairing's numbers on screen. The recordings name the board's old
# AutonaStatsUtil signals; since ADR-005 S3e-5 they are core's
# StatsService.Refresh, which the spy counts under those names.
_StatsTriggers = StatsTriggers


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


def _boardState(app, prefix=PREFIX):
    with StateManager.lock:
        value = deep_clone(StateManager.Get(prefix) or {})
    exact = _normalise(encode(value), app)
    readable = json.loads(_normalise(orjson.dumps(
        _digestAssets(value),
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS).decode(), app))
    return {"state": readable, "stateDigest": digest(exact)}


# Values whose contents are not walked for the key order: the asset tables
# (the state digest covers them byte for byte) and what S2c makes travel
# with a player instead of staying on the card -- a character's variant
# (the old copy left it empty) and the mains. Their keys in the dict around
# them still count.
_KEY_ORDER_OPAQUE = frozenset(("assets", "variant", "mains"))


def _keyOrders(value, path=""):
    """{dot path: [keys in insertion order]} for every dict in `value`."""
    orders = {}
    if isinstance(value, dict):
        orders[path] = [str(key) for key in value]
        for key, item in value.items():
            if key not in _KEY_ORDER_OPAQUE:
                orders.update(_keyOrders(
                    item, f"{path}.{key}" if path else str(key)))
    return orders


def _boardKeyOrder(prefix=PREFIX):
    with StateManager.lock:
        return _keyOrders(deep_clone(StateManager.Get(prefix) or {}))


# ── the operations ───────────────────────────────────────────────────────
#
# What the Qt SCORE's widgets ran, called directly (they went in ADR-005
# S4ac-3). `board` is a board number.

def _setPlayers(board, players):
    """The PLAYERS per team spinbox set to `players`: clamped to its range,
    and asked only when that changes what it showed (team 1's cards) --
    valueChanged fired for nothing else."""
    players = max(0, min(players, AutonaActions.MAX_PLAYERS_PER_TEAM))
    if players != AutonaActions.RosterCount(board, 1):
        AutonaActions.instance.SetPlayersPerTeam(board, players)


def _setCharacters(board, characters):
    """The board's hidden characters per player spinbox, as _setPlayers:
    what it showed was the first slot's character slots, else the count
    kept for a board with none."""
    actions = AutonaActions.instance
    characters = max(0, min(characters,
                            AutonaActions.MAX_CHARACTERS_PER_PLAYER))
    shown = actions.CharacterSlots(board)
    if shown is None:
        shown = actions.EmptyRosterCharacters(board)
    if characters != shown:
        actions.SetCharactersPerPlayer(board, characters)


def _setBestOf(board, value):
    """The BEST OF spinbox set to `value`: SetBestOf, which clamps it to the
    spinbox's range and writes nothing for the length the board has, as
    valueChanged did not fire for the value the spinbox showed."""
    AutonaActions.instance.SetBestOf(board, value)


def _score(board, team, delta):
    """A team's +1 / -1 (CommandScoreChange). `team` is 1-based."""
    AutonaActions.instance.AdjustScore(board, team, delta)


def _losers(board, team, value=True):
    """A team's LOSERS box set: SetTeamLosers, which writes only on a
    change, as toggled fired only on one."""
    AutonaActions.instance.SetTeamLosers(board, team, value)


def _teamName(board, team, text):
    """A team-name field finished with `text`: the name, then its crest
    (SetTeamName, then ExportTeamLogo's PublishTeamLogo), each in its own
    save cycle. The Web's set_route writes the pair in one."""
    AutonaActions.instance.SetTeamName(board, team, text)
    SetLoadService(board).PublishTeamLogo(team, text)


def _swap(board):
    """The SWAP button."""
    AutonaActions.instance.SwapTeams(board)


def _move(board, team, slot, step):
    """A card's move up (-1) / down (+1): its slot exchanged with the
    neighbouring one. The ends of the list are not a move."""
    other = max(1, min(AutonaActions.RosterCount(board, team), slot + step))
    if other != slot:
        AutonaActions.instance.SwapPlayers(board, team, slot, other)


def _resetAll(board):
    """すべての入力値をリセット, answered Yes: AutonaActions.ClearBoard in
    a save block, the set loaded last forgotten -- what v1's :reset-all
    runs."""
    WebOps.reset_all(board)


def _pickCharacter(path, row, character):
    """A card's character combo on row `row` moved to `character`: the skin
    combo took the character's skins and its first row, which published the
    slots, then the character combo published them again -- two save cycles
    of the same value. The other rows, and the row's variant, keep what the
    card showed: the slot's own."""
    player = PlayerService(path)
    values = StateManager.Get(player.Key("character")) or {}
    slots = [player.SlotFromValue(values.get(index, values.get(str(index))))
             for index in range(1, len(values) + 1)]
    slots[row - 1] = CharacterSlot(character, 0, slots[row - 1].variant,
                                   character)
    player.PublishCharacterSlots(slots)
    player.PublishCharacterSlots(slots)


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
    _fillFrom(board, team, PLAYERS)


def _steps(board):
    """What an operator (or a set load) does to a board, in order."""
    return [
        ("two players, two characters", lambda: (
            _setPlayers(board, 2), _setCharacters(board, 2))),
        ("fill team 1", lambda: _fill(board, 1)),
        ("fill team 2", lambda: _fill(board, 2)),
        ("team names", lambda: (_teamName(board, 1, "Alpha"),
                                _teamName(board, 2, "Bravo"))),
        ("score", lambda: (_score(board, 1, 2), _score(board, 2, 1))),
        ("losers", lambda: _losers(board, 2)),
        ("swap teams", lambda: _swap(board)),
        ("move a player down", lambda: _move(board, 1, 1, 1)),
        ("swap teams back", lambda: _swap(board)),
        # Row 1 of team 2's second card: its combo's row 5.
        ("pick a character", lambda: _pickCharacter(
            f"score.{board}.team.2.player.2", 1, "Peach")),
        ("reset match", lambda: AutonaActions.instance.ResetMatch(board)),
        ("clear players", lambda: clear_players(board)),
        ("load a set", lambda: apply_set(board, {
            "round_name": "Winners Final", "team1score": 1,
            "team2score": 0, "overwrite": True,
            "entrants": [
                [{"gamerTag": "E1", "country_code": "JP",
                  "state_code": "27",
                  "mains": {"tgame": [["Zelda", 0]]}}],
                [{"gamerTag": "E2", "prefix": "PX", "seed": 4}]]})),
        ("reset all inputs", lambda: _resetAll(board)),
        # The losers flag and the " [L]" it puts on every name of its team
        # (S2b moved that suffix from the cards to core). Recorded on the
        # pre-S2b tree (76a740fb).
        ("two players again", lambda: (
            _setPlayers(board, 2), _fill(board, 1), _fill(board, 2))),
        ("losers on a filled team", lambda: _losers(board, 1)),
        ("rename on a losers team",
         lambda: edit_tag(f"score.{board}.team.1.player.1", "ALPHA2")),
        ("grow the roster on a losers team", lambda: _setPlayers(board, 3)),
        ("back to one player", lambda: _setPlayers(board, 1)),
        ("load a losers set that grows the roster", lambda: apply_set(board, {
            "team1losers": False, "team2losers": True, "overwrite": True,
            "entrants": [
                [{"gamerTag": "L1"}, {"gamerTag": "L2", "prefix": "LP"}],
                [{"gamerTag": "W1", "prefix": "WP"}, {"gamerTag": "W2"}]]})),
        ("swap teams with a losers side", lambda: _swap(board)),
        ("load the losers flags while swapped", lambda: apply_set(board, {
            "team1losers": True, "team2losers": False})),
    ]


# ── the swap / move operations (ADR-005 S2c) ─────────────────────────────

# PLAYERS plus what a swap has to carry across that the first recording's
# players do not have: provider ids (the statistics fetches hang off them)
# and a city on a player whose partner has none.
SWAP_PLAYERS = {
    (1, 0): dict(PLAYERS[(1, 0)], id=[101, 1101]),
    (1, 1): dict(PLAYERS[(1, 1)]),
    (2, 0): dict(PLAYERS[(2, 0)], id=[303, 3303]),
    (2, 1): dict(PLAYERS[(2, 1)], id=[404, 4404], city="Osaka"),
}

# A crest for team "Alpha" only (SetLoadService.PublishTeamLogo looks it up
# by the lower-cased name), so one side has a logo and the other has none.
TEAM_LOGO = Path("user_data") / "team_logo" / "alpha.png"

_realSettingsGet = SettingsManager.Get


def _trackerEnabled(key, default=None):
    # The individual game tracker ships disabled; patched rather than
    # written, as in tests/test_scoreboard_operations.py.
    if key == "general.disable_individual_game_tracker":
        return False
    return _realSettingsGet(key, default)


def _withTracker(operation):
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        operation()


def _gameLoaded():
    """What a game load runs: what the shared wiring writes (bootstrap.
    wire_game_roster -> ApplyGameRoster; it was the board's handler's until
    ADR-005 S3e-6, the set length until S4t) and the commit the asset
    adapter runs after every slot. The board's own onLoad handler, which
    rendered and wrote nothing, ran in between until S4ac-3."""
    actions = AutonaActions.instance
    actions.ApplyGameRoster()
    actions.RepublishBestOfs()
    actions.CommitGameLoad()


def _clickStage(board, game, team):
    """A click on the T1 / T2 button of game `game`'s row (1-based): the
    button toggled, so the row goes to that team -- or back to unreported
    when it had it -- and a row that was written sums the score from the
    rows (the tracker's _StageResultClicked)."""
    tracker = AutonaActions.instance.GameTracker(board)
    index = game - 1
    assert index < tracker.StageCount(), (board, game)
    result = TEAM_1 if team == 1 else TEAM_2
    winner = UNREPORTED if tracker.GameWinner(index) == result else result
    if tracker.SetStageResult(index, winner):
        tracker.PublishStageResults()


def _fillFrom(board, team, players):
    """Every player of `team` loaded from `players`, as each card's
    SetData(dontLoadFromDB=True) loaded it (PlayerService.ApplyData)."""
    for index in range(AutonaActions.RosterCount(board, team)):
        load_player(board, team, dict(players[(team, index)]), index + 1)


def _swapSteps(board):
    """A team board set up with everything a swap moves, then swapped,
    its players moved, and swapped back."""
    return [
        ("two players, two characters", lambda: (
            _setPlayers(board, 2), _setCharacters(board, 2))),
        ("tracker on, best of 5",
         lambda: _withTracker(lambda: _setBestOf(board, 5))),
        ("fill team 1", lambda: _fillFrom(board, 1, SWAP_PLAYERS)),
        ("fill team 2", lambda: _fillFrom(board, 2, SWAP_PLAYERS)),
        # A character-count change republishes every slot's characters as
        # its mains, so the four slots' mains differ from here on.
        ("three characters", lambda: _setCharacters(board, 3)),
        ("team names", lambda: (_teamName(board, 1, "Alpha"),
                                _teamName(board, 2, "Bravo"))),
        # T2, T1, T1: 2-1, and an order a swap has to keep (mirrored).
        ("stage results", lambda: _stageResults(board)),
        ("losers", lambda: _losers(board, 2)),
        ("swap teams", lambda: _swap(board)),
        ("move a player down", lambda: _move(board, 1, 1, 1)),
        ("move a player up", lambda: _move(board, 2, 2, -1)),
        ("move the top player up", lambda: _move(board, 1, 1, -1)),
        ("a point with the tracker on", lambda: _score(board, 2, 1)),
        ("swap teams back", lambda: _swap(board)),
    ]


# ── the clear operations (ADR-005 S2d) ───────────────────────────────────

_SET_MATCH_BAR = {"round_name": "Winners Final",
                  "round_name_en": "Winners Final",
                  "tournament_phase": "Top 8", "tournament_pool": "Pool A"}


def _fillBoth(board):
    _fillFrom(board, 1, SWAP_PLAYERS)
    _fillFrom(board, 2, SWAP_PLAYERS)


def _everything(board, teamNames=True):
    """A board holding everything a clear empties: two filled teams with
    names (one with a crest), a match bar, a score and a losers side.
    `teamNames=False` for the singles board, which shows none."""
    _fillBoth(board)
    if teamNames:
        _teamName(board, 1, "Alpha")
        _teamName(board, 2, "Bravo")
    apply_set(board, dict(_SET_MATCH_BAR))
    _score(board, 1, 1)
    _score(board, 2, 2)
    _losers(board, 2)


def _clearSteps(board, app):
    """Every clear, from every entry point, on a team board that has
    something for each of them to empty -- ids, a city, an online avatar,
    notes, regions, variants, mains, seeds, team names and a crest, a match
    bar, the game tracker on -- refilled in between."""
    actions = app.web()
    return [
        ("two players, two characters", lambda: (
            _setPlayers(board, 2), _setCharacters(board, 2))),
        ("tracker on, best of 5",
         lambda: _withTracker(lambda: _setBestOf(board, 5))),
        ("fill both teams", lambda: _fillBoth(board)),
        ("team names", lambda: (_teamName(board, 1, "Alpha"),
                                _teamName(board, 2, "Bravo"))),
        ("a match bar", lambda: apply_set(board, dict(_SET_MATCH_BAR))),
        # T2, T1, T1: 2-1 on the rows and on the score.
        ("stage results", lambda: _stageResults(board)),
        ("losers", lambda: _losers(board, 2)),
        ("clear the score", lambda: AutonaActions.instance.ClearScore(board)),
        ("score and losers again", lambda: (
            _score(board, 1, 1), _score(board, 2, 2), _losers(board, 1))),
        ("reset match", lambda: AutonaActions.instance.ResetMatch(board)),
        ("a set with a match bar, a score and losers",
         lambda: apply_set(board, dict(
             _SET_MATCH_BAR, team1score=2, team2score=1, team1losers=True))),
        ("web reset match", lambda: actions.reset_match(BOARD)),
        ("tracker on, best of 3",
         lambda: _withTracker(lambda: _setBestOf(board, 3))),
        ("web set route", lambda: actions.set_route(
            BOARD, phase="Bracket", pool="Pool B", match="Grand Final",
            losers="true", team="2")),
        ("web team name", lambda: actions.set_route(
            BOARD, team="1", teamName="Charlie")),
        ("web team name unchanged", lambda: actions.set_route(
            BOARD, team="1", teamName="Charlie")),
        ("swap teams", lambda: _swap(board)),
        # Set-shaped: team 1 of the set is the right column now.
        ("web losers while swapped", lambda: actions.set_route(
            BOARD, losers="false", team="1")),
        ("web team name while swapped", lambda: actions.set_route(
            BOARD, team="2", teamName="Delta")),
        ("swap teams back", lambda: _swap(board)),
        ("web empty match", lambda: actions.set_route(BOARD, match="")),
        ("clear players keeping mains",
         lambda: clear_players(board, no_mains=True)),
        ("fill both teams again", lambda: _fillBoth(board)),
        ("one character", lambda: _setCharacters(board, 1)),
        ("clear players keeping mains with one character",
         lambda: clear_players(board, no_mains=True)),
        ("two characters and refill", lambda: (
            _setCharacters(board, 2), _fillBoth(board))),
        ("clear players", lambda: clear_players(board)),
        ("refill", lambda: _fillBoth(board)),
        ("web reset players", lambda: actions.reset_players(BOARD)),
        ("refill everything", lambda: _everything(board)),
        ("web clear all", lambda: actions.clear_all(BOARD)),
        ("two players, two characters, best of 5 and refill everything",
         lambda: (_setPlayers(board, 2),
                  _setCharacters(board, 2),
                  _withTracker(lambda: _setBestOf(board, 5)),
                  _everything(board))),
        ("reset all inputs", lambda: _resetAll(board)),
        ("refill everything again", lambda: _everything(board)),
        # A set load's pre-pass: players (keeping the mains) and the score,
        # not the match bar. The follow timers are stopped in the same step
        # so that none fires during the steps that follow.
        ("follow a set", lambda: _follow(board, "999", True)),
    ]


def _stageResults(board):
    # T2, T1, T1: 2-1 on the rows and on the score.
    _clickStage(board, 1, 2)
    _clickStage(board, 2, 1)
    _clickStage(board, 3, 1)


def _follow(board, setId, noMains):
    """A set chosen to follow, as the set / station / stream windows did
    (the SCORE page's set dialog now), with a tournament loaded that
    answers nothing (a set loads only then); the polling stopped at once
    so that none fires later (the Qt board's StopAutonaUpdate())."""
    follow = SetFollowService(board)
    with tournamentLoaded():
        follow.LoadSet({"id": setId, "auto_update": "set",
                        "no_mains": noMains})
    follow.StopFollowing()


def _singlesClearSteps(board, app):
    """The clears of `_clearSteps` on the singles board (board 1, the
    shipped default): one player a side, no team names, the LOSERS boxes
    on the players' detail rows."""
    actions = app.web()
    number = board
    return [
        ("two characters", lambda: _setCharacters(board, 2)),
        ("tracker on, best of 5",
         lambda: _withTracker(lambda: _setBestOf(board, 5))),
        ("fill both players", lambda: _fillBoth(board)),
        ("a match bar", lambda: apply_set(board, dict(_SET_MATCH_BAR))),
        ("stage results", lambda: _stageResults(board)),
        ("losers", lambda: _losers(board, 2)),
        ("clear the score", lambda: AutonaActions.instance.ClearScore(board)),
        ("score and losers again", lambda: (
            _score(board, 1, 1), _score(board, 2, 2), _losers(board, 1))),
        ("reset match", lambda: AutonaActions.instance.ResetMatch(board)),
        ("a set with a match bar, a score and losers",
         lambda: apply_set(board, dict(
             _SET_MATCH_BAR, team1score=2, team2score=1, team1losers=True))),
        ("web reset match", lambda: actions.reset_match(number)),
        ("clear players keeping mains",
         lambda: clear_players(board, no_mains=True)),
        ("fill both players again", lambda: _fillBoth(board)),
        ("one character", lambda: _setCharacters(board, 1)),
        ("clear players keeping mains with one character",
         lambda: clear_players(board, no_mains=True)),
        ("two characters and refill", lambda: (
            _setCharacters(board, 2), _fillBoth(board))),
        ("clear players", lambda: clear_players(board)),
        ("refill", lambda: _fillBoth(board)),
        ("web reset players", lambda: actions.reset_players(number)),
        ("refill everything",
         lambda: _everything(board, teamNames=False)),
        ("web clear all", lambda: actions.clear_all(number)),
        ("two characters, best of 5 and refill everything", lambda: (
            _setCharacters(board, 2),
            _withTracker(lambda: _setBestOf(board, 5)),
            _everything(board, teamNames=False))),
        ("reset all inputs", lambda: _resetAll(board)),
        ("refill everything again",
         lambda: _everything(board, teamNames=False)),
        ("follow a set without keeping mains",
         lambda: _follow(board, "999", False)),
    ]


def _prepassSteps(board):
    """A set load's pre-pass (the players, then the score) on a team board
    whose game tracker has rows recorded -- without keeping the mains, then
    keeping them -- and the set followed already chosen again, which skips
    it."""
    return [
        ("two players, two characters", lambda: (
            _setPlayers(board, 2), _setCharacters(board, 2))),
        ("tracker on, best of 5",
         lambda: _withTracker(lambda: _setBestOf(board, 5))),
        ("fill both teams", lambda: _fillBoth(board)),
        ("team names", lambda: (_teamName(board, 1, "Alpha"),
                                _teamName(board, 2, "Bravo"))),
        ("a match bar", lambda: apply_set(board, dict(_SET_MATCH_BAR))),
        ("stage results", lambda: _stageResults(board)),
        ("losers", lambda: _losers(board, 2)),
        ("follow a set without keeping mains",
         lambda: _follow(board, "999", False)),
        ("refill, stage results and losers", lambda: (
            _fillBoth(board), _stageResults(board), _losers(board, 1))),
        ("follow another set keeping mains",
         lambda: _follow(board, "1000", True)),
        ("refill and stage results", lambda: (
            _fillBoth(board), _stageResults(board))),
        ("follow the same set again", lambda: _follow(board, "1000", True)),
    ]


def _runSteps(app, board, afterEach=None, steps=None, stats=False,
              keyOrders=None):
    # The board's own subtree: the team board's everywhere but the singles
    # clears (ADR-005 S2d review).
    prefix = f"score.{board}"
    recorded = []
    for name, step in (steps if steps is not None else _steps(board)):
        with _Cycles(prefix) as cycles, _StatsTriggers(board) as triggers:
            step()
            app.settle()
        entry = {"step": name, "cycles": cycles.count}
        if stats:
            entry["stats"] = triggers.counts
        entry.update(_boardState(app, prefix))
        if keyOrders is not None:
            keyOrders.append({"step": name,
                              "keyOrder": _boardKeyOrder(prefix)})
        if afterEach is not None:
            afterEach(name)
        with _Published() as published:
            StateManager.CommitHeld(prefix)
            app.settle()
        entry["published"] = [len(published.deltas), published.bigChanges,
                              digest(_normalise(published.encoded(), app))]
        entry["publishedRows"] = digest(
            _normalise(published.encoded(sortKeys=True), app))
        recorded.append(entry)
    return recorded


@pytest.fixture
def board(slotEnv, autonaApp):
    """The team board's number, the board made afresh as the launch makes
    it."""
    slotEnv("tgame")
    return autonaApp.ready(BOARD)


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


# What ADR-005 S2c changed on purpose. The swap and the player moves were a
# copy from card to card (SwapWith); they exchange the slots in the state
# now (AutonaActions.SwapTeams / SwapPlayers), which fixed what the copy
# lost on the way:
#
# - `travel`: a player's mains and character variants go where the player
#   goes. The copy re-picked each character by its en_name and dropped the
#   variant (Dr. Mario "EX" arrived as plain Dr. Mario), and it never moved
#   the mains at all -- they stayed on the card, under the next player's
#   name. The value is the step whose state says where every player's were;
#   only a character the player still has keeps its variant.
# - `stages`: with the game tracker on, the mirrored rows match the swapped
#   score. The copy's score change re-awarded rows on top of the mirror, so
#   T2 T1 T1 (2-1) came out as T1 T2 T1 -- 1-2 on the score, 2-1 on the rows.
# - `stats`: the statistics are fetched once per swap / move, not once per
#   card the copy rewrote (and the upset factor is recalculated by the
#   operation, not by a seed spinbox the copy typed into).
#
# The digests follow from those; `published` turns into a whole-state
# change (more than 100 rows) where a move now carries the mains along.
#
# Changed on purpose but invisible here: the birthday travels with its
# player too (the copy left the hidden checkbox on the card; every player in
# these steps has birthday false), and the seed is always written as an int
# on both slots, as SetSeed did (pinned in tests/test_actions.py).
S2C_CHANGES = {
    "swap teams": {"travel": "losers",
                   "stateDigest": "82e1ee0a06fda06b"},
    "move a player down": {"travel": "losers",
                           "stateDigest": "4a0a0849a8ce31d7"},
    "swap teams back": {"travel": "losers",
                        "stateDigest": "6ff0aa17538c4e6c"},
    "pick a character": {"travel": "losers",
                         "stateDigest": "9caeb95f5a26109c"},
    "reset match": {"travel": "losers",
                    "stateDigest": "cb954b6e189e0039"},
    # The state is the recording's; what 反映 publishes now also takes the
    # two variants the swap kept off the overlays.
    "clear players": {"published": [1, 0, "ee9f778f6f33304e"],
                      "publishedRows": "16c902f8950f4403"},
}

# Every statistic once: the head-to-head, and each side's last sets and
# standings -- both sides for a swap, the moving team's for a move.
_SWAP_STATS = {
    "RecentSetsSignal": 1, "LastSetsP1Signal": 1, "LastSetsP2Signal": 1,
    "PlayerHistoryStandingsP1Signal": 1, "PlayerHistoryStandingsP2Signal": 1,
    "UpsetFactorCalculation": 0}

S2C_SWAP_CHANGES = {
    "swap teams": {
        "travel": "losers", "stages": ("T1", "T2", "T2", "-", "-"),
        "stats": _SWAP_STATS,
        "stateDigest": "b115e92c30acde89"},
    "move a player down": {
        "travel": "losers", "stages": ("T1", "T2", "T2", "-", "-"),
        "stats": dict(_SWAP_STATS, LastSetsP2Signal=0,
                      PlayerHistoryStandingsP2Signal=0),
        "stateDigest": "490df9db64929ca2",
        "published": [1, 0, "8d2b9cc3ecfbdb4a"],
        "publishedRows": "2c1c7f9209c534b3"},
    "move a player up": {
        "travel": "losers", "stages": ("T1", "T2", "T2", "-", "-"),
        "stats": dict(_SWAP_STATS, LastSetsP1Signal=0,
                      PlayerHistoryStandingsP1Signal=0),
        "stateDigest": "6423979a92d3fe2e",
        "published": [0, 1, "b3283bf184bb082f"],
        "publishedRows": "b3283bf184bb082f"},
    "move the top player up": {
        "travel": "losers", "stages": ("T1", "T2", "T2", "-", "-"),
        "stateDigest": "6423979a92d3fe2e"},
    "a point with the tracker on": {
        "travel": "losers", "stages": ("T1", "T2", "T2", "T2", "-"),
        "stateDigest": "953693a4d02018b3"},
    "swap teams back": {
        "travel": "losers", "stages": ("T2", "T1", "T1", "T1", "-"),
        "stats": _SWAP_STATS,
        "stateDigest": "f129ff86c1afb141"},
}

# What ADR-005 S2d changed on purpose. The clears were the board's widgets
# driven back to empty -- each player card's Clear() a save cycle of its
# own, the score spinboxes' valueChanged writing the score and, with the
# game tracker on, clearing the stage rows inside that same write. They are
# core operations on the state now (AutonaActions.ClearScore / ResetMatch /
# ClearPlayers / ClearBoard): the state, its key order and what 反映
# publishes are the recording's at every step, and so is what every card
# shows. What differs:
#
# - `cycles`: clearing the players is one save cycle (it was one per card).
#   The score is written in one cycle, and the stage rows follow it from the
#   board's held cycle like after every other core score write (ResetScore,
#   a +1): one cycle per row cleared. So with the tracker on and games
#   recorded, a clear of the score takes 1 + one per recorded game -- fewer
#   than before for ClearScore (each score and each row was a cycle), more
#   for ResetMatch (its block used to cover the rows too). Tracker off (the
#   shipped default), it is one.
# - `stats`: the statistics are fetched once per clear that dropped player
#   ids, for the teams it dropped them on (the board, from the state), not
#   once per card; the upset factor is recalculated by the operation.
# - a team name set from the Web is one cycle (it was the name, then the
#   crest).
_CLEAR_STATS = {
    "RecentSetsSignal": 1, "LastSetsP1Signal": 1, "LastSetsP2Signal": 1,
    "PlayerHistoryStandingsP1Signal": 1, "PlayerHistoryStandingsP2Signal": 1,
    "UpsetFactorCalculation": 0}

S2D_CLEAR_CHANGES = {
    "clear the score": {"cycles": 4},
    "reset match": {"cycles": 4},
    "web reset match": {"cycles": 5},
    "web team name": {"cycles": 1},
    "clear players keeping mains": {"cycles": 1, "stats": _CLEAR_STATS},
    "clear players keeping mains with one character": {
        "cycles": 1, "stats": _CLEAR_STATS},
    "clear players": {"cycles": 1, "stats": _CLEAR_STATS},
    "web reset players": {"cycles": 1, "stats": _CLEAR_STATS},
    # The reset's 1 + 3 game rows, the best of, the two counts (3) and the
    # players (1; it was 2 cards).
    "web clear all": {"cycles": 9, "stats": _CLEAR_STATS},
    "reset all inputs": {"stats": _CLEAR_STATS},
    "follow a set": {"stats": _CLEAR_STATS},
}

# The same on the first recording: its players' clear (no ids, tracker off).
S2D_CHANGES = {
    "clear players": {"cycles": 1},
}

# What the S3e-3 review changed on purpose on the first recording: a load
# empties the city (and the id) its player does not bring. ALPHA is the only
# player here with one (Shibuya); the players' clear and the reset keep a
# city (they did and do), so the set loaded over the cleared slot -- E1, then
# L1, both without a city -- used to carry ALPHA's onto the overlays. Now
# their slot's city is None, and stays so through the reset and the swap
# that follow, until ALPHA is filled in again ("two players again", whose
# 反映 now publishes the city coming back). No id is involved: nobody here
# has one. Key order and cycles are the recording's (`city` was there).
_LOADED_WITHOUT_CITY = {"team.1.player.1.city": None}
_SWAPPED_WITHOUT_CITY = {"team.2.player.1.city": None}
S3E3_CHANGES = {
    "load a set": {"values": _LOADED_WITHOUT_CITY,
                   "stateDigest": "5512f4278e8680e9",
                   "published": [1, 0, "c720b65c91d82f4a"],
                   "publishedRows": "36352a38814765a4"},
    "reset all inputs": {"values": _LOADED_WITHOUT_CITY,
                         "stateDigest": "edbbd9b0db4c6ed0"},
    "two players again": {"published": [1, 0, "32191436f2a81ba5"],
                          "publishedRows": "21f19cada23d2128"},
    "load a losers set that grows the roster": {
        "values": _LOADED_WITHOUT_CITY,
        "stateDigest": "f82b4610534ef381",
        "published": [1, 0, "698940506e604a8b"],
        "publishedRows": "5a1cb15a7bcefc5a"},
    "swap teams with a losers side": {
        "values": _SWAPPED_WITHOUT_CITY,
        "stateDigest": "6ce6ed70026d354c",
        "published": [1, 0, "eabab7e92c573fe1"],
        "publishedRows": "ea04b81f72c965ed"},
    "load the losers flags while swapped": {
        "values": _SWAPPED_WITHOUT_CITY,
        "stateDigest": "263b37a06735b137"},
}

# The singles board (ADR-005 S2d review): the same differences, with one
# card a side -- the players' clear was 2 cycles (a card each) and fetched
# the recent sets and recalculated the upset factor once per card.
S2D_SINGLES_CHANGES = {
    "clear the score": {"cycles": 4},
    "reset match": {"cycles": 4},
    "web reset match": {"cycles": 5},
    "clear players keeping mains": {"cycles": 1, "stats": _CLEAR_STATS},
    "clear players keeping mains with one character": {
        "cycles": 1, "stats": _CLEAR_STATS},
    "clear players": {"cycles": 1, "stats": _CLEAR_STATS},
    "web reset players": {"cycles": 1, "stats": _CLEAR_STATS},
    # No game rows by then (the Web reset took the best of): the players'
    # clear is the one cycle less.
    "web clear all": {"cycles": 4, "stats": _CLEAR_STATS},
    "reset all inputs": {"stats": _CLEAR_STATS},
    "follow a set without keeping mains": {"stats": _CLEAR_STATS},
}

# A set load's pre-pass with game rows recorded: the set load's block used
# to cover the rows too (the score spinboxes cleared them on the way); now
# they follow the cleared score from the held cycle, one cycle each -- the
# same as ResetMatch above. The state each step leaves is unchanged.
S2D_PREPASS_CHANGES = {
    "follow a set without keeping mains": {"cycles": 4,
                                           "stats": _CLEAR_STATS},
    "follow another set keeping mains": {"cycles": 4, "stats": _CLEAR_STATS},
}

# ADR-005 S2e, on top of the S2d differences: the rows a held cycle moves to
# follow the score are one save cycle for both teams (they were one a row).
# So a score that moves by more than one game with the tracker on -- cleared
# (ClearScore / ResetMatch / ResetScore), loaded (ApplyScores) or adjusted by
# two (AdjustScore) -- is 1 + 1 cycles. A +1 moves one row and is unchanged,
# and so is everything with the tracker off. The state, key order and
# publication of every step are unchanged.
S2E_CLEAR_CHANGES = {
    "clear the score": {"cycles": 2},
    "score and losers again": {"cycles": 5},     # team 2's +2
    "reset match": {"cycles": 2},
    "a set with a match bar, a score and losers": {"cycles": 2},
    "web reset match": {"cycles": 3},
    "refill everything": {"cycles": 11},         # team 2's +2
    "web clear all": {"cycles": 7},
    "two players, two characters, best of 5 and refill everything": {
        "cycles": 19},
}

S2E_SINGLES_CLEAR_CHANGES = {
    "clear the score": {"cycles": 2},
    "score and losers again": {"cycles": 5},
    "reset match": {"cycles": 2},
    "a set with a match bar, a score and losers": {"cycles": 2},
    "web reset match": {"cycles": 3},
    "two characters, best of 5 and refill everything": {"cycles": 11},
}

S2E_PREPASS_CHANGES = {
    "follow a set without keeping mains": {"cycles": 2},
    "follow another set keeping mains": {"cycles": 2},
}

# ADR-005 S3e-6: the players per team and the characters per player are the
# state's (AutonaActions.SetRoster); the board's spinboxes ask for a change
# and its cards follow the slots. What changed on purpose:
#
# - `cycles`: a change of the characters per player is one save cycle (each
#   card published its own rows: one a card), and so is a change of both
#   counts at once -- clear_all's counts were the players' cycle and a cycle
#   a card. The players' count alone was, and is, one.
# - the best-of recordings' game load (below, S3E6_BEST_OF_CHANGES).
#
# The state, its key order and what 反映 publishes are the recording's at
# every other step.
S3E6_CHANGES = {
    "two players, two characters": {"cycles": 2},
}

S3E6_SWAP_CHANGES = {
    "two players, two characters": {"cycles": 2},
    "three characters": {"cycles": 1},
}

S3E6_CLEAR_CHANGES = {
    "two players, two characters": {"cycles": 2},
    "one character": {"cycles": 1},
    "two characters and refill": {"cycles": 5},
    "web clear all": {"cycles": 5},
    "two players, two characters, best of 5 and refill everything": {
        "cycles": 16},
}

S3E6_SINGLES_CLEAR_CHANGES = {
    "two characters": {"cycles": 1},
    "one character": {"cycles": 1},
    "two characters and refill": {"cycles": 3},
    "web clear all": {"cycles": 3},
    "two characters, best of 5 and refill everything": {"cycles": 10},
}

S3E6_PREPASS_CHANGES = {
    "two players, two characters": {"cycles": 2},
}


# ADR-005 S4t: the game rows follow the score in the operation that moved it
# (GameTrackerService.FollowScores), in its block. They were one cycle more,
# from the board's held handler. So with the tracker on a score that moves a
# row is one cycle less; the state, its key order and what 反映 publishes are
# the recording's at every step, which is what says the follow is the same.
S4T_SWAP_CHANGES = {
    "a point with the tracker on": {"cycles": 1},
}

S4T_CLEAR_CHANGES = {
    "clear the score": {"cycles": 1},
    "score and losers again": {"cycles": 3},     # team 1's +1, team 2's +2
    "reset match": {"cycles": 1},
    "a set with a match bar, a score and losers": {"cycles": 1},
    "web reset match": {"cycles": 2},
    "refill everything": {"cycles": 9},
    "web clear all": {"cycles": 4},
    "two players, two characters, best of 5 and refill everything": {
        "cycles": 14},
}

S4T_SINGLES_CLEAR_CHANGES = {
    "clear the score": {"cycles": 1},
    "score and losers again": {"cycles": 3},
    "reset match": {"cycles": 1},
    "a set with a match bar, a score and losers": {"cycles": 1},
    "web reset match": {"cycles": 2},
    "two characters, best of 5 and refill everything": {"cycles": 8},
}

S4T_PREPASS_CHANGES = {
    "follow a set without keeping mains": {"cycles": 1},
    "follow another set keeping mains": {"cycles": 1},
}

S4T_BEST_OF_CHANGES = {
    "tracker on, reset the score": {"cycles": 1},
    "a set with a score, tracker on": {"cycles": 1},
    "a set with a best of, a round and a score": {"cycles": 1},
    "web reset match, tracker on": {"cycles": 2},
    "web clear all, tracker on": {"cycles": 4},
}


def _layered(*layers):
    """Per-step change dicts of successive migrations as one: a later
    migration's fields replace an earlier one's for the same step."""
    merged = {}
    for layer in layers:
        for step, change in layer.items():
            merged[step] = {**merged.get(step, {}), **change}
    return merged


def _travelled(state, reference):
    """`state` with every player's mains and character variants taken from
    where that player (by tag) was in `reference`."""
    were = {player.get(schema.PLAYER_TAG_KEY): player
            for team in reference.get("team", {}).values()
            for player in team.get("player", {}).values()}
    for team in state.get("team", {}).values():
        for player in team.get("player", {}).values():
            before = were.get(player.get(schema.PLAYER_TAG_KEY))
            if before is None:
                continue
            if "mains" in before:
                player["mains"] = deep_clone(before["mains"])
            else:
                player.pop("mains", None)
            for index, character in player.get("character", {}).items():
                was = before.get("character", {}).get(index)
                if was is not None \
                        and was.get("en_name") == character.get("en_name"):
                    character["variant"] = deep_clone(was.get("variant", {}))


def _withStages(state, results):
    """`state` with the game tracker's rows holding `results` ("T1" / "T2"
    / "-" per game, in order)."""
    for number, result in enumerate(results, start=1):
        row = state["stages"][str(number)]
        row["t1_win"] = result == "T1"
        row["t2_win"] = result == "T2"
        row["tie"] = False


def _withChanges(entry, changes, recording):
    """A recorded step with what a migration changed on purpose applied:
    `dropped` paths out of its state, `values` (path -> value, S3e-3) and
    `travel` / `stages` (S2c) into it, and every other listed field
    replaced."""
    change = changes.get(entry["step"])
    if change is None:
        return entry
    expected = json.loads(json.dumps(entry))
    for path in change.get("dropped", ()):
        *parents, leaf = path.split(".")
        node = expected["state"]
        for key in parents:
            node = node[key]
        del node[leaf]
    for path, value in change.get("values", {}).items():
        *parents, leaf = path.split(".")
        node = expected["state"]
        for key in parents:
            node = node[key]
        assert leaf in node, path    # a value changed, not a key added
        node[leaf] = value
    if "travel" in change:
        reference = next(e["state"] for e in recording
                         if e["step"] == change["travel"])
        _travelled(expected["state"], reference)
    if "stages" in change:
        _withStages(expected["state"], change["stages"])
    expected.update({field: value for field, value in change.items()
                     if field not in ("dropped", "values", "travel",
                                      "stages")})
    return expected


def test_board_operations_publish_the_recorded_state(board, autonaApp):
    """The state after every operation, its save cycles and what 反映 then
    published are what the pre-migration tree produced, apart from what the
    migrations changed on purpose."""
    keyOrders = []
    recorded = _runSteps(autonaApp, board, keyOrders=keyOrders)

    if RECORD_KEY_ORDER:
        _recordKeyOrder("board operations", keyOrders)
        return
    if RECORD:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(
            {"operations": recorded}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return
    recording = GOLDEN["operations"]
    golden = [_withChanges(_withChanges(_withChanges(_withChanges(
        _withChanges(entry, S2B_CHANGES, recording), S2C_CHANGES, recording),
        S2D_CHANGES, recording), S3E3_CHANGES, recording), S3E6_CHANGES,
        recording)
              for entry in recording]
    assert [e["step"] for e in recorded] == [e["step"] for e in golden]
    for actual, expected in zip(recorded, golden):
        assert actual["state"] == expected["state"], actual["step"]
    # Before the digests: where S2c changed a step's values, its digest is
    # this tree's own, and only the recorded key order can say the order
    # is still the old one.
    _assertKeyOrder("board operations", keyOrders)
    for actual, expected in zip(recorded, golden):
        assert actual == expected, actual["step"]


def _recordKeyOrder(section, keyOrders):
    # Read now, not at import: the other section may have been written by
    # the test that ran just before.
    recorded = json.loads(KEY_ORDER_PATH.read_text(encoding="utf-8")) \
        if KEY_ORDER_PATH.exists() else {}
    recorded[section] = keyOrders
    KEY_ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_ORDER_PATH.write_text(json.dumps(
        recorded, ensure_ascii=False, indent=1), encoding="utf-8")


def _assertKeyOrder(section, keyOrders):
    """Every dict of the board subtree has the keys, in the order, the
    pre-S2c tree left after the same step -- the swap / move steps whose
    values S2c changed on purpose included."""
    assert KEY_ORDER is not None and section in KEY_ORDER, (
        f"{KEY_ORDER_PATH} has no {section!r}: record it on the pre-S2c "
        "tree (see KEY_ORDER_PATH above)")
    recording = KEY_ORDER[section]
    assert [e["step"] for e in keyOrders] == [e["step"] for e in recording]
    differences = {}
    for actual, expected in zip(keyOrders, recording):
        paths = set(actual["keyOrder"]) | set(expected["keyOrder"])
        changed = {path: (actual["keyOrder"].get(path),
                          expected["keyOrder"].get(path))
                   for path in sorted(paths)
                   if actual["keyOrder"].get(path)
                   != expected["keyOrder"].get(path)}
        if changed:
            differences[actual["step"]] = changed
    assert differences == {}


@pytest.fixture
def teamLogo(autonaApp):
    path = autonaApp.dir / TEAM_LOGO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_swap_operations_publish_the_recorded_state(
        board, autonaApp, teamLogo):
    """The swap and the player moves, against the pre-S2c recording: the
    same state, cycles and publication apart from what S2c changed on
    purpose (S2C_SWAP_CHANGES), and the statistics fetched once per
    operation."""
    keyOrders = []
    recorded = _runSteps(autonaApp, board, steps=_swapSteps(board),
                         stats=True, keyOrders=keyOrders)

    if RECORD_KEY_ORDER:
        _recordKeyOrder("swap operations", keyOrders)
        return
    if RECORD:
        SWAP_GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        SWAP_GOLDEN_PATH.write_text(json.dumps(
            {"operations": recorded}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return
    assert SWAP_GOLDEN is not None, (
        f"{SWAP_GOLDEN_PATH} is missing: it is recorded on the pre-S2c tree "
        "(6a6f04cc) with AUTONA_RECORD_BOARD_OPERATIONS=1 -k swap_operations")
    recording = SWAP_GOLDEN["operations"]
    golden = [_withChanges(_withChanges(_withChanges(
        entry, S2C_SWAP_CHANGES, recording), S3E6_SWAP_CHANGES, recording),
        S4T_SWAP_CHANGES, recording)
              for entry in recording]
    assert [e["step"] for e in recorded] == [e["step"] for e in golden]
    for actual, expected in zip(recorded, golden):
        assert actual["state"] == expected["state"], actual["step"]
    # Before the digests: where S2c changed a step's values, its digest is
    # this tree's own, and only the recorded key order can say the order
    # is still the old one.
    _assertKeyOrder("swap operations", keyOrders)
    for actual, expected in zip(recorded, golden):
        assert actual == expected, actual["step"]


def _recordClearGolden(section, recorded, path=CLEAR_GOLDEN_PATH):
    # Read now, not at import: the clear recordings share the file, one
    # section per test (and so do the best-of ones, in theirs).
    golden = json.loads(path.read_text(encoding="utf-8")) \
        if path.exists() else {}
    golden[section] = recorded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        golden, ensure_ascii=False, indent=1), encoding="utf-8")


def _replayClears(app, board, steps, section, keyOrderSection, changes,
                  path=CLEAR_GOLDEN_PATH, tree="pre-S2d tree (2045c973)",
                  selector="clear_operations"):
    """`steps` against the pre-S2d recording `section` of clear_golden.json:
    the same state (key order included), cycles, publication and
    statistics fetches, apart from `changes`.

    The best-of recordings (ADR-005 S2e) are replayed the same way from
    their own file (`path`, recorded on `tree`)."""
    keyOrders = []
    recorded = _runSteps(app, board, steps=steps, stats=True,
                         keyOrders=keyOrders)

    if RECORD_KEY_ORDER:
        _recordKeyOrder(keyOrderSection, keyOrders)
        return
    if RECORD:
        _recordClearGolden(section, recorded, path)
        return
    golden = json.loads(path.read_text(encoding="utf-8")) \
        if path.exists() else None
    assert golden is not None and section in golden, (
        f"{path} has no {section!r}: it is recorded on the {tree} with "
        f"AUTONA_RECORD_BOARD_OPERATIONS=1 -k {selector}")
    recording = golden[section]
    golden = [_withChanges(entry, changes, recording)
              for entry in recording]
    assert [e["step"] for e in recorded] == [e["step"] for e in golden]
    for actual, expected in zip(recorded, golden):
        assert actual["state"] == expected["state"], actual["step"]
    _assertKeyOrder(keyOrderSection, keyOrders)
    for actual, expected in zip(recorded, golden):
        assert actual == expected, actual["step"]


def test_clear_operations_publish_the_recorded_state(
        board, autonaApp, teamLogo):
    """Every clear from every entry point on the team board."""
    _replayClears(autonaApp, board, _clearSteps(board, autonaApp),
                  "operations", "clear operations",
                  _layered(S2D_CLEAR_CHANGES, S2E_CLEAR_CHANGES,
                           S3E6_CLEAR_CHANGES, S4T_CLEAR_CHANGES))


@pytest.fixture
def singlesBoard(slotEnv, autonaApp):
    slotEnv("tgame")
    return autonaApp.ready(1)


def test_singles_clear_operations_publish_the_recorded_state(
        singlesBoard, autonaApp):
    """The same clears on the singles board."""
    _replayClears(autonaApp, singlesBoard,
                  _singlesClearSteps(singlesBoard, autonaApp),
                  "singles", "singles clear operations",
                  _layered(S2D_SINGLES_CHANGES, S2E_SINGLES_CLEAR_CHANGES,
                           S3E6_SINGLES_CLEAR_CHANGES,
                           S4T_SINGLES_CLEAR_CHANGES))


def test_set_load_prepass_clear_operations_publish_the_recorded_state(
        board, autonaApp, teamLogo):
    """A set load's pre-pass with game rows recorded, both ways."""
    _replayClears(autonaApp, board, _prepassSteps(board),
                  "prepass", "set load pre-pass",
                  _layered(S2D_PREPASS_CHANGES, S2E_PREPASS_CHANGES,
                           S3E6_PREPASS_CHANGES, S4T_PREPASS_CHANGES))


# ── the best of (ADR-005 S2e) ────────────────────────────────────────────
#
# The set length was the BEST OF spinbox's: its valueChanged published the
# six keys and rebuilt the game tracker's rows, and every other entry point
# (a set load, the Web's set_route / reset_match / clear_all, /api/v1's
# PATCH set, a game load, the reset-all button) drove that spinbox. S2e makes
# it a core operation (AutonaActions.SetBestOf) that resizes the rows in the
# same cycle (the spinbox and the rows rendered the state until ADR-005
# S4ac-3 removed them). Recorded on the
# last pre-S2e tree (79da38bf) with `-k best_of_operations`, one section per
# test in best_of_golden.json, the key order in key_order.json.

BEST_OF_GOLDEN_PATH = GOLDEN_PATH.parent / "best_of_golden.json"
BEST_OF_TREE = "pre-S2e tree (79da38bf)"


def _v1Set(client, number, body):
    """/api/v1's PATCH set, through the real route table."""
    response = client.patch(f"/api/v1/scoreboards/{number}/set", json=body)
    assert response.status_code == 200, response.get_json()


def _bestOfSteps(board, app, client):
    """Every way the set length is set, on a board with the game tracker on
    and off: the spinbox, a set load, the Web's set_route (a number and a
    query string), /api/v1's PATCH set, a game load, the Web's resets and
    the reset-all button -- with game results recorded in between, so that
    a step that rebuilt the rows (or did not) shows."""
    actions = app.web()
    number = board

    def spin(value):
        return lambda: _setBestOf(board, value)

    def tracked(operation):
        return lambda: _withTracker(operation)

    def web(**query):
        return lambda: actions.set_route(number, **query)

    def v1(body):
        return lambda: _v1Set(client, number, body)

    if number == 2:
        counts = ("two players, two characters", lambda: (
            _setPlayers(board, 2), _setCharacters(board, 2)))
    else:
        counts = ("two characters", lambda: _setCharacters(board, 2))
    return [
        counts,
        ("fill both teams", lambda: _fillBoth(board)),
        ("best of 3", spin(3)),
        ("best of 5", spin(5)),
        ("the same best of", spin(5)),
        ("best of 0", spin(0)),
        ("best of over the maximum", spin(150)),
        ("tracker on, best of 5", tracked(spin(5))),
        ("stage results", lambda: _stageResults(board)),
        ("tracker on, best of 7 over the results", tracked(spin(7))),
        ("stage results on the new rows", lambda: _stageResults(board)),
        ("tracker on, best of 3", tracked(spin(3))),
        ("stage results on every game", lambda: _stageResults(board)),
        ("a point on a full set", lambda: _score(board, 1, 1)),
        ("tracker on, reset the score",
         tracked(lambda: AutonaActions.instance.ResetScore(board))),
        ("tracker on, best of 5 again", tracked(spin(5))),
        ("a set with a score, tracker on", tracked(lambda: apply_set(board,
            {"team1score": 2, "team2score": 1}))),
        ("web best of 7, tracker on", tracked(web(bestOf=7))),
        ("stage results after the web", lambda: _stageResults(board)),
        ("web same best of, tracker on", tracked(web(bestOf="7"))),
        ("web best of 0 is not carried", tracked(web(bestOf=0))),
        ("a match bar", lambda: apply_set(board, dict(_SET_MATCH_BAR))),
        ("web best of 3 blanks the English round",
         tracked(web(bestOf="3"))),
        ("web best of over the maximum, tracker off", web(bestOf=150)),
        ("v1 best of 5, tracker on", tracked(v1({"bestOf": 5}))),
        ("stage results after v1", lambda: _stageResults(board)),
        ("v1 best of 7 with a phase and a match", tracked(v1(
            {"bestOf": 7, "phase": "Top 8", "match": "Grand Final"}))),
        ("v1 best of 0 is not carried", tracked(v1({"bestOf": 0}))),
        ("stage results before a set", lambda: _stageResults(board)),
        ("a set with a best of, a round and a score",
         tracked(lambda: apply_set(board, {
             "bestOf": 5, "round_name": "Losers Final",
             "team1score": 1, "team2score": 2}))),
        ("stage results after the set", lambda: _stageResults(board)),
        ("a set with the same best of", tracked(lambda: apply_set(board,
            {"bestOf": 5, "round_name": "Losers Final"}))),
        ("a set with best of 0", tracked(lambda: apply_set(board,
            {"bestOf": 0, "round_name": "Losers Final"}))),
        ("game load, tracker on", tracked(_gameLoaded)),
        ("stage results after the game load",
         lambda: _stageResults(board)),
        ("web reset match, tracker on",
         tracked(lambda: actions.reset_match(number))),
        ("tracker on, best of 5 and stage results",
         tracked(lambda: (_setBestOf(board, 5), _stageResults(board)))),
        ("web clear all, tracker on",
         tracked(lambda: actions.clear_all(number))),
        ("refill, best of 5 and stage results",
         tracked(lambda: (_fillBoth(board), _setBestOf(board, 5),
                          _stageResults(board)))),
        ("reset all inputs, tracker on",
         tracked(lambda: _resetAll(board))),
        ("reset all inputs again", lambda: _resetAll(board)),
        ("game load with no best of, tracker on",
         tracked(_gameLoaded)),
        ("tracker on, best of 3 after the resets", tracked(spin(3))),
    ]


# What ADR-005 S2e changed on purpose, step by step (see _withChanges). The
# best of itself changed nothing: every step's state, key order, cycles and
# publication are the recording's. What did change is the score's follow on
# the rows (see
# S2E_CLEAR_CHANGES): one cycle where a score moved by several games.
S2E_CHANGES = {
    "tracker on, reset the score": {"cycles": 2},
    "a set with a score, tracker on": {"cycles": 2},
    "web reset match, tracker on": {"cycles": 3},
    "web clear all, tracker on": {"cycles": 6},
}
S2E_SINGLES_CHANGES = dict(S2E_CHANGES)

# ADR-005 S3e-6. The counts in one cycle (S3E6_CHANGES), and the game load:
# its roster is the shared wiring's now (bootstrap.wire_game_roster ->
# AutonaActions.ApplyGameRoster, _gameLoaded), written ahead of the board's
# onLoad handler instead of from it, in one cycle with its slot-count
# change. What it writes is the recording's: the same game again (the
# load the recording ran), so the resized slots keep their first picks.
# A load that replaces the game empties every pick and main instead
# (tests/test_roster_flow.py: the game switch, and the recorded game load).
S3E6_BEST_OF_CHANGES = {
    "two players, two characters": {"cycles": 2},
    "game load, tracker on": {"cycles": 2},
    "web clear all, tracker on": {"cycles": 5},
    "game load with no best of, tracker on": {"cycles": 2},
}
S3E6_SINGLES_BEST_OF_CHANGES = {
    "two characters": {"cycles": 1},
    "game load, tracker on": {"cycles": 2},
    "web clear all, tracker on": {"cycles": 5},
    "game load with no best of, tracker on": {"cycles": 2},
}


def test_best_of_operations_publish_the_recorded_state(
        board, autonaApp, client):
    """Every way to set the best of on the team board."""
    _replayClears(autonaApp, board, _bestOfSteps(board, autonaApp, client),
                  "operations", "best of operations",
                  _layered(S2E_CHANGES, S3E6_BEST_OF_CHANGES,
                           S4T_BEST_OF_CHANGES),
                  path=BEST_OF_GOLDEN_PATH, tree=BEST_OF_TREE,
                  selector="best_of_operations")


def test_singles_best_of_operations_publish_the_recorded_state(
        singlesBoard, autonaApp, client):
    """The same on the singles board."""
    _replayClears(autonaApp, singlesBoard,
                  _bestOfSteps(singlesBoard, autonaApp, client),
                  "singles", "singles best of operations",
                  _layered(S2E_SINGLES_CHANGES, S3E6_SINGLES_BEST_OF_CHANGES,
                           S4T_BEST_OF_CHANGES),
                  path=BEST_OF_GOLDEN_PATH,
                  tree=BEST_OF_TREE, selector="best_of_operations")


def test_best_of_operations_on_a_board_just_made_publish_the_recorded_state(
        slotEnv, autonaApp, client):
    """The Web's set_route and /api/v1's PATCH set on a board made a moment
    before, as the launch makes it (AutonaActions.EnsureBoard with
    `panel`): the state, the cycles and what is published while it is made
    and then on 反映 are the recorded ones.

    Recorded when the route made the board itself: the desktop host's
    Prepare built a board that was not built, which is what let the write
    wait for 反映. Since ADR-005 S4ac-3 the launch makes every board and
    nothing makes one again, so each step makes its board the way the
    launch does and then asks the route (the recording's section is still
    "unbuilt")."""
    slotEnv("tgame")
    actions = autonaApp.web()

    def made(number, route):
        def step():
            assert AutonaActions.instance.EnsureBoard(number, panel=True)
            route()
        return lambda: _withTracker(step)

    steps = [
        ("web best of on an unbuilt team board", 2,
         made(2, lambda: actions.set_route(2, bestOf=5))),
        ("v1 best of on an unbuilt singles board", 1,
         made(1, lambda: _v1Set(client, 1, {"bestOf": 3}))),
    ]
    for number in (1, 2):
        autonaApp._dropBoard(number)
        # What a board not made yet held when this was recorded (the seed
        # the scoreboard manager wrote for a board it had not built).
        schema.SeedBoard(number)
    autonaApp.settle()

    recorded = []
    keyOrders = []
    for name, number, step in steps:
        prefix = f"score.{number}"
        assert not AutonaActions.instance.BoardEnsured(number)
        with _Cycles(prefix) as cycles, _Published() as during:
            step()
            autonaApp.settle()
        entry = {"step": name, "cycles": cycles.count,
                 "during": [len(during.deltas), during.bigChanges,
                            digest(_normalise(during.encoded(sortKeys=True),
                                              autonaApp))]}
        entry.update(_boardState(autonaApp, prefix))
        keyOrders.append({"step": name, "keyOrder": _boardKeyOrder(prefix)})
        assert AutonaActions.instance.BoardEnsured(number)
        with _Published() as published:
            StateManager.CommitHeld(prefix)
            autonaApp.settle()
        entry["published"] = [len(published.deltas), published.bigChanges,
                              digest(_normalise(published.encoded(),
                                                autonaApp))]
        entry["publishedRows"] = digest(
            _normalise(published.encoded(sortKeys=True), autonaApp))
        recorded.append(entry)

    if RECORD_KEY_ORDER:
        _recordKeyOrder("unbuilt best of operations", keyOrders)
        return
    if RECORD:
        _recordClearGolden("unbuilt", recorded, BEST_OF_GOLDEN_PATH)
        return
    golden = json.loads(BEST_OF_GOLDEN_PATH.read_text(encoding="utf-8")) \
        if BEST_OF_GOLDEN_PATH.exists() else None
    assert golden is not None and "unbuilt" in golden, (
        f"{BEST_OF_GOLDEN_PATH} has no 'unbuilt': it is recorded on the "
        f"{BEST_OF_TREE} with AUTONA_RECORD_BOARD_OPERATIONS=1 "
        "-k best_of_operations")
    _assertKeyOrder("unbuilt best of operations", keyOrders)
    assert recorded == golden["unbuilt"]


# The steps that are one operation in one save cycle. The others were a
# widget writing field by field when this was written (a SetData per card,
# a Clear per card) and replay those cycles. The losers flag became one in
# S2b -- it was the flag plus one cycle per player card. The swap and the
# moves were already one cycle as a copy between cards (inside the board's
# SaveBlock) and are one as the core operation (AutonaActions.SwapTeams /
# SwapPlayers).
ONE_CYCLE_STEPS = ("losers", "swap teams", "move a player down",
                   "swap teams back", "reset match", "clear players",
                   "load a set",
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


# A card's own clear button was PlayerService.ClearSlot (ADR-005 S3e-3)
# until the cards went in S4ac-3, and the Web's player clear still is
# (AutonaWebOperations). Kept as the pin that clearing the slots one by one
# publishes what the board's clear (AutonaActions.ClearPlayers) does.

@pytest.mark.parametrize("characters", [1, 2])
@pytest.mark.parametrize("no_mains", [False, True])
def test_each_slot_cleared_publishes_what_the_board_clear_does(
        board, autonaApp, teamLogo, no_mains, characters):
    if RECORD or RECORD_KEY_ORDER:
        pytest.skip("not part of the recording")
    _setPlayers(board, 2)
    _setCharacters(board, characters)
    _fillBoth(board)
    autonaApp.settle()
    with StateManager.lock:
        before = deep_clone(StateManager.Get(PREFIX))

    for team in (1, 2):
        for slot in (1, 2):
            PlayerService(f"{PREFIX}.team.{team}.player.{slot}").ClearSlot(
                no_mains)
    autonaApp.settle()
    with StateManager.lock:
        viaSlots = deep_clone(StateManager.Get(PREFIX))

    # The same filled board again.
    StateManager.Set(PREFIX, deep_clone(before))
    autonaApp.settle()
    AutonaActions.instance.ClearPlayers(BOARD, no_mains)
    autonaApp.settle()
    with StateManager.lock:
        viaBoard = deep_clone(StateManager.Get(PREFIX))

    assert viaBoard != before
    assert viaBoard == viaSlots
    assert _keyOrders(viaBoard) == _keyOrders(viaSlots)


# The upset factor is the seeds' distance, and only for singles
# (StatsService.IsSingles). A load recalculates it when a seed moves
# (SlotChange) -- and, since the S3e-3 review, when a set changes the board
# from one player a side to several or back: the old load cleared and
# re-set every seed on the way, the core load does not when they are the
# same, and the previous pairing's number stayed.

def _seededSet(players):
    return {"overwrite": True, "entrants": [
        [{"gamerTag": f"A{p}", "seed": 3 if p == 1 else 0}
         for p in range(1, players + 1)],
        [{"gamerTag": f"B{p}", "seed": 12 if p == 1 else 0}
         for p in range(1, players + 1)]]}


@pytest.mark.parametrize("before, after", [(1, 2), (2, 1)])
def test_a_set_between_singles_and_teams_recalculates_the_upset_factor(
        board, autonaApp, before, after):
    if RECORD or RECORD_KEY_ORDER:
        pytest.skip("not part of the recording")
    from src.core.StatsService import StatsService

    singles = abs(StatsService.CalculatePlacementMath("DOUBLE_ELIMINATION", 3)
                  - StatsService.CalculatePlacementMath(
                      "DOUBLE_ELIMINATION", 12))
    assert singles > 0
    upset = f"{PREFIX}.upset_factor"

    apply_set(board, _seededSet(before))
    autonaApp.settle()
    assert autonaApp.get(upset) == (singles if before == 1 else 0)

    with _StatsTriggers(board) as triggers:
        apply_set(board, _seededSet(after))
        autonaApp.settle()
    assert triggers.counts["UpsetFactorCalculation"] == 1
    assert autonaApp.get(upset) == (singles if after == 1 else 0)

    # The same set again: nothing to recalculate.
    with _StatsTriggers(board) as triggers:
        apply_set(board, _seededSet(after))
        autonaApp.settle()
    assert triggers.counts["UpsetFactorCalculation"] == 0
