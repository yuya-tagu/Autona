"""The Stream page's /api/v1 routes (ADR-005 S4e).

What the React Stream page (web/src/StreamPage) writes through, added to
/api/v1 only (nothing existing changed):

- ``PUT    /api/v1/stream``                    the destination / title (AutonaActions)
- ``GET / POST /api/v1/stream/channels``       the saved channels (StreamService)
- ``PUT / DELETE /api/v1/stream/channels/<i>`` one of them
- ``PUT    /api/v1/stream/org/<icon|thumbnail>`` an org image, a base64 data URL
- ``DELETE /api/v1/stream/org``                both org images
- ``GET / PUT /api/v1/stream/thumbnail/options`` the generator's options
- ``POST   /api/v1/stream/thumbnail``          202; the result arrives in the state

Through Flask's test client against the real route table, on a
StreamService of the test's own in a directory of its own (the `env`
fixture of tests/test_stream_service.py, where the operations themselves
are pinned): the contract (envelopes, 400 / 404 / 409 / 413, nothing
written on a refusal), the upload's size limit, and the access rules (this
PC without the token, another computer with it).
"""
import base64
import copy
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src import AutonaApiV1
from src import AutonaWebOperations as WebOps
from src.core import StateSchema as schema
from src.core import StreamService as stream
from src.core import WebAccess
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_stream_service import (  # noqa: F401
    GREEN, WAIT, _save_image, env, executor)


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _data_url(size=(64, 32), fmt="PNG", mime="image/png"):
    buffer = BytesIO()
    Image.new("RGB", size, GREEN).save(buffer, fmt)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _stream():
    return copy.deepcopy(SM.Get(schema.STREAM_ROOT))


# ── PUT /stream ──────────────────────────────────────────────────────────

def test_the_destination_and_the_title_are_one_publish(env, client):
    before = SM.revision

    body = _ok(client.put("/api/v1/stream", json={
        "url": "https://example.invalid/live", "title": "Mine"}))

    assert SM.Get(stream.URL_PATH) == "https://example.invalid/live"
    assert SM.Get(stream.TITLE_PATH) == "Mine"
    assert body["revision"] == SM.revision == before + 1


def test_either_field_alone_is_written_alone(env, client):
    _ok(client.put("/api/v1/stream", json={"url": "https://a.invalid/"}))
    _ok(client.put("/api/v1/stream", json={"title": "T"}))
    _ok(client.put("/api/v1/stream", json={"url": ""}))

    assert SM.Get(stream.URL_PATH) == ""
    assert SM.Get(stream.TITLE_PATH) == "T"


def test_an_unchanged_value_publishes_nothing(env, client):
    _ok(client.put("/api/v1/stream", json={"title": "Same"}))
    before = SM.revision

    body = _ok(client.put("/api/v1/stream", json={"title": "Same"}))

    assert body["revision"] == SM.revision == before


@pytest.mark.parametrize("body", [
    {},
    {"title": 5},
    {"url": None},
    {"title": "T", "titleAuto": "T"},
    {"title": "T", "url": ["x"]},
    ["title"],
])
def test_a_bad_stream_write_is_rejected_without_writing(env, client, body):
    _ok(client.put("/api/v1/stream", json={"title": "Kept"}))
    kept = _stream()

    _assertRejected(client.put("/api/v1/stream", json=body))

    assert _stream() == kept


# ── /stream/channels ─────────────────────────────────────────────────────

def _channels(client):
    return _ok(client.get("/api/v1/stream/channels"))["data"]["channels"]


