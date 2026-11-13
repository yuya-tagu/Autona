"""What a player card publishes for its characters and its location (ADR-005 S1d).

Until S1d the card read the selected rows' whole UserRole dicts back out of
its Qt combos (character / skin / variant, country / region) and handed them
to PlayerService as the value to publish. Since S1d it hands over identifiers
only -- the character's en_name, the skin row, the variant's en_name, the
country code, the region code -- and PlayerService builds the value from the
catalogs. The state is an external contract (program_state.json, the
Socket.IO delta, the OBS overlays), so the move is pinned against what the
old path published, byte for byte: a Qt model hands its dicts out as
QVariantMaps, i.e. with the keys of every nested dict in UTF-16 order, and
both program_state.json (orjson) and the Socket.IO payload (json.dumps) are
serialised in insertion order.

`fixtures/player_slots/golden.json` was recorded by THIS module against the
last pre-S1d tree (2f831e58, with AUTONA_RECORD_PLAYER_SLOTS=1), through the
widgets' public behaviour only. Its character and location sections are
replayed by tests/test_player_service.py without Qt (the identifiers ride
along with every value); this module replays its whole operations on one
slot, through what the card called -- the card itself until ADR-005
S4ac-3, which removed it with the Qt SCORE.

It cannot be recorded again: that tree does not have what the operations go
through since ADR-005 S4n (AutonaActions.SetRoster), nor the card since
S4ac-3, and on a later tree a recording would only record that tree, which
pins nothing, with the changes made on purpose since (_expectedOperations)
inside it. A change of the fixture games or the shipped country data is
carried into golden.json by hand, each difference looked at. Each value is
`orjson.dumps(value, OPT_NON_STR_KEYS)` -- the program_state.json encoding --
of what one `StateManager.Set` received; long values are stored as digests
(the first 16 hex digits of a sha256).

Since ADR-005 S4n the character count is driven the way the app changes it
(AutonaActions.SetRoster; the card's own setter went with the Qt Player List,
its only caller), and what only that panel's card could do -- the editor's
path, swapping two character rows -- left the recording.
"""
import json
from unittest.mock import patch

import pytest

from qtpy.QtWidgets import QApplication

from src.core.Actions import AutonaActions
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.PlayerService import CharacterSlot, PlayerService
from src.core.StateManager import StateManager
# The scenarios, the custom flags, the locale and the encoding are shared
# with tests/test_player_service.py, which replays the same recording.
from tests.conftest import copy_trees, drain_tasks, remove_tree
from tests.fixtures.player_slots.recording import (
    CUSTOM_FLAGS, GAME_FIXTURE, GAME_SCENARIOS, GAMES, LOCALE, digest,
    encode, loadGolden)

GOLDEN = loadGolden()

# The slot the recording drove: board 1's first card of team 1.
PATH = "score.1.team.1.player.1"


class SetTrace:
    """Every StateManager.Set under one player path, as (leaf, encoded)."""

    def __init__(self, path):
        self.path = path
        self.calls = []

    def __enter__(self):
        original = StateManager.Set
        prefix = self.path + "."

        def recording(key, value):
            if key.startswith(prefix):
                self.calls.append([key[len(prefix):], encode(value)])
            return original(key, value)

        self._patch = patch.object(StateManager, "Set", side_effect=recording)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class CycleCount:
    """Save cycles per step: held_state_updated for the slot's board (its
    subtree is held)."""

    def __init__(self, path):
        self.count = 0
        self.board = path.split(".")[1]

    def _onHeld(self, prefix):
        if prefix == f"score.{self.board}":
            self.count += 1

    def __enter__(self):
        StateManager.observers.held_state_updated.subscribe(self._onHeld)
        return self

    def __exit__(self, *exc):
        StateManager.observers.held_state_updated.unsubscribe(self._onHeld)
        return False


