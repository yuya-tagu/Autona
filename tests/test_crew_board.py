"""What the crew battle board (score.3) writes, against the real widget.

Board 3 is the one scoreboard that had no behavioural coverage at all: it
carries neither a Start.gg pull nor a character UI, so none of the set-load
and player suites reach it, and the handful of assertions that mention it
elsewhere (tests/test_scoreboard_operations.py) exist to prove the *generic*
web actions do not crash on it rather than to describe what it does.

That gap is why the crew half of ``ChangeSetData`` was deliberately left out
of headless core Step 4b: its normalisation rules differ from boards 1 and 2
in four places (see the ``ChangeSetData`` section below) and a "harmless"
unification would have been a silent behaviour change nobody could have
detected. These tests are written against the pre-migration widget so the
crew rules are pinned *first* and the move is provably behaviour preserving.

The assertions read StateManager rather than the widgets, for the same
reason the scoreboard suite does: StateManager is what the OBS crew overlay
and the Web UI consume.
"""
from unittest.mock import patch

import pytest

from src.AutonaCrewBattleWidget import (
    DEFAULT_COLORS, DEFAULT_MEMBER_NUM, DEFAULT_STOCK, MAX_MEMBERS)

BOARD = 3
PREFIX = f"score.{BOARD}"


# ── helpers ──────────────────────────────────────────────────────────────

def _key(*parts):
    return ".".join([PREFIX, *(str(part) for part in parts)])


def _get(app, *parts):
    return app.get(_key(*parts))


def _row(board, side, member):
    """One member row. `side` is 0-based (team 1 = 0), `member` 1-based."""
    return board.memberRows[side][member - 1]


def _nameMember(board, side, member, name):
    """Type a member name the way an operator does (edit + focus out)."""
    edit = _row(board, side, member)["name"]
    edit.setText(name)
    edit.editingFinished.emit()


def _typeTeamName(board, side, name):
    edit = board.teamNameEdits[side]
    edit.setText(name)
    edit.editingFinished.emit()


def _typeCombo(combo, text):
    combo.setCurrentText(text)
    combo.lineEdit().editingFinished.emit()


class _SetSpy:
    """Records every StateManager.Set key path made while it is active.

    Board 3 is not a held subtree, so unlike the 1v1 / team boards there is
    no held_state_updated to count save cycles with -- but the *keys* are
    exactly what a migration must not change, and a spy sees the writes that
    land on an unchanged value too (those produce no delta and no revision
    bump, so a revision check would miss a duplicated writer).
    """

    def __init__(self):
        self.keys = []

    def __enter__(self):
        from src.core import StateManager as module

        real = module.StateManager.Set

        def spy(key, value=None, *args, **kwargs):
            self.keys.append(key)
            return real(key, value, *args, **kwargs)

        self._patcher = patch.object(module.StateManager, "Set", spy)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False

    def under(self, *parts):
        """The recorded keys under one sub-path, in order."""
        prefix = _key(*parts) + "."
        return [key for key in self.keys if key.startswith(prefix)]


# ── construction ─────────────────────────────────────────────────────────

def test_a_fresh_crew_board_seeds_its_whole_subtree(autonaApp):
    """The OBS crew overlay reads every one of these unconditionally.

    SeedBoard(3) writes only the subtree root (unlike boards 1/2 it has no
    set history and no station queue); everything below is the board's own
    launch write.
    """
    autonaApp.board(BOARD)
    autonaApp.settle()

    assert _get(autonaApp, "phase") == ""
    assert _get(autonaApp, "match") == ""
    assert _get(autonaApp, "best_of") == 0

    for team in (1, 2):
        assert _get(autonaApp, "team", team, "teamName") == ""
        assert _get(autonaApp, "team", team, "color") == DEFAULT_COLORS[team - 1]
        assert _get(autonaApp, "team", team, "memberNum") == DEFAULT_MEMBER_NUM
        assert _get(autonaApp, "team", team, "activeMember") == "0"
        assert _get(autonaApp, "team", team, "score") == 0
        for member in range(1, MAX_MEMBERS + 1):
            assert _get(autonaApp, "team", team, "player", member, "name") == ""
            assert _get(
                autonaApp, "team", team, "player", member,
                "stock") == DEFAULT_STOCK


