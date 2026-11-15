"""Core's own words (core/CoreWords.py, ADR-005 S-final SF-7).

Until SF-7 the thumbnail and the result services were given Qt's translator
(``translate=QCoreApplication.translate`` from ``bootstrap_core()``) and asked
it for the ``app`` / ``thumb_app`` contexts of src/i18n/*.ts. Their words are
a table in core now, so core needs no translator and the .ts files hold the
native dialogs' words alone (tests/test_web_panel_host.py pins what is left
of them). Pinned here:

- the words are the ones the .ts files had, in English and Japanese
  (TS_WORDS: a frozen copy of the entries SF-7 took out of them, with the
  key each one became), so no user sees a word change (words added later
  are the table's alone);
- the language: the program locale's first subtag, English for anything but
  Japanese;
- the table is the services' words and nothing else: every key they name is
  in it, every entry is named by one of them;
- no translate injection is left: neither service takes one, bootstrap passes
  none, core asks no translator.

What the services answer in each language is their own tests'
(tests/test_thumbnail_service.py, tests/test_result_service.py).
"""
import ast
import string
from pathlib import Path

import pytest

from src.core import CoreWords
from src.core import ResultService as results
from src.core import ThumbnailService as thumbnails

ROOT = Path(__file__).resolve().parent.parent

# key -> (the .ts context, the source, the English translation, the Japanese
# translation), as src/i18n/Autona_{en,ja}.ts had them when SF-7 took them
# out (frozen).
TS_WORDS = {
    "thumbnail_template_type_a": ("app", "Standard", "Standard", "標準"),
    "thumbnail_template_type_b": (
        "app", "Center Panel", "Center Panel", "中央パネル"),
    "thumbnail_no_game": (
        "thumb_app", "Please select a game first",
        "Please select a game first", "ゲームを選んで下さい"),
    "thumbnail_no_player": (
        "thumb_app", "Player {0} tag missing", "Player {0} tag missing",
        "プレイヤー{0}のタグがありません"),
    "sample_tournament": (
        "app", "Sample Tournament", "Sample Tournament", "サンプル大会"),
    "sample_team": ("app", "Team {0}", "Team {0}", "チーム{0}"),
    "result_layout_horizontal": (
        "app", "Horizontal (1920x1080)", "Horizontal (1920x1080)",
        "横向き (1920x1080)"),
    "result_layout_horizontal_16": (
        "app", "Horizontal TOP16 (1920x1080)",
        "Horizontal TOP16 (1920x1080)", "横向き TOP16 (1920x1080)"),
    "result_layout_vertical": (
        "app", "Vertical (960x1080)", "Vertical (960x1080)",
        "縦向き (960x1080)"),
    "result_layout_vertical_geki": (
        "app", "Vertical Dark Gold 激 (960x1080)",
        "Vertical Dark Gold 激 (960x1080)",
        "縦向き Dark Gold 激 (960x1080)"),
    "result_layout_team": (
        "app", "Team / Crew (1920x1080)", "Team / Crew (1920x1080)",
        "チーム / クルー (1920x1080)"),
    "result_layout_team_split": (
        "app", "Team / Crew 縦並び (1920x1080)",
        "Team / Crew vertical (1920x1080)",
        "Team / Crew 縦並び (1920x1080)"),
}


def _fields(text):
    return sorted(name for _literal, name, _spec, _conversion
                  in string.Formatter().parse(text) if name is not None)


def test_the_words_are_the_ts_files():
    # Each word the .ts files had keeps its English and Japanese. A word
    # added after SF-7 (a new layout or template) is the table's alone: it
    # is not added to the frozen copy.
    assert {key: (CoreWords.WORDS[key]["en"], CoreWords.WORDS[key]["ja"])
            for key in TS_WORDS} == {
        key: (en, ja) for key, (_context, _source, en, ja) in TS_WORDS.items()}


def test_every_entry_has_both_languages_with_the_same_fields():
    for key, words in CoreWords.WORDS.items():
        assert set(words) == set(CoreWords.LANGUAGES), key
        assert all(words.values()), key
        assert _fields(words["en"]) == _fields(words["ja"]), key


@pytest.mark.parametrize("locale, language", [
    ("ja-JP", "ja"), ("ja", "ja"), ("ja_JP", "ja"), ("JA-jp", "ja"),
    ("en-US", "en"), ("en", "en"), ("pt-BR", "en"), ("fr", "en"),
    ("", "en"), (None, "en"), (5, "en"), ("jax", "en"),
])
def test_the_language_is_the_locales(locale, language):
    assert CoreWords.Language(locale) == language


def test_text_reads_the_table_in_the_locales_language():
    assert CoreWords.Text("sample_tournament", "ja-JP") == "サンプル大会"
    assert CoreWords.Text("sample_tournament", "en-US") == "Sample Tournament"
    assert CoreWords.Text("sample_team", "ja").format("A") == "チームA"
    with pytest.raises(KeyError):
        CoreWords.Text("no_such_words", "en")


def _namedKeys(path):
    """The keys a service names: the string constants it gives its _Text."""
    keys = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_Text" and node.args
                and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


def test_the_table_is_the_services_words():
    named = (set(thumbnails.TEMPLATE_NAMES.values())
             | set(results.LAYOUT_NAMES.values())
             | _namedKeys(ROOT / "src" / "core" / "ThumbnailService.py")
             | _namedKeys(ROOT / "src" / "core" / "ResultService.py"))
    assert named == set(CoreWords.WORDS)
    assert set(thumbnails.TEMPLATE_NAMES) == set(thumbnails.TEMPLATE_IDS)
    assert set(results.LAYOUT_NAMES) == set(results.LAYOUT_SUBDIRS)


def test_no_translator_is_injected_or_asked():
    import inspect

    for service in (thumbnails.ThumbnailService, results.ResultService):
        assert "translate" not in inspect.signature(service.__init__).parameters
    bootstrap = ast.parse((ROOT / "src" / "bootstrap.py").read_text(
        encoding="utf-8"))
    assert [keyword.arg for node in ast.walk(bootstrap)
            if isinstance(node, ast.Call) for keyword in node.keywords
            if keyword.arg == "translate"] == []
    for path in sorted((ROOT / "src" / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        asked = [node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)
                 and node.attr in ("translate", "_translate", "tr")]
        assert asked == [], path.name
