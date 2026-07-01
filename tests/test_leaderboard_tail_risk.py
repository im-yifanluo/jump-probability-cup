import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from leaderboard_tail_risk import (  # noqa: E402
    alt_rbp,
    combined_count_field,
    combined_goal_field,
    combined_penalty_red_field,
    combined_player_field,
    combined_raw_special,
    fallback_mix,
    hard_field_deviation_side,
    sample_rows_by_mix,
    simulate,
    special_loto_model_probabilities,
    special_outcome,
    special_question_diagnostics,
    special_proxy_probability,
    strategy_specs,
)


def _row(order: int, question: str, you: float, crowd: float, outcome: int, official: float = 1.0):
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question=question,
        you=you,
        crowd=crowd,
        outcome=outcome,
        official_rbp=official,
        family="team_win",
        stat="",
        period="match",
    )


def test_alt_rbp_scales_official_and_delta():
    row = _row(1, "Will Brazil win the match?", 0.60, 0.55, 1, official=2.0)

    # Same probability preserves the official score, then applies stage weight.
    assert alt_rbp(row, 0.60, stage_weight=2.0) == 4.0

    better = alt_rbp(row, 0.70, stage_weight=2.0)
    expected = 2.0 * (2.0 + 100 * ((0.60 - 1) ** 2 - (0.70 - 1) ** 2))
    assert better == expected


def test_fallback_mix_sums_to_sample_size():
    rows = [
        _row(1, "Will Brazil win the match?", 0.60, 0.55, 1),
        replace(_row(2, "Will there be 4 or more total cards shown?", 0.40, 0.45, 0), family="total_count_ge", stat="cards"),
    ]

    mix = fallback_mix(rows, 7)

    assert sum(mix.values()) == 7
    assert set(mix) == {"odds_goal", "count_cards"}


def test_sample_rows_reports_missing_category_fallback():
    rows = [_row(1, "Will Brazil win the match?", 0.60, 0.55, 1)]
    by_category = {"odds_goal": rows}
    import random

    slate, fallback = sample_rows_by_mix(by_category, Counter({"special": 3}), rng=random.Random(1))

    assert len(slate) == 3
    assert fallback == Counter({"special": 3})


def test_hard_field_deviation_side_uses_model_minus_field_direction():
    below = _row(1, "Will Brazil win the match?", 0.40, 0.55, 1)
    above = _row(2, "Will Brazil win the match?", 0.60, 0.55, 1)

    assert hard_field_deviation_side(below) == 0.01
    assert hard_field_deviation_side(above) == 0.99


def test_special_outcome_maps_current_special_questions():
    row = {
        "goal_before_30": True,
        "goal_after_75": False,
        "goal_1h_stoppage": True,
        "goal_2h_stoppage": False,
        "offside_before_30": True,
        "card_after_75": False,
        "substitute_goal": True,
        "own_goal": True,
        "outside_box_goal": False,
        "header_goal": True,
        "any_player_goal": True,
        "any_player_brace": False,
        "any_player_2_sot": True,
        "substitution_before_halftime": False,
        "shots": 21,
        "corners_before_30": 2,
    }

    assert special_outcome(row, "Will a goal be scored before the first hydration break?")
    assert not special_outcome(row, "Will a goal be scored after the second hydration break?")
    assert special_outcome(row, "Will a goal be scored in first-half stoppage time?")
    assert special_outcome(row, "Will either team be ruled offside before the first hydration break?")
    assert not special_outcome(row, "Will a card be shown after the second hydration break, including any extra time?")
    assert special_outcome(row, "Will a substitute score a goal in regulation (90 minutes + stoppage time)?")
    assert special_outcome(row, "Will an own goal be scored in regulation (90 minutes + stoppage time)?")
    assert not special_outcome(row, "Will a goal be scored from outside the penalty area in regulation (90 minutes + stoppage time)?")
    assert special_outcome(row, "Will a header goal be scored in regulation (90 minutes + stoppage time)?")
    assert special_outcome(row, "Will any player score at least 1 goal in regulation (90 minutes + stoppage time)?")
    assert not special_outcome(row, "Will any player score more than 1 goal (excluding own goals) in regulation (90 minutes + stoppage time)?")
    assert special_outcome(row, "Will any player record 2 or more shots on target in regulation (90 minutes + stoppage time)?")
    assert not special_outcome(row, "Will a substitution be made before halftime?")
    assert special_outcome(row, "Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?")
    assert not special_outcome(row, "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?")
    assert special_outcome(row, "Will 2 or more corner kicks be taken before the first hydration break?")


def test_special_proxy_probability_uses_parametric_count_thresholds():
    train = [
        {"tournament": "a", "shots": 18, "corners_before_30": 1, "goal_before_30": True},
        {"tournament": "a", "shots": 22, "corners_before_30": 2, "goal_before_30": False},
        {"tournament": "b", "shots": 30, "corners_before_30": 5, "goal_before_30": True},
    ]

    empirical_shots = sum(row["shots"] >= 22 for row in train) / len(train)
    model_shots = special_proxy_probability(
        train,
        "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
    )
    empirical_corners = sum(row["corners_before_30"] >= 2 for row in train) / len(train)
    model_corners = special_proxy_probability(
        train,
        "Will 2 or more corner kicks be taken before the first hydration break?",
    )
    timing = special_proxy_probability(
        train,
        "Will a goal be scored before the first hydration break?",
    )

    assert 0 < model_shots < 1
    assert model_shots != empirical_shots
    assert 0 < model_corners < 1
    assert model_corners != empirical_corners
    assert timing == 2 / 3


