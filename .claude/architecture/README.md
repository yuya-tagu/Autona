# Architecture research notes

> **性格: 凍結された調査ログ（2026-04 時点のスナップショット）。原則として更新しない。**
> 現状を追従する「変更影響マップ」は同名の [../../docs/architecture/](../../docs/architecture/) が担う。性格が逆なので混同しないこと。
>
> ⚠️ **特に陳腐化している項目（2026-07-30 時点）**: `phase-a-b/singleton-init-order.md` が記述する
> 「import 順序 = 初期化順序」「モジュール末尾でのシングルトン生成」は、起動アーキテクチャ再設計
> （[../../docs/changes/startup-architecture-redesign.md](../../docs/changes/startup-architecture-redesign.md)）で構造ごと廃止された。
> 現在の正本は [../../src/bootstrap.py](../../src/bootstrap.py) の `bootstrap_core()`。
> 同様に、タブ化 Dock + `QMenuBar` を前提とする `phase-a-b/ui-pyqt.md` / `ui-problems.md` の
> レイアウト記述（`windowState_v8` / `tabifyDockWidget` / `AutonaDockTabTheme`）も現存しない。

このディレクトリは Autona の設計に関する**調査成果物**を保管する。

## 正本

正本は **`.claude/design/Design.md`**（または旧パス `.claude/worktrees/ui-generation-refactor/Design.md`）。

本ディレクトリの内容は**根拠資料・調査ログ**であり、実装判断で `Design.md` と矛盾する場合は **`Design.md` を優先**する。

調査ファイルを更新する場合は、`Design.md` に反映が必要か確認する。

## 構成

```
.claude/architecture/
├── README.md             # 本書
└── phase-a-b/            # Phase A/B（UI 実装方式リファクタ調査、2026-04）
    ├── ui-pyqt.md            # PyQt UI レイヤー
    ├── ui-obs-layouts.md     # OBS HTML レイアウト
    ├── ui-image-renderers.md # WebEngine 画像生成
    ├── ui-web-react.md       # React Web UI
    ├── ui-styles-theme.md    # スタイル/テーマ定義の所在
    ├── ui-problems.md        # 重複/同期/設定散在/拡張範囲/テーマ統合 + OBS/ClickUp 比較
    ├── state-keys.md         # StateManager キーパス Read/Write マップ
    ├── python-imports.md     # src/ の import 関係マップ
    ├── python-hotspots.md    # 巨大ファイル・関数・コード重複
    └── singleton-init-order.md  # シングルトン初期化順と依存グラフ
```

## 役割

| ファイル種別 | 性質 | 信頼区分 |
|------------|------|---------|
| `Design.md` | 正本仕様 | 実装の判断基準 |
| `phase-a-b/*.md` | 調査ログ・事実情報 | 過去時点での実装の事実、現状実装は変化している可能性あり |

## 利用ルール

1. 実装判断の根拠が必要なときは `Design.md` を最優先で参照する
2. 「なぜこの設計になったか」の経緯を追うときは `phase-a-b/*.md` を参照
3. `phase-a-b/*.md` を更新する場合、ファイル冒頭の「Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。」の前提を維持する
4. 新たな調査を行う場合は `phase-{name}/` を新設し、本書 README に追記する
