"""The game loader, recorded step by step (ADR-005 S-final SF-6a; one
loader since SF-6b, one for both shells since SF-6c).

What the game loader does -- core's GameLoader, bootstrap_core()'s
GameLoader.instance for both shells since SF-6c (the desktop's asset
adapter, AutonaGameAssetManager, and the headless shell's HeadlessGames
each held and wrapped one until then) -- recorded serially for five
scenarios. SF-6a recorded the two loaders each shell had then, into
<shell>_<scenario>.json; SF-6b put one in core, which left the desktop's
recordings as they were and made each headless recording its desktop
counterpart. SF-6c stopped handing what follows a load's selection to the
main thread: the writes are 6b's, made on the loader's thread, and the
recordings are one per scenario. The contract is tests/test_asset_jobs
.py's; this is the detail.

Recorded for each, into tests/fixtures/game_loader/<scenario>.json:

- writes: every StateManager.Set / Unset, in order, with the role of the
  thread that made it and the value as program_state.json holds it;
- published: each publish -- its revision (counted from the scenario's
  start), the publishing thread's role and the delta as a client receives
  it ("BIG" for a state_big_change);
- committed: per publish, the score.* paths it carried (what a commit put
  out), cut to the player's group;
- settings: the SettingsManager.Set calls;
- events: the order of what happened, each with its thread's role -- the
  requests, the scan, the selection, the footer's status, the held
  notifications, the publishes, the roster / set length / commit (the
  loader's _Publish), the settings writes, `loaded` (GameLoader.loaded, on
  the loader's thread), the player DB's refresh and the bridge's
  game_loaded (a slot connected last) on the main thread, each request's
  done();
- answers: what each request returned, read once it was over (its
  Future's outcome);
- after: what is left pending on the held boards, and the save block count.

Held boards (the harness makes them as the desktop's launch does; a
headless shell's boards are not held, so its commit finds nothing there):
a load is one save cycle, opened and closed on the loader's thread -- the
selection, the roster and its commit, the pick -- and told after it
closes. Until SF-6c the cycle was closed on the main thread at the end of
the loader's _Publish, with the announcement's slots inside it; until
SF-6b the headless selection was published alone on the loader's thread
and the roster in a cycle of its own on the main thread.

The background work runs serially (_serially): the jobs to the end, with
the main thread not pumping -- none waits for it -- then what they queued
there, and again, so a queued slot never lands between a job's steps.

Roles: main (the main / GUI thread), loader (the "game load" job), scan
(the "game scan" job), job (another job), other. What is not deterministic
is left out or normalised: the runtime directory (<RUNTIME>), revisions
(counted from the scenario's start), the footer's tokens and progress
updates, the order within a run of held-dirty notifications (a set's);
the stream title's refresh (StreamService queues it on the main
thread on every publish, where it would run beside the loader's tail) is
stopped while the scenarios run.

Regenerate with AUTONA_RECORD_GAME_LOADER=1, and read the diff: it is the
point.
"""
import json
import os
import threading
import time
from pathlib import Path

import pytest
from qtpy.QtWidgets import QApplication

from src.core.StateManager import StateManager
from src.core.TaskExecutor import JOB_THREAD_PREFIX, TaskExecutor
from tests.conftest import drain_tasks
from tests.fixtures.write_recording import encode, encodeDelta
from tests.test_asset_jobs import (  # noqa: F401  (fixtures)
    WAIT, _afterTheSelection, _holdTheLoader, _waitFor, ownExecutor, shell)
from tests.test_game_asset_manager_load import (  # noqa: F401  (fixtures)
    goldenGames)

RECORD = os.environ.get("AUTONA_RECORD_GAME_LOADER") == "1"
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "game_loader"

# The role of a job's thread, by its task name.
TASK_ROLES = {"game load": "loader", "game scan": "scan"}

# The held boards whose pending keys are recorded at the end.
HELD = ("score.1", "score.2")


