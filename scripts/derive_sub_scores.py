"""Derive the substitute-scores conditional P(>=1 substitute scores | total goals G)
from StatsBomb events — split by SUBSTITUTION REGIME so the 3-sub WC-2018 era does
not bias the (5-sub) 2026 estimate.

The live market: "Will a substitute score a goal (excluding own goals) in regulation
(90 min + stoppage)?" so we count, per match, REGULATION goals only (periods 1-2):
  - G (the conditioner) = all regulation goals incl. own goals (matches the scoreline
    total the goal model conditions on),
  - sub_scored        = any regulation Shot-Goal by a player who came on as a sub
    (own goals excluded — the market says so, and they aren't a player "scoring").

Run: python scripts/derive_sub_scores.py
Prints the pooled table (current counts.py source), the 5-sub-only table, and the
per-G sample sizes so noise is visible.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wcdata.statsbomb import SB_DIR  # noqa: E402

THREE_SUB = {"wc2018"}            # only the 2018 World Cup predates the 5-sub rule
REG_PERIODS = {1, 2}             # regulation = first + second half (exclude ET/shootout)
GMAX = 6                          # table is indexed 0..GMAX, G>=GMAX folds into the last cell


def _match_facts(events: list[dict]) -> tuple[int, bool]:
    """(regulation goal total G, did any substitute score a regulation goal)."""
    subs: set[str] = set()
    for e in events:
        if e["type"]["name"] == "Substitution":
            rep = e.get("substitution", {}).get("replacement", {}).get("name")
            if rep:
                subs.add(rep)
    g = 0
    sub_scored = False
    for e in events:
        if e.get("period") not in REG_PERIODS:
            continue
        etype = e["type"]["name"]
        if etype == "Shot" and e.get("shot", {}).get("outcome", {}).get("name") == "Goal":
            g += 1
            if e["player"]["name"] in subs:
                sub_scored = True
        elif etype == "Own Goal Against":
            g += 1   # counts toward the scoreline total, but never as a sub "scoring"
    return g, sub_scored


def _table(rows: list[tuple[int, bool]]) -> tuple[list[float], list[int]]:
    """rows of (G, sub_scored) -> (P(sub|G) for G=0..GMAX, n per G)."""
    by_g: dict[int, list[bool]] = defaultdict(list)
    for g, scored in rows:
        by_g[min(g, GMAX)].append(scored)
    probs, ns = [], []
    for g in range(GMAX + 1):
        vals = by_g.get(g, [])
        probs.append(round(sum(vals) / len(vals), 3) if vals else 0.0)
        ns.append(len(vals))
    return probs, ns


def main():
    all_rows, five_rows = [], []
    per_tourn = {}
    for d in sorted(p.name for p in SB_DIR.iterdir() if (p / "events").exists()):
        rows = []
        for path in sorted((SB_DIR / d / "events").glob("*.json")):
            rows.append(_match_facts(json.loads(path.read_text())))
        per_tourn[d] = (sum(s for _, s in rows) / len(rows), len(rows))
        all_rows += rows
        if d not in THREE_SUB:
            five_rows += rows

    print("Per-tournament marginal P(a substitute scores in regulation):")
    for d, (rate, n) in per_tourn.items():
        tag = "  [3-sub]" if d in THREE_SUB else "  [5-sub]"
        print(f"  {d:10s} n={n:3d}  P(sub scored)={rate:.3f}{tag}")

    pooled_p, pooled_n = _table(all_rows)
    five_p, five_n = _table(five_rows)
    cur = (0.0, 0.068, 0.225, 0.304, 0.600, 0.579, 0.917)  # current counts._SUB_GOAL
    print(f"\nMarginal P(sub scores): pooled(314)={sum(s for _,s in all_rows)/len(all_rows):.3f}"
          f"   5-sub-only({len(five_rows)})={sum(s for _,s in five_rows)/len(five_rows):.3f}")
    print("\n G |  current |  pooled(n)   | 5-sub-only(n)")
    for g in range(GMAX + 1):
        print(f" {g}{'+' if g == GMAX else ' '}|  {cur[g]:.3f}   | "
              f"{pooled_p[g]:.3f} ({pooled_n[g]:3d})  | {five_p[g]:.3f} ({five_n[g]:3d})")
    print("\n5-sub-only table (paste into counts._SUB_GOAL if materially different):")
    print("  " + repr(tuple(five_p)))


if __name__ == "__main__":
    main()
