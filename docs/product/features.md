# Autona 機能一覧

格闘ゲーム大会の配信オペレーションを支援するデスクトップアプリケーション。スコアボード管理を中核に、配信オーバーレイ出力・Web リモート操作・サムネイル生成・X 投稿までを統合する。

本ドキュメントはコードベースと UI 定義（`.ui` ファイル・メニュー定義）をリバースエンジニアリングしてまとめたユーザー視点の機能一覧。実装詳細は [CLAUDE.md](../../CLAUDE.md) と [.claude/design/Design.md](../../.claude/design/Design.md) を参照。

---

## 目次

1. [メインメニュー](#1-メインメニュー)
2. [スコアボード管理](#2-スコアボード管理)
3. [選手情報管理](#3-選手情報管理)
4. [配信チャネル・ストリーム URL 管理](#4-配信チャネルストリーム-url-管理)
5. [X（旧 Twitter）投稿](#5-x旧-twitter-投稿)
7. [実況・解説パネル](#7-実況解説パネル)
8. [大会情報（Tournament Info）](#8-大会情報tournament-info)
9. [ブラケット表示（TOP 8）](#9-ブラケット表示top-8)
10. [選手リスト](#10-選手リスト)
11. [統計・分析](#11-統計分析)
12. [補助ウィジェット](#12-補助ウィジェット)
13. [クルーバトル](#13-クルーバトル)
14. [Individual Game Tracker](#14-individual-game-tracker)
15. [サムネイル・対戦動画生成](#15-サムネイル対戦動画生成)
16. [YouTube メタデータ生成](#16-youtube-メタデータ生成)
17. [結果画面生成](#17-結果画面生成)
18. [Start.gg 連携](#18-startgg-連携)
19. [Web UI（リモート操作）](#19-web-uiリモート操作)
20. [Web API エンドポイント](#20-web-api-エンドポイント)
21. [ホットキー](#21-ホットキー)
22. [ローカル選手 DB](#22-ローカル選手-db)
23. [ゲーム切替・Modded Content](#23-ゲーム切替modded-content)
24. [レイアウト・ウィンドウ操作](#24-レイアウトウィンドウ操作)
25. [設定・環境](#25-設定環境)
26. [ローカライズ](#26-ローカライズ)
27. [ヘルプ・サポートリンク](#27-ヘルプサポートリンク)
28. [自動アップデート](#28-自動アップデート)
29. [配信オーバーレイ出力](#29-配信オーバーレイ出力)
30. [その他の連携機能](#30-その他の連携機能)
31. [Board・Designer・Figma 取込](#31-boarddesignerfigma-取込)
32. [統計表示設定](#32-統計表示設定)

---

## 1. メインメニュー

[src/Autona.py](../../src/Autona.py) のサイドバー（`_navSections`）/ Help & Settings メニュー。

### サイドバー（`QStackedWidget` のページ切替）
- **Setup** — Tournament Info / Stream Settings（配信設定）
- **Scoreboard** — Scoreboard / Board（デザイン選択・ライブプレビュー・機能別カラー・Designer 起動。§ 31 参照）
- **Display** — Thumbnail Settings / Result Settings / Top 8 Display Settings / Commentary / Stats Display Settings（統計表示設定）
- **Data** — Statistics / Player List

### Help & Settings メニュー
- **Always on top** — ウィンドウを常に最前面に表示
- **Settings** — 設定ダイアログ（General / Language / Hotkeys。言語設定は Language カテゴリ。§ 25・§ 26）
- **Add game assets** — ゲームアセット入手ガイド
- **API Settings** — サブメニュー: 外部制御 API のリファレンス / テスター（Open reference & tester）、Companion settings（Bitfocus Companion / Stream Deck 接続ガイド）、OBS / Advanced Scene Switcher（OBS 側ツールから Autona API を叩くための接続ガイド）
- **Check for updates** — 手動アップデート確認
- **Join the Discord community** — Discord コミュニティ
- **Event support inquiries** — イベント運営支援の問い合わせ
- **About Autona** — アプリ情報・ライセンス表示

### 大会メニュー（サイドバー上部の大会ボタン）
- **Set tournament** — Start.gg イベント URL を入力してロード
- **Unset tournament** — 大会解除

### ゲーム選択（サイドバー上部）
- **ゲーム選択メニュー** — 対応ゲームから選択してアセットロード
- **Modded content** — Mod コンテンツの有効化（対応ゲームのみ表示）

---

## 2. スコアボード管理

[src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py)。

### できること
- **スコア加算・減算** — チーム 1 / チーム 2 それぞれに +1 / −1
- **Best Of 形式** — Bo1 / Bo3 / Bo5 / Bo7 などを動的切替
- **マッチ段階（Match）** — 予選 / Winners / Losers / Grand Final 等
- **フェーズ指定（Phase）**
- **Top N 指定** — トップ 8 / トップ 16 等のラウンド規模
- **チーム入れ替え（Swap）**
- **リセット（Reset Score）** — 両チームのスコアを 0 に戻す
- **全クリア（Clear All）** — 選手情報を含めてスコアボードを初期化（Web UI の Clear All ボタン / `*-clear-all` ルート）
- **1v1 ⇄ チーム モード切替**
- **前セットの読み込み（Load Previous Set）**
- **Start.gg セット選択ウィンドウ**
- **Station 選択ウィンドウ**
- **サムネイル即時生成ボタン** — スコアボード上部に配置、対戦動画 PNG を出力
- **ブラウザ版スコアボードを開くボタン** — `http://localhost:{port}/scoreboard` を既定ブラウザで開く
- **X 投稿ボタン** — § 5 参照

---

## 3. 選手情報管理

[src/AutonaScoreboardPlayerWidget.py](../../src/AutonaScoreboardPlayerWidget.py)。

### 入力項目
- ゲーマータグ / 本名 / スポンサー（Prefix）
- 国コード / 州コード
- シード番号
- 代名詞 / カスタムテキスト
- **Twitter ハンドル** — X 投稿時のメンションに自動利用
- キャラクター選択（複数キャラ・バリアント・スキン対応）
- 選手状態（ロスト側 / Welcome Back 等）

### 連携機能
- タグ入力でローカル DB 自動補完
- 国旗 / キャラアイコン / スポンサーロゴの自動表示
- 表示項目ごとの表示・非表示トグル（スコアボードの eye メニュー / Web UI の DISPLAY_OPTIONS。Twitter / Pronoun 等）

---

## 4. 配信チャネル・ストリーム URL 管理

[src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py)（`_addStreamChannel`・`PopulateStreamChannels`）。

- スコアボードにストリーム URL 欄（YouTube / Twitch / ニコ生 等）
- **ストリームチャネル追加ボタン** — 使う URL をドロップダウンにプリセット登録
- 複数チャネルを切替可能
- X 投稿時・サムネイル生成時に自動で本文に組み込まれる

---

## 5. X（旧 Twitter）投稿

[src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py) `PostToX`。

### ワンクリックで以下を実行
1. **サムネイル画像を自動生成**（`GenerateThumbnail(quiet_mode=True)`）
2. **クリップボードに画像をコピー**
3. **ツイート本文を自動組み立て**
   - 大会名（`tournamentInfo.tournamentName`）
   - ラウンド名（`[Phase] Top N - Match`）
   - 両チームの選手情報: `名前 @Twitterハンドル(キャラ1/キャラ2) [Seed:N]`
   - ストリーム URL（`🔴STREAM: ...`）
4. **`https://twitter.com/intent/tweet?text=...` を既定ブラウザで開く**

### バリデーション
- チーム 1 / チーム 2 の選手名・ラウンド名が空の場合は警告ダイアログ
- Twitter ハンドルが `@` 無し入力でも自動補正

---

## 7. 実況・解説パネル

[src/AutonaCommentaryWidget.py](../../src/AutonaCommentaryWidget.py)・[src/core/CommentaryModel.py](../../src/core/CommentaryModel.py)・[layout/commentators/](../../layout/commentators/)。

- 12 スロット固定の実況・解説者カード（名前 / チーム / 本名 / Twitter / 役割 / コメント）
- 役割プレフィックス（実況・解説・選手・主催）を選択可能
- 「配信に表示」トグルで OBS オーバーレイに出す最大 4 名（`MAX_DISPLAY`）を選択（人数に応じた可変レイアウト）
- 右ペインにデザイン選択プルダウン（`layout/commentators*/` を自動検出、選択で即時反映）+ オーバーレイのライブプレビュー + OBS 用ブラウザソース URL 案内
- 名前フィールドはローカル選手 DB から補完（チーム / Twitter 等を自動入力）

---

## 8. 大会情報（Tournament Info）

[src/AutonaTournamentInfoWidget.py](../../src/AutonaTournamentInfoWidget.py)・[src/layout/AutonaTournamentInfo.ui](../../src/layout/AutonaTournamentInfo.ui)。

### 自動取得情報
- 大会名 / イベント名 / 参加者数 / 開始日 / 会場住所 / ショートリンク
- **大会アイコン / バナーのダウンロード**（`_DownloadIconAndBannerAsync`）
  - Start.gg からアイコン・バナーを一括で自動取得、ローカル画像の差し替えも可
- **テーマカラー / アクセントカラー** のカラーピッカー

### 配信用ツール
配信 URL・配信タイトルコピー・配信サムネイル生成（Center Crop / Netflix Style）は Stream Settings パネル（[src/AutonaStreamSettingsWidget.py](../../src/AutonaStreamSettingsWidget.py)）に移動済み。

### その他
- **Load from tournament** — Start.gg から一括ロード
- **Clear** — 大会情報クリア

---

## 9. ブラケット表示（TOP 8）

[src/AutonaTop8Widget.py](../../src/AutonaTop8Widget.py)・[src/core/Top8Model.py](../../src/core/Top8Model.py)・[layout/bracket_top8/](../../layout/bracket_top8/)。

- FGC 標準の **TOP 8 固定ダブルエリミネーションブラケット**（Winners スタート 4 名 + Losers スタート 4 名、全 11 試合: WSF×2 / WF / LR1×2 / LQF×2 / LSF / LF / GF / GF リセット）
- **選手カード（8 スロット固定）** — 名前 / スポンサー / 国 / 使用キャラ・カラーを編集。対戦カードの変更は選手の入れ替えで行う
- **試合スコア表** — 各試合のスコアを入力し Apply で反映。勝者は自動で後続の試合に伝播、GF リセットは成立時のみ有効化。全リセットボタン付き
- **Start.gg 自動読込** — 大会のダブルエリミネーション最終フェーズを自動で対象にし、最終 8 名と各試合のスコアを取り込み（Ctrl+Shift+P ホットキーでも更新可）
- **表示ラベル**（オーバーレイに出すフェーズ名）の編集
- **ライブプレビュー** — `layout/bracket_top8/` オーバーレイの埋め込みプレビュー（再読み込みボタン付き）
- **OBS 追加案内** — ブラウザソース URL の表示・コピー。選手・スコアの編集は WebSocket（Socket.IO）で OBS 上のブラケットにリアルタイム反映

---

## 10. 選手一覧

[src/AutonaPlayerBrowserWidget.py](../../src/AutonaPlayerBrowserWidget.py)。サイドバーの独立パネル（統計情報の下）。2 タブ構成。

- **トーナメント出場選手** — 現在ロード中の大会の参加者をシード順に一覧表示（スポンサー / ハンドルネーム / 本名 / 国 / 使用キャラ）。行選択から P1 / P2 へワンクリックでセット
- **登録済み選手** — ローカル選手 DB（`user_data/local_players.json`）の閲覧・インクリメンタル検索・新規登録・編集・削除。編集はスコアボードと同じ選手フォームをダイアログで再利用

---

## 11. 統計・分析

STATS ドック（[src/AutonaStatsWidget.py](../../src/AutonaStatsWidget.py)）の内部タブに集約。

- **Head-to-Head / Last Sets / Tournament History**（内蔵タブ）
- **この大会の試合一覧**（[src/AutonaAnalysisWidget.py](../../src/AutonaAnalysisWidget.py) パネル）
  - 全試合一覧（ラウンド・シード・選手・スコア）
  - Upset Factor / Loser's Run / SeedOver
  - Stream Queue 反映 / 列ソート対応

---

## 12. 補助ウィジェット

- **アラート通知**（[src/AutonaAlertNotification.py](../../src/AutonaAlertNotification.py)）

---

## 13. クルーバトル

[src/AutonaCrewBattleWidget.py](../../src/AutonaCrewBattleWidget.py)（board 3。[src/AutonaScoreboardManager.py](../../src/AutonaScoreboardManager.py) が生成）。

- メンバー数設定（1〜9人、両チーム共通）
- メンバーごとのストック数管理・チーム合計の自動集計（名前未入力のメンバーは合計に含まない）
- アクティブメンバー（現在プレイ中）マーカー
- チーム名 / チームカラー編集・チーム入れ替え（Swap Teams）
- ストックリセット（Reset Stocks）
- Phase / Pool / Match / Best of 入力
- X 投稿ボタン

---

## 14. Individual Game Tracker

[src/AutonaIndividualGameTracker.py](../../src/AutonaIndividualGameTracker.py)。

- Bo3/Bo5 の各ゲームごとに使われたステージ・勝者を記録（ベータ機能）
- 設定から有効化

---

## 15. サムネイル・対戦動画生成

[src/AutonaThumbnailSettingsWidget.py](../../src/AutonaThumbnailSettingsWidget.py)・[src/thumbnail/ThumbnailRenderer.py](../../src/thumbnail/ThumbnailRenderer.py)。

- YouTube / Twitch 用サムネイルを自動生成
- テーマ / アクセントカラー
- バナー画像差し替え
- タイトルテンプレート（`{tournament} {round} | {fighter1} vs {fighter2} | {game}`）
- フォント・文字サイズ
- リアルタイムプレビュー
- 出力先: `out/stream/thumbnail.png`

---

## 16. YouTube メタデータ生成

[src/AutonaThumbnailSettingsWidget.py](../../src/AutonaThumbnailSettingsWidget.py) `_BuildVideoTitle()`。

- **動画タイトル自動生成** — 対戦情報からテンプレート適用
- **動画タイトル Copy ボタン**（`btCopyVideoTitle`）— クリップボードにコピー
- サムネイル生成時に同時出力される

---

## 17. 結果画面生成

[src/AutonaResultSettingsWidget.py](../../src/AutonaResultSettingsWidget.py)・[src/result/ResultRenderer.py](../../src/result/ResultRenderer.py)。

- 試合結果画面の自動生成
- プレビューはレイアウト選択・選手編集で自動再描画（手動更新ボタンなし）
- **Copy to Clipboard** — 画像をクリップボードにコピー
- **Save Result Image** — PNG ファイルとして保存
- 勝者ハイライト / スコア / ラウンド情報

---

## 18. Start.gg 連携

[src/TournamentDataProvider/StartGGDataProvider.py](../../src/TournamentDataProvider/StartGGDataProvider.py)。

### 取得できるデータ
| データ | 用途 |
|-------|------|
| 大会メタ情報 | 大会名・日程・ゲームタイトル・会場 |
| 大会アイコン / バナー | UI・サムネイル素材 |
| 参加者一覧 | 選手リスト |
| フェーズ / フェーズグループ | ブラケット |
| セット詳細 | スコアボード・ブラケット |
| ストリームキュー | 配信待機セット |
| ステーション別セット | 会場設備割当 |
| 選手の過去セット | H2H / Last Sets |
| 大会履歴 | Tournament History |
| Standings | 最終順位 |

### 特徴
- GraphQL クエリ（`src/TournamentDataProvider/StartGG*Query.txt`）
- 指数バックオフ自動リトライ（最大 5 回）/ 20 秒タイムアウト
- ユーザーアカウント連携で「自分が出場する大会」を追跡

---

## 19. Web UI（リモート操作）

[web/src/](../../web/src/)。React 19 / Redux Toolkit / MUI 7 / Socket.IO（状態受信）。操作は `/api/v1` REST 経由。

### `/web`・`/scoreboard`（board1）・`/team`（board2）
Header / CurrentSet / Team / Player / SetScore / CharSelector / UpcomingSets / Commentators

### 通信
- Socket.IO で `program_state` 全体受信、差分は `program_state_update`
- デルタは到着時に即適用（200ms の救済タイマーは接続前に溜まった分の保険）
- 操作（スコア更新等）は Socket.IO ではなく `/api/v1` REST 経由

---

## 20. Web API エンドポイント

[src/AutonaWebServer.py](../../src/AutonaWebServer.py) / [src/AutonaApiV1.py](../../src/AutonaApiV1.py) / [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)。

- 既存制御面: スコア操作 / セット管理 / 統計取得 / 選手情報 / 大会データロード / マスタデータ（characters・variants・match-names）など HTTP ルート + Socket.IO。
- 外部制御 API: `/api/v1/` 名前空間で実装済み。読み取り、スコア操作、set 情報更新、破壊的操作の `confirm:true`、重い操作の `202 Accepted` + 多重起動ガードに対応。
- API Settings 画面からエンドポイント一覧・curl 例・テスターを確認できる。

---

## 21. ホットキー

[src/AutonaHotkeys.py](../../src/AutonaHotkeys.py)。Windows グローバルホットキー。

| キー | 動作 |
|------|------|
| Ctrl+Shift+1〜4 | 1: チーム 1 +1 / 2: チーム 1 −1 / 3: チーム 2 +1 / 4: チーム 2 −1 |
| Ctrl+Shift+R | リセット |
| Ctrl+Shift+S | スワップ |
| Ctrl+Shift+O | セット読込 |
| Ctrl+Shift+P | フェーズグループ更新 |
| Ctrl+Shift+L | 反映（apply changes） |

設定から再割り当て可能。

---

## 22. ローカル選手 DB

[src/AutonaPlayerDB.py](../../src/AutonaPlayerDB.py)。

- 保存先: `user_data/local_players.json`
- 項目: prefix / gamerTag / name / twitter / country_code / state_code / mains / pronoun / custom_textbox
- タグ入力でオートコンプリート
- 旧 CSV 形式からの自動移行

---

## 23. ゲーム切替・Modded Content

- Title メニューのコンボボックスで対応ゲーム切替
- ゲームアセットの非同期ロード
- **Modded content トグル** — 対応ゲームのみ表示、Mod キャラ / ステージを有効化

---

## 24. レイアウト・ウィンドウ操作

### 基本
- 左サイドバー + `QStackedWidget` のページ切替（自由ドッキングは廃止。パネルは `QDockWidget` サブクラスだがドックしない）
- 選択ページとサイドバー折りたたみ状態を `QSettings` に保存・復元（`STATE_VERSION` 管理）
- 重いパネルは初回表示時に遅延構築

### その他
- **Always on top** トグル（Help & Settings メニュー）

---

## 25. 設定・環境

[src/Settings/AutonaSettingsWindow.py](../../src/Settings/AutonaSettingsWindow.py)。

### General タブ
- Web サーバーポート（既定 5107）
- LAN 公開（他デバイスからのアクセス許可。次回起動で反映）
- 下品表現フィルター
- 自動セット更新の有効/無効
- サムネイルウィジェット無効化
- Individual Game Tracker 有効化（ベータ）

### Hotkeys タブ
- 各アクションの再割り当て

### Language タブ
- Program / Game Asset / Tournament term の言語設定（§ 26）

---

## 26. ローカライズ

3 系統の言語設定を独立して持てる。

| 設定 | 対象 |
|-----|------|
| **Program Language** | アプリ UI 全般 |
| **Game Asset Language** | ゲームアセット（キャラ名・ステージ名） |
| **Tournament term language** | 大会用語（Winners/Losers 等） |

対応言語: 日本語 / 英語 の 2 言語。システム言語にも追従可能。変更後は再起動が必要。

---

## 27. ヘルプ・サポートリンク

Help & Settings メニュー。

- **Check for updates** — 手動アップデート確認
- **Join the Discord community** — Discord
- **Event support inquiries** — イベント運営支援の問い合わせ
- **About Autona** — アプリ情報・ライセンス表示

リンクは既定ブラウザで開く。

---

## 28. 自動アップデート

[src/AutonaUpdater.py](../../src/AutonaUpdater.py)。

- 起動時に GitHub Releases を確認
- 新バージョン検出時にダイアログ
- `release-windows.zip` を自動 DL → 次回起動時に `update.zip` を自動展開
- 旧 exe は自動バックアップ
- PyInstaller ビルド版のみ動作

---

## 29. 配信オーバーレイ出力

[src/AutonaWebServer.py](../../src/AutonaWebServer.py) + `layout/` サブモジュール。

- `out/program_state.json` — 全状態 JSON
- `out/` 配下の個別テキストファイル（OBS テキストソース向け）
- HTML オーバーレイテンプレート（gsap / jQuery）
- ブラウザソースで `http://localhost:5107/layout/...` を参照
- 用途別レイアウト多数: `scoreboard`, `bracket_top8`, `commentators`, `recent_sets`, `result`, `result_top16`, `tournament_history`, `thumbnail` 等

---

## 30. その他の連携機能

### スポンサー / チームロゴ
[src/core/SponsorHelper.py](../../src/core/SponsorHelper.py)。
- `user_data/sponsor_logo/` から prefix で自動マッチ
- 複数スポンサー対応

### 国・地域マスタ
[src/Helpers/AutonaCountryHelper.py](../../src/Helpers/AutonaCountryHelper.py)。
- ISO 国コード / 米国州コードの国旗マスタ

### 下品表現フィルター
[src/Helpers/AutonaBadWordFilter.py](../../src/Helpers/AutonaBadWordFilter.py)。
- タグ・本名の NG ワード警告・差し替え

### Twitch ユーザー名設定
- `SettingsManager.Get("twitch_username")` — Stats や Stream Queue のフィルタに利用

---

## 主要出力ファイル

| パス | 内容 |
|------|------|
| `out/program_state.json` | 全状態スナップショット |
| `out/*.txt` | OBS テキストソース向け個別ファイル |
| `out/stream/thumbnail.png` | 生成済みサムネイル |
| `user_data/local_players.json` | ローカル選手 DB |
| `user_data/games/{game}/` | ゲーム別キャラ・ステージ |
| `user_data/settings.json` | ユーザー設定 |

---

## 対応ゲーム

`user_data/games/` にゲーム別定義を配置。キャラ / ステージ / バリアント（スキン）を JSON + 画像で管理。

- Super Smash Bros. Ultimate
- Street Fighter 6
- Tekken 8
- その他（設定ファイルで拡張可能）

---

## 31. Board・Designer・Figma 取込

### BOARD パネル
[src/AutonaBoardWidget.py](../../src/AutonaBoardWidget.py)。
- スコアボードのデザイン選択（`layout/` の組込み + Designer 製 `layout/custom/{slug}`）
- ライブプレビュー（埋め込み WebEngine）
- 機能別カラー（`AutonaFeatureColors`、feature `board`）
- Designer 起動ボタン

### Designer
[web/src/ScoreboardDesigner/](../../web/src/ScoreboardDesigner/)・[src/AutonaDesignerWindow.py](../../src/AutonaDesignerWindow.py)・[src/AutonaDesignerService.py](../../src/AutonaDesignerService.py)。
- レイヤーエディタでスコアボードデザインを作成し `layout/custom/{slug}` に保存

### Figma 取込
[tools/figma-scoreboard-plugin/](../../tools/figma-scoreboard-plugin/)。
- Figma プラグインから Designer へデザインを取り込む。認証はペアリングコード方式

---

## 32. 統計表示設定
[src/AutonaStatsPreviewWidget.py](../../src/AutonaStatsPreviewWidget.py)（Stats Display Settings パネル）。
- 統計オーバーレイに出す統計情報の種類を選択
- Companion マクロ / OBS への追加案内

---

## 参考

- [README.md](../../README.md) — インストール・ビルド手順
- [CLAUDE.md](../../CLAUDE.md) — 開発者向けアーキテクチャ
- [.claude/design/Design.md](../../.claude/design/Design.md) — 実装仕様の正本

---

## 運用上の前提と劣化させてはいけない機能

- Windows 専用前提（グローバルホットキーは pynput、exe 配布）
- 言語変更は再起動必須（Program / Game Asset / Tournament term の 3 系統）
- 自動アップデートは PyInstaller ビルド版のみ動作（`python main.py` では無効）
- 永続化: 設定 `user_data/settings.json` / ローカル選手 DB `user_data/local_players.json`

変更時に必ず動作確認する対象（リグレッション禁止リスト）:

1. 既存 Web UI / OBS ブラウザソースの動作（`out/` 出力 + HTTP/WS 配信）
2. デスクトップ UI からのスコア操作とホットキー
3. 既存 HTTP/Socket.IO ルート群（`/score`・`/scoreboard<N>-*` 等）
4. StateManager → WebSocket デルタ配信のリアルタイム性
5. Start.gg ロード〜セット反映フロー
6. レイアウト保存/復元（STATE_VERSION）と保存データ互換

手順は [../operations/runtime-operations.md](../operations/runtime-operations.md) と [../../.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md) を参照。
