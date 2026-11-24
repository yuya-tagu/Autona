"""Draw the shipped result layouts on template contract 2 and the code they
had before it on contract 1, and say how each pair of pictures compares.

Run by tests/test_render_platform.py in a process of its own (the tests'
process is on Qt's offscreen platform): python <this> <project root>, in a
working directory of the caller's (a tmp one), which becomes the app's
directory: what the preview listener serves and the catalog lists. Copied
there (no links: a cleanup that goes wrong would delete the project's): the
six shipped result layouts, the include/ files they load, the logo, the
fonts, the default banner and the mock characters. Beside each, a copy as it
was before ADR-008 D7 R4 (<folder>_c1, contract 1 in its manifest): its page
without autona.js and without the line that registers drawResult, and the
scripts of then -- frozen copies in contract1/ beside this file of
layout/include/result.js (as include/result_c1.js) and of
layout/result_vertical_geki/result.js (the copy's own result.js), as they
were at 3c9dc273. The pages, the styles and the rest of include/ are what
they were then (R4 only added those two lines to the pages).

The startup is the app's (tests/fixtures/thumbnail/draw_on_platform.py's).
Prints one JSON line: {"errors", "colours", "sampled", "differing"} -- each
picture's error and colour count (a 60 px grid), in the order (shipped, copy)
per layout, and per pair how many pixels of a 4 px grid there are and how
many differ by more than the GPU's rounding (ROUNDING levels a channel).
"""
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
APP = Path.cwd().resolve()
FROZEN = Path(__file__).resolve().parent / "contract1"
sys.path.insert(0, str(ROOT))
os.environ["QT_API"] = "pyside6"

# id -> folder, as core/LayoutCatalog.BUNDLED_TEMPLATES has them.
SHIPPED = {"horizontal": "result", "horizontal_16": "result_top16",
           "vertical": "result_vertical",
           "vertical_geki": "result_vertical_geki",
           "team": "result_team", "team_split": "result_team_split"}

for relative in ["layout/" + folder for folder in SHIPPED.values()] + [
        "assets/font/LINESeedJP", "assets/thumbnail_base",
        "assets/mock_data/mock_asset"]:
    shutil.copytree(ROOT / relative, APP / relative)
shutil.copy(ROOT / "layout" / "logo.png", APP / "layout" / "logo.png")
(APP / "layout" / "include").mkdir()
for name in ("jquery-3.7.1.min.js", "lodash.min.js", "thumbnail_assets.js",
             "autona.js", "result.js", "result.css", "result_team.css"):
    shutil.copy(ROOT / "layout" / "include" / name,
                APP / "layout" / "include" / name)
shutil.copy(FROZEN / "result.js", APP / "layout" / "include" / "result_c1.js")

AUTONA = '<script src="../include/autona.js"></script>'
REGISTER = "<script>Autona.onRender(drawResult);</script>"
SHARED = '<script src="../include/result.js"></script>'


def contract_1(folder):
    """layout/<folder>_c1/: layout/<folder>/ as it was before R4."""
    source = APP / "layout" / folder
    target = APP / "layout" / (folder + "_c1")
    shutil.copytree(source, target)
    settings = json.loads((source / "settings.json")
                          .read_text(encoding="utf-8"))
    assert settings["autona"]["contract"] == 2
    settings["autona"]["contract"] = 1
    (target / "settings.json").write_text(json.dumps(settings),
                                          encoding="utf-8")
    page = (source / "result.html").read_text(encoding="utf-8")
    lines = [line for line in page.splitlines()
             if line.strip() not in (AUTONA, REGISTER)]
    assert len(lines) == len(page.splitlines()) - (
        1 if folder == "result_vertical_geki" else 2), folder
    page = "\n".join(lines) + "\n"
    if folder == "result_vertical_geki":
        shutil.copy(FROZEN / "result_vertical_geki.js", target / "result.js")
    else:
        assert page.count(SHARED) == 1, folder
        page = page.replace(SHARED,
                            '<script src="../include/result_c1.js"></script>')
    (target / "result.html").write_text(page, encoding="utf-8")


for folder in SHIPPED.values():
    contract_1(folder)

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

from src.result.ResultPageRenderer import ResultPageRenderer  # noqa: E402


