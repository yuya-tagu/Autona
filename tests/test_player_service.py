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
import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import StateSchema as schema
from src.core.CountryCatalog import CountryCatalog
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.PlayerService import (
    KEEP_ONLINE_AVATAR, CharacterSlot, PlayerService,
    RepublishTeamMergedNames)
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager as SM
from tests.conftest import _link_tree
from tests.fixtures.player_apply import scenarios as applyScenarios
from tests.fixtures.player_slots import recording

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
# commentary panel was a React page (it built no player cards) and the
# `{base}.slot.{I}.player.{M}` roster editor was retired, so what ships is a
# board slot and the PLAYERS editor. Both are covered below, and
# test_player_widgets.py pins that those are the only two constructions.

@pytest.mark.parametrize("path", [BOARD_PATH, EDITOR_PATH])
def test_every_write_stays_under_the_slot_it_was_given(path):
    service = PlayerService(path)

    service.SetText(schema.PLAYER_TAG_KEY, "TAG")
    service.SetSeed(4)
    service.SetCity("Tokyo")
    service.PublishMergedName("SPONSOR", "TAG")

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


# ── the location: codes in, the catalog's rows out (ADR-005 S1d) ────────
#
# The card hands over the picked codes and the value comes from
# CountryCatalog. A two-row stand-in catalog is enough for the rules; the
# shipped data is replayed against the recording at the end of the module.

class _Countries:
    """CountryCatalog's two lookups over literal rows (fresh dicts, as the
    catalog returns), recording what was asked."""

    def __init__(self):
        self.asked = []

    def CountryByCode(self, code):
        self.asked.append(("country", code))
        if code == "JP":
            return {"name": "Japan", "en_name": "Japan", "code": "JP",
                    "asset": "./assets/country_flag/jp.png"}
        return None

    def State(self, iso2, code):
        self.asked.append(("state", iso2, code))
        if (iso2, code) == ("JP", "13"):
            return {"name": "Tokyo", "code": "13", "original_code": "13",
                    "asset": "./assets/state_flag/JP/13.png"}
        return {}


@pytest.mark.parametrize("path", [BOARD_PATH, EDITOR_PATH])
def test_the_country_is_the_catalog_row_for_its_code(path):
    countries = _Countries()
    PlayerService(path, countries=countries).SetCountry("JP")

    published = SM.Get(f"{path}.{schema.PLAYER_COUNTRY_KEY}")
    assert published == countries.CountryByCode("JP")
    # The order a Qt model handed the row out in (QVariantMap: UTF-16 code
    # units), which program_state.json and the Socket.IO delta carry.
    assert list(published) == ["asset", "code", "en_name", "name"]


@pytest.mark.parametrize("path", [BOARD_PATH, EDITOR_PATH])
def test_the_region_is_resolved_against_the_country_the_card_shows(path):
    countries = _Countries()
    PlayerService(path, countries=countries).SetRegion("JP", "13")

    published = SM.Get(f"{path}.{schema.PLAYER_STATE_KEY}")
    assert countries.asked == [("state", "JP", "13")]
    assert list(published) == ["asset", "code", "name", "original_code"]
    assert published["name"] == "Tokyo"


def test_no_country_and_no_region_are_an_empty_dict():
    countries = _Countries()
    service = PlayerService(BOARD_PATH, countries=countries)

    service.SetCountry(None)
    service.SetRegion("JP", None)

    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_COUNTRY_KEY}") == {}
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_STATE_KEY}") == {}
    assert countries.asked == [], "nothing to look up for no pick"


def test_a_code_the_catalog_does_not_know_is_no_location():
    """A name typed into the editable country (or region) combo, or the
    custom-flag separator, has no row behind it. The card used to publish
    that missing row as null; it publishes {} now, the type every other
    "no country" / "no region" has always had."""
    service = PlayerService(BOARD_PATH, countries=_Countries())

    service.SetCountry("Atlantis")
    service.SetRegion("JP", "99")

    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_COUNTRY_KEY}") == {}
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_STATE_KEY}") == {}


def test_an_empty_location_is_published_rather_than_skipped():
    """The blank row every region rebuild starts on has to reach the state.

    Guarding it as "same value, skip" would swallow the publish
    tests/test_player_widgets counts to know the in-place refill still emits
    exactly one index change per country change.
    """
    service = PlayerService(BOARD_PATH, countries=_Countries())
    with patch.object(SM, "Set", wraps=SM.Set) as written:
        service.SetRegion(None, None)
        service.SetRegion(None, None)

    assert written.call_count == 2
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
    # The flag is the team's, read from the state (ADR-005 S2b).
    SM.Set(f"score.1.team.1.{schema.TEAM_LOSERS_KEY}", losers)

    player.PublishMergedName(sponsor, tag)

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
        player.PublishMergedName("SPONSOR", "TAG")
    finally:
        unsubscribe()
        SM.heldPrefixes.discard(PREFIX)

    assert seen == [PREFIX], (
        f"publishing the display name produced {len(seen)} save cycles")


def test_the_suffix_is_the_slots_own_team_flag():
    """Each slot reads its own team's flag; the editor has no team."""
    SM.Set(f"score.1.team.2.{schema.TEAM_LOSERS_KEY}", True)

    PlayerService("score.1.team.1.player.1").PublishMergedName("", "ONE")
    PlayerService("score.1.team.2.player.1").PublishMergedName("", "TWO")
    PlayerService("score.2.team.2.player.1").PublishMergedName("", "OTHER")
    PlayerService(EDITOR_PATH).PublishMergedName("", "DRAFT")

    assert SM.Get("score.1.team.1.player.1.mergedName") == "ONE"
    assert SM.Get("score.1.team.2.player.1.mergedName") == "TWO [L]"
    assert SM.Get("score.2.team.2.player.1.mergedName") == "OTHER"
    assert SM.Get(f"{EDITOR_PATH}.mergedName") == "DRAFT"
    assert PlayerService(EDITOR_PATH).TeamLosersKey() is None


# ── the team's names, rebuilt from the state (ADR-005 S2b) ───────────────

def _slot(team, number, sponsor, tag):
    path = f"score.1.team.{team}.player.{number}"
    SM.Set(f"{path}.{schema.PLAYER_SPONSOR_KEY}", sponsor)
    SM.Set(f"{path}.{schema.PLAYER_TAG_KEY}", tag)
    return path


