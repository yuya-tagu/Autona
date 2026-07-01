from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from .AutonaPlayerDB import AutonaPlayerDB
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from . import ui_constants as UI


class AutonaCommentaryWidget(QDockWidget):
    ChangeCommDataSignal = Signal(int, object)
    LoadCommFromTagSignal = Signal(int, str, bool)

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowTitle(QApplication.translate("app", "Commentary"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget = QWidget()
        self.widget.setMinimumWidth(0)
        self.widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(self.widget)
        self.widget.setLayout(QVBoxLayout())
        self.widget.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)

        topOptions = QWidget()
        topOptions.setLayout(QHBoxLayout())
        topOptions.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)

        self.widget.layout().addWidget(topOptions)
        
        self.ChangeCommDataSignal.connect(self.SetData)
        self.LoadCommFromTagSignal.connect(self.LoadCommentatorFromTag)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        topOptions.layout().addWidget(col)
        self.commentatorNumber = QSpinBox()
        row = QWidget()
        row.setLayout(QHBoxLayout())
        commsNumber = QLabel(QApplication.translate("app", "Number of commentators"))
        commsNumber.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        row.layout().addWidget(commsNumber)
        row.layout().addWidget(self.commentatorNumber)
        self.commentatorNumber.valueChanged.connect(
            lambda val: self.SetCommentatorNumber(val))
        
        self.characterNumber = QSpinBox()
        charNumber = QLabel(QApplication.translate("app", "Characters per player"))
        charNumber.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        row.layout().addWidget(charNumber)
        row.layout().addWidget(self.characterNumber)
        self.characterNumber.valueChanged.connect(self.SetCharacterNumber)
        
        self.eyeBt = QToolButton()
        self.eyeBt.setIcon(QIcon(UI.icon_path('eye.svg')))
        self.eyeBt.setSizePolicy(
            QSizePolicy.Maximum, QSizePolicy.Fixed)
        col.layout().addWidget(self.eyeBt, Qt.AlignmentFlag.AlignRight)
        self.eyeBt.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu()
        self.eyeBt.setMenu(menu)

        menu.addSection("Players")

        self.elements = [
            [QApplication.translate("app", "Real Name"),              ["real_name"],                         "show_name"],
            [QApplication.translate("app", "Twitter"),                ["twitter"],                           "show_social"],
            [QApplication.translate("app", "Seed"),                   ["seed", "seedLabel"],                 "show_seed"],
            [QApplication.translate("app", "Location"),               ["state", "country"],                  "show_location"],
            [QApplication.translate("app", "Characters"),             ["characters"],                        "show_characters"],
            [QApplication.translate("app", "Additional information"), ["custom_textbox"],                    "show_additional"],
        ]
        for element in self.elements:
            action: QAction = self.eyeBt.menu().addAction(element[0])
            action.setCheckable(True)
            action.setChecked(SettingsManager.Get(f"display_options.{element[2]}", True))
            action.toggled.connect(
                lambda toggled, action=action, element=element: self.ToggleElements(action, element[1]))
        
        row.layout().addWidget(self.eyeBt)
        col.layout().addWidget(row)

        scrollArea = QScrollArea()
        scrollArea.setWidgetResizable(True)

        self.widgetArea = QWidget()
        self.widgetArea.setLayout(QVBoxLayout())
        self.widgetArea.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Maximum)
        scrollArea.setWidget(self.widgetArea)

        self.widget.layout().addWidget(scrollArea)

        self.commentaryWidgets: list[AutonaScoreboardPlayerWidget] = []

        StateManager.Set("commentary", {})
        self.commentatorNumber.setValue(2)
        self.characterNumber.setValue(1)
        
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self.SetDefaultsFromAssets
        )

        for x, element in enumerate(self.elements, start=1):
            action: QAction = self.eyeBt.menu().actions()[x]
            self.ToggleElements(action, element[1])

    def SetData(self, index, data):
        if index > len(self.commentaryWidgets):
            self.SetCommentatorNumber(index+1)
            self.commentatorNumber.setValue(index+1)

        logger.info(index)

        commentatorWidget = self.commentaryWidgets[index]
        logger.info(commentatorWidget)
        commentatorWidget.SetData(data, False, False)

    def LoadCommentatorFromTag(self, index: int, tag: str, no_mains: bool):
        if index > len(self.commentaryWidgets) - 1:
            self.SetCommentatorNumber(index+1)
            self.commentatorNumber.setValue(index+1)

        playerData = AutonaPlayerDB.GetPlayerFromTag(tag)
        if playerData:
            widget = self.commentaryWidgets[index]
            widget.SetData(playerData, False, True, no_mains)
            return True
        return False

    def SetCommentatorNumber(self, number):
        while len(self.commentaryWidgets) < number:
            comm = AutonaScoreboardPlayerWidget(
                index=len(self.commentaryWidgets)+1,
                teamNumber=0,
                path=f'commentary.{len(self.commentaryWidgets)+1}',
                customName="Commentator"
            )

            comm.btMoveUp.clicked.connect(
                lambda x=None, index=len(self.commentaryWidgets): self.MoveUp(index))

            comm.btMoveDown.clicked.connect(
                lambda x=None, index=len(self.commentaryWidgets): self.MoveDown(index))

            self.commentaryWidgets.append(comm)
            self.widgetArea.layout().addWidget(comm)

        while len(self.commentaryWidgets) > number:
            comm = self.commentaryWidgets[-1]
            comm.setParent(None)
            self.commentaryWidgets.remove(comm)

        self._compactCommentaryState(number)

    def _compactCommentaryState(self, number):
        # Drop non-numeric keys and indices outside [1, number], then
        # renumber remaining entries so commentary.{1..N} stays
        # contiguous (Design.md §3.1.2 / §8 T-2).
        commentary = StateManager.Get('commentary')
        if not commentary:
            return

        with StateManager.SaveBlock():
            survivors = sorted(
                ((int(k), v) for k, v in list(commentary.items())
                 if k.isnumeric() and 1 <= int(k) <= number),
                key=lambda kv: kv[0],
            )
            for k in list(commentary.keys()):
                StateManager.Unset(f'commentary.{k}')
            for new_idx, (_old_idx, value) in enumerate(survivors, start=1):
                StateManager.Set(f'commentary.{new_idx}', value)

    def MoveUp(self, index):
        if index > 0:
            self.SwapComms(index, index-1)

    def MoveDown(self, index):
        if index < len(self.commentaryWidgets)-1:
            self.SwapComms(index, index+1)

    def SwapComms(self, index1, index2):
        self.commentaryWidgets[index1].SwapWith(self.commentaryWidgets[index2])

    def ToggleElements(self, action: QAction, elements):
        for pw in self.commentaryWidgets:
            for element in elements:
                w = pw.findChild(QWidget, element)
                # Some objectNames (e.g. removed inline labels) may not exist
                # on the player widget; skip rather than crash the toggle.
                if w is not None:
                    w.setVisible(action.isChecked())
    
    def SetCharacterNumber(self, value):
        for pw in self.commentaryWidgets:
            pw.SetCharactersPerPlayer(value)
    
    def SetDefaultsFromAssets(self):
        if StateManager.Get(f'game.defaults'):
            characters = StateManager.Get(f'game.defaults.characters_per_player', 1)
        else:
            characters = 1
        self.characterNumber.setValue(characters)
