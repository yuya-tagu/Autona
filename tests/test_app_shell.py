"""The AppShell's tables held to the Qt main window's (ADR-005 S-final SF-5a-3).

/web/app/<panel> (web/src/AppShell) draws the Qt window's frame in React: the
sidebar's navigation, its operations header and its Help & Settings footer.
Until SF-5b the desktop still shows the Qt frame, so the two must say the
same: the navigation's sections, order, panels and icons
(web/src/AppShell/navSections.json against Window._navSections, read from
src/Autona.py's source -- importing it starts the app), the menu pages, and
every word the AppShell shares with the Qt frame, in English and Japanese
(i18next's app_* against the Qt .ts files). The outside addresses a browser
links are src/AutonaExternalLinks.py's.

The route itself (/web/app/<panel>: STATIC, the same names, 404 otherwise,
no other page's frame) is tests/test_web_access.py's.
"""
import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src import AutonaExternalLinks
from src.core import WebAccess

ROOT = Path(__file__).resolve().parent.parent
APP_SHELL = ROOT / "web" / "src" / "AppShell"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"
TS_FILES = {"en": ROOT / "src" / "i18n" / "Autona_en.ts",
            "ja": ROOT / "src" / "i18n" / "Autona_ja.ts"}

# The AppShell's words that are the Qt frame's too: i18next key -> the Qt
# translation (context, source). The navigation's headings and labels are
# matched through _navSections below.
SHARED_WORDS = {
    "app_show_sidebar": ("app", "Show sidebar"),
    "app_set_tournament": ("app", "Set tournament"),
    "app_unset_tournament": ("app", "Unset tournament"),
    "app_set_tournament_url": ("app", "Set tournament URL"),
    "app_confirm": ("app", "Confirm"),
    "app_unset_confirm": (
        "app", "Start.gg との接続を解除します。よろしいですか？"),
    "app_select_game": ("app", "Select game title"),
    "app_add_game_assets": ("app", "Add game assets"),
    "app_modded_content": ("app", "Modded content"),
    "app_help_settings": ("app", "Help & Settings"),
    "app_settings": ("Settings", "Settings"),
    "app_always_on_top": ("app", "Always on top"),
    "app_api_settings": ("app", "API Settings"),
    "app_open_reference": ("app", "Open reference & tester"),
    "app_companion_settings": ("app", "Companion settings"),
    "app_obs_settings": ("app", "OBS / Advanced Scene Switcher"),
    "app_check_updates": ("app", "Check for updates"),
    "app_discord": ("app", "Join the Discord community"),
    "app_inquiry": ("app", "Event support inquiries"),
    "app_about": ("app", "About Autona"),
}


def _window_class():
    tree = ast.parse((ROOT / "src" / "Autona.py").read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name == "Window")


def _translated(node):
    """("context", "source") of a QApplication.translate(...) call."""
    assert isinstance(node, ast.Call), ast.dump(node)
    assert ast.unparse(node.func) == "QApplication.translate", ast.dump(node)
    context, source = node.args
    return context.value, source.value


def _qt_nav_sections():
    """Window._navSections as [(heading, [(key, icon, label), ...]), ...],
    the heading and the label as their translate() calls' (context,
    source)."""
    for node in ast.walk(_window_class()):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and ast.unparse(node.targets[0]) == "self._navSections"):
            sections = []
            for section in node.value.elts:
                heading, items = section.elts
                sections.append((_translated(heading), [
                    (item.elts[3].value, item.elts[1].value,
                     _translated(item.elts[2]))
                    for item in items.elts]))
            return sections
    raise AssertionError("Window._navSections not found")


def _qt_menu_pages():
    for node in _window_class().body:
        if (isinstance(node, ast.Assign)
                and ast.unparse(node.targets[0]) == "MENU_PAGES"):
            return [element.value for element in node.value.elts]
    raise AssertionError("Window.MENU_PAGES not found")


def _nav_table():
    return json.loads((APP_SHELL / "navSections.json").read_text(
        encoding="utf-8"))


def _locale(language):
    return json.loads((LOCALES / f"{language}.json").read_text(
        encoding="utf-8"))


