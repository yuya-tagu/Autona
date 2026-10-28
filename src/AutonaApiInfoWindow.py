"""External Control API (`/api/v1/`) reference + tester window.

A developer-facing dialog reachable from the "API Settings" menu. It does two
things:

  - Reference: shows the connection base URL, every implemented endpoint
    (method / path / body example / confirm / op-log), the response envelope
    and error-code legend, plus one-click curl copy.
  - Tester: sends a real request to the locally hosted server and shows the
    raw response, so the operator can verify the API without external tooling.

Endpoint metadata mirrors the implementation in ``AutonaApiV1.py`` (the source
of truth); this window is purely descriptive and never duplicates state logic.
Requests run as a TaskExecutor job (ADR-005 S3f-4; a Worker on a QThreadPool
until then) because some endpoints (``/sets``, heavy ops) can block ~20s on a
synchronous start.gg call (agent-pitfalls #11).

All user-facing strings go through ``QApplication.translate("api", ...)`` so a
single language is shown according to the app's language setting. Japanese
translations live in ``src/i18n/Autona_ja.ts`` (compiled to ``.qm``).
"""

import json

import requests
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from shiboken6 import Shiboken

from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .core import WebAccess
from .core.TaskExecutor import TaskExecutor
from .Helpers import AutonaQtHelper
from .Helpers.AutonaNetworkHelper import GetWebServerHost


def _tr(text):
    return QApplication.translate("api", text)


# Request timeout: a little above the provider's 20s start.gg timeout so the
# read endpoints that proxy a synchronous fetch don't get cut off early. Heavy
# write ops answer 202 immediately, so they never approach this.
_REQUEST_TIMEOUT = 25

# Placeholder substitutions used when turning a template path into a concrete,
# copy-pasteable curl / tester default.
_PATH_DEFAULTS = {"{n}": "1", "{t}": "1", "{p}": "1", "{setId}": "12345",
                  "{code}": "JP"}


# Endpoint catalogue. Mirrors AutonaApiV1.py route-by-route. `desc` is an
# English source string translated at render time. `body` is an example
# payload (dict) or None. `destructive` triggers a confirm dialog in the
# tester. `confirm` means the API itself rejects the call without
# {"confirm": true}. `logged` means the op is recorded by the write op-log.
#   (method, path, desc, body, destructive, logged, confirm)
_ENDPOINTS = [
    # ── Read (GET, not op-logged) ──
    ("GET", "/state", "Full state + revision", None, False, False, False),
    ("GET", "/health", "Liveness / version / activeBoard", None, False, False, False),
    ("GET", "/scoreboards/{n}", "Scoreboard n state (incl. swapped)", None, False, False, False),
    ("GET", "/games", "Game list", None, False, False, False),
    ("GET", "/games/current", "Current game", None, False, False, False),
    ("GET", "/match-names", "Match / phase name candidates", None, False, False, False),
    ("GET", "/characters", "Character list", None, False, False, False),
    ("GET", "/variants", "Variant list", None, False, False, False),
    ("GET", "/sets?finished=true", "Set list (synchronous start.gg call)", None, False, False, False),
    ("GET", "/sets/{setId}", "Set detail (synchronous start.gg call)", None, False, False, False),
    ("GET", "/commentary", "Commentary state", None, False, False, False),
    ("GET", "/countries/{code}/states", "States / regions of a country", None, False, False, False),
    ("GET", "/layouts?kind=scoreboard", "Layout list (?kind=scoreboard|designer|commentary|top8)", None, False, False, False),
    # ── Safe write (op-logged, no confirm) ──
    ("POST", "/scoreboards/{n}/teams/{t}/score:adjust", "Adjust score by delta", {"delta": 1}, False, True, False),
    ("POST", "/scoreboards/{n}/score:reset", "Reset scores", None, False, True, False),
    ("POST", "/scoreboards/{n}/score:set", "Set both scores (absolute)", {"team1": 2, "team2": 1}, False, True, False),
    ("POST", "/scoreboards/{n}:apply", "Apply pending edits (反映)", None, False, True, False),
    ("POST", "/scoreboards/{n}:swap-teams", "Swap teams", None, False, True, False),
    ("POST", "/scoreboards/{n}/stats:refresh", "Refresh statistics (async fetch)", None, False, True, False),
    ("POST", "/scoreboards/{n}/teams/{t}/color", "Set team color", {"color": "#FF0000"}, False, True, False),
    ("PATCH", "/scoreboards/{n}/set", "Patch set info (partial)", {"bestOf": 5, "phase": "Top 8", "pool": "Pool A1", "match": "Winners Finals"}, False, True, False),
    ("PATCH", "/scoreboards/{n}/teams/{t}/players/{p}", "Update player fields (partial)", {"gamerTag": "Player", "twitter": "player_x"}, False, True, False),
    ("PATCH", "/display-options", "Toggle display options (partial)", {"show_seed": False}, False, True, False),
    # ── Destructive (confirm required) ──
    ("POST", "/scoreboards/{n}:clear", "Clear board (confirm required)", {"confirm": True}, True, True, True),
    ("POST", "/scoreboards/{n}:reset-players", "Reset players (confirm required)", {"confirm": True}, True, True, True),
    ("POST", "/scoreboards/{n}:reset-match", "Reset match (confirm required)", {"confirm": True}, True, True, True),
    # ── Board overwrite (confirm recommended) ──
    ("POST", "/scoreboards/{n}:load-set", "Load a set onto the board", {"setId": "12345"}, False, True, False),
    ("POST", "/scoreboards/{n}:pull-stream", "Pull current stream set", None, False, True, False),
    # ── Heavy (202 accepted + in-progress flag) ──
    ("POST", "/tournament", "Load tournament (202, async)", {"url": "https://www.start.gg/tournament/.../event/..."}, False, True, False),
    ("POST", "/game", "Set game (202, async)", {"codename": "ssbu", "modsActive": False}, False, True, False),
    ("POST", "/bracket:refresh", "Refresh bracket (202, async)", None, False, True, False),
]

