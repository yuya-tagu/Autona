# リファクタ候補リスト (Known Smells)

作成日: 2026-04-08
最終検証: 2026-06-24（全項目を実コードに突き合わせ、行数・件数・解消状況を更新）
根拠: `.claude/architecture/` 配下の調査成果物 (python-imports.md, singleton-init-order.md, state-keys.md, python-hotspots.md)

本ファイルはフェーズ2の夜間タスク配布の燃料として使う。各項目に優先度・難易度・影響範囲を付記する。

> **2026-06-24 検証の総括**: 作成から約2.5ヶ月で実態と大きく乖離していた。S-07 は WebEngine 移行で実質解消、S-01 は基底クラス抽出で部分解消、S-06 は縮小。一方 S-02 / S-05 はむしろ肥大化。S-10 / S-11 / S-13 は件数が古かった。各項目に検証結果を追記済み。

---

## 凡例

- **優先度**: P1（高）> P2（中）> P3（低）
- **難易度**: D1（簡単・局所的）> D2（中程度）> D3（難・広範囲）
- **安全度**: S1（テスト可）> S2（手動確認必要）> S3（リスク高）

---

## S-01: AutonaScoreboardPlayerWidget ↔ AutonaTeamPlayerWidget の重複 [P1/D2/S2] ✅ 実質解消（2026-06-27）

**根拠**: python-hotspots.md「コード重複」セクション

**経緯**:
- 基底クラス `AutonaBasePlayerWidget(QGroupBox)`（`src/AutonaBasePlayerWidget.py`）が抽出済みで、両クラスが継承。真に同一な9メソッドは抽出済みだった。
- 2026-06-27、残4メソッドを精査。**クリーンに統合できる2つを昇格**:
  - `SwapWith`: 本体95%同一、差は restore 末尾のみ → 基底に昇格し `_swapRestoreTail(data)` フックで吸収（Scoreboard=`ExportPlayerId`、Team=`processEvents`+`ExportPlayerImages`）。
  - `LoadCountries`: completer 設定は同一、差は末尾スタイルのみ → 基底に昇格し `_styleCountryStateWidgets(country, state)` フックで吸収（Scoreboard=placeholder、Team=font）。
  - 約49行の重複を削減。

**意図的に分岐のため統合しない2つ（残すのが正解）**:
- `SetCharactersPerPlayer`: アイコンサイズ(16 vs 48)・フォント・`NoIconProxyModel` 有無・move ボタン非表示・幅調整が全面的に相違。1v1 と team battle で別設定の UI ビルダー。
- `SetData`: real_name/custom_textbox/controller/seed フィールドの有無、`ExportPlayerId`、**mains-list 処理が別実装**（Scoreboard はバグ修正済・Team は旧挙動）、proxy vs 直接モデルアクセス。
- これらは「重複」ではなく意図的差分。フック多数での統合は可読性を下げ、テスト網の無い中核 UI でリグレッションを招くため**統合しない**判断（PdM 承認済）。

**影響範囲**: `AutonaBasePlayerWidget.py`, `AutonaScoreboardPlayerWidget.py`, `AutonaTeamPlayerWidget.py`

**検証**: py_compile OK、pytest 73件グリーン（widget は自動テスト対象外）、`python main.py` 起動正常（LoadCountries は選手ウィジェット __init__ で実行され、失敗時は exit() するため起動継続が動作の証左）。**SwapWith の手動スワップ確認は S2 として残る**（agent-pitfalls #5/#7 留意）。

---

## S-02: God __init__ — AutonaScoreboardWidget [P2/D2/S2] 🔴 未着手（肥大化）

**根拠**: python-hotspots.md「God __init__」セクション

**2026-06-24 検証**: `AutonaScoreboardWidget.__init__` は記述の 688行 → **884行に増加**（`src/AutonaScoreboardWidget.py` 94〜977行、ファイル総行数 1985行）。UI構築・シグナル接続・メニュー構築・タイマー設定が1メソッドに詰まっている状況は変わらず、むしろ悪化している。

