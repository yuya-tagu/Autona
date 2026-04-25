import math
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from .AutonaBracket import Bracket, BracketSet
from .AutonaPlayerList import *
from .StateManager import StateManager
from . import ui_constants as UI
import traceback
from loguru import logger


def _roundIndex(p: int) -> int:
    """Double-elimination placement → expected round-of-elimination index.

        r(1)=0, r(2)=1, r(3)=2, r(4)=3,
        r(5..6)=4, r(7..8)=5, r(9..12)=6, r(13..16)=7,
        r(17..24)=8, r(25..32)=9, r(33..48)=10, r(49..64)=11, ...
    """
    if p is None or p < 1:
        return 0
    if p == 1:
        return 0
    # k = ceil(log2(p)); bit_length avoids float-edge issues.
    k = (p - 1).bit_length()
    # Split each [2^(k-1)+1 .. 2^k] band in two: lower ≤ 3·2^(k-2), upper above.
    if 4 * p <= 3 * (1 << k):
        return 2 * (k - 1)
    return 2 * (k - 1) + 1


def _upsetFactor(winnerSeed: int, loserSeed: int) -> int:
    """UF = r(winnerSeed) - r(loserSeed).

    Positive for upsets (weaker seed won), negative for expected results.
    The roundIndex table maps each seed to its expected exit round in a
    double-elimination bracket, so UF measures how many elimination-rounds
    the winner out-performed (or under-performed) their seed.

    Examples:
        1 vs 5, seed 5 wins  → +4   (r(5) - r(1) = 4 - 0)
        1 vs 2, seed 2 wins  → +1
        5 vs 1, seed 1 wins  → -4   (non-upset, white)
    """
    if winnerSeed <= 0 or loserSeed <= 0:
        return 0
    return _roundIndex(winnerSeed) - _roundIndex(loserSeed)


# Fixed per-match widget height in pixels. Setting this explicitly keeps
# bracket rounds geometrically aligned: the between-match spacing for round
# R is derived from MATCH_UNIT_PX + GAP_PX, so if the widget's rendered
# height deviates from this constant, later rounds drift vertically from
# their ideal "centred between two prior matches" positions.
MATCH_UNIT_PX = 75

# Inter-match breathing room between the UF label of one match and the
# top of the next match. Included in the scale math so that a round-R
# match still sits vertically centred between the two prior-round matches
# it's fed from.
GAP_PX = 8

# Full vertical "slot" occupied by one match including its gap below.
SLOT_PX = MATCH_UNIT_PX + GAP_PX

# Horizontal breathing room between round columns so the connection lines
# have room to bend cleanly.
ROUND_GAP_PX = 24


def _upsetFactorStyle(uf: int) -> str:
    """Piecewise gradient matching Autona's icon palette:
        0 = white, 3 = gold (#FFD700), 6 = crimson (#C41232), 10 = magenta.
    Non-upsets render as plain white; actual upsets bold up and warm toward
    the app's signature crimson before cooling to magenta on extreme upsets.
    """
    def _lerp(a, b, t):
        return int(round(a + t * (b - a)))

    if uf <= 0:
        r, g, b = 255, 255, 255
        weight = "normal"
    else:
        weight = "bold"
        if uf <= 3:
            t = uf / 3.0
            r = _lerp(255, 255, t)
            g = _lerp(255, 215, t)
            b = _lerp(255, 0, t)
        elif uf <= 6:
            t = (uf - 3) / 3.0
            r = _lerp(255, 196, t)
            g = _lerp(215, 18, t)
            b = _lerp(0, 50, t)
        elif uf <= 10:
            t = (uf - 6) / 4.0
            r = _lerp(196, 255, t)
            g = _lerp(18, 0, t)
            b = _lerp(50, 255, t)
        else:
            r, g, b = 255, 0, 255
    return f"color: rgb({r}, {g}, {b}); font-weight: {weight}; font-size: 9pt;"


