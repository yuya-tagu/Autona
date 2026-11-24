"""What an off-screen render's template may read (ADR-007 D4 / T2; ADR-008 D7
R1).

The match thumbnail and the tournament result are drawn from the preview
listener's origin (AutonaOffscreenRenderer): a template reads what that
listener serves and the files its render holds (core/RenderAssets), and its
requests anywhere else -- file:, another port of this PC (this server's own),
the outside -- never leave the page. Without a preview listener the render is
refused, never drawn from file://.

The page under test is a real QWebEngineView (offscreen) with a probe page in
place of thumbnail.html: it tries each way out, and a TCP sink on another
127.0.0.1 port counts what arrives there (the stand-in for this server's own
port and for the outside: to the page's filter they are the same, another
origin). Waits end as soon as the page has answered (see
tests/test_thumbnail_render.py on why nothing depends on how long a load
takes).
"""
import json
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from qtpy.QtGui import QColor, QPixmap

from src.core import RenderAssets
from src.core.RenderLane import PREVIEW_UNAVAILABLE
from tests.fixtures import template_packages

WAIT_SECS = 60.0
LONG_TIMEOUT_MS = int(WAIT_SECS * 1000 * 2)


def _waitUntil(predicate, timeout=WAIT_SECS):
    # A nested loop, not processEvents() (pitfalls): QWebEngine answers
    # through the event loop.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


def _image(path, fmt="PNG"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (0, 200, 0)).save(path, fmt)
    return Path(path)


# ── RenderAssets: what a payload's paths become ──────────────────────────

@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A working directory of the test's own (the services' relative paths
    are to it), and the app's directory the listener serves from."""
    from src.core import LayoutCatalog

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(LayoutCatalog, "_appDirectory", lambda: str(tmp_path))
    yield tmp_path


def _drawn(value, key="background_path"):
    payload = {"data": {}, "config": {key: value}, "template": {}}
    drawn, held = RenderAssets.ForRender(payload)
    return drawn["config"][key], held


def test_a_served_path_stays_relative_with_its_version(workdir):
    banner = _image(workdir / "user_data" / "tournament_banner.png")
    stamp = f"{banner.stat().st_mtime_ns:x}"

    assert _drawn("./user_data/tournament_banner.png?v=3", "main_icon_path") \
        == (f"./user_data/tournament_banner.png?v=3&m={stamp}", [])
    assert _drawn("./user_data/tournament_banner.png") \
        == (f"./user_data/tournament_banner.png?m={stamp}", [])


def test_what_the_listener_does_not_serve_is_held_under_an_id(workdir):
    upload = _image(workdir / "user_data" / "thumbnail_background_ab12.png")
    frame = _image(workdir / "jobs" / "0001_bg.jpg", "JPEG")
    held = []
    try:
        for value, path in (
                ("./user_data/thumbnail_background_ab12.png", upload),
                (str(frame), frame),
                (frame.as_uri(), frame)):
            drawn, ids = _drawn(value)
            held += ids
            assert len(ids) == 1
            assert drawn == RenderAssets.Url(ids[0])
            assert drawn.startswith("/render-asset/")
            # The template is given no path of this PC's.
            assert str(workdir) not in drawn and "jobs" not in drawn
            assert Path(RenderAssets.HeldPath(ids[0])) == path.resolve()
        # The same file is the same id (the page's decoded image is reused).
        assert held[1] == held[2]
    finally:
        RenderAssets.Release(held)
    assert [RenderAssets.HeldPath(i) for i in held] == [None] * 3


def test_a_file_changed_in_place_is_a_new_id(workdir):
    upload = _image(workdir / "user_data" / "thumbnail_background_x.png")
    _first, [one] = _drawn(str(upload))
    RenderAssets.Release([one])
    Image.new("RGB", (16, 16), (200, 0, 0)).save(upload, "PNG")
    _second, [two] = _drawn(str(upload))
    RenderAssets.Release([two])
    assert one != two


