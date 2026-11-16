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
[--download-dir DIR] [--no-cut] [--thumbs] [--progress json]
[--cancel-on-stdin]``
    What it does depends on ``FAKE_VODSPLIT_MODE``:

    ``ok`` (the default)
        Prints a ``stage`` and ``progress`` line for each of vodsplit's
        stages, a ``log`` and a ``warning`` line, writes ``DIR/sets.json``
        (the shape of vodsplit ``export.save()``: two sets, the second one
        needing review, with ``games_ocr``; with ``--results``, the sets
        take its players and scores) and prints ``done``; exit 0. A URL
        source with ``--download-dir`` is "downloaded" as vodsplit names it
        (``<dir>/fakevideo01.open.mp4``, VIDEO_BYTES), and sets.json's
        ``source`` is that file, as vodsplit's is. With ``--thumbs``, as
        vodsplit's export.py: sets.json is written first without frames,
        then the ``export`` stage (``書き出し``, the 5th of 5) prints a
        progress line per set as it writes its background frame
        (``DIR/NN_fake_bg.jpg``, FRAME_BYTES), and sets.json is written again
        with each set's ``thumb`` / ``thumb_time``.
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

``export <edits.json> --out DIR [--progress json] [--cancel-on-stdin]``
    vodsplit's ``export`` (ADR-006 A4), by ``FAKE_VODSPLIT_MODE``:

    ``ok``
        Reads the request (vodsplit export.validate_edits' shape). A source
        that is not a file is ``{"type": "error", "code": "source"}`` and a
        DIR with a sets.json ``{"type": "error", "code": "edits"}`` (exit
        2), as vodsplit's. Else prints the ``export`` stage (``書き出し``,
        1 of 1) and, per set (``FAKE_VODSPLIT_DELAY`` apart: a cancel ends
        the wait), writes ``DIR/NN_<title>.mp4`` (CLIP_BYTES)
        and ``NN_<title>.txt`` -- the title made as the template would
        (``<event> <round> <p1> vs <p2>``, spaces as ``_`` in the file
        name), ``NN_unmatched.mp4`` with no text for a set with no result
        -- and a progress line, rewriting ``DIR/export.json`` after each
        (vodsplit's shape: ``clips`` of ``no / start / end / title /
        description / file / text / exported / reencode / skipped``). A
        clip the folder's export.json says was cut from the same source and
        range is skipped (``skipped``). Then ``{"type": "done", "out_dir",
        "export_json", "clips", "skipped"}``; exit 0.
    ``error``
        Prints the stage, then ``{"type": "error", "code": "source"}``;
        exit 2.
    ``locked``
        Prints the stage, then vodsplit's internal error for a clip it
        cannot replace (``{"type": "error", "code": "internal", "message":
        "PermissionError: ..."}``: the earlier clip open in a player); exit
        1.
    ``crash`` / ``slow`` / ``hang``
        As ``run``'s.

    It writes ``FAKE_VODSPLIT_ARGV`` as ``run`` does (the arguments after
    ``export``).

