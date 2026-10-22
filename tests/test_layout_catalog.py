"""Layout catalog (src/core/LayoutCatalog.py): one list of every overlay design.

docs/changes/layout-custom-unification.md Step 1. The Board / Commentary /
Top 8 tabs, the Designer's "open" list and ``GET /api/v1/layouts`` all read
this catalog, so what is pinned here is what every one of them shows:

- discovery of built-in layouts (``settings.json`` ``autona`` block) and
  Designer projects (``layout/custom/{slug}/project.json``), in one shape;
- the display names: a Designer project is "Custom: {name}", and a name two
  entries of one kind share gets each one's id appended;
- the defaults (a Designer project without ``meta`` is "1v1 / カスタム", the
  text the Board tab used to hard-code for every custom design);
- the ``kind`` filter and the folders that are never listed
  (``designer_runtime/``, ``include/``, ``icons/``, ``custom/*/_legacy/``);
- one unreadable file (locked, broken, absurdly nested) costing only its own
  entry;
- the watch directories the Board tab hands to its QFileSystemWatcher;
- and, against the shipped ``layout/`` tree, that the display names and the
  format / game moved into ``settings.json`` are character-for-character the
  ones the panels used to carry as constants.
"""
import json
import os
from pathlib import Path

import pytest
from loguru import logger

from src.core import LayoutCatalog
from src.core.LayoutCatalog import (
    KIND_COMMENTARY, KIND_DESIGNER, KIND_SCOREBOARD, KIND_TOP8)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── tree builders ────────────────────────────────────────────────────────
def builtin(root, name, autona=None, entry="index.html", settings=None):
    """A built-in layout folder with an entry file and, optionally, a
    settings.json carrying an ``autona`` block."""
    directory = root / name
    directory.mkdir(parents=True)
    if entry:
        (directory / entry).write_text("<html></html>", encoding="utf-8")
    data = dict(settings or {})
    if autona is not None:
        data["autona"] = autona
    if data or settings is not None:
        (directory / "settings.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return directory


def project(root, slug, project_data=None, stub=True, raw=None):
    """A Designer project folder under ``root/custom``."""
    directory = root / "custom" / slug
    directory.mkdir(parents=True)
    if stub:
        (directory / "index.html").write_text(
            "<!-- Autona Designer stub -->", encoding="utf-8")
    if raw is not None:
        (directory / "project.json").write_text(raw, encoding="utf-8")
    elif project_data is not None:
        (directory / "project.json").write_text(
            json.dumps(project_data, ensure_ascii=False), encoding="utf-8")
    return directory


@pytest.fixture
def layoutRoot(tmp_path):
    root = tmp_path / "layout"
    root.mkdir()
    return root


def ids(layouts):
    return [entry["id"] for entry in layouts]


# ── discovery and shape ──────────────────────────────────────────────────
def test_builtin_layouts_and_designer_projects_share_one_shape(layoutRoot):
    builtin(layoutRoot, "scoreboard_simple", {
        "kind": "scoreboard", "displayName": "Simple",
        "format": "1v1 / チーム", "game": "汎用（全ゲーム）"},
        settings={"assets": {"default": {}}})
    builtin(layoutRoot, "commentators", {
        "kind": "commentary", "displayName": "標準"})
    project(layoutRoot, "finals", {
        "schemaVersion": 3, "name": "決勝",
        "meta": {"format": "2v2", "game": "スト6"}, "layers": []})

    catalog = LayoutCatalog.Discover(layoutRoot)

    assert catalog.layouts == [
        {"id": "commentators", "kind": "commentary", "displayName": "標準",
         "format": None, "game": None, "entry": "index.html"},
        {"id": "scoreboard_simple", "kind": "scoreboard",
         "displayName": "Simple", "format": "1v1 / チーム",
         "game": "汎用（全ゲーム）", "entry": "index.html"},
        {"id": "custom/finals", "kind": "designer",
         "displayName": "Custom: 決勝",
         "format": "2v2", "game": "スト6", "entry": "index.html"},
    ]


def test_builtin_layouts_come_first_in_folder_order_then_projects(layoutRoot):
    # Built-ins keep the plain sort the Board tab always used (upper case
    # first: scoreboard_Universal before scoreboard_crewbattle); projects
    # sort case-insensitively, as the Designer's open list always did.
    for name in ("scoreboard_crewbattle", "scoreboard_Universal",
                 "scoreboard_simple"):
        builtin(layoutRoot, name, {"kind": "scoreboard"})
    for slug in ("beta", "Alpha", "gamma"):
        project(layoutRoot, slug, {"name": slug})

    assert ids(LayoutCatalog.Discover(layoutRoot).layouts) == [
        "scoreboard_Universal", "scoreboard_crewbattle", "scoreboard_simple",
        "custom/Alpha", "custom/beta", "custom/gamma",
    ]


def test_nothing_is_read_at_import_and_the_default_root_is_resolved_late(
        layoutRoot, monkeypatch):
    builtin(layoutRoot, "scoreboard_simple", {"kind": "scoreboard"})
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(layoutRoot))
    assert ids(LayoutCatalog.Discover().layouts) == ["scoreboard_simple"]


