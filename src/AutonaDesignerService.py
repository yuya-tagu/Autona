"""Local-only persistence for the desktop Scoreboard Designer.

The React editor runs inside a QWebEngineView, but it never receives direct
filesystem access. A short-lived token owned by the desktop window authorizes
requests to this service, which writes only below ``layout/custom``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from loguru import logger


class DesignerProjectError(ValueError):
    pass


class AutonaDesignerService:
    ROOT = Path("layout/custom")
    # The shared renderer every stub loads. Writing a stub while it is
    # missing (e.g. only Autona.exe and _internal/ were swapped by hand)
    # would turn every design blank in OBS, so both callers check it first.
    RUNTIME_ROOT = Path("layout/designer_runtime")
    RUNTIME_FILES = ("runtime.js", "runtime.css")
    # Identifies a stub regardless of line endings or later edits to the
    # template below.
    STUB_MARKER = "Autona Designer stub"
    # Every design's entry point is this logic-free stub; the renderer is the
    # single shared runtime in layout/designer_runtime/ (README there). The
    # paths are relative to layout/custom/{slug}/index.html, so the same file
    # works over http (preview) and file:// (OBS local file), and runtime.js
    # keeps resolving ./project.json and assets/ against the design folder.
    STUB_INDEX_HTML = """<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <!-- Autona Designer stub: the renderer is layout/designer_runtime/. -->
    <script>
      window.tshLayoutRootPath = "../..";
      window.tshProgramRootPath = "../../..";
    </script>
    <script src="../../include/globals.js"></script>
    <link rel="stylesheet" href="../../main.css" />
    <link rel="stylesheet" href="../../designer_runtime/runtime.css" />
  </head>
  <body>
    <main id="scoreboard-composer" aria-label="Autona scoreboard"></main>
    <div id="composer-error" role="alert"></div>
    <script src="../../designer_runtime/runtime.js"></script>
  </body>
