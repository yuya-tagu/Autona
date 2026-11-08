"""What the thumbnail recording (golden.json next to this file) is made of.

ADR-005 S4o moved building a match thumbnail's payload (prepare_data and
createFalseData of src/thumbnail/main_generate_thumbnail.py) and the video
title (_BuildVideoTitle of the Qt panel) into core/ThumbnailService. The
recording was made through those functions on the last tree that still had
them (f2cda760, record_golden.py); tests/test_thumbnail_service.py replays it
through the service. Both build the world from here, so this module imports
nothing of the application.

A payload scenario is the world a call sees:

- ``settings``: settings.json;
- ``state``: the state;
- ``files``: {path relative to the working directory: text}, on top of the
  two fixture templates and the bundled default banner (FILES). The fixture
  templates stand in for the shipped ones so a design change of a template
  does not touch the recording: only "the file, plus its id" is recorded;
- ``preview``, ``board``: the call's arguments;
- ``games``: None (the call was given no game assets), or the game assets a
  placeholder preview draws from: its characters and selected game, and which
  files a character has (``assets``, see CharacterAssets);
- ``locale``: the program's locale and its round names (LOCALE by default).

The translations are the identity: a recording made with no translator
installed, replayed with an identity `translate` (what a real translator
receives is checked separately, with the context and the text). The random
choices of a placeholder are the module's: `random.seed(SEED)` before every
scenario, so moving the code must keep the order of the draws.

The paths in the recording need no folding: the custom backgrounds here are
relative on purpose, and nothing else in a payload depends on the machine.
"""
import copy
import json
import os
from pathlib import Path

SEED = 20261107

LOCALE = {
    "programLocale": "en",
    "matchNames": {"winners_final": "Winners Final"},
    "phaseNames": {"group": "Group {0}"},
}

FILES = {
    "assets/thumbnail_base/thumbnail_types/type_a.json":
        '{"name": "Fixture A", '
        '"default_background": "./assets/thumbnail_base/a.png"}',
    "assets/thumbnail_base/thumbnail_types/type_b.json":
        '{"name": "Fixture B", '
        '"default_background": "./assets/thumbnail_base/b.png"}',
    "assets/thumbnail_base/default_banner.png": "default banner",
}

BANNER_FILE = {"user_data/tournament_banner.png": "banner"}
CUSTOM_BACKGROUND = "./user_data/custom background.png"


def _player(name, team="", **extra):
    merged = f"{team} | {name}" if team else name
    return dict({"name": name, "team": team, "mergedName": merged,
                 "mergedOnlyName": merged, "country": {}, "state": {}},
                **extra)


def _team(players, teamName="", score=0, losers=False):
    return {"teamName": teamName, "score": score, "losers": losers,
            "player": {str(i + 1): player
                       for i, player in enumerate(players)}}


def _board(left, right, match="Grand Final", phase="Top 8"):
    return {"best_of": 5, "match": match, "phase": phase,
            "team": {"1": left, "2": right}}


_MARIO = {"1": {"name": "Mario", "codename": "mario", "skin": 2,
                "assets": {"full": "./user_data/games/tgame/mario_2.png"}}}

WITH_PLAYERS = {
    "game": {"codename": "tgame", "name": "Test Game", "smashgg_id": 1386},
    "tournamentInfo": {"tournamentName": "Big Cup",
                       "eventName": "Singles"},
    "score": {
        "1": _board(
            _team([_player("Alpha", "SPN", character=_MARIO)], "Red", 2),
            _team([_player("Bravo")], "Blue", 1, losers=True)),
        "2": _board(
            _team([_player("Charlie"), _player("Delta")], "Team C"),
            _team([_player("Echo"), _player("Foxtrot")], "Team E"),
            match="Round 1", phase=""),
    },
}

# A game, but the second side has no player.
ONE_PLAYER = {
    "game": dict(WITH_PLAYERS["game"]),
    "score": {"1": _board(_team([_player("Alpha")]), _team([]))},
}

# No game was selected.
NO_GAME = {"score": copy.deepcopy(WITH_PLAYERS["score"])}

CHARACTERS = {
    "Mario": {"codename": "mario",
              "locale": {"ja": "マリオ", "pt_BR": "Mário"}},
    "Link": {"codename": "link", "locale": {"zh_CN": "林克"}},
    "Kirby": {"codename": "kirby"},
    "Pikachu": {"codename": "pikachu", "locale": {}},
}
GAMES = {"characters": CHARACTERS,
         "game": {"codename": "tgame", "name": "Test Game"},
         "assets": "full"}


def CharacterAssets(kind, codename, skin):
    """The files of a character's skin, by the scenario's `assets`."""
    if kind == "full":
        return {"full": f"./games/{codename}/full_{skin}.png",
                "portrait": f"./games/{codename}/portrait_{skin}.png"}
    if kind == "portrait":
        return {"portrait": f"./games/{codename}/portrait_{skin}.png"}
    if kind == "icon":
        return {"base_files/icon": f"./games/{codename}/icon_{skin}.png"}
    if kind == "pack":
        return {"costume": f"./games/{codename}/costume_{skin}.png",
                "full": f"./games/{codename}/full_{skin}.png"}
    return {}


