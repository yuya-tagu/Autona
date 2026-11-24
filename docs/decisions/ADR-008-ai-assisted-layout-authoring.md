# ADR-008: 運営が AI でレイアウトを作れるようにする（オーバーレイ契約・作成キット・アプリ内の確認）

## Status

Accepted（2026-11-21 オーナー承認。末尾の論点は 1 = A・2 = A・3 = A・4 = A）。D7（描画系への拡張）は 2026-11-23 追記・オーナー承認

## Context

イベント運営が自分で OBS のオーバーレイ（BOARD・実況・TOP 8）を足せるようにしたい。想定する作り手は、
**AI（ChatGPT / Claude などのチャット、または Claude Code / Cursor などのエージェント）に頼んで HTML を作れる人**で、
腕前はマクロで自動化できる程度。Autona の内部は知らない。

**次は採用済みで、この ADR では覆さない。**

- デザインの単位は `layout/` 直下の 1 フォルダで、`settings.json` の `autona` ブロックを `core/LayoutCatalog` が読む（ADR-007 D1。T1 で実装済み）。
  手で置いたフォルダは再起動なしで一覧に出る
- zip の取り込み（全 kind）と、リザルト / サムネイルの描画の隔離は ADR-007 の T2 / T3 で作る（この ADR では作らない）
- GUI で作る経路として Designer（`layout/custom/`）がある。この ADR では残す

**2026-11-21 に調べて分かった事実（判断の根拠）:**

| 項目 | 事実 |
|------|------|
| 受け取りの仕組み | 同梱のオーバーレイは全部 `layout/include/globals.js`（約 1,100 行。上流の TSH 由来）を使う。グローバル `data` / `oldData` に state 全体が入り、レイアウトは `Start()` と `Update(event)` を上書きする。同時に `tsh_update` イベントも出す。転送は Socket.IO（`program_state` 全量 / `program_state_update` 差分）で、file:// ・`host=absolute`・再接続の失敗のときは `out/program_state.json` を 64ms ごとに読む。jquery・gsap・kuroshiro など 10 本のライブラリを順に読み、最初の更新までは `gsap.globalTimeline` を止めている |
| URL のクエリ | `globals.js` は URL のクエリを**全部 `window[k]` に代入する**（`scoreboardNumber` もこの仕組み）。レイアウトのトップレベルの宣言と同じ名前のクエリはそれを文字列で上書きする |
| レイアウトが読むキー | state の生のパスを直接たどる（例: `data.score[N].team["1"].player["1"].name`・`score[N].best_of`・`commentary[i].display`・`top8.matches[id].slots`）。チームの番号が文字列の `"1"` / `"2"`、ボード番号は URL の `scoreboardNumber` |
| キーの正本 | `src/core/StateSchema.py` の `KEY_CATALOG`（ルート 20・キーパス 153）。**キー単位で「外部に約束する」区分は無い**。改名を防ぐのは `StateMigrations` だけで、オーバーレイ向けの約束は文書にも無い |
| v1 API | エンドポイントは版で守る（破壊的変更は `/api/v2`）。**state の中身の形は版の対象外** |
| Designer の語彙 | `designer_runtime/runtime.js` の `resolveBinding()` が固定の 21 語（`p1.name`・`p1.score`・`tournament.name`・`match.phase` など）を state に対応づける。ボード 1 に固定で、表示の切り替え（`show_sponsor`）や整形（空のスコアは 0・シードは `SEED n`）を中で効かせる。Web 側に語彙の写し（`ScoreboardDesigner/types.ts` の `BINDINGS`）とサンプル（`bindings.ts` の `SAMPLE_STATE`）がある |
| サンプルデータ | オーバーレイを描くための state 全体のサンプルは無い（部分的な golden だけ）。`out/` は gitignore |
| 作者向けの案内 | `layout/README.md` はマニフェスト・Chrome でのローカル確認・テンプレートの contract 1・Designer の説明。**読めるキー・`Update` の使い方・データの届き方は書かれていない** |
| 一覧から消える理由 | `LayoutCatalog` は、入口の HTML が無いフォルダと、ブロックもフォルダ名の規約も無いフォルダを**警告なしで**一覧から外す。警告（ログだけ）が出るのは壊れたブロック・知らない kind・テンプレートの知らない contract。どの場合も一覧に出ないので、アプリでは理由が見えない |
| 例外の行き先 | `Update` の中の例外は `console.error` に出るだけ。OBS とアプリのプレビューでは作り手に見えない |
| アプリ内のプレビュー | BOARD / 実況 / TOP 8 / Stats のページの `DesignPane` が、プレビュー用リスナー（SF-8a。別ポート = 別オリジン、読み取り専用）のオリジンで `sandbox="allow-scripts allow-same-origin"` の iframe を出す。Board はオーバーレイの iframe が 2 枚。**「Web 側に `message` のリスナーを足さない」が規則**（`.claude/rules/state-and-webserver.md`）で、`DesignPane.test.js` が固定している。iframe がフォーカスを持つと外す（隔離） |
| フォルダを開く | `LayoutCatalog.RevealBoardFile` が Board 用にエクスプローラーで開く操作を持つ（SHELL） |
| 配布 zip | `scripts/gen_release.ps1` が git 管理下の `assets/` `layout/` `user_data/` をコピーし、`validate_release_zip.ps1` が必須のエントリを確かめる。自動更新は `_internal` 以外をマージで上書きする（消したファイルは利用者の手元に残る） |
| リザルト / サムネイル | 契約（contract 1・payload の golden・追加だけ・知らない番号は一覧に出さず警告）が ADR-007 D1 / D3 で決まっている。オーバーレイには同じものが無い |

**問題は 3 つ。**

1. AI に渡す材料が無い。読めるキーとサンプルデータが無いので、AI は同梱のコードを読むか推測で書くしかない。
   生のパスは入れ子が深く（チームの番号が文字列・ボード番号は URL）、推測では外れやすい。URL のクエリが同名の変数を壊すといった罠も知らされない
2. 約束が無い。運営が作ったレイアウトは state の内部の形に直接つながる。内部のキーを整理するたびに、
   Autona の側からは見えない第三者のレイアウトが壊れる
3. 確かめる手段が無い。一覧から黙って消え、例外はコンソールにしか出ない。作り手が「動かない」を AI に伝える材料が取れない

## Decision

### D1: オーバーレイ契約（overlay contract 1）を作り、`layout/include/autona.js` で出す【論点 1・3】

- 新しい部品 `layout/include/autona.js` を足す。`globals.js` の転送（Socket.IO・差分・フォールバック・再接続）をそのまま使い、
  レイアウトには**整えた形（ビュー）**だけを渡す。受け取りは `globals.js` の `tsh_update` イベントに `addEventListener` でつなぐ
  （グローバルの `Update` は上書きしない。同梱を真似た AI のコードが `Update` を定義しても `onUpdate` が止まらないように）

```html
<script src="../include/globals.js"></script>
<script src="../include/autona.js"></script>
<script>
  Autona.onUpdate((view, prev) => {
    document.querySelector("#p1-name").textContent = view.board.p1.name;
    document.querySelector("#p1-score").textContent = view.board.p1.score;
  });
</script>
```

- ビューの形（contract 1 の範囲。キーの全量は L1 で D1 の対応表に書く）:

| まとまり | 中身の例 | 元の state |
|---|---|---|
| `view.board` | `p1` / `p2` の `name`・`sponsor`・`score`・`country`・`character`・`losers`・`teamName`・`color`、`bestOf`・`phase`・`round`・`station` | `score[N]`（N は URL の `scoreboardNumber`。既定 1。今の OBS 用 URL と同じ） |
| `view.board.p1.players[]` | 団体戦のときのメンバー（単独戦では 1 人） | `score[N].team.{T}.player.{M}` |
| `view.tournament` | `name`・`logo`・`themeColor`・`accentColor` | `tournamentInfo`・`colors` |
| `view.commentary[]` | 表示する人だけを表示順に。`name`・`sponsor`・`twitter`・`role`・`icon` | `commentary`（`display` で並べ替え・絞り込み） |
| `view.top8` | `players`・`matches`・`phaseLabel` | `top8` |
| `view.options` | 表示の切り替え（スポンサーを出すか等）。ビューの値には効かせない（素の値を渡し、出すかどうかは作り手が決める） | `display_options` |

- 名前は英語の camelCase、番号は配列か `p1` / `p2`、画像は**そのまま `<img src>` に入る URL**（オリジンからの絶対パス `/assets/...` `/user_data/...`）にする。
  作り手に相対パスの組み立てをさせない。そのため **`autona.js` は http で開いたときだけ対応する**（file:// は対象外。確かめる場所はアプリのプレビューと OBS 用 URL）
