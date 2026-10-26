"""What the player-apply recording (golden.json next to this file) is made of.

ADR-005 S3e-3 (S2f) moved loading a player into a slot -- the card's
SetData, its Clear, its SavePlayerToDB and the board's three ways in
(LoadPlayerFromTag, ApplyPlayerData, a set's entrants) -- into
core/PlayerService. The recording was made through those entry points on the
last tree that still had the widget implementation (ae922c24); two tests
replay it: tests/test_player_apply.py through the same entry points (the
card and the board), tests/test_player_service.py through PlayerService
alone. Both build the world from here. Qt-free on purpose: the service test
must not import Qt.

Every scenario starts from a freshly built board (or a fresh PLAYERS editor)
with three character slots per card, the fixture game "tgame" loaded
(tests/fixtures/player_slots/recording.py), the players named in `db` in the
local player database and a bad-word filter over `words` (none: nothing is
censored). Avatars are relative paths, never URLs: the state export would
download an http one.

Steps (`op`):

- ``set``    -- the card's SetData(data, dontLoadFromDB=not loadFromDB,
                clear, no_mains)
- ``clear``  -- the card's Clear(no_mains)
- ``save``   -- the card's SavePlayerToDB()
- ``typed``  -- character slots written as the card publishes them
                (PlayerService.PublishCharacterSlots), which the board's
                cards then render: `slots` are CharacterSlot fields; a slot
                with only `text` is a typed name. Board slots only (the
                editor is never rendered from the state)
- ``pick``   -- the operator picking one slot's character, skin and variant
                on the card's combos (`index` 1-based; rows by en_name)
- ``chars``  -- the board's character count (charNumber)
- ``tag``    -- the board's LoadPlayerFromTag(tag, team, player, no_mains)
- ``update`` -- the board's ApplyPlayerData(team, player, data) (/api/v1)
- ``setdata``-- the board's ChangeSetData(payload) (a set's entrants, the
                legacy update-team route)
- ``swap``   -- the board's SwapTeams()

`slot` is [team, player] on the board, or "editor" for the PLAYERS editor
(player_editor.1). Scenarios with `core: False` use a step PlayerService
alone does not have (the board's set payload, the swap).

The second half is how a step is observed (every write under the board or
the editor, its save cycles) and how a replay is compared to the recording,
for both replays: what S3e-3 changed on purpose is ``CHANGES``.
"""
import copy
from unittest.mock import patch

from src.core.StateManager import StateManager
from tests.fixtures.player_slots.recording import digest, encode

# The local player database each scenario starts from (a subset of these,
# by DB tag).
DB_PLAYERS = {
    "SP TAG": {
        "prefix": "SP", "gamerTag": "TAG", "name": "Real Tag",
        "twitter": "tagtw", "country_code": "JP", "state_code": "13",
        "mains": {"tgame": [["Luigi", 1]], "game2": [["Other", 0]]},
        "custom_textbox": "line one\\nline two",
    },
    "LEG": {
        "gamerTag": "LEG", "country_code": "US", "state_code": "CA",
        "mains": {"tgame": [["Peach"]]},
    },
}

# The bad-word scenario's word list (assets/ngword/common.txt).
BAD_WORDS = ["nasty", "vile"]

ENTRANTS = [
    [{"gamerTag": "TAG", "prefix": "SP", "id": 101, "seed": 3,
      "city": "Osaka", "avatar": "avatars/tag.png",
      "country_code": "JP", "state_code": "27",
      "mains": {"tgame": [["Mario", 1], ["Peach", 2, "EX"]]}},
     {"gamerTag": "WING", "id": 102, "seed": "8",
      "mains": {"tgame": [["Bowser"]]}}],
    [{"gamerTag": "RIVAL", "prefix": "RV", "id": 201, "seed": 1,
      "country_code": "GB", "state_code": "CON",
      "mains": {"tgame": [["Zelda", 0]]}},
     {"gamerTag": "LEG", "id": 202, "seed": None}],
]

SELECTIONS = [
    [{"gamerTag": "TAG", "mains": {"tgame": [["Luigi", 2]]}},
     {"gamerTag": "WING", "mains": {"tgame": [["Mario", 2, "EX"]]}}],
    [{"gamerTag": "RIVAL", "mains": {"tgame": [["Peach", 1]]}},
     {"gamerTag": "LEG", "mains": None}],
]


def _set(slot, data, loadFromDB=False, clear=True, no_mains=False):
    return {"op": "set", "slot": slot, "data": data, "loadFromDB": loadFromDB,
            "clear": clear, "no_mains": no_mains}


