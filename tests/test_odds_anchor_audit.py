from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from odds_anchor_audit import (
    classify_source_state,
    equal_blend_max_shift,
    max_abs_disagreement,
    orient_book_odds,
    polymarket_liquidity_tier,
)
from wcdata.polymarket import fetch_match_1x2
from wcdata.odds_feeds import MatchOdds


def _odds(home="Canada", away="South Africa"):
    return MatchOdds(
        home_name=home,
        away_name=away,
        p_home=0.58,
        p_draw=0.25,
        p_away=0.17,
        total_line=2.5,
        p_over=0.50,
        source="test",
    )


def test_orient_book_odds_handles_reversed_espn_home_away():
    oriented = orient_book_odds("RSA vs CAN", _odds())

    assert oriented.orientation == "first_team_is_espn_away"
    assert oriented.p_first == 0.17
    assert oriented.p_draw == 0.25
    assert oriented.p_second == 0.58


def test_max_abs_disagreement_and_classification():
    assert max_abs_disagreement((0.5, 0.25, 0.25), None) is None
    assert abs(max_abs_disagreement((0.5, 0.25, 0.25), (0.55, 0.23, 0.22)) - 0.05) < 1e-9
    assert equal_blend_max_shift(None) is None
    assert equal_blend_max_shift(0.05) == 0.025

    assert classify_source_state(None, None)[0] == "BOOK_ONLY"
    assert classify_source_state(0.03, 1_000_000)[0] == "ALIGNED"
    assert classify_source_state(0.08, 1_000_000)[0] == "MEDIUM_DISAGREEMENT"
    assert classify_source_state(0.12, 1_000_000)[0] == "HIGH_DISAGREEMENT"
    assert classify_source_state(0.03, 10_000)[0] == "LOW_VOLUME_PM"
    assert classify_source_state(0.02, 80_000)[1].startswith("Polymarket is thin but aligned")


def test_polymarket_liquidity_tiers_are_report_only_diagnostics():
    assert polymarket_liquidity_tier(None) == "none"
    assert polymarket_liquidity_tier(99_999) == "thin"
    assert polymarket_liquidity_tier(100_000) == "moderate"
    assert polymarket_liquidity_tier(499_999) == "moderate"
    assert polymarket_liquidity_tier(500_000) == "liquid"


def test_full_polymarket_client_searches_world_cup_series():
    event = {
        "id": "bra-jpn",
        "title": "Brazil vs. Japan",
        "volume": 2_000_000,
        "markets": [
            {"question": "Will Brazil win on 2026-06-29?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.575", "0.425"]'},
            {"question": "Will Brazil vs. Japan end in a draw?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.255", "0.745"]'},
            {"question": "Will Japan win on 2026-06-29?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.185", "0.815"]'},
        ],
    }

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, _url, params=None, timeout=None):
            self.calls.append(params or {})
            if (params or {}).get("series_slug") == "soccer-fifwc":
                return Response([event])
            return Response([])

    session = Session()
    odds = fetch_match_1x2("Brazil", "Japan", session=session)

    assert odds is not None
    assert round(odds.p_home, 3) == 0.567
    assert round(odds.p_draw, 3) == 0.251
    assert round(odds.p_away, 3) == 0.182
    assert odds.volume == 2_000_000
    assert any(call.get("series_slug") == "soccer-fifwc" for call in session.calls)
