"""PluginJobs (ADR-009 lane L8): the two lanes and busy, the job folder
(request.json / job.json against docs/plugins/contract_1.json), secrets
kept out of every file and log, the artifacts' checks, the keeping rules
(20 manual / action runs, the last failed hook run), Sweep, Shutdown and
the two callbacks.

Deterministic: a fake runner (the test plays the runner's threads by
calling the onEvent / onEnd it was given), a fake registry / inputs /
settings / secrets, a fixed clock and tmp_path. The real PluginRunner
(lane L1) with tests/fixtures/fake_plugin_worker.py is the wiring's
integration test.
"""

import datetime
import json
import os
import re
from pathlib import Path

import pytest
from loguru import logger

from src.core.PluginJobs import CheckArtifact, PluginJobs
from src.core.PluginTypes import (
    RUN_ID_PATTERN, Action, ActionTarget, Artifact, ArtifactKind,
    ArtifactRejection, Badge, DoneEvent, EndReason, ErrorEvent, Field,
    FieldType, Hook, HookEvent, InputFile, InputPick, Job, JobState, Lane,
    LogEvent, Manifest, ManifestCheck, ManifestProblem, ManifestReport,
    Permissions, PluginState, ProblemScope, ProgressEvent, Refused,
    RegisteredPlugin, RunOutcome, StageEvent, StartRefusal, Trigger,
    TriggerKind, WarningEvent)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "docs" / "plugins" / "contract_1.json")
                      .read_text(encoding="utf-8"))
PLUGIN = "example.webhook"
SECRET = "s3cr3t-value-XYZ"
TZ = datetime.timezone(datetime.timedelta(hours=9))

MANUAL = Trigger(kind=TriggerKind.MANUAL)
ACTION = Trigger(kind=TriggerKind.ACTION, target=ActionTarget.RESULT_IMAGE)
HOOK = Trigger(kind=TriggerKind.HOOK, event=HookEvent.MATCH_DECIDED,
               hook="post_match/match_decided")


def _Manifest(autonaApi="none", entry=("bin/worker.exe",)):
    return Manifest(
        manifest=1, id=PLUGIN, version="1.0.0", name="Example",
        author="someone", license="MIT", min_autona="2.0.0",
        protocol=(1,), platforms=("windows-x64",), entry=entry,
        permissions=Permissions(network=(), files="inputs", spawn=False,
                                autona_api=autonaApi),
        settings=(
            Field(key="mention", type="select", label="Mention",
                  default="none"),
            Field(key="channel", type="text", label="Channel"),
            Field(key="token", type="secret", label="Token", required=True),
        ),
        jobs=(
            Job(id="post", title="Post", lane="light", timeout_seconds=60,
                memory_mb=256,
                params=(
                    Field(key="message", type="text", label="Message",
                          default="hello"),
                    Field(key="style", type="select", label="Style"),
                    Field(key="count", type="number", label="Count",
                          required=True, default=1),
                    Field(key="state", type="state", label="State"),
                    Field(key="log", type="match_log", label="Log",
                          required=True),
                )),
            Job(id="post_image", title="Post image", lane="light",
                timeout_seconds=120, manual=False,
                params=(Field(key="image", type="result_image",
                              label="Image", required=True),),
                actions=(Action(target="result_image", label="Post"),)),
            Job(id="post_match", title="Post match", lane="light",
                timeout_seconds=120, manual=False,
                params=(Field(key="match", type="match_record",
                              label="Match", required=True),
                        Field(key="style", type="text", label="Style",
                              default="games")),
                hooks=(Hook(on="match_decided", label="On match"),)),
            Job(id="split", title="Split", lane="heavy",
                timeout_seconds=3600),
        ))


class FakeHandle:
    def __init__(self):
        self.pid = 4242
        self.cancels = 0
        self.outcome = None

    def Cancel(self):
        self.cancels += 1

    def Wait(self, timeout=None):
        return self.outcome


