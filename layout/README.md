## Autona 配信オーバーレイ テンプレート

Autona が出力する状態データ（`out/` 配下のファイル）を読み取って表示する、
HTML/CSS/JS ベースのアニメーション付きオーバーレイテンプレート集です。
OBS 等のブラウザソースで読み込んで使用します。

`/include/` には各テンプレート共通で使うユーティリティ（`globals.js`）と、
利用ライブラリ（`gsap`・`jquery`）を同梱しています。リザルト画像・サムネイルの同梱テンプレートが共有する
`thumbnail_assets.js`・`result.js`・`result.css`・`result_team.css` もここにあります。

### 新しいデザインを作る（BOARD / 実況 / TOP 8。AI に頼んで作る）

1. アプリの BOARD / 実況 / TOP 8 のページで、デザイン一覧の下の「新しいデザインを作る」を押し、名前（半角英数字と `_` `-`）を入れる
2. `layout/<種類の接頭辞><名前>/`（`scoreboard_` / `commentators_` / `bracket_top8_`）ができ、一覧で選ばれ、エクスプローラーで開く。
   中身は動く最小のサンプル（`index.html` / `index.css` / `index.js`）・`settings.json`・AI への指示書 `AGENTS.md`（と `CLAUDE.md`）・
   キーの表 `CONTRACT.md`・チャットの AI に貼る用の `AI_PROMPT.md`
3. そのフォルダで AI のエージェント（Claude Code / Cursor / Codex）に作りたいものを頼む（`AGENTS.md` を自動で読む）。
   チャットの AI なら、一覧の「AI 用の説明をコピー」で貼って頼み、返ってきた 3 ファイルをフォルダに上書きする
4. アプリのプレビューと `http://127.0.0.1:5107/layout/<フォルダ名>/index.html` で確かめる（試合が無いときは URL に
   `?autonaSample=singles` / `teams` / `empty`）
5. **OBS には、ブラウザソースの「URL」欄にこの http の URL を入れる**（幅 1920・高さ 1080）。「ローカルファイル」で選ぶと、文字は出るが
   画像と見本のデータが出ない（キットのデザインは `autona.js` を使い、http で開いたときだけ全部動く）。BOARD のページの OBS の手順
   （ローカルファイル）は同梱のデザイン向け

キットの元は `include/kit/`（指示書のひな形 `AGENTS.md.tmpl` / `CLAUDE.md.tmpl`・種類ごとのサンプル）。`CONTRACT.md` と
`AI_PROMPT.md` は `include/overlay_contract.json` から `python scripts/gen_layout_kit.py` で作る生成物（手で直さない）。

### 新しいサムネイルのテンプレートを作る（AI に頼んで作る）

1. アプリのサムネイルのページで、「デザイン」の「テンプレート」の下の「新しいデザインを作る」を押し、名前（半角英数字と `_` `-`）を入れる
2. `layout/thumbnail_<名前>/` ができ、テンプレートに選ばれ、エクスプローラーで開く。中身は動く最小のサンプル
   （`thumbnail.html` / `thumbnail.css` / `thumbnail.js`・`template.json`）・`settings.json`（契約 2 のマニフェスト）・
   AI への指示書 `AGENTS.md`（と `CLAUDE.md`）・payload のキーの表 `CONTRACT.md`・payload の見本 `sample_payload.json`・
   チャットの AI に貼る用の `AI_PROMPT.md`
3. そのフォルダで AI のエージェントに頼むか、「AI 用の説明をコピー」でチャットの AI に貼って頼み、返ってきたファイルを上書きする
   （`settings.json` は上書きしない。2 回目以降の直しは、今の `thumbnail.html` / `.css` / `.js` も一緒に貼る）
4. プレビューの「デザインを再読み込み」で描き直す。エラーは「レイアウトの確認」の欄に出る（「AI に貼る用にコピー」で AI に返せる）。
   一覧に出なくなったら、テンプレートの選択の下の「読み込めなかったデザイン」に理由が出る
5. 「サムネイルを作成」で `out/thumbnails/` に画像ができる（アプリのウィンドウで）

キットの元は `include/kit/thumbnail/`（指示書のひな形 `AGENTS.md.tmpl`・サンプル）。`CONTRACT.md`・`AI_PROMPT.md`・`sample_payload.json` は
payload の表 `include/thumbnail_contract.json` から `python scripts/gen_layout_kit.py` で作る生成物（手で直さない）。書き方の詳細は下の
「テンプレートの契約 2」。

