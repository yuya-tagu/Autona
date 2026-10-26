"""Qt adapter of the game asset catalog (ADR-005 S1a).

The data -- games, characters, skins, stages, asset paths, the start.gg id
tables -- lives in the Qt-free `src/core/GameAssetCatalog.py`. This class only:

- turns the catalog's current snapshot into the QStandardItemModels the
  combos bind to, plus their icons (decoded on worker threads, applied on the
  GUI thread);
- runs the catalog's slow operations (scan, load, start.gg download) off the
  GUI thread and announces them through `signals.onLoadAssets` / `onLoad`;
- persists and restores the last selected game (SettingsManager).

The old attributes (`games`, `selectedGame`, `characters`, ...) are
read-only properties over the current snapshot. They hand out the catalog's
own dicts: treat them as read-only and copy before writing (into the state,
say).
"""
import os
import traceback

from loguru import logger
from PIL import Image
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from .core.GameAssetCatalog import CANCEL_ICON_PATH
from .core.SettingsManager import SettingsManager
from .core.StateManager import StateManager
from .Helpers import AutonaQtHelper
from .Workers import Worker


class AutonaGameAssetManagerSignals(QObject):
    onLoad = Signal()
    onLoadAssets = Signal()
    # The GameAssetSnapshot a load published, emitted just before onLoad
    # (bootstrap.wire_game_roster). Its own: a slot queued onto the GUI
    # thread from the loader's runs after the load is over, by when the
    # catalog's Current() can be a later load's.
    gameLoaded = Signal(object)


