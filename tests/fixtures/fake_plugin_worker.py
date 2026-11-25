"""A stand-in plugin worker that speaks Worker Protocol 1 (ADR-009 appendix
A; docs/plugins/contract_1.json) for the tests: the plugin engine's
(PluginRunner, PluginJobs), the SDK's fake host and tests/test_plugin_contract.py.
No real work.

Used as a dev plugin's entry (``["python", "<this file>"]``) or run
directly::

    <python> fake_plugin_worker.py describe
    <python> fake_plugin_worker.py run <request.json>

"Cannot be launched" is the process side (no exe, CreateProcess failing):
the fake cannot be that. Point the entry at a file that does not exist
instead (the engine's ``launch_failed`` / ``entry_missing``).

describe
--------
Prints one line ``{"type": "describe", "protocol": [1], "id", "version"}``
and exits 0. The id and version are read from ``plugin.json`` in
``AUTONA_PLUGIN_DIR`` (as the SDK does), else ``example.fake`` / ``1.0.0``.
``FAKE_PLUGIN_DESCRIBE`` changes it:

``ok`` (the default)
    As above.
``wrong_id`` / ``wrong_version``
    The id / version differs from the manifest's.
``no_protocol``
    No ``protocol`` key.
``protocol_2``
    ``"protocol": [2]`` (no protocol in common with the host).
``not_json``
    A line that is not JSON, exit 0.
``no_line``
    Prints nothing, exit 0.
``noisy``
    A line that is not JSON and a ``log`` event first, then the answer.
``exit_1``
    The answer, then exit 1.
``hang``
    Sleeps (past the host's 60 seconds).

run <request.json>
------------------
Reads the request (UTF-8) and writes into its ``out_dir`` (made by the
host; made here too when missing, for direct runs). The mode is the
request's ``params.mode`` when it is a string, else ``FAKE_PLUGIN_MODE``,
else ``ok``. Every mode first prints one line that is not JSON on stdout and
one line on stderr (both go to worker.log). ``FAKE_PLUGIN_DELAY`` (seconds)
is the pause of the modes that wait. stdin is watched on a thread: a
``cancel`` line or EOF sets the cancel (other lines are ignored), except in
``hang`` (it never reads stdin) and ``stdin_open``.

``ok``
    ``stage`` (2 of them, Japanese labels), ``progress``, ``log``,
    ``warning``, then ``done`` with a summary and three artifacts written
    to out_dir (``message.txt`` text, ``result.png`` image, ``data.bin``
    file); exit 0.
``error``
    A stage, then ``{"type": "error", "code": "unsupported_host",
    "message", "hint"}``; exit 2.
``crash``
    A stage, a traceback on stderr, exit 1 with no terminal line.
``slow``
    A stage, then a progress line every max(delay, 0.05) s until the
    cancel (a ``cancel`` line or EOF), then ``cancelled``; exit 130.
``hang``
    A stage, then sleeps forever and never reads stdin (only a kill ends
    it). With ``FAKE_PLUGIN_CHILD_PID`` (a path) it first starts a child
    that sleeps too (60 s: one a test fails to kill does not linger) and
    writes the child's pid there.
``grandchild``
    A stage, starts a child that sleeps (stdio not inherited) and writes
    its pid to ``FAKE_PLUGIN_CHILD_PID`` (else ``out_dir/child.pid``),
    then ``done``; exit 0 while the child runs (the job object must end
    it).
``done_after_cancel``
    A stage, waits for the cancel, then (a send it cannot take back)
    ``done``; exit 0.
``timeout``
    A stage, a progress line every 0.05 s for ``FAKE_PLUGIN_DELAY``
    seconds (5 by default), then ``done``; exit 0. A cancel before that
    ends it with ``cancelled``; exit 130. With a time limit under the
    delay, a host marks it timed_out (failed whatever the end).
``combo``
    A stage, then the terminal line ``FAKE_PLUGIN_FINAL`` names
    (``done`` / ``error`` / ``cancelled`` / ``none``) and exit code
    ``FAKE_PLUGIN_EXIT`` (an integer): every row of the end table.
``noise``
    Lines the host must send to worker.log: an unknown ``type``, a line
    without ``type``, JSON that is not an object, a line over 1 MiB, an
    empty line, invalid bytes; a ``log`` line with an ``id`` key (the key
    is ignored, the line taken); and events with unknown keys and invalid
    fields (the fields dropped, the events taken; raw keeps the unknown
    keys): then ``done``; exit 0.
``limits``
    250 warnings, a summary of 600 characters, a 3000-character log
    message and 60 text artifacts (all written): the host keeps 200,
    truncates to 500 / 2000 and keeps 50; exit 0.
``bad_code``
    ``{"type": "error", "code": "Bad-Code!"}`` (not the pattern: the
    host makes it ``error``); exit 2.
``artifacts``
    ``done`` with good artifacts (text, png, jpg, webp images, a file, a
    json) and bad ones the host drops: outside out_dir (``../outside.txt``
    and an absolute path), missing, a hard link, a symbolic link (when the
    OS lets it be made), an image with another extension (``.bmp``), a
    text over 64 KiB, an unknown kind (``html``), an artifact without
    ``path``; exit 0.
``stdin_open``
    Watches stdin for ``FAKE_PLUGIN_DELAY`` seconds (0.3 by default):
    ``done`` (exit 0) when it stayed open, ``error`` code
    ``stdin_closed`` (exit 2) when it closed, ``cancelled`` (exit 130)
    when a ``cancel`` line came.
``echo``
    Writes ``out_dir/echo.json``: ``{"argv", "cwd", "env", "request"}``
    (the whole environment: the host's subtraction is checked on it),
    then ``done`` with it as a json artifact; exit 0.
"""
import json
import os
import subprocess
import sys
import threading
import time

PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
             b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\r"
             b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
             b"\x00\x00IEND\xaeB`\x82")
JPG_BYTES = b"\xff\xd8\xff\xe0fake jpg\xff\xd9"
WEBP_BYTES = b"RIFF\x0c\x00\x00\x00WEBPVP8 fake"
DEFAULT_ID = "example.fake"
DEFAULT_VERSION = "1.0.0"
LINE_LIMIT = 1024 * 1024


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def raw(text):
    print(text, flush=True)


def delay(default=0.0):
    try:
        return float(os.environ.get("FAKE_PLUGIN_DELAY", "") or default)
    except ValueError:
        return default


def manifest_identity():
    folder = os.environ.get("AUTONA_PLUGIN_DIR")
    if folder:
        try:
            with open(os.path.join(folder, "plugin.json"),
                      encoding="utf-8-sig") as file:
                manifest = json.load(file)
            return (str(manifest.get("id") or DEFAULT_ID),
                    str(manifest.get("version") or DEFAULT_VERSION))
        except (OSError, ValueError, AttributeError):
            pass
    return DEFAULT_ID, DEFAULT_VERSION


def describe():
    mode = os.environ.get("FAKE_PLUGIN_DESCRIBE", "ok")
    plugin_id, version = manifest_identity()
    answer = {"type": "describe", "protocol": [1], "id": plugin_id,
              "version": version}
    if mode == "hang":
        while True:
            time.sleep(1)
    if mode == "no_line":
        return 0
    if mode == "not_json":
        raw("describe: not a JSON line")
        return 0
    if mode == "wrong_id":
        answer["id"] = "other." + plugin_id.split(".", 1)[-1]
    elif mode == "wrong_version":
        answer["version"] = "9.9.9"
    elif mode == "no_protocol":
        del answer["protocol"]
    elif mode == "protocol_2":
        answer["protocol"] = [2]
    elif mode == "noisy":
        raw("describe: loading (not JSON)")
        emit({"type": "log", "message": "starting"})
    emit(answer)
    return 1 if mode == "exit_1" else 0


def watch_stdin():
    """The cancel: set by a `cancel` line or EOF; other lines ignored."""
    cancel = threading.Event()

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


def stage(index, count=2):
    labels = [("prepare", {"ja": "準備", "en": "Prepare"}),
              ("send", {"ja": "送信", "en": "Send"})]
    key, label = labels[min(index, len(labels) - 1)]
    emit({"type": "stage", "stage": key, "label": label, "index": index + 1,
          "count": count})


def write(out_dir, name, data):
    path = os.path.join(out_dir, name)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as file:
        file.write(data)
    return path