def test_a_missing_layout_root_is_an_empty_catalog(tmp_path):
    catalog = LayoutCatalog.Discover(tmp_path / "nope")
    assert catalog.layouts == []
    assert catalog.watchDirs == []


# ── defaults ─────────────────────────────────────────────────────────────
def test_a_project_without_meta_is_1v1_custom(layoutRoot):
    project(layoutRoot, "plain", {"schemaVersion": 1, "name": "新しいデザイン",
                                  "layers": []})

    [entry] = LayoutCatalog.Layouts([KIND_DESIGNER], root=layoutRoot)
    assert (entry["format"], entry["game"]) == ("1v1", "カスタム")
    # The Board tab renders it exactly as its former fixed custom text.
    assert f"対戦形式：{entry['format']}\nゲーム：{entry['game']}" == (
        "対戦形式：1v1\nゲーム：カスタム")


@pytest.mark.parametrize("meta, expected", [
    ({"format": "2v2"}, ("2v2", "カスタム")),
    ({"game": "スト6"}, ("1v1", "スト6")),
    ({"format": "  ", "game": ""}, ("1v1", "カスタム")),
    ({"format": 3, "game": None}, ("1v1", "カスタム")),
    ("not an object", ("1v1", "カスタム")),
])
def test_project_meta_fields_fall_back_one_by_one(layoutRoot, meta, expected):
    project(layoutRoot, "p", {"name": "P", "meta": meta})
    [entry] = LayoutCatalog.Layouts([KIND_DESIGNER], root=layoutRoot)
    assert (entry["format"], entry["game"]) == expected


@pytest.mark.parametrize("name, expected", [
    (None, "my-slug"),
    ("", "my-slug"),
    (42, "42"),
])
def test_a_project_without_a_name_is_listed_under_its_slug(
        layoutRoot, name, expected):
    project(layoutRoot, "my-slug", {"name": name})
    [entry] = LayoutCatalog.Layouts([KIND_DESIGNER], root=layoutRoot)
    assert entry["displayName"] == "Custom: " + expected
    # The Designer's own list (CustomLayouts) carries the bare name.
    [raw] = LayoutCatalog.CustomLayouts(layoutRoot / "custom")
    assert raw["displayName"] == expected


