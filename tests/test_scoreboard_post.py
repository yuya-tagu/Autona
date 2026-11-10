"""Boards 1/2's X post (ADR-005 S4x).

The 1v1 / team board's "Post to X" button built its text in the widget
(PostToX). The text and what it is missing are core's now
(AutonaActions.PostText); the button only adds the desktop's part -- the
dialog naming what is empty, the thumbnail on the clipboard, the browser --
and ``GET /api/v1/scoreboards/{n}/post`` answers the same for boards 1/2 (the
crew board's since S4w), guarded as PLAYERDB: the text is of the live
values, 反映 or not, as the button always read them.

The reference below is PostToX's text and checks at 19bbdb56, Qt taken
out; core has to build the same text from the same state on both shells.
"""
import copy
import urllib.parse
from unittest.mock import patch

import pytest
from qtpy.QtWidgets import QApplication, QMessageBox

from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.CrewBattleService import CrewBattleService
from src.core.PlayerService import PlayerService
from src.core.StateManager import StateManager
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_api_v1_crew import _CountedLock
from tests.test_api_v1_score import _ok
from tests.test_web_operations import _goHeadless

TOURNAMENT_KEY = f"{schema.TOURNAMENT_INFO_ROOT}.{schema.TOURNAMENT_NAME_KEY}"
STREAM_KEY = "stream.url"
INTENT = "https://twitter.com/intent/tweet?text="


def _referencePost(n):
    """(text, missing labels) as AutonaScoreboardWidget.PostToX built them
    at 19bbdb56, with the translated labels as their English source."""
    tournament_name = StateManager.Get(TOURNAMENT_KEY, "") or ""
    phase = StateManager.Get(f"score.{n}.phase", "") or ""
    match = StateManager.Get(f"score.{n}.match", "") or ""
    stream_url = StateManager.Get(STREAM_KEY, "") or ""

    def team_has_name(team_n):
        team_state = StateManager.Get(f"score.{n}.team.{team_n}.player") or {}
        for k in team_state.keys():
            name = (StateManager.Get(
                f"score.{n}.team.{team_n}.player.{k}.name") or "").strip()
            if name:
                return True
        return False

    missing = []
    if not team_has_name(1):
        missing.append("Team 1 player name")
    if not team_has_name(2):
        missing.append("Team 2 player name")
    if not (phase.strip() or match.strip()):
        missing.append("Round name")

    def get_team_line(team_n):
        players = []
        team_state = StateManager.Get(f"score.{n}.team.{team_n}.player") or {}
        for k in sorted(team_state.keys(),
                        key=lambda x: int(x) if str(x).isdigit() else 0):
            name = (StateManager.Get(
                f"score.{n}.team.{team_n}.player.{k}.name") or "").strip()
            if not name:
                continue
            seed = StateManager.Get(f"score.{n}.team.{team_n}.player.{k}.seed")
            chars = StateManager.Get(
                f"score.{n}.team.{team_n}.player.{k}.character") or {}
            char_names = []
            for ck in sorted(chars.keys(),
                             key=lambda x: int(x) if str(x).isdigit() else 0):
                char_name = chars[ck].get("name", "")
                if not char_name:
                    continue
                char_names.append(char_name)
            twitter = (StateManager.Get(
                f"score.{n}.team.{team_n}.player.{k}.twitter") or "").strip()
            entry = name
            if twitter:
                handle = twitter.lstrip("@").strip()
                if handle:
                    entry += f" @{handle}"
            if char_names:
                entry += f"({'/'.join(char_names)})"
            if seed:
                entry += f" [Seed:{seed}]"
            players.append(entry)
        return " / ".join(players) if players else "???"

    round_line = f"[{phase}]" if phase else ""
    if match:
        if round_line:
            round_line += f" - {match}"
        else:
            round_line = match

    parts = []
    if tournament_name:
        parts.append(tournament_name)
    if round_line:
        parts.append(round_line)
    parts.append("")
    parts.append(get_team_line(1))
    parts.append("vs")
    parts.append(get_team_line(2))
    parts.append("")
    if stream_url:
        parts.append(f"\U0001f534STREAM: {stream_url}")
    return "\n".join(parts), missing


LABELS = {CrewBattleService.POST_MISSING_TEAM_1: "Team 1 player name",
          CrewBattleService.POST_MISSING_TEAM_2: "Team 2 player name",
          CrewBattleService.POST_MISSING_ROUND: "Round name"}


def _player(name="", twitter="", seed=0, characters=()):
    return {"name": name, "twitter": twitter, "seed": seed,
            "character": {str(i): {"name": character}
                          for i, character in enumerate(characters, 1)}}