def _settings(**config):
    return {"thumbnail_config": config}


def _scenario(name, settings=None, state=WITH_PLAYERS, files=None,
              preview=False, board=1, games=None, locale=None):
    return {"name": name, "settings": settings or {}, "state": state,
            "files": files or {}, "preview": preview, "board": board,
            "games": games, "locale": locale or LOCALE}


def _Scenarios():
    scenarios = []

    # Two templates x three background sources, with and without the banner
    # the banner source looks for.
    for template in ("type_a", "type_b"):
        for source, path in (("default", ""),
                             ("banner", ""),
                             ("custom", CUSTOM_BACKGROUND)):
            scenarios.append(_scenario(
                f"{template}_{source}",
                _settings(template_id=template, background_source=source,
                          background_path=path),
                files=BANNER_FILE))
    scenarios += [
        _scenario("banner_source_without_the_banner_file",
                  _settings(background_source="banner", background_path="")),
        _scenario("custom_source_without_a_file",
                  _settings(background_source="custom", background_path="")),
        # Set by a build from before the source selector.
        _scenario("legacy_config_with_a_path",
                  _settings(background_path=CUSTOM_BACKGROUND)),
        _scenario("legacy_config_with_the_banner_path",
                  _settings(
                      background_path="./user_data/tournament_banner.png")),
        _scenario("legacy_config_without_a_path", _settings(display_phase=True)),
        _scenario("no_settings"),
        _scenario("an_empty_config", _settings()),
        _scenario("an_unknown_template", _settings(template_id="type_z")),
    ]

    # The toggles, each way, and a game's settings, which the payload carries
    # as they are saved.
    for toggle in ("display_phase", "use_team_names", "use_sponsors"):
        for value in (True, False):
            scenarios.append(_scenario(
                f"{toggle}_{'on' if value else 'off'}",
                _settings(**{toggle: value})))
    scenarios += [
        _scenario("game_settings", _settings(
            main_icon_path="./layout/logo.png",
            game={"tgame": {"flip_p1": True, "flip_p2": False,
                            "smooth_scale": False, "zoom": 150,
                            "align": {"horizontal": 10, "vertical": 90}},
                  "None": {"zoom": 100}})),
        _scenario("colors", dict(
            _settings(template_id="type_b"),
            colors={"thumbnail": {"theme_color": "#111111",
                                  "accent_color": "#222222"}})),
        _scenario("the_second_board", board=2),
    ]

    # Where the data comes from: the state, else the file a final generation
    # falls back to, and what is refused.
    program = {"out/program_state.json": _json(WITH_PLAYERS)}
    scenarios += [
        _scenario("final_from_the_file", state={}, files=program),
        _scenario("final_from_the_file_when_a_player_is_missing",
                  state=ONE_PLAYER, files=program),
        _scenario("final_from_the_file_without_a_game", state=NO_GAME,
                  files=program),
        _scenario("final_without_a_game", state={},
                  files={"out/program_state.json": _json(NO_GAME)}),
        _scenario("final_without_the_first_player", state={},
                  files={"out/program_state.json": _json(
                      {"game": WITH_PLAYERS["game"],
                       "score": {"1": _board(_team([]), _team([]))}})}),
        _scenario("final_without_the_second_player", state={},
                  files={"out/program_state.json": _json(ONE_PLAYER)}),
        _scenario("final_without_a_file", state={}),
        _scenario("final_with_a_broken_file", state={},
                  files={"out/program_state.json": "{not json"}),
    ]

    # Previews: the real data when there is any, else a placeholder.
    scenarios += [
        _scenario("preview_with_players", preview=True, games=GAMES),
        _scenario("preview_from_the_file", state={}, files=program,
                  preview=True, games=GAMES),
        _scenario("preview_without_a_game_or_assets", state={}, preview=True),
        _scenario("preview_with_no_characters", state=NO_GAME, preview=True,
                  games=dict(GAMES, characters={})),
        _scenario("preview_placeholder", state=NO_GAME, preview=True,
                  games=GAMES),
        _scenario("preview_placeholder_portrait", state=NO_GAME, preview=True,
                  games=dict(GAMES, assets="portrait")),
        _scenario("preview_placeholder_icon", state=NO_GAME, preview=True,
                  games=dict(GAMES, assets="icon")),
        _scenario("preview_placeholder_no_asset", state=NO_GAME, preview=True,
                  games=dict(GAMES, assets="none")),
        _scenario("preview_placeholder_asset_pack", state=ONE_PLAYER,
                  preview=True, games=dict(GAMES, assets="pack"),
                  settings=_settings(game={"tgame": {"asset_pack": "costume"}})),
        _scenario("preview_placeholder_exact_locale", state=NO_GAME,
                  preview=True, games=GAMES,
                  locale=dict(LOCALE, programLocale="pt-BR")),
        _scenario("preview_placeholder_base_locale", state=NO_GAME,
                  preview=True, games=GAMES,
                  locale=dict(LOCALE, programLocale="ja_JP")),
        _scenario("preview_placeholder_other_board", state=NO_GAME,
                  preview=True, games=GAMES, board=2),
    ]
    return scenarios


