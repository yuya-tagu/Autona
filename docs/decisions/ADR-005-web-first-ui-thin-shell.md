# ADR-005: Web-first UI と薄い Qt シェル（Headless Core の完成と UI 技術の一本化）

## Status

Accepted（2026-10-23 PdM 承認。「現代的にアーキテクチャを抜本的に修正したい」方針を受け、
現状棚卸し → Fable（外部レビュー）→ 再定義を経て案 2 を採用）

> 棚卸し・レビュー・終着点・移行ステップの詳細は
> [../changes/architecture-modernization.md](../changes/architecture-modernization.md) を正とする。
> 本 ADR は「なぜこの方針か」の記録のみを持つ。

## Context

ADR-004（2026-09-03 完了）で「Qt ウィジェットは `StateManager` を直接書かない」は達成したが、
2026-10-23 のコード実測で、core が状態とドメインの**唯一の**正本になりきれていない構造が残っていた:

- ゲームアセット（キャラ・スキン・ステージ）のカタログが `QStandardItemModel` の中にあり
  （`AutonaGameAssetManager`）、Web からの参照も GUI スレッドで同期待ちする
- 選手スロットのスワップ／クリアの正本がウィジェット（`AutonaBasePlayerWidget.SwapWith` が
  ウィジェット間で値をコピー、`teamsSwapped` はウィジェット属性）。Web・ホットキー・
  ヘッドレスから操作が完結しない
- 永続化の正本が OBS 向け出力 `out/program_state.json` を兼ねており、スキーマの型付け・
  マイグレーションを入れられない
- 外部入口が legacy REST 45 + Socket.IO 41 + `/api/v1` 31 の 3 系統。`@gui_thread_sync` 22 箇所が
  Flask スレッドから GUI を同期ブロックする
- UI 技術が Qt Widgets（約 26k 行）/ React（13.5k 行）/ jQuery オーバーレイ（14.5k 行）の 3 種。
  スコア入力は Qt と React で二重実装。PySide6 ネイティブクラッシュ地雷（agent-pitfalls #9〜#15）は
  全て「データが Qt オブジェクトの寿命に乗っている」ことが原因

一方、**Designer は既に「React を QWebEngineView で包む」構成で稼働しており**、
React スコアボードも `/api/v1` + Socket.IO で動いている。Web-first UI は仮説ではなく実証済みの構成である。

## Decision

### D1: 終着点は「Python Headless Core + 単一 Web UI（React）+ 薄い Qt シェル」

- core（Qt import ゼロ）が状態・ドメイン・カタログの唯一の正本。`--headless` で単独起動できる
- UI はすべて React（`/web`）。デスクトップ内 UI もリモート UI も同じ `/api/v1` + Socket.IO を使い、
  特権経路を作らない
- Qt は「WebEngineView 1 枚 + 単一インスタンス + トレイ + Win32 ホットキー + ファイルダイアログ +
  Updater 起動」の薄いシェルに縮退する

### D2: 新規 Qt Widgets パネルの追加を凍結する

本 ADR 承認時点から、新機能の UI は React 側で作る。既存 Qt パネルは移行完了まで保守のみ。

### D3: 段階移行（ストラングラー）。S0〜S3 は基盤、S4 以降でパネル単位に置換

S0 掃除 → S1 アセットカタログの core 化 → S2 選手スロットの正本を state へ →
S3 ヘッドレス起動と永続化分離（`user_data/state.json` を正本、`out/` は派生）→
S4〜 パネル単位の React 化 → S-final シェル化。ADR-003 / ADR-004 と同じ規律
（1 PR = 独立検証・revert 可能・独立リリース可能）を踏襲する。

### D4: 外部契約と配布構造は変えない

`out/` の内容と書き込み順、OBS URL、Socket.IO の payload と revision、`/api/v1`（追加のみ）、
保存データの形式、アセットパック、onedir と zip 構造、更新ヘルパーは変えない。
legacy REST / Socket.IO は告知どおり v2.0 で削除する。

### D5: 付随して決めること

- LAN 公開時のトークン認証は **S3 と同時に導入する**（ヘッドレス起動で LAN 越しの操作が
  現実的になり、UI = API になるため）。実装設計は S3 着手時に提示する
- シェルの WebView 技術（QtWebEngine を続けるか WebView2 にするか）は S-final で判断する
  - **決定（2026-11-14 S-final。2026-11-16 追記）: WebView2 化しない。QtWebEngine を継続する**。別 ADR は書かない。理由:
    - オフスクリーン描画（サムネイル・リザルト）が QtWebEngine に残る限り、WebEngine 本体（約 340MB）は減らない。WebView2 に実用的なオフスクリーン描画が無い
    - zip と `_internal` の構造が変わり、自動更新ヘルパーへの制約（D4・台帳 §4.4 末尾の「外部契約の守り方」）に反する
    - PySide6 はホットキー・ダイアログ・Updater のためにどのみち残る
    - サイズの削減は WebView2 化ではなく、使っていない同梱物を外す（SF-9。下の「S-final の完了」）で行った
  - トレイ（D1 の記述）は実装されたことが無く、需要の記録も無いので作らず、S-final の完了条件から外した

