import base64

import pytest

from .conftest import direct_import


designer_module = direct_import(
    "AutonaDesignerService_under_test",
    "src/AutonaDesignerService.py",
)
AutonaDesignerService = designer_module.AutonaDesignerService
DesignerProjectError = designer_module.DesignerProjectError


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


@pytest.fixture
def designer_service(tmp_path, monkeypatch):
    root = tmp_path / "layout" / "custom"
    template = tmp_path / "runtime"
    template.mkdir()
    for name in AutonaDesignerService.TEMPLATE_FILES:
        (template / name).write_text(f"template:{name}", encoding="utf-8")

    monkeypatch.setattr(AutonaDesignerService, "ROOT", root)
    monkeypatch.setattr(
        AutonaDesignerService, "TEMPLATE_CANDIDATES", (template,))
    with AutonaDesignerService._token_lock:
        AutonaDesignerService._tokens.clear()
    return AutonaDesignerService


def png_data_url():
    data = b"\x89PNG\r\n\x1a\n" + b"test-png"
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


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


def test_rejects_paths_and_non_png_assets(designer_service):
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
