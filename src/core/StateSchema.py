"""State schema: what keys exist, what they default to, and how they are seeded.

Until now the answer to "what is in the state?" lived in whichever widget
happened to write a key first: the held-subtree exemption list was a class
attribute of ``AutonaScoreboardWidget``, the display-option key set was
spelled out twice (once as that widget's ``elements``, once as
``WebServerActions._DISPLAY_OPTION_KEYS``), and the launch seeds were split
between two widget constructors and their manager. This module is the single
place those definitions live now (ADR-004 D3, headless core Step 2), and like
everything under ``src/core`` it must stay Qt-free -- so a headless run seeds
exactly the same state a GUI launch does.

Two things it deliberately is *not* yet:

- **Not a replacement for *every* dynamic keypath.** Step 5 is converting
  them widget by widget. The SCORE panel's board-level and team-card inputs
  are done (``SCORE_MATCH_FIELDS`` / ``SCORE_TEAM_INPUT_KEYS`` below are read
  by ``AutonaActions`` and by ``AutonaScoreboardWidget``), and so is the
  player card (``PLAYER_INPUT_KEYS``, read by ``PlayerService`` and by
  ``AutonaScoreboardPlayerWidget``); all of ``tournamentInfo.*`` is still
  generated from Qt ``objectName`` values at runtime (known-smells S-14, and
  ``DYNAMIC_KEY_FAMILIES`` below is the remaining inventory). For that one,
  ``KEY_CATALOG`` only *enumerates* what the widget produces -- documentation
  with a type.
- **Not a migration layer.** There is none yet: a renamed ``objectName``
  still breaks an existing ``out/program_state.json`` silently. When Step 5
  statically names those keys it needs one anchor listing the *old* names to
  read from, and this catalog is meant to be it -- which is why it is written
  against the keys as they exist today rather than as they ought to be.

Import side effects: none (agent-pitfalls #1). Every read and write happens
inside a call.
"""
import os
from typing import NamedTuple

# Upward imports into src/ are allowed only for modules that are themselves
# Qt-free (these two are pure state models, and are the candidates to move
# into core next). Importing a Qt-dependent module from here would make core
# unusable headless -- tests/test_core_qt_free.py detects it.
from .. import AutonaCommentaryModel as comms
from .. import AutonaTop8Model as top8
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

    ``fields`` are player-widget ``objectName`` values whose Qt visibility
    follows the toggle. Only ``show_sponsor`` still uses them on the desktop
    (the details dialog shows every field); the rest are carried so the
    lookup stays available to the team columns.
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

# OBS overlays append these versions as a query string
# (``/layout/stream_org_icon.png?v={version}``) to get past the
# Cache-Control: max-age AutonaWebServer serves. Paths are relative to the
# process CWD, like every other mutable path in the app (./out, ./user_data).
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

# Root of the retired player-editor subtree. AutonaPlayerBrowserWidget writes
# player_editor.1 while its dialog is open and clears it on close; a crashed
# session leaves it behind and LoadState would restore it forever.
PLAYER_EDITOR_ROOT = "player_editor"

# Whole subtrees dropped at every launch. All three belong to features that
# were removed, and all three are restored from out/program_state.json until
# something unsets them.
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
# as AutonaScoreboardManager.BOARD_NUMBERS (ints, used to iterate boards) and
# a second, string-typed spelling of the same thing is the drift this module
# is supposed to end. The ranges are in the placeholder note above.
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
# AutonaScoreboardWidget looks its widgets up *by* them instead of reading
# them back off the widgets.
#
# The strings are unchanged on purpose. An existing out/program_state.json
# holds keys named after the old objectNames and there is still no migration
# layer, so renaming one here (or in the .ui) is the same compatibility break
# it always was -- tests/test_objectname_keypath_contract.py now pins that the
# two spellings agree.

# The match bar's three editable combos (src/layout/AutonaScoreboardScore.ui).
# Boards 1 and 2 only: the crew board has no match bar. Named individually
# (not just indexed into the tuple) for the same reason as TEAM_NAME_KEY /
# TEAM_LOSERS_KEY below: AutonaScoreboardWidget looks its widgets up *by*
# these constants (findChild), not by SCORE_MATCH_FIELDS[i].
PHASE_KEY = "phase"
POOL_KEY = "pool"
MATCH_KEY = "match"
SCORE_MATCH_FIELDS = (PHASE_KEY, POOL_KEY, MATCH_KEY)

