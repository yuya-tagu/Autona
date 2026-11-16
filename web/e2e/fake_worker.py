"""Archive worker for the E2E (ADR-006 A1): tests/fixtures/fake_vodsplit_worker.py
with its mode chosen per job, so one headless app serves every scenario.

AUTONA_VODSPLIT_CMD points here (web/e2e/global-setup.ts). The fake worker
reads FAKE_VODSPLIT_MODE from the environment; this wrapper sets it from the
job's source path instead: a path containing "slow" runs the "slow" mode (a
job that runs until cancelled), anything else the default "ok" mode.
"""
import os
import runpy
import sys
from pathlib import Path

FAKE = (Path(__file__).resolve().parents[2]
        / "tests" / "fixtures" / "fake_vodsplit_worker.py")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["run"] and len(args) > 1 and "slow" in args[1].lower():
        os.environ["FAKE_VODSPLIT_MODE"] = "slow"
    sys.argv = [str(FAKE)] + args
    runpy.run_path(str(FAKE), run_name="__main__")
