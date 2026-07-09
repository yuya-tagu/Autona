from multiprocessing import Lock
import os
import json
import orjson
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import csv
import traceback
from loguru import logger

from copy import deepcopy

from .Helpers.AutonaDictHelper import deep_clone
from .Helpers.AutonaQtHelper import gui_thread_sync
from .AutonaGameAssetManager import AutonaGameAssetManager
from .SettingsManager import SettingsManager

class AutonaPlayerDBSignals(QObject):
    db_updated = Signal()


class AutonaPlayerDB:
    signals = AutonaPlayerDBSignals()
    database = {}
    model: QStandardItemModel = None
    webServer = None
    modelLock = Lock()
    fieldnames = ["prefix", "gamerTag", "name", "twitter",
                "country_code", "state_code", "mains", "custom_textbox"] # Used for filtering what we save locally

    @staticmethod
    def LoadDB():
        try:
            json_db_exists = True
            if os.path.exists("./user_data/local_players.json") == False:
                json_db_exists = False
                with open('./user_data/local_players.json', 'wt', encoding='utf-8') as outfile:
                    outfile.write(json.dumps([]))

            if (not json_db_exists) and os.path.exists("./user_data/local_players.csv"):
                logger.info("Importing from the previous version of the player database")
                with open('./user_data/local_players.csv', 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile, quotechar='\'')
                    for player in reader:
                        tag = player.get(
                            "prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")
                        if tag not in AutonaPlayerDB.database:
                            AutonaPlayerDB.database[tag] = player
                            logger.info(f"Import player {tag} from old database")
                            try:
                                player["mains"] = json.loads(
                                    player.get("mains", "{}"))
                            except:
                                player["mains"] = {}
                                logger.error(f"No mains found for: {tag}")

            json_db_exists = True

            if os.path.exists("./user_data/local_players.csv"):
                try:
                    os.remove("./user_data/local_players.csv")
                except Exception as e:
                    logger.error(traceback.format_exc())

            with open('./user_data/local_players.json', 'rt', encoding='utf-8') as jsonfile:
                player_list = orjson.loads(jsonfile.read())
                for player in player_list:
                    tag = player.get("prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")
                    logger.info(f"Loading player {tag} from local database")
                    if tag not in AutonaPlayerDB.database:
                        AutonaPlayerDB.database[tag] = player

                        try:
                            player["mains"] = player.get("mains", "{}")
                        except:
                            player["mains"] = {}
                            logger.error(f"No mains found for: {tag}")
            
            AutonaPlayerDB.SaveDB()
            AutonaPlayerDB.SetupModel()
            
        except Exception as e:
            logger.error(traceback.format_exc())

    @staticmethod
    def AddPlayers(players, overwrite=False, deferRefresh=False):
        """Merge `players` into the in-memory database.

        When `deferRefresh` is True, SaveDB() and SetupModel() are skipped —
        the caller is responsible for invoking them once after the final
        batch. This avoids re-serialising the full JSON DB and rebuilding
        the entire QStandardItemModel on every page of a paginated fetch
        (9 pages × full O(N) rebuild would otherwise freeze the UI on
        large local databases).
        """
        logger.info(f"Adding players to DB: {len(players)}")
        for player in players:
            if player is not None:
                tag = player.get(
                    "prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")

                if not overwrite:
                    if tag not in AutonaPlayerDB.database:
                        incomingMains = player.get("mains", {})
                        for game in incomingMains:
                            for main in incomingMains[game]:
                                if len(main) == 1:
                                    main.append(0)
                        AutonaPlayerDB.database[tag] = player
                    else:
                        dbMains = deep_clone(
                            AutonaPlayerDB.database[tag].get("mains", {})
                        )
                        if isinstance(dbMains, str):
                            dbMains = json.loads(dbMains)
                        incomingMains = player.get("mains", {})

                        newMains = []
                        for game in incomingMains:
                            for main in incomingMains[game]:
                                if len(main) == 1:
                                    found = next(
                                        (m for m in dbMains.get(game, []) if m[0] == main[0]), None)
                                    if found:
                                        main.append(found[1])
                                    else:
                                        main.append(0)
                                newMains.append(main)
                            dbMains[game] = newMains

                        if SettingsManager.Get("general.disable_overwrite", False):
                            AutonaPlayerDB.database[tag] = player | AutonaPlayerDB.database[tag]
                        else:
                            AutonaPlayerDB.database[tag].update(player)
                        AutonaPlayerDB.database[tag]["mains"] = dbMains
                else:
                    if AutonaPlayerDB.database.get(tag) is not None and player.get("mains") is not None:
                        try:
                            mains = AutonaPlayerDB.database[tag].get("mains", {})
                            mains.update(player.get("mains", {}))
                            player["mains"] = mains
                        except:
                            logger.error(traceback.format_exc())
                    AutonaPlayerDB.database[tag] = player

        if not deferRefresh:
            AutonaPlayerDB.SaveDB()
            AutonaPlayerDB.SetupModel()

    @staticmethod
    def DeletePlayer(tag):
        if tag in AutonaPlayerDB.database:
            del AutonaPlayerDB.database[tag]

        AutonaPlayerDB.SaveDB()
        AutonaPlayerDB.SetupModel()

    @staticmethod
    def GetPlayerFromTag(tag):
        for player_db in AutonaPlayerDB.database.values():
            if tag.lower() == player_db.get("gamerTag").lower():
                return player_db
        return None

    @staticmethod
    @gui_thread_sync
    def SetupModel():
        with AutonaPlayerDB.modelLock:
            AutonaPlayerDB.model = QStandardItemModel()

            cancelIcon = QIcon(QPixmap.fromImage(QImage("./assets/icons/cancel.svg").scaledToWidth(
                32, Qt.TransformationMode.SmoothTransformation)))

            # Decode only the icons actually referenced by players (their
            # primary main's character/skin), on demand, via the shared
            # memoised provider. Previously this eagerly decoded every
            # character x skin stock icon on the GUI thread on every DB
            # refresh, of which only the players' primary mains were ever used.

            for player in AutonaPlayerDB.database.values():
                if player is not None:
                    try:
                        tag = player.get(
                            "prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")

                        item = QStandardItem(tag)
                        item.setIcon(cancelIcon)

                        if player.get("mains") and type(player.get("mains")) == dict:
                            if AutonaGameAssetManager.instance.selectedGame.get("codename") in player.get("mains", {}).keys():
                                playerMains = player.get(
                                    "mains")[AutonaGameAssetManager.instance.selectedGame.get("codename")]

                                if playerMains is not None and len(playerMains) > 0:
                                    # Must be a list of size 2 [character, skin]
                                    playerMains = [main for main in playerMains if isinstance(
                                        main, list) and (len(main) == 2) or (len(main) == 3)]

                                    # If the skin is invalid, default to 0
                                    for main in playerMains:
                                        while len(main) < 3:
                                            main.append("")
                                        skin = 0
                                        try:
                                            skin = int(main[1])
                                        except:
                                            logger.error(
                                                f'Local DB error: Player {player.get("gamerTag")} has an invalid skin for character {main[0]}')
                                            logger.error(traceback.format_exc())
                                        main[1] = skin

                                        # If no variant, set to none
                                        if len(main) >=3:
                                            variant = main[2]
                                        else:
                                            variant = ""
                                        main[2] = variant
                                        

                                    if playerMains[0][0] in AutonaGameAssetManager.instance.characters.keys():
                                        character = playerMains[0]

                                        # Memoised; decodes once per (char, skin)
                                        # and is a cache hit for repeated mains.
                                        mainIcon = AutonaGameAssetManager.instance.GetStockIcon(
                                            character[0], int(playerMains[0][1]))
                                        if not mainIcon.isNull():
                                            item.setIcon(mainIcon)

                        item.setData(player, Qt.ItemDataRole.UserRole)

                        AutonaPlayerDB.model.appendRow(item)
                    except:
                        logger.error(
                            f'Error loading player from local DB: {player.get("gamerTag")}')
                        logger.error(traceback.format_exc())

            AutonaPlayerDB.signals.db_updated.emit()

    @staticmethod
    def SaveDB():
        try:
            player_list = []

            for player in AutonaPlayerDB.database.values():
                if player is not None:
                    playerData = deep_clone(player)

                    for key in deepcopy(list(playerData.keys())):
                        if key not in AutonaPlayerDB.fieldnames:
                            del playerData[key]

                    player_list.append(playerData)

            with open('./user_data/local_players.json', 'wt', encoding="utf-8") as outfile:
                outfile.write(json.dumps(player_list, indent=2))
        except Exception as e:
            logger.error(traceback.format_exc())


AutonaGameAssetManager.instance.signals.onLoad.connect(AutonaPlayerDB.SetupModel)