- 値が無いときは空文字・空配列・`null` のどれかに決めて表に書く（`undefined` を出さない。AI が書く `if` を減らす）
- **対応表の正本を 1 つにする**: `layout/include/overlay_contract.json`（ビューのキー・型・空のときの値・元の state のパス・説明 ja / en）。
  - pytest: 表の元のパスが全部 `KEY_CATALOG` にあること（state の改名で落ちる）
  - pytest から node の `vm` で `autona.js` を本物の `globals.js` と動かし、サンプルの state → ビューが期待値（`tests/fixtures/overlay/views.json`）と一致し、表のキーと型に合うこと
  - キットの `CONTRACT.md`・`AI_PROMPT.md` はこの表から生成する（D2）
- 約束の範囲: **キーの追加だけ**。改名・削除・意味の変更は contract を上げる（ADR-007 D3 と同じ規則）。
  マニフェストの `"contract"` はオーバーレイでは省略可（省略は 1）。**知らない番号は一覧に出さず理由を出す**（ADR-007 D1 と同じ扱い。理由は D3 で見えるようにする）
- 生の state は塞がず `Autona.raw` で渡すが、約束の外と明記する（論点 3）。同梱のレイアウトは今の書き方のまま（移行は強制しない）
- 対応づけは JS（`autona.js`）に置く。core に新しい state のルートやイベントは足さない（論点 1）
- `autona.js` が読む URL のクエリは `autona` の接頭辞付き（`autonaSample` / `autonaPreview`）。`URLSearchParams` で読み、`window` の値には頼らない。
  キットに「トップレベルで使わない名前」（`Autona`・`autona*`・`scoreboardNumber`・`Start`・`Update`・`data`・`oldData`・`settings`・`tsh_settings`・`LoadEverything`・`getData`・`UpdateData`）を書く
- キットに `gsap` を使うなら最初の更新まで止まっていることを書く（`globals.js` の既存の挙動）
- L1 の実装: ビューのキーの全量は `layout/include/overlay_contract.json`（この節の表は例のまま残す）。各選手は
  `name`・`sponsor`・`sponsorLogo`・`realName`・`twitter`（先頭の `@` を除く）・`seed`・`country` / `region`（`{code, name, flag}`）・
  `character`（`{name, icon, image, images[]}`。`characters[]` の先頭）・`characters[]`、`players[]` の要素はさらに `stock`・`active`
  （クルーバトルだけが値を持つ）。`board.p1` / `p2` は `players[0]` と同じキー（`stock`・`active` を除く）にチームの `score`・`losers`・
  `teamName`・`color`・`players` を足した形。`board` は `number`・`bestOf`・`phase`・`pool`・`round`・`station`。`top8` は `phaseLabel`・
  `players[]`・`matches`（固定の 11 試合の id をキーにしたオブジェクトで、各試合は `name`・`p1` / `p2`（選手のキーに `slot`・`score`）・
  `finished`・`active`・`winner`）。実況のアイコンは名前（`mic` など）なので `commentary[].iconName`（上の表の `icon`。画像の URL と取り違えないよう改名）
- L1 の実装: 値が無いときの値は型で決める（文字列 `""`・数値 `null`・配列 `[]`・真偽値 `false`）。例外は `board.number`（URL が無ければ 1）と
  `options`（`StateSchema.DISPLAY_OPTIONS` の既定値）。オブジェクトのキー（`country`・`character`・`top8.matches` の 11 試合）は常にある。
  `board.bestOf` は state の 0（未設定。同梱の `scoreboard_simple` は 0 でゲームの枠を出さない）も `null`。TOP 8 の `slot` は未定か 8 人のどれでもない
  番号なら `null`（選手のキーは全部空）。TOP 8 の state は国の名前を持たないので `top8` の `country.name` は今は常に空
- L1 の実装: `character.image` は、レイアウトの `settings.json` の `assets.default.asset_key`（同梱が使うキー）→ `full` → アイコン以外でアセットの
  名前順の先頭 → アイコン。`images[]` もアセットの名前順（state のキーの順に頼らない）
- L1 の実装: `commentary[]` は `display` が 0 でなく**名前が空でない**人だけ（同梱の `commentators` と同じ。名前の無い欄を表示順に出しても
  描くものが無い）。並びは `display`、同じなら欄の番号。件数の上限は core（表示は 4 人まで）に任せ、autona.js では切らない
- L1 の実装: `tournament.name` は表示大会名、空なら大会名。`tournament.themeColor` / `accentColor` はレイアウトの種類の `colors.{feature}`
  （`scoreboard` → `board`・`commentary`・`top8`）があればそれ、無ければ大会の色（同梱の 3 種と同じ解決）。種類は `settings.json` の
  `autona.kind`、無い（または知らない値の）ときは URL のフォルダ名から `LayoutCatalog` と同じ接頭辞の規則で決める。`tournament.logo` は
  `logoVersion` が 0 なら空
- L1 の実装: `onUpdate` は**ビューが変わったときだけ**呼ぶ（統計など、ビューに出ない state の更新では呼ばない）。最初の更新の後に登録した関数は
  その時点のビューで 1 回呼ぶ。`view` と `prev` は登録した関数のあいだで共有するので再帰的に凍結する。1 つの関数の例外（同期・非同期）は
  `console.error` に出し（同じ関数は成功するまで 1 回だけ）、ほかの関数は呼ぶ
- L1 の実装: 読む順番は **`globals.js` の直後、レイアウト自身のスクリプトより前**（キットにも書く）。`autona.js` は `LoadEverything()` を自分で呼ぶ
  （ページの解析の後）。同梱をまねたレイアウトが自分でも呼ぶので、`LoadEverything` を「2 回目以降は 1 回目の Promise を返す」包みに差し替える
  （上の最小例は `LoadEverything` を書かない）。読み込んだ時点で既に `LoadEverything()` が走っていた（`window.tshSocket` か、それが足す
  `include/jquery-3.7.1.min.js` の `<script>` がある）ら `console.warn` を 1 回出し、自分では呼ばない
- L1 の実装: `autonaSample` は `globals.js` の `getData`（ファイル経由の読み込みと、OBS がソースを再表示したときの読み直し）をサンプルを返す
  関数に、`UpdateData_SocketIO` を「`tsh_init` の後に `UpdateData()` を 1 回呼ぶ」関数に差し替える（Socket.IO はつながない。
  サンプルの名前は英数字・`_`・`-` だけ）。サンプルは `singles`（1on1 の 2 ボード・実況 3 人・TOP 8 の途中）・`teams`（3on3 とクルーバトル・
  始まる前の TOP 8）・`empty`。キャラの画像は同梱の `assets/mock_data/` を指す。サンプルの葉のキーも `KEY_CATALOG` に収まることをテストで固定する
- L1 の実装: `Autona` が出すのは `contract`（数値の 1）・`onUpdate`・`raw` だけ。対応づけは JS のコードで、表は実行時に読まない。
  表との一致（キー・型・空の値）と元のパス（`KEY_CATALOG` のキー、または dict を持つキー（`country`・`character.{K}` など）の下だけ）は
  `tests/test_overlay_contract.py` が固定する（`tests/test_designer_runtime_contract.py` と同じく pytest から node の `vm` で本物の
  `globals.js` と動かす形）。期待値 `views.json` はケースごとに見るまとまりだけを持ち、空の state の期待値は表から作る
- L1 の実装: マニフェストの `contract` はブロックがあるオーバーレイ（`scoreboard` / `commentary` / `top8`）で読み、省略は 1。
  `LayoutCatalog.OVERLAY_CONTRACTS` に無い値（整数でないものを含む）は警告ログを出して、フォルダ名の規約でも拾わない。一覧の項目の形
  （`GET /api/v1/layouts`）は変えない。配布 zip の必須の項目（`validate_release_zip.ps1`）に `autona.js` と `overlay_contract.json` を足した

### D2: 作成キットを「新しいデザインを作る」で `layout/` に直接作る【論点 2】

作り手の作業場所を最初から `layout/<フォルダ名>/` にする。`../include/` が解決し、アプリのプレビューと OBS 用 URL がそのまま使えるため。

- BOARD / 実況 / TOP 8 のデザイン一覧に「新しいデザインを作る」を置く。名前と種類を聞き、`layout/<フォルダ名>/` を作って次を置き、エクスプローラーで開く
  （開くのは `RevealBoardFile` と同じ SHELL の操作を一般化して使う）