class FakeRunner:
    def __init__(self):
        self.started = []
        self.shutdowns = []
        self.fail = False

    def BuildEnvironment(self, parent, *, pluginDir, protocol, secrets,
                         apiUrl):
        env = {k: v for k, v in parent.items()
               if not k.upper().startswith("AUTONA_")}
        env["AUTONA_PLUGIN_DIR"] = pluginDir
        env["AUTONA_PROTOCOL"] = str(protocol)
        env.update(secrets)
        if apiUrl:
            env["AUTONA_API_URL"] = apiUrl
        return env

    def Start(self, spec, onEvent, onEnd):
        if self.fail:
            raise OSError("cannot")
        handle = FakeHandle()
        self.started.append((spec, onEvent, onEnd, handle))
        return handle

    def Shutdown(self, grace):
        self.shutdowns.append(grace)


class FakeRegistry:
    def __init__(self, folder, manifest=None, badge=Badge.MANUAL,
                 state=PluginState.READY, problems=(), enabled=True):
        self.plugin = RegisteredPlugin(
            id=PLUGIN, folder=str(folder), badge=badge,
            report=ManifestReport(manifest=manifest or _Manifest(),
                                  problems=tuple(problems)),
            state=state, enabled=enabled)

    def Get(self, pluginId):
        return self.plugin if pluginId == PLUGIN else None


class FakeInputs:
    def __init__(self):
        self.calls = []

    def Prepare(self, key, kind, inputsDir, pick):
        self.calls.append((key, kind, pick))
        if kind is FieldType.RESULT_IMAGE:
            if pick is None:
                return None
            path = os.path.join(inputsDir, key + ".png")
            Path(path).write_bytes(b"\x89PNG")
            return InputFile(kind=kind, format=1, path=path, sha256="a" * 64)
        if pick is not None and pick.choice == "bogus":
            raise LookupError("unknown choice")
        extension = ".jsonl" if kind is FieldType.MATCH_LOG else ".json"
        path = os.path.join(inputsDir, key + extension)
        Path(path).write_text("{}", encoding="utf-8")
        return InputFile(kind=kind, format=1, path=path)


class FakeSettings:
    def __init__(self):
        self.missing = ()

    def Missing(self, pluginId, manifest, *, dev):
        return self.missing


class FakeSecrets:
    def Environment(self, pluginId, *, dev):
        return {"AUTONA_SECRET_TOKEN": SECRET}


class Clock:
    def __init__(self):
        self.now = datetime.datetime(2026, 11, 26, 21, 30, 0, tzinfo=TZ)

    def __call__(self):
        return self.now

    def Advance(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)


class Suffixes:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"{self.n:04d}"


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "app" / "plugins" / PLUGIN
    (folder / "bin").mkdir(parents=True)
    (folder / "bin" / "worker.exe").write_bytes(b"MZ")
    return _Env(tmp_path, folder)


class _Env:
    def __init__(self, tmp, folder, **registry):
        self.tmp = tmp
        self.folder = folder
        self.work = tmp / "work"
        self.userData = tmp / "app" / "user_data"
        self.runner = FakeRunner()
        self.registry = FakeRegistry(folder, **registry)
        self.inputs = FakeInputs()
        self.settings = FakeSettings()
        self.clock = Clock()
        self.ended = []
        self.freed = []
        self.apiUrl = "http://127.0.0.1:5107/api/v1"
        self.jobs = PluginJobs(
            runner=self.runner, registry=self.registry, inputs=self.inputs,
            settings=self.settings, secrets=FakeSecrets(),
            workDir=lambda: str(self.work), userDataDir=str(self.userData),
            hostVersion="2.6.0", locale=lambda: "ja",
            apiUrl=lambda: self.apiUrl,
            onRunEnded=self.ended.append, onLaneFreed=self.freed.append,
            clock=self.clock, randomSuffix=Suffixes(),
            environ=lambda: {"PATH": "x", "AUTONA_PLUGIN_DEV": "leak",
                             "AUTONA_SECRET_OTHER": "other"})

    def Rebuild(self, **registry):
        self.registry.plugin = FakeRegistry(self.folder,
                                            **registry).plugin

    def StartPost(self, **params):
        return self.jobs.Start(
            PLUGIN, "post", MANUAL, params,
            {"mention": "here", "token": SECRET},
            {"log": InputPick(key="log", kind=FieldType.MATCH_LOG,
                              choice="2026-11-26")})

    def End(self, index, outcome):
        spec, onEvent, onEnd, handle = self.runner.started[index]
        onEnd(outcome)

    def RunFolder(self, record):
        return self.work / "plugins" / PLUGIN / record.run_id