# (id, tournament, stream URL, phase, match, team 1 slots, team 2 slots)
SCENARIOS = [
    ("everything", "Cup", "https://twitch.tv/cup", "Pools", "Winners Final",
     {"1": _player("A1", "@a_one", 3, ("Mario", "Luigi")),
      "2": _player("A2", "a_two ", 0, ("Peach",))},
     {"1": _player("B1", "", 12)}),
    ("no stream, no tournament", "", "", "Top 8", "",
     {"1": _player("A1")}, {"1": _player("B1")}),
    ("a round and no phase", "Cup", "", "", "GF",
     {"1": _player("A1")}, {"1": _player("B1")}),
    ("nobody named on a side", "Cup", "", "Pools", "",
     {"1": _player("   "), "2": _player("")}, {"1": _player("B1")}),
    ("nothing", "", "", "", "", {"1": _player()}, {"1": _player()}),
    ("a round of spaces", "", "", "  ", "  ",
     {"1": _player("A1")}, {"1": _player("B1")}),
    ("slots in their number's order", "", "", "P", "M",
     {"10": _player("TEN"), "2": _player("TWO"), "1": _player("ONE")},
     {"1": _player("B1", "@", 0, ("", "Kirby"))}),
    ("a handle of an @ alone and names with spaces", "  Cup  ", "", "P", "",
     {"1": _player("  A 1  ", " @ ")}, {"1": _player("B&1", "@x y")}),
]


@pytest.fixture
def world(autonaApp):
    """The tournament name and the stream URL, put back afterwards."""
    kept = {key: StateManager.Get(key) for key in (TOURNAMENT_KEY, STREAM_KEY)}
    yield
    with StateManager.SaveBlock():
        for key, value in kept.items():
            if value is None:
                StateManager.Unset(key)
            else:
                StateManager.Set(key, value)
    autonaApp.settle()


@pytest.fixture
def bare(autonaApp, monkeypatch, world):
    """No board built (the headless host): any state can be written, with
    no card to render it. The text is a read of the state; which shell holds
    it is the route tests' (below)."""
    _goHeadless(autonaApp, monkeypatch)
    try:
        yield
    finally:
        for number in (1, 2, 3):
            StateManager.Unset(f"score.{number}")
        autonaApp.settle()


@pytest.fixture(params=["desktop", "headless"])
def shell(request, autonaApp, monkeypatch, world):
    """Boards 1 and 2 on the host each shell runs them on: made (held, no
    widget; ADR-005 S4ac-2) on the desktop, seeded headless."""
    if request.param == "desktop":
        autonaApp.ready(1)
        autonaApp.ready(2)
    else:
        _goHeadless(autonaApp, monkeypatch)
    try:
        yield request.param
    finally:
        if request.param == "headless":
            for number in (1, 2, 3):
                StateManager.Unset(f"score.{number}")
            autonaApp.settle()


def _write(app, n, scenario):
    _id, tournament, stream, phase, match, team1, team2 = scenario
    with StateManager.SaveBlock():
        StateManager.Set(TOURNAMENT_KEY, tournament)
        StateManager.Set(STREAM_KEY, stream)
        StateManager.Set(f"score.{n}.phase", phase)
        StateManager.Set(f"score.{n}.match", match)
        for team, slots in ((1, team1), (2, team2)):
            StateManager.Set(f"score.{n}.team.{team}.player",
                             copy.deepcopy(slots))
    app.settle()


def _core(n):
    text, missing = AutonaActions.instance.PostText(n)
    return text, [LABELS[item] for item in missing]


# ── the text ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 2])
@pytest.mark.parametrize("scenario", SCENARIOS,
                         ids=[scenario[0] for scenario in SCENARIOS])
def test_the_text_is_what_the_button_built(autonaApp, bare, n, scenario):
    _write(autonaApp, n, scenario)
    assert _core(n) == _referencePost(n)


def test_the_text_word_for_word(autonaApp, bare):
    _write(autonaApp, 1, SCENARIOS[0])
    assert AutonaActions.instance.PostText(1) == (
        "Cup\n[Pools] - Winners Final\n\n"
        "A1 @a_one(Mario/Luigi) [Seed:3] / A2 @a_two(Peach)\nvs\n"
        "B1 [Seed:12]\n\n\U0001f534STREAM: https://twitch.tv/cup", [])

    _write(autonaApp, 1, SCENARIOS[4])
    assert AutonaActions.instance.PostText(1) == (
        "\n???\nvs\n???\n", ["team1", "team2", "round"])


