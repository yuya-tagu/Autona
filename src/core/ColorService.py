"""Tournament-wide and per-feature theme / accent colors.

Autona used to have a single pair of colors -- SettingsManager
``general.theme_color`` / ``general.accent_color``, mirrored into StateManager
as ``tournamentInfo.themeColor`` / ``accentColor`` -- shared by every OBS
layout and image renderer. This module keeps that pair as the *tournament*
colors and adds an optional per-feature override on top of it:

    SettingsManager  colors.{feature}.theme_color / accent_color   (正本)
    StateManager     colors.{feature}.themeColor  / accentColor    (表示用展開)

Resolution order is ``colors.{feature}`` -> tournament colors -> the layout's
own CSS default. Only the features the operator has explicitly set are written
into StateManager (see SeedColorState), so an unset feature has no
``colors.{feature}`` node at all and the layout JS falls back to
``tournamentInfo`` by itself.

The ``tournamentInfo.themeColor`` / ``accentColor`` keys keep their names and
their meaning (= the tournament colors) for backwards compatibility: existing
templates and ``out/`` consumers are unaffected. They are *this* module's
keys despite the prefix -- ``TournamentInfoService`` owns the EVENT tab's
fields and the display-name rules, and never touches these two. The ledger in
docs/changes/headless-core-redesign.md records that split.

Where this came from (headless core Step 5, instalment 5): all of the above
lived in ``src/AutonaFeatureColors.py``, next to the Qt cards that drive it.
Step 2 moved the launch seed of every *other* key family into
``StateSchema`` but deliberately left ``SeedColorState`` behind, because
splitting a key family's seed from its reader and writer puts two owners on
one subtree. Moving the family whole is what that note was waiting for; the
cards stay in ``AutonaFeatureColors``, which now re-exports these names so its
importers are unaffected.

Import side effects: none (agent-pitfalls #1). Every read and write happens
inside a call. Qt-free, like everything under ``src/core``.
"""
from . import StateSchema as schema
from .SettingsManager import SettingsManager
from .StateManager import StateManager

# Feature keys. One per settings panel that owns an overlay / rendered image.
FEATURE_BOARD = "board"
FEATURE_THUMBNAIL = "thumbnail"
FEATURE_RESULT = "result"
FEATURE_TOP8 = "top8"
FEATURE_COMMENTARY = "commentary"
FEATURE_STATS = "stats"

FEATURES = (
    FEATURE_BOARD,
    FEATURE_THUMBNAIL,
    FEATURE_RESULT,
    FEATURE_TOP8,
    FEATURE_COMMENTARY,
    FEATURE_STATS,
)

# Color kinds, and their key spelling on each side: SettingsManager uses
# snake_case, StateManager camelCase (the spelling the layouts already read).
KIND_THEME = "theme"
KIND_ACCENT = "accent"

_SETTINGS_SUFFIX = {
    KIND_THEME: "theme_color",
    KIND_ACCENT: "accent_color",
}
# The state-side spellings come from the schema rather than being written out
# again here: the same two names appear in COLORS_FEATURE_KEYS and, for the
# tournament pair, in TOURNAMENT_INFO_KEYS. Three literal copies of
# "themeColor" is how the catalog and the writer drift apart (the lesson from
# instalment 3).
_STATE_SUFFIX = {
    KIND_THEME: schema.THEME_COLOR_KEY,
    KIND_ACCENT: schema.ACCENT_COLOR_KEY,
}


# ── Tournament colors (shared default for every feature) ─────────────────

def GetTournamentColors():
    """Return the (theme, accent) tournament colors, falling back to the
    Autona defaults when nothing has been stored yet."""
    return (
        SettingsManager.Get("general.theme_color", schema.DEFAULT_THEME_COLOR),
        SettingsManager.Get(
            "general.accent_color", schema.DEFAULT_ACCENT_COLOR),
    )


