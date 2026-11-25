# ADR-009: ワーカーのプラグイン化（第三者がジョブ・アクション・フックを持ち寄る）

## Status

Accepted（2026-11-26 オーナー承認。末尾の論点 1〜3 はすべて推奨案 A を採用）

ADR-007 のうち「第三者の実行ファイルを Autona が起動する汎用のワーカー登録は作らない」の部分だけを置き換える（ADR-007 のほかの決定は変えない）。

README の規則に従い ADR-007 の本文は直さない。README の一覧の ADR-007 の Status 欄を「Accepted（一部 Superseded by ADR-009）」にした。

## Context

### 依頼

「ワーカーの仕様を精緻化する。ゲームのプラグインのように、他の開発者も機能を持ち寄って追加できるようにしたい。」

### ADR-007 が退けた案を、いま見直す理由

ADR-007 は汎用のワーカー機構を「任意の exe を起動し、ページも要るので実質プラグイン機構になる。入口は v1 API の外部ツールで足りる」として退け、
見直しの条件を「2 つ目の実ワーカー」に置いた。今回はオーナーが一般化そのものを望んだので、条件を待たない。ただし退けた理由は今も正しいので、この ADR はそれぞれに答える。

- 「任意の exe を起動する」→ その通りで、隔離はしない。守れる物と守れない物を 3 列に分け、画面と文書に同じ言葉で書く（D6）。これを受け入れるかは論点 2
- 「ページも要る」→ プラグインにページを足させない。宣言から描く汎用ページ 1 枚と、既存ページへのボタンの差し込みだけ（D4）
- 「外部ツールで足りる」→ 外部ツールは Autona の画面に出ず、導入も止め方も利用者任せ。「持ち寄る」には、見える場所・一手で入る経路・作者の名前が出る場所が要る（D4・D7）

### 判断を支える事実（コードで確かめたもの）

| 項目 | 事実 | 場所 |
|------|------|------|
| 今のワーカー契約 | 1 ジョブ = 1 プロセス。入力はファイルと引数、出力は stdout の JSON Lines（`stage` `progress` `log` `warning` と終端の `done` `error` `cancelled`）。知らない `type`・JSON でない行は `worker.log`。終端行は最後の 1 つを採る | `src/core/ArchiveJobService.py` `_OnLine` |
| 終了判定 | 「exit 0 かつ `done`」→「exit 130 か `cancelled`」→「exit 2 かつ `error`」→「中止の依頼の後で終端行なし」→ それ以外は `failed` の順 | 同 `_EndState` |
| 子の環境 | `dict(os.environ)` の全量に `PYTHONIOENCODING` と `PYINSTALLER_RESET_ENVIRONMENT` を足す。`cwd` の指定なし。Autona が自分の環境に足すのは `QT_API` だけ | `src/core/ArchiveWorker.py`、`main.py` |
| ジョブオブジェクト | `Popen` の**後に** `AssignProcessToJobObject`（その間に生まれた孫は漏れうる）。制限は `KILL_ON_JOB_CLOSE` だけ。`PeakJobMemoryUsed` は未使用 | `ArchiveWorker.py` |
| この PC の書き込み | 転送ヘッダが無く `Host` が自分で、`Sec-Fetch-Site` と `Origin` の無い要求（ブラウザでないローカルのプロセス）は**トークン無しで WRITE が通る** | `src/core/WebAccess.py` `_AmbientRefusal` |
| SHELL | THIS_PC＋`same-origin`＋メモリだけの秘密。秘密のヘッダが付くのは共有ビューの fetch / XHR だけで、`<img>`・ナビゲーションには付かない。ヘッドレスでは常に 403 | 同 `ShellRefusalReason`、`src/AutonaWebPanelHost.py` |
| ページの登録 | `WEB_PANELS`・`registry.tsx` の `PANELS`・`navSections.json` の 3 点がビルド時に固定 | `WebAccess.py`、`web/src/…/registry.tsx`、`navSections.json` |
| 試合の記録 | ボード 1 の 1on1 だけ。読み込んだセットに最初からあるスコアは baseline（ゲームに数えない）。記録が閉じるのは次の試合の始まりかシェルの終了で、**最後のゲームの直後には閉じない**。observer は `StateManager.lock` の中で書き込みスレッドへ積むだけ。`best_of` は読んでいない | `src/core/MatchLogService.py` |

### この事実から出る前提

1. 同じ利用者の権限で動く exe に対して、Autona はファイル・ネットワーク・v1 への接続のどれも強制できない。プラグインは `settings.json`（LAN トークン入り）を読め、
   トークン無しで v1 に書け、Run キーやタスクで Autona の外に居座れる。**悪意に対して効くのは「起動する前」の守り（何を・いつ起動するか）だけ**で、
   実行中の制限は行儀の良いプラグインの事故を防ぐ物にすぎない
2. プラグイン用にスコープ付きのトークンを配っても、プラグインは配らない v1 を素で叩ける。守りにならない
3. 今の契約（ファイルで受け、JSON Lines で返す 1 回きりの子プロセス）は一般化に向く。重さの大半は UI（ページの登録が静的）と配布（定数が 1 つのワーカー前提）にある

## Decision

### 決めたこと

1. プラグインが持ち寄れるのは**ジョブ**（入力から出力を作って終わる 1 回の処理）だけ。起動のしかたは手動・アクション・フックの 3 つ（D1）
2. 契約は今の vodsplit の JSON Lines に「Worker Protocol 1」と名前を付けて固定し、`describe`・`request.json`・成果物だけを足す（D2・付録 A）
3. フォームと入力は独自の項目の配列で宣言する。ページはプラグインに描かせず、汎用ページ 1 枚と既存 3 ページへの「プラグイン ▾」の差し込みだけ（D3・D4）
4. フックは 2 つの契機（試合が決まった・リザルト画像を保存した）から。既定は無効で、軽い枠・短い制限時間・回数の上限・自動無効をホストが強制する（D5・付録 D）
5. 隔離はしない。守りは「同意しない物・遮断した物は起動しない」「入口は SHELL だけ」という起動の前の守りで、実行中の制限は事故対策と書き分ける（D6）
6. 配布は作者ごとの GitHub リリース。アプリの「探す」に出す掲載リストと遮断リストを公開リポジトリの JSON 1 枚で持ち、更新元はリポジトリの数値 id で固定する（D7・付録 C）
7. vodsplit は動かさずに「専用ページを持つ自社のプラグイン」として同じ実行エンジンに載せる（D9）
8. 段階は P1〜P7（P5 は a / b に分ける）。OSS 公開は P5b の後（D10・論点 3）

### D1: スコープ — 持ち寄れるのは「ジョブ」と、その起動のしかた 3 つ

| 起動のしかた | 誰が起動するか | どこに出るか |
|------------|------------|------------|
| 手動 | 利用者 | 汎用ページ「プラグイン」のフォーム |
| アクション | 利用者 | 既存ページの成果物の横のメニュー（リザルト画像・サムネイル・切り抜きの書き出し） |
| フック | ホスト（利用者が有効にした契機だけ） | 契機は 2 つ（D5）。既定は無効 |

| 入れる | 入れない（理由） |
|-------|---------------|
| ジョブ・項目の配列のフォーム・設定・`secret`・文言（ja / en）・アイコンと作者名 | 常駐サービス（死活監視・再起動・終了順が要る）／独自のページ・ナビ・iframe（第三者の HTML を共有ビューのオリジンに入れられない: Design.md 4.2.3）／v1 のルート・state のキー・`out/` への寄与（`ROUTE_ACCESS` と `KEY_CATALOG` は静的な正本）／プロセス内の Python（1 つの例外で配信が止まる）／プラグイン同士の連携（ほかのプラグインの成果物を受ける・依存する）／結果の取り込み元（provider）／テンプレートの同梱 |

見た目はテンプレートパッケージ（ADR-007 / 008）、常駐・秒単位の連動は v1 API の外部ツール（ADR-001）のまま。

**プラグインにする物の基準**（`docs/plugins/` に載せる）: 重い依存・解析の負荷・本体と違う更新周期（ADR-006 が vodsplit を分けた理由）、本体の方針では持たない連携、
「Autona のデータを入力にして Autona の画面から押したい」処理。**軽くて全員が使う物は本体に入れる**（試合ログの CSV など）。

**ROADMAP との関係**:
- 6 行の「Discord 連携は検討外」は、本体が持ち、追従を約束する機能の話と読む。入れた人だけが使う別配布のプラグインとは層が違うので両立する。ROADMAP に「本体では」と限定する一文を足すかは論点 1
- 7 行の「切り抜きの YouTube への下書きアップロード」は本体の別 ADR。入力源 `archive_export` の形を固めるとき（P3）は、その ADR の要件（クリップの題・説明・チャプター）を先に見る
- 34・161 行の parry.gg は本体の取り込み元になる予定で、既存データとの合わせ方の設計が別に要る。provider をこの ADR に入れない理由

### D2: 契約は Worker Protocol 1 — 今の JSON Lines に名前を付けて固定する

全文は付録 A。決め方の要点:

- 起動は `<entry> describe` と `<entry> run <request.json のパス>` の 2 つ。利用者の入力は引数に入れず、全部 `request.json` に書く。`secret` だけは環境変数で渡す
- `describe` は「起動できるか・話せる版は何か」を確かめる probe で、導入・更新・開発中の「読み直す」の時だけ呼ぶ。結果はキャッシュせず、ページを開いても呼ばない。宣言の正本はマニフェスト
- `done` に `summary` と `artifacts`（成果物）を足す
- stdin は正常時にジョブの終わりまで開いたまま。ホストが閉じる（EOF）のは中止の時だけ
- 終了判定は今の `_EndState` の順のまま全組み合わせを表にし、その前にホストの印「制限時間切れ（`timed_out`）」を見る
- 環境変数は**引き算**: 親の環境から `AUTONA_*` と Autona が自分のために足した `QT_API` を外し、契約の変数を足す。
  許可リストにしないのは、利用者の永続の環境変数は同じ権限の exe ならレジストリから読めて隠す意味が薄く、欠けた名前による壊れ方は環境ごとに違って再現しにくいから
- JSON-RPC・MCP・双方向の問い合わせ・ソケットは使わない（Alternatives）

vodsplit との互換は**イベント・stdin・終了コードの層**の互換。起動の形（専用の引数）・`done` の中身・`--cancel-on-stdin` は vodsplit 専用のまま（D9）。

### D3: マニフェスト `plugin.json` と項目の配列

プラグインのフォルダ直下の `plugin.json`（言語を問わない独立したファイル）。全項目と型は付録 B。

