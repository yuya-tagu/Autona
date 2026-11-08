"""The rows a state change goes out as, held to what a client makes of them.

web/src/stateDelta.cases.json is read by this file and by
web/src/stateDelta.test.js, as characterPick.cases.json is by the Top 8 page's
tests. Here every case's rows must be what StateManager.SaveState publishes,
as they go over Socket.IO (SocketioJson); there, applied one by one, they must
turn the case's first state into its last. A case of more than two states is
several revisions: the web server joins them in one message, without a mark of
where one ends (AutonaWebServer.on_program_state_update).

Regenerating the rows after a change of the diff's options is meant to be seen
in a review: a failure below prints what is published now.
"""
import json
import os
from pathlib import Path

import pytest

from src.AutonaWebServer import SocketioJson
from src.core.StateManager import StateManager as SM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "web" / "src" / "stateDelta.cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))

# What web/src/stateDelta.js has an answer for. An action outside this set is
# written to the path's last key like a new value, which is how a removed
# element used to come back.
HANDLED_ACTIONS = {
    "values_changed", "type_changes",
    "dictionary_item_added", "dictionary_item_removed",
    "iterable_item_added", "iterable_item_removed",
}
# DeepDiff's Delta leaves a value out when it thinks it can be derived, and the
# flat rows say this in its place.
NO_VALUE = "unknown___"
_ORIGINAL_CWD = os.getcwd()


def _Published(key, states):
    """The rows of writing each of `states` to `key` after the first, joined as
    the web server joins a burst, in the form they are sent."""
    SM.Set(key, json.loads(json.dumps(states[0])))
    rows = []
    callback = SM.observers.state_updated.subscribe(
        lambda payload: rows.extend(payload["delta"]))
    try:
        for state in states[1:]:
            SM.Set(key, json.loads(json.dumps(state)))
    finally:
        SM.observers.state_updated.unsubscribe(callback)
    return json.loads(SocketioJson.dumps(rows))


@pytest.fixture(autouse=True)
def _in_work_dir(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "user_data").mkdir()
    os.chdir(tmp_path)
    # Initialize() is a no-op after its first call anywhere in the process.
    SM.Initialize()
    yield
    for case in CASES:
        SM.Unset(".".join(case["key"].split(".")[:2]))
    if SM._export_thread is not None:
        SM._export_thread.join()
    os.chdir(_ORIGINAL_CWD)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_the_rows_are_what_a_change_publishes(case):
    assert _Published(case["key"], case["states"]) == case["delta"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_a_client_has_an_answer_for_every_row(case):
    for row in case["delta"]:
        assert row["action"] in HANDLED_ACTIONS, row
        assert row["value"] != NO_VALUE, row


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_only_the_tail_of_a_list_comes_and_goes_within_a_revision(case):
    """Why a client can apply the rows one by one, whatever was joined with
    them: a list is compared position by position (zip_ordered_iterables), so
    nothing moves along. A row of a list's element names the place it had or
    has, and no place before it changes length."""
    if len(case["states"]) != 2:
        return
    for row in case["delta"]:
        if row["action"] == "iterable_item_removed":
            *parent, index = row["path"]
            kept = _At(case, parent, 1)
            assert len(kept) <= index, row
        elif row["action"] == "iterable_item_added" \
                and isinstance(row["path"][-1], int):
            *parent, index = row["path"]
            assert index >= len(_At(case, parent, 0) or []), row


def _At(case, path, stateIndex):
    """What `path` of the published rows is in one of the case's states; None
    if it is not there yet."""
    keys = case["key"].split(".")
    value = case["states"][stateIndex]
    for piece in path[len(keys):]:
        try:
            value = value[piece]
        except (KeyError, IndexError, TypeError):
            return None
    return value
