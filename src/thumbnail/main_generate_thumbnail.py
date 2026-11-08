# The Qt side of a match thumbnail, for the two ways that render synchronously
# and keep their contracts: SCORE's button (and its X post) and the legacy
# GET /scoreboard<N>-get-thumbnail-<png|jpg>. The settings, the payload, the
# names and the generations are core/ThumbnailService's (ADR-005 S4o); what is
# left here is the renderer's QPixmap. Both draw the settings as the React page
# shows them (RenderPayload, S4p). `isPreview` was the Qt panel's, gone in S4p:
# nothing passes True any more, and the signatures stay what SCORE, the legacy
# route and tests/live call until the Board panel moves.

from pathlib import Path

from loguru import logger

from src.core import ThumbnailService as service
from src.Helpers.AutonaDirHelper import AutonaResolve
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


def save_result(pixmap, data, settings, scoreboardNumber=1, isPreview=False):
    """Save rendered pixmap to disk. Returns output file path."""
    out_path = service.OUTPUT_DIRECTORY if not isPreview else AutonaResolve("tmp/thumbnail")
    Path(out_path).mkdir(parents=True, exist_ok=True)

    if not isPreview:
        thumbnail_filename = service.OutputStem(data, scoreboardNumber)

        pixmap.save(f"{out_path}/{thumbnail_filename}.png")
        pixmap.save(f"{out_path}/{thumbnail_filename}.jpg")
        logger.info(
            f"Thumbnail successfully saved as {out_path}/{thumbnail_filename}.png and {out_path}/{thumbnail_filename}.jpg")
        # Keep disk usage bounded; the file just written is protected
        PruneOutputFiles(out_path, service.OUTPUT_PATTERNS, service.OUTPUT_KEEP,
                         protect=f"{out_path}/{thumbnail_filename}.png")
        return f"{out_path}/{thumbnail_filename}.png"
    else:
        thumbnail_filename = "template"
        pixmap.save(f"{out_path}/{thumbnail_filename}.png")
        logger.info(
            f"Thumbnail preview saved as {out_path}/{thumbnail_filename}.png")
        return f"{out_path}/{thumbnail_filename}.png"


def generate(settingsManager, isPreview=False, gameAssetManager=None, scoreboardNumber=1):
    """Generate a thumbnail synchronously (SCORE's button and the legacy
    route). A preview in the background is core/ThumbnailService's."""
    from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer

    data, config, template_data = prepare_data(
        settingsManager, isPreview, gameAssetManager, scoreboardNumber)

    renderer = ThumbnailRenderer.instance()
    pixmap = renderer.renderSync(
        data=data,
        config=config,
        template=template_data,
    )

    if pixmap.isNull():
        logger.error("Thumbnail rendering produced an empty image")
        if isPreview:
            return None
        raise Exception("Thumbnail rendering failed")

    settings = settingsManager.Get("thumbnail_config") or {}
    return save_result(pixmap, data, settings, scoreboardNumber, isPreview)
