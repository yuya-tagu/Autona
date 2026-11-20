"""Startup splash + post-show loading overlay for Autona.

Lives in src/ but imports nothing from the rest of src (`src/__init__.py` is empty, so `from src import autona_splash` pulls in nothing heavy).
main.py starts the splash right after its single-instance gate, before
PySide6 is even imported: from there until the window is shown, the main
thread imports PySide6, builds the QApplication, imports the application
(`from src import Autona`: the logger, every singleton via bootstrap_core())
and builds the window, which is exactly what the splash exists to cover.

Importing this module starts nothing and imports no Qt (pitfalls #1):
main.py imports it before PySide6. The Qt half, `LoadingOverlay`, is defined
on first access (PEP 562 `__getattr__` at the bottom), which src/Autona.py
makes once PySide6 is loaded anyway.

Splash:
  * The main thread is blocked for the splash's whole life (imports, then
    Window.__init__, with no event loop running), so a Qt splash animated by
    a QTimer would freeze and read as a hang, and QtQuick's animators are not
    in the release (dependencies/autona.spec excludes QtQuick / QtQml). The
    splash is a native Win32 layered window owned by a daemon thread of its
    own, drawn with GDI+ through ctypes (no Qt): on a small dark rounded
    panel, the Autona icon with a diagonal band of light sweeping across it
    (lighting the icon only), then a pause, and "Loading..." below it, its
    dots counting with the cycle. No progress. The frames are rendered once,
    up front; the thread's loop only picks the frame for the time and hands
    it to UpdateLayeredWindow. Every Win32 / GDI+ call keeps the GIL (see
    _bind) but the wait between frames, so a busy main thread costs the
    setup nothing and a frame at most one GIL switch interval.
  * `create_splash()` — starts it and waits (at most SHOWN_WAIT_S) until it
    is on screen. Windows only; elsewhere, or on any failure, there is no
    splash: it is cosmetic and must never be the reason startup fails.
  * `active_splash()` — the splash main.py created, or a no-op stand-in.
  * `.finish(window)` — the window calls it right after its show(): the
    thread drops the window and is joined, for at most FINISH_JOIN_S.
  * `icon_box()` / `text_box()` / `band_center()` / `band_layers()` /
    `cycle_spec()` / `frame_table()` / `cycle_frame()` — the layout and the
    animation as pure functions (tests/test_splash.py).

Startup instrumentation:
  * `set_startup_t0()` / `log_startup_phase()` / `attach_startup_logger()` —
    the "STARTUP phase=... elapsed=...s" markers. They live here because
    the earliest phases happen before loguru is configured (that happens
    while src/Autona.py is imported), so the marks are buffered and replayed
    once the real logger is attached.

Overlay:
  * `LoadingOverlay` — a child widget that covers the main window with
    a dim backdrop + rotating spinner, shown immediately after
    `QMainWindow.show()` and kept up until the window's page is there
    (ADR-005 S-final SF-5b-3: the window is the shared web view alone, and
    has nothing to show before the web server is up and its first load has
    ended). `.call_when_painted(fn)` runs `fn` once the overlay has been
    painted, from the event loop, leaving it up (the window builds its web
    server there, which holds the GUI thread for a few seconds); a window
    never painted (started minimized) runs it past a longest wait anyway.
    `.finish()` drops it; the window calls it on the first load's end, on
    the web server's start failure, or past its own cap
    (Window.SPINNER_CAP_MS). tests/test_loading_overlay.py pins this.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import NamedTuple


def _icon_path(name: str) -> str:
    # Relative to the working directory, the install dir, as everywhere else
    # in the app (src/ui_constants.py icon_path).
    return f"assets/icons/{name}"


# ──────────────────────────────────────────────────────────
# Splash: look and motion
# ──────────────────────────────────────────────────────────

# Sizes in device-independent pixels, scaled by the monitor's DPI.
SPLASH_DIP = 220            # the panel is a square
SPLASH_RADIUS_DIP = 18
ICON_DIP = 96
TEXT_GAP_DIP = 16           # between the icon and the text
TEXT_DIP = 14               # the text's em size
TEXT_BOX_DIP = 22           # the line the text is centred in
LOADING_TEXT = "Loading..."
LOADING_DOTS = 3            # "Loading" .. "Loading..."

# The shine: a band of white light at 45 degrees ("/"), sweeping from the
# icon's top-left corner past its bottom-right one. Across the band its
# alpha rises and falls as a raised cosine, peaking at SHINE_ALPHA in the
# middle; BAND_HALF_WIDTH is the band's half-width as a share of the
# icon's diagonal.
SHINE_ALPHA = 0.55
BAND_HALF_WIDTH = 0.16
BAND_LAYERS = 10            # nested bands drawn to build that profile

# One frame per tick of the splash thread's 16 ms timer (62.5 fps): a frame
# grid finer or coarser than the ticks would show some frames twice. A
# cycle is the sweep, then a pause with no band; the dots count up once
# per cycle.
FRAME_INTERVAL_MS = 16
SWEEP_FRAMES = 44           # 0.70 s
PAUSE_FRAMES = 56           # 0.90 s
CYCLE_FRAMES = SWEEP_FRAMES + PAUSE_FRAMES
CYCLE_S = CYCLE_FRAMES * FRAME_INTERVAL_MS / 1000                  # 1.6 s
DOT_FRAMES = CYCLE_FRAMES // (LOADING_DOTS + 1)                    # 0.4 s

# ARGB, from src/ui_constants.py.
COLOR_PANEL = 0xFF23262C       # bg_tertiary
COLOR_PANEL_EDGE = 0xFF383C44  # border_default
COLOR_TEXT = 0xFFAFB3BB        # text_secondary
TEXT_FONT_FILE = "assets/font/LINESeedJP/LINESeedJP-Regular.ttf"
TEXT_FONT_FALLBACK = "Segoe UI"

SHOWN_WAIT_S = 1.0          # create_splash() waits this long at most
FINISH_JOIN_S = 0.5         # finish() waits this long at most


def _group_top() -> float:
    # The icon and the text below it, centred together in the panel: the
    # icon sits a little above the middle.
    group = ICON_DIP + TEXT_GAP_DIP + TEXT_BOX_DIP
    return (SPLASH_DIP - group) / 2


def icon_box(scale: float = 1.0) -> tuple[int, int, int]:
    """(x, y, size) of the icon in panel pixels: whole pixels, so the icon
    (scaled once to `size`) is drawn 1:1 and the shine lines up with it."""
    size = int(round(ICON_DIP * scale))
    x = int(round((SPLASH_DIP * scale - size) / 2))
    y = int(round(_group_top() * scale))
    return x, y, size


def text_box(scale: float = 1.0) -> tuple[float, float, float, float]:
    """(x, y, width, height) of the line the text is centred in."""
    top = _group_top() + ICON_DIP + TEXT_GAP_DIP
    return 0.0, top * scale, SPLASH_DIP * scale, TEXT_BOX_DIP * scale


def band_half_width(icon_px: float) -> float:
    """The band's half-width, along the icon's diagonal, in pixels."""
    return BAND_HALF_WIDTH * icon_px * math.sqrt(2)


def band_center(t: float, icon_px: float) -> float:
    """Where the band's middle is `t` (0 .. 1) into a sweep, measured along
    the icon's diagonal from its top-left corner (0) to its bottom-right one
    (icon_px * sqrt 2), in pixels. Gently eased in and out (half linear,
    half smoothstep: a full ease lingers where the icon's corners are
    empty); at 0 the band is wholly before the icon and at 1 wholly past
    it."""
    w = band_half_width(icon_px)
    eased = (t + t * t * (3.0 - 2.0 * t)) / 2.0
    return -w + eased * (icon_px * math.sqrt(2) + 2.0 * w)


def band_layer_alpha(layers: int = BAND_LAYERS,
                     peak: float = SHINE_ALPHA) -> float:
    """The alpha of each nested band: all of them together give `peak`."""
    return 1.0 - (1.0 - peak) ** (1.0 / layers)


def band_layers(half_width: float, layers: int = BAND_LAYERS,
                peak: float = SHINE_ALPHA) -> list[float]:
    """The half-widths of the nested bands, widest first. Each has the same
    alpha (band_layer_alpha), so the light at a distance d from the band's
    middle is that of the bands wider than d; the widths are where the
    raised cosine peak * cos^2(pi d / 2w) crosses each step's midpoint."""
    a = band_layer_alpha(layers, peak)
    widths = []
    for k in range(1, layers + 1):
        level = (2.0 - (1.0 - a) ** (k - 1) - (1.0 - a) ** k) / 2.0
        ratio = min(1.0, max(0.0, level / peak))
        widths.append(2.0 * half_width / math.pi * math.acos(math.sqrt(ratio)))
    return widths


