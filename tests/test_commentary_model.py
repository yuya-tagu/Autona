"""Unit tests for the commentary panel model (pure logic, no Qt)."""
from tests.conftest import direct_import

model = direct_import("AutonaCommentaryModel", "src/AutonaCommentaryModel.py")


def test_default_state_has_all_eight_slots():
    state = model.DefaultState()
    assert sorted(state.keys(), key=int) == [str(i) for i in range(1, 9)]
    for slot in state.values():
        assert slot == {
            "name": "", "team": "", "real_name": "", "twitter": "",
            "role": "", "note": "", "display": 0,
        }


def test_normalize_slot_migrates_legacy_fields():
    legacy = {
        "name": "Taro", "team": "HW", "real_name": "Taro Yamada",
        "twitter": "taro", "custom_textbox": "本年度ランキング2位",
        "seed": 3, "country_code": "JP", "state_code": "JP-13",
        "character": {"1": {"name": "Ryu"}}, "mains": {"1": {}},
    }
    slot = model.NormalizeSlot(legacy)
    assert slot["name"] == "Taro"
    assert slot["team"] == "HW"
    assert slot["real_name"] == "Taro Yamada"
    assert slot["twitter"] == "taro"
    assert slot["note"] == "本年度ランキング2位"
    assert slot["role"] == ""
    assert slot["display"] == 0
    for legacy_key in ("seed", "country_code", "state_code",
                       "character", "mains", "custom_textbox"):
        assert legacy_key not in slot


def test_normalize_slot_prefers_new_note_over_legacy():
    slot = model.NormalizeSlot({"note": "new", "custom_textbox": "old"})
    assert slot["note"] == "new"


def test_normalize_slot_rejects_garbage():
    assert model.NormalizeSlot(None) == model.DefaultSlot()
    assert model.NormalizeSlot("string") == model.DefaultSlot()
    slot = model.NormalizeSlot({"name": 123, "display": "yes"})
    assert slot["name"] == ""       # non-str ignored
    assert slot["display"] == 0     # non-numeric display ignored


def test_normalize_slot_coerces_display_values():
    assert model.NormalizeSlot({"display": 3})["display"] == 3
    assert model.NormalizeSlot({"display": True})["display"] == 1   # legacy
    assert model.NormalizeSlot({"display": False})["display"] == 0
    assert model.NormalizeSlot({"display": -2})["display"] == 0


def test_normalize_slot_coerces_unknown_role():
    assert model.NormalizeSlot({"role": "実況"})["role"] == "実況"
    assert model.NormalizeSlot({"role": "DJ"})["role"] == ""


def test_normalize_state_drops_out_of_range_keys():
    state = model.NormalizeState({
        "1": {"name": "A"},
        "9": {"name": "OUT"},
        "0": {"name": "OUT"},
        "foo": {"name": "OUT"},
    })
    assert sorted(state.keys(), key=int) == [str(i) for i in range(1, 9)]
    assert state["1"]["name"] == "A"
    assert all(state[s]["name"] == "" for s in state if s != "1")


def test_normalize_state_renumbers_orders_contiguously():
    state = model.NormalizeState({
        "2": {"name": "B", "display": 7},
        "5": {"name": "E", "display": 3},
    })
    assert state["5"]["display"] == 1   # lower stored order wins
    assert state["2"]["display"] == 2


def test_normalize_state_caps_display_at_four():
    source = {str(i): {"name": f"P{i}", "display": i} for i in range(1, 7)}
    state = model.NormalizeState(source)
    assert [state[str(i)]["display"] for i in range(1, 7)] == \
        [1, 2, 3, 4, 0, 0]


def test_normalize_state_migrates_legacy_bools_by_slot_order():
    source = {str(i): {"name": f"P{i}", "display": True}
              for i in (6, 2, 4)}
    state = model.NormalizeState(source)
    assert state["2"]["display"] == 1
    assert state["4"]["display"] == 2
    assert state["6"]["display"] == 3


def test_displayed_slots_follow_check_order_not_slot_order():
    state = model.DefaultState()
    state["5"].update(name="E", display=1)   # checked first
    state["2"].update(name="B", display=2)   # checked second
    assert model.DisplayedSlots(state) == ["5", "2"]


def test_displayed_slots_skips_unnamed_and_caps_at_four():
    state = model.DefaultState()
    for order, slot in enumerate(("1", "2", "3", "4", "5", "6"), start=1):
        state[slot].update(name=f"P{slot}", display=order)
    state["2"]["name"] = "   "          # blank name -> skipped
    assert model.DisplayedSlots(state) == ["1", "3", "4", "5"]


def test_displayed_slots_tolerates_garbage_state():
    assert model.DisplayedSlots(None) == []
    assert model.DisplayedSlots({"1": "junk", "2": None}) == []
    assert model.DisplayedSlots(model.DefaultState()) == []
