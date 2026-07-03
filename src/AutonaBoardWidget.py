"""BOARD tab: pick a scoreboard overlay design, preview it live, tune its
colors, and get the OBS browser-source path.

The built-in scoreboard overlays live in ``layout/scoreboard_*/`` and custom
composer projects in ``layout/custom/{slug}/`` (TSH-style HTML/CSS/JS that
reads ``out/`` state and is loaded into OBS as a *local file*). This tab bundles
the operator's "make it look right and get it into OBS" workflow:

* **Design** — a combo over every ``layout/scoreboard*/`` folder that has an
  ``index.html``. The choice persists to ``board.selected_design``.
* **Preview** — an embedded QWebEngineView pointed at the running web server
  (``http://127.0.0.1:{port}/layout/{design}/index.html``). It shares the same
  Socket.IO feed as a real overlay, so score and color edits reflect live.
* **Colors** — theme / accent color pickers (moved here from the EVENT tab).
  Same SettingsManager / StateManager keys, so overlays and ``out/`` export are
  unaffected.
* **Designer** — opens the layer editor in a dedicated desktop window.
* **Add to OBS** — the local file path for the selected design with Copy /
  Open-in-Explorer buttons and the 1920x1080 sizing note.
"""

import os
import sys
import subprocess

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import QWebEngineSettings
from loguru import logger

from .StateManager import StateManager
from .SettingsManager import SettingsManager
from .AutonaColorButton import AutonaColorButton
from .AutonaDesignerWindow import AutonaDesignerWindow
from . import ui_constants as UI
from . import ui_layout as L

# Mirrors the constants the EVENT tab used before the color pickers moved here.
DEFAULT_THEME_COLOR = "#1D1B18"
DEFAULT_ACCENT_COLOR = "#f0a500"

LAYOUT_DIR = "layout"
DEFAULT_DESIGN = "scoreboard_crewbattle"

# Friendlier labels than the prettified folder name where it helps.
DESIGN_DISPLAY_NAMES = {
    "scoreboard_Universal": "Universal",
    "scoreboard_crewbattle": "Crew Battle",
    "scoreboard_simple": "Simple",
    "scoreboard_team": "Team",
    "scoreboard_topleft": "Top Left",
}

