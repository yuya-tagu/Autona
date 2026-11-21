"""Every overlay design under ``layout/`` as one normalized list.

Two kinds of layout folder live side by side (ADR-007):

* **Built-in (TSH-style) layouts** -- ``layout/{name}/`` with a hand-written
  ``index.html``. Their metadata is the ``autona`` block of the folder's own
  ``settings.json`` (the file globals.js already merges for the page; the
  page ignores the extra key)::

      "autona": {"kind": "scoreboard", "displayName": "Simple",
                 "format": "1v1 / チーム", "game": "汎用（全ゲーム）"}

  ``kind`` is required; ``displayName`` / ``format`` / ``game`` / ``entry``
  are optional, and so is ``contract`` (the overlay contract the layout
  reads, ADR-008 D1: 1 when left out; a folder declaring one not in
  OVERLAY_CONTRACTS is not listed, with a warning).
* **Designer projects** -- ``layout/custom/{slug}/`` with the stub
  ``index.html`` and a ``project.json``. Metadata is the project's ``name``
  and ``meta.format`` / ``meta.game``; unset fields take DESIGNER_DEFAULT_*.
  The catalog lists a project as ``"Custom: " + name`` (DESIGNER_NAME_PREFIX)
  so it reads apart from the built-in layouts.

Both are normalized to the same entry shape::

    {"id": "custom/x", "kind": "designer", "displayName": "Custom: ...",
     "format": "...", "game": "...", "entry": "index.html"}

Display names are unique within a kind: when two entries of one kind would
show the same name (a copied ``scoreboard_simple - コピー`` next to
``scoreboard_simple``, two projects with one name), every one of them shows
``"{displayName} ({id})"`` instead. A name nothing else shares is untouched.

``id`` is the path below ``layout/`` with forward slashes, so
``layout/{id}/{entry}`` is the OBS file path and ``/layout/{id}/{entry}`` the
web server URL. ``format`` / ``game`` are None when a built-in layout does not
declare them.

**Template packages** (ADR-007 T1) are built-in folders whose block says
``kind`` ``result`` (the tournament result's layouts, core/ResultService) or
``thumbnail`` (the match thumbnail's templates, core/ThumbnailService). They
have no folder-name fallback: the block is their manifest, and one that does
not hold what the template contract needs (``contract``, ``size``, ``rows`` /
``template``; _TemplateEntry) is skipped with a warning. Their entry carries
more (``folder``, ``bundled``, ``contract``, ``size``, ``author``,
``license``, and ``rows`` or ``template``), and ``id`` is not always the
folder: the shipped eight keep the ids settings.json and the state saved
before (BUNDLED_TEMPLATES, frozen); any other package's id is its folder name.
A folder named like a shipped id of its kind, in any case (Windows does not
tell them apart), is not listed: the shipped one wins. Their names are
``displayNames`` -- ``{"ja", "en"}``, the languages there are: a shipped
package's core's words (CoreWords), another's its block's ``displayName`` (a
string, which is the name in both, or ``{"ja", "en"}``) -- and
``displayName``, as for every kind a string: the name in the language of the
``locale`` the list is asked in (DisplayText). They are listed after the
other built-in layouts, the shipped ones first in BUNDLED_TEMPLATES' order.

An overlay whose block declares ``contract`` (a design made from the layout
kit does, ADR-008 D2) carries it in its entry as well: the pages expect such a
layout to load layout/include/autona.js and tell its preview so (ADR-008 D3).
An overlay that does not declare one has no ``contract`` key.

**What is not listed, and why** (ADR-008 D3): a scan also gives the folders
it skipped (Scan), each ``{"folder", "kind", "reason", "detail"}`` -- the
kind when the block or the folder name says one (None otherwise), a reason of
UNLISTED_REASONS and a short English detail (a file name, the value it did
not take, a JSON error; never a path) or None. What never was a design is not
among them: the reserved folders (custom/ -- the Designer's projects --
included), a work folder of the layout kit, a file, the folders the release
ships that no list shows (SHIPPED_NON_DESIGNS) or shipped once
(RETIRED_SHIPPED_DIRS: an update leaves a deleted folder behind), and a folder
with neither a settings.json nor a page (an .html file) in it.

A listed entry whose folder has a manifest problem the catalog went past -- a
settings.json it could not read, a broken block or an unknown kind (listed by
its folder name all the same), an ``entry`` that is not a file name -- carries
``warnings``: ``[{"reason", "detail"}]``, the reasons and details of the
unlisted ones (plus UNLISTED_BAD_ENTRY). An entry without one has no
``warnings`` key.

This is the single source the Board / Commentary / Top 8 / Result / Thumbnail
pages, the Designer's "open" list (AutonaDesignerService.list_projects), the
result's and the thumbnail's services and renderers, and
``GET /api/v1/layouts`` read; none of them keeps its own folder scan or
display-name table any more.

Core module: no Qt (tests/test_core_qt_free.py), and nothing happens at import
time -- every function reads the filesystem when called. The folder is
``Root()``, the one rule every reader goes through (the services, the
renderers, the routes): LAYOUT_ROOT under the app's directory, which
``bootstrap_core()`` gives (``SetAppDirectory``: the directory the app
started in, the one its static files are served from), or the working
directory before that. A function given a root reads that one instead.
"""