**対策案**:
`_build_team_columns()`, `_build_score_column()`, `_build_bottom_options()`, `_connect_signals()` 等のプライベートメソッドに分割。クラスの責務自体は変えない。

**影響範囲**: `AutonaScoreboardWidget.py` のみ

**リスク**: ローカル変数をインスタンス変数に昇格する際の漏れ。分割後の初期化順序の誤り。

---

## S-03: God __init__ — Autona.Window [P2/D3/S3] 🔴 未着手

**根拠**: python-hotspots.md「God __init__」セクション

**2026-06-24 検証**: `Window.__init__` は記述の 629行 → **662行**（`src/Autona.py` 199〜860行、ファイル総行数 1162行）。全ドックウィジェット生成・メニューバー構築・レイアウト復元・シングルトン初期化の Phase 2-3 を含む状況は変わらず。

**対策案**:
`_create_dock_widgets()`, `_build_menu_bar()`, `_restore_layout()`, `_mount_ui()` 等に分割。

**影響範囲**: `Autona.py` のみ。ただし全シングルトンの初期化順序に影響しうる。

**リスク**: singleton-init-order.md で特定した初期化順序依存（AutonaGameAssetManager → AutonaTournamentDataProvider）を壊す可能性。分割後も import 順序は変えないこと。

---

## S-04: AutonaGameAssetManager.LoadGameAssets の内部クラス重複 [P2/D2/S2] ✅ 解消（2026-06-27）

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**2026-06-24 検証**: `LoadGameAssets` 内に `AssetsLoaderThread(QThread)` と `AssetsLoader` の2つの内部クラスがまだ存在。`run()` はそれぞれ **346行 / 313行**で約90%同一だが diverge していた。

**2026-06-27 実施**: 共通ロジックを `AutonaGameAssetManager._LoadGameAssetsCore(self, game, mods_active, run_models)` と
`_ResolveTargetGame(self, game, mods_reload_mode)` の2メソッドに抽出。両 `run()` は薄い委譲に縮小。差分はフックで吸収:
- **親アクセス**: core は `self`（manager）で統一（Thread版 `self.parent()` / 同期版 `self.parent` の差を呼び出し側で吸収）
- **モデル再構築**: `run_models(fn)` フック。Thread版＝`gui_executor.run_sync(fn)`（GUIスレッドへ marshal、boot SIGSEGV 回避）、同期版＝`fn()` パススルー
- **lock/status/waitForDone** は Thread 固有のため Thread版 `run()` の `try/finally` に残置

**統合で解消した diverge（既存バグ）**:
1. `preset_colors` の型不一致: 同期版が `{}` 既定＋`.keys()`（dict扱い）だったが、正本は **list**（`__init__` 既定 `[]` / `UpdateColorModel` が `range(len())`）。core で list に統一 → ゲーム切替時のカラー表示名翻訳が復活。
2. `SaveBlock` 欠落: 同期版は2回の `StateManager.Set` が個別ファイル書き込みだった → core が常時 SaveBlock で囲み、切替を1書き込みにバッチ化（レスポンス改善）。
3. 例外時クリーンアップの非対称: 同期版に `finally` が無くリソース/ロックリーク余地 → 構造統一で解消。

**影響範囲**: `AutonaGameAssetManager.py` 内部のみ

**検証**: py_compile OK、`python main.py` 起動正常（worker スレッドで `_LoadGameAssetsCore` 実行・`UpdateColorModel` が list から正常生成を確認）、pytest 73件グリーン。
**未検証（要 PdM 手動）**: ゲーム/モード切替（同期版パス）でのキャラ/スキン/カラー/ステージ再構築と、切替が1書き込みになることの目視。

---

## S-05: StartGGDataProvider のパース関数肥大化 [P1/D2/S1] ✅ 実質解消（前提は計測ミス・2026-06-27）

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**2026-06-27 訂正（重要）**: 「795行/626行の神関数」という前提は**計測ミス**だった。`def` から
次の `def` までを素朴にカウントした際、間に挟まる多数の別メソッド（`ProcessFutureSet` 1197 〜
`GetEntrantsWorker` 1865 等）を誤って合算していた。実ファイルの確定行数:

