"""No Qt object may be destroyed behind PySide6's back.

PySide6 keeps a binding manager keyed by C++ address: every Python wrapper it
has handed out is registered there, and the entry is cleared when the object
dies. For a QObject it learns about the death; for the non-QObject helper
types -- QLayoutItem, QWidgetItem, QSpacerItem -- it cannot, because there is
nothing to listen to. Destroy one of those while a Python wrapper for it
exists and the registration is left behind, still flagged valid, pointing at
freed memory.

That is not a leak, it is a landmine. The next C++ object allocated at the
same address gets looked up through the dead entry -- Qt calls the virtual
metaObject() from inside a QObject constructor, before Shiboken has
registered the new object -- and PySide reads a QMetaObject out of the stale
wrapper's type. PySide::SignalManager::retrieveMetaObject dereferences a null
TypeUserData and the process dies with an access violation, in whatever
widget happened to be under construction at the time.

That is the flaky suite crash this file exists to stop coming back:
AutonaScoreboardPlayerWidget._buildCompactDetailsUI strips the emptied detail
rows out of the player card's grid, and it used to destroy them
(`grid.takeAt(i)` + `sub_layout.deleteLater()`). Destroying a QLayout
destroys the QLayoutItems it holds -- including the ones __init__ had already
wrapped while zeroing the row margins and collapsing the seed row's spacers.
The crash then surfaced one or two player widgets later, inside the
QCompleter built by AutonaBasePlayerWidget.LoadCountries, in a different test
file, in roughly one run out of three.

The fix is to let PySide do the freeing. takeAt()/removeItem() make Python
the owner of the removed item; deleteLater() hands it back to Qt, and Qt's
destruction is the part Shiboken cannot see. Dropping the last Python
reference instead frees it through Shiboken, which invalidates the layout's
wrapper and the item wrappers registered under it.
"""
import ast
from pathlib import Path

from qtpy.QtWidgets import QHBoxLayout

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
PLAYER_WIDGET = SRC_ROOT / "AutonaScoreboardPlayerWidget.py"

# The rows _buildCompactDetailsUI empties into the details dialog and then
# takes out of the card's grid.
STRIPPED_ROWS = ("horizontalLayout", "identityRow", "locationRow")

# Calls that put the freeing back in Qt's hands (or shuffle ownership until
# it ends up there). removeItem() alone is the safe form: Python is left
# owning the layout and PySide frees it. itemAt() is banned here for the
# other half of the problem -- it wraps every QWidgetItem it walks past, and
# the caller re-homes those widgets a few lines later, which frees the items.
FORBIDDEN_CALLS = {"deleteLater", "setParent", "itemAt", "takeAt"}

# Handing out a wrapper for a QLayoutItem is only safe where the item is not
# going to be destroyed behind PySide's back. In this widget that is exactly
# the PlayerDB-editor path: the scoreboard card re-homes its fields into the
# details dialog and strips the emptied rows, destroying the items.
ITEM_WRAPPING_CALLS = {"itemAt", "takeAt", "spacerItem"}
EDITOR_ONLY_GUARD = "path.startswith('score.')"

# The one function allowed to walk items on the scoreboard path: it hands the
# wrapper straight to Shiboken.invalidate(), so nothing is left registered.
RELEASE_HELPER = "_releaseLayoutItemWrappers"


def _takeLayoutFromGrid_source():
    tree = ast.parse(PLAYER_WIDGET.read_text(encoding="utf-8"),
                     filename=str(PLAYER_WIDGET))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_takeLayoutFromGrid":
            return node
    raise AssertionError(
        "AutonaScoreboardPlayerWidget._takeLayoutFromGrid is gone; if the row "
        "stripping moved, move this guard with it")


def test_the_row_stripper_never_destroys_the_layout_it_removes():
    """The structural half: it is *who* destroys it that corrupts PySide.

    Static, because the damage is invisible at the call site and only kills
    the process later, in unrelated code -- there is no assertion to make at
    the moment the mistake is made.
    """
    offenders = sorted({
        node.func.attr
        for node in ast.walk(_takeLayoutFromGrid_source())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FORBIDDEN_CALLS
    })
    assert not offenders, (
        "_takeLayoutFromGrid calls " + ", ".join(f"{o}()" for o in offenders)
        + ". Destroying a row layout destroys its QLayoutItems, which are not "
          "QObjects, so PySide6 never learns their wrappers are dead and the "
          "process dies with an access violation the next time a QObject is "
          "allocated at one of their addresses. Use grid.removeItem() on its "
          "own and let PySide free the layout when the reference goes.")


