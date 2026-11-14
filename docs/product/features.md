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
- **Add game assets** — ゲームアセット入手ページ（共有ビューの `/web/assets`。上流の公開ソース `joaorb64/StreamHelperAssets` からパックをダウンロード、または手持ちの ZIP を取り込む。フォルダは ZIP にしてから取り込む（フォルダからの直接の取り込みは今後のアップデートで戻る）。ブラウザで開くと案内とソースへのリンクだけ）。ゲームのメニューの「Add game assets」も同じページを開く
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

[web/src/ScorePage/](../../web/src/ScorePage/)（React の SCORE ページ。ADR-005 S4aa）・[src/core/Actions.py](../../src/core/Actions.py)。Qt のスコアボード（`AutonaScoreboardWidget` ほか）は ADR-005 S4ac-3 で削除した。

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
- **Start.gg セット選択ダイアログ**（「大会の試合を選ぶ」。ADR-005 S4ab）
- **配信台（Station）選択ダイアログ** と追従中のタイマー行
- **ブラウザ版スコアボードを開くボタン** — `http://localhost:{port}/scoreboard` を既定ブラウザで開く
- **X 投稿ボタン** — § 5 参照

Qt 版にあった「前セットの読み込み」「サムネイル即時生成ボタン」は Qt のスコアボードと一緒に削除した（ADR-005 S4ac-3。サムネイルはサムネイルのページと X 投稿が作る）。

---

## 3. 選手情報管理

[web/src/ScorePage/Cards.tsx](../../web/src/ScorePage/Cards.tsx)・[src/core/PlayerService.py](../../src/core/PlayerService.py)（Qt の選手カード `AutonaScoreboardPlayerWidget` は ADR-005 S4ac-3 で削除）。

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
- 表示項目ごとの表示・非表示トグル（Web UI の DISPLAY_OPTIONS。Twitter / Pronoun 等。Qt のスコアボードの eye メニューは ADR-005 S4ac-3 で削除）

---

## 4. 配信チャネル・ストリーム URL 管理

[web/src/StreamPage/](../../web/src/StreamPage/)・[src/core/StreamService.py](../../src/core/StreamService.py)（配信設定のページ。ADR-005 S4d / S4e。Qt のスコアボードの欄は ADR-005 S4ac-3 で削除）。

- スコアボードにストリーム URL 欄（YouTube / Twitch / ニコ生 等）
- **ストリームチャネル追加ボタン** — 使う URL をドロップダウンにプリセット登録
- 複数チャネルを切替可能
- X 投稿時・サムネイル生成時に自動で本文に組み込まれる

---

## 5. X（旧 Twitter）投稿

SCORE ページの X 投稿ボタン（[web/src/ScorePage/](../../web/src/ScorePage/)）。本文は `AutonaActions.PostText`（`GET /api/v1/scoreboards/{n}/post`、ADR-005 S4x）。Qt のスコアボードの `PostToX` は ADR-005 S4ac-3 で削除した。

### ワンクリックで以下を実行
1. **サムネイル画像を自動生成**（`POST /api/v1/thumbnail:generate`。クルーボードは画像なし）
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

[web/src/CommentaryPage/](../../web/src/CommentaryPage/)（ADR-005 S4b）・[src/core/CommentaryModel.py](../../src/core/CommentaryModel.py)・[layout/commentators/](../../layout/commentators/)。

- 12 スロット固定の実況・解説者カード（名前 / チーム / 本名 / Twitter / 役割 / コメント）
- 役割プレフィックス（実況・解説・選手・主催）を選択可能
- 「配信に表示」トグルで OBS オーバーレイに出す最大 4 名（`MAX_DISPLAY`）を選択（人数に応じた可変レイアウト）
- 右ペインにデザイン選択プルダウン（`layout/commentators*/` を自動検出、選択で即時反映）+ オーバーレイのライブプレビュー + OBS 用ブラウザソース URL 案内
- 名前フィールドはローカル選手 DB から補完（チーム / Twitter 等を自動入力）

---

## 8. 大会情報（Tournament Info）

React の [web/src/EventPage/](../../web/src/EventPage/)（`/web/event`、デスクトップでは共有ビューに表示。ADR-005 S4g で Qt 版から移行）。ブラウザ（LAN の別の PC・ヘッドレス）からも同じページで操作できる。処理の実体は core の [src/core/TournamentService.py](../../src/core/TournamentService.py)（大会の設定・解除）・[src/core/TournamentInfoService.py](../../src/core/TournamentInfoService.py)（入力欄）・[src/core/TournamentMediaService.py](../../src/core/TournamentMediaService.py)（アイコン / バナー）・[src/core/ColorService.py](../../src/core/ColorService.py)（大会カラー）で、ページは `/api/v1` を呼んで state を描くだけ。

