from qtpy.QtCore import QObject, Signal
from loguru import logger

from .Helpers.AutonaQtHelper import gui_thread_async
from .core.GameAssetCatalog import GameAssetCatalog
from .core.PlayerDB import PlayerDB

class AutonaPlayerDBSignals(QObject):
    db_updated = Signal()


class AutonaPlayerDB:
    """The player DB's Qt adapter (ADR-005 S3e-1).

    The data -- the players, Load / Add / Delete / Get / Save, the loaded
    tournament's entrant tags, the mains normalisation -- is
    src/core/PlayerDB.py (`PlayerDB.instance`); call it for anything that
    reads or changes a player. What stays here is Qt: `signals.db_updated`,
    which the web server's player DB push listens to. Attach() relays every
    change the core announces to SetupModel(), i.e. to db_updated on the GUI
    thread, exactly where the old class called SetupModel() itself. (The
    completers' proxy over the player model -- `completerModel` /
    GetCompleterModel() -- went in ADR-005 S4ad with its last consumer, the
    Qt player card's name completer; the model itself in the Qt model
    clean-up's PR3, once the mains normalisation its build did in place was
    core's: PlayerDB.NormaliseMains.)

    Everything here runs on the GUI thread, and nothing waits for it there
    (gui_thread_async, ADR-005 S3f-2): called on the GUI thread it runs at
    once, called from another thread -- the start.gg entrants job, a web
    request's database save -- it is queued and the caller goes on. Those
    callers used to wait for the GUI thread (gui_thread_sync); a job of the
    core executor must not, since that wait never returns once the event
    loop has stopped. Queued from one thread, the calls run in the order
    they were made (SetTournamentPlayers before the announcement's
    SetupModel, so the db_updated slots read the new entrants).
    """
    signals = AutonaPlayerDBSignals()
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
        normalisation and db_updated.
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
    @gui_thread_async
    def SetupModel():
        """Refresh what the DB's consumers see after it changed: normalise
        the selected game's mains in place (PlayerDB.NormaliseMains), then
        emit db_updated -- whether anything was rewritten or not, and with
        no save (the next one writes the rewrite out), as the Qt model build
        that did the normalisation until the Qt model clean-up's PR3 did.
        Both shells: the headless one normalises too since then (until then
        its /playerdb and local_players.json kept the mains as stored).

        On the GUI thread, where the emit has always come from; a caller on
        another thread does not wait for it (see the class docstring).
        Reached from the core's change announcements (Attach) and from a game
        load: the asset adapter's onLoad on the desktop, the headless
        loader's gameChanged -- once the load is over, with the loaded game
        the catalog's selection. The name is the model's, kept for its
        callers.
        """
        # The selection as the model build read it (the asset adapter's
        # `selectedGame` is this snapshot's `game`), read once per refresh.
        # A lock-free read of the catalog (pitfalls #16: no writer here).
        codename = GameAssetCatalog.instance.Current().game.get("codename")
        PlayerDB.instance.NormaliseMains(codename)
        # After the DB's lock: db_updated's slots (the web server's push
        # among them) read the DB through the core's own locked readers.
        AutonaPlayerDB.signals.db_updated.emit()

    @staticmethod
    @gui_thread_async
    def SetTournamentPlayers(tags, cancel_event=None):
        """Restrict the player candidates to the given DB tags (the loaded
        tournament's entrants: PlayerDB.SetTournamentTags, which the web
        server's player DB push reads). Called from the entrants fetch job;
        queued to the GUI thread, not waited for, so it lands ahead of the
        fetch's own save announcement (it refiltered the completers' proxy
        there too, until ADR-005 S4ad).

        `cancel_event`: the fetch's. Set by the time this runs, another
        tournament (or none) was loaded meanwhile -- it is set on the GUI
        thread before that tournament's filter is touched -- and the tags
        are dropped (ADR-005 S3f-2 review)."""
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Tournament players of a replaced tournament: "
                        "dropped")
            return
        PlayerDB.instance.SetTournamentTags(tags)

    @staticmethod
    @gui_thread_async
    def ClearTournamentPlayers():
        """Back to the whole DB as player candidates (no tournament
        loaded).

        Announced with db_updated: the web server's player DB push sends
        TournamentPlayers(), which only now is the whole DB again -- without
        it the web pages' candidates stayed the previous tournament's
        entrants (ADR-005 S4b review). No announcement for setting the
        entrants: the fetch that sets them announces its own save
        (NotifyChanged), queued after this filter."""
        if not PlayerDB.instance.ClearTournamentTags():
            return
        AutonaPlayerDB.signals.db_updated.emit()
