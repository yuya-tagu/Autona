"""Forward-only migrations of the persisted state (ADR-005 S3b).

The persisted state lives in ``user_data/state.json`` (``StatePersistence``),
which records the version of the state it holds next to the state, never
inside it: a ``state_version`` key in the tree would reach the Socket.IO
payloads and ``out/program_state.json``, both external contracts.

Version 0 is every state written before the store existed -- the
``out/program_state.json`` a pre-S3b build read back at launch. Version 1 is
the first one ``state.json`` is written with.

Rules for adding a version:

- **Forward only.** ``Migrate`` takes a state from any version up to
  ``CURRENT_VERSION`` and never back. A store from a newer build is set
  aside by ``StatePersistence`` (``state.json.v{N}.<time>.bak``), not read.
- **A step is frozen once it shipped.** It describes the difference between
  two versions, so it keeps its own copy of the names it touches instead of
  reading today's ``StateSchema`` constants: those move on, and the step
  would silently start doing something else to old data. (This module also
  must not import ``StateSchema``: ``StateManager`` reaches it through
  ``StatePersistence``, and ``StateSchema`` imports ``StateManager``.)
- **A step accepts anything its source version could hold**, including the
  half-written leftovers of a crash, and does no I/O.
- **A step is idempotent: data already in its target shape passes through
  unchanged.** ``out/program_state.json`` carries no version, so
  ``StatePersistence`` always migrates it from 0 -- but a build with the
  store writes it in that build's shape. After a crash (program_state.json
  newer than state.json), after a downgrade, and on every launch that falls
  back to it, every step runs on data that may already be past it.
- Bump ``CURRENT_VERSION`` and add the step to ``_STEPS`` together.

What is *not* a migration: per-launch cleanup of data a build at the current
version could leave behind itself. ``player_editor`` is the example -- a v1
session that crashed with the Qt editor dialog open (it is gone since ADR-005
S4n) wrote it into a v1 ``state.json``, which no v0 -> v1 step will ever see
again. That cleanup stays in
``StateSchema.SeedPanelState()``, which runs at every launch.

Import side effects: none (agent-pitfalls #1).
"""
from . import CommentaryModel

CURRENT_VERSION = 2

# ── v0 -> v1 ──────────────────────────────────────────────────────────────
#
# The three ad-hoc migrations that predate this module, frozen as they stood
# in StateSchema.SeedPanelState() when it was written (2026-10-25). They are
# still run there at every launch as well; see that function for why the
# double application is harmless.

# StateSchema.RETIRED_ROOTS: subtrees of removed features.
_V1_RETIRED_ROOTS = (
    "bracket",        # legacy full bracket, replaced by top8.* (2026-07)
    "player_list",    # retired PLAYERS roster editor
    "player_editor",  # the editor dialog's scratch subtree
)

# StateSchema.STALE_KEYS: the pre-配信設定 stream title / destination that
# lived on the EVENT tab. (root, key) pairs.
_V1_STALE_KEYS = (
    ("tournamentInfo", "streamTitleEdit"),
    ("tournamentInfo", "streamUrl"),
)

# The commentary subtree, normalised to the slot-fixed 12-caster shape
# (custom_textbox -> note, legacy role / icon shapes folded in).
_V1_COMMENTARY_ROOT = "commentary"


def _v0_to_v1(state):
    for root in _V1_RETIRED_ROOTS:
        state.pop(root, None)

    for root, key in _V1_STALE_KEYS:
        parent = state.get(root)
        if isinstance(parent, dict):
            parent.pop(key, None)

    # Only when present: a state without the subtree is seeded at launch by
    # SeedPanelState(), and seeding is not this step's business.
    if _V1_COMMENTARY_ROOT in state:
        # Uses the live normaliser rather than a frozen copy because its
        # output *is* the v1 commentary shape. If that shape ever changes
        # incompatibly, freeze a copy of today's NormalizeState here first and
        # put the change in a v1 -> v2 step.
        state[_V1_COMMENTARY_ROOT] = CommentaryModel.NormalizeState(
            state[_V1_COMMENTARY_ROOT])
    return state


# ── v1 -> v2 ──────────────────────────────────────────────────────────────
#
# The tournament result's rows (ADR-005 S4q). The Qt panel wrote the singles'
# and the teams' to one key, whichever its layout used; the teams' have their
# own now, so a switch of layout leaves the other's alone. A row of the teams'
# is one with `members`, which the singles' never had.

_V2_SINGLES_ROOT = "result_players"
_V2_TEAMS_ROOT = "result_teams"
# The panel had sixteen rows of a team, of which no layout showed more than
# eight.
_V2_TEAM_SLOTS = tuple(str(slot) for slot in range(1, 9))


def _v1_to_v2(state):
    rows = state.get(_V2_SINGLES_ROOT)
    if not isinstance(rows, dict):
        return state
    teams = {slot: row for slot, row in rows.items()
             if isinstance(row, dict) and "members" in row}
    if not teams:
        return state
    # What is there already is newer than what the panel left beside it (a
    # state of this version, read back from out/program_state.json).
    if not state.get(_V2_TEAMS_ROOT):
        state[_V2_TEAMS_ROOT] = {slot: teams[slot] for slot in _V2_TEAM_SLOTS
                                 if slot in teams}
    for slot in teams:
        del rows[slot]
    if not rows:
        del state[_V2_SINGLES_ROOT]
    return state


# from-version -> the step that produces from-version + 1.
_STEPS = {
    0: _v0_to_v1,
    1: _v1_to_v2,
}


def Migrate(state, version):
    """Bring `state`, stored at `version`, up to ``CURRENT_VERSION``.

    Returns the migrated state. `state` may be modified in place (it is the
    caller's freshly parsed copy), so use the return value. A state already
    at ``CURRENT_VERSION`` comes back untouched.

    Raises TypeError / ValueError for a version this build cannot migrate
    from: not an int, negative, or newer than ``CURRENT_VERSION``.
    """
    if not isinstance(state, dict):
        raise TypeError(f"state must be a dict, not {type(state).__name__}")
    if not isinstance(version, int) or isinstance(version, bool):
        raise TypeError(f"state version must be an int, not {version!r}")
    if version < 0 or version > CURRENT_VERSION:
        raise ValueError(
            f"cannot migrate a state from version {version} "
            f"(this build knows 0..{CURRENT_VERSION})")
    for current in range(version, CURRENT_VERSION):
        state = _STEPS[current](state)
    return state