def test_a_team_rebuild_republishes_every_slot_from_its_state():
    one = _slot(1, 1, "SP", "ALPHA")
    two = _slot(1, 2, "", "BETA")
    # A slot holding no name yet still gets the suffix, as every card did.
    SM.Set("score.1.team.1.player.3.seed", 0)
    other = _slot(2, 1, "", "GAMMA")
    SM.Set(f"score.1.team.1.{schema.TEAM_LOSERS_KEY}", True)

    rebuilt = RepublishTeamMergedNames(1, 1)

    assert rebuilt == ["1", "2", "3"]
    assert SM.Get(f"{one}.mergedName") == "SP | ALPHA [L]"
    assert SM.Get(f"{one}.mergedOnlyName") == "ALPHA [L]"
    assert SM.Get(f"{two}.mergedName") == "BETA [L]"
    assert SM.Get("score.1.team.1.player.3.mergedName") == " [L]"
    assert SM.Get(f"{other}.mergedName") is None, "the other team was touched"


def test_a_team_rebuild_is_one_save_cycle():
    _slot(1, 1, "SP", "ALPHA")
    _slot(1, 2, "", "BETA")
    SM.Set(f"score.1.team.1.{schema.TEAM_LOSERS_KEY}", True)
    SM.HoldSubtree(PREFIX)
    seen, unsubscribe = _heldSpy()
    try:
        RepublishTeamMergedNames(1, 1)
    finally:
        unsubscribe()
        SM.heldPrefixes.discard(PREFIX)

    assert seen == [PREFIX]


def test_a_team_without_a_roster_rebuilds_nothing():
    with patch.object(SM, "Set", wraps=SM.Set) as written:
        assert RepublishTeamMergedNames(1, 1) == []
    assert written.call_count == 0


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


# ── the character slots: identifiers in, the catalog's rows out (S1d) ───

class _Games:
    """GameAssetCatalog's three row lookups over literal rows, recording the
    snapshot every lookup was resolved against."""

    def __init__(self):
        self.snapshots = []
        self.against = []

    def Current(self):
        snapshot = object()
        self.snapshots.append(snapshot)
        return snapshot

    def CharacterRow(self, en_name, snapshot=None):
        self.against.append(snapshot)
        rows = {
            "Mario": {"name": "Mario", "en_name": "Mario",
                      "display_name": "Mario", "codename": "mario",
                      "modded": False},
            "Doctor": {"name": "Doc", "en_name": "Doctor",
                       "display_name": "Doc", "codename": "doctor",
                       "modded": False},
        }
        return dict(rows[en_name]) if en_name in rows else None

    def SkinRow(self, en_name, row, snapshot=None):
        self.against.append(snapshot)
        if en_name == "Mario" and row == 1:
            return {"name": "", "en_name": "",
                    "assets": {"full": {"asset": "./m1.png"},
                               "base_files/icon": {"asset": "./i1.png"}}}
        if en_name == "Doctor" and row == 0:
            # A skin that is another character (the Dr. Mario case).
            return {"name": "Dr. Mario", "en_name": "Dr. Mario",
                    "assets": {}}
        return None

    def VariantRow(self, en_name, snapshot=None):
        self.against.append(snapshot)
        if en_name == "EX":
            return {"name": "EX", "en_name": "EX", "assets": {}}
        return None


def test_a_slot_is_built_from_the_rows_it_names():
    service = PlayerService(BOARD_PATH, games=_Games())

    value = service.CharacterValue(CharacterSlot("Mario", 1, "EX", "shown"))

    assert value == {
        "codename": "mario", "display_name": "Mario", "en_name": "Mario",
        "modded": False, "name": "Mario",
        "assets": {"base_files/icon": {"asset": "./i1.png"},
                   "full": {"asset": "./m1.png"}},
        "skin": 1,
        "variant": {"assets": {}, "en_name": "EX", "name": "EX"},
    }
    # The rows in QVariantMap order, then the three keys the card always
    # appended last, in the order it appended them.
    assert list(value) == ["codename", "display_name", "en_name", "modded",
                           "name", "assets", "skin", "variant"]
    assert list(value["assets"]) == ["base_files/icon", "full"]


def test_a_skin_that_is_another_character_renames_the_slot():
    value = PlayerService(BOARD_PATH, games=_Games()).CharacterValue(
        CharacterSlot("Doctor", 0, None, "Doc"))

    assert value["name"] == "Dr. Mario"
    assert value["en_name"] == "Dr. Mario"
    assert value["codename"] == "doctor"


@pytest.mark.parametrize("slot", [
    # The blank row, and a name typed into the editable combo.
    CharacterSlot(None, -1, None, ""),
    CharacterSlot(None, -1, None, "Typed Name"),
    # A character the loaded game does not have: the moment between a game
    # load and the combos' rebind. It converges on the rebind's republish.
    CharacterSlot("Ghost", 0, None, "Ghost"),
])
def test_a_slot_without_a_row_is_its_shown_text(slot):
    value = PlayerService(BOARD_PATH, games=_Games()).CharacterValue(slot)

    assert value == {"name": slot.text, "assets": {}, "skin": slot.skin,
                     "variant": {}}
    assert list(value) == ["name", "assets", "skin", "variant"]


def test_a_character_without_a_skin_row_has_no_assets():
    value = PlayerService(BOARD_PATH, games=_Games()).CharacterValue(
        CharacterSlot("Mario", -1, "nope", "Mario"))

    assert value["assets"] == {}
    assert value["skin"] == -1
    assert value["variant"] == {}


def test_every_slot_of_a_card_is_resolved_against_one_snapshot():
    """A game load swaps the catalog's snapshot; slot 1 and slot 3 of one
    publish must not come from two games."""
    games = _Games()
    PlayerService(BOARD_PATH, games=games).PublishCharacterSlots(
        [CharacterSlot("Mario", 1, "EX"), CharacterSlot("Doctor", 0),
         CharacterSlot()])

    assert len(games.snapshots) == 1
    assert games.against
    assert all(snapshot is games.snapshots[0] for snapshot in games.against)


