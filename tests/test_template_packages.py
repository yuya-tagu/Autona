"""Template packages (ADR-007 T1): the result's layouts and the thumbnail's
templates found under layout/ (src/core/LayoutCatalog.py).

Pinned here:

- the shipped eight, frozen: their ids, folders, sizes, rows / template
  files, the defaults and which draw teams, all as the hard-coded tables of
  core/ResultService, core/ThumbnailService and the renderers had them
  before (the copy below), and the ids settings.json and the state saved;
- the shipped manifests themselves, and the names the catalog gives the
  shipped packages (core's words, never the manifest's);
- discovery of a package placed by hand: its id is its folder's name, its
  name its manifest's displayName, listed after the shipped ones;
- a manifest that does not hold what the contract needs costs its own entry,
  with a warning, and never stops the rest; a folder named like a shipped id
  of its kind is not listed (the shipped one wins); no folder-name fallback;
- a package reads nothing outside its folder but layout/include/ (and the
  app's assets/ and the payload's paths, through ../../), and a thumbnail's
  template file points its images into assets/;
- the one folder every reader reads (LayoutCatalog.Root: layout/ under the
  app's directory), and that the isolated launch the smoke test and the
  browser E2E use sees the shipped eight.

What the services make of the catalog (a hand-placed package drawn, a gone
one drawn on the default) is pinned in tests/test_result_service.py and
tests/test_thumbnail_service.py; the payloads' goldens there are unchanged.
"""
import json
import os
import re
from pathlib import Path

import pytest
from loguru import logger

from src.core import CoreWords
from src.core import LayoutCatalog
from src.core import ResultService as results
from src.core import ThumbnailService as thumbnails
from src.core.LayoutCatalog import KIND_RESULT, KIND_THUMBNAIL
from tests.fixtures import template_packages
from tests.test_bootstrap_split import (
    _PRELUDE, SHIPPED_TEMPLATE_LINKS, _probe)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_ROOT = PROJECT_ROOT / "layout"

# What the removed tables said, spelled out: ResultService.LAYOUT_SUBDIRS /
# LAYOUT_SIZES / TEAM_LAYOUTS / SlotsFor, ThumbnailService.TEMPLATE_IDS and
# ThumbnailRenderer.TEMPLATE_SUBDIRS (the view's 1920x1080), and the template
# files' names (assets/thumbnail_base/thumbnail_types/<id>.json until T1).
# id -> (folder, (width, height), drawn rows, team).
RESULT_LAYOUTS = {
    "horizontal": ("result", (1920, 1080), 8, False),
    "horizontal_16": ("result_top16", (1920, 1080), 16, False),
    "vertical": ("result_vertical", (960, 1080), 8, False),
    "vertical_geki": ("result_vertical_geki", (960, 1080), 8, False),
    "team": ("result_team", (1920, 1080), 8, True),
    "team_split": ("result_team_split", (1920, 1080), 8, True),
}
# id -> (folder, (width, height), template file).
THUMBNAIL_TEMPLATES = {
    "type_a": ("thumbnail", (1920, 1080), "type_a.json"),
    "type_b": ("thumbnail_type_b", (1920, 1080), "type_b.json"),
}


@pytest.fixture
def layoutRoot(tmp_path):
    root = tmp_path / "layout"
    root.mkdir()
    return root


@pytest.fixture
def warnings():
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def package(root, name, autona, entry=None, files=()):
    """A template package folder: its settings.json, its entry page (the
    kind's default unless named; only a plain file name is written, never a
    path a manifest names to be refused) and `files`."""
    directory = root / name
    directory.mkdir(parents=True)
    if isinstance(autona, dict):
        kind = autona.get("kind")
        page = entry or autona.get("entry") \
            or LayoutCatalog.TEMPLATE_DEFAULT_ENTRY.get(kind, "index.html")
        if isinstance(page, str) and re.fullmatch(r"[\w-]+(\.[\w-]+)*", page):
            (directory / page).write_text("<html></html>", encoding="utf-8")
    for file in files:
        (directory / file).write_text("{}", encoding="utf-8")
    (directory / "settings.json").write_text(
        json.dumps({"autona": autona}, ensure_ascii=False), encoding="utf-8")
    return directory


