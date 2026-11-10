"""The match thumbnail (サムネイル, ADR-005 S4o).

Everything about a match thumbnail that needs no Qt, so that the settings
page is a React one (web/src/ThumbnailPage, ADR-005 S4p, which removed the Qt
panel) and it also answers in the headless shell:

- **the settings** (settings.json ``thumbnail_config``; the shape is
  unchanged): ``Settings()`` reads them with their defaults filled in and
  writes nothing -- the Qt panel's GetSetting wrote every default it read,
  which left remains like ``game.None``. Those are skipped here, not removed.
  Every render is given what they read as (``RenderPayload``), whatever the
  template would make of the saved value. ``Update()`` checks everything
  before it writes anything, ``Reset()`` goes back to the defaults;
- **the video title** (``VideoTitle``) and **the payload** ``thumbnail.html``
  is given (``BuildPayload``), both moved here unchanged:
  tests/test_thumbnail_service.py replays what the old code answered
  (tests/fixtures/thumbnail/golden.json). So are the background's resolution,
  the output's names and its 30 generations;
- **the custom background** (``ImportBackground``): an uploaded image, saved
  under user_data/. Its name holds a hash of its content: the template has no
  cache buster, on purpose (its comment), and would keep showing the previous
  image from the same path;
- **a render of the thumbnail in the background** (``Preview`` / ``Generate``),
  whose progress and result are the state's ``thumbnail.render``. The drawing
  itself is the shell's: an off-screen WebEngine view only the desktop has
  (``render``, injected; headless, AutonaWebOperations refuses first, 503);
- **a board's thumbnail drawn now** (``DrawBoard``, ADR-005 S4y): the
  synchronous way the desktop's legacy route and SCORE's X post wait on, with
  the shell's drawer given by the caller.

The render lane is core/RenderLane's, shared with the tournament result
(ADR-005 S4q), where the rules of a run are. A kept generate that finds no
match to save (NO_GAME / NO_PLAYER) is followed by the preview it stood in
for, which draws a placeholder.

``ThumbnailService.instance`` is built by ``bootstrap_core()``; the deferral,
the shell's renderer, the translations, the locale's names and the output
pruning are injected there, because core may not import them
(tests/test_core_qt_free.py). Import side effects: none (agent-pitfalls #1).
"""
import copy
import datetime
import hashlib
import json
import os
import random
import re
import threading
from pathlib import Path

from loguru import logger

from . import ImageImport
from . import StateSchema as schema
from .ColorService import FEATURE_THUMBNAIL, GetFeatureColors
from .DictHelper import deep_get
from .GameAssetCatalog import GameAssetCatalog
from .RenderLane import (  # noqa: F401  (re-exported: this module's names)
    BUSY, CANCELLED, DONE, ERROR, GENERATE, IDLE, PAYLOAD_FAILED, PREVIEW,
    RENDER_FAILED, RUNNING, RenderDataMissing, RenderLane)
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .StreamService import GameAbbreviation

CONFIG_SETTING = "thumbnail_config"
RENDER_PATH = f"{schema.THUMBNAIL_ROOT}.{schema.THUMBNAIL_RENDER_KEY}"

# ── the templates ────────────────────────────────────────────────────────

# Two templates, each a subdirectory under layout/ (ThumbnailRenderer).
TEMPLATE_IDS = ("type_a", "type_b")
DEFAULT_TEMPLATE_ID = "type_a"
# id -> its name in the "app" translation context.
TEMPLATE_NAMES = {"type_a": "Standard", "type_b": "Center Panel"}
TEMPLATE_DIRECTORY = "./assets/thumbnail_base/thumbnail_types"

# ── the background ───────────────────────────────────────────────────────

BACKGROUND_DEFAULT = "default"
BACKGROUND_BANNER = "banner"
BACKGROUND_CUSTOM = "custom"
BACKGROUND_SOURCES = (BACKGROUND_DEFAULT, BACKGROUND_BANNER, BACKGROUND_CUSTOM)
# core/TournamentMediaService manages the banner's file; a thumbnail reads it
# when the operator picks "use the tournament banner".
BANNER_PATH = schema.TOURNAMENT_BANNER_PATH
DEFAULT_BANNER_PATH = schema.DEFAULT_BANNER_PATH
# An uploaded background: a full-HD image is all a thumbnail can show.
BACKGROUND_DIRECTORY = "./user_data"
BACKGROUND_PREFIX = "thumbnail_background_"
BACKGROUND_LONGEST_SIDE = 1920

