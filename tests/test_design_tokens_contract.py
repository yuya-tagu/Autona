"""Contract test: src/ui_constants.py <-> assets/design-tokens.json.

design-tokens.json is the machine-readable source of truth shared with the
web UI (web/src/themes.js has the mirror contract test in themes.test.js).
If this fails, one side changed without the other: update
assets/design-tokens.json first, then both consumers.

Runtime-free: ui_constants.py is imported directly by file path so the src
package chain (QApplication + singletons) is never triggered
(.claude/rules/agent-pitfalls.md #1).
"""
import json
from pathlib import Path

from tests.conftest import direct_import

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ui = direct_import("ui_constants", "src/ui_constants.py")

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
