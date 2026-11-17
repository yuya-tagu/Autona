"""The archive clipper's worker, installed from Autona-releases (ADR-006 A7):
core's ArchiveWorkerInstaller, ArchiveJobService's hold of the worker for
the swap, the v1 routes, and what keeps Autona's own update away from the
worker's folder.

The releases come from a local HTTP server of the test's (the GitHub API's
list and the release assets' downloads, as the installer reads them); the
zips are made here. The worker's ``version`` is a stand-in
(``ArchiveWorker.ProbeCommand`` faked): the unpacked ``vodsplit-worker.exe``
is not a program, its ``version.json`` says what it answers. Every test
runs in a folder of its own (the worker's folder is CWD-relative) on a
stepped TaskExecutor: the release check and the install run when the test
says (``run_next``).
"""
import hashlib
import io
import json
import os
import re
import stat
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import ArchiveJobService as archive
from src.core import ArchiveWorker as worker
from src.core import ArchiveWorkerInstaller as install
from src.core import WebAccess
from src.core.ArchiveJobService import ArchiveError, ArchiveJobService
from src.core.ArchiveWorkerInstaller import (
    ArchiveWorkerInstaller, InstallerError)
from src.core.SettingsManager import SettingsManager
from src.core.TaskExecutor import TaskExecutor
from tests.test_archive_jobs import (  # noqa: F401
    COMMAND, SOURCE, _ended, _mode, _until)
from tests.test_web_access import (  # noqa: F401
    LAN_PEER, _client, _shell_headers, server, settings, shellSecret)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WAIT = 30
BASE = "/api/v1/archive"
# Bigger than several download reads (the installer reads 64 KiB at a
# time), random so that the zip does not shrink it: a cancel lands between
# reads.
BIG = os.urandom(700 * 1024)


# ── the releases' server ─────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        net = self.server.net
        path = self.path.split("?")[0]
        net.hits.append(path)
        hook = net.hooks.get(path)
        if hook is not None:
            hook()
        if path == "/releases":
            body = json.dumps(net.releases).encode("utf-8")
        elif path in net.files:
            body = net.files[path]
        else:
            body = b"not found"
        status = net.status.get(path, 200 if path == "/releases"
                                or path in net.files else 404)
        asked = self.headers.get("Range")
        if asked is not None:
            net.ranges.append(asked)
        start = 0
        if asked is not None and path in net.ranged and status == 200:
            start = int(re.fullmatch(r"bytes=([0-9]+)-", asked).group(1))
            status = 206
        self.send_response(status)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-"
                             f"{len(body) - 1}/{len(body)}")
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        body = body[start:]
        gate = net.gates.get(path)
        breaks = net.breaks.get(path)
        try:
            if breaks:
                # This answer ends (the connection closed) after so many
                # bytes.
                self.wfile.write(body[:breaks.pop(0)])
                self.wfile.flush()
                self.close_connection = True
            elif path in net.trickled:
                # A slow line: 16 KiB every 50 ms.
                for at in range(0, len(body), 16 * 1024):
                    self.wfile.write(body[at:at + 16 * 1024])
                    self.wfile.flush()
                    time.sleep(0.05)
            elif gate is not None:
                # The first part, then the rest once the test lets it go.
                self.wfile.write(body[:400 * 1024])
                self.wfile.flush()
                gate.wait(WAIT)
                self.wfile.write(body[400 * 1024:])
            else:
                self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

    def log_message(self, *_args):
        pass


class Net:
    """The releases list (GitHub's JSON), the assets by path; by path, a
    status, a gate (the body paused after its first part), a hook (called
    as it is asked), breaks (the answers' lengths before the connection is
    closed, one per request), a slow line (trickled) and Range answered
    (ranged: 206, else a Range is ignored); the paths and the Ranges
    asked."""

    def __init__(self):
        self.releases = []
        self.files = {}
        self.status = {}
        self.gates = {}
        self.hooks = {}
        self.breaks = {}
        self.trickled = set()
        self.ranged = set()
        self.hits = []
        self.ranges = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.net = self
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       args=(0.05,), daemon=True)
        self.thread.start()

    def close(self):
        for gate in self.gates.values():
            gate.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, tag, name):
        return f"{self.base}/download/{tag}/{name}"

    def publish(self, version, archive=None, fmt=1, minAutona=None,
                manifest=None, tag=None, served=None, assetSize=None,
                zipUrl=None, **release):
        """A worker release: its manifest and zip (made for `version` when
        not given). `manifest` overrides its fields; `served` is what the
        zip's download answers instead of the zip; `assetSize` the size the
        releases list says."""
        tag = tag or f"worker-v{version}"
        archive = worker_zip(version) if archive is None else archive
        body = {"version": version, "sets_format": fmt,
                "zip": {"name": install.ZIP_NAME, "size": len(archive),
                        "sha256": hashlib.sha256(archive).hexdigest()}}
        if minAutona is not None:
            body["min_autona"] = minAutona
        body.update(manifest or {})
        manifestBytes = json.dumps(body).encode("utf-8")
        zipPath = f"/download/{tag}/{install.ZIP_NAME}"
        self.files[f"/download/{tag}/{install.MANIFEST_NAME}"] = manifestBytes
        self.files[zipPath] = archive if served is None else served
        self.releases.append(dict({
            "tag_name": tag, "draft": False, "prerelease": False,
            "published_at": "2026-11-17T00:00:00Z",
            "assets": [
                {"name": install.MANIFEST_NAME, "size": len(manifestBytes),
                 "browser_download_url": self.url(tag, install.MANIFEST_NAME)},
                {"name": install.ZIP_NAME,
                 "size": len(archive) if assetSize is None else assetSize,
                 "browser_download_url": zipUrl or self.url(
                     tag, install.ZIP_NAME)},
            ]}, **release))
        return zipPath


