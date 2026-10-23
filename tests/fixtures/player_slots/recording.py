"""What the player slot recording (golden.json next to this file) is made of.

Two tests read the recording (ADR-005 S1d): tests/test_player_slot_publish.py
records and replays it through the player card, tests/test_player_service.py
replays it through PlayerService alone. Both have to build the same world --
the same fixture games in the same scenarios, the same custom flags, the
same locale -- and compare in the same encoding, so those are defined here,
once. Qt-free on purpose: the service test must not import Qt.
"""
import hashlib
import json
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
GOLDEN_PATH = HERE / "golden.json"
GAME_FIXTURE = HERE.parent / "game_assets"
GAMES = [p.name for p in (GAME_FIXTURE / "games").iterdir() if p.is_dir()]

# The game asset recording's locale setup ({program, export, remapping});
# the player slots were recorded under it too, countries included.
LOCALE = json.loads(
    (GAME_FIXTURE / "golden.json").read_text(encoding="utf-8"))["locale"]

# Custom flags exercise the second half of the country combo: a separator
# row (no data) and rows whose data is not a standard country's.
CUSTOM_FLAGS = ["Agender.png", "Custom Flag.png", "zzz.jpg"]

GAME_SCENARIOS = {
    # label: (codename, modsActive, reload)
    "tgame": ("tgame", False, False),
    "tgame_mods": ("tgame", True, True),
    "game2": ("game2", False, False),
}


def loadGolden():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def encode(value):
    """What program_state.json holds for `value` (StateManager._WriteExports)."""
    return orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")


def digest(value):
    """How large tables are stored: the first 16 hex digits of the sha256 of
    json.dumps(value)."""
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:16]


def remapFor(table):
    """AutonaLocaleHelper.GetRemaps over LOCALE["remapping"], for a catalog
    built without the Qt-side locale helper."""
    def remap(language):
        for name, languages in table.items():
            if language.replace('-', '_') in languages:
                return name
        return None
    return remap
