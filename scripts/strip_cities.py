"""Generate a slim country+states JSON by stripping out city arrays.

Autona only uses country and state information at startup
(src/core/CountryCatalog.py); parsing the city arrays would gate a ~5 s
JSON parse on every launch (source file ≈ 46 MB). Dropping cities
shrinks the file by roughly an order of magnitude. The city -> region
lookup reads its own pre-computed table instead
(assets/city_to_state.json, scripts/gen_city_to_state.py).

Run once:
    python scripts/strip_cities.py

Output:
    assets/countries+states.json   (cities arrays removed)
"""

from __future__ import annotations

from pathlib import Path

import orjson

SRC = Path("assets/countries+states+cities.json")
DST = Path("assets/countries+states.json")


def strip(src: Path = SRC, dst: Path = DST) -> None:
    if not src.exists():
        raise SystemExit(f"Source not found: {src}")
    data = orjson.loads(src.read_bytes())
    for country in data:
        for state in country.get("states") or []:
            state.pop("cities", None)
    dst.write_bytes(
        orjson.dumps(data, option=orjson.OPT_INDENT_2))
    print(f"{src} ({src.stat().st_size:,} bytes)")
    print(f"  → {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == "__main__":
    strip()