- **フォームは独自の項目の配列**（JSON Schema の方言とは名乗らない）。入力ファイル（入力源）も同じ配列の型で書く。同意の画面に出す「渡すデータ」もこの配列から作る
- アクション・フックが**どの項目を埋めるかは型で決まる**（付録 B.4）。ジョブの起動のしかたはどれも、そのジョブの入力源の項目を全部埋められなければならない（埋められない組み合わせは検査で落とす。結果としてリザルト画像のアクションと試合のフックは別のジョブになる）
- 入力源は `match_log` `match_record` `state` `result_image` `thumbnail_image` `archive_export` の 6 つ。中身は付録 B.5 に欄の表と例で書き、golden のテストで固定する
- `timeout_seconds` はジョブごとの宣言をホストが強制する。フックで起動したときは min(宣言, 30 秒)
- `entry` は**フォルダの中の exe** を基本にする。`@` で始まる先頭の語は将来の予約（本体同梱の実行環境 `["@python", "main.py"]` の余地）で、今は受けない
- 文言は全部「文字列か、言語 → 文字列の dict」の 1 種類

### D4: UI と開発者の入口

**汎用ページ「プラグイン」**: `plugins` を `WEB_PANELS`・`registry.tsx`・`navSections.json`（見出し `app_data` の末尾）に**静的に 1 回だけ**登録する。プラグインが増えても 3 点は変わらない。

- 一覧（アイコン・名前・作者・版・状態・印「掲載 / 手動 / 開発中」・サイズ）。選ぶと設定・ジョブのフォームと実行・フックの有効化と履歴・過去のジョブ・申告と渡すデータ・homepage と funding のリンク
- **ジョブのログ**: 各ジョブの stdout の知らない行と stderr（`worker.log` の末尾）をページで見られる。作者が最初に頼る画面
- 実行中の進捗は今の `ArchivePage` の部品を共通にして使い、1 秒ごとにジョブの状態を読む（StateManager に載せない。ADR-006 D6 の理由）
- vodsplit は一覧に出し、「開く」で今の `archive` ページへ移る

**アクションの差し込み**: リザルト・サムネイル・アーカイブの 3 ページが、アクションの一覧を読んで成果物の横に「プラグイン ▾」を出す。
押すと小さなダイアログでフォームを開き、文脈の入力は埋めてある。共有ビューの外（ブラウザで開いた Web UI）ではメニューを出さない。画像がまだ無い時は無効。

**ルート**: `/api/v1/plugins…` の固定の数本（プラグインごとのルートは作らない。ApiCatalog に載せない）。

| ルート | 区分 | 中身 |
|-------|------|------|
| `GET /api/v1/plugins` | THIS_PC | 一覧。欄は `id` `name` `author` `version` `state`（`ready` / `disabled` / `blocked` / `needs_settings` / `incompatible` / `running` の列挙だけ）`badge`（`listed` / `manual` / `dev` / `builtin`）`size_bytes` `has_icon` に限る。理由の本文・エラー・パス・履歴は載せない |
| `GET /api/v1/plugins/{id}/icon` | THIS_PC | マニフェストの `icon`。ホストが png の署名と 256 KiB を確かめ、`Content-Type: image/png`・`nosniff` で返す。`<img>` でそのまま出せる |
| それ以外（詳細・設定・`secret` の保存・実行・中止・ジョブの状態・ログ・成果物・フックの有効化と履歴・導入・更新の確認・削除・掲載の読み込み） | SHELL | 選手名・パス・作者が書いたエラーを含む |

- 一覧を THIS_PC にするのは、ブラウザで開いた Web UI とヘッドレスでも「何が入っているか」を見られるようにするため（問い合わせの時に役立つ）。中身は id と列挙だけなので、同じオリジンの `layout/` が読んでも漏れる物が無い
- それ以外を SHELL にするのは、既存の archive の読み取り（THIS_PC）より厳しい。archive の出力は中身を本体が決めているが、プラグインの出力・ログ・エラーは第三者が決めるため
- 成果物の画像は `<img src>` で直接出さない（SHELL の秘密が付かず 403 になる）。ページが fetch で取って blob URL にして出す
- シェル要求に 2 つ足す。`pickFile`（`file` 型の項目。拡張子の絞り込みつき）と、`openPluginLink`（下）

**`openPluginLink`**: 引数は `{"source": "installed" | "listing", "id": …, "link": "homepage" | "funding" | "repo"}`。ホストが URL を組んで既定のブラウザで開く。
導入済みはマニフェストの `homepage` / `funding`、掲載の行（まだ入れていない物・`kind: template` / `tool` の行も）は手元の掲載の写しの `repo` から `https://github.com/<repo>` を組む。
URL は `https://` だけ・userinfo なし・2,048 字までを確かめる。ページは URL を渡さないので、今の `openExternal` の「URL を受けない」規則を保てる。

