"""Characterization tests for core.DictHelper (deep_get / deep_set / deep_unset / deep_clone).

Target: known-smells S-01, S-09 — StateManager relies on these primitives.
Refactoring PlayerWidget or StateManager without breaking these is critical.
"""
import pytest

from src.core.DictHelper import (
    deep_clone, deep_get, deep_set, deep_unset)


# ── deep_get ──────────────────────────────────────────────

class TestDeepGet:
    def test_single_key(self):
        assert deep_get({"a": 1}, "a") == 1

    def test_nested_key(self):
        assert deep_get({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing_key_returns_default(self):
        assert deep_get({"a": 1}, "b") is None

    def test_missing_key_custom_default(self):
        assert deep_get({"a": 1}, "b", "fallback") == "fallback"

    def test_missing_nested_returns_default(self):
        assert deep_get({"a": {"b": 1}}, "a.c.d") is None

    def test_empty_dict(self):
        assert deep_get({}, "a") is None

    def test_value_is_dict(self):
        d = {"a": {"b": 1}}
        assert deep_get(d, "a") == {"b": 1}

    def test_value_is_list(self):
        d = {"a": [1, 2, 3]}
        assert deep_get(d, "a") == [1, 2, 3]

    def test_value_is_none(self):
        # deep_get treats None values as default because of isinstance(d, dict) check
        d = {"a": None}
        assert deep_get(d, "a") is None

    def test_numeric_string_key(self):
        """StateManager uses numeric strings as keys: score.1.team.2"""
        d = {"score": {"1": {"team": {"2": {"score": 5}}}}}
        assert deep_get(d, "score.1.team.2.score") == 5


# ── deep_set ──────────────────────────────────────────────

class TestDeepSet:
    def test_single_key(self):
        d = {}
        deep_set(d, "a", 1)
        assert d == {"a": 1}

    def test_nested_key_creates_intermediates(self):
        d = {}
        deep_set(d, "a.b.c", 3)
        assert d == {"a": {"b": {"c": 3}}}

    def test_overwrite_existing(self):
        d = {"a": {"b": 1}}
        deep_set(d, "a.b", 2)
        assert d["a"]["b"] == 2

    def test_set_preserves_siblings(self):
        d = {"a": {"b": 1, "c": 2}}
        deep_set(d, "a.b", 99)
        assert d == {"a": {"b": 99, "c": 2}}

    def test_set_none_value(self):
        d = {}
        deep_set(d, "a.b", None)
        assert d == {"a": {"b": None}}

    def test_set_dict_value(self):
        d = {}
        deep_set(d, "game", {"name": "ssbu", "codename": "ssbu"})
        assert d["game"]["name"] == "ssbu"

    def test_scoreboard_pattern(self):
        """Reproduce actual StateManager usage: score.{N}.team.{T}.player.{M}.name"""
        d = {}
        deep_set(d, "score.1.team.1.player.1.name", "Tagu")
        deep_set(d, "score.1.team.2.player.1.name", "Opponent")
        assert deep_get(d, "score.1.team.1.player.1.name") == "Tagu"
        assert deep_get(d, "score.1.team.2.player.1.name") == "Opponent"


# ── deep_unset ────────────────────────────────────────────

class TestDeepUnset:
    def test_remove_leaf(self):
        d = {"a": {"b": 1, "c": 2}}
        deep_unset(d, "a.b")
        assert d == {"a": {"c": 2}}

    def test_remove_nonexistent_is_noop(self):
        d = {"a": 1}
        deep_unset(d, "b")
        assert d == {"a": 1}

    def test_remove_nested_nonexistent_is_noop(self):
        d = {"a": {"b": 1}}
        deep_unset(d, "a.c")
        assert d == {"a": {"b": 1}}

    def test_remove_leaves_empty_parent(self):
        """deep_unset does NOT prune empty parent dicts — verify this behavior."""
        d = {"a": {"b": 1}}
        deep_unset(d, "a.b")
        assert d == {"a": {}}

    def test_unset_score_pattern(self):
        """Reproduce: StateManager.Unset('score.1.team.1.player')"""
        d = {"score": {"1": {"team": {"1": {"player": {}, "score": 0}}}}}
        deep_unset(d, "score.1.team.1.player")
        assert "player" not in d["score"]["1"]["team"]["1"]
        assert d["score"]["1"]["team"]["1"]["score"] == 0


# ── deep_clone ────────────────────────────────────────────

class TestDeepClone:
    def test_clone_is_equal(self):
        d = {"a": {"b": [1, 2, 3]}, "c": "text"}
        cloned = deep_clone(d)
        assert cloned == d

    def test_clone_is_independent(self):
        d = {"a": {"b": 1}}
        cloned = deep_clone(d)
        cloned["a"]["b"] = 99
        assert d["a"]["b"] == 1

    def test_clone_empty(self):
        assert deep_clone({}) == {}

    def test_clone_with_numeric_keys(self):
        """StateManager state uses string keys, but deep_clone should handle int keys too."""
        d = {"score": {1: {"team": {1: "data"}}}}
        cloned = deep_clone(d)
        assert cloned == d


# ── Integration: Set → Get → Unset cycle ─────────────────

class TestSetGetUnsetCycle:
    """Reproduce the actual StateManager usage pattern."""

    def test_full_cycle(self):
        state = {}
        # Initialize scoreboard (AutonaScoreboardWidget.__init__)
        deep_set(state, "score.1", {})
        deep_set(state, "score.1.last_sets.1", {})
        deep_set(state, "score.1.last_sets.2", {})

        # Set player data
        deep_set(state, "score.1.team.1.player.1.name", "Player1")
        deep_set(state, "score.1.team.1.player.1.seed", 3)
        deep_set(state, "score.1.team.2.player.1.name", "Player2")

        # Read back
        assert deep_get(state, "score.1.team.1.player.1.name") == "Player1"
        assert deep_get(state, "score.1.team.1.player.1.seed") == 3

        # Set score
        deep_set(state, "score.1.team.1.score", 2)
        deep_set(state, "score.1.team.2.score", 1)

        # Clear player (AutonaScoreboardWidget.CommandClearAll)
        deep_unset(state, "score.1.team.1.player")
        assert deep_get(state, "score.1.team.1.player") is None
        # Score should remain
        assert deep_get(state, "score.1.team.1.score") == 2

    def test_game_state_cycle(self):
        state = {}
        # AutonaGameAssetManager sets game
        deep_set(state, "game", {
            "name": "Super Smash Bros. Ultimate",
            "codename": "ssbu",
            "has_stages": True,
            "defaults": {"players_per_team": 1, "characters_per_player": 1}
        })

        assert deep_get(state, "game.name") == "Super Smash Bros. Ultimate"
        assert deep_get(state, "game.defaults.players_per_team") == 1

        # Overwrite game (game change)
        deep_set(state, "game", {"name": "Street Fighter 6", "codename": "sf6"})
        assert deep_get(state, "game.name") == "Street Fighter 6"
        # Old nested keys are gone
        assert deep_get(state, "game.defaults") is None