### 新しいリザルト画像のレイアウトを作る（AI に頼んで作る）

サムネイルと同じ流れで、リザルトのページの「レイアウト」の下の「新しいデザインを作る」から始める。

1. `layout/result_<名前>/` ができ、レイアウトに選ばれる。中身は動く最小のサンプル（`result.html` / `result.css` / `result.js`）・
   `settings.json`（契約 2 のマニフェスト。`rows: "players"`）・`AGENTS.md`（と `CLAUDE.md`）・`CONTRACT.md`・payload の見本
   `sample_payload.json`（個人 16 行）/ `sample_payload_teams.json`（チーム 8 行）・`AI_PROMPT.md`
2. AI に頼んで返ってきた 3 ファイルを上書きする（`settings.json` は上書きしない。チームのレイアウトにするときだけ `rows` を `"teams"` に変える）
3. プレビューの「デザインを再読み込み」で描き直す。行を増やす・減らす・同順位にする・キャラを外す・Top 16 のレイアウトにする、でも
   「レイアウトの確認」が「エラーはありません」のままか確かめる
4. 「作成」で `out/results/` に画像ができる（アプリのウィンドウで）

キットの元は `include/kit/result/`。`CONTRACT.md`・`AI_PROMPT.md`・2 つの見本の payload は payload の表 `include/result_contract.json` から
`python scripts/gen_layout_kit.py` で作る生成物（手で直さない）。

> 開発者向け: 接頭辞（`scoreboard_` / `commentators_` / `bracket_top8_` / `thumbnail_` / `result_`）の付いた名前の同梱フォルダを足すときは、利用者が作った
> フォルダとの衝突を確かめる（自動更新は `layout/` をマージで上書きする）。足したら `src/core/LayoutKit.py` の `RESERVED_DIRS` にも足す。

### ローカルでのテスト・デバッグ（Chrome）

- Chrome 起動時に `--allow-file-access-from-files` フラグを付ける
    - Windows: `"C:\PathTo\chrome.exe" --allow-file-access-from-files`
      もしくは PowerShell で `Start-Process "chrome.exe" "--allow-file-access-from-files"`
- レイアウトの `html` ファイルを Chrome で開き、F12 でデベロッパーツールを表示
- *Device toolbar* を有効化し、画面サイズを 1920x1080 に設定

これでコンソールログ（とエラー）の確認、CSS ルールの即時編集ができます。

### アプリのデザイン一覧に載せる（`settings.json` の `autona` ブロック）

BOARD / 実況 / TOP 8 タブのデザイン一覧と `GET /api/v1/layouts` は、各フォルダの
`settings.json` にある `autona` ブロックを読んで表示名などを決めます
（読み手は `src/core/LayoutCatalog.py`。ページ側の `globals.js` はこのキーを使いません）。

```json
{
  "autona": {
    "kind": "scoreboard",
    "displayName": "Simple",
    "format": "1v1 / チーム",
    "game": "汎用（全ゲーム）"
  }
}
```

| キー | 必須 | 内容 |
|---|---|---|
| `kind` | ○ | `scoreboard`（BOARD）/ `commentary`（実況）/ `top8`（TOP 8）/ `result`（リザルト画像）/ `thumbnail`（サムネイル）。`designer` は `layout/custom/` 専用で宣言できない。不正な値・壊れたブロックは警告ログを出し、ブロック無しとして扱う。`result` / `thumbnail` は下の「テンプレートパッケージ」の規則に従う |
| `displayName` | | 一覧の表示名。省略時はフォルダ名から作る（下記） |
| `format` | | 対戦形式（BOARD のメタ欄）。省略時は「—」 |
| `game` | | 対応ゲーム（BOARD のメタ欄）。省略時は「—」 |
| `entry` | | 入口の HTML ファイル名（フォルダ内のファイル名のみ）。省略時は `index.html`。フォルダの外を指す値は無視して `index.html` |
| `contract` | | BOARD / 実況 / TOP 8 で読むオーバーレイ契約の番号（下の「autona.js で作る」）。今は `1` だけで、省略は `1`。知らない番号のフォルダは警告ログを出して一覧に出ない（フォルダ名の規約でも拾わない） |