@pytest.mark.parametrize("path", [BOARD_PATH, EDITOR_PATH])
def test_the_character_slots_are_published_whole(path):
    PlayerService(path, games=_Games()).PublishCharacterSlots(
        [CharacterSlot("Mario", 1, "EX"), CharacterSlot()])

    published = SM.Get(f"{path}.character")
    assert published[1]["name"] == "Mario"
    assert published[2] == {"name": "", "assets": {}, "skin": -1,
                            "variant": {}}
    assert SM.Get(f"{path}.mains") is None


def test_mains_are_only_republished_when_asked():
    service = PlayerService(BOARD_PATH, games=_Games())
    service.PublishCharacterSlots([CharacterSlot("Mario", 1)],
                                  includeMains=True)

    assert SM.Get(f"{BOARD_PATH}.mains") == SM.Get(f"{BOARD_PATH}.character")
    assert SM.Get(f"{BOARD_PATH}.mains")[1]["name"] == "Mario"


def test_character_and_mains_are_one_save_cycle():
    service = PlayerService(BOARD_PATH, games=_Games())
    SM.HoldSubtree(PREFIX)
    seen, unsubscribe = _heldSpy()
    try:
        service.PublishCharacterSlots([CharacterSlot("Mario", 1)],
                                      includeMains=True)
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


# ── the recording: the old card's bytes, without a widget (S1d) ──────────
#
# tests/fixtures/player_slots/golden.json holds what the card published
# before S1d -- the values read back out of its Qt models, keys in
# QVariantMap order -- for every row of every combo, with the identifiers
# of each pick. Replayed here through the service alone: the same
# identifiers have to give the same bytes. (tests/test_player_slot_publish.py
# replays it through the card, and documents how it was recorded.)

# The world the recording was made in (scenarios, custom flags, locale)
# and its encoding come from the module both replays share.
SLOT_GOLDEN = recording.loadGolden()
_encode = recording.encode
_digest = recording.digest


@pytest.fixture(scope="module")
def recordingDir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("player_slot_recording")
    link = directory / "assets"
    _link_tree(link, Path(__file__).resolve().parent.parent / "assets")
    (directory / "out").mkdir()
    shutil.copytree(recording.GAME_FIXTURE / "games",
                    directory / "user_data" / "games")
    flags = directory / "user_data" / "additional_flag"
    flags.mkdir(parents=True)
    for name in recording.CUSTOM_FLAGS:
        (flags / name).write_bytes(b"")
    try:
        yield directory
    finally:
        # The link is removed, never followed (conftest's _autonaRuntime
        # teardown, for the same reason): a POSIX symlink is unlinked, a
        # Windows junction is a directory entry and needs rmdir.
        try:
            if link.is_symlink():
                os.unlink(link)
            else:
                os.rmdir(link)
        except OSError:
            pass


@pytest.fixture
def recordedCatalogs(recordingDir, monkeypatch):
    """Both catalogs over the recording's data and locale. The CWD stays in
    the recording directory: a region's flag is resolved on the disk when it
    is asked for."""
    monkeypatch.chdir(recordingDir)

    locale = recording.LOCALE

    def locales():
        return locale["program"], locale["export"]

    games = GameAssetCatalog(locales=locales,
                             remap=recording.remapFor(locale["remapping"]))
    games.ResetGameState()
    games.ScanGames()
    countries = CountryCatalog(locales=locales)
    assert countries.Load() is True
    return games, countries


def _published(service, slots):
    """What one PublishCharacterSlots hands StateManager, encoded."""
    with patch.object(SM, "Set") as written:
        service.PublishCharacterSlots(slots)
    assert written.call_count == 1
    key, value = written.call_args.args
    assert key == f"{service.path}.character"
    return _encode(value)


@pytest.mark.parametrize("label", list(recording.GAME_SCENARIOS))
def test_every_recorded_character_slot_is_rebuilt_byte_for_byte(
        recordedCatalogs, label):
    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS[label]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    service = PlayerService(BOARD_PATH, games=games, countries=countries)
    golden = SLOT_GOLDEN[f"characters.{label}"]

    def published(entry):
        # The recording varied slot 1 of a three-slot card; the other two
        # stayed blank.
        return _published(service, [
            CharacterSlot(entry["character"], entry["skin"],
                          entry["variant"], entry["text"]),
            CharacterSlot(), CharacterSlot()])

    for sample in golden["samples"]:
        assert published(sample) == sample["published"], sample
    assert [_digest(published(entry)) for entry in golden["entries"]] \
        == [entry["digest"] for entry in golden["entries"]]


def test_every_recorded_country_and_region_is_rebuilt_byte_for_byte(
        recordedCatalogs):
    games, countries = recordedCatalogs
    service = PlayerService(BOARD_PATH, games=games, countries=countries)
    golden = SLOT_GOLDEN["locations"]

    def countryPick(code):
        # One pick of the country combo: the region list is rebuilt for the
        # new country (its blank row is published), then the country.
        return [["state", _encode(service.RegionValue(code, None))],
                ["country", _encode(service.CountryValue(code))]]

    samples = {sample["row"]: sample for sample in golden["countrySamples"]}
    for entry in golden["countries"]:
        calls = countryPick(entry["code"])
        if not entry["hasData"]:
            # The custom-flag separator published its missing row as null;
            # it is "no country" now (the one intended change here).
            assert calls == [["state", "{}"], ["country", "{}"]]
            continue
        if entry["row"] in samples:
            assert calls == samples[entry["row"]]["calls"], entry["code"]
        assert _digest(calls) == entry["digest"], entry["code"]

    def regionPicks(code):
        # Every row of the region combo, in the combo's (the catalog's)
        # order, named by the row's own code as the card names it.
        return [[region["code"],
                 [["state",
                   _encode(service.RegionValue(code, region["code"]))]]]
                for region in countries.States(code).values()]

    assert regionPicks("JP") == golden["regionSamples"]["JP"]
    assert [pick for pick in regionPicks("GB") if pick[0] == "CON"] \
        == golden["regionSamples"]["GB.CON"]
    assert {code: _digest(regionPicks(code)) for code in golden["regions"]} \
        == golden["regions"]


# ── loading a player: the S3e-3 recording, through the service alone ─────
#
# ADR-005 S3e-3 moved the card's SetData / Clear / SavePlayerToDB into this
# class (ApplyData / ClearSlot / SavePlayerToDB). tests/test_player_apply.py
# replays tests/fixtures/player_apply/golden.json -- recorded through the
# widget implementation -- through the card and the board; this replays
# every scenario that needs nothing but a slot (`core` not False) through
# PlayerService with no widget at all, the way a headless shell loads a
# player. Compared: each slot's exact value (key order included), every
# write under each slot (a load's runs collapsed, as the card replay
# compares them), the save cycles, local_players.json, the outcome, and the
# SlotChange each operation returns against the statistics triggers the
# board fired. A step that is the card's own pick or a board's slot count
# is reproduced from its result (the slot's value afterwards).

