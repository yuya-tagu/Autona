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
                    "start": 775.5, "end": 1384.0, "set": {...} | null}}}

One entry per set the operator changed, keyed by the set's ``no`` in the
job's out/sets.json. What an entry leaves out is the worker's: not
confirmed, the set's own ``start`` / ``end``, the set's own result
(``set``). ``set`` null is "no result" (the clip is ``NN_unmatched``, with
no text). ``base`` is the set's [start, end] in sets.json when the entry was
written: an entry whose set has gone or moved since (a sets.json that is not
the one it was made against) is dropped.

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
sets.json (a range and an assignment are dropped one by one; a range that
no longer fits goes with the entry's confirmation, so an export never cuts
the worker's range in its place unasked). A save (``Check``) is strict:
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
ENTRY_KEYS = frozenset({"confirmed", "start", "end", "set"})
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
        if isinstance(item, dict) and ResultKey(item) not in seen:
            seen.add(ResultKey(item))
            found.append(item)
    return found


def _Candidate(result, candidates):
    """Whether `result` may be given: None (no result) or one of
    `candidates`."""
    return result is None or (isinstance(result, dict) and ResultKey(result)
                              in {ResultKey(item) for item in candidates})


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


def Assigned(review, row):
    """The result set `row` (a sets.json row) is given in `review`: its
    entry's, else its own; None for no result."""
    entry = review["sets"].get(str(row["no"]), {})
    result = entry["set"] if "set" in entry else row.get("set")
    return result if isinstance(result, dict) else None


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
    candidates = Candidates(sets)
    entries = {}
    for key, value in raw["sets"].items():
        row = _RowOf(rows, key)
        if row is None or not isinstance(value, dict) \
                or value.get("base") != [row["start"], row["end"]]:
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
            if _Candidate(result, candidates) \
                    and ResultKey(result) != ResultKey(row.get("set")):
                entry["set"] = result
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
    return {"format": FORMAT, "sets": entries}


def Check(body, sets):
    """A save's review `body` ({"sets": {no: {"confirmed"?, "start"?,
    "end"?, "set"?}}}) against sets.json `sets` -> the review (as
    Normalize gives it). Raises ReviewError."""
    if not isinstance(body, dict) or not isinstance(body.get("sets"), dict):
        raise ReviewError(
            'レビューの形式が違います（"sets" のオブジェクトがありません）。')
    rows = Rows(sets)
    duration = Duration(sets)
    candidates = Candidates(sets)
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
        entry = {}
        if "confirmed" in value:
            if not isinstance(value["confirmed"], bool):
                raise ReviewError(f"{where}の confirmed は true か false にしてください。")
            if value["confirmed"]:
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
            if not _Candidate(result, candidates):
                raise ReviewError(f"{where}に割り当てた結果は、このジョブの結果にありません。")
            if ResultKey(result) != ResultKey(row.get("set")):
                entry["set"] = result
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


def ExportSets(review, sets):
    """The sets of the export request (vodsplit export.validate_edits): the
    confirmed ones in sets.json's order, with their range and result as
    reviewed. Titles and descriptions are null: the worker makes them from
    its template for the result and the range (a set with no result is
    ``NN_unmatched``, with no text)."""
    chosen = []
    for no, row in Rows(sets).items():
        entry = review["sets"].get(str(no), {})
        if not entry.get("confirmed"):
            continue
        starts = row.get("game_starts")
        chosen.append({
            "no": no,
            "start": entry.get("start", row["start"]),
            "end": entry.get("end", row["end"]),
            "title": None, "description": None,
            "set": Assigned(review, row),
            "game_starts": [t for t in starts if _Seconds(t)]
            if isinstance(starts, list) else [],
        })
    return chosen
