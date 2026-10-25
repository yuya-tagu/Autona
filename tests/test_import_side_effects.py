"""Import side-effect regression guard for the migrated singletons.

Step 3 of the startup redesign moves every singleton construction -- and
every static data load -- out of module level and into src/bootstrap.py.
This test pins down what has already been migrated: importing
SettingsManager / StateManager / AutonaHotkeys / AutonaGameAssetManager /
AutonaPlayerDB / AutonaLocaleHelper / AutonaBadWordFilter /
StartGGDataProvider / AutonaTournamentDataProvider / AutonaAlertNotification
/ AutonaUpdater / AutonaCountryHelper / AutonaScoreboardManager /
AutonaWebServer must not build a QApplication, must not create any of those
singleton instances, must not load any data file, and must not touch the
filesystem below the current working directory (./out, ./user_data/games,
./user_data/alerts_red.json, ./user_data/settings.json,
./user_data/badword_*.txt).

Headless core Step 1 adds two things to that list. The core package itself
(src/core: the state layer, its settings and the observer channels) is
imported directly rather than through the compatibility shims, so the rule
holds for the module that actually defines the state rather than for the
re-export in front of it -- and the channels must come up with no
subscribers, since a module-level subscription would be exactly the kind of
hidden wiring bootstrap_core() exists to eliminate. StateManagerBridge is
the other: it is a singleton like the rest, created by bootstrap_core(),
and importing it must not build it.

Note what this cannot check: src/core must also import without Qt, which is
a stronger property than "no side effects" and lives in
tests/test_core_qt_free.py. This module imports the whole widget tree, so
Qt is in sys.modules here either way.

AutonaWebServer is the odd one out: what it used to build at import time was
not a singleton but a Flask app plus a SocketIO server plus ~90 route/event
registrations, run from the WebServer class body. That moved into
_build_web_app(), called once from WebServer.__init__. The exposed surface is
pinned separately in tests/test_webserver_routes.py.

The package itself is covered too: since Step 3g `src/__init__.py` is a
docstring and nothing else, so `import src` must be inert as well. That is
what keeps the init from silently growing a convenience re-export back into
Autona.py, which would boot the whole app on any `from src.X import Y`
(the old agent-pitfalls #1).

Everything runs in a subprocess because the checks are about a *fresh*
interpreter: once any other test has imported these modules, the side effects
(or their absence) can no longer be observed.

The temporary working directory is seeded with the data files those modules
read, because they are read cwd-relatively: AutonaResolve() resolves against
Path.cwd() captured at import time (src/Helpers/AutonaDirHelper.py), and the
rest use plain relative paths. Nothing may read them at import time any more
-- the seeds exist so the *positive controls* at the end (the explicit
Initialize() calls) exercise the real loaders rather than their error paths.
Seeding cannot hide a write: every path asserted below is deliberately left
absent, and ./user_data is not created at all, so an import that writes into
it fails the check instead of quietly succeeding.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# Modules whose import must be side-effect free, in migration order.
_CHECK_SCRIPT = r'''
import importlib
import os
import sys

sys.dont_write_bytecode = True

# The package init itself: importing it must not pull in Autona.py, so
# nothing may be constructed and no submodule may be loaded either.
import src

assert not hasattr(src, "Window"), \
    "src/__init__.py re-exports Window (imports Autona.py)"
assert not hasattr(src, "App"), \
    "src/__init__.py re-exports App (imports Autona.py)"
assert "src.Autona" not in sys.modules, "import src loaded src.Autona"
assert [n for n in sys.modules if n.startswith("src.")] == [], \
    "import src loaded submodules: " \
    f"{[n for n in sys.modules if n.startswith('src.')]}"

# The core package, imported by its real name before anything reaches it
# through a shim: src/core is where the state layer lives (headless core
# Step 1), so that is where the "inert import" rule has to hold.
core_package = importlib.import_module("src.core")
# Like src/__init__.py, this one is a docstring and nothing else. A
# convenience re-export here would make `from src.core import X` drag in the
# whole state layer, which is how the old import-order coupling started.
assert not hasattr(core_package, "StateManager"), \
    "src/core/__init__.py re-exports StateManager"
assert not hasattr(core_package, "SettingsManager"), \
    "src/core/__init__.py re-exports SettingsManager"
importlib.import_module("src.core.Observable")
core_settings = importlib.import_module(
    "src.core.SettingsManager").SettingsManager
core_state = importlib.import_module("src.core.StateManager").StateManager
# The domain-operations layer (headless core Step 3). It is a singleton like
# the Qt-side ones, so the same rule applies: bootstrap_core() creates it,
# importing the module must not.
core_actions = importlib.import_module("src.core.Actions").AutonaActions
# The game asset catalog (ADR-005 S1a): the Qt-free half of what used to be
# AutonaGameAssetManager, a singleton created by bootstrap_core() as well.
game_catalog = importlib.import_module(
    "src.core.GameAssetCatalog").GameAssetCatalog
# The country catalog (ADR-005 S1c): the Qt-free half of what used to be
# AutonaCountryHelper, likewise created by bootstrap_core().
country_catalog = importlib.import_module(
    "src.core.CountryCatalog").CountryCatalog
# The web server's access policy (ADR-005 S3a). It reads -- or issues and
# saves to settings.json -- the access token on first use, never on import.
web_access = importlib.import_module("src.core.WebAccess")
# The persisted state store and its migrations (ADR-005 S3b). The store is
# read, opened and written only from StateManager.Initialize() onwards.
state_persistence = importlib.import_module(
    "src.core.StatePersistence").StatePersistence
state_migrations = importlib.import_module("src.core.StateMigrations")

# SettingsManager comes first, mirroring bootstrap_core(): StateManager
# imports it, so it is in the chain either way, but it is listed explicitly
# because its module tail used to create ./user_data/settings.json.
settings_manager = core_settings
state_manager = core_state
# The old src/SettingsManager.py / src/StateManager.py compat shims are gone;
# nothing may bring a second copy of the state layer back.
for _shim in ("src.SettingsManager", "src.StateManager"):
    try:
        importlib.import_module(_shim)
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(f"{_shim} compat shim is back")
hotkeys = importlib.import_module("src.AutonaHotkeys").AutonaHotkeys
game_assets = importlib.import_module(
    "src.AutonaGameAssetManager").AutonaGameAssetManager
# PlayerDB is imported explicitly, and before the provider that pulls it in
# anyway, because its module tail used to reach into
# AutonaGameAssetManager.instance.signals -- an import-time dependency on
# another module's singleton. That wiring now lives in bootstrap_core().
player_db = importlib.import_module("src.AutonaPlayerDB").AutonaPlayerDB
# The three static-data modules. Their tails used to read the shipped
# language/ngword/GraphQL files -- and, for the bad-word lists, to *create*
# ./user_data/badword_{white,black}list.txt -- as a side effect of being
# pulled into the chain by something else.
locale_helper = importlib.import_module(
    "src.Helpers.AutonaLocaleHelper").AutonaLocaleHelper
bad_words = importlib.import_module(
    "src.Helpers.AutonaBadWordFilter").AutonaBadWordFilter
startgg = importlib.import_module(
    "src.TournamentDataProvider.StartGGDataProvider").StartGGDataProvider
tdp = importlib.import_module(
    "src.AutonaTournamentDataProvider").AutonaTournamentDataProvider
alerts = importlib.import_module(
    "src.AutonaAlertNotification").AutonaAlertNotification
updater = importlib.import_module("src.AutonaUpdater").AutonaUpdater
country_helper = importlib.import_module(
    "src.Helpers.AutonaCountryHelper").AutonaCountryHelper
# ScoreboardManager drags in the whole scoreboard widget tree
# (AutonaScoreboardWidget / AutonaCrewBattleWidget and ~16 further modules),
# which is precisely the point: none of them may build a widget or a
# singleton while merely being imported.
scoreboard_manager = importlib.import_module(
    "src.AutonaScoreboardManager").AutonaScoreboardManager
# WebServer additionally pulls in AutonaWebServerActions / AutonaApiV1 /
# AutonaDesignerService. None of those may build anything at module level
# either -- AutonaApiV1 only declares its Blueprint, which is inert until
# WebServer.__init__ registers it on the app.
web_server = importlib.import_module("src.AutonaWebServer").WebServer
# The Qt adapter for the state layer. Imported last on purpose: everything
# above already pulled it in (every widget that listens to state does), so
# if any of them built one at import time this assertion catches it.
bridge = importlib.import_module("src.StateManagerBridge").StateManagerBridge

from qtpy.QtWidgets import QApplication

assert QApplication.instance() is None, "import created a QApplication"
assert core_actions.instance is None, "import created AutonaActions.instance"
assert game_catalog.instance is None, "import created GameAssetCatalog.instance"
assert country_catalog.instance is None,     "import created CountryCatalog.instance"
assert web_access._token is None, "import read or issued the access token"
assert web_access._tokenGeneration == 0, "import issued an access token"
assert state_persistence.Path() is None, "import opened the state store"
assert state_persistence._thread is None, "import started the store writer"
assert state_persistence._pending is None, "import queued a store write"
assert state_migrations.CURRENT_VERSION >= 1
assert hotkeys.instance is None, "import created AutonaHotkeys.instance"
assert not hotkeys._actions_connected, \
    "import wired the hotkeys to AutonaActions"
assert game_assets.instance is None, \
    "import created AutonaGameAssetManager.instance"
assert tdp.instance is None, \
    "import created AutonaTournamentDataProvider.instance"
assert alerts.instance is None, \
    "import created AutonaAlertNotification.instance"
assert updater.instance is None, "import created AutonaUpdater.instance"
assert country_helper.instance is None, \
    "import created AutonaCountryHelper.instance"
assert scoreboard_manager.instance is None, \
    "import created AutonaScoreboardManager.instance"
assert bridge.instance is None, "import created StateManagerBridge.instance"
assert web_server.app is None, "import built WebServer.app (Flask)"
assert web_server.socketio is None, "import built WebServer.socketio"
assert web_server.actions is None, "import built WebServer.actions"

# The core state layer comes up empty, and -- the point of importing the
# whole widget tree above -- with nobody wired to its channels. Only
# bootstrap_core() subscribes (through StateManagerBridge); a module-level
# subscription anywhere would show up as a non-zero count here.
assert core_state.state == {}, "import populated StateManager.state"
assert core_state.lastSavedState == {}, "import populated lastSavedState"
for channel_name in ("state_big_change", "state_updated",
                     "held_dirty_changed", "held_state_updated"):
    channel = getattr(core_state.observers, channel_name)
    assert channel.subscriber_count() == 0, \
        f"import subscribed to StateManager.observers.{channel_name}"

# Static data: nothing loaded, nothing written.
assert settings_manager.settings == {}, "import loaded settings.json"
assert locale_helper.languages == [], "import loaded the language table"
assert locale_helper.remapping == {}, "import loaded the locale remapping"
assert locale_helper.countryToLanguage == {}, \
    "import loaded data_countries.json"
assert locale_helper.countryToContinent == {}, \
    "import loaded data_countries.json"
assert not bad_words.patterns, "import built the bad-word patterns"
assert not bad_words.badWordTries, "import built the bad-word tries"
assert bad_words.whiteList == [], "import loaded the bad-word whitelist"
assert bad_words.blackList == [], "import loaded the bad-word blacklist"
# Every GraphQL query attribute, found by name so a newly added query is
# covered without touching this test.
queryAttrs = [n for n in vars(startgg) if n.endswith("Query")]
assert len(queryAttrs) == 17, \
    f"expected 17 StartGG query attributes, found {len(queryAttrs)}"
for name in queryAttrs:
    assert getattr(startgg, name) is None, \
        f"import loaded StartGGDataProvider.{name}"

assert not os.path.exists("./out"), "import created ./out"
assert not os.path.exists("./user_data"), "import created ./user_data"

# Positive control: the work simply moved to the explicit entry point, so
# the test cannot pass just because the code stopped working.
# SettingsManager first, exactly as in bootstrap_core(); it also creates
# ./user_data, which the loads below write into.
settings_manager.Initialize()
assert os.path.isfile("./user_data/settings.json"), \
    "SettingsManager.Initialize() did not create ./user_data/settings.json"

locale_helper.Initialize()
assert locale_helper.languages, \
    "AutonaLocaleHelper.Initialize() did not load the language table"
assert locale_helper.countryToLanguage, \
    "AutonaLocaleHelper.Initialize() did not load data_countries.json"

bad_words.Initialize()
assert bad_words.patterns, \
    "AutonaBadWordFilter.Initialize() did not build the patterns"
assert os.path.isfile("./user_data/badword_whitelist.txt"), \
    "AutonaBadWordFilter.Initialize() did not create badword_whitelist.txt"
assert os.path.isfile("./user_data/badword_blacklist.txt"), \
    "AutonaBadWordFilter.Initialize() did not create badword_blacklist.txt"

startgg.Initialize()
for name in queryAttrs:
    assert getattr(startgg, name), \
        f"StartGGDataProvider.Initialize() did not load {name}"

state_manager.Initialize()
assert os.path.isdir("./out"), "Initialize() did not create ./out"
# Initialize() opens the store; with nothing to migrate it writes nothing.
assert state_persistence.Path() == os.path.abspath("./user_data/state.json"), \
    "Initialize() did not open user_data/state.json"
assert not os.path.exists("./user_data/state.json"), \
    "Initialize() wrote state.json with nothing to migrate"

# The actions layer needs both managers above, which is why bootstrap_core()
# creates it here rather than at import. Its positive control is that it can
# be built and used with no Qt object in sight.
core_actions.instance = core_actions()
core_actions.instance.SetDisplayOption("show_seed", False)
assert core_state.Get("display_options.show_seed") is False, \
    "AutonaActions could not write the state headlessly"
# ...and that published write reaches the store.
assert state_persistence.Flush(30), "the store writer did not finish"
assert os.path.isfile("./user_data/state.json"), \
    "a published save cycle did not write user_data/state.json"

# Same for the game asset catalog and its Qt adapter (ADR-005 S1a). The
# ./user_data/games guarantee travels with the scan that lists it
# (ScanGames()), and the empty "game" state is published by
# ResetGameState() -- which is why StateManager.Initialize() has to run
# first. Constructing either object touches neither.
game_catalog.instance = game_catalog()
assert not os.path.exists("./user_data/games"), \
    "GameAssetCatalog() created ./user_data/games"
game_catalog.instance.ResetGameState()
assert core_state.Get("game") == {}, \
    "GameAssetCatalog.ResetGameState() did not publish an empty game"
game_assets.instance = game_assets(game_catalog.instance)
assert not os.path.exists("./user_data/games"), \
    "AutonaGameAssetManager() created ./user_data/games"
game_catalog.instance.ScanGames()
assert os.path.isdir("./user_data/games"), \
    "GameAssetCatalog.ScanGames() did not create ./user_data/games"

# Same for AutonaAlertNotification: MarkNotificationRed() needs the file, so
# the guarantee moved from module level into __init__. No QApplication is
# needed -- it is a plain QObject.
alerts.instance = alerts()
assert os.path.isfile("./user_data/alerts_red.json"), \
    "AutonaAlertNotification() did not create ./user_data/alerts_red.json"

# The country catalog reads nothing until Load() -- which the Qt adapter
# drives from Window.__init__, once the locale is known -- and the
# ./user_data/additional_flag guarantee travels with that load.
country_catalog.instance = country_catalog()
assert not country_catalog.instance.IsLoaded(),     "CountryCatalog() loaded the country data"
assert not os.path.exists("./user_data/additional_flag"),     "CountryCatalog() created ./user_data/additional_flag"
assert country_catalog.instance.Load(), "CountryCatalog.Load() failed"
assert country_catalog.instance.BasicCountryInfo("JP").get("en_name")     == "Japan", "CountryCatalog.Load() did not load the country data"
assert os.path.isdir("./user_data/additional_flag"),     "CountryCatalog.Load() did not create ./user_data/additional_flag"

# No positive control for AutonaCountryHelper (its __init__ is empty; the real
# work is LoadCountries(), driven from init(), and it builds Qt items) and none
# for AutonaScoreboardManager either: it is a QDockWidget, and constructing a
# QWidget without a QApplication aborts the interpreter, which would take this
# whole subprocess down. The import-time assertions above are the contract.

print("OK")
'''

# Standalone guard for the package init: `import src` on its own, in a cwd
# seeded with nothing at all, must load no submodule, build no QApplication
# and create no file or directory.
_INIT_ONLY_SCRIPT = r'''
import os
import sys

sys.dont_write_bytecode = True

before = set(os.listdir("."))

import src

assert "src.Autona" not in sys.modules, "import src loaded src.Autona"
loaded = sorted(n for n in sys.modules if n == "src" or n.startswith("src."))
assert loaded == ["src"], f"import src loaded submodules: {loaded}"
assert not hasattr(src, "Window") and not hasattr(src, "App"), \
    "src/__init__.py re-exports Window/App, i.e. it imports Autona.py"

from qtpy.QtWidgets import QApplication

assert QApplication.instance() is None, "import src created a QApplication"
assert set(os.listdir(".")) == before, \
    f"import src touched the cwd: {sorted(set(os.listdir('.')) - before)}"

print("OK")
'''

# Read-only program data the Initialize() calls consume, resolved
# cwd-relatively. Seeded so the positive controls run the real loaders.
# ./user_data is deliberately NOT created: its absence is what makes the
# "import created ./user_data" assertion meaningful.
_SEED_FILES = [
    "src/i18n/mapping.json",
    "assets/data_countries.json",
    "assets/countries+states.json",
]

# Same, for whole directories.
_SEED_DIRS = [
    "assets/ngword",
]


def _seed_cwd(tmp_path):
    for rel in _SEED_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / rel, dest)

    for rel in _SEED_DIRS:
        shutil.copytree(PROJECT_ROOT / rel, tmp_path / rel)

    # StartGGDataProvider.Initialize() reads its GraphQL queries from
    # src/TournamentDataProvider/, resolved against the cwd.
    query_dir = tmp_path / "src" / "TournamentDataProvider"
    query_dir.mkdir(parents=True, exist_ok=True)
    for query_file in (SRC_ROOT / "TournamentDataProvider").glob("StartGG*Query.txt"):
        shutil.copyfile(query_file, query_dir / query_file.name)


def _run_check(script, tmp_path):
    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    # Never let a headless CI/agent shell pop a real Qt platform plugin.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )


def test_importing_the_src_package_is_inert(tmp_path):
    """`import src` must stay a no-op: no submodule, no Qt, no file."""
    result = _run_check(_INIT_ONLY_SCRIPT, tmp_path)

    assert result.returncode == 0, (
        f"src package init is not side-effect free\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")
    assert "OK" in result.stdout


def test_core_modules_have_no_import_side_effects(tmp_path):
    _seed_cwd(tmp_path)

    result = _run_check(_CHECK_SCRIPT, tmp_path)

    assert result.returncode == 0, (
        f"side-effect check failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")
    assert "OK" in result.stdout
