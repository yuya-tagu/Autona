"""Connections from process-wide signals must not outlive their receiver.

Boards, player widgets and panels are torn down while the singletons they
listen to (AutonaGameAssetManager, AutonaTournamentDataProvider,
AutonaHotkeys, StateManager, AutonaPlayerDB) live for the whole process. A
`connect(self.Method)` is safe there: PySide6 keeps a weak reference to the
receiver and drops the connection when it dies. A `connect(lambda: ...)` is
not -- Qt holds the callable, the closure holds the widget, and nothing ever
disconnects it. The destroyed widget stays reachable and keeps answering,
rewriting the state subtree it used to own (the "ghost board").

Since headless core Step 1 there is a second, blunter way into the same bug.
The state layer publishes through `ObserverChannel` (src/core/Observable.py),
whose `subscribe()` holds a **strong** reference to the callback and has no
weak-receiver machinery at all. A bound method is no safer there than a
lambda: nothing is ever disconnected implicitly, so a widget that subscribes
directly is a ghost board by construction, not by accident. Widgets connect
to `StateManagerBridge.instance` instead and let Qt disconnect them; the
channels themselves are for core and other Qt-free code. That is the third
structural check below.

Tests below: two structural ones that keep new lambdas and new direct
subscriptions out, and behavioural ones that show a dropped board really
does go quiet on its own.
"""
import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
# The Qt-free half of src. Code here has no widgets and no Qt lifetime, so
# subscribing to an ObserverChannel is exactly what it is supposed to do;
# the subscription check below applies to the Qt-side files only.
CORE_DIR = SRC_ROOT / "core"

# Senders that live for the whole process. A connection made from one of
# these is only as short-lived as Qt can prove the receiver to be.
PROCESS_WIDE_SENDERS = (
    "AutonaGameAssetManager.instance.signals",
    "AutonaTournamentDataProvider.instance.signals",
    "AutonaScoreboardManager.instance.signals",
    "AutonaAlertNotification.instance.signals",
    "AutonaHotkeys.signals",
    "AutonaPlayerDB.signals",
    # The state layer's Qt signals live on the adapter now, not on
    # StateManager itself (headless core Step 1). Same four signals, same
    # process lifetime -- the bridge is built once by bootstrap_core().
    "StateManagerBridge.instance",
    # The application object itself (focusChanged, which the player cards
    # listen to since ADR-005 S2a, and the rest of its signals).
    "QApplication.instance()",
    "QGuiApplication.instance()",
    "QCoreApplication.instance()",
)

# The exceptions, listed as (module, full sender expression). Every receiver
# here is built once and lives as long as the application does, so the
# connection can never outlive it:
#
# - Autona.py            -- Window, the QMainWindow itself.
# - AutonaThumbnailSettingsWidget.py
#                        -- Window.thumbnailSetting, one instance created by
#                           Window.__init__ and never replaced.
#
# Do not extend this list for a widget that can be destroyed. Convert the
# lambda into a bound method instead; if it captured a value, put that value
# in a method of its own (AutonaHotkeys._onTeam1ScoreUp and friends are the
# worked example -- they started as lambdas capturing (team, delta), became
# bound methods on the board, and are static hotkey slots on a
# process-lifetime object now that the write itself moved to core).
ALLOWED_LAMBDA_CONNECTIONS = {
    ("Autona.py",
     "AutonaTournamentDataProvider.instance.signals.tournament_url_update"),
    ("Autona.py",
     "AutonaTournamentDataProvider.instance.signals.tournament_changed"),
    ("Autona.py", "AutonaGameAssetManager.instance.signals.onLoad"),
    ("AutonaThumbnailSettingsWidget.py",
     "AutonaGameAssetManager.instance.signals.onLoad"),
}

# The one Qt-side module allowed to subscribe to a core channel: the adapter
# whose entire job is to turn those channels into Qt signals. It is built
# once by bootstrap_core(), lives for the whole process, and refuses a second
# construction, so its four subscriptions can never be stale or duplicated.
#
# Do not add a widget here. A widget cannot hold this kind of subscription
# safely -- see the module docstring -- and the fix is always the same:
# connect to StateManagerBridge.instance instead.
ALLOWED_OBSERVER_SUBSCRIBERS = {"StateManagerBridge.py"}


