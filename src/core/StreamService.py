"""The broadcast's own settings and generators (配信設定, ADR-005 S4d).

Everything the 配信設定 panel (``AutonaStreamSettingsWidget``) used to do
itself, so that it happens with no panel -- before the page is first shown,
and in the headless shell:

- **the stream title** (``stream.title``): rebuilt from the tournament name,
  the game's abbreviation and the tournament's top entrants whenever one of
  them changes, unless the operator typed a title of their own;
- **the stream channels** (settings.json ``stream_channels``, a list of
  ``{"name", "url"}`` -- the shape is unchanged);
- **the stream org's icon / thumbnail** (``./layout/stream_org_icon.png`` /
  ``stream_org_thumbnail.png``, scaled down with PIL, and their cache-bust
  versions ``stream.orgIconVersion`` / ``stream.orgThumbVersion``);
- **the stream thumbnail generator** (``./out/stream/thumbnail_*.jpg``), a
  job of the task executor whose progress and result are the state's
  ``stream.thumbnail``; its options are kept in settings.json
  (``stream_thumbnail_options``).

The panel only asks for these and draws the state; the file dialog, the
previews and the clipboard stay Qt's.

The title, in detail. The panel computed it on three Qt signals (tournament
data, entrants, game assets) with a memory of the last auto value that
lived in the panel -- so nothing was generated until the page was opened,
and never in the headless shell. Now:

- the tournament name and the game come from the state. A state observer
  (subscribed by ``Start()``) notices when either changes. It runs inside
  ``StateManager.lock`` (the publish that delivers it), so it writes
  nothing there and waits for nothing (agent-pitfalls #16 #17): it only
  queues one refresh with the injected ``later`` (the GUI executor's
  ``later``: the main thread, once its event loop is back). A refresh
  written from inside the observer would publish a second delta in the
  middle of the first one's delivery;
- the top entrants are not state: the tournament provider hands them over
  (``SetTopEntrants``) once its entrants fetch has them, and empties them
  when another tournament (or none) is loaded;
- every refresh publishes the generated title to ``stream.titleAuto`` and
  overwrites ``stream.title`` only while that still holds the last auto
  value or nothing (``TitleIsAuto``), so a title the operator typed stays
  until they clear it. The last auto value is state, not this service's
  memory: it is persisted, so a typed title survives a restart, and a
  client (the panel, the Web page) can tell an auto title from a typed one.
  A client's typing wins over an auto title that lands while the operator
  is in the field (``TypedTitleWins``): the generator never beats a hand.
  A state from before S4d has no ``titleAuto``; its title counts as auto
  (the panel regenerated it whenever it was opened).

``StreamService.instance`` is built and started by ``bootstrap_core()``;
the banner lookup (the provider's), the deferral and the output pruning are
injected there, because core may not import them (tests/test_core_qt_free).
Import side effects: none (agent-pitfalls #1).
"""
import datetime
import os
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path

import requests
from loguru import logger
from PIL import Image, ImageEnhance, ImageFilter

from . import StateSchema as schema
from .Actions import AutonaActions
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

TITLE_PATH = f"{schema.STREAM_ROOT}.{schema.STREAM_TITLE_KEY}"
TITLE_AUTO_PATH = f"{schema.STREAM_ROOT}.{schema.STREAM_TITLE_AUTO_KEY}"
URL_PATH = f"{schema.STREAM_ROOT}.{schema.STREAM_URL_KEY}"
THUMBNAIL_PATH = f"{schema.STREAM_ROOT}.{schema.STREAM_THUMBNAIL_KEY}"
TOURNAMENT_NAME_PATH = (
    f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_NAME_KEY}")

# ── the title ────────────────────────────────────────────────────────────

STREAM_TITLE_MAX_LENGTH = 100
# The seed of an entrant without one (the provider's stand-in).
NO_SEED = 999999
# Fallback abbreviation by smashgg_game_id when the per-game config.json
# does not specify one (games not installed in user_data/games/).
GAME_ABBREVIATION_FALLBACK = {
    1386: "SSBU",   # Super Smash Bros. Ultimate
    43868: "SF6",   # Street Fighter 6
}

# ── the channels and the thumbnail options (settings.json) ──────────────

