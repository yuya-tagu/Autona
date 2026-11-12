"""State schema: what keys exist, what they default to, and how they are seeded.

Until now the answer to "what is in the state?" lived in whichever widget
happened to write a key first: the held-subtree exemption list was a class
attribute of ``AutonaScoreboardWidget`` (the Qt board, removed in ADR-005
S4ac-3), the display-option key set was
spelled out twice (once as that widget's ``elements``, once in the web
server's actions class), and the launch seeds were split
between two widget constructors and their manager. This module is the single
place those definitions live now (ADR-004 D3, headless core Step 2), and like
everything under ``src/core`` it must stay Qt-free -- so a headless run seeds
exactly the same state a GUI launch does.

One thing it deliberately is *not*:

- **Not the migration layer.** That is ``src/core/StateMigrations.py``
  (ADR-005 S3b): the persisted state (``user_data/state.json``) carries a
  ``state_version``, and a renamed key needs a step there -- a renamed
  ``objectName`` alone still breaks the saved state silently. The static key
  families below were introduced without renaming a single string precisely
  so none was owed; if one ever is, this catalog is the anchor listing the
  *old* names to read from, which is why it is written against the keys as
  they exist today rather than as they ought to be.

Step 5 finished the conversion of the dynamic keypaths widget by widget:
the SCORE panel's board-level and team-card inputs (``SCORE_MATCH_FIELDS`` /
``SCORE_TEAM_INPUT_KEYS``, read by ``AutonaActions``), the player card
(``PLAYER_INPUT_KEYS``, read by ``PlayerService``) and the EVENT tab (the
``TOURNAMENT_*_FIELDS``, read by ``TournamentInfoService``). Their Qt
widgets read them too -- the EVENT tab's until it became the React Event
page in ADR-005 S4g, the board's and the player card's until the Qt SCORE
went in S4ac-3.
``DYNAMIC_KEY_FAMILIES`` below -- the checklist that inventory shrank
against -- is empty as a result (known-smells S-14).

The final instalment added the **subtree roots** (``SCORE_ROOT`` and the rest,
below ``COLORS_FEATURE_KEYS``). They are here for the same reason the leaves
are: every one of them is now written from outside this module -- a core
service or ``AutonaActions`` -- and a root spelled at its write site drifts
exactly the way a leaf did. ``KEY_CATALOG`` is keyed by them.

Import side effects: none (agent-pitfalls #1). Every read and write happens
inside a call.
"""
import os
from typing import NamedTuple

# Upward imports into src/ are allowed only for modules that are themselves
# Qt-free (these two are pure state models, and are the candidates to move
# into core next). Importing a Qt-dependent module from here would make core
# unusable headless -- tests/test_core_qt_free.py detects it.
from . import CommentaryModel as comms
from . import Top8Model as top8
from .SettingsManager import SettingsManager
from .StateManager import StateManager


# ── Held subtree (反映) ───────────────────────────────────────────────────

# Sub-paths of a board's held subtree that AutonaStatsUtil fills with data
# fetched from the provider. Exempt from the 反映 hold so overlays and the
# stats preview update as soon as the fetch lands, instead of waiting for an
# operator who has no reason to press 反映 for data they did not type.
# Passed to StateManager.HoldSubtree(prefix, exempt=...).
STATS_KEYS = ("last_sets", "history_sets", "recent_sets", "upset_factor")


# ── display_options.* ────────────────────────────────────────────────────


class DisplayOption(NamedTuple):
    """One scoreboard field-visibility toggle.

    ``key`` is used verbatim on both sides of the mirror:
    ``SettingsManager.display_options.{key}`` is the persisted local default
    and ``StateManager.display_options.{key}`` is what the Web UI and the OBS
    overlays render from.

    ``fields`` are the player card's field names each toggle hid. Nothing
    reads them since ADR-005 S4ac-3: the Qt board's eye menu looked its
    widgets up by them (only ``show_sponsor`` by then), and the React SCORE
    page hides its fields by ``key``.
    """
    key: str
    fields: tuple
    default: bool


# The order is the eye-menu order, on the desktop and in the Web UI alike.
# web/src/ScoreboardPage/displayOptions.js mirrors this list -- keep the two
# in sync (there is no build step shared between them).
DISPLAY_OPTIONS = (
    DisplayOption("show_sponsor", ("team",), True),
    DisplayOption("show_name", ("name",), True),
    DisplayOption("show_seed", ("seedDisplay",), True),
    DisplayOption("show_characters", ("characters",), True),
    DisplayOption("show_social", ("twitter",), True),
    DisplayOption("show_real_name", ("real_name",), False),
    DisplayOption("show_location", ("country", "state"), True),
    DisplayOption("show_additional", ("custom_textbox",), False),
)

DISPLAY_OPTION_KEYS = tuple(option.key for option in DISPLAY_OPTIONS)
DISPLAY_OPTION_DEFAULTS = {
    option.key: option.default for option in DISPLAY_OPTIONS}


# ── Assets whose mtime is published as a cache-bust version ──────────────

# A reader appends these versions as a query string
# (``/layout/stream_org_icon.png?v={version}``) to get past the
# Cache-Control: max-age AutonaWebServer serves. No shipped overlay reads
# the stream org pair yet (checked 2026-10-31, ADR-005 S4d); the keys are
# kept as a contract for the layouts and the Web page that will. Paths are
# relative to the process CWD, like every other mutable path in the app
# (./out, ./user_data). Written by core/StreamService.py (the import and
# the reset) and seeded at launch by SeedPanelState() below.
STREAM_ORG_ICON_PATH = "./layout/stream_org_icon.png"
STREAM_ORG_THUMB_PATH = "./layout/stream_org_thumbnail.png"

STREAM_ASSET_VERSIONS = (
    ("stream.orgIconVersion", STREAM_ORG_ICON_PATH),
    ("stream.orgThumbVersion", STREAM_ORG_THUMB_PATH),
)


# ── Defaults for keys seeded here ────────────────────────────────────────

# Autona's own colors, used when neither the tournament nor a feature has a
# stored one. The persisted source of truth is SettingsManager
# (general.theme_color / general.accent_color); tournamentInfo.themeColor /
# accentColor are the display-side expansion the layouts read.
DEFAULT_THEME_COLOR = "#181B21"
DEFAULT_ACCENT_COLOR = "#f0a500"

# Root of the retired player-editor subtree. The Qt Player List wrote
# player_editor.1 while its editor dialog was open (until ADR-005 S4n, nothing
# writes it now); a session that crashed then left it behind, and LoadState
# would restore it forever.
PLAYER_EDITOR_ROOT = "player_editor"

