"""The tournament result's off-screen renderer and the real way through it
(ADR-005 S4q).

What result/ResultPageRenderer shares with the thumbnail's is
AutonaOffscreenRenderer's and is pinned by tests/test_thumbnail_render.py: one
render at a time, the order of its waits. Here is what is the result's own: a
layout says its page and its size, and the first picture of a page at another
size is still compared with what the view showed before the load.

These drive a real QWebEngineView (offscreen). Its grab() does not show the
page's pixels (the desktop's to check), so a shipped page is checked by what its
script drew in the document, and the order of the waits by a stand-in for
grab(). As tests/test_thumbnail_render.py, no test depends on how long anything
takes.
"""
import json
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from loguru import logger
from PIL import Image
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWebEngineCore import QWebEngineSettings

from src import AutonaWebOperations as WebOps
from src.core import ResultService as results
from src.core import StateSchema as schema
from src.core.DictHelper import deep_clone
from src.core.PlayerDB import PlayerDB
from src.core.ResultService import ResultService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TaskExecutor import TaskExecutor
from src.result.ResultPageRenderer import ResultPageRenderer
from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer
from tests.conftest import remove_tree
from tests.fixtures import template_packages
from tests.fixtures.result import scenarios as world
from tests.test_api_v1 import client, webApp  # noqa: F401
from tests.test_result_service import _Catalog
from tests.test_thumbnail_render import (
    LONG_TIMEOUT_MS, MARKS_ON_PAGE, WAIT_SECS, _destroy, _evaluate, _frame,
    _waitUntil)

PROJECT_ROOT = Path(__file__).parent.parent

# The shipped layouts' folders (ADR-007 T1: the catalog's result packages).
FOLDERS = template_packages.BUNDLED["result"]


def _size(layout):
    """The shipped layout's picture, as its manifest declares it."""
    return tuple(template_packages.Manifest(FOLDERS[layout])["size"])


RED = "#ff0000"
GREEN = "#00ff00"

PAINTED = "PAINTED "

PAGE = """<!doctype html><html><body><script>
function initResult(payload) {
  window.lastPayload = payload;
  const config = payload.config;
  if (config.hang) return;
  document.title = "%s" + (config.color || "#808080");
  document.title = "RENDER_READY";
}
</script></body></html>
""" % PAINTED


@pytest.fixture
def pages(autonaApp, previewListener):
    """The stand-in page for every layout, in the runtime's layout/, with the
    shipped manifest (the catalog's result packages, ADR-007 T1) at contract
    1: the stand-in is an initResult page."""
    created = template_packages.Install(
        autonaApp.dir / "layout", template_packages.RESULT_FOLDERS, page=PAGE,
        contract=1)
    yield
    for directory in created:
        remove_tree(directory)


@pytest.fixture
def renderer(pages):
    made = ResultPageRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    # The offscreen grab never shows the page's frame mark: each picture
    # would wait all of FRAME_WAIT_MS for nothing.
    made.FRAME_WAIT_MS = 0
    yield made
    _destroy(made)


def _render(made, layout, config=None, data=None):
    answers = []
    made.renderAsync(data or {}, config or {}, layout,
                     lambda pixmap, error: answers.append((pixmap, error)))
    assert _waitUntil(lambda: answers)
    return answers[0]


# ── a layout's page and size ─────────────────────────────────────────────

def test_no_page_is_loaded_before_the_first_render_says_which(renderer):
    assert renderer._template_id is None
    assert renderer._load_number == 0


@pytest.mark.parametrize("layout", world.LAYOUTS)
def test_a_layout_is_drawn_on_its_page_at_its_size(renderer, layout):
    pixmap, error = _render(renderer, layout, {"color": GREEN})

    assert error == ""
    assert (pixmap.width(), pixmap.height()) == _size(layout)
    assert renderer._template_id == layout
    assert (renderer._view.width(), renderer._view.height()) == _size(layout)
    assert f"/layout/{FOLDERS[layout]}/result.html" \
        in renderer._view.url().path()
    assert _evaluate(renderer, "window.lastPayload.layout") == layout


def test_the_size_follows_the_layout_there_and_back(renderer):
    assert _render(renderer, "vertical")[0].width() == 960
    assert _render(renderer, "horizontal")[0].width() == 1920
    assert _render(renderer, "vertical_geki")[0].width() == 960


def test_a_render_on_the_same_layout_does_not_load_again(renderer):
    _render(renderer, "team", {"color": RED})
    loads = renderer._load_number

    _render(renderer, "team", {"color": GREEN})

    assert renderer._load_number == loads
    assert _evaluate(renderer, "window.lastPayload.config.color") == GREEN


