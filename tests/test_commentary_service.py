"""The caster line-up's operations (core/CommentaryService.py, ADR-005 S3e-2).

RECORDING (golden.json) -- only on the pre-S3e-2 tree:

    AUTONA_RECORD_COMMENTARY=1 python -m pytest tests/test_commentary_service.py -k record
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from src import AutonaWebOperations as WebOps
from src.core import CommentaryModel as comms
from tests.fixtures.write_recording import WriteRecorder, encode

RECORD = os.environ.get("AUTONA_RECORD_COMMENTARY") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures" / "commentary"
               / "golden.json")


def _slot(**values):
    slot = comms.DefaultSlot()
    slot.update(values)
    return slot


def _baseline():
    state = comms.DefaultState()
    state["2"] = _slot(name="Old", team="T", twitter="old", role="解説",
                       icon="mic", note="n", display=1)
    state["4"] = _slot(name="Same", team="", twitter="", role="実況",
                       icon="", note="", display=0)
    state["12"] = _slot(name="Twelve", team="TT", twitter="tw", role="MC",
                        icon="phone", note="last", display=1)
    return state


PLAYERS = {
    "Ada": {"gamerTag": "Ada", "prefix": "P", "twitter": "@a",
            "custom_textbox": "c", "country_code": "JP", "mains": {}},
    "qbad": {"gamerTag": "qbad guy", "prefix": "qbadteam", "twitter": "x"},
    "Partial": {"gamerTag": "Partial", "prefix": None, "twitter": 3},
}

# (label, operation, arguments). "set" is set_commentary_data(index, data),
# "load" load_commentator_from_tag(index, tag, no_mains).
SCENARIOS = [
    ("full", "set", (1, {"gamerTag": "Ada", "prefix": "TeamA",
                         "twitter": "@ada", "custom_textbox": "note"})),
    ("partial keeps role icon display", "set", (2, {"gamerTag": "New"})),
    ("strip and non-strings", "set",
     (2, {"gamerTag": "  Spaced  ", "twitter": " @@x ", "prefix": 5,
          "custom_textbox": None})),
    ("last slot, socket payload", "set",
     ("12", {"commentator": "12", "gamerTag": "Last", "prefix": ""})),
    ("censored", "set", (3, {"gamerTag": "qbad word", "prefix": "ok",
                             "twitter": "@qbad", "custom_textbox": "qbad"})),
    ("unchanged", "set", (4, {"gamerTag": "Same"})),
    ("empty data", "set", (5, {})),
    ("not a dict", "set", (3, ["not", "a", "dict"])),
    ("above the slots", "set", (13, {"gamerTag": "X"})),
    ("below the slots", "set", (0, {"gamerTag": "X"})),
    ("not a number", "set", ("x", {"gamerTag": "X"})),
    ("load", "load", (1, "Ada", False)),
    ("load the last old slot", "load", (8, "Ada", True)),
    ("load censored", "load", (3, "qbad", False)),
    ("load non-strings", "load", (2, "Partial", False)),
    ("load unknown", "load", (2, "Nobody", False)),
]


# ── harness ──────────────────────────────────────────────────────────────

def _state():
    from src.core.StateManager import StateManager
    return StateManager


@pytest.fixture
def lineup(autonaApp):
    """The baseline line-up, restored afterwards (the commentary subtree is
    process-global)."""
    from src.core.DictHelper import deep_clone
    saved = deep_clone(_state().Get("commentary"))
    _state().Set("commentary", _baseline())
    autonaApp.settle()
    try:
        yield autonaApp
    finally:
        _state().Set("commentary", saved)
        autonaApp.settle()


def _buildPanel(app):
    from src.AutonaCommentaryWidget import AutonaCommentaryWidget
    panel = AutonaCommentaryWidget()
    app.settle()
    return panel


def _dropPanel(app, panel):
    from qtpy.QtCore import QCoreApplication, QEvent
    from src.AutonaCommentaryWidget import AutonaCommentaryWidget
    if AutonaCommentaryWidget.instance is panel:
        AutonaCommentaryWidget.instance = None
    panel.setParent(None)
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(panel, QEvent.Type.DeferredDelete)
    app.settle()


@pytest.fixture
def fakes(monkeypatch):
    """A deterministic censor and player DB, recording what was asked."""
    from src.core.BadWordFilter import BadWordFilter
    from src.core.PlayerDB import PlayerDB

    calls = {"censor": [], "tags": []}

    def censor(value, countryCode2=None):
        calls["censor"].append([value, countryCode2])
        if isinstance(value, str):
            return value.replace("qbad", "***")
        return value

    def lookup(tag):
        calls["tags"].append(tag)
        player = PLAYERS.get(tag)
        return dict(player) if player is not None else None

    monkeypatch.setattr(BadWordFilter.instance, "Censor", censor)
    monkeypatch.setattr(PlayerDB.instance, "GetPlayerFromTag", lookup)
    return calls


def _run(app, kind, args):
    """The operation through the web layer: the shared web operation, or --
    on the pre-S3e-2 tree, which has none -- the desktop's WebServerActions
    (through the panel)."""
    name = ("set_commentary_data" if kind == "set"
            else "load_commentator_from_tag")
    op = getattr(WebOps, name, None) or getattr(app.actions(), name)
    return op(*args)


def _record(app, fakes, kind, args):
    with WriteRecorder() as recorder:
        try:
            result, raises = _run(app, kind, args), None
        except Exception as error:
            result, raises = None, type(error).__name__
        app.settle()
    recorded = recorder.Result()
    recorded.update({
        "result": result,
        "raises": raises,
        "censor": [list(call) for call in fakes["censor"]],
        "commentary": encode(_state().Get("commentary")),
    })
    return recorded


@pytest.mark.skipif(not RECORD, reason="recording only")
def test_record(lineup, fakes):
    golden = {"baseline": encode(_baseline()), "scenarios": {}}
    for label, kind, args in SCENARIOS:
        _state().Set("commentary", _baseline())
        lineup.settle()
        fakes["censor"].clear()
        panel = _buildPanel(lineup)
        try:
            golden["scenarios"][label] = _record(lineup, fakes, kind, args)
        finally:
            _dropPanel(lineup, panel)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")


# ── replay: what the operations write now ────────────────────────────────
#
# The golden was recorded through the panel, before the move. The core path
# has to write the same -- with no panel, with the panel built (it renders
# and writes nothing), and on the headless board host -- except for the one
# answer S3e-2 fixed on purpose: load_commentator_from_tag answered None
# (a 500 on the legacy route, a null ack) and now answers "OK", or "ERROR"
# when the player DB has no such tag.

SCENARIO = {label: (kind, args) for label, kind, args in SCENARIOS}


def _golden():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _expected(label):
    expected = dict(_golden()["scenarios"][label])
    kind, args = SCENARIO[label]
    if kind == "load":
        assert expected["result"] is None   # what the recording answered
        expected["result"] = "OK" if args[1] in PLAYERS else "ERROR"
    return expected


def _shown(panel):
    """The line-up as the panel's cards show it."""
    shown = {}
    for slot, w in panel.casterInputs.items():
        caster = {field: w[field].text() for field in comms.TEXT_FIELDS}
        caster["icon"] = w["icon"].currentData() or ""
        caster["display"] = 1 if w["display"].isChecked() else 0
        shown[slot] = {field: caster[field] for field in comms.DefaultSlot()}
    return shown


