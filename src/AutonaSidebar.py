#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Todoist-style left navigation sidebar for the Autona main window.

Replaces the former tabified-dock + QMenuBar navigation. Top to bottom the
sidebar hosts:

  * an operations header — a "tournament" dropdown button, a sidebar collapse
    toggle, a game selector, and a 1v1 / Team / Crew format segment,
  * a vertical list of feature nav items (icon + title) that drive the
    QStackedWidget in the main window,
  * a footer "Help & Settings" dropdown button.

The bar itself is a flat dark surface (bg_secondary); only the selected nav
item carries a crimson-tinted highlight (brand color), mirroring Todoist —
the whole bar is intentionally NOT painted in the brand color.

The widget is deliberately "dumb": the main Window builds the QMenus, the
game-selector combo box, and wires every action, then hands them here via the
setter methods. That keeps the intricate signal wiring in one place (Window)
and this module focused on layout + selection state.
"""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal, QSize
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import ui_constants as UI

SIDEBAR_WIDTH = 196
NAV_ICON_SIZE = 16

_FORMATS = (("singles", "1v1"), ("team", "Team"), ("crew", "Crew"))


class AutonaSidebar(QWidget):
    """Left navigation sidebar. See module docstring for the layout."""

    navSelected = Signal(int)        # nav item clicked → stacked-widget index
    collapseRequested = Signal()     # collapse toggle clicked
    formatSelected = Signal(str)     # "singles" | "team" | "crew"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("autonaSidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setStyleSheet(self._build_qss())

        self._navButtons: list[QToolButton] = []
        self._navGroup = QButtonGroup(self)
        self._navGroup.setExclusive(True)
        self._fmtButtons: dict[str, QToolButton] = {}
        self._fmtGroup = QButtonGroup(self)
        self._fmtGroup.setExclusive(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._hline())
        root.addWidget(self._build_nav(), 1)
        root.addWidget(self._hline())
        root.addWidget(self._build_footer())

    # ── Public API (wired by the main Window) ──────────────────────

    def addNavItem(self, icon: QIcon, title: str) -> int:
        """Append a feature nav item (icon + title). Returns its index, which
        matches the page index callers should use in the QStackedWidget."""
        idx = len(self._navButtons)
        btn = QToolButton()
        btn.setObjectName("navItem")
        btn.setText(title)
        btn.setIcon(icon)
        btn.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._navGroup.addButton(btn, idx)
        self._navButtons.append(btn)
        self._navLayout.insertWidget(self._navLayout.count() - 1, btn)
        btn.clicked.connect(lambda _checked=False, i=idx: self.navSelected.emit(i))
        return idx

    def setCurrentIndex(self, idx: int) -> None:
        if 0 <= idx < len(self._navButtons):
            self._navButtons[idx].setChecked(True)

    def currentIndex(self) -> int:
        for i, btn in enumerate(self._navButtons):
            if btn.isChecked():
                return i
        return -1

    def setGameSelect(self, combo: QWidget, modded: QWidget | None = None) -> None:
        """Insert the game-selector combo (built by Window) under the top row,
        and the optional 'modded content' checkbox below the format segment."""
        self._headerLayout.insertWidget(1, combo)
        if modded is not None:
            self._headerLayout.addWidget(modded)

    def setFormat(self, fmt: str) -> None:
        btn = self._fmtButtons.get(fmt)
        if btn is not None:
            btn.setChecked(True)

    @property
    def tournamentButton(self) -> QToolButton:
        return self._tournamentButton

    @property
    def helpButton(self) -> QToolButton:
        return self._helpButton

    @property
    def collapseButton(self) -> QToolButton:
        return self._collapseButton

    # ── Construction helpers ───────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        self._headerLayout = hl = QVBoxLayout(header)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(4)

        topRow = QHBoxLayout()
        topRow.setContentsMargins(0, 0, 0, 0)
        topRow.setSpacing(6)

        self._tournamentButton = QToolButton()
        self._tournamentButton.setObjectName("sidebarTournamentBtn")
        self._tournamentButton.setIcon(QIcon(UI.icon_path("tournament.svg")))
        self._tournamentButton.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self._tournamentButton.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._tournamentButton.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tournamentButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tournamentButton.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        topRow.addWidget(self._tournamentButton, 1)

        self._collapseButton = QToolButton()
        self._collapseButton.setObjectName("sidebarCollapseBtn")
        self._collapseButton.setIcon(QIcon(UI.icon_path("menu.svg")))
        self._collapseButton.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self._collapseButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapseButton.clicked.connect(self.collapseRequested)
        topRow.addWidget(self._collapseButton, 0)

        hl.addLayout(topRow)
        hl.addWidget(self._build_format_segment())
        return header

    def _build_format_segment(self) -> QWidget:
        seg = QWidget()
        seg.setObjectName("fmtSegBox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(2)
        for key, label in _FORMATS:
            btn = QToolButton()
            btn.setObjectName("fmtSeg")
            btn.setText(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._fmtGroup.addButton(btn)
            self._fmtButtons[key] = btn
            btn.clicked.connect(
                lambda _checked=False, k=key: self.formatSelected.emit(k))
            sl.addWidget(btn, 1)
        return seg

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        self._navLayout = nl = QVBoxLayout(nav)
        nl.setContentsMargins(8, 3, 8, 3)
        nl.setSpacing(1)
        nl.addStretch(1)
        return nav

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.setSpacing(0)
        self._helpButton = QToolButton()
        self._helpButton.setObjectName("sidebarHelpBtn")
        self._helpButton.setIcon(QIcon(UI.icon_path("settings.svg")))
        self._helpButton.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self._helpButton.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._helpButton.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self._helpButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self._helpButton.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fl.addWidget(self._helpButton)
        return footer

    def _hline(self) -> QFrame:
        line = QFrame()
        line.setObjectName("sidebarDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _build_qss() -> str:
        p = UI.palette()
        # Selected nav: crimson-tinted fill + brand text. The brand color is
        # reserved for live/status signals elsewhere; here it doubles as the
        # selection accent (Todoist-style), which is the one approved use on
        # the sidebar. rgba percentages keep the tint readable on bg_secondary.
        return f"""
        #autonaSidebar {{ background-color: {p['bg_secondary']}; }}
        #sidebarDivider {{ background-color: {p['border_subtle']}; border: none; }}

        QToolButton#navItem {{
            background: transparent; border: none; text-align: left;
            padding: 4px 10px; border-radius: 6px;
            color: {p['text_secondary']};
            font-size: {UI.FONT_MD}pt;
        }}
        QToolButton#navItem:hover {{ background: rgba(255, 255, 255, 6%); }}
        QToolButton#navItem:checked {{
            background: rgba(220, 20, 60, 18%);
            color: {p['brand']};
            font-weight: 600;
        }}

        QToolButton#sidebarTournamentBtn, QToolButton#sidebarHelpBtn {{
            background: rgba(255, 255, 255, 6%); border: none; text-align: left;
            padding: 5px 9px; border-radius: 6px;
            color: {p['text_primary']};
            font-size: {UI.FONT_SM}pt;
        }}
        QToolButton#sidebarTournamentBtn:hover,
        QToolButton#sidebarHelpBtn:hover {{ background: rgba(255, 255, 255, 10%); }}
        QToolButton#sidebarTournamentBtn::menu-indicator,
        QToolButton#sidebarHelpBtn::menu-indicator {{
            subcontrol-position: right center; right: 6px;
        }}

        QToolButton#sidebarCollapseBtn {{
            background: transparent; border: none; padding: 5px;
            border-radius: 6px; color: {p['text_secondary']};
        }}
        QToolButton#sidebarCollapseBtn:hover {{ background: rgba(255, 255, 255, 6%); }}

        QWidget#fmtSegBox {{
            background: rgba(255, 255, 255, 5%); border-radius: 6px;
        }}
        QToolButton#fmtSeg {{
            background: transparent; border: none; padding: 3px 0;
            border-radius: 4px; color: {p['text_secondary']};
            font-size: {UI.FONT_SM}pt;
        }}
        QToolButton#fmtSeg:hover {{ background: rgba(255, 255, 255, 6%); }}
        QToolButton#fmtSeg:checked {{
            background: {p['brand']}; color: {p['on_accent']};
        }}
        """
