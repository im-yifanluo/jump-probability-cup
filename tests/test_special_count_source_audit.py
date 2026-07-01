import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_count_source_audit import audit_sources, render  # noqa: E402


COLUMNS = [
    "market_id",
    "match",
    "kickoff_utc",
    "risk_state",
    "evidence_key",
    "model_entry",
    "base_hit_rate",
    "model_minus_base_pp",
    "question",
    "source_1",
    "source_1_probability",
    "source_1_count_mean",
    "source_2",
    "source_2_probability",
    "source_2_count_mean",
    "notes",
]


def _row(market_id: str, risk_state: str = "COUNT_THRESHOLD_HIGH_HETEROGENEITY", **overrides):
    row = {
        "market_id": market_id,
        "match": "AUS vs EGY",
        "kickoff_utc": "2026-07-03T18:00+00:00",
        "risk_state": risk_state,
        "evidence_key": "total_shots_ge_20",
        "model_entry": "33%",
        "base_hit_rate": "73.6%",
        "model_minus_base_pp": "-40",
        "question": "Will there be 20 or more total shots?",
        "source_1": "",
        "source_1_probability": "",
        "source_1_count_mean": "",
        "source_2": "",
        "source_2_probability": "",
        "source_2_count_mean": "",
        "notes": "",
    }
    row.update(overrides)
    return row


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})


def _write_discovery(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(
        "\n".join(
            [
                "# Special Count Source Discovery",
                "",
                "## Row Discovery",
                "",
                "| market_id | match | evidence_key | status | usable | probability | volume | event | market | reason |",
                "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
                *[
                    "| {market_id} | AUS vs EGY | total_shots_ge_20 | {status} | false |  |  | event | market | {reason} |".format(
                        **row
                    )
                    for row in rows
                ],
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_complete_requires_two_independent_sources(tmp_path):
    path = tmp_path / "sources.csv"
    _write(
        path,
        [
            _row(
                "m1",
                source_1="DraftKings",
                source_1_probability="37%",
                source_2="Sportsbook B",
                source_2_count_mean="21.5",
            )
        ],
    )

    result = audit_sources(path)

    assert result.status == "COMPLETE"
    assert result.complete_two_source_rows == 1
    assert result.issues == ()


def test_partial_when_lower_risk_row_has_one_source_but_high_row_is_complete(tmp_path):
    path = tmp_path / "sources.csv"
    _write(
        path,
        [
            _row(
                "high",
                "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE",
                source_1="DraftKings",
                source_1_probability="33",
                source_2="Book B",
                source_2_probability="0.31",
            ),
            _row("medium", source_1="Book C", source_1_count_mean="22.0"),
        ],
    )

    result = audit_sources(path)

    assert result.status == "PARTIAL_SINGLE_SOURCE_RECHECKS"
    assert result.high_missing_two_source_rows == 0
    assert result.single_source_rows == 1
    assert any(issue.issue == "single_current_source" for issue in result.issues)


def test_single_source_rows_join_public_discovery_non_sources(tmp_path):
    path = tmp_path / "sources.csv"
    discovery = tmp_path / "discovery.md"
    _write(path, [_row("m1", source_1="ESPN current tournament boxscore shrink", source_1_probability="83%")])
    _write_discovery(
        discovery,
        [
            {
                "market_id": "m1",
                "status": "GOAL_MARKET_ONLY",
                "reason": "same-match market is an ordinary goal total, not a count source",
            }
        ],
    )

    result = audit_sources(path, discovery)

    assert result.status == "PARTIAL_SINGLE_SOURCE_RECHECKS"
    assert result.public_discovery_status == "LOADED"
    assert result.no_usable_public_exact_rows == 1
    assert result.near_miss_public_rows == 0
    assert result.issues[0].issue == "single_current_source_no_usable_public_exact"
    assert "ordinary goal total" in result.issues[0].detail


def test_blank_rows_expose_near_miss_public_source(tmp_path):
    path = tmp_path / "sources.csv"
    discovery = tmp_path / "discovery.md"
    _write(path, [_row("m1", "COUNT_THRESHOLD_PROXY", evidence_key="early_corners_ge_2")])
    _write_discovery(
        discovery,
        [
            {
                "market_id": "m1",
                "status": "NEAR_PERIOD_MISMATCH",
                "reason": "first-half corner total is related but not the first-hydration-break period",
            }
        ],
    )

    result = audit_sources(path, discovery)

    assert result.status == "INCOMPLETE"
    assert result.no_usable_public_exact_rows == 1
    assert result.near_miss_public_rows == 1
    assert result.issues[0].issue == "blank_current_sources"
    assert "first-hydration-break" in result.issues[0].detail


def test_incomplete_flags_blank_invalid_and_duplicate_source_rows(tmp_path):
    path = tmp_path / "sources.csv"
    _write(
        path,
        [
            _row("blank", "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"),
            _row("invalid", source_1="Book A", source_1_probability="101"),
            _row("duplicate", source_1="Book A", source_1_probability="35", source_2="book a", source_2_count_mean="21"),
        ],
    )

    result = audit_sources(path)
    issue_names = [issue.issue for issue in result.issues]

    assert result.status == "INCOMPLETE"
    assert result.blank_rows == 1
    assert result.invalid_rows == 2
    assert result.duplicate_source_rows == 1
    assert result.high_missing_two_source_rows == 1
    assert "blank_current_sources" in issue_names
    assert "invalid_current_sources" in issue_names


def test_render_exposes_machine_readable_status(tmp_path):
    path = tmp_path / "sources.csv"
    _write(path, [_row("m1", source_1="Book A", source_1_probability="37", source_2="Book B", source_2_probability="40")])

    text = render(
        audit_sources(path),
        path=path,
        now=datetime(2026, 6, 29, 14, tzinfo=timezone.utc),
    )

    assert "- special_count_source_status: COMPLETE" in text
    assert "- complete_two_source_rows: 1" in text
    assert "- no_usable_public_exact_rows: 0" in text
    assert "`/predictions` was not read" in text