def test_channels_are_added_updated_and_deleted(env, client):
    assert _channels(client) == []

    added = _ok(client.post("/api/v1/stream/channels", json={
        "name": "Main", "url": "https://a.invalid/live"}))
    assert added["data"] == {"index": 0, "channels": [
        {"name": "Main", "url": "https://a.invalid/live"}]}
    # Settings, not state.
    assert "revision" not in added
    added = _ok(client.post("/api/v1/stream/channels", json={
        "name": "Sub", "url": "https://b.invalid/live"}))
    assert added["data"]["index"] == 1
    assert SettingsManager.Get(stream.CHANNELS_SETTING) == [
        {"name": "Main", "url": "https://a.invalid/live"},
        {"name": "Sub", "url": "https://b.invalid/live"}]

    # The destination was the edited channel's URL: it follows.
    _ok(client.put("/api/v1/stream", json={"url": "https://a.invalid/live"}))
    updated = _ok(client.put("/api/v1/stream/channels/0", json={
        "name": "Main 2", "url": "https://a.invalid/new"}))
    assert updated["data"]["channels"][0] == {
        "name": "Main 2", "url": "https://a.invalid/new"}
    assert SM.Get(stream.URL_PATH) == "https://a.invalid/new"
    assert updated["revision"] == SM.revision

    # Deleting it clears the destination; the other one moves up.
    deleted = _ok(client.delete("/api/v1/stream/channels/0"))
    assert deleted["data"]["channels"] == [
        {"name": "Sub", "url": "https://b.invalid/live"}]
    assert SM.Get(stream.URL_PATH) == ""
    assert _channels(client) == deleted["data"]["channels"]


def test_a_channel_that_is_not_there_is_not_found(env, client):
    _ok(client.post("/api/v1/stream/channels", json={
        "name": "Main", "url": "https://a.invalid/live"}))

    _assertRejected(client.put("/api/v1/stream/channels/1", json={
        "name": "X", "url": "https://x.invalid/"}), "NOT_FOUND", 404)
    _assertRejected(client.delete("/api/v1/stream/channels/1"),
                    "NOT_FOUND", 404)

    assert _channels(client) == [
        {"name": "Main", "url": "https://a.invalid/live"}]


@pytest.mark.parametrize("body", [
    {},
    {"name": "Main"},
    {"url": "https://a.invalid/"},
    {"name": "", "url": "https://a.invalid/"},
    {"name": "Main", "url": "   "},
    {"name": 5, "url": "https://a.invalid/"},
    {"name": "Main", "url": "https://a.invalid/", "id": 1},
    ["Main"],
])
def test_a_bad_channel_is_rejected_without_saving(env, client, body):
    _ok(client.post("/api/v1/stream/channels", json={
        "name": "Main", "url": "https://a.invalid/live"}))
    saved = copy.deepcopy(SettingsManager.settings)

    _assertRejected(client.post("/api/v1/stream/channels", json=body))
    _assertRejected(client.put("/api/v1/stream/channels/0", json=body))

    assert SettingsManager.settings == saved


# ── PUT /stream/org/<kind>, DELETE /stream/org ──────────────────────────

@pytest.mark.parametrize("kind, fmt, mime, version", [
    ("icon", "PNG", "image/png", "orgIconVersion"),
    ("thumbnail", "JPEG", "image/jpeg", "orgThumbVersion"),
    ("icon", "BMP", "image/bmp", "orgIconVersion"),
    ("thumbnail", "BMP", "image/x-ms-bmp", "orgThumbVersion"),
])
def test_an_org_image_is_saved_and_its_version_published(
        env, client, kind, fmt, mime, version):
    body = _ok(client.put(f"/api/v1/stream/org/{kind}", json={
        "image": _data_url(fmt=fmt, mime=mime)}))

    path, _key, _side = stream.ORG_ASSETS[kind]
    with Image.open(path) as saved:
        assert saved.format == "PNG"
        assert saved.size == (64, 32)
    assert SM.Get(f"{schema.STREAM_ROOT}.{version}") > 0
    assert body["revision"] == SM.revision


@pytest.mark.parametrize("mime", [
    "IMAGE/PNG", "application/octet-stream", "", "image/gif"])
def test_the_data_url_type_is_not_looked_at(env, client, mime):
    # The browser's guess from the file name (application/octet-stream for
    # one it does not know, S4e review): core reads what the image is.
    _ok(client.put("/api/v1/stream/org/icon", json={
        "image": _data_url(mime=mime)}))
    assert SM.Get(f"{schema.STREAM_ROOT}.orgIconVersion") > 0