| ファイル | 中身 |
|---|---|
| `index.html` / `index.css` / `index.js` | 種類ごとの最小のサンプル（各 50〜100 行。`autona.js` を使う）。AI が書き換える元 |
| `settings.json` | `autona` ブロック（`kind`・`contract: 1`・`displayName`・`author`・`license`）。ADR-007 D5 の取り込みの条件（`license` 必須・安全なフォルダ名・予約名でない）を最初から満たす形にし、人に渡すときに T3 で断られないようにする |
| `AGENTS.md` | AI への指示書。契約 1 の使い方・守る規則（`../include/` 以外の外部ファイルを読まない・CDN を使わない・使わない名前・1920×1080 で透過背景・`settings.json` を壊さない）・完成の条件（アプリのプレビューでエラーが 0）。エージェント（Claude Code / Cursor / Codex）が自動で読む |
| `CLAUDE.md` | `@AGENTS.md` だけ（Claude Code 用の入口） |
| `AI_PROMPT.md` | チャットの AI に**1 ファイルで貼れる**版（AGENTS.md・契約の表・サンプルを 1 つにまとめた生成物） |

- フォルダ名には kind の接頭辞を自動で付ける（BOARD `scoreboard_<名前>`・実況 `commentators_<名前>`・TOP 8 `bracket_top8_<名前>`）。
  `layout/` は種類ごとのサブフォルダに分けない（OBS に登録済みの URL・`../include/` の相対パス・保存済みのデザインの id が全部フォルダの位置に
  依存するため。種類の正本は `kind` だけにし、エクスプローラーでの見やすさは接頭辞で補う。2026-11-21 オーナー確認）
- キットの元は `layout/include/kit/` に置く（ひな形の名前は `AGENTS.md.tmpl` のように変え、このリポジトリで作業する開発用のエージェントに読み込まれないようにする。
  作るときに改名する）。`include/` はカタログが予約しているので一覧には出ない。`layout/` は git 管理なので配布 zip に入る
- 生成物（`AI_PROMPT.md` の元・`CONTRACT.md`）は D1 の対応表からスクリプトで作り、テストで「生成し直しても差分が出ない」ことを確かめる
- チャットの AI 向けに、一覧に「AI 用の説明をコピー」（`AI_PROMPT.md` の中身をクリップボードへ）も置く
- 書き込み先は `layout/` の新しいフォルダだけ（既にあれば断る。フォルダ名は ADR-007 D5 と同じ安全な文字・予約名の規則）。ルートは SHELL
  （`layout/` に置いたものは OBS のオーバーレイと同じオリジンで出るので、レイアウトのスクリプトから操作させない。ADR-007 D5 と同じ理由）。
  ヘッドレスではフォルダを開く操作だけ断る（作るのはできる）
- ADR-007 T4（作り方の文書・最小のサンプル・ローカルでの確かめ方）の**オーバーレイの部分はこのキットが担う**。T4 はリザルト / サムネイルの文書と
  コミュニティの一覧に絞る
- L2 の実装: キットの元は `layout/include/kit/`。`AGENTS.md.tmpl`・`CLAUDE.md.tmpl`・`CONTRACT.md`（全キー）と、種類ごとのフォルダ
  `scoreboard/` / `commentary/` / `top8/`（`index.html` / `index.css` / `index.js` と `AI_PROMPT.md`）。`CONTRACT.md` と `AI_PROMPT.md` は
  `scripts/gen_layout_kit.py` の生成物を**コミットする**（作るときは写すだけ。「AI 用の説明をコピー」も同じファイルを読む）。`AI_PROMPT.md` は
  種類ごとに、指示書（見出しを 1 段下げる）・その種類で使うまとまり（`board` / `commentary` / `top8` のどれかと `tournament`・`options`）の
  キーの表・その種類のサンプル 3 ファイル・「3 ファイルの全文を返す」の依頼を 1 つにしたもの。指示書は種類に依らない 1 枚（種類は `settings.json`
  の `autona.kind` で読ませる）。`tests/test_layout_kit.py` が生成し直した結果との一致（改行コードは除く）と、サンプルが本物の
  `globals.js` + `autona.js` の上で 3 つのサンプルの state（BOARD はボード 2・3 も）をエラーなしに描くこと（node の vm。`test_overlay_contract.py` の仕組み）を固定する
- L2 の実装: 新しいデザインのフォルダには表のファイルに加えて `CONTRACT.md` も置く（`AGENTS.md` がキーの全量としてこれを指す）。
  生成物の先頭の「Generated by ... Do not edit」の行は、フォルダに写すときと「AI 用の説明をコピー」の文から外す。
  フォルダ名（v1 の `name`）は `[A-Za-z0-9][A-Za-z0-9_-]*` の 1〜40 文字（URL にそのまま入る文字だけ）。表示名（v1 の `displayName`。任意）は
  日本語も可で、前後の空白を除いて 64 文字まで・制御文字は不可（`bad_display_name`）。無い・空なら `name`。ダイアログは表示名が ASCII なら
  そこからフォルダ名を下書きし、そうでなければ空にして入力を促す。`author` / `license` の初期値は `unknown` / `All rights reserved`
  （人に渡すときに埋める）
- L2 の実装: キットの指示書には、`[hidden] { display: none !important; }`（`display: flex` と `hidden` 属性の衝突。受け入れ試験で 3 種とも
  AI がはまった）、同梱フォントの一覧（`../include/` の 3 つは英数字だけ。日本語は `../../assets/font/` の LINE Seed JP と源暎Mゴシック。
  外部の Web フォントは不可、OS のフォント名を並べるのは可）、`iconName` の値（`mic` / `megaphone` / `phone`。画像は `view` に無いので自前の SVG）、
  テーマ色を CSS 変数で受ける例（固定の色でもよい）、TOP 8 の進行中・未定の枠・`gf` / `gf_reset` の置き方、OBS には http の URL を
  ブラウザソースの URL 欄に入れること、を書く
- L2 の実装: TOP 8 の「今やっている試合」は state に無い（`top8.matches.{ID}.active` は `gf_reset` を行うかだけ。`core/Top8Model.py`。
  同梱の `bracket_top8` も進行中を描かない）。契約にキーは足さず、「両方の `slot` が埋まり `finished` が `false`」を進行中とみなすと指示書に書いた
- L2 の実装: 予約名は `core/LayoutKit.RESERVED_DIRS`（カタログの予約 4 つと、配布に入る `layout/` 直下のフォルダ全部。自動更新が上書きするため）。
  大文字小文字を区別せずに比べ、同じ名前のファイル・フォルダがあれば `exists`。作業フォルダ `layout/.new-design-<乱数>/` で組んでから
  `os.rename` で置く（途中で同じ名前ができたら置き換えずに `exists`。`PermissionError`（索引・ウイルス対策が一瞬ファイルを持つ）は 0.1 秒おきに
  5 回まで試し直す。失敗したら作業フォルダを消す）。作るたびに 1 時間より古い作業フォルダ（落ちた作成の残り）を消す。カタログは
  `.new-design-` で始まるフォルダだけを一覧に出さない（`LayoutCatalog.NEW_DESIGN_WORK_PREFIX`）
- L2 の実装: **接頭辞（`scoreboard_` / `commentators_` / `bracket_top8_`）の付いた名前の同梱フォルダを足すときは、利用者が作ったフォルダと
  衝突しないか確かめる**（自動更新は `layout/` をマージで上書きするので、同じ名前の利用者のデザインを上書きする）。足したら
  `LayoutKit.RESERVED_DIRS` にも足す（`tests/test_layout_kit.py` が git の管理と照合する）
- L2 の実装: v1 は `POST /layouts:create`（201 で一覧の項目）・`GET /layouts/kit/{kind}/ai-prompt`・`POST /layouts:reveal`（`{"id"}`。一覧に
  ある id だけ）。3 本とも SHELL・no-store・API 一覧に載せない。拒否の `reason` は `bad_kind` / `bad_name` / `bad_display_name`（400）・
  `reserved` / `exists`（409）・`kit_missing`（キットが欠けている。何も作らない）/ `write_failed` / `not_listed`（作って置いたが一覧に出ない。
  フォルダは残す）（500）。フォルダを開くのは `LayoutCatalog.RevealLayoutFolder`（`RevealBoardFile` と同じ `_Reveal` を通る。
  ファイルを選ぶのでなくフォルダを開く）。Board の `board:reveal` は THIS_PC のまま変えない
- L2 の実装: **ヘッドレスでは 3 本とも SHELL の規則で 403**（ヘッドレスに共有ビューは無く SHELL は常に 403）。上の「作るのはできる」は core の
  操作（`LayoutKit.NewDesign`。Qt に依らない）の話で、v1 では作れない。作成を THIS_PC にすると同じオリジンのレイアウトのスクリプトが作れるため、
  SHELL を優先した
