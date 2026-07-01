import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_workflow_readiness_audit import build_readiness, render  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def _automation(path: Path, *, status: str = "ACTIVE") -> None:
    _write(
        path,
        f'''
        version = 1
        id = "probability-cup-nightly-manual-predictions-in-chat"
        kind = "heartbeat"
        status = "{status}"
        rrule = "DTSTART;TZID=America/Chicago:20260629T080000\\nRRULE:FREQ=DAILY;BYHOUR=8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23;BYMINUTE=0,30;BYSECOND=0"
        prompt = "Read docs/manual_strategy.md, docs/strategies.md, docs/full_research_program.md. Do not submit. /predictions is not part of the manual workflow. Run full_manual_pricing.py, brier_decomposition_audit.py, current_player_stats_audit.py, current_player_stats_replay.py, current_tournament_stats_audit.py, lineup_source_audit.py, special_count_source_discovery.py, match_lock_readiness_audit.py, manual_submission_packet_audit.py, manual_workflow_readiness_audit.py, and write manual_predictions.md."
        target_thread_id = "thread"
        ''',
    )


def test_build_readiness_passes_with_research_queue_warnings(tmp_path):
    logs = tmp_path / "data" / "logs"
    automation = tmp_path / "automation.toml"
    _automation(automation)
    _write(
        logs / "evidence_freshness_audit.md",
        """
        ## Status Summary
        | status | rows |
        | --- | ---: |
        | FRESH | 35 |
        """,
    )
    _write(
        logs / "strategy_evidence_matrix.md",
        """
        - bot_predictions: not_read
        - sports_predict: not_called
        """,
    )
    _write(
        logs / "manual_research_queue.md",
        """
        - bot_predictions: not_read

        ## Queue Summary
        | tier | rows |
        | --- | ---: |
        | HIGH | 2 |
        | MEDIUM | 3 |
        """,
    )
    _write(
        logs / "manual_field_capture_audit.md",
        """
        - status: COMPLETE
        - required_rows: 2
        - supplied_rows: 2
        - matched_rows: 2
        - missing_rows: 0
        - blank_rows: 0
        - invalid_rows: 0
        - duplicate_rows: 0
        - extra_rows: 0
        """,
    )
    _write(
        logs / "player_prop_diagnostics.md",
        """
        - player_prop_anchor_status: COMPLETE
        - current_player_rows: 2
        - csv_rows: 2
        - usable: 2
        - current_missing_usable: 0
        - high_priority_missing_usable: 0
        - current_blank: 0
        - current_invalid: 0
        - unused_usable: 0
        - current_prop_anchor_status: COMPLETE
        - current_prop_anchor_rows: 2
        - current_prop_anchor_missing_rows: 0
        - high_priority_missing_current_prop_anchor: 0
        - medium_priority_anchor_rows: 0
        """,
    )
    _write(
        logs / "current_player_stats_audit.md",
        """
        - current_player_stats_status: COMPLETE
        - completed_player_appearance_rows: 2301
        - open_player_prop_rows: 2
        - shrink_strength_appearances: 4
        """,
    )
    _write(
        logs / "current_player_stats_replay.md",
        """
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
        logs / "lineup_source_audit.md",
        """
        - lineup_source_status: COMPLETE
        - player_matches: 1
        - pulled_matches: 1
        - not_posted_matches: 0
        - in_window_not_posted_matches: 0
        - unmatched_players: 0
        - no_event_matches: 0
        """,
    )
    _write(
        logs / "special_count_source_audit.md",
        """
        - special_count_source_status: COMPLETE
        - required_rows: 1
        - complete_two_source_rows: 1
        - single_source_rows: 0
        - blank_rows: 0
        - invalid_rows: 0
        - duplicate_source_rows: 0
        - high_required_rows: 1
        - high_missing_two_source_rows: 0
        - no_usable_public_exact_rows: 0
        - near_miss_public_rows: 0
        """,
    )
    _write(
        logs / "special_count_source_discovery.md",
        """
        - decision: DISCOVERY_COMPLETE_NO_EXACT_SOURCES
        - exact_usable_rows: 0
        - near_miss_rows: 1
        - no_exact_market_rows: 3
        - polymarket_status: FETCHED
        """,
    )
    _write(
        logs / "current_tournament_stats_audit.md",
        """
        - current_tournament_stats_status: COMPLETE
        - completed_matches: 73
        - team_match_rows: 146
        - open_count_source_rows: 35
        - special_count_csv_source1_rows_written: 3
        """,
    )
    _write(
        logs / "match_lock_readiness_audit.md",
        """
        - decision: LOCK_READY
        - matches: 1
        - hard_required_rows: 0
        - recheck_rows: 0
        - low_confidence_rows: 0
        """,
    )
    _write(
        logs / "manual_submission_packet_audit.md",
        """
        - submission_packet_status: READY_TO_SUBMIT
        - matches: 1
        - markets: 15
        - ready_rows: 15
        - required_input_rows: 0
        - recheck_rows: 0
        - low_confidence_rows: 0
        """,
    )
    _write(
        logs / "manual_predictions.md",
        """
        ## 2026-06-29T04:40:08+00:00 full_manual_pricing_v2 local_date=2026-06-29

        - sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only
        - bot_predictions: not_read
        - field_csv: not_supplied

        ### BRA vs JPN
        | question | entry |
        | --- | ---: |
        | Will Brazil win? | 72% |
        """,
    )
    _write(
        logs / "automation_inventory_audit.md",
        """
        - decision: PRIMARY_HEARTBEAT_READY
        - active_stale_or_duplicate_count: 0
        - paused_legacy_count: 3
        - primary_status: ACTIVE
        - primary_schedule_status: OK
        - primary_prompt_status: OK
        """,
    )

    decision, checks, meta = build_readiness(root=tmp_path, local_date="2026-06-29", automation_file=automation)
    text = render(decision, checks, meta, now=datetime(2026, 6, 29, 5, tzinfo=timezone.utc))

    assert decision == "READY_WITH_RESEARCH_QUEUE"
    assert meta["manual_sheet_matches"] == "1"
    assert meta["research_high_rows"] == "2"
    assert meta["field_capture_status"] == "UNAVAILABLE_PRELOCK"
    assert meta["player_prop_anchor_status"] == "COMPLETE"
    assert meta["current_prop_anchor_status"] == "COMPLETE"
    assert meta["current_player_stats_status"] == "COMPLETE"
    assert meta["current_player_stats_replay_status"] == "COMPLETE"
    assert meta["current_player_stats_replay_decision"] == "NOT_PROMOTED_DIAGNOSTIC_ONLY"
    assert meta["lineup_source_status"] == "COMPLETE"
    assert meta["special_count_source_status"] == "COMPLETE"
    assert meta["special_count_source_discovery_status"] == "DISCOVERY_COMPLETE_NO_EXACT_SOURCES"
    assert meta["current_tournament_stats_status"] == "COMPLETE"
    assert meta["match_lock_decision"] == "LOCK_READY"
    assert meta["submission_packet_status"] == "READY_TO_SUBMIT"
    assert meta["automation_schedule_status"] == "ACTIVE"
    assert meta["automation_inventory_decision"] == "PRIMARY_HEARTBEAT_READY"
    assert "field_capture_audit | PASS" in text
    assert "player_prop_anchor_audit | PASS" in text
    assert "current_prop_anchor_coverage | PASS" in text
    assert "current_player_stats_audit | PASS" in text
    assert "current_player_stats_replay | PASS" in text
    assert "lineup_source_audit | PASS" in text
    assert "special_count_source_audit | PASS" in text
    assert "public_no_usable_exact=0" in text
    assert "special_count_source_discovery | WARN" in text
    assert "current_tournament_stats_audit | PASS" in text
    assert "match_lock_readiness | PASS" in text
    assert "manual_submission_packet | PASS" in text
    assert "main_chat_automation_schedule | PASS" in text
    assert "automation_inventory | PASS" in text
    assert "field_csv_visibility | PASS" in text
    assert "`/predictions` was not read" in text


def test_build_readiness_fails_without_manual_sheet(tmp_path):
    logs = tmp_path / "data" / "logs"
    automation = tmp_path / "automation.toml"
    _automation(automation)
    _write(
        logs / "evidence_freshness_audit.md",
        """
        ## Status Summary
        | status | rows |
        | --- | ---: |
        | FRESH | 35 |
        """,
    )
    _write(logs / "strategy_evidence_matrix.md", "- bot_predictions: not_read\n- sports_predict: not_called")
    _write(logs / "manual_research_queue.md", "- bot_predictions: not_read")
    _write(logs / "automation_inventory_audit.md", "- decision: PRIMARY_HEARTBEAT_READY")

    decision, checks, _meta = build_readiness(root=tmp_path, local_date="2026-06-29", automation_file=automation)

    assert decision == "NOT_READY"
    assert any(check.check == "manual_prediction_sheet" and check.status == "FAIL" for check in checks)


def test_build_readiness_treats_incomplete_field_capture_as_prelock_unavailable(tmp_path):
    logs = tmp_path / "data" / "logs"
    automation = tmp_path / "automation.toml"
    _automation(automation)
    _write(
        logs / "evidence_freshness_audit.md",
        """
        ## Status Summary
        | status | rows |
        | --- | ---: |
        | FRESH | 35 |
        """,
    )
    _write(logs / "strategy_evidence_matrix.md", "- bot_predictions: not_read\n- sports_predict: not_called")
    _write(logs / "manual_research_queue.md", "- bot_predictions: not_read")
    _write(logs / "automation_inventory_audit.md", "- decision: PRIMARY_HEARTBEAT_READY")
    _write(
        logs / "manual_field_capture_audit.md",
        """
        - status: INCOMPLETE
        - required_rows: 2
        - supplied_rows: 2
        - matched_rows: 1
        - missing_rows: 0
        - blank_rows: 1
        - invalid_rows: 0
        - duplicate_rows: 0
        - extra_rows: 0
        """,
    )
    _write(
        logs / "player_prop_diagnostics.md",
        """
        - player_prop_anchor_status: INCOMPLETE
        - current_player_rows: 3
        - csv_rows: 3
        - usable: 1
        - current_missing_usable: 2
        - high_priority_missing_usable: 1
        - current_blank: 1
        - current_invalid: 0
        - unused_usable: 0
        - current_prop_anchor_status: PARTIAL_HIGH_PRIORITY_MISSING
        - current_prop_anchor_rows: 1
        - current_prop_anchor_missing_rows: 2
        - high_priority_missing_current_prop_anchor: 1
        - medium_priority_anchor_rows: 1
        """,
    )
    _write(
        logs / "current_player_stats_audit.md",
        """
        - current_player_stats_status: PARTIAL
        - completed_player_appearance_rows: 2000
        - open_player_prop_rows: 3
        - shrink_strength_appearances: 4
        """,
    )
    _write(
        logs / "lineup_source_audit.md",
        """
        - lineup_source_status: PENDING_NOT_IN_WINDOW
        - player_matches: 3
        - pulled_matches: 0
        - not_posted_matches: 3
        - in_window_not_posted_matches: 0
        - unmatched_players: 0
        - no_event_matches: 0
        """,
    )
    _write(
        logs / "special_count_source_audit.md",
        """
        - special_count_source_status: INCOMPLETE
        - required_rows: 4
        - complete_two_source_rows: 0
        - single_source_rows: 0
        - blank_rows: 4
        - invalid_rows: 0
        - duplicate_source_rows: 0
        - high_required_rows: 1
        - high_missing_two_source_rows: 1
        - no_usable_public_exact_rows: 4
        - near_miss_public_rows: 1
        """,
    )
    _write(
        logs / "current_tournament_stats_audit.md",
        """
        - current_tournament_stats_status: COMPLETE
        - completed_matches: 73
        - team_match_rows: 146
        - open_count_source_rows: 35
        - special_count_csv_source1_rows_written: 3
        """,
    )
    _write(
        logs / "match_lock_readiness_audit.md",
        """
        - decision: LOCK_BLOCKED_BY_REQUIRED_INPUTS
        - matches: 1
        - hard_required_rows: 2
        - recheck_rows: 3
        - low_confidence_rows: 1
        """,
    )
    _write(
        logs / "manual_submission_packet_audit.md",
        """
        - submission_packet_status: NOT_LOCKED_REQUIRED_INPUTS
        - matches: 1
        - markets: 15
        - ready_rows: 4
        - required_input_rows: 4
        - recheck_rows: 2
        - low_confidence_rows: 5

        | actionability | markets |
        | --- | ---: |
        | NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK | 1 |
        | NOW_COUNT_SOURCE_RESEARCH | 1 |
        | NEAR_LOCK_ANCHOR_RECHECK | 1 |
        | NEAR_LOCK_PROP_LINEUP_RECHECK | 2 |
        | NEAR_LOCK_RESEARCHED_SOURCE_RECHECK | 1 |
        | NO_FIELD_ACTION_PRELOCK | 5 |
        | LOW_CONFIDENCE_PROXY_ENTRY | 2 |
        | LOW_CONFIDENCE_GUARDRAIL_ENTRY | 3 |
        """,
    )
    _write(
        logs / "manual_predictions.md",
        """
        ## 2026-06-29T04:40:08+00:00 full_manual_pricing_v2 local_date=2026-06-29

        - sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only
        - bot_predictions: not_read

        ### BRA vs JPN
        | question | entry |
        | --- | ---: |
        | Will Brazil win? | 72% |
        """,
    )

    decision, checks, meta = build_readiness(root=tmp_path, local_date="2026-06-29", automation_file=automation)

    assert decision == "READY_WITH_RESEARCH_QUEUE"
    assert meta["field_capture_status"] == "UNAVAILABLE_PRELOCK"
    assert meta["player_prop_anchor_status"] == "INCOMPLETE"
    assert meta["current_prop_anchor_status"] == "PARTIAL_HIGH_PRIORITY_MISSING"
    assert meta["current_player_stats_status"] == "PARTIAL"
    assert meta["lineup_source_status"] == "PENDING_NOT_IN_WINDOW"
    assert meta["special_count_source_status"] == "INCOMPLETE"
    assert meta["current_tournament_stats_status"] == "COMPLETE"
    assert meta["match_lock_decision"] == "LOCK_BLOCKED_BY_REQUIRED_INPUTS"
    assert meta["submission_packet_status"] == "NOT_LOCKED_REQUIRED_INPUTS"
    assert meta["submission_packet_now_research_rows"] == "2"
    assert meta["submission_packet_near_lock_recheck_rows"] == "4"
    assert meta["submission_packet_no_field_rows"] == "5"
    assert meta["submission_packet_low_confidence_action_rows"] == "5"
    assert any(check.check == "field_capture_audit" and check.status == "PASS" for check in checks)
    assert any(check.check == "player_prop_anchor_audit" and check.status == "WARN" for check in checks)
    assert any(check.check == "current_prop_anchor_coverage" and check.status == "WARN" for check in checks)
    assert any(check.check == "current_player_stats_audit" and check.status == "WARN" for check in checks)
    assert any(check.check == "lineup_source_audit" and check.status == "WARN" for check in checks)
    assert any(check.check == "special_count_source_audit" and check.status == "WARN" for check in checks)
    assert any(
        check.check == "special_count_source_audit" and "public_no_usable_exact=4" in check.observed
        for check in checks
    )
    assert any(check.check == "match_lock_readiness" and check.status == "WARN" for check in checks)
    assert any(check.check == "manual_submission_packet" and check.status == "WARN" for check in checks)
    assert any(
        check.check == "manual_submission_packet" and "now_research=2" in check.observed and "near_lock=4" in check.observed
        for check in checks
    )
