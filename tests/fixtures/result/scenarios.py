"""What the result recording (golden.json next to this file) is made of.

ADR-005 S4q moved the result image's payload (prepare_data of
src/result/main_generate_result.py), the values the Qt panel made of its
inputs, its player DB records, its post text and the output's name into
core/ResultService. The recording was made through the old functions on the
last tree that had them all (bd5b9b97, record_golden.py);
tests/test_result_service.py replays it through the service. Both build the
world from here, so this module imports nothing of the application.

Five sets of scenarios, each a section of golden.json:

- ``SCENARIOS`` (``payloads``): a state -> what the template is given, for the
  six layouts, singles and teams, with data and with the sample, the colours
  and the background. The rows are in the shapes the panel wrote. The teams'
  are in ``result_players``, where the panel wrote them: the replay brings the
  state through StateMigrations first, which makes every team scenario a proof
  of the migration too.
- ``INPUT_SCENARIOS`` (``inputs``): what the panel's input fields held (a pick
  is an en_name and a skin row) -> what the template is given. The old side
  drove _syncPlayersToState / _syncTeamsToState with stand-ins for the fields,
  whose combos answer with the rows of ``GAME``; the new side writes the same
  picks by their identifiers. Only what the templates read is compared, as the
  value's shape is what S4q changes (decision 7).
- ``RECORD_SCENARIOS`` (``records``): the same inputs -> what the player DB was
  given (_persistPlayersToDB).
- ``POST_SCENARIOS`` (``posts``): a state -> the X post's text
  (_buildResultPostText).
- ``NAME_SCENARIOS`` (``names``): a tournament -> the saved file's name
  (save_result), at the fixed time ``NOW``.

The sample's characters were drawn with ``random``. They are not any more
(decision 13), so the recording fixes the old draws to what the new code does:
every character in the table's order, on its first skin (FixedDraws). A
recording of random draws could not be replayed at all. What else differs from
the recording, on purpose, is listed in tests/test_result_service.py
(DIFFERENCES) with the reason.
"""
import copy
from pathlib import Path

NOW = (2026, 11, 9, 1, 2, 3)

LOCALE = "en"

# The files a scenario may ask for.
BANNER_FILE = {"user_data/tournament_banner.png": "banner"}
CUSTOM_BACKGROUND = "./user_data/custom background.png"


# ── the game ─────────────────────────────────────────────────────────────

def _assets(codename, skin):
    return {"full": {"asset": f"./games/{codename}/full_{skin}.png"},
            "base_files/icon": {"asset": f"./games/{codename}/icon_{skin}.png"}}


def _row(en_name, codename, name=None):
    """A character combo's row, as the catalog publishes it."""
    return {"name": name or en_name, "en_name": en_name,
            "display_name": name or en_name, "codename": codename,
            "modded": False}


def _skins(codename, count):
    """A skin combo's rows. (skin, label, data)."""
    return [(skin, str(skin), {"assets": _assets(codename, skin)})
            for skin in range(count)]


GAME = {
    "game": {"codename": "tgame", "name": "Test Game"},
    "characters": {
        "Mario": {"codename": "mario", "en_name": "Mario",
                  "locale": {"ja": "マリオ", "pt_BR": "Mário"}},
        "Link": {"codename": "link", "en_name": "Link",
                 "locale": {"zh_CN": "林克"}},
        "Kirby": {"codename": "kirby", "en_name": "Kirby"},
        "Pikachu": {"codename": "pikachu", "en_name": "Pikachu", "locale": {}},
    },
    "characterRows": [
        ("Mario", _row("Mario", "mario")),
        ("Link", _row("Link", "link")),
        ("Kirby", _row("Kirby", "kirby")),
        ("Pikachu", _row("Pikachu", "pikachu")),
    ],
    "skinRows": {
        "Mario": _skins("mario", 3),
        "Link": _skins("link", 2),
        # Its skins have no files.
        "Kirby": [(0, "0", {"assets": {}})],
        "Pikachu": _skins("pikachu", 2),
    },
    "assets": "full",
}


def CharacterAssets(kind, codename, skin):
    """The files of a character's skin, by the scenario's `assets`."""
    if kind == "full":
        return {"full": f"./games/{codename}/full_{skin}.png",
                "portrait": f"./games/{codename}/portrait_{skin}.png"}
    if kind == "portrait":
        return {"portrait": f"./games/{codename}/portrait_{skin}.png"}
    if kind == "none":
        return {}
    raise AssertionError(kind)


