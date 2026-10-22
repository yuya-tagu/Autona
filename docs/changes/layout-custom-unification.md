# layout/custom 統合（Designer 生成 layout を他 layout と同じ概念で扱う）設計書

関連: [ADR-001](../decisions/ADR-001-external-control-api-boundary.md)（`/api/v1/` に `layouts` を追加する前提）、
`layout/README.md`（Designer の操作説明）、OSS 公開前整理（PR #206 の続き）。
凡例: 【確認】= ファイル・行で裏取り済み、【推測】= 状況証拠。

**状態（2026-10-22）: Step 1〜4 完了**（Step 2 は PR #208、Step 1・3 は PR #209 でマージ済み、Step 4 は実装済み）。残りは Step 5（公開後可）。
実装で確定した規約と既知の制約は §7。

## 1. 目的と非目標

| | 内容 |
|--|--|
| 目的 | 公開前に `layout/custom/{slug}/` を、旧来型 layout（`layout/scoreboard_*` 等 21 フォルダ）と**同じ発見・同じメタデータ・同じ配信 URL 規約**で扱えるようにし、ランタイムと検証の重複を 1 箇所に畳む |
| 非目標 | 旧来型 layout 21 個を Designer 形式に作り直すこと／state キー・`globals.js` の `tsh*` グローバル・`out/program_state.json` の形式変更／Designer の編集 UI 刷新 |

## 2. 着手前の状態（2026-10-21 時点の事実）

> Step 1〜3 で解消済みの記録。当時の行番号はコードが変わって指す先が無くなったため削除した。

### 2.1 構成とデータフロー【確認】

```
layout/include/globals.js ── Socket.IO で state 受信 → Start()/Update() を呼ぶ（両型式共通）
├─ scoreboard_simple/ 等 ── index.html + index.js（手続き型: data.score[N]... を直接参照）
│                           settings.json（globals.js が /layout/settings.json と合成して読む）
└─ custom/{slug}/        ── index.html（固定 19 行、tshLayoutRootPath="../.." を注入）
                            runtime.js / runtime.css（★保存毎にコピー）
                            project.json（宣言型: layers[] + BINDINGS 21 種）+ assets/*.png|svg
```

- 発見: Board タブ `_discover_designs()` が `layout/scoreboard*/index.html` と `layout/custom/*/index.html` を**別ループ**で走査（`src/AutonaBoardWidget.py`）。Commentary / Top 8 は別関数 `DiscoverLayoutDesigns(prefix)`（`src/AutonaLayoutPreview.py`）で、custom を知らない
- 配信 URL: プレビューは `http://127.0.0.1:{port}/layout/{design}/index.html`（`AutonaLayoutPreview.py`）、OBS 向けはローカル絶対パス `layout/{design}/index.html`（`AutonaBoardWidget.py`）。custom は `design = "custom/{slug}"` で同じ式に乗る
- Designer の保存 API は `/api/designer/*`（`src/AutonaWebServer.py`、トークン認証）。`/api/v1/` の外にあり、deprecated 対象外と明記

### 2.2 ランタイムがコピーされる箇所【確認】

`AutonaDesignerService.save_project()` が `TEMPLATE_FILES = ("index.html","runtime.css","runtime.js")` を
`web/build/` → `web/public/scoreboard-designer-runtime/` の順で探し、保存毎に slug 配下へ複製する。
結果、実機には **3 世代の runtime** が並存する:

| 場所 | サイズ | 受理する schemaVersion |
|------|--------|------------------------|
| `web/public/scoreboard-designer-runtime/runtime.js`（現行） | 42,846 B | 1/2/3 を内部で 3 へ移行（描画は 3 のみ） |
| `layout/custom/shibuyadairan_v1/runtime.js` | 42,824 B | 同上の 1 つ前の版【推測: md5 不一致・行数近似】 |
| `layout/custom/新しいデザイン/runtime.js` | 12,541 B | **1 のみ**（`validateProject` が `schemaVersion !== 1` を拒否） |

ランタイム修正は既存 design に届かず、design 数だけバージョンが増える。

### 2.3 検証・移行が三重になっている箇所【確認】

| 実装 | 役割 | 場所 |
|------|------|------|
| TS `model.ts` | 編集時の検証 + v1/v2 → v3 移行（正本の候補） | `web/src/ScoreboardDesigner/model.ts` |
| Python `_validate_project` | 保存・読込時に **レイヤー意味論まで**再検証（v2 の animations、v3 の events/assets manifest…約 220 行） | `src/AutonaDesignerService.py` |
| `runtime.js` | OBS で project.json を直接読むため移行を再実装 | `runtime.js` |

### 2.4 メタデータが三重管理になっている箇所【確認】

