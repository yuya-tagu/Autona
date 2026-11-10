"""One generated image at a time, in the background (ADR-005 S4q).

What the match thumbnail (core/ThumbnailService, ADR-005 S4o, where this was
written) and the tournament result (core/ResultService) share: the lane of
their renders, the encoding and the files. A service is a subclass: it says
what it draws (``_RunPayload``), what the state calls the run
(``RENDER_PATH``, ``_ReadRender``), where the files go and how they are named.

The lane, one run at a time:

- A request while nothing runs starts a run: the state says "running" before
  the request returns.
- A preview during a run is kept for afterwards, the last one standing for all
  of them: the payload is built when a run starts, so it draws the newest
  settings anyway. A generate during a preview is kept the same way (a save
  does not fail because a preview was on its way), and stands in for a kept
  preview: it draws the same picture and updates the preview's file too. One
  that finds nothing to save (RenderDataMissing, with one of MISSING_CODES) is
  followed by the preview it stood in for, which draws a placeholder.
  Unless the service says the generate draws another picture than the
  preview's (``_GenerateDrawsPreview``: ThumbnailService's board 2, ADR-005
  S4z): that one neither stands in for a preview nor writes the preview's
  file, and a preview kept with it is drawn after it, under the next revision
  (the one asked before the generate came is answered by that later run, as
  a preview owed after a stand-in is).
- A generate while one runs or is kept: BUSY (nothing changes).
- Every run gets the next revision, which Preview / Generate answer whether it
  starts now or is kept: a client knows which published result is its own.

A run: the payload on the requester's thread (it reads files and the state,
and waits for nobody); the render on the GUI thread, started by ``render`` and
never waited for -- no nested event loop on this path (agent-pitfalls #9); the
encoding and the files in a job of the task executor, so that a PNG of
1920x1080 does not stall the GUI thread. The result is published from whichever
thread ended the run, and only then the next run is queued with ``later`` (the
GUI executor's: a job's thread may not wait for the GUI thread).

The lock order is StateManager.lock -> the lane's lock: the lane's lock only
guards the lane's own fields, and neither is held across a render or a job.

Import side effects: none (agent-pitfalls #1).
"""
import threading
import time
from pathlib import Path

from loguru import logger

from . import ImageImport
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

# What a run does.
PREVIEW = "preview"
GENERATE = "generate"
# The published status.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# The published error: one of these, ImageImport's SAVE_FAILED and a service's
# MISSING_CODES, never an exception's text (a path of the operator's has no
# place in what every client is sent; the log has the rest).
PAYLOAD_FAILED = "payload_failed"  # the template or the settings would not read
RENDER_FAILED = "render_failed"  # the renderer gave no image, or did not start
CANCELLED = "cancelled"         # stopped by the shutdown
SAVE_FAILED = ImageImport.SAVE_FAILED
# What Preview / Generate answer instead of a revision.
BUSY = "busy"


