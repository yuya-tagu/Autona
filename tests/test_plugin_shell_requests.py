"""The core side of the plugins' shell requests (ADR-009 D4; lane L10):
ShellPicks (what pickFile / pickFolder for a plugin hold, and the
``resolvePick`` the plugin routes take) and PluginLinks (the URL
openPluginLink opens). Qt-free. The shell's dialogs and the shared view's
parsing are tests/test_web_panel_host.py's."""

from types import SimpleNamespace

import pytest

from src.core import PluginLinks
from src.core.PluginSettings import CheckFieldValue
from src.core.PluginTypes import Field, FieldType, PluginState
from src.core.ShellPicks import (FILE_EXTENSIONS_MAX, MAX_PICKS,
                                 PICK_EXPIRY_SECS, ParseExtensions, PickName,
                                 ShellPicks)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _picks(clock=None):
    count = iter(range(1, 10000))
    return ShellPicks(clock=clock or Clock(),
                      newId=lambda: f"pick-{next(count)}")


# ── ShellPicks ──────────────────────────────────────────────────────────

def test_a_pick_resolves_to_its_path_for_its_kind_only(tmp_path):
    picks = _picks()
    file = picks.Hold("file", str(tmp_path / "a" / ".." / "logo.png"))
    folder = picks.Hold("folder", str(tmp_path))
    assert picks.Resolve("file", file) == str(tmp_path / "logo.png")
    assert picks.Resolve("folder", folder) == str(tmp_path)
    # FieldType values resolve as their names do (PluginSettings passes
    # str(kind)).
    assert picks.Resolve(FieldType.FILE, file) == str(tmp_path / "logo.png")
    assert picks.Resolve("folder", file) is None
    assert picks.Resolve("file", folder) is None
    for unknown in ("pick-99", "", None, 12):
        assert picks.Resolve("file", unknown) is None
    # As often as it is asked for.
    assert picks.Resolve("file", file) == str(tmp_path / "logo.png")


def test_ids_are_unguessable_by_default(tmp_path):
    picks = ShellPicks()
    ids = {picks.Hold("folder", str(tmp_path)) for _ in range(5)}
    assert len(ids) == 5 and all(len(pickId) >= 20 for pickId in ids)


@pytest.mark.parametrize("kind, path", [
    ("folder", r"\\server\share"), ("folder", "//server/share"),
    ("file", r"\\?\C:\x.png"), ("folder", "relative"), ("folder", ""),
    ("folder", "C:\\a\x00b"), ("secret", "C:\\x"), ("text", "C:\\x"),
])
def test_only_an_absolute_local_path_of_a_known_kind_is_held(kind, path):
    picks = _picks()
    with pytest.raises(ValueError):
        picks.Hold(kind, path)
    assert picks.Count() == 0


def test_a_pick_expires(tmp_path):
    clock = Clock()
    picks = _picks(clock)
    first = picks.Hold("folder", str(tmp_path))
    clock.now += PICK_EXPIRY_SECS - 1
    second = picks.Hold("folder", str(tmp_path))
    assert picks.Resolve("folder", first) == str(tmp_path)
    clock.now += 1
    assert picks.Resolve("folder", first) is None
    assert picks.Resolve("folder", second) == str(tmp_path)
    assert picks.Count() == 1
    clock.now += PICK_EXPIRY_SECS
    assert picks.Count() == 0


def test_the_oldest_pick_goes_past_the_cap(tmp_path):
    picks = _picks()
    ids = [picks.Hold("folder", str(tmp_path)) for _ in range(MAX_PICKS + 2)]
    assert picks.Count() == MAX_PICKS
    assert [picks.Resolve("folder", pickId) for pickId in ids[:2]] == [
        None, None]
    assert picks.Resolve("folder", ids[-1]) == str(tmp_path)


def test_resolve_is_what_the_settings_check_takes(tmp_path):
    # PluginSettings.CheckFieldValue(resolvePick=picks.Resolve): the id
    # becomes the path, checked as a file of the field's extensions.
    image = tmp_path / "logo.png"
    image.write_bytes(b"x")
    picks = _picks()
    pickId = picks.Hold("file", str(image))
    field = Field(key="logo", type=FieldType.FILE, label="Logo",
                  extensions=("png",))
    assert CheckFieldValue(field, pickId, resolvePick=picks.Resolve) == (
        True, str(image))
    assert CheckFieldValue(field, "pick-x", resolvePick=picks.Resolve) == (
        False, "unknown pick id")
    folderField = Field(key="dir", type=FieldType.FOLDER, label="Dir")
    assert CheckFieldValue(folderField, pickId,
                           resolvePick=picks.Resolve)[0] is False


@pytest.mark.parametrize("path, name", [
    ("C:\\Users\\me\\private\\logo.png", "logo.png"),
    ("C:\\Users\\me\\private\\Folder\\", "Folder"),
    ("C:\\", "C:"),
    ("D:/games/mine", "mine"),
])
def test_a_pick_shows_its_name_only(path, name):
    assert PickName(path) == name