def test_a_fresh_crew_board_has_no_pool_key(autonaApp):
    """Asymmetry, pinned rather than fixed.

    The launch write seeds `phase` and `match` but not `pool`, even though
    the board has all three combos and `ChangeSetData` can write all three.
    Boards 1/2 do not seed any of them either (their combos publish on the
    first edit), so nothing depends on the missing key today -- but a
    migration that quietly adds it would change what a fresh
    program_state.json contains.
    """
    autonaApp.board(BOARD)
    autonaApp.settle()

    assert _get(autonaApp, "pool") is None


def test_the_crew_board_writes_no_key_outside_its_own_subtree(autonaApp):
    """Everything the launch write touches is under score.3.

    The board also *reads* tournamentInfo.displayTournamentName at
    construction; if that ever became a write, an operator switching to the
    crew board would clear the display name boards 1/2 had published.
    """
    with _SetSpy() as spy:
        autonaApp.board(BOARD)
        autonaApp.settle()

    stray = [key for key in spy.keys
             if key != PREFIX and not key.startswith(f"{PREFIX}.")]
    assert stray == [], f"crew construction wrote outside its subtree: {stray}"


# ── the team total (stocks summed by the board) ──────────────────────────

def test_a_named_member_counts_toward_the_team_total(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "name") == "Alpha"
    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK


def test_an_unnamed_member_does_not_count_toward_the_total(autonaApp):
    """The rule that makes the total useful before a crew is entered.

    Every row starts at full stocks, so summing all of them would show 9 on
    an empty board. Only named rows count.
    """
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    _nameMember(board, 0, 2, "   ")        # whitespace is not a name
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK


def test_a_member_beyond_the_visible_count_does_not_count(autonaApp):
    board = autonaApp.board(BOARD)

    for member in range(1, MAX_MEMBERS + 1):
        _nameMember(board, 0, member, f"P{member}")
    autonaApp.settle()

    # Only DEFAULT_MEMBER_NUM rows are visible, so only those are summed.
    assert _get(
        autonaApp, "team", 1, "score") == DEFAULT_MEMBER_NUM * DEFAULT_STOCK


def test_clearing_a_member_name_takes_its_stocks_off_the_total(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    autonaApp.settle()
    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK

    _nameMember(board, 0, 1, "")
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "score") == 0


def test_a_stock_change_reaches_the_state_and_the_total(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    board.CommandMemberStock(0, 1, -1)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "stock") == DEFAULT_STOCK - 1
    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK - 1


def test_an_out_of_range_stock_command_is_ignored(autonaApp):
    board = autonaApp.board(BOARD)

    # Reachable from the legacy REST route; must not raise.
    board.CommandMemberStock(5, 1, -1)
    board.CommandMemberStock(0, MAX_MEMBERS + 1, -1)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "stock") == DEFAULT_STOCK


def test_a_remote_team_score_command_does_not_crash_or_alter_the_total(
        autonaApp):
    """CommandScoreChange (the /scoreboard3-teamN-scoreup route) is a no-op.

    `AutonaActions.AdjustScore` only writes `score.{n}.team.{t}.score` for
    boards 1/2 (`SCORE_BOARDS`); board 3's team score is derived from member
    stocks, so the same web action must reach the crew board without raising
    and without disturbing the total the roster produces
    (`AutonaCrewBattleWidget.CommandScoreChange`'s "safe no-op" comment).
    """
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")
    autonaApp.settle()

    autonaApp.actions().team_scoreup(BOARD, "1")
    autonaApp.actions().team_scoredown(BOARD, "1")
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK


