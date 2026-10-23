"""The Qt adapter's models, against the pre-S1a recording (ADR-005 S1a).

Since S1a AutonaGameAssetManager builds its QStandardItemModels from the
catalog's snapshot rows instead of sorting its own items. These tests load
the golden fixture games through the production synchronous path (the one
WebServerActions.set_game takes) and read the models back the way the
recording did -- DisplayRole text plus UserRole data, row by row -- and the
payloads through the WebServerActions readers. Those walked the models (on
the GUI thread) until S1b; they read the catalog now, on whatever thread
asks, and the same recording pins that their output did not move.

They also pin the two lifetime rules of the rebuild: every model of the
previous generation, the per-game tracker's stage model included, is retired
rather than dropped (agent-pitfalls #13), and a tracker row that outlives a
rebuild is rebound without writing the state.

The last block drives the threaded load -- the path every pick in the UI
takes -- and what hangs off it: the footer status, the persisted pick and
its restore at startup, and forgetting the pick when the URL is cleared.
"""
import ast
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from qtpy.QtCore import Qt

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_assets"
GOLDEN = json.loads((FIXTURE / "golden.json").read_text(encoding="utf-8"))
GAMES = [p.name for p in (FIXTURE / "games").iterdir() if p.is_dir()]


def _rows(model):
    return [[model.item(row).data(Qt.ItemDataRole.DisplayRole),
             model.item(row).data(Qt.ItemDataRole.UserRole)]
            for row in range(model.rowCount())]


@pytest.fixture
def goldenGames(autonaApp, monkeypatch):
    """The fixture games installed into the harness, under the recording's
    locale. Uninstalled afterwards, back to the no-game models."""
    from qtpy.QtWidgets import QApplication

    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    locale = GOLDEN["locale"]
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", locale["program"])
    monkeypatch.setattr(AutonaLocaleHelper, "exportLocale", locale["export"])
    monkeypatch.setattr(AutonaLocaleHelper, "remapping", locale["remapping"])

    games = autonaApp.dir / "user_data" / "games"
    for name in GAMES:
        shutil.copytree(FIXTURE / "games" / name, games / name)
    manager = AutonaGameAssetManager.instance
    catalog = GameAssetCatalog.instance
    catalog.ScanGames()
    # A real load ends in onLoad, which persists the pick.
    persisted = SettingsManager.Get("SELECTED_GAME_CODENAME", "")

    yield manager

    manager.threadpool.waitForDone()
    QApplication.processEvents()
    for name in GAMES:
        shutil.rmtree(games / name, ignore_errors=True)
    catalog.ScanGames()
    catalog.ResetGameState()
    manager._RebuildModels()
    SettingsManager.Set("SELECTED_GAME_CODENAME", persisted)
    autonaApp.settle()


def _load(manager, codename, modsActive=False, reload=False):
    from qtpy.QtWidgets import QApplication

    manager.LoadGameByCodename(codename, async_mode=False,
                               mods_active=modsActive, mods_reload_mode=reload)
    manager.threadpool.waitForDone()
    QApplication.processEvents()


SCENARIOS = {
    "tgame": ("tgame", False, False),
    "tgame_mods": ("tgame", True, True),
    "game2": ("game2", False, False),
}


@pytest.mark.parametrize("label", list(SCENARIOS))
def test_the_models_match_the_recording(goldenGames, autonaApp, label):
    manager = goldenGames
    codename, modsActive, reload = SCENARIOS[label]
    golden = GOLDEN["scenarios"][label]

    _load(manager, codename, modsActive, reload)

    models = golden["models"]
    assert _rows(manager.characterModel) == models["character"]
    assert _rows(manager.variantModel) == models["variant"]
    assert _rows(manager.colorModel) == models["color"]
    assert _rows(manager.stageModel) == models["stage"]
    assert _rows(manager.stageModelWithBlank) == models["stageWithBlank"]
    assert {en: _rows(model) for en, model in manager.skinModels.items()} \
        == models["skins"]
    assert autonaApp.get("game") == golden["state"]

    # The Web readers (catalog-backed since S1b): what they send must not
    # move.
    actions = autonaApp.actions()
    for name, actual in (("characters", actions.get_characters()),
                         ("variants", actions.get_variants()),
                         ("games", actions.get_games())):
        assert actual == golden["payloads"][name], name
        assert list(actual) == list(golden["payloads"][name]), name
        # Byte for byte as Socket.IO sends it (SocketioJson = json.dumps,
        # insertion order), nested key order included.
        assert json.dumps(actual) == json.dumps(golden["payloads"][name]), name


