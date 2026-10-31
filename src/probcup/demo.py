"""Price a real tournament board offline and compare with what actually happened.

    probcup demo                                   # the World Cup final, ESP vs ARG
    probcup demo --list                            # every board available
    probcup demo --match "FRA vs ESP" --odds 0.37 0.32 2.64

No network and no API keys: the odds anchors come from examples/odds.yaml (or
--odds), everything else from the archived model files and researched inputs
in data/. Each forecast is shown next to what bot 1 submitted live, the crowd's
average, the result, and the points it earned against the crowd.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json

import pandas as pd
import yaml

from probcup import config
from probcup.pipeline.build import build_pricer
from probcup.questions.parser import parse
from probcup.questions.teams import ALL_TEAM_NAMES, match_teams

TRIM = (" in regulation (90 minutes + stoppage time)", " (90 minutes + stoppage time)",
        " (excluding own goals)", " (both teams combined)", " (on and off target)")


def _short(question: str, width: int = 70) -> str:
    question = question.split("?")[0] + "?"
    for t in TRIM:
        question = question.replace(t, "")
    return question if len(question) <= width else question[: width - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="probcup demo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", default="ESP vs ARG", help='board to price (default: the final)')
    ap.add_argument("--odds", nargs=3, type=float, metavar=("P_HOME", "P_DRAW", "TOTAL_GOALS"),
                    help="odds anchors for the first-listed team (overrides examples/odds.yaml)")
    ap.add_argument("--list", action="store_true", help="list the available boards and exit")
    args = ap.parse_args(argv)

    boards = {b["match_name"]: b for b in json.loads((config.EXAMPLES / "boards.json").read_text())}
    if args.list:
        print(f"{len(boards)} boards (first-listed team = home):")
        for name, b in boards.items():
            print(f"  {name:12s} {len(b['markets']):>2} markets  first priced {b['first_created'][:10]}")
        return 0
    if args.match not in boards:
        raise SystemExit(f"no board {args.match!r}; see `probcup demo --list`")
    board = boards[args.match]

    recorded = yaml.safe_load((config.EXAMPLES / "odds.yaml").read_text()) or {}
    if args.odds:
        anchors, source = tuple(args.odds), "from --odds"
    elif args.match in recorded:
        r = recorded[args.match]
        anchors = (r["p_home"], r["p_draw"], r["total_goals"])
        source = "recovered from bot 1's submissions by examples/recover_odds.py"
    else:
        raise SystemExit(f"no recorded odds for {args.match}; pass --odds P_HOME P_DRAW TOTAL_GOALS")

    kickoff = pd.Timestamp(board["kickoff"] or board["first_created"])
    with contextlib.redirect_stdout(io.StringIO()):  # scorer-prop scoping notes
        pricer = build_pricer(args.match, kickoff, anchors=anchors)
    settled = pd.read_csv(config.RESULTS_DATA / "settled_markets_bot1.csv").set_index("market_id")

    home, away = match_teams(args.match)
    print(f"\n{home} vs {away} — priced offline")
    print(f"odds anchors: {home} {anchors[0]:.1%} / draw {anchors[1]:.1%} / "
          f"{anchors[2]:.2f} expected goals  ({source})\n")
    print(f"{'ours':>5} {'sent':>5} {'crowd':>6} {'result':>7} {'points':>7}  market")
    same = rows = 0
    for m in board["markets"]:
        spec = parse(m["question"], teams=ALL_TEAM_NAMES)
        ours = pricer.price(spec)
        s = settled.loc[m["market_id"]] if m["market_id"] in settled.index else None
        if s is None:
            sent = crowd = result = pts = "—"
        else:
            sent, crowd = f"{int(s.probability_submitted)}%", f"{int(s.crowd_value)}%"
            result, pts = s.result.split()[0], f"{s.rbp:+.1f}"
            same += round(ours * 100) == int(s.probability_submitted)
            rows += 1
        print(f"{ours:>5.0%} {sent:>5} {crowd:>6} {result:>7} {pts:>7}  {_short(m['question'])}")

    mine = settled.loc[[m["market_id"] for m in board["markets"] if m["market_id"] in settled.index]]
    if rows:
        print(f"\nRecomputed offline: {same} of {rows} forecasts equal what bot 1 submitted "
              f"(to the whole percent).")
        print(f"Bot 1 on this board: {mine.rbp.sum():+.1f} points against the crowd, "
              f"mean Brier {mine.brier_score.mean():.3f}; beat the crowd on "
              f"{(mine.outcome_tag == 'beat_crowd').sum()} of {rows} markets.")
    return 0
