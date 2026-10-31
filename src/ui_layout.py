"""
Layout primitives for the Autona desktop UI.

Centralises the visual contract for cards, section titles, form rows,
button hierarchy, two-column layouts, media previews, and the score
control panel. Built on top of `ui_constants` (color/spacing/font tokens)
— do not introduce hard-coded colors or sizes here; pull from `UI`.

Usage pattern:
  from . import ui_layout as L
  card = L.make_card(title="Tournament Info", child_layout=...)
  L.apply_section_title(label_widget)
  L.tag_button(btn, kind="primary")  # or "secondary" / "danger"

The functions either:
  - return a freshly constructed QWidget/QLayout, or
  - mutate an existing widget in-place (apply_*, tag_*).

Kept import-light: only qtpy + ui_constants. No side effects on import
beyond what `ui_constants` already triggers (palette/SettingsManager).
"""

from qtpy.QtWidgets import (
    QWidget, QLabel, QFrame, QGroupBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSizePolicy, QPushButton, QLayout, QLineEdit,
    QApplication,
)
from qtpy.QtCore import Qt, QObject, QEvent, QTimer, Signal, QMimeData
from qtpy.QtGui import QIcon, QDrag

from . import ui_constants as UI


# ─── Button Hierarchy ────────────────────────────────────────────
#
# Three roles:
#   primary   — the dominant call-to-action in a section (accent fill).
#               Limit to one per visible group to preserve hierarchy.
#   secondary — neutral default (bg_tertiary). The unset state of QPushButton.
#   danger    — destructive / clearing actions. Renders with brand color.
#
# Implemented via objectName so QSS in ui_constants picks them up. The
# existing rule for `QPushButton#primary` already exists — we add danger
# below.

BUTTON_PRIMARY = "primary"
BUTTON_SECONDARY = ""        # default — leave objectName untouched
BUTTON_DANGER = "danger"


def tag_button(button: QPushButton, kind: str) -> None:
    """Tag a button with one of BUTTON_PRIMARY / BUTTON_DANGER /
    BUTTON_SECONDARY. Re-polishes so the global QSS selector
    (`QPushButton#primary` / `#danger`) kicks in even when the widget
    is already on screen."""
    button.setObjectName(kind or "")
    s = button.style()
    s.unpolish(button)
    s.polish(button)


# ─── Card style ──────────────────────────────────────────────────
#
# A "card" is a QGroupBox with the title rendered as an uppercase label
# on the top-left, a subtle border, and consistent inner padding. The
# global QSS already paints QGroupBox this way; this helper just builds
# one with the right inner layout + content margins so callers don't have
# to remember the spacing tokens.

CARD_PADDING = (UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD)
CARD_INNER_SPACING = UI.SPACE_SM


def make_card(title: str, parent: QWidget | None = None) -> QGroupBox:
    """Create an empty card (QGroupBox styled per ui_constants global QSS).
    Add child widgets via card.layout().addWidget(...).

    The QGroupBox stylesheet in ui_constants already supplies the title
    treatment (uppercase, letter-spacing, accent_subtle bottom rule), so
    this helper only normalises the inner layout."""
    group = QGroupBox(title, parent)
    layout = QVBoxLayout(group)
    layout.setContentsMargins(*CARD_PADDING)
    layout.setSpacing(CARD_INNER_SPACING)
    return group


def apply_card_layout(group: QGroupBox) -> None:
    """Normalise an existing QGroupBox loaded from .ui to use the standard
    card padding and inner spacing. Use this for groups defined in .ui
    files where we cannot rebuild the widget tree.

    Safe to call on a group whose layout is None (does nothing) — .ui
    forms always populate a layout, so the no-op branch is defensive."""
    layout = group.layout()
    if layout is None:
        return
    layout.setContentsMargins(*CARD_PADDING)
    layout.setSpacing(CARD_INNER_SPACING)


# ─── Section title style ─────────────────────────────────────────
#
# Used INSIDE a card when the card needs sub-sections (e.g. "Display"
# / "Game Settings" within a single Thumbnail card). Smaller and quieter
# than the card title so the hierarchy stays readable.

