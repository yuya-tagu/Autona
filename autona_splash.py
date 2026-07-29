"""RPG-style splash screen + post-show loading overlay for Autona.

Lives at the project root, NOT under src/, and imports nothing from src.
main.py shows the splash before `from src import Autona`, and that import is
the whole application boot (QApplication use, logger, every singleton via
bootstrap_core()), which is exactly what the splash exists to cover.
For the same reason the Qt imports below are plain PySide6 rather than the
qtpy shim used everywhere else in the app: qtpy is only pulled in later, by
the application module.

Splash:
  * `AutonaSplashScreen` — a QSplashScreen subclass with a painted
    background (gradient + brass double frame + centered app icon + title)
    and a built-in dot animation for messages ending in ellipsis.
  * `create_splash()` — factory returning a ready-to-show splash, also
    published as `ACTIVE_SPLASH` for the later startup stages.
  * `active_splash()` — the splash created by main.py (or a no-op stand-in).
  * `pick_splash_messages()` — returns a random sequence of 5 loading
    messages from the themed pools below, one message per init checkpoint.

Startup instrumentation:
  * `set_startup_t0()` / `log_startup_phase()` / `attach_startup_logger()` —
    the "STARTUP phase=... elapsed=...s" markers. They live here because
    the earliest phases happen before loguru is configured (that happens
    while src/Autona.py is imported), so the marks are buffered and replayed
    once the real logger is attached.

Overlay:
  * `LoadingOverlay` — a child widget that covers the main window with
    a dim backdrop + rotating spinner, shown immediately after
    `QMainWindow.show()` so the user doesn't see the half-rendered UI
    settle during the first paint/layout passes. Call
    `.finish_when_ready()` to let it dismiss itself as soon as that first
    pass is done, or `.finish()` to drop it immediately.
"""

from __future__ import annotations

import random
import time

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget

# Standalone copies of the only two src/ui_constants.py tokens this module
# needs. Keep them in sync with ui_constants (FONT_FAMILY / icon_path).
FONT_FAMILY = "LINE Seed JP"


def _icon_path(name: str) -> str:
    return f"assets/icons/{name}"


# ──────────────────────────────────────────────────────────
# Layout constants
# ──────────────────────────────────────────────────────────

SPLASH_W, SPLASH_H = 520, 340

COLOR_TEXT = QColor("#F0C45A")        # parchment gold
COLOR_MSG = QColor("#E0D6B8")         # soft parchment
COLOR_BG_TOP = QColor("#1C1610")
COLOR_BG_BOTTOM = QColor("#0A0806")

# Outer decorative frame uses the Autona-icon crimson → orange → gold
# gradient. The inner 1px accent line is a darkened echo of the same
# ramp so the two frames read as one piece.
FRAME_GRADIENT_STOPS: list[tuple[float, str]] = [
    (0.0, "#6B1020"),
    (0.45, "#C41232"),
    (1.0, "#F0C45A"),
]
FRAME_INNER_GRADIENT_STOPS: list[tuple[float, str]] = [
    (0.0, "#3B0A12"),
    (0.45, "#6B0B1E"),
    (1.0, "#8B6F1C"),
]
FRAME_OUTER_W = 3
FRAME_INNER_W = 1
FRAME_OUTER_MARGIN = 4
FRAME_INNER_MARGIN = 12

  # (Previously the splash animated trailing "..." dots via a QTimer,
  # which required pumping the event loop for ~900ms after each message
  # just to let the dots visibly tick — ~3.6s of pure wait across 4
  # checkpoints. Dot animation has been removed; messages render as-is
  # and the progress bar tween alone provides visual motion.)

# Progress bar
PROGRESS_BAR_H = 6
PROGRESS_BAR_MARGIN_X = 60
PROGRESS_BAR_Y = 290
PROGRESS_BAR_RADIUS = 3
COLOR_PROGRESS_TRACK = QColor("#2A2014")
COLOR_PROGRESS_FILL_A = QColor("#C41232")
COLOR_PROGRESS_FILL_B = QColor("#F0C45A")
# Progress bar: set_message() snaps to the new value and returns
# immediately. We deliberately do not animate between checkpoints — the
# splash exists to mask a long startup, so blocking init to run a tween
# would be counterproductive. The caller fires many small checkpoints
# during init and the bar advances in short visible hops.


