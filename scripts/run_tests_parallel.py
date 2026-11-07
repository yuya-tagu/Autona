"""Run the whole pytest suite in several plain pytest processes (stdlib only).

Usage (from anywhere; the project root is used as the working directory):

    python scripts/run_tests_parallel.py              # min(4, cpu_count) workers
    python scripts/run_tests_parallel.py -n 3         # choose the worker count
    python scripts/run_tests_parallel.py -m "not slow"   # extra args go to every pytest

The test files under tests/ (tests/live excluded, same as the serial command
`python -m pytest -q -ra -p no:cacheprovider --ignore=tests/live`) are split with
a greedy balance by the per-file seconds measured on the previous full run
(cache: <tempdir>/autona_pytest_file_times.json, outside the repo). Files with no
measurement, and runs with no usable cache, fall back to file size (scaled to
seconds when some files are known). The cache is rewritten only by a full run
(no extra pytest args) whose every worker wrote a readable JUnit XML; a partial
run (-m / -k ...) never overwrites it. Every worker is an ordinary pytest process, so
each one bootstraps on its own and Qt is never shared. This is NOT pytest-xdist
(no new dependency). CI stays serial on purpose: it is the net for order
dependence between files and for anything a split could hide.

Output of each worker is captured and only printed at the end: a merged summary,
the failed / errored test ids, and the full output of every failed worker.
Exit code is 0 only if every worker exited 0 and every summary could be parsed.
With extra pytest args (-k / -m ...) a worker whose shard selects no test exits 5;
that counts as ok, unless every worker did so (nothing ran at all = failure).
A failed worker is preceded by its file list and a command that reproduces the
same combination. The last line is `RESULT: OK` or `RESULT: FAILED (...)`.

--timeout SECONDS (default 1200) bounds the whole run: past it the workers are
killed, counted as failed, the tail of their output is shown and the exit code is 1.
"""
import argparse
import os
import re
import json
import shlex
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"
EXCLUDED_DIRS = ("live",)
DEFAULT_MAX_WORKERS = 4
DEFAULT_TIMEOUT = 1200.0
POLL_INTERVAL = 0.2
NO_TESTS_EXIT = 5  # pytest: no test collected / all deselected
TAIL_LINES = 40
CACHE_PATH = Path(tempfile.gettempdir()) / "autona_pytest_file_times.json"

_COUNT_RE = re.compile(
    r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)")
_SUMMARY_RE = re.compile(r"\bin \d+(\.\d+)?s\b")
_KEYS = {"error": "errors", "warning": "warnings", "warnings": "warnings",
         "errors": "errors"}


def collect_test_files(tests_dir=TESTS_DIR):
    """Every test_*.py under tests_dir except the excluded sub-directories."""
    found = []
    for path in sorted(Path(tests_dir).rglob("test_*.py")):
        relative = path.relative_to(tests_dir)
        if relative.parts[0] in EXCLUDED_DIRS:
            continue
        found.append(path)
    return found


def split_files(files, workers, size_of=lambda path: Path(path).stat().st_size):
    """Greedy split: biggest file first, always onto the lightest shard.

    Never returns an empty shard (a pytest run with no files would collect the
    whole suite), so fewer shards than `workers` come back for few files.
    """
    workers = max(1, min(workers, len(files)))
    shards = [[] for _ in range(workers)]
    load = [0] * workers
    for path in sorted(files, key=lambda p: (-size_of(p), str(p))):
        i = load.index(min(load))
        shards[i].append(path)
        load[i] += size_of(path)
    return [sorted(shard, key=str) for shard in shards if shard]


def relative_key(path, root=PROJECT_ROOT):
    """Cache key of a test file: path relative to the project, '/' separated."""
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def file_times_from_junit(xml_paths, files, root=PROJECT_ROOT):
    """Sum testcase seconds per test file from JUnit XML files.

    Returns {relative path: seconds}, or None if any XML is missing/unreadable.
    A testcase classname is the dotted module path, plus the class for class
    based tests (tests.test_x.TestY); it is matched to the longest known module.
    """
    modules = {}
    for f in files:
        key = relative_key(f, root)
        modules[key[:-3].replace("/", ".")] = key
    times = {}
    try:
        for xml_path in xml_paths:
            for case in ET.parse(xml_path).getroot().iter("testcase"):
                parts = (case.get("classname") or "").split(".")
                for n in range(len(parts), 0, -1):
                    key = modules.get(".".join(parts[:n]))
                    if key:
                        times[key] = times.get(key, 0.0) + float(case.get("time") or 0)
                        break
    except (OSError, ET.ParseError, ValueError):
        return None
    return times


