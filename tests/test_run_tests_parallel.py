"""scripts/run_tests_parallel.py: pure logic, plus integration tests that start the
runner (a subprocess) on a throw-away project under tmp_path. Those children never
see the real conftest, cache or out/ directory.
"""
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load():
    """Import scripts/run_tests_parallel.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_tests_parallel", PROJECT_ROOT / "scripts" / "run_tests_parallel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load()


def test_split_covers_every_file_exactly_once():
    files = [Path(f"t{i}.py") for i in range(11)]
    sizes = {f: (i + 1) * 100 for i, f in enumerate(files)}
    shards = runner.split_files(files, 4, size_of=sizes.get)
    flat = [f for shard in shards for f in shard]
    assert sorted(flat) == sorted(files)
    assert len(shards) == 4


def test_split_balances_by_size():
    files = [Path("a"), Path("b"), Path("c"), Path("d")]
    sizes = {Path("a"): 10, Path("b"): 9, Path("c"): 2, Path("d"): 1}
    shards = runner.split_files(files, 2, size_of=sizes.get)
    loads = sorted(sum(sizes[f] for f in shard) for shard in shards)
    assert loads == [11, 11]


def test_split_never_returns_an_empty_shard():
    files = [Path("a"), Path("b")]
    shards = runner.split_files(files, 8, size_of=lambda p: 1)
    assert len(shards) == 2 and all(shards)


def test_collected_files_exclude_live_and_match_pytest_pattern():
    files = runner.collect_test_files()
    assert files
    assert all(f.name.startswith("test_") and f.suffix == ".py" for f in files)
    assert not any("live" in f.relative_to(runner.TESTS_DIR).parts for f in files)
    assert Path(__file__).resolve() in [f.resolve() for f in files]


def test_parse_summary_reads_the_final_line():
    out = "....\nFAILED tests/a.py::t - x\n1 failed, 3461 passed, 2 skipped in 140.00s (0:02:20)\n"
    assert runner.parse_summary(out) == {"failed": 1, "passed": 3461, "skipped": 2}


def test_parse_summary_counts_errors_and_warnings():
    out = "2 passed, 1 warning, 3 errors in 1.50s"
    assert runner.parse_summary(out) == {"passed": 2, "warnings": 1, "errors": 3}


def test_parse_summary_returns_none_when_unparseable():
    assert runner.parse_summary("") is None
    assert runner.parse_summary("Fatal Python error: access violation\n") is None


def test_failed_test_ids_drop_the_message():
    out = ("FAILED tests/a.py::test_x - AssertionError: no\n"
           "ERROR tests/b.py::test_y\n"
           "PASSED tests/c.py::test_z\n")
    assert runner.failed_test_ids(out) == [
        "FAILED tests/a.py::test_x", "ERROR tests/b.py::test_y"]


def test_merge_counts_sums_and_ignores_none():
    merged = runner.merge_counts([{"passed": 2, "failed": 1}, None, {"passed": 3}])
    assert merged == {"passed": 5, "failed": 1}


def test_exit_code_is_zero_only_when_every_worker_is_clean():
    ok = (0, {"passed": 1})
    assert runner.overall_exit_code([ok, ok]) == 0
    assert runner.overall_exit_code([ok, (1, {"failed": 1})]) == 1


def test_a_crashed_worker_or_unparsed_summary_is_never_success():
    ok = (0, {"passed": 1})
    assert runner.overall_exit_code([ok, (-1073741819, None)]) == 1
    assert runner.overall_exit_code([ok, (0, None)]) == 1
    assert runner.overall_exit_code([(1, {"passed": 1})]) == 1
    assert runner.overall_exit_code([]) == 1


def _make_tree(tmp_path, names):
    root = tmp_path / "proj"
    files = []
    for name in names:
        f = root / "tests" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x" * 100)
        files.append(f)
    return root, files


def _write_xml(path, cases):
    body = "".join(f'<testcase classname="{c}" name="t" time="{t}"/>' for c, t in cases)
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")
    return path


def test_junit_times_are_summed_per_file_with_and_without_classes(tmp_path):
    root, files = _make_tree(tmp_path, ["test_a.py", "sub/test_b.py"])
    xml = _write_xml(tmp_path / "w.xml", [
        ("tests.test_a", 1.0), ("tests.test_a.TestY", 2.5),
        ("tests.sub.test_b.TestZ", 4.0), ("tests.unknown", 9.0)])
    times = runner.file_times_from_junit([xml], files, root)
    assert times == {"tests/test_a.py": 3.5, "tests/sub/test_b.py": 4.0}


def test_junit_unreadable_xml_gives_none(tmp_path):
    root, files = _make_tree(tmp_path, ["test_a.py"])
    bad = tmp_path / "bad.xml"
    bad.write_text("<not xml")
    assert runner.file_times_from_junit([bad], files, root) is None
    assert runner.file_times_from_junit([tmp_path / "missing.xml"], files, root) is None


def test_weights_mix_known_seconds_and_scaled_sizes(tmp_path):
    root, files = _make_tree(tmp_path, ["test_a.py", "test_b.py"])
    files[1].write_text("x" * 300)
    weight, known = runner.make_weight(files, {"tests/test_a.py": 10.0}, root)
    assert known == 1
    assert weight(files[0]) == 10.0
    assert weight(files[1]) == 300 * (10.0 / 100)


def test_weights_fall_back_to_size_without_cache(tmp_path):
    root, files = _make_tree(tmp_path, ["test_a.py"])
    for cache in (None, {}, {"tests/other.py": 5.0}):
        weight, known = runner.make_weight(files, cache, root)
        assert known == 0 and weight(files[0]) == 100


def test_broken_caches_are_rejected(tmp_path):
    p = tmp_path / "c.json"
    assert runner.load_cache(p) is None  # missing
    for text in ("{not json", "[1, 2]", '{"a": "x"}', '{"a": true}', '{"a": -1}', "{}"):
        p.write_text(text)
        assert runner.load_cache(p) is None, text
    p.write_text('{"tests/a.py": 1.5}')
    assert runner.load_cache(p) == {"tests/a.py": 1.5}


def test_cache_is_written_only_by_a_full_run(tmp_path):
    p = tmp_path / "c.json"
    assert runner.update_cache_if_full_run({"a": 1.0}, ["-m", "not slow"], p) is False
    assert runner.update_cache_if_full_run(None, [], p) is False
    assert not p.exists()
    assert runner.update_cache_if_full_run({"a": 1.0}, [], p) is True
    assert runner.load_cache(p) == {"a": 1.0}
    assert not list(tmp_path.glob("*.tmp"))


def test_slow_files_land_on_different_workers():
    files = [Path(n) for n in ("slow1", "slow2", "fast1", "fast2")]
    secs = {Path("slow1"): 100, Path("slow2"): 90, Path("fast1"): 5, Path("fast2"): 5}
    shards = runner.split_files(files, 2, size_of=secs.get)
    assert not ({Path("slow1"), Path("slow2")} <= set(shards[0])
                or {Path("slow1"), Path("slow2")} <= set(shards[1]))


def test_own_junitxml_argument_is_detected():
    assert runner.has_own_junit(["--junitxml=x.xml"])
    assert runner.has_own_junit(["--junitxml", "x.xml"])
    assert not runner.has_own_junit(["-m", "slow"])


def test_junit_xml_spelling_is_detected_too():
    assert runner.has_own_junit(["--junit-xml=x.xml"])
    assert runner.has_own_junit(["--junit-xml", "x.xml"])


def test_exit_five_is_tolerated_only_for_a_partial_run_that_ran_something():
    ok = (0, {"passed": 1})
    idle = (5, None)
    assert runner.overall_exit_code([ok, idle]) == 1
    assert runner.overall_exit_code([ok, idle], allow_no_tests=True) == 0
    assert runner.overall_exit_code([idle, idle], allow_no_tests=True) == 1
    assert runner.overall_exit_code([ok, (1, {"failed": 1})], allow_no_tests=True) == 1


def test_a_summary_with_only_deselected_is_parsed():
    assert runner.parse_summary("1 deselected in 0.01s\n") == {"deselected": 1}


# --- integration: the runner really starts pytest workers on a throw-away project ---

RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_tests_parallel.py"

PASS_A = "def test_alpha():\n    assert True\n"
PASS_B = "def test_beta():\n    assert True\n"


def _project(tmp_path, files):
    """A tiny project with its own tests/ and no conftest (never the real one)."""
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    for name, text in files.items():
        (root / "tests" / name).write_text(text, encoding="utf-8")
    return root


def _run_runner(tmp_path, root, *args, console_encoding="utf-8"):
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env["PYTHONIOENCODING"] = console_encoding
    done = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "-n", "2", "--project-root", str(root),
         "--cache-path", str(tmp_path / "cache.json"), *args],
        capture_output=True, timeout=120, env=env)
    out = done.stdout.decode(console_encoding, errors="replace")
    return done.returncode, out, done.stderr.decode(console_encoding, errors="replace")


def _pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_runner_all_green_exits_zero(tmp_path):
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_b.py": PASS_B})
    code, out, _ = _run_runner(tmp_path, root)
    assert code == 0, out
    assert out.rstrip().splitlines()[-1] == "RESULT: OK"
    assert "2 passed" in out
    assert (tmp_path / "cache.json").exists()  # full run wrote the cache (tmp only)


def test_runner_survives_non_cp932_text_in_a_failure(tmp_path):
    failing = ('def test_boom():\n'
               '    assert 1 == 2, "\u65e5\u672c\u8a9e\u2713"\n')
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_bad.py": failing})
    code, out, err = _run_runner(tmp_path, root, console_encoding="cp932")
    assert "UnicodeEncodeError" not in err and "Traceback" not in err, err
    assert code == 1
    assert "FAILED tests/test_bad.py::test_boom" in out
    assert "RESULT: FAILED (1 workers failed)" in out
    assert out.rstrip().splitlines()[-1].startswith("RESULT: FAILED")
    assert "python -m pytest -q -ra -p no:cacheprovider tests/test_bad.py" in out


def test_runner_partial_run_tolerates_a_shard_with_no_selected_test(tmp_path):
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_b.py": PASS_B})
    code, out, _ = _run_runner(tmp_path, root, "-k", "alpha")
    assert code == 0, out
    assert out.rstrip().splitlines()[-1] == "RESULT: OK"
    assert not (tmp_path / "cache.json").exists()  # partial run never writes it


def test_runner_fails_when_no_shard_selects_anything(tmp_path):
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_b.py": PASS_B})
    code, out, _ = _run_runner(tmp_path, root, "-k", "nothing_matches")
    assert code == 1, out
    assert out.rstrip().splitlines()[-1].startswith("RESULT: FAILED")


def test_runner_fails_when_a_worker_dies_without_a_summary(tmp_path):
    dying = "import os\n\ndef test_die():\n    os._exit(0)\n"
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_die.py": dying})
    code, out, _ = _run_runner(tmp_path, root)
    assert code == 1, out
    assert out.rstrip().splitlines()[-1] == "RESULT: FAILED (1 workers failed)"


def test_runner_kills_hung_workers_at_the_timeout(tmp_path):
    pid_file = tmp_path / "hung.pid"
    hung = ("import os, time\n\ndef test_hang():\n"
            f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "    time.sleep(120)\n")
    root = _project(tmp_path, {"test_a.py": PASS_A, "test_hang.py": hung})
    code, out, _ = _run_runner(tmp_path, root, "--timeout", "6")
    assert code == 1, out
    assert "timeout" in out
    assert out.rstrip().splitlines()[-1] == "RESULT: FAILED (1 workers failed)"
    pid = int(pid_file.read_text())
    for _ in range(50):
        if not _pid_alive(pid):
            break
        time.sleep(0.2)
    assert not _pid_alive(pid)
