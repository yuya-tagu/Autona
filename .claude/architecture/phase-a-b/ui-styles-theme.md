# スタイル・配色・テーマ定義の所在マップ（Phase A 調査結果）

**Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。**

## 1. PyQt 側のスタイル定義

### 1.1 中央管理: `src/ui_constants.py`

| カテゴリ | 内容 | 数値 |
|---------|------|------|
| Typography | FONT_XS〜FONT_XL | 8pt〜16pt |
| Typography | FONT_FAMILY | "LINE Seed JP" |
| Typography | FONT_FAMILY_CONDENSED | "Roboto Condensed" |
| Typography | FONT_FAMILY_MONO | "JetBrains Mono" |
| Spacing | SPACE_XS〜SPACE_XL | 2px〜16px |
| Icon Size | ICON_SM〜ICON_LG | 24px〜64px |
| Button Size | BTN_SM〜BTN_LG | 24px〜36px |
| Border Radius | RADIUS_SM〜RADIUS_LG | 3px〜8px |
| Color Palette (Dark) | 14 個キー | #0A0A0A〜#F0EAE0 |
| Color Palette (Light) | 14 個キー | #1A1917〜#F4F3F0 |
| Widget Height | _WIDGET_H | 26px |

**Dark palette キー:** canvas, bg_primary, bg_secondary, bg_tertiary, bg_quaternary, border_subtle/default/strong, text_primary/secondary/muted/label/disabled, brand, brand_subtle, accent, accent_hover, accent_subtle, gold, gold_subtle, positive, negative, on_accent

**関数ベースのスタイルジェネレータ:**
- `global_qss()` — モード検出して全体 QSS 生成（561 行）
- `readonly_style()`, `disabled_style()`, `secondary_label_style()`
- `auto_update_toggle_style()`, `score_btn_style(scale)`, `score_display_style(scale)`

**全体 QSS マーク (L210-561):** QWidget, QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QTabBar, QTabWidget, QDockWidget, QMenuBar, QMenu, QToolBar, QHeaderView, QTableView, QListView, QTreeView, QScrollBar, QStatusBar, QGroupBox, QCheckBox, QRadioButton, QToolTip, QProgressBar をカバー

### 1.2 setStyleSheet の直接呼び出し

**16 ファイル / 合計 48 行以上**

| ファイル | スタイル例 | 数 |
|---------|---------|-----|
| AutonaColorButton.py | `"QToolButton { background-color: %s; }"` (動的) | 2 |
| AutonaBracketView.py | `"background-color: rgba(0, 255, 0, 50);"` (ハードコード) | 8 |
| AutonaScoreboardWidget.py | スコアボタン・スコア表示スタイル関数参照 | 11 |
| AutonaScoreboardPlayerWidget.py | `UI.readonly_style()`, `UI.disabled_style()` | 2 |
| AutonaBracketWidget.py | プレースホルダースタイル | 1 |
| その他 11 ファイル | ラベル・タイトル・ボタン固有スタイル | 21 |

**ハードコード色:**
- `rgba(0, 255, 0, 50)` — 3 回（bracket 勝敗表示）
- `rgba(0, 0, 0, 80)` — 3 回
- `rgba(255,255,255,0.45)` — 2 回
- `#B5ADA0` — 1 回（empty placeholder）

### 1.3 .ui ファイル

13 ファイル確認、`<stylesheet>` タグは未検出。フォント・寸法は QSS または `ui_constants.py` が支配。

---

## 2. OBS HTML 側のスタイル定義

### 2.1 `layout/main.css`

**CSS 変数（:root）:**
```css
--font: "LINE Seed JP", sans-serif
--border-radius: 16px
--text-color: #ffffff
--bg-color: #121212
--p1-score-bg-color: #fe3636
--p1-sponsor-color: #ff7a6d
--p1-score-color: #ffffff
--p2-score-bg-color: #2e89ff
--p2-sponsor-color: #29b6f6
--p2-score-color: #ffffff
--theme-color: #181B21
--accent-color: #f0a500
--bevel-light: color-mix(in srgb, var(--theme-color), white 35%)
--bevel-dark: color-mix(in srgb, var(--theme-color), black 25%)
--tile-radius: 6px
--tile-gap: 5px
```

