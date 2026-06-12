"""Submit a predictions CSV to the Probability Cup.

The CSV is the contract between any model and the platform: produce one from
a notebook or script, inspect it, then submit it here.

Required columns:  market_id, probability   (probability as a 0-1 float)
Extra columns (question, match_name, ...) are ignored — keep them for readability.

Usage:
    python3 scripts/submit.py data/predictions.csv --dry-run   # preview actions
    python3 scripts/submit.py data/predictions.csv             # actually submit
    python3 scripts/submit.py data/predictions.csv --bot 2     # second bot's entry
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from probability_cup import ProbabilityCup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV with market_id, probability (0-1 float)")
    parser.add_argument("--bot", type=int, default=1, choices=[1, 2], help="which bot key to use")
    parser.add_argument("--dry-run", action="store_true", help="show actions without submitting")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    missing = {"market_id", "probability"} - set(df.columns)
    if missing:
        raise SystemExit(f"CSV is missing required columns: {missing}")

    probs = dict(zip(df["market_id"], df["probability"]))
    print(f"{len(probs)} predictions from {args.csv} -> bot {args.bot}")

    report = ProbabilityCup(bot=args.bot).submit(probs, dry_run=args.dry_run)

    prefix = "[dry run] " if args.dry_run else ""
    print(f"{prefix}{report['action'].value_counts().to_dict()}")

    errors = report[report["action"] == "error"]
    if not errors.empty:
        print("\nFailed entries:")
        print(errors[["market_id", "probability", "error"]].to_string(index=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
