"""The archive clipper's title templates and the titles' preview (ADR-006
A6): core/ArchiveTemplates.py (user_data/archive_templates/<id>.json, the
checks, the worker's order), the templates handed to the worker's ``run`` and
``export`` (``--template``), ArchiveJobService.PreviewTitles (the worker's
``titles``, vodsplit V10) and the v1 routes the page calls.

The worker is tests/fixtures/fake_vodsplit_worker.py (tests/test_archive_jobs
fixtures); who may call the routes is tests/test_archive_jobs.py's table and
tests/test_web_access.py's walk.
"""
import json
import os
import threading
import time

import pytest

from src.core import ArchiveJobService as archive
from src.core import ArchiveTemplates as templates
from src.core import ArchiveWorker as worker
from src.core.ArchiveJobService import ArchiveJobService
# fake, page, service, work, server, settings and shellSecret: the routes'
# fixtures (pytest finds them by name).
from tests.test_archive_jobs import (  # noqa: F401
    BASE, DONE_ID, KAPPA, MU, REVIEW_SETS, SOURCE, UNUSED, WAIT, _data,
    _done, _ended, _export_ended, _mode, _refusal, _refused,
    _stored as _stored_job, _until, _vodsplit_export, fake, page, service,
    work)
from tests.test_web_access import server, settings, shellSecret  # noqa: F401

