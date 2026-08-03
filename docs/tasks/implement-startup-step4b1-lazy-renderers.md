# implement: 起動再設計 Step 4b-1 — Thumbnail / Result レンダラの遅延生成

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 4-2 ／ 先行: Step 4a（PR #156）／ 調査: 2026-07-30 実施（パネル台帳・外部参照マップ）

## ゴール

起動時に生成されている ThumbnailRenderer / ResultRenderer（QWebEngineView ×2 +
初回 page load + 初回レンダ、実測 〜3s）を**初回に必要になった時点**の生成へ遅らせる。
**サイドバー構造・_navSections・STATE_VERSION には一切触らない**（それは 4b-2）。

## 実装内容

### 1. AutonaThumbnailSettingsWidget（現: `__init__` :309-314 で生成 + GeneratePreview）

- `ThumbnailRenderer.instance()` の取得と初回 `GeneratePreview()` を初回 `showEvent` へ移動
  （`_previewLoaded` 型の一度きりガード。既存パターン踏襲）
- `__init__` で接続しているシグナルハンドラ（state_big_change の自動再生成・
  media_updated の動画タイトル更新等）は、**レンダラ未生成の間は生成せずスキップ**する
  （バックグラウンドイベントで裏からレンダラが立ち上がると遅延の意味が消える）。
  パネル表示後は従来挙動
- CLI / API 経路（`main_generate_thumbnail.py` の `.instance()`）は明示的な生成要求
  なので従来通り（触らない）。パネル未表示でも API からサムネ生成が動くこと

### 2. AutonaResultSettingsWidget（現: `__init__` :454-467 で生成 + _onLayoutChanged）

- 同様に初回 `showEvent` へ
- **起動時二重 page load の解消**: 現在 `ResultRenderer.__init__` が既定 'horizontal' を
  ロード → 直後に `Window.__init__`:746 の `SetBattleFormat` が 'team' 等を再ロードしている。
  レンダラ生成時に**その時点の battle_format から初回レイアウトを決定**する形にし、
  二重ロードを構造的に無くす（パネル側が format を保持し、生成時に渡す等。
  `Window.SetBattleFormat` の null ガード付き経路 :1158-1161 は既存のまま活かす）

### 3. 検証項目の設計書追記は不要（本チケットに記載で足りる）

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 両パネルの `__init__` 内シグナル接続のハンドラを列挙し、それぞれ
      「レンダラ未生成時にスキップして安全か（表示時に自己回復するか）」を確認。
      自己回復しないもの（データ欠落が残るもの）があれば対応案を添えて報告
- [ ] `ThumbnailRenderer` / `ResultRenderer` の `.instance()` を呼ぶ**全**箇所を再列挙し、
      起動経路がパネル 2 箇所のみであることを再確認（API/CLI 経路は明示要求として維持）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#9 #10 #12 #13。
  特に WebEngine まわりのクラッシュ実績）
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、
  `web/src/ScoreboardPage/Player.jsx`
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし

## 検証（ユーザー実施分・報告時に提示）

- [ ] 起動 → Thumbnail / Result パネルを開かずに通常操作（ログに ThumbnailRenderer /
      ResultRenderer の load 行が**出ない**こと）
- [ ] Thumbnail パネル初回表示 → プレビュー生成・テンプレート切替が従来通り
- [ ] Result パネル初回表示 → 現在の battle_format のレイアウトが**一発で**ロードされる
      （二重ロードのログが無い）
- [ ] パネルを開かずにサムネ生成（スコアボードの生成ボタン / API）→ 正常生成
- [ ] STARTUP ログ: window-init → core-shown の短縮を確認

## スコープ外（4b-2 以降）

- サイドバーのファクトリ構造・プレビュー 4 パネル（Board / Top8 / Commentary / StatsPreview）の lazy 化
- StateManager シードの bootstrap 移設（Board theme/accent・top8/commentary 正規化）
