# Autona

[日本語](README.md) | **English**

A streaming workbench for fighting-game community tournaments, built in Japan. It supports small teams from pre-event preparation through the live broadcast to post-event posting.

Autona brings scoreboard management, broadcast overlays, thumbnail/result image generation, and social posting into one tool, so a TO who also runs the stream can do it with a small crew. It handles 1v1, team battles, and crew battles. The UI is Japanese-first and also available in English.

> The Japanese [README.md](README.md) is the primary version. This English README is maintained alongside it.

<!-- TODO(owner): add screenshots here, e.g. docs/images/scoreboard.png, overlay.png, web-remote.png -->

## What you can do

- **Scoreboard management** — manage teams, players, scores, and character selection in real time
- **Broadcast overlays** — animated HTML/CSS/JS overlays you load as a browser source in OBS and similar tools; you can also build and share your own layouts ([layout/README.md](layout/README.md))
- **Start.gg integration** — fetch brackets, set lists, and entrants automatically
- **Web remote control** — operate the scoreboard from a browser, so duties can be split between operators
- **Multiple formats** — switch between 1v1, team, and crew battle (up to 9v9 with stock tracking) without restarting
- **Stats displays** — head-to-head, last sets, and tournament history
- **Local player database** — keep tags, nationalities, and mains locally
- **Hotkeys** — global keyboard shortcuts for score changes
- **Thumbnail / result images** — generate tournament thumbnails and result images, with one-click posting to X
- **Japanese and English UI**

## Download

Get the latest Windows build from the releases repository:

**https://github.com/yuya-tagu/Autona-releases**

Unzip it and run `Autona.exe`. Please read [TERMS.md](TERMS.md) (notes on using the distributed binary) first.

> **Game images (characters / stages) are not bundled**, because their copyrights belong to the rights holders. Fetch them via the in-app asset guide and place them under `user_data/games/`. See [user_data/games/README.md](user_data/games/README.md).

## Development

Prerequisites: Python 3.12 and Node.js 20+.

```bash
# Python dependencies
pip install -r dependencies/requirements.txt

# Web UI dependencies
cd web && npm ci

# Run in development mode
python main.py
```

Tech stack: PySide6 (via qtpy), React 19 / TypeScript / Redux Toolkit / MUI, Flask-SocketIO, PyInstaller.

Tests: `QT_QPA_PLATFORM=offscreen python -m pytest` (Python) and `cd web && npm test` (jest).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow. Design documents live in [.claude/design/Design.md](.claude/design/Design.md) and [docs/](docs/README.md) (mostly in Japanese).

## Community and support

- Discord: https://discord.gg/yAFAmhaUqn
- Bugs and feature requests: [GitHub Issues](https://github.com/yuya-tagu/Autona/issues) (English is welcome)
- Security issues: see [SECURITY.md](SECURITY.md)
- Please follow the [Code of Conduct](CODE_OF_CONDUCT.md)

## License

Autona is released under the [MIT License](LICENSE) (© 2026 taguyan and Autona contributors). The copyright notice of the upstream [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper) (MIT License) is preserved in [LICENSE](LICENSE).

For notes on the distributed binary see [TERMS.md](TERMS.md); for the use of the "Autona" name and icon see [TRADEMARK.md](TRADEMARK.md).

Third-party licenses are collected in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) and `licenses/`, and asset sources (fonts, flag images, etc.) in [ATTRIBUTIONS.md](ATTRIBUTIONS.md). Qt / PySide6 is used under LGPL v3 via dynamic linking. The MIT License does not extend to these third-party files. In particular, `layout/include/gsap.min.js` (GSAP) is bundled under the GSAP Standard "No Charge" License ([licenses/GSAP-LICENSE.txt](licenses/GSAP-LICENSE.txt)), which is not an open-source license; if you fork or redistribute, follow its terms (no charging for it, keep copyright notices, etc.).