def _normalise(text, app):
    root = str(app.dir)
    # The longest spelling first: a path inside an encoded value inside the
    # JSON text has its backslashes escaped twice.
    for spelling in (root.replace("\\", "\\\\\\\\"),
                     root.replace("\\", "\\\\"), root,
                     root.replace("\\", "/")):
        text = text.replace(spelling, "<RUNTIME>")
    return text


def _heldDirtyInOrder(events):
    """Each run of adjacent held-dirty notifications, sorted: the boards a
    commit drained are told in the order of a set (StateManager
    ._heldCommitted), which string hashing reorders from run to run."""
    settled, run = [], []
    for event in events:
        if event[1] == "held dirty":
            run.append(event)
            continue
        settled.extend(sorted(run, key=json.dumps))
        run = []
        settled.append(event)
    settled.extend(sorted(run, key=json.dumps))
    return settled


@pytest.fixture
def noStreamTitle():
    """The session's StreamService stops following the state while the
    scenario runs (its refresh is queued on the main thread on every
    publish), and follows it again afterwards."""
    from src.core.StreamService import StreamService

    service = StreamService.instance
    started = service._started
    service.Stop()
    try:
        yield
    finally:
        if started:
            service.Start()


class _Recorder:
    """What a scenario does, recorded once Start() is called. The spies go
    in at construction, so the jobs a scenario's setup submits have their
    roles too."""

    def __init__(self, shell, monkeypatch, app):
        self.shell = shell
        self.app = app
        self.local = threading.local()
        self.lock = threading.Lock()
        self.active = False
        self.base = 0
        self.writes, self.published, self.committed = [], [], []
        self.settings, self.events, self.answers = [], [], []
        self._Spy(monkeypatch)

    # -- roles and events -------------------------------------------------

    def Role(self):
        thread = threading.current_thread()
        if thread is threading.main_thread():
            return "main"
        role = getattr(self.local, "role", None)
        if role is not None:
            return role
        return "job" if thread.name.startswith(JOB_THREAD_PREFIX) \
            else "other"

    def Event(self, kind, *details):
        if self.active:
            role = self.Role()
            with self.lock:
                self.events.append([role, kind, *details])

    def Done(self, label):
        """A request's done(), recorded as an event."""
        return lambda: self.Event("done", label)

    def Answer(self, label, returned):
        """What the request labelled `label` returned, read once it is
        over."""
        self.answers.append([label, self.shell.Answer(returned, WAIT)])

    # -- the spies --------------------------------------------------------

    def _Spy(self, monkeypatch):
        from src.core.Actions import AutonaActions
        from src.core.SettingsManager import SettingsManager
        from src.core.StatusService import StatusService
        from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

        realSet, realUnset = StateManager.Set, StateManager.Unset

        def set_(key, value):
            if self.active:
                role = self.Role()
                with self.lock:
                    self.writes.append([role, "set", key, encode(value)])
            return realSet(key, value)

        def unset(key):
            if self.active:
                role = self.Role()
                with self.lock:
                    self.writes.append([role, "unset", key])
            return realUnset(key)

        monkeypatch.setattr(StateManager, "Set", set_)
        monkeypatch.setattr(StateManager, "Unset", unset)

        realSettings = SettingsManager.Set

        def settings(key, value):
            if self.active:
                role = self.Role()
                with self.lock:
                    self.settings.append([role, key, value])
                self.Event("settings", key, value)
            return realSettings(key, value)

        monkeypatch.setattr(SettingsManager, "Set", settings)

        loader = self.shell.loader
        realLoad = loader.LoadGameByCodename

        def load(codename, async_mode=True, mods_active=False,
                 mods_reload_mode=False, done=None):
            self.Event("request", codename, mods_active, mods_reload_mode,
                       async_mode)
            return realLoad(codename, async_mode=async_mode,
                            mods_active=mods_active,
                            mods_reload_mode=mods_reload_mode, done=done)

        monkeypatch.setattr(loader, "LoadGameByCodename", load)

        catalog = self.shell.catalog
        realSelect = catalog.SelectGame

        def select(codename, **kwargs):
            self.Event("select", codename, kwargs.get("modsActive"),
                       kwargs.get("reload"))
            return realSelect(codename, **kwargs)

        monkeypatch.setattr(catalog, "SelectGame", select)
        realScan = catalog.ScanGames

        def scan():
            self.Event("scan")
            return realScan()

        monkeypatch.setattr(catalog, "ScanGames", scan)

        actions = AutonaActions.instance
        realRoster = actions.ApplyGameRoster
        realBestOfs = actions.RepublishBestOfs
        realCommit = actions.CommitGameLoad

        def roster(replaced=None):
            self.Event("roster", replaced)
            return realRoster(replaced)

        def bestOfs():
            self.Event("best of")
            return realBestOfs()

        def commit(written):
            self.Event("commit")
            return realCommit(written)

        monkeypatch.setattr(actions, "ApplyGameRoster", roster)
        monkeypatch.setattr(actions, "RepublishBestOfs", bestOfs)
        monkeypatch.setattr(actions, "CommitGameLoad", commit)

        realPush, realPop = StatusService.Push, StatusService.Pop

        def push(code, args=None, done=0, total=0):
            # The footer's job as its code and args (ADR-005 S-final SF-2),
            # not the translated words: those follow the machine's locale.
            self.Event("status push", code, dict(args or {}))
            return realPush(code, args, done, total)

        def pop(token):
            if token is not None:
                self.Event("status pop")
            return realPop(token)

        monkeypatch.setattr(StatusService, "Push", staticmethod(push))
        monkeypatch.setattr(StatusService, "Pop", staticmethod(pop))

        executor = TaskExecutor.instance
        realSubmit = executor.submit

        def submit(fn, *args, task_name, **kwargs):
            role = TASK_ROLES.get(task_name, "job")

            def run(*fnArgs, **fnKwargs):
                self.local.role = role
                return fn(*fnArgs, **fnKwargs)

            return realSubmit(run, *args, task_name=task_name, **kwargs)

        monkeypatch.setattr(executor, "submit", submit)

    # -- the subscriptions ------------------------------------------------

    def _OnPublished(self, payload):
        if not self.active:
            return
        delta = payload.get("delta") or []
        self._Publish(payload.get("revision"), encodeDelta(delta), delta)

    def _OnBigChange(self):
        if self.active:
            self._Publish(StateManager.revision, "BIG", [])

    def _Publish(self, revision, encoded, delta):
        revision -= self.base
        role = self.Role()
        paths = sorted({".".join(str(part) for part in row["path"][:7])
                        for row in delta
                        if row.get("path") and row["path"][0] == "score"})
        with self.lock:
            self.published.append(
                {"revision": revision, "role": role, "delta": encoded})
            self.committed.append({"revision": revision, "paths": paths})
        self.Event("published", revision)

    def _OnHeldDirty(self, prefix, dirty):
        self.Event("held dirty", prefix, dirty)

    def _OnLoaded(self, snapshot):
        self.Event("loaded", getattr(snapshot, "codename", None),
                   getattr(snapshot, "replaced", None))

    def _OnPlayerDB(self, *_args):
        self.Event("player DB")

    def _OnAnnounced(self, *_args):
        self.Event("game_loaded")

    def _Connections(self):
        # The main thread's receivers: the player DB's refresh (connected to
        # the bridge's game_loaded by bootstrap_core()) and game_loaded
        # itself, connected after it. (Until ADR-005 S-final SF-6c: the
        # shell's gameLoaded and its announcement, onLoad / gameChanged.)
        from src.AutonaPlayerDB import AutonaPlayerDB

        return ((AutonaPlayerDB.signals.db_updated, self._OnPlayerDB),
                (self.shell.announced, self._OnAnnounced))

    def Start(self):
        """Record from here (the scenario's setup is done)."""
        from src.core.GameLoader import GameLoader

        observers = StateManager.observers
        self._subscriptions = (
            (observers.state_updated, self._OnPublished),
            (observers.state_big_change, self._OnBigChange),
            (observers.held_dirty_changed, self._OnHeldDirty),
            (GameLoader.loaded, self._OnLoaded))
        for channel, callback in self._subscriptions:
            channel.subscribe(callback)
        for signal, slot in self._Connections():
            signal.connect(slot)
        self.base = StateManager.revision
        self.active = True

    def Stop(self):
        if not self.active:
            return
        self.active = False
        for channel, callback in self._subscriptions:
            channel.unsubscribe(callback)
        for signal, slot in self._Connections():
            signal.disconnect(slot)

    def Result(self, scenario):
        with StateManager.lock:
            pending = {prefix: sorted(StateManager.heldKeys.get(prefix, []))
                       for prefix in HELD}
            saveBlocked = StateManager.saveBlocked
        result = {
            "about": SCENARIOS[scenario],
            "writes": self.writes,
            "published": self.published,
            "committed": self.committed,
            "settings": self.settings,
            "events": _heldDirtyInOrder(self.events),
            "answers": self.answers,
            "after": {"pending": pending, "saveBlocked": saveBlocked},
        }
        return json.loads(_normalise(json.dumps(result, ensure_ascii=False),
                                     self.app))