### 大会の設定
- start.gg の event URL、または大会管理画面のブラケットの URL（admin）を貼り付けて「設定」。アイコン / バナーも一緒に取得する。「再取得」は同じ URL でも読み直す。「解除」は確認つきで、選択中のゲームも外れる
- 状態（イベントを探している / 読み込み中 / 読み込み済み / 見つからない / 読み込めない）を URL の下に出す。サイドバー・ほかの端末・起動時の読み直しで設定した大会の状態も出る（state の `tournamentInfo.load`）
- URL の欄は今の大会の URL（`tournamentInfo.url`）を出す。打ちかけの間に大会の URL がどこかで変わったら（設定・解除）、打ちかけは捨ててその URL を出す（古い URL で設定し直してしまわないように）。断られた URL・イベントの見つからない admin URL は、直せるように欄に残る。「解除」は打ちかけも消す

### 自動取得情報
- 大会名 / イベント名 / 参加者数（0〜999999）/ 開始日 / 会場住所（手で書き換えた値は、次に大会を読み込むまで残る）
- ゲームの選択（サイドバーのゲームボタンと同じ。一覧はゲームの読み込み（state の `game` の変化）とページの再表示で読み直す。スキャンだけでは state が変わらないので、ページを出し直したときに出る）
- **大会アイコン / バナー**: Start.gg から一括で取得（バックグラウンドのジョブ。状態は `tournamentInfo.media`）、PC の画像で差し替え（アイコンは長辺 512・バナーは長辺 1920 の PNG に縮小）、Autona の既定に戻す（確認つき）
- **テーマカラー / アクセントカラー** のカラーピッカー（変えると機能別のカラーもその色に揃う）と「既定の色に戻す」

### 配信用ツール
配信 URL・配信タイトルコピー・配信サムネイル生成（Center Crop / Netflix Style）は Stream Settings（配信設定）ページ（React の [web/src/StreamPage/](../../web/src/StreamPage/)。`/web/stream`、デスクトップでは共有ビューに表示。ADR-005 S4e で Qt 版から移行）にある。ブラウザからも同じページで操作でき、配信サムネの画像コピーはこの PC で開いたとき（デスクトップ・`localhost`）だけで、LAN の別の PC からは「画像を開く / 保存」になる。処理の実体は core の [src/core/StreamService.py](../../src/core/StreamService.py)（ADR-005 S4d）で、配信タイトルは大会を読み込むとパネルを開かなくても（ヘッドレスでも）生成される。手で書き換えたタイトルは、空にするまで自動生成で上書きしない（再起動しても残る。打っている途中に自動生成が走っても、打った文字が勝つ）。

- 配信サムネイルの出力: `out/stream/thumbnail_{背景}_{スタイル}_{YYYYMMDD_HHMMSS}.jpg`（背景 `banner` / `org`・スタイル `center` / `netflix`。直近 30 世代を残す）。生成はバックグラウンドで走り、状態は `stream.thumbnail` に出る
- 生成オプション（背景・スタイル・団体アイコンの合成）は `settings.json` の `stream_thumbnail_options` に保存される

---

## 9. ブラケット表示（TOP 8）

React の [web/src/Top8Page/](../../web/src/Top8Page/)（`/web/top8`、デスクトップでは共有ビューに表示。ADR-005 S4i で Qt 版から移行）・[src/core/Top8Model.py](../../src/core/Top8Model.py)・[layout/bracket_top8/](../../layout/bracket_top8/)。ブラウザ（LAN の別の PC・ヘッドレス）からも同じページで操作できる。処理の実体は core の [src/core/Top8Service.py](../../src/core/Top8Service.py) で、ページは `/api/v1`（`top8*`・`bracket:refresh`・`colors/top8`・`layouts/selected/top8`）を呼んで state を描くだけ。

