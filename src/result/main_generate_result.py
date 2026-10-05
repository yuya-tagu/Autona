"""Prepare data and generate result images (Top 8 / Top 16)."""

import datetime
import os
import random
import re
from pathlib import Path

from qtpy.QtWidgets import QApplication
from loguru import logger

from src.StateManager import StateManager
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.SettingsManager import SettingsManager
from src.AutonaFeatureColors import FEATURE_RESULT, GetFeatureColors
from src.Helpers.AutonaDictHelper import deep_get
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.Helpers.AutonaDirHelper import AutonaResolve
from src.Helpers.AutonaOutputRetention import PruneOutputFiles


def _remove_special_chars(s):
    return re.sub(r'[\\/*?:"<>|]', "", s)


def _max_players_for_layout(layout):
    """Return the player count for a given layout key."""
    return 16 if layout == "horizontal_16" else 8


def prepare_data(settingsManager, isPreview=False, gameAssetManager=None,
                 max_players=8, layout=None):
    """Prepare result data from StateManager or generate preview mock data.

    Returns (data, config) tuple.
    data = {
        "tournamentName": str,
        "eventName": str,
        "players": [
            { "rank": int, "name": str, "sponsor": str,
              "character": { "assets": {...}, "codename": str, ... },
              # Team layout only: each member's main fighter + name.
              "members": [ { "name": str, "character": {...} }, ... ] },
            ...
        ]
    }
    config = {
        "main_icon_path": str,
        "background_path": str,
        "theme_color": str,
        "accent_color": str,
    }
    """
    settings = settingsManager.Get("result_config") or {}
    config = dict(settings)
    config["main_icon_path"] = settings.get(
        "main_icon_path", "./layout/logo.png")
    # Inherit the thumbnail's effective background when the result has none of
    # its own. thumbnail_config.background_path is now the custom-image slot
    # only, so resolve the real image via the thumbnail's source selector.
    from src.thumbnail import main_generate_thumbnail as _thumb
    thumb_bg = _thumb.resolve_background_path(
        settingsManager.Get("thumbnail_config") or {})
    config["background_path"] = settings.get(
        "background_path", thumb_bg or _thumb.DEFAULT_BANNER_PATH)
    # Result-scoped colors (colors.result.*), falling back to the tournament
    # colors and then to the Autona defaults. The layouts read theme_color for
    # their surfaces and accent_color for the highlights.
    theme_color, accent_color = GetFeatureColors(FEATURE_RESULT)
    config["theme_color"] = theme_color
    config["accent_color"] = accent_color

    # Build player list from result state
    data = {
        "tournamentName": "",
        "eventName": "",
        "startAt": None,
        "numEntrants": 0,
        "players": [],
    }

    try:
        # Use lock-acquired snapshot rather than touching StateManager.state
        # directly (Design.md §3.2.5).
        state = StateManager.GetFullState().get("state", {})
        if state:
            data["tournamentName"] = deep_get(
                state, "tournamentInfo.tournamentName", "") or ""
            data["eventName"] = deep_get(
                state, "tournamentInfo.eventName", "") or ""
            data["startAt"] = deep_get(
                state, "tournamentInfo.startAt", None)
            data["numEntrants"] = deep_get(
                state, "tournamentInfo.numEntrants", 0) or 0
    except Exception:
        pass

    # Read result_players from state (set by the widget)
    result_players = StateManager.Get("result_players") or {}
    has_data = False

    for i in range(1, max_players + 1):
        key = str(i)
        p = result_players.get(key, {})
        name = p.get("name", "")
        if name:
            has_data = True
        char_data = p.get("character", {})
        data["players"].append({
            "rank": p.get("rank", i),
            "name": name,
            "sponsor": p.get("sponsor", ""),
            "character": char_data,
            "characters": p.get("characters", [char_data] if char_data else []),
            # Team layout: roster of {name, character} per rank.
            "members": p.get("members", []),
        })

    if isPreview and not has_data:
        data = _create_preview_data(gameAssetManager, max_players=max_players,
                                    layout=layout)

    return data, config


