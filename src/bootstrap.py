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
  tournament provider, the game loader with its registrations and the
  player DB's refresh after a load (both shells' since ADR-005 S-final
  SF-6c), and the plugins' parts (ADR-009 E.1). Nothing
  here needs a QGuiApplication: the Qt objects it builds are QtCore ones
  (QObject), which a QCoreApplication serves. The headless shell (main.py
  --headless) stops here.
- bootstrap_gui(): the desktop shell's Qt adapters on top of it -- the
  hotkeys, the updater, the boards made and held at launch, the shell
  operations (the plugins' picks and links among them), and their
  registrations, which the web operations run on the
  GUI thread (the scoreboard dock, through which they ran, went with the Qt
  SCORE in ADR-005 S4ac-3; the game asset loader adapter went in S-final
  SF-6c). src/Autona.py runs both, in that order, before the Window is
  built.

Below them, the wiring both shells make once their own pieces exist
(wire_tournament_data / wire_web_server): the window and src/headless.py
call the same functions, so the two cannot drift apart. The plugin v1
routes' parts (plugin_api_deps) are read by the web server when it is made.

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

# The plugins' user_data folder (their settings, secrets and dev.json are
# below it), against the working directory as the other stores' are.
USER_DATA_DIR = "./user_data"
# The plugin routes' artifact reveal (AutonaPluginApi.PluginApiDeps
# .revealFile): the desktop's, set by bootstrap_gui(); None in the headless
# shell (its SHELL routes answer 403 anyway). Read by plugin_api_deps().
_plugin_reveal_file = None
# The plugins' golden inputs (ADR-009 D.6: a dev plugin's sample run of a
# hook), through AutonaResolve: the checkout's docs/, a release's
# _internal/ (packaging/autona.spec bundles them).
PLUGIN_SAMPLES_DIR = "./docs/plugins/inputs"


def bootstrap_core(headless=False):
    """Load the core data and create the singletons every shell depends on.

    `headless`: the headless shell's (src/headless.py), which runs no plugin
    hook (ADR-009 D.7: the hooks' dispatcher is not made, the match log is
    given none). The desktop calls it without (src/Autona.py, and
    bootstrap_gui() as its guard).

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

    # The layout catalog's folder (core/LayoutCatalog.Root, ADR-007 T1):
    # layout/ under the directory the app started in, read at each use --
    # the one folder the result's and the thumbnail's services, their
    # renderers and the routes read, whatever the working directory is a
    # moment later. Depends on nothing; before anything can list a design.
    from .core import LayoutCatalog
    from .Helpers import AutonaDirHelper
    LayoutCatalog.SetAppDirectory(lambda: AutonaDirHelper.current_dir)

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
    # through the GUI executor, which the shells start before LoadDB). Before
    # the emit it normalises the selected game's mains (PlayerDB
    # .NormaliseMains, the Qt model clean-up's PR3), reading the selection
    # from GameAssetCatalog.instance, made above.
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
    # that *reads* tournamentInfo.* connects to that signal -- the Qt
    # sidebar's tournament button was one such reader (until ADR-005 S-final
    # SF-5b-3), and it used to be correct only because the EVENT panel
    # happened to be constructed earlier in Window.__init__ than the button.
    # The panel is lazy since headless core Step 4b, so the writer lives here
    # instead. The shells make the actual connection (wire_tournament_data);
    # this only has to exist first. The date format is injected
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
    # applied (both shells make it before calling this). A tournament newly
    # loaded turns the mods off (ADR-005 S-final SF-5b-1; the Qt window did
    # it until then): through the web operations' turn_mods_off, which reads
    # the loader's TurnModsOff the shell registers (bootstrap_gui() /
    # src/headless.Assemble, both after this) when it is called -- before
    # that, none is registered and it logs.
    from .core.TournamentMediaService import TournamentMediaService
    from .core.TournamentService import TournamentService
    if TournamentMediaService.instance is None:
        TournamentMediaService.instance = TournamentMediaService(
            currentProvider=AutonaTournamentDataProvider.instance.GetProvider)
    if TournamentService.instance is None:
        TournamentService.instance = TournamentService(
            provider=AutonaTournamentDataProvider.instance,
            onMain=lambda fn: AutonaQtHelper.gui_executor(fn),
            turnModsOff=lambda: AutonaWebOperations.turn_mods_off())

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
    # (bootstrap_gui()) and the headless shell has not (503). The deferral
    # and the locale and its names are read when used; the locale is the
    # shell's (LoadLocale, after this function returns) and the language of
    # the service's own words (core/CoreWords, ADR-005 S-final SF-7: Qt's
    # translations until then), and the output pruning and whether the GUI
    # thread is gone are Helpers functions core may not import, hence
    # injected.
    from .core.ThumbnailService import ThumbnailService
    if ThumbnailService.instance is None:
        ThumbnailService.instance = ThumbnailService(
            later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
            render=AutonaWebOperations.start_image_render,
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
            locale=lambda: AutonaLocaleHelper.programLocale,
            prune=PruneOutputFiles,
            shuttingDown=lambda:
                AutonaQtHelper.gui_unavailable_reason() is not None,
            fetcher=AutonaTournamentDataProvider.instance.RunWithProvider,
            refreshTournament=(
                AutonaTournamentDataProvider.instance.RefreshTournamentData),
            currentProvider=lambda:
                AutonaTournamentDataProvider.instance.provider,
            # Each result image saved: the hooks' result_image_saved
            # (ADR-009 D.2), read when told -- the dispatcher is made
            # below, and not at all headless.
            saved=_result_image_saved)

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

    # ORDER CONSTRAINT: the game asset packs -- the upstream index, the
    # download and the imports (ADR-005 S-final SF-3b) -- after the executor
    # its jobs run on and the game asset catalog it lists and scans into.
    # Nothing is read or fetched here. The scan that follows an import is
    # the game loader's, set right below, before anything can start a job
    # (the web server is the shell's, after this function returns). The
    # games a job moved aside and did not put back are put back by the
    # loader's first scan job, before it scans (RestoreReplaced; the loader
    # is started by each shell: the window's UiMounted, src/headless
    # .Assemble), so after this; it does not wait for a job, which does the
    # same at its start.
    from .core.AssetPackService import AssetPackService
    if AssetPackService.instance is None:
        AssetPackService.instance = AssetPackService()

    # ORDER CONSTRAINT: the game loader -- the scans, the loads, the last
    # pick restored (core's GameLoader) -- one for both shells (ADR-005
    # S-final SF-6c; each shell's adapter built its own, around its Qt
    # signals, until then: bootstrap_gui()'s AutonaGameAssetManager and
    # src/headless's HeadlessGames). After the catalog it loads, the
    # provider whose game it re-applies after every scan (`afterScan`, on
    # the main thread ahead of the startup restore, so the restore wins:
    # tests/test_bootstrap_split.py pins it on a real scan), the bridge
    # that turns its notifications into signals (game_loaded,
    # games_scanned) and the asset packs whose scan it is. Nothing runs
    # until a shell starts it (GameLoader.Start).
    from .core.GameLoader import GameLoader
    if GameLoader.instance is None:
        GameLoader.instance = make_game_loader(
            GameAssetCatalog.instance,
            afterScan=AutonaTournamentDataProvider.instance.SetGameFromProvider)

    # The loader's registrations, the same in both shells (each shell made
    # them for its adapter's loader until ADR-005 S-final SF-6c): the
    # selector a tournament's start.gg videogame id loads through (without
    # it the provider logs that the game was not applied), the web
    # operations' loads and the mods turned off when a tournament is newly
    # loaded (ADR-005 S-final SF-5b-1), and the scan that follows an asset
    # pack's install or import -- run on the import's own job, which may
    # not submit another (TaskExecutor's NestedSubmitError). Before the
    # provider can load a tournament and before the web server takes a
    # request (both the shells', after this function returns).
    AutonaTournamentDataProvider.instance.selectGameFromStartGGId = (
        GameLoader.instance.SelectGameFromStartGGId)
    AutonaWebOperations.loadGameByCodename = (
        GameLoader.instance.LoadGameByCodename)
    AutonaWebOperations.turnModsOff = GameLoader.instance.TurnModsOff
    AssetPackService.instance.scan = GameLoader.instance.Scan

    # The player DB's refresh follows a game load: SetupModel normalises the
    # loaded game's mains (PlayerDB.NormaliseMains) and pushes the DB
    # (db_updated), on the main thread (gui_thread_async), queued behind
    # the load's delta: the bridge's game_loaded is emitted on the loader's
    # thread. ORDER CONSTRAINT: connected before the web server's push
    # (bootstrap.wire_web_server, the shells' later), which ran after it on
    # the shells' announcement too; and before a shell starts the loader,
    # so the first load normalises and pushes as every later one does.
    # (The adapter's onLoad / HeadlessGames' gameChanged until ADR-005
    # S-final SF-6c; bootstrap_gui() connected the desktop's, inside the
    # load's board operation and save cycle.)
    StateManagerBridge.instance.game_loaded.connect(AutonaPlayerDB.SetupModel)

    # ORDER CONSTRAINT: the plugins (ADR-009 E.1) -- the runner, the shell's
    # picks, the secrets and settings, the registry, the input sources, the
    # runs, the hooks' dispatcher (the desktop's), the installer and the
    # installs -- after SettingsManager (./user_data, the work folder
    # archive.work_dir) and the task executor; before ArchiveJobService
    # below (the built-in vodsplit is the runner's run on the heavy lane
    # PluginJobs owns: ADR-009 D9), before MatchLogService (the hooks'
    # dispatcher goes between the runs and the match log, which hands it its
    # candidates) and before the web server that serves their routes (the
    # shells', after this function returns: WebServer.__init__ adds the
    # routes over plugin_api_deps()). None imports Qt or flask; making them
    # starts no process or thread (the sweep's job below does run on an
    # executor lane; the dispatcher's thread is started after the scan,
    # below). What they read when used -- the match log's flush and open
    # record, the archive's work folder and last probe, the runs for the
    # list's state, the server's address, the locale -- is read through
    # callables, so the match log and ArchiveJobService (made below) and the
    # locale (the shell's, after this function) are not needed yet. The
    # parts are in dependency order: settings over secrets and the picks
    # (a file / folder value is a pick id: ShellPicks.Resolve, L10), the
    # registry over settings (its needs_settings) and the runner (a dev
    # plugin's describe), the runs over all of them, the installer over the
    # runs (its hold) and the installs over the installer; the hooks over
    # the runs (their start, the light lane) and before the installer and
    # the installs (both forget and refresh them: a new install, a
    # reconsent, a block, a removal); the runs tell the hooks of a hook's
    # run's end and the light lane freed through functions that read the
    # dispatcher when called (made after them); the registry
    # reads the installer and the installs back through lambdas (enabled,
    # install.json's source, the blocklist). The user_data folder is
    # made absolute here, once: every part names the same folder whatever
    # the working directory is later.
    import os

    from .core import MatchLogService as matchLog
    from .core.PluginHooks import PluginHooks
    from .core.PluginInputs import PluginInputs
    from .core.PluginInstaller import PluginInstaller
    from .core.PluginInstalls import PluginInstalls
    from .core.PluginJobs import PluginJobs
    from .core.PluginRegistry import PluginRegistry
    from .core.PluginRunner import PluginRunner
    from .core.PluginSecrets import PluginSecrets
    from .core.PluginSettings import PluginSettings
    from .core.PluginTypes import Lane
    from .core.ShellPicks import ShellPicks
    from .Helpers.AutonaDirHelper import AutonaResolve
    hostVersion = _program_version() or ""
    userData = os.path.abspath(USER_DATA_DIR)
    # The golden inputs (docs/plugins/inputs/; a release has them under
    # _internal/): a dev plugin's sample run of a hook (ADR-009 D.6).
    samplesDir = AutonaResolve(PLUGIN_SAMPLES_DIR)
    if PluginRunner.instance is None:
        PluginRunner.instance = PluginRunner()
    if ShellPicks.instance is None:
        ShellPicks.instance = ShellPicks()
    if PluginSecrets.instance is None:
        PluginSecrets.instance = PluginSecrets(userData)
    if PluginSettings.instance is None:
        PluginSettings.instance = PluginSettings(
            userData, PluginSecrets.instance,
            resolvePick=ShellPicks.instance.Resolve)
    if PluginRegistry.instance is None:
        PluginRegistry.instance = PluginRegistry(
            str(AutonaDirHelper.current_dir), userData, hostVersion,
            describe=PluginRunner.instance.Describe,
            isRunning=lambda pluginId: PluginJobs.instance is not None
                and PluginJobs.instance.Running(pluginId),
            settingsMissing=PluginSettings.instance.Missing,
            builtinVersion=_vodsplit_version,
            # The installer's and the installs' (made below): install.json,
            # enabled.json, the blocklist in effect.
            installSource=_plugin_install_source,
            isEnabled=lambda pluginId: PluginInstaller.instance is None
                or PluginInstaller.instance.IsEnabled(pluginId),
            blockedBy=lambda pluginId, version:
                PluginInstalls.instance.BlockedReason(pluginId, version)
                if PluginInstalls.instance is not None else None)
    if PluginInputs.instance is None:
        PluginInputs.instance = PluginInputs(
            outDir="./out", matchLogDir=matchLog.MATCH_LOG_DIR,
            workDir=_archive_work_dir,
            publishedState=lambda: StateManager.GetFullState()["state"],
            flushMatchLog=lambda: matchLog.MatchLogService.instance.Flush(
                timeout=2.0),
            openRecordDate=lambda: matchLog.MatchLogService.instance
                .OpenRecordDate(),
            # A dev plugin's sample run of a result_image_saved hook hands
            # the stand-in image of the golden inputs (ADR-009 D.6).
            samplesDir=samplesDir)
    if PluginJobs.instance is None:
        PluginJobs.instance = PluginJobs(
            runner=PluginRunner.instance, registry=PluginRegistry.instance,
            inputs=PluginInputs.instance, settings=PluginSettings.instance,
            secrets=PluginSecrets.instance, workDir=_archive_work_dir,
            userDataDir=userData, hostVersion=hostVersion,
            locale=lambda: AutonaLocaleHelper.programLocale,
            apiUrl=_plugin_api_url,
            # The hooks' dispatcher's (ADR-009 D.3-D.5): a hook's run's end
            # counted, the light lane freed tried for the pending hooks.
            onRunEnded=_plugin_run_ended,
            onLaneFreed=_plugin_lane_freed)
    # The hooks' dispatcher (ADR-009 D5, appendix D), the desktop's alone:
    # the headless shell runs no hook (D.7: nothing to enable them, see
    # their failures or stop them with), so it has none, and the match log
    # and the routes get None. Over the registry (the hook jobs), the runs
    # (its start; the light lane full), the settings (a run's request.json)
    # and the picks (a file / folder value). Making it reads nothing; its
    # thread is started after the scan, below.
    if not headless and PluginHooks.instance is None:
        PluginHooks.instance = PluginHooks(
            userData, PluginRegistry.instance, PluginJobs.instance.Start,
            busy=lambda: PluginJobs.instance.BusyFor(Lane.LIGHT) is not None,
            settings=_plugin_hook_settings,
            resolvePick=ShellPicks.instance.Resolve,
            samplesDir=samplesDir)
    # The installs (P5a, wiring I4): the installer over the registry's
    # check and describe, the runs' hold (the swap and a removal), the
    # secrets' and the hooks' Forget (a new install, a reconsent, a
    # removal: the installer; a block: the installs) and the hooks'
    # Refresh after a scan (the installs); the coordinator the routes drive
    # (its prepare, commit and removal are executor jobs). Making them reads
    # nothing; the leftovers' sweep is the launch job below.
    if PluginInstaller.instance is None:
        PluginInstaller.instance = PluginInstaller(
            str(AutonaDirHelper.current_dir), userData,
            registry=PluginRegistry.instance, runner=PluginRunner.instance,
            hostVersion=hostVersion, secrets=PluginSecrets.instance,
            hooks=PluginHooks.instance,
            jobs=PluginJobs.instance, workDir=_archive_work_dir,
            isDevId=lambda pluginId: any(
                folder.plugin_id == pluginId
                for folder in PluginRegistry.instance.DevFolders()),
            blocklist=lambda: PluginInstalls.instance.Blocklist(),
            stopping=TaskExecutor.instance.stopping.is_set)
    if PluginInstalls.instance is None:
        PluginInstalls.instance = PluginInstalls(
            PluginInstaller.instance, PluginRegistry.instance,
            jobs=PluginJobs.instance, hooks=PluginHooks.instance)

    # The plugins at launch (ADR-009 E.2): every manifest read, nothing
    # started. The scan is a few stats and the built-in's folder walked once
    # (measured: 10-30 ms for an installed vodsplit's 212 files, 412 MB),
    # so it runs here, on this thread: the list is whole before the web
    # server serves it. The risk taken: a dev folder (dev.json's while the
    # developer toggle is on, or AUTONA_PLUGIN_DEV's) on a network share or
    # an unplugged drive holds the launch until its reads time out -- a
    # developer's own setup; with the toggle off dev.json's folders are not
    # read at all. A scan that raises (a third party's manifest broke what
    # the check did not catch) is logged and the launch goes on: the list
    # is then the built-in alone until the next scan. (The sweep of the runs
    # left 'running' follows ArchiveJobService, below.)
    try:
        PluginRegistry.instance.Scan()
    except Exception:
        logger.opt(exception=True).error("Plugins: the launch's scan failed")
    # The blocklist in effect (ADR-009 C.4): the listing's previous copy,
    # else the one shipped with Autona (fetching it is P5b's). A few KB
    # read here, after the scan: an installed plugin it blocks is turned
    # off (its hooks too: PluginHooks.Forget, BLOCKED) before anything can
    # start it.
    from .core.PluginInstalls import (BUNDLED_LISTING, LISTING_COPY,
                                      ReadListingBlocklist)
    try:
        PluginInstalls.instance.SetBlocklist(ReadListingBlocklist((
            os.path.join(userData, "plugins", LISTING_COPY),
            AutonaResolve(BUNDLED_LISTING))))
    except Exception:
        logger.opt(exception=True).error("Plugins: the blocklist failed")
    # The hooks' dispatcher started (the desktop's): the enabled hooks read
    # (each plugin with a hook job's hooks.json -- after the scan, which
    # says which, and the blocklist, which turned a blocked one's off) and
    # its thread started. ORDER CONSTRAINT: before MatchLogService below
    # (its first candidate) and the shells' web server; nothing triggers a
    # hook before then. A failure is logged and the launch goes on: no hook
    # runs until the next refresh reads them.
    if PluginHooks.instance is not None:
        try:
            PluginHooks.instance.Start()
        except Exception:
            logger.opt(exception=True).error(
                "Plugins: the hooks could not be started")

    # ORDER CONSTRAINT: the archive clipper's jobs (ADR-006 A1), after
    # SettingsManager (its work folder is archive.work_dir) and the plugin
    # runner and runs above (ADR-009 E.1 / D9: a job's vodsplit is the
    # runner's run, holding PluginJobs' heavy lane), and before the
    # web server that starts and reads them (the shells', after this
    # function returns); each shell's close stops its worker (Shutdown,
    # before PluginJobs', the runner's and the executor's shutdown). Depends on nothing else and nothing
    # depends on it: not in the state (D6). Nothing is read, swept or
    # probed here -- the work folder at the first job request, the worker
    # at the first status.
    from .core.ArchiveJobService import ArchiveJobService
    if ArchiveJobService.instance is None:
        ArchiveJobService.instance = ArchiveJobService(
            runner=PluginRunner.instance, lanes=PluginJobs.instance)

    # The plugin runs a crash left 'running' marked failed, and each
    # plugin's kept runs pruned (ADR-009 E.2: PluginJobs.Sweep). ORDER
    # CONSTRAINT: after ArchiveJobService, whose WorkDir names the work
    # folder the runs are under, and after the registry's scan above (it
    # asks which plugin is a dev one). A job of the executor, not a step
    # here: the work folder may be on a sleeping or unplugged drive, and
    # the launch does not wait for it (the archive's own jobs read it only
    # when the page asks). What the sweep leaves alone: a run of this
    # process (active, or started after PluginJobs was made) and a run of
    # another Autona still alive (its job.json names its host). It stops
    # between plugins once the executor is stopping, so a slow drive does
    # not hold the close either. First in the same job, the installs'
    # leftovers (PluginInstalls.Sweep: the swaps' .new / .old / .part, the
    # uploads, a removal a crash ended; a scan when a folder was put back):
    # the plugins folder is beside Autona, so it is quick, and uploads and
    # installs are refused until it has run.
    TaskExecutor.instance.submit(
        lambda: (PluginInstalls.instance.Sweep(), PluginJobs.instance.Sweep(
            stopping=TaskExecutor.instance.stopping.is_set)),
        task_name="plugins' sweep")

    # ORDER CONSTRAINT: the archive worker's installer (ADR-006 A7), after
    # ArchiveJobService (it reads the installed version from the jobs' last
    # probe and holds them still while it swaps the worker's folder) and the
    # executor (its release check and its install are jobs), before the web
    # server (the shells'): the page reads and starts it. Not in the state.
    # Nothing is fetched here: the releases are read when the page asks.
    # What an install a crash ended left (the download, the unpacked
    # folder, the old worker moved aside) is cleaned now, before anything
    # can run the worker: a few existence checks when there is none.
    from .core.ArchiveWorkerInstaller import ArchiveWorkerInstaller
    if ArchiveWorkerInstaller.instance is None:
        ArchiveWorkerInstaller.instance = ArchiveWorkerInstaller(
            jobs=ArchiveJobService.instance, autonaVersion=_program_version)
        ArchiveWorkerInstaller.instance.CleanLeftovers()

    # ORDER CONSTRAINT: the match log (ADR-006 A2), a state observer, after
    # StateManager.Initialize(): LoadState's big change is not seen (the
    # board restored is last session's, and both shells' launch seed resets
    # it -- that reset is the first publish it follows), and its directory
    # is fixed against the working directory the store's is. Started here,
    # i.e. following board 1's publishes, before the shells' launch seed,
    # tournament restore and web server, headless too. It reads and queues
    # inside the observer and writes on a thread of its own; each shell's
    # close stops it (Shutdown, after the web server and the executor, which
    # can still publish). Nothing reads or writes through it. ORDER
    # CONSTRAINT: after the hooks' dispatcher (the desktop's, above), which
    # its observer queues match_decided's candidates to (ADR-009 E.1).
    from .core.MatchLogService import MatchLogService
    if MatchLogService.instance is None:
        # The hooks' dispatcher it tells of match_decided's candidates
        # (ADR-009 D.1; None headless: nothing told).
        MatchLogService.instance = MatchLogService(hooks=PluginHooks.instance)
        MatchLogService.instance.Start()

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
    Window is built, under the desktop's QApplication (what it wires serves
    the window: the hotkeys, the alert and updater dialogs). It built QtGui
    models too -- the asset, country and player models -- until the Qt model
    clean-up removed them (PR2-PR4). Safe to call more than once; only the
    first call does work.

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

    # The Updater depends on nothing constructed above: its __init__ only
    # builds the object itself. It is the desktop's: it replaces the
    # installed exe. (AutonaAlertNotification, which was built right before
    # it, is gone: nothing ever emitted its `alerts` signal -- ADR-005 SF-1.)
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
    # objects they point at (the shell operations, above). The shell
    # operations (the set selection, the thumbnail, ADR-005 S3e-9; the
    # image renderer, S4o) are AutonaShellOperations' (the dock's until
    # S4y), run on the GUI thread. The defaults are the headless shell's (no
    # shell operation: 503). Before the web server exists
    # (Window._StartWebServer), so no request can reach the defaults on the
    # desktop (ADR-005 S3c). The game loader's registrations are
    # bootstrap_core()'s, the same in both shells (ADR-005 S-final SF-6c;
    # this shell registered its adapter's loader here until then). The
    # board operations are not registered: they run on the calling thread
    # under the board host's lock in both shells
    # (AutonaWebOperations.boardHost, ADR-005 S4ad; the desktop registered
    # DesktopBoardHost, which ran them on the GUI thread, until then).
    from . import AutonaWebOperations
    AutonaWebOperations.openSetSelection = (
        AutonaShellOperations.instance.OpenSetSelection)
    AutonaWebOperations.renderThumbnail = (
        AutonaShellOperations.instance.RenderThumbnail)
    AutonaWebOperations.startImageRender = (
        AutonaShellOperations.instance.StartImageRender)

    # The plugin side of the shell requests (ADR-009 D4, L10): pickFile and
    # pickFolder for a plugin hold their picks in core's ShellPicks (which
    # PluginSettings and the plugin routes resolve them with), and
    # openPluginLink reads the installed plugins' manifests from the
    # registry. ORDER CONSTRAINT: after the shell operations (above) and
    # bootstrap_core()'s plugin parts; before the window, whose shared view
    # sends the requests. The listing's lookup comes with the listing (P5b).
    from .core.PluginRegistry import PluginRegistry
    from .core.ShellPicks import ShellPicks
    AutonaShellOperations.instance.UsePlugins(
        picks=ShellPicks.instance, registry=PluginRegistry.instance)

    # The plugin routes' artifact reveal (ADR-009 D4), the desktop's: read
    # by plugin_api_deps() when the web server is made
    # (Window._StartWebServer, after this function). The headless shell
    # leaves it None: its SHELL routes answer 403 anyway.
    global _plugin_reveal_file
    _plugin_reveal_file = _reveal_plugin_file

    logger.debug("bootstrap_gui complete")


def wire_tournament_data(on_failure=None):
    """Connect the tournament provider to the state, as both shells need it.

    - tournament_data_updated -> TournamentInfoService.ApplyProviderPayload,
      the tournamentInfo.* writer. `on_failure(data)` is called when a
      payload did not apply: the headless shell logs; the desktop passes
      none (the pages tell it from the state's tournamentInfo.load: the
      AppShell's notice, the Event page's card -- ADR-005 S-final SF-5b-2;
      the window's dialog went in SF-5b-3).
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


def _program_version():
    """Autona's version (assets/versions.json's "program", None when it
    cannot be read): what a worker release's min_autona is compared with
    (core/ArchiveWorkerInstaller.py). Through AutonaResolve, as the window
    title and /health read it (a release has the file under _internal/)."""
    import json

    from .Helpers.AutonaDirHelper import AutonaResolve
    try:
        with open(AutonaResolve("./assets/versions.json"),
                  encoding="utf-8") as f:
            return json.load(f).get("program")
    except (OSError, ValueError):
        return None


def plugin_api_deps():
    """The plugin v1 routes' parts (AutonaPluginApi.PluginApiDeps), from
    what bootstrap_core() made (a part not made yet is None: its routes
    answer 503: the hooks headless) and the desktop's reveal
    (bootstrap_gui()). WebServer.__init__ hands them to
    RegisterPluginRoutes before it registers the v1 blueprint, so flask
    and the routes are imported with the web server, after the window is
    shown (ADR-003), not at bootstrap. A new object per call, read per
    request: what a later stage adds is added here."""
    from .AutonaPluginApi import PluginApiDeps
    from .core.PluginHooks import PluginHooks
    from .core.PluginInputs import PluginInputs
    from .core.PluginInstalls import PluginInstalls
    from .core.PluginJobs import PluginJobs
    from .core.PluginRegistry import PluginRegistry
    from .core.PluginSecrets import PluginSecrets
    from .core.PluginSettings import PluginSettings
    from .core.ShellPicks import ShellPicks
    picks = ShellPicks.instance
    return PluginApiDeps(
        registry=PluginRegistry.instance, settings=PluginSettings.instance,
        secrets=PluginSecrets.instance, inputs=PluginInputs.instance,
        jobs=PluginJobs.instance, installs=PluginInstalls.instance,
        hooks=PluginHooks.instance,
        resolvePick=picks.Resolve if picks is not None else None,
        workDir=_archive_work_dir, revealFile=_plugin_reveal_file)


def _plugin_hooks():
    """The hooks' dispatcher (the desktop's; None headless or before
    bootstrap_core() made it)."""
    from .core.PluginHooks import PluginHooks
    return PluginHooks.instance


def _plugin_run_ended(record):
    """PluginJobs' onRunEnded: a hook's run's end, counted by the
    dispatcher (ADR-009 D.5: failures, history, the last error). Another
    run's end tells it nothing. Outside PluginJobs' lock; only queues."""
    from .core.PluginTypes import TriggerKind
    hooks = _plugin_hooks()
    if hooks is not None and record.trigger.kind is TriggerKind.HOOK:
        hooks.RunEnded(record.plugin, record.trigger.hook, record)


def _plugin_lane_freed(lane):
    """PluginJobs' onLaneFreed: the light lane free, the pending hooks tried
    (ADR-009 D.3). The heavy lane runs no hook. Only queues."""
    from .core.PluginTypes import Lane
    hooks = _plugin_hooks()
    if hooks is not None and lane == Lane.LIGHT:
        hooks.LaneFreed()


def _plugin_hook_settings(pluginId):
    """The hooks' settings(pluginId): the plugin's settings for a hook run's
    request.json, as a manual run's route reads them (PluginSettings.Get
    over the registry's manifest; a dev plugin's own). {} when it has no
    usable manifest (the start then refuses it)."""
    from .core.PluginRegistry import PluginRegistry
    from .core.PluginSettings import PluginSettings
    from .core.PluginTypes import Badge
    registered = PluginRegistry.instance.Get(pluginId)
    manifest = (registered.report.manifest
                if registered is not None else None)
    if manifest is None:
        return {}
    return PluginSettings.instance.Get(
        pluginId, manifest, dev=registered.badge == Badge.DEV)


def _result_image_saved(path):
    """ResultService's saved(path): one result image saved to out/results,
    the hooks' result_image_saved (ADR-009 D.2). The dispatcher queues it
    only when a hook on it is enabled (D.7); headless there is none."""
    hooks = _plugin_hooks()
    if hooks is not None:
        hooks.ResultImageSaved(path)


def _plugin_install_source(pluginId):
    """The registry's installSource: an installed plugin's install.json
    source (None: manual), read through the installer once it is made."""
    from .core.PluginInstaller import PluginInstaller
    if PluginInstaller.instance is None:
        return None
    record = PluginInstaller.instance.ReadInstallRecord(pluginId)
    return record.source if record is not None else None


def _archive_work_dir():
    """The work folder now (ArchiveJobService.WorkDir: archive.work_dir,
    else the default), read at each use: the plugin runs' folders are below
    it (ADR-009 A.8). Called only once bootstrap_core() has made the
    archive's jobs."""
    from .core.ArchiveJobService import ArchiveJobService
    return ArchiveJobService.instance.WorkDir()


def _vodsplit_version():
    """The built-in vodsplit's version, as the archive page last probed it
    ("" before a probe): the plugin list's row (PluginRegistry)."""
    from .core.ArchiveJobService import ArchiveJobService
    jobs = ArchiveJobService.instance
    probe = jobs.CachedProbe() if jobs is not None else None
    return (probe.version or "") if probe is not None else ""


def _plugin_api_url():
    """The v1 base URL a plugin declaring autona_api read / write is given
    (AUTONA_API_URL, ADR-009 A.7): this PC's loopback at the port the server
    is reached at (WebServer.Serving, the one reader)."""
    from .AutonaWebServer import WebServer
    port, _lan = WebServer.Serving()
    return f"http://127.0.0.1:{port}/api/v1"


def _reveal_plugin_file(path):
    """A plugin artifact's "show in Explorer" (the plugin routes'
    revealFile): the file selected in its folder, as LayoutCatalog shows
    its own (quoted for Explorer, no shell); False when it was not shown.
    The desktop's alone (bootstrap_gui())."""
    from .core import LayoutCatalog
    try:
        return LayoutCatalog.RevealFile(path)
    except OSError:
        return False


def make_game_loader(catalog, afterScan=None):
    """The game loader (core's GameLoader, ADR-005 S-final SF-6b), with the
    hooks both shells give it: the board host (read when used, so a test
    that gives the operations a host of its own gives it to the loads too),
    the main thread's event loop (GuiExecutor's later: what follows a scan),
    why it would not run a call (gui_unavailable_reason: the app is going
    away), and whether this is it. `afterScan()`: the provider's game
    re-applied after a scan.

    Called by bootstrap_core() for GameLoader.instance (each shell's adapter
    called it until ADR-005 S-final SF-6c, importing it lazily from this
    module; until then it also took the shell's notifications and a marshal
    onto the main thread, run_sync). Builds the loader only: nothing runs
    until Start().
    """
    from . import AutonaWebOperations
    from .Helpers import AutonaQtHelper
    from .core.GameLoader import GameLoader

    return GameLoader(
        catalog,
        runOnBoard=lambda op, **kwargs:
            AutonaWebOperations.current_board_host().Run(op, **kwargs),
        later=lambda fn: AutonaQtHelper.gui_executor.later(fn),
        unavailable=lambda: AutonaQtHelper.gui_unavailable_reason(),
        isMainThread=lambda: AutonaQtHelper.in_gui_thread(),
        afterScan=afterScan)


def wire_web_server(webserver):
    """Connect what the web server pushes on its own, as both shells need it.

    - StateManagerBridge.game_loaded (core's GameLoader.loaded: a load is
      published; the shells passed their loader adapter's onLoad /
      gameChanged until ADR-005 S-final SF-6c, and the window relayed onLoad
      through a GameChanged of its own until SF-6b) ->
      webserver.ws_game_changed: the full state and the new character list
      to every client, after the load's delta. Emitted on the loader's
      thread, so queued to the main thread (after the player DB's refresh,
      connected by bootstrap_core()).
    - StateManagerBridge.games_scanned (core's GameLoader.scanned; the
      shells' onLoadAssets / gamesScanned until SF-6c) ->
      webserver.on_games_change: the `games_changed` notice, GET
      /api/v1/games may answer otherwise (ADR-005 S-final SF-3a).
    - AutonaPlayerDB.signals.db_updated -> webserver.ws_playerdb: the player
      DB to the connections allowed to read it.

    Connected after the server exists, i.e. after LoadDB(): nothing is lost,
    clients pull all three on (re)connect. Was the window's alone until
    ADR-005 S3c.
    """
    from .AutonaPlayerDB import AutonaPlayerDB
    from .StateManagerBridge import StateManagerBridge

    bridge = StateManagerBridge.instance
    bridge.game_loaded.connect(webserver.ws_game_changed)
    bridge.games_scanned.connect(webserver.on_games_change)
    AutonaPlayerDB.signals.db_updated.connect(webserver.ws_playerdb)
