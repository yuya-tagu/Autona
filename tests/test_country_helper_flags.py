"""BUG-2 (docs/changes/test-automation-strategy.md §3): a missing
`./user_data/additional_flag` used to take every flag down with it.

`AutonaCountryHelper.LoadCountries` built the whole standard country list and
model first, then reached `os.listdir("./user_data/additional_flag")` for the
optional custom-flag add-on. That directory is normally shipped and git
tracked, so this only bit a user who had cleaned out `user_data` -- but the
harness's synthetic `user_data` never had it either (`tests/conftest.py`'s
`_RUNTIME_DIRS` creates `user_data` and `user_data/games`, not
`user_data/additional_flag`), so LoadCountries logged an ERROR traceback on
every harness run that built a board, and any test asserting on the
`country_code` SetData path was one accident away from an unguarded
`AutonaCountryHelper.countryModel.rowCount()` call on a `None` model
(AutonaScoreboardPlayerWidget.SetData).

The fix isolates the custom-flag block in its own try/except and guarantees
the directory with `os.makedirs(..., exist_ok=True)` first (the same pattern
AutonaGameAssetManager uses for `./user_data/games`), so the standard flags
load is never at the mercy of the optional add-on.
"""
import shutil

from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
from src.core.CountryCatalog import CountryCatalog

ADDITIONAL_FLAG_DIR = "./user_data/additional_flag"


def _removeAdditionalFlagDir():
    shutil.rmtree(ADDITIONAL_FLAG_DIR, ignore_errors=True)


# ── the direct repro: LoadCountries() with the directory absent ──────────

def test_load_countries_populates_the_model_without_additional_flag_dir(
        autonaApp):
    """The BUG-2 repro, called directly so it does not depend on whether an
    earlier test in this (session-scoped) harness already lazily triggered
    LoadCountries() once and left additional_flag behind."""
    _removeAdditionalFlagDir()

    AutonaCountryHelper.LoadCountries()

    assert AutonaCountryHelper.countryModel is not None
    # Row 0 is the "no country" placeholder; real entries start at 1. ~250
    # countries ship in assets/countries+states.json.
    assert AutonaCountryHelper.countryModel.rowCount() > 100


def test_load_countries_creates_the_missing_directory(autonaApp):
    """Matches AutonaGameAssetManager's ./user_data/games pattern: the app
    guarantees its own optional data directories rather than erroring on a
    cleaned-out user_data."""
    _removeAdditionalFlagDir()

    AutonaCountryHelper.LoadCountries()

    import os
    assert os.path.isdir(ADDITIONAL_FLAG_DIR)


def test_standard_flag_lookup_works_without_additional_flag_dir(autonaApp):
    _removeAdditionalFlagDir()

    AutonaCountryHelper.LoadCountries()

    info = CountryCatalog.instance.BasicCountryInfo("JP")
    assert info.get("en_name") == "Japan"
    assert info.get("code") == "JP"


# ── the L2 path this unblocks: country_code through a real player widget ──

def test_set_data_with_country_code_publishes_the_country(autonaApp):
    """Previously untestable in the harness (test-automation-strategy.md
    §3): country_code through SetData reads AutonaCountryHelper.countryModel,
    which BUG-2 left None for the whole session once the first board build
    hit the missing-directory exception."""
    _removeAdditionalFlagDir()

    board = autonaApp.board(1)
    player = board.team1playerWidgets[0]

    player.SetData({"gamerTag": "TAG", "country_code": "JP"},
                    dontLoadFromDB=True)
    autonaApp.settle()

    published = autonaApp.get(f"{player.path}.country")
    assert published is not None, "country_code did not reach state at all"
    assert published.get("code") == "JP"
    assert published.get("en_name") == "Japan"
