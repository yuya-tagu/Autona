"""The archive clipper's thumbnails (ADR-006 A6): the data a set is drawn
with (core/ArchiveThumbnails.py: the state's shape, from a set's result and
A5's ``autona`` codenames), the match thumbnail's entry for a set that is not
on a board (ThumbnailService.SetPayload / RenderSet), the job's run of them
(ArchiveJobService.StartThumbs: <job>/thumbs/<no>.png, the copy beside a
clip) and the v1 routes the page calls.

The shell's renderer is a stand-in that keeps what it was asked (its real
half is tests/test_thumbnail_render.py); the worker is
tests/fixtures/fake_vodsplit_worker.py (tests/test_archive_jobs fixtures).
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import AutonaWebOperations as WebOps
from src.core import ArchiveJobService as archive
from src.core import ArchiveResults
from src.core import ArchiveThumbnails as thumbs
from src.core import LayoutCatalog
from src.core import StateSchema as schema
from src.core import ThumbnailService as thumbnail_module
from src.core.GameAssetCatalog import GameAssetCatalog, GameAssetSnapshot
from src.core.SettingsManager import SettingsManager
from src.core.TaskExecutor import TaskExecutor
from src.core.ThumbnailService import ThumbnailService
from tests.fixtures import template_packages
# fake, page, service, work, server, settings, shellSecret and assets: the
# fixtures (pytest finds them by name).
from tests.test_archive_jobs import (  # noqa: F401
    BASE, DONE_ID, KAPPA, MU, REVIEW_SETS, UNUSED, _data, _done, _ended,
    _export_ended, _mode, _refusal, _refused, _stored, _until, fake, page,
    service, work)
from tests.test_archive_results import _scanned, assets  # noqa: F401
from tests.test_web_access import server, settings, shellSecret  # noqa: F401

KAPPA_A = dict(KAPPA, autona={"game": "tgame", "c1": ["mario", "luigi"],
                              "c2": ["peach"], "icons": {}})
MU_A = dict(MU, autona={"game": "tgame", "c1": ["mario"], "c2": ["nobody"],
                        "icons": {}})
THUMB_SETS = dict(REVIEW_SETS, sets=[
    dict(REVIEW_SETS["sets"][0], set=KAPPA_A, thumb="01_fake_bg.jpg"),
    dict(REVIEW_SETS["sets"][1], thumb="02_fake_bg.jpg"),
    dict(REVIEW_SETS["sets"][2], set=MU_A, thumb="03_fake_bg.jpg")])


class _Catalog:
    """What a set's data reads of the game asset catalog."""

    def GamesView(self):
        return {"tgame": {"name": "Test Game"}}

    def GameCharacters(self, game):
        return {"Mario": {"codename": "mario", "display_name": "マリオ"},
                "Luigi": {"codename": "luigi", "display_name": None},
                "Peach": {"codename": "peach", "display_name": "ピーチ"}} \
            if game == "tgame" else {}

    def GameCharacterAssets(self, game, codename, skin=0):
        return {"full": {"type": ["full"], "asset":
                         f"./user_data/games/{game}/full/{codename}_{skin}"
                         ".png"}}


def _assets(codename):
    return _Catalog().GameCharacterAssets("tgame", codename)


# ── the data a set is drawn with ─────────────────────────────────────────

def test_the_namespace_and_the_characters_are_the_results():
    assert thumbs.NAMESPACE == ArchiveResults.NAMESPACE
    assert thumbs.MAX_CHARACTERS == ArchiveResults.MAX_SHOWN


