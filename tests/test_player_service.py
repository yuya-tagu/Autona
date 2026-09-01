"""Contract tests for src/core/PlayerService.py (headless core Step 5).

The player card was the widget with the most state keys and the fewest
testable rules: what a merged display name looks like, where a local avatar
is looked for, what "this slot is now somebody else" means for the
statistics fetches -- all of it lived inside a QGroupBox that could only be
built with a QApplication, three combo models and a .ui file. **Nothing here
builds a widget**, which is the point of the move; the widget-level
behaviour it is paired with stays in tests/test_player_widgets.py.

Two properties get most of the attention:

- **which path a write lands on.** One class serves two path shapes (a
  board's `score.{N}.team.{T}.player.{M}` and the PLAYERS editor's
  `player_editor.1`), and mixing them up would publish an operator's
  scratch edits onto a live scoreboard. agent-pitfalls #7 is about exactly
  this.
- **which save cycle it lands in.** `mergedName` / `mergedOnlyName` are one
  fact and must not reach an overlay half-updated, and the two guarded
  writes (`city`, `id`) must not mark a held board dirty for a value it
  already holds.

Isolation follows tests/test_actions.py: temp CWD, and the shared
StateManager / SettingsManager handed back untouched.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core import StateSchema as schema
from src.core.PlayerService import KEEP_ONLINE_AVATAR, PlayerService
from src.Helpers.AutonaDictHelper import deep_clone
from src.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_player_service_test_")
_ORIGINAL_CWD = os.getcwd()

# See tests/test_actions.py: StateManager.Initialize() is a once-per-process
# no-op already spent by tests/test_state_manager_held.py, so only the
# directories the export thread writes into are created here.
os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)

BOARD_PATH = "score.1.team.1.player.1"
EDITOR_PATH = f"{schema.PLAYER_EDITOR_ROOT}.1"
PREFIX = "score.1"


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        heldPrefixes = set(SM.heldPrefixes)
        heldExempt = dict(SM.heldExempt)
        heldKeys = {k: list(v) for k, v in SM.heldKeys.items()}
        changedKeys = list(SM.changedKeys)
        SM.state.pop("score", None)
        SM.state.pop(schema.PLAYER_EDITOR_ROOT, None)
        SM.lastSavedState.pop("score", None)
        SM.lastSavedState.pop(schema.PLAYER_EDITOR_ROOT, None)
        SM.heldPrefixes.discard(PREFIX)
        SM.heldExempt.pop(PREFIX, None)
        SM.heldKeys.pop(PREFIX, None)
        SM._commitPrefixes.discard(PREFIX)
        SM._heldTouched.discard(PREFIX)
        SM.changedKeys = []
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.heldPrefixes = heldPrefixes
            SM.heldExempt = heldExempt
            SM.heldKeys = heldKeys
            SM.changedKeys = changedKeys
            SM._commitPrefixes.clear()
            SM._heldTouched.clear()
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def player():
    return PlayerService(BOARD_PATH)


def _heldSpy():
    """(list, unsubscribe) collecting held_state_updated for score.1."""
    seen = []

    def onHeld(prefix):
        if prefix == PREFIX:
            seen.append(prefix)

    SM.observers.held_state_updated.subscribe(onHeld)
    return seen, lambda: SM.observers.held_state_updated.unsubscribe(onHeld)


# ── the two path shapes (agent-pitfalls #7) ──────────────────────────────
#
# The list in agent-pitfalls names three; two of the callers are gone. The
# commentary panel builds its own cards (AutonaCommentaryWidget) and the
# `{base}.slot.{I}.player.{M}` roster editor was retired, so what ships is a
# board slot and the PLAYERS editor. Both are covered below, and
# test_player_widgets.py pins that those are the only two constructions.

@pytest.mark.parametrize("path", [BOARD_PATH, EDITOR_PATH])
def test_every_write_stays_under_the_slot_it_was_given(path):
    service = PlayerService(path)

    service.SetText(schema.PLAYER_TAG_KEY, "TAG")
    service.SetSeed(4)
    service.SetCity("Tokyo")
    service.PublishMergedName("SPONSOR", "TAG", losers=False)

    assert SM.Get(f"{path}.{schema.PLAYER_TAG_KEY}") == "TAG"
    assert SM.Get(f"{path}.{schema.PLAYER_SEED_KEY}") == 4
    assert SM.Get(f"{path}.city") == "Tokyo"
    assert SM.Get(f"{path}.mergedName") == "SPONSOR | TAG"


def test_the_editor_slot_never_reaches_a_board():
    """The PLAYERS panel edits a scratch subtree, not the live scoreboard."""
    PlayerService(EDITOR_PATH).SetText(schema.PLAYER_TAG_KEY, "DRAFT")

    assert SM.Get(f"{EDITOR_PATH}.{schema.PLAYER_TAG_KEY}") == "DRAFT"
    assert SM.Get("score") is None


@pytest.mark.parametrize("path,expected", [
    ("score.1.team.1.player.1", 1),
    ("score.2.team.2.player.3", 2),
    ("score.3.team.1.player.5", 3),
    (EDITOR_PATH, None),
    ("", None),
])
def test_the_board_a_slot_belongs_to(path, expected):
    """What the details dialog's 反映 button resolves before committing."""
    assert PlayerService(path).BoardNumber() == expected