- L2 の実装: デザイン一覧（`DesignPane` の `actions`）に「新しいデザインを作る」（表示名とフォルダ名のダイアログ → 作成 → 一覧の読み直しと選択 →
  フォルダを開く。読み直しに失敗したら選ばない。`kit_missing` / `not_listed` でも一覧を読み直す）と「AI 用の説明をコピー」（種類の文を先に読んでおき、
  クリックの中で同期でクリップボードに書く）。`web/src/CommentaryPage/NewDesign.tsx`。どちらも共有ビューの中でだけ出す（`useShellAccess` の
  `shell`。ブラウザ・ヘッドレスでは SHELL が 403 なので出さない）。Stats のページには出さない

### D3: アプリの中で確かめられるようにする【論点 4】

- **一覧から外れた理由を出す**: `GET /api/v1/layouts` に、一覧に出さなかったフォルダと理由（入口が無い・ブロックが壊れている・知らない kind / contract・
  フォルダ名の規約にもブロックにも当たらない）を足す（キーの追加。`external-control-api.md` も直す）。デザイン一覧の下に「読み込めなかったデザイン」として出す
- **エラーを親に渡す**: `autona.js` は `onUpdate` の中の例外・`window.onerror`・`unhandledrejection` を捕まえ、iframe の中なら `postMessage` で親に渡す。
  読み込みが終わった時点で `ready` も送る。OBS の中では何もしない（画面にも出さない）
- **親の受け方**（今の「`message` のリスナーを足さない」の規則を変える。論点 4）:
  - 受けるのは `DesignPane` だけで、`event.source` がそのページの iframe の `contentWindow`（Board は 2 枚）と一致し、`event.origin` がプレビューのオリジンのものだけ
  - 形は固定（`{type: "autona:ready" | "autona:error", message, source, line}`）。文字列は長さに上限を付け、**テキストとしてだけ**描く（HTML にしない）。件数に上限を付ける
  - メッセージで動くのはエラーの欄の表示だけ（state・ナビゲーション・クリップボードには触らない）
  - 規則の文と `DesignPane.test.js` の固定は、この形に差し替える
- **確認の欄**: エラーを「レイアウト名・契約の番号・エラー文・該当行・ヒント」で並べ、「AI に貼る用にコピー」ボタンでそのまま渡せる文（日本語）をコピーする。
  `ready` が数秒来なければ「`autona.js` が読めていない（パスの間違い・先に読むスクリプトの構文エラー）」と出す（AI が作ったもので一番多い壊れ方）
- **サンプル / 実データの切り替え**: プレビューの URL に `autonaSample=<名前>` を付けると、`autona.js` が Socket の代わりにサンプルの state を使う。
  試合の無いときでも全部の表示を確かめられる。サンプルの正本は `layout/include/sample/*.json`（state の形）1 か所で、D1 のテストもこれを使う。
  `layout/` は本体とプレビュー用リスナーの両方が出すので配信の手当ては要らない
- **再読み込みと URL**: 一覧に「再読み込み」（カタログの再走査とプレビューの開き直し）と「OBS 用 URL をコピー」を置く。ファイルの変更を見張る自動更新は作らない
- L3 の実装: 外れた理由は `LayoutCatalog.Scan(kinds)`（一覧と同じ 1 回の走査）が集め、`GET /api/v1/layouts` の封筒の `data` の隣に `unlisted`
  （`[{folder, kind, reason, detail}]`）で返す。`reason` は `no_entry`（入口のファイルが無い）・`bad_settings`（`settings.json` が JSON のオブジェクトでない）・
  `bad_block`（`autona` ブロックがオブジェクトでない）・`unknown_kind`・`unknown_contract`（オーバーレイ・テンプレートとも）・`bad_template`（テンプレートの必須項目）・
  `bundled_id`（同梱テンプレートの id と同じ名前）・`no_manifest`（ブロックも接頭辞も無い）で、追加だけ（`LayoutCatalog.UNLISTED_REASONS`）。`detail` は英語の短い文
  （ファイル名・受けなかった値の `repr`・JSON のエラー。OSError はパスを含むので種類と `strerror` だけ）で 200 文字まで。`kind` はブロックか接頭辞で分かればそれ、
  分からなければ `null` で、`?kind=` はその種類と `null` のものを返す（ページには自分の種類と種類不明のものが出る）。`settings.json` が壊れていても接頭辞で拾えるフォルダは
  今までどおり一覧に出る（理由は出さない）。予約フォルダ（`custom/` を含む）・`.new-design-*`・ファイルに加え、**同梱の非デザインのフォルダ**（統計の 5 つ・
  `winner_popup`・`camborder_Universal`。`LayoutCatalog.SHIPPED_NON_DESIGNS`。ブロックも接頭辞も無いので `no_manifest` になる）は出さない。
  `tests/test_layout_catalog_unlisted.py` が、同梱の `layout/` が 1 件も出さないことを固定する
- L3 の実装: **旧版で同梱して後で削除したフォルダ**（`bracket_top12` / `bracket_top16` / `top_8` など 38。`LayoutCatalog.RETIRED_SHIPPED_DIRS`）も
  理由を出さない。自動更新は消したファイルを利用者の手元に残すので、出すと全ページに `no_manifest` が出続ける。リリースが同梱をやめたフォルダはここに足す
  （テストが git の履歴の削除と照合する。浅いクローンでは照合が弱くなるだけ）。接頭辞と入口のあるものは今までどおり名前で一覧に出る。
  `no_manifest` は `settings.json` か `.html` のファイルがあるフォルダに限る（画像やフォントを置いただけのフォルダはデザインの失敗ではない）
- L3 の実装: **一覧に出た項目の manifest の警告**: `settings.json` が読めない・ブロックが壊れている・知らない kind でもフォルダ名の規約で一覧に出るもの、
  `entry` がファイル名でないもの（`index.html` に代える）は、項目に `warnings: [{reason, detail}]`（`unlisted` と同じコードと `bad_entry`。無ければキーごと無い）を
  載せる。`DesignPane` はそのデザインの確認の欄に、読み込めなかったデザインと同じ文で出し（`settings.json: …`・detail・ヒント）、「AI に貼る用にコピー」にも入れる。
  警告があるときは `ready` を受けても「エラーはありません」を出さない
- L3 の実装: 確認の欄の報告・`ready`・タイムアウトは、フレームが読む文書ごとの鍵（文書の URL と、表示し直すたびに増やす番号）に結びつける。同じデザイン
  （A → B → A）やサンプル（実データ → teams → 実データ）に戻っても前の報告を持ち越さない。サンプルの選択はデザインごとで、デザインを替えたら実データに戻す。
  受ける側の上限: 同じエラーの回数と「ほかに N 件」は 999 で止め、それより後のメッセージでは描き直さない。実況・TOP 8 の再読み込みは、読み直した一覧の同じ id の
  項目で表示中のデザインを置き換える（`contract`・`warnings` の反映）。OBS 用 URL のホストは既存の「OBS に追加する方法」のカードも含め `127.0.0.1` に揃えた
- L3 の実装: オーバーレイの一覧の項目は、ブロックが `contract` を宣言していれば `contract` を持つ（L1 の「項目の形は変えない」からの追加。キットで作ったデザインは宣言する）。
  宣言は「`autona.js` を使う」という表明として、`ready` を待つかどうかに使う（下）
- L3 の実装: `autona.js` は iframe の中（`window.parent` があり自分でない）のときだけ `postMessage` する。形は固定で
  `{type: "autona:ready" | "autona:error", contract, kind, message, source, line, column}`（`kind` は `ready` / `onUpdate` / `error` / `unhandledrejection` / `autona`）。
  `message` は 500 文字・`source` は 300 文字まで（同じオリジンのスクリプトはパスに縮める）、エラーは 1 ページ 50 件まで。`onUpdate` の例外は `console.error` に出すとき
  （同じ関数は成功するまで 1 回）だけ送る。`window` の `error` はスクリプトのエラー（`message` を持つ `ErrorEvent`）だけで、画像などの読み込み失敗は送らない。
  `autona.js` 自身の失敗（`globals.js` が先に無い・読む順番の間違い・サンプルが無い・state を読めない）も `kind: "autona"` で送り、`globals.js` が無いときは `ready` を送らない。
  `ready` はページの解析が終わってレイアウトのスクリプトが走った後（`LoadEverything()` を呼ぶ直前）に送る
- L3 の実装: **targetOrigin は `"*"`**。親（本体の Web UI）のオリジンは `localhost` / `127.0.0.1` / LAN の名前とポートで変わり、レイアウトの側から決め打ちできない。
  送るのはそのレイアウト自身のエラーの文と行だけで、そのレイアウトを埋め込めるページはプレビュー用リスナーから同じ state もコードも読める（READ）ので、漏れて困るものを
  足さない。受け手の照合（送り元・オリジン・形）で守る。OBS は親が無いので何も送らない
