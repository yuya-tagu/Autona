"""Draws an HTML template off-screen and takes its picture (ADR-005 S4q).

What the images the app generates have in common: the match thumbnail's
(thumbnail/ThumbnailRenderer, S4o, where this was written) and the tournament
result's (result/ResultPageRenderer). A subclass says which kind of template
package it draws (the layout catalog's, ADR-007 T1: a package's folder, entry
page and size), its default, and which function of the page draws. One view
per kind of image: their sizes differ.

Where a template is drawn (ADR-007 D4 / T2; ADR-008 D7 R1): on the preview
listener's origin, ``http://127.0.0.1:<WebServer.PreviewPort()>/layout/
<folder>/<entry>`` -- never file:// any more. That origin reads what the
listener serves (layout/, assets/, the state's images, v1's READ) and writes
nothing.

Each renderer's page has a profile of its own (_RenderPage): off the record
(no storage on disk), no HTTP cache, and its request filter
(_RenderRequestFilter) on the profile, so that it sees every request made in
it -- the page's, its frames', its workers' (dedicated, shared, service) --
and stops all but those to its origin and data: / blob: (another host or
port -- this server's own among them --, file:, ws:, any other scheme). Being
another profile than the default one, the page shares no storage with the
shared view's pages (the design preview's frame is the same origin):
BroadcastChannel, localStorage, IndexedDB and cookies are the render's own.
What a request filter cannot see is not stopped: WebRTC's UDP (STUN / TURN)
is the network stack's own, and a template could reach another host that
way. The view lets the page read no local file nor remote URL as local
content, answers every permission it asks for with no (a WebRTC data channel
asks none), makes no window, and its main frame goes nowhere but the
document the renderer loaded. A payload's images the listener does not serve
are held while the render runs (core/RenderAssets: ForRender at its start,
Release at its end) and given as /render-asset/<id>. With no preview
listener (it could not bind, the server is not up yet) a render is refused
(RenderLane.PREVIEW_UNAVAILABLE), never drawn from file:// instead.

How a page says it drew, by its package's contract (the catalog's entry;
ADR-008 D7 R2): contract 1 runs the page's INIT_FUNCTION, which sets its
title to RENDER_READY, whatever went wrong in it; contract 2 hands the
payload to layout/include/autona.js (RENDER_SCRIPT), which draws in the
page's Autona.onRender, waits for its fonts and images and says ok or
failed. A failed one ends with no picture: its error is a
RenderLane.TemplateError, which carries what the page reported. An "ok" is
read again (Autona._renderResult) once the picture is taken: an error the
page caught after it (a timer, a rAF) fails the render too. A timed-out
contract 2 render ends with the errors its page caught, when it caught any.
A package's page is loaded again before a render when it is not what the
package is now (_stale: another contract, an edited file, no autona.js).

The Chromium sandbox is not turned off here any more (the module used to set
QTWEBENGINE_DISABLE_SANDBOX=1 on import). QtWebEngine reads it once, when it
starts, which in the app is the shared view's construction, before the first
render imports this module: it had no effect there (measured: the renderer
processes ran sandboxed, as the shared view's do). Only where a renderer
started WebEngine first (the tests) did it turn the sandbox off, for every
page; without it the sandbox is on whichever view starts first. See
docs/architecture/Design.md section 7.3.
"""
import json
import math
import os
import time
from urllib.parse import quote

from qtpy.QtCore import QObject, QRect, QTimer, QUrl, Qt
from qtpy.QtGui import QGuiApplication, QPainter, QPixmap
from qtpy.QtWidgets import QWidget
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import (
    QWebEngineLoadingInfo, QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineUrlRequestInterceptor)
from loguru import logger

from .AutonaWebServer import WebServer
from .core import LayoutCatalog, RenderAssets
from .core.RenderLane import PREVIEW_UNAVAILABLE, TemplateError

# The preview listener's host, as a render's page is loaded from it: the
# listener is bound to 127.0.0.1 or to every address (LAN); both answer here.
PREVIEW_HOST = "127.0.0.1"
# What a render's page may ask for besides its origin.
_INLINE_SCHEMES = ("data", "blob")
# How many refused requests a page's log names (the first ones of each load):
# a template that asks for a thousand does not write a thousand lines.
_REFUSALS_LOGGED = 20


class PreviewUnavailable(RuntimeError):
    """No preview listener to draw on: the render is refused."""


def Allowed(url, origin):
    """Whether a render's page may ask for ``url`` (a QUrl): data: / blob:,
    or http on ``origin`` ((host, port) of the preview listener the page was
    loaded from; None: no page loaded yet, nothing on http)."""
    scheme = url.scheme().lower()
    if scheme in _INLINE_SCHEMES:
        return True
    return origin is not None and scheme == "http" \
        and (url.host().lower(), url.port()) == origin


class _RenderRequestFilter(QWebEngineUrlRequestInterceptor):
    """A render's request filter (ADR-007 D4): only its page's origin (the
    preview listener the page was loaded from) and data: / blob: go through;
    anything else -- another host or port (this server's own, the outside),
    file:, ws: anywhere, another scheme -- is blocked. On the render's own
    profile (_RenderPage), which nothing else uses: a page's interceptor does
    not see what its service and shared workers ask. Qt calls it on the UI
    thread for every request made in the profile, and for a redirect's next
    hop. A decision that raises blocks."""

    def __init__(self, page, parent):
        super().__init__(parent)
        self._page = page

    def interceptRequest(self, info):
        try:
            url = info.requestUrl()
            if Allowed(url, self._page.origin):
                return
            info.block(True)
            self._page.refused(url)
        except Exception:
            logger.opt(exception=True).warning(
                "A render's request could not be judged; blocking it")
            try:
                info.block(True)
            except Exception:
                logger.opt(exception=True).error(
                    "A render's request could not be blocked")


