"""Player-prop models: anytime scorer, score-or-assist, shots on target.

Rate hierarchy per player (best available wins):
1. data/player_overrides.yaml — hand-researched numbers (current club-season
   SOT per match, de-margined anytime-scorer prop odds, confirmed lineups).
2. StatsBomb tournament history (2018-2024) — per-appearance rates, shrunk
   toward a positional prior to avoid small-sample extremes.
3. Pure prior — a typical starter who gets asked about in a prop market.

Scorer probability ties into the match's goal model: a player owning share s
of a team expected to score lam goals scores with p = 1 - exp(-s * lam).
SOT props scale the player's own rate by the team's SOT tempo for this match.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from pathlib import Path

import pandas as pd
import yaml

from models.counts import p_ge

ROOT = Path(__file__).resolve().parent.parent.parent
OVERRIDES_PATH = ROOT / "data" / "player_overrides.yaml"

# Shrinkage priors: an average prop-market player
PRIOR_GOAL_SHARE = 0.12      # share of own team's goals
PRIOR_INVOLVEMENT = 0.20     # share of goals scored-or-assisted
PRIOR_SOT_RATE = 0.80        # shots on target per appearance
SHRINK_APPS = 6.0            # appearances of prior weight
SOT_ALPHA = 0.10             # mild overdispersion for player SOT
TOURNAMENT_TEAM_SOT = 3.70   # measured tournament mean (counts aggregates)


@dataclass
class PlayerRates:
    goal_share: float
    involvement_share: float
    sot_per_match: float
    source: str


class PlayerModel:
    def __init__(self, statsbomb_players: pd.DataFrame):
        self._history = (
            statsbomb_players.groupby("player")
            .agg(apps=("match_id", "count"), goals=("goals", "sum"),
                 assists=("assists", "sum"), sot=("sot", "sum"),
                 team=("team", "last"))
        )
        # team goals across each player's tournaments, for share computation
        team_goals = statsbomb_players.groupby("team")["goals"].sum()
        team_apps = statsbomb_players.groupby(["team", "match_id"]).size().groupby("team").size()
        self._team_goals_per_match = (team_goals / team_apps).to_dict()
        self._overrides = (
            yaml.safe_load(OVERRIDES_PATH.read_text()) or {}
            if OVERRIDES_PATH.exists() else {}
        )

    def rates(self, player: str) -> PlayerRates:
        override = self._overrides.get(player, {})
        history = self._history.loc[player] if player in self._history.index else None

        if history is not None and history["apps"] > 0:
            apps = float(history["apps"])
            team_lam = self._team_goals_per_match.get(history["team"], 1.2)
            expected_team_goals = max(team_lam * apps, 1e-6)
            goal_share = ((history["goals"] + PRIOR_GOAL_SHARE * SHRINK_APPS)
                          / (expected_team_goals + SHRINK_APPS))
            involvement = ((history["goals"] + history["assists"]
                            + PRIOR_INVOLVEMENT * SHRINK_APPS)
                           / (expected_team_goals + SHRINK_APPS))
            sot_rate = ((history["sot"] + PRIOR_SOT_RATE * SHRINK_APPS)
                        / (apps + SHRINK_APPS))
            source = f"statsbomb({int(apps)} apps)"
        else:
            goal_share, involvement, sot_rate = (
                PRIOR_GOAL_SHARE, PRIOR_INVOLVEMENT, PRIOR_SOT_RATE)
            source = "prior"

        return PlayerRates(
            goal_share=override.get("goal_share", goal_share),
            involvement_share=override.get("involvement_share", involvement),
            sot_per_match=override.get("sot_per_match", sot_rate),
            source="override" if override else source,
        )

    # --- market families ----------------------------------------------------

    def p_scores(self, player: str, team_lam: float,
                 prop_prob: float | None = None) -> float:
        """Anytime scorer. If a de-margined prop-market probability is
        supplied it wins outright (markets beat models)."""
        if prop_prob is not None:
            return prop_prob
        return 1 - exp(-self.rates(player).goal_share * team_lam)

    def p_scores_or_assists(self, player: str, team_lam: float) -> float:
        return 1 - exp(-self.rates(player).involvement_share * team_lam)

    def p_sot_ge(self, player: str, threshold: int, team_sot_mean: float,
                 period: str = "match", h2_share: float = 0.57) -> float:
        rate = self.rates(player).sot_per_match
        rate *= team_sot_mean / TOURNAMENT_TEAM_SOT  # match tempo adjustment
        if period == "2h":
            rate *= h2_share
        return p_ge(rate, SOT_ALPHA, threshold)