The delay between lines is ``FAKE_VODSPLIT_DELAY`` seconds (0 by default;
``slow`` uses at least 0.05).
"""
import json
import os
import sys
import threading
import time

# What the fake writes for the downloaded video and a set's frame: bytes,
# not media (the tests read them back).
VIDEO_BYTES = bytes(range(256)) * 64
FRAME_BYTES = b"\xff\xd8\xff\xe0fake frame\xff\xd9"
CLIP_BYTES = b"fake clip"

STAGES = [
    ("download", "ダウンロード"),
    ("decode", "映像を解析（試合中の判定）"),
    ("ocr", "スコアボードを読み取り（OCR）"),
    ("segment", "セットに分割"),
    ("export", "書き出し"),
]
# vodsplit's export stage runs only when there is something to write (the
# frames: --thumbs; the cuts: not with --no-cut).
EXPORT = len(STAGES) - 1


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def stage(index):
    key, label = STAGES[index]
    emit({"type": "stage", "stage": key, "label": label, "index": index + 1,
          "count": len(STAGES)})


def option(argv, name, default=None):
    """`name`'s value, as argparse reads it: ``--name VALUE`` or
    ``--name=VALUE``."""
    for position, word in enumerate(argv):
        if word.startswith(name + "="):
            return word[len(name) + 1:]
        if word == name and position + 1 < len(argv):
            return argv[position + 1]
    return default


def sets_json(source, results, event, thumbs=False):
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
        if thumbs:
            rows[-1]["thumb_time"] = start + 30.0
            rows[-1]["thumb"] = f"{number:02d}_fake_bg.jpg"
        start = end + 60.0
    return {
        "version": "0.0.0-fake", "format": 1, "source": source,
        "source_id": "fakevideo01", "event": event or "Fake Cup",
        "template": "default", "duration": 3600.0, "video": None,
        "scoreboard": None,
        "games": [game for row in rows for game in row["game_spans"]],
        "games_ocr": [{"samples": 20, "names": {
            row["set"]["p1"]: 1.0, row["set"]["p2"]: 0.5}} for row in rows],
        "warnings": ["fake warning"], "unused_sets": [], "sets": rows,
    }


def download(source, folder):
    """vodsplit's download: the file it would write for a URL (VP9 offered:
    ``<id>.open.mp4``), or the source itself for a file."""
    if not source.startswith(("http://", "https://")) or not folder:
        return source
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "fakevideo01.open.mp4")
    with open(path, "wb") as file:
        file.write(VIDEO_BYTES)
    return path


def run(argv):
    mode = os.environ.get("FAKE_VODSPLIT_MODE", "ok")
    delay = float(os.environ.get("FAKE_VODSPLIT_DELAY", "0") or 0)
    cancel = _start(argv, mode)
    source = argv[0] if argv else ""
    out = option(argv, "--out")
    results_path = option(argv, "--results")
    event = option(argv, "--event")

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
    for index in range(EXPORT):
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
    video = download(source, option(argv, "--download-dir"))

    def save(data):
        with open(os.path.join(out, "sets.json"), "w",
                  encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=1)

    data = sets_json(video, results, event)
    save(data)
    if "--thumbs" in argv:
        stage(EXPORT)
        data = sets_json(video, results, event, thumbs=True)
        steps = len(data["sets"])
        for done, row in enumerate(data["sets"], 1):
            time.sleep(delay)
            if cancel.is_set():
                emit({"type": "cancelled"})
                return 130
            with open(os.path.join(out, row["thumb"]), "wb") as file:
                file.write(FRAME_BYTES)
            overall = round(overall + 0.1 / steps, 4)
            emit({"type": "progress", "stage": "export", "done": done,
                  "total": steps, "fraction": done / steps,
                  "overall": overall, "eta": None})
        save(data)
    review = sum(1 for row in data["sets"] if row["needs_review"])
    emit({"type": "done", "out_dir": out,
          "sets_json": os.path.join(out, "sets.json"),
          "review": os.path.join(out, "review.md"),
          "sets": len(data["sets"]), "needs_review": review})
    return 0


def _start(argv, mode):
    """What run and export do first: write FAKE_VODSPLIT_ARGV, the two
    lines for worker.log, and watch stdin for a cancel -> the Event it
    sets."""
    record = os.environ.get("FAKE_VODSPLIT_ARGV")
    if record:
        with open(record, "w", encoding="utf-8") as file:
            json.dump(argv, file)
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
    return cancel


def export(argv):
    mode = os.environ.get("FAKE_VODSPLIT_MODE", "ok")
    delay = float(os.environ.get("FAKE_VODSPLIT_DELAY", "0") or 0)
    cancel = _start(argv, mode)
    out = option(argv, "--out")
    emit({"type": "stage", "stage": "export", "label": "書き出し",
          "index": 1, "count": 1})
    if mode == "crash":
        print("Traceback (most recent call last):\nRuntimeError: fake crash",
              file=sys.stderr, flush=True)
        return 1
    if mode == "locked":
        emit({"type": "error", "code": "internal",
              "message": "PermissionError: [WinError 32] プロセスはファイルに"
                         "アクセスできません。別のプロセスが使用中です。"})
        return 1
    if mode == "hang":
        while True:
            time.sleep(1)
    if mode == "slow":
        done = 0
        while not cancel.wait(max(delay, 0.05)):
            done += 1
            emit({"type": "progress", "stage": "export", "done": done,
                  "total": 1000, "fraction": min(1.0, done / 1000),
                  "overall": min(1.0, done / 1000), "eta": 3.0})
        emit({"type": "cancelled"})
        return 130
    with open(argv[0], encoding="utf-8") as file:
        request = json.load(file)
    source = request.get("source")
    if mode == "error" or not isinstance(source, str) \
            or not os.path.isfile(source):
        emit({"type": "error", "code": "source",
              "message": f"動画ファイルが見つかりません: {source}",
              "hint": "解析した動画が移動・削除されていないか確認してください。"})
        return 2
    if os.path.exists(os.path.join(out, "sets.json")):
        emit({"type": "error", "code": "edits",
              "message": f"書き出し先が run の出力フォルダです: {out}",
              "hint": None})
        return 2
    os.makedirs(out, exist_ok=True)
    listed = os.path.join(out, "export.json")
    previous = {}
    try:
        with open(listed, encoding="utf-8") as file:
            earlier = json.load(file)
        if earlier.get("source") == source:
            previous = {clip["no"]: clip for clip in earlier["clips"]}
    except (OSError, ValueError, KeyError, TypeError):
        previous = {}
    rows = []
    for item in request["sets"]:
        played = item.get("set")
        title = item.get("title")
        if title is None and played:
            title = (f"{request.get('event') or 'Fake Cup'} "
                     f"{played.get('round') or ''} {played['p1']} vs "
                     f"{played['p2']}")
        name = title.replace(" ", "_") if title else "unmatched"
        base = f"{item['no']:02d}_{name}"
        old = previous.get(item["no"], {})
        skipped = (old.get("file") == base + ".mp4"
                   and (old.get("start"), old.get("end"))
                   == (item["start"], item["end"])
                   and os.path.exists(os.path.join(out, base + ".mp4")))
        rows.append({"no": item["no"], "start": item["start"],
                     "end": item["end"], "title": title,
                     "description": item.get("description"),
                     "file": base + ".mp4",
                     "text": base + ".txt" if title else None,
                     "exported": skipped, "reencode": False,
                     "skipped": skipped})

    def save():
        mine = {row["no"] for row in rows}
        kept = [clip for no, clip in previous.items() if no not in mine]
        with open(listed, "w", encoding="utf-8") as file:
            json.dump({"version": "0.0.0-fake", "format": 1,
                       "source": source, "event": request.get("event"),
                       "template": "default",
                       "clips": sorted(kept + rows,
                                       key=lambda clip: clip["no"])},
                      file, ensure_ascii=False, indent=1)

    save()
    for done, row in enumerate(rows, 1):
        # The delay comes between the clips, and a cancel cuts it short: a
        # test cancels after the first clip, whatever the load.
        if cancel.is_set() or (done > 1 and cancel.wait(delay)):
            emit({"type": "cancelled"})
            return 130
        if not row["skipped"]:
            with open(os.path.join(out, row["file"]), "wb") as file:
                file.write(CLIP_BYTES)
            if row["text"]:
                with open(os.path.join(out, row["text"]), "w",
                          encoding="utf-8") as file:
                    file.write(row["title"] + "\n")
            row["exported"] = True
            save()
        emit({"type": "progress", "stage": "export", "done": done,
              "total": len(rows), "fraction": done / len(rows),
              "overall": done / len(rows), "eta": None})
    emit({"type": "done", "out_dir": out, "export_json": listed,
          "clips": len(rows),
          "skipped": sum(1 for row in rows if row["skipped"])})
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not argv:
        print("usage: fake_vodsplit_worker.py version | run ... | export ...",
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
    if argv[0] == "export":
        return export(argv[1:])
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