IMPORTED = ImageImport.IMPORTED
INVALID_IMAGE = ImageImport.INVALID_IMAGE
SAVE_FAILED = ImageImport.SAVE_FAILED

# ── the settings ─────────────────────────────────────────────────────────

# What a missing setting means. They are the old Qt panel's, which is what an
# operator has been looking at (and what it saved once opened), and not all of
# them are thumbnail.js's own: without a saved value it reads use_team_names
# and flip_p2 as false. Every render is given the settings as they read
# (_AsRead, through RenderPayload): what GET says is what gets drawn.
DEFAULT_TOGGLES = {
    "display_phase": True,
    "use_team_names": True,
    "use_sponsors": True,
}
DEFAULT_GAME_SETTINGS = {
    "flip_p1": False,
    "flip_p2": True,
    "smooth_scale": True,
    "zoom": 100,
    "align": {"horizontal": 50, "vertical": 40},
}
# The old Qt panel's spin boxes (the page's number fields take the same), but
# for 0, which they took as an alignment: thumbnail.js reads it as "no value"
# (`|| 50`, `|| 40`) and draws the default, and the templates are not changed.
# So it is not taken, and a saved 0 reads as that default, which is what it
# draws.
ZOOM_RANGE = (1, 1000)
ALIGN_RANGE = (1, 100)
# A game's settings are saved under its codename, a dot path segment, which
# is also a directory name: no separators, nothing that could not be one. That
# is the name's shape, which a saved one is read by too; Update also wants it
# to be a game there is (_KnownCodenames).
CODENAME_MAX_LENGTH = 128
_CODENAME_REFUSED = re.compile(r"[./\\\x00-\x1f\x7f]")
# What the old Qt panel's GetSetting saved while no game was selected.
_NO_GAME_CODENAME = "None"

# ── the data ─────────────────────────────────────────────────────────────

# What a final thumbnail falls back to when the state has no complete match.
PROGRAM_STATE_FILE = "./out/program_state.json"
# ADR-005 S4 design: a render of this module's draws board 1, as the old Qt
# panel's preview, copy and save did. SCORE's X post and the legacy route still
# draw their own board (DrawBoard's `board`, ADR-005 S4y).
RENDER_BOARD = 1

# ── the output ───────────────────────────────────────────────────────────

OUTPUT_DIRECTORY = "./out/thumbnails"
OUTPUT_PATTERNS = ("*.png", "*.jpg")
OUTPUT_KEEP = 30
# Not tmp/thumbnail/template.png, which the old Qt panel's synchronous preview
# wrote (main_generate_thumbnail's synchronous preview, removed in S4ac). Removed with
# the rest of ./tmp when the desktop app exits, and at construction: the
# headless shell and a crash leave it, and a new session has not drawn
# anything yet. Served by
# GET /api/v1/thumbnail/preview: ./tmp is not one of the web server's static
# directories, and does not become one.
PREVIEW_PATH = "./tmp/thumbnail/preview.png"

# ── the render ───────────────────────────────────────────────────────

# What the shell is asked to draw.
RENDER_KIND = "thumbnail"
# thumbnail.render's error: RenderLane's, SAVE_FAILED, and what this module
# finds missing.
NO_GAME = "no_game"             # no game is selected
NO_PLAYER = "no_player"         # a side of the match has no player


class ThumbnailDataMissing(RenderDataMissing):
    """There is no match to draw a final thumbnail of. Its text is for a
    person (the SCORE panel's message box), its `code` for thumbnail.render."""


# ── pure helpers ─────────────────────────────────────────────────────────

def RemoveSpecialChars(text):
    for character in '<>:"/\\|?* ':
        text = text.replace(character, "")
    return text


def OutputStem(data, board=1, now=None):
    """The name of a saved thumbnail, without its extension:
    "<tag>-vs-<tag>-<UTC date and time>"."""
    left = deep_get(data, f"score.{board}.team.1.player.1.name", "") or ""
    right = deep_get(data, f"score.{board}.team.2.player.1.name", "") or ""
    when = now or datetime.datetime.now(datetime.timezone.utc)
    return (f"{RemoveSpecialChars(left)}-vs-{RemoveSpecialChars(right)}-"
            f"{when.strftime('%Y-%m-%d-%H-%M-%S')}")


def ResolveBackgroundPath(config):
    """The image a thumbnail's background is, from the source selector, for
    the template's single ``background_path``; None: the template's default.

    - banner: looked at on every render, as the file may appear only after
      the operator downloads it;
    - custom: the operator's own image (``background_path``);
    - no selector (a config from before it): whatever ``background_path``
      holds.
    """
    source = config.get("background_source")
    if source == BACKGROUND_BANNER:
        return BANNER_PATH if os.path.exists(BANNER_PATH) \
            else DEFAULT_BANNER_PATH
    if source == BACKGROUND_DEFAULT:
        return None
    return config.get("background_path") or None