@pytest.mark.parametrize("path, body", [
    ("/api/v1/stream/org/banner", {"image": "png"}),
    ("/api/v1/stream/org/Icon", {"image": "png"}),
    ("/api/v1/stream/org/icon", {}),
    ("/api/v1/stream/org/icon", {"image": 5}),
    ("/api/v1/stream/org/icon", {"image": "iVBORw0KGgo="}),
    ("/api/v1/stream/org/icon", {"image": "data:image/png,iVBORw0KGgo="}),
    # Not an image core reads (a GIF), whatever the type says.
    ("/api/v1/stream/org/icon", {"image": "data:image/gif;base64,R0lGOD=="}),
    ("/api/v1/stream/org/icon", {"image": "data:image/png;base64,"}),
    ("/api/v1/stream/org/icon", {"image": "data:image/png;base64,***"}),
    # Base64 of what is not an image: core refuses it.
    ("/api/v1/stream/org/icon",
     {"image": "data:image/png;base64," + base64.b64encode(
         b"not an image").decode("ascii")}),
    # A GIF sent as a PNG: core reads what it really is.
    ("/api/v1/stream/org/thumbnail",
     {"image": "data:image/png;base64,R0lGODlhAQABAAAAACw="}),
])
def test_a_bad_org_image_is_rejected_and_nothing_saved(env, client, path,
                                                       body):
    before = _stream()

    _assertRejected(client.put(path, json=body))

    assert _stream() == before
    for asset, _key, _side in stream.ORG_ASSETS.values():
        assert not Path(asset).exists()


def test_an_org_image_that_cannot_be_saved_is_an_internal_error(
        env, client, monkeypatch):
    def held(path, data):
        raise PermissionError("held open")
    monkeypatch.setattr(stream, "_WriteFile", held)

    _assertRejected(client.put("/api/v1/stream/org/icon", json={
        "image": _data_url()}), "INTERNAL", 500)

    assert SM.Get(f"{schema.STREAM_ROOT}.orgIconVersion", 0) == 0


def test_a_body_over_the_limit_is_refused_before_it_is_read(env, client):
    # The Content-Length says it: the body is dropped, not parsed (a short
    # body that claims to be long is refused all the same).
    response = client.put(
        "/api/v1/stream/org/icon", data=b'{"image": ""}',
        content_type="application/json",
        environ_overrides={
            "CONTENT_LENGTH": str(AutonaApiV1._ORG_IMAGE_MAX_BODY + 1)})

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)
    assert not Path(stream.ORG_ASSETS["icon"][0]).exists()


def test_the_real_server_answers_an_oversized_upload_with_413(env, webApp):
    """werkzeug's own server (what AutonaWebServer runs: make_server,
    threaded), over a socket (S4e review). A client that has not sent the
    body yet when the answer is ready -- a slower link than this one --
    still gets the 413: the body is read and dropped first. Answered
    unread, the server closed the connection on it and the client got a
    reset (ConnectionAbortedError here) instead."""
    import http.client
    import json
    import threading
    import time

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, webApp, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    size = AutonaApiV1._ORG_IMAGE_MAX_BODY + 1
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=30)
    try:
        connection.putrequest("PUT", "/api/v1/stream/org/icon")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        time.sleep(0.3)
        chunk = b"x" * (1024 * 1024)
        sent = 0
        while sent < size:
            part = chunk[:size - sent]
            connection.send(part)
            sent += len(part)
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(5)

    assert status == 413
    assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert not Path(stream.ORG_ASSETS["icon"][0]).exists()


