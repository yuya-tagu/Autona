# ADR-008: 運営が AI でレイアウトを作れるようにする（オーバーレイ契約・作成キット・アプリ内の確認）

## Status

Accepted（2026-11-21 オーナー承認。末尾の論点は 1 = A・2 = A・3 = A・4 = A）

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
  - jest: vm で `autona.js` を動かし、サンプルの state → ビューが期待値（`tests/fixtures/overlay/views.json`）と一致し、表のキーと型に合うこと
  - キットの `CONTRACT.md`・`AI_PROMPT.md` はこの表から生成する（D2）
- 約束の範囲: **キーの追加だけ**。改名・削除・意味の変更は contract を上げる（ADR-007 D3 と同じ規則）。
  マニフェストの `"contract"` はオーバーレイでは省略可（省略は 1）。**知らない番号は一覧に出さず理由を出す**（ADR-007 D1 と同じ扱い。理由は D3 で見えるようにする）
- 生の state は塞がず `Autona.raw` で渡すが、約束の外と明記する（論点 3）。同梱のレイアウトは今の書き方のまま（移行は強制しない）
- 対応づけは JS（`autona.js`）に置く。core に新しい state のルートやイベントは足さない（論点 1）
- `autona.js` が読む URL のクエリは `autona` の接頭辞付き（`autonaSample` / `autonaPreview`）。`URLSearchParams` で読み、`window` の値には頼らない。
  キットに「トップレベルで使わない名前」（`scoreboardNumber`・`autona*`・`Start`・`Update`・`data`・`oldData`・`settings`）を書く
- キットに `gsap` を使うなら最初の更新まで止まっていることを書く（`globals.js` の既存の挙動）

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
| **L1 契約** | `autona.js`（ビュー・`onUpdate`・`Autona.raw`・`autonaSample`）、対応表 `overlay_contract.json`、サンプルの state `layout/include/sample/`、ビューの期待値、マニフェストの `contract`（オーバーレイ）、`layout/README.md` の更新 | jest: vm で state → ビュー（単独戦・団体戦・crew・値の欠け・ボード 2）、差分の更新で `onUpdate` が呼ばれること、`Update` を定義したレイアウトでも呼ばれること。pytest: 表の元のパスと `KEY_CATALOG`、知らない contract を一覧に出さないこと。実機: 隔離データで `autona.js` を使った試作レイアウトを OBS 用 URL で開き、スコアの変更が反映されること | 通常 |
| **L2 キット** | `layout/include/kit/`、生成スクリプト（`CONTRACT.md`・`AI_PROMPT.md`）、「新しいデザインを作る」（core の操作・SHELL のルート・ROUTE_ACCESS・v1）、フォルダを開く操作の一般化、「AI 用の説明をコピー」、`validate_release_zip.ps1` の必須エントリ | 生成物の差分なしのテスト、作る操作の pytest（既にある名前・予約名・不正な文字・ルート台帳とアクセス区分）、jest。**受け入れ: `AI_PROMPT.md` だけをチャットの AI に渡し、指定したデザインが手直しなしで動くこと**（3 種で試す） | 高（`layout/` への新しい書き込み口） |
| **L3 アプリ内の確認** | 一覧から外れた理由（v1 の追加と文書）、`autona.js` のエラー捕捉と `postMessage`、`DesignPane` の受け方とエラーの欄・コピー、規則と固定のテストの差し替え、再読み込み・URL のコピー | jest: 送り元とオリジンの照合（別のフレーム・入れ子のフレーム・形の違うメッセージを捨てる）・長さと件数の上限・テキストとしてだけ描くこと・コピーの文。pytest: 外れた理由の各分類。E2E: エラーを起こす試作レイアウトでエラーの欄に出ること、`autona.js` を読まないレイアウトで「読めていない」が出ること、`autonaSample` でサンプルが出ること。実機: 隔離データで 3 ページを確かめる | 高（共有ビューの `message` の規則を変える） |
| **L4 Designer の寄せ** | 21 語の golden、`resolveBinding()` をビュー経由に | 21 語の golden が変わらないこと、Designer 製デザインの表示が変わらないこと | 通常 |

- L4 は任意。L1〜L3 の後、語彙の二重管理が問題になったら行う

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
