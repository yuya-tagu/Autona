"""What the main window keeps between launches, and the page names it keeps
apart (ADR-005 S-final SF-5b-3).

The window is the shared view alone (AutonaWebPanelHost, the AppShell's
/web/app/<panel>): it reopens on the page shown last (``lastPanel``, a
panel's name) with the AppShell's sidebar hidden or not as it was
(``navCollapsed``). While a page of the Help & Settings menu shows, the page
to reopen on is the one before it (RememberedPanel), as the Qt sidebar's
highlight stayed on its item. The window saves both as soon as either
changes, and again on close (SaveWindowState writes only what differs).

Until SF-5b-3 the Qt sidebar kept its item's index (``sidebarIndex_v22``) and
whether it was hidden (``sidebarCollapsed_v22``). Each is read once, into
the new key, and removed: the index through LEGACY_SIDEBAR_PANELS, the
sidebar's items at that version, a table that never changes with the
navigation.

Plain functions over the settings object handed in (the window's QSettings,
or anything with value / setValue / remove / contains): importing this
creates nothing and needs no Qt (agent-pitfalls #1).
"""

from .core import WebAccess

# The pages the AppShell's Help & Settings menu opens, which are not items of
# its navigation (web/src/AppShell/navTable.ts's MENU_PAGES, held to this by
# tests/test_app_shell.py): Settings (ADR-005 S4af), the API reference, the
# Companion and OBS guides, About (S-final SF-4a-2) and Add game assets
# (SF-4b-2). Never the page the window reopens on.
MENU_PAGES = ("settings", "api", "companion", "obs", "about", "assets")

# The panel shell's empty page (WebAccess.WEB_PANELS' "shell-check", which
# checks the shell itself: the tests' and the e2e's page). No item or menu
# opens it, so the operator never chose it: never the page the window
# reopens on either.
SHELL_CHECK_PAGE = "shell-check"

# The page a first launch opens on: the navigation's first item (the Qt
# sidebar's index 0).
DEFAULT_PANEL = "event"

LAST_PANEL_KEY = "lastPanel"
NAV_COLLAPSED_KEY = "navCollapsed"

# The Qt sidebar's keys at its last layout version (Window.STATE_VERSION 22),
# read once by RestorePanel / RestoreNavCollapsed.
LEGACY_SIDEBAR_INDEX_KEY = "sidebarIndex_v22"
LEGACY_SIDEBAR_COLLAPSED_KEY = "sidebarCollapsed_v22"

# The Qt sidebar's items at version 22, by index: what sidebarIndex_v22
# names. Fixed: it reads a value that sidebar saved, whatever the AppShell's
# navigation becomes.
LEGACY_SIDEBAR_PANELS = (
    "event", "stream", "scoreboard", "board", "thumbnail", "result", "top8",
    "commentary", "statsPreview", "stats", "playerBrowser")


def IsReopenable(panel):
    """Whether the window may reopen on ``panel``: a React panel's page that
    is not one of the menu's, nor the panel shell's empty page."""
    return (WebAccess.IsWebPanel(panel) and panel not in MENU_PAGES
            and panel != SHELL_CHECK_PAGE)


def RememberedPanel(panel, previous):
    """The page to reopen on once ``panel`` shows, ``previous`` being the
    one remembered until then: ``panel`` itself, but ``previous`` while a
    menu page shows (or a name the window would not reopen on)."""
    return panel if IsReopenable(panel) else previous


def _Int(value):
    # QSettings hands an int back as an int or as its text, by backend.
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _Bool(value):
    # A bool, or the text QSettings keeps one as ("true" / "false").
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


def LegacyPanel(index):
    """The page sidebarIndex_v22's ``index`` named: LEGACY_SIDEBAR_PANELS'
    item, or DEFAULT_PANEL for one out of the table (the Qt window then
    stayed on its first item too) or not a number."""
    number = _Int(index)
    if number is None or not 0 <= number < len(LEGACY_SIDEBAR_PANELS):
        return DEFAULT_PANEL
    return LEGACY_SIDEBAR_PANELS[number]


def RestorePanel(settings):
    """The page the window opens on: ``lastPanel`` when it names one to
    reopen on, else DEFAULT_PANEL. The Qt sidebar's index, when it is still
    there, is read first, once: into ``lastPanel`` unless that is set
    already, and removed."""
    if settings.contains(LEGACY_SIDEBAR_INDEX_KEY):
        legacy = LegacyPanel(settings.value(LEGACY_SIDEBAR_INDEX_KEY))
        settings.remove(LEGACY_SIDEBAR_INDEX_KEY)
        if not settings.contains(LAST_PANEL_KEY):
            settings.setValue(LAST_PANEL_KEY, legacy)
    panel = settings.value(LAST_PANEL_KEY)
    return panel if IsReopenable(panel) else DEFAULT_PANEL


def RestoreNavCollapsed(settings):
    """Whether the AppShell's sidebar opens hidden: ``navCollapsed``, the Qt
    sidebar's sidebarCollapsed_v22 read into it once (as RestorePanel reads
    the index)."""
    if settings.contains(LEGACY_SIDEBAR_COLLAPSED_KEY):
        legacy = _Bool(settings.value(LEGACY_SIDEBAR_COLLAPSED_KEY))
        settings.remove(LEGACY_SIDEBAR_COLLAPSED_KEY)
        if not settings.contains(NAV_COLLAPSED_KEY):
            settings.setValue(NAV_COLLAPSED_KEY, legacy)
    return _Bool(settings.value(NAV_COLLAPSED_KEY, False))


def SaveWindowState(settings, panel, navCollapsed):
    """Keep the page to reopen on (one RestorePanel would take: anything
    else leaves the saved one) and whether the sidebar is hidden. Only what
    differs from the saved values is written (the window saves on each
    change, and on close); returns whether anything was."""
    wrote = False
    if IsReopenable(panel) and settings.value(LAST_PANEL_KEY) != panel:
        settings.setValue(LAST_PANEL_KEY, panel)
        wrote = True
    collapsed = bool(navCollapsed)
    if (not settings.contains(NAV_COLLAPSED_KEY)
            or _Bool(settings.value(NAV_COLLAPSED_KEY)) != collapsed):
        settings.setValue(NAV_COLLAPSED_KEY, collapsed)
        wrote = True
    return wrote
