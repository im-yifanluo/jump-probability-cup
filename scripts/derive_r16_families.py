"""Derive the empirical inputs for the two R16 families that need data:

  1. hold_lead — "Will <team> hold a lead at any point (excluding shootout)?"
     Decompose P(led at some point in regulation) by the final REGULATION result:
       P(led | won), P(led | drew, scored first), P(led | drew, not first), P(led | lost)
     Price-time composition (goals model supplies the result probs + first-goal share):
       P(hold) = P(win)*a_win + P(draw)*[fg*a_dsf + (1-fg)*a_dnf] + P(loss)*a_loss

  2. plays_full_match — "Will <player> play the entire match in regulation?"
     Among STARTERS in 5-sub-era tournaments only (wc2022 euro2024 copa2024 afcon2023 —
     the 3-sub era under-counts substitutions), the share NOT substituted off (and not
     red-carded), by position group. Star attackers are subbed most; this is the base
     rate for the (rare) player-minutes family.

Run: python scripts/derive_r16_families.py
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

REG = {1, 2}
FIVE_SUB = {"wc2022", "euro2024", "copa2024", "afcon2023"}


def _match_lead_facts(events):
    """Per team: (led_at_any_point, scored_first, reg_result). Regulation only."""
    goals = []  # (team, is_own_goal_against)
    teams = set()
    for e in events:
        if e.get("period") not in REG:
            continue
        t = e["type"]["name"]
        team = e.get("team", {}).get("name")
        if team:
            teams.add(team)
        if t == "Own Goal Against":
            goals.append(("OPP_OF:" + team, None))  # credited to the opponent
        elif t == "Shot" and e.get("shot", {}).get("outcome", {}).get("name") == "Goal":
            goals.append((team, None))
    teams = sorted(teams)
    if len(teams) != 2:
        return None
    a, b = teams
    score = {a: 0, b: 0}
    led = {a: False, b: False}
    first = {a: False, b: False}
    for i, (scorer, _) in enumerate(goals):
        if scorer.startswith("OPP_OF:"):
            conceder = scorer[7:]
            scorer = a if conceder == b else b
        score[scorer] += 1
        if i == 0:
            first[scorer] = True
        for t, o in ((a, b), (b, a)):
            if score[t] > score[o]:
                led[t] = True
    res = {}
    for t, o in ((a, b), (b, a)):
        r = "won" if score[t] > score[o] else ("lost" if score[t] < score[o] else "drew")
        res[t] = (led[t], first[t], r)
    return res


def _full90_facts(events):
    """Per starter: (position_group, played_full). 5-sub era callers only."""
    starters = {}   # player -> position group
    subbed_off = set()
    sent_off = set()
    for e in events:
        t = e["type"]["name"]
        if t == "Starting XI":
            for p in e.get("tactics", {}).get("lineup", []):
                pos = p.get("position", {}).get("name", "")
                grp = ("GK" if "Keeper" in pos else
                       "DEF" if "Back" in pos else
                       "FWD" if ("Forward" in pos or "Wing" in pos or "Striker" in pos) else "MID")
                starters[p["player"]["name"]] = grp
        elif t == "Substitution" and e.get("period") in REG:
            # regulation-only: an extra-time sub still played the full 90
            subbed_off.add(e["player"]["name"])
        elif t in ("Bad Behaviour", "Foul Committed"):
            card = (e.get("bad_behaviour", {}).get("card", {}) or e.get("foul_committed", {}).get("card", {})).get("name", "")
            if card in ("Red Card", "Second Yellow"):
                sent_off.add(e["player"]["name"])
    return [(grp, name not in subbed_off and name not in sent_off)
            for name, grp in starters.items()]


def main():
    lead = defaultdict(list)   # key -> [led...]
    full = defaultdict(list)   # posgrp -> [full90...]
    n_lead = n_full = 0
    for d in sorted(p.name for p in SB_DIR.iterdir() if (p / "events").exists()):
        for p in sorted((SB_DIR / d / "events").glob("*.json")):
            events = json.loads(p.read_text())
            facts = _match_lead_facts(events)
            if facts:
                n_lead += 1
                for _, (led, first, res) in facts.items():
                    key = res if res != "drew" else ("drew_first" if first else "drew_nofirst")
                    lead[key].append(led)
            if d in FIVE_SUB:
                n_full += 1
                for grp, f90 in _full90_facts(events):
                    full[grp].append(f90)

    print(f"hold_lead conditionals ({n_lead} matches, both teams):")
    for k in ("won", "drew_first", "drew_nofirst", "lost"):
        v = lead[k]
        print(f"  P(led at any point | {k:12}) = {np.mean(v):.3f}  (n={len(v)})")

    print(f"\nplays_full_match — starter full-90 share, 5-sub era ({n_full} matches):")
    for g in ("GK", "DEF", "MID", "FWD"):
        v = full[g]
        print(f"  {g}: {np.mean(v):.3f}  (n={len(v)})")


if __name__ == "__main__":
    main()
