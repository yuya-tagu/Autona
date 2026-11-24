"""The thumbnail's layout kit (ADR-008 D7 R3): "make a new design" on the
thumbnail page.

- the payload's table (layout/include/thumbnail_contract.json) is the code's:
  its keys only grow (TABLE_KEYS, a frozen copy), its data sources are state
  keys of KEY_CATALOG, its config keys are in every config ThumbnailService
  hands a render and its preview (RenderPayload / PreviewPayload; the extra
  ones are named here), and the sample payload the generator writes holds
  them with their types;
- the AI_PROMPT.md is the instructions, the whole table and the samples;
- core's LayoutKit makes a thumbnail_ template package from the kit that the
  catalog lists as contract 2 (the manifest ADR-007 D1 asks for);
- the v1 routes make and read it as they do an overlay's (SHELL);
- a thumbnail_ folder the catalog cannot list is told under the thumbnail
  page alone.

The kit's sample drawn by the real renderer: tests/test_render_platform.py.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from src.core import LayoutCatalog, LayoutKit, ThumbnailService
from src.core.LayoutKit import LayoutKitError
from tests.test_layout_kit import (  # noqa: F401 (fixtures)
    CREATE, KIT, REVEAL, _left, _prompt, api, root, started)
from src.core.SettingsManager import SettingsManager
from tests.test_overlay_contract import (
    DICT_VALUED, NODE, catalog_paths, needs_node, sample_leaves,
    segments_match)
from tests.test_thumbnail_service import (  # noqa: F401 (fixtures)
    env, executor, match)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INCLUDE = PROJECT_ROOT / "layout" / "include"
TABLE = INCLUDE / "thumbnail_contract.json"
THUMBNAIL_KIT = KIT / "thumbnail"
NoneType = type(None)
TYPES = {"string": str, "number": (int, float), "boolean": bool,
         "object": dict, "string|null": (str, NoneType)}
GROUPS = ("data", "config", "template")
# The config's keys a render may hand besides the table's: the saved
# template's id (the template's is template.template_id).
CONFIG_EXTRA = {"template_id"}

# The table's keys, frozen: contract 2 only adds keys (a renamed or removed
# one is contract 3). A key added to the table is added here too.
TABLE_KEYS = (
    "data.score.{N}.phase",
    "data.score.{N}.pool",
    "data.score.{N}.match",
    "data.score.{N}.match_en",
    "data.score.{N}.best_of",
    "data.score.{N}.best_of_text",
    "data.score.{N}.first_to",
    "data.score.{N}.team.{T}.teamName",
    "data.score.{N}.team.{T}.score",
    "data.score.{N}.team.{T}.color",
    "data.score.{N}.team.{T}.losers",
    "data.score.{N}.team.{T}.player.{M}.name",
    "data.score.{N}.team.{T}.player.{M}.team",
    "data.score.{N}.team.{T}.player.{M}.mergedName",
    "data.score.{N}.team.{T}.player.{M}.real_name",
    "data.score.{N}.team.{T}.player.{M}.twitter",
    "data.score.{N}.team.{T}.player.{M}.seed",
    "data.score.{N}.team.{T}.player.{M}.country",
    "data.score.{N}.team.{T}.player.{M}.state",
    "data.score.{N}.team.{T}.player.{M}.character.{K}",
    "data.score.{N}.team.{T}.player.{M}.character.{K}.assets.{A}.asset",
    "data.score.{N}.team.{T}.player.{M}.character.{K}.assets.{A}.eyesight",
    "data.score.{N}.team.{T}.player.{M}.sponsor_logo",
    "data.game.name",
    "data.game.codename",
    "data.game.abbreviation",
    "data.tournamentInfo.tournamentName",
    "data.tournamentInfo.displayTournamentName",
    "data.tournamentInfo.eventName",
    "data.tournamentInfo.address",
    "data.tournamentInfo.startAt",
    "data.tournamentInfo.startAtDate",
    "data.tournamentInfo.numEntrants",
    "data.display_options.show_sponsor",
    "config.scoreboardNumber",
    "config.background_source",
    "config.background_path",
    "config.theme_color",
    "config.accent_color",
    "config.display_phase",
    "config.use_team_names",
    "config.use_sponsors",
    "config.game.{codename}.flip_p1",
    "config.game.{codename}.flip_p2",
    "config.game.{codename}.smooth_scale",
    "config.game.{codename}.zoom",
    "config.game.{codename}.align.horizontal",
    "config.game.{codename}.align.vertical",
    "template.template_id",
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _table():
    return _read(TABLE)


def _rows(group):
    return {row["key"].split(".", 1)[1]: row for row in _table()["keys"]
            if row["key"].split(".", 1)[0] == group}


def _typed(value, kind):
    if kind in ("number", "string") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[kind])


def _config_paths(config):
    """The config's keys as the table writes them: a game's settings under
    game.{codename}."""
    out = set()
    for path, _value in sample_leaves(config):
        if path[0] == "game" and len(path) > 1:
            path = ("game", "{codename}", *path[2:])
        out.add(".".join(path))
    return out


def _found(value, segments):
    """Every value at `segments` (a table key's, placeholders matching any
    key of a dict) in `value`."""
    if not segments:
        yield value
        return
    if not isinstance(value, dict):
        return
    head, rest = segments[0], segments[1:]
    if head.startswith("{") and head.endswith("}"):
        for child in value.values():
            yield from _found(child, rest)
    elif head in value:
        yield from _found(value[head], rest)


@pytest.fixture
def colors(monkeypatch):
    monkeypatch.setattr(ThumbnailService, "GetFeatureColors",
                        lambda feature: ("#181B21", "#f0a500"))


def _rendered_config(settings):
    """The config RenderPayload hands a render of `settings` (a game whose
    codename is "sample")."""
    config = ThumbnailService._Config(
        ThumbnailService._WithBackgroundSource(dict(settings)), 1)
    return ThumbnailService._AsRead(config, {"game": {"codename": "sample"}})


# ── the table ────────────────────────────────────────────────────────────

def test_the_table_is_well_formed():
    table = _table()
    assert table["contract"] == LayoutKit.TEMPLATE_CONTRACT == 2
    keys = [row["key"] for row in table["keys"]]
    assert len(keys) == len(set(keys))
    for row in table["keys"]:
        assert row["key"].split(".", 1)[0] in GROUPS, row["key"]
        assert row["type"] in TYPES, row["key"]
        assert row["ja"].strip(), row["key"]
        assert row["source"], row["key"]
    for group in GROUPS:
        assert _rows(group), group


def test_every_data_source_is_a_state_key_of_the_catalog():
    # A source names a KEY_CATALOG path, or a field of a dict one of
    # DICT_VALUED holds; the key is the state's own path under data (an
    # asset's path inside its character's dict).
    paths = catalog_paths()
    for key, row in _rows("data").items():
        (source,) = row["source"]
        assert source in paths or any(
            source.startswith(path + ".") for path in DICT_VALUED), key
        assert key == source or key.startswith(source + "."), key


def test_the_config_rows_are_what_every_render_is_handed(colors):
    rows = set(_rows("config"))
    assert all(row["source"] == ["config"] for row in _rows("config").values())
    # Nothing saved yet, and every source with every setting saved.
    assert _config_paths(_rendered_config({})) == rows
    for source in ThumbnailService.BACKGROUND_SOURCES:
        saved = {"template_id": "thumbnail", "background_source": source,
                 "background_path": "./user_data/bg.png",
                 "display_phase": False, "use_team_names": False,
                 "use_sponsors": False,
                 "game": {"sample": {"flip_p1": True, "flip_p2": False,
                                     "smooth_scale": False, "zoom": 150,
                                     "align": {"horizontal": 10,
                                               "vertical": 20}}}}
        # Every setting a PATCH takes, and the uploaded background.
        assert set(saved) == set(ThumbnailService._CHECKS) | {
            "background_path"}
        config = _rendered_config(saved)
        # The template's id is the template's (template.template_id).
        assert _config_paths(config) == rows | {"template_id"}
        for key, row in _rows("config").items():
            for value in _found(config, key.split(".")):
                assert _typed(value, row["type"]), (source, key, value)


def test_the_tables_keys_only_grow():
    assert {row["key"] for row in _table()["keys"]} == set(TABLE_KEYS)


@pytest.mark.parametrize("saved", ["nothing", "everything"])
def test_every_config_a_render_and_its_preview_get_has_the_rows(
        match, saved):
    # Through the service itself: RenderPayload (the PNG) and PreviewPayload
    # (GET /api/v1/thumbnail/payload, the live preview).
    if saved == "everything":
        codename = match.service.RenderPayload(preview=True)[0]["game"][
            "codename"]
        SettingsManager.settings = {"thumbnail_config": {
            "template_id": "type_a", "background_source": "banner",
            "display_phase": False, "use_team_names": True,
            "use_sponsors": False,
            "game": {codename: {"flip_p1": True, "flip_p2": False,
                                "smooth_scale": False, "zoom": 150,
                                "align": {"horizontal": 10,
                                          "vertical": 20}}}}}
    rows = _rows("config")
    _data, rendered, _template = match.service.RenderPayload(preview=True)
    previewed = match.service.PreviewPayload()["payload"]["config"]
    for config in (rendered, previewed):
        paths = _config_paths(config)
        assert set(rows) <= paths, set(rows) - paths
        assert paths - set(rows) <= CONFIG_EXTRA, paths - set(rows)
        for key, row in rows.items():
            for value in _found(config, key.split(".")):
                assert _typed(value, row["type"]), (key, value)


def test_the_template_rows_are_what_the_template_is_handed():
    assert set(_rows("template")) == {"template_id"}
    assert _rows("template")["template_id"]["source"] == ["template"]


def test_the_sample_config_is_a_config_a_render_is_handed(colors):
    sample = _table()["sampleConfig"]
    assert _config_paths(sample) == set(_rows("config"))
    assert sample == _rendered_config(sample)


# ── the sample payload ───────────────────────────────────────────────────

def _payload():
    return _read(THUMBNAIL_KIT / "sample_payload.json")


def test_the_sample_payload_holds_the_rows_with_their_types():
    payload = _payload()
    assert set(payload) == set(GROUPS)
    missing = []
    for row in _table()["keys"]:
        found = list(_found(payload, row["key"].split(".")))
        if not found:
            missing.append(row["key"])
        for value in found:
            assert _typed(value, row["type"]), (row["key"], value)
    # A key the sample game has no value for (the table says it may be
    # missing).
    assert missing == ["data.game.abbreviation"]


def test_the_sample_payloads_config_and_template_are_the_tables():
    payload = _payload()
    assert _config_paths(payload["config"]) == set(_rows("config"))
    assert payload["template"] == dict(
        _read(THUMBNAIL_KIT / "template.json"), template_id="thumbnail_sample")


def test_every_key_of_the_sample_data_is_a_state_key_of_the_catalog():
    patterns = [tuple(path.split(".")) for path in catalog_paths()]
    dicts = [tuple(path.split(".")) for path in DICT_VALUED]
    unknown = []
    for path, value in sample_leaves(_payload()["data"]):
        if any(segments_match(pattern, path) for pattern in patterns):
            continue
        if any(segments_match(pattern, path[:len(pattern)])
               for pattern in dicts if len(path) > len(pattern)):
            continue
        if value == {} and any(segments_match(pattern[:len(path)], path)
                               for pattern in patterns
                               if len(pattern) > len(path)):
            continue
        unknown.append(".".join(path))
    assert unknown == []


# ── the AI_PROMPT.md ─────────────────────────────────────────────────────

def test_the_ai_prompt_holds_the_instructions_the_keys_and_the_samples():
    prompt = (THUMBNAIL_KIT / "AI_PROMPT.md").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    for name in ("thumbnail.html", "thumbnail.css", "thumbnail.js",
                 "template.json", "sample_payload.json"):
        sample = (THUMBNAIL_KIT / name).read_text(encoding="utf-8")
        assert sample.replace("\r\n", "\n").strip() in prompt, name
    assert all(f"`{row['key']}`" in prompt for row in _table()["keys"])
    # The rules a chat AI must keep.
    for rule in ("thumbnail_<名前>", '"kind": "thumbnail"', '"contract": 2',
                 '"size": [1920, 1080]', '"template": "template.json"',
                 "Autona.onRender", "async", "document.fonts.ready",
                 '"../../"', "/render-asset/", "file://", "CSP", "CDN",
                 "Google Fonts", "`size`", "overflow: hidden",
                 "[hidden] { display: none !important; }", "`Autona`",
                 "../include/autona.js", "../../assets/font/LINESeedJP/",
                 "textContent", "レイアウトの確認", "AI に貼る用にコピー",
                 "デザインを再読み込み", "読み込めなかったデザイン",
                 "out/thumbnails/", "エラーはありません",
                 # The acceptance test's questions (R3).
                 "settings.json` は返さない", "（作ったときの表示名）",
                 "use_team_names", "use_sponsors", "display_phase",
                 "theme_color", "accent_color", "CSS だけ",
                 "default_background", "flip_p1", "smooth_scale",
                 "eyesight", "match_en", "団体戦", "2 回目以降",
                 "document.title", "/web/app/thumbnail", "5107",
                 "「レイアウトの確認」の欄にも出ない"):
        assert rule in prompt, rule
    full = (THUMBNAIL_KIT / "CONTRACT.md").read_text(encoding="utf-8")
    assert all(f"`{row['key']}`" in full for row in _table()["keys"])


def test_the_kits_sample_reads_the_payload_as_the_instructions_say():
    script = (THUMBNAIL_KIT / "thumbnail.js").read_text(encoding="utf-8")
    assert "Autona.onRender(" in script
    assert script.lstrip().startswith("(function")
    html = (THUMBNAIL_KIT / "thumbnail.html").read_text(encoding="utf-8")
    assert html.index("../include/autona.js") < html.index("./thumbnail.js")
    css = (THUMBNAIL_KIT / "thumbnail.css").read_text(encoding="utf-8")
    assert "width: 1920px" in css and "height: 1080px" in css
    assert "http://" not in html + css + script
    assert "https://" not in html + css + script
    # The character placement of the thumbnail page is read.
    for key in ("flip_p", "zoom", "align", "smooth_scale", "eyesight"):
        assert key in script, key


# What the sample's resolve() makes of a payload's path (the rules the
# instructions give): Autona's folder two up, its own URLs and data: /
# blob: as they are, nothing for a path this page cannot load.
RESOLVED = {
    "": "", "./assets/a.png": "../../assets/a.png",
    "user_data/b.png": "../../user_data/b.png",
    "/render-asset/0123": "/render-asset/0123",
    "data:image/png;base64,AAAA": "data:image/png;base64,AAAA",
    "blob:http://127.0.0.1/x": "blob:http://127.0.0.1/x",
    "C:\\Users\\x.png": "", "file:///C:/x.png": "", "https://x/y.png": "",
    "//x/y.png": "", "\\\\server\\x.png": "",
}


@needs_node
def test_the_samples_resolve_follows_the_instructions():
    script = (THUMBNAIL_KIT / "thumbnail.js").read_text(encoding="utf-8")
    source = re.search(r"  function resolve\(path\) \{.*?\n  \}\n", script,
                       re.S).group(0)
    program = (source + "process.stdout.write(JSON.stringify("
               + json.dumps(list(RESOLVED)) + ".map(resolve)));")
    done = subprocess.run([NODE, "-e", program], capture_output=True,
                          text=True, encoding="utf-8", timeout=60, check=True)
    assert dict(zip(RESOLVED, json.loads(done.stdout), strict=True)) == RESOLVED


# ── core: making one ─────────────────────────────────────────────────────

def test_the_folder_is_the_thumbnail_prefix_and_the_name():
    assert LayoutKit.FolderName("thumbnail", "Neon") == "thumbnail_Neon"
    # The bundled second template's folder, in any case.
    for name in ("type_b", "TYPE_B"):
        with pytest.raises(LayoutKitError) as refused:
            LayoutKit.FolderName("thumbnail", name)
        assert refused.value.code == LayoutKit.RESERVED


def test_a_new_thumbnail_is_made_from_the_kit_and_listed(root):
    entry = LayoutKit.NewDesign("thumbnail", "Neon", "ネオン", root=root)

    folder = root / "thumbnail_Neon"
    assert sorted(p.name for p in folder.iterdir()) == sorted([
        "thumbnail.html", "thumbnail.css", "thumbnail.js", "template.json",
        "sample_payload.json", "settings.json", "AGENTS.md", "CLAUDE.md",
        "AI_PROMPT.md", "CONTRACT.md"])
    for name in ("thumbnail.html", "thumbnail.css", "thumbnail.js",
                 "template.json", "sample_payload.json"):
        assert (folder / name).read_bytes() == (
            THUMBNAIL_KIT / name).read_bytes()
    assert (folder / "AGENTS.md").read_bytes() == (
        THUMBNAIL_KIT / "AGENTS.md.tmpl").read_bytes()
    assert (folder / "CLAUDE.md").read_bytes() == (
        KIT / "CLAUDE.md.tmpl").read_bytes()
    for name in ("AI_PROMPT.md", "CONTRACT.md"):
        copied = (folder / name).read_text(encoding="utf-8")
        kit = (THUMBNAIL_KIT / name).read_text(encoding="utf-8")
        assert copied == LayoutKit._WithoutMark(kit) != kit
    # The manifest a template package is listed by (ADR-007 D1 / D5).
    assert _read(folder / "settings.json") == {"autona": {
        "kind": "thumbnail", "contract": 2, "entry": "thumbnail.html",
        "size": [1920, 1080], "template": "template.json",
        "displayName": "ネオン", "author": LayoutKit.DEFAULT_AUTHOR,
        "license": LayoutKit.DEFAULT_LICENSE}}
    assert entry["id"] == "thumbnail_Neon"
    assert entry["kind"] == "thumbnail" and entry["contract"] == 2
    listed = LayoutCatalog.Templates("thumbnail", root)
    assert listed["thumbnail_Neon"]["size"] == [1920, 1080]
    assert listed["thumbnail_Neon"]["entry"] == "thumbnail.html"
    assert listed["thumbnail_Neon"]["template"] == "template.json"
    assert LayoutCatalog.IsTemplateFolder("thumbnail_Neon", root)
    # Not an overlay: no overlay page lists it.
    assert LayoutCatalog.Layouts(list(LayoutKit.OVERLAY_KINDS), root) == []
    assert _left(root) == ["thumbnail_Neon"]


def test_a_thumbnail_kit_that_is_not_whole_makes_nothing(root):
    (root / "include" / "kit" / "thumbnail" / "template.json").unlink()
    with pytest.raises(LayoutKitError) as refused:
        LayoutKit.NewDesign("thumbnail", "mine", root=root)
    assert refused.value.code == LayoutKit.KIT_MISSING
    assert _left(root) == []


def test_the_thumbnails_ai_prompt_is_the_kits_without_its_mark():
    text = (THUMBNAIL_KIT / "AI_PROMPT.md").read_text(encoding="utf-8")
    prompt = LayoutKit.AiPrompt("thumbnail", root=INCLUDE.parent)
    assert prompt == LayoutKit._WithoutMark(text)
    assert prompt.startswith("# ")


# ── the catalog: a thumbnail_ folder it cannot list ──────────────────────

@pytest.mark.parametrize("settings, reason", [
    ("{not json", LayoutCatalog.UNLISTED_BAD_SETTINGS),
    (None, LayoutCatalog.UNLISTED_NO_MANIFEST),
])
def test_a_broken_thumbnail_folder_is_told_on_the_thumbnail_page(
        tmp_path, settings, reason):
    folder = tmp_path / "thumbnail_broken"
    folder.mkdir()
    (folder / "thumbnail.html").write_text("<html></html>", encoding="utf-8")
    if settings is not None:
        (folder / "settings.json").write_text(settings, encoding="utf-8")

    listed, unlisted = LayoutCatalog.Scan(["thumbnail"], tmp_path)
    assert listed == []
    assert [(u["folder"], u["kind"], u["reason"]) for u in unlisted] == [
        ("thumbnail_broken", "thumbnail", reason)]
    # The overlay pages do not tell it.
    assert LayoutCatalog.Scan(["scoreboard"], tmp_path)[1] == []


# ── the v1 routes ────────────────────────────────────────────────────────

def test_a_thumbnail_is_made_through_v1(api):
    made = api.post(CREATE, json={"kind": "thumbnail", "name": "Neon",
                                  "displayName": "ネオン"},
                    headers=api.shell)

    assert made.status_code == 201, made.get_data(as_text=True)
    assert made.headers["Cache-Control"] == "no-store"
    entry = made.get_json()["data"]
    assert entry["id"] == "thumbnail_Neon" and entry["contract"] == 2
    listed = api.get("/api/v1/layouts?kind=thumbnail").get_json()["data"]
    assert "thumbnail_Neon" in [e["id"] for e in listed]
    shown = api.post(REVEAL, json={"id": entry["id"]}, headers=api.shell)
    assert shown.status_code == 200
    assert len(api.started) == 1


def test_the_thumbnails_ai_prompt_is_read_through_v1(api):
    answer = api.get(_prompt("thumbnail"), headers=api.shell)

    assert answer.status_code == 200
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.get_json()["data"] == {
        "kind": "thumbnail",
        "text": LayoutKit._WithoutMark((THUMBNAIL_KIT / "AI_PROMPT.md")
                                       .read_text(encoding="utf-8"))}


def test_a_page_of_this_origin_cannot_make_a_thumbnail(api):
    same = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty"}
    assert api.post(CREATE, json={"kind": "thumbnail", "name": "x"},
                    headers=same).status_code == 403
    assert api.get(_prompt("thumbnail"), headers=same).status_code == 403
    assert _left(api.root) == []


def test_the_release_ships_the_thumbnail_kit():
    # Every file core copies is in the kit (and so in git and the zip).
    for source in LayoutKit.KitFiles("thumbnail").values():
        assert (KIT / source).is_file(), source
