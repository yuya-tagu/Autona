# scripts/

| ファイル | 用途 |
|----------|------|
| `compile_translations.ps1` | `src/i18n/*.ts`（手動編集）を `.qm` にコンパイルする（翻訳の正本手順） |
| `gen_city_to_state.py` | 都市名 → 州コードの引き当て表 `city_to_state.json` を生成する（生成物は git 管理必須） |
| `strip_cities.py` | 国・州 JSON から city 配列を除いた軽量版を生成する（起動時の JSON パース短縮） |
| `strip_iccp.py` | PNG の壊れた iCCP チャンクを除去し、libpng の CRC 警告を消す |
| `gen_release.ps1` | リリース zip（`release-windows.zip`）を組み立てる |
| `validate_release_zip.ps1` | リリース zip の中身（区切り文字・開発データ混入）を検証する |
| `publish_release.ps1` | リリースを公開リポジトリへ公開する（手順は `.claude/release-runbook.md`） |
