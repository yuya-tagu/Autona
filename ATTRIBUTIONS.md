# サードパーティ クレジット / Third-Party Attributions

Autona は [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper)
（MIT License）を起点に独自開発したツールです。
本ファイルは公開配布物に含まれる第三者素材の出典・ライセンスを集約します。

> **凡例**: ✅ 確認済み（ライセンス同梱 or 周知） / ⚠️ 要確認（TBD）。
> ⚠️ の項目は公開前に出典・ライセンスを確定し、必要なライセンスファイルを同梱すること。

---

## ベースソフトウェア

| 項目 | ライセンス | 状態 |
|------|-----------|------|
| TournamentStreamHelper (joaorb64) | MIT License | ✅ [LICENSE](LICENSE) に原著作権を保持、全文を [licenses/TournamentStreamHelper-MIT.txt](licenses/TournamentStreamHelper-MIT.txt) に同梱 |

---

## GUI フレームワーク（Qt / PySide6）

| 項目 | ライセンス | 状態 |
|------|-----------|------|
| Qt 6 / PySide6 / shiboken6 | **LGPL v3** | ✅ 動的リンクで使用（`_internal/` に個別ファイルとして同梱、利用者が差し替え可能）。全文 [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt) + [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt) 同梱 |

2026-07 に PyQt6 (GPL v3) から PySide6 (LGPL v3) へ移行済み。GPL はソース非公開の
バイナリ配布と両立しないため、**PyQt6 の再導入は禁止**。LGPL 準拠の詳細は
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) §2 を参照。

---

## ゲームアセット（キャラクター／ステージ画像）

各権利者に著作権があるため **Autona には同梱していない**。
利用者は TournamentStreamHelper のアセットダウンローダーから取得する。
→ [user_data/games/README.md](user_data/games/README.md)

---

## 国旗・地域旗

| 対象 | 内容 | 出典・ライセンス | 状態 |
|------|------|-----------------|------|
| `assets/country_flag/` | ISO 3166-1 alpha-2 命名の国旗 PNG（100px 幅・実比率）257点 | [hampusborgos/country-flags](https://github.com/hampusborgos/country-flags) の `png100px/`（2026-07-03 に代表ファイルの SHA-256 バイト一致で確定）。同リポジトリは Wikimedia Commons 由来で「flags are in the public domain」と表明 | ✅ 出典確定 |
| `assets/state_flag/` | 国別の州・地域旗 PNG（64×45）3000点超 | [joaorb64/world-state-flags](https://github.com/joaorb64/world-state-flags)（TSH 作者本人が [TSH Issue #176](https://github.com/joaorb64/TournamentStreamHelper/issues/176) で明言、代表ファイルのバイト一致でも確認）。取得元は主に Wikipedia / Wikimedia Commons、一部 FOTW（fotw.info） | ✅ 出典確定（下記残リスク参照） |
| `assets/additional_flag/` | 追加旗（プライド旗等） | 単純な幾何学構成の旗デザイン（一般に著作権保護の対象外） | ✅ 実用上問題なし |

**残リスク**: state_flag のうち FOTW（crwflags.com）由来の一部画像は寄稿者著作物で
パブリックドメイン保証がない。国旗・州旗という性質上（公的旗章の単純な再現）実務リスクは
低いと判断するが、個別の権利主張があった場合は該当ファイルを Wikimedia Commons 由来の
画像に差し替えて対応する。

---

## フォント

公開準備時に**実使用のフォントのみ**へ整理し、ライセンス本文を同梱済み。
詳細は [assets/font/README.md](assets/font/README.md) を参照。

### 同梱フォント（✅ ライセンス確認・同梱済み）

| フォント | 場所 | ライセンス | 著作権者 / 出典 |
|---------|------|-----------|----------------|
| LINE Seed JP | `assets/font/LINESeedJP/` | SIL OFL 1.1 | LY Corporation — https://seed.line.me/ |
| 源暎Mゴシック | `assets/font/GenEiMGothic/` | SIL OFL 1.1 | おたもん — https://okoneya.jp/font/ |
| Roboto Condensed | `assets/font/RobotoCondensed.ttf` 他 | Apache 2.0 | Google |
| Noto Sans | `layout/include/NotoSans-Regular.ttf` | SIL OFL 1.1 | The Noto Project Authors |
| Roboto | `layout/include/Roboto-Regular.ttf` | Apache 2.0 | Google |

ライセンス本文: [assets/font/OFL-1.1.txt](assets/font/OFL-1.1.txt)（OFL 系）/
[assets/font/Apache-License-2.0.txt](assets/font/Apache-License-2.0.txt)（Apache 系）。

### 削除したフォント（公開準備時）

- **未使用フォント**（源暎ちくみん明朝 / 源暎ｷﾞｺ字 / kaiso_up / OpenSans /
  Oswald / OtsutomeFont / Samurai / Red Hat Display・Mono / Bungee /
  Kaisotai / Noto Sans 未使用ウェイト / `layout/fonts/` 一式）を削除。
- **たまねぎ楷書激**: フォント単体の再配布が利用規約で禁止されているため削除。
  使用していた `layout/result_vertical_geki/` は LINE Seed JP にフォールバック。

---

## ライブラリ（オーバーレイ `layout/include/`）

| ライブラリ | ライセンス | 状態 |
|-----------|-----------|------|
| jQuery | MIT | ✅ min.js ヘッダに著作権表示保持 |
| Lodash | MIT | ✅ 同上 |
| Socket.IO (client) | MIT | ✅ 同上 |
| color-thief | MIT | ✅ 同上 |
| he.js | MIT | ✅ 同上 |
| kuroshiro / kuroshiro-analyzer-kuromoji | MIT（形態素解析部は kuromoji.js = Apache 2.0） | ✅ 確認済み |
| GSAP 3.12.5 (GreenSock) | GreenSock Standard License | ✅ 確認済み（2026-07-03）。無償配布アプリへの同梱は Standard License の範囲内（本体 min.js ヘッダの `@license` 表記を保持）。参考: 3.13 以降は Webflow 傘下で完全無料の新ライセンスに移行済みのため、将来バージョン更新時はそちらに乗る |

---

## Web UI 依存（npm / `web/package.json`）

React, MUI (@mui/*), Redux Toolkit, react-redux, react-router-dom, socket.io-client
等は MIT / Apache 2.0 等のパーミッシブライセンス。配布物（`web/build/`）に含まれる
OSS の一覧は [licenses/npm-packages.txt](licenses/npm-packages.txt) に機械生成済み。

---

## Python 依存（配布バイナリ同梱）

`dependencies/requirements.txt` の推移的閉包の一覧を
[licenses/python-packages.txt](licenses/python-packages.txt) に機械生成済み。
コピーレフト系（LGPL の PySide6 / py7zr 系）の扱いは
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) を参照。

**注**: `countryflag` は GPL v3 の `country_converter` / `aioconsole` を引き込むため
requirements から削除済み（2026-07。コード側の import は以前から削除されていた）。
**GPL ライセンスのパッケージを依存に追加してはならない**（ソース非公開配布と非両立）。

---

## 未確定事項（公開前の TODO）

- [x] ~~各フォントのライセンスファイル同梱~~ → 実使用フォントへ整理・OFL/Apache 同梱済み
- [x] ~~国旗・地域旗画像セットの出典・ライセンス確定~~ → 2026-07-03 確定（上記表参照）
- [x] ~~GSAP の配布条件確認~~ → Standard License で問題なし（上記表参照）
- [x] ~~`web/build/` 同梱 OSS のライセンス一覧~~ → licenses/npm-packages.txt に生成済み
