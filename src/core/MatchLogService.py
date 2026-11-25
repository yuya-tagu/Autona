"""The match log (ADR-006 A2): what board 1 showed on stream, one record per
match, appended to ``user_data/match_log/YYYYMMDD.jsonl``.

The archive clipper (ADR-006) matches the sets its video analysis finds to
results by the players' names and the score; at a tournament without
start.gg the record of what the scoreboard said is those results (A5 reads
it). It is made from the state as it is published -- the operator's 反映 on
the desktop, every write in the headless shell -- so it is kept whether or
not any page is open, headless too.

What is followed
----------------
Board 1 (1on1) only: ``set_id``, ``phase``, ``match`` (the round),
``teamsSwapped``, each team's ``score`` and its first player's ``name`` (the
tag) and first character's ``codename`` (its ``name`` when it has none: a
character the catalog does not know; the catalog is not read); the loaded
game's codename; and the tournament's display name
(``tournamentInfo.displayTournamentName``, its ``tournamentName`` when that
is empty). Each publish -- ``state_updated``, and ``state_big_change``,
which carries no delta (a set load on the desktop is often one) -- reads
those values as published (``StateManager.GetCommitted``) and compares them
with the open record. The delta rows are not used: a big change has none,
and a change to or from None (``set_id`` when a set is forgotten) is no row.

A record, and when one ends
---------------------------
A record is *played* once a game has been counted in it; *loaded* when it
began with a set's load (or kept the set's id, below).

- A new record starts when another set is loaded (``set_id`` changes to a
  value that is not empty and not the open record's); when both players
  change (both names set before and after, both different); when the score
  goes back to 0-0 from ``RESET_FROM`` (2) games or more; or when a played
  record is at 0-0 -- just reset, or reset before -- with another name on
  the board (the winner staying on for the next match; one name changed in
  the same 反映 as the reset, or typed after it). The open one is closed (a
  last line with ``closed: true``) or, never written, dropped. The new
  record keeps the set's id when it started with the set's load, or when
  the board still shows the id the closed record had -- then only while
  its two names, once both are there, are the closed record's two (either
  side): the same set loaded again keeps it, a match typed over the set
  the board still shows (reset, other names) drops it. Otherwise it has
  none (a match typed by hand: ``set_id`` stays on the board after a
  clear). A game counted with the same two settles it. Known limit: the
  same two retyped one at a time on swapped sides pass through another
  pair (the same name twice) and drop it; the swap button keeps it.
- A score going down is taken as a correction: as many games are taken off
  the end. From one game to 0-0 -- below ``RESET_FROM`` -- that game is set
  aside: the record ending next (another set, both players, another name,
  or the close) puts it back first (the reset after a best-of-1, not a
  correction); a game counted next drops it (it was a mis-click).
- The score's total going up by N in a record with both names already in
  it is N games ended. Each gets the characters as of the publish before
  (a character changed in the same 反映 is the next game's); the new ones
  when there were none before.
- Names and a score arriving together for the first time: in a match typed
  by hand that score is that many games (only the last has its characters,
  the others are not known); in a loaded record it is the score the
  tournament reported, a baseline, no game.
- Before its first game a record is what the board shows. Once played, a
  change to one name, the tournament, the phase, the round or a character
  waits: it is taken in when the next game ends (it was a correction, or
  the characters of that game), or left behind when a new record starts
  (it was the next match's), so a closed line never says the next match.
  Known limit: a name corrected after the last game of a set is never
  taken in (the winner staying on looks the same).
- An empty value never overwrites one (a cleared board, a cleared match
  bar), but the loaded game changing clears the characters (the last
  game's are not this one's). A board with both names empty changes
  nothing but a set change.
- A line's ``game`` is the loaded game's codename (null when none is
  loaded): the game its characters are of. A record takes the game loaded
  when it began, and another game loaded before its first game is counted
  (the game corrected); once it has a game, it keeps it (a set over, the
  next game loaded before the next set is). Known limit: a game switched in
  the middle of a set leaves the games counted after it named in the new
  game, under the first.
- ``teamsSwapped`` flipping swaps the record's sides (names, characters,
  score, every game's, a game set aside), so the swap the state makes is no
  change.

The record is written once it has both names and a game, and then every time
it changes: the whole record as one line (the last line of an ``id`` is the
record), to ``<started date>.jsonl``, the local date it was first written.
An ``id`` is ``YYYYMMDD-HHMMSS-N-XXXX``: the first write's local time, N
from 1 within one second, and four hex digits drawn once per process (two
processes writing the same folder in the same second). The shells' close
(``Shutdown``) ends the open record like a new record starting would (a
game set aside is put back) and closes it -- the boards are reset at
launch, so nothing continues it -- and lets the writer finish. Old files
are not removed.

Threads and locks
-----------------
The observer runs inside ``StateManager.lock`` (the publish that delivers
it). There it reads, updates the record in memory and hands the line to a
writer thread (``AutonaMatchLog``, daemon, started by ``Start``): it writes
no state, waits for nothing and calls no ``GameAssetCatalog`` writer
(pitfalls #16 #17). The record is only touched with
``StateManager.lock`` held (the observer, ``Start``, ``Shutdown``). The
writer's queue has a lock of its own, a leaf: taken after
``StateManager.lock``, never the other way round, and held for no I/O and
no other lock (the writer writes outside it; it never takes
``StateManager.lock``). ``Flush`` and ``Shutdown`` wait for the writer, so
they are not called holding ``StateManager.lock``.

The writer appends and fsyncs each file's batch; a file it first appends to
in this process, or whose last append failed, gets a line break first if it
does not end in one (a line cut short by a crash is not joined to the
next). Lines it cannot write (an OSError: the folder locked,
the disk full) are tried again after ``RETRY_DELAYS``, then dropped with a
warning; at the close, within ``Shutdown``'s time. Any other error drops
that file's lines with a warning and the writer goes on. Text that cannot
be encoded (an unpaired surrogate) is written replaced. No player's name is
logged.

Known gap: a partial commit (a game load's ``CommitHeld`` with ``written``)
publishes board keys while other edits stay pending, and ``GetCommitted``
answers the last published value for the whole board until the next
publish: the record takes such a value one publish late (an empty one --
the characters a game switch cleared -- is not taken anyway).

One instance: ``bootstrap_core()`` makes it after
``StateManager.Initialize()`` (``MatchLogService.instance``) and starts it.
The directory is fixed at construction, against the working directory then.
Nothing is made or read here on import.
"""

