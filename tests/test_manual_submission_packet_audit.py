import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_submission_packet_audit import build_packet, render  # noqa: E402


def _write_coverage(path: Path, rows: list[dict[str, str]]) -> None:
    headers = [
        "market_id",
        "kickoff_local",
        "match",
        "entry",
        "risk",
        "direct_anchor_risk",
        "evidence_status",
        "coverage_state",
        "evidence_area",
        "question",
        "gate",
    ]
    lines = [
        "# Manual Evidence Coverage Audit",
        "",
        "## Markets",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row.get(header, "") for header in headers) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(match: str, coverage_state: str, question: str = "Will Brazil win?", **overrides):
    row = {
        "kickoff_local": "2026-06-29 12:00 CDT",
        "match": match,
        "market_id": "",
        "entry": "52%",
        "risk": "LOW",
        "direct_anchor_risk": "-",
        "evidence_status": "PROMOTED",
        "coverage_state": coverage_state,
        "evidence_area": "odds_goal_rows",
        "question": question,
        "gate": "covered",
    }
    row.update(overrides)
    return row


def test_packet_treats_prelock_field_unavailable_as_low_confidence(tmp_path):
    path = tmp_path / "coverage.md"
    _write_coverage(
        path,
        [
            _row("BRA vs JPN", "COVERED"),
            _row(
                "BRA vs JPN",
                "FIELD_UNAVAILABLE_PRELOCK",
                question="Will there be 4 or more total cards?",
                risk="HIGH",
                evidence_area="count_field_policy",
                gate="field averages are unavailable before prediction lock",
            ),
        ],
    )

    audit = build_packet(path)

    assert audit.status == "READY_WITH_LOW_CONFIDENCE_ROWS"
    assert audit.matches[0].status == "READY_WITH_LOW_CONFIDENCE_ROWS"
    assert audit.matches[0].required == 0
    assert audit.matches[0].low_confidence == 1
    assert any(row.submission_state == "LOW_CONFIDENCE_PROVISIONAL" for row in audit.rows)
    assert audit.rows[1].actionability == "NO_FIELD_ACTION_PRELOCK"


def test_packet_recheck_status_when_no_hard_blockers(tmp_path):
    path = tmp_path / "coverage.md"
    _write_coverage(
        path,
        [
            _row("GER vs PAR", "COVERED"),
            _row(
                "GER vs PAR",
                "CURRENT_ANCHOR_RECHECK",
                question="Will Germany be ahead at halftime?",
                evidence_status="CURRENT_SOURCE_RULE",
                evidence_area="exact_polymarket_anchors",
                gate="verify exact wording",
            ),
        ],
    )

    audit = build_packet(path)

    assert audit.status == "NOT_LOCKED_RECHECKS"
    assert audit.matches[0].recheck == 1
    assert audit.rows[1].submission_state == "RECHECK_REQUIRED"
    assert audit.rows[1].actionability == "NEAR_LOCK_ANCHOR_RECHECK"


def test_packet_can_be_ready_with_only_low_confidence_rows(tmp_path):
    path = tmp_path / "coverage.md"
    _write_coverage(
        path,
        [
            _row("NED vs MAR", "COVERED"),
            _row(
                "NED vs MAR",
                "PROXY_ONLY",
                question="Will a goal be scored after the second hydration break?",
                evidence_status="PROXY_ONLY",
                evidence_area="special_markets",
                gate="use raw proxy prior",
            ),
        ],
    )

    audit = build_packet(path)
    text = render(audit, now=datetime(2026, 6, 29, 12, tzinfo=timezone.utc))

    assert audit.status == "READY_WITH_LOW_CONFIDENCE_ROWS"
    assert audit.matches[0].low_confidence == 1
    assert "- submission_packet_status: READY_WITH_LOW_CONFIDENCE_ROWS" in text
    assert "LOW_CONFIDENCE_PROVISIONAL" in text


def test_packet_ready_when_all_rows_covered(tmp_path):
    path = tmp_path / "coverage.md"
    _write_coverage(path, [_row("BRA vs JPN", "COVERED"), _row("BRA vs JPN", "COVERED", question="Will Japan advance?")])

    audit = build_packet(path)

    assert audit.status == "READY_TO_SUBMIT"
    assert audit.matches[0].ready == 2
    assert audit.matches[0].required == 0


