"""Qt adapter of the country catalog (ADR-005 S1c).

The data -- countries, regions, custom flags, the city -> region lookup --
lives in the Qt-free `src/core/CountryCatalog.py`; read it there
(`CountryCatalog.instance`), from any thread. What is left here is the part
that needs Qt: the shared country QStandardItemModel every country combo
shows, its flag icons, the translated "custom flags" separator, and the
`countriesUpdated` signal.
"""
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from ..core.CountryCatalog import CountryCatalog
# NOTE: `countryflag` / `country_converter` were dropped here. They were only
# used by the old getflag()-based flag lookup, which was replaced by the
# hand-rolled emoji offset now in core.CountryCatalog.flag_emoji().
# country_converter eagerly imports pandas, so these dead imports cost ~1.4s
# of startup for nothing.


class AutonaCountryHelperSignals(QObject):
    # Emitted right before a reload clears the shared model and refills it in
    # place. Every combo attached to the model reports index changes on the
    # way through (-1, then the first row), so a consumer whose combo index
    # feeds the state holds that path from here until `countriesUpdated`,
    # then re-selects what it showed before. Not emitted for the first load,
    # which creates the model rather than refilling it.
    countriesAboutToReload = Signal()
    # After every load, first or reload -- also when a reload's refill raised
    # half-way, so a consumer that held its path on countriesAboutToReload is
    # always released.
    countriesUpdated = Signal()


class AutonaCountryHelper(QObject):
    instance: "AutonaCountryHelper" = None

    # The one country model every country combo and completer is attached
    # to. Created by the first successful LoadCountries() and kept for the
    # process lifetime: a later load refills its rows in place instead of
    # swapping in a new model (agent-pitfalls #13 / #15), so no view, proxy
    # or completer can ever be left pointing at a model that was dropped.
    countryModel = None
    signals = AutonaCountryHelperSignals()

    def __init__(self) -> None:
        super().__init__()

    def init(self):
        AutonaCountryHelper.LoadCountries()

    @staticmethod
    def LoadCountries():
        """Load the catalog (under the current locale) and fill the model.

        On GUI thread only (it builds QStandardItems). When the dataset
        cannot be read the model is left as it was -- None before the first
        successful load, which is what the player widgets' fallback checks.
        """
        if not CountryCatalog.instance.Load():
            return
        snap = CountryCatalog.instance.Current()

        # Rows first, then into the live model in one stretch (see below).
        rows = []

        noCountry = QStandardItem()
        noCountry.setData({}, Qt.ItemDataRole.UserRole)
        rows.append(noCountry)

        for text, data in snap.countryRows:
            item = QStandardItem()
            item.setIcon(QIcon(data["asset"]))
            item.setData(data, Qt.ItemDataRole.UserRole)
            item.setData(text, Qt.ItemDataRole.EditRole)
            rows.append(item)

        if snap.customFlagRows:
            separator = QStandardItem()
            separator.setData("    " + QApplication.translate("app",
                              "Custom Flags").upper() + "    ", Qt.ItemDataRole.EditRole)
            separator.setEnabled(False)
            separator.setSelectable(False)
            rows.append(separator)

        for text, data in snap.customFlagRows:
            item = QStandardItem()
            item.setIcon(QIcon(data["asset"]))
            item.setData(data, Qt.ItemDataRole.UserRole)
            item.setData(text, Qt.ItemDataRole.EditRole)
            rows.append(item)

        signals = AutonaCountryHelper.signals
        model = AutonaCountryHelper.countryModel
        reloading = model is not None
        if reloading:
            # A reload: same model, new rows. The attached combos are about
            # to see their rows vanish and come back; let them hold the
            # combo -> state path first (see countriesAboutToReload).
            signals.countriesAboutToReload.emit()
        else:
            model = QStandardItemModel()
            AutonaCountryHelper.countryModel = model
        try:
            if reloading:
                model.clear()
            # One row at a time through the model's own appendRow():
            # invisibleRootItem().appendRows() would register every item on
            # the root item's wrapper for good (agent-pitfalls #13b).
            for item in rows:
                model.appendRow(item)
        finally:
            signals.countriesUpdated.emit()