- L3 の実装: `DesignPane` は自分の iframe（デザインと、Board はポップアップ）の `contentWindow` と `event.source` が一致し、`event.origin` がプレビューのオリジンのものだけを受け、
  形がちょうど上のもの（キーの過不足・型の違いは捨てる。`LayoutCheck.parseLayoutMessage`）だけを使う。受けて変わるのは確認の欄だけ。表示する文書（デザイン・
  再読み込み・サンプル）が変われば欄を空にする。同じエラーは回数を数え、別のエラーは 20 件まで（超えた数は「ほかに N 件」）。ポップアップの `ready` はデザインのものとしない
- L3 の実装: **`ready` の誤検出の対策**: `ready` を待つのは「一覧の項目が `contract` を宣言している」か「このページでそのデザインから `ready` を一度受けた」デザインだけで、
  5 秒（`READY_TIMEOUT_MS`）来なければ「`autona.js` が読めていない」を出す。どちらでもないデザイン（同梱・旧来の `globals.js` だけのもの）は何も言わない限り欄を出さず、
  エラーを受けたときだけ出す。`autona.js` を使わないのに `contract` を宣言したデザインは誤って出るので、ヒントに「使わないなら `contract` を書かない」と書いた
- L3 の実装: 欄はレイアウト名・契約の番号（受けた `contract`、無ければ項目の `contract`）・エラーの種類と文・場所（パス・行・列）・回数・ヒント（文の型から選ぶ:
  未定義の名前・undefined / null の読み取り・構文・凍結・読む順番・サンプル名ほか）。テキストとしてだけ描く。「AI に貼る用にコピー」はページの言語に依らず日本語
  （キットの指示書と同じ。`i18n.getFixedT("ja")`）で、クリックの中で同期で書く。`ready` を受けてエラーが無ければ「エラーはありません」を出す
- L3 の実装: 「プレビューのデータ」（実データ / `singles` / `teams` / `empty`）は `autona.js` を使うと分かっているデザイン（上の 2 条件）だけに出し、デザインの iframe に
  `?autonaSample=` を付ける。BOARD・実況・TOP 8 のどれでも動く（SHELL ではないのでブラウザ・ヘッドレスでも出る）
- L3 の実装: 再読み込みは、実況と TOP 8 に「デザインを再読み込み」（一覧の読み直しとフレームの開き直し）を足した。BOARD は既にある「再読み込み」（一覧・選択・
  フレーム）がそれを兼ねるので足さない。「OBS 用 URL をコピー」は **BOARD にだけ**足した（ボード 1〜3 のメニューで
  `http://127.0.0.1:<ポート>/layout/<id>/<entry>?scoreboardNumber=N`）。実況・TOP 8 は「OBS に追加する方法」のカードに http の URL のコピーが既にあるので重ねない。
  BOARD の「OBS に追加する方法」の file:// の手順は同梱・旧来のデザイン向けに残し、「`autona.js` を使うデザインは URL 欄に http の URL を入れる」の注記を足した

### D4: 入れ方は今の 2 つのまま

- 自分（自分の AI）で作ったものは、D2 で `layout/` に作ったフォルダをそのまま使う（手で置くのと同じ。信頼の前提も同じ）
- 人からもらったものは ADR-007 T3 の **zip の取り込み**。この ADR は取り込みの仕組みを足さない

### D5: Designer との関係

- Designer は「AI を使わずに作る」経路として残す。どちらで作っても同じ一覧に出る
- Designer の 21 語をビューから読む形に寄せるのは段階 L4（任意）。語彙の意味がビューと違う（ボード 1 固定・表示の切り替えを中で効かせる・整形）ので、
  寄せる前に 21 語それぞれの今の出力の golden を作り、違いの埋め方を決めてから行う。`project.json` の形は変えない

### D6: 段階分け（1 段階 = 1 PR）

ADR-007 の T2（描画の隔離）・T3（zip 取り込み）とは独立に進められる。L1〜L3 は自分で置くフォルダの話で、信頼の前提は今と同じため T2 を待たない。
L2 と L3 はどちらもデザイン一覧の UI と SHELL のルートに触れるので直列にする。

| 段階 | 内容 | 検証 | 区分 |
|------|------|------|------|
| **L1 契約** | `autona.js`（ビュー・`onUpdate`・`Autona.raw`・`autonaSample`）、対応表 `overlay_contract.json`、サンプルの state `layout/include/sample/`、ビューの期待値、マニフェストの `contract`（オーバーレイ）、`layout/README.md` の更新 | pytest（node の vm）: state → ビュー（単独戦・団体戦・crew・値の欠け・ボード 2）、差分の更新で `onUpdate` が呼ばれること、`Update` を定義したレイアウトでも呼ばれること。pytest: 表の元のパスと `KEY_CATALOG`、知らない contract を一覧に出さないこと。実機: 隔離データで `autona.js` を使った試作レイアウトを OBS 用 URL で開き、スコアの変更が反映されること | 通常 |
| **L2 キット** | `layout/include/kit/`、生成スクリプト（`CONTRACT.md`・`AI_PROMPT.md`）、「新しいデザインを作る」（core の操作・SHELL のルート・ROUTE_ACCESS・v1）、フォルダを開く操作の一般化、「AI 用の説明をコピー」、`validate_release_zip.ps1` の必須エントリ | 生成物の差分なしのテスト、作る操作の pytest（既にある名前・予約名・不正な文字・ルート台帳とアクセス区分）、jest。**受け入れ: `AI_PROMPT.md` だけをチャットの AI に渡し、指定したデザインが手直しなしで動くこと**（3 種で試す） | 高（`layout/` への新しい書き込み口） |
| **L3 アプリ内の確認** | 一覧から外れた理由（v1 の追加と文書）、`autona.js` のエラー捕捉と `postMessage`、`DesignPane` の受け方とエラーの欄・コピー、規則と固定のテストの差し替え、再読み込み・URL のコピー | jest: 送り元とオリジンの照合（別のフレーム・入れ子のフレーム・形の違うメッセージを捨てる）・長さと件数の上限・テキストとしてだけ描くこと・コピーの文。pytest: 外れた理由の各分類。E2E: エラーを起こす試作レイアウトでエラーの欄に出ること、`autona.js` を読まないレイアウトで「読めていない」が出ること、`autonaSample` でサンプルが出ること。実機: 隔離データで 3 ページを確かめる | 高（共有ビューの `message` の規則を変える） |
| **L4 Designer の寄せ** | 21 語の golden、`resolveBinding()` をビュー経由に | 21 語の golden が変わらないこと、Designer 製デザインの表示が変わらないこと | 通常 |

- L4 は任意。L1〜L3 の後、語彙の二重管理が問題になったら行う

### D7: 描画系（サムネイル・リザルト）にも広げ、描画の方式をオーバーレイと揃える（2026-11-23 追記・オーナー承認）

依頼は「対戦動画サムネイルもレイアウト追加機能がほしい」。サムネイル・リザルトは ADR-007 の contract 1 で、file:// のオフスクリーン描画・
`initThumbnail` / `initResult`・`RENDER_READY` の合図で動いている。今の仕組みにキットとエラー表示を後付けすると、
エラーの経路（postMessage が使えない）・画像のパスの扱い（相対・file URL・絶対パスの混在）・AI 向けの約束がオーバーレイと二重になる。
同梱の `thumbnail.js` / `result.js` は例外を握りつぶして `RENDER_READY` を出すので、描きかけの絵が成功として撮られ、エラーはログにしか残らない。
そこで後付けをやめ、**描画の方式をオーバーレイと揃えてから**キットを載せる。

- **R1 = ADR-007 T2**: 描画を `http://127.0.0.1:<previewPort>/layout/<フォルダ>/<entry>` で行い、payload のパスを読める URL に揃える
  （配信していないアップロードの背景・アーカイブの `_bg.jpg` は描画 1 回ぶんの枠）。内容と検証は ADR-007 D4 / D6 のとおり。
  contract 1 のテンプレート（同梱 2+1 種・ユーザーが置いたもの）は `../../` の相対パスのまま読めることを保つ
- **R2 描画用の契約（contract 2）**: `autona.js` に `Autona.onRender(payload => …)` を足す。描き終わりの合図・画像とフォントの待ち・
  例外の捕捉（例外のときは成功の合図を出さずエラーとして返す）は部品が持つ。オフスクリーン描画はエラーを集めて描画結果に載せる。
  Web UI のプレビューは DesignPane の iframe（payload はサーバーから受ける）にして、L3 のエラーの欄と「AI に貼る用にコピー」を使い回す。
  本番の PNG は今までどおりオフスクリーン描画（デスクトップのみ）。iframe のプレビューはヘッドレスでも出せる見込み。
  contract 1 は読み続ける（移行は任意）
