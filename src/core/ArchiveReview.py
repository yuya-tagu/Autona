"""The archive clipper's review (ADR-006 A4): what the operator decided on
the page about the sets the worker found -- which ones are confirmed, their
IN / OUT and which of the results each one is -- and the sets of the export
request made from it (the worker's ``export``).

review.json
-----------
``<work>/<id>/review.json`` (ArchiveJobService reads and writes it, whole:
temp file + os.replace; not the StateManager, ADR-006 D6)::

    {"format": 1,
     "sets": {"2": {"base": [780.0, 1380.0], "confirmed": true,
                    "start": 775.5, "end": 1384.0, "set": {...} | null,
                    "skins": {"c1": {"mario": 3}, "c2": {"luigi": 1}}},
              "5": {"base": [2400.0, 2460.0], "removed": true}}}

One entry per set the operator changed, keyed by the set's ``no`` in the
job's out/sets.json. What an entry leaves out is the worker's: not
confirmed, the set's own ``start`` / ``end``, the set's own result
(``set``), not removed, every character in its first colour. ``set`` null
is "no result" (the clip is ``NN_unmatched``, with no text). ``base`` is
the set's [start, end] in sets.json when the entry was written: an entry
whose set has gone or moved since (a sets.json that is not the one it was
made against) is dropped.

``skins`` (ADR-006 A6) is the colour each character of the set's
thumbnail is drawn in: per side (``c1`` / ``c2``), a codename of the
result the set is given (its ``autona`` ``c1`` / ``c2``,
ArchiveResults.NAMESPACE) and the number of its skin (the number in its
asset files' names, as GameAssetCatalog.GameCharacterSkins lists them --
not a skin combo's row). 0 is the default and is never kept, nor a side
with none. Only the thumbnails read it (``Skins``): it is not in
``ExportSets`` / ``ReviewedSets``, the worker's requests. A codename the
result no longer names (the set given another result) goes with it.

A removed set (``removed`` true: one the operator does not want, typically
a block the worker found no result for) is ``{"removed": true}`` alone --
what else its entry held goes --, is never confirmed (no export, no
thumbnail) and is left out of the titles' preview (``ReviewedSets``), and
holds no result: its own may be given to another set.

A set may be given its own result or one of sets.json's ``unused_sets``
(``Candidates``: the results the worker found no set for), and two sets
never get the same one. Two results are the same when their ``ResultKey``
is (every comparison here goes through it; the page's ``canonical`` gives
the same string).

A range is at least ``MIN_CLIP`` long, and so is what of it lies before the
video's end less ``END_MARGIN`` (the worker cuts an end past the video at
the end).

Reading (``Normalize``) never fails: a file that is not this format is no
review (the next save writes over it), and an entry keeps what still fits
sets.json (a range, an assignment and a skin are dropped one by one; a
range that no longer fits goes with the entry's confirmation, so an export
never cuts the worker's range in its place unasked). A save (``Check``) is
strict:
anything that does not fit is refused (ReviewError, Japanese) and nothing
is written.

Pure: no file, no lock, no Qt; nothing happens at import.
"""
import json
import math
import re

FORMAT = 1
FILE = "review.json"

# vodsplit export.MIN_CLIP: the worker refuses a shorter range.
MIN_CLIP = 1.0
# The precision a range from the page is kept to (a frame is 1/60 s).
DECIMALS = 2
# What of a range must lie before the video's end, less this: the worker
# cuts an end past the end at the length it probes, which may differ from
# sets.json's duration in the last digits (the page keeps to it too).
END_MARGIN = 0.05
ENTRY_KEYS = frozenset({"confirmed", "start", "end", "set", "removed",
                        "skins"})
# The sides of a result's characters (its autona's c1 / c2) and where the
# codenames are (ArchiveResults.NAMESPACE: not imported -- ArchiveResults
# imports the job service, which imports this module).
SIDES = ("c1", "c2")
NAMESPACE = "autona"
_NUMBER = re.compile(r"[1-9][0-9]{0,5}")


class ReviewError(Exception):
    """A review that does not fit the job's sets: the Japanese message the
    page shows as it is."""


def Empty():
    return {"format": FORMAT, "sets": {}}