# ── display names that would read the same ──────────────────────────────
def test_names_shared_within_a_kind_all_get_their_id(layoutRoot):
    simple = {"kind": "scoreboard", "displayName": "Simple",
              "format": "1v1 / チーム", "game": "汎用（全ゲーム）"}
    builtin(layoutRoot, "scoreboard_simple", simple)
    # Explorer's copy of a shipped folder: same settings.json, same name.
    builtin(layoutRoot, "scoreboard_simple - コピー", simple)
    builtin(layoutRoot, "scoreboard_team", {
        "kind": "scoreboard", "displayName": "Team"})
    builtin(layoutRoot, "commentators", {
        "kind": "commentary", "displayName": "標準"})
    builtin(layoutRoot, "commentators_dark", {
        "kind": "commentary", "displayName": "標準"})
    # Same name as the commentary ones, but another kind (another pulldown).
    builtin(layoutRoot, "bracket_top8", {"kind": "top8", "displayName": "標準"})
    project(layoutRoot, "a", {"name": "決勝"})
    project(layoutRoot, "b", {"name": "決勝"})
    # A project named like a built-in: the prefix already tells them apart.
    project(layoutRoot, "c", {"name": "Team"})

    layouts = LayoutCatalog.Discover(layoutRoot).layouts
    assert {e["id"]: e["displayName"] for e in layouts} == {
        "scoreboard_simple": "Simple (scoreboard_simple)",
        "scoreboard_simple - コピー": "Simple (scoreboard_simple - コピー)",
        "scoreboard_team": "Team",
        "commentators": "標準 (commentators)",
        "commentators_dark": "標準 (commentators_dark)",
        "bracket_top8": "標準",
        "custom/a": "Custom: 決勝 (custom/a)",
        "custom/b": "Custom: 決勝 (custom/b)",
        "custom/c": "Custom: Team",
    }
    # Only the label changes; the rest of the entry is the folder's own.
    copy = next(e for e in layouts if e["id"] == "scoreboard_simple - コピー")
    assert (copy["format"], copy["game"]) == ("1v1 / チーム", "汎用（全ゲーム）")


def test_a_kind_filtered_list_disambiguates_like_the_full_catalog(layoutRoot):
    builtin(layoutRoot, "commentators", {
        "kind": "commentary", "displayName": "標準"})
    builtin(layoutRoot, "commentators_dark", {
        "kind": "commentary", "displayName": "標準"})
    builtin(layoutRoot, "bracket_top8", {"kind": "top8", "displayName": "標準"})
    project(layoutRoot, "a", {"name": "決勝"})
    project(layoutRoot, "b", {"name": "決勝"})

    full = LayoutCatalog.Discover(layoutRoot).layouts
    for kinds in ([KIND_COMMENTARY], [KIND_TOP8], [KIND_DESIGNER],
                  [KIND_SCOREBOARD, KIND_DESIGNER]):
        assert LayoutCatalog.Layouts(kinds, root=layoutRoot) == [
            entry for entry in full if entry["kind"] in kinds]


def test_a_name_that_stops_being_shared_goes_back_to_plain(layoutRoot):
    builtin(layoutRoot, "commentators", {
        "kind": "commentary", "displayName": "標準"})
    dark = builtin(layoutRoot, "commentators_dark", {
        "kind": "commentary", "displayName": "標準"})
    assert [e["displayName"] for e in LayoutCatalog.Discover(
        layoutRoot).layouts] == ["標準 (commentators)", "標準 (commentators_dark)"]

    (dark / "settings.json").write_text(json.dumps(
        {"autona": {"kind": "commentary", "displayName": "Dark"}}),
        encoding="utf-8")
    assert [e["displayName"] for e in LayoutCatalog.Discover(
        layoutRoot).layouts] == ["標準", "Dark"]


def test_a_builtin_block_with_only_a_kind_keeps_the_folder_derived_name(
        layoutRoot):
    builtin(layoutRoot, "scoreboard_big_one", {"kind": "scoreboard"})
    builtin(layoutRoot, "commentators", {"kind": "commentary"})
    builtin(layoutRoot, "lower_third", {"kind": "scoreboard"})

    assert {e["id"]: (e["displayName"], e["format"], e["game"])
            for e in LayoutCatalog.Discover(layoutRoot).layouts} == {
        "commentators": ("標準", None, None),
        "lower_third": ("Lower Third", None, None),
        "scoreboard_big_one": ("Big One", None, None),
    }


