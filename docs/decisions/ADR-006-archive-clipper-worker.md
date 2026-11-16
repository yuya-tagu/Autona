# ADR-006: アーカイブ切り抜き（vodsplit を別プロセスのワーカーとして組み込む）

## Status

Accepted（2026-11-16 オーナー承認。末尾の論点 1・2 はどちらも推奨案 A を採用）

## Context

スマブラSPの大会配信アーカイブを、1 セット＝1 本のクリップに切り出すエンジン `vodsplit` を
別リポジトリ（`30_Products/SmashVodSplitter`）で作った。雷影杯#34（3 時間 40 分・720p）での成績は、
平均 IoU 0.929、処理 約 178 秒、ピークメモリ 約 320MB。これを Autona の「アーカイブ切り抜き」ページとして
組み込む（オーナー承認済み）。

**次の方針は承認済みで、この ADR では覆さない。**

- 区切りは映像解析で決める。試合の開始時刻（start.gg の startedAt / Hivee の started_at）には依存しない
- 結果データから使うのは、選手名・スコア・ラウンド・キャラだけ
- 映像からキャラを判定する方式は採らない
- キャラは次の順で取る: (1) 配信中のスコアボード記録（新設）、(2) 結果データに報告されたキャラ、
  (3) PlayerDB の mains（「推定」と表示）
- vodsplit は独立したライブラリのまま保ち、Autona からは別プロセスで動かす
- GameAssetCatalog・ThumbnailService・StartGGDataProvider・PlayerDB は Autona のものを使う
- 画面はモック（`SmashVodSplitter/docs/previews/app_mock.html` / `templates.html`）に従う

**2026-11-16 に調べて分かった事実（判断の根拠）:**

| 項目 | 事実 |
|------|------|
| Autona の配布 | `dependencies/autona.spec` は numpy を `excludes` に入れている。`_internal` は 344.4 MiB（zip 216.9 MiB）。`scripts/validate_release_zip.ps1` の上限は 390 MiB |
| vodsplit を単体の onedir にした場合（試作） | ビルド 30 秒。288MB（cv2 139MB・ffmpeg.exe 101MB・numpy 27MB ほか）。deno.exe（98MB）を足すと約 386MB、zip にすると **150MB** |
| 凍結した exe の動作 | 雷影杯#34 で `run --no-cut --progress json` が通った。OCR（winrt）の段も走り、結果は開発環境と同じ（10 セット・要確認 1）。キャッシュありの再実行は 8 秒 |
| 凍結すると動かないところ | `media.download` が `sys.executable -m yt_dlp` で yt-dlp を起動している（凍結すると `sys.executable` は exe 自身になる）。yt-dlp が使う deno は PATH から探している |
| **Autona の画面での動画再生** | PySide6 6.11 の QtWebEngine は **H.264 / AAC を再生できない**（`canPlayType` が "no"）。VP9・AV1・Opus は再生できる（"probably"） |
| デコード速度（720p・120 秒・`fps=0.5,scale=320:180`） | H.264 は 1.61 秒、VP9 は 2.61 秒（マルチスレッド時）。vodsplit の時間の大半は全編をデコードする段なので、VP9 にすると解析が 1.5〜1.6 倍になる見込み（雷影杯#34 で 約 3 分 → 約 4.5 分） |
| Web サーバー | Flask（werkzeug）。`send_file` / `send_from_directory` は `conditional=True` が既定なので、Range 要求には 206 で応える。v1 の各エンドポイントのアクセス区分は `WebAccess.ROUTE_ACCESS` に書く（区分のないものは書き込み扱い） |
| 試合の記録 | Autona には無い（ROADMAP §3-1-3「VOD チャプターマーカー記録」として未着手）。新しいセットの読み込みは `SetFollowService.LoadSet` で、`score.{N}.set_id` が変わる |
| start.gg | `StartGGDataProvider` は公開のサイト API（`www.start.gg/api/-/gql`）を**トークンなし**で使っている。ゲームごとのキャラ（`games.selections`）は取得しているが、パーサーはメインキャラを数えるのに使うだけ |
| ThumbnailService | core にあり Qt に依存しない。ただし入力は state 全体（`score.{board}`）で、任意の過去のセットを渡す口は無い。描画は WebEngine のオフスクリーン描画（`startImageRender`。デスクトップ専用） |
| 子プロセスの扱い | QProcess は使っていない。終了処理は `Window.closeEvent` と `HeadlessShell.Shutdown` の 2 か所 |

