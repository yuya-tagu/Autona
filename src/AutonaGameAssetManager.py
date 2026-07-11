import os
import orjson
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from .StateManager import StateManager
import re
import traceback
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers import AutonaQtHelper
from .Workers import Worker
from PIL import Image
from loguru import logger
import glob
import shutil

import requests


# characters.json is shipped as a tracked seed in assets/ but is refreshed from
# the start.gg API at every startup. Writing the runtime copy back into the
# tracked seed dirties the working tree and risks committing API data (see
# known-smells S-15). The runtime download now targets user_data/ (gitignored),
# and reads prefer that copy, falling back to the tracked seed for a fresh clone.
CHARACTERS_SEED_PATH = './assets/characters.json'
CHARACTERS_RUNTIME_PATH = './user_data/characters.json'


def _resolve_characters_path() -> str:
    if os.path.exists(CHARACTERS_RUNTIME_PATH):
        return CHARACTERS_RUNTIME_PATH
    return CHARACTERS_SEED_PATH


class AutonaGameAssetManagerSignals(QObject):
    onLoad = Signal()
    onLoadAssets = Signal()


class AutonaGameAssetManager(QObject):
    instance: "AutonaGameAssetManager" = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.signals = AutonaGameAssetManagerSignals()
        self.games = {}
        self.characters = {}
        self.variants = {}
        self.colors = []
        self.selectedGame = {}
        self.stockIcons = {}
        self.startgg_id_to_character = {}

        # Single source of truth for asset file discovery, built once per game
        # load in _LoadGameAssetsCore and read (no filesystem access) by
        # GetCharacterAssets / variant lookups. Prevents the per-(char,skin)
        # os.listdir storm that froze the GUI thread on boot.
        # characterAssetIndex: {codename: {assetKey: {skin: filename}}}
        self.characterAssetIndex = {}
        # variantIconIndex: {variant_codename: {"path": str, "image_size": any}}
        self.variantIconIndex = {}
        # Memoised 32px stock-icon QIcons, keyed by (en_name, skin). QIcon must
        # be built on the GUI thread; all callers run there. Cleared on load.
        self._iconCache = {}

        self.characterModel = QStandardItemModel()
        self.skinModels = {}
        self.variantModel = QStandardItemModel()
        self.colorModel = QStandardItemModel()
        self.stageModel = QStandardItemModel()
        self.stageModelWithBlank = QStandardItemModel()

        StateManager.Set(f"game", {})
        self.assetsLoaderLock = QMutex()
        self.assetsLoaderThread = None
        self.thumbnailSettingsLoaded = False
        self.threadpool = QThreadPool()
        self.workers = []

        self.skinLoaderLock = QMutex()

    def UiMounted(self):
        self.DownloadStartGGCharacters()
        self.LoadGames()

    def DownloadStartGGCharacters(self):
        class DownloaderThread(QThread):
            def run(self):
                tmp_path = CHARACTERS_RUNTIME_PATH + '.tmp'
                try:
                    url = 'https://api.start.gg/characters'
                    r = requests.get(url, allow_redirects=True)
                    r_json = orjson.dumps(orjson.loads(
                        r.text), option=orjson.OPT_INDENT_2)

                    # Write runtime copy into user_data/ (gitignored), never the
                    # tracked seed in assets/ (known-smells S-15)
                    os.makedirs(
                        os.path.dirname(CHARACTERS_RUNTIME_PATH), exist_ok=True)
                    open(tmp_path, 'wb').write(r_json)

                    try:
                        # Test if downloaded JSON is valid
                        orjson.loads(open(tmp_path, 'rb').read())

                        # Atomically overwrite the runtime copy (os.replace works
                        # whether or not the target already exists)
                        os.replace(tmp_path, CHARACTERS_RUNTIME_PATH)

                        logger.info("startgg characters file updated")
                    except:
                        logger.error("Characters file download failed")
                except Exception as e:
                    logger.error(
                        "Could not update " + CHARACTERS_RUNTIME_PATH + ": " + str(e))
        thread = DownloaderThread(self)
        thread.start()

    def LoadGames(self):
        class GameLoaderThread(QThread):
            def run(self):
                with StateManager.SaveBlock():
                    self.parent().games = {}

                    gameDirs = os.listdir("./user_data/games/")

                    for game in gameDirs:
                        if os.path.isfile("./user_data/games/"+game+"/base_files/config.json"):
                            with open("./user_data/games/"+game +
                                      "/base_files/config.json", "rb") as f:
                                self.parent().games[game] = orjson.loads(f.read())

                            # Try logo_small, if it doesn't exist use logo
                            # Store path only — QPixmap/QIcon must be created on the main thread
                            if os.path.isfile("./user_data/games/"+game+"/base_files/logo_small.png"):
                                self.parent().games[game]["logo_path"] = \
                                    "./user_data/games/"+game+"/base_files/logo_small.png"
                            elif os.path.isfile("./user_data/games/"+game+"/base_files/logo.png"):
                                self.parent().games[game]["logo_path"] = \
                                    "./user_data/games/"+game+"/base_files/logo.png"

                            self.parent().games[game]["assets"] = {}
                            self.parent(
                            ).games[game]["path"] = "./user_data/games/"+game+"/"

                            assetDirs = os.listdir("./user_data/games/"+game)
                            assetDirs += ["base_files/" +
                                          f for f in os.listdir("./user_data/games/"+game+"/base_files/")]

                            for dir in assetDirs:
                                if os.path.isdir("./user_data/games/"+game+"/"+dir):
                                    if os.path.isfile("./user_data/games/"+game+"/"+dir+"/config.json"):
                                        logger.info(
                                            "Found asset config for ["+game+"]["+dir+"]")
                                        with open("./user_data/games/"+game+"/"+dir +
                                                  "/config.json", "rb") as f:
                                            self.parent().games[game]["assets"][dir] = \
                                                orjson.loads(f.read())
                                    else:
                                        logger.error(
                                            "No config file for "+game+" - "+dir)

                            # Load translated names
                            # Translate game name
                            locale = AutonaLocaleHelper.programLocale
                            if locale.replace('-', '_') in self.parent().games[game].get("locale", {}):
                                game_name = self.parent(
                                ).games[game]["locale"][locale.replace('-', '_')].get("name")
                                if game_name:
                                    self.parent(
                                    ).games[game]["name"] = game_name
                            elif locale.split('-')[0] in self.parent().games[game].get("locale", {}):
                                game_name = self.parent(
                                ).games[game]["locale"][locale.split('-')[0]].get("name")
                                if game_name:
                                    self.parent(
                                    ).games[game]["name"] = game_name
                        else:
                            logger.info("Game config for "+game+" doesn't exist.")
                    # print(self.parent().games)
                    self.parent().signals.onLoadAssets.emit()

        gameLoaderThread = GameLoaderThread(self)
        gameLoaderThread.start()

    def SetGameFromStartGGId(self, gameid):
        def detect_smashgg_id_match(game, id):
            result = str(game.get("smashgg_game_id", "")) == str(id)
            if not result:
                alternates = game.get("alternate_versions", [])
                alternates_ids = []
                for alternate in alternates:
                    if alternate.get("smashgg_game_id"):
                        alternates_ids.append(
                            str(alternate.get("smashgg_game_id")))
                result = str(id) in alternates_ids
            return (result)

        if len(self.games.keys()) == 0:
            return

        for i, game in enumerate(self.games.values()):
            if detect_smashgg_id_match(game, gameid):
                self.LoadGameAssets(i+1)
                break

    def CopyCSS(self, game):
        # Make dir if doesn't exists
        css_dir_path = "./out/css"
        if not os.path.isdir(css_dir_path):
            os.mkdir(css_dir_path)

        # Empty dir and remove all CSS
        try:
            list_current_files = glob.glob(f"{css_dir_path}/*.css")
        except OSError as e:
            logger.error(f"Error listing CSS files in {css_dir_path}: {e}")
            list_current_files = []
        for file_path in list_current_files:
            try:
                os.remove(file_path)
            except OSError as e:
                logger.error(f"Error removing CSS file {file_path}: {e}")

        # Copy game CSS files
        game_css_path = f"./user_data/games/{game}/base_files/css"
        if os.path.isdir(game_css_path):
            try:
                list_game_css_files = glob.glob(f"{game_css_path}/*.css")
            except OSError as e:
                logger.error(f"Error listing CSS files in {game_css_path}: {e}")
                list_game_css_files = []
            for file_path in list_game_css_files:
                logger.info("Copying CSS file: "+file_path)
                try:
                    shutil.copy(file_path, css_dir_path)
                except OSError as e:
                    logger.error(f"Error copying CSS file {file_path}: {e}")

        logger.info("Game CSS file copy complete")

    def _ResolveTargetGame(self, game, mods_reload_mode):
        # Resolve the integer game index to the codename to load. Returns
        # None when the load should be skipped: no games available, or the
        # requested game is already selected and we are not reloading mods.
        if len(self.games.keys()) == 0:
            return None
        if game == 0 or game == None:
            game = ""
        else:
            game = list(self.games.keys())[game-1]
        if game == self.selectedGame.get("codename") and (not mods_reload_mode):
            return None
        return game

    def _LoadGameAssetsCore(self, game, mods_active, run_models):
        # Shared asset-loading body for the threaded (startup) and the
        # synchronous (game/mode switch) paths. `game` is the resolved
        # codename; `run_models` runs the model-rebuild closure (marshalled
        # onto the GUI thread by the worker, inline on the sync path). All
        # state writes share one SaveBlock so a switch is a single file write.
        logger.info("Changed to game: "+game)

        self.CopyCSS(game)

        # Reset per-game indices/caches so a game or mods switch rebuilds them
        # against the new asset set.
        self.characterAssetIndex = {}
        self.variantIconIndex = {}
        self._iconCache = {}

        gameObj = self.games.get(game, {})
        self.selectedGame = gameObj
        gameObj["codename"] = game

        if gameObj != None:
            self.characters = gameObj.get("character_to_codename", {})
            self.variants = gameObj.get("variant_to_codename", {})
            self.colors = gameObj.get("preset_colors", [])

            assetsKey = ""
            if len(list(gameObj.get("assets", {}).keys())) > 0:
                assetsKey = list(gameObj.get(
                    "assets", {}).keys())[0]

            for asset in list(gameObj.get("assets", {}).keys()):
                if "icon" in gameObj["assets"][asset].get("type", ""):
                    assetsKey = asset
                    break

            assetsObj = gameObj.get(
                "assets", {}).get(assetsKey, None)
            files = sorted(os.listdir(
                './user_data/games/'+game+'/'+assetsKey))

            self.stockIcons = {}

            for c in self.characters.keys():
                self.stockIcons[c] = {}

                filteredFiles = \
                    [f for f in files if f.startswith(assetsObj.get(
                        "prefix", "")+self.characters[c].get("codename")+assetsObj.get("postfix", ""))]

                if len(filteredFiles) == 0:
                    # Store path only — QImage must be created on the main thread
                    self.stockIcons[c][0] = './assets/icons/cancel.svg'

                for i, f in enumerate(filteredFiles):
                    numberStart = f.rfind(
                        assetsObj.get("postfix", "")) + len(assetsObj.get("postfix", ""))
                    numberEnd = f.rfind(".")
                    number = 0
                    try:
                        number = int(f[numberStart:numberEnd])
                    except:
                        logger.error(f)
                        pass
                    # Store path only — QImage must be created on the main thread
                    self.stockIcons[c][number] = \
                        './user_data/games/'+game+'/'+assetsKey+'/'+f

            logger.info("Loaded stock icons")

            self.skins = {}

            packSkinMask = {}

            widths = {}
            heights = {}

            # List each asset pack directory once (the path depends only on the
            # pack, not the character/skin), then index every file by its
            # parsed skin number. This is the single scan that feeds
            # characterAssetIndex; GetCharacterAssets reads it without touching
            # the filesystem again.
            packFiles = {}
            for assetsKey in list(gameObj["assets"].keys()):
                try:
                    packFiles[assetsKey] = sorted(os.listdir(
                        './user_data/games/'+game+'/'+assetsKey))
                except OSError:
                    packFiles[assetsKey] = []

            for c in self.characters.keys():
                self.skins[c] = {}
                codename = self.characters[c].get("codename")
                for assetsKey in list(gameObj["assets"].keys()):
                    asset = gameObj["assets"][assetsKey]

                    filteredFiles = \
                        [f for f in packFiles[assetsKey] if f.startswith(asset.get(
                            "prefix", "")+codename+asset.get("postfix", ""))]

                    # Store the filtered filename list verbatim so
                    # GetCharacterAssets parses skin numbers with its own logic
                    # (identical behaviour, just no filesystem access).
                    self.characterAssetIndex.setdefault(
                        codename, {})[assetsKey] = filteredFiles

                    for f in filteredFiles:
                        numberStart = f.rfind(
                            asset.get("postfix", "")) + len(asset.get("postfix", ""))
                        numberEnd = f.rfind(".")
                        number = 0
                        try:
                            number = int(f[numberStart:numberEnd])
                        except:
                            pass
                        self.skins[c][number] = True

                        if c not in packSkinMask:
                            packSkinMask[c] = {}

                        if assetsKey not in packSkinMask[c]:
                            packSkinMask[c][assetsKey] = set()

                        packSkinMask[c][assetsKey].add(number)

                        # Single source of truth: codename -> pack -> skin -> filename
                        self.characterAssetIndex.setdefault(
                            codename, {}).setdefault(assetsKey, {})[number] = f

                logger.info("Character "+c+" has " +
                            str(len(self.skins[c]))+" skins")

            # Set average size
            for assetsKey in list(gameObj.get("assets", {}).keys()):
                if assetsKey != "base_files" and assetsKey not in ["stage_icon", "variant_icon"]:
                    try:
                        if len(widths.get(assetsKey, [])) > 0 and len(heights.get(assetsKey, [])) > 0:
                            gameObj["assets"][assetsKey]["average_size"] = assetsObj.get("average_size")
                    except:
                        logger.error(traceback.format_exc())

            # Set complete
            for assetsKey in list(gameObj.get("assets", {}).keys()):
                try:
                    complete = True

                    for c in self.characters.keys():
                        if "random" in c.lower():
                            continue
                        for skin in self.skins[c].keys():
                            if assetsKey not in packSkinMask[c] or skin not in packSkinMask[c][assetsKey]:
                                complete = False
                                break

                    gameObj["assets"][assetsKey]["complete"] = complete
                except:
                    logger.error(traceback.format_exc())

            # Get biggest complete pack
            assetsKey = "base_files/icon"
            biggestAverage = 0

            for asset in list(gameObj.get("assets", {}).keys()):
                if gameObj["assets"][asset].get("complete") and gameObj["assets"][asset].get("average_size") and asset not in ["stage_icon", "variant_icon"]:
                    size = sum(gameObj["assets"][asset].get(
                        "average_size").values())

                    if size > biggestAverage:
                        assetsKey = asset
                        biggestAverage = size

            self.biggestCompletePack = assetsKey
            logger.info("Biggest complete assets: " + assetsKey)

            # Get stage icon
            assetsKey = None

            for asset in list(gameObj.get("assets", {}).keys()):
                if "stage_icon" in gameObj["assets"][asset].get("type", ""):
                    assetsKey = asset
                    break

            self.stages = gameObj.get("stage_to_codename", {})

            if assetsKey:
                assetsObj = gameObj.get(
                    "assets", {}).get(assetsKey)
                files = sorted(os.listdir(
                    './user_data/games/'+game+'/'+assetsKey))

                for stage in self.stages:
                    self.stages[stage]["path"] = './user_data/games/'+game+'/'+assetsKey+'/'+assetsObj.get(
                        "prefix", "")+self.stages[stage].get("codename", "")+assetsObj.get("postfix", "")+".png"

            for s in self.stages.keys():
                self.stages[s]["name"] = s

            # Build the variant icon index once: resolve each variant's icon
            # path + image_size from a single config read and a single listdir,
            # so UpdateVariantModel / GetVariantIconPath / GetVariantIconSize
            # never re-open variant_icon/config.json or stat files per item.
            self.variantIconIndex = {}
            variant_icon_dir = './user_data/games/'+game+'/variant_icon'
            variant_icon_config_path = variant_icon_dir+'/config.json'
            if self.variants and os.path.isfile(variant_icon_config_path):
                try:
                    with open(variant_icon_config_path, "rt", encoding="utf-8") as f:
                        variant_icon_config = orjson.loads(f.read())
                    variant_files = set(os.listdir(variant_icon_dir))
                    prefix = variant_icon_config.get("prefix")
                    postfix = variant_icon_config.get("postfix")
                    image_sizes = variant_icon_config.get("image_sizes", {})
                    extensions = ["png", "jpg", "gif", "webp"]
                    codenames = {self.variants[v].get(
                        "codename") for v in self.variants}
                    for variant_codename in codenames:
                        if variant_codename is None:
                            continue
                        base = f"{prefix}{variant_codename}{postfix}"
                        icon_path = ""
                        for extension in extensions:
                            if f"{base}.{extension}" in variant_files:
                                icon_path = f"{variant_icon_dir}/{base}.{extension}"
                                break
                        # image_size mirrors old GetVariantIconSize: only when a
                        # .png exists, read image_sizes[codename]["null"].
                        image_size = None
                        if f"{base}.png" in variant_files:
                            image_size = image_sizes.get(
                                variant_codename, {}).get("null")
                        self.variantIconIndex[variant_codename] = {
                            "path": icon_path,
                            "image_size": image_size,
                        }
                except:
                    logger.error(traceback.format_exc())

            # Load translations
            try:
                for c in self.characters.keys():
                    display_name = c
                    export_name = c
                    en_name = c

                    if self.characters[c].get("locale"):
                        locale = AutonaLocaleHelper.programLocale
                        if locale.replace("-", "_") in self.characters[c]["locale"]:
                            display_name = self.characters[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.characters[c]["locale"]:
                            display_name = self.characters[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.programLocale) in self.characters[c]["locale"]:
                            display_name = self.characters[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.programLocale)]

                        locale = AutonaLocaleHelper.exportLocale
                        if locale.replace("-", "_") in self.characters[c]["locale"]:
                            export_name = self.characters[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.characters[c]["locale"]:
                            export_name = self.characters[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale) in self.characters[c]["locale"]:
                            export_name = self.characters[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.exportLocale)]

                    self.characters[c]["display_name"] = display_name
                    self.characters[c]["export_name"] = export_name
                    self.characters[c]["en_name"] = en_name
            except:
                logger.error(traceback.format_exc())

            
            # Load translations for variants
            try:
                for c in self.variants.keys():
                    display_name = c
                    export_name = c
                    en_name = c

                    if self.variants[c].get("locale"):
                        locale = AutonaLocaleHelper.programLocale
                        if locale.replace("-", "_") in self.variants[c]["locale"]:
                            display_name = self.variants[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.variants[c]["locale"]:
                            display_name = self.variants[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.programLocale) in self.variants[c]["locale"]:
                            display_name = self.variants[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.programLocale)]

                        locale = AutonaLocaleHelper.exportLocale
                        if locale.replace("-", "_") in self.variants[c]["locale"]:
                            export_name = self.variants[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.variants[c]["locale"]:
                            export_name = self.variants[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale) in self.variants[c]["locale"]:
                            export_name = self.variants[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.exportLocale)]

                    self.variants[c]["display_name"] = display_name
                    self.variants[c]["export_name"] = export_name
                    self.variants[c]["en_name"] = en_name
            except:
                logger.error(traceback.format_exc())

            # Load translations for colors
            try:
                for c in range(len(self.colors)):
                    display_name = self.colors[c].get("name")
                    export_name = self.colors[c].get("name")
                    en_name = self.colors[c].get("name")

                    if self.colors[c].get("locale"):
                        locale = AutonaLocaleHelper.programLocale
                        if locale.replace("-", "_") in self.colors[c]["locale"]:
                            display_name = self.colors[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.colors[c]["locale"]:
                            display_name = self.colors[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.programLocale) in self.colors[c]["locale"]:
                            display_name = self.colors[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.programLocale)]

                        locale = AutonaLocaleHelper.exportLocale
                        if locale.replace("-", "_") in self.colors[c]["locale"]:
                            export_name = self.colors[
                                c]["locale"][locale.replace("-", "_")]
                        elif re.split("-|_", locale)[0] in self.colors[c]["locale"]:
                            export_name = self.colors[
                                c]["locale"][re.split("-|_", locale)[0]]
                        elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale) in self.colors[c]["locale"]:
                            export_name = self.colors[c]["locale"][AutonaLocaleHelper.GetRemaps(
                                AutonaLocaleHelper.exportLocale)]

                    self.colors[c]["display_name"] = display_name
                    self.colors[c]["export_name"] = export_name
                    self.colors[c]["en_name"] = en_name
            except:
                logger.error(traceback.format_exc())

        with StateManager.SaveBlock():
            StateManager.Set(f"game", {
                "name": self.selectedGame.get("name"),
                "abbreviation": self.selectedGame.get("abbreviation"),
                "smashgg_id": self.selectedGame.get("smashgg_game_id"),
                "codename": self.selectedGame.get("codename"),
                "logo": self.selectedGame.get("path", "")+"/base_files/logo.png",
                "defaults": self.selectedGame.get("defaults"),
                "mods_active": mods_active,
                "has_stages": bool(self.selectedGame.get("stage_to_codename")),
                "has_variants": bool(self.selectedGame.get("variant_to_codename")),
                "has_colors": bool(self.selectedGame.get("preset_colors"))
            })

            # Model rebuilds construct QPixmap / QIcon / QImage,
            # which Qt requires be created on the GUI thread.
            # Calling them directly from this worker thread is
            # the cause of intermittent SIGSEGVs at boot ("ssbu
            # game change → Loaded stock icons → Character X has
            # Y skins → silent crash"). Marshal the whole
            # rebuild batch to the main thread synchronously so
            # the worker waits for it to finish before
            # continuing.
            def _rebuild_models():
                # Retire the previous model generation instead of letting it
                # be garbage-collected mid-swap: player-widget combos and
                # proxies stay attached to the old models until their onLoad
                # handlers (ReloadCharacters etc.) rebind AFTER this batch,
                # and deleting a C++ model under a live view/proxy is an
                # access-violation crash (same class as the
                # AutonaPlayerDB.SetupModel boot crash, 2026-07-11).
                self._retiredModels = [
                    getattr(self, name, None)
                    for name in ("characterModel", "skinModels",
                                 "variantModel", "colorModel", "stageModel")
                ]
                self.has_modded_content = False
                self.UpdateCharacterModel(mods_active)
                self.UpdateSkinModel()
                self.UpdateVariantModel()
                self.UpdateColorModel()
                self.UpdateStageModel(mods_active)
            run_models(_rebuild_models)

            StateManager.Set(f"game.has_modded_content", self.has_modded_content)

            self.signals.onLoad.emit()

    def LoadGameAssets(self, game: int = 0, async_mode=True, mods_active=False, mods_reload_mode=False):
        class AssetsLoaderThread(QThread):
            def __init__(self, parent=...) -> None:
                super().__init__(parent)
                self.game = None
                self.lock = None
                self.mods_active = False
                self.mods_reload_mode = False

            def run(self):
                from .Helpers.AutonaStatusManager import AutonaStatusManager
                status_token = None
                manager = self.parent()
                self.lock.lock()
                try:
                    game = manager._ResolveTargetGame(self.game, self.mods_reload_mode)
                    if game is None:
                        return
                    status_token = AutonaStatusManager.push(
                        f"ゲームアセットを読み込み中... ({game})")
                    # QPixmap/QIcon/QImage must be built on the GUI thread;
                    # marshal the rebuild there to avoid intermittent boot
                    # SIGSEGVs seen when run from this worker thread.
                    manager._LoadGameAssetsCore(
                        game, self.mods_active,
                        lambda fn: AutonaQtHelper.gui_executor.run_sync(fn))
                except:
                    logger.error(traceback.format_exc())
                finally:
                    manager.threadpool.waitForDone()
                    self.lock.unlock()
                    AutonaStatusManager.pop(status_token)

        class AssetsLoader():
            def __init__(self, parent=...) -> None:
                self.parent = parent
                self.game = None

            def run(self, mods_active=False, mods_reload_mode=False):
                manager = self.parent
                try:
                    game = manager._ResolveTargetGame(self.game, mods_reload_mode)
                    if game is None:
                        return
                    # Synchronous path already runs on the main (GUI) thread.
                    manager._LoadGameAssetsCore(game, mods_active, lambda fn: fn())
                except:
                    logger.error(traceback.format_exc())

        self.thumbnailSettingsLoaded = False
        if async_mode:
            self.assetsLoaderThread = AssetsLoaderThread(
                AutonaGameAssetManager.instance)
            self.assetsLoaderThread.game = game
            self.assetsLoaderThread.lock = self.assetsLoaderLock
            self.assetsLoaderThread.mods_active = mods_active
            self.assetsLoaderThread.mods_reload_mode = mods_reload_mode
            self.assetsLoaderThread.start(QThread.Priority.HighestPriority)
        else:
            self.assetsLoader = AssetsLoader(
                parent=AutonaGameAssetManager.instance
            )
            self.assetsLoader.game = game
            self.assetsLoader.run(mods_active=mods_active, mods_reload_mode=mods_reload_mode)

        # Setup startgg character id to character name (prefer runtime copy in
        # user_data/, fall back to the tracked seed in assets/)
        sggcharacters = orjson.loads(
            open(_resolve_characters_path(), 'rb').read())
        self.startgg_id_to_character = {}

        for c in sggcharacters.get("entities", {}).get("character", []):
            self.startgg_id_to_character[str(c.get("id"))] = c

        # self.programState["asset_path"] = self.selectedGame.get("path")
        # self.programState["game"] = game

        # self.SetupAutocomplete()

        # if self.settings.get("autosave") == True:
        #    self.ExportProgramState()

        # self.gameSelect.clear()

        # self.gameSelect.addItem("")

        # for game in self.games:
        #    self.gameSelect.addItem(self.games[game]["name"])

    def UpdateStageModel(self, mods_active = True):
        # TODO: Make modded content disabled by default
        # TODO: Add checkbox on game bar to enable / disable modded content
        try:
            self.stageModel = QStandardItemModel()
            self.stageModelWithBlank = QStandardItemModel()
            # Add blank
            item = QStandardItem("")
            item.setData({}, Qt.ItemDataRole.UserRole)
            self.stageModelWithBlank.appendRow(item)

            for stage in self.selectedGame.get("stage_to_codename", {}).items():
                # Load stage name translations
                stage[1]["en_name"] = stage[1].get("name")

                # Display name
                display_name = stage[1].get("name")

                locale = AutonaLocaleHelper.programLocale
                if locale.replace('-', '_') in stage[1].get("locale", {}):
                    display_name = stage[1].get("locale", {})[
                        locale.replace('-', '_')]
                elif locale.split('-')[0] in stage[1].get("locale", {}):
                    display_name = stage[1].get("locale", {})[
                        locale.split('-')[0]]
                elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.programLocale) in stage[1].get("locale", {}):
                    display_name = stage[1].get("locale", {})[
                        AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.programLocale)]

                stage[1]["display_name"] = display_name

                # Export name
                export_name = stage[1].get("name")

                locale = AutonaLocaleHelper.exportLocale
                if locale.replace('-', '_') in stage[1].get("locale", {}):
                    export_name = stage[1].get("locale", {})[
                        locale.replace('-', '_')]
                elif locale.split('-')[0] in stage[1].get("locale", {}):
                    export_name = stage[1].get("locale", {})[
                        locale.split('-')[0]]
                elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale) in stage[1].get("locale", {}):
                    export_name = stage[1].get("locale", {})[
                        AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale)]

                stage[1]["name"] = export_name

                item = QStandardItem(f'{stage[1].get("display_name")} / {stage[1].get("en_name")}' if stage[1].get(
                    "display_name") != stage[1].get("en_name") else stage[1].get("display_name"))
                item.setData(stage[1], Qt.ItemDataRole.UserRole)

                item_with_blank = QStandardItem(f'{stage[1].get("display_name")} / {stage[1].get("en_name")}' if stage[1].get(
                    "display_name") != stage[1].get("en_name") else stage[1].get("display_name"))
                item_with_blank.setData(stage[1], Qt.ItemDataRole.UserRole)

                if stage[1].get("modded"):
                    self.has_modded_content = True

                if (not mods_active) and stage[1].get("modded"):
                    item.setEnabled(False)
                    item.setSelectable(False)
                    item_with_blank.setEnabled(False)
                    item_with_blank.setSelectable(False)
                else:
                    self.stageModel.appendRow(item)
                    self.stageModelWithBlank.appendRow(item_with_blank)

                worker = Worker(self.LoadStageImage, *[stage[1], item])
                worker_blank = Worker(self.LoadStageImage, *[stage[1], item_with_blank])
                worker.signals.result.connect(self.LoadStageImageComplete)
                worker_blank.signals.result.connect(self.LoadStageImageComplete)
                self.threadpool.start(worker)
                self.threadpool.start(worker_blank)
            self.stageModelWithBlank.sort(0)
        except:
            logger.error(traceback.format_exc())

    def LoadStageImage(self, stage, item, progress_callback, cancel_event):
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
                pix = QPixmap.fromImage(qimg)
                icon = QIcon(pix)

                return ([item, icon])
        except Exception as e:
            img = QPixmap("./assets/icons/cancel.svg").scaled(32, 32)
            icon = QIcon(img)
            logger.error(traceback.format_exc())
            return ([item, icon])

    def LoadStageImageComplete(self, result):
        try:
            if result is not None:
                if result[0] and result[1]:
                    result[0].setIcon(result[1])
        except Exception as e:
            logger.error(traceback.format_exc())
            return (None)

    def GetStockIcon(self, en_name, skin=0):
        # Shared, memoised 32px stock-icon QIcon for a character/skin. QIcon /
        # QPixmap must be built on the GUI thread; all callers
        # (UpdateCharacterModel, AutonaPlayerDB.SetupModel) run there. The cache
        # is cleared on game load. Returns an empty QIcon when no path exists.
        cache_key = (en_name, skin)
        icon = self._iconCache.get(cache_key)
        if icon is None:
            path = self.stockIcons.get(en_name, {}).get(skin)
            if path is None:
                return QIcon()
            icon = QIcon(QPixmap.fromImage(QImage(path).scaledToWidth(
                32, Qt.TransformationMode.SmoothTransformation)))
            self._iconCache[cache_key] = icon
        return icon

    def UpdateCharacterModel(self, mods_active = True):
        # TODO: Make modded content disabled by default
        # TODO: Add checkbox on game bar to enable / disable modded content
        try:
            self.characterModel = QStandardItemModel()

            # Add one empty
            item = QStandardItem("")
            self.characterModel.appendRow(item)

            for c in self.characters.keys():
                item = QStandardItem()
                item.setData(c, Qt.ItemDataRole.EditRole)
                logger.debug(c)
                item.setIcon(self.GetStockIcon(c, 0))

                data = {
                    "name": self.characters[c].get("export_name"),
                    "en_name": c,
                    "display_name": self.characters[c].get("display_name"),
                    "codename": self.characters[c].get("codename"),
                    "modded": self.characters[c].get("modded", False)
                }

                if self.characters[c].get("display_name") != c:
                    item.setData(
                        f'{self.characters[c].get("display_name")} / {c}', Qt.ItemDataRole.EditRole)

                item.setData(data, Qt.ItemDataRole.UserRole)

                if data.get("modded"):
                    self.has_modded_content = True

                if (not mods_active) and data.get("modded"):
                    item.setEnabled(False)
                    item.setSelectable(False)
                else:
                    self.characterModel.appendRow(item)

            self.characterModel.sort(0)
        except:
            logger.error(traceback.format_exc())

    def UpdateColorModel(self):
        try:
            self.colorModel = QStandardItemModel()

            # Add one empty
            item = QStandardItem("")
            self.colorModel.appendRow(item)

            for c in range(len(self.colors)):
                name = self.colors[c].get("name")
                item = QStandardItem()
                item.setData(name, Qt.ItemDataRole.EditRole)
                logger.debug(name)

                data = {
                    "name": self.colors[c].get("export_name"),
                    "en_name": c,
                    "display_name": self.colors[c].get("display_name"),
                    "value": self.colors[c].get("value"),
                    "force_opponent": self.colors[c].get("force_opponent")
                }

                icon = QPixmap(100,100)
                icon.fill(QColor("#" + self.colors[c].get("value")))
                item.setIcon(QIcon(icon))


                if self.colors[c].get("display_name") != name:
                    item.setData(
                        f'{self.colors[c].get("display_name")} / {name}', Qt.ItemDataRole.EditRole)

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.colorModel.appendRow(item)

            self.colorModel.sort(0)
        except:
            logger.error(traceback.format_exc())


    def UpdateVariantModel(self):
        try:
            self.variantModel = QStandardItemModel()

            # Add one empty
            item = QStandardItem("")
            self.variantModel.appendRow(item)

            for c in self.variants.keys():
                item = QStandardItem()
                item.setData(c, Qt.ItemDataRole.EditRole)
                logger.debug(c)

                data = {
                    "name": self.variants[c].get("export_name"),
                    "en_name": c,
                    "display_name": self.variants[c].get("display_name"),
                    "codename": self.variants[c].get("codename")
                }

                
                data["icon_path"] = self.GetVariantIconPath(data["codename"])
                data["image_size"] = self.GetVariantIconSize(data["codename"])
                if data["icon_path"]:
                    item.setIcon(QIcon(QPixmap.fromImage(QImage(data["icon_path"])))
                    )
                else:
                    item.setIcon(QIcon(QPixmap.fromImage(QImage('./assets/icons/cancel.svg')))
                    )

                if self.variants[c].get("display_name") != c:
                    item.setData(
                        f'{self.variants[c].get("display_name")} / {c}', Qt.ItemDataRole.EditRole)

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.variantModel.appendRow(item)

            self.variantModel.sort(0)
        except:
            logger.error(traceback.format_exc())

    def GetVariantIconPath(self, variant_codename):
        # Read from the variant icon index built at load time (no per-item
        # config read / isfile). See _LoadGameAssetsCore.
        return self.variantIconIndex.get(variant_codename, {}).get("path", "")

    def GetVariantIconSize(self, variant_codename):
        # Read from the variant icon index built at load time.
        return self.variantIconIndex.get(variant_codename, {}).get("image_size")

    def UpdateSkinModel(self):
        self.skinModels = {}

        self.workers = []

        for key, character in self.characters.items():
            characterData = character

            if characterData:
                skins = AutonaGameAssetManager.instance.skins.get(key, {})

            sortedSkins = [int(k) for k in skins.keys()]
            sortedSkins.sort()

            skinModel = QStandardItemModel()

            allAssetData = []
            allItem = []

            for skin in sortedSkins:
                item = QStandardItem()

                skinModel.appendRow(item)

                assetData = {}
                assetData["assets"] = AutonaGameAssetManager.instance.GetCharacterAssets(
                    character.get("codename"), skin)
                if assetData["assets"] == None:
                    assetData["assets"] = {}

                skinIndex = str(skin)

                # Get skin name
                skinNameData = AutonaGameAssetManager.instance.characters.get(
                    character.get("en_name"), {}).get("skin_name", {})

                skin_name = character.get("name")
                skin_name_en = character.get("en_name")

                try:
                    locale = AutonaLocaleHelper.programLocale

                    if locale.replace("-", "_") in skinNameData.get(skinIndex, {}).get("locale", {}):
                        skin_name = skinNameData.get(skinIndex, {}).get(
                            "locale", {})[locale.replace("-", "_")]
                    elif re.split("-|_", locale)[0] in skinNameData.get(skinIndex, {}).get("locale", {}):
                        skin_name = skinNameData.get(skinIndex, {}).get(
                            "locale", {})[re.split("-|_", locale)[0]]
                    elif AutonaLocaleHelper.GetRemaps(AutonaLocaleHelper.exportLocale) in skinNameData.get("locale", {}):
                        skin_name = skinNameData.get(skinIndex, {}).get("locale", {})[AutonaLocaleHelper.GetRemaps(
                            AutonaLocaleHelper.exportLocale)]
                    elif skinNameData.get(skinIndex, {}).get("name"):
                        skin_name = skinNameData.get(skinIndex, {}).get("name")

                    if skinNameData.get(skinIndex, {}).get("name"):
                        skin_name_en = skinNameData.get(
                            skinIndex, {}).get("name")
                except:
                    logger.error(traceback.format_exc())

                if skinNameData.get(skinIndex, {}).get("is_different_character", False):
                    assetData["name"] = skin_name
                    assetData["en_name"] = skin_name_en

                item.setData(skin_name if skin_name else skinIndex,
                             Qt.ItemDataRole.EditRole)
                item.setData(assetData, Qt.ItemDataRole.UserRole)

                allItem.append(item)
                allAssetData.append(assetData)

            worker = Worker(self.LoadSkinImages, *
                            [allAssetData, allItem, skinModel])
            worker.signals.result.connect(self.LoadSkinImagesComplete)
            self.workers.append(worker)

            self.skinModels[key] = skinModel

        for w in self.workers:
            self.threadpool.start(w)

    def LoadSkinImages(self, allAssetData, allItem, skinModel, progress_callback, cancel_event):
        try:
            icons = []

            for i in range(len(allAssetData)):
                assetData = allAssetData[i]

                # Set to use first asset as a fallback
                key = AutonaGameAssetManager.instance.biggestCompletePack
                asset = None

                if assetData["assets"].get(key):
                    asset = assetData["assets"][key]
                elif assetData["assets"].get("full"):
                    asset = assetData["assets"]["full"]
                elif assetData["assets"].get("base_files/icon"):
                    asset = assetData["assets"]["base_files/icon"]

                if asset:
                    # pix = QPixmap(asset["asset"])
                    img = Image.open(asset["asset"])
                else:
                    # pix = QPixmap("./assets/icons/cancel.svg").scaled(16,16)
                    img = Image.open("./assets/icons/cancel.svg")

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
                pix = QPixmap.fromImage(qimg)
                icon = QIcon(pix)

                icons.append(icon)

            return ([(allItem[i], icons[i]) for i in range(len(allItem))])
        except Exception as e:
            logger.error(traceback.format_exc())
            return (None)

    def LoadSkinImagesComplete(self, results):
        try:
            for result in results:
                if result is not None:
                    if result[0] and result[1]:
                        result[0].setIcon(result[1])
        except Exception as e:
            logger.error(traceback.format_exc())
            return (None)

    def GetCharacterAssets(self, characterCodename: str, skin: int, assetpack: str = None):
        charFiles = {}

        if self.selectedGame is not None:
            assetsPacks = []

            if assetpack:
                assetsPacks = [assetpack]
            else:
                assetsPacks = self.selectedGame.get("assets", {}).items()

            # For each assets pack
            for assetKey, asset in assetsPacks:
                try:
                    # Skip stage icon asset packs
                    if type(asset.get("type")) == list:
                        if "stage_icon" in asset.get("type") or "variant_icon" in asset.get("type"):
                            continue
                    elif type(asset.get("type")) == str:
                        if asset.get("type") in ["stage_icon", "variant_icon"]:
                            continue

                    assetPath = f'{self.selectedGame.get("path")}/{assetKey}/'

                    baseName = asset.get(
                        "prefix", "")+characterCodename+asset.get("postfix", "")

                    # Read filenames from the load-time index (no filesystem
                    # access). Falls back to a live scan only if the codename/
                    # pack was not indexed (e.g. ad-hoc assetpack argument).
                    skinFileList = self.characterAssetIndex.get(
                        characterCodename, {}).get(assetKey)
                    if skinFileList is None:
                        try:
                            skinFileList = [f for f in os.listdir(
                                assetPath) if f.startswith(baseName)]
                        except OSError:
                            skinFileList = []

                    skinFiles = {}

                    for f in skinFileList:
                        skinId = f[len(baseName):].rsplit(".", 1)[0]
                        if skinId == "":
                            skinId = 0
                        else:
                            skinId = int(skinId)
                        skinFiles[skinId] = f

                    if len(skinFiles) > 0:
                        charFiles[assetKey] = {
                            "type": asset.get("type", [])
                        }

                        # If skin exists, load it
                        if skin in skinFiles:
                            charFiles[assetKey]["asset"] = assetPath + \
                                skinFiles[skin]
                        else:
                            # If skin remap exists, load remap
                            if asset.get("skin_mapping", {}).get(characterCodename, {}).get(str(skin)):
                                target = int(asset.get("skin_mapping", {}).get(
                                    characterCodename, {}).get(str(skin)))
                                charFiles[assetKey]["asset"] = assetPath + \
                                    skinFiles[target]
                            # If none is found, default to skin 0
                            else:
                                charFiles[assetKey]["asset"] = assetPath + \
                                    skinFiles[0]

                    if asset.get("eyesights"):
                        eyesights = asset.get("eyesights", {}).get(
                            characterCodename, {})

                        if len(eyesights.keys()) > 0:
                            if str(skin) in eyesights:
                                if assetKey in charFiles:
                                    charFiles[assetKey] = charFiles.get(assetKey, {})
                                    charFiles[assetKey]["eyesight"] = eyesights.get(
                                        str(skin))
                            else:
                                charFiles[assetKey] = charFiles.get(assetKey, {})
                                charFiles[assetKey]["eyesight"] = list(
                                    eyesights.values())[0]
                    
                    if asset.get("image_sizes"):
                        image_sizes = asset.get("image_sizes", {}).get(
                            characterCodename, {})

                        if len(image_sizes.keys()) > 0:
                            if str(skin) in image_sizes:
                                if assetKey in charFiles:
                                    charFiles[assetKey]["image_size"] = image_sizes.get(
                                        str(skin))
                            else:
                                charFiles[assetKey]["image_size"] = list(
                                    image_sizes.values())[0]

                    if asset.get("rescaling_factor"):
                        rescaling_factor = asset.get("rescaling_factor", {}).get(
                            characterCodename, {})

                        if len(rescaling_factor.keys()) > 0:
                            if str(skin) in rescaling_factor:
                                if assetKey in charFiles:
                                    charFiles[assetKey]["rescaling_factor"] = rescaling_factor.get(
                                        str(skin))
                            else:
                                charFiles[assetKey]["rescaling_factor"] = rescaling_factor.get(
                                    "0", 1)

                    if asset.get("unflippable"):
                        unflippable = asset.get("unflippable", {}).get(
                            characterCodename, {})

                        if len(unflippable.keys()) > 0:
                            if str(skin) in unflippable:
                                if assetKey in charFiles:
                                    charFiles[assetKey]["unflippable"] = unflippable.get(
                                        str(skin))
                            else:
                                charFiles[assetKey]["unflippable"] = list(
                                    unflippable.values())[0]

                    if asset.get("metadata"):
                        metadata = {}
                        charFiles[assetKey]['metadata'] = {}
                        for key in range(len(asset.get("metadata"))):
                            metadata[key] = asset.get("metadata", {})[key]["values"].get(
                                characterCodename, {}).get("value", "")
                            charFiles[assetKey]['metadata'][f"{key}"] = {}
                            charFiles[assetKey]['metadata'][f"{key}"]["title_en"] = asset.get("metadata", {})[
                                key].get("title", '')
                            if AutonaLocaleHelper.exportLocale in asset.get("metadata", {})[key].get("locale", {}).keys() or AutonaLocaleHelper.exportLocale.split('-')[0] in asset.get("metadata", {})[key].get("locale", {}).keys():
                                try:
                                    metadata_title_locale = asset.get("metadata", {})[key].get("locale", {})[
                                        AutonaLocaleHelper.exportLocale]
                                except KeyError:
                                    metadata_title_locale = asset.get("metadata", {})[key].get(
                                        "locale", {})[AutonaLocaleHelper.exportLocale.split('-')[0]]
                            else:
                                metadata_title_locale = asset.get("metadata", {})[
                                    key].get("title", '')
                            charFiles[assetKey]['metadata'][f"{key}"]["title"] = metadata_title_locale
                            charFiles[assetKey]['metadata'][f"{key}"][f"value_en"] = metadata[key]
                            if AutonaLocaleHelper.exportLocale in asset.get("metadata", {})[key]["values"].get(characterCodename, {}).get("locale", {}).keys() or AutonaLocaleHelper.exportLocale.split('-')[0] in asset.get("metadata", {})[key]["values"].get(characterCodename, {}).get("locale", {}).keys():
                                try:
                                    metadata[key] = asset.get("metadata", {})[key]["values"].get(
                                        characterCodename, {}).get("locale", {})[AutonaLocaleHelper.exportLocale]
                                except KeyError:
                                    metadata[key] = asset.get("metadata", {})[key]["values"].get(
                                        characterCodename, {}).get("locale", {})[AutonaLocaleHelper.exportLocale.split('-')[0]]
                            charFiles[assetKey]['metadata'][f"{key}"][f"value"] = metadata[key]

                        # if len(metadata.keys()) > 0:
                        #     if str(skin) in metadata:
                        #         charFiles[assetKey]["metadata"] = metadata.get(
                        #             str(skin))
                        #     else:
                        #         charFiles[assetKey]["metadata"] = list(
                        #             metadata.values())[0]

                    if asset.get("uncropped_edge"):
                        if assetKey in charFiles:
                            charFiles[assetKey]["uncropped_edge"] = asset.get(
                                "uncropped_edge")

                    if asset.get("average_size"):
                        if assetKey in charFiles:
                            charFiles[assetKey]["average_size"] = asset.get(
                                "average_size")
                except Exception as e:
                    logger.error(traceback.format_exc())

        return (charFiles)

    def GetCharacterFromStartGGId(self, smashgg_id: int):
        startggcharacter = self.startgg_id_to_character.get(str(smashgg_id))

        if startggcharacter:
            character = next((c for c in self.characters.items() if c[1].get(
                "smashgg_name") == startggcharacter.get("name")), None)
            if character:
                return character

        return None

    def GetStageFromStartGGId(self, smashgg_id: int):
        stage = next((s for s in self.stages.items() if str(
            s[1].get("smashgg_id")) == str(smashgg_id)), None)
        return stage


if not os.path.exists("./user_data/games"):
    os.makedirs("./user_data/games")

if AutonaGameAssetManager.instance == None:
    AutonaGameAssetManager.instance = AutonaGameAssetManager()
