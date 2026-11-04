"""Contract tests for src/core/Top8Service.py (headless core Step 5-5).

The TOP 8 panel owns a QWebEngineView, so it is one of the widgets the test
harness deliberately never builds -- which is why the *decisions* it used to
make on its way to `StateManager.Set` had no coverage at all: merging staged
spin values into the published matches, calling a non-tie "finished",
re-running Recompute, and falling back to "TOP 8" for an empty caption.
`tests/test_top8_model.py` covered the topology underneath them; this covers
the layer that was previously inside the widget, with **no QApplication**.

Isolation follows tests/test_actions.py.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core import Top8Model as top8
from src.core import StateSchema as schema
from src.core import Top8Service
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_top8_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(schema.TOP8_ROOT, None)
        SM.lastSavedState.pop(schema.TOP8_ROOT, None)
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
            SM.changedKeys = changedKeys
        os.chdir(_ORIGINAL_CWD)


def _settle():
    if SM._export_thread is not None:
        SM._export_thread.join()


def _allTies():
    return {matchId: [0, 0] for matchId in top8.MATCH_IDS}


def _seed():
    """The state a launch leaves behind, as StateSchema.SeedPanelState does.

    Deliberately including the Recompute: an empty ``top8.matches`` is not a
    state the panel is ever handed, and the difference matters -- see
    ``test_the_first_apply_on_an_unrecomputed_seed_drops_the_scores`` for
    what happens when it is.
    """
    SM.Set(schema.TOP8_ROOT, top8.DefaultState())
    SM.Set(Top8Service.MATCHES_PATH,
           top8.Recompute(top8.DefaultState()["matches"]))
    _settle()


# ── key paths ────────────────────────────────────────────────────────────

def test_the_paths_are_built_from_the_schema():
    assert Top8Service.PLAYERS_PATH == "top8.players"
    assert Top8Service.MATCHES_PATH == "top8.matches"
    assert Top8Service.PHASE_LABEL_PATH == "top8.phaseLabel"
    assert schema.TOP8_PHASE_LABEL_KEY in schema.TOP8_KEYS


# ── players ──────────────────────────────────────────────────────────────

def _seedPlayers():
    SM.Set(Top8Service.PLAYERS_PATH,
           {slot: top8.DefaultPlayer() for slot in top8.PLAYER_SLOTS})
    _settle()


def _setsDuring(action):
    """The keys StateManager.Set was called with while `action` ran."""
    from unittest.mock import patch
    keys = []
    real = SM.Set

    def spy(key, value=None, *args, **kwargs):
        keys.append(key)
        return real(key, value, *args, **kwargs)

    with patch.object(SM, "Set", spy):
        result = action()
    _settle()
    return keys, result


def test_a_text_edit_writes_only_that_field_trimmed():
    # Per field since ADR-005 S3e-8: the panel used to republish all eight
    # players from its inputs on every edit.
    _seedPlayers()
    SM.Set("top8.players.2.name", "Bob")
    _settle()
    before = SM.revision

    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerText("1", "name", "  Alice  "))

    assert written is True
    assert keys == ["top8.players.1.name"]
    assert SM.Get("top8.players.1.name") == "Alice"
    assert SM.Get("top8.players.2.name") == "Bob"
    assert SM.revision == before + 1


def test_an_unchanged_field_is_not_written():
    _seedPlayers()
    SM.Set("top8.players.1.sponsor", "AA")
    _settle()
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerText(1, "sponsor", " AA"))
    assert written is False and keys == []


def test_an_unknown_slot_or_field_writes_nothing():
    _seedPlayers()
    keys, _ = _setsDuring(lambda: (
        Top8Service.SetPlayerText("9", "name", "X"),
        Top8Service.SetPlayerText("1", "country", "X"),
        Top8Service.SetPlayerCountry("0", "JP"),
        Top8Service.SetPlayerCharacter("x", "Mario")))
    assert keys == []


# ── a country and a character, by identifier (ADR-005 S4h) ───────────────
#
# Both values are built from the catalogs with the player cards' builders,
# so the two catalogs here are real ones over the fixture games and
# countries the player slot recording was made in (no Qt).

@pytest.fixture(scope="module")
def _catalogDir(tmp_path_factory):
    import shutil
    from tests.conftest import _link_tree
    from tests.fixtures.player_slots import recording
    directory = tmp_path_factory.mktemp("top8_catalogs")
    link = directory / "assets"
    _link_tree(link, Path(__file__).resolve().parent.parent / "assets")
    (directory / "out").mkdir()
    shutil.copytree(recording.GAME_FIXTURE / "games",
                    directory / "user_data" / "games")
    _addBowserJrAndRoy(directory / "user_data" / "games" / "tgame")
    flags = directory / "user_data" / "additional_flag"
    flags.mkdir(parents=True)
    for name in recording.CUSTOM_FLAGS:
        (flags / name).write_bytes(b"")
    try:
        yield directory
    finally:
        # Unlinked, never followed (see tests/test_player_service.py).
        try:
            if link.is_symlink():
                os.unlink(link)
            else:
                os.rmdir(link)
        except OSError:
            pass


def _addBowserJrAndRoy(game):
    """Two characters of SSBU's shape, in this module's copy of the test
    game: Bowser Jr.'s skin 2 is "Roy", a different character, and Roy is
    a character too -- a skin's en_name that is another character's. Each
    skin's files are copies of Mario's, in every pack Mario has them in."""
    import json
    import shutil
    config = game / "base_files" / "config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["character_to_codename"]["Bowser Jr."] = {
        "codename": "koopajr", "smashgg_name": "Bowser Jr.",
        "locale": {"ja": "クッパJr."},
        "skin_name": {"2": {"name": "Roy", "is_different_character": True,
                            "locale": {"ja": "ロイ"}}}}
    data["character_to_codename"]["Roy"] = {
        "codename": "roy", "smashgg_name": "Roy", "locale": {"ja": "ロイ"}}
    config.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    for pack, prefix in ((game / "base_files" / "icon", "chara_2_"),
                         (game / "full", ""), (game / "portrait", "")):
        for path in sorted(pack.glob(f"{prefix}mario_*.png")):
            skin = int(path.stem.rsplit("_", 1)[1])
            for codename, count in (("koopajr", 3), ("roy", 2)):
                if skin < count:
                    shutil.copyfile(path, pack / path.name.replace(
                        f"{prefix}mario_", f"{prefix}{codename}_"))


@pytest.fixture
def catalogs(_catalogDir, monkeypatch):
    """(games, countries) over the fixture data, the test game loaded; the
    CWD in their directory (a skin's assets are found on the disk)."""
    from src.core.CountryCatalog import CountryCatalog
    from src.core.GameAssetCatalog import GameAssetCatalog
    from tests.fixtures.player_slots import recording
    monkeypatch.chdir(_catalogDir)
    locale = recording.LOCALE

    def locales():
        return locale["program"], locale["export"]

    games = GameAssetCatalog(locales=locales,
                             remap=recording.remapFor(locale["remapping"]))
    games.ResetGameState()
    games.ScanGames()
    games.SelectGame("tgame")
    countries = CountryCatalog(locales=locales)
    assert countries.Load() is True
    _settle()
    return games, countries


def _cardValues(games, countries):
    from src.core.PlayerService import PlayerService
    return PlayerService("score.1.team.1.player.1", games=games,
                         countries=countries)


def test_a_country_is_picked_by_its_code_and_built_like_a_cards(catalogs):
    """The value, its key order included, is the player card's for the same
    code (the panel used to hand over its combo's row)."""
    from tests.fixtures.player_slots.recording import encode
    games, countries = catalogs
    card = _cardValues(games, countries)
    _seedPlayers()

    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCountry("3", "JP", countries))
    assert written is True
    assert keys == ["top8.players.3.country"]
    stored = SM.Get("top8.players.3.country")
    assert stored["code"] == "JP" and stored["asset"]
    assert encode(stored) == encode(card.CountryValue("JP"))

    # A custom flag is a code too (its name).
    Top8Service.SetPlayerCountry("4", "Custom Flag", countries)
    assert SM.Get("top8.players.4.country") == card.CountryValue("Custom Flag")
    assert SM.Get("top8.players.4.country")["asset"] \
        == "./user_data/additional_flag/Custom Flag.png"

    # The same again is not news; none empties it.
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCountry("3", "JP", countries))
    assert (keys, written) == ([], False)
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCountry("3", None, countries))
    assert written is True and SM.Get("top8.players.3.country") == {}


