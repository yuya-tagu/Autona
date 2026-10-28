"""Set loading and following: which set a board shows, and keeping it
current (ADR-005 S3e-7).

Until S3e-7 this was the board widget's: ``NewSetSelected`` decided what a
newly selected set means (the clears, which set is followed and how, the
fetch), two QTimers on the board polled the provider every 5 seconds, the
provider adapter's workers resolved a station / stream to its current set
and answered into the board's signals, and ``ChangeSetData`` poured a
payload into the state. A shell without the board -- the headless one --
could not load a set at all (503), and the answers crossed through a
Worker's own signals object, which could be collected before the main
thread delivered them (the set load silently did nothing).

What is here, per board (`SetFollowService(n)`; the state is class-level,
keyed by board, so the board, the web operations and the timer's tick share
it):

- ``LoadSet(data)``: the logic of ``NewSetSelected``. ``data`` is the set
  window's / the web's / a station's ``{"id", "auto_update", "no_mains",
  ...}``. A set the board does not already show clears the players and the
  score, is recorded as followed (``SetLoadService.BeginSet``) and fetched;
  the answer is applied once it arrives (``UpdateSetData``). With no
  tournament loaded nothing happens: nothing could be fetched, so nothing
  is cleared (the board cleared the players and the score all the same
  until the S3e-7 review).
- polling: while ``general.disable_autoupdate`` is off, every load
  (re)starts a 5-second poll of what it loaded: the set again (an update,
  not a reload), the stream queue, and for a station / stream follow the
  station's current set. The clock is the shell's (``fetcher``); core keeps
  what is polled and asks the clock to follow (``Poll``).
- ``FollowStation(station)``: the station window's station or stream:
  its current set is looked up and loaded. The sets queued behind a
  station are not fetched: nothing showed them (``score.{n}.station_queue``
  stays the ``{}`` it has always been: the recorded tree's answer never
  arrived), so fetching them only added start.gg requests (ADR-005 S3f-3).
- ``PullStream(channel)``: the web's pull of the streamer's channel, which
  its auto-update switch repeats every few seconds. A pull while the board
  follows the channel is a tick -- the channel's set is looked up again and
  loaded only when it is another one; any other pull starts following the
  channel, and loads its set unless the board shows it already (the set
  loaded last: a switch turned on over a set loaded by hand keeps it).
  Following it afresh on every pull cleared the board and fetched the set
  again each time: the score went back to 0, and the board stayed empty for
  as long as the fetch took.
- ``StopFollowing(forget)``: the timer row's cancel button.
- ``ApplySetPayload(data)``: the logic of ``ChangeSetData`` -- a payload
  (a fetched set, a POST /score) into the board's state in one save cycle.

Late answers. Request numbers decide whether an answer still applies, and
each is checked where the board's operations run (the host, below), as the
first thing there -- as is everything that changes them (a load, a follow,
the cancel, the tick). A check and the decision it checks against never
interleave: a web load-set landing right after a lookup's check waits for
the lookup's load to finish, and replaces it.

- a fetched set is dropped when it is not the set the board loaded last
  (``SetLoadService.IsStaleUpdate``, as before), and when a newer full load
  was started since it was asked for (``_loads``): an update answering
  after the operator loaded the set again does not land on the fresh load;
- a station / stream lookup -- a pull's or a tick's, one sequence -- is
  taken only when it is newer than every lookup taken before and nothing
  else decided what the board follows since it was asked for: a set loaded
  by hand or from the web, another station, the cancel button
  (``_lookups``). Two lookups answering in the other order left the older
  one's set on the board; one answering after a set loaded by hand loaded
  the station's set over the operator's.

A full load whose fetch fails -- an answer with no set, an error, no fetch
-- leaves the board cleared for the set and showing none of it. The set is
then no longer recorded as the one loaded (``_LoadFailed``): the next pull,
lookup, tick or the same set chosen again loads it again. It was skipped as
the set already shown, and the board stayed empty.

Threads. Every operation -- what it decides and what it writes -- runs
through the board host (``boardHost()``: the desktop's runs it on the GUI
thread, the headless one under its lock, one at a time with the web's),
the fetcher's answers arrive on the main thread and go through it too, and
nothing here waits for another thread: the fetches are started, never
joined, and no lock of this module is held while calling out (agent-pitfalls
#17).

The fetcher's interface (``AutonaSetFollowUtil``): ``Available()`` says
whether a tournament is loaded; ``Match(setId, onResult, onError)``,
``StreamQueue()``, ``StationSets(stationId, onResult)``, ``StreamSet(name,
onResult)`` start a fetch (False when no tournament is loaded) and call
``onResult(data)`` on the main thread when
it answers -- ``Match``'s ``onError(error)`` when it raised; ``Poll(board)``
makes the board's clock follow
``PollGeneration(board)``; ``FollowChanged(board)`` tells the board what it
follows changed; ``RemainingMs(board)`` is the time to the next
tick, or None.

Import side effects: none (agent-pitfalls #1).
"""
import threading

