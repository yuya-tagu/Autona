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
last pre-S1d tree (2f831e58), through the widgets' public behaviour only, so
the same drivers replay it against the current tree:

    AUTONA_RECORD_PLAYER_SLOTS=1 python -m pytest tests/test_player_slot_publish.py

(on a checkout of 2f831e58 with this file and the fixture directory copied
in). Re-record only when the fixture games or the shipped country data
change. Each value is `orjson.dumps(value, OPT_NON_STR_KEYS)` -- the
program_state.json encoding -- of what one `StateManager.Set` received;
large tables are stored as digests (the first 16 hex digits of a sha256),
with a few rows in full for readable failures.

The recorded identifiers ride along with every value, which is what
tests/test_player_service.py replays without Qt.
"""
import json
import os
import shutil
from unittest.mock import patch

import pytest

from qtpy.QtCore import QCoreApplication, QEvent, QSignalBlocker, Qt
from qtpy.QtWidgets import QApplication, QComboBox

from src.core.StateManager import StateManager
# The scenarios, the custom flags, the locale and the encoding are shared
# with tests/test_player_service.py, which replays the same recording.
from tests.fixtures.player_slots.recording import (
    CUSTOM_FLAGS, GAME_FIXTURE, GAME_SCENARIOS, GAMES, GOLDEN_PATH, LOCALE,
    digest, encode, loadGolden)

RECORD = os.environ.get("AUTONA_RECORD_PLAYER_SLOTS") == "1"
GOLDEN = None if RECORD else loadGolden()

# Kept in full in the golden file; every other country is a digest.
SAMPLE_COUNTRIES = ("JP", "GB", "US")
# Matrix rows kept in full (by character en_name) on top of the digests.
SAMPLE_CHARACTERS = ("Mario", None)


def _save(section, value):
    golden = {}
    if GOLDEN_PATH.exists():
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden[section] = value
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")


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
    """Save cycles per step: held_state_updated for a board slot (the
    subtree is held), state_updated for the editor (it is not)."""

    def __init__(self, path):
        self.path = path
        self.count = 0
        self.board = path.split(".")[1] if path.startswith("score.") else None

    def _onHeld(self, prefix):
        if self.board is not None and prefix == f"score.{self.board}":
            self.count += 1

    def _onDelta(self, payload):
        parts = self.path.split(".")
        if self.board is None and any(
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
    from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
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
    AutonaCountryHelper.LoadCountries()

    games = autonaApp.dir / "user_data" / "games"
    for name in GAMES:
        shutil.copytree(GAME_FIXTURE / "games" / name, games / name)
    manager = AutonaGameAssetManager.instance
    catalog = GameAssetCatalog.instance
    catalog.ScanGames()
    persisted = SettingsManager.Get("SELECTED_GAME_CODENAME", "")

    def load(label):
        codename, modsActive, reload = GAME_SCENARIOS[label]
        manager.LoadGameByCodename(codename, async_mode=False,
                                   mods_active=modsActive,
                                   mods_reload_mode=reload)
        manager.threadpool.waitForDone()
        QApplication.processEvents()

    try:
        yield load
    finally:
        manager.threadpool.waitForDone()
        QApplication.processEvents()
        autonaApp.dropAllBoards()
        for name in GAMES:
            shutil.rmtree(games / name, ignore_errors=True)
        catalog.ScanGames()
        catalog.ResetGameState()
        manager._RebuildModels()
        SettingsManager.Set("SELECTED_GAME_CODENAME", persisted)
        for name in CUSTOM_FLAGS:
            (flags / name).unlink(missing_ok=True)
        (AutonaLocaleHelper.programLocale, AutonaLocaleHelper.exportLocale,
         AutonaLocaleHelper.remapping) = saved
        AutonaCountryHelper.LoadCountries()
        autonaApp.settle()


def _boardPlayer(autonaApp):
    return autonaApp.board(1).team1playerWidgets[0]


# ── the character slot: every combination of the three combos ────────────

def _slotIdentity(character, color, variant):
    data = character.currentData()
    variantData = variant.currentData()
    return {
        "character": data.get("en_name") if isinstance(data, dict) else None,
        "text": character.currentText(),
        "skin": color.currentIndex(),
        "variant": (variantData.get("en_name")
                    if isinstance(variantData, dict) and variantData
                    else None),
    }


def characterMatrix(player):
    """Publish slot 1 once per character row x skin row x variant row.

    The combos are set with their signals blocked and the publish is asked
    for directly (CharactersChanged), so each entry is exactly one value.
    Also covered: no skin row picked (-1), and a character typed by hand
    (no row: the slot carries the typed text as its name).
    """
    _element, character, color, variant = player.character_elements[0]
    entries = []
    rows = list(range(character.model().rowCount())) + ["typed"]
    for row in rows:
        with QSignalBlocker(character), QSignalBlocker(color), \
                QSignalBlocker(variant):
            if row == "typed":
                character.setCurrentIndex(-1)
                character.setEditText("Typed Name")
            else:
                character.setCurrentIndex(row)
            player.LoadSkinOptions(character, color)
        skins = list(range(color.model().rowCount())) + [-1]
        variants = list(range(variant.model().rowCount())) or [-1]
        for skin in skins:
            for variantRow in variants:
                with QSignalBlocker(color), QSignalBlocker(variant):
                    color.setCurrentIndex(skin)
                    variant.setCurrentIndex(variantRow)
                with SetTrace(player.path) as trace:
                    player.CharactersChanged()
                assert [leaf for leaf, _ in trace.calls] == ["character"]
                entry = _slotIdentity(character, color, variant)
                entry["published"] = trace.calls[0][1]
                entries.append(entry)
    return entries


def _matrixDigests(entries):
    return [{key: value for key, value in entry.items() if key != "published"}
            | {"digest": digest(entry["published"])} for entry in entries]


@pytest.mark.parametrize("label", list(GAME_SCENARIOS))
def test_every_character_slot_publishes_the_recorded_value(
        slotEnv, autonaApp, label):
    slotEnv(label)
    player = _boardPlayer(autonaApp)

    entries = characterMatrix(player)

    if RECORD:
        _save(f"characters.{label}", {
            "entries": _matrixDigests(entries),
            "samples": [entry for entry in entries
                        if entry["character"] in SAMPLE_CHARACTERS],
        })
        return
    golden = GOLDEN[f"characters.{label}"]
    assert [{k: v for k, v in e.items() if k != "digest"}
            for e in golden["entries"]] \
        == [{k: v for k, v in e.items() if k != "published"}
            for e in entries], "the combo rows themselves moved"
    for sample in golden["samples"]:
        match = next(entry for entry in entries
                     if {k: v for k, v in entry.items() if k != "published"}
                     == {k: v for k, v in sample.items() if k != "published"})
        assert match["published"] == sample["published"], sample
    assert _matrixDigests(entries) == golden["entries"]


# ── the location: every country row, every region row ─────────────────────

def _code(combo):
    data = combo.currentData()
    return data.get("code") if isinstance(data, dict) else None


def countryRows(player):
    """Select every row of the country combo through the real signal path.

    Each entry is what one pick published, in order (the region list is
    rebuilt first -- it is connected first -- then the country itself).
    Row 0 (no country) goes last, so that it is a change.
    """
    country = player._w("country", QComboBox)
    rows = list(range(1, country.model().rowCount())) + [0]
    entries = []
    for row in rows:
        with SetTrace(player.path) as trace:
            country.setCurrentIndex(row)
        entries.append({"row": row, "code": _code(country),
                        "hasData": country.currentData() is not None,
                        "calls": trace.calls})
    return entries


def regionRows(player, codes):
    """Every region row of every country in `codes`, picked in turn."""
    country = player._w("country", QComboBox)
    state = player._w("state", QComboBox)
    model = country.model()
    byCode = {}
    for row in range(model.rowCount()):
        data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("code") in codes:
            byCode.setdefault(data["code"], row)
    result = {}
    for code in codes:
        country.setCurrentIndex(byCode[code])
        picks = []
        for row in range(1, state.model().rowCount()):
            with SetTrace(player.path) as trace:
                state.setCurrentIndex(row)
            picks.append([_code(state), trace.calls])
        result[code] = picks
    return result


def test_every_country_and_region_pick_publishes_the_recorded_value(
        slotEnv, autonaApp):
    from src.core.CountryCatalog import CountryCatalog

    player = _boardPlayer(autonaApp)

    countries = countryRows(player)
    standard = [entry["code"] for entry in countries
                if entry["code"] and CountryCatalog.instance.States(
                    entry["code"])]
    regions = regionRows(player, standard)

    recorded = {
        "countries": [{k: v for k, v in e.items() if k != "calls"}
                      | {"digest": digest(e["calls"])} for e in countries],
        "countrySamples": [e for e in countries
                           if e["code"] in SAMPLE_COUNTRIES
                           or not e["hasData"] or e["row"] == 0
                           or e["code"] in [n[:-4] for n in CUSTOM_FLAGS]],
        "regions": {code: digest(picks) for code, picks in regions.items()},
        # Japan in full; from the UK only Cornwall, whose flag ships as
        # _CON.png (Windows will not open CON.png).
        "regionSamples": {"JP": regions["JP"],
                          "GB.CON": [pick for pick in regions["GB"]
                                     if pick[0] == "CON"]},
    }
    if RECORD:
        _save("locations", recorded)
        return
    expected = _expectedLocations(GOLDEN["locations"])
    assert len(recorded["countries"]) == len(expected["countries"])
    for sample in expected["countrySamples"]:
        actual = next(e for e in recorded["countrySamples"]
                      if e["row"] == sample["row"])
        assert actual == sample, sample["code"]
    assert recorded["countries"] == expected["countries"]
    assert recorded["regionSamples"] == expected["regionSamples"]
    assert recorded["regions"] == expected["regions"]


# ── the two intentional changes ──────────────────────────────────────────
#
# Everything above must equal the recording byte for byte, except for what
# these two functions rewrite into it -- each change has a test of its own
# further down (and in tests/test_player_widgets.py).

def _expectedLocations(golden):
    """A row without data (the custom-flag separator; a typed name) used to
    publish its missing data as `null`. It publishes {} now, the type of
    every other "no country". (A typed region is the same change; the
    recording has none, see test_a_typed_region_publishes_no_region.)"""
    golden = json.loads(json.dumps(golden))
    fixed = {}
    for sample in golden["countrySamples"]:
        if not sample["hasData"]:
            sample["calls"] = [[leaf, "{}" if value == "null" else value]
                               for leaf, value in sample["calls"]]
            fixed[sample["row"]] = digest(sample["calls"])
    for entry in golden["countries"]:
        if entry["row"] in fixed:
            entry["digest"] = fixed[entry["row"]]
    return golden


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
    two and leave " [L]" on the old name. Clear() goes through that signal,
    so its emptied `name` now comes ahead of the name pair (and the images
    published with it). Same writes, same save cycle; the slot already has
    every key there, so the state's key order does not move either."""
    golden = json.loads(json.dumps(golden))
    for side in ("board", "editor"):
        step = next(s for s in golden[side]
                    if s["step"] == "set data without a country")
        at = step["calls"].index(["name", '"NOWHERE"']) + 1
        step["calls"][at:at] = [["state", "{}"], ["country", "{}"]]

        calls = next(s for s in golden[side] if s["step"] == "clear")["calls"]
        name = next(call for call in calls if call[0] == "name")
        calls.remove(name)
        calls.insert(calls.index(
            next(call for call in calls if call[0] == "mergedName")), name)
    return golden