class BracketSetWidget(QWidget):
    def __init__(self, bracketSet: BracketSet = None, bracketView: "AutonaBracketView" = None, *args) -> None:
        # Pass bracketView as parent so these don't flash as top-level windows
        # during bracket rebuilds.
        super().__init__(bracketView, *args)
        self.setLayout(QVBoxLayout())

        self.playerId: list = []
        self.name: list = []
        self.score: list = []

        self.bracketSet = bracketSet
        self.bracketView = bracketView

        for i in [0, 1]:
            hbox = QWidget()
            hbox.setLayout(QHBoxLayout())
            hbox.layout().setContentsMargins(0, 0, 0, 0)
            hbox.layout().setSpacing(2)
            self.layout().addWidget(hbox)
            hbox.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

            idLabel = QLineEdit()
            idLabel.setDisabled(True)
            idLabel.setMinimumWidth(30)
            idLabel.setMaximumWidth(30)
            idLabel.setFont(QFont(idLabel.font().family(), 8))
            idLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.playerId.append(idLabel)
            hbox.layout().addWidget(idLabel)

            name = QLineEdit()
            name.setMinimumWidth(120)
            name.setMaximumWidth(120)
            name.setDisabled(True)
            # Override Qt's disabled palette so player names keep the
            # current theme's primary text colour against the dark
            # bracket bg — the default greyed-out disabled text is
            # unreadable. Reads palette() so it tracks light/dark.
            name.setStyleSheet(
                f"QLineEdit:disabled {{ color: {UI.palette()['text_primary']}; "
                "background-color: rgba(0, 0, 0, 0); }}"
            )
            self.name.append(name)
            hbox.layout().addWidget(name)
            name.sizePolicy().setRetainSizeWhenHidden(False)

            score = QSpinBox()
            score.setMinimum(-1)
            # Compact score column: no spin arrows.
            score.setButtonSymbols(QAbstractSpinBox.NoButtons)
            score.setMaximumWidth(45)
            score.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.score.append(score)
            hbox.layout().addWidget(score)
            score.setValue(-1)
            score.valueChanged.connect(
                lambda newVal, i=i: self.SetScore(i, newVal))

        # Upset factor label — replaces the old "Finished" checkbox. Value
        # and colour are assigned in Update() once playerIds and scores are
        # known. Right-aligned below the second player row so it reads as
        # an annotation of the decided match rather than a centred header.
        self.upsetLabel = QLabel("")
        self.upsetLabel.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.upsetLabel.setStyleSheet("font-size: 9pt;")
        self.layout().addWidget(self.upsetLabel)

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.sizePolicy().setRetainSizeWhenHidden(True)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(2)
        # Fixed height so bracket column layout math matches reality. See
        # MATCH_UNIT_PX docstring.
        self.setFixedHeight(MATCH_UNIT_PX)

        self._applyVisibility()

    def _applyVisibility(self):
        if self.bracketSet is None:
            return
        if self.bracketSet.isByeSet:
            self.hide()
        else:
            self.show()

    def SetScore(self, id, score, updateDisplay=True):
        self.bracketSet.score[id] = score
        if updateDisplay:
            self.bracketSet.bracket.UpdateBracket()
            self.bracketView.Update()

    def Update(self):
        if self.bracketSet is None:
            return

        self.playerId[0].setText(str(self.bracketSet.playerIds[0]))
        self.playerId[1].setText(str(self.bracketSet.playerIds[1]))

        if self.playerId[0].text() == "-2":
            self.playerId[0].setText("")
        if self.playerId[1].text() == "-2":
            self.playerId[1].setText("")

        self.score[0].blockSignals(True)
        self.score[1].blockSignals(True)
        self.score[0].setValue(
            min(self.bracketSet.score[0], self.score[0].maximum()))
        self.score[1].setValue(
            min(self.bracketSet.score[1], self.score[1].maximum()))
        self.score[0].blockSignals(False)
        self.score[1].blockSignals(False)

        if self.bracketSet.score[0] > self.bracketSet.score[1]:
            self.score[0].setStyleSheet("background-color: rgba(0, 255, 0, 50);")
            self.score[1].setStyleSheet("background-color: rgba(0, 0, 0, 80);")
        elif self.bracketSet.score[0] < self.bracketSet.score[1]:
            self.score[1].setStyleSheet("background-color: rgba(0, 255, 0, 50);")
            self.score[0].setStyleSheet("background-color: rgba(0, 0, 0, 80);")
        else:
            self.score[0].setStyleSheet("background-color: rgba(0, 0, 0, 80);")
            self.score[1].setStyleSheet("background-color: rgba(0, 0, 0, 80);")

        for i in (0, 1):
            try:
                pid = self.bracketSet.playerIds[i]
                if pid > 0:
                    team = StateManager.Get(
                        f"bracket.players.slot.{pid}", {}) or {}
                    if team.get("name"):
                        teamName = team.get("name")
                    else:
                        teamName = " / ".join([p.get("name", "")
                                              for p in (team.get("player") or {}).values()])
                    self.name[i].setText(teamName)
                else:
                    self.name[i].setText("")
            except Exception:
                logger.error(traceback.format_exc())

        # Upset factor — winner-perspective, shown only when both players
        # are decided and the match has a clear winner.
        p0, p1 = self.bracketSet.playerIds
        s0, s1 = self.bracketSet.score
        if p0 > 0 and p1 > 0 and self.bracketSet.finished and s0 != s1:
            winnerSeed, loserSeed = (p0, p1) if s0 > s1 else (p1, p0)
            uf = _upsetFactor(winnerSeed, loserSeed)
            sign = "+" if uf > 0 else ""
            self.upsetLabel.setText(f"U{sign}{uf}")
            self.upsetLabel.setStyleSheet(_upsetFactorStyle(uf))
        else:
            self.upsetLabel.setText("")
            self.upsetLabel.setStyleSheet("font-size: 9pt;")

        self._applyVisibility()

        limitExportNumber, winnersOffset, losersOffset = \
            self.bracketView.GetLimitedExportingBracketOffsets()

        if self.bracketSet.pos[0] > 0:
            dim = (self.bracketSet.pos[0] - winnersOffset) <= 0
        elif self.bracketSet.pos[0] < 0:
            dim = (self.bracketSet.pos[0] + losersOffset) >= 0
        else:
            dim = False

        bg = "rgba(0, 0, 0, 80)" if dim else "rgba(0, 0, 0, 0)"
        ss = (
            f"QLineEdit:disabled {{ color: {UI.palette()['text_primary']}; "
            f"background-color: {bg}; }}"
        )
        self.name[0].setStyleSheet(ss)
        self.name[1].setStyleSheet(ss)


