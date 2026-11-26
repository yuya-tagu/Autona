"""PluginSettings (ADR-009 B.2 / B.3, lane L3): config.json's values held
to their fields' types, file / folder by pick id, and Missing over config
and secrets together."""

import json
import os
import sys

import pytest

from src.core.PluginSettings import (CheckFieldValue, IsAbsoluteLocal,
                                     PluginSettings, UserDir)
from src.core.PluginTypes import (Field, Job, Manifest, Option, Permissions,
                                  Refused, StartRefusal)

PLUGIN = "example.settings"


def _manifest(*settings):
    return Manifest(
        manifest=1, id=PLUGIN, version="1.0.0", name="Example",
        author="example", license="MIT", min_autona="2.6.0", protocol=(1,),
        platforms=("windows-x64",), entry=("bin/x.exe",),
        permissions=Permissions(network=(), files="inputs", spawn=False,
                                autona_api="none"),
        jobs=(Job(id="run", title="Run", lane="light", timeout_seconds=10,
                  memory_mb=128),),
        settings=tuple(settings))


TEXT = Field(key="name", type="text", label="Name")
SHORT = Field(key="short", type="text", label="Short", max_length=5)
AREA = Field(key="body", type="textarea", label="Body")
NUMBER = Field(key="count", type="number", label="Count", min=1, max=10,
               step=1)
HALF = Field(key="ratio", type="number", label="Ratio", min=0, max=1,
             step=0.25)
FREE = Field(key="free", type="number", label="Free")
BOOL = Field(key="flag", type="bool", label="Flag")
SELECT = Field(key="mention", type="select", label="Mention",
               default="none", options=(Option("none", "None"),
                                        Option("here", "@here")))
FILE = Field(key="logo", type="file", label="Logo",
             extensions=("png", "jpg"))
FOLDER = Field(key="where", type="folder", label="Where")
SECRET = Field(key="webhook_url", type="secret", label="URL", required=True)


class _Secrets:
    def __init__(self, keys=()):
        self.keys = frozenset(keys)
        self.asked = []

    def Keys(self, pluginId, *, dev):
        self.asked.append((pluginId, dev))
        return self.keys


@pytest.fixture
def picks(tmp_path):
    table = {}

    def resolve(kind, pickId):
        return table.get((kind, pickId))
    resolve.table = table
    return resolve


@pytest.fixture
def settings(tmp_path, picks):
    return PluginSettings(str(tmp_path / "user_data"), _Secrets(), picks)


ALL = _manifest(TEXT, SHORT, AREA, NUMBER, HALF, FREE, BOOL, SELECT, FILE,
                FOLDER, SECRET)


def test_get_answers_every_non_secret_key_with_its_default(settings):
    assert settings.Get(PLUGIN, ALL, dev=False) == {
        "name": None, "short": None, "body": None, "count": None,
        "ratio": None, "free": None, "flag": None, "mention": "none",
        "logo": None, "where": None}


def test_config_lives_per_plugin_and_apart_for_dev(settings, tmp_path):
    root = tmp_path / "user_data" / "plugins"
    assert settings.ConfigPath(PLUGIN, dev=False) \
        == str(root / PLUGIN / "config.json")
    assert settings.ConfigPath(PLUGIN, dev=True) \
        == str(root / "_dev" / PLUGIN / "config.json")
    settings.Save(PLUGIN, ALL, {"name": "dev"}, dev=True)
    assert settings.Get(PLUGIN, ALL, dev=True)["name"] == "dev"
    assert settings.Get(PLUGIN, ALL, dev=False)["name"] is None
    with pytest.raises(ValueError):
        UserDir(str(tmp_path), "../escape", dev=False)


def test_save_keeps_values_that_fit_and_answers_get(settings, tmp_path):
    answer = settings.Save(PLUGIN, ALL, {
        "name": "Alice", "short": "abcde", "body": "x\ny", "count": 3,
        "ratio": 0.75, "free": -2.5, "flag": False, "mention": "here"},
        dev=False)
    assert answer == settings.Get(PLUGIN, ALL, dev=False)
    assert answer["name"] == "Alice" and answer["count"] == 3
    assert answer["flag"] is False and answer["mention"] == "here"
    with open(settings.ConfigPath(PLUGIN, dev=False), encoding="utf-8") \
            as file:
        saved = json.load(file)
    assert "webhook_url" not in saved
    assert saved["ratio"] == 0.75


