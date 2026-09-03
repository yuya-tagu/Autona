"""Sponsor crest lookup: which ``sponsor_logo`` files a team prefix resolves to.

Moved here from ``src/Helpers/AutonaSponsorHelper.py`` in headless core Step 5
(instalment 5). It was already Qt-free, and since the player instalment its
only caller has been ``PlayerService.PublishImages`` -- so leaving it under
``src/Helpers`` meant core reached *up* into ``src`` for a write it owns. That
reverse dependency is the one thing the layering rule
(``.claude/rules/state-and-webserver.md``) asks to keep short, and this module
was the whole of it outside ``StateSchema``'s two state models.

It writes ``score.{N}.team.{T}.player.{M}.sponsor_logo`` /
``.sponsor_logos.{I}`` -- keys ``StateSchema.SCORE_PLAYER_KEYS`` already
catalogs -- so it belongs in the same layer as the rest of the player subtree's
writer.

Import side effects: none (agent-pitfalls #1).
"""
import os
import re

from .StateManager import StateManager


class AutonaSponsorHelper:

    def ExportValidSponsors(team: str, path: str):
        # Coalesce up to ~5 sponsor Set/Unset calls into a single state save.
        with StateManager.SaveBlock():
            sponsor_logo = None
            cleaned_sponsor = re.sub(r"[,/|;:<>\\?*]", "_", team)
            if os.path.exists(f"./user_data/sponsor_logo/{cleaned_sponsor.upper()}.png"):
                sponsor_logo = f"./user_data/sponsor_logo/{cleaned_sponsor.upper()}.png"
                StateManager.Unset(f"{path}.sponsor_logos")
            else:
                split_sponsor = re.split(r"[,/|;: <>\\?*]", team)
                i: int = 0
                for sponsor in split_sponsor:
                    if os.path.exists(f"./user_data/sponsor_logo/{sponsor.upper()}.png"):
                        if sponsor_logo is None:
                            sponsor_logo = f"./user_data/sponsor_logo/{sponsor.upper()}.png"
                        StateManager.Set(f"{path}.sponsor_logos.{i+1}", f"./user_data/sponsor_logo/{sponsor.upper()}.png")
                        i =+ 1

            if sponsor_logo is not None:
                StateManager.Set(f"{path}.sponsor_logo", sponsor_logo)
            else:
                StateManager.Unset(f"{path}.sponsor_logo")
                StateManager.Unset(f"{path}.sponsor_logos")
