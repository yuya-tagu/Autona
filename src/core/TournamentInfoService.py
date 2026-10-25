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
抽出"). Step 5's third instalment brought the other direction here too: an
operator typing in the EVENT tab calls ``SetTextField`` / ``SetNumberField``
/ ``SetDateField`` instead of writing ``tournamentInfo.{objectName}`` from a
lambda, so ``tournamentInfo.*`` has exactly one writer whichever end the
value came from -- and the payload path applies each field through the very
same three operations.

The Qt side keeps two things and no more:

- the *widgets*: which line edit holds which field, and what a QDate means
  (the panel converts it to a unix timestamp, which is what this service
  stores and formats);
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

    # The EVENT tab's input fields. Aliases of the schema tuples rather than
    # a second spelling of them: the same strings are what the panel passes
    # to findChild, and a catalog that half the code follows is the drift
    # StateSchema exists to end. Split by widget kind because the old code's
    # behaviour differed per kind, and the differences are load-bearing:
    # a QLineEdit was always rewritten, a QSpinBox / QDateEdit only when the
    # value actually moved. _SetIfChanged makes all three behave like the
    # latter, which is the same published state and one less spurious
    # changed key. See AutonaTournamentInfo.ui.
    TEXT_FIELDS = schema.TOURNAMENT_TEXT_FIELDS
    NUMBER_FIELDS = schema.TOURNAMENT_NUMBER_FIELDS
    DATE_FIELDS = schema.TOURNAMENT_DATE_FIELDS

    # Payload keys with no field on the tab. They are published as they
    # arrive, except these three timestamps, which are published as a short
    # date string (the ``exceptions`` list of the old UpdateData).
    SHORT_DATE_KEYS = schema.TOURNAMENT_SHORT_DATE_KEYS

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

    @classmethod
    def Key(cls, field: str) -> str:
        """The full key path of one ``tournamentInfo`` leaf."""
        return f"{cls.PREFIX}.{field}"

    # Write `key` only when it would actually change; True if written (the
    # shared `StateManager.SetIfChanged`; see TEXT_FIELDS above for why the
    # EVENT tab's fields all take this path).
    _SetIfChanged = staticmethod(StateManager.SetIfChanged)

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
        display = self.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY)
        name = StateManager.Get(
            self.Key(schema.TOURNAMENT_NAME_KEY), "") or ""
        if StateManager.Get(display, None) is None:
            StateManager.Set(display, name)
        self._lastTournamentName = name

        # The text fields used to be seeded by the panel's own construction
        # (an empty QLineEdit emitting editingFinished wrote ""), so a fresh
        # install had the keys even with no tournament loaded. A lazy panel
        # does not, and the overlays read them. Missing keys only: a value
        # restored from the persisted state (user_data/state.json) must not
        # be blanked.
        for field in self.TEXT_FIELDS:
            path = self.Key(field)
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
            self.Key(schema.TOURNAMENT_LOGO_VERSION_KEY),
            TOURNAMENT_LOGO_PATH)

    # ── the EVENT tab's fields (typed, or applied from a payload) ────────
    #
    # One operation per widget kind, and both directions go through them:
    # the panel's editingFinished / valueChanged / dateChanged handlers, and
    # _ApplyKey below. That is the point of the instalment -- the old code
    # had the payload drive the widgets so that *their* signals wrote the
    # state, which is why "what does loading a tournament write" could only
    # be answered by building the panel.

    def SetTextField(self, field, value) -> bool:
        """Set one of the tab's text fields. False if `field` is not one.

        The old loop accepted whatever objectName it found under the tab,
        which is the property that made the .ui file the schema. Rejecting an
        unknown field is what ends that -- and it is why the panel logs
        loudly when findChild comes up empty instead of silently wiring
        nothing.
        """
        if field not in self.TEXT_FIELDS:
            logger.warning(
                f"TournamentInfoService.SetTextField: unknown field {field}")
            return False
        changed = self._SetIfChanged(self.Key(field), value)
        if field == schema.TOURNAMENT_NAME_KEY:
            # Unconditional, exactly as both former callers were: the panel
            # connected FollowDisplayName to editingFinished next to the
            # write, and _ApplyKey called it after _SetIfChanged without
            # looking at the result. It is what lets an empty display name
            # adopt an unchanged tournament name.
            self.FollowDisplayName(value)
        return changed

    def SetNumberField(self, field, value) -> bool:
        """Set one of the tab's numeric fields. False if `field` is not one."""
        if field not in self.NUMBER_FIELDS:
            logger.warning(
                f"TournamentInfoService.SetNumberField: unknown field {field}")
            return False
        return self._SetIfChanged(self.Key(field), value)

    def SetDateField(self, field, timestamp) -> bool:
        """Set one of the tab's date fields from a unix timestamp.

        Published as a formatted short date -- that is the form the overlays
        and the thumbnail read, and it cannot be parsed back into a QDate,
        so the raw timestamp is kept here for a panel built later (RawDate).
        Keeping it on the *typed* path too is what stops the panel's own
        re-render from putting the loaded tournament's date back over the
        one an operator just picked.

        Deliberately unguarded against a malformed timestamp: the payload
        path relies on the exception to abandon the rest of the payload
        (ApplyTournamentData), and the panel cannot produce one -- its
        QDateEdit has a minimum of 2000-01-01.
        """
        if field not in self.DATE_FIELDS:
            logger.warning(
                f"TournamentInfoService.SetDateField: unknown field {field}")
            return False
        formatted = self._FormatEventDate(timestamp)
        self._rawDates[field] = timestamp
        return self._SetIfChanged(self.Key(field), formatted)

    # ── 表示大会名 (the display name the overlays render) ─────────────────

    def SetDisplayName(self, name) -> bool:
        """The operator typed a 表示大会名 directly. True if it changed.

        The strongest of the three writers, and the simplest: it just writes.
        `_lastTournamentName` is deliberately *not* updated -- that ledger
        means "the tournament name the display name last followed", and
        leaving it where it is is what makes `FollowDisplayName` below treat
        the typed value as customised and stop overwriting it.

        The field lives on the SCORE panel (and the crew board), not the
        EVENT tab, which is why this is an operation rather than part of
        `ApplyTournamentData`.
        """
        return self._SetIfChanged(
            self.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY),
            "" if name is None else str(name))

    def FollowDisplayName(self, newName):
        """Let the display name follow the tournament name while unmodified.

        Two sources feed 表示大会名 with different override semantics. This
        is the weaker one: a *manual* edit of the tournament name carries
        into the display name only while the display name has not been
        customised (it is empty, or still equal to the last tournament name
        we saw). ApplyDisplayNameFromSelection below is the stronger one.
        """
        newName = newName or ""
        display = self.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY)
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
        name = data.get(schema.TOURNAMENT_NAME_KEY, "") or ""
        if not name or data.get("initial_load"):
            return
        self._SetIfChanged(
            self.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY), name)
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
            # Through the same operation the panel calls: blanking the name
            # is an edit like any other, so the display name follows it --
            # which is what lets the next payload's name be adopted even
            # when the previous one had been followed into it.
            self.SetTextField(field, "")
        for field in self.NUMBER_FIELDS:
            self.SetNumberField(field, 0)

    def _ApplyKey(self, key, value):
        if value is not None:
            if key in self.TEXT_FIELDS:
                self.SetTextField(key, value)
                return
            if key in self.NUMBER_FIELDS:
                self.SetNumberField(key, value)
                return
            if key in self.DATE_FIELDS:
                self.SetDateField(key, value)
                return

        # No field on the tab (or a null value for one): publish as-is.
        info = value
        if key in self.SHORT_DATE_KEYS and value != "":
            info = time.strftime("%x", time.localtime(float(value)))
        self._SetIfChanged(self.Key(key), info)
