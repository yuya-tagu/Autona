"""The AppShell's tables held to what the Qt main window said (ADR-005 S-final
SF-5a-3 / SF-5b-3).

/web/app/<panel> (web/src/AppShell) is the desktop's frame: the sidebar's
navigation, its operations header and its Help & Settings footer. SF-5a-3
held it to the Qt frame's; SF-5b-3 deleted the Qt frame (the window is one
shared view showing /web/app/<panel>), so NAVIGATION below is the frozen copy
of the deleted Window._navSections: the sections, order, panels, icons and
the words (as their Qt translations' (context, source)). The menu pages are
src/AutonaWindowState.py's MENU_PAGES (the pages the window does not reopen
at the next start). Every word the AppShell shares with the old Qt frame, in
English and Japanese, is still what the Qt frame showed (i18next's app_*
against QT_WORDS: the frame's translations, frozen when ADR-005 S-final SF-7
took them out of src/i18n/*.ts, which hold the native dialogs' words alone
now): the words the desktop showed stay. The outside addresses a browser
links are src/AutonaExternalLinks.py's.

The route itself (/web/app/<panel>: STATIC, the same names, 404 otherwise,
no other page's frame) is tests/test_web_access.py's.
"""
import json
import re
from pathlib import Path

import pytest

from src import AutonaExternalLinks, AutonaWindowState
from src.core import WebAccess

ROOT = Path(__file__).resolve().parent.parent
APP_SHELL = ROOT / "web" / "src" / "AppShell"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

# The AppShell's words that are the Qt frame's too: i18next key -> the Qt
# translation (context, source). The navigation's headings and labels are
# matched through NAVIGATION below.
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


# The deleted Qt sidebar's navigation (Window._navSections up to SF-5b-3),
# frozen: [(heading, [(panel, icon, label), ...]), ...], the heading and the
# label as their QApplication.translate() calls' (context, source).
NAVIGATION = [
    (("app", "Setup"), [
        ("event", "tournament.svg", ("app", "Tournament Info")),
        ("stream", "megaphone.svg", ("app", "Stream Settings")),
    ]),
    (("app", "ScoreboardCategory"), [
        ("scoreboard", "scoreboard.svg", ("app", "Scoreboard")),
        ("board", "board.svg", ("app", "Board")),
    ]),
    (("app", "Display"), [
        ("thumbnail", "thumbnail.svg", ("app", "Thumbnail Settings")),
        ("result", "result.svg", ("app", "Result Settings")),
        ("top8", "bracket.svg", ("app", "Top 8 Display Settings")),
        ("commentary", "commentary.svg", ("app", "Commentary")),
        ("statsPreview", "stats.svg", ("app", "Stats Display Settings")),
    ]),
    (("app", "Data"), [
        ("stats", "stats.svg", ("app", "Statistics")),
        ("playerBrowser", "players.svg", ("app", "Player List")),
    ]),
]

