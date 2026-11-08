import os
import json
import time

from qtpy.QtCore import QObject, QUrl, QEventLoop, QTimer, Signal, Qt
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import QApplication, QWidget
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import (
    QWebEngineLoadingInfo, QWebEngineSettings, QWebEnginePage)
from loguru import logger


class _ThumbnailPage(QWebEnginePage):
    """Custom page to capture JS console messages."""

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        source = sourceID.split("/")[-1] if sourceID else "unknown"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error(f"[Thumbnail JS] {source}:{lineNumber} {message}")
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning(f"[Thumbnail JS] {source}:{lineNumber} {message}")
        else:
            logger.debug(f"[Thumbnail JS] {source}:{lineNumber} {message}")

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"


class _Ticket:
    """One render: what to draw and who wants it."""
    __slots__ = ("payload", "templateId", "done", "announce", "started",
                 "number", "ready", "painted", "frameDeadline", "begun",
                 "readyAt", "paintedAt", "paintDetails")

    def __init__(self, payload, templateId, done, announce):
        self.payload = payload
        self.templateId = templateId
        # done(pixmap, error): the image, or None and why.
        self.done = done
        # Whether renderComplete is emitted for it.
        self.announce = announce
        # Its script is running in the page; under this number.
        self.started = False
        self.number = 0
        # The page said RENDER_READY, and then that it painted.
        self.ready = False
        self.painted = False
        # Until when a frame of the page before this one is waited out.
        self.frameDeadline = None
        # When it started, said RENDER_READY and painted (time.monotonic()),
        # and what the page said of its wait: for the log.
        self.begun = self.readyAt = self.paintedAt = None
        self.paintDetails = ""


