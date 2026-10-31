"""The caster line-up's operations (core/CommentaryService.py, ADR-005 S3e-2).

golden.json was recorded through the (since removed, ADR-005 S4c) Qt panel
on the pre-S3e-2 tree; the core path has to keep writing the same. The way
to re-record the golden went with the panel (S4c): when the output is meant
to change, edit golden.json by hand.
"""
import json
from pathlib import Path

import pytest

from src import AutonaWebOperations as WebOps
from src.core import CommentaryModel as comms
from tests.fixtures.write_recording import WriteRecorder, encode

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


# ── replay: what the operations write now ────────────────────────────────
#
# The golden was recorded through the Qt panel, before the move. The core
# path has to write the same -- on the desktop and on the headless board
# host -- except for the one
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


def test_the_golden_baseline_is_the_fixture():
    assert _golden()["baseline"] == encode(_baseline())


@pytest.mark.parametrize("mode", ["default", "headless"])
@pytest.mark.parametrize("label", list(SCENARIO))
def test_the_operation_writes_what_the_panel_wrote(
        lineup, fakes, monkeypatch, mode, label):
    kind, args = SCENARIO[label]
    if mode == "headless":
        monkeypatch.setattr(WebOps, "boardHost", WebOps.BoardHost())
    assert _record(lineup, fakes, kind, args) == _expected(label)


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