import json
import os
import subprocess
import sys
from collections import Counter

from loguru import logger

from . import CoreWords
from .SettingsManager import SettingsManager

LAYOUT_ROOT = "layout"
CUSTOM_DIRNAME = "custom"
DEFAULT_ENTRY = "index.html"
PROJECT_FILE = "project.json"
SETTINGS_FILE = "settings.json"
METADATA_KEY = "autona"

KIND_SCOREBOARD = "scoreboard"
KIND_DESIGNER = "designer"
KIND_COMMENTARY = "commentary"
KIND_TOP8 = "top8"
KIND_RESULT = "result"
KIND_THUMBNAIL = "thumbnail"
KINDS = (KIND_SCOREBOARD, KIND_DESIGNER, KIND_COMMENTARY, KIND_TOP8,
         KIND_RESULT, KIND_THUMBNAIL)

# ── template packages (ADR-007) ──────────────────────────────────────────

TEMPLATE_KINDS = (KIND_RESULT, KIND_THUMBNAIL)
# The template contracts this Autona draws (ADR-007 D3): a package declaring
# another is not listed.
TEMPLATE_CONTRACTS = (1,)
# A package's entry page when its block names none.
TEMPLATE_DEFAULT_ENTRY = {KIND_RESULT: "result.html",
                          KIND_THUMBNAIL: "thumbnail.html"}
# The longest side a package's picture may declare (px).
TEMPLATE_MAX_SIDE = 4096
# What a result package draws (`rows`): players' rows (16 of them are kept)
# or teams' (8).
ROWS_PLAYERS = "players"
ROWS_TEAMS = "teams"
RESULT_ROWS = (ROWS_PLAYERS, ROWS_TEAMS)

# The shipped template packages: kind -> {id: folder}, frozen (ADR-007 D1).
# The ids are what settings.json (result_config.layout,
# thumbnail_config.template_id), the state's result_layout and the v1 answers
# have always held, so they never change; the order is the one the pages list
# them in. tests/test_template_packages.py holds this to a copy.
BUNDLED_TEMPLATES = {
    KIND_RESULT: {
        "horizontal": "result",
        "horizontal_16": "result_top16",
        "vertical": "result_vertical",
        "vertical_geki": "result_vertical_geki",
        "team": "result_team",
        "team_split": "result_team_split",
    },
    KIND_THUMBNAIL: {
        "type_a": "thumbnail",
        "type_b": "thumbnail_type_b",
    },
}
# The shipped result layouts of players that are given eight rows, as they
# always were (the payload's golden); any other layout of players is given
# all sixteen (ResultService.SlotsFor). Frozen with the ids above.
BUNDLED_EIGHT_ROW_RESULTS = ("horizontal", "vertical", "vertical_geki")
# A shipped package's name in core's words (CoreWords), by its id.
_BUNDLED_NAME_KEYS = {KIND_RESULT: "result_layout_{}",
                      KIND_THUMBNAIL: "thumbnail_template_{}"}

# What a Designer project without meta.format / meta.game shows. Matches the
# Board panel's former fixed text for every custom design.
DESIGNER_DEFAULT_FORMAT = "1v1"
DESIGNER_DEFAULT_GAME = "カスタム"

# Put in front of a Designer project's name in the catalog, so a project is
# never mistaken for (or confused by name with) a built-in layout. The Board
# panel labelled custom designs this way before the catalog existed.
DESIGNER_NAME_PREFIX = "Custom: "

# Folders under layout/ that are never a design themselves. custom/ holds the
# Designer projects (scanned separately); designer_runtime/ is their shared
# renderer; include/ and icons/ are shared assets.
_RESERVED_DIRS = frozenset({CUSTOM_DIRNAME, "designer_runtime", "include", "icons"})

# The work folder core/LayoutKit builds a new design in before renaming it
# into place (ADR-008 D2): never listed, however far it got.
NEW_DESIGN_WORK_PREFIX = ".new-design-"

# Folders the release ships under layout/ that are not a design of any list
# (the statistics' pages, which the Stats page names itself, the winner popup,
# the camera border): not listed, and no reason is given for them either.
# tests/test_layout_catalog_unlisted.py holds the shipped tree to giving none.
SHIPPED_NON_DESIGNS = frozenset({
    "camborder_Universal", "h2h_record", "last_sets", "recent_sets",
    "tournament_history", "upset_factor", "winner_popup",
})

