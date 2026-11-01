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
   (settings.json ``TOURNAMENT_URL``), the provider adapter loads it -- or
   the selected game is forgotten (``GameAssetCatalog.ForgetSelection``:
   owner's decision 2, every clear) and the adapter unloads the tournament
   (its ``tournament_changed`` then shows no game) -- and the adapter
   announces the URL to the shell (the sidebar's buttons);
3. with ``fetchMedia``, the tournament's icon and banner are fetched
   (``TournamentMediaService.FetchMedia``).

A set or clear that comes after another one wins: an admin URL whose
resolution answers after a later set / clear is dropped.

The provider adapter is injected (``provider``: an object with
``LoadTournament(url, initialLoading, force)``, ``UnloadTournament()``,
``AnnounceUrl(url)`` and a blocking ``ResolveEventUrl(url)``), because core
may not import it. ``TournamentService.instance`` is built by
``bootstrap_core()``. Import side effects: none (agent-pitfalls #1).
"""
import re
import threading

from loguru import logger

from .GameAssetCatalog import GameAssetCatalog
from .SettingsManager import SettingsManager
from .TaskExecutor import TaskExecutor
from .TournamentMediaService import TournamentMediaService

URL_SETTING = "TOURNAMENT_URL"

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

    def __init__(self, provider, onMain):
        """`provider`: the tournament provider adapter (see the module
        docstring). `onMain(fn)` runs `fn` on the main thread: right away
        when called there, queued otherwise (the GUI executor)."""
        self._provider = provider
        self._onMain = onMain
        # Every SetTournament / ClearTournament takes the next generation;
        # an apply that is not the newest one's is dropped.
        self._lock = threading.Lock()
        self._generation = 0

    # ── reading ──────────────────────────────────────────────────────────

    @staticmethod
    def TournamentUrl() -> str:
        """The tournament URL last set ("" when none): settings.json's
        TOURNAMENT_URL, which survives a restart (Restore loads it)."""
        url = SettingsManager.Get(URL_SETTING, "")
        return url if isinstance(url, str) else ""

    # ── setting / clearing ───────────────────────────────────────────────

    def SetTournament(self, url, force=False, fetchMedia=False,
                      initialLoading=False) -> str:
        """Load the tournament at `url`, a start.gg event or admin/brackets
        URL; STARTED. The loaded URL again is not re-fetched unless `force`.
        With `fetchMedia`, the tournament's icon and banner are fetched once
        it is loaded. INVALID_URL (nothing changes) when `url` is neither
        form. Any thread; the load itself runs on the main thread (right
        away when called there with an event URL)."""
        kind = UrlKind(url)
        if kind is None:
            logger.warning(f"Tournament: refusing the URL {url!r}")
            return INVALID_URL
        generation = self._NextGeneration()
        if kind == EVENT:
            self._onMain(lambda: self._Apply(
                generation, NormalizeEventUrl(url), force, fetchMedia,
                initialLoading))
            return STARTED

        def resolved(eventUrl):
            if UrlKind(eventUrl) != EVENT:
                logger.error(
                    f"Tournament: {url} could not be resolved to an event")
                return
            self._onMain(lambda: self._Apply(
                generation, NormalizeEventUrl(eventUrl), force, fetchMedia,
                initialLoading))

        TaskExecutor.instance.submit(
            self._provider.ResolveEventUrl, url,
            task_name="tournament URL", then=resolved)
        return STARTED

    def ClearTournament(self):
        """Unload the tournament: its URL is forgotten, the tournament info
        is emptied, and so is the selected game. Any thread; the clear
        itself runs on the main thread (right away when called there)."""
        generation = self._NextGeneration()
        self._onMain(lambda: self._ApplyClear(generation))

    def Restore(self):
        """Load the tournament URL saved by the last session, as the launch's
        restore (the payload keeps the fields the operator edited). Nothing
        when there is none."""
        url = self.TournamentUrl()
        if url:
            self.SetTournament(url, initialLoading=True)

    # ── on the main thread ───────────────────────────────────────────────

    def _NextGeneration(self):
        with self._lock:
            self._generation += 1
            return self._generation

    def _IsCurrent(self, generation):
        with self._lock:
            return generation == self._generation

    def _Apply(self, generation, url, force, fetchMedia, initialLoading):
        if not self._IsCurrent(generation):
            logger.info(f"Tournament: {url} was superseded before it loaded")
            return
        SettingsManager.Set(URL_SETTING, url)
        self._provider.LoadTournament(
            url, initialLoading=initialLoading, force=force)
        self._provider.AnnounceUrl(url)
        if fetchMedia:
            media = TournamentMediaService.instance
            if media is not None:
                media.FetchMedia()

    def _ApplyClear(self, generation):
        if not self._IsCurrent(generation):
            return
        SettingsManager.Set(URL_SETTING, "")
        # Every clear forgets the selected game (owner's decision 2, S4f):
        # the next tournament's game is picked from it, not from the last
        # one's leftovers. Ahead of the unload, whose tournament_changed the
        # game pickers re-read the selection on (the EVENT panel's combo,
        # the sidebar's button), and of the announcement.
        if GameAssetCatalog.instance is not None:
            GameAssetCatalog.instance.ForgetSelection()
        self._provider.UnloadTournament()
        self._provider.AnnounceUrl("")
