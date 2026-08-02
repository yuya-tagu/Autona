"""The stats routes, driven through the real WebServerActions.

`AutonaStatsUtil` is per-board -- one instance per AutonaScoreboardWidget,
writing under `score.{n}.*`. Upstream TSH built a single instance at import
time and exposed it as `AutonaStatsUtil.instance`; the fork's very first
commit made the constructor take (scoreboardNumber, scoreboard) and dropped
that assignment, but two WebServerActions handlers kept calling
`AutonaStatsUtil.instance.signals...`. Since `instance` was never assigned
again, `/scoreboard<n>-stats-recent-sets` and
`/scoreboard<n>-stats-upset-factor` raised AttributeError on None for every
request -- and ignored the board number they were given.

These tests drive the same objects an HTTP request reaches
(`autonaApp.actions()` is what every route delegates to) and assert on
StateManager, which is what the OBS overlays consume.
"""
import pytest

from src.AutonaStatsUtil import AutonaStatsUtil


# ── the singleton must stay gone ─────────────────────────────────────────

def test_stats_util_exposes_no_class_level_instance():
    """Reintroducing `instance` would revive the None-deref path.

    Nothing can assign it meaningfully: an AutonaStatsUtil belongs to one
    board, so a class attribute could only ever be whichever board happened
    to be built last.
    """
    assert not hasattr(AutonaStatsUtil, "instance")


def test_every_board_owns_its_own_stats(autonaApp):
    board1 = autonaApp.board(1)
    board2 = autonaApp.board(2)

    assert board1.stats is not board2.stats
    assert board1.stats.scoreboardNumber == 1
    assert board2.stats.scoreboardNumber == 2


# ── upset factor ─────────────────────────────────────────────────────────

def _seed(app, board, team, value):
    app.state.Set(f"score.{board}.team.{team}.player.1.seed", value)


def test_upset_factor_route_calculates_for_the_board(autonaApp):
    """Pre-fix this raised AttributeError before touching any state."""
    autonaApp.board(1)
    _seed(autonaApp, 1, 1, 1)
    _seed(autonaApp, 1, 2, 5)

    assert autonaApp.actions().stats_upset_factor("1") == "OK"
    autonaApp.settle()

    # Double elimination, seeds 1 vs 5: |0 - (2 + 2)| = 4.
    assert autonaApp.get("score.1.upset_factor") == 4


def test_upset_factor_route_resets_when_a_seed_is_missing(autonaApp):
    autonaApp.board(1)
    _seed(autonaApp, 1, 1, 1)
    _seed(autonaApp, 1, 2, 5)
    autonaApp.actions().stats_upset_factor("1")
    autonaApp.settle()
    assert autonaApp.get("score.1.upset_factor") == 4

    # Player 2 cleared: the previous pairing's factor must not linger.
    autonaApp.state.Set("score.1.team.2.player.1.seed", None)
    autonaApp.actions().stats_upset_factor("1")
    autonaApp.settle()

    assert autonaApp.get("score.1.upset_factor") == 0


# ── recent sets ──────────────────────────────────────────────────────────

def test_recent_sets_route_settles_without_a_tournament(autonaApp):
    """No provider is loaded here, which is also the day-to-day case.

    The handler must report "done" with no sets rather than raise -- the
    provider is None until a tournament URL is set, and the route is
    reachable (overlay refresh button, external API) at any time.
    """
    autonaApp.board(1)

    assert autonaApp.actions().stats_recent_sets("1") == "OK"
    autonaApp.settle()

    assert autonaApp.get("score.1.recent_sets") == {"state": "done", "sets": []}


# ── the board number is honoured ─────────────────────────────────────────

@pytest.mark.parametrize("board", ["1", "2"])
def test_stats_routes_address_the_requested_board(autonaApp, board):
    """The `scoreboard` argument used to be accepted and then ignored."""
    autonaApp.board(int(board))
    other = "2" if board == "1" else "1"
    autonaApp.state.Unset(f"score.{other}.recent_sets")

    autonaApp.actions().stats_recent_sets(board)
    autonaApp.settle()

    assert autonaApp.get(f"score.{board}.recent_sets.state") == "done"
    assert autonaApp.get(f"score.{other}.recent_sets") is None


# ── the socket.io wrappers call these with the right arity ───────────────

def test_socketio_stats_handlers_pass_only_the_board_number():
    """`stats_recent_sets` / `stats_upset_factor` take (scoreboard) only.

    The Socket.IO wrappers were copied from the last_sets/history_sets pair
    and kept passing `info.get("player")` as a second positional argument,
    which is a TypeError on these two.
    """
    import inspect
    import re

    from src import AutonaWebServer

    source = inspect.getsource(AutonaWebServer)
    for handler in ("stats_recent_sets", "stats_upset_factor"):
        calls = re.findall(
            r"WebServer\.actions\.%s\((.*?)\)\)" % handler, source)
        assert calls, f"no WebServer.actions.{handler}(...) call found"
        for arguments in calls:
            assert "player" not in arguments, (
                f"{handler} takes only the board number, got: {arguments}")
