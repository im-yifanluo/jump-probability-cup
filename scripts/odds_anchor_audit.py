"""Audit live goal/outcome odds anchors for open manual markets.

This is a read-only manual-entry diagnostic. It uses SportsPredict only for GET
market discovery, then compares ESPN/DraftKings 1X2 odds with Polymarket 1X2
when available. It never reads `/predictions` and never submits.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match
from wcdata.odds_feeds import MatchOdds, fetch_match_odds
from wcdata.polymarket import fetch_match_1x2


OUT = ROOT / "data" / "logs" / "odds_anchor_audit.md"

ODDS_GOAL_FAMILIES = {
    "btts",
    "btts_and_total_ge",
    "first_goal_and_other_scores_2h",
    "h2_more_goals_than_h1",
    "ht_team_winning",
    "ht_tied",
    "match_drawn",
    "team_advance",
    "team_clean_sheet",
    "team_first_goal_2h",
    "team_first_goal_game",
    "team_goals_ge",
    "team_more_goals_2h",
    "team_scores_both_halves",
    "team_scores_in_half",
    "team_win",
    "team_win_by_ge",
    "total_goals",
}


@dataclass(frozen=True)
class OpenMatch:
    name: str
    kickoff: datetime
    open_markets: int
    goal_markets: int


@dataclass(frozen=True)
class OrientedBook:
    p_first: float
    p_draw: float
    p_second: float
    espn_home: str
    espn_away: str
    total_goals: float
    source: str
    orientation: str


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def orient_book_odds(match_name: str, odds: MatchOdds) -> OrientedBook:
    """Orient ESPN's home/away odds to SportsPredict's first-listed team."""

    first, second = match_teams(match_name)
    if names_match(first, odds.home_name) and names_match(second, odds.away_name):
        return OrientedBook(
            odds.p_home,
            odds.p_draw,
            odds.p_away,
            odds.home_name,
            odds.away_name,
            odds.total_goals,
            odds.source,
            "first_team_is_espn_home",
        )
    if names_match(first, odds.away_name) and names_match(second, odds.home_name):
        return OrientedBook(
            odds.p_away,
            odds.p_draw,
            odds.p_home,
            odds.home_name,
            odds.away_name,
            odds.total_goals,
            odds.source,
            "first_team_is_espn_away",
        )
    raise LookupError(f"cannot orient ESPN teams {odds.home_name}/{odds.away_name} to {match_name}")


def max_abs_disagreement(book: tuple[float, float, float], pm: tuple[float, float, float] | None) -> float | None:
    if pm is None:
        return None
    return max(abs(a - b) for a, b in zip(book, pm))


def equal_blend_max_shift(max_diff: float | None) -> float | None:
    """Maximum probability movement from book-only to a 50/50 book/PM blend."""

    if max_diff is None:
        return None
    return max_diff / 2


def polymarket_liquidity_tier(pm_volume: float | None) -> str:
    if pm_volume is None:
        return "none"
    if pm_volume < 100_000:
        return "thin"
    if pm_volume < 500_000:
        return "moderate"
    return "liquid"


def classify_source_state(max_diff: float | None, pm_volume: float | None) -> tuple[str, str]:
    if max_diff is None:
        return "BOOK_ONLY", "Polymarket 1X2 unavailable; use ESPN/DraftKings anchor and recheck before lock"
    shift = equal_blend_max_shift(max_diff) or 0.0
    if pm_volume is not None and pm_volume < 100_000:
        if shift < 0.015:
            return "LOW_VOLUME_PM", "Polymarket is thin but aligned; equal-blend shift is small, recheck near lock"
        return "LOW_VOLUME_PM", "Polymarket exists but volume is thin; inspect source freshness before equal blending"
    if max_diff >= 0.10:
        return "HIGH_DISAGREEMENT", "inspect odds source freshness/orientation before trusting equal blend"
    if max_diff >= 0.07:
        return "MEDIUM_DISAGREEMENT", "recheck odds near lock; blend is plausible but not clean"
    return "ALIGNED", "sources agree closely; current blend is acceptable"


def read_open_matches() -> list[OpenMatch]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    matches, markets = fetch_sports_predict_open_markets(key)
    by_id = {m["id"]: m for m in matches}
    open_counts: dict[str, int] = defaultdict(int)
    goal_counts: dict[str, int] = defaultdict(int)
    kickoffs: dict[str, datetime] = {}
    for market in markets:
        match = market.get("match") or {}
        match_id = match.get("id")
        match_row = by_id.get(match_id, match)
        match_name = match_row.get("name") or match.get("name") or ""
        if not match_name:
            continue
        open_counts[match_name] += 1
        kickoffs[match_name] = _dt(match_row["opening_time"])
        try:
            spec = parse(market.get("question", ""), teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if spec.family in ODDS_GOAL_FAMILIES:
            goal_counts[match_name] += 1
    return sorted(
        [
            OpenMatch(
                name=name,
                kickoff=kickoffs[name],
                open_markets=open_counts[name],
                goal_markets=goal_counts[name],
            )
            for name in open_counts
        ],
        key=lambda row: (row.kickoff, row.name),
    )


def audit_rows() -> list[dict]:
    rows = []
    for match in read_open_matches():
        first, second = match_teams(match.name)
        row = {
            "match": match.name,
            "kickoff": match.kickoff,
            "first": first,
            "second": second,
            "open_markets": match.open_markets,
            "goal_markets": match.goal_markets,
            "book": None,
            "pm": None,
            "max_diff": None,
            "blend_shift": None,
            "pm_liquidity": "none",
            "state": "ERROR",
            "action": "",
            "error": "",
        }
        try:
            odds = fetch_match_odds(match.kickoff.strftime("%Y%m%d"), first.split()[0])
            book = orient_book_odds(match.name, odds)
            row["book"] = book
        except Exception as exc:
            row["state"] = "BOOK_ERROR"
            row["action"] = "manual odds research required before lock"
            row["error"] = str(exc)
            rows.append(row)
            continue
        try:
            pm = fetch_match_1x2(first, second)
        except Exception as exc:
            pm = None
            row["error"] = f"polymarket_error: {exc}"
        row["pm"] = pm
        pm_tuple = None if pm is None else (pm.p_home, pm.p_draw, pm.p_away)
        max_diff = max_abs_disagreement((book.p_first, book.p_draw, book.p_second), pm_tuple)
        row["max_diff"] = max_diff
        row["blend_shift"] = equal_blend_max_shift(max_diff)
        row["pm_liquidity"] = polymarket_liquidity_tier(None if pm is None else pm.volume)
        state, action = classify_source_state(max_diff, None if pm is None else pm.volume)
        row["state"] = state
        row["action"] = action
        rows.append(row)
    return rows


def render(rows: list[dict], *, now: datetime | None = None, tz_name: str = "America/Chicago") -> str:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(tz_name)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row["state"])] += 1
    lines = [
        "# Odds Anchor Audit",
        "",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets only",
        "- bot_predictions: not_read",
        "- odds_sources: ESPN/DraftKings public soccer feed; Polymarket Gamma API when available",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- policy_context: goal/outcome backtest keeps raw odds-anchored probabilities; this audit checks source quality, not field-relative strategy",
        "",
        "## Source State Summary",
        "",
        "| state | matches |",
        "| --- | ---: |",
    ]
    for state in ("ALIGNED", "MEDIUM_DISAGREEMENT", "HIGH_DISAGREEMENT", "LOW_VOLUME_PM", "BOOK_ONLY", "BOOK_ERROR"):
        if counts.get(state):
            lines.append(f"| {state} | {counts[state]} |")

    lines.extend(
        [
            "",
            "## Open Match Odds Sources",
            "",
            "| kickoff_local | match | goal_markets | state | ESPN first/draw/second | PM first/draw/second | max_diff | blend_shift | total_goals | PM_volume | PM_liquidity | orientation | action |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in sorted(rows, key=lambda r: (r["kickoff"], r["match"])):
        book: OrientedBook | None = row["book"]
        pm = row["pm"]
        book_txt = (
            "-"
            if book is None
            else f"{book.p_first:.3f}/{book.p_draw:.3f}/{book.p_second:.3f}"
        )
        pm_txt = "-" if pm is None else f"{pm.p_home:.3f}/{pm.p_draw:.3f}/{pm.p_away:.3f}"
        max_diff = "-" if row["max_diff"] is None else f"{row['max_diff']:.3f}"
        blend_shift = "-" if row["blend_shift"] is None else f"{row['blend_shift']:.3f}"
        total_goals = "-" if book is None else f"{book.total_goals:.2f}"
        volume = "-" if pm is None else f"{pm.volume:.0f}"
        orientation = "-" if book is None else f"{book.orientation}; ESPN {book.espn_home} vs {book.espn_away}"
        lines.append(
            f"| {row['kickoff'].astimezone(tz):%Y-%m-%d %H:%M %Z} | {row['match']} | "
            f"{row['goal_markets']} | {row['state']} | {book_txt} | {pm_txt} | "
            f"{max_diff} | {blend_shift} | {total_goals} | {volume} | {row['pm_liquidity']} | "
            f"{orientation} | {row['action']} |"
        )

    errors = [row for row in rows if row.get("error")]
    if errors:
        lines.extend(["", "## Source Errors", "", "| match | error |", "| --- | --- |"])
        for row in errors:
            lines.append(f"| {row['match']} | {str(row['error']).replace('|', '/')} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `ALIGNED` means the current ESPN/DraftKings plus Polymarket blend is operationally clean.",
            "- `blend_shift` is the largest point movement from the ESPN/DraftKings distribution to the current 50/50 book/Polymarket blend; small shifts make liquidity concerns less operationally important.",
            "- `BOOK_ONLY` is not automatically wrong; it means the anchor depends on one public bookmaker feed and should be rechecked near lock.",
            "- `LOW_VOLUME_PM`, `MEDIUM_DISAGREEMENT`, or `HIGH_DISAGREEMENT` should trigger targeted odds-source research before manual entry, because equal blending can hide a stale, thin, or mis-oriented source.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--log", type=Path, default=OUT, help="log path")
    args = ap.parse_args()

    report = render(audit_rows())
    print(report)
    if args.write:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(report, encoding="utf-8")
        print(f"wrote {args.log}")


if __name__ == "__main__":
    main()
