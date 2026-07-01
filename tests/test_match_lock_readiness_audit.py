import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from match_lock_readiness_audit import build_match_rows, load_coverage_rows, render  # noqa: E402


def _coverage_log(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            # Manual Evidence Coverage Audit

            - run_utc: 2026-06-29T10:00:00+00:00
            - local_date: all-open
            - bot_predictions: not_read
            - sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only

            ## Markets

            | kickoff_local | match | entry | risk | direct_anchor_risk | evidence_status | coverage_state | evidence_area | question | gate |
            | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
            | 2026-06-29 12:00 CDT | BRA vs JPN | 31% | HIGH | - | PROMOTED | FIELD_UNAVAILABLE_PRELOCK | count_field_policy | Will there be 4 or more total cards? | field averages are unavailable before lock |
            | 2026-06-29 12:00 CDT | BRA vs JPN | 28% | HIGH | - | PROMOTED_LIMITED | PLAYER_RESEARCH_REQUIRED | player_no_field_fallback | Will Ayase Ueda have at least 1 shot on target? | clear lineup/role/prop flags |
            | 2026-06-29 15:30 CDT | GER vs PAR | 55% | LOW | MEDIUM | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will Germany be ahead at halftime? | verify exact wording and volume |
            | 2026-06-29 15:30 CDT | GER vs PAR | 46% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will both teams score? | covered |
            | 2026-06-29 20:00 CDT | NED vs MAR | 42% | LOW | - | PROMOTED | COVERED | odds_goal_rows | Will the Netherlands win? | covered |
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_load_coverage_rows_parses_market_table(tmp_path):
    path = tmp_path / "coverage.md"
    _coverage_log(path)

    rows, meta = load_coverage_rows(path)

    assert meta["coverage_status"] == "loaded"
    assert meta["local_date"] == "all-open"
    assert len(rows) == 5
    assert rows[0].match == "BRA vs JPN"
    assert rows[0].coverage_state == "FIELD_UNAVAILABLE_PRELOCK"


def test_build_match_rows_classifies_hard_recheck_and_ready_matches(tmp_path):
    path = tmp_path / "coverage.md"
    _coverage_log(path)
    rows, _meta = load_coverage_rows(path)

    matches = {row["match"]: row for row in build_match_rows(rows)}

    assert matches["BRA vs JPN"]["decision"] == "LOCK_BLOCKED_BY_REQUIRED_INPUTS"
    assert matches["BRA vs JPN"]["hard"] == 1
    assert matches["BRA vs JPN"]["low_confidence"] == 1
    assert matches["GER vs PAR"]["decision"] == "LOCK_RECHECK_REQUIRED"
    assert matches["GER vs PAR"]["recheck"] == 1
    assert matches["NED vs MAR"]["decision"] == "LOCK_READY"
    assert matches["NED vs MAR"]["covered"] == 1


def test_researched_unresolved_hard_rows_become_rechecks(tmp_path):
    path = tmp_path / "coverage.md"
    resolution = tmp_path / "research.csv"
    _coverage_log(path)
    resolution.write_text(
        textwrap.dedent(
            """\
            market_id,match,question,status,updated_at,source,notes
            ,BRA vs JPN,Will Ayase Ueda have at least 1 shot on target?,researched_no_usable_current_source,2026-06-29T21:30:00-05:00,public+polymarket,no liquid prop found
            """
        ),
        encoding="utf-8",
    )

    rows, meta = load_coverage_rows(path, resolution)
    matches = {row["match"]: row for row in build_match_rows(rows)}
    text = render(list(matches.values()), meta, now=datetime(2026, 6, 29, 10, tzinfo=timezone.utc))

    assert matches["BRA vs JPN"]["decision"] == "LOCK_RECHECK_REQUIRED"
    assert matches["BRA vs JPN"]["hard"] == 0
    assert matches["BRA vs JPN"]["recheck"] == 1
    assert "RESEARCHED_UNRESOLVED_RECHECK/HIGH" in text
    assert "no liquid prop found" in text


def test_build_match_rows_can_filter_by_match_and_date(tmp_path):
    path = tmp_path / "coverage.md"
    _coverage_log(path)
    rows, _meta = load_coverage_rows(path)

    only_match = build_match_rows(rows, matches={"GER vs PAR"})
    only_date = build_match_rows(rows, local_date="2026-06-29")

    assert [row["match"] for row in only_match] == ["GER vs PAR"]
    assert len(only_date) == 3


def test_render_includes_decision_and_blocking_rows(tmp_path):
    path = tmp_path / "coverage.md"
    _coverage_log(path)
    rows, meta = load_coverage_rows(path)
    match_rows = build_match_rows(rows)

    text = render(match_rows, meta, now=datetime(2026, 6, 29, 10, tzinfo=timezone.utc))

    assert "# Match Lock Readiness Audit" in text
    assert "bot_predictions: not_read" in text
    assert "decision: LOCK_BLOCKED_BY_REQUIRED_INPUTS" in text
    assert "FIELD_UNAVAILABLE_PRELOCK/HIGH" in text
    assert "LOCK_RECHECK_REQUIRED" in text