@pytest.mark.parametrize("code", ["ZZZ", {"code": "JP"}, 81])
def test_a_country_that_is_no_code_is_not_written(catalogs, code):
    _, countries = catalogs
    _seedPlayers()
    Top8Service.SetPlayerCountry("2", "FR", countries)
    _settle()
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCountry("2", code, countries))
    assert (keys, written) == ([], False)
    assert SM.Get("top8.players.2.country")["code"] == "FR"


def test_a_character_is_built_like_a_cards_with_its_skins_assets(catalogs):
    """{"1": the player card's slot value for that character and skin} --
    name, en_name, codename..., the skin's assets (what the overlay draws),
    skin and variant -- whichever skin is picked."""
    from src.core.PlayerService import BuildCharacterValue, CharacterSlot
    from tests.fixtures.player_slots.recording import encode
    games, _ = catalogs
    _seedPlayers()

    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCharacter("8", "Luigi", 1, games))
    assert written is True
    assert keys == ["top8.players.8.characters"]
    stored = SM.Get("top8.players.8.characters")
    assert list(stored) == ["1"]
    assert encode(stored["1"]) == encode(
        BuildCharacterValue(CharacterSlot("Luigi", 1), games))
    assert stored["1"]["en_name"] == "Luigi"
    assert stored["1"]["skin"] == 1 and stored["1"]["variant"] == {}
    assert stored["1"]["assets"]

    # Not news: not written.
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCharacter("8", "Luigi", 1, games))
    assert (keys, written) == ([], False)
    # None empties it.
    Top8Service.SetPlayerCharacter("8", None, games=games)
    assert SM.Get("top8.players.8.characters") == {}


