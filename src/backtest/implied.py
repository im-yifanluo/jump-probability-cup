"""Vectorized odds -> implied goal rates for historical club matches.

For each match with closing 1X2 and over/under-2.5 odds:
1. de-margin both books,
2. total T solves P(Poisson(T) >= 3) = p_over,
3. split T into (lam_home, lam_away) so that P(home > away) = p_home under
   independent Poissons (bisection on the share, fully vectorized).

This is the same anchoring the live pricer uses (minus the small DC rho
refinement, which barely moves non-draw markets), so the backtest evaluates
the production pipeline end to end.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

MAX_GOALS = 12


def _de_margin_columns(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    inv = 1.0 / df[cols].to_numpy()
    return inv / inv.sum(axis=1, keepdims=True)


def _invert_total(p_over: np.ndarray) -> np.ndarray:
    """T such that P(Pois(T) >= 3) = p_over, by interpolation on a fine grid."""
    grid = np.linspace(0.4, 6.0, 1200)
    p_grid = 1 - stats.poisson.cdf(2, grid)
    return np.interp(p_over, p_grid, grid)


def _p_home_win(lam_h: np.ndarray, lam_a: np.ndarray) -> np.ndarray:
    """P(H > A) for arrays of independent Poisson rates."""
    k = np.arange(MAX_GOALS + 1)
    pmf_h = stats.poisson.pmf(k[None, :], lam_h[:, None])  # (n, 13)
    pmf_a = stats.poisson.pmf(k[None, :], lam_a[:, None])
    cdf_a = np.cumsum(pmf_a, axis=1)
    # P(H > A) = sum_h pmf_h[h] * P(A <= h-1)
    cdf_shift = np.concatenate([np.zeros((len(lam_h), 1)), cdf_a[:, :-1]], axis=1)
    return (pmf_h * cdf_shift).sum(axis=1)


def add_implied_lambdas(matches: pd.DataFrame) -> pd.DataFrame:
    """Filter to matches with full, valid odds and append lam_home/lam_away."""
    odds_cols = ["odds_home", "odds_draw", "odds_away", "odds_over25", "odds_under25"]
    df = matches.dropna(subset=odds_cols)
    # Decimal odds must exceed 1.0 — a few raw CSV rows carry 0/garbage,
    # which would divide-by-zero into NaN lambdas downstream.
    df = df[(df[odds_cols] > 1.0).all(axis=1)].copy()
    p1x2 = _de_margin_columns(df, ["odds_home", "odds_draw", "odds_away"])
    p_ou = _de_margin_columns(df, ["odds_over25", "odds_under25"])
    df["p_home_market"], df["p_draw_market"], df["p_away_market"] = p1x2.T
    total = _invert_total(p_ou[:, 0])

    lo = np.full(len(df), 0.05)
    hi = total - 0.05
    target = df["p_home_market"].to_numpy()
    for _ in range(35):  # bisection on lam_home
        mid = (lo + hi) / 2
        too_low = _p_home_win(mid, total - mid) < target
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    df["lam_home"] = (lo + hi) / 2
    df["lam_away"] = total - df["lam_home"]
    return df
