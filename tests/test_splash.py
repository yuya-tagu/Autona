"""The startup splash (autona_splash: NativeSplash and its animation).

The main thread is blocked for the splash's whole life (importing PySide6
and the app, then building the window, with no event loop), so the splash is
a native Win32 layered window owned and animated by a thread of its own
(2026-11-19; the RPG-style QSplashScreen it replaced only repainted when the
startup code called set_message). It shows the Autona icon with a diagonal
band of light sweeping across it, then a pause, and "Loading..." below.
What has to hold:

* the layout and the animation (pure functions, any platform): the icon is
  centred a little above the middle with the text's line below it, both in
  the panel; the band starts wholly before the icon and ends wholly past it,
  always moving forward; its nested layers are widest first and add up to
  the peak alpha, tracing a raised cosine; a cycle is the sweep's ticks then
  the pause's, the dots counting 0 .. 3 across it, the pause's ticks sharing
  one frame per dot count, and the tick for a time is rounded and wraps;
* importing autona_splash starts nothing and imports no Qt -- main.py
  imports it before PySide6 to put the splash up first -- and
  `LoadingOverlay`, the Qt half, is defined on first access, once;
* there is always something to finish(): a no-op stand-in where there is no
  splash (not Windows, any failure, no create_splash() at all), and a
  failure is noted, never raised -- also one after start() returned True,
  which finish() notes; a splash slower than create_splash()'s wait is
  noted, published all the same and comes up when ready; one that comes up
  is published; a failing frame wait falls back to sleeping the tick;
* on Windows, the real thing: a rendered frame lights the icon only (the
  band changes nothing outside the icon's box) and leaves the panel's
  corners transparent; the window is up when start() returns, is a layered
  top-level (not topmost) owned by the splash thread, frames advance, and finish()
  returns promptly with the thread and the window gone -- also when asked
  while the frames are still being rendered, the window then never coming
  up;
* the call sites: main.py puts it up right after the gate, before PySide6,
  takes it down at exit if the start fails, and the window finishes it
  right after its show(); no progress messages
  and no processEvents() anywhere (agent-pitfalls #9).

The Windows test puts a real 220 px window on screen for a fraction of a
second (not activated: it takes no focus).
"""
import ast
import math
import subprocess
import sys
import threading
import time

import pytest

import autona_splash
from autona_splash import (
    CYCLE_FRAMES,
    DOT_FRAMES,
    FRAME_INTERVAL_MS,
    ICON_DIP,
    LOADING_DOTS,
    SHINE_ALPHA,
    SPLASH_DIP,
    SWEEP_FRAMES,
    FrameSpec,
    band_center,
    band_half_width,
    band_layer_alpha,
    band_layers,
    cycle_frame,
    cycle_spec,
    frame_table,
    icon_box,
    loading_text,
    text_box,
)
from tests.conftest import PROJECT_ROOT

windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="the native splash is Windows only")


# ── the layout ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_the_icon_sits_above_the_middle_with_the_text_below(scale):
    size = SPLASH_DIP * scale
    x, y, px = icon_box(scale)
    assert all(isinstance(v, int) for v in (x, y, px))
    assert px == round(ICON_DIP * scale)
    assert abs(x + px / 2 - size / 2) <= 0.5          # centred
    assert y + px / 2 < size / 2                       # a little above
    tx, ty, tw, th = text_box(scale)
    assert ty >= y + px                                # below the icon
    assert tx == 0 and tw == pytest.approx(size)       # the panel's width
    assert 0 < y and ty + th < size
    # The whole group is centred: as much room above the icon as below
    # the text's line.
    assert y == pytest.approx(size - (ty + th), abs=1)


