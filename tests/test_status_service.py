"""What the app is busy with (ADR-005 S-final SF-2).

Until SF-2 the desktop footer had its own Qt-side source,
``Helpers/AutonaStatusManager``: a stack of ready-made Japanese messages
behind a module-level QObject signal. Now it is core's Qt-free
``StatusService``, which the web reads too (GET /api/v1/status and the
Socket.IO ``status`` event), and a job's words are a code and its args that
each reader translates. Pinned here:

- the service: push / update / pop, the last job is the current one, a
  revision that grows on every change, copies to the readers, the channel
  told after every change outside the lock, the asset load's progress;
- the words: every code has its words in the web's i18next status_<code>
  (en and ja), with the same args, and no Japanese in the English ones. The
  Qt footer's table (AutonaLocaleHelper.STATUS_TEXT / StatusText) and the
  "status" context of src/i18n/*.ts went with the Qt footer (ADR-005 S-final
  SF-5b-3 removed it, SF-7 its words);
- who reads it: the desktop footer reads this source (Window is never
  built in the tests: src/Autona.py is the startup itself, so it is pinned
  by its code), and the web gets it on GET /api/v1/status and on connect;
- who reports: every call says a known code with its args, never a
  sentence.

The `status` notice's schedule is in tests/test_webserver_delta_debounce.py;
the headless process answers both in tests/test_headless_smoke.py and the
browser E2E (web/e2e/status.spec.ts).
"""
import ast
import json
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.core.Observable import ObserverChannel
from src.core.StatusService import CODES, StatusProgress, StatusService

ROOT = Path(__file__).resolve().parent.parent
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿]")


@pytest.fixture
def told(monkeypatch):
    """A status of its own, starting empty; the snapshots its channel was
    told, read when told."""
    monkeypatch.setattr(StatusService, "_jobs", {})
    monkeypatch.setattr(StatusService, "_revision", 0)
    monkeypatch.setattr(StatusService, "_nextToken", 0)
    channel = ObserverChannel("status")
    monkeypatch.setattr(StatusService, "changes", channel)
    snapshots = []
    channel.subscribe(lambda: snapshots.append(StatusService.Snapshot()))
    return snapshots


def _job(token, code, args=None, done=0, total=0):
    return {"id": token, "code": code, "args": args or {}, "done": done,
            "total": total}


# ── the service ──────────────────────────────────────────────────────────

def test_nothing_runs_at_first(told):
    assert StatusService.Snapshot() == {"revision": 0, "jobs": []}
    assert StatusService.Current() is None


def test_a_job_is_pushed_updated_and_popped(told):
    token = StatusService.Push("startgg_sets")
    assert StatusService.Current() == _job(token, "startgg_sets")

    StatusService.Update(token, "startgg_sets_page",
                         {"page": 2, "pages": 5}, done=2, total=5)
    assert StatusService.Current() == _job(
        token, "startgg_sets_page", {"page": 2, "pages": 5}, 2, 5)

    StatusService.Pop(token)
    assert StatusService.Current() is None
    assert told == [
        {"revision": 1, "jobs": [_job(token, "startgg_sets")]},
        {"revision": 2, "jobs": [_job(
            token, "startgg_sets_page", {"page": 2, "pages": 5}, 2, 5)]},
        {"revision": 3, "jobs": []}]


def test_the_last_pushed_is_the_current(told):
    first = StatusService.Push("startgg_entrants")
    second = StatusService.Push("assets_load", {"codename": "ssbu"})
    assert StatusService.Current()["id"] == second

    # An update of the one underneath does not show it.
    StatusService.Update(first, "startgg_entrants_page",
                         {"page": 1, "pages": 2}, done=0, total=2)
    assert StatusService.Current()["id"] == second
    assert [job["id"] for job in StatusService.Snapshot()["jobs"]] == [
        first, second]

    StatusService.Pop(second)
    assert StatusService.Current() == _job(
        first, "startgg_entrants_page", {"page": 1, "pages": 2}, 0, 2)
    StatusService.Pop(first)
    assert StatusService.Snapshot() == {"revision": 5, "jobs": []}


