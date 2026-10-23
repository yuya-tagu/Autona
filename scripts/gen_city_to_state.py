"""Pre-compute the city/state → state-code lookup used by FindState.

Start.gg leaves `location.state` null for most Japanese users and puts the
region in `location.city` instead ("Tokyo", "Shibuya-ku", ...), so the
region field on a scoreboard stays empty unless the city string is resolved
back to a prefecture. The old runtime implementation did that by scanning
the country's state and city tables linearly for every entrant, which meant
parsing the 46 MB countries+states+cities.json at startup.

This script moves that work to build time: one flat dict per country,
`{normalized key: state iso2}`, so the runtime side is a lazy load plus an
O(1) lookup. Keys per country, in insertion order (first write wins):

    1. state names            "tokyo"      -> "13"
    2. state iso2 codes       "ca"         -> "CA"
    3. city names             "osaka"      -> "27"
    4. city names with a Japanese suffix stripped
                              "shibuya"    -> "13"   (from "Shibuya-ku")
    5. Japanese prefecture names, JP only
                              "東京" / "東京都" -> "13"

A state name or code (1, 2) always wins -- it is an unambiguous answer to
"which region". A city-derived key (3, 4) is only kept when every state
claiming it agrees: "Kawasaki" is a city in six prefectures, and guessing
one of them would put a confidently wrong region on a broadcast, which is
worse than the blank field this restores. The unambiguous full name
("kawasaki-shi") still resolves, so a user who writes it out is served.
Exact names and aliases are weighed together, because "Ota" is both a city
in Gunma and what Tokyo's "Ōta-ku" reduces to.

The country key is the same code CountryCatalog.Load (src/core) derives
(iso2, or the initials of the country name when iso2 is numeric), because
that is what ends up in playerData["country_code"].

Run once:
    python scripts/gen_city_to_state.py

Output:
    assets/city_to_state.json   (no indent; size matters, it ships)
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import orjson

SRC = Path("assets/countries+states+cities.json")
DST = Path("assets/city_to_state.json")

# Japanese municipality/prefecture suffixes. Stripping them turns
# "Shibuya-ku" into "Shibuya" and "Fukuoka-ken" into "Fukuoka", which is
# what users actually type / what Start.gg stores. CountryCatalog
# applies the same pattern at lookup time; tests/test_find_state.py pins
# that the two stay identical. "to"/"fu"/"do" are deliberately absent --
# two-letter suffixes strip real name fragments far too eagerly.
SUFFIX_RE = re.compile(r"[\s-](shi|ku|machi|cho|gun|city|ken|prefecture)$")

# Japanese prefecture names, keyed by the iso2 code the dataset uses for
# JP states ("01".."47"). Both the bare name and the 都/道/府/県 form are
# registered; Hokkaidō only exists in the suffixed form.
JP_PREFECTURES = {
    "01": ["北海道"],
    "02": ["青森", "青森県"],
    "03": ["岩手", "岩手県"],
    "04": ["宮城", "宮城県"],
    "05": ["秋田", "秋田県"],
    "06": ["山形", "山形県"],
    "07": ["福島", "福島県"],
    "08": ["茨城", "茨城県"],
    "09": ["栃木", "栃木県"],
    "10": ["群馬", "群馬県"],
    "11": ["埼玉", "埼玉県"],
    "12": ["千葉", "千葉県"],
    "13": ["東京", "東京都"],
    "14": ["神奈川", "神奈川県"],
    "15": ["新潟", "新潟県"],
    "16": ["富山", "富山県"],
    "17": ["石川", "石川県"],
    "18": ["福井", "福井県"],
    "19": ["山梨", "山梨県"],
    "20": ["長野", "長野県"],
    "21": ["岐阜", "岐阜県"],
    "22": ["静岡", "静岡県"],
    "23": ["愛知", "愛知県"],
    "24": ["三重", "三重県"],
    "25": ["滋賀", "滋賀県"],
    "26": ["京都", "京都府"],
    "27": ["大阪", "大阪府"],
    "28": ["兵庫", "兵庫県"],
    "29": ["奈良", "奈良県"],
    "30": ["和歌山", "和歌山県"],
    "31": ["鳥取", "鳥取県"],
    "32": ["島根", "島根県"],
    "33": ["岡山", "岡山県"],
    "34": ["広島", "広島県"],
    "35": ["山口", "山口県"],
    "36": ["徳島", "徳島県"],
    "37": ["香川", "香川県"],
    "38": ["愛媛", "愛媛県"],
    "39": ["高知", "高知県"],
    "40": ["福岡", "福岡県"],
    "41": ["佐賀", "佐賀県"],
    "42": ["長崎", "長崎県"],
    "43": ["熊本", "熊本県"],
    "44": ["大分", "大分県"],
    "45": ["宮崎", "宮崎県"],
    "46": ["鹿児島", "鹿児島県"],
    "47": ["沖縄", "沖縄県"],
}


def normalize(value: str) -> str:
    """Same normalization as remove_accents_lower (src/core/TextHelper.py,
    which CountryCatalog.FindState uses).

    Kept as a local copy so this build-time tool never imports src. The two
    must stay identical: the runtime looks up keys produced here.

    The trailing strip() is the one addition, and it is not optional: the
    source data contains padded names ("Barisal "), while FindState strips
    each comma-separated part before normalizing it. Without it those keys
    could never be hit.
    """
    nfkd_form = unicodedata.normalize("NFKD", value)
    return "".join(
        c for c in nfkd_form if not unicodedata.combining(c)).lower().strip()


def country_code(country: dict) -> str | None:
    """The code CountryCatalog.Load derives (playerData["country_code"])."""
    iso2 = country.get("iso2")
    if not iso2:
        return None
    if iso2.isdigit():
        return "".join(
            word[0] for word in re.split(r"\s+|-", country.get("name") or "")
            if word)
    return iso2


def build_maps(data: list, dropped: dict | None = None) -> dict:
    """Build {country code: {normalized key: state iso2}} from the dataset.

    `dropped`, when given, receives {country code: [ambiguous keys]} -- the
    city names that map to more than one state and are therefore left out.
    """
    maps: dict[str, dict[str, str]] = {}

    for country in data:
        code = country_code(country)
        if not code:
            continue

        states = [s for s in (country.get("states") or []) if s.get("iso2")]
        entries = maps.setdefault(code, {})
        ambiguous: set[str] = set()

        def add(key: str, state_code: str):
            # First write wins. Only the state pass and the JP prefecture
            # table write more than once per key, and both are ordered so
            # that the earlier writer is the one that should win.
            if key and key not in entries:
                entries[key] = state_code

        for state in states:
            add(normalize(state.get("name") or ""), state["iso2"])
            add(normalize(state["iso2"]), state["iso2"])

        # City names and their suffix-stripped aliases are judged together:
        # "Ota" is a city in Gunma *and* the alias of Tokyo's "Ōta-ku", and
        # picking either would be a coin flip.
        claims: dict[str, set[str]] = {}
        for state in states:
            for city in state.get("cities") or []:
                name = normalize(city.get("name") or "")
                if not name:
                    continue
                claims.setdefault(name, set()).add(state["iso2"])
                alias = SUFFIX_RE.sub("", name)
                if alias and alias != name:
                    claims.setdefault(alias, set()).add(state["iso2"])

        for key, codes in claims.items():
            if key in entries:
                # A state name or code already answers this key.
                continue
            if len(codes) > 1:
                ambiguous.add(key)
                continue
            add(key, next(iter(codes)))

        if country.get("iso2") == "JP":
            known = {s["iso2"] for s in states}
            for state_code, names in JP_PREFECTURES.items():
                if state_code not in known:
                    continue
                for name in names:
                    # The hand-written prefecture table is authoritative:
                    # it overrides an ambiguity found among city names.
                    key = normalize(name)
                    ambiguous.discard(key)
                    add(key, state_code)

        if dropped is not None and ambiguous:
            dropped[code] = sorted(ambiguous)

    return maps


def generate(src: Path = SRC, dst: Path = DST) -> None:
    if not src.exists():
        raise SystemExit(f"Source not found: {src}")
    data = orjson.loads(src.read_bytes())
    dropped: dict[str, list[str]] = {}
    maps = build_maps(data, dropped=dropped)
    dst.write_bytes(orjson.dumps(maps))
    print(f"{src} ({src.stat().st_size:,} bytes)")
    print(f"  -> {dst} ({dst.stat().st_size:,} bytes, "
          f"{len(maps)} countries, "
          f"{sum(len(v) for v in maps.values()):,} keys)")
    print(f"  ambiguous keys dropped: "
          f"{sum(len(v) for v in dropped.values()):,} total, "
          f"{len(dropped.get('JP', []))} in JP")


if __name__ == "__main__":
    generate()