# Whole subtrees dropped at every launch. All three belong to features that
# were removed, and all three are restored from the persisted state until
# something unsets them. StateMigrations' v0 -> v1 step drops a frozen copy
# of this list too (and of STALE_KEYS below); SeedPanelState() keeps doing
# it at every launch -- see the note there.
RETIRED_ROOTS = (
    "bracket",        # legacy full bracket, replaced by top8.* (2026-07)
    "player_list",    # retired PLAYERS roster editor
    PLAYER_EDITOR_ROOT,
)

# Residual keys from the pre-配信設定 layouts, where the stream title and the
# destination lived on the EVENT tab. Nothing reads them. Unset only when
# present: Unset queues a changed key unconditionally, and a fresh install
# would otherwise fire two no-op save cycles at every launch.
STALE_KEYS = (
    "tournamentInfo.streamTitleEdit",
    "tournamentInfo.streamUrl",
)


# ── Keypath catalog ──────────────────────────────────────────────────────
#
# A transcription of Design.md §3.1.2, verified against the writers in src/.
# Placeholders: {N} board 1..3, {T} team 1..2, {M} player index, {K}
# character index, {I} a list index. Index segments are always *strings* in
# the state ("1", not 1) -- agent-pitfalls #3.

# No BOARD/TEAM index tuples here on purpose: the board list already exists
# as AutonaActions.BOARDS (ints, used to iterate boards) and a second,
# string-typed spelling of the same thing is the drift this module is
# supposed to end. The ranges are in the placeholder note above.
#
# Slot-fixed indices, owned by the Qt-free models that also normalise them.
# Referenced rather than copied: two spellings of "there are 12 commentary
# slots" is exactly the drift this module exists to end.
COMMENTARY_SLOTS = tuple(comms.SLOTS)
TOP8_PLAYER_SLOTS = tuple(top8.PLAYER_SLOTS)
TOP8_MATCH_IDS = tuple(top8.MATCH_IDS)

# ── Statically named keys the SCORE panel writes ─────────────────────────
#
# Two of the board's key families used to be produced at runtime from Qt
# objectNames -- `score.{N}.{combo.objectName()}` for the match bar and
# `score.{N}.team.{T}.{widget.objectName()}` for the team cards (S-14). Since
# headless core Step 5 the names live here: AutonaActions writes them, and
# the Qt board (AutonaScoreboardWidget, until ADR-005 S4ac-3) looked its
# widgets up *by* them instead of reading them back off the widgets.
#
# The strings are unchanged on purpose. The persisted state holds keys named
# after the old objectNames, so renaming one here without a StateMigrations
# step is the same compatibility break it always was --
# tests/test_objectname_keypath_contract.py keeps the saved names as a
# frozen snapshot (EXPECTED).

# The match bar's three editable fields (in the .ui of boards 1 and 2 and
# built in code on the crew board until ADR-005 S4ac-3; /api/v1's PATCH set
# and the SCORE page now). Named individually (not just indexed into the
# tuple) for the same reason as TEAM_NAME_KEY / TEAM_LOSERS_KEY below: the
# writers name the one they write.
PHASE_KEY = "phase"
POOL_KEY = "pool"
MATCH_KEY = "match"
SCORE_MATCH_FIELDS = (PHASE_KEY, POOL_KEY, MATCH_KEY)

# The team card's two operator-editable inputs (the Qt team card's .ui
# until ADR-005 S4ac-3, where they were also findChild lookup keys).
TEAM_NAME_KEY = "teamName"
TEAM_LOSERS_KEY = "losers"
SCORE_TEAM_INPUT_KEYS = (TEAM_NAME_KEY, TEAM_LOSERS_KEY)

# The player card's operator-editable inputs (the Qt card's .ui, plus the
# note box built in code, until ADR-005 S4ac-3; /api/v1's player fields and
# the SCORE page now). These were the last family generated from Qt
# objectNames at runtime: one `findChildren(QLineEdit)` loop wrote
# `{path}.{element.objectName()}` and a matching `findChildren(QComboBox)`
# loop did the same for the two location combos. `PlayerService` writes
# them; the strings are unchanged, so an existing saved state still matches
# (tests/test_objectname_keypath_contract.py keeps them as a frozen
# snapshot).
#
# NOTE: the same names double as the field ids in DisplayOption.fields
# above -- `team`, `name`, `twitter`, `real_name`, `country`, `state` and
# `custom_textbox`.
PLAYER_SPONSOR_KEY = "team"
PLAYER_TAG_KEY = "name"
PLAYER_REAL_NAME_KEY = "real_name"
PLAYER_TWITTER_KEY = "twitter"
PLAYER_NOTE_KEY = "custom_textbox"     # the note (a QPlainTextEdit on the Qt card)
PLAYER_SEED_KEY = "seed"
PLAYER_BIRTHDAY_KEY = "birthday"       # nothing reads it
PLAYER_COUNTRY_KEY = "country"
PLAYER_STATE_KEY = "state"

# The four one-line text fields, in the Qt card's .ui order.
PLAYER_LINE_EDIT_KEYS = (
    PLAYER_SPONSOR_KEY,
    PLAYER_TAG_KEY,
    PLAYER_REAL_NAME_KEY,
    PLAYER_TWITTER_KEY,
)
# Every free-text input, i.e. what PlayerService.SetText accepts.
PLAYER_TEXT_KEYS = PLAYER_LINE_EDIT_KEYS + (PLAYER_NOTE_KEY,)
# The two combos whose selected row is published whole (a dict carrying the
# code, the display name and the flag asset). The card names the row by its
# code; PlayerService.SetCountry / SetRegion build the dict (ADR-005 S1d).
PLAYER_LOCATION_KEYS = (PLAYER_COUNTRY_KEY, PLAYER_STATE_KEY)

PLAYER_INPUT_KEYS = PLAYER_TEXT_KEYS + (
    PLAYER_SEED_KEY, PLAYER_BIRTHDAY_KEY) + PLAYER_LOCATION_KEYS

# Board-level keys under score.{N}. Nested groups are listed by their own
# tuples below rather than expanded here. SCORE_MATCH_FIELDS above is the
# operator-editable subset of this list (pinned by tests/test_state_schema).
SCORE_BOARD_KEYS = (
    "best_of",
    "best_of_short_text",
    "best_of_text",
    "first_to",
    "first_to_short_text",
    "first_to_text",
    "match",
    "match_en",              # raw English round name, for English-only output
    "phase",
    "pool",
    "set_id",
    "auto_update",
    "station",
    "station_queue",
    "teamsSwapped",
    "last_sets.{T}.{I}",
    "history_sets.{T}.{I}",
    "recent_sets",           # dict: sets, request_time
    "upset_factor",
)

SCORE_TEAM_KEYS = (
    TEAM_NAME_KEY,
    "score",
    "color",
    "logo",
    TEAM_LOSERS_KEY,
)

