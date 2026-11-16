"""The URL of a Hivee event's stream overlay, as the tournament URL field
takes it (the Hivee provider, src/TournamentDataProvider/HiveeDataProvider
.py, reads the event through it).

Hivee (the sibling event app) publishes one read-only JSON per event for
OBS: ``GET {origin}/api/events/{eventId}/overlay?token=...`` -- no login, a
capability token instead (64 hex characters, rotated from Hivee's stream
page; a wrong one answers 404). Two forms are taken, both carrying the
token: the OBS browser source URL Hivee's stream page shows
(``{origin}/overlay/{eventId}/scoreboard?token=...`` or ``/top8``, with an
optional ``&program=`` for the Top 8) and the API URL itself. Both are
normalised to the API URL (``Normalize``), which is what is saved and
loaded. Plain http is taken for this PC only (localhost, 127.0.0.1, ::1,
``*.localhost``: a Hivee run locally); anywhere else the token would cross
the network in the clear, so the URL must be https.

**The token is a secret**: whoever has it reads the event's overlay. It is
never put in a log line or an exception message -- ``Mask`` blanks it in
any text that may carry a URL -- nor in the state, the READ APIs or out/:
those carry the masked URL, and only settings.json and the provider hold the
real one. A masked URL sent back (the Event page sends what it shows) is
taken for the saved one it masks (``Unmask``).

Pure functions; import side effects: none (agent-pitfalls #1).
"""
import re
from collections import namedtuple
from urllib.parse import parse_qs, urlencode, urlsplit

# What a Hivee overlay URL names: the site's origin ("https://host[:port]"),
# the event, the token and the Top 8's program ("" when not given).
HiveeTarget = namedtuple("HiveeTarget", "origin eventId token program")

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_UUID_ONLY = re.compile(rf"^{_UUID}$")
# The OBS page (/overlay/{event}/scoreboard|top8) and the API
# (/api/events/{event}/overlay); a trailing slash is tolerated.
_PATHS = (
    re.compile(rf"^/overlay/(?P<event>{_UUID})/(?:scoreboard|top8)/?$"),
    re.compile(rf"^/api/events/(?P<event>{_UUID})/overlay/?$"),
)
# Hivee's tokens are hex; anything else URL-safe is taken as it is, nothing
# that would need quoting.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
# The hosts plain http is taken for: this PC.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

# `token=<value>` in any text: a URL, a query, the repr of a request body,
# percent-encoded or not. The value runs to the next separator.
_TOKEN_IN_TEXT = re.compile(
    r"(token(?:=|%3[dD]))(?:(?!%26)[^&#\s'\"<>])+", re.IGNORECASE)
MASK = "***"


def Parse(url):
    """The HiveeTarget a Hivee overlay URL names, or None when `url` is not
    one (another site's URL, no token, not an event's)."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.scheme == "http" and not _IsLocal(parts.hostname):
        return None
    event = None
    for pattern in _PATHS:
        match = pattern.match(parts.path)
        if match:
            event = match.group("event").lower()
            break
    if event is None:
        return None
    query = parse_qs(parts.query)
    token = (query.get("token") or [""])[0]
    if not token or not _TOKEN.match(token):
        return None
    program = (query.get("program") or [""])[0]
    if not _UUID_ONLY.match(program):
        program = ""
    origin = f"{parts.scheme}://{parts.netloc}"
    return HiveeTarget(origin, event, token, program.lower())


def _IsLocal(host) -> bool:
    """Whether `host` (urlsplit's hostname: lower case, no brackets) is
    this PC's."""
    if not host:
        return False
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def IsHiveeUrl(url) -> bool:
    return Parse(url) is not None


def IsMatchId(text) -> bool:
    """Whether `text` is a Hivee match id (a uuid): the set id a Hivee
    tournament's sets go by, where start.gg's are numbers."""
    return isinstance(text, str) and bool(_UUID_ONLY.match(text.strip()))


def ApiUrl(target, view=None) -> str:
    """The overlay API's URL for `target` (token included: never log it,
    Mask it), with `view` ("scoreboard" / "top8") when given."""
    params = {"token": target.token}
    if view:
        params["view"] = view
    if target.program:
        params["program"] = target.program
    return (f"{target.origin}/api/events/{target.eventId}/overlay?"
            f"{urlencode(params)}")


def Normalize(url):
    """A Hivee overlay URL as the API URL with only what is read from it
    (the token and the program), or None when `url` is not one."""
    target = Parse(url)
    return None if target is None else ApiUrl(target)


def Mask(text) -> str:
    """`text` with every token value replaced by ``***`` -- what a log line
    or an error message may show of a URL. Anything that is not a string is
    shown as its str()."""
    if not isinstance(text, str):
        text = str(text)
    return _TOKEN_IN_TEXT.sub(lambda m: m.group(1) + MASK, text)


def Unmask(text, real):
    """`real` (the saved URL) when `text` is its masked form -- what the
    state and the READ APIs showed of it, sent back as it was -- else `text`
    as it is. A URL that masks to nothing else (no token in it) is never
    taken for another."""
    if not (isinstance(text, str) and isinstance(real, str) and real):
        return text
    masked = Mask(real)
    if masked != real and text.strip() == masked:
        return real
    return text
