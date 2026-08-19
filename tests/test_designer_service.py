import base64
import json

import pytest

from src.AutonaDesignerService import (
    AutonaDesignerService, DesignerProjectError)


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
    template = tmp_path / "runtime"
    template.mkdir()
    for name in (
            *AutonaDesignerService.TEMPLATE_FILES,
            *AutonaDesignerService.NATIVE_TEMPLATE_FILES):
        target = template / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"template:{name}", encoding="utf-8")

    monkeypatch.setattr(AutonaDesignerService, "ROOT", root)
    monkeypatch.setattr(
        AutonaDesignerService, "TEMPLATE_CANDIDATES", (template,))
    monkeypatch.setattr(
        AutonaDesignerService,
        "LINK_REGISTRY",
        tmp_path / "user_data" / "designer_source_links.json",
    )
    with AutonaDesignerService._token_lock:
        AutonaDesignerService._tokens.clear()
    with AutonaDesignerService._import_lock:
        AutonaDesignerService._latest_import = None
        AutonaDesignerService._latest_import_id = None
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
    assert (directory / "runtime.js").read_text(
        encoding="utf-8") == "template:runtime.js"

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


def test_rejects_invalid_schema_v2_animation(designer_service):
    project = make_v2_project()
    project["layers"][0]["animations"]["intro"]["easing"]["x1"] = 1.5
    with pytest.raises(DesignerProjectError, match="easing"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.png": png_data_url()},
        })

    project = make_v2_project()
    project["layers"][0]["animations"]["intro"]["keyframes"][1]["timeMs"] = 999
    with pytest.raises(DesignerProjectError, match="timeMs"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.png": png_data_url()},
        })


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
    project["canvas"]["width"] = 1280
    with pytest.raises(DesignerProjectError, match="1920"):
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


def test_rejects_invalid_svg_game_history_parts(designer_service):
    project = make_v3_project("Invalid SVG Game History Parts")
    project["layers"][0]["asset"] = "assets/frame.svg"
    project["layers"][0]["gameHistoryStyle"] = {
        "slotIndex": 0,
        "mode": "parts",
        "parts": [
            {"path": [0], "role": "set", "setIndex": 0},
            {"path": [0], "role": "winner"},
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

    with pytest.raises(DesignerProjectError, match="重複"):
        designer_service.save_project({
            "project": project,
            "assets": {"assets/frame.svg": svg_data_url()},
        })


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