- **R3 キット**: L2 と同じ形で `thumbnail_` の接頭辞・`include/kit/thumbnail/`・AI_PROMPT.md（payload の表は契約から生成）・
  サンプルの payload を足し、サムネイルのページに「新しいデザインを作る」を置く。受け入れ試験は L2 と同じ（AI_PROMPT.md だけで作らせる）
- リザルトは R2 の上に R3 と同じ形で後から足す（R4・任意）

| 段階 | 内容 | 区分 |
|------|------|------|
| **R1 描画の http 化** | ADR-007 T2 の全部 | 高 |
| **R2 描画用の契約** | `Autona.onRender`・contract 2・描画結果のエラー・サムネイルのページの iframe プレビュー | 高 |
| **R3 サムネイルのキット** | 接頭辞・キット・「新しいデザインを作る」・受け入れ試験 | 高 |
| **R4 リザルトのキット** | R3 と同じ形 | 通常 |

**R2 の実装**（2026-11-24）:

- **契約の見分け方はマニフェストの `contract`**（`settings.json` の `autona.contract`。`LayoutCatalog.TEMPLATE_CONTRACTS = (1, 2)`。
  レンダラーはカタログのエントリの値で分岐する）。ページの中身（`Autona.onRender` を呼んだか・`initThumbnail` があるか）で推測しない:
  読み込みの途中で落ちたページを契約違いと取り違えず、「autona.js を読めていない」と正しく言えるため。知らない番号は一覧に出さない（D3 のまま）
- **contract 2 の描画**: レンダラーはページを読み終えたら `Autona._render(payload, serial)` を呼ぶ。`autona.js` が `onRender` の関数を
  呼び、返った promise・フォント（`document.fonts.ready`）・`<img>` と CSS の背景の decode を待ち（上限 5 秒）、タイトルを
  `AUTONA_RENDER <serial> ok | failed` にする。`RENDER_READY` は contract 2 のページでは無視する（撮るタイミングは contract 1 と同じ
  `RENDER_PAINTED`）。`failed` のときは `Autona._renderResult(serial)` でエラーを読み、絵を撮らない。例外（同期の throw・promise の失敗・
  描画中の `error` / `unhandledrejection` イベント）・`onRender` を呼んでいない・**最初の描画より前**（読み込み中）のエラーが失敗になる。
  描き終えた後のエラー（`requestAnimationFrame` / `setTimeout` / 待たれない promise）は**その描画の**エラーとして足し、次の描画には
  持ち越さない（`Autona._renderResult` は直近 4 回ぶんを持つ）。レンダラーは `ok` の後、撮る直前（`RENDER_PAINTED` の後）に
  `_renderResult` を読み直し、エラーがあれば `TemplateError` にする。タイムアウトのときも先に読み、捕まえたエラーがあれば
  `TemplateError`、無ければ `render_timeout`（どちらもページを読み直す）。描画は **1 回ずつ**（描画中に来た分は最新の 1 件だけ待たせ、
  間の serial は描かない。古い描画が新しい描画を上書きしない）。
  `autona.js` が無いページは呼び出しのスクリプトがタイトルを `AUTONA_RENDER <serial> missing` にし、即座に失敗にする（タイムアウトを待たない）
- **レンダラーはページを読み直す**: 同じテンプレート ID でも、前回 `missing` だった・カタログの `contract` が読み込んだ時と違う・
  フォルダのファイル（`settings.json` 以外。大きさの変更は `_fit_view` が追う）の大きさか mtime が変わった、のどれかなら読み直す。
  編集したテンプレートを「生成」したときに古いページで撮らないため。`layout/include/`（`autona.js`）は `no-cache`（`max-age=0` で
  再検証、変わっていなければ 304）で配り、更新後に古い部品で描かない
- **エラーの経路**: `RenderLane.TemplateError`（値は `template_error`。`TEMPLATE_ERROR`）が `.errors` を持ち、`done(pixmap, error)` を
  そのまま通る。`RenderLane` が `thumbnail.render.errors` / `result_render.errors` に載せる（`{kind, message, source, line, column}`。
  20 件・message 500 字・source 300 字で切る。行・列が分からなければ 0。`StateSchema` の `Read*Render` がリストとして読む）。
  ログには件数と kind だけ（message は選手名・パスを含みうる）。Web は `thumbnail_error_template_error` / `result_error_template_error` と、
  `LayoutCheck.reportFromErrors` で L3 のエラーの欄（`_template` の文言・`onRender` のヒント・AI 用のコピー）に出す。
  サムネイルのページとリザルトのページ（プレビュー・生成が `template_error` で落ちたとき。同梱のリザルトは R4 まで contract 1 なので
  出るのはユーザーが置いた contract 2 のもの）の両方。SCORE ボタンの投稿（`ScorePage`）と旧来の同期の生成（`DrawThumbnailNow`）は
  1 件目の message を添えて失敗を言う（ログには `template_error` の値だけ）
- **errors の message は state で全接続に配る（受け入れる）**: `thumbnail.render.errors` / `result_render.errors` は state なので、
  WebSocket の購読者（LAN のブラウザ・オーバーレイ）にも届き、message は選手名やパスを含みうる。受け入れる: 同じ内容は
  READ で読めるプレビューの PNG（`GET /api/v1/thumbnail/preview`）に描かれており、エラーの message はそれ以上のものを足さない。
  payload のルートを PLAYERDB にしたのは別の理由（未反映の編集＝まだどこにも出していない値と、選手 DB の全体に近い内容を一度に返すため）で、
  両者は「既に出ている内容と同じか」で線を引いており矛盾しない
- **iframe のプレビュー**: サムネイルのページは選んだテンプレートが contract 2 なら、レンダラーに頼まず `GET /api/v1/thumbnail/payload`
  （`ThumbnailService.PreviewPayload`）で payload を受け、`DesignPane`（選択欄なし・テンプレートの大きさ）の iframe に
  `{type: "autona:render", serial, payload}` を `postMessage` する（プレビューのオリジン宛て・`autona:ready` の後・serial ごとに 1 回。
  2 回目以降の serial はエラーの欄を空にしてから）。iframe の URL は `?autonaParent=<ページのオリジン>` を持ち、`autona.js` は
  親（`window.parent`）から、そのオリジン（`event.origin` と一致）の、ちょうどこの形で serial が増えるものだけを受け、エラーと
  `{type: "autona:rendered", contract: 2, serial, ok}` をそのオリジン宛てに返す（`autonaParent` が無いか不正なら描画を受けない）。
  ページは `autona:rendered` が今の serial に届くまで「エラーはありません」を出さず「描画中…」と出す。再読み込みのボタン
  （`onReload`）はページと payload を読み直す。最初のプレビューはカタログ（テンプレートの契約）を読み終えてから出す。
  payload が 401 / 403（LAN でトークンなし）か contract 2 でなかったときは、そのテンプレートはレンダラーの画像
  （`GET /thumbnail/preview`、READ）でプレビューする（トークンの案内は 1 回）。
  レンダラーが要らないのでヘッドレスでも LAN のブラウザでも出る（生成ボタンはデスクトップのまま）。本番の PNG は今までどおりオフスクリーン描画。
  contract 1 のテンプレート（ユーザーが置いたもの）は今までどおりレンダラーの画像でプレビューする
- **payload のルートは PLAYERDB**: 未反映の編集（held のライブ値）と選手名を含むため。v1 の READ にするとプレビューのリスナーと
  第三者のレイアウトから読める。描画は要らないのでヘッドレスでも 200
- **配信していない背景は出さない（安全側）**: `RenderAssets.ForPreview` は `ForRender` と同じ読み方で、**何も持たない**。
  持つはずだったファイル（アップロードした背景・旧版の絶対パス）は `""`（テンプレートの既定を描く）にし、キーを `omitted` で返す。
  ページはキーごとの文言（`thumbnail_live_omitted_background_path` / `_main_icon_path`）で「このプレビューに出ない（生成した画像には入る）」と出す。
  不採用: プレビューのリスナーで `user_data/thumbnail_background_*` を配る（LAN 有効時は全アドレスに bind しているので、トークンなしで
  LAN の誰でも読める。`/render-asset/` はループバックかつ描画中だけという R1 の線を崩す）
- **テンプレートのフォルダに CSP**: kind `thumbnail` / `result` のフォルダ（`LayoutCatalog.IsTemplateFolder`。一覧に出ないものも）の
  ファイルは両方のポートで `WebAccess.TEMPLATE_CONTENT_POLICY`（`default-src 'self'`・`connect-src 'self'`・`worker-src` / `frame-src` /
  `object-src` / `form-action` は `'none'` ほか）を付け、`no-cache` で配る（`IsTemplateFolder` はマニフェストの大きさと mtime で答えを覚え、
  要求ごとに `settings.json` を読まない）。ブラウザの iframe にはレンダラーの要求フィルタが無いので、
  ADR-007 論点 3 = A（外部へ送らない）をここで保つ。**残るリスク**: フレーム自身の別の URL への遷移（`navigate-to` は無い。
  sandbox に `allow-top-navigation` は無いので親は動かせない）と WebRTC（R1 と同じ）