CHANNELS_SETTING = "stream_channels"
THUMBNAIL_OPTIONS_SETTING = "stream_thumbnail_options"
THUMBNAIL_SOURCES = ("banner", "org")
THUMBNAIL_STYLES = ("center", "netflix")
DEFAULT_THUMBNAIL_OPTIONS = {
    "source": "banner",
    "style": "center",
    "overlayOrgIcon": False,
}

# ── the stream org's assets ──────────────────────────────────────────────

# kind -> (file, version key, longest side). 512 for the icon: the same cap
# as the tournament icon (oversized sources choke the off-screen
# QWebEngineView the match thumbnail renders in); 1920 keeps a full-HD
# thumbnail source usable without re-encoding.
ORG_ASSETS = {
    "icon": (schema.STREAM_ORG_ICON_PATH, "stream.orgIconVersion", 512),
    "thumbnail": (schema.STREAM_ORG_THUMB_PATH, "stream.orgThumbVersion",
                  1920),
}
# What the panel's file dialog offered (*.png *.jpg *.bmp), as PIL names it.
# MPO is a JPEG with more frames after the first (what many phones and
# cameras save as .jpg); its first frame is the picture.
ORG_ASSET_FORMATS = ("PNG", "JPEG", "MPO", "BMP")
# The largest image taken in, by its header's width x height: 50 megapixels
# -- above the photos phones commonly take (48 MP) and an 8K frame (33 MP),
# while a PNG / BMP of that size decodes to about 200 MB as RGBA, before it
# is scaled down (a JPEG is decoded at a reduced scale, see ImportOrgAsset).
# PIL's own guard (a warning at 89 MP, an error at 179) is process-wide and
# left as it is.
ORG_ASSET_MAX_PIXELS = 50_000_000

# What ImportOrgAsset answers.
IMPORTED = "imported"
INVALID_IMAGE = "invalid_image"   # not a PNG / JPEG / BMP within the cap
SAVE_FAILED = "save_failed"       # the file could not be written

# The asset is replaced through a sibling temporary file; on Windows a
# reader holding the target open (an overlay, the panel's preview) makes
# os.replace fail with a sharing violation. Tried this many times: 20 + 40
# + 80 ms of sleep at worst -- PlayerDB's budget.
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02

# ── the stream thumbnail ─────────────────────────────────────────────────

# Tournament-scoped assets, owned by the EVENT tab -- read here only as
# thumbnail material. (The match thumbnail keeps its own copy of the pair;
# sharing them is that panel's migration's business.)
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = "./user_data/tournament_banner.png"
BANNER_TIMEOUT_SECS = 20
BANNER_HEADERS = {
    "client-version": "20",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/119.0.0.0 Mobile Safari/537.36",
}

STREAM_OUTPUT_DIR = "./out/stream"
# out/stream may hold other files: only our own output is pruned.
THUMBNAIL_PATTERNS = ("thumbnail_*.jpg",)
THUMBNAIL_KEEP = 30
THUMBNAIL_SIZE = (1920, 1080)

# stream.thumbnail's status.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# stream.thumbnail's error, for the two runs that fail on missing material
# (a reader words them); any other failure carries the exception's text.
NO_ORG_THUMBNAIL = "no_org_thumbnail"
NO_BANNER = "no_banner"
# The run was stopped by the shutdown: cancelled while queued, or told to
# stop (TaskExecutor.stopping) before it wrote anything.
CANCELLED = "cancelled"

# What GenerateThumbnail answers.
STARTED = "started"
BUSY = "busy"


# ── pure helpers ─────────────────────────────────────────────────────────

def GameAbbreviation(game):
    """The abbreviation the title ends in, from a `game` state dict: its
    own, else GAME_ABBREVIATION_FALLBACK by start.gg id; None if neither."""
    game = game if isinstance(game, dict) else {}
    abbreviation = game.get("abbreviation")
    if abbreviation:
        return abbreviation
    try:
        return GAME_ABBREVIATION_FALLBACK.get(int(game.get("smashgg_id")))
    except (TypeError, ValueError):
        return None