@pytest.mark.parametrize("skin", [99, -1, "2", None, True])
def test_a_skin_that_is_no_row_is_the_first(catalogs, skin):
    """The colour has no blank choice in the Top 8 and the overlay draws
    the skin's icon: a skin past the character's rows (or no number) is
    its first."""
    games, _ = catalogs
    _seedPlayers()
    Top8Service.SetPlayerCharacter("1", "Luigi", skin, games)
    assert SM.Get("top8.players.1.characters") \
        == {"1": Top8Service.CharacterValue("Luigi", 0, games)}


def test_a_different_character_skin_is_that_character(catalogs):
    """Mario's skin 2 is Dr. Mario: the value names Dr. Mario (the card's
    rule) and reads back as Mario on that skin."""
    games, _ = catalogs
    value = Top8Service.CharacterValue("Mario", 2, games)
    assert value["en_name"] == "Dr. Mario" and value["codename"] == "mario"
    assert Top8Service.CharacterPick(value, games) == ("Mario", 2)


@pytest.mark.parametrize("en_name", ["Nobody", 5, {"en_name": "Mario"}])
def test_a_character_the_game_does_not_list_is_not_written(catalogs, en_name):
    games, _ = catalogs
    _seedPlayers()
    Top8Service.SetPlayerCharacter("6", "Peach", 0, games)
    _settle()
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCharacter("6", en_name, 0, games))
    assert (keys, written) == ([], False)
    assert SM.Get("top8.players.6.characters.1.en_name") == "Peach"


def test_every_character_and_skin_reads_back_as_its_pick(catalogs):
    """The panel's value and the import's, for every skin of every
    character the combo lists."""
    games, _ = catalogs
    snap = games.Current()
    picks = [(data["en_name"], row)
             for _text, data in snap.characterRows
             for row in range(len(snap.skinRows.get(data["en_name"], ())))]
    assert ("Bowser Jr.", 2) in picks
    for pick in picks:
        assert Top8Service.CharacterPick(
            Top8Service.CharacterValue(*pick, games), games) == pick, pick
        assert Top8Service.CharacterPick(
            Top8Service.ImportedCharacterValue(*pick, games), games) \
            == pick, pick