def test_changing_the_member_count_writes_both_teams(autonaApp):
    """One spinbox, two teams -- crew battles are symmetric by definition."""
    board = autonaApp.board(BOARD)

    for member in range(1, MAX_MEMBERS + 1):
        _nameMember(board, 0, member, f"P{member}")
    board.playerNumber.setValue(5)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "memberNum") == 5
    assert _get(autonaApp, "team", 2, "memberNum") == 5
    assert _get(autonaApp, "team", 1, "score") == 5 * DEFAULT_STOCK


# ── team header (name / colour) ──────────────────────────────────────────

def test_typing_a_team_name_reaches_the_state(autonaApp):
    board = autonaApp.board(BOARD)

    _typeTeamName(board, 0, "Team Rocket")
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "teamName") == "Team Rocket"


def test_picking_a_team_colour_reaches_the_state(autonaApp):
    board = autonaApp.board(BOARD)

    board.colorButtons[1].setColor("#123456")
    autonaApp.settle()

    assert _get(autonaApp, "team", 2, "color") == "#123456"


def test_a_remote_colour_change_reaches_the_crew_board(autonaApp):
    """AutonaActions writes it; the board mirrors it into its button.

    The mirror is what stops the operator's swatch from showing the previous
    colour after an /api/v1 or legacy REST change, and it must not write the
    value straight back (that is what _SyncTeamColors' QSignalBlocker is
    for).
    """
    from src.core.StateManager import StateManager

    board = autonaApp.board(BOARD)
    autonaApp.settle()

    revision = StateManager.revision
    autonaApp.actions().team_color(BOARD, "1", "#abcdef")
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "color") == "#abcdef"
    assert board.colorButtons[0].color() == "#abcdef"
    assert StateManager.revision == revision + 1, (
        "the crew board echoed the remote colour change back")


# ── the active member marker ─────────────────────────────────────────────

def test_the_active_member_is_published_as_a_string(autonaApp):
    board = autonaApp.board(BOARD)

    board.SetActiveMember(0, 2)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "activeMember") == "2"


def test_only_one_member_of_a_team_can_be_active(autonaApp):
    board = autonaApp.board(BOARD)

    board.SetActiveMember(0, 1)
    board.SetActiveMember(0, 3)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "activeMember") == "3"
    assert _row(board, 0, 1)["active"].isChecked() is False


def test_clearing_the_active_member_publishes_zero(autonaApp):
    board = autonaApp.board(BOARD)

    board.SetActiveMember(0, 1)
    autonaApp.settle()
    board.SetActiveMember(0, 0)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "activeMember") == "0"


def test_the_two_teams_keep_separate_active_members(autonaApp):
    board = autonaApp.board(BOARD)

    board.SetActiveMember(0, 1)
    board.SetActiveMember(1, 2)
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "activeMember") == "1"
    assert _get(autonaApp, "team", 2, "activeMember") == "2"


# ── swap / reset / clear ─────────────────────────────────────────────────

def test_swapping_exchanges_the_whole_board(autonaApp):
    board = autonaApp.board(BOARD)

    _typeTeamName(board, 0, "Alpha")
    _typeTeamName(board, 1, "Bravo")
    _nameMember(board, 0, 1, "A1")
    _nameMember(board, 1, 1, "B1")
    board.CommandMemberStock(0, 1, -2)
    board.SetActiveMember(1, 1)
    board.colorButtons[0].setColor("#111111")
    board.colorButtons[1].setColor("#222222")
    autonaApp.settle()

    board.SwapTeams()
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "teamName") == "Bravo"
    assert _get(autonaApp, "team", 2, "teamName") == "Alpha"
    assert _get(autonaApp, "team", 1, "player", 1, "name") == "B1"
    assert _get(autonaApp, "team", 2, "player", 1, "name") == "A1"
    assert _get(autonaApp, "team", 2, "player", 1, "stock") == DEFAULT_STOCK - 2
    assert _get(autonaApp, "team", 1, "activeMember") == "1"
    assert _get(autonaApp, "team", 2, "activeMember") == "0"
    assert _get(autonaApp, "team", 1, "color") == "#222222"
    assert _get(autonaApp, "team", 2, "color") == "#111111"
    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK
    assert _get(autonaApp, "team", 2, "score") == DEFAULT_STOCK - 2
    assert board.teamsSwapped is True