def test_a_sets_data_is_the_states_shape_thumbnail_js_reads():
    data = thumbs.SetData(KAPPA_A, "Fake Cup", _Catalog(), date="2026-11-16")
    assert data == {
        "game": {"codename": "tgame", "name": "Test Game"},
        "tournamentInfo": {"tournamentName": "Fake Cup",
                           "startAt": "2026-11-16"},
        "score": {"1": {
            "phase": "", "pool": "", "match": "Winners Final",
            "team": {
                "1": {"score": 3, "teamName": "", "losers": False,
                      "player": {"1": {
                          "name": "Kappa", "team": "", "mergedName": "Kappa",
                          "character": {
                              "1": {"name": "マリオ", "en_name": "Mario",
                                    "display_name": "マリオ",
                                    "codename": "mario",
                                    "assets": _assets("mario"), "skin": 0,
                                    "variant": {}},
                              # No display name: the en_name.
                              "2": {"name": "Luigi", "en_name": "Luigi",
                                    "display_name": "Luigi",
                                    "codename": "luigi",
                                    "assets": _assets("luigi"), "skin": 0,
                                    "variant": {}}},
                          "country": {}, "state": {}}}},
                "2": {"score": 1, "teamName": "", "losers": False,
                      "player": {"1": {
                          "name": "Lambda", "team": "",
                          "mergedName": "Lambda",
                          "character": {
                              "1": {"name": "ピーチ", "en_name": "Peach",
                                    "display_name": "ピーチ",
                                    "codename": "peach",
                                    "assets": _assets("peach"), "skin": 0,
                                    "variant": {}}},
                          "country": {}, "state": {}}}}}}},
    }


def test_a_character_the_game_does_not_name_is_left_out_and_two_at_most():
    data = thumbs.SetData(dict(KAPPA, autona={
        "game": "tgame", "c1": ["nobody", "peach", 3, "mario", "luigi"],
        "c2": "mario"}), "", _Catalog())
    team = data["score"]["1"]["team"]
    assert [c["codename"] for c in team["1"]["player"]["1"][
        "character"].values()] == ["peach", "mario"]
    assert list(team["1"]["player"]["1"]["character"]) == ["1", "2"]
    assert team["2"]["player"]["1"]["character"] == {}


@pytest.mark.parametrize("result", [
    KAPPA, dict(KAPPA, autona=None), dict(KAPPA, autona={"c1": ["mario"]}),
    dict(KAPPA, autona={"game": "nogame", "c1": ["mario"]})])
def test_a_set_without_characters_draws_the_names(result):
    data = thumbs.SetData(result, "Fake Cup", _Catalog())
    for side in ("1", "2"):
        assert data["score"]["1"]["team"][side]["player"]["1"][
            "character"] == {}
    assert data["score"]["1"]["team"]["1"]["player"]["1"]["name"] == "Kappa"
    assert data["tournamentInfo"]["startAt"] == ""
    # And no catalog at all: no game name, no characters.
    none = thumbs.SetData(KAPPA_A, "Fake Cup", None)
    assert none["game"] == {"codename": "tgame", "name": ""}
    assert none["score"]["1"]["team"]["1"]["player"]["1"][
        "character"] == {}


def test_what_is_not_a_result_reads_as_nothing():
    data = thumbs.SetData({"round": 3, "p1": None, "s1": True, "p2": "B",
                           "s2": 2.5}, None, _Catalog())
    team = data["score"]["1"]["team"]
    assert data["score"]["1"]["match"] == ""
    assert (team["1"]["score"], team["1"]["player"]["1"]["name"]) == (0, "")
    assert (team["2"]["score"], team["2"]["player"]["1"]["name"]) == (0, "B")
    assert data["tournamentInfo"]["tournamentName"] == ""


def test_each_games_characters_are_listed_once_a_run():
    asked = []

    class Counting(_Catalog):
        def GameCharacters(self, game):
            asked.append(game)
            return super().GameCharacters(game)

    cache = {}
    for result in (KAPPA_A, MU_A, KAPPA_A):
        thumbs.SetData(result, "", Counting(), cache=cache)
    assert asked == ["tgame"]


