"""A failing handler must not switch off saving for the rest of the session.

`StateManager.saveBlocked` is a counter: SaveState only runs when it is back
at zero. So a code path that increments it and then dies before the matching
decrement does not lose one write -- it loses every write from that moment
on. `./out` stops updating and the WebSocket feed stops changing, with no
error visible anywhere in the UI. During a stream that is the whole overlay
frozen on the previous match.

BUG-3 (docs/changes/test-automation-strategy.md §3) was exactly this: the
best_of handler chained BlockSaving() ... ReleaseSaving() as elements of a
list built inside a lambda, so any exception in between skipped the release.
It was reachable -- an unloaded locale makes matchNames.get("best_of") return
None and .format() raise -- and it is the one place where a bug in a single
handler takes the entire output layer down with it.

These tests therefore assert two things after a handler blows up: that the
counter is back to zero, and that a later write really does reach ./out.
Only the second one is the user-visible contract; the first says why.
"""
import pytest

from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper


# A key of its own rather than one of the board's: everything under score.{n}
# is a held subtree (the 反映 button), so those writes sit in the hold buffer
# and reaching ./out would prove nothing about SaveState having run.
PROBE_KEY = "test_save_block_probe"


def _probeFile(app):
    return app.dir / "out" / f"{PROBE_KEY}.txt"


def _breakBestOfFormatting(monkeypatch):
    """Make the best_of handler raise partway through, the way BUG-3 did.

    An empty table is what AutonaLocaleHelper actually holds before
    LoadLocale() runs, so this is the real failure and not an injected one:
    matchNames.get("best_of") returns None and the .format() call on it
    raises AttributeError -- after best_of has been written and before
    first_to is.
    """
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {})


# ── the happy path ───────────────────────────────────────────────────────

def test_best_of_change_leaves_saving_unblocked(autonaApp):
    board = autonaApp.board(1)

    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    assert autonaApp.state.saveBlocked == 0


# ── the path that used to wedge it ───────────────────────────────────────

def test_a_failing_best_of_handler_leaves_saving_unblocked(
        autonaApp, monkeypatch):
    board = autonaApp.board(1)
    _breakBestOfFormatting(monkeypatch)

    # PySide6 reports an exception raised inside a slot and carries on, so
    # setValue() returns normally here and the traceback goes to stderr --
    # which is precisely why the leaked counter was invisible in production.
    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    assert autonaApp.state.saveBlocked == 0, (
        "the best_of handler kept saving blocked after raising; "
        "every later SaveState is suppressed")


def test_state_still_exports_after_a_failing_best_of_handler(
        autonaApp, monkeypatch):
    """The contract the counter assertion above stands in for."""
    board = autonaApp.board(1)
    state = autonaApp.state

    try:
        state.Set(PROBE_KEY, "before")
        autonaApp.settle()

        exported = _probeFile(autonaApp)
        assert exported.is_file(), "precondition: state should export at all"
        assert exported.read_text(encoding="utf-8") == "before"

        _breakBestOfFormatting(monkeypatch)
        board.bestOfSpin.setValue(5)
        autonaApp.settle()

        # Business as usual afterwards: the next write reaches the overlay.
        state.Set(PROBE_KEY, "after")
        autonaApp.settle()

        assert exported.read_text(encoding="utf-8") == "after", (
            "state stopped reaching ./out after the best_of handler raised")
    finally:
        # Shared process, shared state: leave nothing behind for the tests
        # that assert on the exported tree as a whole.
        state.Unset(PROBE_KEY)
        autonaApp.settle()


def test_the_widget_survives_a_failing_best_of_handler(autonaApp, monkeypatch):
    """...and the next, successful, change publishes normally."""
    board = autonaApp.board(1)

    with monkeypatch.context() as broken:
        _breakBestOfFormatting(broken)
        board.bestOfSpin.setValue(5)
        autonaApp.settle()

    board.bestOfSpin.setValue(3)
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 3
    assert autonaApp.get("score.1.first_to") == 2
    assert "3" in autonaApp.get("score.1.best_of_text")
    assert autonaApp.state.saveBlocked == 0


# ── the structural rule, so the shape cannot come back ───────────────────

def test_the_best_of_handler_is_a_method_not_a_lambda_chain(autonaApp):
    """BlockSaving/ReleaseSaving as list elements is what caused BUG-3.

    A lambda returning `[BlockSaving(), Set(...), ..., ReleaseSaving()]` has
    no place to put a finally, so the pairing is only as safe as the code
    between the brackets. Pinning the handler as a real method keeps the
    `with StateManager.SaveBlock():` in AutonaScoreboardWidget the only way
    to write it.
    """
    import inspect

    from src.AutonaScoreboardWidget import AutonaScoreboardWidget

    source = inspect.getsource(AutonaScoreboardWidget._onBestOfChanged)
    # Comments in there talk about the two calls by name; only real code counts.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))

    assert "with StateManager.SaveBlock():" in code
    assert "BlockSaving()" not in code
    assert "ReleaseSaving()" not in code


@pytest.mark.parametrize("boardNumber", [1, 2])
def test_best_of_handler_is_shared_by_both_scoreboards(autonaApp, boardNumber):
    """1v1 and team are the same class here; assert it rather than assume."""
    board = autonaApp.board(boardNumber)

    board.bestOfSpin.setValue(7)
    autonaApp.settle()

    assert autonaApp.get(f"score.{boardNumber}.best_of") == 7
    assert autonaApp.get(f"score.{boardNumber}.first_to") == 4
    assert autonaApp.state.saveBlocked == 0
