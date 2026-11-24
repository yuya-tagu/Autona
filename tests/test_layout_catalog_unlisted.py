"""What the layout catalog does not list, and why (ADR-008 D3).

``LayoutCatalog.Scan`` gives the folders it skipped beside the list, each
``{folder, kind, reason, detail}``: GET /api/v1/layouts' ``unlisted`` and the
design lists' "designs that could not be read". Pinned here: each reason, the
kind it is given under (and so which page shows it), what is never reported
(the reserved folders, Designer projects, the kit's work folders, files, the
release's own non-design folders), and that the shipped tree reports nothing.
"""
import subprocess
from pathlib import Path

import pytest

from src.core import LayoutCatalog
from tests.test_layout_catalog import builtin, project

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def layoutRoot(tmp_path):
    root = tmp_path / "layout"
    root.mkdir()
    return root


def unlisted(root, kinds=None):
    return LayoutCatalog.Scan(kinds, root=root)[1]


def reasons(root, kinds=None):
    return {f["folder"]: (f["kind"], f["reason"], f["detail"])
            for f in unlisted(root, kinds)}


def test_every_reason_is_one_of_the_codes():
    assert LayoutCatalog.UNLISTED_REASONS == (
        "no_entry", "bad_settings", "bad_block", "unknown_kind",
        "unknown_contract", "bad_template", "bundled_id", "no_manifest",
        "bad_entry")


def test_a_missing_entry_page(layoutRoot):
    builtin(layoutRoot, "scoreboard_mine", {"kind": "scoreboard"}, entry=None)
    builtin(layoutRoot, "commentators_x", {
        "kind": "commentary", "entry": "main.html"})
    # By its folder name alone.
    builtin(layoutRoot, "bracket_top8_y", entry=None)
    assert reasons(layoutRoot) == {
        "scoreboard_mine": ("scoreboard", "no_entry", "index.html"),
        "commentators_x": ("commentary", "no_entry", "main.html"),
        "bracket_top8_y": ("top8", "no_entry", "index.html"),
    }


def test_a_settings_json_that_is_not_json(layoutRoot):
    folder = builtin(layoutRoot, "my_overlay")
    (folder / "settings.json").write_text('{"autona": {"kind": "scoreboard",}',
                                          encoding="utf-8")
    listed = builtin(layoutRoot, "other")
    (listed / "settings.json").write_text("[1, 2]", encoding="utf-8")
    found = reasons(layoutRoot)
    kind, reason, detail = found["my_overlay"]
    assert (kind, reason) == (None, "bad_settings")
    # The JSON error, which says where; never the path.
    assert detail.startswith("JSONDecodeError: ")
    assert "line 1" in detail and str(layoutRoot) not in detail
    assert found["other"] == (None, "bad_settings", "not a JSON object")


def test_a_broken_manifest_of_a_named_folder_is_listed_with_a_warning(
        layoutRoot):
    # Listed by its folder name as before; what is wrong rides on the entry.
    folder = builtin(layoutRoot, "scoreboard_named")
    (folder / "settings.json").write_text("{", encoding="utf-8")
    builtin(layoutRoot, "commentators_block", settings={"autona": [1]})
    builtin(layoutRoot, "bracket_top8_kind", {"kind": "top-8"})
    builtin(layoutRoot, "scoreboard_entry", {
        "kind": "scoreboard", "entry": "../x.html"})
    builtin(layoutRoot, "scoreboard_fine", {"kind": "scoreboard"})
    layouts, skipped = LayoutCatalog.Scan(root=layoutRoot)
    assert skipped == []
    warned = {e["id"]: e.get("warnings") for e in layouts}
    [named] = warned["scoreboard_named"]
    assert named["reason"] == "bad_settings"
    assert named["detail"].startswith("JSONDecodeError: ")
    assert warned["commentators_block"] == [
        {"reason": "bad_block", "detail": None}]
    assert warned["bracket_top8_kind"] == [
        {"reason": "unknown_kind", "detail": "'top-8'"}]
    assert warned["scoreboard_entry"] == [
        {"reason": "bad_entry", "detail": "'../x.html'"}]
    # None: no key at all.
    assert "warnings" not in next(
        e for e in layouts if e["id"] == "scoreboard_fine")