def test_the_characters_are_a_scanned_games_whether_loaded_or_not(assets):
    catalog = _scanned()
    data = thumbs.SetData(KAPPA_A, "Fake Cup", catalog)
    # The scan's name, in the program locale (ja-JP), as a load's.
    assert data["game"]["name"] == catalog.GamesView()["tgame"]["name"]         == "テストゲーム"
    mario = data["score"]["1"]["team"]["1"]["player"]["1"]["character"]["1"]
    assert (mario["name"], mario["en_name"], mario["codename"]) == (
        "マリオ", "Mario", "mario")
    assert mario["assets"]["full"]["asset"].endswith("/full/mario_0.png")
    assert catalog.GameCharacterAssets("tgame", "mario") == mario["assets"]
    assert catalog.GameCharacterAssets("nogame", "mario") == {}
    assert catalog.GameCharacterAssets("tgame", "") == {}
    # The loaded game: from its load's index, as CharacterAssets reads it.
    config = catalog.Games()["tgame"]
    catalog._current = GameAssetSnapshot(
        codename="tgame", game=config,
        assetIndex={"mario": {"full": ["mario_0.png"]}})
    assert catalog.GameCharacterAssets("tgame", "mario") == \
        catalog.CharacterAssets("mario", 0)


# ── the match thumbnail's entry for a set ────────────────────────────────

@pytest.fixture
def drawing(fake, work, tmp_path, monkeypatch):
    """The thumbnail service (its template in cwd tmp, a stand-in renderer,
    `later` queued for the test to run), the catalog and an executor."""
    # The default template's package (the catalog's, ADR-007 T1), with a
    # template file of the test's own.
    template_packages.Install(tmp_path / "layout", ("thumbnail",))
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(tmp_path / "layout"))
    types = tmp_path / "layout" / "thumbnail"
    (types / "type_a.json").write_text(json.dumps({"name": "A"}),
                                       encoding="utf-8")
    renderer = SimpleNamespace(asked=[], refuse=None)

    def render(kind, payload, done):
        if renderer.refuse is not None:
            raise renderer.refuse
        renderer.asked.append(SimpleNamespace(kind=kind, payload=payload,
                                              done=done))

    queued = []
    made = ThumbnailService(later=queued.append, render=render)
    monkeypatch.setattr(ThumbnailService, "instance", made)
    monkeypatch.setattr(GameAssetCatalog, "instance", _Catalog())
    executor = TaskExecutor()
    monkeypatch.setattr(TaskExecutor, "instance", executor)
    monkeypatch.setattr(WebOps, "startImageRender", render)
    yield SimpleNamespace(service=made, renderer=renderer, queued=queued,
                          types=types)
    executor.shutdown(30)


def test_a_sets_payload_is_drawn_with_the_settings_and_its_background(
        drawing):
    SettingsManager.settings["thumbnail_config"] = {
        "background_source": "custom",
        "background_path": "./user_data/thumbnail_background_x.png",
        "game": {"tgame": {"zoom": 120}}}
    saved = json.dumps(SettingsManager.settings, sort_keys=True)
    data = thumbs.SetData(KAPPA_A, "Fake Cup", _Catalog())

    same, config, template = drawing.service.SetPayload(
        data, "file:///C:/work/01_fake_bg.jpg")
    assert same is data
    assert template == {"name": "A", "template_id": "type_a"}
    assert config["background_path"] == "file:///C:/work/01_fake_bg.jpg"
    assert config["scoreboardNumber"] == 1
    # The set's game's settings, as a render of the board reads them.
    assert config["game"]["tgame"]["zoom"] == 120
    # None: the settings' own background.
    _, config, _ = drawing.service.SetPayload(data)
    assert config["background_path"] == \
        "./user_data/thumbnail_background_x.png"
    # Nothing written, the state not read for a match.
    assert json.dumps(SettingsManager.settings, sort_keys=True) == saved


