import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from odds_total_source_audit import (  # noqa: E402
    build_rows,
    read_coverage_total_rows,
    read_direct_total_rows,
    render,
)


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_reads_total_goal_rows_from_manual_coverage(tmp_path):
    coverage = tmp_path / "manual_evidence_coverage.md"
    _write(
        coverage,
        """
        # Manual Evidence Coverage Audit

        ## Markets

        | kickoff_local | match | entry | risk | direct_anchor_risk | evidence_status | coverage_state | evidence_area | question | gate |
        | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
        | 2026-06-29 15:30 CDT | GER vs PAR | 56% | LOW | MEDIUM | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | recheck |
        | 2026-06-29 15:30 CDT | GER vs PAR | 72% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will Germany win in regulation (90 minutes + stoppage time)? | covered |
        | 2026-06-29 20:00 CDT | NED vs MAR | 26% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will the first half produce 2 or more goals? | covered |
        """,
    )

    rows = read_coverage_total_rows(coverage)

    assert [row.question for row in rows] == [
        "Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)?",
        "Will the first half produce 2 or more goals?",
    ]
    assert rows[0].entry == 56
    assert rows[1].spec.period == "1h"


def test_build_rows_classifies_direct_thin_large_shift_and_missing(tmp_path):
    coverage = tmp_path / "manual_evidence_coverage.md"
    direct = tmp_path / "polymarket_direct_coverage_audit.md"
    _write(
        coverage,
        """
        # Manual Evidence Coverage Audit

        ## Markets

        | kickoff_local | match | entry | risk | direct_anchor_risk | evidence_status | coverage_state | evidence_area | question | gate |
        | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
        | 2026-06-29 12:00 CDT | BRA vs JPN | 44% | LOW | MEDIUM | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | recheck |
        | 2026-06-29 15:30 CDT | GER vs PAR | 56% | LOW | MEDIUM | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | recheck |
        | 2026-07-03 17:00 CDT | ARG vs CPV | 48% | LOW | MEDIUM | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will Argentina score 3 or more goals in regulation (90 minutes + stoppage time)? | recheck |
        | 2026-06-29 20:00 CDT | NED vs MAR | 26% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will the first half produce 2 or more goals? | covered |
        """,
    )
    _write(
        direct,
        """
        # Polymarket Direct Coverage Audit

        ## Matched Markets

        | status | match | kickoff_utc | model | anchor | delta_pp | volume | question | polymarket_question |
        | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
        | promoted_non_player | BRA vs JPN | 2026-06-29T17:00+00:00 | 45% | 44% | -1 | $769,726 | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | Brazil vs. Japan: O/U 2.5 |
        | promoted_non_player | GER vs PAR | 2026-06-29T20:30+00:00 | 50% | 56% | +6 | $44,536 | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | Germany vs. Paraguay: O/U 2.5 |
        | promoted_non_player | ARG vs CPV | 2026-07-03T22:00+00:00 | 48% | 48% | +0 | $3,141 | Will Argentina score 3 or more goals in regulation (90 minutes + stoppage time)? | Argentina vs. Cabo Verde: Argentina O/U 2.5 |
        """,
    )

    coverage_rows = read_coverage_total_rows(coverage)
    direct_rows = read_direct_total_rows(direct)
    rows = build_rows(
        coverage_rows,
        direct_rows,
        {
            "BRA vs JPN": {"total_goals": "2.49", "state": "ALIGNED"},
            "GER vs PAR": {"total_goals": "2.91", "state": "ALIGNED"},
        },
    )
    by_question = {row.total.question: row for row in rows}

    assert by_question[
        "Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)?"
    ].source_state in {"DIRECT_TOTAL_ALIGNED", "DIRECT_TOTAL_LARGE_SHIFT"}
    states = {row.total.match: row.source_state for row in rows}
    assert states["BRA vs JPN"] == "DIRECT_TOTAL_ALIGNED"
    assert states["GER vs PAR"] == "DIRECT_TOTAL_LARGE_SHIFT"
    assert states["ARG vs CPV"] == "DIRECT_TOTAL_THIN"
    assert states["NED vs MAR"] == "NO_DIRECT_TOTAL_ANCHOR"


def test_render_includes_read_only_guardrails():
    rows = build_rows(
        read_coverage_total_rows(ROOT / "data" / "logs" / "manual_evidence_coverage.md"),
        read_direct_total_rows(ROOT / "data" / "logs" / "polymarket_direct_coverage_audit.md"),
        {},
    )
    text = render(rows, now=datetime(2026, 6, 29, 2, 30, tzinfo=timezone.utc))

    assert "# Odds Total Source Audit" in text
    assert "bot_predictions" not in text
    assert "`/predictions` was not read" in text
    assert "decision: guardrail_only_no_probability_transform" in text
    assert "NO_DIRECT_TOTAL_ANCHOR" in text