def result_block(**changes):
    return dict({"kind": "result", "contract": 1, "size": [1920, 1080],
                 "rows": "players"}, **changes)


def thumbnail_block(**changes):
    return dict({"kind": "thumbnail", "contract": 1, "size": [1920, 1080],
                 "template": "layout.json"}, **changes)


# ── the shipped eight, frozen ────────────────────────────────────────────

def test_the_frozen_ids_are_the_ones_always_saved():
    assert {kind: dict(ids) for kind, ids
            in LayoutCatalog.BUNDLED_TEMPLATES.items()} == {
        KIND_RESULT: {templateId: folder for templateId, (folder, *_)
                      in RESULT_LAYOUTS.items()},
        KIND_THUMBNAIL: {templateId: folder for templateId, (folder, *_)
                         in THUMBNAIL_TEMPLATES.items()},
    }
    # The tests' own copy (tests/fixtures/template_packages.py) agrees.
    assert template_packages.BUNDLED == LayoutCatalog.BUNDLED_TEMPLATES
    assert results.DEFAULT_LAYOUT == "horizontal"
    assert results.DEFAULT_TEAM_LAYOUT == "team"
    assert thumbnails.DEFAULT_TEMPLATE_ID == "type_a"


def test_the_shipped_result_layouts_are_as_they_were():
    entries = LayoutCatalog.Templates(KIND_RESULT, SHIPPED_ROOT)
    shipped = {templateId: entry for templateId, entry in entries.items()
               if entry["bundled"]}
    # In the order the page always listed them, ahead of anything else.
    assert list(shipped) == list(RESULT_LAYOUTS)
    assert list(entries)[:len(shipped)] == list(RESULT_LAYOUTS)
    for templateId, (folder, size, slots, team) in RESULT_LAYOUTS.items():
        entry = shipped[templateId]
        assert (entry["folder"], tuple(entry["size"]), entry["entry"],
                entry["contract"]) == (folder, size, "result.html", 1)
        assert results.SlotsFor(templateId, entries) == slots, templateId
        assert results.IsTeamLayout(templateId, entries) is team, templateId
        assert results.KindOf(templateId, entries) == (
            results.TEAMS if team else results.PLAYERS)


def test_the_shipped_thumbnail_templates_are_as_they_were():
    entries = LayoutCatalog.Templates(KIND_THUMBNAIL, SHIPPED_ROOT)
    shipped = {templateId: entry for templateId, entry in entries.items()
               if entry["bundled"]}
    assert list(shipped) == list(THUMBNAIL_TEMPLATES)
    assert list(entries)[:len(shipped)] == list(THUMBNAIL_TEMPLATES)
    for templateId, (folder, size, template) in THUMBNAIL_TEMPLATES.items():
        entry = shipped[templateId]
        assert (entry["folder"], tuple(entry["size"]), entry["entry"],
                entry["template"], entry["contract"]) == (
            folder, size, "thumbnail.html", template, 1)
        # The template file is the layout's numbers, a JSON object.
        layout = json.loads(Path(LayoutCatalog.TemplatePath(
            entry, template, SHIPPED_ROOT)).read_text(encoding="utf-8"))
        assert isinstance(layout, dict) and "base_ratio" in layout


def test_an_unknown_layout_reads_as_eight_players_rows_as_it_always_did():
    entries = LayoutCatalog.Templates(KIND_RESULT, SHIPPED_ROOT)
    for layout in ("gone", None, 3):
        assert results.KindOf(layout, entries) == results.PLAYERS
        assert results.SlotsFor(layout, entries) == 8


def test_the_shipped_manifests_declare_the_contract():
    for kind, ids in LayoutCatalog.BUNDLED_TEMPLATES.items():
        for folder in ids.values():
            block = json.loads((SHIPPED_ROOT / folder / "settings.json")
                               .read_text(encoding="utf-8"))["autona"]
            assert block["kind"] == kind, folder
            assert block["contract"] == 1, folder
            assert block["license"] == "MIT", folder
            # A shipped package is named in core's words, not by its block.
            assert "displayName" not in block, folder
            assert set(block) <= {"kind", "contract", "entry", "size", "rows",
                                  "template", "author", "license"}, folder


