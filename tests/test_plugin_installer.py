"""PluginInstaller (ADR-009 D7, appendix C; lane L4): the zip's checks
(``<id>/`` stripped, the descriptor's ``tag``), the source decision's whole
table (manual to manual, the SHA-256 match, ``taguyan.``), the consent
digest, the blocklist's three kinds, the install's steps with describe and
the swap's put-back, the removal and the launch's sweep. The registry,
describe and GitHub are fakes; every zip is made here."""

import hashlib
import json
import os
import stat
import zipfile

import pytest

from src.core import PluginInstaller as installer_module
from src.core.PluginInstaller import (
    BAD_ZIP, BLOCKED, DESCRIBE_FAILED, DESCRIPTOR_INVALID, ENTRY_MISSING,
    ENTRY_OUTSIDE, FOLDER_NOT_ID, ID_MISMATCH, IN_PROGRESS, MANIFEST_INVALID,
    MANIFEST_MISSING, MANIFEST_UNREADABLE, NETWORK_FAILED, NOT_FOUND,
    NOT_ONE_FOLDER, NOT_PENDING, RUNNING, SHA256_MISMATCH, SIZE_MISMATCH,
    SWAP_FAILED, VERSION_MISMATCH, ZIP_TOO_LARGE, BlockedBy, CheckDescriptor,
    ConsentDigest, DecideSource, InstallError, ParseBlocklist,
    PluginInstaller, VersionMatches)
from src.core.PluginTypes import (BlockEntry, DescribeAnswer,
                                  DescribeFailure, DescribeResult, Field,
                                  Hook, HookDisabledReason, InstallDecision,
                                  InstallRecord, InstallSource, Job,
                                  Manifest, ManifestCheck, ManifestProblem,
                                  ManifestReport, Permissions, ProblemScope)
from src.core.ZipSafety import LINK, PATH_TOO_LONG

PLUGIN = "example.tool"
REPO_ID = 812345678
REPO = "example/autona-tool"
GITHUB = InstallSource(kind="github", repo_id=REPO_ID, name=REPO)
MANUAL = InstallSource(kind="manual")


# ── zips and descriptors ────────────────────────────────────────────────

def _raw(pluginId=PLUGIN, version="1.0.0", **overrides):
    raw = {
        "manifest": 1, "id": pluginId, "version": version, "name": "Tool",
        "author": "example", "license": "MIT", "min_autona": "2.6.0",
        "protocol": [1], "platforms": ["windows-x64"],
        "entry": ["bin/tool.exe"],
        "permissions": {"network": [], "files": "inputs", "spawn": False,
                        "autona_api": "none"},
        "jobs": [{"id": "run", "title": "Run", "lane": "light",
                  "timeout_seconds": 10, "memory_mb": 128}],
    }
    raw.update(overrides)
    return raw


def _write_zip(path, entries, *, mutate=None):
    """``entries``: [(name, bytes or a dict for JSON)]."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            if mutate:
                mutate(info)
            if isinstance(data, dict):
                data = json.dumps(data).encode("utf-8")
            archive.writestr(info, data)
    return str(path)


def _plugin_zip(path, pluginId=PLUGIN, version="1.0.0", raw=None,
                extra=()):
    raw = raw if raw is not None else _raw(pluginId, version)
    return _write_zip(path, [(f"{pluginId}/plugin.json", raw),
                             (f"{pluginId}/bin/tool.exe", b"MZ" + b"0" * 64),
                             (f"{pluginId}/LICENSE", b"MIT"), *extra])


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _descriptor(zipPath, pluginId=PLUGIN, version="1.0.0", **overrides):
    data = {"autona_plugin": 1, "id": pluginId, "version": version,
            "tag": f"v{version}", "protocol": [1], "min_autona": "2.6.0",
            "zip": {"name": f"{pluginId}-{version}-windows-x64.zip",
                    "size": os.path.getsize(zipPath),
                    "sha256": _sha(zipPath)}}
    data.update(overrides)
    return data


# ── fakes ───────────────────────────────────────────────────────────────

def _manifest_from(raw):
    def field(item):
        return Field(key=item["key"], type=item["type"], label="label")

    jobs = tuple(
        Job(id=job["id"], title="t", lane=job.get("lane", "light"),
            timeout_seconds=job.get("timeout_seconds", 10),
            memory_mb=job.get("memory_mb"),
            params=tuple(field(item) for item in job.get("params", ())),
            hooks=tuple(Hook(on=hook["on"], label="l")
                        for hook in job.get("hooks", ())))
        for job in raw["jobs"])
    permissions = raw["permissions"]
    return Manifest(
        manifest=1, id=raw["id"], version=raw["version"], name=raw["name"],
        author=raw["author"], license=raw["license"],
        min_autona=raw["min_autona"], protocol=tuple(raw["protocol"]),
        platforms=tuple(raw["platforms"]), entry=tuple(raw["entry"]),
        permissions=Permissions(network=tuple(permissions["network"]),
                                files=permissions["files"],
                                spawn=permissions["spawn"],
                                autona_api=permissions["autona_api"],
                                note=permissions.get("note")),
        jobs=jobs,
        settings=tuple(field(item) for item in raw.get("settings", ())))


class FakeRegistry:
    """Check like the real one in what the installer depends on: the id
    held to the folder's name (a .new folder fails it)."""

    def __init__(self):
        self.checked = []
        self.extra = ()
        self.failure = None

    def Check(self, raw, folder, source, *, dev, hostVersion):
        self.checked.append((folder, source, dev, hostVersion))
        problems = list(self.extra)
        if os.path.basename(folder) != raw.get("id"):
            problems.append(ManifestProblem(ManifestCheck.ID_FOLDER_MISMATCH,
                                            ProblemScope.PLUGIN))
        return ManifestReport(_manifest_from(raw), tuple(problems))

    def JudgeDescribe(self, run, manifest):
        if self.failure is not None:
            return DescribeResult(answer=None, failure=self.failure)
        return DescribeResult(
            answer=DescribeAnswer(protocol=(1,), id=manifest.id,
                                  version=manifest.version), protocol=1)


