# タスク: Python コードベース読解(Step 1)

## 前提
まず以下を読め:
1. `CLAUDE.md`(プロジェクト全体ルール)
2. `.claude/plans/refactor-phase1.md`(本フェーズの目的と原則)

## やること
`src/` 配下の Python ファイルについて、以下2つのドキュメントを作成する。

### 成果物1: `.claude/architecture/python-imports.md`

src/ 配下の全 .py ファイルの import 関係を機械的に抽出し、Markdown で表現する。

**手順**:
1. `find src -name "*.py" -type f` で対象ファイル一覧を取得
2. 各ファイルについて `grep -n "^import\|^from" <file>` を実行し、import 文を収集
3. 標準ライブラリ(os, sys, json, threading 等)と PyQt6 系は除外。**プロジェクト内 import と外部主要ライブラリ(deepdiff, flask, socketio, pynput 等)のみ**を記録
4. 結果を以下フォーマットで出力:

```markdown
# Python Import Map

## ファイル別 import 一覧

### src/StateManager.py
- 内部: (なし or src/Helpers/XXX)
- 外部: deepdiff, threading

### src/AutonaScoreboardWidget.py
- 内部: src/StateManager, src/AutonaGameAssetManager, ...
- 外部: PyQt6.QtWidgets

(以下全ファイル分)

## 循環 import 候補
(A が B を import し、B が A を import している組を列挙。なければ「検出されず」と書く)

## 孤立ファイル
(どこからも import されていないファイル。dead code 候補)
```

### 成果物2: `.claude/architecture/singleton-init-order.md`

CLAUDE.md に記載のシングルトン群の初期化順を `main.py` から追跡する。

対象シングルトン:
- AutonaScoreboardManager
- AutonaGameAssetManager
- AutonaTournamentDataProvider
- AutonaHotkeys
- AutonaAlertNotification
- (他に `.instance` パターンのものがあれば追加)

**手順**:
1. `main.py` を view ツールで全文読む
2. `grep -rn "\.instance" src/` で各シングルトンの初期化箇所(`= ClassName()` または `instance = cls()` パターン)を特定
3. main.py における呼び出し順序、または import 時の副作用初期化を記録
4. 各シングルトンが他のシングルトンに依存しているか(コンストラクタ内で他の `.instance` を参照しているか)を調査
5. 結果を以下フォーマットで:

```markdown
# シングルトン初期化順

## main.py における初期化シーケンス
1. XXX (line N) — 依存: なし
2. YYY (line N) — 依存: XXX
...

## シングルトン間依存グラフ
- AutonaScoreboardManager → AutonaGameAssetManager (理由: __init__ で .instance 参照)
- ...

## 暗黙の初期化順序リスク
(import 順を変えると壊れそうな箇所、明示的初期化なしに使われている箇所を「要確認」マーク付きで列挙)
```

## 制約
- **コードは一切変更するな**。調査とドキュメント作成のみ
- ファイル数が多い場合、まず全ファイル数を報告してから着手すること
- 1ファイルでも読めない/解釈できないものがあれば「要確認」として残し、勝手に推測で埋めない
- 完了したら成果物2ファイルのパスを報告して停止せよ。commit はしない(Tagu がレビューしてから別途指示する)

## 報告フォーマット(完了時)
- 対象ファイル数: N
- 検出された循環 import: N 件
- 検出された孤立ファイル: N 件
- シングルトン総数: N
- 暗黙初期化リスク: N 件
- 「要確認」マーク総数: N
