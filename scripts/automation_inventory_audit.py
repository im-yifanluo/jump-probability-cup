"""Audit Codex automation state for the manual Probability Cup workflow.

This is a local read-only report over `$HOME/.codex/automations`. It does not
call SportsPredict, does not read `/predictions`, and does not submit anything.
The purpose is to make the in-chat heartbeat the explicit source of scheduled
manual workflow runs and to flag stale active cron duplicates.

Usage:
    python3 scripts/automation_inventory_audit.py --write
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "logs" / "automation_inventory_audit.md"
DEFAULT_AUTOMATION_DIR = Path.home() / ".codex" / "automations"
PRIMARY_ID = "probability-cup-nightly-manual-predictions-in-chat"
KNOWN_LEGACY_IDS = {
    "probability-cup-morning-adjustment-check",
    "probability-cup-nightly-manual-predictions",
    "probability-cup-pre-match-change-checker",
}
REQUIRED_PROMPT_TERMS = (
    "docs/manual_strategy.md",
    "docs/strategies.md",
    "docs/full_research_program.md",
    "full_manual_pricing.py",
    "brier_decomposition_audit.py",
    "current_player_stats_audit.py",
    "current_player_stats_replay.py",
    "current_tournament_stats_audit.py",
    "lineup_source_audit.py",
    "special_count_source_discovery.py",
    "match_lock_readiness_audit.py",
    "manual_submission_packet_audit.py",
    "manual_workflow_readiness_audit.py",
    "manual_predictions.md",
    "player_prop_anchor_status",
    "current_prop_anchor_status",
    "Do not submit",
    "/predictions is not part",
)
FORBIDDEN_PROMPT_TERMS = (
    "field information from the UI only when actually visible",
    "Apply the manual field policy",
    "field is visible/captured",
)


@dataclass(frozen=True)
class AutomationRow:
    automation_id: str
    name: str
    kind: str
    status: str
    role: str
    schedule_status: str
    prompt_status: str
    risk: str
    action: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rrule_hours(rrule: str) -> set[int]:
    match = re.search(r"BYHOUR=([0-9,]+)", rrule)
    if not match:
        return set()
    return {int(part) for part in match.group(1).split(",") if part}


def _rrule_minutes(rrule: str) -> set[int]:
    match = re.search(r"BYMINUTE=([0-9,]+)", rrule)
    if not match:
        return set()
    return {int(part) for part in match.group(1).split(",") if part}


def _manual_relevant(data: dict) -> bool:
    text = " ".join(str(data.get(key, "")) for key in ("id", "name", "prompt")).lower()
    return "probability cup" in text or "probability-cup" in text


def _schedule_ok(data: dict) -> bool:
    rrule = str(data.get("rrule", ""))
    hours = _rrule_hours(rrule)
    minutes = _rrule_minutes(rrule)
    return {9, 23}.issubset(hours) and {0, 30}.issubset(minutes)


def _prompt_ok(data: dict) -> bool:
    prompt = str(data.get("prompt", ""))
    return all(term in prompt for term in REQUIRED_PROMPT_TERMS) and not any(
        term in prompt for term in FORBIDDEN_PROMPT_TERMS
    )


def _load_automations(automation_dir: Path) -> list[dict]:
    rows: list[dict] = []
    if not automation_dir.exists():
        return rows
    for path in sorted(automation_dir.glob("*/automation.toml")):
        try:
            data = tomllib.loads(_read(path))
        except Exception:
            data = {"id": path.parent.name, "name": path.parent.name, "status": "UNREADABLE", "kind": "unknown"}
        data["_path"] = str(path)
        rows.append(data)
    return rows


def classify(data: dict) -> AutomationRow:
    automation_id = str(data.get("id", ""))
    name = str(data.get("name", automation_id))
    kind = str(data.get("kind", ""))
    status = str(data.get("status", ""))
    relevant = _manual_relevant(data)
    schedule_status = "OK" if _schedule_ok(data) else "CHECK"
    prompt_status = "OK" if _prompt_ok(data) else "CHECK"

    if automation_id == PRIMARY_ID:
        role = "PRIMARY_MAIN_CHAT"
        if status == "ACTIVE" and kind == "heartbeat" and data.get("target_thread_id") and schedule_status == "OK" and prompt_status == "OK":
            risk = "OK"
            action = "use this as the authoritative scheduled manual workflow"
        else:
            risk = "PRIMARY_NOT_READY"
            action = "update the main-chat heartbeat prompt, target thread, status, or rrule"
    elif automation_id in KNOWN_LEGACY_IDS:
        role = "LEGACY_STANDALONE"
        if status == "PAUSED":
            risk = "PAUSED_OK"
            action = "keep paused; the active main-chat heartbeat owns the workflow"
        else:
            risk = "STALE_DUPLICATE_ACTIVE"
            action = "pause or update before it can emit stale standalone recommendations"
    elif relevant:
        role = "OTHER_PROBABILITY_CUP"
        if status == "ACTIVE":
            risk = "UNKNOWN_ACTIVE_DUPLICATE"
            action = "inspect and either pause or align with the main-chat workflow"
        else:
            risk = "CHECK"
            action = "inspect if reactivated"
    else:
        role = "UNRELATED"
        risk = "IGNORED"
        action = "not part of the manual Probability Cup workflow"

    return AutomationRow(
        automation_id=automation_id,
        name=name,
        kind=kind,
        status=status,
        role=role,
        schedule_status=schedule_status,
        prompt_status=prompt_status,
        risk=risk,
        action=action,
    )


def build_inventory(automation_dir: Path = DEFAULT_AUTOMATION_DIR) -> tuple[str, list[AutomationRow], dict[str, str]]:
    data_rows = [data for data in _load_automations(automation_dir) if _manual_relevant(data) or str(data.get("id", "")).startswith("probability-cup")]
    rows = [classify(data) for data in data_rows]
    counts = Counter(row.risk for row in rows)
    primary = next((row for row in rows if row.automation_id == PRIMARY_ID), None)
    active_duplicate_count = sum(
        1
        for row in rows
        if row.automation_id != PRIMARY_ID and row.status == "ACTIVE" and row.role != "UNRELATED"
    )
    primary_ready = primary is not None and primary.risk == "OK"
    decision = "PRIMARY_HEARTBEAT_READY" if primary_ready and active_duplicate_count == 0 else "ACTION_REQUIRED"
    meta = {
        "automation_dir": str(automation_dir),
        "total_probability_cup_automations": str(len(rows)),
        "active_stale_or_duplicate_count": str(active_duplicate_count),
        "paused_legacy_count": str(sum(1 for row in rows if row.role == "LEGACY_STANDALONE" and row.status == "PAUSED")),
        "primary_status": primary.status if primary else "missing",
        "primary_schedule_status": primary.schedule_status if primary else "missing",
        "primary_prompt_status": primary.prompt_status if primary else "missing",
        "decision": decision,
        **{f"risk_{key}": str(value) for key, value in sorted(counts.items())},
    }
    return decision, rows, meta


def _table(rows: list[AutomationRow]) -> list[str]:
    lines = [
        "| id | name | kind | status | role | schedule | prompt | risk | action |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.automation_id,
                    row.name,
                    row.kind,
                    row.status,
                    row.role,
                    row.schedule_status,
                    row.prompt_status,
                    row.risk,
                    row.action,
                ]
            )
            + " |"
        )
    return lines


def render(decision: str, rows: list[AutomationRow], meta: dict[str, str], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Automation Inventory Audit",
        "",
        "Read-only local automation audit. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- automation_dir: {meta.get('automation_dir', '?')}",
        f"- decision: {decision}",
        f"- total_probability_cup_automations: {meta.get('total_probability_cup_automations', '0')}",
        f"- active_stale_or_duplicate_count: {meta.get('active_stale_or_duplicate_count', '0')}",
        f"- paused_legacy_count: {meta.get('paused_legacy_count', '0')}",
        f"- primary_status: {meta.get('primary_status', 'unknown')}",
        f"- primary_schedule_status: {meta.get('primary_schedule_status', 'unknown')}",
        f"- primary_prompt_status: {meta.get('primary_prompt_status', 'unknown')}",
        "",
        "## Automations",
        "",
        *_table(rows),
        "",
        "## Interpretation",
        "",
        "- `PRIMARY_HEARTBEAT_READY` means the main-chat heartbeat is the only active Probability Cup workflow.",
        "- Paused legacy standalone crons are acceptable; active legacy crons are not.",
        "- The primary heartbeat must include the manual-only guardrails, full pricing/readiness commands, and the 09:00/23:00/half-hour polling schedule.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation-dir", type=Path, default=DEFAULT_AUTOMATION_DIR)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    decision, rows, meta = build_inventory(args.automation_dir)
    text = render(decision, rows, meta)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
