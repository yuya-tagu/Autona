"""What the app is busy with: the long-running jobs the footer shows.

Qt-free (ADR-005 S-final SF-2). Until then this was the Qt-side
``Helpers/AutonaStatusManager`` -- a stack of ready-made Japanese messages
behind a module-level QObject signal, read by the main window's
``QStatusBar`` alone. Here the jobs are words the reader translates: a
``code`` from ``CODES`` and its ``args``. The desktop footer turns them into
its Qt translation (``AutonaLocaleHelper.StatusText``), the web clients into
their i18next one (``status_<code>``), so the English UI shows no Japanese.

A job is one entry, in push order: ``{"id", "code", "args", "done",
"total"}``. ``total`` 0 means "no measurable progress" (the message alone);
otherwise ``done`` of ``total`` steps are done. The last job is the one the
footer shows (``Current``). ``Snapshot`` is the whole list with a revision
that grows on every change, what ``GET /api/v1/status`` answers and the
Socket.IO ``status`` event carries.

Who reports: the start.gg fetches (``StartGGDataProvider``), the provider's
jobs (``AutonaTournamentDataProvider.RunWithProvider``: the Top 8 and the
result imports) and the game asset load (``Progress``, through
``GameAssetCatalog.SelectGame``'s ``progress``).

Process-wide and static, like the class-level channels of
``SetFollowService.changes`` / ``AutonaActions.battleFormatChanges``:
importing builds a lock, an empty dict and a channel, nothing else.

Thread rules
------------
- Any thread may push, update and pop. ``_lock`` guards the jobs only.
- ``changes`` is notified after every change, outside ``_lock``, on the
  reporting thread, with no value: a subscriber reads ``Snapshot`` /
  ``Current`` (the latest, whichever notification it answers).
- The asset load reports under the game catalog's writer lock (its
  ``progress`` is the one callback ``SelectGame`` calls holding it), so a
  subscriber must not block: the one in the app, ``StateManagerBridge``,
  re-emits a Qt signal, queued to the GUI thread off it, and the web
  server's flusher only queues the notice (``WebServer.on_status_change``).
"""
import contextlib
import threading

from loguru import logger

from .Observable import ObserverChannel


# Every status the app reports: its code and the names of its args. The
# readers translate the code (the Qt footer's table in AutonaLocaleHelper
# with the "status" context of src/i18n/*.ts, the web's i18next keys
# status_<code>); tests/test_status_service.py checks that every table has
# every code with these args.
CODES = {
    # The start.gg fetches (StartGGDataProvider).
    "startgg_bracket": (),
    "startgg_set": (),
    "startgg_sets": (),
    "startgg_sets_page": ("page", "pages"),
    "startgg_sets_failed": ("failed", "pages"),
    "startgg_entrants": (),
    "startgg_entrants_page": ("page", "pages"),
    # The game asset load.
    "assets_load": ("codename",),
    # The provider's jobs (Top8Service / ResultService IMPORT_STATUS).
    "top8_import": (),
    "result_import": (),
}


class StatusService:
    # Told after every change, with no value (see the module docstring).
    changes = ObserverChannel("status")

    _lock = threading.RLock()
    # token -> the job. Push order is the dict's insertion order.
    _jobs = {}
    _nextToken = 0
    _revision = 0

    @staticmethod
    def Push(code, args=None, done=0, total=0):
        """Start showing a job. Returns its token, for Update / Pop."""
        StatusService._CheckCode(code)
        with StatusService._lock:
            token = StatusService._nextToken
            StatusService._nextToken += 1
            StatusService._jobs[token] = StatusService._Job(
                token, code, args, done, total)
            StatusService._revision += 1
        StatusService.changes.notify()
        return token

    @staticmethod
    def Update(token, code, args=None, done=0, total=0):
        """Replace a pushed job's words and progress. Nothing for a token
        not shown (popped already, or None)."""
        StatusService._CheckCode(code)
        with StatusService._lock:
            if token not in StatusService._jobs:
                return
            StatusService._jobs[token] = StatusService._Job(
                token, code, args, done, total)
            StatusService._revision += 1
        StatusService.changes.notify()

    @staticmethod
    def Pop(token):
        """Stop showing a job. Nothing for None or a token not shown."""
        if token is None:
            return
        with StatusService._lock:
            if StatusService._jobs.pop(token, None) is None:
                return
            StatusService._revision += 1
        StatusService.changes.notify()

    @staticmethod
    @contextlib.contextmanager
    def Show(code, args=None):
        """The job shown while the block runs."""
        token = StatusService.Push(code, args)
        try:
            yield token
        finally:
            StatusService.Pop(token)

    @staticmethod
    def Progress(code, args=None, every=1):
        """A ``progress(done, total)`` callable for a load that reports
        steps (``GameAssetCatalog.SelectGame``'s): see StatusProgress."""
        return StatusProgress(code, args, every)

    @staticmethod
    def Snapshot():
        """``{"revision": n, "jobs": [job, ...]}``, the jobs in push order
        and copied: what GET /api/v1/status answers and the Socket.IO
        ``status`` event carries."""
        with StatusService._lock:
            return {"revision": StatusService._revision,
                    "jobs": [StatusService._Copy(job)
                             for job in StatusService._jobs.values()]}

    @staticmethod
    def Current():
        """The job the footer shows (the last pushed still shown), copied;
        None when nothing runs."""
        with StatusService._lock:
            if not StatusService._jobs:
                return None
            return StatusService._Copy(
                next(reversed(StatusService._jobs.values())))

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _Job(token, code, args, done, total):
        return {"id": token, "code": str(code), "args": dict(args or {}),
                "done": int(done), "total": int(total)}

    @staticmethod
    def _Copy(job):
        return {**job, "args": dict(job["args"])}

    @staticmethod
    def _CheckCode(code):
        # Shown anyway: the readers show the code itself for one they do not
        # know, and a footer's mistake must not stop the job it reports.
        if code not in CODES:
            logger.warning(f"unknown status code {code!r}")


class StatusProgress:
    """The footer of a load that reports ``progress(done, total)``.

    The job is pushed on the first report, so a load that never starts (the
    catalog skips it before reporting) shows nothing. Its progress is
    updated when ``done`` is a multiple of ``every`` or reaches ``total``
    (the asset load reports every character; every 5 is enough for the
    bar). ``Close()`` pops it, and is harmless when it was never shown.

    Reports and closes from one thread (the load's). It never blocks: the
    catalog calls it under its writer lock."""

    def __init__(self, code, args=None, every=1):
        self.code = code
        self.args = dict(args or {})
        self.every = max(1, int(every))
        self.token = None

    def __call__(self, done, total):
        if self.token is None:
            self.token = StatusService.Push(self.code, self.args)
        if done > 0 and (done % self.every == 0 or done >= total):
            StatusService.Update(self.token, self.code, self.args,
                                 done=done, total=total)

    def Close(self):
        token, self.token = self.token, None
        StatusService.Pop(token)
