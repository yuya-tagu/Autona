"""Icon decodes of AutonaGameAssetManager stay off QPixmap / QIcon.

UpdateStageModel / UpdateSkinModel fan out decodes of the stage and skin
images to the TaskExecutor's fan-out lane (ADR-005 S3f-4; QThreadPool workers
until then). QPixmap (and the QIcon built on it) is GUI-thread only per the
Qt docs, so a decode must stop at QImage and leave the QPixmap / QIcon
conversion -- and the setIcon on the model item -- to the completion it
hands to the GUI thread.

These tests drive the production rebuilds against a tiny synthetic game on
disk, with the module's QPixmap / QIcon swapped for spies that record which
thread built them. They also pin the generation check: a decode result that
lands after its model was rebuilt (retired) must not be applied.
"""
import json
import threading

import pytest

from tests.conftest import deliver_decodes, remove_tree


class _ThreadLog:
    def __init__(self):
        self.offGui = []
        self.onGui = 0

    def record(self, what):
        if threading.current_thread() is threading.main_thread():
            self.onGui += 1
        else:
            self.offGui.append((what, threading.current_thread().name))


@pytest.fixture
def pixmapThreads(monkeypatch):
    """Record the thread of every QPixmap / QIcon the asset manager builds."""
    from qtpy.QtGui import QIcon, QPixmap

    import src.AutonaGameAssetManager as module

    log = _ThreadLog()

    class SpyPixmap(QPixmap):
        def __init__(self, *args):
            log.record("QPixmap()")
            super().__init__(*args)

        @staticmethod
        def fromImage(*args, **kwargs):
            log.record("QPixmap.fromImage")
            return QPixmap.fromImage(*args, **kwargs)

    class SpyIcon(QIcon):
        def __init__(self, *args):
            log.record("QIcon()")
            super().__init__(*args)

    # The module does `from qtpy.QtGui import *`, so its globals are what the
    # decodes and the completions resolve these names through.
    monkeypatch.setattr(module, "QPixmap", SpyPixmap)
    monkeypatch.setattr(module, "QIcon", SpyIcon)
    return log


def _png(path, color, size=(40, 30)):
    from PIL import Image
    Image.new("RGBA", size, color).save(path)


ICON_GAME = "icon_game"


@pytest.fixture
def syntheticGame(autonaApp):
    """Install and load a one-character, two-stage game.

    Written into the harness's runtime user_data/games and loaded through the
    catalog plus the adapter's model rebuild, like a real switch minus the
    onLoad broadcast. Uninstalled afterwards, back to the no-game models.
    """
    from qtpy.QtWidgets import QApplication

    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.GameAssetCatalog import GameAssetCatalog

    manager = AutonaGameAssetManager.instance
    catalog = GameAssetCatalog.instance

    game = autonaApp.dir / "user_data" / "games" / ICON_GAME
    (game / "base_files").mkdir(parents=True)
    (game / "base_files" / "config.json").write_text(json.dumps({
        "name": "Icon Game",
        "character_to_codename": {"Mario": {"codename": "mario"}},
        "stage_to_codename": {
            "Battlefield": {"codename": "battlefield"},
            "Broken": {"codename": "broken"},
        },
    }))
    pack = game / "full"
    pack.mkdir()
    (pack / "config.json").write_text(json.dumps(
        {"type": ["full"], "prefix": "", "postfix": "_"}))
    for skin, color in enumerate(("red", "blue")):
        _png(pack / f"mario_{skin}.png", color)
    stages = game / "stage_icon"
    stages.mkdir()
    (stages / "config.json").write_text(json.dumps(
        {"type": ["stage_icon"], "prefix": "", "postfix": ""}))
    _png(stages / "battlefield.png", "green", size=(80, 45))
    # Exists but does not decode: the decode's fallback (cancel icon) path.
    (stages / "broken.png").write_bytes(b"not an image")

    catalog.ScanGames()
    catalog.SelectGame(ICON_GAME, reload=True)
    deliver_decodes(manager._RebuildModels())

    yield manager

    # No decode may outlive the test (and the harness CWD).
    _finishDecodes()
    # Results still queued on the GUI thread target this test's items;
    # retire their generation so a later processEvents() discards them
    # instead of calling setIcon on them.
    manager._stageIconGeneration += 1
    manager._skinIconGeneration += 1
    QApplication.processEvents()
    remove_tree(game)
    catalog.ScanGames()
    catalog.ResetGameState()
    manager._RebuildModels()


def _finishDecodes():
    """Wait for every decode, without running what they handed to the GUI
    thread (their icons stay queued there)."""
    from src.core.TaskExecutor import TaskExecutor

    assert TaskExecutor.instance.drain(30), "an icon decode did not finish"


def _rows(model):
    return [model.item(row) for row in range(model.rowCount())]


