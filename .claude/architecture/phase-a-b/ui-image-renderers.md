# WebEngine 経由の画像レンダリングレイヤー（Phase A 調査結果）

**Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。**

## 概要

Autona の UI 画像レンダリングは、QWebEngineView を用いた 2 つの独立したオフスクリーン HTML レンダラーで構成される。
- **ThumbnailRenderer**: サムネイル画像生成（1920×1080）
- **ResultRenderer**: リザルト画像生成（複数レイアウト対応）

各レンダラーは HTML テンプレートを読み込み、Python から JavaScript 経由で state を注入、pixmap として画像をキャプチャする。

---

## 1. レンダラーの役割と入出力

### 1.1 ThumbnailRenderer（src/thumbnail/ThumbnailRenderer.py）

| 項目 | 内容 |
|------|------|
| **クラス定義** | `class ThumbnailRenderer(QObject)` (L27) |
| **インスタンス管理** | Singleton パターン (`instance()` 静的メソッド) |
| **ホストウィジェット** | QWidget (オフスクリーン、WA_DontShowOnScreen) |
| **WebEngineView サイズ** | 固定 1920×1080 (L63, L66) |
| **カスタムページ** | `_ThumbnailPage` (L12) — JS console メッセージキャプチャ用 |

**入力**:
- `render(data: dict, config: dict, template: dict)` — 非同期レンダリング (L114)
  - `data`: スコアボード データ（game, score, tournamentInfo など）
  - `config`: レンダリング設定（display_phase, use_sponsors 等）
  - `template`: `{template_id, ...}` テンプレート ID を含む辞書 (L95-98)
- `renderSync(data, config, template) → QPixmap` — 同期レンダリング (L140)

**出力**:
- 非同期: `renderComplete(QPixmap)` シグナル (L45)
- 同期: QPixmap オブジェクト（1920×1080 KeepAspectRatio） (L219-223)

**WebEngineView 利用パターン**:
1. `_load_template(template_id)` (L100) — `layout/{TEMPLATE_SUBDIRS[id]}/thumbnail.html` を file:// URL で読み込み (L104-106)
2. `_start_render(payload)` (L173) — JS 経由でデータ注入: `runJavaScript(f"initThumbnail(JSON.parse(\`{json_str_escaped}\`))")` (L182-183)
3. `_on_title_changed()` (L203) — HTML → JS → `document.title = "RENDER_READY"` で完了検知。100ms 遅延後 `_finalize_render()` (L213)
4. `_finalize_render()` (L215) — `self._view.grab()` で off-screen buffer をキャプチャ → 必要に応じ `scaled()` (L218-223)

**テンプレートマッピング** (L39-42):
```python
TEMPLATE_SUBDIRS = {
    "type_a": "thumbnail",
    "type_b": "thumbnail_type_b",
}
DEFAULT_TEMPLATE_ID = "type_a"
```

**ページロード・レンダリング状態管理** (L79-86):
- `_template_id`, `_page_loaded`, `_pending_payload`, `_rendering`, `_queued_payload`
- `_sync_loop`, `_page_load_loop`: QEventLoop による同期化

**透過 PNG 対応** (L77): `setBackgroundColor(Qt.GlobalColor.transparent)`

---

### 1.2 ResultRenderer（src/result/ResultRenderer.py）

| 項目 | 内容 |
|------|------|
| **クラス定義** | `class ResultRenderer(QObject)` (L39) |
| **インスタンス管理** | Singleton パターン |
| **ホストウィジェット** | QWidget (オフスクリーン) |
| **初期サイズ** | 1920×1080 (L71, L74) → レイアウト選択時に動的変更 |
| **カスタムページ** | `_ResultPage` (L27) |

**レイアウト定義** (L16-24):
```python
LAYOUTS = {
    "horizontal": ("result", 1920, 1080),
    "horizontal_16": ("result_top16", 1920, 1080),
    "vertical": ("result_vertical", 960, 1080),
    "vertical_geki": ("result_vertical_geki", 960, 1080),
}
DEFAULT_LAYOUT = "horizontal"
```

