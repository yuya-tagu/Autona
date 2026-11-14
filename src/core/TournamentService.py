"""Setting and clearing the tournament (大会の設定・解除, ADR-005 S4f).

Until S4f a tournament was set or cleared through four entrances that did
not agree:

- the EVENT panel's button and the sidebar's "Set tournament" opened the Qt
  URL dialog, which resolved an admin/brackets URL with a start.gg request
  *on the GUI thread* and re-fetched even the loaded URL;
- the panel's and the sidebar's "clear" called the adapter's
  ``SetTournament(None)``, which kept the selected game;
- /api/v1, the legacy route and Socket.IO emitted the adapter's
  ``tournament_url_update`` as a command, which re-fetched nothing already
  loaded, took the event form only, and -- cleared -- forgot the selected
  game (a slot of the same signal, in bootstrap's wiring).

Now every entrance calls this service, and the differences that were meant
are arguments: ``SetTournament(url, force, fetchMedia)`` and
``ClearTournament()``. What it does, in order:

1. an admin/brackets URL (``start.gg/admin/tournament/<t>/brackets/<id>``)
   is resolved to its event URL by a job of the task executor (a start.gg
   request), never on the caller's thread; an event URL is normalised
   (``/events/`` -> ``/event/``, anything after the event slug dropped);
2. on the main thread (the injected ``onMain``): the URL is saved
   (settings.json ``TOURNAMENT_URL``), the provider adapter loads it -- and
   a tournament newly loaded turns the modded content off: the selected
   game, loaded with its mods, is asked for again without them (the
   injected ``loadGame``; ADR-005 S-final SF-5b-1, the Qt window's mods
   checkbox did it until then, the desktop only) -- or the selected game is
   forgotten (``GameAssetCatalog.ForgetSelection``: owner's decision 2,
   every clear; ``game.selected`` goes False) and the adapter unloads the
   tournament (its ``tournament_changed`` then shows no game), with no game
   loaded again -- and the adapter announces the URL to the shell (the
   sidebar's buttons);
3. with ``fetchMedia``, the tournament's icon and banner are fetched
   (``TournamentMediaService.FetchMedia``).

A set or clear that comes after another one wins: an admin URL whose
resolution answers after a later set / clear is dropped.

How the newest set / clear is going is published in the state (ADR-005
S4g; S4g review M1), so that every client sees it without asking:
``tournamentInfo.load`` -- ``{status, error, revision}``, shaped like
``tournamentInfo.media`` -- with ``tournamentInfo.url``, the URL set last,
beside it. status: ``idle`` (none set), ``resolving`` (an admin URL),
``loading`` (the tournament's data being fetched), ``loaded``, or ``error``
with what went wrong -- ``unresolved`` (the admin URL stood for no event:
nothing changed) or ``load_failed`` (the data did not come: start.gg did not
answer, there is no such event, the adapter's load raised, or the payload
did not apply). Every set / clear takes the next revision, published by the
call itself on the caller's thread, so a request answered (/api/v1's 202)
is in the state already; its progress keeps that revision. The data's
arrival is the shell's to report (``PayloadApplied``, from
bootstrap.wire_tournament_data); the adapter drops the data of a
tournament replaced meanwhile. The launch seeds ``idle``
(StateSchema.SeedPanelState) and the restore then loads.

Lock order: ``StateManager.lock``, then this service's own lock, which is
held only around its own fields -- nothing is called with it held.

The provider adapter is injected (``provider``: an object with
``LoadTournament(url, initialLoading, force)``, ``UnloadTournament()``,
``AnnounceUrl(url)`` and a blocking ``ResolveEventUrl(url)``), because core
may not import it; so is the game load (``loadGame(codename, modsActive)``:
the shell's game loader, asked and not waited for). ``TournamentService
.instance`` is built by ``bootstrap_core()``. Import side effects: none
(agent-pitfalls #1).
"""
import re
import threading

from loguru import logger

from . import StateSchema as schema
from .GameAssetCatalog import GameAssetCatalog
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor
from .TournamentMediaService import TournamentMediaService

