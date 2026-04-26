# リファクタ候補リスト (Known Smells)

作成日: 2026-04-08
根拠: `.claude/architecture/` 配下の調査成果物 (python-imports.md, singleton-init-order.md, state-keys.md, python-hotspots.md)

本ファイルはフェーズ2の夜間タスク配布の燃料として使う。各項目に優先度・難易度・影響範囲を付記する。

---

## 凡例

- **優先度**: P1（高）> P2（中）> P3（低）
- **難易度**: D1（簡単・局所的）> D2（中程度）> D3（難・広範囲）
- **安全度**: S1（テスト可）> S2（手動確認必要）> S3（リスク高）

---

## S-01: AutonaScoreboardPlayerWidget ↔ AutonaTeamPlayerWidget の重複 [P1/D2/S2]

**根拠**: python-hotspots.md「コード重複」セクション

**現状**:
- 19個の共通メソッド名。`SetData`(174行), `SetCharactersPerPlayer`(122-125行), `SwapWith`, `SwapCharacters`, `CharactersChanged`, `LoadCountries`, `LoadStates`, `Clear` 等がほぼ同一コピー
- 合計 ~700行の重複。バグ修正が常に2箇所必要

**対策案**:
基底クラス `AutonaBasePlayerWidget(QGroupBox)` を抽出し、共通ロジックを移動。各サブクラスは差分のみ保持。
- Scoreboard版固有: `ExportPlayerId`, `SetLosers`, `SavePlayerToDB`, DB関連, `refresh_theme_styles`
- TeamBattle版固有: stock/active/dead 管理, `SpinnerHandling`, `SetBattleMode`

**影響範囲**: `AutonaScoreboardPlayerWidget.py`, `AutonaTeamPlayerWidget.py`, それぞれの import 元（5ファイル）

**リスク**: `self.path` 経由の StateManager 操作パターンが微妙に異なる箇所がある。抽出時に差分を見落とすと実行時エラー。

---

## S-02: God __init__ — AutonaScoreboardWidget (688行) [P2/D2/S2]

**根拠**: python-hotspots.md「God __init__」セクション

**現状**:
`AutonaScoreboardWidget.__init__` が688行。UI構築・シグナル接続・メニュー構築・タイマー設定が1メソッドに詰まっている。

**対策案**:
`_build_team_columns()`, `_build_score_column()`, `_build_bottom_options()`, `_connect_signals()` 等のプライベートメソッドに分割。クラスの責務自体は変えない。

**影響範囲**: `AutonaScoreboardWidget.py` のみ

**リスク**: ローカル変数をインスタンス変数に昇格する際の漏れ。分割後の初期化順序の誤り。

---

## S-03: God __init__ — Autona.Window (629行) [P2/D3/S3]

**根拠**: python-hotspots.md「God __init__」セクション

**現状**:
`Window.__init__` が629行。全ドックウィジェット生成・メニューバー構築・レイアウト復元・シングルトン初期化の Phase 2-3 を含む。

**対策案**:
`_create_dock_widgets()`, `_build_menu_bar()`, `_restore_layout()`, `_mount_ui()` 等に分割。

**影響範囲**: `Autona.py` のみ。ただし全シングルトンの初期化順序に影響しうる。

**リスク**: singleton-init-order.md で特定した初期化順序依存（AutonaGameAssetManager → AutonaTournamentDataProvider）を壊す可能性。分割後も import 順序は変えないこと。

---

## S-04: AutonaGameAssetManager.LoadGameAssets の内部クラス重複 (678行) [P2/D2/S2]

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**現状**:
`LoadGameAssets` 内に `AssetsLoaderThread(QThread)` と `AssetsLoader` の2つの内部クラスがあり、それぞれ `run()` メソッドが 327行 / 312行。両者のロジックはほぼ同一（非同期版 vs 同期版）。

**対策案**:
共通ロジックを `_load_assets_core(mods_active, mods_reload_mode)` に抽出し、Thread版/同期版はそれを呼ぶだけにする。

**影響範囲**: `AutonaGameAssetManager.py` 内部のみ

