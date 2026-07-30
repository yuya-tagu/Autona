# implement: 起動再設計 Step 5 — 後始末・ドキュメント追従

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 5 ／ 先行: Step 1〜4 完了（PR #144-147, #149, #151-161）

## ゴール

Step 1〜4 で変わった起動アーキテクチャの実態に、ドキュメント群を追従させる。
あわせてポート衝突検知の残置分を調査し、整理の要否を判断する。
**原則ドキュメントのみの PR とし、コード変更はポート衝突まわりの
「明確に死んでいるコード・実態とズレたコメント/文言」に限る**（挙動変更は提案止まり）。

## 現在の実態（ドキュメントが合わせるべき正）

- `src/__init__.py` は空（import 副作用ゼロ）。シングルトン生成順の正本は
  `src/bootstrap.py` の `bootstrap_core()`（`src/Autona.py` の import 時に実行）
- Window は QMenuBar を持たない（サイドバー + QStackedWidget。Help & Settings メニュー）。
  `QMainWindow.saveState/restoreState` 不使用（`sidebarIndex_v{N}` 復元）
- **遅延構築**: 設定系 5 ウィンドウ（4a）/ Thumbnail・Result レンダラ（4b-1）/
  サイドバー 9 パネル = Board・Top8・Commentary・StatsPreview（4b-2）+
  Stream・Thumbnail・Result・Stats(+Analysis)・PlayerBrowser（4b-3）が初回表示時構築。
  eager は TournamentInfo（`tournamentInfo.*` の唯一の書き手のため意図的据え置き）と
  Scoreboard のみ
- board はアクティブ battle_format の 1 面のみ起動時構築、他は初回切替 /
  `GetScoreboard` の ensure 構築（4e）
- WebServer はオーバーレイ消滅後の非同期起動（4c）。`webserver-ready` フェーズ・
  AutonaLayoutPreview の readiness gate あり
- パネル未構築でも必要な state シードは `Window._SeedPanelState()` に集約
  （theme/accent・top8 正規化・commentary 正規化・stream org バージョン・廃止キー掃除）。
  board スケルトンは `AutonaScoreboardManager.CreateScoreboard()` 内
- STARTUP 計測フェーズ: gate / splash / import-src / window-init / core-shown /
  webserver-ready

## 作業内容

### 1. ポート衝突検知の残置調査（[src/AutonaWebServer.py](../../src/AutonaWebServer.py):968 `check_webserver_port` と呼び出し元 [src/Autona.py](../../src/Autona.py):883 付近）

- 設計正本 §2 Step 5 の「自己衝突が消えた前提でメッセージ・挙動を見直し」を実施:
  現在の起動順（WebServer は表示後）で check_webserver_port が担う役割
  （他プロセス衝突検知・kill 提案ダイアログ等）を精読し、
  (a) 死んでいる分岐・実態とズレたコメント/ダイアログ文言があれば修正、
  (b) **挙動変更が必要と判断した場合は実装せず提案として最終報告に記載**
- 翻訳文言（`src/i18n/Autona_ja.ts`）に触れる場合は手動編集
  （lupdate は使えない環境。文言変更が不要ならば触らない）

### 2. ドキュメント追従（コードの実態が正。矛盾はコード側に合わせる）

- [CLAUDE.md](../../CLAUDE.md)（リポジトリ直下）: アーキテクチャ概要表の
  Window 行（「QMenuBar + 全ドックウィジェット管理。STATE_VERSION でレイアウト復元」が
  実態ズレ）と、起動に関わる記述全般。**要点のみの方針を崩さない**（詳細は参照先へ）
- [.claude/design/Design.md](../../.claude/design/Design.md): 起動時挙動への言及を
  全 grep（「起動時」等）し、実態に合わせて修正（例: :337 Top8 の起動時シード →
  `_SeedPanelState` へ移動済み。lazy 構築されたパネルの記述）。
  構成・粒度は既存踏襲、全面書き換えはしない
- [docs/architecture/current-architecture.md](../../docs/architecture/current-architecture.md):
  起動シーケンス記述を上記「現在の実態」に更新
- [.claude/known-smells.md](../../.claude/known-smells.md):208 付近の
  `direct_import()` 言及（既に削除済みの仕組みを現存として記述）を解消。
  該当スメル項目全体を現状に合わせて見直す
- [docs/changes/startup-architecture-redesign.md](../../docs/changes/startup-architecture-redesign.md):
  §2 の各 Step に完了マークと PR 番号（Step 2: #145-146 / Step 3: #147, #149, #151-155 /
  4a: #156 / 4b-1: #157 / 4c: #158 / 4b-2: #159 / 4e: #160 / 4b-3: #161）、
  §3 に実測の推移を追記:
  - baseline: core-shown 27.58s（warm）/ 34.49s（cold）
  - Step 2+3 後: 19.14s（exe・cold 気味）
  - 4a 後: 14.22s（dev warm）→ 4b-1: 13.31s → 4c: 14.88s（webserver-ready 15.66s・
    run 間ばらつきあり）→ 4b-2: 13.02s（wsr 13.81s）→ 4e: 10.95s（wsr 11.64s）
  - 4b-3 後: 実測待ち（見込み 〜8.5〜9s。ユーザー計測後に追記）の行を用意
  - 注記: dev warm 計測。目標 ≤5s に対する残り内訳（gate+splash 〜2.6s /
    import-src 〜2.8s / TournamentInfo+Scoreboard+シェル構築）と、
    残候補（TournamentInfo サービス抽出・import 遅延化・PyInstaller 側）は
    「今後の候補（未着手）」として明記
- [docs/decisions/ADR-003-startup-architecture.md](../../docs/decisions/ADR-003-startup-architecture.md):
  Status を実装完了に更新（該当フィールドがあれば。無ければ追記しない）
- `.claude/rules/agent-pitfalls.md` / `.claude/rules/python-widgets.md` は
  Step 3 / 4b-2 で更新済み。**通読して残る実態ズレが無いかだけ確認**し、あれば最小修正

### 3. ドキュメント間の矛盾チェック

- 上記ファイル群と `.claude/README.md`・`docs/README.md` の相互参照が
  切れていないか確認（リンク先の存在・見出し名）

## 制約

- **web/src/ 変更禁止**（読み取り可）。アプリ起動禁止。コミット禁止
- ドキュメントは日本語（既存トーン踏襲・簡潔に）。コード内コメントは英語
- 各ドキュメントの記載ルール（[.claude/README.md](../../.claude/README.md)）に従う

## 検証（Claude 実施分）

- [ ] コード変更がある場合のみ: `python -m py_compile` + `pytest tests/ -q` 回帰なし
- [ ] 修正した全ドキュメントの Markdown リンクが実在ファイル・実在アンカーを指す
- [ ] 「起動時」「eager」「QMenuBar」「direct_import」等で全 doc を再 grep し、
      実態ズレの残存ゼロ

## スコープ外

- TournamentInfo のヘッドレスサービス抽出・import-src 遅延化（別タスク起票候補）
- ポート衝突まわりの挙動変更（提案のみ）
