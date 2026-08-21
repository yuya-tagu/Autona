"""Player widgets against the real thing, both implementations at once.

A player widget is what turns "a set was loaded" into the tags, sponsors and
character art an overlay renders, and it exists twice:
AutonaScoreboardPlayerWidget and AutonaTeamPlayerWidget share no base beyond
AutonaBasePlayerWidget's helpers and reimplement SetData, the character slots
and the mains handling separately. That duplication is agent-pitfalls #5, and
"fixed one, forgot the other" is a shipped-bug pattern here -- so every
assertion that applies to both is parametrized over both rather than written
once against whichever one was handy.

Where the two genuinely differ (only the scoreboard widget carries a seed),
the test says so and covers the one that has the field.

Assertions read StateManager, not the widgets: state is what the OBS overlays
and the Web UI consume, and a combo box showing the right character while the
published value stayed behind is exactly the failure worth catching.

NOTE ON REACHABILITY: board 2 ("team") is an AutonaScoreboardWidget, so its
player widgets are AutonaScoreboardPlayerWidget just like board 1's.
AutonaTeamPlayerWidget's only caller, AutonaTeamBattleWidget, is commented out
in src/Autona.py -- see test_the_team_board_uses_the_scoreboard_player_widget
below, which pins that so this file's premise cannot rot silently.
"""
import pytest

from qtpy.QtWidgets import QLineEdit, QSpinBox

# Both parametrized values build a real widget; see _players().
KINDS = ["scoreboard", "team"]


def _players(app, kind):
    """One live player widget per team, of the requested implementation.

    "scoreboard" comes off the real 1v1 board. "team" is built standalone
    (autonaApp.teamPlayerWidget) because no board builds that class anymore;
    it still gets a production-shaped path, score.2.team.{T}.player.1.

    The synthetic character roster is loaded first either way: with no game
    installed the character model is empty and every "which character ended
    up in slot N" assertion would be vacuously true.
    """
    app.loadCharacters()
    if kind == "scoreboard":
        board = app.board(1)
        return board.team1playerWidgets[0], board.team2playerWidgets[0]
    return app.teamPlayerWidget(teamNumber=1), app.teamPlayerWidget(teamNumber=2)


def _player(app, kind):
    return _players(app, kind)[0]


def _characters(app, player):
    return app.get(f"{player.path}.character") or {}


def _names(app, player):
    """Published character name per slot, keyed by slot number."""
    return {slot: data.get("name", "")
            for slot, data in _characters(app, player).items()}


# ── SetData: the fields an overlay reads ─────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_set_data_publishes_the_tag_and_the_sponsor(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    # The keys are the QLineEdit objectNames ("name" / "team"), which is the
    # contract the overlays are written against (agent-pitfalls #4). Note
    # that the *input* field is "gamerTag" and the *published* key is "name":
    # .claude/architecture/phase-a-b/state-keys.md still says gamerTag.
    assert autonaApp.get(f"{player.path}.name") == "TAG"
    assert autonaApp.get(f"{player.path}.team") == "SPONSOR"


@pytest.mark.parametrize("kind", KINDS)
def test_set_data_merges_the_sponsor_into_the_display_name(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.mergedName") == "SPONSOR | TAG"
    # The "name only" variant is what the compact overlays use; it must never
    # pick the sponsor up.
    assert autonaApp.get(f"{player.path}.mergedOnlyName") == "TAG"


@pytest.mark.parametrize("kind", KINDS)
def test_set_data_without_a_sponsor_publishes_the_bare_tag(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG"}, dontLoadFromDB=True)
    autonaApp.settle()

    # No separator when there is nothing to separate.
    assert autonaApp.get(f"{player.path}.mergedName") == "TAG"


@pytest.mark.parametrize("kind", KINDS)
def test_set_data_resolves_a_free_form_state_name(autonaApp, kind):
    """The state combo matches on the region's iso2 ("13"), but a
    state_code saved by an older build -- or auto-filled from Start.gg
    before this existed -- is a free-form name ("Tokyo"). SetData falls back
    to AutonaCountryHelper.FindState for exactly that case, so the region
    reaches the overlay instead of staying blank.

    Both implementations are covered because SetData is written twice
    (agent-pitfalls #5) and this is the kind of fix that lands in one.
    """
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "Tokyo"}, dontLoadFromDB=True)
    autonaApp.settle()

    published = autonaApp.get(f"{player.path}.state")
    assert published, "state_code did not reach state at all"
    assert published.get("original_code") == "13"