- 入口ファイルが無いフォルダは一覧に出ない。`custom/` `designer_runtime/` `include/` `icons/` はデザインとして扱わない
- **フォルダ名へのフォールバック**: ブロックが無い（または壊れている）フォルダは、従来のフォルダ名規約で拾う。
  `scoreboard*` → BOARD（`scoreboard_my_overlay` → `My Overlay`）、`commentators` / `commentators_*` → 実況、
  `bracket_top8` / `bracket_top8_*` → TOP 8（基本フォルダは「標準」、`commentators_dark-gold` → `Dark Gold`）。
  `displayName` だけ省略したときも同じ規則で名前を作り、規約に当たらないフォルダ名は `_` を空白にして
  単語の先頭を大文字にする（`lower_third` → `Lower Third`）。規約に当たらずブロックも無いフォルダは一覧に出ない
- **Designer で作ったデザイン**（`layout/custom/{slug}/`）はブロックを持たない。表示名は `project.json` の
  `name` を使って `Custom: {name}`（`name` が空なら slug）、対戦形式・ゲームは `meta.format` / `meta.game`
  （未設定なら「1v1」「カスタム」）。`meta` は Designer 左上の「対戦形式」「ゲーム」欄で編集する任意項目で、
  保存時に前後の空白を除き、空の項目は書かない（両方空なら `meta` ごと書かない）。各 64 文字まで。
  OBS 用ランタイムは `meta` を読まない
- **表示名の重複**: 同じ `kind` の中で表示名が重なったら、重なったもの全部を `{表示名} ({id})` と表示する
  （`id` は `layout/` からの相対パス。例: フォルダを複製した `scoreboard_simple - コピー` があると
  `Simple (scoreboard_simple)` と `Simple (scoreboard_simple - コピー)`）。重なりが無くなれば元に戻る。
  `kind` が違えば同名でもそのまま（実況と TOP 8 の「標準」など）

### autona.js で作る（BOARD / 実況 / TOP 8 のオーバーレイ契約 1）

`../include/autona.js` を読むと、state の生の形ではなく整えた形（ビュー）でデータを受け取れます。
キーは**足すだけ**の約束（契約 1）で、Autona の内部の state を整理しても、ビューだけを読むレイアウトは壊れません。

**読む順番は `globals.js` の直後、自分の `<script>` より前に `autona.js`**（自分のスクリプトが先に `LoadEverything()` を呼んでいると、
autona.js はコンソールに警告を 1 回出す）。

`layout/my_overlay/settings.json`（一覧に載せるためのブロック。上の表）:

```json
{"autona": {"kind": "scoreboard", "contract": 1, "displayName": "My Overlay"}}
```

`layout/my_overlay/index.html`:

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <script src="../include/globals.js"></script>
    <script src="../include/autona.js"></script>
  </head>
  <body>
    <div id="p1-name"></div><div id="p1-score"></div>
    <script>
      Autona.onUpdate((view, prev) => {
        document.querySelector("#p1-name").textContent = view.board.p1.name;
        document.querySelector("#p1-score").textContent = view.board.p1.score ?? 0;
      });
    </script>
  </body>
</html>
```

- `Autona.onUpdate((view, prev) => { ... })`: ビューが変わるたびに呼ばれる（最初は `prev` が `null`）。ビューに出ない state だけが変わったときは呼ばれない。
  最初の更新の後に登録した関数も、すぐ今のビューで 1 回呼ばれる。戻り値は登録を外す関数。
  `view` と `prev` は登録した関数のあいだで共有するので**書き換えられない**（凍結してある。並べ替えなどはコピーしてから）。
  関数の中の例外はコンソールに出る（同じ関数が失敗し続けても、成功するまでは 1 回だけ）
- **ビューのキーの表は [`include/overlay_contract.json`](include/overlay_contract.json)**（キー・型・値が無いときの値・元の state・説明 ja / en）。
  まとまりは `view.board`（URL の `scoreboardNumber` のボード。`p1` / `p2` と各 `players[]`、`bestOf`・`phase`・`round` など）・
  `view.tournament`・`view.commentary[]`（表示する人だけを表示順に）・`view.top8`・`view.options`（アプリの表示設定の素の値。ビューの値には
  効かせていないので、スポンサーを出すか等はレイアウトが決める）
- 値が無いときは文字列 `""`・数値 `null`・配列 `[]`・真偽値 `false`（`undefined` は出ない）
- 画像の値はそのまま `<img src>` に入る URL（`/assets/...`・`/user_data/...` のようなオリジンからの絶対パス）。そのため
  **autona.js は http で開いたときだけ対応**（アプリのプレビューと OBS 用 URL `http://127.0.0.1:5107/layout/<フォルダ>/index.html`。
  file:// で開くと画像が出ない）。OBS にはローカルファイルではなく URL で登録する
