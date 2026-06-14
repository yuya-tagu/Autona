# 実装チケット: External Control API — 安全操作

> **チケット2/3。** confirm 不要の安全な書き込み操作を実装する。
> [readonly チケット](implement-external-control-api-readonly.md)の共通基盤（エンベロープ・エラー処理・
> n 検証・操作ログ）完了が前提。

- 正本仕様: [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)（§6 安全操作・§2.7 ログ・§2.9 冪等・§14.2 失敗モード）
- 決定: [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)（D6/D7/D8）

## ゴール

confirm 不要の操作を `/api/v1/` に追加。すべて**既存 `WebServerActions` へ委譲**し、構造化レスポンスで返す。
**書き込み操作ログ（INFO）必須**。

## 前提・制約（厳守）

- 既存ルート・Socket.IO を変更しない（regression 禁止）。
- 状態変更は**必ず `WebServerActions` → Qt Signal → `StateManager`**（D4・バイパス禁止）。新規に state を直接書かない。
- `{n}` は readonly チケットの `validate_scoreboard()` で `{1,2,3}` 検証（§2.12）。
- `int()` パース（delta/bestOf 等）は API 層で検証し、失敗は `INVALID_ARGUMENT`（§14.2）。
- 動作確認は手動のみ。

## エンドポイント（spec §6）

| メソッド・パス | body | 委譲 Action | sync | 備考 |
|---------------|------|------------|:---:|------|
| `POST /api/v1/scoreboards/<int:n>/teams/<int:t>/score:adjust` | `{"delta": 1}` | `team_scoreup`/`team_scoredown`（符号で分岐、\|delta\| 回 emit） | ✕ | 冪等キー対応（§2.9） |
| `POST /api/v1/scoreboards/<int:n>/score:reset` | なし | `reset_scores` | ✓ | revision 正確 |
| `POST /api/v1/scoreboards/<int:n>:swap-teams` | なし | `swap_teams` | ✕ | |
| `POST /api/v1/scoreboards/<int:n>/teams/<int:t>/color` | `{"color":"#FF0000"}` | `team_color` | ✕ | 色形式検証（`#` + hex）→ 不正は `INVALID_ARGUMENT` |
| `PATCH /api/v1/scoreboards/<int:n>/set` | §下記 | `set_route` | ✓ | 与えたキーのみ反映 |

### `PATCH .../set` の body（部分更新・spec §6.5）
```json
{ "bestOf": 5, "phase": "Top 8", "match": "Winners Finals",
  "players": 1, "characters": 1, "losers": { "team": 1, "value": true } }
```
- 既存 `set_route` はクエリ引数（`best-of`/`phase`/`match`/`players`/`characters`/`losers`/`team`）。
  本 API は JSON を同 Action にマップする。`bestOf`/`players`/`characters` は数値検証。

## 実装メモ（spec §14.2 由来）

- **`t`（team）検証**: 1|2 以外は `INVALID_ARGUMENT`。
- **revision のタイミング（§14.1a）**: `score:adjust`/`swap-teams`/`color` は `@gui_thread_sync` でない＝
  emit 即 return で **revision がまだ上がっていないことがある**。レスポンスの `revision` は
  「応答生成時点の `StateManager.revision`」をベストエフォートで返し、**確定確認はクライアントが WS で行う**前提。
  `score:reset`/`set`（PATCH）は `@gui_thread_sync` のため revision 正確。
- **冪等性（§2.9・任意/SHOULD）**: `score:adjust` は `Idempotency-Key` ヘッダがあれば重複適用しない
  （プロセス内短命キャッシュ・`threading.Lock` 保護・§2.10）。キー無しは従来通り。
  キャッシュ件数/TTL は実装裁量（例: 直近 256 件 / 60 秒）。
- **操作ログ**: 全エンドポイントで書き込みログ（INFO）を出す（readonly チケットのロガー使用）。

## エラーマップ

- `n` 無効 → `INVALID_ARGUMENT`(400)。`t` 無効 → `INVALID_ARGUMENT`。
- `delta`/`bestOf`/`players`/`characters` が数値でない → `INVALID_ARGUMENT`。
- `color` 形式不正 → `INVALID_ARGUMENT`。
- 未処理例外 → `INTERNAL`(500)。

## 触るファイル

- 変更: `src/AutonaApiV1.py`（エンドポイント追加）
- 参照のみ: `AutonaWebServerActions.py`
- 既存 `AutonaWebServer.py` / 既存ルートは触らない。

## 受け入れ条件（手動）

- [ ] `POST /api/v1/scoreboards/1/teams/1/score:adjust {"delta":1}` で board1 のスコア +1、OBS/Web に反映。
- [ ] `{"delta":-2}` で -2。`delta` 非数値で 400。
- [ ] 同操作を同一 `Idempotency-Key` で連投しても 1 回しか加算されない。
- [ ] `:swap-teams` で左右入替。`score:reset` でスコアのみ 0。
- [ ] `PATCH .../set {"bestOf":5,"match":"WF"}` が反映。`bestOf:"x"` で 400。
- [ ] `teams/3/...` や `scoreboards/4/...` が 400。
- [ ] 書き込み操作が操作ログ（INFO）に出る。
- [ ] 既存ルート・Web UI・OBS が従来通り（regression なし）。

## スコープ外

- confirm/重い操作（guarded-ops チケット）。
- board3（crew）専用操作（初回除外・spec §1.1）。
- `set_team_data`（選手データ更新・payload スキーマ未確定・spec §12）。

## 依存

- [readonly チケット](implement-external-control-api-readonly.md)（共通基盤）完了後に着手。
</content>
