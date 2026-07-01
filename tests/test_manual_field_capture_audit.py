import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_field_capture_audit import audit_field_capture, render  # noqa: E402


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "market_id",
        "match",
        "kickoff_utc",
        "risk_tier",
        "model_probability",
        "manual_entry",
        "manual_policy",
        "question",
        "field",
        "notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _row(market_id: str, field: str = "") -> dict[str, str]:
    return {
        "market_id": market_id,
        "match": "BRA vs JPN",
        "kickoff_utc": "2026-06-29T17:00+00:00",
        "risk_tier": "HIGH",
        "model_probability": "31%",
        "manual_entry": "31%",
        "manual_policy": "card_count_no_field_model",
        "question": "Will there be 4 or more total cards shown in regulation?",
        "field": field,
        "notes": "",
    }


def test_complete_capture_all_required_rows(tmp_path):
    template = tmp_path / "template.csv"
    field_csv = tmp_path / "fields.csv"
    _write_csv(template, [_row("m1"), _row("m2")])
    _write_csv(field_csv, [_row("m1", "37%"), _row("m2", "0.42")])

    result = audit_field_capture(template, field_csv)

    assert result.status == "COMPLETE"
    assert result.required_rows == 2
    assert result.matched_rows == 2
    assert result.issues == ()


def test_incomplete_capture_reports_blank_missing_invalid_duplicate_and_extra_rows(tmp_path):
    template = tmp_path / "template.csv"
    field_csv = tmp_path / "fields.csv"
    _write_csv(template, [_row("m1"), _row("m2"), _row("m3"), _row("m4")])
    _write_csv(
        field_csv,
        [
            _row("m1", "41"),
            _row("m2", ""),
            _row("m3", "101"),
            _row("m3", "44"),
            _row("extra", "25"),
        ],
    )

    result = audit_field_capture(template, field_csv)
    issue_names = [issue.issue for issue in result.issues]

    assert result.status == "INCOMPLETE"
    assert result.matched_rows == 1
    assert result.blank_rows == 1
    assert result.invalid_rows == 1
    assert result.duplicate_rows == 1
    assert result.extra_rows == 1
    assert result.missing_rows == 1
    assert "blank_field_value" in issue_names
    assert "invalid_field_value" in issue_names
    assert "duplicate_capture_market_id" in issue_names
    assert "extra_market_id" in issue_names
    assert "missing_field_value" in issue_names


def test_missing_capture_file_lists_required_rows(tmp_path):
    template = tmp_path / "template.csv"
    _write_csv(template, [_row("m1"), _row("m2")])

    result = audit_field_capture(template, tmp_path / "missing.csv")

    assert result.status == "MISSING_CAPTURE_FILE"
    assert result.required_rows == 2
    assert result.missing_rows == 2
    assert len(result.issues) == 2


def test_empty_template_is_no_actionable_fields(tmp_path):
    template = tmp_path / "template.csv"
    _write_csv(template, [])

    result = audit_field_capture(template, template)

    assert result.status == "NO_ACTIONABLE_FIELDS"
    assert result.required_rows == 0
    assert result.issues == ()


def test_render_exposes_machine_readable_status(tmp_path):
    template = tmp_path / "template.csv"
    _write_csv(template, [_row("m1", "37")])
    result = audit_field_capture(template, template)

    text = render(
        result,
        template_path=template,
        field_csv_path=template,
        now=datetime(2026, 6, 29, 13, tzinfo=timezone.utc),
    )

    assert "- status: COMPLETE" in text
    assert "- matched_rows: 1" in text
    assert "`/predictions` was not read" in text
