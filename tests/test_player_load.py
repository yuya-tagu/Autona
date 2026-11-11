"""Loading a player into a board slot, against the state.

A load is what turns "a set was loaded" into the tags, sponsors, regions,
seeds, avatars and character slots an overlay renders. It is core's
(`PlayerService.ApplyData`, ADR-005 S3e-3); these tests drove it through
the Qt player card's SetData until the card went in ADR-005 S4ac-3, and call
what SetData ran now (`load_player` in conftest: ApplyData without the DB
merge). The card's own wiring, its combos' models and its details dialog
went with it (their tests: the leak pins of agent-pitfalls #13 / #13b, the
construction-site pin of #7, the objectName pin of the player fields).

Assertions read StateManager: state is what the OBS overlays and the Web UI
consume.
"""
import pytest

from src.core.PlayerService import PlayerService
from src.core.StateManager import StateManager
from tests.conftest import load_player


def _slot(app, team=1, board=1):
    """Slot 1 of `team` on board `board`, made afresh, with the synthetic
    character roster loaded first: with no game installed the character
    model is empty and every "which character ended up in slot N" assertion
    would be vacuously true. The slot's state path."""
    app.loadCharacters()
    app.ready(board)
    return f"score.{board}.team.{team}.player.1"


def _load(path, data):
    """`data` loaded into the slot at `path`, as the card's SetData ran it."""
    parts = path.split(".")
    load_player(int(parts[1]), int(parts[3]), data, int(parts[5]))


def _characters(app, path):
    return app.get(f"{path}.character") or {}


def _names(app, path):
    """Published character name per slot, keyed by slot number."""
    return {slot: data.get("name", "")
            for slot, data in _characters(app, path).items()}


# ── the fields an overlay reads ──────────────────────────────────────────

def test_a_load_publishes_the_tag_and_the_sponsor(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "prefix": "SPONSOR"})
    autonaApp.settle()

    # The keys are StateSchema's PLAYER_INPUT_KEYS ("name" / "team"), which
    # is the contract the overlays are written against (agent-pitfalls #4).
    # Note that the *input* field is "gamerTag" and the *published* key is
    # "name".
    assert autonaApp.get(f"{path}.name") == "TAG"
    assert autonaApp.get(f"{path}.team") == "SPONSOR"


def test_a_load_merges_the_sponsor_into_the_display_name(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "prefix": "SPONSOR"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.mergedName") == "SPONSOR | TAG"
    # The "name only" variant is what the compact overlays use; it must never
    # pick the sponsor up.
    assert autonaApp.get(f"{path}.mergedOnlyName") == "TAG"


def test_a_load_without_a_sponsor_publishes_the_bare_tag(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG"})
    autonaApp.settle()

    # No separator when there is nothing to separate.
    assert autonaApp.get(f"{path}.mergedName") == "TAG"


def test_a_load_resolves_a_free_form_state_name(autonaApp):
    """A region matches on its iso2 ("13"), but a state_code saved by an
    older build -- or auto-filled from Start.gg before this existed -- is a
    free-form name ("Tokyo"). The load falls back to CountryCatalog.FindState
    for exactly that case, so the region reaches the overlay instead of
    staying blank.
    """
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "country_code": "JP",
                 "state_code": "Tokyo"})
    autonaApp.settle()

    published = autonaApp.get(f"{path}.state")
    assert published, "state_code did not reach state at all"
    assert published.get("original_code") == "13"


def test_a_load_takes_a_state_iso2_as_is(autonaApp):
    """The normal path: Start.gg already gave us the iso2, so the direct
    match wins and FindState is never consulted (its first call parses the
    pre-computed table, and a load runs under the state's lock --
    PlayerService.ApplyData since ADR-005 S3e-3)."""
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "country_code": "JP", "state_code": "13"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.state").get("original_code") == "13"


