"""BOARD tab: pick a scoreboard overlay design, preview it live, tune its
colors, and get the OBS browser-source path.

The scoreboard overlays live in ``layout/scoreboard_*/`` (TSH-style HTML/CSS/JS
that reads ``out/`` state and is loaded into OBS as a *local file*). This tab
bundles the operator's "make it look right and get it into OBS" workflow:

* **Design** — a combo over every ``layout/scoreboard*/`` folder that has an
  ``index.html``. The choice persists to ``board.selected_design``.
* **Preview** — an embedded QWebEngineView pointed at the running web server
  (``http://127.0.0.1:{port}/layout/{design}/index.html``). It shares the same
  Socket.IO feed as a real overlay, so score and color edits reflect live.
* **Colors** — theme / accent color pickers (moved here from the EVENT tab).
  Same SettingsManager / StateManager keys, so overlays and ``out/`` export are
  unaffected.
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
    """List ``layout/scoreboard*/`` folders that have an ``index.html``.

    The legacy ``layout/scoreboard/`` (per-game .html, no single entry) is
    excluded by the index.html check."""
    designs = []
    try:
        base = os.path.abspath(LAYOUT_DIR)
        for name in sorted(os.listdir(base)):
            if not name.startswith("scoreboard"):
                continue
            if os.path.isfile(os.path.join(base, name, "index.html")):
                designs.append(name)
    except OSError as e:
        logger.warning(f"Board: failed to list layout designs: {e}")
    return designs


class _ScoreboardPreview(QFrame):
    """Hosts the preview QWebEngineView and keeps it at a 16:9 letterbox,
    zooming the 1920x1080 overlay down to the panel width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        p = UI.palette()
        self.setStyleSheet(
            f"QFrame {{"
            f"background-color: {p['bg_tertiary']};"
            f"border: 1px solid {p['border_subtle']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        self.setMinimumHeight(220)

        self.view = QWebEngineView(self)
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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layoutView()

    def _layoutView(self):
        inset = 1  # sit inside the 1px border
        w = max(1, self.width() - 2 * inset)
        h = int(w * 9 / 16)
        # Centre vertically when the frame is taller than the 16:9 box.
        y = max(inset, (self.height() - h) // 2)
        self.view.setGeometry(inset, y, w, h)
        self.view.setZoomFactor(w / 1920.0)

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

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._buildDesignGroup())
        outer.addWidget(self._buildColorGroup())
        outer.addWidget(self._buildPreviewGroup(), 1)
        outer.addWidget(self._buildObsGroup())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setWidget(scroll)

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
        row.addWidget(self.designMetaLabel, 1)
        group.layout().addLayout(row)

        # Connect after the initial selection so it doesn't fire on startup.
        self.designCombo.currentIndexChanged.connect(self._onDesignChanged)
        if not self._designs:
            warn = QLabel("layout/ にスコアボードデザインが見つかりません。")
            warn.setWordWrap(True)
            group.layout().addWidget(warn)
        self._refreshDesignMeta()
        return group

    def _designMetaText(self, design):
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
