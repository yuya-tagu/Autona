"""HeldBackText, the rule the player cards, the crew board, the commentary
panel and the board 1/2 team name share for a field a render had to leave
alone (src/Helpers/AutonaQtHelper.py, ADR-005 S3e-2).

The rule itself, without a widget or a QApplication: the keys are plain
strings, as the callers' fields are only keys to it. The widgets' tests
(test_player_card_render.py, test_crew_operations.py,
test_commentary_service.py) pin the same cases through real focus.
"""
from src.Helpers.AutonaQtHelper import HeldBackText


def _shown(text, key="tag"):
    held = HeldBackText()
    held.Shown(key, text)
    return held


def test_a_finished_field_holding_the_state_is_no_edit():
    held = _shown("A")
    assert held.IsEdit("tag", "A", stateHolds=True) is False


def test_typing_with_no_render_in_between_is_an_edit():
    held = _shown("A")
    assert held.IsEdit("tag", "AB", stateHolds=False) is True


def test_a_finished_edit_becomes_what_the_field_shows():
    held = _shown("A")
    assert held.IsEdit("tag", "AB", stateHolds=False) is True
    # The state moves on while the operator sits in the field again: the
    # text they wrote is now the one it showed in step with the state.
    held.LeftAlone("tag", "AB", "C")
    assert held.IsHeldBack("tag", "AB")


# ── (a) the state moved on from what the field showed ───────────────────

def test_an_untouched_field_the_state_moved_on_from_is_held_back():
    held = _shown("A")
    held.LeftAlone("tag", "A", "B")
    assert held.IsHeldBack("tag", "A")
    assert held.IsEdit("tag", "A", stateHolds=False) is False


def test_typing_after_the_state_moved_on_wins():
    """The operator was only in the field when the external write arrived
    (held back), then typed on: what they typed is their edit, and is
    written over the external value (rule (b) does not apply -- they were
    not typing when it arrived)."""
    held = _shown("A")
    held.LeftAlone("tag", "A", "B")
    assert held.IsEdit("tag", "A!", stateHolds=False) is True


# ── (b) the field's own value was written elsewhere while it was typed ──

def test_typing_when_the_fields_value_is_written_elsewhere_is_held_back():
    held = _shown("")
    held.LeftAlone("tag", "TYPED", "ELSEWHERE")
    assert held.IsHeldBack("tag", "TYPED")
    assert held.IsEdit("tag", "TYPED", stateHolds=False) is False


def test_only_the_first_render_that_sees_the_value_holds_typing_back():
    held = _shown("")
    held.LeftAlone("tag", "TYPED", "ELSEWHERE")
    # A later pass (another field changed) sees the same value: typing
    # since is the operator's again.
    held.LeftAlone("tag", "TYPED!", "ELSEWHERE")
    assert not held.IsHeldBack("tag", "TYPED!")
    assert held.IsEdit("tag", "TYPED!", stateHolds=False) is True


def test_typing_while_another_field_renders_is_an_edit():
    """The field's own value did not move (another field rendered): the
    text in it is the operator's, and finishing it writes it."""
    held = _shown("")
    held.LeftAlone("tag", "Bob", "")
    assert not held.IsHeldBack("tag", "Bob")
    assert held.IsEdit("tag", "Bob", stateHolds=False) is True


# ── what clears it ──────────────────────────────────────────────────────

def test_showing_the_state_clears_what_was_held_back():
    held = _shown("A")
    held.LeftAlone("tag", "A", "B")
    held.Shown("tag", "B")
    assert not held.IsHeldBack("tag", "A")
    # ...and the value a render saw: the next write of the field's value
    # elsewhere is news again.
    held.LeftAlone("tag", "TYPED", "B")
    assert not held.IsHeldBack("tag", "TYPED")


def test_finishing_a_held_back_field_keeps_it_held_back():
    """IsEdit refusing the held-back text does not forget it: Enter and the
    focus-out that follows are both finishes of the same text."""
    held = _shown("A")
    held.LeftAlone("tag", "A", "B")
    assert held.IsEdit("tag", "A", stateHolds=False) is False
    assert held.IsEdit("tag", "A", stateHolds=False) is False


def test_the_fields_are_independent():
    held = HeldBackText()
    held.Shown("tag", "A")
    held.Shown("sponsor", "S")
    held.LeftAlone("tag", "A", "B")
    assert held.IsHeldBack("tag", "A")
    assert not held.IsHeldBack("sponsor", "A")
    assert held.IsEdit("sponsor", "S2", stateHolds=False) is True


def test_a_field_never_shown_counts_as_showing_nothing():
    held = HeldBackText()
    held.LeftAlone("tag", "", "B")
    assert held.IsHeldBack("tag", "")
    held.LeftAlone("other", "T", "B")
    assert held.IsHeldBack("other", "T")