**入力**:
- `render(data, config, layout=None)` — 非同期 (L119)
- `renderSync(data, config, layout=None) → QPixmap` — 同期 (L139)

**出力**:
- 非同期: `renderComplete(QPixmap)` Signal (L51)
- 同期: QPixmap （horizontal: 1920×1080、vertical: 960×1080） (L179-184)

**WebEngineView 利用パターン**:
1. `_load_layout(layout_name)` (L92) — レイアウト名 → (subdir, w, h) → `self._view.resize(w, h)` で動的リサイズ (L99) → `layout/{subdir}/result.html` を file:// で読み込み (L101-106)
2. `_start_render(payload)` (L163) — `initResult(JSON.parse(\`{json_str_escaped}\`))` (L168-169)
3. `_on_title_changed()` (L171) — 100ms 遅延後 `_finalize_render()` (L174)
4. `_finalize_render()` (L176) — `self._view.grab()` → KeepAspectRatio で縮尺補正 (L181-184)

**同期ブロッキング** (L139-161):
- QEventLoop に接続した `loadFinished` で 10 秒タイムアウト (L148-151)
- `_sync_loop` で renderComplete 待機 (L154-160)

---

### 1.3 main_generate_thumbnail.py

| 関数 | 役割 |
|------|------|
| `prepare_data(settingsManager, isPreview, gameAssetManager, scoreboardNumber)` (L176) | データ・設定・テンプレート準備 |
| `generate(settingsManager, isPreview, gameAssetManager, scoreboardNumber)` (L277) | 最終サムネイル生成 |
| `save_result(pixmap, data, settings, scoreboardNumber, isPreview)` (L242) | PNG/JPG + メタデータ保存 |

**prepare_data フロー** (L176-239):
1. `SettingsManager.Get("thumbnail_config")` 読み込み (L182)
2. Template JSON ロード: `./assets/thumbnail_base/thumbnail_types/{template_id}.json` (L188)
3. `StateManager.state` から最新 state、ない場合は `./out/program_state.json` フォールバック (L196-224)
4. Preview モードなら `createFalseData()` で mock data (L225-232)
5. `(data, config, template_data)` tuple 返却

**generate フロー** (L277-301):
1. `prepare_data()`
2. `ThumbnailRenderer.instance().renderSync()` (L288-291)
3. pixmap が null なら例外 (L294-298)
4. `save_result()` で PNG/JPG 保存、ファイルパス返却 (L300-301)

**save_result 出力先** (L242-274):
- 非 Preview: `./out/thumbnails/{tag1}-vs-{tag2}-{UTC-timestamp}.png/jpg` (L252-255)
- 特殊文字除去: `<>:"/\|?* ` (L158-162)
- YouTube メタデータ (`_title.txt`, `_desc.txt`) 同時生成 (L260-267)
- Preview: `AutonaResolve("tmp/thumbnail")/template.png` (L270-274)

---

### 1.4 main_generate_result.py

| 関数 | 役割 |
|------|------|
| `prepare_data(settingsManager, isPreview, gameAssetManager, max_players)` (L29) | データ・設定準備 |
| `generate(settingsManager, isPreview, gameAssetManager, layout)` (L177) | 最終リザルト画像生成 |
| `save_result(pixmap, data)` (L160) | PNG/JPG 保存 |
| `_create_preview_data(gameAssetManager, max_players)` (L108) | Mock データ生成 |

**prepare_data フロー** (L29-105):
1. `settingsManager.Get("result_config")` (L49)
2. ロゴ・背景・accent_color を設定から or デフォルト (L50-58)
3. `StateManager.state` から tournamentInfo, numEntrants (L69-81)
4. `StateManager.Get("result_players")` から player 情報（Widget が Set） (L84)
5. Preview で player data 空なら `_create_preview_data()` (L102-103)

