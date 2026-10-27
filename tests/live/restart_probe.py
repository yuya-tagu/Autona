"""Second process = a simulated restart of Autona. Not collected by pytest.

Usage: python restart_probe.py <runtime_dir> <project_root>

Boots the app the way main.py does -- QApplication, then bootstrap_core() and
bootstrap_gui() --
inside the *same* working directory a previous run left behind, and reports
what survived. Everything it needs to answer lives in one JSON blob on
stdout, prefixed with a marker so loguru's stderr chatter cannot be mistaken
for it.

Why a subprocess: bootstrap_core() is guarded against re-entry and
StateManager.Initialize() loads the persisted state (./user_data/state.json,
ADR-005 S3b) exactly once per process, so "start again and see what comes
back" is not something the test process can do to itself.
"""
import json
import os
import sys

MARKER = "<<<AUTONA_RESTART_PROBE>>>"


def main():
    runtime, project = sys.argv[1], sys.argv[2]

    # chdir BEFORE importing anything from src: Helpers/AutonaDirHelper
    # captures the working directory at import time and every AutonaResolve()
    # in the process resolves against that snapshot.
    os.chdir(runtime)
    sys.path.insert(0, project)

    os.environ["QT_API"] = "pyside6"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from qtpy.QtCore import QEventLoop, QTimer
    from qtpy.QtWidgets import QApplication

    # AutonaScoreboardManager is a QWidget; constructing one without a
    # QApplication aborts at the C++ level.
    app = QApplication.instance() or QApplication([])

    result = {}

    # What the previous process actually persisted, read before anything in
    # src can touch it: the store's envelope, {"state_version", "state"}.
    # The store's path is fixed by the first StateManager.Initialize() of the
    # test process, so when that ran in another directory (a full-suite run)
    # there is none here -- and the restart below then migrates from
    # ./out/program_state.json, exactly as the app does, so read that.
    persisted = {}
    try:
        try:
            with open("./user_data/state.json", "rb") as handle:
                persisted = json.loads(handle.read())["state"]
            result["persisted_source"] = "user_data/state.json"
        except FileNotFoundError:
            with open("./out/program_state.json", "rb") as handle:
                persisted = json.loads(handle.read())
            result["persisted_source"] = "out/program_state.json"
    except Exception as error:
        persisted = {}
        result["persisted_read_error"] = f"{type(error).__name__}: {error}"

    result["persisted_tournament_name"] = (
        persisted.get("tournamentInfo", {}).get("tournamentName"))
    result["persisted_score_keys"] = sorted(persisted.get("score", {}).keys())
    result["persisted_player_names"] = [
        persisted.get("score", {}).get("1", {}).get("team", {})
        .get(team, {}).get("player", {}).get("1", {}).get("name")
        for team in ("1", "2")]

    from src.bootstrap import bootstrap_core, bootstrap_gui
    bootstrap_core()
    # The desktop's half too: (b) below is AutonaScoreboardManager's doing,
    # and the scoreboard dock is built by bootstrap_gui() (ADR-005 S3c).
    bootstrap_gui()

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    # (a) StateManager.LoadState() restored the persisted tree, and
    # (b) AutonaScoreboardManager.__init__ deliberately dropped score.*
    #     -- a scoreboard must come up empty, not showing yesterday's set.
    result["restored_tournament_name"] = StateManager.Get(
        "tournamentInfo.tournamentName")
    result["restored_num_entrants"] = StateManager.Get(
        "tournamentInfo.numEntrants")
    result["restored_score"] = StateManager.Get("score")

    # (c) the URL survived in the settings file, not just in memory.
    result["settings_url"] = SettingsManager.Get("TOURNAMENT_URL")

    # (d) UiMounted() re-fetches from the live API on that URL. The result
    # is handed from a task executor job to the GUI thread through a queued
    # connection (the gui executor), so it needs a running event loop to be
    # delivered.
    received = {}
    loop = QEventLoop()

    def onTournamentData(data):
        received.update(data or {})
        loop.quit()

    AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
        onTournamentData)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(60000)

    AutonaTournamentDataProvider.instance.UiMounted()
    loop.exec_() if hasattr(loop, "exec_") else loop.exec()

    result["refetched"] = bool(received)
    result["refetched_tournament_name"] = received.get("tournamentName")
    result["refetched_num_entrants"] = received.get("numEntrants")
    result["refetched_initial_load"] = received.get("initial_load")
    result["provider_url"] = getattr(
        AutonaTournamentDataProvider.instance.provider, "url", None)

    sys.stdout.write(MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()

    # SetTournament also kicked off the entrant fetch, and the task
    # executor's job threads are joined at interpreter exit. Nothing left is doing
    # work that matters (program_state.json and the store are written
    # atomically), so leave rather than wait out a page-by-page entrant
    # download.
    os._exit(0)


if __name__ == "__main__":
    main()