def worker_zip(version, entries=None, drop=(), big=False, said=None):
    """A worker's zip: vodsplit-worker/ with the exe, its version.json (what
    the fake probe answers: `said` adds to it -- "features", "hang",
    "check_failed") and a DLL; `entries` adds or replaces (None drops) by
    name, `drop` leaves names out."""
    files = {"vodsplit-worker/vodsplit-worker.exe": b"MZ not a program",
             "vodsplit-worker/version.json": json.dumps(
                 {"version": version, **(said or {})}).encode("utf-8"),
             "vodsplit-worker/_internal/python312.dll": b"dll"}
    if big:
        files["vodsplit-worker/_internal/big.bin"] = BIG
    for name, data in (entries or {}).items():
        files[name] = data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            if name in drop or data is None:
                continue
            if isinstance(data, zipfile.ZipInfo):
                archive.writestr(data, b"target")
            else:
                archive.writestr(name, data)
    return out.getvalue()


def _symlink(name):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


# ── the installer's world ────────────────────────────────────────────────

@pytest.fixture
def env(settings, monkeypatch, tmp_path):  # noqa: F811
    """The installer on a stepped executor in tmp_path/autona (Autona's
    folder), its releases on the test's server, the worker's version faked,
    ArchiveJobService of its own, a still clock."""
    previous = TaskExecutor.instance
    if previous is not None:
        assert previous.drain(WAIT)
    root = tmp_path / "autona"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv(worker.ENV_COMMAND, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    SettingsManager.settings["archive"] = {"work_dir": str(tmp_path / "work")}

    probes = []
    checks = []
    real = worker.ProbeCommand

    def answer(exe):
        return json.loads((exe.parent / "version.json").read_text(
            encoding="utf-8"))

    def probe(command, timeout=worker.PROBE_TIMEOUT_SECS,
              where=worker.WORKER_EXECUTABLE, stop=None):
        exe = Path(command[0])
        if exe.name != worker.WORKER_EXECUTABLE:
            return real(command, timeout, where, stop)
        probes.append((str(exe), timeout))
        try:
            said = answer(exe)
        except OSError:
            return worker.ProbeResult(False, None, None, "起動できません", None)
        if said.get("hang"):
            # A worker that does not answer: until `stop` says so.
            while not stop():
                time.sleep(0.02)
            raise worker.ProbeStopped()
        return worker.ProbeResult(True, said["version"], 1, None,
                                  tuple(command),
                                  tuple(said.get("features", ["titles"])))

    def check(command, timeout, stop=None):
        exe = Path(command[0])
        checks.append((str(exe), timeout))
        failed = answer(exe).get("check_failed")
        return f"切り抜きワーカーに同梱のものが動きません（{failed}）。" \
            if failed else None

    monkeypatch.setattr(worker, "ProbeCommand", probe)
    monkeypatch.setattr(worker, "CheckCommand", check)
    monkeypatch.setattr(install, "DOWNLOAD_PAUSE_SECS", 0.0)
    monkeypatch.setattr(install, "SWAP_RETRY_SECS", 0.01)
    clock = [1000.0]
    monkeypatch.setattr(install, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time, sleep=time.sleep))
    net = Net()
    jobs = ArchiveJobService()
    monkeypatch.setattr(ArchiveJobService, "instance", jobs)
    installer = ArchiveWorkerInstaller(
        jobs=jobs, autonaVersion=lambda: "2.0.1",
        releasesUrl=f"{net.base}/releases",
        downloadPrefix=f"{net.base}/download/")
    monkeypatch.setattr(ArchiveWorkerInstaller, "instance", installer)
    executor = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", executor)
    e = SimpleNamespace(root=root, net=net, jobs=jobs, installer=installer,
                        executor=executor, probes=probes, checks=checks,
                        clock=clock, folder=root / "vodsplit-worker")
    try:
        yield e
    finally:
        net.close()
        executor.shutdown(WAIT)
        jobs.Shutdown(5.0)


def _checked(env, refresh=False):
    """The snapshot once the release check the read started has run."""
    env.installer.Snapshot(refresh=refresh)
    assert env.executor.run_next(WAIT)
    return env.installer.Snapshot()


def _installed(env, version="0.3.0"):
    """Install the latest (shown as `version`) and run the job -> its
    state."""
    started = env.installer.Install(version)
    assert started["status"] == install.RUNNING
    assert env.executor.run_next(WAIT)
    return env.installer.Snapshot()["job"]


