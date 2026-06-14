# 実装チケット: External Control API — 危険操作（ガード付き）

> **チケット3/3。** 破壊的操作（confirm 必須）と重い操作（202 受付 + 実行中フラグ）を実装する。
> [readonly](implement-external-control-api-readonly.md) / [safe-ops](implement-external-control-api-safe-ops.md)
> 完了が前提。**最もリスクが高いので最後**。

- 正本仕様: [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)（§7 危険操作・§2.8 非同期 202・§2.10 スレッド安全・§14.2/§14.3 スパイク）
- 決定: [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)（D6/D7）

## ゴール

破壊的・重い操作を**ガード付き**で `/api/v1/` に追加。すべて既存 `WebServerActions` へ委譲。
- **破壊的操作**: `confirm:true` 必須。
- **重い操作**: `202 Accepted` で受付、**実行中フラグ（Lock 保護）で多重起動を 409**。完了は WS で確認。
- 全エンドポイントで**書き込み操作ログ（INFO）**。

## 前提・制約（厳守）

- 既存ルート・Socket.IO を変更しない（regression 禁止）。状態変更は StateManager 単一経路（D4）。
- `{n}` は `validate_scoreboard()` で検証。board1/2 用操作を board3 に投げたら明示拒否（spec §2.12）。
- 「配信中」自動ブロックは**実装しない**（Autona に配信状態判定が無い・D7/C-2）。confirm のみで担保。
- 動作確認は手動のみ。

## エンドポイント

### 破壊的操作（confirm 必須・spec §7.2）

| メソッド・パス | body | 委譲 | sync |
|---------------|------|------|:---:|
| `POST /api/v1/scoreboards/<int:n>:clear` | `{"confirm": true}` | `clear_all` | ✓ |
| `POST /api/v1/scoreboards/<int:n>:reset-players` | `{"confirm": true}` | `reset_players` | ✓ |
| `POST /api/v1/scoreboards/<int:n>:reset-match` | `{"confirm": true}` | `reset_match` | ✓ |

- `confirm` が無い/false → **`409 CONFIRMATION_REQUIRED`、状態を変更しない**。
- sync（`@gui_thread_sync`）のため revision 正確。

### 盤面上書き（confirm 推奨・spec §7.1）

| メソッド・パス | body | 委譲 | sync |
|---------------|------|------|:---:|
| `POST /api/v1/scoreboards/<int:n>:load-set` | `{"setId":"...","noMains":false}` | `load_set` | ✓ |
| `POST /api/v1/scoreboards/<int:n>:pull-stream` | なし | `pull_stream_set` | ✕ |
| `POST /api/v1/scoreboards/<int:n>:pull-user` | なし | `pull_user_set` | ✕ |

### 重い操作（202 受付 + 実行中フラグ・spec §2.8/§7.3）

| メソッド・パス | body | 委譲 | 受付前判定 |
|---------------|------|------|-----------|
| `POST /api/v1/tournament` | `{"url":"https://www.start.gg/.../event/..."}` | `load_tournament` | url 空=大会クリア |
| `POST /api/v1/game` | `{"codename":"ssbu","modsActive":false}` | `set_game` | 未知 codename→`404 NOT_FOUND`（受付前に判定可なら） |
| `POST /api/v1/bracket:refresh` | なし | `update_bracket` | provider 不在→`409 NOT_READY` |

### 設定変更（同期・spec §7.3）

| メソッド・パス | body | 委譲 | 備考 |
|---------------|------|------|------|
| `POST /api/v1/ui/theme` | `{"value": true}` | `set_light_mode` | 重くない＝同期 200。OBS 見た目に波及・ログ必須。SettingsManager にも書く（§2.4a） |

## 実装メモ（spec §14.2/§14.3 由来・重要）

1. **実行中フラグ（§2.8/§2.10）**: `tournament`/`game`/`bracket` の操作キーごとにフラグを持ち、
   **`threading.Lock` で保護**（複数リクエストスレッド前提）。実行中に同種要求 → `409 CONFLICT`。
   202 で受け付けた処理が**完了/失敗するまでフラグを保持**（完了検知の仕組みも実装）。
