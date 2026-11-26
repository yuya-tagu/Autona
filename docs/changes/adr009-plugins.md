# ADR-009 プラグイン化の実装（短命チェックリスト・完了したら削除）

方針: 契約と型を先に固定 → 部品を worktree で並列（未配線の新規モジュール）→ 配線はメインが直列 → 実機確認は節目ごと。

委任（2026-11-26 オーナー）: 準備〜M6 は「高」も CI 緑＋review＋節目の実機確認で自己マージし次へ。M2 直後に webhook リポジトリ（公開）と空の plugins.json を作ってよい。exe ビルドは M1・M3 で各 1 回。Webhook の実送信はオーナーが確認。リリースと告知はオーナー。

## 運用の規則
- push・PR・マージ予約はメインだけが直列で行う。サブエージェントは worktree 内のコミットまで。認証は `GH_TOKEN="$(gh auth token --user yuya-tagu)"`＋`credential.helper=!gh auth git-credential`（switch しない）
- worktree はメインが fetch 後に origin/main から切る。1 レーン = 1 PR・base main。W1 の間、ローカル main は origin/main と一致（コミットしない）
- **共有ファイルは配線 PR だけが触る**: `bootstrap.py`・`WebAccess.py`・`ApiCatalog.py`・`AutonaApiV1.py` の登録・`registry.tsx`・`navSections.json`・`test_bootstrap_split`・`test_import_side_effects`・契約（contract・PluginTypes・golden）
- `THREADS_LEFT`（`test_task_executor`）は L1 と L9 だけが触り、マージは直列。部品レーンは Archive* を触らない（載せ替えと旧コード削除は I2 だけ）
- web は専用 worktree（`npm ci`）で常に 1 PR だけ。`i18n/locales/*.json` は web レーンだけが触る
- 同時に開く PR は core 4＋web 1。マージは小さい順。各レーンのテストは自モジュール＋pin 3 本（qt_free・task_executor・import_side_effects）
- 部品レーンが契約（contract・PluginTypes・golden）の不足に気づいたら、自レーンで直さずメインに報告する。契約の変更はメインが小さな PR で入れ、各レーンを rebase する
- 区分: 未配線の部品は「通常」、配線（I1a・I2・I4）と build_release / validate の変更は「高」

## 準備
- [x] main の CI 赤（#382 のマージ）を直して基準線を緑に。残っている worktree 3 つを掃除（junction を先に外す）

## W0 契約（メイン）
- [x] PR0: ADR-009・README・ROADMAP・本書＋`docs/plugins/contract_1.json`・`plugin.schema.json`・入力源の golden（state は部分一致）・`core/PluginTypes.py`（dataclass と Protocol だけ）・v1 ルート表・偽ワーカー（全モード）

## W1 部品（並列。上から順に投入）
- [x] L5 `MatchLogService` の match_decided 判定（L3 の golden より先にマージ）
- [x] L3 `PluginSecrets`（DPAPI）＋`PluginInputs`（入力源 6 つ）
- [x] L2 `PluginRegistry`（マニフェスト検査・B.4・開発中フォルダ）
- [x] L1 `PluginRunner`（新規。CREATE_SUSPENDED・優先度上限・timed_out・環境変数の引き算）
- [x] L8 `PluginJobs`（request.json・ジョブフォルダ・成果物の検査・保持・busy・起動時の後始末）
- [x] L9 `PluginHooks`（保留・2 つの窓・自動無効。時計を注入）
- [x] L4 `PluginInstaller` の部品（共有の zip 検査を新規に・配布元の判定表・同意ダイジェスト・遮断の解釈）
- [x] L11 v1 ハンドラ（新規モジュール。登録は配線で）
- [x] L12 SDK `autona_plugin.py` を本体内に（偽ホストを本体の pytest で実エンジンと照合）
- [x] L6 web: plugins ページ・フォーム部品・進捗部品の共通化（モック。#389 マージ済み）→ 続けて L10 シェル要求 `pickFile` / `openPluginLink`

## W2 以降 配線（メイン・直列）
- [x] I1a（#399）→ I1b（#400）＋I2（#401）→ **M1** 実機・exe 1 回・verify 全量（済。exe からのワーカー起動での Defender の反応と vodsplit の中止は未確認 → M3 の exe とオーナーの実 VOD で）
- [x] I3（#402）アクション「プラグイン ▾」・secret・webhook（ローカルフォルダ）→ **M2**（P3）（済。ループバックで画像送信まで確認。Discord への実送信はオーナー）
- [x] M2 直後: Autona-releases に空の `plugins.json`（Autona-releases#2）、`yuya-tagu/autona-webhook` を作成（非公開。I6 で公開・掲載）
- [ ] I4 導入・同意・遮断（実装済み・PR 中。ArchiveWorkerInstaller の ZipSafety 化は見送り）→ **M3**（P5a）実機・exe 1 回 → I5 フック → **M4**（P7）→ I6 掲載 → **M5**（P5b・告知可）→ I7 更新 → **M6**（P6）
- [ ] 各節目で verify 全量 1 回

## 止まる条件
機能削除・ADR（D4 の区分・D6）を超える変更・2 レーンが同じファイルを要る・main の push CI が赤・単独で再実行しても再現する原因不明の失敗・許可外の外向き操作（リリース・告知）