class FixedDraws:
    """The old sample's `random`, with its draws fixed to what the new code
    does. `randint` is asked for a character and then a skin in turn by the
    singles' sample, and only for a character by the teams'."""

    def __init__(self, team):
        self._team = team
        self._character = 0
        self._isSkin = False

    def randint(self, low, high):
        if not self._team and self._isSkin:
            self._isSkin = False
            return 0
        self._isSkin = True
        draw = self._character % (high - low + 1)
        self._character += 1
        return low + draw


# ── rows as the panel wrote them ─────────────────────────────────────────

def _skinValue(codename, skin):
    """The panel's value for a pick with a skin: the skin row's data."""
    return {"assets": _assets(codename, skin)}


def _character(codename, skin):
    """A value with everything: what the scoreboard's slots hold."""
    return {"name": codename.title(), "en_name": codename.title(),
            "display_name": codename.title(), "codename": codename,
            "modded": False, "assets": _assets(codename, skin), "skin": skin,
            "variant": {}}


def _single(rank, name, sponsor="", twitter="", *picks):
    values = list(picks)
    return {"rank": rank, "name": name, "sponsor": sponsor, "twitter": twitter,
            "character": values[0] if values else {},
            "characters": [value for value in values if value]}


def _empty(rank):
    return _single(rank, "")


def _singles(count=16, named=8):
    rows = {}
    for index in range(count):
        rank = index + 1
        if index < named:
            codename = ("mario", "link", "pikachu")[index % 3]
            rows[str(rank)] = _single(
                rank, f"Player{rank}", "SPN" if index % 2 else "",
                "", _skinValue(codename, index % 2))
        else:
            rows[str(rank)] = _empty(rank)
    return rows


def _team(rank, name, *members):
    return {"rank": rank, "name": name, "sponsor": "",
            "members": [{"name": member, "character": _skinValue("link", 1)}
                        if isinstance(member, str) else member
                        for member in members]}


def _teams():
    """The panel's sixteen team rows."""
    rows = {}
    for index in range(16):
        rank = index + 1
        if index < 8:
            rows[str(rank)] = _team(rank, f"Team{rank}", f"A{rank}", f"B{rank}",
                                    {"name": f"C{rank}", "character": {}})
        else:
            rows[str(rank)] = _team(rank, "")
    return rows


TOURNAMENT = {"tournamentName": "Big Cup", "eventName": "Singles",
              "startAt": "2026-11-09", "numEntrants": 128}


def _state(players=None, tournament=TOURNAMENT, **more):
    state = {}
    if tournament is not None:
        state["tournamentInfo"] = copy.deepcopy(tournament)
    if players is not None:
        state["result_players"] = copy.deepcopy(players)
    state.update(copy.deepcopy(more))
    return state


SINGLES_LAYOUTS = ("horizontal", "horizontal_16", "vertical", "vertical_geki")
TEAM_LAYOUTS = ("team", "team_split")
LAYOUTS = SINGLES_LAYOUTS + TEAM_LAYOUTS


def _scenario(name, layout="horizontal", state=None, settings=None, files=None,
              preview=False, games=None, locale=LOCALE):
    return {"name": name, "layout": layout,
            "state": _state() if state is None else state,
            "settings": settings or {}, "files": files or {},
            "preview": preview, "games": games, "locale": locale}


def _thumbnailConfig(**config):
    return {"thumbnail_config": config}


