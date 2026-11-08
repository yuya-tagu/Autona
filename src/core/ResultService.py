"""The tournament result (大会リザルト, ADR-005 S4q).

Everything about a result image that needs no Qt, so that it also answers in
the headless shell and its page can be a React one (ADR-005 S4r, which removes
the Qt panel and src/result/main_generate_result.py):

- **the rows**, which are the state: a player's in ``result_players`` (sixteen
  of them), a team's in ``result_teams`` (eight), so that a switch of layout
  leaves the other's alone. What the operator typed stays in state.json until
  ``Clear``. One row is written at a time (``UpdatePlayer`` / ``UpdateTeam``),
  so two clients editing two rows do not undo each other; ``Reorder``, ``Clear``
  and the standings' import are one save cycle each. A character of another
  game than the one loaded is read as none and stays in the state (``_Rows``);
- **the values**: a character comes as identifiers -- an en_name and its skin
  row -- and the value is built here (``Top8Service.CharacterValue``, the player
  cards' shape), against one snapshot of the catalog, all of a row's or none.
  ``Top8Service.CharacterPick`` reads a value back, and the shapes the Qt panel
  wrote too;
- **the payload** the templates are given (``BuildPayload``), moved here from
  main_generate_result.prepare_data: tests/test_result_service.py replays what
  the old code answered (tests/fixtures/result/golden.json). A player without
  a character is drawn with the character the scoreboard has for the same name:
  looked up when drawing, never written;
- **the sample**, drawn by a preview while no row has a name. It is always the
  same one (the old code's was random, a new one per render);
- **a render in the background** (``Preview`` / ``Generate``), whose progress
  and result are the state's ``result_render``: core/RenderLane's, like the
  thumbnail's. The output is out/results' PNG, with the old names and 30
  generations. A generate without data is refused (``NO_DATA``);
- **the layout**, in settings.json (``result_config.layout``), which follows the
  battle format as the panel's did (``FollowBattleFormat``);
- **the standings' import** (``LoadStandings``), a job on the provider's worker
  whose progress is the state's ``result_standings``;
- **the X post's text** (``PostText``), in the order of the ranks, and **the
  player database** (``SaveToDb``; a generate does it too, a preview never).

While the Qt panel lives (until S4r) it goes on writing its whole table to
``result_players``, a team layout's too, from fields it never fills from the
state; its own rendering does not come here. What it wrote is read if it can
be: the rows of a team layout (they have ``members``) are not a player's, and
are left out of everything here.

The lock order is StateManager.lock -> the lane's lock -> the import's lock.
PlayerDB is called with no lock held: its change relay reaches the main thread.

``ResultService.instance`` is built by ``bootstrap_core()``; the deferral, the
shell's renderer, the translations, the locale, the output pruning and the way
to the provider are injected there, because core may not import them
(tests/test_core_qt_free.py). Import side effects: none (agent-pitfalls #1).
"""
import copy
import datetime
import re
import threading
from collections import namedtuple

from loguru import logger

from . import StateSchema as schema
from . import ThumbnailService as thumbnails
from . import Top8Service
from .Actions import AutonaActions
from .ColorService import FEATURE_RESULT, GetFeatureColors
from .DictHelper import deep_clone
from .GameAssetCatalog import GAMES_DIR, GameAssetCatalog
from .PlayerDB import PlayerDB
from .RenderLane import (  # noqa: F401  (re-exported: this module's names)
    BUSY, CANCELLED, DONE, ERROR, GENERATE, IDLE, PAYLOAD_FAILED, PREVIEW,
    RENDER_FAILED, RUNNING, SAVE_FAILED, RenderDataMissing, RenderLane)
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

CONFIG_SETTING = "result_config"

# ── the layouts ──────────────────────────────────────────────────────────

# Each a directory under layout/, with a result.html (src/result).
LAYOUT_SUBDIRS = {
    "horizontal": "result",
    "horizontal_16": "result_top16",
    "vertical": "result_vertical",
    "vertical_geki": "result_vertical_geki",
    "team": "result_team",
    "team_split": "result_team_split",
}
LAYOUT_SIZES = {
    "horizontal": (1920, 1080),
    "horizontal_16": (1920, 1080),
    "vertical": (960, 1080),
    "vertical_geki": (960, 1080),
    "team": (1920, 1080),
    "team_split": (1920, 1080),
}
# id -> its name in the "app" translation context.
LAYOUT_NAMES = {
    "horizontal": "Horizontal (1920x1080)",
    "horizontal_16": "Horizontal TOP16 (1920x1080)",
    "vertical": "Vertical (960x1080)",
    "vertical_geki": "Vertical Dark Gold 激 (960x1080)",
    "team": "Team / Crew (1920x1080)",
    "team_split": "Team / Crew 縦並び (1920x1080)",
}
DEFAULT_LAYOUT = "horizontal"
DEFAULT_TEAM_LAYOUT = "team"
TEAM_LAYOUTS = ("team", "team_split")
# What the battle format's team layouts are for.
TEAM_FORMATS = ("team", "crew")

# ── the rows ─────────────────────────────────────────────────────────────

PLAYERS = "players"
TEAMS = "teams"
ROOTS = {PLAYERS: schema.RESULT_PLAYERS_ROOT, TEAMS: schema.RESULT_TEAMS_ROOT}
# Every layout of a team shows eight, so a ninth cannot be reached.
SLOT_COUNTS = {PLAYERS: 16, TEAMS: 8}
PLAYER_SLOTS = tuple(str(slot) for slot in range(1, SLOT_COUNTS[PLAYERS] + 1))
TEAM_SLOTS = tuple(str(slot) for slot in range(1, SLOT_COUNTS[TEAMS] + 1))
MAX_CHARACTERS = 3
MAX_MEMBERS = 5
# The panel's spin box.
RANK_RANGE = (1, 999)
PLAYER_TEXT_FIELDS = ("name", "sponsor", "twitter")

# What UpdatePlayer / UpdateTeam refuse: the code (Top8Service's) and the pick
# that was refused, an (en_name, skin row, codename).
UNKNOWN_CHARACTER = Top8Service.UNKNOWN_CHARACTER
UNKNOWN_SKIN = Top8Service.UNKNOWN_SKIN
OTHER_GAME = Top8Service.OTHER_GAME
Refusal = namedtuple("Refusal", ("code", "pick"))

# ── the output ───────────────────────────────────────────────────────────