def test_a_job_not_shown_changes_nothing(told):
    token = StatusService.Push("top8_import")
    StatusService.Pop(token)
    del told[:]

    StatusService.Update(token, "top8_import")
    StatusService.Pop(token)
    StatusService.Pop(None)
    StatusService.Pop(12345)

    assert told == []
    assert StatusService.Snapshot() == {"revision": 2, "jobs": []}


def test_the_readers_get_copies(told):
    StatusService.Push("assets_load", {"codename": "ssbu"})
    snapshot = StatusService.Snapshot()
    snapshot["jobs"][0]["args"]["codename"] = "changed"
    snapshot["jobs"].clear()
    current = StatusService.Current()
    current["args"]["codename"] = "changed too"

    assert StatusService.Current()["args"] == {"codename": "ssbu"}


def test_the_args_given_are_not_kept(told):
    args = {"page": 1, "pages": 2}
    token = StatusService.Push("startgg_sets_page", args)
    args["page"] = 99
    assert StatusService.Current()["args"] == {"page": 1, "pages": 2}
    StatusService.Pop(token)


def test_the_channel_is_told_outside_the_lock(told):
    """A subscriber may read and take locks of its own (the bridge, the web
    server's queue): it is told with the service's lock free."""
    free = []

    def probe(result):
        # Another thread: the RLock is re-entrant for the one told.
        if StatusService._lock.acquire(timeout=5):
            StatusService._lock.release()
            result.append(True)

    def subscriber():
        result = []
        thread = threading.Thread(target=probe, args=(result,))
        thread.start()
        thread.join(10)
        free.append(result == [True])

    StatusService.changes.subscribe(subscriber)
    token = StatusService.Push("result_import")
    StatusService.Update(token, "result_import")
    StatusService.Pop(token)
    assert free == [True, True, True]


def test_a_failing_subscriber_does_not_stop_the_job(told):
    def broken():
        raise RuntimeError("the footer is gone")

    StatusService.changes.subscribe(broken)
    token = StatusService.Push("top8_import")
    StatusService.Pop(token)
    assert StatusService.Current() is None
    assert len(told) == 2


def test_an_unknown_code_is_shown_and_warned_about(told):
    from loguru import logger
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="WARNING")
    try:
        token = StatusService.Push("something_new")
    finally:
        logger.remove(sink)
    assert StatusService.Current() == _job(token, "something_new")
    assert any("something_new" in line for line in lines)


def test_show_is_the_job_of_a_block(told):
    with StatusService.Show("top8_import") as token:
        assert StatusService.Current() == _job(token, "top8_import")
    assert StatusService.Current() is None

    with pytest.raises(RuntimeError):
        with StatusService.Show("result_import"):
            raise RuntimeError("the import failed")
    assert StatusService.Current() is None


def test_the_progress_of_a_load(told):
    """GameAssetCatalog.SelectGame's `progress(done, total)`: pushed on the
    first report, the bar every `every` steps and at the end, popped on
    Close()."""
    progress = StatusService.Progress("assets_load", {"codename": "ssbu"},
                                      every=5)
    assert isinstance(progress, StatusProgress)
    assert told == []

    progress(0, 12)
    token = progress.token
    assert StatusService.Current() == _job(
        token, "assets_load", {"codename": "ssbu"})
    for done in range(1, 5):
        progress(done, 12)
    assert len(told) == 1          # no tick between the fifths
    progress(5, 12)
    assert StatusService.Current()["done"] == 5
    assert StatusService.Current()["total"] == 12
    progress(12, 12)
    assert StatusService.Current()["done"] == 12

    progress.Close()
    progress.Close()               # harmless twice
    assert StatusService.Current() is None
    assert len(told) == 4


