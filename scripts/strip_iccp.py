"""Remove broken iCCP chunks from PNG files.

libpng warns `iCCP: CRC error` when a PNG has an ICC color profile
chunk with an invalid CRC (common in PNGs exported by older tools).
The warning is harmless, but strips noisy output from Qt's imageio.

This script surgically removes only the iCCP chunk, preserving all
other chunks (IHDR, IDAT, PLTE, tEXt, etc.) and pixel data byte-for-byte.
Skips files that don't contain an iCCP chunk so mtimes stay stable.

Usage:
    python scripts/strip_iccp.py <dir> [<dir> ...]

If no directories are given, scans `assets/` and `user_data/`.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def strip_iccp(path: Path) -> bool:
    """Return True if the file was modified."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not data.startswith(PNG_SIG):
        return False

    out = bytearray(PNG_SIG)
    pos = 8
    changed = False
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        chunk_type = data[pos + 4:pos + 8]
        total = 12 + length  # length(4) + type(4) + data(length) + crc(4)
        if pos + total > len(data):
            # Truncated; stop and write what we have so far
            out.extend(data[pos:])
            break
        if chunk_type == b"iCCP":
            changed = True
        else:
            out.extend(data[pos:pos + total])
        pos += total

    if changed:
        path.write_bytes(bytes(out))
    return changed


def walk(roots: list[Path]) -> tuple[int, int]:
    modified = 0
    scanned = 0
    for root in roots:
        if not root.exists():
            print(f"skip: {root} (not found)")
            continue
        for png in root.rglob("*.png"):
            scanned += 1
            if strip_iccp(png):
                modified += 1
                print(f"fixed: {png}")
    return scanned, modified


if __name__ == "__main__":
    if len(sys.argv) > 1:
        roots = [Path(p) for p in sys.argv[1:]]
    else:
        roots = [Path("assets"), Path("user_data"), Path("layout")]
    scanned, modified = walk(roots)
    print(f"\nScanned {scanned} PNG(s), stripped iCCP from {modified}.")
