"""PluginSecrets (ADR-009 B.3 / D6, lane L3): a plugin's secret settings in
DPAPI, out of every log and error text.

The real DPAPI round trip runs on Windows only; the rest runs with a stand-in
cipher (a reversible byte flip) so the file's rules are checked anywhere.
"""

import os
import sys

import pytest
from loguru import logger

from src.core import PluginSecrets as module
from src.core.PluginSecrets import PluginSecrets, SecretValueError
from src.core.PluginTypes import ENV_SECRET_PREFIX, SECRET_MAX_LENGTH

PLUGIN = "taguyan.webhook"
SECRET = "https://discord.com/api/webhooks/123/very-secret-token"

windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="DPAPI is Windows'")


def _flip(data):
    return bytes(byte ^ 0x5A for byte in data)


@pytest.fixture
def secrets(tmp_path):
    return PluginSecrets(str(tmp_path / "user_data"), protect=_flip,
                         unprotect=_flip)


@pytest.fixture
def logged():
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    yield lines
    logger.remove(sink)


def test_a_secret_round_trips_and_is_cleared(secrets, tmp_path):
    assert secrets.Keys(PLUGIN, dev=False) == frozenset()
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Set(PLUGIN, "token", "abc", dev=False)
    assert secrets.Keys(PLUGIN, dev=False) == {"webhook_url", "token"}
    path = tmp_path / "user_data" / "plugins" / PLUGIN / "secrets.bin"
    assert secrets.Path(PLUGIN, dev=False) == str(path)
    assert path.is_file()
    secrets.Delete(PLUGIN, "token", dev=False)
    assert secrets.Keys(PLUGIN, dev=False) == {"webhook_url"}
    # Clearing one not set is nothing.
    secrets.Delete(PLUGIN, "token", dev=False)
    # The last one cleared removes the file.
    secrets.Delete(PLUGIN, "webhook_url", dev=False)
    assert not path.exists()


def test_forget_removes_the_file(secrets):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Forget(PLUGIN, dev=False)
    assert not os.path.exists(secrets.Path(PLUGIN, dev=False))
    assert secrets.Keys(PLUGIN, dev=False) == frozenset()
    # Forgetting twice is fine.
    secrets.Forget(PLUGIN, dev=False)


def test_a_dev_plugin_keeps_its_secrets_apart(secrets, tmp_path):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=True)
    assert secrets.Path(PLUGIN, dev=True) == str(
        tmp_path / "user_data" / "plugins" / "_dev" / PLUGIN / "secrets.bin")
    assert secrets.Keys(PLUGIN, dev=True) == {"webhook_url"}
    assert secrets.Keys(PLUGIN, dev=False) == frozenset()
    secrets.Forget(PLUGIN, dev=False)
    assert secrets.Keys(PLUGIN, dev=True) == {"webhook_url"}


def test_the_environment_names_are_the_keys_in_upper_case(secrets):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Set(PLUGIN, "api_key2", "k", dev=False)
    env = secrets.Environment(PLUGIN, dev=False)
    assert env == {"AUTONA_SECRET_WEBHOOK_URL": SECRET,
                   "AUTONA_SECRET_API_KEY2": "k"}
    assert all(name.startswith(ENV_SECRET_PREFIX) for name in env)
    assert module.EnvironmentName("webhook_url") \
        == "AUTONA_SECRET_WEBHOOK_URL"


@pytest.mark.parametrize("value", [
    "",
    "a\nb",
    "a\rb",
    "x" * (SECRET_MAX_LENGTH + 1),
    12345,
    None,
])
def test_a_value_that_does_not_fit_is_refused_without_showing_it(
        secrets, value):
    with pytest.raises(SecretValueError) as raised:
        secrets.Set(PLUGIN, "webhook_url", value, dev=False)
    if isinstance(value, str) and value:
        assert value not in str(raised.value)
    assert secrets.Keys(PLUGIN, dev=False) == frozenset()


def test_the_longest_value_fits(secrets):
    secrets.Set(PLUGIN, "webhook_url", "x" * SECRET_MAX_LENGTH, dev=False)
    assert secrets.Environment(PLUGIN, dev=False)[
        "AUTONA_SECRET_WEBHOOK_URL"] == "x" * SECRET_MAX_LENGTH


@pytest.mark.parametrize("key", ["", "Upper", "1st", "a-b", "a" * 33, None])
def test_a_bad_key_is_refused(secrets, key):
    with pytest.raises(SecretValueError):
        secrets.Set(PLUGIN, key, "v", dev=False)
    with pytest.raises(SecretValueError):
        secrets.Delete(PLUGIN, key, dev=False)


@pytest.mark.parametrize("pluginId", ["..", "../x.y", "a\\b.c", "nodot", ""])
def test_a_bad_plugin_id_names_no_folder(secrets, pluginId):
    with pytest.raises(ValueError):
        secrets.Set(pluginId, "k", "v", dev=False)