def test_a_region_resolves_after_another_country(autonaApp):
    """From one country to another, through the empty slot a set load
    leaves in between (the board's clear, then the load): the second
    country's region is published, not the first's or none. (The Qt card
    refilled its region combo in place on that path, agent-pitfalls #13.)"""
    path = _slot(autonaApp)

    PlayerService(path).ClearSlot()
    _load(path, {"gamerTag": "TAG0", "country_code": "US", "state_code": "CA"})
    autonaApp.settle()
    assert autonaApp.get(f"{path}.state").get("original_code") == "CA"

    PlayerService(path).ClearSlot()
    _load(path, {"gamerTag": "TAG1", "country_code": "JP", "state_code": "13"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.country").get("code") == "JP"
    assert autonaApp.get(f"{path}.state").get("original_code") == "13"


def test_loading_a_player_without_a_country_drops_the_previous_one(
        autonaApp):
    """A player loaded without a country_code used to keep the previous
    player's flag on the overlay (the card emptied its country combo with
    its signals blocked; found while recording the ADR-005 S1d golden). The
    load empties the region and the country in its own save cycle."""
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "FIRST", "country_code": "JP",
                 "state_code": "13"})
    autonaApp.settle()
    assert autonaApp.get(f"{path}.country").get("code") == "JP"
    assert autonaApp.get(f"{path}.state").get("code") == "13"

    _load(path, {"gamerTag": "SECOND"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.country") == {}
    assert autonaApp.get(f"{path}.state") == {}

    # A third player with a country is not disturbed by the fix.
    _load(path, {"gamerTag": "THIRD", "country_code": "US",
                 "state_code": "CA"})
    autonaApp.settle()
    assert autonaApp.get(f"{path}.country").get("code") == "US"
    assert autonaApp.get(f"{path}.state").get("code") == "CA"


def test_loading_a_second_player_replaces_the_first(autonaApp):
    """A load clears the slot first: how a set load overwrites the previous
    match."""
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "FIRST", "prefix": "OLD"})
    autonaApp.settle()
    _load(path, {"gamerTag": "SECOND"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.name") == "SECOND"
    # The sponsor of the player who is no longer on the board must not stay.
    assert autonaApp.get(f"{path}.team") == ""
    assert autonaApp.get(f"{path}.mergedName") == "SECOND"


def test_a_clear_empties_the_published_player_fields(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "prefix": "SPONSOR"})
    autonaApp.settle()

    # The card's clear (PlayerService.ClearSlot since ADR-005 S3e-3; the
    # Web's player clear too).
    PlayerService(path).ClearSlot()
    autonaApp.settle()

    assert autonaApp.get(f"{path}.name") == ""
    assert autonaApp.get(f"{path}.team") == ""


def test_the_losers_flag_is_appended_to_the_merged_name(autonaApp):
    """A name published carries its team's flag from the state.

    The card had no copy of the flag from ADR-005 S2b (it had `losers`,
    kept in step by a per-card SetLosers); PlayerService reads
    `team.{t}.losers` when it publishes.
    """
    from src.core.Actions import AutonaActions

    path = _slot(autonaApp)
    AutonaActions.instance.SetTeamLosers(1, 1, True)
    try:
        _load(path, {"gamerTag": "TAG", "prefix": "SPONSOR"})
        autonaApp.settle()

        assert autonaApp.get(f"{path}.mergedName") == "SPONSOR | TAG [L]"
        assert autonaApp.get(f"{path}.mergedOnlyName") == "TAG [L]"
    finally:
        AutonaActions.instance.SetTeamLosers(1, 1, False)
        autonaApp.settle()


# ── character slots ──────────────────────────────────────────────────────

def _setCharacters(number):
    """Board 1's slots at `number` character slots: the count is the state's
    (AutonaActions.SetRoster, ADR-005 S3e-6)."""
    from src.core.Actions import AutonaActions
    assert AutonaActions.instance.SetRoster(1, characters=number) is True


def test_character_slots_follow_the_requested_count(autonaApp):
    path = _slot(autonaApp)

    _setCharacters(3)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, path)) == [1, 2, 3]

    # Shrinking has to remove the published slots too: a stale
    # score.N...character.3 keeps a third character on the overlay.
    _setCharacters(1)
    autonaApp.settle()
    assert sorted(_characters(autonaApp, path)) == [1]