@pytest.mark.parametrize("order", ["listed", "reversed"])
def test_a_skin_named_as_another_character_reads_back_as_its_own(
        catalogs, order):
    """Bowser Jr.'s skin 2 is "Roy", a different character, and Roy is a
    character too (SSBU). Whatever the shape, and whichever of the two the
    combo lists first, the pick is Bowser Jr. on skin 2: the codename comes
    before the en_name, and a value with no codename (the skin row the
    panel wrote before S4h) is found among every character's skins."""
    import dataclasses
    games, _ = catalogs
    snap = games.Current()
    if order == "reversed":
        snap = dataclasses.replace(
            snap, characterRows=tuple(reversed(snap.characterRows)))
    pick = lambda value: Top8Service.CharacterPick(value, games, snap)

    value = Top8Service.CharacterValue("Bowser Jr.", 2, games, snap)
    assert (value["en_name"], value["codename"]) == ("Roy", "koopajr")
    assert pick(value) == ("Bowser Jr.", 2)
    # Before S4h: the skin row -- Roy's name, no codename.
    skinRow = games.SkinRow("Bowser Jr.", 2, snapshot=snap)
    assert skinRow["en_name"] == "Roy" and "codename" not in skinRow
    assert pick(skinRow) == ("Bowser Jr.", 2)
    # Written in another locale, with another asset pack: the codename.
    moved = {**value, "display_name": "クッパJr.",
             "assets": {"base_files/icon": {"asset": "elsewhere.png"}}}
    assert pick(moved) == ("Bowser Jr.", 2)
    # Roy is still Roy.
    assert pick(Top8Service.CharacterValue("Roy", 1, games, snap)) \
        == ("Roy", 1)
    assert pick(games.SkinRow("Roy", 1, snapshot=snap)) == ("Roy", 1)


def test_the_shapes_a_slot_held_before_s4h_read_back(catalogs):
    """What a saved Top 8 can still hold from before S4h, read as the pick
    the panel shows (it showed nothing for the skin row's shape)."""
    games, _ = catalogs
    pick = Top8Service.CharacterPick

    # The panel's skin row ({"assets"}; a different-character skin carries
    # that character's name too): found by its assets.
    assert pick(games.SkinRow("Luigi", 2), games) == ("Luigi", 2)
    drMario = games.SkinRow("Mario", 2)
    assert drMario["en_name"] == "Dr. Mario"
    assert pick(drMario, games) == ("Mario", 2)
    # The panel's character row (no assets) and the import's copy of the
    # character's config: the character, on its first skin.
    assert pick(games.CharacterRow("Peach"), games) == ("Peach", 0)
    assert pick(games.Character("Luigi"), games) == ("Luigi", 0)
    # Nothing, or a character this game does not have.
    for value in ({}, None, "Mario", {"en_name": "Nobody", "assets": {}}):
        assert pick(value, games) == (None, -1)


def _import(main, games, countries, snap=None):
    snap = snap if snap is not None else games.Current()
    return Top8Service.ResolveImportedPlayer(
        {"name": "P", "sponsor": "", "country_code": None,
         "mains": {"tgame": [main], "other": [["Mario", 0]]}},
        snap, countries, games)


@pytest.mark.parametrize("main, pick", [
    (["Luigi"], ("Luigi", 0)),          # Start.gg's: the key, no skin
    (["mario", 1], ("Mario", 1)),       # by codename
    (["Mario", 2], ("Mario", 2)),       # a different-character skin
    (["koopa", "1"], ("Bowser", 1)),    # a database record's string skin
    (["Peach", 42], ("Peach", 0)),      # past the pack's skins
    (["Peach", float("inf")], ("Peach", 0)),   # no number at all
    (["koopajr", 2], ("Bowser Jr.", 2)),
    (["NoArt", 0], ("NoArt", -1)),      # a character without skins
])
def test_an_imported_main_is_the_config_with_the_panels_keys_added(
        catalogs, main, pick):
    """Additive (ADR-005 S4h): the import writes the character's config as
    it always did -- every key, its value and its place: the test game's
    config has the real games' shape (smashgg_name, locale, skin_name) --
    and adds the keys of the panel's value it lacks, on the main's skin:
    the skin's assets (the overlay's icon), modded, name, skin, variant."""
    games, countries = catalogs
    value = _import(main, games, countries)["characters"]["1"]
    config = games.Character(pick[0])
    panels = Top8Service.CharacterValue(*pick, games)

    assert list(value)[:len(config)] == list(config)
    assert {key: value[key] for key in config} == config
    added = [key for key in value if key not in config]
    assert added == [key for key in panels if key not in config]
    assert {key: value[key] for key in added} \
        == {key: panels[key] for key in added}
    assert {"assets", "skin", "variant"} <= set(added)
    assert value["assets"] == panels["assets"]
    assert value["skin"] == pick[1]
    assert Top8Service.CharacterPick(value, games) == pick

    if pick == ("Mario", 1):
        for key in ("smashgg_name", "locale", "skin_name", "export_name"):
            assert key in value, key
    if pick[0] in ("Bowser Jr.", "Mario") and pick[1] == 2:
        # A different-character skin: the panel's value names the skin's
        # character, the import keeps the config's en_name (additive).
        assert panels["en_name"] in ("Roy", "Dr. Mario")
        assert value["en_name"] == pick[0]
        assert value["name"] == panels["name"]