def _Done(summary="ok", artifacts=(), raw=None):
    final = DoneEvent(summary=summary, artifacts=tuple(artifacts),
                      raw=raw or {})
    return RunOutcome(state=JobState.DONE, exit_code=0, final=final,
                      final_raw=raw)


FAILED = RunOutcome(state=JobState.FAILED, exit_code=1,
                    reason=EndReason.ABNORMAL_EXIT)


# ── request.json and job.json ───────────────────────────────────────────

def test_request_json_holds_the_contract_fields(env):
    record = env.StartPost(message=None)
    assert record.state is JobState.RUNNING
    assert re.fullmatch(RUN_ID_PATTERN, record.run_id)
    assert record.run_id.startswith("20261126-213000-")
    folder = env.RunFolder(record)
    data = (folder / "request.json").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    request = json.loads(data.decode("utf-8"))

    assert set(request) == set(CONTRACT["request"]["fields"])
    assert request["protocol"] == 1
    assert request["plugin"] == PLUGIN and request["job"] == "post"
    assert request["run_id"] == record.run_id
    assert request["trigger"] == {"kind": "manual"}
    assert request["locale"] == "ja"
    # Every value key: a missing / None one is its default, else null.
    assert request["params"] == {"message": "hello", "style": None,
                                 "count": 1}
    # Every non-secret setting key; the secret the caller passed is gone.
    assert request["settings"] == {"mention": "here", "channel": None}
    assert set(request["inputs"]) == {"state", "log"}
    assert request["inputs"]["log"]["kind"] == "match_log"
    assert request["inputs"]["log"]["format"] == 1
    assert os.path.isabs(request["inputs"]["log"]["path"])
    assert os.path.isabs(request["out_dir"])
    assert Path(request["out_dir"]) == folder / "out"
    assert (folder / "out").is_dir() and (folder / "inputs").is_dir()
    assert Path(request["data_dir"]) == \
        env.userData / "plugins" / PLUGIN / "data"
    assert Path(request["data_dir"]).is_dir()
    assert request["host"] == {"name": "Autona", "version": "2.6.0"}

    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert set(job) == set(CONTRACT["job_record"]["fields"])
    assert job["state"] == "running" and job["lane"] == "light"
    assert job["trigger"] == {"kind": "manual"}
    assert job["timeout_seconds"] == 60
    assert job["started_at"] == "2026-11-26T21:30:00+09:00"


def test_an_action_and_a_hook_write_their_trigger(env):
    record = env.jobs.Start(
        PLUGIN, "post_image", ACTION, {}, {"mention": "none"},
        {"image": InputPick(key="image", kind=FieldType.RESULT_IMAGE,
                            path="C:/somewhere/result.png")})
    request = json.loads((env.RunFolder(record) / "request.json")
                         .read_text(encoding="utf-8"))
    assert request["trigger"] == {"kind": "action",
                                  "target": "result_image"}
    assert request["params"] == {}
    assert request["inputs"]["image"]["sha256"] == "a" * 64
    env.End(0, _Done())

    record = env.jobs.Start(
        PLUGIN, "post_match", HOOK, {}, {},
        {"match": InputPick(key="match", kind=FieldType.MATCH_RECORD,
                            record={"id": "x"})})
    request = json.loads((env.RunFolder(record) / "request.json")
                         .read_text(encoding="utf-8"))
    assert request["trigger"] == {"kind": "hook", "event": "match_decided",
                                  "hook": "post_match/match_decided"}
    assert request["params"] == {"style": "games"}
    assert request["inputs"]["match"]["path"].endswith("match.json")