def _check(name, recorded):
    path = GOLDEN_DIR / f"{name}.json"
    if RECORD:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(recorded, ensure_ascii=False, indent=1))
            file.write("\n")
        return
    assert path.exists(), (
        f"no recording {path.name}: record it with "
        "AUTONA_RECORD_GAME_LOADER=1")
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert list(recorded) == list(golden), (list(recorded), list(golden))
    for section, expected in golden.items():
        actual = recorded[section]
        if actual == expected:
            continue
        if isinstance(expected, list) and isinstance(actual, list):
            at = next((i for i, (now, then) in enumerate(zip(actual, expected))
                       if now != then), min(len(actual), len(expected)))
            pytest.fail(
                f"{name}: {section} differs from [{at}] (now {len(actual)} "
                f"entries, recorded {len(expected)})\n"
                f"recorded: {json.dumps(expected[at:at + 3], ensure_ascii=False)[:3000]}\n"
                f"now:      {json.dumps(actual[at:at + 3], ensure_ascii=False)[:3000]}")
        pytest.fail(
            f"{name}: {section} differs\n"
            f"recorded: {json.dumps(expected, ensure_ascii=False)[:3000]}\n"
            f"now:      {json.dumps(actual, ensure_ascii=False)[:3000]}")


# ── the scenarios ────────────────────────────────────────────────────────