def test_a_file_held_twice_is_served_until_both_let_go(workdir):
    upload = _image(workdir / "user_data" / "thumbnail_background_y.png")
    _a, [one] = _drawn(str(upload))
    _b, [two] = _drawn(str(upload))
    assert one == two
    RenderAssets.Release([one])
    assert RenderAssets.HeldPath(two) is not None
    RenderAssets.Release([two])
    assert RenderAssets.HeldPath(two) is None


@pytest.mark.parametrize("value", [
    "./user_data/nothing_here.png",          # not there
    "./user_data/tournament_banner.png?v=2",  # served, and not there
    "assets/no_such_banner.png",
    "C:/no/such/folder/bg.png",
    "./tmp/not_an_image.txt",                 # not an image
    "file://otherhost/share/bg.png",          # another host's
    # A network path: not looked at on the GUI thread, where a render starts.
    "\\\\fileserver\\share\\bg.png", "//fileserver/share/bg.png",
    "file:////fileserver/share/bg.png",
])
def test_what_cannot_be_read_is_given_as_nothing(workdir, value):
    (workdir / "tmp").mkdir()
    (workdir / "tmp" / "not_an_image.txt").write_text("x", encoding="utf-8")
    assert _drawn(value) == ("", [])


@pytest.mark.parametrize("value", [
    "data:image/png;base64,AAAA", "blob:http://127.0.0.1:1/x",
    "https://example.invalid/bg.png", "/render-asset/whatever",
    "images/background.png",                  # the template's own folder's
])
def test_what_the_page_resolves_is_kept(workdir, value):
    assert _drawn(value) == (value, [])


def test_the_payload_given_is_not_changed(workdir):
    upload = _image(workdir / "user_data" / "thumbnail_background_z.png")
    payload = {"data": {"a": 1}, "config": {"background_path": str(upload),
                                           "other": 2}}
    before = json.dumps(payload)
    drawn, held = RenderAssets.ForRender(payload)
    RenderAssets.Release(held)
    assert json.dumps(payload) == before
    assert drawn["config"]["other"] == 2 and drawn["data"] is payload["data"]
    assert RenderAssets.ForRender({"data": {}}) == ({"data": {}}, [])


@pytest.mark.parametrize("assetId", [None, "", "../settings.json",
                                     "x" * 21, "x" * 23, "a/b" * 8])
def test_what_is_not_an_id_names_nothing(assetId):
    assert RenderAssets.HeldPath(assetId) is None


def test_a_file_url_is_decoded_once(workdir):
    # A file named "%41.png" is "%2541.png" in its URL; decoded twice it was
    # looked for as "A.png" (and A.png, if there, drawn instead).
    named = _image(workdir / "frames" / "%41.png")
    _image(workdir / "frames" / "A.png")
    drawn, [assetId] = _drawn(named.as_uri())
    assert "%2541" in named.as_uri()
    assert Path(RenderAssets.HeldPath(assetId)) == named.resolve()
    RenderAssets.Release([assetId])


def test_a_served_path_is_looked_for_where_the_listener_serves_it(
        workdir, tmp_path_factory, monkeypatch):
    from src.core import LayoutCatalog

    appDir = tmp_path_factory.mktemp("app")
    banner = _image(appDir / "user_data" / "tournament_banner.png")
    monkeypatch.setattr(LayoutCatalog, "_appDirectory", lambda: str(appDir))
    stamp = f"{banner.stat().st_mtime_ns:x}"
    # The working directory has none: the app's directory has.
    assert _drawn("./user_data/tournament_banner.png") \
        == (f"./user_data/tournament_banner.png?m={stamp}", [])


# ── the route: the preview listener's alone, while held ─────────────────