def _put_worker(env, version, extra=()):
    """A worker already in Autona's folder (its exe, version.json and
    `extra` files)."""
    (env.folder / "_internal").mkdir(parents=True)
    (env.folder / worker.WORKER_EXECUTABLE).write_bytes(b"MZ old")
    (env.folder / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8")
    for name in extra:
        (env.folder / name).write_bytes(b"old")


def _tree(folder):
    return sorted(str(p.relative_to(folder)).replace("\\", "/")
                  for p in folder.rglob("*") if p.is_file())


def _leftovers(env):
    return sorted(p.name for p in env.root.iterdir()
                  if p.name != "vodsplit-worker")


# ── the releases ─────────────────────────────────────────────────────────

def test_the_repository_is_the_updaters():
    from src.AutonaUpdater import GITHUB_REPO
    assert install.RELEASES_REPO == GITHUB_REPO
    assert install.RELEASES_API_URL.startswith(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases")
    assert install.DOWNLOAD_PREFIX == (
        f"https://github.com/{GITHUB_REPO}/releases/download/")


def test_nothing_is_fetched_until_the_page_reads_and_a_read_never_waits(env):
    assert env.net.hits == [] and env.executor.queued() == 0
    first = env.installer.Snapshot()
    assert first["check"]["status"] == install.CHECK_RUNNING
    assert first["latest"] is None and env.net.hits == []
    assert env.executor.queued() == 1


def test_the_newest_usable_worker_release_is_the_latest(env):
    net = env.net
    # Autona's own releases have no worker assets.
    net.releases.append({"tag_name": "v2.0.1", "draft": False,
                         "prerelease": False, "assets": [
                             {"name": "release-windows.zip", "size": 1,
                              "browser_download_url": net.url("v2.0.1", "x")}]})
    net.publish("0.2.0")
    net.publish("0.3.0")
    # A format this Autona does not read, an Autona newer than this one, a
    # draft, a prerelease, a manifest of another version, assets elsewhere,
    # a zip the list says is not the manifest's size, a tag that is not
    # x.y.z: skipped.
    net.publish("0.9.0", fmt=2)
    net.publish("0.8.0", minAutona="2.1.0")
    net.publish("0.7.0", draft=True)
    net.publish("0.6.0", prerelease=True)
    net.publish("0.5.0", manifest={"version": "0.5.1"})
    net.publish("0.4.0", zipUrl="https://example.invalid/vodsplit.zip")
    net.publish("0.3.9", assetSize=12)
    net.publish("0.3.8", tag="worker-v0.3.8-beta")
    # A zip in another release's folder.
    net.publish("0.3.7", zipUrl=net.url("worker-v0.3.0", install.ZIP_NAME))
    snapshot = _checked(env)
    assert snapshot["latest"]["version"] == "0.3.0"
    assert snapshot["latest"]["size"] == len(worker_zip("0.3.0"))
    assert snapshot["check"]["status"] == install.CHECK_DONE
    assert snapshot["check"]["error"] is None
    # Nothing installed known yet (the status probes it): no update shown.
    assert snapshot["installed"] is None
    assert snapshot["update_available"] is False
    # The drafts and the prereleases' manifests were not read; at most
    # MANIFEST_CANDIDATES were.
    manifests = [h for h in net.hits if h.endswith(install.MANIFEST_NAME)]
    assert len(manifests) == install.MANIFEST_CANDIDATES
    assert not any("0.7.0" in h or "0.6.0" in h for h in manifests)


def test_a_minimum_autona_reached_is_usable(env):
    env.net.publish("0.3.0", minAutona="2.0.1")
    assert _checked(env)["latest"]["version"] == "0.3.0"


def test_the_check_is_kept_ten_minutes_and_a_refresh_asked_at_most_every_3s(
        env):
    env.net.publish("0.3.0")
    _checked(env)
    hits = len(env.net.hits)
    env.installer.Snapshot()
    env.installer.Snapshot(refresh=True)
    assert env.executor.queued() == 0
    env.clock[0] += install.CHECK_REFRESH_FLOOR_SECS + 1
    env.installer.Snapshot()
    assert env.executor.queued() == 0
    # The page's "check again" is not held to CHECK_RETRY_SECS.
    assert install.CHECK_REFRESH_FLOOR_SECS < install.CHECK_RETRY_SECS
    assert env.installer.Snapshot(refresh=True)["check"]["status"] == \
        install.CHECK_RUNNING
    assert env.executor.queued() == 1
    assert env.executor.run_next(WAIT) and len(env.net.hits) > hits
    env.clock[0] += install.CHECK_MAX_AGE_SECS + 1
    env.installer.Snapshot()
    assert env.executor.queued() == 1


def test_a_failed_check_says_so_and_is_tried_again_after_a_while(env):
    env.net.status["/releases"] = 500
    snapshot = _checked(env)
    assert snapshot["check"]["status"] == install.CHECK_ERROR
    assert snapshot["check"]["error"] == install.FETCH_FAILED
    assert snapshot["check"]["message"] == install.FETCH_FAILED_TEXT
    assert snapshot["latest"] is None
    env.installer.Snapshot()
    assert env.executor.queued() == 0
    env.clock[0] += install.CHECK_RETRY_SECS + 1
    del env.net.status["/releases"]
    env.net.publish("0.3.0")
    assert _checked(env)["latest"]["version"] == "0.3.0"


def test_autona_ending_stops_the_check_between_its_requests(env):
    env.net.publish("0.3.0")
    env.net.hooks["/releases"] = env.executor.stopping.set
    snapshot = _checked(env)
    # Not a failure: nothing is said, and no manifest was read.
    assert snapshot["check"]["status"] == install.CHECK_IDLE
    assert snapshot["check"]["error"] is None
    assert env.net.hits == ["/releases"]


# ── the install ──────────────────────────────────────────────────────────

def test_the_install_downloads_checks_unpacks_and_moves_the_worker_in(env):
    env.net.publish("0.3.0")
    _checked(env)
    job = _installed(env)
    assert job["status"] == install.DONE and job["error"] is None
    assert job["version"] == "0.3.0" and "0.3.0" in job["message"]
    assert _tree(env.folder) == ["_internal/python312.dll", "version.json",
                                 "vodsplit-worker.exe"]
    assert _leftovers(env) == []
    # The unpacked worker was asked its version where it was unpacked,
    # with the install's time; a worker with no self-check is not asked one.
    assert env.probes == [(str(env.root / "vodsplit-worker.new"
                               / worker.WORKER_EXECUTABLE),
                           install.INSTALL_PROBE_TIMEOUT_SECS)]
    assert env.checks == []
    # The status probes the worker now in the folder.
    status = env.jobs.Status()
    assert (status["available"], status["version"]) == (True, "0.3.0")
    snapshot = env.installer.Snapshot()
    assert snapshot["installed"] == {"available": True, "version": "0.3.0"}
    assert snapshot["update_available"] is False


def test_an_update_replaces_the_old_worker_whole(env):
    _put_worker(env, "0.2.0", extra=["_internal/gone.dll"])
    assert env.jobs.Status()["version"] == "0.2.0"
    env.net.publish("0.3.0")
    snapshot = _checked(env)
    assert snapshot["installed"]["version"] == "0.2.0"
    assert snapshot["update_available"] is True
    job = _installed(env)
    assert job["status"] == install.DONE
    assert "_internal/gone.dll" not in _tree(env.folder)
    assert json.loads((env.folder / "version.json").read_text())[
        "version"] == "0.3.0"
    assert _leftovers(env) == []
    # The cached probe of the old worker was dropped by the swap.
    assert env.jobs.CachedProbe() is None
    assert env.jobs.Status()["version"] == "0.3.0"


def test_the_progress_is_the_downloads(env):
    env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    _checked(env)
    gate = threading.Event()
    path = f"/download/worker-v0.3.0/{install.ZIP_NAME}"
    env.net.gates[path] = gate
    env.installer.Install("0.3.0")
    runner = threading.Thread(target=env.executor.run_next, args=(WAIT,))
    runner.start()
    job = _until(lambda: (lambda j: j if j["done"] else None)(
        env.installer.Snapshot()["job"]), "the download's progress")
    assert job["stage"] == install.DOWNLOAD
    assert 0 < job["done"] < job["total"] == len(env.net.files[path])
    gate.set()
    runner.join(WAIT)
    assert env.installer.Snapshot()["job"]["status"] == install.DONE


def _assert_kept(env):
    """The worker that was there is as it was, and nothing is left."""
    assert _tree(env.folder) == ["_internal/kept.dll", "version.json",
                                 "vodsplit-worker.exe"]
    assert json.loads((env.folder / "version.json").read_text())[
        "version"] == "0.2.0"
    assert _leftovers(env) == []


@pytest.mark.parametrize("case, publish, code", [
    ("hash", dict(manifest={"zip": {"name": install.ZIP_NAME, "size": None,
                                    "sha256": "0" * 64}}),
     install.HASH_MISMATCH),
    ("longer", dict(served=worker_zip("0.3.0") + b"tail"),
     install.SIZE_MISMATCH),
    ("shorter", dict(served=worker_zip("0.3.0")[:-10]),
     install.SIZE_MISMATCH),
    ("http", dict(status=404), install.DOWNLOAD_FAILED),
    ("dotdot", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker/../evil.txt": b"x"})), install.UNSAFE_ARCHIVE),
    ("nested-dotdot", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker/_internal/../../../evil.txt": b"x"})),
     install.UNSAFE_ARCHIVE),
    ("outside", dict(archive=worker_zip("0.3.0", {
        "other/evil.txt": b"x"})), install.UNSAFE_ARCHIVE),
    ("absolute", dict(archive=worker_zip("0.3.0", {
        "/vodsplit-worker/evil.txt": b"x"})), install.UNSAFE_ARCHIVE),
    ("drive", dict(archive=worker_zip("0.3.0", {
        "C:/vodsplit-worker/evil.txt": b"x"})), install.UNSAFE_ARCHIVE),
    ("backslash", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker\\..\\evil.txt": b"x"})), install.UNSAFE_ARCHIVE),
    ("link", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker/link": _symlink("vodsplit-worker/link")})),
     install.UNSAFE_ARCHIVE),
    ("twice", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker/VERSION.JSON": b"{}"})), install.UNSAFE_ARCHIVE),
    ("no-exe", dict(archive=worker_zip(
        "0.3.0", drop={"vodsplit-worker/vodsplit-worker.exe"})),
     install.BAD_ARCHIVE),
    ("not-a-zip", dict(archive=b"PK not a zip at all" * 10),
     install.BAD_ARCHIVE),
    ("probe", dict(archive=worker_zip("0.3.0", {
        "vodsplit-worker/version.json": b'{"version": "0.2.9"}'})),
     install.PROBE_FAILED),
])
def test_a_failure_before_the_swap_keeps_the_worker_and_leaves_nothing(
        env, case, publish, code):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    publish = dict(publish)
    status = publish.pop("status", None)
    manifest = publish.get("manifest")
    if manifest is not None:
        # The size the zip has (only the hash is wrong).
        manifest["zip"]["size"] = len(worker_zip("0.3.0"))
    path = env.net.publish("0.3.0", **publish)
    if status is not None:
        env.net.status[path] = status
    assert _checked(env)["latest"]["version"] == "0.3.0"
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR, code), job
    assert job["message"]
    assert str(env.root) not in job["message"]
    assert not (env.root.parent / "evil.txt").exists()
    assert not (env.root / "evil.txt").exists()
    _assert_kept(env)


def test_a_corrupt_member_fails_while_unpacking_and_is_cleaned(env):
    # The CRC of one member broken: zipfile notices while reading it, with
    # files already unpacked into .new.
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    data = bytearray(worker_zip("0.3.0", big=True))
    at = data.find(BIG[:64])
    data[at + 1000] ^= 0xFF
    env.net.publish("0.3.0", archive=bytes(data))
    _checked(env)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR,
                                             install.BAD_ARCHIVE), job
    _assert_kept(env)


def test_cancelling_stops_the_download_and_keeps_the_worker(env):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    path = env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    _checked(env)
    gate = threading.Event()
    env.net.gates[path] = gate
    revision = env.installer.Install("0.3.0")["revision"]
    runner = threading.Thread(target=env.executor.run_next, args=(WAIT,))
    runner.start()
    _until(lambda: env.installer.Snapshot()["job"]["done"], "the download")
    with pytest.raises(InstallerError) as refused:
        env.installer.Cancel(revision + 1)
    assert refused.value.code == install.NOT_RUNNING
    assert env.installer.Cancel(revision)["status"] == install.RUNNING
    gate.set()
    runner.join(WAIT)
    job = env.installer.Snapshot()["job"]
    assert (job["status"], job["error"]) == (install.CANCELLED, None)
    _assert_kept(env)
    with pytest.raises(InstallerError) as refused:
        env.installer.Cancel(revision)
    assert refused.value.code == install.NOT_RUNNING


def test_a_swap_that_cannot_move_the_new_worker_in_puts_the_old_back(
        env, monkeypatch):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0")
    _checked(env)
    rename = install._Rename

    def refuse(source, target):
        if source.endswith(install.NEW_SUFFIX):
            raise PermissionError(13, "Access is denied", source)
        rename(source, target)

    monkeypatch.setattr(install, "_Rename", refuse)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR,
                                             install.SWAP_FAILED), job
    assert "Access is denied" in job["message"]
    assert str(env.root) not in job["message"]
    _assert_kept(env)
    # The hold is released: the worker can run again.
    assert env.jobs.SwapBusy() is None
    env.jobs.ReserveSwap()
    env.jobs.ReleaseSwap()


def test_the_swap_does_not_wait_for_a_job_and_refuses_instead(
        env, monkeypatch):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0")
    _checked(env)
    asked = []

    def busy():
        asked.append(1)
        raise ArchiveError(archive.BUSY, archive.SWAP_BUSY_JOB)

    monkeypatch.setattr(env.jobs, "ReserveSwap", busy)
    job = _installed(env)
    assert (job["status"], job["error"], job["message"]) == (
        install.ERROR, install.BUSY, archive.SWAP_BUSY_JOB)
    assert len(asked) == 1
    _assert_kept(env)


def test_the_swap_waits_a_little_for_a_probe_or_a_preview(env, monkeypatch):
    env.net.publish("0.3.0")
    _checked(env)
    real = env.jobs.ReserveSwap
    asked = []

    def busy():
        asked.append(1)
        if len(asked) < 3:
            raise ArchiveError(archive.BUSY, archive.SWAP_BUSY_PROBE,
                               transient=True)
        real()

    monkeypatch.setattr(env.jobs, "ReserveSwap", busy)
    assert _installed(env)["status"] == install.DONE
    assert len(asked) == 3


def test_a_probe_or_a_preview_that_does_not_end_refuses_the_swap(
        env, monkeypatch):
    monkeypatch.setattr(install, "SWAP_WAIT_SECS", 0.05)
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0")
    _checked(env)

    def busy():
        raise ArchiveError(archive.BUSY, archive.SWAP_BUSY_TITLES,
                           transient=True)

    monkeypatch.setattr(env.jobs, "ReserveSwap", busy)
    job = _installed(env)
    assert (job["status"], job["error"], job["message"]) == (
        install.ERROR, install.BUSY, archive.SWAP_BUSY_TITLES)
    _assert_kept(env)


def test_a_swap_refused_by_autona_ending_is_stopped(env, monkeypatch):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0")
    _checked(env)

    def stopping():
        raise ArchiveError(archive.STOPPING, "Autona の終了中です。")

    monkeypatch.setattr(env.jobs, "ReserveSwap", stopping)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR, install.STOPPED)
    _assert_kept(env)


def test_a_swap_finding_the_old_worker_aside_puts_it_back_first(
        env, monkeypatch):
    # .old with no worker in place (a crash's, which the cleaning could not
    # put back): the swap puts it back before anything, so a new worker
    # that cannot be moved in leaves the old one in place.
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0")
    _checked(env)
    probeNew = env.installer._ProbeNew

    def thenAside(job):
        probeNew(job)
        os.rename(env.folder, env.root / "vodsplit-worker.old")

    rename = install._Rename

    def refuse(source, target):
        if source.endswith(install.NEW_SUFFIX):
            raise PermissionError(13, "Access is denied", source)
        rename(source, target)

    monkeypatch.setattr(env.installer, "_ProbeNew", thenAside)
    monkeypatch.setattr(install, "_Rename", refuse)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR,
                                             install.SWAP_FAILED), job
    _assert_kept(env)


