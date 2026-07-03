"""Unit tests for the Top 8 double elimination model (pure logic, no Qt)."""
import types

from tests.conftest import direct_import

model = direct_import("AutonaTop8Model", "src/AutonaTop8Model.py")


def finish(matches, match_id, score):
    matches[match_id]["score"] = list(score)
    matches[match_id]["finished"] = True
    return model.Recompute(matches)


def fresh():
    return model.Recompute(model.DefaultState()["matches"])


def test_initial_slots():
    matches = fresh()
    assert matches["wsf1"]["slots"] == [1, 2]
    assert matches["wsf2"]["slots"] == [3, 4]
    assert matches["lr1a"]["slots"] == [5, 6]
    assert matches["lr1b"]["slots"] == [7, 8]
    for match_id in ("wf", "lqf1", "lqf2", "lsf", "lf", "gf", "gf_reset"):
        assert matches[match_id]["slots"] == [0, 0]
    assert matches["gf_reset"]["active"] is False


def test_winner_advances_to_wf():
    matches = finish(fresh(), "wsf1", (3, 1))
    assert matches["wf"]["slots"] == [1, 0]


def test_cross_progression_wsf_losers():
    matches = fresh()
    matches = finish(matches, "wsf1", (3, 1))   # loser: slot 2
    matches = finish(matches, "wsf2", (0, 3))   # loser: slot 3
    # FGC cross: WSF1 loser -> LQF2 bottom, WSF2 loser -> LQF1 bottom
    assert matches["lqf2"]["slots"][1] == 2
    assert matches["lqf1"]["slots"][1] == 3


def test_unfinished_or_tied_match_does_not_advance():
    matches = fresh()
    matches["wsf1"]["score"] = [3, 1]           # not finished
    matches = model.Recompute(matches)
    assert matches["wf"]["slots"] == [0, 0]

    matches = finish(matches, "wsf2", (2, 2))   # finished but tied
    assert matches["wf"]["slots"] == [0, 0]


def run_full_bracket():
    """Slot 1 wins everything from winners side; slot 5 crawls out of
    losers."""
    matches = fresh()
    matches = finish(matches, "wsf1", (3, 0))   # 1 > 2
    matches = finish(matches, "wsf2", (3, 1))   # 3 > 4
    matches = finish(matches, "lr1a", (3, 2))   # 5 > 6
    matches = finish(matches, "lr1b", (1, 3))   # 8 > 7
    assert matches["wf"]["slots"] == [1, 3]
    assert matches["lqf1"]["slots"] == [5, 4]   # LR1A winner vs WSF2 loser
    assert matches["lqf2"]["slots"] == [8, 2]   # LR1B winner vs WSF1 loser
    matches = finish(matches, "wf", (3, 2))     # 1 > 3, 3 falls to LF
    matches = finish(matches, "lqf1", (3, 0))   # 5 > 4
    matches = finish(matches, "lqf2", (3, 1))   # 8 > 2
    assert matches["lsf"]["slots"] == [5, 8]
    matches = finish(matches, "lsf", (3, 1))    # 5 > 8
    assert matches["lf"]["slots"] == [5, 3]
    matches = finish(matches, "lf", (3, 2))     # 5 > 3
    assert matches["gf"]["slots"] == [1, 5]
    return matches


def test_full_bracket_flow():
    run_full_bracket()


def test_grand_final_won_from_winners_side_no_reset():
    matches = run_full_bracket()
    matches = finish(matches, "gf", (3, 1))     # winners side (slot 1) wins
    assert matches["gf_reset"]["active"] is False
    assert matches["gf_reset"]["slots"] == [0, 0]
    assert model.Champion(matches) == 1


def test_grand_final_reset():
    matches = run_full_bracket()
    matches = finish(matches, "gf", (2, 3))     # losers side (slot 5) wins
    assert matches["gf_reset"]["active"] is True
    assert matches["gf_reset"]["slots"] == [1, 5]
    assert model.Champion(matches) == 0         # reset not played yet

    matches = finish(matches, "gf_reset", (1, 3))
    assert model.Champion(matches) == 5


def test_changed_participants_reset_downstream_score():
    matches = fresh()
    matches = finish(matches, "wsf1", (3, 0))
    matches = finish(matches, "wsf2", (3, 0))
    matches = finish(matches, "wf", (3, 1))     # 1 beats 3
    assert matches["wf"]["score"] == [3, 1]

    # Reverting WSF1 changes WF participants -> WF result must be wiped.
    matches["wsf1"]["finished"] = False
    matches = model.Recompute(matches)
    assert matches["wf"]["slots"] == [0, 3]
    assert matches["wf"]["score"] == [0, 0]
    assert matches["wf"]["finished"] is False