from loguru import logger

from .Actions import AutonaActions
from .PlayerDB import PlayerDB
from .PlayerService import PlayerService, SlotChange
from .SetLoadService import SetLoadService
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .StatsService import StatsService


class SetFollowService:
    """The set loading and following of one board (1 or 2)."""

    # The interval of the poll, as the board's QTimer had it.
    POLL_MS = 5000

    # Registered by bootstrap_core(): the provider and the clock
    # (AutonaSetFollowUtil), and where a board operation runs -- a callable
    # answering the shell's board host (AutonaWebOperations.boardHost, read
    # when it is used: the desktop registers its own after bootstrap_core()).
    # None: nothing is fetched / the operation runs on the calling thread.
    fetcher = None
    boardHost = None

    _lock = threading.RLock()
    # {board: {"data", "mode", "identifier", "generation"}}: what the poll
    # follows. No entry: not polling.
    _polls = {}
    # {board: station dict}: the station / stream followed (the window's
    # `lastStationSelected`), kept through a set loaded by hand.
    _stations = {}
    # {board: int}: the request number of the newest station / stream
    # lookup taken, or of the latest decision about what the board follows
    # (a set loaded, a follow, the cancel). A lookup's answer is taken only
    # above it (_TakeLookup).
    _lookups = {}
    # {board: int}: the request number of the latest full load.
    _loads = {}
    # Every request number, of every kind and board: one sequence.
    _counter = 0

    def __init__(self, scoreboardNumber):
        self.scoreboardNumber = AutonaActions._asInt(
            scoreboardNumber, scoreboardNumber)
        self.setLoad = SetLoadService(self.scoreboardNumber)

    # ── registrations ────────────────────────────────────────────────────

    @classmethod
    def _Host(cls):
        host = cls.boardHost() if cls.boardHost is not None else None
        return host if host is not None else _CallerThread()

    @classmethod
    def _Fetcher(cls):
        return cls.fetcher if cls.fetcher is not None else _NoFetcher()

    @staticmethod
    def Polling() -> bool:
        """Whether a load starts polling: `general.disable_autoupdate` off.
        The desktop's board turns the setting on whenever it is built (its
        own toggle left the UI), and the settings window's "Disable
        automatic set updating" checkbox turns it off again until the next
        build. A shell that never wrote the key does not poll: there is no
        web operation to stop it."""
        return not SettingsManager.Get("general.disable_autoupdate", True)

    @classmethod
    def _Next(cls):
        with cls._lock:
            cls._counter += 1
            return cls._counter

    def _Bump(self):
        """Something decided what the board follows: every lookup asked for
        until now answers for nothing."""
        number = self._Next()
        with SetFollowService._lock:
            SetFollowService._lookups[self.scoreboardNumber] = number

    def _TakeLookup(self, number):
        """In the host: whether the answer of lookup `number` is still the
        board's to load -- newer than every lookup taken, and asked for
        after the last decision about what the board follows. Taken, it
        drops the older ones still to answer."""
        with SetFollowService._lock:
            if number <= SetFollowService._lookups.get(
                    self.scoreboardNumber, 0):
                return False
            SetFollowService._lookups[self.scoreboardNumber] = number
            return True

    # ── what the board follows (read by the board's timer row) ──────────

    @classmethod
    def PollGeneration(cls, board):
        """The number of the board's running poll, or None when it polls
        nothing. A new number is a new 5 seconds."""
        with cls._lock:
            poll = cls._polls.get(AutonaActions._asInt(board, board))
            return None if poll is None else poll["generation"]

    def Following(self):
        """(mode, identifier) of the running poll -- "set", "stream" or
        "station", and the stream / station it follows -- or None."""
        with SetFollowService._lock:
            poll = SetFollowService._polls.get(self.scoreboardNumber)
            return None if poll is None else (poll["mode"],
                                              poll["identifier"])

    def SecondsLeft(self):
        """Whole seconds to the next tick, or None when not polling."""
        if self.Following() is None:
            return None
        remaining = self._Fetcher().RemainingMs(self.scoreboardNumber)
        if remaining is None or remaining < 0:
            return None
        return int(remaining / 1000)

    def Station(self):
        with SetFollowService._lock:
            return SetFollowService._stations.get(self.scoreboardNumber)

    # ── loading a set (the board's NewSetSelected) ──────────────────────

    def LoadSet(self, data):
        """Show set `data` on the board (see the module docstring), loaded
        by hand or from the web."""
        host = self._Host()
        host.Run(lambda: self._Load(host, data, chosen=True))

    def _Load(self, host, data, chosen=False, lookedUp=False):
        """The load itself, in the host. `chosen`: a set loaded by hand or
        from the web, which a lookup still to answer must not replace.
        `lookedUp`: a lookup's set, the one shown whichever type its id
        comes in (the lookups answer an int, the web loads a str)."""
        fetcher = self._Fetcher()
        board = self.scoreboardNumber
        if not fetcher.Available():
            logger.info(f"score.{board}: no tournament loaded, set "
                        f"{data.get('id')} not loaded")
            return
        setId = data.get("id")
        shown = self.setLoad.lastSetSelected
        if lookedUp:
            loads = setId is not None and str(setId) != str(shown)
        else:
            # `!=` on the raw ids, as before: the set window hands an int,
            # the web a str.
            loads = setId is not None and setId != shown
        if loads and chosen:
            # A lookup still to answer must not replace it. Bumped where
            # the lookups are checked: one checked before this loads its set
            # first, and this replaces it. Only a load that loads decides:
            # the set shown chosen again, or nothing to fetch it from,
            # leaves the follow as it was.
            self._Bump()
        if self.Polling():
            self._StartPoll(data)
        with StateManager.SaveBlock():
            fetcher.StreamQueue()
            if loads:
                noMains = data.get("no_mains") \
                    if data.get("no_mains") is not None else False
                # The previous set's players and score go first: a set's
                # score arrives as 0-0 until it is reported, and is not
                # written as such (ClearScore, not ResetMatch: the round
                # name / phase / pool stay until the new set brings its
                # own, S-17). The clear forgets the set loaded last, which
                # is why it is recorded after it.
                host.ClearPlayers(board, no_mains=noMains)
                AutonaActions.instance.ClearScore(board)
                self.setLoad.lastSetSelected = setId
                station = self.Station()
                self.setLoad.BeginSet(
                    setId, data.get("auto_update"),
                    station.get("identifier") if station else None)
                self._RequestMatch(setId, True, noMains)

    def _RequestMatch(self, setId, overwrite, noMains, tick=False):
        """In the host: fetch set `setId`, a full load (`overwrite`) or an
        update. `tick`: the poll's update, which its next tick waits for
        (Tick)."""
        seq = self._Next()
        if overwrite:
            with SetFollowService._lock:
                SetFollowService._loads[self.scoreboardNumber] = seq
        if tick:
            with SetFollowService._lock:
                poll = SetFollowService._polls.get(self.scoreboardNumber)
                if poll is not None:
                    poll["fetching"] = seq
        started = self._Fetcher().Match(
            setId,
            lambda data: self._OnMatch(seq, setId, data, overwrite, noMains),
            lambda error: self._OnMatchFailed(seq, setId, error))
        if not started:
            self._TickAnswered(seq)
            self._LoadFailed(seq, setId, "nothing to fetch it from")

    def _TickAnswered(self, seq):
        """Fetch `seq` answered (or never started): if it is the poll's
        update its next tick waits for, that tick fetches again."""
        with SetFollowService._lock:
            poll = SetFollowService._polls.get(self.scoreboardNumber)
            if poll is not None and poll.get("fetching") == seq:
                del poll["fetching"]

    def _OnMatch(self, seq, setId, data, overwrite, noMains):
        self._TickAnswered(seq)
        if not isinstance(data, dict) or data.get("id") is None:
            # No set in the answer: the provider's requests failed (it
            # answers what it has, with no id), or it knows no such set.
            self._OnMatchFailed(seq, setId, "the answer has no set")
            return
        data.update({"overwrite": overwrite, "no_mains": noMains})
        self.UpdateSetData(data, _request=seq)

    def _OnMatchFailed(self, seq, setId, error):
        self._TickAnswered(seq)
        self._Host().Run(lambda: self._LoadFailed(seq, setId, error))

    def _LoadFailed(self, seq, setId, error):
        """In the host: the fetch of full load `seq` failed. The board was
        cleared for set `setId` and shows none of it, so the set is no
        longer recorded as loaded: the next pull, lookup or the set chosen
        again loads it again, and so does the next tick of a poll following
        it (an update would be dropped, there being no set to update).
        Nothing for an update, a load another replaced, or a set a clear
        forgot since."""
        board = self.scoreboardNumber
        with SetFollowService._lock:
            if SetFollowService._loads.get(board) != seq:
                return
            poll = SetFollowService._polls.get(board)
        if self.setLoad.lastSetSelected is None or \
                str(self.setLoad.lastSetSelected) != str(setId):
            return
        logger.warning(f"score.{board}: set {setId} was not loaded "
                       f"({error}); it loads again when asked for again")
        self.setLoad.lastSetSelected = None
        if poll is not None and str(poll["data"].get("id")) == str(setId):
            with SetFollowService._lock:
                poll["retry"] = True

    def _Superseded(self, request):
        """Whether a full load was started after fetch `request`."""
        with SetFollowService._lock:
            return request < SetFollowService._loads.get(
                self.scoreboardNumber, 0)

    def UpdateSetData(self, data, _request=None):
        """A fetched set: applied unless the board moved on (see the module
        docstring). With `general.disable_overwrite`, each team's first
        entrant keeps what the local player database knows of them."""
        host = self._Host()

        def run():
            if _request is not None and self._Superseded(_request):
                return None
            if SetLoadService.IsStaleUpdate(
                    data, self.setLoad.lastSetSelected):
                return None
            if SettingsManager.Get("general.disable_overwrite", False):
                for entrant in data.get("entrants"):
                    dbPlayer = PlayerDB.instance.Get(
                        entrant[0].get("gamerTag"))
                    if dbPlayer is not None:
                        entrant[0] = entrant[0] | dbPlayer
            return self.ApplySetPayload(data)

        SaveRecord(host.Run(run))

    # ── polling ──────────────────────────────────────────────────────────

    def _StartPoll(self, data):
        """In the host."""
        mode = data.get("auto_update")
        station = self.Station()
        identifier = station.get("identifier") \
            if station and mode in ("stream", "station") else None
        with SetFollowService._lock:
            SetFollowService._polls[self.scoreboardNumber] = {
                "data": data, "mode": mode, "identifier": identifier,
                "generation": self._Next()}
        fetcher = self._Fetcher()
        fetcher.Poll(self.scoreboardNumber)
        fetcher.FollowChanged(self.scoreboardNumber)

    def Tick(self):
        """The poll's 5 seconds are up: the set again (an update), the
        stream queue, and a station's / stream's current set. A set whose
        load failed is loaded again instead (_LoadFailed).

        Nothing is fetched while the poll's previous update has not
        answered (ADR-005 S3f-2 review): with start.gg slow, each tick
        queued two or three more fetches behind the ones still waiting, on
        a job lane every tournament fetch shares, and the queue only grew.
        Its answer lets the next tick fetch again; a new poll (a load, a
        follow) starts without one."""
        host = self._Host()

        def run():
            board = self.scoreboardNumber
            with SetFollowService._lock:
                poll = SetFollowService._polls.get(board)
                retry = poll is not None and poll.pop("retry", False)
                fetching = None if poll is None else poll.get("fetching")
            if poll is None:
                return
            data = poll["data"]
            if retry and self.setLoad.lastSetSelected is None:
                self._Load(host, data)
                return
            if fetching is not None:
                logger.info(f"score.{board}: the last poll's update has not "
                            "answered yet; this tick fetches nothing")
                return
            self._RequestMatch(data.get("id"), False, False, tick=True)
            self._Fetcher().StreamQueue()
            if data.get("auto_update") in ("stream", "station"):
                self._Resolve()

        host.Run(run)

    def StopFollowing(self, forget=False):
        """Stop polling; with `forget` (the cancel button), also forget the
        station and the followed set -- and the set loaded last with it."""
        def run():
            board = self.scoreboardNumber
            with SetFollowService._lock:
                SetFollowService._polls.pop(board, None)
                if forget:
                    SetFollowService._stations.pop(board, None)
            self._Bump()
            fetcher = self._Fetcher()
            fetcher.Poll(board)
            fetcher.FollowChanged(board)
            if forget:
                self.setLoad.ClearSetTracking()

        self._Host().Run(run)

    # ── stations and streams ─────────────────────────────────────────────

    def FollowStation(self, station):
        """Follow `station` -- ``{"type": "station", "id", "identifier"}``
        or ``{"type": "stream", "identifier": channel}``: its current set is
        looked up and loaded, whatever the board shows now."""
        station = dict(station)
        self._Host().Run(lambda: self._Follow(station))

    def PullStream(self, channel):
        """The web's pull of the streamer's channel `channel` (see the
        module docstring): looked up again as a tick does if the board
        follows it, followed otherwise -- without forgetting the set shown,
        which stays if it is the channel's. Nothing with no tournament
        loaded."""
        def run():
            if not self._Fetcher().Available():
                logger.info(f"score.{self.scoreboardNumber}: no tournament "
                            "loaded, the stream is not pulled")
                return
            station = self.Station()
            if station and station.get("type") == "stream" \
                    and station.get("identifier") == channel:
                # The set shown stays; the lookup loads the channel's only
                # if it is another one.
                self._Resolve()
            else:
                self._Follow({"type": "stream", "identifier": channel},
                             reload=False)

        self._Host().Run(run)

    def _Follow(self, station, reload=True):
        """In the host: follow `station` and look its set up. `reload` (the
        station window): its set is loaded whatever the board shows -- the
        set loaded last is forgotten. A pull keeps it: the board shows the
        channel's set already when it is that one (the auto-update switch
        turned on over a set loaded by hand), and is not cleared for it."""
        if reload:
            self.setLoad.lastSetSelected = None
        with SetFollowService._lock:
            SetFollowService._stations[self.scoreboardNumber] = station
        self._Bump()
        self._Resolve()

    def _Resolve(self):
        """In the host: look the followed station's / stream's set up. Each
        lookup -- a pull's, a tick's, a follow's -- takes the next request
        number: its answer is loaded only if no newer one was."""
        station = self.Station()
        if not station:
            return
        number = self._Next()
        fetcher = self._Fetcher()
        if station.get("type") == "stream":
            fetcher.StreamSet(
                station.get("identifier"),
                lambda node: self._OnResolved(number, station, node))
        else:
            fetcher.StationSets(
                station.get("id"),
                lambda sets: self._OnStationSets(number, station, sets))

    def _OnStationSets(self, number, station, sets):
        host = self._Host()

        def run():
            if not self._TakeLookup(number):
                return
            # Only the first set is loaded; the sets queued behind it are
            # not fetched (see the module docstring).
            first = sets[0] if sets is not None and len(sets) > 0 else None
            self._LoadResolved(host, station, first)

        host.Run(run)

    def _OnResolved(self, number, station, node):
        host = self._Host()

        def run():
            if not self._TakeLookup(number):
                return
            self._LoadResolved(host, station, node)

        host.Run(run)

    def _LoadResolved(self, host, station, node):
        """In the host: the set a lookup found (none: an empty load)."""
        data = dict(node) if node else {}
        data["auto_update"] = station.get("type")
        self._Load(host, data, lookedUp=True)

    # ── a payload into the state (the board's ChangeSetData) ────────────

    def ApplySetPayload(self, data):
        """Apply a set payload to the board (1 or 2), in one save cycle.

        A fetched set, a POST /score: the match information, the scores,
        the set length, the losers flags, the bracket type, and either the
        two entrant rosters or one player (`team` / `player` / `data`). The
        prepared database record of a one-player payload's
        `savePlayerToDb` is returned, for the caller to save once it holds
        no lock (`SaveRecord`); None otherwise.
        """
        host = self._Host()
        board = self.scoreboardNumber
        service = self.setLoad
        record = None
        with StateManager.SaveBlock():
            service.ApplyMatchInfo(data)
            service.ApplyScores(data)

            bestOf = service.BestOfFromSetData(data)
            if bestOf is not None:
                AutonaActions.instance.SetBestOf(board, bestOf)

            # The flag and its team's " [L]" names (ADR-005 S2b). The
            # stream URL is the operator's (the dropdown): not written.
            service.ApplyLosers(data)

            upsetStale = False
            if data.get("entrants"):
                upsetStale = self._ApplyEntrants(host, data)
            else:
                applied, record = self._ApplySinglePlayer(host, data)
                if not applied:
                    # Neither entrants nor a team / player: nothing more to
                    # apply -- the bracket type included, as before (a
                    # payload carrying a bracket type carries entrants).
                    return None

            # The upset factor of the bracket type / roster this set brought.
            if service.ApplyBracketType(data) or upsetStale:
                StatsService(board).RefreshQuietly(StatsService.UPSET_FACTOR)
        return record

    def _SetRoster(self, host, players):
        """The roster a set's entrants need, with the characters per player
        the board shows: a board with no character slot takes its count from
        the host (the board's spinbox), and one losing its last player keeps
        the count it had for the next ones (AutonaWebOperations._set_players'
        rule; the board passed its spinbox's value)."""
        actions = AutonaActions.instance
        board = self.scoreboardNumber
        characters = actions.CharacterSlots(board)
        if characters is None:
            characters = host.EmptyRosterCharacters(board)
        elif players <= 0:
            host.KeepEmptyRosterCharacters(board, characters)
        actions.SetRoster(board, players, characters)

    def _ApplyEntrants(self, host, data) -> bool:
        """Pour a set's two entrant rosters into the player slots.

        True when the upset factor is left to recalculate: the roster went
        from one player a side to several or back -- the factor is the
        seeds' distance in singles and 0 otherwise (StatsService.IsSingles)
        -- and no player's seed moved, which would have recalculated it
        already (PlayerService.Announce).
        """
        entrants = data.get("entrants")
        stats = StatsService(self.scoreboardNumber)
        wasSingles = stats.IsSingles()
        # The slots the larger side needs; the board's cards follow once the
        # save block is over -- the players load into the state's slots.
        self._SetRoster(host, len(max(entrants, key=lambda x: len(x))))
        singlesChanged = stats.IsSingles() != wasSingles
        seedMoved = False

        try:
            # `!=` kept exactly: no_mains comes from a provider payload, and
            # `1 != True` is False in Python.
            noMains = data.get("no_mains")
            if noMains is None:
                noMains = False
            for t, team in enumerate(entrants):
                stateTeam = self.setLoad.DisplayTeam(t + 1)

                if len(team) > 1:
                    # The team name and the crest that follows from it.
                    self.setLoad.ApplyTeamName(
                        t + 1, data.get(f"p{t + 1}_name"))

                for p, player in enumerate(team):
                    change = SlotChange()
                    if data.get("overwrite"):
                        # The full player dict carries the mains; the
                        # mains-only load is for the non-overwrite case.
                        change = self._ApplyPlayer(
                            host, stateTeam, p + 1, player, True, True,
                            noMains)
                    elif data.get("has_selection_data") and noMains != True:
                        change = self._ApplyPlayer(
                            host, stateTeam, p + 1,
                            {"mains": player.get("mains")}, False, False)
                    seedMoved = seedMoved or change.seedChanged
        except Exception as e:
            logger.error(f"Error while setting entrants: {e}")
        return singlesChanged and not seedMoved

    def _ApplySinglePlayer(self, host, data):
        """A one-player payload (POST /score's `team` / `player` / `data`):
        (False, None) when it names no team or player.

        The state team is addressed directly (no swap flip): the caller
        names the side it is looking at. A slot the state does not have --
        a team other than 1 / 2, a player outside 1..the roster, "0" and
        negative numbers included -- is logged and nothing is written, as
        the legacy update-team route does (it used to be a list index, and
        "0" wrote team 2 / the last player). So is a load that raises.
        Applied all the same: the bracket type still follows.
        """
        team, player = data.get("team"), data.get("player")
        if not team or not player:
            return False, None
        board = self.scoreboardNumber
        record = None
        try:
            team_, player_ = int(team), int(player)
            if team_ not in (1, 2) or not \
                    1 <= player_ <= AutonaActions.RosterCount(board, team_):
                raise IndexError(f"team {team} has no player {player}")
            payload = data.get("data")
            self._ApplyPlayer(host, team_, player_, payload, True, False)
            if payload.get("savePlayerToDb", False):
                record = PlayerService(
                    _SlotPath(board, team_, player_)).PrepareSaveToDB()
        except Exception as e:
            logger.error(f"post_score: scoreboard {board} team {team} "
                         f"player {player} not written: {e}")
        return True, record

    def _ApplyPlayer(self, host, team, slot, data, loadFromDB, clear,
                     no_mains=False) -> SlotChange:
        """Load a player into slot `slot` of state team `team`
        (PlayerService.ApplyData, which asks for its statistics too), in one
        save cycle, telling the host (the desktop's card drops the text
        typed into its combos after a load that cleared)."""
        service = PlayerService(_SlotPath(self.scoreboardNumber, team, slot))
        with StateManager.SaveBlock():
            try:
                return service.ApplyData(data, loadFromDB, clear, no_mains)
            finally:
                host.PlayerLoaded(self.scoreboardNumber, team, slot, clear)

    # ── tests ────────────────────────────────────────────────────────────

    @classmethod
    def ForgetBoard(cls, board=None):
        """Drop what board `board` (every board: None) follows and loaded,
        as a board built afresh had none, and every answer still in flight
        for it. The app never drops a board; the test harness rebuilds
        them."""
        with cls._lock:
            for store in (cls._polls, cls._stations):
                if board is None:
                    store.clear()
                else:
                    store.pop(AutonaActions._asInt(board, board), None)
            numbers = ({AutonaActions._asInt(board, board)}
                       if board is not None else
                       set(AutonaActions.SCORE_BOARDS) | set(cls._lookups)
                       | set(cls._loads))
            # Moved on, not dropped: a number back at 0 would let a lookup
            # or a fetch asked for before pass.
            for number in numbers:
                cls._lookups[number] = cls._Next()
                cls._loads[number] = cls._Next()
        SetLoadService.ForgetLastSets(board)
        if cls.fetcher is not None:
            for number in ((board,) if board is not None
                           else AutonaActions.SCORE_BOARDS):
                cls.fetcher.Poll(AutonaActions._asInt(number, number))


