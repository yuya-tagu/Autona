"""The crew battle board (score.3): what its state means, and who writes it.

Board 3 is not a variant of the 1v1 / team scoreboard, it is a different
game: a team has no score of its own, it has a roster of members who each
hold a remaining-stock count, and the team total is the sum of the stocks of
the members who are *named and visible*. Which member is currently playing
is a third piece of state (``activeMember``), and both teams share one
member count.

All of that lived in ``AutonaCrewBattleWidget`` as the widget tree itself:
``_teamTotal`` summed QSpinBox values behind QLineEdits with text in them,
and ``_flushAllState`` copied the whole board out of the widgets whenever a
bulk operation (swap / reset / clear) had finished poking them. The state
was a mirror of the form. Since headless core Step 5 (instalment 4) the
rules are here, the state is what they are written to, and the widget passes
in the values it read off its rows -- the same split
``core/PlayerService.py`` has with the player card.

Two things deliberately differ from boards 1 and 2, and both are the reason
Step 4b left the crew board alone rather than folding it into
``SetLoadService``:

- **The set payload is read with different tests.** ``ApplyMatchInfo`` below
  treats ``round_name`` / ``tournament_phase`` / ``tournament_pool`` as
  "carried" whenever the key is present with a non-None value, so an empty
  string *clears* the field. ``SetLoadService.ApplyMatchInfo`` tests the
  first two for truthiness (an empty round name means "not carried") and the
  third for key presence. It also writes ``match_en`` unconditionally, which
  board 3 never has. Unifying them would silently change three behaviours at
  once; the two are kept apart, with the difference stated here and pinned by
  tests/test_crew_board.py.
- **``best_of`` publishes the raw key only.** ``AutonaActions.SetBestOf``
  additionally derives ``best_of_text`` / ``first_to*`` from the localized
  round-name tables. Nothing on the crew overlay reads them, and this board's
  ``ChangeSetData`` is the tolerant path a generic web caller lands on, so it
  must not depend on those tables having been loaded.

What is *not* crew-specific goes through the shared operations instead: the
team name and the team colour are ``AutonaActions.SetTeamName`` /
``SetTeamColor`` (same keys, same meaning as on boards 1 and 2), and the
match bar's three combos are ``AutonaActions.SetMatchField``.

Import side effects: none (agent-pitfalls #1). One instance per crew board,
created by that board's AutonaCrewBattleWidget.
"""
from typing import NamedTuple

from loguru import logger

from . import StateSchema as schema
from .StateManager import StateManager


class CrewMember(NamedTuple):
    """One roster row as the widget currently shows it."""
    name: str = ""
    stock: int = 0
    active: bool = False


class CrewTeam(NamedTuple):
    """One crew as the widget currently shows it."""
    name: str = ""
    color: str = ""
    members: tuple = ()


