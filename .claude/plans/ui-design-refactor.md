# UI 実装方式リファクタリング — フェーズ計画

## 目的
PyQt / OBS HTML / WebEngine 画像生成 / React Web UI の 4 系統が混在する Autona の UI 実装方式を整理し、あるべき姿を Design.md として定義する。

## スコープ境界（PdM 確定）

### 「自由・独自」領域 — リファクタ対象外
- **OBS 用 HTML レイアウト**（`layout/scoreboard*`, `layout/bracket*`, `layout/recent_sets`, `layout/commentators`, `layout/camborder*`, `layout/top_8`, `layout/tournament_history`）
- **サムネイル / リザルト画像生成テンプレ**（`layout/thumbnail*`, `layout/result*`）
- 理由: 大会配信・クリエイティブ制作物のため、テンプレートごとに独自配色・独自レイアウトを持てるのが仕様。

### 「統一すべき」領域 — リファクタ対象
- **PyQt デスクトップ UI**（`src/**/*.py`, `src/layout/*.ui`）
- **React Web UI**（`web/src/`）
- 理由: アプリ内部のデザインは一貫性がブランド価値・UX の前提。

### accent 色の方針（PdM 確定）
- PyQt `#8A1A2C` と React `#F5A545` は揃える（どちらに寄せるかは Phase C で議論）
- OBS `#f0a500` は意図的に分けたまま（クリエイティブ領域の色）

### size="small" の方針（PdM 確定）
- React 側で徹底されていないのは指摘事項として扱う（Phase B のチェックリストに含める）

---

## Phase B 観点の優先順位（PdM 確定）

1. **(a) 同一概念の重複表現マップ** — どの UI 概念がどの系統に何回現れるか
2. **(b) 状態同期経路の正確化** — StateManager のキーがどの UI にどう届くか
3. **(d) 設定散在状況** — SettingsManager / settings.json / user_data の責務
4. **(e) テーマ・色トークンの統合可能性** — accent 色含む統合方針の素案
5. **(c) 拡張時の修正範囲** — 新ゲーム / 新テンプレ追加の影響範囲

## 比較対象アプリ（PdM 確定）
- **OBS Studio** — 同種（配信ツール、Qt + ブラウザソース）
- **ClickUp** — Web ベース SaaS の UI 一貫性・スケール設計

## 成果物
- Phase A: `.claude/architecture/ui-{pyqt,obs-layouts,image-renderers,web-react,styles-theme}.md` — **完了**
- Phase B: `.claude/architecture/ui-problems.md` — **完了**（問題整理 + OBS/ClickUp 比較統合）
- Phase C: `Design.md`（プロジェクトルート） — あるべき姿仕様書

## Phase B 終了時の PdM 確定事項

| 課題 | 方針 |
|------|------|
| `score.upset_factor` 二重定義 | 番号なし版を削除 |
| `commentary.{N}` インデックス欠番 | Unset 時に詰める実装に変更 |
| React 操作の最大 1250ms 遅延 | `applySavedDeltas` のバッチ間隔を短縮 |
| OBS HTML poll の順序保証 | Socket.IO 接続を必須にする / poll 順序保証を追加 |
| accent 色 | **クリムゾン `#8A1A2C` に統合**（React 側を寄せる） |
| OBS `--accent-color: #f0a500` | **分離維持**（クリエイティブ領域） |

## Phase C: Design.md 構成

```
1. 設計原則
2. UI レイヤー定義
   2.1 PyQt デスクトップ UI（操作・編集）
   2.2 React Web UI（リモート操作）
   2.3 OBS HTML レイアウト（配信表示）
   2.4 WebEngine 画像生成（成果物出力）
   2.5 各レイヤーの責務境界
3. データモデル
   3.1 StateManager キーパス完全仕様
   3.2 同期経路の規約
   3.3 設定永続化の規約
4. 画面仕様
   4.1 ドックウィジェット一覧と責務
   4.2 React ページ仕様
   4.3 OBS テンプレ命名規約
   4.4 画像生成テンプレ命名規約
5. デザインシステム
   5.1 統合色トークン
   5.2 タイポグラフィ
   5.3 寸法トークン
   5.4 テーマ切替
   5.5 クリエイティブ領域の独立性
6. ホットキー仕様
7. 拡張ガイドライン
8. リファクタ移行プラン
```
