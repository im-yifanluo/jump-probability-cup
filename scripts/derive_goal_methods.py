"""Derive + LOTO-backtest the three 'goal by method' market models from StatsBomb:
  - own goal scored        (Own Goal Against events)
  - header goal scored     (Shot, outcome Goal, body_part = Head)
  - outside-the-box goal   (Shot, outcome Goal, location outside the 18-yard box)

All in REGULATION (periods 1-2) to match the market wording. Each is modelled as a
conditional on the match's total regulation goals G — P(>=1 type | G) — to be
integrated over the goal pmf at price time (the substitute-scores pattern). The
LOTO test asks: does conditioning on G beat a flat base rate out-of-sample, and is
it calibrated? Run: python scripts/derive_goal_methods.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wcdata.statsbomb import SB_DIR  # noqa: E402

REG = {1, 2}            # regulation halves
GMAX = 6                # condition table indexed 0..GMAX
# StatsBomb pitch 120x80; penalty box = x in [102,120], y in [18,62].
def _outside_box(loc) -> bool:
    if not loc or len(loc) < 2:
        return False
    x, y = loc[0], loc[1]
    return not (102.0 <= x <= 120.0 and 18.0 <= y <= 62.0)


def _match_facts(events: list[dict]) -> tuple[int, bool, bool, bool]:
    """(regulation total goals G, any own goal, any header goal, any outside-box goal)."""
    g = own = head = outside = 0
    for e in events:
        if e.get("period") not in REG:
            continue
        t = e["type"]["name"]
        if t == "Own Goal Against":
            g += 1; own += 1
        elif t == "Shot" and e.get("shot", {}).get("outcome", {}).get("name") == "Goal":
            g += 1
            if e["shot"].get("body_part", {}).get("name") == "Head":
                head += 1
            if _outside_box(e.get("location")):
                outside += 1
    return g, own > 0, head > 0, outside > 0


def _load() -> dict[str, list]:
    """tournament -> [(G, any_own, any_head, any_outside), ...]."""
    out = {}
    for d in sorted(p.name for p in SB_DIR.iterdir() if (p / "events").exists()):
        rows = [_match_facts(json.loads(p.read_text()))
                for p in sorted((SB_DIR / d / "events").glob("*.json"))]
        out[d] = rows
    return out


def _table(rows, idx) -> tuple[np.ndarray, np.ndarray]:
    """P(outcome idx | G) for G=0..GMAX, and per-G counts. idx: 1=own,2=head,3=outside."""
    by_g = defaultdict(list)
    for r in rows:
        by_g[min(r[0], GMAX)].append(r[idx])
    p = np.array([np.mean(by_g[g]) if by_g[g] else 0.0 for g in range(GMAX + 1)])
    n = np.array([len(by_g[g]) for g in range(GMAX + 1)])
    return p, n


def main():
    data = _load()
    allrows = [r for rows in data.values() for r in rows]
    names = ["", "own goal", "header goal", "outside-box goal"]
    print(f"StatsBomb: {len(allrows)} matches across {len(data)} tournaments\n")
    for idx in (1, 2, 3):
        base = np.mean([r[idx] for r in allrows])
        tbl, n = _table(allrows, idx)
        print(f"=== {names[idx]} ===  base rate P(>=1) = {base:.3f}")
        print("   P(>=1 | G):  " + "  ".join(f"G{g}:{tbl[g]:.2f}(n{n[g]})" for g in range(GMAX + 1)))
        # LOTO: conditional-on-G vs flat base rate, out-of-sample
        b_cond = b_base = nrows = 0
        for hold, rows in data.items():
            tr = [r for t, rs in data.items() if t != hold for r in rs]
            tbl_tr, _ = _table(tr, idx)
            base_tr = np.mean([r[idx] for r in tr])
            for r in rows:
                o = r[idx]
                b_cond += (tbl_tr[min(r[0], GMAX)] - o) ** 2
                b_base += (base_tr - o) ** 2
                nrows += 1
        print(f"   LOTO Brier: conditional-on-G {b_cond/nrows:.4f}  vs flat base-rate {b_base/nrows:.4f}"
              f"  -> {'CONDITION on G' if b_cond < b_base else 'use FLAT base rate'}\n")


if __name__ == "__main__":
    main()