# ── the Web readers off the GUI thread (ADR-005 S1b) ────────────────────

# WebServerActions method -> golden payload name.
WEB_READERS = {
    "get_games": "games",
    "get_characters": "characters",
    "get_variants": "variants",
}


def test_the_web_readers_read_the_catalog_without_a_gui_hop():
    """S1's completion condition (docs/changes/architecture-modernization.md
    §4.4): get_games / get_characters / get_variants carry no
    @gui_thread_sync, and read the catalog rather than the Qt models."""
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "src" / "AutonaWebServerActions.py").read_text(
        encoding="utf-8"))
    actionsClass = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef)
                        and node.name == "WebServerActions")
    methods = {node.name: node for node in actionsClass.body
               if isinstance(node, ast.FunctionDef)}

    for name in WEB_READERS:
        decorators = [ast.unparse(d) for d in methods[name].decorator_list]
        assert not any("gui_thread_sync" in d for d in decorators), \
            f"{name} hops to the GUI thread again: {decorators}"
        body = ast.unparse(methods[name])
        assert "GameAssetCatalog.instance" in body, name
        assert "AutonaGameAssetManager" not in body, name


def test_the_web_readers_answer_on_a_worker_thread(goldenGames, autonaApp):
    """The way the Flask / Socket.IO threads call them -- and with this (the
    GUI) thread NOT spinning the event loop meanwhile. A reader that still
    marshalled onto the GUI thread would wait in run_sync until the join
    below times out."""
    _load(goldenGames, "tgame")
    actions = autonaApp.actions()
    results = {}

    def work():
        for name in WEB_READERS:
            results[name] = getattr(actions, name)()

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "a reader waited for the GUI thread"
    payloads = GOLDEN["scenarios"]["tgame"]["payloads"]
    for name, payload in WEB_READERS.items():
        assert json.dumps(results[name]) == json.dumps(payloads[payload]), name


def test_every_model_of_the_previous_generation_is_retired(goldenGames):
    manager = goldenGames
    _load(manager, "tgame")
    previous = [manager.characterModel, manager.skinModels,
                manager.variantModel, manager.colorModel, manager.stageModel,
                manager.stageModelWithBlank]

    _load(manager, "game2")

    # Six, the tracker's stage model included: a combo still bound to one of
    # them until its onLoad handler rebinds must not see it deleted.
    assert len(manager._retiredModels) == 6
    assert all(old is kept
               for old, kept in zip(previous, manager._retiredModels))
    assert manager.stageModelWithBlank is not previous[-1]


def test_reloading_the_same_generation_rebuilds_nothing(goldenGames):
    manager = goldenGames
    _load(manager, "tgame")
    model = manager.characterModel

    # Same game, no reload: the catalog skips, so the models stay.
    _load(manager, "tgame")
    assert manager.characterModel is model
    # An explicit rebuild of the generation already shown is a no-op too.
    manager._RebuildModels()
    assert manager.characterModel is model


def test_a_model_takes_icons_and_pack_from_its_own_snapshot(goldenGames):
    """Rows, stock icons and the skin icons' pack all come from the snapshot
    the model is built from -- not from Current(), which a load landing
    meanwhile may already have moved to another game."""
    from src.core.GameAssetCatalog import GameAssetCatalog

    manager = goldenGames
    _load(manager, "tgame")
    tgame = GameAssetCatalog.instance.Current()
    _load(manager, "game2")
    assert GameAssetCatalog.instance.Current().biggestCompletePack \
        != tgame.biggestCompletePack

    manager.UpdateSkinModel(tgame)
    manager.UpdateCharacterModel(tgame)

    assert manager.workers
    assert all(worker.args[4] == tgame.biggestCompletePack
               for worker in manager.workers)
    model = manager.characterModel
    mario = next(
        row for row in range(model.rowCount())
        if (model.item(row).data(Qt.ItemDataRole.UserRole) or {}).get(
            "en_name") == "Mario")
    # game2 has no Mario: an icon looked up in Current() would be empty.
    assert not model.item(mario).icon().isNull()


