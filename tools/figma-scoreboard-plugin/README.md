# Autona Scoreboard Export Figma Plugin

Figmaで選択したLayerをAutona Scoreboard Designerの一時インポートへ送信するローカルプラグインです。

## 使い方

1. Autonaを起動し、Webサーバーを既定ポート `5000` で待機させます。
2. Figma Desktopで `Plugins > Development > Import plugin from manifest...` を開きます。
3. このディレクトリの `manifest.json` を選択します。
4. 1920x1080のFrame（アートボード）内で移植したいLayerを選択します。アートボード自体を選択した場合は、表示中の子要素を展開して読み込みます。
5. プラグインを起動し、各Layerを `PNG` / `SVG` / `動的テキスト` / `動的画像` / `ゲーム履歴` / `除外` に割り当てます。Layer名が `p1.name` や `p1.score` などのbindingと一致する場合は自動認識します。
6. `Autonaへ送信` を押します。
7. AutonaのScoreboard Designerに自動反映されます。追加し忘れたLayerは、後から選択して再送信できます。
8. 内容を確認して、`保存` で `layout/custom/{slug}` に保存します。

TEXT Layerは自動的に初期値が `動的テキスト` になります。ベクター図形は自動的に初期値が `SVG` になります。
TEXT Layerでも `PNG` / `SVG` を選べば、文字を固定したまま取り込めます。`SVG`を選択したLayerは
SVGのまま保存・表示され、ベクターの解像度を保持します。SubtractなどのBoolean
Operationもベクターへ変換して、結果形状をSVGレイヤーとして取り込みます。

SVGとして取り込んだ図形は、Autona Scoreboard Designerのレイヤーインスペクターで
`Game-History表示`を有効にできます。ゲーム番号とP1/P2の取得色、未取得時の線色・線幅を
設定すると、未取得時はアウトライン、取得済みは塗りとして表示されます。

Figmaで左右反転したLayerは、回転とは別の反転情報として保持し、動的テキスト・動的画像でも反転状態を再現します。
PNG/SVGとして書き出すLayerは、Figmaが書き出した反転済みの見た目をそのまま使用します。

テキストを `動的テキスト` にした場合、Figmaのフォントファミリーとウェイトを
Autonaへ引き継ぎます。Google Fontsとして利用できるフォントは、DesignerとOBS
ランタイムがGoogle Fontsから読み込みます。ネットワークに接続できない環境では
CSSのフォールバックフォントが使われます。Figma上の見た目を完全に固定したい
テキストは `PNG` を選択してください。

プラグインのLayer一覧で `Figmaで選択` を押すか、行をクリックすると、対応する要素を
Figma上で選択して表示位置へ移動します。Figma上の選択状態は一覧の強調表示にも反映されます。

## 制約

- 接続先は `http://localhost:5000/api/designer/imports` 固定です。
- 選択Layerの直近親Frameは1920x1080である必要があります。1920x1080のFrame（アートボード）を選択した場合は、表示中の子要素を個別レイヤーとして送信します。
- アートボード内の子Frame・Groupも再帰的に展開します。アートボード以外のFrameやGroupを直接選択した場合は、その選択対象を1枚のPNGまたはSVGとして扱います。
- 固定テキストは `PNG` または `SVG` として取り込めます。Autonaの実データで差し替える文字は `動的テキスト` を選んでbindingを指定してください。
- 送信した内容はAutona側で一時保持され、開いているScoreboard Designerへ自動反映されます。既存の編集内容を維持したまま追加・統合されます。ファイルには保存されません。
- OBS用ファイルは、Autona Designerで `保存` を押すと生成されます。