def test_the_limit_is_twenty_megabytes_of_image(webApp):
    assert AutonaApiV1.ORG_IMAGE_MAX_BYTES == 20 * 1024 * 1024
    # Its base64, and a little JSON around it.
    assert AutonaApiV1._ORG_IMAGE_MAX_BASE64 == (
        (AutonaApiV1.ORG_IMAGE_MAX_BYTES + 2) // 3 * 4)
    assert 0 < AutonaApiV1._ORG_IMAGE_MAX_BODY - \
        AutonaApiV1._ORG_IMAGE_MAX_BASE64 <= 1024
    # The upload's own, checked by its route only: the app has no limit
    # (the Designer's routes check theirs by hand, AutonaWebServer.py).
    assert webApp.config["MAX_CONTENT_LENGTH"] is None


def test_the_page_refuses_the_same_size_before_sending():
    # The Stream page's copy of the limit (it refuses a larger file before
    # reading it): one value on both sides (S4e review).
    import re

    from tests.conftest import PROJECT_ROOT

    source = (PROJECT_ROOT / "web" / "src" / "StreamPage" / "Cards.tsx"
              ).read_text(encoding="utf-8")
    found = re.findall(
        r"^export const ORG_IMAGE_MAX_BYTES = ([\d\s*]+);$", source,
        re.MULTILINE)
    assert len(found) == 1, found
    value = 1
    for factor in found[0].split("*"):
        value *= int(factor)
    assert value == AutonaApiV1.ORG_IMAGE_MAX_BYTES


def test_an_image_over_the_limit_inside_the_body_is_refused(
        env, client, monkeypatch):
    image = _data_url()
    payload = image.partition(",")[2]
    decoded = len(base64.b64decode(payload))

    # Its base64 is over the limit: refused before it is decoded.
    monkeypatch.setattr(AutonaApiV1, "_ORG_IMAGE_MAX_BASE64",
                        len(payload) - 1)
    _assertRejected(client.put("/api/v1/stream/org/icon", json={
        "image": image}), "PAYLOAD_TOO_LARGE", 413)
    # Decoded, it is over the limit.
    monkeypatch.setattr(AutonaApiV1, "_ORG_IMAGE_MAX_BASE64", len(payload))
    monkeypatch.setattr(AutonaApiV1, "ORG_IMAGE_MAX_BYTES", decoded - 1)
    _assertRejected(client.put("/api/v1/stream/org/icon", json={
        "image": image}), "PAYLOAD_TOO_LARGE", 413)
    assert not Path(stream.ORG_ASSETS["icon"][0]).exists()

    monkeypatch.setattr(AutonaApiV1, "ORG_IMAGE_MAX_BYTES", decoded)
    _ok(client.put("/api/v1/stream/org/icon", json={"image": image}))


def test_the_image_stays_out_of_the_op_log(env, client):
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    image = _data_url()
    try:
        _ok(client.put("/api/v1/stream/org/icon", json={"image": image}))
    finally:
        logger.remove(sink)

    opLog = [line for line in lines if "op=stream.org.import" in line]
    assert len(opLog) == 1
    assert "['image']" in opLog[0]
    assert image.partition(",")[2][:40] not in opLog[0]


def test_the_reset_removes_both_and_moves_the_background_to_the_banner(
        env, client):
    for kind in ("icon", "thumbnail"):
        _ok(client.put(f"/api/v1/stream/org/{kind}", json={
            "image": _data_url()}))
    _ok(client.put("/api/v1/stream/thumbnail/options",
                   json={"source": "org"}))

    body = _ok(client.delete("/api/v1/stream/org"))

    assert body["data"]["thumbnailOptions"]["source"] == "banner"
    assert body["revision"] == SM.revision
    for path, _key, _side in stream.ORG_ASSETS.values():
        assert not Path(path).exists()
    assert SM.Get(f"{schema.STREAM_ROOT}.orgIconVersion") == 0
    assert SM.Get(f"{schema.STREAM_ROOT}.orgThumbVersion") == 0


# ── /stream/thumbnail/options ────────────────────────────────────────────

def test_the_options_are_read_and_saved(env, client):
    assert _ok(client.get("/api/v1/stream/thumbnail/options"))["data"] == (
        stream.DEFAULT_THUMBNAIL_OPTIONS)

    body = _ok(client.put("/api/v1/stream/thumbnail/options", json={
        "style": "netflix", "overlayOrgIcon": True}))

    expected = dict(stream.DEFAULT_THUMBNAIL_OPTIONS, style="netflix",
                    overlayOrgIcon=True)
    assert body == {"ok": True, "data": expected}
    assert SettingsManager.Get(stream.THUMBNAIL_OPTIONS_SETTING) == expected
    assert _ok(client.get("/api/v1/stream/thumbnail/options"))["data"] == (
        expected)


@pytest.mark.parametrize("body", [
    {},
    {"source": "both"},
    {"style": "Netflix"},
    {"overlayOrgIcon": "true"},
    {"style": "center", "size": 2},
    ["style"],
])
def test_bad_options_are_rejected_without_saving(env, client, body):
    _assertRejected(client.put("/api/v1/stream/thumbnail/options", json=body))

    assert SettingsManager.Get(stream.THUMBNAIL_OPTIONS_SETTING) is None


# ── POST /stream/thumbnail ───────────────────────────────────────────────

def test_a_thumbnail_is_accepted_then_arrives_in_the_state(
        env, client, executor):
    _save_image(stream.BANNER_PATH, (320, 180), GREEN)

    body = _ok(client.post("/api/v1/stream/thumbnail"), 202)
    assert body["data"] == {"accepted": True,
                            "operation": "stream.thumbnail"}
    assert SM.Get(stream.THUMBNAIL_PATH)["status"] == stream.RUNNING
    assert body["revision"] == SM.revision

    # One at a time.
    _assertRejected(client.post("/api/v1/stream/thumbnail"), "CONFLICT", 409)
    assert executor.queued() == 1

    assert executor.run_next(WAIT) is True
    thumbnail = SM.Get(stream.THUMBNAIL_PATH)
    assert thumbnail["status"] == stream.DONE
    assert thumbnail["path"].startswith("out/stream/thumbnail_banner_center_")
    assert Path(thumbnail["path"]).exists()
    # The next one is taken again.
    _ok(client.post("/api/v1/stream/thumbnail"), 202)
    assert executor.drain(WAIT)
    assert SM.Get(stream.THUMBNAIL_PATH)["revision"] == 2


def test_a_thumbnail_that_fails_says_why_in_the_state(env, client, executor):
    _ok(client.put("/api/v1/stream/thumbnail/options",
                   json={"source": "org"}))

    _ok(client.post("/api/v1/stream/thumbnail"), 202)
    assert executor.drain(WAIT)

    thumbnail = SM.Get(stream.THUMBNAIL_PATH)
    assert thumbnail["status"] == stream.ERROR
    assert thumbnail["error"] == stream.NO_ORG_THUMBNAIL


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

_IMAGE = "data:image/png;base64,iVBORw0KGgo="
_WRITES = [
    ("put", "/api/v1/stream", {"title": "X"}, "update_stream", 200),
    ("post", "/api/v1/stream/channels", {"name": "X", "url": "https://x/"},
     "add_stream_channel", 200),
    ("put", "/api/v1/stream/channels/0", {"name": "X", "url": "https://x/"},
     "update_stream_channel", 200),
    ("delete", "/api/v1/stream/channels/0", None, "delete_stream_channel",
     200),
    ("put", "/api/v1/stream/org/icon", {"image": _IMAGE},
     "import_stream_org_asset", 200),
    ("delete", "/api/v1/stream/org", None, "reset_stream_org_assets", 200),
    ("put", "/api/v1/stream/thumbnail/options", {"style": "center"},
     "set_stream_thumbnail_options", 200),
    ("post", "/api/v1/stream/thumbnail", None, "generate_stream_thumbnail",
     202),
]


@pytest.fixture
def lan(monkeypatch, webApp):
    """The routes' operations recorded, the token private."""
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    calls = []
    for _method, _path, _body, name, _status in _WRITES:
        monkeypatch.setattr(
            WebOps, name,
            lambda *args, _name=name: calls.append(_name) or {"id": "x"})
    return calls


def _send(client, method, path, body, **kwargs):
    if body is None:
        return getattr(client, method)(path, **kwargs)
    return getattr(client, method)(path, json=body, **kwargs)


@pytest.mark.parametrize("method, path, body, operation, status", _WRITES)
def test_the_writes_need_the_token_from_another_computer(
        autonaApp, webApp, lan, method, path, body, operation, status):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = _send(peer, method, path, body)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert _send(peer, method, path, body,
                 headers={"X-Autona-Token": token}).status_code == status
    assert lan == [operation]

    # This PC (the desktop's embedded view): no token.
    assert _send(webApp.test_client(), method, path, body
                 ).status_code == status
    assert lan == [operation, operation]


def test_the_reads_need_no_token(autonaApp, webApp, lan):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    for path in ("/api/v1/stream/channels",
                 "/api/v1/stream/thumbnail/options"):
        assert peer.get(path).status_code == 200, path