# What the deleted Qt frame showed for its words, frozen from
# src/i18n/Autona_{en,ja}.ts before ADR-005 S-final SF-7 took them out:
# (context, source) -> (English, Japanese).
QT_WORDS = {
    ("app", "Show sidebar"): ("Show sidebar", "サイドバーを表示"),
    ("app", "Set tournament"): ("Set tournament", "大会を選ぶ"),
    ("app", "Unset tournament"): ("Unset tournament", "大会の設定を解除"),
    ("app", "Set tournament URL"): ("Set tournament URL", "大会のURLを貼る"),
    ("app", "Confirm"): ("Confirm", "確認"),
    ("app", "Start.gg との接続を解除します。よろしいですか？"): (
        "Disconnect from Start.gg. Are you sure?",
        "Start.gg との接続を解除します。よろしいですか？"),
    ("app", "Select game title"): ("Select game title", "ゲームを選ぶ"),
    ("app", "Add game assets"): ("Add game assets", "ゲームアセットを追加"),
    ("app", "Modded content"): ("Modded content", "MODコンテンツ"),
    ("app", "Help & Settings"): ("Help & Settings", "ヘルプ＆設定"),
    ("Settings", "Settings"): ("Settings", "設定"),
    ("app", "Always on top"): ("Always on top", "常に手前に表示"),
    ("app", "API Settings"): ("API Settings", "API設定"),
    ("app", "Open reference & tester"): (
        "Open reference & tester", "リファレンス・テスターを開く"),
    ("app", "Companion settings"): ("Companion settings", "Companion の設定"),
    ("app", "OBS / Advanced Scene Switcher"): (
        "OBS / Advanced Scene Switcher", "OBS / Advanced Scene Switcher"),
    ("app", "Check for updates"): ("Check for updates", "更新情報を調べる"),
    ("app", "Join the Discord community"): (
        "Join the Discord community", "Discordコミュニティに参加する"),
    ("app", "Event support inquiries"): (
        "Event support inquiries", "イベント運営支援のご相談"),
    ("app", "About Autona"): ("About Autona", "Autona について"),
    ("app", "Setup"): ("Setup", "事前設定"),
    ("app", "Tournament Info"): ("Tournament Info", "大会情報設定"),
    ("app", "Stream Settings"): ("Stream Settings", "配信チャンネル設定"),
    ("app", "ScoreboardCategory"): ("Scoreboard", "スコアボード"),
    ("app", "Scoreboard"): ("Scoreboard", "スコアボード入力"),
    ("app", "Board"): ("Board", "スコアボード設定"),
    ("app", "Display"): ("Display", "表示設定"),
    ("app", "Thumbnail Settings"): ("Thumbnail Settings", "対戦動画サムネイル"),
    ("app", "Result Settings"): ("Result Settings", "大会リザルト"),
    ("app", "Top 8 Display Settings"): ("Top 8 Display Settings", "TOP8表示設定"),
    ("app", "Commentary"): ("Commentary", "実況・解説設定"),
    ("app", "Stats Display Settings"): (
        "Stats Display Settings", "統計情報表示設定"),
    ("app", "Data"): ("Data", "各種データ"),
    ("app", "Statistics"): ("Statistics", "統計情報"),
    ("app", "Player List"): ("Player List", "選手一覧"),
}

# The deleted Window.MENU_PAGES, frozen (now AutonaWindowState.MENU_PAGES).
MENU_PAGES = ["settings", "api", "companion", "obs", "about", "assets"]


def _nav_table():
    return json.loads((APP_SHELL / "navSections.json").read_text(
        encoding="utf-8"))


def _locale(language):
    return json.loads((LOCALES / f"{language}.json").read_text(
        encoding="utf-8"))


def _qt_translations(language):
    """{(context, source): what the Qt frame showed} in `language` (en /
    ja), from QT_WORDS."""
    index = {"en": 0, "ja": 1}[language]
    return {key: words[index] for key, words in QT_WORDS.items()}


def test_the_frozen_words_are_every_shared_one():
    shared = set(SHARED_WORDS.values())
    for heading, items in NAVIGATION:
        shared.add(heading)
        shared.update(label for _key, _icon, label in items)
    assert shared == set(QT_WORDS)


def test_the_navigation_is_the_qt_sidebars():
    table = _nav_table()
    assert [[(item["panel"], item["icon"]) for item in section["items"]]
            for section in table] == [
        [(key, icon) for key, icon, _label in items]
        for _heading, items in NAVIGATION]


@pytest.mark.parametrize("language", ["en", "ja"])
def test_the_navigations_words_are_the_qt_sidebars(language):
    qt = NAVIGATION
    table = _nav_table()
    assert len(table) == len(qt)
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
    assert re.findall(r'"([^"]+)"', match.group(1)) == MENU_PAGES
    assert list(AutonaWindowState.MENU_PAGES) == MENU_PAGES


def test_every_page_the_app_shell_opens_is_a_web_panel():
    panels = [item["panel"] for section in _nav_table()
              for item in section["items"]]
    assert len(panels) == len(set(panels))
    for panel in [*panels, *MENU_PAGES]:
        assert WebAccess.IsWebPanel(panel), panel


def test_the_legacy_index_table_is_the_deleted_sidebars():
    # The legacy QSettings sidebarIndex_v22 counted the Qt sidebar's items
    # in this order (AutonaWindowState migrates it once). Held to the frozen
    # copy, not to navSections.json: the table reads what that sidebar
    # saved, and stays as it is whatever the AppShell's navigation becomes.
    assert list(AutonaWindowState.LEGACY_SIDEBAR_PANELS) == [
        panel for _heading, items in NAVIGATION
        for panel, _icon, _label in items]


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
