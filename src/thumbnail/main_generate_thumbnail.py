# Thumbnail generation using HTML/CSS rendering via QWebEngineView.
# Data preparation is separated from rendering for async support.

import random
import traceback
from pathlib import Path
import json
import shutil
import datetime
import os
import re
from loguru import logger

from qtpy.QtWidgets import QApplication

from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.StateManager import StateManager
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.Helpers.AutonaDictHelper import *
from src.Helpers.AutonaDirHelper import AutonaResolve


def createFalseData(gameAssetManager: AutonaGameAssetManager = None, used_assets: str = None):
    """Generate mock data for preview mode."""
    chars = []

    if gameAssetManager and len(gameAssetManager.instance.characters.keys()) > 0:
        for i in range(4):
            asset = None
            if not asset:
                key = list(gameAssetManager.instance.characters.keys())[
                    random.randint(0, len(gameAssetManager.instance.characters)-1)]

                character = gameAssetManager.instance.characters[key]
                skin = random.randint(
                    0, len(gameAssetManager.instance.characters[key]))

                data = gameAssetManager.instance.GetCharacterAssets(
                    character.get("codename"), skin)

                asset = data.get(used_assets)

                if not asset:
                    asset = data.get("full")
                if not asset:
                    asset = data.get("portrait")
                if not asset:
                    asset = data.get("base_files/icon")

            name = key
            if character.get("locale"):
                locale = AutonaLocaleHelper.programLocale
                if locale.replace("-", "_") in character["locale"]:
                    name = character["locale"][locale.replace("-", "_")]
                elif re.split("-|_", locale)[0] in character["locale"]:
                    name = character["locale"][re.split("-|_", locale)[0]]

            chars.append({
                "name": name,
                "team": gameAssetManager.instance.selectedGame.get("codename").upper(),
                "asset": {
                    "assets": {
                        "full": asset
                    },
                    "codename": character["codename"],
                    "name": key,
                    "skin": "0"
                }
            })
    else:
        # No game selected — return empty template (background/foreground/icons only)
        return {
            "game": {"codename": "test", "name": "Test", "smashgg_id": 0},
            "tournamentInfo": {
                "tournamentName": QApplication.translate("app", "Sample Tournament"),
                "themeColor": "#2a2a2a",
            },
            "score": {
                "1": {
                    "best_of": 3, "match": "", "phase": "",
                    "team": {
                        "1": {"losers": False, "player": {"1": {"character": {}, "country": {}, "mergedName": "", "name": "", "state": {}, "team": ""}}, "score": 0, "teamName": ""},
                        "2": {"losers": False, "player": {"1": {"character": {}, "country": {}, "mergedName": "", "name": "", "state": {}, "team": ""}}, "score": 0, "teamName": ""}
                    }
                }
            }
        }

    data = {
        "game": {
            "codename": gameAssetManager.instance.selectedGame.get("codename", "test"),
            "name": gameAssetManager.instance.selectedGame.get("name", "Test"),
            "smashgg_id": 0
        },
        "tournamentInfo": {
            "tournamentName": QApplication.translate("app", "Sample Tournament"),
            "themeColor": "#2a2a2a",
        },
        "score": {
            "1": {
                "best_of": 3,
                "match": AutonaLocaleHelper.matchNames.get("winners_final"),
                "phase": AutonaLocaleHelper.phaseNames.get("group").format("A"),
                "team": {
                    "1": {
                        "losers": False,
                        "player": {
                            "1": {
                                "character": {
                                    "1": chars[0]["asset"],
                                    "2": chars[1]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[0]['team']} | {chars[0]['name']}",
                                "name": chars[0]["name"],
                                "state": {},
                                "team": chars[0]["team"]
                            }
                        },
                        "score": 0,
                        "teamName": QApplication.translate("app", "Team {0}").format("A")
                    },
                    "2": {
                        "losers": False,
                        "player": {
                            "1": {
                                "character": {
                                    "1": chars[2]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[2]['team']} | {chars[2]['name']}",
                                "name": chars[2]["name"],
                                "state": {},
                                "team": chars[2]["team"]
                            },
                            "2": {
                                "character": {
                                    "1": chars[3]["asset"]
                                },
                                "country": {},
                                "mergedName": f"{chars[3]['team']} | {chars[3]['name']}",
                                "name": chars[3]["name"],
                                "state": {},
                                "team": chars[3]["team"]
                            }
                        },
                        "score": 0,
                        "teamName": QApplication.translate("app", "Team {0}").format("B")
                    }
                }
            }
        }
    }
    return data


def remove_special_chars(input_str: str):
    invalid = '<>:"/\\|?* '
    for char in invalid:
        input_str = input_str.replace(char, "")
    return input_str


# Two templates: "Standard" (type_a) and "Center Panel" (type_b). Both share
# the Center Panel design as a starting point and diverge over time.
SUPPORTED_TEMPLATE_IDS = ("type_a", "type_b")
DEFAULT_TEMPLATE_ID = "type_a"

# Tournament banner image locations. The EVENT tab (AutonaTournamentInfoWidget)
# manages the banner file; the thumbnail consumes it when the user selects
# "Use tournament banner". Keep these in sync with that widget's constants.
BANNER_PATH = "./user_data/tournament_banner.png"
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"


def resolve_background_path(config):
    """Resolve the effective background image from the explicit source
    selector (Thumbnail Settings → Select Background Image) so the renderer
    template can keep reading a single ``background_path`` key.

    - ``banner``: resolved dynamically — the banner file may appear only after
      the user downloads it, so we re-check existence on every render.
    - ``custom``: the user's own image (``background_path``).
    - ``default`` / unset: None → the JS falls back to ``template.default_background``.
    """
    source = config.get("background_source")
    if source == "banner":
        return BANNER_PATH if os.path.exists(BANNER_PATH) else DEFAULT_BANNER_PATH
    if source == "custom":
        return config.get("background_path") or None
    if source == "default":
        return None
    # Legacy config (pre-selector): honour whatever background_path holds.
    return config.get("background_path") or None


