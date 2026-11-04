# Git ワークフロー（詳細）

CLAUDE.md の Git 要点の詳細版。**push / PR 作成 / マージ / ブランチ削除を行うときに参照する**。
要点（ローカル `main` 上で作業、push 時のみ種別ブランチ、PR 経由、日本語、明示依頼時のみ）は CLAUDE.md 側に記載。

## 作業ブランチ運用

「ローカル `main` 上で作業し、push 時にだけリモート種別ブランチを切って PR 経由でマージ」する運用とする。

- リモート: `origin` = `https://github.com/yuya-tagu/Autona.git`（HTTPS）。**`origin` 以外の remote を追加しない**
- **`upstream` remote を復活させない**（2026-08-02 削除）。gh CLI は `upstream` という名前の remote を `origin` より優先して base リポジトリに選ぶため、clone 由来で残っていた `joaorb64/TournamentStreamHelper` に対して `gh pr create` / `gh pr view` が向いてしまっていた（PR #170 作成時に発覚。当リポジトリは fork ではないため GitHub 側で拒否され実害なし）。保険として `gh repo set-default yuya-tagu/Autona` を設定済み
- **作業はローカル `main` 上で行う**。worktree もローカル作業ブランチも作らない（例外: サブエージェントの worktree 並列（`isolation: "worktree"`。`claude/...` ブランチは還流後に worktree ごと削除）→ [orchestration.md](orchestration.md)）。ブランチはリモート push 時にだけ切る
- push 時は `git push origin main:<type>/<topic>`（種別ごとに `feat` / `fix` / `docs` / `chore` / `ci`）でリモート側にだけブランチを作る。**リモート `main` への直接 push はしない**（必ず PR 経由でマージ）
- ブランチ命名（`<topic>` 部分）は内容を表す短い英語（例: `feat/bracket-fix`、`docs/operational-docs-foundation`、`fix/thumbnail-perf`）。timestamp や連番は不要（`git log --all` / `gh pr list` で時系列は追える）
- ビルド成果物（`Autona.exe`、`web/build/`、`dist/` 等）はこのローカル `main` 上で運用する

## 王道フロー

1. **最新化**: `git fetch origin && git reset --hard origin/main` でローカル `main` を最新にする
2. **作業**: ローカル `main` 上で通常通り編集・コミットする（メッセージは日本語）
3. **PR**: 「PRを出して」と依頼されたとき、Claude は コミット（ローカル `main`）→ `git push origin main:<type>/<topic>` で push → **`gh pr create` で PR 作成** → 自動マージ予約（手順 4）→ PR URL と変更概要の報告 までを実行する。タイトル・本文は日本語（本文の先頭にリスク区分、Summary / Test Plan 構成）。未実施の検証項目は Test Plan に未チェックで明記する。コミット・push・PR 作成は明示的に依頼されたときのみ
4. **自動マージを予約して報告する（「通常」「軽微」。2026-11-04 Hivee と同じ運用に変更）**: PR を作ったら続けて `gh pr merge <PR番号> --merge --auto` を予約し、CI の完了を待たずに **PR URL・リスク区分・自動マージ予約済み**を報告して次の作業に移る。「PR を出して」の依頼が予約の指示を兼ねる
   - 予約後は `gh pr checks <PR番号> --watch --fail-fast` をバックグラウンドで走らせ、完了を受けて次へ進む: 緑なら `gh pr view <PR番号> --json state` で `MERGED` を確かめて手順 5 へ、落ちたらローカル `main` で直して同じブランチへ push する（予約は残るので、緑になった時点でマージされる）
   - オーナーが止めたいときは `gh pr merge <PR番号> --disable-auto` で予約を外す
   - **リスク区分「高」は予約しない**。CI の緑を待って「マージ可」として PR URL・CI 結果・リスク区分を報告し、オーナーが差分を見てからマージする（オーナーに「マージして」と言われたら Claude が `gh pr merge <PR番号> --merge --auto` を実行する）
   - main はブランチ保護で CI の 3 ジョブ（`Python tests` / `Web UI build and tests` / `Browser E2E (headless app)`）の成功が必須（管理者も迂回不可）。**マージ方式は merge commit**（`--merge`）。squash にするとローカル `main` のコミットが origin の祖先でなくなり、手順 5 の `merge --ff-only` 同期が成り立たないため Hivee と変えている
   - head ブランチはマージ時に自動削除される（リポジトリ設定 `delete_branch_on_merge`）
   - `gh` の操作は push と同じアカウント切替が必要（`CLAUDE.local.md` 参照）