def test_an_imported_main_the_game_does_not_have_is_no_character(catalogs):
    games, countries = catalogs
    assert _import(["nobody", 0], games, countries)["characters"] == {}


def test_an_imported_modded_main_with_mods_off_is_its_config(catalogs):
    """A modded character is not in the combo while mods are off, so there
    is no panel value to add: the import writes the config's copy alone,
    as it did before S4h."""
    games, countries = catalogs
    assert games.CharacterRow("Waluigi") is None
    value = _import(["waluigi", 0], games, countries)["characters"]["1"]
    assert value == games.Character("Waluigi")
    assert value["modded"] is True
    assert Top8Service.CharacterPick(value, games) == (None, -1)


def test_without_a_catalog_instance_nothing_raises(catalogs, monkeypatch):
    """`games` left out with no GameAssetCatalog instance: no value to
    build -- the import keeps the config's copy, a pick is none, a write
    is refused."""
    from src.core.GameAssetCatalog import GameAssetCatalog
    games, countries = catalogs
    snap = games.Current()
    monkeypatch.setattr(GameAssetCatalog, "instance", None)
    _seedPlayers()

    player = Top8Service.ResolveImportedPlayer(
        {"name": "P", "mains": {"tgame": [["Luigi", 1]]}}, snap, countries)
    assert player["characters"] == {"1": games.Character("Luigi")}
    assert Top8Service.CharacterValue("Luigi") == {}
    assert Top8Service.ImportedCharacterValue("Luigi") == {}
    assert Top8Service.CharacterPick({"en_name": "Luigi"}) == (None, -1)
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCharacter("1", "Luigi", 0))
    assert (keys, written) == ([], False)


def test_forgetting_the_characters_writes_only_slots_that_have_one():
    """A game switch: every character goes (the skin with it), in one save
    cycle, and a slot without one is not written (the smallest delta)."""
    _seedPlayers()
    SM.Set("top8.players.2.characters",
           {"1": {"en_name": "Mario", "assets": {"icon": "mario_2.png"}}})
    SM.Set("top8.players.7.characters", {"1": {"en_name": "Luigi"}})
    Top8Service.SetPlayerText("2", "name", "Alice")
    _settle()
    before = SM.revision

    keys, slots = _setsDuring(Top8Service.ForgetCharacters)

    assert slots == ["2", "7"]
    assert keys == ["top8.players.2.characters", "top8.players.7.characters"]
    assert SM.revision == before + 1
    assert all(SM.Get(f"top8.players.{slot}.characters") == {}
               for slot in top8.PLAYER_SLOTS)
    assert SM.Get("top8.players.2.name") == "Alice"

    # Nothing left to forget: no write, no cycle.
    keys, slots = _setsDuring(Top8Service.ForgetCharacters)
    assert (keys, slots) == ([], [])
    assert SM.revision == before + 1


def test_forgetting_the_characters_of_a_missing_bracket_writes_nothing():
    keys, slots = _setsDuring(Top8Service.ForgetCharacters)
    assert (keys, slots) == ([], [])
    assert SM.Get(Top8Service.PLAYERS_PATH) is None


# ── the phase caption ────────────────────────────────────────────────────