def test_the_launch_spec(env):
    record = env.StartPost()
    spec = env.runner.started[0][0]
    folder = env.RunFolder(record)
    assert spec.argv == (str(env.folder / "bin" / "worker.exe"), "run",
                         str(folder / "request.json"))
    assert spec.cwd == str(folder)
    assert spec.log_path == str(folder / "worker.log")
    assert spec.lane is Lane.LIGHT
    assert spec.timeout_seconds == 60 and spec.memory_mb == 256
    assert spec.env["AUTONA_PLUGIN_DIR"] == str(env.folder)
    assert spec.env["AUTONA_SECRET_TOKEN"] == SECRET
    # autona_api none: no API URL.
    assert "AUTONA_API_URL" not in spec.env
    env.End(0, _Done())

    # A hook's limit is min(declared, 30).
    env.jobs.Start(PLUGIN, "post_match", HOOK, {}, {},
                   {"match": InputPick(key="match",
                                       kind=FieldType.MATCH_RECORD,
                                       record={})})
    assert env.runner.started[1][0].timeout_seconds == 30
    env.End(1, _Done())

    env.Rebuild(manifest=_Manifest(autonaApi="read"))
    env.StartPost()
    assert env.runner.started[2][0].env["AUTONA_API_URL"] == env.apiUrl


def test_a_dev_entry_resolves_its_files_in_the_plugin_folder(env):
    (env.folder / "main.py").write_text("", encoding="utf-8")
    env.Rebuild(manifest=_Manifest(entry=("python", "main.py")),
                badge=Badge.DEV)
    record = env.StartPost()
    spec = env.runner.started[0][0]
    assert spec.argv[:2] == ("python", str(env.folder / "main.py"))
    request = json.loads((env.RunFolder(record) / "request.json")
                         .read_text(encoding="utf-8"))
    assert Path(request["data_dir"]) == \
        env.userData / "plugins" / "_dev" / PLUGIN / "data"


def test_a_secret_is_never_in_request_json_job_json_or_the_log(env):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    try:
        record = env.StartPost(message=SECRET[:3])
        env.End(0, _Done(summary="sent"))
        failed = env.StartPost()
        env.End(1, FAILED)
    finally:
        logger.remove(sink)
    for run in (record, failed):
        folder = env.RunFolder(run)
        for name in ("request.json", "job.json"):
            assert SECRET not in (folder / name).read_text(encoding="utf-8")
    assert lines and not any(SECRET in line for line in lines)
    # The runner did get it, in the environment only.
    assert env.runner.started[0][0].env["AUTONA_SECRET_TOKEN"] == SECRET


# ── refusals ────────────────────────────────────────────────────────────

def test_a_full_lane_is_busy_with_the_remaining_time(env):
    env.StartPost()
    env.clock.Advance(10)
    refused = env.StartPost()
    assert refused.reason is StartRefusal.BUSY
    assert refused.remaining_seconds == 50
    assert env.jobs.BusyFor(Lane.LIGHT).remaining_seconds == 50
    assert env.jobs.BusyFor(Lane.HEAVY) is None
    assert len(env.runner.started) == 1

    # The worker's eta wins over the time limit.
    onEvent = env.runner.started[0][1]
    onEvent(ProgressEvent(fraction=0.5, eta=7.5))
    assert env.jobs.BusyFor(Lane.LIGHT).remaining_seconds == 7.5

    # The heavy lane is the other slot.
    heavy = env.jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    assert heavy.lane is Lane.HEAVY
    assert env.jobs.BusyFor(Lane.HEAVY) is not None


def test_another_owner_on_the_lane_is_busy(env):
    elsewhere = Refused(reason=StartRefusal.BUSY, remaining_seconds=None)
    env.jobs._laneElsewhere = \
        lambda lane: elsewhere if lane is Lane.HEAVY else None
    assert env.jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {}) is elsewhere
    assert env.jobs.BusyFor(Lane.HEAVY) is elsewhere
    assert env.jobs.BusyFor(Lane.LIGHT) is None


