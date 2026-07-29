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

AutonaWebServer is the odd one out: what it used to build at import time was
not a singleton but a Flask app plus a SocketIO server plus ~90 route/event
registrations, run from the WebServer class body. That moved into
_build_web_app(), called once from WebServer.__init__. The exposed surface is
pinned separately in tests/test_webserver_routes.py.

It runs in a subprocess because the checks are about a *fresh* interpreter:
once any other test has imported these modules, the side effects (or their
absence) can no longer be observed. conftest's direct_import() cannot be used
either -- the modules use relative imports, so they need a parent package.
The subprocess therefore registers a bare types.ModuleType("src") with a
__path__ pointing at src/, which makes `src.StateManager` importable *without*
executing src/__init__.py (that would pull in Autona.py and boot the whole
app; agent-pitfalls #1).

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
import types

sys.dont_write_bytecode = True

SRC_ROOT = sys.argv[1]

# Fake parent package: gives the relative imports inside the modules a
# package to resolve against without running src/__init__.py.
pkg = types.ModuleType("src")
pkg.__path__ = [SRC_ROOT]
sys.modules["src"] = pkg

# SettingsManager comes first, mirroring bootstrap_core(): StateManager
# imports it, so it is in the chain either way, but it is listed explicitly
# because its module tail used to create ./user_data/settings.json.
settings_manager = importlib.import_module(
    "src.SettingsManager").SettingsManager
state_manager = importlib.import_module("src.StateManager").StateManager
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

from qtpy.QtWidgets import QApplication

assert QApplication.instance() is None, "import created a QApplication"
assert hotkeys.instance is None, "import created AutonaHotkeys.instance"
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
assert web_server.app is None, "import built WebServer.app (Flask)"
assert web_server.socketio is None, "import built WebServer.socketio"
assert web_server.actions is None, "import built WebServer.actions"

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

# Same for the GameAssetManager: its ./user_data/games guarantee moved from
# module level into __init__. StateManager.Initialize() has to run first --
# the constructor writes the "game" state key.
game_assets.instance = game_assets()
assert os.path.isdir("./user_data/games"), \
    "AutonaGameAssetManager() did not create ./user_data/games"

# Same for AutonaAlertNotification: MarkNotificationRed() needs the file, so
# the guarantee moved from module level into __init__. No QApplication is
# needed -- it is a plain QObject.
alerts.instance = alerts()
assert os.path.isfile("./user_data/alerts_red.json"), \
    "AutonaAlertNotification() did not create ./user_data/alerts_red.json"

# No positive control for AutonaCountryHelper (its __init__ is empty; the real
# work is LoadCountries(), driven from init()) and none for
# AutonaScoreboardManager either: it is a QDockWidget, and constructing a
# QWidget without a QApplication aborts the interpreter, which would take this
# whole subprocess down. The import-time assertions above are the contract.

print("OK")
'''

# Read-only program data the Initialize() calls consume, resolved
# cwd-relatively. Seeded so the positive controls run the real loaders.
# ./user_data is deliberately NOT created: its absence is what makes the
# "import created ./user_data" assertion meaningful.
_SEED_FILES = [
    "src/i18n/mapping.json",
    "assets/data_countries.json",
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


def test_core_modules_have_no_import_side_effects(tmp_path):
    _seed_cwd(tmp_path)

    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    # Never let a headless CI/agent shell pop a real Qt platform plugin.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    result = subprocess.run(
        [sys.executable, "-B", "-c", _CHECK_SCRIPT, str(SRC_ROOT)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"side-effect check failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")
    assert "OK" in result.stdout