def test_a_held_file_is_served_on_the_preview_listener_only(
        autonaApp, previewListener, tmp_path):
    from werkzeug.test import Client

    from src.AutonaWebServer import PreviewGate, _build_web_app

    upload = _image(tmp_path / "bg.png")
    _drawnPath, [assetId] = _drawn(str(upload))
    url = f"http://127.0.0.1:{previewListener}{RenderAssets.Url(assetId)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200
            assert response.read() == upload.read_bytes()
            assert "no-store" in response.headers["Cache-Control"]
        # This server's own port: like a missing file.
        app, _socketio = _build_web_app()
        assert Client(app).get(RenderAssets.Url(assetId)).status_code == 404
        # The preview listener, bound to every address (LAN access on), asked
        # by another computer, or through a proxy: the same.
        gated = Client(PreviewGate(app))
        for remote, headers in (("192.168.1.20", {}), ("::ffff:10.0.0.3", {}),
                                ("127.0.0.1", {"X-Forwarded-For": "10.0.0.9"})):
            assert gated.get(RenderAssets.Url(assetId), headers=headers,
                             environ_base={"REMOTE_ADDR": remote}
                             ).status_code == 404, remote
        assert gated.get(RenderAssets.Url(assetId), environ_base={
            "REMOTE_ADDR": "::1"}).status_code == 200
    finally:
        RenderAssets.Release([assetId])
    with pytest.raises(urllib.error.HTTPError) as gone:
        urllib.request.urlopen(url, timeout=10)
    assert gone.value.code == 404


# ── a real page: what reaches where ──────────────────────────────────────

PROBE = r"""<!doctype html><html><body><script>
async function tryFetch(url) {
  try { const r = await fetch(url, {cache: "no-store"});
        return r.status; } catch (e) { return "blocked"; }
}
function tryImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve("loaded");
    img.onerror = () => resolve("blocked");
    img.src = url;
  });
}
function trySocket(url) {
  return new Promise((resolve) => {
    let ws;
    try { ws = new WebSocket(url); } catch (e) { resolve("blocked"); return; }
    ws.onopen = () => resolve("open");
    ws.onerror = () => resolve("blocked");
  });
}
function tryFrame(url) {
  return new Promise((resolve) => {
    const frame = document.createElement("iframe");
    frame.onload = () => {
      let where;
      try { where = frame.contentWindow.location.href; }
      catch (e) { where = "elsewhere"; }
      resolve(where);
    };
    frame.src = url;
    document.body.appendChild(frame);
    setTimeout(() => resolve("no load"), 3000);
  });
}
async function probe(config) {
  const sink = "http://127.0.0.1:" + config.sink;
  const own = location.origin;
  const out = {};
  out.sinkFetch = await tryFetch(sink + "/fetch");
  out.sinkImage = await tryImage(sink + "/image.png");
  out.sinkSocket = await trySocket("ws://127.0.0.1:" + config.sink + "/ws");
  out.ownSocket = await trySocket(own.replace("http", "ws") + "/socket.io/");
  out.sinkBeacon = navigator.sendBeacon(sink + "/beacon", "x");
  out.sinkFrame = await tryFrame(sink + "/frame");
  out.otherHost = await tryFetch(
    "http://localhost:" + location.port + "/api/v1/health");
  out.fileFetch = await tryFetch(config.fileUrl);
  out.fileImage = await tryImage(config.fileUrl);
  out.opened = window.open(sink + "/window") === null ? "none" : "opened";
  out.layout = await tryFetch(location.pathname);
  out.assets = await tryFetch("/" + config.served);
  out.health = await tryFetch("/api/v1/health");
  out.held = await tryFetch(config.background_path);
  out.heldImage = await tryImage(config.background_path);
  out.background = config.background_path;
  try {
    out.notification = await Notification.requestPermission();
  } catch (e) { out.notification = "threw"; }
  try {
    await navigator.mediaDevices.getUserMedia({audio: true});
    out.camera = "granted";
  } catch (e) { out.camera = "refused"; }
  // Workers, whose requests a page's own interceptor would not see (the
  // shared and the service worker's): each asks the sink.
  const query = "?sink=" + config.sink;
  out.worker = await tryWorker(() => new Worker("probe_worker.js" + query));
  out.sharedWorker = await tryWorker(
    () => new SharedWorker("probe_worker.js" + query + "&shared=1"));
  out.serviceWorker = await tryServiceWorker("probe_sw.js" + query);
  // What another page of this origin in another profile could hear.
  new BroadcastChannel("autona-render-probe").postMessage("from the render");
  try { localStorage.setItem("autona-render-probe", "from the render"); }
  catch (e) {}
  return out;
}
function tryWorker(make) {
  return new Promise((resolve) => {
    let worker;
    try { worker = make(); } catch (e) { resolve("not made"); return; }
    const port = worker.port || worker;
    port.onmessage = (event) => resolve(event.data);
    if (worker.port) worker.port.start();
    worker.onerror = () => resolve("failed");
    setTimeout(() => resolve("no answer"), 5000);
  });
}
async function tryServiceWorker(url) {
  try {
    const registration = await navigator.serviceWorker.register(url);
    const worker = registration.installing || registration.waiting
      || registration.active;
    await new Promise((resolve) => {
      if (!worker || worker.state === "activated") { resolve(); return; }
      worker.addEventListener("statechange", () => {
        if (worker.state === "activated" || worker.state === "redundant")
          resolve();
      });
      setTimeout(resolve, 5000);
    });
    return "registered";
  } catch (e) { return "refused"; }
}
function initThumbnail(payload) {
  probe(payload.config).then((out) => {
    window.probed = out;
    // The page may not go elsewhere: it stays, and says it is ready.
    location.href = "http://127.0.0.1:" + payload.config.sink + "/navigate";
    setTimeout(() => { document.title = "RENDER_READY"; }, 300);
  });
}
</script></body></html>
"""