def BuildTitle(tournamentName, abbreviation, topEntrants) -> str:
    """The auto-generated stream title.

    "<name> ft. <tags...> ... and more! - <abbreviation>", with entrants
    dropped from the tail until it fits STREAM_TITLE_MAX_LENGTH; just
    "<name> - <abbreviation>" when none fits, or while no entrant has a
    seed yet (the seeds are not out, or the entrants are not fetched).
    `topEntrants` is SetTopEntrants' form ({"seed": int, "gamerTag": str},
    best seed first).
    """
    name = tournamentName or ""
    suffix = f" - {abbreviation}" if abbreviation else ""
    entrants = topEntrants or ()
    if any(entrant["seed"] < NO_SEED for entrant in entrants):
        # Every tag takes at least two characters (itself and a space), so
        # no more than this many can ever fit: the rest would only be popped
        # again one by one.
        names = [entrant["gamerTag"] for entrant in entrants
                 if entrant["gamerTag"]][:STREAM_TITLE_MAX_LENGTH]
        while names:
            candidate = (f"{name} ft. " + " ".join(names)
                         + " ... and more!" + suffix)
            if len(candidate) <= STREAM_TITLE_MAX_LENGTH:
                return candidate
            names.pop()
    return f"{name}{suffix}".strip()


def TitleIsAuto(title, titleAuto) -> bool:
    """Whether `title` (``stream.title``) is the auto-generated one: what
    the generator wrote last (`titleAuto`, ``stream.titleAuto``) or nothing.
    Such a title gives way -- to the next auto title, and to what an
    operator types. `titleAuto` None (a state from before S4d, which never
    recorded it): the title counts as auto, as the panel regenerated it
    whenever it was opened."""
    title = title or ""
    if titleAuto is None:
        return True
    return not title or title == titleAuto


def TypedTitleWins(typed, shown, title, titleAuto) -> bool:
    """Whether a client writes the title the operator typed when they leave
    the field. The rule for every client of ``stream.title`` (the panel's
    field, the Web page's).

    `typed`: the field's text; `shown`: what the field last showed in step
    with the state (rendered, or written from it); `title` / `titleAuto`:
    ``stream.title`` / ``stream.titleAuto`` now.

    Not when the text is the state's already, nor when the operator did not
    change what was shown (the field merely kept an old value while they
    were in it). Otherwise yes when the title is still the one they started
    from, or an auto title (one that landed while they typed: the generator
    never beats a hand) -- but not over another writer's own title (the Web,
    another client) that landed meanwhile: that one wins, as a field's own
    write elsewhere does everywhere (ADR-005 S2d)."""
    typed = typed or ""
    title = title or ""
    if typed == title or typed == (shown or ""):
        return False
    return title == (shown or "") or TitleIsAuto(title, titleAuto)


def _Entrant(entrant):
    """One top entrant in SetTopEntrants' form, or None."""
    if not isinstance(entrant, dict):
        return None
    seed = entrant.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not seed:
        seed = NO_SEED
    tag = entrant.get("gamerTag")
    return {"seed": seed, "gamerTag": tag if isinstance(tag, str) else ""}


def _Channel(channel):
    """A saved channel (a dict) as {"name", "url"}."""
    name = channel.get("name")
    url = channel.get("url")
    return {"name": name if isinstance(name, str) else "",
            "url": url if isinstance(url, str) else ""}


def _SavedChannels():
    """The saved channels that are dicts (copies, any extra key kept): what
    the channel indexes count. Anything else in the list is skipped, and
    dropped by the next write."""
    channels = SettingsManager.Get(CHANNELS_SETTING, [])
    if not isinstance(channels, list):
        return []
    return [dict(channel) for channel in channels
            if isinstance(channel, dict)]


def _ChannelFields(name, url):
    """(name, url) stripped, or None when either is empty -- the panel's
    dialog refused that too."""
    name = name.strip() if isinstance(name, str) else ""
    url = url.strip() if isinstance(url, str) else ""
    if not name or not url:
        return None
    return name, url


def _ThumbnailOptions(raw):
    """`raw` (what settings.json holds) with every missing or invalid value
    replaced by its default."""
    raw = raw if isinstance(raw, dict) else {}
    options = dict(DEFAULT_THUMBNAIL_OPTIONS)
    if raw.get("source") in THUMBNAIL_SOURCES:
        options["source"] = raw["source"]
    if raw.get("style") in THUMBNAIL_STYLES:
        options["style"] = raw["style"]
    if isinstance(raw.get("overlayOrgIcon"), bool):
        options["overlayOrgIcon"] = raw["overlayOrgIcon"]
    return options


