"""The Qt side of the country catalog, against the pre-S1c recording.

ADR-005 S1c moved the country data into the Qt-free core CountryCatalog
(tests/test_country_catalog.py). What stayed in Qt is checked here against
the same recording (tests/fixtures/countries/golden.json, taken from the old
AutonaCountryHelper before the move by record_golden.py next to it):

- the shared country model, row by row (DisplayRole, UserRole, enabled,
  selectable), under three locale pairs and with custom flags installed --
  and that a reload refills that one model instead of swapping in a new
  one (agent-pitfalls #13 / #15);
- what that reload does to the model's two consumers, the TOP 8 panel and
  the player cards: nothing is published and the combos keep showing the
  state; that a card's country wiring is done once (countriesUpdated no
  longer stacks connections); and the fallback for a card built before
  the first load;
- the region combo LoadStates fills, for every country -- and that filling
  it no longer writes the flag `asset` into the catalog's table;
- the Web reader get_states: the recorded payload byte for byte, the same
  whatever the desktop has shown, no @gui_thread_sync, and answered on a
  worker thread while the GUI thread is not spinning its event loop.
"""
import ast
import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from qtpy.QtCore import SIGNAL, QObject, Qt
from qtpy.QtWidgets import QApplication, QComboBox, QLineEdit

from src.AutonaBasePlayerWidget import AutonaBasePlayerWidget
from src.AutonaTop8Widget import AutonaTop8Widget
from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.core import StateSchema as schema
from src.core import Top8Model as top8
from src.core import Top8Service
from src.core.CountryCatalog import CountryCatalog
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager

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


class _RegionCombo(QObject):
    """Just enough of a player widget to run the real LoadStates: the
    recording drove the old one through the same two combos."""
    _EnsureStateModel = AutonaBasePlayerWidget._EnsureStateModel
    LoadStates = AutonaBasePlayerWidget.LoadStates

    def __init__(self):
        super().__init__()
        self.country = QComboBox()
        self.country.setModel(AutonaCountryHelper.countryModel)
        self.state = QComboBox()

    def _w(self, name, klass=None):
        return {schema.PLAYER_COUNTRY_KEY: self.country,
                schema.PLAYER_STATE_KEY: self.state}[name]

    def show(self, row):
        self.country.setCurrentIndex(row)
        self.LoadStates(row)
        return self.country.currentData(Qt.ItemDataRole.UserRole)["code"]


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


# ── the model's consumers: reload, wiring, fallback ──────────────────────

def _row(model, code):
    for row in range(model.rowCount()):
        data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("code") == code:
            return row
    raise AssertionError(f"no row for {code!r}")


def _code(combo):
    data = combo.currentData(Qt.ItemDataRole.UserRole)
    return data.get("code") if isinstance(data, dict) else None


class _Top8Countries(QObject):
    """The TOP 8 panel's country half, on the panel's real methods.

    The panel itself owns a QWebEngineView (its live preview), which keeps
    it out of the widget harness. What is borrowed here is the whole path
    between the shared country model and Top8Service.PublishPlayers: the
    combos as the panel makes them, its handlers, and its two
    country-model slots, connected the way its __init__ connects them.
    """
    _makeCountryCombo = AutonaTop8Widget._makeCountryCombo
    _onCountriesAboutToReload = AutonaTop8Widget._onCountriesAboutToReload
    _rebindCountryModels = AutonaTop8Widget._rebindCountryModels
    _selectCountry = staticmethod(AutonaTop8Widget._selectCountry)
    _onPlayerComboChanged = AutonaTop8Widget._onPlayerComboChanged
    _syncPlayersToState = AutonaTop8Widget._syncPlayersToState
    _resolveCharData = AutonaTop8Widget._resolveCharData

    def __init__(self):
        super().__init__()
        self._updating = False
        self._heldForCountryReload = False
        self.playerInputs = {}
        for slot in top8.PLAYER_SLOTS:
            country = self._makeCountryCombo()
            country.currentIndexChanged.connect(self._onPlayerComboChanged)
            self.playerInputs[slot] = {
                "name": QLineEdit(), "sponsor": QLineEdit(),
                "country": country, "char": QComboBox(), "color": QComboBox(),
            }
        signals = AutonaCountryHelper.signals
        signals.countriesAboutToReload.connect(self._onCountriesAboutToReload)
        signals.countriesUpdated.connect(self._rebindCountryModels)

    def _refreshMatchRows(self, matches, update_scores=True):
        """The match table; it renders names, not countries."""

    def close(self):
        signals = AutonaCountryHelper.signals
        signals.countriesAboutToReload.disconnect(
            self._onCountriesAboutToReload)
        signals.countriesUpdated.disconnect(self._rebindCountryModels)