**コメント済み代替テーマ:**
1. Dark Solid (L82-89): `--bg-color: #18181b`, P1=#ff3837ff, P2=#1255a3ff
2. Light Gradient (L91-97): `--text-color: black`, `--bg-color: #e2ebf0ff`

**@font-face:** NotoSans (NotoSans-Regular.ttf), RobotoCondensed (RobotoCondensed.ttf)

### 2.2 テンプレート固有 CSS（21 ファイル）

| ファイル | 役割 | 色カスタマイズ |
|---------|------|---------------|
| bracket_top8/index.css | Top 8 bracket | `--theme-color: #0d1220`, `--accent-color: #ffd83d` |
| bracket_top12/index.css | Top 12 | 同上 |
| bracket_top16/index.css | Top 16 | 同上 |
| bracket_ultimate/index.css | Ultimate | main.css 継承 |
| camborder_Universal/index.css | カメラ枠 | main.css 継承 |
| commentators/index.css | コメンテーター | main.css 継承 |
| recent_sets/index.css | 最近のセット | main.css 継承 |
| result/result.css | Result Top 8 | `rgba(0,0,0,0.7)`, `rgba(255,255,255,0.8)` |
| result_top16/result.css | Top 16 | 同上 |
| result_vertical/result.css | 縦版 | 同上 |
| result_vertical_geki/result.css | 縦版・激 | 同上 |
| scoreboard/index.css | 標準 | main.css 継承 |
| scoreboard/multicam.css | マルチカム | main.css 継承 |
| scoreboard_Universal/index.css | 汎用 | main.css 継承 |
| scoreboard_simple/index.css | シンプル | main.css 継承 |
| scoreboard_topleft/index.css | 左上 | main.css 継承 |
| thumbnail/thumbnail.css | サムネ Type A | `--p1-color`, `--p2-color`（動的） |
| thumbnail_type_b/thumbnail.css | サムネ Type B | 同上 |
| top_8/index.css | Top 8 | main.css 継承 |
| tournament_history/index.css | 履歴 | main.css 継承 |

**色定義の重複:**
- main.css で 30 個の色・寸法変数定義
- 各テンプレが主に変数参照で上書き
- bracket_top8/16 のみ独自 `--theme-color`, `--accent-color` を定義

**CSS 変数参照数:** `var(--text-color)` 31 回, `var(--bg-color)` 28 回, `var(--p1-score-bg-color)` 12 回, `var(--p2-score-bg-color)` 12 回, `var(--accent-color)` 18 回 — **layout/ 全体で 792 個の色/寸法参照**

---

## 3. WebEngine レンダラーが使う CSS

### 3.1 ResultRenderer

```python
LAYOUTS = {
    "horizontal": ("result", 1920, 1080),
    "horizontal_16": ("result_top16", 1920, 1080),
    "vertical": ("result_vertical", 960, 1080),
    "vertical_geki": ("result_vertical_geki", 960, 1080),
}
```

**CSS チェーン:**
1. layout/{subdir}/index.html を QWebEngineView でロード
2. index.html が result.css を読み込む
3. result.css が main.css に依存（import or 変数参照）

### 3.2 ThumbnailRenderer

```python
TEMPLATE_SUBDIRS = {
    "type_a": "thumbnail",
    "type_b": "thumbnail_type_b",
}
```

**CSS チェーン:**
1. layout/{template}/thumbnail.html をロード
2. thumbnail.css で `--p1-color`, `--p2-color` を受け取る
3. JavaScript で動的に CSS 変数注入

### 3.3 Injected CSS

ResultRenderer / ThumbnailRenderer ともに追加 CSS 注入ロジックは未検出。HTML の `<style>` または `<link>` で全管理。

---

## 4. React Web UI 側のスタイル定義

### 4.1 `web/src/themes.js`

**DARK_TOKENS (20 キー):**
- canvas: #0A0A0A, bgPrimary: #121212, bgSecondary: #1A1A1A, bgTertiary: #252524, bgQuaternary: #2F2F2D
- borderSubtle: #252524, borderDefault: #393936, borderStrong: #55534E
- textPrimary: #F0EAE0, textSecondary: #B5ADA0, textMuted: #85807A, textLabel: #A29A90, textDisabled: #524F4A
- brand: #DC143C, brandSubtle: #2A1418
- accent: #F5A545, accentHover: #FFB960, accentSubtle: #2F2820
- gold: #F0C45A, goldSubtle: #2E2A1E