- `Autona.raw` は生の state（約束の外。版が変わると形が変わることがある）
- `LoadEverything()` は autona.js が呼ぶので書かなくてよい（同梱のレイアウトのように書いても 1 回しか走らない）。
  グローバルの `Start` / `Update` を定義しても `onUpdate` は止まらない
- **サンプルで確かめる**: URL に `?autonaSample=singles`（`include/sample/` のファイル名。`singles`・`teams`・`empty`）を付けると、
  アプリの state の代わりにサンプルを描く。ボード 3（クルーバトル）は `?autonaSample=teams&scoreboardNumber=3`
- **トップレベルで使わない名前**: `scoreboardNumber`・`autona` で始まる名前・`Start`・`Update`・`data`・`oldData`・`settings`。
  `globals.js` は URL のクエリを全部同じ名前のグローバル変数に代入し、ほかの名前は `globals.js` 自身が使っている
- **gsap を使うなら**: `globals.js` は最初の更新が来るまで `gsap.globalTimeline` を止めている（`timeScale(0)`）。最初の `onUpdate` より前に
  始めたアニメーションはそこまで動かない
- 同梱のレイアウトは今までどおり `globals.js` の `data` / `Update` を直接使っている（autona.js への移行はしていない）

### リザルト画像・サムネイルのテンプレートパッケージ（`kind`: `result` / `thumbnail`）

リザルト画像のレイアウトとサムネイルのテンプレートは、`layout/` 直下の 1 フォルダ（テンプレートパッケージ）です。
フォルダを置くと、リザルト / サムネイルのページの選択肢と `GET /api/v1/layouts?kind=result`（`thumbnail`）に出て、
そのデザインで画像を作れます（アプリの再起動は要りません。ページを開き直すと一覧が更新されます）。

```json
{
  "autona": {
    "kind": "result",
    "contract": 2,
    "displayName": {"ja": "ネオン TOP8", "en": "Neon TOP 8"},
    "entry": "result.html",
    "size": [1920, 1080],
    "rows": "players",
    "author": "your-name",
    "license": "MIT"
  }
}
```

| キー | 必須 | 内容 |
|---|---|---|
| `kind` | ○ | `result` / `thumbnail` |
| `contract` | ○ | テンプレートの契約の番号。`1`（`initResult` / `initThumbnail`）か `2`（`Autona.onRender`。新しく作るならこちら）。下記。知らない番号のパッケージは一覧に出ない |
| `size` | ○ | 出力画像の `[幅, 高さ]`（px の整数。各 1〜4096） |
| `rows` | result で ○ | `players`（単独。16 行まで渡る）/ `teams`（団体。8 行渡る） |
| `template` | thumbnail で ○ | 配置の数値の JSON（フォルダ内のファイル名だけ）。payload の `template` として渡る |
| `entry` | | 入口の HTML（フォルダ内のファイル名だけ）。省略時は `result.html` / `thumbnail.html` |
| `displayName` | | 一覧の表示名。文字列か `{"ja": ..., "en": ...}`（アプリの言語で選び、無ければもう一方）。省略時はフォルダ名から作る |
| `author` / `license` | | 作者とライセンス |

- **id はフォルダ名**（`settings.json` の保存値・`PATCH /api/v1/result` の `layout`・`PATCH /api/v1/thumbnail` の `template_id`）。
  同梱の 8 種だけは昔からの id（`horizontal` → `result/`、`horizontal_16` → `result_top16/`、`vertical` → `result_vertical/`、
  `vertical_geki` → `result_vertical_geki/`、`team` → `result_team/`、`team_split` → `result_team_split/`、`type_a` → `thumbnail/`、
  `type_b` → `thumbnail_type_b/`）で、表示名もアプリの文言です。同梱の id と同じ名前のフォルダ（例: `layout/horizontal/` に result）は
  一覧に出ません（同梱が優先。警告ログ）
- **マニフェストが必須**: フォルダ名の規約へのフォールバックはありません。必須のキーが欠けた・値が不正・入口や `template` の
  ファイルが無いパッケージは、警告ログを出してそのフォルダだけ一覧に出ません
