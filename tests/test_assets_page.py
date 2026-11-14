"""The game assets page (ADR-005 S-final SF-4b-2, web/src/AssetsPage/): what
the Python side and the page must keep in step.

The page's words are keyed by core's fixed codes (a refusal's
``error.reason``, a job's ``error`` / ``stage``, the index's ``error``), so a
code core gains needs its translation (``test_api_v1_guides`` does the same
for the API catalogue); the upload limit and the source URL are written in
the page too. The window that this page replaced is gone
(tests/test_web_panel_host.py pins that).
"""

import ast
import json
import re
from pathlib import Path

import pytest

from src import AutonaApiV1 as v1
from src.core import AssetPackService as assets
from src.core import WebAccess

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"
PAGE = WEB / "AssetsPage"
LOCALES = WEB / "i18n" / "locales"


def _locale(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _page_source(name):
    return (PAGE / name).read_text(encoding="utf-8")


# The codes the page says in words: every refusal core can answer (the
# v1 table, which test_api_v1_assets pins against core's raises), and the
# job's own failures that are not refusals.
ERROR_CODES = sorted(set(v1._ASSET_REFUSALS) | {
    assets.DOWNLOAD_FAILED, assets.EXTRACT_FAILED, assets.COPY_FAILED})
STAGES = [assets.DOWNLOAD, assets.EXTRACT, assets.COPY, assets.SCAN]
INDEX_ERRORS = [assets.INDEX_FETCH_FAILED, assets.BAD_INDEX]


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_every_core_code_has_its_words(lang):
    strings = _locale(lang)
    for code in ERROR_CODES:
        assert strings.get(f"assets_error_{code}"), (lang, code)
    for stage in STAGES:
        assert strings.get(f"assets_stage_{stage}"), (lang, stage)
    for code in INDEX_ERRORS:
        assert strings.get(f"assets_index_{code}"), (lang, code)


def test_the_pages_error_words_are_core_codes_or_the_pages_own():
    # No translated code that core no longer has (a key nothing can reach).
    own = {"forbidden", "request"}
    for lang in ("en", "ja"):
        keys = {key[len("assets_error_"):] for key in _locale(lang)
                if key.startswith("assets_error_")}
        assert keys - own == set(ERROR_CODES), lang
        stages = {key[len("assets_stage_"):] for key in _locale(lang)
                  if key.startswith("assets_stage_")}
        assert stages == set(STAGES), lang
        index = {key[len("assets_index_"):] for key in _locale(lang)
                 if key.startswith("assets_index_")}
        assert index == set(INDEX_ERRORS), lang


def test_the_japanese_words_are_the_english_keys():
    # i18next plural suffixes differ by language (en has _one and _other,
    # ja only _other): compare the base keys.
    def base(lang):
        return {re.sub(r"_(one|other)$", "", key) for key in _locale(lang)
                if key.startswith("assets_")}
    assert base("en") == base("ja")


def test_the_page_checks_a_size_against_core_upload_limit():
    source = _page_source("assetsApi.ts")
    match = re.search(r"export const MAX_UPLOAD_BYTES = ([0-9 *]+);", source)
    assert match, "assetsApi.ts: no MAX_UPLOAD_BYTES"
    # Digits, spaces and * only (the pattern above); `**` is a power in
    # both languages.
    assert eval(match.group(1)) == assets.MAX_UPLOAD_BYTES  # noqa: S307


def test_the_page_links_core_asset_source():
    source = _page_source("assetsApi.ts")
    match = re.search(r'export const SOURCE_URL = "([^"]+)";', source)
    assert match and match.group(1) == assets.SOURCE_URL


def test_the_page_calls_the_routes_core_serves():
    # The class of each route (SHELL / THIS_PC) is pinned by
    # test_webserver_routes and test_web_access; here, that the page names
    # exactly them.
    source = _page_source("assetsApi.ts")
    paths = dict(re.findall(r'export const (\w+_PATH) = "([^"]+)";', source))
    assert paths == {
        "PACKS_PATH": "/api/v1/assets/packs",
        "JOB_PATH": "/api/v1/assets/job",
        "UPLOADS_PATH": "/api/v1/assets/uploads",
        "IMPORT_ZIP_PATH": "/api/v1/assets:importZip",
        "IMPORT_FOLDER_PATH": "/api/v1/assets:importFolder",
        "INSTALL_PATH": "/api/v1/assets/packs:install",
        "CANCEL_PATH": "/api/v1/assets/job:cancel",
        "REVEAL_PATH": "/api/v1/assets/games-folder:reveal",
    }
    assert 'FILE_NAME_HEADER = "X-Autona-File-Name"' in source
    assert v1.ASSET_FILE_NAME_HEADER == "X-Autona-File-Name"


def test_the_page_is_a_web_panel_and_a_menu_page():
    assert WebAccess.IsWebPanel("assets")
    registry = (WEB / "PanelShell" / "registry.tsx").read_text(
        encoding="utf-8")
    assert '"assets": AssetsPage' in registry
    source = (ROOT / "src" / "Autona.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    pages = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "MENU_PAGES"
                         for t in node.targets))
    assert pages[-1] == "assets"
    # Both "Add game assets" entries (Help & Settings and the game menu)
    # open it.
    opened = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "OpenMenuPage"
              and node.args and isinstance(node.args[0], ast.Constant)
              and node.args[0].value == "assets"]
    assert len(opened) == 2
