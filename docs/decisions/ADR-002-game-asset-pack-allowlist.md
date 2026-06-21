# ADR-002: ゲームアセットパックの許可制（allowlist）

## Status

Accepted

## Context

Autona はゲームのキャラクター/ステージ画像を同梱しない（著作権のため。
[user_data/games/README.md](../../user_data/games/README.md) / 公開準備時の判断）。
代わりに「ゲームアセットを追加」ダイアログ
（[src/AutonaGameAssetGuideWindow.py](../../src/AutonaGameAssetGuideWindow.py)）から、
上流の公開ソース `joaorb64/StreamHelperAssets` の `assets.json` を参照して
アセットパックをオンライン取得（または手持ちフォルダ/ZIP を取り込み）できる。

各ゲームのアセットは複数の「パック」に分かれている（`base_files`, `full`,
`portrait`, `stage_icon`, `variant_icon`, `mural_art`, `vs_renders`, `webm`,
`website`, `art`, `render`, `logo`, `comic`, `poster`, `versus_screen*` など、
カタログ全体で 72 種別）。

このうち、ロゴ・キャラクター/イメージイラスト・レンダー・プロモ素材・音楽/効果音
などの「公式素材」は、メーカーの大会ガイドラインで利用が制限される。例として
任天堂のコミュニティ大会ガイドライン
（https://www.nintendo.co.jp/tournament_guideline/index.html 、Q2/A2）は、
キャプチャ映像/スクリーンショットを除き、ゲームのロゴ・キャラクター/イメージ
イラスト・音楽/効果音等を大会の告知・会場装飾・BGM に使用することを禁じている。

制約: **上流の `assets.json` にはメーカー（publisher）情報が無い**
（各ゲームは `name` と `assets` のみ）。そのため「任天堂タイトルだけ」を
プログラムで自動判別して特別扱いすることはできない。

## Decision

ダウンロード/取り込みできるアセットパックを **許可制（allowlist）** にする。
Autona が機能上実際に使う以下のパックのみを、**全タイトル一律で**提供・インストール
対象とする:

```
base_files, full, portrait, stage_icon, variant_icon
```

これ以外のパック（logo / art / mural_art / vs_renders / render / poster / comic /
cartoon / versus_screen* / webm / webp / website / banner / card / 音声系 など）は、
オンライン取得の一覧に出さず、ダウンロードもせず、フォルダ/ZIP 取り込み時には
取り込み後に該当パックフォルダ（`config.json` を持つ非許可サブフォルダ）を削除する。

実装: `_ALLOWED_PACKS` 定数
（[src/AutonaGameAssetGuideWindow.py](../../src/AutonaGameAssetGuideWindow.py)）。
オンライン一覧生成・ダウンロード対象選択・取り込み後クリーンアップの 3 箇所で参照する。

## Alternatives considered

- **メーカー別の手動ブロックリスト**（任天堂タイトルのコードを列挙し、その対象だけ
  art/promo 系パックを遮断）: 対象を限定できるが、
  (1) `assets.json` にメーカー情報が無く任天堂タイトルの一覧を手作業で維持する必要が
  あり、上流のゲーム追加に追従できず漏れる。
  (2) 遮断対象パック名（72 種別、今後増える）も手作業列挙が必要で同様に漏れる。
  → 保守コストが高く取りこぼしリスクが大きいため不採用。

- **パック名のブロックリスト**（`mural_art`/`vs_renders`/`webm`/`website` を遮断）:
  当初の実装。SSBU のパック名に依存しており、他タイトルの同種パック（`art`,
  `render`, `logo`, `comic` 等）を取りこぼす。新パック種別が既定で通ってしまう。
  → 「既定で安全」でないため不採用、許可制へ置換。

## Consequences

- **良い影響**: 公式イラスト/レンダー/プロモ/音声等を一切インストールしない。
  メーカー判別不要で全タイトルに一律適用でき、上流に新パック種別が増えても既定で
  遮断される（fail-safe）。判断基準が「アプリが使う機能パックか」で単純。
- **悪い影響/トレードオフ**:
  - 任天堂以外（例: Capcom 等）のタイトルでも art/promo 系パックを提供しなくなる。
    各メーカーの許諾如何に関わらず一律で絞るため、本来使えた素材も対象外になりうる。
  - `full`/`portrait`（キャラクター立ち絵）は機能上必要なため許可しているが、これらも
    広義には「キャラクターイラスト」に該当する。スコアボード表示という機能目的での
    利用であり、告知物/装飾/BGM とは用途が異なるという整理で許可している（用途次第の
    判断であり、ガイドラインの逐語的禁止をすべて回避するものではない点に留意）。
  - 特定ゲームが art/render 系しか持たない場合、そのゲームは立ち絵が入らない
    （`base_files` のアイコン等での運用になる）。

## Revisit conditions

- 上流 `assets.json` にメーカー/ライセンス情報が追加され、タイトル単位での
  自動判別が可能になったとき（メーカー別ポリシーへ移行を検討）。
- Autona が新たな機能で別のパック種別（例: 新しいアイコン種別）を使うようになったとき
  （`_ALLOWED_PACKS` に追加）。
- 主要メーカーの大会ガイドラインが改定され、許可/禁止の線引きが変わったとき。
- `full`/`portrait` の同梱・取得方針について、より保守的（スクリーンショットのみ）/
  より緩い方針に見直す必要が生じたとき。
