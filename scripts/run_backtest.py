"""Run the walk-forward backtest and print/save the per-family report.

Usage: python3 scripts/run_backtest.py
Saves raw results to data/backtest_results.parquet and the summary to
data/backtest_report.csv.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from backtest.engine import report, run_backtest


def calibration_table(results: pd.DataFrame, buckets: int = 10) -> pd.DataFrame:
    df = results.dropna(subset=["p"]).copy()
    df["bucket"] = (df["p"] * buckets).clip(0, buckets - 1).astype(int)
    cal = df.groupby("bucket").agg(
        n=("outcome", "size"), predicted=("p", "mean"), actual=("outcome", "mean"))
    cal["gap"] = cal["actual"] - cal["predicted"]
    return cal


def main():
    results = run_backtest()
    results.to_parquet(ROOT / "data" / "backtest_results.parquet")

    summary = report(results)
    summary.to_csv(ROOT / "data" / "backtest_report.csv", index=False)
    pd.set_option("display.width", 140)
    print("\n=== Per-family Brier (lower is better) ===")
    print(summary.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n=== Calibration (all predictions pooled) ===")
    print(calibration_table(results).to_string(float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
