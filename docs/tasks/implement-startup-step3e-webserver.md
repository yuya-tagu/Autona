# implement: 起動再設計 Step 3e — WebServer の Flask/SocketIO 構築をインスタンス化時へ移行

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3 ／ 先行: Step 3a〜3d（PR #147/#149/#151/#152）

## ゴール

移行リスト最後の 9 番目。[src/AutonaWebServer.py](../../src/AutonaWebServer.py) の
**クラス本体で実行される Flask + CORS + SocketIO 構築**（`WebServer` class body 冒頭）と、
それに依存する **88 個の decorator 登録**（`@app.route` / `@socketio.on` /
`@app.before_request` / `@app.after_request`）の import 時実行を廃止し、
**初回インスタンス化時**の構築に移す。

- WebServer の**インスタンス生成は従来通り Window 側**（[src/Autona.py](../../src/Autona.py):434）。
  bootstrap.py には追加しない（生成に commentaryWidget 等の Window 文脈が必要なため）
- 挙動不変: ルート・イベント・ハンドラ本体・CORS/JSON 設定は 1 つも変えない

## 実装方式（この設計に従う）

### 1. モジュールレベルのファクトリ関数 `_build_web_app()` へ移動

- `app = Flask(...)` / `CORS(app)` / `socketio = SocketIO(...)` / `app.config[...]` と、
  **decorator 登録された 88 ハンドラの定義を、decorator 構文ごと**ファクトリ関数内へ移す
  （closure 方式。`@staticmethod` は外す。ハンドラ本体は変更しない — diff がインデント変更
  中心になることが正しさのレビュー材料になる）
- ファクトリは `(app, socketio)` を返す
- クラス側は `app = None` / `socketio = None` とし、`__init__` 冒頭で
  `if WebServer.app is None: WebServer.app, WebServer.socketio = _build_web_app()` を実行
  （既存の instance ガードと同じ冪等パターン。以降のクラス経由参照
  `WebServer.app` / `WebServer.socketio` は従来通り動く）
- `__init__` 内の既存処理（blueprint 登録・signals 接続等）は app 構築**後**に実行されるよう順序を保つ

### 2. クラスに残すもの

- route 登録されていない staticmethod / classmethod（`ws_emit` / `on_program_state_update` /
  `ws_program_state` 等、クラス名経由で参照されるもの）はクラスに残す
- **route 登録されており、かつクラス名経由でも参照されるハンドラ**が存在する場合は、
  クラスに残して closure から `WebServer.<name>` を登録する等の対応を取り、報告に明記する

### 3. 回帰検証: ルート台帳スナップショット（本チケットの要）

88 本の移し替えの正しさは目視では保証できないため、**リファクタ前に台帳を採取**し、
テストで固定する:

1. 実装前に、現行コードで `WebServer.app.url_map`（rule / methods / endpoint）と
   SocketIO の登録イベント名一覧をダンプして保存
2. `tests/test_webserver_routes.py`（新規）: fake-package 方式で import → ファクトリ実行 →
   台帳（期待値としてテスト内にハードコード）と完全一致を assert
   - Flask/SocketIO の import が重いため、subprocess 方式 or pytest マーカーで分離してよい
3. リファクタ後にこのテストが green であること = 88 本の登録漏れ・重複ゼロの証明

### 4. `tests/test_import_side_effects.py` の対象拡張

- 検査対象に `src.AutonaWebServer` を追加
- assert: `WebServer.app is None` / `WebServer.socketio is None` / instance 系 assert 維持
- import 連鎖に `AutonaWebServerActions` / `AutonaDesignerService` / `AutonaApiV1` が入るため、
  これらのモジュールレベル副作用も事前に AST 確認（下記）

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] `WebServer` class body で app/socketio 以外に import 時実行されるもの
      （`log.setLevel` はモジュールレベルで無害・現状維持でよい）
- [ ] `AutonaWebServerActions` / `AutonaApiV1` / `AutonaDesignerService` のモジュールレベルに
      import 時副作用・シングルトン instance 参照が無いか（AST 走査）
- [ ] route 登録済みハンドラのうち、モジュール内外から**名前で参照**されるものの一覧
      （`WebServer.xxx` / `self.xxx` 経由。closure 化すると名前参照が壊れるため要個別対応）
- [ ] `check_webserver_port()` 等、インスタンス化前に呼ばれる関数が `WebServer.app` /
      `socketio` に触れていないか

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（特に #1 / #12）と
  [.claude/rules/state-and-webserver.md](../../.claude/rules/state-and-webserver.md)
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、
  `web/src/ScoreboardPage/Player.jsx`、`.claude/git-workflow.md`
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止
- 外部 API 互換: `/api/v1/`（ADR-001）と全既存ルートの URL・メソッドは 1 つも変えない

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/test_webserver_routes.py -v` 通過（台帳完全一致）
- [ ] `pytest tests/test_import_side_effects.py -v` 通過
- [ ] `pytest tests/ -q` 回帰なし

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → Web UI（`/web`・`/scoreboard`）表示・スコア反映
- [ ] OBS ブラウザソース相当の取得（`/program-state` 等）
- [ ] Web UI からのスコア操作（POST → デスクトップ反映 → WebSocket 配信）
- [ ] ポート変更・衝突ダイアログの動作（check_webserver_port 経路）

## スコープ外（後続 PR）

- SettingsManager / LocaleHelper / StartGGDataProvider / BadWordFilter の残副作用
- `src/__init__.py` の連鎖 import 廃止（Step 3 最終 PR）
- WebServer 起動の表示後非同期化（Step 4）