**generate フロー** (L177-202):
1. `_max_players_for_layout()`: "horizontal_16" → 16, その他 → 8 (L182)
2. `prepare_data()`
3. `ResultRenderer.instance().renderSync()` (L187)
4. Preview: `AutonaResolve("tmp/result")/preview.png` (L195-200)
5. 非-Preview: `save_result()` で `./out/results/{tournament}-top{n}-{timestamp}.png/jpg` (L202)

---

## 2. テンプレート参照

### 2.1 テンプレートディレクトリ構造

**Thumbnail** (2 種):
```
layout/thumbnail/
  ├── thumbnail.html
  ├── thumbnail.css
  ├── thumbnail.js           (initThumbnail() 定義)
  └── thumbnail_assets.js

layout/thumbnail_type_b/
  ├── thumbnail.html
  ├── thumbnail.css
  └── thumbnail.js
```

**Result** (4 種):
```
layout/result/             ├── result.html, result.css, result.js
layout/result_top16/       ├── result.html, result.css
layout/result_vertical/    ├── result.html, result.css
layout/result_vertical_geki/ ├── result.html, result.js, result.css
```

**共有リソース**:
```
layout/include/
  ├── jquery-3.7.1.min.js
  └── lodash.min.js
layout/thumbnail/thumbnail_assets.js   (result.html も参照)
```

### 2.2 テンプレート選択ロジック

**Thumbnail** (ThumbnailRenderer.py L94-98):
```python
def _resolve_template_id(self, template: dict) -> str:
    tid = (template or {}).get("template_id")
    if tid not in self.TEMPLATE_SUBDIRS:
        return self.DEFAULT_TEMPLATE_ID
    return tid
```

**Result** (ResultRenderer.py L92-106): レイアウト名で直接マッピング、無効 → "horizontal"

### 2.3 HTML ロード時のパラメータ形式

**Method**: JavaScript `runJavaScript()` による JSON ペイロード注入

**Thumbnail** (L181-183):
```python
self._view.page().runJavaScript('document.title = "LOADING"')
self._view.page().runJavaScript(
    f"initThumbnail(JSON.parse(`{json_str_escaped}`))")
```
- Payload: `{data, config, template}`
- Template リテラル (backtick) + バックスラッシュ/バッククォート エスケープ (L178-179)

**Result** (L167-169): 同様のパターン、Payload: `{data, config}`

**JavaScript エントリーポイント**:
- `thumbnail.js` L17: `async function initThumbnail(payload)` → `document.title = "RENDER_READY"`
- `result.js` L13: `async function initResult(payload)` → `document.title = "RENDER_READY"`

---

## 3. テンプレート選択 UI と設定永続化

### 3.1 Thumbnail テンプレート選択

**UI コンポーネント** (AutonaThumbnailSettingsWidget.py L148-166):
- `QComboBox` (templateSelect) → UI から動的構築
- 選択肢: `[("type_a", "Standard"), ("type_b", "Center Panel")]`
- コミット 0db54e2a で右側プレビュー上部に移動

**Signal フロー** (L160-166):
```python
self.templateSelect.currentIndexChanged.connect(
    lambda: self.SaveSettings(
        key="template_id",
        val=self.templateSelect.currentData(),
        generatePreview=True,
    )
)
```

**設定永続化** (L571-578):
- Key: `thumbnail_config.template_id`
- 値: `"type_a"` or `"type_b"`
- 保存先: `SettingsManager` (JSON)

**プレビュー生成フロー** (L587-602):
1. ComboBox 選択 → `SaveSettings(key="template_id", generatePreview=True)`
2. `GeneratePreview()` → 300ms debounce タイマー (L299-302)
3. `_doGeneratePreview()` → `prepare_data()` → `_renderer.render()`
4. `_onRenderComplete()` で pixmap をプレビューウィジェットに表示 (L606-615)

### 3.2 Result レイアウト選択

