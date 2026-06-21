## Autona 配信オーバーレイ テンプレート

Autona が出力する状態データ（`out/` 配下のファイル）を読み取って表示する、
HTML/CSS/JS ベースのアニメーション付きオーバーレイテンプレート集です。
OBS 等のブラウザソースで読み込んで使用します。

`/include/` には各テンプレート共通で使うユーティリティ（`globals.js`）と、
利用ライブラリ（`gsap`・`jquery`）を同梱しています。

### ローカルでのテスト・デバッグ（Chrome）

- Chrome 起動時に `--allow-file-access-from-files` フラグを付ける
    - Windows: `"C:\PathTo\chrome.exe" --allow-file-access-from-files`
      もしくは PowerShell で `Start-Process "chrome.exe" "--allow-file-access-from-files"`
- レイアウトの `html` ファイルを Chrome で開き、F12 でデベロッパーツールを表示
- *Device toolbar* を有効化し、画面サイズを 1920x1080 に設定

これでコンソールログ（とエラー）の確認、CSS ルールの即時編集ができます。

> **Note:** 現行テンプレートは前身プロジェクトのデザインを引き継いでいます。
> 今後 Autona 版として順次リデザインを進める予定です。
