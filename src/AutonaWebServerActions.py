"""The web layer's board-dependent operations (ADR-005 S3c).

What is left here needs a board or panel *widget* to answer: the set
selection window (open_sets) and the thumbnail. Every operation that is
core's moved to src/AutonaWebOperations.py, which both shells share (the
crew board's members and the caster line-up with ADR-005 S3e-2, the Top 8
import with S3e-8, the player edits and the tag lookup with S3e-4, the
statistics with S3e-5, set loading -- load_set, post_score,
pull_stream_set -- with S3e-7); this object exists only in the desktop
shell (the window hands it to the WebServer), and in the headless shell the
routes behind it answer 503 (AutonaWebOperations.NotAvailable). It goes in
S3e-9, once these are shell operations.
"""
import os
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *

from .Helpers.AutonaQtHelper import gui_thread_sync
from loguru import logger


class WebServerActions(QObject):
    def __init__(self, parent=None, scoreboard=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard

    def open_sets(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.SetSelection.emit()
        return "OK"

    @gui_thread_sync
    def get_thumbnail(self, scoreboard, file_format):
        thumbnailPath = self.scoreboard.GetScoreboard(scoreboard).GenerateThumbnail(quiet_mode=True, disable_msgbox=True)
        if thumbnailPath:
            if file_format == "jpg":
                thumbnailPath = thumbnailPath.replace(".png", ".jpg")
            return os.path.abspath(thumbnailPath)
        else:
            return None
