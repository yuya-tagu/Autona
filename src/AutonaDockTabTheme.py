"""ClickUp-style dock tab bar theme for Autona.

The QMainWindow in Autona tabifies its dock widgets, so Qt renders a
QTabBar at the top of the tab group. We want this tab bar to look like
ClickUp's sidebar: a continuous warm-tone gradient background with square
icons on top and a small English label below.

Two Qt mechanisms that would normally achieve this don't work here:

1. **Stylesheet (QSS) alone** — QSS does not support icon-above-text in a
   QTabBar. It can only place icon left of text.

2. **QProxyStyle via `widget.setStyle()`** — When the application has an
   active QSS (we set one globally via qdarktheme + our tokens), Qt wraps
   the style with `QStyleSheetStyle`, which handles `QTabBar::tab` painting
   directly. A widget-level proxy style never gets `drawControl` called
   for those elements, so no custom paint runs.

This module installs a single `QObject` event filter on `QApplication`
that intercepts `QPaintEvent` for every dock tab bar (any `QTabBar` that
is a descendant of a `QMainWindow` but not nested inside a `QTabWidget`)
and paints it ourselves with `QPainter`. Because we install on the
application, we receive paint events even for tab bars that Qt
recreates after dock rearrangement — there is nothing to re-attach per
widget.

Hit-testing, keyboard navigation, and mouse event handling are left to
QTabBar's default implementation; we only replace the drawing.
"""

from __future__ import annotations

from loguru import logger
from qtpy.QtCore import (
    QEvent,
    QObject,
    QRect,
    QSize,
    Qt,
)
from qtpy.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabBar,
    QTabWidget,
)


# ──────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────

# Untranslated English dock names (the keys passed to
# QApplication.translate("app", ...) when dock widgets are created) →
# short uppercase label shown under the tab icon.
TAB_SHORT_LABELS: dict[str, str] = {
    "Tournament Info": "EVENT",
    "Scoreboard": "SCORE",
    "Statistics": "STATS",
    "Thumbnail Settings": "THUMB",
    "Result Settings": "RESULT",
    "Player List": "PLAYERS",
    "Commentary": "COMMS",
    "Stage": "STAGE",
    "Ruleset": "RULES",
    "Bracket": "BRACKET",
    "Additional Notes": "NOTES",
}

# Warm-tone gradient sampled from the Autona app icon (crimson → orange).
GRADIENT_STOPS: list[tuple[float, str]] = [
    (0.0, "#6B1020"),
    (0.45, "#C41232"),
    (1.0, "#B37018"),
]

# Sidebar geometry (ClickUp-style vertical dock tab bar on the left).
# Sized tightly for a 20px icon + a small-caps label at 6pt bold.
ICON_SIZE = QSize(20, 20)
SIDEBAR_WIDTH = 52
SIDEBAR_TAB_HEIGHT = 48

LABEL_FONT_PT = 6
LABEL_GAP = 3

# Corner radius on the gradient area. The gradient spans the full tab
# bar extent along the main axis (window-width dependent, not tab-count
# dependent) and matches the pills' cross-axis extent so heights align.
BG_RADIUS = 8
BG_EDGE_MARGIN = 6   # gap between tab bar edge and the gradient rectangle

# Amount by which painted tab cells (pill + icon + label) are shifted along
# the stack axis so the first tab sits INSIDE the gradient with visible
# breathing room between the gradient edge and the first pill. Qt's
# hit-testing uses the original (unshifted) rects, so normal clicks on
# the visible pill still land in the correct cell.
TAB_STACK_OFFSET = 12

# Inset + radius for the rounded "pill" highlighting the selected/hover tab.
TAB_INNER_MARGIN_X = 4
TAB_INNER_MARGIN_Y = 3
TAB_PILL_RADIUS = 6

TEXT_COLOR = QColor("#F0EAE0")
TEXT_COLOR_SELECTED = QColor("#FFFFFF")
SELECTED_OVERLAY = QColor(255, 255, 255, 60)
HOVER_OVERLAY = QColor(255, 255, 255, 30)


# ──────────────────────────────────────────────────────────
# Event filter
# ──────────────────────────────────────────────────────────