def test_no_value_reaches_the_log(secrets, logged):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Environment(PLUGIN, dev=False)
    secrets.Delete(PLUGIN, "webhook_url", dev=False)
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Forget(PLUGIN, dev=False)
    assert logged
    assert not any(SECRET in line for line in logged)


def test_an_undecryptable_file_reads_as_none_set(tmp_path, logged):
    writer = PluginSecrets(str(tmp_path), protect=_flip, unprotect=_flip)
    writer.Set(PLUGIN, "webhook_url", SECRET, dev=False)

    def refuse(_data):
        raise OSError(13, "another user's")

    reader = PluginSecrets(str(tmp_path), protect=_flip, unprotect=refuse)
    assert reader.Keys(PLUGIN, dev=False) == frozenset()
    assert reader.Environment(PLUGIN, dev=False) == {}
    assert any("could not be decrypted" in line for line in logged)
    assert not any(SECRET in line for line in logged)


def test_the_file_never_holds_the_value_in_plain_text(secrets):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    with open(secrets.Path(PLUGIN, dev=False), "rb") as file:
        blob = file.read()
    assert SECRET.encode("utf-8") not in blob


def test_nothing_is_written_on_import_or_construction(tmp_path):
    PluginSecrets(str(tmp_path / "user_data"))
    assert not (tmp_path / "user_data").exists()


@windows_only
def test_dpapi_round_trips_and_hides_the_value(tmp_path):
    secrets = PluginSecrets(str(tmp_path))
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    secrets.Set(PLUGIN, "name", "日本語の値", dev=False)
    with open(secrets.Path(PLUGIN, dev=False), "rb") as file:
        blob = file.read()
    assert SECRET.encode("utf-8") not in blob
    assert SECRET.encode("utf-16-le") not in blob
    # A new instance (a new launch) reads it back.
    again = PluginSecrets(str(tmp_path))
    assert again.Environment(PLUGIN, dev=False) == {
        "AUTONA_SECRET_WEBHOOK_URL": SECRET,
        "AUTONA_SECRET_NAME": "日本語の値"}
    again.Delete(PLUGIN, "name", dev=False)
    assert again.Keys(PLUGIN, dev=False) == {"webhook_url"}
    again.Forget(PLUGIN, dev=False)
    assert again.Keys(PLUGIN, dev=False) == frozenset()


@windows_only
def test_dpapi_refuses_a_damaged_blob():
    with pytest.raises(OSError):
        module.Unprotect(b"not a dpapi blob")
    assert module.Unprotect(module.Protect(b"x")) == b"x"


@pytest.mark.skipif(sys.platform == "win32", reason="off Windows only")
def test_dpapi_is_an_os_error_off_windows():
    with pytest.raises(OSError):
        module.Protect(b"x")


# ── review follow-ups ──────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["a\x00b", "a\tb", "a\x7fb", "a\x85b",
                                   "a\x1bb"])
def test_a_control_character_is_refused(secrets, value):
    with pytest.raises(SecretValueError):
        secrets.Set(PLUGIN, "webhook_url", value, dev=False)


def test_text_beyond_ascii_is_a_value(secrets):
    secrets.Set(PLUGIN, "name", "日本語   é", dev=False)
    assert secrets.Environment(PLUGIN, dev=False)["AUTONA_SECRET_NAME"] \
        == "日本語   é"


def _unreadable(monkeypatch, path):
    import builtins
    real = builtins.open

    def refusing(file, mode="r", *args, **kwargs):
        if os.path.abspath(str(file)) == path and "r" in mode:
            raise PermissionError(32, "in use by another process")
        return real(file, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", refusing)


def test_a_file_that_cannot_be_read_now_is_not_written_over(
        secrets, monkeypatch):
    secrets.Set(PLUGIN, "webhook_url", SECRET, dev=False)
    path = secrets.Path(PLUGIN, dev=False)
    with open(path, "rb") as file:
        before = file.read()
    _unreadable(monkeypatch, path)
    with pytest.raises(OSError):
        secrets.Set(PLUGIN, "token", "abc", dev=False)
    with pytest.raises(OSError):
        secrets.Delete(PLUGIN, "webhook_url", dev=False)
    # A read is "none set" meanwhile.
    assert secrets.Keys(PLUGIN, dev=False) == frozenset()
    monkeypatch.undo()
    with open(path, "rb") as file:
        assert file.read() == before
    assert secrets.Keys(PLUGIN, dev=False) == {"webhook_url"}


def test_an_undecryptable_file_is_warned_about_once(tmp_path, logged):
    writer = PluginSecrets(str(tmp_path), protect=_flip, unprotect=_flip)
    writer.Set(PLUGIN, "webhook_url", SECRET, dev=False)

    def refuse(_data):
        raise OSError(13, "another user's")

    reader = PluginSecrets(str(tmp_path), protect=_flip, unprotect=refuse)
    for _ in range(3):
        reader.Keys(PLUGIN, dev=False)
        reader.Environment(PLUGIN, dev=False)
    assert sum("could not be decrypted" in line for line in logged) == 1