def test_the_golden_baseline_is_the_fixture():
    assert _golden()["baseline"] == encode(_baseline())


@pytest.mark.parametrize("mode", ["no panel", "panel", "headless"])
@pytest.mark.parametrize("label", list(SCENARIO))
def test_the_operation_writes_what_the_panel_wrote(
        lineup, fakes, monkeypatch, mode, label):
    if RECORD:
        pytest.skip("recording")
    from src.AutonaCommentaryWidget import AutonaCommentaryWidget
    kind, args = SCENARIO[label]
    panel = None
    if mode == "panel":
        panel = _buildPanel(lineup)
    elif mode == "headless":
        monkeypatch.setattr(WebOps, "boardHost", WebOps.BoardHost())
    assert (AutonaCommentaryWidget.instance is not None) == (panel is not None)
    try:
        assert _record(lineup, fakes, kind, args) == _expected(label)
        if panel is not None:
            # ...and the panel shows what was written.
            assert _shown(panel) == _state().Get("commentary")
    finally:
        if panel is not None:
            _dropPanel(lineup, panel)


def test_the_load_reaches_all_twelve_slots(lineup, fakes):
    assert WebOps.load_commentator_from_tag(12, "Ada") == "OK"
    assert WebOps.load_commentator_from_tag("9", "Ada") == "OK"
    lineup.settle()
    assert lineup.get("commentary.12.name") == "Ada"
    assert lineup.get("commentary.9.name") == "Ada"
    # The role / icon / display of the slot stay.
    assert lineup.get("commentary.12.role") == "MC"
    assert lineup.get("commentary.12.display") == 1

    for index in (0, 13):
        assert WebOps.load_commentator_from_tag(index, "Ada") == (
            "ERROR : index must be 1..12")
    assert WebOps.load_commentator_from_tag(3, "Nobody") == "ERROR"