def test_a_tracker_row_is_rebound_to_the_new_stage_model(
        goldenGames, autonaApp, monkeypatch):
    from qtpy.QtWidgets import QComboBox
    from src.core.SettingsManager import SettingsManager

    # The tracker ships disabled. Patch the lookup rather than the
    # process-global setting (as tests/test_scoreboard_operations.py does).
    realGet = SettingsManager.Get
    monkeypatch.setattr(SettingsManager, "Get", staticmethod(
        lambda key, default=None:
            False if key == "general.disable_individual_game_tracker"
            else realGet(key, default)))
    manager = goldenGames
    _load(manager, "tgame")
    tracker = autonaApp.board(1).individualGameTracker
    tracker.SetStageCount(3)
    combos = [row.findChild(QComboBox, f"stageMenu_{index}")
              for index, row in enumerate(tracker.stage_widget_list)]
    assert combos and all(c is not None for c in combos)
    # Pick a stage on the first row, the way an operator does.
    combos[0].setCurrentIndex(combos[0].findText("Final Destination"))
    autonaApp.settle()
    stages = autonaApp.get("score.1.stages")
    revision = autonaApp.state.revision

    # New stage models without the board's row rebuild (which normally
    # follows on the same onLoad).
    manager.UpdateStageModel()
    assert all(c.model() is not manager.stageModelWithBlank for c in combos)

    tracker.RebindStageModel()
    autonaApp.settle()

    assert all(c.model() is manager.stageModelWithBlank for c in combos)
    assert combos[0].currentText() == "Final Destination"
    # Not a selection: nothing was written.
    assert autonaApp.get("score.1.stages") == stages
    assert autonaApp.state.revision == revision


# ── the threaded load ───────────────────────────────────────────────────

LOADER_TIMEOUT_SECS = 30


def _waitForLoader(manager):
    """Wait for the asset loader thread while pumping this (GUI) thread.

    The loader marshals the model rebuild onto the GUI thread (run_sync),
    so blocking in QThread.wait() here would deadlock it."""
    from qtpy.QtWidgets import QApplication

    thread = manager.assetsLoaderThread
    deadline = time.monotonic() + LOADER_TIMEOUT_SECS
    while not thread.isFinished():
        assert time.monotonic() < deadline, "the asset loader did not finish"
        QApplication.processEvents()
        time.sleep(0.005)
    manager.threadpool.waitForDone()
    # What the thread queued on its way out: onLoad's receivers here.
    QApplication.processEvents()


@pytest.fixture
def statusCalls(monkeypatch):
    """AutonaStatusManager's footer calls, recorded and passed through."""
    from src.Helpers.AutonaStatusManager import AutonaStatusManager

    calls = []
    realPush = AutonaStatusManager.push
    realUpdate = AutonaStatusManager.update
    realPop = AutonaStatusManager.pop

    def push(message, progress=0, total=0):
        token = realPush(message, progress, total)
        calls.append(("push", token, message))
        return token

    def update(token, message, progress=0, total=0):
        calls.append(("update", token, message))
        realUpdate(token, message, progress=progress, total=total)

    def pop(token):
        calls.append(("pop", token, None))
        realPop(token)

    monkeypatch.setattr(AutonaStatusManager, "push", staticmethod(push))
    monkeypatch.setattr(AutonaStatusManager, "update", staticmethod(update))
    monkeypatch.setattr(AutonaStatusManager, "pop", staticmethod(pop))
    return calls


@pytest.fixture
def onLoadCount(goldenGames):
    """How many times signals.onLoad fired (from whichever thread)."""
    emitted = []

    def count():
        emitted.append(1)

    goldenGames.signals.onLoad.connect(count)
    yield emitted
    goldenGames.signals.onLoad.disconnect(count)


def _loaderStatus(calls, codename):
    return [token for kind, token, message in calls
            if kind == "push" and f"({codename})" in message]


