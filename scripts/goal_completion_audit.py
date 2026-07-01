"""Audit progress against the full manual Probability Cup goal.

This is a local completion-readiness report over generated logs and docs. It
does not call SportsPredict, does not read `/predictions`, and does not submit
anything. The purpose is to prevent partial work from being mistaken for the
full user goal: researched strategies, robust backtests, live data/update
system, timed main-chat runs, deferred low-value tasks, and rewritten docs.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "goal_completion_audit.md"


@dataclass(frozen=True)
class GoalCheck:
    deliverable: str
    status: str
    observed: str
    required: str
    next_action: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- ([A-Za-z0-9_ ./%>=#-]+):\s*(.*)$", line)
        if match:
            out[match.group(1).strip()] = match.group(2).strip()
    return out


def _status_count(text: str, status: str) -> int:
    match = re.search(rf"^\|\s*{re.escape(status)}\s*\|\s*(\d+)\s*\|", text, flags=re.M)
    return int(match.group(1)) if match else 0


def _section_exists(text: str, heading: str) -> bool:
    return bool(re.search(rf"^##+\s+{re.escape(heading)}\s*$", text, flags=re.M))


def _doc_has(path: Path, *needles: str) -> bool:
    if not path.exists():
        return False
    text = _read(path)
    return all(needle in text for needle in needles)


def _log_keys(root: Path, filename: str) -> dict[str, str] | None:
    path = root / "data" / "logs" / filename
    if not path.exists():
        return None
    return _key_values(_read(path))


def _log_text(root: Path, filename: str) -> str | None:
    path = root / "data" / "logs" / filename
    if not path.exists():
        return None
    return _read(path)


def _int(value: str | None) -> int:
    if not value:
        return 0
    value = value.strip().replace(",", "")
    if value in {"", "-"}:
        return 0
    return int(float(value))


def _strategy_counts(matrix: str | None) -> dict[str, int]:
    if not matrix:
        return {}
    labels = [
        "PROMOTED",
        "PROMOTED_LIMITED",
        "CURRENT_SOURCE_RULE",
        "PROXY_ONLY",
        "GUARDRAIL_ONLY",
        "NOT_PROMOTED",
        "REJECTED",
    ]
    return {label: _status_count(matrix, label) for label in labels}


def build_audit(root: Path = ROOT) -> tuple[str, list[GoalCheck], dict[str, str]]:
    docs_dir = root / "docs"
    logs_dir = root / "data" / "logs"
    meta: dict[str, str] = {}
    checks: list[GoalCheck] = []

    full_research = docs_dir / "full_research_program.md"
    methodology = docs_dir / "methodology_sources.md"
    strategies = docs_dir / "strategies.md"
    readme = root / "README.md"
    manual_strategy = docs_dir / "manual_strategy.md"

    matrix_text = _log_text(root, "strategy_evidence_matrix.md")
    matrix_counts = _strategy_counts(matrix_text)
    evaluated_rows = sum(matrix_counts.values())
    active_limited_rows = sum(matrix_counts.get(label, 0) for label in ("PROXY_ONLY", "GUARDRAIL_ONLY", "NOT_PROMOTED"))
    meta["strategy_evaluated_rows"] = str(evaluated_rows)
    meta["active_limited_strategy_rows"] = str(active_limited_rows)

    family_keys = _log_keys(root, "question_family_coverage_audit.md")
    family_status = family_keys.get("question_family_coverage_status", "missing") if family_keys else "missing"
    observed_families = _int(family_keys.get("observed_family_keys") if family_keys else None)
    parser_families = _int(family_keys.get("parser_family_names") if family_keys else None)
    meta["question_family_coverage_status"] = family_status
    checks.append(
        GoalCheck(
            "1_research_all_question_types",
            "PASS"
            if full_research.exists()
            and methodology.exists()
            and strategies.exists()
            and evaluated_rows > 0
            and family_status in {"COMPLETE_PROMOTED_OBSERVED_FAMILIES", "COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS"}
            and observed_families > 0
            and parser_families > 0
            else "FAIL",
            (
                f"strategy_rows={evaluated_rows}, active_limited={active_limited_rows}, "
                f"family_status={family_status}, observed_families={observed_families}, parser_families={parser_families}"
            ),
            "docs and strategy matrix cover observed/parser families with explicit promoted/limited/proxy/guardrail ownership",
            "run question_family_coverage_audit.py, strategy_evidence_matrix.py, and update docs for any uncovered family",
        )
    )

    freshness_text = _log_text(root, "evidence_freshness_audit.md")
    fresh = _status_count(freshness_text or "", "FRESH")
    stale = _status_count(freshness_text or "", "STALE_OR_INCONSISTENT")
    missing = _status_count(freshness_text or "", "MISSING")
    meta["fresh_evidence_rows"] = str(fresh)
    checks.append(
        GoalCheck(
            "2_backtested_strategy_evidence",
            "PASS" if fresh > 0 and stale == 0 and missing == 0 and evaluated_rows > 0 else "FAIL",
            f"fresh={fresh}, stale={stale}, missing={missing}, strategy_rows={evaluated_rows}",
            "all generated evidence checks fresh and strategy rows backed by current logs",
            "rerun stale/missing backtests or audits before claiming optimized strategy completion",
        )
    )

    workflow = _log_keys(root, "manual_workflow_readiness_audit.md")
    workflow_decision = workflow.get("decision", "missing") if workflow else "missing"
    packet_status = workflow.get("submission_packet_status", "missing") if workflow else "missing"
    automation_decision = workflow.get("automation_inventory_decision", "missing") if workflow else "missing"
    now_research = _int(workflow.get("submission_packet_now_research_rows") if workflow else None)
    near_lock = _int(workflow.get("submission_packet_near_lock_recheck_rows") if workflow else None)
    no_field = _int(workflow.get("submission_packet_no_field_rows") if workflow else None)
    meta["manual_workflow_decision"] = workflow_decision
    meta["submission_packet_status"] = packet_status
    meta["automation_inventory_decision"] = automation_decision
    meta["submission_packet_now_research_rows"] = str(now_research)
    checks.append(
        GoalCheck(
            "3_live_data_and_scheduled_system",
            "PASS"
            if automation_decision == "PRIMARY_HEARTBEAT_READY"
            and workflow_decision in {"READY", "READY_WITH_RESEARCH_QUEUE"}
            and packet_status
            in {"READY_TO_SUBMIT", "READY_WITH_LOW_CONFIDENCE_ROWS", "NOT_LOCKED_RECHECKS", "NOT_LOCKED_REQUIRED_INPUTS"}
            else "FAIL",
            (
                f"workflow={workflow_decision}, packet={packet_status}, automation={automation_decision}, "
                f"now_research={now_research}, near_lock={near_lock}, no_field={no_field}"
            ),
            "main-chat heartbeat ready, read-only live workflow usable, row-level packet status explicit",
            "repair automation/workflow failures; then clear NOW_* and NEAR_LOCK_* rows before final entries",
        )
    )

    checks.append(
        GoalCheck(
            "4_deadline_and_remaining_work_scope",
            "PASS" if now_research == 0 and packet_status in {"READY_TO_SUBMIT", "READY_WITH_LOW_CONFIDENCE_ROWS", "NOT_LOCKED_RECHECKS"} else "WARN",
            f"packet={packet_status}, now_research={now_research}, near_lock={near_lock}, no_field={no_field}",
            "no current NOW_* blockers before next final-entry window; low-value tasks deferred separately",
            "work NOW_PROP_ROLE and NOW_COUNT_SOURCE rows first; carry NO_FIELD_ACTION_PRELOCK as documented no-field risk",
        )
    )

    deferred_ok = full_research.exists() and _section_exists(_read(full_research), "Deferred Low-Value Tasks")
    checks.append(
        GoalCheck(
            "4b_deferred_low_value_tasks",
            "PASS" if deferred_ok else "FAIL",
            "deferred_low_value_section=yes" if deferred_ok else "deferred_low_value_section=no",
            "low-value future work documented separately from live entry blockers",
            "update docs/full_research_program.md with deferred low-value tasks",
        )
    )

    docs_ok = (
        _doc_has(readme, "Manual Pipeline", "Current Strategy Summary", "manual_submission_packet_audit.py")
        and _doc_has(strategies, "Manual Strategy Catalog", "Status Labels", "Goal, Win, Team Goal, And Total Goal Markets")
        and _doc_has(manual_strategy, "Manual Entry Strategy", "manual_submission_packet_audit.py")
    )
    checks.append(
        GoalCheck(
            "5_docs_rewritten_for_pipeline_and_strategies",
            "PASS" if docs_ok else "FAIL",
            f"README={readme.exists()}, strategies={strategies.exists()}, manual_strategy={manual_strategy.exists()}",
            "README, strategies.md, and manual_strategy.md describe pipeline, strategies, and packet/readiness controls",
            "update operating docs so a future run can reproduce the workflow without chat memory",
        )
    )

    read_only_logs = [
        _log_keys(root, "manual_workflow_readiness_audit.md"),
        _log_keys(root, "manual_submission_packet_audit.md"),
        _log_keys(root, "strategy_evidence_matrix.md"),
    ]
    read_only_ok = all(keys and keys.get("bot_predictions") == "not_read" for keys in read_only_logs)
    checks.append(
        GoalCheck(
            "safety_manual_only_read_only",
            "PASS" if read_only_ok else "FAIL",
            f"checked_logs={sum(1 for keys in read_only_logs if keys)}",
            "manual workflow evidence reports state bot_predictions=not_read and do not submit",
            "regenerate any report that lacks read-only/manual-only guardrails",
        )
    )

    counts = Counter(check.status for check in checks)
    meta["failures"] = str(counts.get("FAIL", 0))
    meta["warnings"] = str(counts.get("WARN", 0))
    if counts.get("FAIL", 0):
        decision = "GOAL_NOT_READY"
    elif counts.get("WARN", 0):
        decision = "GOAL_IN_PROGRESS"
    else:
        decision = "GOAL_READY"
    return decision, checks, meta


def render(
    decision: str,
    checks: list[GoalCheck],
    meta: dict[str, str],
    *,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(check.status for check in checks)
    lines = [
        "# Goal Completion Audit",
        "",
        "Read-only analysis. SportsPredict was not called, `/predictions` was not read, and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- decision: {decision}",
        f"- failures: {meta.get('failures', '0')}",
        f"- warnings: {meta.get('warnings', '0')}",
        f"- strategy_evaluated_rows: {meta.get('strategy_evaluated_rows', '0')}",
        f"- active_limited_strategy_rows: {meta.get('active_limited_strategy_rows', '0')}",
        f"- question_family_coverage_status: {meta.get('question_family_coverage_status', 'unknown')}",
        f"- manual_workflow_decision: {meta.get('manual_workflow_decision', 'unknown')}",
        f"- submission_packet_status: {meta.get('submission_packet_status', 'unknown')}",
        f"- submission_packet_now_research_rows: {meta.get('submission_packet_now_research_rows', '0')}",
        f"- automation_inventory_decision: {meta.get('automation_inventory_decision', 'unknown')}",
        "",
        "## Status Summary",
        "",
        "| status | checks |",
        "| --- | ---: |",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Deliverable Checks",
            "",
            "| deliverable | status | observed | required | next_action |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for check in checks:
        lines.append(
            f"| {check.deliverable} | {check.status} | {check.observed} | {check.required} | {check.next_action} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `GOAL_READY` means the audit found no missing evidence and no live NOW_* blockers.",
            "- `GOAL_IN_PROGRESS` means the research system is usable but current manual-entry work remains.",
            "- `GOAL_NOT_READY` means at least one required artifact or safety guardrail is missing.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    decision, checks, meta = build_audit(args.root)
    text = render(decision, checks, meta)
    print(text)
    if args.write:
        out = args.root / "data" / "logs" / "goal_completion_audit.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