| 関数 | 旧主張 | 実際 | 状態 |
|------|--------|------|------|
| `ParseMatchDataOldApi` | 795行 | **47行** | 既に `_extract_char_selections`/`_find_latest_winner`/`_parse_stage_strike`/`_build_entrants_old_api` へ委譲済み |
| `ParseMatchDataNewApi` | 276行 | **31行** | 既に `_build_set_metadata`/`_parse_participant_new_api` へ委譲済み |
| `GetTournamentPhaseGroup` | 626行 | **138行** | ネットワーク取得＋4変換ブロックの中規模 |

**S-05 が本来求めた「神関数の分割」は、過去の作業で既に完了済み**。当初の対象は存在しない。

**2026-06-24 安全網**: Characterization Test `tests/test_startgg_parse.py` は既存（当初の「未作成」は誤り）。
検証時 5件が赤だったが全て意図的挙動変更へのテスト陳腐化（stream 形式/既定、state の city→state 推論削除、
pronoun 撤去）。現行挙動に合わせ修復し **62件グリーン化済み**。

**2026-06-27 実施（実態に残る本物の改善 3点）**:
1. **`oldData` デッドコード除去**: `GetTournamentPhaseGroup` がレガシー REST API を毎回叩いて未使用だった
   ブロックを削除。不要なネットワーク往復（タイムアウト時 最大 ~30秒）を解消。
2. **`_parse_stage_strike`（実質最大の167行）を3分割**: `_scan_stage_strike_tasks`（タスク逆走査）+
   `_build_stage_strike_dicts`（ID解決＋dict構築）+ 薄いオーケストレータ。`test_stage_strike_*` がカバー。
3. **`GetTournamentPhaseGroup` ↔ `GetAnalysisPhaseGroup` の重複共通化**: sets→rounds 変換を
   `_parse_sets_to_rounds(sets, include_full_round_text)`、GF Reset 分割を `_split_gf_reset()` に抽出し両者から呼ぶ。
   純粋関数なので単体テスト11件を新規追加（計 **73件グリーン**）。seeds/teams は player 抽出差が大きく共通化せず。

**残骸メモ（任意・別件）**: pronoun 撤去後、i18n に `settings.show_pronouns` / `layout_options.chip_pronouns_display`
のラベルが残存（実害なし）。翻訳整理時に除去可。

**未検証（要 PdM 手動）**: `GetTournamentPhaseGroup`/`GetAnalysisPhaseGroup` はネットワーク依存で単体テスト網が
無いため、実トーナメント読込での Bracket / Analysis ドック描画（GF/GF Reset の別カラム表示含む）の目視確認が
最終確認として残る。共通化は strictly-safer な null 処理を採用（挙動差は壊れたエッジケースのみ）。

**リスク**: Start.gg API のレスポンス構造が変わると既存テストも壊れるが、それは元々の問題。agent-pitfalls #11（GQL クエリ 20秒タイムアウト）にも留意。

---

## S-06: AutonaThumbnailSettingsWidget.__init__ + updateFromSettings [P3/D2/S2] 🟢 縮小（優先度低下）

**根拠**: python-hotspots.md「God __init__」

**2026-06-24 検証**: 記述から縮小。`src/AutonaThumbnailSettingsWidget.py`:
- `__init__`: 473行 → **286行**（行79-364）
- `updateFromSettings`: 243行 → **121行**（行472-592）

すでに分割が進んだとみられ、緊急度は下がった。残作業があれば S-02 と同様のプライベートメソッド分割で対応。

**影響範囲**: `AutonaThumbnailSettingsWidget.py` のみ

---

## S-07: thumbnail/main_generate_thumbnail.py の巨大関数群 [P3/D2/S1] ✅ 実質解消

**根拠**: python-hotspots.md「巨大関数 TOP 15」

