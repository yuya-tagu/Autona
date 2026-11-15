"""The desktop's notifications of the game loader (ADR-005 S1a; the loader
itself is core's since ADR-005 S-final SF-6b).

The data -- games, characters, skins, stages, asset paths, the start.gg id
tables -- lives in the Qt-free `src/core/GameAssetCatalog.py`, and the
startup, the scan and the loads in core's `src/core/GameLoader.py`, which
both shells share (`loader`; until SF-6b this class held its own copy, and
the headless shell another). What is left here is the desktop's side of it:

- `signals.onLoadAssets` (a scan is over, emitted from the scan's job: the
  web server's games_changed), `gameLoaded` (the load's snapshot: the
  roster's wiring, bootstrap.wire_game_roster) and `onLoad` (the load is
  in: the persisted pick first, the player DB's refresh, the web server's
  push), emitted on the GUI thread inside the load's board operation (the
  loader's _Publish);
- UiMounted(): the loader's Start(), once the window is up.

It held the Qt combos' QStandardItemModels until the Qt model clean-up's
PR4, and needs no QtGui. SF-6c removes it (the loader's announcement then
goes through core and the state bridge).
"""
from qtpy.QtCore import QObject, Signal


class AutonaGameAssetManagerSignals(QObject):
    onLoad = Signal()
    onLoadAssets = Signal()
    # The GameAssetSnapshot a load published, emitted just before onLoad
    # (bootstrap.wire_game_roster), on the GUI thread. The load's own
    # snapshot, which a slot keeps reading after a later load has moved the
    # catalog's Current() on.
    gameLoaded = Signal(object)


class AutonaGameAssetManager(QObject):
    instance: "AutonaGameAssetManager" = None

    def __init__(self, catalog, provider=None, parent=None) -> None:
        super().__init__(parent)
        # The composition root's: what both shells give their loader.
        from .bootstrap import make_game_loader

        self.catalog = catalog
        self.signals = AutonaGameAssetManagerSignals()
        # `provider`: the tournament provider, whose game every scan
        # re-applies ahead of the startup restore (bootstrap_gui() passes
        # it; it was the first slot on onLoadAssets until SF-6b).
        self.loader = make_game_loader(
            catalog, announce=self._Announce,
            scanned=self.signals.onLoadAssets.emit,
            afterScan=provider.SetGameFromProvider if provider else None)
        # First, so it runs ahead of the other slots on onLoad (connected
        # by bootstrap_gui() and the window): the next launch's pick.
        self.signals.onLoad.connect(self.loader.PersistSelectedGame)

    def UiMounted(self):
        """The startup's scan, restore and start.gg download (the
        loader's), once the window is up."""
        self.loader.Start()

    def _Announce(self, snapshot):
        # The loader's announcement, inside its board operation on the GUI
        # thread (GameLoader._Publish): gameLoaded ahead of onLoad.
        self.signals.gameLoaded.emit(snapshot)
        self.signals.onLoad.emit()