# ── whole operations: every write, in order, and the save cycles ─────────

def _steps(player, codename):
    """The operations an operator (or a set load) runs on one card."""
    def slot(index):
        return player.character_elements[index]

    def pick(combo, row):
        return lambda: combo().setCurrentIndex(row)

    return [
        ("three slots", lambda: player.SetCharactersPerPlayer(3)),
        ("set data with mains", lambda: player.SetData({
            "gamerTag": "TAG", "prefix": "SP", "country_code": "JP",
            "state_code": "13",
            "mains": {codename: [["Mario", 2, "EX"], ["Luigi", 1],
                                 ["Bowser"]]}}, dontLoadFromDB=True)),
        ("pick a variant", pick(lambda: slot(1)[3], 2)),
        ("pick a skin", pick(lambda: slot(0)[2], 1)),
        ("pick a character", pick(lambda: slot(2)[1], 3)),
        ("swap two slots", lambda: player.SwapCharacters(0, 1)),
        ("set data with a legacy main", lambda: player.SetData({
            "gamerTag": "LEG", "mains": ["Peach", 1], "country_code": "US",
            "state_code": "CA"}, dontLoadFromDB=True)),
        ("set data with a free-form region", lambda: player.SetData({
            "gamerTag": "FREE", "country_code": "JP",
            "state_code": "Tokyo"}, dontLoadFromDB=True)),
        ("set data without a country", lambda: player.SetData({
            "gamerTag": "NOWHERE"}, dontLoadFromDB=True)),
        ("set data with a custom flag", lambda: player.SetData({
            "gamerTag": "FLAG", "country_code": "Agender"},
            dontLoadFromDB=True)),
        ("clear", lambda: player.Clear()),
        ("one slot", lambda: player.SetCharactersPerPlayer(1)),
    ]