APPLY_GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "player_apply"
     / "golden.json").read_text(encoding="utf-8"))
CORE_SCENARIOS = [s for s in applyScenarios.SCENARIOS
                  if s.get("core", True)]


@pytest.fixture
def applyWorld(recordedCatalogs, recordingDir, tmp_path, monkeypatch):
    """Yields `run(scenario)` -> its record, from fresh slots, a player
    database of the scenario's players and its bad-word filter."""
    from src.core.BadWordFilter import BadWordFilter
    from src.core.PlayerDB import PlayerDB
    from src.core.SettingsManager import SettingsManager

    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS["tgame"]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    # The filter's switch, on as the recording had it (and as it defaults).
    assert SettingsManager.Get("general.profanity_filter", True) is True

    def run(scenario, number):
        work = tmp_path / f"scenario{number}"
        work.mkdir()
        db = PlayerDB(path=str(work / "local_players.json"),
                      legacyCsvPath=str(work / "local_players.csv"))
        db.database = {tag: copy.deepcopy(applyScenarios.DB_PLAYERS[tag])
                       for tag in scenario["db"]}
        db.SaveDB()
        monkeypatch.setattr(PlayerDB, "instance", db)
        ngword = work / "ngword"
        ngword.mkdir()
        if scenario.get("words"):
            (ngword / "common.txt").write_text(
                "\n".join(scenario["words"]), encoding="utf-16")
        badWords = BadWordFilter(ngwordDir=str(ngword),
                                 userDataDir=str(work / "user_data"))
        badWords.Initialize()
        monkeypatch.setattr(BadWordFilter, "instance", badWords)
        return _ApplyReplay(scenario, games, countries, db,
                            recordingDir).run()

    return run


class _ApplyReplay:
    def __init__(self, scenario, games, countries, db, directory):
        self.scenario = scenario
        self.games = games
        self.countries = countries
        self.db = db
        self.directory = directory
        board = scenario["board"]
        if board is None:
            self.root = EDITOR_PATH
            self.paths = [EDITOR_PATH]
        else:
            self.root = f"score.{board}"
            players = scenario.get("players", 1)
            # The board's card order: team 1's, then team 2's, per index.
            self.paths = [f"{self.root}.team.{team}.player.{slot}"
                          for slot in range(1, players + 1)
                          for team in (1, 2)]

    def service(self, path):
        return PlayerService(path, games=self.games,
                             countries=self.countries)

    def slotPath(self, slot):
        if slot == "editor":
            return EDITOR_PATH
        team, player = slot
        return f"{self.root}.team.{team}.player.{player}"

    def seed(self):
        """The slots a fresh board's cards (or the editor) publish."""
        with SM.SaveBlock():
            for path in self.paths:
                self.service(path).SeedEmpty(3)
        if self.scenario["board"] is not None:
            schema.HoldBoard(self.scenario["board"])

    def snapshot(self):
        slots = {}
        for path in self.paths:
            with SM.lock:
                value = deep_clone(SM.Get(path) or {})
            slots[path] = _digest(applyScenarios.normalise(
                _encode(value), self.directory))
        dbPath = Path(self.db.path)
        text = dbPath.read_bytes().decode("utf-8").replace("\r\n", "\n") \
            if dbPath.exists() else None
        return {"slots": slots, "db": text}

    def step(self, step):
        """(result, SlotChange or None) of one step."""
        op = step["op"]
        if op == "update":
            path = self.slotPath([step["team"], step["player"]])
            if SM.Get(path) is None:
                # The Web's roster check (the slot is in the state:
                # AutonaWebOperations.update_player, ADR-005 S3e-4).
                return False, None
            service = self.service(path)
            data = copy.deepcopy(step["data"])
            prepared = None
            with SM.SaveBlock():
                change = service.ApplyData(data, True, False)
                if data.get("savePlayerToDb", False):
                    prepared = service.PrepareSaveToDB()
            # The database write after the board host (S3e-4 review).
            if prepared is not None:
                service.SaveRecordToDB(prepared)
            return True, change
        if op == "chars":
            # Every card gets the new number of character rows, the new
            # ones blank, and republishes its slots with the mains.
            for path in self.paths:
                service = self.service(path)
                snap = self.games.Current()
                slots = service._CharacterSlots(
                    SM.Get(service.Key("character")), snap)
                slots = (slots + [CharacterSlot()] * step["count"])[
                    :step["count"]]
                with SM.SaveBlock():
                    service.PublishCharacterSlots(slots, includeMains=True)
            return None, None
        service = self.service(self.slotPath(step["slot"]))
        if op == "set":
            data = step["data"]
            data = self.db.Get(data) if isinstance(data, str) \
                else copy.deepcopy(data)
            return None, service.ApplyData(
                data, step["loadFromDB"], step["clear"], step["no_mains"])
        if op == "clear":
            return None, service.ClearSlot(step["no_mains"])
        if op == "save":
            service.SavePlayerToDB()
            return None, None
        if op == "typed":
            with SM.SaveBlock():
                service.PublishCharacterSlots(
                    [CharacterSlot(**slot) for slot in step["slots"]])
            return None, None
        if op == "pick":
            snap = self.games.Current()
            slots = service._CharacterSlots(
                SM.Get(service.Key("character")), snap)
            row = self.games.CharacterRow(step["character"], snapshot=snap)
            slots[step["index"] - 1] = CharacterSlot(
                step["character"], step["skin"], step["variant"],
                row.get("name") or "")
            with SM.SaveBlock():
                service.PublishCharacterSlots(slots)
            return None, None
        raise AssertionError(f"no service step for {op!r}")

    # The operations that answer with a SlotChange.
    SLOT_OPS = ("set", "clear", "update")

    def run(self):
        self.seed()
        record = {"initial": self.snapshot(), "steps": []}
        for step in self.scenario["steps"]:
            change = None
            if step["op"] in self.SLOT_OPS:
                slot = step.get("slot") or [step["team"], step["player"]]
                watched = self.service(self.slotPath(slot))
                before = watched.Identity()
            with applyScenarios.Cycles(self.root) as cycles, \
                    applyScenarios.Writes(self.root) as writes:
                try:
                    result, returned = self.step(step)
                except Exception as e:
                    result, returned = {"raises": type(e).__name__}, None
            if step["op"] in self.SLOT_OPS:
                # Measured, as the board does around a call: a load that
                # raised half-way has still changed what it wrote.
                change = watched.ChangeSince(before)
                if returned is not None:
                    assert returned == change, step
            entry = {"op": step["op"], "result": result, "change": change,
                     "cycles": cycles.count,
                     "calls": applyScenarios.compact(writes.calls)}
            entry.update(self.snapshot())
            record["steps"].append(entry)
        return record