# Folders an earlier release shipped under layout/ and a later one deleted.
# The update merges the release over the install and never deletes, so they
# stay on an operator's PC: no reason is given for them (one with a folder-name
# prefix and its page is still listed by its name, as before). Add a folder
# here when the release stops shipping it (tests/test_layout_catalog_unlisted.py
# compares this with git's history).
RETIRED_SHIPPED_DIRS = frozenset({
    "bracket", "bracket_top12", "bracket_top16", "bracket_ultimate",
    "character_gallery", "character_line", "fonts", "game_screenshots", "map",
    "map_world_fighters", "player_list", "player_presentation",
    "recent_sets_full", "ruleset", "scoreboard", "scoreboard_2xko",
    "scoreboard_4by3", "scoreboard_BST", "scoreboard_JinTracker",
    "scoreboard_WellyRoA", "scoreboard_WellySmash", "scoreboard_artistic",
    "scoreboard_dynamic", "scoreboard_mainstagy", "scoreboard_pandastic",
    "scoreboard_roa2", "scoreboard_vgbootcampy",
    "scoreboard_vgbootcampy_classic", "scoreboard_vgbootcampy_neo",
    "scoreboard_vgbootcampy_waiting", "stage_strike", "stream_queue",
    "stream_queue_simple", "top_8", "top_n_list", "versus_screen",
    "versus_screen_pokken", "versus_screen_tekken8",
})

# Why a folder is not listed (Scan; ADR-008 D3). Codes are an external
# contract (GET /api/v1/layouts' "unlisted"): only ever added to.
UNLISTED_NO_ENTRY = "no_entry"              # its entry page is not there
UNLISTED_BAD_SETTINGS = "bad_settings"      # settings.json is not a JSON object
UNLISTED_BAD_BLOCK = "bad_block"            # its autona block is not an object
UNLISTED_UNKNOWN_KIND = "unknown_kind"      # the block's kind is none we list
UNLISTED_UNKNOWN_CONTRACT = "unknown_contract"  # a contract this does not serve
UNLISTED_BAD_TEMPLATE = "bad_template"      # a template manifest lacks a field
UNLISTED_BUNDLED_ID = "bundled_id"          # named like a shipped template
UNLISTED_NO_MANIFEST = "no_manifest"        # no block, no folder-name prefix
# A listed entry's warning only: its block's entry is not a file name (the
# default page is listed instead).
UNLISTED_BAD_ENTRY = "bad_entry"
UNLISTED_REASONS = (
    UNLISTED_NO_ENTRY, UNLISTED_BAD_SETTINGS, UNLISTED_BAD_BLOCK,
    UNLISTED_UNKNOWN_KIND, UNLISTED_UNKNOWN_CONTRACT, UNLISTED_BAD_TEMPLATE,
    UNLISTED_BUNDLED_ID, UNLISTED_NO_MANIFEST, UNLISTED_BAD_ENTRY)
# The longest detail an unlisted folder is given.
UNLISTED_DETAIL_MAX = 200

# The overlay contracts this Autona serves (ADR-008 D1: the view
# layout/include/autona.js hands a layout, its table
# layout/include/overlay_contract.json). An overlay's block may leave
# "contract" out (the first); one declaring another is not listed.
OVERLAY_CONTRACTS = (1,)

# Kinds a built-in settings.json may declare. "designer" is reserved for
# layout/custom/ projects.
_BUILTIN_KINDS = (KIND_SCOREBOARD, KIND_COMMENTARY, KIND_TOP8) + TEMPLATE_KINDS

# Built-in folders without an autona block -- a layout the operator dropped
# into layout/ before the block existed -- keep being found by the folder-name
# convention the panels used before this catalog. (prefix, kind, variants
# need an underscore after the prefix). Their display name is derived from
# the folder name the same way the panels derived it.
_LEGACY_PREFIXES = (
    ("scoreboard", KIND_SCOREBOARD, False),
    ("commentators", KIND_COMMENTARY, True),
    ("bracket_top8", KIND_TOP8, True),
)


# The Board page's selection (ADR-005 S4s): the name of a selection, not a
# catalog kind (KINDS). It lists two of those.
SELECTION_BOARD = "board"

# The design a panel shows and hands to OBS, per selection: the settings.json
# key it is kept under, the layout picked when none is kept (or the kept one is
# gone) and the catalog kinds it lists. The Commentary page's (ADR-005 S4b), the
# Top 8's (S4h, under the key its Qt panel always kept; the Top 8 page's since
# S4i) and the Board's (S4s, likewise): the built-in scoreboards, then the
# Designer projects.
SELECTION_SETTINGS = {
    KIND_COMMENTARY: ("commentary.selected_design", "commentators",
                      (KIND_COMMENTARY,)),
    KIND_TOP8: ("top8.selected_design", "bracket_top8", (KIND_TOP8,)),
    SELECTION_BOARD: ("board.selected_design", "scoreboard_crewbattle",
                      (KIND_SCOREBOARD, KIND_DESIGNER)),
}

# The winner popup: a separate OBS source beside the Board's design, in a folder
# of its own that no selection lists.
WINNER_POPUP_DIR = "winner_popup"

# The Board's OBS sources, by what a client asks for: the selected design and
# the winner popup. A client names one of these, never a path.
BOARD_FILE_DESIGN = "design"
BOARD_FILE_WINNER_POPUP = "winnerPopup"
BOARD_FILES = (BOARD_FILE_DESIGN, BOARD_FILE_WINNER_POPUP)


class SelectionChanged(Exception):
    """A request about the design it shows, which is not the selected one any
    more: another client chose meanwhile."""