def test_swapping_twice_is_the_identity(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "A1")
    _nameMember(board, 1, 1, "B1")
    autonaApp.settle()

    board.SwapTeams()
    board.SwapTeams()
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "name") == "A1"
    assert _get(autonaApp, "team", 2, "player", 1, "name") == "B1"
    assert board.teamsSwapped is False


def test_reset_score_restores_stocks_but_keeps_the_crew(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    board.CommandMemberStock(0, 1, -2)
    board.SetActiveMember(0, 1)
    autonaApp.settle()

    board.ResetScore()
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "stock") == DEFAULT_STOCK
    assert _get(autonaApp, "team", 1, "activeMember") == "0"
    # The names are what separates 反映-less "reset stocks" from "clear all".
    assert _get(autonaApp, "team", 1, "player", 1, "name") == "Alpha"
    assert _get(autonaApp, "team", 1, "score") == DEFAULT_STOCK


def test_clear_all_wipes_the_crew(autonaApp):
    board = autonaApp.board(BOARD)

    _nameMember(board, 0, 1, "Alpha")
    board.CommandMemberStock(0, 1, -2)
    board.SetActiveMember(0, 1)
    autonaApp.settle()

    board.CommandClearAll()
    autonaApp.settle()

    assert _get(autonaApp, "team", 1, "player", 1, "name") == ""
    assert _get(autonaApp, "team", 1, "player", 1, "stock") == DEFAULT_STOCK
    assert _get(autonaApp, "team", 1, "activeMember") == "0"
    assert _get(autonaApp, "team", 1, "score") == 0


# ── the match bar (crew has phase / pool / match too) ────────────────────

def test_typing_in_the_crew_match_bar_reaches_the_state(autonaApp):
    board = autonaApp.board(BOARD)

    _typeCombo(board.phaseCombo, "Pools")
    _typeCombo(board.poolCombo, "A")
    _typeCombo(board.matchCombo, "Winners Final")
    autonaApp.settle()

    assert _get(autonaApp, "phase") == "Pools"
    assert _get(autonaApp, "pool") == "A"
    assert _get(autonaApp, "match") == "Winners Final"


# ── ChangeSetData: the crew rules, which are NOT the board 1/2 rules ─────
#
# Four documented differences from SetLoadService.ApplyMatchInfo, all pinned
# below because Step 4b explicitly deferred unifying them:
#
#   1. round_name        crew: `is not None`   boards 1/2: truthiness
#   2. tournament_phase  crew: `is not None`   boards 1/2: truthiness
#   3. tournament_pool   crew: `is not None`   boards 1/2: key presence
#   4. match_en          crew: never written   boards 1/2: written always
#
# plus `bestOf` is `is not None` here (boards 1/2 test truthiness), so a
# payload carrying bestOf=0 clears the crew board's set length.

def test_an_empty_round_name_clears_the_crew_match(autonaApp):
    board = autonaApp.board(BOARD)

    board.ChangeSetData({"round_name": "Winners Final"})
    autonaApp.settle()
    assert _get(autonaApp, "match") == "Winners Final"

    board.ChangeSetData({"round_name": ""})
    autonaApp.settle()

    # Boards 1/2 would keep "Winners Final" here (SetLoadService tests an
    # empty round name as "this payload does not carry one").
    assert _get(autonaApp, "match") == ""


def test_an_empty_phase_clears_the_crew_phase(autonaApp):
    board = autonaApp.board(BOARD)

    board.ChangeSetData({"tournament_phase": "Pools"})
    autonaApp.settle()
    board.ChangeSetData({"tournament_phase": ""})
    autonaApp.settle()

    assert _get(autonaApp, "phase") == ""