def _resolve_template_id(settings):
    tid = (settings or {}).get("template_id", DEFAULT_TEMPLATE_ID)
    if tid not in SUPPORTED_TEMPLATE_IDS:
        return DEFAULT_TEMPLATE_ID
    return tid


def prepare_data(settingsManager, isPreview=False, gameAssetManager=None, scoreboardNumber=1):
    """Prepare data, config, and template for thumbnail rendering.

    Returns (data, config, template) tuple.
    Raises on error for non-preview mode.
    """
    settings = settingsManager.Get("thumbnail_config")
    if not settings:
        settings = {}

    # Load template JSON based on selected template id
    template_id = _resolve_template_id(settings)
    template_path = f"./assets/thumbnail_base/thumbnail_types/{template_id}.json"
    with open(template_path, 'rt') as f:
        template_data = json.load(f)
    # Stamp id so the renderer can switch HTML when needed
    template_data["template_id"] = template_id

    # Read from in-memory state first (always up-to-date),
    # fall back to file for non-preview (final) generation
    data = None
    try:
        # Use lock-acquired snapshot rather than touching StateManager.state
        # directly (Design.md §3.2.5). include_pending: the thumbnail must
        # render what the operator currently has in the form, even before
        # the 反映 button published it.
        state = StateManager.GetFullState(include_pending=True).get("state", {})
        if state and state.get("game", {}).get("codename"):
            has_players = True
            for i in [1, 2]:
                if 'name' not in state.get("score", {}).get(str(scoreboardNumber), {}).get("team", {}).get(str(i), {}).get("player", {}).get("1", {}):
                    has_players = False
                    break
            if has_players:
                data = state
    except Exception:
        pass

    if data is None:
        # Try file as fallback
        data_path = "./out/program_state.json"
        try:
            with open(data_path, 'rt', encoding='utf-8') as f:
                data = json.load(f)
            if not data.get("game", {}).get("codename"):
                raise Exception(QApplication.translate(
                    "thumb_app", "Please select a game first"))
            for i in [1, 2]:
                if 'name' not in data.get("score", {}).get(str(scoreboardNumber), {}).get("team", {}).get(str(i), {}).get("player", {}).get("1", {}):
                    raise Exception(QApplication.translate(
                        "thumb_app", "Player {0} tag missing").format(i))
        except Exception as e:
            if isPreview:
                # No live/complete program state yet (e.g. fresh startup before
                # any tournament data is loaded) is expected for previews — fall
                # back to placeholder data silently, matching the result
                # generator's `_create_preview_data` path. Not a warning.
                logger.debug(
                    f"No live program state; rendering placeholder preview: {e}")
                game_codename = "test"
                try:
                    game_codename = StateManager.GetFullState().get("state", {}).get("game", {}).get("codename", "test")
                except Exception:
                    pass
                data = createFalseData(gameAssetManager, deep_get(
                    settings, f"game.{game_codename}.asset_pack"))
            else:
                # Final generation with missing data is a genuine error.
                logger.warning(f"Failed to load program state: {e}")
                raise e

    config = dict(settings)
    config["scoreboardNumber"] = scoreboardNumber
    config["background_path"] = resolve_background_path(config)

    return data, config, template_data


def save_result(pixmap, data, settings, scoreboardNumber=1, isPreview=False):
    """Save rendered pixmap to disk. Returns output file path."""
    out_path = "./out/thumbnails" if not isPreview else AutonaResolve("tmp/thumbnail")
    Path(out_path).mkdir(parents=True, exist_ok=True)

    if not isPreview:
        # deep_get uses (dict, key) order; fallback to "" to avoid passing None to remove_special_chars
        tag_player1 = deep_get(data, f"score.{scoreboardNumber}.team.1.player.1.name", "") or ""
        tag_player2 = deep_get(data, f"score.{scoreboardNumber}.team.2.player.1.name", "") or ""
        thumbnail_filename = f"{remove_special_chars(tag_player1)}-vs-{remove_special_chars(tag_player2)}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d-%H-%M-%S')}"

        pixmap.save(f"{out_path}/{thumbnail_filename}.png")
        pixmap.save(f"{out_path}/{thumbnail_filename}.jpg")
        logger.info(
            f"Thumbnail successfully saved as {out_path}/{thumbnail_filename}.png and {out_path}/{thumbnail_filename}.jpg")
        return f"{out_path}/{thumbnail_filename}.png"
    else:
        thumbnail_filename = "template"
        pixmap.save(f"{out_path}/{thumbnail_filename}.png")
        logger.info(
            f"Thumbnail preview saved as {out_path}/{thumbnail_filename}.png")
        return f"{out_path}/{thumbnail_filename}.png"


def generate(settingsManager, isPreview=False, gameAssetManager=None, scoreboardNumber=1):
    """Generate a thumbnail synchronously (for final generation).

    For preview, use prepare_data() + ThumbnailRenderer.render() directly.
    """
    from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer

    data, config, template_data = prepare_data(
        settingsManager, isPreview, gameAssetManager, scoreboardNumber)

    renderer = ThumbnailRenderer.instance()
    pixmap = renderer.renderSync(
        data=data,
        config=config,
        template=template_data,
    )

    if pixmap.isNull():
        logger.error("Thumbnail rendering produced an empty image")
        if isPreview:
            return None
        raise Exception("Thumbnail rendering failed")

    settings = settingsManager.Get("thumbnail_config") or {}
    return save_result(pixmap, data, settings, scoreboardNumber, isPreview)