def test_a_load_that_never_reports_shows_nothing(told):
    progress = StatusService.Progress("assets_load", {"codename": "ssbu"})
    progress.Close()
    assert told == []
    assert StatusService.Snapshot()["revision"] == 0


def test_many_threads_push_and_pop(told):
    tokens = []
    guard = threading.Lock()

    def worker():
        for _ in range(100):
            token = StatusService.Push("startgg_sets")
            with guard:
                tokens.append(token)
            StatusService.Pop(token)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert len(set(tokens)) == 800
    assert StatusService.Snapshot() == {"revision": 1600, "jobs": []}


# ── the words ────────────────────────────────────────────────────────────

def _webWords(language):
    path = ROOT / "web" / "src" / "i18n" / "locales" / f"{language}.json"
    table = json.loads(path.read_text(encoding="utf-8"))
    return {key[len("status_"):]: words for key, words in table.items()
            if key.startswith("status_")}


@pytest.mark.parametrize("language", ["en", "ja"])
def test_every_code_has_the_webs_words_with_its_args(language):
    words = _webWords(language)
    assert set(words) == set(CODES)
    for code, args in CODES.items():
        named = sorted(re.findall(r"{{(\w+)}}", words[code]))
        assert (code, named) == (code, sorted(args))
        assert bool(JAPANESE.search(words[code])) == (language == "ja"), code


def test_the_qt_footers_words_are_gone():
    # The Qt status bar went in ADR-005 S-final SF-5b-3 (the AppShell's
    # footer reads i18next's status_<code>); its table and the .ts context
    # went in SF-7. The .ts files' contexts are pinned as a whole by
    # tests/test_web_panel_host.py.
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    assert not hasattr(AutonaLocaleHelper, "STATUS_TEXT")
    assert not hasattr(AutonaLocaleHelper, "StatusText")
    for language in ("en", "ja"):
        tree = ET.parse(ROOT / "src" / "i18n" / f"Autona_{language}.ts")
        assert "status" not in {context.findtext("name")
                                for context in tree.getroot().iter("context")}


# ── who reads it ─────────────────────────────────────────────────────────

def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _calls(node):
    return [_dotted(call.func) for call in ast.walk(node)
            if isinstance(call, ast.Call)]


def test_the_desktop_footer_reads_the_status_service():
    # The desktop's footer is the AppShell's since ADR-005 S-final SF-5b-3
    # (web/src/AppShell/Footer.tsx through useAppStatus: GET /api/v1/status,
    # then Socket.IO `status`); the Qt status bar it replaced
    # (Window._on_status_changed: StatusService.Current, the .ts words)
    # went. The bridge's signal has one receiver, the web server's notice.
    window = _tree("src/Autona.py")
    assert "_on_status_changed" not in {
        node.name for node in ast.walk(window)
        if isinstance(node, ast.FunctionDef)}
    assert not [call for call in _calls(window) if "Status" in call]
    receivers = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(call, ast.Call) and _dotted(call.func)
                    == "StateManagerBridge.instance.status_changed.connect"):
                receivers.append((path.name, _dotted(call.args[0])))
    assert receivers == [("AutonaWebServer.py", "WebServer.on_status_change")]
    footer = (ROOT / "web" / "src" / "AppShell" / "Footer.tsx").read_text(
        encoding="utf-8")
    assert ('import {statusText, useAppStatus} from '
            '"../PanelShell/useAppStatus";') in footer


def test_the_qt_status_source_is_gone():
    assert not (ROOT / "src" / "Helpers" / "AutonaStatusManager.py").exists()
    # Nothing imports or names it (a docstring may tell its history).
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert "AutonaStatusManager" not in (node.module or ""), path
                assert all(alias.name != "AutonaStatusManager"
                           for alias in node.names), path
            elif isinstance(node, ast.Name):
                assert node.id != "AutonaStatusManager", path


