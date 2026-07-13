"""TOP 8 tab: edit the Top 8 double elimination bracket and get it into OBS.

Replaces the old free-form Bracket dock (AutonaBracketWidget /
AutonaBracketView). The bracket is now a fixed FGC-standard Top 8
(4 winners-side + 4 losers-side starters) whose topology lives in
AutonaTop8Model; this panel only edits players and match results.

* **Players** — 8 slot-fixed cards (name / sponsor / country / character).
* **Matches** — one score row per match; winners propagate automatically
  via AutonaTop8Model.Recompute on every edit.
* **Start.gg** — load a double-elimination phase group and extract its
  final 8 into the fixed topology.
* **Preview / Add to OBS** — embedded live preview of the
  ``layout/bracket_top8/`` overlay plus the browser-source URL, mirroring
  AutonaBoardWidget's workflow (URL instead of local file: the overlay
  needs the web server's Socket.IO feed).

State contract: everything under ``top8.*`` (see AutonaTop8Model). All
writes go through explicit StateManager.Set inside SaveBlock — no
objectName-derived keypaths.
"""

import traceback

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .StateManager import StateManager
from .AutonaLayoutPreview import AutonaLayoutPreview, ObsUrl
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaHotkeys import AutonaHotkeys
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Workers import Worker
from . import AutonaTop8Model as top8
from . import ui_constants as UI
from . import ui_layout as L

LAYOUT_NAME = "bracket_top8"

# Sidebar section -> match ids, in display order.
MATCH_SECTIONS = [
    ("Winners", ["wsf1", "wsf2", "wf"]),
    ("Losers", ["lr1a", "lr1b", "lqf1", "lqf2", "lsf", "lf"]),
    ("Grand Final", ["gf", "gf_reset"]),
]

# Compact row labels for the score table. The long names stay in
# top8.matches.{id}.name (the OBS overlay renders those).
SHORT_MATCH_LABELS = {
    "wsf1": "WSF1", "wsf2": "WSF2", "wf": "WF",
    "lr1a": "LR1A", "lr1b": "LR1B",
    "lqf1": "LQF1", "lqf2": "LQF2", "lsf": "LSF", "lf": "LF",
    "gf": "GF", "gf_reset": "RESET",
}

PLAYER_CARD_WIDTH = 240


class _NoIconProxyModel(QSortFilterProxyModel):
    """Strip icons (DecorationRole) from the source model.

    Duplicated from AutonaResultSettingsWidget on purpose: importing that
    module here would pull the result renderer chain into Autona.py's
    import order (singleton init-order hazard)."""

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole:
            return None
        return super().data(index, role)