# ── the shine ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("icon_px", [96, 144, 192])
def test_the_band_sweeps_from_before_the_icon_to_past_it(icon_px):
    w = band_half_width(icon_px)
    diagonal = icon_px * math.sqrt(2)
    assert band_center(0.0, icon_px) + w == pytest.approx(0.0)
    assert band_center(1.0, icon_px) - w == pytest.approx(diagonal)
    assert band_center(0.5, icon_px) == pytest.approx(diagonal / 2)
    centres = [band_center(i / 100, icon_px) for i in range(101)]
    assert all(b > a for a, b in zip(centres, centres[1:]))
    # The band is a soft stripe, not a wash: well under the icon's size.
    assert 0 < 2 * w < diagonal / 2


def test_the_band_layers_add_up_to_a_raised_cosine():
    w = 50.0
    layers = band_layers(w)
    a = band_layer_alpha()
    assert len(layers) == autona_splash.BAND_LAYERS
    assert all(0 < half <= w for half in layers)
    assert all(b < a_ for a_, b in zip(layers, layers[1:]))   # widest first
    # All of them together: the peak, in the middle of the band.
    assert 1 - (1 - a) ** len(layers) == pytest.approx(SHINE_ALPHA)
    # Anywhere across the band, the light of the layers covering it is
    # within one layer's step of the raised cosine.
    for i in range(101):
        d = w * i / 100
        covering = sum(1 for half in layers if half > d)
        light = 1 - (1 - a) ** covering
        target = SHINE_ALPHA * math.cos(math.pi * d / (2 * w)) ** 2
        assert abs(light - target) <= a


# ── the cycle ───────────────────────────────────────────────────────────

def test_a_cycle_is_the_sweep_then_the_pause_with_the_dots_counting():
    specs = [cycle_spec(m) for m in range(CYCLE_FRAMES)]
    sweeps = [spec.sweep for spec in specs[:SWEEP_FRAMES]]
    assert sweeps[0] == 0.0 and sweeps[-1] == 1.0
    assert all(b > a for a, b in zip(sweeps, sweeps[1:]))
    assert all(spec.sweep is None for spec in specs[SWEEP_FRAMES:])
    dots = [spec.dots for spec in specs]
    assert dots == sorted(dots)
    assert [dots.count(n) for n in range(LOADING_DOTS + 1)] == (
        [DOT_FRAMES] * (LOADING_DOTS + 1))
    assert cycle_spec(CYCLE_FRAMES) == cycle_spec(0)
    # 0.7 s of sweep, 0.9 s of pause.
    tick = FRAME_INTERVAL_MS / 1000
    assert SWEEP_FRAMES * tick == pytest.approx(0.7, abs=0.02)
    assert (CYCLE_FRAMES - SWEEP_FRAMES) * tick == pytest.approx(0.9,
                                                                 abs=0.02)
    assert [loading_text(n) for n in range(LOADING_DOTS + 1)] == [
        "Loading", "Loading.", "Loading..", "Loading..."]


def test_the_frames_rendered_are_the_sweep_and_one_per_pause_dot_count():
    specs, index = frame_table()
    assert len(index) == CYCLE_FRAMES
    assert len(specs) == len(set(specs))
    assert [specs[i] for i in index] == [cycle_spec(m)
                                         for m in range(CYCLE_FRAMES)]
    pause_dots = {cycle_spec(m).dots
                  for m in range(SWEEP_FRAMES, CYCLE_FRAMES)}
    assert len(specs) == SWEEP_FRAMES + len(pause_dots)
    assert all(isinstance(spec, FrameSpec) for spec in specs)


def test_the_tick_for_a_time_is_rounded_and_wraps():
    tick = FRAME_INTERVAL_MS / 1000
    for m in range(CYCLE_FRAMES + 5):
        assert cycle_frame(m * tick) == m % CYCLE_FRAMES
        assert cycle_frame(m * tick + 0.004) == m % CYCLE_FRAMES
        assert cycle_frame(max(0.0, m * tick - 0.004)) == m % CYCLE_FRAMES


# ── import, the stand-in, failures ──────────────────────────────────────

