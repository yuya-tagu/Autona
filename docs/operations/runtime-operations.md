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
3. ゲーム選択（Title メニュー）→ アセットの非同期ロード 📄
4. Start.gg 大会をロード（Startgg メニュー → Set tournament）📄
5. OBS 側でブラウザソース/テキストソースが `http://localhost:{port}/...` と `out/` を参照していることを確認 📄
   - ⚠️ 既定ポート（README/FEATURES は `5000`）と Web UI ルートの実値は要確認 → [../product/current-features.md](../product/current-features.md)
6. Web リモート操作を使う場合、操作端末から該当 URL に接続できることを確認 📄

> ⚠️ **起動順の厳密な依存**（Web サーバーが上がる前に OBS を繋ぐと何が起きるか等）は `TBD`。
> 確認方法: [src/Autona.py](../../src/Autona.py) の起動シーケンスと [src/AutonaWebServer.py](../../src/AutonaWebServer.py) の起動位置を追う。

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
- [ ] **外部制御 API / Web を使う場合: 同一 LAN 内に限定し、ルータ/PC ファイアウォールで
      ポート（既定 5000）を外部公開しない**（下記「Network & security」参照）✅

## During-event operation

- スコア操作の経路は **3 つ**: デスクトップ UI / グローバルホットキー / Web UI（リモート）✅
- いずれの経路も最終的に StateManager を更新し、`out/` + WebSocket で配信に反映 ✅
- 本番中は**レイアウト保存状態を不用意に変えない**（STATE_VERSION 復元に影響）✅

## Network & security（外部制御 API / Web リモート）

> ⚠️ **重要（ADR-001 D5 のリスク受容の前提条件）**。外部制御 API（`/api/v1/`・計画中）と既存 Web 制御面は
> **認証なし**で動く。Web サーバーは `0.0.0.0`（全 NIC）にバインドし、CORS 全許可。
> = **同一 LAN に到達できる端末は誰でも全操作（スコア変更・リセット・大会ロード等の破壊的操作含む）を叩ける。**

現場で必ず守ること:

- **同一 LAN 内のみで運用する。** インターネットや信頼できないネットワークに**ポート（既定 5000）を公開しない**。
  - ルータのポートフォワード／DMZ に Autona 機を載せない
  - PC のファイアウォールで 5000 番への外部アクセスを遮断（LAN 内のみ許可）
- 外部公開が必要な事情がある場合は、**VPN 経由 or リバースプロキシ＋認証**を別途用意する
  （Autona 本体は認証を持たない）。
- 操作端末（Companion / Stream Deck / ブラウザ）は**信頼できる端末のみ**を LAN に入れる。
- 万一の誤操作・不正操作の追跡用に、外部制御 API は**書き込み操作をログに記録**する（spec §2.7）。
  ただし無認証のため記録できるのは送信元 IP まで。

> この制約が許容できない運用（LAN 外配信オペ・不特定端末の混在等）が出たら、
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) の
> 認証/バインド方針（D5）を Revisit する。

## OBS connection handling

- OBS-websocket は使わない。連携は**ブラウザソース + テキスト/JSON ファイル参照**のみ 📄
- Autona 再起動時、ブラウザソースは再読込が必要な場合がある（`TBD`: 自動再接続の有無）
- `out/program_state.json` と個別テキストファイルは状態変更ごとに更新される ✅

## Scoreboard operation

- スコア +1/−1、Swap、各種 Reset、Clear All は HTTP/WS ルートとしても提供 ✅
- クルーバトル時はストック増減・アクティブ選手切替の専用操作あり ✅
- ⚠️ 「自動セット更新」「ステージストライクからのスコア自動制御」等の設定は General タブにあるが、
  ステージストライク自体は削除済みのため**該当設定の現状は要確認**（`TBD`）

## Recovery procedure

⚠️ 大半が `TBD`。現状で言えること:

- Autona がクラッシュした場合、再起動でレイアウト/状態は保存値から復元される（STATE_VERSION）✅
- `TBD`: セッション途中の状態（スコア等）がどこまで永続化され、再起動で戻るか → 確認要
- `TBD`: 自動バックアップ/スナップショットは**現状なし**（ROADMAP に提案あり、未実装）📄
- Web UI 側が状態を見失った場合、ページ再読込で `program_state` 全受信からやり直せる ✅

## Known failure modes

- **Start.gg クエリ timeout**（20 秒）: 重いクエリ/不安定回線で空データが返る → null-safe で表示は壊れない設計 ✅
- **複数 GitHub アカウントによる push 失敗**: 開発側の話（運用には無関係）✅（CLAUDE.md）
- **`__pycache__` のスタール**: 開発時のみ。`sys.dont_write_bytecode=True` で対策済 ✅
- `TBD`: ポート競合（5000 が他プロセス使用中）時の挙動
- `TBD`: OBS とのファイル/URL 参照ずれ時の症状切り分け手順

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