# PLAYER_INPUT_KEYS above is the operator-editable subset (pinned by
# tests/test_state_schema); the rest are derived or provider-supplied and are
# written by PlayerService from what the widget resolved.
#
# Three names in this list were wrong until the player instalment of Step 5,
# which is the drift a catalog nobody consumed was always going to collect:
# `gamerTag` is the *input* field name in a provider payload and has never
# been a state key (the published key is `name`), and the location combos
# publish their whole selected row under `country` / `state`, not the
# `country_code` / `state_code` that a payload and the player database use.
# `birthday` was missing.
SCORE_PLAYER_KEYS = (
    PLAYER_TAG_KEY,
    PLAYER_SPONSOR_KEY,
    PLAYER_REAL_NAME_KEY,
    PLAYER_TWITTER_KEY,
    PLAYER_NOTE_KEY,
    PLAYER_SEED_KEY,
    PLAYER_BIRTHDAY_KEY,
    PLAYER_COUNTRY_KEY,      # dict: code, name, display_name, en_name, asset
    PLAYER_STATE_KEY,        # dict: code, original_code, name, asset
    "id",
    "city",
    "online_avatar",
    "avatar",
    "mergedName",
    "mergedOnlyName",
    "character.{K}",         # dict: name, en_name, assets, skin, variant
    "mains",
    "sponsor_logo",
    "sponsor_logos.{I}",
)

# score.3 only. The crew board (CrewBattleService) writes the team and
# player keys above, plus a roster on top of them: `memberNum` is how many
# member rows are visible, `activeMember` the 1-based index of the member
# currently playing ("0" = nobody, and it is published as a *string*), and
# each member carries its own remaining `stock` (the team's `score` is the
# sum of the named members' stocks). Boards 1 and 2 never write any of these.
#
# Named individually, and consumed by `core/CrewBattleService.py`, for the
# same reason the families above are: the crew board built these key paths
# from string literals spelled out at each of its write sites, which is the
# half-converted state Step 5 exists to end. Unlike the other families they
# were never derived from a Qt objectName -- the Qt crew rows were built in
# code and had no objectName at all.
CREW_MEMBER_NUM_KEY = "memberNum"
CREW_ACTIVE_MEMBER_KEY = "activeMember"
CREW_STOCK_KEY = "stock"

SCORE_CREW_TEAM_KEYS = (
    CREW_MEMBER_NUM_KEY,
    CREW_ACTIVE_MEMBER_KEY,
)

SCORE_CREW_PLAYER_KEYS = (
    CREW_STOCK_KEY,
)

# Board-independent, written from the loaded set and read by AutonaStatsUtil
# for the upset-factor math. Not under score.{N} -- and wiped together with
# the rest of `score` by ResetScoreRoot (once per launch).
SCORE_SHARED_KEYS = ("bracket_type",)

GAME_KEYS = (
    "name",
    "abbreviation",
    "smashgg_id",
    "codename",
    "logo",
    "defaults.players_per_team",
    "defaults.characters_per_player",
    "mods_active",
    "has_stages",
    "has_variants",
    "has_colors",
    "has_modded_content",
)

# The three sub-roots of top8.*, written whole by core/Top8Service.py (the
# TOP 8 panel used to spell them at each of its six write sites).
TOP8_PHASE_LABEL_KEY = "phaseLabel"
TOP8_PLAYERS_KEY = "players"
TOP8_MATCHES_KEY = "matches"

TOP8_KEYS = (
    TOP8_PHASE_LABEL_KEY,
    "players.{I}.name",
    "players.{I}.sponsor",
    "players.{I}.country",          # {code, asset} -- CountryCatalog.BasicCountryInfo form
    "players.{I}.characters.{K}",
    "matches.{ID}.name",
    "matches.{ID}.slots",           # [int, int]; 0 = TBD, derived by Recompute
    "matches.{ID}.score",
    "matches.{ID}.finished",
    "matches.{ID}.active",          # only meaningful for gf_reset
)

COMMENTARY_SLOT_KEYS = (
    "name",
    "team",
    "twitter",
    "role",
    "icon",
    "note",
    "display",                      # int: 0 hidden, 1 shown (max 4 shown)
)

# ── Statically named keys the EVENT tab writes ───────────────────────────
#
# The last family generated from Qt objectNames at runtime (Step 5,
# instalment 3). The Qt EVENT panel ran three findChildren loops --
# QLineEdit / QSpinBox / QDateEdit -- and each wrote
# `tournamentInfo.{widget.objectName()}`; the panel then looked its inputs
# up *by* these constants, and `TournamentInfoService` writes them (it
# already named them for the provider side since Step 4b, as its
# {TEXT,NUMBER,DATE}_FIELDS -- which alias these tuples rather than
# respelling them).
#
# Split by kind because the behaviour differs per kind, not for tidiness: a
# text field carries a string, a number field an int, and a date field is
# published as a *formatted short date* (with its raw day beside it, below).
#
# The strings are unchanged, so an existing out/program_state.json still
# matches. The Qt tab (and the .ui whose objectNames these were, which
# tests/test_objectname_keypath_contract.py pinned) went in ADR-005 S4g: the
# React Event page writes the same keys through /api/v1.
TOURNAMENT_NAME_KEY = "tournamentName"
TOURNAMENT_EVENT_NAME_KEY = "eventName"
TOURNAMENT_ADDRESS_KEY = "address"
TOURNAMENT_ENTRANTS_KEY = "numEntrants"      # QSpinBox
TOURNAMENT_START_AT_KEY = "startAt"          # QDateEdit

TOURNAMENT_TEXT_FIELDS = (
    TOURNAMENT_NAME_KEY,
    TOURNAMENT_EVENT_NAME_KEY,
    TOURNAMENT_ADDRESS_KEY,
)
TOURNAMENT_NUMBER_FIELDS = (TOURNAMENT_ENTRANTS_KEY,)
TOURNAMENT_DATE_FIELDS = (TOURNAMENT_START_AT_KEY,)

# The raw date beside each date field (ADR-005 S4f): `YYYY-MM-DD`, the day
# the field holds, or None with it. Published next to the formatted short
# date (which stays as it was) so a reader -- the Event page's <input
# type="date"> -- gets the day back without parsing a locale's "%x". Turned
# into a timestamp (local midnight, in the PC's timezone) by
# core/TournamentInfoService.SetDate, never by a reader.
TOURNAMENT_START_AT_DATE_KEY = "startAtDate"
TOURNAMENT_RAW_DATE_KEYS = {TOURNAMENT_START_AT_KEY: TOURNAMENT_START_AT_DATE_KEY}

# The fields an operator edits, by the names /api/v1's PATCH
# /tournament/info takes them (ADR-005 S4g): the three texts, the entrant
# count and the start day -- its raw day, the formatted startAt being
# derived from it. They were the Qt EVENT tab's objectNames (with startAt
# for the date edit) until the tab became the React Event page.
TOURNAMENT_INPUT_KEYS = (
    TOURNAMENT_TEXT_FIELDS + TOURNAMENT_NUMBER_FIELDS
    + (TOURNAMENT_START_AT_DATE_KEY,))

