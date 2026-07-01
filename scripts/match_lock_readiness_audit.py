"""Match-level lock readiness from the manual evidence coverage log.

This local read-only audit turns row-level evidence coverage into a per-match
lock checklist. It does not call SportsPredict, does not read `/predictions`,
and does not submit anything.

Usage:
    python3 scripts/match_lock_readiness_audit.py --write
    python3 scripts/match_lock_readiness_audit.py --match "BRA vs JPN"
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
COVERAGE_LOG = LOG_DIR / "manual_evidence_coverage.md"
OUT = LOG_DIR / "match_lock_readiness_audit.md"

from manual_submission_packet_audit import (  # noqa: E402
    RESEARCH_RESOLUTION_CSV,
    load_research_resolutions,
    research_resolution_for,
    resolution_label,
)

HARD_STATES = {"PLAYER_RESEARCH_REQUIRED", "SPECIAL_COUNT_RESEARCH_REQUIRED"}
RECHECK_STATES = {"CURRENT_ANCHOR_RECHECK", "PLAYER_RECHECK", "SPECIAL_COUNT_RECHECK"}
LOW_CONFIDENCE_STATES = {"FIELD_UNAVAILABLE_PRELOCK", "PROXY_ONLY", "GUARDRAIL_ONLY", "NOT_PROMOTED_GUARD"}
OK_STATES = {"COVERED", "LIMITED_SUPPORT", "MONITOR"}


@dataclass(frozen=True)
class MarketRow:
    market_id: str
    kickoff_local: str
    match: str
    entry: str
    risk: str
    direct_anchor_risk: str
    evidence_status: str
    coverage_state: str
    evidence_area: str
    research_resolution: str
    question: str
    gate: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == f"## {heading}":
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


def load_coverage_rows(path: Path = COVERAGE_LOG, resolution_path: Path = RESEARCH_RESOLUTION_CSV) -> tuple[list[MarketRow], dict[str, str]]:
    if not path.exists():
        return [], {"coverage_log": str(path), "coverage_status": "missing"}
    text = _read(path)
    meta = _key_values(text)
    resolutions = load_research_resolutions(resolution_path)
    rows: list[MarketRow] = []
    for row in _table_after(text, "Markets"):
        resolution = research_resolution_for(row, resolutions)
        coverage_state = row.get("coverage_state", "")
        if resolution and coverage_state in HARD_STATES:
            coverage_state = "RESEARCHED_UNRESOLVED_RECHECK"
        rows.append(
            MarketRow(
                market_id=row.get("market_id", ""),
                kickoff_local=row.get("kickoff_local", ""),
                match=row.get("match", ""),
                entry=row.get("entry", ""),
                risk=row.get("risk", ""),
                direct_anchor_risk=row.get("direct_anchor_risk", ""),
                evidence_status=row.get("evidence_status", ""),
                coverage_state=coverage_state,
                evidence_area=row.get("evidence_area", ""),
                research_resolution=resolution_label(resolution),
                question=row.get("question", ""),
                gate=(
                    f"{row.get('gate', '')}; researched unresolved: {(resolution or {}).get('notes', '').strip()}"
                    if resolution
                    else row.get("gate", "")
                ),
            )
        )
    meta["coverage_log"] = str(path)
    meta["research_resolution_csv"] = str(resolution_path)
    meta["coverage_status"] = "loaded"
    return rows, meta


def _state_class(state: str) -> str:
    if state in HARD_STATES:
        return "hard"
    if state in RECHECK_STATES or state == "RESEARCHED_UNRESOLVED_RECHECK":
        return "recheck"
    if state in LOW_CONFIDENCE_STATES:
        return "low_confidence"
    if state in OK_STATES:
        return "ok"
    return "unknown"


def _match_decision(rows: list[MarketRow]) -> str:
    classes = Counter(_state_class(row.coverage_state) for row in rows)
    if classes["hard"] > 0:
        return "LOCK_BLOCKED_BY_REQUIRED_INPUTS"
    if classes["recheck"] > 0:
        return "LOCK_RECHECK_REQUIRED"
    if classes["low_confidence"] > 0 or classes["unknown"] > 0:
        return "LOCK_LOW_CONFIDENCE_MONITOR"
    return "LOCK_READY"


def build_match_rows(rows: list[MarketRow], *, matches: set[str] | None = None, local_date: str | None = None) -> list[dict]:
    grouped: dict[str, list[MarketRow]] = defaultdict(list)
    for row in rows:
        if matches and row.match not in matches:
            continue
        if local_date and not row.kickoff_local.startswith(local_date):
            continue
        grouped[row.match].append(row)

    out = []
    for match, part in grouped.items():
        states = Counter(row.coverage_state for row in part)
        risks = Counter(row.risk for row in part)
        classes = Counter(_state_class(row.coverage_state) for row in part)
        direct_high = sum(1 for row in part if row.direct_anchor_risk == "HIGH")
        out.append(
            {
                "match": match,
                "kickoff_local": part[0].kickoff_local,
                "markets": len(part),
                "decision": _match_decision(part),
                "hard": classes["hard"],
                "recheck": classes["recheck"],
                "low_confidence": classes["low_confidence"] + classes["unknown"],
                "covered": classes["ok"],
                "high_risk": risks["HIGH"],
                "medium_risk": risks["MEDIUM"],
                "direct_high": direct_high,
                "states": states,
                "rows": sorted(part, key=lambda row: (_state_class(row.coverage_state), row.risk, row.question)),
            }
        )
    return sorted(out, key=lambda row: (row["kickoff_local"], row["match"]))


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(match_rows: list[dict], meta: dict[str, str], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    decision_counts = Counter(row["decision"] for row in match_rows)
    total_hard = sum(int(row["hard"]) for row in match_rows)
    total_recheck = sum(int(row["recheck"]) for row in match_rows)
    total_low = sum(int(row["low_confidence"]) for row in match_rows)
    overall = (
        "LOCK_BLOCKED_BY_REQUIRED_INPUTS"
        if total_hard
        else "LOCK_RECHECK_REQUIRED"
        if total_recheck
        else "LOCK_LOW_CONFIDENCE_MONITOR"
        if total_low
        else "LOCK_READY"
    )

    lines = [
        "# Match Lock Readiness Audit",
        "",
        "Read-only local coverage audit. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- coverage_log: {meta.get('coverage_log', '?')}",
        f"- research_resolution_csv: {meta.get('research_resolution_csv', '?')}",
        f"- coverage_status: {meta.get('coverage_status', '?')}",
        f"- source_local_date: {meta.get('local_date', '?')}",
        f"- decision: {overall}",
        f"- matches: {len(match_rows)}",
        f"- hard_required_rows: {total_hard}",
        f"- recheck_rows: {total_recheck}",
        f"- low_confidence_rows: {total_low}",
        "",
        "## Decision Summary",
        "",
        "| decision | matches |",
        "| --- | ---: |",
    ]
    for decision, count in sorted(decision_counts.items()):
        lines.append(f"| {decision} | {count} |")

    lines.extend(["", "## Match Summary", ""])
    lines += _table(
        [
            "kickoff_local",
            "match",
            "decision",
            "markets",
            "hard",
            "recheck",
            "low_conf",
            "covered",
            "HIGH",
            "MEDIUM",
            "direct_HIGH",
        ],
        [
            [
                row["kickoff_local"],
                row["match"],
                row["decision"],
                str(row["markets"]),
                str(row["hard"]),
                str(row["recheck"]),
                str(row["low_confidence"]),
                str(row["covered"]),
                str(row["high_risk"]),
                str(row["medium_risk"]),
                str(row["direct_high"]),
            ]
            for row in match_rows
        ],
    )

    lines.extend(["", "## Blocking And Recheck Rows", ""])
    for match in match_rows:
        lines.extend([f"### {match['match']}", "", f"- kickoff_local: {match['kickoff_local']}", f"- decision: {match['decision']}"])
        important = [
            row
            for row in match["rows"]
            if _state_class(row.coverage_state) in {"hard", "recheck", "low_confidence", "unknown"}
        ]
        if not important:
            lines.append("- no unresolved lock rows")
            lines.append("")
            continue
        for row in important:
            direct = f", direct={row.direct_anchor_risk}" if row.direct_anchor_risk and row.direct_anchor_risk != "-" else ""
            resolution = f", research={row.research_resolution}" if row.research_resolution != "-" else ""
            lines.append(
                f"- [{row.coverage_state}/{row.risk}{direct}{resolution}] {row.entry} {row.question} -- {row.gate}"
            )
        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "- `LOCK_BLOCKED_BY_REQUIRED_INPUTS`: hard player or special-count source work is still required before treating that match as final.",
            "- `LOCK_RECHECK_REQUIRED`: no hard blockers, but exact-anchor/player/special rechecks remain before lock.",
            "- `LOCK_LOW_CONFIDENCE_MONITOR`: only unavailable-field, proxy, or guardrail rows remain; do not invent a transform.",
            "- `LOCK_READY`: no unresolved coverage gates remain in the current evidence log.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-log", type=Path, default=COVERAGE_LOG)
    parser.add_argument("--research-resolution-csv", type=Path, default=RESEARCH_RESOLUTION_CSV)
    parser.add_argument("--match", action="append", help="Restrict to an exact match name. Repeatable.")
    parser.add_argument("--local-date", help="Restrict to kickoff_local date YYYY-MM-DD.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    rows, meta = load_coverage_rows(args.coverage_log, args.research_resolution_csv)
    match_rows = build_match_rows(rows, matches=set(args.match or []), local_date=args.local_date)
    text = render(match_rows, meta)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