SCENARIOS = [
    {
        "name": "load with the database",
        "board": 1, "db": ["SP TAG", "LEG"],
        "steps": [
            _set([1, 1], {"gamerTag": "TAG", "prefix": "SP", "id": 101,
                          "seed": 7, "city": "Tokyo",
                          "avatar": "avatars/a.png",
                          "mains": {"tgame": [["Mario", 2, "EX"]]}},
                 loadFromDB=True),
            _set([2, 1], {"gamerTag": "LEG", "seed": 2, "id": 202},
                 loadFromDB=True),
            # A prefix without a tag: the DB lookup's tag cannot be built
            # (it raised; since the S3e-3 review the lookup is skipped).
            _set([2, 1], {"prefix": "ONLY"}, loadFromDB=True),
        ],
    },
    {
        "name": "a player over another",
        "board": 1, "db": [],
        "steps": [
            _set([1, 1], {"gamerTag": "TAG", "prefix": "SP", "name": "Real",
                          "twitter": "tw", "custom_textbox": "a\\nb",
                          "id": 101, "seed": 7, "city": "Kyoto",
                          "country_code": "JP", "state_code": "27",
                          "mains": {"tgame": [["Mario", 1], ["Luigi", 2]]}}),
            _set([1, 1], {"gamerTag": "NEXT", "id": 303, "seed": 7,
                          "country_code": "JP"}),
            _set([1, 1], {"gamerTag": "THIRD"}),
            _set([1, 1], {"seed": None, "twitter": "tw2"}, clear=False),
            _set([1, 1], {"seed": "12", "id": 303, "city": "Kyoto"},
                 clear=False),
            _set([1, 1], {"seed": "x"}, clear=False),
        ],
    },
    {
        "name": "keeping the mains",
        "board": 1, "db": [],
        "steps": [
            _set([1, 1], {"gamerTag": "A", "mains": {"tgame": [
                ["Mario", 1], ["Luigi", 2, "EX"], ["Bowser"]]}}),
            # Three slots: the old clear reset every slot anyway.
            _set([1, 1], {"gamerTag": "B", "mains": {"tgame": [["Peach"]]}},
                 no_mains=True),
            _set([1, 1], {"mains": {"tgame": [["Zelda", 0]]}}, clear=False),
            {"op": "chars", "count": 1},
            # One slot: kept.
            _set([1, 1], {"gamerTag": "C", "mains": {"tgame": [["Peach"]]}},
                 no_mains=True),
            {"op": "clear", "slot": [1, 1], "no_mains": True},
            {"op": "chars", "count": 2},
            _set([1, 1], {"gamerTag": "D", "mains": {"tgame": [
                ["Mario"], ["Luigi"]]}}),
            {"op": "clear", "slot": [1, 1], "no_mains": True},
            {"op": "clear", "slot": [1, 1], "no_mains": False},
        ],
    },
    {
        "name": "locations",
        "board": 1, "db": [],
        "steps": [
            _set([1, 1], {"gamerTag": "L1", "country_code": "JP",
                          "state_code": "Tokyo"}),
            _set([1, 1], {"gamerTag": "L2", "country_code": "ZZ",
                          "state_code": "13"}),
            _set([1, 1], {"gamerTag": "L3", "country_code": "GB",
                          "state_code": "CON"}),
            _set([1, 1], {"state_code": "ENG"}, clear=False),
            _set([1, 1], {"state_code": "Nowhere"}, clear=False),
            _set([1, 1], {"gamerTag": "L4", "state_code": "13"}),
            _set([1, 1], {"gamerTag": "L5", "country_code": "Agender"}),
            _set([1, 1], {"country_code": "JP", "state_code": "13"},
                 clear=False),
            _set([1, 1], {"country_code": "JP"}, clear=False),
            _set([1, 1], {"country_code": "ZZ"}, clear=False),
        ],
    },
    {
        "name": "characters",
        "board": 1, "db": [],
        "steps": [
            _set([1, 1], {"gamerTag": "K", "mains": ["Peach", "x"]}),
            _set([1, 1], {"mains": {"tgame": [
                ["Mario", 99], ["Nobody", 1, "EX"], ["Luigi", 0, "NOPE"]]}},
                clear=False),
            {"op": "typed", "slot": [1, 1], "slots": [
                {"text": "Typed Hero"},
                {"character": "Mario", "skin": 2, "variant": "EX",
                 "text": "Mario"},
                {}]},
            _set([1, 1], {"mains": {"game2": [["Other"]]}}, clear=False),
            _set([1, 1], {"mains": ["Luigi", 1]}, clear=False),
            _set([1, 1], {"mains": ["Nobody"]}, clear=False),
            _set([1, 1], {"mains": {"tgame": [["Mario", 2]]}}, clear=False),
        ],
    },
    {
        "name": "save to the database",
        "board": 1, "db": ["SP TAG"],
        "steps": [
            _set([1, 1], {"gamerTag": "TAG", "prefix": "SP", "name": "Real",
                          "twitter": "tw",
                          "custom_textbox": "one\\ntwo",
                          "country_code": "GB", "state_code": "CON",
                          "mains": {"tgame": [["Mario", 2, "EX"],
                                              ["Luigi", 1]]}}),
            {"op": "save", "slot": [1, 1]},
            {"op": "typed", "slot": [1, 1], "slots": [
                {"text": "Typed Hero"},
                {"character": "Peach", "skin": 1, "variant": "EX",
                 "text": "Peach"},
                {}]},
            {"op": "save", "slot": [1, 1]},
            _set([2, 1], {"gamerTag": "FLAG", "country_code": "Agender",
                          "custom_textbox": "x"}),
            {"op": "save", "slot": [2, 1]},
        ],
    },
    {
        "name": "the editor",
        "board": None, "db": ["SP TAG"],
        "steps": [
            # What the PLAYERS panel does with the DB record it edits.
            _set("editor", "SP TAG"),
            {"op": "save", "slot": "editor"},
            {"op": "pick", "slot": "editor", "index": 2,
             "character": "Mario", "skin": 2, "variant": "EX"},
            {"op": "save", "slot": "editor"},
            {"op": "clear", "slot": "editor", "no_mains": False},
        ],
    },
    {
        "name": "bad words",
        "board": 1, "db": [], "words": BAD_WORDS,
        "steps": [
            _set([1, 1], {"gamerTag": "nasty", "prefix": "nasty sponsor",
                          "name": "so vile", "twitter": "nasty",
                          "custom_textbox": "nasty\\nline",
                          "country_code": "US"}),
            _set([1, 1], {"gamerTag": "nasty", "name": "fine"},
                 clear=False),
        ],
    },
    {
        "name": "load from a tag",
        "board": 1, "db": ["SP TAG", "LEG"], "core": False,
        "steps": [
            {"op": "tag", "tag": "tag", "team": 1, "player": 1},
            {"op": "tag", "tag": "nobody", "team": 1, "player": 1},
            {"op": "swap"},
            {"op": "tag", "tag": "leg", "team": 1, "player": 1},
            {"op": "tag", "tag": "TAG", "team": 1, "player": 1,
             "no_mains": True},
        ],
    },
    {
        "name": "the web's player edit",
        "board": 1, "db": ["SP TAG"],
        "steps": [
            {"op": "update", "team": 2, "player": 1, "data": {
                "gamerTag": "NEW", "prefix": "P",
                "mains": {"tgame": [["Zelda", 0]]},
                "country_code": "GB", "state_code": "CON",
                "savePlayerToDb": True}},
            {"op": "update", "team": 1, "player": 1, "data": {
                "gamerTag": "TAG", "prefix": "SP"}},
            {"op": "update", "team": 1, "player": 2, "data": {
                "gamerTag": "NOPE"}},
            {"op": "update", "team": 2, "player": 1, "data": {
                "twitter": "newtw", "savePlayerToDb": True}},
        ],
    },
    {
        "name": "a set's entrants",
        "board": 2, "players": 2, "db": ["LEG"], "core": False,
        "steps": [
            {"op": "setdata", "payload": {
                "entrants": ENTRANTS, "overwrite": True,
                "p1_name": "Alpha", "p2_name": "Bravo"}},
            {"op": "setdata", "payload": {
                "entrants": SELECTIONS, "has_selection_data": True}},
            {"op": "setdata", "payload": {
                "entrants": ENTRANTS, "overwrite": True, "no_mains": True}},
            {"op": "setdata", "payload": {
                "team": 1, "player": 2,
                "data": {"gamerTag": "SOLO", "twitter": "solo"}}},
        ],
    },
]