def test_the_stripped_rows_are_freed_by_pyside_not_by_qt(autonaApp):
    """The behavioural half: the row is gone by the time __init__ returns.

    With the old code the layout was handed to deleteLater(), which under
    pytest (loop level 0, no exec()) is never reaped -- so the object was
    still findable here, and only died later when Qt tore the widget down,
    invisibly to Shiboken. Its absence now is the observable difference
    between "PySide freed it" and "Qt will free it behind Shiboken's back".
    """
    board = autonaApp.board(1)
    player = board.team1playerWidgets[0]

    for name in STRIPPED_ROWS:
        assert player.findChild(QHBoxLayout, name) is None, (
            f"{name} is still alive after _buildCompactDetailsUI stripped it. "
            "It has to be destroyed by PySide, while Python owns it: if it is "
            "left for Qt (deleteLater), the QLayoutItem wrappers registered "
            "under it stay in Shiboken's binding manager pointing at freed "
            "memory, and the process dies with an access violation the next "
            "time a QObject lands on one of those addresses.")


def _editorOnlyLineRanges(tree):
    """Line spans of the `if not self.path.startswith("score.")` branches."""
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        try:
            test = ast.unparse(node.test)
        except Exception:
            continue
        if EDITOR_ONLY_GUARD not in test:
            continue
        last = max((getattr(n, "lineno", node.lineno) for n in ast.walk(node)),
                   default=node.lineno)
        spans.append((node.lineno, last))
    return spans


def test_layout_items_are_only_wrapped_where_nothing_destroys_them():
    """itemAt()/spacerItem() hand out a wrapper PySide6 keeps but cannot track.

    The wrapper is registered as a Shiboken child of the layout, so it lives
    as long as the layout's own wrapper -- but QWidgetItem and QSpacerItem are
    not QObjects, so nothing tells PySide when Qt frees them. In this widget
    they are freed constantly: _buildCompactDetailsUI re-homes every detail
    field into the details dialog (which deletes the QWidgetItem that held it)
    and strips the emptied rows.

    Two places are allowed. The PlayerDB editor path keeps its rows intact, so
    wrapping items there is safe. And _releaseLayoutItemWrappers() has to walk
    them precisely so it can invalidate the wrappers before Qt frees the
    items -- it hands each one to Shiboken.invalidate() and never returns it.
    """
    source = PLAYER_WIDGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PLAYER_WIDGET))
    allowed = _editorOnlyLineRanges(tree)
    release = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == RELEASE_HELPER]
    assert release, (
        f"{RELEASE_HELPER}() is gone. _buildCompactDetailsUI frees the "
        "QWidgetItems and QSpacerItems of every row it re-homes or strips, "
        "and something has to clear PySide6's registrations for them first")
    allowed = allowed + [
        (n.lineno, max((getattr(c, "lineno", n.lineno) for c in ast.walk(n)),
                       default=n.lineno))
        for n in release]
    assert allowed, (
        "the PlayerDB-editor-only guard around the seed-row spacer walk is "
        "gone; that walk must not run on the scoreboard card, whose rows are "
        "stripped a few lines later")

    builder = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "_buildCompactDetailsUI"), None)
    assert builder is not None, "_buildCompactDetailsUI is gone"
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == RELEASE_HELPER for n in ast.walk(builder)), (
        f"_buildCompactDetailsUI no longer calls {RELEASE_HELPER}(). It frees "
        "the QWidgetItems and QSpacerItems of every row it re-homes or strips; "
        "without clearing PySide6's registrations first, the process dies with "
        "an access violation in an unrelated QObject construction later.")

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ITEM_WRAPPING_CALLS:
            continue
        if any(lo <= node.lineno <= hi for lo, hi in allowed):
            continue
        offenders.append(f"line {node.lineno}: .{node.func.attr}()")

    assert not offenders, (
        "QLayoutItem wrappers are handed out on the scoreboard path: "
        + ", ".join(offenders)
        + ". Those items are destroyed by the re-homing in "
          "_buildCompactDetailsUI, PySide6 never learns, and the stale "
          "registrations kill an unrelated QObject construction later. Reach "
          "sub-layouts through the QObject tree (findChildren(QLayout, "
          "FindDirectChildrenOnly)) and locate rows with indexOf().")
