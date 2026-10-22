import base64
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path, PurePosixPath

import pytest
from loguru import logger

from src.AutonaDesignerService import (
    AutonaDesignerService, DesignerProjectError)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The entry point every design was saved with before the shared runtime: a
# verbatim copy of the former web/public/scoreboard-designer-runtime/
# index.html, which save_project() copied next to a private runtime.js/css.
LEGACY_INDEX_HTML = """<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script>
      window.tshLayoutRootPath = "../..";
      window.tshProgramRootPath = "../../..";
    </script>
    <script src="../../include/globals.js"></script>
    <link rel="stylesheet" href="../../main.css" />
    <link rel="stylesheet" href="./runtime.css" />
  </head>
  <body>
    <main id="scoreboard-composer" aria-label="Autona scoreboard"></main>
    <div id="composer-error" role="alert"></div>
    <script src="./runtime.js"></script>
  </body>
</html>
"""


def make_project(name="Finals", with_image=True):
    layers = []
    if with_image:
        layers.append({
            "id": "frame",
            "name": "Frame",
            "type": "image",
            "asset": "assets/frame.png",
            "objectFit": "contain",
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "rotation": 0,
            "opacity": 1,
            "zIndex": 1,
            "visible": True,
            "animation": {
                "preset": "none",
                "durationMs": 0,
                "delayMs": 0,
            },
        })
    return {
        "schemaVersion": 1,
        "name": name,
        "canvas": {
            "width": 1920,
            "height": 1080,
            "backgroundColor": "transparent",
        },
        "layers": layers,
    }


def motion_animation():
    return {
        "renderer": "webAnimations",
        "durationMs": 450,
        "delayMs": 0,
        "easing": {"x1": 0.2, "y1": 0.8, "x2": 0.2, "y2": 1},
        "loop": False,
        "keyframes": [
            {
                "id": "start",
                "timeMs": 0,
                "x": -80,
                "y": 0,
                "rotation": 0,
                "scaleX": 1,
                "scaleY": 1,
            },
            {
                "id": "end",
                "timeMs": 450,
                "x": 0,
                "y": 0,
                "rotation": 0,
                "scaleX": 1,
                "scaleY": 1,
            },
        ],
    }


def make_v2_project(name="Finals", with_image=True):
    project = make_project(name, with_image)
    project["schemaVersion"] = 2
    for layer in project["layers"]:
        layer["animations"] = {
            "intro": motion_animation(),
            "update": motion_animation(),
        }
    return project


def event_timeline(change=False):
    timeline = {
        "durationMs": 440,
        "delayMs": 0,
        "easing": {"x1": 0.2, "y1": 0.8, "x2": 0.2, "y2": 1},
        "visualKeyframes": [
            {
                "id": "start",
                "timeMs": 0,
                "x": 0,
                "y": 0,
                "rotation": 0,
                "scaleX": 1,
                "scaleY": 1,
                "opacity": 1,
            },
            {
                "id": "end",
                "timeMs": 440,
                "x": 0,
                "y": 0,
                "rotation": 0,
                "scaleX": 1,
                "scaleY": 1,
                "opacity": 1,
            },
        ],
        "rendererActions": [],
    }
    if change:
        timeline.update({
            "swapAtMs": 220,
            "watchBindings": [],
            "interruptPolicy": "coalesceLatest",
        })
    return timeline


def make_v3_project(name="Finals", with_image=True):
    project = make_project(name, with_image)
    project.update({
        "schemaVersion": 3,
        "id": "project-v3",
        "assets": [{
            "id": "frame-asset",
            "kind": "image",
            "path": "assets/frame.png",
            "mediaType": "image/png",
        }] if with_image else [],
    })
    for layer in project["layers"]:
        layer["events"] = {
            "sceneEnter": event_timeline(),
            "dataChange": event_timeline(change=True),
        }
    return project


@pytest.fixture
def designer_service(tmp_path, monkeypatch):
    root = tmp_path / "layout" / "custom"
    runtime = tmp_path / "layout" / "designer_runtime"
    runtime.mkdir(parents=True)
    for name in AutonaDesignerService.RUNTIME_FILES:
        (runtime / name).write_bytes(b"/* shared runtime */")

    monkeypatch.setattr(AutonaDesignerService, "ROOT", root)
    monkeypatch.setattr(AutonaDesignerService, "RUNTIME_ROOT", runtime)
    with AutonaDesignerService._token_lock:
        AutonaDesignerService._tokens.clear()
    with AutonaDesignerService._import_lock:
        AutonaDesignerService._latest_import = None
        AutonaDesignerService._imports.clear()
    return AutonaDesignerService


def png_data_url():
    data = b"\x89PNG\r\n\x1a\n" + b"test-png"
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def svg_data_url():
    data = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<path d="M0 0h10v10H0z" fill="#fff"/></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")


def test_token_is_local_and_revocable(designer_service):
    token = designer_service.issue_token()
    assert designer_service.is_authorized(token, "127.0.0.1")
    assert not designer_service.is_authorized(token, "192.168.1.10")
    designer_service.revoke_token(token)
    assert not designer_service.is_authorized(token, "127.0.0.1")


def test_save_list_and_load_project(designer_service):
    project = make_project("決勝 Design")
    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    assert result == {
        "slug": "決勝-design",
        "path": "layout/custom/決勝-design",
    }
    directory = designer_service.ROOT / "決勝-design"
    assert (directory / "project.json").is_file()
    assert (directory / "assets" / "frame.png").is_file()
    assert (directory / "index.html").read_text(
        encoding="utf-8") == designer_service.STUB_INDEX_HTML

    assert designer_service.list_projects() == [{
        "slug": "決勝-design",
        "name": "決勝 Design",
    }]
    loaded = designer_service.load_project("決勝-design")
    assert loaded["project"]["name"] == "決勝 Design"
    assert loaded["assets"]["assets/frame.png"].startswith(
        "data:image/png;base64,")