def test_a_reload_publishes_nothing_from_top8(countries, autonaApp):
    """A reload clears the model under every TOP 8 country combo and
    refills it. Each index change on the way used to reach
    _syncPlayersToState and publish all eight slots with blank countries.
    The state must come out as it went in, and the combos show it."""
    countries("en")
    saved = deep_clone(StateManager.Get(Top8Service.PLAYERS_PATH))
    panel = _Top8Countries()
    try:
        chosen = {"1": "JP", "2": "US"}
        for slot, code in chosen.items():
            combo = panel.playerInputs[slot]["country"]
            combo.setCurrentIndex(_row(combo.model(), code))  # publishes
        autonaApp.settle()
        before = deep_clone(StateManager.Get(Top8Service.PLAYERS_PATH))
        assert before["1"]["country"]["code"] == "JP"
        assert before["2"]["country"]["code"] == "US"

        with patch.object(Top8Service, "PublishPlayers",
                          wraps=Top8Service.PublishPlayers) as publish:
            countries("ja")
            autonaApp.settle()

        assert publish.call_count == 0
        assert StateManager.Get(Top8Service.PLAYERS_PATH) == before
        assert {slot: _code(w["country"])
                for slot, w in panel.playerInputs.items()} \
            == {slot: chosen.get(slot) for slot in top8.PLAYER_SLOTS}
        # Released: the next real choice is published again.
        assert panel._updating is False
        combo = panel.playerInputs["3"]["country"]
        combo.setCurrentIndex(_row(combo.model(), "FR"))
        assert StateManager.Get(
            Top8Service.PLAYERS_PATH)["3"]["country"]["code"] == "FR"
    finally:
        panel.close()
        if saved is None:
            StateManager.Unset(Top8Service.PLAYERS_PATH)
        else:
            StateManager.Set(Top8Service.PLAYERS_PATH, saved)
        autonaApp.settle()


def test_a_reload_leaves_a_player_card_and_its_state_alone(
        countries, autonaApp):
    """The same reload under a live player card: nothing is published
    (each index change used to reach the location publish, blanking the
    country and the region), and the card still shows what the state holds."""
    countries("en")
    player = autonaApp.board(1).team1playerWidgets[0]
    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()
    country = player._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    state = player._w(schema.PLAYER_STATE_KEY, QComboBox)
    before = {key: deep_clone(autonaApp.get(f"{player.path}.{key}"))
              for key in schema.PLAYER_LOCATION_KEYS}
    assert before[schema.PLAYER_COUNTRY_KEY]["code"] == "JP"
    assert before[schema.PLAYER_STATE_KEY]["original_code"] == "13"

    with patch.object(player.player, "SetCountry",
                      wraps=player.player.SetCountry) as setCountry, \
            patch.object(player.player, "SetRegion",
                         wraps=player.player.SetRegion) as setRegion:
        countries("ja")
        autonaApp.settle()

        assert setCountry.call_count == 0
        assert setRegion.call_count == 0
        assert {key: autonaApp.get(f"{player.path}.{key}")
                for key in schema.PLAYER_LOCATION_KEYS} == before
        assert _code(country) == "JP"
        assert state.currentData(
            Qt.ItemDataRole.UserRole)["original_code"] == "13"
        assert not country.signalsBlocked()

        # Released: the next real choice goes through as before.
        country.setCurrentIndex(_row(country.model(), "US"))
        autonaApp.settle()

    assert autonaApp.get(
        f"{player.path}.{schema.PLAYER_COUNTRY_KEY}")["code"] == "US"
    # The region (blank), then the country.
    assert setRegion.call_count == 1
    assert setCountry.call_count == 1


def test_country_updates_do_not_stack_connections(autonaApp):
    """countriesUpdated was connected to the method that did the wiring,
    so every emit added one more LoadStates connection (and a completer).
    The wiring runs once; an emit only (re)attaches the model."""
    player = autonaApp.board(1).team1playerWidgets[0]
    country = player._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    signals = AutonaCountryHelper.signals

    def wiring():
        return (country.receivers(SIGNAL("currentIndexChanged(int)")),
                signals.receivers(SIGNAL("countriesUpdated()")),
                signals.receivers(SIGNAL("countriesAboutToReload()")),
                country.completer())

    before = wiring()
    for _ in range(3):
        signals.countriesUpdated.emit()

    assert wiring() == before
    assert country.model() is AutonaCountryHelper.countryModel
    assert country.completer().model() is AutonaCountryHelper.countryModel