**開発者の入口**:
- プラグインのページの「開発者向け」のトグルの下に「開発中のフォルダを追加」（SHELL・`pickFolder`）。トグルとフォルダの一覧は `user_data\plugins\dev.json`（v1 の設定ルートに出さないので LAN の WRITE からは届かない）。環境変数 `AUTONA_PLUGIN_DEV`（`;` 区切りで複数）も読む
- トグルを入れる時と、フォルダを足す時に確認を出す。文は D6 の固定文に「開発中のフォルダのプログラムは、ハッシュの確認も同意もなしで動きます。自分で書いた物だけを足してください」を足した物
- **導入済みと同じ id の開発中フォルダは断る**（「同じ id のプラグインが入っています。先に削除するか、id を変えてください」）。逆に、開発中と同じ id の導入も断る。開発中の状態（設定・`secret`・フック）は `user_data\plugins\_dev\<id>\` に分け、導入済みとは何も共有しない
- 開発中のプラグインは一覧で名前より前に「開発中」と出す。ハッシュと同意を飛ばし、`entry` に任意のコマンド（`["python", "main.py"]`）を許し、遮断の対象外。フックの行に「サンプルの入力で試す」を出し、終わったジョブの `inputs\` を残す（再現用）
- SDK・雛形・文書は P4 で用意する（D10）

### D5: フック — 契機 2 つと、ホストが強制する条件

**同じ ADR に入れる理由**: 「試合が決まったら投稿」のような連携は、押さずに走ることに価値の大半がある。アクションだけで出すと作者はそれを作れず、v1 を常駐で見張る外部ツールへ流れ、Autona の守り（同意・遮断・資源）の外に出る。いちばん大きいリスクを取る判断なので、条件を先に全部決めてから最後の段階（P7）で入れる。

| 契機 | いつ出るか | 埋める型 |
|------|----------|---------|
| `match_decided` | ボード 1 の 1on1 の記録で、試合の勝ちが決まった時（厳密な条件は付録 D.1）。記録 1 件につき 1 回 | `match_record` |
| `result_image_saved` | リザルト画像を 1 枚 `out/results` に保存するたびに 1 回 | `result_image` |

契機の名前は増やせる形にしておく（将来の候補: 切り抜きの書き出しが終わった時。Revisit）。知らない契機のフックを持つジョブは「この Autona では使えない」と理由付きで出す。

「記録が閉じた時」を契機にしないのは、記録が閉じるのが次の試合の始まりで、その日の最後の試合（グランドファイナル）はシェルの終了でしか閉じず、終了時はフックを走らせないので**いちばん大事な試合が投稿されない**から。

ホストが強制する条件（数字と手順は付録 D）: 既定は**無効**で、利用者がフックごとに有効にする（SHELL・デスクトップだけ）。有効にする時に、そのフックで使うフォームの値を利用者が決めて保存し、フックはその値で走る。
**軽い枠だけ**で走り、制限時間は min(宣言, 30 秒)。フックごとに**最新 1 件だけ保留**する。回数は 1 分と 1 時間の 2 つの窓で数え、1 時間の上限を超えたら、また失敗が 5 回続いたら**自動で無効**にする。ヘッドレスでは走らせない（有効化も失敗の確認も止める手も SHELL にしか無い）。

**受け入れるリスク**: WRITE を持つ相手（LAN のクライアント・同じオリジンの `layout/`・ローカルの外部ツール）がスコアを書けば `match_decided` を起こせ、選手名などの中身も決められる。
走るのは利用者が有効にした同意済みの exe だけで、既定無効・回数の上限・資源の上限・自動無効で穴の大きさを抑える。作者向けの文書に「入力の文字列は信頼しない」（選手名に `@everyone` を入れられる）と書き、公式の Webhook プラグインはメンションを無効にして見本を示す。

### D6: 守りの線引きと資源

**守りの表**（文書・同意の画面・`docs/plugins/security.md` に同じ言葉で書く）

| 悪意にも効く | 事故・暴走を防ぐ（善意の作者向け） | 作者の申告（強制しない） |
|------------|------------------------------|---------------------|
| 同意しない物は起動しない | ジョブオブジェクトの寿命（**直接の子孫だけ**。WMI・タスク スケジューラ・サービス・COM 経由で起動した物は外れる） | ファイルの読み書きの範囲 |
| 遮断した物は Autona から起動しない（D7） | 軽い枠のメモリ上限（コミットが失敗するだけで、止めはしない） | ネットワークの相手 |
| 導入時の SHA-256 とサイズ（ダウンロードが壊れていない・取り違えていない。**出所の証明ではない**）・zip の検査 | 制限時間・優先度の上限・2 レーン | 別のプログラムの起動 |
| 更新元を GitHub の数値 id で固定・配布元が違えば新規の導入・`taguyan.` の予約（D7） | フックの回数上限と自動無効 | v1 を叩くかどうか |
| 実行・導入・設定の入口が SHELL だけ | cwd・環境変数の引き算 | |
| 受け取る出力の検査（Autona のルートが任意のファイルを配らない。プラグインへの守りではない） | `secret` を平文でディスク・ログ・`request.json` に散らさない（DPAPI。**プラグイン間の分離は無い**: 同じ利用者のどのプロセスも復号できる） | |

- 起動ごとのハッシュ確認はしない（onedir の `_internal` を見ないので見かけだけ。フォルダに書ける相手は既に利用者と同じ権限）
- OS の隔離（AppContainer・制限付きトークン）・WASM・署名の検証はしない（Alternatives）
- 今の Autona に秘密ストアは無い（LAN トークンは `settings.json` に平文）。`secret` の DPAPI はこの ADR で足す
- 同意の固定文: 「プラグインは、この PC であなたと同じ権限で動くプログラムです。Autona は中身を検査していません。Autona の設定（LAN のトークン・start.gg のトークン）、
  ほかのプラグインの秘密、ほかのファイルを読めます。一度でも動かしたプラグインは、Autona の外で動き続ける仕掛けを置けます。信頼できる配布元のものだけを入れてください。」

**資源**（実装の制約は付録 E）

| 項目 | 決めたこと |
|------|----------|
| 起動 | `CREATE_SUSPENDED` で作り → ジョブオブジェクトに入れ → 再開する。孫が生まれる前にジョブに入る。`CREATE_NO_WINDOW`・シェルを通さない |
| レーン | **重い枠 1 ＋ 軽い枠 1**。重い枠は vodsplit と `lane: heavy`、軽い枠は `lane: light`・アクション・フック。埋まった枠への手動・アクションの要求は `busy`（「実行中のジョブが終わってから押してください」と残り時間）。待ち行列なし（フックの保留は付録 D）。自動の再実行なし |
| 優先度 | ジョブに `JOB_OBJECT_LIMIT_PRIORITY_CLASS`（BELOW_NORMAL）を掛け、子孫全部に効かせる。中止の前はジョブの値を NORMAL に書き換える（pid を列挙しない。ADR-006 の 81 秒の教訓）。`describe` は NORMAL |
| 制限時間 | 宣言を強制（軽い枠は 300 秒まで、重い枠は 43,200 秒まで、フックは min(宣言, 30)）。過ぎたら `timed_out` の印を付けて `cancel` を送り、10 秒で終わらなければジョブごと止める。vodsplit は上限なし（今のまま） |
| メモリ | 軽い枠だけ `memory_mb` を `JOB_OBJECT_LIMIT_JOB_MEMORY` で上限にする。意味は「暴走したプロセスがコミットの失敗で自分で落ちる」。重い枠は上限を掛けずピークを記録して一覧に出す（正しい動画処理が上限で落ちると、原因が利用者に見えない） |

### D7: 配布・掲載・遮断

**掲載リストを作る理由**: ゲームの MOD 文化で人が集まる理由の中心は「見つけられる・一手で入る・作者の名前が出る」こと。遮断リストと同じファイルにすれば、追加の運用はほとんど増えない。手順と数字は付録 C。

- **入れ方は 3 経路**: 掲載リストから（印「掲載」）・手動の zip（「手動」。強い警告付き）・開発中のフォルダ（「開発中」）。置き場所は `Autona.exe` の隣の `plugins\<id>\`（本体の更新は `_internal` だけを `/MIR` するので消えない）
- **掲載リスト**: `yuya-tagu/Autona-releases`（公開）に `plugins.json` を 1 枚（掲載 `listed` と遮断 `blocked` を同じファイルで）。追加・遮断は PR。
  掲載で見るのは機械的な条件（リポジトリが公開・リリースに `autona-plugin.json` と zip・LICENSE・id の重複なし）だけで、**1 プラグイン 1 回**。版ごとの審査はしない。画面に「掲載は安全の保証ではありません」
- **配布元は「Autona が自分でダウンロードした GitHub のリポジトリ」だけを数値 id で記録する**（`owner/repo` の文字列は表示用）。改名・移管は黙って追い、古い名前を他人が取り直しても id が違うので止まる
- 手動の zip の配布元は常に「手動」。zip の中の `update.github` は自己申告なので配布元の証明に使わない。**その zip の SHA-256 が、記録済みのリポジトリの最新リリースの `autona-plugin.json` と一致した時だけ**、そのリポジトリから来た物と同じに扱う
- **配布元が同じと認められない導入は全部「新規の導入」**（相手が掲載でも手動でも、手動から手動でも）: `secret` を消し、フックを全部無効にし、同意をやり直す。別人の zip が元のプラグインの Webhook の URL と有効なフックを受け継がない
- **`taguyan.` で始まる id はホストが予約を強制**: 本体の定数にある公式のリポジトリ id から来た物（ダウンロードか、上の SHA-256 の一致）と、開発中のフォルダだけ通す
- **遮断リスト**: 組み込み以外のプラグインが 1 つでも入っていれば、起動後にバックグラウンドで取得し、以後 6 時間ごとに取り直す（大会の日は 1 回の起動が長いため）。起動は待たせない。
  失敗したら前回の写し、それも無ければ本体のリリースに同梱した写し。遮断に一致した物は**何も起動しない**（手動・アクション・フック・`describe`）
- **更新の確認**: プラグインのページを開いた時と「更新を確認」を押した時だけ、前回から 6 時間以上空いていれば。API を使わずリリースの `autona-plugin.json` を直接読む（GitHub API の回数制限を本体・vodsplit の確認と取り合わない）。自動では入れない
- **再同意**: 同意に出す項目（申告・入力源の型・`secret` の名前・フック・配布元）を正規化した 1 つのダイジェストが変わったら再同意。それまでは旧版のまま動く

### D8: 互換性

| 対象 | 版 | 上げる条件 | 古い版の扱い |
|------|----|----------|------------|
| Worker Protocol | 整数（`protocol`） | 項目の削除・意味の変更・必須の追加（イベントや任意項目の追加では上げない） | 直前の版を最低 1 年話す |
| マニフェスト | 整数（`manifest`） | 同上（項目の型・契機の追加では上げない。知らない型・契機のジョブは理由付きで出さない） | 同上 |
| 入力源の形 | 入力源ごとの整数（`format`） | 同上（欄の追加では上げない） | 直前の版を書き出す口を 1 年残す |

- **プレビュー期間**: 外部の実プラグインが 1 つ掲載されるまで、3 つとも「プレビュー」。1 年の約束は最初の掲載の日から数える。プレビュー中でも壊す変更は CHANGELOG とプラグインのカードで知らせる
- 版を落とすのは本体のメジャー版だけ。半年前から、その版だけを話すプラグインのカードに予告を出す
- 両側とも、知らないキー・知らないイベントは無視する
- 正本は機械可読の `docs/plugins/contract_1.json`（イベント・項目の型・契機・入力源の `format`・外す環境変数）と `plugin.schema.json`・入力源の golden。pytest が実エンジン・SDK の偽ホスト・偽ワーカーをこれに突き合わせる（ADR-008 の `overlay_contract.json` と同じやり方）

### D9: vodsplit の扱い

- vodsplit は「専用ページを持つ自社のプラグイン」。**組み込みのマニフェスト**（`id: taguyan.vodsplit`・`lane: heavy`・制限時間なし・`describe` は `version` に読み替え・専用の引数に `--cancel-on-stdin`）が、今の `vodsplit-worker\vodsplit-worker.exe` を指す。**フォルダは動かさない**（古い Autona に戻しても見つかる）
- 専用ページ・専用の引数・`ArchiveJobService` の VOD 固有の部分は残す。共通にするのはプロセス管理・イベントの解釈・終了判定・2 レーン・環境変数の引き算
- **インストーラは載せ替えない**（今の `ArchiveWorkerInstaller` のまま。zip の検査と swap の関数だけ共有する）。載せ替えは外部のプラグインが 1 つ掲載されてからの任意の段階
- 組み込みの vodsplit は無効化・削除・遮断の対象外。vodsplit 側の変更は要らない
- vodsplit の別ゲーム対応（ROADMAP 3-4-2）はプラグイン機構の外（vodsplit の中）で扱う

### D10: 段階分け（1 段階 = 1〜2 PR）

| 段階 | 内容 | 価値 | 区分 |
|------|------|------|------|
| **P1 実行エンジン** | `ArchiveWorker` / `ArchiveJobService` から `core/PluginRunner` を切り出す（起動・読み取り・終了判定・中止・制限時間と `timed_out`・終了処理）。`CREATE_SUSPENDED`→ジョブ→再開・優先度の上限・ピークメモリの記録・環境変数の引き算（vodsplit も）。`contract_1.json` | 子孫がジョブから漏れない。契約が文書とテストになる | 高 |
| **P2 マニフェストと汎用ジョブ** | `core/PluginRegistry`・`plugins` ページの静的な登録・開発中のフォルダ・`describe`・`run <request.json>`・項目の配列のフォーム・入力源 `match_log` `state`・`file` / `folder`（`pickFile`）・成果物・2 レーン・ジョブのログ表示 | 開発中のフォルダに置けば Autona の画面から走る | 高 |
| **P3 アクションと `secret`** | 3 つの差し込み先・入力源 `result_image` `thumbnail_image` `archive_export`・`secret`（DPAPI）。公式プラグイン「Webhook 送信」（`taguyan.webhook`）を開発中のフォルダで動かす | 機能が作業の場所に出る。ネットワークと秘密を使う実物で契約を確かめる | 高 |
| **P4 SDK・雛形・文書** | 公式の SDK `autona_plugin.py`（Python の 1 ファイル・依存なし・MIT。ワーカー側のヘルパーと偽ホスト `check` / `run` を兼ねる）・雛形リポジトリ 2 種（Python の onedir 版と Go の単一 exe 版。タグで zip・`autona-plugin.json`・SHA-256 を作る CI）・`docs/plugins/`（契約の 2 文書は英語が正本、日本語は概要）・`CONTRIBUTING` | 第三者が作り始められる。P3 で直した契約を 1 回で書ける | 軽微 |
| **P5a 手動の導入** | `core/PluginInstaller`・手動の zip・同意・有効 / 無効・削除・遮断（3 種と同梱の写しの CI）・`SECURITY.md`・`validate_release_zip.ps1` の追加検査 | 作った物を zip で配れ、悪い物を止められる | 高 |
| **P5b 掲載からの導入** | 掲載リストの「探す」・ダウンロード・数値 id の記録と照合・配布元の判定・`openPluginLink`・`taguyan.webhook` の掲載。**ここまでで OSS 公開してよい** | 利用者がアプリから見つけて入れられる | 高 |
| **P6 更新** | 更新の確認（6 時間）・再同意のダイジェスト | 更新に気づいて入れられる | 通常 |
| **P7 フック** | 配り役・2 つの契機（`MatchLogService` に `best_of` の読み取りと判定を足す）・有効化と値の保存・保留・回数上限・自動無効・履歴 | 押さずに走る連携が作れる | 高 |

- 段階の外（任意）: vodsplit のインストーラの載せ替え
- 各段階で pin するテストは付録 E.4。テストは段階ごとに影響範囲だけ回し、全量は最後に回す

## Alternatives considered

- **何も作らず v1 の外部ツールだけ**: いちばん安いが、画面に出ず導入も止め方も利用者任せ。「持ち寄る」に届かない
- **JSON-RPC 2.0・MCP**: 対応付け・`initialize`・双方向が要るのは常駐型。1 ジョブ 1 プロセスでは包みが増え、今のパーサー・偽ワーカー・vodsplit を書き直すことになる
- **ローカルの HTTP / ソケット / gRPC**: ポート・接続・認証が増える（ADR-006 の理由のまま）
- **Python のプラグインを Autona のプロセスで読む**: 凍結した Autona では依存を足せず、例外とメモリが配信中の Autona に乗る。Qt-free・lock の規則を第三者に守らせられない
- **WASM（Extism）**: ファイル・ネットワークを強制できる唯一の手段だが、ffmpeg・OpenCV の重い処理が載らない
- **OS の隔離（AppContainer・制限付きトークン・Windows Sandbox）**: AppContainer は ffmpeg・作業フォルダ・ネットワークの全部に ACL と capability が要り、作者ごとに壊れ方が違う。Windows Sandbox は Home で使えない。中途半端な隔離は「守られている」誤解だけを残す
- **スコープ付きトークン**: この PC 発の WRITE はトークン無しで通るので境界にならない
- **フォームを JSON Schema にする**: 受け付ける面が広く、Autona 固有の入力源が無い。部分集合を名乗っても方言になる
- **iframe・動的なナビ**: 橋が実質もう 1 つの API になる。3 点の pin と `lastPanel` の復元に手が入る
- **常駐の外部ツールを Autona が管理する**: 「いつ起動するか」が起動時に広がり、死活監視と終了順が増える
- **公式のレジストリと版ごとの審査・署名の検証**: 審査の手間と責任を個人で持てず、作者の更新のたびにオーナーのマージが要る。子プロセスの起動は SmartScreen を通らず、署名を検証するには sigstore などの配布が要る

## Consequences

**良い影響:**

- 重い処理と本体に入れない連携を、本体の配布サイズ・依存・リリース周期を壊さずに足せる。第三者も同じ形で足せ、アプリの中で見つけて入れられ、作者の名前が出る
- 実行が 1 か所になり、子孫がジョブから漏れる穴（`Popen` の後の Assign）が閉じ、ピークメモリが見える
- プロトコルは今の vodsplit のイベント層と同じなので、実装・偽ワーカー・テストを捨てない
- 軽い枠ができ、長い切り抜きの間も短い処理を押せる

**受け入れるリスク・コスト:**

- **第三者の exe を隔離なしで起動する**。悪意あるプラグインは利用者のファイル・設定・トークン・ほかのプラグインの秘密を読め、外へ送れ、居座れる。守りは出所（掲載の身元の確認・数値 id の固定）・同意・遮断・資源まで。事件が起きれば Autona（個人名義）の名前で語られる（論点 2）
- 遮断リストを取得できない間（オフライン）は新しい遮断が届かない。配信中に外部の取得失敗でプラグインが止まる方が害が大きいので、止めない側に倒す
- フックで「押した時だけ走る」が「有効にした契機でも走る」に変わり、WRITE を持つ相手による間接起動も受け入れる（D5）
- `match_decided` は `best_of` を設定した大会でしか出ない。スコアの打ち間違いで勝ちが決まる値に 5 秒とどまると、**誤った結果が投稿され、直した後の本当の決着は投稿されない**（記録 1 件につき 1 回のため）。ボード 2 以降・ダブルス・クルーは出ない
- 手動の zip で入れたプラグインは、最新リリースと同じ zip でない限り、入れ直すたびに `secret` の入れ直しとフックの有効化のやり直しになる（別人の zip に受け継がせないための代償）
- 掲載の確認と遮断の判断がオーナーの運用になる（1 プラグイン 1 回・事件の時）
- 入力源の形・プロトコル・マニフェストが外部契約になり、掲載の後は 1 年の互換の義務を負う
- 汎用ページの表現力は項目の配列と 4 種の成果物まで。vodsplit 級の対話は本体にページを書く
- P1・P2・P3・P5a・P5b・P7 は区分「高」

## Revisit conditions

- **プラグイン同士の連携・契機「書き出しが終わった」**: ほかのジョブの成果物を受けたい実例が出たら、`job_finished` の契機・入力源 `job_result`・`done.data` を、依存の扱い（相手が無い・入れ直された時）と同意の見せ方と一緒に決める。切り抜きの書き出し（`taguyan.vodsplit` の export）だけを先に出すことも、YouTube の ADR の後で検討する
- **取り込み元（provider）**: 本体の parry.gg 対応（ROADMAP 1-3 の 3）で取り込みの受け先（既存データとの合わせ方）が決まったら、別 ADR で検討する
- **テンプレートの同梱**: ADR-007 T3（zip の取り込み）が入ったら、マニフェストに `templates` を足す（知らないキーは無視なので後から足しても互換）。そのとき zip のエントリ名の ASCII 限定も見直す
- 作者から「exe を作るのが壁」という声が複数出たら、`@python`（本体同梱の実行環境）を検討する
- 宣言のフォームで作れない実プラグインが出たら、プレビュー用のリスナーのオリジンで iframe を出す案を検討する
- フックで作れない常駐の実例が 2 件出たら、常駐の管理と Protocol 2（双方向）を検討する
- ヘッドレスでフックを走らせたい要望が出たら、有効化と失敗の確認の入口を決め直す
- この PC 発の書き込みにもトークンを求めるよう ADR-001 を変えたら、スコープ付きトークンを足す
- 外部のプラグインが 1 つ掲載されたら、vodsplit のインストーラの載せ替えを検討する

## オーナーの判断が要った論点（2026-11-26 すべて A で決定）

1. **ROADMAP 6 行の「Discord 連携は検討外」に「本体では」と限定する一文を足すか**
   - **A（推奨）: 足す**。「本体では作らない（入れた人だけが使うプラグインの受け皿はある。ADR-009）」。公式の「Webhook 送信」は Discord の Webhook URL も受けるので、方針の読み方を文書に残しておく。追従は約束しない、も書く
   - B: 足さない。プラグインは作れるが、公式の Webhook プラグインに Discord の URL を入れられることと方針の文言が食い違って見える
2. **隔離なしで他人の exe を Autona が起動することを受け入れるか**（守りは出所・同意・遮断・資源だけ。事件が起きれば Autona の名前で語られる）
   - **A（推奨）: 受け入れる**。掲載の確認（機械的な条件・1 プラグイン 1 回）と遮断の PR はオーナーが持つ
   - B: 受け入れない。その場合**プラグインは作らない**（ADR-007 のまま。外部ツールの入口だけ）。中途半端な隔離は守っている誤解だけを残すので、間の案は推さない
3. **OSS 公開を掲載からの導入（P5b）まで待つか**
   - **A（推奨）: 待つ**。差は数週間。公開の告知に「作り方」と「アプリから入れられる」を載せられ、作った物を配る手段が最初からある
   - B: 先に公開し、P5a 以降は外の開発者が現れてから。公開は早いが、配る手段が「開発中のフォルダに置いてね」になる

---

## 付録 A: Worker Protocol 1

### A.1 起動

```
<entry...> describe
<entry...> run <request.json の絶対パス>
```

- `entry` はマニフェストの argv。先頭はプラグインのフォルダの中の相対パス（`..`・絶対・ドライブ不可）の exe。シェルを通さない。引数はこの形だけ
- 開発中のフォルダだけ、先頭に任意のコマンド（`python` など。PATH で探す）を許す
- cwd は `run` ではジョブフォルダ（`<run_id>\`）、`describe` ではプラグインのフォルダ（導入の途中は展開先の `.new` の中。どちらも `AUTONA_PLUGIN_DIR` と同じ）。パスは cwd ではなく `AUTONA_PLUGIN_DIR` と `request.json` から組むこと
- 終了コード: `0` 成功・`2` 利用者が直せる失敗・`130` 中止・それ以外は異常

### A.2 `describe`

```json
{"type": "describe", "protocol": [1], "id": "example.webhook-post", "version": "1.0.0"}
```

- stdout に 1 行、exit 0。制限時間 60 秒（未署名の onedir はウイルス対策の走査で初回が遅い）。重いライブラリを import しない
- 呼ぶのは導入・更新・開発中の「読み直す」の時だけ。同意の後に呼ぶ（同意前の exe は 1 度も起動しない）
- `id` `version` がマニフェストと違う・共通の `protocol` が無い → 使えない扱いにして理由を出す
- SDK は `plugin.json`（`AUTONA_PLUGIN_DIR` の下）から組むので、作者が版と id を手で揃える必要は無い

### A.3 `request.json`（Autona が書く。UTF-8・BOM なし。プラグインは読むだけ）

アクションで起動したとき（付録 B.1 の `post_image`）:

```json
{
  "protocol": 1,
  "plugin": "taguyan.webhook",
  "job": "post_image",
  "run_id": "20261126-213000-a1b2",
  "trigger": {"kind": "action", "target": "result_image"},
  "locale": "ja",
  "params": {"message": "TOP8 が決まりました"},
  "settings": {"mention": "none"},
  "inputs": {
    "image": {"kind": "result_image", "format": 1,
              "path": "C:\\Users\\<user>\\Videos\\Autona\\plugins\\taguyan.webhook\\20261126-213000-a1b2\\inputs\\image.png",
              "sha256": "9b1c…（64 桁）"}
  },
  "out_dir": "C:\\Users\\<user>\\Videos\\Autona\\plugins\\taguyan.webhook\\20261126-213000-a1b2\\out",
  "data_dir": "C:\\<Autona>\\user_data\\plugins\\taguyan.webhook\\data",
  "host": {"name": "Autona", "version": "2.6.0"}
}
```

フック `match_decided` で `post_match` を起動したとき、違うのは次の欄だけ（`params` は利用者がフックを有効にした時に保存した値）:

```json
"job": "post_match",
"trigger": {"kind": "hook", "event": "match_decided", "hook": "post_match/match_decided"},
"params": {"style": "games"},
"inputs": {"match": {"kind": "match_record", "format": 1, "path": "…\\inputs\\match.json"}}
```

- `trigger.kind` は `manual` / `action`（`target` 付き）/ `hook`（`event` とフックのキー `hook` 付き）
- `inputs` のファイル名は `<項目の key>.<拡張子>`（画像は元の拡張子。`.jpeg` は `.jpg` にそろえる）
- `locale` は `ja` か `en`（Autona の表示の言語）
- `params` はジョブの値の項目の**全部のキー**（手動・アクションは押した時のフォームの値、フックは保存した値。未入力の任意項目は `default`、無ければ `null`）。値の項目が無いジョブは `{}`。`settings` は `secret` 以外の設定の全部のキー（同じ規則）
- `inputs` は入力源の項目の `key` ごとに `{kind, format, path}`（画像は `sha256` も。同じ画像の再送を作者が見分けられる）。入力源が無いジョブでも `{}` で必ずある。中身は付録 B.5
- パスは全部絶対。`out_dir` はホストが作ってから起動する。`data_dir` はプラグインが自由に使う小さな状態の置き場（OAuth の更新トークンなど。**Autona は暗号化しない**）。大きな一時物は `out_dir` へ。重い枠と軽い枠で同じプラグインの 2 ジョブが同時に走りうるので、`data_dir` の取り合いは作者が避ける
- `secret` はここに書かない（A.7）

### A.4 stdout のイベント（1 行 1 オブジェクト・必ず `type`）

| type | 項目 | ホストの扱い |
|------|------|------------|
| `stage` | `stage`（識別子）`label`（文言）`index` `count` | 段を作り直す。段の名前は解釈しない |
| `progress` | `stage` `fraction` `overall`（0〜1）`eta`（秒） | 数値だけ採る |
| `log` | `message` | 直近の行だけ画面に出す |
| `warning` | `message` | ジョブに残す（200 件まで） |
| `done` | `summary`（文言・500 字まで）・`artifacts` | 終端 |
| `error` | `code`（`^[a-z0-9_]{1,64}$`・作者が自由に決める）`message` `hint`（文言） | 終端。利用者が直せる失敗。`message` と `hint` に秘密を入れない |
| `cancelled` | – | 終端 |

- 「文言」は文字列か `{"ja": …, "en": …}`。ホストは今の言語 → `en` → `ja` → 最初の値の順で出す
- 知らない `type`・JSON でない行・1 MiB を超える行・stderr は `worker.log`（ジョブごと 10 MiB まで。以後は捨てる）に回して無視する。イベントの知らないキーも無視する。`id` キーは将来の双方向のために予約し、今は無視して行を採る（`describe` の答えはこの規則の外）
- 終端行は最後の 1 つを採る
- stdout は UTF-8 で書き、1 行ごとに flush する。ホストは `errors="replace"` で読むので、化けた行は黙って壊れる（Python は `sys.stdout.reconfigure(encoding="utf-8")`、C# は `Console.OutputEncoding = UTF8`、Node は既定で UTF-8。SDK は自分で設定する）

**`artifacts`**（50 件まで）

```json
{"type": "done", "summary": {"ja": "Webhook に送った", "en": "Posted"},
 "artifacts": [{"path": "message.txt", "kind": "text", "label": {"ja": "送った本文", "en": "Message"}}]}
