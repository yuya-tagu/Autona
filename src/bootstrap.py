"""Composition root: the single place where core state is brought up.

This file is the authority on singleton construction order. Modules must
not create their own instance at import time anymore (Design: startup
architecture redesign, Stage 2); every migrated singleton is constructed
here, in an explicit, readable order. The same holds for the static data
that used to be read from module tails (settings, language tables, bad-word
lists, GraphQL queries): each such module exposes an idempotent
Initialize(), called from here.

Two stages (ADR-005 S3c):

- bootstrap_core(): what every shell needs -- the settings, the state layer
  and its Qt bridge, the domain operations, the Qt-free catalogs, the
  tournament provider. Nothing here needs a QGuiApplication: the Qt objects
  it builds are QtCore ones (QObject, QThreadPool), which a QCoreApplication
  serves. The headless shell (main.py --headless) stops here.
- bootstrap_gui(): the desktop shell's Qt adapters on top of it -- the
  hotkeys, the game asset / country models, the player DB model wiring, the
  alert and updater objects and the scoreboard dock. src/Autona.py runs both,
  in that order, before the Window is built.

Rules for editing this file:

- Whenever a construction step depends on another one having run first,
  state that constraint in a comment directly above the call. The order is
  code, the reason must be code too -- a reordering that looks harmless is
  exactly how the implicit import-order dependencies used to break.
- A step that builds a QtGui / QtWidgets object, or that only the desktop
  window uses, goes in bootstrap_gui(). bootstrap_core() must stay runnable
  under a QCoreApplication.
- Imports are done *inside* the functions, never at module level, so that
  importing `src.bootstrap` itself pulls in nothing and triggers no side
  effect. Callers decide when the chain runs.
"""

from loguru import logger

# Re-entrancy guards: both stages are idempotent, so a second call (e.g. a
# re-import of src under a different name, or PyInstaller's dependency
# analysis pass) is a no-op rather than a duplicate construction.
_core_bootstrapped = False
_gui_bootstrapped = False


