"""Replay WC count-intensity multiplier candidates on settled count markets.

This is a read-only manual-strategy diagnostic. It does not read
SportsPredict `/predictions` and never submits.

The replay reconstructs each settled count market from `docs/history.md` using:

* the market question and outcome/RBP from history,
* historical ESPN/DraftKings pickcenter odds for the match,
* the current CountPricer structure,
* several predeclared `TOURNAMENT_INTENSITY` maps.

It is stronger than a comment-level calibration claim, but not a perfect
time-machine replay: it uses current ESPN historical pickcenter odds as the
market anchor and does not reconstruct old Polymarket blends, referee overrides,
or every intraday odds snapshot.

Usage:
    uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
      python scripts/count_intensity_replay.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Iterable

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket, brier, clip_prob, official_alt_rbp, official_delta_rbp  # noqa: E402
from count_policy_backtest import load_count_rows  # noqa: E402
from models import counts as count_module  # noqa: E402
from models.counts import CountPricer, load_count_models, tournament_aggregates  # noqa: E402
from models.goals import ScorelineModel  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, names_match  # noqa: E402
from wcdata.odds_feeds import SCOREBOARD, SUMMARY, MatchOdds, _american_to_decimal, _de_margin  # noqa: E402
from wcdata.statsbomb import build_aggregates  # noqa: E402


OUT = ROOT / "data" / "logs" / "count_intensity_replay.md"
TOURNAMENT_AVG_LAM = 1.30


@dataclass(frozen=True)
class MatchContext:
    match: str
    kickoff: datetime
    left: str
    right: str
    odds: MatchOdds
    goals: ScorelineModel
    counts: CountPricer


@dataclass(frozen=True)
class PricedRow:
    row: HistoryMarket
    probability: float
    candidate: str


def candidate_maps() -> dict[str, dict[str, float]]:
    """Predeclared count-intensity candidates.

    These are mean multipliers applied after club->international discounts.
    """

    return {
        "current_v4": {"cards": 0.78, "sot": 1.00, "corners": 1.05, "offsides": 0.90, "fouls": 0.90},
        "sot105_only": {"cards": 0.78, "sot": 1.05, "corners": 1.05, "offsides": 0.90, "fouls": 0.90},
        "uniform090_v3": {"cards": 0.90, "sot": 0.90, "corners": 0.90, "offsides": 0.90, "fouls": 0.90},
        "aggressive_bot2": {"cards": 0.72, "sot": 1.05, "corners": 1.15, "offsides": 0.90, "fouls": 0.90},
        "no_extra_100": {"cards": 1.00, "sot": 1.00, "corners": 1.00, "offsides": 1.00, "fouls": 1.00},
    }


def canonical_team(name: str) -> str:
    for team in sorted(ALL_TEAM_NAMES):
        if names_match(team, name):
            return team
    raise ValueError(f"no canonical team match for {name!r}")


def split_match(match: str) -> tuple[str, str]:
    left, sep, right = match.partition(" vs ")
    if not sep:
        raise ValueError(f"cannot split match name {match!r}")
    return canonical_team(left.strip()), canonical_team(right.strip())


def _event_teams(event: dict) -> tuple[str, str]:
    comp = event["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")["team"]["displayName"]
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")["team"]["displayName"]
    return home, away


def event_matches(event: dict, left: str, right: str) -> bool:
    home, away = _event_teams(event)
    return (
        (names_match(left, home) and names_match(right, away))
        or (names_match(left, away) and names_match(right, home))
    )


def fetch_scoreboard_events(start: date, end: date, session: requests.Session) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        board = session.get(SCOREBOARD, params={"dates": cur.strftime("%Y%m%d")}, timeout=20).json()
        for event in board.get("events", []):
            event_id = str(event.get("id"))
            if event_id not in seen:
                seen.add(event_id)
                events.append(event)
        cur += timedelta(days=1)
    return events


def _event_odds(event: dict, session: requests.Session) -> MatchOdds | None:
    summary = session.get(SUMMARY, params={"event": event["id"]}, timeout=20).json()
    pick = next(
        (
            o for o in summary.get("pickcenter", [])
            if o.get("homeTeamOdds", {}).get("moneyLine") is not None
            and o.get("awayTeamOdds", {}).get("moneyLine") is not None
            and o.get("drawOdds", {}).get("moneyLine") is not None
        ),
        None,
    )
    if not pick:
        return None
    home, away = _event_teams(event)
    p_home, p_draw, p_away = _de_margin(
        _american_to_decimal(pick["homeTeamOdds"]["moneyLine"]),
        _american_to_decimal(pick["drawOdds"]["moneyLine"]),
        _american_to_decimal(pick["awayTeamOdds"]["moneyLine"]),
    )
    total_line = float(pick.get("overUnder") or 2.5)
    over = pick.get("overOdds")
    under = pick.get("underOdds")
    if over is not None and under is not None:
        p_over, _ = _de_margin(_american_to_decimal(over), _american_to_decimal(under))
    else:
        p_over = 0.5
    provider = pick.get("provider", {}).get("name", "ESPN")
    return MatchOdds(home, away, p_home, p_draw, p_away, total_line, p_over, f"ESPN/{provider}/historical")


def build_contexts(
    matches: Iterable[str],
    *,
    start: date,
    end: date,
    session: requests.Session | None = None,
) -> tuple[dict[str, MatchContext], list[str]]:
    session = session or requests.Session()
    events = fetch_scoreboard_events(start, end, session)
    params = load_count_models()
    team_halves, _players = build_aggregates()
    aggregates = tournament_aggregates(team_halves)
    h2_goals = aggregates["stats"]["goals"]["h2_share"]

    contexts: dict[str, MatchContext] = {}
    missing: list[str] = []
    for match in sorted(set(matches)):
        left, right = split_match(match)
        event = next((event for event in events if event_matches(event, left, right)), None)
        if event is None:
            missing.append(f"{match}: no ESPN event")
            continue
        odds = _event_odds(event, session)
        if odds is None:
            missing.append(f"{match}: no ESPN pickcenter odds")
            continue
        if names_match(left, odds.home_name):
            p_left, p_draw = odds.p_home, odds.p_draw
        elif names_match(left, odds.away_name):
            p_left, p_draw = odds.p_away, odds.p_draw
        else:
            missing.append(f"{match}: cannot orient odds {odds.home_name}/{odds.away_name}")
            continue
        goals = ScorelineModel.from_market(p_left, p_draw, odds.total_goals, h2_share=h2_goals)
        counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates, neutral_venue=True, tournament=True)
        contexts[match] = MatchContext(
            match=match,
            kickoff=datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
            left=left,
            right=right,
            odds=odds,
            goals=goals,
            counts=counts,
        )
    return contexts, missing


def _side(spec: MarketSpec, ctx: MatchContext) -> str:
    if spec.team is None:
        raise ValueError("team side requested for teamless market")
    if names_match(ctx.left, spec.team):
        return "home"
    if names_match(ctx.right, spec.team):
        return "away"
    raise ValueError(f"cannot orient team {spec.team!r} in {ctx.match!r}")


def price_count_spec(spec: MarketSpec, ctx: MatchContext) -> float:
    if spec.family == "team_count_ge":
        return ctx.counts.p_team_count_ge(spec.stat, _side(spec, ctx), spec.threshold, spec.period)
    if spec.family == "total_count_ge":
        return ctx.counts.p_total_count_ge(spec.stat, spec.threshold, spec.period)
    if spec.family == "both_teams_count_ge":
        return ctx.counts.p_both_teams_ge(spec.stat, spec.threshold, spec.period)
    if spec.family == "team_more_count_than":
        return ctx.counts.p_more_than(spec.stat, _side(spec, ctx), spec.period)
    raise ValueError(f"not an ordinary count market: {spec.family}/{spec.stat}")


def price_rows(
    rows: list[HistoryMarket],
    contexts: dict[str, MatchContext],
    *,
    intensity: dict[str, float],
    candidate: str,
) -> tuple[list[PricedRow], list[str]]:
    saved = count_module.TOURNAMENT_INTENSITY
    count_module.TOURNAMENT_INTENSITY = intensity
    priced: list[PricedRow] = []
    skipped: list[str] = []
    try:
        for row in rows:
            ctx = contexts.get(row.match)
            if ctx is None:
                skipped.append(f"{row.match}: no context")
                continue
            try:
                spec = parse(row.question, teams=ALL_TEAM_NAMES)
                p = clip_prob(price_count_spec(spec, ctx))
            except Exception as exc:
                skipped.append(f"{row.match}: {row.question}: {exc}")
                continue
            priced.append(PricedRow(row=row, probability=p, candidate=candidate))
    finally:
        count_module.TOURNAMENT_INTENSITY = saved
    return priced, skipped


def summarize(priced: list[PricedRow]) -> dict[str, float]:
    if not priced:
        return {"n": 0, "official_rbp": 0.0, "delta_vs_submitted": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    return {
        "n": len(priced),
        "official_rbp": sum(official_alt_rbp(row.row, row.probability) for row in priced),
        "delta_vs_submitted": sum(official_delta_rbp(row.row, row.probability) for row in priced),
        "brier": sum(brier(row.probability, row.row.outcome or 0) for row in priced) / len(priced),
        "mean_p": sum(row.probability for row in priced) / len(priced),
    }


def summarize_by_stat(priced: list[PricedRow]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[PricedRow]] = defaultdict(list)
    for row in priced:
        grouped[row.row.stat or "none"].append(row)
    return {stat: summarize(part) for stat, part in grouped.items()}


def summarize_by_family(priced: list[PricedRow]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[PricedRow]] = defaultdict(list)
    for row in priced:
        grouped[row.row.famkey].append(row)
    return {family: summarize(part) for family, part in grouped.items()}


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return out


def render(
    rows: list[HistoryMarket],
    contexts: dict[str, MatchContext],
    missing_contexts: list[str],
    priced_by_candidate: dict[str, list[PricedRow]],
    skipped_by_candidate: dict[str, list[str]],
    *,
    now: datetime,
) -> str:
    summaries = {name: summarize(priced) for name, priced in priced_by_candidate.items()}
    lines = [
        "# Count Intensity Replay",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- settled_count_rows_requested: {len(rows)}",
        f"- matched_match_contexts: {len(contexts)}",
        f"- missing_match_contexts: {len(missing_contexts)}",
        "- odds_source: ESPN/DraftKings historical pickcenter",
        "- model: current CountPricer structure with candidate `TOURNAMENT_INTENSITY` maps",
        "- limitations: historical Polymarket blends, old intraday odds snapshots, and old referee overrides are not reconstructed",
        "",
        "## Candidate Maps",
        "",
    ]
    lines += _md_table(
        ["candidate", "cards", "sot", "corners", "offsides", "fouls"],
        [
            [
                name,
                f"{m['cards']:.2f}",
                f"{m['sot']:.2f}",
                f"{m['corners']:.2f}",
                f"{m['offsides']:.2f}",
                f"{m['fouls']:.2f}",
            ]
            for name, m in candidate_maps().items()
        ],
    )
    lines += ["", "## Overall Replay", ""]
    lines += _md_table(
        ["candidate", "n", "official_rbp", "delta_vs_submitted", "brier", "mean_p"],
        [
            [
                name,
                str(int(s["n"])),
                f"{s['official_rbp']:.2f}",
                f"{s['delta_vs_submitted']:.2f}",
                f"{s['brier']:.4f}",
                f"{s['mean_p']:.3f}",
            ]
            for name, s in sorted(summaries.items(), key=lambda item: item[1]["official_rbp"], reverse=True)
        ],
    )
    lines += ["", "## Replay By Stat", ""]
    stat_names = sorted({row.row.stat or "none" for priced in priced_by_candidate.values() for row in priced})
    headers = ["stat"]
    for candidate in priced_by_candidate:
        headers += [f"{candidate}_n", f"{candidate}_brier", f"{candidate}_delta"]
    stat_rows = []
    by_stat = {name: summarize_by_stat(priced) for name, priced in priced_by_candidate.items()}
    for stat in stat_names:
        row = [stat]
        for candidate in priced_by_candidate:
            s = by_stat[candidate].get(stat, {"n": 0, "brier": float("nan"), "delta_vs_submitted": 0.0})
            row += [str(int(s["n"])), f"{s['brier']:.4f}", f"{s['delta_vs_submitted']:.2f}"]
        stat_rows.append(row)
    lines += _md_table(headers, stat_rows)
    lines += ["", "## Replay By Family", ""]
    family_names = sorted({row.row.famkey for priced in priced_by_candidate.values() for row in priced})
    family_headers = ["family"]
    for candidate in priced_by_candidate:
        family_headers += [f"{candidate}_n", f"{candidate}_brier", f"{candidate}_delta"]
    family_summary = {name: summarize_by_family(priced) for name, priced in priced_by_candidate.items()}
    family_rows = []
    for family in family_names:
        row = [family]
        for candidate in priced_by_candidate:
            s = family_summary[candidate].get(family, {"n": 0, "brier": float("nan"), "delta_vs_submitted": 0.0})
            row += [str(int(s["n"])), f"{s['brier']:.4f}", f"{s['delta_vs_submitted']:.2f}"]
        family_rows.append(row)
    lines += _md_table(family_headers, family_rows)
    if missing_contexts:
        lines += [
            "",
            "## Missing Match Contexts",
            "",
            *[f"- {item}" for item in missing_contexts[:25]],
        ]
    skipped = sorted({item for values in skipped_by_candidate.values() for item in values})
    if skipped:
        lines += [
            "",
            "## Skipped Rows",
            "",
            *[f"- {item}" for item in skipped[:25]],
        ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- This is a challenger replay for tournament count mean multipliers, not a manual field-policy test.",
        "- A candidate must beat the submitted probabilities and the simpler `uniform090_v3` baseline before it can justify changing the live count level.",
        "- Because old Polymarket/referee/intraday context is not reconstructed, use this as directional evidence alongside the exact live-history count policy backtest.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--start", default="2026-06-11", help="first ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--end", default="2026-06-28", help="last ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_count_rows(sports_predict=args.sports_predict)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    contexts, missing = build_contexts([row.match for row in rows], start=start, end=end)

    priced_by_candidate = {}
    skipped_by_candidate = {}
    for name, intensity in candidate_maps().items():
        priced, skipped = price_rows(rows, contexts, intensity=intensity, candidate=name)
        priced_by_candidate[name] = priced
        skipped_by_candidate[name] = skipped

    text = render(
        rows,
        contexts,
        missing,
        priced_by_candidate,
        skipped_by_candidate,
        now=datetime.now(timezone.utc),
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
