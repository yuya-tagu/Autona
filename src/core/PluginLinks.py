"""The URL the shell request openPluginLink opens (ADR-009 D4; lane L10).

The page names a link -- ``{source: installed | listing, id, link:
homepage | funding | repo}`` -- never a URL: the host builds it here and the
shell opens it in the default browser (AutonaShellOperations). That keeps
the shell requests' rule that a page passes no URL (openExternal names a key
of a fixed table).

- ``installed``: the manifest's ``homepage`` / ``funding`` of a plugin the
  registry knows (PluginRegistry.Get). A blocked plugin opens none.
- ``listing``: ``https://github.com/<repo>`` from the local copy of the
  listing's row (``repo``, ``owner/name``). The listing arrives in P5b: its
  lookup is injected (``listingRepo(id) -> "owner/name" | None``) and
  without one nothing opens.

Any other pair (an installed plugin's ``repo``, a listing row's
``homepage``) is NO_LINK. Whatever is built is checked again before it is
answered (SafeHttpsUrl): https only, no userinfo, a host name as the
manifest's network hosts are checked, no blank or control character, no
backslash, URL_MAX_LENGTH at most -- the manifest check's own rules, so a
link the registry took is one this opens, and nothing else is.

Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

from typing import Any, Callable, Optional, Tuple
from urllib.parse import urlsplit

from .PluginRegistry import _GITHUB_REPO, _HOST_NAME, _HTTPS_URL
from .PluginTypes import URL_MAX_LENGTH, PluginState

SOURCES = ("installed", "listing")
LINKS = ("homepage", "funding", "repo")
# What the shell request's ``id`` may be: a plugin id (PLUGIN_ID_PATTERN)
# or a listing row's id (its shape is the listing's, P5b), within one
# pattern that carries nothing but a name. Which one exists is the lookup's.
LINK_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
# What each source offers.
_OFFERED = {"installed": ("homepage", "funding"), "listing": ("repo",)}

GITHUB_URL = "https://github.com/"

# The refusals' codes (the shell answers them as its error).
NOT_READY = "not_ready"
UNKNOWN_PLUGIN = "unknown_plugin"
BLOCKED = "blocked"
NO_LINK = "no_link"
BAD_LINK = "bad_link"

ListingRepo = Callable[[str], Optional[str]]


def SafeHttpsUrl(url: Any) -> bool:
    """Whether ``url`` is one openPluginLink may open (module docstring)."""
    if not isinstance(url, str) or not url or len(url) > URL_MAX_LENGTH:
        return False
    if "\\" in url or any(ord(c) < 0x20 or ord(c) == 0x7f for c in url):
        return False
    if not _HTTPS_URL.fullmatch(url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 -- raises ValueError for a bad port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None \
            or parts.password is not None:
        return False
    host = parts.hostname or ""
    return host != "*" and bool(_HOST_NAME.fullmatch(host))


def PluginLinkUrl(source: str, pluginId: str, link: str, *, registry=None,
                  listingRepo: Optional[ListingRepo] = None
                  ) -> Tuple[Optional[str], Optional[str]]:
    """``(url, None)`` to open, or ``(None, code)``: NOT_READY (no registry
    / no listing yet), UNKNOWN_PLUGIN, BLOCKED, NO_LINK (not offered, not
    set), BAD_LINK (what was found is not a SafeHttpsUrl). ``registry``:
    what has PluginRegistry's ``Get(id)``."""
    if link not in _OFFERED.get(source, ()):
        return None, NO_LINK
    if source == "installed":
        if registry is None:
            return None, NOT_READY
        plugin = registry.Get(pluginId)
        if plugin is None:
            return None, UNKNOWN_PLUGIN
        if plugin.state == PluginState.BLOCKED:
            return None, BLOCKED
        manifest = plugin.report.manifest
        url = getattr(manifest, link, None) if manifest is not None else None
        if not url:
            return None, NO_LINK
    else:
        if listingRepo is None:
            return None, NOT_READY
        repo = listingRepo(pluginId)
        if repo is None or repo == "":
            return None, UNKNOWN_PLUGIN
        if not (isinstance(repo, str) and _GITHUB_REPO.fullmatch(repo)) \
                or repo.split("/", 1)[1] in (".", ".."):
            return None, BAD_LINK
        url = GITHUB_URL + repo
    if not SafeHttpsUrl(url):
        return None, BAD_LINK
    return url, None
