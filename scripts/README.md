# scripts/

| ファイル | 用途 |
|----------|------|
| `update_translations.ps1` | PySide6 同梱の lupdate で `main.py` と `src/` の翻訳呼び出しから `src/i18n/Autona_*.ts` を更新する（新規文言は未翻訳で追加、コードから消えた文言は削除。オプションなし。続けて `compile_translations.ps1`） |
| `compile_translations.ps1` | `src/i18n/*.ts`（手動編集）を `.qm` にコンパイルする（翻訳の正本手順） |
| `gen_city_to_state.py` | 都市名 → 州コードの引き当て表 `city_to_state.json` を生成する（生成物は git 管理必須） |
| `run_tests_parallel.py` | pytest 全量をファイル単位で複数プロセスに分けて並列実行し（分割は前回の実測時間。キャッシュは一時ディレクトリの `autona_pytest_file_times.json`、全量実行でのみ更新。無ければファイルサイズ）、結果を統合する（`--timeout` 秒（既定 1200）で全体を打ち切り、ワーカーを止めて失敗にする。最終行は `RESULT: OK` / `RESULT: FAILED`。標準ライブラリのみ。ローカル全量検証と CI の `Python tests` が使う。main への push では CI が直列も走らせる） |
| `gen_layout_kit.py` | overlay / thumbnail / result の契約（`layout/include/*_contract.json`）とキットのサンプルから `layout/include/kit/` に `CONTRACT.md`（kit 直下と `thumbnail/`・`result/`）・`<kind>/AI_PROMPT.md`・`sample_payload*.json`（`thumbnail/` と `result/`）を生成する（生成物はコミット。`tests/test_layout_kit.py` が差分を検出） |
| `strip_cities.py` | 国・州 JSON から city 配列を除いた軽量版を生成する（起動時の JSON パース短縮） |
| `strip_iccp.py` | PNG の壊れた iCCP チャンクを除去し、libpng の CRC 警告を消す |
| `gen_release.ps1` | リリース zip（`release-windows.zip`）を組み立てる |
| `validate_release_zip.ps1` | リリース zip の中身（区切り文字・開発データ混入・保存済み state・Qt の同梱物）を検証する |
| `publish_release.ps1` | リリースを公開リポジトリへ公開する（手順は `.claude/release-runbook.md`） |
