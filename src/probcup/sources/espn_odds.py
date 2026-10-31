"""Live odds for World Cup fixtures from ESPN's public JSON API (DraftKings).

fetch_match_odds(date, team_hint) returns de-margined 1X2 probabilities and
the expected-total-goals anchor for one fixture:

    MatchOdds(home_name, away_name, p_home, p_draw, p_away, total_line,
              p_over, total_goals)

If ESPN is missing the fixture or its odds, callers fall back to a manual
YAML file (data/inputs/manual_odds.yaml) with the same fields — see
probcup.pipeline.build.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from scipy import stats
import numpy as np

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"


@dataclass
class MatchOdds:
    home_name: str   # ESPN designated home side
    away_name: str
    p_home: float    # de-margined
    p_draw: float
    p_away: float
    total_line: float
    p_over: float    # de-margined P(total > line)
    source: str

    @property
    def total_goals(self) -> float:
        """Expected total goals implied by the totals market: T such that
        P(Poisson(T) >= line + 0.5) = p_over."""
        grid = np.linspace(0.4, 6.0, 1200)
        need = int(self.total_line + 0.5)
        p_grid = 1 - stats.poisson.cdf(need - 1, grid)
        return float(np.interp(self.p_over, p_grid, grid))


def _american_to_decimal(ml: float) -> float:
    ml = float(ml)
    return 1 + (ml / 100 if ml > 0 else 100 / -ml)


def _de_margin(*decimal_odds: float) -> tuple[float, ...]:
    raw = [1 / o for o in decimal_odds]
    s = sum(raw)
    return tuple(p / s for p in raw)


def fetch_match_odds(date_yyyymmdd: str, team_hint: str,
                     session: requests.Session | None = None) -> MatchOdds:
    """Fetch odds for the fixture on `date` whose name contains team_hint.
    ESPN buckets fixtures by US local date, so a late-UTC kickoff can sit on
    the previous day's scoreboard — both days are searched."""
    s = session or requests.Session()
    from datetime import datetime, timedelta
    day = datetime.strptime(date_yyyymmdd, "%Y%m%d")
    event = None
    for date in (date_yyyymmdd, (day - timedelta(days=1)).strftime("%Y%m%d")):
        board = s.get(SCOREBOARD, params={"dates": date}, timeout=20).json()
        event = next((e for e in board.get("events", [])
                      if team_hint.lower() in e["name"].lower()), None)
        if event:
            break
    if event is None:
        raise LookupError(f"no ESPN fixture matching {team_hint!r} on {date_yyyymmdd}")

    comp = event["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")["team"]["displayName"]
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")["team"]["displayName"]

    summary = s.get(SUMMARY, params={"event": event["id"]}, timeout=20).json()
    pick = next(o for o in summary.get("pickcenter", [])
                if o.get("homeTeamOdds", {}).get("moneyLine") is not None)

    p_home, p_draw, p_away = _de_margin(
        _american_to_decimal(pick["homeTeamOdds"]["moneyLine"]),
        _american_to_decimal(pick["drawOdds"]["moneyLine"]),
        _american_to_decimal(pick["awayTeamOdds"]["moneyLine"]),
    )

    # Totals: prefer the closing over/under prices from the odds block
    total_line = float(pick.get("overUnder") or 2.5)
    odds_block = next((o for o in (comp.get("odds") or []) if o), {})
    over = odds_block.get("total", {}).get("over", {}).get("close", {}).get("odds")
    under = odds_block.get("total", {}).get("under", {}).get("close", {}).get("odds")
    if over and under:
        p_over, _ = _de_margin(_american_to_decimal(over.replace("+", "")),
                               _american_to_decimal(under.replace("+", "")))
    else:
        p_over = 0.5

    provider = pick.get("provider", {}).get("name", "ESPN")
    return MatchOdds(home, away, p_home, p_draw, p_away, total_line, p_over,
                     source=f"ESPN/{provider}")