def test_an_install_is_refused_until_its_release_is_the_latest_known(env):
    with pytest.raises(InstallerError) as refused:
        env.installer.Install(None)
    assert refused.value.code == install.INVALID
    with pytest.raises(InstallerError) as refused:
        env.installer.Install("0.3.0")
    assert refused.value.code == install.NO_RELEASE
    env.net.publish("0.3.0")
    _checked(env)
    with pytest.raises(InstallerError) as refused:
        env.installer.Install("0.2.0")
    assert refused.value.code == install.CHANGED
    env.installer.Install("0.3.0")
    with pytest.raises(InstallerError) as refused:
        env.installer.Install("0.3.0")
    assert refused.value.code == install.BUSY
    assert env.executor.queued() == 1


def test_an_install_of_the_version_in_the_folder_is_refused(env):
    _put_worker(env, "0.3.0")
    assert env.jobs.Status()["version"] == "0.3.0"
    env.net.publish("0.3.0")
    snapshot = _checked(env)
    assert snapshot["update_available"] is False
    with pytest.raises(InstallerError) as refused:
        env.installer.Install("0.3.0")
    assert refused.value.code == install.INSTALLED
    assert "0.3.0" in refused.value.message
    assert env.executor.queued() == 0


# ── the self-check ───────────────────────────────────────────────────────

