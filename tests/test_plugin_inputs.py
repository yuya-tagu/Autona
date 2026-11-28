"""PluginInputs (ADR-009 B.3 / B.4 / B.5, lane L3): the manual choices of
each source type and the files placed in a run's inputs/, held to the
golden files of docs/plugins/inputs/ and the naming rule
(``<key><ext>``, ``.jpeg`` -> ``.jpg``)."""

import datetime
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from src.core import PluginInputs as module
from src.core.PluginInputs import InputError, PluginInputs
from src.core.PluginTypes import (INPUT_FORMATS, RESULT_IMAGE_CHOICES,
                                  SHA256_PATTERN, FieldType, InputPick)

GOLDEN = Path(__file__).resolve().parent.parent / "docs" / "plugins" \
    / "inputs"
DAY = "20261126"
OTHER = ('{"id": "20261126-230000-1-b0b0", "board": 1, '
         '"started_at": "2026-11-26T23:00:00+09:00", '
         '"updated_at": "2026-11-26T23:00:00+09:00", "closed": false, '
         '"tournament": "Other Cup", "set_id": null, "phase": "", '
         '"round": "Pools", "p1": "X", "p2": "Y", "game": "sample", '
         '"c1": "", "c2": "", "score": [1, 0], "games": [{"c1": "", '
         '"c2": "", "at": "2026-11-26T23:00:00+09:00"}]}')


def _golden_lines():
    with open(GOLDEN / "match_log.jsonl", encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file if line.strip()]


