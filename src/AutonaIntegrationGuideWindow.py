"""Connection guide for Bitfocus Companion, which drives Autona through the
External Control API (`/api/v1/`).

Companion fires an HTTP request to a board endpoint when a physical button is
pressed. This window is purely descriptive — it shows the live base URL, the
setup steps, a table of ready-made "button → request" mappings (full URL + JSON
body), and copy-paste key icons. The mappings mirror the endpoint catalogue in
``AutonaApiInfoWindow`` / ``AutonaApiV1.py`` (the source of truth).

(A Stream Deck is driven the same way via Companion, so it has no separate
entry: Companion covers it.)

All user-facing strings go through ``QApplication.translate("api", ...)`` so the
shared labels (Base URL / Port / Copy / Method …) reuse the translations already
defined for the API info window. Japanese strings live in ``Autona_ja.ts``.
"""

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

import json

from .SettingsManager import SettingsManager
from .Helpers.AutonaNetworkHelper import GetWebServerHost


def _tr(text):
    return QApplication.translate("api", text)


# Ready-made "physical button -> request" mappings. A curated subset of the API
# aimed at live score operation, so each row maps cleanly to one Companion
# button. Paths target board 1 / teams 1-2 (the operator edits them for other
# boards). label/method/path/body drive the reference table; bg/top/main drive
# the copy-paste key icon (a solid coloured tile with a drawn glyph) — gains are
# green, losses red, each action its own hue. The glyph text stays ASCII because
# it is painted into the bitmap, not shown as a translatable label.
_BUTTON_EXAMPLES = [
    {"label": "Player 1 score +1", "method": "POST",
     "path": "/scoreboards/1/teams/1/score:adjust", "body": {"delta": 1},
     "bg": "#2e7d32", "top": "P1", "main": "+1"},
    {"label": "Player 1 score -1", "method": "POST",
     "path": "/scoreboards/1/teams/1/score:adjust", "body": {"delta": -1},
     "bg": "#c62828", "top": "P1", "main": "-1"},
    {"label": "Player 2 score +1", "method": "POST",
     "path": "/scoreboards/1/teams/2/score:adjust", "body": {"delta": 1},
     "bg": "#2e7d32", "top": "P2", "main": "+1"},
    {"label": "Player 2 score -1", "method": "POST",
     "path": "/scoreboards/1/teams/2/score:adjust", "body": {"delta": -1},
     "bg": "#c62828", "top": "P2", "main": "-1"},
    {"label": "Reset scores", "method": "POST",
     "path": "/scoreboards/1/score:reset", "body": None,
     "bg": "#ef6c00", "top": None, "main": "SCORE\nRESET"},
    {"label": "Swap teams", "method": "POST",
     "path": "/scoreboards/1:swap-teams", "body": None,
     "bg": "#1565c0", "top": None, "main": "SWAP"},
    {"label": "Pull current stream set", "method": "POST",
     "path": "/scoreboards/1:pull-stream", "body": None,
     "bg": "#6a1b9a", "top": None, "main": "MATCH\nPULL"},
    {"label": "Refresh Top 8 bracket", "method": "POST",
     "path": "/bracket:refresh", "body": None,
     "bg": "#00838f", "top": None, "main": "TOP8\nREFRESH"},
]

# Companion key icons are commonly 72-144px; render at 144 so the copied bitmap
# stays crisp when the target downscales it.
_ICON_SIZE = 144

# Heading + numbered setup steps. English source strings translated at render
# time. Keep in sync with Companion's real "Generic HTTP" module UI.
_GUIDE = {
    "title": "Companion settings",
    "desc": "Bitfocus Companion controls Autona through the External "
            "Control API over HTTP. Map physical buttons to the "
            "operations below.",
    "steps": [
        "Launch Companion and open its web admin page "
        "(default http://127.0.0.1:8000).",
        "In \"Connections\", add the \"Generic HTTP\" module.",
        "Select a button, then \"Add action\" and choose the Generic "
        "HTTP \"POST\" (or \"GET\") action.",
        "Paste the request URL from the table below. For POST, set the "
        "body to the JSON shown and add the header "
        "Content-Type: application/json.",
        "Optionally set the button text, then press the key to test.",
    ],
}


class AutonaIntegrationGuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._guide = _GUIDE

    # Built lazily once the app is fully up, mirroring AutonaApiInfoWindow.
    def UiMounted(self):
        self.setWindowTitle(_tr(self._guide["title"]))
        self.resize(900, 860)

        # The window stacks several groups taller than most screens, so host
        # everything in a scroll area rather than letting it clip.
        content = QWidget()
        layout = QVBoxLayout(content)

        heading = QLabel(_tr(self._guide["desc"]))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        layout.addWidget(self._buildConnectionGroup())
        layout.addWidget(self._buildStepsGroup())
        layout.addWidget(self._buildExamplesGroup())
        layout.addWidget(self._buildIconsGroup())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)
        self._refresh()

    # ── Connection info ──────────────────────────────────────────────────
    def _baseUrl(self):
        port = SettingsManager.Get("general.webserver_port", 5000)
        return f"http://{GetWebServerHost()}:{port}/api/v1"

    def _buildConnectionGroup(self):
        group = QGroupBox(_tr("Connection"))
        form = QGridLayout()

        form.addWidget(QLabel(_tr("Base URL") + ":"), 0, 0)
        self.baseUrlField = QLineEdit()
        self.baseUrlField.setReadOnly(True)
        form.addWidget(self.baseUrlField, 0, 1)

        btCopy = QPushButton(_tr("Copy"))
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.baseUrlField.text()))
        form.addWidget(btCopy, 0, 2)

        btRefresh = QPushButton(_tr("Refresh"))
        btRefresh.clicked.connect(self._refresh)
        form.addWidget(btRefresh, 0, 3)

        self.portLabel = QLabel()
        form.addWidget(self.portLabel, 1, 0, 1, 2)

        # Text is swapped in _refresh() depending on the LAN access setting.
        self.lanNoteLabel = QLabel()
        self.lanNoteLabel.setWordWrap(True)
        form.addWidget(self.lanNoteLabel, 2, 0, 1, 4)

        form.setColumnStretch(1, 1)
        group.setLayout(form)
        return group

    def _refresh(self):
        port = SettingsManager.Get("general.webserver_port", 5000)
        self.baseUrlField.setText(self._baseUrl())
        self.portLabel.setText(_tr("Port") + f": {port}")
        if SettingsManager.Get("general.webserver_listen_lan", False):
            self.lanNoteLabel.setText(
                _tr("This device must be on the same LAN as this PC."))
        else:
            self.lanNoteLabel.setText(_tr(
                "LAN access: disabled — other devices cannot connect. "
                "Enable it under Settings → General (restart required)."))
        self._populateExamples()

    # ── Setup steps ──────────────────────────────────────────────────────
    def _buildStepsGroup(self):
        group = QGroupBox(_tr("Setup steps"))
        v = QVBoxLayout()
        for i, step in enumerate(self._guide["steps"], start=1):
            label = QLabel(f"{i}. " + _tr(step))
            label.setWordWrap(True)
            v.addWidget(label)
        group.setLayout(v)
        return group

    # ── Example button mappings ──────────────────────────────────────────
    def _buildExamplesGroup(self):
        group = QGroupBox(_tr("Example button mappings"))
        v = QVBoxLayout()

        headers = [_tr("Operation"), _tr("Method"), _tr("URL"), _tr("Body")]
        self.table = QTableWidget(len(_BUTTON_EXAMPLES), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # No inner scroll — the outer QScrollArea governs overflow, so size the
        # table to show all rows.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setMinimumHeight(
            self.table.horizontalHeader().height()
            + self.table.verticalHeader().defaultSectionSize() * len(_BUTTON_EXAMPLES)
            + 8)
        v.addWidget(self.table)

        btnRow = QHBoxLayout()
        btUrl = QPushButton(_tr("Copy URL"))
        btUrl.clicked.connect(self._copyUrl)
        btnRow.addWidget(btUrl)

        btBody = QPushButton(_tr("Copy body"))
        btBody.clicked.connect(self._copyBody)
        btnRow.addWidget(btBody)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        group.setLayout(v)
        return group

    def _populateExamples(self):
        base = self._baseUrl()
        for row, e in enumerate(_BUTTON_EXAMPLES):
            self.table.setItem(row, 0, QTableWidgetItem(_tr(e["label"])))
            self.table.setItem(row, 1, QTableWidgetItem(e["method"]))
            self.table.setItem(row, 2, QTableWidgetItem(base + e["path"]))
            self.table.setItem(
                row, 3,
                QTableWidgetItem(
                    json.dumps(e["body"], ensure_ascii=False) if e["body"] else "—"))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

    def _copyUrl(self):
        row = self.table.currentRow()
        if row < 0:
            return
        QApplication.clipboard().setText(
            self._baseUrl() + _BUTTON_EXAMPLES[row]["path"])

    def _copyBody(self):
        row = self.table.currentRow()
        if row < 0:
            return
        body = _BUTTON_EXAMPLES[row]["body"]
        QApplication.clipboard().setText(
            json.dumps(body, ensure_ascii=False) if body else "")

    # ── Copy-paste key icons ─────────────────────────────────────────────
    # Icons are generated, not shipped: a solid coloured tile + a drawn glyph,
    # painted with QPainter so there are no asset files and the bitmap can go
    # straight onto the clipboard (QClipboard.setPixmap) for the operator to
    # paste onto a Companion button.
    def _renderIcon(self, entry):
        pm = QPixmap(_ICON_SIZE, _ICON_SIZE)
        pm.fill(QColor(entry["bg"]))

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setPen(QColor("#ffffff"))

        top = entry.get("top")
        if top:
            f = QFont(QApplication.font())
            f.setBold(True)
            f.setPixelSize(40)
            painter.setFont(f)
            painter.drawText(
                QRect(0, 14, _ICON_SIZE, 46),
                Qt.AlignHCenter | Qt.AlignVCenter, top)
            mainRect = QRect(8, 58, _ICON_SIZE - 16, _ICON_SIZE - 70)
        else:
            mainRect = QRect(10, 10, _ICON_SIZE - 20, _ICON_SIZE - 20)

        # Shrink the glyph until it fits its box. Measure per line so multi-line
        # labels ("SCORE\nRESET") and big single glyphs ("+1") both fit.
        main = entry["main"]
        lines = main.split("\n")
        f = QFont(QApplication.font())
        f.setBold(True)
        size = 72
        while size > 12:
            f.setPixelSize(size)
            painter.setFont(f)
            fm = painter.fontMetrics()
            w = max(fm.horizontalAdvance(line) for line in lines)
            h = fm.lineSpacing() * len(lines)
            if w <= mainRect.width() and h <= mainRect.height():
                break
            size -= 4
        # drawText treats "\n" as a line break and AlignCenter centres each line.
        painter.drawText(mainRect, Qt.AlignCenter, main)
        painter.end()
        return pm

    def _buildIconsGroup(self):
        group = QGroupBox(_tr("Button icons (click to copy)"))
        outer = QVBoxLayout()

        hint = QLabel(_tr(
            "Click an icon to copy it to the clipboard, then paste it onto a "
            "Companion button."))
        hint.setWordWrap(True)
        outer.addWidget(hint)

        grid = QGridLayout()
        self._iconPixmaps = []
        cols = 4
        display = 72
        for i, entry in enumerate(_BUTTON_EXAMPLES):
            pm = self._renderIcon(entry)
            self._iconPixmaps.append(pm)

            cell = QVBoxLayout()
            btn = QToolButton()
            btn.setIcon(QIcon(pm))
            btn.setIconSize(QSize(display, display))
            btn.setAutoRaise(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(_tr("Copy this icon"))
            # clicked passes a checked bool first; capture i as default arg.
            btn.clicked.connect(lambda _checked=False, idx=i: self._copyIcon(idx))
            cell.addWidget(btn, 0, Qt.AlignHCenter)

            caption = QLabel(_tr(entry["label"]))
            caption.setAlignment(Qt.AlignHCenter)
            caption.setWordWrap(True)
            cell.addWidget(caption)

            wrapper = QWidget()
            wrapper.setLayout(cell)
            grid.addWidget(wrapper, i // cols, i % cols)
        outer.addLayout(grid)

        self.iconStatus = QLabel("")
        self.iconStatus.setStyleSheet("color: #6aa84f;")
        outer.addWidget(self.iconStatus)

        group.setLayout(outer)
        return group

    def _copyIcon(self, idx):
        QApplication.clipboard().setPixmap(self._iconPixmaps[idx])
        name = _tr(_BUTTON_EXAMPLES[idx]["label"])
        self.iconStatus.setText(_tr("Copied icon") + f": {name}")