@pytest.mark.parametrize("locale, language", [
    ("ja-JP", "ja"), ("en-US", "en"), (None, "en")])
def test_a_shipped_package_is_named_in_cores_words(locale, language):
    for kind in LayoutCatalog.TEMPLATE_KINDS:
        for templateId, entry in LayoutCatalog.Templates(
                kind, SHIPPED_ROOT, locale).items():
            if not entry["bundled"]:
                continue
            words = CoreWords.WORDS[LayoutCatalog.BundledNameKey(
                kind, templateId)]
            # As the catalog shows it; with the id after it when a local
            # package shares the name.
            assert (entry["displayName"], entry["displayNames"]) in (
                (words[language], {"ja": words["ja"], "en": words["en"]}),
                (f"{words[language]} ({templateId})",
                 {"ja": f"{words['ja']} ({templateId})",
                  "en": f"{words['en']} ({templateId})"})), templateId


# ── a package placed by hand ─────────────────────────────────────────────

def test_a_package_placed_by_hand_is_listed_under_its_folders_name(
        layoutRoot):
    package(layoutRoot, "result", result_block())   # a shipped one
    package(layoutRoot, "neon_top8", result_block(
        displayName={"ja": "ネオン", "en": "Neon"}, size=[1280, 720],
        rows="teams", author="someone", license="MIT", entry="index.html"))
    package(layoutRoot, "plain_thumb", thumbnail_block(displayName="Plain"),
            files=("layout.json",))
    package(layoutRoot, "unnamed_thumb", thumbnail_block(),
            files=("layout.json",))

    assert LayoutCatalog.Templates(KIND_RESULT, layoutRoot)["neon_top8"] == {
        "id": "neon_top8", "kind": "result",
        # A string, as every kind's: the name in the list's locale (None:
        # English); both are displayNames.
        "displayName": "Neon",
        "displayNames": {"ja": "ネオン", "en": "Neon"},
        "format": None, "game": None, "entry": "index.html",
        "folder": "neon_top8", "bundled": False, "contract": 1,
        "size": [1280, 720], "rows": "teams", "author": "someone",
        "license": "MIT"}
    assert LayoutCatalog.Templates(KIND_RESULT, layoutRoot, "ja-JP")[
        "neon_top8"]["displayName"] == "ネオン"
    assert list(LayoutCatalog.Templates(KIND_RESULT, layoutRoot)) == [
        "horizontal", "neon_top8"]
    thumbs = LayoutCatalog.Templates(KIND_THUMBNAIL, layoutRoot)
    # A string name is the name in both languages.
    assert (thumbs["plain_thumb"]["displayName"],
            thumbs["plain_thumb"]["displayNames"]) == (
        "Plain", {"ja": "Plain", "en": "Plain"})
    assert thumbs["plain_thumb"]["template"] == "layout.json"
    assert (thumbs["plain_thumb"]["author"], thumbs["plain_thumb"]["license"]) \
        == (None, None)
    # Without a name, the folder's, as for the other kinds.
    assert thumbs["unnamed_thumb"]["displayName"] == "Unnamed Thumb"
    assert thumbs["unnamed_thumb"]["displayNames"] == {
        "ja": "Unnamed Thumb", "en": "Unnamed Thumb"}


def test_the_packages_come_after_the_other_layouts_shipped_ones_first(
        layoutRoot):
    package(layoutRoot, "a_result", result_block())
    package(layoutRoot, "result_team", result_block(rows="teams"))
    package(layoutRoot, "result", result_block())
    package(layoutRoot, "thumbnail", thumbnail_block(template="type_a.json"),
            files=("type_a.json",))
    package(layoutRoot, "zz_scoreboard", {"kind": "scoreboard"},
            entry="index.html")

    assert [entry["id"] for entry in LayoutCatalog.Layouts(root=layoutRoot)] \
        == ["zz_scoreboard", "horizontal", "team", "a_result", "type_a"]


