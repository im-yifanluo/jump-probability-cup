"""Parse docs/history.md (V1 tab-tables + V2 Q-blocks) into a unified RBP CSV
and report per-family realized RBP (our edge points vs the crowd).

V2 blocks carry explicit RBP; V1 carries You/Field/IfYes/IfNo, so we join the
settled outcome from results_df to pick the realized side.
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

pd.set_option("display.max_columns", None); pd.set_option("display.width", 200)
TEXT = (ROOT / "docs" / "history.md").read_text()
v1_txt, _, v2_txt = TEXT.partition("## Version 2")


def fam(q):
    try:
        s = parse(q, teams=ALL_TEAM_NAMES)
        return s.family, s.stat or "", s.period
    except Exception:
        return "UNPARSED", "", ""


rows = []
order = 0

# ---- V2: explicit RBP per market ----
for block in re.split(r"\n### ", v2_txt):
    m = re.match(r"\s*(.+?)\n", block)
    if not m:
        continue
    match = m.group(1).strip()
    if match.startswith("#"):
        match = match.lstrip("# ").strip()
    order += 1
    for q in re.split(r"\nQ\d+\n", block):
        res = re.search(r"RESULT:\s*(YES|NO)", q)
        you = re.search(r"YOU\s*\n\s*(\d+)%", q)
        crowd = re.search(r"CROWD\s*\n\s*(\d+)%", q)
        outc = re.search(r"OUTCOME\s*\n\s*(\d+)%", q)
        rbp = re.search(r"\nRBP\s*\n\s*([-+]?\d+\.?\d*)", q)
        qt = re.search(r"RESULT:\s*(?:YES|NO)\s*\n(.+)", q)
        if not (res and you and crowd and rbp and qt):
            continue
        f, st, pe = fam(qt.group(1).strip())
        rows.append(dict(ver=2, order=order, match=match, question=qt.group(1).strip(),
                         you=int(you.group(1))/100, crowd=int(crowd.group(1))/100,
                         outcome=1 if (outc and outc.group(1)=="100") else 0,
                         rbp=float(rbp.group(1)), family=f, stat=st, period=pe))

# ---- V1: join outcomes from results_df ----
r = ProbabilityCup(bot=1).results_df()
r["pp"] = r["probability_submitted"].map(lambda v: v/100 if v>1 else v)
r["o"] = r.apply(lambda x: 0 if abs(x.brier_score-x.pp**2)<=abs(x.brier_score-(1-x.pp)**2) else 1, axis=1)
o_by_q = dict(zip(r["question"].str.strip(), r["o"]))
ROW = re.compile(r"^(.*?)\t(\d+)%\t(\d+)%\t([-+]?\d+\.\d+)\t([-+]?\d+\.\d+)\t(\d+)\s*$")
cur = None
for line in v1_txt.splitlines():
    h = re.match(r"^###\s+(.*?)\s+is live", line)
    if h:
        cur = h.group(1); order += 1; continue
    mm = ROW.match(line)
    if mm and cur:
        q, you, fld, iy, ino, sw = mm.groups()
        o = o_by_q.get(q.strip())
        if o is None:
            continue
        f, st, pe = fam(q.strip())
        rows.append(dict(ver=1, order=order, match=cur, question=q.strip(),
                         you=int(you)/100, crowd=int(fld)/100, outcome=int(o),
                         rbp=float(iy) if o==1 else float(ino), family=f, stat=st, period=pe))

d = pd.DataFrame(rows)
d.to_csv(ROOT/"data"/"rbp_full.csv", index=False)
print(f"parsed {len(d)} markets across {d['match'].nunique()} matches; total RBP {d.rbp.sum():+.1f}")
print(f"  V1: {len(d[d.ver==1])} markets ({d[d.ver==1].rbp.sum():+.1f}) | V2: {len(d[d.ver==2])} ({d[d.ver==2].rbp.sum():+.1f})")
print("\n=== PER-FAMILY RBP (sorted, our leak/edge) ===")
d["famkey"] = d.family + d.stat.map(lambda s: "/"+s if s else "")
g = d.groupby("famkey").agg(n=("rbp","size"), rbp_sum=("rbp","sum"), rbp_mean=("rbp","mean"),
                            mean_you=("you","mean"), mean_crowd=("crowd","mean"), hit=("outcome","mean")).sort_values("rbp_sum")
print(g.round(2).to_string())