def test_a_layout_it_does_not_know_is_drawn_on_the_default_page(renderer):
    pixmap, error = _render(renderer, "gone")

    assert error == ""
    assert pixmap.width() == 1920
    assert renderer._template_id == results.DEFAULT_LAYOUT


def test_the_view_is_as_the_old_renderer_had_it(renderer):
    # The thumbnail's local file access and transparent background are its own.
    settings = renderer._view.settings()
    attribute = QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls
    assert not settings.testAttribute(attribute)
    assert renderer._view.page().backgroundColor() \
        != QColor(Qt.GlobalColor.transparent)
    assert ResultPageRenderer.ASPECT_MODE == Qt.AspectRatioMode.KeepAspectRatio
    assert ThumbnailRenderer.ASPECT_MODE == Qt.AspectRatioMode.IgnoreAspectRatio


def test_the_frame_mark_is_looked_for_at_the_pages_size_and_scale(pages):
    """A page of another size than the one before (vertical, 960 x 1080),
    grabbed in device pixels (a screen at 200 %): its frame mark is found in
    its corner, and the picture is the template's size."""
    made = ResultPageRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    made.FRAME_WAIT_MS = LONG_TIMEOUT_MS
    shown = {"color": RED, "mark": None}
    grabs = []

    def grab(rect=None):
        size = (made._view.width(), made._view.height())
        grabs.append((size, shown["color"], shown["mark"]))
        return _frame(*size, shown["color"], shown["mark"], scale=2,
                      rect=rect)

    made._view.grab = grab
    answers = []
    try:
        made.renderAsync({}, {}, "vertical",
                         lambda pixmap, error: answers.append((pixmap, error)))
        assert _waitUntil(lambda: grabs)
        number = made._active.number
        assert grabs[0][0] == (960, 1080)
        _waitUntil(lambda: False, timeout=0.3)
        assert answers == []

        shown.update(color=GREEN, mark=number)
        assert _waitUntil(
            lambda: json.loads(_evaluate(made, MARKS_ON_PAGE)) == [])
        shown["mark"] = None
        assert _waitUntil(lambda: answers)
        pixmap, error = answers[0]
        assert error == ""
        assert (pixmap.width(), pixmap.height()) == (960, 1080)
        assert pixmap.toImage().pixelColor(480, 540).name() == GREEN
    finally:
        _destroy(made)


# ── a render that does not end ───────────────────────────────────────────

def test_a_hung_render_fails_and_the_next_one_works(renderer):
    assert _render(renderer, "horizontal")[1] == ""
    renderer.STAGE_TIMEOUT_MS = 500

    assert _render(renderer, "horizontal", {"hang": True}) \
        == (None, "the render timed out")

    # Time enough from here on.
    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    pixmap, error = _render(renderer, "vertical", {"color": GREEN})
    assert error == ""
    assert pixmap.width() == 960


def test_a_page_that_never_loads_fails_its_render_and_the_next_one_tries(pages):
    made = ResultPageRenderer()
    made.STAGE_TIMEOUT_MS = 500
    try:
        # Listed when the renderer was made: its page is looked for, and is
        # not there.
        (Path("layout") / FOLDERS["team"] / "result.html").unlink()

        assert _render(made, "team") == (None, "the render timed out")

        made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
        (Path("layout") / FOLDERS["team"] / "result.html") \
            .write_text(PAGE, encoding="utf-8")
        made.FRAME_WAIT_MS = 0
        assert _render(made, "team")[1] == ""
    finally:
        _destroy(made)


# ── the shell ────────────────────────────────────────────────────────────

def test_the_shell_draws_a_result_on_the_results_renderer(renderer, monkeypatch):
    from src.AutonaShellOperations import AutonaShellOperations

    monkeypatch.setattr(ResultPageRenderer, "_instance", renderer)
    answers = []

    AutonaShellOperations.instance.StartImageRender(
        "result", ({}, {"color": GREEN}, "vertical"),
        lambda encode, error: answers.append((encode, error)))

    assert _waitUntil(lambda: answers)
    encode, error = answers[0]
    assert error == ""
    assert Image.open(BytesIO(encode("PNG"))).size == (960, 1080)
    assert renderer._template_id == "vertical"


