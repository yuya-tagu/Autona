"""The result's layout kit (ADR-008 D7 R4): "make a new design" on the
result page.

- the payload's table (layout/include/result_contract.json) is the code's:
  its keys only grow (TABLE_KEYS, a frozen copy), its sources are the
  result's rows of the state, the tournament's state keys of KEY_CATALOG,
  the manifest or the config, and every payload ResultService builds (the
  recorded scenarios, a players' and a teams' layout, the sample of a
  preview, the live preview's PreviewPayload) holds its keys with their
  types and nothing else; the sample payloads the generator writes do too;
- the AI_PROMPT.md is the instructions, the whole table and the samples;
- core's LayoutKit makes a result_ template package from the kit that the
  catalog lists as contract 2 with its rows (the manifest ADR-007 D1 asks
  for);
- the v1 routes make and read it as they do a thumbnail's (SHELL);
- a result_ folder the catalog cannot list is told under the result page
  alone.

The kit's sample drawn by the real renderer: tests/test_render_platform.py.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from src.core import LayoutCatalog, LayoutKit
from src.core import ResultService as results
from src.core.LayoutKit import LayoutKitError
from src.core.SettingsManager import SettingsManager
from tests.fixtures.result import scenarios as world
from tests.test_layout_kit import (  # noqa: F401 (fixtures)
    CREATE, KIT, REVEAL, _left, _prompt, api, root, started)
from tests.test_overlay_contract import NODE, catalog_paths, needs_node
from tests.test_result_service import (  # noqa: F401 (fixtures)
    _Catalog, _payloadOf, env)
from tests.test_thumbnail_kit import RESOLVED

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INCLUDE = PROJECT_ROOT / "layout" / "include"
TABLE = INCLUDE / "result_contract.json"
RESULT_KIT = KIT / "result"
NoneType = type(None)
TYPES = {"string": str, "number": (int, float), "object": dict,
         "array": list, "string|number|null": (str, int, float, NoneType),
         "array|string": (list, str)}
GROUPS = ("data", "config", "layout")
# The sources a key may have besides a state key: this folder's
# settings.json (the rows, the id) and the result's settings.
OTHER_SOURCES = {"manifest", "config"}
# A row's keys of the state (ResultService.DefaultPlayer / DefaultTeam).
ROW_ROOTS = {"result_players": set(results.DefaultPlayer(1)),
             "result_teams": set(results.DefaultTeam(1))}
# The keys the sample of a preview (no row has an input) does not have, as
# the table says: the tournament's date and entrants, a team row's
# characters, the characters of a players' row, a players' row's members.
SAMPLE_MISSING = {"data.startAt", "data.numEntrants",
                  "data.players[{R}].character", "data.players[{R}].characters",
                  "data.players[{R}].members"}

# The table's keys, frozen: contract 2 only adds keys (a renamed or removed
# one is contract 3). A key added to the table is added here too.
TABLE_KEYS = (
    "data.tournamentName",
    "data.eventName",
    "data.startAt",
    "data.numEntrants",
    "data.rows",
    "data.players",
    "data.players[{R}].rank",
    "data.players[{R}].name",
    "data.players[{R}].sponsor",
    "data.players[{R}].character",
    "data.players[{R}].character.assets.{A}.asset",
    "data.players[{R}].character.assets.{A}.eyesight",
    "data.players[{R}].character.assets.{A}.type",
    "data.players[{R}].character.assets.{A}.image_size",
    "data.players[{R}].characters",
    "data.players[{R}].characters[{C}]",
    "data.players[{R}].members",
    "data.players[{R}].members[{M}].name",
    "data.players[{R}].members[{M}].character",
    "config.main_icon_path",
    "config.background_path",
    "config.theme_color",
    "config.accent_color",
    "layout",
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _table():
    return _read(TABLE)


def _pattern(key):
    """A table key as segments: "[]" an item of a list, "{}" any key of a
    dict."""
    out = []
    for part in key.split("."):
        listed = re.fullmatch(r"(.+?)\[\{\w\}\]", part)
        if listed:
            out += [listed.group(1), "[]"]
        elif part.startswith("{") and part.endswith("}"):
            out.append("{}")
        else:
            out.append(part)
    return tuple(out)


def _nodes(value, path=()):
    """(segments, value) of every value in `value`, an item of a list at
    "[]"."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _nodes(child, (*path, key))
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child, (*path, "[]"))


