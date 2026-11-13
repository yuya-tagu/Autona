import React from "react";
import ShellCheckPage from "./ShellCheckPage";
import CommentaryPage from "../CommentaryPage";
import StreamPage from "../StreamPage";
import EventPage from "../EventPage";
import Top8Page from "../Top8Page";
import StatsPage from "../StatsPage";
import StatsPreviewPage from "../StatsPreviewPage";
import PlayerBrowserPage from "../PlayerBrowserPage";
import ThumbnailPage from "../ThumbnailPage";
import ResultPage from "../ResultPage";
import BoardPage from "../BoardPage";
import ScorePage from "../ScorePage";
import SettingsPage from "../SettingsPage";

// The React panels, by the name in their URL (/web/<panel>). Must name the
// same panels as WEB_PANELS in src/core/WebAccess.py, which decides what the
// server serves (tests/test_web_access.py compares the two). "shell-check"
// is the empty page that checks the shell itself (ADR-005 S4a);
// "commentary" the caster line-up (S4b); "stream" 配信設定 (S4e); "event"
// 大会情報 (S4g); "top8" Top 8 表示設定 (S4i); "stats" 統計情報 and the
// tournament's match list (S4k); "statsPreview" 統計情報表示設定 (S4l);
// "playerBrowser" 選手リスト (S4n); "thumbnail" サムネイル (S4p); "result"
// 大会リザルト (S4r); "board" スコアボード設定 (S4s); "scoreboard" スコアボード入力
// (S4aa); "settings" 設定 (S4af: the Help & Settings menu's, not a sidebar
// item).
export const PANELS: Record<string, React.ComponentType> = {
    "shell-check": ShellCheckPage,
    "commentary": CommentaryPage,
    "stream": StreamPage,
    "event": EventPage,
    "top8": Top8Page,
    "stats": StatsPage,
    "statsPreview": StatsPreviewPage,
    "playerBrowser": PlayerBrowserPage,
    "thumbnail": ThumbnailPage,
    "result": ResultPage,
    "board": BoardPage,
    "scoreboard": ScorePage,
    "settings": SettingsPage,
};

export function panelPage(panel: string): React.ComponentType | undefined {
    // Own names only: "constructor" is not a panel.
    return Object.prototype.hasOwnProperty.call(PANELS, panel)
        ? PANELS[panel]
        : undefined;
}