def test_a_worker_with_a_self_check_is_checked_before_the_swap(env):
    env.net.publish("0.3.0", archive=worker_zip(
        "0.3.0", said={"features": ["titles", worker.CHECK_FEATURE]}))
    _checked(env)
    assert _installed(env)["status"] == install.DONE
    assert env.checks == [(str(env.root / "vodsplit-worker.new"
                               / worker.WORKER_EXECUTABLE),
                           install.INSTALL_CHECK_TIMEOUT_SECS)]


def test_a_worker_whose_self_check_fails_is_not_moved_in(env):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0", archive=worker_zip(
        "0.3.0", said={"features": ["titles", worker.CHECK_FEATURE],
                       "check_failed": "ffmpeg"}))
    _checked(env)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR,
                                             install.PROBE_FAILED), job
    assert "ffmpeg" in job["message"]
    _assert_kept(env)


# ── Autona's end ─────────────────────────────────────────────────────────

def _ended_by_shutdown(env, runner):
    """Shut the executor down -> how many seconds it took; the install
    ended as stopped."""
    began = time.monotonic()
    assert env.executor.shutdown(WAIT)
    took = time.monotonic() - began
    runner.join(WAIT)
    job = env.installer.Snapshot()["job"]
    assert (job["status"], job["error"]) == (install.ERROR, install.STOPPED)
    return took


def _install_in_background(env):
    env.installer.Install("0.3.0")
    runner = threading.Thread(target=env.executor.run_next, args=(WAIT,))
    runner.start()
    return runner


def test_autona_ending_stops_a_download_on_a_slow_line_at_once(env):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    path = env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    env.net.trickled.add(path)
    _checked(env)
    runner = _install_in_background(env)
    _until(lambda: env.installer.Snapshot()["job"]["done"], "the download")
    assert _ended_by_shutdown(env, runner) < 3
    _assert_kept(env)


