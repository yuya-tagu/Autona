# implement: 起動再設計 Step 1 — 単一インスタンスガード + 早期フィードバック

作成: 2026-07-28 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §1.2 Stage 0-1・§2 Step 1 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md)

## ゴール

1. exe を連打しても**プロセスは常に 1 個**。2 個目は既存ウィンドウを前面化して即終了（表示なし）
2. ダブルクリック後 **1〜2 秒以内にスプラッシュが表示**される（`import src` の前に描画）
3. 起動フェーズごとの経過時間が INFO ログに出る（以降のステップの計測基盤）

## 実装内容

### 1. 単一インスタンスガード（main.py・`import src` より前）

- 既存の `_update_in_flight()` チェック（main.py:42-77）の**後**に、named mutex ガードを追加:
  - ctypes `kernel32.CreateMutexW(None, False, "taguyan.Autona.SingleInstance")`
  - `GetLastError() == 183`（ERROR_ALREADY_EXISTS）なら 2 個目と判定
  - mutex ハンドルはプロセス生存中保持（モジュールレベル変数）。明示 Close 不要（プロセス終了で OS が解放）
- 2 個目の挙動: `QLocalSocket` で固定名 `taguyan.Autona.ipc` へ接続し `b"activate"` を送信 → `sys.exit(0)`
  - 接続リトライ: 500ms × 3 回。失敗しても**何も表示せず** exit 0（既起動は mutex で確定済み。`_update_in_flight` と同じ「黙って退く」方針）
  - `waitForConnected` / `waitForBytesWritten` を使いイベントループ不要で完結させる
- 1 個目の挙動: QApplication 生成後（下記 2）に `QLocalServer` を listen:
  - `QLocalServer.removeServer("taguyan.Autona.ipc")` してから `listen()`（前回クラッシュの stale socket 対策）
  - 接続受信で "activate" を読んだら前面化。**ウィンドウ未生成の間に受信した場合はフラグを立て、Window 生成後に消化**する
  - 前面化 = `showNormal()`（最小化解除）+ `raise_()` + `activateWindow()`

### 2. QApplication + スプラッシュの前倒し（main.py・`import src` より前）

- main.py で `QT_API` 設定・AppID 設定の後に:
  1. `from PySide6.QtCore import Qt` / `QtWidgets` / `QtNetwork` のみ import（**src はまだ import しない**）
  2. `QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)` を **QApplication 生成前**に実行（後段の QWebEngineView 前提。PySide6 では `QtWebEngineQuick.initialize` 不要だがこの属性は必須側に倒す）
  3. `app = QApplication(sys.argv)` 生成 → スプラッシュ表示 → `app.processEvents()` で 1 フレーム描画
  4. その後に `import src`
- スプラッシュの src 非依存化:
  - `src/AutonaSplash.py` を**プロジェクト直下の `autona_splash.py` に移動**する（`from src...` の import を残さないこと。`import src.AutonaSplash` は `src/__init__.py` → 全起動を引くため不可）
  - 移動後、旧 `src/AutonaSplash.py` は削除し、参照元（`src/Autona.py` の `create_splash` 等）を新モジュールに追従させる
  - main.py で生成したスプラッシュインスタンスは `autona_splash` モジュールの変数（例: `autona_splash.ACTIVE_SPLASH`）に保持し、`src/Autona.py` 側は生成せずそれを使って進捗更新・`finish()` する
- `src/Autona.py:41` の `App = QApplication(sys.argv)` は `App = QApplication.instance()` に変更（main.py が生成済みであることが前提。None フォールバックは書かない — 前提が壊れたら早期に落とすべき）
- 注意: `main.py` はランチャ以外に開発時 `python main.py` でも同一経路。dev/frozen で分岐しない共通実装にする

### 3. 起動フェーズ計測ログ

- `time.perf_counter()` の t0 を main.py 冒頭で取り、`autona_splash.py` にモジュール変数として保持
- 以下の時点で `STARTUP phase=<name> elapsed=<x.x>s` を INFO 出力:
  - `gate`（mutex 取得直後）/ `splash`（スプラッシュ描画直後）/ `import-src`（import src 完了）/ `window-init`（Window.__init__ 開始）/ `core-shown`（`self.show()` 直後）
- loguru の sink は `import src` 中に構成されるため、`gate` / `splash` は標準 `print` でよい（または時刻だけ記録して import-src 後にまとめて loguru へ出す。実装しやすい方）

### 4. ポート衝突ダイアログのデフォルト変更

- `src/AutonaWebServer.py` の `check_webserver_port()` 内 `box.setDefaultButton(QMessageBox.StandardButton.Yes)` を **No** に変更（誤 Enter で既存プロセスを kill しない）

## 制約・地雷（必読）

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) 全項目。特に #1（src import は全起動）— main.py の早期段階で src 由来のものを一切 import しないこと
- PySide6 のみ使用（PyQt6 禁止・ライセンス要件）。main.py 冒頭の `QT_API=pyside6` より後であれば qtpy 経由でも可だが、main.py の早期部は素の `PySide6.*` 直 import でよい（qtpy import 自体のコスト回避）
- アップデータ整合: `_update_in_flight()` チェックは mutex ガードより**前**のまま動かさない
- `dependencies/autona.spec` : `autona_splash.py` は main.py から直 import されるため Analysis が自動検出するはずだが、datas/hiddenimports に影響がないか spec を確認する
- **アプリを起動しての動作確認は行わない**（ユーザーが実施）。実装後は `python -m py_compile` 相当の構文チェックと import 静的確認まで

## 検証（ユーザー実施・設計書 §2 Step 1 のチェックリストを使用)

実装完了時に、設計書のチェックリストをそのまま提示すること。

## 完了条件

- [ ] 上記 1〜4 の実装
- [ ] 全変更ファイルの構文チェック通過
- [ ] 変更ファイル一覧・設計書チェックリスト・既知の残リスクを添えて報告
