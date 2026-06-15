"""Integration guide for OBS Studio + Advanced Scene Switcher (ASS).

Two directions are covered (A/C — Autona driving OBS over obs-websocket — are
intentionally out of scope; see docs/architecture/obs-scene-switcher-control.md):

* **(B) ASS -> Autona**: an ASS macro runs a *Run* action that calls ``curl`` to
  hit the External Control API (``/api/v1/``). ASS's built-in *HTTP* action is
  not used because it discards the response and offers no custom headers, so a
  JSON API needs the Run+curl form instead.
* **(D) Autona -> OBS**: ASS watches the ``out/`` files Autona continuously
  exports (``out/<keypath>.txt`` = raw value, ``out/program_state.json`` = full
  state with ``revision``/``timestamp``) with a *File* condition and fires a
  *Switch scene* action.

Like ``AutonaIntegrationGuideWindow`` this window is purely descriptive: it shows
the live base URL / out directory and ready-to-paste curl commands + file paths.
All user-facing strings go through ``QApplication.translate("api", ...)`` so the
shared labels (Base URL / Port / Copy / Connection …) reuse the API window's
translations. Japanese strings live in ``Autona_ja.ts``.
"""

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

import json
import os

from .SettingsManager import SettingsManager
from .Helpers.AutonaNetworkHelper import GetLocalIP


def _tr(text):
    return QApplication.translate("api", text)


# (B) Ready-made curl commands for an ASS Run action. The operation set mirrors
# the Companion guide so operators recognise it; paths target board 1 / teams
# 1-2 (edit for other boards). label/method/path/body build the table.
_HTTP_EXAMPLES = [
    {"label": "Player 1 score +1", "method": "POST",
     "path": "/scoreboards/1/teams/1/score:adjust", "body": {"delta": 1}},
    {"label": "Player 1 score -1", "method": "POST",
     "path": "/scoreboards/1/teams/1/score:adjust", "body": {"delta": -1}},
    {"label": "Player 2 score +1", "method": "POST",
     "path": "/scoreboards/1/teams/2/score:adjust", "body": {"delta": 1}},
    {"label": "Player 2 score -1", "method": "POST",
     "path": "/scoreboards/1/teams/2/score:adjust", "body": {"delta": -1}},
    {"label": "Reset scores", "method": "POST",
     "path": "/scoreboards/1/score:reset", "body": None},
    {"label": "Swap teams", "method": "POST",
     "path": "/scoreboards/1:swap-teams", "body": None},
    {"label": "Pull current stream set", "method": "POST",
     "path": "/scoreboards/1:pull-stream", "body": None},
    {"label": "Refresh bracket", "method": "POST",
     "path": "/bracket:refresh", "body": None},
]

# (D) out/ files an ASS File condition can watch. `rel` is shown in the table;
# the copy button yields the absolute path (ASS's file picker needs the real
# path). These mirror StateManager's export: out/<keypath>.txt holds the raw
# value, program_state.json the full state. A file only exists once its value
# has been set at least once.
_WATCH_FILES = [
    {"rel": "out/program_state.json",
     "desc": "Full state (JSON, has revision/timestamp)"},
    {"rel": "out/score/1/team/1/score.txt", "desc": "Board 1 / Team 1 score"},
    {"rel": "out/score/1/team/2/score.txt", "desc": "Board 1 / Team 2 score"},
    {"rel": "out/score/1/team/1/player/1/gamerTag.txt",
     "desc": "Board 1 / Team 1 / Player 1 tag"},
    {"rel": "out/score/1/team/2/player/1/gamerTag.txt",
     "desc": "Board 1 / Team 2 / Player 1 tag"},
    {"rel": "out/score/1/match.txt", "desc": "Match label (e.g. Winners Finals)"},
    {"rel": "out/score/1/phase.txt", "desc": "Phase label (e.g. Top 8)"},
    {"rel": "out/score/1/best_of.txt", "desc": "Best-of value"},
]

_DESC = (
    "Set up integration between OBS Studio's Advanced Scene Switcher plugin "
    "and Autona."
)

