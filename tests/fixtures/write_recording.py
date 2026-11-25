"""What an operation's recorded writes are made of (ADR-005 S3e-2).

The commentary and crew operations moved from their panel / board widget
into core (CommentaryService, CrewBattleService). Their recordings --
tests/fixtures/commentary/golden.json and
tests/fixtures/crew_operations/golden.json, made on the last tree before the
move (8aa8c33b) through the widget path -- pin what an operation writes, and
the core path has to write the same:

- every StateManager.Set, in order, with its value as program_state.json
  holds it (orjson, so the key order is compared too);
- every published save cycle (the state_updated notifications, with their
  deltas) and full reloads (state_big_change);
- the subtree afterwards, encoded the same way.

Qt-free on purpose (the core-only replays use it too).
"""
import json
from unittest.mock import patch

import orjson


def encode(value):
    """What program_state.json holds for `value` (StateManager._WriteExports)."""
    return orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")


def encodeDelta(delta):
    """What a Socket.IO client receives for a delta (the web server's
    SocketioJson: the rows carry Python types, sent as their str())."""
    return json.dumps(
        delta, ensure_ascii=False,
        default=lambda obj: str(obj) if isinstance(obj, type) else obj)


class WriteRecorder:
    """Records the writes and publications made while it is active."""

    def __init__(self):
        self.sets = []
        self.deltas = []
        self.bigChanges = 0

    def __enter__(self):
        from src.core import StateManager as module

        state = module.StateManager
        real = state.Set

        def spy(key, value=None, *args, **kwargs):
            self.sets.append([key, encode(value)])
            return real(key, value, *args, **kwargs)

        self._patcher = patch.object(state, "Set", spy)
        self._patcher.start()
        # A test is not a widget: subscribing to the core channels directly
        # is what they are for (unsubscribed on exit).
        self._subs = (
            (state.observers.state_updated, self._onDelta),
            (state.observers.state_big_change, self._onBigChange),
        )
        for channel, callback in self._subs:
            channel.subscribe(callback)
        return self

    def __exit__(self, *exc):
        for channel, callback in self._subs:
            channel.unsubscribe(callback)
        self._patcher.stop()
        return False

    def _onDelta(self, payload):
        self.deltas.append(encodeDelta(payload.get("delta")))

    def _onBigChange(self):
        self.bigChanges += 1

    def Result(self):
        return {"sets": self.sets, "deltas": self.deltas,
                "bigChanges": self.bigChanges}


NOOP_ELIDED = {
    "score.bracket_type": '"DOUBLE_ELIMINATION"',
    # What tests/test_set_follow_flow.py's fixture leaves before it
    # records (the stats recording writes neither).
    "streamQueue": "{}",
    "currentStream": '""',
}

# The head-to-head's loading / cleared values (StatsService's
# MarkRecentSetsLoading / ClearRecentSets). Its answer (UpdateRecentSets,
# with a request_time) is always written.
_RECENT_SETS_ELIDED = (
    encode({"state": "loading", "sets": []}),
    encode({"state": "done", "sets": []}),
)
_PER_PLAYER_STATS = ("last_sets", "history_sets")


def _watchKind(key):
    """Which of a watched key's sets are no longer made when they repeat
    its value, or None for a key that is not watched.

    - "any": bracket_type, a board's upset_factor and the stream queue's
      two roots (StatsService.UpdateStreamQueue) -- what the set follow's
      tick used to rewrite with the same value;
    - "recent": a board's recent_sets, its loading / cleared values only;
    - "player": a board's last_sets.{t} / history_sets.{t}, a clear ({})
      only. The Update* reset writes the same {} and is still made; it is
      told apart by the child writes that follow it (_dropRepeats).

    A board's keys are absolute (score.{n}.…) or board-relative.
    """
    if key in NOOP_ELIDED:
        return "any"
    parts = key.split(".")
    if parts[0] == "score" and len(parts) > 2:
        parts = parts[2:]
    if parts == ["upset_factor"]:
        return "any"
    if parts == ["recent_sets"]:
        return "recent"
    if len(parts) == 2 and parts[0] in _PER_PLAYER_STATS:
        return "player"
    return None