def _matches(pattern, path):
    return len(pattern) == len(path) and all(
        p == s or (p == "{}" and s != "[]")
        for p, s in zip(pattern, path, strict=True))


def _typed(value, kind):
    if kind != "object" and kind != "array" and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[kind])


def _checked(payload, missing=frozenset()):
    """What of `payload` the table does not say: [(key, value)] of a wrong
    type, the paths no key names, the keys a payload always has that it
    lacks (but `missing`)."""
    rows = [(row["key"], _pattern(row["key"]), row["type"])
            for row in _table()["keys"]]
    wrong, unknown = [], []
    for path, value in _nodes(payload):
        named = [(key, kind) for key, pattern, kind in rows
                 if _matches(pattern, path)]
        for key, kind in named:
            if not _typed(value, kind):
                wrong.append((key, value))
        if named or not path:
            continue
        # A dict or list on the way to a key, or a value inside an object
        # the table names as a whole (a character's other keys).
        if any(len(pattern) > len(path)
               and _matches(pattern[:len(path)], path)
               for _key, pattern, _kind in rows):
            continue
        if any(kind == "object" and len(pattern) < len(path)
               and _matches(pattern, path[:len(pattern)])
               for _key, pattern, kind in rows):
            continue
        unknown.append(".".join(path))
    lacking = []
    for key, pattern, _kind in rows:
        if "{}" in pattern or pattern.count("[]") > 1 or key in missing:
            continue
        if "[]" not in pattern:
            if not any(_matches(pattern, path) for path, _v in _nodes(payload)):
                lacking.append(key)
            continue
        for row in payload["data"]["players"]:
            if pattern[-1] not in row:
                lacking.append(key)
                break
    return wrong, unknown, lacking


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
        assert any(key.split(".", 1)[0] == group for key in keys), group
    # Every placeholder a key has is told.
    used = set(re.findall(r"\{\w\}", " ".join(keys)))
    sources = " ".join(s for row in table["keys"] for s in row["source"])
    assert used | set(re.findall(r"\{\w\}", sources)) == set(
        table["placeholders"])


def test_the_tables_keys_only_grow():
    assert {row["key"] for row in _table()["keys"]} == set(TABLE_KEYS)


def test_every_source_is_a_state_key_the_manifest_or_the_config():
    paths = catalog_paths()
    for row in _table()["keys"]:
        for source in row["source"]:
            if source in OTHER_SOURCES or source in paths:
                continue
            root_key, *rest = source.split(".")
            assert root_key in ROW_ROOTS, (row["key"], source)
            # The rows, a row's whole, or one of a row's keys.
            assert rest in ([], ["{S}"]) or (
                rest[0] == "{S}" and len(rest) == 2
                and rest[1] in ROW_ROOTS[root_key]), (row["key"], source)
    config = [row for row in _table()["keys"]
              if row["key"].startswith("config.")]
    assert all(row["source"] == ["config"] for row in config)


@pytest.fixture
def colors(monkeypatch):
    monkeypatch.setattr(results, "GetFeatureColors",
                        lambda feature: ("#181B21", "#f0a500"))


def test_the_config_rows_are_what_every_render_is_handed(env, colors):
    rows = {row["key"].split(".", 1)[1]: row for row in _table()["keys"]
            if row["key"].startswith("config.")}
    config = results._Config()
    assert set(config) == set(rows)
    for key, row in rows.items():
        assert _typed(config[key], row["type"]), key
    # Nothing saved: the table's sample config.
    assert config == _table()["sampleConfig"]


# ── every payload the service builds ─────────────────────────────────────

@pytest.mark.parametrize("scenario", world.SCENARIOS,
                         ids=[s["name"] for s in world.SCENARIOS])
def test_every_payload_holds_the_tables_keys_and_nothing_else(env, scenario):
    data, config = _payloadOf(env, scenario)
    payload = {"data": data, "config": config, "layout": scenario["layout"]}
    # The sample of a preview: rows with nothing to draw.
    missing = SAMPLE_MISSING if scenario["preview"] and not results.HasData(
        scenario["layout"]) else set()
    wrong, unknown, lacking = _checked(payload, missing)
    assert (wrong, unknown) == ([], [])
    if not missing:
        assert lacking == []


