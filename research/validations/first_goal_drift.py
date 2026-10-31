"""Validate the team_first_goal_game live drift (favourites score first MORE than
lam-share) on the full StatsBomb set.

Clean, exact test — conditional on the final REGULATION goal counts (a:b, a>b),
independent-Poisson goal ordering is EXCHANGEABLE in time, so
P(higher-scoring team scored first) = a/(a+b). If empirically the higher team
scores first MORE than a/(a+b), goals are front-loaded to the stronger side —
the mechanism behind the model's first-goal under-prediction for favourites.
"""
from __future__ import annotations
import json, sys
import numpy as np

from probcup.sources.statsbomb import SB_DIR
REG = {1, 2}


def _goals_in_order(events):
    """Ordered list of scoring team names (own goals credited to the opponent)."""
    goals, teams = [], set()
    for e in events:
        if e.get("period") not in REG:
            continue
        t = e["type"]["name"]; team = e.get("team", {}).get("name")
        if team:
            teams.add(team)
        if t == "Own Goal Against":
            goals.append("OPP_OF:" + team)
        elif t == "Shot" and e.get("shot", {}).get("outcome", {}).get("name") == "Goal":
            goals.append(team)
    teams = sorted(teams)
    if len(teams) != 2:
        return None
    a, b = teams
    resolved = []
    for g in goals:
        if g.startswith("OPP_OF:"):
            conceder = g[7:]; resolved.append(a if conceder == b else b)
        else:
            resolved.append(g)
    return a, b, resolved


def main():
    obs, model = [], []          # higher-count team scored first? vs a/(a+b)
    by_bucket = {}               # dominance bucket -> [obs]
    n = 0
    for d in sorted(p.name for p in SB_DIR.iterdir() if (p / "events").exists()):
        for p in sorted((SB_DIR / d / "events").glob("*.json")):
            r = _goals_in_order(json.loads(p.read_text()))
            if not r:
                continue
            a, b, gs = r
            if not gs:
                continue
            ca, cb = gs.count(a), gs.count(b)
            if ca == cb:
                continue                       # no goal-count favourite this match
            hi, hcount, lcount = (a, ca, cb) if ca > cb else (b, cb, ca)
            share = hcount / (hcount + lcount)
            scored_first = 1.0 if gs[0] == hi else 0.0
            obs.append(scored_first); model.append(share)
            bkt = "big edge (>=2x)" if hcount >= 2 * lcount else "small edge (<2x)"
            by_bucket.setdefault(bkt, []).append((scored_first, share))
            n += 1

    obs, model = np.array(obs), np.array(model)
    gap = obs.mean() - model.mean()
    se = np.sqrt(obs.var(ddof=1) / len(obs))
    print(f"StatsBomb matches with a goal-count favourite: n={n}")
    print(f"  P(higher-count team scored FIRST): observed {obs.mean():.3f} "
          f"vs model(exchangeable) {model.mean():.3f}")
    print(f"  GAP = {gap:+.3f}  (SE {se:.3f}, z={gap/se:.2f})")
    print("  by dominance:")
    for bkt, v in sorted(by_bucket.items()):
        o = np.array([x[0] for x in v]); m = np.array([x[1] for x in v])
        print(f"    {bkt:16} n={len(v):3}  obs {o.mean():.3f}  model {m.mean():.3f}  gap {o.mean()-m.mean():+.3f}")


if __name__ == "__main__":
    main()
