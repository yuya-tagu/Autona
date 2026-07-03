# OBS Studio / Advanced Scene Switcher 操作 API 調査

> **ステータス: 調査メモ（一次情報ベース）／一部要実機確認。** 2026-06-15 作成。
> 本書は OBS Studio の制御 API（**obs-websocket v5**）と、OBS プラグイン
> **Advanced Scene Switcher（ASS）** を Autona の「操作 API」として採用するための
> **仕様調査と利用方法の整理**である。最終的な採否・設計境界は ADR で確定する（§6）。

- 関連: 既存の外部制御 API I/F → [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)
- 関連: 既存制御面・OBS 連携の現状方針 → [current-architecture.md](current-architecture.md)（§「OBS 連携の方針」）
- 関連: 操作棚卸し → [external-control-api-candidates.md](external-control-api-candidates.md)
- 関連: 設計境界の決定 → [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)

> 凡例: ✅=一次情報で確認 / 📄=ドキュメント記載 / ⚠️=注意・制約 / **TBD**=要実機確認。

---

## 0. 重要: 既存方針との関係（先に読む）

現行ドキュメントは **「OBS-websocket は使わない方針（ROADMAP で検討外）」** と明記している
（[current-architecture.md](current-architecture.md) §「OBS 連携の方針」）。
本調査は **この方針を見直し、OBS Studio + ASS を操作 API として採用する**方向の検討である。

- 既存の連携（OBS ブラウザソースで `layout/` の HTML 表示、`out/` のテキスト/JSON 参照）は**維持**する。
  本件は**それを置き換えるものではなく、双方向の能動制御を追加**する話。
- 「使わない」→「使う」への転換は**設計判断**なので、本書では決め切らず ADR 候補として §6 に整理する。
- Autona 本体のコード変更は本ドキュメント作業では行わない（[../README.md](../README.md) 記入ルール準拠）。

---

## 1. 全体像（連携の 4 方向）

「操作 API」と一口に言っても、Autona・OBS・ASS の三者の間には**向きの異なる 4 経路**がある。
これを混同すると設計が破綻するため、最初に分離して定義する。

```
                    ┌─────────────────────────────────────────┐
                    │                  OBS Studio              │
   ┌────────┐  (A)  │  obs-websocket(:4455)   ┌──────────────┐ │
   │        │──────▶│  ── Scene/Source 制御 ──│  Advanced    │ │
   │ Autona │       │                         │  Scene       │ │
   │  本体  │◀──────│  (C) CallVendorRequest  │  Switcher    │ │
   │        │  (A)  │      でマクロ/変数起動 ─▶│ (ASS, IN-PROC)│ │
   │ /api/v1│       └─────────────────────────│              │ │
   └────────┘                                 │  Macro       │ │
       ▲                                      │ (Cond+Action)│ │
       │ (B) ASS → Autona の /api/v1 を叩く     │              │ │
       └──────────────────────────────────────│  ローカル OBS │ │
       │ (D) ASS が out/ ファイル内容を監視      │  を直接操作   │ │
   ┌────────┐                                 └──────────────┘ │
   │  out/  │◀────────────────────────(D)──────────────────────┘
   │ files  │
   └────────┘
```

| 経路 | 向き | 手段 | 主な用途 |
|------|------|------|---------|
| **(A)** | Autona → OBS | obs-websocket v5 クライアント | Autona からシーン切替・ブラウザソース URL 差替・配信/録画制御 |
| **(B)** | ASS → Autona | ASS の Run action + `curl`（HTTP action は §4 制約あり） | OBS 内の自動化トリガから Autona のスコア操作等を呼ぶ |
| **(C)** | Autona/外部 → ASS | obs-websocket `CallVendorRequest` | Autona から ASS マクロ起動・変数注入（→ ASS が OBS をローカル操作） |
| **(D)** | ASS ← Autona の出力 | ASS の **File condition**（`out/` 監視） | コードを足さずに Autona の状態変化で OBS を自動制御 |

**設計上の含意:**
- **(A)/(C) は同じ obs-websocket サーバー（OBS の :4455）を使う**。Autona は OBS に対して
  「Companion と同じ外部 WebSocket クライアント」の立場になる（OBS のインプロセス側ではない）。
- **(B)/(D) は ASS が Autona を呼ぶ側**。(B) は能動 HTTP、(D) は受動ファイル監視。
- **ASS は OBS のインプロセス・プラグイン**なので、自分が動く OBS のシーン/ソースは
  obs-websocket を介さず**直接**操作する（ネットワーク往復なし）。リモート OBS のときだけ Connections（§4.5）を使う。