def test_the_web_gets_the_status_on_get_and_on_connect(monkeypatch, told,
                                                       tmp_path):
    from src import AutonaWebOperations as WebOps
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WebOps, "openSetSelection", None)
    monkeypatch.setattr(WebOps, "renderThumbnail", None)
    monkeypatch.setattr(WebOps, "startImageRender", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", ())
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())
    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    token = StatusService.Push("assets_load", {"codename": "ssbu"})
    StatusService.Update(token, "assets_load", {"codename": "ssbu"},
                         done=10, total=80)
    expected = {"revision": 2, "jobs": [_job(
        token, "assets_load", {"codename": "ssbu"}, 10, 80)]}

    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "data": expected}
    # Read from the LAN too: codes and counts (WebAccess READ).
    peer = app.test_client()
    peer.environ_base["REMOTE_ADDR"] = "192.168.0.50"
    assert peer.get("/api/v1/status").get_json()["data"] == expected

    ws = socketio.test_client(app, flask_test_client=client)
    try:
        received = ws.get_received()
    finally:
        ws.disconnect()
    names = [packet["name"] for packet in received]
    # After the state, to the new connection.
    assert names.index("status") > names.index("program_state")
    [status] = [packet["args"] for packet in received
                if packet["name"] == "status"]
    assert status == [expected]


# ── who reports ──────────────────────────────────────────────────────────

# The calls whose code is not a literal: the variable and where it comes
# from (checked below).
INDIRECT = {("src/AutonaTournamentDataProvider.py", "status")}
CODE_ARG = {"Push": 0, "Show": 0, "Progress": 0, "Update": 1}


def _reports():
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative == "src/core/StatusService.py":
            continue
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(call, ast.Call):
                continue
            name = _dotted(call.func)
            method = name.rpartition(".")[2]
            if name.startswith("StatusService.") and method in CODE_ARG:
                yield relative, method, call


def test_every_report_says_a_known_code_with_its_args():
    seen = set()
    for relative, method, call in _reports():
        index = CODE_ARG[method]
        code = call.args[index]
        where = f"{relative}:{call.lineno}"
        if isinstance(code, ast.Constant):
            assert code.value in CODES, where
            seen.add(code.value)
            args = (call.args[index + 1] if len(call.args) > index + 1
                    else next((kw.value for kw in call.keywords
                               if kw.arg == "args"), None))
            if isinstance(args, ast.Dict):
                keys = sorted(key.value for key in args.keys)
                assert keys == sorted(CODES[code.value]), where
            else:
                assert args is None and not CODES[code.value], where
        else:
            assert (relative, _dotted(code)) in INDIRECT, where
        # Never a sentence.
        for node in ast.walk(call):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not JAPANESE.search(node.value), where
    # The start.gg fetches and the asset load say theirs.
    assert seen >= {"startgg_bracket", "startgg_set", "startgg_sets",
                    "startgg_sets_page", "startgg_sets_failed",
                    "startgg_entrants", "startgg_entrants_page",
                    "assets_load"}


def test_the_provider_jobs_name_their_codes():
    """RunWithProvider's `status` (INDIRECT above): the services hand their
    fetcher (the shell's RunWithProvider) their IMPORT_STATUS."""
    from src.core import ResultService, Top8Service
    assert Top8Service.IMPORT_STATUS == "top8_import"
    assert ResultService.IMPORT_STATUS == "result_import"
    for relative in ("src/core/Top8Service.py", "src/core/ResultService.py"):
        handed = [call for call in ast.walk(_tree(relative))
                  if isinstance(call, ast.Call)
                  and _dotted(call.func).endswith("fetcher")]
        assert handed, relative
        for call in handed:
            assert [_dotted(arg) for arg in call.args][1:] == [
                "IMPORT_STATUS"], relative


def test_the_startgg_provider_has_no_japanese_words():
    text = (ROOT / "src" / "TournamentDataProvider"
            / "StartGGDataProvider.py").read_text(encoding="utf-8")
    assert not JAPANESE.search(text)
