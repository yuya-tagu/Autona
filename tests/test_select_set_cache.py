"""Set-selection window: no fetch on open, and the list survives a close.

Operators open "セットを選ぶ" repeatedly during a stream. Fetching a large
event's sets takes seconds, so opening the window must never start a fetch by
itself: it restores the rows (and the sort) of the last fetch and leaves
refreshing to the "最新の試合情報を取得" button.

The cache deliberately lives on the class, not the instance -- every
scoreboard (1v1 / team / crew) builds its own window, and all of them show
the same event. It is process-local: closing Autona starts clean.

AutonaTournamentDataProvider.instance is stubbed; the tests assert the
window's fetch *decisions* and what it renders, never Start.gg behaviour.
"""
import pytest

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QWidget

from src.AutonaTournamentDataProvider import (
    AutonaTournamentDataProvider, AutonaTournamentDataProviderSignals)
from src.AutonaSelectSetWindow import AutonaSelectSetWindow

TOURNAMENT_URL = "https://start.gg/tournament/t/event/e"


def _set(setId, p1, p2):
    return {
        "id": setId,
        "p1_name": p1,
        "p2_name": p2,
        "round_name": f"Round {setId}",
        "stream": "",
        "station": None,
        "tournament_phase": "Pools",
        "tournament_pool": "A",
    }


class _StubProvider:
    def __init__(self, url):
        self.url = url


class _StubDataProvider:
    """Records what the window asks for instead of hitting the network."""

    def __init__(self, url=TOURNAMENT_URL):
        self.signals = AutonaTournamentDataProviderSignals()
        self.provider = _StubProvider(url)
        self.loadSetsCalls = []
        self.getPhasesCalls = 0
        self.cancelSetLoadCalls = 0

    def LoadSets(self, showFinished, phaseGroupIds=None):
        self.loadSetsCalls.append((showFinished, phaseGroupIds))

    def CancelSetLoad(self):
        self.cancelSetLoadCalls += 1

    def GetTournamentPhases(self):
        self.getPhasesCalls += 1

    def FinishFetch(self, sets):
        """Play back a completed fetch: pages first, then the full list."""
        self.signals.sets_data_updated.emit(
            {"progress": 1, "totalPages": 2, "sets": []})
        self.signals.get_sets_finished.emit(sets)


@pytest.fixture
def provider():
    previous = AutonaTournamentDataProvider.instance
    stub = _StubDataProvider()
    AutonaTournamentDataProvider.instance = stub
    AutonaSelectSetWindow._cache = None
    AutonaSelectSetWindow._sortState = None
    yield stub
    AutonaSelectSetWindow._cache = None
    AutonaSelectSetWindow._sortState = None
    AutonaTournamentDataProvider.instance = previous


@pytest.fixture
def makeWindow(qapp, provider):
    """Builds windows as the scoreboard does (parented, never shown). The
    parents are kept alive for the test: dropping one takes its child's C++
    objects with it."""
    parents = []

    def build():
        parent = QWidget()
        parents.append(parent)
        return AutonaSelectSetWindow(parent)

    yield build


@pytest.fixture
def window(makeWindow):
    return makeWindow()


def test_opening_does_not_fetch_sets(window, provider):
    window.PrepareForDisplay()

    assert provider.loadSetsCalls == []
    assert window.model.rowCount() == 0
    assert window.labelStatus.text() != ""


def test_fetch_button_fetches_and_fills_the_table(window, provider):
    window.PrepareForDisplay()
    window.btFetchSets.click()

    assert provider.loadSetsCalls == [(False, None)]
    # Disabled with a progress bar while the pages come in (isVisibleTo:
    # the window itself is never shown in the test).
    assert not window.btFetchSets.isEnabled()
    assert window.progressBar.isVisibleTo(window)

    provider.FinishFetch([_set(1, "A", "B"), _set(2, "C", "D")])

    assert window.model.rowCount() == 2
    assert window.btFetchSets.isEnabled()
    assert not window.progressBar.isVisibleTo(window)


