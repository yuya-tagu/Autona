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