URL_SETTING = schema.TOURNAMENT_URL_SETTING
URL_PATH = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_URL_KEY}"
LOAD_PATH = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_LOAD_KEY}"

# The two URL forms taken (the Qt dialog's validators, unchanged: searched,
# not anchored -- a pasted URL may carry a scheme, "www." and more path).
EVENT = "event"
ADMIN = "admin"
_URL_FORMS = (
    (EVENT, re.compile(r"start.gg/tournament/[^/]+/event[s]?/[^/]+")),
    (ADMIN, re.compile(r"start.gg/admin/tournament/[^/]+/brackets/[^/]+")),
)
_EVENT_PREFIX = re.compile(r"(.*start.gg/tournament/[^/]*/event[s]?/[^/]*)")

# What SetTournament answers.
STARTED = "started"
INVALID_URL = "invalid_url"

# How the newest set / clear is going (tournamentInfo.load; see the module
# docstring).
IDLE = "idle"
RESOLVING = "resolving"
LOADING = "loading"
LOADED = "loaded"
ERROR = "error"
# What went wrong, with ERROR ("" otherwise).
UNRESOLVED = "unresolved"
LOAD_FAILED = "load_failed"


def UrlKind(url):
    """EVENT or ADMIN when `url` is a start.gg URL of that form, else
    None."""
    if not isinstance(url, str):
        return None
    for kind, pattern in _URL_FORMS:
        if pattern.search(url):
            return kind
    return None


def NormalizeEventUrl(url):
    """An event URL up to its event slug, with start.gg's "/events/" made
    "/event/" (the API does not take the plural) -- what the dialog and the
    web operation each did. Anything else is returned as it is."""
    if not isinstance(url, str) or "start.gg" not in url:
        return url
    match = _EVENT_PREFIX.match(url)
    if not match:
        return url
    return match.group(0).replace("/events/", "/event/")


