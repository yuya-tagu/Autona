"""Smoke test of the real headless process: ``python main.py --headless``.

One process, started the way an operator starts it, in a throwaway working
directory on a free port (ADR-005 S3c; the first of S3d's smoke checks):

- it serves: /api/v1/health, the state over Socket.IO, a core write
  (score:adjust) reaching the state and the Socket.IO delta stream;
- the crew board's routes, on the board it seeds (ADR-005 S4w);
- the two shell operations, the desktop's alone (ADR-005 S3e-9: the set
  selection window and the thumbnail), answer 503 UNAVAILABLE; the Top 8
  import, core's, is refused only for want of a tournament;
- a write relayed from elsewhere (a forwarded request) needs the token, over
  HTTP and Socket.IO alike, and goes in with it;
- a second launch is refused (exit 1, a message on stderr) instead of
  starting a second server;
- Ctrl+Break stops it cleanly (exit 0), with user_data/state.json and
  out/program_state.json written.

AUTONA_INSTANCE_TAG gives the process its own single-instance names, so an
Autona the developer has open is neither a "second launch" for it nor
activated by it. The process is stopped by its PID only, whatever happens.

_launch() and _wait_until_serving() also start the headless app for the
Playwright E2E (web/e2e/headless_server.py, ADR-005 S3d).
"""
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from tests.test_bootstrap_split import _free_port, _runtime

PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="stops the process with CTRL_BREAK_EVENT (Windows consoles)")


