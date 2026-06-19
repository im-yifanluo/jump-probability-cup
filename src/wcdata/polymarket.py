"""Polymarket prediction-market 1X2 for World Cup matches (Gamma API, public).

Prediction-market prices are real-money, very liquid for WC matches ($1-3M
volume), and carry a tiny overround (~0.3% vs bookmakers' ~5%) — a sharp,
independent second opinion on the match-outcome probabilities. We blend these
with the bookmaker (ESPN/DraftKings) 1X2 so the anchor doesn't depend on a
single source. Polymarket has no totals market for WC games, so the
over/under line still comes from the bookmaker.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from questions.teams import names_match

GAMMA = "https://gamma-api.polymarket.com/events"


@dataclass
class PolymarketOdds:
    home_name: str
    away_name: str
    p_home: float   # de-margined
    p_draw: float
    p_away: float
    volume: float


def _soccer_events(session: requests.Session) -> list[dict]:
    return session.get(GAMMA, params={"closed": "false", "tag_slug": "soccer", "limit": 200},
                       timeout=20).json()


def _outcome_yes_price(market: dict) -> float | None:
    """The 'Yes' price of a binary Polymarket market."""
    import json
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes, prices = json.loads(outcomes), json.loads(prices)
    if not outcomes or not prices:
        return None
    for o, p in zip(outcomes, prices):
        if o.lower() == "yes":
            return float(p)
    return None


def fetch_match_1x2(home_full: str, away_full: str,
                    session: requests.Session | None = None) -> PolymarketOdds | None:
    """De-margined (p_home, p_draw, p_away) from Polymarket for one fixture,
    or None if the match isn't listed. Robust to home/away ordering."""
    s = session or requests.Session()
    events = _soccer_events(s)

    for e in events:
        title = e.get("title", "")
        if " vs" not in title.lower():
            continue
        left, _, right = title.partition(" vs")
        right = right.lstrip(". ")
        pair = {left.strip(), right.strip()}
        if not (any(names_match(home_full, t) for t in pair)
                and any(names_match(away_full, t) for t in pair)):
            continue

        # Pull the three outcome markets by their question wording
        p_home = p_away = p_draw = None
        for m in e.get("markets", []):
            q = m.get("question", "").lower()
            price = _outcome_yes_price(m)
            if price is None:
                continue
            if "draw" in q:
                p_draw = price
            elif names_match(home_full, _win_subject(q)):
                p_home = price
            elif names_match(away_full, _win_subject(q)):
                p_away = price
        if None in (p_home, p_draw, p_away):
            return None
        total = p_home + p_draw + p_away
        return PolymarketOdds(home_full, away_full, p_home / total, p_draw / total,
                              p_away / total, float(e.get("volume") or 0))
    return None


def _win_subject(question: str) -> str:
    """'will germany win on 2026-06-14?' -> 'germany'."""
    q = question.replace("will ", "").strip()
    return q.split(" win")[0].strip()