class AutonaGameAssetManager(QObject):
    instance: "AutonaGameAssetManager" = None

    def __init__(self, catalog, parent=None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.signals = AutonaGameAssetManagerSignals()

        # Memoised 32px stock-icon QIcons, keyed by (en_name, skin), for the
        # snapshot generation in _iconCacheGeneration. QIcon must be built
        # on the GUI thread; all callers run there.
        self._iconCache = {}
        self._iconCacheGeneration = None

        self.characterModel = QStandardItemModel()
        self.skinModels = {}
        self.variantModel = QStandardItemModel()
        self.colorModel = QStandardItemModel()
        self.stageModel = QStandardItemModel()
        self.stageModelWithBlank = QStandardItemModel()
        # The previous model generation, kept alive until the next rebuild
        # (agent-pitfalls #13). See _RebuildModels.
        self._retiredModels = None
        # The snapshot generation the models above were built from.
        self._modelGeneration = None

        self.assetsLoaderLock = QMutex()
        self.assetsLoaderThread = None
        self.threadpool = QThreadPool()
        self.workers = []
        # Generation of the stage / skin models the icon workers decode for.
        # Bumped whenever UpdateStageModel / UpdateSkinModel replace those
        # models; a worker result carrying an older value belongs to a retired
        # generation and is dropped instead of being applied to its items.
        self._stageIconGeneration = 0
        self._skinIconGeneration = 0

        # Startup restore of the last-used game (see _RestoreSelectedGame).
        # Armed by UiMounted() so a later LoadGames() rescan — "Add game
        # assets", asset guide — never overrides the user's current pick.
        self._pendingSelectedGameRestore = False
        # onLoad is emitted from the asset loader thread, so this queued
        # connection runs the settings write on the GUI thread.
        self.signals.onLoad.connect(self._PersistSelectedGame)

        self._RebuildModels()

    # ── read-only views of the current snapshot ─────────────────────────

    @property
    def games(self):
        return self.catalog.GamesView()

    @property
    def selectedGame(self):
        return self.catalog.Current().game

    @property
    def characters(self):
        return self.catalog.Current().characters

    @property
    def skins(self):
        return self.catalog.Current().skins

    @property
    def variants(self):
        return self.catalog.Current().variants

    @property
    def colors(self):
        return self.catalog.Current().colors

    @property
    def stages(self):
        return self.catalog.Current().stages

    @property
    def biggestCompletePack(self):
        return self.catalog.Current().biggestCompletePack

    @property
    def has_modded_content(self):
        return self.catalog.Current().hasModdedContent

    # ── startup / scan ──────────────────────────────────────────────────

    def UiMounted(self):
        self.DownloadStartGGCharacters()
        self._pendingSelectedGameRestore = True
        self.signals.onLoadAssets.connect(self._RestoreSelectedGame)
        self.LoadGames()

    def _PersistSelectedGame(self):
        # The selected game used to live only in memory (reset every launch)
        # and was re-derived solely from the Start.gg videogame id of the
        # restored tournament. That made the sidebar game pulldown depend on
        # a live network round trip: blank for the first seconds of every
        # launch, and blank for the whole session when offline / when the
        # fetch failed / with no tournament. Persist the pick locally
        # instead. StateManager is not usable as the restore source —
        # bootstrap clears `game`, and out/program_state.json is a
        # regenerable export, not user settings.
        SettingsManager.Set(
            "SELECTED_GAME_CODENAME",
            StateManager.Get("game.codename", "") or "")

    def _RestoreSelectedGame(self):
        # Runs on the GUI thread (queued from the GameLoaderThread emit), by
        # which point the catalog holds the local user_data/games scan.
        if not self._pendingSelectedGameRestore:
            return
        self._pendingSelectedGameRestore = False

        codename = SettingsManager.Get("SELECTED_GAME_CODENAME", "") or ""
        if not codename:
            return

        if codename not in self.catalog.GameCodenames():
            logger.info(
                f"Last used game '{codename}' is not installed anymore - "
                "skipping restore")
            return

        # Mods stay off on restore, unchanged from the previous startup
        # behaviour: the sidebar checkbox re-syncs itself from
        # game.mods_active on onLoad.
        self.LoadGameByCodename(codename)

    def DownloadStartGGCharacters(self):
        class DownloaderThread(QThread):
            def run(self):
                self.parent().catalog.DownloadStartGGCharacters()
        thread = DownloaderThread(self)
        thread.start()

    def LoadGames(self):
        class GameLoaderThread(QThread):
            def run(self):
                manager = self.parent()
                try:
                    manager.catalog.ScanGames()
                except Exception:
                    logger.error(traceback.format_exc())
                    return
                manager.signals.onLoadAssets.emit()

        gameLoaderThread = GameLoaderThread(self)
        gameLoaderThread.start()

    # ── game load ───────────────────────────────────────────────────────

    def SetGameFromStartGGId(self, gameid):
        codename = self.catalog.GameFromStartGGId(gameid)
        if codename:
            self.LoadGameByCodename(codename)

    def LoadGameAssets(self, game: int = 0, async_mode=True, mods_active=False, mods_reload_mode=False):
        """Load the game at 1-based position `game` in the scan.

        0 / None / out of range means "no game" (the tournament URL was
        cleared), which keeps whatever is loaded (see SelectGame)."""
        codenames = self.catalog.GameCodenames()
        codename = ""
        if isinstance(game, int) and 1 <= game <= len(codenames):
            codename = codenames[game - 1]
        self.LoadGameByCodename(codename, async_mode=async_mode,
                                mods_active=mods_active,
                                mods_reload_mode=mods_reload_mode)

    def LoadGameByCodename(self, codename, async_mode=True, mods_active=False, mods_reload_mode=False, done=None):
        """Load `codename`, on the loader thread unless `async_mode` is off.

        `done()`, if given, is called once the attempt is over however it
        went -- loaded, skipped by the catalog (an unknown codename, e.g. one
        a rescan dropped meanwhile; the same game without reload) or failed
        -- on the loader thread for an async load. onLoad fires only for a
        load that happened, so whoever holds something until "the load is
        over" (the /api/v1 in-progress flag) releases it here.
        """
        if not codename:
            if done is not None:
                done()
            return

        class AssetsLoaderThread(QThread):
            def __init__(self, parent=...) -> None:
                super().__init__(parent)
                self.codename = None
                self.lock = None
                self.mods_active = False
                self.mods_reload_mode = False
                self.done = None

            def run(self):
                from .Helpers.AutonaStatusManager import AutonaStatusManager
                manager = self.parent()
                status = {}
                self.lock.lock()
                try:
                    # QPixmap/QIcon/QImage must be built on the GUI thread;
                    # the model rebuild is marshalled there to avoid the
                    # intermittent boot SIGSEGVs seen when run from this
                    # worker thread.
                    manager._Load(
                        self.codename, self.mods_active, self.mods_reload_mode,
                        lambda fn: AutonaQtHelper.gui_executor.run_sync(fn),
                        manager._StatusProgress(self.codename, status))
                except Exception:
                    logger.error(traceback.format_exc())
                finally:
                    try:
                        manager.threadpool.waitForDone()
                        self.lock.unlock()
                        AutonaStatusManager.pop(status.get("token"))
                    finally:
                        if self.done is not None:
                            self.done()

        if async_mode:
            self.assetsLoaderThread = AssetsLoaderThread(self)
            self.assetsLoaderThread.codename = codename
            self.assetsLoaderThread.lock = self.assetsLoaderLock
            self.assetsLoaderThread.mods_active = mods_active
            self.assetsLoaderThread.mods_reload_mode = mods_reload_mode
            self.assetsLoaderThread.done = done
            self.assetsLoaderThread.start(QThread.Priority.HighestPriority)
        else:
            try:
                # Synchronous path already runs on the main (GUI) thread.
                self._Load(codename, mods_active, mods_reload_mode,
                           lambda fn: fn())
            except Exception:
                logger.error(traceback.format_exc())
            finally:
                if done is not None:
                    done()

    def _Load(self, codename, mods_active, mods_reload_mode, run_models,
              progress=None):
        # Shared body of the threaded and the synchronous load. `run_models`
        # runs the model rebuild on the GUI thread (marshalled by the
        # worker, inline on the sync path). One SaveBlock around the whole
        # load: a switch is a single state cycle. The catalog's writer lock
        # is released by the time run_models runs (SelectGame returned), so
        # the GUI thread is never waited on while holding it.
        self.catalog.ReloadStartGGCharacters()
        with StateManager.SaveBlock():
            snapshot = self.catalog.SelectGame(
                codename, modsActive=mods_active, reload=mods_reload_mode,
                progress=progress)
            if snapshot is None:
                return
            run_models(self._RebuildModels)
            self.signals.gameLoaded.emit(snapshot)
            self.signals.onLoad.emit()

    @staticmethod
    def _StatusProgress(codename, status):
        # Footer progress for the threaded load. Called by the catalog under
        # its writer lock: AutonaStatusManager only emits a (queued) signal,
        # so this never blocks. The status is pushed on the first call, so
        # a load the catalog skips shows nothing.
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        message = f"ゲームアセットを読み込み中... ({codename})"

        def progress(done, total):
            if status.get("token") is None:
                status["token"] = AutonaStatusManager.push(message)
            # Throttled to every 5 characters to avoid signal spam.
            if done > 0 and (done % 5 == 0 or done >= total):
                AutonaStatusManager.update(
                    status["token"], message, progress=done, total=total)
        return progress

    # ── models ──────────────────────────────────────────────────────────

    @staticmethod
    def _HasGame(snapshot):
        # False only for the snapshot before any load (and after
        # ResetGameState): its models are empty, without the blank first
        # rows, exactly as before the first load.
        return bool(snapshot.codename or snapshot.characters or snapshot.stages
                    or snapshot.variants or snapshot.colors)

    def _RebuildModels(self):
        """Rebuild every model from the catalog's current snapshot.

        Reads Current() rather than taking the snapshot of the load that
        asked for it, so overlapping loads converge on the latest one, and
        skips when the models already belong to that generation. GUI thread
        only (QPixmap / QIcon).
        """
        snapshot = self.catalog.Current()
        if snapshot.generation == self._modelGeneration:
            return
        # Retire the previous model generation instead of letting it be
        # garbage-collected mid-swap: player-widget combos and proxies stay
        # attached to the old models until their onLoad handlers
        # (ReloadCharacters etc.) rebind AFTER this batch, and deleting a C++
        # model under a live view/proxy is an access-violation crash (same
        # class as the AutonaPlayerDB.SetupModel boot crash, 2026-07-11).
        self._retiredModels = [
            self.characterModel, self.skinModels, self.variantModel,
            self.colorModel, self.stageModel, self.stageModelWithBlank]
        self._modelGeneration = snapshot.generation
        self.UpdateCharacterModel(snapshot)
        self.UpdateSkinModel(snapshot)
        self.UpdateVariantModel(snapshot)
        self.UpdateColorModel(snapshot)
        self.UpdateStageModel(snapshot)

    def UpdateStageModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self._stageIconGeneration += 1
            generation = self._stageIconGeneration
            self.stageModel = QStandardItemModel()
            self.stageModelWithBlank = QStandardItemModel()
            if not self._HasGame(snapshot):
                return
            # Add blank
            item = QStandardItem("")
            item.setData({}, Qt.ItemDataRole.UserRole)
            self.stageModelWithBlank.appendRow(item)

            # The catalog orders both lists: config order for the stage
            # combo, sorted for the with-blank one.
            for model, rows in ((self.stageModel, snapshot.stageRows),
                                (self.stageModelWithBlank,
                                 snapshot.stageRowsSorted)):
                for text, stage in rows:
                    item = QStandardItem(text)
                    item.setData(stage, Qt.ItemDataRole.UserRole)
                    model.appendRow(item)

                    worker = Worker(self.LoadStageImage, *[stage, item, generation])
                    # Queued: the worker stops at QImage; the QPixmap / QIcon
                    # conversion and setIcon run in the slot on the GUI thread.
                    worker.signals.result.connect(
                        self.LoadStageImageComplete, Qt.ConnectionType.QueuedConnection)
                    self.threadpool.start(worker)
        except Exception:
            logger.error(traceback.format_exc())

    def LoadStageImage(self, stage, item, generation, progress_callback, cancel_event):
        # Runs on a QThreadPool worker. QPixmap / QIcon are GUI-thread only, so
        # this stops at a QImage; LoadStageImageComplete converts it on the GUI
        # thread. A None image asks the slot for the fallback (cancel) icon.
        try:
            if stage.get("path") and os.path.exists(stage.get("path")):
                img = Image.open(stage.get("path"))

                resizeMultiplier = 1

                if img.width > img.height:
                    resizeMultiplier = 64/img.width
                else:
                    resizeMultiplier = 64/img.height

                img = img.resize((int(img.width*resizeMultiplier),
                                 int(img.height*resizeMultiplier)), Image.BILINEAR)

                img = img.convert("RGBA")
                data = img.tobytes("raw", "RGBA")
                qimg = QImage(
                    data, img.size[0], img.size[1], QImage.Format.Format_RGBA8888)

                # copy(): the QImage above only wraps `data`, which does not
                # outlive this call; the GUI thread gets one owning its pixels.
                return (generation, item, qimg.copy())
        except Exception:
            logger.error(traceback.format_exc())
            return (generation, item, None)

    def LoadStageImageComplete(self, result):
        # GUI thread (queued from the LoadStageImage worker).
        try:
            if result is None:
                return
            generation, item, image = result
            if generation != self._stageIconGeneration:
                # The stage models were rebuilt while this worker ran: its
                # item belongs to a retired generation.
                return
            if image is None:
                icon = QIcon(QPixmap(CANCEL_ICON_PATH).scaled(32, 32))
            else:
                icon = QIcon(QPixmap.fromImage(image))
            if not icon.isNull():
                item.setIcon(icon)
        except Exception:
            logger.error(traceback.format_exc())
            return (None)

    def GetStockIcon(self, en_name, skin=0):
        # Shared, memoised 32px stock-icon QIcon for a character/skin, from
        # the current snapshot. QIcon / QPixmap must be built on the GUI
        # thread; all callers (AutonaPlayerDB.SetupModel, the player browser)
        # run there. Returns an empty QIcon when no path exists.
        return self._StockIcon(self.catalog.Current(), en_name, skin)

    def _StockIcon(self, snapshot, en_name, skin):
        # The icon comes from `snapshot`, so a model built from one snapshot
        # never mixes in another game's icon when a load lands meanwhile.
        # The cache follows the snapshot generation.
        if snapshot.generation != self._iconCacheGeneration:
            self._iconCache = {}
            self._iconCacheGeneration = snapshot.generation
        cache_key = (en_name, skin)
        icon = self._iconCache.get(cache_key)
        if icon is None:
            path = snapshot.stockIconPaths.get(en_name, {}).get(skin)
            if path is None:
                return QIcon()
            icon = QIcon(QPixmap.fromImage(QImage(path).scaledToWidth(
                32, Qt.TransformationMode.SmoothTransformation)))
            self._iconCache[cache_key] = icon
        return icon

    def UpdateCharacterModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.characterModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.characterModel.appendRow(item)

            # Rows come in display order, modded ones already left out while
            # mods are off (GameAssetCatalog).
            for text, data in snapshot.characterRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)
                item.setIcon(self._StockIcon(snapshot, data.get("en_name"), 0))
                item.setData(data, Qt.ItemDataRole.UserRole)
                self.characterModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateColorModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.colorModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.colorModel.appendRow(item)

            for text, data in snapshot.colorRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)

                try:
                    icon = QPixmap(100, 100)
                    icon.fill(QColor("#" + data.get("value")))
                    item.setIcon(QIcon(icon))
                except Exception:
                    # A colour without a value: the row stays, iconless.
                    logger.error(traceback.format_exc())

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.colorModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateVariantModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.variantModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.variantModel.appendRow(item)

            for text, data in snapshot.variantRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)

                if data.get("icon_path"):
                    item.setIcon(QIcon(QPixmap.fromImage(QImage(data["icon_path"]))))
                else:
                    item.setIcon(QIcon(QPixmap.fromImage(QImage(CANCEL_ICON_PATH))))

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.variantModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateSkinModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        self.skinModels = {}
        self._skinIconGeneration += 1
        generation = self._skinIconGeneration

        self.workers = []

        for key, rows in snapshot.skinRows.items():
            skinModel = QStandardItemModel()

            allAssetData = []
            allItem = []

            for _skin, text, assetData in rows:
                item = QStandardItem()
                skinModel.appendRow(item)
                item.setData(text, Qt.ItemDataRole.EditRole)
                item.setData(assetData, Qt.ItemDataRole.UserRole)

                allItem.append(item)
                allAssetData.append(assetData)

            # The pack comes from the snapshot the rows were built from, not
            # from Current() inside the worker (a later load may have landed).
            worker = Worker(self.LoadSkinImages, *
                            [allAssetData, allItem, skinModel, generation,
                             snapshot.biggestCompletePack])
            # Queued: the worker stops at QImage; the QPixmap / QIcon
            # conversion and setIcon run in the slot on the GUI thread.
            worker.signals.result.connect(
                self.LoadSkinImagesComplete, Qt.ConnectionType.QueuedConnection)
            self.workers.append(worker)

            self.skinModels[key] = skinModel

        for w in self.workers:
            self.threadpool.start(w)

    def LoadSkinImages(self, allAssetData, allItem, skinModel, generation, biggestCompletePack, progress_callback, cancel_event):
        # Runs on a QThreadPool worker: stops at QImage (see LoadStageImage);
        # LoadSkinImagesComplete builds the icons on the GUI thread.
        # skinModel is unused: passed only to keep the model alive while the worker runs.
        # A None image (no usable asset, or one that failed to decode) asks
        # the slot for the fallback (cancel) icon, per skin. The old code
        # opened cancel.svg with PIL, which cannot read SVG, so one
        # asset-less skin dropped the icons of the whole character.
        images = []

        for i in range(len(allAssetData)):
            assetData = allAssetData[i]
            try:
                # Set to use first asset as a fallback
                key = biggestCompletePack
                asset = None

                if assetData["assets"].get(key):
                    asset = assetData["assets"][key]
                elif assetData["assets"].get("full"):
                    asset = assetData["assets"]["full"]
                elif assetData["assets"].get("base_files/icon"):
                    asset = assetData["assets"]["base_files/icon"]

                if not asset or not asset.get("asset"):
                    images.append(None)
                    continue

                img = Image.open(asset["asset"])

                targetW = 128
                targetH = 96

                originalW = img.width
                originalH = img.height

                proportional_zoom = 1

                if asset.get("average_size"):
                    proportional_zoom = 0
                    proportional_zoom = max(
                        proportional_zoom,
                        (targetW / asset.get("average_size", {}).get("x", 0)) * 1.2
                    )
                    proportional_zoom = max(
                        proportional_zoom,
                        (targetH / asset.get("average_size", {}).get("y", 0)) * 1.2
                    )

                # For cropped assets, zoom to fill
                # Calculate max zoom
                zoom_x = targetW / originalW
                zoom_y = targetH / originalH

                minZoom = 1
                rescalingFactor = 1
                customZoom = 1

                if asset.get("rescaling_factor"):
                    rescalingFactor = asset.get("rescaling_factor")

                uncropped_edge = asset.get("uncropped_edge", [])

                if not uncropped_edge or len(uncropped_edge) == 0:
                    if zoom_x > zoom_y:
                        minZoom = zoom_x
                    else:
                        minZoom = zoom_y
                else:
                    if (
                        "u" in uncropped_edge and
                        "d" in uncropped_edge and
                        "l" in uncropped_edge and
                        "r" in uncropped_edge
                    ):
                        if "icon" in asset.get("type", []):
                            customZoom = 0.5 # Add zoom for icons to fit
                        else:
                            customZoom = 1.2  # Add zoom in for uncropped assets
                        minZoom = customZoom * proportional_zoom * rescalingFactor
                    elif (
                        not "l" in uncropped_edge and
                        not "r" in uncropped_edge
                    ):
                        minZoom = zoom_x
                    elif (
                        not "u" in uncropped_edge and
                        not "d" in uncropped_edge
                    ):
                        minZoom = zoom_y
                    else:
                        minZoom = customZoom * proportional_zoom * rescalingFactor

                zoom = max(minZoom, customZoom * minZoom)

                # Centering
                xx = 0
                yy = 0

                eyesight = asset.get("eyesight")

                if not eyesight:
                    eyesight = {
                        "x": originalW / 2,
                        "y": originalH / 2
                    }

                xx = -eyesight["x"] * zoom + targetW / 2

                maxMoveX = targetW - originalW * zoom

                if not uncropped_edge or not "l" in uncropped_edge:
                    if (xx > 0):
                        xx = 0

                if not uncropped_edge or not "r" in uncropped_edge:
                    if (xx < maxMoveX):
                        xx = maxMoveX

                yy = -eyesight["y"] * zoom + targetH / 2

                maxMoveY = targetH - originalH * zoom

                if not uncropped_edge or not "u" in uncropped_edge:
                    if (yy > 0):
                        yy = 0

                if not uncropped_edge or not "d" in uncropped_edge:
                    if (yy < maxMoveY):
                        yy = maxMoveY

                img = img.resize(
                    (int(originalW*zoom), int(originalH*zoom)), Image.BILINEAR)
                img = img.crop((
                    int(-xx),
                    int(-yy),
                    int(-xx+128),
                    int(-yy+96)
                ))

                img = img.convert("RGBA")
                data = img.tobytes("raw", "RGBA")
                qimg = QImage(
                    data, img.size[0], img.size[1], QImage.Format.Format_RGBA8888)

                # copy(): own the pixels, `data` does not outlive this call.
                images.append(qimg.copy())
            except Exception:
                logger.error(traceback.format_exc())
                images.append(None)

        return (generation, [(allItem[i], images[i]) for i in range(len(allItem))])

    def LoadSkinImagesComplete(self, result):
        # GUI thread (queued from the LoadSkinImages worker).
        try:
            if result is None:
                return
            generation, pairs = result
            if generation != self._skinIconGeneration:
                # The skin models were rebuilt while this worker ran: its
                # items belong to a retired generation.
                return
            fallback = None
            for item, image in pairs:
                if image is None:
                    if fallback is None:
                        fallback = QIcon(
                            QPixmap(CANCEL_ICON_PATH).scaled(32, 32))
                    icon = fallback
                else:
                    icon = QIcon(QPixmap.fromImage(image))
                if not icon.isNull():
                    item.setIcon(icon)
        except Exception:
            logger.error(traceback.format_exc())
            return (None)

    # ── lookups (delegated to the catalog) ──────────────────────────────

    def GetCharacterAssets(self, characterCodename: str, skin: int, assetpack: str = None):
        return self.catalog.CharacterAssets(characterCodename, skin, assetpack)