import collections
import json
import os
import secrets
import threading
import time
from datetime import datetime

from loguru import logger

from . import StateSchema as schema
from .StateManager import StateManager

MATCH_LOG_DIR = os.path.join(".", "user_data", "match_log")

# The board followed: 1on1's.
BOARD = "1"

# A score back to 0-0 from this many games or more starts a new record; from
# fewer it is a correction (a game counted by mistake taken back).
RESET_FROM = 2

WRITER_THREAD = "AutonaMatchLog"

# The waits before a line that could not be written is tried again, one per
# try; past the last it is dropped.
RETRY_DELAYS = (0.5, 2.0, 5.0)

# What a publish shows of the board, as compared with the record.
_Shown = collections.namedtuple(
    "_Shown",
    "setId swapped game tournament phase round p1 p2 c1 c2 score")

# What a played record takes in only with its next game, as well as a
# single changed name (the same attributes on _Shown and _Record).
HELD_FIELDS = ("tournament", "phase", "round", "c1", "c2")


def _Text(value):
    return "" if value is None else str(value).strip()


def _Score(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _Codename(characters):
    """The first character's codename, or its name when it has none (one
    the catalog does not know). The slot's characters are keyed by int
    (PlayerService.PublishCharacterSlots), by str once read back from a
    file: either."""
    if not isinstance(characters, dict):
        return ""
    character = characters.get(1, characters.get("1"))
    if isinstance(character, dict):
        return (_Text(character.get("codename"))
                or _Text(character.get("name")))
    return ""


def _Name(shown, side):
    return shown.p1 if side == 1 else shown.p2


def _Stamp(moment):
    return moment.isoformat(timespec="seconds")


class _Record:
    """One match, as the record says it. Only touched holding
    StateManager.lock."""

    def __init__(self, shown, setId):
        self.id = None
        self.startedAt = None
        self.updatedAt = None
        self.setId = setId
        # The loaded game's codename: the one the characters are of.
        self.game = shown.game
        # Began with a set's load: the score it came with is the reported
        # one, no games.
        self.loaded = bool(setId)
        self.tournament = shown.tournament
        self.phase = shown.phase
        self.round = shown.round
        self.p1 = shown.p1
        self.p2 = shown.p2
        self.c1 = shown.c1
        self.c2 = shown.c2
        self.score = list(shown.score)
        self.games = []
        # A game has been counted in it (taken back or not).
        self.played = False
        # (score, games) a score going from one game to 0-0 took off.
        self.setAside = None
        # The closed record's two players, while this one keeps its set id
        # on trust: another pair (either side) is another match.
        self.setFrom = None
        # The content of the last line handed to the writer: None until the
        # record is first written.
        self.written = None

    def Name(self, side):
        return self.p1 if side == 1 else self.p2

    def SwapSides(self):
        self.p1, self.p2 = self.p2, self.p1
        self.c1, self.c2 = self.c2, self.c1
        self.score.reverse()
        games = list(self.games)
        if self.setAside is not None:
            score, aside = self.setAside
            score.reverse()
            games += aside
        for game in games:
            game["c1"], game["c2"] = game["c2"], game["c1"]

    def ChangeGame(self, game):
        """Another game loaded: the board's characters are not this game's.
        A record with no game counted takes the new game (the game was
        corrected before play); one with games keeps the game they were
        played in (a set over, the next game loaded before the next set)."""
        self.c1 = self.c2 = ""
        if not self.games and self.setAside is None:
            self.game = game

    def TakeIn(self, shown):
        """The board's non-empty names and held fields."""
        for field in ("p1", "p2") + HELD_FIELDS:
            value = getattr(shown, field)
            if value:
                setattr(self, field, value)

    def CheckSet(self):
        """A set id kept from the closed record stays only for the same two
        players, on either side; another pair drops it (a match typed over
        the set the board still shows)."""
        if self.setFrom is None or not (self.p1 and self.p2):
            return
        if sorted((self.p1, self.p2)) != sorted(self.setFrom):
            self.setId = ""
            self.loaded = False
            self.setFrom = None

    def PutBack(self):
        """The game a 1 -> 0 set aside, back (the record is closing)."""
        if self.setAside is not None:
            score, games = self.setAside
            self.setAside = None
            self.score = score
            self.games += games

    def Content(self):
        """What a change is told by: everything but the times and `closed`,
        copied (the lists change in place)."""
        return {
            "tournament": self.tournament,
            "set_id": self.setId or None,
            "phase": self.phase,
            "round": self.round,
            "p1": self.p1,
            "p2": self.p2,
            "game": self.game or None,
            "c1": self.c1,
            "c2": self.c2,
            "score": list(self.score),
            "games": [dict(game) for game in self.games],
        }

    def Line(self, closed):
        return {
            "id": self.id,
            "board": int(BOARD),
            "started_at": _Stamp(self.startedAt),
            "updated_at": _Stamp(self.updatedAt),
            "closed": closed,
            **self.Content(),
        }


class MatchLogService:
    instance = None

    def __init__(self, directory=None, clock=None):
        self.directory = os.path.abspath(directory or MATCH_LOG_DIR)
        # Local time, with its offset (the line's times say it).
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.retryDelays = RETRY_DELAYS
        # The following, under StateManager.lock.
        self._started = False
        self._seen = None
        self._record = None
        self._lastStamp = None
        self._stampCount = 0
        self._idSuffix = secrets.token_hex(2)
        # The writer's queue. A leaf lock: after StateManager.lock, never
        # before it, and nothing (no I/O, no other lock) inside it.
        self._cond = threading.Condition()
        self._queue = collections.deque()
        self._queued = 0
        self._done = 0
        self._writer = None
        self._stopping = False
        # When the writer stops trying lines again (the close's deadline).
        self._giveUpAt = None
        # The writer's own: the files it has appended to in this process.
        self._appended = set()

    # ── life ─────────────────────────────────────────────────────────────

    def Start(self):
        """Follow the state's publishes. What the board shows now is where
        the following starts (no record yet: the next change makes one)."""
        with self._cond:
            if self._writer is None and not self._stopping:
                self._StartWriter()
        with StateManager.lock:
            if self._started:
                return
            self._started = True
            self._seen = self._Read()
            StateManager.observers.state_updated.subscribe(
                self._OnStatePublished)
            StateManager.observers.state_big_change.subscribe(
                self._OnStatePublished)

    def Stop(self):
        """Stop following (a test replacing the instance). The open record
        and the writer stay."""
        with StateManager.lock:
            if not self._started:
                return
            self._started = False
            StateManager.observers.state_updated.unsubscribe(
                self._OnStatePublished)
            StateManager.observers.state_big_change.unsubscribe(
                self._OnStatePublished)

    def Shutdown(self, timeout=2.0):
        """The shells' close: stop following, close the open record and let
        the writer write what it has, trying failed lines again until most
        of `timeout` has passed. Final: nothing is written after it. Not
        called holding StateManager.lock (it waits for the writer). False if
        the writer had not finished in `timeout` seconds."""
        started = time.monotonic()
        self.Stop()
        with StateManager.lock:
            self._Close(self._clock())
        with self._cond:
            self._stopping = True
            # Leaves the writer a little of the time to finish its last try.
            self._giveUpAt = started + timeout * 0.8
            self._cond.notify_all()
            writer = self._writer
        if writer is None:
            return True
        writer.join(max(0.0, started + timeout - time.monotonic()))
        if writer.is_alive():
            logger.warning("match log: the writer had not finished on close")
            return False
        return True

    def Flush(self, timeout=None):
        """Wait until every line handed to the writer so far is written or
        dropped (logged). Not called holding StateManager.lock. False if
        that took longer than `timeout` seconds, or there is no writer."""
        with self._cond:
            if self._writer is None:
                return not self._queue
            target = self._queued
            return self._cond.wait_for(lambda: self._done >= target, timeout)

    # ── the observer ─────────────────────────────────────────────────────

    def _OnStatePublished(self, *_payload):
        """state_updated / state_big_change: inside StateManager.lock (the
        publish's). Reads, updates the record, queues its line; nothing
        more (see the module docstring)."""
        with StateManager.lock:
            if not self._started:
                return
            shown = self._Read()
            if shown == self._seen:
                return
            previous, self._seen = self._seen, shown
            self._Follow(previous, shown, self._clock())

    @staticmethod
    def _Read():
        """The followed values, as published. Caller holds the lock."""
        get = StateManager.GetCommitted
        board = f"{schema.SCORE_ROOT}.{BOARD}"
        info = schema.TOURNAMENT_INFO_ROOT

        def player(team, key):
            return get(f"{board}.team.{team}.player.1.{key}")

        return _Shown(
            setId=_Text(get(f"{board}.set_id")),
            swapped=get(f"{board}.teamsSwapped") is True,
            game=_Text(get(f"{schema.GAME_ROOT}.codename")),
            tournament=(
                _Text(get(f"{info}.{schema.TOURNAMENT_DISPLAY_NAME_KEY}"))
                or _Text(get(f"{info}.{schema.TOURNAMENT_NAME_KEY}"))),
            phase=_Text(get(f"{board}.{schema.PHASE_KEY}")),
            round=_Text(get(f"{board}.{schema.MATCH_KEY}")),
            p1=_Text(player(1, schema.PLAYER_TAG_KEY)),
            p2=_Text(player(2, schema.PLAYER_TAG_KEY)),
            c1=_Codename(player(1, "character")),
            c2=_Codename(player(2, "character")),
            score=(_Score(get(f"{board}.team.1.score")),
                   _Score(get(f"{board}.team.2.score"))),
        )

    def _Follow(self, previous, shown, now):
        setChanged = bool(shown.setId) and shown.setId != previous.setId
        record = self._record
        if record is None:
            # The first change followed: the record so far is what the
            # board showed when the following started.
            record = self._record = _Record(previous, "")
        if shown.swapped != previous.swapped:
            record.SwapSides()
        if shown.game != previous.game:
            record.ChangeGame(shown.game)
        if self._Begins(record, shown, setChanged):
            self._Close(now)
            inherits = (not setChanged and bool(shown.setId)
                        and shown.setId == record.setId)
            closed, record = record, _Record(
                shown, shown.setId if setChanged or inherits else "")
            if inherits and closed.p1 and closed.p2:
                record.setFrom = (closed.p1, closed.p2)
                record.CheckSet()
            self._record = record
        else:
            self._Update(record, previous, shown, now)
        self._Save(record, now)

    @staticmethod
    def _Begins(record, shown, setChanged):
        """Whether `shown` is another match than `record`."""
        if setChanged and shown.setId != record.setId:
            return True
        renamed = [side for side in (1, 2) if _Name(shown, side)
                   and _Name(shown, side) != record.Name(side)]
        if len(renamed) == 2 and record.p1 and record.p2:
            return True
        if shown.score != (0, 0):
            return False
        if sum(record.score) >= RESET_FROM:
            return True
        # Played, back to 0-0, and a name not the record's: the next match
        # (the winner staying on), not a game taken back.
        return record.played and bool(renamed)

    @staticmethod
    def _Update(record, previous, shown, now):
        if not (shown.p1 or shown.p2):
            # A cleared board says nothing about the match.
            return
        before, after = sum(record.score), sum(shown.score)
        named = bool(record.p1 and record.p2)
        if not record.played:
            record.TakeIn(shown)
            record.CheckSet()
        at = _Stamp(now)
        # A game's characters: the publish's before, unless it had none.
        c1, c2 = previous.c1 or shown.c1, previous.c2 or shown.c2
        if named and after > before:
            record.games.extend(
                {"c1": c1, "c2": c2, "at": at}
                for _ in range(after - before))
            record.played = True
            record.setAside = None
            # Its first game with the same two players: the set's.
            record.setFrom = None
            record.TakeIn(shown)
        elif (not named and record.p1 and record.p2 and after
                and not record.loaded):
            # Typed by hand, its names and a score together at last: that
            # many games, the characters known for the last one only.
            record.games.extend(
                {"c1": "", "c2": "", "at": at} for _ in range(after - 1))
            record.games.append({"c1": c1, "c2": c2, "at": at})
            record.played = True
        elif after < before and record.games:
            taken = record.games[max(0, len(record.games) - (before - after)):]
            del record.games[len(record.games) - len(taken):]
            if after == 0:
                record.setAside = (list(record.score), taken)
        record.score = list(shown.score)

    def _Save(self, record, now):
        """Queue the record's line if it is to be written and changed."""
        if record.written is None and not (
                record.p1 and record.p2 and record.games):
            return
        content = record.Content()
        if content == record.written:
            return
        if record.written is None:
            record.startedAt = now
            record.id = self._NextId(now)
        record.written = content
        record.updatedAt = now
        self._Enqueue(record, closed=False)

    def _Close(self, now):
        """Close the open record (its last line), or drop it unwritten: a
        new record starting, or the close. Either ends the match, so a game
        a 1 -> 0 set aside is put back first. Caller holds
        StateManager.lock."""
        record, self._record = self._record, None
        if record is not None:
            record.PutBack()
        if record is not None and record.written is not None:
            record.updatedAt = now
            self._Enqueue(record, closed=True)

    def _NextId(self, now):
        stamp = now.strftime("%Y%m%d-%H%M%S")
        if stamp == self._lastStamp:
            self._stampCount += 1
        else:
            self._lastStamp, self._stampCount = stamp, 1
        return f"{stamp}-{self._stampCount}-{self._idSuffix}"

    # ── the writer ───────────────────────────────────────────────────────

    def _Enqueue(self, record, closed):
        path = os.path.join(
            self.directory, record.startedAt.strftime("%Y%m%d") + ".jsonl")
        line = json.dumps(record.Line(closed), ensure_ascii=False)
        with self._cond:
            if self._stopping:
                logger.debug("match log: a line after the close, not written")
                return
            self._queue.append((path, line))
            self._queued += 1
            self._cond.notify_all()

    def _StartWriter(self):
        """Caller holds self._cond."""
        self._writer = threading.Thread(
            target=self._Run, name=WRITER_THREAD, daemon=True)
        self._writer.start()

    def _Run(self):
        # Lines to try again: (path, line, tries so far), oldest first.
        again = []
        while True:
            with self._cond:
                if again:
                    self._WaitToTryAgain(again)
                else:
                    self._cond.wait_for(
                        lambda: self._queue or self._stopping)
                batch = again + [(path, line, 0)
                                 for path, line in self._queue]
                self._queue.clear()
                if not batch:
                    return
            failed = self._Write(batch)
            with self._cond:
                late = (self._giveUpAt is not None
                        and time.monotonic() >= self._giveUpAt)
            if late:
                again, dropped = [], failed
            else:
                tries = len(self.retryDelays)
                again = [entry for entry in failed if entry[2] <= tries]
                dropped = [entry for entry in failed if entry[2] > tries]
            self._Drop(dropped)
            with self._cond:
                self._done += len(batch) - len(again)
                self._cond.notify_all()

    def _WaitToTryAgain(self, again):
        """Until the delay of the most tried line has passed, or the
        close's deadline. Caller holds self._cond."""
        tries = max(entry[2] for entry in again)
        due = time.monotonic() + self.retryDelays[tries - 1]
        while True:
            end = due if self._giveUpAt is None else min(due, self._giveUpAt)
            left = end - time.monotonic()
            if left <= 0:
                return
            self._cond.wait(left)

    @staticmethod
    def _Drop(entries):
        counts = collections.Counter(path for path, _line, _tries in entries)
        for path, count in counts.items():
            logger.warning(
                f"match log: {count} line(s) dropped, not written to "
                f"{os.path.basename(path)}")

    def _Write(self, batch):
        """Append the batch's lines, each file's in order. Returns the
        entries an OSError kept from being written, their tries counted;
        any other error drops that file's lines (logged)."""
        files = {}
        for path, line, tries in batch:
            files.setdefault(path, []).append((line, tries))
        failed = []
        for path, entries in files.items():
            name = os.path.basename(path)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                text = "".join(line + "\n" for line, _tries in entries)
                if path not in self._appended:
                    text = self._Separator(path) + text
                with open(path, "a", encoding="utf-8", errors="replace",
                          newline="\n") as f:
                    f.write(text)
                    # One fsync per file (batch), not per line.
                    f.flush()
                    os.fsync(f.fileno())
                self._appended.add(path)
            except OSError as error:
                # A failed append may have left half a line: look at the
                # file's end again before the next append.
                self._appended.discard(path)
                failed += [(path, line, tries + 1)
                           for line, tries in entries]
                logger.warning(
                    f"match log: {len(entries)} line(s) not written to "
                    f"{name} ({type(error).__name__}), to be tried again")
            except Exception as error:
                self._appended.discard(path)
                logger.warning(
                    f"match log: {len(entries)} line(s) dropped, not written "
                    f"to {name} ({type(error).__name__})")
        return failed

    @staticmethod
    def _Separator(path):
        """A line break if the file does not end in one (a line a crash cut
        short): a new line does not join it."""
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    return ""
                f.seek(-1, os.SEEK_END)
                return "" if f.read(1) == b"\n" else "\n"
        except FileNotFoundError:
            return ""