def test_a_set_is_drawn_without_the_tournament_loaded_now(drawing,
                                                          tmp_path):
    # The tournament's colors and banner are the one loaded now, not the
    # archive's: the defaults, unless the thumbnail has its own colors.
    SettingsManager.settings["general"] = {"theme_color": "#111111",
                                           "accent_color": "#222222"}
    SettingsManager.settings["thumbnail_config"] = {
        "background_source": "banner"}
    banner = tmp_path / thumbnail_module.BANNER_PATH
    banner.parent.mkdir(parents=True, exist_ok=True)
    banner.write_bytes(b"png")
    data = thumbs.SetData(KAPPA_A, "Fake Cup", _Catalog())

    _, config, _ = drawing.service.SetPayload(data)
    assert (config["theme_color"], config["accent_color"]) == (
        schema.DEFAULT_THEME_COLOR, schema.DEFAULT_ACCENT_COLOR)
    assert config["background_path"] == thumbnail_module.DEFAULT_BANNER_PATH
    # A frame is still the background it is given.
    _, config, _ = drawing.service.SetPayload(data, "file:///C:/w/f.jpg")
    assert config["background_path"] == "file:///C:/w/f.jpg"
    # The thumbnail's own colors are the operator's settings for it.
    SettingsManager.settings["colors"] = {"thumbnail": {
        "theme_color": "#333333", "accent_color": "#444444"}}
    _, config, _ = drawing.service.SetPayload(data)
    assert (config["theme_color"], config["accent_color"]) == (
        "#333333", "#444444")
    # The board's thumbnail still takes the tournament's banner.
    assert thumbnail_module.ResolveBackgroundPath(
        {"background_source": "banner"}) == thumbnail_module.BANNER_PATH


def test_a_set_is_drawn_by_the_renderer_outside_the_lane(drawing):
    payload = drawing.service.SetPayload(
        thumbs.SetData(KAPPA_A, "", _Catalog()))
    answers = []
    drawing.service.RenderSet(payload, lambda *answer: answers.append(answer))
    asked = drawing.renderer.asked.pop()
    assert (asked.kind, asked.payload) == ("thumbnail", payload)
    asked.done("encode", "")
    assert answers == [("encode", "")]
    drawing.service.Later("fn")
    assert drawing.queued == ["fn"]
    with pytest.raises(RuntimeError):
        ThumbnailService(later=list.append).RenderSet(payload, print)


# ── a job's thumbnails ───────────────────────────────────────────────────

def _thumbs_ended(service, drawing, image=b"PNG"):
    """Answer every render the run asks for and run what it queues, until
    its thumbnails end -> the job's thumbs."""
    def step():
        if drawing.renderer.asked:
            asked = drawing.renderer.asked.pop(0)
            no = asked.payload[0]["score"]["1"]["match"]
            asked.done(lambda fmt, fast=False: image + b"-" + no.encode(),
                       "")
            return None
        if drawing.queued:
            drawing.queued.pop(0)()
            return None
        view = service.Job(DONE_ID)[0]["thumbs"]
        return view if view["state"] != archive.THUMBS_RUNNING else None

    return _until(step, "the thumbnails to end")


def _ready(work, tmp_path, service, frames=True):
    video = _done(work, tmp_path, THUMB_SETS)
    out = work / DONE_ID / "out"
    if frames:
        for no in (1, 2, 3):
            (out / f"0{no}_fake_bg.jpg").write_bytes(b"jpeg")
    (work / DONE_ID / "results.json").write_text(json.dumps(
        {"sets": [KAPPA], "date": "2026-11-16"}), encoding="utf-8")
    # Set 2 is confirmed but has no result: no thumbnail.
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True},
                                          "2": {"confirmed": True},
                                          "3": {"confirmed": True}}})
    return video


