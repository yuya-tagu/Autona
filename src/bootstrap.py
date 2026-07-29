"""Composition root: the single place where core singletons are created.

This file is the authority on singleton construction order. Modules must
not create their own instance at import time anymore (Design: startup
architecture redesign, Stage 2); every migrated singleton is constructed
here, in an explicit, readable order.

Rules for editing this file:

- Whenever a construction step depends on another one having run first,
  state that constraint in a comment directly above the call. The order is
  code, the reason must be code too -- a reordering that looks harmless is
  exactly how the implicit import-order dependencies used to break.
- Imports are done *inside* the functions, never at module level, so that
  importing `src.bootstrap` itself pulls in nothing and triggers no side
  effect. Callers decide when the chain runs.
"""

from loguru import logger

# Re-entrancy guard: bootstrap_core() is idempotent, so a second call (e.g.
# a re-import of src under a different name, or PyInstaller's dependency
# analysis pass) is a no-op rather than a duplicate construction.
_core_bootstrapped = False


def bootstrap_core():
    """Create the core singletons that everything else depends on.

    Safe to call more than once; only the first call does work.
    """
    global _core_bootstrapped
    if _core_bootstrapped:
        return
    _core_bootstrapped = True

    # StateManager first: it owns ./out and loads the persisted program
    # state, and every widget built later reads through it.
    from .StateManager import StateManager
    StateManager.Initialize()

    # AutonaHotkeys only needs SettingsManager (read at construction time by
    # LoadUserHotkeys); the Windows hotkeys themselves are registered later,
    # from UiMounted(). Its `signals` class attribute is referenced by
    # widgets at connect time, so the instance must exist before the window
    # is built, not before the widget modules are imported.
    from .AutonaHotkeys import AutonaHotkeys
    if AutonaHotkeys.instance is None:
        AutonaHotkeys.instance = AutonaHotkeys()

    # GameAssetManager writes StateManager's "game" key in its __init__, so it
    # has to come after StateManager.Initialize() above. It loads no asset
    # here: the games/characters scan runs later, from UiMounted().
    from .AutonaGameAssetManager import AutonaGameAssetManager
    if AutonaGameAssetManager.instance is None:
        AutonaGameAssetManager.instance = AutonaGameAssetManager()

    # PlayerDB's model rebuild must be wired to GameAssetManager.onLoad before
    # the first LoadGames() (UiMounted). This used to be an import-time side
    # effect at the bottom of AutonaPlayerDB, which silently required
    # GameAssetManager.instance to exist before *anything* imported PlayerDB --
    # including TournamentDataProvider and StartGGDataProvider, which both do.
    # PlayerDB is a static class, so there is no instance to create here, only
    # this wiring.
    from .AutonaPlayerDB import AutonaPlayerDB
    AutonaGameAssetManager.instance.signals.onLoad.connect(
        AutonaPlayerDB.SetupModel)

    # ORDER CONSTRAINT: AutonaTournamentDataProvider.__init__ connects to
    # AutonaGameAssetManager.instance.signals, so the GameAssetManager above
    # must already be constructed. This used to be an implicit import-order
    # rule in Autona.py (agent-pitfalls #2); it is an explicit call order now.
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    if AutonaTournamentDataProvider.instance is None:
        AutonaTournamentDataProvider.instance = AutonaTournamentDataProvider()

    # AlertNotification and Updater have no dependency on each other, nor on
    # anything constructed above: both __init__ only build the object itself
    # (AlertNotification also ensures ./user_data/alerts_red.json exists). They
    # are kept in this position, and in this order, because that is where the
    # old import-time construction sat in Autona.py (imports 177 -> 178), and
    # this stage is a behaviour-preserving move.
    from .AutonaAlertNotification import AutonaAlertNotification
    if AutonaAlertNotification.instance is None:
        AutonaAlertNotification.instance = AutonaAlertNotification()

    from .AutonaUpdater import AutonaUpdater
    if AutonaUpdater.instance is None:
        AutonaUpdater.instance = AutonaUpdater()

    logger.debug("bootstrap_core complete")
