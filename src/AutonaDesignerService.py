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
from pathlib import Path
from typing import Any


class DesignerProjectError(ValueError):
    pass


class AutonaDesignerService:
    ROOT = Path("layout/custom")
    TEMPLATE_CANDIDATES = (
        Path("web/build/scoreboard-designer-runtime"),
        Path("web/public/scoreboard-designer-runtime"),
    )
    TEMPLATE_FILES = ("index.html", "runtime.css", "runtime.js")

    MAX_PROJECT_BYTES = 2 * 1024 * 1024
    MAX_ASSET_BYTES = 10 * 1024 * 1024
    MAX_TOTAL_ASSET_BYTES = 50 * 1024 * 1024
    MAX_ASSETS = 30

    _tokens: set[str] = set()
    _token_lock = threading.Lock()

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
        is_loopback = remote_addr in {
            "127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"
        }
        if not is_loopback or not token:
            return False
        with cls._token_lock:
            return secrets.compare_digest(
                token, next((item for item in cls._tokens if item == token), ""))

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

        assets: dict[str, str] = {}
        for layer in project["layers"]:
            if layer.get("type") != "image":
                continue
            asset_path = layer["asset"]
            file_path = directory / Path(asset_path)
            cls._assert_within(directory, file_path)
            if not file_path.is_file():
                continue
            data = file_path.read_bytes()
            if len(data) > cls.MAX_ASSET_BYTES:
                raise DesignerProjectError(
                    f"{asset_path}がサイズ上限を超えています。")
            assets[asset_path] = (
                "data:image/png;base64," +
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
    def save_project(cls, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise DesignerProjectError("保存データがオブジェクトではありません。")
        project = payload.get("project")
        assets = payload.get("assets")
        cls._validate_project(project)
        if not isinstance(assets, dict):
            raise DesignerProjectError("assetsがオブジェクトではありません。")

        slug = cls.slugify(project["name"])
        directory = cls._project_directory(slug)
        if directory.exists() and directory.is_symlink():
            raise DesignerProjectError("シンボリックリンクには保存できません。")
        directory.mkdir(parents=True, exist_ok=True)
        asset_directory = directory / "assets"
        if asset_directory.exists() and asset_directory.is_symlink():
            raise DesignerProjectError("assetsがシンボリックリンクです。")
        asset_directory.mkdir(exist_ok=True)

        image_paths = {
            layer["asset"]
            for layer in project["layers"]
            if layer.get("type") == "image"
        }
        if len(image_paths) > cls.MAX_ASSETS:
            raise DesignerProjectError("PNGレイヤー数が上限を超えています。")

        total_bytes = 0
        decoded_assets: dict[str, bytes] = {}
        for asset_path in image_paths:
            encoded = assets.get(asset_path)
            if not isinstance(encoded, str):
                raise DesignerProjectError(
                    f"{asset_path}の画像データがありません。")
            data = cls._decode_png(encoded, asset_path)
            total_bytes += len(data)
            if total_bytes > cls.MAX_TOTAL_ASSET_BYTES:
                raise DesignerProjectError("PNGの合計サイズが上限を超えています。")
            decoded_assets[asset_path] = data

        project_bytes = json.dumps(
            project, ensure_ascii=False, indent=2).encode("utf-8")
        if len(project_bytes) > cls.MAX_PROJECT_BYTES:
            raise DesignerProjectError("project.jsonがサイズ上限を超えています。")

        for asset_path, data in decoded_assets.items():
            destination = directory / Path(asset_path)
            cls._assert_within(directory, destination)
            cls._atomic_write(destination, data)

        template_root = cls._template_root()
        for filename in cls.TEMPLATE_FILES:
            cls._atomic_write(
                directory / filename,
                (template_root / filename).read_bytes(),
            )

        # Write the manifest last so readers never see references to assets
        # that have not finished saving.
        cls._atomic_write(directory / "project.json", project_bytes)
        return {"slug": slug, "path": f"layout/custom/{slug}"}

    @classmethod
    def _validate_project(cls, project: Any) -> None:
        if not isinstance(project, dict) or project.get("schemaVersion") != 1:
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
        allowed_types = {"image", "data-text", "data-image", "game-history"}
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
            if layer.get("type") == "image":
                asset = layer.get("asset")
                if not isinstance(asset, str) or not re.fullmatch(
                        r"assets/[^/\\]+\.png", asset, flags=re.IGNORECASE):
                    raise DesignerProjectError("PNGのassetパスが不正です。")

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
    def _template_root(cls) -> Path:
        for candidate in cls.TEMPLATE_CANDIDATES:
            if all((candidate / name).is_file()
                   for name in cls.TEMPLATE_FILES):
                return candidate
        raise DesignerProjectError("OBSランタイムテンプレートが見つかりません。")

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