def apply_section_title(label: QLabel) -> None:
    """Style a QLabel as a section sub-title. Reads as body text (same size,
    colour and margins as surrounding content) and is set apart only by its
    bold weight. Use only inside cards; the card itself already provides the
    top-level heading."""
    p = UI.palette()
    label.setStyleSheet(
        f"color: {p['text_primary']};"
        f"font-size: {UI.FONT_SM}pt;"
        f"font-weight: 700;"
        f"background: transparent;"
        f"border: none;"
        f"padding: 0;"
    )


def make_section_title(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    apply_section_title(label)
    return label


# ─── Settings-panel left pane ────────────────────────────────────
#
# The preview-style panels (Board / Thumbnail / Result / Top 8)
# share a two-pane layout: settings in a fixed-width left
# pane, the preview filling the right. One width so they line up when
# switching panels.

LEFT_PANE_WIDTH = 340   # px


# ─── Form row layout ─────────────────────────────────────────────
#
# A single label-on-left, control(s)-on-right row. The label gets a
# fixed width so labels across multiple rows align vertically. Put rows
# inside a card via card.layout().addLayout(form_row(...)).

FORM_LABEL_WIDTH = 110   # px — accommodates "Tournament Name" / "配信先" etc.
FORM_ROW_SPACING = UI.SPACE_SM


def form_row(label_text: str, *controls: QWidget,
             label_width: int = FORM_LABEL_WIDTH) -> QHBoxLayout:
    """Build a horizontal form row: [label | control(s)…].
    The first control stretches to fill remaining width; subsequent
    controls take their preferred size."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(FORM_ROW_SPACING)
    label = QLabel(label_text)
    label.setFixedWidth(label_width)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(label)
    for i, ctrl in enumerate(controls):
        layout.addWidget(ctrl, 1 if i == 0 else 0)
    return layout


def apply_form_grid(grid: QGridLayout,
                    horizontal_spacing: int = UI.SPACE_MD,
                    vertical_spacing: int = UI.SPACE_SM) -> None:
    """Normalise a QGridLayout (e.g. from a .ui file) to the standard
    form spacing. Call after loading the .ui."""
    grid.setHorizontalSpacing(horizontal_spacing)
    grid.setVerticalSpacing(vertical_spacing)
    grid.setContentsMargins(0, 0, 0, 0)


# ─── Two-column layout ───────────────────────────────────────────
#
# A top-level horizontal split where two cards (or arbitrary widgets)
# sit side by side with a fixed gutter. Used for EVENT (info | stream)
# and similar dual-pane sections. Stretch ratios let callers control
# which side dominates when the window resizes.

TWO_COL_GUTTER = UI.SPACE_MD


def two_column(left: QWidget, right: QWidget,
               left_stretch: int = 1, right_stretch: int = 1,
               parent: QWidget | None = None) -> QWidget:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(TWO_COL_GUTTER)
    layout.addWidget(left, left_stretch)
    layout.addWidget(right, right_stretch)
    return container


# ─── Media preview layout ────────────────────────────────────────
#
# Standard frame for icon / banner / thumbnail previews: a thin border,
# fixed/min size for the QLabel, and a control row below for
# Load / Download / Default buttons.

MEDIA_PREVIEW_BORDER_RADIUS = UI.RADIUS_SM


def apply_media_preview(label: QLabel,
                        min_width: int | None = None,
                        min_height: int | None = None) -> None:
    """Style a QLabel that displays an image preview with a subtle
    bordered frame so the bounds are visible even when the pixmap is
    transparent or smaller than the slot.

    Sizes are opt-in: pass min_width / min_height only when the source
    .ui doesn't already constrain the slot. Forcing a minimum on a
    label that .ui pinned via maximumSize would push min above max and
    let Qt resolve the conflict in favour of min — which is what
    happened to streamThumbPreview before this guard was added."""
    p = UI.palette()
    if min_width is not None or min_height is not None:
        label.setMinimumSize(min_width or 0, min_height or 0)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"background-color: {p['bg_tertiary']};"
        f"border: 1px solid {p['border_subtle']};"
        f"border-radius: {MEDIA_PREVIEW_BORDER_RADIUS}px;"
    )


def media_action_row(*buttons: QPushButton) -> QHBoxLayout:
    """Standard button row that sits beneath a media preview.
    Buttons are left-aligned with a small gap; the row pushes a stretch
    on the right so the buttons hug the preview."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(UI.SPACE_SM)
    for btn in buttons:
        row.addWidget(btn)
    row.addStretch(1)
    return row


# ─── Helpers: divider ────────────────────────────────────────────

def horizontal_divider(parent: QWidget | None = None) -> QFrame:
    """Standard 1px horizontal rule using border_subtle. Use between
    grouped sections that don't warrant their own card border."""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ─── Drag-reorderable card list ──────────────────────────────────
#
# Used by AutonaResultSettingsWidget (result player/team cards). A card is
# any QFrame added via DragCardList.addCard; a DragGrip placed inside the card starts the
# move drag. Each list should pass its own `mime` so drags cannot land
# on a foreign list.

class DragGrip(QLabel):
    """Drag handle (hamburger icon) for a card in a DragCardList.

    A press-and-drag that starts on this grip begins an internal move
    drag; the owning DragCardList performs the reorder on drop. Dragging
    is restricted to the grip so the card's input fields stay fully
    usable.
    """

    def __init__(self, card, card_list):
        super().__init__()
        self._card = card
        self._card_list = card_list
        self._press_pos = None
        self.setObjectName("cardGrip")
        self.setPixmap(QIcon(UI.icon_path("menu.svg")).pixmap(16, 16))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(QApplication.translate("app", "Drag to reorder"))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.pos()

    def mouseMoveEvent(self, e):
        if self._press_pos is None:
            return
        if (e.pos() - self._press_pos).manhattanLength() \
                < QApplication.startDragDistance():
            return
        idx = self._card_list.indexOf(self._card)
        self._press_pos = None
        if idx < 0:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._card_list.MIME, str(idx).encode("ascii"))
        drag.setMimeData(mime)
        drag.setPixmap(self._gripDragPixmap())
        drag.setHotSpot(self.mapTo(self._card, e.pos()))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        drag.exec(Qt.DropAction.MoveAction)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        self._press_pos = None

    def _gripDragPixmap(self):
        """Grab the card one step brighter (bg_quaternary) for the drag
        image.

        The `dragging` property drives a QSS variant; toggle it around
        grab() with a repolish so only the offscreen drag pixmap is
        brightened — the on-screen card is restored before any repaint
        reaches the screen.
        """
        card = self._card
        card.setProperty("dragging", True)
        card.style().unpolish(card)
        card.style().polish(card)
        pixmap = card.grab()
        card.setProperty("dragging", False)
        card.style().unpolish(card)
        card.style().polish(card)
        return pixmap


