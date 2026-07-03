# 実況・解説（Commentary）機能リワークの影響分析（2026-07）

## 変更概要

実況・解説パネルを TOP8 パネルと同じ「左: 一覧編集 / 右: ライブプレビュー」の
2 ペイン構成に全面リワーク。スキーマ・オーバーレイ・周辺 API を刷新した。

- スロット固定 8 名（欠番・動的増減の廃止）、役割プレフィックス・コメント・
  「配信に表示」トグル（最大 4 名。チェックした順に採番され、オーバーレイの
  左から表示。`display` は int の表示順で 0=非表示）を新設
- データ層は Qt 非依存の `src/AutonaCommentaryModel.py` に分離（テスト付き）
- プレビューは `src/AutonaLayoutPreview.py`（旧 Top8 `_BracketPreview` の汎用化）
- オーバーレイは `layout/commentators/index.html` に一本化

## 破壊的変更

| 変更 | 影響 | 移行 |
|------|------|------|
| `commentary.{N}` スキーマ刷新（name/team/real_name/twitter/role/note/display のみ） | `GET /commentary`・`get_comms` の返却形が変わる。旧フィールド（seed/country/character/mains/custom_textbox 等）は消える | 旧 state は起動時 `NormalizeState` で自動移行（custom_textbox → note、他は破棄） |
| 実況者数の自動拡張廃止 | `/update-commentary-<N>`・`/load-commentator-from-tag-<N>` は N=1..8 のみ受付、範囲外は `ERROR : index must be 1..8` | Companion / curl 運用側は 9 以上を使わない |
| 旧オーバーレイ tag.html / tag_twitter.html / tag_name_twitter.html を削除 | 旧 URL を OBS ブラウザソースに登録していると 404 | `http://localhost:{port}/layout/commentators/index.html`（1920x1080）に差し替え |
| 起動時に commentary を空にする挙動の廃止 | 再起動後もデータが残る（挙動改善） | 空で始めたい場合はカードを手動クリア |

## 互換性を維持した点

- `ChangeCommDataSignal(int, object)` / `LoadCommFromTagSignal(int, str, bool)` は
  同名・同シグネチャ（AutonaWebServerActions の参照経路は無変更）
- 流入 data のキー名は PlayerDB 形式（gamerTag/prefix/name/twitter/custom_textbox）を
  引き続き受け付け、新フィールドにマップする
- `src/Autona.py` のパネル登録・`STATE_VERSION`（=16）は無変更（ドック増減なし）

## リリース時の注意

- リリースノートに「OBS の実況者ソース URL 差し替え」を明記すること
- 手動テスト手順は `.claude/manual-test-checklist.md` の「実況・解説」節を参照