def test_a_threaded_load_emits_on_load_once_and_clears_its_status(
        goldenGames, autonaApp, statusCalls, onLoadCount):
    from src.Helpers.AutonaStatusManager import AutonaStatusManager

    manager = goldenGames
    manager.LoadGameByCodename("tgame", async_mode=True)
    _waitForLoader(manager)
    autonaApp.settle()

    assert len(onLoadCount) == 1
    assert autonaApp.get("game.codename") == "tgame"
    # Rebuilt on the GUI thread for this load.
    assert _rows(manager.characterModel) \
        == GOLDEN["scenarios"]["tgame"]["models"]["character"]

    tokens = _loaderStatus(statusCalls, "tgame")
    assert len(tokens) == 1
    token = tokens[0]
    assert ("pop", token, None) in statusCalls
    pops = [i for i, call in enumerate(statusCalls) if call[:2] == ("pop", token)]
    updates = [i for i, call in enumerate(statusCalls)
               if call[:2] == ("update", token)]
    # The last progress tick lands before the pop, and the footer is clear.
    assert updates and max(updates) < pops[0]
    assert token not in AutonaStatusManager._stack


def test_a_skipped_threaded_load_shows_no_status(
        goldenGames, autonaApp, statusCalls, onLoadCount):
    manager = goldenGames
    _load(manager, "tgame")
    statusCalls.clear()
    onLoadCount.clear()

    # Same game, no reload: the catalog skips it.
    manager.LoadGameByCodename("tgame", async_mode=True)
    _waitForLoader(manager)
    autonaApp.settle()

    assert onLoadCount == []
    # Nothing pushed; the loader's unconditional pop(None) is a no-op.
    assert [call for call in statusCalls if call[0] != "pop"] == []
    assert all(token is None for _kind, token, _m in statusCalls)


def test_the_last_pick_is_persisted_and_restored(goldenGames, autonaApp):
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager

    manager = goldenGames
    _load(manager, "game2")
    autonaApp.settle()
    # onLoad -> _PersistSelectedGame.
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == "game2"

    # Next launch: bootstrap clears the game, UiMounted arms the restore and
    # the scan's onLoadAssets runs it.
    GameAssetCatalog.instance.ResetGameState()
    manager._RebuildModels()
    autonaApp.settle()
    assert autonaApp.get("game") == {}
    manager._pendingSelectedGameRestore = True
    manager._RestoreSelectedGame()
    _waitForLoader(manager)
    autonaApp.settle()

    assert autonaApp.get("game.codename") == "game2"
    assert manager.selectedGame["codename"] == "game2"
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == "game2"

    # Armed once: a later rescan ("Add game assets") keeps the user's pick.
    thread = manager.assetsLoaderThread
    manager._RestoreSelectedGame()
    assert manager.assetsLoaderThread is thread


def test_a_last_pick_that_is_no_longer_installed_is_not_restored(
        goldenGames, autonaApp):
    from src.core.SettingsManager import SettingsManager

    manager = goldenGames
    SettingsManager.Set("SELECTED_GAME_CODENAME", "uninstalled")
    thread = manager.assetsLoaderThread

    manager._pendingSelectedGameRestore = True
    manager._RestoreSelectedGame()

    assert manager.assetsLoaderThread is thread
    assert manager._pendingSelectedGameRestore is False
    assert not autonaApp.get("game.codename")


def test_clearing_the_tournament_url_forgets_the_pick(goldenGames):
    from src.core.GameAssetCatalog import GameAssetCatalog

    # Source-level half: Window cannot be built in this harness (importing
    # src/Autona.py is the startup sequence -- agent-pitfalls #1).
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "src" / "Autona.py").read_text(encoding="utf-8"))
    handler = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "Signal_GameChange")
    cleared = next(node for node in ast.walk(handler)
                   if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "url == ''")
    calls = {ast.unparse(node.func)
             for statement in cleared.body for node in ast.walk(statement)
             if isinstance(node, ast.Call)}
    assert "GameAssetCatalog.instance.ForgetSelection" in calls, (
        "Signal_GameChange('') no longer forgets the selected game; picking "
        "the same game again after clearing the URL would be skipped")
    assert "selectedGame" not in ast.unparse(handler), (
        "the adapter's selectedGame is a read-only view since S1a")

    # Runtime half: what that call does to the adapter.
    manager = goldenGames
    _load(manager, "tgame")
    model = manager.characterModel

    GameAssetCatalog.instance.ForgetSelection()

    assert manager.selectedGame == {}
    # Same assets, same generation: the combos keep their rows.
    manager._RebuildModels()
    assert manager.characterModel is model
    # ...and picking the same game again is a load, not a skip.
    _load(manager, "tgame")
    assert manager.selectedGame["codename"] == "tgame"
    assert manager.characterModel is not model
