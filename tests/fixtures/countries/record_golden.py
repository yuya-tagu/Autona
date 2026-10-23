"""Record golden.json (next to this file) from the PRE-S1c implementation.

ADR-005 S1c moved the country data out of AutonaCountryHelper into the
Qt-free src/core/CountryCatalog.py, and pinned the move against what the old
code produced: tests/test_country_catalog.py and
tests/test_country_helper_model.py compare against golden.json. This is the
script that recorded it.

It drives the OLD API -- `AutonaCountryHelper.countries`,
`GetBasicCountryInfo`, `GetStates`, and the old `LoadStates` that wrote each
region's `asset` into the shared table -- none of which exists any more, so
it cannot run against the current tree. Run it against a copy of the last
commit before S1c (a016e2ba):

    mkdir ../autona-pre-s1c
    git archive a016e2ba src | tar -x -C ../autona-pre-s1c
    python tests/fixtures/countries/record_golden.py ../autona-pre-s1c \\
        tests/fixtures/countries/golden.json

Arguments: <old checkout> <output json> [<assets dir>]. The old checkout's
src/ is what gets imported. The country data is read from <assets dir>
(default: this project's assets/) through a link in a throwaway working
directory, which also holds user_data/additional_flag with CUSTOM_FLAGS
below (empty files: only their names reach the model). Re-record only when
the shipped country data changes; the result is meant to be identical
otherwise.

Large tables are stored as digests (the first 16 hex digits of the sha256 of
`json.dumps(value)`), with a few countries in full for readable failures.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Exercise the custom-flag rows: ordering (case, a space) and a non-PNG
# extension.
CUSTOM_FLAGS = ["Agender.png", "ab.png", "Custom Flag.png", "zzz.jpg"]

SAMPLES = ("JP", "GB", "US")
LOCALES = {"en": ("en-US", "en-US"), "ja": ("ja", "ja"),
           "mixed": ("pt_BR", "zh-CN")}


def digest(value):
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:16]


def _link(link: Path, target: Path):
    # A junction on Windows (os.symlink needs Developer Mode or elevation);
    # same as tests/conftest._link_tree.
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def record(oldCheckout: Path):
    sys.path.insert(0, str(oldCheckout))
    sys.dont_write_bytecode = True
    os.environ["QT_API"] = "pyside6"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from qtpy.QtCore import QObject, Qt
    from qtpy.QtWidgets import QApplication, QComboBox

    app = QApplication.instance() or QApplication([])  # noqa: F841

    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
    from src.AutonaBasePlayerWidget import AutonaBasePlayerWidget
    from src.core import StateSchema as schema

    def rows(model):
        result = []
        for row in range(model.rowCount()):
            item = model.item(row)
            result.append([item.data(Qt.ItemDataRole.DisplayRole),
                           item.data(Qt.ItemDataRole.UserRole),
                           item.isEnabled(), item.isSelectable()])
        return result

    class FakePlayer(QObject):
        """Just enough of a player widget to run the real LoadStates."""
        _EnsureStateModel = AutonaBasePlayerWidget._EnsureStateModel
        LoadStates = AutonaBasePlayerWidget.LoadStates

        def __init__(self):
            super().__init__()
            self.country = QComboBox()
            self.country.setModel(AutonaCountryHelper.countryModel)
            self.state = QComboBox()

        def _w(self, name, klass=None):
            return {schema.PLAYER_COUNTRY_KEY: self.country,
                    schema.PLAYER_STATE_KEY: self.state}[name]

    golden = {"customFlags": list(CUSTOM_FLAGS), "locales": {},
              "scenarios": {}}
    for label, (program, export) in LOCALES.items():
        golden["locales"][label] = {"program": program, "export": export}
        AutonaLocaleHelper.programLocale = program
        AutonaLocaleHelper.exportLocale = export
        # A fresh start per scenario: the old class attributes accumulate
        # across loads.
        AutonaCountryHelper.countries = {}
        AutonaCountryHelper.countryModel = None
        AutonaCountryHelper.LoadCountries()

        countries = AutonaCountryHelper.countries
        codes = list(countries.keys())
        scenario = {"codes": codes, "countriesDigest": digest(countries)}
        basic = {c: AutonaCountryHelper.GetBasicCountryInfo(c) for c in codes}
        scenario["basicInfoDigests"] = {c: digest(v) for c, v in basic.items()}
        scenario["basicInfo"] = {c: basic[c] for c in SAMPLES}
        modelRows = rows(AutonaCountryHelper.countryModel)
        scenario["modelRowCount"] = len(modelRows)
        scenario["modelRowDigests"] = [digest(r) for r in modelRows]
        scenario["modelRowsHead"] = modelRows[:3]
        # Every row after the standard countries: the separator and the
        # custom flags.
        scenario["modelRowsTail"] = modelRows[1 + len(codes):]
        golden["scenarios"][label] = scenario
        if label != "en":
            continue

        # The region tables do not depend on the locale: recorded once,
        # before ("pristine") and after the old LoadStates had shown every
        # country (it wrote `asset` into each region it displayed).
        pristine = {c: AutonaCountryHelper.GetStates(c) for c in codes}
        states = {"pristineDigests": {c: digest(v)
                                      for c, v in pristine.items()},
                  "unknown": AutonaCountryHelper.GetStates("XX-NOPE"),
                  "pristineJP": json.loads(json.dumps(pristine["JP"]))}
        fake = FakePlayer()
        stateRows = {}
        for row in range(1, 1 + len(codes)):
            fake.country.setCurrentIndex(row)
            fake.LoadStates(row)
            code = fake.country.currentData(Qt.ItemDataRole.UserRole)["code"]
            stateRows[code] = rows(fake.state.model())
        states["comboDigests"] = {c: digest(v) for c, v in stateRows.items()}
        states["comboJPHead"] = stateRows["JP"][:3]
        after = {c: AutonaCountryHelper.GetStates(c) for c in codes}
        states["afterDigests"] = {c: digest(v) for c, v in after.items()}
        states["afterJP"] = json.loads(json.dumps(after["JP"]))
        states["afterGB_CON"] = json.loads(json.dumps(after["GB"]["CON"]))
        states["countryJP"] = json.loads(json.dumps(countries["JP"]))
        golden["states"] = states
    return golden


def main(argv):
    if len(argv) not in (3, 4):
        sys.exit(__doc__)
    oldCheckout = Path(argv[1]).resolve()
    out = Path(argv[2]).resolve()
    assets = Path(argv[3]).resolve() if len(argv) == 4 \
        else PROJECT_ROOT / "assets"

    previousDir = os.getcwd()
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        flags = work / "user_data" / "additional_flag"
        flags.mkdir(parents=True)
        for name in CUSTOM_FLAGS:
            (flags / name).write_bytes(b"")
        _link(work / "assets", assets)
        os.chdir(work)
        try:
            golden = record(oldCheckout)
        finally:
            os.chdir(previousDir)
            # Remove the link itself, never what it points at: rmtree on a
            # junction descends into it before Python 3.12 (see conftest).
            if (work / "assets").is_symlink():
                os.unlink(work / "assets")
            else:
                os.rmdir(work / "assets")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=1)
    print("recorded", out)


if __name__ == "__main__":
    main(sys.argv)