def _desktopMains(text):
    """local_players.json with the loaded game's mains as the desktop holds
    them: [character, skin as a number, variant or ""].

    The desktop's completer model rewrites them so in place on every
    database change (AutonaPlayerDB._BuildModel, S3c-2 decision 5), and the
    next save writes them out; the service alone (a headless shell) leaves
    a record's mains as they were stored. S3e-3 kept that in the adapter: a
    load reads both spellings alike (PlayerService._ApplyMains), and moving
    it would change the headless shell's local_players.json and /playerdb.
    Applied to both sides, so only this difference is excused."""
    if text is None:
        return None
    players = json.loads(text)
    for player in players:
        mains = player.get("mains")
        if not isinstance(mains, dict):
            continue
        for main in mains.get("tgame") or []:
            if not isinstance(main, list) or len(main) not in (2, 3):
                continue
            while len(main) < 3:
                main.append("")
            try:
                main[1] = int(main[1])
            except (TypeError, ValueError):
                main[1] = 0
    return json.dumps(players, indent=2, ensure_ascii=False)


# The steps the service reproduces by their result only: the operator's
# pick on the card's combos (a write per combo) and the board's slot count
# (a write per card).
BY_RESULT_OPS = ("pick", "chars")


def _assertServiceStep(name, index, actual, expected, board):
    where = (name, index, expected["op"])
    expected = applyScenarios.Expected(name, index, expected)
    assert actual["slots"] == expected["slots"], where
    assert _desktopMains(actual["db"]) == _desktopMains(expected["db"]), where
    assert actual["result"] == expected["result"], where
    if expected["op"] in BY_RESULT_OPS:
        return
    assert actual["cycles"] == expected["cycles"], where

    def calls(step):
        grouped = applyScenarios.byPlayer(step["calls"])
        if board is not None:
            # The board's own keys are the statistics' writes.
            grouped.pop("", None)
        return grouped

    if expected.get("callsByEffect"):
        assert {path: applyScenarios.effect(c)
                for path, c in calls(actual).items()} \
            == {path: applyScenarios.effect(c)
                for path, c in calls(expected).items()}, where
    else:
        assert {path: applyScenarios.collapsed(c)
                for path, c in calls(actual).items()} \
            == {path: applyScenarios.collapsed(c)
                for path, c in calls(expected).items()}, where

    if board is None:
        return
    stats = expected["stats"]
    change = actual["change"]
    if change is None:
        assert not any(stats.values()), where
        return
    # One slot per step: its id moving is the recent sets and its team's
    # last sets / standings, its seed moving the upset factor.
    assert change.idChanged == (stats["RecentSetsSignal"] > 0), where
    assert change.seedChanged == (stats["UpsetFactorCalculation"] > 0), where


@pytest.mark.parametrize("scenario", CORE_SCENARIOS,
                         ids=[s["name"] for s in CORE_SCENARIOS])
def test_recorded_player_loads_are_reproduced_by_the_service_alone(
        applyWorld, scenario):
    record = applyWorld(scenario, CORE_SCENARIOS.index(scenario))
    expected = APPLY_GOLDEN[scenario["name"]]
    assert record["initial"]["slots"] == expected["initial"]["slots"]
    assert _desktopMains(record["initial"]["db"]) \
        == _desktopMains(expected["initial"]["db"])
    assert [s["op"] for s in record["steps"]] \
        == [s["op"] for s in expected["steps"]]
    for index, (actual, wanted) in enumerate(
            zip(record["steps"], expected["steps"])):
        _assertServiceStep(scenario["name"], index, actual, wanted,
                           scenario["board"])


def test_a_load_leaves_its_data_and_the_database_record_alone(
        recordedCatalogs, tmp_path, monkeypatch):
    """The card's SetData censored the data it was handed in place -- the
    caller's dict, and the database's own record when that was what it
    loaded (the PLAYERS editor hands over the record itself). The service
    censors copies: what reaches the slot is censored, what it was given is
    not touched."""
    from src.core.BadWordFilter import BadWordFilter
    from src.core.PlayerDB import PlayerDB

    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS["tgame"]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    db = PlayerDB(path=str(tmp_path / "local_players.json"),
                  legacyCsvPath=str(tmp_path / "local_players.csv"))
    db.database = {"BAD": {"gamerTag": "BAD", "name": "so nasty",
                           "mains": {"tgame": [["Mario", 1]]}}}
    monkeypatch.setattr(PlayerDB, "instance", db)
    ngword = tmp_path / "ngword"
    ngword.mkdir()
    (ngword / "common.txt").write_text("nasty", encoding="utf-16")
    badWords = BadWordFilter(ngwordDir=str(ngword),
                             userDataDir=str(tmp_path / "user_data"))
    badWords.Initialize()
    monkeypatch.setattr(BadWordFilter, "instance", badWords)

    service = PlayerService(BOARD_PATH, games=games, countries=countries)
    service.SeedEmpty(3)
    data = {"gamerTag": "BAD", "twitter": "nasty one"}
    given = copy.deepcopy(data)
    stored = copy.deepcopy(db.database["BAD"])
    service.ApplyData(data)

    assert data == given
    assert db.database["BAD"] == stored
    assert "nasty" not in SM.Get(f"{BOARD_PATH}.{schema.PLAYER_REAL_NAME_KEY}")
    assert "nasty" not in SM.Get(f"{BOARD_PATH}.{schema.PLAYER_TWITTER_KEY}")
    assert SM.Get(f"{BOARD_PATH}.character")[1]["en_name"] == "Mario"