@pytest.mark.parametrize("kind", KINDS)
def test_set_data_takes_a_state_iso2_as_is(autonaApp, kind):
    """The normal path: Start.gg already gave us the iso2, so the direct
    match wins and FindState is never consulted (its first call parses the
    pre-computed table, and this runs on the GUI thread under dataLock)."""
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.state").get("original_code") == "13"


@pytest.mark.parametrize("kind", KINDS)
def test_loading_a_second_player_replaces_the_first(autonaApp, kind):
    """SetData(clear=True) is how a set load overwrites the previous match."""
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "FIRST", "prefix": "OLD"}, dontLoadFromDB=True)
    autonaApp.settle()
    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == "SECOND"
    # The sponsor of the player who is no longer on the board must not stay.
    assert autonaApp.get(f"{player.path}.team") == ""
    assert autonaApp.get(f"{player.path}.mergedName") == "SECOND"


@pytest.mark.parametrize("kind", KINDS)
def test_clear_empties_the_published_player_fields(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    player.Clear()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == ""
    assert autonaApp.get(f"{player.path}.team") == ""


@pytest.mark.parametrize("kind", KINDS)
def test_the_losers_flag_is_appended_to_the_merged_name(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    player.losers = True
    player.ExportMergedName()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.mergedName") == "SPONSOR | TAG [L]"
    assert autonaApp.get(f"{player.path}.mergedOnlyName") == "TAG [L]"


# ── character slots ──────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_character_slots_follow_the_requested_count(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetCharactersPerPlayer(3)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, player)) == [1, 2, 3]

    # Shrinking has to remove the published slots too, not just the combos:
    # a stale score.N...character.3 keeps a third character on the overlay.
    player.SetCharactersPerPlayer(1)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, player)) == [1]


@pytest.mark.parametrize("kind", KINDS)
def test_the_slot_count_change_republishes_mains(autonaApp, kind):
    """mains is only rewritten on a slot rebuild, so it must track the count.

    Overlays that render "this player's mains" next to the live pick read
    score.N...mains; if it keeps the old slot count they disagree.
    """
    player = _player(autonaApp, kind)

    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    assert sorted(autonaApp.get(f"{player.path}.mains") or {}) == [1, 2]


@pytest.mark.parametrize("kind", KINDS)
def test_mains_for_the_selected_game_fill_their_own_slots(autonaApp, kind):
    player = _player(autonaApp, kind)
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


