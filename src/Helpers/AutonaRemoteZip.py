"""Random-access reader for a remote zip over HTTP Range requests.

Purpose: differential updates. A release zip is ~311 MB but between releases
only a few percent of entries actually change (measured v1.3.0 -> v1.3.2:
19.7 MB of 310.6 MB). The zip central directory carries every entry's CRC32,
so we can fetch just the directory (~1 MB), compare CRCs against the local
install, and Range-fetch only the entries that differ - no release-pipeline
changes, works across any version gap.

Design notes:
- No Qt, no relative imports: tests load this via conftest.direct_import
  without booting the src package (agent-pitfalls #1).
- Entry data is located by "this entry's header_offset .. next entry's
  header_offset" using the CENTRAL directory only, then the local header is
  parsed out of the fetched bytes. This sidesteps local/central extra-field
  length mismatches and data-descriptor layouts entirely.
- The default HTTP fetcher validates 206 + Content-Range on every response
  (a Range-ignoring proxy would otherwise hand us the whole archive as "one
  entry") and pins the asset's total size + ETag across requests (a release
  asset swapped mid-download would silently shift every offset).
- Every failure raises RemoteZipError; the caller (AutonaUpdater) treats any
  of them as "fall back to the full download". Diff is an optimization and
  must never add a failure mode.
"""
import os
import shutil
import struct
import zlib
from pathlib import Path

import requests
from loguru import logger

# End Of Central Directory: sig, disk, cd_disk, n_disk, n_total, cd_size,
# cd_offset, comment_len
_EOCD_SIG = b"PK\x05\x06"
_EOCD_FMT = "<4sHHHHIIH"
_EOCD_SIZE = struct.calcsize(_EOCD_FMT)  # 22

# Central directory entry: sig, ver_made, ver_need, flags, method, time,
# date, crc, comp_size, file_size, name_len, extra_len, comment_len,
# disk_start, int_attr, ext_attr, header_offset
_CDE_SIG = b"PK\x01\x02"
_CDE_FMT = "<4sHHHHHHIIIHHHHHII"
_CDE_SIZE = struct.calcsize(_CDE_FMT)  # 46

# Local file header: sig, ver_need, flags, method, time, date, crc,
# comp_size, file_size, name_len, extra_len
_LFH_SIG = b"PK\x03\x04"
_LFH_FMT = "<4sHHHHHIIIHH"
_LFH_SIZE = struct.calcsize(_LFH_FMT)  # 30

_UTF8_FLAG = 0x800

# Fetch this much tail to find the EOCD (22 bytes + up to 64 KiB comment).
_EOCD_TAIL = _EOCD_SIZE + 65536

# Merge Range requests for entries whose spans are closer than this. Keeps
# the request count around 10-20 for a typical release instead of one per
# changed file.
_COALESCE_GAP = 512 * 1024


class RemoteZipError(Exception):
    """Any condition that makes the diff path unusable. The caller falls
    back to the full-zip download - never surface this to the user."""


class RemoteZipEntry:
    __slots__ = ("name", "crc", "comp_size", "file_size", "method",
                 "header_offset", "flags")

    def __init__(self, name, crc, comp_size, file_size, method,
                 header_offset, flags):
        self.name = name
        self.crc = crc
        self.comp_size = comp_size
        self.file_size = file_size
        self.method = method
        self.header_offset = header_offset
        self.flags = flags

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/") or self.name.endswith("\\")


def make_http_range_fetcher(url: str, timeout: int = 60):
    """Build the default fetcher: fetch(start, length) -> (bytes,
    total_size). Absolute ranges only — GitHub's release CDN rejects suffix
    ranges (bytes=-N) with HTTP 501, so the reader sizes the archive with a
    1-byte probe instead.

    Follows the 302 to objects.githubusercontent.com on every call (requests
    re-sends the Range header across redirects; re-resolving each time also
    dodges signed-URL expiry on long downloads). Pins total size and ETag
    from the first response so an asset replaced mid-download is detected
    immediately instead of via a downstream CRC mismatch."""
    state = {"total": None, "etag": None}

    def fetch(start, length):
        rng = f"bytes={start}-{start + length - 1}"
        try:
            resp = requests.get(
                url, timeout=timeout,
                headers={"Range": rng, "Accept-Encoding": "identity"})
        except requests.RequestException as e:
            raise RemoteZipError(f"range request failed: {e}") from e
        if resp.status_code != 206:
            # 200 = server/proxy ignored the Range header. Proceeding would
            # treat the whole 311 MB archive as one entry's bytes.
            raise RemoteZipError(
                f"server did not honor Range (HTTP {resp.status_code})")
        content_range = resp.headers.get("Content-Range", "")
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            raise RemoteZipError(
                f"unparsable Content-Range: {content_range!r}") from None
        etag = resp.headers.get("ETag")
        if state["total"] is None:
            state["total"], state["etag"] = total, etag
        elif total != state["total"] or (
                etag and state["etag"] and etag != state["etag"]):
            raise RemoteZipError("asset changed during download")
        return resp.content, total

    return fetch


