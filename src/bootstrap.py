"""Composition root: the single place where core state is brought up.

This file is the authority on singleton construction order. Modules must
not create their own instance at import time anymore (Design: startup
architecture redesign, Stage 2); every migrated singleton is constructed
here, in an explicit, readable order. The same holds for the static data
that used to be read from module tails (settings, language tables, bad-word
lists, GraphQL queries): each such module exposes an idempotent
Initialize(), called from here.

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
    """Load the core data and create the singletons everything depends on.

    Safe to call more than once; only the first call does work.
    """
    global _core_bootstrapped
    if _core_bootstrapped:
        return
    _core_bootstrapped = True

    # ORDER CONSTRAINT: SettingsManager before everything else. It is the
    # only step here with no dependency of its own, and almost every step
    # below reads through SettingsManager.Get() -- StateManager imports it,
    # AutonaHotkeys.LoadUserHotkeys() reads the user hotkey bindings from
    # it, and the widgets built later read ports, languages and layout
    # options. Loading it late would make every one of those silently fall
    # back to its default. It also creates ./user_data, which later steps
    # write into.
    from .SettingsManager import SettingsManager
    SettingsManager.Initialize()

    # Static asset data with no dependency on anything constructed here:
    # LocaleHelper reads the shipped language tables (src/i18n/mapping.json
    # and assets/data_countries.json), BadWordFilter the ngword lists. Both
    # only fill class attributes. They are placed this early because the
    # data is read from all over the widget tree at runtime and there is no
    # reason to make later steps care about the ordering. (The per-run
    # locale itself -- LoadLocale/LoadRoundNames -- still runs from
    # Window.__init__: it needs a QGuiApplication for the QTranslator.)
    from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    AutonaLocaleHelper.Initialize()

    from .Helpers.AutonaBadWordFilter import AutonaBadWordFilter
    AutonaBadWordFilter.Initialize()

    # StateManager next: it owns ./out and loads the persisted program
    # state, and every widget built later reads through it.
    from .StateManager import StateManager
    StateManager.Initialize()

    # ORDER CONSTRAINT: the Qt bridge for the state layer must exist before
    # the first consumer connects to it -- that is every widget built from
    # Window.__init__ and WebServer.__init__, i.e. everything from
    # AutonaHotkeys below onwards. It must also come *after*
    # StateManager.Initialize(): LoadState() fires state_big_change, and
    # creating the bridge earlier would re-emit that into a widget tree that
    # does not exist yet (today it reaches nobody either way -- keeping the
    # order makes that true by construction rather than by luck).
    # It is a plain QObject, so it needs a QApplication no more than
    # AutonaHotkeys does; it only has to be built on the GUI thread, which
    # is where bootstrap_core() runs.
    # The None check is the same idempotence guard the other singletons get
    # here; the bridge additionally refuses a second construction from its
    # own __init__, because a duplicate would double every state
    # notification without raising anything.
    from .StateManagerBridge import StateManagerBridge
    if StateManagerBridge.instance is None:
        StateManagerBridge.instance = StateManagerBridge()

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

    # ORDER CONSTRAINT: the GraphQL query texts must be on the class before
    # the first Start.gg request. Loading them here, right above the
    # provider that owns them, keeps the "read files" step next to the only
    # consumer instead of hidden at the bottom of a 2200-line module.
    from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
    StartGGDataProvider.Initialize()

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

    # CountryHelper's __init__ is empty -- the expensive part (LoadCountries,
    # ~250 flag icons + the country model) still runs from init(), called by
    # the window. Its import-time construction used to happen wherever the
    # module was first pulled in (in practice through StartGGDataProvider,
    # i.e. earlier than the Autona.py import that nominally owned it); the
    # constructor depends on nothing, so pinning it here is behaviour
    # preserving and matches the old nominal import order (CountryHelper
    # before ScoreboardManager).
    from .Helpers.AutonaCountryHelper import AutonaCountryHelper
    if AutonaCountryHelper.instance is None:
        AutonaCountryHelper.instance = AutonaCountryHelper()

    # ORDER CONSTRAINT: AutonaScoreboardManager is a QDockWidget, so a
    # QApplication must already exist -- constructing a QWidget without one
    # aborts the process at the C++ level. That holds at every entry point:
    # main.py builds the QApplication before `from src import Autona`, and
    # Autona.py keeps a fallback for PyInstaller's dependency-analysis
    # subprocess, which imports modules without main.py. Both run before
    # bootstrap_core().
    # It also calls StateManager.Unset("score") in __init__, so it must come
    # after StateManager.Initialize() above. The boards themselves are not
    # built here: CreateScoreboard() is called later, from the window.
    from .AutonaScoreboardManager import AutonaScoreboardManager
    if AutonaScoreboardManager.instance is None:
        AutonaScoreboardManager.instance = AutonaScoreboardManager()

    logger.debug("bootstrap_core complete")
