# サードパーティ クレジット / Third-Party Attributions

Autona は [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper)
のフォークであり、複数のサードパーティ素材・ライブラリを利用しています。
本ファイルは公開配布物に含まれる第三者素材の出典・ライセンスを集約します。

> **凡例**: ✅ 確認済み（ライセンス同梱 or 周知） / ⚠️ 要確認（TBD）。
> ⚠️ の項目は公開前に出典・ライセンスを確定し、必要なライセンスファイルを同梱すること。

---

## ベースソフトウェア

| 項目 | ライセンス | 状態 |
|------|-----------|------|
| TournamentStreamHelper (joaorb64) | MIT License | ✅ [LICENSE](LICENSE) に原著作権を保持 |

---

## ゲームアセット（キャラクター／ステージ画像）

各権利者に著作権があるため **Autona には同梱していない**。
利用者は TournamentStreamHelper のアセットダウンローダーから取得する。
→ [user_data/games/README.md](user_data/games/README.md)

---

## 国旗・地域旗

| 対象 | 内容 | 状態 |
|------|------|------|
| `assets/country_flag/` | ISO 3166-1 alpha-2 命名の国旗 PNG（100px 幅・実比率）257点 | ⚠️ 上流 TSH から継承。国旗自体は一般にパブリックドメイン。画像セットの正確な出典（flagcdn/flagpedia 系と推定）・ライセンスは要確認 |
| `assets/state_flag/` | 国別の州・地域旗 PNG（64×45）3000点超 | ⚠️ 出典・ライセンス要確認 |
| `assets/additional_flag/` | 追加旗 | ⚠️ 出典・ライセンス要確認 |

確認方法: 上流 TSH リポジトリの該当ディレクトリの帰属表記、または画像提供元
（flagcdn.com 等）の利用規約を照合する。

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

| ライブラリ | 想定ライセンス | 状態 |
|-----------|--------------|------|
| jQuery | MIT | ✅ 周知 |
| Lodash | MIT | ✅ 周知 |
| Socket.IO (client) | MIT | ✅ 周知 |
| color-thief | MIT | ✅ 周知 |
| he.js | MIT | ✅ 周知 |
| kuromoji / kuroshiro | Apache 2.0 / MIT | ⚠️ 各ライセンスを要確認 |
| GSAP (GreenSock) | GreenSock 標準ライセンス | ⚠️ 配布条件を要確認（MIT ではない） |

---

## Web UI 依存（npm / `web/package.json`）

React, MUI (@mui/*), Redux Toolkit, react-redux, react-router-dom, socket.io-client
等は概ね MIT。詳細は `web/package.json` および各パッケージのライセンスを参照。
配布物（`web/build/`）に含まれる OSS ライセンスの一覧化は別途検討。

---

## 未確定事項（公開前の TODO）

- [x] ~~各フォントのライセンスファイル同梱~~ → 実使用フォントへ整理・OFL/Apache 同梱済み
- [ ] 国旗・地域旗画像セットの出典・ライセンス確定
- [ ] GSAP の配布条件確認
- [ ] `web/build/` 同梱 OSS のライセンス一覧（必要に応じて）
