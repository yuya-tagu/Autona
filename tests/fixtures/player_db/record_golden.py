"""Record golden.json (next to this file) from the PRE-S3e-1 implementation.

ADR-005 S3e-1 moved the player database's data half (`database`, LoadDB,
AddPlayers, DeletePlayer, GetPlayerFromTag, SaveDB) out of the Qt class
src/AutonaPlayerDB.py into the Qt-free src/core/PlayerDB.py.
tests/test_player_db.py replays golden.json against the new class: the same
operations must leave the same database in memory and the same bytes in
user_data/local_players.json. This is the script that recorded it.

It drives the OLD static API (`AutonaPlayerDB.LoadDB()` and friends, with
SetupModel -- the Qt model rebuild and db_updated -- stubbed out), so it
cannot run against the current tree. Run it against a copy of the last
commit before S3e-1 (0f6c276d):

    mkdir ../autona-pre-s3e1
    git archive 0f6c276d src | tar -x -C ../autona-pre-s3e1
    python tests/fixtures/player_db/record_golden.py ../autona-pre-s3e1 \\
        tests/fixtures/player_db/golden.json

Arguments: <old checkout> <output json>. Each scenario runs in a throwaway
working directory holding only the input files below; the old code resolves
./user_data relative to it.

After every step the file's text, whether the legacy CSV is still there and
the whole in-memory database are recorded (json round trip, so dict order is
kept). The file is stored with its line ends folded to "\\n": the old writer
opened it in text mode, i.e. wrote os.linesep, and the replay compares
against the text re-expanded with the running platform's os.linesep. The
recorder checks that the folding is exact before storing it.

The scenarios cover what the move must not change: the load-time
normalisation (a missing `mains` becomes the string "{}", duplicates keep
the first), the save-time field filter and its indent=2 / ensure_ascii
output, every AddPlayers branch (new / merge with the one-element mains
lookup -- including the shared `newMains` list carried across games --,
`general.disable_overwrite`, overwrite with the mains update and its
failure on a string `mains`, None entries, deferRefresh), DeletePlayer of a
present and an absent tag (both save), the first run (an empty "[]" file),
the legacy CSV import and its removal, and GetPlayerFromTag.
"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

INITIAL = [
    {"prefix": "SPN", "gamerTag": "Alpha", "name": "Alpha Real",
     "twitter": "alpha_tw", "country_code": "JP", "state_code": "13",
     "mains": {"ssbu": [["mario", 2], ["luigi", 0, "alt"]]},
     "custom_textbox": "", "seed": 3, "avatar": "http://x/a.png",
     "id": [1, 2]},
    {"prefix": "", "gamerTag": "Bravo", "name": "",
     "mains": {"ssbu": [["peach", "1"]], "sf6": [["ryu", 0]]}},
    {"gamerTag": "Charlie"},
    {"prefix": None, "gamerTag": "Delta", "name": "デルタ",
     "country_code": "US", "mains": {}},
    {"prefix": "SPN", "gamerTag": "Alpha", "name": "duplicate, ignored"},
    {"gamerTag": "Echo", "mains": "{\"ssbu\": [[\"kirby\", 1]]}"},
]
INITIAL_TEXT = json.dumps(INITIAL, ensure_ascii=False)

CSV_FIELDS = ["prefix", "gamerTag", "name", "twitter", "country_code",
              "state_code", "mains", "custom_textbox", "extra"]
CSV_ROWS = [
    ["SPN", "Juliet", "Juliet Real", "jt", "BR", "", '{"ssbu": [["mario", 1]]}',
     "", "dropped on save"],
    ["", "Kilo", "", "", "", "", "not json", "", ""],
    ["SPN", "Juliet", "second row, ignored", "", "", "", "{}", "", ""],
]


def _csv_text():
    buffer = io.StringIO()
    writer = csv.writer(buffer, quotechar="'", lineterminator="\n")
    writer.writerow(CSV_FIELDS)
    writer.writerows(CSV_ROWS)
    return buffer.getvalue()


SCENARIOS = [
    {"name": "load_then_save",
     "files": {"local_players.json": INITIAL_TEXT},
     "steps": [{"op": "load"}, {"op": "save"}]},
    {"name": "add",
     "files": {"local_players.json": INITIAL_TEXT},
     "steps": [
         {"op": "load"},
         {"op": "add", "players": [
             {"gamerTag": "Foxtrot", "prefix": "NEW", "seed": 5,
              "mains": {"ssbu": [["mario"], ["luigi", 3]]}}]},
         {"op": "add", "players": [
             {"prefix": "SPN", "gamerTag": "Alpha", "name": "Alpha Updated",
              "mains": {"ssbu": [["mario"], ["wario"]], "sf6": [["ken"]]}}]},
         {"op": "add", "disable_overwrite": True, "players": [
             {"gamerTag": "Bravo", "prefix": "", "name": "Bravo New",
              "twitter": "bravo_tw"}]},
         {"op": "add", "overwrite": True, "players": [
             {"gamerTag": "Charlie", "mains": {"ssbu": [["pit", 1]]}}]},
         {"op": "add", "overwrite": True, "players": [
             {"prefix": "SPN", "gamerTag": "Alpha",
              "name": "Alpha Overwritten",
              "mains": {"sf6": [["guile", 2]]}}]},
         {"op": "add", "players": [None, {"gamerTag": "Golf"}]},
         {"op": "add", "players": [
             {"gamerTag": "Echo", "mains": {"ssbu": [["kirby"]]}}]},
         {"op": "add", "deferRefresh": True, "players": [
             {"gamerTag": "Hotel", "mains": {"ssbu": [["ness"]]}}]},
         {"op": "save"},
     ]},
    {"name": "delete",
     "files": {"local_players.json": INITIAL_TEXT},
     "steps": [{"op": "load"}, {"op": "delete", "tag": "Bravo"},
               {"op": "delete", "tag": "Nobody"}]},
    {"name": "first_run",
     "files": {},
     "steps": [{"op": "load"},
               {"op": "add", "players": [{"gamerTag": "India"}]}]},
    {"name": "legacy_csv_import",
     "files": {"local_players.csv": _csv_text()},
     "steps": [{"op": "load"}]},
    {"name": "legacy_csv_ignored_next_to_json",
     "files": {"local_players.json": INITIAL_TEXT,
               "local_players.csv": _csv_text()},
     "steps": [{"op": "load"}]},
    {"name": "get_player_from_tag",
     "files": {"local_players.json": INITIAL_TEXT},
     "steps": [{"op": "load"}, {"op": "get", "tag": "alpha"},
               {"op": "get", "tag": "SPN Alpha"}, {"op": "get", "tag": "zulu"},
               {"op": "get", "tag": "DELTA"}]},
]


def _snapshot(directory, database, result=None):
    json_path = directory / "user_data" / "local_players.json"
    text = None
    if json_path.exists():
        raw = json_path.read_bytes()
        decoded = raw.decode("utf-8")
        text = decoded.replace(os.linesep, "\n")
        assert text.replace("\n", os.linesep).encode("utf-8") == raw, \
            "the file's line ends do not fold back exactly"
    snapshot = {
        "file": text,
        "csv_exists": (directory / "user_data" / "local_players.csv").exists(),
        "database": json.loads(json.dumps(database)),
    }
    if result is not None:
        snapshot["result"] = result
    return snapshot


def main(old_checkout, output):
    old_checkout = Path(old_checkout).resolve()
    output = Path(output).resolve()
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(old_checkout))

    from loguru import logger
    logger.remove()

    from src.core.SettingsManager import SettingsManager
    from src.AutonaPlayerDB import AutonaPlayerDB
    assert Path(sys.modules["src"].__file__).resolve().is_relative_to(
        old_checkout), "imported the current tree, not the old checkout"
    # The Qt half (model rebuild + db_updated) is not what is recorded.
    AutonaPlayerDB.SetupModel = staticmethod(lambda: None)

    recorded = []
    for scenario in SCENARIOS:
        work = Path(tempfile.mkdtemp(prefix="autona-playerdb-golden-"))
        try:
            (work / "user_data").mkdir()
            for name, text in scenario["files"].items():
                (work / "user_data" / name).write_bytes(text.encode("utf-8"))
            os.chdir(work)
            AutonaPlayerDB.database = {}
            SettingsManager.settings = {}

            after = []
            for step in json.loads(json.dumps(scenario["steps"])):
                result = None
                op = step["op"]
                if op == "load":
                    AutonaPlayerDB.LoadDB()
                elif op == "save":
                    AutonaPlayerDB.SaveDB()
                elif op == "add":
                    SettingsManager.settings = {"general": {
                        "disable_overwrite": step.get("disable_overwrite",
                                                      False)}}
                    AutonaPlayerDB.AddPlayers(
                        step["players"], overwrite=step.get("overwrite", False),
                        deferRefresh=step.get("deferRefresh", False))
                    SettingsManager.settings = {}
                elif op == "delete":
                    AutonaPlayerDB.DeletePlayer(step["tag"])
                elif op == "get":
                    result = {"value": AutonaPlayerDB.GetPlayerFromTag(
                        step["tag"])}
                else:
                    raise ValueError(op)
                after.append(_snapshot(work, AutonaPlayerDB.database, result))
            recorded.append(dict(scenario, after=after))
        finally:
            os.chdir(PROJECT_ROOT)
            shutil.rmtree(work, ignore_errors=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"scenarios": recorded}, ensure_ascii=False, indent=1)
        + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
