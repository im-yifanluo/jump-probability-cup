"""Rigorous backtest of the timing_scale change on the FULL settled timing_event
set (not just the live bot2-vs-bot1 divergent probes).

For each settled timing_event market, bot1's submitted probability was priced at
TIMING_SCALE=1.0, so we can recover the window Poisson mean and re-price at any
scale s, then score Brier vs the realized outcome:
  threshold==1 : p = 1 - exp(-wmean*s)          (wmean = -ln(1-p1))
  threshold>=2 : p = 1 - poisson.cdf(k-1, wmean*s)  (wmean recovered numerically)

Reports: (a) in-sample Brier vs scale, (b) the Brier-minimising scale,
(c) a chronological walk-forward — fit the best scale on all settles STRICTLY
BEFORE each market's date, apply OOS, and compare walk-forward Brier at the
fitted scale vs the shipped scale 1.0 and the candidate 0.85.
"""
from __future__ import annotations
import sys
import numpy as np
from scipy import stats
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from probability_cup import ProbabilityCup          # noqa: E402
from questions.parser import parse, UnparsedQuestion  # noqa: E402

SCALES = np.round(np.arange(0.45, 1.26, 0.05), 2)


def _recover_wmean(p1: float, threshold: int) -> float:
    """Invert bot1's scale-1.0 price back to the raw window Poisson mean."""
    p1 = min(max(p1, 1e-6), 1 - 1e-6)
    if threshold <= 1:
        return -np.log(1 - p1)
    # numeric invert P(>=k) = 1 - cdf(k-1, mu) for mu
    lo, hi = 1e-6, 50.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1 - stats.poisson.cdf(threshold - 1, mid) < p1:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _price(wmean: float, scale: float, threshold: int) -> float:
    mu = wmean * scale
    if threshold <= 1:
        return 1 - np.exp(-mu)
    return float(1 - stats.poisson.cdf(threshold - 1, mu))


def main():
    r = ProbabilityCup(bot=1).results_df()
    rows = []
    for _, x in r.iterrows():
        try:
            spec = parse(x.question, teams=[])
        except (UnparsedQuestion, Exception):
            continue
        if spec.family != "timing_event":
            continue
        p1 = float(x.probability_submitted)
        if p1 > 1:
            p1 /= 100.0
        br = float(x.brier_score)
        outcome = 1 if abs(br - (p1 - 1) ** 2) < abs(br - p1 ** 2) else 0
        th = spec.threshold or 1
        rows.append({"date": str(x.created_date)[:10], "p1": p1,
                     "outcome": outcome, "wmean": _recover_wmean(p1, th), "th": th})
    rows.sort(key=lambda z: z["date"])
    n = len(rows)
    print(f"settled timing_event markets: n={n}")
    print(f"  realized rate {np.mean([z['outcome'] for z in rows]):.3f} "
          f"vs mean shipped prob {np.mean([z['p1'] for z in rows]):.3f}")

    # (a) in-sample Brier vs scale
    print("\nin-sample Brier by scale:")
    best_s, best_b = None, 9.9
    for s in SCALES:
        b = np.mean([(_price(z["wmean"], s, z["th"]) - z["outcome"]) ** 2 for z in rows])
        mark = ""
        if b < best_b:
            best_b, best_s = b, s
        if s in (1.0, 0.85, 0.70):
            mark = "  <-- " + {1.0: "shipped", 0.85: "candidate", 0.70: "deeper probe"}[float(s)]
        print(f"  scale {s:.2f}: Brier {b:.4f}{mark}")
    print(f"  => in-sample optimum: scale {best_s:.2f} (Brier {best_b:.4f})")

    # (b) walk-forward: fit best scale on strictly-earlier settles, apply OOS
    def brier_at(scale_fn):
        errs = []
        for i, z in enumerate(rows):
            s = scale_fn(i)
            if s is None:
                continue
            errs.append((_price(z["wmean"], s, z["th"]) - z["outcome"]) ** 2)
        return np.mean(errs), len(errs)

    def wf_fit(i):
        prior = rows[:i]
        if len(prior) < 6:            # need a warmup before trusting a fitted scale
            return None
        return min(SCALES, key=lambda s: np.mean(
            [(_price(z["wmean"], s, z["th"]) - z["outcome"]) ** 2 for z in prior]))

    wf_b, wf_n = brier_at(wf_fit)
    ship_b, _ = brier_at(lambda i: 1.0 if wf_fit(i) is not None else None)
    c085_b, _ = brier_at(lambda i: 0.85 if wf_fit(i) is not None else None)
    c070_b, _ = brier_at(lambda i: 0.70 if wf_fit(i) is not None else None)
    print(f"\nwalk-forward (n={wf_n} OOS, 6-market warmup):")
    print(f"  shipped 1.00 : Brier {ship_b:.4f}")
    print(f"  candidate 0.85: Brier {c085_b:.4f}  ({(c085_b-ship_b)*100:+.2f} vs shipped)")
    print(f"  deeper   0.70 : Brier {c070_b:.4f}  ({(c070_b-ship_b)*100:+.2f} vs shipped)")
    print(f"  fitted-scale  : Brier {wf_b:.4f}  ({(wf_b-ship_b)*100:+.2f} vs shipped)")


if __name__ == "__main__":
    main()