class TournamentService:
    """The tournament's set / clear (see the module docstring)."""

    instance: "TournamentService" = None

    def __init__(self, provider, onMain, loadGame=None):
        """`provider`: the tournament provider adapter (see the module
        docstring). `onMain(fn)` runs `fn` on the main thread: right away
        when called there, queued otherwise (the GUI executor).
        `loadGame(codename, modsActive)` asks the shell's game loader for a
        load of `codename` -- a reload when it is the loaded game -- and
        returns without waiting for it (bootstrap_core() passes the web
        operations' load_game, which reaches the loader both shells
        register); None: a tournament newly loaded leaves the mods as they
        are."""
        self._provider = provider
        self._onMain = onMain
        self._loadGame = loadGame
        # Every SetTournament / ClearTournament takes the next generation;
        # an apply that is not the newest one's is dropped.
        self._lock = threading.Lock()
        self._generation = 0
        # The loaded tournament's data -- LOADING until its payload is
        # reported, then LOADED / ERROR -- and the generation that loaded
        # it. What a set of the loaded URL again (not re-fetched) reports.
        self._data = (IDLE, "")
        self._dataGeneration = 0

    # ── reading ──────────────────────────────────────────────────────────

    @staticmethod
    def TournamentUrl() -> str:
        """The tournament URL last set ("" when none): settings.json's
        TOURNAMENT_URL, which survives a restart (Restore loads it)."""
        url = SettingsManager.Get(URL_SETTING, "")
        return url if isinstance(url, str) else ""

    @staticmethod
    def Load() -> dict:
        """How the newest set / clear is going, as published:
        tournamentInfo.load's {"status", "error", "revision"} (see the
        module docstring). Any thread."""
        return schema.ReadTournamentLoad()

    # ── setting / clearing ───────────────────────────────────────────────

    def SetTournament(self, url, force=False, fetchMedia=False,
                      initialLoading=False) -> str:
        """Load the tournament at `url`, a start.gg event or admin/brackets
        URL; STARTED. The loaded URL again is not re-fetched unless `force`.
        With `fetchMedia`, the tournament's icon and banner are fetched once
        it is loaded. INVALID_URL (nothing changes) when `url` is neither
        form. Any thread; the load itself runs on the main thread (right
        away when called there with an event URL). Started, its status
        (resolving / loading) is published at the next revision before
        this returns."""
        kind = UrlKind(url)
        if kind is None:
            logger.warning(f"Tournament: refusing the URL {url!r}")
            return INVALID_URL
        generation = self._NextGeneration(
            LOADING if kind == EVENT else RESOLVING)
        if kind == EVENT:
            self._onMain(lambda: self._Apply(
                generation, NormalizeEventUrl(url), force, fetchMedia,
                initialLoading))
            return STARTED

        def resolved(eventUrl):
            if UrlKind(eventUrl) != EVENT:
                unresolved()
                return
            self._onMain(lambda: self._Apply(
                generation, NormalizeEventUrl(eventUrl), force, fetchMedia,
                initialLoading))

        def unresolved(error=None):
            # Nothing changes (S4f): the tournament loaded until now stays.
            # Told to whoever asked through the status, not only logged
            # (S4f review L14).
            logger.error(
                f"Tournament: {url} could not be resolved to an event")
            self._SetStatus(generation, ERROR, UNRESOLVED)

        TaskExecutor.instance.submit(
            self._provider.ResolveEventUrl, url,
            task_name="tournament URL", then=resolved, on_error=unresolved)
        return STARTED

    def ClearTournament(self):
        """Unload the tournament: its URL is forgotten, the tournament info
        is emptied, and so is the selected game. Any thread; the clear
        itself runs on the main thread (right away when called there).
        Idle is published at the next revision before this returns."""
        generation = self._NextGeneration(IDLE)
        self._onMain(lambda: self._ApplyClear(generation))

    def Restore(self):
        """Load the tournament URL saved by the last session, as the launch's
        restore (the payload keeps the fields the operator edited). Nothing
        when there is none."""
        url = self.TournamentUrl()
        if url:
            self.SetTournament(url, initialLoading=True)

    def PayloadApplied(self, data, applied):
        """The shell's report of a tournament payload (tournament_data_
        updated) having been applied -- `applied` False when it failed
        (TournamentInfoService.ApplyProviderPayload). It settles the load
        the status is waiting for: LOADED, or ERROR / LOAD_FAILED when the
        payload failed or carries no tournament name (what the provider
        answers when start.gg did not answer or has no such event). Ignored
        when no load is waiting (the empty payload of a clear, a late one).
        The URL is published again whatever it was: a payload that failed
        dropped it (TournamentInfoService.ApplyTournamentData). Main
        thread."""
        loaded = bool(applied) and isinstance(data, dict) and bool(
            data.get(schema.TOURNAMENT_NAME_KEY))
        with StateManager.lock:
            self._PublishUrl()
            with self._lock:
                if self._data[0] != LOADING:
                    return
                self._data = (LOADED, "") if loaded else (ERROR, LOAD_FAILED)
                status, error = self._data
                # Published only while the set that loaded it is the newest
                # one: a later set still queued says LOADING for its own.
                current = self._dataGeneration == self._generation
            load = self.Load()
            if current and load["status"] == LOADING:
                self._Publish(status, error, load["revision"])

    # ── the published status ─────────────────────────────────────────────

    @staticmethod
    def _Publish(status, error, revision):
        # One write of the whole dict: a reader never sees half of it.
        StateManager.SetIfChanged(LOAD_PATH, {
            "status": status, "error": error, "revision": revision})

    def _PublishUrl(self):
        StateManager.SetIfChanged(URL_PATH, self.TournamentUrl())

    def _NextGeneration(self, status):
        """The next generation, its status published at the next revision
        (in the same hold of StateManager.lock: generations and revisions
        go up together)."""
        with StateManager.lock:
            with self._lock:
                self._generation += 1
                generation = self._generation
            self._Publish(status, "", self.Load()["revision"] + 1)
        return generation

    def _IsCurrent(self, generation):
        with self._lock:
            return generation == self._generation

    def _SetStatus(self, generation, status, error=""):
        """Publish the status of `generation` (at its revision), while it
        is the newest set / clear."""
        with StateManager.lock:
            if not self._IsCurrent(generation):
                return
            self._Publish(status, error, self.Load()["revision"])

    # ── on the main thread ───────────────────────────────────────────────

    def _Apply(self, generation, url, force, fetchMedia, initialLoading):
        if not self._IsCurrent(generation):
            logger.info(f"Tournament: {url} was superseded before it loaded")
            return
        SettingsManager.Set(URL_SETTING, url)
        self._PublishUrl()
        # Waiting for the payload from here: it may be reported before the
        # load returns (a provider that answers at once).
        with self._lock:
            previous = self._data
            self._data = (LOADING, "")
            self._dataGeneration = generation
        try:
            reloaded = self._provider.LoadTournament(
                url, initialLoading=initialLoading, force=force)
        except Exception:
            # No payload is coming for it (S4g review L2): a failed load,
            # not one left loading.
            logger.opt(exception=True).error(
                f"Tournament: {url} could not be loaded")
            with self._lock:
                if self._dataGeneration == generation:
                    self._data = (ERROR, LOAD_FAILED)
            self._SetStatus(generation, ERROR, LOAD_FAILED)
            self._provider.AnnounceUrl(url)
            return
        if reloaded:
            # Where the window's mods checkbox was unchecked until SF-5b-1
            # (on tournament_changed, emitted inside the load): after the
            # load, ahead of the announcement.
            self._TurnModsOff()
        with self._lock:
            if not reloaded and self._data == (LOADING, ""):
                # The loaded URL again, not re-fetched: what its data did.
                self._data = previous
            status, error = self._data
        self._SetStatus(generation, status, error)
        self._provider.AnnounceUrl(url)
        if fetchMedia:
            media = TournamentMediaService.instance
            if media is not None:
                media.FetchMedia()

    def _TurnModsOff(self):
        """A tournament newly loaded turns the modded content off (ADR-005
        S-final SF-5b-1): the selected game, loaded with its mods
        (game.mods_active), is asked for again without them. Both shells,
        through the injected loader; until SF-5b-1 the Qt window unchecked
        its mods checkbox on tournament_changed, which reloaded the game --
        the desktop only. Asked, not waited for (the loader is a job). The
        request is made before the tournament's data can arrive (it comes
        to this thread later, queued), so the game the data selects
        (SetGameFromProvider) is asked for after it and wins (the latest
        request wins).

        Nothing when no game is selected -- after a clear, whose forgotten
        game is not loaded again: the tournament's own game comes with its
        data, mods off -- or its mods are off already. Read from the
        catalog's snapshot, which the state's `game` is published from
        (game.selected / game.mods_active). A failed request is logged: the
        tournament is loaded all the same."""
        catalog = GameAssetCatalog.instance
        if self._loadGame is None or catalog is None:
            return
        snapshot = catalog.Current()
        if not snapshot.codename or not snapshot.modsActive:
            return
        logger.info(f"Tournament: {snapshot.codename} asked for again "
                    f"without its modded content")
        try:
            self._loadGame(snapshot.codename, False)
        except Exception:
            logger.opt(exception=True).error(
                f"Tournament: could not ask for {snapshot.codename} without "
                f"its modded content")

    def _ApplyClear(self, generation):
        if not self._IsCurrent(generation):
            return
        SettingsManager.Set(URL_SETTING, "")
        self._PublishUrl()
        # Ahead of the unload, whose empty payload waits for nothing.
        with self._lock:
            self._data = (IDLE, "")
        # Every clear forgets the selected game (owner's decision 2, S4f):
        # the next tournament's game is picked from it, not from the last
        # one's leftovers. Ahead of the unload, whose tournament_changed the
        # sidebar's game button re-reads the selection on, and of the
        # announcement. The state's game.selected goes False there
        # (ADR-005 S-final SF-5b-1), and no game is loaded again: until
        # SF-5b-1 the window's mods checkbox, unchecked on that
        # tournament_changed, asked for a reload of "no game" (skipped).
        if GameAssetCatalog.instance is not None:
            GameAssetCatalog.instance.ForgetSelection()
        self._provider.UnloadTournament()
        self._provider.AnnounceUrl("")
