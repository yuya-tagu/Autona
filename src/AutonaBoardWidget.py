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
* **Colors** — the scoreboard's own theme / accent pickers (the shared
  ``AutonaFeatureColors`` card, feature ``board``). Unset, they inherit the
  tournament colors edited on the EVENT tab.
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
from loguru import logger

from .SettingsManager import SettingsManager
from .AutonaDesignerWindow import AutonaDesignerWindow
from .AutonaLayoutPreview import AutonaLayoutPreview
from .AutonaFeatureColors import FEATURE_BOARD, MakeFeatureColorCard
from . import ui_constants as UI
from . import ui_layout as L

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

    The index.html check keeps this robust against non-design folders
    accidentally dropped under ``layout/`` (e.g. a ``custom/{slug}/`` that
    hasn't been built out yet). Composer projects are stored one level
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

        # Two panes, each with its own scroll (thumbnail-settings style,
        # same idiom as the Commentary / Top 8 panels): settings (design /
        # colors / OBS) in a fixed-width left column, the live preview
        # filling the right.
        leftContent = QWidget()
        leftCol = QVBoxLayout(leftContent)
        leftCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        leftCol.addWidget(self._buildDesignerGroup())
        leftCol.addWidget(
            MakeFeatureColorCard(FEATURE_BOARD, "スコアボードの色"))
        leftCol.addWidget(self._buildObsGroup())
        leftCol.addStretch(1)

        leftScroll = QScrollArea()
        leftScroll.setWidgetResizable(True)
        leftScroll.setWidget(leftContent)
        leftScroll.setFixedWidth(L.LEFT_PANE_WIDTH)
        leftScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        rightContent = QWidget()
        rightCol = QVBoxLayout(rightContent)
        rightCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        rightCol.setSpacing(UI.PANEL_MARGIN)
        rightCol.addLayout(self._buildDesignSelector())
        rightCol.addWidget(self._buildPreview(), 1)
        rightCol.addLayout(self.preview.BuildActionButtons())

        rightScroll = QScrollArea()
        rightScroll.setWidgetResizable(True)
        rightScroll.setWidget(rightContent)

        panes = QWidget()
        panesLayout = QHBoxLayout(panes)
        panesLayout.setContentsMargins(0, 0, 0, 0)
        panesLayout.setSpacing(0)
        panesLayout.addWidget(leftScroll)
        panesLayout.addWidget(rightScroll, 1)
        self.setWidget(panes)
        self._watchDesignDirectories()

        # NOTE: the theme/accent seed that used to run here moved to
        # Window._SeedPanelState() (startup Step 4b-2). This panel is built
        # on first reveal now, and the overlays need those colors from
        # launch whether or not it is ever opened. The color card added above
        # edits the board-scoped override (colors.board.*); the tournament
        # colors it falls back to live on the EVENT tab.

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

    def _buildDesignSelector(self):
        # Right-column selector above the preview: section-title label +
        # full-width combo, matching the RESULT tab. Japanese literals
        # follow the existing hardcoded-JP precedent ("ブラウザ版を開く" in
        # AutonaScoreboardWidget); the runtime reads compiled .qm only, so
        # untranslated translate() calls would show English.
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        label = QLabel("スコアボードのデザインを選択")
        L.apply_section_title(label)
        col.addWidget(label)

        self.designCombo = QComboBox()
        for d in self._designs:
            self.designCombo.addItem(self._displayName(d), d)
        saved = SettingsManager.Get("board.selected_design", DEFAULT_DESIGN)
        idx = self.designCombo.findData(saved)
        if idx < 0:
            idx = self.designCombo.findData(DEFAULT_DESIGN)
        if idx >= 0:
            self.designCombo.setCurrentIndex(idx)
        col.addWidget(self.designCombo)

        # Supported format / game for the selected design. No manual
        # refresh button: _designWatcher already re-runs _refreshDesigns
        # whenever a design folder changes on disk.
        self.designMetaLabel = QLabel()
        self.designMetaLabel.setWordWrap(True)
        self.designMetaLabel.setStyleSheet(UI.secondary_label_style())
        col.addWidget(self.designMetaLabel)

        # Connect after the initial selection so it doesn't fire on startup.
        self.designCombo.currentIndexChanged.connect(self._onDesignChanged)
        if not self._designs:
            warn = QLabel("layout/ にスコアボードデザインが見つかりません。")
            warn.setWordWrap(True)
            col.addWidget(warn)
        self._refreshDesignMeta()
        return col

    def _buildDesignerGroup(self):
        group = L.make_card("Designer")
        designerDescription = QLabel(
            "PNGと選手名・スコアを組み合わせて、新しいデザインを作成・編集します。")
        designerDescription.setWordWrap(True)
        designerDescription.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(designerDescription)
        self.btOpenDesigner = QPushButton("Designerを開く")
        self.btOpenDesigner.setToolTip(
            "デスクトップウインドウでスコアボードDesignerを開きます")
        self.btOpenDesigner.clicked.connect(self._openDesigner)
        group.layout().addWidget(self.btOpenDesigner)
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
    def _buildPreview(self):
        # Bare preview widget, no card wrapper — the RESULT tab's right-pane
        # structure is the reference for every preview panel. The preview
        # keeps a fixed 1920x1080 logical resolution and fits the whole
        # canvas to the panel (zoom scales with size); see
        # AutonaLayoutPreview.
        self.preview = AutonaLayoutPreview()
        self.preview.setBackgroundImage(
            os.path.join("layout", "videoframe_1659973.png"))
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return self.preview

    def _loadPreview(self):
        design = self._currentDesign()
        if design:
            self.preview.load(design)

    def showEvent(self, e):
        super().showEvent(e)
        # Lazy-load on first reveal: by the time the user opens this tab the
        # web server is up, so the preview's HTTP fetch + Socket.IO connect.
        if not self._previewLoaded and self._designs:
            self._previewLoaded = True
            self._loadPreview()

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

        # Narrow left column: the path field gets its own row, the two
        # action buttons share the row below.
        self.obsPathField = QLineEdit()
        self.obsPathField.setReadOnly(True)
        group.layout().addWidget(self.obsPathField)
        btnRow = QHBoxLayout()
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsPathField.text()))
        btnRow.addWidget(btCopy)
        btOpen = QPushButton("エクスプローラで開く")
        btOpen.clicked.connect(lambda: self._openInExplorer())
        btnRow.addWidget(btOpen)
        btnRow.addStretch(1)
        group.layout().addLayout(btnRow)

        # Winner popup: a separate OBS source, so it gets its own fixed path
        # (it does not depend on the selected design).
        popupTitle = QLabel("勝者ポップアップ（WINNER / CHAMPION）")
        popupTitle.setWordWrap(True)
        group.layout().addWidget(popupTitle)
        popupHint = QLabel(
            "試合の勝者が決まると中央下部に名前入りで表示されます。"
            "上と同じ手順で、別のブラウザソースとして追加します。")
        popupHint.setWordWrap(True)
        group.layout().addWidget(popupHint)
        self.winnerPopupPathField = QLineEdit()
        self.winnerPopupPathField.setReadOnly(True)
        self.winnerPopupPathField.setText(self._winnerPopupPath())
        group.layout().addWidget(self.winnerPopupPathField)
        popupBtnRow = QHBoxLayout()
        btPopupCopy = QPushButton("コピー")
        btPopupCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(
                self.winnerPopupPathField.text()))
        popupBtnRow.addWidget(btPopupCopy)
        btPopupOpen = QPushButton("エクスプローラで開く")
        btPopupOpen.clicked.connect(
            lambda: self._openInExplorer(self._winnerPopupPath()))
        popupBtnRow.addWidget(btPopupOpen)
        popupBtnRow.addStretch(1)
        group.layout().addLayout(popupBtnRow)

        note = QLabel(
            "Autona を起動したままにすると、OBS 上でスコアがリアルタイムに反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group

    def _winnerPopupPath(self):
        return os.path.normpath(
            os.path.abspath(f"{LAYOUT_DIR}/winner_popup/index.html"))

    def _obsPath(self):
        design = self._currentDesign()
        if not design:
            return ""
        return os.path.normpath(
            os.path.abspath(f"{LAYOUT_DIR}/{design}/index.html"))

    def _refreshObsPath(self):
        self.obsPathField.setText(self._obsPath())

    def _openInExplorer(self, path=None):
        if path is None:
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
