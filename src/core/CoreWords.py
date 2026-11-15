"""The few words core writes itself, in English and Japanese (ADR-005 S-final
SF-7).

Two services put words of their own into what they answer: the match
thumbnail (core/ThumbnailService: its templates' names, its placeholder
preview's tournament and team names, the text of a final thumbnail without a
match) and the tournament result (core/ResultService: its layouts' names, its
sample's tournament name). Until SF-7 they were Qt's translations
(``QCoreApplication.translate``, injected by ``bootstrap_core()``) of the
``app`` / ``thumb_app`` contexts of src/i18n/*.ts. They are this table now,
with the words those .ts files had (tests/test_core_words.py holds it to a
frozen copy of them), so core needs no translator and the .ts files hold the
native dialogs' words alone.

The language is the program locale's, the one the services are given already
(``AutonaLocaleHelper.programLocale``, read when used): Japanese for a ``ja``
locale, English otherwise. It is the locale ``LoadLocale`` loaded the .qm for
(the setting's language, or the system's when the setting is ``default``)
and the one the shared view gives the web UI's i18next (``lng``). A page
shows its own words first (i18next ``thumbnail_template_<id>`` /
``result_layout_<id>``, the server's name as the fallback); these are what
the v1 answers carry and the previews draw.

Import side effects: none (agent-pitfalls #1).
"""
import re

ENGLISH = "en"
JAPANESE = "ja"
LANGUAGES = (ENGLISH, JAPANESE)

# key -> {language: words}. A "{0}" is filled by the caller (.format).
WORDS = {
    # The thumbnail's templates (ThumbnailService.TEMPLATE_NAMES).
    "thumbnail_template_type_a": {ENGLISH: "Standard", JAPANESE: "標準"},
    "thumbnail_template_type_b": {ENGLISH: "Center Panel",
                                  JAPANESE: "中央パネル"},
    # A final thumbnail without a match (ThumbnailDataMissing's text).
    "thumbnail_no_game": {ENGLISH: "Please select a game first",
                          JAPANESE: "ゲームを選んで下さい"},
    "thumbnail_no_player": {ENGLISH: "Player {0} tag missing",
                            JAPANESE: "プレイヤー{0}のタグがありません"},
    # What a preview draws while there is nothing to draw (both services).
    "sample_tournament": {ENGLISH: "Sample Tournament",
                          JAPANESE: "サンプル大会"},
    "sample_team": {ENGLISH: "Team {0}", JAPANESE: "チーム{0}"},
    # The result's layouts (ResultService.LAYOUT_NAMES).
    "result_layout_horizontal": {ENGLISH: "Horizontal (1920x1080)",
                                 JAPANESE: "横向き (1920x1080)"},
    "result_layout_horizontal_16": {ENGLISH: "Horizontal TOP16 (1920x1080)",
                                    JAPANESE: "横向き TOP16 (1920x1080)"},
    "result_layout_vertical": {ENGLISH: "Vertical (960x1080)",
                               JAPANESE: "縦向き (960x1080)"},
    "result_layout_vertical_geki": {
        ENGLISH: "Vertical Dark Gold 激 (960x1080)",
        JAPANESE: "縦向き Dark Gold 激 (960x1080)"},
    "result_layout_team": {ENGLISH: "Team / Crew (1920x1080)",
                           JAPANESE: "チーム / クルー (1920x1080)"},
    "result_layout_team_split": {ENGLISH: "Team / Crew vertical (1920x1080)",
                                 JAPANESE: "Team / Crew 縦並び (1920x1080)"},
}


def Language(locale) -> str:
    """The language of the words for program locale `locale` ("ja-JP",
    "ja", "en-US", ...): its first subtag when it is one of LANGUAGES,
    English otherwise (and for what is not a string)."""
    if not isinstance(locale, str):
        return ENGLISH
    primary = re.split("[-_]", locale, maxsplit=1)[0].lower()
    return primary if primary in LANGUAGES else ENGLISH


def Text(key, locale) -> str:
    """The words of `key` (one of WORDS) in program locale `locale`'s
    language. A key that is not one raises KeyError (a mistake the tests
    catch, not a word to make up)."""
    return WORDS[key][Language(locale)]