def _related(key, other):
    """`key` is `other`, one of its parents or one of its children."""
    return key == other or other.startswith(key + ".") \
        or key.startswith(other + ".")


_SEED_RUN = ("last_sets.1", "last_sets.2", "history_sets.1",
             "history_sets.2")


def _seedRuns(keys):
    """{index: board prefix} of the writes that start StateSchema.SeedBoard's
    run of empty per-player stats, in its order. A board being made: the
    stats recording holds those keys only, not the board's own write
    before them. A clear goes team by team (last_sets.1, history_sets.1,
    ...), so it never matches."""
    runs = {}
    for index, key in enumerate(keys):
        if key is None or not key.endswith(_SEED_RUN[0]):
            continue
        prefix = key[:-len(_SEED_RUN[0])]
        if keys[index:index + len(_SEED_RUN)] \
                == [prefix + leaf for leaf in _SEED_RUN]:
            runs[index] = prefix
    return runs


def _dropRepeats(writes, last, parse):
    """`writes` without the watched sets that repeat the key's last value
    (they are no longer made: StateManager.SetIfChanged). `parse(write)` is
    (key, value, isUnset); `last` carries the values across steps.

    Any other write -- an unset, a write the writer still makes whatever
    the value, a write of a parent or of a child -- forgets the last value
    of the watched keys it touches: what the state then holds there is not
    known from the recording, so their next write is kept. A board being
    made (_seedRuns) forgets all of its keys."""
    kept = []
    parsed = [parse(write) for write in writes]
    seeds = _seedRuns([None if isUnset else key
                       for key, _value, isUnset in parsed])
    seeding = {index for start in seeds
               for index in range(start, start + len(_SEED_RUN))}
    for index, (write, (key, value, isUnset)) in enumerate(
            zip(writes, parsed)):
        if index in seeds:
            for other in [other for other in last
                          if other.startswith(seeds[index])]:
                del last[other]
        watched = None if isUnset else _watchKind(key)
        elided = watched
        following = parsed[index + 1][0] if index + 1 < len(parsed) else ""
        if index in seeding:
            elided = None  # SeedBoard's Set, made whatever the value
        if watched == "player" and following.startswith(key + "."):
            elided = None  # an Update*'s reset: its children follow
        if watched == "recent" and value not in _RECENT_SETS_ELIDED:
            elided = None  # an answer (UpdateRecentSets)
        if elided is not None and key in last and last[key] == value:
            continue
        for other in [other for other in last if _related(key, other)]:
            del last[other]
        if watched is not None:
            last[key] = value
        kept.append(write)
    return kept


def withoutNoOpWrites(recording):
    """A recording of steps {"writes": [["set"|"unset", key, value?]]}
    without the no-op writes of the watched keys (_watchKind). Both
    recordings (set_follow, stats) start with boards 1 and 2 fresh:
    StateSchema.SeedBoard's empty per-player stats."""
    last = dict(NOOP_ELIDED)
    last.update({f"score.{board}.{stat}.{team}": "{}"
                 for board in (1, 2) for stat in _PER_PLAYER_STATS
                 for team in (1, 2)})
    steps = [dict(step, writes=_dropRepeats(
        step["writes"], last,
        lambda w: (w[1], w[2] if len(w) > 2 else None, w[0] == "unset")))
        for step in recording["steps"]]
    return dict(recording, steps=steps)


def withoutNoOpSets(sets, last):
    """The same for a WriteRecorder's sets [[key, value]]; `last` holds the
    watched keys' values when the recording started (carried across
    calls)."""
    return _dropRepeats(sets, last, lambda s: (s[0], s[1], False))


def withoutNoOpCalls(steps):
    """The same for steps {"calls": [[board-relative key, value]]} (the
    player_apply recording; a None value is an unset). Each scenario starts
    from a fresh board: StateSchema.SeedBoard's empty per-player stats."""
    last = {f"{stat}.{team}": "{}" for stat in _PER_PLAYER_STATS
            for team in (1, 2)}
    return [dict(step, calls=_dropRepeats(
        step["calls"], last, lambda c: (c[0], c[1], c[1] is None)))
        for step in steps]