def test_a_missing_pool_key_leaves_the_crew_pool_alone(autonaApp):
    board = autonaApp.board(BOARD)

    board.ChangeSetData({"tournament_pool": "A"})
    autonaApp.settle()
    board.ChangeSetData({"tournament_phase": "Pools"})
    autonaApp.settle()

    assert _get(autonaApp, "pool") == "A"


def test_the_crew_board_never_writes_match_en(autonaApp):
    """The English round name has no crew consumer and is not published.

    Boards 1/2 write `match_en` unconditionally (the YouTube title
    generator reads it); folding the two paths together would add the key to
    board 3.
    """
    board = autonaApp.board(BOARD)

    board.ChangeSetData(
        {"round_name": "Winners Final", "round_name_en": "Winners Final"})
    autonaApp.settle()

    assert _get(autonaApp, "match_en") is None


def test_a_zero_best_of_reaches_the_crew_board(autonaApp):
    board = autonaApp.board(BOARD)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()
    board.ChangeSetData({"bestOf": 0})
    autonaApp.settle()

    assert _get(autonaApp, "best_of") == 0
    assert board._bestOfSpin.value() == 0


def test_the_crew_best_of_publishes_only_the_raw_key(autonaApp):
    """No best_of_text / first_to* on board 3 -- deliberately.

    The localized long forms are formatted from AutonaLocaleHelper's round
    name tables, which the crew board must not depend on: its ChangeSetData
    is the tolerant path generic web callers land on.
    """
    board = autonaApp.board(BOARD)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    assert _get(autonaApp, "best_of") == 5
    for derived in ("best_of_text", "best_of_short_text", "first_to",
                    "first_to_short_text", "first_to_text"):
        assert _get(autonaApp, derived) is None, (
            f"crew board published the derived key {derived}")


def test_a_non_dict_set_payload_is_ignored(autonaApp):
    board = autonaApp.board(BOARD)

    board.ChangeSetData(None)
    board.ChangeSetData("nonsense")
    autonaApp.settle()

    assert _get(autonaApp, "match") == ""