def test_save_and_load_schema_v2_project(designer_service):
    project = make_v2_project("Animated Finals")
    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    loaded = designer_service.load_project(result["slug"])["project"]

    assert loaded["schemaVersion"] == 2
    assert loaded["layers"][0]["animations"]["intro"]["renderer"] == "webAnimations"
    assert loaded["layers"][0]["animations"]["update"]["keyframes"][0]["x"] == -80


def test_save_and_load_schema_v3_project(designer_service):
    project = make_v3_project("Event Finals")
    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    loaded = designer_service.load_project(result["slug"])["project"]

    assert loaded["schemaVersion"] == 3
    assert loaded["id"] == "project-v3"
    assert loaded["layers"][0]["events"]["dataChange"]["swapAtMs"] == 220
    assert loaded["assets"][0]["mediaType"] == "image/png"


def test_schema_v1_project_still_valid(designer_service):
    project = make_project("Legacy")
    designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    loaded = designer_service.load_project("legacy")["project"]

    assert loaded["schemaVersion"] == 1


def test_save_and_load_preserves_figma_reflection_flags(designer_service):
    project = make_project("Flipped")
    project["layers"][0]["flipX"] = True
    project["layers"][0]["flipY"] = False
    project["layers"][0]["assetTransformApplied"] = True

    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    loaded = designer_service.load_project(result["slug"])["project"]

    assert loaded["layers"][0]["flipX"] is True
    assert loaded["layers"][0]["flipY"] is False
    assert loaded["layers"][0]["assetTransformApplied"] is True


def test_layer_semantics_are_left_to_model_ts(designer_service):
    # What a project means is judged by web/src/ScoreboardDesigner/model.ts
    # alone (model.test.js pins the rejections); the service stores what is
    # safe to store. Re-adding these checks here would fork that rule. Only
    # values with no bearing on safety are pinned as accepted: layer ids and
    # types are left open, so the service may refuse them later if that
    # ever becomes a safety question.
    project = make_v3_project("Semantics Elsewhere")
    project["canvas"]["width"] = 1280
    layer = project["layers"][0]
    layer["opacity"] = 7
    layer["events"]["sceneEnter"]["easing"]["x1"] = 1.5
    layer["gameHistoryStyle"] = {"mode": "parts", "parts": [
        {"path": [0], "role": "set", "setIndex": 0},
        {"path": [0], "role": "winner"},
    ]}
    # The manifest's mediaType is not trusted either: the extension decides
    # how the bytes are checked and served.
    project["assets"][0].update({
        "path": "assets/frame.svg", "mediaType": "image/png"})
    layer["asset"] = "assets/frame.svg"

    saved = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.svg": svg_data_url()},
    })
    loaded = designer_service.load_project(saved["slug"])

    assert loaded["project"]["canvas"]["width"] == 1280
    assert loaded["assets"]["assets/frame.svg"].startswith(
        "data:image/svg+xml;base64,")


@pytest.mark.parametrize("version", [None, 0, 4, "3", 3.5, True])
def test_rejects_unknown_schema_versions(designer_service, version):
    project = make_v3_project()
    project["schemaVersion"] = version
    with pytest.raises(DesignerProjectError, match="schemaVersion"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.png": png_data_url()},
        })


def test_rejects_projects_over_the_size_limit(designer_service, monkeypatch):
    monkeypatch.setattr(AutonaDesignerService, "MAX_PROJECT_BYTES", 4096)
    project = make_project("Large", with_image=False)
    project["layers"].append({"id": "note", "type": "data-text",
                              "text": "x" * 4096})
    with pytest.raises(DesignerProjectError, match="サイズ上限"):
        designer_service.receive_import({"project": project, "assets": {}})


def test_rejects_more_layers_than_the_limit(designer_service):
    project = make_project("Crowded", with_image=False)
    project["layers"] = [
        {"id": f"text-{index}", "type": "data-text", "text": ""}
        for index in range(AutonaDesignerService.MAX_LAYERS + 1)]
    with pytest.raises(DesignerProjectError, match="レイヤー数"):
        designer_service.save_project({"project": project, "assets": {}})

    project["layers"] = ["not a layer"]
    with pytest.raises(DesignerProjectError, match="不正なレイヤー"):
        designer_service.save_project({"project": project, "assets": {}})


def _with_asset_path(kind, path):
    if kind == "v3 manifest":
        project = make_v3_project()
        project["assets"][0]["path"] = path
        return project
    if kind in ("v1 image layer", "v3 image layer"):
        # v3: the manifest still lists a valid file; only the layer -- the
        # path the runtime actually loads -- points elsewhere.
        project = make_project() if kind.startswith("v1") else make_v3_project()
        project["layers"][0]["asset"] = path
        return project
    project = (make_project(with_image=False) if kind.startswith("v1")
               else make_v3_project(with_image=False))
    project["layers"].append({
        "id": "games", "type": "game-history", "binding": "games",
        "slots": [{"asset": "assets/game.png"}, {"asset": path}],
    })
    return project


ASSET_PATH_KINDS = [
    "v3 manifest", "v1 image layer", "v1 game-history slot",
    "v3 image layer", "v3 game-history slot"]