def EffectiveBackgroundPath():
    """The image the saved settings draw a thumbnail's background with, as
    every render reads them (RenderPayload: a config from before the source
    selector moved to it, a malformed one read as none); None: the template's
    default. What the tournament result's background follows (ADR-005 S4q,
    decision 15). Writes nothing."""
    config = _WithBackgroundSource(_StoredConfig())
    # The resolved path, as _AsRead looks at it: a banner saved without a
    # background_path is the banner.
    path = ResolveBackgroundPath(config)
    if config.get("background_source") not in BACKGROUND_SOURCES \
            or not isinstance(path, str):
        return None
    return path


def ResolveTemplateId(settings):
    templateId = (settings or {}).get("template_id", DEFAULT_TEMPLATE_ID)
    return templateId if templateId in TEMPLATE_IDS else DEFAULT_TEMPLATE_ID


def _WithBackgroundSource(config):
    """`config` (written to) from before the source selector, moved to it.
    What it holds already is left alone."""
    if config.get("background_source") is not None:
        return config
    legacy = config.get("background_path") or ""
    if legacy in (BANNER_PATH, DEFAULT_BANNER_PATH):
        # It pointed at the tournament banner: free the custom slot.
        config["background_source"] = BACKGROUND_BANNER
        config["background_path"] = ""
    elif legacy:
        config["background_source"] = BACKGROUND_CUSTOM
    else:
        config["background_source"] = BACKGROUND_DEFAULT
    return config


def _StoredConfig():
    """A copy of what settings.json holds: never the live dict."""
    with SettingsManager.lock:
        stored = SettingsManager.Get(CONFIG_SETTING)
        return copy.deepcopy(stored) if isinstance(stored, dict) else {}


def _IsBool(value):
    return isinstance(value, bool)


def _IsIntIn(value, limits):
    return isinstance(value, int) and not isinstance(value, bool) \
        and limits[0] <= value <= limits[1]


def _ValidCodename(name):
    return isinstance(name, str) and bool(name) and name != _NO_GAME_CODENAME \
        and len(name) <= CODENAME_MAX_LENGTH \
        and not _CODENAME_REFUSED.search(name)


def _ValidAlign(value):
    return isinstance(value, dict) and bool(value) and all(
        key in DEFAULT_GAME_SETTINGS["align"] and _IsIntIn(number, ALIGN_RANGE)
        for key, number in value.items())


_GAME_CHECKS = {
    "flip_p1": _IsBool,
    "flip_p2": _IsBool,
    "smooth_scale": _IsBool,
    "zoom": lambda value: _IsIntIn(value, ZOOM_RANGE),
    "align": _ValidAlign,
}


def _ValidGames(value):
    return isinstance(value, dict) and all(
        _ValidCodename(name) and isinstance(changes, dict) and all(
            key in _GAME_CHECKS and _GAME_CHECKS[key](setting)
            for key, setting in changes.items())
        for name, changes in value.items())


# `background_path` and `main_icon_path` are not among them: a path would have
# the render read any local file. A background comes in as an upload.
_CHECKS = {
    "template_id": lambda value: isinstance(value, str)
    and value in TEMPLATE_IDS,
    "background_source": lambda value: isinstance(value, str)
    and value in BACKGROUND_SOURCES,
    "display_phase": _IsBool,
    "use_team_names": _IsBool,
    "use_sponsors": _IsBool,
    "game": _ValidGames,
}


def _ReadToggle(config, key):
    value = config.get(key)
    return value if _IsBool(value) else DEFAULT_TOGGLES[key]


def _ReadGame(saved):
    """A game's settings with the defaults filled in (anything malformed
    reads as its default, and an alignment of 0 as what thumbnail.js draws for
    it)."""
    settings = copy.deepcopy(DEFAULT_GAME_SETTINGS)
    for key, check in _GAME_CHECKS.items():
        if key != "align" and key in saved and check(saved[key]):
            settings[key] = saved[key]
    align = saved.get("align")
    if isinstance(align, dict):
        for key in settings["align"]:
            if _IsIntIn(align.get(key), ALIGN_RANGE):
                settings["align"][key] = align[key]
    return settings