---

## 2. OBS Studio: obs-websocket v5 仕様

### 2.1 概要・バージョン ✅

| 項目 | 内容 |
|------|------|
| 正体 | OBS を WebSocket 経由でリモート制御する公式 API（"Remote-control of OBS Studio through WebSocket"） |
| v4 / v5 | v5 でプロトコル全面刷新（`{op,d}` の OpCode 構造・SHA256 認証・RequestBatch）。**v4 と非互換**。クライアントも v5 専用が必要 |
| バンドル | **OBS Studio 28.0.0 以降に標準同梱**。別途インストール不要 |
| RPC バージョン | `rpcVersion: 1`（破壊的変更まで増えない）。実機は `GetVersion` の `obsWebSocketVersion` で確認 |

### 2.2 接続・有効化 ✅

| 項目 | 内容 |
|------|------|
| 既定ポート | **4455**（設定変更可） |
| URL 形式 | `ws://<host>:4455`（例: `ws://127.0.0.1:4455`） |
| 有効化 | OBS の **「ツール → WebSocket サーバー設定」** で有効化。初回ロード時にパスワード自動生成 |
| 接続情報 | 同ダイアログの **「Show Connect Info」** で IP / ポート / パスワード表示 |
| CLI 上書き | `--websocket_port` / `--websocket_password` / `--websocket_ipv4_only` 等 |

⚠️ プロトコルは**平文 WS（非 TLS）**。**LAN 限定運用が前提**（Autona の既存信頼境界と同じ。ADR-001 §2.2 と整合）。

### 2.3 メッセージ構造・OpCode ✅

全メッセージは `{ "op": <OpCode>, "d": {...} }`。

| OpCode | 名称 | 向き | 説明 |
|:---:|------|------|------|
| 0 | Hello | S→C | 接続直後。バージョン・rpcVersion・（認証時）`authentication`{challenge,salt} |
| 1 | Identify | C→S | Hello 応答。認証文字列・`eventSubscriptions`・`rpcVersion` |
| 2 | Identified | S→C | 識別完了。以後操作可 |
| 3 | Reidentify | C→S | イベント購読等の更新 |
| 5 | Event | S→C | イベント発生時 |
| 6 | Request | C→S | リクエスト送信 |
| 7 | RequestResponse | S→C | リクエスト応答 |
| 8 | RequestBatch | C→S | 複数リクエスト一括 |
| 9 | RequestBatchResponse | S→C | バッチ応答 |

### 2.4 認証フロー ✅

`authentication` フィールドが Hello に**ある場合のみ**認証が必要（なければパスワード無し運用）。

```text
# Hello の salt / challenge と password から SHA256+base64 を 2 段
secret = base64( sha256_bytes( password + salt ) )
auth   = base64( sha256_bytes( secret + challenge ) )
# auth を Identify に載せる
```

> 主要クライアントライブラリ（§2.8）はこの計算を内部処理するので、利用側はパスワードを渡すだけ。

### 2.5 主要 Request（Autona 用途中心）✅

Request の `d` = `{ requestType, requestId, requestData }`。応答 = `{ requestType, requestId, requestStatus, responseData }`。

**シーン / スタジオモード:**

| Request | 用途 | 主要パラメータ |
|---------|------|---------------|
| `GetSceneList` | 全シーン一覧 + 現在のプログラム/プレビュー | — |
| `GetCurrentProgramScene` | 現在のプログラムシーン | — |
| `SetCurrentProgramScene` | **プログラムシーン切替** | `sceneName` |
| `GetCurrentPreviewScene` / `SetCurrentPreviewScene` | プレビューシーン（スタジオモード） | `sceneName` |
| `GetStudioModeEnabled` / `SetStudioModeEnabled` | スタジオモード状態/切替 | `studioModeEnabled` |
| `TriggerStudioModeTransition` | プレビュー→プログラムへトランジション | `?transitionDuration` |
| `SetCurrentSceneTransition` | トランジション種別設定 | `?transitionName` |

**ソース表示・設定（ブラウザソース URL 差替が重要）:**

