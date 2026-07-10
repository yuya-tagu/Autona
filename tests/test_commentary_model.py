"""Unit tests for the commentary panel model (pure logic, no Qt)."""
from tests.conftest import direct_import

model = direct_import("AutonaCommentaryModel", "src/AutonaCommentaryModel.py")


def test_default_state_has_all_twelve_slots():
    state = model.DefaultState()
    assert sorted(state.keys(), key=int) == [str(i) for i in range(1, 13)]
    for slot in state.values():
        assert slot == {
            "name": "", "team": "", "twitter": "",
            "role": "", "icon": "", "note": "", "display": 0,
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
    assert slot["twitter"] == "taro"
    assert slot["note"] == "本年度ランキング2位"
    assert slot["role"] == ""
    assert slot["icon"] == ""
    assert slot["display"] == 0
    for legacy_key in ("real_name", "seed", "country_code", "state_code",
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
    # Legacy check-order values (1..4) collapse to the shown flag.
    assert model.NormalizeSlot({"display": 3})["display"] == 1
    assert model.NormalizeSlot({"display": True})["display"] == 1   # legacy
    assert model.NormalizeSlot({"display": False})["display"] == 0
    assert model.NormalizeSlot({"display": -2})["display"] == 0


def test_normalize_slot_keeps_role_free_text():
    # Free text: candidates in ROLES are not a whitelist.
    assert model.NormalizeSlot({"role": "実況"})["role"] == "実況"
    assert model.NormalizeSlot({"role": "DJ"})["role"] == "DJ"
    assert model.NormalizeSlot({"role": 123})["role"] == ""


def test_normalize_slot_coerces_icon_stem():
    assert model.NormalizeSlot({"icon": "mic"})["icon"] == "mic"
    assert model.NormalizeSlot({"icon": "dj"})["icon"] == ""
    assert model.NormalizeSlot({"icon": 5})["icon"] == ""


def test_normalize_slot_migrates_icon_label_role_to_icon():
    # Legacy free-text shape: an icon label typed as the role.
    slot = model.NormalizeSlot({"role": "マイク"})
    assert slot["role"] == ""
    assert slot["icon"] == "mic"
    # An explicit icon wins; the label then stays as role text.
    slot = model.NormalizeSlot({"role": "電話", "icon": "mic"})
    assert slot["role"] == "電話"
    assert slot["icon"] == "mic"


def test_normalize_slot_migrates_legacy_roles_list():
    # Legacy multi-select shape: text roles joined, icon labels moved
    # to the icon field (first one wins).
    slot = model.NormalizeSlot({"roles": ["実況", "マイク", "電話"]})
    assert slot["role"] == "実況"
    assert slot["icon"] == "mic"
    assert "roles" not in slot
    slot = model.NormalizeSlot({"roles": ["実況", "MC"]})
    assert slot["role"] == "実況・MC"
    # A present "role" str wins over the legacy list.
    slot = model.NormalizeSlot({"role": "MC", "roles": ["解説"]})
    assert slot["role"] == "MC"
    # Non-str junk inside the list is dropped.
    assert model.NormalizeSlot({"roles": ["解説", 5, ""]})["role"] == "解説"


def test_icon_labels_are_not_role_candidates():
    assert not set(model.ICONS.values()) & set(model.ROLES)


def test_normalize_state_drops_out_of_range_keys():
    state = model.NormalizeState({
        "1": {"name": "A"},
        "13": {"name": "OUT"},
        "0": {"name": "OUT"},
        "foo": {"name": "OUT"},
    })
    assert sorted(state.keys(), key=int) == [str(i) for i in range(1, 13)]
    assert state["1"]["name"] == "A"
    assert all(state[s]["name"] == "" for s in state if s != "1")


def test_normalize_state_collapses_legacy_orders_to_flags():
    state = model.NormalizeState({
        "2": {"name": "B", "display": 7},
        "5": {"name": "E", "display": 3},
    })
    assert state["2"]["display"] == 1
    assert state["5"]["display"] == 1


def test_normalize_state_caps_display_at_four_by_slot_order():
    source = {str(i): {"name": f"P{i}", "display": 1} for i in range(1, 7)}
    state = model.NormalizeState(source)
    assert [state[str(i)]["display"] for i in range(1, 7)] == \
        [1, 1, 1, 1, 0, 0]


def test_normalize_state_migrates_legacy_bools():
    source = {str(i): {"name": f"P{i}", "display": True}
              for i in (6, 2, 4)}
    state = model.NormalizeState(source)
    assert state["2"]["display"] == 1
    assert state["4"]["display"] == 1
    assert state["6"]["display"] == 1


def test_displayed_slots_follow_slot_order():
    state = model.DefaultState()
    state["5"].update(name="E", display=1)
    state["2"].update(name="B", display=1)
    assert model.DisplayedSlots(state) == ["2", "5"]


def test_displayed_slots_skips_unnamed_and_caps_at_four():
    state = model.DefaultState()
    for slot in ("1", "2", "3", "4", "5", "6"):
        state[slot].update(name=f"P{slot}", display=1)
    state["2"]["name"] = "   "          # blank name -> skipped
    assert model.DisplayedSlots(state) == ["1", "3", "4", "5"]


def test_displayed_slots_tolerates_garbage_state():
    assert model.DisplayedSlots(None) == []
    assert model.DisplayedSlots({"1": "junk", "2": None}) == []
    assert model.DisplayedSlots(model.DefaultState()) == []