def test_autona_ending_stops_a_stalled_download(env, monkeypatch):
    # The server stops sending: the read's timeout lets the stop through.
    monkeypatch.setattr(install, "DOWNLOAD_TIMEOUT", (1, 1))
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    path = env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    env.net.gates[path] = threading.Event()
    _checked(env)
    runner = _install_in_background(env)
    _until(lambda: env.installer.Snapshot()["job"]["done"], "the download")
    assert _ended_by_shutdown(env, runner) < 4
    _assert_kept(env)


def test_autona_ending_kills_the_new_workers_probe(env):
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    env.net.publish("0.3.0", archive=worker_zip("0.3.0",
                                                said={"hang": True}))
    _checked(env)
    runner = _install_in_background(env)
    _until(lambda: env.installer.Snapshot()["job"]["stage"] == install.PROBE
           and env.probes, "the probe")
    assert _ended_by_shutdown(env, runner) < 3
    _assert_kept(env)


# ── a download that breaks ───────────────────────────────────────────────

def test_a_broken_download_is_resumed_where_it_stopped(env):
    path = env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    env.net.breaks[path] = [300 * 1024]
    env.net.ranged.add(path)
    _checked(env)
    assert _installed(env)["status"] == install.DONE
    assert env.net.hits.count(path) == 2
    assert len(env.net.ranges) == 1
    start = int(re.fullmatch(r"bytes=([0-9]+)-", env.net.ranges[0]).group(1))
    assert 0 < start <= 300 * 1024
    assert _leftovers(env) == []


def test_a_server_that_ignores_the_range_starts_the_download_over(env):
    path = env.net.publish("0.3.0", archive=worker_zip("0.3.0", big=True))
    env.net.breaks[path] = [300 * 1024, 200 * 1024]
    _checked(env)
    assert _installed(env)["status"] == install.DONE
    assert env.net.hits.count(path) == 3
    assert len(env.net.ranges) == 2


def test_a_download_that_gets_nowhere_fails_after_its_tries(env,
                                                            monkeypatch):
    monkeypatch.setattr(install, "DOWNLOAD_RETRIES", 3)
    _put_worker(env, "0.2.0", extra=["_internal/kept.dll"])
    path = env.net.publish("0.3.0")
    env.net.breaks[path] = [0] * 10
    _checked(env)
    job = _installed(env)
    assert (job["status"], job["error"]) == (install.ERROR,
                                             install.DOWNLOAD_FAILED), job
    assert env.net.hits.count(path) == 3
    _assert_kept(env)


def test_an_install_is_refused_while_a_job_runs_the_worker(env, monkeypatch):
    # The fake worker (AUTONA_VODSPLIT_CMD) runs a job; the installer's
    # folder is not the one used, and the page says so.
    monkeypatch.setattr(worker, "_BELOW_NORMAL_PRIORITY_CLASS", 0)
    monkeypatch.setenv(worker.ENV_COMMAND, COMMAND)
    _mode(monkeypatch, "slow")
    env.net.publish("0.3.0")
    _checked(env)
    assert env.installer.Snapshot()["env_command"] is True
    jobId = env.jobs.Create(SOURCE)["id"]
    with pytest.raises(InstallerError) as refused:
        env.installer.Install("0.3.0")
    assert (refused.value.code, refused.value.message) == (
        install.BUSY, archive.SWAP_BUSY_JOB)
    with pytest.raises(ArchiveError) as held:
        env.jobs.ReserveSwap()
    assert held.value.code == archive.BUSY
    env.jobs.Cancel(jobId)
    _ended(env.jobs, jobId)
    assert env.jobs.SwapBusy() is None
    assert env.installer.Install("0.3.0")["status"] == install.RUNNING


# ── what a crash leaves ──────────────────────────────────────────────────

def test_what_an_unfinished_install_left_is_removed(env):
    _put_worker(env, "0.3.0")
    (env.root / "vodsplit-worker.part").write_bytes(b"partial")
    for name in ("vodsplit-worker.new", "vodsplit-worker.old"):
        (env.root / name / "_internal").mkdir(parents=True)
        (env.root / name / "_internal" / "x.dll").write_bytes(b"x")
        os.chmod(env.root / name / "_internal" / "x.dll", stat.S_IREAD)
    env.installer.CleanLeftovers()
    assert _leftovers(env) == []
    assert "vodsplit-worker.exe" in _tree(env.folder)


def test_an_old_worker_moved_aside_with_no_worker_in_place_is_put_back(env):
    _put_worker(env, "0.2.0")
    os.rename(env.folder, env.root / "vodsplit-worker.old")
    (env.root / "vodsplit-worker.new").mkdir()
    env.installer.CleanLeftovers()
    assert _leftovers(env) == []
    assert json.loads((env.folder / "version.json").read_text())[
        "version"] == "0.2.0"


def test_cleaning_goes_on_past_a_step_that_fails(env, monkeypatch):
    _put_worker(env, "0.2.0")
    os.rename(env.folder, env.root / "vodsplit-worker.old")
    part = env.root / "vodsplit-worker.part"
    part.write_bytes(b"partial")
    (env.root / "vodsplit-worker.new").mkdir()
    remove = os.remove

    def refuse(path):
        if str(path).endswith(install.PART_SUFFIX):
            raise PermissionError(13, "Access is denied", path)
        remove(path)

    monkeypatch.setattr(install.os, "remove", refuse)
    env.installer.CleanLeftovers()
    assert _leftovers(env) == ["vodsplit-worker.part"]
    assert json.loads((env.folder / "version.json").read_text())[
        "version"] == "0.2.0"


def test_the_install_cleans_first(env):
    _put_worker(env, "0.2.0")
    (env.root / "vodsplit-worker.part").write_bytes(b"partial")
    (env.root / "vodsplit-worker.old").mkdir()
    env.net.publish("0.3.0")
    _checked(env)
    assert _installed(env)["status"] == install.DONE
    assert _leftovers(env) == []


