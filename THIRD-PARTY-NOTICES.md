# Third-Party Notices / 第三者ソフトウェアに関する表示

Autona の配布物には、以下の第三者ソフトウェア・素材が含まれています。
各ソフトウェアはそれぞれのライセンスに従って利用・再配布されます。
ライセンス全文は本ファイルと同梱の `licenses/` ディレクトリを参照してください。

最終更新: 2026-07-03

---

## 1. ベースソフトウェア

| ソフトウェア | ライセンス | 全文 |
|-------------|-----------|------|
| [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper) (João Ribeiro Bezerra) | MIT License | [licenses/TournamentStreamHelper-MIT.txt](licenses/TournamentStreamHelper-MIT.txt) |

Autona 自体も MIT License です。TournamentStreamHelper を起点に開発されており、
その著作権表示は [LICENSE](LICENSE) にも保持されています。

## 2. GUI フレームワーク（Qt / PySide6）— LGPL v3

本ソフトウェアは以下を **LGPL v3** の下で**動的リンク**により使用しています:

| ソフトウェア | バージョン | ライセンス |
|-------------|-----------|-----------|
| Qt 6 (The Qt Company) | 6.11 系 | LGPL v3 |
| PySide6 (Qt for Python) | 6.11 系 | LGPL v3 |
| shiboken6 | 6.11 系 | LGPL v3 |

**LGPL v3 準拠に関する表示:**

- ライセンス全文: [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt)（LGPL v3 は GPL v3 への
  追加許諾であるため [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt) も併せて参照）
- Qt / PySide6 のライブラリは配布物の `_internal/` ディレクトリに**個別のファイル**
  （DLL / pyd）として同梱されており、利用者は互換バージョンのライブラリに
  **自由に差し替える（relink する）ことができます**
- LGPL ライブラリの差し替え・差し替えのためのデバッグは、本ソフトウェアの
  ライセンス（MIT）および TERMS.md によって妨げられません
- ソースコードの入手先: Qt — https://download.qt.io/ / PySide6 —
  https://pypi.org/project/PySide6/ および https://code.qt.io/cgit/pyside/pyside-setup.git/
- Autona は Qt / PySide6 を**改変せずに**使用しています

## 3. Python ライブラリ

配布バイナリに同梱される Python パッケージの一覧とライセンスは
[licenses/python-packages.txt](licenses/python-packages.txt) を参照してください。

**コピーレフト系ライセンスのパッケージ（全て未改変・動的利用）:**

| パッケージ | ライセンス | 全文 | ソース入手先 |
|-----------|-----------|------|-------------|
| py7zr | LGPL v2.1+ | [licenses/LGPL-2.1.txt](licenses/LGPL-2.1.txt) | https://pypi.org/project/py7zr/ |
| pyppmd / pybcj / inflate64 / multivolumefile | LGPL v2.1+ | 同上 | https://pypi.org/ の各プロジェクトページ |

その他のパッケージは MIT / BSD / Apache 2.0 / MPL 2.0 等の
パーミッシブライセンスです（一覧参照）。

## 4. Web UI（React / npm パッケージ）

Web UI（`web/build/`）にバンドルされる npm パッケージの一覧は
[licenses/npm-packages.txt](licenses/npm-packages.txt) を参照してください。
主要なもの: React (MIT), MUI (MIT), Redux Toolkit (MIT), react-router (MIT),
socket.io-client (MIT), i18next (MIT), Emotion (MIT), TypeScript (Apache 2.0)。

## 5. オーバーレイ同梱 JavaScript ライブラリ（`layout/include/`）

| ライブラリ | ライセンス | 備考 |
|-----------|-----------|------|
| jQuery / jquery.waitforimages | MIT | |
| Lodash | MIT | |
| Socket.IO client | MIT | |
| color-thief | MIT | |
| he.js | MIT | |
| kuroshiro / kuroshiro-analyzer-kuromoji | MIT | 形態素解析は kuromoji.js (Apache 2.0) を利用 |
| GSAP 3.13.0 (Webflow / GreenSock) | GSAP Standard "No Charge" License | 無償配布アプリへの同梱利用。全文は `licenses/GSAP-LICENSE.txt`、https://gsap.com/standard-license 参照 |

MIT ライブラリの著作権表示は各 `.min.js` ファイル先頭のヘッダに保持されています。

## 6. フォント

| フォント | ライセンス | 全文 |
|---------|-----------|------|
| LINE Seed JP (LY Corporation) | SIL OFL 1.1 | [licenses/OFL-1.1.txt](licenses/OFL-1.1.txt) |
| 源暎Mゴシック (おたもん) | SIL OFL 1.1 | 同上 |
| Noto Sans (The Noto Project Authors) | SIL OFL 1.1 | 同上 |
| Roboto / Roboto Condensed (Google) | Apache 2.0 | [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt) |

詳細は `assets/font/README.md` および `assets/font/` 同梱のライセンス本文を参照。

## 7. 国旗・地域旗画像

`assets/country_flag/` / `assets/state_flag/` / `assets/additional_flag/` の
旗画像の出典は [ATTRIBUTIONS.md](ATTRIBUTIONS.md) を参照してください。

## 8. 外部サービス

- 本ソフトウェアは **start.gg API**（Start.gg, Inc.）を利用して大会データを取得できます。
  start.gg は Start.gg, Inc. の商標です。**Autona は start.gg の非公式ツールであり、
  Start.gg, Inc. とは提携・承認関係にありません。**
  API の利用は [start.gg APIs Terms of Use](https://www.start.gg/about/apitos) に従います。
- ゲームのキャラクター・ステージ画像等のゲームアセットは各ゲームの権利者に
  著作権があり、**Autona には同梱されていません**（利用者が各自取得します）。
