import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.polymarket_direct import (  # noqa: E402
    count_prop_anchor_for_market,
    direct_anchor_for_market,
    inspect_player_prop_candidates,
    player_prop_anchor_for_market,
)
from questions.parser import H1, MarketSpec  # noqa: E402


def _event(title: str, markets: list[dict]) -> dict:
    return {"title": title, "markets": markets}


def _market(question: str, outcomes: list[str], prices: list[float], volume: float) -> dict:
    return {
        "question": question,
        "outcomes": outcomes,
        "outcomePrices": prices,
        "volume": volume,
    }


def test_total_goals_anchor_uses_match_total_not_team_total():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [
                _market("Germany vs. Paraguay: Germany O/U 2.5", ["Over", "Under"], [0.39, 0.61], 10_000),
                _market("Germany vs. Paraguay: O/U 2.5", ["Over", "Under"], [0.56, 0.44], 2_000),
            ],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("total_goals", stat="goals", threshold=3),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.560
    assert anchor.market_question == "Germany vs. Paraguay: O/U 2.5"


def test_team_goal_total_anchor_maps_threshold_to_over_line():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [
                _market("Germany vs. Paraguay: O/U 1.5", ["Over", "Under"], [0.785, 0.215], 20_000),
                _market("Germany vs. Paraguay: Germany O/U 1.5", ["Over", "Under"], [0.665, 0.335], 2_000),
            ],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("team_goals_ge", team="Germany", stat="goals", threshold=2),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.665
    assert anchor.market_question == "Germany vs. Paraguay: Germany O/U 1.5"


def test_first_half_team_score_anchor_uses_period_specific_total():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [_market("Germany vs. Paraguay: Germany 1st Half O/U 0.5", ["Over", "Under"], [0.63, 0.37], 2_000)],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("team_scores_in_half", team="Germany", stat="goals", period=H1),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.630


def test_clean_sheet_anchor_maps_to_opponent_under_half_goal():
    events = [
        _event(
            "Argentina vs. Cape Verde - More Markets",
            [_market("Argentina vs. Cape Verde: Cape Verde O/U 0.5", ["Over", "Under"], [0.35, 0.65], 2_000)],
        )
    ]

    anchor = direct_anchor_for_market(
        "ARG vs CPV",
        MarketSpec("team_clean_sheet", team="Argentina", stat="goals"),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.650


def test_first_goal_anchor_rejects_neither_team_market():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [
                _market("Germany vs. Paraguay: Neither team to score first?", ["Yes", "No"], [0.06, 0.94], 20_000)
            ],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("team_first_goal_game", team="Germany", stat="goals"),
        events,
        min_volume=1000,
    )

    assert anchor is None


def test_first_goal_anchor_requires_target_team_subject():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [
                _market("Germany vs. Paraguay: Germany to score first?", ["Yes", "No"], [0.72, 0.28], 2_000),
                _market("Germany vs. Paraguay: Paraguay to score first?", ["Yes", "No"], [0.18, 0.82], 20_000),
            ],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("team_first_goal_game", team="Germany", stat="goals"),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.720
    assert anchor.market_question == "Germany vs. Paraguay: Germany to score first?"


def test_direct_anchor_respects_market_volume_gate():
    events = [
        _event(
            "Germany vs. Paraguay - More Markets",
            [_market("Germany vs. Paraguay: O/U 2.5", ["Over", "Under"], [0.56, 0.44], 999)],
        )
    ]

    anchor = direct_anchor_for_market(
        "GER vs PAR",
        MarketSpec("total_goals", stat="goals", threshold=3),
        events,
        min_volume=1000,
    )

    assert anchor is None


