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
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPT = PROJECT_ROOT / "tests" / "fixtures" / "thumbnail" / "draw_on_platform.py"
BG_PRIMARY = "#111317"

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the app's platform is windows")


def _draw(tmp_path, platform):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    if platform is not None:
        env["QT_QPA_PLATFORM"] = platform
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), str(PROJECT_ROOT)],
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