def test_reopening_restores_the_list_without_refetching(window, makeWindow, provider):
    window.PrepareForDisplay()
    window.btFetchSets.click()
    provider.FinishFetch([_set(1, "A", "B"), _set(2, "C", "D")])
    window.close()

    # A second scoreboard's window shares the cache: same rows, no fetch.
    other = makeWindow()
    other.PrepareForDisplay()

    assert other.model.rowCount() == 2
    assert len(provider.loadSetsCalls) == 1


def test_sort_order_survives_reopening(window, makeWindow, provider):
    window.PrepareForDisplay()
    window.btFetchSets.click()
    provider.FinishFetch([_set(1, "A", "B"), _set(2, "C", "D")])
    window.startggSetSelectionItemList.sortByColumn(
        5, Qt.SortOrder.DescendingOrder)
    window.close()

    other = makeWindow()
    other.PrepareForDisplay()

    header = other.startggSetSelectionItemList.horizontalHeader()
    assert header.sortIndicatorSection() == 5
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    # Sorting lives in the proxy, so read the order through the view.
    assert other.proxyModel.index(0, 5).data() == "C"


def test_cache_is_not_reused_for_another_tournament(window, makeWindow, provider):
    window.PrepareForDisplay()
    window.btFetchSets.click()
    provider.FinishFetch([_set(1, "A", "B")])

    provider.provider = _StubProvider("https://start.gg/tournament/other/event/e")
    other = makeWindow()
    other.PrepareForDisplay()

    assert other.model.rowCount() == 0
    assert len(provider.loadSetsCalls) == 1


def test_phase_choice_fetches_that_phase_only(window, provider):
    provider.signals.tournament_phases_updated.emit([
        {"name": "Pools", "groups": [{"id": 11}, {"id": 12}]},
        {"name": "Top 8", "groups": [{"id": 21}]},
    ])
    window.PrepareForDisplay()
    assert provider.loadSetsCalls == []

    # index 0 = placeholder, 1 = all phases, 2+ = the phases themselves
    window.phaseCombo.setCurrentIndex(3)
    window._onPhaseChosen(3)

    assert provider.loadSetsCalls == [(False, [21])]


def test_fetch_without_a_tournament_does_not_hang_the_button(window, provider):
    # The external open-set API can open this window with no tournament set.
    provider.provider = None
    window.PrepareForDisplay()
    window.btFetchSets.click()

    assert provider.loadSetsCalls == []
    assert window.btFetchSets.isEnabled()


def test_complete_pairs_filter_reuses_the_cache(window, provider):
    window.PrepareForDisplay()
    window.btFetchSets.click()
    provider.FinishFetch([_set(1, "A", "B"), _set(2, "C", "")])
    assert window.model.rowCount() == 2

    window.showCompletePairs.click()

    assert window.model.rowCount() == 1
    assert len(provider.loadSetsCalls) == 1


def test_the_stub_has_only_what_the_real_adapter_has():
    """The stub stands in for AutonaTournamentDataProvider: every method
    the window may call on it must exist on the real adapter, or the stub
    hides a call that fails in the app (ADR-005 S3f-2 review: the window
    still read the adapter's removed `setLoadingWorker`)."""
    helpers = {"FinishFetch"}
    for name in vars(_StubDataProvider):
        if name.startswith("_") or name in helpers:
            continue
        real = getattr(AutonaTournamentDataProvider, name, None)
        assert callable(real), name


def test_picking_a_set_cancels_the_list_still_loading(window, provider):
    """Picking a set closes the window: the list it is still loading is
    cancelled (CancelSetLoad) and the fetch button is given back."""
    window.PrepareForDisplay()
    window.btFetchSets.click()
    assert not window.btFetchSets.isEnabled()

    window.LoadSelectedSet()  # an empty table: no set to load

    assert provider.cancelSetLoadCalls == 1
    assert window.btFetchSets.isEnabled()