UNSAFE_ASSET_PATHS = [
    "../outside.png", "assets/../../outside.png", "assets/sub/frame.png",
    "assets\\frame.png", "/frame.png", "C:/frame.png", "frame.png",
    "assets/frame.gif", "assets/.png", None,
    "https://example.com/assets/frame.png",
    # A drive or an alternate data stream.
    "assets/C:frame.png", "assets/frame.png:hidden.png",
    # Control characters.
    "assets/fr\x00ame.png", "assets/fr\name.png", "assets/fr\x7fame.svg",
    "assets/fr\x85ame.png",
    # DOS device names, with an extension and any case.
    "assets/CON.png", "assets/con.svg", "assets/Nul.tar.png",
    "assets/aux .png", "assets/PRN.png", "assets/COM1.png",
    "assets/lpt9.svg", "assets/COM¹.png", "assets/CONIN$.png",
    # A dot or space right before the extension.
    "assets/frame .png", "assets/frame..png", "assets/ .svg",
]


@pytest.mark.parametrize("kind", ASSET_PATH_KINDS)
@pytest.mark.parametrize("path", UNSAFE_ASSET_PATHS)
def test_rejects_asset_references_outside_assets(designer_service, kind, path):
    project = _with_asset_path(kind, path)
    with pytest.raises(DesignerProjectError, match="assetパス"):
        designer_service.save_project({"project": project, "assets": {}})
    assert not designer_service.ROOT.exists()
    with pytest.raises(DesignerProjectError, match="assetパス"):
        designer_service.receive_import({"project": project, "assets": {}})


def test_rejects_unsafe_layer_references_in_a_saved_v3_design(
        designer_service):
    # project.json files placed by hand are checked on load as well.
    saved = designer_service.save_project({
        "project": make_v3_project("Hand Edited"),
        "assets": {"assets/frame.png": png_data_url()},
    })
    project_path = designer_service.ROOT / saved["slug"] / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["layers"][0]["asset"] = "../../../secret.png"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    with pytest.raises(DesignerProjectError, match="assetパス"):
        designer_service.load_project(saved["slug"])


@pytest.mark.parametrize("path", [
    "assets/決勝ロゴ.png", "assets/フレーム 1.svg", "assets/console.png",
    "assets/com10.png", "assets/CONTROL.svg", "assets/nul_frame.png",
    "assets/lpt.png", "assets/.hidden.png", "assets/frame.v2.PNG",
    "assets/Vector-183-164.svg",
])
def test_accepts_ordinary_asset_names(designer_service, path):
    project = make_project("Names")
    project["layers"][0]["asset"] = path
    data = (svg_data_url() if path.lower().endswith(".svg")
            else png_data_url())

    saved = designer_service.save_project(
        {"project": project, "assets": {path: data}})

    assert (designer_service.ROOT / saved["slug"] / path).is_file()
    assert set(designer_service.load_project(saved["slug"])["assets"]) == {
        path}