# ── folders without an autona block (pre-catalog convention) ─────────────
def test_folders_without_a_block_keep_the_old_folder_name_convention(
        layoutRoot):
    # A layout the operator dropped in before settings.json carried the
    # block is still offered where it was, under the name it had.
    builtin(layoutRoot, "scoreboard_my_overlay")
    builtin(layoutRoot, "scoreboard")
    builtin(layoutRoot, "commentators_dark-gold", settings={})
    builtin(layoutRoot, "bracket_top8")
    builtin(layoutRoot, "bracket_top8_neon")
    # Not one of the three prefixes, or not followed by "_": not a design.
    builtin(layoutRoot, "commentatorsX")
    builtin(layoutRoot, "h2h_record", settings={"assets": {"default": {}}})

    assert {e["id"]: (e["kind"], e["displayName"])
            for e in LayoutCatalog.Discover(layoutRoot).layouts} == {
        "bracket_top8": (KIND_TOP8, "標準"),
        "bracket_top8_neon": (KIND_TOP8, "Neon"),
        "commentators_dark-gold": (KIND_COMMENTARY, "Dark Gold"),
        "scoreboard": (KIND_SCOREBOARD, "Scoreboard"),
        "scoreboard_my_overlay": (KIND_SCOREBOARD, "My Overlay"),
    }


@pytest.mark.parametrize("block", [
    {"kind": "designer", "displayName": "Hijack"},
    {"kind": "lower-third"},
    {"displayName": "No kind"},
    "not an object",
])
def test_a_broken_block_is_ignored_and_the_folder_falls_back(
        layoutRoot, block):
    builtin(layoutRoot, "scoreboard_x", block)
    builtin(layoutRoot, "lower_third", block)

    assert {e["id"]: (e["kind"], e["displayName"])
            for e in LayoutCatalog.Discover(layoutRoot).layouts} == {
        "scoreboard_x": (KIND_SCOREBOARD, "X"),
    }


def test_an_unreadable_settings_file_falls_back_too(layoutRoot):
    directory = builtin(layoutRoot, "scoreboard_x")
    (directory / "settings.json").write_text("{not json", encoding="utf-8")
    assert ids(LayoutCatalog.Discover(layoutRoot).layouts) == ["scoreboard_x"]


# ── one bad file costs one entry ─────────────────────────────────────────
# json.load raises RecursionError (not ValueError) on deep enough nesting.
_TOO_DEEP = "[" * 100_000 + "]" * 100_000


@pytest.fixture
def loggedWarnings():
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_a_nested_to_death_project_is_skipped_alone(
        layoutRoot, loggedWarnings):
    project(layoutRoot, "deep", raw='{"name": ' + _TOO_DEEP + "}")
    project(layoutRoot, "fine", {"name": "Fine"})

    assert ids(LayoutCatalog.Discover(layoutRoot).layouts) == ["custom/fine"]
    # The Designer's open list goes through the same reader.
    assert ids(LayoutCatalog.CustomLayouts(layoutRoot / "custom")) == [
        "custom/fine"]
    assert any("RecursionError" in line and "deep" in line
               for line in loggedWarnings)


def test_a_nested_to_death_settings_file_falls_back_to_the_folder_name(
        layoutRoot, loggedWarnings):
    directory = builtin(layoutRoot, "scoreboard_x")
    (directory / "settings.json").write_text(
        '{"autona": ' + _TOO_DEEP + "}", encoding="utf-8")
    builtin(layoutRoot, "scoreboard_y", {
        "kind": "scoreboard", "displayName": "Y"})

    assert {e["id"]: e["displayName"]
            for e in LayoutCatalog.Discover(layoutRoot).layouts} == {
        "scoreboard_x": "X", "scoreboard_y": "Y"}
    assert any("RecursionError" in line and "scoreboard_x" in line
               for line in loggedWarnings)