class DragCardList(QWidget):
    """Vertical, drag-reorderable list of cards.

    Cards are added top-to-bottom and dragged by their DragGrip. On drop
    the list reorders itself and emits orderChanged; the owning widget
    re-reads cards() for the new visual order. Only visible cards
    participate as drop targets, so hidden cards never receive a drop.
    """

    MIME = "application/x-autona-drag-card"
    orderChanged = Signal()

    def __init__(self, parent=None, mime=None):
        super().__init__(parent)
        if mime:
            self.MIME = mime
        self._cards = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(UI.SPACE_SM)
        self._layout.addStretch(1)
        self.setAcceptDrops(True)
        # 2px accent line shown at the drop position during a drag.
        self._indicator = QFrame(self)
        self._indicator.setFixedHeight(2)
        self._indicator.setStyleSheet(
            f"background-color: {UI.palette()['accent']}; border: none;")
        self._indicator.hide()

    def addCard(self, card):
        self._cards.append(card)
        # Insert before the trailing stretch (always the last layout item).
        self._layout.insertWidget(self._layout.count() - 1, card)

    def cards(self):
        return list(self._cards)

    def indexOf(self, card):
        try:
            return self._cards.index(card)
        except ValueError:
            return -1

    # -- drag & drop --
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self.MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(self.MIME):
            return
        self._showIndicator(self._insertIndex(self._eventY(e)))
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._indicator.hide()

    def dropEvent(self, e):
        self._indicator.hide()
        if not e.mimeData().hasFormat(self.MIME):
            return
        try:
            src = int(bytes(e.mimeData().data(self.MIME)).decode("ascii"))
        except (ValueError, TypeError):
            return
        dst = self._insertIndex(self._eventY(e))
        e.acceptProposedAction()
        # Defer the reorder: mutating the layout while Qt is still delivering
        # the drop event risks a re-entrant layout crash (agent-pitfalls #9).
        QTimer.singleShot(0, lambda: self._commitMove(src, dst))

    # -- helpers --
    def _eventY(self, e):
        # qtpy/Qt6 exposes position() (QPointF); fall back to pos() for Qt5.
        try:
            return int(e.position().y())
        except AttributeError:
            return e.pos().y()

    def _visibleCards(self):
        return [c for c in self._cards if c.isVisible()]

    def _insertIndex(self, y):
        """Insertion index (in _cards terms) from a y coordinate, using only
        visible cards' vertical midpoints."""
        for c in self._cards:
            if not c.isVisible():
                continue
            if y < c.y() + c.height() / 2:
                return self._cards.index(c)
        vis = self._visibleCards()
        return self._cards.index(vis[-1]) + 1 if vis else len(self._cards)

    def _showIndicator(self, ins):
        spacing = self._layout.spacing()
        if ins < len(self._cards) and self._cards[ins].isVisible():
            y = self._cards[ins].y() - spacing // 2
        else:
            vis = self._visibleCards()
            y = (vis[-1].y() + vis[-1].height() + spacing // 2) if vis else 0
        self._indicator.setGeometry(0, y, self.width(), 2)
        self._indicator.show()
        self._indicator.raise_()

    def _commitMove(self, src, dst):
        if src < 0 or src >= len(self._cards):
            return
        # Moving downward: removing src shifts later items up by one.
        if dst > src:
            dst -= 1
        dst = max(0, min(dst, len(self._cards) - 1))
        if dst == src:
            return
        card = self._cards.pop(src)
        self._cards.insert(dst, card)
        for c in self._cards:
            self._layout.removeWidget(c)
        for i, c in enumerate(self._cards):
            self._layout.insertWidget(i, c)
        self.orderChanged.emit()


# ─── Helpers: completer popup behavior ───────────────────────────

_POPUP_ON_FOCUS_PROP = "_completerPopupOnFocus"


class _CompleterPopupOnFocus(QObject):
    """Event filter that opens a QLineEdit's completer popup on focus-in
    and click — even while the box is empty — so the candidate list is
    browsable without typing. Also re-opens the popup with the full list
    when the user clears the text (Qt hides it once the prefix becomes
    empty). Parented to the edit; reads the completer lazily so it keeps
    working when the completer is replaced later (PlayerDB rebinds)."""

    def __init__(self, edit: QLineEdit):
        super().__init__(edit)
        self._edit = edit
        edit.installEventFilter(self)
        edit.textEdited.connect(self._onTextEdited)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            self._popup()
        elif t == QEvent.Type.FocusIn and event.reason() in (
                Qt.FocusReason.MouseFocusReason,
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason):
            # Deferred: the popup positions correctly only after focus
            # settles. Restricting the reasons keeps the popup from
            # re-opening when its own close returns focus to the edit.
            QTimer.singleShot(0, self._popup)
        return False

    def _onTextEdited(self, text):
        if not text:
            # Deferred so it runs after QLineEdit's own completer
            # handling, which hides the popup on an empty prefix.
            QTimer.singleShot(0, self._popup)

    def _popup(self):
        completer = self._edit.completer()
        if completer is None or not self._edit.hasFocus():
            return
        completer.setCompletionPrefix(self._edit.text())
        completer.complete()


def attach_popup_on_focus(edit: QLineEdit) -> None:
    """App-wide text box contract: a QLineEdit with a completer shows
    its candidates on focus/click even before any text is typed. Safe to
    call again on the same edit (no-op) — rebind sites re-run setup when
    their model changes."""
    if edit.property(_POPUP_ON_FOCUS_PROP):
        return
    edit.setProperty(_POPUP_ON_FOCUS_PROP, True)
    _CompleterPopupOnFocus(edit)
