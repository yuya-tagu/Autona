# ゲームアセットについて / Game Assets

このフォルダには、各ゲームのキャラクター・ステージ画像（アイコン／ポートレート／
フルアート等）を配置します。

**これらの画像は各権利者に著作権があるため、Autona には同梱していません。**
アセットは [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper)
のアセットダウンローダーから取得し、このフォルダ配下に配置してください。

---

This folder holds per-game character and stage images (icons / portraits /
full art, etc.).

**These images are copyrighted by their respective rights holders and are NOT
bundled with Autona.** Obtain them using the asset downloader in
[TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper)
and place them under this folder.

## 配置先 / Expected layout

```
user_data/games/
└── {game}/                     # 例: ssbu
    ├── base_files/
    │   ├── config.json         # ゲーム定義（Autona がゲームを認識するために必須）
    │   └── icon/               # キャラクターアイコン
    ├── full/                   # フルアート
    ├── portrait/               # ポートレート
    └── stage_icon/             # ステージアイコン
```

Autona は起動時に `user_data/games/*/base_files/config.json` を走査して
利用可能なゲームを認識します。`config.json` を含むゲームフォルダを上記の構成で
配置すれば、そのゲームが選択できるようになります。
