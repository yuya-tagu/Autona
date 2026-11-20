# リファクタ候補リスト (Known Smells)

未解決の項目だけを現在形で書く。解消・却下した項目は消す（経緯は git 履歴。コミット d4311f81 の `.claude/known-smells.md` に S-01〜S-18 の全文がある）。ID は変えない。

- **優先度**: P1（高）> P2（中）> P3（低）
- **難易度**: D1（簡単・局所的）> D2（中程度）> D3（難・広範囲）
- **安全度**: S1（テスト可）> S2（手動確認必要）> S3（リスク高）

---

## S-03: God __init__ — Autona.Window [P2/D3/S3]

`Window.__init__` は SaveBlock で `_BuildUi()`（`src/Autona.py`、約 220 行）を包むだけ。ウィンドウの中身は共有ビュー `AutonaWebPanelHost` 1 枚で、ナビ・ヘッダー・フッター・全ページは React の AppShell が持つ。`_BuildUi` に残るのは、ロケール・テーマ・フォント・既定アイコンの用意、state のシード、ホストの組み立てと結線、保存したページの復元（`AutonaWindowState`）、`show()` → スピナー → `_StartWebServer` の起動順、各シングルトンの `UiMounted`。

**対策案**: 起動前のファイル準備（`out/`・`user_data/games`・`layout/logo.png` の種）とフォント・テーマの塊を `_PrepareFiles()` / `_LoadFonts()` 程度に分ける。優先度は低い。

**影響範囲**: `src/Autona.py` のみ。

**リスク**: 起動順（ホストの組み立て → 保存したページの復元 → `show()` → `core-shown` → スピナーとその上限 → `call_when_painted(_StartWebServer)`）は `tests/test_web_panel_host.py` が pin している。分割時はこの順を保つ。

---

## S-13: AutonaWebServer.py のルート肥大化 [P3/D2/S1]

`src/AutonaWebServer.py`（約 2,550 行）に `@app.route` 47 本と `@socketio.on` 5 本が集約されている。Flask Blueprint によるドメイン別の分割（scoreboard・commentary・bracket 等）は未実施。

**影響範囲**: `AutonaWebServer.py`、`AutonaWebOperations.py`。ルートの台帳は `tests/test_webserver_routes.py` が pin している。

**付記**: ステージストライクの機能は削除済みだが、`StartGGDataProvider._parse_stage_strike()`（`src/TournamentDataProvider/StartGGDataProvider.py`）にパーサーが残っている。棚卸しの候補。