**リスク**: Thread版は `self.parent()` (QThread の parent)、同期版は `self.parent` (通常属性) でアクセスしているため、統合時に参照先の違いに注意。

---

## S-05: StartGGDataProvider のパース関数肥大化 [P1/D2/S1]

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**現状**:
- `ParseMatchDataOldApi`: 265行 — レスポンス JSON を内部構造に変換
- `ParseMatchDataNewApi`: 166行 — 同上（新API版）
- `GetTournamentPhaseGroup`: 137行
- `ProcessEntrantData`: 112行

**対策案**:
パース関数は純粋関数に近いため、Characterization Test（現在の入出力を記録するテスト）を先に書き、その後に分割・リファクタする。`TournamentDataProvider/` 配下にヘルパーモジュールを追加。

**影響範囲**: `StartGGDataProvider.py` 内部。テストを先に書けば安全に進められる。

**リスク**: Start.gg API のレスポンス構造が変わると既存テストも壊れるが、それは元々の問題。

---

## S-06: AutonaThumbnailSettingsWidget.__init__ (473行) + updateFromSettings (243行) [P3/D2/S2]

**根拠**: python-hotspots.md「God __init__」

**現状**:
設定フォームのUI構築が473行の `__init__` に詰まっている。`updateFromSettings` も243行。

**対策案**:
S-02 と同様のプライベートメソッド分割。設定項目のグループごとにメソッドを分ける。

**影響範囲**: `AutonaThumbnailSettingsWidget.py` のみ

---

## S-07: thumbnail/main_generate_thumbnail.py の巨大関数群 [P3/D2/S1]

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**現状**:
- `paste_image_matrix`: 323行 — 座標計算とペースト処理の混在
- `generate`: 261行 — エントリーポイント
- `paste_characters`: 175行

クラスを使わない関数型モジュール。17個のトップレベル関数。

**対策案**:
座標計算・画像操作・テキスト描画を別モジュールに分離。純粋関数が多いのでテスト追加しやすい。

**影響範囲**: `thumbnail/` 配下、`AutonaThumbnailSettingsWidget.py`

---

## S-08: `score.upset_factor` キーの不整合 [P1/D1/S1] ✅ 解消済（M1a / Design.md §8 T-1）

**根拠**: state-keys.md「要注意事項 #1」

**現状（解消済）**:
`AutonaStatsUtil.py` の `GetSetUpsetFactor` は両分岐とも `score.{self.scoreboardNumber}.upset_factor` を Set する形に統一済み。番号なし版（`score.upset_factor`）は存在しない。

**影響範囲**: `AutonaStatsUtil.py`

---

## S-09: StateManager.state の直接参照（ロックなし） [P2/D1/S2] ✅ 解消済（M1a / Design.md §8 T-3）

**根拠**: state-keys.md「要注意事項 #3」

**現状（解消済）**:
`StateManager.GetFullState()` がロック取得 + `deep_clone` して `{'state': ..., 'delta_index': ...}` を返す。`AutonaWebServerActions.program_state` / `src/result/main_generate_result.py` / `src/thumbnail/main_generate_thumbnail.py` はすべてこの API 経由に置き換え済み。

**影響範囲**: `AutonaWebServerActions.py`, `StateManager.py`, `src/result/`, `src/thumbnail/`

---

## S-10: モジュールトップレベルのシングルトン生成 [P3/D3/S3]

**根拠**: singleton-init-order.md「暗黙の初期化順序リスク #2」

**現状**:
全8シングルトンが `XXX.instance = XXX()` をモジュール末尾で実行。import 時に副作用（GUI 操作含む）が発生するため、pytest でのユニットテストが困難。

**対策案**:
遅延初期化パターン（`@classmethod def get_instance(cls)`）への移行。ただし全コードベースで `.instance` を参照しているため影響範囲が非常に広い。

**影響範囲**: 全ファイル。段階的に進める必要あり。

**リスク**: 初期化順序の暗黙的保証が崩れる。singleton-init-order.md の依存グラフを参照しながら進めること。

---