def test_the_shell_hands_on_a_failed_result(renderer, monkeypatch):
    from src.AutonaShellOperations import AutonaShellOperations

    monkeypatch.setattr(ResultPageRenderer, "_instance", renderer)
    answers = []
    assert _render(renderer, "horizontal")[1] == ""
    # After the page's load, which on a busy machine takes longer than this.
    renderer.STAGE_TIMEOUT_MS = 500

    AutonaShellOperations.instance.StartImageRender(
        "result", ({}, {"hang": True}, "horizontal"),
        lambda encode, error: answers.append((encode, error)))

    assert _waitUntil(lambda: answers)
    assert answers == [(None, "the render timed out")]


# ── the shipped pages ────────────────────────────────────────────────────

@pytest.fixture
def shippedPages(autonaApp, previewListener):
    """The templates as shipped in the runtime's layout/. Copies, as in
    tests/test_thumbnail_render.py: layout/include/ is 19 MB, of which the pages
    only want what they share (ADR-007 D2: a package reads nothing outside its
    folder but include/)."""
    created = []
    layout = autonaApp.dir / "layout"
    for name in FOLDERS.values():
        directory = layout / name
        assert not directory.exists()
        shutil.copytree(PROJECT_ROOT / "layout" / name, directory)
        created.append(directory)
    include = layout / "include"
    assert not include.exists()
    include.mkdir()
    created.append(include)
    for file in ("autona.js", "jquery-3.7.1.min.js", "lodash.min.js",
                 "thumbnail_assets.js", "result.js", "result.css",
                 "result_team.css"):
        shutil.copy(PROJECT_ROOT / "layout" / "include" / file, include)
    try:
        yield
    finally:
        for directory in created:
            remove_tree(directory)


@pytest.fixture
def shipped(shippedPages):
    made = ResultPageRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    made.FRAME_WAIT_MS = 0
    yield made
    _destroy(made)


AWKWARD = "Al pha\ud800\"</script>${b}`'"
CHARACTER = {"assets": {"full": {"asset": "./missing/full.png"}},
             "codename": "mario", "name": "Mario", "en_name": "Mario"}


def _awkwardData(layout):
    if layout in world.TEAM_LAYOUTS:
        rows = [{"rank": 1, "name": AWKWARD, "sponsor": "", "character": {},
                 "characters": [],
                 "members": [{"name": AWKWARD, "character": CHARACTER},
                             {"name": "B1", "character": {}}]}]
    else:
        rows = [{"rank": 1, "name": AWKWARD, "sponsor": AWKWARD,
                 "character": CHARACTER, "characters": [CHARACTER, CHARACTER],
                 "members": []}]
    return {"tournamentName": AWKWARD, "eventName": "Top 8", "startAt": None,
            "numEntrants": 0, "players": rows}


def _config():
    return {"main_icon_path": "./layout/logo.png",
            "background_path": schema.DEFAULT_BANNER_PATH,
            "theme_color": "#111111", "accent_color": "#222222"}


def _scriptErrors():
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="ERROR",
                      filter=lambda record: "initResult" in record["message"])
    return lines, sink


@pytest.mark.parametrize("layout", world.LAYOUTS)
def test_a_shipped_page_draws_what_it_is_given(shipped, layout):
    """The real result.js, on a payload with what is awkward in it. Not its
    pixels, which the offscreen platform does not show (tests/
    test_render_platform.py compares them). Drawn in Autona.onRender
    (template contract 2, ADR-008 D7 R4): an exception would be the render's
    error."""
    errors, sink = _scriptErrors()
    try:
        pixmap, error = _render(shipped, layout, _config(),
                                _awkwardData(layout))
        text = _evaluate(shipped, "document.body.textContent")
    finally:
        logger.remove(sink)

    assert error == ""
    assert (pixmap.width(), pixmap.height()) == _size(layout)
    assert errors == []
    # Twice at least: in the header, and in the row. As text: none of it ran.
    assert text.count("\"</script>${b}`'") >= 2
    # No script of the payload's: the inline ones are the page's own line
    # that registers drawResult (none on geki, whose result.js does).
    inline = json.loads(_evaluate(shipped, "JSON.stringify([...document.scripts]"
                                  ".filter((s) => !s.src)"
                                  ".map((s) => s.textContent.trim()))"))
    assert inline == ([] if layout == "vertical_geki"
                      else ["Autona.onRender(drawResult);"])


def test_the_shared_script_registers_nothing():
    """include/result.js only defines drawResult (and initResult): a page that
    reuses it registers its own function, and Autona.onRender takes one."""
    shared = (PROJECT_ROOT / "layout" / "include" / "result.js").read_text(
        encoding="utf-8")
    assert "async function drawResult(" in shared
    assert "async function initResult(" in shared
    code = [line for line in shared.splitlines()
            if not line.lstrip().startswith(("*", "/*", "//"))]
    assert not any("Autona.onRender(" in line for line in code)