2. **`set_game` の 202 化が最大の難所（§14.3）**: `set_game` は `@gui_thread_sync` ＋
   `LoadGameAssets(async_mode=False)` で**同期ブロック**する。202 にするには
   **`@gui_thread_sync` を介さずに起動**する必要がある。
   - 方針: 既存の非同期ロード経路（`AutonaGameAssetManager` の `LoadGameAssets(async_mode=True)` /
     `onLoad`・`onLoadAssets` シグナル）を辿り、API 層は起動だけして 202 を返す。完了はそのシグナル or
     WS の `revision` 進行で検知してフラグ解除。**実装前にこの経路を確定すること。**
   - codename 検証は `AutonaGameAssetManager.instance.games` のキー存在で受付前に行い、未知は `404 NOT_FOUND`。
3. **`update_bracket` の堅牢化（§14.2）**: 既存 `update_bracket` は
   `.provider`（null 未チェック）・`GetTournamentPhases()[0]`（IndexError）・`.get("groups")[0]`（空で TypeError/IndexError）
   と**複数の未ガード点**がある。API 層で受付前に provider 有無を確認（`NOT_READY`）し、実行は try/except で
   囲んで失敗をログ。**既存 Action 自体は変更せず、API 層でラップ**する（regression 回避）。重く・同期 Start.gg のため 202。
4. **`tournament` の url 検証**: 既存 `load_tournament` は start.gg 以外でも `SettingsManager.Set` してしまう緩さがある。
   API 層で start.gg URL 形式を軽くチェックし、不正は `INVALID_ARGUMENT`（既存挙動は変えずラップで弾く）。
5. **同期 Start.gg のタイムアウト（§14.2）**: 重い操作は 202 後に失敗しうる（レスポンスで返せない）。
   失敗はログ（§2.7）に残し、`revision` が進まないことで間接検知（spec §2.8）。

## エラーマップ

- `confirm` 欠如 → `CONFIRMATION_REQUIRED`(409)。
- `n` 無効 / board3 への不適合操作 → `INVALID_ARGUMENT`(400)（or `NOT_READY`）。
- 未知 codename → `NOT_FOUND`(404)。provider 不在（bracket）→ `NOT_READY`(409)。
- 重い操作の多重起動 → `CONFLICT`(409)。
- start.gg URL 不正 → `INVALID_ARGUMENT`(400)。
- 受付後の Start.gg 失敗 → ログのみ（202 済）。
- 未処理例外 → `INTERNAL`(500)。

## 触るファイル

- 変更: `src/AutonaApiV1.py`（エンドポイント + 実行中フラグ + Lock）
- 参照のみ: `AutonaWebServerActions.py` / `AutonaGameAssetManager.py`（非同期ロード経路の確認）/ `AutonaTournamentDataProvider.py`
- 既存 Action・既存ルートは変更しない（API 層でラップ）。

## 受け入れ条件（手動）

- [ ] `POST /api/v1/scoreboards/1:clear`（body `{}`）が `409 CONFIRMATION_REQUIRED` で**状態不変**。
- [ ] `{"confirm":true}` で clear 実行・OBS/Web に反映。
- [ ] `:load-set {"setId":"..."}` で盤面が対象セットに更新。
- [ ] `POST /api/v1/tournament {"url":...}` が **202** で返り、ロード完了が WS（`revision` 進行）で観測できる。
- [ ] ロード中に再度 `POST /api/v1/tournament` → `409 CONFLICT`。
- [ ] `POST /api/v1/game {"codename":"unknown"}` → `404 NOT_FOUND`。正常 codename は 202 でアセット再ロード。
- [ ] `POST /api/v1/bracket:refresh`（大会未ロード）→ `409 NOT_READY`。
- [ ] `POST /api/v1/ui/theme {"value":true}` でテーマ切替（OBS 含む）。
- [ ] 全書き込み操作が操作ログ（INFO）に出る。
- [ ] 既存ルート・Web UI・OBS・ホットキーが従来通り（regression なし）。

## スコープ外

- X / Bluesky 投稿（外部送信・機密・初回除外）。
- 「配信中」自動ガード（D7・将来）。
- crew（board3）専用操作。
- 既存 Action のリファクタ（堅牢化は API 層のラップで対応）。

## 依存

- [readonly](implement-external-control-api-readonly.md) / [safe-ops](implement-external-control-api-safe-ops.md) 完了後に着手。
</content>
