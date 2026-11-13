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
- the words: every code has its words in every table (the footer's
  AutonaLocaleHelper.STATUS_TEXT and the "status" context of src/i18n/*.ts,
  the web's i18next status_<code>), with the same args, and no Japanese in
  the English ones; the footer's words follow the program's language;
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
import string
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

def _fields(text):
    return sorted(name for _lit, name, _spec, _conv
                  in string.Formatter().parse(text) if name)


def _tsStatus(language):
    tree = ET.parse(ROOT / "src" / "i18n" / f"Autona_{language}.ts")
    [context] = [context for context in tree.getroot().iter("context")
                 if context.findtext("name") == "status"]
    return {message.findtext("source"): message.findtext("translation")
            for message in context.iter("message")}


def _webWords(language):
    path = ROOT / "web" / "src" / "i18n" / "locales" / f"{language}.json"
    table = json.loads(path.read_text(encoding="utf-8"))
    return {key[len("status_"):]: words for key, words in table.items()
            if key.startswith("status_")}


def test_every_code_has_the_footers_words_with_its_args():
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    table = AutonaLocaleHelper.STATUS_TEXT
    assert set(table) == set(CODES)
    for code, args in CODES.items():
        assert (code, _fields(table[code])) == (code, sorted(args))
        assert not JAPANESE.search(table[code]), code


@pytest.mark.parametrize("language", ["en", "ja"])
def test_the_footers_words_are_translated(language):
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    messages = _tsStatus(language)
    assert set(messages) == set(AutonaLocaleHelper.STATUS_TEXT.values())
    for source, translation in messages.items():
        assert (source, _fields(translation)) == (source, _fields(source))
        if language == "en":
            assert translation == source
        else:
            assert JAPANESE.search(translation), source


@pytest.mark.parametrize("language", ["en", "ja"])
def test_every_code_has_the_webs_words_with_its_args(language):
    words = _webWords(language)
    assert set(words) == set(CODES)
    for code, args in CODES.items():
        named = sorted(re.findall(r"{{(\w+)}}", words[code]))
        assert (code, named) == (code, sorted(args))
        assert bool(JAPANESE.search(words[code])) == (language == "ja"), code


def _translated(path, job):
    from qtpy.QtCore import QCoreApplication, QTranslator
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    translator = QTranslator()
    assert translator.load(str(path))
    # The last one installed is asked first, whatever the harness installed.
    assert QCoreApplication.installTranslator(translator)
    try:
        return AutonaLocaleHelper.StatusText(job)
    finally:
        QCoreApplication.removeTranslator(translator)


def test_the_footers_words_follow_the_programs_language(qapp):
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    i18n = ROOT / "src" / "i18n"
    pages = _job(1, "startgg_sets_page", {"page": 2, "pages": 5}, 2, 5)
    load = _job(2, "assets_load", {"codename": "ssbu"}, 10, 80)

    assert _translated(i18n / "Autona_en.qm", pages) == (
        "Fetching the set list from Start.gg... (2/5)")
    assert _translated(i18n / "Autona_en.qm", load) == (
        "Loading game assets... (ssbu)")
    assert _translated(i18n / "Autona_ja.qm", pages) == (
        "Start.gg からセット一覧を取得中... (2/5)")
    assert _translated(i18n / "Autona_ja.qm", load) == (
        "ゲームアセットを読み込み中... (ssbu)")
    assert AutonaLocaleHelper.StatusText(None) == ""
    # A code this build does not know shows as itself.
    assert AutonaLocaleHelper.StatusText(_job(3, "something_new")) == (
        "something_new")


def test_every_ja_word_is_compiled():
    """The .qm the app loads holds every status translation (the .ts is
    edited by hand and compiled by scripts/compile_translations.ps1)."""
    data = (ROOT / "src" / "i18n" / "Autona_ja.qm").read_bytes()
    for translation in _tsStatus("ja").values():
        assert translation.encode("utf-16-be") in data, translation


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
    tree = _tree("src/Autona.py")
    [slot] = [node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_on_status_changed"]
    calls = _calls(slot)
    assert "StatusService.Current" in calls
    assert "AutonaLocaleHelper.StatusText" in calls
    # Told by the bridge (the one subscriber of the core channel), and read
    # once when built: a job pushed before the footer existed is shown.
    connects = [call for call in ast.walk(tree)
                if isinstance(call, ast.Call)
                and _dotted(call.func)
                == "StateManagerBridge.instance.status_changed.connect"]
    assert [_dotted(call.args[0]) for call in connects] == [
        "self._on_status_changed"]
    assert "self._on_status_changed" in _calls(tree)


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