@pytest.mark.parametrize("layout", world.LAYOUTS)
def test_a_shipped_page_draws_the_sample(shipped, layout, autonaApp):
    """The sample of a game of the scenarios', through the service's payload."""
    service = ResultService(later=lambda _fn: None)
    errors, sink = _scriptErrors()
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        data, config, _layout = service.BuildPayload(
            preview=True, games=_Catalog(), layout=layout)
        _pixmap, error = _render(shipped, layout, config, data)
        text = _evaluate(shipped, "document.body.textContent")
    finally:
        SettingsManager.settings = settings
        logger.remove(sink)

    assert error == ""
    assert errors == []
    assert "Sample Tournament" in text
    assert ("Team 1" if layout in world.TEAM_LAYOUTS else "Mario") in text


def test_a_teams_package_not_named_team_is_drawn_as_teams(
        shipped, autonaApp):
    """A package placed by hand that reuses the shipped team design: its id
    is its folder's name, which does not start with "team"; the payload's
    data.rows ("teams", its manifest's) is what makes include/result.js draw
    the members (ADR-007 T1)."""
    folder = autonaApp.dir / "layout" / "crew_board"
    shutil.copytree(PROJECT_ROOT / "layout" / FOLDERS["team"], folder)
    service = ResultService(later=lambda _fn: None)
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        data, config, layout = service.BuildPayload(
            preview=True, games=_Catalog(), layout="crew_board")
        assert (layout, data["rows"]) == ("crew_board", "teams")
        _pixmap, error = _render(shipped, "crew_board", config, data)
        members = _evaluate(
            shipped, "document.querySelectorAll('.member-cell').length")
    finally:
        SettingsManager.settings = settings
        remove_tree(folder)

    assert error == ""
    assert members > 0
    assert shipped._view.url().path().endswith("/layout/crew_board/result.html")


# ── a package placed by hand, and a manifest changed meanwhile ───────────

def _placeLayout(root, name, size, rows="players", entry="index.html"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / entry).write_text(PAGE, encoding="utf-8")
    (directory / "settings.json").write_text(json.dumps({"autona": {
        "kind": "result", "contract": 1, "size": list(size), "rows": rows,
        "entry": entry}}), encoding="utf-8")
    return directory


def test_a_package_placed_by_hand_is_drawn_from_its_folder_at_its_size(
        renderer, autonaApp):
    directory = _placeLayout(autonaApp.dir / "layout", "neon_board",
                             (1280, 720))
    try:
        pixmap, error = _render(renderer, "neon_board", {"color": GREEN})

        assert error == ""
        assert renderer._template_id == "neon_board"
        assert (pixmap.width(), pixmap.height()) == (1280, 720)
        assert (renderer._view.width(), renderer._view.height()) \
            == (1280, 720)
        # The preview listener's (ADR-007 D4), from its folder.
        assert renderer._view.url().path() == "/layout/neon_board/index.html"
    finally:
        remove_tree(directory)


def test_a_size_changed_in_the_manifest_is_drawn_at_not_stretched_to(
        renderer, autonaApp):
    assert _render(renderer, "horizontal", {"color": GREEN})[1] == ""
    loads = renderer._load_number
    manifest = autonaApp.dir / "layout" / FOLDERS["horizontal"] \
        / "settings.json"
    block = json.loads(manifest.read_text(encoding="utf-8"))
    block["autona"]["size"] = [1000, 1000]
    manifest.write_text(json.dumps(block), encoding="utf-8")

    pixmap, error = _render(renderer, "horizontal", {"color": GREEN})

    assert error == ""
    # The same page, at the new size: the view was resized, not the picture
    # scaled (KeepAspectRatio would have made it 1000 x 562).
    assert renderer._load_number == loads
    assert (renderer._view.width(), renderer._view.height()) == (1000, 1000)
    assert (pixmap.width(), pixmap.height()) == (1000, 1000)


def _writeImage(path, size):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (0, 200, 0)).save(path, "PNG")


