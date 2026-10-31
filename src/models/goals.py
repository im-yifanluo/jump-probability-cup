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
ET_FRACTION = 1.0 / 3.0  # 30' extra time vs 90' regulation, at the same Poisson rate
_SINGLE_DIGIT_SHARE = 0.383  # StatsBomb: 269/703 goals scored by a jersey-1-9 player
PENS_TILT = 0.25         # a penalty shootout retains only ~this share of the regulation
                         # strength edge — empirically shootouts are near coin-flips


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

    def p_btts(self) -> float:
        """Both teams score (>=1 each)."""
        return float(self.grid[(self._h >= 1) & (self._a >= 1)].sum())

    def p_win_by_ge(self, side: str, margin: int) -> float:
        """Team wins by a margin of at least `margin` goals."""
        diff = (self._h - self._a) if side == "home" else (self._a - self._h)
        return float(self.grid[diff >= margin].sum())

    def p_clean_sheet(self, side: str) -> float:
        """Team `side` keeps a clean sheet = the opponent scores 0."""
        return float(self.grid[:, 0].sum() if side == "home" else self.grid[0, :].sum())

    def total_goals_pmf(self) -> np.ndarray:
        """P(total goals = k) over the scoreline grid."""
        return np.bincount((self._h + self._a).ravel(), weights=self.grid.ravel())

    def p_advance(self, side: str) -> float:
        """Team `side` wins a knockout tie: win in regulation, OR a regulation
        draw (p_draw) then win the tie-break.

        The tie-break is modelled EXPLICITLY rather than as one proportional-to-
        odds share: 30' of extra time as independent Poissons at ET_FRACTION of
        each side's 90' rate, and if extra time is still level, penalties as a
        near-coin-flip (the regulation strength share is shrunk toward 0.5 by
        PENS_TILT, since shootouts are empirically close to 50/50). This lifts the
        underdog's advance odds in lopsided ties, where the old proportional model
        over-favoured the stronger team. Conserves exactly:
        p_advance(home) + p_advance(away) == 1 (proof: ET outcomes partition, and
        the two sides' pens_share sum to 1 because their strength shares do)."""
        pw = self.p_team_win(side)
        po = self.p_team_win("away" if side == "home" else "home")
        s = pw / (pw + po) if (pw + po) > 0 else 0.5   # regulation strength share
        lam_s = (self.lam_home if side == "home" else self.lam_away) * ET_FRACTION
        lam_o = (self.lam_away if side == "home" else self.lam_home) * ET_FRACTION
        et_s, et_o = poisson_pmf(lam_s), poisson_pmf(lam_o)
        pw_et = float(sum(et_s[i] * et_o[:i].sum() for i in range(1, len(et_s))))
        pdraw_et = float(np.sum(et_s * et_o))
        pens_share = 0.5 + PENS_TILT * (s - 0.5)
        cond = pw_et + pdraw_et * pens_share
        return pw + self.p_draw() * cond

    def p_shootout(self) -> float:
        """P(the tie is decided by a penalty shootout) = P(regulation draw) x
        P(extra time still level) — the same ET Poissons as p_advance
        (ET_FRACTION of each side's 90' rate), so the two families are
        mutually consistent."""
        et_h = poisson_pmf(self.lam_home * ET_FRACTION)
        et_a = poisson_pmf(self.lam_away * ET_FRACTION)
        return self.p_draw() * float(np.sum(et_h * et_a))

    def p_total_goals_exact(self, n: int) -> float:
        """P(exactly n total goals in regulation), straight off the pmf."""
        pmf = self.total_goals_pmf()
        return float(pmf[n]) if 0 <= n < len(pmf) else 0.0

    def p_goal_each_half(self) -> float:
        """P(>=1 goal in EACH half, either team) — independent Poisson halves
        at the h2_share split (the same machinery as the half markets)."""
        return (self.p_total_goals_half_ge(1, "1h")
                * self.p_total_goals_half_ge(1, "2h"))

    def p_same_goals_halves(self) -> float:
        """P(both halves produce the SAME number of total goals) — independent
        Poisson halves at the h2_share split (QF template, 2026-07-06)."""
        lam_tot = self.lam_home + self.lam_away
        h2 = self.h2_share
        pmf1 = poisson_pmf(lam_tot * (1 - h2))
        pmf2 = poisson_pmf(lam_tot * h2)
        n = min(len(pmf1), len(pmf2))
        return float(np.sum(pmf1[:n] * pmf2[:n]))

    def p_team_n_in_one_half(self, n: int) -> float:
        """P(SOME team scores >= n goals within a single half) — the four
        (team, half) cells as independent Poissons at the h2_share split
        (final template, 2026-07-17)."""
        cells = [self._half_lambda(side, half)
                 for side in ("home", "away") for half in ("1h", "2h")]
        p_none = 1.0
        for lam in cells:
            p_none *= float(poisson_pmf(lam)[:n].sum())   # P(cell < n)
        return 1.0 - p_none

    def p_first_goal_single_digit(self) -> float:
        """P(the match's first goal — incl. extra time — is scored by a player
        wearing a single-digit shirt 1-9; No if 0-0 after ET). = P(>=1 goal in
        regulation+ET) x the StatsBomb single-digit scorer share (269/703=0.383;
        modern attackers mostly wear 10/11/17+). ET only occurs on a reg draw."""
        lam_et = (self.lam_home + self.lam_away) * ET_FRACTION
        p00 = float(self.grid[0, 0])
        p_goal = (1 - p00) + p00 * (1 - exp(-lam_et))   # reg goal, or 0-0 reg then ET goal
        return float(p_goal * _SINGLE_DIGIT_SHARE)

    def p_first_goal_by_other(self, excl_lambda_sum: float) -> float:
        """P(the match's first goal is scored by a player OUTSIDE a named set) =
        P(>=1 goal) x (1 - the set's share of total scoring intensity). Competing
        Poisson: given a goal occurs, its scorer is player X with prob λ_X/λ_total;
        own goals fall outside any named set (KO template, 2026-07-07)."""
        lam_tot = self.lam_home + self.lam_away
        if lam_tot <= 0:
            return 0.0
        p_goal = 1 - float(self.grid[0, 0])
        share = min(max(excl_lambda_sum, 0.0) / lam_tot, 1.0)
        return float(p_goal * (1 - share))

    def p_total_goals_odd(self) -> float:
        """P(the total number of regulation goals is ODD) — parity sum over the
        total-goals pmf (KO template, 2026-07-07)."""
        return float(self.total_goals_pmf()[1::2].sum())

    def p_margin_exact(self, margin: int) -> float:
        """P(the regulation result is decided by exactly `margin` goals) — the
        absolute goal difference equals `margin` (a draw is margin 0, so
        margin==1 is "decided by exactly one goal"). Straight off the grid."""
        diff = np.abs(self._h - self._a)
        return float(self.grid[diff == margin].sum())

    def p_ht_scoreless(self) -> float:
        """P(the match is 0-0 at halftime) = P(no goals in the first half) =
        exp(-lambda_1h_total), the first-half Poisson at the h2_share split."""
        lam_1h = (self.lam_home + self.lam_away) * (1 - self.h2_share)
        return float(exp(-lam_1h))

    def p_first_goal_in_2h(self) -> float:
        """P(the match's first goal falls in the second half) = P(no 1H goal) x
        P(>=1 2H goal), independent Poisson halves at the h2_share split."""
        lam_tot = self.lam_home + self.lam_away
        h2 = self.h2_share
        return float(exp(-lam_tot * (1 - h2)) * (1 - exp(-lam_tot * h2)))

    def _p_win_half(self, side: str, period: str) -> float:
        """P(`side` outscores the opponent within a single half) — independent
        Poisson half rates at the h2_share split."""
        ls = self._half_lambda(side, period)
        lo = self._half_lambda("away" if side == "home" else "home", period)
        ps, po = poisson_pmf(ls), poisson_pmf(lo)
        return float(sum(ps[i] * po[:i].sum() for i in range(1, len(ps))))

    def p_either_wins_both_halves(self) -> float:
        """P(one team wins BOTH halves on the scoreboard) — a team outscores the
        opponent in the 1st half AND in the 2nd half. Independent halves, summed
        over the two mutually-exclusive teams (both can't win the same half)."""
        return (self._p_win_half("home", "1h") * self._p_win_half("home", "2h")
                + self._p_win_half("away", "1h") * self._p_win_half("away", "2h"))

    # StatsBomb decomposition for "hold a lead at any point" (regulation), from
    # scripts/derive_r16_families.py over 314 matches / 628 team-matches:
    #   P(led | won) = 1.000 (exact — you must lead to win)
    #   P(led | drew & scored first) = 1.000 (exact — the first scorer led 1-0)
    #   P(led | drew & did not score first) = 0.073
    #   P(led | lost) = 0.132 (comeback losses)
    _LED_DREW_NOFIRST = 0.073
    _LED_LOST = 0.132

    def p_hold_lead(self, side: str) -> float:
        """P(`side` holds a lead at any point in regulation): exact result
        decomposition, with P(scored first | draw) = strength share x
        P(>=1 goal | draw) from the score grid (0-0 draws can't have a leader)."""
        other = "away" if side == "home" else "home"
        p_win, p_loss, p_draw = self.p_team_win(side), self.p_team_win(other), self.p_draw()
        lam_s = self.lam_home if side == "home" else self.lam_away
        share = lam_s / (self.lam_home + self.lam_away)
        p00 = float(self.grid[0, 0])
        p_first_given_draw = share * max(0.0, 1.0 - p00 / p_draw) if p_draw > 0 else 0.0
        return (p_win
                + p_draw * (p_first_given_draw * 1.0
                            + (1 - p_first_given_draw) * self._LED_DREW_NOFIRST)
                + p_loss * self._LED_LOST)

    # ----------------------------------------------------------------- #
    # Half-based markets (independent Poisson halves, lam * share)
    # ----------------------------------------------------------------- #

    def _half_lambda(self, side: str, period: str) -> float:
        lam = self.lam_home if side == "home" else self.lam_away
        return lam * (self.h2_share if period == "2h" else 1 - self.h2_share)

    def p_team_scores_in_half(self, side: str, period: str) -> float:
        return 1 - exp(-self._half_lambda(side, period))

    def p_team_scores_both_halves(self, side: str) -> float:
        """Team scores in BOTH halves (independent-half Poisson)."""
        return ((1 - exp(-self._half_lambda(side, "1h")))
                * (1 - exp(-self._half_lambda(side, "2h"))))

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

    def p_first_goal_game(self, side: str) -> float:
        """Team scores the game's first goal — Poisson race over the full match:
        rate share * P(the match has >=1 goal)."""
        lam_s = self.lam_home if side == "home" else self.lam_away
        lam_total = self.lam_home + self.lam_away
        return lam_s / lam_total * (1 - float(self.grid[0, 0]))

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