@pytest.mark.parametrize("key, value", [
    ("name", 5),
    ("name", "x" * 201),
    ("short", "abcdef"),
    ("body", "x" * 4001),
    ("count", 0),
    ("count", 11),
    ("count", 2.5),
    ("count", True),
    ("count", "3"),
    ("count", float("nan")),
    ("ratio", 0.3),
    ("flag", 1),
    ("flag", "true"),
    ("mention", "everyone"),
    ("mention", 1),
])
def test_a_value_of_the_wrong_type_is_refused(settings, key, value):
    answer = settings.Save(PLUGIN, ALL, {key: value}, dev=False)
    assert isinstance(answer, Refused)
    assert answer.reason == StartRefusal.INVALID_REQUEST
    assert answer.detail.startswith(key + ":")
    if isinstance(value, str) and len(value) > 3:
        assert value not in answer.detail
    assert not os.path.exists(settings.ConfigPath(PLUGIN, dev=False))


def test_the_text_limits_are_the_defaults_and_the_cap(settings):
    assert not isinstance(settings.Save(
        PLUGIN, ALL, {"name": "x" * 200, "body": "y" * 4000}, dev=False),
        Refused)
    capped = Field(key="big", type="text", label="Big", max_length=50000)
    assert CheckFieldValue(capped, "z" * 20000) == (True, "z" * 20000)
    assert CheckFieldValue(capped, "z" * 20001)[0] is False


def test_one_bad_value_writes_nothing(settings):
    settings.Save(PLUGIN, ALL, {"name": "Alice"}, dev=False)
    answer = settings.Save(PLUGIN, ALL, {"name": "Bob", "count": 99},
                           dev=False)
    assert isinstance(answer, Refused)
    assert settings.Get(PLUGIN, ALL, dev=False)["name"] == "Alice"


def test_save_is_a_partial_update_and_none_clears(settings):
    settings.Save(PLUGIN, ALL, {"name": "Alice", "mention": "here"},
                  dev=False)
    settings.Save(PLUGIN, ALL, {"count": 2}, dev=False)
    values = settings.Get(PLUGIN, ALL, dev=False)
    assert values["name"] == "Alice" and values["count"] == 2
    settings.Save(PLUGIN, ALL, {"mention": None, "name": ""}, dev=False)
    values = settings.Get(PLUGIN, ALL, dev=False)
    # Back to the default / None.
    assert values["mention"] == "none" and values["name"] is None


@pytest.mark.parametrize("values", [
    {"unknown": 1},
    {"webhook_url": "https://x"},
    {"Name": "x"},
])
def test_an_unknown_or_secret_key_is_refused(settings, values):
    answer = settings.Save(PLUGIN, ALL, values, dev=False)
    assert isinstance(answer, Refused)
    assert answer.reason == StartRefusal.INVALID_REQUEST


def test_a_saved_value_that_no_longer_fits_is_its_default(settings):
    settings.Save(PLUGIN, ALL, {"mention": "here", "count": 8}, dev=False)
    narrower = _manifest(
        Field(key="mention", type="select", label="M", default="none",
              options=(Option("none", "None"),)),
        Field(key="count", type="number", label="C", max=5, default=1))
    assert settings.Get(PLUGIN, narrower, dev=False) == {
        "mention": "none", "count": 1}


def test_a_key_left_out_of_the_manifest_is_kept(settings):
    settings.Save(PLUGIN, ALL, {"name": "Alice"}, dev=False)
    settings.Save(PLUGIN, _manifest(BOOL), {"flag": True}, dev=False)
    assert settings.Get(PLUGIN, ALL, dev=False)["name"] == "Alice"


def test_config_with_a_bom_is_read(settings):
    path = settings.ConfigPath(PLUGIN, dev=False)
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8-sig") as file:
        json.dump({"name": "BOM"}, file)
    assert settings.Get(PLUGIN, ALL, dev=False)["name"] == "BOM"


def test_a_damaged_config_reads_as_nothing_saved(settings):
    path = settings.ConfigPath(PLUGIN, dev=False)
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        file.write("{not json")
    assert settings.Get(PLUGIN, ALL, dev=False)["mention"] == "none"


# ── file / folder ──────────────────────────────────────────────────────

def test_a_file_pick_is_stored_as_its_absolute_path(settings, picks,
                                                    tmp_path):
    image = tmp_path / "logo.PNG"
    image.write_bytes(b"png")
    folder = tmp_path / "out"
    folder.mkdir()
    picks.table[("file", "p1")] = str(image)
    picks.table[("folder", "p2")] = str(folder)
    answer = settings.Save(PLUGIN, ALL, {"logo": "p1", "where": "p2"},
                           dev=False)
    assert answer["logo"] == str(image)
    assert answer["where"] == str(folder)
    # Kept while another value changes (the page cannot send the path).
    settings.Save(PLUGIN, ALL, {"name": "x"}, dev=False)
    assert settings.Get(PLUGIN, ALL, dev=False)["logo"] == str(image)


