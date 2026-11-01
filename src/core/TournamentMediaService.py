"""The tournament's icon and banner (大会アイコン / バナー, ADR-005 S4f).

What the EVENT panel used to do itself with QPixmap, ``requests`` and the
file system -- so that it happens with no panel, headless too, and through
one path whichever end asked (the panel's buttons now; the Web page and
/api/v1 in S4g):

- **importing** an image (the operator's file, or the tournament's own):
  checked and scaled down -- the icon to a longest side of 512, the banner
  to 1920 (it used to be saved as it came, owner's decision 4) -- and saved
  as PNG at the paths the overlays and the generators have always read
  (``./layout/logo.png``, ``./user_data/tournament_banner.png``), through a
  temporary file and ``os.replace`` (core/ImageImport.py, shared with the
  stream org assets). Each file's mtime is published as its cache-bust
  version: ``tournamentInfo.logoVersion`` (as before) and
  ``tournamentInfo.bannerVersion`` (new);
- **fetching** the loaded tournament's icon and banner from start.gg, as a
  job of the task executor whose progress is the state's
  ``tournamentInfo.media`` (``{status, error, revision}``, shaped like
  ``stream.thumbnail``); one at a time (BUSY), given up once the executor
  is stopping. A fetch asked for another tournament while one runs (the
  tournament set meanwhile) is run after it, by the same job (QUEUED);
- **resetting** them: the icon back to Autona's own, the banner removed
  (its readers fall back to the bundled default banner).

A tournament is told by its URL (``provider.url``): a provider rebuilt for
the URL loaded already (a forced reload) is the same tournament, so a fetch
running for it goes on.

The Qt side keeps the previews and the ``media_updated`` signal, which the
provider adapter emits when ``bannerVersion`` moves (the thumbnail / result
previews listen to it, unchanged).

``TournamentMediaService.instance`` is built by ``bootstrap_core()``; the
loaded tournament's provider is injected there (``currentProvider``),
because core may not import the adapter. Import side effects: none
(agent-pitfalls #1).
"""
import os
import threading

import requests
from loguru import logger

from . import ImageImport
from . import StateSchema as schema
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor
from .TournamentInfoService import TOURNAMENT_LOGO_PATH

# Autona's own icon, which "reset" puts back as the tournament icon (what
# the panel's 既定に戻す copied).
DEFAULT_ICON_PATH = "./assets/icons/icon.png"

ICON = "icon"
BANNER = "banner"
# kind -> (its file, its version's key, the longest side it is scaled to).
MEDIA = {
    ICON: (TOURNAMENT_LOGO_PATH,
           f"{schema.TOURNAMENT_INFO_ROOT}."
           f"{schema.TOURNAMENT_LOGO_VERSION_KEY}",
           512),
    BANNER: (schema.TOURNAMENT_BANNER_PATH,
             f"{schema.TOURNAMENT_INFO_ROOT}."
             f"{schema.TOURNAMENT_BANNER_VERSION_KEY}",
             1920),
}

MEDIA_PATH = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_MEDIA_KEY}"
DOWNLOAD_TIMEOUT_SECS = 20

# What ImportMedia answers (core/ImageImport.py's).
IMPORTED = ImageImport.IMPORTED
INVALID_IMAGE = ImageImport.INVALID_IMAGE
SAVE_FAILED = ImageImport.SAVE_FAILED

# tournamentInfo.media's status.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# tournamentInfo.media's error (a reader words them). The run is DONE when
# it imported something and nothing it found failed.
NO_MEDIA = "no_media"            # the tournament has neither an icon nor a banner
FETCH_FAILED = "fetch_failed"    # a lookup or a download failed, or no image came
# SAVE_FAILED (above): a file could not be written.
SUPERSEDED = "superseded"        # another tournament (or none) was loaded meanwhile
CANCELLED = "cancelled"          # the shutdown stopped it

# What FetchMedia answers.
STARTED = "started"
BUSY = "busy"                    # a fetch for this tournament runs already
QUEUED = "queued"                # run once the fetch running (another tournament's) ends
NO_TOURNAMENT = "no_tournament"


