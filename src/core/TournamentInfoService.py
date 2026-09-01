"""The tournament's own metadata: name, event, entrants, dates, venue.

``tournamentInfo.*`` was written by the EVENT panel and by nothing else.
``AutonaTournamentInfoWidget.UpdateData`` took the Start.gg payload, looked
up a widget whose ``objectName`` matched each key, drove that widget, and let
the widget's ``editingFinished`` / ``valueChanged`` / ``dateChanged`` lambda
write the state -- with a fallback branch that wrote the key directly for the
payload fields the panel has no input for. Which meant the panel was the only
sidebar page that could not be built lazily: an operator who never opened it
would have had no tournament name in the state at all, and the sidebar
button, the overlays and the thumbnail all read it from there.

Since headless core Step 4b this class is the writer (ADR-004 D2) and the
panel is a renderer, which is what let it join the other lazy pages
(startup-architecture-redesign.md §3, "TournamentInfo のヘッドレスサービス
抽出"). The Qt side keeps two things and no more:

- the *input* half -- an operator typing in the EVENT tab still writes
  through the objectName-to-keypath lambdas (that is category (a), Step 5);
- the short-date *format*, which is a QLocale question. It is injected as
  ``formatEventDate`` rather than imported, for the same reason
  ``StatsService.playersPerTeam`` is: core must not import Qt
  (tests/test_core_qt_free.py). Without one the platform short date is used,
  so a headless run still publishes the key.

Import side effects: none (agent-pitfalls #1). ``bootstrap_core()`` creates
the single instance.
"""
import time
import traceback

from loguru import logger

from . import StateSchema as schema
from .SettingsManager import SettingsManager
from .StateManager import StateManager

# The tournament icon, served out of ./layout/ and cache-busted by the OBS
# overlays with its mtime -- exactly like the stream org assets in
# StateSchema.STREAM_ASSET_VERSIONS. Relative to the process CWD.
TOURNAMENT_LOGO_PATH = "./layout/logo.png"