class AutonaBracketView(QGraphicsView):
    def __init__(self, bracket: Bracket, playerList: AutonaPlayerList = None,
                 bracketWidget=None, *args):
        super().__init__(*args)

        self.bracketWidget = bracketWidget
        self.playerList = playerList

        self._zoom = 0
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setFrameShape(QFrame.NoFrame)
        self.setDragMode(QGraphicsView.ScrollHandDrag)

        # Floating loading overlay. Lives outside the scene to avoid
        # touching QGraphicsProxyWidget lifecycle during fetch/render
        # swaps (a prior scene-based approach caused access violations).
        self.loadingOverlay = QLabel("取得中…\nLoading…", self)
        self.loadingOverlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loadingOverlay.setStyleSheet(
            f"color: {UI.palette()['text_primary']}; font-size: 14pt; "
            "background-color: rgba(0, 0, 0, 120);")
        self.loadingOverlay.hide()

        self.SetBracket(bracket)
        self.bracketLines = []

    def _sortedRoundKeys(self, positive=True):
        keys = [k for k in self.bracket.rounds.keys()
                if (int(k) > 0) == positive and int(k) != 0]
        keys.sort(key=lambda k: abs(int(k)))
        return keys

    def GetCutouts(self, forExport=False):
        """Compute (winnersCutout, losersCutout) as [startExclusive, endExclusive]
        round-number thresholds. With the Start.gg-driven bracket, the only
        source of cutoffs is the limitExport toggle; otherwise every round
        Start.gg returned is rendered.
        """
        winnersKeys = [int(k) for k in self._sortedRoundKeys(positive=True)]
        losersKeys = [abs(int(k)) for k in self._sortedRoundKeys(positive=False)]

        maxWinners = max(winnersKeys) if winnersKeys else 0
        maxLosers = max(losersKeys) if losersKeys else 0

        winnersCutout = [0, maxWinners + 1]
        losersCutout = [0, maxLosers + 1]

        # limitExport hides early rounds so the view only shows the top N.
        if self.bracketWidget is not None and self.bracketWidget.limitExport.isChecked():
            target = max(1, int(self.bracketWidget.limitExportNumber.value()))
            # Winners: show enough rounds to cover the last log2(target) matches
            # plus grand final + reset.
            winnersKeep = max(1, int(math.ceil(math.log2(max(target, 2))))) + 2
            losersKeep = max(1, 2 * int(math.ceil(math.log2(max(target, 2)))))

            winnersCutout[0] = max(0, maxWinners - winnersKeep)
            losersCutout[1] = maxLosers + 1  # keep all on losers' right side
            losersCutout[0] = max(0, maxLosers - losersKeep)

        return (winnersCutout, losersCutout)

    def ShowLoading(self):
        """Float a "取得中" label over the current scene while a phase-group
        fetch is in flight. The scene itself is not touched — SetBracket
        later rebuilds the scene and hides the overlay."""
        self.loadingOverlay.setGeometry(self.viewport().rect())
        self.loadingOverlay.show()
        self.loadingOverlay.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the loading overlay covering the full viewport on resize.
        if hasattr(self, "loadingOverlay"):
            self.loadingOverlay.setGeometry(self.viewport().rect())

    def SetBracket(self, bracket, **_legacyKwargs):
        """Render the given bracket. Legacy kwargs (progressionsIn/Out/
        winnersOnlyProgressions/customSeeding) are ignored — Start.gg provides
        the round structure directly now.
        """
        self.bracket = bracket

        # Loading overlay (if visible) is dismissed now that real data is
        # about to render.
        if hasattr(self, "loadingOverlay"):
            self.loadingOverlay.hide()

        self.bracketLines = []
        self._scene.clear()

        self.bracketLayout = QWidget()
        self.bracketLayout.setLayout(QVBoxLayout())

        self.winnersBracket = QWidget()
        self.winnersBracket.setLayout(QHBoxLayout())
        self.winnersBracket.layout().setSpacing(ROUND_GAP_PX)
        self.bracketLayout.layout().addWidget(self.winnersBracket)

        self.losersBracket = QWidget()
        self.losersBracket.setLayout(QHBoxLayout())
        self.losersBracket.layout().setSpacing(ROUND_GAP_PX)
        self.bracketLayout.layout().addWidget(self.losersBracket)

        self._scene.addWidget(self.bracketLayout)

        self.bracketWidgets = []
        self.winnersBracketWidgets = []
        self.losersBracketWidgets = []
        self.roundNameLabels = {}

        self.bracket.UpdateBracket()

        winnersCutout, losersCutout = self.GetCutouts()

        # Iterate rounds in a stable visual order: winners ascending, then
        # losers ascending by absolute value.
        roundOrder = self._sortedRoundKeys(positive=True) + \
            self._sortedRoundKeys(positive=False)

        if not roundOrder:
            empty = QLabel(QApplication.translate(
                "app",
                "トーナメント情報が未取得です。\n"
                "フェーズグループを選択してください。"
                "Start.ggがタイムアウトした場合は「Update bracket info」ボタンで再取得してください。\n"
                "\n"
                "No bracket data. "
                "Pick a phase group, or retry if Start.gg timed out."
            ))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {UI.palette()['text_secondary']}; "
                "font-size: 11pt; padding: 40px;")
            self.bracketLayout.layout().addWidget(empty)

        # Pass 1: collect rendered round info so each round can size its
        # top margin and inter-match spacing so match N sits vertically
        # centred between the two prior-round matches that feed into it.
        def _keep(roundNum, round):
            if not round:
                return False
            # Start.gg emits pre-resolved all-bye rounds (e.g. LR1-3 of a
            # 256-slot bracket with byes) that contain sets but render
            # invisibly via isByeSet. Treat them as absent so they neither
            # get a labelled column nor consume a displayIdx slot.
            if all(getattr(s, "isByeSet", False) for s in round):
                return False
            if roundNum > 0:
                return winnersCutout[0] < roundNum < winnersCutout[1]
            return losersCutout[0] < abs(roundNum) < losersCutout[1]

        winnersCounts = []
        losersCounts = []
        for roundKey in roundOrder:
            roundNum = int(roundKey)
            round = self.bracket.rounds[roundKey]
            if not _keep(roundNum, round):
                continue
            (winnersCounts if roundNum > 0 else losersCounts).append(len(round))

        winnersFirst = winnersCounts[0] if winnersCounts else 1
        losersFirst = losersCounts[0] if losersCounts else 1

        winnersDisplayIdx = 0
        losersDisplayIdx = 0
        for roundKey in roundOrder:
            roundNum = int(roundKey)
            round = self.bracket.rounds[roundKey]

            if not _keep(roundNum, round):
                continue

            if roundNum > 0:
                winnersDisplayIdx += 1
                displayIdx = winnersDisplayIdx
                scale = (winnersFirst / len(round)) if len(round) else 1
                currentBracket = self.winnersBracket
                currentWidgets = self.winnersBracketWidgets
            else:
                losersDisplayIdx += 1
                displayIdx = losersDisplayIdx
                scale = (losersFirst / len(round)) if len(round) else 1
                currentBracket = self.losersBracket
                currentWidgets = self.losersBracketWidgets

            # Position round-R so each match is vertically centred between
            # its two prior-round inputs, using SLOT_PX (= widget + gap) as
            # the unit so the baseline inter-match gap shows up even at
            # R=1 (scale=1 → spacing = GAP_PX).
            topOffset = int((scale - 1) * SLOT_PX / 2)
            spacing = int(scale * SLOT_PX - MATCH_UNIT_PX)

            layoutOuter = QWidget()
            currentBracket.layout().addWidget(layoutOuter)
            layoutOuter.setLayout(QVBoxLayout())
            # Zero out the outer column's margins/spacing so they don't
            # vary per column and contribute to vertical drift.
            layoutOuter.layout().setContentsMargins(0, 0, 0, 0)
            layoutOuter.layout().setSpacing(4)

            roundNameLabel = QLineEdit()
            roundNameLabel.setPlaceholderText(
                self.bracket.GetRoundName(
                    roundKey, winnersCutout, losersCutout,
                    displayIdx=displayIdx) or "")
            layoutOuter.layout().addWidget(roundNameLabel)
            self.roundNameLabels[roundKey] = roundNameLabel

            layoutRound = QWidget()
            layoutOuter.layout().addWidget(layoutRound)
            layoutRound.setLayout(QVBoxLayout())
            layoutRound.layout().setContentsMargins(0, topOffset, 0, 0)
            layoutRound.layout().setSpacing(spacing)

            roundWidgets = []
            for _set in round:
                wid = BracketSetWidget(_set, self)
                layoutRound.layout().addWidget(wid)
                roundWidgets.append(wid)
            # Absorb any excess column height at the bottom so the QHBoxLayout
            # can't redistribute it as inter-match padding when this column
            # is shorter than its siblings.
            layoutRound.layout().addStretch(1)
            self.bracketWidgets.append(roundWidgets)
            currentWidgets.append(roundWidgets)

        # Defer line drawing + fit to the next event loop tick. Calling
        # processEvents() synchronously inside a signal handler re-enters
        # Qt's event dispatch, which has caused access-violation crashes
        # while partial widget trees were being built/torn down.
        QTimer.singleShot(0, self._postLayoutRefresh)

    def _postLayoutRefresh(self):
        try:
            self.DrawLines()
            self.fitInView()
        except Exception:
            logger.error(traceback.format_exc())

    def GetLimitedExportingBracketOffsets(self):
        """Return (limitExportNumber, winnersOffset, losersOffset) used by
        Update() to renumber exported rounds and by BracketSetWidget to dim
        rounds that won't be exported.
        """
        limitExportNumber = -1
        winnersOffset = 0
        losersOffset = 0

        if self.bracketWidget is not None and self.bracketWidget.limitExport.isChecked():
            limitExportNumber = int(self.bracketWidget.limitExportNumber.value())
            winnersCutout, losersCutout = self.GetCutouts()
            winnersOffset = winnersCutout[0]
            losersOffset = losersCutout[0]

        return (limitExportNumber, winnersOffset, losersOffset)

    def Update(self):
        self.bracket.UpdateBracket()

        for round in self.bracketWidgets:
            for setWidget in round:
                for w in setWidget.score:
                    w.blockSignals(True)
                setWidget.Update()
                for w in setWidget.score:
                    w.blockSignals(False)

        # DrawLines needs resolved widget geometry — schedule after the
        # current event-loop tick instead of forcing a synchronous flush.
        QTimer.singleShot(0, self.DrawLines)

        StateManager.BlockSaving()

        # Keep legacy keys populated so bracket_ultimate overlay's diff
        # detection doesn't trip on undefined values.
        StateManager.Set("bracket.bracket.progressionsIn", 0)
        StateManager.Set("bracket.bracket.progressionsOut", 0)
        StateManager.Set("bracket.bracket.winnersOnly", True)
        StateManager.Set("bracket.bracket.winnersOnlyProgressions", True)

        limitExportNumber, winnersOffset, losersOffset = \
            self.GetLimitedExportingBracketOffsets()
        winnersCutout, losersCutout = self.GetCutouts(forExport=True)

        StateManager.Set("bracket.bracket.limitExportNumber", limitExportNumber)

        data = {}

        for roundKey, round in self.bracket.rounds.items():
            roundNum = int(roundKey)

            if roundNum > 0:
                if roundNum <= winnersCutout[0]:
                    continue
                if roundNum >= winnersCutout[1]:
                    continue
            elif roundNum < 0:
                if abs(roundNum) <= losersCutout[0]:
                    continue
                if abs(roundNum) >= losersCutout[1]:
                    continue

            if self.roundNameLabels.get(roundKey):
                roundName = self.roundNameLabels.get(roundKey).text()
                if roundName == "":
                    roundName = self.roundNameLabels.get(
                        roundKey).placeholderText()
            else:
                roundName = ""

            # Remap round keys when limitExport is on so the overlay receives
            # a contiguous 1,2,3... / -1,-2,... sequence starting from the
            # cutoff.
            exportRoundKey = roundKey
            if roundNum > 0:
                exportRoundKey = str(roundNum - winnersOffset)
                if int(exportRoundKey) <= 0:
                    continue
            elif roundNum < 0:
                exportRoundKey = str(roundNum + losersOffset)
                if int(exportRoundKey) >= 0:
                    continue

            data[exportRoundKey] = {
                "name": roundName,
                "sets": {}
            }

            for j, bs in enumerate(round):
                nextWin = bs.winNext.pos.copy() if bs.winNext else None
                nextLose = bs.loseNext.pos.copy() if bs.loseNext else None

                if nextWin:
                    if nextWin[0] > 0:
                        nextWin[0] -= winnersOffset
                    else:
                        nextWin[0] += losersOffset
                if nextLose:
                    if nextLose[0] < 0:
                        nextLose[0] += losersOffset
                    else:
                        nextLose[0] -= winnersOffset

                p1name = ""
                if bs.playerIds[0] > 0:
                    try:
                        p1tree: dict = StateManager.Get(
                            f"bracket.players.slot.{bs.playerIds[0]}.player")
                        p1name = " / ".join([p.get("name")
                                            for p in p1tree.values()])
                    except Exception:
                        pass

                p2name = ""
                if bs.playerIds[1] > 0:
                    try:
                        p2tree: dict = StateManager.Get(
                            f"bracket.players.slot.{bs.playerIds[1]}.player")
                        p2name = " / ".join([p.get("name")
                                            for p in p2tree.values()])
                    except Exception:
                        pass

                data[exportRoundKey]["sets"][j] = {
                    "playerId": bs.playerIds,
                    "score": bs.score,
                    "nextWin": nextWin,
                    "winSlot": bs.winNextSlot,
                    "nextLose": nextLose,
                    "loseSlot": bs.loseNextSlot,
                    "playerName": [p1name, p2name],
                    "completed": bs.finished,
                }

        StateManager.Set("bracket.bracket.rounds", data)
        StateManager.ReleaseSaving()

    def DrawLines(self):
        for element in self.bracketLines:
            self._scene.removeItem(element)

        self.bracketLines = []

        path = QPainterPath()
        dashedPath = QPainterPath()

        def pathBetween(setWidget, nextSetWidget):
            start = QPointF(
                setWidget.mapTo(self.bracketLayout, QPoint(0, 0)).x(),
                setWidget.mapTo(self.bracketLayout, QPoint(0, 0)).y()
            ) + QPointF(setWidget.width(), setWidget.height()/2)
            end = QPointF(
                nextSetWidget.mapTo(self.bracketLayout, QPoint(0, 0)).x(),
                nextSetWidget.mapTo(self.bracketLayout, QPoint(0, 0)).y()
            ) + QPointF(0, setWidget.height()/2)
            midpoint1 = QPointF(
                start.x() + (end.x() - start.x()) / 2, start.y())
            midpoint2 = QPointF(
                start.x() + (end.x() - start.x()) / 2, end.y())
            path.addPolygon(QPolygonF([start, midpoint1, midpoint2, end]))

        # Winners side connections — follow winNext references. They may jump
        # to the losers side (grand final inputs don't exist here; losers
        # final's winner goes into grand final, drawn below).
        for columnWidgets in self.winnersBracketWidgets:
            for setWidget in columnWidgets:
                if setWidget.isHidden():
                    continue
                nextSet = setWidget.bracketSet.winNext
                if nextSet is None:
                    continue
                nextWidget = self._findWidget(nextSet)
                if nextWidget is None or nextWidget.isHidden():
                    continue
                try:
                    pathBetween(setWidget, nextWidget)
                except Exception:
                    logger.error(traceback.format_exc())

        # Losers side — both winNext and loseNext from winners can land here.
        for columnWidgets in self.losersBracketWidgets:
            for setWidget in columnWidgets:
                if setWidget.isHidden():
                    continue
                nextSet = setWidget.bracketSet.winNext
                if nextSet is None:
                    continue
                nextWidget = self._findWidget(nextSet)
                if nextWidget is None or nextWidget.isHidden():
                    continue
                try:
                    pathBetween(setWidget, nextWidget)
                except Exception:
                    logger.error(traceback.format_exc())

        # Winners ↔ losers drop lines are intentionally not drawn. They
        # cross over the bracket visually and add noise without informing
        # viewers in practice.

        pen = QPen(Qt.gray, 4, Qt.SolidLine)
        pen2 = QPen(Qt.black, 6, Qt.SolidLine)

        self.bracketLines.append(self._scene.addPath(path, pen2))
        self.bracketLines.append(self._scene.addPath(path, pen))
        self.bracketLines.append(self._scene.addPath(dashedPath, pen2))
        self.bracketLines.append(self._scene.addPath(dashedPath, pen))

    def _findWidget(self, bracketSet: BracketSet):
        if bracketSet is None:
            return None
        for columnWidgets in self.bracketWidgets:
            for w in columnWidgets:
                if w.bracketSet is bracketSet:
                    return w
        return None

    def fitInView(self):
        rect = QRectF(self.bracketLayout.rect())
        if not rect.isNull():
            self.setSceneRect(rect)
            unity = self.transform().mapRect(QRectF(0, 0, 1, 1))
            self.scale(1 / unity.width(), 1 / unity.height())
            viewrect = self.viewport().rect()
            scenerect = self.transform().mapRect(rect)
            factor = min(viewrect.width() / scenerect.width(),
                         viewrect.height() / scenerect.height())
            self.scale(factor, factor)
            self._zoom = 0

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            factor = 1.25
            self._zoom += 1
        else:
            factor = 0.8
            self._zoom -= 1

        if self._zoom > 0:
            self.scale(factor, factor)
        else:
            self.fitInView()
            self._zoom = 0

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
