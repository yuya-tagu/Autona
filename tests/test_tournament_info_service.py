"""The tournament metadata rules, with no QApplication and no EVENT tab.

``AutonaTournamentInfoWidget.UpdateData`` matched each payload key against a
widget objectName, drove that widget, and let the widget's signal write the
state -- so "what does loading a tournament put in tournamentInfo.*" could
only be answered by building the panel. That is also why the panel had to be
eager: it was the only writer.

Since headless core Step 4b the writer is
``src/core/TournamentInfoService.py`` and the panel renders it, which is what
let the page become lazy. These are the rules that made the extraction
non-trivial: the clear-then-apply pass, the two different 表示大会名 override
semantics, and the short-date keys.

Isolation follows tests/test_set_load_service.py.
"""
import os
import tempfile
import time
from pathlib import Path

import pytest

from src.core.SettingsManager import SettingsManager
from src.core.TournamentInfoService import (
    TournamentInfoService, TOURNAMENT_LOGO_PATH)
from src.Helpers.AutonaDictHelper import deep_clone
from src.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_tinfo_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "layout", exist_ok=True)

PREFIX = "tournamentInfo"


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        SM.state.pop(PREFIX, None)
        SM.lastSavedState.pop(PREFIX, None)
        SM.changedKeys = []
    settingsSnapshot = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
        SettingsManager.settings = settingsSnapshot
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def service():
    """A service of this module's own, with a deterministic date format.

    `instance` is cleared around the construction because the class refuses
    a second one (the 表示大会名 follow rule is stateful, so two ledgers
    would disagree) -- and this module shares its process with the widget
    harness, which does bootstrap one. Restored on the way out.
    """
    existing = TournamentInfoService.instance
    TournamentInfoService.instance = None
    try:
        # A deterministic stand-in for AutonaLocaleHelper.FormatExportDate,
        # so the assertions do not depend on the machine's locale.
        yield TournamentInfoService(
            formatEventDate=lambda ts: "DATE:" + time.strftime(
                "%Y-%m-%d", time.localtime(float(ts))))
    finally:
        TournamentInfoService.instance = existing


def test_a_second_service_is_refused(service):
    """Two ledgers would silently disagree about the display name."""
    TournamentInfoService.instance = service
    try:
        with pytest.raises(RuntimeError):
            TournamentInfoService()
    finally:
        TournamentInfoService.instance = None


def _get(key, default=None):
    return SM.Get(f"{PREFIX}.{key}", default)


# ── seeds ────────────────────────────────────────────────────────────────

def test_seed_defaults_the_display_name_to_the_tournament_name(service):
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    service.Seed()
    assert _get("displayTournamentName") == "Autona Cup"


def test_seed_keeps_a_customised_display_name(service):
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    SM.Set(f"{PREFIX}.displayTournamentName", "Autona Cup 2026 FINAL")
    service.Seed()
    assert _get("displayTournamentName") == "Autona Cup 2026 FINAL"


def test_seed_creates_the_text_keys_the_panel_used_to_create(service):
    """The lazy panel no longer seeds them by being built."""
    service.Seed()
    for field in TournamentInfoService.TEXT_FIELDS:
        assert _get(field) == ""


def test_seed_does_not_blank_restored_text_keys(service):
    SM.Set(f"{PREFIX}.eventName", "Singles")
    service.Seed()
    assert _get("eventName") == "Singles"


def test_seed_publishes_the_logo_version(service):
    Path(TOURNAMENT_LOGO_PATH).write_bytes(b"")
    try:
        service.Seed()
        expected = int(os.path.getmtime(TOURNAMENT_LOGO_PATH) * 1000)
        assert _get("logoVersion") == expected
    finally:
        Path(TOURNAMENT_LOGO_PATH).unlink()


def test_a_missing_logo_publishes_version_zero(service):
    service.PublishLogoVersion()
    assert _get("logoVersion") == 0


# ── applying a payload ───────────────────────────────────────────────────

def _payload(**overrides):
    data = {
        "tournamentName": "Autona Cup",
        "eventName": "Singles",
        "numEntrants": 128,
        "address": "Tokyo",
        "shortLink": "autona",
        "startAt": 1735689600,
        "endAt": 1735776000,
        "eventStartAt": 1735689600,
        "eventEndAt": 1735776000,
        "initial_load": False,
    }
    data.update(overrides)
    return data


def test_a_payload_fills_every_field(service):
    assert service.ApplyTournamentData(_payload()) is True
    assert _get("tournamentName") == "Autona Cup"
    assert _get("eventName") == "Singles"
    assert _get("numEntrants") == 128
    assert _get("address") == "Tokyo"
    assert _get("shortLink") == "autona"