def test_bootstrap_makes_and_cleans_and_reads_the_autona_version():
    import ast
    source = (PROJECT_ROOT / "src" / "bootstrap.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    calls = [ast.unparse(node) for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and ast.unparse(node.func).startswith("ArchiveWorkerInstaller")]
    assert calls == ["ArchiveWorkerInstaller(jobs=ArchiveJobService.instance, "
                     "autonaVersion=_program_version)",
                     "ArchiveWorkerInstaller.instance.CleanLeftovers()"]
    from src.bootstrap import _program_version
    with open(PROJECT_ROOT / "assets" / "versions.json",
              encoding="utf-8") as f:
        assert _program_version() == json.load(f)["program"]


# ── ArchiveJobService's hold of the worker ──────────────────────────────

def test_the_hold_refuses_a_preview_or_a_probe_in_progress(env):
    ticket = env.jobs._ClaimTitles(env.jobs._swaps, False)
    with pytest.raises(ArchiveError) as refused:
        env.jobs.ReserveSwap()
    assert (refused.value.code, refused.value.message,
            refused.value.transient) == (
        archive.BUSY, archive.SWAP_BUSY_TITLES, True)
    with env.jobs._titlesLock:
        assert ticket == env.jobs._titlesTicket
        env.jobs._titlesBusy = False

    held, release = threading.Event(), threading.Event()

    def probing():
        with env.jobs._probeLock:
            held.set()
            release.wait(WAIT)

    thread = threading.Thread(target=probing)
    thread.start()
    held.wait(WAIT)
    try:
        with pytest.raises(ArchiveError) as refused:
            env.jobs.ReserveSwap()
        assert refused.value.message == archive.SWAP_BUSY_PROBE
        assert refused.value.transient is True
    finally:
        release.set()
        thread.join(WAIT)
    env.jobs.ReserveSwap()
    env.jobs.ReleaseSwap()


def _probing(env, monkeypatch, swaps):
    """_Available faked: the first `swaps` probes are followed by a swap
    (before the start takes its lock); each probe names its own version.
    _Start records the probe it got and starts nothing."""
    probes, started = [], []

    def available():
        probes.append(1)
        if len(probes) <= swaps:
            env.jobs.ReserveSwap()
            env.jobs.ReleaseSwap()
        return worker.ProbeResult(True, f"0.{len(probes)}.0", 1, None,
                                  ("worker",), ())

    def start(_run, probe, _arguments):
        started.append(probe.version)
        raise RuntimeError("not started in this test")

    monkeypatch.setattr(env.jobs, "_Available", available)
    monkeypatch.setattr(env.jobs, "_Start", start)
    return probes, started


def test_a_start_probes_again_when_the_worker_was_replaced_since(
        env, monkeypatch):
    probes, started = _probing(env, monkeypatch, swaps=1)
    with pytest.raises(RuntimeError):
        env.jobs.Create(SOURCE)
    assert (len(probes), started) == (2, ["0.2.0"])


def test_a_start_replaced_twice_is_refused(env, monkeypatch):
    probes, started = _probing(env, monkeypatch, swaps=2)
    with pytest.raises(ArchiveError) as refused:
        env.jobs.Create(SOURCE)
    assert (refused.value.code, refused.value.message) == (
        archive.BUSY, archive.SWAPPING)
    assert (len(probes), started) == (2, [])
    # A preview's claim says the same.
    swaps = env.jobs._swaps
    env.jobs.ReserveSwap()
    env.jobs.ReleaseSwap()
    assert env.jobs._ClaimTitles(swaps, False) is None
    with pytest.raises(ArchiveError):
        env.jobs._ClaimTitles(swaps, True)


def test_nothing_of_the_worker_starts_while_it_is_held(env):
    _put_worker(env, "0.2.0")
    env.jobs.ReserveSwap()
    try:
        assert env.jobs.SwapBusy() == archive.SWAPPING
        probes = len(env.probes)
        status = env.jobs.Status(refresh=True)
        assert (status["available"], status["reason"]) == (
            False, archive.SWAPPING)
        assert len(env.probes) == probes
        for start in (lambda: env.jobs.Create(SOURCE),
                      lambda: env.jobs._ClaimTitles(env.jobs._swaps, False),
                      env.jobs.ReserveSwap):
            with pytest.raises(ArchiveError) as refused:
                start()
            assert refused.value.code == archive.BUSY
    finally:
        env.jobs.ReleaseSwap()
    status = env.jobs.Status()
    assert (status["available"], status["version"]) == (True, "0.2.0")


# ── the v1 routes ────────────────────────────────────────────────────────

def _data(response, status=200):
    body = response.get_json()
    assert response.status_code == status, body
    return body["data"]


def test_the_routes_read_install_and_cancel(server, env, shellSecret):  # noqa: F811
    page = _client(server)
    headers = _shell_headers(shellSecret)
    data = _data(page.get(f"{BASE}/worker"))
    assert set(data) == {"installed", "env_command", "latest", "check",
                         "update_available", "job"}
    assert data["check"]["status"] == install.CHECK_RUNNING
    assert data["job"]["status"] == install.IDLE
    response = page.post(f"{BASE}/worker:install", json={"version": "0.3.0"},
                         headers=headers)
    body = response.get_json()
    assert response.status_code == 409 and body["error"]["code"] == "NOT_READY"
    assert body["error"]["reason"] == install.NO_RELEASE
    env.net.publish("0.3.0")
    assert env.executor.run_next(WAIT)
    response = page.post(f"{BASE}/worker:install", json={}, headers=headers)
    assert response.status_code == 400
    assert response.get_json()["error"]["reason"] == install.INVALID
    job = _data(page.post(f"{BASE}/worker:install",
                          json={"version": "0.3.0"}, headers=headers), 202)["job"]
    assert job["status"] == install.RUNNING
    response = page.post(f"{BASE}/worker:install", json={"version": "0.3.0"},
                         headers=headers)
    assert response.status_code == 409
    assert response.get_json()["error"]["reason"] == install.BUSY
    response = page.post(f"{BASE}/worker:cancel", json={"revision": True},
                         headers=headers)
    assert response.status_code == 400
    response = page.post(f"{BASE}/worker:cancel",
                         json={"revision": job["revision"] + 1},
                         headers=headers)
    assert response.status_code == 409
    assert response.get_json()["error"]["reason"] == install.NOT_RUNNING
    cancelled = _data(page.post(f"{BASE}/worker:cancel",
                                json={"revision": job["revision"]},
                                headers=headers))["job"]
    assert cancelled["revision"] == job["revision"]
    assert env.executor.run_next(WAIT)
    assert _data(page.get(f"{BASE}/worker"))["job"]["status"] == \
        install.CANCELLED
    assert not env.folder.exists()


def test_the_read_is_this_pcs_and_the_writes_the_shells(server, env,  # noqa: F811
                                                        shellSecret):
    assert {name: WebAccess.ROUTE_ACCESS[f"api_v1.{name}"] for name in (
        "get_archive_worker", "archive_worker_install",
        "archive_worker_cancel")} == {
        "get_archive_worker": WebAccess.THIS_PC,
        "archive_worker_install": WebAccess.SHELL,
        "archive_worker_cancel": WebAccess.SHELL}
    # Another computer, even with the token and the shell's secret.
    lan = _client(server, LAN_PEER)
    auth = dict(_shell_headers(shellSecret),
                **{WebAccess.TOKEN_HEADER: WebAccess.Token()})
    response = lan.get(f"{BASE}/worker", headers=auth)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    for path, body in ((f"{BASE}/worker:install", {"version": "0.3.0"}),
                       (f"{BASE}/worker:cancel", {"revision": 1})):
        response = lan.post(path, json=body, headers=auth)
        assert response.status_code == 403
        assert response.get_json() == WebAccess.ErrorBody(
            WebAccess.SHELL_ONLY)
        # This PC without the shell's secret (a browser, a layout page).
        response = _client(server).post(
            path, json=body, headers={"Sec-Fetch-Site": "same-origin"})
        assert response.status_code == 403
    assert env.executor.queued() == 0 and env.net.hits == []


# ── Autona's own update leaves the worker alone ─────────────────────────

def test_the_update_helper_never_removes_or_purges_the_install_root():
    helper = (PROJECT_ROOT / "assets" / "apply_update.ps1").read_bytes() \
        .decode("utf-8-sig")
    root = re.search(r'Invoke-RobocopyPass\s+"root"\s+\(\'([^\']*)\'',
                     helper).group(1)
    assert "/E" in root
    for flag in ("/MIR", "/PURGE", "/MOV", "/MOVE"):
        assert flag not in root.upper()
    # The purging pass mirrors _internal onto Autona's _internal only (the
    # worker's own _internal is under vodsplit-worker/).
    internal = re.search(
        r'Invoke-RobocopyPass\s+"_internal"[^\n]*\n[^\n]*', helper).group(0)
    assert "$StagingInternal, $InstallInternal" in internal
    assert '$InstallInternal = (Join-Path $InstallDir "_internal")' in helper
    # What it removes: its own lock, zip, marker, staging and folder.
    targets = set(re.findall(r"Remove-Item\s+-LiteralPath\s+(\S+)", helper))
    assert targets <= {"$script:LockPath", "$LockPath", "$Zip",
                       "$MarkerPath", "$Staging", "'$dir'"}, targets
    assert "$dir = $PSScriptRoot" in helper
    assert "vodsplit" not in helper


def test_the_startup_cleanup_of_an_update_leaves_the_worker(
        settings, monkeypatch, tmp_path):  # noqa: F811
    from src import AutonaUpdater as updater
    monkeypatch.chdir(tmp_path)
    files = ["vodsplit-worker/vodsplit-worker.exe",
             "vodsplit-worker/_internal/python312.dll",
             "vodsplit-worker/_internal/legacy" + updater._LEGACY_RENAME_SUFFIX,
             "vodsplit-worker/deno.exe"]
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / ("root" + updater._LEGACY_RENAME_SUFFIX)).write_bytes(b"x")
    workdir = tmp_path / "temp_update"
    workdir.mkdir()
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_local_version", lambda: "9.9.9")
    monkeypatch.setattr(updater, "_UpdateInFlight", lambda: False)
    monkeypatch.setattr(updater, "_DownloadInFlight", lambda: False)
    monkeypatch.setattr(updater, "_update_workdir", lambda: workdir)
    updater.CleanupUpdateLeftovers()
    # It ran (its own leftovers are gone) and the worker is whole.
    assert not workdir.exists()
    assert not (tmp_path / ("root" + updater._LEGACY_RENAME_SUFFIX)).exists()
    assert all((tmp_path / name).exists() for name in files)