class _RenderPage(QWebEnginePage):
    """A render's page: logs the template's console, filters its requests
    (_RenderRequestFilter), denies every permission and new window, and keeps
    its main frame on the document the renderer loaded (``expected``).

    On a profile of its own (see the module docstring), made here and made
    the page's child: a profile has to outlive the pages that use it (Qt
    warns, and may crash, when it goes first), and a child is deleted after
    its parent's destructor has run -- whoever deletes the page, and however
    late at exit. The filter is the profile's child, for the same reason (the
    profile does not own its interceptor)."""

    def __init__(self, logName):
        # No storage name: off the record, nothing on disk; and no HTTP
        # cache, so that a template edited while the app runs is drawn as it
        # is now (the listener serves layout/ for a day, for the overlays).
        profile = QWebEngineProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setSpellCheckEnabled(False)
        policy = getattr(QWebEngineProfile, "PersistentPermissionsPolicy",
                         None)
        if policy is not None:
            # Nothing is granted anyway (_deny_permission); nothing is kept.
            profile.setPersistentPermissionsPolicy(policy.AskEveryTime)
        super().__init__(profile)
        profile.setParent(self)
        self._profile = profile
        self._logName = logName
        # (host, port) the page's document is loaded from, and that document
        # (the renderer's load, with its query): set before each load
        # (loading(), from AutonaOffscreenRenderer._load_template).
        self.origin = None
        self.expected = None
        self._refusals = 0
        # Before the page loads anything. Kept here as well as parented: Qt
        # holds no reference to it.
        self._filter = _RenderRequestFilter(self, profile)
        profile.setUrlRequestInterceptor(self._filter)
        self.permissionRequested.connect(self._deny_permission)
        self.fileSystemAccessRequested.connect(
            lambda request: request.reject())
        self.registerProtocolHandlerRequested.connect(
            lambda request: request.reject())

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        source = sourceID.split("/")[-1] if sourceID else "unknown"
        where = f"[{self._logName} JS] {source}:{lineNumber} {message}"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error(where)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning(where)
        else:
            logger.debug(where)

    def loading(self, origin, url):
        """The renderer is about to load ``url`` (a QUrl) from ``origin``."""
        self.origin = origin
        self.expected = QUrl(url)
        self._refusals = 0

    def refused(self, url):
        # Its scheme, host and port only: a query may hold a payload's text.
        self._refusals += 1
        if self._refusals <= _REFUSALS_LOGGED:
            port = f":{url.port()}" if url.port() != -1 else ""
            logger.warning(
                f"{self._logName}: the template asked for {url.scheme()}://"
                f"{url.host()}{port}; refused (a template reads its preview "
                "origin only)")

    def _deny_permission(self, permission):
        logger.warning(f"{self._logName}: the template asked for "
                       f"{permission.permissionType().name}; denied")
        permission.deny()

    def acceptNavigationRequest(self, url, navigationType, isMainFrame):
        if isMainFrame:
            if self.expected is not None and url == self.expected:
                return True
            logger.warning(f"{self._logName}: the template's page may not go "
                           f"to {url.scheme()}://{url.host()}; refused")
            return False
        # A frame of its own: on its origin, or inline. Its requests are the
        # filter's as well; about: (no request) goes.
        if url.scheme().lower() == "about" or Allowed(url, self.origin):
            return True
        logger.warning(f"{self._logName}: a frame of the template may not go "
                       f"to {url.scheme()}://{url.host()}; refused")
        return False

    def createWindow(self, windowType):
        # No window: nothing a template opens is shown anywhere.
        return None


# The template contracts (ADR-007 D3, ADR-008 D7 R2): 1 -- the page's
# INIT_FUNCTION says RENDER_READY; 2 -- layout/include/autona.js's
# Autona._render draws in the page's Autona.onRender and says "AUTONA_RENDER
# <serial> ok | failed".
TEMPLATE_CONTRACT = 1
RENDER_CONTRACT = 2


class _Ticket:
    """One render: what to draw and who wants it."""
    __slots__ = ("payload", "templateId", "done", "ownLoad", "started",
                 "number", "ready", "painted", "marked", "markSeen",
                 "frameDeadline", "begun", "readyAt", "paintedAt",
                 "paintDetails", "held", "contract", "timedOut")

    def __init__(self, payload, templateId, done):
        self.payload = payload
        self.templateId = templateId
        # Its template's contract (LayoutCatalog.TEMPLATE_CONTRACTS), as the
        # catalog said when its script started: how the page says it is done.
        self.contract = TEMPLATE_CONTRACT
        # The files held for it (RenderAssets.ForRender), let go at its end.
        self.held = []
        # done(pixmap, error): the image, or None and why.
        self.done = done
        # Its page was loaded for it. A load on its way when it came was
        # begun for another (at construction, or for a render that timed out).
        self.ownLoad = False
        # Its script is running in the page; under this number.
        self.started = False
        self.number = 0
        # The page said RENDER_READY, and then that it painted.
        self.ready = False
        self.painted = False
        # Whether its page was asked to mark its frame (PAINT_SCRIPT ran),
        # whether a grab has shown the mark, and until when the grab looked
        # for (the mark, then a frame without it) is waited for.
        self.marked = False
        self.markSeen = False
        self.frameDeadline = None
        # When it started, said RENDER_READY and painted (time.monotonic()),
        # and what the page said of its wait: for the log.
        self.begun = self.readyAt = self.paintedAt = None
        self.paintDetails = ""
        # Its stage's time is over, and what its page caught is being read
        # (contract 2, _on_timeout).
        self.timedOut = False