# ── the service's own operations ─────────────────────────────────────────

def test_a_field_is_written_alone_and_only_when_it_changes(lineup):
    from src.core import CommentaryService
    with WriteRecorder() as recorder:
        assert CommentaryService.SetField("2", "twitter", " @new ")
        assert not CommentaryService.SetField("2", "twitter", "new")
        assert CommentaryService.SetField(2, "icon", "phone")
        assert CommentaryService.SetField("2", "icon", "nonsense")
        assert not CommentaryService.SetField("13", "name", "X")
        assert not CommentaryService.SetField("2", "gamerTag", "X")
        lineup.settle()
    assert recorder.sets == [["commentary.2.twitter", '"new"'],
                             ["commentary.2.icon", '"phone"'],
                             ["commentary.2.icon", '""']]
    assert lineup.get("commentary.2.name") == "Old"


def test_a_fifth_caster_is_not_shown(lineup):
    from src.core import CommentaryService
    # The baseline shows 2 and 12.
    assert CommentaryService.SetField("1", "display", True)
    assert CommentaryService.SetField("3", "display", 1)
    assert not CommentaryService.SetField("5", "display", 1)
    assert lineup.get("commentary.5.display") == 0
    # Hiding one makes room; a shown caster can be "shown" again.
    assert CommentaryService.SetField("1", "display", 0)
    assert CommentaryService.SetField("5", "display", 1)
    assert not CommentaryService.SetField("5", "display", 1)


def test_a_reorder_moves_the_casters_in_one_write(lineup):
    from src.core import CommentaryService
    order = list(comms.SLOTS)
    order[0], order[1] = "2", "1"
    with WriteRecorder() as recorder:
        assert CommentaryService.Reorder(order)
        assert not CommentaryService.Reorder(order[:-1])
        assert not CommentaryService.Reorder(["1"] * len(comms.SLOTS))
        lineup.settle()
    assert [key for key, _ in recorder.sets] == ["commentary"]
    assert len(recorder.deltas) == 1
    assert lineup.get("commentary.1.name") == "Old"
    assert lineup.get("commentary.2.name") == ""
    assert list(lineup.get("commentary")) == comms.SLOTS


# ── the panel follows the state ──────────────────────────────────────────

@pytest.fixture
def panel(lineup):
    panel = _buildPanel(lineup)
    try:
        yield panel
    finally:
        _dropPanel(lineup, panel)


def test_the_panel_shows_what_core_writes_and_writes_nothing(
        lineup, fakes, panel):
    from src.core import CommentaryService
    assert _shown(panel) == lineup.get("commentary")

    CommentaryService.ApplyPlayerData("3", {"gamerTag": "Core"})
    CommentaryService.SetField("3", "display", 1)
    CommentaryService.SetField("3", "icon", "megaphone")
    with WriteRecorder() as recorder:
        lineup.settle()

    assert recorder.sets == []
    assert panel.casterInputs["3"]["name"].text() == "Core"
    assert panel.casterInputs["3"]["display"].isChecked()
    assert panel.casterInputs["3"]["icon"].currentData() == "megaphone"
    assert _shown(panel) == lineup.get("commentary")


def test_the_panel_follows_a_full_reload(lineup, panel):
    state = _baseline()
    state["6"] = _slot(name="Reloaded", display=1)
    with _state().lock:
        _state().state["commentary"] = state
    _state().observers.state_big_change.notify()
    lineup.settle()
    assert panel.casterInputs["6"]["name"].text() == "Reloaded"


