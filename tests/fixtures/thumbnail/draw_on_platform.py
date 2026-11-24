"""Draw one match thumbnail as the desktop app would, and say what it drew.

Run by tests/test_render_platform.py in a process of its own (the tests'
process is on Qt's offscreen platform, tests/conftest.py): python <this>
<project root>. The startup the off-screen renderer meets in the app (main.py:
AA_ShareOpenGLContexts before the QApplication, Window.LoadTheme's
qdarktheme), the preview listener as the server makes it, the shipped type_a
template with the default banner, a real grab(). Prints one JSON line:
{"error", "colours", "centre"} -- the colours sampled on a 60 px grid.
Writes nothing: the working directory is the caller's (a tmp one), and the
app's directory -- what is served and listed -- is the project's.
"""
import copy
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
os.environ["QT_API"] = "pyside6"

from qtpy.QtCore import QEventLoop, QTimer, Qt  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
app = QApplication(sys.argv[:1])

import qdarktheme  # noqa: E402

from src import ui_constants as UI  # noqa: E402

qdarktheme.setup_theme("dark", custom_colors=UI.DARK_CUSTOM_COLORS,
                       additional_qss=UI.global_qss())

from werkzeug.serving import make_server  # noqa: E402

from src.AutonaWebServer import PreviewGate, WebServer, _build_web_app  # noqa: E402
from src.core import LayoutCatalog  # noqa: E402

LayoutCatalog.SetAppDirectory(lambda: str(ROOT))
webApp, _socketio = _build_web_app()
server = WebServer._MakeListener(make_server, "127.0.0.1", 0,
                                 PreviewGate(webApp))
threading.Thread(target=server.serve_forever, daemon=True).start()
WebServer._preview_port = server.server_port

from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer  # noqa: E402


def wait(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


golden = json.loads((ROOT / "tests/fixtures/thumbnail/golden.json")
                    .read_text(encoding="utf-8"))["payloads"]
payload = copy.deepcopy(golden["type_a_default"])
folder = ROOT / "layout" / "thumbnail"
manifest = json.loads((folder / "settings.json").read_text(
    encoding="utf-8"))["autona"]
template = json.loads((folder / manifest["template"]).read_text(
    encoding="utf-8"))
template["template_id"] = "type_a"
config = dict(payload["config"],
              background_path="./assets/thumbnail_base/default_banner.png")

renderer = ThumbnailRenderer()
got = []
renderer.renderAsync(payload["data"], config, template,
                     lambda pixmap, error: got.append((pixmap, error)))
answer = {"error": "no answer", "colours": 0, "centre": ""}
if wait(lambda: got):
    pixmap, error = got[0]
    answer["error"] = error
    if pixmap is not None:
        image = pixmap.toImage()
        colours = {image.pixelColor(x, y).name()
                   for y in range(0, image.height(), 60)
                   for x in range(0, image.width(), 60)}
        answer["colours"] = len(colours)
        answer["centre"] = image.pixelColor(image.width() // 2,
                                            image.height() // 2).name()
server.shutdown()
print("DRAWN " + json.dumps(answer), flush=True)
