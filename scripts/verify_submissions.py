"""Reconcile our submission log against the platform's stored predictions.

Verifies every submission landed correctly by comparing, per market:
  - what we last intended to submit (data/logs/submissions.csv), against
  - what GET /predictions actually returns from the platform.

Reports three failure modes:
  MISSING    we logged a submit but the platform has no prediction for it
  MISMATCH   the platform's stored value differs from our last submitted value
  UNLOGGED   the platform has a prediction we have no submission record for

Exit 0 = every logged submission is confirmed on the platform.

Usage: python3 scripts/verify_submissions.py [--bot 1]
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from probability_cup import ProbabilityCup

SUBMISSIONS = ROOT / "data" / "logs" / "submissions.csv"
SUBMITTED_ACTIONS = {"create", "update", "unchanged"}  # actions that should be on-platform


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bot", type=int, default=1, choices=[1, 2])
    args = ap.parse_args()

    pc = ProbabilityCup(bot=args.bot)

    # Platform truth: market_id -> stored integer probability
    platform = {p["market_id"]: pc.stored_prob_to_int(p["probability"])
                for p in pc.api.predictions(lobby_id=pc.lobby["id"])}
    print(f"platform: {len(platform)} predictions stored (bot {args.bot})")

    if not SUBMISSIONS.exists():
        print("no submissions.csv yet — nothing logged to reconcile")
        return 0

    # The audit log has a schema transition (a `bot` column was added partway
    # through), so parse tolerantly rather than rewriting historical rows.
    sent: dict[str, int] = {}
    for line in SUBMISSIONS.read_text().splitlines():
        f = line.split(",")
        if f[0] == "submitted_at" or len(f) < 5:
            continue  # header rows / malformed
        if len(f) == 5:                       # old: ts, market, prob, action, error
            bot, market, prob, action = 1, f[1], f[2], f[3]
        else:                                 # new: ts, bot, market, prob, action, error
            bot, market, prob, action = int(f[1]), f[2], f[3], f[4]
        if bot == args.bot and action in SUBMITTED_ACTIONS:
            sent[market] = int(prob)          # keep last per market (file is chronological)
    print(f"submission log: {len(sent)} markets we believe we submitted\n")

    missing = [m for m in sent if m not in platform]
    mismatch = [(m, sent[m], platform[m]) for m in sent
                if m in platform and platform[m] != int(sent[m])]
    unlogged = [m for m in platform if m not in sent]

    if missing:
        print(f"MISSING ({len(missing)}): logged but not on platform")
        for m in missing[:10]:
            print(f"  {m}  (we sent {sent[m]})")
    if mismatch:
        print(f"MISMATCH ({len(mismatch)}): platform value != last submitted")
        for m, ours, theirs in mismatch[:10]:
            print(f"  {m}  sent {ours} -> platform {theirs}")
    if unlogged:
        print(f"UNLOGGED ({len(unlogged)}): on platform, no submission record "
              f"(expected for predictions made before logging existed)")

    ok = not missing and not mismatch
    print("\n" + ("VERIFIED: every logged submission matches the platform"
                  if ok else "DISCREPANCIES FOUND — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
