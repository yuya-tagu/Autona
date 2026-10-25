from multiprocessing import Lock
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import traceback
from loguru import logger

from .Helpers.AutonaQtHelper import gui_thread_sync
from .AutonaGameAssetManager import AutonaGameAssetManager
from .core.PlayerDB import PlayerDB

class AutonaPlayerDBSignals(QObject):
    db_updated = Signal()


class _TournamentEntrantsProxy(QSortFilterProxyModel):
    """Filters the player model down to the currently loaded
    tournament's entrants (PlayerDB.tournamentTags). Passes every
    row through while no tournament entrant set is known, so completers
    fall back to the whole DB."""

    def filterAcceptsRow(self, source_row, source_parent):
        tags = PlayerDB.instance.tournamentTags
        if not tags:
            return True
        index = self.sourceModel().index(source_row, 0, source_parent)
        return self.sourceModel().data(index) in tags


class AutonaPlayerDB:
    """The player DB's Qt adapter (ADR-005 S3e-1).

    The data -- the players, Load / Add / Delete / Get / Save, the loaded
    tournament's entrant tags -- is src/core/PlayerDB.py
    (`PlayerDB.instance`); call it for anything that reads or changes a
    player. What stays here is Qt: the completer model and its tournament
    filter, and `signals.db_updated`, which the widgets and the web server's
    player DB push listen to. Attach() relays every change the core
    announces to SetupModel(), i.e. to db_updated on the GUI thread, exactly
    where the old class called SetupModel() itself.
    """
    signals = AutonaPlayerDBSignals()
    model: QStandardItemModel = None
    # Stable proxy over `model` used by the player-name completers; the
    # source model is swapped on every SetupModel rebuild. See
    # GetCompleterModel().
    completerModel: _TournamentEntrantsProxy = None
    # Previous generation of `model`, kept alive for one rebuild cycle so
    # the proxy/completer chain never references a deleted C++ model (see
    # SetupModel).
    _retiredModel: QStandardItemModel = None
    # Whether SetupModel builds the Qt model (QStandardItemModel + QIcons,
    # i.e. QtGui). Switched on by the desktop shell (bootstrap_gui ->
    # EnableModel); the headless shell has no completer to feed and no
    # QGuiApplication to build icons with, so there a refresh only tells the
    # listeners (ADR-005 S3c).
    modelEnabled = False
    modelLock = Lock()
    # The PlayerDB whose `changed` channel SetupModel is subscribed to.
    _attachedTo: PlayerDB = None

    @staticmethod
    def Attach():
        """Subscribe SetupModel to PlayerDB.instance's change announcements.

        Called once by bootstrap_core(), right after the core DB is built:
        both shells need it (db_updated is what the web server's player DB
        push listens to). This class is static and lives as long as the
        process, so the subscription -- an ObserverChannel holds its
        callback strongly -- can never outlive its receiver. Idempotent; when
        PlayerDB.instance was replaced since the last call, the old DB's
        subscription is dropped first, so only the current DB drives the
        model and db_updated.
        """
        db = PlayerDB.instance
        previous = AutonaPlayerDB._attachedTo
        if previous is db:
            return
        if previous is not None:
            previous.changed.unsubscribe(AutonaPlayerDB.SetupModel)
        db.changed.subscribe(AutonaPlayerDB.SetupModel)
        AutonaPlayerDB._attachedTo = db

    @staticmethod
    def EnableModel():
        """Have SetupModel build the Qt model from now on. The desktop
        shell's switch (bootstrap_gui), called before the first LoadDB()."""
        AutonaPlayerDB.modelEnabled = True

    @staticmethod
    @gui_thread_sync
    def SetupModel():
        """Refresh what the DB's consumers see after it changed.

        On the desktop: rebuild the completer model (below), which also
        normalises the loaded game's mains in place, then emit db_updated.
        Without the model (the headless shell, see `modelEnabled`): emit
        db_updated only -- the web server's player DB push is what listens
        there, and it reads the core DB itself. Either way on the GUI thread,
        where the emit has always come from.

        Reached from the core's change announcements (Attach) and from a game
        load (the asset adapter's onLoad on the desktop, the headless
        loader's gameChanged).
        """
        if not AutonaPlayerDB.modelEnabled:
            AutonaPlayerDB.signals.db_updated.emit()
            return
        AutonaPlayerDB._BuildModel()

    @staticmethod
    def _BuildModel():
        # GUI thread only (QStandardItemModel / QIcon): reached through
        # SetupModel, which marshals there.
        db = PlayerDB.instance
        # The DB's lock for the whole walk: the entrants worker may be adding
        # players meanwhile, and the walk below also rewrites mains in place
        # (S3c-2 decision 5; whether that belongs to the core is S3e-3's
        # call). Nothing in here waits for another thread, and the core never
        # announces a change while holding the lock, so a worker blocked on it
        # cannot be the one this thread is waiting for.
        with AutonaPlayerDB.modelLock, db.lock:
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

            for player in db.database.values():
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

        # After both locks: db_updated's slots (the web server's push among
        # them) read the DB through the core's own locked readers, and none
        # of them rebuilds the model (modelLock is not re-entrant).
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
        PlayerDB.instance.SetTournamentTags(tags)
        if AutonaPlayerDB.completerModel is not None:
            AutonaPlayerDB.completerModel.invalidateFilter()

    @staticmethod
    @gui_thread_sync
    def ClearTournamentPlayers():
        """Back to the whole DB as completer candidates (no tournament
        loaded)."""
        if not PlayerDB.instance.ClearTournamentTags():
            return
        if AutonaPlayerDB.completerModel is not None:
            AutonaPlayerDB.completerModel.invalidateFilter()