def test_a_date_field_is_published_through_the_injected_formatter(service):
    service.ApplyTournamentData(_payload())
    assert _get("startAt") == "DATE:" + time.strftime(
        "%Y-%m-%d", time.localtime(1735689600))


def test_a_date_field_keeps_its_raw_timestamp_for_a_late_built_panel(service):
    service.ApplyTournamentData(_payload())
    assert service.RawDate("startAt") == 1735689600


def test_the_short_date_keys_are_published_as_platform_short_dates(service):
    service.ApplyTournamentData(_payload())
    assert _get("endAt") == time.strftime("%x", time.localtime(1735776000))


def test_an_empty_short_date_key_is_published_as_is(service):
    service.ApplyTournamentData(_payload(endAt=""))
    assert _get("endAt") == ""


def test_a_new_payload_clears_the_fields_the_previous_one_filled(service):
    service.ApplyTournamentData(_payload())
    service.ApplyTournamentData(
        {"tournamentName": "Other Cup", "initial_load": False})
    # address came only from the first tournament and must not survive.
    assert _get("address") == ""
    assert _get("numEntrants") == 0
    assert _get("tournamentName") == "Other Cup"


def test_the_startup_restore_does_not_clear_anything(service):
    """initial_load is the previous tournament being restored, not a load."""
    service.ApplyTournamentData(_payload())
    service.ApplyTournamentData(
        {"tournamentName": "Autona Cup", "initial_load": True})
    assert _get("address") == "Tokyo"


def test_an_empty_payload_clears_the_form(service):
    """SetTournament(None) emits {} -- the 'unset tournament' path."""
    service.ApplyTournamentData(_payload())
    service.ApplyTournamentData({})
    assert _get("tournamentName") == ""
    assert _get("eventName") == ""
    assert _get("numEntrants") == 0


def test_a_broken_payload_reports_failure_and_drops_the_url(service):
    SettingsManager.Set("TOURNAMENT_URL", "https://start.gg/whatever")
    assert service.ApplyTournamentData(
        {"endAt": "not a timestamp", "initial_load": True}) is False
    assert SettingsManager.Get("TOURNAMENT_URL") == ""


def test_a_payload_is_one_save_cycle(service):
    before = SM.revision
    service.ApplyTournamentData(_payload())
    assert SM.revision == before + 1


def test_reapplying_the_same_payload_publishes_nothing(service):
    service.ApplyTournamentData(_payload())
    if SM._export_thread is not None:
        SM._export_thread.join()
    before = SM.revision
    service.ApplyTournamentData(_payload())
    assert SM.revision == before


# ── the EVENT tab's fields (headless core Step 5, instalment 3) ──────────
#
# The panel's inputs call these instead of writing
# tournamentInfo.{objectName} from a lambda, and _ApplyKey applies the
# payload through the very same three operations -- so "one writer" is true
# of both directions, not just the provider one.

def test_typing_a_text_field_writes_it(service):
    assert service.SetTextField("eventName", "Singles") is True
    assert _get("eventName") == "Singles"


def test_retyping_the_same_text_writes_nothing(service):
    service.SetTextField("address", "Tokyo")
    if SM._export_thread is not None:
        SM._export_thread.join()
    before = SM.revision
    assert service.SetTextField("address", "Tokyo") is False
    assert SM.revision == before


def test_an_unknown_text_field_is_refused(service):
    """The old loop accepted whatever objectName it found under the tab."""
    assert service.SetTextField("gameTitleCombo", "Smash") is False
    assert _get("gameTitleCombo") is None


def test_typing_the_tournament_name_carries_into_the_display_name(service):
    """The follow rule rides on the field, not on a second connection.

    The panel used to connect FollowDisplayName to editingFinished next to
    the key write; folding it in here is what keeps the typed path and the
    payload path (_ApplyKey) applying the same rule.
    """
    service.Seed()
    service.SetTextField("tournamentName", "Autona Cup")
    assert _get("displayTournamentName") == "Autona Cup"


def test_the_follow_rule_runs_even_when_the_name_did_not_move(service):
    """Unconditional, exactly as both former callers were.

    Focusing the name field and leaving it writes nothing now, but an empty
    display name still adopts the name that is already there -- which is the
    behaviour the panel's separate connection used to provide.
    """
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    service.Seed()
    SM.Set(f"{PREFIX}.displayTournamentName", "")

    assert service.SetTextField("tournamentName", "Autona Cup") is False
    assert _get("displayTournamentName") == "Autona Cup"