class FakeRunner:
    def __init__(self):
        self.described = []

    def Describe(self, pluginDir, entry, *, dev):
        # describe runs in the staged folder, which holds the entry.
        assert os.path.isfile(os.path.join(pluginDir, *entry[0].split("/")))
        self.described.append((pluginDir, tuple(entry), dev))
        return object()


class FakeNetwork:
    def __init__(self):
        self.names = {}
        self.descriptors = {}
        self.zips = {}
        self.calls = []
        self.fail = False

    def RepoName(self, repoId):
        self.calls.append(("RepoName", repoId))
        if self.fail:
            raise OSError("offline")
        return self.names[repoId]

    def RepoId(self, name):
        self.calls.append(("RepoId", name))
        if self.fail:
            raise OSError("offline")
        return {value: key for key, value in self.names.items()}[name]

    def LatestDescriptor(self, name):
        self.calls.append(("LatestDescriptor", name))
        if self.fail:
            raise OSError("offline")
        return json.dumps(self.descriptors[name]).encode("utf-8")

    def Download(self, name, tag, zipName, target, size):
        self.calls.append(("Download", name, tag, zipName))
        with open(target, "wb") as file:
            file.write(self.zips[(name, tag, zipName)])


class FakeSecrets:
    def __init__(self):
        self.forgotten = []

    def Forget(self, pluginId, *, dev):
        self.forgotten.append((pluginId, dev))


class FakeHooks:
    def __init__(self):
        self.forgotten = []

    def Forget(self, pluginId, reason):
        self.forgotten.append((pluginId, reason))


class FakeJobs:
    def __init__(self):
        self.running = set()

    def Running(self, pluginId):
        return pluginId in self.running


@pytest.fixture
def env(tmp_path):
    class Env:
        pass

    e = Env()
    e.app = tmp_path / "Autona"
    e.user = e.app / "user_data"
    e.work = tmp_path / "work"
    e.app.mkdir()
    e.registry, e.runner = FakeRegistry(), FakeRunner()
    e.network, e.secrets = FakeNetwork(), FakeSecrets()
    e.hooks, e.jobs = FakeHooks(), FakeJobs()
    e.blocklist, e.devIds, e.official = [], set(), set()
    e.tmp = tmp_path

    def make(**kwargs):
        options = dict(
            registry=e.registry, runner=e.runner, hostVersion="2.6.0",
            network=e.network, secrets=e.secrets, hooks=e.hooks, jobs=e.jobs,
            workDir=lambda: str(e.work), isDevId=e.devIds.__contains__,
            blocklist=lambda: e.blocklist, officialRepoIds=e.official,
            clock=lambda: 1795000000.0, sleep=lambda _s: None)
        options.update(kwargs)
        return PluginInstaller(str(e.app), str(e.user), **options)

    e.make = make
    e.installer = make()
    e.plugins = e.app / "plugins"
    return e


def _user_dir(env, pluginId=PLUGIN):
    return env.user / "plugins" / pluginId


def _record(env, pluginId=PLUGIN):
    return json.loads((_user_dir(env, pluginId) / "install.json")
                      .read_text(encoding="utf-8"))


def _install_manual(env, name="a.zip", **kwargs):
    path = _plugin_zip(env.tmp / name, **kwargs)
    pending = env.installer.PrepareFromZip(path)
    return pending, env.installer.Commit(pending)


# ── CheckZip ────────────────────────────────────────────────────────────

def test_a_good_zip_has_no_problem(env):
    path = _plugin_zip(env.tmp / "a.zip")
    assert list(env.installer.CheckZip(path, None)) == []
    assert list(env.installer.CheckZip(path, _descriptor(path))) == []


def _symlink(info):
    if info.filename.endswith("link"):
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16


@pytest.mark.parametrize("entries, problem", [
    ([("plugin.json", _raw())], NOT_ONE_FOLDER),
    ([(f"{PLUGIN}/plugin.json", _raw()), ("stray.txt", b"x")],
     NOT_ONE_FOLDER),
    ([(f"{PLUGIN}/plugin.json", _raw()), ("other.tool/x", b"x")],
     NOT_ONE_FOLDER),
    ([("Not_An_Id/plugin.json", _raw())], FOLDER_NOT_ID),
    ([(f"{PLUGIN}/bin/tool.exe", b"MZ")], MANIFEST_MISSING),
    ([(f"{PLUGIN}/sub/plugin.json", _raw()),
      (f"{PLUGIN}/bin/tool.exe", b"MZ")], MANIFEST_MISSING),
    ([(f"{PLUGIN}/plugin.json", b"{not json"),
      (f"{PLUGIN}/bin/tool.exe", b"MZ")], MANIFEST_UNREADABLE),
    ([(f"{PLUGIN}/plugin.json", b"[1, 2]"),
      (f"{PLUGIN}/bin/tool.exe", b"MZ")], MANIFEST_UNREADABLE),
    ([(f"{PLUGIN}/plugin.json", _raw(pluginId="example.other")),
      (f"{PLUGIN}/bin/tool.exe", b"MZ")], ID_MISMATCH),
    ([(f"{PLUGIN}/plugin.json", _raw())], ENTRY_MISSING),
    ([(f"{PLUGIN}/plugin.json", _raw(entry=["../tool.exe"]))],
     ENTRY_OUTSIDE),
    ([(f"{PLUGIN}/plugin.json", _raw(entry=["C:/tool.exe"]))],
     ENTRY_OUTSIDE),
    ([(f"{PLUGIN}/plugin.json", _raw()), (f"{PLUGIN}/bin/tool.exe", b"MZ"),
      (f"{PLUGIN}/link", b"/etc/passwd")], LINK),
])
def test_the_zip_rules(env, entries, problem):
    path = _write_zip(env.tmp / "a.zip", entries, mutate=_symlink)
    assert problem in env.installer.CheckZip(path, None)