**UI コンポーネント** (AutonaResultSettingsWidget.py L186-209):
- `QComboBox` (layoutCombo) → 動的に作成し verticalLayout_right に挿入（コミット 0db54e2a で変更）
- 選択肢: "Horizontal", "Horizontal TOP16", "Vertical", "Vertical Dark Gold"
- currentData: `"horizontal"`, `"horizontal_16"`, `"vertical"`, `"vertical_geki"`

**_onLayoutChanged()** (L533-544):
- 表示する player 行数を動的変更
- `GeneratePreview(True)` でレイアウト切り替え後のプレビューを自動生成

### 3.3 プレビュー画像の所在

| 種別 | パス | 備考 |
|------|------|------|
| Thumbnail Preview (temp) | `AutonaResolve("tmp/thumbnail")/template.png` | 毎回上書き |
| Result Preview (temp) | `AutonaResolve("tmp/result")/preview.png` | 毎回上書き |
| Thumbnail Final | `./out/thumbnails/{p1}-vs-{p2}-{timestamp}.png/jpg` | 日時付き |
| Result Final | `./out/results/{tournament}-top{n}-{timestamp}.png/jpg` | 日時付き |

---

## 4. 出力ファイルパス

### 4.1 Thumbnail
- 非-Preview: `./out/thumbnails/{tag_p1}-vs-{tag_p2}-{ISO8601_UTC}.png/jpg`
- 特殊文字除去: `<>:"/\|?* ` (L158-162)
- 同時生成: `{filename}_title.txt`, `{filename}_desc.txt` (L260-267)

### 4.2 Result
- 非-Preview: `./out/results/{tournament}-top{num_players}-{ISO8601_UTC}.png/jpg`
- 特殊文字除去: `\/*?:"<>|` (regex) (L21)

---

## 5. WebEngine 固有の制約と実装

### 5.1 ローカルファイルアクセス

**有効化** (ThumbnailRenderer.py L70-74):
```python
settings = self._view.settings()
settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
```

### 5.2 DPI / スケールファクター

**Widget リサイズ**:
- ThumbnailRenderer: 固定 1920×1080 (L63-66)
- ResultRenderer: レイアウト依存 (L92-99)

**Pixmap キャプチャ時の補正**:
- ThumbnailRenderer: `IgnoreAspectRatio` (L218-223)
- ResultRenderer: `KeepAspectRatio` (L178-184)

**sandbox 無効化** (ThumbnailRenderer.py L24): `os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"`

### 5.3 透過 PNG 生成

**ThumbnailRenderer** (L77): `setBackgroundColor(Qt.GlobalColor.transparent)`
**ResultRenderer**: 背景設定なし（HTML/CSS で background 指定）

### 5.4 ページロード完了検知

**Signal**: `QWebEngineView.loadFinished(ok: bool)` (L88)
**Callback**: `_on_load_finished()` (L185)

### 5.5 レンダリング完了検知

**Mechanism**: JavaScript `document.title = "RENDER_READY"` で検知 (L203)
- 100ms 遅延: off-screen compositor の paint 完了を待機

---

## 6. 他レイヤーとの結合

### 6.1 AutonaThumbnailSettingsWidget → ThumbnailRenderer フロー

```
Widget UI (ComboBox/Checkbox/Button)
  ↓
SaveSettings(key, val, generatePreview=True)
  ↓
SettingsManager.Set("thumbnail_config.{key}", val)
  ↓
GeneratePreview() [→ debounce timer]
  ↓
_doGeneratePreview()
  ├─ prepare_data(SettingsManager, isPreview=True, ...)
  │   ├─ StateManager.Get("score", ...) [live state]
  │   └─ ./out/program_state.json [fallback]
  ├─ _renderer.render(data, config, template) [async]
  └─ [await] _onRenderComplete(pixmap)
        ├─ pixmap.save(tmp_file)
        └─ preview.setPixmap(pixmap)
```

