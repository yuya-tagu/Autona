"""BOARD tab: pick a scoreboard overlay design, preview it live, tune its
colors, and get the OBS browser-source path.

The built-in scoreboard overlays live in ``layout/scoreboard_*/`` and custom
composer projects in ``layout/custom/{slug}/`` (TSH-style HTML/CSS/JS that
reads ``out/`` state and is loaded into OBS as a *local file*). This tab bundles
the operator's "make it look right and get it into OBS" workflow:

* **Design** — a combo over the ``scoreboard`` and ``designer`` entries of
  the layout catalog (``src/core/LayoutCatalog.py``: display name and
  format / game come from each folder's metadata, not from this module).
  The operator's choice persists to ``board.selected_design``; a re-scan
  that cannot find it (a project.json locked mid-save or broken) only falls
  back on screen and never overwrites it.
* **Preview** — an embedded QWebEngineView pointed at the running web server
  (``http://127.0.0.1:{port}/layout/{design}/index.html``). It shares the same
  Socket.IO feed as a real overlay, so score and color edits reflect live.
  The winner popup (``layout/winner_popup/``) can be stacked on it as
  WINNER / CHAMPION to check its look; preview-only, never persisted.
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

from .core.SettingsManager import SettingsManager
from .core import LayoutCatalog
from .AutonaDesignerWindow import AutonaDesignerWindow
from .AutonaLayoutPreview import AutonaLayoutPreview
from .AutonaFeatureColors import FEATURE_BOARD, MakeFeatureColorCard
from . import ui_constants as UI
from . import ui_layout as L

LAYOUT_DIR = "layout"
DEFAULT_DESIGN = "scoreboard_crewbattle"
WINNER_POPUP_DIR = "winner_popup"

# Winner-popup choices for the preview: (label, mode). The mode goes to the
# popup page as its #preview= fragment (see layout/winner_popup/index.js);
# None leaves the popup off the preview.
WINNER_POPUP_PREVIEW_MODES = (
    ("非表示", None),
    ("WINNER", "winner"),
    ("CHAMPION", "champion"),
)

# Catalog kinds this tab offers: the built-in scoreboards, then the
# Designer projects (layout/custom/{slug}). Metadata for both -- display
# name, match format, supported game -- is the catalog's; a format / game a
# layout does not declare shows as "—".
BOARD_KINDS = (LayoutCatalog.KIND_SCOREBOARD, LayoutCatalog.KIND_DESIGNER)
UNKNOWN_META = "—"


def _boardLayouts(catalog):
    return [entry for entry in catalog.layouts if entry["kind"] in BOARD_KINDS]


class AutonaBoardWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)
        # windowTitle is the visible tab label (objectName is set in Autona.py).
        self.setWindowTitle(QApplication.translate("app", "Board"))

        catalog = LayoutCatalog.Discover()
        self._designs = _boardLayouts(catalog)
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
        rightCol.addLayout(self._buildWinnerPopupSelector())
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
        self._watchDesignDirectories(catalog.watchDirs)

        # NOTE: the theme/accent seed that used to run here moved to
        # Window._SeedPanelState() (startup Step 4b-2). This panel is built
        # on first reveal now, and the overlays need those colors from
        # launch whether or not it is ever opened. The color card added above
        # edits the board-scoped override (colors.board.*); the tournament
        # colors it falls back to live on the EVENT tab.

        self._refreshObsPath()

    # ── Design selection ─────────────────────────────────────────────
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
        for entry in self._designs:
            self.designCombo.addItem(entry["displayName"], entry["id"])
        saved = SettingsManager.Get("board.selected_design", DEFAULT_DESIGN)
        # What the operator picked, kept apart from what the combo shows: a
        # design missing from a scan is shown as the fallback below but stays
        # the choice, so it comes back as soon as a scan lists it again.
        self._selectedDesign = saved
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
        # format / game are free text from settings.json / project.json.
        self.designMetaLabel.setTextFormat(Qt.TextFormat.PlainText)
        self.designMetaLabel.setWordWrap(True)
        self.designMetaLabel.setStyleSheet(UI.secondary_label_style())
        col.addWidget(self.designMetaLabel)

        # Connect after the initial selection so it doesn't fire on startup.
        self.designCombo.currentIndexChanged.connect(self._onDesignChanged)
        self.designCombo.activated.connect(self._onDesignActivated)
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

    def _designEntry(self, design):
        return next(
            (entry for entry in self._designs if entry["id"] == design), None)

    def _designMetaText(self, design):
        entry = self._designEntry(design) or {}
        fmt = entry.get("format") or UNKNOWN_META
        game = entry.get("game") or UNKNOWN_META
        return f"対戦形式：{fmt}\nゲーム：{game}"

    def _designFile(self, design):
        """The design's entry file name inside its folder."""
        entry = self._designEntry(design)
        return entry["entry"] if entry else LayoutCatalog.DEFAULT_ENTRY

    def _refreshDesignMeta(self):
        design = self._currentDesign()
        self.designMetaLabel.setText(
            self._designMetaText(design) if design else "")

    def _currentDesign(self):
        if self.designCombo.count() == 0:
            return None
        return self.designCombo.currentData()

    def _onDesignChanged(self, _idx):
        # Only the operator's own pick reaches here (_refreshDesigns refills
        # the combo with signals blocked), so this and _onDesignActivated are
        # the only places the choice is persisted.
        design = self._currentDesign()
        if design:
            self._selectedDesign = design
            SettingsManager.Set("board.selected_design", design)
        self._refreshDesignMeta()
        self._refreshObsPath()
        if self._previewLoaded:
            self._loadPreview()

    def _onDesignActivated(self, _idx):
        # Choosing the item already shown emits no currentIndexChanged. That
        # matters while the combo shows a fallback for a saved design it
        # cannot find: picking that fallback must still make it the choice.
        # Qt emits activated after currentIndexChanged, so an ordinary pick
        # is already saved by _onDesignChanged and this is a no-op.
        design = self._currentDesign()
        if design and design != self._selectedDesign:
            self._selectedDesign = design
            SettingsManager.Set("board.selected_design", design)

    def _watchDesignDirectories(self, paths):
        # The catalog says which directories can change its contents
        # (LayoutCatalog.Catalog.watchDirs); this tab only watches them.
        watched = set(self._designWatcher.directories())
        for path in paths:
            if path not in watched:
                self._designWatcher.addPath(path)

    def _scheduleDesignRefresh(self, _path=None):
        # Saving creates the slug directory before writing index.html. Debounce
        # directory notifications so discovery runs after the atomic writes.
        # Watch a new slug directory right away (a cheap listing, no file
        # reads) so its writes keep pushing the debounce back.
        self._watchDesignDirectories(LayoutCatalog.WatchDirs())
        self._designRefreshTimer.start()

    def _refreshDesigns(self):
        catalog = LayoutCatalog.Discover()
        self._watchDesignDirectories(catalog.watchDirs)
        designs = _boardLayouts(catalog)
        # The operator's choice, not what the combo currently shows: after a
        # scan that missed it the combo shows a fallback, and keying on that
        # would make the fallback stick once the design is listed again.
        preferred = self._selectedDesign

        self.designCombo.blockSignals(True)
        try:
            self.designCombo.clear()
            for entry in designs:
                self.designCombo.addItem(entry["displayName"], entry["id"])

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
        # No SettingsManager write: a fallback is for display only, and a
        # found choice is already what is saved.
        selected = self._currentDesign()
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
            self.preview.load(design, self._designFile(design))

    def _buildWinnerPopupSelector(self):
        # Same label + full-width combo shape as the design selector above.
        # Preview-only and not persisted: it starts off on every launch and
        # never reaches the state or the popup OBS shows.
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        label = QLabel("勝者ポップアップをプレビューに重ねる")
        L.apply_section_title(label)
        col.addWidget(label)

        self.winnerPopupCombo = QComboBox()
        for text, mode in WINNER_POPUP_PREVIEW_MODES:
            self.winnerPopupCombo.addItem(text, mode)
        self.winnerPopupCombo.setToolTip(
            "このプレビューにだけ表示します。OBS の表示には影響しません。")
        # Connect after the items are in so filling the combo doesn't fire.
        self.winnerPopupCombo.currentIndexChanged.connect(
            self._onWinnerPopupPreviewChanged)
        col.addWidget(self.winnerPopupCombo)
        return col

    def _onWinnerPopupPreviewChanged(self, _idx):
        # The preview re-adds the popup after every load, so the choice
        # carries over design switches without anything here.
        mode = self.winnerPopupCombo.currentData()
        self.preview.SetOverlayFrame(
            f"{WINNER_POPUP_DIR}/index.html#preview={mode}" if mode else None)

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
            os.path.abspath(f"{LAYOUT_DIR}/{WINNER_POPUP_DIR}/index.html"))

    def _obsPath(self):
        design = self._currentDesign()
        if not design:
            return ""
        return os.path.normpath(os.path.abspath(
            f"{LAYOUT_DIR}/{design}/{self._designFile(design)}"))

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