```

- `path` は `out_dir` からの相対。ホストが resolve し、`out_dir` の外・リンク・ハードリンク（`st_nlink > 1`）・存在しない物は捨てて警告する。検査は `done` の時と配る時の 2 回
- `kind`: `text`（64 KiB まで全文を出してコピー）・`image`（png / jpg / webp・20 MiB まで縮小表示）・`file` / `json`（「フォルダで開く」）。HTML として描かない。配るルートは SHELL・固定の `Content-Type`・`nosniff`
- フックで起動したジョブのフォルダは終わったら消すので、`artifacts` は残らない（履歴には `summary` だけが残る。付録 D.6）

### A.5 stdin と中止

- ホストは stdin を**正常時はジョブの終わりまで開いたまま**にする。書くのは中止の時の `cancel\n` と、その直後の close だけ
- ワーカーは `cancel` 行か EOF を見たら、次の安全な所で止まり `cancelled` を出して exit 130（Autona が落ちて stdin が閉じても止まる）。知らない行は無視する。stdin は別のスレッドで読むと、重い処理の途中でも中止に気づける
- 中止は**依頼**。取り消せない処理（送信）がもう始まっていれば、送り終えて `done` を出し exit 0 でよい。ホストは `done` と出し、「中止の前に終わっていた」を添える
- ホストは中止を送る前に優先度を NORMAL に戻し、10 秒で終わらなければジョブごと止める。送信中の中止を送り切りたいなら、ネットワークの待ち時間は 8 秒ほどに抑える

### A.6 終了判定（全組み合わせ）

最初に次の 2 つを見る。

- 起動できなかった（exe が無い・起動が失敗して終了コードも無い）→ **`failed`**（理由「起動できない」。exe が見つからなければ「ウイルス対策ソフトが隔離した可能性」と添える）
- ホストの印「制限時間切れ（`timed_out`）」がある → 終端行と終了コードに関係なく **`failed`**（理由「制限時間」）。`done` を出して exit 0 で終わっていても制限時間の後なら `failed`（送信は済んでいるかもしれないが、作者に時間の宣言を直してもらうため）。フックの失敗に数える

それ以外は次の表（今の `_EndState` と同じ順で決まる）。

| 終端行 ＼ 終了コード | 0 | 2 | 130 | それ以外（1・クラッシュ） |
|-------------------|---|---|-----|----------------------|
| `done` | **done** | failed | cancelled | failed |
| `error` | failed | **error** | cancelled | failed |
| `cancelled` | cancelled | cancelled | **cancelled** | cancelled |
| なし | failed ※ | failed ※ | cancelled | failed ※ |

- ※ 中止を依頼した後なら `cancelled`
- `failed` の画面には `worker.log` の末尾を出す。終わった時の `PeakJobMemoryUsed` が軽い枠の上限に届いていれば、理由を「メモリの上限」と出す
- `error` は `code` `message` `hint` を出す

### A.7 環境変数

- 親の環境から外す: `AUTONA_` で始まる全部（開発用の `AUTONA_VODSPLIT_CMD` `AUTONA_PLUGIN_DEV` ほか・ほかのプラグインの `AUTONA_SECRET_*`）と `QT_API`。一覧は `contract_1.json`
- 足す: `PYTHONIOENCODING=utf-8`・`PYINSTALLER_RESET_ENVIRONMENT=1`・`AUTONA_PROTOCOL`（使う版）・`AUTONA_PLUGIN_DIR`（プラグインのフォルダ）・`AUTONA_SECRET_<KEY の大文字>`・`AUTONA_API_URL`（申告 `autona_api` が `read` / `write` の時だけ）
- これは**うっかり漏らさないための物で、境界ではない**（プラグインは親のファイルもレジストリも読める）。子孫のプロセス（ffmpeg など）も環境を継ぐので、`secret` もそこへ渡る

### A.8 ジョブフォルダ

```
<work>\plugins\<id>\                作業フォルダ（archive.work_dir。既定 %USERPROFILE%\Videos\Autona）の下
  <run_id>\                         run_id は今の job_id と同じ形 [0-9]{8}-[0-9]{6}-[a-z0-9]{4}
    request.json  job.json  worker.log
    inputs\                         ホストが置いた入力。ジョブが終わったら消す（開発中だけ残す）
    out\                            ワーカーの出力