## Decision

### D1: ワーカーは CLI の子プロセスにする。進捗は `--progress json` の標準出力、中断は標準入力で伝える

- core の `ArchiveJobService`（新設・Qt を import しない）が `subprocess.Popen` でワーカーを起動する。
  `stdin=PIPE`、`stdout=PIPE`（JSON Lines）、`stderr=PIPE`（Autona の読み取りスレッドがジョブフォルダの `worker.log` に書く。
  親子が同じファイルに追記すると Windows では上書きし合うため）。
  `creationflags=CREATE_NO_WINDOW`
- 標準出力は専用の読み取りスレッドが 1 行ずつ JSON として読み、ジョブの進捗（段・fraction・残り時間・
  ログ・警告）をメモリに置く。JSON として読めない行は `worker.log` に回す
- **中断**: 標準入力に `cancel\n` を書いて閉じる。vodsplit 側は標準入力を見張るスレッドを持ち、
  `cancel` 行か EOF で `cancel.set()` する（V4）。Windows では CTRL_C を `CREATE_NO_WINDOW` の子に送れないので、
  シグナルは使わない
- **Autona が異常終了したとき**: パイプが閉じて EOF になるので、ワーカーは自分で中断する。保険として、
  ワーカーは `KILL_ON_JOB_CLOSE` の Windows ジョブオブジェクトに入れる（ctypes。孫プロセスの ffmpeg / yt-dlp も
  まとめて止まる）
- **Autona を終了するとき**: `ArchiveJobService.Shutdown(timeout=5)` を `Window.closeEvent` と
  `HeadlessShell.Shutdown` に足す。中断を送り、5 秒待っても終わらなければジョブオブジェクトごと終了させる。
  vodsplit のキャッシュは一時ファイルからのリネームで書くので、途中で止めても次の実行で続きから再開できる
- **終了の判定**: 終了コード（0 完了 / 2 利用者が直せる / 130 中断 / 1 想定外）と、最後の
  `done` / `error` / `cancelled` 行の両方を見る。行が無いまま終わったとき（強制終了・クラッシュ）は
  「想定外の終了」とし、`worker.log` の末尾を見せる
- 同時に動かすジョブは 1 つ（解析は CPU を使うので、配信中の PC で並列に走らせない）。ワーカーの
  優先度は `BELOW_NORMAL_PRIORITY_CLASS` にする
- ワーカーを探す順: 環境変数 `AUTONA_VODSPLIT_CMD`（開発用。例 `python -m vodsplit`）→
  `./vodsplit-worker/vodsplit-worker.exe`。起動時に `vodsplit-worker version` で版と sets.json の形式番号を確かめ、
  合わなければ使わない（V9）
- **ライブラリとして import する案（`api.run` をスレッドで呼ぶ）は採らない**。numpy / opencv を Autona の
  プロセスに入れると、配布の除外設定（numpy）と `_internal` の上限にぶつかり、解析中のメモリ 320MB と CPU が
  配信中の Autona と同じプロセスに乗る

### D2: 配布は別の実行ファイル（onedir）にし、Autona の zip とは別に配る【論点 1】

- SmashVodSplitter のリポジトリに PyInstaller の spec を置き、`vodsplit-worker/`（onedir。deno.exe・ffmpeg を含む）を
  ビルドする。Autona の spec には何も足さない（numpy の除外はそのまま）
- 配り方は**論点 1**。推奨は「Autona-releases に別のアセット `vodsplit-worker-windows.zip` として置き、
  ページから初回に入手する」。理由は次の 3 つ
  1. Autona の zip が 217MB から約 370MB になり、390 MiB の上限も超える。切り抜きを使わない配信者にも重さがかかる
  2. **yt-dlp は YouTube の仕様変更で頻繁に壊れ、そのたびに更新が要る**。Autona のリリースとは別の周期で
     ワーカーだけを更新できる
  3. ワーカーは別の Python ランタイムを持つので、Autona の依存とぶつからない
- 入手するときは SHA-256 を確かめ、Autona のフォルダの `vodsplit-worker/` に展開する
  （Autona のアップデーターと同じ置き場所の規則）