def wait(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


def character(number):
    return {"name": f"Fighter {number}", "en_name": f"Fighter {number}",
            "codename": f"fighter_{number}", "assets": {"full": {
                "asset": f"assets/mock_data/mock_asset/"
                         f"full_character_{number % 4}.svg"}}}


# Rows as the service gives them (core/ResultService.BuildPayload): ties at
# 5th and 9th, a name long enough to be squeezed, a second fighter now and
# then, empty rows at the end.
ROWS = [(1, "Alpha", "TEAM"), (2, "Bravo", ""), (3, "Charlie", "SP"),
        (4, "Delta", ""), (5, "Echo with a very long name indeed", ""),
        (5, "Foxtrot", ""), (7, "Golf", ""), (8, "Hotel", ""),
        (9, "India", ""), (9, "Juliett", "JP"), (11, "Kilo", ""),
        (12, "Lima", ""), (13, "Mike", ""), (13, "November", "")]


def player(i, rank, name, sponsor):
    characters = [character(i), character(i + 1)] if i % 3 == 0 else [
        character(i)]
    return {"rank": rank, "name": name, "sponsor": sponsor,
            "character": characters[0], "characters": characters,
            "members": []}


def empty(rank):
    return {"rank": rank, "name": "", "sponsor": "", "character": {},
            "characters": [], "members": []}


PLAYERS = [player(i, *row) for i, row in enumerate(ROWS)]
EIGHT = PLAYERS[:7] + [empty(8)]
SIXTEEN = PLAYERS + [empty(15), empty(16)]
TEAMS = [{"rank": rank, "name": f"Team {rank}", "sponsor": "",
          "character": {}, "characters": [], "members": [
              {"name": f"T{rank}M{n}", "character": character(rank + n)}
              for n in range(count)]}
         for rank, count in zip((1, 2, 3, 3, 5, 5, 7, 8),
                                (3, 3, 2, 5, 1, 3, 0, 4))]
HEADER = {"tournamentName": "Sample Tournament", "eventName": "Top 8",
          "startAt": "2026/11/24", "numEntrants": 128}
CONFIG = {"main_icon_path": "./layout/logo.png",
          "background_path": "./assets/thumbnail_base/default_banner.png",
          "theme_color": "#1a1a2e", "accent_color": "#e94560"}

ROWS_OF = {"horizontal": ("players", EIGHT),
           "horizontal_16": ("players", SIXTEEN),
           "vertical": ("players", EIGHT),
           "vertical_geki": ("players", EIGHT),
           "team": ("teams", TEAMS), "team_split": ("teams", TEAMS)}
PAIRS = [(layout, SHIPPED[layout] + "_c1") + ROWS_OF[layout]
         for layout in SHIPPED]

renderer = ResultPageRenderer()
images = []
errors = []
for shipped, copy, rows, players in PAIRS:
    data = dict(HEADER, rows=rows, players=players)
    for layout in (shipped, copy):
        got = []
        renderer.renderAsync(data, CONFIG, layout,
                             lambda pixmap, error: got.append((pixmap, error)))
        if not wait(lambda: got):
            errors.append("no answer")
            images.append(None)
            continue
        pixmap, error = got[0]
        errors.append(str(error))
        images.append(pixmap.toImage() if pixmap is not None else None)
server.shutdown()

# The blurred background and the gradients come out one level apart between
# two loads of the same page; a different drawing is more than that.
ROUNDING = 2
answer = {"errors": errors, "colours": [], "sampled": [], "differing": []}
for image in images:
    answer["colours"].append(len({
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), 60)
        for x in range(0, image.width(), 60)}) if image is not None else 0)
for first, second in zip(images[0::2], images[1::2]):
    sampled, differing = 0, -1
    if first is not None and second is not None \
            and first.size() == second.size():
        differing = 0
        for y in range(0, first.height(), 4):
            for x in range(0, first.width(), 4):
                sampled += 1
                one, two = first.pixelColor(x, y), second.pixelColor(x, y)
                if max(abs(one.red() - two.red()),
                       abs(one.green() - two.green()),
                       abs(one.blue() - two.blue())) > ROUNDING:
                    differing += 1
    answer["sampled"].append(sampled)
    answer["differing"].append(differing)
print("DRAWN " + json.dumps(answer), flush=True)
