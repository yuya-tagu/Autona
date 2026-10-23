"""The game asset catalog: the Qt-free source of truth for game assets.

ADR-005 S1a. Everything the app knows about the installed games and the
selected one -- characters, skins, variants, preset colours, stages, asset
file paths, the Start.gg id tables -- lives here as plain dicts. The Qt side
(`src/AutonaGameAssetManager.py`) only turns a snapshot into
QStandardItemModels for the combos; it no longer holds any of the data.

Snapshots
---------
The loaded game is published as one immutable `GameAssetSnapshot`. A load
builds a complete new snapshot off to the side and swaps the reference in one
assignment, so a reader that takes `snap = catalog.Current()` once sees one
game from start to end -- never characters of the new game with the stage
table of the old one. Nothing mutates a snapshot after it was published; the
dicts inside it are plain dicts by design (they are serialised, handed to
Qt and to the state), so that is a convention, and the readers below return
deep copies wherever a caller could reasonably write into the result.
`Current()` and the adapter's read-only properties hand out references:
treat them as read-only.

`generation` identifies the asset set the snapshot was built from. Every
load gets a new one; `ForgetSelection()` keeps it, because it changes the
selection and not the assets (the Qt models stay valid).

Locking rules
-------------
- Writers (`ResetGameState`, `ScanGames`, `SelectGame`, `ForgetSelection`,
  `ReloadStartGGCharacters`) are serialised by one `threading.Lock`.
  Readers take no lock: they read `self._current` (or `self._games`) once at
  the top and work from that reference.
- Never call `run_sync`, emit a Qt signal or notify an observer while holding
  the writer lock: the GUI thread may be waiting on the very same lock
  (the synchronous load path, `ForgetSelection`), and that is a deadlock.
  The one callback invoked under the lock is `SelectGame`'s `progress`; it
  must not block the calling thread (the Qt adapter passes a status update,
  which is a queued signal when the load runs on a worker).
- `game.*` is published inside the lock, so the snapshot swap and the state
  can never disagree, and the `StateManager.SaveBlock()` is opened *outside*
  it: the save cycle (and with it every observer) runs after the lock is
  released. Lock order is therefore catalog lock -> StateManager.lock; never
  call a catalog writer from a state observer.
- The catalog has no observer channel of its own and must not grow one
  (core services are called, they do not subscribe or publish events --
  the Qt adapter's `onLoad` signal is the notification).

Construction is side-effect free; bootstrap_core() calls `ResetGameState()`
to publish the empty `game` state, and the Qt adapter drives `ScanGames()`.
"""
import glob
import os
import re
import shutil
import threading
import traceback
import types
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

import orjson
import requests
from loguru import logger

from . import StateSchema as schema
from .DictHelper import deep_clone, qvariantmap_copy
from .StateManager import StateManager

GAMES_DIR = "./user_data/games/"

# characters.json is shipped as a tracked seed in assets/ but is refreshed from
# the start.gg API at every startup. Writing the runtime copy back into the
# tracked seed dirties the working tree and risks committing API data (see
# known-smells S-15). The runtime download targets user_data/ (gitignored),
# and reads prefer that copy, falling back to the tracked seed for a fresh
# clone.
CHARACTERS_SEED_PATH = './assets/characters.json'
CHARACTERS_RUNTIME_PATH = './user_data/characters.json'
STARTGG_CHARACTERS_URL = 'https://api.start.gg/characters'

# Stock icon used for a character without any icon file. Stored as a path:
# the image itself is decoded by the Qt adapter.
CANCEL_ICON_PATH = './assets/icons/cancel.svg'


def _resolve_characters_path() -> str:
    if os.path.exists(CHARACTERS_RUNTIME_PATH):
        return CHARACTERS_RUNTIME_PATH
    return CHARACTERS_SEED_PATH


def _utf16_key(row):
    """Sort key matching QStandardItemModel.sort(0) on a row's text.

    Qt compares QStrings by UTF-16 code unit (case-sensitive, not
    locale-aware), which differs from Python's code-point order for
    characters outside the BMP. An invalid QVariant (text None) sorts after
    everything else. Combined with Python's stable sort this reproduces
    Qt's stable_sort exactly.
    """
    text = row[0]
    if text is None:
        return (1, b"")
    if not isinstance(text, str):
        text = str(text)
    return (0, text.encode("utf-16-be"))


def _utf16_sorted(rows):
    return tuple(sorted(rows, key=_utf16_key))


@dataclass(frozen=True)
class GameAssetSnapshot:
    """One loaded game, immutable once published.

    The `*Rows` fields are the combo contents in display order, as
    `(text, data)` pairs -- the blank first row the combos add is not part of
    them. `skinRows[en_name]` are `(skin, text, data)` triples.
    """
    generation: int = 0
    codename: Optional[str] = None
    # The selected game's config (the old `selectedGame`), with the
    # per-load annotations: codename, asset pack `complete` flags, the
    # translated character/stage/variant/colour names.
    game: dict = field(default_factory=dict)
    modsActive: bool = False
    characters: dict = field(default_factory=dict)
    characterRows: tuple = ()
    skins: dict = field(default_factory=dict)
    skinRows: dict = field(default_factory=dict)
    variants: dict = field(default_factory=dict)
    variantRows: tuple = ()
    colors: list = field(default_factory=list)
    colorRows: tuple = ()
    stages: dict = field(default_factory=dict)
    # Config order (the stage combo) and UTF-16 order (the with-blank combo
    # of the per-game tracker, which is the only sorted stage list).
    stageRows: tuple = ()
    stageRowsSorted: tuple = ()
    # {en_name: {skin: path}} -- decoded into QIcons by the adapter.
    stockIconPaths: dict = field(default_factory=dict)
    # {codename: {asset pack: [filename, ...]}}: the one directory scan per
    # load that CharacterAssets() reads instead of the filesystem.
    assetIndex: dict = field(default_factory=dict)
    # {variant codename: {"path": str, "image_size": any}}
    variantIconIndex: dict = field(default_factory=dict)
    biggestCompletePack: Optional[str] = None
    hasModdedContent: bool = False


