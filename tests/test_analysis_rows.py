"""The match list of the tournament (この大会の試合一覧, ADR-005 S4j / S4k):
the rows.

fixtures/analysis_rows/golden.json is frozen data: what the pre-S4j Qt
panel (AutonaAnalysisWidget, whose fetch and rows were its own) showed on
the last tree before they moved to core, dcf53307. It cannot be recorded
again -- that panel is gone (S4k removed the thin one S4j left too) -- and
is not to be edited. How it was recorded: for every scenario below, a fresh
panel with the scenario's fake provider loaded, its fetch button clicked
and the event loop and the provider's jobs drained; then the panel's table
-- every cell's text and the sort key its column sorts by --, its status
label once the fetch was over (the last text that was not the "取得中 n/m"
progress: when the last two answers arrived in one pass of the event loop,
the old panel's next-fetch timer put the progress back up after the
result, which real requests seconds apart do not do), its label before the
click, and what the provider was asked for. Phase groups are built with
tests/test_top8_refresh.py's helpers.

The rows are compared in one fixed order -- by the Round column's sort key
(phase group id, round, index of the set in its round), which no two rows
share -- because the order core makes them in depends on which of its two
parallel fetches answered first. The fetches are compared as a sorted list,
for the same reason.

Since S4k the React Statistics page (web/src/StatsPage) draws the rows. It
reads them through GET /api/v1/analysis (replayed here scenario by
scenario) and words them with its own rules: web/src/StatsPage/
analysisRows.cases.json holds, per scenario, core's rows, the cells and the
status line the page must make of them -- checked here against core and
the golden, and by the page's jest test against the page's code -- and
names in the order Python sorts them by the page's key. Rewrite it
after a deliberate change with

    AUTONA_WRITE_ANALYSIS_CASES=1 python -m pytest tests/test_analysis_rows.py
"""
import copy
import json
import os
from pathlib import Path

import pytest

from tests.test_api_v1 import webApp  # noqa: F401
from tests.test_top8_refresh import (
    COMPLETE, COMPLETE_SEEDS, ENTRANTS, IN_PROGRESS, SMALL,
    _fromSet, _group, _seedSlot, _set)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = (PROJECT_ROOT / "tests" / "fixtures" / "analysis_rows"
               / "golden.json")
# The page's side (ADR-005 S4k): core's rows and what the page makes of them.
CASES_PATH = (PROJECT_ROOT / "web" / "src" / "StatsPage"
              / "analysisRows.cases.json")
JA_PATH = PROJECT_ROOT / "web" / "src" / "i18n" / "locales" / "ja.json"
WRITE_CASES = os.environ.get("AUTONA_WRITE_ANALYSIS_CASES") == "1"


# ── Start.gg-shaped data (what GetAnalysisPhaseGroup returns) ────────────

BYE = {"prereqType": "bye", "prereqId": None, "prereqPlacement": None,
       "seedNum": None}

# Six entrants in an eight-slot double elimination: seeds 1 and 2 have a
# bye in the first winners round, and so does the first losers round's
# first set (its loser of 201 is a bye).
BYES = {
    "entrants": copy.deepcopy(ENTRANTS[:6]),
    "sets": {
        "1": [_set(201, [_seedSlot(1), BYE]),
              _set(202, [_seedSlot(4), _seedSlot(5)], (2, 1), True),
              _set(203, [_seedSlot(2), BYE]),
              _set(204, [_seedSlot(3), _seedSlot(6)], (0, 2), True)],
        "2": [_set(205, [_fromSet(201, 1), _fromSet(202, 1)], (2, 0), True),
              _set(206, [_fromSet(203, 1), _fromSet(204, 1)], (1, 2), True)],
        "3": [_set(207, [_fromSet(205, 1), _fromSet(206, 1)])],
        "-1": [_set(208, [BYE, _fromSet(202, 2)]),
               _set(209, [_fromSet(204, 2), BYE])],
        "-2": [_set(210, [_fromSet(208, 1), _fromSet(206, 2)], (2, 1), True),
               _set(211, [_fromSet(209, 1), _fromSet(205, 2)])],
    },
    "progressionsIn": [], "progressionsOut": None,
}