def test_svg_source_is_only_ever_the_decoded_svg(designer_service):
    project = make_v3_project("Inline SVG")
    style = {
        "slotIndex": 0, "p1Color": "#dc143c", "p2Color": "#3d8be8",
        "neutralColor": "#f0eae0", "strokeWidth": 3,
        "svgSource": "<svg><script>alert(1)</script></svg>",
    }
    project["layers"][0].update({
        "asset": "assets/frame.svg", "gameHistoryStyle": dict(style)})
    project["assets"][0].update({
        "path": "assets/frame.svg", "mediaType": "image/svg+xml"})
    # Neither of these has a decoded SVG to inline, so neither keeps one.
    project["layers"].append({
        "id": "png", "type": "image", "asset": "assets/frame.svg.png",
        "gameHistoryStyle": dict(style)})
    project["assets"].append({
        "id": "png-asset", "kind": "image", "path": "assets/frame.svg.png",
        "mediaType": "image/png"})
    project["layers"].append({
        "id": "text", "type": "data-text", "gameHistoryStyle": dict(style)})

    saved = designer_service.save_project({
        "project": project,
        "assets": {
            "assets/frame.svg": svg_data_url(),
            "assets/frame.svg.png": png_data_url(),
        },
    })

    layers = {layer["id"]: layer
              for layer in designer_service.load_project(
                  saved["slug"])["project"]["layers"]}
    decoded = base64.b64decode(svg_data_url().split(",", 1)[1]).decode()
    assert layers["frame"]["gameHistoryStyle"]["svgSource"] == decoded
    assert "svgSource" not in layers["png"]["gameHistoryStyle"]
    assert "svgSource" not in layers["text"]["gameHistoryStyle"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nan_and_infinity_before_storing(designer_service, value):
    project = make_project("Not A Number")
    project["layers"][0]["x"] = value
    payload = {"project": project,
               "assets": {"assets/frame.png": png_data_url()}}

    with pytest.raises(DesignerProjectError, match="NaN"):
        designer_service.save_project(payload)
    assert not designer_service.ROOT.exists()
    with pytest.raises(DesignerProjectError, match="NaN"):
        designer_service.receive_import(payload)
    with pytest.raises(FileNotFoundError):
        designer_service.latest_import()


def test_the_size_limit_bounds_saves_and_imports_but_not_loads(
        designer_service, monkeypatch):
    saved = designer_service.save_project({
        "project": make_project("Hand Grown"),
        "assets": {"assets/frame.png": png_data_url()},
    })
    project_path = designer_service.ROOT / saved["slug"] / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["layers"][0]["note"] = "x" * 8192
    project_path.write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setattr(AutonaDesignerService, "MAX_PROJECT_BYTES", 4096)

    # A larger project.json placed by hand still opens ...
    assert designer_service.load_project(saved["slug"])["project"][
        "layers"][0]["note"] == "x" * 8192
    # ... but is not written back, nor held as an import.
    payload = {"project": project,
               "assets": {"assets/frame.png": png_data_url()}}
    with pytest.raises(DesignerProjectError, match="サイズ上限"):
        designer_service.save_project(payload)
    with pytest.raises(DesignerProjectError, match="サイズ上限"):
        designer_service.receive_import(payload)
    assert json.loads(project_path.read_text(encoding="utf-8")) == project


def _unlink_tree(link):
    # A Windows junction is a directory entry (rmdir); a symlink is unlinked.
    if link.is_symlink():
        os.unlink(link)
    else:
        os.rmdir(link)


def test_rejects_an_assets_folder_linked_outside_the_design(
        designer_service, tmp_path):
    from tests.conftest import _link_tree

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\noutside")
    payload = {
        "project": make_project("Linked"),
        "assets": {"assets/frame.png": png_data_url()},
    }
    saved = designer_service.save_project(payload)
    assets = designer_service.ROOT / saved["slug"] / "assets"
    shutil.rmtree(assets)
    _link_tree(assets, outside)
    try:
        with pytest.raises(DesignerProjectError, match="外を指して"):
            designer_service.load_project(saved["slug"])
        with pytest.raises(
                DesignerProjectError, match="外を指して|シンボリックリンク"):
            designer_service.save_project(payload)
    finally:
        _unlink_tree(assets)
    assert (outside / "frame.png").read_bytes() == (
        b"\x89PNG\r\n\x1a\noutside")


def test_save_and_load_preserves_optional_editor_metadata(designer_service):
    project = make_project("Grouped", with_image=False)
    project["layers"].append({
        "id": "name",
        "name": "P1 Name",
        "type": "data-text",
        "binding": "p1.name",
        "groupId": "group-main",
        "x": 100,
        "y": 100,
        "width": 400,
        "height": 80,
        "rotation": 0,
        "opacity": 1,
        "zIndex": 1,
        "visible": True,
        "animation": {
            "preset": "none",
            "durationMs": 0,
            "delayMs": 0,
        },
        "typography": {
            "fontFamily": "sans-serif",
            "fontSize": 48,
            "fontWeight": 700,
            "color": "#ffffff",
            "align": "left",
            "italic": False,
            "letterSpacing": 0,
        },
    })
    project["groups"] = [{
        "id": "group-main",
        "name": "Main",
        "collapsed": False,
    }]
    project["autoLayouts"] = [{
        "id": "layout-main",
        "name": "Main layout",
        "layerIds": ["name"],
        "direction": "row",
        "padding": 16,
        "gap": 8,
        "align": "center",
    }]

    designer_service.save_project({"project": project, "assets": {}})
    loaded = designer_service.load_project("grouped")["project"]

    assert loaded["layers"][0]["groupId"] == "group-main"
    assert loaded["groups"][0]["name"] == "Main"
    assert loaded["autoLayouts"][0]["layerIds"] == ["name"]


def test_save_and_load_preserves_meta(designer_service):
    # meta (format / game) is optional within schemaVersion 3; model.ts sends
    # it trimmed and leaves unset fields out.
    project = make_v3_project("Meta Finals")
    project["meta"] = {"format": "クルーバトル", "game": "スト6"}
    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    stored = json.loads((designer_service.ROOT / result["slug"] /
                         "project.json").read_text(encoding="utf-8"))
    assert stored["meta"] == {"format": "クルーバトル", "game": "スト6"}
    loaded = designer_service.load_project(result["slug"])["project"]
    assert loaded["schemaVersion"] == 3
    assert loaded["meta"] == {"format": "クルーバトル", "game": "スト6"}


@pytest.mark.parametrize("meta", ["1v1", ["1v1"], None, 3])
def test_rejects_meta_that_is_not_an_object(designer_service, meta):
    project = make_v3_project()
    project["meta"] = meta
    with pytest.raises(DesignerProjectError, match="meta"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.png": png_data_url()},
        })


def test_meta_fields_are_left_to_model_ts(designer_service):
    # Only meta's type is a safety question here: its fields (strings, their
    # length, trimming) are model.ts's call, and the catalog shows nothing
    # but a non-empty string from them (test_layout_catalog.py).
    project = make_v3_project("Meta Elsewhere")
    project["meta"] = {"format": 3, "game": "x" * 500, "extra": {"a": 1}}
    saved = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })
    loaded = designer_service.load_project(saved["slug"])["project"]
    assert loaded["meta"]["game"] == "x" * 500


def test_delete_project_removes_its_entire_directory(designer_service):
    designer_service.save_project({
        "project": make_project("Finals"),
        "assets": {"assets/frame.png": png_data_url()},
    })

    result = designer_service.delete_project("finals")

    assert result == {"slug": "finals"}
    assert not (designer_service.ROOT / "finals").exists()
    assert designer_service.list_projects() == []


def test_delete_project_rejects_missing_and_invalid_directories(
        designer_service):
    with pytest.raises(FileNotFoundError, match="見つかりません"):
        designer_service.delete_project("missing")

    invalid_directory = designer_service.ROOT / "not-a-project"
    invalid_directory.mkdir(parents=True)
    (invalid_directory / "unrelated.txt").write_text(
        "keep", encoding="utf-8")
    with pytest.raises(DesignerProjectError, match="形式が不正"):
        designer_service.delete_project("not-a-project")
    assert invalid_directory.is_dir()


def test_rejects_paths_and_non_image_assets(designer_service):
    project = make_project()
    project["layers"][0]["asset"] = "../outside.png"
    with pytest.raises(DesignerProjectError, match="asset"):
        designer_service.save_project({"project": project, "assets": {}})

    project = make_project()
    bad_asset = "data:image/png;base64," + base64.b64encode(
        b"not-png").decode("ascii")
    with pytest.raises(DesignerProjectError, match="PNG"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.png": bad_asset},
        })


