"""End-to-end tests against a real, finished start.gg tournament.

The rest of the suite proves the app is self-consistent. This module proves
it survives contact with the API: the shapes start.gg actually returns, the
URLs it actually hands out for avatars, the fields it silently omits. Every
assertion below is about data no fixture invented.

Skipped unless AUTONA_LIVE_TESTS=1 (see conftest.py).
"""
import copy
import time
from pathlib import Path

import pytest

from tests.live.conftest import EVENT_URL, live_only, runRestartProbe

pytestmark = live_only

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Sanity window for a start.gg epoch: after 2014 (the site's own launch era)
# and before 2050. Wide on purpose -- this catches a milliseconds-vs-seconds
# or string-vs-int regression, not a wrong date.
_EPOCH_FLOOR = 1_400_000_000
_EPOCH_CEILING = 2_500_000_000


# ── T1: the event itself ─────────────────────────────────────────────────

def test_the_event_metadata_is_the_real_tournament(liveEvent):
    data = liveEvent.tournament

    assert data.get("tournamentName"), "no tournament name came back"
    assert "BeeSmash" in data["tournamentName"], (
        f"pinned event now resolves to {data['tournamentName']!r}")
    assert data.get("eventName")

    assert isinstance(data.get("numEntrants"), int)
    assert data["numEntrants"] > 0

    # Super Smash Bros. Ultimate. GetTournamentData hands the id back under
    # _videogame for the caller to emit game_changed from the GUI thread,
    # and caches it on the provider for SetGameFromStartGGId.
    assert data.get("_videogame") == 1386
    assert liveEvent.provider.videogame == 1386

    startAt, endAt = data.get("startAt"), data.get("endAt")
    for name, value in (("startAt", startAt), ("endAt", endAt)):
        assert isinstance(value, int), f"{name} is {value!r}"
        assert _EPOCH_FLOOR < value < _EPOCH_CEILING, f"{name}={value}"
    assert endAt >= startAt
    assert endAt < time.time(), "the pinned event is supposed to be finished"

    # The headcount the provider caches is what anchors TOP-N round naming.
    assert liveEvent.provider.numEntrants == data["numEntrants"]


def test_the_event_start_time_is_reported_but_the_end_time_is_not(liveEvent):
    """Event-level endAt is a known hole in start.gg's internal endpoint.

    The query asks for event.endAt and the response simply omits the field,
    so GetTournamentData's deep_get default turns it into "". The tournament
    -level endAt (asserted above) is the one with real data. Pinned because
    anything that starts *computing* with eventEndAt has to handle "".
    """
    data = liveEvent.tournament
    assert isinstance(data.get("eventStartAt"), int)
    assert data.get("eventEndAt") == "", (
        f"start.gg started returning event.endAt ({data.get('eventEndAt')!r})"
        " -- consumers assuming '' can be relaxed")


def test_the_event_reports_its_phases(liveEvent):
    phases = liveEvent.phases
    assert phases, "no phases came back"

    for phase in phases:
        assert isinstance(phase.get("id"), int), phase
        assert phase.get("name"), phase
        assert isinstance(phase.get("groups"), list), phase
        for group in phase["groups"]:
            assert isinstance(group.get("id"), int), group
            assert group.get("name"), group
            assert group.get("bracketType"), group


# ── T2: the set list ─────────────────────────────────────────────────────

def test_every_finished_set_parses_into_a_usable_shape(liveEvent):
    """One report for the whole bracket, not a failure on the first bad set.

    A single malformed set out of thirty is exactly the kind of thing that
    only shows up on a real event, and knowing whether it is one set or all
    of them is most of the diagnosis.
    """
    sets = liveEvent.sets
    assert sets, "no sets came back for a finished tournament"

    problems = []

    def complain(_set, message):
        problems.append(f"set {_set.get('id')}"
                        f" ({_set.get('round_name_en')!r}): {message}")

    for _set in sets:
        if not isinstance(_set.get("id"), int):
            complain(_set, f"id is {_set.get('id')!r}")
        for side in ("p1_name", "p2_name"):
            if not _set.get(side):
                complain(_set, f"{side} is empty")
        if not _set.get("round_name"):
            complain(_set, "round_name is empty")
        if not _set.get("round_name_en"):
            complain(_set, "round_name_en is empty")
        if not _set.get("tournament_phase"):
            complain(_set, "tournament_phase is empty")

        bestOf = _set.get("bestOf")
        if bestOf is not None and not isinstance(bestOf, int):
            complain(_set, f"bestOf is {bestOf!r}")

        entrants = _set.get("entrants")
        if not isinstance(entrants, list) or len(entrants) != 2:
            complain(_set, f"entrants is {entrants!r}")
            continue
        for index, team in enumerate(entrants, start=1):
            if not isinstance(team, list) or not team:
                complain(_set, f"team {index} is {team!r}")
                continue
            for player in team:
                if not player.get("gamerTag"):
                    complain(_set, f"team {index} player has no gamerTag:"
                                   f" {player!r}")

    assert problems == [], (
        f"{len(problems)} problem(s) across {len(sets)} sets:\n  "
        + "\n  ".join(problems))


def test_the_set_list_loses_no_page_and_repeats_no_set(liveEvent):
    """GetMatches pages concurrently and dedupes; both halves must hold.

    The concurrent fetch has one retry round for failed pages and then logs
    and moves on -- a dropped page is a silently short set list, which on
    stream looks like "that match isn't in the list" and nothing else.
    """
    ids = [_set.get("id") for _set in liveEvent.sets]

    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"the same set id came back twice: {duplicates}"

    assert liveEvent.reportedTotal is not None, (
        "could not read pageInfo.total to compare against")
    assert len(ids) == liveEvent.reportedTotal, (
        f"start.gg reports {liveEvent.reportedTotal} sets for these filters,"
        f" GetMatches returned {len(ids)}")


def test_the_set_list_carries_no_best_of(liveEvent):
    """Documented asymmetry, not an oversight.

    StartGGSetsQuery.txt does not request bestOf, so a set picked from the
    list has none; only GetMatch's legacy-endpoint half reports it. Anything
    that reads bestOf straight off a list entry is reading None.
    """
    assert all(_set.get("bestOf") is None for _set in liveEvent.sets)


# ── T3: a real set on a real scoreboard ──────────────────────────────────

def test_loading_a_real_set_fills_the_scoreboard(autonaApp, liveEvent,
                                                 realSetOnBoard):
    data = realSetOnBoard.data
    get = autonaApp.get

    assert get("score.1.match") == data["round_name"]
    assert get("score.1.match_en") == data["round_name_en"]
    assert get("score.1.phase") == data["tournament_phase"]
    # Single-group phase -> the pool name is cleared, not left stale.
    assert get("score.1.pool") == data["tournament_pool"] == ""

    assert int(get("score.1.team.1.score")) == data["team1score"]
    assert int(get("score.1.team.2.score")) == data["team2score"]

    for teamIndex in (1, 2):
        player = data["entrants"][teamIndex - 1][0]
        path = f"score.1.team.{teamIndex}.player.1"

        # SetData censors the tag in place, so compare against the dict the
        # widget was actually handed rather than the pre-call value.
        assert get(f"{path}.name") == player["gamerTag"], (
            f"team {teamIndex} got the wrong player")
        assert get(f"{path}.name"), "a real entrant tag must not be empty"

        if player.get("seed"):
            assert int(get(f"{path}.seed")) == int(player["seed"])
        if player.get("twitter"):
            assert get(f"{path}.twitter") == player["twitter"]
        if player.get("prefix"):
            assert get(f"{path}.team") == player["prefix"]

    assert get("score.bracket_type") == data["bracket_type"]


def test_the_real_set_carries_both_entrants_seeds(liveEvent):
    """Seeds come from the set query, not from the entrant cache.

    _parse_participant_new_api prefers setData["p{n}_seed"]
    (slot.entrant.initialSeedNum) over the player_seeds map, which is only
    populated by a full entrant fetch. A set loaded before that fetch
    finishes still has to show seeds.
    """
    match = liveEvent.match
    assert match, "no set detail came back"
    for index, team in enumerate(match["entrants"], start=1):
        assert team, f"team {index} is empty"
        seed = team[0].get("seed")
        assert isinstance(seed, int) and seed > 0, (
            f"team {index} seed is {seed!r}")


def test_an_offline_events_best_of_is_dropped_on_purpose(autonaApp, liveEvent):
    """GetMatch nulls bestOf for offline events; the scoreboard keeps its own.

    The legacy endpoint reports a bestOf for every set, but for an offline
    bracket that value is the ruleset default rather than what the players
    agreed to at the setup, so GetMatch discards it. The consequence worth
    pinning is at the widget: ChangeSetData must then leave the operator's
    current best_of alone instead of writing None over it.
    """
    match = liveEvent.match
    assert match.get("isOnline") is False, (
        "the pinned event is supposed to be an offline bracket")
    assert match.get("bestOf") is None

    legacy = liveEvent.legacy
    assert isinstance(legacy.get("bestOf"), int), (
        "precondition: the legacy endpoint does report a bestOf"
        f" (got {legacy.get('bestOf')!r})")

    board = autonaApp.board(1)
    before = autonaApp.get("score.1.best_of")

    data = copy.deepcopy(match)
    data.update({"overwrite": True, "no_mains": False})
    board.ChangeSetData(data)
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == before


def test_the_set_detail_reports_no_character_selections(liveEvent):
    """An offline bracket has no per-game character data, and that is fine.

    Character mains reach the scoreboard through the legacy endpoint's
    setTask stream, which only exists for events reported through start.gg's
    in-app flow. This pins that the offline case degrades to "no mains"
    rather than to an exception. It is also why this event cannot cover
    LIVE-BUG-3, where _build_entrants_old_api indexed
    GetCharacterFromStartGGId(...)[0] with no None guard: a set that *did*
    carry selections while no game asset pack is installed took the whole
    legacy half down with it. That one is covered offline, in
    tests/test_startgg_parse.py::TestOldApiUnknownCharacterIds.
    """
    match = liveEvent.match
    assert match.get("has_selection_data") is None
    assert liveEvent.legacy.get("entrants") == [[], []]
    for team in match["entrants"]:
        for player in team:
            assert player.get("mains") is None


# ── T4: save, restart, restore ───────────────────────────────────────────

def test_a_restart_restores_the_tournament_and_refetches(autonaApp, liveEvent,
                                                         realSetOnBoard,
                                                         committedBoard):
    """The full cache cycle, across a real process boundary.

    What an operator does between two streams: load a tournament, load a
    set, close the app, open it again. The second process must come back
    with the tournament info still on screen, the scoreboard *empty* (not
    showing yesterday's set), the URL still in settings, and a fresh fetch
    against the live API on startup.
    """
    from src.core.SettingsManager import SettingsManager

    state = autonaApp.state
    tournamentName = liveEvent.tournament["tournamentName"]
    numEntrants = liveEvent.tournament["numEntrants"]

    realSettings = PROJECT_ROOT / "user_data" / "settings.json"
    before = realSettings.stat() if realSettings.exists() else None

    previousUrl = SettingsManager.Get("TOURNAMENT_URL")
    try:
        state.Set("tournamentInfo.tournamentName", tournamentName)
        state.Set("tournamentInfo.numEntrants", numEntrants)
        SettingsManager.Set("TOURNAMENT_URL", EVENT_URL)

        # Abort before spawning anything if the write went to the real
        # installation: the next line would hand a live process the
        # developer's own settings file.
        runtimeSettings = autonaApp.dir / "user_data" / "settings.json"
        assert runtimeSettings.is_file(), "settings were not written at all"
        assert EVENT_URL in runtimeSettings.read_text(encoding="utf-8")
        after = realSettings.stat() if realSettings.exists() else None
        assert (before is None) == (after is None)
        if before is not None:
            assert (before.st_mtime_ns, before.st_size) == (
                after.st_mtime_ns, after.st_size), (
                "the real user_data/settings.json was modified -- stop")

        # The scoreboard subtree is held until 反映, so publish it the way
        # the button does before asserting on what got persisted.
        committedBoard(1)

        persisted = autonaApp.dir / "out" / "program_state.json"
        assert persisted.is_file()
        import orjson
        saved = orjson.loads(persisted.read_bytes())
        assert saved["tournamentInfo"]["tournamentName"] == tournamentName
        assert saved["score"]["1"]["team"]["1"]["player"]["1"]["name"]

        result, completed = runRestartProbe(autonaApp.dir)
    finally:
        if previousUrl is None:
            SettingsManager.Unset("TOURNAMENT_URL")
        else:
            SettingsManager.Set("TOURNAMENT_URL", previousUrl)

    assert result.get("persisted_read_error") is None, result

    # (a) the persisted tree came back
    assert result["restored_tournament_name"] == tournamentName
    assert result["restored_num_entrants"] == numEntrants

    # (b) ...but the scoreboard did not. AutonaScoreboardManager.__init__
    # unsets score.* on purpose: a board rebuilt from a stale file would
    # show the previous session's players with no set loaded.
    assert result["persisted_score_keys"], (
        "precondition: score.* really was in the file the probe read")
    assert all(result["persisted_player_names"]), result["persisted_player_names"]
    assert result["restored_score"] is None, (
        f"score survived the restart: {result['restored_score']!r}")

    # (c) the URL is in the settings file, not only in the previous process
    assert result["settings_url"] == EVENT_URL

    # (d) UiMounted() re-fetched it from the live API
    assert result["refetched"], (
        "UiMounted() never produced tournament data\n"
        f"stderr tail:\n{completed.stderr[-2000:]}")
    assert result["refetched_tournament_name"] == tournamentName
    assert result["refetched_num_entrants"] == numEntrants
    assert result["refetched_initial_load"] is True
    assert result["provider_url"] == EVENT_URL


# ── T5: avatars actually land on disk ────────────────────────────────────

def _avatarUrls(match):
    """(team, player, url) for every entrant the API gave an avatar."""
    found = []
    for teamIndex, team in enumerate(match["entrants"], start=1):
        for playerIndex, player in enumerate(team, start=1):
            url = player.get("avatar")
            if url:
                found.append((teamIndex, playerIndex, url))
    return found


def _exportedAvatar(autonaApp, teamIndex, playerIndex, suffix="png"):
    return (autonaApp.dir / "out" / "score" / "1" / "team" / str(teamIndex)
            / "player" / str(playerIndex) / f"online_avatar.{suffix}")


def _publishAvatars(autonaApp, entries, committedBoard):
    """Publish the board so the avatars SetData wrote reach ./out.

    They used to have to be written by hand here: SetData's finally block
    cleared them (LIVE-BUG-1) and every assertion about the *export* layer
    would have been blocked behind that one bug, with the two failures
    hiding each other. Now SetData keeps them, and that is asserted rather
    than assumed -- a regression in the widget half must not be able to look
    like an export failure.
    """
    for teamIndex, playerIndex, url in entries:
        assert autonaApp.get(
            f"score.1.team.{teamIndex}.player.{playerIndex}.online_avatar") == url, (
            f"team {teamIndex} player {playerIndex} lost its avatar before"
            " anything could publish it")
    committedBoard(1)


def test_a_loaded_set_keeps_the_startgg_avatar(autonaApp, liveEvent,
                                               realSetOnBoard):
    """LIVE-BUG-1, from the real API.

    SetData wrote the start.gg avatar with ExportPlayerImages(data["avatar"])
    and then, in its own finally block, called the deferred
    ExportPlayerImages() with no argument -- whose onlineAvatar parameter
    defaulted to None. The whole method runs inside one BlockSaving, so
    nothing ever observed the URL: every set loaded from start.gg reached the
    overlays with online_avatar = None. The parameter now defaults to "leave
    it alone" and only Clear() and an explicit None reset it.
    """
    entries = _avatarUrls(liveEvent.match)
    if not entries:
        pytest.skip("no entrant in this set has an avatar")

    for teamIndex, playerIndex, url in entries:
        assert autonaApp.get(
            f"score.1.team.{teamIndex}.player.{playerIndex}.online_avatar") == url


def test_an_entrant_with_no_startgg_avatar_gets_none(autonaApp, liveEvent,
                                                     realSetOnBoard):
    """The other half of the same semantics, on real entrants.

    "Keep the avatar" must not become "never clear it": an entrant with no
    start.gg account has none, and the previous player's face staying on the
    overlay under the new name is its own bug.
    """
    missing = [(teamIndex, playerIndex)
               for teamIndex, team in enumerate(liveEvent.match["entrants"],
                                                start=1)
               for playerIndex, player in enumerate(team, start=1)
               if not player.get("avatar")]
    if not missing:
        pytest.skip("every entrant in this set has an avatar")

    for teamIndex, playerIndex in missing:
        assert autonaApp.get(
            f"score.1.team.{teamIndex}.player.{playerIndex}.online_avatar") is None


def test_the_export_layer_downloads_a_plain_avatar_url(autonaApp, liveEvent,
                                                       realSetOnBoard,
                                                       committedBoard):
    """The OBS path: a state value that is an image URL becomes a real file.

    StateManager.CreateFilesDict spawns a download thread for any leaf whose
    value is an http URL ending in .png/.jpg, converts .jpg to .png with
    Pillow, and writes it under ./out mirroring the state key path. An OBS
    image source points at that file.
    """
    from PIL import Image

    plain = [entry for entry in _avatarUrls(liveEvent.match)
             if "?" not in entry[2]
             and (entry[2].endswith(".png") or entry[2].endswith(".jpg"))]
    if not plain:
        pytest.skip("no entrant in this set has a plain .png/.jpg avatar URL")

    _publishAvatars(autonaApp, plain, committedBoard)

    for teamIndex, playerIndex, url in plain:
        exported = _exportedAvatar(autonaApp, teamIndex, playerIndex)
        assert exported.is_file(), (
            f"avatar for team {teamIndex} player {playerIndex} never landed:"
            f" {url}")
        with Image.open(exported) as image:
            assert image.width > 0 and image.height > 0


def test_the_export_layer_downloads_an_avatar_url_with_a_cache_key(
        autonaApp, liveEvent, realSetOnBoard, committedBoard):
    """LIVE-BUG-2, on the URL shape start.gg actually serves.

    CreateFilesDict recognised an image by str.endswith(".png"/".jpg"), and a
    start.gg avatar carries a cache key (.../image-<hash>.jpg?ehk=...), so it
    fell through to the plain-text branch and was written as
    online_avatar.txt holding the URL. The Web UI reads the URL out of
    program_state.json and was unaffected, which is why this survived: only
    an OBS image source pointed at online_avatar.png could see it. The
    extension now comes from urlparse(...).path.
    """
    from PIL import Image

    withQuery = [entry for entry in _avatarUrls(liveEvent.match)
                 if "?" in entry[2]]
    if not withQuery:
        pytest.skip("no entrant in this set has a query-string avatar URL")

    _publishAvatars(autonaApp, withQuery, committedBoard)

    for teamIndex, playerIndex, url in withQuery:
        exported = _exportedAvatar(autonaApp, teamIndex, playerIndex)
        assert exported.is_file(), (
            f"avatar for team {teamIndex} player {playerIndex} never landed:"
            f" {url}")
        with Image.open(exported) as image:
            assert image.width > 0 and image.height > 0


def test_a_query_string_avatar_is_not_written_as_text(autonaApp, liveEvent,
                                                      realSetOnBoard,
                                                      committedBoard):
    """The observable half of the export bug above, inverted.

    Kept separate from the download assertion because the two fail for
    different reasons: a missing .png can also mean the network was slow,
    while a .txt holding the URL means the branch decision itself is wrong
    again.
    """
    withQuery = [entry for entry in _avatarUrls(liveEvent.match)
                 if "?" in entry[2]]
    if not withQuery:
        pytest.skip("no entrant in this set has a query-string avatar URL")

    _publishAvatars(autonaApp, withQuery, committedBoard)

    for teamIndex, playerIndex, url in withQuery:
        asText = _exportedAvatar(autonaApp, teamIndex, playerIndex,
                                 suffix="txt")
        assert not asText.exists(), (
            f"the avatar URL was exported as text: {url}")


# ── T6: thumbnail ────────────────────────────────────────────────────────

def _prepare(scoreboardNumber=1):
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.SettingsManager import SettingsManager
    from src.thumbnail.main_generate_thumbnail import prepare_data

    return prepare_data(SettingsManager, isPreview=False,
                        gameAssetManager=AutonaGameAssetManager,
                        scoreboardNumber=scoreboardNumber)


def test_prepare_data_builds_a_payload_from_the_live_set(autonaApp, liveEvent,
                                                         realSetOnBoard,
                                                         syntheticGame):
    data, config, template = _prepare()

    assert config["scoreboardNumber"] == 1
    assert template.get("template_id") == "type_a"
    assert template.get("default_background")
    assert config.get("theme_color") and config.get("accent_color")

    board = data["score"]["1"]
    for teamIndex in (1, 2):
        expected = realSetOnBoard.data["entrants"][teamIndex - 1][0]["gamerTag"]
        assert board["team"][str(teamIndex)]["player"]["1"]["name"] == expected


def test_save_result_writes_both_image_formats(autonaApp, liveEvent,
                                               realSetOnBoard, syntheticGame):
    """The disk half of generation, without a renderer in the way."""
    from qtpy.QtGui import QColor, QPixmap

    from src.core.SettingsManager import SettingsManager
    from src.thumbnail.main_generate_thumbnail import save_result

    data, _config, _template = _prepare()

    pixmap = QPixmap(640, 360)
    pixmap.fill(QColor("#123456"))

    saved = Path(save_result(
        pixmap, data, SettingsManager.Get("thumbnail_config") or {},
        scoreboardNumber=1, isPreview=False)).resolve()

    assert saved.is_relative_to(autonaApp.dir.resolve()), (
        f"the thumbnail escaped the runtime directory: {saved}")
    assert saved.is_file()
    assert saved.with_suffix(".jpg").is_file(), (
        "the .jpg twin is missing -- QPixmap.save needs the jpeg imageformat"
        " plugin")

    tags = [realSetOnBoard.data["entrants"][i][0]["gamerTag"] for i in (0, 1)]
    assert f"{tags[0]}-vs-{tags[1]}" in saved.name


def test_generate_renders_a_full_size_thumbnail(autonaApp, liveEvent,
                                                realSetOnBoard, syntheticGame,
                                                thumbnailLayout):
    """The real renderer: QWebEngineView, offscreen, at broadcast size.

    ThumbnailRenderer loads layout/<template>/thumbnail.html from the CWD,
    which the thumbnailLayout fixture links into the runtime directory.
    """
    from PIL import Image

    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.SettingsManager import SettingsManager
    from src.thumbnail.main_generate_thumbnail import generate

    saved = Path(generate(SettingsManager, isPreview=False,
                          gameAssetManager=AutonaGameAssetManager,
                          scoreboardNumber=1)).resolve()

    assert saved.is_relative_to(autonaApp.dir.resolve())
    with Image.open(saved) as image:
        assert (image.width, image.height) == (1920, 1080)