def _quiet(app):
    drain_tasks()
    app.settle()


def _serially():
    """The background work to the end, the main thread's share after the
    jobs': the jobs first, with this (the main) thread not pumping -- no job
    waits for it since ADR-005 S-final SF-6c -- then what they queued here
    (a load's notification through the bridge, what follows a scan), which
    can submit more (the restore's load), so again until nothing is left.
    The recorded order is then the same on every run: drain_tasks pumps
    while the jobs run, so a queued slot could land between a job's last
    steps."""
    executor = TaskExecutor.instance
    deadline = time.monotonic() + 3 * WAIT
    while True:
        assert executor.drain(WAIT), "the background tasks did not finish"
        QApplication.processEvents()
        if executor.drain(0):
            QApplication.processEvents()
            if executor.drain(0):
                return
        assert time.monotonic() < deadline, "the work did not settle"


# A character of each fixture game, picked on the boards before a scenario.
PICKS = {"tgame": "Mario", "game2": "Ryu"}


def _loaded(shell, app, codename="tgame"):
    """Every board made as the desktop's launch makes them (boards 1 / 2
    held), `codename` loaded, and one of its characters picked on the first
    player of both held boards, applied."""
    from src.core.PlayerService import CharacterSlot, PlayerService

    app.readyAll()
    shell.Load(codename)
    _quiet(app)
    with StateManager.SaveBlock():
        for board in (1, 2):
            PlayerService(f"score.{board}.team.1.player.1") \
                .PublishCharacterSlots(
                    [CharacterSlot(character=PICKS[codename])],
                    includeMains=True)
    for board in (1, 2):
        StateManager.CommitHeld(f"score.{board}")
    _quiet(app)