OUTPUT_DIRECTORY = "./out/results"
OUTPUT_PATTERNS = ("*.png", "*.jpg")
OUTPUT_KEEP = 30
# Not tmp/result/preview.png, which the Qt panel writes until S4r. Removed with
# the rest of ./tmp when the desktop app exits, and at construction. Served by
# GET /api/v1/result/preview: ./tmp is not one of the web server's static
# directories, and does not become one.
PREVIEW_PATH = "./tmp/result/result_preview.png"
DEFAULT_ICON_PATH = "./layout/logo.png"
# How the file is named when the tournament has no name.
NO_TOURNAMENT_STEM = "result"

# ── the render ───────────────────────────────────────────────────────────

RENDER_KIND = "result"
# What Generate answers instead of a revision, and result_render's error when a
# generate kept for after a run finds it so by then.
NO_DATA = "no_data"

# ── the standings' import ────────────────────────────────────────────────

# What LoadStandings answers instead of a revision (or BUSY).
NO_TOURNAMENT = "no_tournament"
NEEDS_CONFIRMATION = "needs_confirmation"
# result_standings' error.
FETCH_FAILED = "fetch_failed"   # start.gg did not answer, or not readably
NO_STANDINGS = "no_standings"   # it answered: the event has none yet
IMPORT_FAILED = "import_failed" # it answered: its answer did not go in the rows
CHANGED = "changed"             # the game or the tournament was switched
IMPORT_STATUS = "リザルトを取得中..."

# How many standings the post's text lists.
POST_RANKS = 4


# ── pure helpers ─────────────────────────────────────────────────────────

def IsTeamLayout(layout) -> bool:
    return layout in TEAM_LAYOUTS


def KindOf(layout) -> str:
    return TEAMS if IsTeamLayout(layout) else PLAYERS


def SlotsFor(layout) -> int:
    """How many rows `layout` draws."""
    return 16 if layout == "horizontal_16" else 8


def RemoveSpecialChars(text):
    """What may not be in a file's name. Not the thumbnail's function, which
    also takes spaces out: the names stay what they were."""
    return re.sub(r'[\\/*?:"<>|]', "", text)


def OutputStem(data, now=None):
    """The name of a saved result, without its extension:
    "<tournament>-top<N>-<UTC date and time>"."""
    tournament = RemoveSpecialChars(data.get("tournamentName") or "") \
        or NO_TOURNAMENT_STEM
    count = len(data.get("players") or []) or 8
    when = now or datetime.datetime.now(datetime.timezone.utc)
    return f"{tournament}-top{count}-{when.strftime('%Y-%m-%d-%H-%M-%S')}"


def DefaultPlayer(slot) -> dict:
    return {"rank": int(slot), "name": "", "sponsor": "", "twitter": "",
            "character": {}, "characters": []}


def DefaultTeam(slot) -> dict:
    return {"rank": int(slot), "name": "", "members": []}


def DefaultRow(kind, slot) -> dict:
    return DefaultPlayer(slot) if kind == PLAYERS else DefaultTeam(slot)


def _StoredConfig():
    """A copy of what settings.json holds: never the live dict."""
    with SettingsManager.lock:
        stored = SettingsManager.Get(CONFIG_SETTING)
        return copy.deepcopy(stored) if isinstance(stored, dict) else {}


def _StoredRows(kind) -> dict:
    """The kind's rows as the state has them, by slot: the live dicts, not to
    be written to. A row the Qt panel wrote for a team layout has `members`
    and is not a player's. What a write of a row starts from: its characters
    stay, whatever game they are of."""
    rows = StateManager.Get(ROOTS[kind])
    if not isinstance(rows, dict):
        return {}
    return {str(slot): row for slot, row in rows.items()
            if isinstance(row, dict)
            and not (kind == PLAYERS and "members" in row)}


def _LoadedSnapshot():
    """The game loaded, which the rows are read against; None without a
    catalog: nothing can be said of a value then."""
    catalog = GameAssetCatalog.instance
    return catalog.Current() if catalog is not None else None


def _OfLoadedGame(value, snapshot) -> bool:
    """Whether the character value `value` is of the game `snapshot` holds.

    A value does not say so by a key, and the ones in a saved state.json never
    will, but every picture of a game is under GAMES_DIR/<its codename>/. A
    value without any picture is of the game if the game lists it, by codename
    or en_name. Pictures from somewhere else say nothing either way."""
    assets = value.get("assets")
    owners = set()
    pictures = False
    for asset in assets.values() if isinstance(assets, dict) else ():
        path = asset.get("asset") if isinstance(asset, dict) else asset
        if isinstance(path, str):
            pictures = True
            if path.startswith(GAMES_DIR):
                owners.add(path[len(GAMES_DIR):].split("/", 1)[0])
    if pictures:
        return not owners or owners == {snapshot.codename}
    codename = value.get("codename")
    en_name = value.get("en_name")
    return any((codename and row.get("codename") == codename)
               or (en_name and row.get("en_name") == en_name)
               for _text, row in snapshot.characterRows)


def _Kept(value, snapshot):
    """A stored character as it is read: {} when it is of another game. The
    rows are emptied when the game is switched (ForgetCharacters), but not when
    the app is started on another one: `game` is empty until then, and what was
    saved keeps its characters. Nothing is written: back on its game, it is
    there again."""
    if snapshot is None or not isinstance(value, dict) or not value \
            or _OfLoadedGame(value, snapshot):
        return value
    return {}


def _AsRead(kind, row, snapshot) -> dict:
    """`row` with its characters as they are read (_Kept). A copy, sharing what
    it did not change with the state."""
    if kind == PLAYERS:
        others = row.get("characters")
        return dict(
            row, character=_Kept(row.get("character"), snapshot),
            characters=[_Kept(value, snapshot) for value in others]
            if isinstance(others, list) else others)
    members = row.get("members")
    if not isinstance(members, list):
        return dict(row)
    return dict(row, members=[
        dict(member, character=_Kept(member.get("character"), snapshot))
        if isinstance(member, dict) else member for member in members])


def _Rows(kind) -> dict:
    """The kind's rows as they are read, by slot: what is shown, drawn, posted
    and saved in the player database. Not to be written to, nor written back."""
    snapshot = _LoadedSnapshot()
    return {slot: _AsRead(kind, row, snapshot)
            for slot, row in _StoredRows(kind).items()}