@pytest.fixture
def slotEnv(autonaApp):
    """The fixture games installed and the countries (with custom flags)
    loaded, both under the game recording's locale.

    Yields `load(label)`, which loads one of GAME_SCENARIOS. Afterwards the
    games and flags are removed and both catalogs go back to the harness's
    own locale and data, so later tests see what they always saw.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager
    from src.core.CountryCatalog import CountryCatalog
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    locale = LOCALE
    saved = (AutonaLocaleHelper.programLocale, AutonaLocaleHelper.exportLocale,
             AutonaLocaleHelper.remapping)
    AutonaLocaleHelper.programLocale = locale["program"]
    AutonaLocaleHelper.exportLocale = locale["export"]
    AutonaLocaleHelper.remapping = locale["remapping"]

    flags = autonaApp.dir / "user_data" / "additional_flag"
    flags.mkdir(parents=True, exist_ok=True)
    for name in CUSTOM_FLAGS:
        (flags / name).write_bytes(b"")
    CountryCatalog.instance.Load()

    games = autonaApp.dir / "user_data" / "games"
    copy_trees([(GAME_FIXTURE / "games" / name, games / name)
                for name in GAMES])
    manager = AutonaGameAssetManager.instance
    catalog = GameAssetCatalog.instance
    catalog.ScanGames()
    persisted = SettingsManager.Get("SELECTED_GAME_CODENAME", "")

    def load(label):
        codename, modsActive, reload = GAME_SCENARIOS[label]
        # The load job, this (the GUI) thread pumped while it runs.
        manager.LoadGameByCodename(codename, mods_active=modsActive,
                                   mods_reload_mode=reload)
        drain_tasks()

    try:
        yield load
    finally:
        drain_tasks()
        autonaApp.dropAllBoards()
        for name in GAMES:
            remove_tree(games / name)
        catalog.ScanGames()
        catalog.ResetGameState()
        manager._RebuildModels()
        SettingsManager.Set("SELECTED_GAME_CODENAME", persisted)
        for name in CUSTOM_FLAGS:
            (flags / name).unlink(missing_ok=True)
        (AutonaLocaleHelper.programLocale, AutonaLocaleHelper.exportLocale,
         AutonaLocaleHelper.remapping) = saved
        CountryCatalog.instance.Load()
        autonaApp.settle()


# ── the intentional changes ──────────────────────────────────────────────
#
# The operations below must equal the recording byte for byte, except for
# what this function rewrites into it. (The location section's one change,
# a row without data publishing {} where it published null, is
# tests/test_player_service.py's to replay.)

def _expectedOperations(golden):
    """The recording, with the changes made on purpose since.

    SetData for a player without a country used to leave the previous
    player's country in the state (its Clear() empties the combo with the
    signals blocked). The emptying is announced now: the regions are
    rebuilt for no country, then the country is published, inside the same
    save cycle.

    ADR-005 S2b: on the tag's editingFinished the card writes the text to
    the state (_OnTextChanged) before it publishes the display name
    (NameChanged), so a team rebuild of the names cannot land between the
    two and leave " [L]" on the old name. Clear() went through that signal,
    so its emptied `name` came ahead of the name pair (and the images
    published with it). Same writes, same save cycle; the slot already has
    every key there, so the state's key order does not move either. Since
    ADR-005 S3e-3 SetData and Clear are core's (PlayerService.ApplyData /
    ClearSlot), which publish these same sequences without the widgets."""
    golden = json.loads(json.dumps(golden))
    step = next(s for s in golden if s["step"] == "set data without a country")
    at = step["calls"].index(["name", '"NOWHERE"']) + 1
    step["calls"][at:at] = [["state", "{}"], ["country", "{}"]]

    calls = next(s for s in golden if s["step"] == "clear")["calls"]
    name = next(call for call in calls if call[0] == "name")
    calls.remove(name)
    calls.insert(calls.index(
        next(call for call in calls if call[0] == "mergedName")), name)
    return golden


# ── whole operations: every write, in order, and the save cycles ─────────

def _repicked(path, index, **picked):
    """A card's character row `index` (1-based) re-picked on one of its
    combos: the slots as the card showed them -- each the pick that builds
    its value (PlayerService.SlotFromValue) -- with that row's `picked`
    fields changed, as the card's CharactersChanged handed them over."""
    player = PlayerService(path)
    values = StateManager.Get(player.Key("character")) or {}
    slots = [player.SlotFromValue(values.get(i, values.get(str(i))))
             for i in range(1, len(values) + 1)]
    was = slots[index - 1]
    slots[index - 1] = CharacterSlot(
        picked.get("character", was.character), picked.get("skin", was.skin),
        picked.get("variant", was.variant), picked.get("text", was.text))
    return player, slots


def _comboRow(rows, row):
    """The data of row `row` of a card's character / variant combo: a blank
    row first, then the catalog's rows (AutonaGameAssetManager's models)."""
    return rows[row - 1][1]