5. **マージ後の同期**: Claude がマージした（予約がマージされた）ときは同じセッションが続けてローカル `main` を再同期する。**先に `git status` で未コミット変更の有無を確認**し、無ければ `git fetch origin && git reset --hard origin/main`、有れば（別セッションの作業中ファイル等）作業ツリーに触れない `git fetch origin --prune && git merge --ff-only origin/main` を使う（2026-07-29 の消失事故を受けた手順）

### リスク区分

PR 本文の先頭に書き、マージ前の確認の深さを決める。迷ったら上の区分にする。

| 区分 | 対象 | マージ前の確認 |
|---|---|---|
| **高** | 自動更新（アップデータ）・state スキーマ／保存データ形式・`bootstrap.py` の生成順・ビルド／リリース設定（`dependencies/autona.spec`・`build_release.yml`・`scripts/` のリリース系）・依存の追加更新 | review エージェント + オーナーが差分を確認してマージ |
| **通常** | 機能追加・UI・ロジック修正 | review エージェントの自己レビュー + CI → 自動マージ |
| **軽微** | docs・文言・ハーネス設定 | 目視 + CI → 自動マージ |

実機確認（`python main.py`）はオーナーが行うため、自動マージ後に実機で不具合が出たら前進修正の PR で直す。

### 注意

- ローカル作業ブランチを切らないため、未 push のコミットはローカル `main` に積まれる。push（手順3）→ PR・自動マージ予約（手順4）→ 再同期（手順5）まで一連で回す
- 自動マージ予約中の PR があるあいだに次のトピックをローカル `main` へコミットしてよいが、次の push は前の PR が `MERGED` になってから行う（同じ `main` から切るブランチに前の PR のコミットが混ざるため。スタック PR は作らない）
- 1 つの作業ツリーで作業するため、複数トピックを並行させない。前トピックを push・PR 化してから次に着手する（未コミット変更を残さない。`git status` で確認 → コミットまたは `git stash`）

## コミット・push の実務

### マシン固有の認証手順

複数 GitHub アカウントを使う環境での push / fetch / ブランチ削除の認証手順は、gitignore 済みの `CLAUDE.local.md` を参照する（リポジトリには含まれない。オーナーのローカル運用向け）。

### 複数行コミットメッセージは bash 構文で渡す

Bash ツールは PowerShell ではなく bash で動く。`@'...'@`（PowerShell
here-string）を使うとメッセージ先頭に `@` が混入するなど壊れる。複数行は
heredoc（`git commit -F - <<'EOF' ... EOF`）を使うか、メッセージをファイルに
書いて `git commit -F <file>` で渡し、コミット後に一時ファイルを削除する。

## ブランチ削除フロー

ローカルには作業ブランチを作らないため、掃除対象はリモート（`origin`）の種別ブランチのみ。
`origin/main` と上流フォークの `upstream/*` は**絶対に削除しない**。削除は破壊的なので
**main にマージ済みのものだけ**を対象とし、未マージブランチは中身を確認してから個別に判断する。

**単発（マージ直後）**: 2026-11-04 以降はマージ時に GitHub が head ブランチを自動削除するので、`git fetch origin --prune` だけでよい。自動削除されずに残ったときだけ `git push origin --delete <type>/<topic>` で消す（認証手順は `CLAUDE.local.md` 参照）。

**一括（定期掃除）**: マージ済みブランチがリモートに溜まったときに棚卸ししてから消す。

```bash
# 1. 分類して確認（削除はまだしない）
git fetch origin --prune
git branch -r --merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -vE 'origin/main$|origin/HEAD'   # 安全に削除可
git branch -r --no-merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -v 'origin/HEAD'                 # 未マージ → 中身を確認

# 2. マージ済みのみ一括削除（upstream/* は対象外）
branches=$(git branch -r --merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -vE 'origin/main$|origin/HEAD' | sed 's#^origin/##')
git push origin --delete $branches   # 認証手順は CLAUDE.local.md 参照
git fetch origin --prune
```

- 未マージブランチ（`--no-merged`）は `git log origin/main..origin/<branch>` で中身を確認し、
  陳腐化していれば個別に削除、活きていれば残す。一括削除には混ぜない
