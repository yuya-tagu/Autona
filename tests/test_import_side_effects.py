"""Import side-effect regression guard for the migrated singletons.

Step 3 of the startup redesign moves every singleton construction out of
module level and into src/bootstrap.py. This test pins down what has already
been migrated: importing StateManager / AutonaHotkeys / AutonaGameAssetManager
/ AutonaPlayerDB / AutonaTournamentDataProvider / AutonaAlertNotification /
AutonaUpdater / AutonaCountryHelper / AutonaScoreboardManager /
AutonaWebServer must not build a QApplication, must not create any of those
singleton instances, and must not touch the filesystem below the current
working directory (./out, ./user_data/games, ./user_data/alerts_red.json).

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

The temporary working directory is seeded with a few data files because
AutonaResolve() resolves against Path.cwd() captured at import time
(src/Helpers/AutonaDirHelper.py): AutonaLocaleHelper raises at import when it
cannot read src/i18n/mapping.json or ./assets/data_countries.json,
StartGGDataProvider reads its GraphQL query files at import, and
AutonaBadWordFilter (reached through the scoreboard widget tree) reads
./assets/ngword/. Seeding those makes the import *possible* and keeps the
subprocess output free of swallowed FileNotFoundError tracebacks; it does not
hide any write, since every path asserted below is deliberately left absent.
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

state_manager = importlib.import_module("src.StateManager").StateManager
hotkeys = importlib.import_module("src.AutonaHotkeys").AutonaHotkeys
game_assets = importlib.import_module(
    "src.AutonaGameAssetManager").AutonaGameAssetManager
# PlayerDB is imported explicitly, and before the provider that pulls it in
# anyway, because its module tail used to reach into
# AutonaGameAssetManager.instance.signals -- an import-time dependency on
# another module's singleton. That wiring now lives in bootstrap_core().
player_db = importlib.import_module("src.AutonaPlayerDB").AutonaPlayerDB
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
assert not os.path.exists("./out"), "import created ./out"
assert not os.path.exists("./user_data/games"), \
    "import created ./user_data/games"
assert not os.path.exists("./user_data/alerts_red.json"), \
    "import created ./user_data/alerts_red.json"

# Positive control: the work simply moved to the explicit entry point, so
# the test cannot pass just because the code stopped working.
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

# Files the import chain reads through AutonaResolve() (cwd-relative). See the
# module docstring; without these the imports raise before any assertion runs.
_SEED_FILES = [
    "src/i18n/mapping.json",
    "assets/data_countries.json",
]

# Same, for whole directories read at import time.
_SEED_DIRS = [
    "assets/ngword",
]


def _seed_cwd(tmp_path):
    # Known remaining import-time side effects, deliberately not asserted
    # against here, all of which need ./user_data to exist:
    # - SettingsManager reads/writes ./user_data/settings.json (StateManager
    #   imports it),
    # - AutonaBadWordFilter creates ./user_data/badword_{white,black}list.txt
    #   (reached through the scoreboard widget tree).
    (tmp_path / "user_data").mkdir()

    for rel in _SEED_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / rel, dest)

    for rel in _SEED_DIRS:
        shutil.copytree(PROJECT_ROOT / rel, tmp_path / rel)

    # StartGGDataProvider assigns its GraphQL queries to class attributes at
    # import time, reading them from src/TournamentDataProvider/.
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
