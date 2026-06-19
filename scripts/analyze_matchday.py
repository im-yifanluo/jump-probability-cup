"""Post-settlement analysis for a slate of matches.

Maps settled markets to their match two ways: (1) authoritative market_id join
against the last data/snapshot.json (works for matches still open at snapshot
time), (2) team-name parse of the question (works for any team-named market).
Reconstructs each market's binary outcome from brier=(p-o)^2, then reports
per-match / per-family Brier, calibration, and the best & worst calls.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

# today's slate (latest settled)
TODAY = ["ESP vs CPV", "BEL vs EGY", "KSA vs URU", "IRN vs New Zealand"]


def outcome(p: float, b: float) -> int:
    """o in {0,1} from brier=(p-o)^2."""
    return 0 if abs(b - p ** 2) <= abs(b - (1 - p) ** 2) else 1


def fam_of(q: str) -> str:
    s = parse(q, teams=ALL_TEAM_NAMES)
    return s.family + (f"/{s.stat}" if s.stat else "") + ("" if s.period == "match" else f"/{s.period}")


def main():
    pc = ProbabilityCup(bot=1)
    r = pc.results_df()
    r["p"] = r["probability_submitted"].map(lambda v: v / 100 if v > 1 else v)
    r["o"] = r.apply(lambda x: outcome(x.p, x.brier_score), axis=1)
    r["fam"] = r["question"].map(fam_of)

    # (1) authoritative market_id -> match from the snapshot
    snap = json.load(open(ROOT / "data" / "snapshot.json"))
    id2match = {m["id"]: m["match"]["name"] for m in snap["markets"]}
    r["match"] = r["market_id"].map(id2match)

    # (2) fill gaps by team-name parse (only for today's slate)
    teammap = {m: match_teams(m) for m in TODAY}
    def by_team(q):
        for m, (h, a) in teammap.items():
            if h in q or a in q:
                return m
        return None
    r["match"] = r["match"].fillna(r["question"].map(by_team))
    r["src"] = np.where(r["market_id"].isin(id2match), "snapshot", "team-parse")

    t = r[r["match"].isin(TODAY)].copy()

    print(f"=== TODAY'S SLATE — {len(t)} settled markets mapped (of ~40 = 4x10) ===\n")
    pm = t.groupby("match").agg(n=("brier_score", "size"), brier=("brier_score", "mean"),
                                hit=("o", "mean")).reindex(TODAY)
    pm["edge_vs_.25"] = 0.25 - pm["brier"]
    print(pm.round(4).to_string())
    print(f"\nSLATE mean Brier {t.brier_score.mean():.4f} | all-time {r.brier_score.mean():.4f} "
          f"| beat 0.25 on {(t.brier_score < 0.25).mean()*100:.0f}% of markets")

    print("\n=== BY FAMILY (today) ===")
    fam = t.groupby("fam").agg(n=("brier_score", "size"), brier=("brier_score", "mean")).sort_values("brier")
    print(fam.round(3).to_string())

    print("\n=== BEST CALLS (low Brier) ===")
    cols = ["match", "p", "o", "brier_score", "question"]
    print(t.nsmallest(6, "brier_score")[cols].to_string(index=False, max_colwidth=64))
    print("\n=== WORST CALLS (high Brier) ===")
    print(t.nlargest(8, "brier_score")[cols].to_string(index=False, max_colwidth=64))

    print("\n=== CALIBRATION (confidence vs hit rate) ===")
    # fold to "did the favored side happen": conf = max(p,1-p), win = (o==1 if p>=.5 else o==0)
    t["conf"] = np.where(t.p >= 0.5, t.p, 1 - t.p)
    t["right"] = np.where(t.p >= 0.5, t.o == 1, t.o == 0)
    t["bin"] = pd.cut(t.conf, [0.5, 0.6, 0.7, 0.8, 1.01], right=False)
    cal = t.groupby("bin", observed=True).agg(n=("right", "size"), avg_conf=("conf", "mean"),
                                              hit_rate=("right", "mean"))
    print(cal.round(3).to_string())

    t.to_csv(ROOT / "data" / "analysis_today.csv", index=False)
    miss = t["match"].value_counts().reindex(TODAY).fillna(0).astype(int)
    print("\ncoverage per match:", dict(miss), "(team-less penalty/total/ht-tied markets for "
          "matches closed before the snapshot can't be individually attributed)")


if __name__ == "__main__":
    main()