# ── what a load no longer keeps or raises (S3e-3 review) ─────────────────

@pytest.fixture
def loadWorld(recordedCatalogs, tmp_path, monkeypatch):
    """`service(path)` over the recorded catalogs, a seeded board slot at
    BOARD_PATH, an empty player database (`db`) and a filter of no words."""
    from types import SimpleNamespace

    from src.core.BadWordFilter import BadWordFilter
    from src.core.PlayerDB import PlayerDB

    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS["tgame"]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    db = PlayerDB(path=str(tmp_path / "local_players.json"),
                  legacyCsvPath=str(tmp_path / "local_players.csv"))
    monkeypatch.setattr(PlayerDB, "instance", db)
    ngword = tmp_path / "ngword"
    ngword.mkdir()
    badWords = BadWordFilter(ngwordDir=str(ngword),
                             userDataDir=str(tmp_path / "user_data"))
    badWords.Initialize()
    monkeypatch.setattr(BadWordFilter, "instance", badWords)

    def service(path=BOARD_PATH):
        return PlayerService(path, games=games, countries=countries)

    service().SeedEmpty(3)
    return SimpleNamespace(service=service, db=db)


@pytest.fixture
def warnings():
    from loguru import logger

    messages = []
    handler = logger.add(lambda message: messages.append(str(message)),
                         level="WARNING", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(handler)


def test_a_load_without_an_id_or_a_city_leaves_neither_behind(loadWorld):
    """The clear a load starts with kept the previous player's id and city
    (the card ran it with its name handlers held back), so a player loaded
    without them -- a tag from the local database, a set's entrant without
    a start.gg account -- carried the one before's: the statistics, keyed
    by the id, kept showing the previous player under the new name. The
    clear empties both now, and the id moving asks for the statistics."""
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})

    change = service.ApplyData({"gamerTag": "SECOND"})

    assert SM.Get(f"{BOARD_PATH}.id") is None
    assert SM.Get(f"{BOARD_PATH}.city") is None
    assert change.idChanged


def test_a_load_that_brings_its_id_and_city_writes_each_once(loadWorld):
    """Emptied only when the load does not bring them: one that does writes
    its own over the previous player's, never None first -- and the same
    player loaded again writes neither (the guard the statistics need)."""
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})

    with applyScenarios.Writes(BOARD_PATH) as writes:
        change = service.ApplyData(
            {"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})
    assert [key for key, _value in writes.calls
            if key in ("id", "city")] == []
    assert not change.idChanged

    with applyScenarios.Writes(BOARD_PATH) as writes:
        change = service.ApplyData(
            {"gamerTag": "OTHER", "id": 202, "city": "Osaka"})
    assert [key for key, _value in writes.calls
            if key in ("id", "city")] == ["id", "city"]
    assert (SM.Get(f"{BOARD_PATH}.id"), SM.Get(f"{BOARD_PATH}.city")) \
        == (202, "Osaka")
    assert change.idChanged


def test_an_update_without_a_clear_keeps_the_id_and_the_city(loadWorld):
    """A load without the clear (the Web's player edit) changes the fields
    it brings: the slot is still the same player."""
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})

    change = service.ApplyData({"twitter": "tw"}, clear=False)

    assert (SM.Get(f"{BOARD_PATH}.id"), SM.Get(f"{BOARD_PATH}.city")) \
        == (101, "Kyoto")
    assert not change.idChanged


# ── an update that names another player (S3e-4) ──────────────────────────
#
# The Web UI commits every field of its player card as an update without
# the clear (PATCH .../players/{p}), and never sends the id: writing another
# player's name over the slot kept the previous player's id and city, and
# the statistics -- keyed by the id -- went on showing the previous player
# under the new name. The rule is the desktop card's (NameChanged): the
# sponsor or the tag changing is another player.

def _identity():
    return (SM.Get(f"{BOARD_PATH}.id"), SM.Get(f"{BOARD_PATH}.city"))


def test_an_update_that_names_another_player_leaves_no_id_or_city(
        loadWorld):
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "prefix": "SP", "id": 101,
                       "city": "Kyoto"})

    # What the Web UI sends: the whole card, the new tag in it, no id.
    change = service.ApplyData({
        "gamerTag": "SECOND", "prefix": "SP", "twitter": "tw",
        "country_code": "JP"}, clear=False)

    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_TAG_KEY}") == "SECOND"
    assert _identity() == (None, None)
    assert change.idChanged


def test_another_sponsor_is_another_player(loadWorld):
    """The sponsor and the tag together name a player -- the local
    database's key (PlayerDB.PlayerTag), and what the desktop card compares
    before it drops the id (NameChanged)."""
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "SAME", "prefix": "OLD", "id": 101,
                       "city": "Kyoto"})

    change = service.ApplyData({"gamerTag": "SAME", "prefix": "NEW"},
                               clear=False)

    assert _identity() == (None, None)
    assert change.idChanged


@pytest.mark.parametrize("data", [
    {"gamerTag": "FIRST", "prefix": "SP", "twitter": "edited"},
    {"gamerTag": "FIRST"},
    {"prefix": "SP"},
    # Blank fields are not carried (a blank value leaves the current one).
    {"gamerTag": "", "prefix": "", "name": "Real"},
    {"mains": {"tgame": [["Luigi", 0]]}},
], ids=["the whole card", "the tag again", "the sponsor again", "blank names",
        "characters only"])
def test_an_update_of_the_same_player_keeps_the_id_and_the_city(
        loadWorld, data):
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "prefix": "SP", "id": 101,
                       "city": "Kyoto"})

    with applyScenarios.Writes(BOARD_PATH) as writes:
        change = service.ApplyData(data, clear=False)

    assert _identity() == (101, "Kyoto")
    assert [key for key, _value in writes.calls
            if key in ("id", "city")] == []
    assert not change.idChanged


def test_an_update_that_names_another_player_with_an_id_writes_it_once(
        loadWorld):
    """What the update brings is written over the previous player's, never
    None first; what it does not bring is emptied."""
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})

    with applyScenarios.Writes(BOARD_PATH) as writes:
        change = service.ApplyData({"gamerTag": "SECOND", "id": 202},
                                   clear=False)

    assert [call for call in writes.calls if call[0] in ("id", "city")] \
        == [["id", "202"], ["city", "null"]]
    assert _identity() == (202, None)
    assert change.idChanged