def test_icon_decodes_build_no_pixmap_off_the_gui_thread(
        syntheticGame, pixmapThreads, monkeypatch):
    manager = syntheticGame
    assert threading.current_thread() is threading.main_thread()
    decodeThreads = []
    for name in ("LoadSkinImages", "LoadStageImage"):
        def spy(*args, _decode=getattr(manager, name)):
            decodeThreads.append(threading.current_thread().name)
            return _decode(*args)
        monkeypatch.setattr(manager, name, spy)

    decodes = manager.UpdateSkinModel() + manager.UpdateStageModel()
    deliver_decodes(decodes)

    assert pixmapThreads.offGui == []
    # One decode per character and one per stage row of each of the two
    # stage models (1 + 2 * 2), each on the fan-out lane.
    assert len(decodes) == len(decodeThreads) == 5
    assert all(name.startswith("AutonaFanout") for name in decodeThreads)

    # Not vacuous: the icons were built (on the GUI thread) and applied.
    assert pixmapThreads.onGui > 0
    skins = _rows(manager.skinModels["Mario"])
    assert len(skins) == 2
    assert all(not item.icon().isNull() for item in skins)

    stages = _rows(manager.stageModel)
    assert {item.text() for item in stages} == {"Battlefield", "Broken"}
    # The undecodable stage falls back to the cancel icon, now also GUI-side.
    assert all(not item.icon().isNull() for item in stages)
    # The blank row has no image; the two stage rows do.
    withBlank = _rows(manager.stageModelWithBlank)
    assert [item.icon().isNull() for item in withBlank].count(False) == 2


def test_decode_payload_is_a_qimage(syntheticGame):
    from qtpy.QtGui import QImage, QStandardItem

    manager = syntheticGame
    stage = manager.stages["Battlefield"]
    item = QStandardItem()

    generation, returnedItem, image = manager.LoadStageImage(stage, item, 7)

    assert generation == 7
    assert returnedItem is item
    assert isinstance(image, QImage)
    # Scaled to 64px on the long edge, with the source pixels intact. (This
    # does not prove the image owns its buffer -- the copy() in the decode
    # cannot be observed deterministically from here.)
    assert (image.width(), image.height()) == (64, 36)
    assert image.pixelColor(10, 10).green() == 128

    allAssetData = [{"assets": manager.GetCharacterAssets("mario", skin)}
                    for skin in (0, 1)]
    allItem = [QStandardItem(), QStandardItem()]
    generation, pairs = manager.LoadSkinImages(
        allAssetData, allItem, None, 3,
        manager.catalog.Current().biggestCompletePack)

    assert generation == 3
    assert [pair[0] for pair in pairs] == allItem
    assert all(isinstance(pair[1], QImage) for pair in pairs)
    assert all((pair[1].width(), pair[1].height()) == (128, 96)
               for pair in pairs)


def test_results_for_a_retired_skin_model_are_dropped(syntheticGame):
    manager = syntheticGame

    manager.UpdateSkinModel()
    retired = manager.skinModels["Mario"]
    # Its results are now queued on the GUI thread but not yet delivered...
    _finishDecodes()
    # ...when the models are rebuilt.
    decodes = manager.UpdateSkinModel()
    current = manager.skinModels["Mario"]
    deliver_decodes(decodes)

    assert current is not retired
    assert all(item.icon().isNull() for item in _rows(retired))
    assert all(not item.icon().isNull() for item in _rows(current))


def test_results_for_a_retired_stage_model_are_dropped(syntheticGame):
    manager = syntheticGame

    manager.UpdateStageModel()
    retired = manager.stageModel
    retiredWithBlank = manager.stageModelWithBlank
    _finishDecodes()
    deliver_decodes(manager.UpdateStageModel())

    assert manager.stageModel is not retired
    assert all(item.icon().isNull() for item in _rows(retired))
    assert all(item.icon().isNull() for item in _rows(retiredWithBlank))
    assert all(not item.icon().isNull() for item in _rows(manager.stageModel))


def test_a_skin_without_an_asset_gets_the_fallback_icon(syntheticGame):
    """One asset-less skin used to drop the icons of the whole character:
    the decode opened cancel.svg with PIL (which cannot read SVG) and the
    resulting exception discarded every image of that character."""
    from qtpy.QtGui import QImage, QStandardItem

    manager = syntheticGame
    allAssetData = [{"assets": {}},
                    {"assets": manager.GetCharacterAssets("mario", 1)}]
    allItem = [QStandardItem(), QStandardItem()]

    generation, pairs = manager.LoadSkinImages(
        allAssetData, allItem, None, manager._skinIconGeneration,
        manager.catalog.Current().biggestCompletePack)

    assert pairs[0][1] is None
    assert isinstance(pairs[1][1], QImage)

    # The GUI-thread completion gives the asset-less skin the cancel icon.
    manager.LoadSkinImagesComplete((generation, pairs))
    assert all(not item.icon().isNull() for item in allItem)