**Generate (Save/Copy) フロー**:
```
Button clicked (btSaveThumbnail / btCopyToClipboard)
  ↓ thumbnail.generate(settingsManager)
  ├─ prepare_data()
  ├─ ThumbnailRenderer.instance().renderSync() [blocking]
  └─ save_result(pixmap, ...) → PNG/JPG path
```

### 6.2 AutonaResultSettingsWidget → ResultRenderer フロー

```
Player input → _syncPlayersToState() → StateManager.Set("result_players", players_dict)
Layout ComboBox changed → _onLayoutChanged() → GeneratePreview(True)
  ├─ prepare_data(max_players=_maxPlayersForLayout())
  ├─ _renderer.render(..., layout=self._selectedLayout()) [async]
  └─ _onRenderComplete(pixmap) → preview.setPixmap(pixmap)
```

### 6.3 StateManager との関係

**Thumbnail**:
- `prepare_data()` が `StateManager.state` を直接読み込み (main_generate_thumbnail.py L198)
- fallback: `./out/program_state.json` ファイル

**Result**:
- `prepare_data()` が `StateManager.state` → tournamentInfo (main_generate_result.py L70)
- `StateManager.Get("result_players")` → Widget が設定した player list (L84)

### 6.4 エラーハンドリング

- `loadFinished(ok=False)` → logger.error + loop.quit
- JS error → `javaScriptConsoleMessage()` でログ出力
- pixmap.isNull() → 例外発生（non-preview）
- Widget 側: GeneratePreview 例外 → logger.warning、Generate 例外 → QMessageBox

---

## 7. 最近のコミット変更内容（#29 関連）

### コミット 0db54e2a: テンプレート選択 UI の移動
- Thumbnail: 左側 "General Settings" → 右側 "Preview" 上部
  - `.ui` 構造変更のみ（Python コード変更なし）
- Result: 左カラムの動的追加 → verticalLayout_right の先頭
  - AutonaResultSettingsWidget.py L186-209: rightLayout への insertWidget に変更

### コミット a7286db9: リザルト画像デザイン刷新
- 対象: result, result_top16, result_vertical（result_vertical_geki は除外）
- CSS:
  - `.lighting`: rank 別色分け（金/銀/銅/青）→ 白グラデーション統一、opacity 0.6→0.55
  - `.vignette`: 削除
  - `.player-slot.rank-1/2/3`: スロット背景に金/銀/銅の放射状グラデ追加
- JS: result.js: vignette div 挿入ロジック削除

### コミット d79f7cb8: Merge #29

---

## 8. システム環境設定

| 設定項目 | 値 | 所在 |
|---------|-----|------|
| QTWEBENGINE_DISABLE_SANDBOX | "1" | ThumbnailRenderer.py L24 |
| LocalContentCanAccessFileUrls | True | ThumbnailRenderer.py L71-72 |
| LocalContentCanAccessRemoteUrls | True | ThumbnailRenderer.py L73-74 |
| Background Color | transparent | ThumbnailRenderer.py L77 |

---

## まとめ表

| レイヤー | ファイル | 役割 |
|---------|---------|------|
| Renderer | ThumbnailRenderer.py | QWebEngineView 管理、HTML ロード、JS 注入、pixmap grab |
| Renderer | ResultRenderer.py | 同上（レイアウト動的変更対応） |
| Data Preparation | main_generate_thumbnail.py | state 取得、JSON 準備、PNG/JPG 保存 |
| Data Preparation | main_generate_result.py | 同上 |
| UI Widget | AutonaThumbnailSettingsWidget.py | テンプレート選択、設定 UI、preview/generate ボタン |
| UI Widget | AutonaResultSettingsWidget.py | player input、layout 選択、preview/generate ボタン |
| Template | layout/thumbnail*/*.html/css/js | サムネイル HTML + initThumbnail() entry |
| Template | layout/result*/*.html/css/js | リザルト HTML + initResult() entry |