### S-final の完了（2026-11-16）

- **Qt Widgets のパネルは 0**。Window は共有ビュー（`AutonaWebPanelHost`）1 枚だけを中央に持ち、ナビ・ヘッダー・フッター・全ページは React の AppShell（`/web/app/<panel>`）。`QStackedWidget`・サイドバー・メニュー・ステータスバー・ガイド類や入力用の `QDialog` は無い
- **残る Qt**: Window、共有ビューのホスト、Designer の窓（`AutonaDesignerWindow`）、Updater のダイアログ、ネイティブのダイアログ（LAN の初回確認・ポート競合・起動失敗・フォルダ選択）、Win32 ホットキー・タイトルバー色。QtCore のアダプタとヘッドレスは core 側。許可リストは AST の pin（`tests/test_web_panel_host.py`）
- **core への統合**: ゲームのローダーは両シェル共通の core `GameLoader`（`run_sync` をやめ、ローダーのスレッドで最後まで走る）。フッターの進捗は core の `StatusService`。アプリ UI の文言は i18next が唯一の正本で、Qt の `.ts` はネイティブのダイアログと Updater の分だけ
- **配布物の削減（SF-9）**: 未使用の Qt モジュール（QML 一式・debug の pak・Qt の翻訳・en / ja 以外の WebEngine locale・Git 由来の OpenSSL の DLL）を外し、`_internal` 571 → 344.4 MiB、zip 282.7 → 216.9 MiB（実測）。D4 の構造（onedir・zip・更新ヘルパー）は変えていない。リリース時は Runbook 手順 9（旧版からの自動更新 E2E）で確かめる
- 段ごとの内容・PR・残るリスクは [../changes/architecture-modernization.md](../changes/architecture-modernization.md) の S-final の節

## Alternatives considered

- **(1) 現スタックのまま層構造だけ完成させる**: S0〜S3 は本案と共通。ただし UI 3 種・
  Qt Widgets 26k 行・スコア入力の二重実装・Qt 地雷群が残り、ADR-004 が「最難関・最後」と
  置いた UI 層の問題が再び先送りになる。→ 不採用（S0〜S3 は共通基盤として実施）
- **(3) Tauri / Electron へ移行する**: 配布サイズは減るが、Python backend（Start.gg 連携・
  更新ヘルパー・描画）を書き直すか、2 ランタイムを抱えることになる。exe と zip の構造が変わり、
  自動更新の鶏と卵問題が再来する。ADR-004 Alternative (B) の却下理由がそのまま当たる。→ 不採用
  （(2) 完了後にシェルの差し替えとしてなら再検討できる）
- **core の別プロセス常駐**: IPC・ライフサイクル・認証の複雑性に見合う要件がまだない。
  `--headless` 起動で同じオプション価値を得る。→ 不採用（ADR-004 (C) の判断を維持）

**ADR-004 Alternative (B) との関係**: ADR-004 は「スタック変更（Web アプリ化・Tauri 等）」を
一括で却下したが、その理由（実地知識の再学習・配布互換・機能停止）は「スタック変更」に対する
ものだった。Python core と配布機構を残して UI だけを Web に寄せる本案は、その却下理由に当たらない。
したがって ADR-004 を supersede せず、その「UI 層」を延長する決定として位置付ける。

## Consequences

**良い影響:**

- UI 技術が 1 本（React）になり、スコア入力の二重実装が消える
- データが Qt オブジェクトの寿命から外れ、PySide6 ネイティブクラッシュ地雷が構造ごと消える
- core 単体テスト・API 契約テスト・`--headless` を使った Playwright E2E という、Qt 実アプリに
  依存しないテスト体系になる
- ROADMAP Tier 2（Web ダッシュボード・複数オペレーター）が移行の副産物として手に入る
- React/TS は AI エージェントが得意な領域で、1 人保守のコストが下がる

**受け入れるリスク・コスト:**

- S4 以降は 11 パネル × 各 1〜3 週の投資
- WebEngine 上での日本語 IME・Tab フォーカス・貼り付けの挙動差。**S4 の最初のパネル
  （Commentary）で実機確認し、オペレーターの入力速度を落とさないことを続行条件にする**
- WebEngine は当面残るため、配布サイズ（`_internal` 約 580MB）は S-final まで減らない
  （S-final で WebEngine は継続と決定。サイズは SF-9 で未使用の同梱物を外して 344.4 MiB になった）
- 移行期間中に Qt 側への機能追加要求が来ると二重実装が再発する（D2 で防ぐ）
- S2 はエコーループが起きやすい（ADR-004 と同じ「1 操作で delta 1 回」の検証を必須にする）

## Revisit conditions

- S4 の IME 実機ゲートで入力体験の劣化が解消できない場合、UI の着地点を (1) に戻す
  （S0〜S3 の成果はそのまま有効）
- 複数マシン・複数オペレーターの同時運用が実要件になった場合、core の別プロセス化を再検討する
- 配布サイズが SF-9 の後でも運用上の問題になる場合、WebView2 化を別 ADR で判断する（S-final では継続を決定。D5）
