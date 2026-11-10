# The Qt side of a match thumbnail, drawn synchronously. The settings, the
# payload, the names and the generations are core/ThumbnailService's (ADR-005
# S4o), and since S4y so is the synchronous thumbnail itself
# (ThumbnailService.DrawBoard, which SCORE's X post and the legacy GET
# /scoreboard<N>-get-thumbnail-<png|jpg> reach through AutonaShellOperations,
# not through a board). What is left here is the entry tests/live and the
# golden recorders call. Every way draws the settings as the React page shows
# them (RenderPayload, S4p). The synchronous preview (generate / save_result's
# `isPreview`, tmp/thumbnail/template.png) is gone; prepare_data's `isPreview`
# is RenderPayload's placeholder preview, which the golden recorder still draws.

from pathlib import Path

from loguru import logger

from src.core import ThumbnailService as service
from src.Helpers.AutonaOutputRetention import PruneOutputFiles

SUPPORTED_TEMPLATE_IDS = service.TEMPLATE_IDS
DEFAULT_TEMPLATE_ID = service.DEFAULT_TEMPLATE_ID
BANNER_PATH = service.BANNER_PATH
DEFAULT_BANNER_PATH = service.DEFAULT_BANNER_PATH

resolve_background_path = service.ResolveBackgroundPath


def prepare_data(settingsManager, isPreview=False, gameAssetManager=None, scoreboardNumber=1):
    """Prepare data, config, and template for thumbnail rendering.

    Returns (data, config, template) tuple.
    Raises on error for non-preview mode.

    `settingsManager` is the one SettingsManager, which the service reads.
    The settings are given as they read (what the page shows), like the v1
    renders' (ADR-005 S4p).
    """
    games = gameAssetManager.instance.catalog if gameAssetManager else None
    return service.ThumbnailService.instance.RenderPayload(
        preview=isPreview, games=games, board=scoreboardNumber)


def save_result(pixmap, data, settings, scoreboardNumber=1):
    """Save rendered pixmap to disk. Returns output file path."""
    out_path = service.OUTPUT_DIRECTORY
    Path(out_path).mkdir(parents=True, exist_ok=True)

    thumbnail_filename = service.OutputStem(data, scoreboardNumber)

    pixmap.save(f"{out_path}/{thumbnail_filename}.png")
    pixmap.save(f"{out_path}/{thumbnail_filename}.jpg")
    logger.info(
        f"Thumbnail successfully saved as {out_path}/{thumbnail_filename}.png and {out_path}/{thumbnail_filename}.jpg")
    # Keep disk usage bounded; the file just written is protected
    PruneOutputFiles(out_path, service.OUTPUT_PATTERNS, service.OUTPUT_KEEP,
                     protect=f"{out_path}/{thumbnail_filename}.png")
    return f"{out_path}/{thumbnail_filename}.png"


def generate(settingsManager, gameAssetManager=None, scoreboardNumber=1):
    """Generate a thumbnail synchronously. A preview in the background is
    core/ThumbnailService's.

    The thumbnail itself is core's ThumbnailService.DrawBoard with the
    desktop's synchronous draw (ADR-005 S4y): the one way SCORE's X post
    and the legacy route take (AutonaShellOperations.DrawThumbnail), so this
    writes the same files."""
    from src.AutonaShellOperations import DrawThumbnailNow
    games = gameAssetManager.instance.catalog if gameAssetManager else None
    return service.ThumbnailService.instance.DrawBoard(
        scoreboardNumber, DrawThumbnailNow, games=games)