def test_the_confirmed_sets_thumbnails_are_drawn_on_their_frames(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    jobDir = work / DONE_ID

    job = service.StartThumbs(DONE_ID)
    assert job["thumbs"] == {"state": "running", "done": 0, "total": 2,
                             "failed": [], "files": [], "revision": 0}
    first = drawing.renderer.asked[0].payload
    assert first[0] == thumbs.SetData(KAPPA_A, "Fake Cup", _Catalog(),
                                      date="2026-11-16")
    assert first[1]["background_path"] == Path(os.path.realpath(
        jobDir / "out" / "01_fake_bg.jpg")).as_uri()
    assert first[2]["template_id"] == "type_a"
    # One at a time: the next once this one is saved.
    assert len(drawing.renderer.asked) == 1
    message = _refused(archive.BUSY, service.StartThumbs, DONE_ID)
    assert "サムネイルを作っている" in message

    ended = _thumbs_ended(service, drawing)
    assert ended == {"state": "done", "done": 2, "total": 2, "failed": [],
                     "files": [1, 3], "revision": 2}
    assert (jobDir / "thumbs" / "1.png").read_bytes() == b"PNG-Winners Final"
    assert (jobDir / "thumbs" / "3.png").read_bytes() == b"PNG-Grand Final"
    assert service.ThumbFile(DONE_ID, "3") == (
        str(jobDir / "thumbs" / "3.png"), "image/png")
    for number in ("2", "x", "1000000"):
        _refused(archive.NOT_FOUND, service.ThumbFile, DONE_ID, number)
    # No export yet: nothing beside a clip.
    assert not (jobDir / "clips").exists()
    # The job's other files are as they were.
    assert sorted(p.name for p in jobDir.iterdir()) == [
        "job.json", "out", "results.json", "review.json", "thumbs"]


def test_the_settings_background_leaves_the_frames_out(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    SettingsManager.settings["archive"]["thumbnail_background"] = \
        archive.SETTINGS_BACKGROUND
    service.StartThumbs(DONE_ID)
    # The default background source: the template's own.
    assert drawing.renderer.asked[0].payload[1]["background_path"] is None
    _thumbs_ended(service, drawing)


def test_a_set_with_no_frame_is_drawn_on_the_settings_background(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service, frames=False)
    service.StartThumbs(DONE_ID)
    assert drawing.renderer.asked[0].payload[1]["background_path"] is None
    assert _thumbs_ended(service, drawing)["done"] == 2


def test_a_render_that_fails_is_counted_and_the_next_one_drawn(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    service.StartThumbs(DONE_ID)
    drawing.renderer.asked.pop(0).done(None, "it broke")
    ended = _thumbs_ended(service, drawing)
    assert ended["state"] == "done"
    assert (ended["done"], ended["failed"], ended["files"]) == (1, [1], [3])


def test_a_first_render_that_cannot_start_raises_and_ends_the_run(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    drawing.renderer.refuse = WebOps.NotAvailable("closing")
    with pytest.raises(WebOps.NotAvailable):
        service.StartThumbs(DONE_ID)
    assert service.Job(DONE_ID)[0]["thumbs"] == {
        "state": "failed", "done": 0, "total": 2, "failed": [1],
        "files": [], "revision": 0}
    # Another run may start.
    drawing.renderer.refuse = None
    service.StartThumbs(DONE_ID)
    assert _thumbs_ended(service, drawing)["state"] == "done"


def test_the_shutdown_ends_the_run_at_its_next_set(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    service.StartThumbs(DONE_ID)
    service.Shutdown(5.0)
    ended = _thumbs_ended(service, drawing)
    # The set being drawn is saved; the next one is not started.
    assert (ended["state"], ended["done"], ended["total"]) == (
        archive.THUMBS_CANCELLED, 1, 2)
    assert drawing.renderer.asked == []
    assert ended["files"] == [1]
    _refused(archive.STOPPING, service.StartThumbs, DONE_ID)


def test_a_next_render_that_cannot_be_queued_ends_the_run_failed(
        service, work, fake, tmp_path, drawing, monkeypatch):
    _ready(work, tmp_path, service)

    def closed(fn):
        raise RuntimeError("the event loop is gone")

    monkeypatch.setattr(drawing.service, "_later", closed)
    service.StartThumbs(DONE_ID)
    ended = _thumbs_ended(service, drawing)
    assert (ended["state"], ended["done"], ended["total"]) == (
        archive.THUMBS_FAILED, 1, 2)
    assert drawing.renderer.asked == []
    # Another run may start.
    monkeypatch.setattr(drawing.service, "_later", drawing.queued.append)
    service.StartThumbs(DONE_ID)
    assert _thumbs_ended(service, drawing)["state"] == archive.THUMBS_DONE


def test_a_thumbnail_is_shown_while_the_review_is_what_it_was_drawn_for(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    service.StartThumbs(DONE_ID)
    assert _thumbs_ended(service, drawing)["files"] == [1, 3]

    def shown():
        return service.Job(DONE_ID)[0]["thumbs"]["files"]

    # Set 1's range and set 3's result changed: neither thumbnail is the
    # set as reviewed now (the files stay until the next run).
    service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True, "end": 700.0},
        "3": {"confirmed": True, "set": UNUSED}}})
    assert shown() == []
    assert (work / DONE_ID / "thumbs" / "1.png").exists()
    # Back as drawn: shown again.
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True},
                                          "3": {"confirmed": True}}})
    assert shown() == [1, 3]
    # A thumbnail thumbs.json does not name is not shown.
    (work / DONE_ID / "thumbs" / "thumbs.json").unlink()
    assert shown() == []


