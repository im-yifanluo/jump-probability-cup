import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_calibration_watchlist import (  # noqa: E402
    build_watchlist,
    flagged_buckets,
    latest_pricing_block,
    load_reliability_buckets,
    parse_latest_sheet,
    recommended_action,
    render,
    severity,
    sheet_rows_from_priced_rows,
)


CALIBRATION_MD = """
# Manual Calibration Backtest

## Full-Sample Reliability By Bucket

| policy | bucket | n | mean_p | hit | abs_error | brier |
| --- | --- | --- | --- | --- | --- | --- |
| current | 0.35-0.45 | 12 | 0.400 | 0.500 | 0.100 | 0.2400 |
| combined_manual_policy | 0.20-0.35 | 100 | 0.277 | 0.270 | 0.007 | 0.1954 |
| combined_manual_policy | 0.35-0.45 | 135 | 0.400 | 0.311 | 0.089 | 0.2249 |
| combined_manual_policy | 0.65-0.80 | 37 | 0.704 | 0.757 | 0.053 | 0.1899 |
| combined_manual_policy | 0.80-1.00 | 13 | 0.862 | 0.769 | 0.092 | 0.1864 |
| combined_manual_policy | 0.00-0.20 | 8 | 0.155 | 0.020 | 0.135 | 0.1263 |
"""


PREDICTIONS_MD = """
## 2026-06-28T17:00:00+00:00 full_manual_pricing_v2 local_date=2026-06-28

### OLD vs ROW

- kickoff_local: 2026-06-28 12:00 CDT

| model_probability | manual_entry | policy | flags | question | model_source | reasoning |
| --- | --- | --- | --- | --- | --- | --- |
| 10% | 10% | old_policy | - | Old row? | old_model | stale |

## 2026-06-28T18:00:00+00:00 full_manual_pricing_v2 local_date=2026-06-28

### RSA vs CAN

- kickoff_local: 2026-06-28 14:00 CDT

| model_probability | manual_entry | policy | flags | question | model_source | reasoning |
| --- | --- | --- | --- | --- | --- | --- |
| 42% | 42% | raw_special_model | - | Will a goal be scored before the first hydration break? | goal_before_hydration/goals | special |
| 72% | 72% | raw_odds_goal_model | - | Will Canada win in regulation? | team_win | odds |
| 12% | 12% | player_no_field_confirmed_bench_model | CONFIRMED_BENCH | Will Cyle Larin score? | current_player_prop_anchor [Polymarket direct vol=$3,606 outcome=Yes] | prop |
"""


def test_load_reliability_buckets_keeps_combined_policy_only(tmp_path):
    path = tmp_path / "calibration.md"
    path.write_text(CALIBRATION_MD, encoding="utf-8")

    buckets = load_reliability_buckets(path)
    flagged = flagged_buckets(buckets)

    assert [bucket.bucket for bucket in buckets] == [
        "0.20-0.35",
        "0.35-0.45",
        "0.65-0.80",
        "0.80-1.00",
        "0.00-0.20",
    ]
    assert [bucket.bucket for bucket in flagged] == ["0.35-0.45", "0.65-0.80", "0.80-1.00"]
    assert severity(flagged[0]) == "HIGH"
    assert severity(flagged[1]) == "MEDIUM"


def test_parse_latest_sheet_ignores_older_full_pricing_blocks(tmp_path):
    path = tmp_path / "manual_predictions.md"
    path.write_text(PREDICTIONS_MD, encoding="utf-8")

    run_id, _block = latest_pricing_block(PREDICTIONS_MD)
    rows = parse_latest_sheet(path)

    assert run_id == "2026-06-28T18:00:00+00:00"
    assert len(rows) == 3
    assert {row.question for row in rows} == {
        "Will a goal be scored before the first hydration break?",
        "Will Canada win in regulation?",
        "Will Cyle Larin score?",
    }


def test_build_watchlist_flags_only_weak_buckets_and_preserves_actions(tmp_path):
    calibration = tmp_path / "calibration.md"
    predictions = tmp_path / "manual_predictions.md"
    calibration.write_text(CALIBRATION_MD, encoding="utf-8")
    predictions.write_text(PREDICTIONS_MD, encoding="utf-8")

    buckets = load_reliability_buckets(calibration)
    sheet = parse_latest_sheet(predictions)
    watchlist = build_watchlist(sheet, buckets)

    assert [item["row"].manual_entry for item in watchlist] == [42, 72]
    assert watchlist[0]["severity"] == "HIGH"
    assert "do not push higher" in recommended_action(watchlist[0])
    assert "verify source quality" in recommended_action(watchlist[1])


def test_render_is_read_only_and_reports_guardrail_decision(tmp_path):
    calibration = tmp_path / "calibration.md"
    predictions = tmp_path / "manual_predictions.md"
    calibration.write_text(CALIBRATION_MD, encoding="utf-8")
    predictions.write_text(PREDICTIONS_MD, encoding="utf-8")

    text = render(
        build_watchlist(parse_latest_sheet(predictions), load_reliability_buckets(calibration)),
        load_reliability_buckets(calibration),
        now=datetime(2026, 6, 28, 18, tzinfo=timezone.utc),
        include_medium=True,
    )

    assert "# Manual Calibration Watchlist" in text
    assert "bot_predictions: not_read" in text
    assert "sports_predict: not_called" in text
    assert "guardrail_only" in text
    assert "0.35-0.45" in text


def test_sheet_rows_from_priced_rows_supports_live_pricing_source():
    priced = [
        {
            "match_name": "BRA vs JPN",
            "kickoff": datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "probability": 36,
            "manual_entry": 28,
            "manual_policy": "player_no_field_raw_scores_else_global_base",
            "flags": "LINEUP_NOT_POSTED",
            "question": "Will Ayase Ueda have at least 1 shot on target?",
            "model": "player_scores",
        }
    ]

    rows = sheet_rows_from_priced_rows(priced, run_id="live", tz=timezone.utc)

    assert rows[0].run_id == "live"
    assert rows[0].match == "BRA vs JPN"
    assert rows[0].kickoff_local == "2026-06-29 17:00 UTC"
    assert rows[0].manual_entry == 28
    assert rows[0].model_source == "player_scores"


def test_render_can_document_live_all_open_source(tmp_path):
    calibration = tmp_path / "calibration.md"
    calibration.write_text(CALIBRATION_MD, encoding="utf-8")

    text = render(
        [],
        load_reliability_buckets(calibration),
        now=datetime(2026, 6, 28, 18, tzinfo=timezone.utc),
        include_medium=True,
        source_label="live_full_manual_pricing",
        source_context="all-open",
        sports_predict="read_only GET /events, /lobbies, /matches, /markets, /results only",
        selected_markets=120,
    )

    assert "source_predictions: live_full_manual_pricing" in text
    assert "source_context: all-open" in text
    assert "selected_markets: 120" in text
    assert "sports_predict: read_only GET" in text
