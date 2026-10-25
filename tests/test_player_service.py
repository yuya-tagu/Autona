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


# ── the slot's lifetime ──────────────────────────────────────────────────

def test_unset_drops_the_whole_slot(player):
    player.SetText(schema.PLAYER_TAG_KEY, "TAG")
    player.PublishMergedName("", "TAG")
    assert SM.Get(BOARD_PATH) is not None

    player.Unset()

    assert SM.Get(BOARD_PATH) is None, (
        "a torn-down player widget left its name on the overlay")


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