| Request | 用途 | 主要パラメータ |
|---------|------|---------------|
| `GetSceneItemList` | シーン内アイテム一覧 | `sceneName` |
| `GetSceneItemId` | ソース名→アイテム ID | `sceneName`, `sourceName` |
| `GetSceneItemEnabled` / `SetSceneItemEnabled` | **ソースの表示/非表示** | `sceneName`, `sceneItemId`, `sceneItemEnabled` |
| `GetInputSettings` / `SetInputSettings` | **入力設定取得/変更（ブラウザ URL 差替）** | `inputName`, `inputSettings`, `?overlay` |
| `GetSourceFilterList` / `SetSourceFilterEnabled` | フィルタ一覧/有効切替 | `sourceName`, `filterName`, `filterEnabled` |

ブラウザソースの URL 差替（Autona の overlay 切替に直結する典型操作）:

```json
{ "op": 6, "d": {
  "requestType": "SetInputSettings", "requestId": "url-1",
  "requestData": {
    "inputName": "Scoreboard Overlay",
    "inputSettings": { "url": "http://127.0.0.1:5000/scoreboard?board=1" },
    "overlay": true
  }
}}
```
⚠️ `overlay:true`（既定）は部分マージ、`false` は全置換。`SetInputSettings` の対象キー名は実用上 `inputName`
で通るが、正確なキーは実機 `GetInputSettings` のエコーで **TBD**。

**配信/録画・一般:**

| Request | 用途 |
|---------|------|
| `StartStream` / `StopStream` / `ToggleStream` / `GetStreamStatus` | 配信制御・状態 |
| `StartRecord` / `StopRecord` / `ToggleRecord` / `GetRecordStatus` | 録画制御・状態 |
| `GetVersion` | バージョン・`availableRequests`（対応 Request 一覧） |
| `GetStats` | CPU/メモリ/FPS 等 |
| `BroadcastCustomEvent` | 全 WS クライアントへ任意イベント配信 |
| `CallVendorRequest` | **他プラグイン登録の独自リクエスト呼出（ASS 連携の要・§4.6）** |

**RequestBatch**（複数を 1 メッセージで・OpCode 8）: `executionType`（0=SerialRealtime / 1=SerialFrame / 2=Parallel）、
`haltOnFailure`、間に `Sleep` を挟める。シーン切替→ソース表示の連続操作を原子的に投げるのに有用。

### 2.6 主要 Event ✅

| Event | 発火 | 主フィールド |
|-------|------|-------------|
| `CurrentProgramSceneChanged` | シーン切替 | `sceneName` |
| `SceneItemEnableStateChanged` | ソース表示切替 | `sceneName`, `sceneItemId`, `sceneItemEnabled` |
| `StreamStateChanged` / `RecordStateChanged` | 配信/録画状態変化 | `outputActive`, `outputState` |
| `InputMuteStateChanged` | ミュート変化 | `inputName`, `inputMuted` |
| `CustomEvent` / `VendorEvent` | カスタム/プラグイン発火 | 任意ペイロード |

購読は Identify の `eventSubscriptions` ビットマスク（Scenes=`1<<2`, Inputs=`1<<3`, SceneItems=`1<<7` 等。OR 結合）。

### 2.7 セキュリティ・運用 ✅
- パスワード認証（SHA256 チャレンジ）。無効化も可だが非推奨。
- **複数クライアント同時接続可**（Companion と Autona が同時に OBS へ接続して問題ない）。
- 権限粒度なし（認証を通れば全 Request 実行可）。信頼境界は**パスワード + LAN**。

### 2.8 クライアントライブラリ（Python 厚め）✅

| 言語 | ライブラリ | 備考 |
|------|-----------|------|
| Python | **obsws-python** | v5 専用。メソッドは snake_case（`get_version()`）。同期。Python 3.9+ |
| Python | simpleobsws | async。v1.0.0+ が v5 対応 |
| JS/TS | obs-websocket-js | ブラウザ/Node 両対応 |

```python
import obsws_python as obs
client = obs.ReqClient(host="localhost", port=4455, password="****", timeout=3)
client.set_current_program_scene("Match")
client.set_input_settings(
    name="Scoreboard Overlay",
    settings={"url": "http://127.0.0.1:5000/scoreboard?board=1"},
    overlay=True,
)
```
⚠️ Autona は PySide6 アプリ。obs-websocket クライアントを足すなら**別スレッド/非同期**で動かし、
GUI スレッド・StateManager のロックを尊重する（既存の QThread + Flask と同じ作法。[current-architecture.md](current-architecture.md) §「改変前に影響調査が必要な領域」）。新規依存追加の可否は ADR 事項。

---

## 3. Advanced Scene Switcher（ASS）仕様

