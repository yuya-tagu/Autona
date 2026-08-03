# implement: 起動再設計 Step 4c — WebServer の表示後非同期起動

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §1.2 Stage 4・§2 Step 4-4 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) Consequences（OBS 取得数秒遅延は 2026-07-28 PdM 承認済み）／ 先行: Step 4a（PR #156）/ 4b-1（PR #157）

## ゴール

`Window.__init__` 内で同期実行している WebServer 一式（[src/Autona.py](../../src/Autona.py):419-443:
LAN 許可初回ダイアログ → `WebServer()` 生成 = Flask/SocketIO 構築 → `check_webserver_port` →
`start()` → シグナル接続）を **`show()` 後の非同期実行**へ移す。
dev・warm 実測で window-init → core-shown の支配項（〜6s のうち Flask 構築 + 起動が大半）。

## 実装方式

1. `Window.__init__` の該当ブロックを `self.webserver = None` スロット + `_StartWebServer()`
   メソッドへ移動。中身は現行ブロックを**順序ごと**移す（LAN 初回ダイアログ・
   `start_failed` 接続・ポートチェック・`start()`・`GameChanged` ×2 接続）
2. `ws_playerdb` の接続（[src/Autona.py](../../src/Autona.py):804 付近）も `_StartWebServer()` 内へ
   移動（現位置のままだと webserver 未生成で AttributeError）
3. 起動トリガ: `show()` + オーバーレイ表示後の `QTimer.singleShot(0, self._StartWebServer)`
   （イベントループに乗せて core-shown 計測より後に実行。シグナルハンドラ内モーダルに
   ならないため LAN / ポート衝突ダイアログの exec も安全。agent-pitfalls #9 との整合を
   コメントで明記）
4. **null 安全化**: webserver 未生成（起動直後に閉じた等）でも壊れない:
   - `closeEvent` の terminate/wait（:1034-1050）を `if self.webserver is not None:` ガード
   - その他 `self.webserver` を触る全箇所を grep して同様に（`OnWebServerStartFailed` は
     webserver 自身のシグナルなので生成済み前提でよい）
5. STARTUP 計測: `_StartWebServer` 完了時に `log_startup_phase("webserver-ready")` を追加
   （新フェーズ。既存フェーズ名は変更しない）

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] `self.webserver` を参照する全箇所（Autona.py 内外）を列挙し、show() 前に
      実行されうるものが無いか（あれば null ガード or 移動の対応方針を添えて）
- [ ] Web UI / OBS からの初回接続がサーバ起動前に来た場合の挙動（接続拒否 → クライアント側
      リトライ）が、Web UI（Socket.IO クライアント）の再接続で自然回復すること
      （web/src の Socket.IO 設定の reconnection を確認。**web/src は読むだけ・変更禁止**）
- [ ] LAN 許可初回ダイアログ・ポート衝突ダイアログが表示後に出ることの UX 影響
      （モーダルなのでウィンドウ表示後でも見落とされない想定だが、実装上の問題があれば報告）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#9 #12）・
  [.claude/rules/state-and-webserver.md](../../.claude/rules/state-and-webserver.md)
- **触ってはいけないファイル**: `web/src/**`（読み取りは可）
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし（test_webserver_routes 含む）

## 検証（ユーザー実施分・報告時に提示）

- [ ] 起動 → core-shown 短縮を STARTUP ログで確認（webserver-ready が core-shown より後）
- [ ] 表示後数秒以内に Web UI（`/web`・`/scoreboard`）が取得可能になる
- [ ] OBS ブラウザソースが表示後数秒以内に描画される（自動リトライで回復）
- [ ] ポート衝突時のダイアログが表示後に出て従来通り操作できる
- [ ] 起動直後（WebServer 起動前）にウィンドウを閉じてもクラッシュ・プロセス残留なし
- [ ] 初回起動（webserver_listen_lan 未設定）で LAN 許可ダイアログが表示後に出る

## スコープ外

- 4b-2（サイドバー lazy 基盤 + プレビュー 4 パネル）/ 4d（選手 DB ワーカー化）/
  4e（アクティブ board のみ構築）