def test_the_slot_count_change_republishes_mains(autonaApp):
    """mains is only rewritten on a slot rebuild, so it must track the count.

    Overlays that render "this player's mains" next to the live pick read
    score.N...mains; if it keeps the old slot count they disagree.
    """
    path = _slot(autonaApp)

    _setCharacters(2)
    autonaApp.settle()

    assert sorted(autonaApp.get(f"{path}.mains") or {}) == [1, 2]


def test_mains_for_the_selected_game_fill_their_own_slots(autonaApp):
    path = _slot(autonaApp)
    _setCharacters(3)
    autonaApp.settle()

    _load(path, {
        "gamerTag": "TAG",
        "mains": {"harness_game": [["Mario", 1], ["Luigi", 0]]},
    })
    autonaApp.settle()

    names = _names(autonaApp, path)
    assert names[1] == "Mario"
    assert names[2] == "Luigi"
    # Two mains must not bleed into a third slot.
    assert names[3] == ""
    # The skin index travels with the character, not just the name.
    assert _characters(autonaApp, path)[1]["skin"] == 1
    assert _characters(autonaApp, path)[2]["skin"] == 0


def test_mains_from_another_game_are_ignored(autonaApp):
    """mains is keyed by game codename; a different game's entry is not ours."""
    path = _slot(autonaApp)
    _setCharacters(2)
    autonaApp.settle()

    _load(path, {"gamerTag": "TAG",
                 "mains": {"some_other_game": [["Mario", 0]]}})
    autonaApp.settle()

    assert _names(autonaApp, path) == {1: "", 2: ""}


def test_a_legacy_single_character_main_only_fills_the_first_slot(autonaApp):
    """The shipped "one-character player pollutes every slot" regression.

    The old single-character mains format is a flat ["Name", skin] list.
    It describes ONE character, so it belongs in slot 1 and nowhere else.
    (BUG-7's other half lived in AutonaTeamPlayerWidget, dead code deleted
    on 2026-08-28.)
    """
    path = _slot(autonaApp)
    _setCharacters(3)
    autonaApp.settle()

    _load(path, {"gamerTag": "TAG", "mains": ["Mario", 1]})
    autonaApp.settle()

    names = _names(autonaApp, path)
    assert names[1] == "Mario"
    assert names[2] == ""
    assert names[3] == ""


def test_loading_a_player_without_mains_clears_the_previous_characters(
        autonaApp):
    path = _slot(autonaApp)
    _setCharacters(2)
    autonaApp.settle()

    _load(path, {"gamerTag": "FIRST",
                 "mains": {"harness_game": [["Mario", 0], ["Luigi", 0]]}})
    autonaApp.settle()
    assert _names(autonaApp, path)[1] == "Mario"

    _load(path, {"gamerTag": "SECOND"})
    autonaApp.settle()

    # The next player inherits nothing: otherwise the overlay shows the
    # previous entrant's picks under the new name.
    assert _names(autonaApp, path) == {1: "", 2: ""}


# ── swapping two players ─────────────────────────────────────────────────
#
# A swap exchanges the two slots in the state (AutonaActions.SwapTeams,
# ADR-005 S2c); it used to be a copy from one card into the other (SwapWith).
# `left` / `right` are slot 1 of the two teams of board 1, i.e. exactly the
# pair a team swap exchanges.

def _swap(app):
    from src.core.Actions import AutonaActions
    assert AutonaActions.instance.SwapTeams(1) is True
    app.settle()


def _pair(app):
    left = _slot(app)
    return left, "score.1.team.2.player.1"


def test_a_swap_exchanges_the_published_names(autonaApp):
    left, right = _pair(autonaApp)

    _load(left, {"gamerTag": "LEFT", "prefix": "LS"})
    _load(right, {"gamerTag": "RIGHT", "prefix": "RS"})
    autonaApp.settle()

    _swap(autonaApp)

    assert autonaApp.get(f"{left}.name") == "RIGHT"
    assert autonaApp.get(f"{left}.team") == "RS"
    assert autonaApp.get(f"{right}.name") == "LEFT"
    assert autonaApp.get(f"{right}.team") == "LS"
    assert autonaApp.get(f"{left}.mergedName") == "RS | RIGHT"
    assert autonaApp.get(f"{right}.mergedName") == "LS | LEFT"