class RemoteZipReader:
    """fetch: callable(start, length) -> (bytes, total_size), absolute
    offsets only (no suffix ranges — see make_http_range_fetcher). Injected
    in tests."""

    def __init__(self, url: str = None, fetch=None):
        if fetch is None:
            if not url:
                raise ValueError("url or fetch is required")
            fetch = make_http_range_fetcher(url)
        self._fetch = fetch
        self.size = None
        self.entries = []          # list[RemoteZipEntry], zip order
        self._cd_offset = None

    def _ranged(self, start, length):
        data, total = self._fetch(start, length)
        if self.size is None:
            self.size = total
        if len(data) != length:
            # A range-ignoring middlebox that still returned 206, or a
            # truncated body. Either way the offsets below would be garbage.
            raise RemoteZipError(
                f"range length mismatch: wanted {length}, got {len(data)}")
        return data

    def load_directory(self):
        # 1-byte probe to learn the archive size (Content-Range carries the
        # total). Suffix ranges would do this in one request, but GitHub's
        # release CDN answers them with HTTP 501.
        self._ranged(0, 1)
        if not self.size or self.size < _EOCD_SIZE:
            raise RemoteZipError(f"implausible archive size: {self.size}")
        tail_len = min(_EOCD_TAIL, self.size)
        tail = self._ranged(self.size - tail_len, tail_len)
        pos = tail.rfind(_EOCD_SIG)
        if pos < 0:
            raise RemoteZipError("EOCD signature not found (not a zip?)")
        (_, disk, cd_disk, n_disk, n_total, cd_size, cd_offset,
         _comment_len) = struct.unpack(
            _EOCD_FMT, tail[pos:pos + _EOCD_SIZE])
        if disk or cd_disk:
            raise RemoteZipError("multi-disk archives are not supported")
        if (n_total == 0xFFFF or cd_size == 0xFFFFFFFF
                or cd_offset == 0xFFFFFFFF):
            raise RemoteZipError("zip64 archives are not supported")

        cd = self._ranged(cd_offset, cd_size)
        self._cd_offset = cd_offset
        entries = []
        off = 0
        for _ in range(n_total):
            if cd[off:off + 4] != _CDE_SIG:
                raise RemoteZipError("central directory entry corrupt")
            (_, _ver_made, _ver_need, flags, method, _t, _d, crc, comp_size,
             file_size, name_len, extra_len, comment_len, _disk, _ia, _ea,
             header_offset) = struct.unpack(
                _CDE_FMT, cd[off:off + _CDE_SIZE])
            raw = cd[off + _CDE_SIZE: off + _CDE_SIZE + name_len]
            name = raw.decode("utf-8" if flags & _UTF8_FLAG else "cp437")
            entries.append(RemoteZipEntry(
                name, crc, comp_size, file_size, method, header_offset,
                flags))
            off += _CDE_SIZE + name_len + extra_len + comment_len
        if not entries:
            raise RemoteZipError("archive has no entries")
        self.entries = entries
        return entries

    def _entry_spans(self, wanted):
        """(entry, span_start, span_end) per wanted entry. span_end is the
        next entry's header_offset in file order (or the central directory
        for the last entry), so it always covers the compressed data plus
        any trailing data descriptor without trusting local-header sizes."""
        offsets = sorted(e.header_offset for e in self.entries)
        offsets.append(self._cd_offset)
        by_offset = {e.header_offset: e for e in self.entries}
        spans = []
        for i, start in enumerate(offsets[:-1]):
            e = by_offset[start]
            if e in wanted:
                spans.append((e, start, offsets[i + 1]))
        return spans

    def fetch_entries(self, entries, sink, progress_cb=None, cancel_cb=None,
                      gap: int = _COALESCE_GAP):
        """Download, decompress and CRC-verify each entry, calling
        sink(entry, plain_bytes) as they complete. progress_cb(comp_bytes)
        reports cumulative compressed bytes; cancel_cb() -> True aborts."""
        if self._cd_offset is None:
            raise RemoteZipError("load_directory() has not run")
        wanted = set(e for e in entries if not e.is_dir)
        if not wanted:
            return
        spans = self._entry_spans(wanted)
        if len(spans) != len(wanted):
            raise RemoteZipError("entry not found in directory index")

        # Coalesce adjacent spans into merged Range requests.
        merged = []  # [start, end, [(entry, start, end), ...]]
        for span in spans:
            if merged and span[1] - merged[-1][1] <= gap:
                merged[-1][1] = max(merged[-1][1], span[2])
                merged[-1][2].append(span)
            else:
                merged.append([span[1], span[2], [span]])

        done_comp = 0
        for req_start, req_end, group in merged:
            if cancel_cb and cancel_cb():
                raise RemoteZipError("cancelled")
            blob = self._ranged(req_start, req_end - req_start)
            for entry, start, _end in group:
                base = start - req_start
                header = blob[base: base + _LFH_SIZE]
                if header[:4] != _LFH_SIG:
                    raise RemoteZipError(
                        f"local header corrupt for {entry.name}")
                (_, _ver, _flags, method, _t, _d, _crc, _csize, _usize,
                 name_len, extra_len) = struct.unpack(_LFH_FMT, header)
                data_at = base + _LFH_SIZE + name_len + extra_len
                comp = blob[data_at: data_at + entry.comp_size]
                if len(comp) != entry.comp_size:
                    raise RemoteZipError(
                        f"compressed data truncated for {entry.name}")
                plain = _decompress(comp, entry)
                sink(entry, plain)
                done_comp += entry.comp_size
                if progress_cb:
                    progress_cb(done_comp)