# A worker that asks the sink, and says how it went.
WORKER = r"""
const sink = "http://127.0.0.1:" + new URL(location.href).searchParams.get("sink");
function ask(reply) {
  fetch(sink + "/worker").then(() => reply("fetched"),
                                 () => reply("blocked"));
}
if (new URL(location.href).searchParams.get("shared")) {
  onconnect = (event) => { const port = event.ports[0];
                           ask((said) => port.postMessage(said)); };
} else {
  ask((said) => postMessage(said));
}
"""

# A service worker that asks the sink as it installs, and for every fetch of
# its pages.
SERVICE_WORKER = r"""
const sink = "http://127.0.0.1:" + new URL(location.href).searchParams.get("sink");
self.addEventListener("install", (event) => {
  event.waitUntil(fetch(sink + "/sw-install").catch(() => null));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(fetch(sink + "/sw-activate").catch(() => null));
});
"""

# The design preview's frame, as far as a render could tell: a page of the
# same origin in the default profile. What it hears is kept.
LISTEN = r"""<!doctype html><html><body><script>
window.heard = [];
new BroadcastChannel("autona-render-probe").onmessage =
  (event) => window.heard.push(event.data);
// That a channel carries at all here: one of its own, to itself.
window.control = [];
new BroadcastChannel("autona-render-control").onmessage =
  (event) => window.control.push(event.data);
new BroadcastChannel("autona-render-control").postMessage("control");
document.title = "LISTENING";
</script></body></html>
"""


class _Sink:
    """A TCP listener on 127.0.0.1 that counts the connections it accepts."""

    def __init__(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self._socket.settimeout(0.2)
        self.port = self._socket.getsockname()[1]
        self.accepted = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="test-render-sink")
        self._thread.start()

    def _run(self):
        while not self._stop:
            try:
                connection, _address = self._socket.accept()
            except OSError:
                continue
            self.accepted += 1
            try:
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2"
                                   b"\r\nConnection: close\r\n\r\nok")
            except OSError:
                pass
            connection.close()

    def close(self):
        self._stop = True
        self._thread.join(5)
        self._socket.close()