def SaveRecord(prepared):
    """The player database half of a one-player payload's `savePlayerToDb`
    (PlayerService.SaveRecordToDB), out of every lock: the database's change
    relay waits for the main thread (ADR-005 S3e-4 review)."""
    if prepared is not None:
        PlayerService.SaveRecordToDB(prepared)


def _SlotPath(board, team, player):
    return f"score.{board}.team.{team}.player.{player}"


class _CallerThread:
    """No board host registered: the operation runs here, nothing to tell."""

    def Run(self, op):
        return op()

    def ClearPlayers(self, board, no_mains=False):
        AutonaActions.instance.ClearPlayers(board, no_mains)
        SetLoadService(board).lastSetSelected = None

    def EmptyRosterCharacters(self, board):
        return None

    def KeepEmptyRosterCharacters(self, board, count):
        pass

    def PlayerLoaded(self, board, team, player, cleared):
        pass


class _NoFetcher:
    """No fetcher registered: nothing is fetched, and nothing ticks."""

    def Available(self):
        return False

    def Match(self, setId, onResult, onError=None):
        return False

    def StreamQueue(self):
        return False

    def StationSets(self, stationId, onResult):
        return False

    def StreamSet(self, name, onResult):
        return False

    def Poll(self, board):
        pass

    def FollowChanged(self, board):
        pass

    def RemainingMs(self, board):
        return None