| 場所 | 内容 |
|------|------|
| `layout/{name}/`（実体） | フォルダ名・`settings.json`（現状は `assets.default.asset_key` のみ） |
| `src/AutonaBoardWidget.py` | `DESIGN_DISPLAY_NAMES`（表示名 5 件）と `DESIGN_META`（対戦形式・ゲーム）。custom は `"対戦形式：1v1 / ゲーム：カスタム"` 固定 |
| `web/src/ScoreboardDesigner/index.tsx` | `STANDARD_TEMPLATES` + 座標ハードコード。実在しない `"scoreboard"` キーを含む |

## 3. 目標の構成

```
layout/
├─ designer_runtime/            ← ★共通ランタイム 1 本（git 追跡。web/public/scoreboard-designer-runtime を移動）
│   ├─ runtime.js / runtime.css
│   └─ README.md（schemaVersion 互換範囲を明記）
├─ scoreboard_simple/settings.json ← "autona": {"kind":"scoreboard","displayName":"Simple","format":"1v1 / チーム","game":"汎用"}
└─ custom/{slug}/
    ├─ index.html               ← 10 行のスタブ（ロジック無し。../../designer_runtime/runtime.js を読むだけ）
    ├─ project.json             ← 名前・メタ（format 等）・layers
    └─ assets/
```

- **URL は現状維持**: `layout/custom/{slug}/index.html`。スタブ方式なので OBS 登録済み URL（http / file:// とも）を変えずに済み、`runtime.js:1016` の `fetch("./project.json")` と `:1024` の slug 抽出もそのまま動く。`?project=` 方式は採らない（§6 論点 1）
- **メタデータの正本**: 旧来型は `settings.json` の `autona` ブロック、custom は `project.json`（`name` + 新設 `meta.format/game`、無ければ既定）。Qt 非依存の `src/core/LayoutCatalog.py` が両方を 1 つの一覧 `[{id:"custom/x", kind, displayName, format, game, entry:"index.html"}]` に正規化し、**`GET /api/v1/layouts`**（ADR-001 D3 の枠内。`?kind=scoreboard|designer|commentary…`）と Board / Commentary / Top 8 / Designer「開く」が同じ関数を使う。`DESIGN_DISPLAY_NAMES` / `DESIGN_META` / `_discover_designs` の custom 分岐 / `DiscoverLayoutDesigns` を削除
- **検証の役割分担**: TS `model.ts` = 意味論の正本（編集・移行・保存前検証、保存は常に最新 schemaVersion）。Python = **安全性のみ**（JSON サイズ、レイヤー数、`assets/` 配下のパス、PNG/SVG の実体検査、schemaVersion が既知集合に含まれるか）。`runtime.js` = 読込時移行を保持（OBS はサーバー無しで project.json を読むため不可避）。TS と runtime の移行結果が一致することは **共通フィクスチャ**（`tests/fixtures/designer/{v1,v2,v3}.json` → 期待値 v3）で jest と node 実行の両方から pin する
- **Board タブ**: 特別扱いを `LayoutCatalog` の `kind` で置換。表示名は `displayName`、メタ欄は `format/game`（custom は `project.json.meta` 由来、未設定なら「1v1 / カスタム」）。`QFileSystemWatcher` の監視対象も catalog が返す `watchDirs` に一本化
- **Designer のプリセット**: `STANDARD_TEMPLATES` は旧来型フォルダ名と切り離し「空 / 基本 1v1 / チーム / クルー」の**プリセット**として残す（座標は Designer 内部資産）。`"scoreboard"` の幽霊キーは削除

## 4. 互換性と移行

| 項目 | 方針 |
|------|------|
| 既存 design の自動移行 | `AutonaDesignerService.MigrateLegacyProjects()` を `bootstrap_core()` から 1 回呼ぶ（冪等）。`runtime.js` を持つ slug ごとに ① `_legacy/` へ `index.html, runtime.js, runtime.css` を退避 ② スタブ `index.html` を書く。`project.json` は**触らない**（v1 のままでも共通 runtime が読込時に v3 へ移行する: `runtime.js:159-199`）。実機の `shibuyadairan_v1`（v3 系）・`新しいデザイン`（v1・layers 空）とも同じ手順で通る【確認: project.json 先頭を読了】 |
| OBS 登録済み URL | 変更なし（スタブが同じパスに居る）。Designer 保存後の `designer_project_saved` → `location.reload()`（`runtime.js:1035-1044`）も維持 |
| runtime 版差の吸収 | 共通 runtime 1 本が v1/v2/v3 を受理するので design 側で吸収不要。「v(N-2) まで読む」を `layout/designer_runtime/README.md` に規約化し、フィクスチャ契約テストで pin |
| schemaVersion | 現行 3 を維持。保存は常に最新。Python は既知集合のみチェックし意味論は TS に委ねる |
| キャッシュ | `AutonaWebServer.py:730-741` の `no-store` を `layout/designer_runtime/` にも適用（ランタイム更新が OBS に即反映） |
| 配布 | `gen_release.ps1` は `layout/` を丸ごと同梱するので追加作業なし。`validate_release_zip.ps1` の「`layout/custom/` 混入チェック」は維持（`designer_runtime/` は許可） |
| ロールバック | 旧版 exe に戻す場合: `_legacy/` の 3 ファイルを戻せば旧 URL・旧 runtime で復帰（手順を `docs/operations/runtime-operations.md` に 1 項追加）。戻さなくても旧 exe の Designer で「保存」すれば旧形式に戻る |

