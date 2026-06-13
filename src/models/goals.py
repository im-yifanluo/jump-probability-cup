"""Goal model: Dixon-Coles scoreline distribution anchored to market odds.

The joint distribution of (home goals, away goals) is independent Poisson with
the Dixon-Coles tau correction for low-scoring dependence. Parameters are not
estimated from team histories — they are *fitted to the bookmaker's de-margined
1X2 + total-goals line*, which decades of research show is the best-calibrated
public forecast. Every goal-derived market is then priced by enumerating the
scoreline grid, so all answers are mutually consistent.

Half-based questions use per-half Poisson rates lam * share, with the
second-half goal share measured from data (see backtest; ~0.54).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, factorial

import numpy as np
from scipy.optimize import least_squares

MAX_GOALS = 12  # grid truncation; P(>12 goals) is negligible
H2_GOAL_SHARE = 0.54  # measured on club data (see backtest validation)


def poisson_pmf(lam: float, n: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(n + 1)
    pmf = np.array([exp(-lam) * lam**i / factorial(i) for i in k])
    return pmf / pmf.sum()


def de_margin(*odds: float) -> tuple[float, ...]:
    """Decimal odds -> probabilities, bookmaker margin removed proportionally."""
    raw = [1.0 / o for o in odds]
    total = sum(raw)
    return tuple(p / total for p in raw)


def valid_rho_bounds(lam_h: float, lam_a: float) -> tuple[float, float]:
    """Dixon-Coles rho range keeping all four tau factors non-negative."""
    lo = max(-1.0 / max(lam_h, 1e-6), -1.0 / max(lam_a, 1e-6)) + 1e-3
    hi = min(1.0 / max(lam_h * lam_a, 1e-6), 1.0) - 1e-3
    return lo, hi


def _dc_tau(grid: np.ndarray, lam_h: float, lam_a: float, rho: float) -> np.ndarray:
    """Apply the Dixon-Coles low-score correction and renormalize. The caller
    clamps rho to the valid range; the floor at 0 is belt-and-braces against
    a probability cell going negative."""
    grid = grid.copy()
    grid[0, 0] *= 1 - lam_h * lam_a * rho
    grid[1, 0] *= 1 + lam_a * rho
    grid[0, 1] *= 1 + lam_h * rho
    grid[1, 1] *= 1 - rho
    grid = np.maximum(grid, 0.0)
    return grid / grid.sum()


@dataclass
class ScorelineModel:
    lam_home: float
    lam_away: float
    rho: float = 0.0
    h2_share: float = H2_GOAL_SHARE

    def __post_init__(self):
        self.rho = float(np.clip(self.rho, *valid_rho_bounds(self.lam_home, self.lam_away)))
        joint = np.outer(poisson_pmf(self.lam_home), poisson_pmf(self.lam_away))
        self.grid = _dc_tau(joint, self.lam_home, self.lam_away, self.rho)
        self._h = np.arange(MAX_GOALS + 1)[:, None]  # home goals index
        self._a = np.arange(MAX_GOALS + 1)[None, :]  # away goals index

    # ----------------------------------------------------------------- #
    # Fitting
    # ----------------------------------------------------------------- #

    @classmethod
    def from_market(cls, p_home: float, p_draw: float, total_goals: float,
                    h2_share: float = H2_GOAL_SHARE) -> "ScorelineModel":
        """Find (lam_home, lam_away, rho) reproducing the market's home-win and
        draw probabilities with the given total expected goals."""

        def residuals(params):
            lam_h, rho = params
            model = cls(lam_h, max(total_goals - lam_h, 0.05), rho, h2_share)
            return [model.p_home_win() - p_home, model.p_draw() - p_draw]

        fit = least_squares(residuals, x0=[total_goals / 2, -0.05],
                            bounds=([0.1, -0.9], [total_goals - 0.1, 0.9]))
        lam_h, rho = fit.x
        return cls(lam_h, total_goals - lam_h, rho, h2_share)

    # ----------------------------------------------------------------- #
    # Full-match markets (exact on the DC grid)
    # ----------------------------------------------------------------- #

    def p_home_win(self) -> float:
        return float(self.grid[self._h > self._a].sum())

    def p_away_win(self) -> float:
        return float(self.grid[self._h < self._a].sum())

    def p_draw(self) -> float:
        return float(self.grid[(self._h == self._a).nonzero()].sum())

    def p_team_win(self, side: str) -> float:
        return self.p_home_win() if side == "home" else self.p_away_win()

    def p_total_goals(self, threshold: int, direction: str = ">=") -> float:
        totals = self._h + self._a
        mask = totals >= threshold if direction == ">=" else totals <= threshold
        return float(self.grid[mask].sum())

    def p_btts_and_total_ge(self, threshold: int) -> float:
        mask = (self._h >= 1) & (self._a >= 1) & (self._h + self._a >= threshold)
        return float(self.grid[mask].sum())

    def p_team_goals_ge(self, side: str, threshold: int) -> float:
        axis = 1 if side == "home" else 0
        marginal = self.grid.sum(axis=axis)
        return float(marginal[threshold:].sum())

    # ----------------------------------------------------------------- #
    # Half-based markets (independent Poisson halves, lam * share)
    # ----------------------------------------------------------------- #

    def _half_lambda(self, side: str, period: str) -> float:
        lam = self.lam_home if side == "home" else self.lam_away
        return lam * (self.h2_share if period == "2h" else 1 - self.h2_share)

    def p_team_scores_in_half(self, side: str, period: str) -> float:
        return 1 - exp(-self._half_lambda(side, period))

    def p_total_goals_half_ge(self, threshold: int, period: str) -> float:
        lam = self._half_lambda("home", period) + self._half_lambda("away", period)
        return float(poisson_pmf(lam)[threshold:].sum())

    def p_h2_more_goals_than_h1(self) -> float:
        h1 = poisson_pmf(self._half_lambda("home", "1h") + self._half_lambda("away", "1h"))
        h2 = poisson_pmf(self._half_lambda("home", "2h") + self._half_lambda("away", "2h"))
        return float(sum(h2[i] * h1[:i].sum() for i in range(1, len(h2))))

    def p_team_more_goals_2h(self, side: str) -> float:
        us = poisson_pmf(self._half_lambda(side, "2h"))
        other = "away" if side == "home" else "home"
        them = poisson_pmf(self._half_lambda(other, "2h"))
        return float(sum(us[i] * them[:i].sum() for i in range(1, len(us))))

    def p_ht_tied(self) -> float:
        h = poisson_pmf(self._half_lambda("home", "1h"))
        a = poisson_pmf(self._half_lambda("away", "1h"))
        return float(np.sum(h * a))

    def p_ht_winning(self, side: str) -> float:
        us = poisson_pmf(self._half_lambda(side, "1h"))
        other = "away" if side == "home" else "home"
        them = poisson_pmf(self._half_lambda(other, "1h"))
        return float(sum(us[i] * them[:i].sum() for i in range(1, len(us))))

    # ----------------------------------------------------------------- #
    # First-goal markets (Poisson race: P(side first) ∝ its rate)
    # ----------------------------------------------------------------- #

    def p_first_goal_2h(self, side: str) -> float:
        lam_s = self._half_lambda(side, "2h")
        lam_total = self._half_lambda("home", "2h") + self._half_lambda("away", "2h")
        return lam_s / lam_total * (1 - exp(-lam_total))

    def p_first_goal_game_and_other_scores_2h(self, first_side: str, other_side: str) -> float:
        """Combo: first_side (A) scores the game's first goal AND other_side (B)
        scores in the second half.

        Computed on the half structure rather than as independent marginals
        (which overstate the joint by ~0.02-0.03). Split into where the game's
        first goal falls:
          - 1st half: P(>=1 1H goal) * [A's rate share of 1H] * P(B scores 2H)
          - 2nd half: P(no 1H goal) * P(A's first 2H goal precedes B's, B>=1)
        The second term uses, for independent half-Poissons a2,b2:
          P(first 2H goal is A AND b2>=1)
            = a2/(a2+b2)*(1-e^-(a2+b2)) - (1-e^-a2)*e^-b2
        Verified against Monte Carlo (agreement < 0.005)."""
        a_side, b_side = first_side, other_side
        a1 = self._half_lambda(a_side, "1h"); a2 = self._half_lambda(a_side, "2h")
        b1 = self._half_lambda(b_side, "1h"); b2 = self._half_lambda(b_side, "2h")

        term_1h = (1 - exp(-(a1 + b1))) * (a1 / (a1 + b1)) * (1 - exp(-b2))
        p_first_a_2h = (a2 / (a2 + b2) * (1 - exp(-(a2 + b2)))
                        - (1 - exp(-a2)) * exp(-b2))
        term_2h = exp(-(a1 + b1)) * p_first_a_2h
        return term_1h + term_2h