def sleeper():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def run(argv):
    if not argv:
        print("usage: fake_plugin_worker.py run <request.json>",
              file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as file:
        request = json.load(file)
    params = request.get("params") or {}
    mode = params.get("mode") if isinstance(params.get("mode"), str) \
        else os.environ.get("FAKE_PLUGIN_MODE", "ok")
    out_dir = request.get("out_dir") or os.path.join(os.getcwd(), "out")
    os.makedirs(out_dir, exist_ok=True)
    raw("fake plugin: starting (not a JSON line)")
    print("fake plugin: stderr line", file=sys.stderr, flush=True)

    if mode == "hang":
        child = os.environ.get("FAKE_PLUGIN_CHILD_PID")
        if child:
            with open(child, "w", encoding="utf-8") as file:
                file.write(str(sleeper().pid))
        stage(0)
        while True:
            time.sleep(1)

    if mode == "stdin_open":
        return stdin_open()

    cancel = watch_stdin()
    stage(0)

    if mode == "ok":
        emit({"type": "progress", "stage": "prepare", "fraction": 0.5,
              "overall": 0.25, "eta": 1.5})
        emit({"type": "log", "message": {"ja": "準備中", "en": "Preparing"}})
        emit({"type": "warning", "message": "画像が大きいので縮小しました"})
        stage(1)
        emit({"type": "progress", "stage": "send", "fraction": 1.0,
              "overall": 1.0, "eta": None})
        write(out_dir, "message.txt", "TOP8 が決まりました\n")
        write(out_dir, "result.png", PNG_BYTES)
        write(out_dir, "data.bin", b"\x00\x01\x02")
        emit({"type": "done",
              "summary": {"ja": "Webhook に送った", "en": "Posted"},
              "artifacts": [
                  {"path": "message.txt", "kind": "text",
                   "label": {"ja": "送った本文", "en": "Message"}},
                  {"path": "result.png", "kind": "image"},
                  {"path": "data.bin", "kind": "file", "label": "data"}]})
        return 0
    if mode == "error":
        emit({"type": "error", "code": "unsupported_host",
              "message": {"ja": "この URL には送れません",
                          "en": "Cannot post to this URL"},
              "hint": {"ja": "Discord か Slack の URL を入れてください",
                       "en": "Use a Discord or Slack URL"}})
        return 2
    if mode == "crash":
        print("Traceback (most recent call last):\nRuntimeError: fake crash",
              file=sys.stderr, flush=True)
        return 1
    if mode == "slow":
        step = max(delay(), 0.05)
        done = 0
        while not cancel.wait(step):
            done += 1
            emit({"type": "progress", "stage": "prepare",
                  "fraction": min(1.0, done / 1000),
                  "overall": min(1.0, done / 1000), "eta": 3.0})
        emit({"type": "cancelled"})
        return 130
    if mode == "grandchild":
        child = os.environ.get("FAKE_PLUGIN_CHILD_PID") \
            or os.path.join(out_dir, "child.pid")
        with open(child, "w", encoding="utf-8") as file:
            file.write(str(sleeper().pid))
        emit({"type": "done", "summary": "started a child"})
        return 0
    if mode == "done_after_cancel":
        cancel.wait()
        emit({"type": "log", "message": "already sending: finishing"})
        emit({"type": "done", "summary": "sent before the cancel"})
        return 0
    if mode == "timeout":
        end = time.monotonic() + delay(5.0)
        while time.monotonic() < end:
            if cancel.wait(0.05):
                emit({"type": "cancelled"})
                return 130
            emit({"type": "progress", "stage": "prepare", "overall": 0.5})
        emit({"type": "done", "summary": "finished late"})
        return 0
    if mode == "combo":
        final = os.environ.get("FAKE_PLUGIN_FINAL", "done")
        if final == "done":
            emit({"type": "done", "summary": "combo"})
        elif final == "error":
            emit({"type": "error", "code": "combo", "message": "combo"})
        elif final == "cancelled":
            emit({"type": "cancelled"})
        return int(os.environ.get("FAKE_PLUGIN_EXIT", "0"))
    if mode == "noise":
        return noise()
    if mode == "limits":
        return limits(out_dir)
    if mode == "bad_code":
        emit({"type": "error", "code": "Bad-Code!", "message": "bad code"})
        return 2
    if mode == "artifacts":
        return artifacts(out_dir)
    if mode == "echo":
        echo = {"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ),
                "request": request}
        write(out_dir, "echo.json", json.dumps(echo, ensure_ascii=False))
        emit({"type": "done", "summary": "echo",
              "artifacts": [{"path": "echo.json", "kind": "json"}]})
        return 0
    emit({"type": "error", "code": "unknown_mode",
          "message": f"unknown mode: {mode}"})
    return 2


def stdin_open():
    state = {"line": None}
    ended = threading.Event()

    def watch():
        try:
            state["line"] = sys.stdin.readline()
        except (OSError, ValueError):
            state["line"] = ""
        ended.set()

    threading.Thread(target=watch, daemon=True).start()
    stage(0)
    if not ended.wait(delay(0.3)):
        emit({"type": "done", "summary": "stdin stayed open"})
        return 0
    if (state["line"] or "").strip() == "cancel":
        emit({"type": "cancelled"})
        return 130
    emit({"type": "error", "code": "stdin_closed",
          "message": "stdin closed before the end"})
    return 2


def noise():
    emit({"type": "telemetry", "value": 1})
    emit({"message": "no type"})
    raw("[1, 2, 3]")
    raw('"a string"')
    emit({"type": "log", "message": "reserved", "id": 7})
    emit({"type": "log", "message": "x" * (LINE_LIMIT + 10)})
    raw("")
    sys.stdout.flush()
    sys.stdout.buffer.write(b"\xff\xfe not utf-8\n")
    sys.stdout.buffer.flush()
    emit({"type": "progress", "fraction": 7, "overall": "half", "eta": -1,
          "extra": True})
    emit({"type": "stage", "stage": "send", "index": "two", "colour": "red"})
    emit({"type": "warning", "message": "kept", "future_key": [1]})
    emit({"type": "done", "summary": "after noise", "unknown": {"a": 1}})
    return 0


def limits(out_dir):
    for number in range(250):
        emit({"type": "warning", "message": f"warning {number}"})
    emit({"type": "log", "message": "m" * 3000})
    entries = []
    for number in range(60):
        name = f"a{number:02d}.txt"
        write(out_dir, name, f"{number}\n")
        entries.append({"path": name, "kind": "text"})
    emit({"type": "done", "summary": "s" * 600, "artifacts": entries})
    return 0


def artifacts(out_dir):
    good = [
        ("note.txt", "text", "note\n"),
        ("picture.png", "image", PNG_BYTES),
        ("photo.jpg", "image", JPG_BYTES),
        ("image.webp", "image", WEBP_BYTES),
        ("archive.zip", "file", b"PK\x05\x06" + b"\x00" * 18),
        ("result.json", "json", '{"ok": true}\n'),
    ]
    entries = []
    for name, kind, data in good:
        write(out_dir, name, data)
        entries.append({"path": name, "kind": kind, "label": name})
    parent = os.path.dirname(os.path.abspath(out_dir))
    write(parent, "outside.txt", "outside\n")
    bad = [{"path": "../outside.txt", "kind": "text"},
           {"path": os.path.join(parent, "outside.txt"), "kind": "text"},
           {"path": "missing.txt", "kind": "text"}]
    linked = write(out_dir, "original.txt", "original\n")
    try:
        os.link(linked, os.path.join(out_dir, "hardlink.txt"))
        bad.append({"path": "hardlink.txt", "kind": "text"})
    except OSError:
        pass
    try:
        os.symlink(linked, os.path.join(out_dir, "symlink.txt"))
        bad.append({"path": "symlink.txt", "kind": "text"})
    except (OSError, NotImplementedError):
        pass
    write(out_dir, "picture.bmp", b"BM fake")
    bad.append({"path": "picture.bmp", "kind": "image"})
    write(out_dir, "big.txt", "b" * (64 * 1024 + 1))
    bad.append({"path": "big.txt", "kind": "text"})
    write(out_dir, "page.html", "<p>hi</p>")
    bad.append({"path": "page.html", "kind": "html"})
    bad.append({"kind": "text"})
    emit({"type": "done", "summary": "artifacts", "artifacts": entries + bad})
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not argv:
        print("usage: fake_plugin_worker.py describe | run <request.json>",
              file=sys.stderr)
        return 2
    if argv[0] == "describe":
        return describe()
    if argv[0] == "run":
        return run(argv[1:])
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