def _MergeChanges(config, changes):
    """`changes` (checked) into `config`, one game setting at a time."""
    for key, value in changes.items():
        if key != "game":
            config[key] = value
            continue
        games = config.get("game")
        if not isinstance(games, dict):
            games = config["game"] = {}
        for codename, settings in value.items():
            game = games.get(codename)
            if not isinstance(game, dict):
                game = games[codename] = {}
            for name, setting in settings.items():
                if name == "align":
                    align = game.get("align")
                    if not isinstance(align, dict):
                        align = game["align"] = {}
                    align.update(setting)
                else:
                    game[name] = setting


def _AsRead(config, data):
    """`config` from BuildPayload (written to), with what Settings() reads of
    it for what thumbnail.js looks at: what GET says is what gets drawn. The
    template reads a missing or malformed value otherwise (`use_team_names: 0`
    as false, `zoom: 150.0` as 150, a missing flip_p2 as false) and is not
    changed. Every render goes through here (RenderPayload): the v1 runs, and
    since ADR-005 S4p SCORE's button and the legacy route too, whose settings
    are the React page's now."""
    for key in DEFAULT_TOGGLES:
        config[key] = _ReadToggle(config, key)
    if config.get("background_source") not in BACKGROUND_SOURCES \
            or not isinstance(config.get("background_path"), str):
        config["background_path"] = None
    games = config.get("game")
    if not isinstance(games, dict):
        games = config["game"] = {}
    codename = str(deep_get(data, "game.codename", "") or "")
    game = games.get(codename)
    if not isinstance(game, dict):
        game = games[codename] = {}
    # Over what is saved, whose other keys (asset_pack) stay.
    game.update(_ReadGame(game))
    return config


# ── the video title ──────────────────────────────────────────────────────

def _PlayerDisplayName(board, team):
    """A team's first player's name as shown ("prefix | gamerTag" when there
    is a prefix), without the losers mark; "" while there is none."""
    base = f"score.{board}.team.{team}.player.1"
    merged = StateManager.Get(f"{base}.mergedName", "") or ""
    if merged:
        return merged.replace(" [L]", "")
    return StateManager.Get(f"{base}.gamerTag", "") or ""


def _MainCharacterName(board, team):
    """The name of a team's first player's first character: the slots in
    `character`, then the saved `mains`. "" while there is none."""
    base = f"score.{board}.team.{team}.player.1"
    for subkey in ("character", "mains"):
        characters = StateManager.Get(f"{base}.{subkey}")
        if not isinstance(characters, dict) or not characters:
            continue
        try:
            first = sorted(characters.keys(), key=lambda key: int(key))[0]
        except (TypeError, ValueError):
            first = next(iter(characters))
        name = (characters.get(first) or {}).get("name") or ""
        if name:
            return name
    return ""


def _PlayerWithCharacter(board, team):
    player = _PlayerDisplayName(board, team)
    character = _MainCharacterName(board, team)
    if player and character:
        return f"{player}({character})"
    return player or character


def BuildVideoTitle(board=1) -> str:
    """"<tournament> <round> | <side> vs <side> | <game>", without the parts
    that came out empty (no stray separators)."""
    with StateManager.lock:
        tournament = StateManager.Get(
            f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_NAME_KEY}",
            "") or ""
        roundName = StateManager.Get(f"score.{board}.match_en", "") or ""
        abbreviation = GameAbbreviation(
            StateManager.Get(schema.GAME_ROOT, {}) or {}) or ""
        left = _PlayerWithCharacter(board, 1)
        right = _PlayerWithCharacter(board, 2)
    heading = " ".join(part for part in (tournament, roundName) if part)
    match = f"{left} vs {right}".strip() if left or right else ""
    return " | ".join(
        segment for segment in (heading, match, abbreviation) if segment)


# ── the service ──────────────────────────────────────────────────────────