# ── observing a step, and reading the recording (both replays) ───────────

class Writes:
    """Every Set / Unset under `root`, as [relative key, encoded value]
    (an Unset as [key, None])."""

    def __init__(self, root):
        self.root = root
        self.calls = []

    def __enter__(self):
        originals = {name: getattr(StateManager, name)
                     for name in ("Set", "Unset")}
        prefix = self.root + "."

        def setting(key, value):
            if key.startswith(prefix):
                self.calls.append([key[len(prefix):], encode(value)])
            return originals["Set"](key, value)

        def unsetting(key):
            if key.startswith(prefix):
                self.calls.append([key[len(prefix):], None])
            return originals["Unset"](key)

        self._patches = [
            patch.object(StateManager, "Set", side_effect=setting),
            patch.object(StateManager, "Unset", side_effect=unsetting)]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


class Cycles:
    """Save cycles: held_state_updated for a board (its subtree is held),
    state_updated touching the editor's slot for the editor (it is not)."""

    def __init__(self, root):
        self.root = root
        self.count = 0
        self.held = root.startswith("score.")

    def _onHeld(self, prefix):
        if self.held and prefix == self.root:
            self.count += 1

    def _onDelta(self, payload):
        parts = self.root.split(".")
        if not self.held and any(
                [str(p) for p in row.get("path", [])][:len(parts)] == parts
                for row in payload.get("delta", [])):
            self.count += 1

    def __enter__(self):
        StateManager.observers.held_state_updated.subscribe(self._onHeld)
        StateManager.observers.state_updated.subscribe(self._onDelta)
        return self

    def __exit__(self, *exc):
        StateManager.observers.held_state_updated.unsubscribe(self._onHeld)
        StateManager.observers.state_updated.unsubscribe(self._onDelta)
        return False


