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
from .core.SettingsManager import SettingsManager

class AutonaPlayerDBSignals(QObject):
    db_updated = Signal()


class _TournamentEntrantsProxy(QSortFilterProxyModel):
    """Filters the player model down to the currently loaded
    tournament's entrants (AutonaPlayerDB.tournamentTags). Passes every
    row through while no tournament entrant set is known, so completers
    fall back to the whole DB."""

    def filterAcceptsRow(self, source_row, source_parent):
        tags = AutonaPlayerDB.tournamentTags
        if not tags:
            return True
        index = self.sourceModel().index(source_row, 0, source_parent)
        return self.sourceModel().data(index) in tags


class AutonaPlayerDB:
    signals = AutonaPlayerDBSignals()
    database = {}
    model: QStandardItemModel = None
    # Stable proxy over `model` used by the player-name completers; the
    # source model is swapped on every SetupModel rebuild. See
    # GetCompleterModel().
    completerModel: _TournamentEntrantsProxy = None
    # Previous generation of `model`, kept alive for one rebuild cycle so
    # the proxy/completer chain never references a deleted C++ model (see
    # SetupModel).
    _retiredModel: QStandardItemModel = None
    # DB tags (see PlayerTag) of the loaded tournament's entrants.
    # Empty set = no tournament loaded -> completers show the whole DB.
    tournamentTags = set()
    modelLock = Lock()
    fieldnames = ["prefix", "gamerTag", "name", "twitter",
                "country_code", "state_code", "mains", "custom_textbox"] # Used for filtering what we save locally

    @staticmethod
    def PlayerTag(player):
        """DB key / model display tag for a player dict. Must stay the
        single source of truth so entrant tags collected during a
        tournament fetch match the keys AddPlayers writes."""
        return player.get("prefix")+" "+player.get("gamerTag") \
            if player.get("prefix") else player.get("gamerTag")

    @staticmethod
    def LoadDB():
        try:
            json_db_exists = True
            # True once the legacy CSV import actually put rows in memory. That
            # import exists only in RAM until SaveDB() writes them out, so it is
            # the one path in LoadDB that still has to persist (see below).
            csv_imported = False
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
                            csv_imported = True
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
                    if tag not in AutonaPlayerDB.database:
                        AutonaPlayerDB.database[tag] = player

                        try:
                            player["mains"] = player.get("mains", "{}")
                        except:
                            player["mains"] = {}
                            logger.error(f"No mains found for: {tag}")

            # One line instead of one per player: a 3k-entry DB used to emit 3k
            # INFO records at every launch (written to two log sinks).
            logger.info(
                f"Loaded {len(AutonaPlayerDB.database)} players from the local database")

            # No unconditional write-back: everything LoadDB does above is an
            # in-memory normalisation, so re-serialising the whole DB (indent=2)
            # at every launch only ever rewrote what was just read. The one
            # side effect it also had — dropping keys outside `fieldnames` from
            # the file — is a save-time concern and still happens on the next
            # real mutation (AddPlayers / DeletePlayer / editor save); the
            # in-memory copy keeps those keys either way, so nothing observable
            # changes within a session.
            # The legacy CSV import is the exception: its rows exist nowhere
            # else yet, so that migration still has to be written out here.
            if csv_imported:
                logger.info("Persisting players migrated from the legacy CSV database")
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
                tag = AutonaPlayerDB.PlayerTag(player)

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
            # Retire — don't delete — the outgoing source model. Dropping the
            # last Python reference here destroys the C++ model while it is
            # still installed as completerModel's source; the proxy's stale
            # row mapping (and the QCompleter persistents built on it) then
            # crash with an access violation inside the setSourceModel()
            # reset cascade below (boot crash, 2026-07-11). Holding one
            # generation keeps every dangling C++ reference valid until the
            # swap has rewired the proxy to the new model.
            AutonaPlayerDB._retiredModel = AutonaPlayerDB.model
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

            if AutonaPlayerDB.completerModel is None:
                AutonaPlayerDB.completerModel = _TournamentEntrantsProxy()
            AutonaPlayerDB.completerModel.setSourceModel(AutonaPlayerDB.model)

            AutonaPlayerDB.signals.db_updated.emit()

    @staticmethod
    def GetCompleterModel():
        """Model for the player-name completers: only the loaded
        tournament's entrants, or the whole DB when no tournament is
        loaded. Built by SetupModel — None until the first build, same
        lifecycle as `model`."""
        return AutonaPlayerDB.completerModel

    @staticmethod
    @gui_thread_sync
    def SetTournamentPlayers(tags):
        """Restrict the completer candidates to the given DB tags (the
        loaded tournament's entrants). Called from the entrants fetch
        worker; marshalled to the GUI thread for the proxy refilter."""
        AutonaPlayerDB.tournamentTags = {tag for tag in tags if tag}
        if AutonaPlayerDB.completerModel is not None:
            AutonaPlayerDB.completerModel.invalidateFilter()

    @staticmethod
    @gui_thread_sync
    def ClearTournamentPlayers():
        """Back to the whole DB as completer candidates (no tournament
        loaded)."""
        if not AutonaPlayerDB.tournamentTags:
            return
        AutonaPlayerDB.tournamentTags = set()
        if AutonaPlayerDB.completerModel is not None:
            AutonaPlayerDB.completerModel.invalidateFilter()

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
