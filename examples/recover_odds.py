"""Recover the odds anchors a board was priced with, from what bot 1 submitted.

The live run fitted its goal model to the market's de-margined 1X2 + total
(ESPN/DraftKings averaged with Polymarket), but those inputs weren't logged.
They can be recovered: the goal-derived markets on a board (win/advance, both
teams to score, extra time, ...) depend on nothing else, so we solve for the
(p_home, p_draw, total_goals) that reproduce bot 1's submitted prices on those
markets. Every other market on the board (cards, shots, corners, player props)
is then an out-of-sample check that the archived code and inputs reproduce the
live forecasts.

The researched inputs in data/inputs/ are the versions from the final, so the
final reproduces best (19 of 20 forecasts to the whole percent); earlier boards
were priced with player rates and props that were since updated.

    python examples/recover_odds.py                  # the final
    python examples/recover_odds.py "FRA vs ESP"     # any other board

Writes examples/odds.yaml, which `probcup demo` reads.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

import pandas as pd
import yaml
from scipy.optimize import minimize

from probcup.pipeline.build import build_pricer
from probcup.questions.parser import parse
from probcup.questions.teams import ALL_TEAM_NAMES

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results" / "data" / "settled_markets_bot1.csv"
DEFAULT = ["ESP vs ARG"]  # the World Cup final
GOAL_FAMILIES = {
    "team_win", "advance", "btts", "total_goals", "match_drawn", "ht_tied", "ht_team_winning",
    "team_goals_ge", "team_win_by_ge", "btts_and_total_ge", "team_first_goal_game", "clean_sheet",
    "total_goals_exact", "goal_each_half", "ht_scoreless", "extra_time", "shootout_decided",
    "total_goals_odd", "team_n_in_one_half", "decided_by_one_goal", "same_goals_halves",
}


def recover(board: dict, submitted: dict[str, int]) -> dict:
    specs = [(m["market_id"], parse(m["question"], teams=ALL_TEAM_NAMES)) for m in board["markets"]]
    goal_ids = [mid for mid, s in specs if s.family in GOAL_FAMILIES]
    kickoff = pd.Timestamp(board["kickoff"] or "2026-07-19T19:00:00+00:00")

    def prices(x):
        with contextlib.redirect_stdout(io.StringIO()):
            pricer = build_pricer(board["match_name"], kickoff, anchors=tuple(x))
        return {mid: pricer.price(s) for mid, s in specs}

    def loss(x):
        if not (0.05 < x[0] < 0.9 and 0.05 < x[1] < 0.5 and x[0] + x[1] < 0.97 and 1.0 < x[2] < 5):
            return 10.0
        p = prices(x)
        return sum((p[m] - submitted[m] / 100) ** 2 for m in goal_ids)

    starts = ([0.40, 0.30, 2.4], [0.55, 0.25, 2.7], [0.25, 0.28, 2.3])
    best = min((minimize(loss, x0, method="Nelder-Mead",
                         options={"xatol": 1e-5, "fatol": 1e-10, "maxiter": 500}) for x0 in starts),
               key=lambda r: r.fun)
    p = prices(best.x)
    matched = sum(round(p[mid] * 100) == submitted[mid] for mid, _ in specs)
    return {"p_home": round(float(best.x[0]), 4), "p_draw": round(float(best.x[1]), 4),
            "total_goals": round(float(best.x[2]), 4),
            "fitted_on": len(goal_ids), "reproduced": f"{matched}/{len(specs)}"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matches", nargs="*", default=DEFAULT)
    args = ap.parse_args()
    boards = {b["match_name"]: b for b in json.loads((HERE / "boards.json").read_text())}
    res = pd.read_csv(RESULTS).set_index("market_id")["probability_submitted"].to_dict()
    out_path = HERE / "odds.yaml"
    out = (yaml.safe_load(out_path.read_text()) or {}) if out_path.exists() else {}
    for name in args.matches:
        board = boards[name]
        out[name] = recover(board, {m["market_id"]: int(res[m["market_id"]]) for m in board["markets"]})
        print(f"{name}: {out[name]}")
    out_path.write_text(yaml.safe_dump(out, sort_keys=True))


if __name__ == "__main__":
    main()
