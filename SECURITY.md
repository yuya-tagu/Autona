# Security Policy / セキュリティポリシー

## 日本語

### 脆弱性の報告方法

脆弱性を見つけた場合は、**公開 Issue には書かず**、GitHub の
[Private vulnerability reporting](https://github.com/yuya-tagu/Autona/security/advisories/new)
（リポジトリの Security タブ → "Report a vulnerability"）から非公開で報告してください。再現手順・影響範囲・対象バージョンを添えていただけると助かります。日本語・英語どちらでも構いません。

個人開発のため対応時期の保証はできませんが、確認でき次第ご連絡します。修正版の公開後、ご希望があれば報告者として記載します。

### 対象範囲

- ローカル Web サーバー（`/api/v1`、Designer API、Figma 取込のペアリング）
- アップデーター（更新確認・ダウンロード・適用）

対象外: 利用者の端末側の設定ミス、外部サービス（start.gg 等）側の脆弱性、同梱する第三者ソフトウェア本体の脆弱性（該当プロジェクトへ報告してください）。

### サポートするバージョン

**最新リリースのみ**サポートします。古いバージョンへの修正の取り込みは行いません。最新版は [Autona-releases](https://github.com/yuya-tagu/Autona-releases) で入手できます。

## English

### Reporting a vulnerability

If you find a vulnerability, **do not open a public issue.** Report it privately via GitHub's
[Private vulnerability reporting](https://github.com/yuya-tagu/Autona/security/advisories/new)
(Security tab of the repository, then "Report a vulnerability"). Reproduction steps, impact, and the affected version are appreciated. Japanese or English is fine.

This is a personal project, so response times cannot be guaranteed, but we will reply as soon as we have confirmed the report. After a fix is released, we will credit you if you wish.

### Scope

- The local web server (`/api/v1`, the Designer API, and the Figma import pairing)
- The updater (update check, download, and apply)

Out of scope: misconfiguration on the user's own machine, vulnerabilities in external services (start.gg, etc.), and vulnerabilities in bundled third-party software itself (please report those upstream).

### Supported versions

Only the **latest release** is supported. Fixes are not backported. Get the latest version from [Autona-releases](https://github.com/yuya-tagu/Autona-releases).