def Layouts(kinds=None, root=None, locale=None):
    """Catalog entries whose kind is in ``kinds`` (all when None/empty):
    built-in layouts (folder-name order; the template packages after the
    others, the shipped ones first), then Designer projects
    (case-insensitive slug order).

    Designer projects are only read when ``designer`` is asked for, so the
    panels that list one built-in kind never parse project.json files.
    Names are made unique per kind, so filtering first gives the same names
    as listing them all. A template package's ``displayName`` is its name
    in ``locale``'s language (a program locale; None: English).
    """
    return Scan(kinds, root, locale)[0]


def Scan(kinds=None, root=None, locale=None):
    """``(Layouts(kinds, root, locale), unlisted)``, from one reading of the
    folder: ``unlisted`` is the built-in folders not listed and why (the
    module's docstring), in folder order, those of a kind in ``kinds`` (all
    when None/empty) and those whose kind is unknown (every list's)."""
    wanted = set(kinds or KINDS)
    base = _Base(root)
    unlisted = []
    layouts = [_Named(entry, locale)
               for entry in _BuiltinLayouts(base, unlisted)
               if entry["kind"] in wanted]
    if KIND_DESIGNER in wanted:
        layouts.extend(_DesignerLayouts(base))
    return _Disambiguate(layouts), [
        folder for folder in unlisted
        if folder["kind"] is None or folder["kind"] in wanted]


def Templates(kind, root=None, locale=None):
    """The template packages of ``kind`` (one of TEMPLATE_KINDS) by id, in
    the order they are listed: what the result's and the thumbnail's services
    and renderers draw from (ADR-007 D1). Read now, every call."""
    return {entry["id"]: entry for entry in Layouts([kind], root, locale)}


def TemplatePath(entry, name, root=None):
    """The path of the file ``name`` (a file name the catalog checked: the
    entry page, a thumbnail's template) in a template package's folder."""
    return os.path.normpath(
        os.path.join(_Base(root), *entry["folder"].split("/"), name))


def BundledNameKey(kind, templateId):
    """The key of a shipped template package's name in core's words
    (CoreWords): what the services have always named it by."""
    return _BUNDLED_NAME_KEYS[kind].format(templateId)


def DisplayText(displayName, locale):
    """A template package's display name (a string, or ``{"ja", "en"}``) in
    program locale ``locale``'s language (CoreWords.Language), else in the
    other one; "" for anything else."""
    if isinstance(displayName, str):
        return displayName
    if not isinstance(displayName, dict):
        return ""
    language = CoreWords.Language(locale)
    for candidate in (language,) + tuple(
            other for other in _NAME_LANGUAGES if other != language):
        text = displayName.get(candidate)
        if isinstance(text, str) and text:
            return text
    return ""


def SelectedLayout(kind, root=None):
    """The catalog entry selected for ``kind`` (a SELECTION_SETTINGS name),
    or None when it lists no layout at all.

    The kept id when the catalog still lists it, else the kind's default,
    else the first entry -- the design combos' rule. What
    is kept is not rewritten by a fallback: the operator's choice comes back
    when its folder does.
    """
    key, default, kinds = SELECTION_SETTINGS[kind]
    layouts = Layouts(kinds, root)
    byId = {entry["id"]: entry for entry in layouts}
    kept = SettingsManager.Get(key, default)
    if isinstance(kept, str) and kept in byId:
        return byId[kept]
    if default in byId:
        return byId[default]
    return layouts[0] if layouts else None


def SelectLayout(kind, layoutId, root=None):
    """Keep ``layoutId`` as the selected layout of ``kind`` (a
    SELECTION_SETTINGS name) and return its entry; None, keeping nothing,
    when the catalog does not list it for that selection. The settings.json
    key and its value (a layout id) are the ones the panels always kept."""
    key, _default, kinds = SELECTION_SETTINGS[kind]
    entry = next((entry for entry in Layouts(kinds, root)
                  if entry["id"] == layoutId), None)
    # A pick of what is shown may be of a fallback (kept: another design, or
    # none) or of what is kept already; only the former has anything to write.
    if entry is not None and SettingsManager.Get(key) != layoutId:
        SettingsManager.Set(key, layoutId)
    return entry


def BoardFiles(root=None):
    """The local files the Board's OBS sources point at, as absolute paths:
    ``{"designId": <the selected design, None without one>, BOARD_FILE_DESIGN:
    <its file, None without one>, BOARD_FILE_WINNER_POPUP: <the popup's>}``. Not
    part of an entry: an absolute path names the operator's account, and the
    catalog is readable by anyone who reaches the port. The id says whose path
    it is: the selection may have moved since a client read it."""
    base = _Base(root)
    design = SelectedLayout(SELECTION_BOARD, root)
    return {
        "designId": design["id"] if design else None,
        BOARD_FILE_DESIGN:
            _EntryPath(base, design["id"], design["entry"]) if design else None,
        BOARD_FILE_WINNER_POPUP:
            _EntryPath(base, WINNER_POPUP_DIR, DEFAULT_ENTRY),
    }


