"""RBP analysis vs the field, using docs/history.md (the crowd 'Field' probs +
realized If-Yes/If-No points for 8 settled matches).

Scoring (confirmed empirically): RBP_market = 100*(crowd_brier - user_brier),
where crowd_brier = (field-o)^2 + Var(crowd). The shown If-Yes/If-No ARE the
realized points (variance term included). We:
  - reconstruct each outcome o from our results_df brier,
  - take realized RBP = If-Yes if o==1 else If-No  (the true competition score),
  - split into SKILL = 100*((field-o)^2-(you-o)^2)  (what our deviation earns)
    and the crowd-variance BONUS (free to everyone near the field),
  - aggregate by match / family / deviation direction.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

ROW = re.compile(r"^(.*?)\t(\d+)%\t(\d+)%\t([-+]?\d+\.\d+)\t([-+]?\d+\.\d+)\t(\d+)\s*$")


def parse_history() -> pd.DataFrame:
    rows, match = [], None
    for line in (ROOT / "docs" / "history.md").read_text().splitlines():
        h = re.match(r"^###\s+(.*?)\s+is live", line)
        if h:
            match = h.group(1)
            continue
        m = ROW.match(line)
        if m:
            q, you, field, iy, ino, sw = m.groups()
            rows.append(dict(match=match, question=q.strip(), you=int(you) / 100,
                             field=int(field) / 100, if_yes=float(iy), if_no=float(ino),
                             swing=int(sw)))
    return pd.DataFrame(rows)


def fam_of(q):
    s = parse(q, teams=ALL_TEAM_NAMES)
    return s.family + (f"/{s.stat}" if s.stat else "")


def main():
    h = parse_history()
    # outcomes from our settled results, matched by question text
    r = ProbabilityCup(bot=1).results_df()
    r["pp"] = r["probability_submitted"].map(lambda v: v / 100 if v > 1 else v)
    r["o"] = r.apply(lambda x: 0 if abs(x.brier_score - x.pp**2) <= abs(x.brier_score - (1-x.pp)**2) else 1, axis=1)
    o_by_q = dict(zip(r["question"].str.strip(), r["o"]))

    h["o"] = h["question"].map(o_by_q)
    unmatched = h[h["o"].isna()]
    if len(unmatched):
        print("!! unmatched questions (no outcome):")
        for q in unmatched["question"]:
            print("   ", q)
    h = h.dropna(subset=["o"]).copy()
    h["o"] = h["o"].astype(int)

    # realized score (true) + skill decomposition
    h["rbp"] = np.where(h.o == 1, h.if_yes, h.if_no)
    h["skill"] = 100 * (((h.field - h.o) ** 2) - ((h.you - h.o) ** 2))
    h["bonus"] = h["rbp"] - h["skill"]
    h["dev"] = h["you"] - h["field"]                 # how far we moved off the field
    h["fam"] = h["question"].map(fam_of)

    print(f"=== TOTAL over {len(h)} settled markets in history.md ({h.match.nunique()} matches) ===")
    print(f"  REALIZED RBP (true score) : {h.rbp.sum():+.1f}   (mean {h.rbp.mean():+.2f}/market)")
    print(f"    of which SKILL (our edge): {h.skill.sum():+.1f}   (mean {h.skill.mean():+.2f})")
    print(f"    of which crowd-var BONUS : {h.bonus.sum():+.1f}   (free to anyone near the field)")
    print(f"  markets we BEAT the field on (skill>0): {(h.skill>0).sum()}/{len(h)} = {(h.skill>0).mean()*100:.0f}%")

    print("\n=== PER MATCH (chron order in file) ===")
    pm = h.groupby("match", sort=False).agg(n=("rbp","size"), realized=("rbp","sum"),
                                            skill=("skill","sum"), mean_dev=("dev","mean")).round(2)
    print(pm.to_string())

    print("\n=== PER FAMILY (skill = our edge vs field) ===")
    pf = h.groupby("fam").agg(n=("skill","size"), skill_sum=("skill","sum"),
                              skill_mean=("skill","mean"), realized_sum=("rbp","sum"),
                              mean_dev=("dev","mean")).sort_values("skill_sum").round(2)
    print(pf.to_string())

    print("\n=== BIGGEST SKILL WINS ===")
    c = ["match","fam","you","field","o","skill","rbp"]
    print(h.nlargest(7,"skill")[c].to_string(index=False))
    print("\n=== BIGGEST SKILL LOSSES ===")
    print(h.nsmallest(7,"skill")[c].to_string(index=False))

    # does deviating from the field pay? bucket by deviation size
    print("\n=== DOES DEVIATION PAY? (skill by |you-field| bucket) ===")
    h["absdev"] = h.dev.abs()
    h["bkt"] = pd.cut(h.absdev, [0,0.03,0.08,0.15,1.0], labels=["~0 (≤3pt)","small(3-8)","med(8-15)","big(>15)"])
    db = h.groupby("bkt", observed=True).agg(n=("skill","size"), skill_mean=("skill","mean"),
                                             skill_sum=("skill","sum")).round(2)
    print(db.to_string())

    h.to_csv(ROOT / "data" / "rbp_analysis.csv", index=False)
    print(f"\nsaved data/rbp_analysis.csv")


if __name__ == "__main__":
    main()
