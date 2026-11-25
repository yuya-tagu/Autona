"""LoadRoundNames: a missing user terms file is normal; a broken one warns once."""
import json

import pytest
from loguru import logger

from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper as L


@pytest.fixture
def sink(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(L, "matchNames", {})
    monkeypatch.setattr(L, "phaseNames", {})
    monkeypatch.setattr(L, "fgTermLocale", "en-US")
    records = []
    hid = logger.add(lambda m: records.append(m.record), level="DEBUG")
    yield tmp_path, records
    logger.remove(hid)


def _warnings(records):
    return [r for r in records if r["level"].name == "WARNING"]


def test_missing_file_is_silent_and_keeps_builtin_terms(sink):
    _, records = sink
    L.LoadRoundNames()
    assert _warnings(records) == []
    assert L.matchNames and L.phaseNames


def test_broken_json_warns_once_and_keeps_builtin_terms(sink):
    tmp, records = sink
    (tmp / "user_data").mkdir()
    (tmp / "user_data" / "tournament_terms.json").write_text("{bad", encoding="utf-8")
    L.LoadRoundNames()
    w = _warnings(records)
    assert len(w) == 1 and "JSONDecodeError" in w[0]["message"]
    assert L.matchNames and L.phaseNames


def test_override_is_per_key_and_empty_values_are_ignored(sink):
    tmp, _ = sink
    L.LoadRoundNames()
    mkey, pkey = sorted(L.matchNames)[:2]
    phkey = sorted(L.phaseNames)[0]
    builtin = L.matchNames[pkey]
    (tmp / "user_data").mkdir()
    (tmp / "user_data" / "tournament_terms.json").write_text(
        json.dumps({"match": {mkey: "X {0}", pkey: ""}, "phase": {phkey: "Y"}}),
        encoding="utf-8")
    L.LoadRoundNames()
    assert L.matchNames[mkey] == "X {0}"
    assert L.matchNames[pkey] == builtin
    assert L.phaseNames[phkey] == "Y"