def test_an_update_that_names_a_player_the_database_knows_takes_theirs(
        loadWorld):
    """The database's record of the new player (a tournament's entrant
    carries a start.gg id) is the player's own: kept, not emptied."""
    loadWorld.db.database = {"SECOND": {"gamerTag": "SECOND", "id": 202,
                                        "city": "Osaka"}}
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "FIRST", "id": 101, "city": "Kyoto"})

    change = service.ApplyData({"gamerTag": "SECOND"}, loadFromDB=True,
                               clear=False)

    assert _identity() == (202, "Osaka")
    assert change.idChanged


# The online avatar and the seed stay (S3e-4 review). The Web UI sends the
# whole card, its name included, so a typo fixed in the tag or a sponsor
# added is "another player" too -- and LIVE-BUG-1 is that fixing a typo must
# not cost the player a face. Only the id and the city are emptied, as the
# desktop card's NameChanged does: both terminals end the same. Widening it
# on both is the owner's call (docs/changes/architecture-modernization.md).

OTHER_AVATAR = "https://cdn.example.invalid/avatar/def456"


def _previousPlayer(service):
    service.ApplyData({"gamerTag": "FRIST", "id": 101, "city": "Kyoto",
                       "seed": 5, "avatar": AVATAR})


def _ownFields():
    return {key: SM.Get(f"{BOARD_PATH}.{key}")
            for key in ("id", "city", "online_avatar", "seed")}


@pytest.mark.parametrize("data", [
    # What the Web UI sends: the whole card, the edited name in it.
    {"gamerTag": "FRIST", "prefix": "SP", "name": "", "twitter": "",
     "country_code": "", "state_code": ""},
    {"gamerTag": "FIRST", "prefix": "", "name": "", "twitter": "",
     "country_code": "", "state_code": ""},
], ids=["a sponsor added", "a typo fixed in the tag"])
def test_an_update_that_names_another_player_keeps_the_face_and_the_seed(
        loadWorld, data):
    service = loadWorld.service()
    _previousPlayer(service)

    with applyScenarios.Writes(BOARD_PATH) as writes:
        change = service.ApplyData(data, clear=False)

    assert _ownFields() == {"id": None, "city": None,
                            "online_avatar": AVATAR, "seed": 5}
    assert [call for call in writes.calls
            if call[0] in ("online_avatar", "seed")] == []
    assert change.idChanged and not change.seedChanged


def _failAtTheAvatar(monkeypatch):
    """The avatar step (after the name, before the id) raises."""
    publish = PlayerService.PublishImages

    def failing(self, sponsor, tag, onlineAvatar=KEEP_ONLINE_AVATAR):
        if onlineAvatar is not KEEP_ONLINE_AVATAR:
            raise RuntimeError("the avatar step")
        return publish(self, sponsor, tag)

    monkeypatch.setattr(PlayerService, "PublishImages", failing)


@pytest.mark.parametrize("clear", [True, False], ids=["load", "update"])
def test_a_load_that_raises_before_the_id_leaves_no_previous_id(
        loadWorld, monkeypatch, clear):
    """The new name is written and a later step raises before the id it
    carries: the previous player's id and city must not stay under the new
    name. What the load wrote decides it, not what it carries (S3e-4
    review) -- the clear leaves the id it carries for the load to write
    over. The face and the seed are the clear's to empty (a load), and an
    update's to keep."""
    service = loadWorld.service()
    _previousPlayer(service)
    _failAtTheAvatar(monkeypatch)

    with pytest.raises(RuntimeError):
        service.ApplyData({"gamerTag": "SECOND", "prefix": "SP",
                           "avatar": OTHER_AVATAR, "id": 202,
                           "city": "Osaka", "seed": 3}, clear=clear)

    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_TAG_KEY}") == "SECOND"
    assert SM.Get(f"{BOARD_PATH}.mergedName") == "SP | SECOND"
    assert _ownFields() == {
        "id": None, "city": None,
        "online_avatar": None if clear else AVATAR, "seed": 0 if clear else 5}


def _failToEmptyTheId(monkeypatch):
    setId = PlayerService.SetPlayerId

    def failing(self, value):
        if value is None:
            raise ValueError("emptying the id")
        return setId(self, value)

    monkeypatch.setattr(PlayerService, "SetPlayerId", failing)


def test_a_failure_to_empty_the_id_does_not_hide_the_loads_own(
        loadWorld, monkeypatch, warnings):
    """Emptying the id runs in the load's `finally`: raising there would
    put its exception in place of the step's own. The step's propagates,
    the other is logged, and the name is still published (S3e-4 review)."""
    service = loadWorld.service()
    _previousPlayer(service)
    _failAtTheAvatar(monkeypatch)
    _failToEmptyTheId(monkeypatch)

    with pytest.raises(RuntimeError, match="the avatar step"):
        service.ApplyData({"gamerTag": "SECOND", "avatar": OTHER_AVATAR},
                          clear=False)

    assert SM.Get(f"{BOARD_PATH}.mergedName") == "SECOND"
    assert any("not emptied" in message for message in warnings)


def test_a_failure_to_empty_the_id_alone_is_raised(loadWorld, monkeypatch):
    """With no step failing, emptying the id failing is the load's error --
    raised after the name is published."""
    service = loadWorld.service()
    _previousPlayer(service)
    _failToEmptyTheId(monkeypatch)

    with pytest.raises(ValueError, match="emptying the id"):
        service.ApplyData({"gamerTag": "SECOND"}, clear=False)

    assert SM.Get(f"{BOARD_PATH}.mergedName") == "SECOND"


def _failToTellWhatChanged(monkeypatch):
    def failing(self, identity):
        raise ValueError("what changed")

    monkeypatch.setattr(PlayerService, "ChangeSince", failing)


def test_a_failure_to_tell_what_changed_does_not_hide_the_loads_own(
        loadWorld, monkeypatch, warnings):
    """After a step raised, the load's `finally` works out what changed (to
    ask for the statistics, Announce): raising there would put its
    exception in place of the step's own. The step's propagates, the other
    is logged -- as emptying the id (S3e-5 review)."""
    service = loadWorld.service()
    _previousPlayer(service)
    _failAtTheAvatar(monkeypatch)
    _failToTellWhatChanged(monkeypatch)

    with pytest.raises(RuntimeError, match="the avatar step"):
        service.ApplyData({"gamerTag": "SECOND", "avatar": OTHER_AVATAR},
                          clear=False)

    assert SM.Get(f"{BOARD_PATH}.mergedName") == "SECOND"
    assert any("statistics were not asked for" in message
               for message in warnings)


