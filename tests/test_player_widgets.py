"""Player widgets against the real thing.

A player widget is what turns "a set was loaded" into the tags, sponsors and
character art an overlay renders. AutonaScoreboardPlayerWidget is now the only
implementation: every board builds it, including board 2 ("team") and board 3
("crew") -- see test_the_team_board_uses_the_scoreboard_player_widget below,
which pins that so this file's premise cannot rot silently.

These assertions used to run against a second implementation as well
(AutonaTeamPlayerWidget, the duplicated half of agent-pitfalls #5). It and its
sole caller AutonaTeamBattleWidget were dead code -- the caller had been
commented out in src/Autona.py -- and were deleted in the Headless Core
redesign Step 0 (2026-08-28), which is why nothing here is parametrized over
an implementation any more.

Assertions read StateManager, not the widgets: state is what the OBS overlays
and the Web UI consume, and a combo box showing the right character while the
published value stayed behind is exactly the failure worth catching.
"""
import gc
from unittest.mock import patch

import pytest

from qtpy.QtCore import Qt
from qtpy.QtGui import QStandardItem, QStandardItemModel
from qtpy.QtWidgets import QComboBox, QLineEdit, QSpinBox

from src.core.CountryCatalog import CountryCatalog
from src.core.PlayerService import CharacterSlot
from src.core.StateManager import StateManager


def _players(app):
    """One live player widget per team, off the real 1v1 board.

    The synthetic character roster is loaded first: with no game installed the
    character model is empty and every "which character ended up in slot N"
    assertion would be vacuously true.
    """
    app.loadCharacters()
    board = app.board(1)
    return board.team1playerWidgets[0], board.team2playerWidgets[0]


def _player(app):
    return _players(app)[0]


def _characters(app, player):
    return app.get(f"{player.path}.character") or {}


def _names(app, player):
    """Published character name per slot, keyed by slot number."""
    return {slot: data.get("name", "")
            for slot, data in _characters(app, player).items()}


# ── SetData: the fields an overlay reads ─────────────────────────────────

