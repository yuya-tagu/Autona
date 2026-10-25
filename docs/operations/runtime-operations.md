# Runtime Operations

本番配信時の運用手順。**現時点で確証のない手順は `TBD` とし、確認方法を併記する。**
外部制御 API / Companion 連携を入れる際は、この運用を壊さないことを最優先する。

> ステータス凡例: ✅ コード/ドキュメント確認済 / 📄 既存ドキュメント由来 / ⚠️ 要確認 / `TBD`

## Purpose

- 配信オペが Autona を**事故なく**起動・運用・復旧するための手順を一元化する
- 「壊してはいけない操作」を明文化し、変更レビュー時の判断材料にする

## Startup procedure

⚠️ 以下は README / CLAUDE.md からの推定。**現場の実手順は要確認（`TBD` 箇所あり）**。

1. **配布版（本番）**: `Autona.exe`（プロジェクト直下）を起動 ✅
   - 開発時のみ `python main.py`（自動アップデート無効）✅
2. 起動時に GitHub Releases を確認し、更新があればダイアログ表示 📄
3. ゲーム選択（サイドバー上部のゲームボタン）→ アセットの非同期ロード ✅
4. Start.gg 大会をロード（サイドバー上部の大会ボタン → Set tournament）✅
5. OBS 側でブラウザソース/テキストソースが `http://localhost:{port}/...` と `out/` を参照していることを確認 📄
   - 既定ポートは `5107`（2026-09-19 に `5000` から変更）。設定の `general.webserver_port` で変更可 ✅
   - Web UI は `/`・`/web`・`/scoreboard`（board1）・`/team`（board2）✅
   - **既定バインドは `127.0.0.1`（この PC のみ）**。別 PC の OBS / 操作端末から繋ぐ場合は
     設定 → 一般 の「LAN 上の他のデバイスからのアクセスを許可」をオン（要再起動）✅
6. Web リモート操作を使う場合、操作端末から該当 URL に接続できることを確認 📄

> **起動順の依存（2026-07-30 確定）** ✅: 起動アーキテクチャ再設計以降、**Web サーバーはメイン
> ウィンドウが表示され、ローディングオーバーレイが消えた後に起動する**（`Window._StartWebServer()`）。
> つまり「ウィンドウが出ている ≠ OBS から取得できる」。
>
> - 目安は `logs/autona.log` の `STARTUP phase=webserver-ready` 行（サーバースレッド起動完了。
>   実際に応答し始めるまで一瞬のラグがある）。`phase=core-shown` はウィンドウ表示時点で、まだ未起動。
> - それより前に OBS ブラウザソースが取得を試みると接続失敗する。**OBS 側でソースを再読込すれば
>   復旧する**（Autona 側の再起動は不要）。
> - 「LAN アクセスを許可するか」の確認ダイアログは、この設定に未回答の状態（新規インストール・
>   旧バージョンからの更新後の初回起動）でここに出る。**回答するまでサーバーは起動しない**。
> - Autona は**多重起動しない**（named mutex）。起動済みの状態で再度 exe を実行すると、既存
>   ウィンドウが前面化するだけで 2 個目のプロセスは何も表示せず終了する。

## Pre-event checklist

本番前の確認項目（暫定。PdM/現場で確定要）:

- [ ] `Autona.exe` が最新リリースか（自動アップデート完了済みか）📄
- [ ] 対象ゲームのアセットがロード済みか 📄
- [ ] Start.gg 大会が正しくロードされ、対象フェーズ/セットが見えるか 📄
- [ ] OBS のブラウザソース/テキストソースが正しい URL・パスを指しているか 📄
- [ ] Web リモート端末から接続できるか（使う場合）📄
- [ ] ホットキーが効くか（チームスコア +1/−1 等）✅
- [ ] サムネ/結果画像の出力先（`out/stream/` 等）が書き込めるか 📄
- [ ] `TBD`: ネットワーク（localhost 以外から接続する場合のポート開放/ファイアウォール）
- [ ] **別 PC の OBS / スマホリモート等、LAN 端末から接続する場合: 「LAN 上の他のデバイスからの
      アクセスを許可」設定がオンか（既定オフ、要再起動）** ✅
- [ ] **外部制御 API / Web を使う場合: 同一 LAN 内に限定し、ルータ/PC ファイアウォールで
      ポート（既定 5107）を外部公開しない**（下記「Network & security」参照）✅

## During-event operation

- スコア操作の経路は **3 つ**: デスクトップ UI / グローバルホットキー / Web UI（リモート）✅
- いずれの経路も最終的に StateManager を更新し、`out/` + WebSocket で配信に反映 ✅
- 本番中は**レイアウト保存状態を不用意に変えない**（STATE_VERSION 復元に影響）✅

## Network & security（外部制御 API / Web リモート）