### 3.1 概要・バージョン・導入 ✅
- OBS の**自動化プラグイン**（作者 WarmUpTill）。中核は **Macro = Conditions + Actions（+ Else-Actions）**。
- 最新版 **1.34.2（2026-05-30）**。最新版の**最小対応 OBS は 31.1.1**。⚠️ 使う OBS の版に合うリリースを選ぶ（**TBD**: 自環境の OBS 版）。
- 導入: Windows/macOS はインストーラ推奨、Linux は Flatpak（`com.obsproject.Studio.Plugin.SceneSwitcher`）。
- UI: OBS「ツール → Advanced Scene Switcher」。
- 配布元: OBS フォーラム resources / GitHub Releases。docs サイト（docs.advancedsceneswitcher.io）は調査時応答せず、**canonical は GitHub Wiki**。

### 3.2 マクロモデル ✅
- 条件結合: **AND / OR / NOT / XOR**、**Duration modifier**（N 秒間 true で発火）、短絡評価。
- 評価間隔: General タブの **scene switcher interval（既定 300ms）**。下げると反応↑だが **CPU 負荷↑**。マクロ個別の Custom check interval あり。
- 管理: 有効/無効・Pause/Resume・Grouping・**Run macro**（他マクロ呼出）・Import/Export。
- ⚠️ **レイテンシは最小 ~300ms 級**。スコア +1 のような即時性が要る操作を ASS のポーリング経由でやると体感が鈍い。即時操作は (A)/直接 API、状態連動の自動化を ASS、と棲み分ける。

### 3.3 連携で重要な Condition ✅

