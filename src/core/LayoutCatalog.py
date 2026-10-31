"""Every overlay design under ``layout/`` as one normalized list.

Two kinds of layout folder live side by side (docs/changes/
layout-custom-unification.md §3):

* **Built-in (TSH-style) layouts** -- ``layout/{name}/`` with a hand-written
  ``index.html``. Their metadata is the ``autona`` block of the folder's own
  ``settings.json`` (the file globals.js already merges for the page; the
  page ignores the extra key)::

      "autona": {"kind": "scoreboard", "displayName": "Simple",
                 "format": "1v1 / チーム", "game": "汎用（全ゲーム）"}

  ``kind`` is required; ``displayName`` / ``format`` / ``game`` / ``entry``
  are optional.
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

This is the single source the Board / Commentary / Top 8 tabs, the Designer's
"open" list (AutonaDesignerService.list_projects) and ``GET /api/v1/layouts``
read; none of them keeps its own folder scan or display-name table any more.

Core module: no Qt (tests/test_core_qt_free.py), and nothing happens at import
time -- every function reads the filesystem when called, relative to the
working directory unless a root is passed.
"""

import json
import os
from collections import Counter
from typing import NamedTuple

from loguru import logger

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
KINDS = (KIND_SCOREBOARD, KIND_DESIGNER, KIND_COMMENTARY, KIND_TOP8)

# What a Designer project without meta.format / meta.game shows. Matches the
# Board tab's former fixed text for every custom design.
DESIGNER_DEFAULT_FORMAT = "1v1"
DESIGNER_DEFAULT_GAME = "カスタム"

# Put in front of a Designer project's name in the catalog, so a project is
# never mistaken for (or confused by name with) a built-in layout. The Board
# tab labelled custom designs this way before the catalog existed.
DESIGNER_NAME_PREFIX = "Custom: "

# Folders under layout/ that are never a design themselves. custom/ holds the
# Designer projects (scanned separately); designer_runtime/ is their shared
# renderer; include/ and icons/ are shared assets.
_RESERVED_DIRS = frozenset({CUSTOM_DIRNAME, "designer_runtime", "include", "icons"})

# Kinds a built-in settings.json may declare. "designer" is reserved for
# layout/custom/ projects.
_BUILTIN_KINDS = (KIND_SCOREBOARD, KIND_COMMENTARY, KIND_TOP8)

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


# The design a panel shows and hands to OBS, per kind that has one here:
# the settings.json key it is kept under and the layout picked when none is
# kept (or the kept one is gone). Only the Commentary page's so far (ADR-005
# S4b; the Qt panel reads the same key and the same default). The Board and
# Top 8 panels keep their own until they move to the Web UI.
SELECTION_SETTINGS = {
    KIND_COMMENTARY: ("commentary.selected_design", "commentators"),
}


class Catalog(NamedTuple):
    layouts: list
    # Absolute directories whose change can add, remove or rename an entry
    # (layout/, layout/custom/ and each project folder below it). Only
    # existing ones are listed.
    watchDirs: list


def Discover(root=None):
    """Return the full catalog: built-in layouts (folder-name order), then
    Designer projects (case-insensitive slug order), plus the watch dirs."""
    base = _Base(root)
    layouts = _BuiltinLayouts(base)
    layouts.extend(_DesignerLayouts(base))
    return Catalog(_Disambiguate(layouts), WatchDirs(root))


def Layouts(kinds=None, root=None):
    """Catalog entries whose kind is in ``kinds`` (all when None/empty).

    Designer projects are only read when ``designer`` is asked for, so the
    panels that list one built-in kind never parse project.json files.
    Names are made unique per kind, so filtering first gives the same names
    as Discover().
    """
    wanted = set(kinds or KINDS)
    base = _Base(root)
    layouts = [entry for entry in _BuiltinLayouts(base)
               if entry["kind"] in wanted]
    if KIND_DESIGNER in wanted:
        layouts.extend(_DesignerLayouts(base))
    return _Disambiguate(layouts)


def SelectedLayout(kind, root=None):
    """The catalog entry selected for ``kind`` (a SELECTION_SETTINGS kind),
    or None when the kind lists no layout at all.

    The kept id when the catalog still lists it, else the kind's default,
    else the first entry -- the Commentary panel's rule for its combo. What
    is kept is not rewritten by a fallback: the operator's choice comes back
    when its folder does.
    """
    key, default = SELECTION_SETTINGS[kind]
    layouts = Layouts([kind], root)
    byId = {entry["id"]: entry for entry in layouts}
    kept = SettingsManager.Get(key, default)
    if isinstance(kept, str) and kept in byId:
        return byId[kept]
    if default in byId:
        return byId[default]
    return layouts[0] if layouts else None