def test_backslash_zips_are_read_as_slashes_and_the_folder_stripped(env):
    path = _write_zip(env.tmp / "a.zip", [
        (f"{PLUGIN}\\plugin.json", _raw()),
        (f"{PLUGIN}\\bin\\tool.exe", b"MZ")])
    assert list(env.installer.CheckZip(path, None)) == []
    pending = env.installer.PrepareFromZip(path)
    env.installer.Commit(pending)
    assert (env.plugins / PLUGIN / "plugin.json").is_file()
    assert (env.plugins / PLUGIN / "bin" / "tool.exe").is_file()
    assert not (env.plugins / PLUGIN / PLUGIN).exists()


def test_a_reserved_entry_is_left_to_the_manifest_check(env):
    path = _write_zip(env.tmp / "a.zip", [
        (f"{PLUGIN}/plugin.json", _raw(entry=["@python", "main.py"]))])
    assert list(env.installer.CheckZip(path, None)) == []


def test_the_longest_installed_path_must_fit(env):
    # CheckZip only computes the paths: the folders need not exist.
    exe = "bin/" + "x" * 40 + ".exe"
    path = _write_zip(env.tmp / "a.zip", [
        (f"{PLUGIN}/plugin.json", _raw(entry=[exe])), (f"{PLUGIN}/{exe}", b"MZ")])
    installer = env.make()
    tail = len(os.path.join(PLUGIN, "bin", "x" * 40 + ".exe")) + 1

    def at(finalLength):
        # A plugins folder that makes the installed exe's path this long.
        base = os.path.abspath(os.sep + "a")
        filler = finalLength - tail - len(base) - len(os.sep + "plugins")
        installer.pluginsDir = base + "b" * filler + os.sep + "plugins"
        final = os.path.join(installer.pluginsDir, PLUGIN, "bin",
                             "x" * 40 + ".exe")
        assert len(final) == finalLength
        return installer.CheckZip(path, None)

    # The staging folder ("<id>.new", 4 longer) must fit too.
    assert PATH_TOO_LONG not in at(255)
    assert PATH_TOO_LONG in at(256)
    assert PATH_TOO_LONG in at(300)


def test_a_zip_over_the_limit_is_refused_unread(env, monkeypatch):
    path = _plugin_zip(env.tmp / "a.zip")
    monkeypatch.setattr(installer_module, "ZIP_MAX_BYTES",
                        os.path.getsize(path) - 1)
    assert list(env.installer.CheckZip(path, None)) == [ZIP_TOO_LARGE]


def test_a_file_that_is_no_zip(env):
    path = env.tmp / "a.zip"
    path.write_bytes(b"not a zip")
    assert list(env.installer.CheckZip(str(path), None)) == ["zip_unreadable"]


def test_the_descriptor_is_held_to_the_zip(env):
    path = _plugin_zip(env.tmp / "a.zip")
    good = _descriptor(path)
    check = env.installer.CheckZip
    assert SIZE_MISMATCH in check(path, {**good, "zip": {
        **good["zip"], "size": good["zip"]["size"] + 1}})
    assert SHA256_MISMATCH in check(path, {**good, "zip": {
        **good["zip"], "sha256": "0" * 64}})
    assert ID_MISMATCH in check(path, {**good, "id": "example.other"})
    assert VERSION_MISMATCH in check(path, {**good, "version": "1.0.1"})


@pytest.mark.parametrize("change", [
    {"tag": "v1/../x"}, {"tag": "v1\\x"}, {"tag": "v1..0"}, {"tag": "v%2e"},
    {"tag": "v1\n"}, {"tag": ""}, {"autona_plugin": 2},
    {"autona_plugin": True}, {"id": "Bad"}, {"version": "1.0"},
    {"protocol": []}, {"min_autona": "x"},
])
def test_a_descriptor_that_does_not_fit(env, change):
    path = _plugin_zip(env.tmp / "a.zip")
    data = {**_descriptor(path), **change}
    assert CheckDescriptor(data) == [DESCRIPTOR_INVALID]
    assert DESCRIPTOR_INVALID in env.installer.CheckZip(path, data)


@pytest.mark.parametrize("name", ["a/b.zip", "..zip", "a%20.zip", "a\x00"])
def test_a_descriptor_zip_name_that_cannot_be_a_url_part(env, name):
    path = _plugin_zip(env.tmp / "a.zip")
    data = _descriptor(path)
    data["zip"]["name"] = name
    assert CheckDescriptor(data) == [DESCRIPTOR_INVALID]


# ── the source decision (C.3's table) ───────────────────────────────────

def _installed(source=GITHUB, version="1.0.0"):
    return InstallRecord(source=source, version=version, sha256="a" * 64,
                         consent_digest="d")


OTHER = InstallSource(kind="github", repo_id=99, name="other/repo")


@pytest.mark.parametrize("installed, incoming, version, expected", [
    (None, GITHUB, "1.0.0", InstallDecision.NEW_INSTALL),
    (None, MANUAL, "1.0.0", InstallDecision.NEW_INSTALL),
    (_installed(), GITHUB, "1.1.0", InstallDecision.UPDATE),
    (_installed(), GITHUB, "1.0.0", InstallDecision.CONFIRM_SAME_OR_OLDER),
    (_installed(), GITHUB, "0.9.9", InstallDecision.CONFIRM_SAME_OR_OLDER),
    (_installed(), GITHUB, "bad", InstallDecision.CONFIRM_SAME_OR_OLDER),
    (_installed(), OTHER, "2.0.0", InstallDecision.NEW_INSTALL),
    (_installed(), MANUAL, "2.0.0", InstallDecision.NEW_INSTALL),
    (_installed(MANUAL), MANUAL, "2.0.0", InstallDecision.NEW_INSTALL),
    (_installed(MANUAL), GITHUB, "2.0.0", InstallDecision.NEW_INSTALL),
])
def test_the_source_table(installed, incoming, version, expected):
    assert DecideSource(PLUGIN, installed, incoming, version,
                        devIdExists=False) == expected


