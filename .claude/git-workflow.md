# Git ワークフロー（詳細）

CLAUDE.md の Git 要点の詳細版。**push / PR 作成 / ブランチ削除を行うときに参照する**。
要点（ローカル `main` 上で作業、push 時のみ種別ブランチ、PR 経由、日本語、明示依頼時のみ）は CLAUDE.md 側に記載。

## 作業ブランチ運用

「ローカル `main` 上で作業し、push 時にだけリモート種別ブランチを切って PR 経由でマージ」する運用とする。

- リモート: `origin` = `https://github.com/yuya-tagu/Autona.git`（HTTPS, GitHub アカウント `yuya-tagu`）
- **作業はローカル `main` 上で行う**。worktree もローカル作業ブランチも作らない。ブランチはリモート push 時にだけ切る
- push 時は `git push origin main:<type>/<topic>`（種別ごとに `feat` / `fix` / `docs`）でリモート側にだけブランチを作る。**リモート `main` への直接 push はしない**（必ず PR 経由でマージ）
- ブランチ命名（`<topic>` 部分）は内容を表す短い英語（例: `feat/bracket-fix`、`docs/operational-docs-foundation`、`fix/thumbnail-perf`）。timestamp や連番は不要（`git log --all` / `gh pr list` で時系列は追える）
- ビルド成果物（`Autona.exe`、`web/build/`、`dist/` 等）はこのローカル `main` 上で運用する

## 王道フロー

1. **最新化**: `git fetch origin && git reset --hard origin/main` でローカル `main` を最新にする
2. **作業**: ローカル `main` 上で通常通り編集・コミットする（メッセージは日本語）
3. **PR**: 「PRを出して」と依頼されたとき、Claude は コミット（ローカル `main`）→ `git push origin main:<type>/<topic>` で push → **`gh pr create` で PR 作成** → PR URL と変更概要の報告 までを実行する（2026-07-29 変更。以前は `gh pr create` をユーザーが手動で行っていた）。`gh pr create` は push と同じ `yuya-tagu` への switch 中に実行し、終わったら復元する。タイトル・本文は日本語（Summary / Test Plan 構成）。未実施の検証項目は Test Plan に未チェックで明記する。コミット・push・PR 作成は明示的に依頼されたときのみ
4. **マージ後の同期**: PR マージ後はローカル `main` を再同期する。**先に `git status` で未コミット変更の有無を確認**し、無ければ `git fetch origin && git reset --hard origin/main`、有れば（別セッションの作業中ファイル等）作業ツリーに触れない `git fetch origin --prune && git merge --ff-only origin/main` を使う（2026-07-29 の消失事故を受けた手順）
5. **マージ後のブランチ削除**: GitHub 側の自動削除は無効なので、マージした種別ブランチをリモートから削除する（後述「ブランチ削除フロー」）。push と同じく `yuya-tagu` 認証が必要

### 注意

- ローカル作業ブランチを切らないため、未 push のコミットはローカル `main` に積まれる。push（手順3）→ PR → マージ → 再同期（手順4）→ ブランチ削除（手順5）まで一連で回す
- 1 つの作業ツリーで作業するため、複数トピックを並行させない。前トピックを push・PR 化してから次に着手する（未コミット変更を残さない。`git status` で確認 → コミットまたは `git stash`）

## コミット・push の実務（このマシン固有）

### 複数 GitHub アカウントによる push 失敗（`Repository not found`）

このマシンには gh アカウントが 2 つ登録されており、アクティブが
`yuyat-highfield`（HighField 用）になっていることがある。`yuya-tagu/Autona`
はそのアカウントに書き込み権限がないため、アクティブのまま push すると
`remote: Repository not found`（404）で失敗する。SSH 既定鍵・HTTPS
credential manager のいずれも `yuyat-highfield` として認証されるのが原因。

push はグローバル設定を恒久変更しない一回限りの手順で行う:

```bash
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin main:<type>/<topic>
gh auth switch --user yuyat-highfield   # 元のアクティブアカウントに復元
```

- `gh auth git-credential` はアクティブアカウントのトークンを返すため、
  先に `yuya-tagu` へ switch しておく
- 手順 2 行目の最初の `-c credential.helper=`（空）で既存ヘルパー一覧を
  リセットし、既定の "manager"（yuyat-highfield を返す）を無効化してから
  gh のヘルパーだけを使わせる
- push 後は必ず `yuyat-highfield` に戻す（ユーザーの他作業のアクティブ
  アカウントを変えたままにしない）
- トークンの値はコマンドにも出力にも一切出さない（機密ルール）

`fetch` も private リポのため同じ認証が必要。`git fetch origin && git reset --hard origin/main`
が `Repository not found` になる場合は、上記と同じく `yuya-tagu` へ switch し
`git -c credential.helper= -c credential.helper="!gh auth git-credential" fetch origin --prune`
を使う。

### 複数行コミットメッセージは bash 構文で渡す

Bash ツールは PowerShell ではなく bash で動く。`@'...'@`（PowerShell
here-string）を使うとメッセージ先頭に `@` が混入するなど壊れる。複数行は
heredoc（`git commit -F - <<'EOF' ... EOF`）を使うか、メッセージをファイルに
書いて `git commit -F <file>` で渡し、コミット後に一時ファイルを削除する。

## ブランチ削除フロー

ローカルには作業ブランチを作らないため、掃除対象はリモート（`origin`）の種別ブランチのみ。
`origin/main` と上流フォークの `upstream/*` は**絶対に削除しない**。削除は破壊的なので
**main にマージ済みのものだけ**を対象とし、未マージブランチは中身を確認してから個別に判断する。

**単発（マージ直後）**: PR をマージしたら、そのトピックブランチを削除する。

```bash
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin --delete <type>/<topic>
git fetch origin --prune
gh auth switch --user yuyat-highfield   # 元のアクティブアカウントに復元
```

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
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin --delete $branches
git fetch origin --prune
gh auth switch --user yuyat-highfield
```

- 未マージブランチ（`--no-merged`）は `git log origin/main..origin/<branch>` で中身を確認し、
  陳腐化していれば個別に削除、活きていれば残す。一括削除には混ぜない
- 削除後は必ず `yuyat-highfield` に戻す（push 手順と同じ理由）