def _restore(shell, rec, app, monkeypatch, ownExecutor):
    _loaded(shell, app, "game2")  # which persists the pick
    # What the launch starts from: no game (bootstrap), the boards as
    # state.json kept them.
    shell.catalog.ResetGameState()
    monkeypatch.setattr(shell.catalog, "DownloadStartGGCharacters",
                        lambda: None)  # the network
    _quiet(app)
    rec.Start()
    # Each shell's start (the window's UiMounted, src/headless.Assemble):
    # the loader's Start(), whose restore is armed for the one scan.
    shell.loader.Start()
    _serially()


def _switch(shell, rec, app, monkeypatch, ownExecutor):
    from src.core.Actions import AutonaActions
    from src.core.PlayerService import CharacterSlot, PlayerService

    _loaded(shell, app)
    # Not applied (反映): a team name, and a pick on the other team.
    AutonaActions.instance.SetTeamName(1, 1, "Alpha")
    with StateManager.SaveBlock():
        PlayerService("score.1.team.2.player.1").PublishCharacterSlots(
            [CharacterSlot(character="Peach")], includeMains=True)
    _quiet(app)
    rec.Start()
    returned = shell.Load("game2", done=rec.Done("game2"))
    _serially()
    rec.Answer("game2", returned)


def _mods(shell, rec, app, monkeypatch, ownExecutor):
    _loaded(shell, app)
    rec.Start()
    returned = shell.Load("tgame", mods_active=True, mods_reload_mode=True,
                          done=rec.Done("tgame with mods"))
    _serially()
    rec.Answer("tgame with mods", returned)


def _burst(shell, rec, app, monkeypatch, ownExecutor):
    _loaded(shell, app)
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    rec.Start()
    returned = {"running": shell.Load("game2", done=rec.Done("running"))}
    assert entered.wait(WAIT)
    for label, codename, mods in (("A", "tgame", False),
                                  ("B", "game2", False),
                                  ("C", "tgame", True)):
        returned[label] = shell.Load(
            codename, mods_active=mods, mods_reload_mode=True,
            done=rec.Done(label))
    release.set()
    _serially()
    for label, answer in returned.items():
        rec.Answer(label, answer)


def _shutdown(shell, rec, app, monkeypatch, ownExecutor):
    _loaded(shell, app)
    # The load stops right after its selection until the shutdown begins
    # (it stopped in the marshal, waiting for the main thread, until
    # ADR-005 S-final SF-6c).
    entered = _afterTheSelection(shell.catalog, monkeypatch,
                                 ownExecutor.stopping)
    rec.Start()
    returned = shell.Load("game2", done=rec.Done("game2"))
    assert entered.wait(WAIT)
    assert ownExecutor.shutdown(WAIT)
    QApplication.processEvents()
    QApplication.processEvents()
    rec.Answer("game2", returned)


SCENARIOS = {
    "restore": "the launch's scan restores the last pick "
               "(SELECTED_GAME_CODENAME: game2) from no game, onto boards "
               "with game2 picks applied",
    "switch": "a switch from tgame to game2 with tgame picks applied on "
              "both held boards, while board 1 has a team name and a pick "
              "not applied (反映)",
    "mods": "tgame reloaded with mods on, with tgame picks applied",
    "burst": "game2 running, then A (tgame), B (game2), C (tgame with mods) "
             "asked for while it runs, from tgame with its picks applied",
    "shutdown": "the shutdown comes after a switch's selection (tgame to "
                "game2, tgame picks applied), before what follows it",
}

RUNS = {"restore": _restore, "switch": _switch, "mods": _mods,
        "burst": _burst, "shutdown": _shutdown}


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_the_game_loader_matches_its_recording(
        shell, ownExecutor, autonaApp, noStreamTitle, monkeypatch,
        scenario):
    rec = _Recorder(shell, monkeypatch, autonaApp)
    try:
        RUNS[scenario](shell, rec, autonaApp, monkeypatch, ownExecutor)
        _waitFor(lambda: not shell.loader._loaderRunning)
    finally:
        rec.Stop()
    _check(scenario, rec.Result(scenario))
