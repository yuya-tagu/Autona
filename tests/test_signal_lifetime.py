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

Tests below: the structural ones that keep new lambdas and new direct
subscriptions out, and the score hotkeys wired once in core. (The
behavioural ones that dropped a Qt board and showed it went quiet on its own
went with the Qt boards in ADR-005 S4ac-3: no widget a page shows is torn
down while the app runs any more, but a window or a dialog still can be,
and the structural checks keep covering every Qt-side module.)
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
#
# (The Qt thumbnail panel was the other one; it went in ADR-005 S4p.)
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
    # The window's lambdas on tournament_changed (the mods checkbox
    # unchecked) and onLoad (the checkbox drawn) went in ADR-005 S-final
    # SF-5b-1: core turns the mods off, and the drawing is a bound method.
}

# The one Qt-side module allowed to subscribe to a core channel: the adapter
# whose entire job is to turn those channels into Qt signals. It is built
# once by bootstrap_core(), lives for the whole process, and refuses a second
# construction, so its four subscriptions can never be stale or duplicated.
#
#
# The player DB's Qt adapter is the same arrangement for the core PlayerDB
# (ADR-005 S3e-1): a static class, alive for the whole process, whose
# Attach() -- called once by bootstrap_core(), idempotent -- subscribes its
# SetupModel to PlayerDB.changed so the core's announcements become
# db_updated on the GUI thread.
#
# Do not add a widget here. A widget cannot hold this kind of subscription
# safely -- see the module docstring -- and the fix is always the same:
# connect to StateManagerBridge.instance (or AutonaPlayerDB.signals)
# instead.
ALLOWED_OBSERVER_SUBSCRIBERS = {"StateManagerBridge.py", "AutonaPlayerDB.py"}


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
    signals themselves and checks the state follows. (The board's score
    display followed it too until ADR-005 S4ac-3.)
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
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