def test_a_failure_to_tell_what_changed_alone_is_raised(loadWorld,
                                                        monkeypatch):
    service = loadWorld.service()
    _previousPlayer(service)
    _failToTellWhatChanged(monkeypatch)

    with pytest.raises(ValueError, match="what changed"):
        service.ApplyData({"gamerTag": "SECOND"}, clear=False)


# ── the database saves' order (S3e-4 review) ─────────────────────────────
#
# The Web's edits write the database after their board host let go
# (AutonaWebOperations._save_record), so two saves of one player can reach
# it in the other order. Each record is numbered as its slot is read; one
# read before the record saved last under its key is dropped.

def _databaseFile(db):
    """{database key: player} of the database's file."""
    from src.core.PlayerDB import PlayerDB
    return {PlayerDB.PlayerTag(player): player
            for player in json.loads(Path(db.path).read_text("utf-8"))}


def test_a_save_read_before_the_one_saved_last_is_dropped(loadWorld):
    service = loadWorld.service()
    other = loadWorld.service("score.1.team.2.player.1")
    other.SeedEmpty(3)
    service.ApplyData({"gamerTag": "TAG", "name": "Old"})
    other.ApplyData({"gamerTag": "OTHER", "name": "Other"})
    older = service.PrepareSaveToDB()
    otherOlder = other.PrepareSaveToDB()
    service.ApplyData({"name": "New"}, clear=False)
    newer = service.PrepareSaveToDB()
    assert older.sequence < otherOlder.sequence < newer.sequence

    # The newer read reaches the database first.
    assert PlayerService.SaveRecordToDB(newer) is True
    assert PlayerService.SaveRecordToDB(older) is False
    # Another player's key is its own.
    assert PlayerService.SaveRecordToDB(otherOlder) is True

    assert loadWorld.db.Get("TAG")["name"] == "New"
    saved = _databaseFile(loadWorld.db)
    assert (saved["TAG"]["name"], saved["OTHER"]["name"]) == ("New", "Other")


def test_saves_in_the_order_they_were_read_are_all_written(loadWorld):
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "TAG", "name": "First"})
    service.SavePlayerToDB()
    assert loadWorld.db.Get("TAG")["name"] == "First"

    service.ApplyData({"name": "Second"}, clear=False)
    first = service.PrepareSaveToDB()
    service.ApplyData({"name": "Third"}, clear=False)
    second = service.PrepareSaveToDB()
    assert PlayerService.SaveRecordToDB(first) is True
    assert PlayerService.SaveRecordToDB(second) is True

    assert _databaseFile(loadWorld.db)["TAG"]["name"] == "Third"


def test_an_update_whose_tag_is_censored_to_the_name_shown_is_the_same_player(
        loadWorld, tmp_path, monkeypatch):
    """The slot holds the censored tag; the Web UI sends back the tag as
    the operator typed it. Compared as written, it is the same name."""
    from src.core.BadWordFilter import BadWordFilter

    ngword = tmp_path / "words"
    ngword.mkdir()
    (ngword / "common.txt").write_text("nasty", encoding="utf-16")
    badWords = BadWordFilter(ngwordDir=str(ngword),
                             userDataDir=str(tmp_path / "words_user_data"))
    badWords.Initialize()
    monkeypatch.setattr(BadWordFilter, "instance", badWords)
    service = loadWorld.service()
    service.ApplyData({"gamerTag": "nasty", "id": 101, "city": "Kyoto"})
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_TAG_KEY}") != "nasty"

    change = service.ApplyData({"gamerTag": "nasty", "twitter": "tw"},
                               clear=False)

    assert _identity() == (101, "Kyoto")
    assert not change.idChanged


def test_a_sponsor_without_a_tag_loads_without_the_database(loadWorld):
    """The database is keyed by sponsor + tag, and a sponsor alone named no
    record: the lookup raised (PlayerDB.PlayerTag), after the clear, and the
    sponsor was never written. Without a tag there is nothing to look up."""
    loadWorld.db.database = {"ONLY": {"gamerTag": "ONLY", "name": "Wrong"}}
    service = loadWorld.service()

    service.ApplyData({"prefix": "ONLY"}, loadFromDB=True)

    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_SPONSOR_KEY}") == "ONLY"
    assert SM.Get(f"{BOARD_PATH}.{schema.PLAYER_REAL_NAME_KEY}") != "Wrong"


@pytest.mark.parametrize("skin", ["x", None, ""])
def test_a_main_whose_skin_is_not_a_number_is_its_first_skin(
        loadWorld, warnings, skin):
    """A `mains` entry's skin is a row number. One that is not raised
    (ValueError, the characters unwritten) -- in the data handed over, and
    on a headless shell in a database record too, which the desktop's
    completer model rewrites to 0 before anything reads it
    (AutonaPlayerDB._BuildModel). The first skin, as that rewrite, with a
    warning: both shells load the same characters."""
    loaded = loadWorld.service()
    reference = loadWorld.service("score.1.team.2.player.1")
    reference.SeedEmpty(3)
    reference.ApplyData({"gamerTag": "REF", "mains": {"tgame": [
        ["Mario", 0], ["Luigi", 0, "EX"]]}})

    loaded.ApplyData({"gamerTag": "K", "mains": {"tgame": [
        ["Mario", skin], ["Luigi", skin, "EX"]]}})

    assert SM.Get(f"{BOARD_PATH}.character") \
        == SM.Get("score.1.team.2.player.1.character")
    assert SM.Get(f"{BOARD_PATH}.character")[1]["en_name"] == "Mario"
    assert any("skin" in message for message in warnings)


def test_a_database_record_whose_skin_is_not_a_number_loads_too(loadWorld):
    loadWorld.db.database = {"REC": {"gamerTag": "REC", "mains": {
        "tgame": [["Luigi", "x"]]}}}
    service = loadWorld.service()

    service.ApplyData({"gamerTag": "REC"}, loadFromDB=True)

    value = SM.Get(f"{BOARD_PATH}.character")[1]
    assert (value["en_name"], value["skin"]) == ("Luigi", 0)