```

- 手動・アクションのジョブはプラグインごとに新しい 20 件を残す。フックの実行は付録 D.6
- 再開（resume）は汎用ジョブには入れない（vodsplit は今のまま）
- 起動時に `running` のまま残った汎用ジョブは `failed`（理由「Autona が終了した」）に直す

---

## 付録 B: `plugin.json` と項目の型

### B.1 完全な例（公式の Webhook 送信）

```json
{
  "manifest": 1,
  "id": "taguyan.webhook",
  "version": "1.0.0",
  "name": {"ja": "Webhook 送信", "en": "Webhook post"},
  "description": {"ja": "リザルト画像と試合の結果を Webhook に送ります。Discord は画像と文、Slack は文だけです。", "en": "Posts result images and match results to a webhook. Discord gets the image and text; Slack gets text only."},
  "author": "taguyan",
  "homepage": "https://github.com/yuya-tagu/autona-webhook",
  "icon": "icon.png",
  "license": "MIT",
  "min_autona": "2.6.0",
  "protocol": [1],
  "platforms": ["windows-x64"],
  "entry": ["bin/webhook.exe"],
  "update": {"github": "yuya-tagu/autona-webhook"},
  "permissions": {
    "network": ["discord.com", "hooks.slack.com"],
    "files": "inputs",
    "spawn": false,
    "autona_api": "none",
    "note": {"ja": "受け取った画像と文を、設定した Webhook に送ります。", "en": "Sends the received image and text to the configured webhook."}
  },
  "settings": [
    {"key": "webhook_url", "type": "secret", "label": {"ja": "Webhook の URL", "en": "Webhook URL"}, "required": true,
     "help": {"ja": "Discord か Slack の Incoming Webhook の URL。", "en": "A Discord or Slack incoming webhook URL."}},
    {"key": "mention", "type": "select", "label": {"ja": "メンション", "en": "Mention"}, "default": "none",
     "options": [{"value": "none", "label": {"ja": "しない", "en": "None"}},
                 {"value": "here", "label": "@here"}]}
  ],
  "jobs": [
    {
      "id": "post_image",
      "title": {"ja": "リザルト画像を送る", "en": "Post the result image"},
      "lane": "light",
      "timeout_seconds": 60,
      "memory_mb": 256,
      "params": [
        {"key": "image", "type": "result_image", "label": {"ja": "リザルト画像", "en": "Result image"}, "required": true},
        {"key": "message", "type": "textarea", "label": {"ja": "本文", "en": "Message"}, "max_length": 1500, "default": ""}
      ],
      "actions": [{"target": "result_image", "label": {"ja": "Webhook に送る", "en": "Send to webhook"}}],
      "hooks": [{"on": "result_image_saved", "label": {"ja": "保存したら自動で送る", "en": "Send each saved image"}}]
    },
    {
      "id": "post_match",
      "title": {"ja": "試合の結果を送る", "en": "Post a match result"},
      "lane": "light",
      "timeout_seconds": 20,
      "memory_mb": 128,
      "manual": false,
      "params": [
        {"key": "match", "type": "match_record", "label": {"ja": "決まった試合", "en": "Decided match"}, "required": true},
        {"key": "style", "type": "select", "label": {"ja": "書き方", "en": "Style"}, "default": "short",
         "options": [{"value": "short", "label": {"ja": "短く", "en": "Short"}}, {"value": "games", "label": {"ja": "各ゲームも", "en": "With games"}}]}
      ],
      "hooks": [{"on": "match_decided", "label": {"ja": "試合が決まったら結果を送る", "en": "Post each decided match"}}]
    }
  ]
}
```

- `post_image` はアクションで 60 秒、フックで起動した時は 30 秒になる。フックのキーは `post_image/result_image_saved` と `post_match/match_decided`
- **Discord と Slack の違い**: Discord の Webhook は multipart で画像ファイルを受ける（`payload_json` と `files[0]`。`allowed_mentions` を `{"parse": []}` にしてメンションを無効にし、設定 `mention` が `here` の時だけ `["everyone"]` を許す）。
  Slack の Incoming Webhook は JSON の `text`（と `blocks`）だけでファイルを受けないので、**Slack には文だけを送る**（画像は送らない。説明と設定の help にそう書く）。どちらでもないホストの URL は `error`（`unsupported_host`）

### B.2 最上位の項目

| 項目 | 必須 | 規則（検査） |
|------|------|------------|
| `manifest` | ○ | 整数。知らない値は一覧に理由付きで出し、使えない扱い |
| `id` | ○ | `<作者>.<名前>`。`^[a-z0-9][a-z0-9-]{0,18}\.[a-z0-9][a-z0-9-]{0,19}$`（40 字まで）。フォルダ名と一致。公開後は変えない。作者部分は自由な名前で、本人性は掲載の `repo_id` で見る。`taguyan.` は予約（D7） |
| `version` | ○ | `X.Y.Z` |
| `name` / `description` | ○ / 任意 | 文言。80 字 / 500 字まで |
| `author` | ○ | 文字列。一覧・同意・掲載に出す |
| `homepage` / `funding` | 任意 | `https://` だけ。`openPluginLink` で既定のブラウザに開く |
| `icon` | 任意 | フォルダの中の png（256 KiB まで）。THIS_PC のアイコンのルートで配る |
| `license` | ○ | SPDX の識別子か `"see LICENSE"` |
| `min_autona` | ○ | `X.Y.Z`。下限だけ。決め方: `docs/plugins/` の「機能と最初に入った版」の表（手動とフォーム = P2 の版、アクションと `secret` = P3 の版、フック = P7 の版…）で、使う機能のうちいちばん新しい物の版を書く。迷ったら自分が試した Autona の版。例の `2.6.0` は仮の値 |
| `protocol` | ○ | 整数の配列（今は `[1]`） |
| `platforms` | ○ | 今は `["windows-x64"]` だけ |
| `entry` | ○ | argv。先頭はフォルダの中の exe の相対パス。`@` で始まる先頭は予約（使えない扱い） |
| `update` | 任意 | `{"github": "owner/repo"}`。手動の zip で入れた時に、その zip が本物のリリースと同じかを照らす先（付録 C.3）。配布元の証明にはならない |
| `permissions` | ○ | 申告（強制しない）。下の表 |
| `settings` | 任意 | 項目の配列。入力源の型は使えない |
| `jobs` | ○ | 1 つ以上 |