class CrewBattleService:
    """The crew battle rules of one board. Bound to ``score.{number}``."""

    # LatteCon crew battles support 1v1 up to 9v9. The roster is fixed-size:
    # all nine rows exist and carry a stock count from launch, and
    # `memberNum` decides how many of them are shown and counted. That is why
    # every write below covers all nine rather than the visible ones -- an
    # overlay reading row 7 after the count was lowered gets the value it had
    # rather than a missing key.
    MAX_MEMBERS = 9
    # Stocks a fresh member row starts with (typical Smash crew battle).
    DEFAULT_STOCK = 3
    # Both teams share the count, mirroring LatteCon's single `cb_memberNum`.
    DEFAULT_MEMBER_NUM = 3
    # LatteCon's left/right crew colours.
    DEFAULT_COLORS = ("#fe0000", "#0071bc")

    def __init__(self, scoreboardNumber=3):
        self.scoreboardNumber = scoreboardNumber

    # ── key paths ────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return f"score.{self.scoreboardNumber}"

    def _teamKey(self, team, leaf) -> str:
        return f"{self.prefix}.team.{team}.{leaf}"

    def _memberKey(self, team, member, leaf) -> str:
        return f"{self.prefix}.team.{team}.player.{member}.{leaf}"

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _asInt(value, default=0):
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # Write `key` only when it would actually change; True if written (the
    # shared `StateManager.SetIfChanged`). score.3 is not a held subtree, so
    # unlike the 1v1 / team boards this is not about the 反映 button lighting
    # up: an unchanged write produces no delta, but it still runs a save
    # cycle -- a ./out rewrite and an export thread -- for nothing. Worth
    # guarding because the payload path this is used from
    # (``ApplyMatchInfo``) is a remote one that can be polled: a control
    # panel re-posting the same round name every few seconds is the shape
    # that makes it add up.
    _SetIfChanged = staticmethod(StateManager.SetIfChanged)

    # ── the team total (the crew rule that makes this board different) ───

    def TeamTotal(self, team) -> int:
        """Sum the remaining stocks of one crew's named, visible members.

        Read from the state rather than from the widget rows, which is the
        inversion this instalment is: the roster *is* the state now, so the
        total is derived from it and a headless caller gets the same number
        the operator sees. Unnamed rows are skipped because every row starts
        at full stocks -- counting them would show 9 on an empty board --
        and rows past ``memberNum`` are skipped because they are not in this
        crew battle at all.
        """
        memberNum = self._asInt(
            StateManager.Get(
                self._teamKey(team, schema.CREW_MEMBER_NUM_KEY), 0), 0)
        total = 0
        for member in range(1, min(memberNum, self.MAX_MEMBERS) + 1):
            name = StateManager.Get(
                self._memberKey(team, member, schema.PLAYER_TAG_KEY), "")
            if not str(name or "").strip():
                continue
            total += self._asInt(
                StateManager.Get(
                    self._memberKey(team, member, schema.CREW_STOCK_KEY), 0), 0)
        return total

    def PublishTotal(self, team) -> int:
        """Recompute one crew's total and publish it as the team score."""
        total = self.TeamTotal(team)
        StateManager.Set(self._teamKey(team, "score"), total)
        return total

    def PublishStocks(self, team, stocks) -> int:
        """Write one crew's whole stock column and the total it produces.

        `stocks` is the roster in row order (index 0 = member 1), all
        ``MAX_MEMBERS`` of them. One save cycle: the stocks and the total
        they add up to are one fact, and publishing them separately would
        broadcast a board whose members and total disagree.
        """
        with StateManager.SaveBlock():
            for index, stock in enumerate(stocks, start=1):
                StateManager.Set(
                    self._memberKey(team, index, schema.CREW_STOCK_KEY),
                    self._asInt(stock, 0))
            return self.PublishTotal(team)

    # ── roster ───────────────────────────────────────────────────────────

    def SetMemberName(self, team, member, name, stocks) -> int:
        """Name one roster slot; returns the crew's recomputed total.

        The recompute is not optional and not the caller's business to
        remember: naming or clearing a slot is precisely what decides whether
        its stocks count, so the pair is one operation and one save cycle.
        """
        with StateManager.SaveBlock():
            StateManager.Set(
                self._memberKey(team, member, schema.PLAYER_TAG_KEY),
                "" if name is None else str(name))
            return self.PublishStocks(team, stocks)

    def SetMemberNum(self, value, stocksPerTeam) -> list:
        """Set how many members are in this crew battle; returns the totals.

        Written per team even though there is one count, because that is the
        shape the overlays read (each crew renders its own roster). Both
        totals are recomputed in the same cycle: raising or lowering the
        count changes which rows are summed.

        The number of teams is decided by the length of `stocksPerTeam`, not
        by a constant here -- a crew battle is two-sided by definition, so
        every current caller passes exactly two, but nothing below assumes
        that length.
        """
        value = self._asInt(value, self.DEFAULT_MEMBER_NUM)
        totals = []
        with StateManager.SaveBlock():
            for index, stocks in enumerate(stocksPerTeam, start=1):
                StateManager.Set(
                    self._teamKey(index, schema.CREW_MEMBER_NUM_KEY), value)
                totals.append(self.PublishStocks(index, stocks))
        return totals

    def SetActiveMember(self, team, member):
        """Mark which member of a crew is currently playing (0 = nobody).

        Published as a *string*, which is how the crew overlay has always
        read it -- ``"0"`` rather than ``0``.
        """
        StateManager.Set(
            self._teamKey(team, schema.CREW_ACTIVE_MEMBER_KEY),
            str(self._asInt(member, 0)))

    # ── best of ──────────────────────────────────────────────────────────

    def SetBestOf(self, value):
        """Publish the set length, and nothing derived from it.

        See the module docstring: ``AutonaActions.SetBestOf``'s five extra
        keys have no crew consumer and are formatted from locale tables this
        path must not require.
        """
        StateManager.Set(f"{self.prefix}.best_of", self._asInt(value, 0))

    @classmethod
    def BestOfFromSetData(cls, data):
        """The set length a payload carries, or None when it carries none.

        Parsing lives here so the tolerance this path promises is real: the
        old code handed ``int(data["bestOf"])`` straight to a QSpinBox, so a
        payload carrying a non-numeric set length raised inside a Qt signal
        handler and abandoned the rest of the call. A single bad key is
        dropped with a warning instead.

        ``is not None``, not truthiness: ``bestOf: 0`` means "no set length",
        and clearing it is what the generic reset relies on.
        """
        if not isinstance(data, dict):
            return None
        raw = data.get("bestOf")
        if raw is None:
            return None
        value = cls._asInt(raw, None)
        if value is None:
            logger.warning(
                f"Crew set data: ignoring non-numeric bestOf {raw!r}")
        return value

    # ── match information (phase / pool / round) ─────────────────────────

    def ApplyMatchInfo(self, data) -> bool:
        """Write the round/phase/pool fields a payload carries.

        The crew reading of a payload, which is **not**
        ``SetLoadService.ApplyMatchInfo``'s (see the module docstring): all
        three fields are taken as carried whenever the key holds a non-None
        value, so an empty string clears the field rather than meaning "this
        payload has no opinion". ``match_en`` is not written at all.

        Board 3 never receives a real set load -- only ``set_route`` and
        ``post_score`` reach it -- so "carried" here is always an explicit
        query parameter, which is what makes "empty means clear" the right
        reading on this board and the wrong one on the others.
        """
        if not isinstance(data, dict):
            return False
        written = False
        for field, payloadKey in (
                (schema.MATCH_KEY, "round_name"),
                (schema.PHASE_KEY, "tournament_phase"),
                (schema.POOL_KEY, "tournament_pool")):
            value = data.get(payloadKey)
            if value is None:
                continue
            written |= self._SetIfChanged(f"{self.prefix}.{field}", value)
        return bool(written)

    # ── the whole board (swap / reset / clear / launch) ──────────────────

    def PublishTeam(self, team, crew: CrewTeam, memberNum) -> int:
        """Write one crew wholesale; returns its recomputed total.

        The bulk operations (swap, reset stocks, clear all) rearrange the Qt
        rows with their signals blocked and then hand the finished board
        here, rather than letting each individual write broadcast on its own
        -- a swap that published field by field would put a half-swapped
        board on the stream.

        The team name and colour are written here rather than delegated to
        ``AutonaActions``, for the same reason
        ``SetLoadService.ApplyTeamName`` writes its own: the shared
        operations are the *entry points* an operator action goes through
        (this board's QLineEdit and colour button do call them), while a
        service publishing a batch is the second writer they coordinate with
        through the state. Routing a nine-row snapshot through them would
        also make this class depend on ``AutonaActions.instance`` having been
        bootstrapped, which nothing else under core does.
        """
        StateManager.Set(
            self._teamKey(team, schema.TEAM_NAME_KEY),
            "" if crew.name is None else str(crew.name))
        StateManager.Set(self._teamKey(team, "color"), crew.color or "")
        StateManager.Set(
            self._teamKey(team, schema.CREW_MEMBER_NUM_KEY),
            self._asInt(memberNum, self.DEFAULT_MEMBER_NUM))

        active = 0
        for index, member in enumerate(crew.members, start=1):
            StateManager.Set(
                self._memberKey(team, index, schema.PLAYER_TAG_KEY),
                "" if member.name is None else str(member.name))
            StateManager.Set(
                self._memberKey(team, index, schema.CREW_STOCK_KEY),
                self._asInt(member.stock, 0))
            if member.active:
                active = index
        self.SetActiveMember(team, active)
        return self.PublishTotal(team)

    def PublishBoard(self, teams, memberNum) -> list:
        """Write both crews in one save cycle; returns the two totals."""
        with StateManager.SaveBlock():
            return [self.PublishTeam(index, crew, memberNum)
                    for index, crew in enumerate(teams, start=1)]

    def SeedBoardState(self, teams, memberNum, bestOf) -> list:
        """The board's launch write; returns the two totals.

        ``StateSchema.SeedBoard(3)`` only creates the subtree root -- the
        crew board has no set history and no station queue -- so this is what
        puts the keys the OBS crew overlay reads unconditionally into the
        state. It is deliberately the same pass as ``PublishBoard`` plus the
        board-level fields: a fresh board and a board that has just been
        cleared have to end up identical, and they used to be two separate
        loops that could (and did) drift.

        ``pool`` is *not* seeded, matching what the widget has always done.
        Boards 1 and 2 seed none of the three; the asymmetry is recorded in
        tests/test_crew_board.py rather than quietly fixed here, because
        adding a key changes what a fresh program_state.json contains.
        """
        with StateManager.SaveBlock():
            StateManager.Set(f"{self.prefix}.{schema.PHASE_KEY}", "")
            StateManager.Set(f"{self.prefix}.{schema.MATCH_KEY}", "")
            self.SetBestOf(bestOf)
            return self.PublishBoard(teams, memberNum)

    @classmethod
    def DefaultTeams(cls) -> list:
        """The two crews a freshly built crew board shows: no names, the
        default colours, every row at DEFAULT_STOCK, nobody active."""
        return [
            CrewTeam(name="", color=color, members=tuple(
                CrewMember(name="", stock=cls.DEFAULT_STOCK, active=False)
                for _ in range(cls.MAX_MEMBERS)))
            for color in cls.DEFAULT_COLORS]

    def SeedDefaults(self) -> list:
        """The launch write of a freshly built crew board, without the
        widget: ``SeedBoardState`` with the rows it is built with
        (``DefaultTeams``), the default member count and no best of. For a
        shell that builds no crew widget (the headless one, ADR-005 S3d);
        tests/test_headless_seed.py pins it against a desktop board."""
        return self.SeedBoardState(
            self.DefaultTeams(), self.DEFAULT_MEMBER_NUM, 0)