def _localized(table, locale, remap, split):
    """The old per-entity lookup chain: exact, base language, remap."""
    if locale.replace("-", "_") in table:
        return table[locale.replace("-", "_")], True
    base = split(locale)
    if base in table:
        return table[base], True
    remapped = remap(locale)
    if remapped in table:
        return table[remapped], True
    return None, False


def _split_any(locale):
    return re.split("-|_", locale)[0]


def _split_dash(locale):
    return locale.split('-')[0]


def _CharacterAssets(game, assetIndex, characterCodename, skin, assetpack,
                     exportLocale):
    """Every asset pack's file (and pack metadata) for one character skin.

    Ported unchanged from AutonaGameAssetManager.GetCharacterAssets, except
    that the file list comes from `assetIndex` (built at load time) instead
    of the filesystem, and an explicit `assetpack` limits the result to that
    pack (the old argument could not work: it iterated a string as pairs).
    """
    charFiles = {}

    if game is None:
        return charFiles

    assets = game.get("assets", {})
    if assetpack:
        assetsPacks = [(assetpack, assets[assetpack])] \
            if assetpack in assets else []
    else:
        assetsPacks = assets.items()

    for assetKey, asset in assetsPacks:
        try:
            # Skip stage icon asset packs
            if type(asset.get("type")) == list:
                if "stage_icon" in asset.get("type") or "variant_icon" in asset.get("type"):
                    continue
            elif type(asset.get("type")) == str:
                if asset.get("type") in ["stage_icon", "variant_icon"]:
                    continue

            assetPath = f'{game.get("path")}/{assetKey}/'

            baseName = asset.get(
                "prefix", "")+characterCodename+asset.get("postfix", "")

            # Filenames from the load-time index (no filesystem access).
            # Falls back to a live scan only if the codename/pack was not
            # indexed (a codename that is not one of this game's characters).
            skinFileList = assetIndex.get(
                characterCodename, {}).get(assetKey)
            if skinFileList is None:
                try:
                    skinFileList = [f for f in os.listdir(
                        assetPath) if f.startswith(baseName)]
                except OSError:
                    skinFileList = []

            skinFiles = {}

            for f in skinFileList:
                skinId = f[len(baseName):].rsplit(".", 1)[0]
                if skinId == "":
                    skinId = 0
                else:
                    skinId = int(skinId)
                skinFiles[skinId] = f

            if len(skinFiles) > 0:
                charFiles[assetKey] = {
                    "type": asset.get("type", [])
                }

                # If skin exists, load it
                if skin in skinFiles:
                    charFiles[assetKey]["asset"] = assetPath + \
                        skinFiles[skin]
                else:
                    # If skin remap exists, load remap
                    if asset.get("skin_mapping", {}).get(characterCodename, {}).get(str(skin)):
                        target = int(asset.get("skin_mapping", {}).get(
                            characterCodename, {}).get(str(skin)))
                        charFiles[assetKey]["asset"] = assetPath + \
                            skinFiles[target]
                    # If none is found, default to skin 0
                    else:
                        charFiles[assetKey]["asset"] = assetPath + \
                            skinFiles[0]

            if asset.get("eyesights"):
                eyesights = asset.get("eyesights", {}).get(
                    characterCodename, {})

                if len(eyesights.keys()) > 0:
                    if str(skin) in eyesights:
                        if assetKey in charFiles:
                            charFiles[assetKey] = charFiles.get(assetKey, {})
                            charFiles[assetKey]["eyesight"] = eyesights.get(
                                str(skin))
                    else:
                        charFiles[assetKey] = charFiles.get(assetKey, {})
                        charFiles[assetKey]["eyesight"] = list(
                            eyesights.values())[0]

            if asset.get("image_sizes"):
                image_sizes = asset.get("image_sizes", {}).get(
                    characterCodename, {})

                if len(image_sizes.keys()) > 0:
                    if str(skin) in image_sizes:
                        if assetKey in charFiles:
                            charFiles[assetKey]["image_size"] = image_sizes.get(
                                str(skin))
                    else:
                        charFiles[assetKey]["image_size"] = list(
                            image_sizes.values())[0]

            if asset.get("rescaling_factor"):
                rescaling_factor = asset.get("rescaling_factor", {}).get(
                    characterCodename, {})

                if len(rescaling_factor.keys()) > 0:
                    if str(skin) in rescaling_factor:
                        if assetKey in charFiles:
                            charFiles[assetKey]["rescaling_factor"] = rescaling_factor.get(
                                str(skin))
                    else:
                        charFiles[assetKey]["rescaling_factor"] = rescaling_factor.get(
                            "0", 1)

            if asset.get("unflippable"):
                unflippable = asset.get("unflippable", {}).get(
                    characterCodename, {})

                if len(unflippable.keys()) > 0:
                    if str(skin) in unflippable:
                        if assetKey in charFiles:
                            charFiles[assetKey]["unflippable"] = unflippable.get(
                                str(skin))
                    else:
                        charFiles[assetKey]["unflippable"] = list(
                            unflippable.values())[0]

            if asset.get("metadata"):
                metadata = {}
                charFiles[assetKey]['metadata'] = {}
                for key in range(len(asset.get("metadata"))):
                    metadata[key] = asset.get("metadata", {})[key]["values"].get(
                        characterCodename, {}).get("value", "")
                    charFiles[assetKey]['metadata'][f"{key}"] = {}
                    charFiles[assetKey]['metadata'][f"{key}"]["title_en"] = asset.get("metadata", {})[
                        key].get("title", '')
                    if exportLocale in asset.get("metadata", {})[key].get("locale", {}).keys() or exportLocale.split('-')[0] in asset.get("metadata", {})[key].get("locale", {}).keys():
                        try:
                            metadata_title_locale = asset.get("metadata", {})[key].get("locale", {})[
                                exportLocale]
                        except KeyError:
                            metadata_title_locale = asset.get("metadata", {})[key].get(
                                "locale", {})[exportLocale.split('-')[0]]
                    else:
                        metadata_title_locale = asset.get("metadata", {})[
                            key].get("title", '')
                    charFiles[assetKey]['metadata'][f"{key}"]["title"] = metadata_title_locale
                    charFiles[assetKey]['metadata'][f"{key}"][f"value_en"] = metadata[key]
                    if exportLocale in asset.get("metadata", {})[key]["values"].get(characterCodename, {}).get("locale", {}).keys() or exportLocale.split('-')[0] in asset.get("metadata", {})[key]["values"].get(characterCodename, {}).get("locale", {}).keys():
                        try:
                            metadata[key] = asset.get("metadata", {})[key]["values"].get(
                                characterCodename, {}).get("locale", {})[exportLocale]
                        except KeyError:
                            metadata[key] = asset.get("metadata", {})[key]["values"].get(
                                characterCodename, {}).get("locale", {})[exportLocale.split('-')[0]]
                    charFiles[assetKey]['metadata'][f"{key}"][f"value"] = metadata[key]

            if asset.get("uncropped_edge"):
                if assetKey in charFiles:
                    charFiles[assetKey]["uncropped_edge"] = asset.get(
                        "uncropped_edge")

            if asset.get("average_size"):
                if assetKey in charFiles:
                    charFiles[assetKey]["average_size"] = asset.get(
                        "average_size")
        except Exception:
            logger.error(traceback.format_exc())

    return charFiles


