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

from pathlib import Path

from qtpy.QtCore import Qt, Signal, QSize
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import ui_constants as UI

SIDEBAR_WIDTH = 196
NAV_ICON_SIZE = 16
# Every interactive row (nav item, tournament/help button, game combo, format
# segment) shares this height so the sidebar reads as one tight, uniform stack.
# Matches the game combo's fixed height (UI._WIDGET_H) set in Autona.py.
ROW_H = 26
# Left inset shared by a nav item's icon and a category heading's text, both
# measured from the nav band's content edge. The heading sits flush with the
# item's left edge (the selected row's highlight) and the icon is inset by
# this much inside it.
NAV_ITEM_PAD_X = 8

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

    def addNavCategory(self, title: str) -> None:
        """Append a category heading above the nav items that follow it.

        A static label, not a control: the groups never collapse, so the
        heading only has to name the run of items beneath it (Discord-style).
        It takes no nav index — indices belong to nav items alone, so they
        keep matching QStackedWidget page indices."""
        label = QLabel(title)
        label.setObjectName("navCategory")
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._navLayout.insertWidget(self._navLayout.count() - 1, label)

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

    def setGameSelect(self, gameButton: QWidget, modded: QWidget | None = None) -> None:
        """Insert the game-selector pulldown (a QToolButton built by Window)
        between the tournament pulldown and the format segment, and the optional
        'modded content' checkbox below the format segment.

        Header band order is: [0] hamburger row, [1] tournament button,
        [2] game button (inserted here), [3] format segment."""
        self._headerLayout.insertWidget(2, gameButton)
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
        # All three bands share this objectName so a single #sidebarBand rule
        # paints them the sidebar's lifted colour. Explicit solid fill (not
        # transparency) is required: qdarktheme's app-wide `QWidget{background}`
        # paints these plain containers in the page-content colour, and a
        # `background:transparent` override renders inconsistently across Qt
        # platforms — an ID-selector solid fill matches how #fmtSegBox already
        # paints reliably.
        header.setObjectName("sidebarBand")
        self._headerLayout = hl = QVBoxLayout(header)
        hl.setContentsMargins(8, 10, 8, 10)
        hl.setSpacing(4)

        # Row 1 — collapse (hamburger) alone, right-aligned in its own band.
        topRow = QHBoxLayout()
        topRow.setContentsMargins(0, 0, 0, 0)
        topRow.setSpacing(0)
        self._collapseButton = QToolButton()
        self._collapseButton.setObjectName("sidebarCollapseBtn")
        self._collapseButton.setIcon(QIcon(UI.icon_path("menu.svg")))
        self._collapseButton.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self._collapseButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapseButton.clicked.connect(self.collapseRequested)
        topRow.addStretch(1)
        topRow.addWidget(self._collapseButton, 0)
        hl.addLayout(topRow)

        # Row 2 — tournament pulldown, full width (lines up with the game combo).
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
        hl.addWidget(self._tournamentButton)

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
        nav.setObjectName("sidebarBand")
        self._navLayout = nl = QVBoxLayout(nav)
        nl.setContentsMargins(8, 10, 8, 10)
        nl.setSpacing(1)
        nl.addStretch(1)
        return nav

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("sidebarBand")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(8, 10, 8, 10)
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
        # Shared thin chevron for all three pulldowns (tournament / game combo /
        # help). QSS resolves relative url() unreliably on Windows, so feed it an
        # absolute POSIX path. Forward slashes are required regardless of OS.
        chevron = Path(UI.icon_path("chevron_down.svg")).resolve().as_posix()
        # Selected nav: crimson-tinted fill + brand text. The brand color is
        # reserved for live/status signals elsewhere; here it doubles as the
        # selection accent (Todoist-style), which is the one approved use on
        # the sidebar. rgba percentages keep the tint readable on bg_secondary.
        return f"""
        /* One step up the cool-gray ramp from the page content (bg_primary)
           so the sidebar reads as its own, subtly-lifted zone. */
        #autonaSidebar {{ background-color: {p['bg_secondary']}; }}
        /* The header/nav/footer bands tile the whole sidebar height. qdarktheme's
           app-wide `QWidget {{ background: bg_primary }}` would otherwise paint
           them in the page-content color and bury the lift. Paint them the
           sidebar color explicitly (ID selector beats the type-selector rule) —
           a solid fill rather than transparency, which composites reliably on
           every Qt platform. */
        QWidget#sidebarBand {{ background-color: {p['bg_secondary']}; }}
        #sidebarDivider {{ background-color: {p['border_subtle']}; border: none; }}

        /* Category heading: quieter and smaller than a nav item so the run of
           items below it reads as the label and they read as the content.
           Zero left padding lines its text up with the nav items' left edge —
           i.e. with the selected row's highlight — leaving the items' icons
           inset NAV_ITEM_PAD_X inside it. (No uppercase / letter-spacing: Qt
           QSS supports neither, and ui_constants deliberately avoids that
           treatment on card titles.) The top margin is what separates one
           group from the previous group's last item — the nav layout's own
           spacing is only 1px. */
        QLabel#navCategory {{
            color: {p['text_muted']};
            font-size: {UI.FONT_XS}pt;
            font-weight: 700;
            padding-left: 0px;
            padding-right: {NAV_ITEM_PAD_X}px;
            margin-top: 10px;
            margin-bottom: 3px;
            background: transparent;
        }}

        QToolButton#navItem {{
            background: transparent; border: none; text-align: left;
            padding: 0 {NAV_ITEM_PAD_X}px; min-height: {ROW_H}px;
            border-radius: 6px;
            color: {p['text_secondary']};
            font-size: {UI.FONT_SM}pt;
        }}
        QToolButton#navItem:hover {{ background: rgba(255, 255, 255, 6%); }}
        QToolButton#navItem:checked {{
            background: rgba(220, 20, 60, 18%);
            color: {p['brand']};
            font-weight: 600;
        }}

        /* The three sidebar pulldowns share ONE implementation: QToolButton +
           QMenu (InstantPopup). tournament / game / help are styled identically
           so they are visually and behaviorally indistinguishable. */
        QToolButton#sidebarTournamentBtn,
        QToolButton#sidebarGameBtn,
        QToolButton#sidebarHelpBtn {{
            background: rgba(255, 255, 255, 6%); text-align: left;
            /* Transparent instead of none so the game button's attention
               outline (below) can light up without shifting the layout. */
            border: 1px solid transparent;
            padding: 0 24px 0 9px; min-height: {ROW_H}px; border-radius: 6px;
            color: {p['text_primary']};
            font-size: {UI.FONT_SM}pt;
        }}
        /* Gold outline while the tournament / game pick is still missing —
           the required setup steps. Same gold as the crew "currently
           playing" accent (ACCENT_GOLD in AutonaCrewBattleWidget). */
        QToolButton#sidebarTournamentBtn[attention="true"],
        QToolButton#sidebarGameBtn[attention="true"] {{
            border: 1px solid #f0a500;
        }}
        QToolButton#sidebarTournamentBtn:hover,
        QToolButton#sidebarGameBtn:hover,
        QToolButton#sidebarHelpBtn:hover {{ background: rgba(255, 255, 255, 10%); }}
        QToolButton#sidebarTournamentBtn::menu-indicator,
        QToolButton#sidebarGameBtn::menu-indicator,
        QToolButton#sidebarHelpBtn::menu-indicator {{
            image: url({chevron});
            width: 12px; height: 12px;
            subcontrol-origin: padding;
            subcontrol-position: center right;
            /* qdarktheme ships `QToolButton::menu-indicator {{top:6px;left:3px}}`
               app-wide; our higher-specificity rule must zero those out or they
               cascade in and push the chevron down / cancel the right offset. */
            top: 0px; left: 0px; right: 9px;
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
            background: transparent; border: none; padding: 0;
            min-height: {ROW_H - 4}px; border-radius: 4px;
            color: {p['text_secondary']};
            font-size: {UI.FONT_SM}pt;
        }}
        QToolButton#fmtSeg:hover {{ background: rgba(255, 255, 255, 6%); }}
        QToolButton#fmtSeg:checked {{
            background: {p['brand']}; color: {p['on_accent']};
        }}
        """