def SelectLayout(kind, layoutId, root=None):
    """Keep ``layoutId`` as the selected layout of ``kind`` (a
    SELECTION_SETTINGS kind) and return its entry; None, keeping nothing,
    when the catalog does not list it for that kind. The settings.json key
    and its value (a layout id) are the ones the panels always kept."""
    key, _default = SELECTION_SETTINGS[kind]
    entry = next((entry for entry in Layouts([kind], root)
                  if entry["id"] == layoutId), None)
    if entry is not None:
        SettingsManager.Set(key, layoutId)
    return entry


def CustomLayouts(customRoot):
    """Designer projects below ``customRoot`` (``layout/custom``).

    A project needs a real directory (symlinks and junctions are skipped)
    holding both the entry ``index.html`` and a ``project.json`` that parses
    to an object. Anything else -- a folder still being written, a stray
    file, the ``_legacy/`` rollback copy inside a project -- is not listed.

    ``displayName`` here is the project's own name (or its slug), which is
    what the Designer's "open" list shows. The catalog (Discover / Layouts)
    lists the same entry as ``"Custom: " + name``, made unique per kind.
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


def WatchDirs(root=None):
    """The directories to watch for catalog changes (see Catalog.watchDirs).

    Cheap on purpose (directory listing only, no file reads): the Board tab
    calls it on every change notification to start watching a project
    folder the moment it is created, before its files are written.
    """
    base = _Base(root)
    customBase = os.path.join(base, CUSTOM_DIRNAME)
    dirs = [path for path in (base, customBase) if os.path.isdir(path)]
    if customBase in dirs:
        try:
            for slug in sorted(os.listdir(customBase), key=str.casefold):
                directory = os.path.join(customBase, slug)
                if os.path.isdir(directory):
                    dirs.append(directory)
        except OSError as e:
            logger.warning(f"LayoutCatalog: cannot list {customBase}: {e}")
    return dirs


# ── internals ────────────────────────────────────────────────────────────
def _Base(root):
    return os.path.abspath(root or LAYOUT_ROOT)


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

    Per kind because each kind is its own pulldown (Board lists scoreboard
    and designer together, but every designer name carries the prefix):
    the shipped commentators/ and bracket_top8/ are both "標準" and never
    appear in the same list, so neither is renamed.
    """
    counts = Counter(
        (entry["kind"], entry["displayName"]) for entry in layouts)
    return [
        dict(entry, displayName=f"{entry['displayName']} ({entry['id']})")
        if counts[(entry["kind"], entry["displayName"])] > 1 else entry
        for entry in layouts
    ]


def _BuiltinLayouts(base):
    try:
        names = sorted(os.listdir(base))
    except OSError as e:
        logger.warning(f"LayoutCatalog: cannot list {base}: {e}")
        return []

    layouts = []
    for name in names:
        if name in _RESERVED_DIRS:
            continue
        directory = os.path.join(base, name)
        if not os.path.isdir(directory):
            continue
        entry = _BuiltinEntry(name, directory)
        if entry is not None:
            layouts.append(entry)
    return layouts


def _BuiltinEntry(name, directory):
    block = _MetadataBlock(name, directory)
    inferred = _InferFromName(name)
    if block is None and inferred is None:
        return None
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
        entry = DEFAULT_ENTRY
    if not os.path.isfile(os.path.join(directory, entry)):
        return None
    return {
        "id": name,
        "kind": block["kind"],
        "displayName": _Text(block.get("displayName")),
        "format": _Text(block.get("format")),
        "game": _Text(block.get("game")),
        "entry": entry,
    }


def _MetadataBlock(name, directory):
    """The folder's autona block, or None when it has none (or a broken one,
    which is logged and then treated as absent)."""
    path = os.path.join(directory, SETTINGS_FILE)
    if not os.path.isfile(path):
        return None
    settings = _ReadJson(path)
    if not isinstance(settings, dict) or METADATA_KEY not in settings:
        return None
    block = settings[METADATA_KEY]
    if not isinstance(block, dict) or block.get("kind") not in _BUILTIN_KINDS:
        logger.warning(
            f"LayoutCatalog: {name}/{SETTINGS_FILE}: ignoring the "
            f"{METADATA_KEY!r} block (kind must be one of {_BUILTIN_KINDS})")
        return None
    return block


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
    # One bad file costs only its own entry, never the whole list: whatever
    # reading or parsing it raises -- OSError (locked mid-save), a decode
    # error, RecursionError on absurdly deep nesting -- is logged and the file
    # is treated as unreadable.
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(
            f"LayoutCatalog: cannot read {path}: {type(e).__name__}: {e}")
        return None