# Provider timestamps with no field on the tab at all. Published as short
# date strings rather than as timestamps, which is the only thing that sets
# them apart from the pass-through keys below.
TOURNAMENT_SHORT_DATE_KEYS = ("endAt", "eventStartAt", "eventEndAt")

# Not an EVENT tab field: 表示大会名 is edited on the SCORE panel and on the
# crew board, and follows the tournament name by the rules in
# TournamentInfoService. Named here because that service writes it and this
# is where its key names live.
TOURNAMENT_DISPLAY_NAME_KEY = "displayTournamentName"
# Also not an EVENT tab field, and not TournamentInfoService's either: the
# tournament theme / accent pair is core/ColorService.py's, published under
# this historical prefix because that is what the layouts and ./out consumers
# have always read. The same two spellings name a per-feature override under
# colors.{feature} (COLORS_FEATURE_KEYS below), which is why they are defined
# once here rather than in each of the three places that used to say them.
THEME_COLOR_KEY = "themeColor"
ACCENT_COLOR_KEY = "accentColor"
# Cache-bust version of ./layout/logo.png, published like the stream org
# assets in STREAM_ASSET_VERSIONS above.
TOURNAMENT_LOGO_VERSION_KEY = "logoVersion"
# The tournament banner's file and its cache-bust version (ADR-005 S4f), the
# pair to the logo above. The file is where the thumbnail / result
# generators have always read it (they keep their own copy of the path); the
# version is new. Both are core/TournamentMediaService.py's, and the version
# is seeded at launch by SeedPanelState() below.
TOURNAMENT_BANNER_PATH = "./user_data/tournament_banner.png"
TOURNAMENT_BANNER_VERSION_KEY = "bannerVersion"
# What stands in for a tournament banner when none was saved: the bundled
# image the Event page and the two thumbnail generators fall back to (the
# stream thumbnail's and the match thumbnail's, ADR-005 S4o).
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
# The tournament media fetch's last run (ADR-005 S4f), one dict written whole
# by core/TournamentMediaService.py, shaped like stream.thumbnail: status
# idle | running | done | error, the error of a failed run (a code, see that
# module) and a revision that every run advances.
TOURNAMENT_MEDIA_KEY = "media"
# The tournament's set / clear (ADR-005 S4g review M1), both written by
# core/TournamentService.py: the URL set last ("" none) -- settings.json's
# TOURNAMENT_URL, published beside it: what the Event page's URL field
# follows -- and how the newest set / clear is going, one dict written
# whole and shaped like `media`: status idle | resolving | loading | loaded
# | error, the error (unresolved | load_failed, "" otherwise) and a revision
# that every set / clear advances (the request's: its progress keeps it).
TOURNAMENT_URL_KEY = "url"
TOURNAMENT_LOAD_KEY = "load"
TOURNAMENT_URL_SETTING = "TOURNAMENT_URL"

# One group is still open-ended and stays that way: whatever else the
# provider payload carries (`shortLink`, and the `initial_load` flag that
# rides along with it) is written through as-is, so enumerating it would be
# enumerating Start.gg's response shape.
TOURNAMENT_INFO_KEYS = (
    TOURNAMENT_TEXT_FIELDS + TOURNAMENT_NUMBER_FIELDS + TOURNAMENT_DATE_FIELDS
    + TOURNAMENT_SHORT_DATE_KEYS + (
        THEME_COLOR_KEY,
        ACCENT_COLOR_KEY,
        TOURNAMENT_DISPLAY_NAME_KEY,
        TOURNAMENT_LOGO_VERSION_KEY,
        "shortLink",
        TOURNAMENT_START_AT_DATE_KEY,
        TOURNAMENT_BANNER_VERSION_KEY,
        f"{TOURNAMENT_MEDIA_KEY}.status",
        f"{TOURNAMENT_MEDIA_KEY}.error",
        f"{TOURNAMENT_MEDIA_KEY}.revision",
        TOURNAMENT_URL_KEY,
        f"{TOURNAMENT_LOAD_KEY}.status",
        f"{TOURNAMENT_LOAD_KEY}.error",
        f"{TOURNAMENT_LOAD_KEY}.revision",
    )
)


def DefaultTournamentMedia() -> dict:
    """`tournamentInfo.media` before any run (a fresh dict on every call)."""
    return {"status": "idle", "error": "", "revision": 0}


def ReadTournamentLoad() -> dict:
    """`tournamentInfo.load` as published, a fresh dict: anything missing
    or malformed read as idle, with no error, at revision 0."""
    load = StateManager.Get(
        f"{TOURNAMENT_INFO_ROOT}.{TOURNAMENT_LOAD_KEY}", None)
    if not isinstance(load, dict):
        load = {}
    status, error = load.get("status"), load.get("error")
    revision = load.get("revision")
    return {
        "status": status if isinstance(status, str) else "idle",
        "error": error if isinstance(error, str) else "",
        "revision": revision if isinstance(revision, int)
        and not isinstance(revision, bool) and revision >= 0 else 0,
    }


STREAM_URL_KEY = "url"
STREAM_TITLE_KEY = "title"
# The title the generator wrote last (ADR-005 S4d), whether or not it is
# also in `title`: `title` holding it (or nothing) is what makes the title
# auto-generated -- replaced by the next auto title, and by what an
# operator types over it (core/StreamService.TitleIsAuto). Persisted with
# the rest of the state, so a title typed by hand survives a restart.
STREAM_TITLE_AUTO_KEY = "titleAuto"
# The stream thumbnail generator's last run (ADR-005 S4d), one dict written
# whole by core/StreamService.py: status idle | running | done | error, the
# path of the last thumbnail generated (relative to the app directory, "/"
# separated: "out/stream/thumbnail_....jpg"), the error of a failed run and
# a revision that every run advances (a reader's cache-bust).
STREAM_THUMBNAIL_KEY = "thumbnail"

STREAM_KEYS = (
    STREAM_URL_KEY,
    STREAM_TITLE_KEY,
    STREAM_TITLE_AUTO_KEY,
    "orgIconVersion",
    "orgThumbVersion",
    f"{STREAM_THUMBNAIL_KEY}.status",
    f"{STREAM_THUMBNAIL_KEY}.path",
    f"{STREAM_THUMBNAIL_KEY}.error",
    f"{STREAM_THUMBNAIL_KEY}.revision",
)


def DefaultStreamThumbnail() -> dict:
    """`stream.thumbnail` before any run (a fresh dict on every call)."""
    return {"status": "idle", "path": "", "error": "", "revision": 0}


