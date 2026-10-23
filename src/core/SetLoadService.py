"""Set loading: turning a provider payload into one board's state.

``AutonaScoreboardWidget.ChangeSetData`` was the single largest write in the
app that had no reason to be Qt: 140 lines deciding what a loaded set means
-- which round name to show, whose score goes on which side, whether an
empty pool clears the previous one -- expressed entirely as widget
manipulation. It set a combo's text and emitted ``editingFinished`` so the
objectName-to-keypath lambda would write the key; it drove the score
spinboxes so their ``valueChanged`` would write the score. The state was the
*byproduct* of poking a form.

Since headless core Step 4b it is the other way round, matching what Step 3
did to the score and Step 4a to the stage rows: **this class decides and
writes, the board renders what it finds in the state**
(``AutonaScoreboardWidget._SyncSetFields`` / ``_SyncScoreDisplays``). That
is what makes "an empty tournament_pool clears the pool but a phase-only web
edit does not touch it" a rule with a test instead of a comment above a
combo.

What deliberately did NOT move (and why):

- **Player data.** ``AutonaScoreboardPlayerWidget.SetData`` resolves country
  and state combos, character/skin/variant model rows and the bad-word
  filter -- all of it against Qt models, and all of it still there. What
  moved here is the *addressing* (which entrant lands on which team once the
  teams have been swapped, see ``DisplayTeam``) and the team name that comes
  with it; the player subtree's own writes went to ``core/PlayerService.py``
  in the next instalment, which is what SetData now hands its picks to
  (since ADR-005 S1d as identifiers -- character en_name, skin row, country
  and region code -- which PlayerService turns into the published values
  from the core catalogs).
- **Best of.** ``best_of_text`` / ``first_to_text`` are formatted from
  ``AutonaLocaleHelper.matchNames`` and the same handler resizes the stage
  rows; Step 3 left it on the Qt side and this step does not change that.

INVARIANT -- stage rows. Nothing here writes ``score.{n}.stages.*``. The row
count is kept equal to the widget's ``stage_widget_list`` by
``GameTrackerService.SetStageCount()`` alone (see its ``StageCount()``
docstring), and a set load that grew the rows on its own would leave rows
nothing renders. A loaded score reaches the stages the same way a +1 does:
this class writes ``team.{t}.score`` and the board's ``_SyncScoreDisplays``
hands the transition to ``GameTrackerService.SyncStagesToScore``.

Import side effects: none (agent-pitfalls #1). One instance per board,
created by that board's AutonaScoreboardWidget.
"""
import os

from loguru import logger

from .Actions import AutonaActions
from .PlayerService import RepublishTeamMergedNames
from .SettingsManager import SettingsManager
from .StateManager import StateManager

# Operator-supplied team crests, matched by lower-cased team name. Relative
# to the process CWD like every other mutable path (./out, ./user_data).
TEAM_LOGO_DIR = "./user_data/team_logo"