# ──────────────────────────────────────────────────────────
# Themed message pools (flavour text)
# ──────────────────────────────────────────────────────────

SPLASH_MESSAGE_PATTERNS: list[list[str]] = [
    # 大会運営スタッフ
    [
        "スタッフたちを起こしています...",
        "設営中...",
        "GO LIVE!",
    ],
    # 配信クルー
    [
        "配信クルーを招集中...",
        "カメラの位置調整中...",
        "オンエア!",
    ],
    # 試合前プレイヤー
    [
        "選手入場前、控室で待機...",
        "スパーリングで指慣らし...",
        "READY... FIGHT!",
    ],
    # 裏方演出家
    [
        "幕開け前、楽屋をのぞきに...",
        "照明と音響のリハ...",
        "開幕!!",
    ],
    # 冒険者ギルド
    [
        "ギルドの扉を開けます...",
        "作戦会議を開始...",
        "いざ、出陣!",
    ],
]


def pick_splash_messages() -> list[str]:
    return list(random.choice(SPLASH_MESSAGE_PATTERNS))


# ──────────────────────────────────────────────────────────
# Splash screen implementation
# ──────────────────────────────────────────────────────────


class AutonaSplashScreen(QSplashScreen):
    """Splash with a painted RPG-style background and a discrete progress
    bar. Each set_message() snaps the bar to its new value and returns
    immediately — no animation runs between checkpoints."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._base_pixmap = _render_base_pixmap()
        super().__init__(parent, self._base_pixmap)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        # Carry the app icon on the splash too (the first window shown), so
        # every window under our AppUserModelID has the Autona mark.
        self.setWindowIcon(QIcon(_icon_path("icon.ico")))

        self._message_base = ""
        self._progress = 0.0

    # ── Public API ─────────────────────────────────────────

    def set_message(self, text: str, progress: float | None = None) -> None:
        """Snap the bar to `progress` (0.0–1.0) and display `text`.
        Returns immediately; one short processEvents pass paints the
        new frame without blocking init."""
        self._message_base = text
        if progress is not None:
            self._progress = max(0.0, min(1.0, float(progress)))
        self._render_message()
        QApplication.processEvents()

    def finish(self, main_window: QWidget) -> None:  # type: ignore[override]
        super().finish(main_window)

    # ── Internals ──────────────────────────────────────────

    def _render_message(self) -> None:
        pix = QPixmap(self._base_pixmap)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            font = QFont(FONT_FAMILY, 11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(COLOR_MSG)
            painter.drawText(
                0, SPLASH_H - 84, SPLASH_W, 32,
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignVCenter,
                f"» {self._message_base} «",
            )
            self._paint_progress_bar(painter)
        finally:
            painter.end()
        self.setPixmap(pix)

    def _paint_progress_bar(self, painter: QPainter) -> None:
        track_rect = QRect(
            PROGRESS_BAR_MARGIN_X, PROGRESS_BAR_Y,
            SPLASH_W - 2 * PROGRESS_BAR_MARGIN_X, PROGRESS_BAR_H,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_PROGRESS_TRACK)
        painter.drawRoundedRect(
            track_rect, PROGRESS_BAR_RADIUS, PROGRESS_BAR_RADIUS)

        fill_w = int(track_rect.width() * self._progress)
        if fill_w > 0:
            fill_rect = QRect(
                track_rect.x(), track_rect.y(),
                fill_w, track_rect.height(),
            )
            grad = QLinearGradient(
                track_rect.left(), 0, track_rect.right(), 0)
            grad.setColorAt(0.0, COLOR_PROGRESS_FILL_A)
            grad.setColorAt(1.0, COLOR_PROGRESS_FILL_B)
            painter.setBrush(grad)
            painter.drawRoundedRect(
                fill_rect, PROGRESS_BAR_RADIUS, PROGRESS_BAR_RADIUS)

        # Percentage text right-aligned below the bar
        painter.setPen(QColor("#B5ADA0"))
        small_font = QFont(FONT_FAMILY, 8)
        small_font.setBold(True)
        painter.setFont(small_font)
        painter.drawText(
            track_rect.x(), track_rect.bottom() + 4,
            track_rect.width(), 14,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{int(round(self._progress * 100))} %",
        )


# ──────────────────────────────────────────────────────────
# Factory + process-wide splash handle
# ──────────────────────────────────────────────────────────

# The splash is created by main.py before the application module is imported
# and consumed much later by Window.__init__ (progress checkpoints +
# finish()). Rather than threading it through Window's signature, it is
# published here.
ACTIVE_SPLASH: AutonaSplashScreen | None = None


class _NoOpSplash:
    """Stand-in used when no splash was created (e.g. a Window built from
    a script or a test rather than through main.py). Keeps the startup
    path free of `if splash is not None` noise — the splash is cosmetic
    and must never be the reason a window fails to open."""

    def set_message(self, text: str, progress: float | None = None) -> None:
        pass

    def finish(self, main_window) -> None:
        pass


def create_splash() -> AutonaSplashScreen:
    global ACTIVE_SPLASH
    splash = AutonaSplashScreen()
    splash.show()
    QApplication.processEvents()
    ACTIVE_SPLASH = splash
    return splash


def active_splash():
    """The splash main.py created, or a no-op stand-in."""
    return ACTIVE_SPLASH if ACTIVE_SPLASH is not None else _NoOpSplash()


# ──────────────────────────────────────────────────────────
# Startup phase instrumentation
# ──────────────────────────────────────────────────────────

# Overwritten by main.py with the timestamp taken at the very top of the
# process; the default keeps the helpers usable on their own.
_STARTUP_T0: float = time.perf_counter()
# Marks emitted before loguru exists (it is configured while src/Autona.py
# is imported).
_PENDING_MARKS: list[str] = []
_STARTUP_LOGGER = None


def set_startup_t0(t0: float) -> None:
    global _STARTUP_T0
    _STARTUP_T0 = t0


def log_startup_phase(name: str) -> None:
    """Emit one `STARTUP phase=<name> elapsed=<s>s` marker."""
    line = f"STARTUP phase={name} elapsed={time.perf_counter() - _STARTUP_T0:.2f}s"
    if _STARTUP_LOGGER is not None:
        _STARTUP_LOGGER.info(line)
        return
    _PENDING_MARKS.append(line)
    # print() is a no-op when sys.stdout is None (windowed frozen build),
    # so this is safe there; the buffered marks reach the log file anyway
    # once attach_startup_logger() runs.
    print(line)


def attach_startup_logger(logger) -> None:
    """Hand the buffered marks to loguru and route later ones straight to it."""
    global _STARTUP_LOGGER
    _STARTUP_LOGGER = logger
    for line in _PENDING_MARKS:
        logger.info(line)
    _PENDING_MARKS.clear()


# ──────────────────────────────────────────────────────────
# Base pixmap rendering
# ──────────────────────────────────────────────────────────


def _render_base_pixmap() -> QPixmap:
    pix = QPixmap(SPLASH_W, SPLASH_H)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_background(painter)
        _paint_frame(painter)
        _paint_icon(painter)
        _paint_title(painter)
        _paint_subtitle(painter)
        _paint_divider(painter)
    finally:
        painter.end()
    return pix


def _paint_background(painter: QPainter) -> None:
    grad = QLinearGradient(0, 0, 0, SPLASH_H)
    grad.setColorAt(0.0, COLOR_BG_TOP)
    grad.setColorAt(1.0, COLOR_BG_BOTTOM)
    painter.fillRect(0, 0, SPLASH_W, SPLASH_H, grad)


def _build_frame_gradient(stops: list[tuple[float, str]]) -> QLinearGradient:
    grad = QLinearGradient(0, 0, SPLASH_W, 0)
    for stop, hex_color in stops:
        grad.setColorAt(stop, QColor(hex_color))
    return grad


def _paint_frame(painter: QPainter) -> None:
    # Outer and inner rectangular frames, each filled by a horizontal
    # gradient (crimson → orange → gold) that matches the Autona icon.
    outer_grad = _build_frame_gradient(FRAME_GRADIENT_STOPS)
    inner_grad = _build_frame_gradient(FRAME_INNER_GRADIENT_STOPS)

    outer_rect = QRect(
        FRAME_OUTER_MARGIN, FRAME_OUTER_MARGIN,
        SPLASH_W - 2 * FRAME_OUTER_MARGIN,
        SPLASH_H - 2 * FRAME_OUTER_MARGIN,
    )
    inner_rect = QRect(
        FRAME_INNER_MARGIN, FRAME_INNER_MARGIN,
        SPLASH_W - 2 * FRAME_INNER_MARGIN,
        SPLASH_H - 2 * FRAME_INNER_MARGIN,
    )
    _stroke_rect(painter, outer_rect, outer_grad, FRAME_OUTER_W)
    _stroke_rect(painter, inner_rect, inner_grad, FRAME_INNER_W)

    # Corner ornaments on the inner frame, colored by their x-position
    # along the same gradient.
    painter.setPen(Qt.PenStyle.NoPen)
    ornament_radius = 6
    corners = [
        (FRAME_INNER_MARGIN, FRAME_INNER_MARGIN),
        (SPLASH_W - FRAME_INNER_MARGIN, FRAME_INNER_MARGIN),
        (FRAME_INNER_MARGIN, SPLASH_H - FRAME_INNER_MARGIN),
        (SPLASH_W - FRAME_INNER_MARGIN, SPLASH_H - FRAME_INNER_MARGIN),
    ]
    for cx, cy in corners:
        painter.setBrush(_sample_gradient(cx, FRAME_GRADIENT_STOPS))
        diamond = QPolygon([
            QPoint(cx, cy - ornament_radius),
            QPoint(cx + ornament_radius, cy),
            QPoint(cx, cy + ornament_radius),
            QPoint(cx - ornament_radius, cy),
        ])
        painter.drawPolygon(diamond)


def _stroke_rect(
    painter: QPainter,
    rect: QRect,
    gradient: QLinearGradient,
    width: int,
) -> None:
    """Draw the four edges of `rect` as filled strips using `gradient`."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    # Top / bottom
    painter.drawRect(rect.x(), rect.y(), rect.width(), width)
    painter.drawRect(
        rect.x(), rect.bottom() - width + 1, rect.width(), width)
    # Left / right (skip corners already painted)
    painter.drawRect(rect.x(), rect.y(), width, rect.height())
    painter.drawRect(
        rect.right() - width + 1, rect.y(), width, rect.height())