def _ValidOption(key, value) -> bool:
    if key == "source":
        return value in THUMBNAIL_SOURCES
    if key == "style":
        return value in THUMBNAIL_STYLES
    if key == "overlayOrgIcon":
        return isinstance(value, bool)
    return False


def _WriteFile(path, data):
    """Write `data` to `path` through a sibling temporary file, so a reader
    (an overlay fetching the asset) never gets a half-written one. The
    replace is retried while a reader holds the target (Windows); OSError
    when it never went through. The temporary file never outlives this."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.stem + ".", suffix=".tmp")
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
            logger.warning(f"Stream: could not remove {temporary}")


class _Stopped(Exception):
    """The executor is shutting down: the thumbnail job gives up."""


def _PngReady(image):
    """`image` in a mode PNG keeps as is: RGBA when it has transparency,
    RGB otherwise (what the panel's QPixmap saved)."""
    if image.mode in ("RGB", "RGBA"):
        return image
    transparent = (image.mode in ("LA", "PA")
                   or "transparency" in image.info)
    return image.convert("RGBA" if transparent else "RGB")


def CenterCrop(image):
    """`image` cropped to 16:9 around its centre, then resized to 1920x1080."""
    width, height = image.size
    targetRatio = THUMBNAIL_SIZE[0] / THUMBNAIL_SIZE[1]
    if width / height > targetRatio:
        cropped = int(height * targetRatio)
        left = (width - cropped) // 2
        image = image.crop((left, 0, left + cropped, height))
    else:
        cropped = int(width / targetRatio)
        top = (height - cropped) // 2
        image = image.crop((0, top, width, top + cropped))
    return image.resize(THUMBNAIL_SIZE, Image.LANCZOS)


def NetflixStyle(image):
    """The whole of `image` over a blurred, darkened centre crop of it."""
    background = CenterCrop(image.copy())
    background = background.filter(ImageFilter.GaussianBlur(radius=30))
    background = ImageEnhance.Brightness(background).enhance(0.6)
    width, height = image.size
    scale = min(THUMBNAIL_SIZE[0] / width, THUMBNAIL_SIZE[1] / height)
    fitted = (int(width * scale), int(height * scale))
    foreground = image.resize(fitted, Image.LANCZOS)
    background.paste(foreground, ((THUMBNAIL_SIZE[0] - fitted[0]) // 2,
                                  (THUMBNAIL_SIZE[1] - fitted[1]) // 2))
    return background


def OverlayOrgIcon(image):
    """The registered org icon composited into `image`'s bottom-right
    corner; `image` unchanged when none is registered."""
    if not os.path.exists(schema.STREAM_ORG_ICON_PATH):
        return image
    try:
        icon = Image.open(schema.STREAM_ORG_ICON_PATH).convert("RGBA")
    except Exception:
        logger.opt(exception=True).error(
            "Stream: the org icon could not be read")
        return image
    # 12% of the canvas height, inset by half that -- big enough to read at
    # YouTube's grid size without covering the artwork.
    targetHeight = int(image.height * 0.12)
    scale = targetHeight / icon.height
    icon = icon.resize(
        (max(1, int(icon.width * scale)), max(1, targetHeight)),
        Image.LANCZOS)
    margin = targetHeight // 2
    position = (image.width - icon.width - margin,
                image.height - icon.height - margin)
    # The icon's own alpha as the mask, so PNG transparency does not come
    # out as a black box.
    image.paste(icon, position, icon)
    return image


# ── the service ──────────────────────────────────────────────────────────

class StreamService:
    """The 配信設定 operations (see the module docstring). One per process:
    the top entrants and the running thumbnail are its own."""

    instance: "StreamService" = None

    def __init__(self, later, bannerUrl=None, prune=None):
        """`later(fn)` runs `fn` on the main thread once its event loop is
        back (never right away); `bannerUrl()` answers the loaded
        tournament's banner URL or None, blocking (it is called from the
        thumbnail job); `prune(directory, patterns, keep, protect=)` drops
        all but the newest generated thumbnails (None: none are dropped).
        """
        self._later = later
        self._bannerUrl = bannerUrl
        self._prune = prune
        self._started = False

        # The title's inputs and the queued refresh, under _titleLock, which
        # is never held while StateManager.lock is taken (the observer takes
        # it inside that lock). The last auto title is the state's
        # (stream.titleAuto).
        self._titleLock = threading.Lock()
        self._topEntrants = ()
        self._seenInputs = None
        self._refreshQueued = False

        # The org assets: one import or reset at a time, so a file and its
        # published version always belong together.
        self._assetLock = threading.Lock()

        # The thumbnail: True from a GenerateThumbnail that started a run
        # until that run's result is published.
        self._thumbnailLock = threading.Lock()
        self._thumbnailRunning = False

    # ── lifetime ─────────────────────────────────────────────────────────

    def Start(self):
        """Follow the state from now on (the title's inputs): the first
        publish after this refreshes the title. Idempotent."""
        with StateManager.lock:
            if self._started:
                return
            self._started = True
        StateManager.observers.state_updated.subscribe(self._OnStatePublished)
        StateManager.observers.state_big_change.subscribe(
            self._OnStatePublished)

    def Stop(self):
        """Stop following the state (a test's own service, or the session's
        while a test runs one of its own). Idempotent."""
        with StateManager.lock:
            if not self._started:
                return
            self._started = False
        StateManager.observers.state_updated.unsubscribe(
            self._OnStatePublished)
        StateManager.observers.state_big_change.unsubscribe(
            self._OnStatePublished)
        with self._titleLock:
            self._seenInputs = None

    # ── the title ────────────────────────────────────────────────────────

    def SetTopEntrants(self, entrants, cancelled=None):
        """The loaded tournament's entrants for the title, best seed first:
        dicts with "seed" (an int; missing / 0: none) and "gamerTag". An
        empty list: no tournament, or not fetched yet. Queues a title
        refresh when they changed.

        Called by the tournament provider, from its entrants job (the list)
        and from the GUI thread when the tournament changes (empty).
        `cancelled` (an Event-like, optional) is checked under the same lock
        the emptying takes: a fetch whose tournament was replaced meanwhile
        hands nothing over, even if it got past its own last check."""
        normalized = tuple(
            entrant for entrant in map(_Entrant, entrants or ())
            if entrant is not None)
        with self._titleLock:
            if cancelled is not None and cancelled.is_set():
                return
            if normalized == self._topEntrants:
                return
            self._topEntrants = normalized
        self._QueueRefresh()

    def TopEntrants(self) -> list:
        with self._titleLock:
            return [dict(entrant) for entrant in self._topEntrants]

    def GeneratedTitle(self) -> str:
        """The title the state's tournament name and game and the top
        entrants make now."""
        name, abbreviation = self._TitleInputs()
        with self._titleLock:
            entrants = self._topEntrants
        return BuildTitle(name, abbreviation, entrants)

    def RefreshTitle(self) -> bool:
        """Publish the generated title as ``stream.titleAuto``, and write it
        to ``stream.title`` unless the operator's own is there (one that is
        neither the last auto title nor nothing: TitleIsAuto) -- one save
        cycle. True if ``stream.title`` changed. Not from inside a state
        observer (see the module docstring)."""
        with StateManager.lock:
            title = self.GeneratedTitle()
            overwrite = TitleIsAuto(StateManager.Get(TITLE_PATH, ""),
                                    StateManager.Get(TITLE_AUTO_PATH))
            with StateManager.SaveBlock():
                StateManager.SetIfChanged(TITLE_AUTO_PATH, title)
                if not overwrite:
                    return False
                return AutonaActions.instance.SetStreamTitle(title)

    def _TitleInputs(self):
        name = StateManager.Get(TOURNAMENT_NAME_PATH, "") or ""
        return name, GameAbbreviation(StateManager.Get(schema.GAME_ROOT))

    def _OnStatePublished(self, *_payload):
        # A state observer: inside StateManager.lock when a publish delivers
        # it. Reads two keys and at most queues a refresh -- never writes.
        inputs = self._TitleInputs()
        with self._titleLock:
            if inputs == self._seenInputs:
                return
            self._seenInputs = inputs
        self._QueueRefresh()

    def _QueueRefresh(self):
        with self._titleLock:
            if self._refreshQueued:
                return
            self._refreshQueued = True
        try:
            self._later(self._RunQueuedRefresh)
        except Exception:
            # No deferral to be had (no event loop yet): this change is
            # dropped; the next one queues again.
            logger.opt(exception=True).warning(
                "Stream: the title refresh could not be queued")
            with self._titleLock:
                self._refreshQueued = False

    def _RunQueuedRefresh(self):
        with self._titleLock:
            self._refreshQueued = False
        try:
            self.RefreshTitle()
        except Exception:
            logger.opt(exception=True).error(
                "Stream: refreshing the stream title failed")

    # ── the channels ─────────────────────────────────────────────────────

    def Channels(self) -> list:
        """The saved stream channels, each {"name", "url"} (copies). An entry
        that is not a dict is skipped (and not counted by the indexes)."""
        return [_Channel(channel) for channel in _SavedChannels()]

    def AddChannel(self, name, url):
        """Save a channel at the end; its index, or None when the name or
        the URL is empty (nothing saved)."""
        fields = _ChannelFields(name, url)
        if fields is None:
            return None
        with SettingsManager.lock:
            channels = _SavedChannels()
            channels.append({"name": fields[0], "url": fields[1]})
            SettingsManager.Set(CHANNELS_SETTING, channels)
            return len(channels) - 1

    def UpdateChannel(self, index, name, url) -> bool:
        """Change channel `index`'s name and URL (any other key it has is
        kept). False when there is no such channel or the name or the URL is
        empty (nothing saved). A destination that was this channel's URL
        follows it (``stream.url``)."""
        fields = _ChannelFields(name, url)
        if fields is None:
            return False
        with SettingsManager.lock:
            channels = _SavedChannels()
            if not isinstance(index, int) or not 0 <= index < len(channels):
                return False
            previous = _Channel(channels[index])["url"]
            channels[index].update(name=fields[0], url=fields[1])
            SettingsManager.Set(CHANNELS_SETTING, channels)
        # Outside the settings lock: the state's lock is taken before it
        # elsewhere (a core operation reading a setting).
        with StateManager.lock:
            if previous and StateManager.Get(URL_PATH, "") == previous:
                AutonaActions.instance.SetStreamUrl(fields[1])
        return True

    def DeleteChannel(self, index) -> bool:
        """Remove channel `index`; False when there is no such channel. A
        destination that was its URL is cleared (``stream.url``)."""
        with SettingsManager.lock:
            channels = _SavedChannels()
            if not isinstance(index, int) or not 0 <= index < len(channels):
                return False
            removed = _Channel(channels.pop(index))["url"]
            SettingsManager.Set(CHANNELS_SETTING, channels)
        with StateManager.lock:
            if removed and StateManager.Get(URL_PATH, "") == removed:
                AutonaActions.instance.SetStreamUrl("")
        return True

    # ── the stream org's assets ──────────────────────────────────────────

    def ImportOrgAsset(self, kind, data) -> str:
        """Register the stream org's "icon" or "thumbnail" from an image's
        bytes (PNG, JPEG, MPO -- its first frame -- or BMP, up to
        ORG_ASSET_MAX_PIXELS): scaled down to fit its longest side
        (ORG_ASSETS), saved as PNG at its path and its version published;
        IMPORTED. INVALID_IMAGE when `kind` is unknown or `data` is not such
        an image, SAVE_FAILED when the file could not be written (nothing
        changes in either case)."""
        spec = ORG_ASSETS.get(kind)
        if spec is None or not isinstance(data, (bytes, bytearray)) \
                or not data:
            logger.warning(f"Stream: refusing an org asset ({kind!r})")
            return INVALID_IMAGE
        path, versionKey, longestSide = spec
        try:
            encoded = self._EncodeOrgAsset(kind, data, longestSide)
        except Exception:
            logger.opt(exception=True).warning(
                f"Stream: the org {kind} is not a readable image")
            return INVALID_IMAGE
        if encoded is None:
            return INVALID_IMAGE
        with self._assetLock:
            try:
                _WriteFile(path, encoded)
            except OSError:
                logger.opt(exception=True).error(
                    f"Stream: the org {kind} could not be saved")
                return SAVE_FAILED
            schema.PublishAssetVersion(versionKey, path)
        return IMPORTED

    @staticmethod
    def _EncodeOrgAsset(kind, data, longestSide):
        """`data` scaled down and encoded as PNG; None when it is not an
        image ImportOrgAsset takes. Raises what a broken image raises."""
        image = Image.open(BytesIO(data))
        if image.format not in ORG_ASSET_FORMATS:
            logger.warning(f"Stream: refusing an org {kind} in {image.format}")
            return None
        # The header's size: checked before anything is decoded.
        if image.width * image.height > ORG_ASSET_MAX_PIXELS:
            logger.warning(
                f"Stream: refusing an org {kind} of {image.width}x"
                f"{image.height} (over {ORG_ASSET_MAX_PIXELS} pixels)")
            return None
        # Scaled down first, converted after, so the full-size image is
        # decoded once and never copied (thumbnail() decodes a JPEG / MPO
        # at a reduced scale already). Except an image in a mode resize()
        # scales poorly or not at all (a palette or 1-bit one: nearest
        # neighbour only; 16-bit grey): converted first.
        if image.mode not in ("L", "LA", "RGB", "RGBA", "CMYK"):
            image = _PngReady(image)
        if image.width > longestSide or image.height > longestSide:
            image.thumbnail((longestSide, longestSide), Image.LANCZOS)
        image = _PngReady(image)
        encoded = BytesIO()
        image.save(encoded, "PNG")
        return encoded.getvalue()

    def ResetOrgAssets(self):
        """Unregister both org assets (their files removed, their versions
        0). A thumbnail that was to be made from the org thumbnail falls
        back to the tournament banner, so it does not fail on nothing."""
        with self._assetLock:
            for path, versionKey, _side in ORG_ASSETS.values():
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    logger.opt(exception=True).error(
                        f"Stream: {path} could not be removed")
                schema.PublishAssetVersion(versionKey, path)
        if self.ThumbnailOptions()["source"] == "org":
            self.SetThumbnailOptions({"source": "banner"})

    # ── the stream thumbnail ─────────────────────────────────────────────

    def ThumbnailOptions(self) -> dict:
        """The generator's options: {"source": banner | org, "style":
        center | netflix, "overlayOrgIcon": bool}, defaults filled in."""
        return _ThumbnailOptions(
            SettingsManager.Get(THUMBNAIL_OPTIONS_SETTING))

    def SetThumbnailOptions(self, changes):
        """Change some of the options and save them; all of them as saved,
        or None when a key or a value is not one of the options' (nothing
        saved). settings.json is written only when they changed."""
        if not isinstance(changes, dict) or not all(
                _ValidOption(key, value) for key, value in changes.items()):
            logger.warning(f"Stream: refusing the thumbnail options {changes}")
            return None
        with SettingsManager.lock:
            options = self.ThumbnailOptions()
            options.update(changes)
            if SettingsManager.Get(THUMBNAIL_OPTIONS_SETTING) != options:
                SettingsManager.Set(THUMBNAIL_OPTIONS_SETTING, dict(options))
        return options

    def ThumbnailRunning(self) -> bool:
        with self._thumbnailLock:
            return self._thumbnailRunning

    def GenerateThumbnail(self) -> str:
        """Start making a stream thumbnail with the saved options, as a job
        of the task executor; STARTED. BUSY (nothing started) while one is
        being made. ``stream.thumbnail`` says "running" before this returns
        and "done" (its path) or "error" when the job ends."""
        with self._thumbnailLock:
            if self._thumbnailRunning:
                return BUSY
            self._thumbnailRunning = True
        try:
            with StateManager.lock:
                last = self._Thumbnail()
                revision = last["revision"] + 1
                self._PublishThumbnail(RUNNING, last["path"], "", revision)
            options = self.ThumbnailOptions()
            executor = TaskExecutor.instance
            future = executor.submit(
                self._MakeThumbnail, options, executor.stopping,
                task_name="stream thumbnail",
                then=lambda result: self._ThumbnailEnded(result, revision),
                on_error=lambda error: self._ThumbnailEnded(
                    (ERROR, str(error) or type(error).__name__), revision))
        except BaseException:
            self._ThumbnailEnded((ERROR, "not started"), None)
            raise
        # A job the shutdown cancels (queued then, or submitted after it)
        # runs neither `then` nor `on_error`, only its done callbacks -- right
        # away when it is cancelled already.
        future.add_done_callback(
            lambda done: done.cancelled() and self._ThumbnailEnded(
                (ERROR, CANCELLED), revision))
        return STARTED

    def _Thumbnail(self) -> dict:
        thumbnail = StateManager.Get(THUMBNAIL_PATH)
        default = schema.DefaultStreamThumbnail()
        if not isinstance(thumbnail, dict):
            return default
        path = thumbnail.get("path")
        revision = thumbnail.get("revision")
        return {
            "status": thumbnail.get("status", default["status"]),
            "path": path if isinstance(path, str) else "",
            "error": thumbnail.get("error", ""),
            "revision": revision if isinstance(revision, int)
            and not isinstance(revision, bool) else 0,
        }

    def _PublishThumbnail(self, status, path, error, revision):
        # One Set of the whole dict: a reader never sees half a result.
        StateManager.Set(THUMBNAIL_PATH, {
            "status": status, "path": path, "error": error,
            "revision": revision})

    def _ThumbnailEnded(self, result, revision):
        """Publish a run's `result` -- (DONE, path) or (ERROR, error) --
        then let the next run start. On the job's thread (its then /
        on_error), or the caller's when the run never started."""
        try:
            status, value = result
            with StateManager.lock:
                last = self._Thumbnail()
                if revision is None:
                    revision = last["revision"]
                if status == DONE:
                    self._PublishThumbnail(DONE, value, "", revision)
                else:
                    # The path stays the last thumbnail that was made.
                    self._PublishThumbnail(ERROR, last["path"], value,
                                           revision)
        finally:
            # After the publish: a run started in between would otherwise
            # see its "running" overwritten by this result.
            with self._thumbnailLock:
                self._thumbnailRunning = False

    def _MakeThumbnail(self, options, stopping):
        """The job: (DONE, the thumbnail's path) or (ERROR, why). Gives up
        (CANCELLED) once `stopping` (the executor's) is set: before it
        starts, around the banner download, before it writes anything."""
        if stopping.is_set():
            return ERROR, CANCELLED
        if options["source"] == "org":
            source = self._OrgThumbnailImage()
            if source is None:
                return ERROR, NO_ORG_THUMBNAIL
        else:
            try:
                source = self._BannerImage(stopping)
            except _Stopped:
                return ERROR, CANCELLED
            if source is None:
                return ERROR, NO_BANNER

        if options["style"] == "netflix":
            image = NetflixStyle(source)
        else:
            image = CenterCrop(source)
        if options["overlayOrgIcon"]:
            image = OverlayOrgIcon(image)
        if stopping.is_set():
            return ERROR, CANCELLED

        directory = Path(STREAM_OUTPUT_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = (f"thumbnail_{options['source']}_{options['style']}_"
                f"{timestamp}.jpg")
        target = directory / name
        image.save(target, "JPEG", quality=90)
        if self._prune is not None:
            self._prune(directory, THUMBNAIL_PATTERNS, THUMBNAIL_KEEP,
                        protect=target)
        # Relative to the app directory, "/" separated: "out/stream/...",
        # which is also its URL path on the web server.
        return DONE, target.as_posix()

    def _OrgThumbnailImage(self):
        if not os.path.exists(schema.STREAM_ORG_THUMB_PATH):
            return None
        try:
            return Image.open(schema.STREAM_ORG_THUMB_PATH).convert("RGB")
        except Exception:
            logger.opt(exception=True).error(
                "Stream: the org thumbnail could not be read")
            return None

    def _BannerImage(self, stopping):
        """The thumbnail's banner: the loaded tournament's, downloaded,
        else the banner saved on the EVENT tab, else the bundled default;
        None when none can be read. _Stopped when `stopping` is set before
        or after the lookup and the download (each up to 20 s)."""
        url = None
        if self._bannerUrl is not None:
            if stopping.is_set():
                raise _Stopped()
            try:
                url = self._bannerUrl()
            except Exception:
                logger.opt(exception=True).error(
                    "Stream: looking up the tournament banner failed")
        if url:
            if stopping.is_set():
                raise _Stopped()
            try:
                response = requests.get(url, headers=BANNER_HEADERS,
                                        timeout=BANNER_TIMEOUT_SECS)
                response.raise_for_status()
                downloaded = Image.open(
                    BytesIO(response.content)).convert("RGB")
            except Exception:
                downloaded = None
                logger.opt(exception=True).warning(
                    "Stream: the tournament banner could not be downloaded; "
                    "falling back to the saved one")
            if stopping.is_set():
                raise _Stopped()
            if downloaded is not None:
                return downloaded
        for candidate in (BANNER_PATH, DEFAULT_BANNER_PATH):
            if os.path.exists(candidate):
                try:
                    return Image.open(candidate).convert("RGB")
                except Exception:
                    logger.opt(exception=True).error(
                        f"Stream: {candidate} could not be read")
        return None