class FrameSpec(NamedTuple):
    """What one frame shows: the sweep's progress (None: no band) and how
    many dots follow "Loading"."""
    sweep: float | None
    dots: int


def cycle_spec(m: int) -> FrameSpec:
    """The frame at tick `m` of a cycle (0 .. CYCLE_FRAMES - 1)."""
    m %= CYCLE_FRAMES
    sweep = m / (SWEEP_FRAMES - 1) if m < SWEEP_FRAMES else None
    return FrameSpec(sweep, min(LOADING_DOTS, m // DOT_FRAMES))


def frame_table() -> tuple[list[FrameSpec], list[int]]:
    """(the frames to render, which of them each tick of a cycle shows).
    The pause's ticks share a frame per dot count."""
    specs: list[FrameSpec] = []
    index: list[int] = []
    seen: dict[FrameSpec, int] = {}
    for m in range(CYCLE_FRAMES):
        spec = cycle_spec(m)
        if spec not in seen:
            seen[spec] = len(specs)
            specs.append(spec)
        index.append(seen[spec])
    return specs, index


def loading_text(dots: int) -> str:
    """"Loading" and `dots` dots (0 .. LOADING_DOTS)."""
    word = LOADING_TEXT[:len(LOADING_TEXT) - LOADING_DOTS]
    return word + "." * max(0, min(LOADING_DOTS, dots))


def cycle_frame(elapsed_s: float) -> int:
    """The tick of the cycle `elapsed_s` into the animation. Rounded to the
    nearest tick: the timer's ticks land on the grid plus the thread's
    wake-up latency, so a tick a little late still shows its own frame
    rather than the one before (which would show twice)."""
    return int(elapsed_s * 1000 / FRAME_INTERVAL_MS + 0.5) % CYCLE_FRAMES


# ──────────────────────────────────────────────────────────
# Splash: process-wide handle
# ──────────────────────────────────────────────────────────

# The splash is created by main.py before the application module is imported
# and finished much later by Window._BuildUi. Rather than threading it through
# Window's signature, it is published here.
ACTIVE_SPLASH = None


class _NoOpSplash:
    """Stand-in used when no splash is up (not Windows, the native splash
    failed, or a Window built from a script or a test rather than through
    main.py). Keeps the startup path free of `if splash is not None` noise:
    the splash is cosmetic and must never be the reason a window fails to
    open."""

    def finish(self, main_window=None) -> None:
        pass


def create_splash():
    """Start the splash (main.py, right after its single-instance gate) and
    publish it; a no-op stand-in where there is none."""
    global ACTIVE_SPLASH
    if sys.platform != "win32":
        return _NoOpSplash()
    try:
        splash = NativeSplash()
        if not splash.start():
            splash.finish()     # notes why
            return _NoOpSplash()
    except Exception as e:
        _note(f"splash: not shown ({type(e).__name__}: {e})")
        return _NoOpSplash()
    if not splash.shown:
        _note(f"splash: not up within {SHOWN_WAIT_S:.1f}s; startup goes on "
              f"(the splash shows once it is ready)")
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


def _note(line: str) -> None:
    """One line of the splash's own (failures, frame statistics), through
    the same buffer as the STARTUP marks. Main thread only."""
    if _STARTUP_LOGGER is not None:
        _STARTUP_LOGGER.info(line)
        return
    _PENDING_MARKS.append(line)
    print(line)


# ──────────────────────────────────────────────────────────
# Splash: the native window
# ──────────────────────────────────────────────────────────

class NativeSplash:
    """The shining icon in a Win32 layered window on a thread of its own.

    start() returns once the window is on screen, the thread gave up, or
    its wait ran out (the window then comes up when ready, unless finish()
    came first); finish() asks the thread to drop the window, joins it, and
    notes how it went (a failure included). Every Win32 /
    GDI+ object is created, used and released on the splash thread; the
    main thread only sets the stop flag and posts a wake-up message, under
    the lock that guards the window handle's lifetime.
    """

    def __init__(self, icon_png: str | None = None,
                 icon_ico: str | None = None) -> None:
        self._icon_png = icon_png or _icon_path("icon.png")
        self._icon_ico = icon_ico or _icon_path("icon.ico")
        self._stop = threading.Event()
        self._shown = threading.Event()   # on screen, or the thread gave up
        self._lock = threading.Lock()     # guards _hwnd against its destroy
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._finished = False
        # Written by the splash thread, read after it is joined.
        self.error: str | None = None
        self.setup_ms: float | None = None
        self.frame_count = 0              # frames rendered
        self.frames_shown = 0
        self.longest_gap_ms = 0.0
        self.shown_s = 0.0
        self.wait_error = 0               # the frame wait's, if it failed

    def start(self, wait_s: float | None = None) -> bool:
        """Start the thread and wait (SHOWN_WAIT_S unless told otherwise)
        until the window is on screen; past that, return anyway and let it
        come up when ready. False when it failed (`error` says why)."""
        self._thread = threading.Thread(
            target=self._run, name="AutonaSplash", daemon=True)
        self._thread.start()
        self._shown.wait(SHOWN_WAIT_S if wait_s is None else wait_s)
        return self.error is None

    @property
    def shown(self) -> bool:
        """The window has come up (it may have gone since)."""
        return self.setup_ms is not None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def finish(self, main_window=None) -> None:
        """Drop the splash (the window calls it right after its show()).
        Returns within FINISH_JOIN_S whatever the thread does. Idempotent."""
        if self._finished:
            return
        self._finished = True
        self._stop.set()
        with self._lock:
            if self._hwnd:
                try:
                    _win32().post_message(self._hwnd, _WM_APP_STOP, 0, 0)
                except Exception:
                    pass   # the thread still sees the flag on its next tick
        if self._thread is not None:
            self._thread.join(FINISH_JOIN_S)
            if self._thread.is_alive():
                _note("splash: the thread did not stop in time")
            elif self.error is not None:
                # Also a failure after start() returned True (past its wait,
                # or in the frame loop): nothing else would report it.
                what = "failed" if self.shown else "not shown"
                _note(f"splash: {what} ({self.error})")
            elif self.shown:
                waits = (f", the frame wait failed (error {self.wait_error}),"
                         f" slept instead" if self.wait_error else "")
                _note(f"splash: {self.frames_shown} frames over "
                      f"{self.shown_s:.1f}s, longest tick gap "
                      f"{self.longest_gap_ms:.0f} ms, {self.frame_count} "
                      f"frames, up in {self.setup_ms:.0f} ms{waits}")

    # ── The splash thread ─────────────────────────────────

    def _run(self) -> None:
        try:
            _SplashWindow(self).run()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._hwnd = None
            self._shown.set()


_WM_APP_STOP = 0x8000 + 1   # WM_APP + 1


class _SplashWindow:
    """One run of the splash thread: everything it creates, released in
    reverse order however the run ends."""

    def __init__(self, owner: NativeSplash) -> None:
        self.owner = owner
        self.api = _win32()
        self.cleanups: list = []

    def run(self) -> None:
        try:
            self._run()
        finally:
            for cleanup in reversed(self.cleanups):
                try:
                    cleanup()
                except Exception:
                    pass

    def _run(self) -> None:
        api, owner = self.api, self.owner
        started = time.perf_counter()
        # Per monitor (v2) for this thread only: Qt sets the process's
        # awareness later, and the process-wide API would fight it.
        aware = api.set_thread_dpi_awareness_per_monitor()
        x, y, size, dpi = api.placement(SPLASH_DIP, aware)
        scale = dpi / 96

        token = api.gdiplus_startup()
        self.cleanups.append(lambda: api.gdiplus_shutdown(token))
        icon = api.gdip_load_image(owner._icon_png)
        self.cleanups.append(lambda: api.gdip_dispose_image(icon))

        mem_dc = api.create_memory_dc()
        self.cleanups.append(lambda: api.delete_dc(mem_dc))
        frames = []

        def release_frames():
            for bitmap in frames:
                api.delete_object(bitmap)
        self.cleanups.append(release_frames)
        painter = _FramePainter(api, size, scale, icon)
        self.cleanups.append(painter.close)
        specs, index = frame_table()
        for spec in specs:
            if owner._stop.is_set():
                return
            frames.append(painter.render(mem_dc, spec))
        owner.frame_count = len(frames)
        default_bitmap = api.select_object(mem_dc, frames[index[0]])
        # Back in before the frames go: a bitmap selected into a DC cannot
        # be deleted.
        self.cleanups.append(
            lambda: api.select_object(mem_dc, default_bitmap))

        icons = api.load_icons(owner._icon_ico, dpi)
        self.cleanups.append(lambda: [api.destroy_icon(h) for h in icons
                                      if h])
        api.register_class()
        self.cleanups.append(api.unregister_class)
        # Neither created nor shown once finish() has been asked, checked
        # under the lock finish() posts under: a finish() whose join timed
        # out (setup still running) must not see the splash come up over
        # the main window afterwards.
        with owner._lock:
            if owner._stop.is_set():
                return
            hwnd = api.create_window(x, y, size)
            owner._hwnd = hwnd

        def destroy():
            with owner._lock:
                owner._hwnd = None
            api.destroy_window(hwnd)
        self.cleanups.append(destroy)
        api.set_icons(hwnd, icons)
        if not api.update_layered(hwnd, mem_dc, size, (x, y)):
            raise OSError("UpdateLayeredWindow failed")
        with owner._lock:
            if owner._stop.is_set():
                return
            api.show_no_activate(hwnd)
        owner.setup_ms = (time.perf_counter() - started) * 1000
        owner._shown.set()
        self._loop(api.frame_presenter(hwnd, mem_dc, size), frames, index)

    def _loop(self, present, frames, index) -> None:
        api, owner = self.api, self.owner
        timer = api.create_frame_timer()
        if timer is not None:
            self.cleanups.append(lambda: api.close_handle(timer.value))
        msg = api.wt.MSG()
        t0 = time.perf_counter()
        last_frame = index[0]   # on screen already
        last_tick = t0
        while not owner._stop.is_set():
            if not owner.wait_error:
                owner.wait_error = api.wait_for_tick(timer)
            if owner.wait_error:
                # The wait failed (it returns at once then): never a busy
                # loop. Sleep the tick from then on; finish() notes it.
                time.sleep(FRAME_INTERVAL_MS / 1000)
            if (not api.pump_messages(msg, _WM_APP_STOP)
                    or owner._stop.is_set()):
                break
            now = time.perf_counter()
            # Between two ticks handled, not two frames shown: the pause
            # holds a frame on purpose; this is what a busy main thread
            # holding the GIL costs the animation.
            owner.longest_gap_ms = max(owner.longest_gap_ms,
                                       (now - last_tick) * 1000)
            last_tick = now
            k = index[cycle_frame(now - t0)]
            if k == last_frame:
                continue
            present(frames[k])
            last_frame = k
            owner.frames_shown += 1
        owner.shown_s = time.perf_counter() - t0


# ──────────────────────────────────────────────────────────
# Splash: Win32 / GDI+ bindings (ctypes, built on first use)
# ──────────────────────────────────────────────────────────

_API = None


def _win32():
    """The bindings, built by the first splash thread (never at import)."""
    global _API
    if _API is None:
        _API = _Win32Api()
    return _API


class _Win32Api:
    """The handful of user32 / gdi32 / gdiplus / kernel32 calls the splash
    makes, on private DLL objects (so the argtypes set here never leak into
    another module's `ctypes.windll.user32`)."""

    # user32 / gdi32 constants
    WS_POPUP = 0x80000000
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_LAYERED = 0x00080000
    SW_SHOWNOACTIVATE = 4
    WM_CLOSE = 0x0010
    WM_QUIT = 0x0012
    WM_SETICON = 0x0080
    ICON_SMALL, ICON_BIG = 0, 1
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x10
    IDC_ARROW = 32512
    SM_CXICON, SM_CXSMICON = 11, 49
    MONITOR_DEFAULTTONEAREST = 2
    PM_REMOVE = 1
    QS_ALLINPUT = 0x04FF
    ULW_ALPHA = 2
    AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
    DIB_RGB_COLORS = 0
    ERROR_CLASS_ALREADY_EXISTS = 1410
    WAIT_FAILED = 0xFFFFFFFF
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x2
    TIMER_ALL_ACCESS = 0x1F0003
    # GDI+
    PixelFormat32bppPARGB = 0x000E200B
    SmoothingModeAntiAlias = 4
    InterpolationModeHighQualityBicubic = 7
    PixelOffsetModeHighQuality = 2
    UnitPixel = 2
    FillModeAlternate = 0
    WrapModeClamp = 4
    CombineModeReplace = 0
    MatrixOrderAppend = 1
    ColorAdjustTypeDefault = 0
    ColorMatrixFlagsDefault = 0
    FontStyleRegular = 0
    StringAlignmentCenter = 1
    StringFormatFlagsNoWrap = 0x1000
    TextRenderingHintAntiAlias = 4

    CLASS_NAME = "AutonaSplashWindow"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ctypes = ctypes
        self.wt = wt
        c_void_p, c_int, c_uint, c_float = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_float)
        LRESULT = ctypes.c_ssize_t

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                        ("bmiColors", wt.DWORD * 1)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", wt.BYTE), ("BlendFlags", wt.BYTE),
                        ("SourceConstantAlpha", wt.BYTE),
                        ("AlphaFormat", wt.BYTE)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        class GdiplusStartupInput(ctypes.Structure):
            _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                        ("DebugEventCallback", c_void_p),
                        ("SuppressBackgroundThread", wt.BOOL),
                        ("SuppressExternalCodecs", wt.BOOL)]
        self.GdiplusStartupInput = GdiplusStartupInput

        class PointF(ctypes.Structure):
            _fields_ = [("X", c_float), ("Y", c_float)]

        class RectF(ctypes.Structure):
            _fields_ = [("X", c_float), ("Y", c_float), ("Width", c_float),
                        ("Height", c_float)]

        class ColorMatrix(ctypes.Structure):
            _fields_ = [("m", c_float * 25)]     # REAL m[5][5], row-major
        self.PointF = PointF
        self.RectF = RectF
        self.ColorMatrix = ColorMatrix

        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT),
                        ("lpfnWndProc", WNDPROC), ("cbClsExtra", c_int),
                        ("cbWndExtra", c_int), ("hInstance", wt.HINSTANCE),
                        ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                        ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]

        self.BITMAPINFO = BITMAPINFO
        self.BLENDFUNCTION = BLENDFUNCTION
        self.MONITORINFO = MONITORINFO
        self.WNDCLASSEXW = WNDCLASSEXW

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32")
        kernel32 = ctypes.WinDLL("kernel32")
        gdiplus = ctypes.WinDLL("gdiplus")
        # Every call holds the GIL (_bind) but the one wait of the frame loop.
        b = _bind
        P = ctypes.POINTER
        self._GetLastError = b(kernel32, "GetLastError", wt.DWORD)

        # user32
        self._GetCursorPos = b(user32, "GetCursorPos", wt.BOOL, P(wt.POINT))
        self._MonitorFromPoint = b(user32, "MonitorFromPoint", c_void_p,
                                   wt.POINT, wt.DWORD)
        self._GetMonitorInfoW = b(user32, "GetMonitorInfoW", wt.BOOL,
                                  c_void_p, P(MONITORINFO))
        self._GetSystemMetrics = b(user32, "GetSystemMetrics", c_int, c_int)
        self._GetModuleHandleW = b(kernel32, "GetModuleHandleW", c_void_p,
                                   wt.LPCWSTR)
        self._LoadCursorW = b(user32, "LoadCursorW", c_void_p, c_void_p,
                              c_void_p)
        self._LoadImageW = b(user32, "LoadImageW", c_void_p, c_void_p,
                             wt.LPCWSTR, c_uint, c_int, c_int, c_uint)
        self._DestroyIcon = b(user32, "DestroyIcon", wt.BOOL, c_void_p)
        self._RegisterClassExW = b(user32, "RegisterClassExW", wt.ATOM,
                                   P(WNDCLASSEXW))
        self._UnregisterClassW = b(user32, "UnregisterClassW", wt.BOOL,
                                   wt.LPCWSTR, c_void_p)
        self._CreateWindowExW = b(
            user32, "CreateWindowExW", c_void_p, wt.DWORD, wt.LPCWSTR,
            wt.LPCWSTR, wt.DWORD, c_int, c_int, c_int, c_int, c_void_p,
            c_void_p, c_void_p, c_void_p)
        self._DestroyWindow = b(user32, "DestroyWindow", wt.BOOL, c_void_p)
        self._ShowWindow = b(user32, "ShowWindow", wt.BOOL, c_void_p, c_int)
        self._SendMessageW = b(user32, "SendMessageW", LRESULT, c_void_p,
                               wt.UINT, wt.WPARAM, wt.LPARAM)
        self._PostMessageW = b(user32, "PostMessageW", wt.BOOL, c_void_p,
                               wt.UINT, wt.WPARAM, wt.LPARAM)
        self._DefWindowProcW = b(user32, "DefWindowProcW", LRESULT,
                                 wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        self._UpdateLayeredWindow = b(
            user32, "UpdateLayeredWindow", wt.BOOL, c_void_p, c_void_p,
            P(wt.POINT), P(wt.SIZE), c_void_p, P(wt.POINT), wt.DWORD,
            P(BLENDFUNCTION), wt.DWORD)
        # The one call that releases the GIL: the wait between frames.
        self._MsgWaitForMultipleObjects = _bind_releasing_gil(
            user32, "MsgWaitForMultipleObjects", wt.DWORD, wt.DWORD,
            P(wt.HANDLE), wt.BOOL, wt.DWORD, wt.DWORD)
        try:
            self._SetThreadDpiAwarenessContext = b(
                user32, "SetThreadDpiAwarenessContext", ctypes.c_ssize_t,
                ctypes.c_ssize_t)
        except AttributeError:      # before Windows 10 1607
            self._SetThreadDpiAwarenessContext = None
        try:
            self._GetSystemMetricsForDpi = b(
                user32, "GetSystemMetricsForDpi", c_int, c_int, c_uint)
        except AttributeError:
            self._GetSystemMetricsForDpi = None
        try:
            shcore = ctypes.WinDLL("shcore")
            self._GetDpiForMonitor = b(shcore, "GetDpiForMonitor",
                                       ctypes.c_long, c_void_p, c_int,
                                       P(c_uint), P(c_uint))
        except (OSError, AttributeError):    # before Windows 8.1
            self._GetDpiForMonitor = None

        self._PeekMessageW = b(user32, "PeekMessageW", wt.BOOL, P(wt.MSG),
                               c_void_p, wt.UINT, wt.UINT, wt.UINT)
        self._TranslateMessage = b(user32, "TranslateMessage", wt.BOOL,
                                   P(wt.MSG))
        self._DispatchMessageW = b(user32, "DispatchMessageW", LRESULT,
                                   P(wt.MSG))
        self._SetWaitableTimer = b(
            kernel32, "SetWaitableTimer", wt.BOOL, wt.HANDLE,
            P(ctypes.c_longlong), wt.LONG, c_void_p, c_void_p, wt.BOOL)

        # gdi32
        self._GetDC = b(user32, "GetDC", c_void_p, c_void_p)
        self._ReleaseDC = b(user32, "ReleaseDC", c_int, c_void_p, c_void_p)
        self._CreateCompatibleDC = b(gdi32, "CreateCompatibleDC", c_void_p,
                                     c_void_p)
        self._DeleteDC = b(gdi32, "DeleteDC", wt.BOOL, c_void_p)
        self._CreateDIBSection = b(
            gdi32, "CreateDIBSection", c_void_p, c_void_p, P(BITMAPINFO),
            c_uint, P(c_void_p), c_void_p, wt.DWORD)
        self._SelectObject = b(gdi32, "SelectObject", c_void_p, c_void_p,
                               c_void_p)
        self._DeleteObject = b(gdi32, "DeleteObject", wt.BOOL, c_void_p)

        # kernel32
        self._CreateWaitableTimerExW = b(
            kernel32, "CreateWaitableTimerExW", c_void_p, c_void_p,
            wt.LPCWSTR, wt.DWORD, wt.DWORD)
        self._CloseHandle = b(kernel32, "CloseHandle", wt.BOOL, c_void_p)

        # gdiplus (every call returns a GpStatus, 0 = Ok)
        st = c_int
        self._GdiplusStartup = b(gdiplus, "GdiplusStartup", st,
                                 P(ctypes.c_size_t), P(GdiplusStartupInput),
                                 c_void_p)
        self._GdiplusShutdown = b(gdiplus, "GdiplusShutdown", None,
                                  ctypes.c_size_t)
        self._GdipCreateBitmapFromFile = b(
            gdiplus, "GdipCreateBitmapFromFile", st, wt.LPCWSTR,
            P(c_void_p))
        self._GdipCreateBitmapFromScan0 = b(
            gdiplus, "GdipCreateBitmapFromScan0", st, c_int, c_int, c_int,
            c_int, c_void_p, P(c_void_p))
        self._GdipDisposeImage = b(gdiplus, "GdipDisposeImage", st, c_void_p)
        self._GdipGetImageGraphicsContext = b(
            gdiplus, "GdipGetImageGraphicsContext", st, c_void_p,
            P(c_void_p))
        self._GdipDeleteGraphics = b(gdiplus, "GdipDeleteGraphics", st,
                                     c_void_p)
        self._GdipGraphicsClear = b(gdiplus, "GdipGraphicsClear", st,
                                    c_void_p, wt.DWORD)
        self._GdipSetSmoothingMode = b(gdiplus, "GdipSetSmoothingMode", st,
                                       c_void_p, c_int)
        self._GdipSetInterpolationMode = b(
            gdiplus, "GdipSetInterpolationMode", st, c_void_p, c_int)
        self._GdipSetPixelOffsetMode = b(gdiplus, "GdipSetPixelOffsetMode",
                                         st, c_void_p, c_int)
        self._GdipCreatePath = b(gdiplus, "GdipCreatePath", st, c_int,
                                 P(c_void_p))
        self._GdipAddPathArc = b(gdiplus, "GdipAddPathArc", st, c_void_p,
                                 c_float, c_float, c_float, c_float, c_float,
                                 c_float)
        self._GdipClosePathFigure = b(gdiplus, "GdipClosePathFigure", st,
                                      c_void_p)
        self._GdipDeletePath = b(gdiplus, "GdipDeletePath", st, c_void_p)
        self._GdipCreateSolidFill = b(gdiplus, "GdipCreateSolidFill", st,
                                      wt.DWORD, P(c_void_p))
        self._GdipSetSolidFillColor = b(gdiplus, "GdipSetSolidFillColor", st,
                                        c_void_p, wt.DWORD)
        self._GdipDeleteBrush = b(gdiplus, "GdipDeleteBrush", st, c_void_p)
        self._GdipCreatePen1 = b(gdiplus, "GdipCreatePen1", st, wt.DWORD,
                                 c_float, c_int, P(c_void_p))
        self._GdipDeletePen = b(gdiplus, "GdipDeletePen", st, c_void_p)
        self._GdipFillPath = b(gdiplus, "GdipFillPath", st, c_void_p,
                               c_void_p, c_void_p)
        self._GdipDrawPath = b(gdiplus, "GdipDrawPath", st, c_void_p,
                               c_void_p, c_void_p)
        self._GdipFillPolygon = b(gdiplus, "GdipFillPolygon", st, c_void_p,
                                  c_void_p, P(PointF), c_int, c_int)
        self._GdipSetClipRectI = b(gdiplus, "GdipSetClipRectI", st, c_void_p,
                                   c_int, c_int, c_int, c_int, c_int)
        self._GdipResetClip = b(gdiplus, "GdipResetClip", st, c_void_p)
        self._GdipDrawImageRect = b(gdiplus, "GdipDrawImageRect", st,
                                    c_void_p, c_void_p, c_float, c_float,
                                    c_float, c_float)
        # The shine: the icon made white, at the band's alpha, as a brush.
        self._GdipCreateImageAttributes = b(
            gdiplus, "GdipCreateImageAttributes", st, P(c_void_p))
        self._GdipSetImageAttributesColorMatrix = b(
            gdiplus, "GdipSetImageAttributesColorMatrix", st, c_void_p,
            c_int, wt.BOOL, P(ColorMatrix), c_void_p, c_int)
        self._GdipDisposeImageAttributes = b(
            gdiplus, "GdipDisposeImageAttributes", st, c_void_p)
        self._GdipCreateTextureIA = b(
            gdiplus, "GdipCreateTextureIA", st, c_void_p, c_void_p, c_float,
            c_float, c_float, c_float, P(c_void_p))
        self._GdipSetTextureWrapMode = b(gdiplus, "GdipSetTextureWrapMode",
                                         st, c_void_p, c_int)
        self._GdipTranslateTextureTransform = b(
            gdiplus, "GdipTranslateTextureTransform", st, c_void_p, c_float,
            c_float, c_int)
        # The text.
        self._GdipNewPrivateFontCollection = b(
            gdiplus, "GdipNewPrivateFontCollection", st, P(c_void_p))
        self._GdipDeletePrivateFontCollection = b(
            gdiplus, "GdipDeletePrivateFontCollection", st, P(c_void_p))
        self._GdipPrivateAddFontFile = b(gdiplus, "GdipPrivateAddFontFile",
                                         st, c_void_p, wt.LPCWSTR)
        self._GdipGetFontCollectionFamilyList = b(
            gdiplus, "GdipGetFontCollectionFamilyList", st, c_void_p, c_int,
            P(c_void_p), P(c_int))
        self._GdipCreateFontFamilyFromName = b(
            gdiplus, "GdipCreateFontFamilyFromName", st, wt.LPCWSTR,
            c_void_p, P(c_void_p))
        self._GdipDeleteFontFamily = b(gdiplus, "GdipDeleteFontFamily", st,
                                       c_void_p)
        self._GdipCreateFont = b(gdiplus, "GdipCreateFont", st, c_void_p,
                                 c_float, c_int, c_int, P(c_void_p))
        self._GdipDeleteFont = b(gdiplus, "GdipDeleteFont", st, c_void_p)
        self._GdipCreateStringFormat = b(gdiplus, "GdipCreateStringFormat",
                                         st, c_int, wt.WORD, P(c_void_p))
        self._GdipSetStringFormatLineAlign = b(
            gdiplus, "GdipSetStringFormatLineAlign", st, c_void_p, c_int)
        self._GdipDeleteStringFormat = b(gdiplus, "GdipDeleteStringFormat",
                                         st, c_void_p)
        self._GdipSetTextRenderingHint = b(
            gdiplus, "GdipSetTextRenderingHint", st, c_void_p, c_int)
        self._GdipMeasureString = b(
            gdiplus, "GdipMeasureString", st, c_void_p, wt.LPCWSTR, c_int,
            c_void_p, P(RectF), c_void_p, P(RectF), P(c_int), P(c_int))
        self._GdipDrawString = b(gdiplus, "GdipDrawString", st, c_void_p,
                                 wt.LPCWSTR, c_int, c_void_p, P(RectF),
                                 c_void_p, c_void_p)

        # The window procedure: stateless, so one for every splash, alive as
        # long as these bindings (the class may outlive a failed run).
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.WM_CLOSE:
                return 0    # closed by finish() only (the taskbar's Close)
            return self._DefWindowProcW(hwnd, msg, wparam, lparam)
        self._wndproc = WNDPROC(wndproc)

        self._blend = BLENDFUNCTION(self.AC_SRC_OVER, 0, 255,
                                    self.AC_SRC_ALPHA)

    # ── Placement and DPI ─────────────────────────────────

    def set_thread_dpi_awareness_per_monitor(self) -> bool:
        if self._SetThreadDpiAwarenessContext is None:
            return False
        return bool(self._SetThreadDpiAwarenessContext(
            self.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))

    def placement(self, size_dip: int, aware: bool):
        """(x, y, size, dpi): the panel centred on the work area of the
        monitor under the cursor, sized for that monitor's DPI (when this
        thread is per-monitor aware; otherwise 96 and Windows scales it)."""
        wt = self.wt
        pt = wt.POINT()
        if not self._GetCursorPos(self.ctypes.byref(pt)):
            pt = wt.POINT(0, 0)
        monitor = self._MonitorFromPoint(pt, self.MONITOR_DEFAULTTONEAREST)
        info = self.MONITORINFO()
        info.cbSize = self.ctypes.sizeof(info)
        if not monitor or not self._GetMonitorInfoW(
                monitor, self.ctypes.byref(info)):
            raise OSError("GetMonitorInfoW failed")
        dpi = 96
        if aware and self._GetDpiForMonitor is not None:
            dx, dy = self.ctypes.c_uint(), self.ctypes.c_uint()
            if self._GetDpiForMonitor(monitor, 0, self.ctypes.byref(dx),
                                      self.ctypes.byref(dy)) == 0 and dx.value:
                dpi = dx.value
        size = int(round(size_dip * dpi / 96))
        work = info.rcWork
        x = work.left + (work.right - work.left - size) // 2
        y = work.top + (work.bottom - work.top - size) // 2
        return x, y, size, dpi

    # ── GDI / GDI+ ────────────────────────────────────────

    def gdiplus_startup(self) -> int:
        token = self.ctypes.c_size_t()
        startup = self.GdiplusStartupInput(1, None, False, False)
        _gp(self._GdiplusStartup(self.ctypes.byref(token),
                                 self.ctypes.byref(startup), None),
            "GdiplusStartup")
        return token.value

    def gdiplus_shutdown(self, token: int) -> None:
        self._GdiplusShutdown(token)

    def gdip_load_image(self, path: str):
        image = self.ctypes.c_void_p()
        _gp(self._GdipCreateBitmapFromFile(path, self.ctypes.byref(image)),
            f"loading {path}")
        return image.value

    def gdip_dispose_image(self, image) -> None:
        self._GdipDisposeImage(image)

    def create_memory_dc(self):
        screen = self._GetDC(None)
        try:
            dc = self._CreateCompatibleDC(screen)
        finally:
            self._ReleaseDC(None, screen)
        if not dc:
            raise OSError("CreateCompatibleDC failed")
        return dc

    def delete_dc(self, dc) -> None:
        self._DeleteDC(dc)

    def select_object(self, dc, obj):
        return self._SelectObject(dc, obj)

    def delete_object(self, obj) -> None:
        self._DeleteObject(obj)

    def create_dib(self, dc, size: int):
        """A top-down 32-bit DIB section: (bitmap, its pixels)."""
        info = self.BITMAPINFO()
        header = info.bmiHeader
        header.biSize = self.ctypes.sizeof(header)
        header.biWidth = size
        header.biHeight = -size          # top-down, as GDI+ writes it
        header.biPlanes = 1
        header.biBitCount = 32
        bits = self.ctypes.c_void_p()
        bitmap = self._CreateDIBSection(dc, self.ctypes.byref(info),
                                        self.DIB_RGB_COLORS,
                                        self.ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise OSError("CreateDIBSection failed")
        return bitmap, bits.value

    # ── The window ────────────────────────────────────────

    def load_icons(self, ico_path: str, dpi: int):
        """(big, small) icons for the taskbar button, from the .ico; either
        may be None."""
        icons = []
        for metric in (self.SM_CXICON, self.SM_CXSMICON):
            if self._GetSystemMetricsForDpi is not None:
                px = self._GetSystemMetricsForDpi(metric, dpi)
            else:
                px = self._GetSystemMetrics(metric)
            icons.append(self._LoadImageW(None, ico_path, self.IMAGE_ICON,
                                          px, px, self.LR_LOADFROMFILE))
        return icons

    def destroy_icon(self, icon) -> None:
        self._DestroyIcon(icon)

    def register_class(self) -> int:
        instance = self._GetModuleHandleW(None)
        cls = self.WNDCLASSEXW()
        cls.cbSize = self.ctypes.sizeof(cls)
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = instance
        cls.hCursor = self._LoadCursorW(None, self.IDC_ARROW)
        cls.lpszClassName = self.CLASS_NAME
        atom = self._RegisterClassExW(self.ctypes.byref(cls))
        if not atom:
            error = self._GetLastError()
            if error != self.ERROR_CLASS_ALREADY_EXISTS:
                raise OSError(f"RegisterClassExW failed ({error})")
        return atom

    def unregister_class(self) -> None:
        self._UnregisterClassW(self.CLASS_NAME, self._GetModuleHandleW(None))

    def create_window(self, x: int, y: int, size: int):
        # Unowned popup + WS_EX_APPWINDOW: it has a taskbar button (carrying
        # icon.ico; the AppUserModelID is set process-wide at the top of
        # main.py), as the Qt splash had. Not topmost, as the Qt splash was
        # not: an always-on-top panel would sit on an OBS fullscreen
        # projector or in a capture if the operator switches to them.
        hwnd = self._CreateWindowExW(
            self.WS_EX_LAYERED | self.WS_EX_APPWINDOW,
            self.CLASS_NAME, "Autona", self.WS_POPUP, x, y, size, size,
            None, None, self._GetModuleHandleW(None), None)
        if not hwnd:
            raise OSError(f"CreateWindowExW failed ({self._GetLastError()})")
        return hwnd

    def destroy_window(self, hwnd) -> None:
        self._DestroyWindow(hwnd)

    def set_icons(self, hwnd, icons) -> None:
        big, small = icons
        if big:
            self._SendMessageW(hwnd, self.WM_SETICON, self.ICON_BIG, big)
        if small:
            self._SendMessageW(hwnd, self.WM_SETICON, self.ICON_SMALL, small)

    def show_no_activate(self, hwnd) -> None:
        # Not activated: whatever the operator does meanwhile keeps the
        # focus, and the window (which activates itself on show) is not
        # kept from the foreground by it.
        self._ShowWindow(hwnd, self.SW_SHOWNOACTIVATE)

    def update_layered(self, hwnd, dc, size: int, position) -> bool:
        """The window's first frame, at `position`."""
        wt, byref = self.wt, self.ctypes.byref
        return bool(self._UpdateLayeredWindow(
            hwnd, None, byref(wt.POINT(*position)), byref(wt.SIZE(size, size)),
            dc, byref(wt.POINT(0, 0)), 0, byref(self._blend), self.ULW_ALPHA))

    def frame_presenter(self, hwnd, dc, size: int):
        """present(bitmap): put a rendered frame on screen. Everything but
        the bitmap is bound here, once, so a frame costs two calls, both
        holding the GIL (_bind)."""
        byref = self.ctypes.byref
        size_ref = byref(self.wt.SIZE(size, size))
        origin_ref = byref(self.wt.POINT(0, 0))
        blend_ref = byref(self._blend)
        select = self._SelectObject
        update = self._UpdateLayeredWindow
        flags = self.ULW_ALPHA

        def present(bitmap):
            select(dc, bitmap)
            update(hwnd, None, None, size_ref, dc, origin_ref, 0, blend_ref,
                   flags)
        return present

    def post_message(self, hwnd, msg: int, wparam: int, lparam: int) -> bool:
        return bool(self._PostMessageW(hwnd, msg, wparam, lparam))

    # ── The frame loop ────────────────────────────────────

    def create_frame_timer(self):
        """A high-resolution periodic timer (a HANDLE) ticking every
        FRAME_INTERVAL_MS: the plain wait's timeout is only as fine as the
        system timer (~15.6 ms), which would halve the frame rate. None
        where Windows has none (before 10 1803): the wait's timeout then."""
        timer = self._CreateWaitableTimerExW(
            None, None, self.CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
            self.TIMER_ALL_ACCESS)
        if not timer:
            return None
        due = self.ctypes.c_longlong(-FRAME_INTERVAL_MS * 10_000)
        if not self._SetWaitableTimer(timer, self.ctypes.byref(due),
                                      FRAME_INTERVAL_MS, None, None, False):
            self._CloseHandle(timer)
            return None
        return self.wt.HANDLE(timer)

    def close_handle(self, handle) -> None:
        self._CloseHandle(handle)

    def wait_for_tick(self, timer) -> int:
        """Until the next tick or a message (the GIL released meanwhile).
        0 when it waited; the error code when the wait failed (WAIT_FAILED),
        having returned at once."""
        if timer is not None:
            result = self._MsgWaitForMultipleObjects(
                1, self.ctypes.byref(timer), False, 100, self.QS_ALLINPUT)
        else:
            result = self._MsgWaitForMultipleObjects(
                0, None, False, FRAME_INTERVAL_MS, self.QS_ALLINPUT)
        if result == self.WAIT_FAILED:
            return self.ctypes.get_last_error() or -1
        return 0

    def pump_messages(self, msg, stop_message: int) -> bool:
        """Dispatch what is queued; False on `stop_message` or WM_QUIT."""
        ref = self.ctypes.byref(msg)
        while self._PeekMessageW(ref, None, 0, 0, self.PM_REMOVE):
            if msg.message in (stop_message, self.WM_QUIT):
                return False
            self._TranslateMessage(ref)
            self._DispatchMessageW(ref)
        return True


class _FramePainter:
    """Renders frames into premultiplied BGRA DIB sections with GDI+: the
    rounded panel, the icon, the shine over it, the text. What every frame
    reuses -- the panel's path, the brushes, the pen, the icon scaled to
    size, the font -- is made once."""

    def __init__(self, api: _Win32Api, size: int, scale: float,
                 icon) -> None:
        self.api = api
        self.size = size
        self.scale = scale
        self.path = self.brush = self.pen = self.scaled = self.shine = None
        self.fonts = self.own_family = self.font = self.format = None
        try:
            self._build(icon)
        except Exception:
            self.close()
            raise

    def _build(self, icon) -> None:
        api, size, scale = self.api, self.size, self.scale
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        path = c_void_p()
        _gp(api._GdipCreatePath(api.FillModeAlternate, byref(path)),
            "GdipCreatePath")
        self.path = path.value
        # Inset half a pixel so the 1 px edge lands on whole pixels.
        r2 = 2 * SPLASH_RADIUS_DIP * scale
        lo, hi = 0.5, size - 0.5
        for x, y, start in ((lo, lo, 180), (hi - r2, lo, 270),
                            (hi - r2, hi - r2, 0), (lo, hi - r2, 90)):
            _gp(api._GdipAddPathArc(self.path, x, y, r2, r2, start, 90),
                "GdipAddPathArc")
        _gp(api._GdipClosePathFigure(self.path), "GdipClosePathFigure")
        brush = c_void_p()
        _gp(api._GdipCreateSolidFill(COLOR_PANEL, byref(brush)),
            "GdipCreateSolidFill")
        self.brush = brush.value
        pen = c_void_p()
        _gp(api._GdipCreatePen1(COLOR_PANEL_EDGE, max(1.0, scale),
                                api.UnitPixel, byref(pen)), "GdipCreatePen1")
        self.pen = pen.value
        self.icon_x, self.icon_y, self.icon_px = icon_box(scale)
        self._prescale(icon)
        self._make_shine()
        self.layers = band_layers(band_half_width(self.icon_px))
        self._make_text()

    def _prescale(self, icon) -> None:
        """The icon scaled down once to its size on screen: drawing the
        512 px original into every frame costs ~4 ms a frame."""
        api, px = self.api, self.icon_px
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        image = c_void_p()
        _gp(api._GdipCreateBitmapFromScan0(px, px, 0,
                                           api.PixelFormat32bppPARGB, None,
                                           byref(image)),
            "GdipCreateBitmapFromScan0")
        self.scaled = image.value
        graphics = c_void_p()
        _gp(api._GdipGetImageGraphicsContext(self.scaled, byref(graphics)),
            "GdipGetImageGraphicsContext")
        try:
            api._GdipSetInterpolationMode(
                graphics, api.InterpolationModeHighQualityBicubic)
            api._GdipSetPixelOffsetMode(graphics,
                                        api.PixelOffsetModeHighQuality)
            _gp(api._GdipGraphicsClear(graphics, 0), "GdipGraphicsClear")
            _gp(api._GdipDrawImageRect(graphics, icon, 0, 0, px, px),
                "GdipDrawImageRect")
        finally:
            api._GdipDeleteGraphics(graphics)

    def _make_shine(self) -> None:
        """The shine's brush: the icon in white at one band layer's alpha,
        laid where the icon is drawn and transparent outside it (no tiling).
        Filling a band with it lights the icon's pixels only, and only as
        much as they are opaque: the icon's alpha is the brush's."""
        api, px = self.api, self.icon_px
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        attributes = c_void_p()
        _gp(api._GdipCreateImageAttributes(byref(attributes)),
            "GdipCreateImageAttributes")
        try:
            # [r g b a 1] x m: the last row adds 1 to r, g and b (white),
            # and a is scaled to the layer's alpha.
            matrix = api.ColorMatrix()
            matrix.m[3 * 5 + 3] = band_layer_alpha()
            matrix.m[4 * 5 + 0] = matrix.m[4 * 5 + 1] = 1.0
            matrix.m[4 * 5 + 2] = matrix.m[4 * 5 + 4] = 1.0
            _gp(api._GdipSetImageAttributesColorMatrix(
                attributes, api.ColorAdjustTypeDefault, True, byref(matrix),
                None, api.ColorMatrixFlagsDefault),
                "GdipSetImageAttributesColorMatrix")
            shine = c_void_p()
            _gp(api._GdipCreateTextureIA(self.scaled, attributes, 0, 0, px,
                                         px, byref(shine)),
                "GdipCreateTextureIA")
            self.shine = shine.value
        finally:
            api._GdipDisposeImageAttributes(attributes)
        _gp(api._GdipSetTextureWrapMode(self.shine, api.WrapModeClamp),
            "GdipSetTextureWrapMode")
        _gp(api._GdipTranslateTextureTransform(
            self.shine, self.icon_x, self.icon_y, api.MatrixOrderAppend),
            "GdipTranslateTextureTransform")

    def _make_text(self) -> None:
        """The font (LINE Seed JP from the app's font file, Segoe UI when
        that cannot be loaded), the format, and where the text starts:
        "Loading..." centred, so the dots grow without moving the word."""
        api, scale = self.api, self.scale
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        family = None
        try:
            fonts = c_void_p()
            _gp(api._GdipNewPrivateFontCollection(byref(fonts)),
                "GdipNewPrivateFontCollection")
            self.fonts = fonts.value
            _gp(api._GdipPrivateAddFontFile(self.fonts, TEXT_FONT_FILE),
                f"loading {TEXT_FONT_FILE}")
            found_family, found = c_void_p(), api.ctypes.c_int()
            _gp(api._GdipGetFontCollectionFamilyList(
                self.fonts, 1, byref(found_family), byref(found)),
                "GdipGetFontCollectionFamilyList")
            if found.value:
                family = found_family.value      # the collection's own
        except OSError:
            family = None
        if family is None:
            fallback = c_void_p()
            _gp(api._GdipCreateFontFamilyFromName(TEXT_FONT_FALLBACK, None,
                                                  byref(fallback)),
                "GdipCreateFontFamilyFromName")
            self.own_family = family = fallback.value
        self.font_family_loaded = self.own_family is None
        font = c_void_p()
        _gp(api._GdipCreateFont(family, TEXT_DIP * scale,
                                api.FontStyleRegular, api.UnitPixel,
                                byref(font)), "GdipCreateFont")
        self.font = font.value
        string_format = c_void_p()
        _gp(api._GdipCreateStringFormat(api.StringFormatFlagsNoWrap, 0,
                                        byref(string_format)),
            "GdipCreateStringFormat")
        self.format = string_format.value
        _gp(api._GdipSetStringFormatLineAlign(self.format,
                                              api.StringAlignmentCenter),
            "GdipSetStringFormatLineAlign")
        x, y, w, h = text_box(scale)
        width = self._measure(LOADING_TEXT, api.RectF(x, y, w, h))
        start = max(0.0, (self.size - width) / 2)
        self.text_rect = api.RectF(start, y, self.size - start, h)

    def _measure(self, text: str, rect) -> float:
        api = self.api
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        image = c_void_p()
        _gp(api._GdipCreateBitmapFromScan0(1, 1, 0,
                                           api.PixelFormat32bppPARGB, None,
                                           byref(image)),
            "GdipCreateBitmapFromScan0")
        try:
            graphics = c_void_p()
            _gp(api._GdipGetImageGraphicsContext(image, byref(graphics)),
                "GdipGetImageGraphicsContext")
            try:
                api._GdipSetTextRenderingHint(graphics,
                                              api.TextRenderingHintAntiAlias)
                box = api.RectF()
                fitted, lines = api.ctypes.c_int(), api.ctypes.c_int()
                _gp(api._GdipMeasureString(
                    graphics, text, len(text), self.font, byref(rect),
                    self.format, byref(box), byref(fitted), byref(lines)),
                    "GdipMeasureString")
                return box.Width
            finally:
                api._GdipDeleteGraphics(graphics)
        finally:
            api._GdipDisposeImage(image)

    def close(self) -> None:
        api = self.api
        if self.format:
            api._GdipDeleteStringFormat(self.format)
        if self.font:
            api._GdipDeleteFont(self.font)
        if self.own_family:
            api._GdipDeleteFontFamily(self.own_family)
        if self.fonts:
            api._GdipDeletePrivateFontCollection(
                api.ctypes.byref(api.ctypes.c_void_p(self.fonts)))
        if self.shine:
            api._GdipDeleteBrush(self.shine)
        if self.scaled:
            api._GdipDisposeImage(self.scaled)
        if self.pen:
            api._GdipDeletePen(self.pen)
        if self.brush:
            api._GdipDeleteBrush(self.brush)
        if self.path:
            api._GdipDeletePath(self.path)

    def render(self, dc, spec: FrameSpec):
        """One frame: a DIB section the caller owns (DeleteObject)."""
        api, size = self.api, self.size
        c_void_p, byref = api.ctypes.c_void_p, api.ctypes.byref
        bitmap, bits = api.create_dib(dc, size)
        try:
            image = c_void_p()
            # A GDI+ bitmap over the DIB's own pixels, in the format
            # UpdateLayeredWindow takes: premultiplied BGRA.
            _gp(api._GdipCreateBitmapFromScan0(
                size, size, size * 4, api.PixelFormat32bppPARGB, bits,
                byref(image)), "GdipCreateBitmapFromScan0")
            try:
                graphics = c_void_p()
                _gp(api._GdipGetImageGraphicsContext(image, byref(graphics)),
                    "GdipGetImageGraphicsContext")
                try:
                    self._paint(graphics.value, spec)
                finally:
                    api._GdipDeleteGraphics(graphics)
            finally:
                api._GdipDisposeImage(image)
        except Exception:
            api.delete_object(bitmap)
            raise
        return bitmap

    def _paint(self, g, spec: FrameSpec) -> None:
        api, px = self.api, self.icon_px
        api._GdipSetSmoothingMode(g, api.SmoothingModeAntiAlias)
        api._GdipSetInterpolationMode(g, api.InterpolationModeHighQualityBicubic)
        api._GdipSetPixelOffsetMode(g, api.PixelOffsetModeHighQuality)
        # Grey antialiasing, not ClearType: per-channel coverage has no
        # meaning on a layer with per-pixel alpha.
        api._GdipSetTextRenderingHint(g, api.TextRenderingHintAntiAlias)
        _gp(api._GdipGraphicsClear(g, 0), "GdipGraphicsClear")
        _gp(api._GdipSetSolidFillColor(self.brush, COLOR_PANEL),
            "GdipSetSolidFillColor")
        _gp(api._GdipFillPath(g, self.brush, self.path), "GdipFillPath")
        _gp(api._GdipDrawPath(g, self.pen, self.path), "GdipDrawPath")
        _gp(api._GdipDrawImageRect(g, self.scaled, self.icon_x, self.icon_y,
                                   px, px), "GdipDrawImageRect")
        if spec.sweep is not None:
            # Clipped to the icon's box: outside it the brush is transparent
            # anyway, and the clip spares GDI+ rasterizing the long strips
            # there (~0.45 ms a layer otherwise).
            _gp(api._GdipSetClipRectI(g, self.icon_x, self.icon_y, px, px,
                                      api.CombineModeReplace),
                "GdipSetClipRectI")
            center = band_center(spec.sweep, px)
            for half in self.layers:
                self._fill_band(g, center, half)
            _gp(api._GdipResetClip(g), "GdipResetClip")
        _gp(api._GdipSetSolidFillColor(self.brush, COLOR_TEXT),
            "GdipSetSolidFillColor")
        text = loading_text(spec.dots)
        _gp(api._GdipDrawString(g, text, len(text), self.font,
                                api.ctypes.byref(self.text_rect), self.format,
                                self.brush), "GdipDrawString")

    def _fill_band(self, g, center: float, half: float) -> None:
        """One layer of the band: the strip between the "/" lines
        x + y = (center -+ half) * sqrt 2 (icon coordinates), drawn long
        enough to cross the icon, filled with the shine's brush."""
        api, px = self.api, self.icon_px
        if center + half <= 0 or center - half >= px * math.sqrt(2):
            return
        k1 = (center - half) * math.sqrt(2)
        k2 = (center + half) * math.sqrt(2)
        ox, oy, far = self.icon_x, self.icon_y, px
        P = api.PointF
        points = (P * 4)(P(ox + k1 + far, oy - far),
                         P(ox - far, oy + k1 + far),
                         P(ox - far, oy + k2 + far),
                         P(ox + k2 + far, oy - far))
        _gp(api._GdipFillPolygon(g, self.shine, points, 4,
                                 api.FillModeAlternate), "GdipFillPolygon")


def _bind(dll, name, restype, *argtypes):
    """A binding that keeps the GIL through the call (ctypes.PYFUNCTYPE).

    ctypes releases the GIL around every foreign call, and taking it back
    while the main thread runs Python (the imports) waits up to the switch
    interval (5 ms): the setup's ~1200 calls (rendering the frames) would
    wait seconds, a frame's four calls 20 ms. Every call the splash makes is
    short and waits on nothing that needs the GIL, so they all keep it; only
    the wait between frames gives it up (_bind_releasing_gil). PYFUNCTYPE
    is cdecl, which on 64-bit Windows is the one calling convention there
    is; on 32-bit, where the Win32 API is stdcall, this falls back to the
    ordinary binding. Without use_last_error: a failure's code is read
    with the GetLastError binding, right after, the GIL still held.
    """
    import ctypes
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return _bind_releasing_gil(dll, name, restype, *argtypes)
    return ctypes.PYFUNCTYPE(restype, *argtypes)((name, dll))


def _bind_releasing_gil(dll, name, restype, *argtypes):
    """The ordinary ctypes binding: the GIL is released during the call."""
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


def _gp(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"{what} failed (GDI+ status {status})")


# ──────────────────────────────────────────────────────────
# Post-show loading overlay (Qt; defined on first access)
# ──────────────────────────────────────────────────────────

SPINNER_INTERVAL_MS = 40
SPINNER_BLADES = 12
SPINNER_BLADE_STEP = 360 // SPINNER_BLADES
SPINNER_INNER_R = 14
SPINNER_OUTER_R = 26
SPINNER_BLADE_W = 3


def __getattr__(name: str):
    # PEP 562. LoadingOverlay is a QWidget, and defining it here at import
    # would import PySide6 before main.py has shown the splash -- the import
    # this module's splash exists to cover. src/Autona.py's
    # `from .autona_splash import LoadingOverlay` defines it, once.
    if name == "LoadingOverlay":
        cls = _define_loading_overlay()
        globals()["LoadingOverlay"] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _define_loading_overlay():
    from PySide6.QtCore import QEvent, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import QWidget

    OVERLAY_BG = QColor(10, 11, 13, 235)  # canvas
    OVERLAY_BLADE_COLOR = QColor("#F0C45A")

    class LoadingOverlay(QWidget):
        """Full-window child widget that paints a dim backdrop + rotating
        spinner. Covers the main window from its show until its page is there
        (ADR-005 S-final SF-5b-3: the shared view's first load, after the web
        server's start), so the operator sees a spinner instead of an empty
        window.

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
            self._painted_callbacks: list = []

        # ── Public API ─────────────────────────────────────────

        def start(self) -> None:
            self.show()
            self.raise_()
            self._timer.start()

        def call_when_painted(self, callback, max_ms: int = 1500) -> None:
            """Run `callback` once the overlay has been painted, leaving it up.

            The window's first paint pass (the overlay is painted in it, over
            the window) is what is waited for, so `callback` may block the GUI
            thread without the operator facing an unpainted window (see
            Window._StartWebServer: the web server's build). It is dispatched
            through a zero-delay timer after that paint, never from inside the
            paint event: it runs from the event loop, where a modal dialog is
            an ordinary top-level one and not a nested one (pitfalls #9).
            Not with processEvents() either: re-entering the event loop while
            widgets are still being built crashes at the C++ level (#9).

            A window that is never painted -- started minimized -- does not
            hold it back: past `max_ms` it runs anyway. Once only, whichever
            comes first; right away (through the event loop) when the overlay
            was painted already or is gone.
            """
            done = []

            def run():
                if done:
                    return
                done.append(True)
                callback()

            if self._painted_at is not None or self._finished:
                QTimer.singleShot(0, run)
                return
            self._painted_callbacks.append(run)
            QTimer.singleShot(max_ms, run)

        def finish(self) -> None:
            # Idempotent: the window may be asked to dismiss it more than once
            # (the first page loaded, the web server failed to start).
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
                # After this paint pass, from the event loop (call_when_painted).
                callbacks, self._painted_callbacks = self._painted_callbacks, []
                for callback in callbacks:
                    QTimer.singleShot(0, callback)
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

    return LoadingOverlay