def test_a_block_that_is_not_an_object_or_of_an_unknown_kind(layoutRoot):
    builtin(layoutRoot, "a_block", settings={"autona": "scoreboard"})
    builtin(layoutRoot, "a_kind", {"kind": "scorebaord"})
    builtin(layoutRoot, "no_kind", {"displayName": "X"})
    builtin(layoutRoot, "designer_kind", {"kind": "designer"})
    assert reasons(layoutRoot) == {
        "a_block": (None, "bad_block", None),
        "a_kind": (None, "unknown_kind", "'scorebaord'"),
        "no_kind": (None, "unknown_kind", "None"),
        "designer_kind": (None, "unknown_kind", "'designer'"),
    }


def test_an_unknown_contract(layoutRoot):
    builtin(layoutRoot, "scoreboard_next", {"kind": "scoreboard",
                                            "contract": 2})
    builtin(layoutRoot, "commentators_text", {"kind": "commentary",
                                              "contract": "1"})
    builtin(layoutRoot, "results_next", {
        "kind": "result", "contract": 9, "size": [1920, 1080],
        "rows": "players"}, entry="result.html")
    assert reasons(layoutRoot) == {
        "commentators_text": ("commentary", "unknown_contract", "'1'"),
        "results_next": ("result", "unknown_contract",
                         "contract must be one of (1, 2), got 9"),
        "scoreboard_next": ("scoreboard", "unknown_contract", "2"),
    }


def test_a_template_package_missing_what_its_contract_needs(layoutRoot):
    builtin(layoutRoot, "results_sizeless", {
        "kind": "result", "contract": 1, "rows": "players"},
        entry="result.html")
    builtin(layoutRoot, "thumbs_pageless", {
        "kind": "thumbnail", "contract": 1, "size": [1280, 720],
        "template": "t.png"}, entry=None)
    (layoutRoot / "thumbs_pageless" / "t.png").write_bytes(b"png")
    # Named like a shipped one (in another case).
    builtin(layoutRoot, "Horizontal", {
        "kind": "result", "contract": 1, "size": [1920, 1080],
        "rows": "players"}, entry="result.html")
    found = reasons(layoutRoot)
    assert found["results_sizeless"][:2] == ("result", "bad_template")
    assert found["results_sizeless"][2].startswith("size must be")
    assert found["thumbs_pageless"] == (
        "thumbnail", "no_entry", "thumbnail.html")
    assert found["Horizontal"][:2] == ("result", "bundled_id")


def test_neither_a_block_nor_a_folder_name_prefix(layoutRoot):
    builtin(layoutRoot, "my_overlay")
    builtin(layoutRoot, "with_settings", settings={"width": 1920}, entry=None)
    builtin(layoutRoot, "other_page", entry="overlay.html")
    assert reasons(layoutRoot) == {
        "my_overlay": (None, "no_manifest", None),
        "with_settings": (None, "no_manifest", None),
        "other_page": (None, "no_manifest", None),
    }


def test_a_folder_with_neither_a_settings_json_nor_a_page_is_not_reported(
        layoutRoot):
    # Pictures or fonts an operator keeps in layout/: no design that failed.
    folder = layoutRoot / "pictures"
    folder.mkdir()
    (folder / "bg.png").write_bytes(b"png")
    (layoutRoot / "empty").mkdir()
    assert unlisted(layoutRoot) == []


def test_a_folder_a_release_shipped_and_deleted_is_not_reported(layoutRoot):
    # The update never deletes: they stay on an operator's PC.
    for name in sorted(LayoutCatalog.RETIRED_SHIPPED_DIRS):
        builtin(layoutRoot, name, entry=None)
        (layoutRoot / name / "settings.json").write_text(
            "{}", encoding="utf-8")
    assert unlisted(layoutRoot) == []
    # One with a prefix and its page is still listed by its name.
    (layoutRoot / "scoreboard_BST" / "index.html").write_text(
        "<html></html>", encoding="utf-8")
    assert "scoreboard_BST" in [
        e["id"] for e in LayoutCatalog.Layouts(root=layoutRoot)]