def test_a_missing_required_setting_starts_nothing(env):
    env.settings.missing = ("token",)
    refused = env.StartPost()
    assert refused.reason is StartRefusal.NEEDS_SETTINGS
    assert env.runner.started == []
    assert not (env.work / "plugins" / PLUGIN).exists()
    assert env.jobs.BusyFor(Lane.LIGHT) is None


@pytest.mark.parametrize("registry, reason", [
    (dict(state=PluginState.BLOCKED), StartRefusal.BLOCKED),
    (dict(state=PluginState.INCOMPATIBLE), StartRefusal.INCOMPATIBLE),
    (dict(state=PluginState.DISABLED), StartRefusal.DISABLED),
    (dict(enabled=False), StartRefusal.DISABLED),
    (dict(problems=(ManifestProblem(code=ManifestCheck.FILL_COUNT,
                                    scope=ProblemScope.JOB, job="post"),)),
     StartRefusal.JOB_UNUSABLE),
    (dict(problems=(ManifestProblem(code=ManifestCheck.ENTRY_MISSING,
                                    scope=ProblemScope.PLUGIN),)),
     StartRefusal.INCOMPATIBLE),
])
def test_the_plugins_state_refuses(env, registry, reason):
    env.Rebuild(**registry)
    assert env.StartPost().reason is reason
    assert env.runner.started == []


def test_requests_that_do_not_fit_are_refused(env):
    pick = {"log": InputPick(key="log", kind=FieldType.MATCH_LOG,
                             choice="2026-11-26")}
    start = env.jobs.Start
    assert start("other.plugin", "post", MANUAL, {}, {}, pick).reason \
        is StartRefusal.NOT_FOUND
    assert start(PLUGIN, "nope", MANUAL, {}, {}, pick).reason \
        is StartRefusal.INVALID_REQUEST
    # Not started by hand.
    assert start(PLUGIN, "post_image", MANUAL, {}, {}, {}).reason \
        is StartRefusal.INVALID_REQUEST
    # An action the job does not have.
    assert start(PLUGIN, "post", ACTION, {}, {}, pick).reason \
        is StartRefusal.INVALID_REQUEST
    # A hook key that is not <job>/<on>.
    wrong = Trigger(kind=TriggerKind.HOOK, event=HookEvent.MATCH_DECIDED,
                    hook="post/match_decided")
    assert start(PLUGIN, "post_match", wrong, {}, {}, {}).reason \
        is StartRefusal.INVALID_REQUEST
    # An unknown param key, a required source not picked, a pick of the
    # wrong kind, an unknown choice.
    assert start(PLUGIN, "post", MANUAL, {"x": 1}, {}, pick).reason \
        is StartRefusal.INVALID_REQUEST
    assert start(PLUGIN, "post", MANUAL, {}, {}, {}).reason \
        is StartRefusal.INVALID_REQUEST
    assert start(PLUGIN, "post", MANUAL, {}, {}, {"log": InputPick(
        key="log", kind=FieldType.STATE)}).reason \
        is StartRefusal.INVALID_REQUEST
    assert start(PLUGIN, "post", MANUAL, {}, {}, {"log": InputPick(
        key="log", kind=FieldType.MATCH_LOG, choice="bogus")}).reason \
        is StartRefusal.INVALID_REQUEST
    # Not a trigger / a pick at all.
    assert start(PLUGIN, "post", None, {}, {}, pick).reason \
        is StartRefusal.INVALID_REQUEST
    assert start(PLUGIN, "post", MANUAL, {}, {}, {"log": InputPick(
        key="log", kind="video")}).reason is StartRefusal.INVALID_REQUEST
    # An action whose image is not filled.
    assert start(PLUGIN, "post_image", ACTION, {}, {}, {}).reason \
        is StartRefusal.INVALID_REQUEST
    assert env.runner.started == []
    # A refusal after the lane was reserved gives it back (and says so)
    # and leaves no folder.
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert env.freed == [Lane.LIGHT]
    assert list((env.work / "plugins" / PLUGIN).iterdir()) == []