def test_importing_it_starts_nothing_and_imports_no_qt():
    script = r"""
import sys, threading
import autona_splash
assert threading.active_count() == 1, threading.enumerate()
assert autona_splash.ACTIVE_SPLASH is None
assert autona_splash._API is None
qt = sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "qtpy"))
assert qt == [], qt
overlay = autona_splash.LoadingOverlay
from PySide6.QtWidgets import QWidget
assert issubclass(overlay, QWidget)
assert autona_splash.LoadingOverlay is overlay
from autona_splash import LoadingOverlay
assert LoadingOverlay is overlay
try:
    autona_splash.NoSuchName
except AttributeError:
    pass
else:
    raise AssertionError("module __getattr__ answers any name")
print("ok")
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_without_a_splash_finish_is_a_no_op(monkeypatch):
    monkeypatch.setattr(autona_splash, "ACTIVE_SPLASH", None)
    splash = autona_splash.active_splash()
    splash.finish(object())
    splash.finish()
    # Not Windows: no splash at all, nothing published.
    monkeypatch.setattr(autona_splash.sys, "platform", "linux")
    stand_in = autona_splash.create_splash()
    stand_in.finish(object())
    assert autona_splash.ACTIVE_SPLASH is None
    assert not isinstance(stand_in, autona_splash.NativeSplash)


@windows_only
def test_a_failing_splash_is_noted_and_degrades_to_none(monkeypatch):
    marks = []
    monkeypatch.setattr(autona_splash, "_PENDING_MARKS", marks)
    monkeypatch.setattr(autona_splash, "_STARTUP_LOGGER", None)
    monkeypatch.setattr(autona_splash, "ACTIVE_SPLASH", None)

    def broken():
        raise OSError("no GDI+ here")
    monkeypatch.setattr(autona_splash, "_win32", broken)
    before = threading.active_count()
    splash = autona_splash.create_splash()
    assert not isinstance(splash, autona_splash.NativeSplash)
    assert autona_splash.ACTIVE_SPLASH is None
    splash.finish(object())
    assert marks == ["splash: not shown (OSError: no GDI+ here)"]
    assert threading.active_count() == before


# ── the real window (Windows) ───────────────────────────────────────────

@windows_only
@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_a_frame_lights_the_icon_only_and_keeps_the_corners_clear(
        monkeypatch, scale):
    """Rendered off screen (no window): the band changes nothing outside
    the icon's box and something inside it, the rounded panel's corners stay
    transparent, and its middle is opaque."""
    import ctypes
    api = autona_splash._win32()
    pixels = []
    create_dib = api.create_dib

    def recording(dc, size):
        bitmap, bits = create_dib(dc, size)
        pixels.append(bits)
        return bitmap, bits
    monkeypatch.setattr(api, "create_dib", recording)
    size = int(round(SPLASH_DIP * scale))
    token = api.gdiplus_startup()
    icon = api.gdip_load_image(str(PROJECT_ROOT / "assets/icons/icon.png"))
    dc = api.create_memory_dc()
    painter = autona_splash._FramePainter(api, size, scale, icon)
    bitmaps = []
    try:
        for spec in (FrameSpec(None, 1), FrameSpec(0.5, 1)):
            bitmaps.append(painter.render(dc, spec))
        plain, lit = (ctypes.string_at(bits, size * size * 4)
                      for bits in pixels)
    finally:
        for bitmap in bitmaps:
            api.delete_object(bitmap)
        painter.close()
        api.delete_dc(dc)
        api.gdip_dispose_image(icon)
        api.gdiplus_shutdown(token)

    x0, y0, px = icon_box(scale)
    changed_inside = changed_outside = 0
    for y in range(size):
        for x in range(size):
            at = (y * size + x) * 4
            if plain[at:at + 4] != lit[at:at + 4]:
                if x0 <= x < x0 + px and y0 <= y < y0 + px:
                    changed_inside += 1
                else:
                    changed_outside += 1
    assert changed_outside == 0
    assert changed_inside > px          # a band, not a stray pixel

    def alpha(x, y):
        return plain[(y * size + x) * 4 + 3]
    for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
        assert alpha(x, y) == 0
    assert alpha(size // 2, size // 2) == 255


def _window_facts(hwnd):
    import ctypes
    from ctypes import wintypes as wt
    user32 = ctypes.WinDLL("user32")
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    if not user32.IsWindow(hwnd):
        return None
    return {
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "exstyle": user32.GetWindowLongPtrW(hwnd, -20),     # GWL_EXSTYLE
        "thread": user32.GetWindowThreadProcessId(hwnd, None),
        "owner": user32.GetParent(hwnd),
    }


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@windows_only
def test_the_window_animates_on_its_own_thread_and_finishes_promptly(
        monkeypatch):
    marks = []
    monkeypatch.setattr(autona_splash, "_PENDING_MARKS", marks)
    monkeypatch.setattr(autona_splash, "_STARTUP_LOGGER", None)
    splash = autona_splash.NativeSplash()
    assert splash.start(), splash.error
    try:
        assert splash._shown.wait(10) and splash.shown, splash.error
        hwnd = splash._hwnd
        facts = _window_facts(hwnd)
        assert facts is not None and facts["visible"]
        WS_EX_TOPMOST, WS_EX_LAYERED = 0x8, 0x80000
        assert facts["exstyle"] & WS_EX_LAYERED
        # Not always on top: it would sit on an OBS fullscreen projector.
        assert not facts["exstyle"] & WS_EX_TOPMOST
        assert facts["owner"] is None
        assert facts["thread"] == splash._thread.native_id
        assert facts["thread"] != threading.get_native_id()
        assert splash.frame_count == len(frame_table()[0])
        # Frames advance while this thread does something else entirely
        # (the first sweep's: the pause holds a frame for a dot count).
        assert _wait(lambda: splash.frames_shown >= 5)
    finally:
        started = time.monotonic()
        splash.finish(object())
        took = time.monotonic() - started
    assert took < autona_splash.FINISH_JOIN_S
    assert not splash.running
    assert _window_facts(hwnd) is None
    assert splash.error is None
    assert len(marks) == 1 and marks[0].startswith("splash: ")
    splash.finish(object())          # once is enough
    assert len(marks) == 1


@windows_only
def test_finish_while_the_frames_render_stops_the_thread():
    splash = autona_splash.NativeSplash()
    splash.start(wait_s=0)
    started = time.monotonic()
    splash.finish()
    # finish() never waits past its join's cap...
    assert time.monotonic() - started < autona_splash.FINISH_JOIN_S + 0.25
    # ...and the thread stops at its next check (the setup may be in the
    # middle of a long call on a loaded machine), never showing the window:
    # it would come up over the main window shown before finish().
    assert _wait(lambda: not splash.running, 10)
    assert splash._hwnd is None
    assert not splash.shown


def _marks(monkeypatch):
    marks = []
    monkeypatch.setattr(autona_splash, "_PENDING_MARKS", marks)
    monkeypatch.setattr(autona_splash, "_STARTUP_LOGGER", None)
    monkeypatch.setattr(autona_splash, "ACTIVE_SPLASH", None)
    return marks


@windows_only
def test_a_splash_that_comes_up_is_published(monkeypatch):
    marks = _marks(monkeypatch)
    monkeypatch.setattr(autona_splash, "SHOWN_WAIT_S", 10.0)
    splash = autona_splash.create_splash()
    try:
        assert isinstance(splash, autona_splash.NativeSplash)
        assert splash.shown
        assert autona_splash.ACTIVE_SPLASH is splash
        assert autona_splash.active_splash() is splash
        assert marks == []
    finally:
        splash.finish()


@windows_only
def test_a_splash_slower_than_the_wait_is_noted_and_comes_up_later(
        monkeypatch):
    marks = _marks(monkeypatch)
    monkeypatch.setattr(autona_splash, "SHOWN_WAIT_S", 0.2)
    gate = threading.Event()
    run = autona_splash._SplashWindow.run

    def held(self):
        gate.wait(10)
        run(self)
    monkeypatch.setattr(autona_splash._SplashWindow, "run", held)
    splash = autona_splash.create_splash()
    try:
        assert isinstance(splash, autona_splash.NativeSplash)
        assert autona_splash.ACTIVE_SPLASH is splash   # published all the same
        assert not splash.shown
        assert marks == ["splash: not up within 0.2s; startup goes on "
                         "(the splash shows once it is ready)"]
        gate.set()
        assert splash._shown.wait(10) and splash.shown, splash.error
    finally:
        gate.set()
        splash.finish()
    assert not splash.running
    assert marks[-1].startswith("splash: ") and " frames over " in marks[-1]


@windows_only
def test_a_failure_after_start_returned_is_noted_by_finish(monkeypatch):
    marks = _marks(monkeypatch)
    returned = threading.Event()

    def broken_loop(self, present, frames, index):
        returned.wait(10)
        raise RuntimeError("the loop broke")
    monkeypatch.setattr(autona_splash._SplashWindow, "_loop", broken_loop)
    splash = autona_splash.NativeSplash()
    assert splash.start(wait_s=10)       # up, and nothing wrong yet
    returned.set()
    assert _wait(lambda: not splash.running, 10)
    splash.finish()
    assert marks == ["splash: failed (RuntimeError: the loop broke)"]
    assert splash._hwnd is None


@windows_only
def test_a_failing_frame_wait_falls_back_to_sleeping(monkeypatch):
    marks = _marks(monkeypatch)
    api = autona_splash._win32()
    waits = []

    def failing(timer):
        waits.append(timer)
        return 6                         # ERROR_INVALID_HANDLE
    monkeypatch.setattr(api, "wait_for_tick", failing)
    splash = autona_splash.NativeSplash()
    assert splash.start(wait_s=10)
    try:
        assert _wait(lambda: splash.frames_shown >= 5)
        time.sleep(0.3)
    finally:
        splash.finish()
    assert len(waits) == 1               # not retried: slept from then on
    # Ticks at the sleep's pace, not a busy loop.
    ticks = splash.shown_s * 1000 / FRAME_INTERVAL_MS
    assert splash.frames_shown <= ticks + 2
    assert len(marks) == 1
    assert "the frame wait failed (error 6), slept instead" in marks[0]


# ── the call sites ──────────────────────────────────────────────────────

def _tokens(path):
    # The code alone (ast.unparse drops the comments), whitespace removed.
    source = ast.unparse(ast.parse(path.read_text(encoding="utf-8")))
    return "".join(source.split()).replace("'", '"')


def test_main_puts_it_up_after_the_gate_before_pyside6():
    tokens = _tokens(PROJECT_ROOT / "main.py")
    # After the gate and the headless shell (which shows no splash).
    order = ["ifnot_acquire_single_instance():",
             "sys.exit(headless.run(App))",
             "importautona_splash",
             'autona_splash.log_startup_phase("gate")',
             "autona_splash.create_splash()",
             'autona_splash.log_startup_phase("splash")',
             # A failed start takes it down on the way out.
             "atexit.register(autona_splash.active_splash().finish)",
             "fromPySide6.QtWidgetsimportQApplication",
             "App=QApplication(sys.argv)",
             "fromsrcimportAutona"]
    positions = [tokens.index(token) for token in order]
    assert positions == sorted(positions)
    assert tokens.count("create_splash()") == 1


def test_the_window_finishes_it_right_after_the_show():
    tokens = _tokens(PROJECT_ROOT / "src" / "Autona.py")
    order = ["splash=active_splash()", "self.show()", "splash.finish(self)",
             'log_startup_phase("core-shown")']
    positions = [tokens.index(token) for token in order]
    assert positions == sorted(positions)
    for gone in ("set_message", "pick_splash_messages", "splash_msgs"):
        assert gone not in tokens


def test_nothing_pumps_the_event_loop_for_the_splash():
    names = set()
    for path in (PROJECT_ROOT / "autona_splash.py", PROJECT_ROOT / "main.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
    assert "processEvents" not in names
    for gone in ("AutonaSplashScreen", "SPLASH_MESSAGE_PATTERNS",
                 "pick_splash_messages", "QSplashScreen"):
        assert not hasattr(autona_splash, gone)