`permissions`:

| 項目 | 値 | 意味 |
|------|----|------|
| `network` | ホスト名の配列。`[]` = ネットワークを使わない・`["*"]` = 任意の相手 | 送る相手 |
| `files` | `inputs` / `data` / `user` / `anywhere` | 読み書きする範囲。`inputs` = 渡された入力と `out_dir`・`data` = それに `data_dir`・`user` = 利用者が選んだファイル（`file` / `folder` の項目）まで・`anywhere` = それ以外も |
| `spawn` | 真偽 | 別のプログラムを起動するか（ffmpeg など） |
| `autona_api` | `none` / `read` / `write` | v1 を叩くか。`write` は同意に「スコアボードを書き換えられます」 |
| `note` | 文言 | 作者の説明。同意の画面に出す |

ジョブ:

| 項目 | 必須 | 規則 |
|------|------|------|
| `id` | ○ | `^[a-z][a-z0-9_]{0,31}$` |
| `title` | ○ | 文言 |
| `lane` | ○ | `light` / `heavy` |
| `timeout_seconds` | ○ | `light` は 1〜300、`heavy` は 1〜43,200。フックで起動したときは min(この値, 30) |
| `memory_mb` | light は ○ | `light` は 64〜1024（フックを持つジョブは 512 まで）。`heavy` は書いても記録だけ |
| `params` | 任意 | 項目の配列（値の型と入力源の型） |
| `manual` | 任意 | 既定 true。文脈専用の型（`match_record` `archive_export`）を持つジョブは false 必須 |
| `actions` | 任意 | `[{"target", "label"}]`。`target` は B.4 の 3 つ |
| `hooks` | 任意 | `[{"on", "label"}]`。`on` は `match_decided` / `result_image_saved`。1 つのジョブに同じ `on` は 1 つまで。フックのキーは `<job の id>/<on>` |

`actions` か `hooks` を持つジョブは `lane: light` 必須（`heavy` は手動だけ）。

その他の規則:
- 読むときは `utf-8-sig`（メモ帳の BOM を許す）。256 KiB まで
- 知らない最上位のキーは警告して無視する。知らない項目の型・知らない契機を含むジョブは「この Autona では使えない」と理由付きで出す（黙って項目を落とさない）
- フックを持つジョブは、`params` の入力源以外の `required` の項目に `default` が要る（有効にする時のフォームの初期値になる）
- `settings` の `required` が欠けている時は、手動・アクション・フックのどれでも**起動せず**「設定が要ります」と出す（フックの失敗には数えない）
- 設定（`secret` 以外）の保存先は `user_data\plugins\<id>\config.json`
- 検証用に `docs/plugins/plugin.schema.json` を置く（マニフェストそのものの検証。フォームの語彙を JSON Schema にするのではない）

### B.3 項目の型

共通の欄: `key`（`^[a-z][a-z0-9_]{0,31}$`。ジョブ・設定の中で重複なし）・`type`・`label`（文言）・`required`（既定 false）・`default`・`help`（文言・任意）。

値の型（`params` / `settings` の値に入る）:

| type | 画面 | 値・追加の欄 |
|------|------|-----------|
| `text` / `textarea` | 入力欄 | 文字列。`max_length`（既定 200 / 4000、上限 20,000） |
| `number` | 数値 | 数値。`min` `max` `step`（整数なら `step: 1`） |
| `bool` | チェック | 真偽 |
| `select` | 選択 | `options: [{"value": 文字列, "label": 文言}]` の `value`（100 件まで） |
| `file` | ファイル選択（`pickFile`） | 絶対パス。`extensions: ["png", "jpg"]`（小文字・点なし・必須）。UNC は拒否。保存した値はファイル名だけを画面に出す |
| `folder` | フォルダ選択（`pickFolder`） | 絶対パス。UNC は拒否 |
| `secret` | 伏せ字（`settings` だけ） | 値は DPAPI（CurrentUser）で `user_data\plugins\<id>\secrets.bin`。環境変数 `AUTONA_SECRET_<KEY の大文字>` だけで渡す。改行なし・4,096 字まで。画面は「設定済み」だけ。プラグインから書き戻せない |

入力源の型（ホストが `inputs\` に置き、`request.json` の `inputs` に書く。中身は B.5）:

| type | `format` | 中身 | 手動での選び方 |
|------|---------|------|-------------|
| `match_log` | 1 | 試合の記録の JSONL（B.5 の行が並ぶ） | 日付と大会で選ぶ（ADR-006 D4） |
| `match_record` | 1 | 試合の記録 1 件（B.5 の行 1 つの JSON） | 文脈専用 |
| `state` | 1 | 公開済み state の写し（`out/program_state.json` と同じ形） | 選ばない。押した時の物（手動も同じ） |
| `result_image` | 1 | リザルト画像 1 枚（png / jpg）＋ `sha256` | `out/results` の一覧（新しい 30 枚）から |
| `thumbnail_image` | 1 | 試合サムネイル 1 枚＋ `sha256` | 生成済みの一覧から |
| `archive_export` | 1 | 切り抜きの書き出しの一覧（JSON。クリップ本体は元のパス）。形は P3 で YouTube の ADR の要件を見て固め、プレビューの間に決める | 文脈専用 |

- 選手 DB は渡さない。入力源はどれも選手名を含みうる（同意の画面にそう書く）。`state` には本名・X のアカウントの欄も含まれる
- リザルト画像とサムネイルはデスクトップ専用の機能なので、ヘッドレスでは選べない

### B.4 起動のしかたと埋める型の対応

| 起動 | 埋める型 |
|------|---------|
| アクション `result_image`（リザルトのページ） | `result_image` |
| アクション `thumbnail_image`（サムネイルのページ） | `thumbnail_image` |
| アクション `archive_export`（アーカイブのページの書き出し済みジョブ） | `archive_export` |
| フック `match_decided` | `match_record` |
| フック `result_image_saved` | `result_image` |
| 手動 | フォームで選ぶ（`match_log` `state` `result_image` `thumbnail_image`） |

検査:
- アクション・フックを持つジョブは、それが埋める型の項目を**ちょうど 1 つ**持つ
- ジョブの**どの起動のしかたも**、そのジョブの文脈専用の型（`match_record` `archive_export`）の項目を全部埋められること。たとえば `result_image` のアクションと `match_decided` のフックを 1 つのジョブに持たせると、アクションが `match_record` を埋められないので落ちる（ジョブを分ける）
- 文脈で埋まる項目以外の入力源（たとえばアクションのジョブが別に `state` も持つ）は、押した時の物をホストが置く

### B.5 入力源の中身

正本は golden（`docs/plugins/inputs/`。`match_record.json` `match_log.jsonl` `state.json`）で、pytest が実際の `MatchLogService` と state の出力を突き合わせる。欄の追加は同じ `format` のまま行う（作者は知らない欄を無視する）。

**`match_record` / `match_log` の行**（`MatchLogService` が `user_data/match_log/<日付>.jsonl` に書く行そのもの）

| 欄 | 型 | 意味 |
|----|----|------|
| `id` | 文字列 | 記録の id（`YYYYMMDD-HHMMSS-N-XXXX`）。同じ試合の行は同じ id |
| `board` | 整数 | いつも `1` |
| `started_at` / `updated_at` | 文字列 | 最初に書いた時刻 / この行の時刻（ISO 8601・秒まで・時差つき） |
| `closed` | 真偽 | 記録が閉じたか。**`match_record` は試合が決まった時点の行なので `false`** |
| `tournament` | 文字列 | 大会の表示名（空なら大会名）。空文字のことがある |
| `set_id` | 文字列か `null` | 読み込んだセットの id（start.gg など）。手で打った試合は `null` |
| `phase` | 文字列 | フェーズ（例「Top 8」）。空文字のことがある |
| `round` | 文字列 | ラウンド名（ボードの `match` の表示のまま。言語は運営の入力しだい） |
| `p1` / `p2` | 文字列 | 両者の名前（タグ）。左右の入れ替えは記録の中で吸収され、`p1` はいつも同じ人 |
| `game` | 文字列か `null` | 読み込んだゲームの codename |
| `c1` / `c2` | 文字列 | 今のキャラクターの codename（空文字のことがある） |
| `score` | 整数 2 つの配列 | `[p1 のスコア, p2 のスコア]`。勝敗はこれで見る |
| `games` | 配列 | **この記録で数えたゲーム**だけ。各要素は `{"c1", "c2", "at"}`（そのゲームのキャラクターと終わった時刻）。各ゲームの勝者は持たない。読み込んだセットの baseline のスコアは含まないので、`len(games)` が `score` の合計より少ないことがある |

```json
{"id": "20261126-215230-1-a3f9", "board": 1,
 "started_at": "2026-11-26T21:52:30+09:00", "updated_at": "2026-11-26T21:58:40+09:00",
 "closed": false, "tournament": "Example Weekly #12", "set_id": "78901234",
 "phase": "Top 8", "round": "Winners Semi-Final",
 "p1": "PlayerA", "p2": "PlayerB", "game": "<codename>", "c1": "<codename>", "c2": "<codename>",
 "score": [3, 1],
 "games": [{"c1": "<codename>", "c2": "<codename>", "at": "2026-11-26T21:52:30+09:00"},
           {"c1": "<codename>", "c2": "<codename>", "at": "2026-11-26T21:54:55+09:00"},
           {"c1": "<codename>", "c2": "<codename>", "at": "2026-11-26T21:56:41+09:00"},
           {"c1": "<codename>", "c2": "<codename>", "at": "2026-11-26T21:58:40+09:00"}]}
```

- `match_log` は 1 試合につき変わるたびに 1 行を足すので、同じ `id` の**最後の行**がその試合の記録。ホストは選んだ日付・大会の行をそのまま渡す（まとめない）
- 1 行の例（`post_match` の `short`）: `{round} {p1} {score[0]}-{score[1]} {p2}`

**`state`**: `out/program_state.json` と同じ形。よく使う場所:

| パス | 中身 |
|------|------|
| `score.1.team.{1,2}.player.1.name` | 選手名（タグ）。`real_name` `twitter` `team`（スポンサー）`country` `state` も同じ場所 |
| `score.1.team.{1,2}.score` | スコア |
| `score.1.best_of` / `score.1.first_to` | 対戦形式（0 は未設定）/ `best_of` から出した先取数 |
| `score.1.phase` / `score.1.match` | フェーズ / ラウンド名 |
| `tournamentInfo.displayTournamentName` / `tournamentName` | 大会名 |
| `game.codename` | 読み込んだゲーム |

ボード 2 以降は `score.2` …。state のキーの正本は Autona の `src/core/StateSchema.py`（`KEY_CATALOG`）で、`format` 1 の間はキーを消さない。

---

## 付録 C: 配布・掲載・遮断の手順

### C.1 作者が配る物

- **1 プラグイン 1 リポジトリ**（公開）。リリースのタグ名は自由（推奨 `vX.Y.Z`）。Autona は GitHub の「最新のリリース」（下書き・プレリリースを除く）を見る
- リリースに付けるアセットは 2 つ: zip と `autona-plugin.json`

```json
{"autona_plugin": 1, "id": "example.webhook-post", "version": "1.0.0", "tag": "v1.0.0",
 "protocol": [1], "min_autona": "2.6.0",
 "zip": {"name": "example.webhook-post-1.0.0-windows-x64.zip", "size": 48213993, "sha256": "…（64 桁・小文字）"}}