- **同梱のサムネイル（type_a / type_b）は contract 2 へ移した**（同じ描画を `Autona.onRender` で。contract 1 の写しと画素が同じことを
  `tests/test_render_platform.py` で確かめる。背景の `<img>` はマークアップでなく DOM で作る: パスは操作者の文字列）。リザルトは contract 1 のまま（R4 で移す）。contract 1 の `../../` の相対パスは読み続ける
- pin: `tests/test_template_contract.py`（node の vm。描画後のエラーの帰属・1 回ずつの描画・`autonaParent` の検査）・
  `tests/test_thumbnail_render.py`（実 QtWebEngine の contract 2。撮る直前の読み直し・タイムアウト時のエラー・ページの読み直し）・
  `tests/test_api_v1_thumbnail.py`・`tests/test_web_access.py`・`tests/test_template_packages.py`・`DesignPane.test.js`・
  `ThumbnailPage.test.js`・`ResultPage.test.js`・`ScorePage.test.js`・
  `web/e2e/thumbnail.spec.ts`（ヘッドレスの実ブラウザで、CSP の下で同梱の type_a が描けること・例外が確認の欄に出ること）

**R3 の実装**（2026-11-24）:

- **payload の表 `layout/include/thumbnail_contract.json`**（D1 の `overlay_contract.json` と同じ役割。契約 2 の正本の表）: `data` / `config` /
  `template` の 3 つに分けたキー・型・出どころ（`data` は state のキー、`config` / `template` は ThumbnailService）・日本語の説明と、見本の config
  （`sampleConfig`）。`data` はサムネイルに使うキーだけを書く（本物の `data` は state そのもので、ほかのキーもある・無いことも `null` のこともある）。
  `config` は表のキーが毎回ある（ほかのキーもありうる）。契約 2 はキーを足すだけ（改名・削除・意味の変更は契約 3）。`tests/test_thumbnail_kit.py` が表をコードに縛る:
  表のキーの凍結した写し（`TABLE_KEYS`。行の削除・改名で落ちる。足したら写しにも足す）、`data` の出どころは `KEY_CATALOG` のキー
  （`test_overlay_contract.py` と同じ規則）、`config` の行は `ThumbnailService` が描画とプレビューに渡す config（`RenderPayload` /
  `PreviewPayload` を実際に通す。何も保存していないとき・全部を保存したとき、と `_Config` → `_AsRead` で背景の 3 つの出どころ）に毎回あり、
  表に無いキーはテストに名前を書いたもの（`CONFIG_EXTRA` = 保存したテンプレートの `template_id`）だけ、`sampleConfig` は `_AsRead` を通しても変わらない
- **キット `layout/include/kit/thumbnail/`**: `thumbnail.html` / `.css` / `.js`（契約 2 の最小のサンプル。1on1・団体戦・データなしでエラーにならない）・
  `template.json`・指示書 `AGENTS.md.tmpl`（サムネイル用の 1 枚。フォルダ名・マニフェスト・`Autona.onRender`・payload・画像のパス・
  プレビューに出ない画像・CSP・フォント・大きさ・確かめ方と直し方・完成の条件）と、生成物 `sample_payload.json` / `CONTRACT.md` /
  `AI_PROMPT.md`（`scripts/gen_layout_kit.py` が表・サンプルの state `singles.json`・キットの `template.json` から作る。L2 と同じくコミットし、
  `tests/test_layout_kit.py` が生成し直した結果と照合する）。`CLAUDE.md.tmpl` はオーバーレイと共有（`@AGENTS.md` だけ）。新しいデザインには
  キットのフォルダのファイル全部と `AGENTS.md` / `CLAUDE.md` / `settings.json` を置く（`LayoutKit.KitFiles`）
- **L2 と違えた点**:
  - **一覧の条件は変えない**（ADR-007 D1: テンプレートパッケージはマニフェストだけで一覧に出す。フォルダ名の推測はしない）。接頭辞 `thumbnail_` は
    キットのフォルダ名と、一覧に出せなかったフォルダの種類の帰属（`LayoutCatalog.UNLISTED_PREFIXES` / `_UnlistedKind`）にだけ使う。
    `settings.json` が壊れた `thumbnail_*` はサムネイルのページの「読み込めなかったデザイン」に出て、オーバーレイのページには出ない
  - **マニフェストはテンプレートの形**: `kind: "thumbnail"`・`contract: 2`・`entry: "thumbnail.html"`・`size: [1920, 1080]`・
    `template: "template.json"` と `displayName` / `author` / `license`（`LayoutKit._Manifest`）。作った直後から一覧に契約 2 で出る
  - **サムネイルのページに「読み込めなかったデザイン」を足した**（テンプレートの選択の下。`useTemplatePackagesRead` が `unlisted` も返す）。
    AI が `settings.json` を壊したときに理由が見えないと直せないため。オーバーレイのページは D3 のまま
  - 作った後は一覧を読み直してそのテンプレートを選ぶ（`PATCH /api/v1/thumbnail {template_id}`）。プレビューの「デザインを再読み込み」は
    ページと payload に加えてテンプレートの一覧も読み直す（`settings.json` の直しを再起動なしで拾う）
  - 予約名: 同梱の `thumbnail_type_b` があるので `type_b` は `reserved`（`RESERVED_DIRS` は L2 のまま git の管理から決まる）
- **v1 は変えない**: `POST /layouts:create` の `kind` に `thumbnail` が増え、`GET /layouts/kit/thumbnail/ai-prompt` が読める（SHELL・no-store・
  ヘッドレスとブラウザは 403 のまま）。ページはボタンを共有ビューの中でだけ出す（L2 と同じ `useShellAccess`）
- **配布**: `scripts/validate_release_zip.ps1` の検査 3 にサムネイルのキットと `thumbnail_contract.json` を足した（`tests/test_release_bundle.py` が
  `LayoutKit.KitFiles` の全部と照合する）
- pin: `tests/test_thumbnail_kit.py`（表とコード・見本の payload・AI_PROMPT.md・作成と一覧・一覧に出せないフォルダ・v1）・
  `tests/test_layout_kit.py`（生成物の一致）・`tests/test_render_platform.py`（キットのまま作ったテンプレートを実レンダラーで 1on1・団体戦・
  データなしで描いてエラーが無いこと）・`ThumbnailPage.test.js`・`web/e2e/thumbnail.spec.ts`（ヘッドレスの実ブラウザで、キットのまま置いた
  テンプレートが CSP の下で描けて「エラーはありません」になること・ブラウザにはキットのボタンが出ず作成が 403 になること・壊れたフォルダが出ること）
- **受け入れ試験（メインが行う。L2 と同じ）**: 隔離データの `python main.py` のサムネイルのページで「新しいデザインを作る」→ 作ったフォルダの
  `AI_PROMPT.md`（「AI 用の説明をコピー」と同じ文）**だけ**をチャットの AI に渡し、「〇〇風のサムネイルにして」と頼む → 返ってきた 3 ファイルを
  フォルダに上書き → 「デザインを再読み込み」。確かめること: (1) プレビューに描かれ「レイアウトの確認」が「エラーはありません」、
  (2) 選手を団体戦（2 人以上）にしたとき・キャラを外したとき・試合をクリアしたときもエラーが出ない、(3) 「サムネイルを作成」で
  `out/thumbnails/` に画像ができ、プレビューと同じ絵、(4) 外部の CDN / Web フォントを使っていない（CSP の違反が確認の欄に出ない）、
  (5) エラーが出たら「AI に貼る用にコピー」の文だけを AI に返して直せる。AI がはまった点は指示書（`AGENTS.md.tmpl`）に足して生成し直す