def test_file_and_folder_refusals(settings, picks, tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("x")
    image = tmp_path / "logo.jpg"
    image.write_bytes(b"jpg")
    picks.table.update({
        ("file", "ext"): str(text),
        ("file", "missing"): str(tmp_path / "gone.png"),
        ("file", "relative"): "logo.jpg",
        ("file", "dir"): str(tmp_path),
        ("folder", "file"): str(image),
        ("file", "unc"): "\\\\server\\share\\logo.png",
        ("folder", "unc"): "//server/share",
    })
    for key, pick in [("logo", "ext"), ("logo", "missing"),
                      ("logo", "relative"), ("logo", "dir"),
                      ("logo", "unc"), ("logo", "nobody"), ("logo", 5),
                      ("where", "file"), ("where", "unc"),
                      # A folder's pick id is not a file's.
                      ("logo", "folder")]:
        answer = settings.Save(PLUGIN, ALL, {key: pick}, dev=False)
        assert isinstance(answer, Refused), (key, pick)
        assert answer.reason == StartRefusal.INVALID_REQUEST
        assert str(tmp_path) not in answer.detail


def test_without_a_picker_a_file_is_refused(tmp_path):
    settings = PluginSettings(str(tmp_path), _Secrets())
    assert isinstance(settings.Save(PLUGIN, ALL, {"logo": "p"}, dev=False),
                      Refused)


def test_a_stored_path_is_checked_again_without_the_disk(settings):
    assert CheckFieldValue(FILE, "\\\\server\\x.png", stored=True)[0] \
        is False
    assert CheckFieldValue(FILE, "relative.png", stored=True)[0] is False
    absolute = os.path.abspath("gone.png")
    assert CheckFieldValue(FILE, absolute, stored=True) == (True, absolute)
    assert CheckFieldValue(FILE, os.path.abspath("gone.gif"),
                           stored=True)[0] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_absolute_local_paths_on_windows():
    assert IsAbsoluteLocal("C:\\x\\y.png")
    assert IsAbsoluteLocal("d:/x")
    assert not IsAbsoluteLocal("\\x\\y.png")
    assert not IsAbsoluteLocal("C:x.png")
    assert not IsAbsoluteLocal("\\\\?\\C:\\x.png")
    assert not IsAbsoluteLocal("\\\\server\\share\\x")
    assert not IsAbsoluteLocal("")


def test_a_secret_or_source_type_is_never_a_value():
    assert CheckFieldValue(SECRET, "x")[0] is False
    assert CheckFieldValue(Field(key="s", type="state", label="S"), "x")[0] \
        is False
    assert CheckFieldValue(Field(key="u", type="color", label="U"), "x")[0] \
        is False


# ── Missing ────────────────────────────────────────────────────────────

def test_missing_reads_config_and_secrets_together(tmp_path, picks):
    secrets = _Secrets()
    settings = PluginSettings(str(tmp_path), secrets, picks)
    manifest = _manifest(
        Field(key="name", type="text", label="N", required=True),
        Field(key="mode", type="select", label="M", required=True,
              default="a", options=(Option("a", "A"),)),
        Field(key="flag", type="bool", label="F", required=True),
        Field(key="optional", type="text", label="O"),
        SECRET)
    assert settings.Missing(PLUGIN, manifest, dev=False) \
        == ("name", "flag", "webhook_url")
    settings.Save(PLUGIN, manifest, {"name": "x", "flag": False}, dev=False)
    assert settings.Missing(PLUGIN, manifest, dev=False) == ("webhook_url",)
    secrets.keys = frozenset({"webhook_url"})
    assert settings.Missing(PLUGIN, manifest, dev=False) == ()
    assert secrets.asked[-1] == (PLUGIN, False)
    assert settings.Missing(PLUGIN, manifest, dev=True) \
        == ("name", "flag")
    assert secrets.asked[-1] == (PLUGIN, True)


def test_missing_without_a_required_secret_asks_no_secrets(tmp_path):
    secrets = _Secrets()
    settings = PluginSettings(str(tmp_path), secrets)
    assert settings.Missing(PLUGIN, _manifest(TEXT), dev=False) == ()
    assert secrets.asked == []
