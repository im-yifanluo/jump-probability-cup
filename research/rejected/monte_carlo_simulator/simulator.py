"""Monte Carlo match simulator — game-state-aware pricing for derived markets.

Why this exists: the analytic pricer treats each market from a static
team-strength snapshot, so it can't represent game state — a favourite that
goes ahead sits back and cedes second-half shots/corners to the chasing
underdog (the failure mode behind the Haiti vs SCO miss). A simulator gets
this for free: it plays each match minute-by-minute, tracks the score, and
generates every derived stat as a *correlated* outcome conditioned on how the
game is flowing. All market probabilities then come from counting outcomes
over many simulated matches, so they are mutually consistent by construction.

Calibration philosophy (unchanged from the analytic model):
- Goal rates are anchored to the market odds (the ScorelineModel's lambdas),
  so simulated 1X2 / totals reproduce the betting market.
- Per-stat full-match means come from CountPricer (club regressions + the
  measured international discounts + referee factor).
- Game state only *redistributes* events within a match (who gets the shots
  when), calibrated to the StatsBomb half-split data; it is ~mean-preserving,
  so full-match marginals still match the analytic model.

Run cost: ~20k sims x 95 minutes, fully vectorized over sims — well under a
second per match.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from probcup.models.counts import CountPricer
from probcup.models.goals import ScorelineModel

MINUTES = 95            # regulation + typical stoppage
HALFTIME = 48           # minute boundary (1st half = minutes 1..48)
H2_GOAL_SHARE = 0.54

# Game-state effect on possession stats (shots, corners): a team's attacking
# rate scales by exp(-BETA * own_lead), then is normalized to average 1 across
# sims each minute so the effect REDISTRIBUTES possession (leading team cedes to
# the chaser) without shifting full-match marginals. Calibrated so a favourite
# cedes 2nd-half shots/corners at the StatsBomb-measured rate.
POSSESSION_BETA = 0.20
GOAL_CHASE_BETA = 0.08   # smaller for goals — keeps 1X2/totals anchored to odds


def _mean_preserving(mult: np.ndarray) -> np.ndarray:
    """Scale a per-sim multiplier so its cross-sim average is exactly 1, so it
    only redistributes events (correlates with game state) without changing the
    expected per-minute rate (full-match marginals stay anchored)."""
    m = mult.mean()
    return mult / m if m > 0 else mult


@dataclass
class SimResult:
    """Per-simulation arrays (length n_sims) for every quantity a market needs."""
    n: int
    home_goals: np.ndarray
    away_goals: np.ndarray
    # per side: dict period -> array, for each stat
    stats: dict          # stats[stat][side][period] -> int array
    penalty: np.ndarray  # bool
    red: np.ndarray      # bool
    first_scorer: np.ndarray      # 0 none, 1 home, 2 away
    first_scorer_2h: np.ndarray

    # --- helpers for pricing ---
    def goals(self, side: str, period: str) -> np.ndarray:
        return self.stats["goals"][side][period]

    def count(self, stat: str, side: str, period: str) -> np.ndarray:
        return self.stats[stat][side][period]


class MatchSimulator:
    STATS = ("goals", "sot", "corners", "cards", "fouls", "offsides")
    POSSESSION_STATS = ("sot", "corners")  # subject to the leading-team-cedes effect

    def __init__(self, goals: ScorelineModel, counts: CountPricer,
                 n_sims: int = 20000, seed: int = 0):
        self.goals = goals
        self.counts = counts
        self.n = n_sims
        self.rng = np.random.default_rng(seed)

        # Per-minute base rates (full-match mean / MINUTES), with the 2nd-half
        # goal share folded in via a per-minute weight.
        self.lam = {
            "home": {"goals": goals.lam_home}, "away": {"goals": goals.lam_away},
        }
        for side in ("home", "away"):
            for stat in self.STATS[1:]:
                self.lam[side][stat] = counts.mean(stat, side, "match")
        self.pen_rate = counts.aggregates["p_penalty"]
        self.red_rate = counts.aggregates.get("p_red", 0.10)

        # Per-minute weight for each stat, summing to 1 over the match with the
        # measured 2nd-half share (so half markets match StatsBomb and the
        # full-match marginal is preserved). Goals use H2_GOAL_SHARE.
        minute = np.arange(1, MINUTES + 1)
        self.weight = {"goals": self._half_weight(minute, H2_GOAL_SHARE)}
        for stat in self.STATS[1:]:
            share = counts.aggregates["stats"][stat]["h2_share"]
            self.weight[stat] = self._half_weight(minute, share)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _half_weight(minute: np.ndarray, h2_share: float) -> np.ndarray:
        """Per-minute weights summing to 1, with h2_share of the mass in the
        2nd half (minutes > HALFTIME)."""
        return np.where(minute <= HALFTIME, (1 - h2_share) / HALFTIME,
                        h2_share / (MINUTES - HALFTIME))

    def run(self) -> SimResult:
        n = self.n
        hs = np.zeros(n, dtype=np.int32)   # running home / away score
        as_ = np.zeros(n, dtype=np.int32)
        # accumulators: stat -> side -> period -> array
        acc = {s: {side: {"1h": np.zeros(n, np.int32), "2h": np.zeros(n, np.int32)}
                   for side in ("home", "away")} for s in self.STATS}
        first = np.zeros(n, np.int32)
        first_2h = np.zeros(n, np.int32)

        for t in range(1, MINUTES + 1):
            period = "1h" if t <= HALFTIME else "2h"
            lead_home = hs - as_                       # >0 home leading
            for side, lead in (("home", lead_home), ("away", -lead_home)):
                # --- goals ---
                gw = self.weight["goals"][t - 1]
                goal_rate = self.lam[side]["goals"] * gw * _mean_preserving(np.exp(-GOAL_CHASE_BETA * lead))
                scored = self.rng.random(n) < goal_rate
                acc["goals"][side][period] += scored
                # record first scorers
                code = 1 if side == "home" else 2
                newly = scored & (first == 0)
                first = np.where(newly, code, first)
                newly2 = scored & (period == "2h") & (first_2h == 0)
                first_2h = np.where(newly2, code, first_2h)
                if side == "home":
                    hs = hs + scored
                else:
                    as_ = as_ + scored

                # --- possession stats (shots, corners): leading team cedes ---
                for stat in self.POSSESSION_STATS:
                    rate = (self.lam[side][stat] * self.weight[stat][t - 1]
                            * _mean_preserving(np.exp(-POSSESSION_BETA * lead)))
                    acc[stat][side][period] += self.rng.random(n) < rate
                # --- offsides: ~flat in strength, measured half split ---
                acc["offsides"][side][period] += (
                    self.rng.random(n) < self.lam[side]["offsides"] * self.weight["offsides"][t - 1])
                # --- fouls: trailing team commits slightly more ---
                foul_rate = (self.lam[side]["fouls"] * self.weight["fouls"][t - 1]
                             * _mean_preserving(np.exp(0.05 * lead)))
                acc["fouls"][side][period] += self.rng.random(n) < foul_rate
                # --- cards: measured 2nd-half-heavy split ---
                card_rate = self.lam[side]["cards"] * self.weight["cards"][t - 1]
                acc["cards"][side][period] += self.rng.random(n) < card_rate

        penalty = self.rng.random(n) < self.pen_rate
        red = self.rng.random(n) < self.red_rate

        # fold full-match totals into the stats dict for convenience
        stats = {}
        for s in self.STATS:
            stats[s] = {}
            for side in ("home", "away"):
                h1, h2 = acc[s][side]["1h"], acc[s][side]["2h"]
                stats[s][side] = {"1h": h1, "2h": h2, "match": h1 + h2}

        return SimResult(n, stats["goals"]["home"]["match"], stats["goals"]["away"]["match"],
                         stats, penalty, red, first, first_2h)