def test_typing_a_number_field_writes_it(service):
    assert service.SetNumberField("numEntrants", 64) is True
    assert _get("numEntrants") == 64
    assert service.SetNumberField("numEntrants", 64) is False


def test_an_unknown_number_field_is_refused(service):
    assert service.SetNumberField("tournamentName", 64) is False


def test_picking_a_date_publishes_the_formatted_form(service):
    assert service.SetDateField("startAt", 1735689600) is True
    assert _get("startAt") == "DATE:" + time.strftime(
        "%Y-%m-%d", time.localtime(1735689600))


def test_picking_a_date_records_the_raw_timestamp(service):
    """The reason the typed path goes through the service at all.

    The state holds a formatted date that cannot be parsed back into a
    QDate, so the panel restores its QDateEdit from RawDate(). While a
    manual edit wrote the key directly, that ledger kept the *loaded*
    tournament's date -- and the panel's next re-render put it back over
    whatever the operator had picked.
    """
    service.ApplyTournamentData(_payload())
    assert service.RawDate("startAt") == 1735689600

    service.SetDateField("startAt", 1767225600)
    assert service.RawDate("startAt") == 1767225600


def test_an_unknown_date_field_is_refused(service):
    assert service.SetDateField("endAt", 1735689600) is False
    assert service.RawDate("endAt") is None


# ── 表示大会名 ────────────────────────────────────────────────────────────

def test_typing_a_display_name_writes_it(service):
    # The SCORE panel's 表示大会名 field (headless core Step 5). It used to
    # Set the key itself, which is why this operation did not exist.
    assert service.SetDisplayName("SPECIAL EDITION") is True
    assert _get("displayTournamentName") == "SPECIAL EDITION"
    assert service.SetDisplayName("SPECIAL EDITION") is False
    assert service.SetDisplayName(None) is True
    assert _get("displayTournamentName") == ""


def test_a_typed_display_name_stops_following_the_tournament_name(service):
    """The reason SetDisplayName must not touch the follow ledger.

    `_lastTournamentName` means "the tournament name the display name last
    followed". Updating it here would make the typed value look like it was
    still following, and the next rename would silently overwrite what the
    operator typed.
    """
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    service.Seed()

    service.SetDisplayName("SPECIAL EDITION")
    service.FollowDisplayName("Autona Cup 2")

    assert _get("displayTournamentName") == "SPECIAL EDITION"


def test_the_display_name_follows_a_manual_rename(service):
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    service.Seed()
    service.FollowDisplayName("Autona Cup 2")
    assert _get("displayTournamentName") == "Autona Cup 2"


def test_a_customised_display_name_survives_a_manual_rename(service):
    SM.Set(f"{PREFIX}.tournamentName", "Autona Cup")
    service.Seed()
    SM.Set(f"{PREFIX}.displayTournamentName", "SPECIAL EDITION")
    service.FollowDisplayName("Autona Cup 2")
    assert _get("displayTournamentName") == "SPECIAL EDITION"


def test_selecting_a_tournament_overrides_a_customised_display_name(service):
    SM.Set(f"{PREFIX}.displayTournamentName", "SPECIAL EDITION")
    service.ApplyDisplayNameFromSelection({"tournamentName": "Other Cup"})
    assert _get("displayTournamentName") == "Other Cup"


def test_the_startup_restore_does_not_override_the_display_name(service):
    SM.Set(f"{PREFIX}.displayTournamentName", "SPECIAL EDITION")
    service.ApplyDisplayNameFromSelection(
        {"tournamentName": "Autona Cup", "initial_load": True})
    assert _get("displayTournamentName") == "SPECIAL EDITION"


def test_clearing_the_tournament_does_not_override_the_display_name(service):
    SM.Set(f"{PREFIX}.displayTournamentName", "SPECIAL EDITION")
    service.ApplyDisplayNameFromSelection({})
    assert _get("displayTournamentName") == "SPECIAL EDITION"


def test_loading_a_tournament_end_to_end_names_the_display_name(service):
    """The whole chain the window runs: apply, then the selection override."""
    service.Seed()
    data = _payload()
    service.ApplyTournamentData(data)
    service.ApplyDisplayNameFromSelection(data)
    assert _get("displayTournamentName") == "Autona Cup"


def test_unsetting_a_tournament_clears_a_followed_display_name(service):
    service.Seed()
    data = _payload()
    service.ApplyTournamentData(data)
    service.ApplyDisplayNameFromSelection(data)
    service.ApplyTournamentData({})
    service.ApplyDisplayNameFromSelection({})
    assert _get("displayTournamentName") == ""
