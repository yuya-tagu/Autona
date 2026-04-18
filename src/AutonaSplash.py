"""RPG-style splash screen + post-show loading overlay for Autona.

Splash:
  * `AutonaSplashScreen` — a QSplashScreen subclass with a painted
    background (gradient + brass double frame + centered app icon + title)
    and a built-in dot animation for messages ending in ellipsis.
  * `create_splash()` — factory returning a ready-to-show splash.
  * `pick_splash_messages()` — returns a random sequence of 5 loading
    messages from the themed pools below, one message per init checkpoint.

Overlay:
  * `LoadingOverlay` — a child widget that covers the main window with
    a dim backdrop + rotating spinner, shown immediately after
    `QMainWindow.show()` so the user doesn't see the half-rendered UI
    settle during the first paint/layout passes. Call `.finish()` once
    the window is ready.
"""

from __future__ import annotations

import random

import time

from qtpy.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from qtpy.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)
from qtpy.QtCore import QEvent
from qtpy.QtWidgets import QApplication, QSplashScreen, QWidget

from . import ui_constants as UI


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

DOT_ANIMATION_INTERVAL_MS = 280
MAX_DOTS = 3
# How long to pump the Qt event loop after setting an animated message so
# the user actually sees the dots tick before Window.__init__ continues its
# (synchronous) heavy work and blocks the main thread again.
PUMP_DURATION_MS = 900

# Progress bar
PROGRESS_BAR_H = 6
PROGRESS_BAR_MARGIN_X = 60
PROGRESS_BAR_Y = 290
PROGRESS_BAR_RADIUS = 3
COLOR_PROGRESS_TRACK = QColor("#2A2014")
COLOR_PROGRESS_FILL_A = QColor("#C41232")
COLOR_PROGRESS_FILL_B = QColor("#F0C45A")


# ──────────────────────────────────────────────────────────
# Themed message pools (flavour text)
# ──────────────────────────────────────────────────────────

SPLASH_MESSAGE_PATTERNS: list[list[str]] = [
    # 大会運営スタッフ
    [
        "スタッフたちを起こしています...",
        "運営道具をあつめています...",
        "設営中...",
        "ミスが無いよう指差し確認...",
        "GO LIVE!",
    ],
    # 配信クルー
    [
        "配信クルーを招集中...",
        "ケーブルとマイクを接続中...",
        "カメラの位置調整中...",
        "音量チェック、問題なし...",
        "オンエア!",
    ],
    # 試合前プレイヤー
    [
        "選手入場前、控室で待機...",
        "コントローラーを握りしめる...",
        "スパーリングで指慣らし...",
        "深呼吸、気合い入れ直し...",
        "READY... FIGHT!",
    ],
    # 裏方演出家
    [
        "幕開け前、楽屋をのぞきに...",
        "小道具を舞台袖へ運搬中...",
        "照明と音響のリハ...",
        "開演 5 分前、立ち位置確認...",
        "開幕!!",
    ],
    # 冒険者ギルド
    [
        "ギルドの扉を開けます...",
        "装備品を整えています...",
        "作戦会議を開始...",
        "出発前、仲間と顔を見合わせ...",
        "いざ、出陣!",
    ],
]


def pick_splash_messages() -> list[str]:
    return list(random.choice(SPLASH_MESSAGE_PATTERNS))


# ──────────────────────────────────────────────────────────
# Splash screen implementation
# ──────────────────────────────────────────────────────────


class AutonaSplashScreen(QSplashScreen):
    """Splash with a painted RPG-style background and an animated
    trailing-dots indicator on messages that end in '...'."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._base_pixmap = _render_base_pixmap()
        super().__init__(parent, self._base_pixmap)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        self._message_base = ""
        self._animate = False
        self._dot_count = 1
        self._progress = 0.0
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(DOT_ANIMATION_INTERVAL_MS)
        self._animation_timer.timeout.connect(self._advance_dots)

    # ── Public API ─────────────────────────────────────────

    def set_message(self, text: str, progress: float | None = None) -> None:
        """Display `text`, optionally updating the progress bar to
        `progress` (0.0–1.0). If `text` ends with '...' the trailing dots
        animate from 1 to MAX_DOTS, cycling. Briefly pumps the event loop
        so the dots have time to visibly tick before the caller resumes
        its synchronous work."""
        if progress is not None:
            self._progress = max(0.0, min(1.0, float(progress)))
        if text.endswith("..."):
            self._message_base = text[: -MAX_DOTS]
            self._animate = True
            self._dot_count = 1
            if not self._animation_timer.isActive():
                self._animation_timer.start()
        else:
            self._message_base = text
            self._animate = False
            self._animation_timer.stop()
        self._render_message()
        QApplication.processEvents()
        if self._animate:
            self._pump_for(PUMP_DURATION_MS)

    def finish(self, main_window: QWidget) -> None:  # type: ignore[override]
        self._animation_timer.stop()
        super().finish(main_window)

    @staticmethod
    def _pump_for(duration_ms: int) -> None:
        """Pump the Qt event loop for `duration_ms` so QTimer-driven dot
        animations can tick visibly. Uses `processEvents` + a short sleep
        instead of a nested `QEventLoop.exec()`: qasync's asyncio event
        loop and Windows COM don't permit nested exec() from within an
        input-synchronous call (triggers RPC_E_CANTCALLOUT_ININPUTSYNCCALL
        0x8001010d)."""
        end = time.monotonic() + duration_ms / 1000.0
        while time.monotonic() < end:
            QApplication.processEvents()
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.02, remaining))

    # ── Internals ──────────────────────────────────────────

    def _advance_dots(self) -> None:
        self._dot_count = (self._dot_count % MAX_DOTS) + 1
        self._render_message()

    def _current_text(self) -> str:
        if self._animate:
            return self._message_base + ("." * self._dot_count)
        return self._message_base

    def _render_message(self) -> None:
        pix = QPixmap(self._base_pixmap)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            font = QFont(UI.FONT_FAMILY, 11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(COLOR_MSG)
            painter.drawText(
                0, SPLASH_H - 84, SPLASH_W, 32,
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignVCenter,
                f"» {self._current_text()} «",
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
        small_font = QFont(UI.FONT_FAMILY, 8)
        small_font.setBold(True)
        painter.setFont(small_font)
        painter.drawText(
            track_rect.x(), track_rect.bottom() + 4,
            track_rect.width(), 14,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{int(round(self._progress * 100))} %",
        )


# ──────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────


def create_splash() -> AutonaSplashScreen:
    splash = AutonaSplashScreen()
    splash.show()
    QApplication.processEvents()
    return splash


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
        icon = QPixmap(UI.icon_path("icon.png")).scaled(
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
    font = QFont(UI.FONT_FAMILY, 28)
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
    font = QFont(UI.FONT_FAMILY, 10)
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

    # ── Public API ─────────────────────────────────────────

    def start(self) -> None:
        self.show()
        self.raise_()
        self._timer.start()

    def finish(self) -> None:
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