def _lambda_connections():
    """Every `<process-wide signal>.connect(lambda ...)` under src/."""
    found = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "connect" or not node.args:
                continue
            if not isinstance(node.args[0], ast.Lambda):
                continue
            try:
                sender = ast.unparse(node.func.value)
            except Exception:
                continue
            if not sender.startswith(PROCESS_WIDE_SENDERS):
                continue
            found.append((
                str(path.relative_to(SRC_ROOT)).replace("\\", "/"),
                sender,
                node.lineno,
            ))
    return found


def test_no_destructible_widget_connects_a_lambda_to_a_process_wide_signal():
    offenders = [
        f"{module}:{line}: {sender}.connect(lambda ...)"
        for module, sender, line in _lambda_connections()
        if (module, sender) not in ALLOWED_LAMBDA_CONNECTIONS
    ]
    assert not offenders, (
        "a lambda connected to a process-wide signal is never disconnected; "
        "the receiver stays alive and keeps writing state after it is "
        "destroyed. Use a bound method:\n  " + "\n  ".join(offenders))


def test_the_allow_list_only_covers_connections_that_still_exist():
    # Keeps the exception list from silently outliving the code it excuses.
    present = {(module, sender) for module, sender, _ in _lambda_connections()}
    stale = sorted(ALLOWED_LAMBDA_CONNECTIONS - present)
    assert not stale, f"ALLOWED_LAMBDA_CONNECTIONS entries no longer found: {stale}"


def _observer_subscriptions():
    """Every `<something>.subscribe(...)` in the Qt-side half of src/.

    Matched on the call name rather than on a list of channel expressions:
    `subscribe` belongs to exactly one thing in this codebase
    (ObserverChannel), and the receiver is written every which way at the
    call site -- `StateManager.observers.state_updated`, a local
    `observers`, a channel passed in as an argument. Keying off the
    expression text would miss the last two, which are the ones a
    refactor produces.
    """
    found = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if CORE_DIR in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "subscribe":
                continue
            try:
                sender = ast.unparse(node.func.value)
            except Exception:
                sender = "<unparseable>"
            found.append((
                str(path.relative_to(SRC_ROOT)).replace("\\", "/"),
                sender,
                node.lineno,
            ))
    return found


def test_no_qt_side_module_subscribes_to_a_core_observer_channel():
    offenders = [
        f"{module}:{line}: {sender}.subscribe(...)"
        for module, sender, line in _observer_subscriptions()
        if module not in ALLOWED_OBSERVER_SUBSCRIBERS
    ]
    assert not offenders, (
        "an ObserverChannel subscription from the Qt side is never undone: "
        "the channel holds a strong reference to the callback, so even a "
        "bound method keeps the receiver alive and answering after the "
        "widget is destroyed (the ghost board, with none of Qt's "
        "weak-receiver protection). Connect to StateManagerBridge.instance "
        "instead:\n  " + "\n  ".join(offenders))


def test_the_observer_allow_list_only_covers_modules_that_still_subscribe():
    # Same discipline as the lambda allow list: an entry that no longer
    # matches anything is an exception nobody needs.
    present = {module for module, _, _ in _observer_subscriptions()}
    stale = sorted(ALLOWED_OBSERVER_SUBSCRIBERS - present)
    assert not stale, (
        f"ALLOWED_OBSERVER_SUBSCRIBERS entries no longer subscribe: {stale}")