def test_set_data_publishes_the_tag_and_the_sponsor(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    # The keys are the QLineEdit objectNames ("name" / "team"), which is the
    # contract the overlays are written against (agent-pitfalls #4). Note
    # that the *input* field is "gamerTag" and the *published* key is "name".
    assert autonaApp.get(f"{player.path}.name") == "TAG"
    assert autonaApp.get(f"{player.path}.team") == "SPONSOR"


def test_set_data_merges_the_sponsor_into_the_display_name(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.mergedName") == "SPONSOR | TAG"
    # The "name only" variant is what the compact overlays use; it must never
    # pick the sponsor up.
    assert autonaApp.get(f"{player.path}.mergedOnlyName") == "TAG"


def test_set_data_without_a_sponsor_publishes_the_bare_tag(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG"}, dontLoadFromDB=True)
    autonaApp.settle()

    # No separator when there is nothing to separate.
    assert autonaApp.get(f"{player.path}.mergedName") == "TAG"


def test_set_data_resolves_a_free_form_state_name(autonaApp):
    """The state combo matches on the region's iso2 ("13"), but a
    state_code saved by an older build -- or auto-filled from Start.gg
    before this existed -- is a free-form name ("Tokyo"). SetData falls back
    to CountryCatalog.FindState for exactly that case, so the region
    reaches the overlay instead of staying blank.
    """
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "Tokyo"}, dontLoadFromDB=True)
    autonaApp.settle()

    published = autonaApp.get(f"{player.path}.state")
    assert published, "state_code did not reach state at all"
    assert published.get("original_code") == "13"


def test_set_data_takes_a_state_iso2_as_is(autonaApp):
    """The normal path: Start.gg already gave us the iso2, so the direct
    match wins and FindState is never consulted (its first call parses the
    pre-computed table, and this runs on the GUI thread under dataLock)."""
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.state").get("original_code") == "13"


def test_loading_a_player_without_a_country_drops_the_previous_one(
        autonaApp):
    """SetData's Clear() empties the country combo with its signals
    blocked, so a player loaded without a country_code used to keep the
    previous player's flag on the overlay (found while recording the
    ADR-005 S1d golden). The emptying is announced now, in SetData's own
    save cycle: no region, then no country."""
    player = _player(autonaApp)

    player.SetData({"gamerTag": "FIRST", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.country").get("code") == "JP"
    assert autonaApp.get(f"{player.path}.state").get("code") == "13"

    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.country") == {}
    assert autonaApp.get(f"{player.path}.state") == {}

    # A third player with a country is not disturbed by the fix.
    player.SetData({"gamerTag": "THIRD", "country_code": "US",
                    "state_code": "CA"}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.country").get("code") == "US"
    assert autonaApp.get(f"{player.path}.state").get("code") == "CA"


def test_loading_a_second_player_replaces_the_first(autonaApp):
    """SetData(clear=True) is how a set load overwrites the previous match."""
    player = _player(autonaApp)

    player.SetData({"gamerTag": "FIRST", "prefix": "OLD"}, dontLoadFromDB=True)
    autonaApp.settle()
    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == "SECOND"
    # The sponsor of the player who is no longer on the board must not stay.
    assert autonaApp.get(f"{player.path}.team") == ""
    assert autonaApp.get(f"{player.path}.mergedName") == "SECOND"


def test_clear_empties_the_published_player_fields(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    player.Clear()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == ""
    assert autonaApp.get(f"{player.path}.team") == ""


def test_the_losers_flag_is_appended_to_the_merged_name(autonaApp):
    """A name the card publishes carries its team's flag from the state.

    The card has no copy of the flag since ADR-005 S2b (it had `losers`,
    kept in step by a per-card SetLosers); PlayerService reads
    `team.{t}.losers` when the card publishes.
    """
    from src.core.Actions import AutonaActions

    player = _player(autonaApp)
    AutonaActions.instance.SetTeamLosers(1, player.teamNumber, True)
    try:
        player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                       dontLoadFromDB=True)
        autonaApp.settle()

        assert not hasattr(player, "losers")
        assert autonaApp.get(
            f"{player.path}.mergedName") == "SPONSOR | TAG [L]"
        assert autonaApp.get(f"{player.path}.mergedOnlyName") == "TAG [L]"
    finally:
        AutonaActions.instance.SetTeamLosers(1, player.teamNumber, False)
        autonaApp.settle()


# ── character slots ──────────────────────────────────────────────────────

def test_character_slots_follow_the_requested_count(autonaApp):
    player = _player(autonaApp)

    player.SetCharactersPerPlayer(3)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, player)) == [1, 2, 3]

    # Shrinking has to remove the published slots too, not just the combos:
    # a stale score.N...character.3 keeps a third character on the overlay.
    player.SetCharactersPerPlayer(1)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, player)) == [1]


def test_the_slot_count_change_republishes_mains(autonaApp):
    """mains is only rewritten on a slot rebuild, so it must track the count.

    Overlays that render "this player's mains" next to the live pick read
    score.N...mains; if it keeps the old slot count they disagree.
    """
    player = _player(autonaApp)

    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    assert sorted(autonaApp.get(f"{player.path}.mains") or {}) == [1, 2]


def test_mains_for_the_selected_game_fill_their_own_slots(autonaApp):
    player = _player(autonaApp)
    player.SetCharactersPerPlayer(3)
    autonaApp.settle()

    player.SetData({
        "gamerTag": "TAG",
        "mains": {"harness_game": [["Mario", 1], ["Luigi", 0]]},
    }, dontLoadFromDB=True)
    autonaApp.settle()

    names = _names(autonaApp, player)
    assert names[1] == "Mario"
    assert names[2] == "Luigi"
    # Two mains must not bleed into a third slot.
    assert names[3] == ""
    # The skin index travels with the character, not just the name.
    assert _characters(autonaApp, player)[1]["skin"] == 1
    assert _characters(autonaApp, player)[2]["skin"] == 0