def test_special_loto_model_probabilities_excludes_held_out_tournament():
    data = [
        {"tournament": "a", "shots": 18, "corners_before_30": 1, "goal_before_30": True},
        {"tournament": "a", "shots": 22, "corners_before_30": 2, "goal_before_30": False},
        {"tournament": "b", "shots": 30, "corners_before_30": 5, "goal_before_30": True},
    ]

    probs = special_loto_model_probabilities(
        data,
        "Will a goal be scored before the first hydration break?",
    )

    assert probs[0] == 1.0
    assert probs[1] == 1.0
    assert probs[2] == 0.5


def test_special_question_diagnostics_marks_near_coinflip_proxy_weak():
    data = [
        {"tournament": "a", "goal_before_30": True},
        {"tournament": "a", "goal_before_30": False},
        {"tournament": "b", "goal_before_30": True},
        {"tournament": "b", "goal_before_30": False},
    ]

    [diag] = special_question_diagnostics(
        data,
        ["Will a goal be scored before the first hydration break?"],
    )

    assert diag["n"] == 4
    assert diag["open_markets"] == 1
    assert diag["hit"] == 0.5
    assert diag["edge_vs_50"] == 0
    assert diag["confidence"] == "weak_near_coinflip"


def test_combined_raw_special_keeps_special_model_probability():
    special = _row(
        1,
        "Will a goal be scored before the first hydration break?",
        0.44,
        0.50,
        1,
    )
    card = replace(
        _row(2, "Will there be 4 or more total cards shown?", 0.30, 0.45, 1),
        family="total_count_ge",
        stat="cards",
    )

    assert combined_raw_special(special) == 0.44
    assert combined_raw_special(card) == 0.45


def test_combined_player_field_uses_field_for_player_rows_only():
    player = _row(
        1,
        "Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
        0.33,
        0.48,
        1,
    )
    card = replace(
        _row(2, "Will there be 4 or more total cards shown?", 0.30, 0.45, 1),
        family="total_count_ge",
        stat="cards",
    )

    assert combined_player_field(player) == 0.48
    assert combined_player_field(card) == 0.45


def test_combined_count_field_uses_field_for_non_special_count_rows_only():
    count = replace(
        _row(1, "Will there be 4 or more total cards shown?", 0.30, 0.45, 1),
        family="total_count_ge",
        stat="cards",
    )
    special = _row(
        2,
        "Will a goal be scored before the first hydration break?",
        0.44,
        0.50,
        1,
    )

    assert combined_count_field(count) == 0.45
    assert combined_count_field(special) == 0.44


def test_combined_goal_field_uses_field_for_goal_rows_only():
    goal = _row(
        1,
        "Will regulation (90 minutes + stoppage time) end in a tie?",
        0.24,
        0.31,
        1,
    )
    special = _row(
        2,
        "Will a goal be scored before the first hydration break?",
        0.44,
        0.50,
        1,
    )

    assert combined_goal_field(goal) == 0.31
    assert combined_goal_field(special) == 0.44


def test_combined_penalty_red_field_uses_field_for_penalty_rows_only():
    penalty = _row(
        1,
        "Will a penalty kick be awarded OR a red card be shown in the match?",
        0.37,
        0.44,
        1,
    )
    goal = _row(
        2,
        "Will regulation (90 minutes + stoppage time) end in a tie?",
        0.24,
        0.31,
        1,
    )

    assert combined_penalty_red_field(penalty) == 0.44
    assert combined_penalty_red_field(goal) == 0.24


def test_simulate_outputs_all_strategy_names():
    rows = [
        _row(1, "Will Brazil win the match?", 0.60, 0.55, 1, official=2.0),
        _row(2, "Will Brazil win the match?", 0.40, 0.45, 0, official=2.0),
    ]

    result, fallback = simulate(
        rows,
        Counter({"odds_goal": 2}),
        strategy_specs(),
        trials=5,
        seed=1,
        stage_weight=1.0,
        targets=(1.0,),
    )

    assert fallback == Counter()
    assert {row["strategy"] for row in result} == {spec.name for spec in strategy_specs()}
    assert all("pr_ge_1.00" in row for row in result)


def test_simulate_uses_special_proxy_without_fallback():
    rows = [_row(1, "Will Brazil win the match?", 0.60, 0.55, 1, official=2.0)]
    special = _row(
        2,
        "Will a goal be scored before the first hydration break?",
        0.44,
        0.50,
        1,
        official=100 * ((0.50 - 1) ** 2 - (0.44 - 1) ** 2),
    )

    result, fallback = simulate(
        rows,
        Counter({"special": 2}),
        strategy_specs(),
        trials=3,
        seed=1,
        stage_weight=1.0,
        targets=(1.0,),
        special_proxy_rows=[special],
    )

    assert fallback == Counter()
    assert {row["strategy"] for row in result} == {spec.name for spec in strategy_specs()}
