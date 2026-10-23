"""The game asset catalog, with no QApplication (ADR-005 S1a).

Everything the app knows about the installed games used to live on the Qt
asset manager, half of it only inside QStandardItemModels. It was extracted
into `src/core/GameAssetCatalog.py`; the Qt side only renders snapshots.

The reference for "unchanged behaviour" is `tests/fixtures/game_assets/
golden.json`, recorded from the pre-extraction AutonaGameAssetManager
(models read back through Qt, payloads through WebServerActions) against
the synthetic games next to it. Its `locale` block is the locale setup the
recording ran under, and it is reproduced here through the catalog's
injected callables.

Isolation follows tests/test_tournament_info_service.py: a throwaway CWD
(the catalog reads ./user_data/games and writes ./out/css) and the `game`
subtree of the global StateManager snapshotted around every test.
"""
import dataclasses
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from src.core.DictHelper import deep_clone
from src.core.GameAssetCatalog import (
    GameAssetCatalog, GameAssetSnapshot, _qvariantmap_copy, _utf16_sorted)
from src.core.StateManager import StateManager as SM

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_assets"
GOLDEN = json.loads((FIXTURE / "golden.json").read_text(encoding="utf-8"))

_ORIGINAL_CWD = os.getcwd()

PREFIX = "game"


def _remapFor(table):
    # AutonaLocaleHelper.GetRemaps, over the recording's remapping table.
    def remap(language):
        for name, languages in table.items():
            if language.replace('-', '_') in languages:
                return name
        return None
    return remap


