"""Local-only persistence for the desktop Scoreboard Designer.

The React editor runs inside a QWebEngineView, but it never receives direct
filesystem access. A short-lived token owned by the desktop window authorizes
requests to this service, which writes only below ``layout/custom``.

The one route that does not take that token is the Figma plugin's import
(``POST /api/designer/imports``): the plugin cannot know a token issued to
the Designer window, so it proves itself with a pairing code instead -- a
persistent secret the user reads off the Designer and types into the plugin
once (check_import_pairing).
"""

from __future__ import annotations

import base64
import hmac
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from loguru import logger

from .core import LayoutCatalog
from .core.SettingsManager import SettingsManager


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
    MAX_LAYERS = 200
    MAX_ASSET_BYTES = 50 * 1024 * 1024
    MAX_TOTAL_ASSET_BYTES = 200 * 1024 * 1024
    MAX_ASSETS = 200
    # The versions the shared runtime reads (layout/designer_runtime/README.md).
    SCHEMA_VERSIONS = frozenset({1, 2, 3})
    # The only files a design may reference: a PNG/SVG directly below
    # assets/, under a name Windows stores and opens as written -- no colon
    # (drive / alternate data stream), no control character, no DOS device
    # name (CON, nul.png, "COM1 .svg", ...: the part before the first dot,
    # trailing spaces ignored, as ntpath.isreserved() reads it) and no dot
    # or space right before the extension (the extension is required, so
    # that is where Windows' trailing-dot/space rule can still bite). Any
    # other character, Japanese included, is fine. Used with re.fullmatch
    # and re.IGNORECASE.
    ASSET_PATH_PATTERN = (
        r"assets/"
        r"(?!(?:con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³]) *\.)"
        r"[^/\\:\x00-\x1f\x7f-\x9f]*[^/\\:\x00-\x1f\x7f-\x9f. ]"
        r"\.(?:png|svg)"
    )
    # Pages that may POST a Figma import (is_import_origin_allowed).
    IMPORT_ORIGINS = frozenset({"null", "https://www.figma.com"})

    # Figma import pairing (check_import_pairing). The code is read off the
    # Designer and typed into the plugin, so its alphabet leaves out the
    # look-alikes 0/O and 1/I/L: 31 symbols, 12 of them in groups of 4
    # (XXXX-XXXX-XXXX) = 12 * log2(31) ~ 59.4 bits.
    PAIRING_SETTING = "designer.figma_pairing_code"
    PAIRING_HEADER = "X-Autona-Pairing"
    PAIRING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    PAIRING_GROUPS = 3
    PAIRING_GROUP_LENGTH = 4
    # A header longer than this is not a typo of the code; it is not even
    # normalized (bounded work per request).
    PAIRING_MAX_INPUT = 64
    # Lockout: the PAIRING_MAX_FAILURES-th wrong code in a row locks the
    # route for PAIRING_LOCK_SECONDS, each further one doubles it, up to
    # PAIRING_MAX_LOCK_SECONDS. A right code (or a new code) clears it.
    PAIRING_MAX_FAILURES = 5
    PAIRING_LOCK_SECONDS = 60
    PAIRING_MAX_LOCK_SECONDS = 3600

    _tokens: set[str] = set()
    _token_lock = threading.Lock()
    _latest_import: dict[str, Any] | None = None
    _imports: dict[str, dict[str, Any]] = {}
    _import_lock = threading.Lock()
    _pairing_lock = threading.Lock()
    _pairing_failures = 0
    _pairing_locked_until = 0.0
    # The lockout clock; tests move it instead of sleeping.
    _clock = staticmethod(time.monotonic)

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

    @classmethod
    def is_import_origin_allowed(cls, origin: str | None) -> bool:
        """Whether a Figma import may come from a page of this ``Origin``.

        The import route takes no Designer token, and a loopback peer is any
        page open in a browser on this computer, so the Origin header narrows
        who may even try. No header: not a browser page (curl, a script).
        ``"null"``: the plugin UI, which Figma runs in an opaque-origin
        iframe (tools/figma-scoreboard-plugin/ui.html). And Figma itself.
        "null" is also what a sandboxed iframe, a data: URL or a file://
        page on any site sends, so this only narrows the route; what closes
        it is the pairing code (check_import_pairing), checked after this.
        """
        return origin is None or origin in cls.IMPORT_ORIGINS

    @classmethod
    def pairing_code(cls) -> str:
        """The Figma pairing code, as shown to the user (XXXX-XXXX-XXXX).

        Kept in settings.json so the plugin stays paired across restarts.
        Issued on first use, and again if the stored one is not a code this
        method could have issued (a hand-edited settings.json).
        """
        with cls._pairing_lock:
            return cls._current_pairing_code()

    @classmethod
    def regenerate_pairing_code(cls) -> str:
        """Replace the pairing code; the old one stops working at once."""
        with cls._pairing_lock:
            code = cls._issue_pairing_code()
        logger.info("designer: issued a new Figma pairing code")
        return code

    @classmethod
    def check_import_pairing(cls, supplied: str | None) -> tuple[str, int]:
        """Whether a Figma import's pairing code lets it in.

        Returns ``(status, retry_after_seconds)``, status being one of
        ``"ok"``, ``"missing"`` (no code sent: not counted as a failure, the
        plugin was just never paired), ``"invalid"`` (a wrong code: counted)
        or ``"locked"`` (too many wrong codes in a row; nothing is compared
        until the lock expires, so a right code is refused too). Neither
        code is ever logged, only the failure count.
        """
        with cls._pairing_lock:
            now = cls._clock()
            if now < cls._pairing_locked_until:
                return "locked", max(1, math.ceil(cls._pairing_locked_until - now))
            if not isinstance(supplied, str) or not supplied.strip():
                return "missing", 0
            expected = cls._normalize_pairing_code(cls._current_pairing_code())
            given = ("" if len(supplied) > cls.PAIRING_MAX_INPUT
                     else cls._normalize_pairing_code(supplied))
            # Bytes: compare_digest refuses non-ASCII str, and the header is
            # whatever the client sent.
            if hmac.compare_digest(given.encode("utf-8"),
                                   expected.encode("ascii")):
                cls._pairing_failures = 0
                return "ok", 0
            cls._pairing_failures += 1
            failures = cls._pairing_failures
            if failures < cls.PAIRING_MAX_FAILURES:
                logger.warning(
                    "designer: refused a Figma import with a wrong pairing "
                    "code ({} in a row)", failures)
                return "invalid", 0
            seconds = min(
                cls.PAIRING_LOCK_SECONDS * 2 ** min(
                    failures - cls.PAIRING_MAX_FAILURES, 16),
                cls.PAIRING_MAX_LOCK_SECONDS)
            cls._pairing_locked_until = now + seconds
            logger.warning(
                "designer: refused a Figma import with a wrong pairing code "
                "({} in a row); refusing every import for {}s",
                failures, seconds)
            return "invalid", 0

    @classmethod
    def _current_pairing_code(cls) -> str:
        # Caller holds _pairing_lock.
        code = SettingsManager.Get(cls.PAIRING_SETTING)
        if isinstance(code, str) and cls._is_pairing_code(code):
            return code
        if code is not None:
            logger.warning(
                "designer: the stored Figma pairing code is malformed; "
                "issuing a new one")
        return cls._issue_pairing_code()

    @classmethod
    def _issue_pairing_code(cls) -> str:
        # Caller holds _pairing_lock. A new secret makes earlier wrong
        # guesses irrelevant, so it also lifts the lockout.
        length = cls.PAIRING_GROUPS * cls.PAIRING_GROUP_LENGTH
        raw = "".join(
            secrets.choice(cls.PAIRING_ALPHABET) for _ in range(length))
        code = "-".join(
            raw[i:i + cls.PAIRING_GROUP_LENGTH]
            for i in range(0, length, cls.PAIRING_GROUP_LENGTH))
        SettingsManager.Set(cls.PAIRING_SETTING, code)
        cls._pairing_failures = 0
        cls._pairing_locked_until = 0.0
        return code

    @classmethod
    def _is_pairing_code(cls, code: str) -> bool:
        group = f"[{cls.PAIRING_ALPHABET}]{{{cls.PAIRING_GROUP_LENGTH}}}"
        return re.fullmatch(
            "-".join([group] * cls.PAIRING_GROUPS), code) is not None

    @staticmethod
    def _normalize_pairing_code(value: str) -> str:
        """What the user meant to type: case, separators and full-width
        characters (a Japanese IME) do not matter."""
        return re.sub(
            r"[^0-9A-Z]", "",
            unicodedata.normalize("NFKC", value).upper())

    @staticmethod
    def slugify(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name).strip().casefold()
        slug = re.sub(r"[^\w-]+", "-", normalized, flags=re.UNICODE)
        slug = slug.strip("-_")[:48]
        return slug or "custom"

    @classmethod
    def list_projects(cls) -> list[dict[str, str]]:
        # The Designer's "open" list is the layout catalog's designer entries,
        # so it lists exactly what the Board tab offers, under the same name.
        prefix = f"{LayoutCatalog.CUSTOM_DIRNAME}/"
        return [
            {"slug": entry["id"][len(prefix):], "name": entry["displayName"]}
            for entry in LayoutCatalog.CustomLayouts(cls.ROOT)
        ]

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
        # No MAX_PROJECT_BYTES here: that bounds what the Designer saves and
        # what an import may hold, and a larger project.json placed by hand
        # must still open.
        cls._validate_project(project)
        for asset_path in cls._layer_asset_paths(project):
            cls._assert_within(directory, directory / Path(asset_path))

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
        cls._project_json(project)  # NaN / size, before anything is written
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
            style = layer.get("gameHistoryStyle")
            if not isinstance(style, dict):
                continue
            # The runtime renders svgSource inline in OBS. Only an SVG this
            # save decoded (_decode_svg vetted it) may end up there; whatever
            # the payload carried under that key is dropped.
            style.pop("svgSource", None)
            asset_path = layer.get("asset")
            if layer.get("type") != "image" or not isinstance(asset_path, str):
                continue
            if not asset_path.lower().endswith(".svg"):
                continue
            svg_data = decoded_assets.get(asset_path)
            if svg_data is None:
                continue
            style["svgSource"] = svg_data.decode("utf-8-sig")

        project_bytes = cls._project_json(project_to_write)

        for asset_path in cls._layer_asset_paths(project_to_write):
            cls._assert_within(directory, directory / Path(asset_path))
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
        # Held here, then saved by the Designer: the same limits as a save.
        cls._project_json(project)
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
        """Reject what would be unsafe to store or serve -- nothing more.

        What a project *means* (layer types and their fields, events, the
        asset manifest, schemaVersion 1/2 -> 3 migration) is owned by
        web/src/ScoreboardDesigner/model.ts, whose validateProject() the
        Designer runs on everything it loads, imports and saves; the OBS
        runtime migrates again at load time (layout/designer_runtime/).
        Here: a bounded layer count, a schemaVersion the runtime can read,
        every file reference (the manifest and every path the runtime loads)
        confined to the design's assets/ folder, and PNG/SVG content
        (_decode_asset, when the bytes arrive). What is stored or held also
        has to fit MAX_PROJECT_BYTES and be plain JSON (_project_json).
        ``meta`` (format / game, the Board tab's meta line) only has to be an
        object when present: its fields are model.ts's call, its size is
        bounded with the rest of the file, and its one reader
        (core/LayoutCatalog.py) takes nothing from it but non-empty strings,
        shown as plain text.

        Not every caller is the Designer. Its routes need the desktop
        window's token and a loopback peer (is_authorized); the Figma
        import needs a loopback peer, an admitted Origin
        (is_import_origin_allowed) and the pairing code
        (check_import_pairing) -- but whoever holds the code (the plugin,
        or any program the user gave it to) can send anything. An import is
        only held in memory -- an open Designer merges it into the editor,
        where model.ts validates it -- and reaches layout/custom only when
        the user saves it, through these checks again. So these checks must
        hold for any input on their own, never assuming model.ts has seen
        it. Revisit before these routes ever accept another computer
        (docs/changes/layout-custom-unification.md section 6).
        """
        if not isinstance(project, dict):
            raise DesignerProjectError("project.jsonの形式が不正です。")
        version = project.get("schemaVersion")
        if isinstance(version, bool) or version not in cls.SCHEMA_VERSIONS:
            raise DesignerProjectError("未対応のschemaVersionです。")
        # save_project derives the folder name from it.
        name = project.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DesignerProjectError("デザイン名がありません。")
        if len(name) > 128:
            raise DesignerProjectError("デザイン名が長すぎます。")
        if "meta" in project and not isinstance(project["meta"], dict):
            raise DesignerProjectError("metaがオブジェクトではありません。")
        layers = project.get("layers")
        if not isinstance(layers, list):
            raise DesignerProjectError("layersが配列ではありません。")
        if len(layers) > cls.MAX_LAYERS:
            raise DesignerProjectError("レイヤー数が上限を超えています。")
        # save_project and _project_asset_specs read fields off each layer.
        if not all(isinstance(layer, dict) for layer in layers):
            raise DesignerProjectError("不正なレイヤーがあります。")
        cls._project_asset_specs(project)  # the files stored with it
        # schemaVersion 3 lists its files in the manifest, but the runtime
        # loads each image layer and Game-History slot by its own path, so
        # those must be just as confined. Whether they are listed in the
        # manifest is model.ts's call.
        for path in cls._layer_asset_paths(project):
            cls._check_asset_path(path)

    @classmethod
    def _project_json(cls, project: dict[str, Any]) -> bytes:
        """project.json as save_project writes it, within MAX_PROJECT_BYTES.

        Also the bound on an import, which is saved the same way. NaN and
        Infinity are refused: Python would write them as bare tokens that
        JSON.parse -- the Designer, the OBS runtime -- rejects.
        """
        try:
            data = json.dumps(
                project, ensure_ascii=False, indent=2,
                allow_nan=False).encode("utf-8")
        except ValueError as exc:
            raise DesignerProjectError(
                "project.jsonにNaNまたはInfinityは使用できません。") from exc
        if len(data) > cls.MAX_PROJECT_BYTES:
            raise DesignerProjectError("project.jsonがサイズ上限を超えています。")
        return data

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
        """Media type of every file stored with the project below assets/.

        Raises unless each one is a PNG/SVG directly below assets/; the type
        follows the extension, which is how the file is served.
        schemaVersion 3 lists the files in its ``assets`` manifest; 1 and 2
        predate the manifest, so their layer references are the list (the
        Figma import still arrives as version 1).
        """
        if project.get("schemaVersion") == 3:
            manifest = project.get("assets")
            if not isinstance(manifest, list):
                raise DesignerProjectError("assetsが配列ではありません。")
            paths = [entry.get("path") if isinstance(entry, dict) else None
                     for entry in manifest]
        else:
            paths = cls._layer_asset_paths(project)
        specs: dict[str, str] = {}
        for path in paths:
            cls._check_asset_path(path)
            specs[path] = cls._image_media_type(path)
        return specs

    @staticmethod
    def _layer_asset_paths(project: dict[str, Any]) -> list[Any]:
        """What image layers and Game-History slots point at, unchecked.

        These are the paths the OBS runtime loads (an <img> src, a fetch).
        A slot that is not an object counts as a missing path.
        """
        paths: list[Any] = []
        for layer in project["layers"]:
            if layer.get("type") == "image":
                paths.append(layer.get("asset"))
            elif layer.get("type") == "game-history" and isinstance(
                    layer.get("slots"), list):
                paths.extend(
                    slot.get("asset") if isinstance(slot, dict) else None
                    for slot in layer["slots"])
        return paths

    @classmethod
    def _check_asset_path(cls, path: Any) -> None:
        if not isinstance(path, str) or not re.fullmatch(
                cls.ASSET_PATH_PATTERN, path, flags=re.IGNORECASE):
            raise DesignerProjectError("PNG/SVGのassetパスが不正です。")

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