def SetTournamentColor(kind: str, color: str):
    """Persist one tournament color, republish it, and drop every per-feature
    override of the same kind.

    The StateManager key is the historical ``tournamentInfo.*`` one -- layouts
    and ``out/`` consumers read it as "the tournament color".

    Changing a tournament color is an "apply this everywhere" action: clearing
    the overrides makes the layouts' ``colors.{feature}`` -> ``tournamentInfo``
    fallback deliver the new color to every overlay at once, instead of
    leaving the features the operator once customised stuck on their old
    color. A feature becomes independent again as soon as it is edited.
    Clearing is per kind, so re-picking the tournament theme leaves the
    per-feature accent overrides alone.
    """
    if not color:
        return
    # One save cycle for the whole cascade (1 write + up to 6 clears).
    with StateManager.SaveBlock():
        SettingsManager.Set(f"general.{_SETTINGS_SUFFIX[kind]}", color)
        StateManager.Set(
            f"{schema.TOURNAMENT_INFO_ROOT}.{_STATE_SUFFIX[kind]}", color)
        for feature in FEATURES:
            _ClearFeatureColor(feature, kind)


def _ClearFeatureColor(feature: str, kind: str):
    """Drop one stored feature override if it exists.

    Both Unset helpers create the intermediate dicts on their way down
    (deep_unset in core/DictHelper.py), and SettingsManager.Unset rewrites
    settings.json on every call, so the existence guards are what keep an
    untouched feature from collecting empty ``colors.{feature}`` nodes and
    what keeps a single color pick from triggering six pointless file
    writes. The StateManager guard additionally avoids queueing a changed key
    for a path that was never published (same reason as in _SeedPanelState).
    """
    settingsPath = f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}"
    if SettingsManager.Get(settingsPath, None) is not None:
        SettingsManager.Unset(settingsPath)
    statePath = FeatureColorKey(feature, kind)
    if StateManager.Get(statePath, None) is not None:
        StateManager.Unset(statePath)


# ── Per-feature colors ───────────────────────────────────────────────────

def FeatureColorKey(feature: str, kind: str) -> str:
    """The StateManager key one feature override is published under."""
    return f"{schema.COLORS_ROOT}.{feature}.{_STATE_SUFFIX[kind]}"


def GetFeatureColors(feature: str):
    """Return the (theme, accent) colors in effect for one feature.

    An unset feature inherits the tournament colors, which in turn fall back
    to the Autona defaults."""
    theme, accent = GetTournamentColors()
    return (
        SettingsManager.Get(f"colors.{feature}.theme_color", theme),
        SettingsManager.Get(f"colors.{feature}.accent_color", accent),
    )


def SetFeatureColor(feature: str, kind: str, color: str):
    """Persist one feature color and republish it to the overlays."""
    if not color:
        return
    # Single StateManager key -- no SaveBlock needed (unlike SetTournamentColor
    # above, which is 1 write plus up to 6 clears and does need one).
    SettingsManager.Set(f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}", color)
    StateManager.Set(FeatureColorKey(feature, kind), color)


def SeedColorState():
    """Expand the stored per-feature colors into StateManager at launch.

    Called from Window._SeedPanelState() inside its SaveBlock, because the
    panels that own these cards are built on first reveal and the OBS overlays
    read the colors from launch.

    Only features with an explicit setting are written: leaving the
    ``colors.{feature}`` node absent is what makes the layout JS fall back to
    the tournament colors. A previously written key whose setting has since
    gone away is dropped for the same reason -- guarded, because Unset queues a
    changed key unconditionally.
    """
    for feature in FEATURES:
        for kind in (KIND_THEME, KIND_ACCENT):
            path = FeatureColorKey(feature, kind)
            stored = SettingsManager.Get(
                f"colors.{feature}.{_SETTINGS_SUFFIX[kind]}", None)
            if stored:
                StateManager.Set(path, stored)
            elif StateManager.Get(path, None) is not None:
                StateManager.Unset(path)