def RevealBoardFile(target, designId=None, root=None):
    """Show one of the Board's OBS files (a BOARD_FILES name) in this PC's file
    manager. For BOARD_FILE_DESIGN, ``designId`` is the design the operator is
    looking at: SelectionChanged, starting nothing, when another is selected by
    now. False when there is no such file, which is logged; a file manager that
    cannot be started raises OSError. The path is the catalog's: a client names
    a target, never a path."""
    base = _Base(root)
    files = BoardFiles(root)
    if target == BOARD_FILE_DESIGN and files["designId"] != designId:
        raise SelectionChanged(target)
    path = files[target]
    if path is None or not os.path.isfile(path):
        shown = os.path.relpath(path, base) if path else "no design"
        logger.warning(f"LayoutCatalog: no {target} file to show ({shown})")
        return False
    return _Reveal(path, base, select=True)


def RevealLayoutFolder(layoutId, root=None):
    """Open the folder of the layout the catalog lists as ``layoutId`` (any
    kind) in this PC's file manager: the design lists' "open the folder",
    after "make a new design" (ADR-008 D2). RevealBoardFile's rules: a
    client names a listed id, never a path. False, starting nothing, when
    the catalog lists no such layout (logged); a file manager that cannot be
    started raises OSError."""
    base = _Base(root)
    entry = next((entry for entry in Layouts(root=root)
                  if entry["id"] == layoutId), None)
    if entry is None:
        logger.warning("LayoutCatalog: no listed layout to show")
        return False
    path = os.path.normpath(os.path.join(
        base, *entry.get("folder", entry["id"]).split("/")))
    if not os.path.isdir(path):
        logger.warning(f"LayoutCatalog: no folder to show "
                       f"({os.path.relpath(path, base)})")
        return False
    return _Reveal(path, base, select=False)


def CustomLayouts(customRoot):
    """Designer projects below ``customRoot`` (``layout/custom``).

    A project needs a real directory (symlinks and junctions are skipped)
    holding both the entry ``index.html`` and a ``project.json`` that parses
    to an object. Anything else -- a folder still being written, a stray
    file, the ``_legacy/`` rollback copy inside a project -- is not listed.

    ``displayName`` here is the project's own name (or its slug), which is
    what the Designer's "open" list shows. The catalog (Layouts) lists the
    same entry as ``"Custom: " + name``, made unique per kind.
    """
    try:
        if not os.path.isdir(customRoot):
            return []
        slugs = sorted(os.listdir(customRoot), key=str.casefold)
    except OSError as e:
        logger.warning(f"LayoutCatalog: cannot list {customRoot}: {e}")
        return []

    layouts = []
    for slug in slugs:
        directory = os.path.join(customRoot, slug)
        if not os.path.isdir(directory) or _IsLink(directory):
            continue
        projectPath = os.path.join(directory, PROJECT_FILE)
        if not (os.path.isfile(os.path.join(directory, DEFAULT_ENTRY))
                and os.path.isfile(projectPath)):
            continue
        project = _ReadJson(projectPath)
        if not isinstance(project, dict):
            continue
        meta = project.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        layouts.append({
            "id": f"{CUSTOM_DIRNAME}/{slug}",
            "kind": KIND_DESIGNER,
            "displayName": str(project.get("name") or slug),
            "format": _Text(meta.get("format")) or DESIGNER_DEFAULT_FORMAT,
            "game": _Text(meta.get("game")) or DESIGNER_DEFAULT_GAME,
            "entry": DEFAULT_ENTRY,
        })
    return layouts


# ── the folder ───────────────────────────────────────────────────────────

# () -> the app's directory, which LAYOUT_ROOT is under; None: the working
# directory. Given by the shell (SetAppDirectory).
_appDirectory = None


def SetAppDirectory(directory):
    """``directory()`` -> the app's directory, read at every use: what
    LAYOUT_ROOT is under for every reader (Root). bootstrap_core() gives the
    directory the app started in (AutonaDirHelper.current_dir), so that the
    services and the renderers read one folder, whatever the working
    directory is a moment later. None: the working directory again."""
    global _appDirectory
    _appDirectory = directory


def Root():
    """The layout folder, as an absolute path: LAYOUT_ROOT under the app's
    directory (an absolute LAYOUT_ROOT as it is)."""
    base = _appDirectory() if _appDirectory is not None else os.getcwd()
    return os.path.abspath(os.path.join(base, LAYOUT_ROOT))


# ── internals ────────────────────────────────────────────────────────────
def _Base(root):
    return os.path.abspath(root) if root else Root()


def _Reveal(path, base, select):
    """Show ``path`` (a file the catalog found, selected in its folder; or,
    not ``select``, a folder, opened) in this PC's file manager: True."""
    if sys.platform.startswith("win"):
        # NTFS refuses a double quote in a name, so the catalog has not found
        # one with it; if one ever gets here it could close the quotes below.
        if '"' in path:
            logger.warning(f"LayoutCatalog: not showing "
                           f"{os.path.relpath(path, base)}")
            return False
        if select:
            _ExplorerSelect(path)
        else:
            _ExplorerOpen(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if select else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if select else path])
    return True