def test_any_error_reading_one_file_skips_only_that_file(
        layoutRoot, monkeypatch, loggedWarnings):
    project(layoutRoot, "cursed", {"name": "Cursed"})
    project(layoutRoot, "fine", {"name": "Fine"})
    realLoad = json.load

    def load(f, *args, **kwargs):
        if "cursed" in f.name:
            raise RuntimeError("something nobody planned for")
        return realLoad(f, *args, **kwargs)

    monkeypatch.setattr(LayoutCatalog.json, "load", load)
    assert ids(LayoutCatalog.Discover(layoutRoot).layouts) == ["custom/fine"]
    assert any("something nobody planned for" in line
               for line in loggedWarnings)


# ── entry file ───────────────────────────────────────────────────────────
def test_a_block_can_name_another_entry_file(layoutRoot):
    builtin(layoutRoot, "scoreboard_alt",
            {"kind": "scoreboard", "entry": "board.html"}, entry="board.html")
    [entry] = LayoutCatalog.Discover(layoutRoot).layouts
    assert entry["entry"] == "board.html"


def test_a_builtin_without_its_entry_file_is_not_listed(layoutRoot):
    builtin(layoutRoot, "scoreboard_wip", {"kind": "scoreboard"}, entry=None)
    builtin(layoutRoot, "scoreboard_alt",
            {"kind": "scoreboard", "entry": "board.html"})  # has index.html
    assert LayoutCatalog.Discover(layoutRoot).layouts == []


def test_an_entry_outside_the_folder_is_refused(layoutRoot):
    builtin(layoutRoot, "scoreboard_x",
            {"kind": "scoreboard", "entry": "../winner_popup/index.html"})
    [entry] = LayoutCatalog.Discover(layoutRoot).layouts
    assert entry["entry"] == "index.html"


# ── kind filter ──────────────────────────────────────────────────────────
@pytest.fixture
def mixedTree(layoutRoot):
    builtin(layoutRoot, "scoreboard_simple", {"kind": "scoreboard"})
    builtin(layoutRoot, "commentators", {"kind": "commentary"})
    builtin(layoutRoot, "bracket_top8", {"kind": "top8"})
    project(layoutRoot, "finals", {"name": "Finals"})
    return layoutRoot


@pytest.mark.parametrize("kinds, expected", [
    (None, ["bracket_top8", "commentators", "scoreboard_simple",
            "custom/finals"]),
    ([], ["bracket_top8", "commentators", "scoreboard_simple",
          "custom/finals"]),
    ([KIND_SCOREBOARD], ["scoreboard_simple"]),
    ([KIND_DESIGNER], ["custom/finals"]),
    ([KIND_COMMENTARY], ["commentators"]),
    ([KIND_TOP8], ["bracket_top8"]),
    ([KIND_SCOREBOARD, KIND_DESIGNER], ["scoreboard_simple", "custom/finals"]),
])
def test_layouts_filters_by_kind(mixedTree, kinds, expected):
    assert ids(LayoutCatalog.Layouts(kinds, root=mixedTree)) == expected


def test_layouts_is_discover_filtered(mixedTree):
    full = LayoutCatalog.Discover(mixedTree).layouts
    assert LayoutCatalog.Layouts(root=mixedTree) == full
    for kind in LayoutCatalog.KINDS:
        assert LayoutCatalog.Layouts([kind], root=mixedTree) == [
            entry for entry in full if entry["kind"] == kind]


def test_a_builtin_kind_alone_never_reads_the_projects(mixedTree, monkeypatch):
    def fail(_root):
        raise AssertionError("project.json files were read")

    monkeypatch.setattr(LayoutCatalog, "CustomLayouts", fail)
    assert ids(LayoutCatalog.Layouts([KIND_COMMENTARY], root=mixedTree)) == [
        "commentators"]