def runSteps(player, codename):
    trace = []
    for name, step in _steps(player, codename):
        with CycleCount(player.path) as cycles, \
                SetTrace(player.path) as calls:
            step()
            QApplication.processEvents()
        trace.append({"step": name, "calls": compact(calls.calls),
                      "cycles": cycles.count})
    return trace


def compact(calls):
    """Long values as digests: a full character map is ~1 kB per write."""
    return [[leaf, value if len(value) <= 240 else f"sha:{digest(value)}"]
            for leaf, value in calls]


def _editor(path):
    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
    return AutonaScoreboardPlayerWidget(index=1, teamNumber=1, path=path)


def _drop(widget):
    widget.setParent(None)
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


def test_whole_operations_publish_the_recorded_writes(slotEnv, autonaApp):
    from src.core import StateSchema as schema

    slotEnv("tgame")
    player = _boardPlayer(autonaApp)
    board = runSteps(player, "tgame")

    # The PLAYERS editor: the same widget on the one non-board path
    # (agent-pitfalls #7). Its construction publishes too.
    editorPath = f"{schema.PLAYER_EDITOR_ROOT}.1"
    with CycleCount(editorPath) as cycles, SetTrace(editorPath) as built:
        editor = _editor(editorPath)
        QApplication.processEvents()
    try:
        editorTrace = [{"step": "construction",
                        "calls": compact(built.calls),
                        "cycles": cycles.count}]
        editorTrace += runSteps(editor, "tgame")
    finally:
        _drop(editor)
        StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        autonaApp.settle()

    recorded = {"board": board, "editor": editorTrace}
    if RECORD:
        _save("operations", recorded)
        return
    _assertOperations(recorded, _expectedOperations(GOLDEN["operations"]))