@pytest.mark.parametrize("name, expected", [
    ("Neon", "Neon"),
    ({"ja": "ネオン", "en": "Neon"}, "ネオン"),
    ({"en": "Neon"}, "Neon"),
    ({"ja": "ネオン", "en": "  "}, "ネオン"),
])
def test_a_display_name_is_read_in_the_locales_language(
        layoutRoot, name, expected):
    package(layoutRoot, "mine", result_block(displayName=name))
    english = name if isinstance(name, str) else \
        (name.get("en") or "").strip() or name.get("ja")
    for locale, said in (("ja-JP", expected), ("en-US", english)):
        entry = LayoutCatalog.Templates(KIND_RESULT, layoutRoot, locale)["mine"]
        assert entry["displayName"] == said
        assert LayoutCatalog.DisplayText(entry["displayNames"], locale) == said


def test_two_packages_of_one_name_both_get_their_id(layoutRoot):
    name = {"ja": "同じ", "en": "Same"}
    package(layoutRoot, "one", result_block(displayName=name))
    package(layoutRoot, "two", result_block(displayName=name))
    package(layoutRoot, "three", result_block(displayName="Same"))

    # Shared in the list's language, whatever the other.
    entries = LayoutCatalog.Templates(KIND_RESULT, layoutRoot, "ja-JP")
    assert (entries["one"]["displayName"], entries["one"]["displayNames"]) \
        == ("同じ (one)", {"ja": "同じ (one)", "en": "Same (one)"})
    assert entries["two"]["displayName"] == "同じ (two)"
    assert entries["three"]["displayName"] == "Same"
    entries = LayoutCatalog.Templates(KIND_RESULT, layoutRoot, "en-US")
    assert [entries[layoutId]["displayName"]
            for layoutId in ("one", "three", "two")] == [
        "Same (one)", "Same (three)", "Same (two)"]


# ── what is not listed ───────────────────────────────────────────────────

@pytest.mark.parametrize("block, files", [
    (result_block(contract=None), ()),
    (result_block(contract=2), ()),
    (result_block(contract="1"), ()),
    (result_block(contract=True), ()),
    (result_block(size=None), ()),
    (result_block(size=[1920]), ()),
    (result_block(size=[0, 1080]), ()),
    (result_block(size=[1920, 4097]), ()),
    (result_block(size=[1920.0, 1080]), ()),
    (result_block(size=[True, 1080]), ()),
    (result_block(size="1920x1080"), ()),
    (result_block(rows=None), ()),
    (result_block(rows="pairs"), ()),
    (result_block(entry="../result/result.html"), ()),
    (result_block(entry="..\\result\\result.html"), ()),
    (result_block(entry="C:\\result.html"), ()),
    (result_block(entry="C:result.html"), ()),
    (result_block(entry="sub\\result.html"), ()),
    (result_block(entry="gone.html"), ()),
    (thumbnail_block(template=None), ()),
    (thumbnail_block(), ()),                       # no layout.json
    (thumbnail_block(template="../thumbnail/type_a.json"), ()),
    (thumbnail_block(template="..\\thumbnail\\type_a.json"), ()),
    (thumbnail_block(template="C:\\layout.json"), ()),
    (thumbnail_block(template="sub/layout.json"), ()),
    (thumbnail_block(template="sub\\layout.json"), ()),
], ids=lambda value: json.dumps(value) if isinstance(value, dict) else "")
def test_a_broken_manifest_costs_its_own_entry(
        layoutRoot, warnings, block, files):
    directory = package(layoutRoot, "broken", block, files=files)
    if block.get("entry") == "gone.html":
        # Named, and not there (the kind's default page is).
        (directory / "gone.html").unlink()
        (directory / "result.html").write_text("x", encoding="utf-8")
    package(layoutRoot, "fine_result", result_block())
    package(layoutRoot, "fine_thumb", thumbnail_block(), files=("layout.json",))

    assert [entry["id"] for entry in LayoutCatalog.Layouts(root=layoutRoot)] \
        == ["fine_result", "fine_thumb"]
    assert any("broken" in line and "not listing" in line
               for line in warnings)