def test_a_dev_folders_id_is_refused_first():
    assert DecideSource(PLUGIN, _installed(), GITHUB, "2.0.0",
                        devIdExists=True) == InstallDecision.REFUSE_DEV_ID


def test_taguyan_only_from_an_official_repository():
    official = InstallSource(kind="github", repo_id=1234, name="yuya-tagu/x")
    assert DecideSource("taguyan.webhook", None, official, "1.0.0",
                        devIdExists=False) == InstallDecision.REFUSE_RESERVED
    assert DecideSource("taguyan.webhook", None, MANUAL, "1.0.0",
                        devIdExists=False, officialRepoIds={1234}
                        ) == InstallDecision.REFUSE_RESERVED
    assert DecideSource("taguyan.webhook", None, official, "1.0.0",
                        devIdExists=False, officialRepoIds={1234}
                        ) == InstallDecision.NEW_INSTALL


# ── the consent digest ──────────────────────────────────────────────────

def _digest_raw(**overrides):
    raw = _raw(
        permissions={"network": ["discord.com", "Hooks.Slack.com"],
                     "files": "inputs", "spawn": False, "autona_api": "none",
                     "note": {"ja": "送ります", "en": "Sends"}},
        settings=[{"key": "url", "type": "secret"},
                  {"key": "token", "type": "secret"},
                  {"key": "mention", "type": "select"}],
        jobs=[{"id": "post", "lane": "light", "timeout_seconds": 10,
               "params": [{"key": "image", "type": "result_image"},
                          {"key": "state", "type": "state"},
                          {"key": "message", "type": "textarea"}],
               "hooks": [{"on": "result_image_saved"}]},
              {"id": "match", "lane": "light", "timeout_seconds": 10,
               "params": [{"key": "m", "type": "match_record"}],
               "hooks": [{"on": "match_decided"}]}])
    raw.update(overrides)
    return raw


def test_the_digest_does_not_depend_on_order():
    raw = _digest_raw()
    shuffled = json.loads(json.dumps(raw))
    shuffled["permissions"]["network"].reverse()
    shuffled["permissions"]["note"] = {"en": "Sends", "ja": "送ります"}
    shuffled["permissions"]["network"][0] = \
        shuffled["permissions"]["network"][0].upper()
    shuffled["settings"].reverse()
    shuffled["jobs"].reverse()
    for job in shuffled["jobs"]:
        job["params"].reverse()
    assert ConsentDigest(_manifest_from(raw), GITHUB) == \
        ConsentDigest(_manifest_from(shuffled), GITHUB)


def test_the_digest_follows_a_rename_but_not_another_repository():
    manifest = _manifest_from(_digest_raw())
    renamed = InstallSource(kind="github", repo_id=REPO_ID, name="new/name")
    assert ConsentDigest(manifest, GITHUB) == ConsentDigest(manifest, renamed)
    assert ConsentDigest(manifest, GITHUB) != ConsentDigest(manifest, OTHER)
    assert ConsentDigest(manifest, GITHUB) != ConsentDigest(manifest, MANUAL)


def _changed(path, value):
    raw = _digest_raw()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return raw


@pytest.mark.parametrize("raw", [
    _changed(("permissions", "network"), ["discord.com"]),
    _changed(("permissions", "files"), "anywhere"),
    _changed(("permissions", "spawn"), True),
    _changed(("permissions", "autona_api"), "write"),
    _changed(("permissions", "note"), "Something else"),
    _changed(("settings",), [{"key": "url", "type": "secret"}]),
    _changed(("jobs", 1, "params"), [{"key": "m", "type": "archive_export"}]),
    _changed(("jobs", 1, "hooks"), []),
])
def test_a_change_consent_shows_changes_the_digest(raw):
    assert ConsentDigest(_manifest_from(raw), GITHUB) != \
        ConsentDigest(_manifest_from(_digest_raw()), GITHUB)


@pytest.mark.parametrize("raw", [
    _digest_raw(version="9.9.9"),
    _digest_raw(name="Renamed"),
    _changed(("settings",), [{"key": "url", "type": "secret"},
                             {"key": "token", "type": "secret"},
                             {"key": "other", "type": "text"}]),
    _changed(("jobs", 0, "params"), [{"key": "image", "type": "result_image"},
                                     {"key": "state", "type": "state"},
                                     {"key": "body", "type": "text"}]),
])
def test_what_consent_does_not_show_leaves_the_digest(raw):
    assert ConsentDigest(_manifest_from(raw), GITHUB) == \
        ConsentDigest(_manifest_from(_digest_raw()), GITHUB)


# ── the blocklist ───────────────────────────────────────────────────────

LISTING = {"listed": [], "blocked": [
    {"id": "bad.stealer", "versions": "*", "reason": {"en": "Exfiltrated"},
     "since": "2027-01-05"},
    {"id": "some.plugin", "versions": "<=1.2.0", "reason": "Broken",
     "since": "2027-01-05"},
    {"sha256": "3F5A" + "0" * 60, "reason": "Repackaged",
     "since": "2027-01-05"},
    {"repo_id": 99999999, "reason": "All from it", "since": "2027-01-05"},
    {"reason": "names nothing"},
    "not a row",
    {"id": "Bad Id", "reason": "unreadable id"},
    {"id": "typo.plugin", "versions": "<= one", "reason": "typo"},
    {"id": "none.given", "reason": "no versions"},
]}