# ── events, the end, the callbacks ──────────────────────────────────────

def test_events_update_the_record_and_the_end_calls_back(env):
    record = env.StartPost()
    spec, onEvent, onEnd, handle = env.runner.started[0]
    onEvent(StageEvent(stage="send", label={"ja": "送信", "en": "Send"}))
    onEvent(ProgressEvent(fraction=0.25))
    onEvent(LogEvent(message="line"))
    onEvent(WarningEvent(message="careful"))
    live = env.jobs.Get(PLUGIN, record.run_id)
    assert live.stage.stage == "send" and live.progress.fraction == 0.25
    assert live.log == "line" and live.warnings == ("careful",)
    assert env.jobs.Running(PLUGIN)
    assert env.ended == [] and env.freed == []

    env.clock.Advance(5)
    onEnd(_Done(summary={"ja": "送った", "en": "Sent"}))
    assert len(env.ended) == 1
    ended = env.ended[0]
    assert ended.state is JobState.DONE and ended.run_id == record.run_id
    assert ended.finished_at == "2026-11-26T21:30:05+09:00"
    assert ended.summary == {"ja": "送った", "en": "Sent"}
    assert env.freed == [Lane.LIGHT]
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert not env.jobs.Running(PLUGIN)
    folder = env.RunFolder(record)
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert job["state"] == "done" and job["warnings"] == ["careful"]
    assert job["stage"]["label"] == {"ja": "送信", "en": "Send"}
    # inputs/ goes when the run ends.
    assert not (folder / "inputs").exists()
    assert env.jobs.Get(PLUGIN, record.run_id) == ended


def test_a_dev_plugin_keeps_its_inputs(env):
    env.Rebuild(badge=Badge.DEV)
    record = env.StartPost()
    env.End(0, _Done())
    assert (env.RunFolder(record) / "inputs" / "log.jsonl").exists()


def test_an_error_end_keeps_the_error(env):
    record = env.StartPost()
    error = ErrorEvent(code="unsupported_host", message="m", hint="h")
    env.End(0, RunOutcome(state=JobState.ERROR, exit_code=2, final=error))
    got = env.jobs.Get(PLUGIN, record.run_id)
    assert got.state is JobState.ERROR and got.error == error


def test_cancel(env):
    record = env.StartPost()
    handle = env.runner.started[0][3]
    assert env.jobs.Cancel(PLUGIN, record.run_id)
    assert handle.cancels == 1
    assert env.jobs.Get(PLUGIN, record.run_id).cancel_requested
    env.End(0, RunOutcome(state=JobState.CANCELLED, exit_code=130,
                          cancel_requested=True))
    assert not env.jobs.Cancel(PLUGIN, record.run_id)
    assert env.ended[0].state is JobState.CANCELLED
    assert env.ended[0].cancel_requested


def test_a_runner_that_raises_is_a_launch_failure(env):
    env.runner.fail = True
    record = env.StartPost()
    assert record.state is JobState.FAILED
    assert record.reason is EndReason.LAUNCH_FAILED
    assert env.freed == [Lane.LIGHT] and len(env.ended) == 1


# ── keeping ─────────────────────────────────────────────────────────────

def test_the_newest_twenty_manual_runs_are_kept(env):
    ids = []
    for n in range(23):
        record = env.StartPost()
        ids.append(record.run_id)
        env.End(n, _Done())
        env.clock.Advance(1)
    folders = sorted(p.name for p in (env.work / "plugins" / PLUGIN)
                     .iterdir())
    assert folders == sorted(ids[-20:])
    listed = env.jobs.List(PLUGIN)
    assert [r.run_id for r in listed] == list(reversed(ids[-20:]))
    assert env.jobs.Get(PLUGIN, ids[0]) is None


def _StartHook(env):
    return env.jobs.Start(
        PLUGIN, "post_match", HOOK, {}, {},
        {"match": InputPick(key="match", kind=FieldType.MATCH_RECORD,
                            record={})})