class AutonaTop8Widget(QDockWidget):
    instance: "AutonaTop8Widget" = None

    def __init__(self, *args):
        super().__init__(*args)
        # windowTitle is the visible tab label (objectName is set in Autona.py).
        self.setWindowTitle(QApplication.translate("app", "Top 8"))

        # Guards: _updating suppresses input handlers while the UI is being
        # repopulated from state; _updatingPhaseGroup blocks re-entrant
        # tournament_phasegroup_updated handling (see the old
        # AutonaBracketWidget crash notes).
        self._updating = False
        self._updatingPhaseGroup = False
        self._previewLoaded = False
        self._statusToken = None
        self._phases = []
        self._loadQueuedOnPhases = False

        with StateManager.SaveBlock():
            # The legacy bracket feature is gone; LoadState() restores its
            # keys from out/program_state.json, so purge them once here.
            StateManager.Unset("bracket")
            state = StateManager.Get("top8")
            if not isinstance(state, dict) or not state.get("matches"):
                StateManager.Set("top8", top8.DefaultState())
            else:
                # Normalise a restored state: slots are always derived.
                StateManager.Set(
                    "top8.matches",
                    top8.Recompute(state.get("matches") or {}))
                players = state.get("players") or {}
                for slot in top8.PLAYER_SLOTS:
                    if slot not in players:
                        StateManager.Set(
                            f"top8.players.{slot}", top8.DefaultPlayer())

        # Two panes, each with its own scroll: editing (Start.gg / players /
        # scores / OBS) on the left, the live preview on the right.
        leftContent = QWidget()
        leftCol = QVBoxLayout(leftContent)
        leftCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        leftCol.addWidget(self._buildStartggGroup())
        leftCol.addWidget(self._buildPlayersGroup())
        leftCol.addWidget(self._buildMatchesGroup())
        leftCol.addWidget(self._buildObsGroup())
        leftCol.addStretch(1)

        leftScroll = QScrollArea()
        leftScroll.setWidgetResizable(True)
        leftScroll.setWidget(leftContent)
        leftScroll.setFixedWidth(300)
        leftScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        rightContent = QWidget()
        rightCol = QVBoxLayout(rightContent)
        rightCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        rightCol.setSpacing(UI.PANEL_MARGIN)
        rightCol.addWidget(self._buildPreviewGroup(), 1)

        rightScroll = QScrollArea()
        rightScroll.setWidgetResizable(True)
        rightScroll.setWidget(rightContent)

        panes = QWidget()
        panesLayout = QHBoxLayout(panes)
        panesLayout.setContentsMargins(0, 0, 0, 0)
        panesLayout.setSpacing(0)
        panesLayout.addWidget(leftScroll)
        panesLayout.addWidget(rightScroll, 1)
        self.setWidget(panes)

        self._loadFromState()

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._onGameChanged)
        AutonaCountryHelper.signals.countriesUpdated.connect(
            self._rebindCountryModels)

        signals = AutonaTournamentDataProvider.instance.signals
        signals.tournament_phases_updated.connect(self.UpdatePhases)
        signals.tournament_phasegroup_updated.connect(self.UpdatePhaseGroup)
        signals.tournament_changed.connect(self._onTournamentChanged)

        AutonaHotkeys.signals.refresh_phase_group.connect(
            self.RefreshPhaseGroup)

        AutonaTop8Widget.instance = self

    # ── Players ──────────────────────────────────────────────────────
    def _buildPlayersGroup(self):
        group = L.make_card("選手（スロット固定）")

        note = QLabel(
            "スロット 1-4 は Winners、5-8 は Losers スタートです。"
            "対戦カードを変えるには選手を入れ替えてください。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        _pc = UI.palette()
        cardStyle = (
            f"QFrame#top8PlayerCard {{"
            f" background-color: {_pc['bg_tertiary']};"
            f" border: 1px solid {_pc['border_subtle']};"
            f" border-radius: {UI.RADIUS_MD}px;"
            f" }}")

        cards = QVBoxLayout()
        cards.setSpacing(6)
        group.layout().addLayout(cards)

        # slot(str) -> {"name", "sponsor", "country", "char", "color"}
        self.playerInputs = {}

        for slot in top8.PLAYER_SLOTS:
            card = QFrame()
            card.setObjectName("top8PlayerCard")
            card.setStyleSheet(cardStyle)
            card.setFixedWidth(PLAYER_CARD_WIDTH)
            fields = QGridLayout(card)
            fields.setContentsMargins(8, 8, 8, 8)
            fields.setVerticalSpacing(2)
            fields.setHorizontalSpacing(6)

            header = QLabel(f"{slot} · {top8.SLOT_POSITIONS[slot]}")
            header.setStyleSheet(UI.secondary_label_style())

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            sponsor_edit = QLineEdit()
            sponsor_edit.setPlaceholderText(
                QApplication.translate("app", "Sponsor"))
            sponsor_edit.setFixedWidth(80)

            country_combo = self._makeCountryCombo()
            char_combo = self._makeCharCombo()
            color_combo = self._makeColorCombo()

            fields.addWidget(header,        0, 0, 1, 2)
            # Sponsor tag left of the player name ("[Sponsor] PlayerName"),
            # matching the scoreboard / result settings display order. Nested
            # HBox spanning both columns so the fixed-width sponsor doesn't
            # inherit the stretching char-combo column below.
            nameRow = QHBoxLayout()
            nameRow.setSpacing(6)
            nameRow.addWidget(sponsor_edit)
            nameRow.addWidget(name_edit, 1)
            fields.addLayout(nameRow,       1, 0, 1, 2)
            fields.addWidget(country_combo, 2, 0, 1, 2)
            fields.addWidget(char_combo,    3, 0)
            fields.addWidget(color_combo,   3, 1)
            fields.setColumnStretch(0, 1)

            cards.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)

            name_edit.editingFinished.connect(self._syncPlayersToState)
            sponsor_edit.editingFinished.connect(self._syncPlayersToState)
            country_combo.currentIndexChanged.connect(
                self._onPlayerComboChanged)
            char_combo.currentIndexChanged.connect(
                lambda _, cc=char_combo, vc=color_combo:
                self._onCharComboChanged(cc, vc))
            color_combo.currentIndexChanged.connect(
                self._onPlayerComboChanged)

            self.playerInputs[slot] = {
                "name": name_edit,
                "sponsor": sponsor_edit,
                "country": country_combo,
                "char": char_combo,
                "color": color_combo,
            }

        return group

    def _makeCountryCombo(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        # countryModel is loaded synchronously in Autona.py before panels are
        # built, but its loader swallows failures (model stays None) —
        # countriesUpdated re-binds once data arrives.
        if AutonaCountryHelper.countryModel is not None:
            combo.setModel(AutonaCountryHelper.countryModel)
        combo.setIconSize(QSize(16, 16))
        combo.setFixedHeight(22)
        combo.setCurrentIndex(0)
        combo.lineEdit().setPlaceholderText(
            QApplication.translate("app", "Country"))
        return combo

    def _rebindCountryModels(self):
        for w in self.playerInputs.values():
            combo = w["country"]
            combo.blockSignals(True)
            combo.setModel(AutonaCountryHelper.countryModel)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _makeCharCombo(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(100)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        proxy = _NoIconProxyModel(combo)
        proxy.setSourceModel(AutonaGameAssetManager.instance.characterModel)
        combo.setModel(proxy)
        combo.setIconSize(QSize(0, 0))
        combo.setFixedHeight(22)
        combo.setCurrentIndex(-1)
        combo.lineEdit().setPlaceholderText(
            QApplication.translate("app", "Main character"))
        return combo

    def _makeColorCombo(self):
        combo = QComboBox()
        combo.setIconSize(QSize(16, 16))
        combo.setFixedHeight(22)
        combo.setMinimumWidth(48)
        combo.setMaximumWidth(80)
        view = QListView()
        view.setIconSize(QSize(24, 24))
        combo.setView(view)
        return combo

    def _onGameChanged(self):
        """Re-bind character models when the game changes (the model object
        is replaced). Mirrors AutonaResultSettingsWidget._onGameChanged."""
        for w in self.playerInputs.values():
            char_combo = w["char"]
            char_combo.blockSignals(True)
            proxy = _NoIconProxyModel(char_combo)
            proxy.setSourceModel(
                AutonaGameAssetManager.instance.characterModel)
            char_combo.setModel(proxy)
            char_combo.setIconSize(QSize(0, 0))
            char_combo.setCurrentIndex(-1)
            char_combo.blockSignals(False)
            w["color"].setModel(QStandardItemModel())

    def _onCharComboChanged(self, char_combo, color_combo):
        char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        if char_data and char_data.get("en_name"):
            skin_model = AutonaGameAssetManager.instance.skinModels.get(
                char_data.get("en_name"))
            if skin_model:
                color_combo.setModel(skin_model)
                color_combo.setCurrentIndex(0)
            else:
                color_combo.setModel(QStandardItemModel())
        else:
            color_combo.setModel(QStandardItemModel())
        self._syncPlayersToState()

    def _onPlayerComboChanged(self, *_args):
        self._syncPlayersToState()

    def _resolveCharData(self, char_combo, color_combo):
        skin_data = color_combo.currentData(Qt.ItemDataRole.UserRole)
        if skin_data and skin_data.get("assets"):
            return skin_data
        char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        return char_data or {}

    def _syncPlayersToState(self):
        if self._updating:
            return
        players = {}
        for slot, w in self.playerInputs.items():
            country = w["country"].currentData(Qt.ItemDataRole.UserRole) or {}
            char = self._resolveCharData(w["char"], w["color"])
            players[slot] = {
                "name": w["name"].text().strip(),
                "sponsor": w["sponsor"].text().strip(),
                "country": country,
                "characters": {"1": char} if char else {},
            }
        with StateManager.SaveBlock():
            StateManager.Set("top8.players", players)
        self._refreshMatchRows(
            StateManager.Get("top8.matches") or {}, update_scores=False)

    # ── Matches ──────────────────────────────────────────────────────
    def _buildMatchesGroup(self):
        group = L.make_card("試合スコア")

        labelRow = QHBoxLayout()
        labelRow.addWidget(QLabel("表示ラベル"))
        self.phaseLabelEdit = QLineEdit()
        self.phaseLabelEdit.setPlaceholderText(top8.DEFAULT_PHASE_LABEL)
        self.phaseLabelEdit.editingFinished.connect(self._onPhaseLabelChanged)
        labelRow.addWidget(self.phaseLabelEdit, 1)
        group.layout().addLayout(labelRow)

        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        grid.setHorizontalSpacing(6)
        group.layout().addLayout(grid)

        # match_id -> {"round", "p1", "s1", "s2", "p2"}
        self.matchRows = {}
        row = 0
        for section, match_ids in MATCH_SECTIONS:
            sectionLabel = QLabel(section)
            L.apply_section_title(sectionLabel)
            grid.addWidget(sectionLabel, row, 0, 1, 5)
            row += 1
            for match_id in match_ids:
                roundLabel = QLabel(SHORT_MATCH_LABELS[match_id])
                roundLabel.setToolTip(top8.DEFAULT_ROUND_NAMES[match_id])
                roundLabel.setStyleSheet(UI.secondary_label_style())
                roundLabel.setFixedWidth(48)

                # Ignored horizontal policy: long names clip instead of
                # widening the grid past the fixed 300px pane.
                p1 = QLabel("TBD")
                p1.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                p2 = QLabel("TBD")
                for p in (p1, p2):
                    p.setMinimumWidth(40)
                    p.setSizePolicy(
                        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

                s1 = QSpinBox()
                s2 = QSpinBox()
                for s in (s1, s2):
                    s.setRange(0, 99)
                    s.setFixedWidth(40)
                    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    # The 40px box has no room for the stepper arrows —
                    # they were drawn over the digits. Type / arrow keys /
                    # wheel still step the value.
                    s.setButtonSymbols(
                        QAbstractSpinBox.ButtonSymbols.NoButtons)

                grid.addWidget(roundLabel, row, 0)
                grid.addWidget(p1, row, 1)
                grid.addWidget(s1, row, 2)
                grid.addWidget(s2, row, 3)
                grid.addWidget(p2, row, 4)
                grid.setColumnStretch(1, 1)
                grid.setColumnStretch(4, 1)
                row += 1

                self.matchRows[match_id] = {
                    "round": roundLabel,
                    "p1": p1, "s1": s1, "s2": s2, "p2": p2,
                }

        btRow = QHBoxLayout()
        btRow.addStretch(1)
        self.btApplyScores = QPushButton(
            QApplication.translate("app", "Apply"))
        L.tag_button(self.btApplyScores, L.BUTTON_PRIMARY)
        self.btApplyScores.clicked.connect(self._onApplyScores)
        btRow.addWidget(self.btApplyScores)
        self.btResetAll = QPushButton("全リセット")
        L.tag_button(self.btResetAll, L.BUTTON_DANGER)
        self.btResetAll.clicked.connect(self._onResetAll)
        btRow.addWidget(self.btResetAll)
        group.layout().addLayout(btRow)

        return group

    def _onPhaseLabelChanged(self):
        if self._updating:
            return
        text = self.phaseLabelEdit.text().strip() or top8.DEFAULT_PHASE_LABEL
        StateManager.Set("top8.phaseLabel", text)

    def _onApplyScores(self):
        """反映 button: write the staged spin values to state. A match is
        considered finished when its applied score is a non-tie — there is
        no separate confirm checkbox."""
        # StateManager.Get returns a live reference — never mutate it in
        # place, or DeepDiff sees no change and the delta is never broadcast.
        current = StateManager.Get("top8.matches") or {}
        matches = {}
        for match_id, row in self.matchRows.items():
            m = dict(current.get(match_id) or top8.DefaultMatch(match_id))
            score = [row["s1"].value(), row["s2"].value()]
            m["score"] = score
            m["finished"] = score[0] != score[1]
            matches[match_id] = m
        matches = top8.Recompute(matches)
        with StateManager.SaveBlock():
            StateManager.Set("top8.matches", matches)
        self._refreshMatchRows(matches)

    def _onResetAll(self):
        # Button-click context, so a modal confirm is safe here (the
        # no-modal-in-signal rule targets QSpinBox.valueChanged etc.).
        answer = QMessageBox.question(
            self,
            QApplication.translate("app", "Top 8"),
            "全試合のスコアと確定状態をリセットします。よろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        matches = top8.Recompute(
            {mid: top8.DefaultMatch(mid) for mid in top8.MATCH_IDS})
        with StateManager.SaveBlock():
            StateManager.Set("top8.matches", matches)
        self._refreshMatchRows(matches)

    def _playerLabel(self, slot):
        if not slot:
            return "TBD"
        player = StateManager.Get(f"top8.players.{slot}") or {}
        name = (player.get("name") or "").strip()
        return name if name else f"({slot})"

    def _refreshMatchRows(self, matches, update_scores=True):
        """Repopulate the score rows from a matches dict. Recompute may
        have reset downstream rows, so 反映/リセット mirror the state back.
        update_scores=False refreshes only the name labels so player edits
        don't clobber staged (not yet applied) spin values."""
        self._updating = True
        try:
            for match_id, row in self.matchRows.items():
                m = matches.get(match_id) or top8.DefaultMatch(match_id)
                slots = m.get("slots", [0, 0])
                row["p1"].setText(self._playerLabel(slots[0]))
                row["p2"].setText(self._playerLabel(slots[1]))
                if update_scores:
                    score = m.get("score", [0, 0])
                    row["s1"].setValue(score[0])
                    row["s2"].setValue(score[1])

                enabled = True
                if match_id == "gf_reset":
                    enabled = bool(m.get("active"))
                for key in ("p1", "s1", "s2", "p2", "round"):
                    row[key].setEnabled(enabled)
        finally:
            self._updating = False

    # ── Start.gg ─────────────────────────────────────────────────────
    # The target bracket is fixed: the tournament's FINAL double
    # elimination phase (last DE phase in Start.gg's phase order). No
    # phase picker — the resolved target is shown read-only.
    def _buildStartggGroup(self):
        group = L.make_card("Start.gg から読み込み")

        note = QLabel(
            "大会のダブルエリミネーション最終フェーズを自動で対象にし、"
            "最終 8 名と各試合のスコアを取り込みます（大会は EVENT タブで設定）。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(UI.SPACE_MD)
        self.targetLabel = QLabel("対象: —")
        self.targetLabel.setWordWrap(True)
        row.addWidget(self.targetLabel, 1)

        self.btLoadTop8 = QPushButton(
            QApplication.translate("app", "Load"))
        L.tag_button(self.btLoadTop8, L.BUTTON_PRIMARY)
        self.btLoadTop8.clicked.connect(self.RefreshPhaseGroup)
        row.addWidget(self.btLoadTop8)
        group.layout().addLayout(row)

        return group

    def _resolveTargetPhaseGroup(self):
        """Return (phase, group) for the final double elimination phase, or
        (None, None). Walks phases from the end; within the last phase that
        has a DE group, takes its first DE group (finals phases normally
        have exactly one)."""
        for phase in reversed(self._phases):
            de_groups = [g for g in (phase.get("groups") or [])
                         if g.get("bracketType") == "DOUBLE_ELIMINATION"]
            if de_groups:
                return phase, de_groups[0]
        return None, None

    def _refreshTargetLabel(self):
        phase, group = self._resolveTargetPhaseGroup()
        if phase is None:
            if self._phases:
                self.targetLabel.setText(
                    "対象: —（ダブルエリミネーションのフェーズがありません）")
            else:
                self.targetLabel.setText("対象: —（大会未読込）")
            return
        name = phase.get("name") or ""
        group_name = group.get("name") or ""
        if group_name and group_name != name:
            name = f"{name} / {group_name}"
        self.targetLabel.setText(f"対象: {name}")

    def UpdatePhases(self, phases):
        self._phases = list(phases or [])
        self._refreshTargetLabel()
        if self._loadQueuedOnPhases and self._phases:
            self._loadQueuedOnPhases = False
            QTimer.singleShot(0, self.RefreshPhaseGroup)

    def _onTournamentChanged(self):
        self._phases = []
        self._loadQueuedOnPhases = False
        self._refreshTargetLabel()

    def RefreshPhaseGroup(self):
        """Fetch (or re-fetch) the final double elimination phase group.
        Also the target of the Ctrl+Shift+P hotkey and the /bracket:refresh
        API. When phases haven't arrived yet, queues itself and triggers a
        phases fetch first."""
        if AutonaTournamentDataProvider.instance.provider is None:
            logger.info("Top8: no tournament set, skipping refresh")
            return
        if not self._phases:
            logger.info("Top8: phases not cached yet, fetching first")
            self._loadQueuedOnPhases = True
            AutonaTournamentDataProvider.instance.GetTournamentPhases()
            return
        _phase, group = self._resolveTargetPhaseGroup()
        if group is None or group.get("id") is None:
            logger.info("Top8: no double elimination phase found, skipping")
            return
        AutonaTournamentDataProvider.instance.GetTournamentPhaseGroup(
            group.get("id"))

    def UpdatePhaseGroup(self, phaseGroupData):
        """tournament_phasegroup_updated handler: build the Bracket off the
        GUI thread, then extract its Top 8 into top8.* state."""
        if self._updatingPhaseGroup:
            logger.warning("Top8: skipping re-entrant UpdatePhaseGroup call")
            return
        self._updatingPhaseGroup = True

        from .Helpers.AutonaStatusManager import AutonaStatusManager
        self._statusToken = AutonaStatusManager.push("TOP 8 を構築中...")

        worker = Worker(self._buildTop8Async, phaseGroupData=phaseGroupData)
        # Bound-method slots so Qt delivers the results on the GUI thread
        # (lambdas would run on the worker thread and touch widgets there).
        worker.signals.result.connect(self._onTop8Built)
        worker.signals.error.connect(self._onTop8BuildError)
        AutonaTournamentDataProvider.instance.threadPool.start(worker)

    def _buildTop8Async(self, phaseGroupData,
                        progress_callback=None, cancel_event=None):
        # Pure data work — no Qt widgets touched.
        from .AutonaBracket import Bracket
        bracket = Bracket(phaseGroupData)
        return top8.FromBracket(bracket, phaseGroupData.get("entrants") or [])

    def _onTop8Built(self, extracted):
        self._finalizeTop8Update(extracted, self._statusToken)

    def _onTop8BuildError(self, _err):
        self._finalizeTop8Update(None, self._statusToken)

    def _finalizeTop8Update(self, extracted, status_token):
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        try:
            if extracted is None:
                logger.warning(
                    "Top8: could not extract a Top 8 from the phase group")
                return
            players, matches = extracted
            resolved = {
                slot: self._resolveImportedPlayer(p)
                for slot, p in players.items()
            }
            with StateManager.SaveBlock():
                StateManager.Set("top8.players", resolved)
                StateManager.Set("top8.matches", top8.Recompute(matches))
            self._loadFromState()
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            self._updatingPhaseGroup = False
            AutonaStatusManager.pop(status_token)

    def _resolveImportedPlayer(self, imported):
        """Fill the country/character fields of a FromBracket player from
        the entrant's country_code / mains via the app's asset helpers."""
        player = top8.DefaultPlayer()
        player["name"] = imported.get("name", "")
        player["sponsor"] = imported.get("sponsor", "")

        code = imported.get("country_code")
        if code:
            country = AutonaCountryHelper.GetBasicCountryInfo(code)
            if country:
                player["country"] = country

        mains = imported.get("mains") or {}
        game = AutonaGameAssetManager.instance.selectedGame
        if game and isinstance(mains, dict):
            game_mains = mains.get(game.get("codename", ""), [])
            if game_mains and game_mains[0]:
                char = self._charDataFromCodename(game_mains[0][0])
                if char:
                    player["characters"] = {"1": char}
        return player

    @staticmethod
    def _charDataFromCodename(char_codename):
        chars = AutonaGameAssetManager.instance.characters
        char_entry = chars.get(char_codename)
        if not char_entry:
            for val in chars.values():
                if val.get("codename") == char_codename:
                    char_entry = val
                    break
        return char_entry or {}

    # ── State -> UI ──────────────────────────────────────────────────
    def _loadFromState(self):
        """Populate every input from top8.* (startup restore and Start.gg
        import both land here)."""
        self._updating = True
        try:
            self.phaseLabelEdit.setText(
                StateManager.Get("top8.phaseLabel")
                or top8.DEFAULT_PHASE_LABEL)

            players = StateManager.Get("top8.players") or {}
            for slot, w in self.playerInputs.items():
                player = players.get(slot) or {}
                w["name"].setText(player.get("name", ""))
                w["sponsor"].setText(player.get("sponsor", ""))
                self._selectCountry(w["country"],
                                    (player.get("country") or {}).get("code"))
                char = (player.get("characters") or {}).get("1") or {}
                self._selectCharacter(w["char"], w["color"], char)
        finally:
            self._updating = False
        self._refreshMatchRows(StateManager.Get("top8.matches") or {})

    @staticmethod
    def _selectCountry(combo, code):
        model = AutonaCountryHelper.countryModel
        if model is None:
            return
        combo.blockSignals(True)
        index = 0
        if code:
            for i in range(model.rowCount()):
                data = model.item(i).data(Qt.ItemDataRole.UserRole)
                if data and data.get("code") == code:
                    index = i
                    break
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _selectCharacter(self, char_combo, color_combo, char):
        """Select the stored character (matched by en_name/codename) and load
        its skin model. Skin selection defaults to the first entry."""
        char_combo.blockSignals(True)
        color_combo.blockSignals(True)
        try:
            target = None
            if char:
                target = char.get("en_name") or char.get("codename")
            index = -1
            if target:
                model = char_combo.model()
                for row in range(model.rowCount()):
                    data = model.data(
                        model.index(row, 0), Qt.ItemDataRole.UserRole)
                    if data and (data.get("en_name") == target
                                 or data.get("codename") == target):
                        index = row
                        break
            char_combo.setCurrentIndex(index)
            if index >= 0:
                data = char_combo.currentData(Qt.ItemDataRole.UserRole)
                skin_model = AutonaGameAssetManager.instance.skinModels.get(
                    (data or {}).get("en_name"))
                if skin_model:
                    color_combo.setModel(skin_model)
                    color_combo.setCurrentIndex(0)
                else:
                    color_combo.setModel(QStandardItemModel())
            else:
                color_combo.setModel(QStandardItemModel())
        finally:
            char_combo.blockSignals(False)
            color_combo.blockSignals(False)

    # ── Preview ──────────────────────────────────────────────────────
    def _buildPreviewGroup(self):
        group = L.make_card("プレビュー")
        self.preview = AutonaLayoutPreview(LAYOUT_NAME)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group.layout().addWidget(self.preview)
        btRow = QHBoxLayout()
        btRow.addStretch(1)
        btReload = QPushButton("プレビューを再読み込み")
        btReload.clicked.connect(self._reloadPreview)
        btRow.addWidget(btReload)
        group.layout().addLayout(btRow)
        return group

    def _reloadPreview(self):
        self._previewLoaded = True
        self.preview.load()

    def showEvent(self, e):
        super().showEvent(e)
        # Lazy-load on first reveal: by then the web server is up, so the
        # preview's HTTP fetch + Socket.IO connect succeed.
        if not self._previewLoaded:
            self._previewLoaded = True
            self.preview.load()

    # ── Add to OBS ───────────────────────────────────────────────────
    def _buildObsGroup(self):
        group = L.make_card("OBS に追加する方法")

        steps = [
            "OBS で「ソース」→「ブラウザ」を追加します。",
            "「URL」に下のアドレスを指定します（「コピー」を使用）。",
            "サイズを 幅 1920 / 高さ 1080 に設定します。",
        ]
        for i, step in enumerate(steps, start=1):
            label = QLabel(f"{i}. " + step)
            label.setWordWrap(True)
            group.layout().addWidget(label)

        urlRow = QHBoxLayout()
        self.obsUrlField = QLineEdit()
        self.obsUrlField.setReadOnly(True)
        self.obsUrlField.setText(self._obsUrl())
        urlRow.addWidget(self.obsUrlField, 1)
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsUrlField.text()))
        urlRow.addWidget(btCopy)
        group.layout().addLayout(urlRow)

        note = QLabel(
            "Autona を起動したままにすると、スコアボードと同様に選手やスコアの"
            "編集が OBS 上のブラケットへリアルタイムに反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group

    @staticmethod
    def _obsUrl():
        return ObsUrl(LAYOUT_NAME)