class AutonaOffscreenRenderer(QObject):
    """Renders HTML templates to images using QWebEngineView.

    Architecture (modeled after scoreboard layout):
    - HTML page for the active template is loaded once
    - Each render injects new data via JS (no page reload) until the
      template id changes — then the new template's HTML is loaded
    - One render at a time, each with a ticket (the page has one document
      title to say "ready" with): whoever asks while one runs waits for its
      end, so nobody takes another render's frame for their own. Until
      ADR-005 S4o renderSync started right over a running render, and the
      running one's RENDER_READY ended the wait with its image.
    - A render that does not end within STAGE_TIMEOUT_MS fails. If its script
      was running the page is loaded again, so that the script cannot report
      ready to the next one. If its page was still loading the load goes on:
      cut off every time, a page that takes longer than that would never
      arrive, and the next render takes it from there.
    - A load that fails is tried again by the render waiting for it, if that
      render did not begin it: one that took over a load begun for another (at
      construction, or for a render that timed out) would otherwise wait out
      its whole timeout for a page that is not coming. A render's own load
      that fails is not tried again (a page that is not there is not loaded
      over and over): its timeout ends it, and the next render tries.
    - Whether a load ended is only believed of the newest one: the load
      number in its URL tells it from one it replaced, whose end may still be
      on its way.
    - A render that cannot be started fails like any other, through its
      taker, once.
    - The picture is taken once the page has painted the drawing, not when
      it says RENDER_READY: that only says its script is done, and the frame
      may not be composited yet (a grab then had the frame before it; after
      a change of template, the other template's). The page is then asked,
      by a script of the renderer's (the templates are an external contract
      and stay as they are), to wait for its fonts and to decode its images,
      then to mark its frame (a few pixels in its top left corner, in colours
      of the render's number) and say RENDER_PAINTED <its number>. Bounded by
      PAINT_WAIT_MS, in case its fonts or images never settle. The number is
      the render's, so a page that says it for another render is not
      believed.
    - Then the view's corner is grabbed until it shows the mark, the mark is
      taken away, and once the corner shows it no more the view is grabbed
      whole: the picture, a frame the view showed after one drawn after the
      drawing (_finalize_render).
      How long a frame takes to reach the view is not known: with software
      compositing (a GPU-less CI runner, --disable-gpu) the view showed a
      frame from before the drawing for one to three seconds after the page
      had begun two frames -- its text not in its web font yet, characters
      missing, half of it white --, and then neither a wait for "a fresh
      frame" (one unlike the page before), nor two grabs half a second apart
      that agreed (one frame, not replaced in that time), nor the waits'
      caps of a second kept it out. (They did until R4: a frame every
      170-200 ms with the screen locked, and two frames at 16 ms that had
      not reached the view, had been taken before them.) Bounded, the mark
      and its going together, by FRAME_WAIT_MS and by what the stage's
      watchdog leaves (_frame_wait_ms): a picture is taken then all the
      same, with a warning (Qt's offscreen platform, where a desktop
      composites no page, never shows the mark), and never with the mark
      in it (_without_mark).

    - A template id is one of the layout catalog's packages of CATALOG_KIND,
      read when a render is asked for (_template_for): its page is
      layout/<folder>/<entry>, at its size. A package once listed keeps its
      page: one whose files went missing since fails its load (and the next
      render tries again) like a page that is not there always did. An id the
      catalog never listed is drawn on the default; with no default either,
      the render cannot start.
    - The page is loaded from the preview listener (_preview_origin), as it
      serves when the load begins. With none, the render cannot start, and
      its taker hears PREVIEW_UNAVAILABLE. A page loaded from a listener
      that is not the one serving now (the server was started again on
      another port) is loaded again before the next render.
    - Each render draws its payload with the images the listener does not
      serve held for it (RenderAssets.ForRender, in _start_render), and lets
      them go when it ends, however (_finish).

    A subclass gives the class attributes below and _resolve_template_id, and
    a way in that calls _submit(), all on the GUI thread.
    """

    # What the log calls this kind of image.
    LOG_NAME = ""
    # The layout catalog's kind of the templates (LayoutCatalog.TEMPLATE_KINDS)
    # and the one drawn when the one asked for is not there.
    CATALOG_KIND = None
    DEFAULT_TEMPLATE_ID = None
    # The page's function that draws: given the payload, it says RENDER_READY.
    INIT_FUNCTION = ""
    # A picture that came out another size is made the template's (a screen
    # scale, or a view that did not take its size).
    ASPECT_MODE = Qt.AspectRatioMode.IgnoreAspectRatio
    # Whether the default template is loaded at construction, for a first
    # render that will want it. Otherwise the first render's page is the first.
    LOAD_AT_START = True

    # How long a render may wait for its page, and then for its script (each).
    STAGE_TIMEOUT_MS = 10000
    # At a contract 2 render's timeout: how long its page's errors are
    # waited for (_on_timeout).
    TIMEOUT_READ_MS = 1000
    # How long the page may take to say it painted (PAINT_SCRIPT). The view
    # is looked at then all the same (its mark may come later), and a warning
    # logged. Timed here, not in the page: a page whose frames stop may have
    # its timers held back too.
    PAINT_WAIT_MS = 1000
    # How long the view is looked at for the page's frame mark and then for
    # a frame without it, all told (_finalize_render): at most what is left
    # of the stage's time less FRAME_RESERVE_MS (the picture, and contract
    # 2's last read of its errors), so that a slow page's picture is taken,
    # maybe from before its drawing, as before the mark, not failed. How
    # often its corner is looked at meanwhile.
    FRAME_WAIT_MS = 6000
    FRAME_RESERVE_MS = 1500
    FRAME_POLL_MS = 30
    # The frame mark: two blocks MARK_SIZE pixels square side by side in the
    # top left corner (_mark_blocks: the page draws what they say; _marked:
    # found within MARK_TOLERANCE levels a channel), its style its own over
    # whatever the template's CSS says of a div (MARK_STYLE, inline and
    # important). What it cannot hold its own against is the html element's
    # own transform, zoom or filter: the layout kit's rules say not to.
    MARK_SIZE = 4
    MARK_TOLERANCE = 8
    MARK_STYLE = {
        "position": "fixed", "top": "0px", "right": "auto", "bottom": "auto",
        "margin": "0", "padding": "0", "border": "0", "outline": "none",
        "border-radius": "0", "box-shadow": "none",
        "box-sizing": "content-box", "min-width": "0", "min-height": "0",
        "max-width": "none", "max-height": "none", "display": "block",
        "visibility": "visible", "content-visibility": "visible",
        "opacity": "1", "transform": "none", "scale": "none",
        "translate": "none", "rotate": "none", "zoom": "1", "filter": "none",
        "backdrop-filter": "none", "clip-path": "none", "mask": "none",
        "mix-blend-mode": "normal", "z-index": "2147483647",
        "pointer-events": "none"}
    # Run in the page once it said RENDER_READY, with the render's number
    # (%d) and its frame mark's blocks (__MARK__, _mark_blocks as JSON):
    # once the fonts are loaded and the images (<img> and CSS backgrounds)
    # decoded, marks the frame and says "RENDER_PAINTED <number> painted",
    # "... failed" when it cannot tell; then how long each took, for the log
    # ("fonts=<ms> images=<ms>/<decoded>"). The mark is the page's last
    # element; whatever marks are on the page go first. It is marked when the
    # waits failed too: the picture is still looked for. A script whose
    # render is not the newest one asked of the page when its waits end (its
    # render was over, and the next one's script has begun) does nothing
    # more: it would take that render's mark away.
    # An image the render before decoded on this document is not decoded
    # again: it is the same image (a template draws the same background, the
    # same characters, again), and asking again repeats the work. Only the
    # render before's are remembered, so they do not pile up with every
    # character drawn (those are data: URLs, made per render). `complete` is
    # not the test: an image can be loaded and not decoded.
    PAINT_SCRIPT = r"""(async () => {
  const number = %d;
  window.__renderNewest = Math.max(window.__renderNewest || 0, number);
  const start = performance.now();
  const since = () => Math.round(performance.now() - start);
  const before = window.__renderDecoded || new Set();
  const kept = new Set();
  const seen = new Set();
  const waits = [];
  const decode = (url, image) => {
    if (!url || seen.has(url)) return;
    seen.add(url);
    if (before.has(url)) {
      kept.add(url);
      return;
    }
    if (!image) {
      image = new Image();
      image.src = url;
    }
    waits.push(image.decode().then(() => kept.add(url)));
  };
  let how = "painted";
  let fonts = 0;
  let images = 0;
  try {
    await document.fonts.ready;
    fonts = since();
    for (const image of document.images) {
      decode(image.currentSrc || image.src, image);
    }
    for (const element of document.querySelectorAll("*")) {
      const url = /url\("?([^")]*)"?\)/.exec(
        getComputedStyle(element).backgroundImage);
      if (url) decode(url[1]);
    }
    await Promise.allSettled(waits);
    images = since();
  } catch (error) {
    how = "failed";
  }
  if (window.__renderNewest !== number) return;
  window.__renderDecoded = kept;
  try {
    document.querySelectorAll("[data-autona-frame-mark]")
      .forEach((mark) => mark.remove());
    for (const style of __MARK__) {
      const mark = document.createElement("div");
      mark.dataset.autonaFrameMark = String(number);
      for (const name in style) {
        mark.style.setProperty(name, style[name], "important");
      }
      document.documentElement.appendChild(mark);
    }
  } catch (error) {
    how = "failed";
  }
  document.title = "RENDER_PAINTED " + number + " " + how + " fonts="
    + fonts + " images=" + (images - fonts) + "/" + waits.length;
})();"""
    # Takes render %d's frame mark away (_finalize_render).
    UNMARK_SCRIPT = ('document.querySelectorAll('
                     '"[data-autona-frame-mark=\'%d\']")'
                     '.forEach(function (mark) { mark.remove(); })')
    PAINTED_TITLE = "RENDER_PAINTED "
    # Contract 2's start, with the payload's JSON as a string literal and the
    # render's number: the page's autona.js draws it, or (autona.js is not on
    # the page) the title says "missing". Then "AUTONA_RENDER <number> ok"
    # (taken as contract 1's RENDER_READY) or "... failed", when RENDER_RESULT
    # reads what went wrong.
    RENDER_SCRIPT = ('(function (payload, serial) {'
                     ' var autona = window.Autona;'
                     ' if (autona && typeof autona._render === "function") {'
                     ' autona._render(payload, serial); }'
                     ' else { document.title = "AUTONA_RENDER " + serial'
                     ' + " missing"; }'
                     ' })(JSON.parse(%s), %d)')
    RENDERED_TITLE = "AUTONA_RENDER "
    RENDER_RESULT = ('(function (serial) { var autona = window.Autona;'
                     ' return autona && typeof autona._renderResult'
                     ' === "function" ? autona._renderResult(serial) : null;'
                     ' })(%d)')
    # What a contract 2 page that never loaded autona.js is told.
    NO_AUTONA_JS = ("the page did not load ../include/autona.js, which a "
                    "contract 2 template draws with")
    NO_ERRORS_SAID = "autona.js said the render failed and gave no error"

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = _RenderPage(self.LOG_NAME)
        # Parent the view to a hidden host widget. Without a parent, the
        # view is a top-level window; even with WA_DontShowOnScreen the
        # OpenGL init can flash a native window on screen at startup.
        self._host = QWidget()
        self._host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        # Keep this backstage window out of the "last window closed" check,
        # otherwise it prevents the application from quitting.
        self._host.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._host.resize(1920, 1080)
        self._view = QWebEngineView(self._host)
        self._view.setPage(self._page)
        self._view.resize(1920, 1080)
        self._host.show()
        if QGuiApplication.platformName() == "offscreen":
            # Qt's offscreen platform may composite no web page into a
            # widget (it does on a GPU-less CI runner, not on a desktop with
            # one): every picture is then the host widget's background (the
            # theme's bg_primary, 17,19,23, in the app), and the log still
            # says the render completed. Only the tests run there (tests/conftest.py
            # sets QT_QPA_PLATFORM=offscreen, which a process they launch
            # inherits): said once per renderer, so a wrong picture is not
            # taken for the renderer's fault.
            logger.warning(
                f"{self.LOG_NAME}: on Qt's offscreen platform the pictures "
                "may be the window's background, not the page "
                "(QT_QPA_PLATFORM=offscreen)")

        self._configure_view()

        # The catalog's packages of CATALOG_KIND seen so far, by id
        # (_refresh_pages).
        self._pages = {}
        self._template_id = None       # Currently loaded template id
        # What the loaded page was loaded as: its package's contract and its
        # folder's files (_folder_stamp), and whether a render found it to be
        # another page than its package says (no autona.js): any of them
        # changed, the next render loads it again (_stale).
        self._page_contract = None
        self._page_stamp = None
        self._page_stale = False
        self._page_origin = None       # The preview listener it came from
        self._page_loaded = False
        self._load_query = None        # The load on its way (None: none is)
        self._load_number = 0
        self._render_number = 0
        self._active = None            # The ticket being drawn
        self._waiting = []             # Tickets with a taker, in order

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_timeout)

        # Not loadFinished: it does not say which load finished.
        self._page.loadingChanged.connect(self._on_loading_changed)
        self._view.page().titleChanged.connect(self._on_title_changed)

        self._refresh_pages()
        if self.LOAD_AT_START:
            if self.DEFAULT_TEMPLATE_ID in self._pages:
                try:
                    self._load_template(self.DEFAULT_TEMPLATE_ID)
                except PreviewUnavailable:
                    # The first render loads it, or is refused.
                    logger.warning(f"{self.LOG_NAME}: no preview listener "
                                   "to load the default template from yet")
            else:
                # The first render loads what it can, or fails to start.
                logger.warning(f"{self.LOG_NAME}: the default template "
                               f"'{self.DEFAULT_TEMPLATE_ID}' is not there")

    # ── what a subclass may say otherwise ────────────────────────────────

    def _configure_view(self):
        """Before the first page is loaded: what a template may do (ADR-007
        D4). A subclass that says more calls this first."""
        settings = self._view.settings()
        attribute = QWebEngineSettings.WebAttribute
        for name in ("LocalContentCanAccessFileUrls",
                     "LocalContentCanAccessRemoteUrls",
                     "JavascriptCanOpenWindows", "JavascriptCanAccessClipboard",
                     "JavascriptCanPaste", "AllowWindowActivationFromJavaScript",
                     "PluginsEnabled", "ScreenCaptureEnabled",
                     "HyperlinkAuditingEnabled", "AutoLoadIconsForPage",
                     "NavigateOnDropEnabled", "AllowRunningInsecureContent"):
            settings.setAttribute(getattr(attribute, name), False)

    @staticmethod
    def _preview_origin():
        """(host, port) of the preview listener a page is loaded from now,
        or None while there is none (WebServer.PreviewPort())."""
        port = WebServer.PreviewPort()
        return (PREVIEW_HOST, port) if port else None

    def _size(self, template_id) -> tuple:
        """The template's picture, (width, height): its package's size."""
        page = self._pages.get(template_id)
        return tuple(page["size"]) if page is not None else (1920, 1080)

    def _resolve_template_id(self, template) -> str:
        raise NotImplementedError

    # ── the templates ────────────────────────────────────────────────────

    @staticmethod
    def _layout_root():
        # The catalog's folder (LayoutCatalog.Root), the one the services
        # read: under the directory the app started in (AutonaDirHelper's,
        # given by bootstrap_core() and read now: the tests point it at their
        # runtime), not the process's current one, which nothing of ours
        # changes and still was, for a moment, site-packages/PySide6 or the
        # directory pytest started in (in test runs, near a first WebEngine
        # page; what moves it is not known): the packages listed then were
        # none, or the shipped ones. Not AutonaResolve(): layout/ is not in
        # the bundle. The page itself is the preview listener's to serve
        # (_page_url), from the same layout/ under the app's directory.
        return LayoutCatalog.Root()

    def _refresh_pages(self):
        """What the catalog lists now, over what it listed before. A page
        of a package whose manifest now says another size is drawn at it
        (_fit_view resizes the view for it)."""
        self._pages.update(LayoutCatalog.Templates(
            self.CATALOG_KIND, self._layout_root()))

    def _template_for(self, asked) -> str:
        """The template a render of `asked` (an id) draws: itself when the
        catalog lists it, or did; else the default; else `asked`, whose load
        cannot start (_load_template)."""
        self._refresh_pages()
        if isinstance(asked, str) and asked in self._pages:
            return asked
        if self.DEFAULT_TEMPLATE_ID in self._pages:
            return self.DEFAULT_TEMPLATE_ID
        return asked

    # ── the renders ──────────────────────────────────────────────────────

    def _load_template(self, template_id: str):
        """Load (or reload) the HTML for the given template id. LookupError
        when the catalog has never listed it; PreviewUnavailable when there
        is no preview listener to load it from (nothing changes then)."""
        page = self._pages.get(template_id) \
            if isinstance(template_id, str) else None
        if page is None:
            raise LookupError(
                f"there is no {self.LOG_NAME.lower()} template "
                f"{template_id!r}")
        origin = self._preview_origin()
        if origin is None:
            raise PreviewUnavailable(
                f"no preview listener to load the {self.LOG_NAME.lower()} "
                "template from")
        url = self._page_url(origin, page)
        # The page does not read it: it tells this load from the one it
        # replaces, which may be the same page.
        self._load_number += 1
        self._load_query = f"load={self._load_number}"
        url.setQuery(self._load_query)
        logger.info(
            f"Loading {self.LOG_NAME.lower()} template '{template_id}': "
            f"{url.toString()}")
        self._template_id = template_id
        self._page_origin = origin
        self._page_contract = page.get("contract", TEMPLATE_CONTRACT)
        self._page_stamp = self._folder_stamp(page)
        self._page_stale = False
        self._page_loaded = False
        self._page.loading(origin, url)
        self._view.resize(*self._size(template_id))
        self._view.load(url)

    def _folder_stamp(self, page):
        """The package folder's own files as they are now: (name, size,
        modification time) of each, or None when it cannot be read. One
        directory listing (not include/: the renderer's profile caches
        nothing, so a load reads include/ afresh anyway). Not settings.json:
        the page does not read it, and what of it matters is the catalog's
        entry (the contract: _stale; the size: _fit_view)."""
        try:
            folder = os.path.join(self._layout_root(), page["folder"])
            with os.scandir(folder) as entries:
                return frozenset(
                    (entry.name, status.st_size, status.st_mtime_ns)
                    for entry in entries
                    if entry.is_file() and entry.name != "settings.json"
                    for status in (entry.stat(),))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def _stale(self, template_id):
        """Whether the loaded page (of `template_id`) is not what its package
        is now: a render found no autona.js on a contract 2 page, its
        manifest says another contract, or its folder's files changed (an
        edit: the preview in the page draws the new files, Generate would
        draw the page loaded before)."""
        if self._page_stale:
            return True
        page = self._pages.get(template_id)
        if page is None:
            return False
        return page.get("contract", TEMPLATE_CONTRACT) \
            != self._page_contract \
            or self._folder_stamp(page) != self._page_stamp

    @staticmethod
    def _page_url(origin, page):
        """The URL of a package's entry page on the preview listener at
        ``origin``: /layout/<folder>/<entry>, each part percent-encoded (a
        folder's name may be Japanese, or hold a space). What the listener
        serves of it is the layout/ folder of the app's directory, as the
        catalog's (the server's working directory)."""
        host, port = origin
        # The listener's URL path, not LAYOUT_ROOT (a folder, which a test
        # may make absolute).
        parts = ["layout"] + page["folder"].split("/") \
            + [page["entry"]]
        return QUrl(f"http://{host}:{port}/"
                    + "/".join(quote(part, safe="") for part in parts))

    def _submit(self, payload, templateId, done):
        """Queue a render whose result only `done(pixmap, error)` gets, later
        on this thread; `pixmap` is None when it failed (`error` says why).
        Never waits, and is never drawn over: one in progress ends first."""
        self._waiting.append(_Ticket(payload, templateId, done))
        self._pump()

    def _pump(self):
        """Start the next render, if none is in progress."""
        if self._active is not None or not self._waiting:
            return
        ticket = self._waiting.pop(0)
        self._active = ticket
        ticket.begun = time.monotonic()
        self._watchdog.start(self.STAGE_TIMEOUT_MS)
        try:
            if ticket.templateId != self._template_id or not (
                    self._page_loaded or self._load_query is not None) \
                    or self._page_origin != self._preview_origin() \
                    or self._stale(ticket.templateId):
                # Another template's, or its load failed, or it came from a
                # preview listener that is not the one serving now (none:
                # the load refuses), or it is not its package's page any
                # more (_stale): this one tries again.
                ticket.ownLoad = True
                self._load_template(ticket.templateId)
            elif self._page_loaded:
                self._start_render(ticket)
            # Otherwise its page is on its way: _on_loading_changed starts it.
        except Exception as error:
            self._could_not_start(ticket, error)

    def _could_not_start(self, ticket, error):
        """A render that will never end: its taker hears of it like of any
        other failure (later, on this thread), and only from here. Raised to
        whoever asked as well, the same render would end twice."""
        if isinstance(error, PreviewUnavailable):
            # Not a fault of the render's: said in its fixed code.
            logger.error(f"A {self.LOG_NAME.lower()} render was refused: "
                         f"{error}")
            why = PREVIEW_UNAVAILABLE
        else:
            logger.opt(exception=error).error(
                f"A {self.LOG_NAME.lower()} render could not be started")
            why = "the render could not be started"
        QTimer.singleShot(0, lambda: ticket is self._active and self._finish(
            ticket, None, why))

    def _fit_view(self, template_id):
        """The view at the template's size: its manifest's may say another
        one than when its page was loaded (_refresh_pages), and a picture of
        another size would be scaled to it, not drawn at it."""
        width, height = self._size(template_id)
        if (self._view.width(), self._view.height()) != (width, height):
            self._view.resize(width, height)

    def _start_render(self, ticket):
        ticket.started = True
        self._render_number += 1
        ticket.number = self._render_number
        self._watchdog.start(self.STAGE_TIMEOUT_MS)
        self._fit_view(ticket.templateId)

        # What the preview listener does not serve, held until _finish.
        payload, ticket.held = RenderAssets.ForRender(ticket.payload)
        # JSON of a string is a JS string literal: nothing in a tag is code.
        # (It was a template literal, where "${...}" in a tag ran as script.)
        literal = json.dumps(json.dumps(payload, ensure_ascii=False))

        page = self._pages.get(ticket.templateId) or {}
        ticket.contract = page.get("contract", TEMPLATE_CONTRACT)
        self._view.page().runJavaScript('document.title = "LOADING"')
        if ticket.contract == RENDER_CONTRACT:
            self._view.page().runJavaScript(
                self.RENDER_SCRIPT % (literal, ticket.number))
        else:
            self._view.page().runJavaScript(
                f"{self.INIT_FUNCTION}(JSON.parse({literal}))")

    def _on_loading_changed(self, info):
        status = info.status()
        if status == QWebEngineLoadingInfo.LoadStatus.LoadStartedStatus:
            return
        if info.url().query() != self._load_query:
            # A load that was replaced: its document is not (or not for long)
            # the one a render would be given.
            return

        self._load_query = None
        if status != QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus:
            logger.error(f"Failed to load {self.LOG_NAME.lower()} HTML template")
            ticket = self._active
            if ticket is not None and not ticket.started \
                    and not ticket.ownLoad:
                # It came while the load was on its way, begun for another: it
                # has not tried yet, and only its watchdog would end it, a
                # whole timeout later. Out of the signal: a load grabs the view.
                QTimer.singleShot(0, self, lambda: self._load_again(ticket))
            # Otherwise the render waiting for it is the watchdog's to end (a
            # page that is not there is not loaded over and over), and the
            # next one tries again (_pump).
            return

        logger.info(
            f"{self.LOG_NAME} HTML template loaded (one-time init complete)")
        self._page_loaded = True

        ticket = self._active
        if ticket is not None and not ticket.started \
                and ticket.templateId == self._template_id:
            try:
                self._start_render(ticket)
            except Exception as error:
                self._could_not_start(ticket, error)

    def _load_again(self, ticket):
        """The try of a render whose page was on its way when it came, begun
        for another, and failed (_on_loading_changed)."""
        if ticket is not self._active:
            return  # Its watchdog ended it meanwhile.
        ticket.ownLoad = True
        try:
            self._load_template(ticket.templateId)
        except Exception as error:
            self._could_not_start(ticket, error)

    def _on_title_changed(self, title: str):
        # The ticket stays active during the waits, so concurrent requests
        # queue instead of starting immediately.
        ticket = self._active
        if ticket is None or not ticket.started:
            return
        if ticket.contract == RENDER_CONTRACT:
            if title.startswith(self.RENDERED_TITLE):
                self._rendered(ticket, title[len(self.RENDERED_TITLE):])
                return
            # RENDER_READY is contract 1's: such a page's title says nothing.
        elif title == "RENDER_READY":
            if not ticket.ready:
                ticket.ready = True
                ticket.readyAt = time.monotonic()
                self._wait_for_paint(ticket)
            return
        if title.startswith(self.PAINTED_TITLE):
            number, _, said = title[len(self.PAINTED_TITLE):].partition(" ")
            how, _, details = said.partition(" ")
            if ticket.ready and number == str(ticket.number):
                self._painted(ticket, how, details)
            # Otherwise another render's, which this one is not.

    def _rendered(self, ticket, said):
        """A contract 2 page said its render is over: "<number> ok" is
        contract 1's RENDER_READY; "<number> failed" (or "missing": no
        autona.js) ends the render with no picture and the template's errors
        (TemplateError)."""
        number, _, how = said.partition(" ")
        if ticket.ready or number != str(ticket.number):
            return  # Said already, or another render's.
        ticket.ready = True
        ticket.readyAt = time.monotonic()
        if how == "ok":
            self._wait_for_paint(ticket)
            return
        if how == "missing":
            # Not the page its package says (moved to contract 2 since it
            # was loaded, or broken): the next render loads it again.
            self._page_stale = True
            # Out of the signal: _finish may load the next render's page.
            errors = [{"kind": "autona", "message": self.NO_AUTONA_JS}]
            QTimer.singleShot(0, lambda: self._template_failed(ticket, errors))
            return
        try:
            self._view.page().runJavaScript(
                self.RENDER_RESULT % ticket.number,
                lambda result: QTimer.singleShot(
                    0, lambda: self._template_said(ticket, result)))
        except Exception:
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: could not read the template's errors")
            QTimer.singleShot(0, lambda: self._template_failed(ticket, []))

    @staticmethod
    def _said_errors(result):
        """The errors in Autona._renderResult's ``result`` (JSON, or None):
        a list, empty when it says none (or nothing)."""
        try:
            said = json.loads(result) if isinstance(result, str) else None
        except ValueError:
            said = None
        if isinstance(said, dict) and isinstance(said.get("errors"), list):
            return said["errors"]
        return []

    def _template_said(self, ticket, result):
        """The errors in Autona._renderResult's ``result`` (JSON, or None)."""
        self._template_failed(ticket, self._said_errors(result))

    def _template_error(self, errors):
        """The TemplateError of `errors`, logged (counts and kinds only: a
        message may hold a player's name or a path)."""
        error = TemplateError(errors)
        if not error.errors:
            error = TemplateError([{"kind": "autona",
                                    "message": self.NO_ERRORS_SAID}])
        kinds = sorted({item["kind"] for item in error.errors})
        logger.error(
            f"{self.LOG_NAME}: the template's drawing failed: "
            f"{len(error.errors)} error(s) ({', '.join(kinds)})")
        return error

    def _template_failed(self, ticket, errors):
        if ticket is not self._active:
            return  # It timed out meanwhile.
        self._finish(ticket, None, self._template_error(errors))

    def _wait_for_paint(self, ticket):
        QTimer.singleShot(self.PAINT_WAIT_MS,
                          lambda: self._painted(ticket, "late"))
        try:
            self._view.page().runJavaScript(self._paint_script(ticket.number))
            ticket.marked = True
        except Exception:
            # Nobody will say it painted, nor mark the frame: the picture as
            # it is, as before.
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: could not wait for the page to paint")
            self._painted(ticket, "failed")

    def _painted(self, ticket, how, details=""):
        if ticket is not self._active or ticket.painted:
            return  # Said already, or the render is over.
        ticket.painted = True
        ticket.paintedAt = time.monotonic()
        ticket.paintDetails = details
        if how != "painted":
            logger.warning(
                f"{self.LOG_NAME}: the page did not say it painted ({how}); "
                "looking at the view all the same")
        # Out of the signal: the grab renders the view.
        QTimer.singleShot(0, lambda: self._finalize_render(ticket))

    def _paint_script(self, number):
        """PAINT_SCRIPT for render `number`, with its frame mark's blocks."""
        return (self.PAINT_SCRIPT % number).replace(
            "__MARK__", json.dumps(self._mark_blocks(number)))

    @classmethod
    def _mark_colors(cls, number):
        """The frame mark of render `number`, its two blocks left to right:
        (r, g, b) each. The second tells it from the marks of the renders
        just before it."""
        return ((255, 0, 255), (0, 255, (0, 85, 170, 255)[number % 4]))

    @classmethod
    def _mark_blocks(cls, number):
        """The CSS of render `number`'s frame mark's blocks, as PAINT_SCRIPT
        sets it on each (the one definition of what it draws)."""
        size = cls.MARK_SIZE
        return [dict(cls.MARK_STYLE, left=f"{block * size}px",
                     width=f"{size}px", height=f"{size}px",
                     background=f"rgb({r}, {g}, {b})")
                for block, (r, g, b) in enumerate(cls._mark_colors(number))]

    @classmethod
    def _mark_rect(cls):
        """The view's corner the frame mark is in (its pixels)."""
        return QRect(0, 0, 2 * cls.MARK_SIZE, cls.MARK_SIZE)

    @classmethod
    def _marked(cls, image, number, width):
        """Whether `image`, a grab of the view from its top left corner,
        `width` of the view's pixels wide (the image's own may be device
        pixels: a screen scale), shows render `number`'s frame mark."""
        if image.isNull() or width <= 0:
            return False
        scale = image.width() / width
        for block, rgb in enumerate(cls._mark_colors(number)):
            x = int((block + 0.5) * cls.MARK_SIZE * scale)
            y = int(0.5 * cls.MARK_SIZE * scale)
            if x >= image.width() or y >= image.height():
                return False
            color = image.pixelColor(x, y)
            if any(abs(have - want) > cls.MARK_TOLERANCE for have, want in
                   zip((color.red(), color.green(), color.blue()), rgb)):
                return False
        return True

    def _frame_wait_ms(self):
        """The wait for the frame (_finalize_render): FRAME_WAIT_MS, or what
        the watchdog leaves of the stage less FRAME_RESERVE_MS."""
        left = self._watchdog.remainingTime()
        if left < 0:
            return self.FRAME_WAIT_MS
        return max(0, min(self.FRAME_WAIT_MS, left - self.FRAME_RESERVE_MS))

    def _finalize_render(self, ticket):
        """Look at the view's corner until it shows the page's frame mark,
        take the mark away, and look until it is gone; then the view is the
        picture (see the class's notes). A page not asked to mark it: the
        view as it is."""
        if ticket is not self._active:
            return  # It timed out meanwhile.

        if ticket.marked:
            now = time.monotonic()
            if ticket.frameDeadline is None:
                ticket.frameDeadline = now + self._frame_wait_ms() / 1000
            waiting = now < ticket.frameDeadline
            rect = self._mark_rect()
            shows = self._marked(self._view.grab(rect).toImage(),
                                 ticket.number, rect.width())
            if not ticket.markSeen:
                if shows:
                    ticket.markSeen = True
                    self._unmark(ticket)
                if shows or waiting:
                    self._look_again(ticket)
                    return
                logger.warning(
                    f"{self.LOG_NAME}: the drawing did not reach the view "
                    "(its frame mark was not seen); taking the picture as it "
                    "is")
                self._unmark(ticket)
            elif shows:
                if waiting:
                    self._look_again(ticket)
                    return
                logger.warning(
                    f"{self.LOG_NAME}: the page's frame mark did not go; "
                    "taking the picture as it is")

        pixmap = self._view.grab()
        if ticket.marked:
            pixmap = self._without_mark(pixmap, ticket.number)

        width, height = self._size(ticket.templateId)
        if pixmap.width() != width or pixmap.height() != height:
            pixmap = pixmap.scaled(
                width, height, self.ASPECT_MODE,
                Qt.TransformationMode.SmoothTransformation)

        if ticket.contract == RENDER_CONTRACT:
            # An error after the page said "ok" (a timer, a rAF, a promise
            # its drawing did not wait for) is the render's too: read again
            # now the picture is taken.
            self._confirm_drawn(ticket, pixmap)
            return
        self._drawn(ticket, pixmap)

    def _without_mark(self, pixmap, number):
        """`pixmap`, the picture, with no frame mark in it: one taken when
        the wait ran out may still show it, and then the mark's pixels are
        the picture's own just under them (what the page has under the mark
        is nowhere to be had)."""
        width = self._view.width()
        image = pixmap.toImage()
        if not self._marked(image, number, width):
            return pixmap
        scale = image.width() / width
        mark = QRect(0, 0, math.ceil(2 * self.MARK_SIZE * scale),
                     math.ceil(self.MARK_SIZE * scale))
        ratio = image.devicePixelRatio()
        image.setDevicePixelRatio(1)
        under = image.copy(mark.translated(0, mark.height()))
        painter = QPainter(image)
        painter.drawImage(mark.topLeft(), under)
        painter.end()
        image.setDevicePixelRatio(ratio)
        return QPixmap.fromImage(image)

    def _look_again(self, ticket):
        QTimer.singleShot(self.FRAME_POLL_MS, self,
                          lambda: self._finalize_render(ticket))

    def _unmark(self, ticket):
        try:
            self._view.page().runJavaScript(
                self.UNMARK_SCRIPT % ticket.number)
        except Exception:
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: could not take the frame mark away")

    def _drawn(self, ticket, pixmap):
        logger.info(
            f"{self.LOG_NAME} render complete: {self._timings(ticket)}")
        self._finish(ticket, pixmap, "")

    def _confirm_drawn(self, ticket, pixmap):
        """Contract 2: the picture is the render's unless its page caught an
        error since it said "ok" (Autona._renderResult read again). No
        answer: the watchdog ends it."""
        def said(result):
            if ticket is not self._active:
                return  # It timed out meanwhile.
            errors = self._said_errors(result)
            if errors:
                self._template_failed(ticket, errors)
            else:
                self._drawn(ticket, pixmap)

        try:
            self._view.page().runJavaScript(
                self.RENDER_RESULT % ticket.number,
                lambda result: QTimer.singleShot(0, lambda: said(result)))
        except Exception:
            logger.opt(exception=True).warning(
                f"{self.LOG_NAME}: could not read the template's errors "
                "again; taking the picture")
            self._drawn(ticket, pixmap)

    @staticmethod
    def _timings(ticket):
        """Where a render's time went: its page (a load, when there was one,
        and the template's script), the wait for the paint (the page's own
        account of it), and the picture (the waits for the frame mark and
        for its going, and the grab)."""
        def ms(since, until):
            if since is None or until is None:
                return "?"
            return str(round((until - since) * 1000))

        now = time.monotonic()
        return (f"{ms(ticket.begun, now)} ms "
                f"(page {ms(ticket.begun, ticket.readyAt)}, "
                f"painted {ms(ticket.readyAt, ticket.paintedAt)} "
                f"[{ticket.paintDetails or '-'}], "
                f"taken {ms(ticket.paintedAt, now)})")

    def _on_timeout(self):
        ticket = self._active
        if ticket is None:
            return
        if ticket.started and ticket.contract == RENDER_CONTRACT \
                and not ticket.timedOut:
            # What its page caught so far may say why it did not end (an
            # error it is still waiting past): read it, a moment at most --
            # a page busy for good never answers, and the watchdog comes
            # again.
            ticket.timedOut = True
            self._watchdog.start(self.TIMEOUT_READ_MS)
            try:
                self._view.page().runJavaScript(
                    self.RENDER_RESULT % ticket.number,
                    lambda result: QTimer.singleShot(
                        0, lambda: self._timed_out(ticket, result)))
            except Exception:
                self._timed_out(ticket, None)
            return
        self._timed_out(ticket, None)

    def _timed_out(self, ticket, result):
        """End the active render `ticket`, whose time is over: with the
        errors its page caught (`result`, Autona._renderResult's: contract
        2), or as timed out."""
        if ticket is not self._active:
            return  # Ended meanwhile (an answer after the second timeout).
        errors = self._said_errors(result)
        logger.error(f"{self.LOG_NAME} render timed out")
        if ticket.started:
            # Abandon its document: whatever its script still does, it must
            # not say RENDER_READY during the next render.
            try:
                self._load_template(self._template_id)
            except PreviewUnavailable:
                # The next render loads it, or is refused (_pump).
                self._page_loaded = False
                self._load_query = None
                logger.warning(f"{self.LOG_NAME}: no preview listener to "
                               "load the page again from")
        # Otherwise no script ran, and the page goes on loading for the next.
        if errors:
            self._finish(ticket, None, self._template_error(errors))
            return
        self._finish(ticket, None, "the render timed out")

    def _finish(self, ticket, pixmap, error):
        self._active = None
        self._watchdog.stop()
        RenderAssets.Release(ticket.held)
        ticket.held = []
        try:
            ticket.done(pixmap, error)
        except Exception:
            logger.exception(f"A {self.LOG_NAME.lower()} render's taker failed")
        self._pump()