@pytest.mark.parametrize("tag, newer", [
    ("v2.1.0", True), ("v2.0.1", False), ("v1.9.0", False),
    # The worker's releases are not Autona's, whatever their number.
    ("worker-v9.0.0", False), ("worker-v0.3.0", False), ("", False),
    ("latest", False),
])
def test_the_updater_offers_only_autonas_own_releases(tag, newer):
    from src import AutonaUpdater as updater
    assert updater._is_newer(tag, "2.0.1") is newer


def test_a_worker_release_marked_latest_is_no_update(monkeypatch):
    # GitHub's releases/latest answering a worker's release (marked latest
    # by mistake): the check says there is no update.
    from src import AutonaUpdater as updater

    class Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "worker-v0.3.0", "html_url": "x", "body": "",
                    "assets": [{"name": install.ZIP_NAME,
                                "browser_download_url": "x"}]}

    monkeypatch.setattr(updater.requests, "get", lambda *_a, **_k: Answer())
    monkeypatch.setattr(updater, "_local_version", lambda: "2.0.1")
    thread = updater._CheckThread()
    said = []
    thread.newVersion.connect(lambda *args: said.append(("new", args)))
    thread.noUpdate.connect(lambda: said.append(("none",)))
    thread.error.connect(lambda text: said.append(("error", text)))
    thread.run()
    assert said == [("none",)]


def test_autonas_release_is_published_as_the_latest():
    script = (PROJECT_ROOT / "scripts" / "publish_release.ps1").read_text(
        encoding="utf-8")
    create = [line for line in script.splitlines()
              if line.strip().startswith("gh release create")]
    assert len(create) == 1 and create[0].rstrip().endswith("--latest")
