"""Unit tests for AutonaRemoteZip (differential update core).

Everything runs against zips built in tmp_path with a fake fetcher slicing a
bytes blob - no network, no Qt (direct_import per agent-pitfalls #1).
"""
import io
import zipfile
import zlib
from pathlib import Path

import pytest

from tests.conftest import direct_import

rz = direct_import("AutonaRemoteZip", "src/Helpers/AutonaRemoteZip.py")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def build_zip(files, compression=zipfile.ZIP_DEFLATED, unseekable=False):
    """files: {name_inside_root: bytes}. Entries live under 'Autona/' like a
    real release. unseekable=True forces the writer down the streaming path,
    which emits data descriptors (flag bit 3) - the layout our offset-based
    reader must not trip over."""
    buf = io.BytesIO()

    class _Unseekable(io.RawIOBase):
        def writable(self):
            return True

        def seekable(self):
            return False

        def write(self, b):
            return buf.write(b)

    target = _Unseekable() if unseekable else buf
    with zipfile.ZipFile(target, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(f"Autona/{name}", data)
    return buf.getvalue()


def make_fake_fetch(blob, calls=None):
    def fetch(start, length):
        if calls is not None:
            calls.append((start, length))
        return blob[start:start + length], len(blob)
    return fetch


SAMPLE = {
    "Autona.exe": b"launcher-bytes" * 1000,
    "assets/apply_update.ps1": b"# helper with staging_prebuilt.marker\n",
    "assets/versions.json": b'{"program": "9.9.9"}',
    "_internal/big.dll": bytes(range(256)) * 4096,   # incompressible-ish
    "layout/index.js": b"console.log('hi')\n",
}


def load(blob, calls=None):
    r = rz.RemoteZipReader(fetch=make_fake_fetch(blob, calls))
    r.load_directory()
    return r


# ---------------------------------------------------------------------------
# directory parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unseekable", [False, True],
                         ids=["seekable", "data-descriptor"])
@pytest.mark.parametrize("comp", [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED],
                         ids=["deflated", "stored"])
def test_directory_matches_zipfile(comp, unseekable):
    blob = build_zip(SAMPLE, compression=comp, unseekable=unseekable)
    reader = load(blob)
    expect = {i.filename: (i.CRC, i.file_size)
              for i in zipfile.ZipFile(io.BytesIO(blob)).infolist()}
    got = {e.name: (e.crc, e.file_size) for e in reader.entries}
    assert got == expect


def test_directory_fetch_is_three_small_requests():
    calls = []
    blob = build_zip(SAMPLE)
    load(blob, calls)
    # size probe + EOCD tail + central directory; GitHub's CDN rejects
    # suffix ranges (HTTP 501) so the probe is unavoidable. (On a real
    # 311 MB release these three total under 1 MB; this test zip is smaller
    # than the 64 KB EOCD window, so no volume assertion here.)
    assert len(calls) == 3
    assert calls[0] == (0, 1)


def test_not_a_zip_raises():
    with pytest.raises(rz.RemoteZipError):
        load(b"this is definitely not a zip archive" * 100)


def test_truncated_range_response_raises():
    """A middlebox that ignores Range but still says 206 returns the wrong
    number of bytes - the reader must refuse rather than mis-parse."""
    blob = build_zip(SAMPLE)

    def bad_fetch(start, length):
        return blob[:10], len(blob)        # always 10 bytes

    with pytest.raises(rz.RemoteZipError):
        rz.RemoteZipReader(fetch=bad_fetch).load_directory()


# ---------------------------------------------------------------------------
# entry fetching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unseekable", [False, True],
                         ids=["seekable", "data-descriptor"])
def test_fetch_entries_roundtrip(unseekable):
    blob = build_zip(SAMPLE, unseekable=unseekable)
    reader = load(blob)
    got = {}
    reader.fetch_entries(reader.entries, lambda e, d: got.__setitem__(e.name, d))
    assert got == {f"Autona/{k}": v for k, v in SAMPLE.items()}


def test_fetch_entries_coalesces_requests():
    calls = []
    blob = build_zip(SAMPLE)
    reader = load(blob, calls)
    calls.clear()
    reader.fetch_entries(reader.entries, lambda e, d: None)
    # all sample entries fit well inside one coalesce gap
    assert len(calls) == 1