@pytest.mark.parametrize("kind", KINDS)
def test_mains_from_another_game_are_ignored(autonaApp, kind):
    """mains is keyed by game codename; a different game's entry is not ours."""
    player = _player(autonaApp, kind)
    player.SetCharactersPerPlayer(2)
    autonaApp.settle()

    player.SetData({"gamerTag": "TAG", "mains": {"some_other_game": [["Mario", 0]]}},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert _names(autonaApp, player) == {1: "", 2: ""}


@pytest.mark.parametrize("kind", [
    "scoreboard",
    pytest.param("team", marks=pytest.mark.xfail(strict=True, reason=(
        "BUG-7: AutonaTeamPlayerWidget still has the pre-fix legacy-mains "
        "loop -- it walks every character_element and assigns the single "
        "character to all of them, so loading a one-character player shows "
        "that character in every slot. AutonaScoreboardPlayerWidget was "
        "fixed; this copy was not (agent-pitfalls #5). Not user-visible "
        "today only because AutonaTeamBattleWidget, its sole caller, is "
        "commented out in src/Autona.py. Remove this marker with the fix."))),
])
def test_a_legacy_single_character_main_only_fills_the_first_slot(
        autonaApp, kind):
    """The shipped "one-character player pollutes every slot" regression.

    The old single-character mains format is a flat ["Name", skin] list.
    It describes ONE character, so it belongs in slot 1 and nowhere else.
    """
    player = _player(autonaApp, kind)
    player.SetCharactersPerPlayer(3)
    autonaApp.settle()

    player.SetData({"gamerTag": "TAG", "mains": ["Mario", 1]},
                   dontLoadFromDB=True)
    autonaApp.settle()

    names = _names(autonaApp, player)
    assert names[1] == "Mario"
    assert names[2] == ""
    assert names[3] == ""


@pytest.mark.parametrize("kind", KINDS)
def test_loading_a_player_without_mains_clears_the_previous_characters(
        autonaApp, kind):
    player = _player(autonaApp, kind)
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


@pytest.mark.parametrize("kind", KINDS)
def test_swapping_two_character_slots_exchanges_the_published_names(
        autonaApp, kind):
    player = _player(autonaApp, kind)
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

@pytest.mark.parametrize("kind", KINDS)
def test_swap_with_exchanges_the_published_names(autonaApp, kind):
    left, right = _players(autonaApp, kind)

    left.SetData({"gamerTag": "LEFT", "prefix": "LS"}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "RIGHT", "prefix": "RS"}, dontLoadFromDB=True)
    autonaApp.settle()

    left.SwapWith(right)
    autonaApp.settle()

    assert autonaApp.get(f"{left.path}.name") == "RIGHT"
    assert autonaApp.get(f"{left.path}.team") == "RS"
    assert autonaApp.get(f"{right.path}.name") == "LEFT"
    assert autonaApp.get(f"{right.path}.team") == "LS"
    assert autonaApp.get(f"{left.path}.mergedName") == "RS | RIGHT"
    assert autonaApp.get(f"{right.path}.mergedName") == "LS | LEFT"


@pytest.mark.parametrize("kind", KINDS)
def test_swapping_a_player_with_itself_is_a_no_op(autonaApp, kind):
    # Reachable from the move-up/move-down buttons at the ends of a team.
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "prefix": "SPONSOR"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    player.SwapWith(player)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == "TAG"
    assert autonaApp.get(f"{player.path}.team") == "SPONSOR"


# ── seed (AutonaScoreboardPlayerWidget only) ─────────────────────────────
#
# AutonaTeamPlayer.ui has no seed field at all, so these are not parametrized.
# The seed reaches the overlay through state and the operator through the
# seedDisplay badge; both are asserted, because the shipped bug was a
# mismatch between them.