def _decompress(comp: bytes, entry: RemoteZipEntry) -> bytes:
    if entry.method == 8:                       # deflate
        try:
            d = zlib.decompressobj(-15)
            plain = d.decompress(comp) + d.flush()
        except zlib.error as e:
            raise RemoteZipError(
                f"decompression failed for {entry.name}: {e}") from e
    elif entry.method == 0:                     # stored
        plain = comp
    else:
        raise RemoteZipError(
            f"unsupported compression method {entry.method} "
            f"for {entry.name}")
    if len(plain) != entry.file_size:
        raise RemoteZipError(f"size mismatch for {entry.name}")
    if (zlib.crc32(plain) & 0xFFFFFFFF) != entry.crc:
        raise RemoteZipError(f"CRC mismatch for {entry.name}")
    return plain


# ---------------------------------------------------------------------------
# Diff planning and staging assembly
# ---------------------------------------------------------------------------

def strip_root(name: str):
    """Mirror apply_update.ps1's semantics: normalize separators, drop the
    leading folder ("Autona/"), return None for the root itself, entries
    without a folder, and directory entries."""
    n = name.replace("\\", "/")
    if "/" not in n:
        return None
    rel = n.split("/", 1)[1]
    if not rel or rel.endswith("/"):
        return None
    return rel


def safe_dest(base: Path, rel: str) -> Path:
    """ZIP-slip guard, same policy as the helper: any entry that resolves
    outside the staging dir is an error (we abort rather than skip - a diff
    that silently drops entries would build an incomplete staging)."""
    dest = (base / rel).resolve()
    root = str(base.resolve()).rstrip(os.sep) + os.sep
    if not str(dest).lower().startswith(root.lower()):
        raise RemoteZipError(f"unsafe entry path: {rel}")
    return dest


def local_file_crc32(path: Path, chunk: int = 1024 * 1024) -> int:
    crc = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            crc = zlib.crc32(block, crc)
    return crc & 0xFFFFFFFF


class DiffPlan:
    __slots__ = ("changed", "unchanged", "download_comp", "total_comp",
                 "total_file_size")

    def __init__(self):
        self.changed = []      # RemoteZipEntry needing download
        self.unchanged = []    # RemoteZipEntry satisfiable from local install
        self.download_comp = 0
        self.total_comp = 0
        self.total_file_size = 0


def diff_against_install(entries, install_dir: Path,
                         cancel_cb=None) -> DiffPlan:
    """Split zip entries into changed (download) vs unchanged (copy from the
    local install). Size mismatch short-circuits before the CRC read, so the
    expensive full read only happens for same-size files (the common case:
    unchanged big DLLs - unavoidable, CRC is the ground truth)."""
    plan = DiffPlan()
    for e in entries:
        if cancel_cb and cancel_cb():
            raise RemoteZipError("cancelled")
        rel = strip_root(e.name)
        if rel is None or e.is_dir:
            continue
        plan.total_comp += e.comp_size
        plan.total_file_size += e.file_size
        local = install_dir / rel
        try:
            if (local.is_file()
                    and local.stat().st_size == e.file_size
                    and local_file_crc32(local) == e.crc):
                plan.unchanged.append(e)
                continue
        except OSError:
            pass  # unreadable local file -> treat as changed
        plan.changed.append(e)
        plan.download_comp += e.comp_size
    return plan


def copy_unchanged_to_staging(staging: Path, install_dir: Path, entries,
                              cancel_cb=None) -> int:
    """Materialize the unchanged entries into staging from the local
    install. copy2 (not copy) is load-bearing: it preserves mtime, so the
    helper's robocopy sees size+timestamp equality and skips these files
    instead of rewriting ~1 GB."""
    count = 0
    for e in entries:
        if cancel_cb and cancel_cb():
            raise RemoteZipError("cancelled")
        rel = strip_root(e.name)
        if rel is None:
            continue
        src = install_dir / rel
        dest = safe_dest(staging, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
    return count