def _json(value):
    return json.dumps(value, ensure_ascii=False)


SCENARIOS = _Scenarios()


def _titleState(tournament="Big Cup", match_en="Grand Final", game=None,
                left=None, right=None, board="1"):
    state = {"score": {board: {"match_en": match_en, "team": {
        "1": {"player": {"1": left or {}}},
        "2": {"player": {"1": right or {}}}}}}}
    if tournament is not None:
        state["tournamentInfo"] = {"tournamentName": tournament}
    if game is not None:
        state["game"] = game
    return state


_ALPHA = {"mergedName": "SPN | Alpha", "gamerTag": "Alpha",
          "character": {"1": {"name": "Mario"}}}
_BRAVO = {"gamerTag": "Bravo", "character": {"1": {"name": "Link"}}}
_SSBU = {"abbreviation": "SSBU", "smashgg_id": 1386}

TITLE_SCENARIOS = [
    {"name": "everything", "board": 1, "state": _titleState(
        game=_SSBU, left=_ALPHA, right=_BRAVO)},
    {"name": "the_abbreviation_table", "board": 1, "state": _titleState(
        game={"smashgg_id": 43868}, left=_ALPHA, right=_BRAVO)},
    {"name": "an_id_given_as_a_string", "board": 1, "state": _titleState(
        game={"smashgg_id": "1386"}, left=_ALPHA, right=_BRAVO)},
    {"name": "an_unknown_game", "board": 1, "state": _titleState(
        game={"smashgg_id": 7}, left=_ALPHA, right=_BRAVO)},
    {"name": "a_broken_game_id", "board": 1, "state": _titleState(
        game={"smashgg_id": "x"}, left=_ALPHA, right=_BRAVO)},
    {"name": "no_game", "board": 1, "state": _titleState(
        left=_ALPHA, right=_BRAVO)},
    {"name": "the_losers_mark_goes", "board": 1, "state": _titleState(
        game=_SSBU,
        left={"mergedName": "SPN | Alpha [L]"}, right=_BRAVO)},
    {"name": "a_tag_without_a_merged_name", "board": 1, "state": _titleState(
        game=_SSBU, left={"gamerTag": "Alpha"}, right={"gamerTag": "Bravo"})},
    {"name": "only_a_character", "board": 1, "state": _titleState(
        game=_SSBU, left={"character": {"1": {"name": "Mario"}}},
        right={"character": {"1": {"name": "Link"}}})},
    {"name": "the_mains_when_no_character", "board": 1, "state": _titleState(
        game=_SSBU,
        left={"gamerTag": "Alpha", "character": {"1": {"name": ""}},
              "mains": {"1": {"name": "Kirby"}}},
        right={"gamerTag": "Bravo"})},
    {"name": "the_lowest_slot_number", "board": 1, "state": _titleState(
        game=_SSBU,
        left={"gamerTag": "Alpha", "character": {
            "10": {"name": "Link"}, "2": {"name": "Mario"}}},
        right={"gamerTag": "Bravo"})},
    {"name": "slots_that_are_not_numbers", "board": 1, "state": _titleState(
        game=_SSBU,
        left={"gamerTag": "Alpha", "character": {
            "b": {"name": "Link"}, "a": {"name": "Mario"}}},
        right={"gamerTag": "Bravo"})},
    {"name": "one_side_only", "board": 1, "state": _titleState(
        game=_SSBU, left=_ALPHA)},
    {"name": "the_other_side_only", "board": 1, "state": _titleState(
        game=_SSBU, right=_BRAVO)},
    {"name": "no_players", "board": 1, "state": _titleState(game=_SSBU)},
    {"name": "no_round", "board": 1, "state": _titleState(
        match_en="", game=_SSBU, left=_ALPHA, right=_BRAVO)},
    {"name": "no_tournament", "board": 1, "state": _titleState(
        tournament=None, game=_SSBU, left=_ALPHA, right=_BRAVO)},
    {"name": "only_the_round", "board": 1, "state": _titleState(
        tournament="", match_en="Round 1")},
    {"name": "only_the_game", "board": 1, "state": _titleState(
        tournament="", match_en="", game=_SSBU)},
    {"name": "nothing", "board": 1, "state": {}},
    {"name": "another_board", "board": 2, "state": _titleState(
        game=_SSBU, left=_ALPHA, right=_BRAVO, board="2")},
]


def Install(directory, scenario):
    """Write the scenario's files under `directory`."""
    for relative, text in {**FILES, **scenario["files"]}.items():
        target = Path(directory) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    os.makedirs(Path(directory) / "user_data", exist_ok=True)
