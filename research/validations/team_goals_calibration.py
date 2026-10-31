"""OOS calibration of p_team_goals_ge (team scores >= k) on historical club data —
the 2026-07-16 drift check (live: n=20, pred 0.491 vs realized 0.650, we look LOW).

Same harness as backtest_btts_calibration: fit the production ScorelineModel to
each match's de-margined 1X2 + O/U 2.5 odds, price P(home >= 2) and P(away >= 2),
score vs the actual outcome, with a per-decile calibration table. If the club
sample is calibrated, the WC drift is small-sample noise; if it under-predicts
here too, the goal model needs work before the final.
"""
from __future__ import annotations
from probcup import config
import numpy as np
import pandas as pd
from scipy import stats

from probcup.models.goals import ScorelineModel

N_SAMPLE = 6000


def _total_from_ou(p_over: float) -> float:
    lo, hi = 0.2, 6.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if 1 - stats.poisson.cdf(2, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main():
    df = pd.read_parquet(config.CLUB_MATCHES)
    df = df.dropna(subset=["odds_home", "odds_draw", "odds_away",
                           "odds_over25", "odds_under25", "home_goals", "away_goals"])
    df = df[(df.odds_home > 1) & (df.odds_draw > 1) & (df.odds_away > 1)]
    if len(df) > N_SAMPLE:
        df = df.iloc[:: max(1, len(df) // N_SAMPLE)].head(N_SAMPLE)

    preds, outs = [], []
    for _, m in df.iterrows():
        ih, idr, ia = 1 / m.odds_home, 1 / m.odds_draw, 1 / m.odds_away
        s = ih + idr + ia
        io, iu = 1 / m.odds_over25, 1 / m.odds_under25
        try:
            model = ScorelineModel.from_market(ih / s, idr / s, _total_from_ou(io / (io + iu)))
            for side, goals in (("home", m.home_goals), ("away", m.away_goals)):
                preds.append(model.p_team_goals_ge(side, 2))
                outs.append(1 if goals >= 2 else 0)
        except Exception:
            continue

    preds, outs = np.array(preds), np.array(outs)
    n = len(preds)
    gap = outs.mean() - preds.mean()
    se = np.sqrt(outs.mean() * (1 - outs.mean()) / n)
    print(f"team_goals_ge(2) calibration on {n} team-matches (real from_market model):")
    print(f"  mean predicted {preds.mean():.3f}  vs realized {outs.mean():.3f}  "
          f"gap {gap:+.3f}  (z={gap/se:.2f}, Brier {np.mean((preds-outs)**2):.4f})")
    print("  per-decile (pred -> realized, n):")
    order = np.argsort(preds)
    for i in range(10):
        idx = order[i * n // 10:(i + 1) * n // 10]
        if len(idx):
            print(f"    {preds[idx].mean():.3f} -> {outs[idx].mean():.3f}   (n={len(idx)})")


if __name__ == "__main__":
    main()
