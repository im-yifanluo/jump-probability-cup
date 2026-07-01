import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from polymarket_direct_coverage_audit import AuditRow, render, snapshot_records, write_snapshot_csv  # noqa: E402


def test_render_marks_corner_rows_promoted_count_corner():
    text = render(
        [
            AuditRow(
                status="promoted_count_corner",
                market_id="market-corners",
                match_name="NED vs MAR",
                kickoff=datetime(2026, 6, 30, 1, tzinfo=timezone.utc),
                family="total_count_ge",
                stat="corners",
                model_probability=54,
                anchor_probability=47,
                model_probability_raw=0.54,
                anchor_probability_raw=0.47,
                delta_pp=-7,
                volume=1696,
                question="Will there be 10 or more total corner kicks?",
                event_title="Netherlands vs. Morocco",
                market_question="Netherlands vs. Morocco: O/U 9.5 Total Corners",
                source="Polymarket direct",
                outcome="Over",
                note="Polymarket direct vol=$1,696 outcome=Over",
            )
        ],
        local_date="all-open",
        now=datetime(2026, 6, 28, 13, tzinfo=timezone.utc),
        direct_min_volume=1000,
        player_min_volume=1000,
        count_min_volume=5000,
    )

    assert "| promoted_count_corner | 1 |" in text
    assert "separate volume gates" in text
    assert "NED vs MAR" in text


def test_snapshot_records_are_replay_ready_and_writeable(tmp_path):
    row = AuditRow(
        status="promoted_non_player",
        market_id="market-btts",
        match_name="RSA vs CAN",
        kickoff=datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
        family="btts",
        stat="goals",
        model_probability=43,
        anchor_probability=46,
        model_probability_raw=0.4321,
        anchor_probability_raw=0.4567,
        delta_pp=3,
        volume=122365,
        question="Will both teams score in regulation (90 minutes + stoppage time)?",
        event_title="South Africa vs. Canada",
        market_question="South Africa vs. Canada: Both Teams to Score",
        source="Polymarket direct",
        outcome="Yes",
        note="Polymarket direct vol=$122,365 outcome=Yes",
    )

    records = snapshot_records(
        [row],
        run_utc=datetime(2026, 6, 28, 18, 45, tzinfo=timezone.utc),
        local_date="all-open",
        direct_min_volume=1000,
        player_min_volume=1000,
        count_min_volume=5000,
    )
    path = tmp_path / "snapshots.csv"
    write_snapshot_csv(path, records, append=False)
    write_snapshot_csv(path, records, append=True)

    with path.open(newline="", encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))

    assert len(out) == 2
    assert out[0]["run_utc"] == "2026-06-28T18:45:00+00:00"
    assert out[0]["market_id"] == "market-btts"
    assert out[0]["anchor_probability"] == "0.456700"
    assert out[0]["polymarket_question"] == "South Africa vs. Canada: Both Teams to Score"
    assert out[0]["direct_min_volume"] == "1000.00"