def bootstrap_core():
    """Load the core data and create the singletons every shell depends on.

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
    from .core.SettingsManager import SettingsManager
    SettingsManager.Initialize()

    # The other on-disk settings file: the per-layout options an OBS overlay
    # exposes. Same shape as SettingsManager and no dependency of its own; it
    # only has to be loaded before the LAYOUT panel builds its option widgets
    # (they read their current values through Get()) and before
    # AutonaActions.SetLayoutOption writes through it. Its load used to run at
    # import time (headless core Step 5, instalment 5).
    from .core.LayoutOptionsManager import LayoutOptionsManager
    LayoutOptionsManager.Initialize()

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
    from .core.StateManager import StateManager
    StateManager.Initialize()

    # ORDER CONSTRAINT: the Qt bridge for the state layer must exist before
    # the first consumer connects to it -- that is everything bootstrap_gui()
    # builds and every widget built from Window.__init__, and
    # WebServer.__init__ in either shell. It must also come *after*
    # StateManager.Initialize(): LoadState() fires state_big_change, and
    # creating the bridge earlier would re-emit that into a widget tree that
    # does not exist yet (today it reaches nobody either way -- keeping the
    # order makes that true by construction rather than by luck).
    # It is a plain QObject, so it needs no QApplication; it only has to be
    # built on the main thread (the one running the event loop that its
    # queued deliveries land on), which is where bootstrap_core() runs.
    # The None check is the same idempotence guard the other singletons get
    # here; the bridge additionally refuses a second construction from its
    # own __init__, because a duplicate would double every state
    # notification without raising anything.
    from .StateManagerBridge import StateManagerBridge
    if StateManagerBridge.instance is None:
        StateManagerBridge.instance = StateManagerBridge()

    # ORDER CONSTRAINT: the domain-operations layer must exist before its
    # first caller. That is AutonaHotkeys (bootstrap_gui(), which routes its
    # signals here), and after it every widget and the web server. It has to
    # come after StateManager.Initialize() and SettingsManager.Initialize()
    # above, because its methods read and write both. Qt-free by construction
    # (tests/test_core_qt_free.py), so it needs no QApplication -- which is
    # why the localized BEST OF texts are injected as a callable rather than
    # imported (see AutonaLocaleHelper.FormatMatchText). The tables it reads
    # are filled by LoadRoundNames(), which runs later (Window.__init__); the
    # callable is only invoked when a set length is actually written.
    from .core.Actions import AutonaActions
    if AutonaActions.instance is None:
        AutonaActions.instance = AutonaActions(
            formatMatchText=AutonaLocaleHelper.FormatMatchText)

    # ORDER CONSTRAINT: the game asset catalog (the Qt-free source of truth
    # for games, characters, stages and asset paths) publishes the empty
    # "game" state from ResetGameState(), so it has to come after
    # StateManager.Initialize() above. It reads the per-run locale through
    # the injected callables only when a game is scanned or loaded, i.e.
    # after the shell ran LoadLocale() -- the same deferred-read pattern as
    # AutonaActions' formatMatchText. Nothing is scanned here: the
    # games/characters scan is the shell's (the asset adapter's UiMounted()
    # on the desktop).
    from .core.GameAssetCatalog import GameAssetCatalog
    if GameAssetCatalog.instance is None:
        GameAssetCatalog.instance = GameAssetCatalog(
            locales=lambda: (AutonaLocaleHelper.programLocale,
                             AutonaLocaleHelper.exportLocale),
            remap=AutonaLocaleHelper.GetRemaps)
        GameAssetCatalog.instance.ResetGameState()

    # ORDER CONSTRAINT: the GraphQL query texts must be on the class before
    # the first Start.gg request. Loading them here, right above the
    # provider that owns them, keeps the "read files" step next to the only
    # consumer instead of hidden at the bottom of a 2200-line module.
    from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
    StartGGDataProvider.Initialize()

    # The tournament provider (a QObject with a QThreadPool: QtCore only).
    # It used to connect to the game asset adapter's signals in __init__,
    # which made "adapter before provider" an ORDER CONSTRAINT and the
    # adapter a dependency of every shell. The shell wires that now: it
    # registers how a start.gg videogame id becomes the loaded game
    # (selectGameFromStartGGId) and re-applies the provider's game after a
    # scan -- bootstrap_gui() does both for the desktop (ADR-005 S3c).
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    if AutonaTournamentDataProvider.instance is None:
        AutonaTournamentDataProvider.instance = AutonaTournamentDataProvider()

    # ORDER CONSTRAINT: the tournament metadata writer must exist before the
    # provider above ever emits tournament_data_updated, and before anything
    # that *reads* tournamentInfo.* connects to that signal -- the sidebar
    # tournament button (Window.UpdateTournamentButton) is one such reader,
    # and it used to be correct only because the EVENT panel happened to be
    # constructed earlier in Window.__init__ than the button. The panel is
    # lazy since headless core Step 4b, so the writer lives here instead.
    # Window.__init__ makes the actual connection (the failure path is a
    # dialog); this only has to exist first. The date format is injected
    # because core may not import Qt (tests/test_core_qt_free.py) -- see
    # AutonaLocaleHelper.FormatExportDate.
    from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from .core.TournamentInfoService import TournamentInfoService
    if TournamentInfoService.instance is None:
        TournamentInfoService.instance = TournamentInfoService(
            formatEventDate=AutonaLocaleHelper.FormatExportDate)

    # ORDER CONSTRAINT: the country catalog (the Qt-free source of truth for
    # countries, regions and the city -> region lookup, ADR-005 S1c) has to
    # exist before its Qt adapter (bootstrap_gui()), whose LoadCountries()
    # drives it, and before any reader -- the player widgets built from
    # Window.__init__, the Start.gg parser, the web server. It depends on
    # nothing constructed here. Nothing is read here: the load is the
    # shell's (AutonaCountryHelper.init() in Window.__init__), after
    # LoadLocale(), because the country names are localized and the locale
    # is only read through the injected callable at load time (the
    # GameAssetCatalog pattern).
    from .core.CountryCatalog import CountryCatalog
    if CountryCatalog.instance is None:
        CountryCatalog.instance = CountryCatalog(
            locales=lambda: (AutonaLocaleHelper.programLocale,
                             AutonaLocaleHelper.exportLocale))

    # ORDER CONSTRAINT: Designer designs saved with a private runtime copy
    # are switched to the stub + shared layout/designer_runtime/ before
    # anything can serve or list them -- the web server and the BOARD
    # panel's design discovery are both started by the shell, after this
    # function returns.
    # No dependency on anything above (plain file moves under layout/custom),
    # and it never raises: a failed design keeps its own copy and is logged.
    from .AutonaDesignerService import AutonaDesignerService
    AutonaDesignerService.MigrateLegacyProjects()

    logger.debug("bootstrap_core complete")


def bootstrap_gui():
    """Create the desktop shell's Qt adapters on top of bootstrap_core().

    Called from src/Autona.py right after bootstrap_core(), before the
    Window is built. Needs a QApplication (the scoreboard dock is a
    QWidget). Safe to call more than once; only the first call does work.

    Every step here used to sit inside bootstrap_core(), interleaved with
    the core steps. Moving them behind it changes the construction order only
    where the steps are independent of one another; each block says why
    (ADR-005 S3c).
    """
    global _gui_bootstrapped
    if _gui_bootstrapped:
        return
    _gui_bootstrapped = True

    # ORDER CONSTRAINT: every adapter below wraps or connects to a core
    # singleton. Idempotent, so this is only a guard for a caller that
    # skipped it.
    bootstrap_core()

    # AutonaHotkeys only needs SettingsManager (read at construction time by
    # LoadUserHotkeys) and AutonaActions (ConnectActions below), both from
    # bootstrap_core(); the Windows hotkeys themselves are registered later,
    # from UiMounted(). Its `signals` class attribute is referenced by
    # widgets at connect time, so the instance must exist before the window
    # is built, not before the widget modules are imported. Nothing between
    # its old place (right after AutonaActions) and here reads it.
    from .AutonaHotkeys import AutonaHotkeys
    if AutonaHotkeys.instance is None:
        AutonaHotkeys.instance = AutonaHotkeys()
    # The board-independent hotkeys (score +/-, reset, 反映) are routed to
    # AutonaActions here rather than by each board to itself, which is what
    # made them die when the panel behind them was not built (ADR-004 D2).
    # Idempotent, and deliberately outside the `instance is None` guard so a
    # re-bootstrap re-checks it rather than depending on which branch ran.
    AutonaHotkeys.ConnectActions()

    # ORDER CONSTRAINT: the Qt adapter wraps the game asset catalog
    # (bootstrap_core()). It only builds the (empty) Qt models here.
    from .core.GameAssetCatalog import GameAssetCatalog
    from .AutonaGameAssetManager import AutonaGameAssetManager
    if AutonaGameAssetManager.instance is None:
        AutonaGameAssetManager.instance = AutonaGameAssetManager(
            GameAssetCatalog.instance)

    # The provider's game follows through the adapter on the desktop: a
    # start.gg videogame id loads through it (so its Qt models are rebuilt
    # and onLoad fires), and every scan re-applies the provider's game. The
    # connection used to be made in the provider's __init__; making it here
    # keeps it the first slot on onLoadAssets, ahead of everything the
    # Window connects: Window.ReloadGames (Window.__init__), the EVENT
    # panel's combo when that page is built at startup, and last the
    # adapter's own startup restore (_RestoreSelectedGame, connected in its
    # UiMounted() right before the first scan). The restore therefore still
    # runs after the provider has re-applied its game, as before.
    # tests/test_bootstrap_split.py pins that order on a real emit.
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    AutonaTournamentDataProvider.instance.selectGameFromStartGGId = (
        AutonaGameAssetManager.instance.SetGameFromStartGGId)
    AutonaGameAssetManager.instance.signals.onLoadAssets.connect(
        AutonaTournamentDataProvider.instance.SetGameFromProvider)

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

    # AlertNotification and Updater have no dependency on each other, nor on
    # anything constructed above: both __init__ only build the object itself
    # (AlertNotification also ensures ./user_data/alerts_red.json exists).
    # Kept in this order, the order of the old import-time construction in
    # Autona.py (imports 177 -> 178). Both are the desktop's: the alerts are
    # dialogs and the updater replaces the installed exe.
    from .AutonaAlertNotification import AutonaAlertNotification
    if AutonaAlertNotification.instance is None:
        AutonaAlertNotification.instance = AutonaAlertNotification()

    from .AutonaUpdater import AutonaUpdater
    if AutonaUpdater.instance is None:
        AutonaUpdater.instance = AutonaUpdater()

    # CountryHelper (the country catalog's Qt adapter) has an empty __init__
    # -- the expensive part (LoadCountries: the catalog load, ~250 flag icons
    # + the country model) still runs from init(), called by the window. Its
    # import-time construction used to happen wherever the module was first
    # pulled in (in practice through StartGGDataProvider, i.e. earlier than
    # the Autona.py import that nominally owned it); the constructor depends
    # on nothing, so pinning it here is behaviour preserving and matches the
    # old nominal import order (CountryHelper before ScoreboardManager).
    from .Helpers.AutonaCountryHelper import AutonaCountryHelper
    if AutonaCountryHelper.instance is None:
        AutonaCountryHelper.instance = AutonaCountryHelper()

    # ORDER CONSTRAINT: AutonaScoreboardManager is a QDockWidget, so a
    # QApplication must already exist -- constructing a QWidget without one
    # aborts the process at the C++ level. That holds at every entry point:
    # main.py builds the QApplication before `from src import Autona`, and
    # Autona.py keeps a fallback for PyInstaller's dependency-analysis
    # subprocess, which imports modules without main.py. Both run before
    # bootstrap_gui().
    # It also wipes the `score` subtree in __init__ (StateSchema
    # .ResetScoreRoot), so it must come after StateManager.Initialize()
    # (bootstrap_core()). Still before the Window, i.e. before the first
    # score.* seed (Window._SeedPanelState / CreateScoreboard): moving it
    # behind the Designer migration (which writes no state) changes nothing
    # the state sees. The boards themselves are not built here:
    # CreateScoreboard() is called later, from the window.
    from .AutonaScoreboardManager import AutonaScoreboardManager
    if AutonaScoreboardManager.instance is None:
        AutonaScoreboardManager.instance = AutonaScoreboardManager()

    logger.debug("bootstrap_gui complete")