# The tournament's match list (この大会の試合一覧, ADR-005 S4j): how its
# newest fetch is going, one dict written whole by core/AnalysisService.py,
# shaped like tournamentInfo.load: status idle | running | done | error, the
# error (no_phases | fetch_failed, "" otherwise), the phase groups fetched
# (`done`, failed or not) out of `total` (0 while the phases are fetched),
# the groups that gave no rows (`failed`) and a revision that every fetch
# (and every drop of the results) advances. The rows themselves stay in the
# service's memory (AnalysisService.Result): a tournament's hundreds of rows
# in the state would swell every delta and ./out.
ANALYSIS_KEYS = ("status", "error", "done", "total", "failed", "revision")


def DefaultAnalysis(revision=0) -> dict:
    """`analysis` with no fetch in progress or held, at `revision` (a fresh
    dict on every call)."""
    return {"status": "idle", "error": "", "done": 0, "total": 0,
            "failed": 0, "revision": revision}


def ReadAnalysis() -> dict:
    """`analysis` as published, a fresh dict: anything missing or malformed
    read as idle's (no error, nothing fetched, revision 0)."""
    value = StateManager.Get(ANALYSIS_ROOT, None)
    if not isinstance(value, dict):
        value = {}
    read = DefaultAnalysis()
    status, error = value.get("status"), value.get("error")
    if isinstance(status, str):
        read["status"] = status
    if isinstance(error, str):
        read["error"] = error
    for key in ("done", "total", "failed", "revision"):
        number = value.get(key)
        if isinstance(number, int) and not isinstance(number, bool) \
                and number >= 0:
            read[key] = number
    return read


# ── set_selection (ADR-005 S4v) ──────────────────────────────────────────
#
# How each of the lists the set selection fetches is going, one dict per
# list written whole by core/SetSelectionService.py: its status (idle /
# running / done / error), the error's code, the pages done of the total (0
# while their number is not known), what could not be fetched or read
# (`failed`) and a revision that every fetch (and every drop) advances. The
# rows stay in the service's memory, as the match list's do (see above).
SET_SELECTION_KINDS = ("phases", "sets", "stations")
SET_SELECTION_FETCH_KEYS = ANALYSIS_KEYS


def DefaultSetSelectionFetch(revision=0) -> dict:
    """One list's progress with no fetch in progress, at `revision` (a fresh
    dict on every call)."""
    return {"status": "idle", "error": "", "done": 0, "total": 0,
            "failed": 0, "revision": revision}


def ReadSetSelectionFetch(kind) -> dict:
    """List `kind`'s progress as published, a fresh dict: anything missing or
    malformed read as idle's (no error, nothing fetched, revision 0)."""
    value = StateManager.Get(f"{SET_SELECTION_ROOT}.{kind}", None)
    if not isinstance(value, dict):
        value = {}
    read = DefaultSetSelectionFetch()
    status, error = value.get("status"), value.get("error")
    if isinstance(status, str):
        read["status"] = status
    if isinstance(error, str):
        read["error"] = error
    for key in ("done", "total", "failed", "revision"):
        number = value.get(key)
        if isinstance(number, int) and not isinstance(number, bool) \
                and number >= 0:
            read[key] = number
    return read


def IdleSetSelection(advance=0) -> dict:
    """All of `set_selection` with no fetch in progress, each list's revision
    `advance` past the one published."""
    return {kind: DefaultSetSelectionFetch(
                ReadSetSelectionFetch(kind)["revision"] + advance)
            for kind in SET_SELECTION_KINDS}


# The match thumbnail's newest render (ADR-005 S4o), one dict written whole by
# core/ThumbnailService.py, shaped like stream.thumbnail: status idle | running
# | done | error, `path` the last thumbnail saved (out/thumbnails/*.png, what a
# preview does not change), the error of a failed run (a code, see that module,
# or the exception's text) and a revision that every run advances, which the
# preview image's URL carries.
THUMBNAIL_RENDER_KEY = "render"
THUMBNAIL_KEYS = (
    f"{THUMBNAIL_RENDER_KEY}.status",
    f"{THUMBNAIL_RENDER_KEY}.path",
    f"{THUMBNAIL_RENDER_KEY}.error",
    f"{THUMBNAIL_RENDER_KEY}.revision",
)


def DefaultThumbnailRender(revision=0) -> dict:
    """`thumbnail.render` with no run in progress or held, at `revision` (a
    fresh dict on every call)."""
    return {"status": "idle", "path": "", "error": "", "revision": revision}


def ReadThumbnailRender() -> dict:
    """`thumbnail.render` as published, a fresh dict: anything missing or
    malformed read as idle's (no path, no error, revision 0)."""
    value = StateManager.Get(f"{THUMBNAIL_ROOT}.{THUMBNAIL_RENDER_KEY}", None)
    if not isinstance(value, dict):
        value = {}
    read = DefaultThumbnailRender()
    for key in ("status", "path", "error"):
        if isinstance(value.get(key), str):
            read[key] = value[key]
    revision = value.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) \
            and revision >= 0:
        read["revision"] = revision
    return read


# The tournament result's render and its standings import (ADR-005 S4q;
# core/ResultService.py). Roots of their own, whose names cannot be taken for
# out/results or out/result_players. `result_render` is shaped like
# thumbnail.render. `result_standings`: status idle | running | done | error,
# the error of a failed import (a code, see that module) and a revision that
# every import advances.
RESULT_RENDER_KEYS = ("status", "path", "error", "revision")
RESULT_STANDINGS_KEYS = ("status", "error", "revision")


def DefaultResultRender(revision=0) -> dict:
    """`result_render` with no run in progress, at `revision` (a fresh dict on
    every call)."""
    return {"status": "idle", "path": "", "error": "", "revision": revision}