def _norm(o):
    """What golden.json can hold: str keys, lists for tuples (as recorded)."""
    if isinstance(o, dict):
        return {(k if isinstance(k, str) else f"<{type(k).__name__}>{k}"):
                _norm(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_norm(v) for v in o]
    return o


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    work = tmp_path / "run"
    (work / "out").mkdir(parents=True)
    shutil.copytree(FIXTURE / "games", work / "user_data" / "games")
    shutil.copy(FIXTURE / "characters.json",
                work / "user_data" / "characters.json")
    os.chdir(work)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(PREFIX, None)
        SM.lastSavedState.pop(PREFIX, None)
        SM.changedKeys = []
    try:
        yield work
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
        os.chdir(_ORIGINAL_CWD)


def _catalog():
    locale = GOLDEN["locale"]
    catalog = GameAssetCatalog(
        locales=lambda: (locale["program"], locale["export"]),
        remap=_remapFor(locale["remapping"]))
    catalog.ResetGameState()
    catalog.ScanGames()
    catalog.ReloadStartGGCharacters()
    return catalog


@pytest.fixture
def catalog():
    return _catalog()


def _rows(rows):
    return [[text, _norm(data)] for text, data in rows]


# ── golden: the catalog reproduces the old manager ──────────────────────

def test_the_scan_matches_the_recording(catalog):
    assert _norm(catalog.Games()) == GOLDEN["games"]
    assert catalog.GameCodenames() == list(GOLDEN["games"].keys())
    assert catalog.GamesPayload() == GOLDEN["gamesPayload"]
    assert list(catalog.GamesPayload()) == list(GOLDEN["gamesPayload"])


SCENARIOS = {
    # label: (codename, modsActive, reload)
    "tgame": ("tgame", False, False),
    "tgame_mods": ("tgame", True, True),
    "game2": ("game2", False, False),
}


@pytest.mark.parametrize("label", list(SCENARIOS))
def test_a_load_matches_the_recording(catalog, label):
    codename, modsActive, reload = SCENARIOS[label]
    golden = GOLDEN["scenarios"][label]

    snap = catalog.SelectGame(codename, modsActive=modsActive, reload=reload)

    assert snap is catalog.Current()
    assert snap.codename == codename
    assert snap.modsActive == modsActive
    assert _norm(snap.game) == golden["selectedGame"]
    assert _norm(snap.characters) == golden["characters"]
    assert _norm(snap.skins) == golden["skins"]
    assert _norm(snap.variants) == golden["variants"]
    assert _norm(snap.colors) == golden["colors"]
    assert _norm(snap.stages) == golden["stages"]
    assert _norm(snap.stockIconPaths) == golden["stockIcons"]
    assert _norm(snap.assetIndex) == golden["characterAssetIndex"]
    assert _norm(snap.variantIconIndex) == golden["variantIconIndex"]
    assert snap.biggestCompletePack == golden["biggestCompletePack"]
    assert snap.hasModdedContent == golden["hasModdedContent"]

    for key, expected in golden["characterAssets"].items():
        characterCodename, skin = key.rsplit(":", 1)
        assert _norm(catalog.CharacterAssets(characterCodename, int(skin))) \
            == expected, key

    # The combo contents, without the blank first row the Qt side adds.
    models = golden["models"]
    assert models["character"][0] == ["", None]
    assert _rows(snap.characterRows) == models["character"][1:]
    assert _rows(snap.variantRows) == models["variant"][1:]
    assert _rows(snap.colorRows) == models["color"][1:]
    assert _rows(snap.stageRows) == models["stage"]
    assert models["stageWithBlank"][0] == ["", {}]
    assert _rows(snap.stageRowsSorted) == models["stageWithBlank"][1:]
    assert {en: [[text, _norm(data)] for _skin, text, data in rows]
            for en, rows in snap.skinRows.items()} == models["skins"]

    payloads = golden["payloads"]
    for name, actual in (("characters", catalog.CharactersPayload()),
                         ("variants", catalog.VariantsPayload()),
                         ("games", catalog.GamesPayload())):
        assert _norm(actual) == payloads[name], name
        # Socket.IO keeps the order, so it is part of the contract.
        assert list(actual) == list(payloads[name]), name
        # ...at every level (S1b): the bytes SocketioJson (json.dumps, in
        # insertion order) sends. The recording's nested dicts are in
        # QVariantMap order, having been read back out of the Qt models.
        assert json.dumps(actual) == json.dumps(payloads[name]), name

    state = SM.Get(PREFIX)
    assert state == golden["state"]
    assert list(state) == list(golden["state"])
    assert sorted(os.listdir("./out/css")) == golden["css"]

    startgg = golden["startgg"]
    for smashggId, expected in startgg["characters"].items():
        assert _norm(catalog.CharacterFromStartGGId(int(smashggId))) \
            == expected, smashggId
    for smashggId, expected in startgg["stages"].items():
        assert _norm(catalog.StageFromStartGGId(int(smashggId))) \
            == expected, smashggId


def test_the_start_gg_game_id_resolves_alternate_versions(catalog):
    assert catalog.GameFromStartGGId(1386) == "tgame"
    assert catalog.GameFromStartGGId("9999") == "tgame"
    assert catalog.GameFromStartGGId(43868) == "game2"
    assert catalog.GameFromStartGGId(1) is None


def test_the_combo_order_is_qt_s_utf16_order():
    # Qt compares UTF-16 code units: the astral 𝓖 (a surrogate pair,
    # 0xD835...) sorts before fullwidth ｚ (0xFF5A), where Python's
    # code-point order puts it after. An invalid QVariant (None) goes last.
    rows = [("ｚｅｌｄａ", 1), (None, 2), ("𝓖host", 3), ("Bowser", 4)]
    assert [r[1] for r in _utf16_sorted(rows)] == [4, 3, 1, 2]
    assert sorted(["ｚｅｌｄａ", "𝓖host"]) == ["ｚｅｌｄａ", "𝓖host"]


def test_the_payload_key_order_is_qvariantmap_s():
    """The Web payloads keep the nested key order a round trip through a
    QStandardItem's UserRole (QVariantMap) gave them (S1b). Expected values
    recorded from PySide6: keys in UTF-16 order at every level, lists kept,
    and a dict with a non-str key is no QVariantMap, nor a tuple a
    QVariantList -- they stay as they were, nested dicts included."""
    value = {"ｚ": 1, "𝓖": 2, "B": {"b": 1, "a": 2},
             "a": [{"y": 1, "x": 2}], "Ä": None, "10": 1.0, "2": True}
    copied = _qvariantmap_copy(value)
    assert json.dumps(copied) == json.dumps(
        {"10": 1.0, "2": True, "B": {"a": 2, "b": 1}, "a": [{"x": 2, "y": 1}],
         "Ä": None, "𝓖": 2, "ｚ": 1})
    copied["B"]["a"] = -1
    assert value["B"]["a"] == 2

    notAMap = {"outer": {1: "int key", "s": {"b": 1, "a": 2}}}
    assert json.dumps(_qvariantmap_copy(notAMap)) == json.dumps(notAMap)

    # PySide6 hands a tuple back as the same tuple, inner dicts untouched,
    # also inside a list (which it does convert).
    tuples = {"b": ({"y": 1, "x": 2}, {"d": 1, "c": 2}),
              "l": [({"y": 1, "x": 2},)], "a": 1}
    copied = _qvariantmap_copy(tuples)
    assert json.dumps(copied) == json.dumps(
        {"a": 1, "b": [{"y": 1, "x": 2}, {"d": 1, "c": 2}],
         "l": [[{"y": 1, "x": 2}]]})
    copied["b"][0]["y"] = -1
    assert tuples["b"][0]["y"] == 1


# ── skip rules ──────────────────────────────────────────────────────────

def test_the_selected_game_is_not_reloaded_without_reload(catalog):
    first = catalog.SelectGame("tgame")
    revision = SM.revision

    assert catalog.SelectGame("tgame") is None
    assert catalog.Current() is first
    assert SM.revision == revision

    again = catalog.SelectGame("tgame", reload=True)
    assert again is not None and again.generation > first.generation


def test_an_empty_codename_keeps_what_is_loaded(catalog):
    """The tournament URL was cleared (LoadGameAssets(0)): PdM decision
    2026-10-23 -- do nothing. The old loader removed the CSS, half-switched
    the selection to {"codename": ""} and logged a KeyError."""
    loaded = catalog.SelectGame("tgame")
    state = deep_clone(SM.Get(PREFIX))
    css = sorted(os.listdir("./out/css"))

    for codename in ("", None):
        assert catalog.SelectGame(codename) is None

    assert catalog.Current() is loaded
    assert SM.Get(PREFIX) == state
    assert sorted(os.listdir("./out/css")) == css


def test_an_unknown_codename_or_no_scan_loads_nothing(catalog):
    assert catalog.SelectGame("nope") is None
    assert catalog.Current().codename is None

    unscanned = GameAssetCatalog()
    assert unscanned.SelectGame("tgame") is None


def test_forget_selection_keeps_the_assets(catalog):
    loaded = catalog.SelectGame("tgame")
    catalog.ForgetSelection()

    forgotten = catalog.Current()
    assert forgotten.game == {} and forgotten.codename is None
    # Same assets, so the same generation: the Qt models stay valid.
    assert forgotten.generation == loaded.generation
    assert forgotten.characters is loaded.characters
    # ...and picking the same game again is a load, not a skip.
    assert catalog.SelectGame("tgame") is not None


def test_reset_publishes_an_empty_game(catalog):
    loaded = catalog.SelectGame("tgame")
    catalog.ResetGameState()

    assert SM.Get(PREFIX) == {}
    assert catalog.Current().codename is None
    assert catalog.Current().generation > loaded.generation


# ── snapshot isolation ──────────────────────────────────────────────────

def test_snapshots_are_not_written_through(catalog):
    snap = catalog.SelectGame("tgame")
    pristine = deep_clone(_norm(snap.characters))

    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.codename = "other"

    catalog.Character("Mario")["codename"] = "x"
    catalog.CharacterByCodename("mario")["codename"] = "x"
    catalog.CharacterAssets("mario", 0)["full"]["average_size"]["x"] = -1
    catalog.CharactersPayload()["マリオ"]["codename"] = "x"
    catalog.Games()["tgame"]["name"] = "x"
    catalog.Skins("Mario")[0]["data"]["assets"].clear()
    catalog.StageFromStartGGId(311)[1]["name"] = "x"

    assert _norm(snap.characters) == pristine
    assert catalog.CharacterAssets("mario", 0)["full"]["average_size"]["x"] \
        == 100.0
    assert catalog.Skins("Mario")[0]["data"]["assets"]
    assert catalog.StageFromStartGGId(311)[1]["name"] == "戦場"
    # The state got its own copy of the nested defaults.
    assert SM.Get(f"{PREFIX}.defaults") is not snap.game["defaults"]


def test_loads_never_mutate_the_scan(catalog):
    catalog.SelectGame("tgame")
    catalog.SelectGame("tgame", modsActive=True, reload=True)
    catalog.SelectGame("game2")

    assert _norm(catalog.Games()) == GOLDEN["games"]


def test_readers_never_see_two_games_mixed(catalog):
    """A reader takes one snapshot and works from it while loads swap."""
    expected = {
        codename: set(GOLDEN["scenarios"][label]["characters"])
        for label, (codename, _mods, _reload) in SCENARIOS.items()
        if not _mods}
    stop = threading.Event()
    errors = []

    def writer():
        try:
            for _ in range(4):
                for codename in ("tgame", "game2"):
                    catalog.SelectGame(codename, reload=True)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
        finally:
            stop.set()

    thread = threading.Thread(target=writer)
    thread.start()
    seen = set()
    while not stop.is_set():
        # Yield the GIL first -- also before the `continue` below -- or the
        # busy reader starves the writer.
        time.sleep(0.001)
        snap = catalog.Current()
        if snap.codename is None:
            continue
        seen.add(snap.codename)
        assert snap.game["codename"] == snap.codename
        assert set(snap.characters) == expected[snap.codename]
        assert set(snap.stockIconPaths) == expected[snap.codename]
        assert {data["en_name"] for _t, data in snap.characterRows} \
            <= expected[snap.codename]
        assert all(path.startswith(f"./user_data/games/{snap.codename}/")
                   for icons in snap.stockIconPaths.values()
                   for path in icons.values()
                   if "cancel.svg" not in path)
        payload = catalog.CharactersPayload()
        assert {item["en_name"] for item in payload.values()} \
            <= expected["tgame"] | expected["game2"]
    thread.join()

    assert errors == []
    assert seen  # not vacuous: the reader did see loaded snapshots


# ── locking rules ───────────────────────────────────────────────────────

def test_progress_runs_and_observers_run_outside_the_lock(catalog):
    calls = []
    lockedDuringNotify = []

    def onUpdate(_payload):
        lockedDuringNotify.append(catalog._lock.locked())

    SM.observers.state_updated.subscribe(onUpdate)
    try:
        catalog.SelectGame(
            "tgame", progress=lambda done, total: calls.append((done, total)))
    finally:
        SM.observers.state_updated.unsubscribe(onUpdate)

    characters = len(GOLDEN["scenarios"]["tgame"]["characters"])
    assert calls[0] == (0, characters * 2)
    assert calls[-1] == (characters * 2, characters * 2)
    # The state publish is inside the lock, but the save cycle -- and every
    # observer with it -- runs after the lock is released.
    assert lockedDuringNotify and not any(lockedDuringNotify)


# ── bugs fixed on the way (found while recording the golden) ────────────

def _kidGame(root, name, skins):
    game = root / "user_data" / "games" / name
    (game / "base_files" / "icon").mkdir(parents=True)
    (game / "base_files" / "config.json").write_text(json.dumps(
        {"name": name, "character_to_codename": {"Kid": {"codename": "kid"}}}))
    (game / "base_files" / "icon" / "config.json").write_text(json.dumps(
        {"type": ["icon"], "prefix": "", "postfix": "_"}))
    for number in skins:
        (game / "base_files" / "icon" / f"kid_{number}.png").write_bytes(b"")


def test_ten_or_more_skins_keep_every_skin(_isolated):
    """The old loader also wrote {skin: filename} into the filename list by
    index: from 10 skins on, the string-sorted list was overwritten in
    place, skins 8 and 9 vanished, and skin 8 resolved to skin 0's file."""
    _kidGame(_isolated, "many", range(12))
    catalog = _catalog()

    snap = catalog.SelectGame("many")

    assert sorted(snap.skins["Kid"]) == list(range(12))
    assert snap.assetIndex["kid"]["base_files/icon"] == sorted(
        f"kid_{n}.png" for n in range(12))
    for skin in (8, 9, 11):
        assert catalog.CharacterAssets("kid", skin)["base_files/icon"][
            "asset"].endswith(f"/kid_{skin}.png")


def test_a_gap_in_the_skin_numbers_still_loads(_isolated):
    """...and with a gap (skins 0 and 2) the same write raised IndexError,
    aborting the load after the selection had already switched."""
    _kidGame(_isolated, "gap", (0, 2))
    catalog = _catalog()

    snap = catalog.SelectGame("gap")

    assert snap is not None
    assert SM.Get(f"{PREFIX}.codename") == "gap"
    assert sorted(snap.skins["Kid"]) == [0, 2]
    assets = catalog.CharacterAssets("kid", 2)["base_files/icon"]["asset"]
    assert assets.endswith("/kid_2.png")
    # A missing skin falls back to skin 0, as before.
    assert catalog.CharacterAssets("kid", 1)["base_files/icon"][
        "asset"].endswith("/kid_0.png")


def test_a_broken_pack_is_skipped_and_the_scan_goes_on(_isolated):
    """One unreadable config.json used to raise out of the scan, so a single
    broken download left the index stale (or empty at startup). Now that
    game pack -- or, inside a game, that asset pack -- is logged and left
    out, and the index is replaced with everything that did read."""
    games = _isolated / "user_data" / "games"
    catalog = _catalog()
    assert sorted(catalog.GameCodenames()) == ["game2", "tgame"]

    (games / "game2" / "base_files" / "config.json").write_bytes(b"{nope")
    (games / "tgame" / "portrait" / "config.json").write_bytes(b"{")
    shapeless = games / "listy" / "base_files"
    shapeless.mkdir(parents=True)
    (shapeless / "config.json").write_bytes(b"[]")

    catalog.ScanGames()

    assert catalog.GameCodenames() == ["tgame"]
    tgame = _norm(catalog.Games()["tgame"])
    expected = deep_clone(GOLDEN["games"]["tgame"])
    del expected["assets"]["portrait"]
    assert tgame == expected
    # ...and the surviving game still loads.
    snap = catalog.SelectGame("tgame")
    assert snap is not None and snap.codename == "tgame"


def test_an_unreadable_start_gg_table_is_logged_not_raised(_isolated):
    os.remove("./user_data/characters.json")
    catalog = GameAssetCatalog()
    # No runtime copy and no ./assets seed in this directory.
    catalog.ReloadStartGGCharacters()
    assert catalog.CharacterFromStartGGId(1) is None


def test_the_snapshot_type_is_frozen():
    assert dataclasses.is_dataclass(GameAssetSnapshot)
    assert GameAssetSnapshot.__dataclass_params__.frozen