def test_moving_a_player_past_the_end_of_its_team_is_refused(autonaApp):
    """The move up / move down at the ends of a team (the SCORE page's
    players:swap; the Qt card's buttons, which stopped there themselves,
    until ADR-005 S4ac-3): the route refuses a slot the team does not have,
    which SwapPlayers would otherwise move the existing slot into."""
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "prefix": "SPONSOR"})
    autonaApp.settle()
    before = autonaApp.get(path)

    actions = autonaApp.web()
    assert actions.swap_players(1, 1, 1, 0) is False
    assert actions.swap_players(1, 1, 1, 2) is False
    autonaApp.settle()

    assert autonaApp.get(path) == before
    assert autonaApp.get("score.1.team.1.player.2") is None


# ── seed ─────────────────────────────────────────────────────────────────

def test_a_seed_reaches_the_state(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "seed": 7})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.seed") == 7


def test_a_player_without_seed_info_clears_the_previous_seed(autonaApp):
    """The shipped "seed shown for an unseeded entrant" bug.

    Providers hand back None for an unseeded tournament (initialSeedNum is
    null). The old guard was `is not None`, so None skipped the whole branch
    and the previous entrant's seed stayed on the overlay under the new name.
    """
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "SEEDED", "seed": 7})
    autonaApp.settle()
    assert autonaApp.get(f"{path}.seed") == 7

    _load(path, {"gamerTag": "UNSEEDED", "seed": None})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.seed") == 0


def test_a_player_with_no_seed_key_clears_the_previous_seed(autonaApp):
    # Same failure through the other door: some provider paths omit the key
    # entirely rather than sending None.
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "SEEDED", "seed": 7})
    autonaApp.settle()

    _load(path, {"gamerTag": "UNSEEDED"})
    autonaApp.settle()

    # int(): the Qt card's clear published its spinbox's text ("0") once;
    # both mean unseeded.
    assert int(autonaApp.get(f"{path}.seed")) == 0


def test_a_non_numeric_seed_is_treated_as_unseeded(autonaApp):
    # Providers also send seeds as strings; garbage must not raise inside a
    # set load (the load holds a SaveBlock -- an exception escaping it is the
    # BUG-3 shape, where saving never resumes).
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "seed": "not a number"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.seed") == 0
    assert autonaApp.state.saveBlocked == 0


def test_a_string_seed_is_published_as_a_number(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "seed": "12"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.seed") == 12


def test_the_seed_survives_a_player_swap(autonaApp):
    left, right = _pair(autonaApp)

    _load(left, {"gamerTag": "SEEDED", "seed": 3})
    _load(right, {"gamerTag": "UNSEEDED"})
    autonaApp.settle()

    _swap(autonaApp)

    assert int(autonaApp.get(f"{left}.seed")) == 0
    assert autonaApp.get(f"{right}.seed") == 3


# ── the boards ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("boardNumber", [1, 2])
def test_each_board_publishes_its_players_under_its_own_subtree(
        autonaApp, boardNumber):
    home = _slot(autonaApp, team=1, board=boardNumber)

    _load(home, {"gamerTag": "HOME"})
    _load(f"score.{boardNumber}.team.2.player.1", {"gamerTag": "AWAY"})
    autonaApp.settle()

    assert autonaApp.get(
        f"score.{boardNumber}.team.1.player.1.name") == "HOME"
    assert autonaApp.get(
        f"score.{boardNumber}.team.2.player.1.name") == "AWAY"
    # And nowhere else: the two boards share one StateManager.
    other = 2 if boardNumber == 1 else 1
    assert autonaApp.get(f"score.{other}.team.1.player.1.name") is None


# ── the online avatar (LIVE-BUG-1) ───────────────────────────────────────
#
# The avatar start.gg hands out for an entrant is the one player field that
# is NOT derived from anything else on the slot: it arrives in the set
# payload and only a caller that knows which player now occupies the slot
# may write it. The card's ExportPlayerImages used to default that parameter
# to None, so both deferred refreshes -- SetData's own finally block and
# NameChanged -- published None over the URL SetData had written moments
# earlier. The whole thing happened inside one BlockSaving, so nothing ever
# observed the URL: every set loaded from start.gg reached the overlays with
# no avatar, and so did every hand-edited tag. Found by tests/live against
# the real API. (PlayerService.PublishImages carries the semantics now.)
#
# A URL with no image extension is used throughout: StateManager exports any
# state value that looks like an http .png/.jpg by *downloading* it, and a
# unit test must not reach the network. What is under test here is which
# state key survives, which the extension has no bearing on.

AVATAR = "https://cdn.example.invalid/avatar/abc123"
OTHER_AVATAR = "https://cdn.example.invalid/avatar/def456"


def _editSponsor(path, text):
    """The sponsor edited and finished, as the Qt card's sponsor field ran it
    on editingFinished until ADR-005 S4ac-3 (the same handler as the tag's:
    conftest.edit_tag)."""
    player = PlayerService(path)
    if StateManager.Get(player.Key("team")) != text:
        player.SetText("team", text)
    name = StateManager.Get(player.Key("name")) or ""
    player.PublishMergedName(text, name)
    player.PublishImages(text, name)
    player.SetPlayerId(None, announce=True)


def test_a_load_publishes_the_online_avatar(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "avatar": AVATAR})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.online_avatar") == AVATAR