```

（vodsplit の `vodsplit-worker.json` に似るが、`id` `tag` `protocol` を持ち `sets_format` を持たない別の形。64 KiB まで。`tag` と `zip.name` は URL に組むので `/` `\` `..` `%` と制御文字を拒否する。`size` はバイト数、`sha256` は zip ファイル全体）

- zip の中身は `<id>/` の 1 フォルダだけで、直下に `plugin.json`。**導入時に一番上の `<id>/` を剥がし、その中身を `plugins\<id>\` の直下に置く**（`entry` の相対パスの起点は `plugins\<id>\`）

```
example.webhook-post-1.0.0-windows-x64.zip
└─ example.webhook-post/          ← 剥がす
   ├─ plugin.json                 → plugins\example.webhook-post\plugin.json
   ├─ LICENSE
   ├─ icon.png
   └─ bin/
      ├─ webhook.exe              → entry: ["bin/webhook.exe"]
      └─ _internal/ …
```

- zip の規則（導入時に検査する）: 区切りは `/`（`\` の zip も正規化して受ける）・エントリ名は ASCII だけ・リンクと同名の重複は拒否・zip 2 GiB / 展開後 4 GiB / 50,000 件まで（vodsplit のインストーラと同じ値）・展開後の最長の絶対パスが 259 字に収まる（超えたら「Autona をもっと浅いフォルダに置いてください」）・`plugin.json` の `id` `version` が `autona-plugin.json` と一致・`entry` がフォルダの中
- **exe の作り方**: 手順の正本は雛形リポジトリ（P4）の README とビルド用スクリプト。Python なら PyInstaller の onedir（`pyinstaller --onedir --name webhook worker.py`）で、出来た `dist/webhook/` を `bin/` に入れる。
  **`--noconsole`（`--windowed`）を付けない**（stdout が無くなりイベントを書けない。Autona は `CREATE_NO_WINDOW` で起動するので、コンソールの窓は出ない）。onefile は起動のたびに展開して遅く、ウイルス対策に引っかかりやすいので勧めない。Go などは単一の exe でよい
- 同梱物のライセンス表示（GPL ビルドの ffmpeg を含む）は作者の責任。ffmpeg などの重複は受け入れる（1 つ 80〜150 MB。導入後のサイズを一覧と同意に出す）
- 雛形の CI が zip・`autona-plugin.json`・SHA-256 を全部作る。手で作るなら Python の `zipfile` か 7-Zip（`Compress-Archive` は `\` 区切りの zip を作るので避ける）。サイズと SHA-256 は `os.path.getsize` と `hashlib.sha256` で出す
- 文書の決まり: 雛形・予約名の名義は個人名義（taguyan）。HiveeWorks の表記を入れない

### C.2 掲載の申請

1. 雛形の CI でリリースを作り、手元で `python autona_plugin.py check <展開したフォルダ>` を通す（P4 より前は、付録 A・B に沿って手で確かめる。掲載の受け付けは P5b から）
2. リポジトリの数値 id を調べる（`gh api repos/<owner>/<repo> --jq .id`）
3. `yuya-tagu/Autona-releases` に PR を出し、`plugins.json` の `listed` に 1 行足す。PR テンプレートのチェックリスト（4 の条件と、LICENSE・同梱物のライセンス表示・申告を書いたか）に答える

```json
{"kind": "plugin", "id": "example.webhook-post", "repo": "example/autona-webhook-post", "repo_id": 812345678,
 "author": "example", "summary": {"ja": "画像を Webhook に送る", "en": "Post images to a webhook"}, "listed_at": "2026-12-10"}
```

4. オーナーが見るのは機械的な条件だけ: リポジトリが公開で `repo_id` が一致・最新のリリースに `autona-plugin.json` と zip がありダウンロードできる・LICENSE がある・`id` が既存と重ならない（作者部分がほかの作者の掲載と同じでない）。中身の審査はしない。版の更新で PR は要らない
5. マージされると、利用者のプラグインのページの「探す」に出る

`plugins.json` にはテンプレートパッケージ（`kind: "template"`）と外部ツール（`kind: "tool"`）の行も置ける。アプリはリンクと説明だけを出す（テンプレートは ADR-007 T3 の取り込みで入れる。外部ツールは Autona は起動しない）。

### C.3 利用者の導入（Autona の手順）

**掲載から**:
1. `GET api.github.com/repositories/<repo_id>` で今の名前を引く（未認証の API は 1 時間 60 回 / IP なので、使うのはここと更新を入れる直前の 2 回だけ）
2. `https://github.com/<名前>/releases/latest/download/autona-plugin.json` を読み、`id` が掲載と一致するか確かめる
3. `https://github.com/<名前>/releases/download/<tag>/<zip.name>` をダウンロード（`.part` で再開）→ サイズと SHA-256
4. `plugins\<id>.new\` に展開して zip を検査 → `plugin.json` を読む
5. **同意の画面**（名前・id・版・作者・配布元・SHA-256 の先頭・導入後のサイズ・ライセンス・印・固定の文・作者の申告・渡すデータ・フック（既定無効））。断ったら `.new` を消す
6. `describe`（`.new` の場所で 60 秒）→ swap（失敗したら戻す）→ `user_data\plugins\<id>\install.json` に配布元（`{"kind": "github", "repo_id": …, "name": …}`）・zip の SHA-256・同意のダイジェストを記録

**手動の zip**: SHELL のアップロード（今のゲームアセットの zip の取り込みと同じ型。Content-Length 必須・上限は C.1 の zip の大きさ）。4 からは同じ。同意の画面に「この zip の出所を Autona は確かめられません。作者のページの SHA-256 と見比べてください」と計算した値を出す。

- 配布元は `{"kind": "manual"}` で記録する。ただし次のどちらかで、zip の SHA-256 がそのリポジトリの最新リリースの `autona-plugin.json` の `zip.sha256` と一致したら `{"kind": "github", …}` として記録する（以後は更新も確認できる）: 導入済みの同じ id に記録済みのリポジトリ、まだ入っていなければ `plugin.json` の `update.github`（数値 id を 1 回引く）。オフラインなら照合できないので `manual`
- 掲載済みの id で照合に通らなければ「掲載されている物とは別物です」と出す

**配布元の判定**（導入済みの id に入れるとき。`repo_id` は Autona がダウンロードしたか、上の SHA-256 の照合に通った時だけ持つ）:

| 入れる物 | 扱い |
|---------|------|
| 記録済みと同じ `repo_id`・版が上 | 通常の更新（ダイジェストが同じなら同意なし） |
| 記録済みと同じ `repo_id`・版が同じか下 | 確認を出して入れる（設定は残す） |
| それ以外（違う `repo_id`・照合に通らない手動・手動から手動） | **新規の導入**: `secrets.bin` を消し、フックを全部無効、同意をやり直す |
| 同じ id の開発中のフォルダがある | 断る（D4） |
| `taguyan.` で、公式の `repo_id` でない | 断る |

**実行中のジョブとの関係**: 更新の swap は、そのプラグインのジョブが走っていない時だけ（走っていればボタンを「実行中」で無効にする）。無効化・遮断の一致・削除は、走っているジョブを中止してから行い、保留中のフックを捨てる。

### C.4 更新

- プラグインのページを開いた時と「更新を確認」を押した時だけ。前回から 6 時間未満なら読まない。読むのは配布元が `github` の各プラグインの `releases/latest/download/autona-plugin.json`（API を使わない）
- 版が上なら「更新あり」。押すと C.3 の 1〜6（1 で `repo_id` を照合し、違えば止めて理由を出す）。ダイジェストが変わっていれば再同意。それまでは旧版のまま動く
- 自動では入れない

### C.5 遮断

- 載せる基準: 利用者の情報を断りなく外へ送る・破壊する・申告と大きく違う・遮断を避ける再配布。報告先は `SECURITY.md`（OSS 公開の前に置く）
- 手順: オーナーが `plugins.json` の `blocked` に行を足す PR をマージする。利用者の次の取得（起動後か 6 時間ごと）で届く

```json
"blocked": [
  {"id": "bad.stealer", "versions": "*", "reason": {"ja": "設定ファイルを外部へ送っていた", "en": "Exfiltrated settings"}, "since": "2027-01-05"},
  {"id": "some.plugin", "versions": "<=1.2.0", "reason": {"ja": "1.2.0 までに不具合", "en": "Broken up to 1.2.0"}, "since": "2027-01-05"},
  {"sha256": "3f5a…（64 桁）", "reason": {"ja": "同じ中身の再配布", "en": "Repackaged copy"}, "since": "2027-01-05"},
  {"repo_id": 99999999, "reason": {"ja": "この配布元の物をまとめて止める", "en": "All from this repository"}, "since": "2027-01-05"}
]
```

- 行の意味: `id` と `versions`（`*` か、比較 `<` `<=` `=` `>=` `>` と `X.Y.Z` 1 つ）・`sha256`（`install.json` に記録した**導入した zip** の SHA-256。開発中のフォルダには無い）・`repo_id`（記録した配布元）。自己申告の作者名では止めない
- 取得: 組み込み以外のプラグインが 1 つでも入っていれば、起動後にバックグラウンドで 1 回と以後 6 時間ごと（URL は本体の定数。`raw.githubusercontent.com` の `Autona-releases` の既定のブランチ）。プラグインのページを開いた時の掲載の読み込みでも更新する。失敗したら `user_data\plugins\_listing.json`（前回の写し）、無ければリリースに同梱した写し（本体のリリースの CI が取り込む 1 ステップ）
- 起動の直前（手動・アクション・フック・`describe`）に手元の写しと照らす。一致したら何も起動せず、一覧に理由を出す。フォルダは消さない（利用者が削除を押す）
- 開発中のフォルダは遮断の対象外（自分で置いた物）

### C.6 削除で消す物

| 物 | 扱い |
|----|------|
| `plugins\<id>\` | 消す（走っているジョブは止めてから） |
| `user_data\plugins\<id>\`（`config.json`・`install.json`・同意とフックの記録・履歴） | 消す |
| `secrets.bin` | **必ず消す** |
| `user_data\plugins\<id>\data\`（`data_dir`） | サイズを出して聞いてから消す |
| `<work>\plugins\<id>\`（ジョブフォルダ） | 聞いてから消す（`out\` を残す選択肢あり）。作業フォルダを途中で変えた場合の古い場所は探さず、場所を出すだけ |

### C.7 環境まわりの規則

| 項目 | 決めたこと |
|------|----------|
| 非 ASCII の利用者名 | `TEMP` `APPDATA` が非 ASCII でも動くことを、SDK の偽ホストの `check` が非 ASCII のフォルダで試す。雛形の CI で回す |
| 新しい zip を別フォルダに展開した利用者 | プラグインは付いてこない。文書に「前のフォルダの `plugins\` と `user_data\plugins\` を移す」。`secret` は DPAPI なので別の PC・別の利用者では読めず入れ直し（ローミングプロファイルでは読める） |
| リリース zip への混入 | `validate_release_zip.ps1` の Check 2（git で追跡していないファイルの拒否）で `plugins\` は既に落ちる。追跡済みでも `plugins\`（と `.part` `.new` `.old`）を入れない検査を、`vodsplit-worker/` を拒む Check 6 と同じ形で足す |
| 同じフォルダでデスクトップとヘッドレスを同時に起動 | ヘッドレスはプラグインを実行しないので取り合いは起きない |
| 開発中の `secret` | 画面から入れる（DPAPI で `_dev\<id>\` に保存）。SDK の偽ホストは `--secret KEY=VALUE` で渡せる。親の環境の `AUTONA_SECRET_*` は引き算で外れるので使えない |
| SDK の偽ホスト | `check <folder>`（マニフェストの検査・`describe`・起動の時間・最長パス・非 ASCII）と `run <folder> <job> [--params p.json] [--input key=path] [--setting k=v] [--secret k=v] [--trigger action:<target> / hook:<event>] [--cancel-after N] [--timeout N]`（`request.json` を作って走らせ、イベントと付録 A.6 の判定を突き合わせる）。本体の pytest が同じ偽ワーカーで偽ホストと実エンジンの結果を照らし、ずれを防ぐ |

---

## 付録 D: フックの実行規則

### D.1 `match_decided` の条件

`MatchLogService` の observer が、ボード 1 の publish を処理した後（記録を更新した後）に判定する。observer が読む値に `score.1.best_of` を足す。

- しきい値 `T = best_of // 2 + 1`（過半）。`best_of` が 0（未設定）なら出さない。奇数の `best_of` では state の `first_to` と同じ値。`first_to` は `best_of` から出した値で別の入力が無いので、`first_to` だけを使う大会は無い。偶数の `best_of`（引き分けがありうる形式）では過半を取った時に出る
- 記録ごとに「発火済み」の印を持つ。次の全部を満たした最初の publish で**候補**にする:
  1. 記録に**数えたゲームが 1 つ以上ある**（`games` が空でない）。読み込んだセットの baseline だけの記録・記録の始まりでは出さない。終わったセットを読み直しても出ない
  2. まだ発火していない
  3. `max(score) >= T`
