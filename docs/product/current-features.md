# Current Features

外部制御 API / Companion 連携を**安全に足すため**のプロダクト視点メモ。
網羅的な機能カタログは**ここでは再記述せず** [../../FEATURES.md](../../FEATURES.md)（※下記の不整合に注意）に委ね、
本書は「中核ワークフロー」「劣化させてはいけない機能」「外部設計時の注意」に絞る。

> ステータス凡例: ✅ コード確認済 / 📄 既存ドキュメント由来（未再確認） / ⚠️ 要確認 / `TBD` 未記載

## Purpose

格闘ゲーム大会の**配信オペレーション支援**デスクトップアプリ。スコアボード管理を
中核に、配信オーバーレイ出力・Web リモート操作・サムネイル/結果画像生成・SNS 投稿
までを 1 人〜少人数運営でカバーする統合ワークベンチ。

- 想定ユーザー: 小〜中規模大会（参加者 32〜256 名規模）の TO 兼配信オペ 1〜2 名（📄 ROADMAP 由来）
- 動作環境: Windows（PyInstaller 製 `Autona.exe`）✅

機能の一覧（スコアボード / Start.gg 連携 / 統計 / X・BlueSky 投稿 / 設定等）は
[../../FEATURES.md](../../FEATURES.md) を参照。本書では外部連携に影響する範囲のみ扱う。

## Core workflows

配信オペの時系列に沿った中核フロー。**外部制御を足してもこの流れを壊さない**ことが前提。

1. **大会ロード** — Start.gg イベント URL を設定し、参加者/ブラケット/セットを取得 ✅
2. **セット選択 → スコアボード反映** — ブラケット or Start.gg セット選択からスコアボードへロード ✅
3. **試合中のスコア操作** — チーム/選手/スコア/Best Of/Match/Phase をデスクトップ UI・ホットキー・Web UI から操作 ✅
4. **配信オーバーレイ出力** — 状態を `out/` のファイル群＋ HTTP/WebSocket で OBS ブラウザソースに反映 ✅
5. **告知・記録** — サムネイル/結果画像生成、X / BlueSky 投稿、YouTube メタデータ生成 📄

> 各機能の HTTP/WS 制御面（`/score`・`/scoreboard<N>-*`・`stats-*` 等）と未確定ルート/ポートは
> [../architecture/current-architecture.md](../architecture/current-architecture.md) に集約。

## Stage strike features（削除済み — 設計で叩かないこと）

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

## 外部設計時に効く運用制約

機能カタログには出ないが、外部連携の前提になる制約（一次情報は CLAUDE.md / agent-pitfalls）:

- **Windows 専用前提**（グローバルホットキーは pynput / Windows、exe 配布）📄
- **言語変更は再起動必須**（Program / Game Asset / Tournament term の 3 系統）📄
- **自動アップデートは PyInstaller ビルド版のみ動作**（`python main.py` では無効）📄
- **Bluesky 設定は認証情報（App Password）を含む** ⚠️ — 値の出力・外部送信禁止（CLAUDE.md 機密ルール）
- 永続化: 設定 `user_data/settings.json` / ローカル選手 DB `user_data/local_players.json` 📄

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