TEMPLATE = {
    "name": "雷影杯", "match": "雷影杯", "title": "{p1} vs {p2} {score}",
    "title_team": "", "description": "{event} {round}",
    "chapter_format": "{time} 第{n}試合", "overflow": "short_round",
    "round_map": {"Grand Final": {"ja": "決勝", "short": "GF"}},
    "aliases": {"Kappa": "かっぱ"}, "order": "winner_first"}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """cwd tmp: the templates' folder is its user_data/archive_templates."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / "user_data" / "archive_templates"


def _invalid(body, says, templateId=None):
    with pytest.raises(templates.TemplateError) as refused:
        templates.Check(body, templateId)
    assert refused.value.code == templates.INVALID
    assert says in refused.value.message


def _stored(folder, templateId, body):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{templateId}.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8")


# ── the checks ───────────────────────────────────────────────────────────

def test_a_template_is_every_key_in_vodsplits_order_and_nothing_else():
    checked = templates.Check(dict(TEMPLATE, extra="dropped", name=" 雷影杯 "))
    assert list(checked) == list(templates.KEYS)
    assert checked == dict(TEMPLATE)
    # What vodsplit would fill in from its DEFAULT is read as the standard's.
    assert templates.Check({"name": "x", "title": "t",
                            "chapter_format": "{time}"}) == dict(
        templates.Standard(), name="x", title="t", description="",
        chapter_format="{time}")


def test_the_standard_template_is_vodsplits_default():
    # vodsplit/templates.py DEFAULT (title_team is the format's, unused).
    assert templates.Standard() == {
        "name": "標準", "match": None,
        "title": "{event} {round} {p1}({c1}) vs {p2}({c2})",
        "title_team": "",
        "description": ("{event} {round}\n{p1}（{c1}） vs {p2}（{c2}）　{score}"
                        "\n\n{chapters}\n\n▼元配信\n{source_url_t}"),
        "chapter_format": "{time} Game {n}", "overflow": "drop_characters",
        "round_map": {}, "aliases": {}, "order": "result"}
    assert templates.Standard() is not templates.Standard()


def test_the_error_codes_are_the_job_services():
    assert (templates.INVALID, templates.NOT_FOUND, templates.SAVE_FAILED) \
        == (archive.INVALID, archive.NOT_FOUND, archive.SAVE_FAILED)


@pytest.mark.parametrize("body, says", [
    ([], "形式が違います"),
    (dict(TEMPLATE, name=" "), "名前を入力"),
    (dict(TEMPLATE, name="x" * 61), "60 文字以内"),
    (dict(TEMPLATE, title=""), "タイトルを入力"),
    (dict(TEMPLATE, title="a\nb"), "改行や制御文字"),
    (dict(TEMPLATE, title=3), "文字列"),
    (dict(TEMPLATE, match="x" * 101), "100 文字以内"),
    (dict(TEMPLATE, description="a\x07b"), "制御文字"),
    (dict(TEMPLATE, description="x" * 10001), "10000 文字以内"),
    (dict(TEMPLATE, chapter_format="{time} {round}"), "{time} と {n}"),
    (dict(TEMPLATE, chapter_format="{time"), "{time} と {n}"),
    (dict(TEMPLATE, chapter_format="{0}"), "{time} と {n}"),
    # What str.format raises past KeyError / ValueError: the worker would
    # raise it at the end of its run.
    (dict(TEMPLATE, chapter_format="{time} {n[0]}"), "{time} と {n}"),
    (dict(TEMPLATE, chapter_format="{time.x} {n}"), "{time} と {n}"),
    (dict(TEMPLATE, chapter_format="{time:%} {n}"), "{time} と {n}"),
    (dict(TEMPLATE, overflow="cut"), "縮め方"),
    (dict(TEMPLATE, order="loser_first"), "並び順"),
    (dict(TEMPLATE, round_map=[]), "形式が違います"),
    (dict(TEMPLATE, round_map={"GF": "決勝"}), "表記の形式"),
    (dict(TEMPLATE, round_map={"GF": {"ja": 1}}), "100 文字以内"),
    (dict(TEMPLATE, round_map={" ": {"ja": "x"}}), "空でない"),
    (dict(TEMPLATE, aliases={"Kappa": ""}), "置き換え後の名前"),
    (dict(TEMPLATE, aliases={"Kappa": "x" * 101}), "置き換え後の名前"),
    (dict(TEMPLATE, aliases={str(n): "x" for n in range(501)}), "500 件"),
])
def test_what_vodsplit_would_not_read_is_refused(body, says):
    _invalid(body, says)


def test_what_needs_no_refusal_is_read_as_vodsplit_reads_it():
    checked = templates.Check(dict(
        TEMPLATE, match="  ", description="a\r\nb\tc",
        chapter_format="{{n}} {time}",
        round_map={"Winners Final": {"ja": "", "short": "WF"},
                   "Losers Final": {"ja": None}}))
    # An empty match is none; a description's line ends are "\n"; braces
    # written twice are the braces; an empty name is left out, an entry
    # with none at all too.
    assert checked["match"] is None
    assert checked["description"] == "a\nb\tc"
    assert checked["chapter_format"] == "{{n}} {time}"
    assert checked["round_map"] == {"Winners Final": {"short": "WF"}}


def test_the_standard_template_takes_no_match():
    _invalid(dict(TEMPLATE), "標準のテンプレートには使う大会名",
             templates.DEFAULT_ID)
    assert templates.Check(dict(TEMPLATE, match=""), templates.DEFAULT_ID)[
        "match"] is None


# ── the files and the order ──────────────────────────────────────────────

def test_with_no_file_the_standard_one_is_listed(folder):
    assert templates.List() == [{"id": "default", "stored": False,
                                 "role": "default",
                                 "template": templates.Standard()}]
    # The standard one without a file is the worker's own default.
    assert templates.WorkerTemplates() == []
    assert templates.Get("default")["stored"] is False
    assert not folder.exists()


def test_a_save_writes_the_template_whole_and_reads_it_back(folder):
    edited = dict(templates.Standard(), title="{p1} vs {p2}", extra=1)
    entry = templates.Save("default", edited)
    assert entry == {"id": "default", "stored": True, "role": "default",
                     "template": dict(templates.Standard(),
                                      title="{p1} vs {p2}")}
    written = json.loads((folder / "default.json").read_text(
        encoding="utf-8"))
    assert list(written) == list(templates.KEYS)
    assert written == entry["template"]
    assert templates.List() == [entry]
    assert templates.WorkerTemplates() == [entry["template"]]
    # Nothing but the file (the temporary one was replaced into it).
    assert sorted(os.listdir(folder)) == ["default.json"]


def test_the_worker_order_is_the_longest_match_first_then_the_default(
        folder):
    copy = templates.Duplicate("default")
    assert copy["template"]["name"] == "標準 のコピー"
    assert copy["role"] == "default" and copy["stored"] is True
    assert templates.ID_PATTERN.fullmatch(copy["id"])
    # The standard one (no file yet) comes after the copy: not used.
    assert [(e["id"], e["role"]) for e in templates.List()] == [
        (copy["id"], "default"), ("default", "unused")]

    templates.Save("default", templates.Standard())
    short = templates.Save(copy["id"], dict(TEMPLATE, match="雷影杯"))
    longer = templates.Duplicate(copy["id"])
    longer = templates.Save(longer["id"], dict(TEMPLATE, name="2on2",
                                               match="雷影杯 2on2"))
    listed = templates.List()
    assert [(e["id"], e["role"]) for e in listed] == [
        (longer["id"], "match"), (short["id"], "match"),
        ("default", "default")]
    assert templates.WorkerTemplates() == [e["template"] for e in listed]
    assert short["template"]["round_map"] == TEMPLATE["round_map"]


def test_a_second_template_without_a_match_is_not_used(folder):
    templates.Save("default", templates.Standard())
    other = templates.Duplicate("default")
    assert [(e["id"], e["role"]) for e in templates.List()] == [
        ("default", "default"), (other["id"], "unused")]
    assert templates.WorkerTemplates() == [templates.Standard()]


def test_a_second_template_with_the_same_match_is_shadowed(folder):
    made = [templates.Save(templates.Duplicate("default")["id"],
                           dict(TEMPLATE, name=name)) for name in "AB"]
    # One match: the first by id is taken, the other never is, and says so
    # (it is not given to the worker).
    first, second = sorted(made, key=lambda entry: entry["id"])
    assert [(e["id"], e["role"]) for e in templates.List()] == [
        (first["id"], "match"), (second["id"], "shadowed"),
        ("default", "default")]
    assert templates.Get(second["id"])["role"] == "shadowed"
    assert templates.WorkerTemplates() == [first["template"]]
    # A longer match is not the same match: both are taken.
    templates.Save(second["id"], dict(TEMPLATE, match="雷影杯 2on2"))
    assert [e["role"] for e in templates.List()] == [
        "match", "match", "default"]


def test_a_delete_removes_the_file_and_the_default_goes_back_to_standard(
        folder):
    templates.Save("default", dict(templates.Standard(), title="{p1}"))
    copy = templates.Duplicate("default")
    assert templates.Delete(copy["id"]) is None
    assert not (folder / f"{copy['id']}.json").exists()
    assert templates.Delete("default") == {
        "id": "default", "stored": False, "role": "default",
        "template": templates.Standard()}
    assert templates.List() == [templates.Get("default")]
    # The standard one with no file: nothing to remove, the same answer.
    assert templates.Delete("default")["stored"] is False


@pytest.mark.parametrize("templateId", [
    "20261116-120000-zzzz", "nope", "..\\default", "../default",
    "default.json", "", None, 3])
def test_an_id_that_is_not_one_or_not_stored_is_not_found(folder,
                                                          templateId):
    for call, args in ((templates.Get, ()),
                       (templates.Save, (templates.Standard(),)),
                       (templates.Duplicate, ()), (templates.Delete, ())):
        with pytest.raises(templates.TemplateError) as refused:
            call(templateId, *args)
        assert refused.value.code == templates.NOT_FOUND
    assert not folder.exists()


def test_a_file_edited_by_hand_is_read_as_vodsplit_reads_it(folder):
    # A key left out is the standard's (vodsplit fills it from DEFAULT).
    _stored(folder, "20261116-120000-ab12", {"name": "手書き",
                                             "match": "杯", "title": "{p1}"})
    entry = templates.Get("20261116-120000-ab12")
    assert entry["template"] == dict(templates.Standard(), name="手書き",
                                     match="杯", title="{p1}")
    assert entry["role"] == "match"


@pytest.mark.parametrize("content", [
    "{not json", "[1]", json.dumps({"name": "x", "title": "a\nb"}),
    json.dumps({"name": "x", "chapter_format": "{round}"}),
    json.dumps({"name": "x", "chapter_format": "{time} {n[0]}"}),
    # Nested past the parser's recursion limit: a RecursionError.
    pytest.param("[" * 100000 + "]" * 100000, id="nested-too-deep")])
def test_a_broken_file_is_left_out_of_the_list_and_the_worker(folder,
                                                              content):
    folder.mkdir(parents=True)
    (folder / "20261116-120000-ab12.json").write_text(content,
                                                      encoding="utf-8")
    (folder / "notes.json").write_text("{}", encoding="utf-8")
    assert [e["id"] for e in templates.List()] == ["default"]
    assert templates.WorkerTemplates() == []
    with pytest.raises(templates.TemplateError):
        templates.Get("20261116-120000-ab12")
    # It can still be deleted (the page lists it no more; the file goes).
    assert templates.Delete("20261116-120000-ab12") is None
    assert not (folder / "20261116-120000-ab12.json").exists()


def test_a_refused_save_writes_nothing(folder):
    templates.Save("default", templates.Standard())
    before = (folder / "default.json").read_bytes()
    with pytest.raises(templates.TemplateError):
        templates.Save("default", dict(templates.Standard(), title=""))
    assert (folder / "default.json").read_bytes() == before


def test_a_template_that_cannot_be_written_is_save_failed(folder,
                                                          monkeypatch):
    def unwritable(path, content):
        raise PermissionError("denied")

    monkeypatch.setattr(templates, "_Write", unwritable)
    for call, args in ((templates.Save, ("default", templates.Standard())),
                       (templates.Duplicate, ("default",))):
        with pytest.raises(templates.TemplateError) as refused:
            call(*args)
        assert refused.value.code == templates.SAVE_FAILED
        assert "user_data" in refused.value.message


def test_a_duplicate_past_the_limit_is_refused(folder, monkeypatch):
    monkeypatch.setattr(templates, "MAX_STORED", 2)
    templates.Duplicate("default")
    templates.Duplicate("default")
    with pytest.raises(templates.TemplateError) as refused:
        templates.Duplicate("default")
    assert refused.value.code == templates.INVALID
    assert len(os.listdir(folder)) == 2


# ── the worker is given a snapshot of the stored templates ──────────────

def _snapshot(jobDir):
    return [json.loads((jobDir / "templates" / name).read_text(
        encoding="utf-8"))
        for name in sorted(os.listdir(jobDir / "templates"))]


def test_the_run_and_the_export_are_given_the_stored_templates(
        service, work, fake, tmp_path):
    templates.Save("default", templates.Standard())
    matched = templates.Duplicate("default")
    # Written by hand with spaces around its match: the worker gets it as
    # Autona reads it (stripped), not the raw file.
    folder = tmp_path / "user_data" / "archive_templates"
    (folder / f"{matched['id']}.json").write_text(json.dumps(
        dict(TEMPLATE, match=" 雷影杯 "), ensure_ascii=False),
        encoding="utf-8")
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))

    job = service.Create(SOURCE, event="雷影杯 #1")
    assert _ended(service, job["id"])["state"] == "done"
    jobDir = work / job["id"]
    paths = [str(jobDir / "templates" / name)
             for name in ("01.json", "02.json")]
    assert json.loads(argv.read_text(encoding="utf-8")) == [
        SOURCE, "--out", str(jobDir / "out"),
        "--download-dir", str(work / "downloads"), "--codec", "any",
        "--event=雷影杯 #1",
        "--no-cut", "--thumbs", "--template", paths[0], "--template",
        paths[1], "--progress", "json", "--cancel-on-stdin"]
    assert _snapshot(jobDir) == [dict(TEMPLATE), templates.Standard()]

    # Deleted since: the export is given what is stored now, and the run's
    # snapshot is replaced whole (no 02.json left behind).
    templates.Delete("default")
    service.SaveReview(job["id"], {"sets": {"1": {"confirmed": True}}})
    service.StartExport(job["id"])
    assert _export_ended(service, job["id"])["state"] == "done"
    assert json.loads(argv.read_text(encoding="utf-8")) == [
        str(jobDir / "export_request.json"), "--out", str(jobDir / "clips"),
        "--template", paths[0], "--progress", "json", "--cancel-on-stdin"]
    assert _snapshot(jobDir) == [dict(TEMPLATE)]


def test_with_no_stored_template_the_worker_is_given_none(service, work,
                                                         fake, tmp_path):
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    job = service.Create(SOURCE)
    assert _ended(service, job["id"])["state"] == "done"
    assert "--template" not in json.loads(argv.read_text(encoding="utf-8"))
    assert not (work / job["id"] / "templates").exists()


def _failing(monkeypatch, name):
    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(ArchiveJobService, name, fail)


@pytest.mark.parametrize("name", ["_WriteTemplates", "_Arguments"])
def test_a_start_that_fails_before_the_worker_leaves_no_running_job(
        service, work, fake, tmp_path, monkeypatch, name):
    # The snapshot and the arguments are made before job.json says the job
    # runs: what fails there leaves no "running" job without a worker.
    templates.Save("default", templates.Standard())
    job = service.Create(SOURCE)
    assert _ended(service, job["id"])["state"] == "done"
    service.SaveReview(job["id"], {"sets": {"1": {"confirmed": True}}})
    original = ArchiveJobService.__dict__[name]
    _failing(monkeypatch, name)

    message = _refused(archive.WORK_DIR_UNUSABLE, service.StartExport,
                       job["id"])
    assert "denied" in message
    assert _stored_record(work, job["id"])["export"] is None
    _refused(archive.WORK_DIR_UNUSABLE, service.Create, SOURCE)
    assert [record["state"] for record in service.Jobs()] == ["done"]
    assert service._run is None

    _stored_job(work, job["id"], state="cancelled", source=SOURCE)
    _refused(archive.WORK_DIR_UNUSABLE, service.Resume, job["id"])
    assert _stored_record(work, job["id"])["state"] == "cancelled"
    assert service._run is None
    # Nothing holds the one run: a start goes once the cause is gone.
    monkeypatch.setattr(ArchiveJobService, name, original)
    assert service.Resume(job["id"])["state"] == "running"
    assert _ended(service, job["id"])["state"] == "done"


def _stored_record(work, jobId):
    return json.loads((work / jobId / "job.json").read_text(encoding="utf-8"))


# ── the titles' preview (vodsplit V10 ``titles``) ────────────────────────

PREVIEW_SETS = dict(REVIEW_SETS, sets=[
    REVIEW_SETS["sets"][0], REVIEW_SETS["sets"][1],
    dict(REVIEW_SETS["sets"][2], game_starts=[1410.0, 1600.0, 1800.0])])


def _previewable(work, tmp_path, service):
    _done(work, tmp_path, PREVIEW_SETS)
    (work / DONE_ID / "results.json").write_text(json.dumps(
        {"sets": [KAPPA], "bracket_url": "https://start.gg/x",
         "date": "2026-11-16"}), encoding="utf-8")
    # Set 2 given a result by the review; set 3 not confirmed: the preview
    # shows every set as reviewed.
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True},
                                          "2": {"set": UNUSED}}})


def test_the_preview_is_the_workers_titles_of_every_set_as_reviewed(
        service, work, fake, tmp_path):
    _previewable(work, tmp_path, service)
    argv, inputs = tmp_path / "argv.json", tmp_path / "inputs.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    fake.setenv("FAKE_VODSPLIT_INPUTS", str(inputs))

    answer = service.PreviewTitles(DONE_ID, dict(TEMPLATE, extra=1))
    assert answer == {"template": "雷影杯", "sets": [
        {"no": 1, "title": "Kappa vs Lambda 3-1",
         "description": "Fake Cup Winners Final", "chapters": 0},
        {"no": 2, "title": "Mu vs Lambda 3-0",
         "description": "Fake Cup Losers Final", "chapters": 0},
        {"no": 3, "title": "Kappa vs Mu 2-3",
         "description": "Fake Cup Grand Final", "chapters": 3}]}

    called = json.loads(argv.read_text(encoding="utf-8"))
    assert os.path.basename(called[0]) == "request.json"
    assert called[1] == "--template"
    assert os.path.basename(called[2]) == "template.json"
    assert called[3:] == ["--format", "json"]
    # The temporary files are gone.
    assert not os.path.exists(os.path.dirname(called[0]))
    given = json.loads(inputs.read_text(encoding="utf-8"))
    # No video: titles needs none. The template asked for is the one used
    # (its match dropped, so the worker selects it whatever the event).
    assert given["request"] == {
        "source": None, "source_id": "fakevideo01", "event": "Fake Cup",
        "bracket_url": "https://start.gg/x", "date": "2026-11-16", "sets": [
            {"no": 1, "start": 120.0, "end": 720.0, "title": None,
             "description": None, "set": KAPPA,
             "game_starts": [130.0, 300.0]},
            {"no": 2, "start": 780.0, "end": 1380.0, "title": None,
             "description": None, "set": UNUSED, "game_starts": [790.0]},
            {"no": 3, "start": 1400.0, "end": 2000.0, "title": None,
             "description": None, "set": MU,
             "game_starts": [1410.0, 1600.0, 1800.0]}]}
    assert given["template"] == dict(TEMPLATE, match=None)
    # A preview writes nothing to the job.
    assert sorted(p.name for p in (work / DONE_ID).iterdir()) == [
        "job.json", "out", "results.json", "review.json"]


def test_a_removed_set_is_left_out_of_the_preview(service, work, fake,
                                                  tmp_path):
    _previewable(work, tmp_path, service)
    service.SaveReview(DONE_ID, {"sets": {"1": {"removed": True},
                                          "2": {"set": KAPPA}}})
    inputs = tmp_path / "inputs.json"
    fake.setenv("FAKE_VODSPLIT_INPUTS", str(inputs))
    answer = service.PreviewTitles(DONE_ID, dict(TEMPLATE))
    assert [row["no"] for row in answer["sets"]] == [2, 3]
    given = json.loads(inputs.read_text(encoding="utf-8"))
    # Set 1's result, free once it was removed, is set 2's.
    assert [(row["no"], row["set"]) for row in given["request"]["sets"]] == [
        (2, KAPPA), (3, MU)]


def test_a_set_with_no_result_has_no_title(service, work, fake, tmp_path):
    _done(work, tmp_path)
    rows = service.PreviewTitles(DONE_ID, dict(TEMPLATE))["sets"]
    assert rows[1] == {"no": 2, "title": None, "description": None,
                       "chapters": 0}


def test_an_old_worker_cannot_preview_and_says_so(service, work, fake,
                                                  tmp_path):
    fake.setenv("FAKE_VODSPLIT_NO_TITLES", "1")
    _previewable(work, tmp_path, service)
    assert service.Status()["features"] == []
    message = _refused(archive.UNSUPPORTED, service.PreviewTitles, DONE_ID,
                       dict(TEMPLATE))
    assert message == archive.TITLES_UNSUPPORTED
    assert "この版" in message


def test_a_worker_that_fails_or_takes_too_long_is_refused(
        service, work, fake, tmp_path, monkeypatch):
    _previewable(work, tmp_path, service)
    _mode(fake, "error")
    message = _refused(archive.WORKER_FAILED, service.PreviewTitles, DONE_ID,
                       dict(TEMPLATE))
    assert "編集ファイルの形式が違います" in message
    _mode(fake, "hang")
    monkeypatch.setattr(worker, "TITLES_TIMEOUT_SECS", 1)
    message = _refused(archive.UNAVAILABLE, service.PreviewTitles, DONE_ID,
                       dict(TEMPLATE))
    assert "1 秒" in message


def test_a_preview_is_refused_before_the_worker_is_asked(work, fake,
                                                         tmp_path):
    asked = []
    service = ArchiveJobService(probe=lambda: asked.append(1))
    _done(work, tmp_path)
    message = _refused(archive.INVALID, service.PreviewTitles, DONE_ID,
                       dict(TEMPLATE, chapter_format="{x}"))
    assert "{time} と {n}" in message
    _refused(archive.NOT_FOUND, service.PreviewTitles,
             "20261116-000000-zzzz", dict(TEMPLATE))
    _stored_job(work, DONE_ID, state="cancelled", source=SOURCE)
    _refused(archive.NOT_DONE, service.PreviewTitles, DONE_ID,
             dict(TEMPLATE))
    assert asked == []


def test_no_preview_while_the_jobs_analysis_runs(service, work, fake):
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    _refused(archive.BUSY, service.PreviewTitles, job["id"], dict(TEMPLATE))
    service.Cancel(job["id"])
    _ended(service, job["id"])


# A set of sets.json shorter than the worker takes (the review keeps the
# worker's own range): vodsplit's validate_edits refuses a request with it.
SHORT = {"no": 4, "start": 2100.0, "end": 2100.5, "set": None,
         "game_starts": []}


def test_a_set_the_worker_would_refuse_is_left_out_of_the_preview(
        service, work, fake, tmp_path):
    _done(work, tmp_path, dict(PREVIEW_SETS,
                               sets=PREVIEW_SETS["sets"] + [SHORT]))
    inputs = tmp_path / "inputs.json"
    fake.setenv("FAKE_VODSPLIT_INPUTS", str(inputs))
    answer = service.PreviewTitles(DONE_ID, dict(TEMPLATE))
    assert [row["no"] for row in answer["sets"]] == [1, 2, 3]
    given = json.loads(inputs.read_text(encoding="utf-8"))
    assert [row["no"] for row in given["request"]["sets"]] == [1, 2, 3]


def test_a_preview_with_no_set_the_worker_takes_is_refused(
        page, service, work, fake, tmp_path):
    _done(work, tmp_path, dict(PREVIEW_SETS, sets=[SHORT]))
    asked = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(asked))
    message = _refused(archive.NO_SETS, service.PreviewTitles, DONE_ID,
                       dict(TEMPLATE))
    assert message == archive.TITLES_NO_SETS
    assert "1 秒以上" in message
    _refusal(page.post(f"{BASE}/jobs/{DONE_ID}/titles:preview",
                       json={"template": dict(TEMPLATE)}), 409, "CONFLICT",
             archive.NO_SETS)
    # Refused before the worker is started.
    assert not asked.exists()


def test_the_titles_request_passes_the_workers_own_check(fake, work,
                                                        tmp_path):
    # The request Autona writes is what vodsplit's `titles` reads: its
    # validate_edits(need_source=False) takes it as it is. Skipped where
    # vodsplit is not there (CI).
    export = _vodsplit_export()
    if export is None:
        pytest.skip("vodsplit is not installed nor checked out beside")
    service = ArchiveJobService()
    _done(work, tmp_path, dict(REVIEW_SETS,
                               sets=REVIEW_SETS["sets"] + [SHORT]))
    (work / DONE_ID / "results.json").write_text(json.dumps(
        {"sets": [KAPPA], "bracket_url": "https://start.gg/x",
         "date": "2026-11-16"}), encoding="utf-8")
    service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True, "start": 0.13, "end": 1.14},
        "2": {"set": UNUSED, "start": 775.5},
        "3": {"set": None, "start": 3598.95, "end": 3700}}})
    request = service._TitlesRequest(str(work / DONE_ID), DONE_ID)
    checked = export.validate_edits(json.loads(json.dumps(request)),
                                    need_source=False)
    assert [(row["no"], row["start"], row["end"], row["set"])
            for row in checked["sets"]] == [
        (1, 0.13, 1.14, KAPPA), (2, 775.5, 1380.0, UNUSED),
        (3, 3598.95, 3700, None)]
    assert checked["source"] is None
    assert (checked["event"], checked["bracket_url"], checked["date"]) == (
        "Fake Cup", "https://start.gg/x", "2026-11-16")
    # The set left out is one vodsplit would refuse the whole request for.
    with pytest.raises(export.EditsError):
        export.validate_edits(dict(request, sets=request["sets"] + [
            dict(SHORT, title=None, description=None)]), need_source=False)
    with pytest.raises(export.EditsError):
        export.validate_edits(dict(request, sets=[]), need_source=False)


def _previewing(service):
    """A preview on a thread -> (its thread, what it ended with)."""
    outcome = {}

    def preview():
        try:
            outcome["answer"] = service.PreviewTitles(DONE_ID,
                                                      dict(TEMPLATE))
        except archive.ArchiveError as error:
            outcome["error"] = error

    thread = threading.Thread(target=preview, daemon=True)
    thread.start()
    return thread, outcome


def test_a_newer_preview_kills_the_older_ones_worker(service, work, fake,
                                                     tmp_path):
    _previewable(work, tmp_path, service)
    service.Status()
    _mode(fake, "hang")
    thread, outcome = _previewing(service)
    older = _until(lambda: service._titlesProcess,
                   "the older preview's worker")

    _mode(fake, "ok")
    answer = service.PreviewTitles(DONE_ID, dict(TEMPLATE))
    assert [row["no"] for row in answer["sets"]] == [1, 2, 3]
    thread.join(WAIT)
    assert outcome["error"].code == archive.SUPERSEDED
    assert outcome["error"].message == archive.TITLES_SUPERSEDED
    # Killed, not left to run out its time; nothing holds a worker now.
    assert older.poll() is not None
    assert service._titlesProcess is None


def test_the_shutdown_kills_a_running_preview(service, work, fake, tmp_path):
    _previewable(work, tmp_path, service)
    service.Status()
    _mode(fake, "hang")
    thread, outcome = _previewing(service)
    running = _until(lambda: service._titlesProcess, "the preview's worker")
    service.Shutdown(5.0)
    thread.join(WAIT)
    assert outcome["error"].code == archive.STOPPING
    assert running.poll() is not None
    _refused(archive.STOPPING, service.PreviewTitles, DONE_ID,
             dict(TEMPLATE))


def _probed_after(seconds):
    """A probe that answers a worker with ``titles`` after `seconds`."""
    found = worker.ProbeResult(True, "0.0.0-fake", 1, None, ("worker",),
                               (worker.TITLES_FEATURE,))

    def probe():
        time.sleep(seconds)
        return found

    return probe


def test_the_probe_and_the_run_end_within_the_pages_wait(
        work, fake, tmp_path, monkeypatch):
    # The page gives up at 15 s: the probe (up to 8 s) and the run share
    # TITLES_DEADLINE_SECS.
    assert archive.TITLES_DEADLINE_SECS < 15
    assert worker.PROBE_TIMEOUT_SECS + archive.TITLES_LEAST_SECS \
        <= archive.TITLES_DEADLINE_SECS
    _done(work, tmp_path, PREVIEW_SETS)
    given = []

    def run(command, requestPath, templatePath, timeout=None, started=None):
        given.append(timeout)
        return {"type": "done", "template": "x", "sets": []}

    monkeypatch.setattr(worker, "RunTitles", run)
    monkeypatch.setattr(archive, "TITLES_DEADLINE_SECS", 4)
    ArchiveJobService(probe=_probed_after(1.0)).PreviewTitles(
        DONE_ID, dict(TEMPLATE))
    assert archive.TITLES_LEAST_SECS <= given[0] <= 3.0
    # What is left after the probe is too little: refused, the worker not
    # started.
    monkeypatch.setattr(archive, "TITLES_DEADLINE_SECS", 2)
    message = _refused(archive.UNAVAILABLE, ArchiveJobService(
        probe=_probed_after(1.0)).PreviewTitles, DONE_ID, dict(TEMPLATE))
    assert message == archive.TITLES_OUT_OF_TIME
    assert len(given) == 1


# ── the v1 routes ────────────────────────────────────────────────────────

def test_the_page_edits_the_templates_through_the_routes(page, service,
                                                         folder):
    listed = _data(page.get(f"{BASE}/templates"))
    assert listed == {"templates": [templates.Get("default")]}
    assert _data(page.get(f"{BASE}/templates/default")) == {
        "template": listed["templates"][0]}

    saved = _data(page.put(f"{BASE}/templates/default", json={
        "template": dict(templates.Standard(), title="{p1} vs {p2}")}))
    assert saved["template"]["stored"] is True
    assert saved["template"]["template"]["title"] == "{p1} vs {p2}"
    copy = _data(page.post(f"{BASE}/templates/default:duplicate"), 201)
    copyId = copy["template"]["id"]
    assert copy["template"]["template"]["name"] == "標準 のコピー"
    edited = _data(page.put(f"{BASE}/templates/{copyId}",
                            json={"template": dict(TEMPLATE)}))
    assert edited["template"]["role"] == "match"
    assert [e["id"] for e in _data(page.get(f"{BASE}/templates"))[
        "templates"]] == [copyId, "default"]

    message = _refusal(page.put(f"{BASE}/templates/default", json={
        "template": dict(TEMPLATE)}), 400, "INVALID_ARGUMENT",
        archive.INVALID)
    assert "標準のテンプレート" in message
    _refusal(page.put(f"{BASE}/templates/default", json={}), 400,
             "INVALID_ARGUMENT", archive.INVALID)
    _refusal(page.put(f"{BASE}/templates/default", data="not json"), 400,
             "INVALID_ARGUMENT", archive.INVALID)

    assert _data(page.client.delete(
        f"{BASE}/templates/{copyId}", headers=page.shell)) == {
        "template": None}
    reset = _data(page.client.delete(f"{BASE}/templates/default",
                                     headers=page.shell))
    assert reset == {"template": templates.Get("default")}
    assert reset["template"]["stored"] is False

    for response in (
            page.get(f"{BASE}/templates/20261116-000000-zzzz"),
            page.put(f"{BASE}/templates/{copyId}",
                     json={"template": dict(TEMPLATE)}),
            page.post(f"{BASE}/templates/{copyId}:duplicate"),
            page.client.delete(f"{BASE}/templates/{copyId}",
                               headers=page.shell),
            page.get(f"{BASE}/templates/nope")):
        _refusal(response, 404, "NOT_FOUND", archive.NOT_FOUND)


def test_a_template_that_cannot_be_saved_is_a_conflict(page, service,
                                                       folder, monkeypatch):
    def unwritable(path, content):
        raise PermissionError("denied")

    monkeypatch.setattr(templates, "_Write", unwritable)
    _refusal(page.put(f"{BASE}/templates/default", json={
        "template": templates.Standard()}), 409, "NOT_READY",
        archive.SAVE_FAILED)


def test_the_page_previews_the_titles_through_the_routes(
        page, service, work, fake, tmp_path):
    _previewable(work, tmp_path, service)
    url = f"{BASE}/jobs/{DONE_ID}/titles:preview"
    answer = _data(page.post(url, json={"template": dict(TEMPLATE)}))
    assert answer["template"] == "雷影杯"
    assert [row["title"] for row in answer["sets"]] == [
        "Kappa vs Lambda 3-1", "Mu vs Lambda 3-0", "Kappa vs Mu 2-3"]

    _refusal(page.post(url, json={"template": dict(TEMPLATE, title="")}),
             400, "INVALID_ARGUMENT", archive.INVALID)
    _refusal(page.post(url, json={}), 400, "INVALID_ARGUMENT",
             archive.INVALID)
    _refusal(page.post(f"{BASE}/jobs/20261116-000000-zzzz/titles:preview",
                       json={"template": dict(TEMPLATE)}), 404, "NOT_FOUND",
             archive.NOT_FOUND)
    _mode(fake, "error")
    _refusal(page.post(url, json={"template": dict(TEMPLATE)}), 503,
             "UNAVAILABLE", archive.WORKER_FAILED)

    fake.setenv("FAKE_VODSPLIT_NO_TITLES", "1")
    _mode(fake, "ok")
    status = _data(page.get(f"{BASE}/status?refresh=true"))
    assert status["features"] == []
    message = _refusal(page.post(url, json={"template": dict(TEMPLATE)}),
                       409, "CONFLICT", archive.UNSUPPORTED)
    assert message == archive.TITLES_UNSUPPORTED

    fake.delenv(worker.ENV_COMMAND)
    service._Forget()
    _refusal(page.post(url, json={"template": dict(TEMPLATE)}), 503,
             "UNAVAILABLE", archive.UNAVAILABLE)