def test_a_hook_run_folder_is_removed_but_the_last_failed_one(env):
    done = _StartHook(env)
    env.End(0, _Done())
    assert not env.RunFolder(done).exists()
    assert env.ended[-1].run_id == done.run_id
    env.clock.Advance(1)

    first = _StartHook(env)
    env.End(1, FAILED)
    assert (env.RunFolder(first) / "request.json").exists()
    assert (env.RunFolder(first) / "job.json").exists()
    env.clock.Advance(1)

    second = _StartHook(env)
    env.End(2, RunOutcome(state=JobState.ERROR, exit_code=2,
                          final=ErrorEvent(code="x", message="m")))
    assert not env.RunFolder(first).exists()
    assert env.RunFolder(second).exists()
    env.clock.Advance(1)

    # A success does not remove the kept failure.
    again = _StartHook(env)
    env.End(3, _Done())
    assert env.RunFolder(second).exists()
    assert not env.RunFolder(again).exists()

    # Hook runs are not listed and do not count; Get answers the kept one.
    manual = env.StartPost()
    env.End(4, _Done())
    assert [r.run_id for r in env.jobs.List(PLUGIN)] == [manual.run_id]
    assert env.jobs.Get(PLUGIN, second.run_id).state is JobState.ERROR


# ── artifacts ───────────────────────────────────────────────────────────

def test_artifacts_are_checked_at_done_and_when_served(env, tmp_path):
    record = env.StartPost()
    out = env.RunFolder(record) / "out"
    (out / "message.txt").write_text("hi", encoding="utf-8")
    (out / "result.png").write_bytes(b"\x89PNG")
    (out / "picture.gif").write_bytes(b"GIF8")
    (out / "big.txt").write_bytes(b"x" * (64 * 1024 + 1))
    outside = tmp_path / "secret.txt"
    outside.write_text("user's", encoding="utf-8")
    os.link(outside, out / "linked.txt")
    (out / "sub").mkdir()
    artifacts = [
        Artifact(path="message.txt", kind=ArtifactKind.TEXT),
        Artifact(path="result.png", kind=ArtifactKind.IMAGE),
        Artifact(path="../request.json", kind=ArtifactKind.FILE),
        Artifact(path="nothing.bin", kind=ArtifactKind.FILE),
        Artifact(path="linked.txt", kind=ArtifactKind.TEXT),
        Artifact(path="picture.gif", kind=ArtifactKind.IMAGE),
        Artifact(path="big.txt", kind=ArtifactKind.TEXT),
        Artifact(path="sub", kind=ArtifactKind.FILE),
    ]
    raw = {"type": "done",
           "artifacts": [{"path": a.path, "kind": a.kind.value}
                         for a in artifacts]
           + [{"path": "odd.bin", "kind": "video"}]
           + [{"path": f"more{n}.txt", "kind": "text"} for n in range(3)]}
    env.End(0, _Done(artifacts=artifacts, raw=raw))

    got = env.jobs.Get(PLUGIN, record.run_id)
    assert [a.path for a in got.artifacts] == ["message.txt", "result.png"]
    english = [w["en"] for w in got.warnings]
    for path, why in [("../request.json", "outside out_dir"),
                      ("nothing.bin", "missing"),
                      ("linked.txt", "a hard link"),
                      ("picture.gif", "extension not allowed"),
                      ("big.txt", "too large"), ("sub", "not a file"),
                      ("odd.bin", "unknown kind")]:
        assert f"Dropped the artifact {path} ({why})" in english
    assert "Dropped 3 artifact(s) (past 50, or unreadable)" in english

    path, kind = env.jobs.ArtifactFile(PLUGIN, record.run_id, 1)
    assert Path(path) == (out / "result.png").resolve()
    assert kind is ArtifactKind.IMAGE
    assert env.jobs.ArtifactFile(PLUGIN, record.run_id, 2) is None
    assert env.jobs.ArtifactFile(PLUGIN, record.run_id, -1) is None
    # Checked again when served: swapped for a hard link after the end.
    (out / "message.txt").unlink()
    os.link(outside, out / "message.txt")
    assert env.jobs.ArtifactFile(PLUGIN, record.run_id, 0) is None


