# Current Features

Autona の現行機能を**プロダクト視点**で整理する。詳細な機能カタログは
[../../FEATURES.md](../../FEATURES.md) にある（ただし後述の不整合に注意）。
ここでは外部制御 API / Companion 連携を検討するうえで重要な「中核ワークフロー」
「運用制約」「劣化させてはいけない機能」に絞る。

> ステータス凡例: ✅ コード確認済 / 📄 既存ドキュメント由来（未再確認） / ⚠️ 要確認 / `TBD` 未記載

## Purpose

格闘ゲーム大会の**配信オペレーション支援**デスクトップアプリ。スコアボード管理を
中核に、配信オーバーレイ出力・Web リモート操作・サムネイル/結果画像生成・SNS 投稿
までを 1 人〜少人数運営でカバーする統合ワークベンチ。

- 想定ユーザー: 小〜中規模大会（参加者 32〜256 名規模）の TO 兼配信オペ 1〜2 名（📄 ROADMAP 由来）
- 動作環境: Windows（PyInstaller 製 `Autona.exe`、約 230〜245MB）✅

## Core workflows

配信オペの時系列に沿った中核フロー（📄 FEATURES + ✅ コード確認の混在）。

1. **大会ロード** — Start.gg イベント URL を設定し、参加者/ブラケット/セットを取得 ✅
2. **セット選択 → スコアボード反映** — ブラケット or Start.gg セット選択からスコアボードへロード ✅
3. **試合中のスコア操作** — チーム/選手/スコア/Best Of/Match/Phase をデスクトップ UI・ホットキー・Web UI から操作 ✅
4. **配信オーバーレイ出力** — 状態を `out/` のファイル群＋ HTTP/WebSocket で OBS ブラウザソースに反映 ✅
5. **告知・記録** — サムネイル/結果画像生成、X / BlueSky 投稿、YouTube メタデータ生成 📄

## Scoreboard features

✅ = [src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py) 等で確認。

- スコア加減算（チーム別 +1 / −1）✅（HTTP/WS ルートあり）
- Best Of 切替（Bo1/Bo3/Bo5/Bo7 等）📄
- Match（予選/Winners/Losers/Grand Final 等）・Phase・Top N 指定 📄
- チーム入れ替え（Swap）✅（`swap-teams` ルートあり）
- リセット（スコア / マッチ / 選手 / 全クリア）✅（`reset-*` / `clear-all` ルートあり）
- 1v1 ⇄ チームモード切替（`_applyScoreboardMode`）📄（CLAUDE.md）
- チーム/クルーバトル（Stock Pool / First To、ストック追跡）✅（`crew-*` ルートあり）
- ストリームチャンネル（配信 URL）管理 📄
- 選手情報入力（タグ/本名/スポンサー/国/シード/キャラ/コントローラー等）📄

## OBS integration features

- **HTML オーバーレイテンプレート**を `layout/`（Git サブモジュール）に保持 📄
- OBS は **ブラウザソース**で `http://localhost:{port}/...` を参照（OBS-websocket は使わない方針）📄
- OBS テキストソース向けに `out/` 配下へ個別テキストファイルを出力 📄
- 状態は `out/program_state.json`（全状態スナップショット）にも出力 ✅（`/program-state` ルート）
- ⚠️ **既定ポート・ルーティングの正確な値は要確認**:
  - 既定ポート: README/FEATURES は `5000`、CLAUDE.md は設定既定 `5000` 📄 → 実値は SettingsManager 既定を確認（`TBD`）
  - Web UI ルート: CLAUDE.md は `/web`（既定）`/scoreboard`（board1）`/team`（board2）、
    FEATURES/README は `/scoreboard` `/stage-strike-app`。**CLAUDE.md を正とし要再確認** ⚠️

## Tournament / match operation features

- Start.gg 連携（大会メタ/参加者/フェーズ/セット/ストリームキュー/ステーション/Standings 等）📄
- ブラケット表示（QGraphicsView 樹形図、セットクリックでロード）📄
- 選手リスト（Start.gg Standings 一括読込）📄
- 統計（H2H / Last Sets / Tournament History / Upset Factor 等）✅（`stats-*` ルートあり）
- 実況者パネル（人数増減・各種項目・並び替え）📄
- 大会情報（バナー/アイコン DL、配信タイトル生成、配信サムネイル生成）📄

