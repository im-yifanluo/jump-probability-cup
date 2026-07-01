import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.policy import manual_decision
from questions.parser import MarketSpec


def test_goal_markets_keep_raw_model_even_with_field():
    spec = MarketSpec("team_win", team="Brazil")
    decision = manual_decision(spec, 0.57, field_probability=0.50)

    assert decision.probability == 57
    assert decision.policy == "raw_odds_goal_model"


def test_card_counts_use_field_when_visible():
    spec = MarketSpec("total_count_ge", stat="cards", threshold=4)
    decision = manual_decision(spec, 0.32, field_probability=0.47)

    assert decision.probability == 47
    assert decision.policy == "card_count_field"


def test_strong_sot_count_uses_model():
    spec = MarketSpec("team_count_ge", team="Norway", stat="sot", threshold=6)
    decision = manual_decision(spec, 0.22, field_probability=0.40)

    assert decision.probability == 22
    assert decision.policy == "strong_sot_count_model"


def test_player_prop_uses_ten_point_asymmetric_cap():
    spec = MarketSpec("player_sot_ge", player="Ayase Ueda", stat="sot", threshold=1)

    below = manual_decision(spec, 0.35, field_probability=0.55)
    above = manual_decision(spec, 0.65, field_probability=0.55)

    assert below.probability == 45
    assert below.policy == "player_asym075_cap10"
    assert above.probability == 55


def test_special_market_keeps_raw_model_even_with_field():
    spec = MarketSpec("card_after_second_hydration", stat="cards")
    decision = manual_decision(spec, 0.67, field_probability=0.50)

    assert decision.probability == 67
    assert decision.policy == "raw_special_model"


def test_unresolved_player_uses_field_when_visible():
    spec = MarketSpec("player_scores", player="Unknown Player")

    decision = manual_decision(spec, 0.42, field_probability=0.58, flags={"PRIOR_ONLY"})

    assert decision.probability == 58
    assert decision.policy == "unresolved_player_use_field"


def test_no_field_player_fallback_keeps_scorers_raw_and_uses_base_for_other_props():
    scorer = MarketSpec("player_scores", player="Cyle Larin")
    sot = MarketSpec("player_sot_ge", player="Iqraam Rayners", stat="sot", threshold=1)

    scorer_decision = manual_decision(
        scorer,
        0.15,
        flags={"GLOBAL_MINUTES_UNCONFIRMED"},
        player_no_field_base=0.28,
    )
    sot_decision = manual_decision(
        sot,
        0.36,
        flags={"GLOBAL_MINUTES_UNCONFIRMED"},
        player_no_field_base=0.28,
    )

    assert scorer_decision.probability == 15
    assert scorer_decision.policy == "player_no_field_raw_scores_else_global_base"
    assert sot_decision.probability == 28
    assert sot_decision.policy == "player_no_field_raw_scores_else_global_base"


def test_no_field_player_fallback_does_not_apply_to_unvalidated_sot_thresholds():
    spec = MarketSpec("player_sot_ge", player="Nestory Irankunda", stat="sot", threshold=2)

    decision = manual_decision(
        spec,
        0.01,
        flags={"LINEUP_NOT_POSTED"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 1
    assert decision.policy == "player_no_field_raw_model_unvalidated_threshold"


def test_no_field_player_fallback_keeps_prior_only_source_unvalidated_raw():
    spec = MarketSpec("player_sot_ge", player="Amad Diallo", stat="sot", threshold=1)

    decision = manual_decision(
        spec,
        0.42,
        flags={"PRIOR_ONLY", "LINEUP_NOT_POSTED"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 42
    assert decision.policy == "research_required_no_field_source_unvalidated"


def test_no_field_player_threshold_marks_prior_only_source_unvalidated():
    spec = MarketSpec("player_sot_ge", player="Alexander Sørloth", stat="sot", threshold=2)

    decision = manual_decision(
        spec,
        0.16,
        flags={"PRIOR_ONLY", "LINEUP_NOT_POSTED"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 16
    assert decision.policy == "player_no_field_raw_model_unvalidated_threshold_and_source"


def test_no_field_player_fallback_does_not_override_current_prop_anchor():
    spec = MarketSpec("player_sot_ge", player="Ayase Ueda", stat="sot", threshold=1)

    decision = manual_decision(
        spec,
        0.33,
        flags={"CURRENT_PROP_MARKET_ANCHOR"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 33
    assert decision.policy == "player_no_field_researched_model"


def test_no_field_player_fallback_does_not_override_confirmed_bench_minutes():
    spec = MarketSpec("player_sot_ge", player="Iqraam Rayners", stat="sot", threshold=1)

    decision = manual_decision(
        spec,
        0.09,
        flags={"CONFIRMED_BENCH"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 9
    assert decision.policy == "player_no_field_confirmed_bench_model"


def test_no_field_player_fallback_does_not_hide_unmatched_posted_lineup():
    spec = MarketSpec("player_sot_ge", player="Unmatched Player", stat="sot", threshold=1)

    decision = manual_decision(
        spec,
        0.36,
        flags={"LINEUP_UNMATCHED_DEFAULT_90"},
        player_no_field_base=0.28,
    )

    assert decision.probability == 36
    assert decision.policy == "research_required_no_field"


def test_global_policy_uses_twelve_point_cap():
    spec = MarketSpec("penalty_awarded")

    below = manual_decision(spec, 0.20, field_probability=0.50)
    above = manual_decision(spec, 0.70, field_probability=0.50)

    assert below.probability == 38
    assert below.policy == "global_asym075_cap12"
    assert above.probability == 50