**LIGHT_TOKENS:** 同キー、値反転（canvas: #E8E7E4 等）

**Typography:**
- fontFamily: ['"LINE Seed JP"', '"Inter"', '"Noto Sans JP"', ...]
- fontSize: 13
- button: { fontWeight: 700, letterSpacing: '0.04em', textTransform: 'none' }

**MUI Component Overrides:**
- MuiCssBaseline, MuiPaper, MuiAppBar
- MuiButton (primary/outlined/text)
- MuiOutlinedInput (border, focus)
- MuiTab, MuiTabs (selected, indicator)
- MuiChip, MuiTooltip, MuiDivider
- カスタムスロット: `p1color`, `p2color` (palette.autona)

**注:**
- web/src/themes.js の `accent` (DARK: #F5A545, LIGHT: #A06314)
- src/ui_constants.py の `accent` (#8A1A2C dark, #8A1A2C light)
- layout/main.css の `--accent-color` (#f0a500)
→ いずれも値が異なる

### 4.2 `web/src/App.css`（StageStrikePage 専用）

- `.text` (flex 配置)
- `.container`, `.neutral`, `.counterpick` (flex grid)
- `.title`, `.rules`, `.stage-name`, `.stage-icon`, `.stage-container`
- `.stamp` (SVG icon 配置)
- `.banned_by` (`#000000aa` overlay)

色:
- `color: white`, `background-color: rgba(0, 0, 0, 0.8)` (stage-name)
- `background-color: #424242` (stage-container)

### 4.3 `web/src/index.css`

```css
body { background-color: #0A0A0A; color: #F0EAE0; font-family: "LINE Seed JP", "Inter", "Noto Sans JP", ...; }
code { font-family: "JetBrains Mono", ...; }
```

---

## 5. フォント定義

### 5.1 `assets/font/`（24 ファイル）

| ファミリー | ファイル | 数 |
|----------|---------|-----|
| GenEiChikuMin | GenEiChikugoMin3-R.ttf | 1 |
| GenEiKiwamiGo | GenEiKiwamiGo.ttf | 1 |
| GenEiMGothic | 5 種 (Black, Bold, Heavy, Medium, Regular) | 5 |
| LINESeedJP | 3 種 (Bold, Regular, Thin) | 3 |
| OpenSans | 11 種 | 11 |
| RobotoCondensed | RobotoCondensed.ttf | 1 |
| TamanegiKaisho | TamanegiKaishoGeki.ttf | 1 |
| kaiso_up | Kaisotai-Next-UP-B.ttf | 1 |

### 5.2 フォント参照マッピング

| 層 | 主フォント | 参照元 |
|---|---|---|
| PyQt デスクトップ | LINE Seed JP | src/ui_constants.py, setStyleSheet, QSS |
| PyQt | Roboto Condensed | ui_constants.py FONT_FAMILY_CONDENSED |
| PyQt | JetBrains Mono | ui_constants.py FONT_FAMILY_MONO（スコア表示） |
| OBS HTML | LINE Seed JP | layout/main.css `--font`, 各 index.css |
| OBS HTML | NotoSans, RobotoCondensed | layout/main.css @font-face |
| WebEngine/Result | LINE Seed JP | layout/result/*.css |
| React Web | LINE Seed JP | web/src/themes.js, web/src/index.css |
| React Web | Inter, Noto Sans JP | themes.js fontFamily フォールバック |
| React Web | JetBrains Mono | index.css `<code>` |

`addApplicationFont()` で明示ロードする箇所は確認不可。LINESeedJP は assets/font/ に存在するが、PyQt/OBS/Web いずれもシステムフォントとしてインストール済み前提と推定。

---

## 6. 色のハードコード状況

### 6.1 集計

| パターン | 発見数 | 主な箇所 |
|---------|--------|---------|
| #ffffff / #FFFFFF | 66 回 | PyQt ラベル, OBS CSS, React |
| #121212 / #0A0A0A | 16 回 | PyQt ui_constants, web/src/index.css |
| rgba(255, 255, 255, ...) | 48 回 | OBS bracket/result, PyQt AutonaBracketView |
| 固有 hex 色 | 100+ | bracket/result テンプレ、ハードコード player1/2 |

### 6.2 同一色の複数定義

| 色 | PyQt | OBS | Web | 定義箇所数 |
|----|------|-----|-----|-----------|
| #0A0A0A (canvas) | ui_constants.py DARK | main.css (コメント) | index.css body | 3 |
| #121212 (bg_primary) | ui_constants.py DARK | main.css `--bg-color` | themes.js bgPrimary | 3 |
| #F0EAE0 (text_primary) | ui_constants.py DARK | - | index.css body color | 2 |
| #f0a500 (accent) | - | main.css `--accent-color` | - | 1 (OBS のみ) |
| #fe3636 (P1 score) | - | main.css `--p1-score-bg-color` | - | 1 (OBS のみ) |
| white / #ffffff | 複数ラベル | 多数 | - | 非常に多い |

**accent 色の不一致:**
- ui_constants.py `accent` = `#8A1A2C`
- themes.js `accent` (DARK) = `#F5A545`
- layout/main.css `--accent-color` = `#f0a500`

---

## 7. 寸法トークン

### 7.1 PyQt 定数化

```python
FONT_XS=8, FONT_SM=9, FONT_MD=11, FONT_LG=13, FONT_XL=16
SPACE_XS=2, SPACE_SM=4, SPACE_MD=8, SPACE_LG=12, SPACE_XL=16
ICON_SM=24, ICON_MD=32, ICON_LG=64
BTN_SM=24, BTN_MD=28, BTN_LG=36
RADIUS_SM=3, RADIUS_MD=5, RADIUS_LG=8
_WIDGET_H=26
```

全体 QSS で使用: padding 12/6/10/14/18px (複数), margin 4/6px (複数), border-radius RADIUS_SM/MD/LG, font-size FONT_XS〜FONT_XL。

### 7.2 OBS CSS

`layout/main.css`:
```css
--border-radius: 16px
--tile-radius: 6px
--tile-gap: 5px
```

`bracket_top8/index.css`:
```css
--card-width: 380px, --card-height: 112px, --row-gap: 28px, --col-gap: 60px
```

result/*.css は padding 40px, 20px, 4px などを直接指定。寸法の定数化は限定的。

### 7.3 React Web

`web/src/themes.js`: `shape: { borderRadius: 4 }`（MUI デフォルト）。各コンポーネント override で個別設定。

---

## 8. ダーク/ライト切替

| レイヤー | 実装方式 | 現状 |
|---------|---------|------|
| PyQt | `SettingsManager.Get("light_mode", False)` で palette() 切替 | DARK/LIGHT 各 14 キー定義済み |
| OBS | HTML の :root 変数で静的定義 + JS runtime 上書き（推定） | main.css にコメント済み代替テーマ |
| WebEngine | layout/*.html の runtime に tournamentInfo で CSS 変数注入（推定） | 動的注入で `--theme-color` 等を上書き |
| React | localStorage/Context で theme mode 管理（推定） | `buildTheme(mode)` で 2 セット生成 |

---

## 9. 最終集計表

| 項目 | PyQt | OBS | WebEngine | React | 合計 |
|------|------|-----|-----------|-------|------|
| 色定義ファイル数 | 1 (ui_constants.py) | 21 CSS | 4 (result*, thumbnail*) | 1 (themes.js) + 2 CSS | 29 |
| 中央管理色セット | 1 (DARK/LIGHT 各 14 キー) | 1 (main.css 13 変数) | - | 1 (DARK/LIGHT 各 20 キー) | 3 |
| ハードコード色 | 12+ (rgba, hex) | 100+ (各テンプレ独自) | 多数 (result 用) | 少数 (App.css) | 200+ |
| フォント定義 | 3 種 | 2 種 (@font-face) | LINE Seed JP | 4 種 (fontFamily スタック) | 計 5 種固有 |
| setStyleSheet コールサイト | 16 ファイル | - | - | - | 16 |
| 寸法トークン | 28 個定義 | 8 個定義 | 多数直接値 | MUI デフォルト | 50+ |