def test_a_panel_edit_does_not_write_its_stale_slots_back(
        lineup, fakes, panel):
    """The panel has not shown a later change yet (its render is deferred)
    when the operator finishes an edit: the edit writes its own field, and
    the change it has not shown stays (the old full rebuild put the panel's
    copy of every slot back)."""
    from src.core import CommentaryService
    CommentaryService.ApplyPlayerData("5", {"gamerTag": "Newer"})
    assert panel.casterInputs["5"]["name"].text() == ""   # not shown yet

    edit = panel.casterInputs["1"]["name"]
    edit.setText("Typed")
    with WriteRecorder() as recorder:
        edit.editingFinished.emit()
        lineup.settle()

    assert recorder.sets == [["commentary.1.name", '"Typed"']]
    assert lineup.get("commentary.5.name") == "Newer"
    assert panel.casterInputs["5"]["name"].text() == "Newer"


def test_a_panel_toggle_past_the_limit_is_put_back(lineup, panel):
    for slot in ("1", "3"):
        panel.casterInputs[slot]["display"].setChecked(True)
    lineup.settle()
    assert [s for s in comms.SLOTS
            if lineup.get(f"commentary.{s}.display")] == ["1", "2", "3", "12"]
    box = panel.casterInputs["5"]["display"]
    assert not box.isEnabled()

    box.setChecked(True)   # what a box enabled by a stale lock would do
    lineup.settle()
    assert lineup.get("commentary.5.display") == 0
    assert not box.isChecked()


def test_a_panel_drag_moves_the_casters(lineup, panel):
    cards = panel.casterCardList
    first = panel._cards[0]["card"]
    second = panel._cards[1]["card"]
    assert cards.cards()[:2] == [first, second]
    # What a drop does: the card list's order, then its signal. Only the
    # list's own record is changed -- moving the widgets in the layout would
    # destroy layout items PySide6 still holds wrappers of (agent-pitfalls
    # #14), and the panel reads the order from cards().
    cards._cards[0], cards._cards[1] = second, first
    with WriteRecorder() as recorder:
        cards.orderChanged.emit()
        lineup.settle()

    assert [key for key, _ in recorder.sets] == ["commentary"]
    assert lineup.get("commentary.1.name") == "Old"
    assert lineup.get("commentary.2.name") == ""
    assert panel.casterInputs["1"] is panel._cardWidgets[second]
    assert panel.casterInputs["1"]["name"].text() == "Old"
    assert _shown(panel) == lineup.get("commentary")


# Real focus, on the panel shown offscreen (the player cards' tests,
# tests/test_player_card_render.py, explain why it is not simulated).

@contextmanager
def _onScreen(widget, app):
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QWidget
    window = widget.window()
    window.show()
    window.activateWindow()
    app.settle()
    try:
        yield
    finally:
        standIn = QWidget()
        standIn.show()
        standIn.activateWindow()
        app.settle()
        window.hide()
        standIn.hide()
        standIn.deleteLater()
        QCoreApplication.sendPostedEvents(standIn, QEvent.Type.DeferredDelete)
        app.settle()


def _focus(widget, app):
    widget.setFocus()
    app.settle()
    assert widget.isVisible() and widget.hasFocus(), \
        "the harness could not focus the field"


def test_a_field_being_edited_is_left_alone_and_not_written_back(
        lineup, fakes, panel):
    from src.core import CommentaryService
    edit = panel.casterInputs["2"]["name"]
    other = panel.casterInputs["2"]["note"]
    assert edit.text() == "Old"
    finished = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))

    with _onScreen(panel, lineup):
        _focus(edit, lineup)
        CommentaryService.ApplyPlayerData("2", {"gamerTag": "Web"})
        lineup.settle()
        assert edit.text() == "Old", "the field being edited was redrawn"

        with WriteRecorder() as recorder:
            _focus(other, lineup)
        assert finished == ["Old"], "Qt no longer finishes the field"
        assert recorder.sets == []
        assert lineup.get("commentary.2.name") == "Web"
        assert edit.text() == "Web"


