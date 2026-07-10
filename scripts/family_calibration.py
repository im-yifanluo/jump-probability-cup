"""Per-family live calibration — does each market FAMILY's predicted rate match
what actually happens, as results settle? The global monitor (calibration_report.py)
can look fine while a single new family quietly drifts; this breaks it out so a bad
family is caught within a handful of settled games and can be shrunk or pulled.

Signal = the calibration GAP (mean predicted - mean realized), not raw Brier (which
varies with a family's base rate and so isn't comparable across families). Brier is
shown for reference. The NEW R32 families (built 2026-06-27/28) are flagged with ★ so
they get the most scrutiny while their live sample is still small.

    python scripts/family_calibration.py            # bot 1
    python scripts/family_calibration.py --bot 2
"""
from __future__ import annotations

import argparse
import sys
from math import sqrt
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from probability_cup import ProbabilityCup  # noqa: E402
from questions.parser import parse, UnparsedQuestion  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402

# Families introduced for the knockout rounds — least live evidence, most scrutiny.
NEW_R32_FAMILIES = {
    "advance", "clean_sheet", "btts", "match_drawn", "timing_event",
    "any_player_braces", "any_player_sot_ge", "total_shots_ge",
    "substitute_scores", "substitution_before_ht", "team_first_goal_game",
    "team_win_by_ge", "team_scores_both_halves",
}
MIN_N_TO_FLAG = 8       # below this a family's live gap is too noisy to act on
GAP_FLAG = 0.15         # |mean_pred - mean_actual| above this (with enough n) = drift


def _family(question: str) -> str:
    try:
        return parse(question, teams=ALL_TEAM_NAMES).family
    except UnparsedQuestion:
        return "?unparsed"


def _outcome(p: float, brier: float) -> int:
    """Recover the realized 0/1 from (submitted prob, brier): brier=(p-o)^2, so
    o=1 leaves residual |1-p|, o=0 leaves |p|; pick the closer."""
    sb = sqrt(max(brier, 0.0))
    return 1 if abs(sb - (1 - p)) <= abs(sb - p) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", type=int, default=1)
    args = ap.parse_args()

    pc = ProbabilityCup(bot=args.bot)
    res = pc.results_df()
    if res is None or len(res) == 0:
        print("No settled markets yet — nothing to calibrate.")
        return
    # results_df carries the question text directly, so settled markets resolve
    # even after they age out of the current snapshot.
    res = res.copy()
    res["p"] = pd.to_numeric(res["probability_submitted"], errors="coerce")
    res["p"] = res["p"].where(res["p"] <= 1, res["p"] / 100)
    res["brier"] = pd.to_numeric(res["brier_score"], errors="coerce")
    res = res.dropna(subset=["p", "brier"])

    rows = []
    for _, r in res.iterrows():
        rows.append({"family": _family(r["question"]), "p": r["p"],
                     "o": _outcome(r["p"], r["brier"]), "brier": r["brier"]})
    df = pd.DataFrame(rows)

    g = (df.groupby("family")
           .agg(n=("o", "size"), pred=("p", "mean"), actual=("o", "mean"),
                brier=("brier", "mean"))
           .reset_index())
    g["gap"] = g["pred"] - g["actual"]
    g = g.sort_values(["n"], ascending=False)

    overall = df["brier"].mean()
    print(f"=== Per-family calibration · bot {args.bot} · {len(df)} settled markets · "
          f"overall Brier {overall:.4f} ===")
    print(f"{'family':24s} {'n':>3} {'pred':>6} {'actual':>7} {'gap':>7} {'brier':>7}")
    flagged = []
    for _, r in g.iterrows():
        star = "★" if r["family"] in NEW_R32_FAMILIES else " "
        flag = ""
        if r["n"] >= MIN_N_TO_FLAG and abs(r["gap"]) > GAP_FLAG:
            flag = "  <-- DRIFT"
            flagged.append(r["family"])
        print(f"{star}{r['family']:23s} {int(r['n']):>3} {r['pred']:>6.3f} "
              f"{r['actual']:>7.3f} {r['gap']:>+7.3f} {r['brier']:>7.4f}{flag}")
    print("\n★ = new R32 family (small live sample — watch closely).")
    if flagged:
        print(f"DRIFT (|gap|>{GAP_FLAG}, n>={MIN_N_TO_FLAG}): {', '.join(flagged)} "
              f"-> investigate; shrink toward base rate or pull if it persists.")
    else:
        print(f"No family past the drift threshold (|gap|>{GAP_FLAG}, n>={MIN_N_TO_FLAG}).")


if __name__ == "__main__":
    main()