def test_a_set_payload_with_nothing_crew_understands_changes_nothing(
        autonaApp):
    """`set_team_data` and a full set load both land here.

    Board 3 has no entrant roster, so a payload built for boards 1/2 must
    leave it exactly as it was rather than half-apply.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.board(BOARD)
    autonaApp.settle()

    revision = StateManager.revision
    board.ChangeSetData({"team": 1, "player": 1, "data": {"gamerTag": "X"}})
    board.ChangeSetData({"team1score": 2, "team2score": 1})
    autonaApp.settle()

    assert StateManager.revision == revision


# ── 表示大会名 (shared with the SCORE panel and the EVENT tab) ────────────

@pytest.fixture
def restoreTournamentInfo(autonaApp):
    """Hand tournamentInfo.* back untouched -- it is process-global.

    Same fixture tests/test_tournament_info_panel.py carries, and for the
    same reason: the display name is one field shared by the EVENT tab, the
    SCORE panel and this board, and `_lastTournamentName` is the ledger the
    whole follow rule rests on. Leaving a crew edit behind decides what a
    later module's follow-rule test sees.
    """
    from src.core.TournamentInfoService import TournamentInfoService
    from src.Helpers.AutonaDictHelper import deep_clone
    from src.core.StateManager import StateManager

    saved = deep_clone(StateManager.Get("tournamentInfo") or {})
    service = TournamentInfoService.instance
    lastName = service._lastTournamentName
    try:
        yield
    finally:
        StateManager.Set("tournamentInfo", saved)
        service._lastTournamentName = lastName
        autonaApp.settle()


def test_typing_a_display_tournament_name_reaches_the_state(
        autonaApp, restoreTournamentInfo):
    board = autonaApp.board(BOARD)

    board.displayTournamentNameEdit.setText("Crew Cup")
    board.displayTournamentNameEdit.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get("tournamentInfo.displayTournamentName") == "Crew Cup"


def test_a_display_name_written_elsewhere_reaches_the_crew_field(
        autonaApp, restoreTournamentInfo):
    from src.core.TournamentInfoService import TournamentInfoService

    board = autonaApp.board(BOARD)

    TournamentInfoService.instance.SetDisplayName("From the EVENT tab")
    autonaApp.settle()

    assert board.displayTournamentNameEdit.text() == "From the EVENT tab"


# ── echo-loop / write-key checks ─────────────────────────────────────────
#
# score.3 is not held, so every write publishes immediately and one operator
# action must produce exactly one revision bump. A second one means a
# handler wrote the state back after receiving its own notification.

def _revisionCost(app, action):
    from src.core.StateManager import StateManager

    app.settle()
    before = StateManager.revision
    action()
    app.settle()
    return StateManager.revision - before


def test_a_member_name_edit_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)

    cost = _revisionCost(
        autonaApp, lambda: _nameMember(board, 0, 1, "Alpha"))

    assert cost == 1, f"a member name edit published {cost} times"


def test_a_stock_change_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")

    cost = _revisionCost(
        autonaApp, lambda: board.CommandMemberStock(0, 1, -1))

    assert cost == 1, f"a stock change published {cost} times"


def test_a_member_count_change_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")

    cost = _revisionCost(autonaApp, lambda: board.playerNumber.setValue(5))

    assert cost == 1, f"a member count change published {cost} times"


def test_a_swap_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")
    _nameMember(board, 1, 1, "Bravo")

    cost = _revisionCost(autonaApp, board.SwapTeams)

    assert cost == 1, f"a swap published {cost} times"


def test_a_reset_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")
    board.CommandMemberStock(0, 1, -1)

    cost = _revisionCost(autonaApp, board.ResetScore)

    assert cost == 1, f"a stock reset published {cost} times"


def test_a_clear_all_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Alpha")

    cost = _revisionCost(autonaApp, board.CommandClearAll)

    assert cost == 1, f"clear all published {cost} times"


def test_an_active_toggle_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)

    cost = _revisionCost(autonaApp, lambda: board.SetActiveMember(0, 1))

    assert cost == 1, f"an active toggle published {cost} times"


def test_a_change_set_data_call_is_exactly_one_publish(autonaApp):
    board = autonaApp.board(BOARD)

    cost = _revisionCost(
        autonaApp,
        lambda: board.ChangeSetData(
            {"round_name": "WF", "tournament_phase": "Top 8",
             "tournament_pool": "A", "bestOf": 5}))

    assert cost == 1, f"one set payload published {cost} times"


def test_a_member_name_edit_writes_only_that_team(autonaApp):
    """Which keys one edit touches -- the contract a migration must keep.

    Renaming a member rewrites the whole stock column of *its* team plus the
    team total (the total depends on which rows are named, so the recompute
    is not optional), and nothing on the other team.
    """
    board = autonaApp.board(BOARD)
    autonaApp.settle()

    with _SetSpy() as spy:
        _nameMember(board, 0, 1, "Alpha")
        autonaApp.settle()

    assert _key("team", 1, "player", 1, "name") in spy.keys
    assert spy.under("team", 2) == [], (
        "renaming a member of team 1 wrote into team 2")
    assert _key("team", 1, "score") in spy.keys


def test_the_active_toggle_writes_exactly_one_key(autonaApp):
    board = autonaApp.board(BOARD)
    autonaApp.settle()

    with _SetSpy() as spy:
        board.SetActiveMember(0, 2)
        autonaApp.settle()

    assert spy.keys == [_key("team", 1, "activeMember")]


@pytest.mark.parametrize("field,payloadKey", [
    ("match", "round_name"),
    ("phase", "tournament_phase"),
    ("pool", "tournament_pool"),
])
def test_one_set_field_writes_one_key(autonaApp, field, payloadKey):
    board = autonaApp.board(BOARD)
    autonaApp.settle()

    with _SetSpy() as spy:
        board.ChangeSetData({payloadKey: "value"})
        autonaApp.settle()

    assert spy.keys == [_key(field)]