@pytest.mark.parametrize("layout, rows, slots", [
    ("horizontal", "players", 8), ("horizontal_16", "players", 16),
    ("team", "teams", 8)])
def test_the_live_previews_payload_holds_the_tables_keys(
        env, layout, rows, slots):
    # GET /api/v1/result/payload: the sample of a preview (nothing typed),
    # and the rows once one is.
    SettingsManager.settings.setdefault(
        results.CONFIG_SETTING, {})["layout"] = layout
    preview = env.service.PreviewPayload(games=_Catalog())
    assert preview["contract"] == 2 and preview["rows"] == rows
    payload = preview["payload"]
    assert payload["data"]["rows"] == rows
    assert len(payload["data"]["players"]) == slots
    assert _checked(payload, SAMPLE_MISSING)[:2] == ([], [])

    if rows == "players":
        assert results.UpdatePlayer(1, {"name": "Alpha", "rank": 1}) is None
    else:
        assert results.UpdateTeam(1, {"name": "Crew", "members": [
            {"name": "Alpha"}]}) is None
    payload = env.service.PreviewPayload(games=_Catalog())["payload"]
    assert payload["data"]["players"][0]["name"] in ("Alpha", "Crew")
    assert _checked(payload) == ([], [], [])


# ── the sample payloads ──────────────────────────────────────────────────

@pytest.mark.parametrize("name, rows, slots", [
    ("sample_payload.json", "players", 16),
    ("sample_payload_teams.json", "teams", 8)])
def test_the_sample_payloads_hold_the_rows_with_their_types(name, rows, slots):
    payload = _read(RESULT_KIT / name)
    assert set(payload) == set(GROUPS)
    assert _checked(payload) == ([], [], [])
    data = payload["data"]
    assert data["rows"] == rows and len(data["players"]) == slots
    assert payload["config"] == _table()["sampleConfig"]
    assert payload["layout"] == "result_sample"
    ranks = [row["rank"] for row in data["players"]]
    # Ties: the same rank twice, rows not ranked by their place.
    assert len(set(ranks)) < len(ranks)
    # An empty row, as the app hands a slot without an input.
    assert data["players"][-1]["name"] == ""
    for row in data["players"]:
        assert len(row["characters"]) <= results.MAX_CHARACTERS
        assert len(row["members"]) <= results.MAX_MEMBERS
        if rows == "teams":
            assert row["character"] == {} and row["characters"] == []
        else:
            assert row["members"] == []
            assert row["character"] == (row["characters"] or [{}])[0]


# ── the AI_PROMPT.md ─────────────────────────────────────────────────────

def test_the_ai_prompt_holds_the_instructions_the_keys_and_the_samples():
    prompt = (RESULT_KIT / "AI_PROMPT.md").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    for name in ("result.html", "result.css", "result.js",
                 "sample_payload.json"):
        sample = (RESULT_KIT / name).read_text(encoding="utf-8")
        assert sample.replace("\r\n", "\n").strip() in prompt, name
    assert all(f"`{row['key']}`" in prompt for row in _table()["keys"])
    # The rules a chat AI must keep.
    for rule in ("result_<名前>", '"kind": "result"', '"contract": 2',
                 '"size": [1920, 1080]', '"rows": "players"', '"teams"',
                 "Autona.onRender", "async", "document.fonts.ready",
                 '"../../"', "/render-asset/", "file://", "CSP", "CDN",
                 "Google Fonts", "`size`", "overflow: hidden",
                 "[hidden] { display: none !important; }", "`Autona`",
                 "../include/autona.js", "../../assets/font/LINESeedJP/",
                 "textContent", "レイアウトの確認", "AI に貼る用にコピー",
                 "デザインを再読み込み", "読み込めなかったデザイン",
                 "out/results/", "エラーはありません",
                 # The thumbnail's acceptance test's lessons (R3).
                 "settings.json` は返さない", "（作ったときの表示名）",
                 "theme_color", "accent_color", "CSS だけ", "2 回目以降",
                 "document.title", "/web/app/result", "5107",
                 "「レイアウトの確認」の欄にも出ない",
                 # The result's own: the rows as the app hands them.
                 "同順位", "16 行", "8 行", "Top 16", "eyesight",
                 "numEntrants", "startAt", "sample_payload_teams.json",
                 "「作成」"):
        assert rule in prompt, rule
    full = (RESULT_KIT / "CONTRACT.md").read_text(encoding="utf-8")
    assert all(f"`{row['key']}`" in full for row in _table()["keys"])


