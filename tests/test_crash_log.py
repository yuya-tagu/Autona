"""The native crash log (src/Helpers/AutonaCrashLog.py).

- A non-empty log the previous run left is kept as tsh-crash.prev.log (one
  generation); an empty one is not. A failed move does not stop the start.
- faulthandler stays enabled until the atexit hook, which disables it before
  closing the file and removes the file when nothing was written.
- The exit watchdog dumps every thread's stack when the exit hangs, and the
  hook cancels it.
- src/Autona.py's closeEvent no longer closes the log itself and arms the
  watchdog before quitting (AST: Autona.py is never imported by tests).
"""
import ast
import faulthandler
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.Helpers import AutonaCrashLog

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def registered(monkeypatch):
    """atexit.register recorded instead of run at the test process's exit;
    faulthandler handed back to pytest's stderr afterwards."""
    calls = []
    monkeypatch.setattr(AutonaCrashLog.atexit, "register",
                        lambda fn, *args: calls.append((fn, args)))
    opened = []
    yield calls, opened
    faulthandler.cancel_dump_traceback_later()
    faulthandler.disable()
    for f in opened:
        try:
            f.close()
        except Exception:
            pass
    faulthandler.enable(sys.__stderr__)


def _open(path, opened):
    f = AutonaCrashLog.open_crash_log(path)
    opened.append(f)
    return f


def test_a_dump_left_by_the_previous_run_is_kept(tmp_path, registered):
    _, opened = registered
    path = tmp_path / "logs" / "tsh-crash.log"
    path.parent.mkdir()
    path.write_text("Fatal Python error: Segmentation fault\n")

    _open(path, opened)

    previous = path.with_name(AutonaCrashLog.PREVIOUS_NAME)
    assert previous.read_text() == "Fatal Python error: Segmentation fault\n"
    assert path.exists() and path.stat().st_size == 0
    assert faulthandler.is_enabled()


def test_the_logs_folder_is_made(tmp_path, registered):
    _, opened = registered
    path = tmp_path / "a" / "logs" / "tsh-crash.log"

    _open(path, opened)

    assert path.exists()


def test_an_empty_log_is_not_kept(tmp_path, registered):
    _, opened = registered
    path = tmp_path / "tsh-crash.log"
    path.write_text("")

    _open(path, opened)

    assert not path.with_name(AutonaCrashLog.PREVIOUS_NAME).exists()


def test_an_older_kept_dump_is_replaced(tmp_path, registered):
    _, opened = registered
    path = tmp_path / "tsh-crash.log"
    previous = path.with_name(AutonaCrashLog.PREVIOUS_NAME)
    previous.write_text("older\n")
    path.write_text("newer\n")

    _open(path, opened)

    assert previous.read_text() == "newer\n"


def test_a_failed_move_does_not_stop_the_start(tmp_path, registered,
                                               monkeypatch):
    _, opened = registered
    path = tmp_path / "tsh-crash.log"
    path.write_text("dump\n")

    def _refuse(*_args):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(AutonaCrashLog.os, "replace", _refuse)

    f = _open(path, opened)

    assert not f.closed
    assert faulthandler.is_enabled()
    assert not path.with_name(AutonaCrashLog.PREVIOUS_NAME).exists()


def test_the_exit_hook_disables_and_removes_an_empty_log(tmp_path,
                                                         registered):
    calls, opened = registered
    path = tmp_path / "tsh-crash.log"

    f = _open(path, opened)

    assert calls == [(AutonaCrashLog.close_crash_log, (f, path))]
    fn, args = calls[0]
    fn(*args)
    assert not faulthandler.is_enabled()
    assert f.closed
    assert not path.exists()


def test_the_exit_hook_keeps_a_log_with_a_dump(tmp_path, registered):
    calls, opened = registered
    path = tmp_path / "tsh-crash.log"

    f = _open(path, opened)
    f.write("Thread 0x1 (most recent call first):\n")
    f.flush()
    fn, args = calls[0]
    fn(*args)

    assert not faulthandler.is_enabled()
    assert path.read_text().startswith("Thread")