- この定義で次も決まる: `best_of` を後から入れた（2-0 の後で 3 を入れた）→ その publish で出る。名前とスコアを手で一度に打った記録（そのぶんのゲームを数える）→ 出る
- 候補になってから **5 秒**、条件が保たれていたら発火する（打ち間違いを直す猶予）。5 秒の間に条件を外れたら候補を取り消し、発火済みにしない（後の本当の決着で出る）。発火の時に渡す `match_record` はその時点の記録の行（`closed: false`）
- 発火したら、後でスコアを直しても再発火しない（記録 1 件につき 1 回）。打ち間違いが 5 秒を超えて残ると、誤った結果が出て正しい結果は出ない（Consequences）
- 候補と 5 秒の待ちは配り役が持つ。observer は `StateManager.lock` の中で配り役の列（葉のロック。書き込みスレッドの列と同じ規則）に「候補 / 取り消し」を積むだけ。試合の記録の書き込みの成否には関係させない（ディスクに書けなくてもフックは出る）

### D.2 `result_image_saved`

- リザルト画像を 1 枚 `out/results` に保存するたびに 1 回（png か jpg の 1 ファイル）。保存した描画のレーンの完了から配り役に積む
- 渡すのはその 1 枚（`sha256` 付き）。TOP8 で数枚続けて保存すると、その数だけ契機が出る（D.3 の保留と上限が掛かる）

### D.3 保留

- フックごとに「最新 1 件」の固定の枠を持つ（待ち行列ではない）。契機が来た時に軽い枠が埋まっていれば保留し、空いたら保留の古い順に起動する
- 保留中に同じフックの契機がまた来たら上書きし、履歴に「上書き」と残す。10 分待っても起動できなければ捨て、履歴に「破棄」と残す
- 同じ契機に 2 つのフックがあっても、枠はフックごとなので 2 つ目は落ちない
- 軽い枠が空いた時、利用者が押した手動・アクションの要求は保留中のフックより先に通す（`busy` の表示を見て押し直した要求を待たせない）

### D.4 回数の上限

- 数えるのは**起動**（契機の数ではない）。保留から起動した時もその時に数える
- **直近 60 秒の滑る窓で 6 回まで**。超える起動はしないで、履歴に「スキップ（上限）」と残す。スキップした分は保留しない
- **直近 1 時間の滑る窓で 60 回を超えたら、そのフックを自動で無効**にする（理由「回数の上限」）。成功し続ける輪（v1 を書いて自分を呼び直す・WRITE を持つ相手の連打）はこれで止まる
- 判定の順: 契機 → 枠が埋まっていれば保留（上書き）→ 起動の直前に 60 秒の窓を見てスキップか起動 → 起動したら 1 時間の窓を見て、超えたら無効にする

### D.5 失敗と自動無効

- 連続 5 回の失敗で自動で無効にする。失敗に数えるのは `failed`（`timed_out` を含む）と `error`。成功（`done`）で数を 0 に戻す。`cancelled`（利用者の中止・終了時）は数えない
- 必須の設定（`secret` を含む）が欠けている時は起動せず「設定が要ります」と出し、失敗に数えない
- 自動無効にしたら、そのフックの行を無効にし、理由と最後のエラーをフックの行（SHELL）に出す。一覧の `state` はプラグイン単位のままで、フックの自動無効では変えない。有効に戻すのは利用者だけ

### D.6 有効化・値・履歴

- フックのキーは `<job の id>/<on>`。有効 / 無効・保存した値・保留・回数・失敗の数・履歴は全部このキーごと
- 有効にする時、そのジョブの値の項目（入力源以外）のフォームを `default` を初期値にして出し、利用者が確かめて保存する。保存した値は `user_data\plugins\<id>\hooks.json` に置き、フックはこの値で走る
- 再同意でそのジョブの値の項目が変わったら、そのフックを無効にし、値を `default` に戻して確かめ直してもらう
- フックで起動したジョブのフォルダは終わったら消す。履歴はフックごとに新しい 20 件（時刻・状態・`summary`・理由）。**最後に失敗した 1 件だけはフォルダ（`request.json`・`worker.log`）を残し**、フックの行から「ログを見る」で開ける（自動無効の理由を作者も利用者も追えるように）。次に失敗したら入れ替える
- 開発中のプラグインは、フックの行の「サンプルの入力で試す」で、golden の入力を使って 1 回起動できる（回数と失敗には数えない）

### D.7 走らない時

- ヘッドレスでは走らせない
- シェルの終了時は配り役を止め、保留を捨てる（終了の処理の中では新しく起動しない）
- `result_image_saved` と `match_decided` は、そのフックを有効にしているプラグインが 1 つも無ければ配り役に積まない

---

## 付録 E: 実装の制約

### E.1 生成順とスレッド

- `PluginRegistry`・`PluginRunner`・フックの配り役・`PluginInstaller` は `bootstrap_core()` で `SettingsManager`・`TaskExecutor` の後、Web サーバーの前に作る。`ArchiveJobService` は `PluginRunner` の後（`ORDER CONSTRAINT:` と AST の pin を更新）。どれも Qt を import しない・import の副作用ゼロ
- `MatchLogService` は配り役の後に作る（observer が配り役の列に積むため）
- stdout / stderr の読み取り 2 本は今のものを `PluginRunner` に移す。配り役 1 本を足す（`THREADS_LEFT`）。遮断リストの取得・更新の確認は `TaskExecutor` のジョブ
- 配り役は `state_updated` を購読しない（`STATE_SUBSCRIBERS` を増やさない）。契機は `MatchLogService` の observer・リザルトの描画のレーンの完了から積まれる
- 再開は `NtResumeProcess` で行う（`Popen` はスレッドのハンドルを返さないため）

### E.2 起動時と終了時

- 起動時: マニフェストを読むだけで、何も起動しない。`running` のまま残った汎用ジョブを `failed`（理由「Autona が終了した」。再開が無いので `cancelled` にしない）に直し、swap の残骸（`.new` `.old`）を片付ける。vodsplit は今の `_Sweep` のまま。遮断リストの取得はバックグラウンド
- 終了時: 今の `ArchiveJobService.Shutdown` の位置（Web サーバーの停止の後・`TaskExecutor` の前）で、配り役を止めて保留を捨てる → 両枠に `cancel` → 猶予（デスクトップ 5 秒・ヘッドレス 2 秒）→ 残りを止める。`Window.closeEvent` と `HeadlessShell.Shutdown` の 2 か所

### E.3 ログ

- `main.log` には開始と終わりの 1 行だけ（値・パス・選手名を書かない）。ページの「ログをコピー」は利用者のフォルダ名を伏せる

### E.4 段階ごとに pin するテスト

- **P1**: 偽ワーカーの全モード（ok / error / crash / slow / hang / 孫 / 中止後の done / 起動できない）・`contract_1.json`・`THREADS_LEFT`・生成順の AST・`test_core_qt_free`・凍結した vodsplit の手動確認。実機で確かめる: ジョブの優先度の上限を書き換えた時に既存の子孫が NORMAL に上がるか・`CREATE_SUSPENDED`＋`NtResumeProcess` に Defender が反応しないか
- **P2**: マニフェストの検査の表（B.4 の「どの起動のしかたも埋められる」を含む）・ルート台帳と区分（一覧の欄の列挙）・3 点の登録・入力源の golden・開発中フォルダの id の重なりを断る・E2E は開発中の偽プラグイン
- **P3**: `secret` が `request.json`・`job.json`・ログに出ない・削除で消える・成果物の画像を blob URL で出す
- **P5a / P5b**: zip の検査の表（`<id>/` を剥がす・`tag` の文字）・swap の失敗からの戻し・遮断の 3 種・配布元の判定の表の全行（手動から手動・SHA-256 の照合・`taguyan.`）
- **P7**: `match_decided` の条件（baseline・`best_of` の後入れ・5 秒の取り消し・1 記録 1 回）・保留と上書き・2 つの窓・自動無効・最後の失敗のフォルダを残す・`STATE_SUBSCRIBERS` が増えていない・終了時に保留を捨てる・ヘッドレスで走らない