class AutonaDockTabPainter(QObject):
    """Application-wide event filter that paints dock tab bars in the
    ClickUp-inspired style described at the top of this module."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Cache: translated tab text → short uppercase label.
        self._label_cache: dict[str, str] = {}
        # Tab bars we have already configured (icon size, min height, etc.).
        # We keep a set of id()s so we don't hold strong refs that prevent GC.
        self._configured_ids: set[int] = set()
        self._gradient_colors = [
            (stop, QColor(hex_color)) for stop, hex_color in GRADIENT_STOPS
        ]

    # ── Public API ─────────────────────────────────────────

    def install(self) -> None:
        """Install this filter on the QApplication instance."""
        app = QApplication.instance()
        if app is None:
            logger.warning("AutonaDockTabPainter.install: no QApplication")
            return
        app.installEventFilter(self)
        logger.info("[DockTab] Application-wide event filter installed")

    # ── Qt event filter hook ───────────────────────────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._is_dock_tab_bar(obj):
            return False

        etype = event.type()
        if etype == QEvent.Type.Show:
            self._configure_tab_bar(obj)
        elif etype == QEvent.Type.Paint:
            self._paint(obj)
            return True
        return False

    # ── Helpers ────────────────────────────────────────────

    @staticmethod
    def _is_dock_tab_bar(obj: QObject) -> bool:
        """A tab bar qualifies if it's a QTabBar whose ancestor is a
        QMainWindow, but which is not nested inside a QTabWidget (that
        would be an ordinary in-widget tab bar, e.g. the scoreboard's)."""
        if not isinstance(obj, QTabBar):
            return False
        parent = obj.parentWidget()
        while parent is not None:
            if isinstance(parent, QTabWidget):
                return False
            if isinstance(parent, QMainWindow):
                return True
            parent = parent.parentWidget()
        return False

    def _configure_tab_bar(self, tab_bar: QTabBar) -> None:
        key = id(tab_bar)
        if key not in self._configured_ids:
            self._configured_ids.add(key)
            tab_bar.setObjectName("autonaDockTabBar")
            tab_bar.setIconSize(ICON_SIZE)
            tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
            tab_bar.setExpanding(False)
            tab_bar.setMovable(False)
            tab_bar.setTabsClosable(False)
            # The sidebar is vertical (shape == RoundedWest when the dock
            # area uses TabPosition.West). Constrain the tab bar's width
            # instead of height, and give each tab a fixed height.
            tab_bar.setUsesScrollButtons(False)
            # Force a uniform tab cell size via widget-local QSS. Our
            # Paint-event override bypasses QSS drawing, so these rules
            # only affect Qt's geometry calculation, not rendering.
            # (QSS `padding-left` on QTabBar does not reliably shift the
            # internal tab cells in QMainWindow's private dock tab bar, so
            # we shift visually in paint via TAB_STACK_OFFSET instead.)
            tab_bar.setStyleSheet(
                f"QTabBar::tab {{ "
                f"min-height: {SIDEBAR_TAB_HEIGHT}px; "
                f"max-height: {SIDEBAR_TAB_HEIGHT}px; "
                f"min-width: {SIDEBAR_WIDTH}px; "
                f"max-width: {SIDEBAR_WIDTH}px; "
                f"padding: 0; margin: 0; }}"
            )
            if self._is_vertical(tab_bar):
                tab_bar.setFixedWidth(SIDEBAR_WIDTH)
            else:
                tab_bar.setFixedHeight(SIDEBAR_TAB_HEIGHT)
            tab_bar.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Replace each tab's text with its short English label every time
        # (new tabs may have been tabified since last Show).
        self._shorten_tab_texts(tab_bar)
        tab_bar.update()

    @staticmethod
    def _is_vertical(tab_bar: QTabBar) -> bool:
        shape = tab_bar.shape()
        return shape in (
            QTabBar.Shape.RoundedWest,
            QTabBar.Shape.RoundedEast,
            QTabBar.Shape.TriangularWest,
            QTabBar.Shape.TriangularEast,
        )

    def _shorten_tab_texts(self, tab_bar: QTabBar) -> None:
        for i in range(tab_bar.count()):
            original = tab_bar.tabText(i)
            short = self._short_label_for(original)
            if short and short != original:
                # Cache under the short label too so post-rename paints still
                # find the right value.
                self._label_cache[short] = short
                tab_bar.setTabText(i, short)

    def _short_label_for(self, tab_text: str) -> str:
        if tab_text in self._label_cache:
            return self._label_cache[tab_text]
        for en_key, short in TAB_SHORT_LABELS.items():
            if QApplication.translate("app", en_key) == tab_text:
                self._label_cache[tab_text] = short
                return short
        fallback = tab_text[:6].upper()
        self._label_cache[tab_text] = fallback
        return fallback

    @staticmethod
    def _hovered_index(tab_bar: QTabBar) -> int:
        if not tab_bar.underMouse():
            return -1
        pos = tab_bar.mapFromGlobal(QCursor.pos())
        for i in range(tab_bar.count()):
            if tab_bar.tabRect(i).contains(pos):
                return i
        return -1

    # ── Painting ───────────────────────────────────────────

    def _paint(self, tab_bar: QTabBar) -> None:
        painter = QPainter(tab_bar)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            bg_path = self._paint_background(painter, tab_bar)
            current = tab_bar.currentIndex()
            hovered = self._hovered_index(tab_bar)
            # Clip subsequent tab drawing (pills, icons, labels) to the
            # gradient's rounded-rect silhouette so highlights can't bleed
            # past the corners.
            if bg_path is not None:
                painter.setClipPath(bg_path)
            for i in range(tab_bar.count()):
                if not tab_bar.isTabVisible(i):
                    continue
                self._paint_tab(painter, tab_bar, i, current, hovered)
        finally:
            painter.end()

    def _paint_background(
        self, painter: QPainter, tab_bar: QTabBar
    ) -> QPainterPath | None:
        """Fill the gradient background and return the rounded-rect path
        used, so the caller can clip pills / icons / labels to stay inside
        the gradient's rounded corners."""
        union = self._tabs_bounding_rect(tab_bar)
        if union is None:
            return None
        bar = tab_bar.rect()
        # Along the tab-stack axis the gradient spans the full tab bar
        # (window-width dependent). Across the axis it hugs the tab cells
        # with the same inset as the selected pill so the heights match.
        if self._is_vertical(tab_bar):
            bg_rect = QRect(
                union.x() + TAB_INNER_MARGIN_X,
                bar.y() + BG_EDGE_MARGIN,
                max(0, union.width() - 2 * TAB_INNER_MARGIN_X),
                max(0, bar.height() - 2 * BG_EDGE_MARGIN),
            )
            grad = QLinearGradient(0, bg_rect.top(), 0, bg_rect.bottom())
        else:
            bg_rect = QRect(
                bar.x() + BG_EDGE_MARGIN,
                union.y() + TAB_INNER_MARGIN_Y,
                max(0, bar.width() - 2 * BG_EDGE_MARGIN),
                max(0, union.height() - 2 * TAB_INNER_MARGIN_Y),
            )
            grad = QLinearGradient(
                bg_rect.left(), 0, bg_rect.right(), 0)
        for stop, color in self._gradient_colors:
            grad.setColorAt(stop, color)
        path = QPainterPath()
        path.addRoundedRect(
            float(bg_rect.x()), float(bg_rect.y()),
            float(bg_rect.width()), float(bg_rect.height()),
            BG_RADIUS, BG_RADIUS,
        )
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawPath(path)
        painter.restore()
        return path

    @staticmethod
    def _tabs_bounding_rect(tab_bar: QTabBar):
        union = None
        for i in range(tab_bar.count()):
            if not tab_bar.isTabVisible(i):
                continue
            r = tab_bar.tabRect(i)
            if not r.isValid():
                continue
            union = r if union is None else union.united(r)
        return union

    def _paint_tab(
        self,
        painter: QPainter,
        tab_bar: QTabBar,
        index: int,
        current: int,
        hovered: int,
    ) -> None:
        raw = tab_bar.tabRect(index)
        if not raw.isValid():
            return
        # Shift every tab along the stack axis so the first tab doesn't
        # sit flush against the tab bar edge. Qt's click hit-testing uses
        # the original (unshifted) rects; normal clicks on the visible
        # pill still land inside the correct cell.
        if self._is_vertical(tab_bar):
            tab_rect = raw.translated(0, TAB_STACK_OFFSET)
        else:
            tab_rect = raw.translated(TAB_STACK_OFFSET, 0)
        selected = index == current
        is_hover = index == hovered and not selected

        icon_size = tab_bar.iconSize()
        if icon_size.isNull() or icon_size.width() < 1:
            icon_size = ICON_SIZE
        label_font = self._label_font(tab_bar)
        # Use the label font's *visible* cap height — the actual painted
        # glyph height — rather than the painter's default font metrics
        # (a bug that inflated the label box and drifted the whole block
        # upward). All labels are uppercase with no descenders, so the
        # tight bounding rect of "EVENT" is representative.
        cap_rect = QFontMetrics(label_font).tightBoundingRect("EVENT")
        label_height = cap_rect.height()

        # Vertically center the (icon + gap + label) block within tab_rect
        content_h = icon_size.height() + LABEL_GAP + label_height
        top = tab_rect.y() + max(0, (tab_rect.height() - content_h) // 2)
        icon_y = top
        label_y = top + icon_size.height() + LABEL_GAP

        self._paint_pill(painter, tab_rect, selected, is_hover)
        self._paint_icon(
            painter, tab_bar, index, tab_rect, icon_size, icon_y, selected)
        self._paint_label(
            painter, tab_bar, index, tab_rect,
            label_font, label_y, label_height, selected)

    @staticmethod
    def _paint_pill(
        painter: QPainter,
        tab_rect: QRect,
        selected: bool,
        is_hover: bool,
    ) -> None:
        if not (selected or is_hover):
            return
        inner = tab_rect.adjusted(
            TAB_INNER_MARGIN_X, TAB_INNER_MARGIN_Y,
            -TAB_INNER_MARGIN_X, -TAB_INNER_MARGIN_Y,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SELECTED_OVERLAY if selected else HOVER_OVERLAY)
        painter.drawRoundedRect(inner, TAB_PILL_RADIUS, TAB_PILL_RADIUS)
        painter.restore()

    @staticmethod
    def _label_font(tab_bar: QTabBar) -> QFont:
        font = QFont(tab_bar.font())
        font.setPointSize(LABEL_FONT_PT)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        return font

    @staticmethod
    def _paint_icon(
        painter: QPainter,
        tab_bar: QTabBar,
        index: int,
        tab_rect: QRect,
        icon_size: QSize,
        icon_y: int,
        selected: bool,
    ) -> None:
        icon = tab_bar.tabIcon(index)
        if icon.isNull():
            return
        icon_x = tab_rect.x() + (tab_rect.width() - icon_size.width()) // 2
        mode = QIcon.Mode.Selected if selected else QIcon.Mode.Normal
        state = QIcon.State.On if selected else QIcon.State.Off
        pix = icon.pixmap(icon_size, mode, state)
        painter.drawPixmap(icon_x, icon_y, pix)

    def _paint_label(
        self,
        painter: QPainter,
        tab_bar: QTabBar,
        index: int,
        tab_rect: QRect,
        font: QFont,
        label_y: int,
        label_height: int,
        selected: bool,
    ) -> None:
        text = self._short_label_for(tab_bar.tabText(index))
        painter.setFont(font)
        painter.setPen(TEXT_COLOR_SELECTED if selected else TEXT_COLOR)
        label_rect = QRect(
            tab_rect.x(),
            label_y,
            tab_rect.width(),
            label_height,
        )
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


# ──────────────────────────────────────────────────────────
# Module-level singleton + install helper
# ──────────────────────────────────────────────────────────


_singleton: AutonaDockTabPainter | None = None


def install_dock_tab_theme() -> AutonaDockTabPainter:
    """Install the ClickUp-style dock tab theme for the current application.
    Idempotent: safe to call multiple times."""
    global _singleton
    if _singleton is None:
        _singleton = AutonaDockTabPainter()
        _singleton.install()
    return _singleton