def _PlayerCharacters(row) -> list:
    """The values of a player's character fields, in order: the first is
    `character` ({} when empty), the others come after it. `characters` has
    them to the last one that is not empty, so it starts with the first unless
    that is empty."""
    first = row.get("character")
    first = first if isinstance(first, dict) else {}
    others = row.get("characters")
    others = [value for value in others if isinstance(value, dict)] \
        if isinstance(others, list) else []
    if first and others and others[0] == first:
        others = others[1:]
    return ([first] + others)[:MAX_CHARACTERS]


def _Members(row) -> list:
    members = row.get("members")
    return [member for member in members if isinstance(member, dict)] \
        if isinstance(members, list) else []


def _MemberHasInput(member) -> bool:
    return bool(member.get("name") or member.get("character"))


def _RowHasInput(kind, row) -> bool:
    if kind == PLAYERS:
        return bool(row.get("name") or row.get("sponsor") or row.get("twitter")
                    or any(_PlayerCharacters(row)))
    return bool(row.get("name")
                or any(_MemberHasInput(member) or member.get("twitter")
                       for member in _Members(row)))


def _RowHasData(kind, row) -> bool:
    """What makes a row something to draw: a player's name; a team's name, or
    any member (decision 13: a team of members without a team name used to be
    drawn as the sample)."""
    if kind == PLAYERS:
        return bool(row.get("name"))
    return bool(row.get("name")
                or any(_MemberHasInput(member) for member in _Members(row)))


def _RankKey(row, slot):
    """Sorts rows by their rank, then their slot. A row whose rank is not a
    number (nothing writes one, a hand-edited file might) comes last."""
    rank = row.get("rank", int(slot))
    number = rank if isinstance(rank, int) and not isinstance(rank, bool) \
        else RANK_RANGE[1] + 1
    return number, int(slot)


def _FromScoreboard(name) -> dict:
    """The character the scoreboard has for the player called `name`: the
    first of the first such player with any; {} when there is none. A name is
    compared trimmed and in lower case. Read from the state, what has not been
    published yet too, as the form shows it."""
    wanted = name.strip().lower() if isinstance(name, str) else ""
    if not wanted:
        return {}
    boards = StateManager.Get(schema.SCORE_ROOT)
    boards = boards if isinstance(boards, dict) else {}
    for board in boards.values():
        teams = board.get("team") if isinstance(board, dict) else None
        teams = teams if isinstance(teams, dict) else {}
        for team in ("1", "2"):
            side = teams.get(team)
            players = side.get("player") if isinstance(side, dict) else None
            players = players if isinstance(players, dict) else {}
            for player in players.values():
                if not isinstance(player, dict):
                    continue
                tag = player.get("name")
                if not isinstance(tag, str) or tag.strip().lower() != wanted:
                    continue
                slots = player.get("character")
                if isinstance(slots, dict) and slots:
                    # The first slot in the order of its key, as it always was;
                    # a player found with an empty one ends the search too.
                    for key in sorted(slots, key=str):
                        if slots[key]:
                            return _Kept(slots[key], _LoadedSnapshot())
                    return {}
    return {}


def _PayloadPlayer(index, row) -> dict:
    name = row.get("name", "")
    character = row.get("character")
    character = character if isinstance(character, dict) else {}
    characters = row.get("characters")
    # A template draws what is in the list: an empty field keeps its place on
    # the page, not on the picture.
    characters = [value for value in characters if value] \
        if isinstance(characters, list) else [character] if character else []
    if not character and name:
        character = _FromScoreboard(name)
        if character:
            characters = [character] + list(characters)
    return {
        "rank": row.get("rank", index),
        "name": name,
        "sponsor": row.get("sponsor", ""),
        "character": character,
        "characters": characters,
        "members": [],
    }


def _PayloadTeam(index, row) -> dict:
    members = []
    for member in _Members(row):
        name = member.get("name", "")
        character = member.get("character")
        if not isinstance(character, dict):
            character = {}
        if not character and name:
            character = _FromScoreboard(name)
        # A member is drawn when it has a name or a fighter. Not its X handle:
        # a template has no use for it.
        if name or character:
            members.append({"name": name, "character": character})
    return {
        "rank": row.get("rank", index),
        "name": row.get("name", ""),
        "sponsor": row.get("sponsor", ""),
        "character": {},
        "characters": [],
        "members": members,
    }


def _Background():
    """The result has no background of its own: the thumbnail's (decision 15).
    The banner keeps its path when replaced, and a template shows the file it
    has already shown for a URL it has seen in the same document: the version
    in the URL tells them apart (a query, which the file's URL ignores)."""
    path = thumbnails.EffectiveBackgroundPath() or thumbnails.DEFAULT_BANNER_PATH
    if path == thumbnails.BANNER_PATH:
        version = StateManager.Get(
            f"{schema.TOURNAMENT_INFO_ROOT}"
            f".{schema.TOURNAMENT_BANNER_VERSION_KEY}")
        if isinstance(version, int) and not isinstance(version, bool) \
                and version > 0:
            return f"{path}?v={version}"
    return path


def _Config() -> dict:
    """What the templates read of the settings. `result_config`'s two paths are
    read as they always were (no client writes them: a path would have the
    render read any local file), its other keys are not passed on."""
    settings = _StoredConfig()
    icon = settings.get("main_icon_path")
    background = settings.get("background_path")
    theme, accent = GetFeatureColors(FEATURE_RESULT)
    return {
        "main_icon_path": icon if isinstance(icon, str) else DEFAULT_ICON_PATH,
        "background_path": background if isinstance(background, str)
        else _Background(),
        "theme_color": theme,
        "accent_color": accent,
    }


def _Catalog(games):
    return games if games is not None else GameAssetCatalog.instance


def _PickValue(pick, catalog, snapshot):
    """(the value of `pick`, an (en_name, skin row, codename), None), or (None,
    the Refusal). `snapshot` is `catalog`'s, the one every pick of a write is
    resolved against."""
    en_name, skin, codename = pick
    if catalog is None or snapshot is None:
        return None, Refusal(UNKNOWN_CHARACTER, pick)
    if codename is not None and (snapshot.codename or "") != codename:
        return None, Refusal(OTHER_GAME, pick)
    value = Top8Service.CharacterValue(en_name, skin, catalog, snapshot)
    if not value:
        return None, Refusal(UNKNOWN_CHARACTER, pick)
    # -1: a character without skins, whatever was asked.
    if value.get("skin") not in (skin, -1):
        return None, Refusal(UNKNOWN_SKIN, pick)
    return value, None