def _Seconds(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def Rows(sets):
    """{no: row} of sets.json's sets that can be reviewed, in its order: a
    whole ``no`` >= 1 (the first row of a number) with a range
    0 <= start < end."""
    rows = {}
    items = sets.get("sets") if isinstance(sets, dict) else None
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        no = row.get("no")
        if type(no) is not int or no < 1 or no in rows:
            continue
        start, end = row.get("start"), row.get("end")
        if not (_Seconds(start) and _Seconds(end)) or start < 0 \
                or end <= start:
            continue
        rows[no] = row
    return rows


def Duration(sets):
    """The video's length sets.json gives, or None."""
    value = sets.get("duration") if isinstance(sets, dict) else None
    return value if _Seconds(value) and value > 0 else None


def _Plain(value):
    """`value` with every whole float as its int (JSON's 1.0 and 1 are one
    number: the page reads both as 1)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(key): _Plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_Plain(item) for item in value]
    return value


def ResultKey(result):
    """`result` (a result set, or None for none) as one string: two
    results are the same when their keys are. JSON with sorted keys and
    whole numbers as integers, as the page's ``canonical`` makes it."""
    return json.dumps(_Plain(result), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def Candidates(sets):
    """The results a set may be given: each set's own and sets.json's
    unused_sets, once each."""
    found, seen = [], set()
    unused = sets.get("unused_sets") if isinstance(sets, dict) else None
    for item in [row.get("set") for row in Rows(sets).values()] + (
            unused if isinstance(unused, list) else []):
        if not isinstance(item, dict):
            continue
        key = ResultKey(item)
        if key not in seen:
            seen.add(key)
            found.append(item)
    return found


def _CandidateKeys(candidates):
    """The ResultKey of each of `candidates`, made once per review."""
    return {ResultKey(item) for item in candidates}


def _Candidate(result, keys):
    """Whether `result` may be given: None (no result) or one whose key is
    in `keys` (`_CandidateKeys` of the candidates)."""
    return result is None or (isinstance(result, dict)
                              and ResultKey(result) in keys)


def _RowOf(rows, key):
    if not isinstance(key, str) or not _NUMBER.fullmatch(key):
        return None
    return rows.get(int(key))


def _RangeFits(start, end, duration):
    """Whether [start, end] is a range the worker cuts: at least MIN_CLIP,
    and at least MIN_CLIP up to the video's end less END_MARGIN (the worker
    cuts an end past the end at the end)."""
    if start < 0 or end - start < MIN_CLIP:
        return False
    return duration is None or (
        min(end, duration - END_MARGIN) - start >= MIN_CLIP)


def Removed(review, no):
    """Whether set `no` is removed in `review`."""
    return review["sets"].get(str(no), {}).get("removed") is True


def Assigned(review, row):
    """The result set `row` (a sets.json row) is given in `review`: its
    entry's, else its own; None for no result, and for a removed set (it
    holds none)."""
    if Removed(review, row["no"]):
        return None
    entry = review["sets"].get(str(row["no"]), {})
    result = entry["set"] if "set" in entry else row.get("set")
    return result if isinstance(result, dict) else None


def _Codenames(result, side):
    """The codenames result set `result` (or None) names on `side` (its
    autona's c1 / c2)."""
    extra = result.get(NAMESPACE) if isinstance(result, dict) else None
    names = extra.get(side) if isinstance(extra, dict) else None
    return {name for name in names if isinstance(name, str)} \
        if isinstance(names, list) else set()


def _Skins(raw, result, refuse=None):
    """An entry's ``skins`` `raw` as kept for a set given result `result`:
    {side: {codename: number}} -- a side of SIDES, a codename `result`
    names on it, a whole number >= 0 (not a bool) --, 0 and an empty side
    left out. What does not fit is dropped, or with `refuse` (the set's
    "セット #N " of Check's messages) refused (ReviewError)."""
    def bad(message):
        if refuse is not None:
            raise ReviewError(f"{refuse}{message}")

    if not isinstance(raw, dict):
        bad("の skins はオブジェクトにしてください。")
        return {}
    kept = {}
    for side, chosen in raw.items():
        if side not in SIDES:
            bad(f"の skins に使えない側があります: {side}")
            continue
        if not isinstance(chosen, dict):
            bad(f"の skins の {side} はオブジェクトにしてください。")
            continue
        names = _Codenames(result, side)
        for codename, number in chosen.items():
            if codename not in names:
                bad(f"でカラーを選んだキャラクター（{codename}）は、"
                    "割り当てた結果にいません。")
                continue
            if type(number) is not int or number < 0:
                bad(f"のカラー（{codename}）は 0 以上の整数にしてください。")
                continue
            if number != 0:
                kept.setdefault(side, {})[codename] = number
    return {side: kept[side] for side in SIDES if side in kept}


def Skins(review, no):
    """The colours set `no`'s characters are drawn in in `review` (its
    entry's ``skins``: {side: {codename: number}}, {} for none). What a
    thumbnail draws (ADR-006 A6); never part of the worker's requests."""
    skins = review["sets"].get(str(no), {}).get("skins")
    return {side: dict(chosen) for side, chosen in skins.items()} \
        if isinstance(skins, dict) else {}


def _Twice(entries, rows):
    """The numbers of the sets whose result another set has too."""
    review = {"sets": entries}
    holders = {}
    for no, row in rows.items():
        result = Assigned(review, row)
        if result is not None:
            holders.setdefault(ResultKey(result), []).append(no)
    return {no for group in holders.values() if len(group) > 1
            for no in group}


def _Changed(entries, rows):
    """The sets of `_Twice` that the review gives a result (not the
    worker): a sets.json that gives two sets one result is not the
    operator's to fix."""
    return sorted(no for no in _Twice(entries, rows)
                  if "set" in entries.get(str(no), {}))


def Normalize(raw, sets):
    """review.json `raw` as it applies to sets.json `sets` -> {"format",
    "sets": {no: entry}} (the entries without ``base``). Never raises."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT \
            or not isinstance(raw.get("sets"), dict):
        return Empty()
    rows = Rows(sets)
    duration = Duration(sets)
    keys = _CandidateKeys(Candidates(sets))
    entries = {}
    # Each set's skins, kept once its result is known (below).
    skins = {}
    for key, value in raw["sets"].items():
        row = _RowOf(rows, key)
        if row is None or not isinstance(value, dict) \
                or value.get("base") != [row["start"], row["end"]]:
            continue
        # A removed set keeps nothing else (no confirmation, range, result).
        if value.get("removed") is True:
            entries[str(row["no"])] = {"removed": True}
            continue
        entry = {}
        start = value.get("start", row["start"])
        end = value.get("end", row["end"])
        # A range that no longer fits goes, and its confirmation with it:
        # the set is the worker's range again, unconfirmed.
        if _Seconds(start) and _Seconds(end) \
                and _RangeFits(start, end, duration):
            if value.get("confirmed") is True:
                entry["confirmed"] = True
            if start != row["start"]:
                entry["start"] = start
            if end != row["end"]:
                entry["end"] = end
        if "set" in value:
            result = value["set"]
            if _Candidate(result, keys) \
                    and ResultKey(result) != ResultKey(row.get("set")):
                entry["set"] = result
        if "skins" in value:
            skins[row["no"]] = value["skins"]
        if entry:
            entries[str(row["no"])] = entry
    # Two sets given one result: the review's assignments go (the sets keep
    # the worker's), until none is given twice.
    changed = _Changed(entries, rows)
    while changed:
        for no in changed:
            entry = entries[str(no)]
            del entry["set"]
            if not entry:
                del entries[str(no)]
        changed = _Changed(entries, rows)
    # The skins of the codenames the result each set ends up with names.
    for no, raw in skins.items():
        kept = _Skins(raw, Assigned({"sets": entries}, rows[no]))
        if kept:
            entries.setdefault(str(no), {})["skins"] = kept
    return {"format": FORMAT, "sets": entries}


def Check(body, sets):
    """A save's review `body` ({"sets": {no: {"confirmed"?, "start"?,
    "end"?, "set"?, "removed"?, "skins"?}}}) against sets.json `sets` ->
    the review (as Normalize gives it: a removed set's entry is {"removed":
    true} alone, what else it gave dropped unchecked; a skin 0 left out).
    Raises ReviewError."""
    if not isinstance(body, dict) or not isinstance(body.get("sets"), dict):
        raise ReviewError(
            'レビューの形式が違います（"sets" のオブジェクトがありません）。')
    rows = Rows(sets)
    duration = Duration(sets)
    keys = _CandidateKeys(Candidates(sets))
    entries = {}
    for key, value in body["sets"].items():
        row = _RowOf(rows, key)
        if row is None:
            raise ReviewError(f"セット {key} はこのジョブにありません。")
        where = f"セット #{row['no']} "
        if not isinstance(value, dict):
            raise ReviewError(f"{where}の内容がオブジェクトではありません。")
        unknown = sorted(str(name) for name in set(value) - ENTRY_KEYS)
        if unknown:
            raise ReviewError(f"{where}に使えない項目があります: {', '.join(unknown)}")
        for flag in ("confirmed", "removed"):
            if flag in value and not isinstance(value[flag], bool):
                raise ReviewError(f"{where}の {flag} は true か false にしてください。")
        if value.get("removed"):
            # Nothing else of it is kept, nor checked: its range is never
            # cut (the worker's own may be one too short to be).
            entries[str(row["no"])] = {"removed": True}
            continue
        entry = {}
        if value.get("confirmed"):
            entry["confirmed"] = True
        edges = {}
        for edge in ("start", "end"):
            given = value.get(edge, row[edge])
            if not _Seconds(given):
                raise ReviewError(f"{where}の {edge} は秒数にしてください。")
            edges[edge] = (given if given == row[edge]
                           else round(given, DECIMALS))
        start, end = edges["start"], edges["end"]
        if not _RangeFits(start, end, duration):
            raise ReviewError(
                f"{where}の区間（{start:g} - {end:g} 秒）は、動画の中で "
                f"{MIN_CLIP:g} 秒以上にしてください。")
        if start != row["start"]:
            entry["start"] = start
        if end != row["end"]:
            entry["end"] = end
        if "set" in value:
            result = value["set"]
            if not _Candidate(result, keys):
                raise ReviewError(f"{where}に割り当てた結果は、このジョブの結果にありません。")
            if ResultKey(result) != ResultKey(row.get("set")):
                entry["set"] = result
        if "skins" in value:
            given = value["set"] if "set" in value else row.get("set")
            skins = _Skins(value["skins"], given, refuse=where)
            if skins:
                entry["skins"] = skins
        if entry:
            entries[str(row["no"])] = entry
    changed = _Changed(entries, rows)
    if changed:
        raise ReviewError(
            "同じ結果を 2 つのセットに割り当てています（"
            + "・".join(f"#{no}" for no in sorted(_Twice(entries, rows)))
            + "）。")
    return {"format": FORMAT, "sets": entries}


def Stored(review, sets):
    """review.json's content for `review` (Check's): each entry with its
    set's ``base``."""
    rows = Rows(sets)
    return {"format": FORMAT, "sets": {
        key: {"base": [rows[int(key)]["start"], rows[int(key)]["end"]],
              **entry}
        for key, entry in review["sets"].items()}}


