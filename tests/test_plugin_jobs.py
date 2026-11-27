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
import threading
import time
from pathlib import Path

import pytest
from loguru import logger

from src.core.PluginJobs import (HOST_KEY, CheckArtifact, HostAlive,
                                  PluginJobs, RecordJson, ThisHost)
from src.core.PluginTypes import (
    INPUT_UNAVAILABLE, RUN_ID_PATTERN, Action, ActionTarget, Artifact, ArtifactKind,
    ArtifactRejection, Badge, DoneEvent, EndReason, ErrorEvent, Field,
    FieldType, Hook, HookEvent, InputFile, InputPick, Job, JobState, Lane,
    LogEvent, Manifest, ManifestCheck, ManifestProblem, ManifestReport,
    Permissions, PluginState, ProblemScope, ProgressEvent, RegisteredPlugin, RunOutcome, StageEvent, StartRefusal, Trigger,
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
                    Field(key="older", type="match_log", label="Older"),
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
        self.kills = 0
        self.outcome = None

    def Cancel(self):
        self.cancels += 1

    def Kill(self):
        self.kills += 1

    def Wait(self, timeout=None):
        return self.outcome


class FakeRunner:
    def __init__(self):
        self.started = []
        self.shutdowns = []
        self.fail = False
        self.during = None

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
            raise OSError(2, "cannot", "C:/Users/someone/secret/path")
        handle = FakeHandle()
        self.started.append((spec, onEvent, onEnd, handle))
        if self.during is not None:
            self.during(onEnd)
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
        self.during = None

    def Prepare(self, key, kind, inputsDir, pick):
        self.calls.append((key, kind, pick))
        if self.during is not None:
            self.during()
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
        return f"{self.n:03d}"


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
    assert record.run_id == "20261126-213000-0001"
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
    assert set(request["inputs"]) == {"state", "log", "older"}
    # An optional source not picked by hand is null (Prepare not asked);
    # STATE is always the press's.
    assert request["inputs"]["older"] is None
    assert request["inputs"]["state"]["kind"] == "state"
    assert [call[0] for call in env.inputs.calls] == ["state", "log"]
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
    # The contract's keys, and the host's private one (the Sweep's: which
    # process wrote it), which no RunRecord reads and no route serves.
    assert set(job) == set(CONTRACT["job_record"]["fields"]) | {HOST_KEY}
    assert job[HOST_KEY] == ThisHost()
    assert HOST_KEY not in RecordJson(env.jobs.Get(PLUGIN, record.run_id))
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
    # The environment holds the secrets: never in a repr (a log line).
    assert SECRET not in repr(spec)
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


def test_another_owner_claims_a_lane(env):
    jobs = env.jobs
    assert jobs.Claim(Lane.HEAVY, "taguyan.vodsplit")
    assert not jobs.Claim(Lane.HEAVY, "someone.else")
    refused = jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    assert refused.reason is StartRefusal.BUSY
    assert refused.remaining_seconds is None
    assert jobs.BusyFor(Lane.HEAVY) == refused
    assert jobs.BusyFor(Lane.LIGHT) is None
    assert jobs.Running("taguyan.vodsplit") and not jobs.Running(PLUGIN)
    # Only its owner gives it back; onLaneFreed on the releasing thread.
    jobs.Release(Lane.HEAVY, "someone.else")
    assert jobs.BusyFor(Lane.HEAVY) is not None and env.freed == []
    jobs.Release(Lane.HEAVY, "taguyan.vodsplit")
    assert jobs.BusyFor(Lane.HEAVY) is None and env.freed == [Lane.HEAVY]
    # A lane a run holds cannot be claimed; nothing while shutting down.
    jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    assert not jobs.Claim(Lane.HEAVY, "taguyan.vodsplit")
    jobs.Shutdown(0.0)
    assert not jobs.Claim(Lane.LIGHT, "taguyan.vodsplit")


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
    # A pick under another key, a STATE pick.
    assert start(PLUGIN, "post", MANUAL, {}, {}, {"log": InputPick(
        key="older", kind=FieldType.MATCH_LOG)}).reason \
        is StartRefusal.INVALID_REQUEST
    assert start(PLUGIN, "post", MANUAL, {}, {}, {**pick, "state": InputPick(
        key="state", kind=FieldType.STATE)}).reason \
        is StartRefusal.INVALID_REQUEST
    # An action whose image is not filled.
    assert start(PLUGIN, "post_image", ACTION, {}, {}, {}).reason \
        is StartRefusal.INVALID_REQUEST
    assert env.runner.started == []
    # A refusal after the lane was reserved gives it back (onLaneFreed, so
    # a pending hook is tried again; never onRunEnded) and leaves no folder.
    # The unknown choice is one (its input could not be filled): its
    # detail says the input, not the values (a hook stays on).
    assert start(PLUGIN, "post", MANUAL, {}, {}, {"log": InputPick(
        key="log", kind=FieldType.MATCH_LOG, choice="bogus")}).detail         .startswith(INPUT_UNAVAILABLE)
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert env.freed == [Lane.LIGHT, Lane.LIGHT] and env.ended == []
    assert list((env.work / "plugins" / PLUGIN).iterdir()) == []


def test_the_built_in_plugin_is_not_started_here(env):
    env.Rebuild(badge=Badge.BUILTIN)
    assert env.StartPost().reason is StartRefusal.JOB_UNUSABLE


def test_an_installed_entry_outside_its_folder_is_refused(env):
    env.Rebuild(manifest=_Manifest(entry=("../other/worker.exe",)))
    assert env.StartPost().reason is StartRefusal.INCOMPATIBLE


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


def test_a_held_plugin_starts_nothing_until_it_is_given_back(env):
    # The swap and a removal (PluginInstaller) hold the plugin's starts:
    # refused unavailable (a pending hook is then discarded), counted.
    assert env.jobs.Hold(PLUGIN) is True
    assert env.jobs.Hold(PLUGIN) is True
    refused = env.StartPost()
    assert refused.reason is StartRefusal.UNAVAILABLE
    assert env.runner.started == []
    env.jobs.Unhold(PLUGIN)
    assert env.StartPost().reason is StartRefusal.UNAVAILABLE
    env.jobs.Unhold(PLUGIN)
    assert env.StartPost().run_id
    # Another plugin's hold does not touch it.
    env.End(0, _Done())
    env.jobs.Hold("other.plugin")
    assert env.StartPost().run_id
    env.jobs.Unhold("other.plugin")
    env.jobs.Unhold("never.held")  # nothing to give back: no error


def test_a_hold_while_a_run_holds_a_lane_answers_false(env):
    env.StartPost()
    assert env.jobs.Hold(PLUGIN) is False
    env.jobs.Unhold(PLUGIN)
    # A claimed lane is its owner's run too.
    assert env.jobs.Claim(Lane.HEAVY, "taguyan.vodsplit")
    assert env.jobs.Hold("taguyan.vodsplit") is False
    env.jobs.Unhold("taguyan.vodsplit")


def test_cancel_all_asks_every_run_of_the_plugin(env):
    assert env.jobs.CancelAll(PLUGIN) == 0
    light = env.StartPost()
    heavy = env.jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    assert env.jobs.CancelAll(PLUGIN) == 2
    assert [item[3].cancels for item in env.runner.started] == [1, 1]
    assert env.jobs.Get(PLUGIN, light.run_id).cancel_requested
    assert env.jobs.Get(PLUGIN, heavy.run_id).cancel_requested
    assert env.jobs.CancelAll("other.plugin") == 0
    # A claimed lane is not a run of the plugin: its owner's.
    env.End(1, RunOutcome(state=JobState.CANCELLED, cancel_requested=True))
    assert env.jobs.Claim(Lane.HEAVY, PLUGIN)
    env.End(0, RunOutcome(state=JobState.CANCELLED, cancel_requested=True))
    assert env.jobs.CancelAll(PLUGIN) == 0


def test_a_run_writing_its_folder_still_counts_as_running(env):
    # The lane is freed before job.json is written (_Settle): a removal
    # waiting for Running must not take the folder under that write.
    env.StartPost()
    seen = []
    settle = env.jobs._Settle

    def watched(run, record):
        seen.append((env.jobs.Running(PLUGIN), env.jobs.Hold(PLUGIN)))
        env.jobs.Unhold(PLUGIN)
        return settle(run, record)

    env.jobs._Settle = watched
    env.End(0, _Done())
    assert seen == [(True, False)]
    assert not env.jobs.Running(PLUGIN) and env.jobs.Hold(PLUGIN)


def test_a_start_planned_before_a_hold_is_refused_at_its_lane(env):
    # The plan reads the registry (enabled, blocked, the manifest) outside
    # the lock: a disable / block / swap / removal held and given back in
    # between makes that plan stale -- refused, nothing launched.
    plan = env.jobs._Plan

    def stale(*args):
        planned = plan(*args)
        env.jobs.Hold(PLUGIN)
        env.jobs.Unhold(PLUGIN)
        return planned

    env.jobs._Plan = stale
    refused = env.StartPost()
    assert refused.reason is StartRefusal.UNAVAILABLE
    assert env.runner.started == []
    assert not env.jobs.Running(PLUGIN)
    # Another plugin's hold does not make it stale.
    env.jobs._Plan = lambda *args: (env.jobs.Hold("other.plugin"),
                                    env.jobs.Unhold("other.plugin"),
                                    plan(*args))[2]
    assert env.StartPost().run_id
    env.End(0, _Done())
    # A plan read after the hold was given back starts.
    env.jobs._Plan = plan
    assert env.StartPost().run_id


def _Logged(action):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    try:
        result = action()
    finally:
        logger.remove(sink)
    return result, "\n".join(lines)


def test_a_runner_that_raises_gives_the_lane_back(env):
    env.runner.fail = True
    refused, log = _Logged(env.StartPost)
    assert refused.reason is StartRefusal.UNAVAILABLE
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    # The lane given back is told (a pending hook is tried again).
    assert env.freed == [Lane.LIGHT] and env.ended == []
    assert list((env.work / "plugins" / PLUGIN).iterdir()) == []
    # The exception's type and errno only, never its path.
    assert "(ENOENT)" in log and "someone" not in log


def test_any_failure_while_preparing_gives_the_lane_back(env):
    def boom():
        raise RuntimeError("C:/Users/someone/private")
    env.inputs.during = boom
    refused, log = _Logged(env.StartPost)
    assert refused.reason is StartRefusal.UNAVAILABLE
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert env.freed == [Lane.LIGHT] and env.runner.started == []
    assert "RuntimeError" in log and "someone" not in log
    env.inputs.during = None
    assert env.StartPost().state is JobState.RUNNING


def test_a_work_folder_that_cannot_be_made_is_unavailable(env):
    env.work.parent.mkdir(parents=True, exist_ok=True)
    env.work.write_text("a file", encoding="utf-8")
    assert env.StartPost().reason is StartRefusal.UNAVAILABLE
    assert env.jobs.BusyFor(Lane.LIGHT) is None


def test_a_cancel_while_preparing_launches_nothing(env):
    def cancel():
        if env.inputs.calls[-1][0] == "log":
            (run,) = env.jobs._Runs()
            assert env.jobs.Cancel(PLUGIN, run.runId)
    env.inputs.during = cancel
    record = env.StartPost()
    assert record.state is JobState.CANCELLED and record.cancel_requested
    assert env.runner.started == []
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    # Returned by Start: no onRunEnded; the lane given back is told.
    assert env.ended == [] and env.freed == [Lane.LIGHT]
    job = json.loads((env.RunFolder(record) / "job.json")
                     .read_text(encoding="utf-8"))
    assert job["state"] == "cancelled"


def test_an_end_before_the_handle_is_kept(env):
    env.runner.during = lambda onEnd: onEnd(_Done())
    record = env.StartPost()
    assert len(env.ended) == 1 and env.freed == [Lane.LIGHT]
    assert env.jobs.Get(PLUGIN, record.run_id).state is JobState.DONE
    assert not env.jobs.Cancel(PLUGIN, record.run_id)
    assert env.runner.started[0][3].cancels == 0


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
    (env.RunFolder(first) / "out" / "a.txt").write_text("x", encoding="utf-8")
    env.End(1, RunOutcome(
        state=JobState.FAILED, reason=EndReason.TIMED_OUT, exit_code=0,
        final=DoneEvent(summary="late", artifacts=(
            Artifact(path="a.txt", kind=ArtifactKind.TEXT),))))
    assert (env.RunFolder(first) / "request.json").exists()
    assert (env.RunFolder(first) / "job.json").exists()
    # A hook run keeps no artifact.
    assert not (env.RunFolder(first) / "out").exists()
    assert not (env.RunFolder(first) / "inputs").exists()
    assert env.ended[-1].artifacts == () and env.ended[-1].summary == "late"
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


def test_a_user_refused_busy_is_waiting_once_the_lane_frees(env):
    # D.3: the dispatcher holds the pending hooks for that request.
    hook = _StartHook(env)
    assert not env.jobs.UserWaiting(Lane.LIGHT)
    assert env.StartPost().reason is StartRefusal.BUSY
    # Still held: not waiting yet (the lane is not free).
    assert not env.jobs.UserWaiting(Lane.LIGHT)
    env.End(0, _Done())
    assert env.ended[-1].run_id == hook.run_id
    assert env.jobs.UserWaiting(Lane.LIGHT)
    # The request pressed again takes the lane: no longer waiting.
    manual = env.StartPost()
    assert manual.state is JobState.RUNNING
    assert not env.jobs.UserWaiting(Lane.LIGHT)
    env.End(1, _Done())
    assert not env.jobs.UserWaiting(Lane.LIGHT)
    env.clock.Advance(1)
    # A hook refused busy is not the user's.
    again = env.StartPost()
    assert _StartHook(env).reason is StartRefusal.BUSY
    env.End(2, _Done())
    assert again.run_id == env.ended[-1].run_id
    assert not env.jobs.UserWaiting(Lane.LIGHT)


def _StartSample(env):
    return env.jobs.Start(
        PLUGIN, "post_match", HOOK, {}, {},
        {"match": InputPick(key="match", kind=FieldType.MATCH_RECORD,
                            record={})}, sample=True)


def test_a_sample_run_is_kept_like_a_manual_one(env):
    # PluginHooks.TrySample: a hook trigger whose folder stays after a
    # success (GET runs/<id> and its log), artifacts included; not listed.
    sample = _StartSample(env)
    assert sample.trigger == HOOK
    (env.RunFolder(sample) / "out" / "a.txt").write_text("x",
                                                       encoding="utf-8")
    env.End(0, RunOutcome(state=JobState.DONE, exit_code=0,
                          final=DoneEvent(summary="ok", artifacts=(
                              Artifact(path="a.txt",
                                       kind=ArtifactKind.TEXT),))))
    assert env.ended[-1].run_id == sample.run_id
    assert (env.RunFolder(sample) / "out" / "a.txt").exists()
    got = env.jobs.Get(PLUGIN, sample.run_id)
    assert got.state is JobState.DONE and len(got.artifacts) == 1
    assert env.jobs.LogTail(PLUGIN, sample.run_id, 4096) is not None
    assert env.jobs.List(PLUGIN) == []
    env.clock.Advance(1)
    # A real hook run's success still leaves nothing, and the sample stays.
    hook = _StartHook(env)
    env.End(1, _Done())
    assert not env.RunFolder(hook).exists()
    assert env.RunFolder(sample).exists()
    env.clock.Advance(1)
    # Left running: swept failed, its out/ kept (not a hook run's).
    left = _StartSample(env)
    (env.RunFolder(left) / "out" / "b.txt").write_text("y", encoding="utf-8")
    env.clock.Advance(1)
    env.jobs = _NewProcess(env)
    env.jobs.Sweep()
    swept = env.jobs.Get(PLUGIN, left.run_id)
    assert swept.reason is EndReason.AUTONA_EXITED
    assert (env.RunFolder(left) / "out" / "b.txt").exists()
    assert env.RunFolder(sample).exists()


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
    assert "Dropped 3 artifact(s) (past 50, of an unknown kind, or " \
        "unreadable)" in english

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

def _NewProcess(env, hostAlive=lambda host: False, **kwargs):
    """PluginJobs as the next launch makes it: none of the runs on disk is
    its own, and (by default) the host that wrote them is gone."""
    return PluginJobs(
        runner=env.runner, registry=env.registry, inputs=env.inputs,
        settings=env.settings, secrets=FakeSecrets(),
        workDir=lambda: str(env.work), userDataDir=str(env.userData),
        hostVersion="2.6.0", locale=lambda: "en", apiUrl=lambda: None,
        onRunEnded=env.ended.append, onLaneFreed=env.freed.append,
        clock=env.clock, hostAlive=hostAlive, **kwargs)


def test_sweep_leaves_a_run_of_another_autona_still_running(env):
    # Two Autonas on one work folder (a test run beside the real app):
    # the other's run is alive under it, so it is not marked failed.
    left = env.StartPost()
    env.clock.Advance(1)
    asked = []
    env.jobs = _NewProcess(
        env, hostAlive=lambda host: asked.append(host) or True,
        host={"pid": 1, "started": 2})
    env.clock.Advance(60)
    env.jobs.Sweep()
    assert asked == [ThisHost()]
    assert env.jobs.Get(PLUGIN, left.run_id).state is JobState.RUNNING
    assert (env.RunFolder(left) / "inputs").exists()


def test_sweep_asks_the_real_process_by_default(env):
    # The host is this test's process (alive): left alone. One whose
    # creation time differs is a reused pid: swept.
    left = env.StartPost()
    env.clock.Advance(1)
    env.jobs = _NewProcess(env, hostAlive=HostAlive)
    env.jobs.Sweep()
    assert env.jobs.Get(PLUGIN, left.run_id).state is JobState.RUNNING
    path = env.RunFolder(left) / "job.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[HOST_KEY]["started"] = (raw[HOST_KEY]["started"] or 0) + 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    if raw[HOST_KEY]["started"] is not None and os.name == "nt":
        env.jobs.Sweep()
        assert env.jobs.Get(PLUGIN, left.run_id).state is JobState.FAILED
    assert HostAlive("not a host") is False
    assert HostAlive({"pid": True, "started": None}) is False


def test_sweep_leaves_a_run_started_since_it_was_made(env):
    # The sweep is a job: a run may start before it runs, and its job.json
    # then says running without being in the active set when the sweep
    # took it.
    env.jobs = _NewProcess(env)
    record = env.StartPost()
    folder = env.RunFolder(record)
    # As the sweep sees it when the start came after it took the active
    # runs: an empty set.
    env.jobs._SweepPlugin(PLUGIN, frozenset(), "2026-11-26T22:00:00+09:00")
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert job["state"] == "running"


def test_sweep_stops_between_plugins_when_asked(env):
    env.StartPost()
    env.clock.Advance(1)
    env.jobs = _NewProcess(env)
    swept = []
    env.jobs._SweepPlugin = lambda *args: swept.append(args)
    env.jobs.Sweep(stopping=lambda: True)
    assert swept == []
    env.jobs.Sweep(stopping=lambda: False)
    assert [args[0] for args in swept] == [PLUGIN]

def test_sweep_fails_the_runs_left_running(env):
    left = env.StartPost()
    folder = env.RunFolder(left)
    env.clock.Advance(1)
    # A new process: the run on disk is not one of its own, and the host
    # that wrote it is gone.
    env.jobs = _NewProcess(env)
    env.clock.Advance(59)
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


def test_shutdown_cancels_its_runs_only(env):
    env.StartPost()
    env.jobs.Start(PLUGIN, "split", MANUAL, {}, {}, {})
    light, heavy = env.runner.started[0][3], env.runner.started[1][3]
    light.outcome = RunOutcome(state=JobState.CANCELLED)
    _, log = _Logged(lambda: env.jobs.Shutdown(0.0))
    assert light.cancels == 1 and heavy.cancels == 1
    # The one still running after the grace is killed by its own handle;
    # the runner's Shutdown would stop runs that are not this module's.
    assert light.kills == 0 and heavy.kills == 1
    assert env.runner.shutdowns == []
    assert "1 run(s) still running" in log
    assert env.StartPost().reason is StartRefusal.UNAVAILABLE


def test_shutdown_waits_for_a_run_being_launched(env):
    threads = []

    def during(onEnd):
        # runner.Start is under way (no handle yet) when Shutdown comes.
        thread = threading.Thread(target=env.jobs.Shutdown, args=(5.0,))
        threads.append(thread)
        thread.start()
        deadline = time.monotonic() + 5
        while not env.jobs._closing and time.monotonic() < deadline:
            time.sleep(0.001)
    env.runner.during = during
    record = env.StartPost()
    handle = env.runner.started[0][3]
    handle.outcome = RunOutcome(state=JobState.CANCELLED)
    threads[0].join(5)
    assert not threads[0].is_alive()
    assert handle.cancels >= 1
    assert env.jobs.Get(PLUGIN, record.run_id).cancel_requested


# ── robustness ──────────────────────────────────────────────────────────

def test_a_lone_surrogate_still_writes_job_json(env):
    record = env.StartPost()
    env.runner.started[0][1](LogEvent(message="bad \ud800 text"))
    env.End(0, _Done(summary="\udc00"))
    job = (env.RunFolder(record) / "job.json").read_bytes()
    assert json.loads(job.decode("utf-8"))["state"] == "done"
    assert env.jobs.Get(PLUGIN, record.run_id).summary == "\udc00"
    assert len(env.ended) == 1 and env.freed == [Lane.LIGHT]


def test_a_broken_job_json_breaks_nothing(env):
    sibling = env.StartPost()
    env.End(0, _Done())
    env.clock.Advance(1)
    path = env.RunFolder(sibling) / "job.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(artifacts=5, warnings=True, stage=[1], progress="x",
               error=3, trigger={"kind": "manual", "target": 7})
    path.write_text(json.dumps(raw), encoding="utf-8")
    other = env.work / "plugins" / PLUGIN / "20261126-000000-0zzz"
    other.mkdir()
    (other / "job.json").write_text("[1, 2", encoding="utf-8")

    record = env.StartPost()
    env.End(1, _Done())
    assert len(env.ended) == 2 and env.freed == [Lane.LIGHT] * 2
    listed = {r.run_id: r for r in env.jobs.List(PLUGIN)}
    assert listed[sibling.run_id].artifacts == ()
    assert listed[sibling.run_id].warnings == ()
    assert record.run_id in listed
    env.jobs.Sweep()
    assert other.exists()


