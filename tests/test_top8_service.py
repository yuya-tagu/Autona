"""Contract tests for src/core/Top8Service.py (headless core Step 5-5).

The TOP 8 panel owns a QWebEngineView, so it is one of the widgets the test
harness deliberately never builds -- which is why the *decisions* it used to
make on its way to `StateManager.Set` had no coverage at all: merging staged
spin values into the published matches, calling a non-tie "finished",
re-running Recompute, and falling back to "TOP 8" for an empty caption.
`tests/test_top8_model.py` covered the topology underneath them; this covers
the layer that was previously inside the widget, with **no QApplication**.

Isolation follows tests/test_actions.py.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core import Top8Model as top8
from src.core import StateSchema as schema
from src.core import Top8Service
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_top8_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(schema.TOP8_ROOT, None)
        SM.lastSavedState.pop(schema.TOP8_ROOT, None)
        SM.changedKeys = []
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
        os.chdir(_ORIGINAL_CWD)


def _settle():
    if SM._export_thread is not None:
        SM._export_thread.join()


def _allTies():
    return {matchId: [0, 0] for matchId in top8.MATCH_IDS}


def _seed():
    """The state a launch leaves behind, as StateSchema.SeedPanelState does.

    Deliberately including the Recompute: an empty ``top8.matches`` is not a
    state the panel is ever handed, and the difference matters -- see
    ``test_the_first_apply_on_an_unrecomputed_seed_drops_the_scores`` for
    what happens when it is.
    """
    SM.Set(schema.TOP8_ROOT, top8.DefaultState())
    SM.Set(Top8Service.MATCHES_PATH,
           top8.Recompute(top8.DefaultState()["matches"]))
    _settle()


# ── key paths ────────────────────────────────────────────────────────────

def test_the_paths_are_built_from_the_schema():
    assert Top8Service.PLAYERS_PATH == "top8.players"
    assert Top8Service.MATCHES_PATH == "top8.matches"
    assert Top8Service.PHASE_LABEL_PATH == "top8.phaseLabel"
    assert schema.TOP8_PHASE_LABEL_KEY in schema.TOP8_KEYS


# ── players ──────────────────────────────────────────────────────────────

def _seedPlayers():
    SM.Set(Top8Service.PLAYERS_PATH,
           {slot: top8.DefaultPlayer() for slot in top8.PLAYER_SLOTS})
    _settle()


def _setsDuring(action):
    """The keys StateManager.Set was called with while `action` ran."""
    from unittest.mock import patch
    keys = []
    real = SM.Set

    def spy(key, value=None, *args, **kwargs):
        keys.append(key)
        return real(key, value, *args, **kwargs)

    with patch.object(SM, "Set", spy):
        result = action()
    _settle()
    return keys, result


def test_a_text_edit_writes_only_that_field_trimmed():
    # Per field since ADR-005 S3e-8: the panel used to republish all eight
    # players from its inputs on every edit.
    _seedPlayers()
    SM.Set("top8.players.2.name", "Bob")
    _settle()
    before = SM.revision

    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerText("1", "name", "  Alice  "))

    assert written is True
    assert keys == ["top8.players.1.name"]
    assert SM.Get("top8.players.1.name") == "Alice"
    assert SM.Get("top8.players.2.name") == "Bob"
    assert SM.revision == before + 1


def test_an_unchanged_field_is_not_written():
    _seedPlayers()
    SM.Set("top8.players.1.sponsor", "AA")
    _settle()
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerText(1, "sponsor", " AA"))
    assert written is False and keys == []


def test_an_unknown_slot_or_field_writes_nothing():
    _seedPlayers()
    keys, _ = _setsDuring(lambda: (
        Top8Service.SetPlayerText("9", "name", "X"),
        Top8Service.SetPlayerText("1", "country", "X"),
        Top8Service.SetPlayerCountry("0", {"code": "JP"}),
        Top8Service.SetPlayerCharacter("x", {"en_name": "Mario"})))
    assert keys == []


def test_a_country_edit_writes_only_that_slots_country():
    _seedPlayers()
    country = {"name": "Japan", "code": "JP"}
    keys, written = _setsDuring(
        lambda: Top8Service.SetPlayerCountry("3", country))
    assert written is True
    assert keys == ["top8.players.3.country"]
    assert SM.Get("top8.players.3.country") == country
    keys, _ = _setsDuring(lambda: Top8Service.SetPlayerCountry("3", None))
    assert SM.Get("top8.players.3.country") == {}


def test_a_character_is_stored_as_the_first_character_slot():
    _seedPlayers()
    char = {"en_name": "Mario", "assets": {}}
    keys, _ = _setsDuring(lambda: Top8Service.SetPlayerCharacter("8", char))
    assert keys == ["top8.players.8.characters"]
    assert SM.Get("top8.players.8.characters") == {"1": char}
    Top8Service.SetPlayerCharacter("8", {})
    assert SM.Get("top8.players.8.characters") == {}


def test_forgetting_the_characters_writes_only_slots_that_have_one():
    """A game switch: every character goes (the skin with it), in one save
    cycle, and a slot without one is not written (the smallest delta)."""
    _seedPlayers()
    mario = {"en_name": "Mario", "assets": {"icon": "mario_2.png"}}
    Top8Service.SetPlayerCharacter("2", mario)
    Top8Service.SetPlayerCharacter("7", {"en_name": "Luigi"})
    Top8Service.SetPlayerText("2", "name", "Alice")
    _settle()
    before = SM.revision

    keys, slots = _setsDuring(Top8Service.ForgetCharacters)

    assert slots == ["2", "7"]
    assert keys == ["top8.players.2.characters", "top8.players.7.characters"]
    assert SM.revision == before + 1
    assert all(SM.Get(f"top8.players.{slot}.characters") == {}
               for slot in top8.PLAYER_SLOTS)
    assert SM.Get("top8.players.2.name") == "Alice"

    # Nothing left to forget: no write, no cycle.
    keys, slots = _setsDuring(Top8Service.ForgetCharacters)
    assert (keys, slots) == ([], [])
    assert SM.revision == before + 1


def test_forgetting_the_characters_of_a_missing_bracket_writes_nothing():
    keys, slots = _setsDuring(Top8Service.ForgetCharacters)
    assert (keys, slots) == ([], [])
    assert SM.Get(Top8Service.PLAYERS_PATH) is None


# ── the phase caption ────────────────────────────────────────────────────

def test_an_empty_caption_falls_back_to_the_default():
    assert Top8Service.SetPhaseLabel("  ") == top8.DEFAULT_PHASE_LABEL
    assert SM.Get(Top8Service.PHASE_LABEL_PATH) == top8.DEFAULT_PHASE_LABEL
    assert Top8Service.SetPhaseLabel(None) == top8.DEFAULT_PHASE_LABEL


def test_a_caption_is_stored_trimmed():
    assert Top8Service.SetPhaseLabel("  FINALS  ") == "FINALS"
    assert SM.Get(Top8Service.PHASE_LABEL_PATH) == "FINALS"


# ── 反映 (apply the staged scores) ───────────────────────────────────────

def test_applying_a_non_tie_marks_the_match_finished_and_propagates():
    _seed()
    scores = _allTies()
    scores["wsf1"] = [2, 1]

    matches = Top8Service.ApplyScores(scores)

    assert matches["wsf1"]["score"] == [2, 1]
    assert matches["wsf1"]["finished"] is True
    # Player slot 1 (wsf1's first entrant) won, so it reaches the winners
    # final; the loser crosses to LQF2 (the FGC anti-rematch progression).
    assert matches["wf"]["slots"][0] == 1
    assert matches["lqf2"]["slots"][1] == 2
    assert SM.Get("top8.matches.wsf1.finished") is True


def test_a_tie_is_not_a_result():
    _seed()
    matches = Top8Service.ApplyScores({**_allTies(), "wsf1": [1, 1]})
    assert matches["wsf1"]["finished"] is False
    assert matches["wf"]["slots"] == [top8.TBD, top8.TBD]


def test_applying_scores_keeps_the_published_round_names():
    _seed()
    SM.Set("top8.matches.wsf1.name", "Winners Semi (custom)")

    matches = Top8Service.ApplyScores({**_allTies(), "wsf1": [3, 0]})

    assert matches["wsf1"]["name"] == "Winners Semi (custom)"


def test_applying_scores_is_one_save_cycle():
    """One delta for the whole bracket.

    Recompute can rewrite every downstream match, so publishing per match
    would put a half-propagated bracket on the overlay.
    """
    _seed()
    before = SM.revision

    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0], "wsf2": [2, 1]})

    assert SM.revision == before + 1


def test_the_first_apply_on_a_fresh_install_seed_keeps_the_scores():
    """Regression for known-smells S-18.

    ``top8.DefaultState()`` hands out matches whose ``slots`` are all TBD.
    ``SeedPanelState`` used to write it verbatim on a first launch, so
    Recompute read the first 反映 as "the participants changed" and cleared
    the score. The seed is now recomputed, so the first apply sticks.
    """
    SM.Set(schema.TOP8_ROOT, None)
    schema.SeedPanelState()
    _settle()
    assert SM.Get("top8.matches.wsf1.slots") == [1, 2]

    first = Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 1]})
    assert first["wsf1"]["score"] == [2, 1]


def test_the_service_never_mutates_the_live_matches_dict():
    """StateManager.Get returns a live reference.

    Mutating it in place is the failure mode the panel's comment warned
    about: DeepDiff would see no change and the delta would never be sent.
    """
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0]})
    live = SM.Get(Top8Service.MATCHES_PATH)

    Top8Service.ApplyScores({**_allTies(), "wsf1": [1, 2]})

    assert live is not SM.Get(Top8Service.MATCHES_PATH)


# ── 全リセット ───────────────────────────────────────────────────────────

def test_resetting_clears_every_score_and_flag():
    _seed()
    Top8Service.ApplyScores({**_allTies(), "wsf1": [2, 0], "wsf2": [2, 1]})

    matches = Top8Service.ResetMatches()

    assert set(matches) == set(top8.MATCH_IDS)
    for matchId, match in matches.items():
        assert match["score"] == [0, 0]
        assert match["finished"] is False
    # The starting slots survive a reset -- they are topology, not a result.
    assert matches["wsf1"]["slots"] == [1, 2]
    assert matches["wf"]["slots"] == [top8.TBD, top8.TBD]
    assert SM.Get("top8.matches.wsf1.score") == [0, 0]


# ── Start.gg import ──────────────────────────────────────────────────────

def test_importing_a_bracket_publishes_both_subtrees_in_one_cycle():
    _settle()
    before = SM.revision
    players = {slot: top8.DefaultPlayer() for slot in top8.PLAYER_SLOTS}
    players["1"]["name"] = "Alice"
    # FromBracket hands back matches whose slots are already derived (its
    # docstring says so), which is what lets an imported result survive the
    # Recompute below -- see the unrecomputed-seed test above for the other
    # case.
    matches = top8.Recompute(top8.DefaultState()["matches"])
    matches["wsf1"]["score"] = [2, 0]
    matches["wsf1"]["finished"] = True

    result = Top8Service.ImportBracket(players, matches)

    assert SM.revision == before + 1
    assert SM.Get("top8.players.1.name") == "Alice"
    # Recompute ran on the way in, so the imported result is already
    # propagated rather than waiting for the first edit.
    assert result["wf"]["slots"][0] == 1
    assert SM.Get("top8.matches.wf.slots")[0] == 1
