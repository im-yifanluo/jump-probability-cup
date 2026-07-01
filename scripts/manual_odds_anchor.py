"""Read-only manual-entry odds anchors.

This script is a safe fallback for manual Probability Cup work when the full
production v4 data directory is unavailable.  It never submits predictions.

Examples:
    python3 scripts/manual_odds_anchor.py --date 20260627
    SPORTSPREDICT_KEY_1=... python3 scripts/manual_odds_anchor.py --date 20260627 --sports-predict
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.anchors import (  # noqa: E402
    blend_with_polymarket,
    fetch_espn_anchors,
    fetch_sports_predict_open_markets,
    find_anchor,
    fit_goal_anchor,
    price_goal_spec,
    sports_predict_key,
)
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, match_teams  # noqa: E402

MANUAL_LOG = ROOT / "data" / "logs" / "manual_predictions.md"


def _pct(p: float | None) -> str:
    return "NA" if p is None else f"{round(100 * p):d}%"


def _anchor_rows(goal_anchors) -> list[list[str]]:
    rows = []
    for g in goal_anchors:
        rows.append(
            [
                f"{g.odds.home} vs {g.odds.away}",
                g.odds.kickoff.strftime("%Y-%m-%d %H:%M UTC"),
                f"{round(100*g.odds.p_home):d}/{round(100*g.odds.p_draw):d}/{round(100*g.odds.p_away):d}",
                f"{g.lam_home:.2f}/{g.lam_away:.2f}",
                _pct(price_goal_spec(g, parse(f"Will {g.odds.home} score at least 1 goal?", ALL_TEAM_NAMES))),
                _pct(price_goal_spec(g, parse(f"Will {g.odds.away} score at least 1 goal?", ALL_TEAM_NAMES))),
                _pct(price_goal_spec(g, parse("Will both teams score?", ALL_TEAM_NAMES))),
                _pct(price_goal_spec(g, parse("Will the match have 3 or more total goals?", ALL_TEAM_NAMES))),
                _pct(price_goal_spec(g, parse("Will the match have 4 or more total goals?", ALL_TEAM_NAMES))),
            ]
        )
    return rows


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(str(cell))) for w, cell in zip(widths, row)]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))


def _print_anchor_summary(goal_anchors) -> None:
    headers = ["match", "kickoff", "W/D/W", "lambda", "home scores", "away scores", "BTTS", "3+ goals", "4+ goals"]
    _print_table(headers, _anchor_rows(goal_anchors))


def _sports_predict_rows(
    goal_anchors,
    matches: list[dict],
    markets: list[dict],
    *,
    include_all_open: bool = False,
) -> dict[str, list[list[str]]]:
    match_by_id = {m["id"]: m for m in matches}
    grouped: dict[str, list[dict]] = {}
    for market in markets:
        match = match_by_id.get((market.get("match") or {}).get("id")) or market.get("match") or {}
        grouped.setdefault(match.get("name", "UNKNOWN"), []).append(market)

    output: dict[str, list[list[str]]] = {}
    for match_name, items in sorted(grouped.items()):
        goal_anchor = None
        try:
            team_a, team_b = match_teams(match_name)
        except Exception:
            team_a = team_b = None
        if team_a and team_b:
            anchor = find_anchor([g.odds for g in goal_anchors], team_a, team_b)
            if anchor is not None:
                goal_anchor = next(g for g in goal_anchors if g.odds == anchor)
        if goal_anchor is None and not include_all_open:
            continue
        rows = []
        for market in items:
            question = market["question"]
            if goal_anchor is None:
                rows.append([question, "NEEDS_RESEARCH", "no_manual_anchor"])
                continue
            try:
                spec = parse(question, teams=ALL_TEAM_NAMES)
                p = price_goal_spec(goal_anchor, spec)
            except Exception as exc:
                rows.append([question, "NEEDS_RESEARCH", exc.__class__.__name__])
                continue
            if p is None:
                rows.append([question, "NEEDS_RESEARCH", spec.family])
            else:
                rows.append([question, _pct(p), spec.family])
        output[match_name] = rows
    return output


def _print_sp_markets(rows_by_match: dict[str, list[list[str]]]) -> None:
    print("\nSportsPredict open markets from GET-only reads")
    print("Manual anchors are independent. Bot prediction values are intentionally not read.")
    for match_name, rows in rows_by_match.items():
        print(f"\n## {match_name}")
        _print_table(["question", "manual_anchor", "source"], rows)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |")
    return lines


def _append_log(args, goal_anchors, rows_by_match: dict[str, list[list[str]]], sp_note: str) -> None:
    MANUAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"## {stamp} manual_odds_anchor date={args.date}",
        "",
        f"- sports_predict_markets: {sp_note}",
        "- bot_predictions: not_read",
        f"- polymarket: {'disabled' if args.no_polymarket else 'enabled'}",
        f"- all_open: {bool(args.all_open)}",
        "",
        "### Match-Level Odds Anchors",
        "",
        *_markdown_table(
            ["match", "kickoff", "W/D/W", "lambda", "home scores", "away scores", "BTTS", "3+ goals", "4+ goals"],
            _anchor_rows(goal_anchors),
        ),
        "",
        "### SportsPredict Markets",
        "",
    ]
    if rows_by_match:
        for match_name, rows in rows_by_match.items():
            lines += [
                f"#### {match_name}",
                "",
                *_markdown_table(["question", "manual_anchor", "source"], rows),
                "",
            ]
    else:
        lines.append("_No SportsPredict market rows logged._")
        lines.append("")
    with MANUAL_LOG.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nlogged manual pipeline output to {MANUAL_LOG}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"), help="ESPN scoreboard date YYYYMMDD")
    ap.add_argument("--sports-predict", action="store_true", help="also read open SportsPredict markets via GET only")
    ap.add_argument("--no-polymarket", action="store_true", help="use ESPN/DraftKings only")
    ap.add_argument("--all-open", action="store_true", help="print every open SportsPredict market, not just requested-date matches")
    ap.add_argument("--no-log", action="store_true", help="do not append data/logs/manual_predictions.md")
    args = ap.parse_args()

    anchors = fetch_espn_anchors(args.date)
    if not anchors:
        raise SystemExit(f"No ESPN anchors found for {args.date}")
    if not args.no_polymarket:
        anchors = [blend_with_polymarket(a) for a in anchors]
    goal_anchors = [fit_goal_anchor(a) for a in anchors]

    print(f"Read-only odds anchors for ESPN date {args.date}")
    print("Sources: " + "; ".join(sorted({g.odds.source for g in goal_anchors})))
    _print_anchor_summary(goal_anchors)

    rows_by_match: dict[str, list[list[str]]] = {}
    sp_note = "not_requested"
    if args.sports_predict:
        key = sports_predict_key()
        if not key:
            print("\nSportsPredict read skipped: no SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY in env or .env.")
            sp_note = "skipped_no_key"
        else:
            matches, markets = fetch_sports_predict_open_markets(key)
            rows_by_match = _sports_predict_rows(goal_anchors, matches, markets, include_all_open=args.all_open)
            sp_note = f"read_only_markets rows={sum(len(rows) for rows in rows_by_match.values())}"
            _print_sp_markets(rows_by_match)

    if not args.no_log:
        _append_log(args, goal_anchors, rows_by_match, sp_note)


if __name__ == "__main__":
    main()