def test_receive_import_keeps_latest_project_without_writing_files(
        designer_service):
    project = make_project("Figma Finals")
    result = designer_service.receive_import({
        "project": project,
        "assets": {"assets/frame.png": png_data_url()},
    })

    assert result["status"] == "ok"
    assert result["name"] == "Figma Finals"
    assert result["layers"] == 1
    assert len(result["importId"]) == 32
    assert designer_service.get_import(result["importId"])["project"]["name"] == (
        "Figma Finals"
    )
    assert designer_service.latest_import()["project"]["name"] == "Figma Finals"
    assert designer_service.latest_import()["assets"]["assets/frame.png"].startswith(
        "data:image/png;base64,")
    assert not (designer_service.ROOT / "figma-finals").exists()


def test_latest_import_rejects_missing_import(designer_service):
    with pytest.raises(FileNotFoundError, match="インポート"):
        designer_service.latest_import()


def test_receive_import_uses_project_and_png_validation(designer_service):
    project = make_project()
    project["schemaVersion"] = 4
    with pytest.raises(DesignerProjectError, match="schemaVersion"):
        designer_service.receive_import({
            "project": project,
            "assets": {"assets/frame.png": png_data_url()},
        })

    project = make_project()
    with pytest.raises(DesignerProjectError, match="PNG"):
        designer_service.receive_import({
            "project": project,
            "assets": {
                "assets/frame.png": "data:image/png;base64," +
                base64.b64encode(b"bad").decode("ascii"),
            },
        })


def test_save_and_receive_import_support_svg_assets(designer_service):
    project = make_project("SVG Finals")
    project["layers"][0]["asset"] = "assets/frame.svg"
    project["layers"][0]["gameHistoryStyle"] = {
        "slotIndex": 0,
        "p1Color": "#dc143c",
        "p2Color": "#3d8be8",
        "neutralColor": "#f0eae0",
        "strokeWidth": 3,
    }
    svg_project = make_v3_project("SVG Finals")
    svg_project["layers"][0]["asset"] = "assets/frame.svg"
    svg_project["layers"][0]["gameHistoryStyle"] = project["layers"][0][
        "gameHistoryStyle"
    ]
    svg_project["assets"][0].update({
        "path": "assets/frame.svg",
        "mediaType": "image/svg+xml",
    })

    saved = designer_service.save_project({
        "project": svg_project,
        "assets": {"assets/frame.svg": svg_data_url()},
    })
    loaded = designer_service.load_project(saved["slug"])
    assert loaded["assets"]["assets/frame.svg"].startswith(
        "data:image/svg+xml;base64,"
    )
    assert loaded["project"]["layers"][0]["gameHistoryStyle"][
        "svgSource"
    ].startswith("<svg")

    imported = designer_service.receive_import({
        "project": project,
        "assets": {"assets/frame.svg": svg_data_url()},
    })
    assert designer_service.get_import(imported["importId"])["assets"][
        "assets/frame.svg"
    ].startswith("data:image/svg+xml;base64,")


def test_save_and_load_preserves_svg_game_history_parts(designer_service):
    project = make_v3_project("SVG Game History Parts")
    project["layers"][0]["asset"] = "assets/frame.svg"
    project["layers"][0]["gameHistoryStyle"] = {
        "slotIndex": 0,
        "mode": "parts",
        "parts": [
            {"path": [], "role": "p1-set", "setIndex": 0},
            {"path": [1], "role": "p2-set", "setIndex": 2},
            {"path": [2], "role": "winner"},
            {"path": [3], "role": "p1-set", "setIndex": 2},
            {"path": [3], "role": "p2-set", "setIndex": 2},
        ],
        "p1Color": "#dc143c",
        "p2Color": "#3d8be8",
        "neutralColor": "#f0eae0",
        "strokeWidth": 3,
    }
    project["assets"][0].update({
        "path": "assets/frame.svg",
        "mediaType": "image/svg+xml",
    })

    result = designer_service.save_project({
        "project": project,
        "assets": {"assets/frame.svg": svg_data_url()},
    })

    loaded = designer_service.load_project(result["slug"])["project"]
    expect = loaded["layers"][0]["gameHistoryStyle"]
    assert expect["mode"] == "parts"
    assert expect["parts"] == [
        {"path": [], "role": "p1-set", "setIndex": 0},
        {"path": [1], "role": "p2-set", "setIndex": 2},
        {"path": [2], "role": "winner"},
        {"path": [3], "role": "p1-set", "setIndex": 2},
        {"path": [3], "role": "p2-set", "setIndex": 2},
    ]


def test_save_and_receive_import_support_five_game_history_svgs(
        designer_service):
    project = make_project("Custom Game History", with_image=False)
    project["layers"] = [{
        "id": "games",
        "name": "Game-History",
        "type": "game-history",
        "binding": "games",
        "x": 100,
        "y": 200,
        "width": 500,
        "height": 100,
        "rotation": 0,
        "opacity": 1,
        "zIndex": 1,
        "visible": True,
        "gap": 10,
        "neutralColor": "#252524",
        "p1Color": "#dc143c",
        "p2Color": "#3d8be8",
        "borderColor": "#f0eae0",
        "borderWidth": 2,
        "radius": 8,
        "slots": [{
            "asset": f"assets/game-{index}.svg",
            "x": index * 100,
            "y": 0,
            "width": 80,
            "height": 80,
        } for index in range(1, 6)],
    }]
    assets = {
        f"assets/game-{index}.svg": svg_data_url()
        for index in range(1, 6)
    }

    saved = designer_service.save_project({"project": project, "assets": assets})
    loaded = designer_service.load_project(saved["slug"])
    assert set(loaded["assets"]) == set(assets)
    assert len(loaded["project"]["layers"][0]["slots"]) == 5

    imported = designer_service.receive_import({
        "project": project,
        "assets": assets,
    })
    assert designer_service.get_import(imported["importId"])["assets"].keys() == (
        assets.keys()
    )