def test_the_blocked_rows_are_read():
    rows = ParseBlocklist(LISTING)
    assert [(row.id, row.versions, row.sha256, row.repo_id) for row in rows] \
        == [("bad.stealer", "*", None, None),
            ("some.plugin", "<=1.2.0", None, None),
            (None, None, "3f5a" + "0" * 60, None),
            (None, None, None, 99999999),
            ("typo.plugin", "*", None, None),
            ("none.given", "*", None, None)]
    assert rows[0].reason == {"en": "Exfiltrated"}
    assert ParseBlocklist({"blocked": "nope"}) == ()
    assert ParseBlocklist(None) == ()


@pytest.mark.parametrize("versions, version, matches", [
    ("*", "0.0.1", True), ("<=1.2.0", "1.2.0", True),
    ("<=1.2.0", "1.2.1", False), ("<1.2.0", "1.2.0", False),
    ("=1.2.0", "1.2.0", True), (">=1.2.0", "1.1.9", False),
    (">1.2.0", "1.10.0", True), ("<=1.2.0", "not a version", True),
    (None, "1.0.0", True),
])
def test_versions(versions, version, matches):
    assert VersionMatches(versions, version) is matches


def test_the_three_kinds_of_block():
    rows = ParseBlocklist(LISTING)
    assert BlockedBy("bad.stealer", "9.0.0", None, None, rows) is rows[0]
    assert BlockedBy("some.plugin", "1.2.0", None, None, rows) is rows[1]
    assert BlockedBy("some.plugin", "1.3.0", None, None, rows) is None
    assert BlockedBy("any.thing", "1.0.0", "3f5a" + "0" * 60, None,
                     rows) is rows[2]
    assert BlockedBy("any.thing", "1.0.0", "3F5A" + "0" * 60, None,
                     rows) is rows[2]
    assert BlockedBy("any.thing", "1.0.0", None, 99999999, rows) is rows[3]
    assert BlockedBy("any.thing", "1.0.0", "b" * 64, 1, rows) is None


def test_the_blocklist_against_what_is_installed(env):
    for pluginId, data in [
            ("some.plugin", {"source": {"kind": "manual"},
                             "version": "1.0.0", "sha256": "c" * 64,
                             "consent_digest": "d"}),
            ("by.sha", {"source": {"kind": "manual"}, "version": "1.0.0",
                        "sha256": "3f5a" + "0" * 60, "consent_digest": "d"}),
            ("by.repo", {"source": {"kind": "github", "repo_id": 99999999,
                                    "name": "x/y"},
                         "version": "1.0.0", "sha256": "e" * 64,
                         "consent_digest": "d"}),
            ("fine.one", {"source": {"kind": "manual"}, "version": "1.0.0",
                          "sha256": "f" * 64, "consent_digest": "d"})]:
        folder = _user_dir(env, pluginId)
        folder.mkdir(parents=True)
        (folder / "install.json").write_text(json.dumps(data))
    rows = ParseBlocklist(LISTING)
    blocked = env.installer.BlockedInstalled(
        {"some.plugin": "1.0.0", "by.sha": "1.0.0", "by.repo": "1.0.0",
         "fine.one": "1.0.0", "no.record": "1.0.0"}, rows)
    assert blocked == {"some.plugin": rows[1], "by.sha": rows[2],
                       "by.repo": rows[3]}


# ── an install ──────────────────────────────────────────────────────────

def test_a_manual_zip_is_staged_then_committed(env):
    path = _plugin_zip(env.tmp / "a.zip")
    pending = env.installer.PrepareFromZip(path)
    assert pending.plugin_id == PLUGIN and pending.version == "1.0.0"
    assert pending.source == MANUAL and not pending.verified
    assert pending.decision == InstallDecision.NEW_INSTALL
    assert pending.needs_consent
    assert pending.sha256 == _sha(path)
    assert pending.size_bytes == len(json.dumps(_raw())) + 66 + 3
    # Staged only: nothing is started, nothing replaces anything.
    assert os.path.isdir(pending.new_dir)
    assert pending.new_dir == str(env.plugins / (PLUGIN + ".new"))
    assert not (env.plugins / PLUGIN).exists()
    assert env.runner.described == []
    assert env.registry.checked[0][0] == pending.new_dir
    assert env.registry.checked[0][1:] == (MANUAL, False, "2.6.0")
    # Check's id_folder_mismatch on the .new folder is not the plugin's.
    assert pending.report.problems == ()

    record = env.installer.Commit(pending)
    assert env.runner.described == [(pending.new_dir, ("bin/tool.exe",),
                                     False)]
    assert (env.plugins / PLUGIN / "bin" / "tool.exe").is_file()
    assert not os.path.exists(pending.new_dir)
    assert record == InstallRecord(MANUAL, "1.0.0", _sha(path),
                                   pending.consent_digest)
    assert _record(env) == {
        "source": {"kind": "manual"}, "version": "1.0.0",
        "sha256": _sha(path), "consent_digest": pending.consent_digest,
        "installed_at": _record(env)["installed_at"]}
    assert env.installer.ReadInstallRecord(PLUGIN) == record
    assert os.path.isfile(path)  # the user's zip stays
    assert env.installer.Pending(PLUGIN) is None


def test_a_new_install_forgets_secrets_and_hooks_before_the_swap(env):
    folder = _user_dir(env)
    folder.mkdir(parents=True)
    (folder / "secrets.bin").write_bytes(b"dpapi")
    (folder / "config.json").write_text("{}")
    _install_manual(env)
    assert env.secrets.forgotten == [(PLUGIN, False)]
    assert env.hooks.forgotten == [(PLUGIN, HookDisabledReason.NEW_INSTALL)]
    assert not (folder / "secrets.bin").exists()
    assert (folder / "config.json").exists()