def test_a_new_run_removes_the_thumbnails_of_the_sets_it_does_not_draw(
        service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    folder = work / DONE_ID / "thumbs"
    service.StartThumbs(DONE_ID)
    _thumbs_ended(service, drawing)
    assert sorted(os.listdir(folder)) == ["1.png", "3.png", "thumbs.json"]
    # Set 3 confirmed no more: not drawn again, and its thumbnail goes
    # before the first render.
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    service.StartThumbs(DONE_ID)
    assert sorted(os.listdir(folder)) == ["1.png", "thumbs.json"]
    ended = _thumbs_ended(service, drawing)
    assert (ended["files"], ended["total"]) == ([1], 1)
    drawn = json.loads((folder / "thumbs.json").read_text(encoding="utf-8"))
    assert drawn["format"] == 1 and list(drawn["sets"]) == ["1"]
    assert drawn["sets"]["1"] == {"start": 120.0, "end": 720.0,
                                  "set": KAPPA_A, "beside": None}


def test_a_new_run_removes_the_copies_an_earlier_run_put_beside_the_clips(
        service, work, fake, tmp_path, drawing):
    clips = work / DONE_ID / "clips"
    files = _exported(service, work, tmp_path)
    beside = {no: clips / (os.path.splitext(files[no])[0] + ".png")
              for no in (1, 3)}
    (clips / "cover.png").write_bytes(b"the operator's")
    service.StartThumbs(DONE_ID)
    _thumbs_ended(service, drawing)
    assert beside[1].exists() and beside[3].exists()
    drawn = json.loads((work / DONE_ID / "thumbs" / "thumbs.json")
                       .read_text(encoding="utf-8"))["sets"]
    assert (drawn["1"]["beside"], drawn["3"]["beside"]) == (
        beside[1].name, beside[3].name)

    # Set 1's range changed since the export: its clip is not the set any
    # more. Every copy goes before the first render; set 3's comes back.
    service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True, "end": 700.0}, "3": {"confirmed": True}}})
    service.StartThumbs(DONE_ID)
    assert not beside[1].exists() and not beside[3].exists()
    _thumbs_ended(service, drawing)
    assert not beside[1].exists() and beside[3].exists()
    # What Autona did not put there stays, the clips too.
    assert (clips / "cover.png").read_bytes() == b"the operator's"
    assert all((clips / name).exists() for name in files.values())


def test_the_thumbnails_are_refused_before_anything_is_drawn(
        service, work, fake, tmp_path, drawing, monkeypatch):
    video = _done(work, tmp_path, THUMB_SETS)
    message = _refused(archive.INVALID, service.StartThumbs, DONE_ID)
    assert "確定" in message
    service.SaveReview(DONE_ID, {"sets": {"2": {"confirmed": True}}})
    _refused(archive.INVALID, service.StartThumbs, DONE_ID)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    (drawing.types / "type_a.json").unlink()
    message = _refused(archive.UNAVAILABLE, service.StartThumbs, DONE_ID)
    assert "テンプレート" in message
    monkeypatch.setattr(ThumbnailService, "instance", None)
    _refused(archive.UNAVAILABLE, service.StartThumbs, DONE_ID)
    _refused(archive.NOT_FOUND, service.StartThumbs, "20261116-000000-zzzz")
    _stored(work, DONE_ID, state="cancelled", source=str(video))
    _refused(archive.NOT_DONE, service.StartThumbs, DONE_ID)
    assert drawing.renderer.asked == []
    assert not (work / DONE_ID / "thumbs").exists()