# ── folders that are never listed ────────────────────────────────────────
def test_reserved_folders_are_never_designs(layoutRoot):
    # Even with an index.html and a block, the runtime and shared-asset
    # folders are not designs.
    for name in ("designer_runtime", "include", "icons"):
        builtin(layoutRoot, name, {"kind": "scoreboard"})
    (layoutRoot / "designer_runtime" / "runtime.js").write_text(
        "/* runtime */", encoding="utf-8")
    assert LayoutCatalog.Discover(layoutRoot).layouts == []


def test_legacy_rollback_copies_and_incomplete_projects_are_skipped(
        layoutRoot):
    done = project(layoutRoot, "done", {"name": "Done"})
    legacy = done / "_legacy"
    legacy.mkdir()
    for name in ("index.html", "runtime.js", "runtime.css"):
        (legacy / name).write_text("old", encoding="utf-8")
    project(layoutRoot, "no-manifest")                       # index only
    project(layoutRoot, "no-stub", {"name": "x"}, stub=False)
    project(layoutRoot, "broken", raw="{not json")
    project(layoutRoot, "a-list", raw="[1, 2]")
    (layoutRoot / "custom" / "notes.txt").write_text("x", encoding="utf-8")

    assert ids(LayoutCatalog.Discover(layoutRoot).layouts) == ["custom/done"]


def test_a_linked_project_is_skipped(layoutRoot, tmp_path):
    # conftest's _link_tree: a directory junction on Windows (no Developer
    # Mode or elevation needed, and not a symlink to os.path.islink), a
    # symlink elsewhere -- so this runs on every platform.
    from tests.conftest import _link_tree

    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "index.html").write_text("x", encoding="utf-8")
    (target / "project.json").write_text('{"name": "x"}', encoding="utf-8")
    (layoutRoot / "custom").mkdir()
    link = layoutRoot / "custom" / "linked"
    _link_tree(link, target)
    try:
        assert os.path.isdir(link)  # the link itself resolves
        assert LayoutCatalog.Discover(layoutRoot).layouts == []
        assert LayoutCatalog.CustomLayouts(layoutRoot / "custom") == []
    finally:
        # A junction is a directory entry (rmdir); a symlink is unlinked.
        # Removed here so tmp cleanup never walks through it.
        if link.is_symlink():
            os.unlink(link)
        else:
            os.rmdir(link)
    assert (target / "project.json").is_file()


# ── watch directories ────────────────────────────────────────────────────
def test_watch_dirs_are_the_roots_and_every_project_folder(layoutRoot):
    builtin(layoutRoot, "scoreboard_simple", {"kind": "scoreboard"})
    done = project(layoutRoot, "done", {"name": "Done"})
    (done / "_legacy").mkdir()
    # A folder the Designer has only just created: no files yet, but its
    # writes must already reach the watcher.
    (layoutRoot / "custom" / "being-saved").mkdir()

    expected = [str(layoutRoot), str(layoutRoot / "custom"),
                str(layoutRoot / "custom" / "being-saved"), str(done)]
    assert LayoutCatalog.WatchDirs(layoutRoot) == expected
    assert LayoutCatalog.Discover(layoutRoot).watchDirs == expected
    assert all(os.path.isabs(path) for path in expected)


def test_watch_dirs_without_a_custom_folder(layoutRoot):
    assert LayoutCatalog.WatchDirs(layoutRoot) == [str(layoutRoot)]