# Per-design (match format, supported game) shown beside the picker. Derived
# from each layout's index.js: the index.html designs branch on player count
# (1v1 / team), while crewbattle is the board-3 stock-based crew overlay.
# Edit here when adding a design; unknown designs fall back to "—".
DESIGN_META = {
    "scoreboard_Universal": ("1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_simple": ("1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_topleft": ("1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_team": ("1v1 / チーム", "汎用（全ゲーム）"),
    "scoreboard_crewbattle": ("クルーバトル", "スマブラSP（ストック制）"),
}


def _discover_designs():
    """List built-in and custom folders that have an ``index.html``.

    The legacy ``layout/scoreboard/`` (per-game .html, no single entry) is
    excluded by the index.html check. Composer projects are stored one level
    deeper as ``layout/custom/{slug}/`` and returned with forward slashes so
    the same value works in URLs and filesystem path construction."""
    designs = []
    try:
        base = os.path.abspath(LAYOUT_DIR)
        for name in sorted(os.listdir(base)):
            if not name.startswith("scoreboard"):
                continue
            if os.path.isfile(os.path.join(base, name, "index.html")):
                designs.append(name)

        customBase = os.path.join(base, "custom")
        if os.path.isdir(customBase):
            for slug in sorted(os.listdir(customBase)):
                if os.path.isfile(
                        os.path.join(customBase, slug, "index.html")):
                    designs.append(f"custom/{slug}")
    except OSError as e:
        logger.warning(f"Board: failed to list layout designs: {e}")
    return designs


class _ScoreboardPreview(QFrame):
    """Hosts the preview QWebEngineView and keeps it at a 16:9 letterbox,
    zooming the 1920x1080 overlay down to the panel width.

    Zooming must use setZoomFactor (not a CSS transform): the zoom factor
    keeps the page's LAYOUT viewport at 1920x1080 logical pixels, which
    viewport-relative overlays depend on — a CSS transform shrinks the
    layout viewport itself and breaks them. Chromium resets the zoom
    factor when a page finishes loading (leaving a top-left crop), so it
    is re-applied on loadFinished. Mirrors AutonaTop8Widget._BracketPreview.

    Chromium clamps the zoom factor to [0.25, 5.0], so the view itself is
    never allowed to shrink below 480px (= 1920 * 0.25); when the pane is
    narrower, the view keeps that minimum size inside a scroll area and
    the preview becomes scrollable instead of cropping."""

    # 1920 * Chromium's minimum zoom factor (0.25).
    MIN_VIEW_WIDTH = 480

    def __init__(self, parent=None):
        super().__init__(parent)
        p = UI.palette()
        # No border here — the parent QGroupBox card already draws one;
        # a second border would double-frame the preview.
        self.setStyleSheet(
            f"QFrame {{"
            f"background-color: {p['bg_tertiary']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        self.setMinimumHeight(220)

        self.view = QWebEngineView()
        s = self.view.settings()
        # Match ThumbnailRenderer: harmless over http, but keeps the view happy
        # if a design ever references file:// assets.
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        # Transparent page so the frame's bg_tertiary shows as a neutral
        # backdrop behind overlays that don't paint a full background.
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.view.loadFinished.connect(lambda _ok: self._applyScale())

        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.view)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layoutView()

    def _layoutView(self):
        inset = 1  # small gap from the frame's rounded corners
        w = max(1, self.width() - 2 * inset)
        view_w = max(w, self.MIN_VIEW_WIDTH)
        view_h = int(view_w * 9 / 16)
        self.view.setFixedSize(view_w, view_h)

        extra = (self.scroll.horizontalScrollBar().sizeHint().height()
                 if view_w > w else 0)
        h = min(max(1, self.height() - 2 * inset), view_h + extra)
        # Centre vertically when the frame is taller than the 16:9 box.
        y = max(inset, (self.height() - h) // 2)
        self.scroll.setGeometry(inset, y, w, h)
        self._applyScale()

    def _applyScale(self):
        self.view.setZoomFactor(max(1, self.view.width()) / 1920.0)

    def loadDesign(self, design):
        port = SettingsManager.Get("general.webserver_port", 5000)
        url = f"http://127.0.0.1:{port}/{LAYOUT_DIR}/{design}/index.html"
        self.view.setUrl(QUrl(url))


class AutonaBoardWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)
        # windowTitle is the visible tab label (objectName is set in Autona.py).
        self.setWindowTitle(QApplication.translate("app", "Board"))

        self._designs = _discover_designs()
        self._previewLoaded = False
        self._designerWindow = None
        self._designRefreshTimer = QTimer(self)
        self._designRefreshTimer.setSingleShot(True)
        self._designRefreshTimer.setInterval(750)
        self._designRefreshTimer.timeout.connect(self._refreshDesigns)
        self._designWatcher = QFileSystemWatcher(self)
        self._designWatcher.directoryChanged.connect(
            self._scheduleDesignRefresh)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        outer.setSpacing(UI.PANEL_MARGIN)
        outer.addWidget(self._buildDesignGroup())
        outer.addWidget(self._buildColorGroup())
        outer.addWidget(self._buildPreviewGroup(), 1)
        outer.addWidget(self._buildObsGroup())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setWidget(scroll)
        self._watchDesignDirectories()

        # Seed colors into state at startup (moved from the EVENT tab) so
        # overlays receive theme/accent before the user touches the pickers.
        StateManager.Set(
            "tournamentInfo.themeColor",
            SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR))
        StateManager.Set(
            "tournamentInfo.accentColor",
            SettingsManager.Get("general.accent_color", DEFAULT_ACCENT_COLOR))

        self._refreshObsPath()

    # ── Design selection ─────────────────────────────────────────────
    def _displayName(self, folder):
        if folder in DESIGN_DISPLAY_NAMES:
            return DESIGN_DISPLAY_NAMES[folder]
        if folder.startswith("custom/"):
            name = folder.split("/", 1)[1]
            return f"Custom: {name.replace('_', ' ').replace('-', ' ').title()}"
        name = folder[len("scoreboard"):].lstrip("_") or folder
        return name.replace("_", " ").title()

    def _buildDesignGroup(self):
        # Japanese literals follow the existing hardcoded-JP precedent
        # ("ブラウザ版を開く" in AutonaScoreboardWidget); the runtime reads
        # compiled .qm only, so untranslated translate() calls would show
        # English. The tab title still uses translate() for the short-label.
        group = L.make_card("スコアボードデザイン")
        self.designCombo = QComboBox()
        for d in self._designs:
            self.designCombo.addItem(self._displayName(d), d)
        saved = SettingsManager.Get("board.selected_design", DEFAULT_DESIGN)
        idx = self.designCombo.findData(saved)
        if idx < 0:
            idx = self.designCombo.findData(DEFAULT_DESIGN)
        if idx >= 0:
            self.designCombo.setCurrentIndex(idx)

        # Picker (left half) and the supported format / game (right half),
        # split 50/50 via equal stretch.
        self.designMetaLabel = QLabel()
        self.designMetaLabel.setWordWrap(True)
        self.designMetaLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.designMetaLabel.setStyleSheet(UI.secondary_label_style())
        row = QHBoxLayout()
        row.setSpacing(UI.SPACE_MD)
        row.addWidget(self.designCombo, 1)
        self.btRefreshDesigns = QPushButton("更新")
        self.btRefreshDesigns.setToolTip(
            "保存済みのカスタムデザインを再読み込みします")
        self.btRefreshDesigns.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btRefreshDesigns.clicked.connect(self._refreshDesigns)
        row.addWidget(self.btRefreshDesigns)
        row.addWidget(self.designMetaLabel, 1)
        group.layout().addLayout(row)

        designerRow = QHBoxLayout()
        designerDescription = QLabel(
            "PNGと選手名・スコアを組み合わせて、新しいデザインを作成・編集します。")
        designerDescription.setWordWrap(True)
        designerDescription.setStyleSheet(UI.secondary_label_style())
        designerRow.addWidget(designerDescription, 1)
        self.btOpenDesigner = QPushButton("Designerを開く")
        self.btOpenDesigner.setToolTip(
            "デスクトップウインドウでスコアボードDesignerを開きます")
        self.btOpenDesigner.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btOpenDesigner.clicked.connect(self._openDesigner)
        designerRow.addWidget(self.btOpenDesigner)
        group.layout().addLayout(designerRow)

        # Connect after the initial selection so it doesn't fire on startup.
        self.designCombo.currentIndexChanged.connect(self._onDesignChanged)
        if not self._designs:
            warn = QLabel("layout/ にスコアボードデザインが見つかりません。")
            warn.setWordWrap(True)
            group.layout().addWidget(warn)
        self._refreshDesignMeta()
        return group

    def _designMetaText(self, design):
        if design and design.startswith("custom/"):
            return "対戦形式：1v1\nゲーム：カスタム"
        fmt, game = DESIGN_META.get(design, ("—", "—"))
        return f"対戦形式：{fmt}\nゲーム：{game}"

    def _refreshDesignMeta(self):
        design = self._currentDesign()
        self.designMetaLabel.setText(
            self._designMetaText(design) if design else "")

    def _currentDesign(self):
        if self.designCombo.count() == 0:
            return None
        return self.designCombo.currentData()

    def _onDesignChanged(self, _idx):
        design = self._currentDesign()
        if design:
            SettingsManager.Set("board.selected_design", design)
        self._refreshDesignMeta()
        self._refreshObsPath()
        if self._previewLoaded:
            self._loadPreview()

    def _watchDesignDirectories(self):
        watched = set(self._designWatcher.directories())
        customBase = os.path.abspath(os.path.join(LAYOUT_DIR, "custom"))
        candidates = [
            os.path.abspath(LAYOUT_DIR),
            customBase,
        ]
        if os.path.isdir(customBase):
            try:
                candidates.extend(
                    os.path.join(customBase, slug)
                    for slug in os.listdir(customBase)
                    if os.path.isdir(os.path.join(customBase, slug))
                )
            except OSError as e:
                logger.warning(
                    f"Board: failed to watch custom designs: {e}")
        for path in candidates:
            if os.path.isdir(path) and path not in watched:
                self._designWatcher.addPath(path)

    def _scheduleDesignRefresh(self, _path=None):
        # Saving creates the slug directory before writing index.html. Debounce
        # directory notifications so discovery runs after the atomic writes.
        self._watchDesignDirectories()
        self._designRefreshTimer.start()

    def _refreshDesigns(self):
        self._watchDesignDirectories()
        designs = _discover_designs()
        current = self._currentDesign()
        preferred = current or SettingsManager.Get(
            "board.selected_design", DEFAULT_DESIGN)

        self.designCombo.blockSignals(True)
        try:
            self.designCombo.clear()
            for design in designs:
                self.designCombo.addItem(
                    self._displayName(design), design)

            idx = self.designCombo.findData(preferred)
            if idx < 0:
                idx = self.designCombo.findData(DEFAULT_DESIGN)
            if idx < 0 and self.designCombo.count() > 0:
                idx = 0
            if idx >= 0:
                self.designCombo.setCurrentIndex(idx)
        finally:
            self.designCombo.blockSignals(False)

        self._designs = designs
        selected = self._currentDesign()
        if selected:
            SettingsManager.Set("board.selected_design", selected)
        self._refreshDesignMeta()
        self._refreshObsPath()
        if self._previewLoaded and selected:
            self._loadPreview()

    def _openDesigner(self):
        if self._designerWindow is None:
            self._designerWindow = AutonaDesignerWindow(self)
            self._designerWindow.destroyed.connect(
                lambda: setattr(self, "_designerWindow", None))
        self._designerWindow.show()
        self._designerWindow.raise_()
        self._designerWindow.activateWindow()

    # ── Preview ──────────────────────────────────────────────────────
    def _buildPreviewGroup(self):
        group = L.make_card("プレビュー")
        self.preview = _ScoreboardPreview()
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group.layout().addWidget(self.preview)
        btRow = QHBoxLayout()
        btRow.addStretch(1)
        btReload = QPushButton("プレビューを再読み込み")
        btReload.clicked.connect(self._reloadPreview)
        btRow.addWidget(btReload)
        group.layout().addLayout(btRow)
        return group

    def _loadPreview(self):
        design = self._currentDesign()
        if design:
            self.preview.loadDesign(design)

    def _reloadPreview(self):
        self._previewLoaded = True
        self._loadPreview()

    def showEvent(self, e):
        super().showEvent(e)
        # Lazy-load on first reveal: by the time the user opens this tab the
        # web server is up, so the preview's HTTP fetch + Socket.IO connect.
        if not self._previewLoaded and self._designs:
            self._previewLoaded = True
            self._loadPreview()

    # ── Colors (moved from EVENT) ────────────────────────────────────
    def _buildColorGroup(self):
        group = L.make_card("スコアボードの色")
        # Theme and accent side by side: [label btn]  [label btn].
        row = QHBoxLayout()
        row.setSpacing(UI.SPACE_MD)

        row.addWidget(QLabel("テーマカラー"))
        self.btThemeColor = AutonaColorButton(
            color=SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR))
        self.btThemeColor.setMinimumSize(32, 32)
        self.btThemeColor.colorChanged.connect(self._onThemeColorChanged)
        row.addWidget(self.btThemeColor)

        row.addSpacing(UI.SPACE_LG)

        row.addWidget(QLabel("アクセントカラー"))
        self.btAccentColor = AutonaColorButton(
            color=SettingsManager.Get("general.accent_color", DEFAULT_ACCENT_COLOR))
        self.btAccentColor.setMinimumSize(32, 32)
        self.btAccentColor.colorChanged.connect(self._onAccentColorChanged)
        row.addWidget(self.btAccentColor)

        row.addStretch(1)
        group.layout().addLayout(row)
        return group

    def _onThemeColorChanged(self, color):
        if color:
            SettingsManager.Set("general.theme_color", color)
            StateManager.Set("tournamentInfo.themeColor", color)

    def _onAccentColorChanged(self, color):
        if color:
            SettingsManager.Set("general.accent_color", color)
            StateManager.Set("tournamentInfo.accentColor", color)

    # ── Add to OBS ───────────────────────────────────────────────────
    def _buildObsGroup(self):
        group = L.make_card("OBS に追加する方法")

        steps = [
            "OBS で「ソース」→「ブラウザ」を追加します。",
            "「ローカルファイル」にチェックを入れ、下のパスを指定します"
            "（「コピー」または「エクスプローラで開く」を使用）。",
            "サイズを 幅 1920 / 高さ 1080 に設定します。",
        ]
        for i, step in enumerate(steps, start=1):
            label = QLabel(f"{i}. " + step)
            label.setWordWrap(True)
            group.layout().addWidget(label)

        pathRow = QHBoxLayout()
        self.obsPathField = QLineEdit()
        self.obsPathField.setReadOnly(True)
        pathRow.addWidget(self.obsPathField, 1)
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsPathField.text()))
        pathRow.addWidget(btCopy)
        btOpen = QPushButton("エクスプローラで開く")
        btOpen.clicked.connect(self._openInExplorer)
        pathRow.addWidget(btOpen)
        group.layout().addLayout(pathRow)

        note = QLabel(
            "Autona を起動したままにすると、OBS 上でスコアがリアルタイムに反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group

    def _obsPath(self):
        design = self._currentDesign()
        if not design:
            return ""
        return os.path.normpath(
            os.path.abspath(f"{LAYOUT_DIR}/{design}/index.html"))

    def _refreshObsPath(self):
        self.obsPathField.setText(self._obsPath())

    def _openInExplorer(self):
        path = self._obsPath()
        if not path or not os.path.exists(path):
            return
        try:
            if sys.platform.startswith("win"):
                # explorer /select highlights the file in its folder. It exits
                # non-zero even on success, so don't check the return code.
                subprocess.Popen(r'explorer /select,"' + path + '"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as e:
            logger.warning(f"Board: failed to open explorer: {e}")