def test_the_exit_hook_raises_nothing_when_already_closed(tmp_path,
                                                          registered):
    calls, opened = registered
    path = tmp_path / "tsh-crash.log"

    f = _open(path, opened)
    f.close()
    path.unlink()
    fn, args = calls[0]
    fn(*args)  # no exception


def test_a_hung_exit_writes_every_threads_stack(tmp_path, registered):
    calls, opened = registered
    path = tmp_path / "tsh-crash.log"

    f = _open(path, opened)
    AutonaCrashLog.arm_exit_watchdog(f, 0.1)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and path.stat().st_size == 0:
        time.sleep(0.05)

    text = path.read_text()
    assert "Thread" in text or "File" in text, text


def test_the_normal_exit_cancels_the_watchdog(tmp_path, registered):
    calls, opened = registered
    path = tmp_path / "tsh-crash.log"

    f = _open(path, opened)
    AutonaCrashLog.arm_exit_watchdog(f, 0.2)
    fn, args = calls[0]
    fn(*args)
    time.sleep(0.5)

    assert not path.exists()


def test_importing_the_helper_does_nothing(tmp_path):
    script = (
        "import faulthandler, os, sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "before = set(os.listdir('.'))\n"
        "import src.Helpers.AutonaCrashLog\n"
        "assert not faulthandler.is_enabled()\n"
        "assert set(os.listdir('.')) == before\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script],
                            cwd=tmp_path, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr


# -- the shells -----------------------------------------------------------

def _function(path, name):
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls_in_order(function):
    calls = [node for node in ast.walk(function)
             if isinstance(node, ast.Call)]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return [ast.unparse(node.func) for node in calls]


def test_the_window_close_leaves_the_log_to_the_exit_and_arms_the_watchdog():
    calls = _calls_in_order(_function("src/Autona.py", "closeEvent"))

    assert not any(call.startswith("crashlog.") for call in calls), calls
    assert "faulthandler.disable" not in calls
    arms = [i for i, call in enumerate(calls)
            if call == "AutonaCrashLog.arm_exit_watchdog"]
    assert len(arms) == 1, calls
    assert arms[0] < calls.index("app.quit")
    # The crash log itself: dump_traceback_later's default is sys.stderr.
    arm = next(node for node in ast.walk(_function("src/Autona.py",
                                                   "closeEvent"))
               if isinstance(node, ast.Call)
               and ast.unparse(node.func) == "AutonaCrashLog.arm_exit_watchdog")
    assert ast.unparse(arm.args[0]) == "crashlog"
    # After the teardown it watches over, not in the middle of it.
    assert calls.index("self.webserver.stop") < arms[0]
    assert calls.index("MatchLogService.instance.Shutdown") < arms[0]


def test_the_window_close_removes_tmp_after_its_writers_and_readers_stop():
    # A render job writes tmp/thumbnail/preview.png and
    # tmp/result/result_preview.png, and a request can be reading one: the
    # removal used to run before they stopped and hit WinError 32 or had
    # tmp/ recreated behind it.
    calls = _calls_in_order(_function("src/Autona.py", "closeEvent"))
    removals = [i for i, call in enumerate(calls) if call == "shutil.rmtree"]

    assert len(removals) == 1, calls
    assert calls.index("self.webserver.stop") < removals[0]
    assert calls.index("TaskExecutor.instance.shutdown") < removals[0]
    assert removals[0] < calls.index("QApplication.allWindows")


@pytest.mark.parametrize("path", ["src/Autona.py", "src/headless.py"])
def test_both_shells_open_the_log_through_the_helper(path):
    source = (PROJECT_ROOT / path).read_text(encoding="utf-8")

    assert "AutonaCrashLog.open_crash_log(" in source
    # Look at code, not comments: no faulthandler import or attribute use.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "faulthandler"
                       for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "faulthandler"
        elif isinstance(node, ast.Attribute):
            assert not (isinstance(node.value, ast.Name)
                        and node.value.id == "faulthandler")
    assert "'./logs/tsh-crash.log'" in source