# Seed 7 goes the whole losers side: it beats 6 (losers round 1), 4, 5 and
# 2 -- a losers run of 1, 2 and 3 -- and loses the grand final (no reset).
LOSERS_RUN = {
    1001: ((2, 1), True),    # 1 beats 4
    1002: ((2, 0), True),    # 2 beats 3
    1006: ((2, 0), True),    # 5 beats 8
    1007: ((1, 2), True),    # 7 beats 6
    1003: ((3, 2), True),    # WF: 1 beats 2
    1010: ((2, 1), True),    # LQF1: 5 beats 3
    1009: ((2, 0), True),    # LQF2: 7 beats 4
    1011: ((2, 1), True),    # LSF: 7 beats 5
    1008: ((0, 3), True),    # LF: 7 beats 2
    1004: ((3, 1), True),    # GF: 1 beats 7
}

# A group with no sets: a dict, so it answered, with no rows. (What the
# Start.gg provider answered too when its request failed, before S4j's
# strict requests: then it raises, and the group did not answer.)
EMPTY_ANSWER = {"entrants": [], "sets": {}, "progressionsOut": []}
# Sets the bracket model cannot read (a list, not rounds): its build raises.
UNREADABLE = {"entrants": [], "sets": [1], "progressionsOut": []}


def _phases(*phases):
    """[(phase name, [(group id, group name), ...]), ...] in Start.gg's
    order, as GetTournamentPhases returns them."""
    return [{"id": 900 + index, "name": name,
             "groups": [{"id": groupId, "name": groupName,
                         "bracketType": "DOUBLE_ELIMINATION"}
                        for groupId, groupName in groups]}
            for index, (name, groups) in enumerate(phases)]


ONE_GROUP = _phases(("Top 8", [(50, "Pool 1")]))

# label -> (phases, {group id: what GetAnalysisPhaseGroup answers}); None:
# no tournament loaded.
SCENARIOS = {
    "one group, complete with a reset": (
        ONE_GROUP, {50: _group(COMPLETE)}),
    "one group, seeds known": (
        ONE_GROUP, {50: _group(COMPLETE, seeds=COMPLETE_SEEDS)}),
    "one group, unfinished": (
        ONE_GROUP, {50: _group(IN_PROGRESS, reset=False)}),
    "one group, losers unwired (byes)": (
        ONE_GROUP, {50: _group(COMPLETE, wiredLosers=False)}),
    "one group, byes": (ONE_GROUP, {50: BYES}),
    "one group, a losers run": (
        ONE_GROUP, {50: _group(LOSERS_RUN, reset=False)}),
    "one group, too small": (ONE_GROUP, {50: SMALL}),
    # Every way the round prefix is made: phase and group named apart, the
    # group's name inside the phase's, no phase name, no group name -- and a
    # group with no id, which is not fetched.
    "several groups": (
        _phases(("Pools", [(21, "Pool A1"), (22, "Pool A2")]),
                ("Top 8", [(50, "Top 8")]),
                ("Pool B1 Finals", [(31, "Pool B1"), (None, "Pool X")]),
                ("", [(32, "Pool C")]),
                ("Bracket", [(33, "")])),
        {21: _group(COMPLETE), 22: _group(IN_PROGRESS, reset=False),
         50: SMALL, 31: _group(LOSERS_RUN, reset=False), 32: BYES,
         33: _group(COMPLETE, seeds=COMPLETE_SEEDS)}),
    "some groups failed": (
        _phases(("Pools", [(41, "Pool 1"), (42, "Pool 2"), (43, "Pool 3"),
                           (44, "Pool 4")])),
        {41: _group(COMPLETE), 42: None, 43: EMPTY_ANSWER,
         44: UNREADABLE}),
    "every group failed": (
        _phases(("Pools", [(41, "Pool 1"), (42, "Pool 2")])),
        {41: None, 42: {}}),
    "every group answered empty": (
        _phases(("Pools", [(41, "Pool 1"), (42, "Pool 2")])),
        {41: copy.deepcopy(EMPTY_ANSWER), 42: copy.deepcopy(EMPTY_ANSWER)}),
    "no phase group": ([], {}),
    "no tournament": None,
}


class FakeProvider:
    """The two reads the match list makes, recording what was asked (and
    whether strictly: `strict`, one entry per call)."""

    url = "https://www.start.gg/tournament/harness/event/analysis"
    videogame = None

    def __init__(self, phases, groups):
        self._phases = phases
        self._groups = groups
        self.calls = []
        self.strict = []

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        self.calls.append(["phases"])
        self.strict.append(strict)
        return copy.deepcopy(self._phases)

    def GetAnalysisPhaseGroup(self, id, progress_callback=None,
                              cancel_event=None, strict=False):
        self.calls.append(["group", id])
        self.strict.append(strict)
        return copy.deepcopy(self._groups.get(id))


def _provider(label):
    scenario = SCENARIOS[label]
    return None if scenario is None else FakeProvider(*scenario)


