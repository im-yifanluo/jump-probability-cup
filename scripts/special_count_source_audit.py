"""Validate current-source evidence for special count-threshold markets.

Special count-threshold rows such as total shots and early corners are proxy
markets. The StatsBomb replay is useful, but it is not enough to promote a live
probability transform. This audit validates a fillable CSV of independent
current sources before those rows are treated as researched.

It is a local execution-control audit. It does not call SportsPredict, does not
read `/predictions`, and never submits anything.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "data" / "special_count_sources_template.csv"
DEFAULT_DISCOVERY_LOG = ROOT / "data" / "logs" / "special_count_source_discovery.md"
OUT = ROOT / "data" / "logs" / "special_count_source_audit.md"


@dataclass(frozen=True)
class SourceIssue:
    severity: str
    market_id: str
    issue: str
    detail: str


@dataclass(frozen=True)
class SourceAuditResult:
    status: str
    required_rows: int
    complete_two_source_rows: int
    single_source_rows: int
    blank_rows: int
    invalid_rows: int
    duplicate_source_rows: int
    high_required_rows: int
    high_missing_two_source_rows: int
    public_discovery_status: str
    no_usable_public_exact_rows: int
    near_miss_public_rows: int
    issues: tuple[SourceIssue, ...]


def _parse_probability(raw: str) -> float:
    value = raw.strip().rstrip("%").strip()
    if not value:
        raise ValueError("empty probability")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"probability must be positive: {raw!r}")
    if parsed <= 1:
        return parsed
    if parsed <= 100:
        return parsed / 100.0
    raise ValueError(f"probability must be <= 100%: {raw!r}")


def _parse_count_mean(raw: str) -> float:
    value = raw.strip()
    if not value:
        raise ValueError("empty count mean")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"count mean must be positive: {raw!r}")
    return parsed


def _source_slots(row: dict[str, str]) -> list[tuple[str, str, str]]:
    return [
        (
            (row.get("source_1") or "").strip(),
            (row.get("source_1_probability") or "").strip(),
            (row.get("source_1_count_mean") or "").strip(),
        ),
        (
            (row.get("source_2") or "").strip(),
            (row.get("source_2_probability") or "").strip(),
            (row.get("source_2_count_mean") or "").strip(),
        ),
    ]


def _valid_source_count(row: dict[str, str]) -> tuple[int, list[str]]:
    errors: list[str] = []
    valid_names: list[str] = []
    for idx, (source, probability, count_mean) in enumerate(_source_slots(row), start=1):
        if not source and not probability and not count_mean:
            continue
        if not source:
            errors.append(f"source_{idx} has a value but no source name")
            continue
        if not probability and not count_mean:
            errors.append(f"source_{idx} has a source name but no probability or count mean")
            continue
        try:
            if probability:
                _parse_probability(probability)
            if count_mean:
                _parse_count_mean(count_mean)
        except ValueError as exc:
            errors.append(f"source_{idx}: {exc}")
            continue
        valid_names.append(source.strip().lower())
    if len(valid_names) != len(set(valid_names)):
        errors.append("source_1 and source_2 must be independent sources, not the same source name")
    return len(set(valid_names)), errors


def _clean(value: str) -> str:
    return value.strip().strip("`")


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


def discovery_statuses(path: Path = DEFAULT_DISCOVERY_LOG) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return {
        row["market_id"]: row
        for row in _table_after(path.read_text(encoding="utf-8"), "Row Discovery")
        if row.get("market_id")
    }


def _discovery_state(row: dict[str, str] | None) -> tuple[bool, bool, str]:
    if not row:
        return False, False, ""
    status = row.get("status", "")
    no_usable = status != "EXACT_USABLE"
    near_miss = status in {"EXACT_BELOW_VOLUME", "NEAR_PERIOD_MISMATCH", "RELATED_COUNT_MARKET"}
    detail = f"public_market_discovery={status}"
    reason = row.get("reason", "")
    if reason and reason != "-":
        detail += f"; {reason}"
    return no_usable, near_miss, detail


def audit_sources(path: Path = DEFAULT_TEMPLATE, discovery_log: Path = DEFAULT_DISCOVERY_LOG) -> SourceAuditResult:
    if not path.exists():
        return SourceAuditResult(
            status="MISSING_SOURCE_FILE",
            required_rows=0,
            complete_two_source_rows=0,
            single_source_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_source_rows=0,
            high_required_rows=0,
            high_missing_two_source_rows=0,
            public_discovery_status="MISSING" if not discovery_log.exists() else "LOADED",
            no_usable_public_exact_rows=0,
            near_miss_public_rows=0,
            issues=(SourceIssue("FAIL", "", "missing_source_file", str(path)),),
        )

    issues: list[SourceIssue] = []
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        required = {
            "market_id",
            "risk_state",
            "source_1",
            "source_1_probability",
            "source_1_count_mean",
            "source_2",
            "source_2_probability",
            "source_2_count_mean",
        }
        missing = required - headers
        if missing:
            return SourceAuditResult(
                status="INVALID_SOURCE_FILE",
                required_rows=0,
                complete_two_source_rows=0,
                single_source_rows=0,
                blank_rows=0,
                invalid_rows=0,
                duplicate_source_rows=0,
                high_required_rows=0,
                high_missing_two_source_rows=0,
                public_discovery_status="MISSING" if not discovery_log.exists() else "LOADED",
                no_usable_public_exact_rows=0,
                near_miss_public_rows=0,
                issues=(SourceIssue("FAIL", "", "invalid_source_file", "missing columns: " + ", ".join(sorted(missing))),),
            )
        seen: set[str] = set()
        for line_no, row in enumerate(reader, start=2):
            market_id = (row.get("market_id") or "").strip()
            if not market_id:
                issues.append(SourceIssue("FAIL", "", "missing_market_id", f"line {line_no}"))
                continue
            if market_id in seen:
                issues.append(SourceIssue("FAIL", market_id, "duplicate_market_id", f"line {line_no}"))
                continue
            seen.add(market_id)
            rows.append(row)

    if not rows:
        return SourceAuditResult(
            status="NO_SPECIAL_COUNT_ROWS",
            required_rows=0,
            complete_two_source_rows=0,
            single_source_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_source_rows=0,
            high_required_rows=0,
            high_missing_two_source_rows=0,
            public_discovery_status="MISSING" if not discovery_log.exists() else "LOADED",
            no_usable_public_exact_rows=0,
            near_miss_public_rows=0,
            issues=tuple(issues),
        )

    discovery = discovery_statuses(discovery_log)
    public_discovery_status = "LOADED" if discovery else ("MISSING" if not discovery_log.exists() else "NO_ROWS")
    complete_two = single = blank = invalid = duplicate = high = high_missing = 0
    no_usable_public = near_miss_public = 0
    for row in rows:
        market_id = (row.get("market_id") or "").strip()
        risk_state = (row.get("risk_state") or "").strip()
        source_count, errors = _valid_source_count(row)
        no_usable, near_miss, discovery_detail = _discovery_state(discovery.get(market_id))
        if no_usable:
            no_usable_public += 1
        if near_miss:
            near_miss_public += 1
        if source_count == 0 and not errors:
            blank += 1
            detail = _detail(row)
            if discovery_detail:
                detail += f" | {discovery_detail}"
            issues.append(SourceIssue("FAIL", market_id, "blank_current_sources", detail))
        elif errors:
            invalid += 1
            if any("independent sources" in error for error in errors):
                duplicate += 1
            issues.append(SourceIssue("FAIL", market_id, "invalid_current_sources", "; ".join(errors)))
        elif source_count >= 2:
            complete_two += 1
        else:
            single += 1
            detail = _detail(row)
            if discovery_detail:
                detail += f" | {discovery_detail}"
            issue_name = "single_current_source"
            if no_usable:
                issue_name = "single_current_source_no_usable_public_exact"
            issues.append(SourceIssue("WARN", market_id, issue_name, detail))

        if risk_state == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE":
            high += 1
            if source_count < 2 or errors:
                high_missing += 1

    if high_missing or blank or invalid:
        status = "INCOMPLETE"
    elif complete_two == len(rows):
        status = "COMPLETE"
    else:
        status = "PARTIAL_SINGLE_SOURCE_RECHECKS"

    return SourceAuditResult(
        status=status,
        required_rows=len(rows),
        complete_two_source_rows=complete_two,
        single_source_rows=single,
        blank_rows=blank,
        invalid_rows=invalid,
        duplicate_source_rows=duplicate,
        high_required_rows=high,
        high_missing_two_source_rows=high_missing,
        public_discovery_status=public_discovery_status,
        no_usable_public_exact_rows=no_usable_public,
        near_miss_public_rows=near_miss_public,
        issues=tuple(issues),
    )


def _detail(row: dict[str, str]) -> str:
    bits = [
        row.get("match", ""),
        row.get("kickoff_utc", ""),
        row.get("risk_state", ""),
        row.get("evidence_key", ""),
        row.get("question", ""),
    ]
    return " | ".join(bit for bit in bits if bit)


def render(result: SourceAuditResult, *, path: Path = DEFAULT_TEMPLATE, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Special Count Source Audit",
        "",
        "Local validation only. SportsPredict was not called, `/predictions` was not read, and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- source_csv: {path}",
        f"- special_count_source_status: {result.status}",
        f"- required_rows: {result.required_rows}",
        f"- complete_two_source_rows: {result.complete_two_source_rows}",
        f"- single_source_rows: {result.single_source_rows}",
        f"- blank_rows: {result.blank_rows}",
        f"- invalid_rows: {result.invalid_rows}",
        f"- duplicate_source_rows: {result.duplicate_source_rows}",
        f"- high_required_rows: {result.high_required_rows}",
        f"- high_missing_two_source_rows: {result.high_missing_two_source_rows}",
        f"- public_discovery_status: {result.public_discovery_status}",
        f"- no_usable_public_exact_rows: {result.no_usable_public_exact_rows}",
        f"- near_miss_public_rows: {result.near_miss_public_rows}",
        "",
        "## Issues",
        "",
        "| severity | market_id | issue | detail |",
        "| --- | --- | --- | --- |",
    ]
    if result.issues:
        for issue in result.issues:
            lines.append(f"| {issue.severity} | {issue.market_id or '-'} | {issue.issue} | {_escape(issue.detail)} |")
    else:
        lines.append("| PASS | - | complete | every special count-threshold row has two independent current sources |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `COMPLETE` means every special count-threshold row has two independent current sources.",
            "- `PARTIAL_SINGLE_SOURCE_RECHECKS` means high far-from-base rows have two sources, but at least one lower-risk recheck row has only one source.",
            "- `INCOMPLETE` means at least one required row still lacks enough current evidence; do not treat the proxy row as researched.",
            "- `no_usable_public_exact_rows` comes from the public-market discovery audit; goal totals, first-half corners, and other near misses are not valid exact count sources.",
            "",
        ]
    )
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", default=str(DEFAULT_TEMPLATE), help="Filled special count current-source CSV.")
    parser.add_argument("--discovery-log", default=str(DEFAULT_DISCOVERY_LOG), help="Special count public-source discovery markdown log.")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    path = Path(args.source_csv)
    result = audit_sources(path, Path(args.discovery_log))
    text = render(result, path=path)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