## Stage strike features

> ⚠️ **現行ビルドには存在しない（削除済み・コード確認済）。**
>
> - `CLAUDE.md` 記載: 「ステージストライク機能（旧 `StageStrikePage` / `/stage-strike-app` /
>   RULES ドック / `ruleset`・`stage_strike` 状態）は削除済み」
> - コード確認: `src/**/*Stage*.py`、`web/src/**/StageStrike*`、`web` 内の `stage-strike-app` /
>   `stage_strike` 参照は **いずれも存在しない**（2026-06-14 確認）
> - 一方 `FEATURES.md` / `AGENTS.md` には記述が残っている → **これらがスタール**
>
> 外部制御 API / Companion 連携の設計時に「ステージストライクを叩く」想定を入れないこと。
> ルート直下に `assets/rulesets.json`（未追跡）が存在するが、現行機能との関係は `TBD`。

## X posting features

[src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py) `PostToX` 📄。

- ワンクリックでサムネ生成 → クリップボードコピー → ツイート本文組立 → `intent/tweet` を既定ブラウザで開く
- BlueSky 自動投稿（[src/Helpers/AutonaBskyHelper.py](../../src/Helpers/AutonaBskyHelper.py)）+ alt text 自動付与
- 投稿前バリデーション（選手名・ラウンド名の空チェック）

## Settings and configuration

[src/Settings/AutonaSettingsWindow.py](../../src/Settings/AutonaSettingsWindow.py) 📄。

- General（Web サーバーポート、各種自動制御の ON/OFF、ベータ機能）
- Hotkeys（再割り当て）
- Default Display Options（選手ウィジェットの既定表示項目）
- Bluesky（ホスト/アカウント/App Password）⚠️ **認証情報を含む** — 出力・外部送信禁止
- 永続化: `user_data/settings.json`（📄）/ ローカル選手 DB `user_data/local_players.json`（📄）

## Known operational constraints

- **Windows 専用前提**（グローバルホットキーは pynput / Windows、exe 配布）📄
- **WebEngine 依存**でサムネイル/画像生成（Qt6WebEngineCore.dll 約 196MB）→ exe が大きい ✅（CLAUDE.md）
- **`layout/` は Git サブモジュール**。オーバーレイは別管理 📄
- **言語変更は再起動必須**（Program / Game Asset / Tournament term の 3 系統）📄
- **自動アップデートは PyInstaller ビルド版のみ動作**（`python main.py` では無効）📄
- **Start.gg GQL は 20 秒タイムアウト + 指数バックオフ**。重いクエリ追加で timeout しやすい ✅（agent-pitfalls）
- 状態キーパスはドット区切り文字列・数値も文字列キー（`"1"`）✅（agent-pitfalls）
- `objectName()` が StateManager キーになる箇所がある → `.ui` の objectName 変更で保存互換が壊れる ✅

## Features that must not regress

外部制御 API / Companion 連携を追加しても**絶対に劣化させない**コア（暫定リスト、PdM 確認要）:

1. **既存 Web UI / OBS ブラウザソースの動作**（`out/` 出力 + HTTP/WS 配信）— 現場の配信が直接依存
2. **デスクトップ UI からのスコア操作とホットキー** — 本番中の最速操作経路
3. **既存 HTTP/Socket.IO ルート群**（`/score`・`/scoreboard<N>-*` 等、約 30+）— 既存クライアント/オーバーレイが利用
4. **StateManager → WebSocket デルタ配信のリアルタイム性** — 遅延/欠落は配信事故に直結
5. **Start.gg ロード〜セット反映フロー** — 大会運営の根幹
6. **レイアウト保存/復元（STATE_VERSION）と保存データ互換** — オペの環境が壊れない

> このリストは「変更時に必ず動作確認する対象」。具体的な手順は
> [../operations/runtime-operations.md](../operations/runtime-operations.md) と
> [../../.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md) を参照。