@pytest.fixture
def sink():
    made = _Sink()
    yield made
    made.close()


def _destroy(renderer):
    renderer._watchdog.stop()
    renderer._host.close()
    renderer._view.setPage(None)
    for thing in (renderer._page, renderer._host, renderer):
        thing.deleteLater()
        QCoreApplication.sendPostedEvents(thing, QEvent.Type.DeferredDelete)


def _renderer():
    from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer

    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    # On the offscreen platform a grab is the widget's background: what is
    # under test is what the page reached, not its pixels.
    made._view.grab = lambda: _filled()
    return made


def _filled():
    pixmap = QPixmap(1920, 1080)
    pixmap.fill(QColor("#00ff00"))
    return pixmap


@pytest.fixture
def probePages(autonaApp):
    created = template_packages.Install(
        autonaApp.dir / "layout", template_packages.THUMBNAIL_FOLDERS,
        page=PROBE)
    served = autonaApp.dir / "assets" / "icons" / "render_probe.svg"
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>',
                      encoding="utf-8")
    folder = autonaApp.dir / "layout" / "thumbnail"
    scripts = {"probe_worker.js": WORKER, "probe_sw.js": SERVICE_WORKER,
               "probe_listen.html": LISTEN}
    for name, text in scripts.items():
        (folder / name).write_text(text, encoding="utf-8")
    yield "assets/icons/render_probe.svg"
    served.unlink()
    for name in scripts:
        (folder / name).unlink(missing_ok=True)
    for directory in created:
        shutil.rmtree(directory, ignore_errors=True)


def _evaluate(renderer, script):
    answers = []
    renderer._page.runJavaScript(script, answers.append)
    assert _waitUntil(lambda: answers)
    return answers[0]