def test_an_empty_caption_falls_back_to_the_default():
    assert Top8Service.SetPhaseLabel("  ") == top8.DEFAULT_PHASE_LABEL
    assert SM.Get(Top8Service.PHASE_LABEL_PATH) == top8.DEFAULT_PHASE_LABEL
    assert Top8Service.SetPhaseLabel(None) == top8.DEFAULT_PHASE_LABEL


def test_a_caption_is_stored_trimmed():
    assert Top8Service.SetPhaseLabel("  FINALS  ") == "FINALS"
    assert SM.Get(Top8Service.PHASE_LABEL_PATH) == "FINALS"


# ── 反映 (apply the staged scores) ───────────────────────────────────────

def test_applying_a_non_tie_marks_the_match_finished_and_propagates():
    _seed()
    scores = _allTies()
    scores["wsf1"] = [2, 1]

    matches = Top8Service.ApplyScores(scores)

    assert matches["wsf1"]["score"] == [2, 1]
    assert matches["wsf1"]["finished"] is True
    # Player slot 1 (wsf1's first entrant) won, so it reaches the winners
    # final; the loser crosses to LQF2 (the FGC anti-rematch progression).
    assert matches["wf"]["slots"][0] == 1
    assert matches["lqf2"]["slots"][1] == 2
    assert SM.Get("top8.matches.wsf1.finished") is True


def test_a_tie_is_not_a_result():
    _seed()
    matches = Top8Service.ApplyScores({**_allTies(), "wsf1": [1, 1]})
    assert matches["wsf1"]["finished"] is False
    assert matches["wf"]["slots"] == [top8.TBD, top8.TBD]


def test_applying_scores_keeps_the_published_round_names():
    _seed()
    SM.Set("top8.matches.wsf1.name", "Winners Semi (custom)")

    matches = Top8Service.ApplyScores({**_allTies(), "wsf1": [3, 0]})

    assert matches["wsf1"]["name"] == "Winners Semi (custom)"


def test_applying_scores_is_one_save_cycle():
    """One delta for the whole bracket.

    Recompute can rewrite every downstream match, so publishing per match
    would put a half-propagated bracket on the overlay.
    """
    _seed()
    before = SM.revision

    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0], "wsf2": [2, 1]})

    assert SM.revision == before + 1


def test_the_first_apply_on_a_fresh_install_seed_keeps_the_scores():
    """Regression for known-smells S-18.

    ``top8.DefaultState()`` hands out matches whose ``slots`` are all TBD.
    ``SeedPanelState`` used to write it verbatim on a first launch, so
    Recompute read the first 反映 as "the participants changed" and cleared
    the score. The seed is now recomputed, so the first apply sticks.
    """
    SM.Set(schema.TOP8_ROOT, None)
    schema.SeedPanelState()
    _settle()
    assert SM.Get("top8.matches.wsf1.slots") == [1, 2]

    first = Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 1]})
    assert first["wsf1"]["score"] == [2, 1]


def test_applying_some_matches_keeps_the_others():
    """A merge (ADR-005 S4h): a match left out keeps its published score
    and result -- a client sending only what it changed does not wipe the
    rest (the panel sends every match, so nothing changes for it)."""
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 1]})
    SM.Set("top8.matches.lr1a.name", "LR1 (custom)")
    _settle()
    before = SM.revision

    keys, matches = _setsDuring(
        lambda: Top8Service.ApplyScores({"wsf2": [0, 2]}))

    assert keys == [Top8Service.MATCHES_PATH]
    assert SM.revision == before + 1
    assert matches["wsf1"]["score"] == [2, 1]
    assert matches["wsf1"]["finished"] is True
    assert matches["wsf2"]["score"] == [0, 2]
    assert matches["wsf2"]["finished"] is True
    assert matches["wf"]["slots"] == [1, 4]
    assert matches["lr1a"]["name"] == "LR1 (custom)"
    assert list(matches) == top8.MATCH_IDS
    assert SM.Get(Top8Service.MATCHES_PATH) == matches


def test_applying_every_match_gives_what_it_always_did():
    """The panel's call (every match) is unchanged by the merge."""
    _seed()
    staged = {**_allTies(), "wsf1": [2, 1], "lr1a": [0, 2], "wsf2": [1, 1]}
    # The pre-S4h rebuild: every match from `scores`, over the published.
    seeded = SM.Get(Top8Service.MATCHES_PATH)
    expected = top8.Recompute({
        matchId: {**seeded[matchId], "score": list(score),
                  "finished": score[0] != score[1]}
        for matchId, score in staged.items()})
    assert Top8Service.ApplyScores(staged) == expected