## S-11: `from . import ui_constants` のパッケージ経由 import [P3/D1/S2]

**根拠**: python-imports.md「間接循環」、singleton-init-order.md「リスク #3」

**現状**:
`AutonaCommentaryWidget.py`, `AutonaScoreboardPlayerWidget.py`, `AutonaScoreboardWidget.py` が `from . import ui_constants as UI` で import。`src/__init__.py` → `from .Autona import *` を経由するため、Autona.py のトップレベルが全て実行される。

**対策案**:
`from .ui_constants import ...` に変更（直接 import）。ただし `ui_constants.py` 自体が `from .SettingsManager import SettingsManager` を持つので完全には切れない。最小限の対応として `from . import ui_constants` → `from .ui_constants import icon_path, ...` に変更。

**影響範囲**: 3ファイルの import 文

---

## S-12: commentary インデックスの欠番 [P2/D1/S2] ✅ 解消済（M1a / Design.md §8 T-2）

**根拠**: state-keys.md「要注意事項 #4」

**現状（解消済）**:
`AutonaCommentaryWidget.SetCommentatorNumber` 末尾で `_compactCommentaryState(number)` を呼び、`commentary.{k}` の中で 1..N の範囲外と非数値キーを除いた survivors を 1..N に詰め直す。SaveBlock で囲んで 1 デルタにまとめる。

**影響範囲**: `AutonaCommentaryWidget.py`

---

## S-13: AutonaWebServer.py のルート肥大化 [P3/D2/S1]

**根拠**: python-hotspots.md「AutonaWebServer.py のルート肥大化」

**現状**:
101個の Flask/SocketIO ルートハンドラが1ファイルに集約。各ハンドラは短いが、追加のたびに膨張。REST と WebSocket が混在。

**対策案**:
Flask Blueprint を使ってドメイン別に分割（scoreboard, stage_strike, commentary, bracket 等）。

**影響範囲**: `AutonaWebServer.py`, `AutonaWebServerActions.py`

---

## S-14: Qt objectName 依存の動的 StateManager キー [P2/D3/S3]

**根拠**: state-keys.md「要注意事項 #2」

**現状**:
`AutonaScoreboardWidget`, `AutonaTournamentInfoWidget` 等で `element.objectName()` をキーパスに使用。`.ui` ファイルの変更でキーパスが壊れ、既存の保存データとの互換性が失われる。

**対策案**:
objectName → 明示的なキー名マッピングを導入。ただし既存の .ui ファイルとの整合性維持が必要で工数大。

**影響範囲**: 4ファイル + .ui ファイル群 + 既存の保存データ

**リスク**: 既存ユーザーの `program_state.json` との後方互換性を壊す可能性。マイグレーション戦略が必要。

---

## フェーズ2 タスク配布の推奨順序

### Wave 1（並列可能・即効性あり）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-08 | upset_factor 不整合 | D1。調査 + 1行修正。テストで確認可能 | ✅ M1a |
| S-09 | state 直接参照 | D1。StateManager に1メソッド追加 | ✅ M1a |
| S-11 | ui_constants import | D1。3ファイルの import 文変更 | 未着手 |

### Wave 2（テスト先行）
| ID | 対象 | 理由 |
|----|------|------|
| S-05 | StartGGDataProvider パース | Characterization Test を先に書けば安全 |
| S-01 | PlayerWidget 重複統合 | 最大のリファクタ効果。テスト網が欲しい |

### Wave 3（段階的）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-02 | ScoreboardWidget __init__ | 単一ファイル内。動作確認は手動 | 未着手 |
| S-04 | GameAssetManager 内部クラス | 単一ファイル内 | 未着手 |
| S-12 | commentary 欠番 | Web UI 側との整合性確認が必要 | ✅ M1a |

### Wave 4（長期）
| ID | 対象 | 理由 |
|----|------|------|
| S-03 | Window __init__ | 影響範囲広い。慎重に |
| S-10 | シングルトン遅延初期化 | 全ファイルに影響。フェーズ3以降 |
| S-14 | objectName 依存 | 後方互換性の問題。設計議論が必要 |