def test_recompute_preserves_custom_round_name():
    matches = fresh()
    matches["gf"]["name"] = "GRAND FINALS"
    matches = model.Recompute(matches)
    assert matches["gf"]["name"] == "GRAND FINALS"


# ── FromBracket (Start.gg extraction) ────────────────────────────────

def _bs(pids, score=(0, 0), finished=False):
    """Duck-typed stand-in for AutonaBracket.BracketSet."""
    return types.SimpleNamespace(
        playerIds=list(pids), score=list(score), finished=finished,
        winNext=None, loseNext=None)


def _fake_top8_bracket():
    """Standard Start.gg top-8 phase group, GF not yet played (no reset
    round). Start.gg's loser routing is intentionally the opposite of our
    fixed topology (WSF1 loser -> the LQF fed by LR1A) to exercise the
    wiring-based alignment, and one LQF set stores its score in reversed
    slot order to exercise score re-orientation."""
    wsf1 = _bs([1, 4], (3, 1), True)   # seed 1 > seed 4
    wsf2 = _bs([2, 3], (3, 2), True)   # seed 2 > seed 3
    wf = _bs([1, 2])
    gf = _bs([-2, -2])
    lrA = _bs([5, 8], (3, 0), True)    # seed 5 > seed 8
    lrB = _bs([6, 7], (1, 3), True)    # seed 7 > seed 6
    # Start.gg orientation: WSF loser sits at index 0 here (ours puts the
    # LR1 winner first), so the score below must be flipped on import.
    lqfA = _bs([4, 5], (1, 3), True)   # seed 5 beats seed 4
    lqfB = _bs([3, 7])
    lsf = _bs([5, -2])
    lf = _bs([-2, -2])

    wsf1.loseNext = lqfA
    wsf2.loseNext = lqfB
    lrA.winNext = lqfA
    lrB.winNext = lqfB
    lqfA.winNext = lsf
    lqfB.winNext = lsf

    rounds = {
        "1": [wsf1, wsf2],
        "2": [wf],
        "3": [gf],
        "-1": [lrA, lrB],
        "-2": [lqfA, lqfB],
        "-3": [lsf],
        "-4": [lf],
    }
    return types.SimpleNamespace(rounds=rounds)


def _fake_entrants(count=8):
    return [
        {"players": [{
            "gamerTag": f"P{seed}",
            "prefix": f"S{seed}",
            "country_code": "JP",
            "mains": {},
        }]}
        for seed in range(1, count + 1)
    ]


def test_from_bracket_players_and_wiring():
    result = model.FromBracket(_fake_top8_bracket(), _fake_entrants())
    assert result is not None
    players, matches = result

    # WSF1 -> slots 1/2, WSF2 -> 3/4.
    assert players["1"]["name"] == "P1"
    assert players["4"]["name"] == "P3"
    assert players["1"]["sponsor"] == "S1"
    assert players["1"]["country_code"] == "JP"
    # Our lr1a feeds our lqf1 (= the LQF that receives WSF2's loser = lqfB
    # = fed by lrB), so slots 5/6 come from lrB's players (seeds 6/7).
    assert players["5"]["name"] == "P6"
    assert players["7"]["name"] == "P5"

    # WSF scores imported as-is.
    assert matches["wsf1"]["score"] == [3, 1]
    assert matches["wsf1"]["finished"] is True
    # lqfA (WSF1 loser vs lrA winner) maps to our lqf2 with slots
    # [lr1b winner, wsf1 loser] = [P5's slot, P4's slot] — reversed from
    # Start.gg's orientation, so the score must be flipped.
    assert matches["lqf2"]["slots"] == [7, 2]
    assert matches["lqf2"]["score"] == [3, 1]
    # Propagation: lqf2 winner (slot 7 = P5) reached LSF.
    assert matches["lsf"]["slots"][1] == 7


def test_from_bracket_rejects_progression_pool():
    bracket = _fake_top8_bracket()
    # Replace the GF round with a 2-set round: no single grand final above
    # the WSF-shaped round -> the top two-set round moves, counts break.
    bracket.rounds["3"] = [_bs([-2, -2]), _bs([-2, -2])]
    assert model.FromBracket(bracket, _fake_entrants()) is None