| Condition | 用途 | 連携 |
|-----------|------|------|
| **File** | ファイルの存在/内容を監視（テキスト/正規表現マッチ）。内容を変数に取込可 | **(D)**: `out/` 監視 |
| **WebSocket** | 受信 WS メッセージを条件化（正規表現フィルタ可） | (B') |
| **HTTP**（インバウンド） | 外部から ASS へ HTTP を送りマクロ起動 | (C') ⚠️ ポート/パス/認証は **TBD** |
| **Variable** | 変数の値で分岐（等値/数値/空/変化検知） | 全般 |
| **Scene / Recording / Streaming / Studio Mode** | OBS 状態 | 自動化 |
| **Window / Process** | ウィンドウ・プロセス監視（旧 Window Title 系の後継） | ゲーム検出 |
| **Run** | 外部プログラム実行 → 終了コード/標準出力を条件化 | (B) |

### 3.4 連携で重要な Action ✅

| Action | 用途 | 連携 |
|--------|------|------|
| **Scene Switch** | シーン切替（シーン名に `${var}` 展開可） | **(C/D 帰結)** |
| **Scene Item Visibility** | ソース表示/非表示 | 自動化 |
| **HTTP** | 外部 HTTP API を叩く（GET/POST） | (B) ⚠️ §3.6 制約 |
| **Run** | 外部プログラム/スクリプト実行（`curl` 等）。**標準出力を変数に取込可** | **(B) 推奨** |
| **WebSocket** | WS メッセージ送信（Connections 経由・リモート OBS / 汎用 WS） | (A') |
| **File** | ファイル書込 | 連携 |
| **Variable Operations** | 変数の set/append/数値演算/正規表現抽出/JSON 抽出 | 全般 |
| **Recording / Streaming** | 録画/配信制御 | 自動化 |

### 3.5 Variables ✅
- 参照記法は **`${変数名}`**（`%var%` ではない）。テキストフィールド全般・シーン名・数値欄で展開可。
- 保存挙動: Don't save / Save / Set default の 3 モード。
- 外部データの取込経路: ① **Run action の標準出力 → 変数**（最実用）② File condition ③ WebSocket 受信 ④ obs-websocket vendor request（§4.6）。
- 抽出: 正規表現キャプチャ・JSON 抽出（`EXTRACT_JSON`/`QUERY_JSON`）。⚠️ 組込 JSON 抽出は**1 階層・配列非対応**。ネストは複数回適用 or Run で前処理。

### 3.6 ⚠️ HTTP action の重要な制約（(B) の設計に直結）✅
ASS の **HTTP action** には強い制約がある:
- **GET/POST のみ**。POST は **data フィールドのみ**設定可。
- **カスタムヘッダ（Content-Type 等）の指定 UI が確認できない**（**TBD**／無い可能性が高い）。
- **レスポンスは破棄される**（応答 JSON を変数に取り込めない）。

→ **結論: Autona の `/api/v1/`（JSON ボディ・`Content-Type: application/json` 必須・応答に `revision`）を叩くなら、
HTTP action ではなく Run action + `curl` を推奨**。ヘッダ・任意メソッド・応答取込（標準出力→`${var}`）がすべて可能。

```text
# ASS の Run action 例（スコア +1）。引数に ${var} 展開可
curl -X POST -H "Content-Type: application/json" \
  -d "{\"delta\":1}" \
  http://127.0.0.1:5000/api/v1/scoreboards/1/teams/1/score:adjust
```

### 3.7 Connections（リモート接続）✅
- ASS は **obs-websocket 5.0+** で別 OBS / WS サーバーへ接続できる（"Connections" 設定: 名前・アドレス:ポート・パスワード・自動再接続・Test connection）。
- 用途: 別 PC の OBS のシーン操作、汎用 WS サーバーへの送信。同一 OBS の操作には不要（直接操作するため）。

---

## 4. Autona との連携設計（操作 API の利用方法）

> 各経路を Autona の既存資産（`/api/v1/`・`out/`・既存信頼境界）に対応づけて整理する。

### 4.1 経路(A): Autona → OBS（obs-websocket クライアント）
- Autona が obsws-python 等で OBS（:4455）に接続し、`SetCurrentProgramScene` / `SetSceneItemEnabled` /
  `SetInputSettings`（overlay URL 差替）/ 配信・録画制御を発行する。
- **ユースケース**: 「大会ロード時に試合シーンへ」「board 切替に合わせて overlay URL を差替」「セット確定で勝者紹介シーンへ」。
- **設計の起点**: Autona 側に **OBS 接続設定（host/port/password）と OBS 操作モジュール**を新設する必要がある（新規依存・新規スレッド）。→ ADR 事項（§6）。
- ⚠️ 既存の「`out/` + ブラウザソース」連携は維持。(A) は**それと併存**する能動制御。

### 4.2 経路(B): ASS → Autona（`/api/v1/` を叩く）★コード変更ほぼ不要
- ASS の **Run action + `curl`** で Autona の既存 `/api/v1/` を叩く（§3.6）。**Autona 側は実装済み API をそのまま使える**。
- **ユースケース**: OBS 側の自動化トリガ（ゲームウィンドウ検出・特定シーン遷移・ホットキー）から、Autona の
  スコアリセット・swap・set 情報設定などを呼ぶ。
- ⚠️ 破壊的操作は `/api/v1/` の **confirm 必須**（[external-control-api.md](../../.claude/design/external-control-api.md) §7.2）。`curl` のボディに `"confirm":true` を入れる。
- ⚠️ レイテンシ ~300ms 級・応答は Run の標準出力で受ける（HTTP action では応答破棄）。

### 4.3 経路(C): Autona/外部 → ASS（`CallVendorRequest`）★ASS 起動の王道
- obs-websocket クライアント（= 経路(A)と同じ接続）から `CallVendorRequest` を投げて ASS マクロ/変数を起動する。✅
  - `vendorName = "AdvancedSceneSwitcher"`
  - `requestType = "AdvancedSceneSwitcherRunMacro"`（マクロ実行・変数同時セット可）
  - `requestType = "AdvancedSceneSwitcherSetVariables"`（変数のみ更新）
  - `requestType = "AdvancedSceneSwitcherMessage"` + `requestData.message`（ASS の WebSocket condition で受けて起動）
- **ユースケース**: 「Autona のセット確定 → ASS の変数に勝者名を注入 → ASS が `${winner}` でシーン/テロップ切替」。
  Autona は**シーンの知識を持たず**、ASS 側にシーン構成を寄せられる（関心の分離）。
- これは「外部アプリ → OBS の obs-websocket → ASS マクロ → ASS が OBS をローカル操作」という**言語非依存の堅い経路**。

### 4.4 経路(D): ASS が `out/` を監視（File condition）★最小構成
- ASS の **File condition** で Autona が常時書き出す `out/program_state.json` 等を監視し、内容変化/値で Scene Switch を発火。
- **ユースケース**: 「`out/` のスコアが best_of 到達 → 勝者シーンへ」を**Autona も OBS も無改造**で実現。
- ⚠️ File condition の値抽出は正規表現/JSON（1 階層）。複雑な判定は Run で前処理。ポーリング間隔 ~300ms。

### 4.5 推奨の使い分け（格ゲー大会配信の文脈）

| やりたいこと | 推奨経路 | 理由 |
|-------------|---------|------|
| ボタンで即スコア +1 | 既存 Companion → `/api/v1/`（直接） | 即時性。ASS のポーリングを挟まない |
| 状態連動でシーン自動切替 | **(D)** File / **(C)** Vendor → ASS の Scene Switch | シーン構成を ASS に寄せる。Autona 無改造（(D)）or 最小改造（(C)） |
| Autona から能動的に OBS を操作 | **(A)** obs-websocket | overlay URL 差替・録画開始等、ASS を介さず直接 |
| OBS 自動化から Autona を操作 | **(B)** ASS Run + curl → `/api/v1/` | 既存 API をそのまま再利用 |

**初手の最小コスト案**: まず **(D) File condition + (B) Run+curl** から始めれば、**Autona 本体のコード変更ゼロ〜最小**で
双方向連携の検証ができる。能動制御（(A)/(C)）は OBS 接続モジュールの新設が要るので ADR 後に着手。

---

## 5. 既存資産との整合チェック

| 既存資産 | 整合・注意 |
|---------|-----------|
| `/api/v1/`（実装済み） | (B) からそのまま叩ける。confirm・冪等キー・202 等の規約は不変で利用 |
| `out/` ファイル群 | (D) の入力源。出力契約は壊さない（既存ブラウザソースと共有） |
| 信頼境界（LAN・無認証 API / OBS は password） | obs-websocket も LAN 前提。Autona API は無認証のまま、OBS は password 推奨で非対称な点に留意 |
| StateManager 単一経路 | (B)/(C)/(D) いずれも最終的に `/api/v1/`→WebServerActions→StateManager を通す。**バイパス禁止**は維持 |
| シングルトン初期化順 / スレッド境界 | (A)/(C) で OBS クライアントを足す場合、起動位置とスレッドに注意（agent-pitfalls #2、StateManager ロック尊重） |

---

## 6. 要決定事項（ADR 候補）

本書では決め切らず、以下を ADR（例: ADR-002 "OBS/ASS 操作 API の採用と境界"）で確定する。

1. **方針転換の明文化**: 「OBS-websocket を使わない」(current-architecture.md / ROADMAP) → 「採用する」への更新可否と範囲。
2. **どの経路を採るか**: (A)〜(D) のどれを初回スコープにするか。推奨は **(D)+(B) を初手、(A)/(C) を次段**。
3. **(A)/(C) を採る場合の実装境界**: OBS 接続モジュールの配置（新シングルトン？）・新規依存（obsws-python）追加可否・スレッド設計・設定 UI（host/port/password の保存先 = SettingsManager）。
4. **責務の所在**: 「シーン構成の知識」を Autona に持たせるか、ASS（マクロ）側に寄せるか（推奨: ASS 側に寄せ、Autona は状態/イベントの提供に徹する）。
5. **セキュリティ**: OBS の password 管理（CLAUDE.md 機密ルール: 値を出力・コミットしない）。

---

## 7. 要確認事項（実機検証）

- **obs-websocket**: 実機の `obsVersion` / `obsWebSocketVersion`（`GetVersion`）。`SetInputSettings` のブラウザソース対象キー名。`StopRecord` 戻り値。TLS の要否（平文前提）。
- **ASS**: ① **HTTP condition（インバウンド）** の待受ポート/パス/ボディ/認証の具体仕様、② **HTTP action のカスタムヘッダ可否**（無ければ (B) は Run+curl 確定）、③ 利用 OBS 版と ASS リリースの整合（最小 OBS 31.1.1）、④ `CallVendorRequest` の各 requestType の引数詳細。
- いずれも `python main.py` 起動 + OBS/ASS 実機で 1 回ずつ叩いて確認する（自動テストは作らない方針）。

---

## 8. 参照（一次情報源）

**obs-websocket:**
- プロトコル仕様（正本）: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
- README/接続情報: https://github.com/obsproject/obs-websocket
- Python: https://github.com/aatikturk/obsws-python ／ https://github.com/IRLToolkit/simpleobsws
- JS: https://github.com/obs-websocket-community-projects/obs-websocket-js
- Companion OBS module: https://github.com/bitfocus/companion-module-obs-studio

**Advanced Scene Switcher:**
- GitHub: https://github.com/WarmUpTill/SceneSwitcher
- Wiki（canonical）: https://github.com/WarmUpTill/SceneSwitcher/wiki （Http action / Variables / Websockets 各ページ）
- Releases（1.34.2 / 2026-05-30 等）: https://github.com/WarmUpTill/SceneSwitcher/releases
- OBS フォーラム: https://obsproject.com/forum/resources/advanced-scene-switcher.395/