def _SetRow(kind, slot, row):
    root = ROOTS[kind]
    # One write of the row, so one save cycle. A root the Qt panel never wrote
    # is made by it.
    StateManager.SetIfChanged(f"{root}.{slot}", row)


def _ReadPlayer(slot) -> dict:
    """A copy of the row, whatever a key of it was missing filled in."""
    row = DefaultPlayer(slot)
    row.update(deep_clone(_StoredRows(PLAYERS).get(slot, {})))
    return row


def _ReadTeam(slot) -> dict:
    row = DefaultTeam(slot)
    row.update(deep_clone(_StoredRows(TEAMS).get(slot, {})))
    return row


def _Clean(text) -> str:
    return text.strip() if isinstance(text, str) else ""


# ── the rows' writes ─────────────────────────────────────────────────────

def UpdatePlayer(slot, fields, games=None):
    """Some of one player's fields, in one save cycle (PATCH
    /api/v1/result/players/<n>). `fields` holds some of: ``rank`` (an int);
    ``name`` / ``sponsor`` / ``twitter`` (str, stored trimmed); ``characters``
    (up to MAX_CHARACTERS of (en_name, skin row, codename) or None for an empty
    one, which keeps its place; the codename is the game whose list it was
    picked on, None not to check it; the whole list is replaced).

    Under the state lock, every pick is resolved first, against one
    ``GameAssetCatalog.Current()``, and the values written are the ones
    resolved. Nothing is written when one is refused: the answer is a Refusal.
    Else None. A game switch empties the characters under the same lock after
    swapping its snapshot, so a pick checked against one game is never written
    after the switch to the next.
    """
    slot = str(slot)
    if slot not in PLAYER_SLOTS:
        logger.warning(f"Result: ignoring player {slot}")
        return None
    with StateManager.lock:
        row = _ReadPlayer(slot)
        if "characters" in fields:
            catalog = _Catalog(games)
            snapshot = catalog.Current() if catalog is not None else None
            values = []
            for pick in fields["characters"]:
                if not pick:
                    values.append({})
                    continue
                value, refused = _PickValue(pick, catalog, snapshot)
                if refused is not None:
                    return refused
                values.append(value)
            while values and not values[-1]:
                values.pop()
            row["character"] = values[0] if values else {}
            # Without the first when that is empty: _PlayerCharacters puts it
            # back in front.
            row["characters"] = values if row["character"] else values[1:]
        for field in PLAYER_TEXT_FIELDS:
            if field in fields:
                row[field] = _Clean(fields[field])
        if "rank" in fields:
            row["rank"] = fields["rank"]
        _SetRow(PLAYERS, slot, row)
    return None


def UpdateTeam(slot, fields, games=None):
    """Some of one team's fields, in one save cycle (PATCH
    /api/v1/result/teams/<n>). `fields` holds some of: ``rank`` (an int);
    ``name`` (str, stored trimmed); ``members`` (up to MAX_MEMBERS of {"name",
    "twitter": str, "character": (en_name, skin row, codename) or None}: the
    whole list is replaced, an empty member keeping its place, as a page's rows
    do). Picks as in UpdatePlayer; a Refusal, or None."""
    slot = str(slot)
    if slot not in TEAM_SLOTS:
        logger.warning(f"Result: ignoring team {slot}")
        return None
    with StateManager.lock:
        row = _ReadTeam(slot)
        if "members" in fields:
            catalog = _Catalog(games)
            snapshot = catalog.Current() if catalog is not None else None
            members = []
            for member in fields["members"]:
                pick = member.get("character")
                value = {}
                if pick:
                    value, refused = _PickValue(pick, catalog, snapshot)
                    if refused is not None:
                        return refused
                members.append({"name": _Clean(member.get("name")),
                                "twitter": _Clean(member.get("twitter")),
                                "character": value})
            row["members"] = members
        if "name" in fields:
            row["name"] = _Clean(fields["name"])
        if "rank" in fields:
            row["rank"] = fields["rank"]
        _SetRow(TEAMS, slot, row)
    return None


def Reorder(kind, order) -> bool:
    """Give the first len(`order`) slots the rows of the slots `order` names
    (each of 1..len(order) once), ranked 1.. in that order: one save cycle.
    The rows after them stay as they are (the ones a layout of eight does not
    show). False, writing nothing, for anything else."""
    count = SLOT_COUNTS.get(kind)
    if count is None or not isinstance(order, (list, tuple)) \
            or not 1 <= len(order) <= count \
            or sorted(map(str, order)) != sorted(
                str(slot) for slot in range(1, len(order) + 1)):
        logger.warning(f"Result: refusing the order {order!r} of {kind!r}")
        return False
    with StateManager.lock:
        rows = _StoredRows(kind)
        written = {slot: deep_clone(rows[slot]) for slot in rows}
        for index, source in enumerate(order):
            slot = str(index + 1)
            row = DefaultRow(kind, slot)
            row.update(deep_clone(rows.get(str(source), {})))
            row["rank"] = index + 1
            written[slot] = row
        StateManager.SetIfChanged(ROOTS[kind], written)
    return True


def Clear(kind) -> bool:
    """Empty every row of `kind`, one save cycle. The other kind's stay: a
    layout of the other kind is another event's table. False for an unknown
    kind."""
    count = SLOT_COUNTS.get(kind)
    if count is None:
        logger.warning(f"Result: refusing to clear {kind!r}")
        return False
    with StateManager.lock:
        StateManager.SetIfChanged(
            ROOTS[kind],
            {str(slot): DefaultRow(kind, slot)
             for slot in range(1, count + 1)})
    return True


def HasInput(kind) -> bool:
    """Whether a row of `kind` holds anything an import would overwrite."""
    with StateManager.lock:
        return any(_RowHasInput(kind, row) for row in _Rows(kind).values())


def HasData(layout) -> bool:
    """Whether `layout` has anything to draw, in the rows it shows."""
    kind = KindOf(layout)
    with StateManager.lock:
        rows = _Rows(kind)
        return any(_RowHasData(kind, rows[slot])
                   for slot in map(str, range(1, SlotsFor(layout) + 1))
                   if slot in rows)


