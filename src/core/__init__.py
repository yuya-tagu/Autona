"""Headless core: the Qt-free half of the application.

Everything under `src.core` must import without pulling in PySide6/qtpy.
That is not a style rule but a tested contract
(`tests/test_core_qt_free.py` checks it in a fresh subprocess), and it is
what lets the state layer and the domain logic be exercised without a
QApplication (ADR-004 D1).

Like `src/__init__.py`, this init is a docstring and nothing else: importing
the package must build nothing, read nothing and write nothing
(agent-pitfalls #1).
"""
