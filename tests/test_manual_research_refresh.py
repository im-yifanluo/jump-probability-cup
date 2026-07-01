import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_research_refresh import build_plan, main, validate_plan, run_plan, CommandSpec  # noqa: E402


def test_build_plan_has_read_only_live_and_readiness_commands():
    plan = build_plan(
        profile="live",
        local_date="2026-06-29",
        espn_date="20260629",
        start_date="2026-06-11",
        sports_predict=True,
        include_polymarket_snapshot=False,
    )
    names = [spec.name for spec in plan]

    assert "manual_odds_anchor" in names
    assert "full_manual_pricing" in names
    assert "manual_workflow_readiness_audit" in names
    assert "goal_completion_audit" in names
    assert "strategy_evidence_matrix" in names
    assert all("submit.py" not in spec.argv for spec in plan)
    assert all("/predictions" not in spec.command_text() for spec in plan)


def test_build_plan_keeps_evidence_and_readiness_dependency_order():
    plan = build_plan(
        profile="full",
        local_date="2026-06-29",
        espn_date="20260629",
        start_date="2026-06-11",
        sports_predict=True,
        include_polymarket_snapshot=False,
    )
    names = [spec.name for spec in plan]

    assert names.index("polymarket_direct_coverage_audit") < names.index("polymarket_direct_snapshot_score")
    assert names.index("manual_research_queue") < names.index("manual_calibration_watchlist")
    assert names.index("manual_execution_leverage") < names.index("card_field_component_audit")
    assert names.index("card_field_component_audit") < names.index("strategy_evidence_matrix")
    assert names.index("strategy_evidence_matrix") < names.index("promotion_strength_audit")
    assert names.index("promotion_strength_audit") < names.index("evidence_freshness_audit")
    assert names.index("evidence_freshness_audit") < names.index("manual_workflow_readiness_audit")
    assert names.index("manual_workflow_readiness_audit") < names.index("goal_completion_audit")


def test_validate_plan_rejects_submission_and_predictions_paths():
    bad_submit = [CommandSpec("bad", "submit", ("python3", "scripts/submit.py"))]
    bad_predictions = [CommandSpec("bad", "predictions", ("python3", "tool.py", "/predictions"))]

    try:
        validate_plan(bad_submit)
    except ValueError as exc:
        assert "submission" in str(exc)
    else:
        raise AssertionError("submit.py plan should be rejected")

    try:
        validate_plan(bad_predictions)
    except ValueError as exc:
        assert "/predictions" in str(exc)
    else:
        raise AssertionError("/predictions plan should be rejected")


def test_dry_run_writes_markdown_log_without_execution(tmp_path):
    log = tmp_path / "refresh.md"
    code = main(
        [
            "--profile",
            "readiness",
            "--local-date",
            "2026-06-29",
            "--espn-date",
            "20260629",
            "--dry-run",
            "--log",
            str(log),
        ]
    )

    text = log.read_text(encoding="utf-8")
    assert code == 0
    assert "# Manual Research Refresh" in text
    assert "- dry_run: true" in text
    assert "| DRY_RUN |" in text
    assert "manual_workflow_readiness_audit" in text
    assert "goal_completion_audit" in text
    assert "bot_predictions: not_read" in text
    assert "submissions: not_called" in text


def test_run_plan_records_per_command_timeout(tmp_path):
    slow = CommandSpec(
        "test",
        "slow_command",
        (sys.executable, "-c", "import time; time.sleep(1)"),
    )

    results = run_plan(
        [slow],
        dry_run=False,
        stop_on_failure=False,
        max_seconds=None,
        command_timeout_seconds=0.01,
        root=tmp_path,
    )

    assert len(results) == 1
    assert results[0].status == "TIMEOUT"
    assert "command exceeded" in results[0].output_tail