def test_mains_from_another_game_are_ignored(autonaApp):
    """mains is keyed by game codename; a different game's entry is not ours."""
    player = _player(autonaApp)
    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    player.SetData({"gamerTag": "TAG", "mains": {"some_other_game": [["Mario", 0]]}},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert _names(autonaApp, player) == {1: "", 2: ""}


def test_a_legacy_single_character_main_only_fills_the_first_slot(autonaApp):
    """The shipped "one-character player pollutes every slot" regression.

    The old single-character mains format is a flat ["Name", skin] list.
    It describes ONE character, so it belongs in slot 1 and nowhere else.

    (This was BUG-7's other half: AutonaTeamPlayerWidget kept the pre-fix
    loop that assigned the single character to every slot, tracked here as a
    strict xfail. That widget was dead code and is gone as of 2026-08-28, so
    only the fixed implementation is left to assert against.)
    """
    player = _player(autonaApp)
    player.SetCharactersPerPlayer(3)
    autonaApp.settle()

    player.SetData({"gamerTag": "TAG", "mains": ["Mario", 1]},
                   dontLoadFromDB=True)
    autonaApp.settle()

    names = _names(autonaApp, player)
    assert names[1] == "Mario"
    assert names[2] == ""
    assert names[3] == ""


def test_loading_a_player_without_mains_clears_the_previous_characters(
        autonaApp):
    player = _player(autonaApp)
    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    player.SetData({"gamerTag": "FIRST",
                    "mains": {"harness_game": [["Mario", 0], ["Luigi", 0]]}},
                   dontLoadFromDB=True)
    autonaApp.settle()
    assert _names(autonaApp, player)[1] == "Mario"

    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    # The next player inherits nothing: SetData's Clear() has to reach the
    # character combos, otherwise the overlay shows the previous entrant's
    # picks under the new name.
    assert _names(autonaApp, player) == {1: "", 2: ""}


def test_a_slot_hands_over_identifiers_and_a_name_to_fall_back_on(autonaApp):
    """What CharactersChanged hands PlayerService for one slot (ADR-005 S1d).

    The fallback name only matters when the game catalog has no row for the
    pick. For a typed name that is the typed text. For a picked row it is
    the row's own name: that happens between a game load and the combos'
    rebind, and the combo's text there is its "display name / en_name"
    label, which no overlay ever showed as a name.
    """
    from src.AutonaBasePlayerWidget import AutonaBasePlayerWidget

    def combo(rows):
        box = QComboBox()
        box.setEditable(True)
        model = QStandardItemModel(box)
        for text, data in rows:
            item = QStandardItem(text)
            item.setData(data, Qt.ItemDataRole.UserRole)
            model.appendRow(item)
        box.setModel(model)
        return box

    character = combo([("", None), ("マリオ / Mario",
                                    {"en_name": "Mario", "name": "マリオ"})])
    color = combo([("0", {"assets": {}}), ("1", {"assets": {}})])
    variant = combo([("", {}), ("EX", {"en_name": "EX", "name": "EX"})])

    character.setCurrentIndex(1)
    color.setCurrentIndex(1)
    variant.setCurrentIndex(1)
    assert AutonaBasePlayerWidget._CharacterSlot(character, color, variant) \
        == CharacterSlot("Mario", 1, "EX", "マリオ")

    character.setCurrentIndex(0)
    character.setEditText("Typed Name")
    variant.setCurrentIndex(0)
    assert AutonaBasePlayerWidget._CharacterSlot(character, color, variant) \
        == CharacterSlot(None, 1, None, "Typed Name")


def test_swapping_two_character_slots_exchanges_the_published_names(
        autonaApp):
    player = _player(autonaApp)
    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    player.SetData({"gamerTag": "TAG",
                    "mains": {"harness_game": [["Mario", 0], ["Luigi", 0]]}},
                   dontLoadFromDB=True)
    autonaApp.settle()

    player.SwapCharacters(0, 1)
    autonaApp.settle()

    assert _names(autonaApp, player) == {1: "Luigi", 2: "Mario"}


# ── swapping two players ─────────────────────────────────────────────────
#
# A swap exchanges the two slots in the state (AutonaActions.SwapTeams,
# ADR-005 S2c) and each card renders its own; it used to be a copy from one
# card into the other (SwapWith). `left` / `right` are slot 1 of the two
# teams of board 1, i.e. exactly the pair a team swap exchanges.

def _swap(app):
    from src.core.Actions import AutonaActions
    assert AutonaActions.instance.SwapTeams(1) is True
    app.settle()


def test_a_swap_exchanges_the_published_names(autonaApp):
    left, right = _players(autonaApp)

    left.SetData({"gamerTag": "LEFT", "prefix": "LS"}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "RIGHT", "prefix": "RS"}, dontLoadFromDB=True)
    autonaApp.settle()

    _swap(autonaApp)

    assert autonaApp.get(f"{left.path}.name") == "RIGHT"
    assert autonaApp.get(f"{left.path}.team") == "RS"
    assert autonaApp.get(f"{right.path}.name") == "LEFT"
    assert autonaApp.get(f"{right.path}.team") == "LS"
    assert autonaApp.get(f"{left.path}.mergedName") == "RS | RIGHT"
    assert autonaApp.get(f"{right.path}.mergedName") == "LS | LEFT"