def test_a_package_needs_its_manifest(layoutRoot, warnings):
    # No folder-name fallback for the template kinds (ADR-007 D1).
    for name, page in (("result_mine", "result.html"),
                       ("thumbnail_mine", "thumbnail.html")):
        (layoutRoot / name).mkdir()
        (layoutRoot / name / page).write_text("x", encoding="utf-8")
    broken = layoutRoot / "result_broken"
    broken.mkdir()
    (broken / "result.html").write_text("x", encoding="utf-8")
    (broken / "settings.json").write_text("{not json", encoding="utf-8")

    assert LayoutCatalog.Layouts(root=layoutRoot) == []


def test_a_folder_named_like_a_shipped_id_is_not_listed(layoutRoot, warnings):
    package(layoutRoot, "result", result_block())
    package(layoutRoot, "horizontal", result_block(displayName="Impostor"))
    # Of another kind, the id is not taken.
    package(layoutRoot, "type_b", result_block(displayName="Not a thumbnail"))
    package(layoutRoot, "type_a", thumbnail_block(), files=("layout.json",))

    entries = LayoutCatalog.Templates(KIND_RESULT, layoutRoot)
    assert list(entries) == ["horizontal", "type_b"]
    assert entries["horizontal"]["folder"] == "result"
    assert entries["horizontal"]["bundled"] is True
    assert LayoutCatalog.Templates(KIND_THUMBNAIL, layoutRoot) == {}
    assert any("horizontal" in line and "shipped" in line
               for line in warnings)
    assert any("type_a" in line and "shipped" in line for line in warnings)


@pytest.mark.parametrize("name, block, files", [
    ("Horizontal", result_block(), ()),
    ("HORIZONTAL_16", result_block(), ()),
    ("Type_A", thumbnail_block(), ("layout.json",)),
])
def test_a_shipped_id_in_another_case_is_not_listed(
        layoutRoot, warnings, name, block, files):
    # Windows does not tell the cases of a folder's name apart: one named
    # like a shipped id in another case would be read for it.
    package(layoutRoot, name, block, files=files)
    assert LayoutCatalog.Layouts(root=layoutRoot) == []
    assert any(name in line and "shipped" in line for line in warnings)


def test_a_shipped_folder_in_another_case_is_the_shipped_package(layoutRoot):
    package(layoutRoot, "Result_Top16", result_block())

    entry = LayoutCatalog.Templates(KIND_RESULT, layoutRoot)["horizontal_16"]
    assert (entry["folder"], entry["bundled"]) == ("Result_Top16", True)


def test_a_shipped_folder_of_another_kind_is_an_ordinary_package(layoutRoot):
    package(layoutRoot, "result", thumbnail_block(), files=("layout.json",))

    entry = LayoutCatalog.Templates(KIND_THUMBNAIL, layoutRoot)["result"]
    assert (entry["folder"], entry["bundled"]) == ("result", False)
    assert LayoutCatalog.Templates(KIND_RESULT, layoutRoot) == {}


def test_a_missing_layout_root_lists_no_package():
    assert LayoutCatalog.Templates(KIND_RESULT, PROJECT_ROOT / "nope") == {}


# ── the folder every reader reads ────────────────────────────────────────

def test_the_folder_is_layout_under_the_app_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(LayoutCatalog, "_appDirectory", lambda: tmp_path)
    assert LayoutCatalog.Root() == str(tmp_path / "layout")
    # An absolute LAYOUT_ROOT is taken as it is (the tests' trees).
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(elsewhere))
    assert LayoutCatalog.Root() == str(elsewhere)
    # Given by no shell: the working directory.
    monkeypatch.setattr(LayoutCatalog, "_appDirectory", None)
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", "layout")
    monkeypatch.chdir(tmp_path)
    assert LayoutCatalog.Root() == str(tmp_path / "layout")