def _qt_translations(language):
    """{(context, source): what the program shows} of the .ts file: the
    translation, or the source where there is none."""
    words = {}
    for context in ET.parse(TS_FILES[language]).getroot().findall("context"):
        name = context.findtext("name")
        for message in context.findall("message"):
            source = message.findtext("source")
            translation = message.find("translation")
            text = translation.text if translation is not None else None
            words[(name, source)] = text or source
    return words


def test_the_navigation_is_the_qt_sidebars():
    qt = _qt_nav_sections()
    table = _nav_table()
    assert [[(item["panel"], item["icon"]) for item in section["items"]]
            for section in table] == [
        [(key, icon) for key, icon, _label in items] for _heading, items in qt]


@pytest.mark.parametrize("language", ["en", "ja"])
def test_the_navigations_words_are_the_qt_sidebars(language):
    qt = _qt_nav_sections()
    table = _nav_table()
    words = _locale(language)
    translations = _qt_translations(language)
    for section, (heading, items) in zip(table, qt):
        assert words[section["heading"]] == translations[heading], heading
        for item, (_key, _icon, label) in zip(section["items"], items):
            assert words[item["label"]] == translations[label], label


@pytest.mark.parametrize("language", ["en", "ja"])
def test_the_shared_words_are_the_qt_frames(language):
    words = _locale(language)
    translations = _qt_translations(language)
    assert {key: words.get(key) for key in SHARED_WORDS} == {
        key: translations[qt] for key, qt in SHARED_WORDS.items()}


def test_both_languages_have_every_app_word():
    en = {key for key in _locale("en") if key.startswith("app_")}
    ja = {key for key in _locale("ja") if key.startswith("app_")}
    assert en == ja
    used = set(SHARED_WORDS)
    for section in _nav_table():
        used.add(section["heading"])
        used.update(item["label"] for item in section["items"])
    assert used <= en


def test_the_menu_pages_are_the_windows():
    text = (APP_SHELL / "navTable.ts").read_text(encoding="utf-8")
    match = re.search(r"export const MENU_PAGES = \[([^\]]*)\]", text)
    assert match, "MENU_PAGES not found in navTable.ts"
    assert re.findall(r'"([^"]+)"', match.group(1)) == _qt_menu_pages()


def test_every_page_the_app_shell_opens_is_a_web_panel():
    panels = [item["panel"] for section in _nav_table()
              for item in section["items"]]
    assert len(panels) == len(set(panels))
    for panel in [*panels, *_qt_menu_pages()]:
        assert WebAccess.IsWebPanel(panel), panel


def test_every_icon_is_served():
    icons = {item["icon"] for section in _nav_table()
             for item in section["items"]}
    # The header's and the footer's (the Qt sidebar's too).
    icons |= {"menu.svg", "tournament.svg", "gamepad.svg", "settings.svg",
              "chevron_down.svg", "update_circle.svg"}
    for icon in sorted(icons):
        assert (ROOT / "assets" / "icons" / icon).is_file(), icon
        assert WebAccess.StaticPath(f"assets/icons/{icon}"), icon


def test_the_outside_addresses_are_the_desktops():
    text = (APP_SHELL / "externalLinks.ts").read_text(encoding="utf-8")
    discord = re.search(r'export const DISCORD_URL = "([^"]+)";', text)
    assert discord and discord.group(1) == AutonaExternalLinks.DISCORD_URL
    # The inquiry's address (the About page imports it from here), and it
    # as mailto:.
    email = re.search(r'export const BUSINESS_INQUIRY_EMAIL = "([^"]+)";',
                      text)
    assert email and (
        email.group(1) == AutonaExternalLinks.BUSINESS_INQUIRY_EMAIL)
    assert ("export const BUSINESS_INQUIRY_URL = "
            "`mailto:${BUSINESS_INQUIRY_EMAIL}`;") in text
    assert AutonaExternalLinks.BUSINESS_INQUIRY_URL == (
        f"mailto:{AutonaExternalLinks.BUSINESS_INQUIRY_EMAIL}")
    assert set(AutonaExternalLinks.EXTERNAL_LINKS) == {"discord", "inquiry"}