- 選んでいたパッケージのフォルダを消すと、既定（`horizontal`、団体の対戦形式では `team`、サムネイルは `type_a`）で描きます。
  選んだ値は設定に残り、フォルダを戻すと戻ります
- **フォルダの外で読んでよいのは `../include/` だけ**（ほかのテンプレートのフォルダを読まない）。アプリの `assets/`
  （フォント）と payload が渡すパスは `../../` から読めます。`include/` のファイルはパッケージに入れない
  （特に `gsap.min.js` は再配布に条件があります）。`include/` に無いライブラリ・フォントはパッケージの中に入れ、ライセンスの文書も入れる
- サムネイルの `template` の JSON の中の画像（`default_background` など）は、パッケージの中か `assets/` の相対パスにする
- 描画は隔離されています。ページはアプリのプレビュー用のサーバー（`http://127.0.0.1:<プレビューのポート>/layout/<フォルダ>/<入口>`）
  から読まれ、読めるのはそのサーバーが出すもの（`layout/`・`assets/`・state の画像・v1 の読み取り）と payload の画像だけです。
  この PC のほかのファイル（file:）・ほかのポート・外部（CDN・Web フォント）への要求は届かず（Worker・Service Worker からも）、
  権限の要求は断られ、ウィンドウは開けず、ページはほかの文書へ移れません。ストレージ（localStorage など）は描画専用で、
  アプリのほかの画面とは共有しません。外部のライブラリ・フォントはパッケージの中に入れてください
- ただし WebRTC（STUN / TURN の UDP）は止められません。信頼できる配布元のテンプレートだけを置いてください
- プレビュー用のサーバーが動いていないとき（ポートを取れなかったとき）は描かずにエラー（`preview_unavailable`）になります
- テンプレートのフォルダのファイルには CSP（Content-Security-Policy）が付きます（ブラウザで開いたサムネイルのプレビューでも外部へ送らないため）。種類ごとに読めるものが違います:
  - スクリプト: 同じサーバーのファイルと、インラインの `<script>`・`eval`（`data:` / `blob:` のスクリプトと外部の CDN は不可）
  - スタイル: 同じサーバーのファイルとインラインの `<style>` / `style` 属性
  - 画像: 同じサーバーのファイルと `data:` / `blob:`。動画・音声も同じ
  - フォント: 同じサーバーのファイルと `data:`（Google Fonts などの Web フォントは読めない。パッケージに入れる）
  - `fetch` / XHR / WebSocket: 同じサーバーだけ
  - Worker・iframe・`<object>`・フォームの送信は使えない

#### テンプレートの契約 2（`Autona.onRender`。新しく作るならこちら）

サムネイルの同梱テンプレート（`thumbnail/`・`thumbnail_type_b/`）とリザルトの同梱レイアウト 6 種はこの形です。リザルトの payload のキーの表は `include/result_contract.json`（人が読む形は `include/kit/result/CONTRACT.md`。`data` はアプリが組み立てたもので、表に書いたキーがすべて）。サムネイルの payload のキーの表（型・説明。`data` は state そのものなので、ほかのキーもある）は `include/thumbnail_contract.json`（人が読む形はキットの `include/kit/thumbnail/CONTRACT.md`）。

```json
{"autona": {"kind": "thumbnail", "contract": 2, "size": [1920, 1080], "template": "type_a.json"}}
```

```html
<!-- thumbnail.html: autona.js を自分のスクリプトより前に読む -->
<script src="../include/autona.js"></script>
<script src="./thumbnail.js"></script>
```

```javascript
// thumbnail.js
Autona.onRender(async ({ data, config, template }) => {
  // payload は contract 1 と同じ形（下記）。描き終えたら return するだけ（合図は要らない）
  const board = data.score[config.scoreboardNumber || 1];
  document.getElementById("p1").textContent = board.team["1"].player["1"].name;
});
```

- `Autona.onRender(fn)` を 1 回呼ぶ。アプリは描くたびに `fn(payload)` を呼び、返った Promise・フォント（`document.fonts.ready`）・
  `<img>` と CSS の背景画像の読み込みを待って（最大 5 秒）から撮る。`RENDER_READY` も `initThumbnail` も要らない