_ISOLATED_SCRIPT = _PRELUDE + r'''
import os
from qtpy.QtCore import QCoreApplication

app = QCoreApplication([])

from src.bootstrap import bootstrap_core
bootstrap_core()

from src.core import LayoutCatalog
from src.core.ResultService import ResultService
from src.core.ThumbnailService import ThumbnailService

report({
    "root": LayoutCatalog.Root(),
    "cwd": os.getcwd(),
    "layouts": [layout["id"] for layout in ResultService.instance.Layouts()],
    "templates": [template["id"]
                  for template in ThumbnailService.instance.Templates()],
    "setLayout": ResultService.instance.SetLayout("team_split"),
})
'''


def test_an_isolated_launch_sees_the_shipped_eight(tmp_path):
    """The app started in a working directory of its own, as the headless
    smoke test and the browser E2E start it (tests/test_bootstrap_split.py's
    _runtime): its layout/ has the shipped packages linked in, the catalog
    reads that folder (the app's directory, given by bootstrap_core), and the
    services offer all eight -- the E2E's PATCH /result of a shipped layout
    was refused when they could not."""
    result = _probe(tmp_path, _ISOLATED_SCRIPT)

    assert os.path.normcase(result["root"]) == os.path.normcase(
        os.path.join(result["cwd"], "layout"))
    assert result["layouts"][:len(RESULT_LAYOUTS)] == list(RESULT_LAYOUTS)
    assert result["templates"][:len(THUMBNAIL_TEMPLATES)] \
        == list(THUMBNAIL_TEMPLATES)
    assert result["setLayout"] is True
    assert set(SHIPPED_TEMPLATE_LINKS) == {
        f"layout/{folder}" for ids in LayoutCatalog.BUNDLED_TEMPLATES.values()
        for folder in ids.values()}


# ── a package reads nothing outside its folder but include/ ──────────────

# What a shipped page may name above its folder (ADR-007 D2): layout/include/,
# and through ../../ the app's assets/ (the fonts) or a path the payload gives
# ("../../" + path, the templates' resolveAssetPath).
_PARENT = re.compile(r"(?:\.\./)+[^\s\"'`)]*")
# What a page loads (HTML src / href, CSS url() / @import) and the strings in
# its scripts and template files.
_LOADED = re.compile(
    r"""(?:\b(?:src|href)\s*=\s*["']([^"']*)["'])"""
    r"""|(?:url\(\s*["']?([^"')]*))"""
    r"""|(?:@import\s+["']([^"']*)["'])""")
_LITERAL = re.compile(r""""([^"\n]*)"|'([^'\n]*)'|`([^`\n]*)`""")
# The strings of the payload's paths a shipped template handles (contract 1,
# layout/README.md): the resolver's prefixes and its test for a root-relative
# path, and the default icon the payload always gives (ResultService
# DEFAULT_ICON_PATH, the thumbnail's).
_PAYLOAD_PATH_STRINGS = {"../../", "file:///", "layout/", "./layout/logo.png"}
# An SVG namespace in a data: URL is a name, not something loaded.
_NAMESPACE = "http://www.w3.org/"


def _outside(reference, loaded):
    """Whether `reference` (a string of a shipped package's file) names
    something outside the package that is not layout/include/: a parent other
    than ../include/ (or ../../ to the app's assets and the payload's paths),
    the root of the origin, another host, a drive, a file: or http(s): URL,
    or layout/<x> (the app's layout/, not the package's). `loaded`: it is
    what a page loads (src / href / url() / @import), where no scheme at all
    is taken and the payload's path strings are not either."""
    text = reference.strip()
    if not text or text.startswith(("#", "data:", "${")) \
            or text.startswith(_NAMESPACE) \
            or (not loaded and text in _PAYLOAD_PATH_STRINGS):
        return False
    if _PARENT.match(text):
        return not (text.startswith("../include/") or text == "../../"
                    or (text.startswith("../../assets/")
                        and not text.startswith("../../../")))
    return bool(
        # The origin's root or another host ("//"), a share; not a lone "/"
        # (a separator a script splits by).
        re.match(r"/{1,2}[\w.-]", text) or re.match(r"\\\\?[\w.-]", text)
        or re.match(r"[A-Za-z]:[\\/]", text)
        or re.match(r"(?:file|https?):", text, re.IGNORECASE)
        or re.match(r"(?:\./)?layout/", text)
        or (loaded and re.match(r"[A-Za-z][\w+.-]*:", text)))