- FGC 標準の **TOP 8 固定ダブルエリミネーションブラケット**（Winners スタート 4 名 + Losers スタート 4 名、全 11 試合: WSF×2 / WF / LR1×2 / LQF×2 / LSF / LF / GF / GF リセット）
- **選手カード（8 スロット固定）** — 名前 / 所属タグ（打っている間も送る）/ 国（カスタム国旗を含む。国旗つき）/ 使用キャラ・カラー（アイコンつき。一覧は読み込み中のゲームのもの）を編集。対戦カードの変更は選手の入れ替えで行う
- **試合スコア表** — 各試合のスコアを入力し「反映」で反映（入力した試合だけを送る。それまでは画面の中だけで、ほかの端末の更新・取り込みで試合が変わると入力は捨てる）。勝者は自動で後続の試合に伝播、GF リセットは成立時のみ有効化。全リセットボタン付き（確認つき）
- **Start.gg 自動読込** — 大会のダブルエリミネーション最終フェーズを自動で対象にし、最終 8 名と各試合のスコアを取り込み（Ctrl+Shift+P ホットキーでも更新可）
- **表示ラベル**（オーバーレイに出すフェーズ名）の編集
- **デザイン選択とライブプレビュー** — `top8` 種別のデザイン（`layout/bracket_top8*/` ほか）の選択と、オーバーレイの埋め込みプレビュー（プレビューの画像コピー / 保存は Web 版で廃止）
- **機能別カラー**（テーマ / アクセント、大会カラーに合わせる）
- **OBS 追加案内** — ブラウザソース URL の表示・コピー。選手・スコアの編集は WebSocket（Socket.IO）で OBS 上のブラケットにリアルタイム反映

---

## 10. 選手リスト

React の [web/src/PlayerBrowserPage/](../../web/src/PlayerBrowserPage/)（`/web/playerBrowser`、デスクトップでは共有ビューに表示。サイドバーの Data。ADR-005 S4n で Qt 版から移行）。ブラウザ（LAN の別の PC はトークンが要る・ヘッドレス）からも同じページで使える。行と保存は core の [src/core/PlayerDirectory.py](../../src/core/PlayerDirectory.py)。2 タブ構成で、検索（タグ・スポンサー・本名ほか。本名は表に出さない）と 100 行ごとのページ分割は共通。

- **トーナメント出場選手** — 現在ロード中の大会の参加者をシード順に一覧表示（シード / スポンサー / ハンドルネーム / 国 / 使用キャラ）。行選択から P1 / P2 へワンクリックでセット（ボード 1 の各チームの 1 人目。ローカル選手 DB にある選手）、編集
- **登録済み選手** — ローカル選手 DB（`user_data/local_players.json`）の閲覧・列ソート・新規登録・編集（行のダブルクリックでも）・確認つきの削除。編集ダイアログの項目はスポンサー / ハンドルネーム / 本名 / Twitter / 国 / 地域 / キャラ（今のゲームのもの）/ 追加情報。触っていない国・地域・キャラは保存で変えない。スポンサーかハンドルネームを変えると別の選手として保存される（元の行は残る）

---

## 11. 統計・分析

React の [web/src/StatsPage/](../../web/src/StatsPage/)（`/web/stats`、デスクトップでは共有ビューに表示。ADR-005 S4k で Qt 版から移行）。ブラウザ（LAN の別の PC・ヘッドレス）からも同じページで見られる。統計の取得と書き込みは core の [src/core/StatsService.py](../../src/core/StatsService.py)、試合一覧は [src/core/AnalysisService.py](../../src/core/AnalysisService.py) で、ページは `/api/v1`（`scoreboards/1/stats:refresh`・`analysis:refresh`・`GET analysis`）を呼んで state を描くだけ。

- 上部: 「統計情報を取得する」と、アップセット係数・対戦カードの 1 行（ボード 1。選手名は反映済みの値）
- **直近の対戦 / 戦績 / 大会履歴**（Head-to-Head / Last Sets / Tournament History）
- **この大会の試合一覧**
  - 全試合一覧（ラウンド・シード・選手・スコア）
  - Upset Factor / Loser's Run / SeedOver
  - 列の並べ替え / 選手名・ラウンドの絞り込み
  - 結果は core が持つので、ページを開き直しても・別の端末からも同じ一覧が見える（大会を変える・再起動で消える）

---

## 12. 補助ウィジェット

- （アラート通知は何も emit されない死んだコードだったので、ADR-005 SF-1 で削除した）

---

## 13. クルーバトル

SCORE ページのクルー形式（board 3。[web/src/ScorePage/](../../web/src/ScorePage/)）・[src/core/CrewBattleService.py](../../src/core/CrewBattleService.py)。Qt のクルーボード（`AutonaCrewBattleWidget`）は ADR-005 S4ac-3 で削除した。

- メンバー数設定（1〜9人、両チーム共通）
- メンバーごとのストック数管理・チーム合計の自動集計（名前未入力のメンバーは合計に含まない）
- アクティブメンバー（現在プレイ中）マーカー
- チーム名 / チームカラー編集・チーム入れ替え（Swap Teams）
- ストックリセット（Reset Stocks）
- Phase / Pool / Match / Best of 入力
- X 投稿ボタン