> ⚠️ **重要（ADR-001 D5 のリスク受容の前提条件）**。
> **既定バインドは `127.0.0.1`（この PC のみ）** — この状態では LAN の他端末から一切接続できない。
> 設定 → 一般 の「LAN 上の他のデバイスからのアクセスを許可」をオンにすると `0.0.0.0`（全 NIC）に
> バインドする。
>
> アクセス制御（ADR-005 S3a・判定の正本は `src/core/WebAccess.py`）:
>
> | 接続元 | 読み取り（オーバーレイ・`/web`・state・カタログ・Socket.IO の受信） | 書き込み（v1 の POST/PATCH・legacy の書き込み・Socket.IO の書き込みイベント）と選手 DB |
> |---|---|---|
> | この PC（`127.0.0.1` / `localhost` の URL でも、この PC の LAN IP の URL でも） | 不要 | 不要。ただし次は他端末と同じ扱い（トークン必須）か拒否: ブラウザの別サイト・別オリジンのページ（403。CSRF 対策）、このサーバーの名前以外のホスト名宛て（401。DNS rebinding 対策。`http://<PC 名>:5107` も該当）、転送ヘッダ付きの中継（401。下記） |
> | LAN の他端末 | 不要 | **アクセストークン必須**（無い・誤りは 401 `UNAUTHENTICATED`） |
>
> - トークンは初回起動時に `user_data/settings.json` の `general.webserver_token` に生成される。
>   **ヘルプ＆設定 → API設定 → リファレンス・テスターを開く** で表示・コピー・再発行する
>   （settings.json を読めなかった起動では作り直さず、LAN からの書き込みを拒否する。再起動か再発行で直る）
> - 渡し方: `X-Autona-Token: <token>` ヘッダ / `Authorization: Bearer <token>` / URL 末尾の `?token=<token>`
>   （URL にすでに `?` があるときは `&token=<token>`）のいずれか
> - **読み取りはトークン不要のまま**。同一 LAN に到達できる端末は state（選手名・スコア等）を読める
> - HTTP で配信するファイルは許可リストのみ（`layout/`・`assets/`・`web/build/`・`out/`・`user_data/` の画像フォルダ・
>   `user_data/tournament_banner.png`）。`settings.json`・`local_players.json` などは 404

現場で必ず守ること（LAN アクセスをオンにする場合）:

- **同一 LAN 内のみで運用する。** インターネットや信頼できないネットワークに**ポート（既定 5107）を公開しない**。
  - ルータのポートフォワード／DMZ に Autona 機を載せない
  - PC のファイアウォールで 5107 番への外部アクセスを遮断（LAN 内のみ許可）