def test_player_prop_anchor_maps_exact_scorer_market():
    events = [
        _event(
            "Brazil vs. Japan - Player Props",
            [_market("Matheus Cunha: 1+ goals", ["Yes", "No"], [0.315, 0.685], 2_000)],
        )
    ]

    anchor = player_prop_anchor_for_market(
        "BRA vs JPN",
        MarketSpec("player_scores", player="Matheus Cunha"),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.315
    assert anchor.market_question == "Matheus Cunha: 1+ goals"


def test_player_prop_anchor_requires_exact_threshold_and_volume():
    events = [
        _event(
            "Brazil vs. Japan - Player Props",
            [
                _market("Ayase Ueda: 2+ shots on target", ["Yes", "No"], [0.135, 0.865], 5_000),
                _market("Ayase Ueda: 1+ shots on target", ["Yes", "No"], [0.295, 0.705], 999),
            ],
        )
    ]

    anchor = player_prop_anchor_for_market(
        "BRA vs JPN",
        MarketSpec("player_sot_ge", player="Ayase Ueda", stat="sot", threshold=1),
        events,
        min_volume=1000,
    )

    assert anchor is None


def test_inspect_player_prop_candidates_reports_near_miss_reasons():
    events = [
        _event(
            "Brazil vs. Japan - Player Props",
            [
                _market("Ayase Ueda: 2+ shots on target", ["Yes", "No"], [0.135, 0.865], 5_000),
                _market("Ayase Ueda: 1+ shots on target", ["Yes", "No"], [0.295, 0.705], 999),
                _market("Ayase Ueda: 1+ goals", ["Yes", "No"], [0.180, 0.820], 4_000),
            ],
        )
    ]

    candidates = inspect_player_prop_candidates(
        "BRA vs JPN",
        MarketSpec("player_sot_ge", player="Ayase Ueda", stat="sot", threshold=1),
        events,
        min_volume=1000,
    )

    by_question = {candidate.market_question: candidate for candidate in candidates}
    assert by_question["Ayase Ueda: 1+ shots on target"].status == "below_volume"
    assert "$999 < $1,000" in by_question["Ayase Ueda: 1+ shots on target"].reason
    assert by_question["Ayase Ueda: 2+ shots on target"].status == "prop_mismatch"
    assert by_question["Ayase Ueda: 2+ shots on target"].expected_prop == "1+ shots on target"
    assert by_question["Ayase Ueda: 1+ goals"].status == "prop_mismatch"


def test_player_prop_anchor_allows_middle_name_expansion():
    events = [
        _event(
            "Germany vs. Paraguay - Player Props",
            [_market("Julio César Enciso: 1+ shots on target", ["Yes", "No"], [0.255, 0.745], 2_000)],
        )
    ]

    anchor = player_prop_anchor_for_market(
        "GER vs PAR",
        MarketSpec("player_sot_ge", player="Julio Enciso", stat="sot", threshold=1),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.255


def test_count_corner_anchor_maps_match_total():
    events = [
        _event(
            "Netherlands vs. Morocco - Total Corners",
            [
                _market("Netherlands vs. Morocco: Netherlands O/U 4.5 Corners", ["Over", "Under"], [0.57, 0.43], 5_000),
                _market("Netherlands vs. Morocco: O/U 9.5 Total Corners", ["Over", "Under"], [0.47, 0.53], 2_000),
            ],
        )
    ]

    anchor = count_prop_anchor_for_market(
        "NED vs MAR",
        MarketSpec("total_count_ge", stat="corners", threshold=10),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.470
    assert anchor.market_question == "Netherlands vs. Morocco: O/U 9.5 Total Corners"


def test_count_corner_anchor_maps_team_total_without_using_match_total():
    events = [
        _event(
            "Netherlands vs. Morocco - Total Corners",
            [
                _market("Netherlands vs. Morocco: O/U 4.5 Total Corners", ["Over", "Under"], [0.80, 0.20], 5_000),
                _market("Netherlands vs. Morocco: Netherlands O/U 4.5 Corners", ["Over", "Under"], [0.57, 0.43], 2_000),
            ],
        )
    ]

    anchor = count_prop_anchor_for_market(
        "NED vs MAR",
        MarketSpec("team_count_ge", team="Netherlands", stat="corners", threshold=5),
        events,
        min_volume=1000,
    )

    assert anchor is not None
    assert round(anchor.probability, 3) == 0.570
    assert anchor.market_question == "Netherlands vs. Morocco: Netherlands O/U 4.5 Corners"


def test_count_corner_anchor_respects_volume_gate():
    events = [
        _event(
            "Netherlands vs. Morocco - Total Corners",
            [_market("Netherlands vs. Morocco: O/U 9.5 Total Corners", ["Over", "Under"], [0.47, 0.53], 999)],
        )
    ]

    anchor = count_prop_anchor_for_market(
        "NED vs MAR",
        MarketSpec("total_count_ge", stat="corners", threshold=10),
        events,
        min_volume=1000,
    )

    assert anchor is None
