"""Track live calibration and recommend a shrinkage ONLY when justified.

The model is calibrated on 41k club matches + 314 StatsBomb tournament matches.
Live results are a small, correlated sample (markets within a game move
together), so a few unlucky games can look like overconfidence. This monitor
distinguishes signal from noise: it reports the live reliability curve and the
Brier-optimal shrinkage toward 0.5, but only *recommends* applying it when
(a) the sample is large enough (>= MIN_MARKETS settled) and (b) the optimal
shrinkage is meaningfully below 1 with a bootstrap CI that excludes 1.0.

Run: python3 scripts/calibration_report.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from probability_cup import ProbabilityCup

MIN_MARKETS = 150          # below this, live calibration is too noisy to act on
SHRINK_GRID = np.linspace(0.4, 1.0, 13)


def _outcomes(s: pd.DataFrame) -> pd.DataFrame:
    s = s.copy()
    s["p"] = s["probability_submitted"].map(lambda v: v if v <= 1 else v / 100)
    s["o"] = s.apply(lambda r: int(abs(round(r.p + np.sqrt(r.brier_score)) - (r.p + np.sqrt(r.brier_score)))
                                   < abs(round(r.p - np.sqrt(r.brier_score)) - (r.p - np.sqrt(r.brier_score)))), axis=1)
    return s


def _best_shrink(p, o):
    briers = [np.mean((0.5 + k * (p - 0.5) - o) ** 2) for k in SHRINK_GRID]
    return SHRINK_GRID[int(np.argmin(briers))]


def main():
    s = _outcomes(ProbabilityCup(bot=1).results_df())
    p, o = s["p"].to_numpy(), s["o"].to_numpy()
    n = len(s)
    print(f"=== Calibration over {n} settled markets, Brier {np.mean((p-o)**2):.4f} ===\n")

    s["bucket"] = pd.cut(s["p"], [0, .3, .45, .55, .7, 1])
    print("reliability (predicted vs actual frequency):")
    for b, g in s.groupby("bucket", observed=True):
        flag = "  <-- off" if abs(g.p.mean() - g.o.mean()) > 0.12 else ""
        print(f"  {str(b):12s} n={len(g):3d}  pred={g.p.mean():.2f}  actual={g.o.mean():.2f}{flag}")

    best = _best_shrink(p, o)
    # bootstrap CI on the optimal shrinkage
    boot = [_best_shrink(*(lambda i: (p[i], o[i]))(np.random.randint(0, n, n))) for _ in range(400)]
    lo, hi = np.percentile(boot, [5, 95])
    print(f"\nBrier-optimal shrinkage toward 0.5: {best:.2f}  (90% CI {lo:.2f}-{hi:.2f})")

    if n < MIN_MARKETS:
        print(f"\nRECOMMENDATION: no change — only {n} markets (need >= {MIN_MARKETS} to trust "
              f"live calibration over the 41k-match backtest). Keep monitoring.")
    elif hi < 0.95:
        print(f"\nRECOMMENDATION: apply shrinkage ~{best:.2f} to derived markets — overconfidence "
              f"is now statistically supported (CI excludes 1.0).")
    else:
        print("\nRECOMMENDATION: no change — optimal shrinkage not distinguishable from 1.0.")


if __name__ == "__main__":
    main()
