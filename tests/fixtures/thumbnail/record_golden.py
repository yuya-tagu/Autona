"""Record golden.json (next to this file) from the PRE-S4o implementation.

ADR-005 S4o moved the thumbnail's payload (prepare_data, createFalseData) and
the video title (_BuildVideoTitle of the Qt panel) into the Qt-free
src/core/ThumbnailService.py. tests/test_thumbnail_service.py replays
golden.json against the service: the same world must give the same payload,
and the same title. This is the script that recorded it.

It drives the OLD functions, so it cannot run against a tree where they
forward to the service. Run it against a copy of the last commit before S4o
(f2cda760):

    mkdir ../autona-pre-s4o
    git archive f2cda760 src | tar -x -C ../autona-pre-s4o
    python tests/fixtures/thumbnail/record_golden.py ../autona-pre-s4o \\
        tests/fixtures/thumbnail/golden.json

Arguments: <old checkout> <output json>. Each scenario (scenarios.py) runs in
a throwaway working directory holding only its files.
"""
import copy
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios as world  # noqa: E402


class _FakeManager:
    """The asset adapter, as the old functions read it."""

    def __init__(self, games):
        self.instance = self
        self.characters = games["characters"]
        self.selectedGame = games["game"]
        self._assets = games["assets"]

    def GetCharacterAssets(self, codename, skin):
        return world.CharacterAssets(self._assets, codename, skin)


def main(old_checkout, output):
    old_checkout = Path(old_checkout).resolve()
    output = Path(output).resolve()
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(old_checkout))

    from loguru import logger
    logger.remove()

    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from src.thumbnail import main_generate_thumbnail as thumbnail
    from src.AutonaThumbnailSettingsWidget import (
        AutonaThumbnailSettingsWidget as Panel)
    assert Path(sys.modules["src"].__file__).resolve().is_relative_to(
        old_checkout), "imported the current tree, not the old checkout"

    def enter(scenario):
        StateManager.state = copy.deepcopy(scenario["state"])
        SettingsManager.settings = copy.deepcopy(scenario.get("settings", {}))
        locale = scenario.get("locale", world.LOCALE)
        AutonaLocaleHelper.programLocale = locale["programLocale"]
        AutonaLocaleHelper.matchNames = locale["matchNames"]
        AutonaLocaleHelper.phaseNames = locale["phaseNames"]
        random.seed(world.SEED)

    payloads = {}
    for scenario in world.SCENARIOS:
        work = Path(tempfile.mkdtemp(prefix="autona-thumbnail-golden-"))
        try:
            world.Install(work, scenario)
            os.chdir(work)
            enter(scenario)
            games = scenario["games"]
            try:
                data, config, template = thumbnail.prepare_data(
                    SettingsManager, isPreview=scenario["preview"],
                    gameAssetManager=_FakeManager(games) if games else None,
                    scoreboardNumber=scenario["board"])
                payloads[scenario["name"]] = json.loads(json.dumps(
                    {"data": data, "config": config, "template": template}))
            except Exception as error:
                payloads[scenario["name"]] = {"raises": str(error)}
        finally:
            os.chdir(PROJECT_ROOT)
            shutil.rmtree(work, ignore_errors=True)

    titles = {}
    for scenario in world.TITLE_SCENARIOS:
        enter(scenario)
        titles[scenario["name"]] = Panel._BuildVideoTitle(
            Panel, scenario["board"])

    StateManager.state = {}
    SettingsManager.settings = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"payloads": payloads, "titles": titles},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