---

## 14. Individual Game Tracker

**削除済み**。Bo3/Bo5 の各ゲームごとに使われたステージ・勝者を記録するベータ機能だった。行の UI（Qt のスコアボードの `AutonaIndividualGameTracker`）は ADR-005 S4ac-3 で Qt の SCORE ごと削除し、記録（core の `GameTrackerService`・state の `score.{n}.stages`）・v1 の `GET /api/v1/stages` / `PATCH /api/v1/scoreboards/{n}/stages/{game}`・設定 `general.disable_individual_game_tracker` も ADR-005 S4ac-4（2026-11-12、オーナー承認 2026-11-11）で削除した。節の番号は欠番として残す。

---

## 15. サムネイル・対戦動画生成

サムネイル（Thumbnail Settings）ページ（React の [web/src/ThumbnailPage/](../../web/src/ThumbnailPage/)。`/web/thumbnail`、デスクトップでは共有ビューに表示。ADR-005 S4p で Qt 版から移行）・[src/core/ThumbnailService.py](../../src/core/ThumbnailService.py)（設定・動画タイトル・描画の順番。ADR-005 S4o）・[src/thumbnail/ThumbnailRenderer.py](../../src/thumbnail/ThumbnailRenderer.py)（オフスクリーンの描画。デスクトップだけ）。ブラウザからも同じページで設定でき、プレビューと作成はデスクトップアプリが描く（ヘッドレスでは設定だけ）。スコアボードの「サムネイル生成」・X 投稿もページの設定どおりに描く。

- YouTube / Twitch 用サムネイルを自動生成
- テーマ / アクセントカラー
- バナー画像差し替え
- タイトルテンプレート（`{tournament} {round} | {fighter1} vs {fighter2} | {game}`）
- フォント・文字サイズ
- リアルタイムプレビュー
- 出力先: `out/thumbnails/{選手1}-vs-{選手2}-{日時}.{png,jpg}`

---

## 16. YouTube メタデータ生成

[src/core/ThumbnailService.py](../../src/core/ThumbnailService.py) の `BuildVideoTitle()`（ADR-005 S4o。表示はサムネイルページ）。

- **動画タイトル自動生成** — 対戦情報からテンプレート適用
- **動画タイトルのコピー** — サムネイルページの「コピー」でクリップボードへ（ページでの編集は保存しない）
- サムネイル生成時に同時出力される

---

## 17. 結果画面生成

React の [web/src/ResultPage/](../../web/src/ResultPage/)（`/web/result`）・[src/core/ResultService.py](../../src/core/ResultService.py)・[src/result/ResultPageRenderer.py](../../src/result/ResultPageRenderer.py)。

- 大会リザルト画像の作成（6 レイアウト: 横 TOP8 / 横 TOP16 / 縦 2 種 / 団体 2 種。`out/results/` に PNG・30 世代）
- 単独は順位・所属タグ・名前・X ハンドル・キャラとカラー 3 つ、団体はチーム名とメンバー 5 人。入力は再起動後も残り、「すべてクリア」で今の種類だけ消す
- キャラ欄が空の選手は、スコアボードに同名の選手がいればそのキャラで描く（ページの行に「スコアボードから補完」と出る）
- 上下ボタン（キーボード可）とドラッグで並べ替え。順位は上から振り直す
- **大会結果を読み込む** — Start.gg の standings を取り込む（入力があれば確認。失敗と「まだ結果が無い」を区別して出す）
- プレビューはレイアウト・行・大会情報・色の変更で自動再描画（表示中だけ。背景はサムネイルの設定に従う）。何も入力が無いときは「サンプル」と表示
- **作成** — 画像を保存して保存先を出す。**画像をコピー** は作成した画像（この PC のページだけ。LAN の http では「開く / 保存」）
- **X に投稿** — 1 クリックで作成・クリップボードへのコピー・投稿画面（デスクトップは既定のブラウザ）。投稿画面に貼り付ける
- **選手を DB に保存** — 入力した選手を選手リストへ（作成と投稿のときも新しい選手は自動で追加）

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

[src/AutonaWebServer.py](../../src/AutonaWebServer.py) / [src/AutonaApiV1.py](../../src/AutonaApiV1.py) / [src/AutonaWebOperations.py](../../src/AutonaWebOperations.py)。

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
| Ctrl+Shift+O | アクティブボード（1v1 / チーム）のセット選択ウィンドウを開く。クルーでは何もしない |
| Ctrl+Shift+P | フェーズグループ更新 |
| Ctrl+Shift+L | 反映（apply changes） |

設定から再割り当て可能。

---

