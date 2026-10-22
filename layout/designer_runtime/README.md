# designer_runtime（Designer デザイン共通ランタイム）

Scoreboard Designer で保存したデザイン（`layout/custom/{slug}/`）を OBS で描画する
**共通ランタイム 1 本**。全デザインがここを参照するので、ランタイムの修正は保存し直さなくても
既存デザインすべてに届く。

| ファイル | 役割 |
|---------|------|
| `runtime.js` | `./project.json` を読み、読込時に最新 schemaVersion へ移行して描画する（ビルド不要の素の JS） |
| `runtime.css` | 描画用の固定スタイル |

## デザイン側との契約

- `layout/custom/{slug}/index.html` はロジックを持たない**スタブ**で、
  `../../designer_runtime/runtime.css` と `../../designer_runtime/runtime.js` を読むだけ。
  スタブ本体は `src/AutonaDesignerService.py` の `STUB_INDEX_HTML` が正本
- `runtime.js` のパス解決はすべて**ドキュメント（スタブ）基準**: `fetch("./project.json")`、
  `assets/*` の画像、`window.tshProgramRootPath` / `tshLayoutRootPath`（スタブが注入）。
  `runtime.css` は相対 `url()` を持たない。ここに相対参照を足すと、スクリプト位置基準か
  ドキュメント基準かで解決先が変わるので注意する
- デザインフォルダへ `runtime.*` をコピーしない（旧方式。保存毎にコピーしていたため
  デザイン数だけランタイムの版が並存していた）
- Web サーバーはこのフォルダを `no-store` で配信する（更新が OBS に即反映される）

## schemaVersion 互換範囲

| 項目 | 値 |
|------|-----|
| 現行（描画する版） | **3** |
| 読み込める版 | **1 / 2 / 3**（`migrateProject()` が読込時に 3 へ移行。`project.json` は書き換えない） |

**規約: 現行を N とするとき v(N-2) まで読む。** schemaVersion を 4 に上げるときは
v2・v3 → v4 の移行を `migrateProject()` に足し、v1 の読込を外すかどうかはその時点で
実在するデザインを確認してから決める（Designer は保存時に常に最新版を書くので、
一度でも保存し直したデザインは最新版になっている）。

**移行結果は Designer の `web/src/ScoreboardDesigner/model.ts`（意味論の正本）と一致させる。**
共通フィクスチャ `tests/fixtures/designer/{v1,v2,v3}.json` → `expected_v3.json` を、
`model.test.js`（jest）と `tests/test_designer_runtime_contract.py`（node で本ファイルを実行）が
両側から pin している。`migrateProject()` を変えたら両方を通すこと。node 側は、ページが
`window.__autonaDesignerContractTest` を先に定義したときだけ有効になるフック経由で
`validateProject()` を呼ぶ（出荷ページは定義しないので描画には影響しない）。

## 旧形式からの移行とロールバック

起動時に `AutonaDesignerService.MigrateLegacyProjects()` が、`runtime.js` をフォルダ内に
持つ旧形式デザインの `index.html` / `runtime.js` / `runtime.css` を `_legacy/` へ退避し、
スタブ `index.html` に置き換える（冪等。`project.json` と `assets/` は触らない。`_legacy/` に
既にあるファイルは上書きしない）。このフォルダの `runtime.js` / `runtime.css` が欠けている間は、
移行も Designer の保存も行わない（スタブだけ書くと全デザインが真っ白になるため）。
旧版 exe に戻すときの手順は `docs/operations/runtime-operations.md`
（「Designer デザインのロールバック」）を参照。