def test_a_junction_on_the_way_is_rejected(tmp_path):
    winapi = pytest.importorskip("_winapi")
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "users"
    target.mkdir()
    (target / "file.txt").write_text("x", encoding="utf-8")
    winapi.CreateJunction(str(target), str(out / "link"))
    real, why = CheckArtifact(str(out), Artifact(path="link/file.txt",
                                                 kind=ArtifactKind.TEXT))
    assert real is None and why is ArtifactRejection.SYMLINK_OR_JUNCTION


def test_an_absolute_or_drive_path_is_outside(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for path in (str(tmp_path / "x.txt"), "C:x.txt", "a/../../x.txt", ""):
        assert CheckArtifact(str(out), Artifact(
            path=path, kind=ArtifactKind.TEXT))[1] \
            is ArtifactRejection.OUTSIDE_OUT_DIR


# ── the log ─────────────────────────────────────────────────────────────

def test_log_tail(env):
    record = env.StartPost()
    log = env.RunFolder(record) / "worker.log"
    log.write_bytes("first line\nsecond line\n三行目\n".encode("utf-8"))
    assert env.jobs.LogTail(PLUGIN, record.run_id, 1000) == \
        "first line\nsecond line\n三行目\n"
    assert env.jobs.LogTail(PLUGIN, record.run_id, 15) == "三行目\n"
    assert env.jobs.LogTail(PLUGIN, "20260101-000000-zzzz", 100) is None
    assert env.jobs.LogTail(PLUGIN, "../x", 100) is None


# ── Sweep and Shutdown ──────────────────────────────────────────────────

def test_sweep_fails_the_runs_left_running(env):
    left = env.StartPost()
    folder = env.RunFolder(left)
    # A new process: the run on disk is not one of its own.
    env.jobs = PluginJobs(
        runner=env.runner, registry=env.registry, inputs=env.inputs,
        settings=env.settings, secrets=FakeSecrets(),
        workDir=lambda: str(env.work), userDataDir=str(env.userData),
        hostVersion="2.6.0", locale=lambda: "en", apiUrl=lambda: None,
        onRunEnded=env.ended.append, onLaneFreed=env.freed.append,
        clock=env.clock)
    env.clock.Advance(60)
    current = env.StartPost()
    env.jobs.Sweep()
    swept = env.jobs.Get(PLUGIN, left.run_id)
    assert swept.state is JobState.FAILED
    assert swept.reason is EndReason.AUTONA_EXITED
    assert swept.finished_at == "2026-11-26T21:31:00+09:00"
    assert not (folder / "inputs").exists()
    assert env.jobs.Get(PLUGIN, current.run_id).state is JobState.RUNNING
    job = json.loads((env.RunFolder(current) / "job.json")
                     .read_text(encoding="utf-8"))
    assert job["state"] == "running"
    assert env.ended == []


def test_shutdown_cancels_both_lanes_then_kills_the_rest(env):
    env.StartPost()
    env.jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    light, heavy = env.runner.started[0][3], env.runner.started[1][3]
    light.outcome = RunOutcome(state=JobState.CANCELLED)
    env.jobs.Shutdown(0.0)
    assert light.cancels == 1 and heavy.cancels == 1
    assert env.runner.shutdowns == [0.0]
    assert env.StartPost().reason is StartRefusal.UNAVAILABLE


def test_shutdown_does_not_kill_when_every_run_ended(env):
    env.StartPost()
    env.runner.started[0][3].outcome = RunOutcome(state=JobState.CANCELLED)
    env.jobs.Shutdown(0.0)
    assert env.runner.shutdowns == []


def test_making_it_touches_no_file(env):
    assert not env.work.exists() and not env.userData.exists()
    assert env.jobs.List(PLUGIN) == []
    assert not env.work.exists()