def test_a_seed_reaches_the_state_and_the_badge(autonaApp):
    player = _player(autonaApp, "scoreboard")

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
    player = _player(autonaApp, "scoreboard")

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
    player = _player(autonaApp, "scoreboard")

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
    player = _player(autonaApp, "scoreboard")

    player.SetData({"gamerTag": "TAG", "seed": "not a number"},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 0
    assert autonaApp.state.saveBlocked == 0


def test_a_string_seed_is_published_as_a_number(autonaApp):
    player = _player(autonaApp, "scoreboard")

    player.SetData({"gamerTag": "TAG", "seed": "12"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.seed") == 12
    assert player.seedDisplay.text() == "Seed: 12"


def test_the_seed_survives_a_player_swap(autonaApp):
    left, right = _players(autonaApp, "scoreboard")

    left.SetData({"gamerTag": "SEEDED", "seed": 3}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "UNSEEDED"}, dontLoadFromDB=True)
    autonaApp.settle()

    left.SwapWith(right)
    autonaApp.settle()

    assert int(autonaApp.get(f"{left.path}.seed")) == 0
    assert autonaApp.get(f"{right.path}.seed") == 3


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

    docs and the strategy note describe board 2 as the AutonaTeamPlayerWidget
    board; it is not. Board 2 is an AutonaScoreboardWidget, so both boards
    build AutonaScoreboardPlayerWidget, and AutonaTeamPlayerWidget has no
    caller left (AutonaTeamBattleWidget is commented out in src/Autona.py).
    That is why the parametrized tests above construct the team widget
    directly instead of taking it off a board. If someone wires the team
    battle widget back in, this fails and the helper needs revisiting.
    """
    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget

    for number in (1, 2):
        board = autonaApp.board(number)
        assert isinstance(board.team1playerWidgets[0],
                          AutonaScoreboardPlayerWidget)


def test_the_player_fields_keep_their_state_key_object_names(autonaApp):
    """objectName IS the state key (agent-pitfalls #4), on both widgets.

    Renaming any of these in the .ui breaks every overlay and every saved
    program_state.json without a migration layer. tests/
    test_objectname_keypath_contract.py guards the board-level names; these
    are the per-player ones the two widget classes have in common.
    """
    scoreboard, _ = _players(autonaApp, "scoreboard")
    team, _ = _players(autonaApp, "team")

    for widget in (scoreboard, team):
        for name in ("name", "team", "twitter"):
            assert widget.findChild(QLineEdit, name) is not None, (
                f"{type(widget).__name__} lost the '{name}' field")

    # Seed is the one the two do not share.
    assert scoreboard.findChild(QSpinBox, "seed") is not None
    assert team.findChild(QSpinBox, "seed") is None


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


@pytest.mark.parametrize("kind", KINDS)
def test_set_data_publishes_the_online_avatar(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


@pytest.mark.parametrize("kind", KINDS)
def test_editing_the_tag_keeps_the_online_avatar(autonaApp, kind):
    """The operator fixing a typo in a tag must not cost the player a face."""
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    name = player.findChild(QLineEdit, "name")
    name.setText("TAG_FIXED")
    name.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.name") == "TAG_FIXED"
    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


@pytest.mark.parametrize("kind", KINDS)
def test_editing_the_sponsor_keeps_the_online_avatar(autonaApp, kind):
    # The sponsor field runs the same handler, and it is the one that also
    # re-derives the sponsor images -- so it is the likeliest place for a
    # future "just re-export everything" shortcut to reintroduce the bug.
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    team = player.findChild(QLineEdit, "team")
    team.setText("SPONSOR")
    team.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR


@pytest.mark.parametrize("kind", KINDS)
def test_loading_a_player_without_an_avatar_drops_the_previous_one(
        autonaApp, kind):
    """The other half of the semantics: "keep" must not mean "never clears".

    An entrant with no start.gg account has no avatar, and the previous
    player's face staying on the overlay under the new name is the failure
    that made None the default in the first place.
    """
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "FIRST", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get(f"{player.path}.online_avatar") == AVATAR

    player.SetData({"gamerTag": "SECOND"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") is None


@pytest.mark.parametrize("kind", KINDS)
def test_loading_a_player_replaces_the_previous_avatar(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "FIRST", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    player.SetData({"gamerTag": "SECOND", "avatar": OTHER_AVATAR},
                   dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") == OTHER_AVATAR


@pytest.mark.parametrize("kind", KINDS)
def test_clear_removes_the_online_avatar(autonaApp, kind):
    player = _player(autonaApp, kind)

    player.SetData({"gamerTag": "TAG", "avatar": AVATAR}, dontLoadFromDB=True)
    autonaApp.settle()

    player.Clear()
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.online_avatar") is None


@pytest.mark.parametrize("kind", KINDS)
def test_the_avatar_follows_a_player_swap(autonaApp, kind):
    """Swapping the two sides of a set moves the faces with the tags.

    Before the fix both avatars were wiped instead, as a side effect of the
    same defaulting bug; with the parameter left alone they would stay on
    the widget and end up under the other player's name, which is worse.
    Both widget classes restore it explicitly (_swapRestoreTail).
    """
    left, right = _players(autonaApp, kind)

    left.SetData({"gamerTag": "LEFT", "avatar": AVATAR}, dontLoadFromDB=True)
    right.SetData({"gamerTag": "RIGHT"}, dontLoadFromDB=True)
    autonaApp.settle()

    left.SwapWith(right)
    autonaApp.settle()

    assert autonaApp.get(f"{left.path}.online_avatar") is None
    assert autonaApp.get(f"{right.path}.online_avatar") == AVATAR