**2026-06-24 検証（解消済）**: サムネイル生成が **WebEngine ベースの `ThumbnailRenderer`（`src/thumbnail/ThumbnailRenderer.py` の `render` / `renderSync`）に移行**したため、旧 PIL ベースの `paste_image_matrix` / `paste_characters` は削除済み。`main_generate_thumbnail.py` はファイル 759行相当 → **312行**、トップレベル関数 17個 → **6個**（createFalseData, remove_special_chars, _resolve_template_id, prepare_data, save_result, generate）に縮小。本項目は対応不要。

**影響範囲**: （対応済み）`thumbnail/` 配下、`AutonaThumbnailSettingsWidget.py`

---

## S-08: `score.upset_factor` キーの不整合 [P1/D1/S1] ✅ 解消済（M1a / Design.md §8 T-1）

**根拠**: state-keys.md「要注意事項 #1」

**現状（解消済・2026-06-24 再確認）**:
`AutonaStatsUtil.py` の `GetSetUpsetFactor`（174行・177行）は両分岐とも `score.{self.scoreboardNumber}.upset_factor` を Set する形に統一済み。番号なし版（`score.upset_factor`）は存在しない。

**影響範囲**: `AutonaStatsUtil.py`

---

## S-09: StateManager.state の直接参照（ロックなし） [P2/D1/S2] ✅ 解消済（M1a / Design.md §8 T-3）

**根拠**: state-keys.md「要注意事項 #3」

**現状（解消済・2026-06-24 再確認）**:
`StateManager.GetFullState()`（194-207行）がロック取得 + `deep_clone` して `{'state': ..., 'delta_index': ...}` を返す。`AutonaWebServerActions.program_state` / `src/result/main_generate_result.py` / `src/thumbnail/main_generate_thumbnail.py` はすべてこの API 経由に置き換え済み。

**影響範囲**: `AutonaWebServerActions.py`, `StateManager.py`, `src/result/`, `src/thumbnail/`

---

## S-10: モジュールトップレベルのシングルトン生成 [P3/D3/S3] 🔴 未着手

**根拠**: singleton-init-order.md「暗黙の初期化順序リスク #2」

**2026-06-24 検証**: `.instance =` パターンは記述の 8個 → **11クラス**に増えている。遅延初期化 `get_instance()` への移行は未実施。
- モジュール末尾でトップレベル実行（条件付き含む）: `AutonaAlertNotification`, `AutonaHotkeys`, `AutonaScoreboardManager`, `AutonaTournamentDataProvider`, `AutonaUpdater`, `AutonaCountryHelper`, `AutonaControllerHelper`, `AutonaGameAssetManager`
- `__init__` 内で `self` 代入（遅延割り当ての一種）: `AutonaAnalysisWidget`, `AutonaBracketWidget`, `AutonaPlayerListWidget`

import 時に副作用（GUI 操作含む）が発生するため pytest でのユニットテストが困難。

**対策案**:
遅延初期化パターン（`@classmethod def get_instance(cls)`）への移行。ただし全コードベースで `.instance` を参照しているため影響範囲が非常に広い。

**影響範囲**: 全ファイル。段階的に進める必要あり。

**リスク**: 初期化順序の暗黙的保証が崩れる。singleton-init-order.md の依存グラフを参照しながら進めること。

---

## S-11: `from . import ui_constants` のパッケージ経由 import [P3/D1/S2] ❌ 却下（前提無効・2026-06-24）

**根拠（当初）**: python-imports.md「間接循環」、singleton-init-order.md「リスク #3」

**2026-06-24 却下判断**: 本項目の前提（import スタイル変更で `src/__init__.py` → Autona.py の連鎖を回避できる）は技術的に成立しないため却下する。

1. **連鎖は回避不能**: `src` のサブモジュールを import すると、`from . import ui_constants` でも `from .ui_constants import X` でも、必ず先に `src/__init__.py`（=`from .Autona import Window, App`）が実行される。これは Python の仕様で、形式変更では連鎖は消えない。
2. **テスト分離に無関係**: `tests/conftest.py` の `direct_import()` は `spec_from_file_location` で `src` パッケージを完全にバイパスする。UI ウィジェット群は相対 import 多数でそもそも direct_import 不可のため、どちらの形式でもテスト容易性は変わらない。
3. **「直接 import 化」は D1 ではない**: `UI.` エイリアスを保ったまま直接 import する手段が無く、真に直接 import 化するには `from .ui_constants import FONT_SM, global_qss, ...` へ変え、**140箇所の `UI.x` → `x` を全書き換え**＋ファイル別シンボル列挙が必要。アプリ全体の UI モジュールへの純粋な churn で、機能・テスト容易性の利得ゼロ、リグレッションリスクのみ。
4. 唯一の理論的利点（間接循環）は現代 Python が `from . import X` で吸収済みで、現在アプリは正常起動している。