def test_what_never_was_a_design_is_not_reported(layoutRoot):
    for name in ("designer_runtime", "include", "icons"):
        (layoutRoot / name).mkdir()
    # A Designer project, broken: the Designer's, not the catalog's to explain.
    project(layoutRoot, "half", stub=False)
    (layoutRoot / ".new-design-0a1b").mkdir()
    (layoutRoot / "notes.txt").write_text("x", encoding="utf-8")
    # In any case (Windows does not tell them apart).
    for name in LayoutCatalog.SHIPPED_NON_DESIGNS:
        builtin(layoutRoot, name.upper() if name == "winner_popup" else name)
    assert unlisted(layoutRoot) == []


def test_the_kinds_asked_for_and_the_unknown_ones(layoutRoot):
    builtin(layoutRoot, "scoreboard_a", {"kind": "scoreboard"}, entry=None)
    builtin(layoutRoot, "commentators_b", {"kind": "commentary"}, entry=None)
    builtin(layoutRoot, "bracket_top8_c", {"kind": "top8"}, entry=None)
    builtin(layoutRoot, "loose")

    def folders(kinds):
        return [f["folder"] for f in unlisted(layoutRoot, kinds)]

    # Folder order; the unknown kind's on every list.
    assert folders(None) == ["bracket_top8_c", "commentators_b", "loose",
                             "scoreboard_a"]
    assert folders(["scoreboard", "designer"]) == ["loose", "scoreboard_a"]
    assert folders(["commentary"]) == ["commentators_b", "loose"]
    assert folders(["top8"]) == ["bracket_top8_c", "loose"]
    assert folders(["result"]) == ["loose"]


def test_a_detail_is_cut_to_its_length(layoutRoot):
    builtin(layoutRoot, "scoreboard_long", {"kind": "scoreboard",
                                            "contract": "x" * 500})
    [folder] = unlisted(layoutRoot)
    assert len(folder["detail"]) == LayoutCatalog.UNLISTED_DETAIL_MAX
    assert folder["detail"].endswith("…")


def test_scan_lists_what_layouts_lists(layoutRoot):
    builtin(layoutRoot, "scoreboard_simple", {"kind": "scoreboard"})
    builtin(layoutRoot, "loose")
    project(layoutRoot, "finals", {"name": "F"})
    for kinds in (None, ["scoreboard"], ["designer"], ["top8"]):
        assert LayoutCatalog.Scan(kinds, root=layoutRoot)[0] \
            == LayoutCatalog.Layouts(kinds, root=layoutRoot)


def _tracked_layout_folders():
    out = subprocess.run(
        ["git", "ls-files", "layout"], cwd=PROJECT_ROOT, capture_output=True,
        text=True, encoding="utf-8", check=True).stdout
    return {line.split("/")[1] for line in out.splitlines()
            if line.count("/") >= 2}


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True).stdout


def test_the_retired_folders_are_the_ones_git_saw_deleted():
    tracked = _tracked_layout_folders()
    # None is shipped now (a folder shipped again leaves the set).
    assert not LayoutCatalog.RETIRED_SHIPPED_DIRS & tracked
    # What the history at hand saw deleted (a shallow clone sees less, and
    # the check is then weaker, never wrong).
    deleted = {line.split("/")[1] for line in _git(
        "log", "--diff-filter=D", "--name-only", "--pretty=format:", "--",
        "layout").splitlines() if line.count("/") >= 2}
    # A folder the release stops shipping is added to the set.
    assert deleted - tracked <= LayoutCatalog.RETIRED_SHIPPED_DIRS


def test_the_shipped_tree_reports_nothing():
    # Every folder the release ships is listed, reserved, or one of
    # SHIPPED_NON_DESIGNS: an operator sees no "could not be read" for
    # Autona's own files.
    tracked = _tracked_layout_folders()
    assert LayoutCatalog.SHIPPED_NON_DESIGNS <= tracked
    layouts, skipped = LayoutCatalog.Scan(root=PROJECT_ROOT / "layout")
    shipped = [f for f in skipped if f["folder"] in tracked]
    assert shipped == []
