<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="ja_JP">
<context>
    <name>app</name>
    <message>
        <source>Select a game folder to import</source>
        <translation>取り込むゲームのフォルダを選択</translation>
    </message>
    <message>
        <source>Select a folder</source>
        <translation>フォルダを選択</translation>
    </message>
    <message>
        <source>Select a file</source>
        <translation>ファイルを選択</translation>
    </message>
    <message>
        <source>Warning</source>
        <translation>注意</translation>
    </message>
    <message>
        <source>The program will now close.</source>
        <translation>プログラムを閉じます。</translation>
    </message>
</context><context>
    <name>portguard</name>
    <message>
        <source>Port {port} is already in use by another process:

{details}

If this is a stale Autona instance, the Web UI will appear blank because Windows randomly routes connections to whichever process answers.

Terminate the existing process(es) and continue?

Alternatively, choose No, then change general.webserver_port in user_data/settings.json and restart Autona, or quit the application that is using the port.</source>
        <translation>ポート {port} は既に別のプロセスが使用しています:

{details}

古い Autona インスタンスが残っている場合、Windows が応答したプロセスへ接続をランダムに振り分けるため、Web UI が空白表示になります。

既存のプロセスを終了して続行しますか？

または「No」を選び、user_data/settings.json の general.webserver_port を変更して Autona を再起動するか、ポートを使用しているアプリを終了してください。</translation>
    </message>
    <message>
        <source>LAN access</source>
        <translation>LAN アクセス</translation>
    </message>
    <message>
        <source>Autona's web server now listens only on this PC (127.0.0.1) by default for security.

If you connect from other devices on your LAN (OBS on another PC, smartphone remote control), enable LAN access now.

You can change this later under Settings → Network.</source>
        <translation>セキュリティのため、Autona の Web サーバーは既定でこの PC 内（127.0.0.1）からのみ接続を受け付けるようになりました。

LAN 上の他のデバイス（別 PC の OBS、スマートフォンからのリモート操作など）から接続する場合は、今すぐ LAN アクセスを有効にしてください。

この設定は後から 設定 → ネットワーク で変更できます。</translation>
    </message>
    <message>
        <source>Web server failed to start</source>
        <translation>Web サーバーの起動に失敗しました</translation>
    </message>
    <message>
        <source>The web server could not start on port {port}:

{error}

OBS browser sources and the Web UI will not work.
Change general.webserver_port in user_data/settings.json and restart Autona, or quit the application that is using the port.</source>
        <translation>ポート {port} で Web サーバーを起動できませんでした:

{error}

OBS のブラウザソースと Web UI は動作しません。
user_data/settings.json の general.webserver_port を変更して Autona を再起動するか、ポートを使用しているアプリを終了してください。</translation>
    </message>
    <message>
        <source>Port conflict</source>
        <translation>ポートの競合</translation>
    </message>
    <message>
        <source>Failed to free port {port}. Please terminate the conflicting process manually.</source>
        <translation>ポート {port} を解放できませんでした。競合するプロセスを手動で終了してください。</translation>
    </message>
</context><context>
    <name>updater</name>
    <message>
        <source>Retry</source>
        <translation>再試行</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation>キャンセル</translation>
    </message>
    <message>
        <source>A new version is available</source>
        <translation>新しいバージョンが利用可能です</translation>
    </message>
    <message>
        <source>A new version v{0} is available (current v{1}).</source>
        <translation>新しいバージョン v{0} が利用可能です（現在 v{1}）。</translation>
    </message>
    <message>
        <source>Open the release page</source>
        <translation>リリースページを開く</translation>
    </message>
    <message>
        <source>Download and install</source>
        <translation>ダウンロードしてインストール</translation>
    </message>
    <message>
        <source>Skip this version</source>
        <translation>このバージョンをスキップ</translation>
    </message>
    <message>
        <source>Later</source>
        <translation>後で</translation>
    </message>
    <message>
        <source>{0} is not attached to this release. Please download it manually from the release page.</source>
        <translation>このリリースには {0} が添付されていません。リリースページから手動で取得してください。</translation>
    </message>
    <message>
        <source>Auto-install is not available in dev mode (python main.py). Only the distributed exe can self-update.</source>
        <translation>開発実行（python main.py）では自動インストールできません。配布版 exe でのみ動作します。</translation>
    </message>
    <message>
        <source>Downloading...</source>
        <translation>ダウンロード中…</translation>
    </message>
    <message>
        <source>An update is already being installed. Close Autona and it will restart automatically when the install finishes.</source>
        <translation>アップデートは既に適用中です。Autona を閉じると、インストール完了後に自動で再起動します。</translation>
    </message>
    <message>
        <source>Comparing files with the new version...</source>
        <translation>新しいバージョンとの差分を確認しています…</translation>
    </message>
    <message>
        <source>Downloading... {0:.1f} / {1:.1f} MB</source>
        <translation>ダウンロード中… {0:.1f} / {1:.1f} MB</translation>
    </message>
    <message>
        <source>Download complete. Preparing install...</source>
        <translation>ダウンロード完了。インストール準備中…</translation>
    </message>
    <message>
        <source>Update downloaded. The app will close, install the update, and restart automatically.</source>
        <translation>アップデートをダウンロードしました。アプリを閉じてインストールし、自動で再起動します。</translation>
    </message>
    <message>
        <source>Error while preparing the update:</source>
        <translation>アップデートの準備中にエラーが発生しました:</translation>
    </message>
    <message>
        <source>Download failed: {0}</source>
        <translation>ダウンロードに失敗しました: {0}</translation>
    </message>
    <message>
        <source>Check for updates</source>
        <translation>更新チェック</translation>
    </message>
    <message>
        <source>You are on the latest version.</source>
        <translation>お使いのバージョンが最新です。</translation>
    </message>
    <message>
        <source>Update check failed:
{0}</source>
        <translation>更新の確認に失敗しました:
{0}</translation>
    </message>
</context><context>
    <name>webpanel</name>
    <message>
        <source>The page could not be loaded</source>
        <translation>ページを読み込めませんでした</translation>
    </message>
    <message>
        <source>Autona's web server did not answer. Try again, or restart Autona if this keeps happening.</source>
        <translation>Autona の Web サーバーが応答しませんでした。再試行するか、繰り返す場合は Autona を再起動してください。</translation>
    </message>
    <message>
        <source>Retry</source>
        <translation>再試行</translation>
    </message>
    <message>
        <source>The page could not be shown</source>
        <translation>ページを表示できませんでした</translation>
    </message>
    <message>
        <source>The window's web view could not be created:

{error}

Restart Autona.</source>
        <translation>ウィンドウの Web ビューを作成できませんでした:

{error}

Autona を再起動してください。</translation>
    </message>
</context></TS>