- **ライセンス**: imageio-ffmpeg が同梱する ffmpeg は GPL のビルドだが、別の実行ファイルとして同梱するだけ
  （mere aggregation）なので、Autona と vodsplit の MIT は変わらない。NOTICES に ffmpeg（GPL・ソースの入手先）・
  deno（MIT）・yt-dlp（Unlicense）・OpenCV（Apache-2.0）・numpy（BSD）を足す
- **ビルド時間への影響**: Autona のビルド時間は変わらない（ワーカーのビルドは 30 秒で、別に行う）

### D3: 配信中のスコアボード記録は core の `MatchLogService` が `state_updated` から作る

**置き場所と生成順**

- `src/core/MatchLogService.py`（Qt を import しない。module level では何も作らない）
- `bootstrap_core()` で `StateManager.Initialize()` の後に作り、`StateManager.observers.state_updated` に
  登録する（`ORDER CONSTRAINT:` コメントを足し、`tests/test_bootstrap_split.py` の AST の pin に入れる）。
  ヘッドレスでも記録する

**拾う変更（ボード 1 = 1on1 だけ。チーム戦・クルーは今回は対象外）**

| 変更 | 記録での扱い |
|------|-------------|
| `score.1.set_id` が変わる / 両チームの選手名が両方変わる / スコアが 0-0 に戻る | 前の記録を閉じ、新しい記録を始める |
| `score.1.team.{T}.score` の合計が 1 増える | 1 ゲームが終わったとみなし、その時点の両者のキャラを `games[]` に足す |
| 選手名・キャラ・`phase` / `match`・スコアの変化 | 開いている記録を更新する |

**held 通知と lock の制約（agent-pitfalls #16 / #17）との整合**

- `state_updated` のハンドラは `StateManager.lock` の中で走る。ハンドラでは delta を見てメモリ上の記録を
  更新するだけにし、ファイルの書き込みは単一の書き込みスレッド（キュー）に渡す。ハンドラの中では待たない・
  別スレッドを待たない・`GameAssetCatalog` の writer を呼ばない
- キャラのコードネームは delta にある `character.{K}` の dict（`en_name` など）から取る。カタログは読むだけ

**保存形式と保存場所**

- `user_data/match_log/YYYYMMDD.jsonl`（記録を始めた日のローカル日付）。記録が変わるたびに、記録全体を
  1 行追記する（同じ `id` は最後の行が正）。追記だけなので、途中で落ちても前の行は壊れない
- 1 行の形:

```json
{"id": "20261116-193012-1", "board": 1, "started_at": "2026-11-16T19:30:12+09:00",
 "updated_at": "2026-11-16T19:52:40+09:00", "closed": false, "tournament": "雷影杯#34",
 "set_id": "12345678", "round": "Winners Final", "p1": "Yuzha", "p2": "イシ", "score": [3, 1],
 "games": [{"c1": "pyra", "c2": "wolf", "at": "2026-11-16T19:36:01+09:00"}]}
```

- キャラはコードネームで持つ（表示名はページやタイトルを作るときに `GameAssetCatalog` の locale で引く）
- 古い記録は自動では消さない（1 大会で数十 KB）

**この記録は結果データとしても使う**

- 選手名・スコア・ラウンドがそろっているので、start.gg を使わない大会でも、記録から vodsplit の結果データを作れる
  （ジョブを作るときに「Autona の記録」を結果データとして選べる）

### D4: 記録の時刻は、どの記録を使うかを絞るためだけに使う。アーカイブとの時刻合わせはしない

- 区切りは映像解析で決まり、記録とセットの対応は（start.gg の結果と同じく）**選手名とスコア**で付く。
  時刻を合わせる必要は無い
- 時刻の用途は 2 つだけ
  - ジョブを作るとき、記録を日付（と大会名）で選ぶ
  - yt-dlp がアーカイブの配信開始時刻（`release_timestamp`）を返したときは、[開始, 開始＋長さ] に入る記録だけを
    候補にする（同じ日に同じ組み合わせが別の大会で当たったときの取り違えを防ぐ）。返さないときは日付だけで選ぶ
- アーカイブの頭がカットされている・配信が途中で切れているといった差は、上の使い方なら影響しない

### D5: 既存機能のつなぎ方（Autona のプロセスで行うこと / ワーカーで行うこと）