class _Stopped(Exception):
    """The executor is shutting down: the fetch gives up."""


def _Tournament(provider):
    """Which tournament `provider` fetches for: its URL -- a provider
    rebuilt for the same URL (a forced reload) is the same tournament -- or
    the provider itself when it has none. None for no provider."""
    if provider is None:
        return None
    url = getattr(provider, "url", None)
    return url if isinstance(url, str) and url else provider


class TournamentMediaService:
    """The tournament icon / banner operations (see the module docstring).
    One per process: the running fetch is its own."""

    instance: "TournamentMediaService" = None

    def __init__(self, currentProvider=None):
        """`currentProvider()` answers the loaded tournament's provider (an
        object with blocking ``GetIconURL(strict=True)`` /
        ``GetBannerURL(strict=True)`` -- None when the tournament has none,
        an exception when the lookup failed -- and its ``url``) or None; it
        is called from the fetch job. None: no tournament is ever loaded
        (FetchMedia answers NO_TOURNAMENT). What a crash left of the files'
        temporary copies is removed here."""
        self._currentProvider = currentProvider

        # One import or reset at a time, so a file and its published
        # version always belong together.
        self._mediaLock = threading.Lock()

        # The fetch's claim, under _fetchLock (never held while
        # StateManager.lock is taken): True from a FetchMedia that started a
        # job until that job's last run is over; the tournament the run is
        # for; whether a FetchMedia asked for another one meanwhile (QUEUED).
        self._fetchLock = threading.Lock()
        self._fetchRunning = False
        self._fetchTournament = None
        self._fetchAgain = False

        ImageImport.RemoveLeftovers(
            *(path for path, _key, _side in MEDIA.values()))

    # ── importing / resetting ────────────────────────────────────────────

    def ImportMedia(self, kind, data) -> str:
        """Register the tournament's "icon" or "banner" from an image's bytes
        (PNG, JPEG, MPO -- its first frame -- or BMP, up to
        ImageImport.MAX_PIXELS): scaled down to fit its longest side (MEDIA),
        turned upright by its EXIF orientation, saved as PNG at its path and
        its version published; IMPORTED. INVALID_IMAGE when `kind` is
        unknown or `data` is not such an image, SAVE_FAILED when the file
        could not be written (nothing changes in either case). Any
        thread."""
        spec = MEDIA.get(kind)
        if spec is None:
            logger.warning(f"Tournament media: refusing {kind!r}")
            return INVALID_IMAGE
        path, versionKey, longestSide = spec
        encoded = ImageImport.EncodePng(
            data, longestSide, label=f"Tournament media: the {kind}")
        if encoded is None:
            return INVALID_IMAGE
        with self._mediaLock:
            try:
                ImageImport.WriteFile(path, encoded)
            except OSError:
                logger.opt(exception=True).error(
                    f"Tournament media: the {kind} could not be written to "
                    f"{path}")
                return SAVE_FAILED
            schema.PublishAssetVersion(versionKey, path)
        logger.info(f"Tournament media: the {kind} was saved")
        return IMPORTED

    def ResetMedia(self, kind=None) -> bool:
        """Put the tournament's "icon" back to Autona's own and remove its
        "banner" (its readers fall back to the bundled default); both when
        `kind` is None. Their versions are published in one save cycle.
        False when `kind` is unknown or a file could not be changed (that
        one is left as it was)."""
        if kind is not None and kind not in MEDIA:
            logger.warning(f"Tournament media: cannot reset {kind!r}")
            return False
        kinds = (ICON, BANNER) if kind is None else (kind,)
        ok = True
        with StateManager.SaveBlock():
            with self._mediaLock:
                for each in kinds:
                    path, versionKey, _side = MEDIA[each]
                    if each == ICON:
                        ok = self._ResetIcon(path) and ok
                    else:
                        ok = self._RemoveBanner(path) and ok
                    schema.PublishAssetVersion(versionKey, path)
        return ok

    @staticmethod
    def _ResetIcon(path) -> bool:
        try:
            with open(DEFAULT_ICON_PATH, "rb") as file:
                data = file.read()
        except OSError:
            logger.opt(exception=True).error(
                f"Tournament media: Autona's icon {DEFAULT_ICON_PATH} could "
                "not be read; the tournament icon is left as it was")
            return False
        try:
            ImageImport.WriteFile(path, data)
        except OSError:
            logger.opt(exception=True).error(
                f"Tournament media: Autona's icon could not be written to "
                f"{path}; the tournament icon is left as it was")
            return False
        return True

    @staticmethod
    def _RemoveBanner(path) -> bool:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # none was registered
        except OSError:
            logger.opt(exception=True).error(
                f"Tournament media: the banner {path} could not be removed")
            return False
        return True

    # ── fetching from the tournament ─────────────────────────────────────

    def FetchRunning(self) -> bool:
        with self._fetchLock:
            return self._fetchRunning

    def FetchMedia(self) -> str:
        """Start fetching the loaded tournament's icon and banner as a job of
        the task executor; STARTED. ``tournamentInfo.media`` says "running"
        before this returns and "done" or "error" (with why) when the job
        ends.

        While a fetch runs: BUSY (nothing more) when it is this
        tournament's; QUEUED when it is another one's (the tournament was
        set meanwhile, e.g. while an admin URL resolved): once it ends --
        superseded, as a run for a tournament no longer loaded does -- the
        same job runs again for the tournament loaded then, under a new
        revision. NO_TOURNAMENT (nothing started, nothing published) when no
        tournament is loaded."""
        provider = self._Provider()
        if provider is None:
            return NO_TOURNAMENT
        tournament = _Tournament(provider)
        with self._fetchLock:
            if self._fetchRunning:
                if tournament == self._fetchTournament:
                    return BUSY
                self._fetchAgain = True
                return QUEUED
            self._fetchRunning = True
            self._fetchTournament = tournament
        revision = None
        try:
            revision = self._BeginRun()
            executor = TaskExecutor.instance
            future = executor.submit(
                self._Run, provider, revision, executor.stopping,
                task_name="tournament icon and banner")
        except BaseException:
            self._Release()
            self._PublishEnd((ERROR, FETCH_FAILED), revision)
            raise
        # A job the shutdown cancels (queued then, or submitted after it)
        # never runs, only its done callbacks -- right away when it is
        # cancelled already.
        future.add_done_callback(
            lambda done: done.cancelled() and self._Cancelled(revision))
        return STARTED

    def _Provider(self):
        if self._currentProvider is None:
            return None
        try:
            return self._currentProvider()
        except Exception:
            logger.opt(exception=True).error(
                "Tournament media: the loaded tournament could not be read")
            return None

    def _Media(self) -> dict:
        media = StateManager.Get(MEDIA_PATH)
        default = schema.DefaultTournamentMedia()
        if not isinstance(media, dict):
            return default
        revision = media.get("revision")
        return {
            "status": media.get("status", default["status"]),
            "error": media.get("error", ""),
            "revision": revision if isinstance(revision, int)
            and not isinstance(revision, bool) else 0,
        }

    def _PublishMedia(self, status, error, revision):
        # One Set of the whole dict: a reader never sees half a result.
        StateManager.Set(MEDIA_PATH, {
            "status": status, "error": error, "revision": revision})

    def _BeginRun(self) -> int:
        """Publish a new run as running; its revision."""
        with StateManager.lock:
            revision = self._Media()["revision"] + 1
            self._PublishMedia(RUNNING, "", revision)
        return revision

    def _PublishEnd(self, result, revision):
        """Publish a run's `result` -- (DONE, "") or (ERROR, why) -- unless a
        later run was published already (the claim is released before
        this, so one may have started); None: whatever run is published."""
        status, error = result
        with StateManager.lock:
            current = self._Media()["revision"]
            if revision is not None and revision != current:
                return
            self._PublishMedia(status, error, current)

    def _Release(self):
        with self._fetchLock:
            self._fetchRunning = False
            self._fetchTournament = None
            self._fetchAgain = False

    def _Cancelled(self, revision):
        self._Release()
        self._PublishEnd((ERROR, CANCELLED), revision)

    def _NextProvider(self):
        """After a run: the provider to fetch again for -- the tournament
        loaded when a FetchMedia for another one came meanwhile (QUEUED)
        -- or None, the claim released in the same step as finding that
        nobody asked (so a FetchMedia is never lost in between)."""
        while True:
            with self._fetchLock:
                if not self._fetchAgain:
                    self._fetchRunning = False
                    self._fetchTournament = None
                    return None
                self._fetchAgain = False
            provider = self._Provider()
            if provider is None:
                continue  # unloaded meanwhile: unless asked again, done
            with self._fetchLock:
                self._fetchTournament = _Tournament(provider)
            return provider

    def _Run(self, provider, revision, stopping):
        """The job: a run (_Fetch) for `provider`, then another for each
        tournament a FetchMedia asked for meanwhile, each under its own
        revision; the last one's result is published."""
        claimed = True
        try:
            while True:
                result = self._Fetch(provider, stopping)
                if result == (ERROR, CANCELLED):
                    self._Release()
                    following = None
                else:
                    following = self._NextProvider()
                if following is None:
                    claimed = False
                    self._PublishEnd(result, revision)
                    return result
                logger.info("Tournament media: fetching again, for the "
                            "tournament set while the last fetch ran")
                provider = following
                revision = self._BeginRun()
        except BaseException:
            if claimed:
                self._Release()
                self._PublishEnd((ERROR, FETCH_FAILED), revision)
            raise

    def _Fetch(self, provider, stopping):
        """One run: the icon, then the banner, each looked up, downloaded
        and imported. (DONE, "") when something was imported and nothing
        found failed; (ERROR, why) otherwise. Gives up (CANCELLED) once
        `stopping` (the executor's) is set, before each request; drops what
        it downloaded (SUPERSEDED) once `provider`'s tournament is no longer
        the loaded one."""
        tournament = _Tournament(provider)
        outcomes = []
        try:
            for kind, lookup in ((ICON, provider.GetIconURL),
                                 (BANNER, provider.GetBannerURL)):
                outcomes.append(self._FetchOne(kind, lookup, tournament,
                                               stopping))
                if outcomes[-1] == SUPERSEDED:
                    break
        except _Stopped:
            return ERROR, CANCELLED
        for error in (SUPERSEDED, SAVE_FAILED, FETCH_FAILED):
            if error in outcomes:
                return ERROR, error
        if IMPORTED in outcomes:
            return DONE, ""
        return ERROR, NO_MEDIA

    def _FetchOne(self, kind, lookup, tournament, stopping):
        """One medium: IMPORTED, None (the tournament has none) or the
        run's error for it. _Stopped when `stopping` is set before the
        lookup or the download (each up to 20 s) or after them."""
        if stopping.is_set():
            raise _Stopped()
        try:
            url = lookup(strict=True)
        except Exception:
            if stopping.is_set():
                raise _Stopped()
            logger.opt(exception=True).warning(
                f"Tournament media: start.gg could not be asked for the "
                f"{kind}'s URL")
            return FETCH_FAILED
        if not url:
            logger.info(f"Tournament media: the tournament has no {kind}")
            return None
        if stopping.is_set():
            raise _Stopped()
        try:
            response = requests.get(url, headers=ImageImport.DOWNLOAD_HEADERS,
                                    timeout=DOWNLOAD_TIMEOUT_SECS)
            response.raise_for_status()
            data = response.content
        except Exception:
            logger.opt(exception=True).warning(
                f"Tournament media: the {kind} could not be downloaded "
                f"from {url}")
            return FETCH_FAILED
        if stopping.is_set():
            raise _Stopped()
        if _Tournament(self._Provider()) != tournament:
            logger.info(
                f"Tournament media: dropping the {kind} of a tournament "
                "that is no longer loaded")
            return SUPERSEDED
        result = self.ImportMedia(kind, data)
        if result == INVALID_IMAGE:
            logger.warning(f"Tournament media: what {url} answered for the "
                           f"{kind} is not an image taken in")
            return FETCH_FAILED
        return result