class SetLoadService:
    """The set-loading rules of one board. Bound to ``score.{number}``.

    Which way round the two sides are is read from the state,
    ``score.{n}.teamsSwapped``, which ``AutonaActions.SwapTeams`` toggles
    (ADR-005 S2c). Until then the swap was a widget-to-widget copy of the
    player fields and the board's own ``teamsSwapped`` attribute was the
    authority, handed in here as a callable; the key was only its mirror.
    """

    def __init__(self, scoreboardNumber):
        self.scoreboardNumber = scoreboardNumber

    # ── key paths ────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return f"score.{self.scoreboardNumber}"

    def _teamKey(self, team, leaf) -> str:
        return f"{self.prefix}.team.{team}.{leaf}"

    # ── helpers ──────────────────────────────────────────────────────────

    def DisplayTeam(self, setTeam) -> int:
        """Which state-side team the set's team `setTeam` (1 or 2) lands on.

        The one rule the old code spelled out four times, once per thing it
        moved (scores, losers flags, team names, entrant rosters), each as
        its own list-reversal. After a swap the set's first entrant is shown
        on the right, so it writes ``team.2``.
        """
        setTeam = 1 if int(setTeam) == 1 else 2
        if StateManager.Get(f"{self.prefix}.teamsSwapped", False):
            return 3 - setTeam
        return setTeam

    @staticmethod
    def _asInt(value):
        """`value` as an int, or None when it is not a number at all.

        The score used to be handed straight to ``QSpinBox.setValue``, which
        raises on anything that is not an int -- inside a signal handler, so
        the traceback went to stderr and the rest of the set load was
        abandoned. Refusing the one key is the smaller failure.
        """
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    # Write `key` only when it would actually change; True if written (the
    # shared `StateManager.SetIfChanged`). Every write under a held prefix
    # marks the board dirty and lights the 反映 button, so re-writing a value
    # the state already holds would announce an edit the operator did not
    # make. The Qt path got this for free from the widgets (``setChecked`` /
    # ``setValue`` emit nothing when the value is unchanged); doing it here
    # keeps that property now that the write no longer goes through them.
    _SetIfChanged = staticmethod(StateManager.SetIfChanged)

    # ── match information (phase / pool / round) ─────────────────────────

    def ApplyMatchInfo(self, data) -> bool:
        """Write the round/phase/pool fields a set (or a web edit) carries.

        Three deliberately different tests, all of them load-bearing:

        - ``round_name`` / ``tournament_phase``: truthiness. An empty round
          name means "this payload does not carry one" -- ``set_route``
          builds ``{'round_name': match}`` from a query parameter, and a
          blank one must not wipe what the operator typed.
        - ``tournament_pool``: key *presence*. A set from a pool-less phase
          carries ``tournament_pool = ""`` and has to clear the previous
          pool, while a phase-only edit (no such key) must leave it alone.
        - ``match_en``: written unconditionally, which is how it has always
          behaved. It is the raw English long-form round text used by the
          English-only outputs (the YouTube title generator), and a payload
          that carries no ``round_name_en`` blanks it.
        """
        if not isinstance(data, dict):
            return False
        written = False

        roundName = data.get("round_name")
        if roundName:
            written |= self._SetIfChanged(f"{self.prefix}.match", roundName)

        written |= self._SetIfChanged(
            f"{self.prefix}.match_en", data.get("round_name_en") or "")

        phase = data.get("tournament_phase")
        if phase:
            written |= self._SetIfChanged(f"{self.prefix}.phase", phase)

        if "tournament_pool" in data:
            written |= self._SetIfChanged(
                f"{self.prefix}.pool", data.get("tournament_pool") or "")

        return bool(written)

    # ── score ────────────────────────────────────────────────────────────

    def ApplyScores(self, data) -> bool:
        """Write the two team scores the payload carries.

        `reset_score` zeroes both first, exactly as the old code did by
        driving the spinboxes to 0 -- but the zero and the value that
        follows it now land in the same save cycle, so the board sees one
        transition instead of two. That matters for the individual game
        tracker: the old "clear then re-award" pass rebuilt the stage rows
        from scratch, which reordered them whenever a set had interleaved
        wins (T2, T1, T1 came back as T1, T1, T2). The rows are left alone
        when the score does not actually move now.

        `general.disable_scoreupdate` suppresses only the scores the payload
        brought, never the reset -- same as before.
        """
        if not isinstance(data, dict):
            return False
        written = False

        if data.get("reset_score"):
            for setTeam in (1, 2):
                written |= self._SetIfChanged(
                    self._teamKey(self.DisplayTeam(setTeam), "score"),
                    AutonaActions.SCORE_MIN)

        if SettingsManager.Get("general.disable_scoreupdate", False):
            return bool(written)

        for setTeam in (1, 2):
            raw = data.get(f"team{setTeam}score")
            if raw is None:
                continue
            value = self._asInt(raw)
            if value is None:
                logger.warning(
                    f"Set load: ignoring non-numeric team{setTeam}score "
                    f"{raw!r}")
                continue
            written |= self._SetIfChanged(
                self._teamKey(self.DisplayTeam(setTeam), "score"),
                max(AutonaActions.SCORE_MIN,
                    min(AutonaActions.SCORE_MAX, value)))

        return bool(written)

    # ── losers bracket flag ──────────────────────────────────────────────

    def ApplyLosers(self, data) -> bool:
        """Write the loaded losers flag onto the display team it belongs to.

        Unlike ``_ApplySinglePlayerData``, which addresses the state teams
        directly because its caller already resolved the swap, this goes
        through ``DisplayTeam(setTeam)`` -- the web `set_route?losers=`
        query reuses the same `team{N}losers` payload shape as a set load,
        so it is treated as a set-team write and flips with the board's
        swap state. The neighboring `teamName=` query, by contrast, drives
        the QLineEdit in the literal `team{N}column` slot and does not flip
        (see AutonaWebServerActions.set_route), and neither does
        `set_team_data`, whose caller names the team it means.

        That inconsistency predates this file (the old code reversed
        `losersContainers` the same way) and is **still open**: `losers=` is
        the odd one out among the web queries, so the consistent reading is
        that `team=` always means the column the operator is looking at.
        Making it so is a semantics change to a legacy REST route with no
        test coverage, on the one query an operator may already be using
        while swapped -- a product call, not a refactor, so it is recorded
        here rather than taken. (/api/v1's ``PATCH set`` no longer goes
        through here for its losers flag: it names the state team and calls
        ``AutonaActions.SetTeamLosers``, ADR-005 S2b.)

        A flag that changes takes its team's display names with it, in the
        same save cycle -- the " [L]" suffix, rebuilt from the slots' state by
        ``PlayerService.RepublishTeamMergedNames`` exactly as
        ``AutonaActions.SetTeamLosers`` does. The board used to do that half
        per player card (``_ApplyLosersToPlayers`` / ``_AdoptTeamLosers``).
        Slots a set load creates afterwards (a roster that grows) need
        nothing: their name is published once their entrant is in, and that
        reads the flag written here.
        """
        if not isinstance(data, dict):
            return False
        written = False
        with StateManager.SaveBlock():
            for setTeam in (1, 2):
                value = data.get(f"team{setTeam}losers")
                if value is None:
                    continue
                team = self.DisplayTeam(setTeam)
                if self._SetIfChanged(
                        self._teamKey(team, "losers"), bool(value)):
                    RepublishTeamMergedNames(self.scoreboardNumber, team)
                    written = True
        return written

    # ── team name and its crest ──────────────────────────────────────────

    def ApplyTeamName(self, setTeam, name):
        """Set one team's name and re-resolve the crest that goes with it.

        The crest lookup used to hang off the QLineEdit's ``editingFinished``
        (``AutonaScoreboardWidget.ExportTeamLogo``), so a name written any
        other way silently kept the previous team's logo. Writing them
        together is what makes the pair a single operation.
        """
        team = self.DisplayTeam(setTeam)
        self._SetIfChanged(self._teamKey(team, "teamName"), name or "")
        self.PublishTeamLogo(team, name)

    def PublishTeamLogo(self, team, name):
        """Point ``team.{t}.logo`` at ./user_data/team_logo/<name>.png or None."""
        path = None
        if isinstance(name, str) and name:
            candidate = f"{TEAM_LOGO_DIR}/{name.lower()}.png"
            if os.path.exists(candidate):
                path = candidate
        self._SetIfChanged(self._teamKey(team, "logo"), path)

    # ── bracket type (board-independent) ─────────────────────────────────

    def ApplyBracketType(self, data) -> bool:
        """Publish the set's bracket type; True when the caller should
        recompute the upset factor.

        ``score.bracket_type`` sits beside the boards rather than inside one
        (AutonaStatsUtil reads it for the upset-factor math), so it is
        outside every held prefix and publishes immediately.

        Deliberately *not* _SetIfChanged: the return value is the caller's cue
        to recalculate the upset factor, and that has to happen on every
        payload that carries a bracket type -- the factor also depends on the
        seeds, which the same set load just rewrote. The unconditional write
        is free anyway (no held prefix to dirty, and an identical value
        yields no delta), so the two halves are kept in step by not being
        conditional at all.
        """
        if not isinstance(data, dict) or not data.get("bracket_type"):
            return False
        StateManager.Set("score.bracket_type", data.get("bracket_type"))
        return True

    # ── set lifecycle (which set this board is following) ────────────────

    def BeginSet(self, setId, autoUpdate=None, station=None):
        """Record which set the board is now showing, and how it follows it."""
        with StateManager.SaveBlock():
            StateManager.Set(f"{self.prefix}.set_id", setId)
            StateManager.Set(f"{self.prefix}.auto_update", autoUpdate)
            StateManager.Set(f"{self.prefix}.station", station)

    def ClearSetTracking(self):
        """Forget the followed set (the timer's cancel button)."""
        with StateManager.SaveBlock():
            StateManager.Set(f"{self.prefix}.auto_update", None)
            StateManager.Set(f"{self.prefix}.set_id", None)
            StateManager.Set(f"{self.prefix}.station", None)

    def SetStationQueue(self, queue):
        StateManager.Set(f"{self.prefix}.station_queue", queue)

    @staticmethod
    def IsStaleUpdate(data, lastSetSelected) -> bool:
        """True when an async set refresh must be dropped on arrival.

        Two cases, both of which used to be spelled out at the top of
        ``UpdateSetData``: an empty payload (the provider found nothing), and
        a payload for a set the operator has already navigated away from --
        an in-flight fetch answering after the next set was loaded would
        otherwise overwrite it with the previous match.
        """
        if not isinstance(data, dict) or not data or data.get("id") is None:
            return True
        return str(data.get("id")) != str(lastSetSelected)