def test_an_unknown_match_is_ignored():
    _seed()
    matches = Top8Service.ApplyScores({"wsf1": [2, 0], "bogus": [1, 0]})
    assert "bogus" not in matches
    assert matches["wsf1"]["finished"] is True


@pytest.mark.parametrize("score", [
    3, None, "21", (2, 1), [1], [1, 2, 3], ["2", "1"], [True, False],
    [1.0, 2], [-1, 0], [0, Top8Service.SCORE_MAX + 1], {"0": 1, "1": 2},
])
def test_a_score_that_is_not_two_small_ints_is_refused(score):
    """Validated in core (a client's partial send reaches here, S4i): a
    bad score is refused whole -- the good ones sent with it are not
    applied either -- with no exception and nothing written."""
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf2": [2, 1]})
    _settle()
    before = (SM.revision, deep_clone(SM.Get(Top8Service.MATCHES_PATH)))

    keys, answer = _setsDuring(
        lambda: Top8Service.ApplyScores({"wsf1": [2, 0], "lr1a": score}))

    assert answer is None and keys == []
    assert (SM.revision, SM.Get(Top8Service.MATCHES_PATH)) == before


@pytest.mark.parametrize("scores", [None, [["wsf1", [2, 0]]], "wsf1"])
def test_scores_that_are_not_a_dict_are_refused(scores):
    _seed()
    keys, answer = _setsDuring(lambda: Top8Service.ApplyScores(scores))
    assert answer is None and keys == []


def test_the_scores_bounds_are_applied():
    _seed()
    matches = Top8Service.ApplyScores(
        {"wsf1": [Top8Service.SCORE_MAX, 0], "wsf2": [0, 0]})
    assert matches["wsf1"]["score"] == [Top8Service.SCORE_MAX, 0]
    assert matches["wsf2"]["finished"] is False


def test_the_service_never_mutates_the_live_matches_dict():
    """StateManager.Get returns a live reference.

    Mutating it in place is the failure mode the panel's comment warned
    about: DeepDiff would see no change and the delta would never be sent.
    """
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0]})
    live = SM.Get(Top8Service.MATCHES_PATH)

    Top8Service.ApplyScores({**_allTies(), "wsf1": [1, 2]})

    assert live is not SM.Get(Top8Service.MATCHES_PATH)


# ── 全リセット ───────────────────────────────────────────────────────────

def test_resetting_clears_every_score_and_flag():
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0], "wsf2": [2, 1]})

    matches = Top8Service.ResetMatches()

    assert set(matches) == set(top8.MATCH_IDS)
    for matchId, match in matches.items():
        assert match["score"] == [0, 0]
        assert match["finished"] is False
    # The starting slots survive a reset -- they are topology, not a result.
    assert matches["wsf1"]["slots"] == [1, 2]
    assert matches["wf"]["slots"] == [top8.TBD, top8.TBD]
    assert SM.Get("top8.matches.wsf1.score") == [0, 0]


# ── Start.gg import ──────────────────────────────────────────────────────

def test_importing_a_bracket_publishes_both_subtrees_in_one_cycle():
    _settle()
    before = SM.revision
    players = {slot: top8.DefaultPlayer() for slot in top8.PLAYER_SLOTS}
    players["1"]["name"] = "Alice"
    # FromBracket hands back matches whose slots are already derived (its
    # docstring says so), which is what lets an imported result survive the
    # Recompute below -- see the unrecomputed-seed test above for the other
    # case.
    matches = top8.Recompute(top8.DefaultState()["matches"])
    matches["wsf1"]["score"] = [2, 0]
    matches["wsf1"]["finished"] = True

    result = Top8Service.ImportBracket(players, matches)

    assert SM.revision == before + 1
    assert SM.Get("top8.players.1.name") == "Alice"
    # Recompute ran on the way in, so the imported result is already
    # propagated rather than waiting for the first edit.
    assert result["wf"]["slots"][0] == 1
    assert SM.Get("top8.matches.wf.slots")[0] == 1