- **受け入れ試験の結果と反映**（2026-11-24）: 別の AI に `AI_PROMPT.md` だけで「ネオン風」を作らせ、1 回目からエラーなしで描けた（オフスクリーンの
  PNG・ライブプレビュー）。その AI が挙げた不明点とレビューの指摘を指示書とサンプルに反映した:
  - **`settings.json` は書き換えない・返さない**（全文を返させると表示名・作者が見本の値で上書きされた）。大きさだけ、変える行を伝える。
    作った直後の `displayName` は入れた表示名（無ければ `<名前>`）だと書く
  - **デザインの方針**: 出す項目はテンプレートが決めてよいが、`display_phase` / `use_team_names` / `use_sponsors`（と `display_options.show_sponsor`）には従う。
    色は「`theme_color` / `accent_color` を主要な色に効かせ、補助の色はテンプレートが決めてよい」に 1 本化。既定の背景は CSS だけでもよい
    （`default_background` はサンプルのキーで Autona は読まない）。フェーズ・プール・ラウンドのつなぎ方と、団体戦で誰を出すかの指針を書いた
  - **キャラの配置（`config.game[codename]`）を効かせる**: サンプルの `placeCharacter` が flip（枠ごと反転）・zoom（枠を埋める大きさの %。100 未満は 100）・
    align（画像の `eyesight`、無ければ中心を枠の % の位置に置き、端に隙間を作らない）・smooth_scale（`false` で `pixelated`）を同梱の type_a と
    同じ考え方で当てる。表に `character.{K}.assets.{A}.eyesight` を足した（キーの追加）
  - **画像のパスは 2 種類**: payload の中のパスは Autona のフォルダ基準でサンプルの `resolve()` を通す（`/...`・`data:`・`blob:` はそのまま、
    ドライブのパス・`file:`・`http(s):`・`//` は `""`）。`template.json` とこのフォルダの CSS / HTML のパスはページ基準でそのまま使う
    （以前のサンプルは `template.json` のパスも `resolve()` に通しており、`./bg.png` がアプリの直下として解決された）。読めなかった画像（404）は
    確認の欄に出ないことも書いた
  - **`document.title` を変えない**を規則に足した。実レンダラーで、onRender の中・rAF のループ・1 ms のタイマーで title を書き換えるテンプレートを
    描いて確かめたところ、3 つとも普通に描けた（Qt の `titleChanged` は変化ごとに届き、レンダラーは自分の合図以外を読み捨てる）。実害は無いので
    `autona.js` は変えず、合図に使っていることを理由に指示書で避けさせるだけにした
  - そのほか: 2 回目以降は今のファイルを一緒に貼ってもらう（AI_PROMPT の冒頭）、開発者ツールはブラウザで `/web/app/thumbnail` を開いて F12
    （ポートは設定のネットワーク）、見本の payload の `data` は表のキーだけに絞って生成する、サムネイルのページの「読み込めなかったデザイン」は
    kind `thumbnail` のものだけ（kind 不明はオーバーレイのページのもの。`useTemplatePackagesRead` が kind で絞る）、作ったテンプレートは一覧の
    読み直しが終わって一覧にあるときだけ選ぶ（読み直しに失敗したら選ばない）

## Alternatives considered

- **キットと文書だけ作り、生の state のパスを書いて渡す**: 一番早いが、問題 2（約束が無い）が残る。内部の整理のたびに第三者のレイアウトが
  壊れ、壊れたことに Autona の側が気づけない。不採用
- **ビューを core で作る（新しい state のルート `overlay_view` か Socket のイベント）**: Python 側で 1 回作れば JS 以外（外部ツール）も使え、
  テストも Python だけで済む。しかし state のルートが増え、出力ファイルと差分の配信が太る。ヘッドレス・v1 にも波及する。
  Designer の対応づけが既に JS にある。不採用（論点 1。外部ツールが同じビューを欲しがったら見直す）
- **キットを別のフォルダ（配布 zip の `layout-kit/` やユーザーが選んだ場所）に書き出し、Autona なしで確かめる `preview.html` を付ける**:
  キットの中からは `../include/` が解決せず、file:// では JSON の読み込みがブラウザに止められ、`globals.js` は 17MB の辞書まで読む。
  自動更新は消したファイルを残すので、キットの版が利用者の手元でばらつく。作業場所を `layout/` にして確かめる場所をアプリに一本化するほうが単純。不採用
- **エラーを iframe の中だけに出す（親に渡さない。`message` の規則を変えない）**: 規則に触れないが、iframe の中でコピーのボタンを押すと
  フォーカスの隔離で外される。別オリジンの iframe からクリップボードにも書けない。AI に渡す文が取れない。不採用（論点 4）
- **Designer の機能を広げ、コードを書かずに全部作れるようにする**: 腕前を問わないが、自由度（アニメーション・独自の配置）は AI に頼むほうが高く、
  Designer を広げる量が大きい。範囲外（並べて残す）
- **アプリの中に AI を組み込み、文章からレイアウトを作る**: 作り手は一番楽だが、API キーの扱い・課金・出力の検査・モデルの選択を Autona が背負う。
  運営がふだん使っている AI をそのまま使えるほうが続けやすい。不採用（キットがあれば後から載せられる）
- **ファイルの変更を見張ってプレビューを自動で開き直す**: 便利だが、`layout/` 全体を見張る仕組み（スレッド・OS の通知）が増える。
  再読み込みのボタンで足りる。後回し

## Consequences

**良い影響:**

- 運営は「新しいデザインを作る」→ AI に頼む → アプリで確かめる → OBS 用 URL をコピー、だけでオーバーレイを足せる
- オーバーレイ向けの約束ができ、内部の state を整理しても契約 1 で書いたレイアウトは壊れない（壊れるならテストが落ちる）
- 一覧から消えた理由とエラーが見えるようになり、「動かない」を AI に渡せる文にできる
- サンプルデータで、試合の無いときでも表示を確かめられる（同梱レイアウトの開発にも効く）

**受け入れるリスク・コスト:**

- 契約 1 のビューが外部契約になり、変えにくくなる（追加だけ）。state の改名のたびに対応表を直す手間が増える
- `globals.js` と `autona.js` の 2 つの入口がしばらく並ぶ（同梱は旧来の書き方のまま）
- 共有ビューが初めてフレームからのメッセージを受ける。受けて動くのはエラーの欄の表示だけに絞り、送り元の照合と上限で守る
- `layout/` に新しい書き込み口（フォルダの作成）ができる。SHELL に限り、新しいフォルダだけに書く
- AI が書いたコードの品質は Autona が保証できない。キットは規則と確かめ方を渡すまでで、人に配るなら ADR-007 の取り込みの確認と同じ前提
  （「信頼できる配布元のものだけ」）に乗る
- 自分で作ったオーバーレイは、今までどおり本体と同じオリジンで動き、外部と通信できる（ADR-007 D5 の前提のまま）。キットの規則で
  「外部を読まない」を求めるが、強制はしない
- `autona.js` は file:// では動かない（`layout/README.md` の Chrome でのローカル確認は旧来の書き方のレイアウト向けとして残る）

## Revisit conditions

- 外部ツール（v1 を使う技術者）が同じビューを欲しがったら、ビューを core で作る案を見直す
- 契約 1 で表せない要望（統計・ブラケット全体など）が続いたら、ビューのまとまりを足す（追加なので contract はそのまま）
- 運営が作ったレイアウトの共有が増えたら、ADR-007 の Revisit（URL / 一覧からの取得）と合わせて考える
- 同梱のレイアウトを `autona.js` に移すかどうかは、L1〜L3 が使われてから判断する
- フレームからのメッセージで問題が出たら（偽のエラーで作り手を誤らせる等）、受け方を絞るか止める

## オーナーの判断が要った論点（2026-11-21 決定: 全部 A）

1. **ビューを作る場所**（D1）
   - **A（推奨・採用）: JS（`layout/include/autona.js`）**。core と配信に手が入らない。Designer の対応づけと同じ側に置ける
   - B: core（新しい state のルートか Socket のイベント）。外部ツールも同じビューを使えるが、state と配信が太り、ヘッドレス・v1 に波及する
2. **キットの渡し方**（D2）
   - **A（推奨・採用）: アプリの「新しいデザインを作る」で `layout/` に直接作る**。運営はフォルダを探さず、そのままアプリで確かめられる。`layout/` への書き込み口が 1 つ増える（区分「高」）
   - B: GitHub（リリースのアセット）でキットの zip を配り、運営が `layout/` に展開する。書き込み口は増えないが、運営が置き場所を間違えると `../include/` が解決せず動かない
3. **生の state の扱い**（D1）
   - **A（推奨・採用）: 塞がず、約束の外と明記する（`Autona.raw`）**。契約 1 に無いものも作り手が使える。壊れたら作り手の責任
   - B: キットでは使わせない（`AGENTS.md` で禁止し、`autona.js` から出さない）。壊れにくいが、契約 1 の範囲を超えるものは作れない
4. **共有ビューでフレームからのメッセージを受けるか**（D3）
   - **A（推奨・採用）: 受ける**。送り元の照合・固定の形・長さと件数の上限・テキストとしてだけ描く・エラーの欄にしか効かない、に絞る。「Web 側に `message` のリスナーを足さない」の規則を書き換える
   - B: 受けない。エラーは iframe の中に出すだけにする。規則は守れるが、AI に渡す文をコピーできず、問題 3 の半分しか解けない
