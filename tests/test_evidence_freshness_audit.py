import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_freshness_audit import build_rows, expected_family_counts, render  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


INTEGRITY = """
# Backtest Integrity Audit

- sports_predict_results_rows: 714
- safe_exact_rows_for_strategy_scoring: 8
- unsafe_question_overwrite_exact_rows: 11

## Safe Exact Rows By Family

| family | rows |
| --- | ---: |
| team_win | 2 |
| total_goals/goals | 1 |
| team_count_ge/sot | 1 |
| total_count_ge/cards | 1 |
| player_scores | 1 |
| player_sot_ge/sot | 1 |
| penalty_or_red | 1 |
"""


def test_expected_family_counts_from_integrity_table():
    counts = expected_family_counts(INTEGRITY)

    assert counts == {
        "odds_goal": 3,
        "count": 2,
        "player": 2,
        "penalty_red": 1,
    }


def test_build_rows_flags_stale_and_fresh_artifacts(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "manual_policy_backtest.md", "- settled_rows: 8")
    _write(
        log_dir / "brier_decomposition_audit.md",
        """
        - run_utc: 2026-06-28T22:57:45+00:00
        - settled_rows: 8
        - decision: diagnostic_only_no_probability_transform
        """,
    )
    _write(log_dir / "manual_calibration_backtest.md", "- settled_rows: 7")
    _write(log_dir / "goal_policy_backtest.md", "- settled_goal_rows: 3")
    _write(log_dir / "count_policy_backtest.md", "- settled_count_rows: 2")
    _write(log_dir / "count_no_field_policy_backtest.md", "- settled_count_rows: 2")
    _write(log_dir / "player_policy_backtest.md", "- settled_player_rows: 2")
    _write(log_dir / "player_no_field_policy_backtest.md", "- settled_player_rows: 2")
    _write(log_dir / "penalty_red_policy_backtest.md", "- settled_penalty_red_rows: 1")
    _write(log_dir / "special_official_results_audit.md", "- sports_predict_results_rows: 714")
    _write(log_dir / "polymarket_direct_snapshot_score.md", "- sports_predict_results_rows: 700")
    _write(log_dir / "strategy_evidence_matrix.md", "- run_utc: 2026-06-28T23:00:00+00:00")
    _write(
        log_dir / "promotion_strength_audit.md",
        """
        - run_utc: 2026-06-28T23:01:00+00:00
        - audited_areas: 8
        - warning_rows: 6
        """,
    )
    _write(
        log_dir / "odds_conversion_backtest.md",
        """
        - run_utc: 2026-06-28T22:57:30+00:00
        - decision: not_promoted_keep_multiplicative_devig
        - rows_1x2: 100
        - rows_over_under_2.5: 50
        """,
    )
    _write(
        log_dir / "elo_history_residual_audit.md",
        """
        - run_utc: 2026-06-28T22:58:00+00:00
        - decision: candidate_history_feature_positive_unpriced_only
        - source_rows: 49405
        - scored_rows: 29902
        """,
    )
    _write(
        log_dir / "player_minutes_uncertainty_audit.md",
        """
        - run_utc: 2026-06-28T22:58:15+00:00
        - decision: proxy_candidate_minutes_uncertainty_positive_not_live_promoted
        - appearances: 9318
        - matches: 314
        """,
    )
    _write(
        log_dir / "count_total_dependence_audit.md",
        """
        - run_utc: 2026-06-28T22:58:16+00:00
        - decision: proxy_candidate_direct_total_nb_positive_not_live_promoted
        - source_matches: 47342
        - scored_market_rows: 535122
        - scored_prediction_rows: 2140488
        """,
    )
    _write(
        log_dir / "count_total_dependence_policy_replay.md",
        """
        - run_utc: 2026-06-28T22:58:17+00:00
        - decision: not_promoted_keep_current_independent_total_count_shape
        - settled_total_count_rows_requested: 32
        - matched_match_contexts: 28
        """,
    )
    _write(
        log_dir / "player_sot_rate_audit.md",
        """
        - run_utc: 2026-06-28T22:58:18+00:00
        - decision: proxy_candidate_app_shrink_k8_positive_for_1plus_only_not_live_promoted
        - market_like_rows: 3883
        - chronological_scored_rows: 3161
        - chronological_matches: 250
        """,
    )
    _write(
        log_dir / "player_goal_involvement_rate_audit.md",
        """
        - run_utc: 2026-06-28T22:58:19+00:00
        - decision: not_promoted_keep_current_goal_involvement_shrinkage
        - market_like_rows: 3883
        - chronological_scored_rows: 3161
        - chronological_matches: 250
        """,
    )
    _write(
        log_dir / "referee_card_adjustment_audit.md",
        """
        - run_utc: 2026-06-28T22:58:20+00:00
        - decision: proxy_candidate_referee_threshold_positive_not_live_promoted
        - source_matches: 37822
        - scored_rows: 149288
        - unique_referees: 353
        """,
    )
    _write(
        log_dir / "special_count_threshold_audit.md",
        """
        - run_utc: 2026-06-28T22:58:25+00:00
        - decision: proxy_candidate_count_threshold_adaptation_positive_not_live_promoted
        - matches: 314
        - tournaments: 6
        - scored_rows: 942
        """,
    )
    _write(
        log_dir / "special_count_source_audit.md",
        """
        - special_count_source_status: INCOMPLETE
        - required_rows: 2
        - no_usable_public_exact_rows: 2
        - near_miss_public_rows: 1
        """,
    )
    _write(
        log_dir / "special_count_source_discovery.md",
        """
        - decision: DISCOVERY_COMPLETE_NO_EXACT_SOURCES
        - rows: 2
        - exact_usable_rows: 0
        - exact_below_volume_rows: 0
        - near_miss_rows: 1
        """,
    )
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 5")
    _write(
        log_dir / "leaderboard_tail_risk.md",
        """
        - run_utc: 2026-06-28T22:59:00+00:00
        - simulated_markets_per_trial: 5
        """,
    )
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 1 |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | PROMOTED | 3 |
        | PROXY_ONLY | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        | PROXY_ONLY | 2 |
        | FIELD_UNAVAILABLE_PRELOCK | 1 |

        | evidence_area | markets |
        | --- | ---: |
        | exact_polymarket_anchors | 1 |

        ## Markets

        | kickoff_local | match | entry | risk | direct_anchor_risk | evidence_status | coverage_state | evidence_area | question | gate |
        | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
        | 2026-06-29 12:00 CDT | A vs B | 44% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will the match have 3 or more total goals? | covered |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 1 |
        | MONITOR | 1 |
        | LOW | 1 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        | PROXY_ONLY | 2 |
        | FIELD_UNAVAILABLE_PRELOCK | 1 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 2 |
        | player | 1 |
        | special_proxy | 2 |
        """,
    )
    _write(
        log_dir / "match_lock_readiness_audit.md",
        """
        ## Match Summary

        | kickoff_local | match | decision | markets |
        | --- | --- | --- | ---: |
        | 2026-06-29 12:00 CDT | A vs B | LOCK_READY | 5 |
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 0 |
        """,
    )
    _write(
        log_dir / "manual_calibration_watchlist.md",
        """
        - source_context: all-open
        - selected_markets: 5
        """,
    )
    _write(
        log_dir / "polymarket_direct_coverage_audit.md",
        """
        ## Coverage Summary

        | status | markets |
        | --- | ---: |
        | promoted_non_player | 1 |
        """,
    )
    _write(
        log_dir / "polymarket_direct_anchor_risk_audit.md",
        """
        - run_utc: 2026-06-28T22:57:00+00:00
        - current_anchor_rows: 1
        """,
    )
    _write(
        log_dir / "odds_anchor_audit.md",
        """
        - run_utc: 2026-06-28T22:58:00+00:00

        ## Open Match Odds Sources

        | match | goal_markets |
        | --- | ---: |
        | A vs B | 2 |
        """,
    )
    _write(
        log_dir / "odds_total_source_audit.md",
        """
        - run_utc: 2026-06-28T22:58:30+00:00
        - total_goal_rows: 1
        """,
    )
    _write(
        log_dir / "player_role_research_audit.md",
        """
        - run_utc: 2026-06-28T22:58:00+00:00

        ## Current Open Player Role Summary

        | match | player | role_status |
        | --- | --- | --- |
        | A vs B | Player One | likely_starter |
        """,
    )
    _write(
        log_dir / "player_prop_diagnostics.md",
        """
        ## Current Open Player Props

        | priority | match | player |
        | --- | --- | --- |
        | HIGH | A vs B | Player One |
        """,
    )
    _write(
        log_dir / "current_player_stats_audit.md",
        """
        - current_player_stats_status: COMPLETE
        - completed_player_appearance_rows: 2301
        - open_player_prop_rows: 1
        - shrink_strength_appearances: 4

        ## Source State Summary

        | source_state | rows |
        | --- | ---: |
        | CURRENT_PLAYER_STATS_SOURCE | 1 |
        """,
    )
    _write(
        log_dir / "current_player_stats_replay.md",
        """
        - run_utc: 2026-06-28T22:58:20+00:00
        - current_player_stats_replay_status: COMPLETE
        - decision: NOT_PROMOTED_DIAGNOSTIC_ONLY
        - settled_player_rows: 80
        - matched_replay_rows: 45
        - best_policy: current_player_stats
        - best_delta_vs_raw: 126.44
        - best_bootstrap_ci_2.5: -123.89
        - best_bootstrap_pr_gt_0: 0.864
        """,
    )
    _write(
        log_dir / "player_prop_boundary_audit.md",
        """
        - run_utc: 2026-06-28T22:58:30+00:00
        - current_player_props: 1
        - high_priority_player_rows: 1
        - medium_priority_player_rows: 0
        - decision: field_or_exact_prop_required_for_unresolved_players_limited_no_field_base
        """,
    )
    _write(
        log_dir / "special_market_live_audit.md",
        """
        - run_utc: 2026-06-28T22:58:00+00:00
        - special_rows: 2
        """,
    )
    _write(
        log_dir / "special_boundary_audit.md",
        """
        - run_utc: 2026-06-28T22:58:30+00:00
        - current_special_rows: 2
        - decision: raw_proxy_with_guardrails_no_shrink_no_official_promotion
        """,
    )
    _write(
        log_dir / "referee_research_audit.md",
        """
        - run_utc: 2026-06-28T22:58:00+00:00

        ## Open Match Referee Availability

        | match | card_markets |
        | --- | ---: |
        | A vs B | 1 |

        ## Open Card Markets

        | match | market_id |
        | --- | --- |
        | A vs B | m1 |
        """,
    )
    _write(
        log_dir / "card_field_component_audit.md",
        """
        - run_utc: 2026-06-28T22:58:30+00:00
        - open_card_like_markets: 1
        - field_required_card_count_rows: 0
        - decision: field_visible_required_no_referee_override_no_new_transform
        """,
    )

    rows, meta = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert meta["safe_exact_rows"] == "8"
    assert meta["blocked_by_safe_join"] == "3"
    assert meta["known_open_markets"] == "5"
    assert meta["live_actionable_markets"] == "3"
    assert meta["open_goal_markets"] == "2"
    assert meta["open_total_goal_rows"] == "1"
    assert meta["open_player_role_rows"] == "1"
    assert meta["open_player_prop_rows"] == "1"
    assert meta["current_player_stats_status"] == "COMPLETE"
    assert meta["current_player_stats_rows"] == "1"
    assert meta["current_player_stats_matched_rows"] == "1"
    assert meta["current_player_stats_replay_status"] == "COMPLETE"
    assert meta["current_player_stats_replay_decision"] == "NOT_PROMOTED_DIAGNOSTIC_ONLY"
    assert meta["current_player_stats_replay_matched_rows"] == "45"
    assert meta["open_player_boundary_rows"] == "1"
    assert meta["open_player_boundary_actionable_rows"] == "1"
    assert meta["count_total_dependence_scored_markets"] == "535122"
    assert meta["count_total_dependence_cup_rows"] == "32"
    assert meta["open_special_rows"] == "2"
    assert meta["open_special_boundary_rows"] == "2"
    assert meta["special_count_source_required_rows"] == "2"
    assert meta["special_count_no_usable_public_exact_rows"] == "2"
    assert meta["special_count_public_near_miss_rows"] == "1"
    assert meta["open_referee_card_rows"] == "1"
    assert meta["open_card_component_rows"] == "1"
    assert meta["field_required_card_count_rows"] == "0"
    assert meta["promotion_strength_audited_areas"] == "8"
    assert meta["promotion_strength_warning_rows"] == "6"
    assert meta["elo_history_scored_rows"] == "29902"
    assert meta["player_minutes_appearances"] == "9318"
    assert meta["player_minutes_matches"] == "314"
    assert meta["player_sot_rate_market_like_rows"] == "3883"
    assert meta["player_sot_rate_chronological_rows"] == "3161"
    assert meta["player_goal_rate_market_like_rows"] == "3883"
    assert meta["player_goal_rate_chronological_rows"] == "3161"
    assert meta["referee_card_source_matches"] == "37822"
    assert meta["referee_card_unique_referees"] == "353"
    assert meta["special_count_threshold_scored_rows"] == "942"
    assert by_artifact["manual_policy_backtest.md"].status == "FRESH"
    assert by_artifact["brier_decomposition_audit.md"].status == "FRESH"
    assert by_artifact["manual_calibration_backtest.md"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["polymarket_direct_snapshot_score.md"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["leaderboard_tail_risk.md"].status == "FRESH"
    assert by_artifact["live_market_risk_audit.md"].status == "FRESH"
    assert by_artifact["manual_evidence_coverage.md"].status == "FRESH"
    assert by_artifact["manual_execution_leverage.md"].status == "FRESH"
    assert by_artifact["match_lock_readiness_audit.md"].status == "FRESH"
    assert by_artifact["manual_execution_leverage.md coverage_state"].status == "FRESH"
    assert by_artifact["polymarket_direct_coverage_audit.md exact_anchors"].status == "FRESH"
    assert by_artifact["polymarket_direct_anchor_risk_audit.md current_anchor_rows"].status == "FRESH"
    assert by_artifact["manual_calibration_watchlist.md selected_markets"].status == "FRESH"
    assert by_artifact["odds_anchor_audit.md goal_markets"].status == "FRESH"
    assert by_artifact["odds_conversion_backtest.md"].status == "FRESH"
    assert by_artifact["elo_history_residual_audit.md"].status == "FRESH"
    assert by_artifact["count_total_dependence_audit.md"].status == "FRESH"
    assert by_artifact["count_total_dependence_policy_replay.md"].status == "FRESH"
    assert by_artifact["player_minutes_uncertainty_audit.md"].status == "FRESH"
    assert by_artifact["player_sot_rate_audit.md"].status == "FRESH"
    assert by_artifact["player_goal_involvement_rate_audit.md"].status == "FRESH"
    assert by_artifact["referee_card_adjustment_audit.md"].status == "FRESH"
    assert by_artifact["special_count_threshold_audit.md"].status == "FRESH"
    assert by_artifact["special_count_source_audit.md public_discovery_join"].status == "FRESH"
    assert by_artifact["odds_total_source_audit.md total_goal_rows"].status == "FRESH"
    assert by_artifact["player_role_research_audit.md player_rows"].status == "FRESH"
    assert by_artifact["player_prop_diagnostics.md player_rows"].status == "FRESH"
    assert by_artifact["current_player_stats_audit.md player_rows"].status == "FRESH"
    assert by_artifact["current_player_stats_replay.md"].status == "FRESH"
    assert by_artifact["player_prop_boundary_audit.md current_player_props"].status == "FRESH"
    assert by_artifact["special_market_live_audit.md special_rows"].status == "FRESH"
    assert by_artifact["special_boundary_audit.md current_special_rows"].status == "FRESH"
    assert by_artifact["referee_research_audit.md card_markets"].status == "FRESH"
    assert by_artifact["card_field_component_audit.md open_cards"].status == "FRESH"
    assert by_artifact["manual_research_queue.md"].status == "FRESH"
    assert by_artifact["strategy_evidence_matrix.md"].status == "FRESH"
    assert by_artifact["promotion_strength_audit.md"].status == "FRESH"


def test_build_rows_flags_live_family_reconciliation_mismatch(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 4")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 4")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | HIGH | 1 |
        | MEDIUM | 1 |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | PROMOTED | 4 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 4 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 1 |
        | MEDIUM | 1 |
        | LOW | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 4 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 2 |
        | player | 1 |
        | special_proxy | 1 |
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | HIGH | 1 |
        | MEDIUM | 1 |
        """,
    )
    _write(
        log_dir / "odds_anchor_audit.md",
        """
        ## Open Match Odds Sources

        | match | goal_markets |
        | --- | ---: |
        | A vs B | 3 |
        """,
    )
    _write(
        log_dir / "player_role_research_audit.md",
        """
        ## Current Open Player Role Summary

        | match | player |
        | --- | --- |
        | A vs B | Player One |
        | A vs B | Player Two |
        """,
    )
    _write(
        log_dir / "player_prop_diagnostics.md",
        """
        ## Current Open Player Props

        | priority | match | player |
        | --- | --- | --- |
        | HIGH | A vs B | Player One |
        | HIGH | A vs B | Player Two |
        """,
    )
    _write(log_dir / "special_market_live_audit.md", "- special_rows: 2")
    _write(
        log_dir / "referee_research_audit.md",
        """
        ## Open Match Referee Availability

        | match | card_markets |
        | --- | ---: |
        | A vs B | 2 |

        ## Open Card Markets

        | match | market_id |
        | --- | --- |
        | A vs B | m1 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["odds_anchor_audit.md goal_markets"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["player_role_research_audit.md player_rows"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["player_prop_diagnostics.md player_rows"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["special_market_live_audit.md special_rows"].status == "STALE_OR_INCONSISTENT"
    assert by_artifact["referee_research_audit.md card_markets"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_stale_special_count_source_discovery_join(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 2")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | PROXY_ONLY | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | LOW | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 0 |
        | player | 0 |
        | special_proxy | 2 |
        """,
    )
    _write(log_dir / "special_market_live_audit.md", "- special_rows: 2")
    _write(log_dir / "special_boundary_audit.md", "- current_special_rows: 2")
    _write(
        log_dir / "special_count_threshold_audit.md",
        """
        - decision: proxy_candidate_count_threshold_adaptation_positive_not_live_promoted
        - matches: 314
        - tournaments: 6
        - scored_rows: 942
        """,
    )
    _write(
        log_dir / "special_count_source_audit.md",
        """
        - special_count_source_status: INCOMPLETE
        - required_rows: 2
        - no_usable_public_exact_rows: 1
        - near_miss_public_rows: 0
        """,
    )
    _write(
        log_dir / "special_count_source_discovery.md",
        """
        - decision: DISCOVERY_COMPLETE_NO_EXACT_SOURCES
        - rows: 2
        - exact_usable_rows: 0
        - exact_below_volume_rows: 0
        - near_miss_rows: 1
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | LOW | 2 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["special_count_source_audit.md public_discovery_join"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_stale_calibration_watchlist_source(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 2")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | HIGH | 1 |
        | LOW | 1 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | PROMOTED | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 1 |
        | LOW | 1 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 0 |
        | player | 0 |
        | special_proxy | 0 |
        """,
    )
    _write(
        log_dir / "manual_calibration_watchlist.md",
        """
        - source_context: latest-log
        - selected_markets: 1
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | HIGH | 1 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["manual_calibration_watchlist.md selected_markets"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_stale_polymarket_exact_anchor_count(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 2")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | CURRENT_SOURCE_RULE | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | CURRENT_ANCHOR_RECHECK | 2 |

        | evidence_area | markets |
        | --- | ---: |
        | exact_polymarket_anchors | 2 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | LOW | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | CURRENT_ANCHOR_RECHECK | 2 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 0 |
        | player | 0 |
        | special_proxy | 0 |
        """,
    )
    _write(
        log_dir / "polymarket_direct_coverage_audit.md",
        """
        ## Coverage Summary

        | status | markets |
        | --- | ---: |
        | promoted_non_player | 1 |
        """,
    )
    _write(
        log_dir / "manual_calibration_watchlist.md",
        """
        - source_context: all-open
        - selected_markets: 2
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["polymarket_direct_coverage_audit.md exact_anchors"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_stale_polymarket_anchor_risk_count(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 2")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | CURRENT_SOURCE_RULE | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | CURRENT_ANCHOR_RECHECK | 2 |

        | evidence_area | markets |
        | --- | ---: |
        | exact_polymarket_anchors | 2 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | LOW | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | CURRENT_ANCHOR_RECHECK | 2 |

        | category | markets |
        | --- | ---: |
        | odds_goal | 0 |
        | player | 0 |
        | special_proxy | 0 |
        """,
    )
    _write(
        log_dir / "polymarket_direct_coverage_audit.md",
        """
        ## Coverage Summary

        | status | markets |
        | --- | ---: |
        | promoted_non_player | 2 |
        """,
    )
    _write(log_dir / "polymarket_direct_anchor_risk_audit.md", "- current_anchor_rows: 1")
    _write(
        log_dir / "manual_calibration_watchlist.md",
        """
        - source_context: all-open
        - selected_markets: 2
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["polymarket_direct_coverage_audit.md exact_anchors"].status == "FRESH"
    assert by_artifact["polymarket_direct_anchor_risk_audit.md current_anchor_rows"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_execution_coverage_state_mismatch(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")
    _write(log_dir / "leaderboard_tail_risk.md", "- simulated_markets_per_trial: 2")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | HIGH | 1 |
        | LOW | 1 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | evidence_status | markets |
        | --- | ---: |
        | PROMOTED | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 1 |
        | FIELD_UNAVAILABLE_PRELOCK | 1 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 1 |
        | LOW | 1 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | HIGH | 1 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["manual_execution_leverage.md coverage_state"].status == "STALE_OR_INCONSISTENT"


def test_research_queue_count_is_filtered_ceiling_not_exact(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 5")
    _write(
        log_dir / "live_market_risk_audit.md",
        """
        ## Risk Summary

        | tier | markets |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 1 |
        | LOW | 2 |
        """,
    )
    _write(
        log_dir / "manual_evidence_coverage.md",
        """
        ## Coverage Summary

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        | FIELD_UNAVAILABLE_PRELOCK | 1 |
        | PROXY_ONLY | 2 |
        """,
    )
    _write(
        log_dir / "manual_execution_leverage.md",
        """
        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 1 |
        | LOW | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | COVERED | 2 |
        | FIELD_UNAVAILABLE_PRELOCK | 1 |
        | PROXY_ONLY | 2 |
        """,
    )
    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | MEDIUM | 1 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["manual_research_queue.md"].status == "FRESH"
    assert by_artifact["manual_research_queue.md"].expected == "<= 2"

    _write(
        log_dir / "manual_research_queue.md",
        """
        ## Queue Summary

        | tier | rows |
        | --- | ---: |
        | HIGH | 3 |
        """,
    )

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["manual_research_queue.md"].status == "STALE_OR_INCONSISTENT"


def test_build_rows_flags_stale_strategy_matrix_timestamp(tmp_path):
    log_dir = tmp_path
    _write(log_dir / "backtest_integrity_audit.md", INTEGRITY)
    _write(log_dir / "strategy_evidence_matrix.md", "- run_utc: 2026-06-28T22:00:00+00:00")
    _write(
        log_dir / "leaderboard_tail_risk.md",
        """
        - run_utc: 2026-06-28T23:00:00+00:00
        - simulated_markets_per_trial: 2
        """,
    )
    _write(log_dir / "tournament_target_audit.md", "- known_open_markets: 2")

    rows, _ = build_rows(log_dir)
    by_artifact = {row.artifact: row for row in rows}

    assert by_artifact["strategy_evidence_matrix.md"].status == "STALE_OR_INCONSISTENT"


def test_render_documents_read_only_and_blocked_join_state(tmp_path):
    _write(tmp_path / "backtest_integrity_audit.md", INTEGRITY)
    rows, meta = build_rows(tmp_path)
    text = render(rows, meta, now=datetime(2026, 6, 28, 23, tzinfo=timezone.utc))

    assert "# Evidence Freshness Audit" in text
    assert "sports_predict: not_called" in text
    assert "blocked_by_safe_join: 3" in text
    assert "Open-market freshness checks" in text
    assert "STALE_OR_INCONSISTENT" in text
