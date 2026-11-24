"""The off-screen renderer draws the page under the desktop app's startup, and
the single colour is Qt's offscreen platform, not the renderer.

A launched desktop app gave every thumbnail as one colour, 17,19,23 (the
theme's bg_primary), with HEAD and with ADR-007 T2 alike, while the log said
the render completed. The cause was where it was launched from: a pytest
process, whose QT_QPA_PLATFORM=offscreen (tests/conftest.py) the child
inherited. On that platform no web page is composited into a widget, so a
grab() is the host widget's background, which qdarktheme paints bg_primary.
On the platform the app runs on (windows) the same startup draws the page.

Each case draws in a process of its own (tests/fixtures/thumbnail/
draw_on_platform.py): this process is on the offscreen platform.

The shipped thumbnails moved to template contract 2 (ADR-008 D7 R2): the
same drawing, now in Autona.onRender. draw_contracts.py draws the shipped
type_a and the same page on contract 1, on the app's platform, and the two
pictures are the same.

A thumbnail made from the layout kit (ADR-008 D7 R3), as it is, draws on
contract 2 without an error: draw_kit.py draws the kit's sample payload, the
teams sample and an empty state.

The shipped results moved to contract 2 in R4: tests/fixtures/result/
draw_contracts.py draws horizontal, team and vertical_geki and each on
contract 1 (a user's contract-1 page still takes the shared include/
result.js's initResult), and each pair of pictures is the same; a result
layout made from the kit, as it is, draws without an error: draw_kit.py draws
the kit's players' and teams' sample payloads and sixteen empty rows.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPT = PROJECT_ROOT / "tests" / "fixtures" / "thumbnail" / "draw_on_platform.py"
CONTRACTS = PROJECT_ROOT / "tests" / "fixtures" / "thumbnail" \
    / "draw_contracts.py"
KIT = PROJECT_ROOT / "tests" / "fixtures" / "thumbnail" / "draw_kit.py"
RESULT_CONTRACTS = PROJECT_ROOT / "tests" / "fixtures" / "result" \
    / "draw_contracts.py"
RESULT_KIT = PROJECT_ROOT / "tests" / "fixtures" / "result" / "draw_kit.py"
BG_PRIMARY = "#111317"

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the app's platform is windows")


def _draw(tmp_path, platform, script=SCRIPT):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    if platform is not None:
        env["QT_QPA_PLATFORM"] = platform
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-B", str(script), str(PROJECT_ROOT)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    lines = [line for line in done.stdout.splitlines()
             if line.startswith("DRAWN ")]
    assert lines, done.stdout[-3000:] + done.stderr[-3000:]
    return json.loads(lines[-1][len("DRAWN "):]), done.stdout + done.stderr


def test_the_desktop_startup_draws_the_page(tmp_path):
    drawn, _output = _draw(tmp_path, None)
    assert drawn["error"] == ""
    # The banner, the logo, the names: many colours, not the window's one.
    assert drawn["colours"] > 10, drawn
    assert drawn["centre"] != BG_PRIMARY


def test_on_the_offscreen_platform_it_says_so(tmp_path):
    drawn, output = _draw(tmp_path, "offscreen")
    assert drawn["error"] == ""
    # Whether the page is composited there depends on the machine (a desktop
    # GPU: the window's background only; a GPU-less CI runner: the page), so
    # only the warning is pinned.
    assert "on Qt's offscreen platform the pictures may be the window's " \
        "background" in output


def test_contract_2_draws_the_shipped_page_as_contract_1_did(tmp_path):
    drawn, _output = _draw(tmp_path, None, CONTRACTS)
    assert drawn["errors"] == ["", ""], drawn
    assert all(count > 10 for count in drawn["colours"]), drawn
    assert drawn["sampled"] > 0, drawn
    # The same pixels: the drawing is the template's, whoever calls it.
    assert drawn["differing"] <= drawn["sampled"] // 1000, drawn


def test_a_thumbnail_made_from_the_kit_draws_as_it_is(tmp_path):
    drawn, output = _draw(tmp_path, None, KIT)
    # No error with a match, a team match and no match at all.
    assert drawn["errors"] == ["", "", ""], (drawn, output[-3000:])
    # The background, the characters, the names: many colours.
    assert drawn["colours"][0] > 10, drawn
    assert drawn["colours"][1] > 10, drawn
    # The empty state's picture is not the match's (the names and the
    # characters are left out).
    assert drawn["differing"] > 0, drawn


def test_contract_2_draws_the_shipped_results_as_contract_1_did(tmp_path):
    drawn, output = _draw(tmp_path, None, RESULT_CONTRACTS)
    # The six shipped layouts, each beside the code it had before (frozen
    # copies of then, on contract 1).
    assert drawn["errors"] == [""] * 12, (drawn, output[-3000:])
    assert all(count > 10 for count in drawn["colours"]), drawn
    assert len(drawn["sampled"]) == 6, drawn
    assert all(sampled > 0 for sampled in drawn["sampled"]), drawn
    # The same pixels: moving to contract 2 changed nothing drawn.
    for sampled, differing in zip(drawn["sampled"], drawn["differing"]):
        assert 0 <= differing <= sampled // 1000, drawn


def test_a_result_made_from_the_kit_draws_as_it_is(tmp_path):
    drawn, output = _draw(tmp_path, None, RESULT_KIT)
    # No error with sixteen players, eight teams and nothing typed at all.
    assert drawn["errors"] == ["", "", ""], (drawn, output[-3000:])
    # The background, the characters, the names: many colours.
    assert drawn["colours"][0] > 10, drawn
    assert drawn["colours"][1] > 10, drawn
    # The empty rows' picture is not the players' (the cards are left out).
    assert drawn["differing"] > 0, drawn