def test_rejects_unsafe_svg_asset(designer_service):
    project = make_project("Unsafe SVG")
    project["layers"][0]["asset"] = "assets/frame.svg"
    unsafe = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    )
    encoded = "data:image/svg+xml;base64," + base64.b64encode(unsafe).decode("ascii")
    with pytest.raises(DesignerProjectError, match="実行可能"):
        designer_service.receive_import({
            "project": project,
            "assets": {"assets/frame.svg": encoded},
        })


def test_import_authorization_is_loopback_only(designer_service):
    assert designer_service.is_loopback("127.0.0.1")
    assert designer_service.is_loopback("::1")
    assert not designer_service.is_loopback("192.168.1.20")


@pytest.mark.parametrize("origin, allowed", [
    (None, True),                     # not a browser page (curl, a script)
    ("null", True),                   # the plugin UI's opaque-origin iframe
    ("https://www.figma.com", True),
    ("https://evil.example", False),
    ("http://localhost:3100", False),  # e.g. the Web UI dev server
    ("http://127.0.0.1:5107", False),  # pages served by Autona itself
    ("https://figma.com", False),
    ("https://www.figma.com.evil.example", False),
    ("NULL", False),
    ("", False),
])
def test_import_origins(designer_service, origin, allowed):
    assert designer_service.is_import_origin_allowed(origin) is allowed


@pytest.fixture
def import_client(designer_service, monkeypatch):
    """The real import route, with the SocketIO broadcast recorded."""
    from src.AutonaWebServer import WebServer, _build_web_app

    emitted = []
    monkeypatch.setattr(WebServer, "socketio", types.SimpleNamespace(
        emit=lambda event, data: emitted.append(event)))
    app, _socketio = _build_web_app()
    client = app.test_client()
    client.emitted = emitted
    return client


def _cors_headers(response):
    return {key: value for key, value in response.headers.items()
            if key.lower().startswith("access-control-")}


def _import_payload():
    return {"project": make_project("Figma Finals"),
            "assets": {"assets/frame.png": png_data_url()}}