# ── the card's own inputs ────────────────────────────────────────────────

@pytest.mark.parametrize("field", schema.PLAYER_TEXT_KEYS)
def test_each_text_field_is_accepted_and_published(player, field):
    assert player.SetText(field, "value") is True
    assert SM.Get(f"{BOARD_PATH}.{field}") == "value"


def test_an_unknown_text_field_is_refused(player):
    """The loop this replaced wrote whatever objectName it walked past.

    Which is how the editable country/state combos' *internal* line edits
    (objectName "") ended up connected to it: finishing an edit in either
    published a key with an empty last segment under the player, and every
    export carried it.
    """
    assert player.SetText("", "stray") is False
    assert player.SetText("qt_spinbox_lineedit", "0") is False
    assert SM.Get(BOARD_PATH) in (None, {}), (
        "a refused field still wrote something")


def test_a_missing_text_value_publishes_an_empty_string(player):
    player.SetText(schema.PLAYER_SPONSOR_KEY, None)
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_SPONSOR_KEY}") == ""


@pytest.mark.parametrize("value,expected", [
    (7, 7), ("7", 7), (0, 0), (None, 0), ("", 0), ("not a number", 0),
])
def test_the_seed_is_always_published_as_a_number(player, value, expected):
    """Three callers used to write it in two types.

    The spinbox's valueChanged and SetData wrote an int; the spinbox's
    internal line edit went through the objectName loop and wrote its
    *text*, so a Clear() left the string "0" where an overlay expected a
    number.
    """
    player.SetSeed(value)
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_SEED_KEY}") == expected


def test_the_location_fields_publish_the_whole_selected_row(player):
    row = {"code": "JP", "name": "Japan", "asset": "./assets/jp.png"}
    assert player.SetLocation(schema.PLAYER_COUNTRY_KEY, row) is True
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_COUNTRY_KEY}") == row


def test_an_unknown_location_field_is_refused(player):
    assert player.SetLocation("variants", {"code": "x"}) is False
    assert SM.Get(f"{BOARD_PATH}.variants") is None


def test_an_empty_location_is_published_rather_than_skipped(player):
    """The blank row every region rebuild starts on has to reach the state.

    Guarding it as "same value, skip" would swallow the publish
    tests/test_player_widgets counts to know the in-place refill still emits
    exactly one index change per country change.
    """
    player.SetLocation(schema.PLAYER_STATE_KEY, {})
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_STATE_KEY}") == {}


# ── the display name ─────────────────────────────────────────────────────

@pytest.mark.parametrize("sponsor,tag,losers,merged,nameOnly", [
    ("SPONSOR", "TAG", False, "SPONSOR | TAG", "TAG"),
    ("", "TAG", False, "TAG", "TAG"),
    ("SPONSOR", "TAG", True, "SPONSOR | TAG [L]", "TAG [L]"),
    ("", "TAG", True, "TAG [L]", "TAG [L]"),
])
def test_the_merged_name_spellings(player, sponsor, tag, losers, merged,
                                   nameOnly):
    player.PublishMergedName(sponsor, tag, losers)

    assert SM.Get(f"{BOARD_PATH}.mergedName") == merged
    # The compact overlays render this one; it must never pick the sponsor up.
    assert SM.Get(f"{BOARD_PATH}.mergedOnlyName") == nameOnly


def test_the_two_name_spellings_are_one_save_cycle(player):
    """They are one fact, so an overlay must never see them disagree.

    Two bare Sets is what this used to be, which made a single name edit
    notify a held board twice.
    """
    SM.HoldSubtree(PREFIX)
    seen, unsubscribe = _heldSpy()
    try:
        player.PublishMergedName("SPONSOR", "TAG", losers=False)
    finally:
        unsubscribe()
        SM.heldPrefixes.discard(PREFIX)

    assert seen == [PREFIX], (
        f"publishing the display name produced {len(seen)} save cycles")


# ── the images ───────────────────────────────────────────────────────────

AVATAR = "https://cdn.example.invalid/avatar/abc123"


def test_the_online_avatar_is_left_alone_by_default(player):
    """LIVE-BUG-1: the default used to be None.

    Both deferred refreshes (SetData's finally block and NameChanged) then
    published None over the URL that had just been written, so every set
    loaded from start.gg reached the overlays with no avatar.
    """
    player.SetOnlineAvatar(AVATAR)
    player.PublishImages("SPONSOR", "TAG")

    assert SM.Get(f"{BOARD_PATH}.online_avatar") == AVATAR


def test_the_online_avatar_can_be_cleared_explicitly(player):
    player.SetOnlineAvatar(AVATAR)
    player.PublishImages("SPONSOR", "TAG", onlineAvatar=None)

    assert SM.Get(f"{BOARD_PATH}.online_avatar") is None