def ForgetCharacters():
    """Empty every character of the rows, because the loaded game changed to
    another one: a character of one game means nothing in another (they were
    drawn on until the panel was next edited, which was a bug: decision 17).
    Called by ``GameAssetCatalog.SelectGame`` as Top8Service.ForgetCharacters
    is, in the same save cycle as the switch. A row without one is not written.
    """
    with StateManager.SaveBlock():
        with StateManager.lock:
            for root in ROOTS.values():
                rows = StateManager.Get(root)
                rows = rows if isinstance(rows, dict) else {}
                for slot, row in rows.items():
                    if not isinstance(row, dict):
                        continue
                    if row.get("character"):
                        StateManager.Set(f"{root}.{slot}.character", {})
                    if row.get("characters"):
                        StateManager.Set(f"{root}.{slot}.characters", [])
                    members = _Members(row)
                    if any(member.get("character") for member in members):
                        StateManager.Set(
                            f"{root}.{slot}.members",
                            [dict(deep_clone(member), character={})
                             for member in members])


def _Placement(entry, index) -> int:
    placement = entry.get("placement")
    try:
        return int(placement) if placement else index + 1
    except (TypeError, ValueError, OverflowError):
        return index + 1


def _ImportedCharacters(player, codename, games, snapshot) -> list:
    """The values of a standing's player's mains in the loaded game, on their
    first skin as the panel's import had them; {} for a main that is empty or
    not one of the game's characters."""
    mains = player.get("mains")
    mains = mains.get(codename) if isinstance(mains, dict) and codename else None
    values = []
    for main in (mains if isinstance(mains, list) else [])[:MAX_CHARACTERS]:
        name = main[0] if isinstance(main, (list, tuple)) and main else None
        values.append(Top8Service.CharacterValue(
            Top8Service._CharacterKey(snapshot, name), 0, games, snapshot))
    return values


def _ImportedPlayer(slot, index, entry, codename, games, snapshot) -> dict:
    row = DefaultPlayer(slot)
    row["rank"] = _Placement(entry, index)
    players = entry.get("players")
    player = players[0] if isinstance(players, list) and players else None
    if isinstance(player, dict):
        row["name"] = _Clean(player.get("gamerTag"))
        row["sponsor"] = _Clean(player.get("prefix"))
        row["twitter"] = _Clean(player.get("twitter"))
        values = _ImportedCharacters(player, codename, games, snapshot)
        row["character"] = values[0] if values else {}
        row["characters"] = [value for value in values if value]
    return row


def _ImportedTeam(slot, index, entry, codename, games, snapshot) -> dict:
    row = DefaultTeam(slot)
    row["rank"] = _Placement(entry, index)
    roster = entry.get("players")
    roster = [player for player in roster if isinstance(player, dict)] \
        if isinstance(roster, list) else []
    row["name"] = _Clean(entry.get("name")) or (
        _Clean(roster[0].get("gamerTag")) if roster else "")
    for player in roster[:MAX_MEMBERS]:
        values = _ImportedCharacters(player, codename, games, snapshot)
        row["members"].append({"name": _Clean(player.get("gamerTag")),
                               "twitter": _Clean(player.get("twitter")),
                               "character": values[0] if values else {}})
    return row


def ImportStandings(kind, standings, games=None):
    """Put `standings` (StartGGDataProvider.GetStandings') in the rows of
    `kind`, in one save cycle: the first of them in the first row, the rows
    after them emptied. Resolved and written under the state lock, against the
    game loaded at that moment (Top8Service.ImportPhaseGroup): a game switch
    swaps the snapshot first and only then empties the characters, under this
    lock."""
    catalog = _Catalog(games)
    with StateManager.lock:
        snapshot = catalog.Current()
        codename = snapshot.codename or ""
        build = _ImportedPlayer if kind == PLAYERS else _ImportedTeam
        rows = {}
        for index in range(SLOT_COUNTS[kind]):
            slot = str(index + 1)
            rows[slot] = build(slot, index, standings[index], codename, catalog,
                               snapshot) \
                if index < len(standings) and isinstance(standings[index], dict) \
                else DefaultRow(kind, slot)
        StateManager.SetIfChanged(ROOTS[kind], rows)


# ── the service ──────────────────────────────────────────────────────────