# ── harness ──────────────────────────────────────────────────────────────

@pytest.fixture
def analysis(autonaApp):
    """No tournament provider, and no match list, left behind."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.AnalysisService import AnalysisService
    saved = AutonaTournamentDataProvider.instance.provider
    try:
        yield autonaApp
    finally:
        _drain(autonaApp)
        AnalysisService.instance.Forget()
        AutonaTournamentDataProvider.instance.provider = saved
        autonaApp.settle()


def _drain(app):
    """Let the provider's jobs and what they hand the GUI thread finish."""
    from qtpy.QtCore import QCoreApplication
    from qtpy.QtWidgets import QApplication
    from src.core.TaskExecutor import TaskExecutor
    executor = TaskExecutor.instance
    quiet = 0
    for _ in range(600):
        idle = executor.drain(0.02)
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents()
        quiet = quiet + 1 if idle else 0
        if quiet >= 8:
            break
    app.settle()


def _jsonable(value):
    return json.loads(json.dumps(value))


def _switchTournament(provider):
    """`provider` loaded, as a tournament (re)load / unload leaves the
    match list: forgotten (the provider adapter's Load / UnloadTournament
    call AnalysisService.Forget; the pre-S4j panel dropped its rows on
    tournament_changed)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.AnalysisService import AnalysisService
    AutonaTournamentDataProvider.instance.provider = provider
    AnalysisService.instance.Forget()


# ── replays: core and the routes give what the panel gave ────────────────

def _golden(label):
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))[label]


# The one scenario whose end is not the recording's, on purpose: with no
# phase group to fetch, the pre-S4j panel asked for the phases again and
# waited for them for good ("フェーズ情報を取得中..." until the tournament
# changed). Core fetches the phases itself and says it failed.
NO_PHASE_GROUP = "no phase group"

# `analysis` once each scenario's fetch is over (revision apart). The
# recording's label says the same: "n 試合" (done), "(k 件取得失敗)"
# (failed), "取得に失敗しました" (error).
_ONE_DONE = {"status": "done", "error": "", "done": 1, "total": 1,
             "failed": 0}
EXPECTED_PROGRESS = {
    "one group, complete with a reset": _ONE_DONE,
    "one group, seeds known": _ONE_DONE,
    "one group, unfinished": _ONE_DONE,
    "one group, losers unwired (byes)": _ONE_DONE,
    "one group, byes": _ONE_DONE,
    "one group, a losers run": _ONE_DONE,
    "one group, too small": _ONE_DONE,
    # The group with no id is not fetched.
    "several groups": {"status": "done", "error": "", "done": 6, "total": 6,
                       "failed": 0},
    # No answer, no sets, sets no bracket is built from.
    "some groups failed": {"status": "done", "error": "", "done": 4,
                           "total": 4, "failed": 3},
    "every group failed": {"status": "error", "error": "fetch_failed",
                           "done": 2, "total": 2, "failed": 2},
    # A group with no sets answered (a request that failed raises: "every
    # group failed").
    "every group answered empty": {"status": "done", "error": "", "done": 2,
                                   "total": 2, "failed": 2},
    NO_PHASE_GROUP: {"status": "error", "error": "no_phases", "done": 0,
                     "total": 0, "failed": 0},
}


def _expectedRows(label):
    """The recorded rows, with the one cell core's rows cannot give: the
    Score sort key of a set not finished. The pre-S4j panel sorted it by
    its partial score (1-1 in play: 2) while showing "-"; a row's score is
    None until the set is finished (the S4j design), so it sorts as 0, the
    same as any other "-"."""
    rows = copy.deepcopy(_golden(label)["rows"])
    for row in rows:
        if row[3][0] == "-":
            row[3][1] = 0
    return rows


def test_the_expected_rows_differ_only_in_unfinished_scores():
    """What _expectedRows changes is the partial scores and nothing else
    (IN_PROGRESS's set in play, 1-1: alone, and among several groups)."""
    changed = []
    for label in SCENARIOS:
        for recorded, expected in zip(_golden(label)["rows"],
                                      _expectedRows(label)):
            if recorded != expected:
                assert recorded[:3] + recorded[4:] \
                    == expected[:3] + expected[4:]
                changed.append((label, recorded[3]))
    assert changed == [("one group, unfinished", ["-", 2]),
                       ("several groups", ["-", 2])]


def _pageCells(row):
    """A core row in the page's words (the S4j design: "TBD", "(seed N)"
    and "-" are the page's), as the table's nine [text, sort key] cells:
    the Qt panel's rules, written out here apart from the page's rowCells
    (web/src/StatsPage/analysisRows.ts), which the cases file holds to the
    same cells."""
    def seed(side):
        return [str(side["seed"]) if side["seed"] else "-",
                side["seed"] or 9999]

    def name(side):
        text = side["name"] or (
            f"(seed {side['seed']})" if side["seed"] else "TBD")
        return [text, text.lower()]

    score = row["score"]
    return [
        [row["round"], list(row["roundOrder"])],
        seed(row["p1"]), name(row["p1"]),
        ["-" if score is None else f"{score[0]}-{score[1]}",
         0 if score is None else sum(score)],
        name(row["p2"]), seed(row["p2"]),
        ["" if row["upset"] is None else str(row["upset"]),
         row["upset"] or 0],
        [str(row["losersRun"]) if row["losersRun"] else "",
         row["losersRun"]],
        ["" if row["seedOver"] is None else str(row["seedOver"]),
         row["seedOver"] or 0],
    ]


def test_core_makes_the_recorded_rows(analysis):
    """AnalysisService on its own (no panel; what the headless shell runs):
    every scenario's rows, in the page's words, are the recording's, and
    so are the requests made; `analysis` says how it ended."""
    from src.core import AnalysisService as service
    from src.core.AnalysisService import AnalysisService
    from src.core.StateManager import StateManager
    made = {}
    for label in SCENARIOS:
        provider = _provider(label)
        _switchTournament(provider)
        before = AnalysisService.Progress()["revision"]
        answer = AnalysisService.instance.Refresh()
        if provider is None:
            assert answer == service.NO_TOURNAMENT, label
            assert AnalysisService.Progress()["revision"] == before
            assert AnalysisService.instance.Rows() == []
            continue
        assert answer == service.STARTED, label
        _drain(analysis)
        assert not AnalysisService.instance.Running(), label
        progress = AnalysisService.Progress()
        assert progress == dict(EXPECTED_PROGRESS[label],
                                revision=before + 1), label
        assert StateManager.Get("analysis") == progress
        result = AnalysisService.instance.Result()
        rows = sorted((_pageCells(row) for row in result["rows"]),
                      key=lambda row: row[0][1])
        assert rows == _expectedRows(label), label
        assert sorted(provider.calls, key=json.dumps) \
            == _golden(label)["fetches"], label
        # Asked strictly: a request that failed is told from an empty
        # answer (S4j review M1).
        assert provider.strict and all(provider.strict), label
        if progress["status"] == "done":
            assert result["url"] == FakeProvider.url
            assert result["revision"] == progress["revision"]
        else:
            assert result == {"url": "", "rows": [], "revision": None}
        made[label] = result["rows"]

    # The page's cases are these rows (S4k).
    cases = _pageCases(made)
    if WRITE_CASES:
        CASES_PATH.write_text(
            json.dumps(cases, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    assert json.loads(CASES_PATH.read_text(encoding="utf-8")) == cases


# ── the page's cases (ADR-005 S4k) ───────────────────────────────────────

def _pageLine(label):
    """The status line the page shows once `label`'s fetch is over: the
    recording's -- the page words it as the Qt panel did -- but for the
    scenario with no phase group, which core ends in an error of its own
    (no_phases), worded by the page."""
    if label == NO_PHASE_GROUP:
        ja = json.loads(JA_PATH.read_text(encoding="utf-8"))
        return ja["stats_analysis_no_phases"]
    return _golden(label)["label"]


def _pageCases(made):
    """Per scenario fetched: `analysis` once it was over (revision apart),
    core's rows -- in an order the table's default sort has to undo: the
    Round column's key, last first --, the cells the page makes of them in
    the table's default order (the recording's) and the status line, in
    Japanese (the recording's language)."""
    return {
        "about": (
            "The Statistics page's match list (ADR-005 S4k): per scenario "
            "of tests/test_analysis_rows.py, core's rows as GET "
            "/api/v1/analysis answers them (in an order the table's default "
            "sort undoes), the nine cells the page shows for them in that "
            "default order -- the pre-S4j Qt panel's, recorded in "
            "tests/fixtures/analysis_rows/golden.json -- and the status "
            "line (ja) once the fetch was over; and names in the order "
            "Python sorts them by the page's key, both ways (nameSort). "
            "Written by AUTONA_WRITE_ANALYSIS_CASES=1 python -m pytest "
            "tests/test_analysis_rows.py; checked there against core and "
            "the golden, and by analysisRows.test.js against the page."),
        "cases": [
            {
                "scenario": label,
                "progress": EXPECTED_PROGRESS[label],
                "line": _pageLine(label),
                "rows": _jsonable(sorted(
                    rows, key=lambda row: _jsonable(row["roundOrder"]),
                    reverse=True)),
                "cells": _expectedRows(label),
            }
            for label, rows in made.items()
        ],
        "nameSort": _nameSort(),
    }


# Names Python orders by their code points and JS's `<` by their UTF-16
# code units, differently: astral (emoji, surrogate pairs from 0xD800)
# against full-width (from 0xFF01) and kana; the same name in two cases
# (equal keys: kept in their order, both ways); special casings (the final
# sigma). The page compares by code points (S4k review).
SORTED_NAMES = ["🦊Fox", "Ｚｅｔａ", "alpha", "Alpha", "ｂｅｔａ", "😀Smile",
                "zed", "あおい", "Émile", "ΟΔΟΣ"]


def _nameKey(name):
    """The P1 Name column's sort key the page makes of `name`."""
    return _pageCells({"round": "R", "roundOrder": ["1", 1, 0],
                       "p1": {"seed": 1, "name": name},
                       "p2": {"seed": None, "name": ""}, "score": None,
                       "upset": None, "losersRun": 0, "seedOver": None})[2][1]


def _nameSort():
    """SORTED_NAMES as Python sorts them by the page's key: ascending, and
    descending (equal keys in their order either way, as the page's
    stable sort keeps them)."""
    return {
        "names": SORTED_NAMES,
        "ascending": sorted(SORTED_NAMES, key=_nameKey),
        "descending": sorted(SORTED_NAMES, key=_nameKey, reverse=True),
    }


def test_the_page_cases_name_every_scenario_fetched():
    """The cases file has a case for every scenario with a tournament, the
    recording's cells and its status line (no_phases apart)."""
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    assert [case["scenario"] for case in cases] \
        == [label for label in SCENARIOS if SCENARIOS[label] is not None]
    for case in cases:
        label = case["scenario"]
        assert case["cells"] == _expectedRows(label), label
        assert sorted((_pageCells(row) for row in case["rows"]),
                      key=lambda row: row[0][1]) == case["cells"], label
        assert case["line"] == _pageLine(label), label
    # The rows come in an order the default sort has to put right.
    assert any(case["rows"] and [_pageCells(row) for row in case["rows"]]
               != case["cells"] for case in cases)


def test_the_page_cases_sort_names_as_python_does():
    """The cases file's names are in Python's order (the page's jest test
    sorts them with the page's code), and that order is not UTF-16's."""
    nameSort = json.loads(CASES_PATH.read_text(encoding="utf-8"))["nameSort"]
    assert nameSort == _nameSort()
    assert nameSort["ascending"] != sorted(
        SORTED_NAMES, key=lambda name: _nameKey(name).encode("utf-16-be"))
    # Equal keys are in the names' order, both ways.
    for order in (nameSort["ascending"], nameSort["descending"]):
        assert order.index("alpha") < order.index("Alpha")


# ── through the routes (ADR-005 S4k) ─────────────────────────────────────

def test_the_routes_give_what_the_panel_gave(analysis, webApp):
    """Every scenario as the Statistics page runs it: POST
    /api/v1/analysis:refresh, then GET /api/v1/analysis once the fetch is
    over -- the recording's rows (in the page's words), the status, the
    tournament's URL with done rows only."""
    from src.core.AnalysisService import AnalysisService
    client = webApp.test_client()
    for label in SCENARIOS:
        provider = _provider(label)
        _switchTournament(provider)
        answer = client.post("/api/v1/analysis:refresh")
        body = answer.get_json()
        if provider is None:
            assert answer.status_code == 409, label
            assert body["error"]["code"] == "NOT_READY"
        else:
            assert answer.status_code == 202, label
            assert body["data"] == {"accepted": True,
                                    "operation": "analysis.refresh"}
        _drain(analysis)
        read = client.get("/api/v1/analysis")
        assert read.status_code == 200
        data = read.get_json()["data"]
        assert data["status"] == AnalysisService.Progress()["status"]
        if provider is not None:
            assert data["status"] == EXPECTED_PROGRESS[label]["status"]
        cells = sorted((_pageCells(row) for row in data["rows"]),
                       key=lambda row: row[0][1])
        assert cells == _expectedRows(label), label
        assert data["url"] == (
            FakeProvider.url if data["status"] == "done" else ""), label
        assert sorted(provider.calls if provider else [], key=json.dumps) \
            == _golden(label)["fetches"], label