| 機能 | どちらで | 内容 |
|------|---------|------|
| 結果データ | Autona | start.gg は `StartGGDataProvider`（公開 API）で取得する（任意のイベントの完了済みセットと、ゲームごとのキャラを返すメソッドを足す）。記録（D3）・JSON ファイルからも作る。どれも vodsplit の結果 JSON に変換してジョブフォルダに置き、`--results` で渡す。vodsplit の `--startgg`（トークンが要る公式 API）は CLI 単体で使うときのためだけに残す |
| キャラ | Autona | 結果 JSON を作るとき、各セットに `c1` / `c2`（表示名。使った順に最大 2 体を「/」でつなぐ）・`games[].c1/c2`・`char_source`（`scoreboard` / `report` / `mains`）を入れる。優先順位は 記録 → start.gg の報告 → PlayerDB の mains。記録と結果のセットは、正規化した選手名（＋スコア）で対応づける。vodsplit は受け取ったキャラをタイトルに使い、sets.json にそのまま残す |
| PlayerDB | Autona | mains を引く（`char_source: "mains"`。画面では「推定」と表示） |
| GameAssetCatalog | Autona | コードネームから表示名・アイコン（`chara_2_<codename>_00.png`。既存の静的配信の範囲）を引く |
| タイトル・概要欄 | ワーカー | vodsplit のテンプレートのまま。テンプレートは Autona の `user_data/archive_templates/*.json` に置き、`--template` で渡す |
| サムネイル | 背景はワーカー、描画は Autona | 背景の候補フレーム（`_bg.jpg`）は vodsplit が選ぶ。描画は `ThumbnailService` で行う。任意のセットのデータ（state と同じ形の `score.1`）と背景を渡せる入口を足す。描画はデスクトップ専用（ヘッドレスは今と同じく 503） |
| 解析・切り出し・ダウンロード | ワーカー | vodsplit |

### D6: 画面は React の 1 ページ。ページの state はページのローカルとジョブフォルダに置き、StateManager には載せない

- ページは `archive`（ナビの「データ」セクション）。**ワーカーが見つからないときはナビに出さない**
  （入手の案内だけを出す）。機能フラグは作らない
- **StateManager に載せない**。state は `user_data/state.json` と OBS 向けの `out/` に出るもので、
  進捗のような頻繁に変わる値や、配信と関係のないジョブの情報を入れると、OBS への出力と保存を無駄に揺らす
  - ジョブの情報は `<作業フォルダ>/<job_id>/job.json`、レビューの編集（確定・割り当ての変更・IN/OUT の調整）は
    `review.json`。どちらも v1 で読み書きする
  - 進捗は実行中だけページが 1 秒ごとに `GET /api/v1/archive/jobs/<id>` で取得する（ソケットの新しいチャンネルは作らない）
- 作業フォルダ（動画は 1 本 3GB 程度になる）の既定は `%USERPROFILE%\Videos\Autona` とし、設定で変えられるようにする。
  `user_data/` には置かない（バックアップや更新の対象を膨らませないため）
- **部品**

| モックの部品 | 作り方 |
|-------------|--------|
| ヘッダーの集計（セット数・一致・要確認・ゲーム数） | 既存（MUI の Chip / Typography） |
| フィルタ（すべて / 一致 / 要確認） | 既存（ToggleButtonGroup） |
| セット一覧（幅 320px・サムネ 48×27px・選手名は 2 行で「…」・全文はツールチップ・2 行目に #番号・開始時刻リンク・試合時間・スコア・合わないときだけ赤字の「要確認」） | 既存の List を使う。行の部品は新規。件数は 1 本あたり数十なので仮想化は要らない |
| ジョブの作成・進捗・中断 | 既存（TextField・LinearProgress・Button） |
| 詳細（割り当ての変更・確定） | 既存（Select・Button）＋新規の行の部品 |
| 動画プレビュー（Space 再生・I / O で開始 / 終了・↑↓ でセット・N で次の要確認・Enter で確定して次へ） | **新規**。素の `<video>` を包む。ショートカットはページの中だけで有効にする |
| タイムライン（全体 / セットの 2 段。ゲーム区間・スコアボード補正・選手（OCR）。OCR の欄は初期表示で畳む） | **新規**。SVG で描く。ドラッグで IN / OUT を動かす |
| FHD で 1 画面に収める配置 | CSS grid（左 320px 一覧 / 中央プレビュー / 右詳細 / 下タイムライン） |

