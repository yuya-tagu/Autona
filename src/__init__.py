"""Autona application package — intentionally empty.

Importing `src`, or any module under it, must have no observable side
effect: no QApplication, no singleton, no file read or write. That is what
lets tools, tests and PyInstaller's dependency analysis import any module in
isolation (tests/test_import_side_effects.py pins it).

The single exception is `src/Autona.py`, the application assembly module:
importing it *is* the startup sequence (logger configuration, bootstrap_core()
and the Window definition), which is why main.py imports it explicitly rather
than relying on this package init.

The authoritative singleton construction order lives in `src/bootstrap.py`.
"""
