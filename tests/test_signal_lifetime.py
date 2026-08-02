"""Connections from process-wide signals must not outlive their receiver.

Boards, player widgets and panels are torn down while the singletons they
listen to (AutonaGameAssetManager, AutonaTournamentDataProvider,
AutonaHotkeys, StateManager, AutonaPlayerDB) live for the whole process. A
`connect(self.Method)` is safe there: PySide6 keeps a weak reference to the
receiver and drops the connection when it dies. A `connect(lambda: ...)` is
not -- Qt holds the callable, the closure holds the widget, and nothing ever
disconnects it. The destroyed widget stays reachable and keeps answering,
rewriting the state subtree it used to own (the "ghost board").

Two tests below: a structural one that keeps new lambdas out, and a
behavioural one that shows a dropped board really does go quiet on its own.
"""
import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# Senders that live for the whole process. A connection made from one of
# these is only as short-lived as Qt can prove the receiver to be.
PROCESS_WIDE_SENDERS = (
    "AutonaGameAssetManager.instance.signals",
    "AutonaTournamentDataProvider.instance.signals",
    "AutonaScoreboardManager.instance.signals",
    "AutonaAlertNotification.instance.signals",
    "AutonaHotkeys.signals",
    "AutonaPlayerDB.signals",
    "StateManager.signals",
)

# The exceptions, listed as (module, full sender expression). Every receiver
# here is built once and lives as long as the application does, so the
# connection can never outlive it:
#
# - Autona.py            -- Window, the QMainWindow itself.
# - AutonaApiV1.py       -- a module-level bookkeeping object, no widget.
# - AutonaThumbnailSettingsWidget.py
#                        -- Window.thumbnailSetting, one instance created by
#                           Window.__init__ and never replaced.
#
# Do not extend this list for a widget that can be destroyed. Convert the
# lambda into a bound method instead; if it captured a value, put that value
# in a method of its own (AutonaScoreboardWidget.HotkeyTeam1ScoreUp and
# friends are the worked example).
ALLOWED_LAMBDA_CONNECTIONS = {
    ("Autona.py",
     "AutonaTournamentDataProvider.instance.signals.tournament_url_update"),
    ("Autona.py",
     "AutonaTournamentDataProvider.instance.signals.tournament_changed"),
    ("Autona.py", "AutonaGameAssetManager.instance.signals.onLoad"),
    ("AutonaApiV1.py", "AutonaGameAssetManager.instance.signals.onLoad"),
    ("AutonaApiV1.py",
     "AutonaTournamentDataProvider.instance.signals.tournament_changed"),
    ("AutonaThumbnailSettingsWidget.py",
     "AutonaGameAssetManager.instance.signals.onLoad"),
}


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


def test_the_scoreboard_hotkey_slots_are_bound_methods(autonaApp):
    # The four score hotkeys used to be lambdas capturing (team, delta).
    # They are methods now, and each still maps to the pair it always did.
    board = autonaApp.board(1)

    for method, expected in (
            (board.HotkeyTeam1ScoreUp, ("1", 1)),
            (board.HotkeyTeam2ScoreUp, ("2", 1)),
            (board.HotkeyTeam1ScoreDown, ("1", 0)),
            (board.HotkeyTeam2ScoreDown, ("2", 0)),
    ):
        team, score = expected
        method()
        autonaApp.settle()
        assert autonaApp.get(f"score.1.team.{team}.score") == score


def _connectionCounts():
    """How many slots the two singletons the board listens to still hold.

    QObject.receivers() wants the old SIGNAL()-encoded signature -- a "2"
    prefix in front of the normalised signal name.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.AutonaHotkeys import AutonaHotkeys

    return (
        AutonaGameAssetManager.instance.signals.receivers("2onLoad()"),
        AutonaHotkeys.signals.receivers("2team1_score_up()"),
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
    from src.AutonaHotkeys import AutonaHotkeys

    board = autonaApp.board(1)
    prefix = board.statePrefix
    del board

    autonaApp.dropAllBoards()
    autonaApp.settle()
    assert autonaApp.get(prefix) is None

    AutonaGameAssetManager.instance.signals.onLoad.emit()
    AutonaHotkeys.signals.team1_score_up.emit()
    autonaApp.settle()

    assert autonaApp.get(prefix) is None, (
        "a destroyed board rewrote its state subtree: its connections to the "
        "process-wide singletons were never disconnected")