## 5. 実装ステップ（1 ステップ = 1 PR）

| Step | 内容 | 工数 | pin するテスト | リスク |
|------|------|------|----------------|--------|
| 1（完了） | `src/core/LayoutCatalog.py` 新設 + 旧来型 5 scoreboard の `settings.json` に `autona` ブロック + `GET /api/v1/layouts` + Board / `DiscoverLayoutDesigns` / Designer「開く」を catalog 消費に置換、`DESIGN_*` 削除 | M | 新 `tests/test_layout_catalog.py`（tmp layout ツリーで発見・既定値・kind）、`test_webserver_routes.py` のルート台帳更新、`test_core_qt_free.py`（catalog が Qt を import しない） | 低。表示名の文字列が変わらないことをテストで固定 |
| 2（完了・PR #208） | 共通 runtime 化: `web/public/scoreboard-designer-runtime/` → `layout/designer_runtime/` 移動、`save_project` はスタブのみ書く、`MigrateLegacyProjects()`、webserver の no-store 拡張 | M | `test_designer_service.py` に「保存後に runtime.* が無い」「移行が冪等で `_legacy/` を作る」「スタブが相対パスで runtime を参照」、`validate_release_zip.ps1` の許可リスト | 中。OBS 配信中の移行は起動時のみ・保存時に再読込なので配信中変更なし。`web/build` 依存が消える（Designer プレビューは `/layout/designer_runtime/` を読む） |
| 3（完了） | 検証の分担: Python `_validate_project` を安全性のみ（~60 行）に縮小、TS を正本、`tests/fixtures/designer/*.json` で TS と runtime の移行一致を契約テスト化 | S〜M | jest（`model.test.js`）+ node で `runtime.js` を `vm` 実行する `tests/test_designer_runtime_contract.py` | 低。Python を緩めた分は TS 側テストが既に v1→v3 を網羅（`model.test.js:228-238`） |
| 4（完了） | Designer プリセットの切り離し（幽霊キー削除・命名変更）+ `project.json.meta`（format/game）を Designer から編集可能に | S | `index.test.js`（プリセット一覧・meta の保存）、`model.test.js`（meta の正規化）、`test_layout_catalog.py` / `test_board_widget.py`（meta 反映）、`test_designer_runtime_contract.py`（meta で描画が変わらない） | 無 |
| 5（公開後可） | `/api/designer/*` を `/api/v1/designer/*` へ移し旧パスをエイリアス、Figma プラグインの接続先更新 | S | ルート台帳 | 低（Figma プラグインは別エージェント編集中の `tools/` に依存） |

Step 1 と 2 は独立（並行可）。Step 3 は 2 の後、Step 4 は 1 の後。Step 1〜4 は完了（2026-10-22）。

## 6. 決めてほしいこと（推奨付き）

> **2026-10-21 オーナー決定: 3 点とも推奨案で確定**（スタブ方式で URL 維持 / Python 検証は安全性のみ・TS が意味論の正本 / custom に format・game メタを持たせる）

1. **スタブ方式 vs `?project=` 方式** — 推奨: **スタブ**。OBS 登録 URL・`fetch("./project.json")`・slug 抽出を全て無変更で通せる。`?project=` は URL が綺麗だが、既存 OBS シーンの全張り替えとリダイレクト保守が要る
2. **Python 検証の縮小幅** — 推奨: **安全性のみ**に縮小。Designer はローカル QWebEngine 専用（トークン＋loopback 限定: `AutonaDesignerService.py:74-85`）で、意味論の二重検証は防御価値より保守コストが大きい。LAN 公開を将来許すなら再検討（ADR-001 D5 の見直し時）
3. **custom のメタデータ（対戦形式・ゲーム）を project.json に持たせるか** — 推奨: **持たせる**（Step 4）。持たせないと Board の「1v1 / カスタム」固定が残り、catalog 上で custom だけ情報欠落のまま公開することになる

## 7. 実装で確定した規約と既知の制約（Step 1〜4 完了時点）

### 7.1 確定した規約