def test_the_score_hotkeys_no_longer_belong_to_a_board(autonaApp):
    """The four score hotkeys are wired once, in core, not per board.

    They were lambdas capturing (team, delta) first, then bound methods on
    AutonaScoreboardWidget -- which made "what does Ctrl+Shift+1 do" depend
    on which boards happened to exist. Since headless core Step 3 they go
    AutonaHotkeys -> AutonaActions -> StateManager, so this drives the
    signals themselves and checks the board follows.
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    board = autonaApp.board(1)
    # The hotkeys act on the board the saved battle format shows; the
    # harness never writes one, so it is the default. Asserted rather than
    # assumed, so a stray SettingsManager write elsewhere in the session
    # fails here instead of two assertions further down.
    assert AutonaActions.instance.ActiveBoard() == 1

    for signal, expected in (
            (AutonaHotkeys.signals.team1_score_up, ("1", 1)),
            (AutonaHotkeys.signals.team2_score_up, ("2", 1)),
            (AutonaHotkeys.signals.team1_score_down, ("1", 0)),
            (AutonaHotkeys.signals.team2_score_down, ("2", 0)),
    ):
        team, score = expected
        signal.emit()
        autonaApp.settle()
        assert autonaApp.get(f"score.1.team.{team}.score") == score
        # The board is a display now: its spinbox follows the state.
        display = board.scoreDisplay1 if team == "1" else board.scoreDisplay2
        assert display.value() == score


def _connectionCounts():
    """How many slots the two singletons the board listens to still hold.

    QObject.receivers() wants the old SIGNAL()-encoded signature -- a "2"
    prefix in front of the normalised signal name.

    `load_set` rather than `team1_score_up`: the score hotkeys are wired
    to AutonaActions once for the process now (headless core Step 3), and
    the swap joined them in ADR-005 S2c, so building a board no longer
    changes their receiver count. `load_set` is the one hotkey a board still
    owns, and it is a board-owned connection that is what this measures.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.AutonaHotkeys import AutonaHotkeys

    return (
        AutonaGameAssetManager.instance.signals.receivers("2onLoad()"),
        AutonaHotkeys.signals.receivers("2load_set()"),
    )


def test_a_destroyed_board_disconnects_from_the_process_wide_signals(autonaApp):
    """The ghost-board regression, measured on the connections themselves.

    Note what is *not* asserted: that the board object becomes garbage. It
    does not, and cannot -- the board wires dozens of its own child widgets
    to lambdas that close over `self`, and that cycle runs through Qt's C++
    connection storage, which Python's gc cannot traverse. The wrapper stays
    reachable no matter what.

    That is fine, because it is not what causes the ghost. A board dies by
    having its C++ object destroyed (deleteLater on a battle-format switch),
    and at that moment Qt drops every connection whose receiver it can
    identify -- which is every bound method, and no lambda. So the property
    that matters is the one measured here: after the destruction the
    singletons hold exactly as many slots as before the board existed.
    """
    autonaApp.dropAllBoards()
    autonaApp.settle()
    baseline = _connectionCounts()

    board = autonaApp.board(1)
    live = _connectionCounts()
    assert live[0] > baseline[0] and live[1] > baseline[1], (
        "the board was expected to subscribe to both singletons")

    del board
    autonaApp.dropAllBoards()  # deleteLater + forced DeferredDelete
    autonaApp.settle()

    assert _connectionCounts() == baseline, (
        "a destroyed board is still subscribed to a process-wide signal: "
        "the connection was made with a lambda, which Qt cannot attribute "
        "to a receiver and therefore never disconnects")


def test_a_destroyed_board_stops_writing_its_state_subtree(autonaApp):
    """The user-visible half of the same bug: the ghost rewrites ./out."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    board = autonaApp.board(1)
    prefix = board.statePrefix
    del board

    autonaApp.dropAllBoards()
    autonaApp.settle()
    assert autonaApp.get(prefix) is None

    AutonaGameAssetManager.instance.signals.onLoad.emit()
    # No hotkey: the score, reset, 反映 and swap ones write score.{n}
    # through AutonaActions whether or not a board exists (Step 3, ADR-005
    # S2c), which would say nothing about a ghost here, and the one a board
    # still owns (load_set) opens a window rather than writing.
    autonaApp.settle()

    assert autonaApp.get(prefix) is None, (
        "a destroyed board rewrote its state subtree: its connections to the "
        "process-wide singletons were never disconnected")