# ── consumers that must agree with the catalog ───────────────────────────
def test_the_designer_open_list_is_the_catalogs_projects(
        layoutRoot, monkeypatch):
    from src.AutonaDesignerService import AutonaDesignerService

    project(layoutRoot, "Zeta", {"name": "Z"})
    project(layoutRoot, "alpha", {"schemaVersion": 1})
    project(layoutRoot, "incomplete")
    monkeypatch.setattr(AutonaDesignerService, "ROOT", layoutRoot / "custom")

    designers = LayoutCatalog.Layouts([KIND_DESIGNER], root=layoutRoot)
    assert AutonaDesignerService.list_projects() == [
        {"slug": "alpha", "name": "alpha"},
        {"slug": "Zeta", "name": "Z"},
    ]
    assert [f"custom/{p['slug']}" for p in
            AutonaDesignerService.list_projects()] == ids(designers)
    # The Designer lists only projects, so it shows the bare name; the
    # catalog puts "Custom: " in front of the same name.
    assert ["Custom: " + p["name"] for p in
            AutonaDesignerService.list_projects()] == [
        e["displayName"] for e in designers]


# ── the shipped layout/ tree ─────────────────────────────────────────────
# What the panels showed before the metadata moved into settings.json,
# spelled out from the removed constants: AutonaBoardWidget.
# DESIGN_DISPLAY_NAMES / DESIGN_META, and the base-folder label "標準" of
# AutonaLayoutPreview.LayoutDesignDisplayName (Commentary / Top 8).
SHIPPED_BUILTINS = {
    "scoreboard_Universal": (KIND_SCOREBOARD, "Universal",
                             "1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_crewbattle": (KIND_SCOREBOARD, "Crew Battle",
                              "クルーバトル", "スマブラSP（ストック制）"),
    "scoreboard_simple": (KIND_SCOREBOARD, "Simple",
                          "1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_team": (KIND_SCOREBOARD, "Team",
                        "1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_topleft": (KIND_SCOREBOARD, "Top Left",
                           "1v1 / チーム", "汎用（全ゲーム）"),
    "commentators": (KIND_COMMENTARY, "標準", None, None),
    "bracket_top8": (KIND_TOP8, "標準", None, None),
}


def test_the_shipped_layouts_keep_their_display_strings():
    # Only the folders we ship: a developer's own layout/ may hold more
    # (a copied scoreboard_simple, a design in progress), and those are not
    # this test's business. Such a copy can share a shipped name, in which
    # case the catalog appends the id to both (pinned above with tmp trees);
    # the shipped value itself is pinned exactly by the settings.json test
    # below.
    layouts = {entry["id"]: entry for entry in
               LayoutCatalog.Discover(PROJECT_ROOT / "layout").layouts}
    for name, (kind, display_name, fmt, game) in SHIPPED_BUILTINS.items():
        assert name in layouts, f"{name} is not in the catalog"
        entry = layouts[name]
        assert (entry["kind"], entry["format"], entry["game"]) == (
            kind, fmt, game), name
        assert entry["displayName"] in (
            display_name, f"{display_name} ({name})"), name
        assert entry["entry"] == "index.html", name


def test_the_shipped_layouts_declare_their_metadata_explicitly():
    # Every shipped design carries the block itself: the folder-name
    # fallback is for operator-dropped layouts, not for ours.
    for name, (kind, display_name, fmt, game) in SHIPPED_BUILTINS.items():
        settings = json.loads((PROJECT_ROOT / "layout" / name /
                               "settings.json").read_text(encoding="utf-8"))
        block = settings["autona"]
        assert block["kind"] == kind
        assert block["displayName"] == display_name
        assert block.get("format") == fmt
        assert block.get("game") == game


SHIPPED_BOARD_ORDER = ["scoreboard_Universal", "scoreboard_crewbattle",
                       "scoreboard_simple", "scoreboard_team",
                       "scoreboard_topleft"]


def test_the_shipped_board_order_is_unchanged():
    # The relative order of the shipped folders only: a developer's local
    # layout/ may add scoreboard folders anywhere in between.
    board = [entry["id"] for entry in
             LayoutCatalog.Layouts([KIND_SCOREBOARD],
                                   root=PROJECT_ROOT / "layout")]
    assert [name for name in board if name in SHIPPED_BOARD_ORDER] == (
        SHIPPED_BOARD_ORDER)