class ThumbnailRenderer(QObject):
    """Renders thumbnail HTML templates to images using QWebEngineView.

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
      and stay as they are), to say RENDER_PAINTED <its number> once the
      fonts are in, the images decoded and two frames have begun -- so the
      first of them, which has the drawing, was submitted. Bounded by
      PAINT_WAIT_MS, in case frames stop coming. The number is the render's,
      so a page that says it for another render is not believed.
    - On a page just loaded, a grab that is still what the view showed
      before the load is the old page's: it is taken again until it is not,
      for at most FRESH_FRAME_WAIT_MS. A render on the same page is never
      compared with anything, so drawing the same picture again costs no
      wait.

    Three ways in, all on the GUI thread:
    - render(): the panel's. Only the newest of these is drawn; the result
      is the renderComplete signal.
    - renderAsync(): core/ThumbnailService's. The result goes to its callback
      and to nobody else (renderComplete would change the panel's preview).
    - renderSync(): SCORE's. Waits in a nested event loop; renderComplete too.
    """

    # Maps template id -> subdirectory under layout/ (each contains thumbnail.html).
    # Both templates currently share the Center Panel design as a starting
    # point; they are crafted into distinct designs over time.
    TEMPLATE_SUBDIRS = {
        "type_a": "thumbnail",
        "type_b": "thumbnail_type_b",
    }
    DEFAULT_TEMPLATE_ID = "type_a"

    # How long a render may wait for its page, and then for its script (each).
    STAGE_TIMEOUT_MS = 10000
    # How long the page may take to say it painted (PAINT_SCRIPT). The picture
    # is taken then all the same, and a warning logged. Timed here, not in
    # the page: a page whose frames stop may have its timers held back too.
    PAINT_WAIT_MS = 1000
    # How long a page just loaded may go on showing the page before it, and
    # how often it is looked at meanwhile (_finalize_render).
    FRESH_FRAME_WAIT_MS = 1000
    FRESH_FRAME_POLL_MS = 30
    # Run in the page once it said RENDER_READY, with the render's number:
    # says "RENDER_PAINTED <number> painted" once the fonts are loaded, the
    # images (<img> and CSS backgrounds) decoded and two frames have begun
    # since, "... failed" when it cannot tell; then how long each took, for
    # the log ("fonts=<ms> images=<ms>/<decoded> frames=<ms>").
    # An image the render before decoded on this document is not decoded
    # again: it is the same image (a template draws the same background, the
    # same characters, again), and asking again repeats the work. Only the
    # render before's are remembered, so they do not pile up with every
    # character drawn (those are data: URLs, made per render). `complete` is
    # not the test: an image can be loaded and not decoded.
    PAINT_SCRIPT = r"""(async () => {
  const start = performance.now();
  const since = () => Math.round(performance.now() - start);
  const before = window.__thumbnailDecoded || new Set();
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
    await new Promise((done) =>
      requestAnimationFrame(() => requestAnimationFrame(done)));
  } catch (error) {
    how = "failed";
  }
  window.__thumbnailDecoded = kept;
  document.title = "RENDER_PAINTED %d " + how + " fonts=" + fonts
    + " images=" + (images - fonts) + "/" + waits.length
    + " frames=" + (since() - images);
})();"""
    PAINTED_TITLE = "RENDER_PAINTED "

    renderComplete = Signal(QPixmap)

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = _ThumbnailPage()
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

        # Enable local file access
        settings = self._view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        # Transparent background
        self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)

        self._template_id = None       # Currently loaded template id
        self._page_loaded = False
        self._load_query = None        # The load on its way (None: none is)
        self._load_number = 0
        # What the view showed before the page was loaded, until a picture
        # of the page has been taken (None: one has).
        self._before_load = None
        self._render_number = 0
        self._active = None            # The ticket being drawn
        self._waiting = []             # Tickets with a taker, in order
        self._latest = None            # The newest render() while one runs

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_timeout)

        # Not loadFinished: it does not say which load finished.
        self._page.loadingChanged.connect(self._on_loading_changed)
        self._view.page().titleChanged.connect(self._on_title_changed)

        # Load default template at init
        self._load_template(self.DEFAULT_TEMPLATE_ID)

    def _resolve_template_id(self, template: dict) -> str:
        tid = (template or {}).get("template_id")
        if tid not in self.TEMPLATE_SUBDIRS:
            return self.DEFAULT_TEMPLATE_ID
        return tid

    def _load_template(self, template_id: str):
        """Load (or reload) the HTML for the given template id."""
        subdir = self.TEMPLATE_SUBDIRS.get(
            template_id, self.TEMPLATE_SUBDIRS[self.DEFAULT_TEMPLATE_ID])
        html_path = os.path.abspath(
            os.path.join(os.getcwd(), "layout", subdir, "thumbnail.html"))
        url = QUrl.fromLocalFile(html_path)
        # The page does not read it: it tells this load from the one it
        # replaces, which may be the same page.
        self._load_number += 1
        self._load_query = f"load={self._load_number}"
        url.setQuery(self._load_query)
        logger.info(
            f"Loading thumbnail template '{template_id}': {url.toString()}")
        self._template_id = template_id
        self._page_loaded = False
        # The view goes on showing this until the new page's first frame.
        self._before_load = self._view.grab().toImage()
        self._view.load(url)

    def _ticket(self, data, config, template, done, announce) -> _Ticket:
        payload = {"data": data, "config": config, "template": template}
        return _Ticket(payload, self._resolve_template_id(template), done,
                       announce)

    def render(self, data: dict, config: dict, template: dict):
        """Start async render. Result emitted via renderComplete signal.

        If called while a render is in progress, the new payload replaces
        the queued one — only the latest data is rendered.
        """
        self._latest = self._ticket(data, config, template, None, True)
        self._pump()

    def renderAsync(self, data: dict, config: dict, template: dict, done):
        """Start a render whose result only `done(pixmap, error)` gets, later
        on this thread; `pixmap` is None when it failed (`error` says why).
        Never waits, and is never drawn over: one in progress ends first."""
        self._waiting.append(self._ticket(data, config, template, done, False))
        self._pump()

    def renderSync(self, data: dict, config: dict, template: dict) -> QPixmap:
        """Synchronous render. Blocks until result is ready.

        Only use for user-initiated final generation (no re-entrancy risk).
        Page is already loaded so this blocks for well under a second, plus
        what a render in progress has left: it ends first.
        """
        outcome = []
        loop = QEventLoop()

        def done(pixmap, _error):
            outcome.append(pixmap)
            loop.quit()

        self._waiting.append(self._ticket(data, config, template, done, True))
        self._pump()
        if not outcome:
            loop.exec()
        return outcome[0] if outcome and outcome[0] is not None else QPixmap()

    def _pump(self):
        """Start the next render, if none is in progress."""
        if self._active is not None:
            return
        if self._waiting:
            ticket = self._waiting.pop(0)
        elif self._latest is not None:
            ticket, self._latest = self._latest, None
        else:
            return
        self._active = ticket
        ticket.begun = time.monotonic()
        self._watchdog.start(self.STAGE_TIMEOUT_MS)
        try:
            if ticket.templateId != self._template_id or not (
                    self._page_loaded or self._load_query is not None):
                # Another template's, or its load failed: this one tries again.
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
        logger.opt(exception=error).error(
            "A thumbnail render could not be started")
        QTimer.singleShot(0, lambda: ticket is self._active and self._finish(
            ticket, None, "the render could not be started"))

    def _start_render(self, ticket):
        ticket.started = True
        self._render_number += 1
        ticket.number = self._render_number
        self._watchdog.start(self.STAGE_TIMEOUT_MS)

        # JSON of a string is a JS string literal: nothing in a tag is code.
        # (It was a template literal, where "${...}" in a tag ran as script.)
        literal = json.dumps(json.dumps(ticket.payload, ensure_ascii=False))

        self._view.page().runJavaScript('document.title = "LOADING"')
        self._view.page().runJavaScript(f"initThumbnail(JSON.parse({literal}))")

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
            # The render waiting for it is the watchdog's to end, and the next
            # one tries again (_pump).
            logger.error("Failed to load thumbnail HTML template")
            return

        logger.info("Thumbnail HTML template loaded (one-time init complete)")
        self._page_loaded = True

        ticket = self._active
        if ticket is not None and not ticket.started \
                and ticket.templateId == self._template_id:
            try:
                self._start_render(ticket)
            except Exception as error:
                self._could_not_start(ticket, error)

    def _on_title_changed(self, title: str):
        # The ticket stays active during the waits, so concurrent requests
        # queue instead of starting immediately.
        ticket = self._active
        if ticket is None or not ticket.started:
            return
        if title == "RENDER_READY":
            if not ticket.ready:
                ticket.ready = True
                ticket.readyAt = time.monotonic()
                self._wait_for_paint(ticket)
        elif title.startswith(self.PAINTED_TITLE):
            number, _, said = title[len(self.PAINTED_TITLE):].partition(" ")
            how, _, details = said.partition(" ")
            if ticket.ready and number == str(ticket.number):
                self._painted(ticket, how, details)
            # Otherwise another render's, which this one is not.

    def _wait_for_paint(self, ticket):
        QTimer.singleShot(self.PAINT_WAIT_MS,
                          lambda: self._painted(ticket, "late"))
        try:
            self._view.page().runJavaScript(
                self.PAINT_SCRIPT % ticket.number)
        except Exception:
            # Nobody will say it painted: the picture as it is, as before.
            logger.opt(exception=True).warning(
                "Thumbnail: could not wait for the page to paint")
            self._painted(ticket, "failed")

    def _painted(self, ticket, how, details=""):
        if ticket is not self._active or ticket.painted:
            return  # Said already, or the render is over.
        ticket.painted = True
        ticket.paintedAt = time.monotonic()
        ticket.paintDetails = details
        if how != "painted":
            logger.warning(
                f"Thumbnail: the page did not say it painted ({how}); "
                "taking the picture as it is")
        # Out of the signal: the grab renders the view.
        QTimer.singleShot(0, lambda: self._finalize_render(ticket))

    def _finalize_render(self, ticket):
        if ticket is not self._active:
            return  # It timed out meanwhile.

        pixmap = self._view.grab()
        if self._before_load is not None:
            # The page's first picture: not while the view still shows the
            # page before it.
            if ticket.frameDeadline is None:
                ticket.frameDeadline = time.monotonic() \
                    + self.FRESH_FRAME_WAIT_MS / 1000
            if pixmap.toImage() == self._before_load:
                if time.monotonic() < ticket.frameDeadline:
                    QTimer.singleShot(self.FRESH_FRAME_POLL_MS,
                                      lambda: self._finalize_render(ticket))
                    return
                logger.warning(
                    "Thumbnail: the new page still looks like the one before "
                    "it; taking the picture as it is")
            self._before_load = None

        if pixmap.width() != 1920 or pixmap.height() != 1080:
            pixmap = pixmap.scaled(
                1920, 1080,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        logger.info(f"Thumbnail render complete: {self._timings(ticket)}")
        self._finish(ticket, pixmap, "")

    @staticmethod
    def _timings(ticket):
        """Where a render's time went: its page (a load, when there was one,
        and the template's script), the wait for the paint (the page's own
        account of it), and the picture (the old page's frame waited out,
        and the grab)."""
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
        logger.error("Thumbnail render timed out")
        if ticket.started:
            # Abandon its document: whatever its script still does, it must
            # not say RENDER_READY during the next render.
            self._load_template(self._template_id)
        # Otherwise no script ran, and the page goes on loading for the next.
        self._finish(ticket, None, "the render timed out")

    def _finish(self, ticket, pixmap, error):
        self._active = None
        self._watchdog.stop()
        if ticket.announce and pixmap is not None:
            self.renderComplete.emit(pixmap)
        if ticket.done is not None:
            try:
                ticket.done(pixmap, error)
            except Exception:
                logger.exception("A thumbnail render's taker failed")
        self._pump()
