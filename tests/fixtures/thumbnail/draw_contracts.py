"""Draw the shipped type_a thumbnail on template contract 2 and the same
drawing on contract 1, and say how the two pictures compare.

Run by tests/test_render_platform.py in a process of its own (the tests'
process is on Qt's offscreen platform): python <this> <project root>, in a
working directory of the caller's (a tmp one), which becomes the app's
directory: what the preview listener serves and the catalog lists. Copied
there (no links: a cleanup that goes wrong would delete the project's): the
shipped layout/thumbnail/, the four include/ scripts it loads, its fonts and
its default images. Beside it, layout/thumbnail_c1/: the same page on
contract 1 -- autona.js replaced by a stand-in that keeps the function given
to Autona.onRender, and an initThumbnail that calls it and says
RENDER_READY, as the template did before ADR-008 D7 R2. The startup is the
app's (draw_on_platform.py's). Prints one JSON line: {"errors", "colours",
"sampled", "differing"} -- each picture's error and colour count (a 60 px
grid), and how many pixels of a 4 px grid differ between the two.
"""
import copy
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
APP = Path.cwd().resolve()
sys.path.insert(0, str(ROOT))
os.environ["QT_API"] = "pyside6"

for relative in ("layout/thumbnail", "assets/font/LINESeedJP",
                 "assets/thumbnail_base"):
    shutil.copytree(ROOT / relative, APP / relative)
(APP / "layout" / "include").mkdir()
for script in ("jquery-3.7.1.min.js", "lodash.min.js", "thumbnail_assets.js",
               "autona.js"):
    shutil.copy(ROOT / "layout" / "include" / script,
                APP / "layout" / "include" / script)

SHIPPED = APP / "layout" / "thumbnail"
CONTRACT_1 = APP / "layout" / "thumbnail_c1"
CONTRACT_1.mkdir()
for name in ("thumbnail.js", "thumbnail.css", "type_a.json"):
    shutil.copy(SHIPPED / name, CONTRACT_1 / name)
settings = json.loads((SHIPPED / "settings.json").read_text(encoding="utf-8"))
assert settings["autona"]["contract"] == 2
settings["autona"]["contract"] = 1
(CONTRACT_1 / "settings.json").write_text(json.dumps(settings),
                                          encoding="utf-8")
page = (SHIPPED / "thumbnail.html").read_text(encoding="utf-8")
autona = '<script src="../include/autona.js"></script>'
own = '<script src="./thumbnail.js"></script>'
assert autona in page and own in page
page = page.replace(autona, "<script>window.Autona = {onRender: "
                    "function (draw) { window.__draw = draw; }};</script>")
page = page.replace(own, own + "\n<script>async function initThumbnail(p) {"
                    " try { await window.__draw(p); }"
                    ' finally { document.title = "RENDER_READY"; } }'
                    "</script>")
(CONTRACT_1 / "thumbnail.html").write_text(page, encoding="utf-8")

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

LayoutCatalog.SetAppDirectory(lambda: str(APP))
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
template = json.loads((SHIPPED / "type_a.json").read_text(encoding="utf-8"))
config = dict(payload["config"],
              background_path="./assets/thumbnail_base/default_banner.png")

renderer = ThumbnailRenderer()
images = []
errors = []
for templateId in ("type_a", "thumbnail_c1"):
    got = []
    renderer.renderAsync(payload["data"], config,
                         dict(template, template_id=templateId),
                         lambda pixmap, error: got.append((pixmap, error)))
    if not wait(lambda: got):
        errors.append("no answer")
        images.append(None)
        continue
    pixmap, error = got[0]
    errors.append(str(error))
    images.append(pixmap.toImage() if pixmap is not None else None)
server.shutdown()

answer = {"errors": errors, "colours": [], "sampled": 0, "differing": -1}
for image in images:
    answer["colours"].append(len({
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), 60)
        for x in range(0, image.width(), 60)}) if image is not None else 0)
if all(image is not None for image in images):
    first, second = images
    if first.size() == second.size():
        sampled = differing = 0
        for y in range(0, first.height(), 4):
            for x in range(0, first.width(), 4):
                sampled += 1
                if first.pixel(x, y) != second.pixel(x, y):
                    differing += 1
        answer["sampled"], answer["differing"] = sampled, differing
print("DRAWN " + json.dumps(answer), flush=True)
