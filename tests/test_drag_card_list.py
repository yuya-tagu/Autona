"""DragCardList (src/ui_layout.py): the reorder the Result panel relies on.

The Commentary panel was its other user until ADR-005 S4c; the list keeps
its own pin so the reorder (orderChanged + cards()) stays covered.
"""
import pytest
from qtpy.QtWidgets import QFrame

from src import ui_layout as L


@pytest.fixture
def cardList(qapp):
    lst = L.DragCardList()
    cards = [QFrame() for _ in range(3)]
    for card in cards:
        lst.addCard(card)
    yield lst, cards
    lst.setParent(None)
    lst.deleteLater()


def test_a_drop_reorders_the_cards_and_says_so(cardList):
    lst, (a, b, c) = cardList
    emitted = []
    lst.orderChanged.connect(lambda: emitted.append(lst.cards()))

    lst._commitMove(0, 3)       # drop a below the last card

    assert lst.cards() == [b, c, a]
    assert emitted == [[b, c, a]]
    assert [lst.indexOf(x) for x in (b, c, a)] == [0, 1, 2]


def test_moving_upward_and_a_drop_in_place(cardList):
    lst, (a, b, c) = cardList
    emitted = []
    lst.orderChanged.connect(lambda: emitted.append(1))

    lst._commitMove(2, 0)
    assert lst.cards() == [c, a, b]
    lst._commitMove(1, 1)       # no move: nothing changes, nothing emitted
    lst._commitMove(5, 0)       # out of range: ignored
    assert lst.cards() == [c, a, b]
    assert emitted == [1]
