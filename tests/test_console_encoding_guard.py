"""Regression guard for the loguru console-sink UnicodeEncodeError.

On Windows, a plain `python main.py` launch inherits a console codepage that
defaults to a legacy ANSI encoding (e.g. cp932 on a Japanese Windows), not
UTF-8. src/Autona.py wires loguru's dev-mode sink straight to sys.stdout, so
any log message containing a character outside that codepage (e.g.
"Pokémon Trainer") raised UnicodeEncodeError from inside loguru's handler and
spammed "Logging error in Loguru Handler" for the rest of the run.

src/Autona.py must not be imported here (see .claude/rules/agent-pitfalls.md
#1): importing it assembles and boots the whole application. This test
therefore covers the fix two ways instead:
  - a static check that the sys.stdout.reconfigure guard is still present in
    the sink-construction block, so a future edit can't silently drop it
  - a behavioural check, run in a subprocess with no src import at all, that
    the exact same guard logic neutralises a cp932-only stdout
"""
import io
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTONA_PY = PROJECT_ROOT / "src" / "Autona.py"


def _sink_block():
    text = AUTONA_PY.read_text(encoding="utf-8")
    start = text.index("if sys.stdout != None:")
    end = text.index("logger.configure(**config)")
    return text[start:end]


def test_console_sink_reconfigures_stdout_to_utf8_with_safe_fallback():
    block = _sink_block()
    assert "hasattr(sys.stdout, \"reconfigure\")" in block, \
        "the reconfigure guard was removed from the console-sink block"
    assert 'sys.stdout.reconfigure(encoding="utf-8"' in block
    assert 'errors="backslashreplace"' in block, \
        "the reconfigure call must tolerate un-encodable characters instead " \
        "of raising"


# Same guard logic as src/Autona.py, run against a stdout locked to cp932
# (the legacy Windows console codepage that triggered the original bug) to
# prove it stops UnicodeEncodeError without importing src.Autona.
_CHECK_SCRIPT = r'''
import io
import sys
from loguru import logger

sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")

if sys.stdout != None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    logger.configure(handlers=[
        {"sink": sys.stdout, "format": "{level} | {message}", "level": "DEBUG"},
    ])

logger.info("Character Pokémon Trainer has 8 skins")
print("OK", file=sys.stderr)
'''


def test_reconfigure_guard_prevents_unicode_encode_error_on_cp932_stdout():
    result = subprocess.run(
        [sys.executable, "-B", "-c", _CHECK_SCRIPT],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"guard script crashed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")
    assert "Logging error" not in result.stderr, (
        "loguru still raised inside the handler on a cp932 stdout:\n"
        f"{result.stderr}")
    assert "OK" in result.stderr
