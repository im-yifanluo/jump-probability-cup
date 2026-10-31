"""Rigorous OOS test of the goal model's BTTS calibration on historical club data
(data/club_matches.parquet, ~116k matches with 1X2 + O/U 2.5 odds + FT scores).

The live drift (WC-2026, n=16): BTTS predicted ~0.497 vs realized ~0.688 — we
UNDER-predict. Is that a real flaw in the odds-anchored goal model, or small-sample
/ knockout-regime noise? Fit the REAL production ScorelineModel.from_market to each
match's de-margined odds, compute p_btts(), and compare to the actual BTTS outcome
across thousands of matches (with a per-decile calibration table).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
from scipy import stats

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from models.goals import ScorelineModel  # noqa: E402

N_SAMPLE = 6000


def _total_goals_from_ou(p_over: float) -> float:
    """Invert P(>=3 total goals) = p_over to a total Poisson mean (matches how the
    production model reads an O/U 2.5 line)."""
    lo, hi = 0.2, 6.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if 1 - stats.poisson.cdf(2, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main():
    df = pd.read_parquet(ROOT / "data" / "club_matches.parquet")
    df = df.dropna(subset=["odds_home", "odds_draw", "odds_away",
                           "odds_over25", "odds_under25", "home_goals", "away_goals"])
    df = df[(df.odds_home > 1) & (df.odds_draw > 1) & (df.odds_away > 1)]
    # deterministic sample (no RNG — reproducible)
    if len(df) > N_SAMPLE:
        df = df.iloc[:: max(1, len(df) // N_SAMPLE)].head(N_SAMPLE)

    preds, outs = [], []
    for _, m in df.iterrows():
        ih, idr, ia = 1 / m.odds_home, 1 / m.odds_draw, 1 / m.odds_away
        s = ih + idr + ia
        p_home, p_draw = ih / s, idr / s
        io, iu = 1 / m.odds_over25, 1 / m.odds_under25
        p_over = io / (io + iu)
        tot = _total_goals_from_ou(p_over)
        try:
            model = ScorelineModel.from_market(p_home, p_draw, tot)
            p_btts = model.p_btts()
        except Exception:
            continue
        preds.append(p_btts)
        outs.append(1 if (m.home_goals >= 1 and m.away_goals >= 1) else 0)

    preds, outs = np.array(preds), np.array(outs)
    n = len(preds)
    gap = outs.mean() - preds.mean()
    se = np.sqrt(outs.mean() * (1 - outs.mean()) / n)
    brier = np.mean((preds - outs) ** 2)
    print(f"BTTS calibration on {n} historical club matches (real from_market model):")
    print(f"  mean predicted {preds.mean():.3f}  vs  realized {outs.mean():.3f}  "
          f"gap {gap:+.3f}  (z={gap/se:.2f}, Brier {brier:.4f})")
    print("  per-decile of predicted BTTS (pred -> realized, n):")
    order = np.argsort(preds)
    for i in range(10):
        idx = order[i * n // 10:(i + 1) * n // 10]
        if len(idx):
            print(f"    {preds[idx].mean():.3f} -> {outs[idx].mean():.3f}   (n={len(idx)})")


if __name__ == "__main__":
    main()