def test_moving_the_top_player_up_is_a_no_op(autonaApp):
    # The move-up / move-down buttons at the ends of a team.
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()
    before = autonaApp.get(player.path)

    player.btMoveUp.click()
    player.btMoveDown.click()
    autonaApp.settle()

    assert autonaApp.get(player.path) == before


# ── seed ─────────────────────────────────────────────────────────────────
#
# The seed reaches the overlay through state and the operator through the
# seedDisplay badge; both are asserted, because the shipped bug was a
# mismatch between them.

def test_a_seed_reaches_the_state_and_the_badge(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "seed": 7}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 7
    assert player.seedDisplay.text() == "Seed: 7"
    assert not player.seedDisplay.isHidden()


def test_a_player_without_seed_info_clears_the_previous_seed(autonaApp):
    """The shipped "seed shown for an unseeded entrant" bug.

    Providers hand back None for an unseeded tournament (initialSeedNum is
    null). The old guard was `is not None`, so None skipped the whole branch
    and the previous entrant's seed stayed on the overlay under the new name.
    """
    player = _player(autonaApp)

    player.SetData({"gamerTag": "SEEDED", "seed": 7}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.seed") == 7

    player.SetData({"gamerTag": "UNSEEDED", "seed": None}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 0
    assert player.seedDisplay.text() == ""
    assert player.seedDisplay.isHidden()


def test_a_player_with_no_seed_key_clears_the_previous_seed(autonaApp):
    # Same failure through the other door: some provider paths omit the key
    # entirely rather than sending None.
    player = _player(autonaApp)

    player.SetData({"gamerTag": "SEEDED", "seed": 7}, dontLoadFromDB=True)
    autonaApp.settle()

    player.SetData({"gamerTag": "UNSEEDED"}, dontLoadFromDB=True)
    autonaApp.settle()

    # int(): SetData's own path writes 0, but the Clear() route publishes the
    # spinbox line edit's text ("0"). Both mean unseeded.
    assert int(autonaApp.get(f"{player.path}.seed")) == 0
    assert player.seedDisplay.isHidden()


def test_a_non_numeric_seed_is_treated_as_unseeded(autonaApp):
    # Providers also send seeds as strings; garbage must not raise inside a
    # set load (SetData holds a SaveBlock -- an exception escaping it is the
    # BUG-3 shape, where saving never resumes).
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "seed": "not a number"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 0
    assert autonaApp.state.saveBlocked == 0


def test_a_string_seed_is_published_as_a_number(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "seed": "12"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 12
    assert player.seedDisplay.text() == "Seed: 12"


def test_the_seed_survives_a_player_swap(autonaApp):
    left, right = _players(autonaApp)

    left.SetData({"gamerTag": "SEEDED", "seed": 3}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "UNSEEDED"}, dontLoadFromDB=True)
    autonaApp.settle()

    _swap(autonaApp)

    assert int(autonaApp.get(f"{left.path}.seed")) == 0
    assert autonaApp.get(f"{right.path}.seed") == 3
    # The badges follow too. The copy typed the seed into the spinbox with
    # the badge's update blocked, so the card that lost its seed kept
    # showing it.
    assert right.seedDisplay.text() == "Seed: 3"
    assert left.seedDisplay.text() == ""


# ── the boards the widgets actually live on ──────────────────────────────

@pytest.mark.parametrize("boardNumber", [1, 2])
def test_each_board_publishes_its_players_under_its_own_subtree(
        autonaApp, boardNumber):
    board = autonaApp.board(boardNumber)

    board.team1playerWidgets[0].SetData({"gamerTag": "HOME"},
                                        dontLoadFromDB=True)
    board.team2playerWidgets[0].SetData({"gamerTag": "AWAY"},
                                        dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(
        f"score.{boardNumber}.team.1.player.1.name") == "HOME"
    assert autonaApp.get(
        f"score.{boardNumber}.team.2.player.1.name") == "AWAY"
    # And nowhere else: the two boards share one StateManager.
    other = 2 if boardNumber == 1 else 1
    assert autonaApp.get(f"score.{other}.team.1.player.1.name") is None


def test_the_team_board_uses_the_scoreboard_player_widget(autonaApp):
    """Pins which class each board builds -- this file's premise.

    Older docs and the strategy note describe board 2 as the
    AutonaTeamPlayerWidget board; it never was. Board 2 is an
    AutonaScoreboardWidget, so both boards build AutonaScoreboardPlayerWidget
    -- which is why the assertions above can be written once, against the one
    class that ships. If a second player-widget implementation is ever wired
    back in, this fails and this file needs revisiting.
    """
    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget

    for number in (1, 2):
        board = autonaApp.board(number)
        assert isinstance(board.team1playerWidgets[0],
                          AutonaScoreboardPlayerWidget)


def test_the_player_fields_keep_their_state_key_object_names(autonaApp):
    """objectName IS the state key (agent-pitfalls #4).

    Renaming any of these in the .ui breaks every overlay and every saved
    program_state.json without a migration layer. tests/
    test_objectname_keypath_contract.py guards the board-level names; these
    are the per-player ones.
    """
    scoreboard, _ = _players(autonaApp)

    for name in ("name", "team", "twitter"):
        assert scoreboard.findChild(QLineEdit, name) is not None, (
            f"{type(scoreboard).__name__} lost the '{name}' field")

    assert scoreboard.findChild(QSpinBox, "seed") is not None


# ── the online avatar (LIVE-BUG-1) ───────────────────────────────────────
#
# The avatar start.gg hands out for an entrant is the one player field that
# is NOT derived from anything on the widget: it arrives in the set payload
# and only a caller that knows which player now occupies the slot may write
# it. ExportPlayerImages used to default that parameter to None, so both
# deferred refreshes -- SetData's own finally block and NameChanged --
# published None over the URL SetData had written moments earlier. The whole
# thing happened inside one BlockSaving, so nothing ever observed the URL:
# every set loaded from start.gg reached the overlays with no avatar, and
# so did every hand-edited tag. Found by tests/live against the real API.
#
# A URL with no image extension is used throughout: StateManager exports any
# state value that looks like an http .png/.jpg by *downloading* it, and a
# unit test must not reach the network. What is under test here is which
# state key survives, which the extension has no bearing on.

AVATAR = "https://cdn.example.invalid/avatar/abc123"
OTHER_AVATAR = "https://cdn.example.invalid/avatar/def456"


def test_set_data_publishes_the_online_avatar(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


def test_editing_the_tag_keeps_the_online_avatar(autonaApp):
    """The operator fixing a typo in a tag must not cost the player a face."""
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    name = player.findChild(QLineEdit, "name")
    name.setText("TAG_FIXED")
    name.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == "TAG_FIXED"
    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


def test_editing_the_sponsor_keeps_the_online_avatar(autonaApp):
    # The sponsor field runs the same handler, and it is the one that also
    # re-derives the sponsor images -- so it is the likeliest place for a
    # future "just re-export everything" shortcut to reintroduce the bug.
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    team = player.findChild(QLineEdit, "team")
    team.setText("SPONSOR")
    team.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


def test_loading_a_player_without_an_avatar_drops_the_previous_one(
        autonaApp):
    """The other half of the semantics: "keep" must not mean "never clears".

    An entrant with no start.gg account has no avatar, and the previous
    player's face staying on the overlay under the new name is the failure
    that made None the default in the first place.
    """
    player = _player(autonaApp)

    player.SetData({"gamerTag": "FIRST", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR

    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") is None


def test_loading_a_player_replaces_the_previous_avatar(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "FIRST", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    player.SetData({"gamerTag": "SECOND", "avatar": OTHER_AVATAR},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == OTHER_AVATAR


def test_clear_removes_the_online_avatar(autonaApp):
    player = _player(autonaApp)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    player.Clear()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") is None


def test_the_avatar_follows_a_player_swap(autonaApp):
    """Swapping the two sides of a set moves the faces with the tags.

    Before the fix both avatars were wiped instead, as a side effect of the
    same defaulting bug; with the parameter left alone they would stay on
    the widget and end up under the other player's name, which is worse.
    The swap moves the slot's `online_avatar` with the rest of it
    (AutonaActions.SwapTeams; the card copy had to restore it by hand).
    """
    left, right = _players(autonaApp)

    left.SetData({"gamerTag": "LEFT", "avatar": AVATAR}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "RIGHT"}, dontLoadFromDB=True)
    autonaApp.settle()

    _swap(autonaApp)

    assert autonaApp.get(f"{left.path}.online_avatar") is None
    assert autonaApp.get(f"{right.path}.online_avatar") == AVATAR


# ── The state combo's model outlives the rebuilds ────────────────────────
#
# Loading a set changes the player's country twice: Clear() empties it and
# SetData puts it back. LoadStates rebuilds the region list on each change,
# and it used to hand the combo a brand-new QStandardItemModel every time.
# Measured on this widget, every discarded model then stayed alive: ~50
# icon-bearing rows per player per loaded set, for the rest of the session.
#
# What kept them has not been pinned down -- a bare QComboBox in isolation
# does release the model it is handed, so this is not the general rule "a
# combo keeps every model it is given". These tests therefore assert the
# observable (nothing accumulates, the model object never changes) rather
# than a mechanism. Nothing failed while it leaked; the process just grew for
# as long as the stream ran, which is why it went unnoticed, and why only
# repetition makes it visible.

def _liveCounts():
    """How many models and how many rows are alive in the process.

    Both halves matter, and the model count alone is not enough: refilling one
    model through invisibleRootItem().appendRows() keeps the model count flat
    while every appended item stays registered on the root item's wrapper --
    same leak, one level down (agent-pitfalls #13b).
    """
    gc.collect()
    gc.collect()
    objects = gc.get_objects()
    return (len([o for o in objects if isinstance(o, QStandardItemModel)]),
            len([o for o in objects if isinstance(o, QStandardItem)]))


def _loadSet(player, index, country="JP", state="13"):
    """The player-side shape of a set load.

    The board clears every player first (CommandClearAll -> Clear(), which
    empties the country combo and rebuilds the regions for "no country") and
    then hands each one its new data, which rebuilds them again for the real
    country. That empty-then-refill pair is what stranded a model: going
    straight from one country to another does not, so a test that only
    switches countries passes against the bug.
    """
    player.Clear()
    player.SetData({"gamerTag": f"TAG{index}", "country_code": country,
                    "state_code": state}, dontLoadFromDB=True)


def _loadSets(player, count):
    for i in range(count):
        _loadSet(player, i)


def test_reloading_states_does_not_accumulate_models(autonaApp):
    """The pin: repeating the rebuild must not add live models or rows.

    Counted process-wide rather than per-widget, because the leak was not a
    dangling attribute anyone could find by inspection: nothing in the widget
    referred to the discarded models, and only their continued existence gave
    them away.
    """
    player = _player(autonaApp)

    # Warm up: the first two loads create the model and the country/state
    # tables, so the baseline is taken with everything already built.
    _loadSets(player, 2)
    autonaApp.settle()
    models, items = _liveCounts()

    _loadSets(player, 8)
    autonaApp.settle()
    modelsAfter, itemsAfter = _liveCounts()

    assert modelsAfter == models, (
        "a set load left a QStandardItemModel behind. LoadStates has to refill "
        "the state combo's existing model rather than hand the combo a new "
        "one: a replaced model is not reliably released here, so one per "
        "country change grows the process for the whole session.")
    assert itemsAfter == items, (
        f"a set load left {(itemsAfter - items) / 8:.0f} QStandardItems behind "
        "per load. The rows have to go into the model one at a time through "
        "model.appendRow(); invisibleRootItem().appendRows() keeps every item "
        "it appends registered on the root item, which clear() does not "
        "release (agent-pitfalls #13b).")


def test_the_state_combo_keeps_one_model_for_its_lifetime(autonaApp):
    """The mechanism: same model object before and after, and only one.

    This is what makes the count above stay flat, and it is the assertion that
    says *why* if the count ever moves.
    """
    player = _player(autonaApp)
    combo = player._w("state", QComboBox)

    _loadSets(player, 2)
    autonaApp.settle()
    first = combo.model()

    _loadSets(player, 6)
    autonaApp.settle()

    assert combo.model() is first, (
        "the state combo was handed a different model; it must be refilled "
        "in place")
    # Counted by identity: PySide6 registers the current model under more than
    # one key, so the same object legitimately shows up twice here. What must
    # never grow is the number of *distinct* models.
    held = {id(o) for o in gc.get_referents(combo)
            if isinstance(o, QStandardItemModel)}
    assert len(held) == 1, (
        f"the state combo is holding {len(held)} distinct models. A model it "
        "is handed is not reliably released, so setModel() may only be called "
        "once, when the combo is built.")


def test_a_card_without_a_character_keeps_one_empty_skin_model(autonaApp):
    """LoadSkinOptions handed the skin combo a fresh QStandardItemModel each
    time a card lost its character. Since the swap is a state operation
    (ADR-005 S2c), the card's render does that on every swap that brings it
    a slot with no character -- so the empty model is made once per card and
    handed back (agent-pitfalls #13: reuse rather than replace)."""
    left, right = _players(autonaApp)
    left.SetData({"gamerTag": "LEFT",
                  "mains": {"harness_game": [["Mario", 0]]}},
                 dontLoadFromDB=True)
    right.SetData({"gamerTag": "RIGHT"}, dontLoadFromDB=True)
    autonaApp.settle()
    # Warm up: both cards have now been without a character once.
    _swap(autonaApp)
    _swap(autonaApp)
    models, _ = _liveCounts()

    for _ in range(6):
        _swap(autonaApp)

    assert _liveCounts()[0] == models
    skins = right.character_elements[0][2]
    assert autonaApp.get(f"{right.path}.name") == "RIGHT"
    assert skins.model() is right._emptySkinModel


def test_refilled_states_still_resolve(autonaApp):
    """Refilling in place must leave a combo the region lookup can read.

    SetData walks stateElement.model().item(i) to find the region, so a
    rebuild that left the rows, the row count or the item payload behind
    would publish the wrong region -- or none -- while the model count
    stayed reassuringly flat.
    """
    player = _player(autonaApp)

    # Come from a different country first: this is the rebuild path, not the
    # initial fill.
    _loadSet(player, 0, country="US", state="CA")
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.state").get("original_code") == "CA"

    _loadSet(player, 1, country="JP", state="13")
    autonaApp.settle()

    combo = player._w("state", QComboBox)
    # Every region of the country, plus the blank row each rebuild starts with.
    expected = len(CountryCatalog.instance.States("JP")) + 1
    assert combo.model().rowCount() == expected
    assert autonaApp.get(f"{player.path}.state").get("original_code") == "13"


def test_a_rebuild_publishes_the_state_exactly_once(autonaApp):
    """The refill must look like the old model swap to everyone downstream.

    Attaching a finished model with setModel() produced exactly one index
    change per rebuild. Clearing and refilling a live model would produce one
    per row instead, so the combo is blocked for the refill and the single
    change is replayed by hand afterwards -- which is easy to drop, and just
    as easy to end up emitting twice. Both show up here: the published value
    is written once per rebuild, and it is {} while no country is selected.
    """
    player = _player(autonaApp)
    _loadSet(player, 0)
    autonaApp.settle()

    published = []
    original = StateManager.Set

    def recordingSet(key, value):
        if key == f"{player.path}.state":
            published.append(value)
        return original(key, value)

    with patch.object(StateManager, "Set", side_effect=recordingSet):
        player.Clear()
        autonaApp.settle()
        afterClear = list(published)

        published.clear()
        player.SetData({"gamerTag": "TAG", "country_code": "JP",
                        "state_code": "13"}, dontLoadFromDB=True)
        autonaApp.settle()
        duringLoad = list(published)

    assert afterClear == [{}], (
        f"emptying the country published the region {afterClear!r}; expected "
        "exactly one empty write. The refill either lost its replayed "
        "currentIndexChanged or emitted it more than once.")

    # Loading writes twice on purpose: the rebuild resets the combo to its
    # blank row, then the region the set actually carries is selected. It is
    # the first of those -- the replayed change -- that must happen once.
    assert duringLoad.count({}) == 1, (
        f"the rebuild's own publish happened {duringLoad.count({})} times "
        f"during one country change (writes: {duringLoad!r})")
    assert duringLoad[-1].get("original_code") == "13"


# ── the paths a player widget is built on (agent-pitfalls #7) ────────────
#
# `self.path` is a constructor argument, and everything the widget publishes
# hangs off it -- so "which path shapes exist" is the premise of every
# assertion above. agent-pitfalls #7 lists three; two of those callers are
# gone (the commentary panel builds its own cards, and the roster editor that
# used `{base}.slot.{I}.player.{M}` was retired), which the first test below
# is what keeps honest. The second drives the surviving non-board shape.

def test_the_player_widget_is_only_built_on_two_kinds_of_path():
    """Enumerate the construction sites rather than trust the pitfall note.

    A third shape appearing here is not a bug by itself -- it is the signal
    that PlayerService.BoardNumber(), the details dialog's 反映 button and
    the leak tests above all need revisiting for it.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src"
    sites = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "AutonaScoreboardPlayerWidget"):
                path = next((kw.value for kw in node.keywords
                             if kw.arg == "path"), None)
                sites.append((source.name, ast.dump(path) if path else None))

    files = sorted({name for name, _ in sites})
    assert files == [
        "AutonaPlayerBrowserWidget.py", "AutonaScoreboardWidget.py"], (
        f"a player widget is now built somewhere new: {files}")
    # Both scoreboard sites build an f-string keyed by board/team/index; the
    # browser passes the module-level editor constant.
    assert len(sites) == 3, f"construction sites changed: {sites}"


def test_the_editor_path_publishes_outside_every_board(autonaApp):
    """The PLAYERS panel's editor is the same widget on a scratch subtree.

    Its keys must land under `player_editor.1` and nowhere else: publishing
    an operator's draft onto a live scoreboard is the failure this path shape
    exists to avoid. Built directly rather than through the panel, which
    needs a Window.
    """
    from qtpy.QtCore import QCoreApplication, QEvent

    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
    from src.core import StateSchema as schema

    board = autonaApp.board(1)
    board.team1playerWidgets[0].SetData({"gamerTag": "LIVE"},
                                        dontLoadFromDB=True)
    autonaApp.settle()

    editorPath = f"{schema.PLAYER_EDITOR_ROOT}.1"
    editor = AutonaScoreboardPlayerWidget(index=1, teamNumber=1,
                                          path=editorPath)
    try:
        editor.SetData({"gamerTag": "DRAFT", "prefix": "SCRATCH"},
                       dontLoadFromDB=True)
        autonaApp.settle()

        assert autonaApp.get(f"{editorPath}.name") == "DRAFT"
        assert autonaApp.get(f"{editorPath}.mergedName") == "SCRATCH | DRAFT"
        # ...and the live board is untouched.
        assert autonaApp.get("score.1.team.1.player.1.name") == "LIVE"
    finally:
        editor.setParent(None)
        editor.deleteLater()
        # Actually reap it: pytest never runs an event loop, so a deferred
        # deletion would sit in the queue for the rest of the session with
        # its connections to the process-wide singletons intact (see
        # conftest._dropBoard).
        QCoreApplication.sendPostedEvents(editor, QEvent.Type.DeferredDelete)
        StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        autonaApp.settle()