現状（参考）: `from . import ui_constants as UI` を13ファイルが使用、`UI.` 参照は計140箇所。テスト容易性を本気で上げたいなら本項目ではなく **S-10（シングルトン遅延初期化）** が本丸。

**影響範囲**: （対応不要）

---

## S-12: commentary インデックスの欠番 [P2/D1/S2] ✅ 解消済（M1a / Design.md §8 T-2）

**根拠**: state-keys.md「要注意事項 #4」

**現状（解消済・2026-06-24 再確認）**:
`AutonaCommentaryWidget.SetCommentatorNumber`（136行）末尾で `_compactCommentaryState(number)`（159行）を呼び、`commentary.{k}` の中で 1..N の範囲外と非数値キーを除いた survivors を 1..N に詰め直す。SaveBlock で囲んで 1 デルタにまとめる。

**影響範囲**: `AutonaCommentaryWidget.py`

---

## S-13: AutonaWebServer.py のルート肥大化 [P3/D2/S1] 🔴 未着手（記述一部陳腐化）

**根拠**: python-hotspots.md「AutonaWebServer.py のルート肥大化」

**2026-06-24 検証**: ルートハンドラは記述の 101個 → 実数 **91個**（@app.route 約40 + @socketio.on 約51、`src/AutonaWebServer.py` 総行数 889行）。REST と WebSocket が混在し1ファイル集約のまま。Flask Blueprint 分割は未実施。
- **記述の陳腐化**: 対策案に挙がっていた「stage_strike」ドメインはステージストライク機能削除に伴い AutonaWebServer.py から消えている（分割候補から除外する）。ただし `StartGGDataProvider._parse_stage_strike()`（行956-1182）と `Autona.py` のキーマッピング（行1129-1131）にデータパーサ／マッピングの残骸があり、別途棚卸し候補。

**対策案**:
Flask Blueprint を使ってドメイン別に分割（scoreboard, commentary, bracket 等。stage_strike は除外）。

**影響範囲**: `AutonaWebServer.py`, `AutonaWebServerActions.py`

---

## S-14: Qt objectName 依存の動的 StateManager キー [P2/D3/S3] 🟡 ガード導入済み（フル移行は将来）

**根拠**: state-keys.md「要注意事項 #2」

**2026-06-24 検証**: 記述どおり現存。`element.objectName()` をキーパスに使用しているのは **6ファイル**:
`AutonaScoreboardWidget.py`, `AutonaTournamentInfoWidget.py`, `AutonaScoreboardPlayerWidget.py`, `AutonaTeamPlayerWidget.py`, `AutonaNotesWidget.py`（`AutonaBasePlayerWidget.py` は `objectName() != ""` の検査のみでキーパス用途ではない）。`.ui` の objectName 変更でキーパスが壊れ、既存 `program_state.json` との互換が失われるリスクが継続（`STATE_VERSION` は Qt ドックレイアウト用で state スキーマのマイグレーション層は無い）。

**2026-06-27 ガード導入（ランタイム不変・zero-risk）**: フル移行（明示キーのマッピング層＋データマイグレーション）は S3 で「低リスク」条件から外れるため見送り。代わりに**実行時の挙動を一切変えず**、契約を破る変更を検知する `tests/test_objectname_keypath_contract.py`（3テスト）を追加:
- `.ui` の入力ウィジェット objectName 集合をスナップショット固定（リネーム/削除を検知）
- プログラム的命名（`notes_widget_{N}` / `custom_textbox`）の存在をソースで検証
- QSpinBox 内部 `qt_spinbox_lineedit` → 親 objectName フォールバックの存在を検証