def compact(calls):
    """Long values as digests: a full character map is ~1 kB per write."""
    return [[key, value if value is None or len(value) <= 240
             else f"sha:{digest(value)}"] for key, value in calls]


def normalise(text, root):
    """`text` with the runtime directory `root` spelt <RUNTIME>."""
    root = str(root)
    for spelling in (root, root.replace("\\", "/"),
                     root.replace("\\", "\\\\")):
        text = text.replace(spelling, "<RUNTIME>")
    return text


def collapsed(calls):
    """The writes with every run of consecutive writes to one key reduced
    to its last: a run is unobservable from outside a save cycle (the delta
    is the state against the last saved one, and a key keeps the place its
    first write gave it), and the widget implementation wrote one key per
    combo it moved where the service writes it once."""
    result = []
    for call in calls:
        if result and result[-1][0] == call[0]:
            result[-1] = call
        else:
            result.append(call)
    return result


def byPlayer(calls):
    """{player path relative to the root: its writes}; writes outside a
    player slot (the board's own keys) under ""."""
    grouped = {}
    for key, value in calls:
        parts = key.split(".")
        if len(parts) > 4 and parts[0] == "team" and parts[2] == "player":
            path, leaf = ".".join(parts[:4]), ".".join(parts[4:])
        else:
            path, leaf = "", key
        grouped.setdefault(path, []).append([leaf, value])
    return grouped


def _upsetFactorOnce(step):
    """The upset factor is recalculated when a slot's seed changed, once
    per load. The widget asked on each move of the seed spinbox -- the
    clear a load starts with set it to 0, the load put the seed back -- so
    loading a player over one with the same seed (the same player again, a
    set refreshed) recalculated it twice per card for a seed that never
    changed. These steps change no seed."""
    step["stats"]["UpsetFactorCalculation"] = 0
    step["calls"] = [call for call in step["calls"]
                     if call[0] != "upset_factor"]


def _byEffect(step):
    """The editor's clear button writes what the board's clear writes, in
    its order (PlayerService.ClearSlot, S2d): the name fields first, the
    display name once. The widget emptied the sponsor and re-derived a
    display name from the tag alone before emptying the tag. The editor's
    slot already held every key, so the order is unobservable (one save
    cycle, key order unchanged); compared by what each key ends up
    holding."""
    step["callsByEffect"] = True


# What S3e-3 changed on purpose, by (scenario, step index): applied to the
# recorded step before the comparison (Expected).
#
# Two changes from the S3e-3 review went into golden.json itself instead:
# the steps they move were recorded again, on the S3e-3 tree, and carry a
# "rerecorded" note saying why -- "a player over another" 1-4 (a load's
# clear empties the id and the city the load does not bring) and "load
# with the database" 2 (a sponsor without a tag is loaded without the
# database lookup). Every other step is still the ae922c24 recording. Step
# 1 of the first was listed here as _upsetFactorOnce; its new recording
# has that change in it.
CHANGES = {
    ("a set's entrants", 2): _upsetFactorOnce,
    ("the editor", 4): _byEffect,
}


def effect(calls):
    """{key: what its last write left} (compared without the order)."""
    result = {}
    for key, value in calls:
        result[key] = value
    return result


def Expected(name, index, step):
    """The recorded step `index` of scenario `name`, with what S3e-3
    changed on purpose (CHANGES) applied: what both replays compare to."""
    change = CHANGES.get((name, index))
    if change is None:
        return step
    step = copy.deepcopy(step)
    change(step)
    return step
