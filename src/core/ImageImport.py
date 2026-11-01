"""Taking an image in as an asset file (ADR-005 S4f).

What the stream org assets (core/StreamService.py, S4d) and the tournament
icon / banner (core/TournamentMediaService.py, S4f) do with an image the
operator picked or a tournament provided, in one place:

- **checked**: a PNG, JPEG, MPO (its first frame) or BMP, up to MAX_PIXELS
  by its header (refused before anything is decoded);
- **scaled down** to fit a longest side (never enlarged), turned upright by
  its EXIF orientation (a phone's portrait photo), transparency kept (RGBA),
  and encoded as PNG -- ``EncodePng``;
- **written atomically**: through a hidden sibling temporary file
  (``.logo.<random>.tmp``) and ``os.replace``, retried while a reader holds
  the target open (Windows), so an overlay never reads a half-written file
  -- ``WriteFile``. What a crash left of those is removed by the owner at
  construction -- ``RemoveLeftovers``.

Publishing the file's version (its mtime, ``StateSchema.PublishAssetVersion``)
is the caller's, under its own lock, so a file and its version always belong
together. Qt-free (tests/test_core_qt_free.py); import side effects: none
(agent-pitfalls #1).
"""
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path

from loguru import logger
from PIL import Image, ImageOps

# What an import answers.
IMPORTED = "imported"
INVALID_IMAGE = "invalid_image"   # not a PNG / JPEG / BMP within the cap
SAVE_FAILED = "save_failed"       # the file could not be written

# What the panels' file dialogs offered (*.png *.jpg *.bmp), as PIL names it.
# MPO is a JPEG with more frames after the first (what many phones and
# cameras save as .jpg); its first frame is the picture.
FORMATS = ("PNG", "JPEG", "MPO", "BMP")
# The largest image taken in, by its header's width x height: 50 megapixels
# -- above the photos phones commonly take (48 MP) and an 8K frame (33 MP),
# while a PNG / BMP of that size decodes to about 200 MB as RGBA, before it
# is scaled down (a JPEG is decoded at a reduced scale, see EncodePng).
# PIL's own guard (a warning at 89 MP, an error at 179) is process-wide and
# left as it is.
MAX_PIXELS = 50_000_000

# The headers an image is downloaded with (a tournament's icon / banner):
# what start.gg's image host answers a browser with.
DOWNLOAD_HEADERS = {
    "client-version": "20",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/119.0.0.0 Mobile Safari/537.36",
}

# The file is replaced through a sibling temporary file; on Windows a
# reader holding the target open (an overlay, a panel's preview) makes
# os.replace fail with a sharing violation. Tried this many times: 20 + 40
# + 80 ms of sleep at worst -- PlayerDB's budget.
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02


def PngReady(image):
    """`image` in a mode PNG keeps as is: RGBA when it has transparency,
    RGB otherwise (what the panels' QPixmap saved)."""
    if image.mode in ("RGB", "RGBA"):
        return image
    transparent = (image.mode in ("LA", "PA")
                   or "transparency" in image.info)
    return image.convert("RGBA" if transparent else "RGB")


def EncodePng(data, longestSide, label="image", maxPixels=None):
    """`data` (an image's bytes) scaled down to fit `longestSide` and
    encoded as PNG; None when it is not an image taken in (not bytes, empty,
    not one of FORMATS, over `maxPixels` -- MAX_PIXELS when None -- or
    broken). `label` names it in the log."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        logger.warning(f"refusing {label}: no image data")
        return None
    limit = MAX_PIXELS if maxPixels is None else maxPixels
    try:
        image = Image.open(BytesIO(data))
        if image.format not in FORMATS:
            logger.warning(f"refusing {label} in {image.format}")
            return None
        # The header's size: checked before anything is decoded.
        if image.width * image.height > limit:
            logger.warning(
                f"refusing {label} of {image.width}x{image.height} "
                f"(over {limit} pixels)")
            return None
        # Scaled down first, converted after, so the full-size image is
        # decoded once and never copied (thumbnail() decodes a JPEG / MPO
        # at a reduced scale already). Except an image in a mode resize()
        # scales poorly or not at all (a palette or 1-bit one: nearest
        # neighbour only; 16-bit grey): converted first.
        if image.mode not in ("L", "LA", "RGB", "RGBA", "CMYK"):
            image = PngReady(image)
        if image.width > longestSide or image.height > longestSide:
            image.thumbnail((longestSide, longestSide), Image.LANCZOS)
        # Upright, as a viewer shows it: a phone saves a portrait photo
        # landscape with an EXIF orientation. After the scaling (the box is
        # square, so the longest side is the same either way), on the
        # smaller image; an orientation PIL cannot read leaves it as is.
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            logger.opt(exception=True).warning(
                f"{label}: its EXIF orientation could not be applied")
        image = PngReady(image)
        encoded = BytesIO()
        image.save(encoded, "PNG")
        return encoded.getvalue()
    except Exception:
        logger.opt(exception=True).warning(
            f"{label} is not a readable image")
        return None


def WriteFile(path, data):
    """Write `data` to `path` through a sibling temporary file, so a reader
    (an overlay fetching the asset) never gets a half-written one. The
    replace is retried while a reader holds the target (Windows); OSError
    when it never went through. The temporary file never outlives this."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Hidden (a leading dot), so a leftover does not show among the layout
    # files an operator browses.
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=_TemporaryPrefix(target),
        suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        delay = _REPLACE_BACKOFF
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass  # replaced: the temporary file is the target now
        except OSError:
            logger.warning(f"could not remove {temporary}")


def _TemporaryPrefix(target):
    return f".{target.stem}."


def RemoveLeftovers(*paths):
    """Remove the temporary files WriteFile left beside each of `paths` --
    only a crash between its write and its replace leaves one. Called by a
    service at construction, before it writes: the files are its own (one
    service per process owns each path). Also the visible ones S4d's
    StreamService named ``<stem>.<random>.tmp``. A file that cannot be
    removed is logged and left."""
    for path in paths:
        target = Path(path)
        if not target.parent.is_dir():
            continue
        for pattern in (f"{_TemporaryPrefix(target)}*.tmp",
                        f"{target.stem}.*.tmp"):
            for leftover in target.parent.glob(pattern):
                try:
                    leftover.unlink()
                    logger.info(f"removed the leftover {leftover}")
                except OSError:
                    logger.warning(f"could not remove the leftover {leftover}")