`.ui` の objectName を1つ改名すると test が fail することを確認済み（検知力検証）。

**保護対象 objectName ↔ StateManager キーパス（変更は移行層が前提）**:

| .ui | objectName | キーパス例 |
|-----|-----------|-----------|
| AutonaScoreboardTeam.ui | teamName, losers | `score.{N}.team.{T}.{name}` |
| AutonaBattleTeam.ui | teamName, separateSponsors | team battle ボードのチームキー |
| AutonaScoreboardScore.ui | phase, match, best_of, score_left, score_right | `score.{N}.{name}`（best_of 等）/ findChild キー |
| AutonaScoreboardPlayer.ui | team, name, seed, real_name, twitter, birthday, country, state | `score.{N}.team.{T}.player.{M}.{name}` |
| AutonaTeamPlayer.ui | team, name, country, state, twitter, activePlayer, dead, dynamicSpinner | `{self.path}.{name}` |
| AutonaTournamentInfo.ui | tournamentName, eventName, gameTitleCombo, numEntrants, startAt, address, streamTitleEdit | `tournamentInfo.{name}` |
| （プログラム的） | notes_widget_{N}, custom_textbox | `notes.{name}.{title\|contents}` / `{self.path}.custom_textbox` |

**運用ルール**: 上表の objectName を変更/削除する場合は、**先に StateManager のマイグレーション層を追加**してから `tests/test_objectname_keypath_contract.py` の `EXPECTED` を更新する（= S-14 フル移行を将来やる際の入口）。

**対策案（フル移行・将来）**:
objectName → 明示的なキー名マッピング＋`program_state.json` のスキーマ版管理＋ロード時マイグレーション。工数大・後方互換要注意。

**影響範囲**: 5〜6ファイル + .ui ファイル群 + 既存の保存データ

**リスク**: 既存ユーザーの `program_state.json` との後方互換性を壊す可能性。マイグレーション戦略が必要。

---

## フェーズ2 タスク配布の推奨順序（2026-06-24 更新）

### Wave 1（並列可能・即効性あり）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-08 | upset_factor 不整合 | D1。調査 + 1行修正。テストで確認可能 | ✅ M1a |
| S-09 | state 直接参照 | D1。StateManager に1メソッド追加 | ✅ M1a |
| S-11 | ui_constants import | 前提無効（連鎖回避不能・テスト分離に無関係）。140箇所 churn で利得ゼロ | ❌ 却下 |
| S-15(軽) | alerts_red.json untrack | D1。.gitignore 追加 + git rm --cached | ✅ 解消（2026-06-24） |

### Wave 2（テスト先行）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-05 | StartGGDataProvider パース | 「795行」は計測ミス＝神関数は既に分割済み。残課題（oldData除去/stage_strike分割/Get*PhaseGroup重複）対応済 | ✅ 実質解消（2026-06-27） |
| S-01 | PlayerWidget 重複統合 | クリーンな2メソッド（SwapWith/LoadCountries）を昇格。残2は意図的分岐で統合せず | ✅ 実質解消（2026-06-27） |
| S-15(本) | characters.json untrack | runtime DL 先を user_data/ へ。読み込みフォールバック | ✅ 解消（2026-06-24） |

### Wave 3（段階的）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-02 | ScoreboardWidget __init__ | 単一ファイル内。動作確認は手動。884行に肥大化 | 🔴 未着手 |
| S-04 | GameAssetManager 内部クラス | `_LoadGameAssetsCore`/`_ResolveTargetGame` 抽出。colors型/SaveBlock/finally の diverge 解消 | ✅ 解消（2026-06-27） |
| S-13 | AutonaWebServer Blueprint 分割 | 91ルート。stage_strike は除外 | 🔴 未着手 |
| ~~S-06~~ | ~~ThumbnailSettings 分割~~ | 縮小済みで優先度低下 | 🟢 縮小 |
| ~~S-07~~ | ~~thumbnail 巨大関数~~ | WebEngine 移行で解消 | ✅ 解消 |
| S-12 | commentary 欠番 | — | ✅ M1a |