def _outside_references(path):
    text = path.read_text(encoding="utf-8")
    refused = []
    for match in _PARENT.finditer(text):
        reference = match.group(0)
        if reference.startswith("../include/"):
            continue
        if reference.startswith("../../") and not reference.startswith(
                "../../../") and (reference == "../../"
                                  or reference.startswith("../../assets/")):
            continue
        refused.append(reference)
    loaded = [next(group for group in match.groups() if group is not None)
              for match in _LOADED.finditer(text)]
    refused += [reference for reference in loaded if _outside(reference, True)]
    literals = [next(group for group in match.groups() if group is not None)
                for match in _LITERAL.finditer(text)]
    refused += [literal for literal in literals
                if not _PARENT.match(literal.strip())
                and _outside(literal, False)]
    return refused


def _shipped_files():
    for ids in LayoutCatalog.BUNDLED_TEMPLATES.values():
        for folder in ids.values():
            for path in sorted((SHIPPED_ROOT / folder).iterdir()):
                if path.suffix in (".html", ".css", ".js", ".json"):
                    yield path


def test_a_shipped_package_reads_nothing_outside_its_folder_but_include():
    files = list(_shipped_files())
    assert len(files) >= 16
    refused = {str(path.relative_to(SHIPPED_ROOT)): _outside_references(path)
               for path in files}
    assert {name: found for name, found in refused.items() if found} == {}


@pytest.mark.parametrize("text", [
    '<script src="../thumbnail/thumbnail_assets.js"></script>',
    '@import url("../result_team/result.css");',
    '<script src="/layout/result/result.js"></script>',
    '<link rel="stylesheet" href="layout/result/result.css">',
    '<img src="file:///C:/Users/x.png">',
    '<script src="https://cdn.example.com/x.js"></script>',
    '<script src="//cdn.example.com/x.js"></script>',
    'background: url(C:\\x.png);',
    'const bg = "C:\\\\Users\\\\x.png";',
    'const other = "./layout/result/result.js";',
    'const font = "/assets/font/x.ttf";',
    'fetch("http://example.com/collect");',
    'const up = "../../../x";',
    'const data = "../../user_data/x.png";',
])
def test_the_pin_sees_every_shape_of_an_outside_reference(tmp_path, text):
    path = tmp_path / "page.html"
    path.write_text(text, encoding="utf-8")
    assert _outside_references(path) != []


def test_the_pin_lets_the_package_include_and_the_payload_paths_through(
        tmp_path):
    path = tmp_path / "page.js"
    path.write_text("\n".join([
        '<script src="../include/result.js"></script>',
        '<link href="./result.css"><img src="bg.png">',
        "@import url('../include/result_team.css');",
        'const font = "../../assets/font/LINESeedJP/LINESeedJP-Bold.ttf";',
        'return "../../" + path; return "file:///" + path;',
        'if (path.startsWith("layout/")) {}',
        'resolveAssetPath(config.main_icon_path || "./layout/logo.png");',
        "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>\")",
        'path.split("/"); "a/b".replace(/\\\\/g, "/");',
    ]), encoding="utf-8")
    assert _outside_references(path) == []


def test_what_the_shipped_packages_share_is_in_include():
    # The files a shipped page names in include/ are there, and they too
    # name nothing of another package's.
    shared = set()
    for path in _shipped_files():
        shared |= set(re.findall(r"\.\./include/([\w.-]+)",
                                 path.read_text(encoding="utf-8")))
    assert {"thumbnail_assets.js", "result.js", "result.css",
            "result_team.css"} <= shared
    for name in shared:
        path = SHIPPED_ROOT / "include" / name
        assert path.is_file(), name
        if path.suffix in (".css", ".js") and not name.endswith(".min.js"):
            assert _outside_references(path) == [], name


def test_a_shipped_template_file_points_its_images_into_assets():
    for templateId, (folder, _size, template) in THUMBNAIL_TEMPLATES.items():
        layout = json.loads((SHIPPED_ROOT / folder / template)
                            .read_text(encoding="utf-8"))
        assert layout["default_background"].startswith("./assets/"), \
            templateId