def _assertOperations(recorded, golden):
    for side in ("board", "editor"):
        assert [s["step"] for s in recorded[side]] \
            == [s["step"] for s in golden[side]]
        for actual, expected in zip(recorded[side], golden[side]):
            assert actual == expected, (side, actual["step"])


# ── the intentional changes, on their own ────────────────────────────────

def test_a_country_row_without_data_publishes_no_country(slotEnv, autonaApp):
    """The custom-flag separator has no row data. Picking it used to publish
    null as the country; it is {} now -- the type every other "no country"
    has always had -- region included. (A name typed into the editable
    combo lands on a row without data too.)"""
    if RECORD:
        pytest.skip("not part of the recording (the old tree publishes null)")
    player = _boardPlayer(autonaApp)
    country = player._w("country", QComboBox)
    model = country.model()
    separator = next(
        row for row in range(1, model.rowCount())
        if model.index(row, 0).data(Qt.ItemDataRole.UserRole) is None)
    japan = next(
        row for row in range(1, model.rowCount())
        if (model.index(row, 0).data(Qt.ItemDataRole.UserRole) or {})
        .get("code") == "JP")

    country.setCurrentIndex(japan)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.country")["code"] == "JP"

    country.setCurrentIndex(separator)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.country") == {}
    assert autonaApp.get(f"{player.path}.state") == {}


def test_a_typed_region_publishes_no_region(autonaApp):
    """The region combo is editable too. Enter on a name that matches no
    row inserts a row without data and selects it; that published null as
    the region, and publishes {} now (as the separator above). The country
    is left alone."""
    if RECORD:
        pytest.skip("not part of the recording (the old tree publishes null)")
    player = _boardPlayer(autonaApp)
    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.state")["code"] == "13"
    country = autonaApp.get(f"{player.path}.country")

    state = player._w("state", QComboBox)
    rows = state.model().rowCount()
    state.lineEdit().setText("Nowhere Prefecture")
    state.lineEdit().returnPressed.emit()
    autonaApp.settle()

    # The combo really took the typed name as a row of its own.
    assert state.model().rowCount() == rows + 1
    assert state.currentText() == "Nowhere Prefecture"
    assert state.currentData() is None
    assert autonaApp.get(f"{player.path}.state") == {}
    assert autonaApp.get(f"{player.path}.country") == country
