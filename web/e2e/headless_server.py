"""Run the headless Autona for the Playwright E2E (ADR-005 S3d).

Started by web/e2e/global-setup.ts; not a test module (pytest collects only
tests/test_*.py). The process is started exactly as the headless smoke test
starts it -- tests/test_headless_smoke.py's _launch() and
_wait_until_serving(), in tests/test_bootstrap_split.py's throwaway working
directory, on a free port, with its own AUTONA_INSTANCE_TAG -- so the
developer's user_data/, out/, logs/ and running Autona are never touched.

On top of that working directory the E2E needs what the browser loads: the
built Web UI (web/build) and one overlay (layout/OVERLAY with the shared
layout/include and the two layout-level files it reads). They are linked or
copied in read-only; nothing in the headless shell writes to them.

Protocol with the caller, one line each on stdout:

    AUTONA_E2E_READY {"url": ..., "pid": ..., "dir": ...}   serving
    AUTONA_E2E_FAILED <reason>                               did not start
    AUTONA_E2E_STOPPED {"exit": ..., "killed": ...}          stopped

After READY it waits for "stop" or the end of stdin -- so a caller that dies
without saying stop still stops the server -- then stops the headless process
with Ctrl+Break (the clean stop, as an operator's), by its PID only, and
removes the working directory. The helper exits 0 only for a clean stop
(exit code 0, not killed); the caller fails the run otherwise.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import _LINKED_TREES, _link_tree  # noqa: E402
from tests.test_bootstrap_split import _free_port, _runtime  # noqa: E402
from tests.test_headless_smoke import (  # noqa: E402
    _launch, _wait_until_serving)

# The overlay the E2E opens: the plain 1v1 scoreboard.
OVERLAY = "scoreboard_simple"
# Linked (directory junctions), on top of _runtime()'s own links.
EXTRA_LINKS = ["web/build", "layout/include", f"layout/{OVERLAY}"]
# Copied: files cannot be junctioned. The overlays read both.
EXTRA_FILES = ["layout/main.css", "layout/settings.json"]


def _say(kind, payload=""):
    print(f"AUTONA_E2E_{kind} {payload}".rstrip(), flush=True)


def _unlink(link):
    try:
        if link.is_symlink():
            os.unlink(link)
        else:
            os.rmdir(link)
    except OSError:
        pass


def _kill(process):
    # By its PID only (TerminateProcess on this one process).
    process.kill()
    process.wait(timeout=30)
    return process.returncode, True


def _stop(process):
    """Stop the headless process with Ctrl+Break; kill it only if that does
    not work. Returns (exit code, whether it had to be killed)."""
    if process.poll() is not None:
        return process.returncode, False
    try:
        os.kill(process.pid, signal.CTRL_BREAK_EVENT)
    except OSError:
        # It can end between poll() and the signal, leaving no process
        # group to signal: that is a stop like any other.
        if process.poll() is not None:
            return process.returncode, False
        print("could not send Ctrl+Break to the headless process; killing it",
              file=sys.stderr, flush=True)
        return _kill(process)
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        return _kill(process)
    return process.returncode, False


def _remove(directory, links):
    # Never through a link that is still there: rmtree would descend into
    # the real web/build, layout/ or assets/ behind it.
    remaining = [link for link in links if os.path.lexists(link)]
    if remaining:
        print(f"leaving {directory}: links still present: {remaining}",
              file=sys.stderr, flush=True)
        return
    shutil.rmtree(directory, ignore_errors=True)


def serve(console_copy=None):
    if not (PROJECT_ROOT / "web" / "build" / "index.html").is_file():
        _say("FAILED", "web/build/index.html is missing: run `npm run build`"
                       " in web/ first")
        return 2
    directory = Path(tempfile.mkdtemp(prefix="autona-e2e-"))
    console_path = directory / "console.txt"
    links = [directory / relative for relative in _LINKED_TREES + EXTRA_LINKS]
    exit_code, killed = 1, False
    process = None
    try:
        with _runtime(directory) as cwd:
            try:
                for relative in EXTRA_LINKS:
                    link = cwd / relative
                    link.parent.mkdir(parents=True, exist_ok=True)
                    _link_tree(link, PROJECT_ROOT / relative)
                for relative in EXTRA_FILES:
                    shutil.copyfile(PROJECT_ROOT / relative, cwd / relative)
                port = _free_port()
                (cwd / "user_data" / "settings.json").write_text(json.dumps({
                    "general": {"webserver_port": port,
                                "webserver_listen_lan": False}}),
                    encoding="utf-8")
                with open(console_path, "w", encoding="utf-8") as console:
                    process = _launch(cwd, f"e2e-{uuid.uuid4().hex}", console)
                    try:
                        if not _wait_until_serving(process, port):
                            _stop(process)
                            text = console_path.read_text(
                                encoding="utf-8", errors="replace")
                            _say("FAILED", json.dumps({
                                "exit": process.returncode,
                                "console": text[-4000:]}))
                            return 1
                        _say("READY", json.dumps({
                            "url": f"http://127.0.0.1:{port}",
                            "pid": process.pid, "dir": str(cwd)}))
                        for line in sys.stdin:
                            if line.strip() == "stop":
                                break
                    finally:
                        exit_code, killed = _stop(process)
                _say("STOPPED", json.dumps(
                    {"exit": exit_code, "killed": killed}))
            finally:
                if console_copy and console_path.exists():
                    try:
                        Path(console_copy).parent.mkdir(parents=True,
                                                        exist_ok=True)
                        shutil.copyfile(console_path, console_copy)
                    except OSError:
                        pass
                for relative in reversed(EXTRA_LINKS):
                    _unlink(cwd / relative)
    finally:
        _remove(directory, links)
    return 0 if exit_code == 0 and not killed else 1


def main():
    if sys.platform != "win32":
        _say("FAILED", "the headless E2E stops the server with Ctrl+Break"
                       " (Windows consoles only)")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--console-copy",
                        help="copy the headless console log here at the end")
    args = parser.parse_args()
    return serve(args.console_copy)


if __name__ == "__main__":
    sys.exit(main())
