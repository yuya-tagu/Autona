"""Prepare data and generate result images (Top 8)."""

import datetime
import os
import random
import re
from pathlib import Path

from qtpy.QtWidgets import QApplication
from loguru import logger

from src.StateManager import StateManager
from src.TSHGameAssetManager import TSHGameAssetManager
from src.SettingsManager import SettingsManager
from src.Helpers.TSHDictHelper import deep_get
from src.Helpers.TSHLocaleHelper import TSHLocaleHelper
from src.Helpers.TSHDirHelper import TSHResolve


def _remove_special_chars(s):
    return re.sub(r'[\\/*?:"<>|]', "", s)


def prepare_data(settingsManager, isPreview=False, gameAssetManager=None):
    """Prepare result data from StateManager or generate preview mock data.

    Returns (data, config) tuple.
    data = {
        "tournamentName": str,
        "eventName": str,
        "players": [
            { "rank": int, "name": str, "sponsor": str,
              "character": { "assets": {...}, "codename": str, ... } },
            ...
        ]
    }
    config = {
        "main_icon_path": str,
        "background_path": str,
        "accent_color": str,
    }
    """
    settings = settingsManager.Get("result_config") or {}
    config = dict(settings)
    config["main_icon_path"] = settings.get(
        "main_icon_path", "./layout/logo.png")
    config["background_path"] = settings.get(
        "background_path",
        settingsManager.Get("thumbnail_config.background_path",
                            "./assets/thumbnail_base/default_banner.png"))
    config["accent_color"] = settingsManager.Get(
        "general.accent_color", "#f0a500")

    # Build player list from result state
    data = {
        "tournamentName": "",
        "eventName": "",
        "startAt": None,
        "numEntrants": 0,
        "players": [],
    }

    try:
        state = StateManager.state
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

    for i in range(1, 9):
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
        })

    if isPreview and not has_data:
        data = _create_preview_data(gameAssetManager)

    return data, config


def _create_preview_data(gameAssetManager=None):
    """Generate mock top-8 data for preview."""
    data = {
        "tournamentName": QApplication.translate("app", "Sample Tournament"),
        "eventName": "Top 8",
        "players": [],
    }

    chars = []
    if gameAssetManager and len(gameAssetManager.instance.characters.keys()) > 0:
        keys = list(gameAssetManager.instance.characters.keys())
        for i in range(8):
            key = keys[random.randint(0, len(keys) - 1)]
            character = gameAssetManager.instance.characters[key]
            skin = random.randint(
                0, len(gameAssetManager.instance.characters[key]))
            assets = gameAssetManager.instance.GetCharacterAssets(
                character.get("codename"), skin)

            asset = assets.get("full") or assets.get("portrait")

            name = key
            if character.get("locale"):
                locale = TSHLocaleHelper.programLocale
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

    for i in range(8):
        entry = {
            "rank": i + 1,
            "name": chars[i]["name"] if i < len(chars) else f"Player {i+1}",
            "sponsor": chars[i]["sponsor"] if i < len(chars) else "",
            "character": chars[i]["character"] if i < len(chars) else {},
        }
        data["players"].append(entry)

    return data


def save_result(pixmap, data):
    """Save rendered pixmap to out/results/. Returns output file path."""
    out_path = "./out/results"
    Path(out_path).mkdir(parents=True, exist_ok=True)

    tournament = _remove_special_chars(data.get("tournamentName", "result"))
    timestamp = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
    filename = f"{tournament}-top8-{timestamp}"

    pixmap.save(f"{out_path}/{filename}.png")
    pixmap.save(f"{out_path}/{filename}.jpg")
    logger.info(f"Result image saved: {out_path}/{filename}.png")
    return f"{out_path}/{filename}.png"


def generate(settingsManager, isPreview=False, gameAssetManager=None,
             layout=None):
    """Generate result image synchronously. Returns file path."""
    from src.result.ResultRenderer import ResultRenderer

    data, config = prepare_data(settingsManager, isPreview, gameAssetManager)

    renderer = ResultRenderer.instance()
    pixmap = renderer.renderSync(data=data, config=config, layout=layout)

    if pixmap.isNull():
        logger.error("Result rendering produced an empty image")
        if isPreview:
            return None
        raise Exception("Result rendering failed")

    if isPreview:
        out_path = TSHResolve("tmp/result")
        Path(out_path).mkdir(parents=True, exist_ok=True)
        tmp_file = f"{out_path}/preview.png"
        pixmap.save(tmp_file)
        return tmp_file

    return save_result(pixmap, data)
