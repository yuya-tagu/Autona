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


# The receivers of Qt's translate (QtCore's, QtGui's and QtWidgets' app).
QT_APPLICATIONS = ("QCoreApplication", "QGuiApplication", "QApplication")


def _isQtTranslate(node):
    """`QCoreApplication.translate` (or the other apps', also through their
    module: `QtCore.QCoreApplication.translate`). Not `str.translate`."""
    if not (isinstance(node, ast.Attribute) and node.attr == "translate"):
        return False
    receiver = node.value
    name = (receiver.id if isinstance(receiver, ast.Name)
            else receiver.attr if isinstance(receiver, ast.Attribute)
            else None)
    return name in QT_APPLICATIONS


def _isNoop(node):
    return ((isinstance(node, ast.Name) and node.id == "QT_TRANSLATE_NOOP")
            or (isinstance(node, ast.Attribute)
                and node.attr == "QT_TRANSLATE_NOOP"))


def _selfAttribute(node):
    """X of `self.X`, else None."""
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    return None


def _withClass(node, cls=None):
    """Every node under `node`, with the name of the class it is in."""
    for child in ast.iter_child_nodes(node):
        inner = child.name if isinstance(child, ast.ClassDef) else cls
        yield child, inner
        yield from _withClass(child, inner)


def translationCalls(path):
    """Every Qt translation a module asks for: [(line, context, source)].

    Picked up: `QCoreApplication.translate(context, source)` (and the other
    apps'), a name or `self` attribute bound to it
    (`translate = QCoreApplication.translate`), `QT_TRANSLATE_NOOP(context,
    source)` and `self.tr(source)` (the context is its class's name). Other
    receivers (`str.translate(table)`) are not Qt's. Fails at what could not
    be checked against the .ts files: a context or source that is not a
    constant string, `self.tr` outside a class, or Qt's translate handed on
    as a value (an injection: `translate=QCoreApplication.translate`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names, selfNames, bound = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _isQtTranslate(node.value):
            bound.add(id(node.value))
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                else:
                    assert _selfAttribute(target), f"{path.name}:{node.lineno}"
                    selfNames.add(_selfAttribute(target))

    asked, called = [], set()
    for node, cls in _withClass(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        where = f"{path.name}:{node.lineno}"
        if (_isQtTranslate(func) or _isNoop(func)
                or (isinstance(func, ast.Name) and func.id in names)
                or _selfAttribute(func) in selfNames):
            called.add(id(func))
            words = node.args[:2]
            assert len(words) == 2, where
        elif _selfAttribute(func) == "tr":
            assert cls is not None, where
            words = [ast.Constant(cls), *node.args[:1]]
            assert len(words) == 2, where
        else:
            continue
        assert all(isinstance(word, ast.Constant)
                   and isinstance(word.value, str) for word in words), where
        asked.append((node.lineno, words[0].value, words[1].value))

    handedOn = [node.lineno for node in ast.walk(tree)
                if _isQtTranslate(node)
                and id(node) not in called and id(node) not in bound]
    assert handedOn == [], (path.name, handedOn)
    return asked


@pytest.mark.parametrize("code, asked", [
    ('QCoreApplication.translate("c", "s")', [("c", "s")]),
    ('QtCore.QCoreApplication.translate("c", "s")', [("c", "s")]),
    ('QApplication.translate("c", "s")', [("c", "s")]),
    ('t = QCoreApplication.translate\nt("c", "s")', [("c", "s")]),
    ('class A:\n    def f(self):\n'
     '        self._t = QCoreApplication.translate\n'
     '        self._t("c", "s")', [("c", "s")]),
    ('QT_TRANSLATE_NOOP("c", "s")', [("c", "s")]),
    ('class Box:\n    def f(self):\n        self.tr("s")', [("Box", "s")]),
    ('"abc".translate(table)\nname.translate({1: 2})\nother.tr("s")', []),
])
def test_the_translation_calls_are_qts(tmp_path, code, asked):
    path = tmp_path / "module.py"
    path.write_text(code, encoding="utf-8")
    assert [(context, source)
            for _line, context, source in translationCalls(path)] == asked


@pytest.mark.parametrize("code", [
    'QCoreApplication.translate(context, "s")',
    'QCoreApplication.translate("c")',
    'make(translate=QCoreApplication.translate)',
    'def f(self):\n    self.tr("s")',
])
def test_what_cannot_be_checked_fails(tmp_path, code):
    path = tmp_path / "module.py"
    path.write_text(code, encoding="utf-8")
    with pytest.raises(AssertionError):
        translationCalls(path)


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
        assert translationCalls(path) == [], path.name
