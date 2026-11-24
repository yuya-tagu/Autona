"""Draw a thumbnail made from the layout kit (ADR-008 D7 R3) as it is, on
template contract 2, and say what each picture is.

Run by tests/test_render_platform.py in a process of its own (the tests'
process is on Qt's offscreen platform): python <this> <project root>, in a
working directory of the caller's (a tmp one), which becomes the app's
directory: what the preview listener serves and the catalog lists. Copied
there (no links: a cleanup that goes wrong would delete the project's): the
kit, autona.js, the font the kit's sample uses and the images its sample
payload names. LayoutKit.NewDesign then makes layout/thumbnail_kit/ from the
kit, as "make a new design" does. The startup is the app's
(draw_contracts.py's). Drawn: the kit's sample payload, the teams sample
state with the same config, and an empty state. Prints one JSON line:
{"errors", "colours", "differing"} -- each picture's error and colour count
(a 60 px grid), and how many pixels of a 4 px grid differ between the sample
and the empty state's picture.
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

shutil.copytree(ROOT / "layout" / "include" / "kit",
                APP / "layout" / "include" / "kit")
shutil.copy(ROOT / "layout" / "include" / "autona.js",
            APP / "layout" / "include" / "autona.js")
for relative in ("assets/font/LINESeedJP", "assets/thumbnail_base",
                 "assets/mock_data/mock_asset"):
    shutil.copytree(ROOT / relative, APP / relative)
for relative in ("assets/country_flag/jp.png", "assets/country_flag/us.png",
                 "assets/state_flag/JP/13.png", "assets/state_flag/US/CA.png"):
    (APP / relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / relative, APP / relative)

from src.core import LayoutKit  # noqa: E402

entry = LayoutKit.NewDesign("thumbnail", "kit", root=APP / "layout")
assert entry["id"] == "thumbnail_kit" and entry["contract"] == 2, entry

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


FOLDER = APP / "layout" / "thumbnail_kit"
sample = json.loads((FOLDER / "sample_payload.json").read_text(
    encoding="utf-8"))
teams = json.loads((ROOT / "layout" / "include" / "sample" / "teams.json")
                   .read_text(encoding="utf-8"))
template = dict(json.loads((FOLDER / "template.json").read_text(
    encoding="utf-8")), template_id="thumbnail_kit")
payloads = [
    (sample["data"], sample["config"]),
    (teams, sample["config"]),
    ({}, sample["config"]),
]

renderer = ThumbnailRenderer()
images = []
errors = []
for data, config in payloads:
    got = []
    renderer.renderAsync(copy.deepcopy(data), copy.deepcopy(config),
                         dict(template),
                         lambda pixmap, error: got.append((pixmap, error)))
    if not wait(lambda: got):
        errors.append("no answer")
        images.append(None)
        continue
    pixmap, error = got[0]
    errors.append(str(error))
    images.append(pixmap.toImage() if pixmap is not None else None)
server.shutdown()

answer = {"errors": errors, "colours": [], "differing": -1}
for image in images:
    answer["colours"].append(len({
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), 60)
        for x in range(0, image.width(), 60)}) if image is not None else 0)
first, last = images[0], images[-1]
if first is not None and last is not None and first.size() == last.size():
    answer["differing"] = sum(
        first.pixel(x, y) != last.pixel(x, y)
        for y in range(0, first.height(), 4)
        for x in range(0, first.width(), 4))
print("DRAWN " + json.dumps(answer), flush=True)
