# Architecture

A one-page map of Autona for new contributors. For depth, see [More documentation](#more-documentation) (mostly Japanese).

## What it is

Autona is a streaming workbench for fighting-game community tournaments: scoreboard control, Start.gg integration, stats, thumbnails and results, and OBS browser-source overlays (layouts). It is a Windows desktop app (Python / PySide6) whose entire UI is a React web app rendered in one embedded web view; the same web app is also reachable from a browser over the local network.

## Big picture

```
 OBS browser sources            browser / other PC            Qt shell (desktop only)
 (layout/*, file:// or http)    (React web UI, API clients)   QMainWindow + one web view
          │                              │                            │ loads /web/app/<page>
          │ HTTP + WebSocket             │ HTTP + WebSocket           │
          ▼                              ▼                            ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ Web server (Flask + Socket.IO, src/AutonaWebServer.py)                   │
   │  - serves web/build (React), layout/ (overlays), out/ (exported state)   │
   │  - REST API /api/v1/ and legacy routes → src/AutonaWebOperations.py      │
   │  - pushes state deltas over WebSocket                                    │
   └───────────────────────────────┬──────────────────────────────────────────┘
                                   ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ core (src/core/, Qt-free)                                                │
   │  StateManager (nested dict, single source of truth) + domain services    │
   │  persists to user_data/state.json, exports to out/                       │
   └───────────────────────────────┬──────────────────────────────────────────┘
                                   ▼
        Start.gg provider, game assets, player DB, settings (user_data/)
```

- **The UI is React.** There are no Qt widget pages; the Qt shell (`src/Autona.py`, `src/AutonaWebPanelHost.py`) is a window around one shared web view, plus a few native dialogs, global hotkeys and the updater.
- **core is Qt-free.** Nothing under `src/core/` imports Qt (pinned by `tests/test_core_qt_free.py`). Qt signals are provided by the adapter `src/StateManagerBridge.py`. That is what lets `python main.py --headless` run state + web server without a window.
- **State is one nested dict** owned by `src/core/StateManager.py`, addressed with dot-separated string key paths (`score.1.team.1.score`). Writes go through `StateManager.Set` (in practice via `AutonaActions` or a core service). Every change produces a delta that is broadcast over WebSocket and written to `out/` for overlays. Key names are defined in `src/core/StateSchema.py`.

## Where to start reading

| File | Why |
|------|-----|
| `src/bootstrap.py` | Composition root and the authoritative singleton creation order (`bootstrap_core()` for every shell, `bootstrap_gui()` for the desktop). |
| `src/core/StateManager.py` | Global state, delta computation, persistence hooks. |
| `src/core/StateSchema.py` | Key definitions (`KEY_CATALOG`) and seed values. |
| `src/AutonaWebServer.py`, `src/AutonaWebOperations.py` | HTTP / WebSocket surface and where each route lands. |
| `web/src/AppShell/` | The React shell: navigation, header, footer; pages live in sibling `web/src/*Page/` folders. |
| `layout/` | OBS overlay templates (HTML/CSS/JS reading the exported state); see `layout/README.md`. |

Do not import `src/Autona.py` from tests or scripts: importing it *is* the startup sequence (logger, `bootstrap_core()`, `bootstrap_gui()`, window definition). Only `main.py` does. Every other `src` module must be import-side-effect free (see [pitfalls](docs/development/pitfalls.md) #1, pinned by `tests/test_import_side_effects.py`).

## Directory guide

| Path | Contents |
|------|----------|
| `main.py` | Entry point: single-instance gate, startup splash, then `from src import Autona`. |
| `src/` | Python application: Qt shell, web server, adapters, `bootstrap.py`, `headless.py`, `autona_splash.py`. |
| `src/core/` | Qt-free domain: state, settings, services (players, sets, stats, thumbnails, archive...). |
| `src/TournamentDataProvider/` | Tournament data sources (Start.gg and others). |
| `src/i18n/` | Qt translation sources for native dialogs and the updater (the UI's words are in `web/src/i18n/`). |
| `web/` | React / TypeScript / Redux Toolkit / MUI web UI (`web/src`), jest tests, Playwright e2e (`web/e2e`). Build output goes to `web/build`. |
| `layout/` | Overlay and image-render templates served to OBS; user-made ones go in `layout/custom/`. |
| `tests/` | pytest suite (`python -m pytest`). |
| `docs/` | Design docs, ADRs, operations and change records (Japanese). |
| `scripts/` | Release, translation and data-generation scripts. |
| `tools/` | Auxiliary tools (e.g. a Figma plugin for scoreboard design). |
| `packaging/` | The PyInstaller spec (`requirements.txt` is at the repo root). |

Runtime data is kept outside the source tree's tracked files: `user_data/` (settings, state, player DB) and `out/` (state export read by overlays).

## More documentation

These are written in Japanese; ask on Discord or in an Issue if something is unclear.

- [docs/architecture/Design.md](docs/architecture/Design.md): the source of truth for UI approach, data model, screens and components.
- [docs/development/pitfalls.md](docs/development/pitfalls.md): rules that came out of real crashes. Read before editing `src/` or `tests/`.
- [docs/README.md](docs/README.md): index of operations, change management and ADRs.
- [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md): test layers and the `autonaApp` fixture; read before writing tests.
