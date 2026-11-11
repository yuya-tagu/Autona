"""The Qt side of the country catalog, against the pre-S1c recording.

ADR-005 S1c moved the country data into the Qt-free core CountryCatalog
(tests/test_country_catalog.py). What stayed in Qt is checked here against
the same recording (tests/fixtures/countries/golden.json, taken from the old
AutonaCountryHelper before the move by record_golden.py next to it):

- the shared country model, row by row (DisplayRole, UserRole, enabled,
  selectable), under three locale pairs and with custom flags installed --
  and that a reload refills that one model instead of swapping in a new
  one (agent-pitfalls #13 / #15);
- the Web reader get_states: the recorded payload byte for byte, the same
  whatever a player loaded has written, no @gui_thread_sync, and answered on
  a worker thread while the GUI thread is not spinning its event loop.

The model's consumers went with them: the player cards (ADR-005 S4ac-3;
their reload, wiring and fallback tests, and the region combo their
LoadStates filled -- the recording's `comboDigests` / `comboJPHead`, which
nothing reads any more) and the TOP 8 panel (S4i, a React page reading
GET /api/v1/countries).
"""
import ast
import hashlib
import json
import threading
from pathlib import Path

import pytest

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QComboBox

from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.core.CountryCatalog import CountryCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "countries" / "golden.json")
    .read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:16]


def _rows(model):
    rows = []
    for row in range(model.rowCount()):
        item = model.item(row)
        rows.append([item.data(Qt.ItemDataRole.DisplayRole),
                     item.data(Qt.ItemDataRole.UserRole),
                     item.isEnabled(), item.isSelectable()])
    return rows


@pytest.fixture
def countries(autonaApp):
    """Load the countries under a recorded locale, custom flags installed.

    Yields `load(label)`. Afterwards the flags are removed and the catalog
    and the model are reloaded under the harness's own locale, so later
    tests see the standard data.
    """
    flags = autonaApp.dir / "user_data" / "additional_flag"
    flags.mkdir(parents=True, exist_ok=True)
    for name in GOLDEN["customFlags"]:
        (flags / name).write_bytes(b"")
    saved = (AutonaLocaleHelper.programLocale, AutonaLocaleHelper.exportLocale)

    def load(label):
        locale = GOLDEN["locales"][label]
        AutonaLocaleHelper.programLocale = locale["program"]
        AutonaLocaleHelper.exportLocale = locale["export"]
        AutonaCountryHelper.LoadCountries()
        return CountryCatalog.instance

    try:
        yield load
    finally:
        for name in GOLDEN["customFlags"]:
            (flags / name).unlink(missing_ok=True)
        AutonaLocaleHelper.programLocale, AutonaLocaleHelper.exportLocale = saved
        AutonaCountryHelper.LoadCountries()


# ── the country model ────────────────────────────────────────────────────

@pytest.mark.parametrize("label", list(GOLDEN["scenarios"]))
def test_the_country_model_matches_the_recording(countries, label):
    golden = GOLDEN["scenarios"][label]
    countries(label)

    rows = _rows(AutonaCountryHelper.countryModel)
    assert len(rows) == golden["modelRowCount"]
    assert rows[:3] == golden["modelRowsHead"]
    # The separator's label goes through the translator the harness's
    # LoadLocale() installed for this machine; the recording had none.
    separator = 1 + len(golden["codes"])
    separatorText = "    " + QApplication.translate(
        "app", "Custom Flags").upper() + "    "
    assert rows[separator] == [separatorText] + golden["modelRowsTail"][0][1:]
    assert rows[separator + 1:] == golden["modelRowsTail"][1:]
    assert [digest(row) for index, row in enumerate(rows)
            if index != separator] \
        == [rowDigest for index, rowDigest
            in enumerate(golden["modelRowDigests"]) if index != separator]


def test_a_reload_refills_the_one_model(countries):
    """The model every combo and completer holds is never replaced: a
    swapped-out model was the one exception to the retire rule
    (agent-pitfalls #15), and a refill needs none."""
    countries("en")
    model = AutonaCountryHelper.countryModel
    combo = QComboBox()
    combo.setModel(model)
    rowCount = model.rowCount()
    updates = []

    def updated():
        updates.append(1)

    AutonaCountryHelper.signals.countriesUpdated.connect(updated)
    try:
        countries("ja")
    finally:
        AutonaCountryHelper.signals.countriesUpdated.disconnect(updated)

    assert AutonaCountryHelper.countryModel is model
    assert model.rowCount() == rowCount
    assert combo.model() is model and combo.count() == rowCount
    assert combo.itemText(1) == GOLDEN["scenarios"]["ja"]["modelRowsHead"][1][0]
    assert updates == [1]


def test_a_player_loaded_with_a_country_changes_no_payload(
        countries, autonaApp):
    """What the Web is sent for a country does not depend on whether a
    player with it has been loaded (it used to depend on whether the
    desktop had shown it: the old LoadStates wrote into the table). The
    load is what the Qt card's SetData ran until ADR-005 S4ac-3."""
    from tests.conftest import load_player

    catalog = countries("en")
    actions = autonaApp.web()
    before = json.dumps(actions.get_states("JP"))
    table = digest(catalog.Current().countries)

    board = autonaApp.ready(1)
    load_player(board, 1, {"gamerTag": "TAG", "country_code": "JP",
                           "state_code": "13"})
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.state") \
        == catalog.State("JP", "13")
    assert json.dumps(actions.get_states("JP")) == before
    assert digest(catalog.Current().countries) == table


# ── the Web reader (get_states) ──────────────────────────────────────────

def test_get_states_returns_the_recorded_payload(countries, autonaApp):
    golden = GOLDEN["states"]
    countries("en")
    actions = autonaApp.web()

    assert {code: digest(actions.get_states(code))
            for code in golden["afterDigests"]} == golden["afterDigests"]
    # Byte for byte as Socket.IO sends it (SocketioJson = json.dumps).
    assert json.dumps(actions.get_states("JP")) == json.dumps(golden["afterJP"])
    assert actions.get_states("XX-NOPE") is None


def test_get_states_reads_the_catalog_without_a_gui_hop():
    """S1c's share of the S1 completion condition: the last country reader
    of the Web server carries no @gui_thread_sync and reads the catalog.
    It is a web operation of both shells since ADR-005 S3c."""
    tree = ast.parse((PROJECT_ROOT / "src" / "AutonaWebOperations.py")
                     .read_text(encoding="utf-8"))
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "get_states")

    decorators = [ast.unparse(d) for d in method.decorator_list]
    assert not any("gui_thread_sync" in d for d in decorators), decorators
    body = ast.unparse(method)
    assert "CountryCatalog.instance" in body
    assert "AutonaCountryHelper" not in body


def test_get_states_answers_on_a_worker_thread(countries, autonaApp):
    """The way the Flask / Socket.IO threads call it -- with this (the GUI)
    thread NOT spinning the event loop meanwhile. A reader that still
    marshalled onto the GUI thread would wait in run_sync until the join
    below times out."""
    countries("en")
    actions = autonaApp.web()
    results = {}

    def work():
        results["JP"] = actions.get_states("JP")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "get_states waited for the GUI thread"
    assert json.dumps(results["JP"]) == json.dumps(GOLDEN["states"]["afterJP"])