def _Scenarios():
    scenarios = []

    # The six layouts: with data, without data (a blank image, which only the
    # old code ever saved: the route refuses it now, the payload is the same)
    # and with the sample.
    for layout in LAYOUTS:
        rows = _teams() if layout in TEAM_LAYOUTS else _singles()
        scenarios += [
            _scenario(f"{layout}_with_data", layout, _state(rows)),
            _scenario(f"{layout}_preview_with_data", layout, _state(rows),
                      preview=True, games=GAME),
            _scenario(f"{layout}_without_data", layout, _state()),
            _scenario(f"{layout}_sample", layout, _state(), preview=True,
                      games=GAME),
            _scenario(f"{layout}_sample_no_game", layout, _state(),
                      preview=True),
        ]

    scenarios += [
        # All sixteen rows, and what the layouts of eight leave out.
        _scenario("sixteen_rows", "horizontal_16", _state(_singles(16, 16))),
        _scenario("eight_of_sixteen_rows", "horizontal",
                  _state(_singles(16, 16))),
        _scenario("no_rows_yet", "horizontal", _state(None)),
        # What a row can hold.
        _scenario("ranks_given_by_hand", "horizontal", _state({
            "1": _single(3, "Third", "", "", _skinValue("mario", 0)),
            "2": _single(1, "First", "", "", _skinValue("link", 0)),
            "3": _single(2, "Second"),
        })),
        _scenario("a_row_without_a_rank", "horizontal", _state(
            {"1": {"name": "Alpha"}, "3": {"name": "Bravo", "rank": 9}})),
        _scenario("a_row_with_a_character_only", "horizontal", _state(
            {"1": {"rank": 1, "name": "Alpha",
                   "character": _skinValue("mario", 1)}})),
        _scenario("three_characters", "horizontal", _state({"1": _single(
            1, "Alpha", "SPN", "", _skinValue("mario", 0),
            _skinValue("link", 0), _skinValue("pikachu", 1))})),
        _scenario("a_full_value", "horizontal", _state({"1": _single(
            1, "Alpha", "", "", _character("mario", 2))})),
        # A second pick without a first one.
        _scenario("no_first_character", "horizontal", _state({"1": _single(
            1, "Alpha", "", "", {}, _skinValue("link", 1))})),
        # Only a sponsor or a character is no name: still the sample.
        _scenario("only_a_sponsor", "horizontal", _state(
            {"1": _single(1, "", "SPN")}), preview=True, games=GAME),
        # The team's sample rule.
        _scenario("a_team_with_only_a_name", "team", _state(
            {"1": _team(1, "Only")}), preview=True, games=GAME),
        _scenario("a_team_with_only_members", "team", _state(
            {"1": _team(1, "", "A1")}), preview=True, games=GAME),
        _scenario("a_team_with_an_empty_member", "team_split", _state(
            {"1": _team(1, "Reds", "A1", {"name": "B1", "character": {}})})),

        # The tournament's fields.
        _scenario("no_tournament", "horizontal",
                  _state(_singles(), tournament=None)),
        _scenario("an_empty_tournament", "horizontal", _state(
            _singles(), tournament={"tournamentName": "", "eventName": "",
                                    "startAt": None, "numEntrants": 0})),

        # The sample's words and files.
        _scenario("sample_exact_locale", "horizontal", _state(), preview=True,
                  games=GAME, locale="pt-BR"),
        _scenario("sample_base_locale", "horizontal", _state(), preview=True,
                  games=GAME, locale="ja_JP"),
        _scenario("sample_portrait", "horizontal", _state(), preview=True,
                  games=dict(GAME, assets="portrait")),
        _scenario("sample_no_asset", "horizontal", _state(), preview=True,
                  games=dict(GAME, assets="none")),
        _scenario("sample_no_characters", "horizontal", _state(), preview=True,
                  games=dict(GAME, characters={})),
        _scenario("team_sample_portrait", "team", _state(), preview=True,
                  games=dict(GAME, assets="portrait")),
        _scenario("team_sample_no_asset", "team", _state(), preview=True,
                  games=dict(GAME, assets="none")),

        # The colours.
        _scenario("colors_of_the_result", "horizontal", _state(_singles()),
                  settings={"colors": {"result": {
                      "theme_color": "#111111", "accent_color": "#222222"}}}),
        _scenario("colors_of_the_tournament", "team", _state(_teams()),
                  settings={"colors": {"tournament": {
                      "theme_color": "#333333", "accent_color": "#444444"}}}),
        _scenario("colors_of_both", "vertical", _state(_singles()),
                  settings={"colors": {
                      "result": {"accent_color": "#222222"},
                      "tournament": {"theme_color": "#333333",
                                     "accent_color": "#444444"}}}),

        # The background: the thumbnail's.
        _scenario("background_default", settings=_thumbnailConfig(
            background_source="default", background_path="")),
        _scenario("background_banner", settings=_thumbnailConfig(
            background_source="banner", background_path=""),
            files=BANNER_FILE),
        _scenario("background_banner_without_the_file",
                  settings=_thumbnailConfig(
                      background_source="banner", background_path="")),
        _scenario("background_custom", settings=_thumbnailConfig(
            background_source="custom", background_path=CUSTOM_BACKGROUND)),
        _scenario("background_custom_without_a_file",
                  settings=_thumbnailConfig(
                      background_source="custom", background_path="")),
        _scenario("background_legacy_path", settings=_thumbnailConfig(
            background_path=CUSTOM_BACKGROUND)),
        _scenario("background_legacy_banner_path", settings=_thumbnailConfig(
            background_path="./user_data/tournament_banner.png"),
            files=BANNER_FILE),
        _scenario("background_legacy_banner_path_without_the_file",
                  settings=_thumbnailConfig(
                      background_path="./user_data/tournament_banner.png")),
        _scenario("background_legacy_default_banner_path",
                  settings=_thumbnailConfig(
                      background_path="./assets/thumbnail_base/default_banner.png")),
        _scenario("no_thumbnail_config"),

        # result_config, which nothing in the app writes.
        _scenario("result_config_paths", settings={"result_config": {
            "main_icon_path": "./layout/own_logo.png",
            "background_path": "./user_data/own_background.png"}}),
        _scenario("result_config_empty_background", settings={"result_config": {
            "background_path": ""}}),
        _scenario("result_config_other_keys", settings={"result_config": {
            "logo_path": "./layout/unused.png", "show_rank": True}}),
    ]
    return scenarios