def test_editing_the_tag_keeps_the_online_avatar(autonaApp):
    """The operator fixing a typo in a tag must not cost the player a face."""
    from tests.conftest import edit_tag

    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "avatar": AVATAR})
    autonaApp.settle()

    edit_tag(path, "TAG_FIXED")
    autonaApp.settle()

    assert autonaApp.get(f"{path}.name") == "TAG_FIXED"
    assert autonaApp.get(f"{path}.online_avatar") == AVATAR


def test_editing_the_sponsor_keeps_the_online_avatar(autonaApp):
    # The sponsor field ran the same handler, and it is the one that also
    # re-derives the sponsor images -- so it is the likeliest place for a
    # future "just re-export everything" shortcut to reintroduce the bug.
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "avatar": AVATAR})
    autonaApp.settle()

    _editSponsor(path, "SPONSOR")
    autonaApp.settle()

    assert autonaApp.get(f"{path}.team") == "SPONSOR"
    assert autonaApp.get(f"{path}.online_avatar") == AVATAR


def test_loading_a_player_without_an_avatar_drops_the_previous_one(
        autonaApp):
    """The other half of the semantics: "keep" must not mean "never clears".

    An entrant with no start.gg account has no avatar, and the previous
    player's face staying on the overlay under the new name is the failure
    that made None the default in the first place.
    """
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "FIRST", "avatar": AVATAR})
    autonaApp.settle()
    assert autonaApp.get(f"{path}.online_avatar") == AVATAR

    _load(path, {"gamerTag": "SECOND"})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.online_avatar") is None


def test_loading_a_player_replaces_the_previous_avatar(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "FIRST", "avatar": AVATAR})
    autonaApp.settle()

    _load(path, {"gamerTag": "SECOND", "avatar": OTHER_AVATAR})
    autonaApp.settle()

    assert autonaApp.get(f"{path}.online_avatar") == OTHER_AVATAR


def test_a_clear_removes_the_online_avatar(autonaApp):
    path = _slot(autonaApp)

    _load(path, {"gamerTag": "TAG", "avatar": AVATAR})
    autonaApp.settle()

    PlayerService(path).ClearSlot()
    autonaApp.settle()

    assert autonaApp.get(f"{path}.online_avatar") is None


def test_the_avatar_follows_a_player_swap(autonaApp):
    """Swapping the two sides of a set moves the faces with the tags.

    Before the fix both avatars were wiped instead, as a side effect of the
    same defaulting bug; with the parameter left alone they would have
    stayed on the cards and ended up under the other player's name, which
    is worse. The swap moves the slot's `online_avatar` with the rest of it
    (AutonaActions.SwapTeams; the card copy had to restore it by hand).
    """
    left, right = _pair(autonaApp)

    _load(left, {"gamerTag": "LEFT", "avatar": AVATAR})
    _load(right, {"gamerTag": "RIGHT"})
    autonaApp.settle()

    _swap(autonaApp)

    assert autonaApp.get(f"{left}.online_avatar") is None
    assert autonaApp.get(f"{right}.online_avatar") == AVATAR
