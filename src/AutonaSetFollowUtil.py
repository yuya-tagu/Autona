"""How core's set loading and following reaches Start.gg and the clock: the
Qt side of SetFollowService (ADR-005 S3e-7).

Everything a set load decides and writes is `src/core/SetFollowService.py`.
What is left here is the transport, registered once by bootstrap_core() as
`SetFollowService.fetcher`:

- a fetch runs as a job of the core task executor (ADR-005 S3f-2)
  (`AutonaTournamentDataProvider.RunWithProvider`, the provider read once
  when it starts; nothing is started when no tournament is loaded), and its
  answer comes back on the main thread through `answered`, a queued signal
  to a QObject that lives as long as the app. The answers used to cross
  through each fetch's own Worker signals, which the garbage collector
  could take before the main thread delivered them -- and the station's
  queue was fetched from a worker that had started it on another pool
  thread, whose answer was posted to a thread with no event loop and never
  arrived. A fetch that raises hands its error the same way, to the
  fetch's `onError` (a set's fetch: the load failed);
- the stream queue is relayed as it always was: emitted on the provider's
  `stream_queue_loaded` (a `dict` signal, so its consumers -- the stream
  queue writer, StatsService.UpdateStreamQueue -- get the payload in the
  same QVariantMap order), from the main thread;
- the 5-second clock: one QTimer per board, started, restarted and stopped
  to follow `SetFollowService.PollGeneration(board)` (`Poll`), on the main
  thread whichever thread asked; its tick is `SetFollowService(n).Tick()`.
  The app's `aboutToQuit` stops them all, for good: a tick or a fetch
  answering while the app winds down starts nothing more;
- `followChanged(board)`: what the board follows changed, for the board's
  timer row (connected with a bound method, so it dies with the board).

QtCore only: the headless shell has it too, so headless set loads fetch and
follow under its QCoreApplication.
"""
import threading

from qtpy.QtCore import QCoreApplication, QObject, QTimer, Qt, Signal, Slot
from loguru import logger

from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers.AutonaQtHelper import in_gui_thread
from .core.SetFollowService import SetFollowService
from .core.TaskExecutor import TaskExecutor


def _noProgress(*args):
    pass


class _FollowSignals(QObject):
    # (onResult, data): a fetch answered. `object`, so the payload is the
    # provider's own (a dict signal would hand a sorted copy).
    answered = Signal(object, object)
    # fn: run on the main thread.
    onMain = Signal(object)
    # board: what the board follows changed.
    followChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.answered.connect(self._OnAnswered,
                              Qt.ConnectionType.QueuedConnection)
        self.onMain.connect(self._OnMain, Qt.ConnectionType.QueuedConnection)

    @Slot(object, object)
    def _OnAnswered(self, onResult, data):
        try:
            onResult(data)
        except Exception:
            logger.exception("Set follow: applying a fetched set failed")

    @Slot(object)
    def _OnMain(self, fn):
        try:
            fn()
        except Exception:
            logger.exception("Set follow: following the clock failed")


class AutonaSetFollowUtil:
    """SetFollowService's fetcher (see the module docstring). Built on the
    main thread by bootstrap_core(), after the tournament provider."""

    def __init__(self):
        self.signals = _FollowSignals()
        # {board: QTimer} and {board: the poll generation it was started
        # for}. Main thread only.
        self._timers = {}
        self._started = {}
        self._quitting = False
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._StopClocks)

    @staticmethod
    def _Provider():
        return AutonaTournamentDataProvider.instance

    def _Fetch(self, call, onResult, onError=None):
        """Run `call(provider)` as a provider job and hand its result to
        `onResult` on the main thread -- or, when it raises, the error to
        `onError` (logged either way; no answer without one). No answer
        either once the task executor is stopping. False when no tournament
        is loaded or the app is quitting (nothing is called)."""
        signals = self.signals

        executor = TaskExecutor.instance

        def job(provider):
            try:
                result = call(provider)
            except Exception as error:
                logger.exception("Set follow: a fetch failed")
                if onError is not None:
                    signals.answered.emit(onError, error)
                return
            if executor.stopping.is_set():
                # The app is quitting: a request the shutdown cut short
                # answered {} -- no stream queue, no set -- which is no
                # answer to write (ADR-005 S3f-2 review).
                logger.info("Set follow: a fetch answered while the app is "
                            "quitting: dropped")
                return
            if onResult is not None:
                signals.answered.emit(onResult, result)

        return self._Provider().RunWithProvider(job)

    # ── the provider ─────────────────────────────────────────────────────

    def Available(self):
        return self._Provider().GetProvider() is not None

    def Match(self, setId, onResult, onError=None):
        return self._Fetch(lambda provider: provider.GetMatch(
            setId=setId, progress_callback=_noProgress,
            cancel_event=threading.Event()), onResult, onError)

    def StreamQueue(self):
        adapter = self._Provider()
        return self._Fetch(lambda provider: provider.GetStreamQueue(
            progress_callback=_noProgress, cancel_event=threading.Event()),
            adapter.signals.stream_queue_loaded.emit)

    def StationSets(self, stationId, onResult):
        return self._Fetch(
            lambda provider: provider.GetStationMatchsId(stationId),
            onResult)

    def StreamSet(self, name, onResult):
        return self._Fetch(
            lambda provider: provider.GetStreamMatchId(name), onResult)

    def FutureSets(self, setIds, onResult):
        return self._Fetch(lambda provider: provider.GetFutureMatchesList(
            setsId=setIds, progress_callback=_noProgress,
            cancel_event=threading.Event()), onResult)

    # ── the clock ────────────────────────────────────────────────────────

    def Poll(self, board):
        """Make board `board`'s timer follow its poll: (re)started for a new
        generation, stopped for none."""
        if in_gui_thread():
            self._FollowPoll(board)
        else:
            self.signals.onMain.emit(lambda: self._FollowPoll(board))

    def _FollowPoll(self, board):
        generation = SetFollowService.PollGeneration(board)
        timer = self._timers.get(board)
        if generation is None or self._quitting:
            if timer is not None:
                timer.stop()
            self._started.pop(board, None)
            return
        if timer is None:
            timer = QTimer()
            timer.timeout.connect(lambda: self._OnTick(board))
            self._timers[board] = timer
        if self._started.get(board) == generation and timer.isActive():
            return
        timer.start(SetFollowService.POLL_MS)
        self._started[board] = generation

    def _StopClocks(self):
        # The app is quitting: no tick after this one.
        self._quitting = True
        for timer in self._timers.values():
            timer.stop()
        self._started.clear()

    @staticmethod
    def _OnTick(board):
        try:
            SetFollowService(board).Tick()
        except Exception:
            logger.exception(f"score.{board}: polling the set failed")

    def RemainingMs(self, board):
        timer = self._timers.get(board)
        if timer is None or not timer.isActive():
            return None
        return timer.remainingTime()

    def FollowChanged(self, board):
        self.signals.followChanged.emit(int(board))