def test_no_thumbnails_while_the_jobs_analysis_runs(service, work, fake,
                                                   drawing):
    from tests.test_archive_jobs import SOURCE
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    _refused(archive.BUSY, service.StartThumbs, job["id"])
    _refused(archive.NOT_FOUND, service.ThumbFile, job["id"], "1")
    assert service.Job(job["id"])[0]["thumbs"]["state"] is None
    service.Cancel(job["id"])
    _ended(service, job["id"])


# ── the copy beside a clip ───────────────────────────────────────────────

def _exported(service, work, tmp_path):
    _ready(work, tmp_path, service)
    service.StartExport(DONE_ID)
    export = _export_ended(service, DONE_ID)
    assert export["state"] == "done"
    return {clip["no"]: clip["file"] for clip in export["files"]}


def test_a_thumbnail_goes_beside_the_clip_exported_from_the_same_set(
        service, work, fake, tmp_path, drawing):
    clips = work / DONE_ID / "clips"
    files = _exported(service, work, tmp_path)
    assert sorted(files) == [1, 2, 3]

    service.StartThumbs(DONE_ID)
    _thumbs_ended(service, drawing)
    for no in (1, 3):
        beside = clips / (os.path.splitext(files[no])[0] + ".png")
        assert beside.read_bytes() == (
            work / DONE_ID / "thumbs" / f"{no}.png").read_bytes()
    # Set 2 has no result: no thumbnail, nothing beside its clip.
    assert not (clips / (os.path.splitext(files[2])[0] + ".png")).exists()


def test_no_copy_once_the_review_differs_from_the_export(
        service, work, fake, tmp_path, drawing):
    clips = work / DONE_ID / "clips"
    files = _exported(service, work, tmp_path)
    # Set 1's range changed after the export: its clip is not the set as it
    # is reviewed now.
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True,
                                                "end": 700.0},
                                          "3": {"confirmed": True}}})
    service.StartThumbs(DONE_ID)
    _thumbs_ended(service, drawing)
    assert not (clips / (os.path.splitext(files[1])[0] + ".png")).exists()
    assert (clips / (os.path.splitext(files[3])[0] + ".png")).exists()


def test_no_copy_when_the_clips_are_not_this_videos(
        service, work, fake, tmp_path, drawing):
    clips = work / DONE_ID / "clips"
    files = _exported(service, work, tmp_path)
    listed = json.loads((clips / "export.json").read_text(encoding="utf-8"))
    listed["source"] = str(tmp_path / "other.mp4")
    (clips / "export.json").write_text(json.dumps(listed), encoding="utf-8")
    service.StartThumbs(DONE_ID)
    _thumbs_ended(service, drawing)
    assert [name for name in os.listdir(clips) if name.endswith(".png")] \
        == []
    assert sorted(files) == [1, 2, 3]


def test_no_copy_while_the_jobs_export_runs(service, work, fake, tmp_path,
                                            drawing):
    clips = work / DONE_ID / "clips"
    _exported(service, work, tmp_path)
    _mode(fake, "slow")
    service.StartExport(DONE_ID)
    service.StartThumbs(DONE_ID)
    assert _thumbs_ended(service, drawing)["done"] == 2
    assert [name for name in os.listdir(clips) if name.endswith(".png")] \
        == []
    service.Cancel(DONE_ID)
    _export_ended(service, DONE_ID)


# ── the settings and the v1 routes ───────────────────────────────────────

def test_the_thumbnails_background_is_a_setting(service, work, fake):
    assert service.ThumbnailBackground() == archive.FRAME_BACKGROUND
    service.UpdateSettings({"thumbnail_background": "settings"})
    assert SettingsManager.Get(archive.THUMBNAIL_BACKGROUND_KEY) == \
        "settings"
    assert service.Status()["thumbnail_background"] == "settings"
    for body in ({}, {"thumbnail_background": "banner"}, {"other": 1}, [],
                 {"work_dir": str(work), "thumbnail_background": None}):
        _refused(archive.INVALID, service.UpdateSettings, body)
    # A refused body saves neither.
    assert service.ThumbnailBackground() == "settings"
    assert service.WorkDir() == os.path.normpath(str(work))
    # A value saved by hand that is not one reads as the default.
    SettingsManager.settings["archive"]["thumbnail_background"] = "x"
    assert service.ThumbnailBackground() == archive.FRAME_BACKGROUND