def test_a_failing_step_at_the_end_still_calls_back(env, monkeypatch):
    record = env.StartPost()

    def broken(folder, record, sample=False):
        raise OSError(13, "denied", "C:/Users/someone")
    monkeypatch.setattr(env.jobs, "_WriteRecord", broken)
    monkeypatch.setattr(env.jobs, "_Prune",
                        lambda pluginWork: 1 / 0)
    _, log = _Logged(lambda: env.End(0, _Done()))
    assert len(env.ended) == 1 and env.freed == [Lane.LIGHT]
    assert "someone" not in log and "ZeroDivisionError" in log
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert record.run_id == env.ended[0].run_id


def test_the_ended_run_is_read_from_memory_until_written(env, monkeypatch):
    record = env.StartPost()
    seen = []
    write = env.jobs._WriteRecord

    def spy(folder, written, sample=False):
        seen.append(env.jobs.Get(PLUGIN, record.run_id))
        seen.append([r.state for r in env.jobs.List(PLUGIN)])
        write(folder, written, sample)
    monkeypatch.setattr(env.jobs, "_WriteRecord", spy)
    env.End(0, _Done(summary="done"))
    assert seen[0].state is JobState.DONE and seen[0].summary == "done"
    assert seen[1] == [JobState.DONE]
    assert not env.jobs.Running(PLUGIN)