def test_the_kits_sample_reads_the_payload_as_the_instructions_say():
    script = (RESULT_KIT / "result.js").read_text(encoding="utf-8")
    assert "Autona.onRender(" in script
    assert script.lstrip().startswith("(function")
    assert "document.fonts.ready" in script
    html = (RESULT_KIT / "result.html").read_text(encoding="utf-8")
    assert html.index("../include/autona.js") < html.index("./result.js")
    css = (RESULT_KIT / "result.css").read_text(encoding="utf-8")
    assert "width: 1920px" in css and "height: 1080px" in css
    assert "[hidden]" in css
    assert "http://" not in html + css + script
    assert "https://" not in html + css + script
    # It reads both kinds of rows, and skips an empty one.
    for key in ("members", "characters", "rank", "theme_color",
                "accent_color", "main_icon_path", "background_path"):
        assert key in script, key


@needs_node
def test_the_samples_resolve_follows_the_instructions():
    script = (RESULT_KIT / "result.js").read_text(encoding="utf-8")
    source = re.search(r"  function resolve\(path\) \{.*?\n  \}\n", script,
                       re.S).group(0)
    program = (source + "process.stdout.write(JSON.stringify("
               + json.dumps(list(RESOLVED)) + ".map(resolve)));")
    done = subprocess.run([NODE, "-e", program], capture_output=True,
                          text=True, encoding="utf-8", timeout=60, check=True)
    assert dict(zip(RESOLVED, json.loads(done.stdout), strict=True)) == RESOLVED


# ── core: making one ─────────────────────────────────────────────────────

def test_the_folder_is_the_result_prefix_and_the_name():
    assert LayoutKit.FolderName("result", "Neon") == "result_Neon"
    # The bundled results' folders, in any case.
    for name in ("team", "TOP16", "vertical_geki", "team_split", "vertical"):
        with pytest.raises(LayoutKitError) as refused:
            LayoutKit.FolderName("result", name)
        assert refused.value.code == LayoutKit.RESERVED


def test_a_new_result_is_made_from_the_kit_and_listed(root):
    entry = LayoutKit.NewDesign("result", "Neon", "ネオン", root=root)

    folder = root / "result_Neon"
    assert sorted(p.name for p in folder.iterdir()) == sorted([
        "result.html", "result.css", "result.js", "sample_payload.json",
        "sample_payload_teams.json", "settings.json", "AGENTS.md",
        "CLAUDE.md", "AI_PROMPT.md", "CONTRACT.md"])
    for name in ("result.html", "result.css", "result.js",
                 "sample_payload.json", "sample_payload_teams.json"):
        assert (folder / name).read_bytes() == (RESULT_KIT / name).read_bytes()
    assert (folder / "AGENTS.md").read_bytes() == (
        RESULT_KIT / "AGENTS.md.tmpl").read_bytes()
    assert (folder / "CLAUDE.md").read_bytes() == (
        KIT / "CLAUDE.md.tmpl").read_bytes()
    for name in ("AI_PROMPT.md", "CONTRACT.md"):
        copied = (folder / name).read_text(encoding="utf-8")
        kit = (RESULT_KIT / name).read_text(encoding="utf-8")
        assert copied == LayoutKit._WithoutMark(kit) != kit
    # The manifest a template package is listed by (ADR-007 D1 / D5).
    assert _read(folder / "settings.json") == {"autona": {
        "kind": "result", "contract": 2, "entry": "result.html",
        "size": [1920, 1080], "rows": "players",
        "displayName": "ネオン", "author": LayoutKit.DEFAULT_AUTHOR,
        "license": LayoutKit.DEFAULT_LICENSE}}
    assert entry["id"] == "result_Neon"
    assert entry["kind"] == "result" and entry["contract"] == 2
    listed = LayoutCatalog.Templates("result", root)
    assert listed["result_Neon"]["size"] == [1920, 1080]
    assert listed["result_Neon"]["entry"] == "result.html"
    assert listed["result_Neon"]["rows"] == "players"
    assert LayoutCatalog.IsTemplateFolder("result_Neon", root)
    # The result page lists it with sixteen rows, and draws it live.
    entries = results.LayoutEntries(root)
    assert entries["result_Neon"]["contract"] == 2
    assert results.SlotsFor("result_Neon", entries) == 16
    # Not an overlay, nor a thumbnail.
    assert LayoutCatalog.Layouts(list(LayoutKit.OVERLAY_KINDS), root) == []
    assert LayoutCatalog.Templates("thumbnail", root) == {}
    assert _left(root) == ["result_Neon"]