def test_discard_removes_the_staged_folder(env):
    pending = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    assert env.installer.Discard(pending) is True
    assert not os.path.exists(pending.new_dir)
    assert env.installer.Discard(pending) is False
    with pytest.raises(InstallError) as error:
        env.installer.Commit(pending)
    assert error.value.code == NOT_PENDING


def test_a_second_prepare_replaces_the_first(env):
    first = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    second = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "b.zip",
                                                      version="1.0.1"))
    assert env.installer.Pending(PLUGIN) is second
    with pytest.raises(InstallError) as error:
        env.installer.Commit(first)
    assert error.value.code == NOT_PENDING
    env.installer.Commit(second)
    assert _record(env)["version"] == "1.0.1"


def test_a_bad_zip_writes_nothing(env):
    path = _write_zip(env.tmp / "a.zip", [("../escape", b"x")])
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(path)
    assert error.value.code == BAD_ZIP
    assert not env.plugins.exists() or os.listdir(env.plugins) == []


def test_a_plugin_scope_problem_refuses_and_removes_the_staging(env):
    env.registry.extra = (ManifestProblem(ManifestCheck.PROTOCOL_UNSUPPORTED,
                                          ProblemScope.PLUGIN),
                          ManifestProblem(ManifestCheck.JOB_UNREACHABLE,
                                          ProblemScope.WARNING))
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    assert error.value.code == MANIFEST_INVALID
    assert error.value.problems == ("protocol_unsupported",)
    assert not (env.plugins / (PLUGIN + ".new")).exists()


def test_a_job_scope_problem_is_kept_on_the_report(env):
    problem = ManifestProblem(ManifestCheck.FILL_COUNT, ProblemScope.JOB,
                              job="run")
    env.registry.extra = (problem,)
    pending = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    assert pending.report.problems == (problem,)


def test_the_folder_name_problem_is_kept_when_the_id_differs(env):
    # Only the .new folder's own name is forgiven, nothing else.
    env.registry.extra = (ManifestProblem(ManifestCheck.ID_FOLDER_MISMATCH,
                                          ProblemScope.PLUGIN),)
    original = env.registry.Check

    def check(raw, folder, source, **kwargs):
        report = original(raw, folder, source, **kwargs)
        return ManifestReport(_manifest_from({**raw, "id": "example.else"}),
                              report.problems)

    env.registry.Check = check
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    assert error.value.code == MANIFEST_INVALID


def test_a_describe_failure_installs_nothing(env):
    _install_manual(env)
    env.registry.failure = DescribeFailure.VERSION_MISMATCH
    pending = env.installer.PrepareFromZip(
        _plugin_zip(env.tmp / "b.zip", version="2.0.0"))
    with pytest.raises(InstallError) as error:
        env.installer.Commit(pending)
    assert error.value.code == DESCRIBE_FAILED
    assert error.value.problems == ("version_mismatch",)
    assert not os.path.exists(pending.new_dir)
    assert _record(env)["version"] == "1.0.0"
    assert json.loads((env.plugins / PLUGIN / "plugin.json").read_text())[
        "version"] == "1.0.0"


def test_a_record_that_cannot_be_written_puts_the_old_plugin_back(
        env, monkeypatch):
    _install_manual(env)
    before = _record(env)
    pending = env.installer.PrepareFromZip(
        _plugin_zip(env.tmp / "b.zip", version="2.0.0"))

    def fail(_pluginId, _record):
        raise OSError("disk full")

    monkeypatch.setattr(env.installer, "_WriteRecord", fail)
    with pytest.raises(InstallError) as error:
        env.installer.Commit(pending)
    assert error.value.code == SWAP_FAILED
    assert json.loads((env.plugins / PLUGIN / "plugin.json").read_text())[
        "version"] == "1.0.0"
    assert _record(env) == before
    assert sorted(os.listdir(env.plugins)) == [PLUGIN]


def test_no_swap_while_the_plugin_runs(env):
    pending = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    env.jobs.running.add(PLUGIN)
    with pytest.raises(InstallError) as error:
        env.installer.Commit(pending)
    assert error.value.code == RUNNING
    assert env.installer.Pending(PLUGIN) is pending  # press again later
    env.jobs.running.clear()
    env.installer.Commit(pending)


def test_a_dev_folders_id_and_a_reserved_id_are_refused(env):
    env.devIds.add(PLUGIN)
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    assert error.value.code == InstallDecision.REFUSE_DEV_ID.value
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(
            _plugin_zip(env.tmp / "b.zip", pluginId="taguyan.webhook"))
    assert error.value.code == InstallDecision.REFUSE_RESERVED.value
    assert not env.plugins.exists() or os.listdir(env.plugins) == []


def test_blocked_before_staging_and_again_at_commit(env):
    path = _plugin_zip(env.tmp / "a.zip")
    env.blocklist[:] = [BlockEntry(reason="x", since="", sha256=_sha(path))]
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(path)
    assert error.value.code == BLOCKED
    env.blocklist.clear()
    pending = env.installer.PrepareFromZip(path)
    env.blocklist[:] = [BlockEntry(reason="x", since="", id=PLUGIN,
                                   versions="*")]
    with pytest.raises(InstallError) as error:
        env.installer.Commit(pending)
    assert error.value.code == BLOCKED
    assert env.runner.described == []  # describe never runs for it
    assert not (env.plugins / PLUGIN).exists()


def test_an_install_in_progress_refuses_another(env):
    path = _plugin_zip(env.tmp / "a.zip")
    env.installer._busy.add(PLUGIN)
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromZip(path)
    assert error.value.code == IN_PROGRESS