def test_host_warnings_are_capped_and_texts_cut(env):
    record = env.StartPost()
    onEvent = env.runner.started[0][1]
    onEvent(LogEvent(message="x" * 5000))
    onEvent(WarningEvent(message={"ja": "あ" * 3000, "en": "w"}))
    raw = {"type": "done",
           "artifacts": [{"path": f"v{n}", "kind": "video"}
                         for n in range(1000)]}
    env.End(0, _Done(summary="s" * 900, raw=raw))
    got = env.jobs.Get(PLUGIN, record.run_id)
    assert len(got.log) == 2000 and len(got.summary) == 500
    assert len(got.warnings[0]["ja"]) == 2000
    unknown = [w for w in got.warnings[1:] if "unknown kind)" in w["en"]]
    assert len(unknown) == 10
    assert got.warnings[-1]["en"].startswith("Dropped 990 artifact(s)")


def test_eta_counts_down_and_infinity_is_ignored(env):
    env.StartPost()
    onEvent = env.runner.started[0][1]
    onEvent(ProgressEvent(eta=20.0))
    env.clock.Advance(5)
    assert env.jobs.BusyFor(Lane.LIGHT).remaining_seconds == 15
    onEvent(ProgressEvent(eta=float("inf"), fraction=float("nan")))
    record = env.jobs.List(PLUGIN)[0]
    assert record.progress.eta is None and record.progress.fraction is None
    assert env.jobs.BusyFor(Lane.LIGHT).remaining_seconds == 55


