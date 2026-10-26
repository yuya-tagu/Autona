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

Since ADR-005 S3e-2 the operations read the board back from the state
rather than taking the widget's rows: a stock change, the active member,
the member count, the swap / reset / clear and a set payload are each one
call here (``AdjustMemberStock`` / ``SelectActiveMember`` /
``SetMemberCount`` / ``SwapTeams`` / ``ResetStocks`` / ``ClearCrew`` /
``ApplySetData``), from the board's inputs and from the Web alike, and the
widget renders the state (``AutonaCrewBattleWidget._RenderFromState``). That
is what lets the headless shell run them with no widget, and what keeps a
widget that has not shown a change yet from writing its older rows back.
What each publishes is the widget path's, recorded before the move
(tests/fixtures/crew_operations/golden.json).

Import side effects: none (agent-pitfalls #1). The service is cheap and
stateless but for the swap flag (``teamsSwapped``, kept per board on the
class), so the board's widget keeps one and the Web makes one per call.
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
    # A member's stocks and the set length are bounded as the board's
    # spinboxes bounded them (0..99 both: the stock spinbox's range, and
    # QSpinBox's default one for the hidden best-of spinbox it replaced).
    STOCK_MAX = 99
    BEST_OF_MAX = 99
    TEAMS = (1, 2)

    # Board number -> whether its two crews are shown swapped. Not a state
    # key: score.3 has never carried one (boards 1/2's `teamsSwapped` is
    # theirs since S2c), and adding it would change what program_state.json
    # holds. The board's widget used to keep it; the Web reads it
    # (`get_swap`, v1's `swapped`) with or without a widget now, so it lives
    # here, per board rather than per instance. Written under
    # StateManager.lock (SwapTeams), cleared by the launch write.
    _swapped = {}

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

    def SetMemberName(self, team, member, name, stocks=None) -> int:
        """Name one roster slot; returns the crew's recomputed total.

        The recompute is not optional and not the caller's business to
        remember: naming or clearing a slot is precisely what decides whether
        its stocks count, so the pair is one operation and one save cycle.
        `stocks` (the column republished with it) defaults to the state's.
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                if stocks is None:
                    stocks = self.Stocks(team)
                StateManager.Set(
                    self._memberKey(team, member, schema.PLAYER_TAG_KEY),
                    "" if name is None else str(name))
                return self.PublishStocks(team, stocks)

    def SetMemberNum(self, value, stocksPerTeam=None) -> list:
        """Set how many members are in this crew battle; returns the totals.

        Written per team even though there is one count, because that is the
        shape the overlays read (each crew renders its own roster). Both
        totals are recomputed in the same cycle: raising or lowering the
        count changes which rows are summed.

        The number of teams is decided by the length of `stocksPerTeam`, not
        by a constant here -- a crew battle is two-sided by definition, so
        every current caller passes exactly two, but nothing below assumes
        that length. Omitted, it is both crews' stock columns as the state
        holds them.
        """
        value = self._asInt(value, self.DEFAULT_MEMBER_NUM)
        totals = []
        with StateManager.SaveBlock():
            with StateManager.lock:
                if stocksPerTeam is None:
                    stocksPerTeam = [self.Stocks(team) for team in self.TEAMS]
                for index, stocks in enumerate(stocksPerTeam, start=1):
                    StateManager.Set(
                        self._teamKey(index, schema.CREW_MEMBER_NUM_KEY),
                        value)
                    totals.append(self.PublishStocks(index, stocks))
        return totals

    def SetMemberCount(self, value) -> bool:
        """The member count the operator (or the Web's ``players=``) asks
        for, kept to 1..MAX_MEMBERS. True if it changed; an unchanged count
        writes nothing, as the spinbox it replaces emitted nothing."""
        value = max(1, min(self.MAX_MEMBERS,
                           self._asInt(value, self.DEFAULT_MEMBER_NUM)))
        with StateManager.SaveBlock():
            with StateManager.lock:
                if value == self.MemberNum():
                    return False
                self.SetMemberNum(value)
        return True

    # ── reading the board back ───────────────────────────────────────────

    def MemberNum(self) -> int:
        """The member count both crews share (team 1's copy of it)."""
        return self._asInt(
            StateManager.Get(
                self._teamKey(1, schema.CREW_MEMBER_NUM_KEY),
                self.DEFAULT_MEMBER_NUM),
            self.DEFAULT_MEMBER_NUM)

    def Stocks(self, team) -> list:
        """One crew's stock column, every row (index 0 = member 1). A row
        the state has no stock for is a fresh row's DEFAULT_STOCK."""
        return [self._asInt(
                    StateManager.Get(
                        self._memberKey(team, member, schema.CREW_STOCK_KEY),
                        self.DEFAULT_STOCK),
                    self.DEFAULT_STOCK)
                for member in range(1, self.MAX_MEMBERS + 1)]

    def ActiveMember(self, team) -> int:
        """Which member of a crew is playing (0 = nobody)."""
        return self._asInt(
            StateManager.Get(
                self._teamKey(team, schema.CREW_ACTIVE_MEMBER_KEY), 0), 0)

    def ReadTeam(self, team) -> CrewTeam:
        """One crew as the state holds it, in the shape `PublishTeam` writes.

        A colour the state has none for is the crew's default -- what the
        board's colour button showed until a colour was published."""
        def text(key):
            value = StateManager.Get(key, "")
            return "" if value is None else str(value)

        color = StateManager.Get(self._teamKey(team, "color"), "")
        if not isinstance(color, str) or not color:
            color = self.DEFAULT_COLORS[team - 1]
        active = self.ActiveMember(team)
        stocks = self.Stocks(team)
        return CrewTeam(
            name=text(self._teamKey(team, schema.TEAM_NAME_KEY)),
            color=color,
            members=tuple(
                CrewMember(
                    name=text(self._memberKey(
                        team, member, schema.PLAYER_TAG_KEY)),
                    stock=stocks[member - 1],
                    active=member == active)
                for member in range(1, self.MAX_MEMBERS + 1)))

    # ── a member's stocks, the active member ─────────────────────────────

    def _ValidMember(self, team, member):
        team = self._asInt(team, 0)
        member = self._asInt(member, 0)
        if team not in self.TEAMS or not 1 <= member <= self.MAX_MEMBERS:
            return None
        return team, member

    def _WriteStock(self, team, member, compute) -> bool:
        address = self._ValidMember(team, member)
        if address is None:
            logger.debug(f"Crew: ignoring member {member} of team {team}")
            return False
        team, member = address
        with StateManager.SaveBlock():
            with StateManager.lock:
                stocks = self.Stocks(team)
                value = max(0, min(self.STOCK_MAX,
                                   compute(stocks[member - 1])))
                if value == stocks[member - 1]:
                    return False
                stocks[member - 1] = value
                self.PublishStocks(team, stocks)
        return True

    def AdjustMemberStock(self, team, member, delta) -> bool:
        """Take `delta` stocks from / give them to one member, kept to
        0..STOCK_MAX. True if the stock changed.

        Read from the state and written back with the crew's whole column
        and its total, in one save cycle (``PublishStocks``) -- what the
        stock spinbox's change published. A team other than 1/2, a member
        outside 1..MAX_MEMBERS or a change that leaves the stock where it
        is writes nothing.
        """
        delta = self._asInt(delta, 0)
        return self._WriteStock(team, member, lambda stock: stock + delta)

    def SetMemberStock(self, team, member, value) -> bool:
        """One member's stock as typed into the board, kept to
        0..STOCK_MAX. True if it changed (see ``AdjustMemberStock``)."""
        value = self._asInt(value, 0)
        return self._WriteStock(team, member, lambda _stock: value)

    def SelectActiveMember(self, team, member) -> bool:
        """Mark `member` as the one playing for `team`; 0 (or less) for
        nobody. True if it changed.

        One member per crew is playing, so naming one replaces whoever was.
        A team other than 1/2 or a member past MAX_MEMBERS is ignored, and
        the member who is already marked writes nothing.
        """
        team = self._asInt(team, 0)
        member = max(0, self._asInt(member, 0))
        if team not in self.TEAMS or member > self.MAX_MEMBERS:
            logger.debug(f"Crew: ignoring active member {member} of {team}")
            return False
        with StateManager.lock:
            if self.ActiveMember(team) == member:
                return False
            self.SetActiveMember(team, member)
        return True

    def ReleaseActiveMember(self, team, member) -> bool:
        """`member` is no longer playing (the board's marker turned off).
        Only if it is the one marked: a marker a render has not caught up
        with must not clear the member the state has moved on to."""
        address = self._ValidMember(team, member)
        if address is None:
            return False
        team, member = address
        with StateManager.lock:
            if self.ActiveMember(team) != member:
                return False
            self.SetActiveMember(team, 0)
        return True

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

    def ApplyBestOf(self, value) -> bool:
        """The set length a payload or a reset asks for, kept to
        0..BEST_OF_MAX and written only when it changes. True if written.

        What the board's hidden best-of spinbox did with every value it was
        handed (it was the key's only writer until S3e-2): clamp it to its
        range, and emit -- write -- nothing for the value it already held.
        """
        value = max(0, min(self.BEST_OF_MAX, self._asInt(value, 0)))
        return self._SetIfChanged(f"{self.prefix}.best_of", value)

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

    def ApplySetData(self, data):
        """A set payload on the crew board (``set_route``'s fields, a
        ``post_score`` / ``set_team_data`` that lands on board 3): the match
        fields it carries and its set length, in one save cycle. The rest of
        the payload -- entrants, scores -- means nothing here and is
        ignored, so a payload carrying nothing this board understands costs
        no cycle."""
        with StateManager.SaveBlock():
            self.ApplyMatchInfo(data)
            bestOf = self.BestOfFromSetData(data)
            if bestOf is not None:
                self.ApplyBestOf(bestOf)

    # ── the whole board (swap / reset / clear / launch) ──────────────────

    def PublishTeam(self, team, crew: CrewTeam, memberNum) -> int:
        """Write one crew wholesale; returns its recomputed total.

        The bulk operations (swap, reset stocks, clear all) read the board
        from the state, rearrange it and hand the finished board here
        (``_RepublishBoard``), rather than letting each individual write
        broadcast on its own -- a swap that published field by field would
        put a half-swapped board on the stream.

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

    @property
    def teamsSwapped(self) -> bool:
        """Whether this board's crews are shown swapped (see `_swapped`)."""
        return self._swapped.get(self.scoreboardNumber, False)

    def _RepublishBoard(self, change) -> list:
        """Read both crews, hand each to `change(team, crew)` and publish
        what comes back as the whole board, in one save cycle -- the bulk
        operations below. Returns the two totals."""
        with StateManager.SaveBlock():
            with StateManager.lock:
                teams = [change(team, self.ReadTeam(team))
                         for team in self.TEAMS]
                return self.PublishBoard(teams, self.MemberNum())

    def SwapTeams(self) -> list:
        """Exchange the two crews -- name, colour, every member's name,
        stocks and marker -- and flip `teamsSwapped`. Returns the totals.

        Unlike boards 1/2 (``AutonaActions.SwapTeams``) the colour crosses
        over with its crew: that is what the crew board's swap has always
        done. The whole board in one save cycle, so a half-swapped board is
        never broadcast.
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                first, second = (self.ReadTeam(team) for team in self.TEAMS)
                totals = self.PublishBoard([second, first], self.MemberNum())
                self._swapped[self.scoreboardNumber] = not self.teamsSwapped
        return totals

    @classmethod
    def _FreshMembers(cls, crew, keepNames):
        return crew._replace(members=tuple(
            CrewMember(name=member.name if keepNames else "",
                       stock=cls.DEFAULT_STOCK, active=False)
            for member in crew.members))

    def ResetStocks(self) -> list:
        """Every member back to full stocks and nobody playing; the crews'
        names stay (the board's リセット button, the Web's score reset).
        Returns the totals."""
        return self._RepublishBoard(
            lambda _team, crew: self._FreshMembers(crew, keepNames=True))

    def ClearCrew(self) -> list:
        """Every member row emptied -- no name, full stocks, nobody playing
        -- keeping the crews' names and colours (the Web's players' clear).
        Returns the totals."""
        return self._RepublishBoard(
            lambda _team, crew: self._FreshMembers(crew, keepNames=False))

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
            with StateManager.lock:
                # A fresh board is not swapped (the flag is not in the state
                # this write rebuilds, see `_swapped`).
                self._swapped.pop(self.scoreboardNumber, None)
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