SCENARIOS = _Scenarios()


# ── what the input fields held ───────────────────────────────────────────

def _input(name, sponsor="", twitter="", picks=(), rank=None):
    """One singles row of the panel: up to three (en_name, skin row) picks;
    None for a blank one. A skin row of None: the colour combo is empty."""
    return {"rank": rank, "name": name, "sponsor": sponsor, "twitter": twitter,
            "picks": list(picks)}


def _member(name, pick=None):
    return {"name": name, "pick": pick}


def _teamInput(name, members=(), rank=None):
    return {"rank": rank, "name": name, "members": list(members)}


_ALPHA_ON_THE_BOARD = {"score": {"1": {"team": {
    "1": {"player": {
        "1": {"name": "alpha", "character": {
            "2": _character("link", 1), "1": _character("mario", 2)}}}},
    "2": {"player": {
        "1": {"name": "Bravo ", "character": {"1": _character("kirby", 0)}},
        "2": {"name": "Nochar", "character": {}},
        "3": {"name": "Blank", "character": {"1": {}}}}},
}}}}

INPUT_SCENARIOS = [
    {"name": "singles", "layout": "horizontal", "state": {}, "rows": [
        _input("Alpha", "SPN", "alpha_x", [("Mario", 2), ("Link", 0),
                                           ("Pikachu", 1)]),
        _input("Bravo", "", "", [("Link", 1)], rank=5),
        _input("Charlie"),
    ]},
    {"name": "a_skin_without_files", "layout": "horizontal", "state": {},
     "rows": [_input("Alpha", picks=[("Kirby", 0), ("Link", 0)])]},
    # No colour combo: the panel used the character's row, which has no assets.
    {"name": "a_character_row_alone", "layout": "horizontal", "state": {},
     "rows": [_input("Alpha", picks=[("Mario", None)])]},
    {"name": "second_pick_only", "layout": "horizontal", "state": {},
     "rows": [_input("Alpha", picks=[None, ("Link", 1)])]},
    {"name": "from_the_scoreboard", "layout": "horizontal",
     "state": _ALPHA_ON_THE_BOARD, "rows": [
         # Trimmed and compared in lower case; the board's lowest slot.
         _input("ALPHA"),
         _input("Bravo"),
         # A pick of its own wins.
         _input("alpha ", picks=[("Pikachu", 1)]),
         # The board has the player, without a character.
         _input("Nochar"),
         _input("Blank"),
         # Not on the board.
         _input("Charlie"),
         # A second pick does not stop the first being looked up.
         _input("alpha", picks=[None, ("Pikachu", 0)]),
     ]},
    {"name": "teams", "layout": "team", "state": {}, "rows": [
        _teamInput("Reds", [_member("A1", ("Mario", 2)), _member("B1"),
                            _member("", ("Link", 1)), _member("")], rank=3),
        _teamInput("", [_member("A2", ("Pikachu", 0))]),
        _teamInput("Only a name"),
    ]},
    {"name": "teams_from_the_scoreboard", "layout": "team_split",
     "state": _ALPHA_ON_THE_BOARD, "rows": [
         _teamInput("Reds", [_member("ALPHA"), _member("Bravo"),
                             _member("Charlie"), _member("Nochar"),
                             _member("alpha", ("Pikachu", 1))]),
     ]},
]

