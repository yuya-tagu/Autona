"""Re-check the S4AC4_* tables of tests/test_board_operations.py.

ADR-005 S4ac-4 removed the game tracker. Its rows (`score.{n}.stages`) were
in what 反映 published after many recorded steps, and the recordings hold
only digests of those deltas, so the tables give this tree's digests for
the steps whose publication or cycles moved. This script is how they were
checked, and can be run again: the publication of every step a table lists
has to be what the last tree before S4ac-4 published, with the game
tracker's rows taken out -- and nothing else.

Run it from the project root against a copy of that tree (8b4b2fdf; the
merge 2d47c1db has the same tree):

    mkdir ../autona-pre-s4ac4
    git archive 8b4b2fdf | tar -x -C ../autona-pre-s4ac4
    python tests/fixtures/board_operations/verify_s4ac4.py ../autona-pre-s4ac4

It runs tests/test_board_operations.py twice -- in the old copy and in this
tree -- with this file loaded as a pytest plugin (`-p verify_s4ac4`). Both
runs must pass: then the old run's output is the recording with every
earlier layer applied, which is the `golden` this tree's _assertReplayed
receives. The plugin keeps the text behind every digest the test module
takes (its `digest`), and this tree's run also keeps each table and the
golden it is applied to. Then, for every field a table lists:

- `published` / `publishedRows` / `during`: the delta rows behind this
  tree's digest equal the rows behind the old digest with every row under
  `score.{n}.stages` taken out (also from inside a value that adds or
  replaces a whole board, and dropping a delta that held nothing else);
  the delta count in `published` / `during` follows. The one step where the
  rows differ in form (WHOLE_BOARD below: DeepDiff reports the board as
  replaced, see the note above the tables) must be a single `values_changed`
  row for the board that agrees with every one of the old rows;
- `cycles`: printed old -> new (the comment above each entry says why);
- the field really moved: a table holds no value the old tree had already.

Exit status 0 when everything holds. Nothing here is imported by the tests.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/test_board_operations.py"
DUMP_ENV = "AUTONA_S4AC4_DUMP"
ROW_FIELDS = ("published", "publishedRows", "during")
# (section of _assertReplayed, step): the publication that is one
# `values_changed` row for the whole board where the old tree had a row per
# field (DeepDiff's threshold_to_diff_deeper, the note above the tables).
WHOLE_BOARD = {("set load pre-pass", "follow a set without keeping mains")}


# -- the plugin (loaded into both pytest runs) -------------------------------

_texts = {}      # digest -> what it digested
_tables = []     # this tree: [{"section", "changes", "golden"}]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    module = item.module
    if not module.__name__.endswith("test_board_operations") \
            or getattr(module, "_s4ac4Spied", False):
        return
    module._s4ac4Spied = True

    digest = module.digest

    def spiedDigest(value):
        result = digest(value)
        _texts[result] = value
        return result
    module.digest = spiedDigest

    replayed = getattr(module, "_assertReplayed", None)    # this tree only
    if replayed is None:
        return

    def spiedReplayed(app, section, recorded, raws, keyOrders, golden,
                      changes):
        _tables.append({
            "section": section, "changes": json.loads(json.dumps(changes)),
            "golden": [{field: entry[field]
                        for field in ("step", "cycles") + ROW_FIELDS
                        if field in entry} for entry in golden]})
        return replayed(app, section, recorded, raws, keyOrders, golden,
                        changes)
    module._assertReplayed = spiedReplayed


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get(DUMP_ENV)
    if path:
        Path(path).write_text(json.dumps(
            {"texts": _texts, "tables": _tables}, ensure_ascii=False),
            encoding="utf-8")


# -- the comparison ----------------------------------------------------------

def _isRow(row):
    path = row.get("path") or []
    return len(path) >= 3 and path[0] == "score" and path[2] == "stages"


def _withoutRowsInside(row):
    # A row adding / replacing a whole board, or the score root, carries the
    # game rows inside its value.
    path, value = row.get("path") or [], row.get("value")
    if not isinstance(value, dict) or not path or path[0] != "score":
        return row
    if len(path) == 2:
        value = {k: v for k, v in value.items() if k != "stages"}
    elif len(path) == 1:
        value = {n: ({k: v for k, v in board.items() if k != "stages"}
                     if isinstance(board, dict) else board)
                 for n, board in value.items()}
    else:
        return row
    return dict(row, value=value)


def withoutTheRows(deltas):
    result = []
    for delta in deltas:
        kept = [_withoutRowsInside(row) for row in delta if not _isRow(row)]
        if kept or not delta:
            result.append(kept)
    return result


def _agrees(board, row):
    """An old per-field row of `score.{n}` against the whole board's new
    value: what it set is there, what it removed is not. (A row's path
    holds an int where the state's dict had an int key; the value, read
    back from JSON, has it as a string.)"""
    node = board
    *parents, leaf = [str(key) for key in row["path"][2:]]
    for key in parents:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    if row["action"] == "dictionary_item_removed":
        return not (isinstance(node, dict) and leaf in node)
    return isinstance(node, dict) and leaf in node \
        and node[leaf] == row["value"]


def _checkRows(old, new, wholeBoard):
    stripped = withoutTheRows(old)
    if stripped == new:
        return None
    if not wholeBoard:
        return "rows differ beyond the game rows"
    rows = [row for delta in new for row in delta]
    if len(rows) != 1 or rows[0]["action"] != "values_changed" \
            or len(rows[0]["path"]) != 2:
        return "not one row replacing the board"
    board = rows[0]["value"]
    wrong = [row["path"] for delta in stripped for row in delta
             if not _agrees(board, row)]
    return f"the board row disagrees at {wrong}" if wrong else None


def compare(old, new):
    problems = []
    for table in new["tables"]:
        section, changes = table["section"], table["changes"]
        recorded = {entry["step"]: entry for entry in table["golden"]}
        print(f"== {section}: {len(changes)} step(s)")
        for step, change in changes.items():
            before = recorded.get(step)
            if before is None:
                problems.append(f"{section} / {step}: no such recorded step")
                continue
            notes = []
            for field, value in change.items():
                if before.get(field) == value:
                    problems.append(f"{section} / {step}: {field} did not "
                                    "move (nothing to list)")
                    continue
                if field == "cycles":
                    notes.append(f"cycles {before[field]} -> {value}")
                    continue
                if field not in ROW_FIELDS:
                    problems.append(f"{section} / {step}: unexpected field "
                                    f"{field}")
                    continue
                oldHash = before[field] if field == "publishedRows" \
                    else before[field][2]
                newHash = value if field == "publishedRows" else value[2]
                if oldHash not in old["texts"] or newHash not in new["texts"]:
                    problems.append(f"{section} / {step}: {field} digest "
                                    "not seen in a run")
                    continue
                oldRows = json.loads(old["texts"][oldHash])
                newRows = json.loads(new["texts"][newHash])
                why = _checkRows(oldRows, newRows,
                                 (section, step) in WHOLE_BOARD)
                if why is None and field != "publishedRows" and (
                        value[0] != len(newRows) or value[1] != before[field][1]):
                    why = "the delta count does not follow the rows"
                if why is not None:
                    problems.append(f"{section} / {step}: {field}: {why}")
                notes.append(field)
            print(f"   {step}: {', '.join(notes)}")
    return problems


def _run(cwd, plugin_dir, dump):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(plugin_dir), env.get("PYTHONPATH")]))
    env[DUMP_ENV] = str(dump)
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider",
         "-p", Path(__file__).stem], cwd=cwd, env=env,
        capture_output=True, text=True)
    tail = [line for line in result.stdout.splitlines() if line.strip()][-1:]
    print(f"{cwd}: exit {result.returncode} {tail}")
    if result.returncode != 0:
        print(result.stdout[-4000:], result.stderr[-2000:])
    return result.returncode


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    oldTree = Path(argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        oldDump, newDump = Path(tmp) / "old.json", Path(tmp) / "new.json"
        here = Path(__file__).resolve().parent
        if _run(oldTree, here, oldDump) or _run(PROJECT_ROOT, here, newDump):
            print("a run failed: nothing compared")
            return 1
        old = json.loads(oldDump.read_text(encoding="utf-8"))
        new = json.loads(newDump.read_text(encoding="utf-8"))
    if not new["tables"]:
        print("this tree's run passed no table to _assertReplayed")
        return 1
    problems = compare(old, new)
    for problem in problems:
        print("PROBLEM:", problem)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