class ThumbnailService(RenderLane):
    """See the module docstring. One per process: the render lane is its
    own."""

    instance: "ThumbnailService" = None

    LOG_NAME = "Thumbnail"
    RENDER_KIND = RENDER_KIND
    RENDER_PATH = RENDER_PATH
    OUTPUT_DIRECTORY = OUTPUT_DIRECTORY
    OUTPUT_PATTERNS = OUTPUT_PATTERNS
    OUTPUT_KEEP = OUTPUT_KEEP
    ALSO_JPG = True
    PREVIEW_PATH = PREVIEW_PATH
    LEFTOVERS = (f"{BACKGROUND_DIRECTORY}/{BACKGROUND_PREFIX}*.png",)
    MISSING_CODES = (NO_GAME, NO_PLAYER)

    def __init__(self, later, render=None, translate=None, locale=None,
                 prune=None, shuttingDown=None):
        """`later`, `render`, `prune` and `shuttingDown` as RenderLane takes
        them; `render` is given (data, config, template). `translate(context,
        text)` and `locale()` -> (program locale, match names, phase names) are
        what a placeholder's words come from."""
        self._translate = translate or (lambda _context, text: text)
        self._locale = locale or (lambda: ("en", {}, {}))

        # The uploaded background: one import or reset at a time, so the
        # setting and the file on disk belong together.
        self._backgroundLock = threading.Lock()

        super().__init__(later, render, prune, shuttingDown)

    # ── the settings ─────────────────────────────────────────────────────

    def Settings(self) -> dict:
        """The settings with their defaults filled in. Writes nothing:
        `background_source` is what a config from before the selector moves
        to, the path is not told (a name to show is), and `game` has every
        saved game plus the one in the state. A saved game the old Qt panel's reads
        left behind (`game.None`) is skipped, not removed."""
        config = _WithBackgroundSource(_StoredConfig())
        source = config.get("background_source")
        path = config.get("background_path")
        settings = {
            "template_id": ResolveTemplateId(config),
            "background_source": source if source in BACKGROUND_SOURCES
            else BACKGROUND_DEFAULT,
            "background_name": os.path.basename(path.replace("\\", "/"))
            if isinstance(path, str) else "",
        }
        for key in DEFAULT_TOGGLES:
            settings[key] = _ReadToggle(config, key)
        games = {}
        saved = config.get("game")
        if isinstance(saved, dict):
            for codename, game in saved.items():
                if _ValidCodename(codename) and isinstance(game, dict):
                    games[codename] = _ReadGame(game)
        current = self._CurrentGame().get("codename")
        if _ValidCodename(current):
            games.setdefault(current, _ReadGame({}))
        settings["game"] = games
        return settings

    def Update(self, changes):
        """Change some of the settings and save them; all of them as saved
        (Settings()), or None when a key or a value is not one of theirs, or a
        game is not one there is (nothing is saved, not even what was valid).
        settings.json is written once, and only when something changed."""
        if not isinstance(changes, dict) or not all(
                key in _CHECKS and _CHECKS[key](value)
                for key, value in changes.items()) \
                or not self._KnownCodenames().issuperset(
                    changes.get("game", ())):
            logger.warning("Thumbnail: refusing the settings "
                           f"{sorted(map(str, changes)) if isinstance(changes, dict) else type(changes).__name__}")
            return None
        with SettingsManager.lock:
            stored = _StoredConfig()
            config = _WithBackgroundSource(copy.deepcopy(stored))
            _MergeChanges(config, changes)
            if config != stored:
                SettingsManager.Set(CONFIG_SETTING, config)
        return self.Settings()

    def Reset(self):
        """Back to the defaults, for every game (the setting is removed).
        An uploaded background goes with it."""
        with self._backgroundLock:
            SettingsManager.Unset(CONFIG_SETTING)
            self._RemoveUploadedBackgrounds()

    def Templates(self) -> list:
        return [{"id": templateId,
                 "name": self._translate("app", TEMPLATE_NAMES[templateId])}
                for templateId in TEMPLATE_IDS]

    def Describe(self) -> dict:
        """What a page shows of the thumbnail: the settings, the game they
        are for (None: no game), the templates and the video title."""
        game = self._CurrentGame()
        codename = game.get("codename")
        return {
            "settings": self.Settings(),
            "game": {"codename": codename, "name": game.get("name") or ""}
            if codename else None,
            "templates": self.Templates(),
            "videoTitle": self.VideoTitle(),
        }

    def _CurrentGame(self) -> dict:
        # The state's, as that is what a payload's `data.game` (which
        # thumbnail.js picks the game's settings with) comes from.
        game = StateManager.Get(schema.GAME_ROOT, {})
        return game if isinstance(game, dict) else {}

    def _KnownCodenames(self) -> set:
        """The games whose settings may be saved: the game list's, and the one
        in the state. An entry stays in settings.json until a reset, so a
        codename nobody can vouch for is refused rather than kept forever.
        Before the list is read (or without a catalog) that leaves the game in
        the state, which is the only one a page has on offer then (GET's
        `game`); another one can be saved once the list is there."""
        catalog = GameAssetCatalog.instance
        known = set(catalog.GameCodenames()) if catalog is not None else set()
        current = self._CurrentGame().get("codename")
        if _ValidCodename(current):
            known.add(current)
        return known

    def VideoTitle(self, board=1) -> str:
        return BuildVideoTitle(board)

    # ── the custom background ────────────────────────────────────────────

    def ImportBackground(self, data) -> str:
        """Make an image's bytes (PNG, JPEG, MPO -- its first frame -- or BMP,
        within ImageImport's cap) the background: scaled down to fit
        BACKGROUND_LONGEST_SIDE, saved as PNG under user_data/ and selected
        (``background_path`` and ``background_source``), the previously
        uploaded one removed; IMPORTED. INVALID_IMAGE when `data` is not such
        an image, SAVE_FAILED when the file could not be written (nothing
        changes in either case)."""
        encoded = ImageImport.EncodePng(
            data, BACKGROUND_LONGEST_SIDE, label="Thumbnail: the background")
        if encoded is None:
            return INVALID_IMAGE
        digest = hashlib.sha256(encoded).hexdigest()[:16]
        path = f"{BACKGROUND_DIRECTORY}/{BACKGROUND_PREFIX}{digest}.png"
        with self._backgroundLock:
            try:
                ImageImport.WriteFile(path, encoded)
            except OSError:
                logger.opt(exception=True).error(
                    "Thumbnail: the background could not be saved")
                return SAVE_FAILED
            with SettingsManager.lock:
                config = _StoredConfig()
                config["background_source"] = BACKGROUND_CUSTOM
                config["background_path"] = path
                SettingsManager.Set(CONFIG_SETTING, config)
            self._RemoveUploadedBackgrounds(keep=path)
        return IMPORTED

    def _RemoveUploadedBackgrounds(self, keep=None):
        keep = Path(keep).name if keep else None
        directory = Path(BACKGROUND_DIRECTORY)
        if not directory.is_dir():
            return
        for file in directory.glob(f"{BACKGROUND_PREFIX}*.png"):
            if file.name == keep:
                continue
            try:
                file.unlink()
            except OSError:
                logger.opt(exception=True).warning(
                    f"Thumbnail: {file} could not be removed")

    # ── the payload ──────────────────────────────────────────────────────

    def BuildPayload(self, preview=False, games=None, board=1):
        """(data, config, template) for thumbnail.html. `data` is the state,
        with what has not been published yet (the thumbnail shows what the
        operator has in the form), when it has a game and both sides' first
        players; else what was exported to PROGRAM_STATE_FILE. A final
        thumbnail without a complete match raises ThumbnailDataMissing; a
        preview draws a placeholder, from `games` (a GameAssetCatalog; None:
        no characters)."""
        return self._Payload(_StoredConfig(), preview, games, board)

    def _Payload(self, settings, preview, games, board):
        """BuildPayload's, from `settings`: a copy of the config (never the
        live one), which the payload's config shares its games with."""
        templateId = ResolveTemplateId(settings)
        with open(f"{TEMPLATE_DIRECTORY}/{templateId}.json", "rt",
                  encoding="utf-8") as file:
            template = json.load(file)
        # The renderer picks the template's page by it.
        template["template_id"] = templateId

        data = None
        try:
            state = StateManager.GetFullState(
                include_pending=True).get("state", {})
            if state and state.get("game", {}).get("codename") \
                    and all(self._HasFirstPlayer(state, board, team)
                            for team in (1, 2)):
                data = state
        except Exception:
            pass

        if data is None:
            try:
                data = self._ReadProgramState(board)
            except Exception as error:
                if not preview:
                    logger.warning(
                        f"Thumbnail: failed to load the program state: {error}")
                    raise
                # No complete match yet (a fresh start) is expected of a
                # preview. Not a warning.
                logger.debug(
                    f"Thumbnail: rendering a placeholder preview: {error}")
                codename = "test"
                try:
                    codename = StateManager.GetFullState().get(
                        "state", {}).get("game", {}).get("codename", "test")
                except Exception:
                    pass
                data = self._PlaceholderData(games, deep_get(
                    settings, f"game.{codename}.asset_pack"))

        config = dict(settings)
        config["scoreboardNumber"] = board
        config["background_path"] = ResolveBackgroundPath(config)
        # The thumbnail's colors (colors.thumbnail.*), falling back to the
        # tournament's and then the defaults. thumbnail.js injects them as CSS
        # variables; the per-template JSON keys are untouched.
        config["theme_color"], config["accent_color"] = GetFeatureColors(
            FEATURE_THUMBNAIL)
        return data, config, template

    def RenderPayload(self, preview=False, games=None, board=1):
        """What a render is given: BuildPayload's payload with the settings as
        Settings() reads them (_AsRead), a config from before the background
        selector moved to it (what GET says it is). The v1 runs and the two
        synchronous ways in (SCORE's button and its X post, the legacy GET
        /scoreboard<N>-get-thumbnail-*) all draw this, so the page's values
        are the picture at every entry (ADR-005 S4p; until then the Qt panel
        saved every default it read, and SCORE drew the raw config). Raises
        what BuildPayload raises.

        Writes nothing: the move is made on the copy drawn. settings.json gets
        it from the next change that writes it (Update, the upload), so a
        render never fails, or blocks on the file, over a setting nobody
        changed (S4p review)."""
        settings = _WithBackgroundSource(_StoredConfig())
        data, config, template = self._Payload(settings, preview, games, board)
        return data, _AsRead(config, data), template

    # ── a board's thumbnail, drawn now ───────────────────────────────────

    def DrawBoard(self, board, draw, games=None):
        """Board `board`'s match thumbnail, drawn now and saved: the PNG's
        path, "./out/thumbnails/<stem>.png", with its .jpg beside it.

        The synchronous way in (ADR-005 S4y), for what an operator waits on:
        the desktop's legacy GET /scoreboard<N>-get-thumbnail-<png|jpg> and
        SCORE's X post (AutonaShellOperations.DrawThumbnail), and
        main_generate_thumbnail.generate. Until then each went through the
        board's widget (its GenerateThumbnail), building the board first.
        `draw(payload)` is the shell's: it draws (data, config, template) at
        once and returns `encode(format, fast=False)`, as a render's `done`
        gets it, or raises when it drew nothing. Headless there is none, and
        the route answers 503 before this is reached. `games`: as
        RenderPayload takes it.

        Draws RenderPayload (the settings as they read) for `board`; the
        files are what QPixmap.save() wrote (the encoder's default
        encodings), the oldest past OUTPUT_KEEP pruned. Not a run of the
        lane: it neither waits for one nor publishes thumbnail.render, as
        the synchronous render never did. Raises ThumbnailDataMissing when
        there is no match to draw (in the state nor in program_state.json)
        and what `draw` raises, before anything is written, and OSError when
        a file could not be written."""
        payload = self.RenderPayload(preview=False, games=games, board=board)
        encode = draw(payload)
        png = encode("PNG")
        jpg = encode("JPG")
        if not png or not jpg:
            raise RuntimeError("Thumbnail rendering failed")
        stem = OutputStem(payload[0], board)
        target = Path(OUTPUT_DIRECTORY) / f"{stem}.png"
        ImageImport.WriteFile(target, png)
        ImageImport.WriteFile(target.with_suffix(".jpg"), jpg)
        logger.info(f"Thumbnail saved as {OUTPUT_DIRECTORY}/{stem}.png "
                    f"and {OUTPUT_DIRECTORY}/{stem}.jpg")
        if self._prune is not None:
            # The file just written is protected.
            self._prune(OUTPUT_DIRECTORY, OUTPUT_PATTERNS, OUTPUT_KEEP,
                        protect=f"{OUTPUT_DIRECTORY}/{stem}.png")
        return f"{OUTPUT_DIRECTORY}/{stem}.png"

    @staticmethod
    def _HasFirstPlayer(data, board, team):
        return "name" in data.get("score", {}).get(str(board), {}).get(
            "team", {}).get(str(team), {}).get("player", {}).get("1", {})

    def _ReadProgramState(self, board):
        try:
            with open(PROGRAM_STATE_FILE, "rt", encoding="utf-8") as file:
                data = json.load(file)
            hasGame = bool(data.get("game", {}).get("codename"))
            hasPlayers = [self._HasFirstPlayer(data, board, team)
                          for team in (1, 2)]
        except (OSError, ValueError, AttributeError, TypeError) as error:
            # No file, or not what the export writes: no match in there either.
            # The state, which BuildPayload found incomplete, says what is
            # missing. The text is what SCORE's message box has always shown.
            code = NO_PLAYER if self._CurrentGame().get("codename") else NO_GAME
            raise ThumbnailDataMissing(code, str(error)) from error
        if not hasGame:
            raise ThumbnailDataMissing(NO_GAME, self._translate(
                "thumb_app", "Please select a game first"))
        for team, hasPlayer in zip((1, 2), hasPlayers):
            if not hasPlayer:
                raise ThumbnailDataMissing(NO_PLAYER, self._translate(
                    "thumb_app", "Player {0} tag missing").format(team))
        return data

    def _PlaceholderData(self, games, usedAssets):
        """What a preview draws while there is no match: four random
        characters of the selected game, or nothing but the template's own
        images when there is no game to take them from."""
        snapshot = games.Current() if games else None
        characters = snapshot.characters if snapshot else {}
        selected = snapshot.game if snapshot else {}
        tournamentName = self._translate("app", "Sample Tournament")
        # Characters without a selected game: the catalog keeps them when the
        # selection is forgotten (a tournament cleared).
        if not characters or not selected.get("codename"):
            return {
                "game": {"codename": "test", "name": "Test", "smashgg_id": 0},
                "tournamentInfo": {"tournamentName": tournamentName},
                "score": {"1": {
                    "best_of": 3, "match": "", "phase": "", "pool": "",
                    "team": {
                        "1": self._EmptyTeam(),
                        "2": self._EmptyTeam(),
                    },
                }},
            }

        programLocale, matchNames, phaseNames = self._locale()
        codename = selected.get("codename")
        chars = []
        for _ in range(4):
            key = list(characters.keys())[
                random.randint(0, len(characters) - 1)]
            character = characters[key]
            skin = random.randint(0, len(characters[key]))
            assets = games.CharacterAssets(character.get("codename"), skin)
            asset = assets.get(usedAssets) or assets.get("full") \
                or assets.get("portrait") or assets.get("base_files/icon")

            name = key
            if character.get("locale"):
                if programLocale.replace("-", "_") in character["locale"]:
                    name = character["locale"][programLocale.replace("-", "_")]
                elif re.split("-|_", programLocale)[0] in character["locale"]:
                    name = character["locale"][
                        re.split("-|_", programLocale)[0]]

            chars.append({
                "name": name,
                "team": codename.upper(),
                "asset": {
                    "assets": {"full": asset},
                    "codename": character["codename"],
                    "name": key,
                    "skin": "0",
                },
            })

        def player(first, *others):
            slots = {"1": first["asset"]}
            for index, other in enumerate(others):
                slots[str(index + 2)] = other["asset"]
            return {
                "character": slots,
                "country": {},
                "mergedName": f"{first['team']} | {first['name']}",
                "name": first["name"],
                "state": {},
                "team": first["team"],
            }

        return {
            "game": {
                "codename": codename,
                "name": selected.get("name", "Test"),
                "smashgg_id": 0,
            },
            "tournamentInfo": {"tournamentName": tournamentName},
            "score": {"1": {
                "best_of": 3,
                "match": matchNames.get("winners_final"),
                "phase": phaseNames.get("group").format("A"),
                "team": {
                    "1": {
                        "losers": False,
                        "player": {"1": player(chars[0], chars[1])},
                        "score": 0,
                        "teamName": self._translate(
                            "app", "Team {0}").format("A"),
                    },
                    "2": {
                        "losers": False,
                        "player": {"1": player(chars[2]),
                                   "2": player(chars[3])},
                        "score": 0,
                        "teamName": self._translate(
                            "app", "Team {0}").format("B"),
                    },
                },
            }},
        }

    @staticmethod
    def _EmptyTeam():
        return {
            "losers": False,
            "player": {"1": {"character": {}, "country": {}, "mergedName": "",
                             "name": "", "state": {}, "team": ""}},
            "score": 0,
            "teamName": "",
        }

    # ── the render (RenderLane) ──────────────────────────────────────────

    def _ReadRender(self):
        return schema.ReadThumbnailRender()

    def Generate(self, board=RENDER_BOARD):
        """RenderLane.Generate, of board `board` (1 / 2; ADR-005 S4z, v1's
        POST /thumbnail:generate's `board`). A preview draws RENDER_BOARD:
        the previews kept are drawn together, and the preview is the
        thumbnail page's. A generate of RENDER_BOARD updates the preview
        image too and stands in for a kept preview, as before; one of
        another board does neither (_GenerateDrawsPreview), so that no
        preview is answered with another board's picture."""
        return super().Generate(board)

    def _GenerateDrawsPreview(self, target):
        # The preview is RENDER_BOARD's (None: RenderLane's own default).
        return target is None or target == RENDER_BOARD

    def _RenderBoard(self, kind):
        """The board a run of `kind` draws."""
        if kind == GENERATE:
            return self._GenerateTarget() or RENDER_BOARD
        return RENDER_BOARD

    def _RunPayload(self, kind):
        return self.RenderPayload(
            preview=kind == PREVIEW, games=GameAssetCatalog.instance,
            board=self._RenderBoard(kind))

    def _OutputStem(self, data):
        # Only a generate saves an image (RenderLane._Save).
        return OutputStem(data, self._RenderBoard(GENERATE))