def _sample_gradient(
    x: int, stops: list[tuple[float, str]],
) -> QColor:
    """Sample the horizontal gradient at absolute x coord (0..SPLASH_W)."""
    t = max(0.0, min(1.0, x / SPLASH_W))
    # Find the two stops `t` falls between and lerp.
    lo = stops[0]
    hi = stops[-1]
    for prev, curr in zip(stops, stops[1:]):
        if prev[0] <= t <= curr[0]:
            lo, hi = prev, curr
            break
    if hi[0] == lo[0]:
        return QColor(hi[1])
    ratio = (t - lo[0]) / (hi[0] - lo[0])
    lo_c = QColor(lo[1])
    hi_c = QColor(hi[1])
    return QColor(
        int(lo_c.red() + (hi_c.red() - lo_c.red()) * ratio),
        int(lo_c.green() + (hi_c.green() - lo_c.green()) * ratio),
        int(lo_c.blue() + (hi_c.blue() - lo_c.blue()) * ratio),
    )


def _paint_icon(painter: QPainter) -> None:
    try:
        icon = QPixmap(_icon_path("icon.png")).scaled(
            96, 96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        icon = QPixmap()
    if not icon.isNull():
        painter.drawPixmap((SPLASH_W - icon.width()) // 2, 48, icon)


def _paint_title(painter: QPainter) -> None:
    painter.setPen(COLOR_TEXT)
    font = QFont(FONT_FAMILY, 28)
    font.setBold(True)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
    painter.setFont(font)
    painter.drawText(
        QRect(0, 160, SPLASH_W, 48),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        "AUTONA",
    )


def _paint_subtitle(painter: QPainter) -> None:
    painter.setPen(QColor("#B5ADA0"))
    font = QFont(FONT_FAMILY, 10)
    painter.setFont(font)
    painter.drawText(
        QRect(0, 210, SPLASH_W, 20),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        "~ Tournament Stream Companion ~",
    )


def _paint_divider(painter: QPainter) -> None:
    painter.setPen(
        QPen(QColor("#6B0B1E"), 1, Qt.PenStyle.DashLine))
    painter.drawLine(60, 260, SPLASH_W - 60, 260)


# ──────────────────────────────────────────────────────────
# Post-show loading overlay
# ──────────────────────────────────────────────────────────

SPINNER_INTERVAL_MS = 40
SPINNER_BLADES = 12
SPINNER_BLADE_STEP = 360 // SPINNER_BLADES
SPINNER_INNER_R = 14
SPINNER_OUTER_R = 26
SPINNER_BLADE_W = 3
OVERLAY_BG = QColor(10, 8, 6, 235)
OVERLAY_BLADE_COLOR = QColor("#F0C45A")


class LoadingOverlay(QWidget):
    """Full-window child widget that paints a dim backdrop + rotating
    spinner. Used to hide the main window's first-paint jank while
    initial layout and lazy widgets settle.

    The overlay auto-resizes with its parent (it installs an event
    filter on the parent to catch Resize events). Call `finish()` to
    stop the animation and remove the widget.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Block clicks from reaching the main window until the overlay
        # is dismissed.
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(SPINNER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self.resize(parent.size())
        parent.installEventFilter(self)
        self._finished = False
        self._painted_at: float | None = None
        self._min_ms = 0

    # ── Public API ─────────────────────────────────────────

    def start(self) -> None:
        self.show()
        self.raise_()
        self._timer.start()

    def finish_when_ready(self, min_ms: int = 250, max_ms: int = 1500) -> None:
        """Dismiss once Qt has actually finished the first layout + paint
        pass, instead of after a fixed delay.

        The zero-delay timer fires after everything already queued (that
        first pass) has been dispatched, so it is a real "the window is
        drawn" signal rather than a guess. Note this must NOT be done with
        processEvents(): re-entering the event loop while widgets are still
        being built crashes at the C++ level (agent-pitfalls #9).

        `min_ms` is measured from the overlay's own first paint so the
        spinner never flashes; `max_ms` caps the wait for the case where
        deferred work keeps the queue busy indefinitely.
        """
        self._min_ms = min_ms
        QTimer.singleShot(0, self._finish_when_idle)
        QTimer.singleShot(max_ms, self.finish)

    def _finish_when_idle(self) -> None:
        if self._finished:
            return
        if self._painted_at is None:
            # Queue drained before the overlay itself was painted — retry on
            # the next frame rather than spinning on a 0 ms timer.
            QTimer.singleShot(SPINNER_INTERVAL_MS, self._finish_when_idle)
            return
        remaining = self._min_ms - int(
            (time.monotonic() - self._painted_at) * 1000)
        if remaining > 0:
            QTimer.singleShot(remaining, self.finish)
            return
        self.finish()

    def finish(self) -> None:
        # Idempotent: finish_when_ready() arms both an idle and a cap timer,
        # and the loser must not touch an already-deleted widget.
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        p = self.parentWidget()
        if p is not None:
            p.removeEventFilter(self)
        self.hide()
        self.deleteLater()

    # ── Qt overrides ───────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.resize(obj.size())
        return False

    def _tick(self) -> None:
        self._angle = (self._angle + SPINNER_BLADE_STEP) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        if self._painted_at is None:
            self._painted_at = time.monotonic()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillRect(self.rect(), OVERLAY_BG)
            painter.translate(self.width() // 2, self.height() // 2)
            painter.rotate(self._angle)
            for i in range(SPINNER_BLADES):
                alpha = int(255 * (i + 1) / SPINNER_BLADES)
                color = QColor(OVERLAY_BLADE_COLOR)
                color.setAlpha(alpha)
                pen = QPen(color, SPINNER_BLADE_W)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(
                    0, SPINNER_INNER_R, 0, SPINNER_OUTER_R)
                painter.rotate(SPINNER_BLADE_STEP)
        finally:
            painter.end()