def test_a_result_kit_that_is_not_whole_makes_nothing(root):
    (root / "include" / "kit" / "result" / "sample_payload_teams.json").unlink()
    with pytest.raises(LayoutKitError) as refused:
        LayoutKit.NewDesign("result", "mine", root=root)
    assert refused.value.code == LayoutKit.KIT_MISSING
    assert _left(root) == []


def test_the_results_ai_prompt_is_the_kits_without_its_mark():
    text = (RESULT_KIT / "AI_PROMPT.md").read_text(encoding="utf-8")
    prompt = LayoutKit.AiPrompt("result", root=INCLUDE.parent)
    assert prompt == LayoutKit._WithoutMark(text)
    assert prompt.startswith("# ")


# ── the catalog: a result_ folder it cannot list ─────────────────────────

@pytest.mark.parametrize("settings, reason", [
    ("{not json", LayoutCatalog.UNLISTED_BAD_SETTINGS),
    (None, LayoutCatalog.UNLISTED_NO_MANIFEST),
])
def test_a_broken_result_folder_is_told_on_the_result_page(
        tmp_path, settings, reason):
    folder = tmp_path / "result_broken"
    folder.mkdir()
    (folder / "result.html").write_text("<html></html>", encoding="utf-8")
    if settings is not None:
        (folder / "settings.json").write_text(settings, encoding="utf-8")

    listed, unlisted = LayoutCatalog.Scan(["result"], tmp_path)
    assert listed == []
    assert [(u["folder"], u["kind"], u["reason"]) for u in unlisted] == [
        ("result_broken", "result", reason)]
    # Neither the thumbnail page nor an overlay page tells it.
    assert LayoutCatalog.Scan(["thumbnail"], tmp_path)[1] == []
    assert LayoutCatalog.Scan(["scoreboard"], tmp_path)[1] == []


# ── the v1 routes ────────────────────────────────────────────────────────

def test_a_result_is_made_through_v1(api):
    made = api.post(CREATE, json={"kind": "result", "name": "Neon",
                                  "displayName": "ネオン"},
                    headers=api.shell)

    assert made.status_code == 201, made.get_data(as_text=True)
    assert made.headers["Cache-Control"] == "no-store"
    entry = made.get_json()["data"]
    assert entry["id"] == "result_Neon" and entry["contract"] == 2
    listed = api.get("/api/v1/layouts?kind=result").get_json()["data"]
    assert "result_Neon" in [e["id"] for e in listed]
    shown = api.post(REVEAL, json={"id": entry["id"]}, headers=api.shell)
    assert shown.status_code == 200
    assert len(api.started) == 1


def test_the_results_ai_prompt_is_read_through_v1(api):
    answer = api.get(_prompt("result"), headers=api.shell)

    assert answer.status_code == 200
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.get_json()["data"] == {
        "kind": "result",
        "text": LayoutKit._WithoutMark((RESULT_KIT / "AI_PROMPT.md")
                                       .read_text(encoding="utf-8"))}


def test_a_page_of_this_origin_cannot_make_a_result(api):
    same = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty"}
    assert api.post(CREATE, json={"kind": "result", "name": "x"},
                    headers=same).status_code == 403
    assert api.get(_prompt("result"), headers=same).status_code == 403
    assert _left(api.root) == []


def test_the_release_ships_the_result_kit():
    # Every file core copies is in the kit (and so in git and the zip).
    for source in LayoutKit.KitFiles("result").values():
        assert (KIT / source).is_file(), source
    assert TABLE.is_file()