# The team card's two operator-editable inputs
# (src/layout/AutonaScoreboardTeam.ui). Both are also findChild lookup keys.
TEAM_NAME_KEY = "teamName"
TEAM_LOSERS_KEY = "losers"
SCORE_TEAM_INPUT_KEYS = (TEAM_NAME_KEY, TEAM_LOSERS_KEY)

# The player card's operator-editable inputs
# (src/layout/AutonaScoreboardPlayer.ui, plus the note box built in code).
# These were the last family generated from Qt objectNames at runtime: one
# `findChildren(QLineEdit)` loop wrote `{path}.{element.objectName()}` and a
# matching `findChildren(QComboBox)` loop did the same for the two location
# combos. `AutonaScoreboardPlayerWidget` looks the widgets up *by* these
# constants now and `PlayerService` writes them; the strings are unchanged,
# so an existing out/program_state.json still matches
# (tests/test_objectname_keypath_contract.py pins both spellings).
#
# NOTE: the same names double as the eye-menu's field ids
# (DisplayOption.fields above) -- `team`, `name`, `twitter`, `real_name`,
# `country`, `state` and `custom_textbox` all appear there as findChild keys
# for visibility toggling. One more reason a rename is never local.
PLAYER_SPONSOR_KEY = "team"
PLAYER_TAG_KEY = "name"
PLAYER_REAL_NAME_KEY = "real_name"
PLAYER_TWITTER_KEY = "twitter"
PLAYER_NOTE_KEY = "custom_textbox"     # QPlainTextEdit, created in code
PLAYER_SEED_KEY = "seed"               # QSpinBox (hidden; badge renders it)
PLAYER_BIRTHDAY_KEY = "birthday"       # QCheckBox (hidden; nothing reads it)
PLAYER_COUNTRY_KEY = "country"
PLAYER_STATE_KEY = "state"

# The four QLineEdits carrying a key, in .ui order.
PLAYER_LINE_EDIT_KEYS = (
    PLAYER_SPONSOR_KEY,
    PLAYER_TAG_KEY,
    PLAYER_REAL_NAME_KEY,
    PLAYER_TWITTER_KEY,
)
# Every free-text input, i.e. what PlayerService.SetText accepts.
PLAYER_TEXT_KEYS = PLAYER_LINE_EDIT_KEYS + (PLAYER_NOTE_KEY,)
# The two combos whose selected row is published whole (a dict carrying the
# code, the display name and the flag asset) -- PlayerService.SetLocation.
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
    "stages.{I}",            # dict: t1_win, t2_win, tie
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

# score.3 only. The crew board (AutonaCrewBattleWidget) writes the team and
# player keys above, plus a roster on top of them: `memberNum` is how many
# member rows are visible, `activeMember` the 1-based index of the member
# currently playing ("0" = nobody), and each member carries its own remaining
# `stock` (the team's `score` is the sum of the named members' stocks).
# Boards 1 and 2 never write any of these.
SCORE_CREW_TEAM_KEYS = (
    "memberNum",
    "activeMember",
)

SCORE_CREW_PLAYER_KEYS = (
    "stock",
)

# Board-independent, written from the loaded set and read by AutonaStatsUtil
# for the upset-factor math. Not under score.{N} -- and wiped together with
# the rest of `score` by AutonaScoreboardManager.__init__.
SCORE_SHARED_KEYS = ("bracket_type",)