# Error-code legend (spec §2.6 / AutonaApiV1.py). (code, http, desc).
_ERROR_CODES = [
    ("INVALID_ARGUMENT", "400", "Bad parameter"),
    ("NOT_FOUND", "404", "Unknown resource"),
    ("CONFIRMATION_REQUIRED", "409", "Missing confirm:true"),
    ("CONFLICT", "409", "Heavy operation already running"),
    ("NOT_READY", "409", "Precondition unmet"),
    ("UPSTREAM_TIMEOUT", "504", "Upstream (start.gg) timed out"),
    ("INTERNAL", "500", "Unexpected internal error"),
    # The access guard (ADR-005 S3a, src/core/WebAccess.py).
    ("UNAUTHENTICATED", "401", "Token missing or wrong (writes from another device)"),
    ("FORBIDDEN", "403", "Refused browser page (cross-site / another origin)"),
    # A desktop shell operation in the headless shell (ADR-005 S3c / S3e-9).
    ("UNAVAILABLE", "503", "Not available in headless (needs the desktop window)"),
]


def _concrete_path(path):
    """Substitute {n}/{t}/{setId} placeholders with copy-pasteable defaults."""
    for token, value in _PATH_DEFAULTS.items():
        path = path.replace(token, value)
    return path


class AutonaApiInfoWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

    # UI is built lazily once the app is fully up, mirroring
    # AutonaSettingsWindow (called from Window after construction).
    def UiMounted(self):
        self.setWindowTitle(QApplication.translate("app", "API Settings"))
        self.resize(940, 620)

        layout = QVBoxLayout()
        layout.addWidget(self._buildConnectionGroup())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._buildReferenceTab(), _tr("Reference"))
        self.tabs.addTab(self._buildTesterTab(), _tr("Tester"))
        layout.addWidget(self.tabs)

        self.setLayout(layout)
        self._refreshBaseUrl()

    # ── Connection info ──────────────────────────────────────────────────
    def _baseUrl(self):
        port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
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
        btRefresh.clicked.connect(self._refreshBaseUrl)
        form.addWidget(btRefresh, 0, 3)

        btSettings = QPushButton(_tr("Open settings"))
        btSettings.clicked.connect(self._openSettings)
        form.addWidget(btSettings, 1, 2, 1, 2)

        self.portLabel = QLabel()
        form.addWidget(self.portLabel, 1, 0, 1, 2)

        # The access token (ADR-005 S3a): what another device sends to write.
        # Masked until asked for; copied and replaced from here.
        form.addWidget(QLabel(_tr("Access token") + ":"), 2, 0)
        self.tokenField = QLineEdit()
        self.tokenField.setReadOnly(True)
        self.tokenField.setEchoMode(QLineEdit.Password)
        form.addWidget(self.tokenField, 2, 1)

        tokenButtons = QHBoxLayout()
        btShowToken = QPushButton(_tr("Show"))
        btShowToken.setCheckable(True)
        btShowToken.toggled.connect(
            lambda shown: self.tokenField.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password))
        tokenButtons.addWidget(btShowToken)
        btCopyToken = QPushButton(_tr("Copy"))
        btCopyToken.clicked.connect(
            lambda: QApplication.clipboard().setText(WebAccess.Token()))
        tokenButtons.addWidget(btCopyToken)
        btRegenerateToken = QPushButton(_tr("Regenerate"))
        btRegenerateToken.clicked.connect(self._regenerateToken)
        tokenButtons.addWidget(btRegenerateToken)
        form.addLayout(tokenButtons, 2, 2, 1, 2)

        # What LAN access means (spec §2.2): from another device, writes need
        # the token. Emphasised so the operator sets the token up on each
        # device. Text is swapped in _refreshBaseUrl() when LAN access is
        # disabled.
        self.warningLabel = QLabel()
        self.warningLabel.setWordWrap(True)
        self.warningLabel.setStyleSheet("color: #e2574c;")
        form.addWidget(self.warningLabel, 3, 0, 1, 4)

        form.setColumnStretch(1, 1)
        group.setLayout(form)
        return group

    def _refreshBaseUrl(self):
        port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
        self.baseUrlField.setText(self._baseUrl())
        self.portLabel.setText(_tr("Port") + f": {port}")
        self.tokenField.setText(WebAccess.Token())
        if SettingsManager.Get("general.webserver_listen_lan", False):
            self.warningLabel.setText("⚠ " + _tr(
                "From other devices on the LAN, every operation that changes "
                "something (and the player list) needs the access token: send "
                "the header X-Autona-Token: <token> (or Authorization: Bearer "
                "<token>, or add ?token=<token> to the URL). Reading the state "
                "and the overlays needs no token."))
        else:
            self.warningLabel.setText(_tr(
                "LAN access is disabled; the API is reachable from this PC only. "
                "Enable it under Settings → General (restart required)."))

    def _regenerateToken(self):
        # Button context, so the confirm is a top-level modal, not a nested
        # one (CLAUDE.md / agent-pitfalls #9) -- same as the tester's.
        answer = QMessageBox.question(
            self,
            _tr("Regenerate access token"),
            _tr("Issue a new access token? Devices that use the current one "
                "(Companion, Stream Deck, a browser on another PC) can no "
                "longer change anything until they are given the new token."))
        if answer != QMessageBox.Yes:
            return
        self.tokenField.setText(WebAccess.RegenerateToken())

    def _openSettings(self):
        # The Settings dialog (with the Webserver Port field) lives on the
        # main window; reuse it rather than duplicating the port control.
        # Built on first open by the main window (startup Step 4a), so go
        # through its accessor rather than a plain attribute.
        window = self.parent()
        ensure = getattr(window, "EnsureSettingsWindow", None)
        if ensure is not None:
            ensure().show()

    # ── Reference tab ────────────────────────────────────────────────────
    def _buildReferenceTab(self):
        tab = QWidget()
        v = QVBoxLayout()

        headers = [_tr("Method"), _tr("Path"), _tr("Description"),
                   _tr("Confirm"), _tr("Logged")]
        self.refTable = QTableWidget(len(_ENDPOINTS), len(headers))
        self.refTable.setHorizontalHeaderLabels(headers)
        self.refTable.verticalHeader().setVisible(False)
        self.refTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.refTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.refTable.setSelectionMode(QAbstractItemView.SingleSelection)

        for row, ep in enumerate(_ENDPOINTS):
            method, path, desc, body, destructive, logged, confirm = ep
            self.refTable.setItem(row, 0, QTableWidgetItem(method))
            self.refTable.setItem(row, 1, QTableWidgetItem(path))
            self.refTable.setItem(row, 2, QTableWidgetItem(_tr(desc)))
            self.refTable.setItem(
                row, 3, QTableWidgetItem("✓" if confirm else ""))
            self.refTable.setItem(
                row, 4, QTableWidgetItem("✓" if logged else ""))

        self.refTable.resizeColumnsToContents()
        self.refTable.horizontalHeader().setStretchLastSection(False)
        self.refTable.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.refTable.doubleClicked.connect(
            lambda: self._loadSelectedIntoTester())
        v.addWidget(self.refTable, 1)

        btnRow = QHBoxLayout()
        btCurl = QPushButton(_tr("Copy curl"))
        btCurl.clicked.connect(self._copyCurlForSelected)
        btnRow.addWidget(btCurl)

        btLoad = QPushButton(_tr("Load into tester"))
        btLoad.clicked.connect(self._loadSelectedIntoTester)
        btnRow.addWidget(btLoad)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        v.addWidget(self._buildLegend())

        # Legacy-route notice: the old /scoreboardN-xxx style routes still
        # work unchanged but are deprecated in favour of /api/v1 (ADR-001).
        legacy = QLabel(_tr(
            "Deprecated: the legacy routes (/scoreboardN-..., /score, "
            "/program-state, etc.) will be removed in v2.0. Use /api/v1 "
            "(listed above) for new integrations."))
        legacy.setWordWrap(True)
        legacy.setStyleSheet("color: #e2574c;")
        v.addWidget(legacy)

        tab.setLayout(v)
        return tab

    def _buildLegend(self):
        box = QGroupBox(_tr("Envelope & error codes"))
        g = QVBoxLayout()

        envelope = QLabel(
            _tr("Success") + ': { "ok": true, "data": ..., "revision": 1234 }\n'
            + _tr("Error")
            + ': { "ok": false, "error": { "code": ..., "message": ... } }')
        envelope.setTextInteractionFlags(Qt.TextSelectableByMouse)
        envelope.setStyleSheet("font-family: monospace;")
        g.addWidget(envelope)

        codes = QLabel("\n".join(
            f"{code}  ({http})  {_tr(desc)}"
            for code, http, desc in _ERROR_CODES))
        codes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        codes.setStyleSheet("font-family: monospace;")
        g.addWidget(codes)

        box.setLayout(g)
        return box

    def _selectedEndpoint(self):
        row = self.refTable.currentRow()
        if row < 0:
            return None
        return _ENDPOINTS[row]

    def _copyCurlForSelected(self):
        ep = self._selectedEndpoint()
        if ep is None:
            return
        QApplication.clipboard().setText(self._buildCurl(ep))

    def _buildCurl(self, ep):
        method, path, _desc, body, _destructive, _logged, _confirm = ep
        url = self._baseUrl() + _concrete_path(path)
        parts = [f'curl -X {method} "{url}"']
        # The Base URL is the LAN address when LAN access is on, i.e. the
        # command is meant for another device: give it the token a write
        # needs there (ADR-005 S3a).
        if method != "GET" and SettingsManager.Get(
                "general.webserver_listen_lan", False):
            parts.append(f'-H "{WebAccess.TOKEN_HEADER}: {WebAccess.Token()}"')
        if body is not None:
            parts.append('-H "Content-Type: application/json"')
            parts.append("-d '" + json.dumps(body, ensure_ascii=False) + "'")
        return " ".join(parts)

    def _loadSelectedIntoTester(self):
        ep = self._selectedEndpoint()
        if ep is None:
            return
        self._fillTester(ep)
        self.tabs.setCurrentIndex(1)  # jump to the tester tab

    def _fillTester(self, ep):
        method, path, _desc, body, _destructive, _logged, _confirm = ep
        # Method can carry a query string in the path (e.g. /sets?finished=true);
        # the combo only holds verbs, so set both independently.
        verb = method if method in ("GET", "POST", "PATCH") else "GET"
        self.methodCombo.setCurrentText(verb)
        self.pathField.setText(_concrete_path(path))
        self.bodyEdit.setPlainText(
            json.dumps(body, ensure_ascii=False, indent=2) if body else "")

    # ── Tester tab ───────────────────────────────────────────────────────
    def _buildTesterTab(self):
        tab = QWidget()
        v = QVBoxLayout()

        presetRow = QHBoxLayout()
        presetRow.addWidget(QLabel(_tr("Preset") + ":"))
        self.presetCombo = QComboBox()
        self.presetCombo.addItem("—", -1)
        for i, ep in enumerate(_ENDPOINTS):
            self.presetCombo.addItem(f"{ep[0]}  {ep[1]}", i)
        self.presetCombo.activated.connect(self._applyPreset)
        presetRow.addWidget(self.presetCombo, 1)
        v.addLayout(presetRow)

        reqRow = QHBoxLayout()
        self.methodCombo = QComboBox()
        self.methodCombo.addItems(["GET", "POST", "PATCH"])
        reqRow.addWidget(self.methodCombo)
        self.pathField = QLineEdit("/health")
        reqRow.addWidget(self.pathField, 1)
        v.addLayout(reqRow)

        idemRow = QHBoxLayout()
        idemRow.addWidget(QLabel("Idempotency-Key:"))
        self.idemField = QLineEdit()
        self.idemField.setPlaceholderText(_tr("optional"))
        idemRow.addWidget(self.idemField, 1)
        v.addLayout(idemRow)

        v.addWidget(QLabel(_tr("Body (JSON)") + ":"))
        self.bodyEdit = QPlainTextEdit()
        self.bodyEdit.setPlaceholderText('{ "delta": 1 }')
        self.bodyEdit.setMaximumHeight(120)
        v.addWidget(self.bodyEdit)

        self.sendBtn = QPushButton(_tr("Send"))
        self.sendBtn.setObjectName("primary")
        self.sendBtn.clicked.connect(self._sendRequest)
        v.addWidget(self.sendBtn)

        v.addWidget(QLabel(_tr("Response") + ":"))
        self.responseEdit = QPlainTextEdit()
        self.responseEdit.setReadOnly(True)
        self.responseEdit.setStyleSheet("font-family: monospace;")
        v.addWidget(self.responseEdit, 1)

        tab.setLayout(v)
        return tab

    def _applyPreset(self, _index):
        i = self.presetCombo.currentData()
        if i is None or i < 0:
            return
        self._fillTester(_ENDPOINTS[i])

    def _isDestructivePath(self, path):
        return any(marker in path for marker in
                   (":clear", ":reset-players", ":reset-match"))

    def _sendRequest(self):
        method = self.methodCombo.currentText()
        path = self.pathField.text().strip()
        if not path.startswith("/"):
            path = "/" + path

        # Destructive ops get a click-triggered confirm (button context, so no
        # modal nesting concern — CLAUDE.md / agent-pitfalls #9).
        if method != "GET" and self._isDestructivePath(path):
            answer = QMessageBox.question(
                self,
                _tr("Confirm"),
                _tr("This is a destructive operation. Send it?")
                + f"\n\n{method} {path}")
            if answer != QMessageBox.Yes:
                return

        body_text = self.bodyEdit.toPlainText().strip()
        body = None
        if body_text:
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError as e:
                self.responseEdit.setPlainText(_tr("Invalid JSON body") + f": {e}")
                return

        headers = {}
        if self.idemField.text().strip():
            headers["Idempotency-Key"] = self.idemField.text().strip()

        # Resolve against the local server (always reachable on loopback even
        # if the Base URL shows the LAN address).
        port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
        url = f"http://127.0.0.1:{port}/api/v1{path}"

        self.sendBtn.setEnabled(False)
        self.responseEdit.setPlainText(_tr("Sending…"))

        # The answer (or the error) comes back on the GUI thread through the
        # long-lived GUI executor, to this window if it still exists.
        future = TaskExecutor.instance.submit(
            self._performRequest, method, url, body, headers,
            task_name="API tester request",
            then=lambda text: AutonaQtHelper.gui_executor(
                lambda: self._deliver(self._onResponse, text)),
            on_error=lambda error: AutonaQtHelper.gui_executor(
                lambda: self._deliver(self._onError, error)))
        if future.cancelled():
            # The app is shutting down (the executor has stopped): nothing
            # will be sent, so give the button back now.
            self._onError(RuntimeError(_tr("The app is shutting down")))

    # Runs on a job-lane thread: no Qt widget access here, just return data.
    @staticmethod
    def _performRequest(method, url, body, headers):
        resp = requests.request(
            method, url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT)
        try:
            pretty = json.dumps(resp.json(), ensure_ascii=False, indent=2)
        except ValueError:
            pretty = resp.text
        return f"HTTP {resp.status_code} {resp.reason}\n\n{pretty}"

    def _deliver(self, handler, value):
        # GUI thread. The window may have been destroyed while the request
        # ran (the Worker's signal connections were cut with it).
        if Shiboken.isValid(self):
            handler(value)

    def _onResponse(self, text):
        self.responseEdit.setPlainText(text)
        self.sendBtn.setEnabled(True)

    def _onError(self, error):
        # The request's exception (logged once by the executor).
        self.responseEdit.setPlainText(_tr("Request failed") + f":\n{error}")
        self.sendBtn.setEnabled(True)