class RenderDataMissing(Exception):
    """There is nothing to draw a final image of. Its text is for a person,
    its `code` (one of the service's MISSING_CODES) for the published error."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ExecutorStopping():
    executor = TaskExecutor.instance
    return executor is not None and executor.stopping.is_set()


def _Ms(seconds):
    """A duration in whole milliseconds, for the logs."""
    return round(seconds * 1000)


class RenderLane:
    """See the module docstring."""

    # What the log calls this kind of image.
    LOG_NAME = ""
    # What the shell is asked to draw.
    RENDER_KIND = ""
    # Where the state has the run's progress and result.
    RENDER_PATH = ""
    # The saved images, and how many of them are kept.
    OUTPUT_DIRECTORY = ""
    OUTPUT_PATTERNS = ()
    OUTPUT_KEEP = 0
    # A saved image is a PNG, and with this a JPG beside it.
    ALSO_JPG = False
    # Where the newest picture is, for the preview's route.
    PREVIEW_PATH = ""
    # What a crash may have left of a temporary file (ImageImport.WriteFile),
    # besides PREVIEW_PATH's and the saved images'.
    LEFTOVERS = ()
    # RenderDataMissing's codes: a generate that stood in for a preview and
    # found one of these lets the preview be drawn after all.
    MISSING_CODES = ()

    def __init__(self, later, render, prune=None, shuttingDown=None):
        """`later(fn)` runs `fn` on the main thread once its event loop is
        back (never right away). `render(kind, payload, done)` starts drawing
        `payload` and returns; `done(encode, error)` comes later, once, on the
        main thread, with `encode(format, fast=False)` -> the image's bytes,
        "PNG" or "JPG", callable from any thread (None when it failed:
        `error`); `fast` asks for a PNG that is quicker to make (still
        lossless, compressed less), the preview's. `prune(directory, patterns,
        keep, protect=)` drops the oldest images (None: none are dropped).
        `shuttingDown()` says whether a render that could not start was
        stopped by the shutdown (None: whether the task executor is
        stopping)."""
        self._later = later
        self._render = render
        self._prune = prune
        self._shuttingDown = shuttingDown or _ExecutorStopping

        # The lane: what runs (PREVIEW / GENERATE / None), the revision of the
        # run that has not ended yet (None once its end is on its way), what
        # was kept for after it and the last revision handed out. And whether
        # the kept generate / the running one stands in for a preview asked
        # for (answered with its revision), which is drawn after all when it
        # finds nothing to save (_Ended).
        self._laneLock = threading.Lock()
        self._running = None
        self._runRevision = None
        self._next = None
        self._revision = 0
        self._nextHasPreview = False
        self._runHasPreview = False
        # What the generate kept or running draws (Generate's `target`, the
        # service's own: ThumbnailService's board, ADR-005 S4z). One field
        # is enough: a second generate is BUSY until the first has ended,
        # which is after its payload and its file name were made.
        self._generateTarget = None
        # A preview to draw after the kept generate, which does not draw the
        # preview's picture (_GenerateDrawsPreview). Set only while a
        # generate is kept: when that one starts, this is what is kept next.
        self._previewAfter = False

        # What a crash left of the temporary files (ImageImport.WriteFile).
        # Their names vary, hence the wildcards: only ours are in these
        # directories under those names.
        ImageImport.RemoveLeftovers(
            self.PREVIEW_PATH,
            f"{self.OUTPUT_DIRECTORY}/*.png",
            *self.LEFTOVERS)
        # And the last session's preview (at PREVIEW_PATH).
        try:
            Path(self.PREVIEW_PATH).unlink(missing_ok=True)
        except OSError:
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: the last preview could not be removed")

    # ── what a service says ──────────────────────────────────────────────

    def _ReadRender(self) -> dict:
        """The published result: {status, path, error, revision}."""
        raise NotImplementedError

    def _RunPayload(self, kind):
        """What the shell is given to draw; its first item is the data
        (_OutputStem). May raise RenderDataMissing."""
        raise NotImplementedError

    def _OutputStem(self, data) -> str:
        """The name of a saved image, without its extension."""
        raise NotImplementedError

    def _GenerateDrawsPreview(self, target) -> bool:
        """Whether a generate of `target` draws the preview's picture: if so
        it stands in for a kept preview and writes the preview's file too.
        Called under the lane's lock (it must not take it)."""
        return True

    # ── the requests ─────────────────────────────────────────────────────

    def Preview(self):
        """Draw the preview image (PREVIEW_PATH) in the background: the
        revision of the run that will. The newest settings are drawn, however
        many of these came in during a run."""
        return self._Request(PREVIEW)

    def Generate(self, target=None):
        """Draw and save an image in the background, which also updates the
        preview image (unless _GenerateDrawsPreview says otherwise): the
        revision of the run that will. BUSY (nothing changes) while one is on
        its way. `target` is the service's to read when the run is made
        (_GenerateTarget): what this generate draws."""
        return self._Request(GENERATE, target)

    def _GenerateTarget(self):
        """The `target` of the generate kept or running (None for none)."""
        with self._laneLock:
            return self._generateTarget

    def PreviewImage(self):
        """The preview image's bytes (a PNG); None while there is none. Read
        at once rather than streamed from the file: a slow client must not
        keep a render from replacing it (Windows)."""
        try:
            with open(self.PREVIEW_PATH, "rb") as file:
                return file.read()
        except OSError:
            return None

    # ── the lane ─────────────────────────────────────────────────────────

    def _Request(self, kind, target=None):
        last = self._ReadRender()["revision"]
        with self._laneLock:
            self._revision = max(self._revision, last)
            if kind == GENERATE and GENERATE in (self._running, self._next):
                return BUSY
            if kind == GENERATE:
                self._generateTarget = target
            if self._running is not None:
                if PREVIEW in (kind, self._next) \
                        and GENERATE in (kind, self._next):
                    if self._GenerateDrawsPreview(self._generateTarget):
                        # A preview and a generate kept together are the
                        # generate.
                        self._nextHasPreview = True
                    else:
                        # Two runs: the generate, then the preview.
                        self._previewAfter = True
                        self._next = GENERATE
                        if kind == PREVIEW:
                            return self._revision + 2
                        return self._revision + 1
                if self._next != GENERATE:
                    self._next = kind
                return self._revision + 1
            self._running = kind
            self._revision += 1
            revision = self._revision
            self._runRevision = revision
            self._runHasPreview = False
        self._Start(kind, revision)
        return revision

    def _Start(self, kind, revision):
        """Begin a run the lane has given `revision`. What only the shell's
        `render` raises goes on to the caller, after the run was ended: the
        request's answer (the app is shutting down, 503)."""
        with StateManager.lock:
            self._Publish(RUNNING, self._ReadRender()["path"], "", revision)
        try:
            payload = self._RunPayload(kind)
            data = payload[0]
        except RenderDataMissing as missing:
            self._Ended(kind, revision, (ERROR, missing.code))
            return
        except Exception:
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: the payload could not be built")
            self._Ended(kind, revision, (ERROR, PAYLOAD_FAILED))
            return

        # Taken once, by whichever comes first of the renderer's answer and
        # its raising: the other one is not the run's end too. (`render` and
        # `done` may be on different threads.)
        answer = threading.Lock()

        def done(encode, error):
            if answer.acquire(blocking=False):
                self._Rendered(kind, revision, data, encode, error)
            else:
                logger.warning(f"{self.LOG_NAME}: a render answered twice")

        try:
            self._render(self.RENDER_KIND, payload, done)
        except BaseException:
            if answer.acquire(blocking=False):
                self._Ended(kind, revision, (ERROR, self._NotStarted()))
            raise

    def _NotStarted(self):
        """Why a run that could not start ended."""
        return CANCELLED if self._shuttingDown() else RENDER_FAILED

    def _Rendered(self, kind, revision, data, encode, error):
        """The renderer is done (on the main thread): the encoding and the
        files are a job's."""
        if encode is None:
            logger.error(f"{self.LOG_NAME}: the render failed: {error}")
            self._Ended(kind, revision, (ERROR, RENDER_FAILED))
            return
        try:
            executor = TaskExecutor.instance
            future = executor.submit(
                self._Save, kind, data, encode, executor.stopping,
                task_name=f"{self.LOG_NAME.lower()} save",
                then=lambda result: self._Ended(kind, revision, result),
                on_error=lambda failure: self._SaveFailed(
                    kind, revision, failure))
        except Exception as failure:
            self._SaveFailed(kind, revision, failure)
            return
        # A job the shutdown cancels (queued then, or submitted after it) runs
        # neither `then` nor `on_error`, only its done callbacks -- right away
        # when it is cancelled already.
        future.add_done_callback(
            lambda finished: finished.cancelled() and self._Ended(
                kind, revision, (ERROR, CANCELLED)))

    def _SaveFailed(self, kind, revision, failure):
        logger.opt(exception=failure).error(
            f"{self.LOG_NAME}: the render could not be saved")
        self._Ended(kind, revision, (ERROR, SAVE_FAILED))

    def _Save(self, kind, data, encode, stopping):
        """The job: (DONE, the saved image's path, None for a preview) or
        (ERROR, why). Gives up (CANCELLED) once `stopping` (the executor's) is
        set, before it writes anything."""
        if stopping.is_set():
            return ERROR, CANCELLED
        # A preview asks for the fast PNG: a busy picture's PNG at the default
        # compression took most of a preview's time (ADR-005 S4o judgment
        # 23). A generated image -- the saved one and the preview file it
        # also writes -- keeps the default encodings.
        fast = kind == PREVIEW
        alsoJpg = kind == GENERATE and self.ALSO_JPG
        began = time.monotonic()
        try:
            png = encode("PNG", fast=fast)
            pngDone = time.monotonic()
            jpg = encode("JPG") if alsoJpg else None
        except Exception:
            logger.opt(exception=True).error(
                f"{self.LOG_NAME}: the image could not be encoded")
            return ERROR, RENDER_FAILED
        encoded = time.monotonic()
        if not png or (alsoJpg and not jpg):
            return ERROR, RENDER_FAILED
        if stopping.is_set():
            return ERROR, CANCELLED
        saved = None
        writing = 0.0
        if kind == GENERATE:
            stem = self._OutputStem(data)
            target = Path(self.OUTPUT_DIRECTORY) / f"{stem}.png"
            writeBegan = time.monotonic()
            try:
                ImageImport.WriteFile(target, png)
                if alsoJpg:
                    ImageImport.WriteFile(
                        Path(self.OUTPUT_DIRECTORY) / f"{stem}.jpg", jpg)
            except OSError:
                logger.opt(exception=True).error(
                    f"{self.LOG_NAME}: the image could not be saved")
                return ERROR, SAVE_FAILED
            writing += time.monotonic() - writeBegan
            logger.info(f"{self.LOG_NAME} saved as {target}"
                        f"{' and its .jpg' if alsoJpg else ''}")
            if self._prune is not None:
                self._prune(self.OUTPUT_DIRECTORY, self.OUTPUT_PATTERNS,
                            self.OUTPUT_KEEP, protect=target)
            # Relative to the app directory, "/" separated.
            saved = target.as_posix()
        # The generate running is the one whose target this is: another is
        # BUSY until this one has ended.
        if kind == PREVIEW \
                or self._GenerateDrawsPreview(self._GenerateTarget()):
            writeBegan = time.monotonic()
            try:
                ImageImport.WriteFile(self.PREVIEW_PATH, png)
            except OSError:
                if kind == PREVIEW:
                    logger.opt(exception=True).error(
                        f"{self.LOG_NAME}: the image could not be saved")
                    return ERROR, SAVE_FAILED
                # The image is saved, which is what was asked for.
                logger.opt(exception=True).warning(
                    f"{self.LOG_NAME}: the preview image could not be "
                    "updated")
            writing += time.monotonic() - writeBegan
        jpgPart = (f", JPG {len(jpg)} bytes in {_Ms(encoded - pngDone)} ms"
                   if jpg is not None else "")
        logger.debug(
            f"{self.LOG_NAME} {kind}: encoded in {_Ms(encoded - began)} ms "
            f"(PNG{' fast' if fast else ''} {len(png)} bytes in "
            f"{_Ms(pngDone - began)} ms{jpgPart}), written in "
            f"{_Ms(writing)} ms")
        return DONE, saved

    def _Publish(self, status, path, error, revision):
        # One Set of the whole dict: a reader never sees half a result.
        StateManager.Set(self.RENDER_PATH, {
            "status": status, "path": path, "error": error,
            "revision": revision})

    def _Ended(self, kind, revision, result):
        """Publish a run's `result` -- (DONE, saved path or None) or (ERROR,
        error) -- then let what was kept run. From whichever thread ended it;
        only the first end of a run counts, as a second one would publish over
        the next run and let two run at once."""
        with self._laneLock:
            if revision != self._runRevision:
                logger.warning(
                    f"{self.LOG_NAME}: render {revision} ended again")
                return
            self._runRevision = None
            hadPreview = self._runHasPreview
        status, value = result
        # A generate that stood in for a preview and found nothing to save:
        # the preview is drawn after all (a placeholder), or it would be lost
        # with the generate's payload (S4p review).
        previewOwed = hadPreview and status == ERROR \
            and value in self.MISSING_CODES
        try:
            with StateManager.lock:
                last = self._ReadRender()
                if status == DONE:
                    # A preview leaves the path: the last image saved.
                    self._Publish(DONE, last["path"] if value is None
                                  else value, "", revision)
                else:
                    self._Publish(ERROR, last["path"], value, revision)
        finally:
            # After the publish: a run started in between would otherwise see
            # its "running" overwritten by this result.
            with self._laneLock:
                following, self._next = self._next, None
                if self._previewAfter:
                    # The kept generate starts: the preview after it is
                    # what is kept now.
                    self._next = PREVIEW
                    self._previewAfter = False
                self._runHasPreview, self._nextHasPreview = \
                    self._nextHasPreview, False
                previewOwed = following is None and previewOwed
                if previewOwed:
                    following = PREVIEW
                self._running = following
                if following is not None:
                    self._revision += 1
                    followingRevision = self._revision
                    self._runRevision = followingRevision
            if previewOwed:
                logger.info(f"{self.LOG_NAME}: nothing to save; drawing the "
                            "preview the generate stood in for")
            if following is not None:
                self._QueueRun(following, followingRevision)

    def _QueueRun(self, kind, revision):
        try:
            self._later(lambda: self._RunKept(kind, revision))
        except Exception:
            # No deferral to be had (no event loop): the run is over.
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: the next render could not be queued")
            self._Ended(kind, revision, (ERROR, self._NotStarted()))

    def _RunKept(self, kind, revision):
        try:
            self._Start(kind, revision)
        except Exception:
            # Nobody to answer to: _Start has ended the run.
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: the next render could not be started")