class GameAssetCatalog:
    instance: "GameAssetCatalog" = None

    def __init__(self, locales: Callable = None, remap: Callable = None):
        """`locales()` returns (program locale, export locale) at call time,
        `remap(locale)` the locale remap name or None. Both are injected
        because the tables live in AutonaLocaleHelper, which imports Qt
        (same pattern as AutonaActions' formatMatchText)."""
        self._locales = locales or (lambda: ("en-US", "en-US"))
        self._remap = remap or (lambda language: None)
        self._lock = threading.Lock()
        self._generation = 0
        self._current = GameAssetSnapshot()
        # codename -> game config as scanned. Replaced wholesale by
        # ScanGames(), never mutated afterwards: SelectGame() builds from a
        # copy, so the scan stays pristine across loads.
        self._games = {}
        # str(start.gg character id) -> start.gg character entity
        self._startggCharacters = {}

    # ── readers (no lock: one reference read, then work from it) ─────────

    def Current(self) -> GameAssetSnapshot:
        return self._current

    def Games(self) -> dict:
        """Deep copy of every scanned game config, in scan order."""
        return deep_clone(self._games)

    def GamesView(self):
        """Read-only view of the scanned game configs (no copy).

        For hot read paths that only look things up; the nested dicts are
        the catalog's own and must not be written into."""
        return types.MappingProxyType(self._games)

    def GameCodenames(self) -> list:
        """Scanned game codenames in scan order (the 1-based game index the
        Qt side uses is a position in this list)."""
        return list(self._games.keys())

    def GamesPayload(self) -> dict:
        """The legacy `get_games` payload."""
        games = self._games
        data = {}
        for key, game in games.items():
            data[key] = {
                "name": game.get("name"),
                "locale": deep_clone(game.get("locale")),
                "smashgg_game_id": game.get("smashgg_game_id"),
                "has_stages": bool(game.get("stage_to_codename")),
                "has_variants": bool(game.get("variant_to_codename")),
                "has_colors": bool(game.get("preset_colors"))
            }
        return data

    def CharactersPayload(self) -> dict:
        """The legacy `get_characters` payload: the character combo's rows
        (in display order, modded ones only when mods are active), each with
        its skin list, keyed by export name. Nested key order is the one the
        model-backed reader produced (see DictHelper.qvariantmap_copy), with
        `skins` appended last as it did."""
        snap = self._current
        data = {}
        for _text, row in snap.characterRows:
            item = qvariantmap_copy(row)
            item["skins"] = [qvariantmap_copy(skinData)
                             for _skin, _label, skinData
                             in snap.skinRows.get(item.get("en_name"), ())]
            data[item.get("name")] = item
        return data

    def VariantsPayload(self) -> dict:
        """The legacy `get_variants` payload, keyed by export name (nested
        key order as in CharactersPayload)."""
        snap = self._current
        data = {}
        for _text, row in snap.variantRows:
            data[row.get("name")] = qvariantmap_copy(row)
        return data

    def Character(self, en_name):
        character = self._current.characters.get(en_name)
        return deep_clone(character) if character is not None else None

    # The three combo rows a player card's character slot picks from, keyed
    # the way the card names its pick (ADR-005 S1d): the character and the
    # variant by en_name -- the key of their tables, so unique -- and the
    # skin by its row in that character's skin combo. `snapshot` as in
    # CharacterFromStartGGId: one slot (or one card) resolves against one
    # game. Copies, in insertion order; PlayerService decides what the state
    # holds.

    def CharacterRow(self, en_name, snapshot=None):
        """The character combo row's data for `en_name`, or None when the
        combo has no such row (an unknown name, or a modded character while
        mods are off)."""
        snap = snapshot if snapshot is not None else self._current
        for _text, data in snap.characterRows:
            if data.get("en_name") == en_name:
                return deep_clone(data)
        return None

    def SkinRow(self, en_name, row, snapshot=None):
        """The data of row `row` of `en_name`'s skin combo, or None.

        A row, not a skin number: the rows are the skins in number order, so
        the two differ as soon as the numbering has a gap. The row is what a
        card has always published as `skin` (and what the player database
        stores in `mains`)."""
        snap = snapshot if snapshot is not None else self._current
        rows = snap.skinRows.get(en_name, ())
        if not isinstance(row, int) or isinstance(row, bool) \
                or row < 0 or row >= len(rows):
            return None
        return deep_clone(rows[row][2])

    def VariantRow(self, en_name, snapshot=None):
        """The variant combo row's data for `en_name`, or None."""
        snap = snapshot if snapshot is not None else self._current
        for _text, data in snap.variantRows:
            if data.get("en_name") == en_name:
                return deep_clone(data)
        return None

    def CharacterByCodename(self, codename):
        for character in self._current.characters.values():
            if character.get("codename") == codename:
                return deep_clone(character)
        return None

    def Skins(self, en_name) -> list:
        """The character's skin combo rows: [{"skin", "label", "data"}]."""
        return [{"skin": skin, "label": label, "data": deep_clone(data)}
                for skin, label, data
                in self._current.skinRows.get(en_name, ())]

    def Variant(self, en_name):
        variant = self._current.variants.get(en_name)
        return deep_clone(variant) if variant is not None else None

    def PresetColor(self, row):
        """The preset colour at colour-combo row `row` (row 0 is the blank
        entry), or None."""
        rows = self._current.colorRows
        if not isinstance(row, int) or row < 1 or row > len(rows):
            return None
        return deep_clone(rows[row - 1][1])

    def CharacterAssets(self, codename, skin, assetpack=None) -> dict:
        snap = self._current
        return deep_clone(_CharacterAssets(
            snap.game, snap.assetIndex, codename, skin, assetpack,
            self._locales()[1]))

    def StockIconPath(self, en_name, skin=0):
        return self._current.stockIconPaths.get(en_name, {}).get(skin)

    def CharacterFromStartGGId(self, smashgg_id, snapshot=None):
        """(en_name, character) for a start.gg character id, or None.

        `snapshot` (default: the current one) is for a reader resolving
        several ids into one result: it takes `Current()` once and passes
        it to every lookup, so a load landing meanwhile cannot mix two
        games into that result. The same holds for StageFromStartGGId.
        The start.gg id -> name table (`_startggCharacters`) is not part of
        the snapshot; it does not depend on the game, so reading the live
        one is harmless."""
        snap = snapshot if snapshot is not None else self._current
        startggcharacter = self._startggCharacters.get(str(smashgg_id))
        if startggcharacter:
            character = next(
                (c for c in snap.characters.items()
                 if c[1].get("smashgg_name") == startggcharacter.get("name")),
                None)
            if character:
                return (character[0], deep_clone(character[1]))
        return None

    def StageFromStartGGId(self, smashgg_id, snapshot=None):
        """(stage key, stage) for a start.gg stage id, or None."""
        snap = snapshot if snapshot is not None else self._current
        stage = next(
            (s for s in snap.stages.items()
             if str(s[1].get("smashgg_id")) == str(smashgg_id)),
            None)
        if stage:
            return (stage[0], deep_clone(stage[1]))
        return None

    def GameFromStartGGId(self, gameid):
        """Codename of the first scanned game matching a start.gg videogame
        id (its own or an alternate version's), or None."""
        for codename, game in self._games.items():
            if str(game.get("smashgg_game_id", "")) == str(gameid):
                return codename
            alternates_ids = [
                str(alternate.get("smashgg_game_id"))
                for alternate in game.get("alternate_versions", [])
                if alternate.get("smashgg_game_id")]
            if str(gameid) in alternates_ids:
                return codename
        return None

    # ── writers (serialised by self._lock) ───────────────────────────────

    def _NextGeneration(self):
        self._generation += 1
        return self._generation

    def ResetGameState(self):
        """Drop the selection and publish an empty `game`."""
        with StateManager.SaveBlock():
            with self._lock:
                self._current = GameAssetSnapshot(
                    generation=self._NextGeneration())
                StateManager.Set(schema.GAME_ROOT, {})

    def ForgetSelection(self):
        """No game is selected any more (the tournament URL was cleared).

        Only the selection goes: the assets, the models built from them and
        the `game` state stay, exactly as the old `selectedGame = {}` did.
        A later SelectGame() of the same game therefore loads it again
        instead of being skipped."""
        with self._lock:
            self._current = replace(self._current, game={}, codename=None)

    def ReloadStartGGCharacters(self):
        """Re-read the start.gg character table (runtime copy, else seed)."""
        path = _resolve_characters_path()
        try:
            with open(path, 'rb') as f:
                sggcharacters = orjson.loads(f.read())
        except Exception:
            logger.error(f"Could not read {path}: {traceback.format_exc()}")
            return
        table = {}
        for c in sggcharacters.get("entities", {}).get("character", []):
            table[str(c.get("id"))] = c
        with self._lock:
            self._startggCharacters = table

    def DownloadStartGGCharacters(self):
        """Refresh user_data/characters.json from the start.gg API. Blocking.

        Touches the file only, never the catalog's memory, so it takes no
        lock: a slow or hung request (there is no timeout) must not hold up
        a game load. The table is picked up by the next
        ReloadStartGGCharacters()."""
        tmp_path = CHARACTERS_RUNTIME_PATH + '.tmp'
        try:
            r = requests.get(STARTGG_CHARACTERS_URL, allow_redirects=True)
            r_json = orjson.dumps(orjson.loads(
                r.text), option=orjson.OPT_INDENT_2)

            # Write runtime copy into user_data/ (gitignored), never the
            # tracked seed in assets/ (known-smells S-15)
            os.makedirs(
                os.path.dirname(CHARACTERS_RUNTIME_PATH), exist_ok=True)
            with open(tmp_path, 'wb') as f:
                f.write(r_json)

            try:
                # Test if downloaded JSON is valid
                with open(tmp_path, 'rb') as f:
                    orjson.loads(f.read())

                # Atomically overwrite the runtime copy (os.replace works
                # whether or not the target already exists)
                os.replace(tmp_path, CHARACTERS_RUNTIME_PATH)

                logger.info("startgg characters file updated")
            except Exception:
                logger.error("Characters file download failed")
        except Exception as e:
            logger.error(
                "Could not update " + CHARACTERS_RUNTIME_PATH + ": " + str(e))

    def ScanGames(self):
        """List ./user_data/games and read every game's and pack's config."""
        with self._lock:
            # The scan lists this directory unconditionally, so the catalog
            # guarantees it (it used to be the asset manager's __init__).
            os.makedirs(GAMES_DIR, exist_ok=True)
            programLocale = self._locales()[0]
            games = {}

            for game in os.listdir(GAMES_DIR):
                base = GAMES_DIR + game
                if not os.path.isfile(base + "/base_files/config.json"):
                    logger.info("Game config for "+game+" doesn't exist.")
                    continue

                # One broken pack (unreadable config.json, wrong shape, ...)
                # is logged and left out; the scan carries on and the index
                # is still replaced with every pack that did read. A broken
                # asset-pack config inside a game only drops that asset pack.
                try:
                    games[game] = self._ReadGame(game, base, programLocale)
                except Exception:
                    logger.error(
                        f"Skipping game pack {game}: could not read its "
                        f"config\n{traceback.format_exc()}")

            self._games = games

    @staticmethod
    def _ReadJsonObject(path):
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
        if not isinstance(data, dict):
            raise ValueError(
                f"{path}: expected a JSON object, got {type(data).__name__}")
        return data

    def _ReadGame(self, game, base, programLocale):
        """Read one game pack's config and its asset packs' configs."""
        gameData = self._ReadJsonObject(base + "/base_files/config.json")

        # Try logo_small, if it doesn't exist use logo. Path only:
        # images are decoded by the Qt side on the GUI thread.
        if os.path.isfile(base + "/base_files/logo_small.png"):
            gameData["logo_path"] = base + "/base_files/logo_small.png"
        elif os.path.isfile(base + "/base_files/logo.png"):
            gameData["logo_path"] = base + "/base_files/logo.png"

        gameData["assets"] = {}
        gameData["path"] = base + "/"

        assetDirs = os.listdir(base)
        assetDirs += ["base_files/" + f
                      for f in os.listdir(base + "/base_files/")]

        for dir in assetDirs:
            if os.path.isdir(base + "/" + dir):
                if os.path.isfile(base + "/" + dir + "/config.json"):
                    logger.info(
                        "Found asset config for ["+game+"]["+dir+"]")
                    try:
                        gameData["assets"][dir] = self._ReadJsonObject(
                            base + "/" + dir + "/config.json")
                    except Exception:
                        logger.error(
                            f"Skipping asset pack [{game}][{dir}]: could "
                            f"not read config.json\n{traceback.format_exc()}")
                else:
                    logger.error(
                        "No config file for "+game+" - "+dir)

        # Translate the game name
        gameLocales = gameData.get("locale", {})
        if programLocale.replace('-', '_') in gameLocales:
            game_name = gameLocales[programLocale.replace('-', '_')].get("name")
            if game_name:
                gameData["name"] = game_name
        elif programLocale.split('-')[0] in gameLocales:
            game_name = gameLocales[programLocale.split('-')[0]].get("name")
            if game_name:
                gameData["name"] = game_name

        return gameData

    def SelectGame(self, codename, modsActive=False, reload=False,
                   progress=None):
        """Load `codename` and publish it. Returns the new snapshot, or None
        when nothing was loaded.

        Skipped (None, nothing touched -- no CSS copy, no state write):
        no games scanned; an empty codename (the tournament URL was cleared:
        keep whatever is loaded, PdM decision 2026-10-23); an unknown
        codename; the selected game again without `reload`.

        `progress(done, total)` is called once with done=0 when the load
        actually starts, then per character in two passes. It runs under the
        writer lock (see the module docstring).
        """
        with StateManager.SaveBlock():
            with self._lock:
                if len(self._games) == 0:
                    return None
                if not codename:
                    return None
                if codename == self._current.codename and not reload:
                    return None
                config = self._games.get(codename)
                if config is None:
                    logger.warning(f"Unknown game codename: {codename}")
                    return None

                snapshot = self._Build(codename, config, modsActive, progress)
                self._current = snapshot
                self._Publish(snapshot)
                return snapshot

    def _Publish(self, snapshot):
        # Same two writes, in the same order, as the old asset loader.
        game = snapshot.game
        StateManager.Set(schema.GAME_ROOT, {
            "name": game.get("name"),
            "abbreviation": game.get("abbreviation"),
            "smashgg_id": game.get("smashgg_game_id"),
            "codename": game.get("codename"),
            "logo": game.get("path", "")+"/base_files/logo.png",
            # A copy: the state must not alias the snapshot.
            "defaults": deep_clone(game.get("defaults")),
            "mods_active": snapshot.modsActive,
            "has_stages": bool(game.get("stage_to_codename")),
            "has_variants": bool(game.get("variant_to_codename")),
            "has_colors": bool(game.get("preset_colors"))
        })
        StateManager.Set(f"{schema.GAME_ROOT}.has_modded_content",
                         snapshot.hasModdedContent)

    @staticmethod
    def _CopyCSS(game):
        # Make dir if doesn't exists
        css_dir_path = "./out/css"
        if not os.path.isdir(css_dir_path):
            os.mkdir(css_dir_path)

        # Empty dir and remove all CSS
        try:
            list_current_files = glob.glob(f"{css_dir_path}/*.css")
        except OSError as e:
            logger.error(f"Error listing CSS files in {css_dir_path}: {e}")
            list_current_files = []
        for file_path in list_current_files:
            try:
                os.remove(file_path)
            except OSError as e:
                logger.error(f"Error removing CSS file {file_path}: {e}")

        # Copy game CSS files
        game_css_path = f"{GAMES_DIR}{game}/base_files/css"
        if os.path.isdir(game_css_path):
            try:
                list_game_css_files = glob.glob(f"{game_css_path}/*.css")
            except OSError as e:
                logger.error(f"Error listing CSS files in {game_css_path}: {e}")
                list_game_css_files = []
            for file_path in list_game_css_files:
                logger.info("Copying CSS file: "+file_path)
                try:
                    shutil.copy(file_path, css_dir_path)
                except OSError as e:
                    logger.error(f"Error copying CSS file {file_path}: {e}")

        logger.info("Game CSS file copy complete")

    def _Build(self, game, config, modsActive, progress):
        """Build a complete snapshot for `game` from a copy of its config.

        Ported from the old _LoadGameAssetsCore plus the data half of the
        Update*Model methods (labels, payloads, order, modded filtering).
        Annotations are written into the copy only; the scanned config and
        every published snapshot stay as they were.
        """
        programLocale, exportLocale = self._locales()
        remap = self._remap

        logger.info("Changed to game: "+game)

        gameObj = deep_clone(config)
        gameObj["codename"] = game

        characters = gameObj.get("character_to_codename", {})
        variants = gameObj.get("variant_to_codename", {})
        colors = gameObj.get("preset_colors", [])

        progressTotal = max(1, len(characters) * 2)

        def reportProgress(done):
            if progress is not None:
                progress(done, progressTotal)

        reportProgress(0)

        self._CopyCSS(game)

        assets = gameObj.get("assets", {})

        # ── stock icons ──────────────────────────────────────────────────
        assetsKey = ""
        if len(list(assets.keys())) > 0:
            assetsKey = list(assets.keys())[0]

        for asset in list(assets.keys()):
            if "icon" in assets[asset].get("type", ""):
                assetsKey = asset
                break

        assetsObj = assets.get(assetsKey, None)
        files = sorted(os.listdir(GAMES_DIR+game+'/'+assetsKey))

        stockIcons = {}

        for charIndex, c in enumerate(characters.keys()):
            reportProgress(charIndex + 1)
            stockIcons[c] = {}

            filteredFiles = \
                [f for f in files if f.startswith(assetsObj.get(
                    "prefix", "")+characters[c].get("codename")+assetsObj.get("postfix", ""))]

            if len(filteredFiles) == 0:
                stockIcons[c][0] = CANCEL_ICON_PATH

            for f in filteredFiles:
                numberStart = f.rfind(
                    assetsObj.get("postfix", "")) + len(assetsObj.get("postfix", ""))
                numberEnd = f.rfind(".")
                number = 0
                try:
                    number = int(f[numberStart:numberEnd])
                except Exception:
                    logger.error(f)
                stockIcons[c][number] = GAMES_DIR+game+'/'+assetsKey+'/'+f

        logger.info("Loaded stock icons")

        # ── skins + the asset file index ─────────────────────────────────
        skins = {}
        packSkinMask = {}
        assetIndex = {}

        # List each asset pack directory once (the path depends only on the
        # pack, not the character/skin), then index every file. This is the
        # single scan CharacterAssets() reads instead of the filesystem.
        packFiles = {}
        for assetsKey in list(gameObj["assets"].keys()):
            try:
                packFiles[assetsKey] = sorted(os.listdir(
                    GAMES_DIR+game+'/'+assetsKey))
            except OSError:
                packFiles[assetsKey] = []

        for charIndex, c in enumerate(characters.keys()):
            reportProgress(len(characters) + charIndex + 1)
            skins[c] = {}
            codename = characters[c].get("codename")
            for assetsKey in list(gameObj["assets"].keys()):
                asset = gameObj["assets"][assetsKey]

                filteredFiles = \
                    [f for f in packFiles[assetsKey] if f.startswith(asset.get(
                        "prefix", "")+codename+asset.get("postfix", ""))]

                # The filename list, verbatim: CharacterAssets parses skin
                # numbers with its own logic. (The old loader also wrote
                # {skin: filename} into this same list by index, which
                # corrupted it -- and dropped skins 8/9 -- once a character
                # had 10+ skins, and aborted the whole load on a gap in the
                # numbering. That second write is gone.)
                assetIndex.setdefault(codename, {})[assetsKey] = filteredFiles

                for f in filteredFiles:
                    numberStart = f.rfind(
                        asset.get("postfix", "")) + len(asset.get("postfix", ""))
                    numberEnd = f.rfind(".")
                    number = 0
                    try:
                        number = int(f[numberStart:numberEnd])
                    except Exception:
                        pass
                    skins[c][number] = True

                    if c not in packSkinMask:
                        packSkinMask[c] = {}

                    if assetsKey not in packSkinMask[c]:
                        packSkinMask[c][assetsKey] = set()

                    packSkinMask[c][assetsKey].add(number)

            logger.info("Character "+c+" has " +
                        str(len(skins[c]))+" skins")

        # Set complete
        for assetsKey in list(assets.keys()):
            try:
                complete = True

                for c in characters.keys():
                    if "random" in c.lower():
                        continue
                    for skin in skins[c].keys():
                        if assetsKey not in packSkinMask[c] or skin not in packSkinMask[c][assetsKey]:
                            complete = False
                            break

                assets[assetsKey]["complete"] = complete
            except Exception:
                logger.error(traceback.format_exc())

        # Get biggest complete pack
        biggestCompletePack = "base_files/icon"
        biggestAverage = 0

        for asset in list(assets.keys()):
            if assets[asset].get("complete") and assets[asset].get("average_size") and asset not in ["stage_icon", "variant_icon"]:
                size = sum(assets[asset].get("average_size").values())

                if size > biggestAverage:
                    biggestCompletePack = asset
                    biggestAverage = size

        logger.info("Biggest complete assets: " + biggestCompletePack)

        # ── stage icon paths ─────────────────────────────────────────────
        stageKey = None

        for asset in list(assets.keys()):
            if "stage_icon" in assets[asset].get("type", ""):
                stageKey = asset
                break

        stages = gameObj.get("stage_to_codename", {})

        if stageKey:
            stageAssets = assets.get(stageKey)
            for stage in stages:
                stages[stage]["path"] = GAMES_DIR+game+'/'+stageKey+'/'+stageAssets.get(
                    "prefix", "")+stages[stage].get("codename", "")+stageAssets.get("postfix", "")+".png"

        for s in stages.keys():
            stages[s]["name"] = s

        # ── variant icon index ───────────────────────────────────────────
        # One config read and one listdir: each variant's icon path and
        # image_size.
        variantIconIndex = {}
        variant_icon_dir = GAMES_DIR+game+'/variant_icon'
        variant_icon_config_path = variant_icon_dir+'/config.json'
        if variants and os.path.isfile(variant_icon_config_path):
            try:
                with open(variant_icon_config_path, "rt", encoding="utf-8") as f:
                    variant_icon_config = orjson.loads(f.read())
                variant_files = set(os.listdir(variant_icon_dir))
                prefix = variant_icon_config.get("prefix")
                postfix = variant_icon_config.get("postfix")
                image_sizes = variant_icon_config.get("image_sizes", {})
                extensions = ["png", "jpg", "gif", "webp"]
                codenames = {variants[v].get("codename") for v in variants}
                for variant_codename in codenames:
                    if variant_codename is None:
                        continue
                    base = f"{prefix}{variant_codename}{postfix}"
                    icon_path = ""
                    for extension in extensions:
                        if f"{base}.{extension}" in variant_files:
                            icon_path = f"{variant_icon_dir}/{base}.{extension}"
                            break
                    # image_size only when a .png exists:
                    # image_sizes[codename]["null"].
                    image_size = None
                    if f"{base}.png" in variant_files:
                        image_size = image_sizes.get(
                            variant_codename, {}).get("null")
                    variantIconIndex[variant_codename] = {
                        "path": icon_path,
                        "image_size": image_size,
                    }
            except Exception:
                logger.error(traceback.format_exc())

        # ── translated names ─────────────────────────────────────────────
        for table, label in ((characters, "characters"),
                             (variants, "variants")):
            try:
                for c in table.keys():
                    display_name = c
                    export_name = c

                    if table[c].get("locale"):
                        value, found = _localized(
                            table[c]["locale"], programLocale, remap,
                            _split_any)
                        if found:
                            display_name = value
                        value, found = _localized(
                            table[c]["locale"], exportLocale, remap,
                            _split_any)
                        if found:
                            export_name = value

                    table[c]["display_name"] = display_name
                    table[c]["export_name"] = export_name
                    table[c]["en_name"] = c
            except Exception:
                logger.error(f"{label}: {traceback.format_exc()}")

        try:
            for color in colors:
                display_name = color.get("name")
                export_name = color.get("name")
                en_name = color.get("name")

                if color.get("locale"):
                    value, found = _localized(
                        color["locale"], programLocale, remap, _split_any)
                    if found:
                        display_name = value
                    value, found = _localized(
                        color["locale"], exportLocale, remap, _split_any)
                    if found:
                        export_name = value

                color["display_name"] = display_name
                color["export_name"] = export_name
                color["en_name"] = en_name
        except Exception:
            logger.error(traceback.format_exc())

        # ── combo rows (the data half of the old Update*Model) ───────────
        hasModdedContent = False

        characterRows = []
        try:
            for c in characters.keys():
                text = c
                data = {
                    "name": characters[c].get("export_name"),
                    "en_name": c,
                    "display_name": characters[c].get("display_name"),
                    "codename": characters[c].get("codename"),
                    "modded": characters[c].get("modded", False)
                }
                if characters[c].get("display_name") != c:
                    text = f'{characters[c].get("display_name")} / {c}'
                if data.get("modded"):
                    hasModdedContent = True
                # A modded character is left out of the combo while mods
                # are off (the old model built the item but never appended
                # it).
                if (not modsActive) and data.get("modded"):
                    continue
                characterRows.append((text, data))
        except Exception:
            logger.error(traceback.format_exc())

        def characterAssets(codename, skin):
            return _CharacterAssets(gameObj, assetIndex, codename, skin, None,
                                    exportLocale)

        skinRows = {}
        for key, character in characters.items():
            rows = []
            for skin in sorted(int(k) for k in skins.get(key, {}).keys()):
                assetData = {}
                assetData["assets"] = deep_clone(characterAssets(
                    character.get("codename"), skin)) or {}

                skinIndex = str(skin)

                skinNameData = characters.get(
                    character.get("en_name"), {}).get("skin_name", {})

                skin_name = character.get("name")
                skin_name_en = character.get("en_name")

                try:
                    entry = skinNameData.get(skinIndex, {})
                    locale = programLocale
                    if locale.replace("-", "_") in entry.get("locale", {}):
                        skin_name = entry.get(
                            "locale", {})[locale.replace("-", "_")]
                    elif re.split("-|_", locale)[0] in entry.get("locale", {}):
                        skin_name = entry.get(
                            "locale", {})[re.split("-|_", locale)[0]]
                    # Faithful to the old loader: the remap of the *export*
                    # locale, looked up in the skin table's own "locale".
                    elif remap(exportLocale) in skinNameData.get("locale", {}):
                        skin_name = entry.get("locale", {})[remap(exportLocale)]
                    elif entry.get("name"):
                        skin_name = entry.get("name")

                    if entry.get("name"):
                        skin_name_en = entry.get("name")
                except Exception:
                    logger.error(traceback.format_exc())

                if skinNameData.get(skinIndex, {}).get("is_different_character", False):
                    assetData["name"] = skin_name
                    assetData["en_name"] = skin_name_en

                rows.append((skin, skin_name if skin_name else skinIndex,
                             assetData))
            skinRows[key] = tuple(rows)

        variantRows = []
        try:
            for c in variants.keys():
                text = c
                data = {
                    "name": variants[c].get("export_name"),
                    "en_name": c,
                    "display_name": variants[c].get("display_name"),
                    "codename": variants[c].get("codename")
                }
                data["icon_path"] = variantIconIndex.get(
                    data["codename"], {}).get("path", "")
                data["image_size"] = variantIconIndex.get(
                    data["codename"], {}).get("image_size")
                if variants[c].get("display_name") != c:
                    text = f'{variants[c].get("display_name")} / {c}'
                variantRows.append((text, data))
        except Exception:
            logger.error(traceback.format_exc())

        colorRows = []
        try:
            for c in range(len(colors)):
                name = colors[c].get("name")
                text = name
                data = {
                    "name": colors[c].get("export_name"),
                    # The colour's index, not a name: what the old model
                    # stored, and what the payload readers get.
                    "en_name": c,
                    "display_name": colors[c].get("display_name"),
                    "value": colors[c].get("value"),
                    "force_opponent": colors[c].get("force_opponent")
                }
                if colors[c].get("display_name") != name:
                    text = f'{colors[c].get("display_name")} / {name}'
                colorRows.append((text, data))
        except Exception:
            logger.error(traceback.format_exc())

        stageRows = []
        try:
            for stage in gameObj.get("stage_to_codename", {}).values():
                # Stage name translations. `name` was reset to the stage key
                # above, so en_name is always the key.
                stage["en_name"] = stage.get("name")

                display_name = stage.get("name")
                value, found = _localized(
                    stage.get("locale", {}), programLocale, remap, _split_dash)
                if found:
                    display_name = value
                stage["display_name"] = display_name

                export_name = stage.get("name")
                value, found = _localized(
                    stage.get("locale", {}), exportLocale, remap, _split_dash)
                if found:
                    export_name = value
                stage["name"] = export_name

                text = f'{stage.get("display_name")} / {stage.get("en_name")}' \
                    if stage.get("display_name") != stage.get("en_name") \
                    else stage.get("display_name")

                if stage.get("modded"):
                    hasModdedContent = True
                if (not modsActive) and stage.get("modded"):
                    continue
                stageRows.append((text, stage))
        except Exception:
            logger.error(traceback.format_exc())

        return GameAssetSnapshot(
            generation=self._NextGeneration(),
            codename=game,
            game=gameObj,
            modsActive=modsActive,
            characters=characters,
            characterRows=_utf16_sorted(characterRows),
            skins=skins,
            skinRows=skinRows,
            variants=variants,
            variantRows=_utf16_sorted(variantRows),
            colors=colors,
            colorRows=_utf16_sorted(colorRows),
            stages=stages,
            stageRows=tuple(stageRows),
            stageRowsSorted=_utf16_sorted(stageRows),
            stockIconPaths=stockIcons,
            assetIndex=assetIndex,
            variantIconIndex=variantIconIndex,
            biggestCompletePack=biggestCompletePack,
            hasModdedContent=hasModdedContent,
        )
