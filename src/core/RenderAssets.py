"""What an off-screen render may read besides what the preview listener serves
(ADR-007 D4 / T2; ADR-008 D7 R1).

The tournament result and the match thumbnail are drawn on the preview
listener's origin (``http://127.0.0.1:<previewPort>/layout/<folder>/<entry>``,
AutonaOffscreenRenderer): the page reads what that listener serves
(WebAccess.PreviewServes: ``layout/``, ``assets/``, the state's images under
``user_data/``, v1's READ) and nothing else -- its requests elsewhere are
stopped. A few of a payload's images are files the listener does not serve:

- the uploaded thumbnail background, ``./user_data/thumbnail_background_<hash>
  .png`` (ThumbnailService.ImportBackground), which the result follows too;
- the archive clipper's frame, ``<job folder>/..._bg.jpg``, given as a file
  URL (ArchiveJobService.StartThumbs; the job folder is anywhere);
- a ``background_path`` / ``main_icon_path`` saved by an older version (the
  Qt panels took any file): a drive path, a UNC path, ``./tmp/...``.

Such a file is held for the render under an id (Hold) and the template is
given ``/render-asset/<id>`` instead of the path: the template never sees a
path of this PC's, and the listener serves the file (HeldPath; to this PC's
loopback alone) only while a render holds it -- ForRender at the render's
start, Release at its end, however it ended. The id is not the render's but
the file version's: the same file (path, size and modification time) is the
same id throughout the process, held again by each render that draws it, so
a template shows the image it decoded already instead of fetching and
decoding it again at every preview (thumbnail.js's background has no cache
buster for that reason), and an image changed in place is a new id. The id
is an HMAC of those under a secret made once per process, in memory: it
cannot be guessed, and is not a path.

What ForRender does to each of PATH_KEYS of a payload's ``config`` (the only
keys of the result's and the thumbnail's payloads that hold an arbitrary path;
the rest are the state's own images and the template's own files):

- a path the templates resolve against the app's folder (``./...``,
  ``assets/``, ``user_data/``, ``layout/``: their ``resolveAssetPath``) that the
  preview listener serves is kept, relative, with ``m=<modification time>``
  added to its query (the listener's files are cached for a day; the banner is
  replaced in place) -- looked for where the listener serves it from, the
  app's directory (LayoutCatalog.AppDirectory);
- any other local file -- such a path the listener does not serve, a drive
  path, a ``file:`` URL of this PC -- is held: ``/render-asset/<id>``, an
  origin-absolute path the templates' ``resolveAssetPath`` passes on as it
  is;
- a local file that is not there, or not an image (IMAGE_EXTENSIONS), is
  ``""``: the template draws its default, and is given no path. So is a
  network path (UNC, or a ``file:`` URL of another host): looking at it from
  the GUI thread, where a render starts, could wait on the network;
- anything else (``data:`` / ``blob:`` / ``http(s):`` URLs, ``/...``, a path
  relative to the template's own folder) is kept as it is: the page resolves
  it, and the renderer's request filter decides.

The payload drawn changes, not what the services build: their golden files
(tests/fixtures/*/golden.json) are what the services hand the renderer.

Thread-safety: one lock guards the held files; the web server's threads read
them (HeldPath) while the GUI thread holds and releases. Import side effects:
none (pitfalls #1) -- the secret is made on the first hold.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
from urllib.parse import urlsplit
from urllib.request import url2pathname

from loguru import logger

from . import LayoutCatalog, WebAccess

# Where the preview listener serves a held file (AutonaWebServer's
# render_asset), with its id.
URL_PREFIX = "/render-asset/"
# The keys of a payload's config whose value may be any path.
PATH_KEYS = ("background_path", "main_icon_path")
# What may be held: the images a template draws (a background, an icon).
IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
# The prefixes the templates resolve against the app's folder
# (resolveAssetPath: "../../" + the path).
APP_RELATIVE_PREFIXES = ("./", "assets/", "user_data/", "layout/")
# A held file's id: 16 bytes of the HMAC, URL-safe base64 without padding.
_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_KEPT_SCHEMES = ("data:", "blob:", "http:", "https:")

_lock = threading.Lock()
_secret = None
# id -> [the file's real path, how many renders hold it]
_held = {}


def ForRender(payload):
    """(the payload to draw, the ids it holds): ``payload`` with its config's
    PATH_KEYS readable from the preview's origin (see the module docstring),
    and the files held for it, which the caller lets go (Release) once the
    render has ended, however it ended. The payload given is not changed (the
    copy shares everything but the config). Never raises: a value that cannot
    be read is ``""``."""
    if not isinstance(payload, dict) or not isinstance(
            payload.get("config"), dict):
        return payload, []
    config = dict(payload["config"])
    held = []
    for key in PATH_KEYS:
        value = config.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            config[key], assetId = _Readable(value)
        except Exception:
            logger.opt(exception=True).warning(
                f"RenderAssets: the render's {key} could not be read")
            config[key], assetId = "", None
        if assetId is not None:
            held.append(assetId)
    drawn = dict(payload)
    drawn["config"] = config
    return drawn, held


def Release(ids):
    """Let go of the files a render held (ForRender's ids): one no render
    holds any more is not served again."""
    with _lock:
        for assetId in ids:
            entry = _held.get(assetId)
            if entry is None:
                continue
            entry[1] -= 1
            if entry[1] <= 0:
                del _held[assetId]


def HeldPath(assetId):
    """The file held under ``assetId`` while a render holds it, else None
    (never held, let go, not an id)."""
    if not isinstance(assetId, str) or not _ID.match(assetId):
        return None
    with _lock:
        for held, entry in _held.items():
            if hmac.compare_digest(held, assetId):
                return entry[0]
    return None


def Url(assetId):
    """What the template is given for a held file."""
    return URL_PREFIX + assetId


# ── internals ────────────────────────────────────────────────────────────

def _Readable(value):
    """(what the template is given for ``value``, the id held or None)."""
    lowered = value.lower()
    if lowered.startswith("file:"):
        return _HoldOrNothing(_FileUrlPath(value))
    if value.startswith(("\\\\", "//")):
        logger.warning("RenderAssets: a render's image is on a network path; "
                       "not drawn (it is not looked at while drawing)")
        return "", None
    if _DRIVE.match(value):
        return _HoldOrNothing(value)
    if lowered.startswith(_KEPT_SCHEMES) or value.startswith("/"):
        return value, None
    if value.startswith(APP_RELATIVE_PREFIXES):
        path, query = _SplitQuery(value)
        local = path[2:] if path.startswith("./") else path
        served = WebAccess.PreviewStaticPath(local)
        if served is not None:
            return _Versioned(path, query, served), None
        return _HoldOrNothing(path)
    # Relative to the template's own folder (a package's image), or
    # something no file is: the page resolves it.
    return value, None


def _SplitQuery(value):
    """(the path, the query and fragment after it, "" when none)."""
    for index, character in enumerate(value):
        if character in "?#":
            return value[:index], value[index:]
    return value, ""


def _Versioned(path, query, served):
    """A served path with its file's modification time in the query, so that
    a file replaced in place (the banner) is not the one cached; "" when the
    listener has no such file (``served``, under the app's directory)."""
    try:
        file = os.path.join(LayoutCatalog.AppDirectory(), served)
        if not os.path.isfile(file):
            logger.warning("RenderAssets: a render's image is not there")
            return ""
        stamp = os.stat(file).st_mtime_ns
    except (OSError, ValueError):
        logger.warning("RenderAssets: a render's image is not there")
        return ""
    if query.startswith("#") or not query:
        return f"{path}?m={stamp:x}{query}"
    query, hash_, fragment = query.partition("#")
    return f"{path}{query}&m={stamp:x}{hash_}{fragment}"


def _FileUrlPath(value):
    """The local path of a ``file:`` URL, or "" when it names another host
    (a network path: see _Readable). url2pathname decodes the path's
    percent-escapes itself: decoded twice, a file named ``%41.png``
    (``%2541.png`` in its URL) would be looked for as ``A.png``."""
    parts = urlsplit(value)
    if parts.netloc not in ("", "localhost"):
        return ""
    path = url2pathname(parts.path)
    if path.startswith(("\\\\", "//")):
        return ""
    return path


def _HoldOrNothing(path):
    assetId = _Hold(path) if path else None
    return (Url(assetId), assetId) if assetId is not None else ("", None)


def _Hold(path):
    """Hold the image file ``path`` (relative paths: to the working
    directory, as the services wrote them) for a render: its id, or None
    when it is not a file there or not an image."""
    try:
        real = os.path.realpath(path)
        if os.path.splitext(real)[1].lower() not in IMAGE_EXTENSIONS:
            logger.warning("RenderAssets: a render's image is not one "
                           "(its file type is not taken)")
            return None
        status = os.stat(real)
    except (OSError, ValueError):
        logger.warning("RenderAssets: a render's image is not there")
        return None
    if not os.path.isfile(real):
        logger.warning("RenderAssets: a render's image is not a file")
        return None
    global _secret
    with _lock:
        if _secret is None:
            _secret = secrets.token_bytes(32)
        digest = hmac.new(
            _secret,
            f"{os.path.normcase(real)}\0{status.st_size}\0"
            f"{status.st_mtime_ns}".encode("utf-8", "surrogatepass"),
            hashlib.sha256).digest()[:16]
        assetId = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        entry = _held.get(assetId)
        if entry is None:
            _held[assetId] = [real, 1]
        else:
            entry[1] += 1
    return assetId
