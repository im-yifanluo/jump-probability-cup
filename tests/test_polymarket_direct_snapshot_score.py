import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from polymarket_direct_snapshot_score import (  # noqa: E402
    SnapshotRow,
    filter_semantic_snapshots,
    latest_coverage_by_status,
    latest_per_market,
    latest_snapshot_rows,
    outcomes_by_market_id,
    promotion_gate,
    read_snapshots,
    render,
    score_snapshots,
    summary,
)


def _snapshot(market_id: str, run_minute: int, model: float, anchor: float) -> SnapshotRow:
    return SnapshotRow(
        run_utc=datetime(2026, 6, 28, 18, run_minute, tzinfo=timezone.utc),
        local_date="all-open",
        status="promoted_non_player",
        market_id=market_id,
        match="RSA vs CAN",
        kickoff_utc=datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
        family="btts",
        stat="goals",
        model_probability=model,
        anchor_probability=anchor,
        sports_predict_question="Will both teams score?",
        polymarket_question="South Africa vs. Canada: Both Teams to Score",
        market_volume=1000,
    )


def _first_goal_snapshot(polymarket_question: str) -> SnapshotRow:
    return SnapshotRow(
        run_utc=datetime(2026, 6, 28, 18, 10, tzinfo=timezone.utc),
        local_date="all-open",
        status="promoted_non_player",
        market_id="first-goal",
        match="GER vs PAR",
        kickoff_utc=datetime(2026, 6, 29, 20, 30, tzinfo=timezone.utc),
        family="team_first_goal_game",
        stat="goals",
        model_probability=0.724,
        anchor_probability=0.065,
        sports_predict_question="Will Germany score the first goal of the match?",
        polymarket_question=polymarket_question,
        market_volume=3577,
    )


def test_read_snapshots_parses_replay_csv(tmp_path):
    path = tmp_path / "snapshots.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "run_utc",
                "local_date",
                "status",
                "market_id",
                "match",
                "kickoff_utc",
                "family",
                "stat",
                "model_probability",
                "anchor_probability",
                "delta_pp",
                "market_volume",
                "sports_predict_question",
                "polymarket_event_title",
                "polymarket_question",
                "source",
                "outcome",
                "direct_min_volume",
                "player_min_volume",
                "count_min_volume",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_utc": "2026-06-28T18:52:40+00:00",
                "local_date": "all-open",
                "status": "promoted_non_player",
                "market_id": "m1",
                "match": "RSA vs CAN",
                "kickoff_utc": "2026-06-28T19:00+00:00",
                "family": "btts",
                "stat": "goals",
                "model_probability": "0.468298",
                "anchor_probability": "0.465000",
                "delta_pp": "-1",
                "market_volume": "416348.06",
                "sports_predict_question": "Will both teams score?",
                "polymarket_event_title": "South Africa vs. Canada - More Markets",
                "polymarket_question": "South Africa vs. Canada: Both Teams to Score",
                "source": "Polymarket direct",
                "outcome": "Yes",
                "direct_min_volume": "1000.00",
                "player_min_volume": "1000.00",
                "count_min_volume": "5000.00",
            }
        )

    rows = read_snapshots(path)

    assert len(rows) == 1
    assert rows[0].market_id == "m1"
    assert rows[0].anchor_probability == 0.465
    assert rows[0].kickoff_utc.isoformat() == "2026-06-28T19:00:00+00:00"


def test_outcomes_by_market_id_requires_unique_inferable_outcome():
    results = [
        {"market_id": "yes", "market_status": "settled", "probability_submitted": 60, "brier_score": 0.16},
        {"market_id": "no", "market_status": "settled", "probability_submitted": 60, "brier_score": 0.36},
        {"market_id": "tie", "market_status": "settled", "probability_submitted": 50, "brier_score": 0.25},
        {"market_id": "ambiguous", "market_status": "settled", "probability_submitted": 60, "brier_score": 0.16},
        {"market_id": "ambiguous", "market_status": "settled", "probability_submitted": 60, "brier_score": 0.36},
        {"market_id": "open", "market_status": "open", "probability_submitted": 60, "brier_score": 0.16},
    ]

    outcomes, issues = outcomes_by_market_id(results)

    assert outcomes == {"yes": 1, "no": 0}
    assert issues["tie"] == "uninferable:brier_tie"
    assert issues["ambiguous"] == "ambiguous:multiple_outcomes"
    assert "open" not in outcomes


