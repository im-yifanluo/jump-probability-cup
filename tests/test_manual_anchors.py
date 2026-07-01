import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.anchors import (
    OddsAnchor,
    de_margin,
    fetch_espn_anchors,
    fetch_polymarket_1x2,
    fit_goal_anchor,
    total_goals_mean,
)


def test_de_margin_american_prices():
    p_home, p_draw, p_away = de_margin(-110, 215, 390)
    assert round(p_home, 3) == 0.501
    assert round(p_draw, 3) == 0.304
    assert round(p_away, 3) == 0.195


def test_goal_anchor_matches_market_probabilities():
    anchor = OddsAnchor(
        home="Croatia",
        away="Ghana",
        kickoff=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        p_home=0.507,
        p_draw=0.304,
        p_away=0.189,
        total_goals=total_goals_mean(2.5, 0.403),
        source="test",
    )
    fitted = fit_goal_anchor(anchor)
    home_win = sum(
        p for h, row in enumerate(fitted.grid) for a, p in enumerate(row) if h > a
    )
    draw = sum(fitted.grid[k][k] for k in range(len(fitted.grid)))
    assert abs(home_win - anchor.p_home) < 0.02
    assert abs(draw - anchor.p_draw) < 0.02


def test_fetch_polymarket_1x2_searches_world_cup_series(monkeypatch):
    event = {
        "id": "rsa-can",
        "title": "South Africa vs. Canada",
        "volume": 4_000_000,
        "markets": [
            {"question": "Will Canada win on 2026-06-28?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.595", "0.405"]'},
            {"question": "Will South Africa win on 2026-06-28?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.155", "0.845"]'},
            {"question": "Will South Africa vs. Canada end in a draw?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.255", "0.745"]'},
        ],
    }
    calls = []

    def fake_get_json(_url, params=None, headers=None):
        calls.append(params or {})
        if (params or {}).get("series_slug") == "soccer-fifwc":
            return [event]
        return []

    monkeypatch.setattr("manual.anchors.get_json", fake_get_json)

    p_home, p_draw, p_away, volume = fetch_polymarket_1x2("South Africa", "Canada")

    assert round(p_home, 3) == 0.154
    assert round(p_draw, 3) == 0.254
    assert round(p_away, 3) == 0.592
    assert volume == 4_000_000
    assert any(call.get("series_slug") == "soccer-fifwc" for call in calls)


def test_fetch_espn_anchors_skips_missing_odds_block(monkeypatch):
    board = {
        "events": [
            {
                "date": "2026-06-28T19:00Z",
                "competitions": [
                    {
                        "odds": [None],
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Brazil"}},
                            {"homeAway": "away", "team": {"displayName": "Mexico"}},
                        ],
                    }
                ],
            }
        ]
    }

    monkeypatch.setattr("manual.anchors.get_json", lambda *_args, **_kwargs: board)

    assert fetch_espn_anchors("20260628") == []
