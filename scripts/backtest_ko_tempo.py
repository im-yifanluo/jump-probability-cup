"""Is knockout tempo compressed vs group stage? — the flagged pre-final check.

Live motivation: two straight KO games where our TOTAL-count levels read high
(FRA/ESP low-event; ENG/ARG 8+ SOT lost -22 with bot2's 1.60 losing worse), and
the totals scales (TOTAL_SOT_SCALE 1.50 etc.) were calibrated mostly on
group-heavy samples. If KO games systematically produce fewer counting events,
the totals need a stage haircut BEFORE the 3x final.

Method: StatsBomb tournaments with both stages (wc2018/2022, euro2020/2024,
copa2024, afcon2023). Per match: total SOT / shots / corners / cards / goals /
offsides in REGULATION. Compare KO vs group per tournament (ratio is counting-
convention-free) and pooled, with a t-test. KO matches identified by
competition_stage from the matches metadata.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
from scipy import stats as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wcdata.statsbomb import SB_DIR  # noqa: E402

REG = {1, 2}
GROUP_NAMES = {"Group Stage", "League Stage"}


def _match_counts(events):
    c = {"sot": 0, "shots": 0, "corners": 0, "cards": 0, "goals": 0, "offsides": 0}
    for e in events:
        if e.get("period") not in REG:
            continue
        t = e["type"]["name"]
        if t == "Shot":
            c["shots"] += 1
            out = e.get("shot", {}).get("outcome", {}).get("name")
            if out in ("Goal", "Saved", "Saved To Post"):
                c["sot"] += 1
            if out == "Goal":
                c["goals"] += 1
        elif t == "Own Goal Against":
            c["goals"] += 1
        elif t == "Offside":
            c["offsides"] += 1
        elif t in ("Foul Committed", "Bad Behaviour"):
            card = (e.get("foul_committed") or e.get("bad_behaviour") or {}).get("card", {}).get("name")
            if card:
                c["cards"] += 1
        elif t == "Pass" and (e.get("pass", {}).get("type", {}) or {}).get("name") == "Corner":
            c["corners"] += 1
    return c


def main():
    rows = []  # (tournament, is_ko, counts)
    for d in sorted(p.name for p in SB_DIR.iterdir() if (p / "events").exists()):
        meta_files = list((SB_DIR / d).glob("matches*.json"))
        stage_by_id = {}
        for mf in meta_files:
            for m in json.loads(mf.read_text()):
                stage_by_id[m["match_id"]] = m.get("competition_stage", {}).get("name", "?")
        for p in sorted((SB_DIR / d / "events").glob("*.json")):
            mid = int(p.stem)
            stage = stage_by_id.get(mid)
            if stage is None:
                continue
            is_ko = stage not in GROUP_NAMES
            rows.append((d, is_ko, _match_counts(json.loads(p.read_text()))))

    stats_keys = ["sot", "shots", "corners", "cards", "goals", "offsides"]
    print(f"matches: {len(rows)}  (KO {sum(1 for _,k,_ in rows if k)}, "
          f"group {sum(1 for _,k,_ in rows if not k)})")
    print(f"\n{'stat':9} {'group_mean':>10} {'ko_mean':>8} {'KO/group':>9} {'t':>6} {'p':>7}")
    for k in stats_keys:
        g = np.array([c[k] for _, ko, c in rows if not ko], float)
        ko = np.array([c[k] for _, ko_, c in rows if ko_], float)
        t, pv = st.ttest_ind(ko, g, equal_var=False)
        print(f"{k:9} {g.mean():10.2f} {ko.mean():8.2f} {ko.mean()/g.mean():9.3f} {t:6.2f} {pv:7.3f}")

    # per-tournament SOT ratio (guards against tournament-mix confounds)
    print("\nper-tournament total-SOT KO/group ratio:")
    for d in sorted({d for d, _, _ in rows}):
        g = [c["sot"] for dd, ko, c in rows if dd == d and not ko]
        k = [c["sot"] for dd, ko, c in rows if dd == d and ko]
        if g and k:
            print(f"  {d:10} group {np.mean(g):5.2f} (n={len(g):3})  ko {np.mean(k):5.2f} "
                  f"(n={len(k):3})  ratio {np.mean(k)/np.mean(g):.3f}")


if __name__ == "__main__":
    main()