def _Revision(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value >= 0 else 0


def ReadResultRender() -> dict:
    """`result_render` as published, a fresh dict: anything missing or
    malformed read as idle's (no path, no error, revision 0)."""
    value = StateManager.Get(RESULT_RENDER_ROOT, None)
    if not isinstance(value, dict):
        value = {}
    read = DefaultResultRender(_Revision(value.get("revision")))
    for key in ("status", "path", "error"):
        if isinstance(value.get(key), str):
            read[key] = value[key]
    return read


def DefaultResultStandings(revision=0) -> dict:
    """`result_standings` with no import in progress, at `revision` (a fresh
    dict on every call)."""
    return {"status": "idle", "error": "", "revision": revision}


def ReadResultStandings() -> dict:
    """`result_standings` as published, a fresh dict: anything missing or
    malformed read as idle's (no error, revision 0)."""
    value = StateManager.Get(RESULT_STANDINGS_ROOT, None)
    if not isinstance(value, dict):
        value = {}
    read = DefaultResultStandings(_Revision(value.get("revision")))
    for key in ("status", "error"):
        if isinstance(value.get(key), str):
            read[key] = value[key]
    return read


# colors.{feature}.* -- only features the operator explicitly set are present,
# which is what makes the layouts' colors.{feature} -> tournamentInfo fallback
# work. The feature list itself is owned by core/ColorService.FEATURES, which
# also writes these keys (headless core Step 5, instalment 5); it is not
# referenced here because that module reads this one.
COLORS_FEATURE_KEYS = (THEME_COLOR_KEY, ACCENT_COLOR_KEY)


# ── Subtree roots ────────────────────────────────────────────────────────
#
# The top-level names below. Spelled once here because each of them is now
# written by something outside this module -- a core service or AutonaActions
# -- and a root spelled at its write site is the same drift as a leaf spelled
# at its write site (instalment 4's thirty crew literals). KEY_CATALOG is
# keyed by them so the catalog and the writers cannot disagree.
SCORE_ROOT = "score"
GAME_ROOT = "game"
TOP8_ROOT = "top8"
COMMENTARY_ROOT = "commentary"
TOURNAMENT_INFO_ROOT = "tournamentInfo"
STREAM_ROOT = "stream"
COLORS_ROOT = "colors"
DISPLAY_OPTIONS_ROOT = "display_options"
LAYOUT_OPTIONS_ROOT = "layout_options"
RESULT_PLAYERS_ROOT = "result_players"
RESULT_TEAMS_ROOT = "result_teams"
RESULT_RENDER_ROOT = "result_render"
RESULT_STANDINGS_ROOT = "result_standings"
RESULT_LAYOUT_ROOT = "result_layout"
PROVIDER_VIDEOGAME_ROOT = "provider_videogame"
ANALYSIS_ROOT = "analysis"
SET_SELECTION_ROOT = "set_selection"
THUMBNAIL_ROOT = "thumbnail"

KEY_CATALOG = {
    SCORE_ROOT: SCORE_SHARED_KEYS + tuple(
        f"{{N}}.{key}" for key in SCORE_BOARD_KEYS
    ) + tuple(
        f"{{N}}.team.{{T}}.{key}" for key in SCORE_TEAM_KEYS
    ) + tuple(
        f"{{N}}.team.{{T}}.player.{{M}}.{key}" for key in SCORE_PLAYER_KEYS
    ) + tuple(
        # Board 3 is spelled out rather than left as {N}: these exist only on
        # the crew board.
        f"3.team.{{T}}.{key}" for key in SCORE_CREW_TEAM_KEYS
    ) + tuple(
        f"3.team.{{T}}.player.{{M}}.{key}" for key in SCORE_CREW_PLAYER_KEYS
    ),
    GAME_ROOT: GAME_KEYS,
    TOP8_ROOT: TOP8_KEYS,
    COMMENTARY_ROOT: tuple(
        f"{{I}}.{key}" for key in COMMENTARY_SLOT_KEYS),
    TOURNAMENT_INFO_ROOT: TOURNAMENT_INFO_KEYS,
    STREAM_ROOT: STREAM_KEYS,
    ANALYSIS_ROOT: ANALYSIS_KEYS,
    SET_SELECTION_ROOT: tuple(
        f"{kind}.{key}" for kind in SET_SELECTION_KINDS
        for key in SET_SELECTION_FETCH_KEYS),
    THUMBNAIL_ROOT: THUMBNAIL_KEYS,
    COLORS_ROOT: tuple(f"{{feature}}.{key}" for key in COLORS_FEATURE_KEYS),
    DISPLAY_OPTIONS_ROOT: DISPLAY_OPTION_KEYS,
    # Roots whose shape is owned elsewhere and not enumerated here:
    # layout_options has had no writer since ADR-005 S4s and no overlay reads
    # it: only a state.json of an old version holds it. The rest are provider
    # payloads written wholesale.
    LAYOUT_OPTIONS_ROOT: (),
    PROVIDER_VIDEOGAME_ROOT: (),
    "streamQueue": (),
    "currentStream": (),
    # The rows of the tournament result (core/ResultService.py), written by
    # slot.
    RESULT_PLAYERS_ROOT: (),
    RESULT_TEAMS_ROOT: (),
    RESULT_RENDER_ROOT: RESULT_RENDER_KEYS,
    RESULT_STANDINGS_ROOT: RESULT_STANDINGS_KEYS,
    # A string: the layout in effect, the settings' (core/ResultService.py).
    RESULT_LAYOUT_ROOT: (),
}

# Key families whose final segment comes from a Qt objectName at runtime
# instead of from this catalog (known-smells S-14). This was the Step 5
# inventory and it is **empty**: the Qt board's two families (the
# team card and the match bar) came off when they became
# SCORE_TEAM_INPUT_KEYS / SCORE_MATCH_FIELDS, the player card's followed as
# PLAYER_INPUT_KEYS, and the EVENT tab's -- the last one -- as the
# TOURNAMENT_*_FIELDS. Nothing consumes the tuple; it is the checklist,
# and tests/test_state_schema.py keeps it from growing back.
#
# Kept rather than deleted so a new widget that reaches for the old pattern
# has somewhere to be listed while it is being converted -- not so one can
# live here permanently.
DYNAMIC_KEY_FAMILIES = ()


# ── Seeds ────────────────────────────────────────────────────────────────


def SeedBoard(number):
    """Write the empty skeleton of one board's ``score.{number}`` subtree.

    Called from one place: AutonaActions.SeedFreshBoard, so a new board
    starts from a known shape. The Qt boards' dock (removed in ADR-005
    S4ac-3), AutonaScoreboardManager.CreateScoreboard, also
    called it until ADR-005 S4ac-1, as a placeholder for the boards not
    built at launch (so the Web UI and the OBS overlays read an empty board
    rather than "no such key"); every board is made, whole, at launch now
    (AutonaActions.MakeBoards). The two used to carry their own copy of the
    list and they had already drifted -- the manager knew board 3 seeds only
    its root, the boards did not know about each other at all.

    Not "fill in what is missing": ``Set(prefix, {})`` replaces the subtree,
    so calling this again resets the board to the skeleton. That is why
    nothing may call it on a live board -- the manager's placeholder, once
    the boards were made at launch, would have replaced two of them.
    """
    prefix = f"{SCORE_ROOT}.{number}"
    StateManager.Set(prefix, {})
    if str(number) == "3":
        # Crew battle has no set history and no station queue; the rest of
        # its subtree is CrewBattleService.SeedDefaults()'s.
        return
    StateManager.Set(f"{prefix}.last_sets.1", {})
    StateManager.Set(f"{prefix}.last_sets.2", {})
    StateManager.Set(f"{prefix}.history_sets.1", {})
    StateManager.Set(f"{prefix}.history_sets.2", {})
    StateManager.Set(f"{prefix}.station_queue", {})


def HoldBoard(number):
    """Put a 1v1 / team board's ``score.{number}`` behind the 反映 gate.

    Edits under a held prefix stay in the live state but are not published
    until ``StateManager.CommitHeld`` (the 反映 button, ``ApplyChanges``);
    ``STATS_KEYS`` are fetched data, not edits, and publish at once.

    Called by AutonaActions.EnsureBoard (with `panel`: the desktop, which
    makes every board at launch since ADR-005 S4ac-1 -- MakeBoards), after
    AutonaActions.SeedFreshBoard -- a registration made first would hold
    back the seed itself. The board's constructor registered it until
    ADR-005 S4y, in the same save cycle but after the widgets were built
    (they write nothing as they are, ADR-005 S4t, so the earlier point holds
    back nothing more), and until S4ac-1 the desktop made a board only as it
    built its widgets. A board that is not ensured is not held: the seed
    written when the board is ensured would wipe its edits. Holding every
    board from startup was ADR-005 S2 decision 7, withdrawn in S2c because
    nothing applied a board the window did not show (the 反映 button was on
    the board, and the Web UI had none); it came back in S4ac-1, once the
    React SCORE page applies any board (順序 6 decision 2).

    Idempotent (``HoldSubtree`` keeps a set). Board 3 (crew) is not held:
    the crew board publishes as it goes.
    """
    StateManager.HoldSubtree(f"{SCORE_ROOT}.{number}", exempt=STATS_KEYS)


def ReleaseBoard(number):
    """Take a board's ``score.{number}`` out from behind the 反映 gate
    (``StateManager.ReleaseSubtree``): what was pending is published, and
    edits publish as they go again. For a board whose making or drawing
    failed (AutonaActions.ForgetBoard), which no 反映 button would ever
    apply. A board that is not held is left alone.
    """
    StateManager.ReleaseSubtree(f"{SCORE_ROOT}.{number}")


def PublishDisplayOptions():
    """Re-publish all eight display options from their persisted defaults.

    Run when the desktop makes a 1v1 / team board -- at launch since
    ADR-005 S4ac-1 (AutonaActions.EnsureBoard with `panel`, from
    AutonaActions.MakeBoards; the Qt board's first widget build,
    AutonaScoreboardManager.EnsureScoreboard, until then -- the Qt boards
    went in S4ac-3), which is what the
    "SettingsManager is the source of truth, StateManager is what the
    overlays render" mirror rests on: whatever a previous session left in
    program_state.json is brought back in line with the settings file the
    toggles actually persist to. Unlike the missing-keys-only pass in
    ``SeedPanelState()`` this rewrites every key, because that is what a new
    board has always done -- its constructor used to spell the loop out
    inline, over its own copy of the option list. Not on the headless shell,
    which builds no board: it has only ever had the missing-keys pass, and
    what it restored stays (AutonaActions.SeedFreshBoard).

    Not a ``SaveBlock``: the caller owns the cycle, and ``display_options.*``
    is not held, so an unblocked call here would fire eight of them.
    """
    for key, default in DISPLAY_OPTION_DEFAULTS.items():
        path = f"{DISPLAY_OPTIONS_ROOT}.{key}"
        StateManager.Set(path, SettingsManager.Get(path, default))


def ResetScoreRoot():
    """Drop the whole ``score`` subtree.

    Run once per launch, through ``AutonaActions.ResetBoards`` (the start of
    ``AutonaActions.MakeBoards``, in both shells since ADR-005 S4ac-1; from
    ``AutonaScoreboardManager.__init__`` on the desktop until then; the Qt
    boards went in S4ac-3) --
    i.e. before any board is made -- so a program_state.json written by an older
    multi-scoreboard build cannot leave boards behind that no widget owns.
    It is a subtree lifecycle operation like ``SeedBoard()`` above and lives
    next to it for the same reason: ``score`` is a schema name, and the
    manager is a QDockWidget, which is exactly what Step 5 took the state
    writes out of.

    Nothing may call this after a board exists -- the boards seed themselves
    from ``SeedBoard()`` afterwards, and a live board would be left rendering
    a subtree that is no longer there.
    """
    StateManager.Unset(SCORE_ROOT)


def PublishAssetVersion(key: str, path: str):
    """Publish an asset file's mtime under `key` as a cache-bust version.

    mtime keeps the value stable across restarts when the file has not
    changed, so overlays do not re-fetch every launch. 0 means "no asset".
    Guarded so an unchanged file does not queue a changed key.
    """
    try:
        version = int(os.path.getmtime(path) * 1000)
    except OSError:
        version = 0
    StateManager.SetIfChanged(key, version)


def SeedPanelState():
    """Normalise and seed the state the overlays / Web UI read from launch.

    These seeds used to run inside the Board / Top 8 / Commentary / Stream /
    Player List constructors. Those panels are built on first reveal now
    (startup Step 4b-2), so the seeds have to run whether or not the operator
    ever opens the panel -- an OBS overlay reads the theme colors and the
    commentary slots straight away.

    Runs after StateManager.LoadState(), so what is normalised here is the
    restored state (user_data/state.json, already migrated to the current
    state_version) rather than an empty state. The caller wraps it in a
    StateManager.SaveBlock() so the whole pass is one save cycle;
    Window._SeedPanelState is that caller.

    Three steps below -- the commentary normalisation, RETIRED_ROOTS and
    STALE_KEYS -- are also the v0 -> v1 migration (StateMigrations), and are
    kept here on purpose rather than moved there:

    - ``player_editor`` is not only from before v1. The builds up to ADR-005
      S4n wrote it while the Qt editor dialog was open, so a crashed session
      left it in a v1 state.json, which no v0 -> v1 step ever sees again.
      Only a per-launch pass removes it.
    - The commentary pass is also the seed: it builds the 12 empty slots
      when the subtree is missing (a fresh install), which a migration of
      existing data does not do.
    - Running both is harmless. NormalizeState is idempotent, Unset of an
      absent root changes nothing (inside the caller's SaveBlock it adds no
      save cycle), and STALE_KEYS are only unset when present.
    """
    # Tournament-wide colors (EVENT tab). The per-feature overrides are
    # expanded separately by core/ColorService.SeedColorState(), which the
    # same caller runs inside the same SaveBlock. The two seeds stay apart
    # because this one has to be reachable without importing ColorService --
    # that module reads this one for its defaults and its key spellings.
    StateManager.Set(
        f"{TOURNAMENT_INFO_ROOT}.{THEME_COLOR_KEY}",
        SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR))
    StateManager.Set(
        f"{TOURNAMENT_INFO_ROOT}.{ACCENT_COLOR_KEY}",
        SettingsManager.Get("general.accent_color", DEFAULT_ACCENT_COLOR))

    # display_options.*: the Web UI eye menu and the OBS overlays render
    # field visibility from these keys. The only other writer is the
    # desktop's making of boards 1 / 2 (AutonaActions.EnsureBoard with
    # `panel` -> PublishDisplayOptions), which rewrites all eight from
    # SettingsManager at every launch since ADR-005 S4ac-1 (MakeBoards, in
    # bootstrap_gui(), before the window calls this) -- in crew format too;
    # until then it was a 1v1 / team board's build, so a launch in crew
    # format never wrote them. The headless shell never rewrites them.
    #
    # Missing keys only. SettingsManager is the persisted source of truth
    # here: every toggle writes SettingsManager *and* StateManager. Reading
    # the same source keeps a headless seed identical to what a GUI launch
    # ends up with, and the guard leaves alone what is there: on the desktop
    # what MakeBoards just wrote, headless a restored value (the operator's).
    for key, default in DISPLAY_OPTION_DEFAULTS.items():
        path = f"{DISPLAY_OPTIONS_ROOT}.{key}"
        if StateManager.Get(path, None) is None:
            StateManager.Set(
                path, SettingsManager.Get(path, default))

    # top8: LoadState restores whatever the last session had, including the
    # legacy bracket keys. Slots are always derived, never restored.
    state = StateManager.Get(TOP8_ROOT)
    if not isinstance(state, dict) or not state.get("matches"):
        # Recompute the fresh seed too: DefaultState() hands out TBD slots,
        # and the first ApplyScores would otherwise treat that as "the
        # participants changed" and drop the score (known-smells S-18).
        seed = top8.DefaultState()
        seed[TOP8_MATCHES_KEY] = top8.Recompute(seed[TOP8_MATCHES_KEY])
        StateManager.Set(TOP8_ROOT, seed)
    else:
        StateManager.Set(
            f"{TOP8_ROOT}.{TOP8_MATCHES_KEY}",
            top8.Recompute(state.get(TOP8_MATCHES_KEY) or {}))
        players = state.get(TOP8_PLAYERS_KEY) or {}
        for slot in top8.PLAYER_SLOTS:
            if slot not in players:
                StateManager.Set(
                    f"{TOP8_ROOT}.{TOP8_PLAYERS_KEY}.{slot}",
                    top8.DefaultPlayer())

    # commentary: normalises whatever LoadState restored, including the
    # legacy pre-rework schema.
    StateManager.Set(
        COMMENTARY_ROOT,
        comms.NormalizeState(StateManager.Get(COMMENTARY_ROOT)))

    # The org icon / thumbnail are served out of ./layout/ and a reader
    # cache-busts them with these mtimes, so the versions have to be current
    # from launch. core/StreamService.py republishes them after an import /
    # a reset.
    for key, path in STREAM_ASSET_VERSIONS:
        PublishAssetVersion(key, path)

    # The stream thumbnail generator starts idle: a run does not outlive the
    # process (its job died with it, so a persisted "running" would never
    # end), and the previous launch's result is not shown again -- the
    # desktop panel did not show it either. Guarded like the versions above.
    StateManager.SetIfChanged(
        f"{STREAM_ROOT}.{STREAM_THUMBNAIL_KEY}", DefaultStreamThumbnail())

    # The tournament banner and its fetch, the same way (ADR-005 S4f). The
    # logo's version is TournamentInfoService.Seed()'s, as before.
    PublishAssetVersion(
        f"{TOURNAMENT_INFO_ROOT}.{TOURNAMENT_BANNER_VERSION_KEY}",
        TOURNAMENT_BANNER_PATH)
    StateManager.SetIfChanged(
        f"{TOURNAMENT_INFO_ROOT}.{TOURNAMENT_MEDIA_KEY}",
        DefaultTournamentMedia())

    # The tournament's set / clear (ADR-005 S4g review M1): the URL set last
    # (which the launch's restore loads, publishing its own progress) and
    # idle -- a set does not outlive the process. The revision goes on from
    # the previous session's, so a client still waiting on one of its
    # revisions (a browser kept open across the restart) is not left behind
    # by a count started again. Guarded like the rest.
    url = SettingsManager.Get(TOURNAMENT_URL_SETTING, "")
    StateManager.SetIfChanged(
        f"{TOURNAMENT_INFO_ROOT}.{TOURNAMENT_URL_KEY}",
        url if isinstance(url, str) else "")
    StateManager.SetIfChanged(
        f"{TOURNAMENT_INFO_ROOT}.{TOURNAMENT_LOAD_KEY}",
        {"status": "idle", "error": "",
         "revision": ReadTournamentLoad()["revision"]})

    # The match list's fetch (ADR-005 S4j), the same way: idle -- a fetch
    # and its rows (the service's memory) do not outlive the process -- at
    # the previous session's revision, which the next fetch goes on from.
    StateManager.SetIfChanged(
        ANALYSIS_ROOT, DefaultAnalysis(ReadAnalysis()["revision"]))

    # The set selection's lists (ADR-005 S4v), the same way.
    StateManager.SetIfChanged(SET_SELECTION_ROOT, IdleSetSelection())

    # The match thumbnail's render (ADR-005 S4o), the same way: a run does not
    # outlive the process, nor is the previous launch's result shown again;
    # the revision goes on, so a preview image URL is never reused.
    StateManager.SetIfChanged(
        f"{THUMBNAIL_ROOT}.{THUMBNAIL_RENDER_KEY}",
        DefaultThumbnailRender(ReadThumbnailRender()["revision"]))

    # The tournament result's render and standings import (ADR-005 S4q), the
    # same way. The rows are not seeded: a row nobody wrote reads as an empty
    # one, and they are what the operator typed, which stays (decision 5).
    StateManager.SetIfChanged(
        RESULT_RENDER_ROOT, DefaultResultRender(ReadResultRender()["revision"]))
    StateManager.SetIfChanged(
        RESULT_STANDINGS_ROOT,
        DefaultResultStandings(ReadResultStandings()["revision"]))
    # The layout's echo says nothing until it is chosen or followed (the
    # layout itself is settings.json's: GET /api/v1/result reads it).
    StateManager.SetIfChanged(RESULT_LAYOUT_ROOT, "")
    # A row with `members` in result_players is a team layout's, as the Qt
    # panel wrote it until ADR-005 S4r removed the panel. StateMigrations' v1 ->
    # v2 moved the ones it found, once; a state saved at version 2 may hold
    # more. Not a player's, nor anything the page can show.
    rows = StateManager.Get(RESULT_PLAYERS_ROOT, None)
    if isinstance(rows, dict):
        teams = [slot for slot, row in rows.items()
                 if isinstance(row, dict) and "members" in row]
        if teams and len(teams) == len(rows):
            StateManager.Unset(RESULT_PLAYERS_ROOT)
        else:
            for slot in teams:
                StateManager.Unset(f"{RESULT_PLAYERS_ROOT}.{slot}")

    for root in RETIRED_ROOTS:
        StateManager.Unset(root)
    for stale in STALE_KEYS:
        if StateManager.Get(stale, None) is not None:
            StateManager.Unset(stale)