class ResultService(RenderLane):
    """See the module docstring. One per process: the render lane and the
    standings' in-progress flag are its own."""

    instance: "ResultService" = None

    LOG_NAME = "Result"
    RENDER_KIND = RENDER_KIND
    RENDER_PATH = schema.RESULT_RENDER_ROOT
    OUTPUT_DIRECTORY = OUTPUT_DIRECTORY
    OUTPUT_PATTERNS = OUTPUT_PATTERNS
    OUTPUT_KEEP = OUTPUT_KEEP
    PREVIEW_PATH = PREVIEW_PATH
    MISSING_CODES = (NO_DATA,)

    def __init__(self, later, render=None, translate=None, locale=None,
                 prune=None, shuttingDown=None, fetcher=None,
                 refreshTournament=None, currentProvider=None):
        """`later`, `render`, `prune` and `shuttingDown` as RenderLane takes
        them; `render` is given (data, config, layout). `translate(context,
        text)` and `locale()` -> the program's locale are what the sample's
        words come from. `fetcher(job, status)` runs job(provider) on the
        provider's worker thread and returns True, or returns False, running
        nothing, when no tournament is loaded (Top8Service.fetcher);
        `refreshTournament(done)` asks for the tournament's data again, as
        loading the standings always did (its name and date are on the
        picture), and returns True, or returns False, doing nothing, when no
        tournament is loaded; `done()` once the data is in and the game it names
        loaded, however that went. `currentProvider()` says which provider is
        loaded now: an import from one that is not any more is dropped."""
        self._translate = translate or (lambda _context, text: text)
        self._locale = locale or (lambda: "en")
        self._fetcher = fetcher
        self._refreshTournament = refreshTournament
        self._currentProvider = currentProvider

        # Whether an import is in progress: from the request until its end is
        # published, on the provider's worker.
        self._importLock = threading.Lock()
        self._importing = False

        super().__init__(later, render, prune, shuttingDown)

    # ── the layout ───────────────────────────────────────────────────────

    def Layout(self) -> str:
        """The layout saved, else the one the battle format starts on."""
        saved = _StoredConfig().get("layout")
        if isinstance(saved, str) and saved in LAYOUT_SUBDIRS:
            return saved
        return DEFAULT_TEAM_LAYOUT if self._BattleFormat() in TEAM_FORMATS \
            else DEFAULT_LAYOUT

    @staticmethod
    def _BattleFormat():
        actions = AutonaActions.instance
        return actions.ResolveBattleFormat() if actions is not None \
            else "singles"

    def Layouts(self) -> list:
        return [{"id": layout,
                 "name": self._translate("app", LAYOUT_NAMES[layout]),
                 "width": LAYOUT_SIZES[layout][0],
                 "height": LAYOUT_SIZES[layout][1],
                 "slots": SlotsFor(layout),
                 "team": IsTeamLayout(layout)}
                for layout in LAYOUT_SUBDIRS]

    def SetLayout(self, layout) -> bool:
        """Save the layout. False, saving nothing, when it is not one."""
        if not isinstance(layout, str) or layout not in LAYOUT_SUBDIRS:
            logger.warning(f"Result: refusing the layout {layout!r}")
            return False
        with SettingsManager.lock:
            config = _StoredConfig()
            if config.get("layout") != layout:
                config["layout"] = layout
                SettingsManager.Set(CONFIG_SETTING, config)
        return True

    def FollowBattleFormat(self, fmt):
        """The layout for a battle format just chosen (Window.SetBattleFormat):
        Team and Crew are drawn on a team layout, 1v1 on one of the singles'.
        A layout that fits stays: a team_split or a vertical picked by hand."""
        team = IsTeamLayout(self.Layout())
        if fmt in TEAM_FORMATS:
            if not team:
                self.SetLayout(DEFAULT_TEAM_LAYOUT)
        elif team:
            self.SetLayout(DEFAULT_LAYOUT)

    # ── what a page shows ────────────────────────────────────────────────

    @staticmethod
    def _Pick(value, catalog, snapshot):
        """{"en_name", "skin"} of a value, None when it is empty or not of the
        game loaded."""
        if not value or catalog is None:
            return None
        en_name, skin = Top8Service.CharacterPick(value, catalog, snapshot)
        return {"en_name": en_name, "skin": skin} if en_name else None

    @staticmethod
    def _BorrowedName(row_character, name):
        """The name of the character the scoreboard lends, None when none."""
        if row_character or not name:
            return None
        borrowed = _FromScoreboard(name)
        if not borrowed:
            return None
        return borrowed.get("name") or borrowed.get("en_name") or ""

    def _DescribePlayer(self, slot, row, catalog, snapshot) -> dict:
        values = _PlayerCharacters(row)
        picks = [self._Pick(value, catalog, snapshot) for value in values]
        picks += [None] * (MAX_CHARACTERS - len(picks))
        name = row.get("name") or ""
        return {
            "slot": int(slot),
            "rank": row.get("rank", int(slot)),
            "name": name,
            "sponsor": row.get("sponsor") or "",
            "twitter": row.get("twitter") or "",
            "characters": picks,
            "fromScoreboard": self._BorrowedName(values[0], name),
        }

    def _DescribeTeam(self, slot, row, catalog, snapshot) -> dict:
        members = []
        for member in _Members(row):
            name = member.get("name") or ""
            character = member.get("character")
            members.append({
                "name": name,
                "twitter": member.get("twitter") or "",
                "character": self._Pick(character, catalog, snapshot),
                "fromScoreboard": self._BorrowedName(character, name),
            })
        return {
            "slot": int(slot),
            "rank": row.get("rank", int(slot)),
            "name": row.get("name") or "",
            "members": members,
        }

    def Describe(self) -> dict:
        """What a page shows: the layout and the layouts, the rows of both
        kinds, whether a preview now draws the sample, the game, and how the
        render and the import are going. Not the post's text (PostText), which
        has handles from the player database: anyone on the network may read
        this."""
        catalog = GameAssetCatalog.instance
        layout = self.Layout()
        with StateManager.lock:
            snapshot = catalog.Current() if catalog is not None else None
            players = _Rows(PLAYERS)
            teams = _Rows(TEAMS)
            describedPlayers = [
                self._DescribePlayer(slot, players.get(slot, {}), catalog,
                                     snapshot)
                for slot in PLAYER_SLOTS]
            describedTeams = [
                self._DescribeTeam(slot, teams.get(slot, {}), catalog, snapshot)
                for slot in TEAM_SLOTS]
            game = StateManager.Get(schema.GAME_ROOT)
            game = game if isinstance(game, dict) else {}
            render = schema.ReadResultRender()
            standings = schema.ReadResultStandings()
        return {
            "layout": layout,
            "layouts": self.Layouts(),
            "players": describedPlayers,
            "teams": describedTeams,
            "sample": not HasData(layout),
            "game": {"codename": game.get("codename"),
                     "name": game.get("name") or ""}
            if game.get("codename") else None,
            "render": render,
            "standings": standings,
        }

    # ── the payload ──────────────────────────────────────────────────────

    def BuildPayload(self, preview=False, games=None, layout=None):
        """(data, config, layout) for result.html, of copies: nothing shares
        anything with the state. A preview with no row to draw draws the sample,
        from `games` (a GameAssetCatalog; None: no characters)."""
        layout = layout or self.Layout()
        config = _Config()
        count = SlotsFor(layout)
        kind = KindOf(layout)
        with StateManager.lock:
            if not preview or HasData(layout):
                info = schema.TOURNAMENT_INFO_ROOT
                rows = _Rows(kind)
                build = _PayloadPlayer if kind == PLAYERS else _PayloadTeam
                data = {
                    "tournamentName":
                        StateManager.Get(f"{info}.tournamentName", "") or "",
                    "eventName":
                        StateManager.Get(f"{info}.eventName", "") or "",
                    "startAt": StateManager.Get(f"{info}.startAt", None),
                    "numEntrants":
                        StateManager.Get(f"{info}.numEntrants", 0) or 0,
                    "players": [build(index, rows.get(str(index), {}))
                                for index in range(1, count + 1)],
                }
                return deep_clone(data), config, layout
        # Out of the lock: it looks at the game's files, character by character.
        return self._SampleData(games, count, kind), config, layout

    def _CharacterName(self, key, character):
        """A character's name in the program's locale, its key without one."""
        names = character.get("locale")
        if not names:
            return key
        locale = self._locale()
        exact = locale.replace("-", "_")
        if exact in names:
            return names[exact]
        return names.get(re.split("-|_", locale)[0], key)

    @staticmethod
    def _SampleCharacter(games, key, character):
        """(the value a sample draws, whether it has a picture): the character
        on its first skin."""
        assets = games.CharacterAssets(character.get("codename"), 0)
        asset = assets.get("full") or assets.get("portrait")
        return {"assets": {"full": asset} if asset else {},
                "codename": character.get("codename"),
                "name": key}, bool(asset)

    def _SampleData(self, games, count, kind) -> dict:
        """What a preview draws while there is nothing to draw: always the
        same (decision 13), the game's characters in order."""
        snapshot = games.Current() if games else None
        characters = snapshot.characters if snapshot else {}
        selected = snapshot.game if snapshot else {}
        data = {
            "tournamentName": self._translate("app", "Sample Tournament"),
            "eventName": f"Top {count}",
            "players": [],
        }
        keys = list(characters.keys())
        if kind == TEAMS:
            # Of a team's members, only the ones with a picture.
            pool = []
            for key in keys:
                value, hasPicture = self._SampleCharacter(
                    games, key, characters[key])
                if hasPicture:
                    pool.append(value)
            drawn = 0
            for index in range(count):
                members = []
                # 2-5 members a team, so that the layout's adjustment shows.
                for member in range(2 + index % 4):
                    character = {}
                    if pool:
                        character = pool[drawn % len(pool)]
                        drawn += 1
                    members.append({"name": f"P{member + 1}",
                                    "character": character})
                data["players"].append({"rank": index + 1,
                                        "name": f"Team {index + 1}",
                                        "sponsor": "", "members": members})
            return data

        for index in range(count):
            entry = {"rank": index + 1, "name": f"Player {index + 1}",
                     "sponsor": "", "character": {}}
            if keys:
                key = keys[index % len(keys)]
                character = characters[key]
                entry["name"] = self._CharacterName(key, character)
                entry["sponsor"] = (selected.get("codename") or "").upper()
                entry["character"] = self._SampleCharacter(
                    games, key, character)[0]
            data["players"].append(entry)
        return data

    # ── the render (RenderLane) ──────────────────────────────────────────

    def _ReadRender(self):
        return schema.ReadResultRender()

    def _RunPayload(self, kind):
        layout = self.Layout()
        if kind == GENERATE and not HasData(layout):
            raise RenderDataMissing(NO_DATA, "no row has anything to draw")
        return self.BuildPayload(
            preview=kind == PREVIEW, games=GameAssetCatalog.instance,
            layout=layout)

    def _OutputStem(self, data):
        return OutputStem(data)

    def Generate(self):
        """Draw and save the result in the background (out/results, a PNG),
        which also updates the preview image, and save the rows' players in the
        player database, as making the image always did: the revision of the
        run that will. BUSY (nothing changes) while one is on its way, NO_DATA
        (nothing changes) when there is nothing to draw."""
        if not HasData(self.Layout()):
            return NO_DATA
        answer = super().Generate()
        if answer != BUSY:
            try:
                self.SaveToDb()
            except Exception:
                # A DB hiccup must not take the image with it.
                logger.exception("Result: saving the players to the DB failed")
        return answer

    # ── the player database ──────────────────────────────────────────────

    @staticmethod
    def _Mains(values, catalog, snapshot) -> list:
        """[[en_name, skin, ""], ...] of the values, the shape a player card
        saves (a result has no variant); the ones not of the game are left
        out."""
        mains = []
        for value in values:
            if not value or catalog is None:
                continue
            en_name, skin = Top8Service.CharacterPick(value, catalog, snapshot)
            if en_name:
                mains.append([en_name, max(skin, 0), ""])
        return mains

    def _Records(self, kind, catalog) -> list:
        snapshot = catalog.Current() if catalog is not None else None
        codename = (snapshot.codename or "") if snapshot is not None else ""
        records = []
        with StateManager.lock:
            rows = _Rows(kind)
            for slot in PLAYER_SLOTS if kind == PLAYERS else TEAM_SLOTS:
                row = rows.get(slot)
                if row is None:
                    continue
                if kind == PLAYERS:
                    entries = [(row, _PlayerCharacters(row))]
                else:
                    entries = [(member, [member.get("character")])
                               for member in _Members(row)]
                for entry, values in entries:
                    name = entry.get("name")
                    if not name:
                        continue
                    record = {"gamerTag": name}
                    if entry.get("sponsor"):
                        record["prefix"] = entry["sponsor"]
                    if entry.get("twitter"):
                        record["twitter"] = entry["twitter"]
                    mains = self._Mains(values, catalog, snapshot)
                    if mains and codename:
                        record["mains"] = {codename: mains}
                    records.append(record)
        return records

    def SaveToDb(self, games=None) -> int:
        """Merge the current layout's rows into the player database (country,
        real name and other games' mains are kept: overwrite=False). The number
        of players saved."""
        records = self._Records(KindOf(self.Layout()), _Catalog(games))
        if not records:
            return 0
        PlayerDB.instance.AddPlayers(records, overwrite=False)
        logger.info(f"Result: saved {len(records)} player(s) to the local DB")
        return len(records)

    # ── the post's text ──────────────────────────────────────────────────

    @staticmethod
    def _Handle(name, sponsor="", given=""):
        """" @handle", "" when unknown: the row's, else the player database's,
        by "prefix gamerTag" and then gamerTag."""
        raw = _Clean(given)
        database = PlayerDB.instance
        if not raw and name and database is not None:
            key = f"{sponsor} {name}".strip() if sponsor else name
            record = database.Get(key) or database.Get(name) or {}
            raw = record.get("twitter", "") or ""
        handle = raw.lstrip("@").strip()
        return f" @{handle}" if handle else ""

    def PostText(self, layout=None) -> str:
        """The X post's text for the standings `layout` draws, in the order of
        the ranks: the top POST_RANKS with anything in them, then the URLs. What
        is posted says what the picture says."""
        layout = layout or self.Layout()
        kind = KindOf(layout)
        with StateManager.lock:
            info = schema.TOURNAMENT_INFO_ROOT
            tournament = StateManager.Get(f"{info}.tournamentName") or ""
            event = StateManager.Get(f"{info}.eventName") or ""
            date = StateManager.Get(f"{info}.startAt") or ""
            entrants = StateManager.Get(f"{info}.numEntrants", 0) or 0
            rows = _Rows(kind)
            shown = sorted(
                ((slot, deep_clone(rows[slot]))
                 for slot in map(str, range(1, SlotsFor(layout) + 1))
                 if slot in rows),
                key=lambda item: _RankKey(item[1], item[0]))
            stream = StateManager.Get(f"{schema.STREAM_ROOT}.url", "") or ""
        tournamentUrl = SettingsManager.Get(schema.TOURNAMENT_URL_SETTING, "") or ""

        lines = []
        if tournament:
            lines.append(tournament.upper())
        details = [event, date]
        if entrants:
            details.append(str(entrants))
        heading = " - ".join(part for part in details if part)
        if heading:
            lines.append(heading)
        lines.append("")

        emitted = 0
        for slot, row in shown:
            if emitted >= POST_RANKS:
                break
            rank = row.get("rank", slot)
            line = self._TeamLine(row) if kind == TEAMS else self._PlayerLine(row)
            if line is None:
                continue
            lines.append(f"{rank}/ {line}")
            emitted += 1

        if tournamentUrl:
            lines += ["", "■Startgg", tournamentUrl]
        if stream:
            lines += ["", "■Stream", stream]
        return "\n".join(lines).strip("\n")

    def _PlayerLine(self, row):
        name = _Clean(row.get("name"))
        if not name:
            return None
        sponsor = _Clean(row.get("sponsor"))
        base = f"{sponsor} {name}" if sponsor else name
        return f"{base}{self._Handle(name, sponsor, row.get('twitter'))}"

    def _TeamLine(self, row):
        team = _Clean(row.get("name"))
        members = []
        for member in _Members(row):
            name = _Clean(member.get("name"))
            if name:
                members.append(
                    f"{name}{self._Handle(name, given=member.get('twitter'))}")
        if not team and not members:
            return None
        text = " / ".join(members)
        return f"{team} [{text}]" if team else text

    # ── the standings' import ────────────────────────────────────────────

    def LoadStandings(self, confirmed=False):
        """Fetch the standings the current layout shows and put them in its
        rows, off the calling thread. Answers at once: BUSY when one is in
        progress, whatever the rows hold (this one is dropped, not queued);
        NEEDS_CONFIRMATION when the rows hold something an import would
        overwrite and it was not `confirmed`; NO_TOURNAMENT when none is loaded,
        the app is quitting or no shell registered a fetcher (nothing changes in
        these three); else the revision of result_standings that says how it
        goes, published as "running" before this returns. Any thread but a
        job's."""
        layout = self.Layout()
        kind = KindOf(layout)
        with StateManager.lock:
            with self._importLock:
                if self._importing:
                    logger.info("Result: an import is already in progress")
                    return BUSY
                if not confirmed and HasInput(kind):
                    return NEEDS_CONFIRMATION
                self._importing = True
        try:
            # Taken here and published below: the job may be over by then, in
            # which case it has published this revision, and that stands.
            revision = schema.ReadResultStandings()["revision"] + 1
            began = self._fetcher is not None and self._RefreshThenFetch(
                kind, SlotsFor(layout), revision)
        except Exception:
            self._ReleaseImport()
            raise
        if not began:
            self._ReleaseImport()
            if self._fetcher is None:
                logger.warning("Result: no fetcher registered")
            elif _Stopping():
                logger.info("Result: the app is quitting, not importing")
            else:
                logger.info("Result: no tournament set, not importing")
            return NO_TOURNAMENT
        with StateManager.lock:
            if schema.ReadResultStandings()["revision"] < revision:
                self._PublishImport(RUNNING, "", revision)
        return revision

    def _RefreshThenFetch(self, kind, count, revision) -> bool:
        """The tournament's data asked for again and then the standings, in
        that order: the data names the tournament's game, and a switch to it
        during the fetch would find this import `changed` (the mains are read
        by the game's name), so it is the game loaded after it they are fetched
        for. A failure of the data does not stop the standings. False, nothing
        started, when no tournament is loaded or the app is quitting."""
        asked = self._currentProvider() \
            if self._currentProvider is not None else None

        def fetch():
            catalog = GameAssetCatalog.instance
            codename = catalog.Current().codename if catalog is not None \
                else None

            def job(provider):
                self._RunImport(provider, kind, count, codename, revision)

            return bool(self._fetcher(job, IMPORT_STATUS))

        def refreshed():
            # Later, on another thread: nobody is left to tell but the state.
            began = False
            try:
                began = (self._currentProvider is None
                         or self._currentProvider() is asked) and fetch()
            except Exception:
                logger.exception("Result: the standings could not be asked for")
            if not began:
                logger.info("Result: the tournament changed, or the app is "
                            "quitting, before the standings were asked for")
                self._EndImport(revision, ERROR, CHANGED)

        if self._refreshTournament is None:
            return fetch()
        try:
            return bool(self._refreshTournament(refreshed))
        except Exception:
            logger.opt(exception=True).warning(
                "Result: the tournament's data could not be refreshed")
            return fetch()

    @staticmethod
    def _PublishImport(status, error, revision):
        # One Set of the whole dict: a reader never sees half a result.
        StateManager.Set(schema.RESULT_STANDINGS_ROOT, {
            "status": status, "error": error, "revision": revision})

    def _ReleaseImport(self):
        with self._importLock:
            self._importing = False

    def _EndImport(self, revision, status, error=""):
        """Publish how the import ended, and only then let the next one in:
        whether that went well or not, it is over."""
        try:
            with StateManager.lock:
                self._PublishImport(status, error, revision)
        finally:
            self._ReleaseImport()

    def _RunImport(self, provider, kind, count, codename, revision):
        """The job, on the provider's worker."""
        try:
            standings = provider.GetStandings(count, None, None, strict=True)
        except Exception:
            logger.opt(exception=True).warning(
                "Result: the standings could not be fetched")
            self._EndImport(revision, ERROR, FETCH_FAILED)
            return
        if not standings:
            logger.info("Result: the event has no standings yet")
            self._EndImport(revision, ERROR, NO_STANDINGS)
            return
        try:
            with StateManager.lock:
                if not self._StillTheSame(provider, codename):
                    logger.info("Result: the game or the tournament changed "
                                "during the import; dropped")
                    self._EndImport(revision, ERROR, CHANGED)
                    return
                with StateManager.SaveBlock():
                    ImportStandings(kind, standings)
                    self._EndImport(revision, DONE)
        except Exception:
            logger.opt(exception=True).warning(
                "Result: the standings could not be imported")
            self._EndImport(revision, ERROR, IMPORT_FAILED)

    def _StillTheSame(self, provider, codename) -> bool:
        """The game and the tournament are the ones the import was asked of:
        the mains are read by the game's name, and the rows are this
        tournament's. Under the state lock, where a game switch empties the
        characters."""
        if self._currentProvider is not None \
                and self._currentProvider() is not provider:
            return False
        catalog = GameAssetCatalog.instance
        return catalog is None or catalog.Current().codename == codename


def _Stopping():
    executor = TaskExecutor.instance
    return executor is not None and executor.stopping.is_set()
