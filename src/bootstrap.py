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
  it builds are QtCore ones (QObject), which a QCoreApplication serves.
  The headless shell (main.py --headless) stops here.
- bootstrap_gui(): the desktop shell's Qt adapters on top of it -- the
  hotkeys, the game asset / country models, the player DB model wiring, the
  alert and updater objects, the boards made and held at launch, the shell
  operations, and the registrations that make the web operations run on the
  GUI thread (the scoreboard dock, through which they ran, went with the Qt
  SCORE in ADR-005 S4ac-3). src/Autona.py runs both, in that order, before
  the Window is built.

Below them, the wiring both shells make once their own pieces exist
(wire_tournament_data / wire_web_server): the window and src/headless.py
call the same functions, so the two cannot drift apart.

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
    # write into. A settings.json it cannot read is read again here, before
    # any of them (one held by another program is waited for about a
    # second; a corrupt one is moved aside), so they start from what it
    # holds.
    from .core.SettingsManager import SettingsManager
    SettingsManager.Initialize()

    # Static asset data with no dependency on anything constructed here:
    # LocaleHelper reads the shipped language tables (src/i18n/mapping.json
    # and assets/data_countries.json), BadWordFilter the ngword lists. They
    # are placed this early because the data is read from all over the
    # widget tree at runtime and there is no reason to make later steps care
    # about the ordering. (The per-run locale itself --
    # LoadLocale/LoadRoundNames -- is the shell's: Window.__init__ or
    # src/headless.py. Its QTranslator is QtCore, so it only needs the
    # application object, of either kind.)
    from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    AutonaLocaleHelper.Initialize()

    # The bad-word filter is core (Qt-free, ADR-005 S3e-1): the program
    # locale and the country -> languages / continent tables it selects the
    # word lists by are AutonaLocaleHelper's, so they are injected as
    # callables -- read at each censor, i.e. after the shell's LoadLocale(),
    # the same deferred-read pattern as the catalogs below. Only the lists
    # themselves are read here.
    from .core.BadWordFilter import BadWordFilter
    if BadWordFilter.instance is None:
        BadWordFilter.instance = BadWordFilter(
            programLocale=lambda: AutonaLocaleHelper.programLocale,
            countryLanguages=AutonaLocaleHelper.GetCountrySpokenLanguages,
            countryContinent=AutonaLocaleHelper.GetCountryContinent)
        BadWordFilter.instance.Initialize()

    # ORDER CONSTRAINT: the background task executor (ADR-005 S3f) before
    # anything that can hand it work -- the tournament provider and the
    # services below, the widgets and the web server the shells build after
    # this function returns. It depends on nothing and starts no thread
    # until the first submit, so it is made this early. Both shells shut it
    # down (Window.closeEvent / HeadlessShell.Shutdown) after the web server
    # stops and before ./out and user_data/state.json are flushed.
    from .core.TaskExecutor import TaskExecutor
    if TaskExecutor.instance is None:
        TaskExecutor.instance = TaskExecutor()

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

    # ORDER CONSTRAINT: the player DB (core, ADR-005 S3e-1) before its
    # writers and readers: the tournament provider below, whose start.gg
    # entrants worker adds players to it, the widgets, the web server and
    # the shells' LoadDB(). It reads nothing here (LoadDB is the shell's) and
    # needs only SettingsManager, read when players are merged.
    # The Qt relay is attached right away, in this stage, because both shells
    # need it: every change the DB announces becomes SetupModel(), i.e.
    # db_updated on the GUI thread, which is what the web server's player DB
    # push listens to (wire_web_server) -- the StateManagerBridge
    # arrangement. The relay is QtCore only (a QObject signal marshalled
    # through the GUI executor, which the shells start before LoadDB); the
    # model it can also rebuild is QtGui and switched on by bootstrap_gui().
    from .core.PlayerDB import PlayerDB
    from .AutonaPlayerDB import AutonaPlayerDB
    if PlayerDB.instance is None:
        PlayerDB.instance = PlayerDB()
    AutonaPlayerDB.Attach()

    # ORDER CONSTRAINT: the GraphQL query texts must be on the class before
    # the first Start.gg request. Loading them here, right above the
    # provider that owns them, keeps the "read files" step next to the only
    # consumer instead of hidden at the bottom of a 2200-line module.
    from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
    StartGGDataProvider.Initialize()

    # The tournament provider (a QObject; its fetches are jobs of the task
    # executor above, ADR-005 S3f-2: QtCore only).
    # It used to connect to the game asset adapter's signals in __init__,
    # which made "adapter before provider" an ORDER CONSTRAINT and the
    # adapter a dependency of every shell. The shell wires that now: it
    # registers how a start.gg videogame id becomes the loaded game
    # (selectGameFromStartGGId) and re-applies the provider's game after a
    # scan -- bootstrap_gui() does both for the desktop (ADR-005 S3c).
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    if AutonaTournamentDataProvider.instance is None:
        AutonaTournamentDataProvider.instance = AutonaTournamentDataProvider()

    # ORDER CONSTRAINT: the Top 8 import (core, ADR-005 S3e-8) reaches the
    # tournament through the provider's jobs, so it is registered
    # right after the provider exists and before anything can ask for an
    # import -- the panel's button, the hotkeys and the web server are all
    # the shell's, after this function returns. The catalogs the import
    # resolves players against are read when it runs, not here.
    from .core import Top8Service
    Top8Service.fetcher = AutonaTournamentDataProvider.instance.RunWithProvider

    # ORDER CONSTRAINT: the match list (この大会の試合一覧, ADR-005 S4j), the
    # same way: its fetch is the provider's jobs, so it is built after the
    # provider, and before anything can ask for a fetch or load a
    # tournament (the provider's Load / UnloadTournament call its Forget;
    # both come from the shells, after this function). `later` (its second
    # job is started from the main thread: a job may not submit one) and
    # the round names (the shell's LoadRoundNames) are read when used.
    from .Helpers import AutonaQtHelper
    from .core.AnalysisService import AnalysisService
    if AnalysisService.instance is None:
        AnalysisService.instance = AnalysisService(
            fetcher=AutonaTournamentDataProvider.instance.RunWithProvider,
            later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
            roundNames=lambda: AutonaLocaleHelper.matchNames)

    # ORDER CONSTRAINT: the set selection's lists (ADR-005 S4v), the same way:
    # after the provider whose jobs fetch them, before anything can ask for a
    # fetch or load a tournament (the provider's Load / UnloadTournament call
    # its Forget).
    from .core.SetSelectionService import SetSelectionService
    if SetSelectionService.instance is None:
        SetSelectionService.instance = SetSelectionService(
            fetcher=AutonaTournamentDataProvider.instance.RunWithProvider)

    # ORDER CONSTRAINT: the statistics' fetcher (ADR-005 S3e-5) hands its
    # requests to the provider above and connects to its stream queue
    # signal, so it is built after it; and before anything that can ask for
    # a statistic -- a player load, a swap, a web route (the Statistics
    # page's too) -- all of which are the shell's, after this function
    # returns. Its QObject must live on the main thread (the answers are
    # queued to it), which is where this runs. Without it the statistics
    # are only cleared, never fetched.
    from .AutonaStatsUtil import AutonaStatsUtil
    from .core.StatsService import StatsService
    if StatsService.fetcher is None:
        StatsService.fetcher = AutonaStatsUtil()

    # ORDER CONSTRAINT: set loading and following (ADR-005 S3e-7). Its
    # fetcher hands the set / station / stream fetches to the provider
    # above, and the stream queue it asks for answers into the statistics'
    # relay (StatsService.UpdateStreamQueue), so it comes after both; and
    # before anything that can load a set -- the board's set and station
    # windows, the web server's load-set / score / pull-stream -- all of
    # which are the shell's, after this function returns. Its QObject and
    # its poll timers live on the main thread, which is where this runs.
    # The board operations run through the board host (the web layer's
    # one BoardHost of both shells since ADR-005 S4ad), read when used: core
    # does not import the web layer.
    from . import AutonaWebOperations
    from .AutonaSetFollowUtil import AutonaSetFollowUtil
    from .core.SetFollowService import SetFollowService
    if SetFollowService.fetcher is None:
        SetFollowService.fetcher = AutonaSetFollowUtil()
    SetFollowService.boardHost = AutonaWebOperations.current_board_host

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

    # ORDER CONSTRAINT: the names the match bar's combos offer (ADR-005
    # S4x), fed by the provider's tournament data and phases. Connected here,
    # after the provider and ahead of every reader: a board's combos re-read
    # it on the same two signals (connected when the board is built, after
    # this function), and on the main thread, where the provider emits both,
    # a direct connection runs in the order it was made -- so the board reads
    # what this kept from the same payload. GET /match-names/candidates reads
    # it in both shells. The localized lists and the round terms are
    # AutonaLocaleHelper's, read when asked (the locale is the shell's, after
    # this function).
    from .core.MatchNameCandidates import MatchNameCandidates
    if MatchNameCandidates.instance is None:
        MatchNameCandidates.instance = MatchNameCandidates(
            generic=lambda: {
                "phase": AutonaLocaleHelper.GetPhaseNameCandidates(),
                "pool": AutonaLocaleHelper.GetPoolNameCandidates(),
                "match": AutonaLocaleHelper.GetMatchNameCandidates()},
            matchNames=lambda: AutonaLocaleHelper.matchNames)
        AutonaTournamentDataProvider.instance.signals \
            .tournament_data_updated.connect(
                MatchNameCandidates.instance.FollowTournamentData)
        AutonaTournamentDataProvider.instance.signals \
            .tournament_phases_updated.connect(
                MatchNameCandidates.instance.FollowPhases)

    # ORDER CONSTRAINT: the tournament's icon / banner (ADR-005 S4f) and the
    # tournament's set / clear after what they reach -- the task executor
    # (the fetch and the admin URL's resolution are its jobs), the game
    # asset catalog (a clear forgets the selected game) and the provider
    # adapter above, which the set / clear drive and whose loaded provider
    # the fetch reads (bound here; core may not import the adapter) -- and
    # before anything can set a tournament: the shells' restore
    # (UiMounted), the sidebar's URL dialog and the web server (the Event
    # page's /api/v1) are all the shell's, after this function returns.
    # Its status is the state's (tournamentInfo.load), seeded with the rest
    # by the shells' launch seed (StateSchema.SeedPanelState), ahead of the
    # restore. The set / clear runs
    # on the main thread through the GUI executor, read when one is
    # applied (both shells make it before calling this).
    from .Helpers import AutonaQtHelper
    from .core.TournamentMediaService import TournamentMediaService
    from .core.TournamentService import TournamentService
    if TournamentMediaService.instance is None:
        TournamentMediaService.instance = TournamentMediaService(
            currentProvider=AutonaTournamentDataProvider.instance.GetProvider)
    if TournamentService.instance is None:
        TournamentService.instance = TournamentService(
            provider=AutonaTournamentDataProvider.instance,
            onMain=lambda fn: AutonaQtHelper.gui_executor(fn))

    # ORDER CONSTRAINT: the 配信設定 operations (ADR-005 S4d) after what
    # they write through and read -- the state layer and AutonaActions (the
    # stream title), the task executor (the thumbnail job) and the provider,
    # whose banner lookup is bound here (its entrants fetch hands the top
    # entrants to StreamService.instance, read when it does). Started, i.e.
    # following the state's tournament name and game, before the shells'
    # launch seed and tournament restore, which the first title is made
    # from: the title is generated whether or not the panel is ever shown,
    # headless too. A refresh is deferred to the main thread's event loop
    # through the GUI executor, read when one is queued: both shells make it
    # before calling this (the test harness after; a refresh queued before
    # it exists is dropped with a warning, and the next change queues
    # again). The output pruning is a Helpers function core may not import,
    # hence injected.
    from .Helpers import AutonaQtHelper
    from .Helpers.AutonaOutputRetention import PruneOutputFiles
    from .core.StreamService import StreamService
    if StreamService.instance is None:
        StreamService.instance = StreamService(
            later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
            bannerUrl=AutonaTournamentDataProvider.instance.GetBannerURL,
            prune=PruneOutputFiles)
        StreamService.instance.Start()

    # ORDER CONSTRAINT: the match thumbnail (ADR-005 S4o), after the settings
    # and the state layer it reads and the executor its saves are jobs of.
    # Nothing is read here; all it does on disk is remove the last session's
    # preview and what a crash left of its temporary files, in directories
    # that are its own. The shell's renderer is reached through the web
    # operations when a run starts, where the desktop has registered it
    # (bootstrap_gui()) and the headless shell has not (503). The deferral,
    # the translations and the locale's names are read when used; the two
    # last are Qt's, and the output pruning and whether the GUI thread is gone
    # are Helpers functions core may not import, hence injected.
    from qtpy.QtCore import QCoreApplication
    from .core.ThumbnailService import ThumbnailService
    if ThumbnailService.instance is None:
        ThumbnailService.instance = ThumbnailService(
            later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
            render=AutonaWebOperations.start_image_render,
            translate=QCoreApplication.translate,
            locale=lambda: (AutonaLocaleHelper.programLocale,
                            AutonaLocaleHelper.matchNames,
                            AutonaLocaleHelper.phaseNames),
            prune=PruneOutputFiles,
            shuttingDown=lambda:
                AutonaQtHelper.gui_unavailable_reason() is not None)

    # ORDER CONSTRAINT: the tournament result (ADR-005 S4q), the same way and
    # after the thumbnail, whose background it follows when it draws (read
    # then, not here). Its standings' import reaches the tournament through
    # the provider's jobs, so it is built after the provider, which it asks
    # when the import runs. The catalog's game switch reaches its
    # ForgetCharacters through the module, not this instance.
    from .core.ResultService import ResultService
    if ResultService.instance is None:
        ResultService.instance = ResultService(
            later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
            render=AutonaWebOperations.start_image_render,
            translate=QCoreApplication.translate,
            locale=lambda: AutonaLocaleHelper.programLocale,
            prune=PruneOutputFiles,
            shuttingDown=lambda:
                AutonaQtHelper.gui_unavailable_reason() is not None,
            fetcher=AutonaTournamentDataProvider.instance.RunWithProvider,
            refreshTournament=(
                AutonaTournamentDataProvider.instance.RefreshTournamentData),
            currentProvider=lambda:
                AutonaTournamentDataProvider.instance.provider)

    # ORDER CONSTRAINT: the country catalog (the Qt-free source of truth for
    # countries, regions and the city -> region lookup, ADR-005 S1c) has to
    # exist before any reader -- the Start.gg parser, the web server. It
    # depends on nothing constructed here. Nothing is read here: the load is
    # the shell's (`CountryCatalog.instance.Load()` in Window.__init__ for the
    # desktop, src/headless.py for headless), after
    # LoadLocale(), because the country names are localized and the locale
    # is only read through the injected callable at load time (the
    # GameAssetCatalog pattern).
    from .core.CountryCatalog import CountryCatalog
    if CountryCatalog.instance is None:
        CountryCatalog.instance = CountryCatalog(
            locales=lambda: (AutonaLocaleHelper.programLocale,
                             AutonaLocaleHelper.exportLocale))

    # ORDER CONSTRAINT: the settings page's settings (ADR-005 S4ae) take
    # what this run starts with -- the web server's port and LAN access, the
    # languages -- when built, to tell a saved change a restart is still to
    # apply. So after SettingsManager.Initialize() and before either shell
    # reads them: the locale (LoadLocale) and the web server are the
    # shell's, after this function returns. The language list (the locale
    # table loaded above) and what the server is bound with are read when
    # asked; the server is imported then, not here.
    from .core.AppSettings import AppSettings

    def listening():
        from .AutonaWebServer import WebServer
        return WebServer.Listening()

    if AppSettings.instance is None:
        AppSettings.instance = AppSettings(
            languages=lambda: AutonaLocaleHelper.languages,
            listening=listening)

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
    Window is built. Needs a QApplication (the asset and player models are
    QtGui's). Safe to call more than once; only the first call does work.

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
    # A save through the settings page (core's HotkeyRules.Update, on a
    # request thread) re-registers the hotkeys: the bridge turns core's
    # channel into hotkeys_changed, and the hotkeys object lives on the GUI
    # thread, so the call is queued there -- RegisterHotKey binds to the
    # calling thread's message queue (ADR-005 S4ae). Idempotent like
    # ConnectActions (its own flag). `applied` tells the page that this
    # shell registers them at all (the headless shell does not).
    from .StateManagerBridge import StateManagerBridge
    from .core import HotkeyRules
    AutonaHotkeys.ConnectReload(StateManagerBridge.instance.hotkeys_changed)
    HotkeyRules.applied = True

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
    # The data and its change relay are bootstrap_core()'s (core/PlayerDB +
    # AutonaPlayerDB.Attach, ADR-005 S3e-1); the Qt adapter is a static
    # class, so there is no instance to create here, only this wiring.
    # ORDER CONSTRAINT: the model (QStandardItemModel + QIcons, i.e. QtGui)
    # is the desktop's, so it is switched on here, before the first
    # SetupModel() -- the window's LoadDB(), whose change announcement the
    # relay turns into one. Without it SetupModel only emits db_updated: the
    # headless shell, where a start.gg entrants fetch still updates the DB
    # from a worker thread (ADR-005 S3c).
    from .AutonaPlayerDB import AutonaPlayerDB
    AutonaPlayerDB.EnableModel()
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

    # ORDER CONSTRAINT: the desktop's boards, all three, made at launch in
    # core (ADR-005 S4ac-1): the restored `score` subtree dropped, each
    # board's launch write, and -- `panel` -- boards 1 / 2 put behind their
    # 反映 button (the display options brought back to the settings and the
    # polling turned off, as a board's first build did). Before S4ac-1 the
    # hold came with a board's widgets, so a board the window never showed
    # was not held; the React SCORE page shows any board without widgets,
    # and is the only SCORE since ADR-005 S4ac-3 (the Qt boards and their
    # dock, AutonaScoreboardManager, which was built right before this, are
    # gone). After bootstrap_core() (StateManager.Initialize() -- the
    # restored state it drops -- the settings it reads, AutonaActions).
    # Before the Window: its _SeedPanelState (one save cycle of its own,
    # which reads no board) and the web server (Window._StartWebServer), so
    # no request reaches a board that is not made or not yet held. The
    # headless shell makes the same boards without `panel`
    # (src/headless._SeedBoards).
    from .core.Actions import AutonaActions
    AutonaActions.instance.MakeBoards(panel=True)

    # The desktop's shell operations (ADR-005 S4y; the scoreboard dock's and
    # its boards' until then). Builds nothing: what opens the SCORE page's
    # set selection is the main window, which hands itself over in
    # UiMounted. (It held the Qt set selection / station windows until
    # ADR-005 S4ac-3.)
    from .AutonaShellOperations import AutonaShellOperations
    if AutonaShellOperations.instance is None:
        AutonaShellOperations.instance = AutonaShellOperations()

    # ORDER CONSTRAINT: the web operations' desktop registrations, after the
    # objects they point at (the shell operations and the asset adapter,
    # both above). A game is loaded through the adapter, so its Qt models
    # follow. The shell operations (the set selection, the thumbnail,
    # ADR-005 S3e-9; the image renderer, S4o) are AutonaShellOperations'
    # (the dock's until S4y), run on the GUI thread. The defaults are the
    # headless shell's (no loader until it registers its own; no shell
    # operation: 503). Before the web server exists
    # (Window._StartWebServer), so no request can reach the defaults on the
    # desktop (ADR-005 S3c). The board operations are not registered: they
    # run on the calling thread under the board host's lock in both shells
    # (AutonaWebOperations.boardHost, ADR-005 S4ad; the desktop registered
    # DesktopBoardHost, which ran them on the GUI thread, until then).
    from . import AutonaWebOperations
    AutonaWebOperations.loadGameByCodename = (
        AutonaGameAssetManager.instance.LoadGameByCodename)
    AutonaWebOperations.openSetSelection = (
        AutonaShellOperations.instance.OpenSetSelection)
    AutonaWebOperations.renderThumbnail = (
        AutonaShellOperations.instance.RenderThumbnail)
    AutonaWebOperations.startImageRender = (
        AutonaShellOperations.instance.StartImageRender)

    # ORDER CONSTRAINT: a game load's roster (players per team, characters
    # per player), its set length and its commit, the wiring the headless
    # shell makes too. On gameLoaded, which the adapter emits just before
    # onLoad, so it runs ahead of every slot on onLoad (the player cards'
    # ReloadCharacters and the boards' _onGameAssetsLoaded were among them
    # until ADR-005 S4ac-3, which is why the adapter committed after onLoad
    # until S4ad).
    wire_game_roster(AutonaGameAssetManager.instance.signals.gameLoaded)

    logger.debug("bootstrap_gui complete")


def wire_tournament_data(on_failure=None):
    """Connect the tournament provider to the state, as both shells need it.

    - tournament_data_updated -> TournamentInfoService.ApplyProviderPayload,
      the tournamentInfo.* writer. `on_failure(data)` is called when a
      payload did not apply: the desktop shows a dialog, the headless shell
      logs. The caller decides where in its own wiring this goes -- the
      window connects the readers of tournamentInfo.* after it.
    - the same payload settles the set's status (TournamentService.
      PayloadApplied, ADR-005 S4g): loaded, or the load failed.

    (Forgetting the selected game when the tournament is cleared used to be
    wired here too, on tournament_url_update with an empty URL, and so only
    for the clears that emitted it. It is core's TournamentService.
    ClearTournament since ADR-005 S4f, for every clear.)

    Was the window's alone until ADR-005 S3c; the headless shell has the
    same provider and needs the same writes. Call once, after
    bootstrap_core().
    """
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from .core.TournamentInfoService import TournamentInfoService
    from .core.TournamentService import TournamentService

    def apply(data):
        applied = TournamentInfoService.instance.ApplyProviderPayload(data)
        # The set's status (tournamentInfo.load, ADR-005 S4g) settles on
        # the payload, whichever way it went.
        if TournamentService.instance is not None:
            TournamentService.instance.PayloadApplied(data, applied)
        if applied:
            return
        if on_failure is not None:
            on_failure(data)

    signals = AutonaTournamentDataProvider.instance.signals
    signals.tournament_data_updated.connect(apply)


def wire_game_roster(game_loaded):
    """Give the 1v1 / team boards a loaded game's roster, as both shells
    need it.

    - `game_loaded` (a Qt signal with the load's GameAssetSnapshot: the
      asset adapter's gameLoaded on the desktop, the headless loader's) ->
      AutonaActions.ApplyGameRoster: each board with a roster goes to the
      game's players per team and the default characters per player, its
      picks emptied when that load replaced another game. In one save cycle
      with AutonaActions.RepublishBestOfs: each board's set length again
      (ADR-005 S4t: it was each built board's onLoad handler, so the
      headless shell did without; the game tracker's rows started over with
      it until ADR-005 S4ac-4). And, in the same save block, the commit of
      both boards (AutonaActions.CommitGameLoad: a load is the fresh
      baseline, not edits waiting behind 反映; ADR-005 S4ad -- the
      desktop's asset adapter committed after onLoad until then, because a
      Qt player card wrote its rows on onLoad, until S4ac-3), even when the
      roster or the set length raises. The commit publishes what the load
      wrote and nothing else (StateManager.RecordHeldWrites: an edit
      pending before the load, a request's while the loader selected the
      game, a core-direct write such as score:adjust while it published,
      all stay behind 反映; until the fix of 2026-11-12 they went out with
      it). Inside a game load's own save cycle (the desktop's: opened on
      the loader thread, closed at the end of _Publish) it only queues the
      boards, and the cycle's close publishes what the load wrote by then
      -- what onLoad's slots wrote after the commit included: _Publish
      records from the rebuild to the close, and this joins it. Headless
      nothing is held, so the commit finds nothing.

    The load's own snapshot, not the catalog's Current(): this slot runs on
    the main thread, which the load hands it to (the desktop's load job
    marshals it there since ADR-005 S3f-4, the headless loader since
    S3f-6),
    and by the time it runs a later load may have replaced the snapshot --
    a switch followed at once by a reload of the new game would read "no
    switch" twice and keep the previous game's picks.

    One board operation (AutonaWebOperations.boardHost: under its lock, on
    the main thread this runs on, in both shells since ADR-005 S4ad -- the
    desktop's ran on its GUI thread, which is the main thread, under no
    lock until then; inside the desktop load's own operation, _Publish), so
    no other board operation lands between the roster and the commit. Until ADR-005 S3e-6 it was each built board's onLoad
    handler, so the headless shell's boards never followed a game load.
    bootstrap_gui() makes it for the desktop, src/headless.py for its
    shell.
    """
    from . import AutonaWebOperations
    from .core.Actions import AutonaActions
    from .core.StateManager import StateManager

    def apply(snapshot):
        replaced = getattr(snapshot, "replaced", None)

        def run():
            # The load's writes, told apart from everyone else's: the
            # desktop's _Publish records from the rebuild to the cycle's
            # close, which this joins (onLoad's slots go with them).
            with StateManager.RecordHeldWrites() as written:
                with StateManager.SaveBlock():
                    try:
                        AutonaActions.instance.ApplyGameRoster(replaced)
                        AutonaActions.instance.RepublishBestOfs()
                    finally:
                        # Committed whatever the roster did: a load's
                        # writes (what the roster wrote before failing) are
                        # not left waiting behind 反映 (the asset adapter's
                        # _Publish committed on every path until ADR-005
                        # S4ad).
                        AutonaActions.instance.CommitGameLoad(written)

        AutonaWebOperations.boardHost.Run(run)

    game_loaded.connect(apply)


def wire_web_server(webserver, game_changed):
    """Connect what the web server pushes on its own, as both shells need it.

    - `game_changed` (a Qt signal: the window's GameChanged, the headless
      loader's) -> webserver.ws_game_changed: the full state and the new
      character list to every client.
    - AutonaPlayerDB.signals.db_updated -> webserver.ws_playerdb: the player
      DB to the connections allowed to read it.

    Connected after the server exists, i.e. after LoadDB(): nothing is lost,
    clients pull both on (re)connect. Was the window's alone until ADR-005
    S3c.
    """
    from .AutonaPlayerDB import AutonaPlayerDB

    game_changed.connect(webserver.ws_game_changed)
    AutonaPlayerDB.signals.db_updated.connect(webserver.ws_playerdb)