## 22. ローカル選手 DB

[src/core/PlayerDB.py](../../src/core/PlayerDB.py)（データと mains の正規化。変更の通知を Web の push に中継する Qt のアダプタは [src/AutonaPlayerDB.py](../../src/AutonaPlayerDB.py)）。

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

「Help & Settings」→「設定」で開く React の設定ページ（`/web/settings`。[web/src/SettingsPage/](../../web/src/SettingsPage/)。ADR-005 S4af）。共有ビューに出て、ブラウザ（`http://127.0.0.1:5107/web/settings`）でも同じ操作ができる。読み書きは `GET` / `PATCH /api/v1/settings*`。ネットワーク・言語・ホットキーはこの PC からだけ変えられる（LAN の端末はトークンがあっても断られる）。

### 一般（すぐ反映）
- 下品表現フィルター
- セットの自動更新を無効（デスクトップは起動のたびにオンに戻す）
- スコアの自動更新を無効
- ファイル出力を無効
- ローカル選手 DB の既存値を上書きしない
- チーム 1 / 2 の既定の色

### ネットワーク（再起動で反映）
- Web サーバーポート（既定 5107。1〜65535）。待ち受け中のポートも表示する
- LAN 公開（他デバイスからのアクセス許可）
- Web サーバーが起動できないとき（ポートが使用中など）はページを開けないので、`user_data/settings.json` の `general.webserver_port` を書き換えて再起動するか、ポートを使っているアプリを終了する

### 言語（再起動で反映。アプリは閉じない）
- Program / Game Asset / Tournament term の言語設定（§ 26）

### ホットキー（保存したらすぐ反映）
- 有効 / 無効
- 各アクションの割り当て（Ctrl / Alt / Shift とキーを選ぶ組み立て式）。別のアクションと同じキーは保存できない。OS・OBS と重なりがちなキー、修飾キーなし、Shift だけのキーは警告して保存する

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
[src/core/CountryCatalog.py](../../src/core/CountryCatalog.py)（データ。Qt の国モデルは削除済み）。
- ISO 国コード / 米国州コードの国旗マスタ

### 下品表現フィルター
[src/core/BadWordFilter.py](../../src/core/BadWordFilter.py)。
- タグ・本名の NG ワード警告・差し替え

### Twitch ユーザー名設定
- `SettingsManager.Get("twitch_username")` — Stats や Stream Queue のフィルタに利用

---

## 主要出力ファイル

| パス | 内容 |
|------|------|
| `out/program_state.json` | 全状態スナップショット |
| `out/*.txt` | OBS テキストソース向け個別ファイル |
| `out/thumbnails/*.{png,jpg}` | 生成済みの試合サムネイル |
| `out/stream/thumbnail_*.jpg` | 生成済みの配信サムネイル（30 世代） |
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
[web/src/BoardPage/](../../web/src/BoardPage/)（`/web/board`。ADR-005 S4s で React 化）。
- スコアボードのデザイン選択（`layout/` の組込み + Designer 製 `layout/custom/{slug}`。一覧と選択は `core/LayoutCatalog`）
- ライブプレビュー（OBS と同じページの iframe。ゲーム画面の絵の上に重ね、勝者ポップアップ WINNER / CHAMPION も重ねられる）
- 機能別カラー（`FeatureColorCard`、feature `board`）
- Designer 起動ボタン（デスクトップではホストが Designer のウィンドウを開く）
- OBS に指定するファイルのパスのコピーと、エクスプローラで開く（この PC からのみ）

### Designer
[web/src/ScoreboardDesigner/](../../web/src/ScoreboardDesigner/)・[src/AutonaDesignerWindow.py](../../src/AutonaDesignerWindow.py)・[src/AutonaDesignerService.py](../../src/AutonaDesignerService.py)。
- レイヤーエディタでスコアボードデザインを作成し `layout/custom/{slug}` に保存

### Figma 取込
[tools/figma-scoreboard-plugin/](../../tools/figma-scoreboard-plugin/)。
- Figma プラグインから Designer へデザインを取り込む。認証はペアリングコード方式

---

## 32. 統計表示設定
React の `web/src/StatsPreviewPage/`（`/web/statsPreview`。Stats Display Settings パネル。種類の表は [src/core/StatsOverlays.py](../../src/core/StatsOverlays.py)。ADR-005 S4l）。
- 統計オーバーレイに出す統計情報の種類を選択
- Companion マクロ（`stats:refresh` のリクエストとコピー。設定の手順は「ヘルプ＆設定 → API設定 → Companion の設定」）/ OBS への追加案内 / プレビュー・機能別カラー

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