def test_a_value_that_is_not_text_reads_as_its_text(autonaApp, bare):
    """The button raised on one (its error dialog); core reads it as text."""
    with StateManager.SaveBlock():
        StateManager.Set(TOURNAMENT_KEY, 2024)
        StateManager.Set("score.1.phase", 5)
        StateManager.Set("score.1.match", None)
        StateManager.Set("score.1.team.1.player", {
            "1": {"name": 7, "twitter": 8, "seed": "2",
                  "character": {"1": {"name": 9}, "2": "not a pick"}},
            "2": "not a slot"})
        StateManager.Set("score.1.team.2.player", {"1": {"name": "B"}})
    autonaApp.settle()
    assert AutonaActions.instance.PostText(1) == (
        "2024\n[5]\n\n7 @8(9) [Seed:2]\nvs\nB\n", [])


@pytest.mark.parametrize("board", [0, 3, 4, "x", None])
def test_core_has_no_post_for_another_board(autonaApp, board):
    assert AutonaActions.instance.PostText(board) is None


def test_the_post_is_read_under_one_hold_of_the_lock(autonaApp, bare):
    """A text with nobody's half-written swap in it: a team read before
    and the other after."""
    _write(autonaApp, 1, SCENARIOS[0])
    counted = _CountedLock(StateManager.lock)
    held = []
    get = StateManager.Get

    def spy(key, *args, **kwargs):
        held.append((counted.depth, counted.letGo))
        return get(key, *args, **kwargs)

    with patch.object(StateManager, "lock", counted), \
            patch.object(StateManager, "Get", spy):
        AutonaActions.instance.PostText(1)

    assert held
    assert all(depth >= 1 for depth, _letGo in held), held
    assert len({letGo for _depth, letGo in held}) == 1, held


# ── the route ────────────────────────────────────────────────────────────

def _fill(app, n, phase="Pools", match="Winners Final", names=("A1", "B1"),
          twitter="@a_one", seed=3):
    """Board n's match bar and its first slot of each team, written as the
    board's inputs and the Web write them (core's operations)."""
    actions = AutonaActions.instance
    actions.SetMatchField(n, "phase", phase)
    actions.SetMatchField(n, "match", match)
    for team, name in ((1, names[0]), (2, names[1])):
        player = PlayerService(f"score.{n}.team.{team}.player.1")
        player.SetText("name", name)
        if team == 1:
            player.SetText("twitter", twitter)
            player.SetSeed(seed)
    app.settle()


@pytest.mark.parametrize("n", [1, 2])
def test_the_route_answers_core_s_text_and_missing(
        autonaApp, client, shell, n):
    StateManager.Set(TOURNAMENT_KEY, "Cup")
    StateManager.Set(STREAM_KEY, "https://twitch.tv/cup")
    for fill, missing in (
            (dict(), []),
            (dict(phase="", match=""), ["round"]),
            (dict(names=("", "B1")), ["team1"]),
            (dict(names=("A1", "  ")), ["team2"])):
        _fill(autonaApp, n, **fill)
        text, coreMissing = AutonaActions.instance.PostText(n)
        assert coreMissing == missing
        body = _ok(client.get(f"/api/v1/scoreboards/{n}/post"))
        assert body["data"] == {"text": text, "missing": missing}
        assert body["revision"] == StateManager.revision
        assert (text, [LABELS[item] for item in missing]) \
            == _referencePost(n)
    _fill(autonaApp, n)
    assert _ok(client.get(f"/api/v1/scoreboards/{n}/post"))["data"]["text"] \
        == ("Cup\n[Pools] - Winners Final\n\nA1 @a_one [Seed:3]\nvs\nB1\n\n"
            "\U0001f534STREAM: https://twitch.tv/cup")


def test_the_route_reads_the_values_not_published_yet(
        autonaApp, client, world):
    """On the desktop a board's edits wait behind 反映: the post has them,
    as the button had -- what /state does not answer (why it is guarded)."""
    autonaApp.ready(1)
    _fill(autonaApp, 1)
    AutonaActions.instance.ApplyChanges(1)
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.1") is False

    AutonaActions.instance.SetMatchField(1, "match", "NOT-PUBLISHED-YET")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.1") is True

    post = _ok(client.get("/api/v1/scoreboards/1/post"))["data"]
    assert "NOT-PUBLISHED-YET" in post["text"]
    state = client.get("/api/v1/state").get_data(as_text=True)
    assert "NOT-PUBLISHED-YET" not in state


def test_the_route_builds_no_board_and_writes_nothing(
        autonaApp, client, world):
    from tests.fixtures.write_recording import WriteRecorder
    autonaApp.dropAllBoards()
    with WriteRecorder() as recorder:
        for n in (1, 2):
            _ok(client.get(f"/api/v1/scoreboards/{n}/post"))
        autonaApp.settle()
    assert recorder.sets == []
    assert autonaApp.manager.scoreboards == {}