- npm の依存は足さない（動画は `<video>`、タイムラインは SVG で足りる）
- 区間の時刻は、どこに表示するときも YouTube の該当位置（`https://www.youtube.com/watch?v=<id>&t=<秒>s`）へのリンクにする
  （元がローカルファイルのときはリンクにしない）

### D7: 動画プレビューは v1 から Range 対応で配信する。YouTube からは VP9 / AV1 で取得する

- `GET /api/v1/archive/jobs/<job_id>/video`: `job.json` に記録した元動画だけを `send_file(path, conditional=True)` で返す。
  クライアントからパスは受け取らない。`job_id` は形式を検査し（`^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$`）、
  解決したパスが作業フォルダの下にあることを確かめる。背景フレームも同じ形で `/archive/jobs/<job_id>/frames/<no>`
- **アクセス区分**: 読み取り（status・ジョブの一覧と取得・動画・背景フレーム）は THIS_PC、書き込み（ジョブの作成・
  作業フォルダの変更・中断・再開）は SHELL（2026-11-16 A1 の検収で変更）。どちらも LAN の別の端末にはトークンがあっても開かない。
  書き込みを SHELL にしたのは、THIS_PC は同じオリジンの第三者製レイアウト（OBS のブラウザソース）も通すためで、
  任意の URL のダウンロード・作業フォルダの付け替えをレイアウトにさせない（ゲームアセットのジョブと同じ扱い）。
  このためヘッドレスと素のブラウザからはジョブを作れない（デスクトップの画面からだけ）。元動画の UNC パス（`\\host\share`）は断る
  （存在の確認で SMB 接続を起こさないため）
- **コーデック**: Autona の画面（QtWebEngine）は H.264 / AAC を再生できない（Context の実測）
  - YouTube から取得するときは、VP9 か AV1 の映像と Opus の音声を選ぶ（V3）。解析は 1.5 倍ほど遅くなる
    （約 3 分 → 約 4.5 分）が、プロキシ動画を作るより安い（3 時間半の VP9 エンコードは 10 分を超える）
  - 利用者がローカルの H.264 ファイル（OBS の録画など）を指定したときは、画面で再生できないことを検知し
    （`canPlayType` / `error` イベント）、「ブラウザで開く」を出す（同じページを既定のブラウザで開く。Chrome / Edge は
    H.264 を再生できる。この PC からのアクセスなので THIS_PC のままでよい）
  - 切り出したクリップはストリームコピーで mp4（VP9 / AV1 + Opus）にする。YouTube はそのまま受け付ける

### D8: 段階分け（1 段階 = 1 PR）

vodsplit 側の変更（下の「vodsplit 側の変更」）は、各段階の前に SmashVodSplitter のリポジトリで行う。