- **例外は失敗**: `fn` の中の throw・返した Promise の失敗・描画中のスクリプトのエラーがあると画像を作らず、エラー（メッセージ・
  ファイル・行）がサムネイルのページに出る（「AI に貼る用にコピー」付き）。読み込み時のエラーと `onRender` を呼んでいないことも同じ。
  描き終えた後に `requestAnimationFrame` / `setTimeout` / 待たない Promise の中で起きたエラーも、その描画のエラーとして数える
  （アプリは撮る直前に確かめる）。描画は 1 回ずつで、描いている間に来た分は最新の 1 回だけ後で描く。
  contract 1 のように例外を握りつぶして描きかけの絵が出ることはない。画像が読めなかっただけ（404）はエラーにしない
- **サムネイル・リザルトのページのプレビューはブラウザで描く**: contract 2 のテンプレートはページの中の iframe（プレビュー用のサーバー）で
  同じ `fn` を呼んで表示する（アプリのウィンドウが無いヘッドレス版・LAN のブラウザでも出る。保存する画像はアプリが描く）。
  ブラウザの開発者ツールでそのまま確かめられる。アップロードした背景はプレビューには出ない（`config.background_path` が `""` になる。
  作る画像には入る）ので、`""` のときは既定の背景を描くこと。プレビューの iframe の URL には `?autonaParent=<ページのオリジン>` が付き、
  `autona.js` はそのオリジンからの描画の依頼だけを受ける（ページを直接開いただけでは描かれない）。ファイルを直したらプレビューの再読み込みのボタンで読み直す
- `../include/autona.js` が読めない（パスの間違い・その前のスクリプトの構文エラー）と「autona.js を読めていない」エラーになる

#### テンプレートの契約（contract 1）

- 入口のページは `initResult(payload)`（サムネイルは `initThumbnail(payload)`）を定義する。アプリはページを読み込んでから
  これを呼び、描き終えたら `document.title = "RENDER_READY"` にする（同梱のテンプレートと同じ合図）。撮るタイミングはアプリが決める
- `payload` の形は同梱のテンプレートが受け取っているもの（リザルト: `{data, config, layout}`、サムネイル:
  `{data, config, template}`）。`layout` はレイアウトの id（第三者のパッケージはフォルダ名）、リザルトの `data.rows` は
  `players` / `teams`（マニフェストの `rows`。団体として描くかはこれで決める）、`template` はマニフェストの `template` の JSON に
  `template_id` を足したもの。キーは今後**足すだけ**で、改名・削除・意味の変更は `contract` を上げる
- payload の中のパス（キャラの絵・背景・アイコン）は、アプリのフォルダからの相対パス（`./assets/...`・`user_data/...`・`./layout/...`）か、
  オリジンからの絶対パス `/render-asset/<id>` のどちらか（ADR-007 T2 から）。相対パスは今までどおり `"../../" + path` で読む
  （`config.background_path` / `main_icon_path` には更新時刻の `m=` が足されることがある）。`/render-asset/<id>` はプレビュー用の
  サーバーが出さないファイル（アップロードした背景・アーカイブ切り抜きの背景・旧版が保存した絶対パスの画像）で、その描画の間だけ
  読める。そのまま URL として使う（同梱のテンプレートの `resolveAssetPath` は `/` で始まるパスを変えずに返す）。こう書き換えるのは
  `config.background_path` と `config.main_icon_path` だけで、この 2 つには file の URL や `C:\...` のような絶対パスは入らない。
  読めない画像（無い・画像でない・ネットワークのパス）は `""`（テンプレートの既定で描く）。ほかのパス（キャラの絵・国旗・ロゴ）は
  アプリのフォルダからの相対パス
- 同梱のリザルトの描画を流用するなら `../include/result.js`（`data.rows` が `teams` なら団体として描く。`rows` の無い payload では
  従来どおり `layout` が `team` で始まるとき）と `../include/result.css` を読む。`include/result.js` は読んだだけでは何も登録しない。
  契約 2 なら `../include/autona.js` と `../include/result.js` を読んだ後に自分で登録する（マニフェストを `contract: 2` に）:
  `Autona.onRender(drawResult)`（同梱の `result.html` と同じ）か、描き足すなら
  `Autona.onRender(async (payload) => { await drawResult(payload); /* 自分の描き足し */ })`。`onRender` を呼ぶのは 1 回だけ。
  契約 1 のまま（`autona.js` を読まない）なら今までどおり `initResult` で描く

> **Note:** 現行テンプレートは前身プロジェクトのデザインを引き継いでいます。
> 今後 Autona 版として順次リデザインを進める予定です。