def test_a_template_reads_its_origin_and_nothing_else(
        probePages, previewListener, sink, tmp_path):
    background = _image(tmp_path / "jobs" / "0001_bg.jpg", "JPEG")
    secret = _image(tmp_path / "elsewhere" / "private.png")
    from qtpy.QtCore import QUrl
    from qtpy.QtWebEngineCore import QWebEnginePage

    # The design preview's frame stands in: the same origin, the default
    # profile (the shared view's).
    listener = QWebEnginePage()
    listener.load(QUrl(f"http://127.0.0.1:{previewListener}"
                       "/layout/thumbnail/probe_listen.html"))
    assert _waitUntil(lambda: listener.title() == "LISTENING")
    renderer = _renderer()
    try:
        results = []
        renderer.renderAsync(
            {}, {"sink": sink.port, "fileUrl": secret.as_uri(),
                 "served": probePages,
                 "background_path": background.as_uri()},
            {"template_id": "type_a"},
            lambda pixmap, error: results.append((pixmap, error)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        probed = json.loads(
            _evaluate(renderer, "JSON.stringify(window.probed)"))
        held = probed["background"]

        # What the preview listener serves, and the render's held file.
        assert probed["layout"] == 200
        assert probed["assets"] == 200
        assert probed["health"] == 200
        assert held.startswith("/render-asset/")
        assert str(tmp_path) not in held and "jobs" not in held
        assert (probed["held"], probed["heldImage"]) == (200, "loaded")
        # Nothing else: not another port, not another name for this PC, not
        # file:, not a socket even to its own origin's port (the filter lets
        # http alone through), no window, no permission.
        assert probed["sinkFetch"] == "blocked"
        assert probed["sinkImage"] == "blocked"
        assert probed["sinkSocket"] == "blocked"
        assert probed["ownSocket"] == "blocked"
        assert probed["sinkFrame"] != f"http://127.0.0.1:{sink.port}/frame"
        assert probed["otherHost"] == "blocked"
        assert probed["fileFetch"] == "blocked"
        assert probed["fileImage"] == "blocked"
        assert probed["opened"] == "none"
        assert probed["notification"] != "granted"
        assert probed["camera"] == "refused"
        # Workers of every kind ask nothing of anywhere else either: the
        # filter is the profile's, which sees their requests too. (A service
        # worker may register, from its origin; what it asks goes nowhere.)
        assert probed["worker"] == "blocked"
        assert probed["sharedWorker"] == "blocked"
        assert probed["serviceWorker"] in ("registered", "refused")
        # The main frame stayed on its document.
        url = renderer._view.url()
        assert (url.scheme(), url.host(), url.port()) == (
            "http", "127.0.0.1", previewListener)
        assert url.path().startswith("/layout/")
        assert url.path().endswith("/thumbnail.html")
        # What the sink saw: nothing at all (a beacon is sent after the page
        # asks: given a moment).
        _waitUntil(lambda: sink.accepted, timeout=1.5)
        assert sink.accepted == 0
        # The default profile's page of the same origin heard nothing, and
        # sees none of the render's storage: another profile.
        assert _evaluateOn(listener, "JSON.stringify(window.control)")             == '["control"]'
        assert _evaluateOn(
            listener, "JSON.stringify(localStorage.getItem("
            "'autona-render-probe'))") == "null"
        # While a page of its own profile is heard: one more page there says
        # something on the channel the render said its piece on.
        other = QWebEnginePage()
        other.load(QUrl(f"http://127.0.0.1:{previewListener}"
                        "/layout/thumbnail/probe_listen.html"))
        assert _waitUntil(lambda: other.title() == "LISTENING")
        _evaluateOn(other, "new BroadcastChannel('autona-render-probe')"
                    ".postMessage('from the same profile'), 1")
        assert _waitUntil(lambda: _evaluateOn(
            listener, "window.heard.length") == 1)
        assert _evaluateOn(listener, "JSON.stringify(window.heard)")             == '["from the same profile"]'
        other.deleteLater()
        QCoreApplication.sendPostedEvents(other, QEvent.Type.DeferredDelete)

        # The render is over: its held file is not served any more.
        with pytest.raises(urllib.error.HTTPError) as gone:
            urllib.request.urlopen(
                f"http://127.0.0.1:{previewListener}{held}", timeout=10)
        assert gone.value.code == 404
    finally:
        _destroy(renderer)
        listener.deleteLater()
        QCoreApplication.sendPostedEvents(listener, QEvent.Type.DeferredDelete)


def _evaluateOn(page, script):
    answers = []
    page.runJavaScript(script, answers.append)
    assert _waitUntil(lambda: answers)
    return answers[0]


def test_a_render_without_a_preview_listener_is_refused(
        probePages, monkeypatch):
    from src.AutonaWebServer import WebServer

    monkeypatch.setattr(WebServer, "_preview_port", None)
    renderer = _renderer()
    try:
        # Nothing was loaded: not from file:// either.
        assert renderer._view.url().isEmpty()
        results = []
        renderer.renderAsync({}, {}, {"template_id": "type_a"},
                             lambda pixmap, error: results.append(
                                 (pixmap, error)))
        assert _waitUntil(lambda: results)
        assert results == [(None, PREVIEW_UNAVAILABLE)]
        # The X post's synchronous render (SCORE's renderSync): an empty
        # picture, which its caller takes for a failure.
        assert renderer.renderSync({}, {}, {"template_id": "type_a"}).isNull()
        assert renderer._view.url().isEmpty()
    finally:
        _destroy(renderer)


def test_a_page_from_a_listener_gone_is_not_drawn_on(
        probePages, previewListener, monkeypatch, sink):
    from src.AutonaWebServer import WebServer

    renderer = _renderer()
    try:
        assert _waitUntil(lambda: renderer._page_loaded)
        # The listener's port is not the page's any more (it could not
        # bind again): the next render loads again, and so is refused.
        monkeypatch.setattr(WebServer, "_preview_port", None)
        results = []
        renderer.renderAsync({}, {"sink": sink.port},
                             {"template_id": "type_a"},
                             lambda pixmap, error: results.append(error))
        assert _waitUntil(lambda: results)
        assert results == [PREVIEW_UNAVAILABLE]
    finally:
        _destroy(renderer)


# ── a folder of any name; what a render held is let go however it ended ──

STANDIN = """<!doctype html><html><body><script>
function initThumbnail(payload) {
  if (payload.config.hang) return;
  document.title = "RENDER_READY";
}
</script></body></html>
"""


@pytest.fixture
def standinPages(autonaApp):
    created = template_packages.Install(
        autonaApp.dir / "layout", template_packages.THUMBNAIL_FOLDERS,
        page=STANDIN)
    yield autonaApp.dir / "layout"
    for directory in created:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_folder_named_in_any_script_is_drawn(standinPages, previewListener):
    # Japanese, a space and symbols: percent-encoded in the URL the renderer
    # loads, and the page Chromium navigates to is still that document
    # (_RenderPage.acceptNavigationRequest).
    name = "サムネ テスト #1&(+)%"
    folder = standinPages / name
    folder.mkdir()
    source = template_packages.PROJECT_ROOT / "layout" / "thumbnail_type_b"
    shutil.copy(source / "settings.json", folder / "settings.json")
    shutil.copy(source / "type_b.json", folder / "type_b.json")
    (folder / "thumbnail.html").write_text(STANDIN, encoding="utf-8")
    renderer = _renderer()
    try:
        results = []
        renderer.renderAsync({}, {}, {"template_id": name},
                             lambda pixmap, error: results.append(error))
        assert _waitUntil(lambda: results)
        assert results == [""]
        assert renderer._template_id == name
        assert renderer._view.url().path() \
            == f"/layout/{name}/thumbnail.html"
    finally:
        _destroy(renderer)
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def holds(monkeypatch):
    """The ids every render held (RenderAssets.ForRender), as it held them."""
    held = []
    forRender = RenderAssets.ForRender

    def recording(payload):
        drawn, ids = forRender(payload)
        held.extend(ids)
        return drawn, ids

    monkeypatch.setattr(RenderAssets, "ForRender", recording)
    return held


class _NotJson:
    pass


@pytest.mark.parametrize("how", ["timed out", "could not start"])
def test_a_render_that_failed_lets_go_of_what_it_held(
        standinPages, previewListener, holds, tmp_path, how):
    background = _image(tmp_path / "bg.png")
    renderer = _renderer()
    renderer.STAGE_TIMEOUT_MS = 1500
    try:
        assert _waitUntil(lambda: renderer._page_loaded)
        results = []
        data = {"x": _NotJson()} if how == "could not start" else {}
        renderer.renderAsync(data, {"hang": True,
                                    "background_path": str(background)},
                             {"template_id": "type_a"},
                             lambda pixmap, error: results.append(error))
        assert _waitUntil(lambda: results)
        assert results[0] != ""
        assert len(holds) == 1
        assert RenderAssets.HeldPath(holds[0]) is None
        assert RenderAssets._held == {}
    finally:
        _destroy(renderer)


def test_a_render_whose_page_did_not_load_holds_nothing(
        standinPages, sink, holds, monkeypatch, tmp_path):
    from src.AutonaWebServer import WebServer

    # A port nothing serves a page on: the load fails.
    port = sink.port
    sink.close()
    monkeypatch.setattr(WebServer, "_preview_port", port)
    background = _image(tmp_path / "bg.png")
    renderer = _renderer()
    renderer.STAGE_TIMEOUT_MS = 1500
    try:
        results = []
        renderer.renderAsync({}, {"background_path": str(background)},
                             {"template_id": "type_a"},
                             lambda pixmap, error: results.append(error))
        assert _waitUntil(lambda: results)
        assert results[0] != ""
        assert holds == []
        assert RenderAssets._held == {}
    finally:
        _destroy(renderer)