# (B) setup steps. Keep in sync with the ASS Macros UI. The Run-vs-HTTP caveat
# lives in _NOTE_B_RUN (rendered as a note after the steps, not a step).
_STEPS_B = [
    "In OBS, open Tools -> Advanced Scene Switcher -> Macros and add a macro.",
    "Add a trigger (e.g. Hotkey, Scene, or WebSocket message).",
    "Add an action -> Run: set the program to curl.exe (bundled with Windows), "
    "then paste everything after \"curl\" from the table below as the arguments.",
    "Save and trigger the condition to test.",
]
_NOTE_B_RUN = (
    "Use Run (not the HTTP action): the HTTP action discards the response and "
    "cannot set a Content-Type header, which a JSON API needs."
)

# (D) setup steps. The text-export caveat lives in _NOTE_D_EXPORT (a note).
_STEPS_D = [
    "In OBS, open Tools -> Advanced Scene Switcher -> Macros and add a macro.",
    "Add a condition -> File, then browse to one of the files below (use Copy "
    "file path to get the absolute path).",
    "Choose the match mode: exact content / regex for a *.txt value, or "
    "\"File was modified\" on program_state.json to react to any change.",
    "Add an action -> Switch scene and pick the target scene.",
]
_NOTE_D_EXPORT = (
    "Keep text export enabled (Settings: general.disable_export must be off)."
)


class AutonaObsGuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

    # Built lazily once the app is fully up, mirroring AutonaApiInfoWindow.
    def UiMounted(self):
        self.setWindowTitle(_tr("OBS / Advanced Scene Switcher"))
        self.resize(940, 880)

        # The window stacks several groups taller than most screens, so host
        # everything in a scroll area rather than letting it clip.
        content = QWidget()
        layout = QVBoxLayout(content)

        heading = QLabel(_tr(_DESC))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        layout.addWidget(self._buildConnectionGroup())
        layout.addWidget(self._buildHttpGroup())
        layout.addWidget(self._buildWatchGroup())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)
        self._refresh()

    # ── Connection / paths ───────────────────────────────────────────────
    def _baseUrl(self):
        port = SettingsManager.Get("general.webserver_port", 5000)
        return f"http://{GetLocalIP()}:{port}/api/v1"

    def _outDir(self):
        return os.path.normpath(os.path.abspath("./out"))

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

        form.addWidget(QLabel(_tr("Output folder") + ":"), 1, 0)
        self.outDirField = QLineEdit()
        self.outDirField.setReadOnly(True)
        form.addWidget(self.outDirField, 1, 1)
        btCopyDir = QPushButton(_tr("Copy"))
        btCopyDir.clicked.connect(
            lambda: QApplication.clipboard().setText(self.outDirField.text()))
        form.addWidget(btCopyDir, 1, 2)

        self.portLabel = QLabel()
        form.addWidget(self.portLabel, 2, 0, 1, 2)

        note = QLabel(_tr("OBS must run on this PC, or on the same LAN."))
        note.setWordWrap(True)
        form.addWidget(note, 3, 0, 1, 4)

        form.setColumnStretch(1, 1)
        group.setLayout(form)
        return group

    def _refresh(self):
        port = SettingsManager.Get("general.webserver_port", 5000)
        self.baseUrlField.setText(self._baseUrl())
        self.outDirField.setText(self._outDir())
        self.portLabel.setText(_tr("Port") + f": {port}")
        self._populateHttp()

    # ── (B) ASS -> Autona via curl ───────────────────────────────────────
    def _curl(self, e):
        url = self._baseUrl() + e["path"]
        parts = ["curl", "-X", e["method"]]
        if e["body"]:
            body = json.dumps(e["body"], ensure_ascii=False)
            # Escape inner quotes so the one-liner survives cmd / the ASS Run
            # arguments field on Windows.
            body_esc = body.replace('"', '\\"')
            parts += ["-H", '"Content-Type: application/json"', "-d",
                      f'"{body_esc}"']
        parts.append(f'"{url}"')
        return " ".join(parts)

    def _buildHttpGroup(self):
        group = QGroupBox(_tr("Control Autona from Advanced Scene Switcher"))
        v = QVBoxLayout()

        for i, step in enumerate(_STEPS_B, start=1):
            label = QLabel(f"{i}. " + _tr(step))
            label.setWordWrap(True)
            v.addWidget(label)

        runNote = QLabel("※ " + _tr(_NOTE_B_RUN))
        runNote.setWordWrap(True)
        v.addWidget(runNote)

        headers = [_tr("Operation"), _tr("Command")]
        self.httpTable = QTableWidget(len(_HTTP_EXAMPLES), len(headers))
        self.httpTable.setHorizontalHeaderLabels(headers)
        self.httpTable.verticalHeader().setVisible(False)
        self.httpTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.httpTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.httpTable.setSelectionMode(QAbstractItemView.SingleSelection)
        # No inner scroll — the outer QScrollArea governs overflow.
        self.httpTable.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.httpTable.setMinimumHeight(
            self.httpTable.horizontalHeader().height()
            + self.httpTable.verticalHeader().defaultSectionSize()
            * len(_HTTP_EXAMPLES) + 8)
        v.addWidget(self.httpTable)

        btnRow = QHBoxLayout()
        btCmd = QPushButton(_tr("Copy command"))
        btCmd.clicked.connect(self._copyCommand)
        btnRow.addWidget(btCmd)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        note = QLabel(_tr(
            "Destructive operations (clear / reset) require \"confirm\": true "
            "in the JSON body. See the reference window for the full catalogue."))
        note.setWordWrap(True)
        v.addWidget(note)

        group.setLayout(v)
        return group

    def _populateHttp(self):
        for row, e in enumerate(_HTTP_EXAMPLES):
            self.httpTable.setItem(row, 0, QTableWidgetItem(_tr(e["label"])))
            self.httpTable.setItem(row, 1, QTableWidgetItem(self._curl(e)))
        self.httpTable.resizeColumnsToContents()
        self.httpTable.horizontalHeader().setStretchLastSection(True)

    def _copyCommand(self):
        row = self.httpTable.currentRow()
        if row < 0:
            return
        QApplication.clipboard().setText(self._curl(_HTTP_EXAMPLES[row]))

    # ── (D) ASS watches out/ files ───────────────────────────────────────
    def _absWatchPath(self, rel):
        # rel is "out/...": resolve against the app CWD where out/ is written.
        return os.path.normpath(os.path.abspath(rel))

    def _buildWatchGroup(self):
        group = QGroupBox(_tr("Trigger OBS from Autona state"))
        v = QVBoxLayout()

        for i, step in enumerate(_STEPS_D, start=1):
            label = QLabel(f"{i}. " + _tr(step))
            label.setWordWrap(True)
            v.addWidget(label)

        exportNote = QLabel("※ " + _tr(_NOTE_D_EXPORT))
        exportNote.setWordWrap(True)
        v.addWidget(exportNote)

        headers = [_tr("Watch file"), _tr("Contains")]
        self.watchTable = QTableWidget(len(_WATCH_FILES), len(headers))
        self.watchTable.setHorizontalHeaderLabels(headers)
        self.watchTable.verticalHeader().setVisible(False)
        self.watchTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.watchTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.watchTable.setSelectionMode(QAbstractItemView.SingleSelection)
        self.watchTable.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.watchTable.setMinimumHeight(
            self.watchTable.horizontalHeader().height()
            + self.watchTable.verticalHeader().defaultSectionSize()
            * len(_WATCH_FILES) + 8)
        for row, f in enumerate(_WATCH_FILES):
            self.watchTable.setItem(row, 0, QTableWidgetItem(f["rel"]))
            self.watchTable.setItem(row, 1, QTableWidgetItem(_tr(f["desc"])))
        self.watchTable.resizeColumnsToContents()
        self.watchTable.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.watchTable)

        btnRow = QHBoxLayout()
        btPath = QPushButton(_tr("Copy file path"))
        btPath.clicked.connect(self._copyWatchPath)
        btnRow.addWidget(btPath)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        note = QLabel(_tr(
            "*.txt files hold the raw value (easy to match). "
            "program_state.json carries revision/timestamp, so watch it for "
            "\"file modified\" to react to any state change."))
        note.setWordWrap(True)
        v.addWidget(note)

        group.setLayout(v)
        return group

    def _copyWatchPath(self):
        row = self.watchTable.currentRow()
        if row < 0:
            return
        QApplication.clipboard().setText(
            self._absWatchPath(_WATCH_FILES[row]["rel"]))
