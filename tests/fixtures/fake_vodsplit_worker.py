"""A stand-in for the archive clipper's worker (vodsplit, ADR-006 D1) for the
tests: the same CLI and JSON Lines as vodsplit's ``cli.py``, no video work.

Used by tests/test_archive_jobs.py and meant for the web E2E too: point
``AUTONA_VODSPLIT_CMD`` at it::

    AUTONA_VODSPLIT_CMD="<python> <this file>"   (quote paths with spaces)

The CLI
-------
``version``
    Prints ``{"version": "0.0.0-fake", "format": 1}`` and exits 0.
    ``FAKE_VODSPLIT_FORMAT`` changes the format number (an unsupported
    worker); ``FAKE_VODSPLIT_VERSION_EXIT`` makes it exit with that code
    instead (a broken worker).

``run <source> --out DIR [--results FILE] [--event NAME | --event=NAME]
[--download-dir DIR] [--no-cut] [--progress json] [--cancel-on-stdin]``
    What it does depends on ``FAKE_VODSPLIT_MODE``:

    ``ok`` (the default)
        Prints a ``stage`` and ``progress`` line for each of vodsplit's
        stages, a ``log`` and a ``warning`` line, writes ``DIR/sets.json``
        (the shape of vodsplit ``export.save()``: two sets, the second one
        needing review; with ``--results``, the sets take its players and
        scores) and prints ``done``; exit 0.
    ``error``
        Prints a stage, then ``{"type": "error", "code": "download",
        "message", "hint"}``; exit 2 (an error the operator can fix).
    ``crash``
        Prints a stage, writes a traceback-like line to stderr and exits 1
        with no final line.
    ``slow``
        Prints a stage, then a progress line every 0.05 s until stdin says
        ``cancel`` or closes (``--cancel-on-stdin``), then ``cancelled``;
        exit 130.
    ``hang``
        Prints a stage, then sleeps forever and never reads stdin (a worker
        that does not answer a cancel: only a kill ends it). With
        ``FAKE_VODSPLIT_CHILD_PID`` (a path) it first starts a child process
        that sleeps too (as vodsplit starts yt-dlp / ffmpeg) and writes the
        child's pid there.
    ``orphan``
        Prints a stage, starts a child process that sleeps and keeps this
        worker's stderr (inherited: a grandchild of Autona holding its
        pipe), writes the child's pid to ``FAKE_VODSPLIT_CHILD_PID``, prints
        ``done`` (no sets) and exits 0 while the child still runs.

    Every mode writes ``FAKE_VODSPLIT_ARGV`` (a path, when set) with the
    arguments it was given as JSON, and prints one line that is not JSON on
    stdout and one on stderr (they go to worker.log).

The delay between lines is ``FAKE_VODSPLIT_DELAY`` seconds (0 by default;
``slow`` uses at least 0.05).
"""
import json
import os
import sys
import threading
import time

STAGES = [
    ("download", "ダウンロード"),
    ("decode", "映像を解析（試合中の判定）"),
    ("ocr", "スコアボードを読み取り（OCR）"),
    ("segment", "セットに分割"),
]


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def stage(index):
    key, label = STAGES[index]
    emit({"type": "stage", "stage": key, "label": label, "index": index + 1,
          "count": len(STAGES) + 1})


def option(argv, name, default=None):
    """`name`'s value, as argparse reads it: ``--name VALUE`` or
    ``--name=VALUE``."""
    for position, word in enumerate(argv):
        if word.startswith(name + "="):
            return word[len(name) + 1:]
        if word == name and position + 1 < len(argv):
            return argv[position + 1]
    return default


def sets_json(source, results, event):
    played = (results or {}).get("sets") or [
        {"round": "Winners Round 1", "p1": "Alpha", "s1": 2, "p2": "Beta",
         "s2": 1},
        {"round": "Grand Final", "p1": "Gamma", "s1": 3, "p2": "Delta",
         "s2": 2},
    ]
    rows = []
    start = 120.0
    for number, played_set in enumerate(played[:2], 1):
        end = start + 600.0
        review = number == 2
        rows.append({
            "no": number, "start": start, "end": end,
            "games": played_set["s1"] + played_set["s2"],
            "matched": not review,
            "title": f"{event or 'Fake Cup'} {played_set.get('round', '')} "
                     f"{played_set['p1']} vs {played_set['p2']}",
            "description": "",
            "set": dict(played_set),
            "labels": [played_set["p1"], played_set["p2"]],
            "game_starts": [start + 10.0],
            "game_spans": [[start + 10.0, end - 10.0]],
            "needs_review": review,
            "reason": "名前の読み取りが合いません" if review else "",
        })
        start = end + 60.0
    return {
        "version": "0.0.0-fake", "format": 1, "source": source,
        "source_id": "fakevideo01", "event": event or "Fake Cup",
        "template": "default", "duration": 3600.0, "video": None,
        "scoreboard": None,
        "games": [game for row in rows for game in row["game_spans"]],
        "warnings": ["fake warning"], "unused_sets": [], "sets": rows,
    }


