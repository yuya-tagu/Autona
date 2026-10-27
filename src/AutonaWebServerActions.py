"""The web layer's board-dependent operations (ADR-005 S3c).

What is left here needs a board or panel *widget* to answer: the set
payload a board's ChangeSetData applies (post_score), the set selection and
stream-set follow (open_sets, pull_stream_set, load_set) and the
thumbnail. Every operation that is core's moved to
src/AutonaWebOperations.py, which both shells share (the crew board's
members and the caster line-up with ADR-005 S3e-2, the Top 8 import with
S3e-8, the player edits and the tag lookup with S3e-4, the statistics with
S3e-5); this object exists only in the desktop shell (the window hands it
to the WebServer), and in the headless shell the routes behind it answer 503
(AutonaWebOperations.NotAvailable). It goes in S3e, once these are core
operations too.
"""
import os
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import orjson

from .Helpers.AutonaQtHelper import gui_thread_sync
from loguru import logger


class WebServerActions(QObject):
    def __init__(self, parent=None, scoreboard=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard

    @gui_thread_sync
    def post_score(self, data):
        score = orjson.loads(data)
        scoreboard_number = 1

        if "scoreboard" in score:
            try:
                scoreboard_number = int(score["scoreboard"])
            except ValueError:
                logger.warning(f"Couldn't parse scoreboard [${score['scoreboard']}] from /post_data as int, falling back to scoreboard 1")
                scoreboard_number = 1

        score.update({"reset_score": True})
        self.scoreboard.GetScoreboard(scoreboard_number).signals.ChangeSetData.emit(score)
        return "OK"

    def open_sets(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.SetSelection.emit()
        return "OK"

    def pull_stream_set(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.StreamSetSelection.emit()
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

    @gui_thread_sync
    def load_set(self, scoreboard, set=None, no_mains=False):
        if set is not None:
            if not isinstance(set, str):
                set = '0'
            self.scoreboard.GetScoreboard(scoreboard).signals.NewSetSelected.emit(
                orjson.loads(
                    orjson.dumps({
                        'id': set,
                        'auto_update': "set",
                        'no_mains': no_mains
                    })
                )
            )
        return "OK"
