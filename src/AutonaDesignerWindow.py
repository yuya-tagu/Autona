"""Desktop host window for the layer-based Scoreboard Designer."""

from qtpy.QtCore import Qt, QUrl, QUrlQuery
from qtpy.QtWidgets import QDialog, QVBoxLayout
from qtpy.QtWebEngineWidgets import QWebEngineView

from .AutonaDesignerService import AutonaDesignerService
from .SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT


class AutonaDesignerWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Autona Scoreboard Designer")
        self.resize(1440, 900)
        self.setMinimumSize(800, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._token = AutonaDesignerService.issue_token()
        self._tokenRevoked = False

        self.webView = QWebEngineView(self)
        self.webView.setZoomFactor(1.0)
        self.webView.page().windowCloseRequested.connect(self.close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.webView)

        port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
        url = QUrl(f"http://127.0.0.1:{port}/web")
        query = QUrlQuery()
        query.addQueryItem("view", "designer")
        query.addQueryItem("desktop", "1")
        query.addQueryItem("token", self._token)
        url.setQuery(query)
        self.webView.setUrl(url)

    def closeEvent(self, event):
        self._revokeToken()
        super().closeEvent(event)

    def _revokeToken(self):
        if not self._tokenRevoked:
            AutonaDesignerService.revoke_token(self._token)
            self._tokenRevoked = True