def test_a_second_reload_notice_does_not_leave_the_card_silenced(autonaApp):
    """Two notices before the countriesUpdated that ends them (the fallback
    tests' fixture below does exactly that). Recording the second one would
    capture the combo as already blocked, and finishing the reload would
    then leave it blocked: the card would stop publishing its country."""
    player = autonaApp.board(1).team1playerWidgets[0]
    player.SetData({"gamerTag": "TAG", "country_code": "JP"},
                   dontLoadFromDB=True)
    autonaApp.settle()
    country = player._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    signals = AutonaCountryHelper.signals

    signals.countriesAboutToReload.emit()
    signals.countriesAboutToReload.emit()
    signals.countriesUpdated.emit()

    assert not country.signalsBlocked()
    assert _code(country) == "JP"
    country.setCurrentIndex(_row(country.model(), "US"))
    autonaApp.settle()
    assert autonaApp.get(
        f"{player.path}.{schema.PLAYER_COUNTRY_KEY}")["code"] == "US"


def test_the_top8_panel_makes_the_stand_in_s_connections():
    """_Top8Countries borrows the panel's handlers but makes its own
    connections; this pins that the real panel's __init__ makes the same
    two (read from the source: the panel owns a QWebEngineView)."""
    tree = ast.parse((PROJECT_ROOT / "src" / "AutonaTop8Widget.py")
                     .read_text(encoding="utf-8"))
    panel = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef)
                 and node.name == "AutonaTop8Widget")
    init = next(node for node in panel.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "__init__")

    connected = {}
    for node in ast.walk(init):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Attribute)
                and ast.unparse(node.func.value.value)
                == "AutonaCountryHelper.signals"):
            connected[node.func.value.attr] = ast.unparse(node.args[0])

    assert connected == {
        "countriesAboutToReload": "self._onCountriesAboutToReload",
        "countriesUpdated": "self._rebindCountryModels",
    }


@contextmanager
def _editor_card(autonaApp, catalogLoads=True):
    """A player card on the PlayerDB editor's path (never a live board),
    reaped afterwards the way tests/test_player_widgets.py reaps it.
    With catalogLoads=False it is built while the country load fails."""
    from qtpy.QtCore import QCoreApplication, QEvent

    from src.AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget

    path = f"{schema.PLAYER_EDITOR_ROOT}.1"
    if catalogLoads:
        card = AutonaScoreboardPlayerWidget(index=1, teamNumber=1, path=path)
    else:
        with patch.object(CountryCatalog.instance, "Load",
                          return_value=False):
            card = AutonaScoreboardPlayerWidget(
                index=1, teamNumber=1, path=path)
    try:
        yield card
    finally:
        card.setParent(None)
        card.deleteLater()
        QCoreApplication.sendPostedEvents(card, QEvent.Type.DeferredDelete)
        StateManager.Unset(schema.PLAYER_EDITOR_ROOT)
        autonaApp.settle()