def run(argv):
    mode = os.environ.get("FAKE_VODSPLIT_MODE", "ok")
    delay = float(os.environ.get("FAKE_VODSPLIT_DELAY", "0") or 0)
    record = os.environ.get("FAKE_VODSPLIT_ARGV")
    if record:
        with open(record, "w", encoding="utf-8") as file:
            json.dump(argv, file)
    source = argv[0] if argv else ""
    out = option(argv, "--out")
    results_path = option(argv, "--results")
    event = option(argv, "--event")
    print("fake worker: starting (not a JSON line)", flush=True)
    print("fake worker: stderr line", file=sys.stderr, flush=True)

    cancel = threading.Event()
    if "--cancel-on-stdin" in argv and mode != "hang":
        def watch():
            try:
                for line in sys.stdin:
                    if line.strip() == "cancel":
                        break
            except (OSError, ValueError):
                pass
            cancel.set()
        threading.Thread(target=watch, daemon=True).start()

    stage(0)
    if mode == "error":
        emit({"type": "error", "code": "download",
              "message": "動画をダウンロードできません。",
              "hint": "URL を確かめてください。"})
        return 2
    if mode == "crash":
        print("Traceback (most recent call last):\nRuntimeError: fake crash",
              file=sys.stderr, flush=True)
        return 1
    if mode == "hang":
        child = os.environ.get("FAKE_VODSPLIT_CHILD_PID")
        if child:
            import subprocess
            grandchild = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            with open(child, "w", encoding="utf-8") as file:
                file.write(str(grandchild.pid))
        stage(1)
        while True:
            time.sleep(1)
    if mode == "orphan":
        import subprocess
        grandchild = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        with open(os.environ["FAKE_VODSPLIT_CHILD_PID"], "w",
                  encoding="utf-8") as file:
            file.write(str(grandchild.pid))
        emit({"type": "done", "sets": 0, "needs_review": 0})
        return 0
    if mode == "slow":
        done = 0
        while not cancel.wait(max(delay, 0.05)):
            done += 1
            emit({"type": "progress", "stage": "download", "done": done,
                  "total": 1000, "fraction": min(1.0, done / 1000),
                  "overall": 0.0, "eta": 12.5})
        emit({"type": "cancelled"})
        return 130

    results = None
    if results_path:
        with open(results_path, encoding="utf-8") as file:
            results = json.load(file)
    emit({"type": "log", "message": "fake log line"})
    emit({"type": "warning", "message": "fake warning"})
    overall = 0.0
    for index in range(len(STAGES)):
        if index:
            stage(index)
        for step in (1, 2):
            time.sleep(delay)
            if cancel.is_set():
                emit({"type": "cancelled"})
                return 130
            overall = round(overall + 0.1, 4)
            emit({"type": "progress", "stage": STAGES[index][0],
                  "done": step, "total": 2, "fraction": step / 2,
                  "overall": overall, "eta": 1.0 if step == 1 else None})
    os.makedirs(out, exist_ok=True)
    data = sets_json(source, results, event)
    with open(os.path.join(out, "sets.json"), "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=1)
    review = sum(1 for row in data["sets"] if row["needs_review"])
    emit({"type": "done", "out_dir": out,
          "sets_json": os.path.join(out, "sets.json"),
          "review": os.path.join(out, "review.md"),
          "sets": len(data["sets"]), "needs_review": review})
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not argv:
        print("usage: fake_vodsplit_worker.py version | run ...",
              file=sys.stderr)
        return 2
    if argv[0] == "version":
        code = os.environ.get("FAKE_VODSPLIT_VERSION_EXIT")
        if code:
            return int(code)
        fmt = int(os.environ.get("FAKE_VODSPLIT_FORMAT", "1"))
        print(json.dumps({"version": "0.0.0-fake", "format": fmt}),
              flush=True)
        return 0
    if argv[0] == "run":
        return run(argv[1:])
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