def test_filter_semantic_snapshots_removes_old_neither_first_goal_anchor():
    valid = _first_goal_snapshot("Germany vs. Paraguay: Germany to score first?")
    invalid = _first_goal_snapshot("Germany vs. Paraguay: Neither team to score first?")

    rows, issues = filter_semantic_snapshots([valid, invalid])

    assert rows == [valid]
    assert issues == {"invalid_first_goal_subject": 1}


def test_latest_per_market_prevents_repeated_snapshot_overcounting():
    snapshots = [
        _snapshot("m1", 10, 0.40, 0.45),
        _snapshot("m1", 20, 0.42, 0.50),
        _snapshot("m2", 15, 0.70, 0.60),
    ]
    scored = score_snapshots(snapshots, {"m1": 1, "m2": 0})
    latest = latest_per_market(scored)

    assert len(scored) == 3
    assert len(latest) == 2
    assert latest[0].snapshot.market_id == "m1"
    assert latest[0].snapshot.anchor_probability == 0.50
    assert summary(latest)["n"] == 2


def test_latest_snapshot_rows_prevents_unscored_duplicate_overcounting():
    snapshots = [
        _snapshot("m1", 10, 0.40, 0.45),
        _snapshot("m1", 20, 0.42, 0.50),
        _snapshot("m2", 15, 0.70, 0.60),
    ]

    latest = latest_snapshot_rows(snapshots)

    assert len(latest) == 2
    assert [row.market_id for row in latest] == ["m1", "m2"]
    assert latest[0].anchor_probability == 0.50


def test_latest_coverage_by_status_reports_scored_and_unscored_latest_markets():
    snapshots = [
        _snapshot("m1", 10, 0.40, 0.45),
        _snapshot("m1", 20, 0.42, 0.50),
        _snapshot("m2", 15, 0.70, 0.60),
        _snapshot("m3", 16, 0.30, 0.35),
    ]
    scored = score_snapshots(snapshots, {"m1": 1, "m3": 0})

    [coverage] = latest_coverage_by_status(snapshots, scored)

    assert coverage["status"] == "promoted_non_player"
    assert coverage["latest_markets"] == 3
    assert coverage["scored"] == 2
    assert coverage["unscored"] == 1
    assert coverage["anchor_wins"] == 1


def test_promotion_gate_requires_sufficient_latest_market_sample():
    scored = score_snapshots([_snapshot("m1", 10, 0.40, 0.45)], {"m1": 1})

    gate = promotion_gate(scored, min_latest_markets=2)

    assert gate["decision"] == "not_promoted_insufficient_sample"
    assert gate["latest_markets"] == 1


def test_render_includes_unscored_rows_and_latest_summary():
    snapshots = [
        _snapshot("settled", 10, 0.40, 0.45),
        _snapshot("missing", 10, 0.40, 0.45),
        _snapshot("missing", 20, 0.43, 0.48),
    ]
    scored = score_snapshots(snapshots, {"settled": 1})

    text = render(
        snapshots,
        scored,
        {"missing": "not_settled_or_missing"},
        results_rows=1,
        now=datetime(2026, 6, 28, 20, tzinfo=timezone.utc),
        raw_snapshot_rows=4,
        semantic_exclusions={"invalid_first_goal_subject": 1},
    )

    assert "# Polymarket Direct Snapshot Score" in text
    assert "latest per market" in text
    assert "unscored_latest_markets: 1" in text
    assert "unscored_snapshot_rows: 2" in text
    assert "raw_snapshot_rows: 4" in text
    assert "semantically_excluded_snapshot_rows: 1" in text
    assert "semantic_exclusion_invalid_first_goal_subject: 1" in text
    assert "historical_promotion_decision: not_promoted_insufficient_sample" in text
    assert "Latest Market Coverage By Anchor Type" in text
    assert "not_settled_or_missing" in text