- **メタデータ（`autona` ブロック）の規約**は `layout/README.md`「アプリのデザイン一覧に載せる」が正本
  （`.claude/design/Design.md` §4.3.3 に要約）。kind / displayName / format / game / entry、ブロックが無い
  フォルダはフォルダ名規約へフォールバック
- **表示名**: Designer 製は `Custom: {project.json の name}`（組み込みと区別するため。Designer の「開く」一覧は
  接頭辞なし）。同じ `kind` の中で表示名が重複したら、重複したもの全部を `{displayName} ({id})` にする。
  kind ごとに別のプルダウンなので、kind をまたぐ同名（実況と TOP 8 の「標準」）はそのまま
- **Board の選択**: 再走査で選択中のデザインが一覧から一時的に消えても（project.json のロック・破損）、
  表示だけ既定にフォールバックし `board.selected_design` は上書きしない。一覧に戻れば元の選択に復帰する。
  保存するのはオペレーターが自分で選んだときだけ
- **列挙の安全側**: `layout/custom/` 配下のシンボリックリンク・ジャンクションは列挙しない。JSON の読込失敗
  （ロック・破損・`RecursionError` になる深いネストを含む）は警告ログを出してその 1 件だけ無視する
- **`GET /api/v1/layouts`** は読み取り専用のカタログ系なので `WebServerActions` を経由しない
  （ADR-001 2026-10-22 Addendum、`.claude/rules/state-and-webserver.md`）
- **`project.json.meta`**（Step 4）: `{format?, game?}` の任意フィールド。schemaVersion は 3 のまま
  （v1/v2 からの移行にも素通しで乗る）。Designer 左サイドバーの「対戦形式」「ゲーム」欄で編集し、
  編集中は入力どおり保持、保存時に `model.ts` の `validateProject()` が trim し、空の項目は書かず、
  両方空なら `meta` ごと書かない。各 64 文字（trim 後）まで、文字列以外は拒否、`null` は未設定扱い、
  `format`/`game` 以外のキーは保存時に落とす。Python `_validate_project` は `meta` が dict か不在かだけ
  （サイズは `MAX_PROJECT_BYTES` で全体として制限）。`runtime.js` は読まない。Board のメタ欄は
  `setTextFormat(PlainText)` なので値は HTML として解釈されない
- **Designer のプリセット**（Step 4）: `空 / 基本 1v1 / チーム / クルー`（`index.tsx` の `DESIGNER_PRESETS`）。
  キー・座標とも Designer 内部の資産で `layout/` のフォルダ名とは対応しない。座標は旧テンプレートから
  `基本 1v1` ← Universal、`チーム` ← team、`クルー` ← crewbattle を引き継ぎ、幽霊キー `scoreboard` と
  simple / topleft の座標は削除した。`空` 以外は `meta.format`（`1v1` / `チーム戦` / `クルーバトル`）も入れる。
  プロジェクト名はプリセット名（`空` は通常の新規と同じ「新しいデザイン」）

### 7.2 既知の制約

- **保存済み v3 データのキーフレーム rotation 二重適用は自動補正しない**。旧 Designer の v1/v2 → v3 移行は、
  生成するキーフレームの rotation にレイヤー自身の rotation を入れていた（キャンバスと `runtime.js` はレイヤー枠を
  回してから中身をアニメーションするので、intro 終了後に 2 倍の角度になる）。Step 3 で `model.ts` の移行は 0 を
  入れるよう直したが、移行は schemaVersion が古いときだけ走るため、旧 Designer で v3 として保存済みのデータは
  そのまま。実データで該当 0 件を確認済み。見つかった場合は Designer でキーフレームの回転を 0 に戻して保存する
- **保存のたびに全 `project.json` を解析する**。Board タブは `layout/custom/` 配下の変更通知（750ms デバウンス）で
  カタログ全体を再走査し、全プロジェクトの `project.json` を読み直す。デザイン数が数十程度なら問題にならない
  想定で、キャッシュは入れていない
- **組み込みフォルダの中は監視しない**。監視対象は `layout/`・`layout/custom/`・各プロジェクトフォルダのみ。
  `layout/scoreboard_*/settings.json` の `autona` ブロックを書き換えても、別のきっかけ（`layout/` 直下の
  フォルダ追加・削除・改名、Designer の保存など）で再走査が走るまで Board の一覧に反映されるとは限らない
  （確実なのはアプリ再起動）。Commentary / Top 8 タブはパネル構築時に 1 回読むだけ
- **Designer の UI 修正は `web/build` の再ビルドで反映される**。Designer 画面は `web/build` の成果物を
  QWebEngine で開くため、`web/src/ScoreboardDesigner/` を直したら `cd web && npm run build` が要る
  （OBS 用の `layout/designer_runtime/` は直接配信なのでビルド不要）