def load_cache(path=CACHE_PATH):
    """{relative path: seconds} from the cache, or None if absent / broken."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    for key, value in data.items():
        if (not isinstance(key, str) or isinstance(value, bool)
                or not isinstance(value, (int, float)) or value < 0):
            return None
    return data


def save_cache(times, path=CACHE_PATH):
    """Atomic write (temp file + os.replace)."""
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(times, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def make_weight(files, cache, root=PROJECT_ROOT):
    """Return (weight function, number of files with a measured time).

    Known files weigh their measured seconds; the rest weigh their size scaled
    by (known seconds / known size). With no known file the size is used as is.
    """
    sizes = {f: Path(f).stat().st_size for f in files}
    known = {f: cache[relative_key(f, root)] for f in files
             if cache and relative_key(f, root) in cache}
    known_size = sum(sizes[f] for f in known)
    known_secs = sum(known.values())
    scale = known_secs / known_size if known and known_size and known_secs else (
        1.0 if not known else None)
    if scale is None:  # known files exist but give no usable ratio
        known, scale = {}, 1.0

    def weight(path):
        return known[path] if path in known else sizes[path] * scale
    return weight, len(known)


def update_cache_if_full_run(times, pytest_args, path=CACHE_PATH):
    """Write the cache only for a full run (no extra args) with readable times."""
    if times is None or pytest_args:
        return False
    save_cache(times, path)
    return True


def has_own_junit(pytest_args):
    return any(a in ("--junitxml", "--junit-xml")
               or a.startswith(("--junitxml=", "--junit-xml="))
               for a in pytest_args)


def parse_summary(output):
    """Counts from pytest's final summary line, or None if there is none.

    None means "could not be parsed" and must never be treated as success.
    """
    for line in reversed(output.splitlines()):
        if not _SUMMARY_RE.search(line):
            continue
        counts = {}
        for number, word in _COUNT_RE.findall(line):
            key = _KEYS.get(word, word)
            counts[key] = counts.get(key, 0) + int(number)
        if counts:
            return counts
    return None


def failed_test_ids(output):
    """FAILED / ERROR lines of the -ra short summary (test id, no message)."""
    ids = []
    for line in output.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            kind, _, rest = line.partition(" ")
            ids.append(f"{kind} {rest.split(' - ', 1)[0].strip()}")
    return ids


def worker_ok(exit_code, counts):
    """A worker passes only with exit code 0 and a parsed summary."""
    return exit_code == 0 and counts is not None


def merge_counts(all_counts):
    total = {}
    for counts in all_counts:
        for key, value in (counts or {}).items():
            total[key] = total.get(key, 0) + value
    return total


def worker_state(exit_code, counts, allow_no_tests=False):
    """'ok', 'idle' (exit 5 tolerated for a partial run) or 'FAILED'."""
    if allow_no_tests and exit_code == NO_TESTS_EXIT:
        return "idle"
    return "ok" if worker_ok(exit_code, counts) else "FAILED"


def overall_exit_code(results, allow_no_tests=False):
    """results: list of (exit_code, counts). 0 only if every worker is ok.

    With allow_no_tests, exit 5 means "nothing of this shard was selected" and is
    fine, but not when every worker says so (then nothing ran at all).
    """
    states = [worker_state(code, counts, allow_no_tests) for code, counts in results]
    if not states or "FAILED" in states or all(st == "idle" for st in states):
        return 1
    return 0


def _kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                       capture_output=True)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_workers(shards, pytest_args, files=None, cwd=PROJECT_ROOT, timeout=None):
    """Start one pytest per shard, wait for all; return [(exit_code, output)].

    With `files` given and no user --junitxml, each worker also writes a JUnit
    XML; the per-file times (or None) are stored in run_workers.times. Past
    `timeout` seconds the running workers are killed: their exit code is None and
    their indexes are stored in run_workers.timed_out.
    """
    collect = files is not None and not has_own_junit(pytest_args)
    run_workers.times = None
    run_workers.timed_out = set()
    # Children write UTF-8 to the pipe whatever the console code page is.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    processes = []
    with tempfile.TemporaryDirectory(prefix="autona_pytest_",
                                     ignore_cleanup_errors=True) as tmp:
        try:
            for i, shard in enumerate(shards):
                log = open(Path(tmp) / f"worker_{i}.txt", "wb")
                command = [sys.executable, "-m", "pytest", "-q", "-ra",
                           "-p", "no:cacheprovider", *pytest_args,
                           *([f"--junitxml={Path(tmp) / f'worker_{i}.xml'}"]
                             if collect else []),
                           *(str(p) for p in shard)]
                process = subprocess.Popen(
                    command, cwd=cwd, stdout=log, env=env,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                processes.append((process, log))
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                pending = [i for i, (process, _) in enumerate(processes)
                           if process.poll() is None]
                if not pending:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    run_workers.timed_out = set(pending)
                    break
                time.sleep(POLL_INTERVAL)
        finally:
            # Timeout, Ctrl+C or any exception: leave no child (and no grandchild).
            for process, log in processes:
                _kill_tree(process)
                log.close()
        codes = [None if i in run_workers.timed_out else process.returncode
                 for i, (process, _) in enumerate(processes)]
        if collect and not run_workers.timed_out:
            run_workers.times = file_times_from_junit(
                [Path(tmp) / f"worker_{i}.xml" for i in range(len(codes))], files,
                cwd)
        return [(code, (Path(tmp) / f"worker_{i}.txt").read_bytes()
                 .decode("utf-8", errors="replace"))
                for i, code in enumerate(codes)]


def _tail(output, lines=TAIL_LINES):
    return "\n".join(output.rstrip().splitlines()[-lines:])


def main(argv=None):
    # A non-UTF-8 console (cp932) must never kill the runner while printing.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Unknown arguments are passed to every pytest worker.")
    parser.add_argument("-n", "--workers", type=int,
                        default=min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds for the whole run before the workers are "
                             "killed (default %(default)s)")
    # Test hooks: run against another project / cache (used by the runner's tests).
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT,
                        help=argparse.SUPPRESS)
    parser.add_argument("--cache-path", type=Path, default=CACHE_PATH,
                        help=argparse.SUPPRESS)
    args, pytest_args = parser.parse_known_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    root = args.project_root

    files = collect_test_files(root / "tests")
    if not files:
        print("no test files found under tests/", file=sys.stderr)
        return 1
    cache = load_cache(args.cache_path)
    weight, known = make_weight(files, cache, root)
    shards = split_files(files, args.workers, size_of=weight)
    basis = (f"split by measured times ({known} known)" if known
             else "split by file size (no usable time cache)")
    print(f"{len(files)} test files, {len(shards)} workers, {basis}", flush=True)

    start = time.monotonic()
    try:
        raw = run_workers(shards, pytest_args, files, cwd=root,
                          timeout=args.timeout)
    except KeyboardInterrupt:
        print("interrupted; workers terminated", file=sys.stderr)
        return 130
    wall = time.monotonic() - start
    timed_out = run_workers.timed_out

    # Timing is best effort: it never affects the verdict below.
    if has_own_junit(pytest_args) or timed_out:
        pass
    elif run_workers.times is None:
        print("warning: could not read worker JUnit XML; time cache not updated",
              file=sys.stderr)
    else:
        try:
            update_cache_if_full_run(run_workers.times, pytest_args, args.cache_path)
        except OSError as e:
            print(f"warning: could not write time cache: {e}", file=sys.stderr)

    partial = bool(pytest_args)
    results = [(code, parse_summary(output)) for code, output in raw]
    states = [worker_state(code, counts, partial) for code, counts in results]

    total = merge_counts(counts for _, counts in results)
    print("=" * 60)
    for i, ((code, counts), (_, output)) in enumerate(zip(results, raw)):
        last = output.strip().splitlines()[-1] if output.strip() else ""
        exit_text = "timeout" if i in timed_out else f"exit={code}"
        print(f"worker {i}: {'ok' if states[i] == 'idle' else states[i]} "
              f"{exit_text} {len(shards[i])} files | {last}")
    failures = [t for _, output in raw for t in failed_test_ids(output)]
    if failures:
        print("failed / errored tests:")
        for test_id in failures:
            print(f"  {test_id}")
    for i, ((code, counts), (_, output)) in enumerate(zip(results, raw)):
        if states[i] != "FAILED":
            continue
        names = [relative_key(f, root) for f in shards[i]]
        print("=" * 60)
        print(f"--- worker {i} shard ({len(names)} files) ---")
        for name in names:
            print(f"  {name}")
        print("reproduce: " + shlex.join(
            ["python", "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider",
             *pytest_args, *names]))
        if i in timed_out:
            print(f"--- worker {i} timed out after {args.timeout:g}s; "
                  f"last {TAIL_LINES} lines ---")
            print(_tail(output))
        else:
            reason = "summary not parseable" if counts is None else "non-zero exit"
            print(f"--- worker {i} output ({reason}, exit={code}) ---")
            print(output)
    print("=" * 60)
    parts = [f"{total.get(k, 0)} {k}" for k in ("passed", "failed", "skipped", "errors")]
    print(f"TOTAL: {', '.join(parts)} in {wall:.0f}s ({len(shards)} workers)")
    code = overall_exit_code(results, partial)
    failed = states.count("FAILED")
    if failed:
        print(f"RESULT: FAILED ({failed} workers failed)")
    elif code:
        print("RESULT: FAILED (no test was selected in any worker)")
    else:
        print("RESULT: OK")
    return code


if __name__ == "__main__":
    sys.exit(main())
