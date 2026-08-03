"""Opt-in integration tests that talk to the real start.gg API.

Everything under this package is skipped unless AUTONA_LIVE_TESTS=1 is set,
so a normal `pytest` run stays offline and deterministic. See conftest.py
for the event these tests are pinned to and why.
"""