def test_the_sentinel_is_the_one_value_that_means_leave_it(player):
    player.SetOnlineAvatar(AVATAR)
    player.PublishImages("S", "T", onlineAvatar=KEEP_ONLINE_AVATAR)
    assert SM.Get(f"{BOARD_PATH}.online_avatar") == AVATAR


def test_a_missing_local_avatar_clears_the_previous_one(player):
    # Nothing under ./user_data/player_avatar in the temp CWD.
    player.PublishImages("SPONSOR", "TAG")
    assert SM.Get(f"{BOARD_PATH}.avatar") is None


def test_a_local_avatar_file_is_published_by_path(player):
    """The file is looked up by "<sponsor> <tag>", sanitised for a filename."""
    from src.core import PlayerService as module

    directory = Path(os.getcwd()) / "user_data" / "player_avatar"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SPONSOR TAG.png").write_bytes(b"")
    try:
        player.PublishImages("SPONSOR", "TAG")
        assert SM.Get(f"{BOARD_PATH}.avatar") == (
            f"{module.AVATAR_DIR}/SPONSOR TAG.png")
    finally:
        (directory / "SPONSOR TAG.png").unlink()


def test_filename_unsafe_characters_are_replaced_in_the_avatar_lookup(player):
    from src.core import PlayerService as module

    directory = Path(os.getcwd()) / "user_data" / "player_avatar"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "A_B TAG.png").write_bytes(b"")
    try:
        player.PublishImages("A|B", "TAG")
        assert SM.Get(f"{BOARD_PATH}.avatar") == (
            f"{module.AVATAR_DIR}/A_B TAG.png")
    finally:
        (directory / "A_B TAG.png").unlink()


def test_publishing_images_is_one_save_cycle(player):
    SM.HoldSubtree(PREFIX)
    seen, unsubscribe = _heldSpy()
    try:
        player.PublishImages("SPONSOR", "TAG", onlineAvatar=AVATAR)
    finally:
        unsubscribe()
        SM.heldPrefixes.discard(PREFIX)

    assert seen == [PREFIX], (
        f"the image refresh produced {len(seen)} save cycles; the avatar and "
        "the sponsor crests belong in one")


# ── the character slots ──────────────────────────────────────────────────

def test_the_character_slots_are_published_whole(player):
    slots = {1: {"name": "Mario", "skin": 0}, 2: {"name": "Luigi", "skin": 1}}
    player.PublishCharacters(slots)

    assert SM.Get(f"{BOARD_PATH}.character") == slots
    assert SM.Get(f"{BOARD_PATH}.mains") is None


def test_mains_are_only_republished_when_asked(player):
    slots = {1: {"name": "Mario", "skin": 0}}
    player.PublishCharacters(slots, includeMains=True)

    assert SM.Get(f"{BOARD_PATH}.character") == slots
    assert SM.Get(f"{BOARD_PATH}.mains") == slots


def test_character_and_mains_are_one_save_cycle(player):
    SM.HoldSubtree(PREFIX)
    seen, unsubscribe = _heldSpy()
    try:
        player.PublishCharacters({1: {"name": "Mario"}}, includeMains=True)
    finally:
        unsubscribe()
        SM.heldPrefixes.discard(PREFIX)

    assert seen == [PREFIX]


# ── the two guarded writes ───────────────────────────────────────────────

def test_the_city_is_only_written_when_it_moves(player):
    assert player.SetCity("Tokyo") is True
    assert player.SetCity("Tokyo") is False
    assert player.SetCity("Osaka") is True


def test_re_asserting_the_city_does_not_dirty_a_held_board(player):
    """A set refresh re-sends the same entrant every few seconds."""
    SM.HoldSubtree(PREFIX)
    try:
        player.SetCity("Tokyo")
        SM.CommitHeld(PREFIX)
        assert SM.HasPendingChanges(PREFIX) is False

        player.SetCity("Tokyo")
        assert SM.HasPendingChanges(PREFIX) is False, (
            "an identical city lit the 反映 button")
    finally:
        SM.heldPrefixes.discard(PREFIX)
        SM._commitPrefixes.discard(PREFIX)
        SM.heldKeys.pop(PREFIX, None)


def test_the_player_id_reports_whether_the_slot_changed_hands(player):
    """The caller hangs the statistics fetches off the return value.

    Re-running them for an id that did not move is what the guard inside the
    old widget write prevented, and it has to survive the move.
    """
    assert player.SetPlayerId("123") is True
    assert player.SetPlayerId("123") is False
    assert player.SetPlayerId("456") is True
    assert SM.Get(f"{BOARD_PATH}.id") == "456"


# ── the slot's lifetime ──────────────────────────────────────────────────

def test_unset_drops_the_whole_slot(player):
    player.SetText(schema.PLAYER_TAG_KEY, "TAG")
    player.PublishMergedName("", "TAG", losers=False)
    assert SM.Get(BOARD_PATH) is not None

    player.Unset()

    assert SM.Get(BOARD_PATH) is None, (
        "a torn-down player widget left its name on the overlay")