@pytest.mark.parametrize("finish", ["enter", "focus-out"])
def test_a_field_typed_in_while_the_panel_renders_is_still_written(
        lineup, fakes, panel, finish):
    """The operator is typing in one caster's field when another caster is
    updated from the Web: the text in the field is their edit, not one a
    render left behind, and finishing it writes it."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    edit = panel.casterInputs["1"]["name"]
    other = panel.casterInputs["1"]["note"]

    with _onScreen(panel, lineup):
        _focus(edit, lineup)
        QTest.keyClicks(edit, "Bob")
        assert WebOps.set_commentary_data(3, {"gamerTag": "Web"}) == "OK"
        lineup.settle()
        assert panel.casterInputs["3"]["name"].text() == "Web"
        assert edit.text() == "Bob"
        if finish == "enter":
            QTest.keyClick(edit, Qt.Key.Key_Return)
            lineup.settle()
        _focus(other, lineup)

    assert lineup.get("commentary.1.name") == "Bob"
    assert lineup.get("commentary.3.name") == "Web"
    assert edit.text() == "Bob"


def test_a_refused_toggle_is_put_back_before_the_locks_are_counted(
        lineup, panel):
    """A tick past the limit is refused, and the box is unticked there and
    then, before the locks are counted: they are counted from what the state
    shows -- the refused box locked like the other unticked ones, its card
    not lit -- without waiting for the render. Putting it back writes
    nothing."""
    for slot in ("1", "3"):
        panel.casterInputs[slot]["display"].setChecked(True)
    lineup.settle()
    box = panel.casterInputs["5"]["display"]
    card = panel.casterInputs["5"]["card"]

    with WriteRecorder() as recorder:
        box.setChecked(True)
        assert not box.isChecked()
        assert not box.isEnabled()
        assert not card.property("onair")
        lineup.settle()
    assert recorder.sets == []
    assert lineup.get("commentary.5.display") == 0
    assert not box.isChecked() and not box.isEnabled()


def test_a_panel_icon_pick_writes_the_icon_alone(lineup, panel):
    """The icon pulldown writes its stem -- that one key, through
    CommentaryService.SetField -- and "no icon" writes ""."""
    combo = panel.casterInputs["4"]["icon"]
    with WriteRecorder() as recorder:
        combo.setCurrentIndex(combo.findData("megaphone"))
        lineup.settle()
    assert recorder.sets == [["commentary.4.icon", '"megaphone"']]
    assert lineup.get("commentary.4.icon") == "megaphone"

    with WriteRecorder() as recorder:
        combo.setCurrentIndex(0)
        lineup.settle()
    assert recorder.sets == [["commentary.4.icon", '""']]
    assert _shown(panel) == lineup.get("commentary")


def test_a_completer_pick_fills_the_caster_and_shows_its_name(
        lineup, fakes, panel, monkeypatch):
    """Picking a player from the name field's completer fills the caster
    from the player (CommentaryService.ApplyPlayerData), and the field the
    operator is still in shows the name as stored (censored here, so not
    the completer's own text): the pick is their edit. Leaving the field
    afterwards writes nothing more."""
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QStandardItem, QStandardItemModel
    from qtpy.QtTest import QTest
    from src.AutonaPlayerDB import AutonaPlayerDB
    # The harness loads no player DB, so there is no completer model: one
    # with a single player, which the panel binds as it binds the DB's on
    # db_updated (not emitted: it is process-wide).
    model = QStandardItemModel()
    item = QStandardItem("qbad guy")
    item.setData(dict(PLAYERS["qbad"]), Qt.ItemDataRole.UserRole)
    model.appendRow(item)
    monkeypatch.setattr(AutonaPlayerDB, "model", model)
    monkeypatch.setattr(AutonaPlayerDB, "completerModel", model)
    panel._rebindCompleters()
    w = panel.casterInputs["1"]
    edit = w["name"]
    completer = edit.completer()

    with _onScreen(panel, lineup):
        _focus(edit, lineup)
        QTest.keyClicks(edit, "qb")
        popup = completer.popup()
        assert popup.isVisible(), "the completer offered nothing"
        QTest.keyClick(popup, Qt.Key.Key_Down)
        QTest.keyClick(popup, Qt.Key.Key_Return)
        lineup.settle()
        assert edit.hasFocus()
        assert edit.text() == "*** guy"
        assert lineup.get("commentary.1.name") == "*** guy"
        assert lineup.get("commentary.1.team") == "***team"
        assert lineup.get("commentary.1.twitter") == "x"

        # The closing popup took the (offscreen) window's activation with
        # it; a click on the next field would give it back.
        panel.window().activateWindow()
        lineup.settle()
        with WriteRecorder() as recorder:
            _focus(w["note"], lineup)
        assert recorder.sets == []

    assert edit.text() == "*** guy"
    assert _shown(panel) == lineup.get("commentary")
