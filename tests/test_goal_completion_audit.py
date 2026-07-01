import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from goal_completion_audit import build_audit, render  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def _write_minimal_docs(root: Path) -> None:
    _write(
        root / "README.md",
        """
        # Jump Probability Cup Manual Research System

        ## Manual Pipeline
        Run manual_submission_packet_audit.py.

        ## Current Strategy Summary
        Current rules.
        """,
    )
    _write(
        root / "docs" / "strategies.md",
        """
        # Manual Strategy Catalog

        ## Status Labels

        ## Goal, Win, Team Goal, And Total Goal Markets
        """,
    )
    _write(
        root / "docs" / "manual_strategy.md",
        """
        # Manual Entry Strategy

        Run manual_submission_packet_audit.py.
        """,
    )
    _write(
        root / "docs" / "methodology_sources.md",
        """
        # Methodology Sources And Guardrails
        """,
    )
    _write(
        root / "docs" / "full_research_program.md",
        """
        # Full Research Program

        ## Deferred Low-Value Tasks

        1. Pinned environment.
        """,
    )


def _write_minimal_logs(root: Path, *, now_research: int = 2) -> None:
    logs = root / "data" / "logs"
    _write(
        logs / "strategy_evidence_matrix.md",
        """
        - bot_predictions: not_read
        - sports_predict: not_called

        ## Status Summary

        | status | rows |
        | --- | ---: |
        | PROMOTED | 3 |
        | PROMOTED_LIMITED | 2 |
        | CURRENT_SOURCE_RULE | 2 |
        | PROXY_ONLY | 8 |
        | GUARDRAIL_ONLY | 10 |
        | NOT_PROMOTED | 5 |
        | REJECTED | 3 |
        """,
    )
    _write(
        logs / "question_family_coverage_audit.md",
        """
        - question_family_coverage_status: COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS
        - observed_family_keys: 62
        - parser_family_names: 41
        """,
    )
    _write(
        logs / "evidence_freshness_audit.md",
        """
        ## Status Summary

        | status | rows |
        | --- | ---: |
        | FRESH | 48 |
        """,
    )
    packet_status = "NOT_LOCKED_REQUIRED_INPUTS" if now_research else "READY_TO_SUBMIT"
    workflow_decision = "READY_WITH_RESEARCH_QUEUE" if now_research else "READY"
    _write(
        logs / "manual_workflow_readiness_audit.md",
        f"""
        - bot_predictions: not_read
        - sports_predict: not_called
        - decision: {workflow_decision}
        - submission_packet_status: {packet_status}
        - submission_packet_now_research_rows: {now_research}
        - submission_packet_near_lock_recheck_rows: 3
        - submission_packet_no_field_rows: 5
        - automation_inventory_decision: PRIMARY_HEARTBEAT_READY
        """,
    )
    _write(
        logs / "manual_submission_packet_audit.md",
        f"""
        - bot_predictions: not_read
        - sports_predict: not_called
        - submission_packet_status: {packet_status}
        - required_input_rows: {now_research}
        """,
    )


def test_goal_completion_audit_reports_in_progress_for_live_now_blockers(tmp_path):
    _write_minimal_docs(tmp_path)
    _write_minimal_logs(tmp_path, now_research=2)

    decision, checks, meta = build_audit(tmp_path)
    text = render(decision, checks, meta, now=datetime(2026, 6, 30, 1, tzinfo=timezone.utc))

    assert decision == "GOAL_IN_PROGRESS"
    assert meta["submission_packet_now_research_rows"] == "2"
    assert any(check.deliverable == "4_deadline_and_remaining_work_scope" and check.status == "WARN" for check in checks)
    assert "GOAL_IN_PROGRESS" in text
    assert "NOW_*" in text


def test_goal_completion_audit_can_be_ready_when_all_artifacts_and_no_now_rows(tmp_path):
    _write_minimal_docs(tmp_path)
    _write_minimal_logs(tmp_path, now_research=0)

    decision, checks, meta = build_audit(tmp_path)

    assert decision == "GOAL_READY"
    assert meta["submission_packet_now_research_rows"] == "0"
    assert all(check.status == "PASS" for check in checks)


def test_goal_completion_audit_fails_when_required_docs_missing(tmp_path):
    _write_minimal_logs(tmp_path, now_research=0)

    decision, checks, _meta = build_audit(tmp_path)

    assert decision == "GOAL_NOT_READY"
    assert any(check.deliverable == "1_research_all_question_types" and check.status == "FAIL" for check in checks)
    assert any(check.deliverable == "5_docs_rewritten_for_pipeline_and_strategies" and check.status == "FAIL" for check in checks)
