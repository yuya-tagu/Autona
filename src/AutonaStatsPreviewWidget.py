"""Stats Display Settings tab: preview the statistics stream overlays and
get them onto the stream.

Two-pane panel following the Commentary tab's structure:

* **Left** — a pulldown that picks which statistics overlay to look at
  (upset factor / head-to-head / recent sets / last sets / tournament
  history), the theme / accent colors those overlays render with (the
  shared AutonaFeatureColors card, feature ``stats``), plus the Bitfocus
  Companion macro for the ``stats:refresh`` operation so the operator can
  trigger a stats fetch from a physical button instead of the STATS
  panel's button.
* **Right** — the embedded live preview of the selected overlay and its
  browser-source URL.

The only state this panel writes is its own color override
(``colors.stats.*``, through AutonaFeatureColors) — the statistics
themselves are produced elsewhere. The selected overlay is remembered in
SettingsManager (``stats_preview.selected_type``), which is a UI
preference, not part of the program state contract.

The overlays under ``layout/`` render whatever the stats pipeline already
wrote under ``score.1.*`` (recent_sets / last_sets / history_sets /
upset_factor), so there is nothing to configure here beyond which one to
show — refreshing that data is what the Companion macro is for.
"""

import json

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .AutonaLayoutPreview import AutonaLayoutPreview, ObsUrl
from .Helpers.AutonaNetworkHelper import GetWebServerHost
from .AutonaFeatureColors import FEATURE_STATS, MakeFeatureColorCard
from . import ui_constants as UI
from . import ui_layout as L

# The statistics overlays offered by the pulldown, in display order.
# (settings key, layout dir, entry html, display-name source string)
#
# The settings key is what gets persisted, so it stays stable even if a
# layout folder is renamed. last_sets / tournament_history ship one entry
# file per player rather than a single index.html.
STATS_LAYOUTS = [
    ("upset_factor",          "upset_factor",       "index.html",   "Upset Factor"),
    ("h2h_record",            "h2h_record",         "index.html",   "Head-to-Head Record"),
    ("recent_sets",           "recent_sets",        "index.html",   "Recent Sets"),
    ("last_sets_p1",          "last_sets",          "player1.html", "Last Sets (Player 1)"),
    ("last_sets_p2",          "last_sets",          "player2.html", "Last Sets (Player 2)"),
    ("tournament_history_p1", "tournament_history", "player1.html", "Tournament History (Player 1)"),
    ("tournament_history_p2", "tournament_history", "player2.html", "Tournament History (Player 2)"),
]

DEFAULT_TYPE = STATS_LAYOUTS[0][0]

SETTINGS_KEY = "stats_preview.selected_type"

# Companion macro exposed by this panel: the External Control API operation
# that recalculates every statistic for board 1 (the same work the STATS
# panel's 「統計情報を取得する」 button does). Mirrors the catalogue in
# AutonaApiInfoWindow / AutonaApiV1 (the source of truth).
REFRESH_METHOD = "POST"
REFRESH_PATH = "/scoreboards/1/stats:refresh"
REFRESH_BODY = None