RECORD_SCENARIOS = [
    {"name": "singles_records", "layout": "horizontal", "game": "tgame",
     "rows": [
         _input("Alpha", "SPN", "alpha_x", [("Mario", 2), ("Link", 0),
                                            ("Pikachu", 1)]),
         _input("Bravo", picks=[("Link", 1)]),
         _input("Charlie", "SPN"),
         _input("", "NoName", "noname", [("Mario", 0)]),
         _input("Delta", picks=[None, ("Pikachu", 1)]),
     ]},
    {"name": "singles_records_without_a_game", "layout": "horizontal",
     "game": "", "rows": [_input("Alpha", picks=[("Mario", 2)])]},
    {"name": "team_records", "layout": "team", "game": "tgame", "rows": [
        _teamInput("Reds", [_member("A1", ("Mario", 2)), _member("B1"),
                            _member("", ("Link", 1))]),
        _teamInput("Blues", [_member("A2", ("Pikachu", 1))]),
    ]},
    {"name": "no_records", "layout": "horizontal", "game": "tgame",
     "rows": [_input(""), _input("", "SPN")]},
]


# ── the post's text ──────────────────────────────────────────────────────

_DB = {"Charlie": {"twitter": "@charlie_db"},
       "SPN Delta": {"twitter": "delta_prefixed"},
       "Delta": {"twitter": "delta_plain"},
       "Echo": {"twitter": ""},
       "B1": {"twitter": "b1_db"}}

_POST_SINGLES = {
    "1": _single(1, "Alpha", "SPN", "@alpha_x"),
    "2": _single(2, "Bravo", "", " bravo_x "),
    "3": _single(3, "Charlie"),
    "4": _single(4, "Delta", "SPN"),
    "5": _single(5, "Echo"),
    "6": _single(5, "Foxtrot"),
}


def _teamRow(rank, name, *members):
    """A team as the state holds it: each member's X handle is in the row
    (the old panel kept what the standings said in memory: _standingsTwitter,
    which the recording is given)."""
    return {"rank": rank, "name": name, "sponsor": "",
            "members": [{"name": member, "character": {},
                         "twitter": handle}
                        for member, handle in members]}


POST_SCENARIOS = [
    {"name": "singles_post", "layout": "horizontal", "db": _DB,
     "settings": {"TOURNAMENT_URL": "https://start.gg/x"},
     "state": _state(_POST_SINGLES, stream={"url": "https://twitch.tv/x"})},
    {"name": "only_the_players", "layout": "horizontal", "db": {}, "settings": {},
     "state": _state({"1": _single(1, "Alpha")}, tournament=None)},
    {"name": "empty_rows_are_skipped", "layout": "horizontal", "db": {},
     "settings": {}, "state": _state({
         "1": _single(1, ""), "2": _single(2, "Bravo"),
         "3": _single(3, ""), "4": _single(4, "Delta")})},
    {"name": "no_entrants", "layout": "horizontal", "db": {}, "settings": {},
     "state": _state({"1": _single(1, "Alpha")}, tournament={
         "tournamentName": "Cup", "eventName": "", "startAt": "2026-11-09",
         "numEntrants": 0})},
    {"name": "nothing", "layout": "horizontal", "db": {}, "settings": {},
     "state": {}},
    {"name": "team_post", "layout": "team", "db": _DB,
     "settings": {"TOURNAMENT_URL": "https://start.gg/x"},
     "state": _state({
         "1": _teamRow(1, "Reds", ("A1", "@a1_x"), ("B1", ""), ("", "")),
         "2": _teamRow(2, "", ("A2", "")),
         "3": _teamRow(3, "Greens"),
         "4": _teamRow(4, "", ("", "")),
         "5": _teamRow(5, "Blues", ("A5", "")),
         "6": _teamRow(6, "Whites", ("A6", "")),
     })},
    # The order: the old code's was the slots', which decision 18 changes.
    {"name": "ranks_by_hand", "layout": "horizontal", "db": {}, "settings": {},
     "state": _state({
         "1": _single(5, "Fifth"), "2": _single(1, "First"),
         "3": _single(4, "Fourth"), "4": _single(3, "Third"),
         "5": _single(2, "Second")})},
]


# ── the file's name ──────────────────────────────────────────────────────

NAME_SCENARIOS = [
    {"name": "plain", "tournament": "Big Cup", "players": 8},
    {"name": "sixteen", "tournament": "Big Cup", "players": 16},
    {"name": "special_characters", "tournament": 'A/B\\C*D?E:F"G<H>I|J',
     "players": 8},
    {"name": "japanese", "tournament": "大会 第1回", "players": 8},
    {"name": "no_players", "tournament": "Big Cup", "players": 0},
    # Decision 12 changes this one.
    {"name": "no_tournament", "tournament": "", "players": 8},
]


def Install(directory, scenario):
    """Write the scenario's files under `directory`."""
    for relative, text in scenario["files"].items():
        target = Path(directory) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    (Path(directory) / "user_data").mkdir(parents=True, exist_ok=True)
