import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from strategy_evidence_matrix import build_matrix, render  # noqa: E402


def _by_area(rows):
    return {row.area: row for row in rows}


def test_strategy_matrix_classifies_promoted_and_rejected_branches():
    rows = _by_area(build_matrix(ROOT / "data" / "logs"))

    assert rows["combined_manual_policy"].status == "PROMOTED"
    assert rows["global_post_calibration"].status == "REJECTED"
    assert rows["category_linear_pool"].status == "REJECTED"
    assert rows["calibration_watchlist"].status == "GUARDRAIL_ONLY"
    assert rows["brier_decomposition_reliability"].status == "GUARDRAIL_ONLY"
    assert rows["odds_source_quality"].status == "CURRENT_SOURCE_RULE"
    assert rows["odds_conversion_power"].status == "NOT_PROMOTED"
    assert rows["odds_total_source_quality"].status == "GUARDRAIL_ONLY"
    assert rows["elo_history_residual"].status == "PROXY_ONLY"
    assert rows["count_field_policy"].status == "PROMOTED"
    assert rows["card_field_component"].status == "GUARDRAIL_ONLY"
    assert rows["count_intensity_tuning"].status == "NOT_PROMOTED"
    assert rows["count_total_dependence"].status == "PROXY_ONLY"
    assert rows["researched_player_props"].status == "PROMOTED_LIMITED"
    assert rows["player_no_field_fallback"].status == "PROMOTED_LIMITED"
    assert rows["current_player_stats_replay"].status == "PROXY_ONLY"
    assert rows["player_prop_boundary"].status == "GUARDRAIL_ONLY"
    assert rows["player_recent_role"].status == "GUARDRAIL_ONLY"
    assert rows["player_minutes_uncertainty"].status == "PROXY_ONLY"
    assert rows["player_sot_rate_shrinkage"].status == "PROXY_ONLY"
    assert rows["player_goal_involvement_rate_shrinkage"].status == "NOT_PROMOTED"
    assert rows["count_no_field_fallback"].status == "NOT_PROMOTED"
    assert rows["referee_card_context"].status == "GUARDRAIL_ONLY"
    assert rows["referee_card_adjustment"].status == "PROXY_ONLY"
    assert rows["special_markets"].status == "PROXY_ONLY"
    assert rows["special_boundary"].status == "GUARDRAIL_ONLY"
    assert rows["special_count_thresholds"].status == "PROXY_ONLY"
    assert rows["special_prior_shrinkage"].status == "NOT_PROMOTED"
    assert rows["exact_polymarket_anchors"].status == "CURRENT_SOURCE_RULE"
    assert rows["exact_polymarket_anchor_risk"].status == "GUARDRAIL_ONLY"
    assert rows["catch_up_extremizing"].status == "REJECTED"


def test_strategy_matrix_render_includes_read_only_guardrails():
    text = render(
        build_matrix(ROOT / "data" / "logs"),
        now=datetime(2026, 6, 28, 18, tzinfo=timezone.utc),
    )

    assert "# Strategy Evidence Matrix" in text
    assert "bot_predictions: not_read" in text
    assert "sports_predict: not_called" in text
    assert "NOT_PROMOTED" in text
    assert "PROXY_ONLY" in text
    assert "REJECTED" in text