class AutonaStatsPreviewWidget(QDockWidget):

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowTitle(
            QApplication.translate("app", "Stats Display Settings"))

        self._previewLoaded = False

        # Build the type selector first: the OBS group and the preview below
        # both read the current selection.
        typeCard = self._buildTypeGroup()
        preview = self._buildPreview()

        leftContent = QWidget()
        leftCol = QVBoxLayout(leftContent)
        leftCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        leftCol.addWidget(typeCard)
        leftCol.addWidget(MakeFeatureColorCard(FEATURE_STATS, "統計表示の色"))
        leftCol.addWidget(self._buildCompanionGroup())
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
        previewTitle = QLabel("プレビュー")
        L.apply_section_title(previewTitle)
        rightCol.addWidget(previewTitle)
        rightCol.addWidget(preview, 1)
        rightCol.addLayout(preview.BuildActionButtons())
        rightCol.addWidget(self._buildObsGroup())

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

    # ── Overlay selection ────────────────────────────────────────────
    def _buildTypeGroup(self):
        group = L.make_card("統計情報の種類を選択")

        note = QLabel(
            "配信に出したい統計オーバーレイを選ぶと、右側にプレビューと "
            "OBS 用の URL が表示されます。表示される内容は「統計情報」タブ"
            "で取得したデータです。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        self.typeCombo = QComboBox()
        for key, _dirname, _entry, label in STATS_LAYOUTS:
            self.typeCombo.addItem(QApplication.translate("app", label), key)
        saved = SettingsManager.Get(SETTINGS_KEY, DEFAULT_TYPE)
        idx = self.typeCombo.findData(saved)
        if idx < 0:
            idx = self.typeCombo.findData(DEFAULT_TYPE)
        if idx >= 0:
            self.typeCombo.setCurrentIndex(idx)
        # Connect after the initial selection so it doesn't fire on startup.
        self.typeCombo.currentIndexChanged.connect(self._onTypeChanged)
        group.layout().addWidget(self.typeCombo)
        return group

    def _currentEntry(self):
        """The selected (key, dir, entry, label) row — never None."""
        key = self.typeCombo.currentData()
        for row in STATS_LAYOUTS:
            if row[0] == key:
                return row
        return STATS_LAYOUTS[0]

    def _currentObsUrl(self):
        _key, dirname, entry, _label = self._currentEntry()
        return ObsUrl(dirname, entry)

    def _onTypeChanged(self, _idx):
        key, dirname, entry, _label = self._currentEntry()
        SettingsManager.Set(SETTINGS_KEY, key)
        self.obsUrlField.setText(self._currentObsUrl())
        if self._previewLoaded:
            self.preview.load(dirname, entry)

    # ── Preview ──────────────────────────────────────────────────────
    def _buildPreview(self):
        # Bare preview widget, no card wrapper — the RESULT tab's right-pane
        # structure is the reference for every preview panel.
        _key, dirname, entry, _label = self._currentEntry()
        self.preview = AutonaLayoutPreview(dirname, entry=entry)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return self.preview

    def showEvent(self, e):
        super().showEvent(e)
        # Lazy-load on first reveal: by then the web server is up, so the
        # preview's HTTP fetch + Socket.IO connect succeed.
        if not self._previewLoaded:
            self._previewLoaded = True
            _key, dirname, entry, _label = self._currentEntry()
            self.preview.load(dirname, entry)
        # The port can change in Settings while the app runs; keep the
        # displayed URLs honest each time the panel is shown.
        self.obsUrlField.setText(self._currentObsUrl())
        self.requestUrlField.setText(self._requestUrl())

    # ── Companion macro ──────────────────────────────────────────────
    def _apiBaseUrl(self):
        port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
        return f"http://{GetWebServerHost()}:{port}/api/v1"

    def _requestUrl(self):
        return self._apiBaseUrl() + REFRESH_PATH

    def _buildCompanionGroup(self):
        group = L.make_card("Companion マクロ")

        note = QLabel(
            "Bitfocus Companion の物理ボタンから統計情報の取得（「統計情報」"
            "タブの「統計情報を取得する」と同じ処理）を実行できます。"
            "Generic HTTP モジュールに下のリクエストを設定してください。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        bodyText = (json.dumps(REFRESH_BODY, ensure_ascii=False)
                    if REFRESH_BODY else "なし")
        for label, value in (("メソッド", REFRESH_METHOD),
                             ("パス", REFRESH_PATH),
                             ("ボディ", bodyText)):
            row = L.form_row(label, QLabel(value), label_width=64)
            group.layout().addLayout(row)

        urlRow = QHBoxLayout()
        self.requestUrlField = QLineEdit()
        self.requestUrlField.setReadOnly(True)
        self.requestUrlField.setText(self._requestUrl())
        urlRow.addWidget(self.requestUrlField, 1)
        group.layout().addLayout(urlRow)

        btCopy = QPushButton("リクエストをコピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(
                self.requestUrlField.text()))
        group.layout().addWidget(btCopy)

        btGuide = QPushButton("Companion 設定ガイドを開く")
        btGuide.clicked.connect(self._openCompanionGuide)
        group.layout().addWidget(btGuide)
        return group

    def _openCompanionGuide(self):
        """Open the shared Companion guide dialog owned by the main window.

        Looked up lazily (not held as a reference): the dialog itself is
        built on first open by the main window (startup Step 4a), and this
        panel must not break if the accessor is ever absent.
        """
        ensure = getattr(self.window(), "EnsureCompanionGuideWindow", None)
        if ensure is None:
            logger.warning(
                "Stats preview: Companion guide window is not available")
            return
        guide = ensure()
        guide.show()
        guide.raise_()
        guide.activateWindow()

    # ── Add to OBS ───────────────────────────────────────────────────
    def _buildObsGroup(self):
        group = L.make_card("OBS に追加する方法")

        steps = [
            "OBS で「ソース」→「ブラウザ」を追加します。",
            "「URL」に下のアドレスを指定します（「コピー」を使用）。",
            "サイズを 幅 1920 / 高さ 1080 に設定します。",
        ]
        for i, step in enumerate(steps, start=1):
            label = QLabel(f"{i}. " + step)
            label.setWordWrap(True)
            group.layout().addWidget(label)

        urlRow = QHBoxLayout()
        self.obsUrlField = QLineEdit()
        self.obsUrlField.setReadOnly(True)
        self.obsUrlField.setText(self._currentObsUrl())
        urlRow.addWidget(self.obsUrlField, 1)
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsUrlField.text()))
        urlRow.addWidget(btCopy)
        group.layout().addLayout(urlRow)

        note = QLabel(
            "統計情報を取得し直すと、OBS 上のオーバーレイにもリアルタイムに"
            "反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group