def test_fetch_subset_only_downloads_that_span():
    blob = build_zip(SAMPLE)
    reader = load(blob)
    calls = []
    reader._fetch = make_fake_fetch(blob, calls)
    first = [e for e in reader.entries if e.name == "Autona/Autona.exe"]
    got = {}
    reader.fetch_entries(first, lambda e, d: got.__setitem__(e.name, d),
                         gap=0)
    assert got == {"Autona/Autona.exe": SAMPLE["Autona.exe"]}
    total_fetched = sum(length for _s, length in calls)
    assert total_fetched < len(blob) / 2   # nowhere near the whole archive


def test_corrupted_data_raises_crc_error():
    blob = bytearray(build_zip(SAMPLE))
    reader = load(bytes(blob))
    # flip bytes inside the biggest entry's compressed data
    big = max(reader.entries, key=lambda e: e.comp_size)
    at = big.header_offset + 60
    blob[at] ^= 0xFF
    blob[at + 1] ^= 0xFF
    reader._fetch = make_fake_fetch(bytes(blob))
    with pytest.raises(rz.RemoteZipError):
        reader.fetch_entries(reader.entries, lambda e, d: None)


def test_cancel_aborts():
    reader = load(build_zip(SAMPLE))
    with pytest.raises(rz.RemoteZipError, match="cancelled"):
        reader.fetch_entries(reader.entries, lambda e, d: None,
                             cancel_cb=lambda: True)


# ---------------------------------------------------------------------------
# strip_root / safe_dest
# ---------------------------------------------------------------------------

def test_strip_root_mirrors_helper_semantics():
    assert rz.strip_root("Autona/assets/x.png") == "assets/x.png"
    assert rz.strip_root("Autona\\assets\\x.png") == "assets/x.png"
    assert rz.strip_root("Autona/") is None          # root dir entry
    assert rz.strip_root("Autona/assets/") is None   # dir entry
    assert rz.strip_root("no-folder.txt") is None


def test_safe_dest_rejects_traversal(tmp_path):
    with pytest.raises(rz.RemoteZipError):
        rz.safe_dest(tmp_path / "staging", "../../evil.exe")


# ---------------------------------------------------------------------------
# diff planning + staging assembly
# ---------------------------------------------------------------------------

def make_install(tmp_path, files):
    install = tmp_path / "install"
    for rel, data in files.items():
        p = install / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return install


def test_diff_against_install(tmp_path):
    local = dict(SAMPLE)
    local["Autona.exe"] = b"OLD-launcher" * 900        # changed content
    local["layout/index.js"] = b"console.log('hi')X\n"  # changed size
    local.pop("assets/versions.json")                  # missing locally
    local["stale/leftover.txt"] = b"kept by no-purge"  # extra local file
    install = make_install(tmp_path, local)

    reader = load(build_zip(SAMPLE))
    plan = rz.diff_against_install(reader.entries, install)

    changed = {e.name for e in plan.changed}
    assert changed == {"Autona/Autona.exe", "Autona/layout/index.js",
                       "Autona/assets/versions.json"}
    assert {e.name for e in plan.unchanged} == {
        "Autona/assets/apply_update.ps1", "Autona/_internal/big.dll"}
    assert plan.download_comp < plan.total_comp
    assert plan.total_file_size == sum(len(v) for v in SAMPLE.values())


def test_staging_assembly_is_complete_and_preserves_mtime(tmp_path):
    local = dict(SAMPLE)
    local["Autona.exe"] = b"OLD-launcher" * 900
    install = make_install(tmp_path, local)

    reader = load(build_zip(SAMPLE))
    plan = rz.diff_against_install(reader.entries, install)

    staging = tmp_path / "staging"
    reader.fetch_entries(
        plan.changed,
        lambda e, d: _write(staging, e, d))
    rz.copy_unchanged_to_staging(staging, install, plan.unchanged)

    # staging must be the complete new tree, byte-identical to the zip
    for rel, data in SAMPLE.items():
        assert (staging / rel).read_bytes() == data
    # copy2 preserved mtime so robocopy will skip unchanged files
    rel = "_internal/big.dll"
    assert (staging / rel).stat().st_mtime == (install / rel).stat().st_mtime


def _write(staging, entry, data):
    rel = rz.strip_root(entry.name)
    dest = rz.safe_dest(staging, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