## レイヤー型スコアボードデザイナー

複数の透過PNGと、選手名・スコアなどの動的なHTML要素を、1920×1080の
キャンバス上で組み合わせる編集ツールです。PNGは個別レイヤーのまま保存され、
完成したデザインはOBSブラウザソースとして利用できます。

### 必要な環境

- AutonaのWebサーバーが起動していること
- Web UIが最新状態でビルドされていること

ソースから起動する場合は、最初にWeb UIをビルドします。

```powershell
cd web
npm run build
cd ..
python main.py
```

Autona起動後、デスクトップアプリのBOARDタブにある `Designerを開く` を
押します。DesignerはAutonaの拡張ウインドウとして開き、外部ブラウザからは
編集・保存できません。

### 画面構成

#### 上部ツールバー

| 操作 | 内容 |
|---|---|
| デザイン名 | 出力フォルダ名の元になる名前 |
| 対戦形式 / ゲーム | BOARDタブのデザイン一覧の下に出る説明（任意。空欄なら「1v1」「カスタム」） |
| 新規 | 現在の内容を破棄して空のプロジェクトを作成 |
| 開く | 保存済みデザインの読み込み、または不要なデザインの削除 |
| 保存 | プロジェクトとOBS用ファイルを `layout/custom/{slug}` へ保存 |
| Design / Animate | 静的デザイン編集とアニメーション編集を切り替え |
| アニメーション | 選択中トラックのアニメーションをキャンバス上で再生 |
| サンプル／実データ | Start.gg反映済みの実データと確認用サンプルを切り替え |

#### 左側：追加とレイヤー一覧

- `PNGを追加`: 背景、枠、コーナー装飾などの透過PNGを複数選択できます。
- プリセット: `空` / `基本 1v1` / `チーム` / `クルー` から選んで `読込` を押すと、
  動的要素を配置済みの状態から始められます（現在の内容は破棄）。Designer 内蔵の
  配置で、`layout/` の組み込みデザインとは無関係です。`空` 以外は対戦形式欄にも
  値（`1v1` / `チーム戦` / `クルーバトル`）が入ります。
- 動的要素の選択欄: P1/P2の名前・所属タグ・スコア・国旗・Seed・X・地域、
  大会ロゴ・大会名・フェーズ・ラウンド・ゲーム取得表示を追加できます。
- レイヤー一覧: 上にあるレイヤーほど手前に表示されます。
- 一覧上部のボタンで、背面／前面への移動、複製、削除ができます。
- 目のアイコンでレイヤーを一時的に非表示にできます。

同じ動的要素を複数回追加することもできます。例えば選手名をメイン表示と
装飾用の小表示へ同時に配置できます。

#### 中央：キャンバス

- レイヤー本体をドラッグすると移動できます。
- 選択時に右下へ表示される丸いハンドルでリサイズします。
- 選択時に上側へ表示される丸いハンドルで回転します。
- キャンバス外をクリックすると選択を解除します。

キャンバスサイズはOBS向けの1920×1080固定です。初期背景は透明で、
編集画面の格子模様は透明領域を表しており、OBSには表示されません。

#### 右側：プロパティ / アニメーション

`Design` モードでは、すべてのレイヤーで名前、表示状態、X/Y座標、幅、高さ、
回転角、重なり順、透明度を編集できます。

| レイヤー | 追加設定 |
|---|---|
| PNG | 比率維持、領域を覆う、領域に合わせて変形 |
| 動的画像 | 国旗やロゴの表示方法 |
| 動的テキスト | フォント、文字サイズ、太さ、文字間隔、色、整列、斜体 |
| ゲーム取得表示 | 間隔、P1色、P2色、未取得色 |

`Animate` モードでは、開始時（intro）とデータ更新時（update）の
キーフレームを編集できます。編集対象は、レイヤー本体の配置からの
相対 X/Y 移動、回転、Scale X/Y です。イージングはベジエグラフで調整します。
色、フォントサイズ、アニメーション再生はDesigner内で確認できます。

長いテキストは指定領域へ収まるように横方向へ自動縮小されます。

### 新しいデザインを作成する

1. デザイン名を入力します。
2. `PNGを追加` から必要な画像を追加します。
3. 動的要素を選び、右の `追加` を押します。
4. `Design` でキャンバスとプロパティ欄を使って各レイヤーを調整します。
5. 必要に応じて `Animate` に切り替え、シーン表示時・データ変更時のキーフレームと
   イージングを調整します。