def test_a_replaced_banner_is_drawn_when_its_url_says_so(shipped, autonaApp):
    """The banner keeps its path when it is replaced, and the page is not loaded
    again between two renders of a layout: an image of the same URL in the same
    document is the one it has. The service's URL has the banner's version in
    it (results._Background); the file's URL ignores it. The template has no
    cache buster of its own, and templates stay as they are."""
    banner = autonaApp.dir / schema.TOURNAMENT_BANNER_PATH
    existed = banner.exists()
    kept = banner.read_bytes() if existed else None
    config = dict(_config(), background_path=schema.TOURNAMENT_BANNER_PATH)
    width = "document.querySelector('.background img').naturalWidth"
    try:
        _writeImage(banner, (64, 32))
        assert _render(shipped, "horizontal", config)[1] == ""
        assert _evaluate(shipped, width) == 64

        # The file changes under the same path. The version is what tells them
        # apart, and only a new URL is certain to read the new file.
        _writeImage(banner, (128, 64))
        assert _render(shipped, "horizontal", dict(
            config, background_path=f"{schema.TOURNAMENT_BANNER_PATH}?v=2"))[1] \
            == ""
        assert _evaluate(shipped, width) == 128
    finally:
        if existed:
            banner.write_bytes(kept)
        else:
            banner.unlink(missing_ok=True)


# ── the real way through ─────────────────────────────────────────────────

@pytest.fixture
def service(autonaApp, pages, monkeypatch):
    """The session's service and shell registration, with a renderer of the
    test's own (the singleton would outlive its stand-in page), the saved state
    and the output kept out of each other's way."""
    from src.AutonaShellOperations import AutonaShellOperations
    assert WebOps.startImageRender == AutonaShellOperations.instance\
        .StartImageRender

    made = ResultPageRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    made.FRAME_WAIT_MS = 0
    monkeypatch.setattr(ResultPageRenderer, "_instance", made)
    given = []
    monkeypatch.setattr(PlayerDB.instance, "AddPlayers",
                        lambda players, overwrite=False: given.append(players))
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
    try:
        yield ResultService.instance
    finally:
        assert TaskExecutor.instance.drain(WAIT_SECS)
        # What a test left running ends before its page goes.
        _waitUntil(lambda: ResultService.instance._running is None
                   and ResultService.instance._next is None)
        _destroy(made)
        SettingsManager.settings = settings
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved
        remove_tree(autonaApp.dir / "out" / "results")
        Path(results.PREVIEW_PATH).unlink(missing_ok=True)


def _rendered(revision):
    render = SM.Get(schema.RESULT_RENDER_ROOT) or {}
    return render.get("revision", 0) >= revision \
        and render["status"] not in ("idle", "running")


def test_a_preview_through_the_route_ends_in_a_png(service, client):
    response = client.post("/api/v1/result:preview", json={})

    assert response.status_code == 202, response.get_data(as_text=True)
    revision = response.get_json()["data"]["renderRevision"]
    assert _waitUntil(lambda: _rendered(revision))
    assert SM.Get(schema.RESULT_RENDER_ROOT)["status"] == "done", \
        SM.Get(schema.RESULT_RENDER_ROOT)

    image = client.get(f"/api/v1/result/preview?v={revision}")
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    picture = Image.open(BytesIO(image.get_data()))
    assert picture.format == "PNG"
    assert picture.size == (1920, 1080)


@pytest.mark.parametrize("layout", world.LAYOUTS)
def test_every_layouts_preview_is_its_size(service, client, layout):
    assert client.patch("/api/v1/result", json={"layout": layout}) \
        .status_code == 200

    response = client.post("/api/v1/result:preview", json={})
    revision = response.get_json()["data"]["renderRevision"]

    assert _waitUntil(lambda: _rendered(revision))
    image = client.get(f"/api/v1/result/preview?v={revision}")
    assert Image.open(BytesIO(image.get_data())).size \
        == _size(layout)


def test_a_generate_through_the_route_saves_a_png(service, client, autonaApp):
    assert client.patch("/api/v1/result/players/1",
                        json={"name": "Alpha"}).status_code == 200
    SM.Set("tournamentInfo.tournamentName", "Big Cup")

    response = client.post("/api/v1/result:generate", json={})

    assert response.status_code == 202, response.get_data(as_text=True)
    revision = response.get_json()["data"]["renderRevision"]
    assert _waitUntil(lambda: _rendered(revision))
    render = SM.Get(schema.RESULT_RENDER_ROOT)
    assert render["status"] == "done", render
    assert render["path"].startswith("out/results/Big Cup-top8-")
    png = autonaApp.dir / render["path"]
    assert Image.open(png).format == "PNG"
    assert Image.open(png).size == (1920, 1080)
    assert not png.with_suffix(".jpg").exists()
