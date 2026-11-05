"""The statistics overlays the Stats Display Settings page offers (ADR-005
S4l), and which one is selected.

A fixed table (the layouts under ``layout/`` that render the statistics the
stats pipeline wrote under ``score.1.*``). The key is what gets persisted, so
it stays stable even if a layout folder is renamed. ``last_sets`` and
``tournament_history`` ship one entry file per player, so one folder gives
two items (same ``id``, different ``entry``).

The selection is a UI preference kept in settings.json
(``stats_preview.selected_type``), not part of the state. Qt-free.
"""

from .SettingsManager import SettingsManager

SETTINGS_KEY = "stats_preview.selected_type"

# (key, layout dir, entry html, display name). The display name is the
# English source; the page translates it.
_ROWS = (
    ("upset_factor",          "upset_factor",       "index.html",   "Upset Factor"),
    ("h2h_record",            "h2h_record",         "index.html",   "Head-to-Head Record"),
    ("recent_sets",           "recent_sets",        "index.html",   "Recent Sets"),
    ("last_sets_p1",          "last_sets",          "player1.html", "Last Sets (Player 1)"),
    ("last_sets_p2",          "last_sets",          "player2.html", "Last Sets (Player 2)"),
    ("tournament_history_p1", "tournament_history", "player1.html", "Tournament History (Player 1)"),
    ("tournament_history_p2", "tournament_history", "player2.html", "Tournament History (Player 2)"),
)

DEFAULT_KEY = _ROWS[0][0]


def _Item(row):
    key, folder, entry, displayName = row
    return {"key": key, "id": folder, "entry": entry,
            "displayName": displayName}


def Overlays():
    """Every overlay, in display order: ``{key, id, entry, displayName}``."""
    return [_Item(row) for row in _ROWS]


def Selected():
    """The selected overlay; the first when the kept key is not in the table."""
    kept = SettingsManager.Get(SETTINGS_KEY, DEFAULT_KEY)
    for row in _ROWS:
        if row[0] == kept:
            return _Item(row)
    return _Item(_ROWS[0])


def Select(key):
    """Keep ``key`` as the selection and return its item; None (nothing
    kept) when the table has no such key."""
    for row in _ROWS:
        if row[0] == key:
            SettingsManager.Set(SETTINGS_KEY, key)
            return _Item(row)
    return None
