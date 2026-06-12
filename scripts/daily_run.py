"""Daily pipeline: price + submit every match kicking off within the horizon.

This is the deterministic 90% of the daily routine — designed to be run by a
scheduled agent (or by hand) once or more per day:

1. refresh the market snapshot (new matches/markets appear as rounds open)
2. for each match with kickoff within --hours: fetch live odds, refresh
   player rates (API-budget aware), price all open markets
3. submit via the upsert path — first touch creates predictions, every later
   run PATCHes only the values that changed (so "initial" and "final" passes
   are the same command at different times)
4. print a per-match report + anything needing human/agent attention
   (players without data, odds fallbacks, submission errors)

Usage:
    python3 scripts/daily_run.py --hours 48 --dry-run   # show the plan
    python3 scripts/daily_run.py --hours 48 --submit    # do it
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from price_match import build_pricer
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES

sys.path.insert(0, str(ROOT / "scripts"))


def upcoming_matches(pc: ProbabilityCup, hours: float) -> pd.DataFrame:
    markets = pc.markets_df(max_age=pd.Timedelta(minutes=30).to_pytimedelta())
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    mask = (markets["kickoff"] > now) & (markets["kickoff"] <= horizon)
    return markets[mask]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=48, help="kickoff horizon")
    ap.add_argument("--submit", action="store_true", help="actually submit (else dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="alias for not passing --submit")
    args = ap.parse_args()
    dry = not args.submit

    pc = ProbabilityCup(bot=1)
    markets = upcoming_matches(pc, args.hours)
    if markets.empty:
        print(f"No open markets with kickoff in the next {args.hours:.0f}h.")
        return

    attention, all_probs = [], {}
    for match_name, group in markets.groupby("match_name", sort=False):
        kickoff = group["kickoff"].iloc[0]
        print(f"\n=== {match_name}  (kickoff {kickoff:%Y-%m-%d %H:%M UTC}) ===")
        try:
            pricer = build_pricer(match_name, kickoff)
        except Exception as exc:
            attention.append(f"{match_name}: odds/pricer failed — {exc}")
            print(f"  SKIPPED: {exc}")
            continue

        for _, market in group.iterrows():
            # One unpriceable market must never sink the whole unattended run:
            # skip it, submit the rest, and surface it in the attention report.
            try:
                spec = parse(market["question"], teams=ALL_TEAM_NAMES)
                p = pricer.price(spec)
            except Exception as exc:
                attention.append(f"{match_name}: CANNOT PRICE {market['question']!r} "
                                 f"({exc.__class__.__name__}: {exc})")
                print(f"  SKIP   {market['question'][:70]}  <- {exc.__class__.__name__}")
                continue
            all_probs[market["market_id"]] = p
            flag = ""
            if spec.player and pricer.players.rates(spec.player).source == "prior":
                flag = "  <- prior only: research this player"
                attention.append(f"{match_name}: {spec.player} priced on prior")
            print(f"  {p:5.3f}  {market['question'][:70]}{flag}")

    if all_probs:
        print(f"\n{'[dry run] ' if dry else ''}submitting {len(all_probs)} predictions...")
        report = pc.submit(all_probs, dry_run=dry)
        print(report["action"].value_counts().to_dict())
        errors = report[report["action"] == "error"]
        if not errors.empty:
            attention.append(f"{len(errors)} submission errors")
            print(errors.to_string(index=False))

    if attention:
        print("\nNEEDS ATTENTION:")
        for item in attention:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
