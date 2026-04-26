"""Validation helpers for hotkey configuration.

Two checks are exposed:
- find_app_duplicates: detect when two Autona actions resolve to the same
  shortcut. The settings UI uses this to BLOCK saving (duplicates within
  the app would silently break one of the two actions).
- os_warning_for: detect collisions with shortcuts that the OS, common
  desktop apps, or OBS Studio commonly bind. The settings UI uses this
  to WARN — the user is allowed to keep the binding because their
  environment may not actually use the conflicting shortcut.

Both helpers normalise tokens case-insensitively and order-independently
so that "Ctrl+Shift+F1" and "shift+ctrl+f1" compare equal.

See Design.md §6.4.2 / §8 T-17.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional


def normalize_shortcut(shortcut: str) -> str:
    """Canonical form: modifiers sorted alphabetically, lower-case, '+'-joined."""
    if not shortcut:
        return ""
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    if not parts:
        return ""
    # Stable order: modifiers first (sorted), then key. Treat the last
    # token as the key so we don't sort it into the modifier slot.
    mods = sorted(p for p in parts[:-1])
    key = parts[-1]
    return "+".join([*mods, key])


def find_app_duplicates(hotkeys: Dict[str, str]) -> Dict[str, List[str]]:
    """Group hotkey assignments by canonical shortcut.

    Returns a {canonical_shortcut: [action_key, ...]} dict containing only
    entries with 2+ actions sharing the same shortcut. Empty / falsy
    shortcuts are ignored (a blank slot is not a collision).
    """
    by_shortcut: Dict[str, List[str]] = {}
    for action_key, shortcut in (hotkeys or {}).items():
        # `hotkeys_enabled` lives in the same SettingsManager subtree but
        # is a bool, not a shortcut string — skip non-string values.
        if not isinstance(shortcut, str):
            continue
        canon = normalize_shortcut(shortcut)
        if not canon:
            continue
        by_shortcut.setdefault(canon, []).append(action_key)
    return {k: v for k, v in by_shortcut.items() if len(v) >= 2}


# Shortcuts the OS or virtually any GUI app binds. Matching one of these
# usually means the user can never trigger the Autona action because the
# OS / focused app eats the keypress first. Listed in canonical form
# (modifiers sorted alphabetically, lower-case).
_OS_RESERVED = {
    # Edit / clipboard primitives
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+a",
    # Common file / window operations
    "ctrl+s", "ctrl+o", "ctrl+n", "ctrl+w", "ctrl+p", "ctrl+f",
    "ctrl+r", "ctrl+t", "ctrl+q",
    "alt+f4", "alt+tab", "alt+shift+tab",
    "ctrl+tab", "ctrl+shift+tab",
    # Windows shell
    "ctrl+esc", "ctrl+shift+esc",
}

# OBS Studio's out-of-the-box defaults are mostly *unbound*, but operators
# routinely bind plain F-keys to recording / scene switches. Flag bare
# F1..F12 (no modifiers) as potential OBS-collision territory, plus the
# Studio-Mode / Replay-Buffer style chord.
_OBS_LIKELY = {
    *(f"f{n}" for n in range(1, 13)),
    "ctrl+shift+r",  # frequently bound to "Restart record"
}


def os_warning_for(shortcut: str) -> Optional[str]:
    """Return a human-readable warning if `shortcut` clashes with a
    well-known OS/OBS binding, else None.

    The caller decides what to do — typically: show a non-blocking
    dialog and save anyway (Design.md §6.4.2 / §8 T-17).
    """
    canon = normalize_shortcut(shortcut)
    if not canon:
        return None
    if canon in _OS_RESERVED:
        return (
            f"'{shortcut}' は OS や一般的なアプリ "
            "(コピー・保存・ウィンドウ操作など) で広く予約されています。"
            "他アプリにフォーカスがあると Autona に届きません。"
        )
    if canon in _OBS_LIKELY:
        return (
            f"'{shortcut}' は OBS Studio で録画開始 / シーン切替などに "
            "割り当てられがちです。配信中の意図しない動作を避けるため、"
            "OBS 側のホットキー設定と重複しないか確認してください。"
        )
    return None


def collision_summary(action_keys: Iterable[str]) -> str:
    """Render a short comma-separated list for error dialogs."""
    return ", ".join(sorted(set(action_keys)))