def _Explorer():
    # Explorer parses its own command line and splits it at commas, so the path
    # is always in quotes, which subprocess's list form adds only for a space.
    # So a string (still no shell), and explorer's own full path: by name
    # Windows looks in the working directory before system32. Its exit code is
    # not asked: it is non-zero even on success.
    windows = os.environ.get("WINDIR")
    if not windows:
        raise OSError("WINDIR is not set")
    return os.path.join(windows, "explorer.exe")


def _ExplorerSelect(path):
    subprocess.Popen(f'"{_Explorer()}" /select,"{path}"')


def _ExplorerOpen(path):
    subprocess.Popen(f'"{_Explorer()}" "{path}"')


def _EntryPath(base, layoutId, entry):
    return os.path.normpath(os.path.join(base, *layoutId.split("/"), entry))


def _IsLink(path):
    # A Windows directory junction is not a symlink to os.path.islink, yet it
    # points outside the tree just the same (os.path.isjunction: Python 3.12).
    return os.path.islink(path) or os.path.isjunction(path)


def _DesignerLayouts(base):
    """The Designer projects as the catalog lists them (name prefixed)."""
    return [
        dict(entry, displayName=DESIGNER_NAME_PREFIX + entry["displayName"])
        for entry in CustomLayouts(os.path.join(base, CUSTOM_DIRNAME))
    ]


def _Disambiguate(layouts):
    """Suffix every display name shared within one kind with the entry id.

    Per kind because each kind is its own pulldown (the Board page lists
    scoreboard and designer together, but every designer name carries the prefix):
    the shipped commentators/ and bracket_top8/ are both "標準" and never
    appear in the same list, so neither is renamed. A template package's
    ``displayNames`` get the id with its ``displayName``.
    """
    counts = Counter(
        (entry["kind"], entry["displayName"]) for entry in layouts)
    return [
        _WithId(entry)
        if counts[(entry["kind"], entry["displayName"])] > 1 else entry
        for entry in layouts
    ]


def _WithId(entry):
    named = dict(entry, displayName=f"{entry['displayName']} ({entry['id']})")
    if "displayNames" in entry:
        named["displayNames"] = {
            language: f"{text} ({entry['id']})"
            for language, text in entry["displayNames"].items()}
    return named


def _Named(entry, locale):
    """A template package's entry with its ``displayName`` in ``locale``'s
    language; another kind's as it is."""
    if "displayNames" not in entry:
        return entry
    return dict(entry, displayName=DisplayText(entry["displayNames"], locale))


def _BuiltinLayouts(base, unlisted=None):
    """The built-in entries; each folder that is not listed is handed to
    ``unlisted`` (as Scan gives it), unless it never was a design."""
    try:
        names = sorted(os.listdir(base))
    except OSError as e:
        logger.warning(f"LayoutCatalog: cannot list {base}: {e}")
        return []

    shipped = {folder.casefold()
               for folder in SHIPPED_NON_DESIGNS | RETIRED_SHIPPED_DIRS}
    layouts = []
    for name in names:
        # A design being made (core/LayoutKit builds one in a work folder
        # and renames it into place) is not one yet.
        if name in _RESERVED_DIRS or name.startswith(NEW_DESIGN_WORK_PREFIX):
            continue
        directory = os.path.join(base, name)
        if not os.path.isdir(directory):
            continue

        def skip(reason, kind, detail=None, name=name):
            if unlisted is not None and name.casefold() not in shipped:
                unlisted.append({"folder": name, "kind": kind,
                                 **_Warning(reason, detail)})
            return None

        entry = _BuiltinEntry(name, directory, skip)
        if entry is not None:
            layouts.append(entry)
    return _TemplatesLast(layouts)