@pytest.mark.parametrize("n", [0, 4, 99])
def test_a_board_that_is_not_there_has_no_post(autonaApp, client, n):
    _assertRejected(client.get(f"/api/v1/scoreboards/{n}/post"))


# ── the button ───────────────────────────────────────────────────────────

def _click(board, monkeypatch, thumbnail=lambda board: None):
    # The X post's thumbnail is the shell's (AutonaShellOperations.
    # DrawThumbnail, ADR-005 S4y): `thumbnail(board)` stands in for it.
    from src.AutonaShellOperations import AutonaShellOperations
    monkeypatch.setattr(AutonaShellOperations.instance, "DrawThumbnail",
                        thumbnail)
    shown = []
    with patch("webbrowser.open") as opened, \
            patch.object(QMessageBox, "exec",
                         lambda box: shown.append(
                             (box.text(), box.informativeText()))):
        board.xpostBtn.click()
    return opened, shown


def _posted(opened):
    opened.assert_called_once()
    url = opened.call_args[0][0]
    assert url.startswith(INTENT)
    return urllib.parse.unquote(url[len(INTENT):])


@pytest.mark.parametrize("n", [1, 2])
def test_the_button_posts_what_the_route_answers(
        autonaApp, client, world, monkeypatch, n):
    board = autonaApp.board(n)
    StateManager.Set(STREAM_KEY, "https://twitch.tv/cup")
    _fill(autonaApp, n, names=("A 1&", "日本語"))

    opened, shown = _click(board, monkeypatch)

    assert shown == []
    route = _ok(client.get(f"/api/v1/scoreboards/{n}/post"))["data"]
    assert route["missing"] == []
    assert _posted(opened) == route["text"]
    assert opened.call_args[0][0] == INTENT + urllib.parse.quote(route["text"])
    assert _posted(opened) == _referencePost(n)[0]


@pytest.mark.parametrize("fill", [
    dict(names=("  ", "B1")), dict(names=("", ""), phase="", match=""),
    dict(phase="  ", match="  ")], ids=["team 1", "everything", "round"])
def test_the_button_says_what_the_route_says_is_missing(
        autonaApp, client, world, monkeypatch, fill):
    board = autonaApp.board(1)
    _fill(autonaApp, 1, **fill)
    rendered = []

    opened, shown = _click(board, monkeypatch,
                           thumbnail=lambda board: rendered.append(board))

    opened.assert_not_called()
    assert rendered == []
    missing = _ok(client.get("/api/v1/scoreboards/1/post"))["data"]["missing"]
    assert missing
    translate = QApplication.translate
    assert shown == [(
        translate("app", "Cannot post to X: the following fields are empty."),
        "\n".join(f"- {translate('app', LABELS[item])}" for item in missing))]
    assert [LABELS[item] for item in missing] == _referencePost(1)[1]


def test_the_button_posts_the_values_after_the_thumbnail(
        autonaApp, world, monkeypatch):
    """Read again once the thumbnail is drawn: the players' part always was,
    the tournament, round and stream lines too since S4x (the button read
    them before it)."""
    board = autonaApp.board(1)
    _fill(autonaApp, 1)

    def thumbnail(board):
        AutonaActions.instance.SetMatchField(1, "match", "DURING THE RENDER")
        return None

    opened, _shown = _click(board, monkeypatch, thumbnail=thumbnail)
    autonaApp.settle()
    assert "DURING THE RENDER" in _posted(opened)


@pytest.mark.parametrize("n", [1, 2])
def test_the_button_draws_its_own_boards_thumbnail(
        autonaApp, world, monkeypatch, n):
    """Through the shell (ADR-005 S4y: core's ThumbnailService.DrawBoard),
    for the button's board, once -- not through the board's
    GenerateThumbnail."""
    board = autonaApp.board(n)
    _fill(autonaApp, n)
    asked = []

    opened, shown = _click(board, monkeypatch,
                           thumbnail=lambda number: asked.append(number))

    assert asked == [n]
    assert shown == []
    opened.assert_called_once()


def test_a_thumbnail_that_fails_does_not_stop_the_post(
        autonaApp, world, monkeypatch):
    board = autonaApp.board(1)
    _fill(autonaApp, 1)

    def thumbnail(board):
        raise RuntimeError("no render")

    opened, shown = _click(board, monkeypatch, thumbnail=thumbnail)
    assert _posted(opened) == AutonaActions.instance.PostText(1)[0]
    assert shown == [(QApplication.translate(
        "app",
        "Thumbnail generation failed, so no image was copied to the "
        "clipboard."), "no render")]
