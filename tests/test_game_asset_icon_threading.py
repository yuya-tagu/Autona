"""Icon workers of AutonaGameAssetManager stay off QPixmap / QIcon.

UpdateStageModel / UpdateSkinModel fan out to QThreadPool workers that decode
the stage and skin images. QPixmap (and the QIcon built on it) is GUI-thread
only per the Qt docs, so the workers must stop at QImage and leave the
QPixmap / QIcon conversion -- and the setIcon on the model item -- to the
queued completion slot on the GUI thread.

These tests drive the production rebuilds against a tiny synthetic game on
disk, with the module's QPixmap / QIcon swapped for spies that record which
thread built them. They also pin the generation check: a worker result that
lands after its model was rebuilt (retired) must not be applied.
"""
import threading

import pytest


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
    # workers and the completion slots resolve these names through.
    monkeypatch.setattr(module, "QPixmap", SpyPixmap)
    monkeypatch.setattr(module, "QIcon", SpyIcon)
    return log


def _png(path, color, size=(40, 30)):
    from PIL import Image
    Image.new("RGBA", size, color).save(path)


@pytest.fixture
def syntheticGame(autonaApp, monkeypatch, tmp_path):
    """Point the real asset manager at a one-character, two-stage game.

    Every attribute the rebuilds replace is registered with monkeypatch first,
    so the harness's own (empty) generation is put back after the test.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    manager = AutonaGameAssetManager.instance

    for name in ("characters", "skins", "selectedGame", "characterAssetIndex",
                 "biggestCompletePack", "skinModels", "stageModel",
                 "stageModelWithBlank", "workers"):
        monkeypatch.setattr(manager, name, getattr(manager, name, None),
                            raising=False)

    pack = tmp_path / "full"
    pack.mkdir()
    for skin, color in enumerate(("red", "blue")):
        _png(pack / f"mario_{skin}.png", color)
    stagePng = tmp_path / "battlefield.png"
    _png(stagePng, "green", size=(80, 45))
    # Exists but does not decode: the worker's fallback (cancel icon) path.
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")

    manager.characters = {
        "Mario": {"codename": "mario", "en_name": "Mario",
                  "name": "Mario", "display_name": "Mario"},
    }
    manager.skins = {"Mario": {0: True, 1: True}}
    manager.characterAssetIndex = {
        "mario": {"full": ["mario_0.png", "mario_1.png"]}}
    manager.biggestCompletePack = "full"
    manager.selectedGame = {
        "path": str(tmp_path),
        "assets": {"full": {"type": ["full"], "prefix": "", "postfix": "_"}},
        "stage_to_codename": {
            "battlefield": {"name": "Battlefield", "path": str(stagePng)},
            "broken": {"name": "Broken", "path": str(broken)},
        },
    }

    yield manager

    # No worker may outlive the test (and the harness CWD).
    manager.threadpool.waitForDone()
    # Results still queued on the GUI thread target this test's items, which
    # monkeypatch is about to drop; retire their generation so a later
    # processEvents() discards them instead of calling setIcon on them.
    manager._stageIconGeneration += 1
    manager._skinIconGeneration += 1


def _deliver(manager):
    """Finish the workers, then run their queued completion slots."""
    from qtpy.QtWidgets import QApplication

    manager.threadpool.waitForDone()
    QApplication.processEvents()


def _rows(model):
    return [model.item(row) for row in range(model.rowCount())]


def test_icon_workers_build_no_pixmap_off_the_gui_thread(
        syntheticGame, pixmapThreads):
    manager = syntheticGame
    assert threading.current_thread() is threading.main_thread()

    manager.UpdateSkinModel()
    manager.UpdateStageModel()
    _deliver(manager)

    assert pixmapThreads.offGui == []

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


def test_worker_payload_is_a_qimage(syntheticGame):
    from qtpy.QtGui import QImage, QStandardItem

    manager = syntheticGame
    stage = manager.selectedGame["stage_to_codename"]["battlefield"]
    item = QStandardItem()

    generation, returnedItem, image = manager.LoadStageImage(
        stage, item, 7, progress_callback=None, cancel_event=None)

    assert generation == 7
    assert returnedItem is item
    assert isinstance(image, QImage)
    # Scaled to 64px on the long edge, with the source pixels intact. (This
    # does not prove the image owns its buffer -- the copy() in the worker
    # cannot be observed deterministically from here.)
    assert (image.width(), image.height()) == (64, 36)
    assert image.pixelColor(10, 10).green() == 128

    allAssetData = [{"assets": manager.GetCharacterAssets("mario", skin)}
                    for skin in (0, 1)]
    allItem = [QStandardItem(), QStandardItem()]
    generation, pairs = manager.LoadSkinImages(
        allAssetData, allItem, None, 3,
        progress_callback=None, cancel_event=None)

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
    manager.threadpool.waitForDone()
    # ...when the models are rebuilt.
    manager.UpdateSkinModel()
    current = manager.skinModels["Mario"]
    _deliver(manager)

    assert current is not retired
    assert all(item.icon().isNull() for item in _rows(retired))
    assert all(not item.icon().isNull() for item in _rows(current))


def test_results_for_a_retired_stage_model_are_dropped(syntheticGame):
    manager = syntheticGame

    manager.UpdateStageModel()
    retired = manager.stageModel
    retiredWithBlank = manager.stageModelWithBlank
    manager.threadpool.waitForDone()
    manager.UpdateStageModel()
    _deliver(manager)

    assert manager.stageModel is not retired
    assert all(item.icon().isNull() for item in _rows(retired))
    assert all(item.icon().isNull() for item in _rows(retiredWithBlank))
    assert all(not item.icon().isNull() for item in _rows(manager.stageModel))