def _Clipped(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _TemplatesLast(layouts):
    """The template packages after the other built-in layouts: per kind, the
    shipped ones in BUNDLED_TEMPLATES' order (the order the services always
    listed them in), then the others in folder order."""
    ordered = [entry for entry in layouts
               if entry["kind"] not in TEMPLATE_KINDS]
    for kind in TEMPLATE_KINDS:
        order = list(BUNDLED_TEMPLATES[kind])
        ofKind = [entry for entry in layouts if entry["kind"] == kind]
        ordered += sorted((entry for entry in ofKind if entry["bundled"]),
                          key=lambda entry: order.index(entry["id"]))
        ordered += [entry for entry in ofKind if not entry["bundled"]]
    return ordered


def _BuiltinEntry(name, directory, skip):
    """The folder's entry, or ``skip(reason, kind, detail)`` (which returns
    None) when it is not listed."""
    block, problem = _MetadataBlock(name, directory)
    if block is not None and block["kind"] in TEMPLATE_KINDS:
        # A template package: the block is its manifest, with no folder-name
        # fallback (ADR-007 D1).
        return _TemplateEntry(name, directory, block, skip)
    if block is not None and not _KnownOverlayContract(name, block):
        return skip(UNLISTED_UNKNOWN_CONTRACT, block["kind"],
                    repr(block["contract"]))
    inferred = _InferFromName(name)
    if block is None and inferred is None:
        if problem is not None:
            return skip(problem[0], None, problem[1])
        # Neither a settings.json nor a page: a folder of something else
        # (pictures, fonts), not a design that failed.
        if _LooksLikeADesign(directory):
            return skip(UNLISTED_NO_MANIFEST, None)
        return None
    warnings = []
    if problem is not None:
        # Listed by its folder name; what its settings.json says is not used.
        warnings.append(_Warning(*problem))
    if block is None:
        kind, displayName = inferred
        block = {"kind": kind, "displayName": displayName}
    elif not _Text(block.get("displayName")):
        # A block that only declares its kind keeps the folder-derived name.
        block = dict(block, displayName=(
            inferred[1] if inferred and inferred[0] == block["kind"]
            else _Prettify(name)))

    entry = _Text(block.get("entry")) or DEFAULT_ENTRY
    if os.path.basename(entry) != entry or entry in (".", ".."):
        logger.warning(
            f"LayoutCatalog: {name}/{SETTINGS_FILE}: entry must be a file "
            f"name in the folder, got {entry!r}; using {DEFAULT_ENTRY}")
        warnings.append(_Warning(UNLISTED_BAD_ENTRY, repr(entry)))
        entry = DEFAULT_ENTRY
    if not os.path.isfile(os.path.join(directory, entry)):
        return skip(UNLISTED_NO_ENTRY, block["kind"], entry)
    listed = {
        "id": name,
        "kind": block["kind"],
        "displayName": _Text(block.get("displayName")),
        "format": _Text(block.get("format")),
        "game": _Text(block.get("game")),
        "entry": entry,
    }
    # Declared (and known, above): the layout says it is written to the
    # overlay contract, so its preview expects autona.js (ADR-008 D3).
    if "contract" in block:
        listed["contract"] = block["contract"]
    if warnings:
        listed["warnings"] = warnings
    return listed


def _Warning(reason, detail):
    return {"reason": reason,
            "detail": None if detail is None
            else _Clipped(detail, UNLISTED_DETAIL_MAX)}


def _LooksLikeADesign(directory):
    """Whether a folder holds a settings.json or a page (an .html file)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return False
    return any(name == SETTINGS_FILE or name.lower().endswith(".html")
               for name in names)


def _KnownOverlayContract(name, block):
    """Whether an overlay's block (kind scoreboard / commentary / top8)
    declares a contract this Autona serves (ADR-008 D1): none (the first,
    which every overlay written before the contract existed is) or one of
    OVERLAY_CONTRACTS. Another is logged and the folder is not listed --
    not even by its folder name: it was written for something this Autona
    does not give it (ADR-007 D1's rule for the template packages)."""
    if "contract" not in block:
        return True
    contract = block["contract"]
    if _IsInt(contract) and contract in OVERLAY_CONTRACTS:
        return True
    logger.warning(f"LayoutCatalog: {name}/{SETTINGS_FILE}: not listing the "
                   f"{block['kind']} layout: contract must be one of "
                   f"{OVERLAY_CONTRACTS}, got {contract!r}")
    return False


def _TemplateEntry(name, directory, block, skip):
    """A template package's entry (ADR-007 D1), or None, with a warning, when
    its manifest does not hold what the template contract needs: a known
    ``contract``, a ``size`` within TEMPLATE_MAX_SIDE, a result's ``rows``, a
    thumbnail's ``template`` file in the folder, and its entry page. One such
    folder costs its own entry only (and is handed to ``skip``)."""
    kind = block["kind"]

    def refuse(why, reason=UNLISTED_BAD_TEMPLATE):
        logger.warning(f"LayoutCatalog: {name}/{SETTINGS_FILE}: not listing "
                       f"the {kind} template: {why}")
        return skip(reason, kind, why)

    contract = block.get("contract")
    if not _IsInt(contract) or contract not in TEMPLATE_CONTRACTS:
        return refuse(f"contract must be one of {TEMPLATE_CONTRACTS}, "
                      f"got {contract!r}", UNLISTED_UNKNOWN_CONTRACT)
    size = block.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(
            _IsInt(side) and 1 <= side <= TEMPLATE_MAX_SIDE for side in size)):
        return refuse(f"size must be [width, height], each 1 to "
                      f"{TEMPLATE_MAX_SIDE}, got {size!r}")
    extra = {}
    if kind == KIND_RESULT:
        rows = block.get("rows")
        if rows not in RESULT_ROWS:
            return refuse(f"rows must be one of {RESULT_ROWS}, got {rows!r}")
        extra["rows"] = rows
    else:
        template = block.get("template")
        if not _IsFileName(template) \
                or not os.path.isfile(os.path.join(directory, template)):
            return refuse(f"template must name a file in the folder, "
                          f"got {template!r}")
        extra["template"] = template
    entry = block.get("entry")
    if entry is None:
        entry = TEMPLATE_DEFAULT_ENTRY[kind]
    if not _IsFileName(entry):
        return refuse(f"entry must be a file name in the folder, "
                      f"got {entry!r}")
    if not os.path.isfile(os.path.join(directory, entry)):
        logger.warning(f"LayoutCatalog: {name}/{SETTINGS_FILE}: not listing "
                       f"the {kind} template: there is no {entry}")
        return skip(UNLISTED_NO_ENTRY, kind, entry)

    # Folder names in any case: Windows does not tell them apart.
    shipped = BUNDLED_TEMPLATES[kind]
    folded = name.casefold()
    shippedId = next((templateId for templateId, folder in shipped.items()
                      if folder.casefold() == folded), None)
    if shippedId is None and folded in {
            templateId.casefold() for templateId in shipped}:
        return refuse("a shipped template has this id; the shipped one is "
                      "listed", UNLISTED_BUNDLED_ID)
    if shippedId is not None:
        key = BundledNameKey(kind, shippedId)
        displayNames = {language: CoreWords.WORDS[key][language]
                        for language in _NAME_LANGUAGES}
    else:
        displayNames = _TemplateNames(block.get("displayName")) or {
            language: _Prettify(name) for language in _NAME_LANGUAGES}
    return {
        "id": shippedId or name,
        "kind": kind,
        # In a language by Layouts (_Named).
        "displayName": DisplayText(displayNames, None),
        "displayNames": displayNames,
        "format": None,
        "game": None,
        "entry": entry,
        "folder": name,
        "bundled": shippedId is not None,
        "contract": contract,
        "size": list(size),
        **extra,
        "author": _Text(block.get("author")),
        "license": _Text(block.get("license")),
    }


# The languages a template package's displayName may be given in, in the
# order an entry spells them.
_NAME_LANGUAGES = (CoreWords.JAPANESE, CoreWords.ENGLISH)


def _TemplateNames(value):
    """A template package's displayName as declared, by language: ``{"ja",
    "en"}`` of the non-empty ones, a non-empty string in both; None for
    anything else."""
    if isinstance(value, dict):
        names = {language: _Text(value.get(language))
                 for language in _NAME_LANGUAGES}
        names = {language: text for language, text in names.items() if text}
        return names or None
    text = _Text(value)
    return {language: text for language in _NAME_LANGUAGES} if text else None


def _IsInt(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _IsFileName(value):
    """A file's name in a folder: no separator, no drive, not "." or ".."."""
    return isinstance(value, str) and value not in ("", ".", "..") \
        and os.path.basename(value) == value and "/" not in value \
        and "\\" not in value


def _MetadataBlock(name, directory):
    """``(block, problem)``: the folder's autona block, or None when it has
    none (or a broken one, which is logged and then treated as absent).
    ``problem`` is ``(reason, detail)`` for a broken one -- an unreadable
    settings.json, a block that is not an object, a kind not taken -- and
    None otherwise: what the folder is not listed for, if nothing else
    lists it."""
    path = os.path.join(directory, SETTINGS_FILE)
    if not os.path.isfile(path):
        return None, None
    settings, error = _ReadJsonOrError(path)
    if error is not None:
        return None, (UNLISTED_BAD_SETTINGS, error)
    if not isinstance(settings, dict):
        return None, (UNLISTED_BAD_SETTINGS, "not a JSON object")
    if METADATA_KEY not in settings:
        return None, None
    block = settings[METADATA_KEY]
    if not isinstance(block, dict) or block.get("kind") not in _BUILTIN_KINDS:
        logger.warning(
            f"LayoutCatalog: {name}/{SETTINGS_FILE}: ignoring the "
            f"{METADATA_KEY!r} block (kind must be one of {_BUILTIN_KINDS})")
        if not isinstance(block, dict):
            return None, (UNLISTED_BAD_BLOCK, None)
        return None, (UNLISTED_UNKNOWN_KIND, repr(block.get("kind")))
    return block, None


def _InferFromName(name):
    """(kind, displayName) by the pre-catalog folder-name convention."""
    for prefix, kind, needsUnderscore in _LEGACY_PREFIXES:
        if needsUnderscore:
            if name == prefix:
                return kind, "標準"
            if name.startswith(prefix + "_"):
                suffix = name[len(prefix):].lstrip("_")
                return kind, _Prettify(suffix, dashes=True) or name
        elif name.startswith(prefix):
            suffix = name[len(prefix):].lstrip("_") or name
            return kind, _Prettify(suffix)
    return None


def _Prettify(text, dashes=False):
    text = text.replace("_", " ")
    if dashes:
        text = text.replace("-", " ")
    return text.title()


def _Text(value):
    """A non-empty stripped string, else None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _ReadJson(path):
    return _ReadJsonOrError(path)[0]


def _ReadJsonOrError(path):
    """``(value, None)``, or ``(None, why)`` when the file cannot be read as
    JSON: why in a few words, with no path (an OSError's says the path)."""
    # One bad file costs only its own entry, never the whole list: whatever
    # reading or parsing it raises -- OSError (locked mid-save), a decode
    # error, RecursionError on absurdly deep nesting -- is logged and the file
    # is treated as unreadable.
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        logger.warning(
            f"LayoutCatalog: cannot read {path}: {type(e).__name__}: {e}")
        if isinstance(e, OSError):
            return None, f"{type(e).__name__}: {e.strerror or 'unreadable'}"
        return None, f"{type(e).__name__}: {e}"