def test_the_settings_are_checked_together_and_saved_in_one_write(
        service, work, fake, tmp_path, monkeypatch):
    writes = []
    write = SettingsManager.SetMany

    def recorded(pairs):
        writes.append(list(pairs))
        return write(pairs)

    monkeypatch.setattr(SettingsManager, "SetMany", recorded)
    target = tmp_path / "videos"
    service.UpdateSettings({"work_dir": str(target),
                            "thumbnail_background": "settings"})
    assert writes == [[(archive.WORK_DIR_KEY, str(target)),
                       (archive.THUMBNAIL_BACKGROUND_KEY, "settings")]]
    # A work folder that cannot be used saves the background neither.
    file = tmp_path / "a-file"
    file.write_text("x", encoding="utf-8")
    _refused(archive.INVALID, service.UpdateSettings,
             {"work_dir": str(file), "thumbnail_background": "frame"})
    assert len(writes) == 1
    assert service.ThumbnailBackground() == "settings"

    # A write that fails saves neither (SetMany puts both back).
    def unreadable(pairs):
        raise RuntimeError("settings.json cannot be written")

    monkeypatch.setattr(SettingsManager, "SetMany", unreadable)
    with pytest.raises(RuntimeError):
        service.UpdateSettings({"work_dir": str(tmp_path / "other"),
                                "thumbnail_background": "frame"})
    assert service.WorkDir() == str(target)
    assert service.ThumbnailBackground() == "settings"


def test_the_page_draws_and_shows_the_thumbnails_through_the_routes(
        page, service, work, fake, tmp_path, drawing):
    _ready(work, tmp_path, service)
    status = _data(page.patch(f"{BASE}/settings",
                              json={"thumbnail_background": "frame"}))
    assert status["thumbnail_background"] == "frame"
    url = f"{BASE}/jobs/{DONE_ID}"

    job = _data(page.post(f"{url}/thumbs:generate"))["job"]
    assert job["thumbs"]["state"] == "running"
    _refusal(page.post(f"{url}/thumbs:generate"), 409, "CONFLICT",
             archive.BUSY)
    _thumbs_ended(service, drawing)
    detail = _data(page.get(url))
    assert detail["job"]["thumbs"]["files"] == [1, 3]

    response = page.get(f"{url}/thumbs/1")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_data() == b"PNG-Winners Final"
    response.close()
    message = _refusal(page.get(f"{url}/thumbs/2"), 404, "NOT_FOUND",
                       archive.NOT_FOUND)
    assert message == archive.THUMB_MISSING
    _refusal(page.post(f"{BASE}/jobs/20261116-000000-zzzz/thumbs:generate"),
             404, "NOT_FOUND", archive.NOT_FOUND)
    # What is not a set's number names no file (thumbs/ alone, digits).
    for no in ("abc", "-1", "1.5", "1e3", "0001234567", "1234567", "%2E%2E",
               "..%5C1", "1.png", "%201"):
        message = _refusal(page.get(f"{url}/thumbs/{no}"), 404, "NOT_FOUND",
                           archive.NOT_FOUND)
        assert message == archive.THUMB_MISSING
    for no in ("abc", "-1", "..", "../1", "1/../1", "", None, 1):
        _refused(archive.NOT_FOUND, service.ThumbFile, DONE_ID, no)
    # Leading zeros are the set's number.
    assert service.ThumbFile(DONE_ID, "0001")[0].endswith("1.png")


def test_the_headless_shell_cannot_draw_a_jobs_thumbnails(
        page, service, work, fake, tmp_path, drawing, monkeypatch):
    _ready(work, tmp_path, service)
    monkeypatch.setattr(WebOps, "startImageRender", None)
    response = page.post(f"{BASE}/jobs/{DONE_ID}/thumbs:generate")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "UNAVAILABLE"
    assert drawing.renderer.asked == []
    assert service.Job(DONE_ID)[0]["thumbs"]["state"] is None
