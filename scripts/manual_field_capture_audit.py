"""Validate captured SportsPredict field averages for diagnostics.

This is a local execution-control audit. It validates that the fillable field
CSV produced by `live_market_risk_audit.py --field-template` has usable values.
The current SportsPredict UI does not expose field averages before prediction
lock, so this audit is diagnostic unless the UI behavior changes.

It does not call SportsPredict, does not read `/predictions`, and never
submits anything.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "logs" / "manual_field_capture_audit.md"
DEFAULT_TEMPLATE = ROOT / "data" / "field_averages_template.csv"

sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import _parse_field_probability  # noqa: E402


VALUE_COLUMNS = ("field", "field_probability", "crowd")


@dataclass(frozen=True)
class TemplateRow:
    market_id: str
    match: str
    kickoff_utc: str
    risk_tier: str
    manual_policy: str
    question: str


@dataclass(frozen=True)
class CaptureIssue:
    severity: str
    market_id: str
    issue: str
    detail: str


@dataclass(frozen=True)
class FieldCaptureResult:
    status: str
    required_rows: int
    supplied_rows: int
    matched_rows: int
    missing_rows: int
    blank_rows: int
    invalid_rows: int
    duplicate_rows: int
    extra_rows: int
    issues: tuple[CaptureIssue, ...]


def _row_value(row: dict[str, str]) -> str:
    for column in VALUE_COLUMNS:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return ""


def read_template(path: Path) -> list[TemplateRow]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        if "market_id" not in headers:
            raise ValueError("field template must include `market_id`")
        rows = []
        for line_no, row in enumerate(reader, start=2):
            market_id = (row.get("market_id") or "").strip()
            if not market_id:
                raise ValueError(f"field template line {line_no} is missing market_id")
            rows.append(
                TemplateRow(
                    market_id=market_id,
                    match=(row.get("match") or "").strip(),
                    kickoff_utc=(row.get("kickoff_utc") or "").strip(),
                    risk_tier=(row.get("risk_tier") or "").strip(),
                    manual_policy=(row.get("manual_policy") or "").strip(),
                    question=(row.get("question") or "").strip(),
                )
            )
    return rows


def audit_field_capture(template_path: Path, field_csv_path: Path) -> FieldCaptureResult:
    try:
        template_rows = read_template(template_path)
    except FileNotFoundError:
        return FieldCaptureResult(
            status="MISSING_TEMPLATE",
            required_rows=0,
            supplied_rows=0,
            matched_rows=0,
            missing_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_rows=0,
            extra_rows=0,
            issues=(CaptureIssue("FAIL", "", "missing_template", str(template_path)),),
        )
    except ValueError as exc:
        return FieldCaptureResult(
            status="INVALID_TEMPLATE",
            required_rows=0,
            supplied_rows=0,
            matched_rows=0,
            missing_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_rows=0,
            extra_rows=0,
            issues=(CaptureIssue("FAIL", "", "invalid_template", str(exc)),),
        )

    required_by_id = {row.market_id: row for row in template_rows}
    if len(required_by_id) != len(template_rows):
        seen: set[str] = set()
        duplicates = []
        for row in template_rows:
            if row.market_id in seen:
                duplicates.append(row.market_id)
            seen.add(row.market_id)
        return FieldCaptureResult(
            status="INVALID_TEMPLATE",
            required_rows=len(template_rows),
            supplied_rows=0,
            matched_rows=0,
            missing_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_rows=len(duplicates),
            extra_rows=0,
            issues=tuple(
                CaptureIssue("FAIL", market_id, "duplicate_template_market_id", "template market ids must be unique")
                for market_id in duplicates
            ),
        )

    if not template_rows:
        return FieldCaptureResult(
            status="NO_ACTIONABLE_FIELDS",
            required_rows=0,
            supplied_rows=0,
            matched_rows=0,
            missing_rows=0,
            blank_rows=0,
            invalid_rows=0,
            duplicate_rows=0,
            extra_rows=0,
            issues=(),
        )

    if not field_csv_path.exists():
        return FieldCaptureResult(
            status="MISSING_CAPTURE_FILE",
            required_rows=len(template_rows),
            supplied_rows=0,
            matched_rows=0,
            missing_rows=len(template_rows),
            blank_rows=0,
            invalid_rows=0,
            duplicate_rows=0,
            extra_rows=0,
            issues=tuple(
                CaptureIssue("FAIL", row.market_id, "missing_field_value", _template_detail(row))
                for row in template_rows
            ),
        )

    issues: list[CaptureIssue] = []
    seen_ids: set[str] = set()
    valid_ids: set[str] = set()
    supplied_rows = 0
    blank_rows = 0
    invalid_rows = 0
    duplicate_rows = 0
    extra_rows = 0

    with field_csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        if not ({"market_id", "id"} & headers):
            return FieldCaptureResult(
                status="INVALID_CAPTURE_FILE",
                required_rows=len(template_rows),
                supplied_rows=0,
                matched_rows=0,
                missing_rows=len(template_rows),
                blank_rows=0,
                invalid_rows=0,
                duplicate_rows=0,
                extra_rows=0,
                issues=(CaptureIssue("FAIL", "", "invalid_capture_file", "field CSV must include `market_id` or `id`"),),
            )
        if not (set(VALUE_COLUMNS) & headers):
            return FieldCaptureResult(
                status="INVALID_CAPTURE_FILE",
                required_rows=len(template_rows),
                supplied_rows=0,
                matched_rows=0,
                missing_rows=len(template_rows),
                blank_rows=0,
                invalid_rows=0,
                duplicate_rows=0,
                extra_rows=0,
                issues=(
                    CaptureIssue(
                        "FAIL",
                        "",
                        "invalid_capture_file",
                        "field CSV must include `field`, `field_probability`, or `crowd`",
                    ),
                ),
            )

        for line_no, row in enumerate(reader, start=2):
            market_id = (row.get("market_id") or row.get("id") or "").strip()
            value = _row_value(row)
            if not market_id and not value:
                continue
            supplied_rows += 1
            if not market_id:
                invalid_rows += 1
                issues.append(CaptureIssue("FAIL", "", "missing_market_id", f"line {line_no} has a field value but no id"))
                continue
            if market_id in seen_ids:
                duplicate_rows += 1
                issues.append(CaptureIssue("FAIL", market_id, "duplicate_capture_market_id", f"line {line_no} repeats market_id"))
                continue
            seen_ids.add(market_id)
            template_row = required_by_id.get(market_id)
            if template_row is None:
                extra_rows += 1
                issues.append(CaptureIssue("WARN", market_id, "extra_market_id", "field CSV row is not in the current actionable template"))
                continue
            if not value:
                blank_rows += 1
                issues.append(CaptureIssue("FAIL", market_id, "blank_field_value", _template_detail(template_row)))
                continue
            try:
                _parse_field_probability(value)
            except ValueError as exc:
                invalid_rows += 1
                issues.append(CaptureIssue("FAIL", market_id, "invalid_field_value", f"{value!r}: {exc}"))
                continue
            valid_ids.add(market_id)

    missing_ids = set(required_by_id) - seen_ids
    for market_id in sorted(missing_ids):
        issues.append(CaptureIssue("FAIL", market_id, "missing_field_value", _template_detail(required_by_id[market_id])))

    matched_rows = len(valid_ids)
    unresolved = len(missing_ids) + blank_rows + invalid_rows + duplicate_rows
    if unresolved:
        status = "INCOMPLETE"
    elif extra_rows:
        status = "COMPLETE_WITH_EXTRA_ROWS"
    else:
        status = "COMPLETE"

    return FieldCaptureResult(
        status=status,
        required_rows=len(template_rows),
        supplied_rows=supplied_rows,
        matched_rows=matched_rows,
        missing_rows=len(missing_ids),
        blank_rows=blank_rows,
        invalid_rows=invalid_rows,
        duplicate_rows=duplicate_rows,
        extra_rows=extra_rows,
        issues=tuple(issues),
    )


def _template_detail(row: TemplateRow) -> str:
    bits = [row.match, row.kickoff_utc, row.risk_tier, row.manual_policy, row.question]
    return " | ".join(bit for bit in bits if bit)


def render(result: FieldCaptureResult, *, template_path: Path, field_csv_path: Path, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Manual Field Capture Audit",
        "",
        "Local validation only. SportsPredict was not called, `/predictions` was not read, and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- template: {template_path}",
        f"- field_csv: {field_csv_path}",
        f"- status: {result.status}",
        f"- required_rows: {result.required_rows}",
        f"- supplied_rows: {result.supplied_rows}",
        f"- matched_rows: {result.matched_rows}",
        f"- missing_rows: {result.missing_rows}",
        f"- blank_rows: {result.blank_rows}",
        f"- invalid_rows: {result.invalid_rows}",
        f"- duplicate_rows: {result.duplicate_rows}",
        f"- extra_rows: {result.extra_rows}",
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
        lines.append("| PASS | - | complete | every actionable field row has a valid field value |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `COMPLETE` means the field CSV is internally valid for historical or post-lock diagnostics.",
            "- `INCOMPLETE` is expected pre-lock because field averages are not available before prediction lock; it is not a manual-entry blocker.",
            "- `COMPLETE_WITH_EXTRA_ROWS` is usable, but the extra rows should be checked because they are not in the current actionable template.",
            "- Do not wait for or fabricate field values before prediction lock.",
            "",
        ]
    )
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="Field template from live_market_risk_audit.py.")
    ap.add_argument(
        "--field-csv",
        help="Captured field CSV to validate. Defaults to --template, matching the current fill-in-place workflow.",
    )
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    template_path = Path(args.template)
    field_csv_path = Path(args.field_csv) if args.field_csv else template_path
    result = audit_field_capture(template_path, field_csv_path)
    text = render(result, template_path=template_path, field_csv_path=field_csv_path)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
