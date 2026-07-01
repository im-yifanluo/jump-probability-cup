"""Read-only odds anchors for manual Probability Cup entries.

This module is intentionally independent of the production submission stack.
It never writes to SportsPredict, uses only public odds feeds plus optional
read-only SportsPredict GET calls, and has no pandas/scipy dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ast
import json
import math
import os
from pathlib import Path
import urllib.parse
import urllib.request

from questions.parser import MarketSpec
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match


ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
POLYMARKET_EVENTS = "https://gamma-api.polymarket.com/events"
SPORTSPREDICT_API = "https://api.sportspredict.com/api/v1"
MAX_GOALS = 12
H2_GOAL_SHARE = 0.60


@dataclass(frozen=True)
class OddsAnchor:
    home: str
    away: str
    kickoff: datetime
    p_home: float
    p_draw: float
    p_away: float
    total_goals: float
    source: str


@dataclass(frozen=True)
class GoalAnchor:
    odds: OddsAnchor
    lam_home: float
    lam_away: float
    rho: float
    grid: tuple[tuple[float, ...], ...]


def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> object:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "manual-anchors/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def american_to_decimal(value: str | int | float) -> float:
    if isinstance(value, str):
        clean = value.strip().replace("+", "")
        if clean.lower() in {"even", "evens"}:
            ml = 100.0
        else:
            ml = float(clean)
    else:
        ml = float(value)
    return 1.0 + (ml / 100.0 if ml > 0 else 100.0 / -ml)


def de_margin(*odds: str | int | float) -> tuple[float, ...]:
    raw = [1.0 / american_to_decimal(o) for o in odds]
    total = sum(raw)
    return tuple(p / total for p in raw)


def poisson_pmf(lam: float, n: int = MAX_GOALS) -> tuple[float, ...]:
    out = [math.exp(-lam)]
    for k in range(1, n + 1):
        out.append(out[-1] * lam / k)
    total = sum(out)
    return tuple(x / total for x in out)


def poisson_cdf(k: int, lam: float) -> float:
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return total


def total_goals_mean(line: float, p_over: float) -> float:
    """Poisson total-goals mean implied by an O/U line and de-margined over prob."""
    need = int(line + 0.5)
    lo, hi = 0.20, 6.50
    for _ in range(70):
        mid = (lo + hi) / 2
        if 1 - poisson_cdf(need - 1, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def canonical_team(external_name: str) -> str:
    for team in ALL_TEAM_NAMES:
        if names_match(team, external_name):
            return team
    return external_name


def _moneyline_close(block: dict | None, side: str) -> str | int | float | None:
    if not isinstance(block, dict):
        return None
    side_block = (block.get("moneyline") or {}).get(side) or {}
    return (side_block.get("close") or {}).get("odds") or (side_block.get("open") or {}).get("odds")


def _total_close(block: dict | None, side: str) -> str | int | float | None:
    if not isinstance(block, dict):
        return None
    side_block = ((block.get("total") or {}).get(side) or {})
    return (side_block.get("close") or {}).get("odds") or (side_block.get("open") or {}).get("odds")


def fetch_espn_anchors(date_yyyymmdd: str) -> list[OddsAnchor]:
    board = get_json(ESPN_SCOREBOARD, {"dates": date_yyyymmdd})
    anchors: list[OddsAnchor] = []
    for event in board.get("events", []):
        comp = event["competitions"][0]
        odds_blocks = [block for block in (comp.get("odds") or []) if isinstance(block, dict)]
        if not odds_blocks:
            continue
        odds = odds_blocks[0]
        competitors = {c["homeAway"]: c for c in comp["competitors"]}
        home = canonical_team(competitors["home"]["team"]["displayName"])
        away = canonical_team(competitors["away"]["team"]["displayName"])

        home_ml = _moneyline_close(odds, "home")
        away_ml = _moneyline_close(odds, "away")
        draw_ml = (odds.get("drawOdds") or {}).get("moneyLine")
        if None in (home_ml, draw_ml, away_ml):
            continue
        p_home, p_draw, p_away = de_margin(home_ml, draw_ml, away_ml)

        line = float(odds.get("overUnder") or 2.5)
        over = _total_close(odds, "over")
        under = _total_close(odds, "under")
        p_over = de_margin(over, under)[0] if over and under else 0.5
        anchors.append(
            OddsAnchor(
                home=home,
                away=away,
                kickoff=datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
                p_home=p_home,
                p_draw=p_draw,
                p_away=p_away,
                total_goals=total_goals_mean(line, p_over),
                source="ESPN/DraftKings",
            )
        )
    return anchors


def _loads_maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    return value


def _yes_price(market: dict) -> float | None:
    outcomes = _loads_maybe_json(market.get("outcomes"))
    prices = _loads_maybe_json(market.get("outcomePrices"))
    if not outcomes or not prices:
        return None
    for outcome, price in zip(outcomes, prices):
        if str(outcome).lower() == "yes":
            return float(price)
    return None


def _win_subject(question: str) -> str:
    return question.lower().replace("will ", "", 1).split(" win", 1)[0].strip()


def fetch_polymarket_1x2(home: str, away: str) -> tuple[float, float, float, float] | None:
    events = []
    seen: set[str] = set()
    for params in (
        {"closed": "false", "tag_slug": "soccer", "limit": 100},
        {"closed": "false", "series_slug": "soccer-fifwc", "limit": 100, "offset": 0},
        {"closed": "false", "series_slug": "soccer-fifwc", "limit": 100, "offset": 100},
    ):
        for event in get_json(POLYMARKET_EVENTS, params):
            key = str(event.get("id") or event.get("slug") or event.get("title"))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    for event in events:
        title = event.get("title", "")
        if " vs" not in title.lower():
            continue
        left, _, right = title.partition(" vs")
        pair = {left.strip(), right.lstrip(". ").strip()}
        if not (any(names_match(home, team) for team in pair) and any(names_match(away, team) for team in pair)):
            continue

        p_home = p_draw = p_away = None
        for market in event.get("markets", []):
            question = market.get("question", "")
            price = _yes_price(market)
            if price is None:
                continue
            if "draw" in question.lower():
                p_draw = price
            elif names_match(home, _win_subject(question)):
                p_home = price
            elif names_match(away, _win_subject(question)):
                p_away = price
        if None in (p_home, p_draw, p_away):
            return None
        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total, float(event.get("volume") or 0)
    return None


def blend_with_polymarket(anchor: OddsAnchor) -> OddsAnchor:
    pm = fetch_polymarket_1x2(anchor.home, anchor.away)
    if pm is None:
        return anchor
    p_home, p_draw, p_away, volume = pm
    return OddsAnchor(
        home=anchor.home,
        away=anchor.away,
        kickoff=anchor.kickoff,
        p_home=(anchor.p_home + p_home) / 2,
        p_draw=(anchor.p_draw + p_draw) / 2,
        p_away=(anchor.p_away + p_away) / 2,
        total_goals=anchor.total_goals,
        source=f"{anchor.source}+Polymarket(${volume/1_000_000:.1f}M)",
    )


def rho_bounds(lam_home: float, lam_away: float) -> tuple[float, float]:
    return (
        max(-1.0 / max(lam_home, 1e-6), -1.0 / max(lam_away, 1e-6)) + 1e-3,
        min(1.0 / max(lam_home * lam_away, 1e-6), 1.0) - 1e-3,
    )


def independent_probs(lam_home: float, lam_away: float) -> tuple[float, float, float, float, float, float]:
    home_pmf, away_pmf = poisson_pmf(lam_home), poisson_pmf(lam_away)
    home_win = draw = 0.0
    for h, hp in enumerate(home_pmf):
        for a, ap in enumerate(away_pmf):
            if h > a:
                home_win += hp * ap
            elif h == a:
                draw += hp * ap
    p00 = home_pmf[0] * away_pmf[0]
    p10 = home_pmf[1] * away_pmf[0]
    p11 = home_pmf[1] * away_pmf[1]
    return home_win, draw, p00, p10, p11, home_pmf[0] * away_pmf[1]


def dc_grid(lam_home: float, lam_away: float, rho: float) -> tuple[tuple[float, ...], ...]:
    home_pmf, away_pmf = poisson_pmf(lam_home), poisson_pmf(lam_away)
    rows = [[h * a for a in away_pmf] for h in home_pmf]
    rows[0][0] *= 1 - lam_home * lam_away * rho
    rows[1][0] *= 1 + lam_away * rho
    rows[0][1] *= 1 + lam_home * rho
    rows[1][1] *= 1 - rho
    rows = [[max(0.0, x) for x in row] for row in rows]
    total = sum(sum(row) for row in rows)
    return tuple(tuple(x / total for x in row) for row in rows)


def fit_goal_anchor(anchor: OddsAnchor) -> GoalAnchor:
    """Fit lambdas and Dixon-Coles rho to 1X2 plus total-goals mean."""
    total = anchor.total_goals
    best: tuple[float, float, float] | None = None
    for i in range(50, 951):
        lam_home = total * i / 1000
        lam_away = total - lam_home
        home0, draw0, p00, p10, p11, _p01 = independent_probs(lam_home, lam_away)
        denom = p00 * lam_home * lam_away + p11
        rho = (draw0 - anchor.p_draw) / denom if denom > 1e-9 else 0.0
        lo, hi = rho_bounds(lam_home, lam_away)
        rho = min(hi, max(lo, rho))
        home_win = home0 + p10 * lam_away * rho
        grid = dc_grid(lam_home, lam_away, rho)
        draw = sum(grid[k][k] for k in range(MAX_GOALS + 1))
        err = (home_win - anchor.p_home) ** 2 + (draw - anchor.p_draw) ** 2
        if best is None or err < best[0]:
            best = (err, lam_home, rho)
    assert best is not None
    _, lam_home, rho = best
    lam_away = total - lam_home
    return GoalAnchor(anchor, lam_home, lam_away, rho, dc_grid(lam_home, lam_away, rho))


def find_anchor(anchors: list[OddsAnchor], team_a: str, team_b: str) -> OddsAnchor | None:
    for anchor in anchors:
        pair = {anchor.home, anchor.away}
        if any(names_match(team_a, x) for x in pair) and any(names_match(team_b, x) for x in pair):
            return anchor
    return None


def _side(goal_anchor: GoalAnchor, team: str) -> str:
    if names_match(goal_anchor.odds.home, team):
        return "home"
    if names_match(goal_anchor.odds.away, team):
        return "away"
    raise KeyError(f"{team!r} is not in {goal_anchor.odds.home} vs {goal_anchor.odds.away}")


def _grid_sum(goal_anchor: GoalAnchor, predicate) -> float:
    return sum(
        p
        for h, row in enumerate(goal_anchor.grid)
        for a, p in enumerate(row)
        if predicate(h, a)
    )


def _half_lam(goal_anchor: GoalAnchor, side: str, period: str) -> float:
    lam = goal_anchor.lam_home if side == "home" else goal_anchor.lam_away
    return lam * (H2_GOAL_SHARE if period == "2h" else 1 - H2_GOAL_SHARE)


def p_poisson_ge(lam: float, threshold: int) -> float:
    return sum(poisson_pmf(lam)[threshold:])


def p_strictly_more(lam_a: float, lam_b: float) -> float:
    a, b = poisson_pmf(lam_a), poisson_pmf(lam_b)
    return sum(a[i] * sum(b[:i]) for i in range(1, len(a)))


def price_goal_spec(goal_anchor: GoalAnchor, spec: MarketSpec) -> float | None:
    match spec.family:
        case "team_win":
            side = _side(goal_anchor, spec.team or "")
            return goal_anchor.odds.p_home if side == "home" else goal_anchor.odds.p_away
        case "match_drawn":
            return goal_anchor.odds.p_draw
        case "total_goals":
            if spec.period == "match":
                if spec.direction == ">=":
                    return _grid_sum(goal_anchor, lambda h, a: h + a >= (spec.threshold or 0))
                return _grid_sum(goal_anchor, lambda h, a: h + a <= (spec.threshold or 0))
            lam = _half_lam(goal_anchor, "home", spec.period) + _half_lam(goal_anchor, "away", spec.period)
            return p_poisson_ge(lam, spec.threshold or 0)
        case "btts":
            return _grid_sum(goal_anchor, lambda h, a: h >= 1 and a >= 1)
        case "btts_and_total_ge":
            threshold = spec.threshold or 0
            return _grid_sum(goal_anchor, lambda h, a: h >= 1 and a >= 1 and h + a >= threshold)
        case "team_goals_ge":
            side = _side(goal_anchor, spec.team or "")
            threshold = spec.threshold or 0
            if side == "home":
                return _grid_sum(goal_anchor, lambda h, a: h >= threshold)
            return _grid_sum(goal_anchor, lambda h, a: a >= threshold)
        case "team_scores_in_half":
            side = _side(goal_anchor, spec.team or "")
            return 1 - math.exp(-_half_lam(goal_anchor, side, spec.period))
        case "team_scores_both_halves":
            side = _side(goal_anchor, spec.team or "")
            return (1 - math.exp(-_half_lam(goal_anchor, side, "1h"))) * (
                1 - math.exp(-_half_lam(goal_anchor, side, "2h"))
            )
        case "h2_more_goals_than_h1":
            h1 = goal_anchor.odds.total_goals * (1 - H2_GOAL_SHARE)
            h2 = goal_anchor.odds.total_goals * H2_GOAL_SHARE
            return p_strictly_more(h2, h1)
        case "team_more_goals_2h":
            side = _side(goal_anchor, spec.team or "")
            other = "away" if side == "home" else "home"
            return p_strictly_more(_half_lam(goal_anchor, side, "2h"), _half_lam(goal_anchor, other, "2h"))
        case "ht_tied":
            home = poisson_pmf(_half_lam(goal_anchor, "home", "1h"))
            away = poisson_pmf(_half_lam(goal_anchor, "away", "1h"))
            return sum(h * a for h, a in zip(home, away))
        case "ht_team_winning":
            side = _side(goal_anchor, spec.team or "")
            other = "away" if side == "home" else "home"
            return p_strictly_more(_half_lam(goal_anchor, side, "1h"), _half_lam(goal_anchor, other, "1h"))
        case "team_first_goal_game":
            side = _side(goal_anchor, spec.team or "")
            lam = goal_anchor.lam_home if side == "home" else goal_anchor.lam_away
            return lam / goal_anchor.odds.total_goals * (1 - goal_anchor.grid[0][0])
        case "team_first_goal_2h":
            side = _side(goal_anchor, spec.team or "")
            lam = _half_lam(goal_anchor, side, "2h")
            total = goal_anchor.odds.total_goals * H2_GOAL_SHARE
            return lam / total * (1 - math.exp(-total))
        case "team_win_by_ge":
            side = _side(goal_anchor, spec.team or "")
            margin = spec.threshold or 0
            if side == "home":
                return _grid_sum(goal_anchor, lambda h, a: h - a >= margin)
            return _grid_sum(goal_anchor, lambda h, a: a - h >= margin)
    return None


def sports_predict_key() -> str | None:
    for name in ("SPORTSPREDICT_KEY_1", "SPORTSPREDICT_KEY", "SPORTSPREDICT_KEY_2"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            var, _, value = line.strip().partition("=")
            if var in {"SPORTSPREDICT_KEY_1", "SPORTSPREDICT_KEY", "SPORTSPREDICT_KEY_2"} and value:
                return value.strip().strip("'").strip('"')
    return None


def sp_get(path: str, key: str) -> object:
    return get_json(f"{SPORTSPREDICT_API}{path}", headers={"Authorization": f"Bearer {key}"})


def fetch_sports_predict_open_markets(key: str) -> tuple[list[dict], list[dict]]:
    """Read-only market discovery.

    This intentionally does not read `/predictions`; manual entries should be
    built independently from bot submissions.
    """
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobbies = sp_get(f"/lobbies?event_id={event['id']}", key)
    lobby = lobbies[0]
    matches = sp_get(f"/matches?event_id={event['id']}", key)
    try:
        markets = sp_get(f"/markets?lobby_id={lobby['id']}", key)
    except Exception:
        markets = []
        for match in matches:
            try:
                markets.extend(sp_get(f"/markets?lobby_id={lobby['id']}&match_id={match['id']}", key))
            except Exception:
                continue
    return matches, [m for m in markets if m.get("status") == "open"]