def _create_preview_data(gameAssetManager=None, max_players=8, layout=None):
    """Generate mock preview data for a given player count."""
    # Dispatch any team-style layout (team, team_split, ...) to the team mock
    # so the preview shows rosters instead of a single-character fallback.
    if layout and layout.startswith("team"):
        return _create_team_preview_data(gameAssetManager, max_players)

    data = {
        "tournamentName": QApplication.translate("app", "Sample Tournament"),
        "eventName": f"Top {max_players}",
        "players": [],
    }

    chars = []
    if gameAssetManager and len(gameAssetManager.instance.characters.keys()) > 0:
        keys = list(gameAssetManager.instance.characters.keys())
        for i in range(max_players):
            key = keys[random.randint(0, len(keys) - 1)]
            character = gameAssetManager.instance.characters[key]
            skin = random.randint(
                0, len(gameAssetManager.instance.characters[key]))
            assets = gameAssetManager.instance.GetCharacterAssets(
                character.get("codename"), skin)

            asset = assets.get("full") or assets.get("portrait")

            name = key
            if character.get("locale"):
                locale = AutonaLocaleHelper.programLocale
                if locale.replace("-", "_") in character["locale"]:
                    name = character["locale"][locale.replace("-", "_")]
                elif re.split("-|_", locale)[0] in character["locale"]:
                    name = character["locale"][re.split("-|_", locale)[0]]

            chars.append({
                "name": name,
                "sponsor": gameAssetManager.instance.selectedGame.get(
                    "codename", "").upper(),
                "character": {
                    "assets": {"full": asset} if asset else {},
                    "codename": character["codename"],
                    "name": key,
                },
            })

    for i in range(max_players):
        entry = {
            "rank": i + 1,
            "name": chars[i]["name"] if i < len(chars) else f"Player {i+1}",
            "sponsor": chars[i]["sponsor"] if i < len(chars) else "",
            "character": chars[i]["character"] if i < len(chars) else {},
        }
        data["players"].append(entry)

    return data


def _create_team_preview_data(gameAssetManager=None, max_players=8):
    """Mock preview data for the team layout: each rank is a team whose
    members (2-5) each show a single main fighter."""
    data = {
        "tournamentName": QApplication.translate("app", "Sample Tournament"),
        "eventName": f"Top {max_players}",
        "players": [],
    }

    # Build a pool of characters (full-body asset only) to draw members from.
    char_pool = []
    if gameAssetManager and len(gameAssetManager.instance.characters.keys()) > 0:
        for key in gameAssetManager.instance.characters.keys():
            character = gameAssetManager.instance.characters[key]
            assets = gameAssetManager.instance.GetCharacterAssets(
                character.get("codename"), 0)
            asset = assets.get("full") or assets.get("portrait")
            if asset:
                char_pool.append({
                    "assets": {"full": asset},
                    "codename": character.get("codename"),
                    "name": key,
                })

    for i in range(max_players):
        # Vary 2-5 members per team so the layout adjustment is visible.
        member_count = 2 + (i % 4)
        members = []
        for j in range(member_count):
            char = {}
            if char_pool:
                char = char_pool[random.randint(0, len(char_pool) - 1)]
            members.append({"name": f"P{j + 1}", "character": char})
        data["players"].append({
            "rank": i + 1,
            "name": f"Team {i + 1}",
            "sponsor": "",
            "members": members,
        })

    return data


def save_result(pixmap, data):
    """Save rendered pixmap to out/results/. Returns output file path."""
    out_path = "./out/results"
    Path(out_path).mkdir(parents=True, exist_ok=True)

    tournament = _remove_special_chars(data.get("tournamentName", "result"))
    timestamp = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
    num_players = len(data.get("players", [])) or 8
    filename = f"{tournament}-top{num_players}-{timestamp}"

    # PNG only: the .jpg twin the upstream fork also wrote was consumed by
    # nothing (unlike the thumbnail's, which the /thumbnail API serves) and
    # made every save look duplicated in out/results/.
    pixmap.save(f"{out_path}/{filename}.png")
    logger.info(f"Result image saved: {out_path}/{filename}.png")
    # Keep disk usage bounded (also sweeps legacy .jpg twins)
    PruneOutputFiles(out_path, ("*.png", "*.jpg"), 30,
                     protect=f"{out_path}/{filename}.png")
    return f"{out_path}/{filename}.png"


def generate(settingsManager, isPreview=False, gameAssetManager=None,
             layout=None):
    """Generate result image synchronously. Returns file path."""
    from src.result.ResultRenderer import ResultRenderer

    max_players = _max_players_for_layout(layout)
    data, config = prepare_data(settingsManager, isPreview, gameAssetManager,
                                max_players=max_players, layout=layout)

    renderer = ResultRenderer.instance()
    pixmap = renderer.renderSync(data=data, config=config, layout=layout)

    if pixmap.isNull():
        logger.error("Result rendering produced an empty image")
        if isPreview:
            return None
        raise Exception("Result rendering failed")

    if isPreview:
        out_path = AutonaResolve("tmp/result")
        Path(out_path).mkdir(parents=True, exist_ok=True)
        tmp_file = f"{out_path}/preview.png"
        pixmap.save(tmp_file)
        return tmp_file

    return save_result(pixmap, data)