| 段階 | 内容 | vodsplit の前提 | 検証 | 規模の目安 |
|------|------|----------------|------|-----------|
| **A1 ジョブの実行と一覧** | `ArchiveWorker`（探す・版の確認・起動・ジョブオブジェクト）と `ArchiveJobService`（1 ジョブ・進捗・中断・再開・終了処理）、v1（status / jobs の一覧・作成・取得・中断）、ページ（元動画の URL かパス・結果データは JSON のアップロードか「なし」・進捗と中断・セット一覧（時刻リンク・要確認）・映像で見つからなかった結果）、ワーカーが無いときはナビに出さない | V4・V9 | pytest: 偽のワーカー（JSON 行を出し、標準入力の cancel に従う Python スクリプト）で、完了・利用者のエラー・中断・行の無い異常終了・Autona の終了時の後始末を確かめる。ルート台帳とアクセス区分のテスト。E2E: 偽のワーカーで、ジョブを作ってから一覧が出るまで。実機: 隔離データの `python main.py` で本物の vodsplit（開発環境）を雷影杯#34（キャッシュのコピー）で動かす | 大 |
| **A2 試合の記録** | `MatchLogService`（D3）。**ページより先にリリースに入れて、実際の大会で記録をためる** | なし | pytest: `LoadSet`・`SetScore`・`ResetScore`・キャラ変更の操作の並びから JSONL の中身を確かめる。held サイクルの中で待たないこと（`test_no_thread_is_waited_on_inside_a_held_cycle` の対象に入れる）。bootstrap の順の AST の pin | 中 |
| **A3 プレビューとタイムライン** | 動画と背景フレームの配信（D7）、プレビュー・タイムライン・詳細（読むだけ）・キーボード操作、H.264 のときの「ブラウザで開く」 | V3・V7 | pytest: Range で 206、範囲外のパス・不正な `job_id` が 404、THIS_PC の区分。E2E: ページの表示。実機: Autona の画面で VP9 が再生できること、シーク | 大 |
| **A4 レビューの編集と書き出し** | 確定・割り当ての変更・IN / OUT の調整を `review.json` に保存、「確定分を書き出し」（ワーカーの `export`） | V5 | pytest: `review.json` の読み書き、`export` の引数の組み立て。実機: 2 セットを確定して書き出し、クリップの区間を確かめる | 大 |
| **A5 キャラと結果の取得** | start.gg の任意のイベントの結果（ゲームごとのキャラ付き）、記録からの結果データ、キャラの合成（記録 → 報告 → mains「推定」）、一覧のキャラアイコン | V6 | pytest: 合成の優先順位、名前の正規化での対応づけ、start.gg の応答の変換（録画した応答で） | 中〜大 |
| **A6 テンプレートとサムネイル** | テンプレートのページ（`templates.html` のとおり）、`ThumbnailService` に任意のセットを渡す入口、`_bg.jpg` を背景にした描画 | なし | pytest: 入口の payload。実機: 3 セット分のサムネを描画 | 大 |
| **A7 ワーカーの配布** | ワーカーの spec とビルド、Autona-releases への公開、ページからの入手・更新（SHA-256 の検査・展開）、NOTICES、リリース Runbook の更新 | V1・V2・V8 | pytest: 入手処理（偽のサーバー・ハッシュ不一致・展開の中断）。実機: exe で入手から 1 本解析まで（オーナーの「exe で確認」の指示があるとき） | 中 |

- A7 までは、ワーカーの無い配布物ではナビに出ないので、A1〜A6 を途中でリリースしても利用者からは見えない
- YouTube への下書きアップロードは範囲外（必要になった時点で別の ADR にする）

### vodsplit 側の変更

| # | 変更 | 理由 | 段階 |
|---|------|------|------|
| V1 | yt-dlp を `sys.executable -m yt_dlp` ではなくライブラリ（`yt_dlp.YoutubeDL` と `progress_hooks`）として呼ぶ | 凍結した exe では `sys.executable` が exe 自身になる | A7（開発環境では今のままで動く） |
| V2 | deno の場所を明示する（exe の隣の `deno.exe` を yt-dlp の JS ランタイムに渡す） | 配布先の PC の PATH には deno が無い | A7 |
| V3 | YouTube の取得で VP9 / AV1 + Opus を優先する（`--codec open|any`、既定は open）。切り出しは mp4 へのストリームコピーのまま | Autona の画面は H.264 を再生できない | A3 |
| V4 | `--cancel-on-stdin`: 標準入力の `cancel` 行か EOF で中断する | Windows で GUI の子プロセスに中断を伝える手段。Autona の異常終了でも止まる | A1 |
| V5 | `export` サブコマンド: 編集済みのセット（区間・タイトル・概要欄）を受け取り、そのセットだけを切り出す | レビューで確定したものだけを書き出す | A4 |
| V6 | 結果 JSON の任意の項目 `c1` / `c2` / `games[].c1/c2` / `char_source` / `id` を検査し、sets.json の `set` にそのまま残すことをテストで固定する（今もタイトルの `{c1}` / `{c2}` には使われ、`set` にも残る） | キャラの受け口 | A5 |
| V7 | sets.json に `format`（形式番号）と、ゲームごとの OCR の名前と出現率（`games_ocr`）を足す | 版の確認と、タイムラインの「選手（OCR）」の欄 | A3 |
| V8 | ワーカーの PyInstaller spec（`packaging/worker.spec`）。cv2 の videoio の ffmpeg DLL が 2 つ入るので 1 つにする | 配布 | A7 |
| V9 | `version` サブコマンド: `{"version": "0.2.0", "format": 1}` を出す | Autona が使える版かを確かめる | A1 |

どれも既存のテスト（`python -m pytest -q tests`）を壊さず、変更ごとにテストを足す。

## Alternatives considered

- **ライブラリとして Autona のプロセスで動かす**: 起動が軽く、進捗をコールバックで受け取れる。
  しかし numpy / opencv が Autona の配布の除外設定と上限にぶつかり、解析の CPU・メモリが配信中の Autona と
  同じプロセスに乗る。承認済みの方針（別プロセス）にも反する。不採用