</html>
"""
    # Designs saved before the shared runtime carried a private copy of these
    # three files; MigrateLegacyProjects() parks them here for rollback.
    LEGACY_DIRNAME = "_legacy"
    LEGACY_FILES = ("index.html", "runtime.js", "runtime.css")
    # The index.html those designs were saved with. Only used to tell an
    # untouched entry point from a hand-edited one when replacing it.
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

    MAX_PROJECT_BYTES = 2 * 1024 * 1024
    MAX_ASSET_BYTES = 50 * 1024 * 1024
    MAX_TOTAL_ASSET_BYTES = 200 * 1024 * 1024
    MAX_ASSETS = 200
    BINDINGS = {
        "p1.name", "p1.sponsorName", "p1.sponsorLogo", "p1.score",
        "p1.countryFlag", "p1.seed", "p1.twitter", "p1.state",
        "p2.name", "p2.sponsorName", "p2.sponsorLogo", "p2.score",
        "p2.countryFlag", "p2.seed", "p2.twitter", "p2.state",
        "tournament.logo", "tournament.name", "match.phase",
        "match.round", "games",
    }
    IMAGE_BINDINGS = {
        "p1.sponsorLogo", "p1.countryFlag",
        "p2.sponsorLogo", "p2.countryFlag",
        "tournament.logo",
    }

    _tokens: set[str] = set()
    _token_lock = threading.Lock()
    _latest_import: dict[str, Any] | None = None
    _imports: dict[str, dict[str, Any]] = {}
    _import_lock = threading.Lock()

    @classmethod
    def issue_token(cls) -> str:
        token = secrets.token_urlsafe(32)
        with cls._token_lock:
            cls._tokens.add(token)
        return token

    @classmethod
    def revoke_token(cls, token: str) -> None:
        with cls._token_lock:
            cls._tokens.discard(token)

    @classmethod
    def is_authorized(cls, token: str | None, remote_addr: str | None) -> bool:
        if not cls.is_loopback(remote_addr) or not token:
            return False
        with cls._token_lock:
            return secrets.compare_digest(
                token, next((item for item in cls._tokens if item == token), ""))

    @staticmethod
    def is_loopback(remote_addr: str | None) -> bool:
        return remote_addr in {
            "127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"
        }

    @staticmethod
    def slugify(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name).strip().casefold()
        slug = re.sub(r"[^\w-]+", "-", normalized, flags=re.UNICODE)
        slug = slug.strip("-_")[:48]
        return slug or "custom"

    @classmethod
    def list_projects(cls) -> list[dict[str, str]]:
        root = cls.ROOT
        if not root.is_dir():
            return []
        projects = []
        for directory in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            project_path = directory / "project.json"
            index_path = directory / "index.html"
            if not project_path.is_file() or not index_path.is_file():
                continue
            try:
                project = json.loads(project_path.read_text(encoding="utf-8"))
                name = str(project.get("name") or directory.name)
            except (OSError, ValueError, TypeError):
                continue
            projects.append({"slug": directory.name, "name": name})
        return projects

    @classmethod
    def load_project(cls, slug: str) -> dict[str, Any]:
        safe_slug = cls._validate_slug(slug)
        directory = cls._project_directory(safe_slug)
        project_path = directory / "project.json"
        if not project_path.is_file():
            raise FileNotFoundError("指定されたデザインが見つかりません。")
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DesignerProjectError("project.jsonを読み込めません。") from exc
        cls._validate_project(project)

        asset_specs = cls._project_asset_specs(project)
        assets: dict[str, str] = {}
        for asset_path, media_type in asset_specs.items():
            file_path = directory / Path(asset_path)
            cls._assert_within(directory, file_path)
            if not file_path.is_file():
                continue
            data = file_path.read_bytes()
            if len(data) > cls.MAX_ASSET_BYTES:
                raise DesignerProjectError(
                    f"{asset_path}がサイズ上限を超えています。")
            assets[asset_path] = (
                f"data:{media_type};base64," +
                base64.b64encode(data).decode("ascii")
            )
        return {"project": project, "assets": assets, "slug": safe_slug}

    @classmethod
    def delete_project(cls, slug: str) -> dict[str, str]:
        safe_slug = cls._validate_slug(slug)
        directory = cls._project_directory(safe_slug)
        if not directory.exists():
            raise FileNotFoundError("指定されたデザインが見つかりません。")
        if directory.is_symlink():
            raise DesignerProjectError("シンボリックリンクは削除できません。")
        if not directory.is_dir() or not (
                directory / "project.json").is_file():
            raise DesignerProjectError("デザインフォルダの形式が不正です。")
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise DesignerProjectError(
                "デザインフォルダを削除できません。") from exc
        return {"slug": safe_slug}

    @classmethod
    def MigrateLegacyProjects(cls) -> list[str]:
        """Switch designs that carry a private runtime copy over to the stub.

        Called once from bootstrap_core(). For every design folder holding a
        runtime.js, its index.html / runtime.js / runtime.css are parked in
        ``_legacy/`` (the rollback copy, docs/operations/runtime-operations.md)
        and the stub index.html is written in their place. project.json and
        assets/ are never touched: the shared runtime migrates older
        schemaVersions at load time.

        Idempotent: the marker is runtime.js in the design folder, which
        nothing writes any more. Never raises -- a design that cannot be
        migrated keeps rendering with its own copy, and startup must not
        depend on user files. Does nothing while the shared runtime is
        missing (see RUNTIME_ROOT). Returns the migrated slugs.
        """
        migrated: list[str] = []
        try:
            root = cls.ROOT
            if not root.is_dir():
                return migrated
            if not cls._shared_runtime_available():
                logger.warning(
                    "designer: shared runtime missing in {}; legacy designs "
                    "keep their own runtime copy (incomplete update?)",
                    cls.RUNTIME_ROOT)
                return migrated
            directories = sorted(
                root.iterdir(), key=lambda item: item.name.casefold())
        except Exception:
            logger.exception("designer: cannot scan {} for migration", cls.ROOT)
            return migrated
        for directory in directories:
            try:
                if cls._migrate_legacy_project(directory):
                    migrated.append(directory.name)
            except Exception:
                logger.exception(
                    "designer: legacy migration failed for {}", directory)
        if migrated:
            logger.info(
                "designer: migrated {} legacy design(s) to the shared runtime: {}",
                len(migrated), ", ".join(migrated))
        return migrated

    @classmethod
    def save_project(cls, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise DesignerProjectError("保存データがオブジェクトではありません。")
        project = payload.get("project")
        assets = payload.get("assets")
        cls._validate_project(project)
        if not isinstance(assets, dict):
            raise DesignerProjectError("assetsがオブジェクトではありません。")
        if not cls._shared_runtime_available():
            raise DesignerProjectError(
                "OBSランタイム（layout/designer_runtime）が見つかりません。")

        slug = cls.slugify(project["name"])
        directory = cls._project_directory(slug)
        if directory.exists() and directory.is_symlink():
            raise DesignerProjectError("シンボリックリンクには保存できません。")
        directory.mkdir(parents=True, exist_ok=True)
        # A legacy design that arrived after startup (e.g. copied in while
        # the app runs) is migrated first, so overwriting its index.html
        # below never loses the rollback copy. Not fatal: the save itself
        # does not depend on it.
        try:
            cls._migrate_legacy_project(directory)
        except Exception:
            logger.exception(
                "designer: legacy migration before save failed for {}",
                directory)
        asset_directory = directory / "assets"
        if asset_directory.exists() and asset_directory.is_symlink():
            raise DesignerProjectError("assetsがシンボリックリンクです。")
        asset_directory.mkdir(exist_ok=True)

        decoded_assets = cls._validate_assets_for_project(project, assets)
        project_to_write = json.loads(json.dumps(project, ensure_ascii=False))
        for layer in project_to_write["layers"]:
            if layer.get("type") != "image":
                continue
            style = layer.get("gameHistoryStyle")
            asset_path = layer.get("asset")
            if not isinstance(style, dict) or not isinstance(asset_path, str):
                continue
            if not asset_path.lower().endswith(".svg"):
                continue
            svg_data = decoded_assets.get(asset_path)
            if svg_data is None:
                continue
            style["svgSource"] = svg_data.decode("utf-8-sig")

        project_bytes = json.dumps(
            project_to_write, ensure_ascii=False, indent=2).encode("utf-8")
        if len(project_bytes) > cls.MAX_PROJECT_BYTES:
            raise DesignerProjectError("project.jsonがサイズ上限を超えています。")

        for asset_path, data in decoded_assets.items():
            destination = directory / Path(asset_path)
            cls._assert_within(directory, destination)
            cls._atomic_write(destination, data)

        cls._atomic_write(
            directory / "index.html", cls.STUB_INDEX_HTML.encode("utf-8"))

        # Write the manifest last so readers never see references to assets
        # that have not finished saving.
        cls._atomic_write(directory / "project.json", project_bytes)
        return {"slug": slug, "path": f"layout/custom/{slug}"}

    @classmethod
    def receive_import(cls, payload: Any) -> dict[str, Any]:
        """Validate and keep a Figma import without writing host files."""
        if not isinstance(payload, dict):
            raise DesignerProjectError("インポートデータがオブジェクトではありません。")
        project = payload.get("project")
        assets = payload.get("assets")
        cls._validate_project(project)
        if not isinstance(assets, dict):
            raise DesignerProjectError("assetsがオブジェクトではありません。")
        decoded_assets = cls._validate_assets_for_project(project, assets)

        project_copy = json.loads(json.dumps(project, ensure_ascii=False))
        asset_copy = {
            path: (
                f"data:{cls._image_media_type(path)};base64," +
                base64.b64encode(data).decode("ascii")
            )
            for path, data in decoded_assets.items()
        }
        import_id = uuid.uuid4().hex
        imported = {
            "importId": import_id,
            "project": project_copy,
            "assets": asset_copy,
        }
        with cls._import_lock:
            cls._imports[import_id] = imported
            cls._latest_import = imported
            while len(cls._imports) > 10:
                cls._imports.pop(next(iter(cls._imports)))
        return {
            "status": "ok",
            "importId": import_id,
            "name": str(project_copy["name"]),
            "layers": len(project_copy["layers"]),
        }

    @classmethod
    def latest_import(cls) -> dict[str, Any]:
        with cls._import_lock:
            if cls._latest_import is None:
                raise FileNotFoundError("Figmaからのインポートはありません。")
            return json.loads(json.dumps(
                cls._latest_import, ensure_ascii=False))

    @classmethod
    def _validate_project(cls, project: Any) -> None:
        if not isinstance(project, dict) or project.get("schemaVersion") not in {1, 2, 3}:
            raise DesignerProjectError("project.jsonの形式が不正です。")
        if not isinstance(project.get("name"), str) or not project["name"].strip():
            raise DesignerProjectError("デザイン名がありません。")
        if len(project["name"]) > 128:
            raise DesignerProjectError("デザイン名が長すぎます。")
        canvas = project.get("canvas")
        if not isinstance(canvas, dict) or (
                canvas.get("width"), canvas.get("height")) != (1920, 1080):
            raise DesignerProjectError("キャンバスは1920×1080である必要があります。")
        layers = project.get("layers")
        if not isinstance(layers, list):
            raise DesignerProjectError("layersが配列ではありません。")
        if len(layers) > 200:
            raise DesignerProjectError("レイヤー数が上限を超えています。")

        ids = set()
        allowed_types = {
            "image", "data-text", "data-image", "game-history",
        }
        asset_by_id: dict[str, dict[str, Any]] = {}
        if project.get("schemaVersion") == 3:
            project_id = project.get("id")
            if not isinstance(project_id, str) or not project_id:
                raise DesignerProjectError("project.idが不正です。")
            project_assets = project.get("assets")
            if not isinstance(project_assets, list):
                raise DesignerProjectError("assetsが配列ではありません。")
            for index, asset in enumerate(project_assets):
                cls._validate_project_asset(asset, index)
                asset_id = asset["id"]
                if asset_id in asset_by_id:
                    raise DesignerProjectError("asset IDが重複しています。")
                asset_by_id[asset_id] = asset
        for layer in layers:
            if not isinstance(layer, dict):
                raise DesignerProjectError("不正なレイヤーがあります。")
            layer_id = layer.get("id")
            if not isinstance(layer_id, str) or not layer_id or layer_id in ids:
                raise DesignerProjectError("レイヤーIDが空または重複しています。")
            ids.add(layer_id)
            if layer.get("type") not in allowed_types:
                raise DesignerProjectError("未対応のレイヤー形式です。")
            for key in (
                    "x", "y", "width", "height",
                    "rotation", "opacity", "zIndex"):
                value = layer.get(key)
                if not isinstance(value, (int, float)):
                    raise DesignerProjectError(
                        f"{layer_id}.{key}が数値ではありません。")
            if layer["width"] <= 0 or layer["height"] <= 0:
                raise DesignerProjectError("レイヤーサイズが不正です。")
            if not 0 <= layer["opacity"] <= 1:
                raise DesignerProjectError("レイヤー透明度が不正です。")
            for key in ("flipX", "flipY"):
                if key in layer and not isinstance(layer[key], bool):
                    raise DesignerProjectError(
                        f"{layer_id}.{key}が不正です。")
            if ("assetTransformApplied" in layer and
                    not isinstance(layer["assetTransformApplied"], bool)):
                raise DesignerProjectError(
                    f"{layer_id}.assetTransformAppliedが不正です。")
            if project.get("schemaVersion") == 2:
                animations = layer.get("animations")
                if not isinstance(animations, dict):
                    raise DesignerProjectError("animationsが不正です。")
                cls._validate_motion_animation(
                    animations.get("intro"), "animations.intro")
                cls._validate_motion_animation(
                    animations.get("update"), "animations.update")
            if project.get("schemaVersion") == 3:
                events = layer.get("events")
                if not isinstance(events, dict):
                    raise DesignerProjectError("eventsが不正です。")
                cls._validate_event_timeline(
                    events.get("sceneEnter"), "events.sceneEnter", False)
                cls._validate_event_timeline(
                    events.get("dataChange"), "events.dataChange", True)
            if layer.get("type") == "image":
                asset = layer.get("asset")
                if not isinstance(asset, str) or not re.fullmatch(
                        r"assets/[^/\\]+\.(png|svg)", asset,
                        flags=re.IGNORECASE):
                    raise DesignerProjectError("PNG/SVGのassetパスが不正です。")
                if project.get("schemaVersion") == 3 and not any(
                        candidate.get("kind") == "image" and
                        candidate.get("path") == asset
                        for candidate in asset_by_id.values()):
                    raise DesignerProjectError(
                        "PNG/SVGのasset manifestがありません。")
                style = layer.get("gameHistoryStyle")
                if style is not None:
                    if not asset.lower().endswith(".svg") or not isinstance(
                            style, dict):
                        raise DesignerProjectError(
                            f"{layer_id}のSVG Game-History設定が不正です。")
                    slot_index = style.get("slotIndex")
                    if (isinstance(slot_index, bool) or
                            not isinstance(slot_index, int) or
                            not 0 <= slot_index <= 4):
                        raise DesignerProjectError(
                            f"{layer_id}.gameHistoryStyle.slotIndexが不正です。")
                    stroke_width = style.get("strokeWidth")
                    if (isinstance(stroke_width, bool) or
                            not isinstance(stroke_width, (int, float)) or
                            not 0 <= stroke_width <= 100):
                        raise DesignerProjectError(
                            f"{layer_id}.gameHistoryStyle.strokeWidthが不正です。")
                    for key in ("p1Color", "p2Color", "neutralColor"):
                        if not isinstance(style.get(key), str) or not style[key].strip():
                            raise DesignerProjectError(
                                f"{layer_id}.gameHistoryStyle.{key}が不正です。")
                    if style.get("mode") not in (None, "slot", "parts"):
                        raise DesignerProjectError(
                            f"{layer_id}.gameHistoryStyle.modeが不正です。")
                    parts = style.get("parts")
                    if parts is not None:
                        if not isinstance(parts, list) or len(parts) > 100:
                            raise DesignerProjectError(
                                f"{layer_id}.gameHistoryStyle.partsが不正です。")
                        paths: dict[str, list[dict[str, Any]]] = {}
                        for index, part in enumerate(parts):
                            if not isinstance(part, dict):
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}]が不正です。")
                            path = part.get("path")
                            if (not isinstance(path, list) or len(path) > 64 or
                                    any(isinstance(value, bool) or not isinstance(value, int) or
                                        value < 0 or value > 10000 for value in path)):
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}].pathが不正です。")
                            path_key = ".".join(map(str, path))
                            same_path_parts = paths.get(path_key, [])
                            can_share_path = bool(same_path_parts) and all(
                                existing.get("role") != part.get("role") and
                                existing.get("role") in {"p1-set", "p2-set"} and
                                part.get("role") in {"p1-set", "p2-set"} and
                                existing.get("setIndex") == 2 and
                                part.get("setIndex") == 2
                                for existing in same_path_parts
                            )
                            if same_path_parts and not can_share_path:
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}].pathが重複しています。")
                            same_path_parts.append(part)
                            paths[path_key] = same_path_parts
                            role = part.get("role")
                            if role not in {"set", "p1-set", "p2-set", "winner"}:
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}].roleが不正です。")
                            set_index = part.get("setIndex")
                            if role in {"set", "p1-set", "p2-set"} and (
                                    isinstance(set_index, bool) or
                                    not isinstance(set_index, int) or
                                    not 0 <= set_index <= 4):
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}].setIndexが不正です。")
                            if role == "winner" and set_index is not None:
                                raise DesignerProjectError(
                                    f"{layer_id}.gameHistoryStyle.parts[{index}].setIndexが不正です。")
            if layer.get("type") == "data-text":
                binding = layer.get("binding")
                if binding is None:
                    if not isinstance(layer.get("text"), str):
                        raise DesignerProjectError(
                            f"{layer_id}.textが不正です。")
                elif binding not in cls.BINDINGS or (
                        binding in cls.IMAGE_BINDINGS or binding == "games"):
                    raise DesignerProjectError(
                        f"{layer_id}.bindingが不正です。")
                cls._validate_typography(layer.get("typography"), layer_id)
            if layer.get("type") == "data-image" and layer.get(
                    "binding") not in cls.IMAGE_BINDINGS:
                raise DesignerProjectError(
                    f"{layer_id}.bindingが不正です。")
            if layer.get("type") == "game-history" and layer.get(
                    "binding") != "games":
                raise DesignerProjectError(
                    f"{layer_id}.bindingが不正です。")
            if layer.get("type") == "game-history" and "slots" in layer:
                slots = layer.get("slots")
                if not isinstance(slots, list) or len(slots) != 5:
                    raise DesignerProjectError(
                        f"{layer_id}.slotsは5要素である必要があります。")
                for index, slot in enumerate(slots):
                    if not isinstance(slot, dict):
                        raise DesignerProjectError(
                            f"{layer_id}.slots[{index}]が不正です。")
                    asset_path = slot.get("asset")
                    if not isinstance(asset_path, str) or not re.fullmatch(
                            r"assets/[^/\\]+\.(png|svg)", asset_path,
                            flags=re.IGNORECASE):
                        raise DesignerProjectError(
                            f"{layer_id}.slots[{index}].assetが不正です。")
                    if project.get("schemaVersion") == 3 and not any(
                            candidate.get("kind") == "image" and
                            candidate.get("path") == asset_path
                            for candidate in asset_by_id.values()):
                        raise DesignerProjectError(
                            f"{layer_id}.slots[{index}]のasset manifestがありません。")
                    for key in ("x", "y", "width", "height"):
                        value = slot.get(key)
                        if isinstance(value, bool) or not isinstance(value, (int, float)):
                            raise DesignerProjectError(
                                f"{layer_id}.slots[{index}].{key}が不正です。")
                    if slot["width"] <= 0 or slot["height"] <= 0:
                        raise DesignerProjectError(
                            f"{layer_id}.slots[{index}]のサイズが不正です。")
                    if "opacity" in slot and (
                            isinstance(slot["opacity"], bool) or
                            not isinstance(slot["opacity"], (int, float)) or
                            not 0 <= slot["opacity"] <= 1):
                        raise DesignerProjectError(
                            f"{layer_id}.slots[{index}].opacityが不正です。")
    @classmethod
    def _validate_project_asset(cls, asset: Any, index: int) -> None:
        if not isinstance(asset, dict):
            raise DesignerProjectError(f"assets[{index}]が不正です。")
        if not isinstance(asset.get("id"), str) or not asset["id"]:
            raise DesignerProjectError(f"assets[{index}].idが不正です。")
        kind = asset.get("kind")
        allowed = {
            "image": (
                r"assets/[^/\\]+\.(png|svg)",
                {"image/png", "image/svg+xml"},
            ),
        }
        if kind not in allowed:
            raise DesignerProjectError(f"assets[{index}].kindが不正です。")
        path_pattern, media_type = allowed[kind]
        if not isinstance(asset.get("path"), str) or not re.fullmatch(
                path_pattern, asset["path"], flags=re.IGNORECASE):
            raise DesignerProjectError(f"assets[{index}].pathが不正です。")
        if isinstance(media_type, set):
            media_valid = asset.get("mediaType") in media_type
        else:
            media_valid = asset.get("mediaType") == media_type
        media_valid = media_valid and asset.get("mediaType") == cls._image_media_type(
            asset["path"])
        if not media_valid:
            raise DesignerProjectError(
                f"assets[{index}].mediaTypeが不正です。")

    @staticmethod
    def _validate_typography(typography: Any, layer_id: str) -> None:
        if not isinstance(typography, dict):
            raise DesignerProjectError(
                f"{layer_id}.typographyが不正です。")
        if not isinstance(typography.get("fontFamily"), str) or not typography[
                "fontFamily"].strip():
            raise DesignerProjectError(
                f"{layer_id}.typography.fontFamilyが不正です。")
        google_family = typography.get("googleFontFamily")
        if google_family is not None and (
                not isinstance(google_family, str) or not google_family.strip()):
            raise DesignerProjectError(
                f"{layer_id}.typography.googleFontFamilyが不正です。")
        for key in ("fontSize", "fontWeight", "letterSpacing"):
            value = typography.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DesignerProjectError(
                    f"{layer_id}.typography.{key}が不正です。")
        if not 1 <= typography["fontSize"] <= 1000:
            raise DesignerProjectError(
                f"{layer_id}.typography.fontSizeが範囲外です。")
        if not 1 <= typography["fontWeight"] <= 1000:
            raise DesignerProjectError(
                f"{layer_id}.typography.fontWeightが範囲外です。")
        if not isinstance(typography.get("color"), str) or not typography[
                "color"].strip():
            raise DesignerProjectError(
                f"{layer_id}.typography.colorが不正です。")
        if typography.get("align") not in {"left", "center", "right"}:
            raise DesignerProjectError(
                f"{layer_id}.typography.alignが不正です。")
        if not isinstance(typography.get("italic"), bool):
            raise DesignerProjectError(
                f"{layer_id}.typography.italicが不正です。")

    @classmethod
    def _validate_motion_animation(cls, animation: Any, label: str) -> None:
        if not isinstance(animation, dict):
            raise DesignerProjectError(f"{label}が不正です。")
        if animation.get("renderer") != "webAnimations":
            raise DesignerProjectError(f"{label}.rendererが不正です。")
        for key in ("durationMs", "delayMs"):
            value = animation.get(key)
            if not isinstance(value, (int, float)) or value < 0:
                raise DesignerProjectError(f"{label}.{key}が不正です。")
        easing = animation.get("easing")
        if not isinstance(easing, dict):
            raise DesignerProjectError(f"{label}.easingが不正です。")
        for key in ("x1", "y1", "x2", "y2"):
            value = easing.get(key)
            if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise DesignerProjectError(f"{label}.easing.{key}が不正です。")
        if not isinstance(animation.get("loop"), bool):
            raise DesignerProjectError(f"{label}.loopが不正です。")
        keyframes = animation.get("keyframes")
        if not isinstance(keyframes, list) or not keyframes:
            raise DesignerProjectError(f"{label}.keyframesが不正です。")
        previous_time = -1
        duration = animation["durationMs"]
        for index, frame in enumerate(keyframes):
            if not isinstance(frame, dict):
                raise DesignerProjectError(f"{label}.keyframes[{index}]が不正です。")
            for key in ("timeMs", "x", "y", "rotation", "scaleX", "scaleY"):
                value = frame.get(key)
                if not isinstance(value, (int, float)):
                    raise DesignerProjectError(
                        f"{label}.keyframes[{index}].{key}が不正です。")
            if frame["timeMs"] < 0 or frame["timeMs"] > duration:
                raise DesignerProjectError(
                    f"{label}.keyframes[{index}].timeMsが範囲外です。")
            if frame["timeMs"] < previous_time:
                raise DesignerProjectError(f"{label}.keyframesの順序が不正です。")
            if frame["scaleX"] <= 0 or frame["scaleY"] <= 0:
                raise DesignerProjectError(
                    f"{label}.keyframes[{index}].scaleが不正です。")
            previous_time = frame["timeMs"]

    @classmethod
    def _validate_event_timeline(
            cls, timeline: Any, label: str, is_change: bool) -> None:
        if not isinstance(timeline, dict):
            raise DesignerProjectError(f"{label}が不正です。")
        for key in ("durationMs", "delayMs"):
            value = timeline.get(key)
            if not isinstance(value, (int, float)) or value < 0:
                raise DesignerProjectError(f"{label}.{key}が不正です。")
        easing = timeline.get("easing")
        if not isinstance(easing, dict):
            raise DesignerProjectError(f"{label}.easingが不正です。")
        for key in ("x1", "y1", "x2", "y2"):
            if not isinstance(easing.get(key), (int, float)) or not (
                    0 <= easing[key] <= 1):
                raise DesignerProjectError(f"{label}.easing.{key}が不正です。")
        frames = timeline.get("visualKeyframes")
        if not isinstance(frames, list) or not frames:
            raise DesignerProjectError(
                f"{label}.visualKeyframesが不正です。")
        previous_time = -1
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise DesignerProjectError(
                    f"{label}.visualKeyframes[{index}]が不正です。")
            for key in (
                    "timeMs", "x", "y", "rotation",
                    "scaleX", "scaleY", "opacity"):
                if not isinstance(frame.get(key), (int, float)):
                    raise DesignerProjectError(
                        f"{label}.visualKeyframes[{index}].{key}が不正です。")
            if not previous_time <= frame["timeMs"] <= timeline["durationMs"]:
                raise DesignerProjectError(
                    f"{label}.visualKeyframes[{index}].timeMsが不正です。")
            if frame["scaleX"] <= 0 or frame["scaleY"] <= 0 or not (
                    0 <= frame["opacity"] <= 1):
                raise DesignerProjectError(
                    f"{label}.visualKeyframes[{index}]の表示値が不正です。")
            previous_time = frame["timeMs"]
        actions = timeline.get("rendererActions")
        if not isinstance(actions, list) or actions:
            raise DesignerProjectError(
                f"{label}.rendererActionsが不正です。")
        if is_change:
            swap_at = timeline.get("swapAtMs")
            if not isinstance(swap_at, (int, float)) or not (
                    0 <= swap_at <= timeline["durationMs"]):
                raise DesignerProjectError(f"{label}.swapAtMsが不正です。")
            if timeline.get("interruptPolicy") != "coalesceLatest":
                raise DesignerProjectError(
                    f"{label}.interruptPolicyが不正です。")
            watch_bindings = timeline.get("watchBindings")
            if not isinstance(watch_bindings, list) or any(
                    binding not in cls.BINDINGS for binding in watch_bindings):
                raise DesignerProjectError(
                    f"{label}.watchBindingsが不正です。")

    @classmethod
    def _validate_assets_for_project(
            cls, project: dict[str, Any],
            assets: dict[str, Any]) -> dict[str, bytes]:
        asset_specs = cls._project_asset_specs(project)
        if len(asset_specs) > cls.MAX_ASSETS:
            raise DesignerProjectError("asset数が上限を超えています。")

        total_bytes = 0
        decoded_assets: dict[str, bytes] = {}
        for asset_path, media_type in asset_specs.items():
            encoded = assets.get(asset_path)
            if not isinstance(encoded, str):
                raise DesignerProjectError(
                    f"{asset_path}のassetデータがありません。")
            data = cls._decode_asset(encoded, asset_path, media_type)
            total_bytes += len(data)
            if total_bytes > cls.MAX_TOTAL_ASSET_BYTES:
                raise DesignerProjectError("assetの合計サイズが上限を超えています。")
            decoded_assets[asset_path] = data
        return decoded_assets

    @classmethod
    def _project_asset_specs(
            cls, project: dict[str, Any]) -> dict[str, str]:
        if project.get("schemaVersion") == 3:
            specs: dict[str, str] = {}
            for asset in project.get("assets", []):
                specs[asset["path"]] = asset["mediaType"]
            return specs
        specs: dict[str, str] = {}
        for layer in project["layers"]:
            if layer.get("type") == "image":
                specs[layer["asset"]] = cls._image_media_type(layer["asset"])
            elif layer.get("type") == "game-history":
                for slot in layer.get("slots", []):
                    if isinstance(slot, dict) and isinstance(slot.get("asset"), str):
                        specs[slot["asset"]] = cls._image_media_type(slot["asset"])
        return specs

    @staticmethod
    def _image_media_type(asset_path: str) -> str:
        return (
            "image/svg+xml"
            if asset_path.lower().endswith(".svg")
            else "image/png"
        )

    @classmethod
    def get_import(cls, import_id: str) -> dict[str, Any]:
        if not isinstance(import_id, str) or not re.fullmatch(
                r"[0-9a-f]{32}", import_id):
            raise DesignerProjectError("importIdが不正です。")
        with cls._import_lock:
            imported = cls._imports.get(import_id)
            if imported is None:
                raise FileNotFoundError("インポートが見つかりません。")
            return json.loads(json.dumps(imported, ensure_ascii=False))

    @staticmethod
    def _default_events(binding: str) -> dict[str, Any]:
        easing = {"x1": 0.2, "y1": 0.8, "x2": 0.2, "y2": 1}

        def frame(time_ms: int, scale: float, opacity: float) -> dict[str, Any]:
            return {
                "id": uuid.uuid4().hex,
                "timeMs": time_ms,
                "x": 0,
                "y": 0,
                "rotation": 0,
                "scaleX": scale,
                "scaleY": scale,
                "opacity": opacity,
            }

        return {
            "sceneEnter": {
                "durationMs": 450,
                "delayMs": 0,
                "easing": easing,
                "visualKeyframes": [
                    frame(0, 1, 0),
                    frame(450, 1, 1),
                ],
                "rendererActions": [],
            },
            "dataChange": {
                "durationMs": 440,
                "delayMs": 0,
                "easing": easing,
                "swapAtMs": 220,
                "watchBindings": [binding] if binding else [],
                "interruptPolicy": "coalesceLatest",
                "visualKeyframes": [
                    frame(0, 1, 1),
                    frame(220, 1.04, 0),
                    frame(440, 1, 1),
                ],
                "rendererActions": [],
            },
        }

    @classmethod
    def _decode_asset(
            cls, encoded: str, asset_path: str, media_type: str) -> bytes:
        if media_type == "image/png":
            return cls._decode_png(encoded, asset_path)
        if media_type == "image/svg+xml":
            return cls._decode_svg(encoded, asset_path)
        raise DesignerProjectError(
            f"{asset_path}のMIME typeが不正です。")

    @classmethod
    def _decode_png(cls, encoded: str, asset_path: str) -> bytes:
        prefix = "data:image/png;base64,"
        if not encoded.startswith(prefix):
            raise DesignerProjectError(f"{asset_path}はPNGではありません。")
        try:
            data = base64.b64decode(
                encoded[len(prefix):], validate=True)
        except (ValueError, TypeError) as exc:
            raise DesignerProjectError(
                f"{asset_path}のBase64が不正です。") from exc
        if len(data) > cls.MAX_ASSET_BYTES:
            raise DesignerProjectError(
                f"{asset_path}がサイズ上限を超えています。")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DesignerProjectError(f"{asset_path}はPNGではありません。")
        return data

    @classmethod
    def _decode_svg(cls, encoded: str, asset_path: str) -> bytes:
        prefix = "data:image/svg+xml;base64,"
        if not encoded.startswith(prefix):
            raise DesignerProjectError(f"{asset_path}はSVGではありません。")
        try:
            data = base64.b64decode(
                encoded[len(prefix):], validate=True)
            text = data.decode("utf-8-sig")
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise DesignerProjectError(
                f"{asset_path}のSVGまたはBase64が不正です。") from exc
        if not data or len(data) > cls.MAX_ASSET_BYTES:
            raise DesignerProjectError(
                f"{asset_path}が空またはサイズ上限を超えています。")
        if re.search(r"<!DOCTYPE", text, flags=re.IGNORECASE):
            raise DesignerProjectError(
                f"{asset_path}のDOCTYPEは使用できません。")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise DesignerProjectError(f"{asset_path}のSVGが不正です。") from exc
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise DesignerProjectError(f"{asset_path}のSVGが不正です。")
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            if tag in {"script", "foreignobject"}:
                raise DesignerProjectError(
                    f"{asset_path}に実行可能なSVG要素があります。")
            for attribute, value in element.attrib.items():
                local_attribute = attribute.rsplit("}", 1)[-1].lower()
                normalized_value = str(value).strip().lower()
                if local_attribute.startswith("on"):
                    raise DesignerProjectError(
                        f"{asset_path}にイベント属性があります。")
                if local_attribute in {"href", "src", "style"} and re.search(
                        r"(?:https?:|//|javascript:)", normalized_value):
                    raise DesignerProjectError(
                        f"{asset_path}に外部参照があります。")
        return data

    @classmethod
    def _validate_slug(cls, slug: str) -> str:
        if not isinstance(slug, str) or cls.slugify(slug) != slug:
            raise DesignerProjectError("slugが不正です。")
        return slug

    @classmethod
    def _project_directory(cls, slug: str) -> Path:
        root = cls.ROOT.resolve()
        directory = root / slug
        cls._assert_within(root, directory)
        return directory

    @staticmethod
    def _assert_within(root: Path, path: Path) -> None:
        root_resolved = root.resolve()
        path_resolved = path.resolve(strict=False)
        try:
            path_resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise DesignerProjectError(
                "保存先がlayout/customの外を指しています。") from exc

    @classmethod
    def _shared_runtime_available(cls) -> bool:
        return all((cls.RUNTIME_ROOT / name).is_file()
                   for name in cls.RUNTIME_FILES)

    @classmethod
    def _is_stub(cls, index_html: bytes) -> bool:
        return cls.STUB_MARKER.encode("utf-8") in index_html

    @classmethod
    def _migrate_legacy_project(cls, directory: Path) -> bool:
        """Migrate one design folder; False when there is nothing to do."""
        if not directory.is_dir() or directory.is_symlink():
            return False
        if not (directory / "runtime.js").is_file() or not (
                directory / "project.json").is_file():
            return False
        legacy = directory / cls.LEGACY_DIRNAME
        if legacy.is_symlink():
            raise DesignerProjectError("_legacyがシンボリックリンクです。")
        index = directory / "index.html"
        index_data = index.read_bytes() if index.is_file() else None
        replaces_entry_point = (
            index_data is not None and not cls._is_stub(index_data))
        # Back up, then swap the entry point, then drop the old runtime: an
        # interruption anywhere leaves a design that still renders, and the
        # next start finishes the job because runtime.js is still there.
        index_backed_up = False
        for name in cls.LEGACY_FILES:
            source = directory / name
            destination = legacy / name
            if not source.is_file() or destination.exists():
                # The first backup wins (an interrupted earlier run, or a
                # design migrated before and rolled back by copying).
                continue
            if name == "index.html" and not replaces_entry_point:
                continue  # already a stub; nothing to roll back to
            cls._atomic_write(destination, source.read_bytes())
            index_backed_up = index_backed_up or name == "index.html"
        cls._atomic_write(index, cls.STUB_INDEX_HTML.encode("utf-8"))
        for name in ("runtime.js", "runtime.css"):
            (directory / name).unlink(missing_ok=True)
        if replaces_entry_point and cls._normalized(index_data) != (
                cls._normalized(cls.LEGACY_INDEX_HTML.encode("utf-8"))):
            logger.warning(
                "designer: replaced a hand-edited index.html in {} with the "
                "shared-runtime stub; {}",
                directory,
                f"the edited file is kept in {cls.LEGACY_DIRNAME}/index.html"
                if index_backed_up else
                f"{cls.LEGACY_DIRNAME}/index.html keeps an earlier backup and "
                "the edited file was not kept")
        return True

    @staticmethod
    def _normalized(data: bytes) -> bytes:
        return data.replace(b"\r\n", b"\n").strip()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
