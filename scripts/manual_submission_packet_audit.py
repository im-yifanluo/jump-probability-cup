"""Audit whether the latest manual prediction packet is submission-ready.

This is an execution-control report over `manual_evidence_coverage.md`. It does
not create probabilities, call SportsPredict, read `/predictions`, or submit.

The purpose is to prevent a generated manual sheet from being mistaken for a
locked recommendation when row-level gates still require player, lineup, or
special-count source work. Field averages are not available before prediction
lock, so field-led historical branches are carried as low-confidence no-field
rows rather than required missing inputs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
COVERAGE_LOG = LOG_DIR / "manual_evidence_coverage.md"
RESEARCH_RESOLUTION_CSV = ROOT / "data" / "manual_research_resolutions.csv"
OUT = LOG_DIR / "manual_submission_packet_audit.md"

UNRESOLVED_AFTER_RESEARCH_STATUSES = {
    "researched_no_usable_current_source",
    "researched_no_usable_exact_anchor",
}


REQUIRED_INPUT_STATES = {
    "PLAYER_RESEARCH_REQUIRED",
    "SPECIAL_COUNT_RESEARCH_REQUIRED",
    "NOT_PROMOTED_GUARD",
}
RECHECK_STATES = {
    "CURRENT_ANCHOR_RECHECK",
    "PLAYER_RECHECK",
    "SPECIAL_COUNT_RECHECK",
    "MONITOR",
    "LIMITED_SUPPORT",
}
LOW_CONFIDENCE_STATES = {
    "FIELD_UNAVAILABLE_PRELOCK",
    "PROXY_ONLY",
    "GUARDRAIL_ONLY",
}
READY_STATES = {
    "COVERED",
}


@dataclass(frozen=True)
class SubmissionRow:
    market_id: str
    kickoff_local: str
    match: str
    entry: str
    risk: str
    coverage_state: str
    evidence_status: str
    evidence_area: str
    research_resolution: str
    submission_state: str
    actionability: str
    question: str
    action: str


@dataclass(frozen=True)
class MatchPacket:
    kickoff_local: str
    match: str
    status: str
    rows: int
    ready: int
    required: int
    recheck: int
    low_confidence: int


@dataclass(frozen=True)
class PacketAudit:
    status: str
    rows: tuple[SubmissionRow, ...]
    matches: tuple[MatchPacket, ...]
    source_coverage_log: Path
    source_research_resolution_csv: Path


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(rf"^##+\s+{re.escape(heading)}\s*$", line.strip()):
            start = idx + 1
            break
    if start is None:
        return []
    while start < len(lines) and not lines[start].lstrip().startswith("|"):
        start += 1
    if start >= len(lines):
        return []
    headers = [_clean(part) for part in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean(part) for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def load_research_resolutions(path: Path = RESEARCH_RESOLUTION_CSV) -> dict[tuple[str, str], dict[str, str]]:
    """Load manual research outcomes keyed by market id or match/question.

    The resolution CSV is for completed targeted research that did not produce
    a usable exact source. It should not make a row ready; it only prevents the
    overnight/morning queue from repeatedly treating the same negative search
    as untouched work.
    """

    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            status = (row.get("status") or "").strip()
            if status not in UNRESOLVED_AFTER_RESEARCH_STATUSES:
                continue
            market_id = (row.get("market_id") or "").strip()
            match = (row.get("match") or "").strip()
            question = (row.get("question") or "").strip()
            if market_id:
                out[("id", market_id)] = row
            if match and question:
                out[(match, question)] = row
    return out


def research_resolution_for(row: dict[str, str], resolutions: dict[tuple[str, str], dict[str, str]]) -> dict[str, str] | None:
    market_id = row.get("market_id", "")
    if market_id and ("id", market_id) in resolutions:
        return resolutions[("id", market_id)]
    return resolutions.get((row.get("match", ""), row.get("question", "")))


def resolution_label(resolution: dict[str, str] | None) -> str:
    if not resolution:
        return "-"
    status = (resolution.get("status") or "").strip()
    source = (resolution.get("source") or "").strip()
    return f"{status}{f' via {source}' if source else ''}"


def submission_state(coverage_state: str, resolution: dict[str, str] | None = None) -> str:
    resolution_status = (resolution or {}).get("status", "")
    if coverage_state in REQUIRED_INPUT_STATES:
        if resolution_status in UNRESOLVED_AFTER_RESEARCH_STATUSES:
            return "RECHECK_REQUIRED"
        return "REQUIRED_INPUT_MISSING"
    if coverage_state in RECHECK_STATES:
        return "RECHECK_REQUIRED"
    if coverage_state in LOW_CONFIDENCE_STATES:
        return "LOW_CONFIDENCE_PROVISIONAL"
    if coverage_state in READY_STATES:
        return "READY"
    return "UNKNOWN_REVIEW"


def row_action(state: str, gate: str, resolution: dict[str, str] | None = None) -> str:
    if resolution:
        note = (resolution.get("notes") or "").strip()
        source = (resolution.get("source") or "").strip()
        research_note = "; researched unresolved"
        if source:
            research_note += f" via {source}"
        if note:
            research_note += f": {note}"
        gate = f"{gate}{research_note}" if gate else research_note.lstrip("; ")
    if state == "REQUIRED_INPUT_MISSING":
        return gate or "resolve required player/source input before treating this as locked"
    if state == "RECHECK_REQUIRED":
        return gate or "recheck source/lineup/current market before lock"
    if state == "LOW_CONFIDENCE_PROVISIONAL":
        return gate or "proxy/guardrail row; enter only as calibrated provisional probability if deadline forces"
    if state == "READY":
        return "ready under current evidence"
    return gate or "manual review required"


def row_actionability(
    coverage_state: str,
    evidence_area: str,
    submission_state: str,
    resolution: dict[str, str] | None = None,
) -> str:
    """Classify whether a row can be worked now or only rechecked near lock.

    This is an execution bucket, not a probability model. It exists so the
    nightly/morning/pre-match runs spend time on rows that can actually be
    cleared before lock while treating unavailable field averages as known
    no-field risk.
    """

    if resolution and submission_state == "RECHECK_REQUIRED":
        return "NEAR_LOCK_RESEARCHED_SOURCE_RECHECK"
    if coverage_state == "PLAYER_RESEARCH_REQUIRED":
        return "NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK"
    if coverage_state == "SPECIAL_COUNT_RESEARCH_REQUIRED":
        return "NOW_COUNT_SOURCE_RESEARCH"
    if coverage_state == "NOT_PROMOTED_GUARD":
        return "NOW_STRATEGY_REVIEW"
    if coverage_state == "CURRENT_ANCHOR_RECHECK":
        return "NEAR_LOCK_ANCHOR_RECHECK"
    if coverage_state == "PLAYER_RECHECK":
        return "NEAR_LOCK_PROP_LINEUP_RECHECK"
    if coverage_state == "SPECIAL_COUNT_RECHECK":
        return "NEAR_LOCK_COUNT_SOURCE_RECHECK"
    if coverage_state == "MONITOR":
        return "NEAR_LOCK_MONITOR_ONLY"
    if coverage_state == "LIMITED_SUPPORT":
        return "NEAR_LOCK_LIMITED_SUPPORT_RECHECK"
    if coverage_state == "FIELD_UNAVAILABLE_PRELOCK":
        return "NO_FIELD_ACTION_PRELOCK"
    if coverage_state == "PROXY_ONLY":
        return "LOW_CONFIDENCE_PROXY_ENTRY"
    if coverage_state == "GUARDRAIL_ONLY":
        return "LOW_CONFIDENCE_GUARDRAIL_ENTRY"
    if submission_state == "READY":
        return "READY"
    if evidence_area:
        return "MANUAL_REVIEW"
    return "UNKNOWN_REVIEW"


def match_status(rows: list[SubmissionRow]) -> str:
    states = {row.submission_state for row in rows}
    if "REQUIRED_INPUT_MISSING" in states:
        return "NOT_LOCKED_REQUIRED_INPUTS"
    if "RECHECK_REQUIRED" in states:
        return "NOT_LOCKED_RECHECKS"
    if "LOW_CONFIDENCE_PROVISIONAL" in states:
        return "READY_WITH_LOW_CONFIDENCE_ROWS"
    if states == {"READY"}:
        return "READY_TO_SUBMIT"
    return "NOT_LOCKED_REVIEW"


def overall_status(matches: list[MatchPacket]) -> str:
    statuses = {match.status for match in matches}
    if not matches:
        return "NO_MARKETS"
    if "NOT_LOCKED_REQUIRED_INPUTS" in statuses:
        return "NOT_LOCKED_REQUIRED_INPUTS"
    if "NOT_LOCKED_RECHECKS" in statuses:
        return "NOT_LOCKED_RECHECKS"
    if "READY_WITH_LOW_CONFIDENCE_ROWS" in statuses:
        return "READY_WITH_LOW_CONFIDENCE_ROWS"
    if statuses == {"READY_TO_SUBMIT"}:
        return "READY_TO_SUBMIT"
    return "NOT_LOCKED_REVIEW"


def build_packet(path: Path = COVERAGE_LOG, resolution_path: Path = RESEARCH_RESOLUTION_CSV) -> PacketAudit:
    if not path.exists():
        return PacketAudit(
            status="MISSING_COVERAGE_LOG",
            rows=(),
            matches=(),
            source_coverage_log=path,
            source_research_resolution_csv=resolution_path,
        )
    coverage_rows = _table_after(path.read_text(encoding="utf-8"), "Markets")
    resolutions = load_research_resolutions(resolution_path)
    rows: list[SubmissionRow] = []
    for row in coverage_rows:
        resolution = research_resolution_for(row, resolutions)
        state = submission_state(row.get("coverage_state", ""), resolution)
        rows.append(
            SubmissionRow(
                market_id=row.get("market_id", ""),
                kickoff_local=row.get("kickoff_local", ""),
                match=row.get("match", ""),
                entry=row.get("entry", ""),
                risk=row.get("risk", ""),
                coverage_state=row.get("coverage_state", ""),
                evidence_status=row.get("evidence_status", ""),
                evidence_area=row.get("evidence_area", ""),
                research_resolution=resolution_label(resolution),
                submission_state=state,
                actionability=row_actionability(row.get("coverage_state", ""), row.get("evidence_area", ""), state, resolution),
                question=row.get("question", ""),
                action=row_action(state, row.get("gate", ""), resolution),
            )
        )

    by_match: dict[str, list[SubmissionRow]] = defaultdict(list)
    for row in rows:
        by_match[row.match].append(row)
    matches: list[MatchPacket] = []
    for match, match_rows in sorted(by_match.items(), key=lambda item: (item[1][0].kickoff_local, item[0])):
        counts = Counter(row.submission_state for row in match_rows)
        matches.append(
            MatchPacket(
                kickoff_local=match_rows[0].kickoff_local,
                match=match,
                status=match_status(match_rows),
                rows=len(match_rows),
                ready=counts.get("READY", 0),
                required=counts.get("REQUIRED_INPUT_MISSING", 0),
                recheck=counts.get("RECHECK_REQUIRED", 0),
                low_confidence=counts.get("LOW_CONFIDENCE_PROVISIONAL", 0),
            )
        )
    return PacketAudit(
        status=overall_status(matches),
        rows=tuple(rows),
        matches=tuple(matches),
        source_coverage_log=path,
        source_research_resolution_csv=resolution_path,
    )


def render(audit: PacketAudit, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    row_counts = Counter(row.submission_state for row in audit.rows)
    resolved_rows = sum(1 for row in audit.rows if row.research_resolution != "-")
    match_counts = Counter(match.status for match in audit.matches)
    lines = [
        "# Manual Submission Packet Audit",
        "",
        "Read-only analysis. SportsPredict was not called, `/predictions` was not read, and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- source_coverage_log: {audit.source_coverage_log}",
        f"- source_research_resolution_csv: {audit.source_research_resolution_csv}",
        f"- submission_packet_status: {audit.status}",
        f"- matches: {len(audit.matches)}",
        f"- markets: {len(audit.rows)}",
        f"- ready_rows: {row_counts.get('READY', 0)}",
        f"- required_input_rows: {row_counts.get('REQUIRED_INPUT_MISSING', 0)}",
        f"- recheck_rows: {row_counts.get('RECHECK_REQUIRED', 0)}",
        f"- low_confidence_rows: {row_counts.get('LOW_CONFIDENCE_PROVISIONAL', 0)}",
        f"- researched_unresolved_rows: {resolved_rows}",
        "",
        "## Match Summary",
        "",
        "| kickoff_local | match | status | rows | ready | required | recheck | low_confidence |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for match in audit.matches:
        lines.append(
            f"| {match.kickoff_local} | {match.match} | {match.status} | {match.rows} | "
            f"{match.ready} | {match.required} | {match.recheck} | {match.low_confidence} |"
        )
    lines.extend(["", "## Status Summary", "", "| status | matches |", "| --- | ---: |"])
    for status, count in sorted(match_counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "| submission_state | markets |", "| --- | ---: |"])
    for state, count in sorted(row_counts.items()):
        lines.append(f"| {state} | {count} |")
    action_counts = Counter(row.actionability for row in audit.rows if row.submission_state != "READY")
    lines.extend(["", "| actionability | markets |", "| --- | ---: |"])
    for actionability, count in sorted(action_counts.items()):
        lines.append(f"| {actionability} | {count} |")
    lines.extend(
        [
            "",
            "## Actionability By Match",
            "",
            "| kickoff_local | match | now_research | near_lock_recheck | low_confidence | no_field | monitor |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    by_match_rows: dict[str, list[SubmissionRow]] = defaultdict(list)
    for row in audit.rows:
        by_match_rows[row.match].append(row)
    for match in audit.matches:
        match_rows = by_match_rows.get(match.match, [])
        now_research = sum(1 for row in match_rows if row.actionability.startswith("NOW_"))
        near_lock = sum(1 for row in match_rows if row.actionability.startswith("NEAR_LOCK_"))
        low_conf = sum(1 for row in match_rows if row.actionability.startswith("LOW_CONFIDENCE_"))
        no_field = sum(1 for row in match_rows if row.actionability == "NO_FIELD_ACTION_PRELOCK")
        monitor = sum(1 for row in match_rows if row.actionability in {"MANUAL_REVIEW", "UNKNOWN_REVIEW"})
        lines.append(
            f"| {match.kickoff_local} | {match.match} | {now_research} | {near_lock} | "
            f"{low_conf} | {no_field} | {monitor} |"
        )
    lines.extend(
        [
            "",
            "## Required And Recheck Rows",
            "",
            "| submission_state | actionability | research_resolution | kickoff_local | match | entry | risk | coverage_state | evidence_area | question | action |",
            "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in audit.rows:
        if row.submission_state == "READY":
            continue
        lines.append(
            f"| {row.submission_state} | {row.actionability} | {row.research_resolution} | "
            f"{row.kickoff_local} | {row.match} | {row.entry} | "
            f"{row.risk} | {row.coverage_state} | {row.evidence_area} | {row.question} | {row.action} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `READY_TO_SUBMIT` means the packet has no required, recheck, or low-confidence rows under the current evidence log.",
            "- `NOT_LOCKED_REQUIRED_INPUTS` means at least one hard player or special-count source gate is still unresolved.",
            "- `NOT_LOCKED_RECHECKS` means hard blockers are gone, but current anchors, lineups, or special-count sources still need near-lock review.",
            "- `READY_WITH_LOW_CONFIDENCE_ROWS` means only unavailable-field, proxy, or guardrail rows remain; do not invent a stronger transform.",
            "- `NEAR_LOCK_RESEARCHED_SOURCE_RECHECK` means targeted research found no usable exact source, so keep the provisional model value and recheck lineups/current sources before lock.",
            "- `NOW_*` actionability rows are the nightly/morning research work; `NEAR_LOCK_*` rows are the pre-match recheck work; `NO_FIELD_ACTION_PRELOCK` is unavoidable no-field risk.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-log", type=Path, default=COVERAGE_LOG)
    parser.add_argument("--research-resolution-csv", type=Path, default=RESEARCH_RESOLUTION_CSV)
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    audit = build_packet(args.coverage_log, args.research_resolution_csv)
    text = render(audit)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