def _steps(path, codename):
    """The operations an operator (or a set load) ran on one card, as what
    the card called (until ADR-005 S4ac-3)."""
    board = int(path.split(".")[1])
    player = PlayerService(path)

    def characters(number):
        return lambda: AutonaActions.instance.SetRoster(
            board, characters=number)

    def setData(data):
        # The card's SetData(data, dontLoadFromDB=True).
        return lambda: player.ApplyData(data, False, True, False)

    def pickVariant(index, row):
        # The variant combo's currentIndexChanged: CharactersChanged.
        def run():
            variant = _comboRow(
                GameAssetCatalog.instance.Current().variantRows, row)
            service, slots = _repicked(path, index,
                                       variant=variant["en_name"])
            service.PublishCharacterSlots(slots)
        return run

    def pickSkin(index, row):
        # The skin combo's currentIndexChanged: CharactersChanged.
        def run():
            service, slots = _repicked(path, index, skin=row)
            service.PublishCharacterSlots(slots)
        return run

    def pickCharacter(index, row):
        # The character combo's currentIndexChanged: the skin combo took the
        # character's skins and its first row, which published the slots
        # (CharactersChanged), then the character combo published them
        # again -- two save cycles of the same value.
        def run():
            character = _comboRow(
                GameAssetCatalog.instance.Current().characterRows, row)
            service, slots = _repicked(
                path, index, character=character["en_name"], skin=0,
                text=character.get("name") or character["en_name"])
            service.PublishCharacterSlots(slots)
            service.PublishCharacterSlots(slots)
        return run

    return [
        ("three slots", characters(3)),
        ("set data with mains", setData({
            "gamerTag": "TAG", "prefix": "SP", "country_code": "JP",
            "state_code": "13",
            "mains": {codename: [["Mario", 2, "EX"], ["Luigi", 1],
                                 ["Bowser"]]}})),
        ("pick a variant", pickVariant(2, 2)),
        ("pick a skin", pickSkin(1, 1)),
        ("pick a character", pickCharacter(3, 3)),
        ("set data with a legacy main", setData({
            "gamerTag": "LEG", "mains": ["Peach", 1], "country_code": "US",
            "state_code": "CA"})),
        ("set data with a free-form region", setData({
            "gamerTag": "FREE", "country_code": "JP",
            "state_code": "Tokyo"})),
        ("set data without a country", setData({"gamerTag": "NOWHERE"})),
        ("set data with a custom flag", setData({
            "gamerTag": "FLAG", "country_code": "Agender"})),
        # The card's clear button: ClearSlot(no_mains=False).
        ("clear", lambda: player.ClearSlot(False)),
        ("one slot", characters(1)),
    ]


def runSteps(path, codename):
    trace = []
    for name, step in _steps(path, codename):
        with CycleCount(path) as cycles, SetTrace(path) as calls:
            step()
            QApplication.processEvents()
        trace.append({"step": name, "calls": compact(calls.calls),
                      "cycles": cycles.count})
    return trace


def compact(calls):
    """Long values as digests: a full character map is ~1 kB per write."""
    return [[leaf, value if len(value) <= 240 else f"sha:{digest(value)}"]
            for leaf, value in calls]


def test_whole_operations_publish_the_recorded_writes(slotEnv, autonaApp):
    slotEnv("tgame")
    autonaApp.ready(1)
    # A fresh card had one character slot when this was recorded; the board's
    # default is more now.
    AutonaActions.instance.SetRoster(1, characters=1)
    autonaApp.settle()
    recorded = {"board": runSteps(PATH, "tgame")}
    _assertOperations(recorded["board"],
                      _expectedOperations(GOLDEN["operations"]["board"]))


def _collapsed(calls):
    """The writes with every run of consecutive writes to one key reduced
    to its last."""
    result = []
    for call in calls:
        if result and result[-1][0] == call[0]:
            result[-1] = call
        else:
            result.append(call)
    return result


def _assertOperations(recorded, golden):
    """Every step's writes and save cycles as recorded -- the loads' runs
    of writes to one key collapsed: a load is PlayerService.ApplyData since
    ADR-005 S3e-3, which writes `character` once where the card's SetData
    wrote it once per combo it moved. A run is unobservable from outside the
    save cycle (the delta is the state against the last saved one, and a key
    keeps the place its first write gave it); tests/test_player_apply.py
    compares the loads the same way."""
    assert [s["step"] for s in recorded] == [s["step"] for s in golden]
    for actual, expected in zip(recorded, golden):
        if actual["step"].startswith("set data"):
            actual = dict(actual, calls=_collapsed(actual["calls"]))
            expected = dict(expected, calls=_collapsed(expected["calls"]))
        assert actual == expected, actual["step"]


# ── the intentional changes, on their own ────────────────────────────────