### Wave 4（長期）
| ID | 対象 | 理由 | 状態 |
|----|------|------|------|
| S-03 | Window __init__ | 影響範囲広い。慎重に | 🔴 未着手 |
| S-10 | シングルトン遅延初期化 | 全ファイルに影響（11クラス）。フェーズ3以降 | 🔴 未着手 |
| S-14 | objectName 依存 | 契約テストでリネーム/削除を検知（zero-risk）。フル移行は将来 | 🟡 ガード導入済み（2026-06-27） |

---

## S-15: git 管理ファイルが実行時に書き換わる（テスト汚染・既定逆戻りの地雷）[P2/D2/S2]

**根拠**: 2026-06-24「大会デフォルト/タスクバーアイコン」調査の横展開。
アプリの全書き込み先（`open(...,'w')`/`json.dump`/`shutil.copy`/`.save`/`os.rename`）を
抽出し、git 管理状態・実行時に書くか読むだけかを突き合わせた結果。

**問題の型**: 「出荷時の既定値（tracked）」と「実行時に書き換わる状態ファイル」を兼ねた
ファイルがあると、テスト/利用で working tree が汚れ、うっかり `git add -A && git commit`
すると既定値が逆戻り（または API/テストデータが混入）する。

**該当ファイル（2026-06-24 再確認）**:

| ファイル | tracked | 実行時書き込み | 状態 |
|----------|---------|----------------|------|
| `layout/logo.png` | 除外済 | 大会読込で上書き | ✅ 解消（PR #71 系: gitignore + 起動時 `assets/icons/icon.png` から seed）。`git ls-files` に出ないことを再確認 |
| `assets/characters.json` | YES（出荷シード） | runtime DL は `user_data/characters.json`（gitignore）へ。seed は読み取り専用 | ✅ 解消（2026-06-24）。`DownloadStartGGCharacters` の DL 先を `user_data/characters.json` + `os.replace` に変更、読み込みは `_resolve_characters_path()` で user_data 優先・seed フォールバック。`user_data/characters.json{,.tmp}` を gitignore 追加。起動実機テストで seed 不汚染を確認 |
| `user_data/alerts_red.json` | 除外済 | 通知の「既読」(`MarkNotificationRed`、63-75行)で書き込み | ✅ 解消（2026-06-24）。`.gitignore` 追加 + `git rm --cached`。`AutonaAlertNotification.py` 末尾（78-80行）に「無ければ `[]` 自動生成」があり seed 不要。再生成ロジックを一時 dir で検証済み |

**安全と確認済み（読むだけ / dev ツールのみ書く / 既に gitignore 済）**:
`src/i18n/mapping.json`（書くのは `gen_translation_files.py` のみ）、`user_data/rulesets.json`、
`user_data/pronouns_list.txt`、`layout/settings.json`・`layout/*/settings.json`、
`user_data/{settings,local_players,layout_options}.json`（gitignore 済）、`out/*`・`*.tmp`（gitignore 済）。

**対策案**:
- `assets/characters.json`: 読み取り必須（`AutonaGameAssetManager` 906行付近）でオフラインの
  fresh clone で消えると起動失敗するため**単純な untrack は不可**。runtime DL 先を
  `user_data/characters.json`（gitignore）に変更し、読み込みは「user_data 優先・無ければ
  tracked の `assets/characters.json` にフォールバック」にする。tracked 版は出荷シードとして
  読み取り専用に固定。
- `user_data/alerts_red.json`: `.gitignore` 追加 + `git rm --cached`。無ければ自動生成される
  ため seed 不要。

**横展開の手順（再調査時）**:
```bash
# 書き込み先の抽出
grep -rEn "open\([^,]+,\s*['\"](w|wb|a)|json\.dump|shutil\.copy|\.save\(|os\.rename" src/ | grep -v __pycache__
# tracked かつ ignore されていないものを突き合わせ（git ls-files / git check-ignore）
# 多数起動した後の `git status` が最も確実な「実行時書き込み」の実証
```