@pytest.mark.parametrize("origin", ["null", "https://www.figma.com"])
def test_the_figma_plugin_can_post_an_import(
        designer_service, import_client, origin):
    # What tools/figma-scoreboard-plugin/ui.html sends: a JSON POST, so the
    # browser preflights it first. The plugin UI's origin is "null".
    preflight = import_client.options("/api/designer/imports", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == origin
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    assert "content-type" in preflight.headers[
        "Access-Control-Allow-Headers"].lower()

    response = import_client.post(
        "/api/designer/imports", json=_import_payload(),
        headers={"Origin": origin})
    assert response.status_code == 200, response.get_json()
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.get_json()["layers"] == 1
    assert import_client.emitted == ["designer_import_received"]
    assert designer_service.latest_import()["project"]["name"] == (
        "Figma Finals")


def test_an_import_without_an_origin_is_not_a_browser_page(
        designer_service, import_client):
    response = import_client.post(
        "/api/designer/imports", json=_import_payload())
    assert response.status_code == 200
    assert _cors_headers(response) == {}
    assert import_client.emitted == ["designer_import_received"]


@pytest.mark.parametrize("origin", [
    "https://evil.example", "http://localhost:3100", "http://127.0.0.1:5107"])
def test_other_pages_cannot_post_an_import(
        designer_service, import_client, origin):
    # Every page in a browser on this computer is a loopback peer, and
    # CORS(app) answers any origin elsewhere. Here a foreign page gets a
    # 403 and no CORS headers, on the preflight and on the POST itself
    # (a form post or a no-cors fetch skips the preflight).
    preflight = import_client.options("/api/designer/imports", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 403
    assert _cors_headers(preflight) == {}

    response = import_client.post(
        "/api/designer/imports", json=_import_payload(),
        headers={"Origin": origin})
    assert response.status_code == 403
    assert _cors_headers(response) == {}
    assert import_client.emitted == []
    with pytest.raises(FileNotFoundError):
        designer_service.latest_import()


def test_an_import_from_another_computer_is_refused(
        designer_service, import_client):
    response = import_client.post(
        "/api/designer/imports", json=_import_payload(),
        headers={"Origin": "null"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403
    assert import_client.emitted == []
    with pytest.raises(FileNotFoundError):
        designer_service.latest_import()


# --- Shared runtime (layout-custom-unification Step 2) ----------------------


def make_legacy_design(root, slug):
    """A design as saved before the shared runtime: private runtime copy."""
    directory = root / slug
    (directory / "assets").mkdir(parents=True)
    files = {
        "index.html": LEGACY_INDEX_HTML.encode("utf-8"),
        "runtime.js": f"/* frozen runtime of {slug} */".encode("utf-8"),
        "runtime.css": b"body { opacity: 0; }",
        "project.json": json.dumps(
            make_project(slug, with_image=False),
            ensure_ascii=False).encode("utf-8"),
        "assets/frame.png": b"\x89PNG\r\n\x1a\nlegacy",
    }
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory, files


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


@pytest.fixture
def warnings_logged():
    messages = []
    handler = logger.add(
        lambda message: messages.append(message.record["message"]),
        level="WARNING", format="{message}")
    yield messages
    logger.remove(handler)


def test_save_writes_only_the_stub_project_and_assets(designer_service):
    result = designer_service.save_project({
        "project": make_project("Finals"),
        "assets": {"assets/frame.png": png_data_url()},
    })

    directory = designer_service.ROOT / result["slug"]
    assert set(snapshot(directory)) == {
        "index.html", "project.json", "assets/frame.png"}
    assert (directory / "index.html").read_bytes() == (
        designer_service.STUB_INDEX_HTML.encode("utf-8"))


def test_stub_loads_the_shared_runtime_by_relative_path():
    stub = AutonaDesignerService.STUB_INDEX_HTML
    references = re.findall(r'(?:src|href)="([^"]+)"', stub)
    # Relative only: OBS opens the stub as a local file (file://) as well as
    # over http, and both must resolve to the same shipped files.
    assert references
    assert not [ref for ref in references
                if ref.startswith(("/", "http:", "https:", "file:"))]

    design = PurePosixPath("layout/custom/some-design")
    resolved = {posixpath.normpath(str(design / ref)) for ref in references}
    assert {
        "layout/designer_runtime/runtime.js",
        "layout/designer_runtime/runtime.css",
    } <= resolved
    for path in resolved:
        assert (PROJECT_ROOT / path).is_file(), f"stub points at missing {path}"
    assert "./runtime." not in stub

    # The DOM the runtime renders into must survive the move to a stub.
    runtime = (PROJECT_ROOT / "layout/designer_runtime/runtime.js").read_text(
        encoding="utf-8")
    for element_id in ("scoreboard-composer", "composer-error"):
        assert f'id="{element_id}"' in stub
        assert f'getElementById("{element_id}")' in runtime


def test_migrate_parks_the_private_runtime_and_is_idempotent(
        designer_service, warnings_logged):
    directory, files = make_legacy_design(designer_service.ROOT, "shibuya")

    assert designer_service.MigrateLegacyProjects() == ["shibuya"]
    # An untouched entry point is replaced silently (INFO summary only).
    assert warnings_logged == []
    migrated = snapshot(directory)
    assert migrated == {
        "index.html": designer_service.STUB_INDEX_HTML.encode("utf-8"),
        # project.json and assets/ are never touched: the shared runtime
        # migrates older schemaVersions at load time.
        "project.json": files["project.json"],
        "assets/frame.png": files["assets/frame.png"],
        "_legacy/index.html": files["index.html"],
        "_legacy/runtime.js": files["runtime.js"],
        "_legacy/runtime.css": files["runtime.css"],
    }

    assert designer_service.MigrateLegacyProjects() == []
    assert snapshot(directory) == migrated
    assert designer_service.list_projects() == [
        {"slug": "shibuya", "name": "shibuya"}]


def test_migrate_leaves_everything_else_alone(designer_service):
    root = designer_service.ROOT
    saved = designer_service.save_project({
        "project": make_project("Already Shared"),
        "assets": {"assets/frame.png": png_data_url()},
    })
    hand_made = root / "hand-made"
    hand_made.mkdir()
    (hand_made / "index.html").write_bytes(b"<script src=./runtime.js>")
    (hand_made / "runtime.js").write_bytes(b"/* not a designer project */")
    (root / "notes.txt").write_bytes(b"stray file")
    before = snapshot(root)

    assert designer_service.MigrateLegacyProjects() == []
    assert snapshot(root) == before
    assert saved["slug"] in {p["slug"] for p in designer_service.list_projects()}


def test_migrate_finishes_an_interrupted_run_without_losing_the_original(
        designer_service):
    directory, files = make_legacy_design(designer_service.ROOT, "finals")
    # Interrupted after the entry point was swapped, before the old runtime
    # was dropped: _legacy/ holds the original, index.html is already a stub
    # (here with CRLF line endings: a stub is recognised by its marker, not
    # by byte equality).
    (directory / "_legacy").mkdir()
    (directory / "_legacy" / "index.html").write_bytes(files["index.html"])
    (directory / "index.html").write_bytes(
        designer_service.STUB_INDEX_HTML.replace("\n", "\r\n").encode("utf-8"))

    assert designer_service.MigrateLegacyProjects() == ["finals"]
    assert (directory / "_legacy" / "index.html").read_bytes() == (
        files["index.html"])
    assert (directory / "_legacy" / "runtime.js").read_bytes() == (
        files["runtime.js"])
    assert not list(directory.glob("runtime.*"))


def test_migrate_keeps_the_first_backup(designer_service):
    directory, files = make_legacy_design(designer_service.ROOT, "finals")
    legacy = directory / "_legacy"
    legacy.mkdir()
    first = {
        "index.html": b"<!-- first index -->",
        "runtime.js": b"/* first runtime */",
    }
    for name, data in first.items():
        (legacy / name).write_bytes(data)

    assert designer_service.MigrateLegacyProjects() == ["finals"]
    assert (legacy / "index.html").read_bytes() == first["index.html"]
    assert (legacy / "runtime.js").read_bytes() == first["runtime.js"]
    # Files without an earlier backup are still parked.
    assert (legacy / "runtime.css").read_bytes() == files["runtime.css"]
    assert not list(directory.glob("runtime.*"))


def test_replacing_a_hand_edited_entry_point_is_a_warning(
        designer_service, warnings_logged):
    root = designer_service.ROOT
    edited_dir, files = make_legacy_design(root, "edited")
    edited = files["index.html"].replace(
        b"</head>", b"<style>body { zoom: 2; }</style>\n  </head>")
    (edited_dir / "index.html").write_bytes(edited)
    untouched_dir, _ = make_legacy_design(root, "untouched")
    # Same template with CRLF line endings still counts as untouched.
    (untouched_dir / "index.html").write_bytes(
        LEGACY_INDEX_HTML.replace("\n", "\r\n").encode("utf-8"))

    assert designer_service.MigrateLegacyProjects() == ["edited", "untouched"]
    assert (edited_dir / "_legacy" / "index.html").read_bytes() == edited
    hand_edited = [m for m in warnings_logged if "hand-edited" in m]
    assert len(hand_edited) == 1
    assert str(edited_dir) in hand_edited[0]


@pytest.mark.parametrize("missing", AutonaDesignerService.RUNTIME_FILES)
def test_migrate_waits_for_the_shared_runtime(
        designer_service, warnings_logged, missing):
    # e.g. only Autona.exe and _internal/ were replaced by hand: stubbing the
    # designs now would turn every one of them blank in OBS.
    (designer_service.RUNTIME_ROOT / missing).unlink()
    directory, files = make_legacy_design(designer_service.ROOT, "finals")

    assert designer_service.MigrateLegacyProjects() == []
    assert snapshot(directory) == files
    assert [m for m in warnings_logged if "shared runtime missing" in m]


@pytest.mark.parametrize("missing", AutonaDesignerService.RUNTIME_FILES)
def test_save_refuses_without_the_shared_runtime(designer_service, missing):
    (designer_service.RUNTIME_ROOT / missing).unlink()

    with pytest.raises(DesignerProjectError, match="OBSランタイム"):
        designer_service.save_project({
            "project": make_project("Finals"),
            "assets": {"assets/frame.png": png_data_url()},
        })
    assert not designer_service.ROOT.exists()


def test_migrate_never_raises_and_a_failed_design_keeps_rendering(
        designer_service, monkeypatch):
    broken, broken_files = make_legacy_design(designer_service.ROOT, "broken")
    make_legacy_design(designer_service.ROOT, "healthy")
    original_write = AutonaDesignerService._atomic_write

    def failing_write(path, data):
        if "broken" in path.parts:
            raise OSError("disk full")
        original_write(path, data)

    monkeypatch.setattr(
        AutonaDesignerService, "_atomic_write", staticmethod(failing_write))

    assert designer_service.MigrateLegacyProjects() == ["healthy"]
    # Nothing was swapped, so the design still loads its own runtime copy.
    assert snapshot(broken) == broken_files


def test_migrate_survives_an_unreadable_root(designer_service, monkeypatch):
    class UnreadableRoot:
        def is_dir(self):
            return True

        def iterdir(self):
            raise PermissionError("denied")

    monkeypatch.setattr(AutonaDesignerService, "ROOT", UnreadableRoot())
    assert designer_service.MigrateLegacyProjects() == []


def test_saving_over_a_legacy_design_keeps_its_rollback_copy(designer_service):
    directory, files = make_legacy_design(designer_service.ROOT, "finals")

    designer_service.save_project({
        "project": make_project("Finals"),
        "assets": {"assets/frame.png": png_data_url()},
    })

    assert (directory / "_legacy" / "index.html").read_bytes() == (
        files["index.html"])
    assert not list(directory.glob("runtime.*"))
    assert (directory / "index.html").read_bytes() == (
        designer_service.STUB_INDEX_HTML.encode("utf-8"))


_BOOTSTRAP_SCRIPT = r'''
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])

from qtpy.QtWidgets import QApplication

# AutonaScoreboardManager (built by bootstrap_core) is a QWidget.
app = QApplication([])

from src.bootstrap import bootstrap_core

bootstrap_core()
print("<<<BOOTSTRAPPED>>>")
'''


def test_bootstrap_core_migrates_legacy_designs(tmp_path):
    # A real, fresh boot in a throwaway working directory: bootstrap_core()
    # runs once per process, and the session harness has already used it up.
    from tests.conftest import _LINKED_TREES, _link_tree

    for relative in _LINKED_TREES:
        link = tmp_path / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        _link_tree(link, PROJECT_ROOT / relative)
    for relative in ("out", "user_data", "user_data/games", "tmp"):
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    runtime = tmp_path / "layout" / "designer_runtime"
    runtime.mkdir(parents=True)
    for name in AutonaDesignerService.RUNTIME_FILES:
        (runtime / name).write_bytes(b"/* shared runtime */")
    directory, files = make_legacy_design(
        tmp_path / "layout" / "custom", "finals")

    env = dict(os.environ)
    env.update({
        "QT_API": "pyside6",
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", _BOOTSTRAP_SCRIPT, str(PROJECT_ROOT)],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
    finally:
        # Break the junctions before tmp cleanup can descend through them
        # into the real assets/ and src/ trees (see tests/conftest.py).
        for relative in reversed(_LINKED_TREES):
            link = tmp_path / relative
            try:
                if link.is_symlink():
                    os.unlink(link)
                else:
                    os.rmdir(link)
            except OSError:
                pass

    assert result.returncode == 0 and "<<<BOOTSTRAPPED>>>" in result.stdout, (
        f"bootstrap failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert (directory / "_legacy" / "runtime.js").read_bytes() == (
        files["runtime.js"])
    assert AutonaDesignerService.STUB_MARKER in (
        directory / "index.html").read_text(encoding="utf-8")
    assert not list(directory.glob("runtime.*"))


def test_designer_files_are_served_no_store(tmp_path, monkeypatch):
    from src.AutonaWebServer import _build_web_app

    for relative in (
            "layout/designer_runtime/runtime.js",
            "layout/custom/finals/index.html",
            "layout/scoreboard_simple/index.js"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    app, _socketio = _build_web_app()
    client = app.test_client()

    def cache_control(url):
        response = client.get(url)
        try:
            assert response.status_code == 200, url
            return response.headers.get("Cache-Control", "")
        finally:
            response.close()

    # An app update replaces the shared runtime under every design; OBS must
    # not keep a day-old copy of it.
    assert "no-store" in cache_control("/layout/designer_runtime/runtime.js")
    assert "no-store" in cache_control("/layout/custom/finals/index.html")
    assert "no-store" not in cache_control("/layout/scoreboard_simple/index.js")
    if sys.platform == "win32":
        # The Windows filesystem serves the same files under any casing.
        assert "no-store" in cache_control(
            "/Layout/Designer_Runtime/runtime.js")
        assert "no-store" in cache_control("/LAYOUT/Custom/finals/index.html")