def test_runs_in_one_second_sort_in_start_order(env):
    ids = []
    for n in range(3):
        ids.append(env.StartPost().run_id)
        env.End(n, _Done())
    assert ids == sorted(ids)
    assert [r.run_id for r in env.jobs.List(PLUGIN)] == list(reversed(ids))


def test_a_trigger_carries_its_kinds_keys_only(env):
    odd = Trigger(kind=TriggerKind.MANUAL, target=ActionTarget.RESULT_IMAGE,
                  event=HookEvent.MATCH_DECIDED, hook="x/y")
    record = env.jobs.Start(
        PLUGIN, "post", odd, {}, {},
        {"log": InputPick(key="log", kind=FieldType.MATCH_LOG,
                          choice="2026-11-26")})
    assert record.trigger == MANUAL
    request = json.loads((env.RunFolder(record) / "request.json")
                         .read_text(encoding="utf-8"))
    assert request["trigger"] == {"kind": "manual"}


def test_a_hook_run_left_running_is_swept_and_kept_once(env):
    first = _StartHook(env)
    env.clock.Advance(1)
    env.End(0, FAILED)
    second = _StartHook(env)
    env.clock.Advance(1)
    # A new process finds the second still running.
    env.jobs = _NewProcess(env)
    env.jobs.Sweep()
    assert not env.RunFolder(first).exists()
    swept = env.jobs.Get(PLUGIN, second.run_id)
    assert swept.state is JobState.FAILED
    assert swept.reason is EndReason.AUTONA_EXITED
    assert not (env.RunFolder(second) / "out").exists()


def test_a_junction_for_the_plugins_work_folder_is_not_followed(env):
    winapi = pytest.importorskip("_winapi")
    elsewhere = env.tmp / "elsewhere"
    victim = elsewhere / "20200101-000000-0abc"
    victim.mkdir(parents=True)
    (victim / "job.json").write_text(json.dumps({
        "run_id": victim.name, "plugin": PLUGIN, "job": "post",
        "trigger": {"kind": "manual"}, "lane": "light", "state": "done",
        "started_at": "2020-01-01T00:00:00+09:00"}), encoding="utf-8")
    (env.work / "plugins").mkdir(parents=True)
    winapi.CreateJunction(str(elsewhere), str(env.work / "plugins" / PLUGIN))
    assert env.StartPost().reason is StartRefusal.UNAVAILABLE
    env.jobs.Sweep()
    assert env.jobs.List(PLUGIN) == []
    assert (victim / "job.json").exists()


def test_making_it_touches_no_file(env):
    assert not env.work.exists() and not env.userData.exists()
    assert env.jobs.List(PLUGIN) == []
    assert not env.work.exists()