# ── the source of a manual zip and of a listing ─────────────────────────

def _release(env, path, version="1.0.0", pluginId=PLUGIN):
    descriptor = _descriptor(path, pluginId=pluginId, version=version)
    env.network.names[REPO_ID] = REPO
    env.network.descriptors[REPO] = descriptor
    env.network.zips[(REPO, descriptor["tag"], descriptor["zip"]["name"])] = \
        open(path, "rb").read()
    return descriptor


def test_a_manual_zip_matching_its_update_repository_is_github(env):
    raw = _raw(update={"github": REPO})
    path = _plugin_zip(env.tmp / "a.zip", raw=raw)
    _release(env, path)
    pending = env.installer.PrepareFromZip(path)
    assert pending.source == GITHUB and pending.verified
    assert ("RepoId", REPO) in env.network.calls


def test_a_manual_zip_unlike_the_release_stays_manual(env):
    raw = _raw(update={"github": REPO})
    release = _plugin_zip(env.tmp / "release.zip", raw=raw)
    _release(env, release)
    other = _plugin_zip(env.tmp / "other.zip", raw=raw,
                        extra=[(f"{PLUGIN}/extra.txt", b"changed")])
    pending = env.installer.PrepareFromZip(other)
    assert pending.source == MANUAL and not pending.verified


def test_offline_a_manual_zip_stays_manual(env):
    path = _plugin_zip(env.tmp / "a.zip", raw=_raw(update={"github": REPO}))
    _release(env, path)
    env.network.fail = True
    assert env.installer.PrepareFromZip(path).source == MANUAL
    assert env.make(network=None).PrepareFromZip(path).source == MANUAL


def test_the_recorded_repository_is_asked_before_update_github(env):
    first = _plugin_zip(env.tmp / "a.zip")
    _release(env, first)
    env.installer.Commit(env.installer.PrepareFromListing(REPO_ID, PLUGIN))
    assert _record(env)["source"] == {"kind": "github", "repo_id": REPO_ID,
                                      "name": REPO}
    # The same release's zip, by hand, with another update.github in it:
    # the recorded repository decides.
    newer = _plugin_zip(env.tmp / "b.zip", version="1.1.0",
                        raw=_raw(version="1.1.0",
                                 update={"github": "someone/else"}))
    _release(env, newer, version="1.1.0")
    env.secrets.forgotten.clear()
    pending = env.installer.PrepareFromZip(newer)
    assert pending.source == GITHUB
    assert pending.decision == InstallDecision.UPDATE
    assert not pending.needs_consent  # the same digest
    env.installer.Commit(pending)
    assert env.secrets.forgotten == []  # an update keeps the secrets


def test_a_listing_install_downloads_checks_and_records(env):
    path = _plugin_zip(env.tmp / "a.zip")
    _release(env, path)
    pending = env.installer.PrepareFromListing(REPO_ID, PLUGIN)
    assert pending.source == GITHUB and pending.verified
    assert pending.decision == InstallDecision.NEW_INSTALL
    assert not (env.plugins / (PLUGIN + ".part")).exists()
    env.installer.Commit(pending)
    assert _record(env)["sha256"] == _sha(path)


def test_a_listing_whose_descriptor_names_another_id_stops(env):
    path = _plugin_zip(env.tmp / "a.zip")
    _release(env, path)
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromListing(REPO_ID, "example.other")
    assert (error.value.code, error.value.problems) == (BAD_ZIP,
                                                        (ID_MISMATCH,))
    assert not any(call[0] == "Download" for call in env.network.calls)


def test_a_download_unlike_its_descriptor_is_refused(env):
    path = _plugin_zip(env.tmp / "a.zip")
    descriptor = _release(env, path)
    key = (REPO, descriptor["tag"], descriptor["zip"]["name"])
    env.network.zips[key] = env.network.zips[key][:-1] + b"!"
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromListing(REPO_ID, PLUGIN)
    assert error.value.code == BAD_ZIP
    assert SHA256_MISMATCH in error.value.problems
    assert os.listdir(env.plugins) == []


def test_a_listing_fails_without_the_network(env):
    env.network.fail = True
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromListing(REPO_ID, PLUGIN)
    assert error.value.code == NETWORK_FAILED
    with pytest.raises(InstallError) as error:
        env.make(network=None).PrepareFromListing(REPO_ID, PLUGIN)
    assert error.value.code == NETWORK_FAILED


def test_an_official_listing_may_install_a_taguyan_id(env):
    path = _plugin_zip(env.tmp / "a.zip", pluginId="taguyan.webhook")
    _release(env, path, pluginId="taguyan.webhook")
    with pytest.raises(InstallError) as error:
        env.installer.PrepareFromListing(REPO_ID, "taguyan.webhook")
    assert error.value.code == InstallDecision.REFUSE_RESERVED.value
    env.official.add(REPO_ID)
    pending = env.make().PrepareFromListing(REPO_ID, "taguyan.webhook")
    assert pending.source == GITHUB


def test_another_repository_over_an_install_is_a_new_install(env):
    first = _plugin_zip(env.tmp / "a.zip")
    _release(env, first)
    env.installer.Commit(env.installer.PrepareFromListing(REPO_ID, PLUGIN))
    other = _plugin_zip(env.tmp / "b.zip", version="2.0.0")
    descriptor = _descriptor(other, version="2.0.0")
    env.network.names[99] = "thief/repo"
    env.network.descriptors["thief/repo"] = descriptor
    env.network.zips[("thief/repo", descriptor["tag"],
                      descriptor["zip"]["name"])] = open(other, "rb").read()
    env.secrets.forgotten.clear()
    pending = env.installer.PrepareFromListing(99, PLUGIN)
    assert pending.decision == InstallDecision.NEW_INSTALL
    assert pending.needs_consent
    env.installer.Commit(pending)
    assert env.secrets.forgotten == [(PLUGIN, False)]


