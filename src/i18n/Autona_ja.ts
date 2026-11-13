<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="ja_JP">
<context>
    <name>Settings</name>
    <message>
        <location filename="..\Autona.py" line="610" />
        <source>Settings</source>
        <translation>設定</translation>
    </message>
</context><context>
    <name>api</name>
    <message>
        <location filename="..\Autona.py" line="745" />
        <source>Open reference &amp; tester</source>
        <translation>リファレンス・テスターを開く</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="753" />
        <source>Companion settings</source>
        <translation>Companion の設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="761" />
        <source>OBS / Advanced Scene Switcher</source>
        <translation>OBS / Advanced Scene Switcher</translation>
    </message>
    <message>
        <source>From other devices on the LAN, every operation that changes something (and the player list) needs the access token: send the header X-Autona-Token: &lt;token&gt; (or Authorization: Bearer &lt;token&gt;, or add ?token=&lt;token&gt; to the URL). Reading the state and the overlays needs no token.</source>
        <translation>LAN 上の他のデバイスから状態を変更する操作（と選手リストの取得）には、アクセストークンが必要です。ヘッダ X-Autona-Token: &lt;トークン&gt; を付けて送ってください（Authorization: Bearer &lt;トークン&gt; や、URL 末尾の ?token=&lt;トークン&gt; でも可）。状態の読み取りとオーバーレイの表示にはトークンは不要です。</translation>
    </message>
    <message>
        <source>LAN access is disabled; the API is reachable from this PC only. Enable it under Settings → Network (restart required).</source>
        <translation>LAN アクセスは無効です。API にはこの PC からのみ接続できます。有効にするには 設定 → ネットワーク で設定してください（要再起動）。</translation>
    </message>
    <message>
        <source>LAN access is saved as on and takes effect when Autona is restarted; until then the API is reachable from this PC only.</source>
        <translation>LAN アクセスは有効として保存済みです。Autona を再起動すると有効になります（それまでは API にこの PC からのみ接続できます）。</translation>
    </message>
    <message>
        <source>OBS must run on this PC. For OBS on another PC on the same LAN, replace 127.0.0.1 with this PC's IP address and add ?token=&lt;access token&gt; to the end of the URL (&amp;token=&lt;access token&gt; if the URL already has a ?; copy the token from API Settings → Open reference &amp; tester).</source>
        <translation>OBS はこの PC で実行してください。同一 LAN 上の別 PC の OBS から使う場合は、127.0.0.1 をこの PC の IP アドレスに置き換え、URL の末尾に ?token=&lt;アクセストークン&gt; を付けてください（URL にすでに ? があるときは &amp;token=&lt;アクセストークン&gt;。トークンは API設定 → リファレンス・テスターを開く からコピーできます）。</translation>
    </message>
    <message>
        <source>LAN access: disabled — OBS must run on this PC. To use OBS on another PC, enable it under Settings → Network (restart required).</source>
        <translation>LAN アクセス: 無効 — OBS はこの PC で実行する必要があります。別 PC の OBS を使う場合は 設定 → ネットワーク で有効にしてください（要再起動）。</translation>
    </message>
    <message>
        <source>LAN access: saved as on, takes effect when Autona is restarted — until then OBS must run on this PC.</source>
        <translation>LAN アクセス: 有効として保存済み（Autona を再起動すると有効になります）— それまでは OBS はこの PC で実行する必要があります。</translation>
    </message>
    <message>
        <source>This device must be on the same LAN as this PC.</source>
        <translation>このデバイスはこの PC と同一の LAN 上にある必要があります。</translation>
    </message>
    <message>
        <source>LAN access: disabled — other devices cannot connect. Enable it under Settings → Network (restart required).</source>
        <translation>LAN アクセス: 無効 — 他のデバイスからは接続できません。有効にするには 設定 → ネットワーク で設定してください（要再起動）。</translation>
    </message>
    <message>
        <source>LAN access: saved as on, takes effect when Autona is restarted — until then other devices cannot connect.</source>
        <translation>LAN アクセス: 有効として保存済み（Autona を再起動すると有効になります）— それまでは他のデバイスからは接続できません。</translation>
    </message>
    <message>
        <source>*.txt files hold the raw value (easy to match). program_state.json carries revision/timestamp, so watch it for "file modified" to react to any state change.</source>
        <translation>*.txt ファイルには生の値が入ります（マッチしやすい）。program_state.json は revision/timestamp を持つため、あらゆる状態変化に反応させたい場合は「file modified」で監視します。</translation>
    </message>
    <message>
        <source>Add a condition -&gt; File, then browse to one of the files below (use Copy file path to get the absolute path).</source>
        <translation>条件 -&gt; File を追加し、下記いずれかのファイルを指定します（絶対パスは「ファイルパスをコピー」で取得できます）。</translation>
    </message>
    <message>
        <source>Add a trigger (e.g. Hotkey, Scene, or WebSocket message).</source>
        <translation>トリガーを追加します（例: Hotkey、Scene、WebSocket message）。</translation>
    </message>
    <message>
        <source>Add an action -&gt; HTTP, paste the URL from the table below, and set the method to POST.</source>
        <translation>アクション -&gt; HTTP を追加し、下の表の URL を貼り付けて、メソッドを POST にします。</translation>
    </message>
    <message>
        <source>If the table lists a body, paste it into the body field. Leave Content-Type at its default (application/json).</source>
        <translation>表にボディがある場合はボディ欄に貼り付けます。Content-Type はデフォルト（application/json）のままにします。</translation>
    </message>
    <message>
        <source>Requires Advanced Scene Switcher 1.30 or newer (HTTP action with a Content-Type field). The action also stores the response status and body in temporary variables, usable in follow-up conditions.</source>
        <translation>Advanced Scene Switcher 1.30 以降（Content-Type 欄のある HTTP アクション）が必要です。このアクションは応答のステータスとボディを一時変数に保存するため、後続の条件でも利用できます。</translation>
    </message>
    <message>
        <source>Add an action -&gt; Switch scene and pick the target scene.</source>
        <translation>アクション -&gt; Switch scene を追加し、対象のシーンを選びます。</translation>
    </message>
    <message>
        <source>Base URL</source>
        <translation>ベース URL</translation>
    </message>
    <message>
        <source>Best-of value</source>
        <translation>Best-of の値</translation>
    </message>
    <message>
        <source>Bitfocus Companion controls Autona through the External Control API over HTTP. Map physical buttons to the operations below.</source>
        <translation>Bitfocus Companion は外部制御 API（HTTP）を通じて Autona を操作します。物理ボタンを下記の操作に割り当ててください。</translation>
    </message>
    <message>
        <source>Board 1 / Team 1 / Player 1 tag</source>
        <translation>ボード1 / チーム1 / プレイヤー1 のタグ</translation>
    </message>
    <message>
        <source>Board 1 / Team 1 score</source>
        <translation>ボード1 / チーム1 のスコア</translation>
    </message>
    <message>
        <source>Board 1 / Team 2 / Player 1 tag</source>
        <translation>ボード1 / チーム2 / プレイヤー1 のタグ</translation>
    </message>
    <message>
        <source>Board 1 / Team 2 score</source>
        <translation>ボード1 / チーム2 のスコア</translation>
    </message>
    <message>
        <source>Body</source>
        <translation>ボディ</translation>
    </message>
    <message>
        <source>Body (JSON)</source>
        <translation>ボディ (JSON)</translation>
    </message>
    <message>
        <source>Button icons (click to copy)</source>
        <translation>ボタンアイコン（クリックでコピー）</translation>
    </message>
    <message>
        <source>Choose the match mode: exact content / regex for a *.txt value, or "File was modified" on program_state.json to react to any change.</source>
        <translation>マッチモードを選びます。*.txt の値には exact content / regex を、あらゆる変更に反応させるには program_state.json に「File was modified」を使います。</translation>
    </message>
    <message>
        <source>Click an icon to copy it to the clipboard, then paste it onto a Companion button.</source>
        <translation>アイコンをクリックするとクリップボードにコピーされます。Companion のボタンに貼り付けてください。</translation>
    </message>
    <message>
        <source>Confirm</source>
        <translation>確認</translation>
    </message>
    <message>
        <source>Connection</source>
        <translation>接続</translation>
    </message>
    <message>
        <source>Contains</source>
        <translation>内容</translation>
    </message>
    <message>
        <source>Control Autona from Advanced Scene Switcher</source>
        <translation>Advanced Scene Switcher から Autona を操作する</translation>
    </message>
    <message>
        <source>Copied icon</source>
        <translation>アイコンをコピーしました</translation>
    </message>
    <message>
        <source>Copy</source>
        <translation>コピー</translation>
    </message>
    <message>
        <source>Copy URL</source>
        <translation>URL をコピー</translation>
    </message>
    <message>
        <source>Copy body</source>
        <translation>ボディをコピー</translation>
    </message>
    <message>
        <source>Copy curl</source>
        <translation>curl をコピー</translation>
    </message>
    <message>
        <source>Copy file path</source>
        <translation>ファイルパスをコピー</translation>
    </message>
    <message>
        <source>Copy this icon</source>
        <translation>このアイコンをコピー</translation>
    </message>
    <message>
        <source>Description</source>
        <translation>説明</translation>
    </message>
    <message>
        <source>Destructive operations (clear / reset) require "confirm": true in the JSON body. See the reference window for the full catalogue.</source>
        <translation>破壊的な操作（clear / reset）は JSON ボディに "confirm": true が必要です。全一覧はリファレンスウィンドウを参照してください。</translation>
    </message>
    <message>
        <source>Deprecated: the legacy routes (/scoreboardN-..., /score, /program-state, etc.) will be removed in v2.0. Use /api/v1 (listed above) for new integrations.</source>
        <translation>非推奨: 旧ルート（/scoreboardN-...、/score、/program-state など）は v2.0 で削除予定です。新規の連携には /api/v1（上記一覧）を使用してください。</translation>
    </message>
    <message>
        <source>Envelope &amp; error codes</source>
        <translation>エンベロープ・エラーコード</translation>
    </message>
    <message>
        <source>Error</source>
        <translation>エラー</translation>
    </message>
    <message>
        <source>Example button mappings</source>
        <translation>ボタン割り当ての例</translation>
    </message>
    <message>
        <source>Full state (JSON, has revision/timestamp)</source>
        <translation>全状態（JSON、revision/timestamp を含む）</translation>
    </message>
    <message>
        <source>In "Connections", add the "Generic HTTP" module.</source>
        <translation>「Connections」で「Generic HTTP」モジュールを追加します。</translation>
    </message>
    <message>
        <source>In OBS, open Tools -&gt; Advanced Scene Switcher -&gt; Macros and add a macro.</source>
        <translation>OBS で ツール -&gt; Advanced Scene Switcher -&gt; Macros を開き、マクロを追加します。</translation>
    </message>
    <message>
        <source>Invalid JSON body</source>
        <translation>JSON ボディが不正です</translation>
    </message>
    <message>
        <source>Keep text export enabled (Settings: general.disable_export must be off).</source>
        <translation>テキスト出力を有効にしておきます（設定: general.disable_export をオフにする必要があります）。</translation>
    </message>
    <message>
        <source>Launch Companion and open its web admin page (default http://127.0.0.1:8000).</source>
        <translation>Companion を起動し、Web 管理ページ（既定は http://127.0.0.1:8000）を開きます。</translation>
    </message>
    <message>
        <source>Layout list (?kind=scoreboard|designer|commentary|top8)</source>
        <translation>レイアウト一覧（?kind=scoreboard|designer|commentary|top8 で絞り込み）</translation>
    </message>
    <message>
        <source>Load into tester</source>
        <translation>テスターに読み込む</translation>
    </message>
    <message>
        <source>Logged</source>
        <translation>ログ記録</translation>
    </message>
    <message>
        <source>Match label (e.g. Winners Finals)</source>
        <translation>試合ラベル（例: Winners Finals）</translation>
    </message>
    <message>
        <source>Method</source>
        <translation>メソッド</translation>
    </message>
    <message>
        <source>Open settings</source>
        <translation>設定を開く</translation>
    </message>
    <message>
        <source>Operation</source>
        <translation>操作</translation>
    </message>
    <message>
        <source>Optionally set the button text, then press the key to test.</source>
        <translation>必要に応じてボタンのテキストを設定し、キーを押してテストします。</translation>
    </message>
    <message>
        <source>Output folder</source>
        <translation>出力フォルダ</translation>
    </message>
    <message>
        <source>Paste the request URL from the table below. For POST, set the body to the JSON shown and add the header Content-Type: application/json.</source>
        <translation>下の表のリクエスト URL を貼り付けます。POST の場合はボディに表示された JSON を設定し、ヘッダー Content-Type: application/json を追加します。</translation>
    </message>
    <message>
        <source>Path</source>
        <translation>パス</translation>
    </message>
    <message>
        <source>Phase label (e.g. Top 8)</source>
        <translation>フェーズラベル（例: Top 8）</translation>
    </message>
    <message>
        <source>Player 1 score +1</source>
        <translation>プレイヤー1 スコア +1</translation>
    </message>
    <message>
        <source>Player 1 score -1</source>
        <translation>プレイヤー1 スコア -1</translation>
    </message>
    <message>
        <source>Player 2 score +1</source>
        <translation>プレイヤー2 スコア +1</translation>
    </message>
    <message>
        <source>Player 2 score -1</source>
        <translation>プレイヤー2 スコア -1</translation>
    </message>
    <message>
        <source>Port</source>
        <translation>ポート</translation>
    </message>
    <message>
        <source>Preset</source>
        <translation>プリセット</translation>
    </message>
    <message>
        <source>Pull current stream set</source>
        <translation>現在の配信セットを取得</translation>
    </message>
    <message>
        <source>Reference</source>
        <translation>リファレンス</translation>
    </message>
    <message>
        <source>Refresh</source>
        <translation>更新</translation>
    </message>
    <message>
        <source>Refresh Top 8 bracket</source>
        <translation>Top 8 ブラケットを更新</translation>
    </message>
    <message>
        <source>Refresh stats</source>
        <translation>統計取得</translation>
    </message>
    <message>
        <source>Refresh statistics (async fetch)</source>
        <translation>統計情報を再取得（非同期）</translation>
    </message>
    <message>
        <source>Request failed</source>
        <translation>リクエストに失敗しました</translation>
    </message>
    <message>
        <source>Reset scores</source>
        <translation>スコアをリセット</translation>
    </message>
    <message>
        <source>Response</source>
        <translation>レスポンス</translation>
    </message>
    <message>
        <source>Save and trigger the condition to test.</source>
        <translation>保存し、条件を発火させてテストします。</translation>
    </message>
    <message>
        <source>Select a button, then "Add action" and choose the Generic HTTP "POST" (or "GET") action.</source>
        <translation>ボタンを選択し、「Add action」から Generic HTTP の「POST」（または「GET」）アクションを選びます。</translation>
    </message>
    <message>
        <source>Send</source>
        <translation>送信</translation>
    </message>
    <message>
        <source>Sending…</source>
        <translation>送信中…</translation>
    </message>
    <message>
        <source>Set up integration between OBS Studio's Advanced Scene Switcher plugin and Autona.</source>
        <translation>OBS Studio の Advanced Scene Switcher プラグインと Autona を連携させます。</translation>
    </message>
    <message>
        <source>Setup steps</source>
        <translation>設定手順</translation>
    </message>
    <message>
        <source>Set both scores (absolute)</source>
        <translation>両チームのスコアを設定（絶対値）</translation>
    </message>
    <message>
        <source>States / regions of a country</source>
        <translation>国の州・地域一覧</translation>
    </message>
    <message>
        <source>Toggle display options (partial)</source>
        <translation>表示項目を切り替え（部分更新）</translation>
    </message>
    <message>
        <source>Update player fields (partial)</source>
        <translation>選手データを更新（部分更新）</translation>
    </message>
    <message>
        <source>Success</source>
        <translation>成功</translation>
    </message>
    <message>
        <source>Swap teams</source>
        <translation>チームを入れ替え</translation>
    </message>
    <message>
        <source>Tester</source>
        <translation>テスター</translation>
    </message>
    <message>
        <source>This is a destructive operation. Send it?</source>
        <translation>これは破壊的な操作です。送信しますか？</translation>
    </message>
    <message>
        <source>The app is shutting down</source>
        <translation>アプリを終了しています</translation>
    </message>
    <message>
        <source>Trigger OBS from Autona state</source>
        <translation>Autona の状態から OBS をトリガーする</translation>
    </message>
    <message>
        <source>URL</source>
        <translation>URL</translation>
    </message>
    <message>
        <source>Watch file</source>
        <translation>監視ファイル</translation>
    </message>
    <message>
        <source>optional</source>
        <translation>任意</translation>
    </message>
    <message>
        <source>Access token</source>
        <translation>アクセストークン</translation>
    </message>
    <message>
        <source>Show</source>
        <translation>表示</translation>
    </message>
    <message>
        <source>Regenerate</source>
        <translation>再発行</translation>
    </message>
    <message>
        <source>Regenerate access token</source>
        <translation>アクセストークンの再発行</translation>
    </message>
    <message>
        <source>Issue a new access token? Devices that use the current one (Companion, Stream Deck, a browser on another PC) can no longer change anything until they are given the new token.</source>
        <translation>アクセストークンを再発行しますか？今のトークンを使っている機器（Companion、Stream Deck、別 PC のブラウザ）は、新しいトークンを設定し直すまで操作できなくなります。</translation>
    </message>
    <message>
        <source>Token missing or wrong (writes from another device)</source>
        <translation>トークンがない・誤り（他のデバイスからの書き込み）</translation>
    </message>
    <message>
        <source>Refused browser page (cross-site / another origin)</source>
        <translation>拒否されたブラウザのページ（クロスサイト・別オリジン）</translation>
    </message>
    <message>
        <source>If Companion runs on another PC (LAN access), also add the header X-Autona-Token with the access token as its value. Copy the token from Help &amp; Settings → API Settings → Open reference &amp; tester.</source>
        <translation>Companion を別の PC で動かす場合（LAN アクセス）は、ヘッダ X-Autona-Token も追加し、値にアクセストークンを入れてください。トークンは ヘルプ＆設定 → API設定 → リファレンス・テスターを開く からコピーできます。</translation>
    </message>
    <message>
        <source>Commentary role / icon candidates</source>
        <translation>実況・解説の役割・アイコンの候補</translation>
    </message>
    <message>
        <source>Update caster fields (partial; a 5th shown is 409)</source>
        <translation>実況・解説者の欄を更新（部分更新。5 人目の表示は 409）</translation>
    </message>
    <message>
        <source>Reorder casters (slot order)</source>
        <translation>実況・解説者を並べ替え（スロット順）</translation>
    </message>
    <message>
        <source>Fill a caster from a player DB entry</source>
        <translation>選手 DB の選手で実況・解説者の欄を埋める</translation>
    </message>
    <message>
        <source>Select a panel's layout</source>
        <translation>パネルのレイアウトを選択</translation>
    </message>
    <message>
        <source>Set a feature's colors</source>
        <translation>機能別カラーを設定</translation>
    </message>
</context><context>
    <name>app</name>
    <message>
        <source>Top 8 Display Settings</source>
        <translation>TOP8表示設定</translation>
    </message>
    <message>
        <source>Help &amp; Settings</source>
        <translation>ヘルプ＆設定</translation>
    </message>
    <message>
        <source>Show sidebar</source>
        <translation>サイドバーを表示</translation>
    </message>
    <message>
        <source>Select game title</source>
        <translation>ゲームを選ぶ</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="140" />
        <location filename="..\AutonaUpdater.py" line="104" />
        <location filename="..\Autona.py" line="579" />
        <location filename="..\Autona.py" line="170" />
        <source>Warning</source>
        <translation>注意</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="107" />
        <location filename="..\Autona.py" line="172" />
        <source>The program will now close.</source>
        <translation>プログラムを閉じます。</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="649" />
        <location filename="..\Autona.py" line="292" />
        <source>Set tournament</source>
        <translation>大会を選ぶ</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="309" />
        <source>Tournament Info</source>
        <translation>大会情報設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="416" />
        <source>Setup</source>
        <translation>事前設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="416" />
        <source>Display</source>
        <translation>表示設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="416" />
        <source>Data</source>
        <translation>各種データ</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="416" />
        <source>ScoreboardCategory</source>
        <translation>スコアボード</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="301" />
        <source>Stream Settings</source>
        <translation>配信チャンネル設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="321" />
        <source>Thumbnail Settings</source>
        <translation>対戦動画サムネイル</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="332" />
        <source>Result Settings</source>
        <translation>大会リザルト</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="340" />
        <source>Bracket</source>
        <translation>トーナメント表</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="293" />
        <source>Board</source>
        <translation>スコアボード設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="362" />
        <location filename="..\Autona.py" line="357" />
        <source>Scoreboard</source>
        <translation>スコアボード入力</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="367" />
        <source>Commentary</source>
        <translation>実況・解説設定</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="102" />
        <source>Player List</source>
        <translation>選手一覧</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="393" />
        <source>Statistics</source>
        <translation>統計情報</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="450" />
        <source>Always on top</source>
        <translation>常に手前に表示</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="479" />
        <source>Add game assets</source>
        <translation>ゲームアセットを追加</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="569" />
        <source>Check for updates</source>
        <translation>更新情報を調べる</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="585" />
        <source>Join the Discord community</source>
        <translation>Discordコミュニティに参加する</translation>
    </message>
    <message>
        <source>Event support inquiries</source>
        <translation>イベント運営支援のご相談</translation>
    </message>
    <message>
        <source>About Autona</source>
        <translation>Autona について</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="654" />
        <source>Unset tournament</source>
        <translation>大会の設定を解除</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="716" />
        <source>Modded content</source>
        <translation>MODコンテンツ</translation>
    </message>
    <message>
        <location filename="..\AutonaApiInfoWindow.py" line="116" />
        <location filename="..\Autona.py" line="743" />
        <source>API Settings</source>
        <translation>API設定</translation>
    </message>
    <message>
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="149" />
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="122" />
        <source>Team {0}</source>
        <translation>チーム{0}</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="94" />
        <source>Horizontal (1920x1080)</source>
        <translation>横向き (1920x1080)</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="95" />
        <source>Horizontal TOP16 (1920x1080)</source>
        <translation>横向き TOP16 (1920x1080)</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="96" />
        <source>Vertical (960x1080)</source>
        <translation>縦向き (960x1080)</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="97" />
        <source>Vertical Dark Gold 激 (960x1080)</source>
        <translation>縦向き Dark Gold 激 (960x1080)</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="98" />
        <source>Team / Crew (1920x1080)</source>
        <translation>チーム / クルー (1920x1080)</translation>
    </message>
    <message>
        <location filename="..\core\ResultService.py" line="99" />
        <source>Team / Crew 縦並び (1920x1080)</source>
        <translation>Team / Crew 縦並び (1920x1080)</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="0" />
        <source>Stats Display Settings</source>
        <translation>統計情報表示設定</translation>
    </message>
    <message>
        <location filename="..\core\ThumbnailService.py" line="90" />
        <source>Standard</source>
        <translation>標準</translation>
    </message>
    <message>
        <location filename="..\core\ThumbnailService.py" line="90" />
        <source>Center Panel</source>
        <translation>中央パネル</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="135" />
        <source>Paste the tournament URL.</source>
        <translation>大会のURLをここに貼って下さい</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="135" />
        <source>For StartGG, the link must contain the /event/ part</source>
        <translation>StartGGのリンクには/event/partを含めて下さい</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="135" />
        <source>Supported providers:</source>
        <translation>対応プロバイダー:</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="162" />
        <source>Set tournament URL</source>
        <translation>大会のURLを貼る</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="321" />
        <source>Confirm</source>
        <translation>確認</translation>
    </message>
    <message>
        <location filename="..\AutonaTournamentDataProvider.py" line="323" />
        <source>Start.gg との接続を解除します。よろしいですか？</source>
        <translation>Start.gg との接続を解除します。よろしいですか？</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="1364" />
        <source>Error</source>
        <translation>エラー</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="1356" />
        <source>The tournament URL could not be loaded.</source>
        <translation>大会 URL を読み込めませんでした。</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="1358" />
        <source>Make sure that your tournament URL is correctly formatted and points to an existing event, and try again.</source>
        <translation>大会 URL の形式が正しく、存在するイベントを指しているか確認してから再試行してください。</translation>
    </message>
    <message>
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="97" />
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="76" />
        <location filename="..\core\ResultService.py" line="1000" />
        <source>Sample Tournament</source>
        <translation>サンプル大会</translation>
    </message>
</context><context>
    <name>portguard</name>
    <message>
        <location filename="..\AutonaWebServer.py" line="821" />
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
        <location filename="..\AutonaWebServer.py" line="831" />
        <source>Port conflict</source>
        <translation>ポートの競合</translation>
    </message>
    <message>
        <location filename="..\AutonaWebServer.py" line="861" />
        <source>Failed to free port {port}. Please terminate the conflicting process manually.</source>
        <translation>ポート {port} を解放できませんでした。競合するプロセスを手動で終了してください。</translation>
    </message>
</context><context>
    <name>punctuation</name>
    <message>
        <location filename="..\Autona.py" line="865" />
        <source>(</source>
        <translation>(</translation>
    </message>
    <message>
        <location filename="..\Autona.py" line="865" />
        <source>)</source>
        <translation>)</translation>
    </message>
</context><context>
    <name>status</name>
    <message>
        <source>Fetching bracket data from Start.gg...</source>
        <translation>Start.gg からブラケット情報を取得中...</translation>
    </message>
    <message>
        <source>Fetching set data from Start.gg...</source>
        <translation>Start.gg からセット情報を取得中...</translation>
    </message>
    <message>
        <source>Fetching the set list from Start.gg...</source>
        <translation>Start.gg からセット一覧を取得中...</translation>
    </message>
    <message>
        <source>Fetching the set list from Start.gg... ({page}/{pages})</source>
        <translation>Start.gg からセット一覧を取得中... ({page}/{pages})</translation>
    </message>
    <message>
        <source>Some pages of the set list could not be fetched ({failed}/{pages})</source>
        <translation>セット一覧の一部ページ取得に失敗しました ({failed}/{pages})</translation>
    </message>
    <message>
        <source>Fetching entrants from Start.gg...</source>
        <translation>Start.gg から参加者情報を取得中...</translation>
    </message>
    <message>
        <source>Fetching entrants from Start.gg... ({page}/{pages})</source>
        <translation>Start.gg から参加者情報を取得中... ({page}/{pages})</translation>
    </message>
    <message>
        <source>Loading game assets... ({codename})</source>
        <translation>ゲームアセットを読み込み中... ({codename})</translation>
    </message>
    <message>
        <source>Building the TOP 8...</source>
        <translation>TOP 8 を構築中...</translation>
    </message>
    <message>
        <source>Fetching the results...</source>
        <translation>リザルトを取得中...</translation>
    </message>
</context><context>
    <name>thumb_app</name>
    <message>
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="221" />
        <source>Please select a game first</source>
        <translation>ゲームを選んで下さい</translation>
    </message>
    <message>
        <location filename="..\thumbnail\main_generate_thumbnail.py" line="225" />
        <source>Player {0} tag missing</source>
        <translation>プレイヤー{0}のタグがありません</translation>
    </message>
</context><context>
    <name>updater</name>
    <message>
        <location filename="..\AutonaUpdater.py" line="149" />
        <source>Retry</source>
        <translation>再試行</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="151" />
        <source>Cancel</source>
        <translation>キャンセル</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="437" />
        <source>A new version is available</source>
        <translation>新しいバージョンが利用可能です</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="449" />
        <source>A new version v{0} is available (current v{1}).</source>
        <translation>新しいバージョン v{0} が利用可能です（現在 v{1}）。</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="459" />
        <source>Open the release page</source>
        <translation>リリースページを開く</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="486" />
        <source>Download and install</source>
        <translation>ダウンロードしてインストール</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="493" />
        <source>Skip this version</source>
        <translation>このバージョンをスキップ</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="498" />
        <source>Later</source>
        <translation>後で</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="505" />
        <source>{0} is not attached to this release. Please download it manually from the release page.</source>
        <translation>このリリースには {0} が添付されていません。リリースページから手動で取得してください。</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="516" />
        <source>Auto-install is not available in dev mode (python main.py). Only the distributed exe can self-update.</source>
        <translation>開発実行（python main.py）では自動インストールできません。配布版 exe でのみ動作します。</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="532" />
        <source>Downloading...</source>
        <translation>ダウンロード中…</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="525" />
        <source>An update is already being installed. Close Autona and it will restart automatically when the install finishes.</source>
        <translation>アップデートは既に適用中です。Autona を閉じると、インストール完了後に自動で再起動します。</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="500" />
        <source>Comparing files with the new version...</source>
        <translation>新しいバージョンとの差分を確認しています…</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="548" />
        <source>Downloading... {0:.1f} / {1:.1f} MB</source>
        <translation>ダウンロード中… {0:.1f} / {1:.1f} MB</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="558" />
        <source>Download complete. Preparing install...</source>
        <translation>ダウンロード完了。インストール準備中…</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="570" />
        <source>Download failed: {0}</source>
        <translation>ダウンロードに失敗しました: {0}</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="674" />
        <location filename="..\AutonaUpdater.py" line="665" />
        <source>Check for updates</source>
        <translation>更新チェック</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="666" />
        <source>You are on the latest version.</source>
        <translation>お使いのバージョンが最新です。</translation>
    </message>
    <message>
        <location filename="..\AutonaUpdater.py" line="675" />
        <source>Update check failed:
{0}</source>
        <translation>更新の確認に失敗しました:
{0}</translation>
    </message>
</context></TS>