def test_packet_actionability_splits_now_research_from_near_lock_and_no_field(tmp_path):
    path = tmp_path / "coverage.md"
    _write_coverage(
        path,
        [
            _row("CIV vs NOR", "COVERED"),
            _row(
                "CIV vs NOR",
                "PLAYER_RESEARCH_REQUIRED",
                question="Will Alexander Sorloth have 2 or more shots on target?",
                risk="HIGH",
                evidence_status="PROMOTED_LIMITED",
                evidence_area="player_no_field_fallback",
                gate="clear lineup/role/prop flags",
            ),
            _row(
                "CIV vs NOR",
                "PLAYER_RECHECK",
                question="Will Erling Haaland score?",
                risk="MEDIUM",
                evidence_status="PROMOTED_LIMITED",
                evidence_area="player_no_field_fallback",
                gate="recheck lineup and current prop market before lock",
            ),
            _row(
                "CIV vs NOR",
                "SPECIAL_COUNT_RESEARCH_REQUIRED",
                question="Will there be 20 or more total shots?",
                risk="HIGH",
                evidence_status="PROXY_ONLY",
                evidence_area="special_markets",
                gate="verify count-volume inputs and current count sources",
            ),
            _row(
                "CIV vs NOR",
                "FIELD_UNAVAILABLE_PRELOCK",
                question="Will there be 4 or more total cards?",
                risk="HIGH",
                evidence_status="PROMOTED",
                evidence_area="count_field_policy",
                gate="field averages are unavailable before prediction lock",
            ),
        ],
    )

    audit = build_packet(path)
    text = render(audit, now=datetime(2026, 6, 29, 12, tzinfo=timezone.utc))
    by_state = {row.coverage_state: row.actionability for row in audit.rows}

    assert audit.status == "NOT_LOCKED_REQUIRED_INPUTS"
    assert by_state["PLAYER_RESEARCH_REQUIRED"] == "NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK"
    assert by_state["PLAYER_RECHECK"] == "NEAR_LOCK_PROP_LINEUP_RECHECK"
    assert by_state["SPECIAL_COUNT_RESEARCH_REQUIRED"] == "NOW_COUNT_SOURCE_RESEARCH"
    assert by_state["FIELD_UNAVAILABLE_PRELOCK"] == "NO_FIELD_ACTION_PRELOCK"
    assert "| NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK | 1 |" in text
    assert "| NOW_COUNT_SOURCE_RESEARCH | 1 |" in text
    assert "| 2026-06-29 12:00 CDT | CIV vs NOR | 2 | 1 | 0 | 1 | 0 |" in text


def test_packet_moves_researched_unresolved_rows_to_near_lock_recheck(tmp_path):
    coverage = tmp_path / "coverage.md"
    resolution = tmp_path / "research_resolutions.csv"
    question = "Will Alexander Sorloth have 2 or more shots on target?"
    _write_coverage(
        coverage,
        [
            _row(
                "CIV vs NOR",
                "PLAYER_RESEARCH_REQUIRED",
                market_id="m1",
                question=question,
                risk="HIGH",
                evidence_status="PROMOTED_LIMITED",
                evidence_area="player_no_field_fallback",
                gate="clear lineup/role/prop flags",
            )
        ],
    )
    resolution.write_text(
        textwrap.dedent(
            """\
            market_id,match,question,status,updated_at,source,notes
            m1,CIV vs NOR,Will Alexander Sorloth have 2 or more shots on target?,researched_no_usable_current_source,2026-06-29T21:30:00-05:00,public+polymarket,exact prop found but did not clear liquidity gate
            """
        ),
        encoding="utf-8",
    )

    audit = build_packet(coverage, resolution)
    text = render(audit, now=datetime(2026, 6, 29, 12, tzinfo=timezone.utc))

    assert audit.status == "NOT_LOCKED_RECHECKS"
    assert audit.matches[0].required == 0
    assert audit.matches[0].recheck == 1
    assert audit.rows[0].submission_state == "RECHECK_REQUIRED"
    assert audit.rows[0].actionability == "NEAR_LOCK_RESEARCHED_SOURCE_RECHECK"
    assert audit.rows[0].research_resolution == "researched_no_usable_current_source via public+polymarket"
    assert "researched_unresolved_rows: 1" in text
    assert "exact prop found but did not clear liquidity gate" in text


def test_missing_coverage_log_is_explicit(tmp_path):
    audit = build_packet(tmp_path / "missing.md")

    assert audit.status == "MISSING_COVERAGE_LOG"
    assert audit.rows == ()