def _load(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


class Host:
    """The folders and the injected callables, recording the calls."""

    def __init__(self, root):
        self.root = root
        self.out = root / "out"
        self.log = root / "user_data" / "match_log"
        self.work = root / "work"
        self.inputs = self.work / "plugins" / "example.x" / \
            "20261126-213000-a1b2" / "inputs"
        self.state = _load(GOLDEN / "state.json")
        self.openDate = None
        self.calls = []
        self.flushResult = True
        self.inputsService = PluginInputs(
            outDir=str(self.out), matchLogDir=str(self.log),
            workDir=lambda: str(self.work),
            publishedState=self._state, flushMatchLog=self._flush,
            openRecordDate=lambda: self.openDate,
            today=lambda: datetime.date(2026, 11, 27))

    def _state(self):
        self.calls.append("state")
        return self.state

    def _flush(self):
        self.calls.append("flush")
        return self.flushResult

    def write_log(self, day, lines):
        self.log.mkdir(parents=True, exist_ok=True)
        # As MatchLogService writes it: "\n" ends each line.
        (self.log / f"{day}.jsonl").write_bytes(
            "".join(line + "\n" for line in lines).encode("utf-8"))

    def image(self, folder, name, data=b"\x89PNG data", mtime=None):
        directory = self.out / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(data)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def prepare(self, key, kind, pick=None):
        return self.inputsService.Prepare(key, kind, str(self.inputs), pick)


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _pick(key, kind, **kwargs):
    return InputPick(key=key, kind=kind, **kwargs)


# ── match_log ──────────────────────────────────────────────────────────

def test_a_picked_day_is_its_lines_unchanged(host):
    golden = _golden_lines()
    host.write_log(DAY, golden)
    placed = host.prepare("log", FieldType.MATCH_LOG,
                          _pick("log", FieldType.MATCH_LOG, choice=DAY))
    assert placed.path == str(host.inputs / "log.jsonl")
    assert placed.kind == FieldType.MATCH_LOG
    assert placed.format == INPUT_FORMATS[FieldType.MATCH_LOG] == 1
    assert placed.sha256 is None
    # Byte for byte the log's lines (the golden's, not re-encoded; the
    # golden's checkout may end its lines in CRLF, the log never does).
    assert Path(placed.path).read_bytes() \
        == "".join(line + "\n" for line in golden).encode("utf-8")


def test_a_picked_tournament_is_only_its_lines(host):
    golden = _golden_lines()
    host.write_log(DAY, golden + [OTHER, "{cut by a cra"])
    placed = host.prepare("log", FieldType.MATCH_LOG, _pick(
        "log", FieldType.MATCH_LOG, choice=f"{DAY}/Example Weekly #12"))
    assert Path(placed.path).read_text(encoding="utf-8").splitlines() \
        == golden
    other = host.prepare("log", FieldType.MATCH_LOG, _pick(
        "log", FieldType.MATCH_LOG, choice=f"{DAY}/Other Cup"))
    assert Path(other.path).read_text(encoding="utf-8").splitlines() \
        == [OTHER]


@pytest.mark.parametrize("choice", [
    "20261125", "2026112", "20261340", f"{DAY}/Nobody", "../x", "",
])
def test_a_match_log_choice_naming_nothing_is_refused(host, choice):
    host.write_log(DAY, _golden_lines())
    with pytest.raises(InputError):
        host.prepare("log", FieldType.MATCH_LOG,
                     _pick("log", FieldType.MATCH_LOG, choice=choice))


def test_the_match_log_choices_are_days_and_their_tournaments(host):
    host.write_log(DAY, _golden_lines() + [OTHER])
    host.write_log("20261120", [OTHER.replace('"Other Cup"', '""')])
    host.write_log("20261121", [])
    (host.log / "notes.txt").write_text("x")
    choices = host.inputsService.Choices(FieldType.MATCH_LOG)
    assert [(c.id, c.label, c.detail) for c in choices] == [
        (DAY, "2026-11-26", "3"),
        (f"{DAY}/Example Weekly #12", "2026-11-26 Example Weekly #12", "2"),
        (f"{DAY}/Other Cup", "2026-11-26 Other Cup", "1"),
        ("20261120", "2026-11-20", "1"),
        ("20261120/", "2026-11-20 —", "1"),
    ]
    # Every choice prepares.
    for choice in choices:
        host.prepare("log", FieldType.MATCH_LOG,
                     _pick("log", FieldType.MATCH_LOG, choice=choice.id))


def test_at_the_trigger_the_open_records_day_after_the_flush(host):
    host.openDate = datetime.date(2026, 11, 26)
    golden = _golden_lines()
    host.write_log(DAY, golden[:2])

    def flush():
        # The writer's pending lines land before the copy.
        host.calls.append("flush")
        host.write_log(DAY, golden)
        return True
    host.inputsService._flushMatchLog = flush
    placed = host.prepare("log", FieldType.MATCH_LOG)
    assert host.calls == ["flush"]
    assert Path(placed.path).read_text(encoding="utf-8").splitlines() \
        == golden


def test_at_the_trigger_with_no_record_it_is_today_and_may_be_empty(host):
    host.write_log(DAY, _golden_lines())
    host.flushResult = False
    placed = host.prepare("log", FieldType.MATCH_LOG)
    # Today is the 27th: no file, an empty input.
    assert Path(placed.path).read_bytes() == b""
    assert host.calls == ["flush"]
    host.write_log("20261127", [OTHER])
    placed = host.prepare("log", FieldType.MATCH_LOG)
    assert Path(placed.path).read_text(encoding="utf-8") == OTHER + "\n"


def test_a_failing_flush_still_places_the_file(host):
    def fail():
        raise RuntimeError("writer gone")
    host.inputsService._flushMatchLog = fail
    host.openDate = datetime.date(2026, 11, 26)
    host.write_log(DAY, _golden_lines())
    placed = host.prepare("log", FieldType.MATCH_LOG)
    assert Path(placed.path).read_text(encoding="utf-8").splitlines() \
        == _golden_lines()


# ── match_record ───────────────────────────────────────────────────────

def test_a_match_record_is_the_triggers_line(host):
    record = _load(GOLDEN / "match_record.json")
    placed = host.prepare("match", FieldType.MATCH_RECORD, _pick(
        "match", FieldType.MATCH_RECORD, record=record))
    assert placed.path == str(host.inputs / "match.json")
    assert placed.format == 1 and placed.sha256 is None
    assert _load(placed.path) == record
    assert list(_load(placed.path)) == list(record)
    # Nothing fills it without its trigger.
    assert host.prepare("match", FieldType.MATCH_RECORD) is None


def test_a_match_record_is_never_chosen(host):
    with pytest.raises(InputError):
        host.prepare("match", FieldType.MATCH_RECORD,
                     _pick("match", FieldType.MATCH_RECORD, choice=DAY))
    with pytest.raises(ValueError):
        host.inputsService.Choices(FieldType.MATCH_RECORD)


# ── state ──────────────────────────────────────────────────────────────

def test_the_state_is_the_published_one_at_the_press(host):
    placed = host.prepare("board", FieldType.STATE)
    assert placed.path == str(host.inputs / "board.json")
    assert placed.format == 1
    assert _load(placed.path) == _load(GOLDEN / "state.json")
    assert host.calls == ["state"]
    # A manual pick of a state is the same: no choice.
    host.prepare("board", FieldType.STATE,
                 _pick("board", FieldType.STATE))
    assert host.calls == ["state", "state"]
    assert host.inputsService.Choices(FieldType.STATE) == ()


def test_the_states_int_keys_are_written_as_program_state_json_does(host):
    host.state = {"score": {"1": {"team": {"1": {"player": {"1": {
        "character": {1: {"codename": "fighter_1"}}}}}}}}}
    placed = host.prepare("board", FieldType.STATE)
    assert _load(placed.path)["score"]["1"]["team"]["1"]["player"]["1"][
        "character"] == {"1": {"codename": "fighter_1"}}


def test_a_state_is_never_picked(host):
    with pytest.raises(InputError):
        host.prepare("board", FieldType.STATE,
                     _pick("board", FieldType.STATE, choice="x"))


# ── result_image / thumbnail_image ─────────────────────────────────────

@pytest.mark.parametrize("kind, folder", [
    (FieldType.RESULT_IMAGE, "results"),
    (FieldType.THUMBNAIL_IMAGE, "thumbnails"),
])
@pytest.mark.parametrize("name, extension", [
    ("top8.png", ".png"), ("top8.PNG", ".png"), ("top8.jpg", ".jpg"),
    ("top8.jpeg", ".jpg"), ("top8.JPEG", ".jpg"),
])
def test_an_image_is_copied_with_its_sha256(host, kind, folder, name,
                                            extension):
    data = b"\xff\xd8 image bytes " + name.encode()
    host.image(folder, name, data)
    placed = host.prepare("image", kind,
                          _pick("image", kind, choice=name))
    assert placed.path == str(host.inputs / ("image" + extension))
    assert Path(placed.path).read_bytes() == data
    assert placed.sha256 == hashlib.sha256(data).hexdigest()
    assert re.fullmatch(SHA256_PATTERN, placed.sha256)
    assert placed.kind == kind and placed.format == 1


def test_the_triggers_image_is_taken_from_its_folder_only(host, tmp_path):
    saved = host.image("results", "saved.png", b"saved")
    placed = host.prepare("image", FieldType.RESULT_IMAGE, _pick(
        "image", FieldType.RESULT_IMAGE, path=str(saved)))
    assert Path(placed.path).read_bytes() == b"saved"
    elsewhere = tmp_path / "elsewhere.png"
    elsewhere.write_bytes(b"x")
    thumbnail = host.image("thumbnails", "t.png")
    for path in (elsewhere, thumbnail, host.out / "results" / "gone.png"):
        with pytest.raises(InputError):
            host.prepare("image", FieldType.RESULT_IMAGE, _pick(
                "image", FieldType.RESULT_IMAGE, path=str(path)))


def test_a_sample_image_is_taken_from_the_golden_inputs(host, tmp_path):
    # A dev plugin's sample run of a result_image_saved hook hands the
    # stand-in image of the golden inputs (ADR-009 D.6): taken as a
    # trigger's path, never as a choice, and only inside samplesDir.
    service = PluginInputs(
        outDir=str(host.out), matchLogDir=str(host.log),
        workDir=lambda: str(host.work), publishedState=lambda: {},
        flushMatchLog=lambda: True, openRecordDate=lambda: None,
        samplesDir=str(GOLDEN))
    sample = GOLDEN / "result_image.png"
    placed = service.Prepare("image", FieldType.RESULT_IMAGE,
                             str(host.inputs), _pick(
                                 "image", FieldType.RESULT_IMAGE,
                                 path=str(sample)))
    assert Path(placed.path).read_bytes() == sample.read_bytes()
    assert placed.sha256 == hashlib.sha256(sample.read_bytes()).hexdigest()
    elsewhere = tmp_path / "elsewhere.png"
    elsewhere.write_bytes(b"x")
    for pick in (_pick("image", FieldType.RESULT_IMAGE, path=str(elsewhere)),
                 _pick("image", FieldType.RESULT_IMAGE,
                       choice="result_image.png")):
        with pytest.raises(InputError):
            service.Prepare("image", FieldType.RESULT_IMAGE,
                            str(host.inputs), pick)
    # Without samplesDir (the default) the golden image is refused too.
    with pytest.raises(InputError):
        host.prepare("image", FieldType.RESULT_IMAGE, _pick(
            "image", FieldType.RESULT_IMAGE, path=str(sample)))


@pytest.mark.parametrize("choice", [
    "gone.png", "../secret.png", "..\\secret.png", "sub/top8.png",
    ".hidden.png", "notes.txt", "",
])
def test_an_image_choice_naming_nothing_is_refused(host, choice):
    host.image("results", "top8.png")
    host.image("results", "notes.txt")
    (host.out / "secret.png").write_bytes(b"x")
    with pytest.raises(InputError):
        host.prepare("image", FieldType.RESULT_IMAGE,
                     _pick("image", FieldType.RESULT_IMAGE, choice=choice))


def test_an_image_without_a_pick_is_not_filled(host):
    assert host.prepare("image", FieldType.RESULT_IMAGE) is None
    assert host.prepare("image", FieldType.THUMBNAIL_IMAGE) is None


def test_the_result_choices_are_the_newest_thirty(host):
    for n in range(RESULT_IMAGE_CHOICES + 5):
        host.image("results", f"r{n:02}.png" if n % 2 else f"r{n:02}.jpg",
                   mtime=1_700_000_000 + n)
    host.image("results", "notes.txt")
    host.image("results", ".tmp.png")
    choices = host.inputsService.Choices(FieldType.RESULT_IMAGE)
    assert len(choices) == RESULT_IMAGE_CHOICES
    assert [choice.id for choice in choices][:3] == [
        "r34.jpg", "r33.png", "r32.jpg"]
    assert all(choice.id == choice.label for choice in choices)
    assert all(choice.detail for choice in choices)


def test_the_thumbnail_choices_are_every_thumbnail(host):
    for n in range(RESULT_IMAGE_CHOICES + 2):
        host.image("thumbnails", f"t{n:02}.png", mtime=1_700_000_000 + n)
    choices = host.inputsService.Choices(FieldType.THUMBNAIL_IMAGE)
    assert len(choices) == RESULT_IMAGE_CHOICES + 2
    assert choices[0].id == f"t{RESULT_IMAGE_CHOICES + 1:02}.png"


def test_no_folder_means_no_choices(host):
    assert host.inputsService.Choices(FieldType.RESULT_IMAGE) == ()
    assert host.inputsService.Choices(FieldType.MATCH_LOG) == ()


# ── archive_export ─────────────────────────────────────────────────────

def _export_job(host, clips):
    job = host.work / "20261126-200000-ab12"
    (job / "clips").mkdir(parents=True)
    for clip in clips:
        if clip.get("exported") and isinstance(clip.get("file"), str) \
                and "/" not in clip["file"]:
            (job / "clips" / clip["file"]).write_bytes(b"mp4")
    (job / "clips" / "export.json").write_text(
        json.dumps({"clips": clips}), encoding="utf-8")
    return job


def test_an_archive_export_lists_its_exported_clips(host):
    job = _export_job(host, [
        {"no": 1, "file": "01_A vs B.mp4", "title": "A vs B", "start": 1.5,
         "end": 300, "exported": True, "skipped": False},
        {"no": 2, "file": "02.mp4", "title": "skipped", "exported": True,
         "skipped": True},
        {"no": 3, "file": "03.mp4", "title": "not yet", "exported": False},
        {"no": 4, "file": "../escape.mp4", "exported": True},
        {"no": "5", "file": "05.mp4", "exported": True},
    ])
    (job / "clips" / "04_gone.mp4").unlink(missing_ok=True)
    placed = host.prepare("export", FieldType.ARCHIVE_EXPORT, _pick(
        "export", FieldType.ARCHIVE_EXPORT, path=str(job)))
    assert placed.path == str(host.inputs / "export.json")
    assert placed.format == 1 and placed.sha256 is None
    clipsDir = str(job / "clips")
    assert _load(placed.path) == {
        "job": "20261126-200000-ab12", "clips_dir": clipsDir,
        "clips": [{"no": 1, "path": os.path.join(clipsDir, "01_A vs B.mp4"),
                   "file": "01_A vs B.mp4", "title": "A vs B",
                   "start": 1.5, "end": 300}]}


def test_an_archive_export_is_only_a_job_folder_of_the_work_folder(
        host, tmp_path):
    job = _export_job(host, [])
    stranger = tmp_path / "20261126-200000-zz99"
    (stranger / "clips").mkdir(parents=True)
    (stranger / "clips" / "export.json").write_text('{"clips": []}')
    notAJob = host.work / "plugins"
    notAJob.mkdir(parents=True, exist_ok=True)
    for path in (stranger, notAJob, host.work / "20261126-200000-none"):
        with pytest.raises(InputError):
            host.prepare("export", FieldType.ARCHIVE_EXPORT, _pick(
                "export", FieldType.ARCHIVE_EXPORT, path=str(path)))
    (job / "clips" / "export.json").unlink()
    with pytest.raises(InputError):
        host.prepare("export", FieldType.ARCHIVE_EXPORT, _pick(
            "export", FieldType.ARCHIVE_EXPORT, path=str(job)))
    assert host.prepare("export", FieldType.ARCHIVE_EXPORT) is None


def test_the_archive_names_are_the_archive_jobs():
    from src.core import ArchiveJobService
    assert module.CLIPS_DIR == ArchiveJobService.CLIPS_DIR
    assert module.EXPORT_FILE == ArchiveJobService.EXPORT_FILE
    assert module.ARCHIVE_JOB_ID_PATTERN \
        == ArchiveJobService.JOB_ID_PATTERN.pattern


# ── the rules every type shares ────────────────────────────────────────

@pytest.mark.parametrize("key", ["Image", "1x", "a/b", "..", "", "a" * 33])
def test_a_bad_key_is_refused(host, key):
    with pytest.raises(ValueError):
        host.prepare(key, FieldType.STATE)


def test_a_pick_of_another_field_or_type_is_refused(host):
    with pytest.raises(ValueError):
        host.prepare("a", FieldType.STATE, _pick("b", FieldType.STATE))
    with pytest.raises(ValueError):
        host.prepare("a", FieldType.STATE,
                     _pick("a", FieldType.MATCH_LOG, choice=DAY))


def test_the_inputs_folder_must_be_in_the_work_folder(host, tmp_path):
    with pytest.raises(ValueError):
        host.inputsService.Prepare("board", FieldType.STATE,
                                   str(tmp_path / "elsewhere"), None)


def test_a_value_type_is_not_a_source(host):
    with pytest.raises(ValueError):
        host.prepare("name", FieldType.TEXT)
    with pytest.raises(ValueError):
        host.inputsService.Choices(FieldType.TEXT)


def test_nothing_is_read_or_written_on_construction(host):
    assert not host.root.joinpath("out").exists()
    assert not host.root.joinpath("work").exists()
    assert host.calls == []


# ── review follow-ups ──────────────────────────────────────────────────

ODD_NAME = "Cup Two Three\x85Four"


def test_a_line_breaks_only_at_newline(host):
    # U+2028 / U+2029 / U+0085 in a name are not line ends.
    odd = OTHER.replace("Other Cup", ODD_NAME)
    host.write_log(DAY, [odd, OTHER])
    placed = host.prepare("log", FieldType.MATCH_LOG,
                          _pick("log", FieldType.MATCH_LOG, choice=DAY))
    assert Path(placed.path).read_bytes() \
        == (odd + "\n" + OTHER + "\n").encode("utf-8")
    choices = host.inputsService.Choices(FieldType.MATCH_LOG)
    assert f"{DAY}/{ODD_NAME}" in [c.id for c in choices]
    picked = host.prepare("log", FieldType.MATCH_LOG, _pick(
        "log", FieldType.MATCH_LOG, choice=f"{DAY}/{ODD_NAME}"))
    assert Path(picked.path).read_bytes() == (odd + "\n").encode("utf-8")


def test_crlf_lines_are_read_as_lines(host):
    host.log.mkdir(parents=True)
    (host.log / f"{DAY}.jsonl").write_bytes(
        (OTHER + "\r\n").encode("utf-8"))
    placed = host.prepare("log", FieldType.MATCH_LOG,
                          _pick("log", FieldType.MATCH_LOG, choice=DAY))
    assert Path(placed.path).read_bytes() == (OTHER + "\n").encode("utf-8")


def test_a_days_choices_are_read_again_only_when_its_file_changed(host):
    host.write_log(DAY, _golden_lines())
    host.write_log("20261120", [OTHER])
    service = host.inputsService
    reads = []
    real = service._ReadLog

    def counting(day):
        reads.append(day)
        return real(day)
    service._ReadLog = counting
    first = service.Choices(FieldType.MATCH_LOG)
    assert sorted(reads) == ["20261120", DAY]
    reads.clear()
    assert service.Choices(FieldType.MATCH_LOG) == first
    assert reads == []
    # The writer appends: that day only is read again.
    with open(host.log / f"{DAY}.jsonl", "ab") as file:
        file.write((OTHER + "\n").encode("utf-8"))
    choices = service.Choices(FieldType.MATCH_LOG)
    assert reads == [DAY]
    assert (f"{DAY}/Other Cup", "2026-11-26 Other Cup", "1") in [
        (c.id, c.label, c.detail) for c in choices]
    # A day removed leaves the cache.
    (host.log / "20261120.jsonl").unlink()
    service.Choices(FieldType.MATCH_LOG)
    assert "20261120" not in service._dayChoices


def test_an_image_with_a_time_out_of_range_is_left_out(host, monkeypatch):
    host.image("results", "good.png", mtime=1_700_000_000)
    host.image("results", "odd.png", mtime=1_600_000_000)
    real = datetime.datetime

    class Odd(real):
        @classmethod
        def fromtimestamp(cls, stamp, *args, **kwargs):
            if int(stamp) == 1_600_000_000:
                raise OverflowError("timestamp out of range")
            return real.fromtimestamp(stamp, *args, **kwargs)
    monkeypatch.setattr(module.datetime, "datetime", Odd)
    choices = host.inputsService.Choices(FieldType.RESULT_IMAGE)
    assert [choice.id for choice in choices] == ["good.png"]


def test_the_work_folder_itself_is_not_an_inputs_folder(host):
    host.work.mkdir(parents=True)
    with pytest.raises(ValueError):
        host.inputsService.Prepare("board", FieldType.STATE,
                                   str(host.work), None)


def _junction(link, target):
    import subprocess
    import sys
    if sys.platform != "win32":
        os.symlink(target, link, target_is_directory=True)
        return
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                           str(target)], capture_output=True)
    if done.returncode != 0:
        pytest.skip("no junction could be made")


def test_an_export_whose_clips_folder_leads_elsewhere_is_refused(
        host, tmp_path):
    job = host.work / "20261126-200000-ab12"
    job.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "01.mp4").write_bytes(b"mp4")
    (outside / "export.json").write_text(json.dumps({"clips": [
        {"no": 1, "file": "01.mp4", "exported": True}]}))
    _junction(job / "clips", outside)
    try:
        with pytest.raises(InputError):
            host.prepare("export", FieldType.ARCHIVE_EXPORT, _pick(
                "export", FieldType.ARCHIVE_EXPORT, path=str(job)))
    finally:
        # The junction itself, never what it leads to.
        os.rmdir(job / "clips")


def test_the_image_folders_are_the_render_lanes():
    from src.core import ResultService, ThumbnailService
    for service, name in ((ResultService, module.RESULTS_DIR),
                          (ThumbnailService, module.THUMBNAILS_DIR)):
        directory = service.OUTPUT_DIRECTORY
        assert os.path.basename(directory) == name
        assert os.path.normpath(os.path.dirname(directory)) \
            == os.path.normpath("./out")