class TournamentInfoService:
    # Created once by bootstrap_core(); callers reach it as
    # TournamentInfoService.instance, matching the other singletons.
    instance: "TournamentInfoService" = None

    PREFIX = "tournamentInfo"

    # The EVENT tab's input fields, by objectName -- which *is* the key path
    # leaf (agent-pitfalls #4). Split by widget kind because the old code's
    # behaviour differed per kind, and the differences are load-bearing:
    # a QLineEdit was always rewritten, a QSpinBox / QDateEdit only when the
    # value actually moved. _SetIfChanged makes all three behave like the
    # latter, which is the same published state and one less spurious
    # changed key. See AutonaTournamentInfo.ui.
    TEXT_FIELDS = ("tournamentName", "eventName", "address")
    NUMBER_FIELDS = ("numEntrants",)
    DATE_FIELDS = ("startAt",)

    # Payload keys with no field on the tab. They are published as they
    # arrive, except these three timestamps, which are published as a short
    # date string (the ``exceptions`` list of the old UpdateData).
    SHORT_DATE_KEYS = ("endAt", "eventStartAt", "eventEndAt")

    def __init__(self, formatEventDate=None):
        # A second service would carry its own _lastTournamentName ledger,
        # and that ledger is the whole 表示大会名 follow rule: two of them
        # disagree about what "still unmodified" means, so an operator's
        # customised display name would be silently overwritten by whichever
        # instance the next payload happened to reach. Refusing construction
        # makes that attributable instead of mysterious. Same shape as
        # StateManagerBridge; rebuilding on purpose means clearing
        # `instance` first, which is exactly the deliberate act that should
        # show up in a diff (or a test fixture).
        if TournamentInfoService.instance is not None:
            raise RuntimeError(
                "TournamentInfoService already exists; it is a process-wide "
                "singleton created by bootstrap_core(). Use "
                "TournamentInfoService.instance instead of constructing "
                "another one.")

        self._formatEventDate = formatEventDate
        # Bookkeeping for the 表示大会名 follow rule below. Seeded from the
        # restored state by Seed().
        self._lastTournamentName = ""
        # Raw unix timestamps of the DATE_FIELDS last applied. The state
        # holds the *formatted* date, which cannot be parsed back into a
        # QDate, so a panel built after the tournament was loaded restores
        # its QDateEdit from here instead.
        self._rawDates = {}

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _SetIfChanged(key, value) -> bool:
        if StateManager.Get(key, None) == value:
            return False
        StateManager.Set(key, value)
        return True

    def _FormatEventDate(self, timestamp) -> str:
        if self._formatEventDate is not None:
            return self._formatEventDate(timestamp)
        # Headless fallback: the Qt form is QLocale's short date for the
        # export locale, this is the platform's. Same shape, and publishing
        # something beats publishing nothing.
        return time.strftime("%x", time.localtime(float(timestamp)))

    def RawDate(self, field):
        """The unix timestamp last applied to a date field, or None."""
        return self._rawDates.get(field)

    # ── seeds ────────────────────────────────────────────────────────────

    def Seed(self):
        """Normalise the restored tournamentInfo.* subtree at launch.

        Called from Window._SeedPanelState() inside its SaveBlock, alongside
        StateSchema.SeedPanelState(). It cannot live in the schema itself:
        the 表示大会名 follow rule below needs the "last seen tournament
        name" bookkeeping this instance carries.
        """
        display = f"{self.PREFIX}.displayTournamentName"
        name = StateManager.Get(f"{self.PREFIX}.tournamentName", "") or ""
        if StateManager.Get(display, None) is None:
            StateManager.Set(display, name)
        self._lastTournamentName = name

        # The text fields used to be seeded by the panel's own construction
        # (an empty QLineEdit emitting editingFinished wrote ""), so a fresh
        # install had the keys even with no tournament loaded. A lazy panel
        # does not, and the overlays read them. Missing keys only: a value
        # restored from program_state.json must not be blanked.
        for field in self.TEXT_FIELDS:
            path = f"{self.PREFIX}.{field}"
            if StateManager.Get(path, None) is None:
                StateManager.Set(path, "")

        self.PublishLogoVersion()

    def PublishLogoVersion(self):
        """Publish the tournament icon's mtime as a cache-bust version.

        Used to run from the panel's UpdateIcon(); the panel still calls it
        after a load / reset, but the launch-time publication has to happen
        whether or not the panel is ever opened -- an OBS overlay appends
        the version to /layout/logo.png straight away.
        """
        schema.PublishAssetVersion(
            f"{self.PREFIX}.logoVersion", TOURNAMENT_LOGO_PATH)

    # ── 表示大会名 (the display name the overlays render) ─────────────────

    def FollowDisplayName(self, newName):
        """Let the display name follow the tournament name while unmodified.

        Two sources feed 表示大会名 with different override semantics. This
        is the weaker one: a *manual* edit of the tournament name carries
        into the display name only while the display name has not been
        customised (it is empty, or still equal to the last tournament name
        we saw). ApplyDisplayNameFromSelection below is the stronger one.
        """
        newName = newName or ""
        display = f"{self.PREFIX}.displayTournamentName"
        current = StateManager.Get(display, "")
        if current == "" or current == self._lastTournamentName:
            self._SetIfChanged(display, newName)
        self._lastTournamentName = newName

    def ApplyDisplayNameFromSelection(self, data):
        """A Start.gg tournament (re)selection overrides a manual edit.

        Picking a tournament is an explicit "use this one", so unlike the
        follow rule above this wins over a customised display name. Skipped
        for the startup restore of the previous tournament (initial_load)
        and for the empty payload emitted when the tournament is cleared --
        neither is a user selection.
        """
        if not isinstance(data, dict):
            return
        name = data.get("tournamentName", "") or ""
        if not name or data.get("initial_load"):
            return
        self._SetIfChanged(f"{self.PREFIX}.displayTournamentName", name)
        # Keep the follow rule's bookkeeping consistent with what was just
        # written, whatever order the two run in.
        self._lastTournamentName = name

    # ── the provider payload ─────────────────────────────────────────────

    def ApplyTournamentData(self, data) -> bool:
        """Publish a tournament_data_updated payload. False on failure.

        A failure means one key could not be applied; the tournament URL is
        dropped (it is the likeliest cause) and the rest of the payload is
        abandoned, exactly as the old ``break`` did. Reporting it to the
        operator is the caller's job -- that is a dialog, i.e. Qt.
        """
        logger.info("Tournament Data: " + str(data))
        if not isinstance(data, dict):
            return True

        with StateManager.SaveBlock():
            # A payload that is not the startup restore replaces the whole
            # form, so stale fields are cleared first: a tournament without
            # a venue address must not keep the previous one's.
            if not data.get("initial_load"):
                self._ClearFields()

            for key in list(data.keys()):
                try:
                    self._ApplyKey(key, data.get(key))
                except Exception:
                    logger.error(traceback.format_exc())
                    SettingsManager.Set("TOURNAMENT_URL", '')
                    return False
        return True

    def _ClearFields(self):
        for field in self.TEXT_FIELDS:
            self._SetIfChanged(f"{self.PREFIX}.{field}", "")
            if field == "tournamentName":
                # The blanking is an edit like any other, so the display
                # name follows it -- which is what lets the next payload's
                # name be adopted even when the previous one had been
                # followed into it.
                self.FollowDisplayName("")
        for field in self.NUMBER_FIELDS:
            self._SetIfChanged(f"{self.PREFIX}.{field}", 0)

    def _ApplyKey(self, key, value):
        path = f"{self.PREFIX}.{key}"

        if value is not None and key in self.TEXT_FIELDS:
            self._SetIfChanged(path, value)
            if key == "tournamentName":
                self.FollowDisplayName(value)
            return

        if value is not None and key in self.NUMBER_FIELDS:
            self._SetIfChanged(path, value)
            return

        if value is not None and key in self.DATE_FIELDS:
            self._rawDates[key] = value
            self._SetIfChanged(path, self._FormatEventDate(value))
            return

        # No field on the tab (or a null value for one): publish as-is.
        info = value
        if key in self.SHORT_DATE_KEYS and value != "":
            info = time.strftime("%x", time.localtime(float(value)))
        self._SetIfChanged(path, info)