@pytest.fixture
def no_country_model(autonaApp):
    """Start with no shared model, as before the window's first load --
    with a live board up whose card shows a country, which must not notice.

    Both ends of the swap are announced to the model's consumers as a
    reload: countriesAboutToReload here, and the countriesUpdated that the
    test's first load (or the restore below) emits. The live cards then
    re-attach to whichever model is current without publishing. Without
    it, the test's first load would make every live card publish a blank
    country and region, and the restore would leave them on the test's
    model, dropped right after.
    """
    live = autonaApp.board(1).team1playerWidgets[0]
    live.SetData({"gamerTag": "LIVE", "country_code": "JP",
                  "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()
    before = deep_clone(StateManager.Get(schema.SCORE_ROOT))
    signals = AutonaCountryHelper.signals
    saved = AutonaCountryHelper.countryModel
    signals.countriesAboutToReload.emit()
    AutonaCountryHelper.countryModel = None
    try:
        yield
    finally:
        AutonaCountryHelper.countryModel = saved
        signals.countriesAboutToReload.emit()
        signals.countriesUpdated.emit()
        autonaApp.settle()

    country = live._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    assert country.model() is saved
    assert _code(country) == "JP" and not country.signalsBlocked()
    assert StateManager.Get(schema.SCORE_ROOT) == before


def _assert_card_works(autonaApp, card):
    model = AutonaCountryHelper.countryModel
    country = card._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    state = card._w(schema.PLAYER_STATE_KEY, QComboBox)
    assert country.model() is model
    assert country.completer().model() is model
    country.setCurrentIndex(_row(model, "JP"))
    autonaApp.settle()
    assert autonaApp.get(
        f"{card.path}.{schema.PLAYER_COUNTRY_KEY}")["code"] == "JP"
    assert state.count() == 1 + len(CountryCatalog.instance.States("JP"))


def test_a_card_built_before_the_first_load_loads_the_countries(
        autonaApp, no_country_model):
    """The fallback in LoadCountries: a card that finds no model asks the
    helper for one and attaches it."""
    with _editor_card(autonaApp) as card:
        assert AutonaCountryHelper.countryModel is not None
        assert AutonaCountryHelper.countryModel.rowCount() > 1
        _assert_card_works(autonaApp, card)


def test_a_card_built_while_the_load_fails_attaches_the_later_one(
        autonaApp, no_country_model):
    """When that load fails too, the card is left without a model, and the
    countriesUpdated connection attaches the model the next successful
    load creates."""
    with _editor_card(autonaApp, catalogLoads=False) as card:
        country = card._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
        assert AutonaCountryHelper.countryModel is None
        assert country.count() == 0

        AutonaCountryHelper.LoadCountries()  # the load that lands later

        _assert_card_works(autonaApp, card)


# ── LoadStates ───────────────────────────────────────────────────────────

def test_load_states_fills_the_recorded_rows_and_writes_nothing_back(
        countries):
    """Every country's region combo is what the old LoadStates built. The
    old one also wrote each region's `asset` into the shared table on the
    way; the catalog's table must come out of it untouched."""
    golden = GOLDEN["states"]
    catalog = countries("en")
    table = digest(catalog.Current().countries)
    regions = _RegionCombo()

    combos = {}
    for row in range(1, 1 + len(GOLDEN["scenarios"]["en"]["codes"])):
        code = regions.show(row)
        combos[code] = digest(_rows(regions.state.model()))

    assert combos == golden["comboDigests"]
    regions.show(GOLDEN["scenarios"]["en"]["codes"].index("JP") + 1)
    assert _rows(regions.state.model())[:3] == golden["comboJPHead"]
    assert digest(catalog.Current().countries) == table
    assert GOLDEN["scenarios"]["en"]["countriesDigest"] == table


def test_a_player_widget_showing_a_country_changes_no_payload(
        countries, autonaApp):
    """Through a real player card: what the Web is sent for a country does
    not depend on whether the desktop has shown it (it used to)."""
    catalog = countries("en")
    actions = autonaApp.actions()
    before = json.dumps(actions.get_states("JP"))
    table = digest(catalog.Current().countries)

    player = autonaApp.board(1).team1playerWidgets[0]
    player.SetData({"gamerTag": "TAG", "country_code": "JP",
                    "state_code": "13"}, dontLoadFromDB=True)
    autonaApp.settle()

    assert autonaApp.get(f"{player.path}.state") == catalog.State("JP", "13")
    assert json.dumps(actions.get_states("JP")) == before
    assert digest(catalog.Current().countries) == table


# ── the Web reader (get_states) ──────────────────────────────────────────

def test_get_states_returns_the_recorded_payload(countries, autonaApp):
    golden = GOLDEN["states"]
    countries("en")
    actions = autonaApp.actions()

    assert {code: digest(actions.get_states(code))
            for code in golden["afterDigests"]} == golden["afterDigests"]
    # Byte for byte as Socket.IO sends it (SocketioJson = json.dumps).
    assert json.dumps(actions.get_states("JP")) == json.dumps(golden["afterJP"])
    assert actions.get_states("XX-NOPE") is None


def test_get_states_reads_the_catalog_without_a_gui_hop():
    """S1c's share of the S1 completion condition: the last country reader
    of the Web server carries no @gui_thread_sync and reads the catalog."""
    tree = ast.parse((PROJECT_ROOT / "src" / "AutonaWebServerActions.py")
                     .read_text(encoding="utf-8"))
    actionsClass = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef)
                        and node.name == "WebServerActions")
    method = next(node for node in actionsClass.body
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
    actions = autonaApp.actions()
    results = {}

    def work():
        results["JP"] = actions.get_states("JP")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "get_states waited for the GUI thread"
    assert json.dumps(results["JP"]) == json.dumps(GOLDEN["states"]["afterJP"])