def _Reviewed(review, no, row):
    """Set `row` as the export request has it (ExportSets' shape: the
    worker's, so without the set's ``skins`` -- the thumbnails read them
    with ``Skins``)."""
    entry = review["sets"].get(str(no), {})
    starts = row.get("game_starts")
    return {
        "no": no,
        "start": entry.get("start", row["start"]),
        "end": entry.get("end", row["end"]),
        "title": None, "description": None,
        "set": Assigned(review, row),
        "game_starts": [t for t in starts if _Seconds(t)]
        if isinstance(starts, list) else [],
    }


def ReviewedSets(review, sets):
    """Every set that can be reviewed and is not removed, confirmed or not,
    as ExportSets has a confirmed one (ADR-006 A6: the titles' preview shows
    what an export would name each set)."""
    return [_Reviewed(review, no, row) for no, row in Rows(sets).items()
            if not Removed(review, no)]


def ExportSets(review, sets):
    """The sets of the export request (vodsplit export.validate_edits): the
    confirmed ones (never a removed one) in sets.json's order, with their
    range and result as reviewed. Titles and descriptions are null: the
    worker makes them from its template for the result and the range (a set
    with no result is ``NN_unmatched``, with no text)."""
    return [_Reviewed(review, no, row) for no, row in Rows(sets).items()
            if not Removed(review, no)
            and review["sets"].get(str(no), {}).get("confirmed")]