GAME_KEYS = (
    "name",
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

TOP8_KEYS = (
    "phaseLabel",
    "players.{I}.name",
    "players.{I}.sponsor",
    "players.{I}.country",          # {code, asset} -- AutonaCountryHelper form
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

# tournamentInfo collects three further groups, none of them enumerated here
# yet (Step 5 is what turns them into catalog entries):
#
# - the EVENT tab's input fields, still keyed by their Qt objectName on the
#   *write* side (a human typing) -- see DYNAMIC_KEY_FAMILIES. Since
#   headless core Step 4b the provider-side writer names them statically
#   instead: TournamentInfoService.{TEXT,NUMBER,DATE}_FIELDS.
# - `endAt` / `eventStartAt` / `eventEndAt`, which have no field on the tab
#   at all. TournamentInfoService.SHORT_DATE_KEYS is their definition: they
#   are published as short date strings rather than timestamps.
# - whatever else the provider payload carries (`shortLink`, and the
#   `initial_load` flag that rides along with it), written through as-is.
TOURNAMENT_INFO_KEYS = (
    "tournamentName",
    "themeColor",
    "accentColor",
    "displayTournamentName",
    "logoVersion",
)

STREAM_KEYS = (
    "url",
    "title",
    "orgIconVersion",
    "orgThumbVersion",
)

# colors.{feature}.* -- only features the operator explicitly set are present,
# which is what makes the layouts' colors.{feature} -> tournamentInfo fallback
# work. The feature list itself is owned by AutonaFeatureColors.FEATURES
# (still Qt-side; it moves to core with the color write path).
COLORS_FEATURE_KEYS = ("themeColor", "accentColor")

KEY_CATALOG = {
    "score": SCORE_SHARED_KEYS + tuple(
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
    "game": GAME_KEYS,
    "top8": TOP8_KEYS,
    "commentary": tuple(
        f"{{I}}.{key}" for key in COMMENTARY_SLOT_KEYS),
    "tournamentInfo": TOURNAMENT_INFO_KEYS,
    "stream": STREAM_KEYS,
    "colors": tuple(f"{{feature}}.{key}" for key in COLORS_FEATURE_KEYS),
    "display_options": DISPLAY_OPTION_KEYS,
    # Roots whose shape is owned elsewhere and not enumerated here:
    # layout_options mirrors LayoutOptionsManager (per-layout, open-ended),
    # the rest are provider payloads written wholesale.
    "layout_options": (),
    "provider_videogame": (),
    "streamQueue": (),
    "currentStream": (),
    "result_players": (),
}

# Key families whose final segment still comes from a Qt objectName at
# runtime instead of from this catalog (known-smells S-14). This is the
# remaining Step 5 inventory: AutonaScoreboardWidget's two families (the team
# card and the match bar) came off this list when they became
# SCORE_TEAM_INPUT_KEYS / SCORE_MATCH_FIELDS, and the player card's family
# followed when it became PLAYER_INPUT_KEYS. Nothing consumes the tuple; it
# is the checklist.
DYNAMIC_KEY_FAMILIES = (
    ("tournamentInfo.{objectName}",
     "AutonaTournamentInfoWidget -- every input field"),
)


# ── Seeds ────────────────────────────────────────────────────────────────


def SeedBoard(number):
    """Write the empty skeleton of one board's ``score.{number}`` subtree.

    Called from two places, which is the point: the board's own constructor
    (so a built board starts from a known shape) and
    AutonaScoreboardManager.CreateScoreboard for the boards that are *not*
    built at launch (so the Web UI and the OBS overlays read an empty board
    rather than "no such key"). Both used to carry their own copy of the
    list and they had already drifted -- the manager knew board 3 seeds only
    its root, the boards did not know about each other at all.

    Not "fill in what is missing": ``Set(prefix, {})`` replaces the subtree,
    so calling this again resets the board to the skeleton. That is what the
    lazy build relies on -- a board built later reseeds over the manager's
    placeholder -- and it is why nothing may call it on a live board.
    """
    prefix = f"score.{number}"
    StateManager.Set(prefix, {})
    if str(number) == "3":
        # Crew battle (AutonaCrewBattleWidget) has no set history and no
        # station queue; the rest of its subtree is written by its own
        # _writeInitialState().
        return
    StateManager.Set(f"{prefix}.last_sets.1", {})
    StateManager.Set(f"{prefix}.last_sets.2", {})
    StateManager.Set(f"{prefix}.history_sets.1", {})
    StateManager.Set(f"{prefix}.history_sets.2", {})
    StateManager.Set(f"{prefix}.station_queue", {})


def PublishDisplayOptions():
    """Re-publish all eight display options from their persisted defaults.

    Run when a 1v1 / team board is built, which is what the "SettingsManager
    is the source of truth, StateManager is what the overlays render" mirror
    rests on: whatever a previous session left in program_state.json is
    brought back in line with the settings file the toggles actually persist
    to. Unlike the missing-keys-only pass in ``SeedPanelState()`` this
    rewrites every key, because that is what the board's constructor has
    always done -- it used to spell the loop out inline, over its own copy of
    the option list.

    Not a ``SaveBlock``: the caller owns the cycle
    (``AutonaScoreboardManager.EnsureScoreboard`` already wraps the whole
    construction in one), and ``display_options.*`` is not held, so an
    unblocked call here would fire eight of them.
    """
    for key, default in DISPLAY_OPTION_DEFAULTS.items():
        path = f"display_options.{key}"
        StateManager.Set(path, SettingsManager.Get(path, default))


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
    if StateManager.Get(key, None) != version:
        StateManager.Set(key, version)


def SeedPanelState():
    """Normalise and seed the state the overlays / Web UI read from launch.

    These seeds used to run inside the Board / Top 8 / Commentary / Stream /
    Player List constructors. Those panels are built on first reveal now
    (startup Step 4b-2), so the seeds have to run whether or not the operator
    ever opens the panel -- an OBS overlay reads the theme colors and the
    commentary slots straight away.

    Runs after StateManager.LoadState(), so what is normalised here is the
    restored program_state.json rather than an empty state. The caller wraps
    it in a StateManager.SaveBlock() so the whole pass is one save cycle;
    Window._SeedPanelState is that caller.
    """
    # Tournament-wide colors (EVENT tab). The per-feature overrides are
    # expanded separately by AutonaFeatureColors.SeedColorState().
    StateManager.Set(
        "tournamentInfo.themeColor",
        SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR))
    StateManager.Set(
        "tournamentInfo.accentColor",
        SettingsManager.Get("general.accent_color", DEFAULT_ACCENT_COLOR))

    # display_options.*: the Web UI eye menu and the OBS overlays render
    # field visibility from these keys, but the only writer is a 1v1 / team
    # board's constructor -- so an operator whose saved format is the crew
    # board (score.3, which has no eye menu) never gets them written at all.
    #
    # Missing keys only. SettingsManager is the persisted source of truth
    # here: every toggle writes SettingsManager *and* StateManager, and a
    # board rewrites all eight from SettingsManager when it is built. Reading
    # the same source keeps a headless seed identical to what a GUI launch
    # ends up with, and the guard keeps a restored program_state.json value
    # from being republished for the one save cycle before a board is built.
    for key, default in DISPLAY_OPTION_DEFAULTS.items():
        path = f"display_options.{key}"
        if StateManager.Get(path, None) is None:
            StateManager.Set(
                path, SettingsManager.Get(path, default))

    # top8: LoadState restores whatever the last session had, including the
    # legacy bracket keys. Slots are always derived, never restored.
    state = StateManager.Get("top8")
    if not isinstance(state, dict) or not state.get("matches"):
        StateManager.Set("top8", top8.DefaultState())
    else:
        StateManager.Set(
            "top8.matches", top8.Recompute(state.get("matches") or {}))
        players = state.get("players") or {}
        for slot in top8.PLAYER_SLOTS:
            if slot not in players:
                StateManager.Set(
                    f"top8.players.{slot}", top8.DefaultPlayer())

    # commentary: normalises whatever LoadState restored, including the
    # legacy pre-rework schema.
    StateManager.Set(
        "commentary", comms.NormalizeState(StateManager.Get("commentary")))

    # The org icon / thumbnail are served out of ./layout/ and OBS overlays
    # cache-bust them with these mtimes, so the versions have to be current
    # from launch whether or not the 配信設定 panel is ever opened. The panel
    # republishes them itself after a load / reset.
    for key, path in STREAM_ASSET_VERSIONS:
        PublishAssetVersion(key, path)

    for root in RETIRED_ROOTS:
        StateManager.Unset(root)
    for stale in STALE_KEYS:
        if StateManager.Get(stale, None) is not None:
            StateManager.Unset(stale)
