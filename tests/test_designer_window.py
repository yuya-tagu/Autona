"""The Designer window revokes its token on every way it closes.

QDialog.done() -- reject() on Esc, accept() -- closes the dialog without
calling closeEvent (Qt eats that QCloseEvent), so a token revoked only there
outlived the window.
"""

import pytest
from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWebEngineWidgets import QWebEngineView

from src import AutonaDesignerWindow as WindowModule
from src.AutonaDesignerService import AutonaDesignerService


@pytest.fixture
def designerWindow(qapp, monkeypatch):
    # No server and no network: a fixed URL, and the view never loads it.
    monkeypatch.setattr(WindowModule, "LocalUrl",
                        lambda path: f"http://127.0.0.1:6200{path}")
    monkeypatch.setattr(QWebEngineView, "setUrl", lambda self, url: None)
    built = []

    def build():
        window = WindowModule.AutonaDesignerWindow()
        built.append(window)
        assert AutonaDesignerService.is_authorized(window._token, "127.0.0.1")
        return window

    yield build
    for window in built:
        AutonaDesignerService.revoke_token(window._token)
        try:
            window.close()      # a window left behind by a failed assert
        except RuntimeError:
            pass    # already deleted
        # WA_DeleteOnClose posted a DeferredDelete; collect it for this
        # object only (processEvents does not run it outside exec()).
        try:
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        except RuntimeError:
            pass    # already deleted


def _authorized(token):
    return AutonaDesignerService.is_authorized(token, "127.0.0.1")


def test_reject_revokes_the_token(designerWindow):
    window = designerWindow()
    token = window._token
    window.show()
    window.reject()     # what Esc does
    assert not _authorized(token)


def test_accept_revokes_the_token(designerWindow):
    window = designerWindow()
    token = window._token
    window.show()
    window.done(1)
    assert not _authorized(token)


def test_close_revokes_the_token(designerWindow):
    window = designerWindow()
    token = window._token
    window.show()
    window.close()
    assert not _authorized(token)


def test_a_close_after_reject_is_harmless(designerWindow, monkeypatch):
    window = designerWindow()
    token = window._token
    revoked = []
    real = AutonaDesignerService.revoke_token.__func__
    monkeypatch.setattr(AutonaDesignerService, "revoke_token", classmethod(
        lambda cls, t: (revoked.append(t), real(cls, t))))
    window.show()
    window.reject()
    window.close()      # closeEvent and finished again: revoked once
    assert revoked == [token]
    assert not _authorized(token)