def test_a_changed_digest_asks_consent_on_an_update(env):
    first = _plugin_zip(env.tmp / "a.zip")
    _release(env, first)
    env.installer.Commit(env.installer.PrepareFromListing(REPO_ID, PLUGIN))
    raw = _raw(version="1.1.0")
    raw["permissions"]["network"] = ["*"]
    newer = _plugin_zip(env.tmp / "b.zip", version="1.1.0", raw=raw)
    _release(env, newer, version="1.1.0")
    pending = env.installer.PrepareFromListing(REPO_ID, PLUGIN)
    assert pending.decision == InstallDecision.UPDATE
    assert pending.needs_consent


def test_a_damaged_record_reads_as_none(env):
    folder = _user_dir(env)
    folder.mkdir(parents=True)
    for content in ["{not json", "[]", json.dumps({"source": {"kind": "x"}}),
                    json.dumps({"source": {"kind": "github"},
                                "version": "1.0.0", "sha256": None,
                                "consent_digest": "d"}),
                    json.dumps({"source": {"kind": "manual"},
                                "version": "1.0", "sha256": None,
                                "consent_digest": "d"})]:
        (folder / "install.json").write_text(content)
        assert env.installer.ReadInstallRecord(PLUGIN) is None


# ── removal ─────────────────────────────────────────────────────────────

def _installed_with_state(env):
    _install_manual(env)
    folder = _user_dir(env)
    (folder / "secrets.bin").write_bytes(b"dpapi")
    (folder / "config.json").write_text("{}")
    (folder / "hooks.json").write_text("{}")
    (folder / "data").mkdir()
    (folder / "data" / "state.txt").write_text("kept?")
    runs = env.work / "plugins" / PLUGIN
    for runId in ("20261126-213000-a1b2", "20261126-213100-c3d4"):
        (runs / runId / "out").mkdir(parents=True)
        (runs / runId / "out" / "result.txt").write_text("out")
        (runs / runId / "inputs").mkdir()
        (runs / runId / "worker.log").write_text("log")
    return folder, runs


def test_remove_keeps_data_and_outs_when_asked(env):
    folder, runs = _installed_with_state(env)
    env.secrets.forgotten.clear()
    env.hooks.forgotten.clear()
    env.installer.Remove(PLUGIN, data=False, runs="keep_out")
    assert not (env.plugins / PLUGIN).exists()
    assert sorted(os.listdir(folder)) == ["data"]
    assert env.secrets.forgotten == [(PLUGIN, False)]
    assert env.hooks.forgotten == [(PLUGIN, HookDisabledReason.USER)]
    for runId in os.listdir(runs):
        assert os.listdir(runs / runId) == ["out"]


def test_remove_everything(env):
    folder, runs = _installed_with_state(env)
    env.installer.Remove(PLUGIN, data=True, runs="delete")
    assert not folder.exists() and not runs.exists()
    assert not (env.plugins / PLUGIN).exists()


def test_remove_keeping_the_runs(env):
    _folder, runs = _installed_with_state(env)
    env.installer.Remove(PLUGIN, data=True, runs="keep")
    assert len(os.listdir(runs)) == 2


def test_remove_refusals(env):
    with pytest.raises(InstallError) as error:
        env.installer.Remove(PLUGIN, data=True, runs="delete")
    assert error.value.code == NOT_FOUND
    _install_manual(env)
    env.jobs.running.add(PLUGIN)
    with pytest.raises(InstallError) as error:
        env.installer.Remove(PLUGIN, data=True, runs="delete")
    assert error.value.code == RUNNING
    assert (env.plugins / PLUGIN).exists()
    with pytest.raises(InstallError) as error:
        env.installer.Remove(PLUGIN, data=True, runs="everything")
    assert error.value.code == "invalid_request"
    with pytest.raises(InstallError):
        env.installer.Remove("../escape", data=True, runs="delete")


# ── the launch's sweep ──────────────────────────────────────────────────

def test_sweep_cleans_what_a_crash_left(env):
    plugins = env.plugins
    (plugins / "half.done.new").mkdir(parents=True)
    (plugins / "lost.one.old").mkdir()
    (plugins / "lost.one.old" / "plugin.json").write_text("{}")
    (plugins / "kept.one").mkdir()
    (plugins / "kept.one.old").mkdir()
    (plugins / "get.this.part").write_bytes(b"partial")
    (plugins / "unrelated.folder").mkdir()
    (plugins / "Not An Id.new").mkdir()
    env.installer.Sweep()
    assert sorted(os.listdir(plugins)) == ["Not An Id.new", "kept.one",
                                           "lost.one", "unrelated.folder"]
    assert (plugins / "lost.one" / "plugin.json").is_file()


def test_sweep_leaves_a_pending_install(env):
    pending = env.installer.PrepareFromZip(_plugin_zip(env.tmp / "a.zip"))
    env.installer.Sweep()
    assert os.path.isdir(pending.new_dir)


def test_sweep_without_a_plugins_folder(env):
    env.installer.Sweep()


def test_the_zip_bytes_are_reported(env):
    path = _plugin_zip(env.tmp / "a.zip")
    pending = env.installer.PrepareFromZip(path)
    assert pending.zip_bytes == os.path.getsize(path)


def test_an_install_record_written_is_utf8_without_bom(env):
    _install_manual(env)
    content = (_user_dir(env) / "install.json").read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert json.loads(content)["installed_at"].startswith("2026-")


def test_the_module_reads_its_input_only_from_arguments():
    # ReleaseNetwork is a Protocol: the module opens no connection itself.
    source = open(installer_module.__file__, encoding="utf-8").read()
    for name in ("urllib", "requests", "http.client", "subprocess",
                 "socket"):
        assert f"import {name}" not in source
