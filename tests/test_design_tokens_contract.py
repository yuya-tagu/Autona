"""Contract test: src/ui_constants.py <-> assets/design-tokens.json.

design-tokens.json is the machine-readable source of truth shared with the
web UI (web/src/themes.js has the mirror contract test in themes.test.js).
If this fails, one side changed without the other: update
assets/design-tokens.json first, then both consumers.

Runtime-free: ui_constants.py holds plain constants, and no src module builds
anything at import time, so a normal import is enough.
"""
import json
from pathlib import Path

from src import ui_constants as ui

PROJECT_ROOT = Path(__file__).resolve().parent.parent

with open(PROJECT_ROOT / "assets" / "design-tokens.json", encoding="utf-8") as f:
    TOKENS = json.load(f)


def test_dark_palette_matches_tokens():
    assert ui.DARK == TOKENS["palette"]["dark"]


def test_radius_matches_tokens():
    assert TOKENS["radius"] == {
        "sm": ui.RADIUS_SM,
        "md": ui.RADIUS_MD,
        "lg": ui.RADIUS_LG,
    }


def test_spacing_matches_tokens():
    assert TOKENS["spacing"] == {
        "xs": ui.SPACE_XS,
        "sm": ui.SPACE_SM,
        "md": ui.SPACE_MD,
        "lg": ui.SPACE_LG,
        "xl": ui.SPACE_XL,
    }


def test_font_families_match_tokens():
    assert TOKENS["font"] == {
        "family": ui.FONT_FAMILY,
        "family_condensed": ui.FONT_FAMILY_CONDENSED,
        "family_mono": ui.FONT_FAMILY_MONO,
    }