def test_the_extensions_argument():
    assert ParseExtensions("png") == ("png",)
    assert ParseExtensions("png,jpg,png,mp4") == ("png", "jpg", "mp4")
    assert ParseExtensions(",".join(["e"] * FILE_EXTENSIONS_MAX)) == ("e",)
    for bad in ("", "PNG", ".png", "*.png", "*", "png,", ",png", "png,,jpg",
                "png jpg", "png;jpg", "a" * 11, "pég", "png\n", None, 3,
                ",".join(["e"] * (FILE_EXTENSIONS_MAX + 1))):
        assert ParseExtensions(bad) is None, bad


# ── PluginLinks ─────────────────────────────────────────────────────────

HOMEPAGE = "https://example.com/webhook?ref=autona#top"


def _registry(state=PluginState.READY, homepage=HOMEPAGE, funding=None,
              manifest=True):
    plugin = SimpleNamespace(state=state, report=SimpleNamespace(
        manifest=SimpleNamespace(homepage=homepage, funding=funding)
        if manifest else None))
    return SimpleNamespace(Get=lambda id_: plugin if id_ == "a.b" else None)


@pytest.mark.parametrize("url", [
    "https://example.com", "https://example.com/", HOMEPAGE,
    "https://sub.example-site.co.jp:8443/a/b", "https://github.com/a/b",
    "https://example.com/" + "x" * 2000,
])
def test_a_safe_https_url(url):
    assert PluginLinks.SafeHttpsUrl(url)


@pytest.mark.parametrize("url", [
    None, "", "http://example.com", "HTTPS://example.com/",
    "javascript:alert(1)", "file:///C:/x", "https:example.com",
    "https://user@example.com", "https://user:pw@example.com/",
    "https://*/", "https://exa mple.com", "https://example.com/a b",
    "https://example.com\\@evil.example", "https://example.com/\x07",
    "https://-bad.example/", "https://例え.jp/", "https://example.com:99999/",
    "https://example.com:port/", "https://example.com/" + "x" * 2048,
    "https:///path",
])
def test_anything_else_is_not(url):
    assert not PluginLinks.SafeHttpsUrl(url)


def test_an_installed_plugins_homepage_and_funding():
    registry = _registry(funding="https://example.com/sponsor")
    assert PluginLinks.PluginLinkUrl(
        "installed", "a.b", "homepage", registry=registry) == (HOMEPAGE, None)
    assert PluginLinks.PluginLinkUrl(
        "installed", "a.b", "funding", registry=registry) == (
        "https://example.com/sponsor", None)


def test_a_listing_rows_repository():
    assert PluginLinks.PluginLinkUrl(
        "listing", "row-1", "repo",
        listingRepo={"row-1": "author/plugin.repo"}.get) == (
        "https://github.com/author/plugin.repo", None)


@pytest.mark.parametrize("source, id_, link, registry, listing, code", [
    ("installed", "a.b", "homepage", None, None, PluginLinks.NOT_READY),
    ("listing", "a.b", "repo", None, None, PluginLinks.NOT_READY),
    ("installed", "x.y", "homepage", _registry(), None,
     PluginLinks.UNKNOWN_PLUGIN),
    ("listing", "x", "repo", None, lambda id_: None,
     PluginLinks.UNKNOWN_PLUGIN),
    ("installed", "a.b", "homepage", _registry(state=PluginState.BLOCKED),
     None, PluginLinks.BLOCKED),
    ("installed", "a.b", "funding", _registry(), None, PluginLinks.NO_LINK),
    ("installed", "a.b", "homepage", _registry(manifest=False), None,
     PluginLinks.NO_LINK),
    ("installed", "a.b", "repo", _registry(), None, PluginLinks.NO_LINK),
    ("listing", "a.b", "homepage", None, lambda id_: "a/b",
     PluginLinks.NO_LINK),
    ("elsewhere", "a.b", "homepage", _registry(), None, PluginLinks.NO_LINK),
    ("installed", "a.b", "homepage",
     _registry(homepage="http://example.com"), None, PluginLinks.BAD_LINK),
    ("listing", "a.b", "repo", None, lambda id_: "author/..",
     PluginLinks.BAD_LINK),
    ("listing", "a.b", "repo", None, lambda id_: "author/.",
     PluginLinks.BAD_LINK),
    ("listing", "a.b", "repo", None, lambda id_: "a/b/c",
     PluginLinks.BAD_LINK),
    ("listing", "a.b", "repo", None, lambda id_: "evil.example?x=",
     PluginLinks.BAD_LINK),
])
def test_a_link_refused(source, id_, link, registry, listing, code):
    assert PluginLinks.PluginLinkUrl(
        source, id_, link, registry=registry, listingRepo=listing) == (
        None, code)