def _env(tag):
    env = dict(os.environ)
    env.update({
        "AUTONA_INSTANCE_TAG": tag,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _launch(cwd, tag, console):
    # The console output goes to a file, not a pipe: nothing reads a pipe
    # while the process runs, and a full one would block its logging.
    return subprocess.Popen(
        [sys.executable, "-B", str(PROJECT_ROOT / "main.py"), "--headless"],
        cwd=str(cwd), env=_env(tag), stdout=console,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def _request(port, path, method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def _wait_until_serving(process, port, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if _request(port, "/api/v1/health")[0] == 200:
                return True
        # HTTPException: before anything listens, a connection to a free port
        # can be made to itself (TCP's simultaneous open, when the ephemeral
        # source port it is given is the port itself), and reads back its
        # own request as the answer (BadStatusLine: "GET /api/v1/health
        # HTTP/1.1"). Not serving yet, as a refused connection is.
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    return False


def _output(process, console_path):
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    text = Path(console_path).read_text(encoding="utf-8", errors="replace")
    return f"exit {process.poll()}\nconsole:\n{text[-4000:]}"


def test_main_headless_serves_refuses_a_second_launch_and_stops(tmp_path):
    port = _free_port()
    tag = f"pytest-{uuid.uuid4().hex}"
    with _runtime(tmp_path) as cwd:
        (cwd / "user_data" / "settings.json").write_text(json.dumps({
            "general": {"webserver_port": port,
                        "webserver_listen_lan": False}}), encoding="utf-8")
        console_path = tmp_path / "console.txt"
        console = open(console_path, "w", encoding="utf-8")
        process = _launch(cwd, tag, console)
        try:
            assert _wait_until_serving(process, port), (
                _output(process, console_path))

            # A second launch: refused, not a second server.
            second = subprocess.run(
                [sys.executable, "-B", str(PROJECT_ROOT / "main.py"),
                 "--headless"],
                cwd=str(cwd), env=_env(tag), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            assert second.returncode == 1, second
            assert "already running" in second.stderr

            # The state over Socket.IO (polling: no websocket client
            # installed), and a core write's delta on it.
            import socketio
            received = []
            client = socketio.Client(reconnection=False)
            client.on("program_state",
                      lambda data: received.append(("program_state", data)))
            client.on("program_state_update",
                      lambda data: received.append(("update", data)))
            client.connect(f"http://127.0.0.1:{port}",
                           transports=["polling"], wait_timeout=10)
            try:
                deadline = time.monotonic() + 15
                while (time.monotonic() < deadline and not any(
                        name == "program_state" for name, _ in received)):
                    time.sleep(0.1)
                assert any(name == "program_state" for name, _ in received)

                status, body = _request(
                    port, "/api/v1/scoreboards/1/teams/1/score:adjust",
                    "POST", {"delta": 2})
                assert (status, body["ok"]) == (200, True), body

                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not any(
                        name == "update" and "score.1.team.1.score" in
                        json.dumps(data) for name, data in received):
                    time.sleep(0.1)
            finally:
                client.disconnect()
            updates = [json.dumps(data) for name, data in received
                       if name == "update"]
            assert any("score.1.team.1.score" in update
                       or ('"score"' in update and '"team"' in update)
                       for update in updates), received[-3:]

            # Published at once: there is no board to hold it.
            status, body = _request(port, "/api/v1/state")
            assert body["data"]["score"]["1"]["team"]["1"]["score"] == 2

            # The crew board's routes need no board either (ADR-005 S4w), and
            # the combos' candidates are there without the combos.
            status, body = _request(
                port, "/api/v1/scoreboards/3/teams/1/members/1", "PATCH",
                {"name": "Crew One", "stock": 2, "active": True})
            assert (status, body["ok"]) == (200, True), body
            status, body = _request(
                port, "/api/v1/scoreboards/3:swap-teams", "POST", {})
            assert (status, body["ok"]) == (200, True), body
            status, body = _request(port, "/api/v1/state")
            crew = body["data"]["score"]["3"]
            assert crew["teamsSwapped"] is True
            assert crew["team"]["2"]["player"]["1"]["name"] == "Crew One"
            assert crew["team"]["2"]["player"]["1"]["stock"] == 2
            assert crew["team"]["2"]["activeMember"] == "1"
            assert crew["team"]["2"]["score"] == 2
            status, body = _request(port, "/api/v1/scoreboards/3/post")
            assert "Crew One" in body["data"]["text"]
            status, body = _request(port, "/api/v1/match-names/candidates")
            assert all(body["data"][field] for field in (
                "phase", "pool", "match")), body

            # The Top 8 import is core's (ADR-005 S3e-8): headless it is
            # refused only for want of a tournament.
            status, body = _request(port, "/api/v1/bracket:refresh", "POST",
                                    {})
            assert (status, body["error"]["code"]) == (409, "NOT_READY")
            # The shell operations (ADR-005 S3e-9): the only 503s left.
            for path in ("/scoreboard1-open-set",
                         "/scoreboard1-get-thumbnail-png"):
                status, body = _request(port, path)
                assert (status, body["error"]["code"]) == (
                    503, "UNAVAILABLE"), path

            # A relayed write is another computer's: the token is required.
            # (A real LAN client cannot be made from this one PC: its own LAN
            # address counts as this computer. The address rules themselves
            # are tests/test_web_access.py's, through the Flask test client.)
            relayed = {"X-Forwarded-For": "192.168.1.20"}
            status, body = _request(
                port, "/api/v1/scoreboards/1/teams/1/score:adjust", "POST",
                {"delta": 1}, headers=relayed)
            assert (status, body["error"]["code"]) == (401, "UNAUTHENTICATED")
            # The same over Socket.IO: the connection is let in to read only.
            relayedClient = socketio.Client(reconnection=False)
            relayedClient.connect(f"http://127.0.0.1:{port}", headers=relayed,
                                  transports=["polling"], wait_timeout=10)
            try:
                ack = relayedClient.call(
                    "team_scoreup",
                    json.dumps({"scoreboardNumber": "1", "team": "1"}),
                    timeout=10)
            finally:
                relayedClient.disconnect()
            assert ack["error"]["code"] == "UNAUTHENTICATED", ack
            status, body = _request(port, "/api/v1/state")
            assert body["data"]["score"]["1"]["team"]["1"]["score"] == 2
            # With the token issued at the first launch, it goes in.
            token = json.loads((cwd / "user_data" / "settings.json").read_text(
                encoding="utf-8"))["general"]["webserver_token"]
            status, body = _request(
                port, "/api/v1/scoreboards/1/teams/1/score:adjust", "POST",
                {"delta": 1}, headers={**relayed, "X-Autona-Token": token})
            assert (status, body["ok"]) == (200, True), body
            status, body = _request(port, "/api/v1/state")
            assert body["data"]["score"]["1"]["team"]["1"]["score"] == 3

            # Ctrl+Break: a clean stop that writes everything first.
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pytest.fail("the headless process did not stop on Ctrl+Break")
            assert process.returncode == 0, _output(process, console_path)
        finally:
            if process.poll() is None:
                # By its PID only (TerminateProcess on this one process).
                process.kill()
                process.wait(timeout=30)
            console.close()

        assert process.poll() is not None
        saved = json.loads((cwd / "user_data" / "state.json").read_text(
            encoding="utf-8"))
        assert saved["state"]["score"]["1"]["team"]["1"]["score"] == 3
        exported = json.loads((cwd / "out" / "program_state.json").read_text(
            encoding="utf-8"))
        assert exported["score"]["1"]["team"]["1"]["score"] == 3


def test_waiting_reads_a_connection_answered_by_itself_as_not_serving(
        monkeypatch):
    """What a connection made to itself reads back -- its own request, so no
    status line -- is not the server yet: the wait goes on, as it does for
    a refused connection (the E2E's start-up waits the same way)."""
    answers = [http.client.BadStatusLine("GET /api/v1/health HTTP/1.1"),
               ConnectionRefusedError(), (200, {"ok": True})]

    def request(port, path, **_kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    class Running:
        def poll(self):
            return None

    monkeypatch.setattr(sys.modules[__name__], "_request", request)
    assert _wait_until_serving(Running(), port=1, timeout=30) is True
    assert answers == []