- **Autona の exe を `--vodsplit-worker` で起動し直す（同じ配布物に同居させる）**: 実行ファイルは 1 つで済むが、
  numpy / opencv を Autona の `_internal` に入れることになり、上の問題が全部残る。不採用
- **通信をローカルの HTTP / ソケットにする**: 双方向で拡張しやすいが、ポートの確保と認証が増える。
  1 ジョブ 1 プロセスなら標準入出力で足りる。不採用
- **QProcess で起動する**: Qt に依存し、ヘッドレス（QCoreApplication）や core のテストから使いにくい。
  core は Qt を import しない方針（`tests/test_core_qt_free.py`）。不採用
- **進捗やジョブを StateManager に載せる**: 既存のソケット配信に乗れるが、`state.json` と OBS 向けの出力を
  無駄に揺らす。不採用（D6）
- **H.264 のまま取得し、プレビュー用の低解像度の VP9 を作る**: 3 時間半の VP9 エンコードに 10 分以上かかる。
  取得する形式を変える方が安い。不採用（D7）
- **試合の記録の時刻とアーカイブの時刻を合わせて区切りに使う**: 承認済みの方針（区切りは映像解析）に反し、
  配信の開始時刻のずれに弱い。不採用（D4）

## Consequences

**良い影響:**

- 配信中の Autona に解析の負荷がかからない。ワーカーが落ちても Autona は落ちない
- Autona の配布サイズとビルド時間は変わらない。ワーカー（yt-dlp）だけを短い周期で更新できる
- 試合の記録は、start.gg を使わない大会でも結果データになり、ROADMAP の「VOD チャプターマーカー記録」の
  土台にもなる

**受け入れるリスク・コスト:**

- 配布物が 2 つになる（ワーカーの版の確認・入手・更新の処理と、リリース手順が増える）
- YouTube から VP9 / AV1 で取るので、解析が 1.5 倍ほど遅くなる（雷影杯#34 で約 3 分 → 約 4.5 分の見込み）
- ローカルの H.264 ファイルは Autona の画面ではプレビューできない（ブラウザで開く）
- start.gg は非公式の公開 API に頼る（Autona の既存機能と同じリスク）
- ワーカーの配布物に GPL の ffmpeg（別の実行ファイル）が入る。NOTICES とソースの入手先の記載が要る

## Revisit conditions

- PySide6 の QtWebEngine が H.264 を再生できるようになったら、D7 の取得形式を H.264 に戻す（解析が速くなる）
- WebView2 への移行（ADR-005 の Revisit）を決めたら、D7 を見直す（WebView2 は H.264 を再生できる）
- チーム戦・クルーのアーカイブを切り抜く要望が出たら、D3 の記録の対象を広げる
- yt-dlp の更新頻度が下がり、ワーカーの別配布の手間が見合わなくなったら、D2 の配布形態を見直す

## オーナーの判断が要った論点（2026-11-16 どちらも A で決定）

1. **ワーカーの配り方**（D2）
   - **A（推奨）: 別のアセットとして置き、ページから初回に入手する**。Autona の zip は今のまま（217MB）。
     yt-dlp が壊れたときにワーカーだけを更新できる。入手の処理（ハッシュの検査・展開）を作る手間がかかる
   - B: Autona の zip に同梱する。入手の処理は要らないが、zip が約 370MB になり（390 MiB の上限を上げる）、
     yt-dlp の修正にも Autona のリリースが要る
2. **start.gg の結果の取得方法**（D5）
   - **A（推奨）: Autona の既存の公開 API（トークンなし）で取る**。Autona はすでにこの API に頼っており、
     運営の操作も認証も要らない。この切り抜きのために Hivee の OAuth 中継を待つ必要が無くなる
   - B: 公式 API（OAuth。Hivee の中継 API が要る）で取る。公式で安定しているが、Hivee 側の作業が終わるまで
     start.gg からの取得が使えない（その間は JSON ファイルと記録で使う）

`docs/decisions/README.md` の一覧への追加と ROADMAP の更新（§3-1-3 の記録を本 ADR の A2 に、冒頭の「YouTube API 連携は
検討外」に下書きアップロードの例外を明記）は A1 の PR に含める。
