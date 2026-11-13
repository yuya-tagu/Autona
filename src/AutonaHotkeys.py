import ctypes
import ctypes.wintypes
import platform
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from loguru import logger

from .core.SettingsManager import SettingsManager
from .core.Actions import AutonaActions
from .core import HotkeyRules
from .core import Top8Service

# Win32 constant of the message the native filter answers. The modifiers,
# the key codes, the defaults and the parser are core's (HotkeyRules,
# ADR-005 S4ae): the settings page validates against what this registers.
WM_HOTKEY = 0x0312

user32 = ctypes.windll.user32


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Receives WM_HOTKEY from the Windows message loop."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._callback(msg.wParam)
                return True, 0
        return False, 0


class AutonaHotkeysSignals(QObject):
    team1_score_up = Signal()
    team1_score_down = Signal()
    team2_score_up = Signal()
    team2_score_down = Signal()
    reset_scores = Signal()
    load_set = Signal()
    swap_teams = Signal()
    refresh_phase_group = Signal()
    apply_changes = Signal()


class AutonaHotkeys(QObject):
    instance: "AutonaHotkeys" = None
    signals = AutonaHotkeysSignals()
    parent: QWidget = None

    # The defaults (core's HotkeyRules.DEFAULT_KEYS, where the reason for
    # each is). A copy: LoadUserHotkeys clones it, and nothing may change
    # core's table through it.
    keys = dict(HotkeyRules.DEFAULT_KEYS)

    loaded_keys = {}

    # True once ConnectActions() has wired the signals below to the core
    # operations. Class-level, because `signals` is class-level too: the
    # connections belong to the process, not to an instance (the hotkey unit
    # tests build extra AutonaHotkeys objects, and a per-instance connection
    # would have every one of them answering the same key press).
    _actions_connected = False

    # True once ConnectReload() has connected the settings page's saves to
    # ReloadHotkeys. Class-level like _actions_connected, for the same
    # reason: bootstrap_gui() can run more than once in a process, and a
    # second connection would re-register every hotkey twice per save.
    _reload_connected = False

    @classmethod
    def ConnectReload(cls, signal):
        """Re-register the hotkeys on `signal`: the bridge's
        hotkeys_changed, i.e. a save through the settings page (core's
        HotkeyRules.Update, on a request thread). Connected to the
        instance's bound method: the object lives on the GUI thread, so Qt
        queues the call there -- RegisterHotKey binds a hotkey to the
        calling thread's message queue (ADR-005 S4ae). Idempotent; needs
        the instance."""
        if cls._reload_connected:
            return
        cls._reload_connected = True
        signal.connect(cls.instance.ReloadHotkeys)

    @classmethod
    def ConnectActions(cls):
        """Route the board-independent hotkeys to core (AutonaActions, and
        the Top 8 import to Top8Service).

        The score ones used to be connected by AutonaScoreboardWidget to
        its own bound methods, which made the answer to "what does
        Ctrl+Shift+1 do" depend on which boards were built: nothing at all
        when the saved battle format was crew, and *both* boards after a
        singles/team switch -- scoring a hidden one along with the visible
        one. AutonaActions resolves the active board from the persisted
        format instead, so the hotkey works with no panel built and reaches
        exactly one board (ADR-004 D2, headless core Step 3). `swap_teams`
        joined them in ADR-005 S2c, once the swap became a state operation
        (AutonaActions.SwapTeams) instead of a copy between the player
        widgets.

        `refresh_phase_group` joined them in ADR-005 S3e-8: the Top 8 import
        is core's (Top8Service.RefreshFromProvider), so the hotkey imports
        with no panel at all (it used to be the window's, which built the Qt
        Top 8 panel to ask it; a React page since ADR-005 S4i).

        `load_set` joined them in ADR-005 S4y: it opens the active board's
        set selection through the shell (AutonaShellOperations) instead of
        the board's window -- the SCORE page's dialog since ADR-005 S4ab-2,
        and only that since S4ac-3. Each built 1v1 / team board connected it to
        itself until then, so it opened every built board's window -- a
        hidden one's too, stacked -- and none in crew format if neither had
        been built.

        Not applicable to board 3: a crew battle's team score is the sum of
        its members' remaining stocks, so there is no team-level score to
        nudge or zero. AutonaActions logs and ignores a score adjustment or
        reset aimed at it, which is what these hotkeys do while the operator
        is in crew format. The swap hotkey is ignored there too: the crew
        board never listened to it (its SWAP button and the Web UI swap it).
        So is `load_set`: the crew board has no set window.

        Idempotent -- the guard matters because bootstrap_gui() can run more
        than once in a process (the test harness bootstraps its own runtime,
        and any re-bootstrap re-checks this), and a second connect would
        apply every score hotkey twice per key press.
        """
        if cls._actions_connected:
            return
        cls._actions_connected = True
        cls.signals.team1_score_up.connect(cls._onTeam1ScoreUp)
        cls.signals.team1_score_down.connect(cls._onTeam1ScoreDown)
        cls.signals.team2_score_up.connect(cls._onTeam2ScoreUp)
        cls.signals.team2_score_down.connect(cls._onTeam2ScoreDown)
        cls.signals.reset_scores.connect(cls._onResetScores)
        cls.signals.apply_changes.connect(cls._onApplyChanges)
        cls.signals.swap_teams.connect(cls._onSwapTeams)
        cls.signals.refresh_phase_group.connect(cls._onRefreshTop8)
        cls.signals.load_set.connect(cls._onLoadSet)
        logger.debug("Hotkeys routed to AutonaActions")

    @classmethod
    def DisconnectActions(cls):
        """Undo ConnectActions(). For tests only.

        `signals` and the connected slots are class-level, so once
        ConnectActions() has run there is no object whose destruction would
        release them -- a test that wants to observe the un-routed state, or
        to count how many times a routing happens, has no other way back.
        Nothing in the app calls this: the routing is meant to live as long
        as the process.
        """
        if not cls._actions_connected:
            return
        cls._actions_connected = False
        cls.signals.team1_score_up.disconnect(cls._onTeam1ScoreUp)
        cls.signals.team1_score_down.disconnect(cls._onTeam1ScoreDown)
        cls.signals.team2_score_up.disconnect(cls._onTeam2ScoreUp)
        cls.signals.team2_score_down.disconnect(cls._onTeam2ScoreDown)
        cls.signals.reset_scores.disconnect(cls._onResetScores)
        cls.signals.apply_changes.disconnect(cls._onApplyChanges)
        cls.signals.swap_teams.disconnect(cls._onSwapTeams)
        cls.signals.refresh_phase_group.disconnect(cls._onRefreshTop8)
        cls.signals.load_set.disconnect(cls._onLoadSet)

    # The slots are plain functions rather than bound methods on purpose:
    # `signals` outlives any instance, and these targets are meant to live as
    # long as the process. Nothing here holds a widget, so there is no
    # receiver whose death Qt would need to notice.

    @staticmethod
    def _runOnActiveBoard(operation):
        """`operation(actions, board)` on the active board, as one board
        operation: run by the board host, under its lock, with the active
        board read there too (ADR-005 S4ad review). The board operations run
        on their callers' threads since S4ad, and a hotkey landing between
        the calls of a web one (clear_all's cycles, a PATCH set's) would
        split it; until then they ran on this (the GUI) thread, which kept
        them apart. Read under the lock, the board is the one the operation
        runs on, not one a format switch (also a board operation) replaced
        in between. Imported here, as the web layer is not this module's to
        pull in when it is imported."""
        actions = AutonaActions.instance
        if actions is None:
            logger.warning("Hotkey ignored: AutonaActions is not built yet")
            return
        from . import AutonaWebOperations
        AutonaWebOperations.boardHost.Run(
            lambda: operation(actions, actions.ActiveBoard()))

    @staticmethod
    def _adjustActiveScore(team, delta):
        AutonaHotkeys._runOnActiveBoard(
            lambda actions, board: actions.AdjustScore(board, team, delta))

    @staticmethod
    def _onTeam1ScoreUp():
        AutonaHotkeys._adjustActiveScore(1, 1)

    @staticmethod
    def _onTeam1ScoreDown():
        AutonaHotkeys._adjustActiveScore(1, -1)

    @staticmethod
    def _onTeam2ScoreUp():
        AutonaHotkeys._adjustActiveScore(2, 1)

    @staticmethod
    def _onTeam2ScoreDown():
        AutonaHotkeys._adjustActiveScore(2, -1)

    @staticmethod
    def _onResetScores():
        AutonaHotkeys._runOnActiveBoard(
            lambda actions, board: actions.ResetScore(board))

    @staticmethod
    def _onApplyChanges():
        # 反映 under the board lock too, so it does not publish half of a
        # web operation (v1's apply is not a board operation and runs
        # outside it, as since ADR-005 S4u).
        AutonaHotkeys._runOnActiveBoard(
            lambda actions, board: actions.ApplyChanges(board))

    @staticmethod
    def _onSwapTeams():
        # The crew board's swap is not core's AutonaActions.SwapTeams (it
        # answers False, as the hotkey always left that board alone).
        AutonaHotkeys._runOnActiveBoard(
            lambda actions, board: actions.SwapTeams(board))

    @staticmethod
    def _onLoadSet():
        """Open the active board's set selection: the SCORE page's dialog,
        through the shell (AutonaShellOperations.ShowSetSelection; ADR-005
        S4y / S4ab-2 -- the Qt window on the way back to the Qt panel until
        S4ac-3). Ignored in crew format (no set selection). Every board is
        made at launch since ADR-005 S4ac-1 (until then a set loaded onto an
        unbuilt board was wiped by the seed that built it, and this asked
        the board host whether it was built, until S4ad)."""
        actions = AutonaActions.instance
        if actions is None:
            logger.warning("Hotkey ignored: AutonaActions is not built yet")
            return
        number = actions.ActiveBoard()
        if number not in AutonaActions.SCORE_BOARDS:
            logger.debug(f"Load set hotkey ignored: board {number} has no "
                         "set window")
            return
        # Imported here: the shell operations are not this module's to pull
        # in when it is imported.
        from .AutonaShellOperations import AutonaShellOperations
        shell = AutonaShellOperations.instance
        if shell is None:
            logger.warning("Hotkey ignored: the shell operations are not "
                           "built yet")
            return
        shell.ShowSetSelection(number)

    @staticmethod
    def _onRefreshTop8():
        # The import runs on the provider's worker, not on the GUI thread
        # (a second press while one runs is dropped).
        Top8Service.RefreshFromProvider()

    def __init__(self) -> None:
        super().__init__()
        self._registered_ids = {}  # hotkey_id -> action_key
        self._next_id = 1
        self._filter = None
        # True between SuspendCapture() and ResumeCapture().
        self._capture_suspended = False
        self.LoadUserHotkeys()

    def UiMounted(self, parent):
        self.parent = parent
        # Read again, not the keys of the construction: when the launch
        # could not read settings.json, a write since (the desktop's
        # TurnOffPolling, in bootstrap_gui()) may have read it.
        self.LoadUserHotkeys()
        self.SetupHotkeys()

    def ReloadHotkeys(self):
        """Read the saved shortcuts again and register them -- or none,
        with hotkeys turned off (SetupHotkeys). A save through the settings
        page (core's HotkeyRules.Update, on a request thread) reaches it
        through StateManagerBridge.hotkeys_changed, connected by
        bootstrap_gui(): this object lives on the GUI thread, so Qt queues
        the call there -- RegisterHotKey binds a hotkey to the calling
        thread's message queue, the one the native filter reads (ADR-005
        S4ae). It is the one way a save takes effect since the Qt settings
        window went (S4af; it called this and ApplyEnabledSetting itself)."""
        self.LoadUserHotkeys()
        self.SetupHotkeys()

    def SuspendCapture(self):
        """Release the OS hotkey registrations until ResumeCapture().

        RegisterHotKey grabs the combination system-wide. The Qt settings
        window's key recorder (a QKeySequenceEdit) held them off while it
        had focus, so an assigned shortcut reached the field instead of
        firing; the settings page composes a shortcut from lists and records
        no key (ADR-005 S4af), so no window calls this any more. The test
        session does (tests/conftest.py): its hotkeys stay off the OS while
        saves still reload them. Idempotent, so a duplicate call is harmless.
        """
        if self._capture_suspended:
            return
        self._capture_suspended = True
        self._unregisterAll()
        logger.debug("Global hotkeys suspended for capture")

    def ResumeCapture(self):
        """Re-register the hotkeys after a capture field loses focus.

        Registration always uses the current loaded_keys, so a shortcut that
        was edited (and reloaded) while suspended takes effect here.
        Idempotent: resuming when not suspended does nothing.
        """
        if not self._capture_suspended:
            return
        self._capture_suspended = False
        self.SetupHotkeys()

    def _hotkeysEnabled(self):
        return SettingsManager.Get("hotkeys.hotkeys_enabled", True) != False

    def SetupHotkeys(self):
        self._unregisterAll()

        # ReloadHotkeys() runs on every successful save, suspended or not.
        # Keep the freshly loaded keys but do not hand them back to the OS
        # yet -- ResumeCapture() registers them.
        if self._capture_suspended:
            logger.debug("Hotkey capture suspended; skipping registration")
            return

        # Disabling hotkeys must release the OS-level grab, not just ignore
        # the event: RegisterHotKey swallows the combination system-wide, so
        # keeping it registered would deny Ctrl+Shift+1 etc. to every other
        # app. Every registration path (ReloadHotkeys / ResumeCapture /
        # UiMounted) funnels through here, and _unregisterAll() already ran
        # above, so returning now leaves nothing registered.
        if not self._hotkeysEnabled():
            logger.info("Hotkeys disabled in settings; skipping registration")
            return

        if platform.system() != "Windows":
            logger.warning("Global hotkeys only supported on Windows")
            return

        # Install native event filter once
        if self._filter is None:
            self._filter = _HotkeyFilter(self._onHotkey)
            QApplication.instance().installNativeEventFilter(self._filter)

        for action_key, shortcut_str in self.loaded_keys.items():
            parsed = HotkeyRules.ParseShortcut(shortcut_str)
            if parsed is None:
                logger.warning(
                    f"Could not parse hotkey: {shortcut_str} for {action_key}")
                continue

            modifiers, vk = parsed
            hotkey_id = self._next_id
            self._next_id += 1

            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                self._registered_ids[hotkey_id] = action_key
                logger.debug(
                    f"Registered hotkey {shortcut_str} -> {action_key} (id={hotkey_id})")
            else:
                logger.warning(
                    f"Failed to register hotkey {shortcut_str} for {action_key}")

        logger.info(
            f"Global hotkeys registered: {len(self._registered_ids)} keys")

    def _unregisterAll(self):
        for hotkey_id in list(self._registered_ids.keys()):
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered_ids.clear()

    def _onHotkey(self, hotkey_id):
        action_key = self._registered_ids.get(hotkey_id)
        if action_key:
            self.HotkeyTriggered(
                action_key, self.loaded_keys.get(action_key, ""))

    def HotkeyTriggered(self, k, v):
        if not SettingsManager.Get("hotkeys.hotkeys_enabled", True) == False:
            logger.info(f"Activated {k} by pressing {v}")
            getattr(self.signals, k).emit()

    def LoadUserHotkeys(self):
        # Core's merge of the saved shortcuts over the defaults, the one the
        # settings page checks against. A saved value
        # that is not a string, or a subtree that is not an object (a
        # hand-edited settings.json), no longer raises here, inside
        # UiMounted: SetupHotkeys skips the value as unreadable and
        # registers the others (ADR-005 S4ae).
        self.loaded_keys = HotkeyRules.CurrentKeys()
        logger.info("User hotkeys loaded")