- 外部公開が必要な事情がある場合は、**VPN 経由 or リバースプロキシ＋TLS**を別途用意する
  （トークンは平文の HTTP で流れる。LAN の外に出さない）。
  - ⚠️ **この PC 上のリバースプロキシ・トンネルは、必ず転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP` のどれか）を付ける設定にする。**
    Autona は転送ヘッダの付いた要求を「他の端末から」として扱い、トークンを要求する。
    付けない構成では中継された要求がループバック（この PC）からに見え、**トークンなしで全操作が通る（認証が外れる）**。
- 操作端末（Companion / Stream Deck / ブラウザ）にだけトークンを渡す。漏れた・端末を手放したら**再発行**する
  （再発行すると旧トークンの HTTP 要求は即座に、Socket.IO 接続は次のイベントから拒否される）。
- 万一の誤操作・不正操作の追跡用に、外部制御 API は**書き込み操作をログに記録**する（spec §2.7）。
  トークンは 1 つを共有するため、記録できるのは送信元 IP まで（端末の識別はしない）。
  拒否した要求（トークン不足・CSRF・許可リスト外のファイル）も種類ごとに初回だけ warning で記録する。

> この制約が許容できない運用（LAN 外配信オペ・不特定端末の混在等）が出たら、
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) の
> 認証/バインド方針（D5）を Revisit する。

## OBS connection handling

- Autona 本体は OBS へ直接接続しない。通常連携は**ブラウザソース + テキスト/JSON ファイル参照** 📄
- Advanced Scene Switcher など OBS 側ツールから Autona を制御する場合は、実装済みの `/api/v1/` を HTTP で叩く ✅
- Autona 再起動時、ブラウザソースは再読込が必要な場合がある（`TBD`: 自動再接続の有無）
- `out/program_state.json` と個別テキストファイルは状態変更ごとに更新される ✅

## Scoreboard operation

- スコア +1/−1、Swap、各種 Reset、Clear All は HTTP/WS ルートとしても提供 ✅
- クルーバトル時はストック増減・アクティブ選手切替の専用操作あり ✅
- 「自動セット更新」「自動スコア更新」は General タブの設定で制御する ✅
- ステージストライクの操作 UI / Web ルートは削除済み。Start.gg parser には互換データとして
  `stage_strike` / `ruleset` を読む処理が残るが、現行の外部制御対象ではない ✅

## Recovery procedure

⚠️ 大半が `TBD`。現状で言えること:

- Autona がクラッシュした場合、再起動でレイアウト/状態は保存値から復元される（STATE_VERSION）✅
- `TBD`: セッション途中の状態（スコア等）がどこまで永続化され、再起動で戻るか → 確認要
- `TBD`: 自動バックアップ/スナップショットは**現状なし**（ROADMAP に提案あり、未実装）📄
- Web UI 側が状態を見失った場合、ページ再読込で `program_state` 全受信からやり直せる ✅

### Designer デザインのロールバック（共通ランタイム導入前の exe に戻す場合）

共通ランタイム（`layout/designer_runtime/`）導入後の Autona は、起動時に旧形式の Designer
デザイン（`layout/custom/{slug}/` に `runtime.js` を持つもの）の `index.html` / `runtime.js` /
`runtime.css` を `layout/custom/{slug}/_legacy/` へ退避し、`index.html` を共通ランタイムを読む
スタブに置き換える（`project.json` と `assets/` はそのまま）。

- **上書きダウングレード**（旧版 zip を既存フォルダへ上から展開）: 展開は上書きのみで
  削除をしない（更新ヘルパーの `robocopy /E` も PURGE なし）ので `layout/designer_runtime/` が
  残り、スタブのまま表示される。戻し作業は不要 ✅
- **クリーンインストールで旧版に戻した場合**（フォルダを作り直して旧版を入れ、`layout/custom/` だけ
  持ち込んだ場合）: 旧版には `layout/designer_runtime/` が無いので、スタブのままだと OBS が
  **真っ白になる**。以下の手順で戻す

1. Autona を終了する
2. 各 `layout/custom/{slug}/_legacy/` の 3 ファイル（`index.html` / `runtime.js` / `runtime.css`）を
   1 つ上の `layout/custom/{slug}/` へ**移動**して上書きする（コピーではなく移動。`_legacy/` が
   空になっていれば、再度新版に上げたときに改めて退避される）
3. 旧版 exe を起動し、OBS のブラウザソースを再読込する（URL は変わらない）

`_legacy/` を戻さなくても、旧版 exe の Designer でそのデザインを開いて「保存」すれば旧形式
（runtime 同梱）で書き直される ✅

## Known failure modes

- **Start.gg クエリ timeout**（20 秒）: 重いクエリ/不安定回線で空データが返る → null-safe で表示は壊れない設計 ✅
- **複数 GitHub アカウントによる push 失敗**: 開発側の話（運用には無関係）✅（CLAUDE.md）
- **`__pycache__` のスタール**: 開発時のみ。`sys.dont_write_bytecode=True` で対策済 ✅
- **ポート競合（既定 5107）**: Web サーバー起動時（= ウィンドウ表示後）に競合プロセスを検出し、
  PID とプロセス名を示して終了の可否を尋ねる（既定ボタンは「No」）。解放できない場合はダイアログで
  手動終了を促す ✅
  - 多重起動は named mutex で防いでいるため、検出されるのは**別アプリが 5107 番を使っている**ケース。
    その場合は 設定 → 一般 → Webサーバーポート を変更して Autona を再起動する（「No」を選んでも
    サーバー起動自体は続行するため、変更 + 再起動が必要）
- `TBD`: OBS とのファイル/URL 参照ずれ時の症状切り分け手順
- **既定ポート変更（次期リリース / 2026-09-19 決定）**: 既定 Web サーバーポートが
  `5000` → `5107` に変わる。アップデート後、**OBS のブラウザソース URL と ASS
  （Advanced Scene Switcher）マクロの URL を `:5000` から `:5107` へ書き換える**必要がある
  （書き換えないとブラウザソースが真っ白・マクロが無反応になる）。書き換えたくない場合は
  設定 → 一般 → Webサーバーポート に `5000` を明示保存して再起動すれば従来どおり動く。
  設定済みの `general.webserver_port` はアップデートで上書きされない。理由は
  [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) Addendum

## Manual fallback procedures

スタッフが復旧できる最終手段（暫定）:

1. Autona を再起動（状態は保存値から復元）✅
2. OBS のブラウザソースを再読込 📄
3. Web リモートが不調ならデスクトップ UI / ホットキーで操作継続 ✅
4. `TBD`: Start.gg 連携不調時に手入力でスコアボードを進める手順の明文化

## Operations that must remain simple for staff

非エンジニアのスタッフが本番で触る前提で、**複雑化させてはいけない**操作:

- スコア +1/−1、Swap、Reset（最頻操作・ホットキー前提）
- 大会/セットのロード（メニューから数クリック）
- OBS との接続（URL/ファイル参照を変えさせない）

> ⚠️ 外部制御 API / Companion / Stream Deck を入れる場合も、これらの**既存操作経路を
> 置き換えず、追加の選択肢として**提供すること。既存の最短経路を壊すと現場が止まる。
> 影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)。