6. `アニメーション` で演出を確認します。
7. 初期状態は `サンプルデータ` です。左上のスイッチを切り替え、
   現在のAutonaの選手名やスコアを `実データ` で確認します。
8. `保存` を押します。

フォルダー選択やブラウザの書き込み許可は不要です。Autonaが自動的に
`layout/custom/{slug}/` を作成します。`slug` はデザイン名から生成されます。
PNGとSVGは `assets/` に個別保存され、OBS用ランタイムから参照されます。

データ変更時のタイムラインには「値の差し替え時刻」があります。その時刻までは
旧値を表示し、差し替え後は新値を表示します。更新が連続した場合は未表示の
中間値を捨て、最新値へ遷移します。

Start.ggからセットをAutonaへ反映済みの場合、選手名、所属タグ、スコア、
大会名、フェーズ、ラウンドはDesignerにもリアルタイムで反映されます。
Autonaへまだデータが届いていない場合は `実データ待機中` と表示され、
接続後に自動的に実データへ切り替わります。

### 保存したデザインを再編集する

1. Designerの `開く` を押します。
2. Designer内に表示される保存済みデザインの一覧から対象を選択します。
3. 読み込み後、レイヤーやアニメーションを編集して `保存` を押します。

読み込んだデザインは同じフォルダへ上書き保存されます。別デザインとして
保存したい場合はデザイン名を変更して保存します。

### 保存したデザインを削除する

1. Designerの `開く` を押します。
2. 対象デザインの右側にある削除アイコンを押します。
3. 確認ダイアログで削除を承認します。

削除すると `layout/custom/{slug}/` 内のプロジェクト、PNG、OBS用ファイルが
すべて削除されます。この操作は取り消せません。編集中のデザインを削除した
場合は、Designerが空の新規プロジェクトへ切り替わります。

### OBSへ追加する

保存後、BOARDタブのスコアボードデザイン一覧へ `Custom: {デザイン名}` として
自動的に追加されます。表示されない場合は一覧横の `更新` を押してください。
OBSではBOARDタブに表示される `index.html` をブラウザソースの
ローカルファイルとして指定し、幅1920・高さ1080に設定します。

Autonaを起動した状態で選手名やスコアを変更し、Designerの実データ表示と
OBSの両方へ反映されることを確認してください。

### 出力フォルダ

```text
layout/
├── designer_runtime/        # 全デザイン共通のOBS用ランタイム（runtime.js / runtime.css）
└── custom/
    └── <slug>/
        ├── assets/          # PNG / SVG資産
        ├── project.json     # 編集可能なレイヤー情報（schemaVersion: 3。任意で meta.format / meta.game）
        └── index.html       # OBS入口（designer_runtime を読むだけのスタブ）
```

旧バージョンで保存したデザインは、起動時に自動でこの形式へ切り替わります
（元の `index.html` / `runtime.*` は `<slug>/_legacy/` に退避されます）。

各デザインは `http://127.0.0.1:5107/layout/custom/{slug}/index.html` でも
直接確認できます。保存後はBOARDタブのデザイン一覧に `Custom: {デザイン名}` として
表示されます（同名のデザインが複数あると `Custom: {デザイン名} (custom/{slug})`）。

`project.json` のレイヤーIDや `schemaVersion` は互換性判定に使います。
schemaVersion 1・2 の既存デザインはDesignerで読み込むと schemaVersion 3 へ
移行されます。手動編集する場合も、IDを重複させず、キャンバスサイズを
1920×1080から変更しないでください。

### トラブルシューティング

| 症状 | 確認内容 |
|---|---|
| Designerが表示されない | `npm run build` 後にAutonaを再起動し、BOARDタブから開き直す |
| デスクトップアプリ外で開けない | DesignerはBOARDタブから開いた拡張ウインドウ専用 |
| 保存に失敗する | Autonaの配置ディレクトリへ書き込み権限があるか確認 |
| 保存したデザインがBOARDに出ない | スコアボードデザイン一覧横の `更新` を押す |
| PNGが「画像なし」になる | `layout/custom/{slug}/assets/` 内のPNGを移動・削除していないか確認 |
| 実データが更新されない | Autonaが起動中か確認し、左上のスイッチを実データへ切り替える |
| OBSで全面が黒くなる | 不透明な背景PNGが全面を覆っていないか確認 |
