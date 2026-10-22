"""SettingsManager: concurrent Set() and atomic SaveSettings()."""
import os
import threading

import orjson
import pytest

from src.core.SettingsManager import SettingsManager


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("user_data")
    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "_initialized", False)
    return tmp_path / "user_data" / "settings.json"


def test_concurrent_sets_keep_every_key_and_valid_json(isolated):
    def work(t):
        for i in range(25):
            SettingsManager.Set(f"t{t}.k{i}", i)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    data = orjson.loads(isolated.read_bytes())
    for t in range(8):
        for i in range(25):
            assert data[f"t{t}"][f"k{i}"] == i
    assert [p.name for p in isolated.parent.iterdir()] == ["settings.json"]


def test_failed_save_keeps_old_file(isolated, monkeypatch):
    SettingsManager.Set("a", 1)
    before = isolated.read_bytes()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        SettingsManager.Set("b", 2)
    monkeypatch.undo()

    assert isolated.read_bytes() == before
    assert [p.name for p in isolated.parent.iterdir()] == ["settings.json"]
